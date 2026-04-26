# AdoptIQ Executive Analyzer - Configuration
# All secrets and DB credentials come from environment variables (.env). No hardcoded credentials.
# Version and build - updated by build scripts (macOS/Windows) before packaging.
ADOPTIQ_VERSION = "1.0.3"
ADOPTIQ_BUILD = "1"

def version_string():
    """e.g. 'v1.0.1 build 1'"""
    return "v" + ADOPTIQ_VERSION + " build " + ADOPTIQ_BUILD

from dotenv import load_dotenv
load_dotenv()

import os
import secrets
from pathlib import Path


def _resolve_secret_key() -> str:
    """Resolve app secret key from env or generate a dev-only ephemeral key."""
    configured = os.environ.get('SECRET_KEY') or os.environ.get('ADOPTIQ_SECRET_KEY')
    if configured:
        return configured
    # Avoid predictable hardcoded defaults in local/dev runs.
    return secrets.token_urlsafe(48)


def _is_production_env() -> bool:
    """Round 9 / Phase 1.4: shared production-readiness probe.

    Centralises the truthy detection used by both ``Config.DEBUG`` and
    ``Config.is_production_ready`` so the DEBUG-hardening guard below
    can re-use the exact same definition.  Production is asserted by
    either ``ADOPTIQ_PRODUCTION_READY=1`` or
    ``FLASK_ENV/APP_ENV/ENVIRONMENT=production``.
    """
    if str(os.environ.get('ADOPTIQ_PRODUCTION_READY', '')).strip().lower() in {'1', 'true', 'yes', 'on'}:
        return True
    env_value = (
        os.environ.get('FLASK_ENV')
        or os.environ.get('APP_ENV')
        or os.environ.get('ENVIRONMENT')
        or ''
    ).strip().lower()
    return env_value in {'production', 'prod'}


def _resolve_debug_flag() -> bool:
    """Round 9 / Phase 1.4: hardened DEBUG resolver.

    The previous form (``os.environ.get('DEBUG', 'false') in truthy``)
    let an operator ship a Flask binary with ``DEBUG=True`` to
    production by accident -- the classic Werkzeug-debugger-RCE
    footgun.  When the production gate is asserted we now force
    ``DEBUG=False`` regardless of env, log a warning to stderr so the
    misconfiguration is visible, and (if the override is *also*
    explicitly truthy) refuse to boot via :func:`enforce_production_safety`
    rather than silently honour it.
    """
    raw = str(os.environ.get('DEBUG', 'false')).strip().lower()
    requested = raw in {'true', '1', 'yes', 'on'}
    if _is_production_env() and requested:
        try:
            import sys as _sys
            print(
                "[config] WARNING: DEBUG=true requested while ADOPTIQ_PRODUCTION_READY=1; "
                "forcing DEBUG=False (Round 9 / Phase 1.4).",
                file=_sys.stderr,
            )
        except Exception:
            pass
        return False
    return requested


def enforce_production_safety() -> None:
    """Round 9 / Phase 1.4: hard-fail boot when production+DEBUG/TESTING.

    Defence-in-depth on top of :func:`_resolve_debug_flag`: callers
    (``app_simple.py`` startup) invoke this after ``Config`` is loaded
    so that an operator who *also* mutated ``Config.DEBUG``/``TESTING``
    after import (test fixture leaking into prod, custom subclass,
    etc.) still gets refused at boot rather than running a debug
    interpreter on a production socket.
    """
    if not _is_production_env():
        return
    if bool(getattr(Config, 'DEBUG', False)) or bool(getattr(Config, 'TESTING', False)):
        raise RuntimeError(
            "ADOPTIQ_PRODUCTION_READY=1 requires DEBUG=False and TESTING=False; "
            "refusing to boot (Round 9 / Phase 1.4)."
        )


class Config:
    # Flask Configuration (SECRET_KEY from env; generated ephemeral key otherwise)
    SECRET_KEY = _resolve_secret_key()
    # Round 9 / Phase 1.4: route DEBUG through the hardened resolver so
    # ``ADOPTIQ_PRODUCTION_READY=1`` always wins over a stray
    # ``DEBUG=true`` env entry.
    DEBUG = _resolve_debug_flag()
    # Round 9 / Phase 1.4: explicit TESTING flag participates in the same
    # production safety gate as DEBUG.  Default false; tests opt in.
    TESTING = str(os.environ.get('TESTING', 'false')).strip().lower() in {'true', '1', 'yes', 'on'} and not _is_production_env()
    VERBOSE_DEBUG = os.environ.get('ADOPTIQ_VERBOSE_DEBUG', 'false').lower() in ('true', '1', 'yes', 'on')
    FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
    
    # Analysis Configuration
    ANALYSIS_TIMEOUT = int(os.environ.get('ANALYSIS_TIMEOUT', '300'))
    STEP_TIMEOUT = int(os.environ.get('STEP_TIMEOUT', '60'))
    OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', './outputs')
    MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', '100'))
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', './uploads')
    TEMP_FOLDER = os.environ.get('TEMP_FOLDER', './temp')
    LOG_FOLDER = os.environ.get('LOG_FOLDER', './logs')
    
    # CSOne OneDrive folder: when no file is uploaded, use the most recent .xlsx from this folder.
    # Macro places reports here daily. Override via CSONE_ONEDRIVE_FOLDER env var.
    # Default: OneDrive - Cisco\Documents\AI Projects\AdoptIQ_CSOne_Reports
    # Use expanduser('~') for cross-platform (works on Windows, Mac, Linux)
    _default_csone_folder = os.path.join(
        os.path.expanduser('~'),
        'OneDrive - Cisco', 'Documents', 'AI Projects', 'AdoptIQ_CSOne_Reports'
    )
    CSONE_ONEDRIVE_FOLDER = os.environ.get('CSONE_ONEDRIVE_FOLDER') or _default_csone_folder

    # CSOne shared folder URL: opens in browser so users can download and
    # upload when the OneDrive folder isn't synced.  Configure via
    # ``CSONE_SHARED_FOLDER_URL``.  Round 7 / Phase 3.17: previously
    # defaulted to a personal SharePoint URL belonging to a single user
    # (``jestory_cisco_com``) which leaked operator identity into every
    # deployment and broke for anyone else.  No baked-in default now --
    # callers should treat ``None`` as "no shared folder configured".
    CSONE_SHARED_FOLDER_URL = os.environ.get('CSONE_SHARED_FOLDER_URL') or None

    # Round 7 / Phase 3.18: production-readiness gate is derived from the
    # environment instead of hard-coded ``True``.  Operators opt in
    # explicitly via ``ADOPTIQ_PRODUCTION_READY`` (truthy) or
    # ``FLASK_ENV=production`` / ``ENVIRONMENT=production``.  The previous
    # hard-coded ``True`` made it impossible to surface "not yet ready"
    # in dev/test environments and silently bypassed downstream guards.
    is_production_ready = (
        str(os.environ.get('ADOPTIQ_PRODUCTION_READY', '')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    ) or (
        (
            os.environ.get('FLASK_ENV')
            or os.environ.get('APP_ENV')
            or os.environ.get('ENVIRONMENT')
            or ''
        ).strip().lower()
        in {'production', 'prod'}
    )
    
    # Keeper Configuration - from environment only (no defaults for secrets)
    KEEPER_CONFIG = {
        "url": os.environ.get('KEEPER_URL', 'https://keeper.cisco.com'),
        "namespace": os.environ.get('KEEPER_NAMESPACE', 'cloudDB'),
        "role_id": os.environ.get('KEEPER_ROLE_ID') or '',
        "secret_id": os.environ.get('KEEPER_SECRET_ID') or '',
        "secret_path": os.environ.get('KEEPER_SECRET_PATH', 'secret/snowflake/prd/cx_swssbst_etl_svc/key')
    }
    
    # Snowflake Configuration - from environment only (password = direct auth; no Keeper needed when set)
    SNOWFLAKE_CONFIG = {
        "user": os.environ.get('SNOWFLAKE_USER') or '',
        "account": os.environ.get('SNOWFLAKE_ACCOUNT') or '',
        "role": os.environ.get('SNOWFLAKE_ROLE') or '',
        "warehouse": os.environ.get('SNOWFLAKE_WAREHOUSE') or '',
        "password": os.environ.get('SNOWFLAKE_PASSWORD') or '',
    }
    
    # CircuIT Configuration - from environment only
    CIRCUIT_CONFIG = {
        "client_id": os.environ.get('CIRCUIT_CLIENT_ID') or '',
        "client_secret": os.environ.get('CIRCUIT_CLIENT_SECRET') or '',
        "app_key": os.environ.get('CIRCUIT_APP_KEY') or '',
        "model_name": os.environ.get('CIRCUIT_MODEL_NAME', 'gpt-5-nano')
    }
    
    # Database Table Names
    DSM_TABLE = "CX_DB.CX_SWSSBST_BR.dsm_assignment_data"
    AB_TABLE = "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW"
    
    # Round 7 / Phase 3.17: removed the hard-coded ``TEAM_ROSTER`` and
    # ``MANAGERS`` defaults that previously embedded ~30 personal email
    # addresses (Cisco internal) directly into source control.  The
    # runtime source of truth is ``team_config.json`` loaded via
    # :func:`adoptiq_backend._load_team_config`; nothing in the app
    # actually imported ``Config.TEAM_ROSTER`` / ``Config.MANAGERS`` so
    # the only effect of these defaults was to leak PII through the
    # repo and packaged binary.  Tests should populate
    # ``team_config.json`` (or monkey-patch ``adoptiq_backend.TEAM_ROSTER``)
    # rather than relying on a baked-in roster.
    
    # Technology Choices
    TECH_CHOICES = [
        "Webex Meetings & Messaging",
        "Webex Calling",
        "Webex Contact Center",
        "Webex Contact Center Enterprise",
        "Cisco UCCE",
        "Cisco UCCX",
    ]
    
    # Technology Filters
    TECH_FILTERS = {
        "Webex Meetings & Messaging": [
            r'webex\s*meetings?',
            r'webex\s*messag(ing|e)',
            r'webex\s*app',
            r'webex\s*suite',
            r'collaboration',
            r'room\s*devices',
            r'desk\s*series',
            r'joining\s*a\s*meeting',
            r'scheduling',
            r'productivity\s*tools',
            r'recording',
            r'vidcast',
            r'video',
            r'hybrid\s*calendar',
            r'site\s*management',
            r'user\s*management',
            r'org\s*management',
            r's\s*s\s*p\s*t',
            r'c\s*v\s*i',
            r'edge\s*audio',
            r'video\s*mesh',
            r'edge\s*connect',
            r'webex\s*share',
            r'webex\s*events',
            r'socio',
            r'proactive\s*cases'
        ],
        "Webex Calling": [r'webex\s*calling', r'(dedicated\s*instance|\bdi\b)'],
        "Webex Contact Center": [
            r'(webex\s*contact\s*center|wxcc)',
            r'cloud\s*and\s*hybrid\s*products',
            r'contact\s*center\s*cloud',
            r'contact\s*center\s*hybrid'
        ],
        "Webex Contact Center Enterprise": [
            r'(webex\s*contact\s*center\s*enterprise|wxcc\s*enterprise)',
            r'contact\s*center\s*software',  # Only when NOT UCCX/UCCE
            r'enterprise\s*contact\s*center'
        ],
        "Cisco UCCE": [
            r'\bucce\b', 
            r'unified\s*contact\s*center\s*enterprise',
            r'contact\s*center\s*enterprise'
        ],
        "Cisco UCCX": [
            r'\buccx\b', 
            r'unified\s*contact\s*center\s*express',
            r'contact\s*center\s*express'
        ],
    }

    # Sub-technology normalization assists to reduce "Other/Unknown" leakage.
    SUB_TECHNOLOGY_MAPPINGS = {
        r"\bwebex\s*contact\s*center\s*enterprise\b|\bwxcc\s*enterprise\b|\bwebex\s*cce\b": "Webex Contact Center Enterprise",
        r"\bwebex\s*contact\s*center\b|\bwxcc\b": "Webex Contact Center",
        r"\bunified\s*contact\s*center\s*enterprise\b|\bucce\b": "Cisco UCCE",
        r"\bunified\s*contact\s*center\s*express\b|\buccx\b": "Cisco UCCX",
        r"\bwebex\s*calling\b|\bdedicated\s*instance\b|\bdi\b": "Webex Calling",
        r"\bwebex\s*meetings?\b|\bwebex\s*messag(ing|e)\b|\bwebex\s*app\b|\bcollaboration\b": "Webex Meetings & Messaging",
        r"\bcontact\s*center\s*software\b|\bcontact\s*center\b": "All Contact Center",
    }
    
    # Official Categories
    OFFICIAL_CATEGORIES = {
        "Cisco External": [
            "Not a Customer Priority", "Customer Considering Competitor",
            "Customer Internal Strategy Mis-Alignment", "Future Intent to Adopt",
            "Intent to Opt Out or No Value Fit (Future Follow Up)", "No Budget Funding"
        ],
        "Customer Enablement/Technical Gap": [
            "Additional Customer Training Required", "Cisco or 3rd Party Compatibility",
            "Configuration Assistance Needed", "Diagnostic or Monitoring Assistance Needed",
            "High-Level/Low-Level Design Assistance Needed", "Installation Assistance Needed",
            "Licensing or Smart Account Support Needed", "Limited/Lack of Use Case Understanding",
            "Migration/Upgrade Assistance Needed", "Product Licensing Operations Issue",
            "Professional Services Needed (Cisco AS or Partner)", "TAC Support Needed or Pending TAC Cases"
        ],
        "Customer Environment Not Ready": [
            "Additional HW/SW needed", "Customer's Infrastructure Not Ready"
        ],
        "Customer Perceives Product Not Ready": [
            "Feature to Complete Deploy", "Feature Request",
            "Pending Known Product Bug", "Product or Solution Not Fed-Ramped (or lacks certifications)"
        ],
        "Customer Needs Partner Support": [
            "Partner Experience (includes Technical and/or Resource Challenge)",
            "Partner Needs Enablement", "Partner Unresponsive to Customer"
        ],
        "Cisco Internal": [
            "Unable to Engage", "Customer is Unresponsive", "Invalid Contact", "Missing Contact"
        ],
        "Adoption On Hold": [
            "Deprioritized by Theater Leadership", "Hold Request from Account Team",
            "Internal CS Resource Limitations", "Product/Service Seeded",
            "Supply Chain Delay/Issue with HW"
        ],
        "Data/Documentation Issue": [
            "Documentation Not Found or Insufficient", "Inaccurate Eligibility",
            "Internal Use Case Exit Criteria Issue", "Internal Use Case Telemetry Issue"
        ],
    }
    
    # Help URLs
    HELP_URLS = [
        "https://help.webex.com/en-us/article/mqkve8/Webex-App-%7C-Release-notes",
        "https://help.webex.com/en-us/article/8dmbcr/What's-New-in-Webex-Suite",
        "https://help.webex.com/en-us/article/n8z6v5c/Webex-App-%7C-Known-issues",
    ]
    
    # Likely Column Names for CSOne Excel Files
    LIKELY_DATE_COLS = {"Date/Time Opened", "Created", "Created Date", "OPEN_DATE", "OPEN_DATE_C", "CREATED_DATE", "CREATED_DATE_C"}
    LIKELY_TITLE_COLS = {"Title", "TITLE", "SUBJECT", "SUBJECT_C", "NAME", "ACTION_PLAN_TITLE_C"}
    LIKELY_DESC_COLS = {"Problem Description", "DESCRIPTION", "DESCRIPTION_C", "COMMENTS", "COMMENTS__C"}
    LIKELY_OWNER_COLS = {"Owner Email", "OWNER_EMAIL", "ASSIGNEE_EMAIL", "ASSIGNEE", "ASSIGNEE_C", "OWNER"}
    LIKELY_CUST_COLS = {"Customer Name", "BU_NAME", "CUSTOMER", "ACCOUNT", "ACCOUNT_NAME"}
    LIKELY_CASE_COLS = {"SR Number", "Case Number", "CASE_NUMBER", "SR_NUMBER"}
    LIKELY_TECH_COLS = {"Product", "Technology", "PRODUCT", "PRODUCT_C", "PRODUCT_NAME_C", "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C", "SUCCESS_TRACK_C", "Sub Technology"}
    LIKELY_SUB_COLS = {"Subscription ID", "SUBSCRIPTION_ID", "SUB_ID", "Subscription Number", "Subscription Reference Id"}
