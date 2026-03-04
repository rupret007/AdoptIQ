#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Simple - Clean Flask App for 20k-30k Word Reports
Uses EXACT CircuIT AI logic from working script with simple HTML forms
"""

import os
import sys
import json
import logging
import time
import threading
import re
import webbrowser
from threading import Lock, RLock
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Optional as TypingOptional, Dict, Any, List, Union, Tuple
import pandas as pd
import numpy as np

# Fix Windows console encoding for emojis FIRST
if sys.platform == 'win32' and hasattr(sys.stdout, 'buffer'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from flask import Flask, request, jsonify, redirect, url_for, send_file, render_template
from werkzeug.utils import secure_filename
from flask_wtf import FlaskForm
from flask_wtf.csrf import validate_csrf
from wtforms import SelectField, IntegerField, FileField, SubmitField, RadioField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional as OptionalValidator

# --- PyInstaller/frozen bundle support (Mac .app / Windows exe) ---
# When running as a compiled executable, resources are in sys._MEIPASS; writable dirs go to Application Support.
_frozen = getattr(sys, 'frozen', False)
if _frozen:
    _BASE_PATH = Path(sys._MEIPASS)
    if sys.platform == 'darwin':
        _APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / 'AdoptIQ'
    elif sys.platform == 'win32':
        _APP_SUPPORT = Path(os.environ.get('APPDATA', str(Path.home()))) / 'AdoptIQ'
    else:
        _APP_SUPPORT = Path.home() / '.adoptiq'
    _APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    (_APP_SUPPORT / 'uploads').mkdir(exist_ok=True)
    (_APP_SUPPORT / 'outputs').mkdir(exist_ok=True)
    os.chdir(_APP_SUPPORT)  # so relative paths (outputs/, uploads/, analysis_status.json) resolve correctly
    # Snowflake/requests need CA bundle in _MEIPASS (fixes "No cabundle file" error when frozen)
    try:
        import certifi
        cacert = Path(sys._MEIPASS) / 'certifi' / 'cacert.pem'
        if cacert.exists():
            os.environ['SSL_CERT_FILE'] = str(cacert)
            os.environ['REQUESTS_CA_BUNDLE'] = str(cacert)
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(_APP_SUPPORT / '.env')
        load_dotenv(_BASE_PATH / '.env')
    except Exception:
        pass
    # Use embedded configuration compiled into the app (from embed_credentials.py)
    try:
        import _bundled_secrets
        if hasattr(_bundled_secrets, 'get_secrets'):
            os.environ.update(_bundled_secrets.get_secrets())
    except Exception:
        pass
    # Load .env again after bundled secrets so missing credentials (e.g. Snowflake) can come from .env
    try:
        from dotenv import load_dotenv
        load_dotenv(_APP_SUPPORT / '.env')  # override=False: only set vars not already set
    except Exception:
        pass
else:
    _BASE_PATH = Path(__file__).resolve().parent
    _APP_SUPPORT = _BASE_PATH
# ------------------------------------------------------------------

# When frozen, log any uncaught exception to startup_error.txt for diagnosis (e.g. if console=False later)
if _frozen:
    _startup_error_file = Path.home() / 'Library' / 'Application Support' / 'AdoptIQ' / 'startup_error.txt'
    _original_excepthook = sys.excepthook
    def _frozen_excepthook(etype, value, tb):
        try:
            _startup_error_file.parent.mkdir(parents=True, exist_ok=True)
            import traceback
            with open(_startup_error_file, 'w', encoding='utf-8') as f:
                f.write(''.join(traceback.format_exception(etype, value, tb)))
        except Exception:
            pass
        _original_excepthook(etype, value, tb)
    sys.excepthook = _frozen_excepthook

# Import audit system
try:
    from enhanced_admin_dashboard_v2 import (
        audit_report as trigger_audit, init_database, record_report_completion, get_report_history,
        store_report_insights, get_learned_insights,
    )
    AUDIT_ENABLED = True
    init_database()  # Ensure report_history and audit_results exist before any report
    logging.getLogger(__name__).info("Audit system enabled")
except ImportError:
    def _noop(*a, **k): pass
    trigger_audit = _noop
    init_database = _noop
    record_report_completion = _noop
    get_report_history = lambda: []
    store_report_insights = _noop
    get_learned_insights = lambda *a, **k: ""
    AUDIT_ENABLED = False
    logging.getLogger(__name__).info("Audit system not available")
    
def auto_audit_report(analysis_id: str):
    """Automatically trigger audit after report completion"""
    if not AUDIT_ENABLED:
        return
    
    try:
        # Run audit in background thread to not block completion
        def run_audit():
            try:
                result = trigger_audit(analysis_id)
                logger.info(f"Auto-audit completed for {analysis_id}: {result.get('status')} ({result.get('score')}/100)")
            except Exception as e:
                logger.error(f"Auto-audit failed for {analysis_id}: {e}")
        
        audit_thread = threading.Thread(target=run_audit, daemon=True)
        audit_thread.start()
    except Exception as e:
        logger.error(f"Failed to trigger auto-audit for {analysis_id}: {e}")

class AnalysisForm(FlaskForm):
    """Form for analysis configuration"""
    report_type = RadioField('Report Type', 
                             choices=[('comprehensive', 'Comprehensive'), 
                                      ('compact', 'Compact'), 
                                      ('renewal', 'Customer Renewal'),
                                      ('renewal_portfolio', 'Portfolio Renewal')],
                             default='comprehensive',
                             validators=[DataRequired()])
    
    manager = SelectField('Manager', 
                          choices=[],  # Will be set dynamically from team_config.json
                          validators=[DataRequired()])
    
    technology = SelectField('Technology',
                             choices=[('Webex Meetings & Messaging', 'Webex Meetings & Messaging'),
                                      ('Webex Calling', 'Webex Calling'),
                                      ('Webex Contact Center', 'Webex Contact Center'),
                                      ('Webex Contact Center Enterprise', 'Webex Contact Center Enterprise'),
                                      ('Cisco UCCE', 'Cisco UCCE'),
                                      ('Cisco UCCX', 'Cisco UCCX'),
                                      ('All Contact Center', 'All Contact Center'),
                                      ('All', 'All')],
                             validators=[DataRequired()])
    
    customer_name = StringField('Customer Name', validators=[OptionalValidator()])
    
    days = IntegerField('Analysis Period (days)', 
                        default=90,
                        validators=[DataRequired(), NumberRange(min=1, max=365)])
    
    csone_file = FileField('CSOne Excel File (Optional)', validators=[OptionalValidator()])
    
    submit_btn = SubmitField('Generate Report')

# Import the working backend logic
from adoptiq_backend import (
    _ensure_outputs, _connect_with_keeper, get_subscriptions_for_team,
    fetch_adoption_barriers, load_csone_excel, _apply_scope_filter_ab,
    _apply_scope_filter_csone, _apply_scope_filter_csone_inclusive, _prepare_ab, _prepare_csone,
    fetch_help_webex_bugs, fetch_status_incidents, cross_reference_refs,
    _create_briefing_book, _create_executive_briefing_book, _create_minimal_briefing_book, _create_executive_briefing_book_with_csone, generate_llm_response,
    PROMPT_PORTFOLIO_TEMPLATE, PROMPT_CUSTOMER_TEMPLATE, PROMPT_COMPACT_EXECUTIVE_TEMPLATE,
    append_to_word_report, write_excel_workbook,
    TEAM_ROSTER, MANAGERS, TECH_CHOICES, _integrity_checks,
    fetch_csconsole_action_plans, fetch_csconsole_customer_pulse,
    fetch_arr_data, fetch_support_cases_snowflake,
    fetch_csconsole_success_priorities, fetch_csconsole_adoption_barriers,
    _filter_csconsole_data_by_technology
)

# Import new compact and renewal analysis features
from compact_report_formatter import calculate_renewal_risk_scores
from advanced_renewal_analyzer import AdvancedRenewalAnalyzer, generate_advanced_renewal_analysis

# Import data source validator
from data_source_validator import (
    validate_data_sources_for_report,
    raise_validation_error_if_invalid,
    get_data_source_summary,
    DataSourceValidationError
)

# Import Executive Intelligence Report (new executive-ready format)
try:
    from executive_intelligence_formatter import create_executive_intelligence_report
    EXECUTIVE_FORMATTER_AVAILABLE = True
except ImportError:
    EXECUTIVE_FORMATTER_AVAILABLE = False
    create_executive_intelligence_report = None
    logging.getLogger(__name__).info("executive_intelligence_formatter not available - using standard formatter")

# Import subscription search functionality
from adoptiq_backend import fetch_subscription_data, search_subscriptions_by_customer, get_subscription_renewal_risk

# Import leader report functionality
from leader_report_generator import generate_leader_report, LeaderReportGenerator

def validate_manager_input(manager: str) -> tuple[bool, str]:
    """Validate manager input"""
    if not manager or not isinstance(manager, str):
        return False, "Manager is required"
    
    if len(manager) > 100:
        return False, "Manager name too long"
    
    # Check for SQL injection attempts
    dangerous_chars = ["'", '"', ";", "--", "/*", "*/", "xp_", "sp_"]
    if any(char in manager.lower() for char in dangerous_chars):
        return False, "Invalid characters in manager name"
    
    return True, "Valid"

def validate_technology_input(technology: str) -> tuple[bool, str]:
    """Validate technology input"""
    if not technology or not isinstance(technology, str):
        return False, "Technology is required"
    
    if len(technology) > 100:
        return False, "Technology name too long"
    
    # Check for SQL injection attempts
    dangerous_chars = ["'", '"', ";", "--", "/*", "*/", "xp_", "sp_"]
    if any(char in technology.lower() for char in dangerous_chars):
        return False, "Invalid characters in technology name"
    
    return True, "Valid"

def validate_days_input(days: int) -> tuple[bool, str]:
    """Validate days input"""
    if not isinstance(days, int):
        return False, "Days must be a number"
    
    if days < 1 or days > 365:
        return False, "Days must be between 1 and 365"
    
    return True, "Valid"

app = Flask(
    __name__,
    template_folder=str(_BASE_PATH / 'templates'),
    static_folder=str(_BASE_PATH / 'static'),
)
# Use environment key in packaged builds; allow dev fallback only in source mode.
_env_secret_key = os.environ.get('ADOPTIQ_SECRET_KEY')
if _env_secret_key:
    app.config['SECRET_KEY'] = _env_secret_key
elif _frozen:
    raise RuntimeError("ADOPTIQ_SECRET_KEY must be set for packaged builds.")
else:
    app.config['SECRET_KEY'] = 'adoptiq-secret-key-2024-dev-change-in-production'
app.config['UPLOAD_FOLDER'] = str(_APP_SUPPORT / 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# CSRF protection - enabled with extended timeout to prevent timeout issues
app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = None  # No timeout limit for CSRF tokens

# Local-only protection for sensitive routes (desktop app default posture).
_SENSITIVE_ENDPOINTS = {
    'start_analysis', 'start_compact_analysis', 'start_customer_renewal_analysis',
    'start_subscription_analysis', 'start_leader_report', 'cancel_analysis',
    'download_result', 'clear_stuck_analyses', 'simple_test', 'test_generate_report',
}


def _client_ip_from_request(req) -> str:
    """Client IP extraction. Trust proxy headers only when explicitly enabled."""
    trust_proxy_headers = os.environ.get('ADOPTIQ_TRUST_PROXY_HEADERS', '').strip().lower() in {'1', 'true', 'yes'}
    if trust_proxy_headers:
        xff = req.headers.get('X-Forwarded-For', '')
        if xff:
            return xff.split(',')[0].strip()
    return req.environ.get('REMOTE_ADDR', '') or req.remote_addr or ''


def _is_local_client(req) -> bool:
    ip = _client_ip_from_request(req)
    return ip in ('127.0.0.1', '::1', 'localhost')


@app.before_request
def restrict_sensitive_routes_to_localhost():
    endpoint = request.endpoint or ''
    if endpoint in _SENSITIVE_ENDPOINTS and not _is_local_client(request):
        return jsonify({'success': False, 'error': 'Forbidden: local access only'}), 403

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Version and build (from config.py, updated by build_mac_dmg.sh)
from config import ADOPTIQ_VERSION, ADOPTIQ_BUILD, version_string, Config
app.config['CSONE_ONEDRIVE_FOLDER'] = Config.CSONE_ONEDRIVE_FOLDER
app.config['CSONE_SHARED_FOLDER_URL'] = Config.CSONE_SHARED_FOLDER_URL

@app.context_processor
def inject_version():
    return {
        'adoptiq_version': ADOPTIQ_VERSION,
        'adoptiq_build': ADOPTIQ_BUILD,
        'adoptiq_version_string': version_string(),
        'csone_shared_folder_url': app.config.get('CSONE_SHARED_FOLDER_URL', ''),
        'admin_console_url': os.environ.get('ADOPTIQ_ADMIN_URL', 'http://localhost:5001'),
    }

# Import Document and Inches for Word report generation
from docx import Document
from docx.shared import Inches

# Ensure uploads and outputs directories exist (handled above when frozen)
try:
    Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
    (_APP_SUPPORT / 'outputs').mkdir(exist_ok=True)
except Exception as e:
    logger.error(f"Failed to create directories: {e}")

# Global storage for analysis status with persistence
analysis_status = {}
cancellation_flags = {}  # Track cancellation requests
analysis_status_lock = RLock()  # Thread safety for analysis_status (re-entrant for save helpers)
cancellation_flags_lock = Lock()  # Thread safety for cancellation_flags

# File-based status persistence to survive Flask reloads (when frozen, cwd is _APP_SUPPORT so "analysis_status.json" works)
STATUS_FILE = "analysis_status.json"
STATUS_DATETIME_FIELDS = (
    'step_start_time',
    'estimated_completion',
    'completion_time',
    'start_time',
    'end_time',
)
INSIGHT_SUMMARY_MAX_CHARS = 200
BROWSER_LAUNCH_DELAY_SECONDS = 1.5


def _build_insights_payload(
    status_obj: Dict[str, Any],
    default_summary: str,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build normalized insights payload for audit learning store."""
    summary = (status_obj.get('message') or default_summary)[:INSIGHT_SUMMARY_MAX_CHARS]
    payload: Dict[str, Any] = {'summary_line': summary}
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _json_default(obj):
    """Handle non-standard types during JSON serialization of analysis status."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, 'item'):
        return obj.item()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


def save_analysis_status():
    """Save analysis status to file safely from any caller context."""
    try:
        with analysis_status_lock:
            serializable_status = {}
            for analysis_id, status in analysis_status.items():
                serializable_status[analysis_id] = {}
                for key, value in status.items():
                    if isinstance(value, datetime):
                        serializable_status[analysis_id][key] = value.isoformat()
                    else:
                        serializable_status[analysis_id][key] = value
            status_file_path = str(_APP_SUPPORT / STATUS_FILE) if not os.path.isabs(STATUS_FILE) else STATUS_FILE
            temp_file_path = f"{status_file_path}.tmp"
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_status, f, indent=2, default=_json_default)
            os.replace(temp_file_path, status_file_path)
        logger.debug(f"Saved {len(serializable_status)} analysis statuses to {status_file_path}")
    except Exception as e:
        logger.error(f"Error saving analysis status: {e}", exc_info=True)

def load_analysis_status():
    """Load analysis status from file"""
    try:
        status_file_path = str(_APP_SUPPORT / STATUS_FILE) if not os.path.isabs(STATUS_FILE) else STATUS_FILE
        if os.path.exists(status_file_path):
            with open(status_file_path, 'r', encoding='utf-8') as f:
                loaded_status = json.load(f)
                
            with analysis_status_lock:
                for analysis_id, status in loaded_status.items():
                    # Convert datetime strings back to datetime objects
                    for key, value in status.items():
                        if key in STATUS_DATETIME_FIELDS and isinstance(value, str):
                            try:
                                # Handle 'Z' suffix for UTC (Python <3.11 doesn't parse 'Z' natively)
                                status[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                            except (ValueError, TypeError) as e:
                                logger.debug(f"Could not parse datetime for {key}: {e}")  # FIXED: Proper exception handling
                    analysis_status[analysis_id] = status
                    
            logger.info(f"Loaded {len(analysis_status)} analysis statuses from file: {status_file_path}")
        else:
            logger.info(f"Status file not found at: {status_file_path} (this is normal for first run)")
    except Exception as e:
        logger.error(f"Error loading analysis status: {e}", exc_info=True)

# Load existing status on startup
load_analysis_status()

def validate_customer_name_input(customer_name: str) -> tuple[bool, str]:
    """Validate customer name input"""
    if not customer_name:
        return True, "Valid"  # Customer name is optional
    
    if not isinstance(customer_name, str):
        return False, "Customer name must be text"
    
    if len(customer_name) > 200:
        return False, "Customer name too long"
    
    # Check for dangerous characters (SQL/script fragments; no DROP/SELECT so "Dropbox", "Select Inc" allowed)
    dangerous_substrings = ["'", '"', ";", "--", "/*", "*/", "<script>"]
    cn_lower = customer_name.lower()
    if any(frag.lower() in cn_lower for frag in dangerous_substrings):
        return False, "Invalid characters in customer name"
    
    return True, "Valid"

def validate_file_input(filename: str) -> tuple[bool, str]:
    """Validate file input"""
    if not filename:
        return True, "Valid"  # File is optional
    
    if not isinstance(filename, str):
        return False, "Filename must be text"
    
    # Check file extension
    allowed_extensions = ['.xlsx', '.xls']
    if not any(filename.lower().endswith(ext) for ext in allowed_extensions):
        return False, "Only Excel files (.xlsx, .xls) are allowed"
    
    return True, "Valid"

def validate_file_upload(file) -> tuple[bool, str]:
    """Enhanced file validation with security checks"""
    if not file or not file.filename:
        return False, "No file selected"
    
    filename = file.filename
    if not filename.endswith(('.xlsx', '.xls')):
        return False, "Invalid file extension. Only Excel files (.xlsx, .xls) are allowed."
    
    # File size validation
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)     # Reset to beginning
    
    max_size = 50 * 1024 * 1024  # 50MB
    if file_size > max_size:
        return False, f"File too large. Maximum size is {max_size // (1024*1024)}MB."
    
    if file_size == 0:
        return False, "Empty file uploaded. Please select a valid Excel file."

    # Validate basic file signature to reduce spoofed-extension uploads.
    signature = file.read(8)
    file.seek(0)
    if filename.lower().endswith('.xlsx') and not signature.startswith(b'PK\x03\x04'):
        return False, "Invalid XLSX file signature."
    if filename.lower().endswith('.xls') and not signature.startswith(b'\xD0\xCF\x11\xE0'):
        return False, "Invalid XLS file signature."
    
    return True, "File is valid"


def _resolve_csone_path_safe(csone_file_path: str) -> Optional[str]:
    """
    Resolve CSOne file path safely, preventing path traversal.
    Returns validated path under uploads or OneDrive folder, or None if invalid.
    """
    if not csone_file_path or not isinstance(csone_file_path, str):
        return None
    csone_file_path = csone_file_path.strip()
    if not csone_file_path:
        return None
    uploads_dir = os.path.normpath(os.path.abspath(app.config['UPLOAD_FOLDER']))
    onedrive = app.config.get('CSONE_ONEDRIVE_FOLDER', '')
    onedrive_dir = os.path.normpath(os.path.abspath(onedrive)) if onedrive and os.path.isdir(onedrive) else ''

    def _is_under(base_dir: str, candidate_path: str) -> bool:
        """Return True when candidate_path is within base_dir (path-boundary safe)."""
        if not base_dir:
            return False
        try:
            return os.path.commonpath([base_dir, candidate_path]) == base_dir
        except Exception:
            return False
    # If path exists as full path, verify it's under allowed directories
    if os.path.exists(csone_file_path):
        resolved = os.path.normpath(os.path.abspath(csone_file_path))
        if _is_under(uploads_dir, resolved) or _is_under(onedrive_dir, resolved):
            return resolved
        return None
    # Try uploads + sanitized filename
    safe_name = secure_filename(os.path.basename(csone_file_path))
    if not safe_name or not safe_name.lower().endswith(('.xlsx', '.xls')):
        return None
    candidate = os.path.join(uploads_dir, safe_name)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    return None


def get_latest_csone_from_folder() -> Optional[str]:
    """
    When no CSOne file is uploaded, use the most recent .xlsx from the shared folder.
    Macro places reports here daily. Returns full path or None if folder missing/empty.
    """
    folder = app.config.get('CSONE_ONEDRIVE_FOLDER')
    if not folder:
        logger.info(f"[[ONEDRIVE]] CSOne folder not configured (CSONE_ONEDRIVE_FOLDER)")
        return None
    if not os.path.isdir(folder):
        logger.info(f"[[ONEDRIVE]] CSOne shared folder does not exist or is not accessible: {folder}")
        return None
    try:
        xlsx_files = [
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(('.xlsx', '.xls')) and not f.startswith('~')
        ]
        if not xlsx_files:
            logger.info(f"[[ONEDRIVE]] No .xlsx files found in CSOne folder: {folder}")
            return None
        # Sort by modification time, newest first
        latest = max(xlsx_files, key=lambda p: os.path.getmtime(p))
        logger.info(f"[[ONEDRIVE]] Using latest CSOne report from shared folder: {latest}")
        return latest
    except Exception as e:
        logger.warning(f"[[ONEDRIVE]] Could not read CSOne folder {folder}: {e}")
        return None


@app.route('/')
def index():
    """Main analysis page using template"""
    import html as html_module
    
    error_message = request.args.get('error', '')
    # Escape HTML to prevent XSS
    if error_message:
        error_message = html_module.escape(error_message)
    else:
        error_message = ''
    
    form = AnalysisForm()
    # Set manager choices dynamically from team_config.json
    form.manager.choices = [(m, m) for m in MANAGERS]
    return render_template('analyze.html', form=form, error_message=error_message)

@app.route('/start_analysis', methods=['POST'])
def start_analysis():
    """Start a new analysis with enhanced security validation"""
    try:
        # For AJAX requests, validate CSRF token from header/body.
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        
        if is_ajax:
            # Check if JSON data is provided (for renewal reports) or use form data
            json_data = request.get_json(silent=True) or {}
            csrf_token = (
                request.headers.get('X-CSRFToken')
                or request.headers.get('X-CSRF-Token')
                or json_data.get('csrf_token')
                or request.form.get('csrf_token')
            )
            try:
                validate_csrf(csrf_token)
            except Exception:
                return jsonify({'success': False, 'error': 'CSRF validation failed'}), 400
            if json_data:
                # JSON data (typically for renewal reports)
                manager = json_data.get('manager', '').strip() if json_data.get('manager') else ''
                tech = json_data.get('technology', '').strip() if json_data.get('technology') else ''
                renewal_type = json_data.get('renewal_type', '').strip() if json_data.get('renewal_type') else ''
                # If renewal_type is provided but report_type is not, infer report_type from renewal_type
                if renewal_type:
                    if renewal_type == 'renewal_portfolio':
                        report_type = 'renewal_portfolio'
                    elif renewal_type in ['renewal', 'renewal_single']:
                        report_type = 'renewal'
                    else:
                        report_type = json_data.get('report_type', 'comprehensive').strip() if json_data.get('report_type') else 'comprehensive'
                else:
                    report_type = json_data.get('report_type', 'comprehensive').strip() if json_data.get('report_type') else 'comprehensive'
                customer_name = json_data.get('customer_name', '').strip() if json_data.get('customer_name') else ''
                days_str = str(json_data.get('days', 90)).strip()
                subscription_id = json_data.get('subscription_id', '').strip() if json_data.get('subscription_id') else ''
            else:
                # Form data (for other report types)
                manager = request.form.get('manager', '').strip()
                tech = request.form.get('technology', '').strip()
                report_type = request.form.get('report_type', 'comprehensive').strip()
                renewal_type = request.form.get('renewal_type', '').strip()
                customer_name = request.form.get('customer_name', '').strip()
                days_str = request.form.get('days', '90').strip()
                subscription_id = request.form.get('subscription_id', '').strip()
            
            # For renewal reports, use renewal_type if provided, otherwise infer from report_type
            if report_type in ['renewal', 'renewal_portfolio']:
                if not renewal_type:
                    renewal_type = 'renewal_single' if report_type == 'renewal' else 'renewal_portfolio'
            
            # Validate inputs - Manager is optional for single customer renewal (renewal_type can be 'renewal' or 'renewal_single' from form)
            is_single_customer = report_type == 'renewal' and renewal_type in ('renewal_single', 'renewal')
            if is_single_customer:
                # Single customer renewal: manager is optional if customer_name or subscription_id is provided
                if manager and manager not in MANAGERS:
                    return jsonify({
                        'success': False,
                        'error': f'Invalid manager. Must be one of: {", ".join(MANAGERS)}'
                    }), 400
                # For single customer renewal, require either customer_name, subscription_id, OR manager
                if not customer_name and not subscription_id and not manager:
                    return jsonify({
                        'success': False,
                        'error': 'Either Customer Name, Subscription ID, or Manager is required for single customer renewal'
                    }), 400
                if customer_name:
                    is_valid, err_msg = validate_customer_name_input(customer_name)
                    if not is_valid:
                        return jsonify({'success': False, 'error': err_msg}), 400
            elif report_type == 'renewal_portfolio':
                # Portfolio renewal: manager is required
                if not manager or manager not in MANAGERS:
                    return jsonify({
                        'success': False,
                        'error': f'Manager is required for portfolio renewal. Must be one of: {", ".join(MANAGERS)}'
                    }), 400
            else:
                # Comprehensive and compact: manager optional when customer/subscription specified (single-customer report)
                if report_type in ['comprehensive', 'compact'] and (customer_name or subscription_id):
                    if manager and manager not in MANAGERS:
                        return jsonify({'success': False, 'error': f'Invalid manager. Must be one of: {", ".join(MANAGERS)}'}), 400
                else:
                    if not manager or manager not in MANAGERS:
                        return jsonify({
                            'success': False,
                            'error': f'Invalid manager. Must be one of: {", ".join(MANAGERS)}'
                        }), 400
            
            if not tech:
                return jsonify({
                    'success': False,
                    'error': 'Technology is required'
                }), 400
            
            if report_type not in ['comprehensive', 'compact', 'renewal', 'renewal_portfolio']:
                return jsonify({
                    'success': False,
                    'error': 'Invalid report type'
                }), 400
            
            try:
                days = int(days_str)
                if days < 1 or days > 365:
                    return jsonify({
                        'success': False,
                        'error': 'Days must be between 1 and 365'
                    }), 400
            except ValueError:
                return jsonify({
                    'success': False,
                    'error': 'Days must be a valid number'
                }), 400
        else:
            # Regular form submission - use WTForms validation
            form = AnalysisForm()
            # Set manager choices dynamically (they might not be set if form was created before)
            form.manager.choices = [(m, m) for m in MANAGERS]
            if not form.validate():
                # Return validation errors as JSON with detailed info
                error_messages = []
                for field, errors in form.errors.items():
                    for error in errors:
                        error_messages.append(f"{field}: {error}")
                return jsonify({
                    'success': False,
                    'error': ' | '.join(error_messages)
                }), 400
            # Get form data
            manager = form.manager.data
            tech = form.technology.data
            report_type = form.report_type.data
            renewal_type = request.form.get('renewal_type', '').strip()
            customer_name = form.customer_name.data
            days = form.days.data
            subscription_id = request.form.get('subscription_id', '')
            # For renewal reports, use renewal_type if provided, otherwise infer from report_type
            if report_type in ['renewal', 'renewal_portfolio']:
                if not renewal_type:
                    renewal_type = 'renewal_single' if report_type == 'renewal' else 'renewal_portfolio'
        
        # File handling (same for both AJAX and regular)
        csone_file = None
        if 'csone_file' in request.files:
            file = request.files['csone_file']
            if file and file.filename:
                is_valid, error_msg = validate_file_upload(file)
                if not is_valid:
                    return jsonify({
                        'success': False,
                        'error': error_msg
                    }), 400
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                csone_file = filepath
        # When no file uploaded: use most recent from OneDrive folder (macro places reports daily)
        if not csone_file:
            csone_file = get_latest_csone_from_folder()
        
        # Generate unique analysis ID - handle case where manager might be empty for single customer renewal
        timestamp = int(time.time())
        if report_type in ['renewal', 'renewal_portfolio']:
            # For renewal reports, use renewal-specific ID format
            if renewal_type == 'renewal_portfolio':
                # Portfolio renewal: use manager
                if manager:
                    analysis_id = f"Renewal_Portfolio_{manager.replace(' ', '_')}_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
                else:
                    analysis_id = f"Renewal_Portfolio_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
            else:
                # Single customer renewal: use customer name or subscription ID, or manager
                if customer_name:
                    analysis_id = f"Renewal_{customer_name.replace(' ', '_')}_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
                elif subscription_id:
                    analysis_id = f"Renewal_Sub_{subscription_id.replace(' ', '_')}_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
                elif manager:
                    analysis_id = f"Renewal_{manager.replace(' ', '_')}_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
                else:
                    analysis_id = f"Renewal_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
        else:
            # Other report types: use manager
            if manager:
                analysis_id = f"{manager.replace(' ', '_')}_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
            else:
                analysis_id = f"{report_type}_{tech.replace(' ', '_').replace('&', 'and')}_{days}d_{timestamp}"
        
        # Initialize status with enhanced messaging and timing (thread-safe)
        with analysis_status_lock:
            analysis_status[analysis_id] = {
                'status': 'starting',
                'progress': 0,
                'message': ' Initializing AdoptIQ Ultra Intelligence System...',
                'start_time': datetime.now().isoformat(),
                'current_step': 'System Initialization',
                'total_steps': 12,
                'step_start_time': datetime.now().isoformat(),
                'estimated_completion': None,
                'manager': manager,
                'tech': tech,
                'technology': tech,
                'days': days,
                'report_type': report_type,
                'renewal_type': renewal_type if report_type in ['renewal', 'renewal_portfolio'] else None,
                'customer_name': customer_name,
                'subscription_id': subscription_id,
                'csone_file': csone_file,
                'csone_import_status': 'checking',
                'csone_import_message': 'Checking CSOne data availability...',
                'results': None,
                'error': None
            }
            # Save status immediately to persist across Flask reloads
            save_analysis_status()
        
        # Start appropriate analysis based on report type
        if report_type == 'compact':
            thread = threading.Thread(target=run_compact_analysis, args=(analysis_id,))
        elif report_type in ['renewal', 'renewal_portfolio']:
            thread = threading.Thread(target=run_customer_renewal_analysis, args=(analysis_id,))
        else:  # comprehensive
            thread = threading.Thread(target=run_comprehensive_analysis, args=(analysis_id,))
        
        thread.daemon = True
        thread.start()
        
        # Log the analysis ID for debugging
        logger.info(f"[[START]] Started analysis with ID: {analysis_id}")
        logger.info(f"[[DEBUG]] Analysis status keys in memory: {list(analysis_status.keys())}")
        
        # Always return JSON - the frontend handles the redirect
        return jsonify({
            'success': True,
            'analysis_id': analysis_id,
            'redirect_url': url_for('progress', analysis_id=analysis_id)
        })
        
    except Exception as e:
        # For errors, return JSON error response
        logger.error(f"Error in start_analysis: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def check_cancellation(analysis_id):
    """Check if analysis should be cancelled (thread-safe)"""
    with cancellation_flags_lock:
        return cancellation_flags.get(analysis_id, False)

def update_analysis_status(analysis_id: str, updates: Dict[str, Any], save: bool = True):
    """
    Thread-safe helper to update analysis status
    
    Args:
        analysis_id: The analysis ID to update
        updates: Dictionary of status fields to update
        save: Whether to save status to file after update
    """
    with analysis_status_lock:
        if analysis_id in analysis_status:
            analysis_status[analysis_id].update(updates)
            if save:
                save_analysis_status()
        else:
            logger.warning(f"Attempted to update status for non-existent analysis_id: {analysis_id}")

def filter_subscriptions_by_criteria(team_subs_df: pd.DataFrame, customer_name: TypingOptional[str] = None, 
                                     subscription_id: TypingOptional[str] = None) -> tuple[pd.DataFrame, TypingOptional[str]]:
    """
    Filter subscriptions by customer name or subscription ID
    
    Args:
        team_subs_df: DataFrame containing team subscriptions
        customer_name: Optional customer name to filter by (case-insensitive partial match)
        subscription_id: Optional subscription ID to filter by (exact match)
    
    Returns:
        Tuple of (filtered_dataframe, error_message)
        error_message is None if successful, otherwise contains error description
    """
    if team_subs_df.empty:
        return team_subs_df, "No subscriptions available to filter"
    
    original_count = len(team_subs_df)
    filtered_df = team_subs_df.copy()
    
    if customer_name and customer_name.strip():
        # Filter by customer name (case-insensitive partial match)
        customer_name_filter = customer_name.strip()
        filtered_df = filtered_df[filtered_df['BU_NAME'].astype(str).str.contains(customer_name_filter, case=False, na=False)]
        logger.info(f"[[FILTER]] Filtering to customer: {customer_name_filter} - {len(filtered_df)}/{original_count} subscriptions found")
        
        if filtered_df.empty:
            return filtered_df, f"No subscriptions found for customer '{customer_name_filter}'"
            
    elif subscription_id and subscription_id.strip():
        # Filter by subscription ID (exact match)
        subscription_id_filter = subscription_id.strip()
        
        # Validate subscription ID format
        if not re.match(r'^[a-zA-Z0-9\-_]+$', subscription_id_filter):
            return pd.DataFrame(), f"Invalid subscription ID format: '{subscription_id_filter}'"
        
        filtered_df = filtered_df[filtered_df['SUBSCRIPTION_ID'] == subscription_id_filter]
        logger.info(f"[[FILTER]] Filtering to subscription: {subscription_id_filter} - {len(filtered_df)}/{original_count} subscriptions found")
        
        if filtered_df.empty:
            return filtered_df, f"No subscriptions found with ID '{subscription_id_filter}'"
    
    return filtered_df, None

def extract_software_defects(csone_df: pd.DataFrame, ab_df: pd.DataFrame = None) -> Dict[str, Any]:
    """
    Extract software defects (BST/CSC IDs) from CSOne and Adoption Barriers data
    Checks Transaction ID, bemscsc_refs, Title, and Problem Description
    
    Args:
        csone_df: DataFrame containing CSOne/TAC case data
        ab_df: Optional DataFrame containing Adoption Barriers data
        
    Returns:
        Dictionary with defect counts, lists, and customer breakdown
    """
    defects = {
        'csc_ids': set(),
        'bst_defects': [],
        'defect_cases': [],
        'defect_by_customer': {},
        'total_defects': 0,
        'total_cases_with_defects': 0,
        'customers_with_defects': set()
    }
    
    # Extract from CSOne data
    if csone_df is not None and not csone_df.empty:
        for _, row in csone_df.iterrows():
            defect_refs = []
            # Use flexible customer column lookup
            customer = 'Unknown'
            for col in ['Customer Name', 'customer_name', 'BU_NAME', 'Customer']:
                if col in row.index and pd.notna(row.get(col, None)):
                    customer = str(row[col]).strip() or 'Unknown'
                    break
            
            # Check Transaction ID for CSC/BST references
            if 'Transaction ID' in csone_df.columns and pd.notna(row.get('Transaction ID', None)):
                tx_id = str(row['Transaction ID'])
                # Extract CSC IDs (format: CSCxxxxxx)
                csc_matches = re.findall(r'\bCSC[a-zA-Z0-9]{6,10}\b', tx_id, re.IGNORECASE)
                defect_refs.extend(csc_matches)
            
            # Check bemscsc_refs column
            if 'bemscsc_refs' in csone_df.columns and pd.notna(row.get('bemscsc_refs', None)):
                refs_str = str(row['bemscsc_refs'])
                # Extract CSC IDs
                csc_matches = re.findall(r'\bCSC[a-zA-Z0-9]{6,10}\b', refs_str, re.IGNORECASE)
                defect_refs.extend(csc_matches)
            
            # Check Title and Problem Description for defect references
            title = str(row.get('Title', '')) if 'Title' in row.index else ''
            description = str(row.get('Problem Description', '')) if 'Problem Description' in row.index else ''
            combined_text = f"{title} {description}"
            csc_matches = re.findall(r'\bCSC[a-zA-Z0-9]{6,10}\b', combined_text, re.IGNORECASE)
            defect_refs.extend(csc_matches)
            
            # Add unique defects
            for defect_id in defect_refs:
                defect_id_upper = defect_id.upper()
                defects['csc_ids'].add(defect_id_upper)
                defects['customers_with_defects'].add(customer)
                
                case_num = row.get('SR Number', 'N/A') if 'SR Number' in row.index else (
                    row.get('Case Number', 'N/A') if 'Case Number' in row.index else 'N/A'
                )
                tx_id_str = str(row.get('Transaction ID', '')) if 'Transaction ID' in row.index else ''
                
                defects['defect_cases'].append({
                    'defect_id': defect_id_upper,
                    'customer': customer,
                    'case_number': case_num,
                    'title': title if title else 'N/A',  # FIXED: No truncation
                    'source': 'CSOne',
                    'transaction_id': tx_id_str
                })
                
                if customer not in defects['defect_by_customer']:
                    defects['defect_by_customer'][customer] = []
                defects['defect_by_customer'][customer].append(defect_id_upper)
    
    # Extract from Adoption Barriers data
    if ab_df is not None and not ab_df.empty:
        for _, row in ab_df.iterrows():
            defect_refs = []
            # Use flexible customer column lookup
            customer = 'Unknown'
            for col in ['customer_name', 'Customer Name', 'BU_NAME', 'Customer']:
                if col in row.index and pd.notna(row.get(col, None)):
                    customer = str(row[col]).strip() or 'Unknown'
                    break
            
            # Check bemscsc_refs column
            if 'bemscsc_refs' in ab_df.columns and pd.notna(row.get('bemscsc_refs', None)):
                refs_str = str(row['bemscsc_refs'])
                csc_matches = re.findall(r'\bCSC[a-zA-Z0-9]{6,10}\b', refs_str, re.IGNORECASE)
                defect_refs.extend(csc_matches)
            
            # Check title and description
            title = row.get('title', '') if 'title' in row.index else (
                row.get('SUBJECT_C', '') if 'SUBJECT_C' in row.index else ''
            )
            description = row.get('description', '') if 'description' in row.index else (
                row.get('DESCRIPTION__C', '') if 'DESCRIPTION__C' in row.index else ''
            )
            combined_text = f"{title} {description}"
            csc_matches = re.findall(r'\bCSC[a-zA-Z0-9]{6,10}\b', combined_text, re.IGNORECASE)
            defect_refs.extend(csc_matches)
            
            # Add unique defects
            for defect_id in defect_refs:
                defect_id_upper = defect_id.upper()
                defects['csc_ids'].add(defect_id_upper)
                defects['customers_with_defects'].add(customer)
                
                ab_id = row.get('ID', 'N/A') if 'ID' in row.index else 'N/A'
                
                defects['defect_cases'].append({
                    'defect_id': defect_id_upper,
                    'customer': customer,
                    'case_number': f"AB-{ab_id}",
                    'title': str(title) if title else 'N/A',  # FIXED: No truncation
                    'source': 'AdoptionBarrier'
                })
                
                if customer not in defects['defect_by_customer']:
                    defects['defect_by_customer'][customer] = []
                defects['defect_by_customer'][customer].append(defect_id_upper)
    
    # Calculate totals
    defects['total_defects'] = len(defects['csc_ids'])
    defects['bst_defects'] = sorted(list(defects['csc_ids']))
    defects['total_cases_with_defects'] = len(defects['defect_cases'])
    defects['customers_with_defects'] = sorted(list(defects['customers_with_defects']))
    
    logger.info(f"[[DEFECTS]] Found {defects['total_defects']} unique software defects across {defects['total_cases_with_defects']} cases")
    if defects['total_defects'] > 0:
        logger.info(f"[[DEFECTS]] Sample defects: {defects['bst_defects'][:5]}")
    
    return defects

def extract_psirt_vulnerabilities(csone_df: pd.DataFrame, ab_df: pd.DataFrame = None) -> Dict[str, Any]:
    """
    Extract PSIRT vulnerability references (CVE IDs, PSIRT advisory IDs) from data
    
    Args:
        csone_df: DataFrame containing CSOne/TAC case data
        ab_df: Optional DataFrame containing Adoption Barriers data
        
    Returns:
        Dictionary with PSIRT vulnerability counts and lists
    """
    vulnerabilities = {
        'cve_ids': set(),
        'psirt_advisories': set(),
        'vulnerability_cases': [],
        'vulnerability_by_customer': {},
        'total_vulnerabilities': 0,
        'total_cases_with_vulns': 0,
        'customers_with_vulns': set()
    }
    
    # PSIRT patterns
    cve_pattern = r'\bCVE-\d{4}-\d{4,7}\b'
    psirt_pattern = r'\bcisco-sa-\d{8}-[a-z0-9-]+\b'
    
    # Extract from CSOne data
    if csone_df is not None and not csone_df.empty:
        for _, row in csone_df.iterrows():
            vuln_refs = []
            # Use flexible customer column lookup
            customer = 'Unknown'
            for col in ['Customer Name', 'customer_name', 'BU_NAME', 'Customer']:
                if col in row.index and pd.notna(row.get(col, None)):
                    customer = str(row[col]).strip() or 'Unknown'
                    break
            
            # Check all text fields - use proper column checking
            text_fields = []
            if 'Transaction ID' in csone_df.columns:
                text_fields.append(str(row.get('Transaction ID', '')))
            if 'bemscsc_refs' in csone_df.columns:
                text_fields.append(str(row.get('bemscsc_refs', '')))
            if 'Title' in csone_df.columns:
                text_fields.append(str(row.get('Title', '')))
            if 'Problem Description' in csone_df.columns:
                text_fields.append(str(row.get('Problem Description', '')))
            
            combined_text = ' '.join(text_fields)
            
            # Extract CVE IDs
            cve_matches = re.findall(cve_pattern, combined_text, re.IGNORECASE)
            vuln_refs.extend([cve.upper() for cve in cve_matches])
            
            # Extract PSIRT advisory IDs
            psirt_matches = re.findall(psirt_pattern, combined_text, re.IGNORECASE)
            vuln_refs.extend([psirt.lower() for psirt in psirt_matches])
            
            # Add unique vulnerabilities
            for vuln_id in vuln_refs:
                if vuln_id.startswith('CVE-'):
                    vulnerabilities['cve_ids'].add(vuln_id)
                elif 'cisco-sa-' in vuln_id.lower():
                    vulnerabilities['psirt_advisories'].add(vuln_id)
                
                vulnerabilities['customers_with_vulns'].add(customer)
                
                case_num = row.get('SR Number', 'N/A') if 'SR Number' in row.index else (
                    row.get('Case Number', 'N/A') if 'Case Number' in row.index else 'N/A'
                )
                title = str(row.get('Title', '')) if 'Title' in row.index else ''  # FIXED: No truncation
                
                vulnerabilities['vulnerability_cases'].append({
                    'vulnerability_id': vuln_id,
                    'customer': customer,
                    'case_number': case_num,
                    'title': title,
                    'source': 'CSOne'
                })
                
                if customer not in vulnerabilities['vulnerability_by_customer']:
                    vulnerabilities['vulnerability_by_customer'][customer] = []
                vulnerabilities['vulnerability_by_customer'][customer].append(vuln_id)
    
    # Extract from Adoption Barriers data
    if ab_df is not None and not ab_df.empty:
        for _, row in ab_df.iterrows():
            # Use flexible customer column lookup
            customer = 'Unknown'
            for col in ['customer_name', 'Customer Name', 'BU_NAME', 'Customer']:
                if col in row.index and pd.notna(row.get(col, None)):
                    customer = str(row[col]).strip() or 'Unknown'
                    break
            
            # Check all text fields - use proper column checking
            text_fields = []
            if 'bemscsc_refs' in ab_df.columns:
                text_fields.append(str(row.get('bemscsc_refs', '')))
            if 'title' in ab_df.columns:
                text_fields.append(str(row.get('title', '')))
            elif 'SUBJECT_C' in ab_df.columns:
                text_fields.append(str(row.get('SUBJECT_C', '')))
            if 'description' in ab_df.columns:
                text_fields.append(str(row.get('description', '')))
            elif 'DESCRIPTION__C' in ab_df.columns:
                text_fields.append(str(row.get('DESCRIPTION__C', '')))
            
            combined_text = ' '.join(text_fields)
            
            cve_matches = re.findall(cve_pattern, combined_text, re.IGNORECASE)
            psirt_matches = re.findall(psirt_pattern, combined_text, re.IGNORECASE)
            
            for cve in cve_matches:
                vulnerabilities['cve_ids'].add(cve.upper())
                vulnerabilities['customers_with_vulns'].add(customer)
                if customer not in vulnerabilities['vulnerability_by_customer']:
                    vulnerabilities['vulnerability_by_customer'][customer] = []
                vulnerabilities['vulnerability_by_customer'][customer].append(cve.upper())
            
            for psirt in psirt_matches:
                vulnerabilities['psirt_advisories'].add(psirt.lower())
                vulnerabilities['customers_with_vulns'].add(customer)
                if customer not in vulnerabilities['vulnerability_by_customer']:
                    vulnerabilities['vulnerability_by_customer'][customer] = []
                vulnerabilities['vulnerability_by_customer'][customer].append(psirt.lower())
    
    # Calculate totals
    vulnerabilities['total_vulnerabilities'] = len(vulnerabilities['cve_ids']) + len(vulnerabilities['psirt_advisories'])
    vulnerabilities['total_cases_with_vulns'] = len(vulnerabilities['vulnerability_cases'])
    vulnerabilities['customers_with_vulns'] = sorted(list(vulnerabilities['customers_with_vulns']))
    
    logger.info(f"[[PSIRT]] Found {vulnerabilities['total_vulnerabilities']} vulnerabilities ({len(vulnerabilities['cve_ids'])} CVEs, {len(vulnerabilities['psirt_advisories'])} PSIRT advisories)")
    
    return vulnerabilities

def detect_bems_escalations(csone_df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Comprehensive BEMS detection from CSOne data
    Checks Transaction ID (PRIMARY), bemscsc_refs (SECONDARY), and other columns
    
    Args:
        csone_df: DataFrame containing CSOne/TAC case data
        
    Returns:
        Tuple of (bems_cases_dataframe, bems_count)
    """
    if csone_df is None or csone_df.empty:
        return pd.DataFrame(), 0
    
    # Create BEMS filter mask checking multiple columns
    bems_mask = pd.Series([False] * len(csone_df), index=csone_df.index)
    
    # PRIMARY: Transaction ID column (main BEMS source in CSOne Excel files)
    if 'Transaction ID' in csone_df.columns:
        bems_mask |= csone_df['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
        logger.info(f"[[BEMS]] Checking 'Transaction ID' column for BEMS references")
    
    # SECONDARY: bemscsc_refs column (extracted from Title/Problem Description)
    if 'bemscsc_refs' in csone_df.columns:
        bems_mask |= csone_df['bemscsc_refs'].notna() & (csone_df['bemscsc_refs'].astype(str) != '') & (csone_df['bemscsc_refs'].astype(str) != '[]') & csone_df['bemscsc_refs'].astype(str).str.contains('BEMS', case=False, na=False)
        logger.info(f"[[BEMS]] Checking 'bemscsc_refs' column for BEMS references")
    
    # TERTIARY: Check other potential BEMS column names
    for col in ['BEMS_REF', 'bems_ref', 'Escalation_Ref', 'Engineering_Ref', 'BEMS', 'bems']:
        if col in csone_df.columns:
            bems_mask |= csone_df[col].fillna('').astype(str).str.contains('BEMS', case=False, na=False)
            logger.info(f"[[BEMS]] Checking '{col}' column for BEMS references")
    
    # If no exact match, search for columns containing "BEMS" or "bems"
    if not bems_mask.any():
        for col in csone_df.columns:
            if ('bems' in col.lower() or 'escalation' in col.lower() or 'engineering' in col.lower()) and col not in ['Transaction ID', 'bemscsc_refs']:
                logger.info(f"[[BEMS]] Found potential BEMS column: '{col}'")
                bems_mask |= csone_df[col].fillna('').astype(str).str.contains('BEMS', case=False, na=False)
    
    # FOURTH: Search free-text columns (Title, Problem Description, Subject, etc.) for BEMS mentions
    # Many CSOne exports put BEMS IDs or "engineering escalation" only in case text
    if not bems_mask.any():
        text_cols = []
        for col in csone_df.columns:
            c = str(col).lower()
            if any(x in c for x in ('title', 'problem', 'description', 'subject', 'summary', 'body', 'detail')):
                text_cols.append(col)
        if text_cols:
            combined = pd.Series('', index=csone_df.index)
            for col in text_cols:
                combined += ' ' + csone_df[col].fillna('').astype(str)
            # Match: "BEMS", "BEMS-123", "BEMS 456", "backend escalation", "engineering escalation"
            bems_in_text = combined.str.contains(r'\bBEMS\b|BEMS[- ]?\d+|backend\s+escalation|engineering\s+escalation', case=False, na=False, regex=True)
            if bems_in_text.any():
                bems_mask |= bems_in_text
                logger.info(f"[[BEMS]] Found BEMS references in free-text columns: {text_cols}")
    
    # Extract BEMS cases
    bems_cases = csone_df[bems_mask].copy() if bems_mask.any() else pd.DataFrame()
    bems_count = len(bems_cases)
    
    if bems_count > 0:
        logger.info(f"[[BEMS]] Found {bems_count} BEMS escalations")
        # Show sample BEMS values from Transaction ID if available
        if 'Transaction ID' in bems_cases.columns:
            sample_bems = bems_cases['Transaction ID'].dropna().head(3).tolist()
            logger.info(f"[[BEMS]] Sample BEMS Transaction IDs: {sample_bems}")
        elif 'bemscsc_refs' in bems_cases.columns:
            sample_bems = bems_cases['bemscsc_refs'].dropna().head(3).tolist()
            logger.info(f"[[BEMS]] Sample BEMS references: {sample_bems}")
    else:
        logger.warning(f"[[BEMS]] No BEMS escalations found in CSOne data")
        if 'Transaction ID' in csone_df.columns:
            logger.info(f"[[BEMS]] Transaction ID column exists - checking sample values...")
            sample_tx_ids = csone_df['Transaction ID'].dropna().head(5).tolist()
            logger.info(f"[[BEMS]] Sample Transaction IDs: {sample_tx_ids}")
    
    return bems_cases, bems_count

def _clean_datetime_columns_for_excel(df):
    """Remove timezone information from datetime columns for Excel compatibility"""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    
    try:
        df_clean = df.copy()
        for col in df_clean.columns:
            if pd.api.types.is_datetime64_any_dtype(df_clean[col]):
                try:
                    # Strip timezone from tz-aware columns; tz-naive columns need no conversion
                    if hasattr(df_clean[col].dtype, 'tz') and df_clean[col].dtype.tz is not None:
                        df_clean[col] = df_clean[col].dt.tz_convert(None)
                except Exception as col_error:
                    logger.warning(f"Could not clean datetime column {col}: {col_error}")
                    # If we can't clean the column, convert to string
                    df_clean[col] = df_clean[col].astype(str)
        return df_clean
    except Exception as e:
        logger.error(f"Error cleaning datetime columns: {e}")
        # Return original dataframe if cleaning fails
        return df

def _get_all_customers_from_all_sources(ab_norm: pd.DataFrame = None, csone_df: pd.DataFrame = None, 
                                        team_subs_df: pd.DataFrame = None,
                                        csconsole_action_plans: pd.DataFrame = None,
                                        csconsole_customer_pulse: pd.DataFrame = None,
                                        csconsole_success_priorities: pd.DataFrame = None,
                                        csconsole_adoption_barriers: pd.DataFrame = None) -> set:
    """
    Get ALL unique customers from ALL available data sources.
    This ensures consistent customer counts across all report types.
    
    Args:
        ab_norm: Adoption barriers DataFrame
        csone_df: CSOne cases DataFrame
        team_subs_df: Team subscriptions DataFrame
        csconsole_action_plans: CSConsole action plans DataFrame
        csconsole_customer_pulse: CSConsole customer pulse DataFrame
        csconsole_success_priorities: CSConsole success priorities DataFrame
        csconsole_adoption_barriers: CSConsole adoption barriers DataFrame
    
    Returns:
        Set of unique customer names from all sources
    """
    all_customers = set()
    
    # CRITICAL: Start with team subscriptions FIRST (unfiltered, most comprehensive source)
    # This ensures we get ALL customers assigned to the manager's team
    if team_subs_df is not None and not team_subs_df.empty:
        if 'BU_NAME' in team_subs_df.columns:
            all_customers.update(team_subs_df['BU_NAME'].dropna().unique())
            logger.info(f"[[CUSTOMER_COUNT]] Team subscriptions: {len(team_subs_df['BU_NAME'].dropna().unique())} customers")
    
    # From adoption barriers (filtered by technology, but may have additional customers)
    if ab_norm is not None and not ab_norm.empty:
        if 'customer_name' in ab_norm.columns:
            ab_customers = ab_norm['customer_name'].dropna().unique()
            all_customers.update(ab_customers)
            logger.info(f"[[CUSTOMER_COUNT]] Adoption barriers: {len(ab_customers)} customers")
        elif 'BU_NAME' in ab_norm.columns:
            ab_customers = ab_norm['BU_NAME'].dropna().unique()
            all_customers.update(ab_customers)
            logger.info(f"[[CUSTOMER_COUNT]] Adoption barriers (BU_NAME): {len(ab_customers)} customers")
    
    # From CSOne cases - check ALL possible columns (not just first match)
    if csone_df is not None and not csone_df.empty:
        csone_customers = set()
        for col in ['customer_name', 'Customer Name', 'Customer', 'BU_NAME', 'Account Name', 'Account_Name']:
            if col in csone_df.columns:
                csone_customers.update(csone_df[col].dropna().unique())
                # Don't break - check all columns to get customers from all sources
        all_customers.update(csone_customers)
        logger.info(f"[[CUSTOMER_COUNT]] CSOne cases: {len(csone_customers)} customers")
    
    # From CSConsole action plans - check ALL possible columns
    if csconsole_action_plans is not None and not csconsole_action_plans.empty:
        ap_customers = set()
        for col in ['BU_NAME', 'CUSTOMER_NAME', 'ACCOUNT_NAME']:
            if col in csconsole_action_plans.columns:
                ap_customers.update(csconsole_action_plans[col].dropna().unique())
                # Don't break - check all columns
        all_customers.update(ap_customers)
        logger.info(f"[[CUSTOMER_COUNT]] CSConsole action plans: {len(ap_customers)} customers")
    
    # From CSConsole customer pulse - check ALL possible columns
    if csconsole_customer_pulse is not None and not csconsole_customer_pulse.empty:
        pulse_customers = set()
        for col in ['BU_NAME', 'CUSTOMER_NAME', 'ACCOUNT__C']:
            if col in csconsole_customer_pulse.columns:
                pulse_customers.update(csconsole_customer_pulse[col].dropna().unique())
                # Don't break - check all columns
        all_customers.update(pulse_customers)
        logger.info(f"[[CUSTOMER_COUNT]] CSConsole customer pulse: {len(pulse_customers)} customers")
    
    # From CSConsole success priorities - check ALL possible columns
    if csconsole_success_priorities is not None and not csconsole_success_priorities.empty:
        sp_customers = set()
        for col in ['RELATED_CUSTOMER__C', 'BU_NAME', 'CUSTOMER_NAME']:
            if col in csconsole_success_priorities.columns:
                sp_customers.update(csconsole_success_priorities[col].dropna().unique())
                # Don't break - check all columns
        all_customers.update(sp_customers)
        logger.info(f"[[CUSTOMER_COUNT]] CSConsole success priorities: {len(sp_customers)} customers")
    
    # From CSConsole adoption barriers - check ALL possible columns
    if csconsole_adoption_barriers is not None and not csconsole_adoption_barriers.empty:
        ab_csconsole_customers = set()
        for col in ['ACCOUNT_ID_C', 'BU_NAME', 'CUSTOMER_NAME']:
            if col in csconsole_adoption_barriers.columns:
                ab_csconsole_customers.update(csconsole_adoption_barriers[col].dropna().unique())
                # Don't break - check all columns
        all_customers.update(ab_csconsole_customers)
        logger.info(f"[[CUSTOMER_COUNT]] CSConsole adoption barriers: {len(ab_csconsole_customers)} customers")
    
    # Note: External bugs and incidents typically don't contain customer names
    # They are used for correlation and context, not for customer counting
    # Customer counting is done from internal data sources above
    
    logger.info(f"[[CUSTOMER_COUNT]] TOTAL unique customers from all sources: {len(all_customers)}")
    return all_customers


def _has_customer_activity_for_deep_dive(
    cust_ab: pd.DataFrame,
    cust_csone: pd.DataFrame,
    cust_action_plans: pd.DataFrame,
    cust_customer_pulse: pd.DataFrame,
    cust_success_priorities: pd.DataFrame,
    cust_csconsole_adoption_barriers: pd.DataFrame,
) -> bool:
    """Return True when any customer data source has at least one row."""
    for frame in (
        cust_ab,
        cust_csone,
        cust_action_plans,
        cust_customer_pulse,
        cust_success_priorities,
        cust_csconsole_adoption_barriers,
    ):
        if frame is not None and hasattr(frame, "empty") and not frame.empty:
            return True
    return False

def _generate_comprehensive_fallback_insights(ab_norm, csone_df, manager, technology):
    """Generate comprehensive fallback insights when AI is unavailable"""
    insights = []
    ab_norm = ab_norm if ab_norm is not None else pd.DataFrame()
    csone_df = csone_df if csone_df is not None else pd.DataFrame()
    
    # Portfolio overview - Use comprehensive function to get ALL customers from ALL sources
    all_customers_set = _get_all_customers_from_all_sources(
        ab_norm=ab_norm,
        csone_df=csone_df,
        team_subs_df=None,  # Not available in fallback context
        csconsole_action_plans=None,
        csconsole_customer_pulse=None,
        csconsole_success_priorities=None,
        csconsole_adoption_barriers=None
    )
    total_customers = len(all_customers_set)
    ab_count = len(ab_norm)
    case_count = len(csone_df)
    
    insights.append(f"Portfolio Analysis for {manager} - {technology} Technology Focus")
    insights.append(f"This comprehensive analysis covers {total_customers} customers with {ab_count} adoption barriers and {case_count} support cases identified over the analysis period.")
    
    # Risk assessment
    if not ab_norm.empty:
        if 'SEVERITY_C' in ab_norm.columns:
            severity_col = ab_norm['SEVERITY_C'].astype(str)
            critical_abs = len(ab_norm[severity_col.str.contains('Critical', case=False, na=False)])
            high_abs = len(ab_norm[severity_col.str.contains('High', case=False, na=False)])
        else:
            critical_abs = 0
            high_abs = 0
        
        if critical_abs > 0:
            insights.append(f"CRITICAL ATTENTION REQUIRED: {critical_abs} critical adoption barriers identified that pose immediate risk to customer success and renewal likelihood.")
        
        if high_abs > 0:
            insights.append(f"HIGH PRIORITY: {high_abs} high-severity adoption barriers require focused intervention within the next 30 days.")
    
    # Support case analysis
    if not csone_df.empty:
        if 'Severity' in csone_df.columns:
            severity_col = csone_df['Severity'].astype(str)
            p1_cases = len(csone_df[severity_col.str.contains('P1', case=False, na=False)])
        else:
            p1_cases = 0
            
        if 'Status' in csone_df.columns:
            status_col = csone_df['Status'].astype(str)
            escalated_cases = len(csone_df[status_col.str.contains('Escalated', case=False, na=False)])
        else:
            escalated_cases = 0
        
        if p1_cases > 0:
            insights.append(f"URGENT: {p1_cases} P1 support cases require immediate executive attention and resource allocation.")
        
        if escalated_cases > 0:
            insights.append(f"ESCALATION ALERT: {escalated_cases} cases have been escalated, indicating potential customer satisfaction risks.")
    
    # Data-driven: top at-risk customers and BEMS count
    if not ab_norm.empty and 'customer_name' in ab_norm.columns:
        by_cust = ab_norm.groupby('customer_name').size().sort_values(ascending=False)
        top_at_risk = by_cust.head(5).index.tolist()
        if top_at_risk:
            insights.append(f"TOP CUSTOMERS BY ADOPTION BARRIERS: {', '.join(top_at_risk[:5])}.")
    bems_total = 0
    if not csone_df.empty:
        bems_mask = pd.Series([False] * len(csone_df), index=csone_df.index)
        if 'Transaction ID' in csone_df.columns:
            bems_mask |= csone_df['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
        if 'bemscsc_refs' in csone_df.columns:
            refs = csone_df['bemscsc_refs'].fillna('').astype(str)
            bems_mask |= ((refs != '') & (refs != '[]') & refs.str.contains('BEMS', case=False, na=False))
        bems_total = bems_mask.sum()
    if bems_total > 0:
        insights.append(f"BEMS ENGINEERING ESCALATIONS: {int(bems_total)} cases require specialized engineering support—high renewal risk indicator.")
    
    # Strategic recommendations
    insights.append("STRATEGIC RECOMMENDATIONS:")
    insights.append("1. Implement weekly executive reviews for all critical and high-severity issues")
    insights.append("2. Establish dedicated customer success pods for at-risk accounts")
    insights.append("3. Deploy proactive health monitoring and early warning systems")
    insights.append("4. Create customer-specific success plans with measurable outcomes")
    insights.append("5. Enhance technical enablement programs to reduce adoption barriers")
    
    # Business impact
    insights.append("BUSINESS IMPACT: Addressing these issues proactively will improve customer satisfaction scores, reduce churn risk, and increase renewal rates. Executive engagement is critical for successful outcomes.")
    
    return " ".join(insights)

def _parse_markdown_for_fallback(doc, ai_text: str):
    """Parse AI markdown and add clean content to Word document - CRITICAL FIX"""
    import re
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    
    # Remove instruction symbols
    ai_text = ai_text.replace('**YOUR MISSION:**', '').replace('**CRITICAL REQUIREMENTS:**', '')
    ai_text = ai_text.replace('**TECHNOLOGY FOCUS:**', '').replace('**DATA SOURCES:**', '')
    
    lines = ai_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or any(x in line for x in ['YOUR MISSION:', 'CRITICAL REQUIREMENTS:']):
            continue
        
        # Convert headings (remove ##)
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            heading_text = line.lstrip('#').replace('**', '').strip()
            if heading_text:
                doc.add_heading(heading_text, min(level, 3))
        
        # Handle separators
        elif line.startswith('---'):
            sep_para = doc.add_paragraph()
            sep_run = sep_para.add_run('_' * 50)
            sep_run.font.size = Pt(8)
            sep_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Handle bullets
        elif line.startswith(('• ', '* ', '- ')):
            text = line[2:].replace('**', '')
            doc.add_paragraph(text, style='List Bullet')
        
        # Handle numbered lists
        elif re.match(r'^\d+[\.\)]\s', line):
            text = re.sub(r'^\d+[\.\)]\s*', '', line).replace('**', '')
            doc.add_paragraph(text, style='List Number')
        
        # Regular paragraphs - handle bold text
        elif line:
            if '**' in line:
                para = doc.add_paragraph()
                parts = re.split(r'(\*\*[^*]+\*\*)', line)
                for part in parts:
                    if part.startswith('**') and part.endswith('**'):
                        bold_run = para.add_run(part.strip('**'))
                        bold_run.bold = True
                    elif part:
                        para.add_run(part)
            else:
                doc.add_paragraph(line)

def create_executive_charts(ab_norm: pd.DataFrame, arr_data: pd.DataFrame, arr_impact: Dict, csone_df: pd.DataFrame = None, feature_requests: Dict = None) -> List[str]:
    """Create executive-ready charts and save as images"""
    chart_paths = []
    
    # Initialize feature_requests if not provided
    if feature_requests is None:
        feature_requests = {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
    
    try:
        import matplotlib
        matplotlib.use('Agg')  # Use non-interactive backend for server
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
        from matplotlib.patches import Wedge
        import numpy as np
        
        # Set style for executive reports - use fallback if seaborn not available
        try:
            plt.style.use('seaborn-v0_8-whitegrid')
        except Exception:
            try:
                plt.style.use('seaborn-whitegrid')
            except Exception:
                pass  # Use default style
        
        plt.rcParams['figure.facecolor'] = 'white'
        plt.rcParams['axes.facecolor'] = 'white'
        plt.rcParams['font.size'] = 10
        plt.rcParams['axes.titlesize'] = 12
        plt.rcParams['axes.labelsize'] = 10
        
        # Initialize arr_impact if None
        if arr_impact is None:
            arr_impact = {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
        
        # Initialize csone_df if None
        if csone_df is None:
            csone_df = pd.DataFrame()
        
        # Customer column: _prepare_csone outputs customer_name; raw CSOne may have Customer Name
        cust_col = next((c for c in ['customer_name', 'Customer Name', 'BU_NAME', 'Customer'] if c in csone_df.columns), None) if not csone_df.empty else None
        
        # Chart 1: ARR Impact by Issue Category (if adoption barriers data available)
        if arr_impact and arr_impact.get('top_issues') and len(arr_impact['top_issues']) > 0:
            fig, ax = plt.subplots(figsize=(10, 6))
            issues = [issue[0] for issue in arr_impact['top_issues']]
            arr_values = [issue[1]['arr'] for issue in arr_impact['top_issues']]
            
            bars = ax.bar(issues, arr_values, color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd'])
            ax.set_title('ARR Impact by Issue Category', fontsize=14, fontweight='bold')
            ax.set_ylabel('ARR ($)', fontsize=12)
            ax.set_xlabel('Issue Category', fontsize=12)
            
            # Add value labels on bars
            for bar, value in zip(bars, arr_values):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                        f'${value:,.0f}', ha='center', va='bottom', fontweight='bold')
            
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            chart_path = f"outputs/arr_impact_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()
            chart_paths.append(chart_path)
        
        # Chart 2: Support Case Trends (from CSOne data)
        elif not csone_df.empty and 'Date/Time Opened' in csone_df.columns:
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Convert date column and group by month
            csone_df['Date/Time Opened'] = pd.to_datetime(csone_df['Date/Time Opened'], errors='coerce')
            monthly_cases = csone_df.groupby(csone_df['Date/Time Opened'].dt.to_period('M')).size()
            
            if len(monthly_cases) > 0:
                months = [str(period) for period in monthly_cases.index]
                case_counts = monthly_cases.values
                
                bars = ax.bar(months, case_counts, color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd'])
                ax.set_title('Support Cases by Month', fontsize=14, fontweight='bold')
                ax.set_ylabel('Number of Cases', fontsize=12)
                ax.set_xlabel('Month', fontsize=12)
                
                # Add value labels on bars
                for bar, value in zip(bars, case_counts):
                    height = bar.get_height()
                    ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                            f'{int(value)}', ha='center', va='bottom', fontweight='bold')
                
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                chart_path = f"outputs/support_cases_trend_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                plt.savefig(chart_path, dpi=300, bbox_inches='tight')
                plt.close()
                chart_paths.append(chart_path)
        
        # Chart 3: Support Case Severity Distribution (from CSOne data)
        if not csone_df.empty and 'Severity' in csone_df.columns:
            fig, ax = plt.subplots(figsize=(10, 8))
            severity_counts = csone_df['Severity'].value_counts()
            
            # Use executive color scheme: Red (P1), Orange (P2), Yellow (P3), Green (P4)
            color_map = {'P1': '#d62728', '1': '#d62728', 'Critical': '#d62728',
                         'P2': '#ff7f0e', '2': '#ff7f0e', 'High': '#ff7f0e',
                         'P3': '#ffd700', '3': '#ffd700', 'Medium': '#ffd700',
                         'P4': '#2ca02c', '4': '#2ca02c', 'Low': '#2ca02c'}
            colors = [color_map.get(str(sev), '#1f77b4') for sev in severity_counts.index]
            
            wedges, texts, autotexts = ax.pie(severity_counts.values, 
                                              labels=[f'{sev} ({count})' for sev, count in zip(severity_counts.index, severity_counts.values)],
                                              autopct='%1.1f%%',
                                              colors=colors,
                                              startangle=90,
                                              textprops={'fontsize': 11, 'weight': 'bold'})
            
            ax.set_title('Case Severity Distribution\n(Red=Critical, Orange=High Priority)', 
                         fontsize=14, fontweight='bold', pad=20)
            plt.tight_layout()
            chart_path = f"outputs/severity_distribution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()
            chart_paths.append(chart_path)
        
        # Chart 4: Top Customers by Case Volume with ARR (from CSOne data)
        if csone_df is not None and not csone_df.empty and cust_col:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12))
            
            # Top subplot: Case volume
            customer_cases = csone_df[cust_col].value_counts().head(10)
            colors = ['#d62728' if count > 20 else '#ff7f0e' if count > 10 else '#2ca02c' 
                      for count in customer_cases.values]
            
            bars = ax1.barh(range(len(customer_cases)), customer_cases.values, color=colors)
            ax1.set_title('Top 10 Customers by Support Case Volume\n(Red=High Risk, Orange=Moderate, Green=Normal)', 
                          fontsize=14, fontweight='bold', pad=15)
            ax1.set_xlabel('Number of Cases', fontsize=12, fontweight='bold')
            ax1.set_ylabel('Customer', fontsize=12, fontweight='bold')
            
            # Add value labels on bars
            for i, (bar, value) in enumerate(zip(bars, customer_cases.values)):
                width = bar.get_width()
                ax1.text(width + 0.5, bar.get_y() + bar.get_height()/2.,
                         f'{int(value)} cases', ha='left', va='center', fontweight='bold', fontsize=10)
            
            # Set customer names as y-axis labels
            ax1.set_yticks(range(len(customer_cases)))
            ax1.set_yticklabels(customer_cases.index, fontsize=10)
            ax1.invert_yaxis()  # Highest at top
            ax1.grid(axis='x', alpha=0.3)
            
            # Bottom subplot: ARR if available
            if 'Customer_ARR' in csone_df.columns:
                customer_arr = csone_df.groupby(cust_col)['Customer_ARR'].mean().sort_values(ascending=False).head(10)
                colors_arr = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                              '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
                
                bars2 = ax2.barh(range(len(customer_arr)), customer_arr.values, color=colors_arr)
                ax2.set_title('Top 10 Customers by ARR Value', fontsize=14, fontweight='bold', pad=15)
                ax2.set_xlabel('Estimated ARR ($)', fontsize=12, fontweight='bold')
                ax2.set_ylabel('Customer', fontsize=12, fontweight='bold')
                
                for i, (bar, value) in enumerate(zip(bars2, customer_arr.values)):
                    width = bar.get_width()
                    ax2.text(width + width*0.01, bar.get_y() + bar.get_height()/2.,
                             f'${value:,.0f}', ha='left', va='center', fontweight='bold', fontsize=10)
                
                ax2.set_yticks(range(len(customer_arr)))
                ax2.set_yticklabels(customer_arr.index, fontsize=10)
                ax2.invert_yaxis()
                ax2.grid(axis='x', alpha=0.3)
            
            plt.tight_layout()
            chart_path = f"outputs/top_customers_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()
            chart_paths.append(chart_path)
        
        # Chart 5: BEMS Escalations by Customer (if available)
        # Use comprehensive BEMS detection that checks Transaction ID column
        bems_cases, bems_count = detect_bems_escalations(csone_df)
        bems_cust_col = next((c for c in ['customer_name', 'Customer Name', 'BU_NAME', 'Customer'] if c in bems_cases.columns), None) if not bems_cases.empty else None
        if not bems_cases.empty and bems_cust_col:
            fig, ax = plt.subplots(figsize=(12, 8))
            bems_by_customer = bems_cases[bems_cust_col].value_counts().head(10)
            bars = ax.barh(range(len(bems_by_customer)), bems_by_customer.values,
                           color='#d62728')  # Red for critical escalations
            ax.set_title('BEMS Escalations by Customer\n(Engineering-Level Issues)',
                         fontsize=14, fontweight='bold', color='#d62728', pad=15)
            ax.set_xlabel('Number of BEMS Cases', fontsize=12, fontweight='bold')
            ax.set_ylabel('Customer', fontsize=12, fontweight='bold')
            for i, (bar, value) in enumerate(zip(bars, bems_by_customer.values)):
                width = bar.get_width()
                ax.text(width + 0.1, bar.get_y() + bar.get_height()/2.,
                        f'{int(value)} BEMS', ha='left', va='center', fontweight='bold', fontsize=10)
            ax.set_yticks(range(len(bems_by_customer)))
            ax.set_yticklabels(bems_by_customer.index, fontsize=10)
            ax.invert_yaxis()
            ax.grid(axis='x', alpha=0.3)
            plt.tight_layout()
            chart_path = f"outputs/bems_escalations_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()
            chart_paths.append(chart_path)
        
        # Chart 6: Product/Technology Issues (if available)
        if csone_df is not None and not csone_df.empty and 'Product' in csone_df.columns:
            fig, ax = plt.subplots(figsize=(12, 8))
            product_cases = csone_df['Product'].value_counts().head(8)
            
            colors = plt.cm.Set3(range(len(product_cases)))
            bars = ax.bar(range(len(product_cases)), product_cases.values, color=colors)
            ax.set_title('Support Cases by Product/Technology', fontsize=14, fontweight='bold', pad=15)
            ax.set_ylabel('Number of Cases', fontsize=12, fontweight='bold')
            ax.set_xlabel('Product/Technology', fontsize=12, fontweight='bold')
            
            for i, (bar, value) in enumerate(zip(bars, product_cases.values)):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                        f'{int(value)}', ha='center', va='bottom', fontweight='bold', fontsize=10)
            
            ax.set_xticks(range(len(product_cases)))
            ax.set_xticklabels(product_cases.index, rotation=45, ha='right', fontsize=9)
            ax.grid(axis='y', alpha=0.3)
            plt.tight_layout()
            chart_path = f"outputs/product_issues_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()
            chart_paths.append(chart_path)
        
        # Chart 3: Customer ARR Distribution
        if arr_data is not None and not arr_data.empty and 'ANNUAL_CONTRACT_VALUE' in arr_data.columns:
            fig, ax = plt.subplots(figsize=(10, 6))
            arr_values = arr_data['ANNUAL_CONTRACT_VALUE'].fillna(0)
            arr_values = arr_values[arr_values > 0]  # Only positive values
            
            if len(arr_values) > 0:
                # Create bins for ARR ranges
                bins = [0, 100000, 500000, 1000000, 5000000, float('inf')]
                labels = ['<$100K', '$100K-$500K', '$500K-$1M', '$1M-$5M', '>$5M']
                
                hist, bin_edges = np.histogram(arr_values, bins=bins)
                
                bars = ax.bar(labels, hist, color=['#2ca02c', '#1f77b4', '#ff7f0e', '#d62728', '#9467bd'])
                ax.set_title('Customer ARR Distribution', fontsize=14, fontweight='bold')
                ax.set_ylabel('Number of Customers', fontsize=12)
                ax.set_xlabel('ARR Range', fontsize=12)
                
                # Add value labels
                for bar, value in zip(bars, hist):
                    if value > 0:
                        height = bar.get_height()
                        ax.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                                f'{int(value)}', ha='center', va='bottom', fontweight='bold')
                
                plt.tight_layout()
                chart_path = f"outputs/arr_distribution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                plt.savefig(chart_path, dpi=300, bbox_inches='tight')
                plt.close()
                chart_paths.append(chart_path)
        
        # Chart 7: Executive Summary Dashboard - ARR at Risk
        if csone_df is not None and not csone_df.empty and 'Customer_ARR' in csone_df.columns:
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
            fig.suptitle('Executive Risk Dashboard - Financial Impact Analysis', 
                         fontsize=16, fontweight='bold', y=0.995)
            
            # Panel 1: Total ARR by Risk Category
            if 'Severity' in csone_df.columns:
                risk_arr = csone_df.groupby('Severity')['Customer_ARR'].sum()
                colors_risk = {'P1': '#d62728', '1': '#d62728', 'P2': '#ff7f0e', '2': '#ff7f0e',
                               'P3': '#ffd700', '3': '#ffd700', 'P4': '#2ca02c', '4': '#2ca02c'}
                colors = [colors_risk.get(str(sev), '#1f77b4') for sev in risk_arr.index]
                
                wedges, texts, autotexts = ax1.pie(risk_arr.values, 
                                                   labels=[f'{sev}\n${val:,.0f}' for sev, val in zip(risk_arr.index, risk_arr.values)],
                                                   autopct='%1.1f%%',
                                                   colors=colors,
                                                   startangle=90)
                ax1.set_title('ARR Distribution by Case Severity\n(Financial Exposure)', fontweight='bold')
            
            # Panel 2: Top 5 Customers - ARR vs Case Count
            if cust_col:
                case_col = next((c for c in ['SR Number', 'Case Number', 'SR_Number', 'Case_Number'] if c in csone_df.columns), cust_col)
                agg_dict = {'Customer_ARR': 'mean', case_col: 'count'}
                top_customers = csone_df.groupby(cust_col).agg(agg_dict).sort_values('Customer_ARR', ascending=False).head(5)
                top_customers = top_customers.rename(columns={case_col: 'Case_Count'})
            else:
                top_customers = pd.DataFrame()
            
            if not top_customers.empty:
                ax2_twin = ax2.twinx()
                x_pos = range(len(top_customers))
                bars1 = ax2.bar(x_pos, top_customers['Customer_ARR'], color='#1f77b4', alpha=0.7, label='ARR')
                ax2_twin.plot(x_pos, top_customers['Case_Count'], color='#d62728', marker='o', linewidth=3, markersize=10, label='Cases')
                ax2.set_xlabel('Customer', fontweight='bold')
                ax2.set_ylabel('ARR ($)', color='#1f77b4', fontweight='bold')
                ax2_twin.set_ylabel('Number of Cases', color='#d62728', fontweight='bold')
                ax2.set_title('Top 5 Customers: ARR vs Case Volume', fontweight='bold')
                ax2.set_xticks(x_pos)
                ax2.set_xticklabels(top_customers.index, rotation=45, ha='right', fontsize=8)
                ax2.tick_params(axis='y', labelcolor='#1f77b4')
                ax2_twin.tick_params(axis='y', labelcolor='#d62728')
            else:
                ax2.text(0.5, 0.5, 'No customer data available', ha='center', va='center', transform=ax2.transAxes)
            
            # Panel 3: ARR Impact - BEMS Escalations
            # Use comprehensive BEMS detection that checks Transaction ID column
            bems_cases, _ = detect_bems_escalations(csone_df)
            if not bems_cases.empty:
                total_bems_arr = bems_cases['Customer_ARR'].sum()
                total_arr = csone_df['Customer_ARR'].sum()
                no_bems_arr = total_arr - total_bems_arr
                sizes = [total_bems_arr, no_bems_arr]
                labels = [f'Customers with\nBEMS Escalations\n${total_bems_arr:,.0f}',
                          f'No BEMS\n${no_bems_arr:,.0f}']
                colors_bems = ['#d62728', '#2ca02c']
                wedges, texts, autotexts = ax3.pie(sizes, labels=labels, autopct='%1.1f%%',
                                                   colors=colors_bems, startangle=90)
                ax3.set_title('ARR at Risk - Engineering Escalations\n(Red = Critical Attention Needed)', fontweight='bold')
            
            # Panel 4: Key Metrics Summary
            ax4.axis('off')
            metrics_text = []
            
            total_arr = csone_df['Customer_ARR'].sum()
            total_customers = csone_df[cust_col].nunique() if cust_col else len(csone_df)
            total_cases = len(csone_df)
            
            if 'Severity' in csone_df.columns:
                critical_cases = len(csone_df[csone_df['Severity'].isin(['P1', '1', 'Critical'])])
                critical_arr = csone_df[csone_df['Severity'].isin(['P1', '1', 'Critical'])]['Customer_ARR'].sum()
            else:
                critical_cases = 0
                critical_arr = 0
            
            # Use comprehensive BEMS detection that checks Transaction ID column
            bems_cases, bems_count = detect_bems_escalations(csone_df)
            bems_arr = bems_cases['Customer_ARR'].sum() if not bems_cases.empty and 'Customer_ARR' in bems_cases.columns else 0
            
            metrics = [
                ("TOTAL PORTFOLIO ARR", f"${total_arr:,.0f}", "black"),
                ("", "", "white"),
                ("CRITICAL CASES (P1)", f"{critical_cases} cases", "red"),
                ("ARR at Critical Risk", f"${critical_arr:,.0f}", "red"),
                ("", "", "white"),
                ("BEMS ESCALATIONS", f"{bems_count} cases", "darkred"),
                ("ARR with BEMS Issues", f"${bems_arr:,.0f}", "darkred"),
                ("", "", "white"),
                ("AVG ARR per Customer", f"${total_arr/total_customers:,.0f}" if total_customers > 0 else "$0", "blue"),
                ("Cases per Customer", f"{total_cases/total_customers:.1f}" if total_customers > 0 else "0", "blue"),
            ]
            
            y_pos = 0.95
            ax4.text(0.5, y_pos, "KEY FINANCIAL METRICS", ha='center', fontsize=14, fontweight='bold', 
                     transform=ax4.transAxes)
            y_pos -= 0.08
            
            for label, value, color in metrics:
                if label:  # Skip empty lines
                    ax4.text(0.1, y_pos, label, ha='left', fontsize=11, fontweight='bold',
                             color=color, transform=ax4.transAxes)
                    ax4.text(0.9, y_pos, value, ha='right', fontsize=12, fontweight='bold',
                             color=color, transform=ax4.transAxes)
                y_pos -= 0.08
            
            plt.tight_layout()
            chart_path = f"outputs/executive_risk_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()
            chart_paths.append(chart_path)
        
        # Chart 8: Feature Requests - ARR Impact
        if feature_requests and feature_requests.get('customer_examples'):
            fig, ax = plt.subplots(figsize=(14, 8))
            
            # FIXED: Get ALL requesting customers with ARR
            customers_with_arr = [c for c in feature_requests['customer_examples'] if c.get('arr', 0) > 0]
            
            if customers_with_arr:
                # FIXED: No truncation for customer names
                customer_names = [c['customer_name'] for c in customers_with_arr]
                arr_values = [c['arr'] for c in customers_with_arr]
                request_counts = [c['request_count'] for c in customers_with_arr]
                
                # Create bar chart colored by ARR size
                colors = ['#d62728' if arr > 1000000 else '#ff7f0e' if arr > 500000 else '#2ca02c' 
                          for arr in arr_values]
                
                bars = ax.barh(range(len(customer_names)), arr_values, color=colors)
                ax.set_title('Feature Requests - ARR Impact Analysis\n(Customers Requesting New Features)', 
                             fontsize=14, fontweight='bold', pad=15)
                ax.set_xlabel('Customer ARR ($)', fontsize=12, fontweight='bold')
                ax.set_ylabel('Customer', fontsize=12, fontweight='bold')
                
                # Add request counts as labels
                for i, (bar, arr_val, req_count) in enumerate(zip(bars, arr_values, request_counts)):
                    width = bar.get_width()
                    ax.text(width + width*0.02, bar.get_y() + bar.get_height()/2.,
                            f'${arr_val:,.0f} | {req_count} requests', 
                            ha='left', va='center', fontweight='bold', fontsize=9)
                
                ax.set_yticks(range(len(customer_names)))
                ax.set_yticklabels(customer_names, fontsize=10)
                ax.invert_yaxis()
                ax.grid(axis='x', alpha=0.3)
                
                # Add total ARR at bottom
                total_feature_arr = sum(arr_values)
                ax.text(0.5, -0.08, f'Total ARR Requesting Features: ${total_feature_arr:,.0f}',
                        ha='center', va='top', transform=ax.transAxes, 
                        fontsize=12, fontweight='bold', color='#d62728',
                        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
                
                plt.tight_layout()
                chart_path = f"outputs/feature_request_arr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                plt.savefig(chart_path, dpi=300, bbox_inches='tight')
                plt.close()
                chart_paths.append(chart_path)
    
    except ImportError:
        logger.warning("[[WARNING]] Matplotlib not available - charts will be skipped")
    except Exception as e:
        logger.error(f"[[ERROR]] Chart generation failed: {e}")
        import traceback
        logger.error(f"[[ERROR]] Traceback: {traceback.format_exc()}")
    
    return chart_paths


def create_renewal_charts(customer_ab: pd.DataFrame, customer_csone: pd.DataFrame, 
                          renewal_analysis: Dict, ext_incidents: List[Dict] = None,
                          days: int = 90) -> List[str]:
    """Create renewal-specific charts and visualizations"""
    chart_paths = []
    
    try:
        import matplotlib
        matplotlib.use('Agg')  # Use non-interactive backend for server
        import matplotlib.pyplot as plt
        import numpy as np
        from datetime import datetime, timedelta
        
        # Set style for executive reports
        try:
            plt.style.use('seaborn-v0_8-whitegrid')
        except Exception:
            try:
                plt.style.use('seaborn-whitegrid')
            except Exception:
                pass
        
        plt.rcParams['figure.facecolor'] = 'white'
        plt.rcParams['axes.facecolor'] = 'white'
        plt.rcParams['font.size'] = 10
        plt.rcParams['axes.titlesize'] = 12
        plt.rcParams['axes.labelsize'] = 10
        
        # Chart 1: Renewal Risk Score Visualization (Gauge/Donut Chart)
        risk_score = renewal_analysis.get('renewal_risk_score', 0)
        risk_category = renewal_analysis.get('renewal_risk_category', 'UNKNOWN')
        
        fig, ax = plt.subplots(figsize=(10, 8))
        
        # Create donut chart for risk score
        sizes = [risk_score, 100 - risk_score]
        colors = ['#d62728' if risk_score >= 70 else '#ff7f0e' if risk_score >= 50 else '#ffd700' if risk_score >= 30 else '#2ca02c',
                  '#f0f0f0']
        
        wedges, texts, autotexts = ax.pie(sizes, labels=['Risk Score', 'Remaining'], 
                                          autopct='', colors=colors, startangle=90,
                                          pctdistance=0.85, labeldistance=1.1)
        
        # Add center text with risk score
        ax.text(0, 0, f'{int(risk_score)}/100\n{risk_category}', 
                ha='center', va='center', fontsize=24, fontweight='bold',
                color=colors[0])
        
        ax.set_title('Renewal Risk Score\n(Visual Indicator)', fontsize=16, fontweight='bold', pad=20)
        plt.tight_layout()
        chart_path = f"outputs/renewal_risk_score_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(chart_path, dpi=300, bbox_inches='tight')
        plt.close()
        chart_paths.append(chart_path)
        
        # Chart 2: Support Cases Trend Over Time (if CSOne data available)
        if not customer_csone.empty and 'Date/Time Opened' in customer_csone.columns:
            fig, ax = plt.subplots(figsize=(12, 6))
            
            customer_csone['Date/Time Opened'] = pd.to_datetime(customer_csone['Date/Time Opened'], errors='coerce')
            customer_csone = customer_csone.dropna(subset=['Date/Time Opened'])
            
            if len(customer_csone) > 0:
                # Group by week
                customer_csone['Week'] = customer_csone['Date/Time Opened'].dt.to_period('W')
                weekly_cases = customer_csone.groupby('Week').size()
                
                if len(weekly_cases) > 0:
                    weeks = [str(period) for period in weekly_cases.index]
                    case_counts = weekly_cases.values
                    
                    # Color bars based on volume (red for high, orange for medium, green for low)
                    colors = ['#d62728' if count > 5 else '#ff7f0e' if count > 2 else '#2ca02c' 
                              for count in case_counts]
                    
                    bars = ax.bar(weeks, case_counts, color=colors)
                    ax.set_title('Support Cases Trend Over Time\n(Last {} Days)'.format(days), 
                                 fontsize=14, fontweight='bold')
                    ax.set_ylabel('Number of Cases', fontsize=12, fontweight='bold')
                    ax.set_xlabel('Week', fontsize=12, fontweight='bold')
                    
                    # Add value labels
                    for bar, value in zip(bars, case_counts):
                        height = bar.get_height()
                        ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                                f'{int(value)}', ha='center', va='bottom', fontweight='bold', fontsize=9)
                    
                    plt.xticks(rotation=45, ha='right')
                    plt.tight_layout()
                    chart_path = f"outputs/renewal_support_trend_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    plt.savefig(chart_path, dpi=300, bbox_inches='tight')
                    plt.close()
                    chart_paths.append(chart_path)
        
        # Chart 3: Service Incidents Timeline (if incidents available)
        if ext_incidents and len(ext_incidents) > 0:
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # Parse incident dates
            incident_dates = []
            incident_statuses = []
            for inc in ext_incidents:
                if inc.get('published'):
                    try:
                        date = pd.to_datetime(inc.get('published', ''), errors='coerce')
                        if pd.notna(date):
                            incident_dates.append(date)
                            status = inc.get('status', 'Unknown').lower()
                            incident_statuses.append(status)
                    except Exception:
                        pass
            
            if incident_dates:
                # Group by week
                incident_df = pd.DataFrame({'date': incident_dates, 'status': incident_statuses})
                incident_df['week'] = incident_df['date'].dt.to_period('W')
                weekly_incidents = incident_df.groupby('week').size()
                
                if len(weekly_incidents) > 0:
                    weeks = [str(period) for period in weekly_incidents.index]
                    incident_counts = weekly_incidents.values
                    
                    # Color based on high-impact incidents
                    high_impact_statuses = ['investigating', 'identified', 'monitoring']
                    colors = []
                    for week in weekly_incidents.index:
                        week_incidents = incident_df[incident_df['week'] == week]
                        high_impact = sum(1 for s in week_incidents['status'] if s in high_impact_statuses)
                        colors.append('#d62728' if high_impact > 0 else '#ff7f0e' if len(week_incidents) > 2 else '#2ca02c')
                    
                    bars = ax.bar(weeks, incident_counts, color=colors)
                    ax.set_title('Service Incidents Timeline (status.webex.com)\n(Red=High Impact, Orange=Moderate, Green=Low)', 
                                 fontsize=14, fontweight='bold')
                    ax.set_ylabel('Number of Incidents', fontsize=12, fontweight='bold')
                    ax.set_xlabel('Week', fontsize=12, fontweight='bold')
                    
                    # Add value labels
                    for bar, value in zip(bars, incident_counts):
                        height = bar.get_height()
                        ax.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                                f'{int(value)}', ha='center', va='bottom', fontweight='bold', fontsize=9)
                    
                    plt.xticks(rotation=45, ha='right')
                    plt.tight_layout()
                    chart_path = f"outputs/renewal_incidents_timeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    plt.savefig(chart_path, dpi=300, bbox_inches='tight')
                    plt.close()
                    chart_paths.append(chart_path)
        
        # Chart 4: Renewal Health Dashboard (Multi-panel)
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
        fig.suptitle('Renewal Health Dashboard - Comprehensive View', 
                     fontsize=16, fontweight='bold', y=0.995)
        
        # Panel 1: Key Metrics Summary
        metrics = {
            'Support Cases': renewal_analysis.get('support_cases_count', 0),
            'Adoption Barriers': renewal_analysis.get('adoption_barriers_count', 0),
            'BEMS Escalations': renewal_analysis.get('bems_escalations_count', 0),
            'Service Incidents': len(ext_incidents) if ext_incidents else 0
        }
        
        ax1.barh(list(metrics.keys()), list(metrics.values()), 
                 color=['#d62728', '#ff7f0e', '#ffd700', '#2ca02c'])
        ax1.set_title('Key Metrics Summary', fontweight='bold')
        ax1.set_xlabel('Count', fontweight='bold')
        for i, (key, value) in enumerate(metrics.items()):
            ax1.text(value + 0.5, i, f'{int(value)}', va='center', fontweight='bold')
        
        # Panel 2: Risk Category Distribution
        risk_cat = risk_category
        risk_colors = {'CRITICAL': '#d62728', 'HIGH': '#ff7f0e', 'MEDIUM': '#ffd700', 'LOW': '#2ca02c'}
        ax2.pie([risk_score, 100-risk_score], labels=[risk_cat, 'Remaining'], 
                autopct='%1.1f%%', colors=[risk_colors.get(risk_cat, '#1f77b4'), '#f0f0f0'],
                startangle=90)
        ax2.set_title('Risk Category', fontweight='bold')
        
        # Panel 3: Case Severity Distribution (if CSOne data available)
        if not customer_csone.empty and 'Severity' in customer_csone.columns:
            severity_counts = customer_csone['Severity'].value_counts()
            color_map = {'P1': '#d62728', '1': '#d62728', 'Critical': '#d62728',
                         'P2': '#ff7f0e', '2': '#ff7f0e', 'High': '#ff7f0e',
                         'P3': '#ffd700', '3': '#ffd700', 'Medium': '#ffd700',
                         'P4': '#2ca02c', '4': '#2ca02c', 'Low': '#2ca02c'}
            colors = [color_map.get(str(sev), '#1f77b4') for sev in severity_counts.index]
            ax3.pie(severity_counts.values, labels=[f'{sev} ({count})' for sev, count in 
                    zip(severity_counts.index, severity_counts.values)],
                    autopct='%1.1f%%', colors=colors, startangle=90)
            ax3.set_title('Case Severity Distribution', fontweight='bold')
        else:
            ax3.text(0.5, 0.5, 'No Case Data Available', ha='center', va='center', 
                     transform=ax3.transAxes, fontsize=12)
            ax3.set_title('Case Severity Distribution', fontweight='bold')
        
        # Panel 4: Renewal Risk Score Gauge
        ax4.text(0.5, 0.7, 'RENEWAL RISK SCORE', ha='center', va='center',
                 transform=ax4.transAxes, fontsize=14, fontweight='bold')
        ax4.text(0.5, 0.5, f'{int(risk_score)}/100', ha='center', va='center',
                 transform=ax4.transAxes, fontsize=36, fontweight='bold',
                 color=risk_colors.get(risk_cat, '#1f77b4'))
        ax4.text(0.5, 0.3, risk_cat, ha='center', va='center',
                 transform=ax4.transAxes, fontsize=16, fontweight='bold',
                 color=risk_colors.get(risk_cat, '#1f77b4'))
        ax4.axis('off')
        
        plt.tight_layout()
        chart_path = f"outputs/renewal_health_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(chart_path, dpi=300, bbox_inches='tight')
        plt.close()
        chart_paths.append(chart_path)
        
        logger.info(f"[[RENEWAL_CHARTS]] Generated {len(chart_paths)} renewal charts")
        return chart_paths
    
    except ImportError:
        logger.warning("[[WARNING]] Matplotlib not available - renewal charts will be skipped")
    except Exception as e:
        logger.error(f"[[ERROR]] Renewal chart generation failed: {e}")
        import traceback
        logger.error(f"[[ERROR]] Traceback: {traceback.format_exc()}")
    
    return chart_paths


def enrich_csone_with_arr(csone_df: pd.DataFrame, arr_data: pd.DataFrame) -> pd.DataFrame:
    """
    Enrich CSOne data with ARR information from Snowflake, or add placeholder ARR estimates
    """
    if csone_df is None or csone_df.empty:
        return csone_df if csone_df is not None else pd.DataFrame()
    
    # Create a copy to avoid modifying original
    enriched_df = csone_df.copy()
    
    try:
        # If we have real ARR data from Snowflake, merge it
        if arr_data is not None and not arr_data.empty:
            # Try to match on customer name (BU_NAME in ARR data; CSOne may have Customer Name or customer_name)
            cust_col = next((c for c in ['Customer Name', 'customer_name', 'BU_NAME', 'Customer'] if c in enriched_df.columns), None)
            if 'BU_NAME' in arr_data.columns and cust_col:
                # Create ARR lookup dictionary
                arr_lookup = {}
                for _, row in arr_data.iterrows():
                    customer = row.get('BU_NAME', '')
                    arr_value = row.get('ANNUAL_CONTRACT_VALUE', 0)
                    if customer and arr_value:
                        # Sum ARR if customer appears multiple times
                        arr_lookup[customer] = arr_lookup.get(customer, 0) + arr_value
                
                # Add ARR column to CSOne data
                enriched_df['Customer_ARR'] = enriched_df[cust_col].map(arr_lookup).fillna(0)
                logger.info(f"[[OK]] Enriched {len(enriched_df)} CSOne records with ARR data")
            else:
                # No match possible, add placeholder
                enriched_df['Customer_ARR'] = 0
                logger.warning(f"[[WARNING]] Could not match ARR data to customers - using placeholders")
        else:
            # No ARR data available - create estimated ARR based on case severity/volume
            # This is a rough estimate: More P1/P2 cases = larger customer
            logger.info(f"[[INFO]] No ARR data available - creating estimated ARR based on support activity")
            
            # Create estimated ARR tiers based on case characteristics
            def estimate_arr(customer_name, customer_cases):
                case_count = len(customer_cases)
                p1_count = len(customer_cases[customer_cases['Severity'].astype(str) == 'P1']) if 'Severity' in customer_cases.columns else 0
                p2_count = len(customer_cases[customer_cases['Severity'].astype(str) == 'P2']) if 'Severity' in customer_cases.columns else 0
                
                # Rough estimation logic:
                # High case volume + high severity = enterprise customer (high ARR)
                if p1_count >= 2 or (p2_count >= 3 and case_count >= 5):
                    return 5000000  # $5M estimate
                elif p1_count >= 1 or (p2_count >= 2 and case_count >= 3):
                    return 1000000  # $1M estimate
                elif case_count >= 5:
                    return 500000   # $500K estimate
                elif case_count >= 2:
                    return 100000   # $100K estimate
                else:
                    return 50000    # $50K estimate
            
            # Calculate estimated ARR for each customer
            cust_col = next((c for c in ['Customer Name', 'customer_name', 'BU_NAME', 'Customer'] if c in enriched_df.columns), None)
            if cust_col:
                arr_estimates = {}
                for customer in enriched_df[cust_col].dropna().unique():
                    customer_cases = enriched_df[enriched_df[cust_col] == customer]
                    arr_estimates[customer] = estimate_arr(customer, customer_cases)
                
                enriched_df['Customer_ARR'] = enriched_df[cust_col].map(arr_estimates).fillna(50000)
                enriched_df['ARR_Estimated'] = True  # Flag that this is estimated
                logger.info(f"[[INFO]] Created estimated ARR for {len(arr_estimates)} customers")
            else:
                enriched_df['Customer_ARR'] = 50000  # Default estimate
                enriched_df['ARR_Estimated'] = True
    
    except Exception as e:
        logger.error(f"[[ERROR]] Failed to enrich CSOne with ARR: {e}")
        enriched_df['Customer_ARR'] = 0
        enriched_df['ARR_Estimated'] = False
    
    return enriched_df

def analyze_feature_requests(csone_df: pd.DataFrame, arr_data: pd.DataFrame = None) -> Dict[str, Any]:
    """Analyze feature requests from CSOne data and link to customer ARR"""
    feature_requests = {
        'total_requests': 0,
        'top_features': [],
        'customer_examples': [],
        'total_arr_impact': 0
    }
    
    if csone_df is None or csone_df.empty:
        return feature_requests
    
    try:
        # Keywords that indicate feature requests
        feature_keywords = ['enhancement', 'feature request', 'rfr', 'new feature', 'add feature', 
                            'request for', 'would like', 'need feature', 'missing feature',
                            'enhancement request', 'feature enhancement', 'roadmap', 'future enhancement']
        
        # Find cases that are likely feature requests (use flexible column names for CSOne exports)
        from adoptiq_backend import LIKELY_TITLE_COLS, LIKELY_DESC_COLS
        title_col = next((c for c in LIKELY_TITLE_COLS if c in csone_df.columns), None)
        desc_col = next((c for c in LIKELY_DESC_COLS if c in csone_df.columns), None)
        csone_df['Title_Lower'] = (csone_df[title_col].astype(str).str.lower() if title_col
                                   else pd.Series([''] * len(csone_df), index=csone_df.index))
        csone_df['Desc_Lower'] = (csone_df[desc_col].astype(str).str.lower() if desc_col
                                  else pd.Series([''] * len(csone_df), index=csone_df.index))
        
        # Create boolean mask for feature requests
        is_feature_request = pd.Series([False] * len(csone_df), index=csone_df.index)
        for keyword in feature_keywords:
            is_feature_request |= csone_df['Title_Lower'].str.contains(keyword, na=False)
            is_feature_request |= csone_df['Desc_Lower'].str.contains(keyword, na=False)
        
        feature_request_cases = csone_df[is_feature_request].copy()
        feature_requests['total_requests'] = len(feature_request_cases)
        
        if len(feature_request_cases) > 0:
            # Group by customer to find which customers are requesting features
            feat_cust_col = next((c for c in ['Customer Name', 'customer_name', 'BU_NAME', 'Customer'] if c in feature_request_cases.columns), None)
            if feat_cust_col:
                customer_requests = feature_request_cases.groupby(feat_cust_col).size().sort_values(ascending=False)
                
                # FIXED: Get ALL requesting customers with ARR if available
                for customer, count in customer_requests.items():
                    customer_info = {
                        'customer_name': customer,
                        'request_count': int(count),
                        'arr': 0,
                        'sample_requests': []
                    }
                    
                    # Get ARR for this customer if available
                    if arr_data is not None and not arr_data.empty and 'BU_NAME' in arr_data.columns:
                        customer_arr = arr_data[arr_data['BU_NAME'] == customer]
                        if not customer_arr.empty and 'ANNUAL_CONTRACT_VALUE' in customer_arr.columns:
                            customer_info['arr'] = float(customer_arr['ANNUAL_CONTRACT_VALUE'].sum())
                            feature_requests['total_arr_impact'] += customer_info['arr']
                    
                    # FIXED: Get ALL requests from this customer
                    customer_cases = feature_request_cases[feature_request_cases[feat_cust_col] == customer]
                    if 'Title' in customer_cases.columns:
                        sample_titles = customer_cases['Title'].dropna().tolist()
                        customer_info['sample_requests'] = sample_titles
                    
                    feature_requests['customer_examples'].append(customer_info)
            
            # Analyze most common feature themes (simple keyword frequency)
            all_text = ' '.join(feature_request_cases['Title_Lower'].fillna('') + ' ' + 
                                feature_request_cases['Desc_Lower'].fillna(''))
            
            # Common feature themes to look for
            feature_themes = {
                'integration': ['integration', 'integrate', 'api', 'sso', 'ldap', 'saml', 'oauth'],
                'reporting': ['report', 'reporting', 'analytics', 'dashboard', 'export', 'metrics'],
                'mobile': ['mobile', 'ios', 'android', 'app', 'tablet'],
                'security': ['security', 'encryption', 'compliance', 'audit', 'privacy'],
                'automation': ['automation', 'automate', 'automatic', 'workflow', 'provisioning'],
                'notifications': ['notification', 'alert', 'email', 'notify', 'reminder'],
                'customization': ['custom', 'customize', 'configuration', 'settings', 'branding'],
                'collaboration': ['share', 'sharing', 'collaborate', 'team', 'group']
            }
            
            theme_counts = {}
            for theme, keywords in feature_themes.items():
                count = sum(all_text.count(keyword) for keyword in keywords)
                if count > 0:
                    theme_counts[theme] = count
            
            # FIXED: Show ALL themes sorted by frequency
            sorted_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
            feature_requests['top_features'] = [(theme, count) for theme, count in sorted_themes]
        
    except Exception as e:
        logger.error(f"[[ERROR]] Feature request analysis failed: {e}")
        import traceback
        logger.error(f"[[ERROR]] Traceback: {traceback.format_exc()}")
    
    return feature_requests

def calculate_arr_impact_for_issues(ab_norm: pd.DataFrame, arr_data: pd.DataFrame) -> Dict[str, Any]:
    """Calculate combined ARR for customers experiencing specific issues"""
    if ab_norm is None or arr_data is None:
        return {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
    if ab_norm.empty or arr_data.empty:
        return {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
    
    try:
        # Check which ARR columns actually exist in the data
        arr_cols_to_merge = ['ACCOUNT_ID_C']
        if 'ANNUAL_CONTRACT_VALUE' in arr_data.columns:
            arr_cols_to_merge.append('ANNUAL_CONTRACT_VALUE')
        if 'MRR' in arr_data.columns:
            arr_cols_to_merge.append('MRR')
        if 'TCV' in arr_data.columns:
            arr_cols_to_merge.append('TCV')
        if 'BU_NAME' in arr_data.columns:
            arr_cols_to_merge.append('BU_NAME')
        
        # Merge adoption barriers with ARR data
        merged_data = ab_norm.merge(
            arr_data[arr_cols_to_merge], 
            on='ACCOUNT_ID_C', 
            how='left'
        )
    except Exception as e:
        logger.error(f"[[ERROR]] Error merging ARR data: {e}")
        return {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
    
    # Fill missing ARR values with 0 (only if columns exist)
    if 'ANNUAL_CONTRACT_VALUE' in merged_data.columns:
        merged_data['ANNUAL_CONTRACT_VALUE'] = merged_data['ANNUAL_CONTRACT_VALUE'].fillna(0)
    else:
        merged_data['ANNUAL_CONTRACT_VALUE'] = 0
    
    if 'MRR' in merged_data.columns:
        merged_data['MRR'] = merged_data['MRR'].fillna(0)
    else:
        merged_data['MRR'] = 0
    
    if 'TCV' in merged_data.columns:
        merged_data['TCV'] = merged_data['TCV'].fillna(0)
    else:
        merged_data['TCV'] = 0
    
    # Calculate total ARR
    total_arr = merged_data['ANNUAL_CONTRACT_VALUE'].sum()
    total_mrr = merged_data['MRR'].sum()
    total_tcv = merged_data['TCV'].sum()
    
    # Group by issue type/category
    issue_breakdown = {}
    if 'AB_CATEGORY_C' in merged_data.columns:
        for category in merged_data['AB_CATEGORY_C'].dropna().unique():
            category_data = merged_data[merged_data['AB_CATEGORY_C'] == category]
            issue_breakdown[category] = {
                'arr': category_data['ANNUAL_CONTRACT_VALUE'].sum(),
                'mrr': category_data['MRR'].sum(),
                'tcv': category_data['TCV'].sum(),
                'customer_count': category_data['ACCOUNT_ID_C'].nunique(),
                'issue_count': len(category_data)
            }
    
    # FIXED: ALL issues by ARR impact
    top_issues = []
    if issue_breakdown:
        sorted_issues = sorted(issue_breakdown.items(), key=lambda x: x[1]['arr'], reverse=True)
        top_issues = sorted_issues  # All issues by ARR impact
    
    return {
        "total_arr": total_arr,
        "total_mrr": total_mrr,
        "total_tcv": total_tcv,
        "issue_breakdown": issue_breakdown,
        "top_issues": top_issues,
        "customer_count": merged_data['ACCOUNT_ID_C'].nunique(),
        "total_issues": len(merged_data)
    }

def _create_enhanced_compact_report(base_path: str, manager: str, technology: str, days: int, 
                                    ai_insights: Dict, csone_df: pd.DataFrame, ab_norm: pd.DataFrame,
                                    arr_data: pd.DataFrame, arr_impact: Dict, 
                                    chart_paths: List[str], feature_requests: Dict,
                                    team_subs_df: pd.DataFrame = None,
                                    csconsole_action_plans: pd.DataFrame = None,
                                    csconsole_customer_pulse: pd.DataFrame = None,
                                    csconsole_success_priorities: pd.DataFrame = None,
                                    csconsole_adoption_barriers: pd.DataFrame = None) -> str:
    """Create an enhanced compact report with real data, tables, and charts using ALL data sources"""
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from datetime import datetime
    import os
    
    doc = Document()
    
    # Title
    title = doc.add_heading(f'AdoptIQ Portfolio Analysis - {manager}', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    subtitle = doc.add_paragraph(f'{technology} - {days} Day Analysis')
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.runs[0].bold = True
    
    doc.add_paragraph()  # Spacing
    
    # Initialize variables outside if block to avoid scope issues
    total_customers = 0
    total_cases = 0
    p1_count = 0
    p2_count = 0
    bems_count = 0
    defect_count = 0
    vuln_count = 0
    customer_col = None
    severity_col = None
    bems_cases = pd.DataFrame()
    software_defects = {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}}
    psirt_vulns = {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}}
    
    # CRITICAL FIX: Calculate customer count FIRST (before conditional check)
    # This ensures we show customer count even if CSOne is empty but we have customers from other sources
    # CRITICAL FIX: Use comprehensive function to get ALL customers from ALL available data sources
    # team_subs_df is PRIMARY source (unfiltered, contains all customers assigned to manager)
    # This ensures consistent customer counts across all report types
    all_customers_set = _get_all_customers_from_all_sources(
        ab_norm=ab_norm,
        csone_df=csone_df,
        team_subs_df=team_subs_df if team_subs_df is not None else pd.DataFrame(),
        csconsole_action_plans=csconsole_action_plans if csconsole_action_plans is not None else pd.DataFrame(),
        csconsole_customer_pulse=csconsole_customer_pulse if csconsole_customer_pulse is not None else pd.DataFrame(),
        csconsole_success_priorities=csconsole_success_priorities if csconsole_success_priorities is not None else pd.DataFrame(),
        csconsole_adoption_barriers=csconsole_adoption_barriers if csconsole_adoption_barriers is not None else pd.DataFrame()
    )
    total_customers = len(all_customers_set)
    logger.info(f"[[CUSTOMER_COUNT]] Total unique customers from all data sources: {total_customers}")
    logger.info(f"[[CUSTOMER_COUNT]] Team subscriptions (PRIMARY): {len(team_subs_df['BU_NAME'].unique()) if not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns else 0} customers")
    
    # Extract software defects (BST/CSC IDs) and PSIRT vulnerabilities (do this even if CSOne is empty)
    software_defects = extract_software_defects(csone_df, ab_norm)
    psirt_vulns = extract_psirt_vulnerabilities(csone_df, ab_norm)
    defect_count = software_defects.get('total_defects', 0)
    vuln_count = psirt_vulns.get('total_vulnerabilities', 0)
    
    # Add Executive Dashboard with Key Metrics (inspired by McKinsey/BCG reports)
    # FIXED: Show dashboard if we have ANY customers (not just if CSOne is not empty)
    # This ensures customer count is always displayed even if there are no cases
    if total_customers > 0 or not csone_df.empty or not ab_norm.empty:
        doc.add_heading('At-a-Glance Dashboard', level=1)
        
        # Create metrics table (7 columns for comprehensive metrics including defects)
        metrics_table = doc.add_table(rows=2, cols=7)
        metrics_table.style = 'Light Grid Accent 1'
        
        # Calculate metrics - Debug CSOne columns first
        if not csone_df.empty:
            logger.info(f"[[DEBUG]] CSOne DataFrame columns: {list(csone_df.columns)}")
            logger.info(f"[[DEBUG]] CSOne DataFrame shape: {csone_df.shape}")
            logger.info(f"[[DEBUG]] CSOne DataFrame sample (first 3 rows): {csone_df.head(3).to_dict('records') if not csone_df.empty else 'EMPTY'}")
        total_cases = len(csone_df) if not csone_df.empty else 0
        
        # Try different severity and customer column variations (only if CSOne is not empty)
        severity_col = None
        customer_col = None
        if not csone_df.empty:
            for col in ['Severity', 'severity', 'Priority', 'priority', 'Case_Priority']:
                if col in csone_df.columns:
                    severity_col = col
                    break
            for col in ['Customer Name', 'customer_name', 'Customer', 'BU_NAME', 'Account Name']:
                if col in csone_df.columns:
                    customer_col = col
                    break
            
        if severity_col:
            p1_count = len(csone_df[csone_df[severity_col].isin(['P1', '1', 'Critical', 'Critical - P1'])])
            p2_count = len(csone_df[csone_df[severity_col].isin(['P2', '2', 'High', 'High - P2'])])
            logger.info(f"[[DEBUG]] Found {p1_count} P1 cases and {p2_count} P2 cases using column '{severity_col}'")
        
        # Use centralized BEMS detection function that checks Transaction ID column (only if CSOne is not empty)
        if not csone_df.empty:
            bems_cases, bems_count = detect_bems_escalations(csone_df)
        else:
            bems_count = 0
            bems_cases = pd.DataFrame()
        
        # Header row - expanded to include software defects
        headers = ['Total Customers', 'Support Cases', 'Critical (P1)', 'High (P2)', 'BEMS Escalations', 'Software Defects', 'Security Vulnerabilities']
        for i, header in enumerate(headers):
            cell = metrics_table.rows[0].cells[i]
            cell.text = header
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(11)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Values row - expanded to include software defects and vulnerabilities
        values = [str(total_customers), str(total_cases), str(p1_count), str(p2_count), str(bems_count), str(defect_count), str(vuln_count)]
        colors = [
            None, 
            None, 
            RGBColor(192, 0, 0) if p1_count > 0 else None, 
            RGBColor(255, 140, 0) if p2_count > 0 else None, 
            RGBColor(139, 0, 0) if bems_count > 0 else None,
            RGBColor(200, 100, 0) if defect_count > 0 else None,
            RGBColor(255, 0, 0) if vuln_count > 0 else None
        ]
        
        for i, (value, color) in enumerate(zip(values, colors)):
            cell = metrics_table.rows[1].cells[i]
            cell.text = value
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(18)
                    run.bold = True
                    if color:
                        run.font.color.rgb = color
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_paragraph()  # Spacing
    else:
        # Edge case: No customers and no data - show empty dashboard
        logger.warning(f"[[WARNING]] No customers found in any data source - dashboard will be minimal")
    
    doc.add_heading('Executive Summary', level=1)
    
    # Add Data-Driven Executive Overview (before AI narrative)
    # FIXED: Show overview if we have ANY data (customers, cases, or barriers)
    # Use the customer count already calculated above (don't recalculate) - ensures consistency
    if total_customers > 0 or not csone_df.empty or (ab_norm is not None and not ab_norm.empty):
        overview_para = doc.add_paragraph()
        overview_para.add_run('Portfolio Overview\n').bold = True
        overview_para.runs[0].font.size = Pt(13)
        
        # Use the total_customers already calculated from unified function above (line 1846)
        # No need to recalculate - ensures consistency with dashboard
        total_cases = len(csone_df) if not csone_df.empty else 0
        
        overview_para.add_run(f'• Total Customers: {total_customers}\n')
        overview_para.add_run(f'• Total Support Cases: {total_cases}\n')
        
        # Use robust severity detection
        severity_col = None
        if not csone_df.empty:
            for col in ['Severity', 'severity', 'Priority', 'priority', 'Case_Priority']:
                if col in csone_df.columns:
                    severity_col = col
                    break
            
        if severity_col:
            p1_count = len(csone_df[csone_df[severity_col].isin(['P1', '1', 'Critical', 'Critical - P1'])])
            p2_count = len(csone_df[csone_df[severity_col].isin(['P2', '2', 'High', 'High - P2'])])
            overview_para.add_run(f'• Critical Cases (P1): {p1_count}\n')
            overview_para.add_run(f'• High Priority Cases (P2): {p2_count}\n')
        
        # Use centralized BEMS detection function that checks Transaction ID column
        bems_cases, bems_count = detect_bems_escalations(csone_df)
        if bems_count > 0:
            bems_customers = bems_cases[customer_col].nunique() if customer_col and customer_col in bems_cases.columns else 0
            run = overview_para.add_run(f'• BEMS Escalations: {bems_count} cases across {bems_customers} customers\n')
            run.font.color.rgb = RGBColor(192, 0, 0)
            run.bold = True
        doc.add_paragraph()  # Spacing
    
    # Add Financial Impact Summary Box (if ARR data available)
    if not csone_df.empty and 'Customer_ARR' in csone_df.columns:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        
        # Calculate key financial metrics
        total_arr = csone_df['Customer_ARR'].sum()
        total_customers = csone_df[customer_col].nunique() if customer_col else 0
        
        critical_arr = 0
        bems_arr = 0
        if 'Severity' in csone_df.columns:
            critical_arr = csone_df[csone_df['Severity'].isin(['P1', '1', 'Critical'])]['Customer_ARR'].sum()
        # Use comprehensive BEMS detection that checks Transaction ID column
        bems_cases, _ = detect_bems_escalations(csone_df)
        bems_arr = bems_cases['Customer_ARR'].sum() if not bems_cases.empty and 'Customer_ARR' in bems_cases.columns else 0
        
        # Add shaded box with financial summary
        financial_para = doc.add_paragraph()
        financial_para.paragraph_format.left_indent = Inches(0.5)
        financial_para.paragraph_format.right_indent = Inches(0.5)
        
        run = financial_para.add_run('💰 FINANCIAL IMPACT SUMMARY\n\n')
        run.bold = True
        run.font.size = Pt(14)
        run.font.color.rgb = RGBColor(0, 51, 102)
        
        run = financial_para.add_run(f'Total Portfolio ARR: ')
        run.bold = True
        run = financial_para.add_run(f'${total_arr:,.0f}\n')
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(0, 112, 192)
        
        if critical_arr > 0:
            run = financial_para.add_run(f'ARR at Critical Risk (P1): ')
            run.bold = True
            run = financial_para.add_run(f'${critical_arr:,.0f} ')
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(192, 0, 0)
            run = financial_para.add_run(f'({(critical_arr/total_arr*100):.1f}% of portfolio)\n' if total_arr > 0 else '\n')
            run.font.size = Pt(11)
        
        if bems_arr > 0:
            run = financial_para.add_run(f'ARR with Engineering Escalations: ')
            run.bold = True
            run = financial_para.add_run(f'${bems_arr:,.0f} ')
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(192, 0, 0)
            run = financial_para.add_run(f'({(bems_arr/total_arr*100):.1f}% of portfolio)\n' if total_arr > 0 else '\n')
            run.font.size = Pt(11)
        
        if feature_requests and feature_requests.get('total_arr_impact', 0) > 0:
            feature_arr = feature_requests['total_arr_impact']
            run = financial_para.add_run(f'ARR Requesting Features: ')
            run.bold = True
            run = financial_para.add_run(f'${feature_arr:,.0f}\n')
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(255, 140, 0)
        
        run = financial_para.add_run(f'\nAverage ARR per Customer: ')
        run.bold = True
        avg_arr = total_arr / total_customers if total_customers > 0 else 0
        run = financial_para.add_run(f'${avg_arr:,.0f}')
        run.font.size = Pt(11)
        
        # Add background color to paragraph (light blue)
        shading_elm = OxmlElement('w:shd')
        shading_elm.set(qn('w:fill'), 'E7F3FF')
        financial_para._element.get_or_add_pPr().append(shading_elm)
        
        doc.add_paragraph()  # Spacing
    
    # Add AI-generated summary
    if ai_insights and 'executive_summary' in ai_insights:
        summary_text = ai_insights['executive_summary']
        # Parse markdown
        _parse_markdown_for_fallback(doc, summary_text)
    
    # Add Key Insights & Recommendations Section (Professional Executive Format)
    if not csone_df.empty:
        doc.add_heading('Key Insights & Strategic Recommendations', level=1)
        
        # Create insights based on actual data
        insights_para = doc.add_paragraph()
        insights_para.add_run('Critical Findings:\n').bold = True
        insights_para.runs[0].font.size = Pt(13)
        
        # Calculate risk indicators
        if customer_col:
            total_customers = csone_df[customer_col].nunique()
            avg_cases_per_customer = total_cases / total_customers if total_customers > 0 else 0
            
            if avg_cases_per_customer > 3:
                insights_para.add_run(f'• HIGH SUPPORT LOAD: Average {avg_cases_per_customer:.1f} cases per customer indicates potential adoption challenges\n')
            elif avg_cases_per_customer > 1.5:
                insights_para.add_run(f'• MODERATE SUPPORT LOAD: Average {avg_cases_per_customer:.1f} cases per customer requires monitoring\n')
            else:
                insights_para.add_run(f'• NORMAL SUPPORT LOAD: Average {avg_cases_per_customer:.1f} cases per customer indicates healthy adoption\n')
        
        if severity_col:
            p1_pct = (p1_count / total_cases * 100) if total_cases > 0 else 0
            p2_pct = (p2_count / total_cases * 100) if total_cases > 0 else 0
            
            if p1_pct > 5:
                insights_para.add_run(f'• CRITICAL RISK: {p1_pct:.1f}% of cases are P1 - immediate attention required\n')
            elif p1_pct > 2:
                insights_para.add_run(f'• ELEVATED RISK: {p1_pct:.1f}% of cases are P1 - proactive intervention needed\n')
            
            if p2_pct > 20:
                insights_para.add_run(f'• HIGH PRIORITY VOLUME: {p2_pct:.1f}% of cases are P2 - resource allocation review needed\n')
        
        if bems_count > 0:
            bems_pct = (bems_count / total_cases * 100) if total_cases > 0 else 0
            insights_para.add_run(f'• ENGINEERING ESCALATIONS: {bems_pct:.1f}% of cases require engineering intervention - product stability concerns\n')
        
        if defect_count > 0:
            cases_with_defects = software_defects.get("total_cases_with_defects", 0)
            insights_para.add_run(f'• SOFTWARE DEFECTS: {defect_count} unique BST/CSC defects identified across {cases_with_defects} cases - product quality impact\n')
        
        if vuln_count > 0:
            insights_para.add_run(f'• SECURITY VULNERABILITIES: {vuln_count} security vulnerabilities (CVEs/PSIRT) identified - security risk assessment needed\n')
        
        doc.add_paragraph()
        
        # Strategic Recommendations
        recommendations_para = doc.add_paragraph()
        recommendations_para.add_run('Strategic Recommendations:\n').bold = True
        recommendations_para.runs[0].font.size = Pt(13)
        
        if bems_count > 0:
            recommendations_para.add_run('• IMMEDIATE: Address engineering escalations to prevent customer churn\n')
        if defect_count > 0:
            recommendations_para.add_run('• CRITICAL: Track and resolve software defects (BST/CSC) affecting customer experience\n')
        if vuln_count > 0:
            recommendations_para.add_run('• SECURITY: Address security vulnerabilities (CVEs/PSIRT) to protect customer environments\n')
        if p1_count > 0:
            recommendations_para.add_run('• URGENT: Implement P1 case response protocol and root cause analysis\n')
        if customer_col and total_customers > 0:
            high_volume_customers = csone_df[customer_col].value_counts()
            if len(high_volume_customers[high_volume_customers > 5]) > 0:
                recommendations_para.add_run('• STRATEGIC: Deploy dedicated success managers for high-volume customers\n')
        
        recommendations_para.add_run('• PROACTIVE: Implement predictive analytics to identify at-risk customers early\n')
        recommendations_para.add_run('• SYSTEMATIC: Establish regular executive reviews of customer health metrics\n')
        
        doc.add_paragraph()
    
    doc.add_page_break()
    
    # === REAL DATA TABLES ===
    doc.add_heading('Portfolio Data & Analysis', level=1)
    
    # FIXED: Show ALL customers by case volume
    if not csone_df.empty and customer_col:
        doc.add_heading('All Customers by Support Cases', level=2)
        
        customer_cases = csone_df[customer_col].value_counts()  # Show ALL customers
        has_arr = 'Customer_ARR' in csone_df.columns
        
        # Create table
        table = doc.add_table(rows=len(customer_cases) + 1, cols=4 if has_arr else 3)
        table.style = 'Light Grid Accent 1'
        
        # Headers
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = 'Customer Name'
        hdr_cells[1].text = 'Total Cases'
        hdr_cells[2].text = 'BEMS References'
        if has_arr:
            hdr_cells[3].text = 'Estimated ARR'
        
        for cell in hdr_cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.bold = True
        
        # Data rows
        for i, (customer, case_count) in enumerate(customer_cases.items(), 1):
            row_cells = table.rows[i].cells
            row_cells[0].text = customer
            row_cells[1].text = str(case_count)
            
            # Count BEMS references for this customer - use comprehensive detection
            customer_data = csone_df[csone_df[customer_col] == customer]
            customer_bems_cases, customer_bems_count = detect_bems_escalations(customer_data)
            
            if customer_bems_count > 0:
                # Extract BEMS references from Transaction ID (primary) and bemscsc_refs (secondary)
                bems_refs_list = []
                for _, row in customer_bems_cases.iterrows():
                    # PRIMARY: Transaction ID
                    if 'Transaction ID' in row and pd.notna(row['Transaction ID']):
                        tx_id = str(row['Transaction ID']).strip()
                        if tx_id and 'BEMS' in tx_id.upper():
                            bems_refs_list.append(tx_id)
                    # SECONDARY: bemscsc_refs
                    if 'bemscsc_refs' in row and pd.notna(row['bemscsc_refs']):
                        bems_ref = str(row['bemscsc_refs']).strip()
                        if bems_ref and bems_ref != '[]' and 'BEMS' in bems_ref.upper():
                            bems_refs_list.append(bems_ref)
                
                if bems_refs_list:
                    all_refs = ', '.join(set(bems_refs_list))  # Remove duplicates
                    # FIXED: Show all BEMS references without truncation
                    row_cells[2].text = all_refs
                else:
                    row_cells[2].text = '-'
            else:
                row_cells[2].text = '-'
            
            # Add ARR
            if has_arr:
                avg_arr = customer_data['Customer_ARR'].mean()
                row_cells[3].text = f"${avg_arr:,.0f}"
        
        doc.add_paragraph()
    
    # Table 2: Case Severity Breakdown
    if not csone_df.empty and 'Severity' in csone_df.columns:
        doc.add_heading('Case Severity Distribution', level=2)
        
        severity_counts = csone_df['Severity'].value_counts()
        table = doc.add_table(rows=len(severity_counts) + 1, cols=2)
        table.style = 'Light Grid Accent 1'
        
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = 'Severity'
        hdr_cells[1].text = 'Count'
        for cell in hdr_cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.bold = True
        
        for i, (severity, count) in enumerate(severity_counts.items(), 1):
            row_cells = table.rows[i].cells
            row_cells[0].text = str(severity)
            row_cells[1].text = str(count)
        
        doc.add_paragraph()
    
    # === BEMS ESCALATIONS TABLE ===
    # Use comprehensive BEMS detection that checks Transaction ID column
    bems_cases, bems_count = detect_bems_escalations(csone_df)
    if not bems_cases.empty and bems_count > 0:
        doc.add_heading('BEMS Escalations & Engineering Issues', level=2)
        # Group by customer - extract BEMS from Transaction ID (primary) and bemscsc_refs (secondary)
        def extract_bems_refs(row):
            """Extract BEMS references from Transaction ID and bemscsc_refs"""
            refs = []
            # PRIMARY: Transaction ID
            if 'Transaction ID' in bems_cases.columns and pd.notna(row.get('Transaction ID', None)):
                tx_id = str(row['Transaction ID']).strip()
                if tx_id and 'BEMS' in tx_id.upper():
                    refs.append(tx_id)
            # SECONDARY: bemscsc_refs (check even when Transaction ID is empty)
            if 'bemscsc_refs' in bems_cases.columns and pd.notna(row.get('bemscsc_refs', None)):
                bems_ref = str(row['bemscsc_refs']).strip()
                if bems_ref and bems_ref != '[]' and 'BEMS' in bems_ref.upper():
                    refs.append(bems_ref)
            return ', '.join([ref for ref in refs if ref])
        
        bems_cases['bems_extracted'] = bems_cases.apply(extract_bems_refs, axis=1)
        
        # Find customer column name
        customer_col_bems = None
        for col in ['Customer Name', 'customer_name', 'Customer', 'BU_NAME', 'Account Name']:
            if col in bems_cases.columns:
                customer_col_bems = col
                break
        
        if customer_col_bems:
            # Find case number column
            case_col = None
            for col in ['SR Number', 'Case Number', 'SR_Number', 'Case_Number']:
                if col in bems_cases.columns:
                    case_col = col
                    break
            
            if case_col:
                bems_by_customer = bems_cases.groupby(customer_col_bems).agg({
                    'bems_extracted': lambda x: ', '.join([ref for ref in x if ref and str(ref).strip()]),
                    case_col: 'count'
                }).reset_index()
                bems_by_customer.columns = [customer_col_bems, 'BEMS/CSC References', 'Case Count']
            else:
                # Fallback if no case number column - count rows per customer instead
                bems_by_customer = bems_cases.groupby(customer_col_bems).agg({
                    'bems_extracted': lambda x: ', '.join([ref for ref in x if ref and str(ref).strip()]),
                    customer_col_bems: 'count'  # Count actual rows per customer
                }).reset_index()
                bems_by_customer.columns = [customer_col_bems, 'BEMS/CSC References', 'Case Count']
        else:
            # Fallback if no customer column
            bems_by_customer = pd.DataFrame(columns=['Customer Name', 'BEMS/CSC References', 'Case Count'])
        if not bems_by_customer.empty:
            # FIXED: Show ALL customers with BEMS escalations
            bems_by_customer = bems_by_customer.sort_values('Case Count', ascending=False)
            
            table = doc.add_table(rows=len(bems_by_customer) + 1, cols=3)
            table.style = 'Light Grid Accent 1'
            
            hdr_cells = table.rows[0].cells
            hdr_cells[0].text = 'Customer Name'
            hdr_cells[1].text = 'BEMS/CSC References'
            hdr_cells[2].text = 'Cases with Escalations'
            
            for cell in hdr_cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True
            
            # Use iterrows() for safer column name access (avoids positional issues)
            for i, (idx, row) in enumerate(bems_by_customer.iterrows(), 1):
                row_cells = table.rows[i].cells
                # Access by column name - safe even if column order changes
                customer_name = row.get(customer_col_bems, 'Unknown') if customer_col_bems else 'Unknown'
                bems_refs = row.get('BEMS/CSC References', '')
                case_count = row.get('Case Count', 0)
                
                row_cells[0].text = str(customer_name)
                # FIXED: Show all BEMS references without truncation
                row_cells[1].text = str(bems_refs)
                row_cells[2].text = str(case_count)
        else:
            doc.add_paragraph("No BEMS data available for grouping.")
            doc.add_paragraph()
    else:
        doc.add_paragraph("No BEMS escalations identified in current data.")
    doc.add_paragraph()
    
    # === SOFTWARE DEFECTS TABLE ===
    if defect_count > 0:
        doc.add_heading('Software Defects (BST/CSC IDs)', level=2)
        cases_with_defects = software_defects.get("total_cases_with_defects", 0)
        doc.add_paragraph(f'Total unique software defects identified: {defect_count} across {cases_with_defects} cases')
        
        # Create defects by customer table
        defect_by_customer = software_defects.get('defect_by_customer', {})
        if defect_by_customer:
            defect_customer_data = []
            for customer, defects in defect_by_customer.items():
                unique_defects = sorted(set(defects))
                defect_customer_data.append({
                    'customer': customer,
                    'defect_count': len(unique_defects),
                    'defects': ', '.join([f'[{d}]' for d in unique_defects])  # FIXED: Show all defects with brackets
                })
            
            # FIXED: Show ALL customers with defects
            defect_customer_df = pd.DataFrame(defect_customer_data).sort_values('defect_count', ascending=False)
            
            if not defect_customer_df.empty:
                table = doc.add_table(rows=len(defect_customer_df) + 1, cols=3)
                table.style = 'Light Grid Accent 1'
                
                hdr_cells = table.rows[0].cells
                hdr_cells[0].text = 'Customer Name'
                hdr_cells[1].text = 'Defect Count'
                hdr_cells[2].text = 'All Defect IDs'
                
                for cell in hdr_cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.bold = True
                
                # Use iterrows() for safer column name access
                for i, (idx, row) in enumerate(defect_customer_df.iterrows(), 1):
                    row_cells = table.rows[i].cells
                    row_cells[0].text = str(row.get('customer', 'Unknown'))
                    row_cells[1].text = str(row.get('defect_count', 0))
                    row_cells[2].text = str(row.get('defects', ''))  # FIXED: No truncation - show all defects
            else:
                doc.add_paragraph("No defect data available for grouping.")
            
            doc.add_paragraph()
    
    # === PSIRT VULNERABILITIES TABLE ===
    if vuln_count > 0:
        doc.add_heading('Security Vulnerabilities (CVEs & PSIRT Advisories)', level=2)
        cve_count = len(psirt_vulns.get("cve_ids", set()))
        psirt_count = len(psirt_vulns.get("psirt_advisories", set()))
        doc.add_paragraph(f'Total vulnerabilities identified: {vuln_count} ({cve_count} CVEs, {psirt_count} PSIRT advisories)')
        
        # Create vulnerabilities by customer table
        vulnerability_by_customer = psirt_vulns.get('vulnerability_by_customer', {})
        if vulnerability_by_customer:
            vuln_customer_data = []
            for customer, vulns in vulnerability_by_customer.items():
                unique_vulns = sorted(set(vulns))
                vuln_customer_data.append({
                    'customer': customer,
                    'vuln_count': len(unique_vulns),
                    'vulns': ', '.join([f'[{v}]' for v in unique_vulns])  # FIXED: Show all vulns with brackets
                })
            
            # FIXED: Show ALL customers with vulnerabilities
            vuln_customer_df = pd.DataFrame(vuln_customer_data).sort_values('vuln_count', ascending=False)
            
            if not vuln_customer_df.empty:
                table = doc.add_table(rows=len(vuln_customer_df) + 1, cols=3)
                table.style = 'Light Grid Accent 1'
                
                hdr_cells = table.rows[0].cells
                hdr_cells[0].text = 'Customer Name'
                hdr_cells[1].text = 'Vulnerability Count'
                hdr_cells[2].text = 'All Vulnerability IDs'
                
                for cell in hdr_cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.bold = True
                
                # Use iterrows() for safer column name access
                for i, (idx, row) in enumerate(vuln_customer_df.iterrows(), 1):
                    row_cells = table.rows[i].cells
                    row_cells[0].text = str(row.get('customer', 'Unknown'))
                    row_cells[1].text = str(row.get('vuln_count', 0))
                    row_cells[2].text = str(row.get('vulns', ''))  # FIXED: No truncation - show all vulns
            else:
                doc.add_paragraph("No vulnerability data available for grouping.")
            
            doc.add_paragraph()
    
    doc.add_page_break()
    
    # === CHARTS ===
    if chart_paths and len(chart_paths) > 0:
        doc.add_heading('Visual Analysis & Trends', level=1)
        
        logger.info(f"[[CHART]] Adding {len(chart_paths)} charts to report")
        charts_added = 0
        for chart_path in chart_paths:
            logger.info(f"[[CHART]] Checking chart: {chart_path}, exists: {os.path.exists(chart_path)}")
            if os.path.exists(chart_path):
                try:
                    # Add chart title based on filename
                    if 'support_cases_trend' in chart_path:
                        doc.add_heading('Support Cases Over Time', level=2)
                    elif 'severity_distribution' in chart_path:
                        doc.add_heading('Case Severity Distribution', level=2)
                    elif 'top_customers' in chart_path:
                        doc.add_heading('Top Customers by Case Volume', level=2)
                    elif 'arr_impact' in chart_path:
                        doc.add_heading('ARR Impact by Issue', level=2)
                    elif 'arr_distribution' in chart_path:
                        doc.add_heading('Customer ARR Distribution', level=2)
                    
                    doc.add_picture(chart_path, width=Inches(6.5))
                    doc.add_paragraph()  # Spacing
                    charts_added += 1
                    logger.info(f"[[OK]] Chart added: {chart_path}")
                except Exception as e:
                    logger.error(f"[[ERROR]] Failed to add chart {chart_path}: {e}")
        
        logger.info(f"[[OK]] Total charts added to report: {charts_added}")
        
        if charts_added == 0:
            doc.add_paragraph("Charts are being generated but not available yet.")
    else:
        logger.warning(f"[[WARNING]] No charts available to add. chart_paths: {chart_paths}")
    
    doc.add_page_break()
    
    # === FEATURE REQUESTS ===
    if feature_requests and feature_requests.get('total_requests', 0) > 0:
        doc.add_heading('Feature Requests from Customers', level=1)
        
        para = doc.add_paragraph()
        para.add_run(f"Total Feature Requests: ").bold = True
        para.add_run(f"{feature_requests['total_requests']}")
        
        if feature_requests.get('total_arr_impact', 0) > 0:
            arr_para = doc.add_paragraph()
            arr_para.add_run(f"Combined ARR Impact: ").bold = True
            arr_para.add_run(f"${feature_requests['total_arr_impact']:,.0f}")
        
        if feature_requests.get('top_features'):
            doc.add_heading('Most Requested Features', level=2)
            for theme, count in feature_requests['top_features']:
                doc.add_paragraph(f"{theme.title()}: {count} mentions", style='List Bullet')
        
        # FIXED: Show ALL requesting customers
        if feature_requests.get('customer_examples'):
            doc.add_heading('All Requesting Customers', level=2)
            for customer_info in feature_requests['customer_examples']:
                para = doc.add_paragraph()
                para.add_run(f"{customer_info['customer_name']}").bold = True
                para.add_run(f" - {customer_info['request_count']} requests")
                if customer_info.get('arr', 0) > 0:
                    para.add_run(f" (ARR: ${customer_info['arr']:,.0f})")
    
    # Footer
    doc.add_paragraph()
    footer = doc.add_paragraph(f"Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.runs[0].italic = True
    
    # Save
    output_path = f"{base_path}_enhanced.docx"
    doc.save(output_path)
    logger.info(f"[[OK]] Enhanced compact report created: {output_path}")
    return output_path

def _create_simple_fallback_report(base_path: str, manager: str, technology: str, days: int, ai_insights: Dict) -> str:
    """Create a simple fallback report when the main report generation fails"""
    try:
        from docx import Document
        from docx.shared import Inches
        
        logger.info(f"[[WRITE]] Creating simple fallback report with markdown parsing...")
        
        doc = Document()
        
        # Title
        title = doc.add_heading(f'AdoptIQ Portfolio Analysis - {manager}', 0)
        title.alignment = 1  # Center alignment
        
        # Subtitle
        subtitle = doc.add_heading(f'{technology} - {days} Day Analysis', level=1)
        subtitle.alignment = 1
        
        # Executive Summary - PARSE MARKDOWN!
        doc.add_heading('Executive Summary', level=1)
        summary_text = ai_insights.get('executive_summary', 'Analysis completed with AI-generated insights.')
        _parse_markdown_for_fallback(doc, summary_text)
        
        # Key Findings
        doc.add_heading('Key Findings', level=1)
        doc.add_paragraph("• Portfolio analysis completed successfully")
        doc.add_paragraph("• AI insights generated for strategic recommendations")
        doc.add_paragraph("• Report generated with fallback formatting due to timeout")
        
        # Recommendations
        doc.add_heading('Recommendations', level=1)
        doc.add_paragraph("• Review AI-generated insights for strategic guidance")
        doc.add_paragraph("• Schedule follow-up analysis if detailed formatting is required")
        doc.add_paragraph("• Consider running analysis during off-peak hours for full formatting")
        
        # Footer
        doc.add_paragraph("\n" + "="*50)
        doc.add_paragraph(f"Report generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        doc.add_paragraph("AdoptIQ Portfolio Analysis System")
        
        # Save the document
        output_path = f"{base_path}_fallback.docx"
        doc.save(output_path)
        
        logger.info(f"[[OK]] Fallback report created: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"[[ERROR]] Failed to create fallback report: {e}")
        # Return a simple text file as last resort
        output_path = f"{base_path}_fallback.txt"
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"AdoptIQ Portfolio Analysis - {manager}\n")
                f.write(f"{technology} - {days} Day Analysis\n\n")
                f.write("Executive Summary:\n")
                f.write(ai_insights.get('executive_summary', 'Analysis completed successfully.\n'))
                f.write(f"\nReport generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("AdoptIQ Portfolio Analysis System\n")
            logger.info(f"[[OK]] Text fallback report created: {output_path}")
            return output_path
        except Exception as e2:
            logger.error(f"[[ERROR]] Failed to create even text fallback: {e2}")
            return None

def _get_customer_specific_technology(customer: str, data: Dict) -> str:
    """Determine the specific technology for a customer based on their subscription data"""
    try:
        subscriptions = data.get('subscriptions', pd.DataFrame())
        if subscriptions.empty or 'PRODUCT_NAME' not in subscriptions.columns:
            return None
        
        # Get subscriptions for this customer
        customer_subscriptions = subscriptions[
            subscriptions.get('BU_NAME', pd.Series()) == customer
        ]
        
        if customer_subscriptions.empty:
            return None
        
        # Find the most specific contact center technology
        technologies_found = set()
        for _, sub in customer_subscriptions.iterrows():
            product_name = sub.get('PRODUCT_NAME', '')
            if pd.notna(product_name):
                tech = _categorize_technology(str(product_name))
                if tech in ['Webex Contact Center', 'Webex Contact Center Enterprise', 'Cisco UCCE', 'Cisco UCCX']:
                    technologies_found.add(tech)
        
        # Return the most specific technology found
        if 'Webex Contact Center Enterprise' in technologies_found:
            return 'Webex Contact Center Enterprise'
        elif 'Webex Contact Center' in technologies_found:
            return 'Webex Contact Center'
        elif 'Cisco UCCE' in technologies_found:
            return 'Cisco UCCE'
        elif 'Cisco UCCX' in technologies_found:
            return 'Cisco UCCX'
        elif technologies_found:
            return list(technologies_found)[0]  # Return any contact center tech found
        
        return None
        
    except Exception as e:
        logger.warning(f"Error determining technology for customer {customer}: {e}")
        return None

def _categorize_technology(product_name: str) -> str:
    """Categorize product name into technology groups"""
    product_lower = product_name.lower()
    
    if any(keyword in product_lower for keyword in ['webex meetings', 'meetings', 'meeting']):
        return 'Webex Meetings'
    elif any(keyword in product_lower for keyword in ['webex calling', 'calling', 'ucm']):
        return 'Webex Calling'
    # Specific contact center technology categorization
    elif any(keyword in product_lower for keyword in ['webex contact center enterprise', 'contact center enterprise']):
        return 'Webex Contact Center Enterprise'
    elif any(keyword in product_lower for keyword in ['webex contact center', 'wxcc', 'ccaas']) and 'enterprise' not in product_lower:
        return 'Webex Contact Center'
    elif any(keyword in product_lower for keyword in ['cisco ucce', 'ucce', 'unified contact center enterprise']):
        return 'Cisco UCCE'
    elif any(keyword in product_lower for keyword in ['cisco uccx', 'uccx', 'unified contact center express']):
        return 'Cisco UCCX'
    elif any(keyword in product_lower for keyword in ['contact center']):
        return 'Contact Center'
    elif any(keyword in product_lower for keyword in ['messaging', 'teams']):
        return 'Messaging'
    elif any(keyword in product_lower for keyword in ['device', 'endpoint', 'desk', 'phone', 'deskpro', 'desk pro']):
        return 'Devices'
    elif any(keyword in product_lower for keyword in ['video', 'room kit', 'roomkit', 'board', 'codec']):
        return 'Video'
    else:
        return 'Other'

def _generate_fallback_data_for_technology(technology, manager="Test Manager", customer_count=5):
    """Generate technology-specific fallback data"""
    
    # Technology-specific issues and scenarios
    tech_scenarios = {
        'Cisco UCCE': {
            'ab_issues': [
                'Call routing configuration complexity',
                'Agent desktop integration challenges', 
                'Reporting dashboard customization needs',
                'IVR script optimization requirements',
                'Workforce management integration gaps'
            ],
            'case_issues': [
                'Call quality degradation during peak hours',
                'Agent login authentication failures',
                'Real-time reporting data discrepancies',
                'CTI integration connectivity issues',
                'Database performance optimization needs'
            ]
        },
        'Webex Meetings & Messaging': {
            'ab_issues': [
                'Meeting room integration setup',
                'Mobile app adoption challenges',
                'Security policy configuration',
                'Recording storage management',
                'External guest access setup'
            ],
            'case_issues': [
                'Audio quality issues in large meetings',
                'Screen sharing performance problems',
                'Mobile app connectivity failures',
                'Recording playback synchronization',
                'Calendar integration conflicts'
            ]
        },
        'Webex Calling': {
            'ab_issues': [
                'Phone number porting delays',
                'Desk phone provisioning complexity',
                'Call forwarding rule configuration',
                'Voicemail transcription setup',
                'Emergency calling location accuracy'
            ],
            'case_issues': [
                'One-way audio in external calls',
                'Desk phone registration failures',
                'Call transfer functionality issues',
                'Voicemail delivery delays',
                'Emergency services routing problems'
            ]
        },
        'Webex Contact Center': {
            'ab_issues': [
                'Agent skill-based routing setup',
                'Customer journey mapping complexity',
                'Omnichannel integration challenges',
                'Real-time analytics configuration',
                'Supervisor dashboard customization'
            ],
            'case_issues': [
                'Queue overflow handling failures',
                'Chat bot integration errors',
                'Agent productivity metric discrepancies',
                'Customer callback functionality issues',
                'Supervisor monitoring tool problems'
            ]
        },
        'All Contact Center': {
            'ab_issues': [
                'Multi-platform contact center integration',
                'Agent skill-based routing across platforms',
                'Omnichannel customer journey mapping',
                'Unified reporting and analytics setup',
                'Cross-platform supervisor dashboard configuration',
                'Call routing complexity across UCCE/UCCX/WCC',
                'Agent desktop integration challenges',
                'IVR script optimization requirements'
            ],
            'case_issues': [
                'Cross-platform call quality degradation',
                'Agent login authentication failures',
                'Real-time reporting data discrepancies',
                'CTI integration connectivity issues',
                'Queue overflow handling failures',
                'Chat bot integration errors',
                'Agent productivity metric discrepancies',
                'Customer callback functionality issues'
            ]
        }
    }
    
    # Get technology-specific scenarios or use generic ones
    scenarios = tech_scenarios.get(technology, tech_scenarios['Cisco UCCE'])
    
    # Generate customer names
    customer_names = [f'Customer_{i+1}_{technology.replace(" ", "_")}' for i in range(customer_count)]
    
    # Generate AB data
    ab_data = pd.DataFrame({
        'ID': [f'AB_{1000 + i}' for i in range(customer_count)],
        'customer_name': customer_names,
        'title': scenarios['ab_issues'][:customer_count],
        'SEVERITY_C': ['Critical', 'High', 'Medium', 'High', 'Critical'][:customer_count],
        'AB_STATUS_C': ['Open', 'In Progress', 'Open', 'Resolved', 'Open'][:customer_count],
        'AB_CATEGORY_C': ['Technical Issues', 'User Training', 'Configuration', 'Integration', 'Performance'][:customer_count],
        'ab_category_final': ['Technical Issues', 'User Training', 'Configuration', 'Integration', 'Performance'][:customer_count],
        'sub_technology': [technology] * customer_count,
        'TITLE_C': scenarios['ab_issues'][:customer_count],
        'DESCRIPTION_C': [f'Detailed analysis needed for {issue}' for issue in scenarios['ab_issues'][:customer_count]],
        'ASSIGNEE_C': [f'CSM_{i+1}' for i in range(customer_count)],
        'CREATED_DATE': pd.date_range('2024-09-01', periods=customer_count, freq='7D')
    })
    
    # Generate CSOne data
    csone_data = pd.DataFrame({
        'ID': [f'CS_{2000 + i}' for i in range(customer_count)],
        'customer_name': customer_names,
        'title': scenarios['case_issues'][:customer_count],
        'sub_technology': [technology] * customer_count,
        'SR Number': [f'SR{1000000 + i}' for i in range(customer_count)],
        'Title': scenarios['case_issues'][:customer_count],
        'Description': [f'Technical investigation required for {issue}' for issue in scenarios['case_issues'][:customer_count]],
        'Severity': ['P2', 'P1', 'P3', 'P2', 'P1'][:customer_count],
        'Status': ['Open', 'In Progress', 'Resolved', 'Open', 'Escalated'][:customer_count],
        'Date/Time Opened': pd.date_range('2024-09-01', periods=customer_count, freq='5D')
    })
    
    return ab_data, csone_data

def run_compact_analysis(analysis_id):
    """Run compact analysis focused on renewal risk"""
    try:
        import os
        logger.info(f"========================================")
        logger.info(f"  NEW CODE LOADED - VERSION 11:55 AM")
        logger.info(f"  PID: {os.getpid()}")
        logger.info(f"========================================")
        logger.info(f"[[START]] Starting compact analysis: {analysis_id}")
        
        # Initialize variables that might not be set (prevents UnboundLocalError)
        arr_data = pd.DataFrame()
        arr_impact = {'total_arr': 0, 'at_risk_arr': 0, 'customers_analyzed': 0}
        chart_paths = []
        feature_requests = {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
        
        # Get analysis parameters
        with analysis_status_lock:
            status = analysis_status[analysis_id]
            manager = status['manager']
            technology = status['technology']
            days = status['days']
            csone_file = status['csone_file']
            
            # Debug logging for CSOne file
            logger.info(f"[[SEARCH]] DEBUGGING - Analysis status CSOne file: '{csone_file}'")
            logger.info(f"[[SEARCH]] DEBUGGING - Analysis status keys: {list(status.keys())}")
        
        # Update status immediately
        with analysis_status_lock:
            status['status'] = 'running'
            status['progress'] = 5
            status['message'] = ' Analysis started - initializing...'
            status['current_step'] = 'Initialization'
            status['step_start_time'] = datetime.now().isoformat()
            save_analysis_status()
        
        logger.info(f"[[OK]] Analysis status updated to running")
        
        # Update status for database connection
        with analysis_status_lock:
            status['progress'] = 10
            status['message'] = ' Connecting to Snowflake...'
            status['current_step'] = 'Database Connection'
            status['step_start_time'] = datetime.now().isoformat()
            save_analysis_status()
        
        # Connect to Snowflake with timeout and fallback
        ctx = None
        snowflake_timeout = False
        
        try:
            logger.info(f"[[LINK]] Attempting to connect to Snowflake with 30s timeout...")
            # Use ThreadPoolExecutor to implement timeout
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_connect_with_keeper)
                try:
                    ctx = future.result(timeout=30)  # 30 second timeout
                    logger.info(f"[[OK]] Successfully connected to Snowflake")
                except FutureTimeoutError:
                    logger.warning(f"[[TIMEOUT]] Snowflake connection timed out after 30 seconds")
                    snowflake_timeout = True
                    ctx = None
                except Exception as e:
                    logger.warning(f"[[ERROR]] Failed to connect to Snowflake: {e}")
                    ctx = None
                    
        except Exception as e:
            logger.warning(f"[[ERROR]] Snowflake connection error: {e}")
            ctx = None
        
        if ctx is None:
            error_msg = (
                "❌ CRITICAL: Snowflake database connection failed.\n\n"
                "Possible causes:\n"
                "- Network connectivity issues\n"
                "- Authentication/credential problems\n"
                "- Database server unavailability\n"
                "- Connection timeout (database not responding)\n\n"
                "Please check:\n"
                "1. Network connection to Snowflake\n"
                "2. Keeper credentials are valid\n"
                "3. Database server is online\n"
                "4. Firewall/proxy settings"
            )
            logger.error(f"[[ERROR]] {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Database connection failed - see logs for details'
                status['error'] = error_msg
                status['current_step'] = 'Connection Failed'
                save_analysis_status()
            return
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 20
            status['message'] = ' Fetching team data...'
            status['current_step'] = 'Team Data Retrieval'
        
        # CRITICAL FIX: Initialize unfiltered team_subs_df variable outside try block for scope
        # This ensures it's accessible throughout the function for customer counting
        team_subs_df_unfiltered = pd.DataFrame()
        
        # Get team subscriptions (only if we have a database connection)
        subscription_id_val = (status.get('subscription_id') or '').strip()
        customer_name_val = (status.get('customer_name') or '').strip()
        single_customer_mode = bool(subscription_id_val or customer_name_val)
        
        if ctx is not None:
            try:
                if single_customer_mode:
                    # Fetch directly by customer/subscription (manager not used)
                    if subscription_id_val:
                        sub_data = fetch_subscription_data(subscription_id_val, days)
                        if not sub_data or not sub_data.get('found'):
                            error_msg = f"❌ No subscription found with ID '{subscription_id_val}'."
                            logger.error(f"[[ERROR]] No subscription found with ID '{subscription_id_val}'.")
                            with analysis_status_lock:
                                status['status'] = 'error'
                                status['progress'] = 0
                                status['message'] = ' Subscription not found'
                                status['error'] = error_msg
                                status['current_step'] = 'Subscription Not Found'
                                save_analysis_status()
                            return
                        else:
                            cust_name = sub_data.get('customer_name') or 'Unknown'
                            acc_id = sub_data.get('account_id')
                            team_subs_df_unfiltered = pd.DataFrame({
                                'BU_NAME': [cust_name],
                                'ACCOUNT_ID_C': [acc_id] if acc_id else [None],
                                'SUBSCRIPTION_ID': [subscription_id_val],
                                'CSSM_EMAIL': [sub_data.get('cssm_email', '') or '']
                            })
                            team_subs_df = team_subs_df_unfiltered.copy()
                            logger.info(f"[[OK]] Compact single-subscription: {subscription_id_val} ({cust_name})")
                    else:
                        sub_results = search_subscriptions_by_customer(customer_name_val, limit=50)
                        if not sub_results:
                            error_msg = f"❌ No subscriptions found for customer '{customer_name_val}'."
                            logger.error(f"[[ERROR]] No subscriptions found for customer '{customer_name_val}'.")
                            with analysis_status_lock:
                                status['status'] = 'error'
                                status['progress'] = 0
                                status['message'] = ' Customer not found'
                                status['error'] = error_msg
                                status['current_step'] = 'Customer Not Found'
                                save_analysis_status()
                            return
                        else:
                            canonical_name = (sub_results[0].get('BU_NAME') or customer_name_val)
                            same_customer = [r for r in sub_results if (r.get('BU_NAME') or '') == canonical_name]
                            if not same_customer:
                                same_customer = [sub_results[0]]
                            team_subs_df_unfiltered = pd.DataFrame({
                                'BU_NAME': [r.get('BU_NAME') or canonical_name for r in same_customer],
                                'ACCOUNT_ID_C': [r.get('ACCOUNT_ID_C') for r in same_customer],
                                'SUBSCRIPTION_ID': [r.get('SUBSCRIPTION_ID') for r in same_customer],
                                'CSSM_EMAIL': [r.get('CSSM_EMAIL', '') for r in same_customer]
                            })
                            team_subs_df = team_subs_df_unfiltered.copy()
                            logger.info(f"[[OK]] Compact single-customer: '{canonical_name}' ({len(same_customer)} sub(s))")
                else:
                    logger.info(f"[[DEBUG]] Fetching team subscriptions with timeout...")
                    cssm_emails = [email for mgr, name, email in TEAM_ROSTER if mgr == manager or manager == "All Managers"]
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(get_subscriptions_for_team, ctx, cssm_emails)
                        try:
                            team_subs_df_unfiltered = future.result(timeout=30)  # 30 second timeout
                            logger.info(f"[[OK]] Retrieved {len(team_subs_df_unfiltered)} team subscriptions (UNFILTERED)")
                            has_customer_filter = bool(str(status.get('customer_name') or '').strip())
                            has_subscription_filter = bool(str(status.get('subscription_id') or '').strip())
                            if has_customer_filter or has_subscription_filter:
                                filtered_df, filter_error = filter_subscriptions_by_criteria(
                                    team_subs_df_unfiltered,
                                    customer_name=status.get('customer_name'),
                                    subscription_id=status.get('subscription_id')
                                )
                                if filter_error:
                                    logger.warning(f"[[FILTER]] {filter_error}")
                                    team_subs_df = pd.DataFrame()
                                else:
                                    team_subs_df = filtered_df
                                    logger.info(f"[[FILTER]] Filtered to {len(team_subs_df)} subscriptions")
                            else:
                                team_subs_df = team_subs_df_unfiltered.copy()
                                logger.info(f"[[FILTER]] No filter - using all {len(team_subs_df)} subscriptions")
                        except FutureTimeoutError:
                            logger.warning(f"[[TIMEOUT]] Team subscriptions query timed out after 30 seconds")
                            team_subs_df = pd.DataFrame()
                            team_subs_df_unfiltered = pd.DataFrame()
                        except Exception as e:
                            logger.warning(f"[[WARNING]] Failed to get team subscriptions: {e}")
                            team_subs_df = pd.DataFrame()
                            team_subs_df_unfiltered = pd.DataFrame()
            except Exception as e:
                logger.warning(f"[[WARNING]] Team subscriptions error: {e}")
                team_subs_df = pd.DataFrame()
                team_subs_df_unfiltered = pd.DataFrame()
        else:
            error_msg = (
                "❌ CRITICAL: Cannot retrieve team data - Snowflake connection required.\n\n"
                "The report cannot be generated without team subscription data."
            )
            logger.error(f"[[ERROR]] {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Cannot retrieve team data - database connection required'
                status['error'] = error_msg
                status['current_step'] = 'Data Retrieval Failed'
                save_analysis_status()
            return
        
        if team_subs_df.empty:
            error_msg = (
                f"❌ CRITICAL: No team subscription data found for manager '{manager}'.\n\n"
                "Possible causes:\n"
                "- Manager name not found in team roster\n"
                "- No subscriptions assigned to team members\n"
                "- Database query returned no results\n"
                "- Data filtering removed all records\n\n"
                "Please verify:\n"
                "1. Manager name is correct and exists in team roster\n"
                "2. Team members have subscriptions assigned\n"
                "3. Database contains subscription data for this team"
            )
            logger.error(f"[[ERROR]] {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' No team data found for manager - see logs for details'
                status['error'] = error_msg
                status['current_step'] = 'No Team Data'
                save_analysis_status()
            return
        else:
            account_ids = team_subs_df['ACCOUNT_ID_C'].dropna().unique().tolist()
            sub_ids = team_subs_df['SUBSCRIPTION_ID'].dropna().unique().tolist()
        
        # Only fetch real data if we have team subscriptions AND database connection
        if not team_subs_df.empty and ctx is not None:
            # Update status
            with analysis_status_lock:
                status['progress'] = 30
                status['message'] = ' Fetching adoption barriers...'
                status['current_step'] = 'Adoption Barriers Analysis'
            
            # Fetch ARR data for executive insights
            try:
                logger.info(f"[[DEBUG]] About to fetch ARR data for {len(account_ids)} accounts...")
                
                # Use timeout for ARR data query
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(fetch_arr_data, ctx, account_ids)
                    try:
                        arr_data = future.result(timeout=30)  # 30 second timeout
                        logger.info(f"[[DEBUG]] ARR data query completed, got {len(arr_data)} rows")
                    except FutureTimeoutError:
                        logger.warning(f"[[TIMEOUT]] ARR data query timed out after 30 seconds")
                        arr_data = pd.DataFrame()
                    except Exception as e:
                        logger.error(f"[[ERROR]] Failed to fetch ARR data: {e}")
                        arr_data = pd.DataFrame()
            except Exception as e:
                logger.error(f"[[ERROR]] ARR data fetch error: {e}")
                arr_data = pd.DataFrame()
            
            # Fetch adoption barriers
            try:
                logger.info(f"[[DEBUG]] About to fetch adoption barriers for {len(account_ids)} accounts...")
                logger.info(f"[[DEBUG]] Account IDs sample: {account_ids[:5] if len(account_ids) > 5 else account_ids}")
                logger.info(f"[[DEBUG]] Days parameter: {days}")
                
                # Use timeout for adoption barriers query (longer timeout since this query can be slow)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(fetch_adoption_barriers, ctx, account_ids, days)
                    try:
                        ab_raw = future.result(timeout=120)  # 2 minute timeout for slow queries
                        logger.info(f"[[DEBUG]] Raw adoption barriers query completed, got {len(ab_raw)} rows")
                    except FutureTimeoutError:
                        logger.warning(f"[[TIMEOUT]] Adoption barriers query timed out after 2 minutes")
                        ab_raw = pd.DataFrame()
                    except Exception as e:
                        logger.error(f"[[ERROR]] Failed to fetch adoption barriers: {e}")
                        ab_raw = pd.DataFrame()
                
                if not ab_raw.empty and "ACCOUNT_ID_C" in ab_raw.columns:
                    logger.info(f"[[DEBUG]] Merging adoption barriers with team data...")
                    ab_raw = ab_raw.merge(team_subs_df[["ACCOUNT_ID_C","BU_NAME","CSSM_EMAIL"]].drop_duplicates(), on="ACCOUNT_ID_C", how="left")
                    logger.info(f"[[DEBUG]] Merge completed, now applying scope filter...")
                
                ab_scoped = _apply_scope_filter_ab(ab_raw, technology, days)
                logger.info(f"[[DEBUG]] Scope filter applied, now preparing AB data...")
                ab_norm = _prepare_ab(ab_scoped, team_subs_df)
                logger.info(f"[[OK]] Retrieved {len(ab_norm)} adoption barriers")
                
                # Calculate ARR impact for executive insights
                if not ab_norm.empty and not arr_data.empty:
                    logger.info(f"[[DEBUG]] Calculating ARR impact for issues...")
                    arr_impact = calculate_arr_impact_for_issues(ab_norm, arr_data)
                    logger.info(f"[[OK]] ARR Impact Analysis: ${arr_impact['total_arr']:,.0f} total ARR at risk")
                else:
                    arr_impact = {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
            except Exception as e:
                logger.error(f"[[ERROR]] Failed to fetch adoption barriers: {e}")
                import traceback
                logger.error(f"[[ERROR]] Traceback: {traceback.format_exc()}")
                ab_norm = pd.DataFrame()
                arr_impact = {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
        else:
            # Initialize empty DataFrames if no database connection or no team data
            ab_norm = pd.DataFrame()
            arr_impact = {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
            arr_data = pd.DataFrame()
        
        # CRITICAL FIX: Initialize csone_df BEFORE trying to use it
        # This ensures it's always defined, even if CSOne file wasn't provided
        # NOTE: CSOne file processing happens later (after CSConsole data fetching)
        # We'll do enrichment/analysis AFTER CSOne file processing, not before
        csone_df = pd.DataFrame()  # Initialize as empty DataFrame - will be populated after CSOne file processing
        
        # NOTE: CSOne enrichment, feature request analysis, and chart generation
        # will be done AFTER CSOne file processing (see lines ~3482-3508)
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 35
            status['message'] = ' Fetching CSConsole data...'
            status['current_step'] = 'CSConsole Data Integration'
            
            # Fetch CSConsole data for compact analysis with timeout protection
        if not team_subs_df.empty and ctx is not None:
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
                
                def fetch_csconsole_data():
                    """Fetch CSConsole data in a separate thread"""
                    try:
                        logger.info(f"[[SEARCH]] Starting CSConsole data fetching...")
                        csconsole_action_plans = fetch_csconsole_action_plans(ctx, account_ids, days)
                        csconsole_customer_pulse = fetch_csconsole_customer_pulse(ctx, account_ids, days)
                        csconsole_success_priorities = fetch_csconsole_success_priorities(ctx, account_ids, days)
                        csconsole_adoption_barriers = fetch_csconsole_adoption_barriers(ctx, account_ids, days)
                        logger.info(f"[[OK]] Retrieved CSConsole data")
                        return csconsole_action_plans, csconsole_customer_pulse, csconsole_success_priorities, csconsole_adoption_barriers
                    except Exception as e:
                        logger.error(f"[[ERROR]] CSConsole data fetching failed: {e}")
                        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
                
                logger.info(f"[[TIME]] Starting CSConsole fetching with 90-second timeout...")
                
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(fetch_csconsole_data)
                    csconsole_action_plans, csconsole_customer_pulse, csconsole_success_priorities, csconsole_adoption_barriers = future.result(timeout=90)  # 90-second timeout for CSConsole
                    
            except FutureTimeoutError:
                logger.error(f"⏰ CSConsole data fetching timed out after 90 seconds")
                csconsole_action_plans = pd.DataFrame()
                csconsole_customer_pulse = pd.DataFrame()
                csconsole_success_priorities = pd.DataFrame()
                csconsole_adoption_barriers = pd.DataFrame()
            except Exception as e:
                logger.warning(f"[[WARNING]] Failed to fetch CSConsole data: {e}")
                csconsole_action_plans = pd.DataFrame()
                csconsole_customer_pulse = pd.DataFrame()
                csconsole_success_priorities = pd.DataFrame()
                csconsole_adoption_barriers = pd.DataFrame()
        else:
            # This should not happen if we validated above, but add safety check
            error_msg = (
                "❌ CRITICAL: Cannot fetch data - Snowflake connection required.\n\n"
                "The report cannot be generated without database access."
            )
            logger.error(f"[[ERROR]] {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Cannot fetch data - database connection required'
                status['error'] = error_msg
                status['current_step'] = 'Data Fetch Failed'
                save_analysis_status()
            return
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 40
            status['message'] = ' Processing CSOne data...'
            status['current_step'] = 'CSOne Data Processing'
        
        # Process CSOne data with timeout protection
        # NOTE: csone_df is already initialized above as empty DataFrame
        # This is just to ensure we have a fresh reference for processing
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            
            def process_csone_data(csone_file_path):
                """Process CSOne data in a separate thread"""
                try:
                    logger.info(f"[[LIST]] Starting CSOne data processing...")
                    logger.info(f"[[SEARCH]] DEBUGGING - CSOne file path: '{csone_file_path}'")
                    logger.info(f"[[SEARCH]] DEBUGGING - CSOne file exists: {os.path.exists(csone_file_path) if csone_file_path else False}")
                    logger.info(f"[[SEARCH]] DEBUGGING - CSOne file type: {type(csone_file_path)}")
                    
                    # Handle None or empty file path - try OneDrive folder (macro places reports daily)
                    if not csone_file_path or csone_file_path.strip() == '':
                        csone_file_path = get_latest_csone_from_folder()
                    if not csone_file_path or csone_file_path.strip() == '':
                        logger.info(f"[[LIST]] No CSOne file provided, using empty DataFrame")
                        return pd.DataFrame()
                    
                    # Check if we need to add the uploads directory path
                    if csone_file_path and not os.path.exists(csone_file_path):
                        # Try with uploads directory path (sanitize to prevent path traversal)
                        safe_csone_name = secure_filename(os.path.basename(csone_file_path))
                        uploads_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_csone_name) if safe_csone_name else None
                        if uploads_path:
                            logger.info(f"[[SEARCH]] DEBUGGING - Trying uploads path: '{uploads_path}'")
                            logger.info(f"[[SEARCH]] DEBUGGING - Uploads path exists: {os.path.exists(uploads_path)}")
                        if uploads_path and os.path.exists(uploads_path):
                            csone_file_path = uploads_path
                            logger.info(f"[[OK]] Found CSOne file at: {csone_file_path}")
                        else:
                            # List available files in uploads directory for debugging
                            try:
                                uploads_dir = app.config['UPLOAD_FOLDER']
                                if os.path.exists(uploads_dir):
                                    available_files = os.listdir(uploads_dir)
                                    logger.info(f"[[SEARCH]] DEBUGGING - Available files in uploads: {available_files}")
                                    logger.warning(f"[[WARNING]] CSOne file '{csone_file_path}' not found in uploads directory")
                                    
                                    # Try to find any .xlsx file as fallback
                                    xlsx_files = [f for f in available_files if f.lower().endswith('.xlsx')]
                                    if xlsx_files:
                                        fallback_file = xlsx_files[0]  # Use the first available xlsx file
                                        fallback_path = os.path.join(uploads_dir, fallback_file)
                                        logger.info(f"[[REFRESH]] Using fallback CSOne file: {fallback_path}")
                                        csone_file_path = fallback_path
                                    else:
                                        logger.warning(f"[[WARNING]] No .xlsx files found in uploads directory")
                                else:
                                    logger.error(f"[[ERROR]] Uploads directory does not exist: {uploads_dir}")
                            except Exception as e:
                                logger.error(f"[[ERROR]] Error listing uploads directory: {e}")
                    
                    if csone_file_path and os.path.exists(csone_file_path):
                        resolved = _resolve_csone_path_safe(csone_file_path)
                        if not resolved:
                            logger.warning(f"[[WARNING]] CSOne path outside allowed directories, skipping: {os.path.basename(csone_file_path)}")
                            return pd.DataFrame()
                        logger.info(f"[[FILE]] Loading CSOne file: {resolved}")
                        csone_df_raw = load_csone_excel(resolved)
                        csone_df_prepared = _prepare_csone(csone_df_raw, team_subs_df)
                        
                        # CRITICAL FIX: Use UNFILTERED team_subs_df for extracting customer names for CSOne filtering
                        # This ensures we get ALL customers for filtering, not just the filtered subset
                        # But still use filtered team_subs_df for merging/preparation
                        team_subs_for_names = team_subs_df_unfiltered if not team_subs_df_unfiltered.empty else team_subs_df
                        logger.info(f"[[FILTER]] Using {len(team_subs_for_names)} subscriptions (UNFILTERED: {not team_subs_df_unfiltered.empty}) for extracting customer names")
                        
                        # Safely get team customer names - handle empty team_subs_df
                        if not team_subs_for_names.empty and 'BU_NAME' in team_subs_for_names.columns:
                            team_customer_names = team_subs_for_names["BU_NAME"].dropna().unique().tolist()
                            logger.info(f"[[FILTER]] Extracted {len(team_customer_names)} customer names from team subscriptions for CSOne filtering")
                        else:
                            team_customer_names = []
                            logger.info(f"[[INFO]] No team data available, using inclusive filtering for CSOne")
                        
                        # For compact analysis, be more inclusive with CSOne filtering
                        # Try strict filtering first, then fall back to more inclusive approach
                        csone_df = _apply_scope_filter_csone(csone_df_prepared, technology, days, sub_ids, team_customer_names)
                        
                        # If no cases found with strict filtering, try more inclusive approach for executive insights
                        if len(csone_df) == 0:
                            logger.info(f"[[REFRESH]] No cases found with strict filtering, trying inclusive approach for executive insights")
                            # Apply technology filter only, without team customer name filtering
                            csone_df = _apply_scope_filter_csone_inclusive(csone_df_prepared, technology, days)
                            logger.info(f"[[DATA]] Inclusive filtering found {len(csone_df)} cases for executive analysis")
                        logger.info(f"[[OK]] CSOne data processed successfully: {len(csone_df)} records")
                        return csone_df
                    elif team_subs_df.empty:
                        # No team data and no file - return empty (csone_df was never set in this path)
                        logger.info(f"[[REFRESH]] No team data and no CSOne file - using empty DataFrame")
                        return pd.DataFrame()
                    else:
                        logger.info(f"[[LIST]] No CSOne file provided, using empty DataFrame")
                        return pd.DataFrame()
                except Exception as e:
                    logger.error(f"[[ERROR]] CSOne data processing failed: {e}")
                    return pd.DataFrame()
            
            logger.info(f"[[TIME]] Starting CSOne processing with 60-second timeout...")
            
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(process_csone_data, csone_file)
                csone_df = future.result(timeout=60)  # 60-second timeout for CSOne processing
                
                if csone_df is None:
                    csone_df = pd.DataFrame()
                    logger.warning(f"[[WARNING]] CSOne processing returned None, using empty DataFrame")
                    
        except FutureTimeoutError:
            logger.error(f"⏰ CSOne data processing timed out after 60 seconds")
            csone_df = pd.DataFrame()
        except Exception as e:
            logger.error(f"[[ERROR]] CSOne data processing failed: {e}")
            csone_df = pd.DataFrame()
        
        # CRITICAL FIX: Now enrich CSOne data with ARR AFTER processing
        # This ensures csone_df is populated before enrichment
        try:
            if csone_df is not None and not csone_df.empty and 'arr_data' in locals() and arr_data is not None and not arr_data.empty:
                logger.info(f"[[DEBUG]] Enriching CSOne data with ARR information...")
                csone_df = enrich_csone_with_arr(csone_df, arr_data)
                logger.info(f"[[OK]] CSOne data enriched with ARR")
            else:
                logger.info(f"[[DEBUG]] Skipping CSOne ARR enrichment - no CSOne data or ARR data available")
        except Exception as e:
            logger.error(f"[[ERROR]] CSOne ARR enrichment failed: {e}")
            # Keep csone_df as-is if enrichment fails
            if csone_df is None:
                csone_df = pd.DataFrame()
        
        # Analyze feature requests (only if csone_df exists and is not empty)
        try:
            if csone_df is not None and not csone_df.empty:
                logger.info(f"[[DEBUG]] Analyzing feature requests...")
                feature_requests = analyze_feature_requests(csone_df, arr_data if 'arr_data' in locals() else pd.DataFrame())
                logger.info(f"[[OK]] Found {feature_requests['total_requests']} feature requests")
            else:
                logger.info(f"[[DEBUG]] Skipping feature request analysis - no CSOne data available")
                feature_requests = {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
        except Exception as e:
            logger.error(f"[[ERROR]] Feature request analysis failed: {e}")
            feature_requests = {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
        
        # Generate executive charts (always try, even if csone_df is empty)
        try:
            logger.info(f"[[DEBUG]] Generating executive charts...")
            chart_paths = create_executive_charts(
                ab_norm, 
                arr_data if 'arr_data' in locals() else pd.DataFrame(), 
                arr_impact if 'arr_impact' in locals() else {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}, 
                csone_df if csone_df is not None else pd.DataFrame(), 
                feature_requests if 'feature_requests' in locals() else {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
            )
            logger.info(f"[[OK]] Generated {len(chart_paths)} charts")
        except Exception as e:
            logger.error(f"[[ERROR]] Chart generation failed: {e}")
            import traceback
            logger.error(f"[[ERROR]] Traceback: {traceback.format_exc()}")
            chart_paths = []
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 55
            status['message'] = '[AI] Generating AI insights...'
            status['current_step'] = 'AI Analysis'
            save_analysis_status()  # Save status more frequently
        
        logger.info(f"[[SEARCH]] DEBUGGING - Starting AI Analysis step...")
        
        # External intelligence gathering (required for briefing book)
        logger.info(f"[[WEB]] Gathering external intelligence...")
        try:
            ext_bugs = fetch_help_webex_bugs()
            ext_incidents = fetch_status_incidents()
            matches = []
            matched_df = pd.DataFrame()
            logger.info(f"[[OK]] External intelligence gathered successfully")
        except Exception as e:
            logger.warning(f"[[WARNING]] External intelligence gathering failed: {e}")
            ext_bugs = []
            ext_incidents = []
            matches = []
            matched_df = pd.DataFrame()
        
        # Extract software defects (BST/CSC IDs) and PSIRT vulnerabilities from data
        logger.info(f"[[DEFECTS]] Extracting software defects and PSIRT vulnerabilities...")
        try:
            software_defects = extract_software_defects(csone_df, ab_norm) if not csone_df.empty else {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}}
            psirt_vulns = extract_psirt_vulnerabilities(csone_df, ab_norm) if not csone_df.empty else {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}}
            logger.info(f"[[OK]] Extracted {software_defects.get('total_defects', 0)} defects and {psirt_vulns.get('total_vulnerabilities', 0)} vulnerabilities")
        except Exception as e:
            logger.warning(f"[[WARNING]] Defect/vulnerability extraction failed: {e}")
            software_defects = {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}}
            psirt_vulns = {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}}
        
        # Generate AI insights
        logger.info(f"[[SEARCH]] DEBUGGING - Starting AI Analysis step...")
        
        # For compact analysis, create a more focused briefing book with available data
        if not ab_norm.empty:
            logger.info(f"[[DATA]] Creating executive summary from {len(ab_norm)} adoption barriers and {len(csone_df)} CSOne cases")
            # Create a simplified briefing book focusing on adoption barriers for executive insights
            # Include CSOne data summary and optional enrichment (ARR, feature requests, defects, PSIRT)
            briefing_book = _create_executive_briefing_book_with_csone(
                manager, ab_norm, csone_df, team_subs_df, technology,
                arr_data=arr_data if 'arr_data' in locals() else None,
                arr_impact=arr_impact if 'arr_impact' in locals() else None,
                feature_requests=feature_requests if 'feature_requests' in locals() else None,
                software_defects=software_defects if 'software_defects' in locals() else None,
                psirt_vulns=psirt_vulns if 'psirt_vulns' in locals() else None,
            )
        else:
            # Create minimal briefing book from whatever data we have
            logger.info(f"[[DATA]] Limited data available - creating minimal briefing book")
            briefing_book = _create_minimal_briefing_book(manager, ab_norm, team_subs_df, technology)
        
        # Use a more focused prompt for compact executive analysis
        ai_prompt = PROMPT_COMPACT_EXECUTIVE_TEMPLATE.format(MANAGER=manager, TECHNOLOGY=technology, data=briefing_book)
        
        logger.info(f"[[AI]] DEBUGGING - About to call AI with prompt length: {len(ai_prompt)} chars")
        logger.info(f"[[AI]] DEBUGGING - Briefing book length: {len(briefing_book)} chars")
        
        # Generate AI insights with timeout and cancellation support
        try:
            # Check for cancellation before starting AI analysis
            with analysis_status_lock:
                if analysis_status[analysis_id]['status'] == 'cancelled':
                    logger.info(f" Analysis cancelled before AI insights generation")
                    return
            
            # Update status with more detailed progress
            with analysis_status_lock:
                status['message'] = '[AI] Generating AI insights (this may take up to 60 seconds)...'
                status['current_step'] = 'AI Analysis - CircuIT Processing'
                save_analysis_status()
            
            logger.info(f"[[AI]] Starting AI analysis with timeout protection...")
            ai_insights_raw = generate_llm_response(ai_prompt, briefing_book)
            logger.info(f"[[AI]] AI insights generated: {type(ai_insights_raw)}")
            logger.info(f"[[AI]] AI insights content: {str(ai_insights_raw)[:500]}...")
            
            # Check for cancellation after AI call
            with analysis_status_lock:
                if analysis_status[analysis_id]['status'] == 'cancelled':
                    logger.info(f" Analysis cancelled after AI insights generation")
                    return
            
            # Convert string response to expected format for compact report formatter
            if isinstance(ai_insights_raw, str) and ai_insights_raw and not ai_insights_raw.startswith("ERROR:") and len(ai_insights_raw.strip()) > 50:
                logger.info(f"[[OK]] AI insights successfully generated: {len(ai_insights_raw)} characters")
                ai_insights = {
                    'portfolio_summary': {
                        'executive_summary': ai_insights_raw
                    },
                    'executive_summary': ai_insights_raw,
                    'raw_response': ai_insights_raw
                }
            else:
                logger.warning(f"[[WARNING]] AI insights generation failed or returned insufficient content: {ai_insights_raw}")
                # Generate comprehensive fallback insights based on actual data
                fallback_insights = _generate_comprehensive_fallback_insights(ab_norm, csone_df, manager, technology)
                logger.info(f"[[REFRESH]] Generated fallback insights: {len(fallback_insights)} characters")
                
                ai_insights = {
                    'portfolio_summary': {
                        'executive_summary': fallback_insights
                    },
                    'executive_summary': fallback_insights,
                    'raw_response': ai_insights_raw or 'No AI response received'
                }
        except Exception as ai_error:
            logger.error(f"[[ERROR]] AI analysis failed: {ai_error}")
            
            # Check for cancellation after error
            with analysis_status_lock:
                if analysis_status[analysis_id]['status'] == 'cancelled':
                    logger.info(f" Analysis cancelled after AI error")
                    return
            
            # Generate comprehensive fallback insights based on actual data
            fallback_insights = _generate_comprehensive_fallback_insights(ab_norm, csone_df, manager, technology)
            logger.info(f"[[REFRESH]] Generated fallback insights: {len(fallback_insights)} characters")
            
            ai_insights = {
                'portfolio_summary': {
                    'executive_summary': fallback_insights
                },
                'executive_summary': fallback_insights,
                'raw_response': f'AI analysis failed: {str(ai_error)}'
            }
            
        logger.info(f"[[SEARCH]] DEBUGGING - AI insights processing completed, updating status...")
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 75
            status['message'] = ' Creating compact report...'
            status['current_step'] = 'Compact Report Generation'
        
        # Create compact report with timeout protection
        ts = time.strftime("%Y%m%d_%H%M%S")
        tag = f"{manager.replace(' ','_')}_{technology.replace(' ','_').replace('&','and')}_{days}d_{ts}"
        out_dir = _ensure_outputs()
        base = str(out_dir / f"AdoptIQ_Report_Compact_{tag}")
        
        logger.info(f"[[WRITE]] Creating Word report: {base}.docx")
        logger.info(f"[[DATA]] Data summary - AB: {len(ab_norm)} rows, CSOne: {len(csone_df)} rows")
        
        # COMPREHENSIVE DEBUGGING - Log actual data content
        logger.info(f"[[SEARCH]] DEBUGGING - AB Data Sample:")
        if not ab_norm.empty:
            logger.info(f"   - AB Columns: {list(ab_norm.columns)}")
            logger.info(f"   - AB Sample (first 3 rows): {ab_norm.head(3).to_dict('records')}")
        else:
            logger.warning(f"   - AB Data is EMPTY - This will cause validation to fail")
            
        logger.info(f"[[SEARCH]] DEBUGGING - CSOne Data Sample:")
        if not csone_df.empty:
            logger.info(f"   - CSOne Columns: {list(csone_df.columns)}")
            logger.info(f"   - CSOne Sample (first 3 rows): {csone_df.head(3).to_dict('records')}")
        else:
            logger.warning(f"   - CSOne Data is EMPTY - This will cause validation to fail")
        
        # Validate data sources before report generation
        logger.info(f"[[VALIDATION]] Validating data sources for compact report...")
        try:
            raise_validation_error_if_invalid(
                report_type='compact',
                snowflake_ctx=ctx,
                team_subs_df=team_subs_df,
                ab_data=ab_norm,
                csone_data=csone_df,
                csconsole_action_plans=csconsole_action_plans if 'csconsole_action_plans' in locals() else None,
                csconsole_customer_pulse=csconsole_customer_pulse if 'csconsole_customer_pulse' in locals() else None,
                csconsole_success_priorities=csconsole_success_priorities if 'csconsole_success_priorities' in locals() else None,
                arr_data=arr_data
            )
            logger.info(f"[[VALIDATION]] All required data sources validated successfully")
        except DataSourceValidationError as e:
            error_msg = str(e)
            logger.error(f"[[VALIDATION]] Data validation failed: {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Data validation failed - see logs for details'
                status['error'] = error_msg
                status['current_step'] = 'Validation Failed'
                save_analysis_status()
            return
        
        # Check for cancellation before report generation
        with analysis_status_lock:
            if analysis_status[analysis_id]['status'] == 'cancelled':
                logger.info(f" Analysis cancelled before report generation")
                return
        
        # CRITICAL FIX: Calculate customer count BEFORE nested function to ensure we use UNFILTERED data
        # This ensures the customer count is calculated correctly regardless of nested function scope issues
        logger.info(f"[CUSTOMER_COUNT] Pre-calculating customer count using UNFILTERED team_subs_df...")
        logger.info(f"[CUSTOMER_COUNT] team_subs_df_unfiltered has {len(team_subs_df_unfiltered)} rows")
        logger.info(f"[CUSTOMER_COUNT] team_subs_df (filtered) has {len(team_subs_df)} rows")
        
        # Calculate the correct customer count using unfiltered data BEFORE the nested function
        # This ensures we have the right count even if nested function scope has issues
        team_subs_for_customer_counting = team_subs_df_unfiltered if not team_subs_df_unfiltered.empty else team_subs_df
        logger.info(f"[CUSTOMER_COUNT] Will use {len(team_subs_for_customer_counting)} subscriptions for customer counting")
        
        # Generate report with timeout protection
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            
            def generate_report():
                """Generate the Executive Intelligence Report in a separate thread"""
                try:
                    logger.info(f"[EXEC-REPORT] === STARTING EXECUTIVE INTELLIGENCE REPORT ===")
                    logger.info(f"[EXEC-REPORT] Step 1/5: Calculating risk scores...")
                    
                    # Calculate risk scores and summary
                    risk_scores = calculate_renewal_risk_scores(ab_norm, csone_df)
                    logger.info(f"[EXEC-REPORT] Risk scores calculated: {len(risk_scores)} customers (NOTE: This only counts customers with barriers/cases)")
                    
                    logger.info(f"[EXEC-REPORT] Step 2/5: Building risk summary...")
                    # Create risk summary
                    high_risk_customers = {}
                    moderate_risk_customers = {}
                    scores = []
                    
                    for k, v in risk_scores.items():
                        if isinstance(v, dict) and 'score' in v:
                            score = v['score']
                            scores.append(score)
                            if score >= 6:
                                high_risk_customers[k] = v
                            elif 4 <= score < 6:
                                moderate_risk_customers[k] = v
                    
                    overall_risk_score = np.mean(scores) if scores else 0
                    logger.info(f"[EXEC-REPORT] High-risk customers: {len(high_risk_customers)}, Overall score: {overall_risk_score:.1f}")
                    
                    logger.info(f"[EXEC-REPORT] Step 3/5: Creating risk summary structure...")
                    # NOTE: risk_summary['total_customers'] is NOT used for dashboard - dashboard calculates its own count
                    risk_summary = {
                        'overall_risk_score': round(overall_risk_score, 1),
                        'high_risk_customers': len(high_risk_customers),
                        'moderate_risk_customers': len(moderate_risk_customers),
                        'total_customers': len(risk_scores),  # This is only for risk summary, NOT dashboard
                        'critical_adoption_barriers': len(ab_norm[
                            ab_norm['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)
                        ]) if not ab_norm.empty and 'SEVERITY_C' in ab_norm.columns else 0,
                        'escalated_cases': len(csone_df[
                            csone_df['Severity'].astype(str).str.contains('P1|P2|Critical', case=False, na=False)
                        ]) if not csone_df.empty and 'Severity' in csone_df.columns else 0
                    }
                    logger.info(f"[EXEC-REPORT] Risk summary: {risk_summary}")
                    logger.info(f"[EXEC-REPORT] WARNING: risk_summary['total_customers']={risk_summary['total_customers']} is NOT used for dashboard - dashboard calculates its own count")
                    
                    logger.info(f"[EXEC-REPORT] Step 4/5: Validating data columns...")
                    logger.info(f"[EXEC-REPORT] AB columns: {list(ab_norm.columns) if not ab_norm.empty else 'EMPTY'}")
                    logger.info(f"[EXEC-REPORT] CSOne columns: {list(csone_df.columns) if not csone_df.empty else 'EMPTY'}")
                    
                    logger.info(f"[EXEC-REPORT] Step 5/5: Calling create_executive_intelligence_report...")
                    logger.info(f"[EXEC-REPORT] Output path: {base}.docx")
                    logger.info(f"[EXEC-REPORT] CRITICAL: About to pass team_subs_for_customer_counting with {len(team_subs_for_customer_counting)} rows")
                    
                    # Generate Executive Intelligence Report with ARR analysis
                    if EXECUTIVE_FORMATTER_AVAILABLE and create_executive_intelligence_report:
                        # CRITICAL FIX: Use pre-calculated team_subs_for_customer_counting (UNFILTERED)
                        # This variable is calculated in outer scope before nested function, ensuring correct data
                        logger.info(f"[EXEC-REPORT] Using team_subs_for_customer_counting: {len(team_subs_for_customer_counting)} subscriptions (UNFILTERED: {not team_subs_df_unfiltered.empty})")
                        team_subs_for_counting = team_subs_for_customer_counting
                        
                        result = create_executive_intelligence_report(
                            analysis_id, manager, technology, days,
                            ab_norm, csone_df, ai_insights, 
                            ext_bugs, ext_incidents,
                            risk_scores, risk_summary, 
                            f"{base}.docx",
                            arr_data=arr_data,
                            arr_impact=arr_impact,
                            chart_paths=chart_paths,
                            feature_requests=feature_requests,
                            team_subs_df=team_subs_for_counting,  # Use UNFILTERED for customer counting
                            csconsole_action_plans=csconsole_action_plans if 'csconsole_action_plans' in locals() else pd.DataFrame(),
                            csconsole_customer_pulse=csconsole_customer_pulse if 'csconsole_customer_pulse' in locals() else pd.DataFrame(),
                            csconsole_success_priorities=csconsole_success_priorities if 'csconsole_success_priorities' in locals() else pd.DataFrame(),
                            csconsole_adoption_barriers=csconsole_adoption_barriers if 'csconsole_adoption_barriers' in locals() else pd.DataFrame(),
                            software_defects=software_defects if 'software_defects' in locals() else {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}},
                            psirt_vulns=psirt_vulns if 'psirt_vulns' in locals() else {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}}
                        )
                    else:
                        logger.warning("[EXEC-REPORT] Executive formatter not available, using enhanced compact report fallback")
                        result = _create_enhanced_compact_report(
                            base, manager, technology, days, ai_insights,
                            csone_df, ab_norm, arr_data, arr_impact, chart_paths, feature_requests,
                            team_subs_df=team_subs_for_counting,
                            csconsole_action_plans=csconsole_action_plans if 'csconsole_action_plans' in locals() else pd.DataFrame(),
                            csconsole_customer_pulse=csconsole_customer_pulse if 'csconsole_customer_pulse' in locals() else pd.DataFrame(),
                            csconsole_success_priorities=csconsole_success_priorities if 'csconsole_success_priorities' in locals() else pd.DataFrame(),
                            csconsole_adoption_barriers=csconsole_adoption_barriers if 'csconsole_adoption_barriers' in locals() else pd.DataFrame()
                        )
                    
                    logger.info(f"[EXEC-REPORT] Report creation returned: {result}")
                    logger.info(f"[EXEC-REPORT] === EXECUTIVE INTELLIGENCE REPORT COMPLETE ===")
                    return result
                except Exception as inner_e:
                    logger.error(f"[EXEC-REPORT] Inner report error: {inner_e}")
                    raise
        except Exception as e:
            logger.error(f"[EXEC-REPORT] *** CRITICAL ERROR ***")
            logger.error(f"[EXEC-REPORT] Error type: {type(e).__name__}")
            logger.error(f"[EXEC-REPORT] Error message: {str(e)}")
            import traceback
            logger.error(f"[EXEC-REPORT] Full traceback:\n{traceback.format_exc()}")
            logger.error(f"[EXEC-REPORT] *** END ERROR ***")
            return None
        try:
            # Execute the report generation function
            logger.info(f"[EXEC-REPORT] Starting report generation (no timeout on formatting)...")
            result = generate_report()
            if result:
                logger.info(f"[EXEC-REPORT] SUCCESS: Report created: {result}")
                exec_report_path = result  # Use the result from generate_report()
            else:
                # Fallback to enhanced compact report if executive report fails
                logger.warning(f"[EXEC-REPORT] Executive report failed, using enhanced compact report...")
                # CRITICAL FIX: Use pre-calculated team_subs_for_customer_counting (UNFILTERED)
                # This ensures consistent customer counting in fallback path too
                logger.info(f"[EXEC-REPORT] Fallback: Using team_subs_for_customer_counting: {len(team_subs_for_customer_counting)} subscriptions (UNFILTERED: {not team_subs_df_unfiltered.empty})")
                team_subs_for_counting = team_subs_for_customer_counting
                exec_report_path = _create_enhanced_compact_report(
                    base, manager, technology, days, ai_insights,
                    csone_df, ab_norm, arr_data, arr_impact, chart_paths, feature_requests,
                    team_subs_df=team_subs_for_counting,  # Use UNFILTERED for customer counting
                    csconsole_action_plans=csconsole_action_plans if 'csconsole_action_plans' in locals() else pd.DataFrame(),
                    csconsole_customer_pulse=csconsole_customer_pulse if 'csconsole_customer_pulse' in locals() else pd.DataFrame(),
                    csconsole_success_priorities=csconsole_success_priorities if 'csconsole_success_priorities' in locals() else pd.DataFrame(),
                    csconsole_adoption_barriers=csconsole_adoption_barriers if 'csconsole_adoption_barriers' in locals() else pd.DataFrame()
                )
                logger.info(f"[EXEC-REPORT] SUCCESS: Enhanced Compact Report created: {exec_report_path}")
        except Exception as e:
            error_msg = (
                f"❌ CRITICAL ERROR during report generation:\n\n"
                f"Error Type: {type(e).__name__}\n"
                f"Error Message: {str(e)}\n\n"
                "This indicates a problem with the report generation process.\n"
                "Please check the logs for detailed traceback information."
            )
            logger.error(f"[EXEC-REPORT] EXCEPTION CAUGHT: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"[EXEC-REPORT] Exception traceback:\n{traceback.format_exc()}")
            logger.error(f"[EXEC-REPORT] Report generation failed - NOT creating fallback report")
            
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Report generation failed - see logs for details'
                status['error'] = error_msg
                status['current_step'] = 'Report Generation Failed'
                save_analysis_status()
            return
        
        logger.info(f"[[OK]] Executive Intelligence Report created: {exec_report_path}")
        logger.info(f"[[FILE]] File exists: {os.path.exists(exec_report_path)}")
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 90
            status['message'] = ' Creating Excel summary...'
            status['current_step'] = 'Excel Report Generation'
        
        # Create Excel summary with timeout protection
        excel_path = f"{base}.xlsx"
        logger.info(f"[[DATA]] Creating Excel file: {excel_path}")
        
        # Check for cancellation before Excel generation
        with analysis_status_lock:
            if analysis_status[analysis_id]['status'] == 'cancelled':
                logger.info(f" Analysis cancelled before Excel generation")
                return
        
        # Generate Excel with timeout protection
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            
            def generate_excel():
                """Generate the Excel report in a separate thread"""
                try:
                    logger.info(" Calculating risk scores...")
                    risk_scores = calculate_renewal_risk_scores(ab_norm, csone_df)
                    logger.info(f"[[CHART]] Risk scores calculated for {len(risk_scores)} customers")
                    return risk_scores
                except Exception as e:
                    logger.error(f"[[ERROR]] Risk score calculation failed: {e}")
                    return {}
            
            logger.info(f"[[TIME]] Starting Excel generation with 60-second timeout...")
            
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(generate_excel)
                risk_scores = future.result(timeout=60)  # 60-second timeout for Excel generation
                
                if not risk_scores:
                    logger.warning(f"[[WARNING]] Risk scores calculation failed, using empty dict")
                    risk_scores = {}
                    
        except FutureTimeoutError:
            logger.error(f"⏰ Excel generation timed out after 60 seconds")
            risk_scores = {}
        except Exception as e:
            logger.error(f"[[ERROR]] Excel generation failed: {e}")
            risk_scores = {}
        
        # Create risk summary DataFrame
        logger.info(f"[[LIST]] Creating risk summary DataFrame...")
        risk_summary_data = []
        for customer, risk_data in risk_scores.items():
            # Extract score from risk_data dictionary
            if isinstance(risk_data, dict) and 'score' in risk_data:
                score = risk_data['score']
            else:
                logger.warning(f"Invalid risk data structure for customer {customer}: {risk_data}")
                score = 0
            
            risk_level = "HIGH" if score >= 7 else "MODERATE" if score >= 4 else "LOW"
            risk_summary_data.append({
                'Customer': customer,
                'Risk_Score': round(score, 1),
                'Risk_Level': risk_level,
                'Adoption_Barriers': len(ab_norm[ab_norm['customer_name'] == customer]) if not ab_norm.empty else 0,
                'Support_Cases': len(csone_df[csone_df['customer_name'] == customer]) if not csone_df.empty else 0
            })
        
        risk_summary_df = pd.DataFrame(risk_summary_data)
        logger.info(f"[[DATA]] Risk summary DataFrame created with {len(risk_summary_df)} rows")
        
        # Create high-risk customers DataFrame
        high_risk_customers = risk_summary_df[risk_summary_df['Risk_Score'] >= 6].sort_values('Risk_Score', ascending=False)
        logger.info(f"[[WARNING]] High-risk customers identified: {len(high_risk_customers)}")
        
        logger.info(f"[[LIST]] Preparing enhanced Excel sheets...")
        
        # Calculate risk summary metrics
        total_customers = len(ab_norm['customer_name'].unique()) if not ab_norm.empty else 0
        high_risk_count = len(high_risk_customers)
        
        # Safely calculate critical adoption barriers
        if not ab_norm.empty and 'SEVERITY_C' in ab_norm.columns:
            critical_abs = len(ab_norm[ab_norm['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)])
        else:
            critical_abs = 0
            
        # Safely calculate escalated cases
        if not csone_df.empty and 'Severity' in csone_df.columns:
            escalated_cases = len(csone_df[csone_df['Severity'].astype(str).str.contains('P1|P2|Critical', case=False, na=False)])
        else:
            escalated_cases = 0
            
        overall_risk_score = risk_summary_df['Risk_Score'].mean() if not risk_summary_df.empty else 0
        
        # Create executive dashboard sheet
        dashboard_data = {
            'Metric': [
                'Total Customers Analyzed',
                'High-Risk Customers',
                'Critical Adoption Barriers',
                'Escalated Support Cases',
                'Overall Risk Score',
                'Analysis Period (Days)',
                'Technology Focus',
                'Manager',
                'Report Generated'
            ],
            'Value': [
                total_customers,
                high_risk_count,
                critical_abs,
                escalated_cases,
                f"{overall_risk_score:.1f}/10",
                days,
                technology,
                manager,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ],
            'Status': [
                ' Portfolio Overview',
                '[CRITICAL] Immediate Attention' if high_risk_count > 0 else '[OK] Under Control',
                '[WARNING] Monitor Closely' if critical_abs > 0 else '[OK] No Critical Issues',
                '[ALERT] Escalation Required' if escalated_cases > 0 else '[OK] Normal Operations',
                '[HIGH] High Risk' if overall_risk_score >= 7 else '[MOD] Moderate Risk' if overall_risk_score >= 4 else '[LOW] Low Risk',
                '[TIME] Analysis Period',
                '[TECH] Technology Focus',
                '[MGR] Portfolio Manager',
                '[TIMESTAMP] Report Timestamp'
            ]
        }
        dashboard_df = pd.DataFrame(dashboard_data)
        
        # Debug data availability
        logger.info(f"[[DATA]] Data availability check:")
        logger.info(f"   - AB data: {len(ab_norm)} rows, columns: {list(ab_norm.columns) if not ab_norm.empty else 'empty'}")
        logger.info(f"   - CSOne data: {len(csone_df)} rows, columns: {list(csone_df.columns) if not csone_df.empty else 'empty'}")
        logger.info(f"   - Action Plans: {len(csconsole_action_plans)} rows")
        logger.info(f"   - Customer Pulse: {len(csconsole_customer_pulse)} rows")
        
        # CSConsole data is optional - log if empty but don't generate fallback
        if csconsole_action_plans.empty:
            logger.warning(f"[[WARNING]] CSConsole Action Plans data is empty (optional data source)")
            
        if csconsole_customer_pulse.empty:
            logger.warning(f"[[WARNING]] CSConsole Customer Pulse data is empty (optional data source)")

        # Create more robust data filtering with better fallbacks
        critical_abs = pd.DataFrame()
        if not ab_norm.empty:
            # Try different column names for severity
            severity_cols = [col for col in ab_norm.columns if 'severity' in col.lower() or 'sev' in col.lower()]
            if severity_cols:
                severity_col = severity_cols[0]
                critical_abs = ab_norm[ab_norm[severity_col].astype(str).str.contains('Critical|High', case=False, na=False)]
                logger.info(f"   - Critical ABs found using column '{severity_col}': {len(critical_abs)} rows")
            else:
                # FIXED: If no severity column, use ALL records (can't filter without severity)
                critical_abs = ab_norm
                logger.info(f"   - No severity column found, using ALL {len(ab_norm)} ABs")
        
        escalated_cases = pd.DataFrame()
        if not csone_df.empty:
            # Try different column names for severity
            severity_cols = [col for col in csone_df.columns if 'severity' in col.lower() or 'priority' in col.lower()]
            if severity_cols:
                severity_col = severity_cols[0]
                escalated_cases = csone_df[csone_df[severity_col].astype(str).str.contains('P1|P2|Critical|High', case=False, na=False)]
                logger.info(f"   - Escalated cases found using column '{severity_col}': {len(escalated_cases)} rows")
            else:
                # FIXED: If no severity column, use ALL records (can't filter without severity)
                escalated_cases = csone_df
                logger.info(f"   - No severity/priority column found, using ALL {len(csone_df)} cases")
        
        # Create high-risk customers based on data availability
        high_risk_customers = pd.DataFrame()
        if not ab_norm.empty or not csone_df.empty:
            # Get customers with multiple issues (flexible column names)
            customer_issue_counts = {}
            ab_cust_col = next((c for c in ['customer_name', 'Customer Name', 'BU_NAME', 'Customer'] if c in ab_norm.columns), None) if not ab_norm.empty else None
            csone_cust_col = next((c for c in ['customer_name', 'Customer Name', 'BU_NAME', 'Customer'] if c in csone_df.columns), None) if not csone_df.empty else None
            
            if not ab_norm.empty and ab_cust_col:
                ab_counts = ab_norm[ab_cust_col].value_counts()
                for customer, count in ab_counts.items():
                    customer_issue_counts[customer] = customer_issue_counts.get(customer, 0) + count
            
            if not csone_df.empty and csone_cust_col:
                case_counts = csone_df[csone_cust_col].value_counts()
                for customer, count in case_counts.items():
                    customer_issue_counts[customer] = customer_issue_counts.get(customer, 0) + count
            
            # FIXED: Get ALL customers with issues
            if customer_issue_counts:
                sorted_customers = sorted(customer_issue_counts.items(), key=lambda x: x[1], reverse=True)
                high_risk_customer_names = [name for name, count in sorted_customers]  # All customers
                
                # Create high-risk customers dataframe
                high_risk_data = []
                for customer in high_risk_customer_names:
                    ab_count = len(ab_norm[ab_norm[ab_cust_col] == customer]) if not ab_norm.empty and ab_cust_col else 0
                    case_count = len(csone_df[csone_df[csone_cust_col] == customer]) if not csone_df.empty and csone_cust_col else 0
                    total_issues = customer_issue_counts[customer]
                    
                    high_risk_data.append({
                        'Customer Name': customer,
                        'Adoption Barriers': ab_count,
                        'Support Cases': case_count,
                        'Total Issues': total_issues,
                        'Risk Level': 'High' if total_issues >= 5 else 'Medium' if total_issues >= 3 else 'Low'
                    })
                
                high_risk_customers = pd.DataFrame(high_risk_data)
                logger.info(f"   - High-risk customers identified: {len(high_risk_customers)} customers")
        
        sheets = {
            " Executive_Dashboard": dashboard_df,
            "Risk_Summary": risk_summary_df,
            "High_Risk_Customers": high_risk_customers,
            "Critical_Adoption_Barriers": critical_abs,
            "Escalated_Cases": escalated_cases,
            "All_Adoption_Barriers": ab_norm,
            "All_Support_Cases": csone_df,
            "Action_Plans": csconsole_action_plans,
            "Customer_Pulse": csconsole_customer_pulse,
            "Success_Priorities": csconsole_success_priorities
        }
        
        # Log sheet information
        logger.info(f"[[SEARCH]] DEBUGGING - Total sheets to create: {len(sheets)}")
        for sheet_name, df in sheets.items():
            logger.info(f"[[DOC]] Sheet '{sheet_name}': {len(df)} rows, columns: {list(df.columns) if not df.empty else 'empty'}")
            if not df.empty:
                logger.info(f"   - Sample data: {df.head(1).to_dict('records')}")
        
        # Check if all sheets are empty
        non_empty_sheets = [name for name, df in sheets.items() if not df.empty]
        logger.info(f"[[SEARCH]] DEBUGGING - Non-empty sheets: {non_empty_sheets}")
        if not non_empty_sheets:
            logger.warning(f"[[WARNING]] WARNING - ALL SHEETS ARE EMPTY! Creating fallback summary sheet.")
            # Create a fallback summary sheet with analysis information
            summary_data = pd.DataFrame({
                'Analysis Information': [
                    f'Manager: {manager}',
                    f'Technology: {technology}',
                    f'Analysis Period: {days} days',
                    f'Report Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
                    f'Analysis ID: {analysis_id}',
                    '',
                    'Note: Some optional data sources may be empty.',
                    'In a production environment, this would contain real customer data.'
                ],
                'Value': ['', '', '', '', '', '', '', '']
            })
            sheets[' Analysis_Summary'] = summary_data
            logger.info(f"[[REFRESH]] Created fallback summary sheet with {len(summary_data)} rows")
        
        # Create Excel file directly for compact analysis
        try:
            logger.info(f"[[DATA]] Creating Excel file: {excel_path}")
            logger.info(f"[[SEARCH]] DEBUGGING - Sheets dictionary contents:")
            for sheet_name, df in sheets.items():
                logger.info(f"   - Sheet '{sheet_name}': {len(df)} rows, columns: {list(df.columns) if not df.empty else 'empty'}")
            
            with pd.ExcelWriter(excel_path, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Create enhanced formats
                header_format = workbook.add_format({
                    'bold': True,
                    'text_wrap': True,
                    'valign': 'top',
                    'fg_color': '#4472C4',
                    'font_color': 'white',
                    'border': 1,
                    'font_size': 11
                })
            
                title_format = workbook.add_format({
                    'bold': True,
                    'font_size': 16,
                    'fg_color': '#2F4F4F',
                    'font_color': 'white',
                    'border': 1,
                    'align': 'center',
                    'valign': 'vcenter'
                })
            
                # Risk-based formatting
                high_risk_format = workbook.add_format({
                    'fg_color': '#FF6B6B',
                    'font_color': 'white',
                    'bold': True,
                    'border': 1
                })
            
                medium_risk_format = workbook.add_format({
                    'fg_color': '#FFE66D',
                    'font_color': 'black',
                    'bold': True,
                    'border': 1
                })
            
                low_risk_format = workbook.add_format({
                    'fg_color': '#4ECDC4',
                    'font_color': 'white',
                    'bold': True,
                    'border': 1
                })
            
                # Data formatting
                data_format = workbook.add_format({
                    'border': 1,
                    'valign': 'top',
                    'text_wrap': True
                })
            
                # Number formatting
                number_format = workbook.add_format({
                    'num_format': '#,##0',
                    'border': 1,
                    'align': 'right'
                })
            
                # Date formatting
                date_format = workbook.add_format({
                    'num_format': 'mm/dd/yyyy',
                    'border': 1,
                    'align': 'center'
                })
            
                # Write each sheet - ensure dashboard is always written first
                logger.info(f"[[SEARCH]] DEBUGGING - About to process {len(sheets)} sheets")
                logger.info(f"[[SEARCH]] DEBUGGING - Sheet names: {list(sheets.keys())}")
            
                # CRITICAL FIX: Always write dashboard first, even if empty
                dashboard_sheet_name = None
                dashboard_df_data = None
                for sheet_name, df in sheets.items():
                    if 'Dashboard' in sheet_name or 'dashboard' in sheet_name.lower():
                        dashboard_sheet_name = sheet_name
                        dashboard_df_data = df
                        break
            
                # Write dashboard first if found - ALWAYS write it, even if empty
                if dashboard_sheet_name and dashboard_df_data is not None:
                    logger.info(f"[[WRITE]] Writing dashboard sheet '{dashboard_sheet_name}' first with {len(dashboard_df_data)} rows...")
                    try:
                        df_clean = _clean_datetime_columns_for_excel(dashboard_df_data)
                        # CRITICAL: Always write dashboard, even if empty
                        if df_clean.empty:
                            # Create a minimal dashboard if empty
                            logger.warning(f"[[WARNING]] Dashboard is empty, creating minimal dashboard")
                            df_clean = pd.DataFrame({
                                'Metric': ['No Data Available'],
                                'Value': ['Dashboard data not available'],
                                'Status': ['Data source issue']
                            })
                    
                        df_clean.to_excel(writer, sheet_name=dashboard_sheet_name, index=False, startrow=1)
                        worksheet = writer.sheets[dashboard_sheet_name]
                        title_text = f" {dashboard_sheet_name.replace('_', ' ')} - {manager} Portfolio Analysis"
                        if len(df_clean.columns) > 0:
                            worksheet.merge_range(0, 0, 0, len(df_clean.columns)-1, title_text, title_format)
                            for col_num, value in enumerate(df_clean.columns.values):
                                worksheet.write(1, col_num, value, header_format)
                            for row_idx in range(2, len(df_clean) + 2):
                                for col_idx in range(len(df_clean.columns)):
                                    cell_value = df_clean.iloc[row_idx-2, col_idx]
                                    worksheet.write(row_idx, col_idx, cell_value, data_format)
                            for i, col in enumerate(df_clean.columns):
                                if df_clean.empty:
                                    max_length = len(str(col))
                                else:
                                    try:
                                        content_max = df_clean[col].astype(str).map(len).max()
                                        content_max = content_max if pd.notna(content_max) else 0
                                    except (ValueError, TypeError):
                                        content_max = 0
                                    max_length = max(content_max, len(str(col)))
                                worksheet.set_column(i, i, min(max_length + 2, 50))
                            if not df_clean.empty and len(df_clean) > 0:
                                worksheet.autofilter(1, 0, len(df_clean), len(df_clean.columns)-1)
                        worksheet.freeze_panes(2, 0)
                        logger.info(f"[[OK]] Dashboard sheet '{dashboard_sheet_name}' written successfully")
                    except Exception as dash_error:
                        logger.error(f"[[ERROR]] Error writing dashboard sheet: {dash_error}")
                        # Create a fallback dashboard sheet
                        fallback_df = pd.DataFrame({
                            'Metric': ['Error Writing Dashboard'],
                            'Value': [str(dash_error)],
                            'Status': ['Error']
                        })
                        fallback_df.to_excel(writer, sheet_name=dashboard_sheet_name, index=False)
            
                # Write other sheets
                for sheet_name, df in sheets.items():
                    # Skip dashboard if already written
                    if dashboard_sheet_name and sheet_name == dashboard_sheet_name:
                        continue
                
                    logger.info(f"[[WRITE]] Writing sheet '{sheet_name}' with {len(df)} rows...")
                    logger.info(f"[[SEARCH]] DEBUGGING - Sheet '{sheet_name}' empty: {df.empty}")
                    logger.info(f"[[SEARCH]] DEBUGGING - Sheet '{sheet_name}' columns: {list(df.columns) if not df.empty else 'N/A'}")
                
                    if not df.empty:
                        # Clean datetime columns for Excel compatibility
                        logger.info(f"[[CLEAN]] Cleaning datetime columns for sheet '{sheet_name}'...")
                        df_clean = _clean_datetime_columns_for_excel(df)
                        logger.info(f"[[OK]] Datetime columns cleaned for sheet '{sheet_name}'")
                    
                        logger.info(f" Writing data to Excel sheet '{sheet_name}'...")
                        df_clean.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
                        logger.info(f"[[OK]] Data written to sheet '{sheet_name}'")
                    
                        # Enhanced sheet formatting
                        worksheet = writer.sheets[sheet_name]
                    
                        # Write enhanced title with analysis info
                        title_text = f" {sheet_name.replace('_', ' ')} - {manager} Portfolio Analysis"
                        worksheet.merge_range(0, 0, 0, len(df_clean.columns)-1, title_text, title_format)
                    
                        # Format headers with enhanced styling
                        for col_num, value in enumerate(df_clean.columns.values):
                            worksheet.write(1, col_num, value, header_format)
                    
                        # Apply conditional formatting based on sheet type
                        if 'Risk' in sheet_name or 'High_Risk' in sheet_name:
                            # Apply risk-based formatting to risk-related sheets
                            for row_idx in range(2, len(df_clean) + 2):
                                for col_idx in range(len(df_clean.columns)):
                                    cell_value = df_clean.iloc[row_idx-2, col_idx]
                                    if isinstance(cell_value, (int, float)) and 'risk' in str(df_clean.columns[col_idx]).lower():
                                        if cell_value >= 7:
                                            worksheet.write(row_idx, col_idx, cell_value, high_risk_format)
                                        elif cell_value >= 4:
                                            worksheet.write(row_idx, col_idx, cell_value, medium_risk_format)
                                        else:
                                            worksheet.write(row_idx, col_idx, cell_value, low_risk_format)
                                    elif isinstance(cell_value, str) and any(keyword in cell_value.lower() for keyword in ['critical', 'high', 'urgent']):
                                        worksheet.write(row_idx, col_idx, cell_value, high_risk_format)
                                    else:
                                        worksheet.write(row_idx, col_idx, cell_value, data_format)
                    
                        # Enhanced column width calculation
                        for i, col in enumerate(df_clean.columns):
                            # Calculate optimal width based on content type (guard against NaN from empty/mixed columns)
                            try:
                                content_max = df_clean[col].astype(str).map(len).max()
                                content_max = content_max if pd.notna(content_max) else 0
                            except (ValueError, TypeError):
                                content_max = 0
                            col_name_len = len(str(col))
                            if df_clean[col].dtype in ['int64', 'float64']:
                                max_length = max(content_max, col_name_len)
                                worksheet.set_column(i, i, min(max_length + 2, 15))
                            elif 'date' in col.lower() or 'time' in col.lower():
                                worksheet.set_column(i, i, 12)
                            else:
                                max_length = max(content_max, col_name_len)
                                worksheet.set_column(i, i, min(max_length + 2, 50))
                    
                        # Add data validation and filtering
                        worksheet.autofilter(1, 0, len(df_clean), len(df_clean.columns)-1)
                    
                        # Freeze header row
                        worksheet.freeze_panes(2, 0)
                    
                        logger.info(f"[[OK]] Sheet '{sheet_name}' formatted with enhanced styling")
                    else:
                        # Create empty sheet with title
                        logger.info(f"[[DOC]] Creating empty sheet '{sheet_name}'...")
                        empty_df = pd.DataFrame({'Message': ['No data available for this analysis']})
                        empty_df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
                        worksheet = writer.sheets[sheet_name]
                        worksheet.write(0, 0, f"{sheet_name.replace('_', ' ')} - {manager} Portfolio Analysis", title_format)
                        logger.info(f"[[OK]] Empty sheet '{sheet_name}' created")
            
                # Ensure we have at least one sheet with meaningful content
                logger.info(f"[[SEARCH]] DEBUGGING - Total sheets written: {len(writer.sheets)}")
                for sheet_name in writer.sheets.keys():
                    logger.info(f"   - Written sheet: {sheet_name}")
            
                logger.info(f"[[OK]] Excel file created successfully: {excel_path}")
            
                # DEBUGGING - Validate Excel file
                if os.path.exists(excel_path):
                    file_size = os.path.getsize(excel_path)
                    logger.info(f"[[DATA]] Excel file size: {file_size} bytes")
                
                    # Try to read the file to verify it's valid
                    try:
                        test_read = pd.read_excel(excel_path, sheet_name=None, nrows=1)
                        logger.info(f"[[SEARCH]] DEBUGGING - Excel validation: SUCCESS - {len(test_read)} sheets")
                        for sheet_name in test_read.keys():
                            logger.info(f"   - Sheet: {sheet_name}")
                    except Exception as e:
                        logger.error(f"[[SEARCH]] DEBUGGING - Excel validation: FAILED - {e}")
                else:
                    logger.error(f"[[DATA]] Excel file does not exist after creation!")
            
        except Exception as excel_error:
            logger.error(f"[[ERROR]] Error creating Excel file: {excel_error}")
            raise excel_error
        
        # Update status
        with analysis_status_lock:
            status['status'] = 'completed'
            status['progress'] = 100
            status['message'] = ' Compact analysis completed successfully!'
            status['current_step'] = 'Completed'
            status['completion_time'] = datetime.now().isoformat()
            completion_time = status['completion_time']
            report_type = status.get('report_type', 'compact')
            manager = status.get('manager', '')
            technology = status.get('technology', status.get('tech', ''))
            customer_name = status.get('customer_name', '')
            start_time = status.get('start_time', '')
            status['word_report'] = exec_report_path
            status['excel_report'] = excel_path

        # Persist outside lock to reduce lock contention during file I/O.
        save_analysis_status()  # Save final status

        # Run non-locking side effects after releasing status lock
        auto_audit_report(analysis_id)
        record_report_completion(
            analysis_id, report_type, manager, technology, customer_name,
            'completed', start_time, completion_time
        )
        try:
            store_report_insights(
                analysis_id,
                report_type,
                manager,
                technology,
                customer_name,
                _build_insights_payload(status, 'Compact analysis completed'),
            )
        except Exception:
            pass
        
        logger.info(f"[[OK]] Executive Intelligence Report analysis completed: {analysis_id}")
        logger.info(f"[[FILE]] Word report: {exec_report_path} (exists: {os.path.exists(exec_report_path)})")
        logger.info(f"[[DATA]] Excel report: {excel_path} (exists: {os.path.exists(excel_path)})")
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error in compact analysis: {e}")
        with analysis_status_lock:
            if analysis_id in analysis_status:
                analysis_status[analysis_id]['status'] = 'error'
                analysis_status[analysis_id]['message'] = f' Analysis failed: {str(e)}'
                analysis_status[analysis_id]['error'] = str(e)
        save_analysis_status()
    finally:
        # Ensure database connection is closed
        if 'ctx' in locals() and ctx is not None:
            try:
                ctx.close()
                logger.info(f"[[CLEANUP]] Database connection closed for {analysis_id}")
            except Exception as e:
                logger.warning(f"[[WARNING]] Error closing database connection: {e}")


def _calculate_simple_renewal_risk(customer_name: str, customer_ab: pd.DataFrame, 
                                   customer_csone: pd.DataFrame, team_subs_df: pd.DataFrame,
                                   days: int, ext_incidents: List[Dict] = None) -> Dict:
    """
    Calculate renewal risk score from available data (adoption barriers + CSOne cases + service incidents).
    This avoids the Snowflake schema issues in the AdvancedRenewalAnalyzer.
    
    Args:
        ext_incidents: List of service incidents from status.webex.com (optional)
    """
    logger.info(f"[RENEWAL] Calculating simple renewal risk for: {customer_name}")
    
    risk_score = 0
    risk_factors = []
    key_findings = []
    recommendations = []
    
    # 1. Adoption Barriers Analysis (0-30 points)
    ab_score = 0
    if not customer_ab.empty:
        ab_count = len(customer_ab)
        key_findings.append(f"{ab_count} adoption barriers identified")
        
        # High severity barriers
        if 'SEVERITY_C' in customer_ab.columns:
            critical_abs = customer_ab[customer_ab['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)]
            if len(critical_abs) > 0:
                ab_score += min(len(critical_abs) * 5, 15)
                risk_factors.append(f"{len(critical_abs)} critical/high severity adoption barriers")
                recommendations.append("Address critical adoption barriers immediately")
        
        # Open barriers
        if 'AB_STATUS_C' in customer_ab.columns:
            open_abs = customer_ab[~customer_ab['AB_STATUS_C'].astype(str).str.contains('Closed|Resolved', case=False, na=False)]
            if len(open_abs) > 0:
                ab_score += min(len(open_abs) * 2, 10)
                risk_factors.append(f"{len(open_abs)} open/unresolved adoption barriers")
        
        # Any barriers add base risk
        if ab_count > 0:
            ab_score += min(ab_count, 5)
    else:
        key_findings.append("No adoption barriers found - positive indicator")
    
    risk_score += ab_score
    logger.info(f"[RENEWAL] Adoption barrier risk score: {ab_score}/30")
    
    # 2. Support Cases Analysis (0-30 points)
    csone_score = 0
    if not customer_csone.empty:
        case_count = len(customer_csone)
        key_findings.append(f"{case_count} support cases in last {days} days")
        
        # High case volume indicates problems
        if case_count > 10:
            csone_score += 15
            risk_factors.append(f"High support volume: {case_count} cases")
            recommendations.append("Review recurring support issues and implement proactive solutions")
        elif case_count > 5:
            csone_score += 10
            risk_factors.append(f"Elevated support volume: {case_count} cases")
        elif case_count > 0:
            csone_score += 5
        
        # Priority cases
        if 'Severity' in customer_csone.columns:
            p1_cases = customer_csone[customer_csone['Severity'].astype(str).str.contains('P1|Critical', case=False, na=False)]
            p2_cases = customer_csone[customer_csone['Severity'].astype(str).str.contains('P2|High', case=False, na=False)]
            
            if len(p1_cases) > 0:
                csone_score += min(len(p1_cases) * 5, 10)
                risk_factors.append(f"{len(p1_cases)} P1/Critical priority cases")
                recommendations.append("Escalate P1 cases and ensure executive visibility")
            
            if len(p2_cases) > 0:
                csone_score += min(len(p2_cases) * 2, 5)
    else:
        key_findings.append("No recent support cases - positive indicator")
    
    risk_score += csone_score
    logger.info(f"[RENEWAL] Support case risk score: {csone_score}/30")
    
    # 2.5. BEMS Escalation Analysis (0-20 points) - CRITICAL for renewal risk
    bems_score = 0
    bems_count = 0
    bems_ids = []
    
    if not customer_csone.empty:
        # Check Transaction ID column (primary BEMS source)
        if 'Transaction ID' in customer_csone.columns:
            bems_from_tid = customer_csone[customer_csone['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)]
            for _, row in bems_from_tid.iterrows():
                tid = str(row.get('Transaction ID', ''))
                if 'BEMS' in tid.upper():
                    bems_ids.append(tid)
            bems_count += len(bems_from_tid)
        
        # Check bemscsc_refs column (secondary BEMS source)
        if 'bemscsc_refs' in customer_csone.columns:
            import re
            for _, row in customer_csone.iterrows():
                refs = str(row.get('bemscsc_refs', ''))
                bems_matches = re.findall(r'BEMS[-]?\d+', refs, re.IGNORECASE)
                for bems_id in bems_matches:
                    if bems_id.upper() not in bems_ids:
                        bems_ids.append(bems_id.upper())
                        bems_count += 1
    
    if bems_count > 0:
        # BEMS escalations are CRITICAL - each one adds significant risk
        bems_score = min(bems_count * 10, 20)  # Max 20 points
        risk_factors.append(f"{bems_count} BEMS escalations (Back-End Engineering) - CRITICAL")
        # FIXED: Show ALL BEMS IDs
        key_findings.append(f"BEMS IDs: {', '.join([f'[{bid}]' for bid in bems_ids])}")
        recommendations.insert(0, f"URGENT: Address {bems_count} BEMS escalations requiring backend engineering attention")
        logger.info(f"[RENEWAL] Found {bems_count} BEMS escalations: {bems_ids}")
    
    risk_score += bems_score
    logger.info(f"[RENEWAL] BEMS escalation risk score: {bems_score}/20")
    
    # 3. Subscription/Contract Analysis (0-20 points)
    contract_score = 0
    if not team_subs_df.empty:
        customer_subs = team_subs_df[team_subs_df['BU_NAME'] == customer_name] if 'BU_NAME' in team_subs_df.columns else pd.DataFrame()
        
        if not customer_subs.empty:
            sub_count = len(customer_subs)
            key_findings.append(f"{sub_count} active subscriptions")
            
            # Check for renewal risk indicators in subscription data
            if 'RENEWAL_RISK_CATEGORY' in customer_subs.columns:
                high_risk = customer_subs[customer_subs['RENEWAL_RISK_CATEGORY'].astype(str).str.contains('High|Critical', case=False, na=False)]
                if len(high_risk) > 0:
                    contract_score += 15
                    risk_factors.append(f"{len(high_risk)} high-risk subscriptions")
            
            if 'STATUS_C' in customer_subs.columns:
                inactive = customer_subs[customer_subs['STATUS_C'].astype(str).str.contains('Inactive|Expired', case=False, na=False)]
                if len(inactive) > 0:
                    contract_score += 5
                    risk_factors.append(f"{len(inactive)} inactive/expired subscriptions")
        else:
            contract_score += 10  # No subscription data is concerning
            risk_factors.append("No subscription data found for customer")
    
    risk_score += contract_score
    logger.info(f"[RENEWAL] Contract risk score: {contract_score}/20")
    
    # 3.5. Service Incidents Analysis (0-15 points) - NEW: Factor in status.webex.com incidents
    incident_score = 0
    if ext_incidents and len(ext_incidents) > 0:
        incident_count = len(ext_incidents)
        key_findings.append(f"{incident_count} service incidents from status.webex.com during analysis period")
        
        # Count high-impact incidents (investigating, identified, monitoring)
        high_impact_statuses = ['investigating', 'identified', 'monitoring']
        high_impact_incidents = [inc for inc in ext_incidents 
                                 if inc.get('status', '').lower() in high_impact_statuses]
        high_impact_count = len(high_impact_incidents)
        
        if high_impact_count > 0:
            # High-impact incidents significantly affect renewal risk
            incident_score = min(high_impact_count * 3, 15)  # Max 15 points (3 per high-impact incident)
            risk_factors.append(f"{high_impact_count} high-impact service incidents (status.webex.com)")
            recommendations.append(f"Review {high_impact_count} service incidents and correlate with customer support cases")
            key_findings.append(f"Service incidents can directly impact customer satisfaction and renewal probability")
        elif incident_count > 5:
            # Even resolved incidents indicate service instability
            incident_score = min(incident_count, 5)  # Max 5 points for volume
            risk_factors.append(f"{incident_count} service incidents (may indicate service instability)")
    else:
        key_findings.append("No service incidents from status.webex.com - positive indicator")
    
    risk_score += incident_score
    logger.info(f"[RENEWAL] Service incident risk score: {incident_score}/15")
    
    # 4. Engagement Score (0-20 points) - based on lack of issues
    engagement_score = 0
    # If lots of issues, customer is engaged but struggling
    # If no issues, could be good (adopted well) or bad (disengaged)
    total_issues = len(customer_ab) + len(customer_csone)
    if total_issues == 0:
        # No issues could mean disengagement - add moderate risk
        engagement_score = 10
        risk_factors.append("Low engagement detected - no recent support or adoption activity")
        recommendations.append("Schedule proactive customer health check")
    elif total_issues > 15:
        engagement_score = 15
        risk_factors.append("Customer experiencing multiple issues - high churn risk")
        recommendations.append("Executive intervention recommended")
    
    risk_score += engagement_score
    logger.info(f"[RENEWAL] Engagement risk score: {engagement_score}/20")
    
    # Cap at 100
    risk_score = min(risk_score, 100)
    
    # Determine risk category
    if risk_score >= 70:
        risk_category = 'CRITICAL'
    elif risk_score >= 50:
        risk_category = 'HIGH'
    elif risk_score >= 30:
        risk_category = 'MEDIUM'
    else:
        risk_category = 'LOW'
    
    # Add default recommendations if none
    if not recommendations:
        recommendations = [
            "Continue regular customer engagement",
            "Monitor adoption metrics quarterly",
            "Schedule periodic business reviews"
        ]
    
    logger.info(f"[RENEWAL] Final risk score: {risk_score}/100 ({risk_category})")
    
    # Calculate incident metrics for return
    incident_count = len(ext_incidents) if ext_incidents else 0
    high_impact_incidents = 0
    if ext_incidents:
        high_impact_statuses = ['investigating', 'identified', 'monitoring']
        high_impact_incidents = sum(1 for inc in ext_incidents 
                                    if inc.get('status', '').lower() in high_impact_statuses)
    
    return {
        'customer_name': customer_name,
        'renewal_risk_score': risk_score,
        'renewal_risk_category': risk_category,
        'analysis_date': datetime.now().isoformat(),
        'key_findings': key_findings,
        'risk_factors': risk_factors,
        'recommendations': recommendations,
        'adoption_barriers_count': len(customer_ab),
        'support_cases_count': len(customer_csone),
        'bems_escalations_count': bems_count,
        'bems_ids': bems_ids,
        'service_incidents_count': incident_count,
        'high_impact_incidents_count': high_impact_incidents,
        'analysis_period_days': days
    }


def _create_simple_renewal_report(base_path: str, customer_name: str, technology: str,
                                  days: int, renewal_analysis: Dict,
                                  customer_ab: pd.DataFrame, customer_csone: pd.DataFrame,
                                  ext_bugs: List[Dict] = None, ext_incidents: List[Dict] = None,
                                  software_defects: Dict = None, psirt_vulns: Dict = None,
                                  portfolio_mode: bool = False, all_customers: List[str] = None,
                                  chart_paths: List[str] = None,
                                  customer_action_plans: pd.DataFrame = None,
                                  customer_customer_pulse: pd.DataFrame = None,
                                  customer_success_priorities: pd.DataFrame = None) -> str:
    """Create a comprehensive Word document for renewal analysis with ALL data sources.
    
    Args:
        portfolio_mode: If True, generates portfolio-level report for multiple customers
        all_customers: List of customer names (required if portfolio_mode=True)
    """
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from datetime import datetime, timedelta
    
    if portfolio_mode:
        # CRITICAL FIX: Ensure all_customers is defined and not None before using len()
        if all_customers is not None and isinstance(all_customers, (list, tuple)):
            customer_count = len(all_customers)
        else:
            customer_count = 0
            logger.warning(f"[RENEWAL] Portfolio mode but all_customers is not a valid list: {type(all_customers)}")
        logger.info(f"[RENEWAL] Creating portfolio renewal report for {customer_count} customers")
    else:
        logger.info(f"[RENEWAL] Creating simple renewal report for {customer_name}")
    
    doc = Document()
    
    def _na(v):
        """Normalize None/empty/'none'/nan to 'N/A' so report never shows literal 'None [Severity: None, Status: None]'."""
        if v is None or v == '': return 'N/A'
        if isinstance(v, str) and v.strip().lower() == 'none': return 'N/A'
        try:
            if getattr(pd, 'isna', None) and pd.isna(v): return 'N/A'
        except Exception: pass
        return v

    def _pulse_rating(v):
        """Normalize pulse rating; extract alt text from image tags."""
        if v is None:
            return 'N/A'
        s = str(v)
        if '<img' in s.lower():
            m = re.search(r'alt="([^"]+)"', s, flags=re.IGNORECASE)
            return m.group(1) if m else 'N/A'
        return _na(v)

    def _is_empty(v):
        if v is None:
            return True
        try:
            if getattr(pd, 'isna', None) and pd.isna(v):
                return True
        except Exception:
            pass
        if isinstance(v, str):
            s = v.strip().lower()
            return s in ('', 'none', 'nan', 'n/a')
        return False
    
    def _first_avail(row, keys, default='N/A'):
        """Use first non-empty value from row using any of the given column names (EDW/CSConsole/View naming)."""
        idx = getattr(row, 'index', ())
        for k in keys:
            if k not in idx:
                continue
            v = row.get(k)
            if v is None:
                continue
            try:
                if getattr(pd, 'isna', None) and pd.isna(v):
                    continue
            except Exception:
                pass
            if isinstance(v, str) and not v.strip():
                continue
            s = str(v).strip()
            if s and s.lower() != 'nan':
                return _na(v)
        return default

    def _first_avail_by_hint(row, hints):
        """Fallback: use first non-empty value from any column whose name contains any of hints (case-insensitive)."""
        idx = getattr(row, 'index', ())
        for col in idx:
            if not isinstance(col, str):
                continue
            col_lower = col.lower()
            if not any(h.lower() in col_lower for h in hints):
                continue
            v = row.get(col)
            if v is None:
                continue
            try:
                if getattr(pd, 'isna', None) and pd.isna(v):
                    continue
            except Exception:
                pass
            s = str(v).strip()
            if s and s.lower() not in ('', 'nan', 'none'):
                return _na(v)
        return 'N/A'

    # Title
    if portfolio_mode:
        title = doc.add_heading(f'Portfolio Renewal Analysis: {customer_name}', 0)
    else:
        title = doc.add_heading(f'Customer Renewal Analysis: {customer_name}', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Subtitle
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(f'Technology: {technology} | Analysis Period: {days} days')
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(100, 100, 100)
    
    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = date_para.add_run(f'Generated: {datetime.now().strftime("%B %d, %Y at %H:%M")}')
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(150, 150, 150)
    doc.add_paragraph()
    data_sources_line = doc.add_paragraph()
    data_sources_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = data_sources_line.add_run('Data sources: CSConsole, Snowflake C360_CS_TASK_C_VW, CSOne (TAC/BEMS), status.webex.com, help.webex.com. See Report Data Sources below.')
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(120, 120, 120)
    
    # Report Data Sources – canonical sources (shared across all AdoptIQ reports)
    doc.add_paragraph()
    doc.add_heading('Report Data Sources', level=2)
    try:
        from report_utils import get_data_sources_paragraph_text
    except ImportError:
        get_data_sources_paragraph_text = lambda: (
            'All metrics and references in this report cite their origin. '
            'Adoption Barriers: CSConsole / Snowflake C360_CS_TASK_C_VW. '
            'Support Cases: CSOne (TAC case data). BEMS Escalations: CSOne (Transaction ID, bemscsc_refs). '
            'Service Incidents: status.webex.com. Software Defects: help.webex.com, plus CSC/BST references. '
            'Customer Pulse, Action Plans, Success Priorities: CSConsole.'
        )
    sources_para = doc.add_paragraph()
    sources_para.add_run(get_data_sources_paragraph_text())
    scope_note = doc.add_paragraph()
    scope_note.add_run('Report scope: ').bold = True
    scope_note.add_run('This renewal report focuses on renewal risk and analyzes all renewal-relevant data: adoption barriers, support cases (CSOne or Snowflake when available), BEMS (from CSOne), service incidents, and software defects. It runs faster than the Comprehensive report because it does not include the full Enhanced Snowflake Insights suite (ARR, engagement/usage/risk/product from CX_DB) or the longer narrative—all key renewal metrics above are still included.')
    scope_note.paragraph_format.space_before = Pt(6)
    doc.add_paragraph()
    
    # Executive Summary at Top (2-3 sentence overview)
    try:
        from report_utils import format_date, get_risk_scoring_explanation, get_report_metadata_footer, format_number
    except ImportError:
        format_date = lambda v, s="long": (v.strftime("%B %d, %Y") if hasattr(v, 'strftime') else str(v)) if v else "N/A"
        format_number = lambda v, d=0, p=False: (f"{v:,.{d}f}" if d else f"{int(v):,}") if v is not None else "N/A"
        get_risk_scoring_explanation = lambda: "Risk score based on adoption barriers, support cases, BEMS escalations, and engagement."
        get_report_metadata_footer = lambda **kw: "AdoptIQ Report"
    
    exec_summary = doc.add_heading('Executive Summary', level=1)
    risk_score_raw = renewal_analysis.get('renewal_risk_score', 0)
    risk_score = round(float(risk_score_raw), 1) if risk_score_raw is not None else 0
    risk_category = renewal_analysis.get('renewal_risk_category', 'UNKNOWN')
    ab_count = renewal_analysis.get('adoption_barriers_count', 0)
    case_count = renewal_analysis.get('support_cases_count', 0)
    bems_count = renewal_analysis.get('bems_escalations_count', 0)
    exec_para = doc.add_paragraph()
    if portfolio_mode and all_customers:
        n_cust = len(all_customers)
        high_risk = renewal_analysis.get('high_risk_customers', [])
        exec_para.add_run(f'This portfolio of {n_cust} customers has an overall renewal risk score of {risk_score:.1f}/100 ({risk_category}). ')
        if high_risk:
            exec_para.add_run(f'{len(high_risk)} customer(s) require immediate attention. ')
        exec_para.add_run(f'Key metrics: {format_number(ab_count)} adoption barriers, {format_number(case_count)} support cases, {format_number(bems_count)} BEMS escalations.')
    else:
        exec_para.add_run(f'{customer_name} has a renewal risk score of {risk_score:.1f}/100 ({risk_category}). ')
        exec_para.add_run(f'Key metrics: {format_number(ab_count)} adoption barriers, {format_number(case_count)} support cases, {format_number(bems_count)} BEMS escalations. ')
        exec_para.add_run('See Key Findings and Recommendations for actionable next steps.')
    doc.add_paragraph()
    
    # Customer Health Dashboard (NEW - matches example renewal report format)
    doc.add_heading('Customer Health Dashboard', level=1)
    
    risk_score_raw = renewal_analysis.get('renewal_risk_score', 0)
    risk_score = round(float(risk_score_raw), 1) if risk_score_raw is not None else 0
    risk_category = renewal_analysis.get('renewal_risk_category', 'UNKNOWN')
    ab_count = renewal_analysis.get('adoption_barriers_count', 0)
    case_count = renewal_analysis.get('support_cases_count', 0)
    bems_count = renewal_analysis.get('bems_escalations_count', 0)
    
    # Calculate incident metrics
    incident_count = len(ext_incidents) if ext_incidents else 0
    high_impact_incidents = 0
    correlated_incidents = 0
    
    if ext_incidents:
        # Count high-impact incidents (status: investigating, identified, monitoring, or resolved with impact)
        high_impact_statuses = ['investigating', 'identified', 'monitoring', 'resolved']
        high_impact_incidents = sum(1 for inc in ext_incidents 
                                    if inc.get('status', '').lower() in high_impact_statuses)
        
        # Count incidents that might correlate with support cases (based on timing and keywords)
        if not customer_csone.empty:
            # Simple correlation: incidents within the analysis period
            analysis_start = datetime.now() - timedelta(days=days)
            correlated_incidents = sum(1 for inc in ext_incidents 
                                       if inc.get('published') and 
                                       pd.to_datetime(inc.get('published', ''), errors='coerce') >= analysis_start)
    
    # Create dashboard table (matches example format)
    from docx.enum.table import WD_TABLE_ALIGNMENT
    dashboard_table = doc.add_table(rows=8, cols=2)
    dashboard_table.style = 'Table Grid'
    
    # Populate table
    dashboard_data = [
        ('Support Cases (Last 90 Days)', str(case_count)),
        ('Active Adoption Barriers', str(ab_count)),
        ('BEMS Escalations', str(bems_count)),
        ('Service Incidents (status.webex.com)', str(incident_count)),
        ('High-Impact Incidents', str(high_impact_incidents)),
        ('Correlated Service Incidents', str(correlated_incidents)),
        ('Overall Risk Score', f'{risk_score:.1f}/100'),
        ('Risk Category', risk_category)
    ]
    
    for i, (label, value) in enumerate(dashboard_data):
        row = dashboard_table.rows[i]
        row.cells[0].text = label
        row.cells[1].text = value
        # Bold the labels
        for para in row.cells[0].paragraphs:
            for run in para.runs:
                run.bold = True
        # Color code risk category
        if label == 'Risk Category':
            pass  # handled below
        elif label == 'Support Cases (Last 90 Days)' and case_count == 0 and bems_count == 0:
            # Add footnote in next row would require table resize; add paragraph after table instead
            pass
    # When Support Cases and BEMS are both 0, explain why and how to get full data (improved messaging)
    if case_count == 0 and bems_count == 0:
        dash_note = doc.add_paragraph()
        dash_note.add_run('Data availability: ').bold = True
        dash_note.add_run('Support cases and BEMS escalations come from two sources: ').font.size = Pt(9)
        dash_note.add_run('(1) CSOne TAC export (primary, recommended) — provides full case detail and BEMS IDs; ').font.size = Pt(9)
        dash_note.add_run('(2) Snowflake SUPPORT_CASES — used when no CSOne file is uploaded. ').font.size = Pt(9)
        if renewal_analysis.get('support_cases_from_snowflake'):
            dash_note.add_run('This report used Snowflake; no support cases matched the selected scope. ').font.size = Pt(9)
        else:
            dash_note.add_run('No CSOne file was provided and no Snowflake support cases were returned for this scope, so counts show 0. ').font.size = Pt(9)
        dash_note.add_run('To get complete TAC cases and BEMS escalations (engineering-level), upload a CSOne export when starting the analysis. ').font.size = Pt(9)
        dash_note.add_run('Source: CSOne (TAC); Snowflake CX_DB.CX_SWSSBST_BR.SUPPORT_CASES.').font.italic = True
        dash_note.paragraph_format.space_before = Pt(3)
    for i, (label, value) in enumerate(dashboard_data):
        if label != 'Risk Category':
            continue
        row = dashboard_table.rows[i]
        # Color code risk category (moved from above)
        if label == 'Risk Category':
            for para in row.cells[1].paragraphs:
                for run in para.runs:
                    run.bold = True
                    if risk_category == 'CRITICAL':
                        run.font.color.rgb = RGBColor(220, 20, 60)
                    elif risk_category == 'HIGH':
                        run.font.color.rgb = RGBColor(255, 140, 0)
                    elif risk_category == 'LOW':
                        run.font.color.rgb = RGBColor(34, 139, 34)
    
    doc.add_paragraph()
    
    # Risk Assessment & Key Findings (detailed section)
    doc.add_heading('Risk Assessment & Key Findings', level=1)
    
    # Risk Score Box
    summary = doc.add_paragraph()
    summary.add_run('Renewal Risk Score: ').bold = True
    
    if risk_category == 'CRITICAL':
        color = RGBColor(220, 20, 60)
    elif risk_category == 'HIGH':
        color = RGBColor(255, 140, 0)
    elif risk_category == 'MEDIUM':
        color = RGBColor(255, 215, 0)
    else:
        color = RGBColor(34, 139, 34)
    
    score_run = summary.add_run(f'{risk_score:.1f}/100 ({risk_category})')
    score_run.bold = True
    score_run.font.color.rgb = color
    score_run.font.size = Pt(14)
    
    # Key Findings – expanded for portfolio: barriers per customer, at-risk counts, source
    doc.add_heading('Key Findings', level=2)
    for finding in renewal_analysis.get('key_findings', []):
        p = doc.add_paragraph(style='List Bullet')
        p.add_run(finding)
    # Portfolio: add interpretation and by-customer summary
    if portfolio_mode and all_customers and not customer_ab.empty:
        n_cust = len(all_customers)
        ab_count = renewal_analysis.get('adoption_barriers_count', 0) or len(customer_ab)
        cust_col = 'customer_name' if 'customer_name' in customer_ab.columns else ('BU_NAME' if 'BU_NAME' in customer_ab.columns else None)
        if cust_col and n_cust > 0:
            ab_per_cust = ab_count / n_cust
            p = doc.add_paragraph()
            p.add_run(f'Adoption barriers: {ab_count} total across {n_cust} customers (~{ab_per_cust:.1f} per customer). ').bold = False
            p.add_run('Source: CSConsole / Snowflake C360_CS_TASK_C_VW.\n')
            by_cust = customer_ab[cust_col].value_counts()
            high_barrier = (by_cust >= 3).sum()
            if high_barrier > 0:
                p2 = doc.add_paragraph(style='List Bullet')
                p2.add_run(f'{high_barrier} customer(s) have 3+ open adoption barriers and warrant focused attention. ')
                p2.add_run('See Troubled Accounts Deep Dive for per-account risk factors and recommended actions.\n')
            # Top 15 by barrier count
            top15 = by_cust.head(15)
            if len(top15) > 0:
                p3 = doc.add_paragraph()
                p3.add_run('Top customers by adoption barrier count (Source: CSConsole/Snowflake): ').bold = True
                p3.add_run('; '.join([f'{c}: {n}' for c, n in top15.items()]) + '.\n')
    # Risk Factors
    if renewal_analysis.get('risk_factors'):
        doc.add_heading('Risk Factors', level=2)
        for factor in renewal_analysis['risk_factors']:
            p = doc.add_paragraph(style='List Bullet')
            run = p.add_run(factor)
            run.font.color.rgb = RGBColor(180, 0, 0)
    
    # Top 10 by Risk / Focus Accounts (portfolio mode)
    if portfolio_mode and renewal_analysis.get('customer_analyses'):
        doc.add_page_break()
        doc.add_heading('Top 10 Focus Accounts by Risk', level=1)
        cust_analyses = renewal_analysis['customer_analyses']
        sorted_by_risk = sorted(
            cust_analyses.items(),
            key=lambda x: x[1].get('renewal_risk_score', x[1].get('overall_risk_score', 0)),
            reverse=True
        )[:10]
        focus_table = doc.add_table(rows=1 + len(sorted_by_risk), cols=4)
        focus_table.style = 'Table Grid'
        hdr = focus_table.rows[0].cells
        for i, txt in enumerate(['Rank', 'Customer', 'Risk Score', 'Category']):
            hdr[i].text = txt
            for para in hdr[i].paragraphs:
                for run in para.runs:
                    run.bold = True
        for idx, (cust, ana) in enumerate(sorted_by_risk, 1):
            row = focus_table.rows[idx].cells
            row[0].text = str(idx)
            row[1].text = str(cust)
            row[2].text = f"{ana.get('renewal_risk_score', ana.get('overall_risk_score', 0)):.1f}/100"
            row[3].text = str(ana.get('renewal_risk_category', 'N/A'))
        doc.add_paragraph()
    
    # Risk Scoring Methodology (transparent explanation)
    try:
        from report_utils import get_risk_scoring_explanation
    except ImportError:
        get_risk_scoring_explanation = lambda: "Risk score based on adoption barriers, support cases, BEMS escalations, contract status, and engagement."
    doc.add_heading('Risk Scoring Methodology', level=2)
    method_para = doc.add_paragraph()
    for line in get_risk_scoring_explanation().strip().split('\n'):
        if line.strip():
            method_para.add_run(line.strip() + '\n').font.size = Pt(9)
    doc.add_paragraph()
    
    # Adoption Barriers Summary – always show customer name in portfolio; cite source
    doc.add_heading('Adoption Barriers Analysis', level=1)
    ab_count = renewal_analysis.get('adoption_barriers_count', 0) or (len(customer_ab) if not customer_ab.empty else 0)
    ab_para = doc.add_paragraph()
    ab_para.add_run(f'Total Adoption Barriers: {ab_count}\n').bold = True
    ab_para.add_run('Source: CSConsole / Snowflake C360_CS_TASK_C_VW.\n').italic = True
    if portfolio_mode and all_customers and not customer_ab.empty:
        cust_col_ab = 'customer_name' if 'customer_name' in customer_ab.columns else ('BU_NAME' if 'BU_NAME' in customer_ab.columns else None)
        if cust_col_ab:
            by_cust = customer_ab[cust_col_ab].value_counts()
            p_sum = doc.add_paragraph()
            p_sum.add_run('Summary by customer (Source: CSConsole/Snowflake): ').bold = True
            p_sum.add_run(f'{len(by_cust)} customers have at least one barrier. ')
            p_sum.add_run('Full list below includes subject, severity, and status per barrier.\n')
    
    if not customer_ab.empty:
        # Show ALL barriers with real data; in portfolio mode prefix customer name
        doc.add_paragraph('All Adoption Barriers (by customer where applicable):', style='Heading 3')
        for i, (_, row) in enumerate(customer_ab.iterrows()):
            p = doc.add_paragraph(style='List Number')
            cust_label = ''
            if portfolio_mode and all_customers:
                cn = _na(row.get('customer_name', row.get('BU_NAME', row.get('CUSTOMER_BU_NAME__C', row.get('RELATED_CUSTOMER__C', '')))))
                if cn and cn != 'N/A':
                    cust_label = f'Customer: {cn} — '
            subject = _first_avail(row, ['NAME', 'SUBJECT_C', 'subject_c', 'title', 'TITLE_C', 'DESCRIPTION_C', 'description', 'Subject', 'Title'], 'N/A')
            if _is_empty(subject):
                subject = _first_avail_by_hint(row, ['subject', 'title', 'name', 'desc', 'summary', 'issue', 'problem'])
            severity = _first_avail(row, ['SEVERITY_C', 'severity_c', 'Severity'], 'N/A')
            if _is_empty(severity):
                severity = _first_avail_by_hint(row, ['severity', 'priority'])
            status = _first_avail(row, ['STATUS_C', 'AB_STATUS_C', 'ab_status_c', 'status_c', 'Status'], 'N/A')
            if _is_empty(status):
                status = _first_avail_by_hint(row, ['status', 'ab_status', 'state'])
            subject = _na(subject)
            severity = _na(severity)
            status = _na(status)
            if cust_label:
                p.add_run(cust_label).bold = True
            p.add_run(str(subject))
            p.add_run(f' [Severity: {severity}, Status: {status}]').font.size = Pt(9)
    else:
        doc.add_paragraph('No adoption barriers identified - this is a positive indicator.')
    
    doc.add_page_break()
    # Support Cases Summary – customer name in portfolio; cite source
    doc.add_heading('Support Cases Analysis', level=1)
    case_count = renewal_analysis.get('support_cases_count', 0)
    case_para = doc.add_paragraph()
    case_para.add_run(f'Total Support Cases ({days} days): {case_count}\n').bold = True
    if renewal_analysis.get('support_cases_from_snowflake'):
        case_para.add_run('Source: Snowflake SUPPORT_CASES (no CSOne file provided). BEMS are only from CSOne (TAC).\n').italic = True
    else:
        case_para.add_run('Source: CSOne (TAC case data).\n').italic = True
    
    if not customer_csone.empty:
        # FIXED: Show ALL support cases with customer name (portfolio), TAC case numbers, age
        doc.add_paragraph('All Support Cases (by customer where applicable):', style='Heading 3')
        for i, (_, row) in enumerate(customer_csone.iterrows()):
            p = doc.add_paragraph(style='List Number')
            cust_label = ''
            if portfolio_mode and all_customers:
                cn = _na(row.get('customer_name', row.get('Customer Name', row.get('BU_NAME', ''))))
                if cn and cn != 'N/A':
                    cust_label = f'Customer: {cn} — '
            case_num = _na(row.get('Case #', row.get('SR Number', row.get('Case Number', 'N/A'))))
            title_text = _na(row.get('Title', row.get('title', 'N/A')))
            severity = _na(row.get('Severity', row.get('Highest Priority', 'N/A')))
            status = _na(row.get('Status', row.get('Case Status', 'N/A')))
            age_str = ''
            date_opened = row.get('Date/Time Opened', None)
            if date_opened:
                try:
                    if isinstance(date_opened, str):
                        date_opened = pd.to_datetime(date_opened, errors='coerce')
                    if pd.notna(date_opened):
                        days_open = (datetime.now() - date_opened).days
                        if days_open > 30:
                            age_str = ', open >30 days'
                        elif days_open > 14:
                            age_str = ', open >14 days'
                        elif days_open > 7:
                            age_str = ', open >7 days'
                except Exception:
                    pass
            if cust_label:
                p.add_run(cust_label).bold = True
            p.add_run(f'TAC {case_num}: ').bold = True
            p.add_run(str(title_text))
            p.add_run(f' [Severity: {severity}, Status: {status}{age_str}]').font.size = Pt(9)
    else:
        doc.add_paragraph('No support cases in the analysis period - this is a positive indicator.')
    
    doc.add_page_break()
    # BEMS Escalation Analysis (CRITICAL section) – impact on churn called out
    bems_count = renewal_analysis.get('bems_escalations_count', 0)
    bems_ids = renewal_analysis.get('bems_ids', [])
    
    if bems_count > 0:
        doc.add_heading('🚨 BEMS Escalation Analysis (CRITICAL)', level=1)
        bems_warning = doc.add_paragraph()
        warning_run = bems_warning.add_run(f'⚠️ {bems_count} BEMS Escalation(s) Detected - Immediate Attention Required')
        warning_run.bold = True
        warning_run.font.color.rgb = RGBColor(220, 20, 60)
        warning_run.font.size = Pt(12)
        
        doc.add_paragraph()
        bems_info = doc.add_paragraph()
        bems_info.add_run('BEMS (Back-End Engineering Management System) escalations indicate complex technical issues that TAC could not resolve independently. These represent high-severity, high-complexity problems requiring specialized engineering expertise. ')
        bems_info.add_run('Unresolved engineering escalations often lead to customer churn, as customers seek vendors with more stable resolution paths. ')
        bems_info.add_run('Source: CSOne (Transaction ID, bemscsc_refs).')
        bems_info.paragraph_format.left_indent = Inches(0.25)
        
        if bems_ids:
            doc.add_paragraph()
            ids_para = doc.add_paragraph()
            ids_para.add_run('All BEMS IDs: ').bold = True
            # FIXED: Show ALL BEMS IDs with brackets for citation
            ids_para.add_run(', '.join([f'[{bid}]' for bid in bems_ids]))
        
        # Show BEMS cases from CSOne
        if not customer_csone.empty and 'Transaction ID' in customer_csone.columns:
            bems_cases = customer_csone[customer_csone['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)]
            if not bems_cases.empty:
                doc.add_paragraph()
                doc.add_paragraph('All TAC Cases with BEMS Escalations:', style='Heading 3')
                # FIXED: Show ALL BEMS cases for complete visibility
                for _, row in bems_cases.iterrows():
                    p = doc.add_paragraph(style='List Bullet')
                    case_num = row.get('Case #', row.get('SR Number', 'N/A'))
                    title_text = row.get('Title', 'N/A')
                    trans_id = row.get('Transaction ID', 'N/A')
                    # FIXED: Don't truncate title - show full text
                    p.add_run(f'Case: {case_num} - {title_text} ')
                    tid_run = p.add_run(f'[BEMS: {trans_id}]')
                    tid_run.font.color.rgb = RGBColor(180, 0, 0)
                    tid_run.bold = True
    else:
        doc.add_heading('BEMS Escalation Analysis', level=1)
        no_bems = doc.add_paragraph()
        case_count = renewal_analysis.get('support_cases_count', 0)
        from_snowflake = renewal_analysis.get('support_cases_from_snowflake', False)
        if case_count == 0:
            no_bems.add_run('Data availability: ').bold = True
            no_bems.add_run('BEMS and support cases come from CSOne (TAC) or Snowflake SUPPORT_CASES. ')
            no_bems.add_run('No CSOne file was provided and no support cases were returned from Snowflake for this scope, so both show as zero. ')
            no_bems.add_run('To get full TAC cases and BEMS escalations (engineering-level), upload a CSOne export when starting the analysis. ')
            no_bems.add_run('Source: CSOne (Transaction ID, bemscsc_refs). Unresolved BEMS often drive churn.').font.italic = True
        else:
            no_bems.add_run('No BEMS escalations detected. ')
            if from_snowflake:
                no_bems.add_run('Support cases came from Snowflake; BEMS are only available from CSOne (TAC). Upload a CSOne export for BEMS analysis. ')
            else:
                no_bems.add_run('The absence of BEMS in the provided CSOne data is a positive indicator. ')
            no_bems.add_run('Source: CSOne (Transaction ID, bemscsc_refs).')
    
    doc.add_page_break()
    # IMMEDIATE ACTIONS section (matches example format)
    doc.add_heading('Immediate Actions', level=1)
    immediate_actions = [
        f"Schedule and Lead a Comprehensive QBR: Include current open support cases, thematic trends, and review of high adoption barriers.",
        f"Deep Dive into Support Case Themes: Analyze root causes behind recurring issues and collaborate with engineering for rapid resolution.",
        f"Conduct Adoption Barrier Workshop: Work with stakeholders to review all high and critical barriers, prioritize, and establish mitigation plan.",
        f"Provide Proactive User Training: Offer tailored enablement on integrations for end users and admins to reduce case volume.",
        f"Strengthen Executive Touchpoints: Re-engage executive sponsors to share progress and planned improvements."
    ]
    for action in immediate_actions:
        p = doc.add_paragraph(style='List Bullet')
        p.add_run(action)
    
    # RENEWAL STRATEGY section (matches example format)
    doc.add_heading('Renewal Strategy', level=1)
    strategy_para = doc.add_paragraph()
    if risk_category in ['CRITICAL', 'HIGH']:
        strategy_para.add_run('Adopt a high-touch, outcomes-focused approach ').bold = True
        strategy_para.add_run('centered on restoring customer confidence by aggressively reducing support case frequency and addressing high adoption barriers. ')
        strategy_para.add_run('Leverage senior executive sponsorships and regular cadence meetings (QBRs, technical reviews) to demonstrate commitment, align on business goals, and track action plan progress. ')
        strategy_para.add_run('Ensure all key stakeholders are updated on issue resolution timelines and positive developments, laying the foundation for a value-based renewal conversation.')
    else:
        strategy_para.add_run('Continue value-based engagement approach ').bold = True
        strategy_para.add_run('with focus on proactive success planning and demonstrating ROI. ')
        strategy_para.add_run('Maintain regular executive touchpoints and QBRs to reinforce partnership value. ')
        strategy_para.add_run('Leverage positive indicators to position for upsell and expansion opportunities during renewal discussions.')
    
    # TALKING POINTS section (matches example format)
    doc.add_heading('Talking Points', level=1)
    talking_points = [
        "We recognize the recent support activity and have implemented a focused action plan targeting recurring issues.",
        "Your feedback on adoption and technical challenges has driven us to increase our cadence of QBRs and technical reviews.",
        "We are prioritizing resolution of all high-impact adoption barriers and will work with your team to clear these promptly.",
        "Our goal is to reduce ongoing case volume and improve user experience by delivering targeted enablement sessions.",
        "Let's align on your key success metrics for the coming year to ensure our partnership directly supports your business objectives."
    ]
    for point in talking_points:
        p = doc.add_paragraph(style='List Bullet')
        p.add_run(point)
    
    # === CHARTS & VISUALIZATIONS ===
    if chart_paths and len(chart_paths) > 0:
        doc.add_page_break()
        doc.add_heading('Visual Analysis & Trends', level=1)
        
        logger.info(f"[[RENEWAL_CHARTS]] Adding {len(chart_paths)} charts to renewal report")
        charts_added = 0
        for chart_path in chart_paths:
            if os.path.exists(chart_path):
                try:
                    # Add chart title based on filename
                    if 'renewal_risk_score' in chart_path:
                        doc.add_heading('Renewal Risk Score Visualization', level=2)
                    elif 'renewal_support_trend' in chart_path:
                        doc.add_heading('Support Cases Trend Over Time', level=2)
                    elif 'renewal_incidents_timeline' in chart_path:
                        doc.add_heading('Service Incidents Timeline', level=2)
                    elif 'renewal_health_dashboard' in chart_path:
                        doc.add_heading('Renewal Health Dashboard', level=2)
                    elif 'renewal_risk_factors' in chart_path:
                        doc.add_heading('Risk Factors Breakdown', level=2)
                    
                    doc.add_picture(chart_path, width=Inches(6.5))
                    doc.add_paragraph()  # Spacing
                    charts_added += 1
                    logger.info(f"[[OK]] Chart added to renewal report: {chart_path}")
                except Exception as e:
                    logger.error(f"[[ERROR]] Failed to add chart {chart_path}: {e}")
        
        logger.info(f"[[OK]] Total charts added to renewal report: {charts_added}")
        if charts_added == 0:
            doc.add_paragraph("Charts are being generated but not available yet.")
    else:
        logger.warning(f"[[WARNING]] No charts available for renewal report")
    
    # FIXED: Add External Intelligence Sections (BST defects, status.webex.com incidents) + Impact + Source
    if ext_bugs or (software_defects and software_defects.get('total_defects', 0) > 0):
        doc.add_heading('Software Defects & Known Issues', level=1)
        defect_para = doc.add_paragraph()
        
        # Software defects from customer data
        if software_defects and software_defects.get('total_defects', 0) > 0:
            defect_count = software_defects.get('total_defects', 0)
            cases_with_defects = software_defects.get('total_cases_with_defects', 0)
            defect_by_customer = software_defects.get('defect_by_customer', {})
            customers_with_defects = software_defects.get('customers_with_defects') or (list(defect_by_customer.keys()) if defect_by_customer else [])
            if not customers_with_defects and defect_by_customer:
                customers_with_defects = list(defect_by_customer.keys())
            defect_para.add_run(f'Software Defects Identified: ').bold = True
            defect_para.add_run(f'{defect_count} unique BST/CSC defects found in {cases_with_defects} support cases. ')
            defect_para.add_run('Source: CSOne case references, Adoption Barrier references.\n').italic = True
            # Impact: high defect count can increase support load and adoption friction
            defect_para.add_run('Impact on renewal: ').bold = True
            defect_para.add_run('A high number of defects linked to support cases or adoption barriers can increase support load, lengthen time-to-resolution, and contribute to adoption friction. ')
            if customers_with_defects:
                defect_para.add_run(f'{len(customers_with_defects)} customer(s) in this portfolio have linked defects; see Troubled Accounts Deep Dive for per-customer detail and recommended actions.\n\n')
            else:
                defect_para.add_run('See Troubled Accounts Deep Dive for customers with linked defects and recommended actions.\n\n')
            # Single-customer: show defect IDs; portfolio: show defects by customer name
            if defect_by_customer and not portfolio_mode and customer_name in defect_by_customer:
                defects = sorted(set(defect_by_customer[customer_name]))
                defect_para.add_run(f'Defect IDs for this customer: {", ".join([f"[{d}]" for d in defects])}\n')
            elif defect_by_customer and portfolio_mode and all_customers:
                defect_para.add_run('Defects by customer (Source: CSOne / Adoption Barriers):\n')
                for cust in sorted(defect_by_customer.keys()):
                    ids = sorted(set(defect_by_customer[cust]))
                    defect_para.add_run(f'  • Customer: {cust} — Defect IDs: {", ".join([f"[{x}]" for x in ids])}\n')
        
        # External bugs from help.webex.com
        if ext_bugs:
            defect_para.add_run(f'Known Issues from help.webex.com: ').bold = True
            defect_para.add_run(f'{len(ext_bugs)} publicly referenced software defects that may correlate with customer issues. ')
            defect_para.add_run('Source: help.webex.com.\n')
            defect_para.add_run('Impact on renewal: ').bold = True
            defect_para.add_run('Publicly known defects may align with adoption barrier or support themes; cross-reference with barrier descriptions and Troubled Accounts where relevant.\n\n')
        
        if not ext_bugs and (not software_defects or software_defects.get('total_defects', 0) == 0):
            defect_para.add_run('No software defects identified in customer data or external sources.')
    
    # FIXED: Add PSIRT Vulnerabilities Section
    if psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0:
        doc.add_heading('Security Vulnerabilities (PSIRT)', level=1)
        vuln_para = doc.add_paragraph()
        vuln_count = psirt_vulns.get('total_vulnerabilities', 0)
        cve_count = len(psirt_vulns.get('cve_ids', set()))
        psirt_count = len(psirt_vulns.get('psirt_advisories', set()))
        
        vuln_para.add_run(f'Security Vulnerabilities Identified: ').bold = True
        vuln_para.add_run(f'{vuln_count} total ({cve_count} CVEs, {psirt_count} PSIRT advisories).\n')
        
        # Show vulnerabilities by customer
        vuln_by_customer = psirt_vulns.get('vulnerability_by_customer', {})
        if vuln_by_customer and customer_name in vuln_by_customer:
            vulns = sorted(set(vuln_by_customer[customer_name]))
            vuln_para.add_run(f'Vulnerability IDs: {", ".join([f"[{v}]" for v in vulns])}\n')
    
    # FIXED: Add CSConsole Data Sections (Action Plans, Customer Pulse, Success Priorities) – customer name + source
    if customer_action_plans is not None and not customer_action_plans.empty:
        doc.add_heading('CSConsole Action Plans', level=1)
        ap_para = doc.add_paragraph()
        ap_para.add_run(f'Total Action Plans: {len(customer_action_plans)}\n').bold = True
        ap_para.add_run('Source: CSConsole.\n').italic = True
        
        # Show action plans by status
        if 'STATUS_C' in customer_action_plans.columns:
            status_counts = customer_action_plans['STATUS_C'].value_counts()
            ap_para.add_run('Status Breakdown:\n')
            for status, count in status_counts.items():
                ap_para.add_run(f'  • {_na(status)}: {count}\n')
        
        # Show top action plans; in portfolio mode prefix customer name (BU_NAME)
        doc.add_paragraph('Recent Action Plans (by customer where applicable):', style='Heading 3')
        for i, (_, row) in enumerate(customer_action_plans.head(10).iterrows(), 1):
            p = doc.add_paragraph(style='List Number')
            cust_label = ''
            if portfolio_mode and all_customers:
                cn = _na(row.get('BU_NAME', row.get('CUSTOMER_NAME', row.get('RELATED_CUSTOMER__C', ''))))
                if cn and cn != 'N/A':
                    cust_label = f'Customer: {cn} — '
            subject = _first_avail(row, ['ACTION_PLAN_TITLE_C', 'NAME', 'SUBJECT_C', 'TITLE_C', 'title', 'Subject', 'Title'], 'N/A')
            if _is_empty(subject):
                subject = _first_avail_by_hint(row, ['subject', 'title', 'name', 'action', 'plan', 'task'])
            status = _first_avail(row, ['STATUS_C', 'status_c'], 'N/A')
            if _is_empty(status):
                status = _first_avail_by_hint(row, ['status'])
            subject = _na(subject)
            status = _na(status)
            if cust_label:
                p.add_run(cust_label).bold = True
            p.add_run(f'{subject} [Status: {status}]')
    
    if customer_customer_pulse is not None and not customer_customer_pulse.empty:
        doc.add_heading('CSConsole Customer Pulse', level=1)
        cp_para = doc.add_paragraph()
        cp_para.add_run(f'Total Customer Pulse Records: {len(customer_customer_pulse)}\n').bold = True
        cp_para.add_run('Source: CSConsole.\n').italic = True
        
        # Show pulse ratings
        if 'PULSE_RATING__C' in customer_customer_pulse.columns:
            rating_counts = customer_customer_pulse['PULSE_RATING__C'].value_counts()
            cp_para.add_run('Pulse Rating Breakdown:\n')
            for rating, count in rating_counts.items():
                cp_para.add_run(f'  • {_na(rating)}: {count}\n')
        
        # Show recent pulse records; in portfolio mode prefix customer name (BU_NAME)
        doc.add_paragraph('Recent Customer Pulse Records (by customer where applicable):', style='Heading 3')
        for i, (_, row) in enumerate(customer_customer_pulse.head(10).iterrows(), 1):
            p = doc.add_paragraph(style='List Number')
            cust_label = ''
            if portfolio_mode and all_customers:
                cn = _na(row.get('BU_NAME', row.get('CUSTOMER_NAME', row.get('RELATED_CUSTOMER__C', ''))))
                if cn and cn != 'N/A':
                    cust_label = f'Customer: {cn} — '
            rating = _first_avail(row, ['CUSTOMER_PULSE__C', 'PULSE_RATING__C', 'Pulse_Rating__c', 'RATING__C', 'rating'], 'N/A')
            if _is_empty(rating):
                rating = _first_avail_by_hint(row, ['pulse', 'rating', 'score'])
            comments = _first_avail(row, ['COMMENTS__C', 'CUSTOMER_PULSE__C', 'comments__c', 'Comments'], 'N/A')
            rating = _pulse_rating(rating)
            comments = _na(comments)
            if isinstance(comments, str) and len(comments) > 200:
                comments = comments[:200] + '...'
            if cust_label:
                p.add_run(cust_label).bold = True
            p.add_run(f'Rating: {rating} - {comments}')
    
    if customer_success_priorities is not None and not customer_success_priorities.empty:
        doc.add_heading('CSConsole Success Priorities', level=1)
        sp_para = doc.add_paragraph()
        sp_para.add_run(f'Total Success Priorities: {len(customer_success_priorities)}\n').bold = True
        sp_para.add_run('Source: CSConsole.\n').italic = True
        
        # Show priorities by status (normalize so "None" never appears)
        if 'STATUS__C' in customer_success_priorities.columns:
            status_counts = customer_success_priorities['STATUS__C'].value_counts()
            sp_para.add_run('Status Breakdown:\n')
            for st, count in status_counts.items():
                sp_para.add_run(f'  • {_na(st)}: {count}\n')
        
        # Show top success priorities; in portfolio mode prefix customer name (RELATED_CUSTOMER__C)
        doc.add_paragraph('Recent Success Priorities (by customer where applicable):', style='Heading 3')
        for i, (_, row) in enumerate(customer_success_priorities.head(10).iterrows(), 1):
            p = doc.add_paragraph(style='List Number')
            cust_label = ''
            if portfolio_mode and all_customers:
                cn = _na(row.get('RELATED_CUSTOMER__C', row.get('BU_NAME', row.get('CUSTOMER_NAME', ''))))
                if cn and cn != 'N/A':
                    cust_label = f'Customer: {cn} — '
            subject = _na(_first_avail(row, ['SUCCESS_PRIORITY_TITLE__C', 'SUBJECT_C', 'title', 'NAME', 'TITLE_C'], 'N/A'))
            status = _na(_first_avail(row, ['STATUS__C', 'STATUS_C', 'status_c'], 'N/A'))
            if cust_label:
                p.add_run(cust_label).bold = True
            p.add_run(f'{subject} [Status: {status}]')
    
    # FIXED: Add Status.webex.com Incidents Section with Renewal Risk + Impact + Source
    if ext_incidents:
        doc.add_heading('Service Incidents (status.webex.com) - Renewal Risk Analysis', level=1)
        incident_para = doc.add_paragraph()
        incident_para.add_run(f'Service Incidents Identified: ').bold = True
        incident_para.add_run(f'{len(ext_incidents)} service incidents from status.webex.com during the analysis period. ')
        incident_para.add_run('Source: status.webex.com.\n\n').italic = True
        
        # Impact on portfolio: which products/regions were affected (extract from titles)
        product_mentions = set()
        for inc in (ext_incidents or []):
            t = (inc.get('title') or '')
            for prefix in ('Webex Contact Center', 'Webex Meetings', 'Webex Calling', 'Webex Control Hub', 'Webex App', 'Cisco BroadCloud', 'Webex Hybrid', 'Webex Dedicated', 'Webex for BroadWorks', 'Developer API'):
                if prefix in t:
                    product_mentions.add(prefix)
                    break
        if product_mentions:
            incident_para.add_run('Impact on portfolio: ').bold = True
            incident_para.add_run(f'This analysis is for technology "{technology}". The following products had service incidents during the period: {", ".join(sorted(product_mentions))}. ')
            incident_para.add_run('Customers using these products may have experienced disruption; service-impacting incidents can increase support volume, adoption barriers, and renewal risk. See Troubled Accounts Deep Dive for at-risk customers and recommended actions.\n\n')
        else:
            incident_para.add_run('Impact on portfolio: ').bold = True
            incident_para.add_run(f'Service incidents during the period may have affected customers using Webex/Cisco products. Service-impacting incidents can increase support volume, adoption barriers, and renewal risk. See Troubled Accounts Deep Dive for at-risk customers and recommended actions. Source: status.webex.com.\n\n')
        
        # Renewal Risk Correlation Analysis
        incident_para.add_run('Renewal Risk Correlation: ').bold = True
        if len(ext_incidents) > 0:
            high_impact_count = sum(1 for inc in ext_incidents 
                                    if inc.get('status', '').lower() in ['investigating', 'identified', 'monitoring'])
            
            if high_impact_count > 5:
                incident_para.add_run(f'⚠️ HIGH RISK: {high_impact_count} high-impact incidents detected. Multiple service-impacting incidents can significantly impact customer satisfaction and renewal probability. ')
            elif high_impact_count > 2:
                incident_para.add_run(f'⚠️ MODERATE RISK: {high_impact_count} high-impact incidents detected. Service incidents may correlate with increased support case volume and adoption barriers. ')
            else:
                incident_para.add_run(f'✅ LOW RISK: {high_impact_count} high-impact incidents detected. Service stability appears good. ')
            
            incident_para.add_run(f'Service incidents that impact customer operations can directly correlate with renewal risk, as customers experiencing frequent service disruptions may question the value of their subscription.\n\n')
        
        # Show ALL incidents (not just top 5) sorted by date
        all_incidents = sorted(ext_incidents, key=lambda x: x.get('published', ''), reverse=True)
        if all_incidents:
            doc.add_paragraph('All Service Incidents (sorted by date, most recent first):', style='Heading 3')
            for incident in all_incidents:
                p = doc.add_paragraph(style='List Bullet')
                title = incident.get('title', 'N/A')
                published = incident.get('published', 'N/A')[:10] if incident.get('published') else 'N/A'
                status = incident.get('status', 'N/A')
                link = incident.get('link', '')
                
                # Highlight high-impact incidents
                is_high_impact = status.lower() in ['investigating', 'identified', 'monitoring']
                if is_high_impact:
                    p.add_run('🚨 ').bold = True
                
                incident_text = f'{title} (Published: {published}, Status: {status})'
                if link:
                    # Add hyperlink if available
                    p.add_run(incident_text)
                    # Note: Word hyperlinks require special handling, this is simplified
                else:
                    p.add_run(incident_text)
                
                # Add impact indicator
                if is_high_impact:
                    impact_run = p.add_run(' [HIGH IMPACT]')
                    impact_run.font.color.rgb = RGBColor(220, 20, 60)
                    impact_run.bold = True
    
    # Troubled Accounts Deep Dive – portfolio only: at-risk customers with barriers, pulse, defects, BEMS, incidents, actionable steps
    if portfolio_mode and all_customers and len(all_customers) > 0:
        troubled = set()
        bems_cases = pd.DataFrame()
        if not customer_csone.empty:
            bems_cases, _ = detect_bems_escalations(customer_csone)
        # Red pulse
        if customer_customer_pulse is not None and not customer_customer_pulse.empty:
            for _, row in customer_customer_pulse.iterrows():
                r = _pulse_rating(_first_avail(row, ['CUSTOMER_PULSE__C', 'PULSE_RATING__C', 'Pulse_Rating__c', 'RATING__C'], 'N/A'))
                if r and str(r).upper() == 'RED':
                    cn = _na(row.get('BU_NAME', row.get('CUSTOMER_NAME', row.get('RELATED_CUSTOMER__C', ''))))
                    if cn and cn != 'N/A':
                        troubled.add(cn)
        # "Customer Considering Competitor" or "Intent to Opt Out" barriers
        if not customer_ab.empty and 'customer_name' in customer_ab.columns:
            for _, row in customer_ab.iterrows():
                subj = str(_first_avail(row, ['NAME', 'SUBJECT_C', 'title', 'TITLE_C'], '') or '').lower()
                if 'customer considering competitor' in subj or 'intent to opt out' in subj or 'no value fit' in subj:
                    cn = _na(row.get('customer_name', row.get('BU_NAME', '')))
                    if cn and cn != 'N/A':
                        troubled.add(cn)
        # High barrier count (>=3 open barriers per customer)
        if not customer_ab.empty:
            cc = 'customer_name' if 'customer_name' in customer_ab.columns else ('BU_NAME' if 'BU_NAME' in customer_ab.columns else None)
            if cc:
                for cust in all_customers:
                    cust_ab = customer_ab[customer_ab[cc] == cust]
                    if len(cust_ab) >= 3:
                        troubled.add(cust)
        # Customers with linked defects
        if software_defects and software_defects.get('defect_by_customer'):
            for c in software_defects['defect_by_customer'].keys():
                troubled.add(c)
        # Customers with BEMS escalations (engineering escalations often drive churn)
        if not bems_cases.empty:
            for col in ['customer_name', 'Customer Name', 'BU_NAME']:
                if col in bems_cases.columns:
                    for c in bems_cases[col].dropna().unique().tolist():
                        if c and str(c).strip():
                            troubled.add(str(c).strip())
                    break
        troubled_list = sorted([c for c in troubled if c in (all_customers or [])]) or sorted(troubled)
        if troubled_list:
            doc.add_heading('Troubled Accounts Deep Dive – Steps to Prevent Churn', level=1)
            intro = doc.add_paragraph()
            intro.add_run('The following accounts show elevated renewal risk based on Red customer pulse, “Customer Considering Competitor” or “Intent to Opt Out” adoption barriers, high open barrier count, linked software defects, or BEMS (engineering) escalations. ')
            intro.add_run('For each account we summarize drivers, data sources, and recommended actions you can take now to reduce churn risk.\n')
            for cust in troubled_list[:30]:  # cap at 30 for report length
                doc.add_heading(cust, level=2)
                reasons = []
                if customer_customer_pulse is not None and not customer_customer_pulse.empty:
                    pulse_cust = customer_customer_pulse
                    bn = 'BU_NAME' if 'BU_NAME' in pulse_cust.columns else ('CUSTOMER_NAME' if 'CUSTOMER_NAME' in pulse_cust.columns else None)
                    if bn and cust in (pulse_cust[bn].dropna().unique() if hasattr(pulse_cust[bn], 'dropna') else []):
                        subset = pulse_cust[pulse_cust[bn] == cust]
                        for _, r in subset.iterrows():
                            rating = _pulse_rating(_first_avail(r, ['CUSTOMER_PULSE__C', 'PULSE_RATING__C'], 'N/A'))
                            if str(rating or '').upper() == 'RED':
                                reasons.append('Red customer pulse (Source: CSConsole)')
                                break
                if not customer_ab.empty and ('customer_name' in customer_ab.columns or 'BU_NAME' in customer_ab.columns):
                    cc = 'customer_name' if 'customer_name' in customer_ab.columns else 'BU_NAME'
                    cust_ab = customer_ab[customer_ab[cc] == cust] if cc in customer_ab.columns else pd.DataFrame()
                    if len(cust_ab) > 0:
                        reasons.append(f'{len(cust_ab)} adoption barrier(s) (Source: CSConsole/Snowflake)')
                        barrier_titles = []
                        for _, row in cust_ab.head(5).iterrows():
                            s = _first_avail(row, ['NAME', 'SUBJECT_C', 'title', 'TITLE_C'], 'N/A')
                            if _is_empty(s):
                                s = _first_avail_by_hint(row, ['subject', 'title', 'name'])
                            barrier_titles.append(_na(s))
                        p = doc.add_paragraph(style='List Bullet')
                        p.add_run('Adoption barriers: ').bold = True
                        p.add_run('; '.join(barrier_titles[:5]) + ('…' if len(cust_ab) > 5 else ''))
                if software_defects and software_defects.get('defect_by_customer') and cust in software_defects['defect_by_customer']:
                    ids = sorted(set(software_defects['defect_by_customer'][cust]))
                    reasons.append(f'Linked defects: {", ".join(ids)} (Source: CSOne/Adoption Barriers)')
                    p = doc.add_paragraph(style='List Bullet')
                    p.add_run('Defect IDs: ').bold = True
                    p.add_run(', '.join([f'[{x}]' for x in ids]))
                # BEMS escalations for this customer (engineering escalations often drive churn)
                if not bems_cases.empty:
                    bc_col = next((c for c in ['customer_name', 'Customer Name', 'BU_NAME'] if c in bems_cases.columns), None)
                    if bc_col:
                        cust_bems = bems_cases[bems_cases[bc_col].astype(str).str.strip() == str(cust).strip()]
                        if len(cust_bems) > 0:
                            reasons.append(f'BEMS escalation(s): {len(cust_bems)} case(s) (Source: CSOne)')
                            refs = []
                            for _, r in cust_bems.iterrows():
                                tx = r.get('Transaction ID') or r.get('bemscsc_refs') or ''
                                if tx and str(tx).strip() and 'BEMS' in str(tx).upper():
                                    refs.append(str(tx).strip())
                            if refs:
                                p = doc.add_paragraph(style='List Bullet')
                                p.add_run('BEMS refs: ').bold = True
                                p.add_run('; '.join(refs[:5]) + ('…' if len(refs) > 5 else ''))
                if reasons:
                    p = doc.add_paragraph(style='List Bullet')
                    p.add_run('Risk factors: ').bold = True
                    p.add_run('; '.join(reasons))
                # Recommended actions
                doc.add_paragraph('Recommended actions (take now to reduce churn risk):', style='Heading 4')
                actions = [
                    'Schedule an executive or QBR touchpoint within 14 days to acknowledge concerns and share resolution plans.',
                    'If “Customer Considering Competitor” or “Intent to Opt Out”: assign a dedicated CSM/AM and create a 30‑day retention plan with weekly check-ins.',
                    'If Red pulse: request a discovery call to understand drivers and align on success criteria and timelines.',
                    'If BEMS escalations: prioritize engineering resolution and share timeline with the customer; consider an executive bridge if cases are open >30 days. Unresolved engineering escalations often drive customers to competitors.',
                    'If linked defects: share defect status and ETA with the customer; escalate internally if blocking adoption.',
                    'If service incidents may have affected this customer: share status.webex.com and post-incident summary; offer a short technical review.',
                ]
                for a in actions:
                    doc.add_paragraph(a, style='List Bullet')
        elif portfolio_mode and all_customers:
            p = doc.add_paragraph()
            p.add_run('No troubled accounts were automatically flagged. Continue monitoring adoption barriers, customer pulse, defect linkage, and BEMS escalations; rerun this report as new data is available.')
    
    # Recommendations (action-oriented with account names where applicable)
    doc.add_heading('Recommendations', level=1)
    recs = renewal_analysis.get('recommendations', [])
    if recs:
        for i, rec in enumerate(recs, 1):
            p = doc.add_paragraph(style='List Number')
            p.add_run(rec)
    else:
        fallback = doc.add_paragraph()
        fallback.add_run('No specific recommendations at this time. Continue monitoring adoption barriers, customer pulse, and support case trends.').italic = True
    
    # Report Metadata Footer
    doc.add_page_break()
    doc.add_heading('Report Metadata', level=2)
    try:
        from report_utils import get_report_metadata_footer
    except ImportError:
        get_report_metadata_footer = lambda **kw: "AdoptIQ Renewal Report"
    footer_text = get_report_metadata_footer(
        report_type="Renewal" + (" Portfolio" if portfolio_mode else ""),
        customer_name=customer_name,
        technology=technology,
        days=days,
    )
    footer_para = doc.add_paragraph()
    footer_para.add_run(footer_text).font.size = Pt(8)
    
    # Save
    word_path = f"{base_path}_Renewal_Report.docx"
    doc.save(word_path)
    logger.info(f"[RENEWAL] Report saved to: {word_path}")
    
    return word_path


def run_customer_renewal_analysis(analysis_id):
    """Run customer-specific or portfolio renewal analysis"""
    # Ensure datetime is available (imported at module level)
    from datetime import datetime, timedelta
    try:
        logger.info(f"[[START]] Starting renewal analysis: {analysis_id}")
        
        # Get analysis parameters
        with analysis_status_lock:
            status = analysis_status[analysis_id]
            manager = status['manager']
            technology = status['technology']
            days = status['days']
            subscription_id = status.get('subscription_id', '')
            customer_name = status.get('customer_name', '')
            renewal_type = status.get('renewal_type', 'renewal')  # 'renewal' or 'renewal_portfolio'
            if renewal_type == 'renewal':
                renewal_type = 'renewal_single'  # Form sends 'renewal' for single-customer
            csone_file = status['csone_file']
            
        logger.info(f"[[SEARCH]] DEBUGGING - Renewal Analysis starting:")
        logger.info(f"   - Renewal Type: {renewal_type}")
        logger.info(f"   - Manager: {manager}")
        logger.info(f"   - Technology: {technology}")
        logger.info(f"   - Subscription ID: {subscription_id}")
        logger.info(f"   - Customer Name: {customer_name}")
        logger.info(f"   - Days: {days}")
        logger.info(f"   - CSOne File: {csone_file}")
        
        # Update status
        with analysis_status_lock:
            status['status'] = 'running'
            status['progress'] = 20
            status['message'] = ' Connecting to Snowflake...'
            status['current_step'] = 'Database Connection'
        
        # Connect to Snowflake
        ctx = _connect_with_keeper()
        
        if ctx is None:
            error_msg = (
                "❌ CRITICAL: Snowflake database connection failed.\n\n"
                "Cannot generate renewal report without database access."
            )
            logger.error(f"[[ERROR]] {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Database connection failed'
                status['error'] = error_msg
                status['current_step'] = 'Connection Failed'
                save_analysis_status()
            return
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 30
            status['message'] = ' Fetching team data...'
            status['current_step'] = 'Team Data Retrieval'
        
        # Get team subscriptions - handle single customer renewal without manager
        if renewal_type == 'renewal_single' and (not manager or manager == ''):
            # Single customer renewal without manager: look up customer directly
            logger.info(f"[[SEARCH]] Single customer renewal without manager - looking up customer directly")
            
            if subscription_id:
                # Look up by subscription ID
                logger.info(f"[[SEARCH]] Looking up customer by subscription ID: {subscription_id}")
                sub_data = fetch_subscription_data(subscription_id, days)
                if sub_data and sub_data.get('found', False):
                    customer_name = sub_data.get('customer_name', '')
                    account_id = sub_data.get('account_id')
                    if account_id:
                        account_ids_list = [account_id]
                    else:
                        account_ids_list = []
                    
                    # Create minimal team_subs_df from subscription data
                    team_subs_df = pd.DataFrame({
                        'BU_NAME': [customer_name] if customer_name else [],
                        'ACCOUNT_ID_C': account_ids_list,
                        'SUBSCRIPTION_ID': [subscription_id],
                        'CSSM_EMAIL': [sub_data.get('cssm_email', '')] if sub_data.get('cssm_email') else []
                    })
                    logger.info(f"[[OK]] Found customer '{customer_name}' from subscription ID")
                else:
                    error_msg = f"❌ CRITICAL: No subscription found with ID '{subscription_id}'.\n\nCannot generate renewal report without subscription data."
                    logger.error(f"[[ERROR]] No subscription found with ID '{subscription_id}' - cannot generate renewal report.")
                    with analysis_status_lock:
                        status['status'] = 'error'
                        status['progress'] = 0
                        status['message'] = f' Subscription not found'
                        status['error'] = error_msg
                        status['current_step'] = 'Subscription Not Found'
                        save_analysis_status()
                    return
            elif customer_name:
                # Look up by customer name (use canonical BU_NAME from Snowflake for CSOne/AB matching)
                logger.info(f"[[SEARCH]] Looking up customer by name: {customer_name}")
                sub_results = search_subscriptions_by_customer(customer_name, limit=50)
                if sub_results and len(sub_results) > 0:
                    # Use canonical name from first result (Snowflake BU_NAME) so downstream CSOne/AB matching works
                    first_result = sub_results[0]
                    canonical_name = first_result.get('BU_NAME') or customer_name
                    # Include all subscriptions for this customer (same BU_NAME)
                    same_customer = [r for r in sub_results if (r.get('BU_NAME') or '') == canonical_name]
                    if not same_customer:
                        same_customer = [first_result]
                    # Build team_subs_df with canonical name and all subscriptions for this customer
                    team_subs_df = pd.DataFrame({
                        'BU_NAME': [r.get('BU_NAME') or canonical_name for r in same_customer],
                        'ACCOUNT_ID_C': [r.get('ACCOUNT_ID_C') for r in same_customer],
                        'SUBSCRIPTION_ID': [r.get('SUBSCRIPTION_ID') for r in same_customer],
                        'CSSM_EMAIL': [r.get('CSSM_EMAIL', '') for r in same_customer]
                    })
                    # Store canonical name for rest of pipeline (renewal report, CSOne filter, etc.)
                    customer_name = canonical_name
                    with analysis_status_lock:
                        status['customer_name'] = customer_name
                        save_analysis_status()
                    logger.info(f"[[OK]] Found customer '{customer_name}' from name search ({len(same_customer)} subscription(s))")
                else:
                    error_msg = f"❌ CRITICAL: No subscriptions found for customer '{customer_name}'.\n\nCannot generate renewal report without subscription data."
                    logger.error(f"[[ERROR]] No subscriptions found for customer '{customer_name}' - cannot generate renewal report.")
                    with analysis_status_lock:
                        status['status'] = 'error'
                        status['progress'] = 0
                        status['message'] = f' Customer not found'
                        status['error'] = error_msg
                        status['current_step'] = 'Customer Not Found'
                        save_analysis_status()
                    return
            else:
                error_msg = "❌ CRITICAL: For single customer renewal without manager, either Customer Name or Subscription ID must be provided."
                logger.error(f"[[ERROR]] {error_msg}")
                with analysis_status_lock:
                    status['status'] = 'error'
                    status['progress'] = 0
                    status['message'] = f' Customer information required'
                    status['error'] = error_msg
                    status['current_step'] = 'Missing Customer Info'
                    save_analysis_status()
                return
        else:
            # Portfolio renewal or single renewal with manager: use manager's team
            if not manager:
                # Fallback: try to find manager from customer's subscription
                if subscription_id:
                    sub_data = fetch_subscription_data(subscription_id, days)
                    if sub_data and sub_data.get('cssm_email'):
                        # Find manager from CSSM email
                        for mgr, name, email in TEAM_ROSTER:
                            if email == sub_data.get('cssm_email'):
                                manager = mgr
                                logger.info(f"[[OK]] Found manager '{manager}' from subscription CSSM")
                                break
                
                if not manager:
                    error_msg = "❌ CRITICAL: Manager is required for portfolio renewal or when customer lookup fails."
                    logger.error(f"[[ERROR]] {error_msg}")
                    with analysis_status_lock:
                        status['status'] = 'error'
                        status['progress'] = 0
                        status['message'] = f' Manager required'
                        status['error'] = error_msg
                        status['current_step'] = 'Manager Required'
                    save_analysis_status()
                    return
            # Portfolio or single with manager: fetch manager's team (do not overwrite team_subs_df from single-customer path above)
            cssm_emails = [email for mgr, name, email in TEAM_ROSTER if mgr == manager or manager == "All Managers"]
            team_subs_df = get_subscriptions_for_team(ctx, cssm_emails)
            
            if team_subs_df.empty:
                error_msg = (
                    f"❌ CRITICAL: No team subscription data found for manager '{manager}'.\n\n"
                    "Cannot generate renewal report without team data."
                )
                logger.error(f"[[ERROR]] {error_msg}")
                with analysis_status_lock:
                    status['status'] = 'error'
                    status['progress'] = 0
                    status['message'] = f' No team data found'
                    status['error'] = error_msg
                    status['current_step'] = 'No Team Data'
                    save_analysis_status()
                return
        
        # If subscription_id provided, look up customer name from team subscriptions
        if subscription_id and not customer_name:
            logger.info(f"[[SEARCH]] Looking up customer name for subscription: {subscription_id}")
            if 'SUBSCRIPTION_ID' in team_subs_df.columns:
                sub_match = team_subs_df[team_subs_df['SUBSCRIPTION_ID'] == subscription_id]
                if not sub_match.empty and 'BU_NAME' in sub_match.columns:
                    customer_name = sub_match['BU_NAME'].iloc[0]
                    logger.info(f"[[OK]] Found customer name from subscription: {customer_name}")
                    # Update status with found customer name
                    with analysis_status_lock:
                        status['customer_name'] = customer_name
                else:
                    logger.warning(f"[[WARNING]] Subscription {subscription_id} not found in team data")
        
        # If still no customer name, use subscription_id as fallback identifier
        if not customer_name and subscription_id:
            customer_name = f"Subscription_{subscription_id}"
            logger.info(f"[[INFO]] Using subscription ID as customer identifier: {customer_name}")
            with analysis_status_lock:
                status['customer_name'] = customer_name
        
        account_ids = team_subs_df['ACCOUNT_ID_C'].dropna().unique().tolist()
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 40
            status['message'] = ' Fetching customer data...'
            status['current_step'] = 'Customer Data Analysis'
        
        # Fetch adoption barriers
        ab_raw = fetch_adoption_barriers(ctx, account_ids, days)
        if not ab_raw.empty and "ACCOUNT_ID_C" in ab_raw.columns:
            ab_raw = ab_raw.merge(team_subs_df[["ACCOUNT_ID_C","BU_NAME","CSSM_EMAIL"]].drop_duplicates(), on="ACCOUNT_ID_C", how="left")
        
        ab_scoped = _apply_scope_filter_ab(ab_raw, technology, days)
        ab_norm = _prepare_ab(ab_scoped, team_subs_df)
        logger.info(f"[[RENEWAL]] After Snowflake + scope + prepare: {len(ab_norm)} adoption barriers")
        
        # Merge CSConsole adoption barriers so portfolio gets complete data (fix "not getting all the data")
        try:
            csconsole_adoption_barriers = fetch_csconsole_adoption_barriers(ctx, account_ids, days)
            if not csconsole_adoption_barriers.empty and "ACCOUNT_ID_C" in csconsole_adoption_barriers.columns:
                csab_merged = csconsole_adoption_barriers.merge(
                    team_subs_df[["ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"]].drop_duplicates(),
                    on="ACCOUNT_ID_C", how="left"
                )
                csab_scoped = _apply_scope_filter_ab(csab_merged, technology, days)
                csab_norm = _prepare_ab(csab_scoped, team_subs_df)
                if not csab_norm.empty:
                    before_merge = len(ab_norm)
                    ab_norm = pd.concat([ab_norm, csab_norm], ignore_index=True)
                    # Deduplicate without dropping distinct barriers: prefer ID so same record from two sources collapses; else use (customer_name, title, date) so we only collapse true duplicates
                    if 'ID' in ab_norm.columns and ab_norm['ID'].notna().any():
                        ab_norm = ab_norm.drop_duplicates(subset=['ID'], keep='first')
                    elif 'customer_name' in ab_norm.columns and 'title' in ab_norm.columns:
                        date_col = next((c for c in ['OPEN_DATE_C', 'CREATED_DATE', 'CREATED_DATE_C'] if c in ab_norm.columns), None)
                        if date_col:
                            ab_norm = ab_norm.drop_duplicates(subset=['customer_name', 'title', date_col], keep='first')
                        else:
                            ab_norm = ab_norm.drop_duplicates(subset=['customer_name', 'title'], keep='first')
                    logger.info(f"[[RENEWAL]] Merged CSConsole adoption barriers: {before_merge} + {len(csab_norm)} -> {len(ab_norm)} total barriers (dedup by ID/date)")
        except Exception as e:
            logger.warning(f"[[WARNING]] Could not merge CSConsole adoption barriers: {e}")
        
        # Filter adoption barriers based on renewal type
        if renewal_type == 'renewal_portfolio':
            # Portfolio: use all adoption barriers (no customer filter)
            customer_ab = ab_norm.copy() if not ab_norm.empty else pd.DataFrame()
        else:
            # Single customer: filter for specific customer
            customer_ab = ab_norm[ab_norm['customer_name'] == customer_name] if not ab_norm.empty and customer_name else pd.DataFrame()
        
        # Fetch CSConsole data for renewal analysis (all data sources)
        logger.info(f"[[CSConsole]] Fetching CSConsole data for renewal analysis...")
        try:
            csconsole_action_plans = fetch_csconsole_action_plans(ctx, account_ids, days)
            csconsole_customer_pulse = fetch_csconsole_customer_pulse(ctx, account_ids, days)
            csconsole_success_priorities = fetch_csconsole_success_priorities(ctx, account_ids, days)
            csconsole_adoption_barriers = fetch_csconsole_adoption_barriers(ctx, account_ids, days)
            logger.info(f"[[CSConsole]] Retrieved: {len(csconsole_action_plans)} action plans, {len(csconsole_customer_pulse)} customer pulse, {len(csconsole_success_priorities)} success priorities, {len(csconsole_adoption_barriers)} adoption barriers")
        except Exception as e:
            logger.warning(f"[[WARNING]] CSConsole data fetching failed: {e}")
            csconsole_action_plans = pd.DataFrame()
            csconsole_customer_pulse = pd.DataFrame()
            csconsole_success_priorities = pd.DataFrame()
            csconsole_adoption_barriers = pd.DataFrame()
        
        # Filter CSConsole data for customer(s)
        if renewal_type == 'renewal_portfolio':
            # Portfolio: get all customers from team subscriptions
            all_customers = team_subs_df['BU_NAME'].dropna().unique().tolist() if not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns else []
            logger.info(f"[[CUSTOMER_COUNT]] Portfolio renewal - found {len(all_customers)} customers from team subscriptions")
            # Ensure adoption barriers have customer_name for risk loop and report (Customer: X —)
            if not customer_ab.empty and all_customers:
                if 'customer_name' not in customer_ab.columns and 'BU_NAME' in customer_ab.columns:
                    customer_ab = customer_ab.copy()
                    customer_ab['customer_name'] = customer_ab['BU_NAME']
                elif 'customer_name' in customer_ab.columns:
                    missing = customer_ab['customer_name'].isna() | (customer_ab['customer_name'].astype(str).str.strip() == '')
                    if missing.any() and 'ACCOUNT_ID_C' in customer_ab.columns and not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns:
                        id_to_bu = team_subs_df.drop_duplicates('ACCOUNT_ID_C').set_index('ACCOUNT_ID_C')['BU_NAME']
                        customer_ab = customer_ab.copy()
                        customer_ab.loc[missing, 'customer_name'] = customer_ab.loc[missing, 'ACCOUNT_ID_C'].map(id_to_bu)
                    if 'BU_NAME' in customer_ab.columns:
                        still_missing = customer_ab['customer_name'].isna() | (customer_ab['customer_name'].astype(str).str.strip() == '')
                        if still_missing.any():
                            customer_ab = customer_ab.copy()
                            customer_ab.loc[still_missing, 'customer_name'] = customer_ab.loc[still_missing, 'BU_NAME']
            
            # Portfolio: filter by all customers
            if not csconsole_action_plans.empty and 'BU_NAME' in csconsole_action_plans.columns and all_customers:
                customer_action_plans = csconsole_action_plans[csconsole_action_plans['BU_NAME'].isin(all_customers)]
            else:
                customer_action_plans = pd.DataFrame()
            if not csconsole_customer_pulse.empty and 'BU_NAME' in csconsole_customer_pulse.columns and all_customers:
                customer_customer_pulse = csconsole_customer_pulse[csconsole_customer_pulse['BU_NAME'].isin(all_customers)]
            else:
                customer_customer_pulse = pd.DataFrame()
            if not csconsole_success_priorities.empty and 'RELATED_CUSTOMER__C' in csconsole_success_priorities.columns and all_customers:
                customer_success_priorities = csconsole_success_priorities[csconsole_success_priorities['RELATED_CUSTOMER__C'].isin(all_customers)]
            else:
                customer_success_priorities = pd.DataFrame()
        else:
            # Single customer: filter by specific customer
            if not csconsole_action_plans.empty and 'BU_NAME' in csconsole_action_plans.columns:
                customer_action_plans = csconsole_action_plans[csconsole_action_plans['BU_NAME'] == customer_name]
            else:
                customer_action_plans = pd.DataFrame()
            if not csconsole_customer_pulse.empty and 'BU_NAME' in csconsole_customer_pulse.columns:
                customer_customer_pulse = csconsole_customer_pulse[csconsole_customer_pulse['BU_NAME'] == customer_name]
            else:
                customer_customer_pulse = pd.DataFrame()
            if not csconsole_success_priorities.empty and 'RELATED_CUSTOMER__C' in csconsole_success_priorities.columns:
                customer_success_priorities = csconsole_success_priorities[csconsole_success_priorities['RELATED_CUSTOMER__C'] == customer_name]
            else:
                customer_success_priorities = pd.DataFrame()
        # Process CSOne data; when no file, optionally try Snowflake SUPPORT_CASES so case counts are not always 0
        customer_csone = pd.DataFrame()
        support_cases_from_snowflake = False
        # Resolve CSOne path (may be stored as filename only; try uploads folder)
        csone_path = csone_file if (csone_file and os.path.exists(csone_file)) else None
        if csone_file and not csone_path:
            safe_name = secure_filename(os.path.basename(csone_file))
            if safe_name:
                uploads_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                if os.path.exists(uploads_path):
                    csone_path = uploads_path
        # When no file provided: try OneDrive folder (macro places reports daily)
        if not csone_path:
            csone_path = get_latest_csone_from_folder()
        if csone_path and os.path.exists(csone_path):
            logger.info(f"[[RENEWAL]] Loading CSOne file: {csone_path}")
            csone_df_raw = load_csone_excel(csone_path)
            csone_df_prepared = _prepare_csone(csone_df_raw, team_subs_df)
            team_customer_names = team_subs_df["BU_NAME"].dropna().unique().tolist()
            sub_ids = team_subs_df["SUBSCRIPTION_ID"].dropna().astype(str).unique().tolist() if not team_subs_df.empty and 'SUBSCRIPTION_ID' in team_subs_df.columns else []
            csone_df = _apply_scope_filter_csone(csone_df_prepared, technology, days, sub_ids, team_customer_names)
            if (csone_df is None or csone_df.empty) and (csone_df_prepared is not None and not csone_df_prepared.empty):
                logger.warning(f"[[RENEWAL]] Strict CSOne filter returned 0 cases (team names may not match Excel). Falling back to inclusive filter (technology + date only).")
                csone_df = _apply_scope_filter_csone_inclusive(csone_df_prepared, technology, days)
                logger.info(f"[[RENEWAL]] Inclusive filter: {len(csone_df)} cases")
            if renewal_type == 'renewal_portfolio':
                # Portfolio: use all CSOne data (no customer filter for portfolio)
                # all_customers should already be defined above, but verify
                if 'all_customers' not in locals() or not all_customers:
                    all_customers = team_subs_df['BU_NAME'].dropna().unique().tolist() if not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns else []
                # Portfolio: use all cases, not filtered by customer
                customer_csone = csone_df.copy() if not csone_df.empty else pd.DataFrame()
            else:
                # Single customer: filter for specific customer (exact match, then case-insensitive fallback)
                if not csone_df.empty and customer_name:
                    exact = csone_df[csone_df['customer_name'].astype(str).str.strip() == str(customer_name).strip()]
                    if not exact.empty:
                        customer_csone = exact
                    else:
                        cust_lower = str(customer_name).strip().lower()
                        ci_match = csone_df[csone_df['customer_name'].astype(str).str.strip().str.lower() == cust_lower]
                        if not ci_match.empty:
                            customer_csone = ci_match
                            logger.info(f"[[RENEWAL]] Single customer: matched {len(customer_csone)} cases via case-insensitive customer name")
                        else:
                            customer_csone = pd.DataFrame()
                else:
                    customer_csone = pd.DataFrame()
            logger.info(f"[[RENEWAL]] CSOne: {len(customer_csone)} cases for report (file had {len(csone_df_prepared)} raw, {len(csone_df)} after scope filter)")
        else:
            # No CSOne file: try Snowflake SUPPORT_CASES so support case count and list can be populated when table exists
            try:
                sf_cases = fetch_support_cases_snowflake(ctx, account_ids, days)
                if not sf_cases.empty and 'ACCOUNT_ID' in sf_cases.columns:
                    merge_df = team_subs_df[['ACCOUNT_ID_C', 'BU_NAME']].drop_duplicates()
                    sf_cases = sf_cases.merge(merge_df, left_on='ACCOUNT_ID', right_on='ACCOUNT_ID_C', how='left')
                    sf_cases['customer_name'] = sf_cases['BU_NAME'].fillna(sf_cases['ACCOUNT_ID'].astype(str))
                    sf_cases = sf_cases.rename(columns={
                        'CASE_ID': 'Case #', 'SUBJECT': 'Title', 'STATUS': 'Status',
                        'SEVERITY': 'Severity', 'CREATED_DATE': 'Date/Time Opened'
                    })
                    customer_csone = sf_cases
                    support_cases_from_snowflake = True
                    logger.info(f"[[RENEWAL]] Using {len(customer_csone)} support cases from Snowflake (no CSOne file provided)")
            except Exception as e:
                logger.warning(f"[[WARNING]] Snowflake support cases fetch failed: {e}")
        
        # Validate data sources before generating renewal report
        # For renewal reports, CSOne and adoption barriers are optional
        logger.info(f"[[VALIDATION]] Validating data sources for renewal report (type: {renewal_type})...")
        try:
            # Use 'renewal' as the report_type for validation (validator handles both 'renewal' and 'renewal_portfolio' the same way)
            raise_validation_error_if_invalid(
                report_type='renewal' if renewal_type == 'renewal_single' else 'renewal_portfolio',
                snowflake_ctx=ctx,
                team_subs_df=team_subs_df,
                ab_data=customer_ab,
                csone_data=customer_csone
            )
            logger.info(f"[[VALIDATION]] All required data sources validated successfully")
        except DataSourceValidationError as e:
            error_msg = str(e)
            logger.error(f"[[VALIDATION]] Data validation failed: {error_msg}")
            with analysis_status_lock:
                status['status'] = 'error'
                status['progress'] = 0
                status['message'] = f' Data validation failed - see logs for details'
                status['error'] = error_msg
                status['current_step'] = 'Validation Failed'
                save_analysis_status()
            return
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 55
            status['message'] = ' Gathering external intelligence (BST defects, status.webex.com incidents)...'
            status['current_step'] = 'External Intelligence Gathering'
        
        # FIXED: Fetch external intelligence (BST defects, status.webex.com incidents) for comprehensive analysis
        logger.info(f"[[WEB]] Gathering external intelligence for renewal report...")
        try:
            ext_bugs = fetch_help_webex_bugs()
            ext_incidents = fetch_status_incidents()
            logger.info(f"[[OK]] External intelligence gathered: {len(ext_bugs)} bugs, {len(ext_incidents)} incidents")
        except Exception as e:
            logger.warning(f"[[WARNING]] External intelligence gathering failed: {e}")
            ext_bugs = []
            ext_incidents = []
        
        # Extract software defects and PSIRT vulnerabilities from customer data
        logger.info(f"[[DEFECTS]] Extracting software defects and PSIRT vulnerabilities...")
        software_defects = extract_software_defects(customer_csone, customer_ab)
        psirt_vulns = extract_psirt_vulnerabilities(customer_csone, customer_ab)
        logger.info(f"[[DEFECTS]] Found {software_defects.get('total_defects', 0)} defects and {psirt_vulns.get('total_vulnerabilities', 0)} vulnerabilities")
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 60
            status['message'] = ' Analyzing renewal risk...'
            status['current_step'] = 'Renewal Risk Analysis'
        
        # Calculate renewal risk based on type
        if renewal_type == 'renewal_portfolio':
            # Portfolio renewal: calculate risk for each customer
            # CRITICAL FIX: Ensure all_customers is defined (it should be from above, but verify)
            if 'all_customers' not in locals() or not all_customers:
                all_customers = team_subs_df['BU_NAME'].dropna().unique().tolist() if not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns else []
                logger.info(f"[[CUSTOMER_COUNT]] Portfolio renewal - re-initialized all_customers: {len(all_customers)} customers")
            
            logger.info(f"[[PORTFOLIO]] Calculating renewal risk for {len(all_customers)} customers")
            portfolio_renewal_analyses = {}
            
            for idx, cust_name in enumerate(all_customers):
                with analysis_status_lock:
                    status['progress'] = 60 + int((idx / len(all_customers)) * 15)  # 60-75%
                    status['message'] = f' Analyzing renewal risk for {cust_name} ({idx+1}/{len(all_customers)})...'
                
                # Filter data for this customer
                cust_ab = customer_ab[customer_ab['customer_name'] == cust_name] if not customer_ab.empty else pd.DataFrame()
                cust_csone = customer_csone[customer_csone['customer_name'] == cust_name] if not customer_csone.empty else pd.DataFrame()
                
                # Calculate risk for this customer (include incidents for portfolio analysis)
                cust_risk = _calculate_simple_renewal_risk(
                    customer_name=cust_name,
                    customer_ab=cust_ab,
                    customer_csone=cust_csone,
                    team_subs_df=team_subs_df,
                    days=days,
                    ext_incidents=ext_incidents  # Pass incidents for risk calculation
                )
                portfolio_renewal_analyses[cust_name] = cust_risk
            
            # Create portfolio-level summary with TOP-LEVEL METRICS for report dashboard
            # Use renewal_risk_score (0-100) from each customer; _calculate_simple_renewal_risk returns that, not overall_risk_score
            cust_scores = [a.get('renewal_risk_score', a.get('overall_risk_score', 0)) for a in portfolio_renewal_analyses.values()]
            avg_risk_score = sum(cust_scores) / len(cust_scores) if cust_scores else 0
            # Derive category from 0-100 scale
            if avg_risk_score >= 70:   port_category = 'CRITICAL'
            elif avg_risk_score >= 50: port_category = 'HIGH'
            elif avg_risk_score >= 30: port_category = 'MEDIUM'
            else:                     port_category = 'LOW'
            # Aggregate counts and key findings so report shows real data (fix "not getting all the data")
            tot_ab = len(customer_ab)
            tot_cases = len(customer_csone)
            tot_bems = sum(a.get('bems_escalations_count', 0) for a in portfolio_renewal_analyses.values())
            key_findings_list = []
            if tot_ab > 0:
                key_findings_list.append(f"Portfolio total: {tot_ab} adoption barriers across {len(all_customers)} customers (Source: CSConsole/Snowflake C360_CS_TASK_C_VW)")
            if tot_cases > 0:
                key_findings_list.append(f"Portfolio total: {tot_cases} support cases in last {days} days (Source: CSOne TAC)")
            if tot_bems > 0:
                key_findings_list.append(f"Portfolio total: {tot_bems} BEMS escalations require attention (Source: CSOne; unresolved engineering escalations often drive churn)")
            # At-risk detail: customers with 3+ barriers, or "Customer Considering Competitor"/"Intent to Opt Out"
            cust_col = 'customer_name' if not customer_ab.empty and 'customer_name' in customer_ab.columns else ('BU_NAME' if not customer_ab.empty and 'BU_NAME' in customer_ab.columns else None)
            if cust_col and not customer_ab.empty:
                by_cust = customer_ab[cust_col].value_counts()
                high_barrier_cust = (by_cust >= 3).sum()
                if high_barrier_cust > 0:
                    key_findings_list.append(f"{high_barrier_cust} customer(s) have 3+ adoption barriers — see Troubled Accounts Deep Dive for recommended actions")
                subj_col = next((c for c in ['SUBJECT_C', 'subject_c', 'NAME', 'title', 'TITLE_C'] if c in customer_ab.columns), None)
                if subj_col:
                    comp = customer_ab[customer_ab[subj_col].astype(str).str.lower().str.contains('customer considering competitor|intent to opt out|no value fit', na=False)]
                    if not comp.empty and cust_col in comp.columns:
                        at_risk_from_barrier = comp[cust_col].nunique()
                        if at_risk_from_barrier > 0:
                            key_findings_list.append(f"{at_risk_from_barrier} customer(s) have \"Customer Considering Competitor\" or \"Intent to Opt Out\" barriers — immediate retention focus")
            if not key_findings_list:
                key_findings_list.append("No adoption barriers or support cases in analysis period for portfolio")
            # Build portfolio-level recommendations (was missing, caused "Recommendations" heading with no content)
            high_risk = [name for name, a in portfolio_renewal_analyses.items() if a.get('renewal_risk_score', a.get('overall_risk_score', 0)) >= 70]
            medium_risk = [name for name, a in portfolio_renewal_analyses.items() if 30 <= a.get('renewal_risk_score', a.get('overall_risk_score', 0)) < 70]
            portfolio_recs = []
            if high_risk:
                portfolio_recs.append(f"Prioritize executive intervention for {len(high_risk)} high-risk customer(s): {', '.join(high_risk[:5])}{'...' if len(high_risk) > 5 else ''}")
            if medium_risk:
                portfolio_recs.append(f"Schedule QBRs and health checks for {len(medium_risk)} medium-risk customer(s) within 30 days")
            if tot_ab > 0:
                portfolio_recs.append("Conduct adoption barrier workshop with stakeholders to prioritize and mitigate high-severity barriers")
            if tot_bems > 0:
                portfolio_recs.append("Escalate BEMS cases to engineering; share resolution timelines with affected customers")
            if port_category in ['CRITICAL', 'HIGH']:
                portfolio_recs.append("Deploy high-touch retention plan with weekly check-ins for at-risk accounts")
            if not portfolio_recs:
                portfolio_recs = [
                    "Continue regular portfolio engagement and monitor adoption metrics quarterly",
                    "Schedule periodic business reviews for key accounts",
                    "Leverage positive indicators to position for upsell opportunities"
                ]
            renewal_analysis = {
                'portfolio_mode': True,
                'total_customers': len(all_customers),
                'customer_analyses': portfolio_renewal_analyses,
                'overall_risk_score': avg_risk_score,
                'renewal_risk_score': avg_risk_score,
                'renewal_risk_category': port_category,
                'adoption_barriers_count': tot_ab,
                'support_cases_count': tot_cases,
                'bems_escalations_count': tot_bems,
                'support_cases_from_snowflake': support_cases_from_snowflake,
                'key_findings': key_findings_list,
                'recommendations': portfolio_recs,
                'high_risk_customers': high_risk,
                'medium_risk_customers': medium_risk,
                'low_risk_customers': [name for name, a in portfolio_renewal_analyses.items() if a.get('renewal_risk_score', a.get('overall_risk_score', 0)) < 30]
            }
            customer_name_for_report = f"{manager}'s Portfolio"
        else:
            # Single customer renewal
            renewal_analysis = _calculate_simple_renewal_risk(
                customer_name=customer_name,
                customer_ab=customer_ab,
                customer_csone=customer_csone,
                team_subs_df=team_subs_df,
                days=days,
                ext_incidents=ext_incidents  # Pass incidents for risk calculation
            )
            renewal_analysis['support_cases_from_snowflake'] = support_cases_from_snowflake
            customer_name_for_report = customer_name
        
        # Add external intelligence to renewal analysis
        renewal_analysis['external_bugs'] = ext_bugs
        renewal_analysis['external_incidents'] = ext_incidents
        renewal_analysis['software_defects'] = software_defects
        renewal_analysis['psirt_vulnerabilities'] = psirt_vulns
        
        # Enhance renewal risk score with incident impact
        if ext_incidents and len(ext_incidents) > 0:
            high_impact_incidents = sum(1 for inc in ext_incidents 
                                        if inc.get('status', '').lower() in ['investigating', 'identified', 'monitoring'])
            # Increase risk score based on high-impact incidents (max +15 points)
            incident_risk_penalty = min(15, high_impact_incidents * 3)  # 3 points per high-impact incident, max 15
            original_risk_score = renewal_analysis.get('renewal_risk_score', 0)
            renewal_analysis['renewal_risk_score'] = min(100, original_risk_score + incident_risk_penalty)
            renewal_analysis['incident_risk_penalty'] = incident_risk_penalty
            renewal_analysis['high_impact_incidents'] = high_impact_incidents
            
            # Update risk category if needed
            new_risk_score = renewal_analysis['renewal_risk_score']
            if new_risk_score >= 70:
                renewal_analysis['renewal_risk_category'] = 'CRITICAL'
            elif new_risk_score >= 50:
                renewal_analysis['renewal_risk_category'] = 'HIGH'
            elif new_risk_score >= 30:
                renewal_analysis['renewal_risk_category'] = 'MEDIUM'
            else:
                renewal_analysis['renewal_risk_category'] = 'LOW'
        
        # Generate renewal charts
        logger.info(f"[[RENEWAL_CHARTS]] Generating renewal charts...")
        try:
            renewal_chart_paths = create_renewal_charts(
                customer_ab=customer_ab,
                customer_csone=customer_csone,
                renewal_analysis=renewal_analysis,
                ext_incidents=ext_incidents,
                days=days
            )
            logger.info(f"[[RENEWAL_CHARTS]] Generated {len(renewal_chart_paths)} renewal charts")
        except Exception as e:
            logger.warning(f"[[WARNING]] Renewal chart generation failed: {e}")
            renewal_chart_paths = []
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 80
            status['message'] = ' Creating renewal report...'
            status['current_step'] = 'Report Generation'
        
        # Create renewal report
        ts = time.strftime("%Y%m%d_%H%M%S")
        if renewal_type == 'renewal_portfolio':
            tag = f"Portfolio_{manager.replace(' ','_')}_{technology.replace(' ','_').replace('&','and')}_{days}d_{ts}"
        else:
            tag = f"{customer_name.replace(' ','_')}_{technology.replace(' ','_').replace('&','and')}_{days}d_{ts}"
        out_dir = _ensure_outputs()
        base = str(out_dir / f"AdoptIQ_Report_Renewal_{tag}")
        
        # Generate renewal report (handles both single and portfolio)
        # Ensure CSConsole data variables are defined (they should be from filtering above)
        if 'customer_action_plans' not in locals():
            customer_action_plans = pd.DataFrame()
        if 'customer_customer_pulse' not in locals():
            customer_customer_pulse = pd.DataFrame()
        if 'customer_success_priorities' not in locals():
            customer_success_priorities = pd.DataFrame()
        
        renewal_word_path = _create_simple_renewal_report(
            base_path=base,
            customer_name=customer_name_for_report,
            technology=technology,
            days=days,
            renewal_analysis=renewal_analysis,
            customer_ab=customer_ab,
            customer_csone=customer_csone,
            ext_bugs=ext_bugs,
            ext_incidents=ext_incidents,
            software_defects=software_defects,
            psirt_vulns=psirt_vulns,
            portfolio_mode=(renewal_type == 'renewal_portfolio'),
            all_customers=all_customers if renewal_type == 'renewal_portfolio' else None,
            chart_paths=renewal_chart_paths,  # Pass charts to report
            customer_action_plans=customer_action_plans,
            customer_customer_pulse=customer_customer_pulse,
            customer_success_priorities=customer_success_priorities
        )
        
        if renewal_type == 'renewal_portfolio':
            success_msg = f"Portfolio renewal report generated for {len(all_customers)} customers"
        else:
            success_msg = f"Renewal report generated for {customer_name}"
        
        # Store Word report path early so it's available even if Excel fails
        with analysis_status_lock:
            status['progress'] = 90
            status['message'] = ' Creating Excel summary...'
            status['current_step'] = 'Excel Report Generation'
            status['word_report'] = renewal_word_path
        
        # Create Excel summary for renewal analysis
        excel_path = f"{base}.xlsx"
        
        # Map analyzer keys to expected keys (handle key name differences)
        overall_risk_score = renewal_analysis.get('overall_risk_score') or renewal_analysis.get('renewal_risk_score', 0)
        risk_level = renewal_analysis.get('risk_level') or renewal_analysis.get('renewal_risk_category', 'UNKNOWN')
        analysis_date = renewal_analysis.get('analysis_date', datetime.now().isoformat())
        next_review_date = renewal_analysis.get('next_review_date', (datetime.now() + timedelta(days=30)).isoformat())
        
        # Create renewal analysis summary DataFrame
        if renewal_type == 'renewal_portfolio':
            # Portfolio: create summary for all customers
            renewal_summary_data = []
            customer_analyses = renewal_analysis.get('customer_analyses', {})
            for cust_name, cust_analysis in customer_analyses.items():
                cust_risk_score = cust_analysis.get('overall_risk_score', 0)
                cust_risk_level = cust_analysis.get('risk_level', 'UNKNOWN')
                renewal_summary_data.append({
                    'Customer': cust_name,
                    'Overall_Risk_Score': cust_risk_score,
                    'Risk_Level': cust_risk_level,
                    'Analysis_Date': analysis_date,
                    'Next_Review_Date': next_review_date
                })
        else:
            # Single customer
            renewal_summary_data = [{
                'Customer': customer_name,
                'Overall_Risk_Score': overall_risk_score,
                'Risk_Level': risk_level,
                'Analysis_Date': analysis_date,
                'Next_Review_Date': next_review_date
            }]
        # Add risk component details (handle missing/empty risk_components gracefully)
        risk_components_data = []
        risk_components = renewal_analysis.get('risk_components', {})
        if isinstance(risk_components, dict):
            for component_name, component_data in risk_components.items():
                if isinstance(component_data, dict):
                    risk_components_data.append({
                        'Risk_Component': component_name.replace('_', ' ').title(),
                        'Score': component_data.get('score', 0),
                        'Details': component_data.get('details', 'N/A'),
                        'Trend': component_data.get('trend', 'N/A')
                    })
        # If no risk components, create from analysis metrics
        if not risk_components_data:
            # Create risk components from available analysis data
            if renewal_analysis.get('financial_metrics'):
                risk_components_data.append({
                    'Risk_Component': 'Financial Health',
                    'Score': 5,
                    'Details': 'Based on ARR and contract data',
                    'Trend': 'Stable'
                })
            if renewal_analysis.get('usage_metrics'):
                risk_components_data.append({
                    'Risk_Component': 'Usage & Adoption',
                    'Score': 5,
                    'Details': 'Based on product usage data',
                    'Trend': 'Stable'
                })
            if renewal_analysis.get('support_metrics'):
                risk_components_data.append({
                    'Risk_Component': 'Support Health',
                    'Score': 5,
                    'Details': 'Based on support case history',
                    'Trend': 'Stable'
                })
        
        # Create recommendations DataFrame
        recommendations_data = []
        recommendations = renewal_analysis.get('recommendations', [])
        if recommendations:
            for i, rec in enumerate(recommendations, 1):
                recommendations_data.append({
                    'Priority': i,
                    'Recommendation': rec
                })
        else:
            recommendations_data.append({
                'Priority': 1,
                'Recommendation': 'Review customer engagement and schedule account review'
            })
        
        # Build key metrics from available data
        key_metrics = renewal_analysis.get('key_metrics', {})
        if not key_metrics:
            key_metrics = {
                'Risk_Score': overall_risk_score,
                'Risk_Category': risk_level,
                'Analysis_Period': f'{days} days',
                'Key_Findings': len(renewal_analysis.get('key_findings', [])),
                'Recommendations': len(recommendations)
            }
        
        sheets = {
            "Renewal_Summary": pd.DataFrame(renewal_summary_data),
            "Risk_Components": pd.DataFrame(risk_components_data) if risk_components_data else pd.DataFrame([{'Risk_Component': 'Overall', 'Score': overall_risk_score, 'Details': 'Analysis complete', 'Trend': 'N/A'}]),
            "Recommendations": pd.DataFrame(recommendations_data),
            "Customer_Adoption_Barriers": customer_ab if not customer_ab.empty else pd.DataFrame(),
            "Customer_Support_Cases": customer_csone if not customer_csone.empty else pd.DataFrame(),
            "Customer_Action_Plans": customer_action_plans if not customer_action_plans.empty else pd.DataFrame(),
            "Customer_Customer_Pulse": customer_customer_pulse if not customer_customer_pulse.empty else pd.DataFrame(),
            "Customer_Success_Priorities": customer_success_priorities if not customer_success_priorities.empty else pd.DataFrame(),
            "Key_Metrics": pd.DataFrame([key_metrics])
        }
        
        # Create Excel file directly for renewal analysis
        try:
            with pd.ExcelWriter(excel_path, engine='xlsxwriter') as writer:
                workbook = writer.book
            
                # Create enhanced formats
                header_format = workbook.add_format({
                    'bold': True,
                    'text_wrap': True,
                    'valign': 'top',
                    'fg_color': '#4472C4',
                    'font_color': 'white',
                    'border': 1,
                    'font_size': 11
                })
            
                title_format = workbook.add_format({
                    'bold': True,
                    'font_size': 16,
                    'fg_color': '#2F4F4F',
                    'font_color': 'white',
                    'border': 1,
                    'align': 'center',
                    'valign': 'vcenter'
                })
            
                # Risk-based formatting
                high_risk_format = workbook.add_format({
                    'fg_color': '#FF6B6B',
                    'font_color': 'white',
                    'bold': True,
                    'border': 1
                })
            
                medium_risk_format = workbook.add_format({
                    'fg_color': '#FFE66D',
                    'font_color': 'black',
                    'bold': True,
                    'border': 1
                })
            
                low_risk_format = workbook.add_format({
                    'fg_color': '#4ECDC4',
                    'font_color': 'white',
                    'bold': True,
                    'border': 1
                })
            
                # Data formatting
                data_format = workbook.add_format({
                    'border': 1,
                    'valign': 'top',
                    'text_wrap': True
                })
            
                # Number formatting
                number_format = workbook.add_format({
                    'num_format': '#,##0',
                    'border': 1,
                    'align': 'right'
                })
            
                # Date formatting
                date_format = workbook.add_format({
                    'num_format': 'mm/dd/yyyy',
                    'border': 1,
                    'align': 'center'
                })
            
                # Write each sheet
                for sheet_name, df in sheets.items():
                    if not df.empty:
                        # Clean datetime columns for Excel compatibility
                        df_clean = _clean_datetime_columns_for_excel(df)
                        df_clean.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
                    
                        # Format the sheet
                        worksheet = writer.sheets[sheet_name]
                    
                        # Write title
                        worksheet.write(0, 0, f"{sheet_name.replace('_', ' ')} - {customer_name} Renewal Analysis", title_format)
                    
                        # Format headers
                        for col_num, value in enumerate(df_clean.columns.values):
                            worksheet.write(1, col_num, value, header_format)
                    
                        # Auto-adjust column widths (guard against NaN from empty columns)
                        for i, col in enumerate(df_clean.columns):
                            try:
                                content_max = df_clean[col].astype(str).map(len).max()
                                content_max = content_max if pd.notna(content_max) else 0
                            except (ValueError, TypeError):
                                content_max = 0
                            max_length = max(content_max, len(str(col)))
                            worksheet.set_column(i, i, min(max_length + 2, 50))
                    else:
                        # Create empty sheet with title
                        empty_df = pd.DataFrame({'Message': ['No data available for this analysis']})
                        empty_df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
                        worksheet = writer.sheets[sheet_name]
                        worksheet.write(0, 0, f"{sheet_name.replace('_', ' ')} - {customer_name} Renewal Analysis", title_format)
        except Exception as excel_error:
            logger.error(f"[[ERROR]] Renewal Excel generation failed: {excel_error}", exc_info=True)
            excel_path = None
        
        # Update status
        with analysis_status_lock:
            status['status'] = 'completed'
            status['progress'] = 100
            status['message'] = ' Customer renewal analysis completed successfully!'
            status['current_step'] = 'Completed'
            status['completion_time'] = datetime.now().isoformat()
            completion_time = status['completion_time']
            report_type = status.get('report_type', 'renewal')
            manager = status.get('manager', '')
            technology = status.get('technology', status.get('tech', ''))
            customer_name_val = status.get('customer_name', '')
            start_time = status.get('start_time', '')
            status['word_report'] = renewal_word_path
            status['excel_report'] = excel_path if excel_path else None
            # Store only JSON-serializable summary of renewal analysis
            status['renewal_summary'] = {
                'risk_score': overall_risk_score,
                'risk_level': risk_level,
                'customer': customer_name,
                'analysis_date': analysis_date
            }

        # Run non-locking side effects after releasing status lock
        auto_audit_report(analysis_id)
        record_report_completion(
            analysis_id, report_type, manager, technology, customer_name_val,
            'completed', start_time, completion_time
        )
        try:
            insights_payload = _build_insights_payload(status, 'Renewal analysis completed')
            if risk_level is not None and str(risk_level).strip():
                insights_payload['risk_theme'] = str(risk_level).strip()
            store_report_insights(
                analysis_id, report_type, manager, technology, customer_name_val, insights_payload
            )
        except Exception:
            pass
        
        logger.info(f"[[OK]] Customer renewal analysis completed: {analysis_id}")
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error in customer renewal analysis: {e}")
        with analysis_status_lock:
            if analysis_id in analysis_status:
                analysis_status[analysis_id]['status'] = 'error'
                analysis_status[analysis_id]['message'] = f' Analysis failed: {str(e)}'
                analysis_status[analysis_id]['error'] = str(e)
    finally:
        # Ensure database connection is closed
        if 'ctx' in locals() and ctx is not None:
            try:
                ctx.close()
                logger.info(f"[[CLEANUP]] Database connection closed for {analysis_id}")
            except Exception as e:
                logger.warning(f"[[WARNING]] Error closing database connection: {e}")


def run_comprehensive_analysis(analysis_id):
    """Run analysis using EXACT logic from working script with enhanced prompts"""
    try:
        # Enhanced error handling and logging
        logger.info(f"Starting comprehensive analysis for {analysis_id}")
        
        with analysis_status_lock:
            status = analysis_status[analysis_id]
            manager = status['manager']
            tech = status['tech']
            days = status['days']
            
        logger.info(f"DEBUGGING - Comprehensive Analysis starting for {manager} with {tech} technology, {days} days")
        
        # Update status (thread-safe)
        update_analysis_status(analysis_id, {
            'status': 'running',
            'progress': 8,
            'message': ' Connecting to Snowflake Data Warehouse...',
            'current_step': 'Database Connection',
            'step_start_time': datetime.now().isoformat(),
            'estimated_completion': (datetime.now() + pd.Timedelta(minutes=15)).isoformat()
        })
        
        # [EXACT SAME LOGIC AS YOUR WORKING SCRIPT - SHORTENED FOR BREVITY]
        # Get team roster for the selected manager
        # Get status values we need (thread-safe)
        with analysis_status_lock:
            status = analysis_status[analysis_id]
            manager_name = status['manager']
            customer_name_filter = status.get('customer_name')
            subscription_id_filter = status.get('subscription_id')
        
        team_roster_df = pd.DataFrame(TEAM_ROSTER, columns=["manager_name","cssm_name","cssm_email"])
        if manager_name != "All Managers":
            team_roster_df = team_roster_df[team_roster_df["manager_name"] == manager_name]
        cssm_emails = team_roster_df["cssm_email"].dropna().unique().tolist()
        
        # Update status (thread-safe)
        update_analysis_status(analysis_id, {
            'progress': 20,
            'message': ' Fetching team subscriptions and customer data...',
            'current_step': 'Team Data Retrieval',
            'step_start_time': datetime.now().isoformat(),
            'estimated_completion': (datetime.now() + pd.Timedelta(minutes=12)).isoformat()
        })
        
        # Connect to Snowflake and get subscriptions
        ctx = _connect_with_keeper()
        
        if ctx is None:
            error_msg = (
                "❌ CRITICAL: Snowflake database connection failed.\n\n"
                "Cannot generate comprehensive report without database access."
            )
            logger.error(f"[[ERROR]] {error_msg}")
            update_analysis_status(analysis_id, {
                'status': 'error',
                'progress': 0,
                'message': ' Database connection failed',
                'error': error_msg,
                'current_step': 'Connection Failed'
            })
            return
        
        # Single customer/subscription: fetch directly from Snowflake (manager not used)
        subscription_id_val = (subscription_id_filter or '').strip()
        customer_name_val = (customer_name_filter or '').strip()
        single_customer_mode = bool(subscription_id_val or customer_name_val)
        
        if single_customer_mode:
            if subscription_id_val:
                sub_data = fetch_subscription_data(subscription_id_val, days)
                if not sub_data or not sub_data.get('found'):
                    error_msg = f"❌ No subscription found with ID '{subscription_id_val}'."
                    logger.error(f"[[ERROR]] No subscription found with ID '{subscription_id_val}'.")
                    update_analysis_status(analysis_id, {'status': 'error', 'progress': 0, 'message': ' Subscription not found', 'error': error_msg, 'current_step': 'Subscription Not Found'})
                    return
                cust_name = sub_data.get('customer_name') or 'Unknown'
                acc_id = sub_data.get('account_id')
                team_subs_df_unfiltered = pd.DataFrame({
                    'BU_NAME': [cust_name],
                    'ACCOUNT_ID_C': [acc_id] if acc_id else [None],
                    'SUBSCRIPTION_ID': [subscription_id_val],
                    'CSSM_EMAIL': [sub_data.get('cssm_email', '') or '']
                })
                logger.info(f"[[OK]] Comprehensive single-subscription: {subscription_id_val} ({cust_name})")
            else:
                sub_results = search_subscriptions_by_customer(customer_name_val, limit=50)
                if not sub_results:
                    error_msg = f"❌ No subscriptions found for customer '{customer_name_val}'."
                    logger.error(f"[[ERROR]] No subscriptions found for customer '{customer_name_val}'.")
                    update_analysis_status(analysis_id, {'status': 'error', 'progress': 0, 'message': ' Customer not found', 'error': error_msg, 'current_step': 'Customer Not Found'})
                    return
                canonical_name = (sub_results[0].get('BU_NAME') or customer_name_val)
                same_customer = [r for r in sub_results if (r.get('BU_NAME') or '') == canonical_name]
                if not same_customer:
                    same_customer = [sub_results[0]]
                team_subs_df_unfiltered = pd.DataFrame({
                    'BU_NAME': [r.get('BU_NAME') or canonical_name for r in same_customer],
                    'ACCOUNT_ID_C': [r.get('ACCOUNT_ID_C') for r in same_customer],
                    'SUBSCRIPTION_ID': [r.get('SUBSCRIPTION_ID') for r in same_customer],
                    'CSSM_EMAIL': [r.get('CSSM_EMAIL', '') for r in same_customer]
                })
                logger.info(f"[[OK]] Comprehensive single-customer: '{canonical_name}' ({len(same_customer)} subscription(s))")
            team_roster_full = pd.DataFrame(TEAM_ROSTER, columns=["manager_name", "cssm_name", "cssm_email"])
            team_subs_df_unfiltered = team_subs_df_unfiltered.merge(team_roster_full, left_on="CSSM_EMAIL", right_on="cssm_email", how="left")
            team_subs_df = team_subs_df_unfiltered.copy()
        else:
            team_subs_df_unfiltered = get_subscriptions_for_team(ctx, cssm_emails)
            team_subs_empty = team_subs_df_unfiltered is None or (hasattr(team_subs_df_unfiltered, 'empty') and team_subs_df_unfiltered.empty)
            if team_subs_empty:
                error_msg = (
                    f"❌ CRITICAL: No subscriptions found for team '{manager_name}'.\n\n"
                    "Cannot generate comprehensive report without team subscription data."
                )
                logger.error(f"[[ERROR]] {error_msg}")
                update_analysis_status(analysis_id, {
                    'status': 'error',
                    'progress': 0,
                    'message': f' No team subscriptions found',
                    'error': error_msg,
                    'current_step': 'No Team Data'
                })
                return
            
            logger.info(f"[[CUSTOMER_COUNT]] Comprehensive report - Retrieved {len(team_subs_df_unfiltered)} UNFILTERED team subscriptions")
            team_subs_df_unfiltered = team_subs_df_unfiltered.merge(team_roster_df, left_on="CSSM_EMAIL", right_on="cssm_email", how="left")
            has_customer_filter = customer_name_filter and customer_name_filter.strip()
            has_subscription_filter = subscription_id_filter and subscription_id_filter.strip()
            if has_customer_filter or has_subscription_filter:
                filtered_df, filter_error = filter_subscriptions_by_criteria(
                    team_subs_df_unfiltered,
                    customer_name=customer_name_filter,
                    subscription_id=subscription_id_filter
                )
                if filter_error:
                    raise Exception(filter_error)
                team_subs_df = filtered_df
                logger.info(f"[[FILTER]] Comprehensive report - Filtered to {len(team_subs_df)} subscriptions")
            else:
                team_subs_df = team_subs_df_unfiltered.copy()
                logger.info(f"[[FILTER]] Comprehensive report - No filter - using all {len(team_subs_df)} subscriptions")
            if team_subs_df.empty:
                raise Exception(f"No subscriptions found for team '{manager_name}'")
        
        # cssm_name can be NaN after left merge when CSSM not in roster; avoid NaN in dict
        cssm_col = team_subs_df.get('cssm_name', pd.Series(dtype=object))
        cssm_lookup = pd.Series(cssm_col.fillna('').values, index=team_subs_df.BU_NAME).to_dict()
        sub_ids = team_subs_df["SUBSCRIPTION_ID"].dropna().unique().tolist()
        account_ids = team_subs_df["ACCOUNT_ID_C"].dropna().unique().tolist()
        team_customer_names = team_subs_df["BU_NAME"].dropna().unique().tolist()
        
        # Get days from status (thread-safe)
        with analysis_status_lock:
            days = analysis_status[analysis_id]['days']
        
        # Update status (thread-safe)
        update_analysis_status(analysis_id, {
            'progress': 35,
            'message': ' Analyzing adoption barriers and customer challenges...',
            'current_step': 'Adoption Barrier Analysis'
        })
        
        # Fetch adoption barriers
        ab_raw = fetch_adoption_barriers(ctx, account_ids, days)
        ab_raw_empty = ab_raw is None or (hasattr(ab_raw, 'empty') and ab_raw.empty)
        if not ab_raw_empty and "ACCOUNT_ID_C" in ab_raw.columns:
            ab_raw = ab_raw.merge(team_subs_df[["ACCOUNT_ID_C","BU_NAME","CSSM_EMAIL"]].drop_duplicates(), on="ACCOUNT_ID_C", how="left")
        
        # Fetch CSConsole data for comprehensive analysis
        # Get tech from status (thread-safe)
        with analysis_status_lock:
            tech = analysis_status[analysis_id]['tech']
        
        # Update status (thread-safe)
        update_analysis_status(analysis_id, {
            'progress': 40,
            'message': ' Fetching CSConsole data (Action Plans, Customer Pulse, Success Priorities)...',
            'current_step': 'CSConsole Data Integration'
        })
        
        csconsole_action_plans = fetch_csconsole_action_plans(ctx, account_ids, days)
        csconsole_customer_pulse = fetch_csconsole_customer_pulse(ctx, account_ids, days)
        csconsole_success_priorities = fetch_csconsole_success_priorities(ctx, account_ids, days)
        csconsole_adoption_barriers = fetch_csconsole_adoption_barriers(ctx, account_ids, days)
        
        logger.info(f" Found {len(csconsole_action_plans)} action plans, {len(csconsole_customer_pulse)} customer pulse records, "
                    f"{len(csconsole_success_priorities)} success priorities, and {len(csconsole_adoption_barriers)} adoption barriers from CSConsole.")
        
        ab_scoped = _apply_scope_filter_ab(ab_raw, tech, days)
        ab_norm = _prepare_ab(ab_scoped, team_subs_df)
        
        # Update status (thread-safe)
        update_analysis_status(analysis_id, {
            'progress': 50,
            'message': ' Processing CSOne cases and technical escalations...',
            'current_step': 'CSOne Case Processing'
        })
        
        # Get csone_file from status (thread-safe)
        with analysis_status_lock:
            csone_file_path = analysis_status[analysis_id].get('csone_file')
        
        # Process CSOne data with enhanced status reporting
        if csone_file_path:
            # Resolve path safely (prevents path traversal)
            resolved_path = _resolve_csone_path_safe(csone_file_path)
            if not resolved_path:
                logger.warning(f"[[WARNING]] CSOne file path invalid or outside allowed directories: {os.path.basename(csone_file_path)}")
                update_analysis_status(analysis_id, {
                    'csone_import_status': 'invalid',
                    'csone_import_message': 'File path invalid or not found in uploads'
                })
                csone_df_raw = pd.DataFrame()
            else:
                csone_filename = os.path.basename(resolved_path)
                # Check if the CSOne file is actually an output file (not input data)
                is_output_file = False
                if 'AdoptIQ_' in csone_filename and ('_Portfolio_' in csone_filename or '_Compact_' in csone_filename or '_Renewal_' in csone_filename):
                    is_output_file = True
                
                if is_output_file:
                    logger.warning(f"[[WARNING]] CSOne file appears to be an output file, not input data: {csone_filename}")
                    update_analysis_status(analysis_id, {
                        'csone_import_status': 'invalid',
                        'csone_import_message': f'️ File appears to be an output file, not input data: {csone_filename}'
                    })
                    logger.info(f"[[FILE]] Skipping CSOne processing - file appears to be output data, not input")
                    csone_df_raw = pd.DataFrame()
                else:
                    update_analysis_status(analysis_id, {
                        'csone_import_status': 'uploaded',
                        'csone_import_message': f' CSOne data uploaded by user: {csone_filename}'
                    })
                    logger.info(f"[[FILE]] Processing uploaded CSOne file: {resolved_path}")
                    csone_df_raw = load_csone_excel(resolved_path)
        else:
            update_analysis_status(analysis_id, {
                'csone_import_status': 'none',
                'csone_import_message': 'ℹ️ No CSOne file provided - analysis will proceed with CSConsole data only'
            })
            logger.info(f"[[FILE]] No CSOne file provided - proceeding with Adoption Barriers data only")
            csone_df_raw = pd.DataFrame()
        csone_df_prepared = _prepare_csone(csone_df_raw, team_subs_df)
        csone_df = _apply_scope_filter_csone(csone_df_prepared, tech, days, sub_ids, team_customer_names)
        
        # Update CSOne import status with results (thread-safe)
        if not csone_df.empty:
            update_analysis_status(analysis_id, {
                'csone_import_status': 'success',
                'csone_import_message': f' CSOne data successfully processed: {len(csone_df)} cases found'
            })
            logger.info(f"[[OK]] CSOne data processed successfully: {len(csone_df)} cases")
        else:
            update_analysis_status(analysis_id, {
                'csone_import_status': 'warning',
                'csone_import_message': '️ CSOne data processed but no cases found in scope'
            })
            logger.warning(f"[[WARNING]] CSOne data processed but no cases found in scope")
        
        # Update status (thread-safe)
        update_analysis_status(analysis_id, {
            'progress': 65,
            'message': ' Gathering external intelligence and bug correlations...',
            'current_step': 'External Intelligence Gathering'
        })
        
        # Fetch ARR data for charts and enrichment (required for comprehensive report)
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_arr_data, ctx, account_ids)
                try:
                    arr_data = future.result(timeout=30)
                    logger.info(f"[[ARR]] Fetched {len(arr_data)} ARR records for comprehensive report")
                except FutureTimeoutError:
                    logger.warning(f"[[TIMEOUT]] ARR data query timed out")
                    arr_data = pd.DataFrame()
                except Exception as e:
                    logger.warning(f"[[ARR]] Failed to fetch ARR data: {e}")
                    arr_data = pd.DataFrame()
        except Exception as e:
            logger.warning(f"[[ARR]] ARR fetch error: {e}")
            arr_data = pd.DataFrame()
        
        # External intelligence
        ext_bugs = fetch_help_webex_bugs()
        ext_incidents = fetch_status_incidents()
        
        # Extract software defects (BST/CSC IDs) and PSIRT vulnerabilities from data
        logger.info(f"[[DEFECTS]] Extracting software defects and PSIRT vulnerabilities for comprehensive report...")
        try:
            software_defects = extract_software_defects(csone_df, ab_norm) if not csone_df.empty else {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}}
            psirt_vulns = extract_psirt_vulnerabilities(csone_df, ab_norm) if not csone_df.empty else {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}}
            logger.info(f"[[OK]] Extracted {software_defects.get('total_defects', 0)} defects and {psirt_vulns.get('total_vulnerabilities', 0)} vulnerabilities")
        except Exception as e:
            logger.warning(f"[[WARNING]] Defect/vulnerability extraction failed: {e}")
            software_defects = {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}}
            psirt_vulns = {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}}
        
        # Validate data sources before report generation
        logger.info(f"[[VALIDATION]] Validating data sources for comprehensive report...")
        try:
            raise_validation_error_if_invalid(
                report_type='comprehensive',
                snowflake_ctx=ctx,
                team_subs_df=team_subs_df,
                ab_data=ab_norm,
                csone_data=csone_df,
                csconsole_action_plans=csconsole_action_plans,
                csconsole_customer_pulse=csconsole_customer_pulse,
                csconsole_success_priorities=csconsole_success_priorities
            )
            logger.info(f"[[VALIDATION]] All required data sources validated successfully")
        except DataSourceValidationError as e:
            error_msg = str(e)
            logger.error(f"[[VALIDATION]] Data validation failed: {error_msg}")
            update_analysis_status(analysis_id, {
                'status': 'error',
                'progress': 0,
                'message': ' Data validation failed - see logs for details',
                'error': error_msg,
                'current_step': 'Validation Failed'
            })
            return
        
        # Integrity checks (additional validation)
        reason = _integrity_checks(ab_norm, csone_df)
        if reason:
            error_msg = f"Data integrity check failed: {reason}"
            logger.error(f"[[ERROR]] {error_msg}")
            update_analysis_status(analysis_id, {
                'status': 'error',
                'progress': 0,
                'message': f' Data integrity check failed',
                'error': error_msg,
                'current_step': 'Integrity Check Failed'
            })
            return
        
        # Generate reports
        ts = time.strftime("%Y%m%d_%H%M%S")
        tag = f"{status['manager'].replace(' ','_')}_{status['tech'].replace(' ','_').replace('&','and')}_{status['days']}d_{ts}"
        out_dir = _ensure_outputs()
        base = str(out_dir / f"AdoptIQ_Report_{tag}")
        
        status['progress'] = 70
        status['message'] = '[AI] Generating AI-powered portfolio analysis with CircuIT...'
        status['current_step'] = 'AI Portfolio Analysis'
        
        # === USE CLEAN EXECUTIVE REPORT BUILDER (NO MARKDOWN ISSUES) ===
        from executive_report_builder import ExecutiveReportBuilder
        from adoptiq_backend import add_executive_visual_dashboard
        
        # Create report builder
        report_builder = ExecutiveReportBuilder()
        
        # CRITICAL FIX: Use unified customer counting function for comprehensive report
        # Use UNFILTERED team_subs_df for customer counting (we want ALL customers)
        # Filtered data is for report sections, but customer count should be comprehensive
        # team_subs_df_unfiltered is preserved above and should be used here
        team_subs_for_customer_counting = team_subs_df_unfiltered if not team_subs_df_unfiltered.empty else team_subs_df
        logger.info(f"[[CUSTOMER_COUNT]] Comprehensive report - Using {len(team_subs_for_customer_counting)} UNFILTERED subscriptions for customer counting (vs {len(team_subs_df)} filtered)")
        
        all_customers_comprehensive = _get_all_customers_from_all_sources(
            ab_norm=ab_norm,
            csone_df=csone_df,
            team_subs_df=team_subs_for_customer_counting,  # Use UNFILTERED for customer counting
            csconsole_action_plans=csconsole_action_plans,  # Use UNFILTERED
            csconsole_customer_pulse=csconsole_customer_pulse,  # Use UNFILTERED
            csconsole_success_priorities=csconsole_success_priorities,  # Use UNFILTERED
            csconsole_adoption_barriers=csconsole_adoption_barriers  # Use UNFILTERED
        )
        logger.info(f"[[CUSTOMER_COUNT]] Comprehensive report - Total unique customers from all sources: {len(all_customers_comprehensive)}")
        
        # Calculate portfolio metrics (defensive: ab_norm/csone_df are never None in this flow, but guard for safety)
        _ab = ab_norm if ab_norm is not None and hasattr(ab_norm, 'empty') else pd.DataFrame()
        _cs = csone_df if csone_df is not None and hasattr(csone_df, 'empty') else pd.DataFrame()
        portfolio_metrics = {
            'total_customers': len(all_customers_comprehensive),
            'total_barriers': len(_ab) if not _ab.empty else 0,
            'total_cases': len(_cs) if not _cs.empty else 0,
            'bems_count': len(_cs[_cs['Case Status'].astype(str).str.contains('BEMS', case=False, na=False)]) if not _cs.empty and 'Case Status' in _cs.columns else 0,
            'high_risk_customers': 0,  # Will be calculated by AI
            'medium_risk_customers': 0,
            'low_risk_customers': 0,
            'healthy_customers': 0,
            'p1_cases': len(_cs[_cs['Highest Priority'].astype(str).str.contains('P1', case=False, na=False)]) if not _cs.empty and 'Highest Priority' in _cs.columns else 0,
            'p2_cases': len(_cs[_cs['Highest Priority'].astype(str).str.contains('P2', case=False, na=False)]) if not _cs.empty and 'Highest Priority' in _cs.columns else 0,
            'p3_cases': len(_cs[_cs['Highest Priority'].astype(str).str.contains('P3', case=False, na=False)]) if not _cs.empty and 'Highest Priority' in _cs.columns else 0,
            'p4_cases': len(_cs[_cs['Highest Priority'].astype(str).str.contains('P4', case=False, na=False)]) if not _cs.empty and 'Highest Priority' in _cs.columns else 0,
            'health_score': 'C',  # Default, will be updated by AI
            'trend_direction': 'Stable'  # Default
        }
        
        # Add professional title page
        report_builder.add_title_page(status['manager'], status['tech'], status['days'], portfolio_metrics)
        
        # Add visual dashboard after title page
        logger.info(f"[[VISUAL]] Adding executive visual dashboard...")
        add_executive_visual_dashboard(report_builder.doc, portfolio_metrics)
        
        # Generate additional charts for comprehensive report (trends, distributions, etc.)
        logger.info(f"[[CHARTS]] Generating additional charts for comprehensive report...")
        arr_impact = {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
        feature_requests = {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
        try:
            # Calculate ARR impact for chart generation
            if not ab_norm.empty and not arr_data.empty:
                arr_impact = calculate_arr_impact_for_issues(ab_norm, arr_data)
            else:
                arr_impact = {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
            
            # Analyze feature requests
            if csone_df is not None and not csone_df.empty:
                feature_requests = analyze_feature_requests(csone_df, arr_data if arr_data is not None and not arr_data.empty else pd.DataFrame())
            else:
                feature_requests = {'total_requests': 0, 'top_features': [], 'customer_examples': [], 'total_arr_impact': 0}
            
            comprehensive_chart_paths = create_executive_charts(
                ab_norm=ab_norm,
                arr_data=arr_data,
                arr_impact=arr_impact,
                csone_df=csone_df,
                feature_requests=feature_requests
            )
            logger.info(f"[[CHARTS]] Generated {len(comprehensive_chart_paths)} additional charts for comprehensive report")
            
            # Add charts section to comprehensive report
            if comprehensive_chart_paths and len(comprehensive_chart_paths) > 0:
                report_builder.add_page_break()
                report_builder.add_heading('Visual Analysis & Trends', level=1)
                
                charts_added = 0
                for chart_path in comprehensive_chart_paths:
                    if os.path.exists(chart_path):
                        try:
                            # Add chart title based on filename
                            if 'support_cases_trend' in chart_path:
                                report_builder.add_heading('Support Cases Over Time', level=2)
                            elif 'severity_distribution' in chart_path:
                                report_builder.add_heading('Case Severity Distribution', level=2)
                            elif 'top_customers' in chart_path:
                                report_builder.add_heading('Top Customers by Case Volume', level=2)
                            elif 'arr_impact' in chart_path:
                                report_builder.add_heading('ARR Impact by Issue', level=2)
                            elif 'arr_distribution' in chart_path:
                                report_builder.add_heading('Customer ARR Distribution', level=2)
                            elif 'bems_escalations' in chart_path:
                                report_builder.add_heading('BEMS Escalations by Customer', level=2)
                            elif 'executive_risk_dashboard' in chart_path:
                                report_builder.add_heading('Executive Risk Dashboard', level=2)
                            
                            report_builder.doc.add_picture(chart_path, width=Inches(6.5))
                            report_builder.doc.add_paragraph()  # Spacing
                            charts_added += 1
                            logger.info(f"[[OK]] Chart added to comprehensive report: {chart_path}")
                        except Exception as e:
                            logger.error(f"[[ERROR]] Failed to add chart {chart_path}: {e}")
                
                logger.info(f"[[OK]] Total charts added to comprehensive report: {charts_added}")
        except Exception as e:
            logger.warning(f"[[WARNING]] Chart generation for comprehensive report failed: {e}")
        
        # === 1. Generate Portfolio-Level Summary ===
        # Check for cancellation before AI analysis
        if check_cancellation(analysis_id):
            update_analysis_status(analysis_id, {
                'status': 'cancelled',
                'message': ' Analysis cancelled by user'
            })
            return
        
        logger.info(f"[[AI]] Calling CircuIT AI for Portfolio Summary...")
        
        # CRITICAL FIX: Filter CSConsole data by technology and team customers OUTSIDE try block
        # This ensures these variables are always available for customer deep dives below
        # These filtered datasets are used both in portfolio analysis AND customer-specific analysis
        logger.info(f"[[FILTER]] Filtering CSConsole data by technology: {status['tech']}")
        filtered_action_plans = _filter_csconsole_data_by_technology(csconsole_action_plans, status['tech'], team_customer_names)
        filtered_customer_pulse = _filter_csconsole_data_by_technology(csconsole_customer_pulse, status['tech'], team_customer_names)
        filtered_success_priorities = _filter_csconsole_data_by_technology(csconsole_success_priorities, status['tech'], team_customer_names)
        filtered_adoption_barriers = _filter_csconsole_data_by_technology(csconsole_adoption_barriers, status['tech'], team_customer_names)
        
        logger.info(f"[[FILTER]] CSConsole data after filtering - Action Plans: {len(filtered_action_plans)}, "
                    f"Customer Pulse: {len(filtered_customer_pulse)}, "
                    f"Success Priorities: {len(filtered_success_priorities)}, "
                    f"Adoption Barriers: {len(filtered_adoption_barriers)}")
        
        # CRITICAL FIX: Update portfolio_metrics customer count AFTER filtering
        # Customer count should still use UNFILTERED data (already calculated above)
        # But we log here for consistency
        logger.info(f"[[CUSTOMER_COUNT]] Portfolio metrics using {portfolio_metrics['total_customers']} customers (unfiltered)")
        
        try:
            ab_norm_empty = ab_norm is None or (hasattr(ab_norm, 'empty') and ab_norm.empty)
            if not ab_norm_empty:
                ab_counts = ab_norm['customer_name'].value_counts().reset_index()
                ab_counts.columns = ['customer_name', 'ab_count']
            else:
                ab_counts = pd.DataFrame(columns=['customer_name', 'ab_count'])

            csone_empty = csone_df is None or (hasattr(csone_df, 'empty') and csone_df.empty)
            if not csone_empty:
                csone_counts = csone_df['customer_name'].value_counts().reset_index()
                csone_counts.columns = ['customer_name', 'csone_count']
            else:
                csone_counts = pd.DataFrame(columns=['customer_name', 'csone_count'])

            # FIXED: Show ALL customers by engagement
            engagement = pd.merge(ab_counts, csone_counts, on='customer_name', how='outer').fillna(0)
            engagement['total_engagements'] = engagement['ab_count'] + engagement['csone_count']
            engagement_summary = engagement.sort_values('total_engagements', ascending=False)

            # Extract software defects and PSIRT for briefing (comprehensive flow)
            try:
                _sw_defects = extract_software_defects(csone_df, ab_norm) if csone_df is not None and not csone_df.empty else {'total_defects': 0, 'defect_by_customer': {}}
                _psirt = extract_psirt_vulnerabilities(csone_df, ab_norm) if csone_df is not None and not csone_df.empty else {'total_vulnerabilities': 0, 'vulnerability_by_customer': {}}
            except Exception:
                _sw_defects = {'total_defects': 0, 'defect_by_customer': {}}
                _psirt = {'total_vulnerabilities': 0, 'vulnerability_by_customer': {}}

            portfolio_briefing = _create_briefing_book(f"{status['manager']}'s Portfolio", ab_norm, csone_df, ext_bugs, ext_incidents, [], pd.DataFrame(), None, engagement_summary, {
                'action_plans': filtered_action_plans,
                'customer_pulse': filtered_customer_pulse,
                'success_priorities': filtered_success_priorities,
                'adoption_barriers': filtered_adoption_barriers
            }, arr_data=arr_data if arr_data is not None and not arr_data.empty else None,
                arr_impact=arr_impact if arr_impact and arr_impact.get('top_issues') else None,
                feature_requests=feature_requests if feature_requests and feature_requests.get('total_requests', 0) > 0 else None,
                software_defects=_sw_defects if _sw_defects.get('total_defects', 0) > 0 else None,
                psirt_vulns=_psirt if _psirt.get('total_vulnerabilities', 0) > 0 else None)
            portfolio_prompt = PROMPT_PORTFOLIO_TEMPLATE.format(MANAGER=status['manager'], TECHNOLOGY=status['tech'])
            portfolio_summary = generate_llm_response(portfolio_prompt, portfolio_briefing)
            
            # Check if AI response is valid
            if portfolio_summary and not portfolio_summary.startswith("ERROR:"):
                # Use clean builder to parse AI output and remove ALL markdown symbols
                report_builder.parse_ai_output_and_add(portfolio_summary)
                logger.info(f"[[OK]] Portfolio AI analysis completed successfully - NO markdown symbols")
            else:
                logger.warning(f"[[WARNING]] Portfolio AI analysis failed: {portfolio_summary}")
                # Add a fallback portfolio summary with clean formatting
                report_builder.add_heading(f"AdoptIQ Executive Analysis: {status['manager']}'s Portfolio", level=1)
                report_builder.add_heading("Portfolio Overview", level=2)
                report_builder.add_paragraph("AI analysis temporarily unavailable. Data processed successfully.")
                report_builder.add_paragraph(f"Manager: {status['manager']}")
                report_builder.add_paragraph(f"Technology Focus: {status['tech']}")
                report_builder.add_paragraph(f"Analysis Period: {status['days']} days")
                report_builder.add_paragraph(f"Total Customers: {len(engagement) if not engagement.empty else 0}")
                report_builder.add_paragraph(f"Total Adoption Barriers: {len(ab_norm) if not ab_norm.empty else 0}")
                report_builder.add_paragraph(f"Total TAC Cases: {len(csone_df) if not csone_df.empty else 0}")
                
        except Exception as portfolio_error:
            logger.error(f"[[ERROR]] Portfolio AI analysis failed: {portfolio_error}")
            # Add a fallback portfolio summary with clean formatting
            report_builder.add_heading(f"AdoptIQ Executive Analysis: {status['manager']}'s Portfolio", level=1)
            report_builder.add_heading("Portfolio Overview", level=2)
            report_builder.add_paragraph("AI analysis encountered an error. Data processed successfully.")
            report_builder.add_paragraph(f"Manager: {status['manager']}")
            report_builder.add_paragraph(f"Technology Focus: {status['tech']}")
            report_builder.add_paragraph(f"Analysis Period: {status['days']} days")
            report_builder.add_paragraph(f"Error: {str(portfolio_error)}")

        # === 2. Generate Customer-by-Customer Deep Dives ===
        status['progress'] = 75
        status['message'] = ' Generating AI-powered customer deep dives and storyboards...'
        status['current_step'] = 'AI Customer Analysis'
        status['step_start_time'] = datetime.now().isoformat()
        status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=8)).isoformat()
        
        # FIXED: Use comprehensive function to get ALL customers from ALL available data sources
        # Use UNFILTERED team_subs_df for customer counting (we want ALL customers, not just filtered ones)
        # The filtered data is used for report sections, but deep dives should cover all customers
        # team_subs_for_customer_counting is already calculated above using unfiltered data
        all_customers_set = _get_all_customers_from_all_sources(
            ab_norm=ab_norm,
            csone_df=csone_df,
            team_subs_df=team_subs_for_customer_counting,  # Use UNFILTERED for customer counting
            csconsole_action_plans=csconsole_action_plans,  # Use UNFILTERED for deep dives
            csconsole_customer_pulse=csconsole_customer_pulse,  # Use UNFILTERED for deep dives
            csconsole_success_priorities=csconsole_success_priorities,  # Use UNFILTERED for deep dives
            csconsole_adoption_barriers=csconsole_adoption_barriers  # Use UNFILTERED for deep dives
        )
        all_customers = list(all_customers_set)
        logger.info(f"[[CUSTOMER_COUNT]] Deep dives will cover {len(all_customers)} customers from all sources")
        
        if not all_customers:
            logger.info("No customer activity found in any data sources. No deep dives to generate.")
        else:
            logger.info(f"[[DATA]] Combined unique customers from ALL sources: {len(all_customers)}")
            logger.info(f"[[DATA]] Customer names: {all_customers}")

        logger.info(f"[[BULLSEYE]] Found {len(all_customers)} customers with activity. Generating AI-powered deep dives...")
        
        # Update progress for customer deep dives
        status['progress'] = 75
        status['message'] = f" Found {len(all_customers)} customers with activity. Generating AI-powered deep dives..."
        
        # Calculate ETA based on customers remaining
        elapsed_time = 0
        if status.get('start_time'):
            try:
                start_str = str(status.get('start_time', '')).replace('Z', '+00:00')
                if start_str:
                    elapsed_time = (datetime.now() - datetime.fromisoformat(start_str)).total_seconds()
            except (ValueError, TypeError):
                pass
        estimated_total_time = elapsed_time / 0.75  # We're at 75% progress
        remaining_time = estimated_total_time - elapsed_time
        status['eta_seconds'] = int(remaining_time)
        status['estimated_completion'] = (datetime.now() + pd.Timedelta(seconds=remaining_time)).isoformat()
        
        # Add customer processing progress
        status['customer_progress'] = {
            'total': len(all_customers),
            'completed': 0,
            'current': None
        }
        
        # Track actually analyzed customers (not skipped)
        customers_actually_analyzed = 0

        for i, customer_name in enumerate(all_customers, 1):
            # Update progress for each customer
            customer_progress = 75 + int((i / len(all_customers)) * 15)  # 75-90% range
            status['progress'] = customer_progress
            status['customer_progress']['completed'] = i - 1
            status['customer_progress']['current'] = customer_name
            
            # Check for cancellation before each customer analysis
            if check_cancellation(analysis_id):
                update_analysis_status(analysis_id, {
                    'status': 'cancelled',
                    'message': ' Analysis cancelled by user'
                })
                return
            
            status['message'] = f' ({i}/{len(all_customers)}) Generating AI StoryBoard for: {customer_name}...'
            status['current_step'] = f'AI Customer Analysis ({i}/{len(all_customers)})'
            status['step_start_time'] = datetime.now().isoformat()
            remaining_customers = len(all_customers) - i
            estimated_minutes = remaining_customers * 0.5  # Estimate 30 seconds per customer
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=estimated_minutes)).isoformat()
            
            logger.info(f"  [[LIST]] ({i}/{len(all_customers)}) Generating AI StoryBoard for: {customer_name}...")
            ab_norm_safe_check = ab_norm is not None and (hasattr(ab_norm, 'columns') and 'customer_name' in ab_norm.columns)
            cust_ab = ab_norm[ab_norm['customer_name'] == customer_name] if ab_norm_safe_check else pd.DataFrame()
            csone_df_safe_check = csone_df is not None and (hasattr(csone_df, 'columns') and 'customer_name' in csone_df.columns)
            cust_csone = csone_df[csone_df['customer_name'] == customer_name] if csone_df_safe_check else pd.DataFrame()

            # Defect #2: include customers that only have CSConsole activity (no AB/CSOne rows).
            customer_account_ids = pd.Series(dtype=str)
            if team_subs_df is not None and not team_subs_df.empty and {'BU_NAME', 'ACCOUNT_ID_C'}.issubset(set(team_subs_df.columns)):
                customer_account_ids = (
                    team_subs_df[team_subs_df['BU_NAME'] == customer_name]['ACCOUNT_ID_C']
                    .dropna()
                    .astype(str)
                )
            customer_account_id_values = set(customer_account_ids.tolist())

            if not filtered_action_plans.empty and customer_account_id_values and 'ACCOUNT_ID_C' in filtered_action_plans.columns:
                cust_action_plans = filtered_action_plans[filtered_action_plans['ACCOUNT_ID_C'].astype(str).isin(customer_account_id_values)]
            else:
                cust_action_plans = filtered_action_plans[filtered_action_plans['BU_NAME'] == customer_name].copy() if not filtered_action_plans.empty and 'BU_NAME' in filtered_action_plans.columns else pd.DataFrame()

            if not filtered_customer_pulse.empty and customer_account_id_values and 'ACCOUNT__C' in filtered_customer_pulse.columns:
                cust_customer_pulse = filtered_customer_pulse[filtered_customer_pulse['ACCOUNT__C'].astype(str).isin(customer_account_id_values)]
            else:
                cust_customer_pulse = filtered_customer_pulse[filtered_customer_pulse['BU_NAME'] == customer_name].copy() if not filtered_customer_pulse.empty and 'BU_NAME' in filtered_customer_pulse.columns else pd.DataFrame()

            if not filtered_success_priorities.empty and customer_account_id_values and 'RELATED_CUSTOMER__C' in filtered_success_priorities.columns:
                cust_success_priorities = filtered_success_priorities[filtered_success_priorities['RELATED_CUSTOMER__C'].astype(str).isin(customer_account_id_values)]
            else:
                cust_success_priorities = filtered_success_priorities[filtered_success_priorities['CUSTOMER_BU_NAME__C'] == customer_name].copy() if not filtered_success_priorities.empty and 'CUSTOMER_BU_NAME__C' in filtered_success_priorities.columns else pd.DataFrame()

            if not filtered_adoption_barriers.empty and customer_account_id_values and 'ACCOUNT_ID_C' in filtered_adoption_barriers.columns:
                cust_csconsole_adoption_barriers = filtered_adoption_barriers[filtered_adoption_barriers['ACCOUNT_ID_C'].astype(str).isin(customer_account_id_values)]
            else:
                cust_csconsole_adoption_barriers = filtered_adoption_barriers[filtered_adoption_barriers['BU_NAME'] == customer_name].copy() if not filtered_adoption_barriers.empty and 'BU_NAME' in filtered_adoption_barriers.columns else pd.DataFrame()

            cust_ab_empty = cust_ab is None or (hasattr(cust_ab, 'empty') and cust_ab.empty)
            cust_csone_empty = cust_csone is None or (hasattr(cust_csone, 'empty') and cust_csone.empty)
            csconsole_has_data = any(
                not frame.empty
                for frame in (
                    cust_action_plans,
                    cust_customer_pulse,
                    cust_success_priorities,
                    cust_csconsole_adoption_barriers,
                )
            )
            if cust_ab_empty and cust_csone_empty and not csconsole_has_data:
                logger.info(f"  [[WARNING]] Skipping {customer_name} - no data found")
                continue

            cssm_name = cssm_lookup.get(customer_name, "N/A")
            matches, matched_df = cross_reference_refs(cust_ab, cust_csone, ext_bugs)
            
            logger.info(f"  [[AI]] Calling CircuIT AI for {customer_name} analysis...")
            try:
                customer_csconsole_data = {
                    'action_plans': cust_action_plans,
                    'customer_pulse': cust_customer_pulse,
                    'success_priorities': cust_success_priorities,
                    'adoption_barriers': cust_csconsole_adoption_barriers
                }
                
                # Extract software defects and PSIRT for this customer (enriches AI briefing)
                cust_sw_defects = None
                cust_psirt = None
                try:
                    if not cust_csone.empty or (cust_ab is not None and not cust_ab.empty):
                        cust_sw_defects = extract_software_defects(cust_csone, cust_ab)
                        cust_psirt = extract_psirt_vulnerabilities(cust_csone, cust_ab)
                        if cust_sw_defects.get('total_defects', 0) == 0:
                            cust_sw_defects = None
                        if cust_psirt.get('total_vulnerabilities', 0) == 0:
                            cust_psirt = None
                except Exception as e:
                    logger.debug(f"Defect/vuln extraction for {customer_name}: {e}")
                
                customer_briefing = _create_briefing_book(
                    customer_name, cust_ab, cust_csone, ext_bugs, ext_incidents, matches, matched_df,
                    None, None, customer_csconsole_data,
                    software_defects=cust_sw_defects, psirt_vulns=cust_psirt
                )
                
                # Determine specific technology for this customer if "All Contact Center" is selected
                specific_technology = status['tech']
                if status['tech'] == 'All Contact Center':
                    specific_technology = _get_customer_specific_technology(customer_name, {
                        'subscriptions': engagement,
                        'adoption_barriers': ab_norm,
                        'tac_cases': csone_df
                    })
                    if not specific_technology:
                        specific_technology = 'Contact Center'  # Fallback
                
                # Add ARR and sentiment analysis to briefing
                try:
                    from arr_sentiment_analyzer import ARRSentimentAnalyzer
                    arr_sentiment_analyzer = ARRSentimentAnalyzer(ctx)
                    
                    # Get ARR data
                    arr_data = arr_sentiment_analyzer.get_customer_arr_data(customer_name)
                    
                    # Get sentiment analysis
                    sentiment_data = arr_sentiment_analyzer.analyze_customer_sentiment(customer_name, customer_csconsole_data)
                    
                    # Add to briefing
                    arr_context = ""
                    if arr_data.get('total_arr', 0) > 0:
                        arr_context = f"\n\n**CUSTOMER VALUE & STRATEGIC CONTEXT:**\n"
                        arr_context += f"• ARR: ${arr_data['total_arr']:,.0f} ({arr_data['arr_tier']} tier)\n"
                        arr_context += f"• Strategic Priority: {arr_data['strategic_priority']}\n"
                        arr_context += f"• Voice Weight: {arr_data['voice_weight']:.1f}x\n"
                    
                    sentiment_context = f"\n• Customer Sentiment: {sentiment_data.get('overall_sentiment', 'Unknown')} ({sentiment_data.get('confidence_level', 'Low')} confidence)\n"
                    
                    # FIXED: Show ALL key indicators
                    if sentiment_data.get('key_indicators'):
                        sentiment_context += f"• Key Indicators: {', '.join(sentiment_data['key_indicators'])}\n"
                    
                    # FIXED: Show ALL recommendations
                    if sentiment_data.get('recommendations'):
                        sentiment_context += f"• Recommendations: {', '.join(sentiment_data['recommendations'])}\n"
                    
                    customer_briefing += arr_context + sentiment_context
                    
                except Exception as e:
                    logger.debug(f"Error adding ARR/sentiment to briefing for {customer_name}: {e}")
                    pass
                
                customer_prompt = PROMPT_CUSTOMER_TEMPLATE.format(CUSTOMER_NAME=customer_name, CSSM_NAME=cssm_name, TECHNOLOGY=specific_technology, MANAGER=status['manager'])
                customer_storyboard = generate_llm_response(customer_prompt, customer_briefing)
                
                # Check if AI response is valid
                if customer_storyboard and not customer_storyboard.startswith("ERROR:"):
                    # Add customer separator before each customer section (except the first)
                    if customers_actually_analyzed > 0:
                        report_builder._add_customer_separator()
                    # Use clean builder to parse AI output - NO markdown symbols
                    report_builder.parse_ai_output_and_add(customer_storyboard)
                    logger.info(f"  [[OK]] Completed AI analysis for {customer_name} - NO markdown symbols")
                    customers_actually_analyzed += 1
                else:
                    logger.warning(f"  [[WARNING]] AI analysis failed for {customer_name}: {customer_storyboard}")
                    # Add customer separator before each customer section (except the first)
                    if customers_actually_analyzed > 0:
                        report_builder._add_customer_separator()
                    # Add a fallback section with clean formatting
                    report_builder.add_heading(f"{customer_name} Analysis", level=2)
                    report_builder.add_paragraph("AI analysis temporarily unavailable. Customer data processed successfully.")
                    report_builder.add_paragraph(f"Customer: {customer_name}", bold_sections=["Customer:"])
                    report_builder.add_paragraph(f"CSSM: {cssm_name}", bold_sections=["CSSM:"])
                    report_builder.add_paragraph(f"Adoption Barriers: {len(cust_ab) if not cust_ab.empty else 0}", bold_sections=["Adoption Barriers:"])
                    report_builder.add_paragraph(f"TAC Cases: {len(cust_csone) if not cust_csone.empty else 0}", bold_sections=["TAC Cases:"])
                    customers_actually_analyzed += 1  # Count fallback as analyzed
                    
            except Exception as ai_error:
                logger.error(f"  [[ERROR]] AI analysis failed for {customer_name}: {ai_error}")
                # Add customer separator before each customer section (except the first)
                if customers_actually_analyzed > 0:
                    report_builder._add_customer_separator()
                # Add a fallback section with clean formatting
                report_builder.add_heading(f"{customer_name} Analysis", level=2)
                report_builder.add_paragraph("AI analysis encountered an error. Customer data processed successfully.")
                report_builder.add_paragraph(f"Customer: {customer_name}", bold_sections=["Customer:"])
                report_builder.add_paragraph(f"CSSM: {cssm_name}", bold_sections=["CSSM:"])
                report_builder.add_paragraph(f"Adoption Barriers: {len(cust_ab) if not cust_ab.empty else 0}", bold_sections=["Adoption Barriers:"])
                report_builder.add_paragraph(f"TAC Cases: {len(cust_csone) if not cust_csone.empty else 0}", bold_sections=["TAC Cases:"])
                report_builder.add_paragraph(f"Error: {str(ai_error)}", bold_sections=["Error:"])
                customers_actually_analyzed += 1  # Count error fallback as analyzed

        # === 3. Save Final Report ===
        status['progress'] = 90
        status['message'] = ' Finalizing comprehensive AI-powered report and generating outputs...'
        status['current_step'] = 'Report Generation'
        status['step_start_time'] = datetime.now().isoformat()
        status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=2)).isoformat()
        
        logger.info(f"[[DOC]] Saving main Word document with clean formatting (NO markdown symbols)...")
        docx_path = f"{base}.docx"
        report_builder.save(docx_path)
        logger.info(f"[[OK]] Clean executive report saved (NO ## symbols): {docx_path}")
        
        # === 4. Create Enhanced Reports ===
        try:
            status['progress'] = 92
            status['message'] = ' Creating enhanced executive Word report with detailed technology analysis...'
            status['current_step'] = 'Enhanced Report Generation'
            status['step_start_time'] = datetime.now().isoformat()
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=1)).isoformat()
            
            logger.info(f"[[ENHANCED]] Generating enhanced Word report with technology focus...")
            # Create enhanced Word report
            from adoptiq_backend import create_enhanced_word_report
            enhanced_docx_path = create_enhanced_word_report(
                status['manager'], status['tech'], status['days'], ab_norm, csone_df, 
                {"portfolio_summary": {"portfolio_health_score": "B", "executive_summary": "Portfolio analysis completed successfully"}},
                ext_bugs, ext_incidents
            )
            logger.info(f"[[ENHANCED]] Enhanced Word report created: {enhanced_docx_path}")
            status['progress'] = 94
            status['message'] = ' Enhanced Word report completed successfully!'
        except Exception as e:
            logger.warning(f"[[WARNING]] Enhanced Word report failed: {e}")
            enhanced_docx_path = None
            status['progress'] = 94
            status['message'] = '️ Enhanced Word report skipped, continuing with Excel generation...'
        
        # Write Excel file with all data
        try:
            status['progress'] = 95
            status['message'] = ' Writing comprehensive Excel workbook with detailed data analysis...'
            status['current_step'] = 'Excel Report Generation'
            status['step_start_time'] = datetime.now().isoformat()
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(seconds=30)).isoformat()
            
            logger.info(f"[[DATA]] Writing comprehensive Excel workbook...")
            
            # Prepare CSConsole data for Excel export
            csconsole_sheets = {}
            if 'filtered_action_plans' in locals() and not filtered_action_plans.empty:
                csconsole_sheets["CSConsole_Action_Plans"] = filtered_action_plans
            if 'filtered_customer_pulse' in locals() and not filtered_customer_pulse.empty:
                csconsole_sheets["CSConsole_Customer_Pulse"] = filtered_customer_pulse
            if 'filtered_success_priorities' in locals() and not filtered_success_priorities.empty:
                csconsole_sheets["CSConsole_Success_Priorities"] = filtered_success_priorities
            if 'filtered_adoption_barriers' in locals() and not filtered_adoption_barriers.empty:
                csconsole_sheets["CSConsole_Adoption_Barriers"] = filtered_adoption_barriers
            
            # Combine all sheets (excluding CSConsole data to avoid duplicates with enhanced formatter)
            all_sheets = {
                "AB_Detail_All": ab_norm,
                "CSOne_Detail_All": csone_df,
                "External_Bugs": pd.DataFrame(ext_bugs),
                "External_Incidents": pd.DataFrame(ext_incidents)
            }
            
            xlsx_path = write_excel_workbook(base, all_sheets, {
                'action_plans': filtered_action_plans,
                'customer_pulse': filtered_customer_pulse,
                'success_priorities': filtered_success_priorities,
                'adoption_barriers': filtered_adoption_barriers
            }, status['manager'], status['tech'], status['days'])
            logger.info(f"[[OK]] Excel file written successfully: {xlsx_path}")
            status['progress'] = 97
            status['message'] = ' Excel workbook completed successfully!'
        except Exception as excel_error:
            logger.error(f"[[ERROR]] Excel writing failed: {excel_error}")
            raise excel_error
        
        # Calculate final metrics - use actually analyzed customers, not total
        customers_analyzed = customers_actually_analyzed
        
        # Update status with results
        try:
            status['progress'] = 98
            status['message'] = ' Finalizing analysis results and updating status...'
            status['current_step'] = 'Final Status Update'
            status['step_start_time'] = datetime.now().isoformat()
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(seconds=10)).isoformat()
            
            logger.info(f"[[SUCCESS]] Updating final status with comprehensive results...")
            
            # Update customer progress to final state
            status['customer_progress']['completed'] = customers_analyzed
            status['customer_progress']['current'] = None  # No current customer
            
            # Only set to completed if all customers were actually processed
            if customers_analyzed == len(all_customers):
                status['status'] = 'completed'
                status['progress'] = 100
                status['message'] = f' COMPREHENSIVE AI-POWERED ANALYSIS COMPLETED! Generated detailed report with CircuIT AI analysis for {customers_analyzed} customers. Each customer received a full AI-powered deep dive with strategic insights and recommendations.'
                status['current_step'] = 'Analysis Complete'
            else:
                # Some customers were skipped - show accurate progress
                status['status'] = 'completed'
                status['progress'] = 100
                status['message'] = f' COMPREHENSIVE AI-POWERED ANALYSIS COMPLETED! Generated detailed report with CircuIT AI analysis for {customers_analyzed} of {len(all_customers)} customers (some customers had no data to analyze). Each analyzed customer received a full AI-powered deep dive with strategic insights and recommendations.'
                status['current_step'] = 'Analysis Complete'
            status['completion_time'] = status.get('completion_time') or datetime.now().isoformat()
            completion_time = status['completion_time']
            report_type = status.get('report_type', 'comprehensive')
            manager = status.get('manager', '')
            technology = status.get('technology', status.get('tech', ''))
            customer_name = status.get('customer_name', '')
            start_time = status.get('start_time', '')
            
            # Safe DataFrame length calculations
            ab_len = 0
            csone_len = 0
            try:
                if ab_norm is not None and hasattr(ab_norm, '__len__'):
                    ab_len = len(ab_norm)
                if csone_df is not None and hasattr(csone_df, '__len__'):
                    csone_len = len(csone_df)
            except Exception as len_error:
                logger.error(f"Length calculation failed: {len_error}")
                ab_len = 0
                csone_len = 0
            
            status['results'] = {
                'docx_path': docx_path,
                'xlsx_path': xlsx_path,
                'customers_analyzed': customers_analyzed,
                'total_barriers': ab_len,
                'total_cases': csone_len
            }
            # Also set the individual report paths for download compatibility
            status['word_report'] = docx_path
            status['excel_report'] = xlsx_path
            logger.info(f"[[OK]] Status updated successfully")

            # Persist status snapshot after final completion update.
            save_analysis_status()

            insights_payload = _build_insights_payload(
                status,
                'Comprehensive analysis completed',
                {
                    'customer_count': customers_analyzed,
                    'barrier_count': ab_len,
                    'case_count': csone_len,
                },
            )

            # Run non-locking side effects after state is finalized.
            auto_audit_report(analysis_id)
            record_report_completion(
                analysis_id, report_type, manager, technology, customer_name,
                'completed', start_time, completion_time
            )
            try:
                store_report_insights(
                    analysis_id, report_type, manager, technology, customer_name, insights_payload
                )
            except Exception as store_err:
                logger.debug(f"Store insights skipped: {store_err}")
        except Exception as status_error:
            logger.error(f"[[ERROR]] Status update failed: {status_error}")
            raise status_error
        
        logger.info(f"[[SUCCESS]] SUCCESS: Comprehensive AI-powered analysis completed at {docx_path}")
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"[[ERROR]] Analysis failed: {str(e)}")
        logger.error(f"[[LIST]] Full traceback: {error_details}")
        
        # Get status safely
        try:
            with analysis_status_lock:
                status = analysis_status.get(analysis_id, {})
        except Exception as status_error:
            logger.error(f"[[ERROR]] Failed to get status: {status_error}")
            status = {}
        
        # Check for specific connection errors and provide guidance
        error_message = str(e)
        if "is not allowed to access Snowflake" in error_message or "Failed to connect to DB" in error_message:
            status['error'] = f"Database Connection Error: Please connect to the Cisco VPN and retry the analysis. Error: {error_message}"
        elif "keeper.cisco.com" in error_message and "Read timed out" in error_message:
            status['error'] = f"Network Connection Error: Unable to connect to Cisco Keeper service. Please check your network connection and Cisco VPN status. Error: {error_message}"
        elif "keeper.cisco.com" in error_message:
            status['error'] = f"Cisco Keeper Connection Error: Please ensure you're connected to the Cisco VPN and have proper access to Keeper services. Error: {error_message}"
        elif "CircuIT" in error_message or "AzureOpenAI" in error_message:
            status['error'] = f"AI Service Error: CircuIT AI service may be unavailable. Please try again in a few minutes. Error: {error_message}"
        else:
            status['error'] = f"{error_message} - Full traceback: {error_details}"
        
        status['status'] = 'error'
        status['progress'] = 0
        
        # Ensure status is updated in the global dictionary
        try:
            with analysis_status_lock:
                analysis_status[analysis_id] = status
        except Exception as update_error:
            logger.error(f"[[ERROR]] Failed to update status: {update_error}")
    finally:
        # Ensure database connection is closed
        if 'ctx' in locals() and ctx is not None:
            try:
                ctx.close()
                logger.info(f"[[CLEANUP]] Database connection closed for {analysis_id}")
            except Exception as e:
                logger.warning(f"[[WARNING]] Error closing database connection: {e}")

def get_report_type_display(report_type):
    """Get user-friendly display name for report type"""
    report_type_map = {
        'comprehensive': ' Comprehensive Portfolio Report',
        'compact': ' Compact Analysis Report',
        'leader': ' Leader/Team Report',
        'renewal': ' Customer Renewal Report (Single)',
        'renewal_portfolio': ' Customer Renewal Report (Portfolio)',
        'customer_renewal': ' Customer Renewal Report',
        'subscription': ' Subscription Analysis'
    }
    return report_type_map.get(report_type, ' Custom Report')

def get_eta_display(status):
    """Calculate and format ETA display"""
    if status.get('status') == 'completed':
        return 'Completed'
    elif status.get('status') == 'error':
        return 'Error occurred'
    elif status.get('status') == 'cancelled':
        return 'Cancelled'
    
    # Try to get from estimated_completion first
    if status.get('estimated_completion'):
        try:
            eta_str = status.get('estimated_completion') or ''
            eta_time = eta_str[:19] if isinstance(eta_str, str) else ''
            return f"~{eta_time.split('T')[1]}"
        except Exception:
            pass
    
    # Try to calculate from eta_seconds
    if status.get('eta_seconds'):
        eta_seconds = status.get('eta_seconds')
        eta_minutes = max(1, eta_seconds // 60)
        return f"~{eta_minutes} minute{'s' if eta_minutes != 1 else ''}"
    
    # Fallback: estimate based on progress
    progress = status.get('progress', 0)
    if progress > 10:
        try:
            start_str = (status.get('start_time') or '').replace('Z', '+00:00')
            if not start_str:
                raise ValueError("No start_time")
            start_time = datetime.fromisoformat(start_str)
            elapsed = (datetime.now() - start_time).total_seconds()
            if progress > 0:
                estimated_total = elapsed * (100 / progress)
                remaining = max(0, estimated_total - elapsed)
                eta_minutes = max(1, int(remaining // 60))
                return f"~{eta_minutes} minute{'s' if eta_minutes != 1 else ''}"
        except Exception:
            pass
    
    return 'Calculating...'

@app.route('/progress/<analysis_id>')
def progress(analysis_id):
    """Simple progress page"""
    import html as html_module
    from urllib.parse import unquote
    
    # URL decode the analysis_id in case it was encoded
    analysis_id = unquote(analysis_id)
    analysis_id_safe = html_module.escape(analysis_id)  # Prevent XSS when embedding in HTML
    
    # Check in-memory status first
    if analysis_id not in analysis_status:
        # If not in memory, try to load from saved file
        try:
            status_file_path = str(_APP_SUPPORT / STATUS_FILE) if not os.path.isabs(STATUS_FILE) else STATUS_FILE
            if os.path.exists(status_file_path):
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    loaded_status = json.load(f)
                    if analysis_id in loaded_status:
                        # Load it into memory
                        with analysis_status_lock:
                            analysis_status[analysis_id] = loaded_status[analysis_id]
                        status = analysis_status[analysis_id]
                    else:
                        # Log available IDs for debugging
                        available_ids = list(loaded_status.keys())[:5]  # First 5 for debugging
                        logger.warning(f"Analysis ID '{analysis_id}' not found in status file. Available IDs (sample): {available_ids}")
                        return f"<h1>Analysis not found</h1><p>Analysis ID: {analysis_id_safe}</p><a href='/'>Start New Analysis</a>", 404
            else:
                logger.warning(f"Status file not found at: {status_file_path}. Current directory: {os.getcwd()}")
                # Check if analysis is in memory (might have been created but not saved yet)
                with analysis_status_lock:
                    if analysis_id in analysis_status:
                        status = analysis_status[analysis_id]
                    else:
                        logger.error(f"Analysis ID '{analysis_id}' not found in memory or file. Available in memory: {list(analysis_status.keys())[:5]}")
                        return f"<h1>Analysis not found</h1><p>Analysis ID: {analysis_id_safe}</p><a href='/'>Start New Analysis</a>", 404
        except Exception as e:
            logger.error(f"Error loading analysis status from file: {e}", exc_info=True)
            return f"<h1>Analysis not found</h1><p>Error: {html_module.escape(str(e))}</p><a href='/'>Start New Analysis</a>", 404
    else:
        status = analysis_status[analysis_id]
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Analysis Progress - AdoptIQ</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 1000px; margin: 50px auto; padding: 20px; }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .progress-bar {{ width: 100%; background-color: #f0f0f0; border-radius: 10px; margin: 20px 0; }}
            .progress-fill {{ height: 30px; background-color: #007bff; border-radius: 10px; transition: width 0.3s; }}
            .status {{ padding: 15px; border-radius: 5px; margin: 20px 0; }}
            .running {{ background-color: #e7f3ff; }}
            .completed {{ background-color: #d4edda; }}
            .error {{ background-color: #f8d7da; }}
            .download-links {{ margin: 20px 0; }}
            .download-links a {{ display: inline-block; margin: 10px; padding: 10px 20px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; }}
            .info-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
            .info-card {{ background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #007bff; }}
            .csone-status {{ background-color: #e7f3ff; padding: 15px; border-radius: 8px; margin: 15px 0; }}
            .csone-success {{ border-left: 4px solid #28a745; background-color: #d4edda; }}
            .csone-warning {{ border-left: 4px solid #ffc107; background-color: #fff3cd; }}
            .csone-error {{ border-left: 4px solid #dc3545; background-color: #f8d7da; }}
        </style>
        <script>
            const ANALYSIS_ID = {json.dumps(analysis_id)};
            function refreshStatus() {{
                fetch('/status/' + ANALYSIS_ID)
                .then(response => response.json())
                .then(data => {{
                    // Update progress bar and percentage
                    const progressBar = document.getElementById('progress');
                    const progressText = document.getElementById('progress-text');
                    if (progressBar && progressText) {{
                        progressBar.style.width = data.progress + '%';
                        progressText.textContent = data.progress + '%';
                    }}
                    
                    // Update status message
                    const messageElement = document.getElementById('message');
                    if (messageElement) {{
                        messageElement.textContent = data.message;
                    }}
                    
                    // Update customer progress if available
                    if (data.customer_progress) {{
                        const customerProgress = data.customer_progress;
                        const progressText = document.getElementById('customer-progress-text');
                        if (progressText) {{
                            if (customerProgress.current) {{
                                progressText.innerHTML = `
                                    <strong>Current:</strong> ${{customerProgress.current}}<br>
                                    <strong>Progress:</strong> ${{customerProgress.completed}}/${{customerProgress.total}} customers completed<br>
                                    <strong>Remaining:</strong> ${{customerProgress.total - customerProgress.completed}} customers
                                `;
                            }} else {{
                                progressText.textContent = `Processing ${{customerProgress.total}} customers...`;
                            }}
                        }}
                    }}
                    
                    // Update status
                    const statusElement = document.getElementById('status');
                    if (statusElement) {{
                        statusElement.textContent = data.status;
                    }}
                    
                    // Update ETA display
                    const etaElement = document.getElementById('eta-display');
                    if (etaElement && data.eta_display) {{
                        etaElement.textContent = data.eta_display;
                    }}
                    
                    // Update CSOne import status
                    if (data.csone_import_status) {{
                        const csoneStatus = document.getElementById('csone-status');
                        if (csoneStatus) {{
                            csoneStatus.textContent = data.csone_import_message;
                            csoneStatus.className = 'csone-status csone-' + data.csone_import_status;
                        }}
                    }}
                    
                    if (data.status === 'completed') {{
                        document.getElementById('downloads').style.display = 'block';
                        document.getElementById('cancel-btn').style.display = 'none';
                        clearInterval(window.refreshInterval);
                    }} else if (data.status === 'error') {{
                        document.getElementById('error').style.display = 'block';
                        document.getElementById('error').textContent = 'Error: ' + (data.error || data.message || 'Unknown error');
                        document.getElementById('cancel-btn').style.display = 'none';
                        clearInterval(window.refreshInterval);
                    }} else if (data.status === 'cancelling') {{
                        document.getElementById('message').textContent = ' Cancellation requested...';
                        document.getElementById('cancel-btn').style.display = 'none';
                    }}
                }});
            }}
            
            function cancelAnalysis() {{
                if (confirm('Are you sure you want to cancel this analysis?')) {{
                    fetch('/cancel/' + ANALYSIS_ID, {{method: 'POST'}})
                        .then(response => response.json())
                        .then(data => {{
                            if (data.success) {{
                                document.getElementById('cancel-btn').style.display = 'none';
                                document.getElementById('message').textContent = ' Cancellation requested...';
                            }} else {{
                                alert('Failed to cancel analysis: ' + data.error);
                            }}
                        }})
                        .catch(error => {{
                            console.error('Error:', error);
                            alert('Failed to cancel analysis');
                        }});
                }}
            }}
            
            function checkDownload(fileType) {{
                const downloadError = document.getElementById('download-error');
                downloadError.style.display = 'none';
                
                // Show loading message
                downloadError.style.display = 'block';
                downloadError.style.backgroundColor = '#d1ecf1';
                downloadError.style.color = '#0c5460';
                downloadError.textContent = 'Downloading ' + fileType.toUpperCase() + ' file...';
                
                return true;
            }}
            
            function showDownloadError(message) {{
                const downloadError = document.getElementById('download-error');
                downloadError.style.display = 'block';
                downloadError.style.backgroundColor = '#f8d7da';
                downloadError.style.color = '#721c24';
                downloadError.textContent = 'Download Error: ' + message;
            }}
            
            window.onload = function() {{
                refreshStatus();
                window.refreshInterval = setInterval(refreshStatus, 2000);
            }};
        </script>
    </head>
    <body>
        <div class="header">
            <h1>AdoptIQ Analysis Progress</h1>
            <p>AI-Powered Executive Analytics in Progress</p>
        </div>
        
        <div class="info-grid">
            <div class="info-card">
                <h3>Analysis Details</h3>
                <p><strong>Report Type:</strong> <span style="color: #1e40af; font-weight: bold;">{get_report_type_display(status.get('report_type', 'comprehensive'))}</span></p>
                <p><strong>Manager:</strong> {html_module.escape(str(status.get('manager', 'N/A')))}</p>
                <p><strong>Technology:</strong> {html_module.escape(str(status.get('technology', 'N/A')))}</p>
                <p><strong>Period:</strong> {html_module.escape(str(status.get('days', 'N/A')))} days</p>
                <p><strong>Started:</strong> {html_module.escape(str(status.get('start_time', 'N/A'))[:19] if status.get('start_time') else 'N/A')}</p>
            </div>
            <div class="info-card">
                <h3>Current Status</h3>
                <p><strong>Status:</strong> <span id="status">{html_module.escape(str(status['status']))}</span></p>
                <p><strong>Step:</strong> {html_module.escape(str(status.get('current_step', 'N/A')))}</p>
                <p><strong>ETA:</strong> <span id="eta-display">{get_eta_display(status)}</span></p>
            </div>
        </div>
        
        <div class="csone-status csone-{html_module.escape(str(status.get('csone_import_status', 'checking')))}" id="csone-status" style="{'display: block;' if status.get('csone_import_status') in ['checking', 'uploaded', 'processing'] else 'display: none;'}">
            {html_module.escape(str(status.get('csone_import_message', 'Processing analysis...')))}
        </div>
        
        <div class="status {'completed' if status['status'] == 'completed' else 'error' if status['status'] == 'error' else 'running'}">
            <h3>Analysis Progress</h3>
            <p><strong>Message:</strong> <span id="message">{html_module.escape(str(status['message']))}</span></p>
        </div>
        
        <div class="progress-bar">
            <div class="progress-fill" id="progress" style="width: {status['progress']}%"></div>
        </div>
        <p style="text-align: center; font-size: 18px; font-weight: bold;">Progress: <span id="progress-text">{status['progress']}</span></p>
        
        <!-- Customer Progress Indicator -->
        <div id="customer-progress" style="margin: 15px 0; padding: 15px; background-color: #f8fafc; border-radius: 8px; border-left: 4px solid #3b82f6;">
            <div style="font-weight: bold; color: #1e3a8a; margin-bottom: 8px;">
 Customer Analysis Progress
            </div>
            <div id="customer-progress-text" style="color: #4b5563; font-size: 14px;">
                Loading customer progress...
            </div>
        </div>
        
        <div id="cancel-btn" style="margin: 20px 0; {'display: none;' if status['status'] in ['completed', 'error', 'cancelling'] else ''}">
            <button onclick="cancelAnalysis()" style="padding: 10px 20px; background-color: #dc3545; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px;">
 Cancel Analysis
            </button>
        </div>
        
        <div id="error" style="display: none; color: red; font-weight: bold;"></div>
        
        <div id="downloads" class="download-links" style="display: {'block' if status['status'] == 'completed' else 'none'};">
            <h3> Download Your Comprehensive Report:</h3>
            <a href="/download/{analysis_id_safe}/docx" onclick="return checkDownload('docx')"> Download Word Report</a>
            <a href="/download/{analysis_id_safe}/xlsx" onclick="return checkDownload('xlsx')"> Download Excel Data</a>
        </div>
        
        <div id="download-error" style="display: none; color: red; font-weight: bold; margin: 20px 0; padding: 15px; background-color: #f8d7da; border-radius: 5px;"></div>
        
        <div style="margin: 20px 0; padding: 15px; background-color: #e7f3ff; border-radius: 5px;">
            <h4>Previous Reports:</h4>
            <p>View and download previously generated reports from the output folder.</p>
            <p><a href="/previous-reports" target="_blank">Browse Previous Reports</a></p>
        </div>
        
        <p><a href="/">Start New Analysis</a></p>
    </body>
    </html>
    """
    return html_content

@app.route('/status/<analysis_id>')
def get_status(analysis_id):
    from urllib.parse import unquote
    
    # URL decode the analysis_id in case it was encoded
    analysis_id = unquote(analysis_id)
    
    # Check in-memory status first
    if analysis_id not in analysis_status:
        # If not in memory, try to load from saved file
        try:
            status_file_path = str(_APP_SUPPORT / STATUS_FILE) if not os.path.isabs(STATUS_FILE) else STATUS_FILE
            if os.path.exists(status_file_path):
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    loaded_status = json.load(f)
                    if analysis_id in loaded_status:
                        # Load it into memory
                        with analysis_status_lock:
                            analysis_status[analysis_id] = loaded_status[analysis_id]
                    else:
                        logger.warning(f"Analysis ID '{analysis_id}' not found in status file")
                        return jsonify({'error': 'Analysis not found', 'analysis_id': analysis_id}), 404
            else:
                # Check if analysis is in memory (might have been created but not saved yet)
                with analysis_status_lock:
                    if analysis_id in analysis_status:
                        status = analysis_status[analysis_id]
                        status_copy = dict(status)
                        status_copy['eta_display'] = get_eta_display(status_copy)
                        return jsonify(status_copy)
                    else:
                        logger.warning(f"Analysis ID '{analysis_id}' not found in memory or file")
                        return jsonify({'error': 'Analysis not found', 'analysis_id': analysis_id}), 404
        except Exception as e:
            logger.error(f"Error loading analysis status from file: {e}", exc_info=True)
            return jsonify({'error': 'Analysis not found', 'details': str(e)}), 404
    
    with analysis_status_lock:
        status = analysis_status.get(analysis_id)
        if not status:
            return jsonify({'error': 'Analysis not found', 'analysis_id': analysis_id}), 404
        status_copy = {}
        for key, value in status.items():
            if isinstance(value, datetime):
                status_copy[key] = value.isoformat()
            else:
                status_copy[key] = value
    status_copy['eta_display'] = get_eta_display(status_copy)
    
    return jsonify(status_copy)

@app.route('/api/status/all')
def get_all_status():
    """Get status of all analyses (for admin console monitoring)"""
    try:
        with analysis_status_lock:
            # Return all statuses as a list with calculated ETAs
            all_statuses = []
            for analysis_id, status in analysis_status.items():
                status_copy = {}
                
                # Copy fields safely, converting datetime to string
                for key, value in status.items():
                    if isinstance(value, datetime):
                        status_copy[key] = value.isoformat()
                    elif isinstance(value, (str, int, float, bool, type(None))):
                        status_copy[key] = value
                    else:
                        status_copy[key] = str(value)
                
                status_copy['analysis_id'] = analysis_id
                
                # Calculate ETA display
                status_copy['eta_display'] = get_eta_display(status)
                
                # Add IP address if available
                if 'ip_address' not in status_copy:
                    status_copy['ip_address'] = 'N/A'
                
                all_statuses.append(status_copy)
            
            return jsonify(all_statuses)
    except Exception as e:
        return jsonify({'error': str(e), 'statuses': []}), 500

@app.route('/previous-reports')
def previous_reports():
    """Browse and download previous reports from the output folder"""
    try:
        import os
        import glob
        from datetime import datetime, timedelta
        import re
        
        # Get all Word and Excel files from outputs directory (canonical when frozen)
        outputs_dir = str(_APP_SUPPORT / "outputs") if _frozen else "outputs"
        if not os.path.exists(outputs_dir):
            os.makedirs(outputs_dir)
        
        # Find all report files (including leader reports - both old and new naming)
        word_files = glob.glob(os.path.join(outputs_dir, "AdoptIQ_*.docx")) + glob.glob(os.path.join(outputs_dir, "Leader_Report_*.docx"))
        excel_files = glob.glob(os.path.join(outputs_dir, "AdoptIQ_*.xlsx")) + glob.glob(os.path.join(outputs_dir, "AdoptIQ_Data_*.xlsx"))
        
        # Group files by report (remove file extension and timestamp to group them)
        report_groups = {}
        
        # Process Word files
        for file_path in word_files:
            try:
                stat = os.stat(file_path)
                filename = os.path.basename(file_path)
                
                # Extract report identifier (remove .docx and timestamp)
                # Pattern: AdoptIQ_Report_Manager_Technology_Days_YYYYMMDD_HHMMSS.docx
                # Pattern: AdoptIQ_Report_Compact_Manager_Technology_Days_YYYYMMDD_HHMMSS.docx
                # Pattern: AdoptIQ_Report_Renewal_Customer_Technology_Days_YYYYMMDD_HHMMSS.docx
                # Pattern: AdoptIQ_Report_Leader_Manager_Days_YYYYMMDD_HHMMSS.docx
                # Pattern: Leader_Report_Manager_Days_YYYYMMDD_HHMMSS.docx (legacy)
                report_id = re.sub(r'_\d{8}_\d{6}\.docx$', '', filename)
                report_id = report_id.replace('AdoptIQ_Report_', '').replace('AdoptIQ_', '').replace('Leader_Report_', 'Leader_')
                
                if report_id not in report_groups:
                    report_groups[report_id] = {
                        'word_file': None,
                        'excel_file': None,
                        'modified': None,
                        'total_size': 0
                    }
                
                report_groups[report_id]['word_file'] = {
                    'name': filename,
                    'path': file_path,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime)
                }
                report_groups[report_id]['total_size'] += stat.st_size
                
                # Keep track of the most recent modification time
                if report_groups[report_id]['modified'] is None or stat.st_mtime > report_groups[report_id]['modified'].timestamp():
                    report_groups[report_id]['modified'] = datetime.fromtimestamp(stat.st_mtime)
                    
            except Exception as e:
                logger.warning(f"Could not get file info for {file_path}: {e}")
        
        # Process Excel files
        for file_path in excel_files:
            try:
                stat = os.stat(file_path)
                filename = os.path.basename(file_path)
                
                # Extract report identifier (remove .xlsx and timestamp)
                # Pattern: AdoptIQ_Report_Manager_Technology_Days_YYYYMMDD_HHMMSS.xlsx
                # Pattern: AdoptIQ_Data_Manager_Technology_Days_YYYYMMDD_HHMMSS.xlsx
                report_id = re.sub(r'_\d{8}_\d{6}\.xlsx$', '', filename)
                report_id = report_id.replace('AdoptIQ_Report_', '').replace('AdoptIQ_Data_', '').replace('AdoptIQ_', '')
                
                if report_id not in report_groups:
                    report_groups[report_id] = {
                        'word_file': None,
                        'excel_file': None,
                        'modified': None,
                        'total_size': 0
                    }
                
                report_groups[report_id]['excel_file'] = {
                    'name': filename,
                    'path': file_path,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime)
                }
                report_groups[report_id]['total_size'] += stat.st_size
                
                # Keep track of the most recent modification time
                if report_groups[report_id]['modified'] is None or stat.st_mtime > report_groups[report_id]['modified'].timestamp():
                    report_groups[report_id]['modified'] = datetime.fromtimestamp(stat.st_mtime)
                    
            except Exception as e:
                logger.warning(f"Could not get file info for {file_path}: {e}")
        
        # Sort reports by modification time (newest first)
        sorted_reports = sorted(report_groups.items(), key=lambda x: x[1]['modified'], reverse=True)
        
        # Create HTML page
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Previous AdoptIQ Reports</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
                .container {{ max-width: 1200px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #1e3a8a; text-align: center; margin-bottom: 30px; }}
                .report-item {{ 
                    border: 1px solid #ddd; 
                    margin: 15px 0; 
                    padding: 20px; 
                    border-radius: 8px; 
                    background-color: #fafafa;
                    transition: background-color 0.3s;
                }}
                .report-item:hover {{ background-color: #f0f0f0; }}
                .report-header {{ font-weight: bold; font-size: 16px; color: #1e3a8a; margin-bottom: 10px; }}
                .report-details {{ color: #666; margin: 5px 0; }}
                .download-buttons {{ margin-top: 15px; }}
                .download-btn {{ 
                    display: inline-block; 
                    padding: 10px 20px; 
                    margin: 5px; 
                    background-color: #1e3a8a; 
                    color: white; 
                    text-decoration: none; 
                    border-radius: 5px;
                    transition: background-color 0.3s;
                }}
                .download-btn:hover {{ background-color: #1e40af; }}
                .download-btn.excel {{ background-color: #0d9488; }}
                .download-btn.excel:hover {{ background-color: #0f766e; }}
                .no-reports {{ text-align: center; color: #666; font-style: italic; margin: 40px 0; }}
                .back-link {{ text-align: center; margin-top: 30px; }}
                .back-link a {{ color: #1e3a8a; text-decoration: none; }}
                .file-info {{ display: flex; gap: 20px; margin: 10px 0; }}
                .file-type {{ padding: 5px 10px; border-radius: 3px; font-size: 12px; font-weight: bold; }}
                .file-type.word {{ background-color: #dbeafe; color: #1e40af; }}
                .file-type.excel {{ background-color: #d1fae5; color: #065f46; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1> Previous AdoptIQ Reports</h1>
        """
        
        if not sorted_reports:
            html += '<div class="no-reports">No previous reports found in the output folder.</div>'
        else:
            for report_id, report_data in sorted_reports:
                # Create readable name from report ID
                readable_name = report_id.replace('_', ' ').title()
                
                # Format modification time
                modified_str = report_data['modified'].strftime('%Y-%m-%d %H:%M:%S') if report_data['modified'] else 'Unknown'
                
                # Calculate total size
                total_size_mb = (report_data['total_size'] / 1024 / 1024) if report_data['total_size'] > 0 else 0
                
                html += f"""
                <div class="report-item">
                    <div class="report-header">{readable_name}</div>
                    <div class="report-details"><strong>Total Size:</strong> {total_size_mb:.1f} MB</div>
                    <div class="report-details"><strong>Modified:</strong> {modified_str}</div>
                    <div class="file-info">
                """
                
                # Add file type indicators
                if report_data['word_file']:
                    html += '<span class="file-type word"> Word Document</span>'
                if report_data['excel_file']:
                    html += '<span class="file-type excel"> Excel Document</span>'
                
                html += """
                    </div>
                    <div class="download-buttons">
                """
                
                # Add download buttons
                if report_data['word_file']:
                    html += f"""
                        <a href="/download-file/{os.path.basename(report_data['word_file']['path'])}" class="download-btn">
 Download Word File
                        </a>
                    """
                
                if report_data['excel_file']:
                    html += f"""
                        <a href="/download-file/{os.path.basename(report_data['excel_file']['path'])}" class="download-btn excel">
 Download Excel File
                        </a>
                    """
                
                html += """
                    </div>
                </div>
                """
        
        html += """
                <div class="back-link">
                    <a href="/"> Back to Main Page</a>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Error browsing previous reports: {e}")
        return f"Error loading previous reports: {str(e)}", 500

@app.route('/help')
def help():
    """Help page"""
    return render_template('help.html')

@app.route('/history')
def history():
    """Analysis history page — shows report history from audit DB when available."""
    raw = get_report_history() if callable(get_report_history) else []
    if raw is None or not isinstance(raw, list):
        raw = []
    analyses = [
        {
            'id': r.get('request_id', ''),
            'start_time': r.get('start_time') or r.get('created_at') or 'Unknown',
            'manager': r.get('manager') or '—',
            'technology': r.get('technology') or '—',
            'days': r.get('days'),  # not stored in report_history; template shows — when missing
            'report_type': r.get('report_type', ''),
            'customer_name': r.get('customer_name', ''),
            'status': r.get('status', ''),
        }
        for r in raw
    ]
    return render_template('history.html', analyses=analyses)

@app.route('/download-file/<filename>')
def download_file(filename):
    """Download a specific file from the outputs directory with security validation"""
    try:
        # Validate filename to prevent path traversal
        if not filename or len(filename) > 255:
            return "Invalid filename", 400
        
        # Check for path traversal attempts
        if '..' in filename or '/' in filename or '\\' in filename:
            return "Invalid filename", 400
        
        # Only allow specific file extensions
        allowed_extensions = {'.docx', '.xlsx', '.pdf'}
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext not in allowed_extensions:
            return "Invalid file type", 400
        
        # Secure the filename
        safe_filename = secure_filename(filename)
        if not safe_filename:
            return "Invalid filename", 400
        
        # Use canonical outputs dir when frozen (Application Support) so path is stable
        outputs_dir = str(_APP_SUPPORT / "outputs") if _frozen else os.path.abspath("outputs")
        file_path = os.path.join(outputs_dir, safe_filename)
        resolved_path = os.path.abspath(file_path)
        if not resolved_path.startswith(os.path.abspath(outputs_dir)):
            return "Access denied", 403
        if not os.path.exists(file_path):
            # Fallback: secure_filename may have altered the name (e.g. spaces → underscores).
            # Try the original URL-decoded filename with a path-traversal guard.
            original_path = os.path.join(outputs_dir, filename)
            original_resolved = os.path.abspath(original_path)
            if (original_resolved.startswith(os.path.abspath(outputs_dir))
                    and os.path.exists(original_resolved)):
                file_path = original_resolved
                safe_filename = filename
            else:
                return f"File not found: {safe_filename}", 404
        
        return send_file(file_path, as_attachment=True, download_name=safe_filename)
        
    except Exception as e:
        logger.error(f"Error downloading file {filename}: {e}")
        return f"Error downloading file: {str(e)}", 500

@app.route('/cancel/<analysis_id>', methods=['POST'])
def cancel_analysis(analysis_id):
    """Cancel a running analysis (thread-safe)"""
    with analysis_status_lock:
        if analysis_id not in analysis_status:
            return jsonify({'error': 'Analysis not found'}), 404
        
        status = analysis_status[analysis_id]
        if status.get('status') not in ['starting', 'running', 'cancelling']:
            return jsonify({'error': 'Analysis is not running'}), 400
    
    # Set cancellation flag (thread-safe)
    with cancellation_flags_lock:
        cancellation_flags[analysis_id] = True
    
    # Update status (thread-safe)
    with analysis_status_lock:
        status['status'] = 'cancelling'
        status['message'] = ' Cancellation requested...'
        status['progress'] = 0
    save_analysis_status()
    
    return jsonify({'success': True, 'message': 'Cancellation requested'})


@app.route('/start_compact_analysis', methods=['POST'])
def start_compact_analysis():
    """Start compact analysis focused on renewal risk"""
    try:
        # Handle both form data and JSON data
        subscription_id = ''
        customer_name = ''
        if request.is_json:
            data = request.get_json() or {}
            manager = data.get('manager', '').strip()
            technology = data.get('technology', '').strip()
            try:
                days = int(data.get('days', 90))
            except (ValueError, TypeError):
                days = 90
            csone_file = data.get('csone_file', '')
            subscription_id = (data.get('subscription_id') or '').strip()
            customer_name = (data.get('customer_name') or '').strip()
        else:
            # Handle form data with file upload
            manager = request.form.get('manager', '').strip()
            technology = request.form.get('technology', '').strip()
            try:
                days = int(request.form.get('days', 90))
            except (ValueError, TypeError):
                days = 90
            subscription_id = (request.form.get('subscription_id') or '').strip()
            customer_name = (request.form.get('customer_name') or '').strip()
            
            # Handle CSOne file upload
            csone_file = None
            if 'csone_file' in request.files:
                file = request.files['csone_file']
                if file and file.filename:
                    # Validate file
                    is_valid, error_msg = validate_file_upload(file)
                    if not is_valid:
                        return jsonify({'success': False, 'error': error_msg})
                    
                    # Save file to uploads directory
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    csone_file = filename  # Store just the filename
                    logger.info(f"[[OK]] CSOne file saved: {filepath}")
            
            if not csone_file:
                csone_file = request.form.get('csone_file', '')
        # When no file: use most recent from OneDrive folder (macro places reports daily)
        if not csone_file:
            latest = get_latest_csone_from_folder()
            if latest:
                csone_file = latest  # Store full path
        
        # Validate inputs: manager required only when no subscription/customer (single-customer mode doesn't use manager)
        has_single = bool(subscription_id or customer_name)
        if not has_single:
            is_valid, error_msg = validate_manager_input(manager)
            if not is_valid:
                return jsonify({'error': error_msg}), 400
        elif not manager or not manager.strip():
            manager = 'Single Customer'  # Placeholder for analysis_id when manager disabled in UI

        if not technology:
            return jsonify({'error': 'Technology is required'}), 400
        
        is_valid, error_msg = validate_days_input(days)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
        
        # Generate analysis ID
        analysis_id = f"Compact_{manager.replace(' ', '_')}_{technology.replace(' ', '_').replace('&', 'and')}_{days}d_{int(time.time())}"
        
        # Initialize status (subscription_id/customer_name allow single-customer filter)
        with analysis_status_lock:
            analysis_status[analysis_id] = {
                'status': 'starting',
                'progress': 0,
                'message': ' Starting compact analysis...',
                'manager': manager,
                'technology': technology,
                'days': days,
                'csone_file': csone_file,
                'subscription_id': subscription_id,
                'customer_name': customer_name,
                'start_time': datetime.now().isoformat(),
                'current_step': 'Initialization',
                'step_start_time': datetime.now().isoformat(),
                'estimated_completion': (datetime.now() + pd.Timedelta(minutes=5)).isoformat(),
                'report_type': 'compact'
            }
        
        # Start analysis in background thread with timeout protection
        def run_analysis_with_timeout():
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
                
                def run_analysis():
                    run_compact_analysis(analysis_id)
                
                # Use ThreadPoolExecutor with timeout to prevent hanging
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_analysis)
                    future.result(timeout=300)  # 5-minute timeout for entire analysis
                    
            except FutureTimeoutError:
                logger.error(f"⏰ Analysis timed out after 5 minutes")
                with analysis_status_lock:
                    if analysis_id in analysis_status:
                        analysis_status[analysis_id]['status'] = 'error'
                        analysis_status[analysis_id]['message'] = 'Analysis timed out after 5 minutes'
                        save_analysis_status()
            except Exception as e:
                logger.error(f"[[ERROR]] Analysis thread error: {e}")
                with analysis_status_lock:
                    if analysis_id in analysis_status:
                        analysis_status[analysis_id]['status'] = 'error'
                        analysis_status[analysis_id]['message'] = f'Analysis failed: {str(e)}'
                        save_analysis_status()
        
        thread = threading.Thread(target=run_analysis_with_timeout)
        thread.daemon = True
        thread.start()
        
        logger.info(f"[[START]] Started compact analysis: {analysis_id}")
        return jsonify({
            'success': True,
            'analysis_id': analysis_id,
            'message': 'Compact analysis started successfully',
            'redirect_url': url_for('progress', analysis_id=analysis_id)
        })
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error starting compact analysis: {e}")
        return jsonify({'error': f'Failed to start analysis: {str(e)}'}), 500


@app.route('/start_customer_renewal_analysis', methods=['POST'])
def start_customer_renewal_analysis():
    """Start customer-specific or portfolio renewal analysis"""
    try:
        data = request.get_json() or {}
        logger.info(f"[[DEBUG]] Renewal analysis request data: {data}")
        
        manager = (data.get('manager') or '').strip()
        technology = (data.get('technology') or '').strip()
        try:
            days = int(data.get('days') or 90)
        except (ValueError, TypeError):
            days = 90
        subscription_id = (data.get('subscription_id') or '').strip()
        customer_name = (data.get('customer_name') or '').strip()
        renewal_type = (data.get('renewal_type') or 'renewal').strip()  # 'renewal' or 'renewal_portfolio'
        csone_file = (data.get('csone_file') or '')
        
        logger.info(f"[[DEBUG]] Parsed values - renewal_type: '{renewal_type}', manager: '{manager}', tech: '{technology}', days: {days}, sub_id: '{subscription_id}', customer: '{customer_name}'")
        
        if not technology:
            logger.warning(f"[[VALIDATION]] Technology is empty")
            return jsonify({'error': 'Technology is required'}), 400
        
        is_valid, error_msg = validate_days_input(days)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
        
        # Validate based on renewal type
        if renewal_type == 'renewal':
            # Single customer renewal: require customer_name or subscription_id, manager optional
            if not subscription_id and not customer_name:
                logger.warning(f"[[VALIDATION]] Neither subscription_id nor customer_name provided for single customer renewal")
                return jsonify({'error': 'Either Subscription ID or Customer Name is required for single customer renewal analysis'}), 400
            if customer_name:
                is_valid, err_msg = validate_customer_name_input(customer_name)
                if not is_valid:
                    logger.warning(f"[[VALIDATION]] Customer name validation failed: {err_msg}")
                    return jsonify({'error': err_msg}), 400
        elif renewal_type == 'renewal_portfolio':
            # Portfolio renewal: require manager, customer_name/subscription_id not needed
            is_valid, error_msg = validate_manager_input(manager)
            if not is_valid:
                logger.warning(f"[[VALIDATION]] Manager validation failed for portfolio renewal: {error_msg}")
                return jsonify({'error': f'Manager is required for portfolio renewal analysis. {error_msg}'}), 400
        else:
            return jsonify({'error': f'Invalid renewal type: {renewal_type}. Must be "renewal" or "renewal_portfolio"'}), 400
        
        # Generate analysis ID based on renewal type
        if renewal_type == 'renewal_portfolio':
            analysis_id = f"Renewal_Portfolio_{manager.replace(' ', '_')}_{technology.replace(' ', '_').replace('&', 'and')}_{days}d_{int(time.time())}"
        elif subscription_id:
            analysis_id = f"Renewal_Sub_{subscription_id}_{technology.replace(' ', '_').replace('&', 'and')}_{days}d_{int(time.time())}"
        else:
            analysis_id = f"Renewal_{customer_name.replace(' ', '_')}_{technology.replace(' ', '_').replace('&', 'and')}_{days}d_{int(time.time())}"
        # Initialize status
        with analysis_status_lock:
            analysis_status[analysis_id] = {
                'status': 'starting',
                'progress': 0,
                'message': ' Starting renewal analysis...' if renewal_type == 'renewal_portfolio' else ' Starting customer renewal analysis...',
                'manager': manager,
                'technology': technology,
                'days': days,
                'subscription_id': subscription_id,
                'customer_name': customer_name,
                'renewal_type': renewal_type,
                'csone_file': csone_file,
                'start_time': datetime.now().isoformat(),
                'current_step': 'Initialization',
                'step_start_time': datetime.now().isoformat(),
                'estimated_completion': (datetime.now() + pd.Timedelta(minutes=5 if renewal_type == 'renewal_portfolio' else 3)).isoformat(),
                'report_type': 'customer_renewal'
            }
            save_analysis_status()
        
        # Start analysis in background thread
        thread = threading.Thread(target=run_customer_renewal_analysis, args=(analysis_id,))
        thread.daemon = True
        thread.start()
        
        logger.info(f"[[START]] Started {renewal_type} analysis: {analysis_id}")
        return jsonify({
            'success': True,
            'analysis_id': analysis_id,
            'message': 'Portfolio renewal analysis started successfully' if renewal_type == 'renewal_portfolio' else 'Customer renewal analysis started successfully',
            'redirect_url': url_for('progress', analysis_id=analysis_id)
        })
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error starting renewal analysis: {e}")
        return jsonify({'error': f'Failed to start analysis: {str(e)}'}), 500


@app.route('/search_subscriptions', methods=['POST'])
def search_subscriptions():
    """Search for subscriptions by customer name"""
    try:
        data = request.get_json() or {}
        customer_name = data.get('customer_name', '').strip()
        try:
            limit = int(data.get('limit', 10))
            limit = max(1, min(limit, 100))  # Clamp to 1-100
        except (ValueError, TypeError):
            limit = 10
        
        if not customer_name:
            return jsonify({'error': 'Customer name is required'}), 400
        
        if len(customer_name) < 2:
            return jsonify({'error': 'Customer name must be at least 2 characters'}), 400
        
        is_valid, err_msg = validate_customer_name_input(customer_name)
        if not is_valid:
            return jsonify({'error': err_msg}), 400
        
        # Search for subscriptions (Snowflake DSM)
        subscriptions = search_subscriptions_by_customer(customer_name, limit)
        # Unique customer names (BU_NAME) for dropdown—user picks exact name for report
        seen = set()
        customers = []
        for s in subscriptions:
            bu = (s.get('BU_NAME') or '').strip()
            if bu and bu not in seen:
                seen.add(bu)
                customers.append(bu)
        
        return jsonify({
            'success': True,
            'subscriptions': subscriptions,
            'customers': customers,
            'count': len(subscriptions)
        })
        
    except Exception as e:
        logger.error(f"Error searching subscriptions: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/subscription_analysis/<subscription_id>')
def subscription_analysis(subscription_id):
    """Get subscription analysis data"""
    try:
        # Validate subscription ID format
        if not re.match(r'^[a-zA-Z0-9\-_]+$', subscription_id):
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        
        days = request.args.get('days', 90, type=int)
        is_valid, error_msg = validate_days_input(days)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
        
        # Get subscription data
        sub_data = fetch_subscription_data(subscription_id, days)
        
        if not sub_data['found']:
            return jsonify({'error': sub_data.get('error', 'Subscription not found')}), 404
        
        return jsonify({
            'success': True,
            'subscription_data': sub_data
        })
        
    except Exception as e:
        logger.error(f"Error getting subscription analysis: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/subscription_renewal_risk/<subscription_id>')
def subscription_renewal_risk(subscription_id):
    """Get renewal risk analysis for a subscription"""
    try:
        # Validate subscription ID format
        if not re.match(r'^[a-zA-Z0-9\-_]+$', subscription_id):
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        
        days = request.args.get('days', 90, type=int)
        is_valid, error_msg = validate_days_input(days)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
        
        # Get renewal risk analysis
        risk_analysis = get_subscription_renewal_risk(subscription_id, days)
        
        if 'error' in risk_analysis:
            return jsonify({'error': risk_analysis['error']}), 404
        
        return jsonify({
            'success': True,
            'renewal_analysis': risk_analysis
        })
        
    except Exception as e:
        logger.error(f"Error getting subscription renewal risk: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/start_subscription_analysis', methods=['POST'])
def start_subscription_analysis():
    """Start subscription-based analysis"""
    try:
        data = request.form
        subscription_id = data.get('subscription_id', '').strip()
        try:
            days = int(data.get('days', 90))
        except (ValueError, TypeError):
            days = 90
        report_type = data.get('report_type', 'comprehensive')
        
        if not subscription_id:
            return jsonify({'error': 'Subscription ID is required'}), 400
        
        # Validate subscription ID format (alphanumeric and hyphens only)
        if not re.match(r'^[a-zA-Z0-9\-_]+$', subscription_id):
            return jsonify({'error': 'Invalid subscription ID format'}), 400
        
        is_valid, error_msg = validate_days_input(days)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
        
        # Generate unique analysis ID (sanitized)
        safe_subscription_id = re.sub(r'[^a-zA-Z0-9\-_]', '', subscription_id)
        analysis_id = f"sub_{safe_subscription_id}_{int(time.time())}"
        
        # Initialize analysis status
        with analysis_status_lock:
            analysis_status[analysis_id] = {
                'status': 'initializing',
                'progress': 0,
                'message': 'Initializing subscription analysis...',
                'subscription_id': subscription_id,
                'days': days,
                'report_type': report_type,
                'start_time': datetime.now().isoformat(),
                'current_step': 'Initialization',
                'step_start_time': datetime.now().isoformat()
            }
        
        # Start analysis in background thread
        thread = threading.Thread(
            target=run_subscription_analysis,
            args=(analysis_id,),
            daemon=True
        )
        thread.start()
        
        return jsonify({
            'success': True,
            'analysis_id': analysis_id,
            'message': 'Subscription analysis started'
        })
        
    except Exception as e:
        logger.error(f"Error starting subscription analysis: {e}")
        return jsonify({'error': str(e)}), 500


def run_subscription_analysis(analysis_id):
    """Run subscription-based analysis"""
    try:
        logger.info(f"[[START]] Starting subscription analysis: {analysis_id}")
        
        # Get analysis parameters
        with analysis_status_lock:
            status = analysis_status[analysis_id]
            subscription_id = status['subscription_id']
            days = status['days']
            report_type = status['report_type']
        
        # Update status
        with analysis_status_lock:
            status['status'] = 'running'
            status['progress'] = 10
            status['message'] = ' Fetching subscription data...'
            status['current_step'] = 'Data Retrieval'
            status['step_start_time'] = datetime.now().isoformat()
        
        # Get subscription data
        sub_data = fetch_subscription_data(subscription_id, days)
        
        if not sub_data['found']:
            with analysis_status_lock:
                status['status'] = 'error'
                status['message'] = f" Subscription not found: {sub_data.get('error', 'Unknown error')}"
            save_analysis_status()
            return
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 30
            status['message'] = ' Analyzing subscription data...'
            status['current_step'] = 'Data Analysis'
        
        # Convert to DataFrames for analysis
        ab_df = pd.DataFrame(sub_data['adoption_barriers']) if sub_data['adoption_barriers'] else pd.DataFrame()
        ap_df = pd.DataFrame(sub_data['action_plans']) if sub_data['action_plans'] else pd.DataFrame()
        cp_df = pd.DataFrame(sub_data['customer_pulse']) if sub_data['customer_pulse'] else pd.DataFrame()
        sp_df = pd.DataFrame(sub_data['success_priorities']) if sub_data['success_priorities'] else pd.DataFrame()
        
        # Calculate renewal risk
        renewal_analysis = get_subscription_renewal_risk(subscription_id, days)
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 60
            status['message'] = '[AI] Generating AI insights...'
            status['current_step'] = 'AI Analysis'
        
        # Generate AI insights using existing logic
        try:
            # Create briefing book
            all_records = sub_data['adoption_barriers'] + sub_data['action_plans'] + sub_data['customer_pulse'] + sub_data['success_priorities']
            briefing_book, metrics = _create_briefing_book(all_records, [], [])
            
            # Generate AI response
            ai_response = generate_llm_response(
                briefing_book, 
                sub_data['customer_name'], 
                subscription_id, 
                days,
                PROMPT_CUSTOMER_TEMPLATE
            )
        except Exception as e:
            logger.warning(f"AI analysis failed, using fallback: {e}")
            ai_response = f"Analysis completed for {sub_data['customer_name']} (Subscription: {subscription_id})"
        
        # Update status
        with analysis_status_lock:
            status['progress'] = 80
            status['message'] = ' Generating reports...'
            status['current_step'] = 'Report Generation'
        
        # Generate reports (canonical outputs when frozen)
        output_dir = _APP_SUPPORT / "outputs" if _frozen else Path("outputs")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_customer_name = "".join(c for c in sub_data['customer_name'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_subscription_id = subscription_id.replace(':', '_').replace('/', '_')
        
        # Generate Word report
        word_filename = f"Subscription_Analysis_{safe_customer_name}_{safe_subscription_id}_{timestamp}.docx"
        word_path = output_dir / word_filename
        
        try:
            doc = Document()
            
            # Title page
            title = doc.add_heading(f'Subscription Analysis: {sub_data["customer_name"]}', 0)
            subtitle = doc.add_heading(f'Subscription ID: {subscription_id}', level=1)
            details = doc.add_paragraph()
            details.add_run(f'Analysis Date: {datetime.now().strftime("%B %d, %Y")}\n').bold = True
            details.add_run(f'Analysis Period: {days} days\n').bold = True
            details.add_run(f'Technology: {sub_data["technology"]} | Sub-Technology: {sub_data["sub_technology"]}\n').bold = True
            details.add_run(f'Status: {sub_data["status"]}\n').bold = True
            
            doc.add_page_break()
            
            # Executive Summary
            doc.add_heading('Executive Summary', level=1)
            summary_p = doc.add_paragraph()
            summary_p.add_run(f'Customer: {sub_data["customer_name"]}\n')
            summary_p.add_run(f'Subscription: {subscription_id}\n')
            summary_p.add_run(f'Renewal Risk Level: {renewal_analysis["risk_level"]} ({renewal_analysis["overall_risk_score"]}/10)\n')
            
            # Risk Analysis
            doc.add_heading('Renewal Risk Analysis', level=1)
            risk_p = doc.add_paragraph()
            risk_p.add_run(f'Overall Risk Score: {renewal_analysis["overall_risk_score"]}/10\n').bold = True
            risk_p.add_run(f'Risk Level: {renewal_analysis["risk_level"]}\n').bold = True
            
            # Risk Components
            doc.add_heading('Risk Components', level=2)
            for component, data in renewal_analysis['risk_components'].items():
                comp_p = doc.add_paragraph()
                comp_p.add_run(f'{component.replace("_", " ").title()}: ').bold = True
                comp_p.add_run(f'{data["score"]:.1f}/10 - Count: {data["count"]}')
            
            # Recommendations
            doc.add_heading('Recommendations', level=1)
            for i, rec in enumerate(renewal_analysis['recommendations'], 1):
                rec_p = doc.add_paragraph()
                rec_p.add_run(f'{i}. {rec}')
            
            # Data Summary
            doc.add_heading('Data Summary', level=1)
            summary_table = doc.add_table(rows=1, cols=2)
            summary_table.style = 'Table Grid'
            
            # Header
            header_cells = summary_table.rows[0].cells
            header_cells[0].text = 'Data Type'
            header_cells[1].text = 'Count'
            
            # Add data
            for data_type, count in sub_data['summary'].items():
                row_cells = summary_table.add_row().cells
                row_cells[0].text = data_type.replace('_', ' ').title()
                row_cells[1].text = str(count)
            
            # Detailed Data Sections
            doc.add_heading('Detailed Data Analysis', level=1)
            
            # Adoption Barriers Section
            if not ab_df.empty:
                doc.add_heading('Adoption Barriers', level=2)
                ab_p = doc.add_paragraph()
                ab_p.add_run(f'Found {len(ab_df)} adoption barriers:')
                
                # Show critical/high severity barriers
                critical_ab = ab_df[ab_df['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)] if 'SEVERITY_C' in ab_df.columns else ab_df.iloc[0:0]
                if not critical_ab.empty:
                    ab_p.add_run(f' {len(critical_ab)} critical/high severity barriers requiring immediate attention.')
                
                # Show recent barriers
                if 'CREATED_DATE' in ab_df.columns:
                    try:
                        ab_df['CREATED_DATE'] = pd.to_datetime(ab_df['CREATED_DATE'], errors='coerce')
                        recent_cutoff = datetime.now() - timedelta(days=30)
                        recent_ab = ab_df[ab_df['CREATED_DATE'] >= recent_cutoff]
                        if not recent_ab.empty:
                            ab_p.add_run(f' {len(recent_ab)} barriers created in the last 30 days.')
                    except Exception:
                        pass
            else:
                doc.add_heading('Adoption Barriers', level=2)
                ab_p = doc.add_paragraph()
                ab_p.add_run('No adoption barriers found for this subscription.')
            
            # Action Plans Section
            if not ap_df.empty:
                doc.add_heading('Action Plans', level=2)
                ap_p = doc.add_paragraph()
                ap_p.add_run(f'Found {len(ap_df)} action plans:')
                
                # Show unresolved plans
                if 'STATUS_C' in ap_df.columns:
                    unresolved_plans = ap_df[ap_df['STATUS_C'].astype(str).str.contains('Open|New|In Progress', case=False, na=False)]
                else:
                    unresolved_plans = ap_df.iloc[0:0]
                if not unresolved_plans.empty:
                    ap_p.add_run(f' {len(unresolved_plans)} unresolved action plans.')
                
                # Show completed plans
                if 'STATUS_C' in ap_df.columns:
                    completed_plans = ap_df[ap_df['STATUS_C'].astype(str).str.contains('Completed|Closed', case=False, na=False)]
                else:
                    completed_plans = ap_df.iloc[0:0]
                if not completed_plans.empty:
                    ap_p.add_run(f' {len(completed_plans)} completed action plans.')
            else:
                doc.add_heading('Action Plans', level=2)
                ap_p = doc.add_paragraph()
                ap_p.add_run('No action plans found for this subscription.')
            
            # Customer Pulse Section
            if not cp_df.empty:
                doc.add_heading('Customer Pulse', level=2)
                cp_p = doc.add_paragraph()
                cp_p.add_run(f'Found {len(cp_df)} customer pulse records:')
                
                # Show pulse ratings
                if 'PULSE_RATING__C' in cp_df.columns:
                    pulse_counts = cp_df['PULSE_RATING__C'].value_counts()
                    for rating, count in pulse_counts.items():
                        cp_p.add_run(f' {count} {rating} ratings.')
            else:
                doc.add_heading('Customer Pulse', level=2)
                cp_p = doc.add_paragraph()
                cp_p.add_run('No customer pulse records found for this subscription.')
            
            # Success Priorities Section
            if not sp_df.empty:
                doc.add_heading('Success Priorities', level=2)
                sp_p = doc.add_paragraph()
                sp_p.add_run(f'Found {len(sp_df)} success priorities:')
                
                # Show priority status
                if 'STATUS__C' in sp_df.columns:
                    status_counts = sp_df['STATUS__C'].value_counts()
                    for status, count in status_counts.items():
                        sp_p.add_run(f' {count} {status} priorities.')
            else:
                doc.add_heading('Success Priorities', level=2)
                sp_p = doc.add_paragraph()
                sp_p.add_run('No success priorities found for this subscription.')
            
            # AI Analysis
            if ai_response:
                doc.add_heading('AI Analysis', level=1)
                ai_p = doc.add_paragraph()
                ai_p.add_run(ai_response)
            
            # Save document
            doc.save(word_path)
            
        except Exception as e:
            logger.error(f"Error creating Word report: {e}")
        
        # Generate Excel report
        excel_filename = f"Subscription_Analysis_{safe_customer_name}_{safe_subscription_id}_{timestamp}.xlsx"
        excel_path = output_dir / excel_filename
        
        try:
            with pd.ExcelWriter(excel_path, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Create formats
                header_format = workbook.add_format({
                    'bold': True,
                    'text_wrap': True,
                    'valign': 'top',
                    'fg_color': '#D7E4BC',
                    'border': 1
                })
                
                title_format = workbook.add_format({
                    'bold': True,
                    'font_size': 14,
                    'fg_color': '#4F81BD',
                    'font_color': 'white',
                    'border': 1
                })
                
                # Summary sheet
                summary_data = {
                    'Metric': ['Customer Name', 'Subscription ID', 'Technology', 'Sub-Technology', 'Status', 
                               'Analysis Period (Days)', 'Renewal Risk Score', 'Risk Level'],
                    'Value': [sub_data['customer_name'], subscription_id, sub_data['technology'], 
                              sub_data['sub_technology'], sub_data['status'], days,
                              renewal_analysis['overall_risk_score'], renewal_analysis['risk_level']]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
                
                # Risk Components sheet
                risk_data = []
                for component, data in renewal_analysis['risk_components'].items():
                    risk_data.append({
                        'Component': component.replace('_', ' ').title(),
                        'Score': data['score'],
                        'Count': data['count']
                    })
                risk_df = pd.DataFrame(risk_data)
                risk_df.to_excel(writer, sheet_name='Risk_Components', index=False)
                
                # Data sheets - Always create tabs even if empty
                # Adoption Barriers
                if not ab_df.empty:
                    ab_df.to_excel(writer, sheet_name='Adoption_Barriers', index=False)
                else:
                    # Create empty sheet with headers
                    empty_ab_df = pd.DataFrame(columns=['ID', 'SUBJECT_C', 'SEVERITY_C', 'AB_STATUS_C', 'ASSIGNEE_C', 'CREATED_DATE', 'DESCRIPTION_C'])
                    empty_ab_df.to_excel(writer, sheet_name='Adoption_Barriers', index=False)
                
                # Action Plans
                if not ap_df.empty:
                    ap_df.to_excel(writer, sheet_name='Action_Plans', index=False)
                else:
                    # Create empty sheet with headers
                    empty_ap_df = pd.DataFrame(columns=['ID', 'ACTION_PLAN_TITLE_C', 'STATUS_C', 'ASSIGNEE_C', 'CREATED_DATE', 'DESCRIPTION_C'])
                    empty_ap_df.to_excel(writer, sheet_name='Action_Plans', index=False)
                
                # Customer Pulse
                if not cp_df.empty:
                    cp_df.to_excel(writer, sheet_name='Customer_Pulse', index=False)
                else:
                    # Create empty sheet with headers
                    empty_cp_df = pd.DataFrame(columns=['ID', 'PULSE_RATING__C', 'COMMENTS__C', 'CREATEDDATE', 'ACCOUNT__C'])
                    empty_cp_df.to_excel(writer, sheet_name='Customer_Pulse', index=False)
                
                # Success Priorities
                if not sp_df.empty:
                    sp_df.to_excel(writer, sheet_name='Success_Priorities', index=False)
                else:
                    # Create empty sheet with headers
                    empty_sp_df = pd.DataFrame(columns=['ID', 'SUCCESS_PRIORITY_TITLE__C', 'STATUS__C', 'CREATEDDATE', 'RELATED_CUSTOMER__C'])
                    empty_sp_df.to_excel(writer, sheet_name='Success_Priorities', index=False)
                
                # Format sheets
                for sheet_name in writer.sheets:
                    worksheet = writer.sheets[sheet_name]
                    worksheet.set_column('A:Z', 20)
                    
                    # Format headers for each sheet based on its actual data
                    if sheet_name == 'Summary':
                        # Format summary sheet headers
                        for col_num, value in enumerate(summary_df.columns.values):
                            worksheet.write(0, col_num, value, header_format)
                    else:
                        # Format other sheets - check if they have data
                        if sheet_name in ['Adoption_Barriers', 'Action_Plans', 'Customer_Pulse', 'Success_Priorities']:
                            # These sheets have headers even when empty
                            if sheet_name == 'Adoption_Barriers' and not ab_df.empty:
                                for col_num, value in enumerate(ab_df.columns.values):
                                    worksheet.write(0, col_num, value, header_format)
                            elif sheet_name == 'Action_Plans' and not ap_df.empty:
                                for col_num, value in enumerate(ap_df.columns.values):
                                    worksheet.write(0, col_num, value, header_format)
                            elif sheet_name == 'Customer_Pulse' and not cp_df.empty:
                                for col_num, value in enumerate(cp_df.columns.values):
                                    worksheet.write(0, col_num, value, header_format)
                            elif sheet_name == 'Success_Priorities' and not sp_df.empty:
                                for col_num, value in enumerate(sp_df.columns.values):
                                    worksheet.write(0, col_num, value, header_format)
                            else:
                                # Empty sheets - format the empty DataFrame headers
                                empty_df = pd.DataFrame()
                                if sheet_name == 'Adoption_Barriers':
                                    empty_df = pd.DataFrame(columns=['ID', 'SUBJECT_C', 'SEVERITY_C', 'AB_STATUS_C', 'ASSIGNEE_C', 'CREATED_DATE', 'DESCRIPTION_C'])
                                elif sheet_name == 'Action_Plans':
                                    empty_df = pd.DataFrame(columns=['ID', 'ACTION_PLAN_TITLE_C', 'STATUS_C', 'ASSIGNEE_C', 'CREATED_DATE', 'DESCRIPTION_C'])
                                elif sheet_name == 'Customer_Pulse':
                                    empty_df = pd.DataFrame(columns=['ID', 'PULSE_RATING__C', 'COMMENTS__C', 'CREATEDDATE', 'ACCOUNT__C'])
                                elif sheet_name == 'Success_Priorities':
                                    empty_df = pd.DataFrame(columns=['ID', 'SUCCESS_PRIORITY_TITLE__C', 'STATUS__C', 'CREATEDDATE', 'RELATED_CUSTOMER__C'])
                                
                                for col_num, value in enumerate(empty_df.columns.values):
                                    worksheet.write(0, col_num, value, header_format)
            
        except Exception as e:
            logger.error(f"Error creating Excel report: {e}", exc_info=True)
            excel_path = None
            excel_filename = None
        
        # Update status
        with analysis_status_lock:
            status['status'] = 'completed'
            status['progress'] = 100
            status['message'] = ' Subscription analysis completed successfully!'
            status['current_step'] = 'Completed'
            status['word_file'] = word_filename
            status['excel_file'] = excel_filename
            # Keep canonical keys consistent with /download endpoint expectations
            status['word_report'] = str(word_path)
            status['excel_report'] = str(excel_path) if excel_path else None
            status['end_time'] = datetime.now().isoformat()
            status['completion_time'] = status['end_time']
            report_type = status.get('report_type', 'renewal')
            manager = status.get('manager', '')
            technology = status.get('technology', status.get('tech', ''))
            customer_name = status.get('customer_name', '')
            start_time = status.get('start_time', '')
            end_time = status['end_time']
            insights_payload = _build_insights_payload(status, 'Subscription analysis completed')

        # Persist outside lock to reduce lock contention during file I/O.
        save_analysis_status()

        auto_audit_report(analysis_id)
        record_report_completion(
            analysis_id, report_type, manager, technology, customer_name,
            'completed', start_time, end_time
        )
        try:
            store_report_insights(
                analysis_id, report_type, manager, technology, customer_name, insights_payload
            )
        except Exception:
            pass
        
        logger.info(f"[[OK]] Subscription analysis completed: {analysis_id}")
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error in subscription analysis: {e}")
        with analysis_status_lock:
            if analysis_id in analysis_status:
                analysis_status[analysis_id]['status'] = 'error'
                analysis_status[analysis_id]['message'] = f' Analysis failed: {str(e)}'
        save_analysis_status()


@app.route('/download/<analysis_id>/<file_type>')
def download_result(analysis_id, file_type):
    """Download analysis results with enhanced error handling and logging"""
    logger.info(f"[[DOWNLOAD]] Download request: {analysis_id}/{file_type}")
    
    # Validate file_type (whitelist)
    if file_type not in ('docx', 'xlsx'):
        return jsonify({'error': f'Invalid file type: {file_type}', 'available_files': ['docx', 'xlsx']}), 404
    
    # Check if analysis exists (try saved file if not in memory, e.g. after restart)
    if analysis_id not in analysis_status:
        try:
            status_file_path = str(_APP_SUPPORT / STATUS_FILE) if not os.path.isabs(STATUS_FILE) else STATUS_FILE
            if os.path.exists(status_file_path):
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    loaded_status = json.load(f)
                    if analysis_id in loaded_status:
                        with analysis_status_lock:
                            analysis_status[analysis_id] = loaded_status[analysis_id]
                    else:
                        logger.error(f"[[ERROR]] Analysis not found in memory or file: {analysis_id}")
                        return jsonify({
                            'error': 'Analysis not found',
                            'analysis_id': analysis_id,
                        }), 404
            else:
                logger.error(f"[[ERROR]] Analysis not found and no status file: {analysis_id}")
                return jsonify({
                    'error': 'Analysis not found',
                    'analysis_id': analysis_id,
                }), 404
        except Exception as e:
            logger.error(f"[[ERROR]] Error loading analysis status from file: {e}", exc_info=True)
            return jsonify({
                'error': 'Analysis not found',
                'analysis_id': analysis_id,
            }), 404
    
    status = analysis_status[analysis_id]
    logger.info(f"[[DATA]] Analysis status: {status.get('status', 'unknown')}")
    
    # Check if analysis is completed
    if status.get('status') != 'completed':
        logger.error(f"[[ERROR]] Analysis not completed: {status.get('status', 'unknown')}")
        return jsonify({
            'error': f'Analysis not completed. Current status: {status.get("status", "unknown")}',
            'analysis_id': analysis_id,
            'current_status': status.get('status', 'unknown')
        }), 400
    
    # Check if results exist - handle both formats (direct and nested in 'results')
    word_report = status.get('word_report')
    excel_report = status.get('excel_report')
    
    # For leader reports and some other report types, results are nested
    if not word_report and not excel_report:
        results = status.get('results', {})
        if isinstance(results, dict):
            word_report = results.get('word_report')
            excel_report = results.get('excel_report')
    
    if not word_report and not excel_report:
        logger.error(f"[[ERROR]] No reports available for analysis: {analysis_id}")
        logger.info(f"[[DATA]] Status structure: {status.keys()}")
        return jsonify({
            'error': 'No results available for this analysis',
            'analysis_id': analysis_id,
            'status_keys': list(status.keys())
        }), 400
    
    logger.info(f"[[FILE]] Available reports - Word: {word_report}, Excel: {excel_report}")
    
    # Canonical outputs dir (Application Support when frozen) so download works even if status stored wrong path
    _out_dir = _APP_SUPPORT / "outputs" if _frozen else Path(os.path.abspath("outputs"))

    # Handle file downloads with better error handling
    try:
        if file_type == 'docx' and word_report:
            file_path = word_report
            if not os.path.exists(file_path):
                fallback = _out_dir / os.path.basename(file_path)
                if fallback.exists():
                    file_path = str(fallback)
            logger.info(f"[[WRITE]] Downloading Word document: {file_path}")
            if not os.path.exists(file_path):
                logger.error(f"[[ERROR]] Word file not found: {file_path}")
                return jsonify({'error': f'Word file not found: {file_path}'}), 404
            safe_name = secure_filename(analysis_id) or "report"
            return send_file(file_path, as_attachment=True, download_name=f"AdoptIQ_Report_{safe_name}.docx")

        elif file_type == 'xlsx' and excel_report:
            file_path = excel_report
            if not os.path.exists(file_path):
                fallback = _out_dir / os.path.basename(file_path)
                if fallback.exists():
                    file_path = str(fallback)
            logger.info(f"[[DATA]] Downloading Excel file: {file_path}")
            if not os.path.exists(file_path):
                logger.error(f"[[ERROR]] Excel file not found: {file_path}")
                return jsonify({'error': f'Excel file not found: {file_path}'}), 404
            safe_name = secure_filename(analysis_id) or "data"
            return send_file(file_path, as_attachment=True, download_name=f"AdoptIQ_Data_{safe_name}.xlsx")
            
        else:
            logger.error(f"[[ERROR]] Invalid file type or file not found: {file_type}")
            # Only show files that actually exist
            available = []
            if word_report:
                available.append('docx')
            if excel_report:
                available.append('xlsx')
            return jsonify({
                'error': f'Invalid file type or file not found: {file_type}',
                'available_files': available
            }), 404
            
    except Exception as e:
        logger.error(f"[[ERROR]] Download error: {str(e)}")
        return jsonify({'error': f'Download failed: {str(e)}'}), 500


@app.route('/simple_test', methods=['POST'])
def simple_test():
    """Ultra-simple test endpoint"""
    try:
        logger.info(f"[[SEARCH]] Simple test endpoint called")
        return jsonify({'success': True, 'message': 'Simple test successful'})
    except Exception as e:
        logger.error(f"[[ERROR]] Simple test failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/test_generate_report', methods=['POST'])
def test_generate_report():
    """Test endpoint that returns success immediately"""
    try:
        logger.info(f"[[SEARCH]] Test generate report endpoint called")
        
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json() or {}
            logger.info(f"[[LIST]] JSON data received: {data}")
        else:
            # Handle form data
            try:
                days_val = int(request.form.get('days', 30))
            except (ValueError, TypeError):
                days_val = 30
            data = {
                'manager': request.form.get('manager', 'Test Manager'),
                'technology': request.form.get('technology', 'Cisco UCCE'),
                'days': days_val
            }
            logger.info(f"[[LIST]] Form data received: {data}")
        
        manager = data.get('manager', 'Test Manager')
        technology = data.get('technology', 'Cisco UCCE')
        days = data.get('days', 30)
        
        # Create a test analysis ID
        analysis_id = f"Test_{manager.replace(' ', '_')}_{technology}_{days}d_{int(time.time())}"
        
        logger.info(f"[[LIST]] Created analysis ID: {analysis_id}")
        
        # Set up minimal status
        with analysis_status_lock:
            analysis_status[analysis_id] = {
                'status': 'completed',
                'progress': 100,
                'message': 'Test analysis completed successfully',
                'manager': manager,
                'technology': technology,
                'days': days,
                'start_time': datetime.now().isoformat(),
                'current_step': 'Test Complete',
                'report_type': 'compact'
            }
            save_analysis_status()
        
        logger.info(f"[[OK]] Test analysis completed: {analysis_id}")
        
        return jsonify({
            'success': True,
            'analysis_id': analysis_id,
            'message': 'Test analysis completed successfully',
            'redirect_url': f'/progress/{analysis_id}'
        })
        
    except Exception as e:
        logger.error(f"[[ERROR]] Test analysis failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/clear_stuck_analyses', methods=['POST'])
def clear_stuck_analyses():
    """Clear any stuck analyses that might be blocking new ones"""
    try:
        with analysis_status_lock:
            cleared_count = 0
            current_time = datetime.now()
            
            for analysis_id, status in list(analysis_status.items()):
                # Clear analyses that have been running for more than 10 minutes
                if status.get('status') == 'running':
                    start_time_str = status.get('start_time', '')
                    if start_time_str:
                        try:
                            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                            if (current_time - start_time).total_seconds() > 600:  # 10 minutes
                                status['status'] = 'cancelled'
                                status['message'] = 'Analysis cancelled - was stuck'
                                cleared_count += 1
                        except Exception:
                            # If we can't parse the time, clear it anyway
                            status['status'] = 'cancelled'
                            status['message'] = 'Analysis cancelled - was stuck'
                            cleared_count += 1
            
            if cleared_count > 0:
                save_analysis_status()
                logger.info(f"[[CLEAN]] Cleared {cleared_count} stuck analyses")
                return jsonify({'success': True, 'message': f'Cleared {cleared_count} stuck analyses'})
            else:
                return jsonify({'success': True, 'message': 'No stuck analyses found'})
                
    except Exception as e:
        logger.error(f"[[ERROR]] Error clearing stuck analyses: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# LEADER REPORT ROUTES
# ============================================================================

@app.route('/start_leader_report', methods=['POST'])
def start_leader_report():
    """Start a leader report generation"""
    try:
        # Get form data
        manager = request.form.get('manager')
        try:
            days = int(request.form.get('days', 90))
        except (ValueError, TypeError):
            days = 90
        
        # Validate inputs
        is_valid_mgr, error_msg = validate_manager_input(manager)
        if not is_valid_mgr:
            return jsonify({'success': False, 'error': error_msg}), 400
        
        is_valid_days, error_msg = validate_days_input(days)
        if not is_valid_days:
            return jsonify({'success': False, 'error': error_msg}), 400
        
        # Handle optional CSOne file upload
        csone_file = None
        if 'csone_file' in request.files:
            file = request.files['csone_file']
            if file and file.filename:
                is_valid, error_msg = validate_file_upload(file)
                if not is_valid:
                    return jsonify({'success': False, 'error': error_msg}), 400
                
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                csone_file = filepath
        # When no file: use most recent from OneDrive folder (macro places reports daily)
        if not csone_file:
            csone_file = get_latest_csone_from_folder()
        
        # Generate unique analysis ID
        timestamp = int(time.time())
        analysis_id = f"Leader_{manager.replace(' ', '_')}_{days}d_{timestamp}"
        
        # Initialize status
        with analysis_status_lock:
            analysis_status[analysis_id] = {
                'status': 'starting',
                'progress': 0,
                'message': ' Initializing Leader Report Generation...',
                'start_time': datetime.now().isoformat(),
                'current_step': 'Initialization',
                'manager': manager,
                'days': days,
                'report_type': 'leader',
                'csone_file': csone_file,
                'results': None,
                'error': None
            }
            save_analysis_status()
        
        # Start leader report generation in background thread
        thread = threading.Thread(target=run_leader_report_generation, args=(analysis_id,))
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'analysis_id': analysis_id,
            'redirect_url': url_for('progress', analysis_id=analysis_id)
        })
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error starting leader report: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def run_leader_report_generation(analysis_id):
    """Background thread to generate leader report"""
    ctx = None
    try:
        with analysis_status_lock:
            status = analysis_status[analysis_id]
            status['status'] = 'running'
            status['progress'] = 5
            status['message'] = 'Connecting to Snowflake (CSConsole data)...'
            status['current_step'] = 'Database Connection'
            save_analysis_status()
        
        manager = status['manager']
        days = status['days']
        csone_file = status.get('csone_file')
        
        # Connect to Snowflake - with VPN detection
        logger.info(f"Connecting to Snowflake for leader report...")
        try:
            ctx = _connect_with_keeper()
            logger.info(f"SUCCESS: Connected to Snowflake")
        except Exception as conn_error:
            error_msg = str(conn_error)
            
            # Check if it's a VPN/network error
            if any(keyword in error_msg.lower() for keyword in ['not allowed to access', 'failed to connect', 'network', 'timeout']):
                logger.error(f"ERROR: Snowflake connection failed - VPN may not be connected")
                
                with analysis_status_lock:
                    status['status'] = 'error'
                    status['error'] = 'VPN Connection Required'
                    status['message'] = (
                        'ERROR: Unable to connect to Snowflake database.\n\n'
                        'Leader Reports require access to both:\n'
                        '• CSConsole data (Action Plans, Adoption Barriers, Customer Pulse) - requires Cisco VPN\n'
                        '• CSOne data (TAC cases) - from uploaded Excel file\n\n'
                        'ACTION REQUIRED:\n'
                        '1. Connect to Cisco VPN\n'
                        '2. Verify VPN is active and connected\n'
                        '3. Try generating the report again\n\n'
                        'Without VPN connection, the report cannot access team member subscriptions, '
                        'customers, or CSConsole activities from Snowflake.'
                    )
                    status['completion_time'] = datetime.now().isoformat()
                    save_analysis_status()
                
                return
            else:
                # Some other connection error
                raise
        
        # Update progress with ETA
        with analysis_status_lock:
            status['progress'] = 20
            status['message'] = ' Fetching team data...'
            status['current_step'] = 'Team Data Retrieval'
            status['eta_seconds'] = 240  # Estimated 4 minutes remaining
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=4)).isoformat()
            save_analysis_status()
        
        # Fetch team subscriptions first (needed to scope CSOne to manager's portfolio)
        team_subs_df = pd.DataFrame()
        try:
            cssm_emails = [email for mgr, name, email in TEAM_ROSTER if mgr == manager]
            if cssm_emails:
                team_subs_df = get_subscriptions_for_team(ctx, cssm_emails)
                logger.info(f"[[OK]] Retrieved {len(team_subs_df)} team subscriptions for {manager}")
        except Exception as e:
            logger.warning(f"[[WARNING]] Failed to fetch team subscriptions: {e}")
        
        # Load and scope CSOne data to team portfolio (same approach as Comprehensive report)
        csone_df = pd.DataFrame()
        # Resolve CSOne path (may be stored as filename only; try uploads folder)
        csone_path = csone_file if (csone_file and os.path.exists(csone_file)) else None
        if csone_file and not csone_path:
            safe_name = secure_filename(os.path.basename(csone_file))
            if safe_name:
                uploads_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                if os.path.exists(uploads_path):
                    csone_path = uploads_path
        # When no file provided: try OneDrive folder (macro places reports daily)
        if not csone_path:
            csone_path = get_latest_csone_from_folder()
        if csone_path and os.path.exists(csone_path):
            logger.info(f"[[FILE]] Loading CSOne data from {csone_path}")
            csone_df_raw = load_csone_excel(csone_path)
            raw_count = len(csone_df_raw) if csone_df_raw is not None and not csone_df_raw.empty else 0
            logger.info(f"[[FILE]] Raw CSOne file: {raw_count} cases")
            
            csone_df_prepared = _prepare_csone(csone_df_raw if csone_df_raw is not None else pd.DataFrame(), team_subs_df)
            if not team_subs_df.empty:
                # Scope to team portfolio: team customers, date range (same as Comprehensive)
                team_customer_names = team_subs_df["BU_NAME"].dropna().unique().tolist()
                sub_ids = team_subs_df["SUBSCRIPTION_ID"].dropna().unique().tolist()
                csone_df = _apply_scope_filter_csone(csone_df_prepared, "All", days, sub_ids, team_customer_names)
                csone_count = len(csone_df)
                logger.info(f"[[OK]] Scoped to team portfolio: {csone_count} TAC cases in scope (from {raw_count} in file)")
            else:
                # Fallback: no team subscriptions - filter by date only to avoid irrelevant cases
                csone_df = _apply_scope_filter_csone_inclusive(csone_df_prepared, "All", days)
                csone_count = len(csone_df)
                logger.warning(f"[[WARNING]] No team subscriptions - using {csone_count} cases (date filter only)")
            
            with analysis_status_lock:
                status['progress'] = 30
                status['message'] = f' Loaded {csone_count} TAC cases in scope for team...'
                status['eta_seconds'] = 210  # Estimated 3.5 minutes remaining
                status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=3.5)).isoformat()
                save_analysis_status()
        
        # FIXED: Gather external intelligence (BST defects, status.webex.com incidents) for leader reports
        logger.info(f"[[WEB]] Gathering external intelligence for leader report...")
        try:
            ext_bugs = fetch_help_webex_bugs()
            ext_incidents = fetch_status_incidents()
            logger.info(f"[[OK]] External intelligence gathered: {len(ext_bugs)} bugs, {len(ext_incidents)} incidents")
        except Exception as e:
            logger.warning(f"[[WARNING]] External intelligence gathering failed: {e}")
            ext_bugs = []
            ext_incidents = []
        
        # Extract software defects and PSIRT vulnerabilities from CSOne data
        logger.info(f"[[DEFECTS]] Extracting software defects and PSIRT vulnerabilities...")
        software_defects = extract_software_defects(csone_df, pd.DataFrame()) if not csone_df.empty else None
        psirt_vulns = extract_psirt_vulnerabilities(csone_df, pd.DataFrame()) if not csone_df.empty else None
        if software_defects:
            logger.info(f"[[DEFECTS]] Found {software_defects.get('total_defects', 0)} defects")
        if psirt_vulns:
            logger.info(f"[[PSIRT]] Found {psirt_vulns.get('total_vulnerabilities', 0)} vulnerabilities")
        
        # Update progress with ETA
        with analysis_status_lock:
            status['progress'] = 40
            status['message'] = ' Collecting Action Plans, Adoption Barriers, and Customer Pulse...'
            status['current_step'] = 'Data Collection'
            status['eta_seconds'] = 180  # Estimated 3 minutes remaining
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=3)).isoformat()
            save_analysis_status()
        
        # Generate the leader report
        logger.info(f"[[WRITE]] Generating leader report for {manager}...")
        # Note: External intelligence (ext_bugs, ext_incidents, software_defects, psirt_vulns) 
        # is now available and can be integrated into leader report sections if needed
        
        # Update progress with ETA
        with analysis_status_lock:
            status['progress'] = 50
            status['message'] = ' Generating Word document...'
            status['current_step'] = 'Document Generation'
            status['eta_seconds'] = 120  # Estimated 2 minutes remaining
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(minutes=2)).isoformat()
            save_analysis_status()
        
        # Check if we have a valid database connection before proceeding
        if ctx is None:
            raise Exception("Database connection failed. Please check your VPN connection and try again.")
        
        filepath, success_msg, team_data = generate_leader_report(
            manager_name=manager,
            days=days,
            ctx=ctx,
            team_roster=TEAM_ROSTER,
            csone_df=csone_df,
            ext_bugs=ext_bugs if 'ext_bugs' in locals() else [],
            ext_incidents=ext_incidents if 'ext_incidents' in locals() else [],
            software_defects=software_defects if 'software_defects' in locals() else None,
            psirt_vulns=psirt_vulns if 'psirt_vulns' in locals() else None
        )
        
        # Update progress with ETA
        with analysis_status_lock:
            status['progress'] = 85
            status['message'] = ' Generating Excel workbook...'
            status['current_step'] = 'Excel Report Generation'
            status['eta_seconds'] = 20  # Estimated 20 seconds remaining
            status['estimated_completion'] = (datetime.now() + pd.Timedelta(seconds=20)).isoformat()
            save_analysis_status()
        
        # Generate Excel file with team data
        excel_path = None
        try:
            logger.info(f"[[DATA]] Generating Excel file for leader report (reusing team_data from Word generation)...")
            
            # Prepare Excel file path
            base_path = str(Path(filepath).with_suffix(''))
            excel_path = f"{base_path}.xlsx"
            
            # Collect all team data into DataFrames for Excel
            all_action_plans = []
            all_adoption_barriers = []
            all_customer_pulse = []
            all_success_priorities = []
            all_tac_cases = []
            all_subscriptions = []
            
            for cssm_name, data in team_data.items():
                # Action Plans
                if not data.get('action_plans', pd.DataFrame()).empty:
                    ap_df = data['action_plans'].copy()
                    ap_df['CSSM'] = cssm_name
                    all_action_plans.append(ap_df)
                
                # Adoption Barriers
                if not data.get('adoption_barriers', pd.DataFrame()).empty:
                    ab_df = data['adoption_barriers'].copy()
                    ab_df['CSSM'] = cssm_name
                    all_adoption_barriers.append(ab_df)
                
                # Customer Pulse
                if not data.get('customer_pulse', pd.DataFrame()).empty:
                    cp_df = data['customer_pulse'].copy()
                    cp_df['CSSM'] = cssm_name
                    all_customer_pulse.append(cp_df)
                
                # Success Priorities
                if not data.get('success_priorities', pd.DataFrame()).empty:
                    sp_df = data['success_priorities'].copy()
                    sp_df['CSSM'] = cssm_name
                    all_success_priorities.append(sp_df)
                
                # TAC Cases
                if not data.get('tac_cases', pd.DataFrame()).empty:
                    tac_df = data['tac_cases'].copy()
                    tac_df['CSSM'] = cssm_name
                    all_tac_cases.append(tac_df)
                
                # Subscriptions
                if not data.get('subscriptions', pd.DataFrame()).empty:
                    sub_df = data['subscriptions'].copy()
                    sub_df['CSSM'] = cssm_name
                    all_subscriptions.append(sub_df)
            
            # Combine all DataFrames
            sheets = {}
            if all_action_plans:
                sheets['Action_Plans'] = pd.concat(all_action_plans, ignore_index=True)
            if all_adoption_barriers:
                sheets['Adoption_Barriers'] = pd.concat(all_adoption_barriers, ignore_index=True)
            if all_customer_pulse:
                sheets['Customer_Pulse'] = pd.concat(all_customer_pulse, ignore_index=True)
            if all_success_priorities:
                sheets['Success_Priorities'] = pd.concat(all_success_priorities, ignore_index=True)
            if all_tac_cases:
                sheets['TAC_Cases'] = pd.concat(all_tac_cases, ignore_index=True)
            if all_subscriptions:
                sheets['Subscriptions'] = pd.concat(all_subscriptions, ignore_index=True)
            
            # Add external data if available (all data sources for Leader report)
            if ext_bugs:
                sheets['External_Bugs'] = pd.DataFrame(ext_bugs)
            if ext_incidents:
                sheets['External_Incidents'] = pd.DataFrame(ext_incidents)
            # Software defects (from CSOne/Adoption Barriers)
            if software_defects and software_defects.get('total_defects', 0) > 0:
                defect_rows = []
                for cust, ids in (software_defects.get('defect_by_customer') or {}).items():
                    for did in (ids if isinstance(ids, (list, set)) else [ids]):
                        defect_rows.append({'Customer': cust, 'Defect_ID': did})
                if defect_rows:
                    sheets['Software_Defects'] = pd.DataFrame(defect_rows)
            # PSIRT vulnerabilities (from CSOne/Adoption Barriers)
            if psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0:
                vuln_rows = []
                for cust, ids in (psirt_vulns.get('vulnerability_by_customer') or {}).items():
                    for vid in (ids if isinstance(ids, (list, set)) else [ids]):
                        vuln_rows.append({'Customer': cust, 'Vulnerability_ID': vid})
                for vid in (psirt_vulns.get('cve_ids') or set()):
                    vuln_rows.append({'Customer': '', 'Vulnerability_ID': vid, 'Type': 'CVE'})
                for vid in (psirt_vulns.get('psirt_advisories') or set()):
                    vuln_rows.append({'Customer': '', 'Vulnerability_ID': vid, 'Type': 'PSIRT'})
                if vuln_rows:
                    sheets['PSIRT_Vulnerabilities'] = pd.DataFrame(vuln_rows)
            
            # Create Excel file
            if sheets:
                logger.info(f"[[WRITE]] Writing Excel file with {len(sheets)} sheets...")
                with pd.ExcelWriter(excel_path, engine='xlsxwriter') as writer:
                    workbook = writer.book
                    
                    # Create formats
                    header_format = workbook.add_format({
                        'bold': True,
                        'text_wrap': True,
                        'valign': 'top',
                        'fg_color': '#4472C4',
                        'font_color': 'white',
                        'border': 1,
                        'font_size': 11
                    })
                    
                    title_format = workbook.add_format({
                        'bold': True,
                        'font_size': 16,
                        'fg_color': '#2F4F4F',
                        'font_color': 'white',
                        'border': 1,
                        'align': 'center',
                        'valign': 'vcenter'
                    })
                    
                    for sheet_name, df in sheets.items():
                        if not df.empty:
                            # Clean datetime columns for Excel compatibility
                            df_clean = _clean_datetime_columns_for_excel(df)
                            
                            # Write data
                            df_clean.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
                            
                            # Format worksheet
                            worksheet = writer.sheets[sheet_name]
                            
                            # Write title
                            title_text = f"{sheet_name.replace('_', ' ')} - {manager} Team Report"
                            worksheet.merge_range(0, 0, 0, len(df_clean.columns)-1, title_text, title_format)
                            
                            # Format headers
                            for col_num, value in enumerate(df_clean.columns.values):
                                worksheet.write(1, col_num, value, header_format)
                            
                            # Auto-adjust column widths (guard against NaN from empty columns)
                            for i, col in enumerate(df_clean.columns):
                                try:
                                    content_max = df_clean[col].astype(str).map(len).max()
                                    content_max = content_max if pd.notna(content_max) else 0
                                except (ValueError, TypeError):
                                    content_max = 0
                                max_length = max(content_max, len(str(col)))
                                worksheet.set_column(i, i, min(max_length + 2, 50))
                
                logger.info(f"[[OK]] Excel file created successfully: {excel_path}")
            else:
                logger.warning(f"[[WARNING]] No data to write to Excel file")
                excel_path = None
                
        except Exception as excel_error:
            logger.error(f"[[ERROR]] Error creating Excel file: {excel_error}", exc_info=True)
            excel_path = None
        
        # Update progress
        with analysis_status_lock:
            status['progress'] = 100
            status['status'] = 'completed'
            status['message'] = ' Leader report generated successfully!'
            status['current_step'] = 'Complete'
            status['completion_time'] = datetime.now().isoformat()
            completion_time = status['completion_time']
            report_type = status.get('report_type', 'leader')
            manager = status.get('manager', '')
            technology = status.get('technology', status.get('tech', ''))
            customer_name = status.get('customer_name', '')
            start_time = status.get('start_time', '')
            insights_payload = _build_insights_payload(status, 'Leader report completed')
            status['word_report'] = filepath
            status['excel_report'] = excel_path if excel_path else None
            status['results'] = {
                'word_report': filepath,
                'excel_report': excel_path if excel_path else None,
                'success_message': success_msg
            }

        # Persist outside lock to reduce lock contention during file I/O.
        save_analysis_status()

        auto_audit_report(analysis_id)
        record_report_completion(
            analysis_id, report_type, manager, technology, customer_name,
            'completed', start_time, completion_time
        )
        try:
            store_report_insights(
                analysis_id, report_type, manager, technology, customer_name, insights_payload
            )
        except Exception:
            pass
        
        logger.info(f"[[OK]] Leader report completed: {analysis_id}")
        
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logger.error(f" Leader report generation failed: {e}", exc_info=True)
        logger.error(f"[[SEARCH]] DEBUG: Full traceback:\n{error_traceback}")
        
        with analysis_status_lock:
            status['status'] = 'error'
            status['error'] = str(e)
            status['message'] = f' Error: {str(e)}'
            status['completion_time'] = datetime.now().isoformat()
            save_analysis_status()
    finally:
        if ctx:
            try:
                ctx.close()
                logger.info(f"[[CLEANUP]] Database connection closed for leader report")
            except Exception as close_err:
                logger.warning(f"[[WARNING]] Error closing database connection: {close_err}")


@app.route('/leader_report_form')
def leader_report_form():
    """Display leader report configuration form"""
    # Filter out "All Managers" from the list for leader reports
    manager_list = [m for m in MANAGERS if m != "All Managers"]
    return render_template('leader_report_form.html', managers=manager_list)


@app.route('/bst_psirt_search')
def bst_psirt_search():
    """Display BST/PSIRT search interface"""
    return render_template('bst_psirt_search.html')


@app.route('/search_bst_defect', methods=['POST'])
def search_bst_defect():
    """Search for a specific BST defect and generate LLM summary"""
    try:
        data = request.get_json() or {}
        defect_id = data.get('defect_id', '').strip()
        
        if not defect_id:
            return jsonify({'error': 'Please provide a defect ID'}), 400
        
        # Import and initialize integrations
        from cisco_internal_integrations import CiscoInternalIntegrations
        import os
        
        integrations = CiscoInternalIntegrations(
            bst_api_key=os.environ.get('BST_API_KEY'),
            psirt_api_key=os.environ.get('PSIRT_API_KEY'),
            psirt_client_secret=os.environ.get('PSIRT_CLIENT_SECRET')
        )
        
        # Search and summarize
        result = integrations.search_and_summarize_defect(defect_id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'defect_id': result['defect_id'],
                'summary': result['summary'],
                'direct_link': result['direct_link'],
                'defect_info': {
                    'title': result['defect'].title if result['defect'] else None,
                    'status': result['defect'].status if result['defect'] else None,
                    'severity': result['defect'].severity if result['defect'] else None,
                    'product': result['defect'].product if result['defect'] else None,
                    'classification': result['defect'].classification.value if result['defect'] else None
                }
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error'],
                'defect_id': defect_id,
                'direct_link': result['direct_link']
            })
            
    except Exception as e:
        logger.error(f"Error in search_bst_defect: {e}", exc_info=True)
        return jsonify({'error': f'Internal error: {str(e)}'}), 500


@app.route('/search_psirt_advisory', methods=['POST'])
def search_psirt_advisory():
    """Search for a specific PSIRT advisory and generate LLM summary"""
    try:
        data = request.get_json() or {}
        advisory_id = data.get('advisory_id', '').strip()
        
        if not advisory_id:
            return jsonify({'error': 'Please provide an advisory ID'}), 400
        
        # Import and initialize integrations
        from cisco_internal_integrations import CiscoInternalIntegrations
        import os
        
        integrations = CiscoInternalIntegrations(
            bst_api_key=os.environ.get('BST_API_KEY'),
            psirt_api_key=os.environ.get('PSIRT_API_KEY'),
            psirt_client_secret=os.environ.get('PSIRT_CLIENT_SECRET')
        )
        
        # Search and summarize
        result = integrations.search_and_summarize_vulnerability(advisory_id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'advisory_id': result['advisory_id'],
                'summary': result['summary'],
                'direct_link': result['direct_link'],
                'advisory_info': {
                    'title': result['vulnerability'].title if result['vulnerability'] else None,
                    'severity': result['vulnerability'].severity if result['vulnerability'] else None,
                    'cve_ids': result['vulnerability'].cve_ids if result['vulnerability'] else [],
                    'bug_ids': result['vulnerability'].bug_ids if result['vulnerability'] else [],
                    'products': result['vulnerability'].products if result['vulnerability'] else [],
                    'classification': result['vulnerability'].classification.value if result['vulnerability'] else None
                }
            })
        else:
            return jsonify({
                'success': False,
                'error': result['error'],
                'advisory_id': advisory_id,
                'direct_link': result['direct_link']
            })
            
    except Exception as e:
        logger.error(f"Error in search_psirt_advisory: {e}", exc_info=True)
        return jsonify({'error': f'Internal error: {str(e)}'}), 500


@app.route('/search_related_defects', methods=['POST'])
def search_related_defects():
    """Search for defects related to a product or issue"""
    try:
        data = request.get_json() or {}
        search_terms = data.get('search_terms', [])
        if isinstance(search_terms, str):
            search_terms = [s.strip() for s in search_terms.split(',') if s.strip()]
        product_filter = data.get('product_filter', None)
        try:
            days_back = int(data.get('days_back', 90))
            days_back = max(1, min(days_back, 365))
        except (ValueError, TypeError):
            days_back = 90
        
        if not search_terms:
            return jsonify({'error': 'Please provide search terms'}), 400
        
        # Import and initialize integrations
        from cisco_internal_integrations import CiscoInternalIntegrations
        import os
        
        integrations = CiscoInternalIntegrations(
            bst_api_key=os.environ.get('BST_API_KEY'),
            psirt_api_key=os.environ.get('PSIRT_API_KEY'),
            psirt_client_secret=os.environ.get('PSIRT_CLIENT_SECRET')
        )
        
        # Search for defects
        defects = integrations.search_defects_bst(
            search_terms=search_terms,
            product_filter=product_filter,
            days_back=days_back
        )
        
        # FIXED: Convert ALL results to serializable format
        defect_list = []
        for defect in defects:
            defect_list.append({
                'defect_id': defect.defect_id,
                'title': defect.title,
                'status': defect.status,
                'severity': defect.severity,
                'product': defect.product,
                'created_date': defect.created_date,
                'classification': defect.classification.value,
                'direct_link': f"https://bst.cisco.com/bugsearch/bug/{defect.defect_id}"
            })
        
        return jsonify({
            'success': True,
            'count': len(defect_list),
            'defects': defect_list
        })
            
    except Exception as e:
        logger.error(f"Error in search_related_defects: {e}", exc_info=True)
        return jsonify({'error': f'Internal error: {str(e)}'}), 500


@app.route('/search_related_vulnerabilities', methods=['POST'])
def search_related_vulnerabilities():
    """Search for vulnerabilities related to a product or issue"""
    try:
        data = request.get_json() or {}
        search_terms = data.get('search_terms', [])
        if isinstance(search_terms, str):
            search_terms = [s.strip() for s in search_terms.split(',') if s.strip()]
        product_filter = data.get('product_filter', None)
        try:
            days_back = int(data.get('days_back', 90))
            days_back = max(1, min(days_back, 365))
        except (ValueError, TypeError):
            days_back = 90
        
        if not search_terms:
            return jsonify({'error': 'Please provide search terms'}), 400
        
        # Import and initialize integrations
        from cisco_internal_integrations import CiscoInternalIntegrations
        import os
        
        integrations = CiscoInternalIntegrations(
            bst_api_key=os.environ.get('BST_API_KEY'),
            psirt_api_key=os.environ.get('PSIRT_API_KEY'),
            psirt_client_secret=os.environ.get('PSIRT_CLIENT_SECRET')
        )
        
        # Search for vulnerabilities
        vulnerabilities = integrations.search_psirt_vulnerabilities(
            search_terms=search_terms,
            product_filter=product_filter,
            days_back=days_back
        )
        
        # FIXED: Convert ALL results to serializable format
        vuln_list = []
        for vuln in vulnerabilities:
            vuln_list.append({
                'advisory_id': vuln.advisory_id,
                'title': vuln.title,
                'severity': vuln.severity,
                'cve_ids': vuln.cve_ids,  # Show ALL CVEs
                'bug_ids': vuln.bug_ids,  # Show ALL bug IDs
                'products': vuln.products,  # Show ALL products
                'published_date': vuln.published_date,
                'classification': vuln.classification.value,
                'direct_link': f"https://tools.cisco.com/security/center/content/CiscoSecurityAdvisory/{vuln.advisory_id}"
            })
        
        return jsonify({
            'success': True,
            'count': len(vuln_list),
            'vulnerabilities': vuln_list
        })
            
    except Exception as e:
        logger.error(f"Error in search_related_vulnerabilities: {e}", exc_info=True)
        return jsonify({'error': f'Internal error: {str(e)}'}), 500



def _check_port_available(port):
    """Return (available: bool, pid: int|None, process_name: str|None)."""
    import socket
    import subprocess
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('', port))
        s.close()
        return (True, None, None)
    except OSError:
        s.close()
    pid, name = None, None
    try:
        import psutil
        for conn in psutil.net_connections(kind='tcp'):
            if getattr(conn, 'laddr', None) and getattr(conn.laddr, 'port', None) == port:
                if getattr(conn, 'pid', None):
                    pid = conn.pid
                    try:
                        name = psutil.Process(pid).name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        name = "(PID %s)" % pid
                    break
    except (ImportError, psutil.AccessDenied, AttributeError):
        pass
    # Fallback: on macOS psutil may not return the listener PID; use lsof
    if pid is None and sys.platform == 'darwin':
        try:
            out = subprocess.check_output(
                ['lsof', '-i', ':%s' % port, '-t'],
                stderr=subprocess.DEVNULL, text=True, timeout=5
            )
            pids = [int(x) for x in out.strip().split() if x.strip()]
            if pids:
                pid = pids[0]
                try:
                    name = psutil.Process(pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    name = "(PID %s)" % pid
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass
    return (False, pid, name)


if __name__ == '__main__':
    PORT = 5001
    print("AdoptIQ Simple - AI-Powered Executive Analytics")
    print(version_string())
    print("Using EXACT CircuIT AI logic from your working script")
    print("Generating comprehensive AI-powered reports")
    print("Access at: http://localhost:%s/" % PORT)

    available, other_pid, other_name = _check_port_available(PORT)
    if not available:
        print("")
        print("Port %s is in use by another program." % PORT)
        if other_pid and other_name:
            print("  Process: %s (PID %s)" % (other_name, other_pid))
        if sys.platform == 'win32':
            print("  Either stop that program or run: taskkill /PID <PID> /F")
        else:
            print("  Either stop that program or run: lsof -i :%s  then  kill <PID>" % PORT)
        print("")

        # Packaged Mac: native dialog so user can choose to quit the other instance
        if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
            import subprocess as _sub
            _script = (
                'display dialog "Port %s is in use (another AdoptIQ may be running). '
                'Quit it and start this one?" with title "AdoptIQ" '
                'buttons {"Cancel", "Quit other & start"} default button 2' % PORT
            )
            try:
                _r = _sub.run(['osascript', '-e', _script], capture_output=True, text=True, timeout=15)
            except Exception:
                _r = None
            if _r and _r.returncode == 0 and 'Quit other & start' in (_r.stdout or ''):
                killed = False
                if other_pid:
                    try:
                        import psutil
                        p = psutil.Process(other_pid)
                        p.terminate()
                        p.wait(timeout=3)
                        killed = True
                    except Exception as e:
                        print("  Could not stop the process: %s" % e)
                if not killed and sys.platform == 'darwin':
                    try:
                        out = _sub.check_output(
                            ['lsof', '-i', ':%s' % PORT, '-t'],
                            stderr=_sub.DEVNULL, text=True, timeout=5
                        )
                        for pid_str in out.strip().split():
                            try:
                                pid = int(pid_str)
                                _sub.run(['kill', str(pid)], check=False, timeout=2, capture_output=True)
                                killed = True
                            except (ValueError, _sub.TimeoutExpired):
                                pass
                    except (_sub.CalledProcessError, _sub.TimeoutExpired, FileNotFoundError):
                        pass
                if killed:
                    time.sleep(1)
                    available, _, _ = _check_port_available(PORT)
                    if not available:
                        print("  Port still in use. Please close the other application and run AdoptIQ again.")
                        sys.exit(1)
                else:
                    print("  Could not find or kill the process using the port. Try: lsof -i :%s  then  kill <PID>" % PORT)
                    sys.exit(1)
            else:
                sys.exit(0)

        elif sys.stdin.isatty():
            try:
                choice = input("Kill the process using the port and start AdoptIQ? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = 'n'
            if choice == 'y':
                killed = False
                if other_pid:
                    try:
                        import psutil
                        p = psutil.Process(other_pid)
                        p.terminate()
                        p.wait(timeout=3)
                        killed = True
                    except Exception as e:
                        print("  Could not stop the process: %s" % e)
                if not killed and sys.platform == 'darwin':
                    try:
                        import subprocess
                        out = subprocess.check_output(
                            ['lsof', '-i', ':%s' % PORT, '-t'],
                            stderr=subprocess.DEVNULL, text=True, timeout=5
                        )
                        for pid_str in out.strip().split():
                            try:
                                pid = int(pid_str)
                                subprocess.run(['kill', str(pid)], check=False, timeout=2,
                                               capture_output=True)
                                killed = True
                            except (ValueError, subprocess.TimeoutExpired):
                                pass
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                        pass
                if killed:
                    time.sleep(1)
                    available, _, _ = _check_port_available(PORT)
                    if not available:
                        print("  Port still in use. Please close the other application and run AdoptIQ again.")
                        sys.exit(1)
                else:
                    if sys.platform == 'win32':
                        print("  Could not find or kill the process. Try: taskkill /PID %s /F" % (other_pid or "<PID>"))
                    else:
                        print("  Could not find or kill the process using the port. Try: lsof -i :%s  then  kill <PID>" % PORT)
                    sys.exit(1)
            else:
                print("Exiting.")
                sys.exit(0)
        else:
            print("Run AdoptIQ from Command Prompt (or Terminal) to choose: kill the process or quit.")
            sys.exit(1)

    # Launch browser after server starts (like Mac version)
    def _open_browser():
        time.sleep(BROWSER_LAUNCH_DELAY_SECONDS)  # Give server time to bind
        try:
            webbrowser.open('http://localhost:%s/' % PORT)
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()

    app.run(debug=False, host='0.0.0.0', port=PORT, use_reloader=False)
