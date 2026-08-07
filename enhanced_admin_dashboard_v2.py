#!/usr/bin/env python3
"""
Enhanced AdoptIQ Administrative Dashboard v2.0
Advanced monitoring with real-time IP tracking, report analytics, and security features
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import json
import secrets
import tempfile
try:
    import psutil
except ImportError:
    print("ERROR: Admin Console requires 'psutil'. Run: pip install psutil")
    print("Or re-run setup_windows.bat / setup_mac.sh to install all dependencies.")
    sys.exit(1)
import subprocess
import threading
import time
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, Response, session, abort
from collections import defaultdict, deque
from contextlib import contextmanager
import requests
import socket
import uuid
import hashlib
import re
from typing import Any, Dict, Tuple  # Round 14 / Phase 2.2: needed by `_utc_iso_z(value: Any)`.  Round 69 / Build 43: Dict + Tuple for ``_r69_admin_proxy_post`` signature.

# Round 14 / Phase 2.2: previously `_utc_iso_z` and `_tz` lived inside
# `record_report_completion` only.  `get_report_history` and `get_analytics`
# both referenced them at module scope (computing the "last 7 days" window
# in UTC for sqlite WHERE clauses), which raised NameError every call.  The
# top-level `except Exception` swallowed the failure into a "report history
# retrieval failed" log line and an empty placeholder, so the History UI
# silently lost its 7-day tile and the analytics endpoint silently returned
# `{}`.  Promote both helpers to module scope so all callers share one
# definition.
_tz = timezone


def _utc_iso_z(value: Any) -> str:
    """Render any datetime-ish value as a UTC ISO-8601 ``Z``-suffixed string.

    This mirrors the helper that previously lived inside
    ``record_report_completion``.  Strings that already carry a Z / offset
    are returned untouched; naive strings get a ``Z`` appended; aware
    datetimes are converted via ``astimezone``; naive datetimes are treated
    as UTC.  Empty / falsy inputs return ``''``.
    """
    if not value:
        return ''
    if isinstance(value, str):
        _v = value.strip()
        if not _v:
            return ''
        if _v.endswith('Z') or '+' in _v[10:] or '-' in _v[10:]:
            return _v
        return _v + 'Z'
    try:
        if hasattr(value, 'astimezone'):
            if value.tzinfo is None:
                value = value.replace(tzinfo=_tz.utc)
            return value.astimezone(_tz.utc).isoformat().replace('+00:00', 'Z')
    except Exception:
        pass
    return str(value)

# Setup comprehensive logging (fallback to console only if log file fails)
try:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('admin_dashboard_v2.log'),
            logging.StreamHandler()
        ]
    )
except Exception:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _safe_json_load(s, default=None):
    """Safely parse JSON string; return default on invalid input."""
    if default is None:
        default = {}
    try:
        return json.loads(s) if s else default
    except (json.JSONDecodeError, TypeError):
        return default


def _r12_admin_utc_iso_z() -> str:
    """Round 12 / Phase 10.4: produce a single canonical UTC ISO-Z
    timestamp for every persisted admin field (insights, performance
    metrics, audit rows, server status).  Previously these fields
    used ``datetime.now().isoformat()`` (LOCAL TIME, no zone marker)
    while ``store_report_history`` already routes through a local
    ``_utc_iso_z`` helper -- mixing the two created the same kind
    of "completed_at < start_time" artifact Round 5 / Phase 6.3
    fixed for ``store_report_history`` itself.  Use a UTC, ``Z``-
    suffixed string so the entire admin schema shares one clock.
    """
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


# Main app URL (for container: set ADOPTIQ_MAIN_URL=http://adoptiq-main:5151)
# Round 17.3: default port moved from 5001 -> 5151 (Van Halen-adjacent,
# out of macOS AirPlay Receiver / Flask-default conflict zone).
# Round 37 / Phase 1: this module-level constant is captured at import
# time, but the parent process (``app_simple._start_admin_server_in_thread``)
# now writes ``ADOPTIQ_MAIN_URL=http://127.0.0.1:<live-main-port>`` BEFORE
# importing this module, so the captured value is correct on the .app
# launch path.  ``_main_app_host_port`` re-reads the env on every call as
# defense in depth so a later rebind (or a test that monkeypatches the
# env) is picked up without a module reload.
MAIN_APP_URL = os.environ.get('ADOPTIQ_MAIN_URL', 'http://localhost:5151')

# Round 17.3: env-overridable admin port (default 5152, was 5002).  See
# ``_resolve_admin_port`` for parsing rules.
_DEFAULT_ADMIN_PORT = 5152


def _resolve_admin_port(env=None):
    """Return the admin-app TCP port from ``ADOPTIQ_ADMIN_PORT`` or default.

    Out-of-range or non-integer values fall back to the default; we use
    a print fallback rather than a logger here because this module is
    imported into the main app and we want the warning visible even if
    logging hasn't been initialised yet.
    """
    env_map = os.environ if env is None else env
    raw = (env_map.get('ADOPTIQ_ADMIN_PORT') or '').strip()
    if not raw:
        return _DEFAULT_ADMIN_PORT
    try:
        candidate = int(raw)
    except (TypeError, ValueError):
        print(
            f"[admin] ADOPTIQ_ADMIN_PORT={raw!r} is not an integer; "
            f"falling back to {_DEFAULT_ADMIN_PORT}"
        )
        return _DEFAULT_ADMIN_PORT
    if not (1 <= candidate <= 65535):
        print(
            f"[admin] ADOPTIQ_ADMIN_PORT={candidate} is outside the "
            f"1-65535 TCP range; falling back to {_DEFAULT_ADMIN_PORT}"
        )
        return _DEFAULT_ADMIN_PORT
    return candidate


def _main_app_host_port():
    """Parse the live ``ADOPTIQ_MAIN_URL`` into (host, port) for the socket check.

    Round 37 / Phase 1: re-read the env per call instead of trusting the
    module-level ``MAIN_APP_URL`` constant captured at import time.  The
    constant is fine on the canonical .app boot path because
    ``app_simple._start_admin_server_in_thread`` writes the env BEFORE
    importing this module, but environments that import the module first
    (e.g. tests, dev shells) and then set the env would otherwise see a
    stale value forever.  Cheap and removes a foot-gun.
    """
    try:
        from urllib.parse import urlparse
        # Round 148: when the env var is absent, use the documented default
        # rather than the import-time MAIN_APP_URL snapshot (pytest / dev
        # shells may import this module before ADOPTIQ_MAIN_URL is pinned).
        live_url = os.environ.get('ADOPTIQ_MAIN_URL') or 'http://localhost:5151'
        p = urlparse(live_url)
        host = p.hostname or '127.0.0.1'
        # Round 17.3: fallback bumped from 5000 -> 5151 to match the new
        # main-app default; only exercised when ``ADOPTIQ_MAIN_URL`` is
        # set to a hostname-only URL (rare).
        port = p.port if p.port is not None else 5151
        return host, port
    except Exception:
        return '127.0.0.1', 5151


def _live_main_url() -> str:
    """Return the live main-app base URL, re-reading ``ADOPTIQ_MAIN_URL`` per call.

    Round 44 / Phase 8: the dashboard's running-reports + verbose-debug
    HTTP fetches USED to use the module-level ``MAIN_APP_URL`` constant
    captured at import time (line ~125).  Round 37 / Phase 1 fixed the
    socket-probe path (``_main_app_host_port`` re-reads the env per
    call), but the two ``requests.get(f'{MAIN_APP_URL.rstrip("/")}...')``
    sites still trusted the stale constant.  ``app_simple.py`` eagerly
    imports this module from L157-161 to pull the audit helpers BEFORE
    L210 sets ``os.environ["ADOPTIQ_MAIN_URL"]`` -- so the captured
    constant stamps the unset default into ``MAIN_APP_URL``.  Audited
    Build-20 dashboards rendered "n/a -- main app unreachable" in red
    even when the main app was up and reachable, because the fetch was
    going to the wrong port.  Mirroring ``_main_app_host_port``'s
    re-read pattern here makes the env-pin defense-in-depth cover HTTP
    fetches too.  Returns the URL with no trailing slash so callers can
    safely concatenate ``f"{_live_main_url()}/api/..."``.
    """
    try:
        # Round 148: same unset-env contract as _main_app_host_port — never
        # fall back to the import-time MAIN_APP_URL constant.
        live = os.environ.get('ADOPTIQ_MAIN_URL') or 'http://localhost:5151'
    except Exception:
        live = 'http://localhost:5151'
    return (live or 'http://localhost:5151').rstrip('/')

# Create Flask app for enhanced admin dashboard
admin_app = Flask(__name__)
_admin_secret = os.environ.get('ADOPTIQ_ADMIN_SECRET_KEY')
if _admin_secret:
    admin_app.secret_key = _admin_secret
elif getattr(sys, 'frozen', False):
    raise RuntimeError("ADOPTIQ_ADMIN_SECRET_KEY must be set for packaged builds.")
else:
    admin_app.secret_key = secrets.token_urlsafe(48)

# Round 5 / Phase 2.3: per-session CSRF token issued from the Flask session
# and required by destructive admin endpoints (start_server / stop_server /
# clear_logs / export_logs).  Previously these were GET routes with no CSRF
# protection, so any cross-origin link or img/iframe loaded by an
# authenticated admin could trigger them.  We now require POST with a
# matching ``X-AdoptIQ-Admin-CSRF`` header *or* form field.
def _admin_csrf_token() -> str:
    """Return (and lazily mint) the per-session admin CSRF token."""
    tok = session.get('_admin_csrf')
    if not tok:
        tok = secrets.token_urlsafe(32)
        session['_admin_csrf'] = tok
    return tok


def _require_admin_csrf() -> None:
    """Abort with HTTP 403 if the current request lacks a valid CSRF token.

    Called explicitly from POST-only destructive routes; we deliberately do
    not register an ``@before_request`` hook so read-only routes (and the
    AJAX endpoints already guarded elsewhere) keep their existing
    behaviour.
    """
    expected = session.get('_admin_csrf')
    # Round 6 / Phase 6.13: do NOT accept the CSRF token from
    # ``request.args`` (URL query string).  Query-string tokens leak
    # through ``Referer`` headers, browser history, OS process lists,
    # and webserver access logs, which makes them functionally
    # equivalent to no protection at all for destructive POSTs.
    # Restrict to the header (preferred) and the hidden form field
    # (POST-body) sources.
    provided = (
        request.headers.get('X-AdoptIQ-Admin-CSRF')
        or request.form.get('_admin_csrf')
    )
    if not expected or not provided or not secrets.compare_digest(str(expected), str(provided)):
        log_error('SECURITY', 'Admin CSRF check failed', '_require_admin_csrf')
        abort(403)


@admin_app.context_processor
def _inject_admin_csrf():
    return {'admin_csrf_token': _admin_csrf_token()}

# Global variables for comprehensive monitoring
server_process = None
# Round 37 / Phase 1: initial ``port`` flipped from 5000 -> None so the
# Server Status tile renders "N/A" before the first probe instead of
# falsely advertising a port the main app never bound to.  ``host`` and
# ``port`` are populated by the first ``get_server_status()`` call from
# ``_main_app_host_port()`` which now reads the live env.
server_status = {
    'running': False,
    'pid': None,
    'start_time': None,
    'host': None,
    'port': None,
    'last_check': None,
}

# Enhanced monitoring data
monitoring_data = {
    'active_reports': {},
    'completed_reports': deque(maxlen=200),  # Increased capacity
    'ip_connections': defaultdict(list),
    'system_metrics': deque(maxlen=200),
    'error_logs': deque(maxlen=200),
    'access_logs': deque(maxlen=200),
    'security_events': deque(maxlen=100),
    'performance_metrics': deque(maxlen=100)
}
monitoring_data_lock = threading.RLock()

# Database for persistent storage - use platform-appropriate app data dir for frozen builds
def _get_db_path():
    if getattr(sys, 'frozen', False):
        if sys.platform == 'darwin':
            base = Path.home() / 'Library' / 'Application Support' / 'AdoptIQ'
        elif sys.platform == 'win32':
            base = Path(os.environ.get('APPDATA', str(Path.home()))) / 'AdoptIQ'
        else:
            base = Path.home() / '.adoptiq'
        base.mkdir(parents=True, exist_ok=True)
        return str(base / 'admin_monitoring_v2.db')
    return 'admin_monitoring_v2.db'

DB_PATH = _get_db_path()
_db_initialized_for_path = None
_db_init_lock = threading.Lock()


@contextmanager
def db_connection():
    """Open SQLite connection and always close it."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute('PRAGMA busy_timeout=10000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_database():
    """Initialize SQLite database for persistent monitoring data"""
    global _db_initialized_for_path
    current_db_path = DB_PATH
    if _db_initialized_for_path == current_db_path:
        return
    with _db_init_lock:
        current_db_path = DB_PATH
        if _db_initialized_for_path == current_db_path:
            return
        with db_connection() as conn:
            cursor = conn.cursor()

            # Report history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS report_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    report_type TEXT,
                    manager TEXT,
                    technology TEXT,
                    customer_name TEXT,
                    status TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    error_message TEXT,
                    days INTEGER,
                    word_path TEXT,
                    excel_path TEXT,
                    word_hash TEXT,
                    excel_hash TEXT,
                    partial_data_warnings_json TEXT,
                    scope_type TEXT,
                    scope_value TEXT,
                    scope_member TEXT,
                    data_as_of_utc TEXT,
                    fact_fingerprint TEXT,
                    created_at TEXT
                )
            ''')
            # Round 3 / Phase 5.4: ALTER existing rows so older
            # databases pick up the new audit columns without
            # losing prior history. SQLite has no ``IF NOT EXISTS``
            # for ADD COLUMN, so introspect first.
            try:
                cursor.execute("PRAGMA table_info(report_history)")
                _existing_cols = {row[1] for row in cursor.fetchall()}
                _new_cols = [
                    ("days", "INTEGER"),
                    ("word_path", "TEXT"),
                    ("excel_path", "TEXT"),
                    ("word_hash", "TEXT"),
                    ("excel_hash", "TEXT"),
                    ("partial_data_warnings_json", "TEXT"),
                    ("scope_type", "TEXT"),
                    ("scope_value", "TEXT"),
                    ("scope_member", "TEXT"),
                    ("data_as_of_utc", "TEXT"),
                    ("fact_fingerprint", "TEXT"),
                ]
                for _col, _type in _new_cols:
                    if _col not in _existing_cols:
                        try:
                            cursor.execute(
                                f"ALTER TABLE report_history ADD COLUMN {_col} {_type}"
                            )
                        except Exception as _alter_err:
                            logger.debug(
                                "ALTER report_history ADD %s skipped: %s",
                                _col, _alter_err,
                            )
            except Exception as _migrate_err:
                logger.debug(
                    "report_history schema migration skipped: %s", _migrate_err
                )

            # Round 5 / Phase 4.12: every status / progress request
            # (including the rehydration path in
            # ``_build_status_from_report_history``) issues
            # ``WHERE request_id = ? ORDER BY created_at DESC LIMIT 1``
            # against this table.  Without an index that becomes a
            # full table scan that grows linearly with audit
            # retention, which is exactly the wrong shape for a
            # frequently-polled status endpoint.  Add an index on
            # ``request_id`` (and a covering one on
            # ``(request_id, created_at)`` for the LIMIT 1 newest
            # tiebreak); both are no-ops on existing DBs that already
            # have them.
            try:
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_report_history_request_id "
                    "ON report_history(request_id)"
                )
            except Exception as _idx_err:
                logger.debug(
                    "CREATE INDEX idx_report_history_request_id skipped: %s",
                    _idx_err,
                )
            try:
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_report_history_request_created "
                    "ON report_history(request_id, created_at DESC)"
                )
            except Exception as _idx_err:
                logger.debug(
                    "CREATE INDEX idx_report_history_request_created skipped: %s",
                    _idx_err,
                )

            # Audit results table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT,
                    audit_timestamp TEXT,
                    status TEXT,
                    score INTEGER,
                    max_score INTEGER,
                    checks_json TEXT,
                    created_at TEXT
                )
            ''')

            # Enhanced IP connections table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ip_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT UNIQUE,
                    first_seen TEXT,
                    last_seen TEXT,
                    request_count INTEGER,
                    user_agent TEXT,
                    country TEXT,
                    city TEXT,
                    isp TEXT,
                    risk_level TEXT,
                    created_at TEXT
                )
            ''')

            # Security events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    endpoint TEXT,
                    severity TEXT,
                    description TEXT,
                    created_at TEXT
                )
            ''')

            # Performance metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    cpu_usage REAL,
                    memory_usage REAL,
                    disk_usage REAL,
                    response_time REAL,
                    active_connections INTEGER,
                    created_at TEXT
                )
            ''')

            # Error logs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    level TEXT,
                    message TEXT,
                    source TEXT,
                    ip_address TEXT,
                    created_at TEXT
                )
            ''')

            # Report insights table — stores per-run summaries for "learn from past analyses"
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS report_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    report_type TEXT,
                    manager TEXT,
                    technology TEXT,
                    customer_name TEXT,
                    insights_json TEXT,
                    created_at TEXT
                )
            ''')
        _db_initialized_for_path = current_db_path
        return

def record_report_completion(request_id: str, report_type: str, manager: str, technology: str,
                             customer_name: str, status: str, start_time: str, end_time: str,
                             ip_address: str = '', user_agent: str = '', error_message: str = '',
                             days: int = None, word_path: str = '', excel_path: str = '',
                             partial_data_warnings: list = None,
                             scope_type: str = '', scope_value: str = '',
                             scope_member: str = '', data_as_of_utc: str = '',
                             fact_fingerprint: str = ''):
    """Record a completed report for audit/history. Call from app_simple when report finishes.

    Round 3 / Phase 5.4: persist the audit columns the History page
    actually needs to render an honest "what did this run produce?"
    row — analysis horizon (``days``), generated artifact paths,
    SHA-256 hashes for tamper detection, and the partial-data
    warnings list so caveats survive past the in-memory status dict.
    """
    try:
        import hashlib as _hashlib
        import os as _os
        import json as _json
        from datetime import timezone as _tz

        # Round 5 / Phase 6.3: previously stored ``datetime.now().isoformat()``
        # (LOCAL TIME, no zone marker) which was indistinguishable from
        # the server's local clock.  When the report_history rows were
        # later compared against UI timestamps (which are UTC w/ a 'Z'
        # suffix), the two streams disagreed by the local-UTC offset
        # and "completed_at" appeared to occur before "start_time".
        # Force a UTC, ``Z``-suffixed ISO-8601 string so every audit
        # row uses a single, unambiguous clock.
        def _utc_iso_z(value: Any) -> str:
            if not value:
                return ''
            if isinstance(value, str):
                # If the caller already supplied a Z / offset string,
                # keep it. Otherwise, treat naive strings as UTC.
                _v = value.strip()
                if not _v:
                    return ''
                if _v.endswith('Z') or '+' in _v[10:] or '-' in _v[10:]:
                    return _v
                return _v + 'Z'
            try:
                if hasattr(value, 'astimezone'):
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=_tz.utc)
                    return value.astimezone(_tz.utc).isoformat().replace('+00:00', 'Z')
            except Exception:
                pass
            return str(value)

        def _hash_artifact(path: str) -> str:
            if not path:
                return ''
            try:
                if not _os.path.exists(path):
                    return ''
                _h = _hashlib.sha256()
                with open(path, 'rb') as _fh:
                    for _chunk in iter(lambda: _fh.read(65536), b''):
                        _h.update(_chunk)
                return _h.hexdigest()
            except Exception as _hash_err:
                logger.debug(
                    "Could not hash artifact %s: %s", path, _hash_err
                )
                return ''

        word_hash = _hash_artifact(word_path)
        excel_hash = _hash_artifact(excel_path)
        try:
            partial_warnings_json = (
                _json.dumps(partial_data_warnings)
                if partial_data_warnings else ''
            )
        except Exception:
            partial_warnings_json = ''

        # Round 146: retain the server-authorized report scope independently
        # from the human-readable customer label.  These values rehydrate
        # report-bound Ask AI after status.json eviction or an app restart.
        # Fail closed on unknown scope types instead of persisting a value
        # that a later authorization check could misinterpret.
        normalized_scope_type = str(scope_type or '').strip().casefold()
        if normalized_scope_type not in {'team', 'member', 'customer', 'subscription'}:
            normalized_scope_type = ''
        normalized_scope_value = str(scope_value or '').strip()[:500]
        normalized_scope_member = str(scope_member or '').strip().casefold()[:320]
        if normalized_scope_type == 'team':
            normalized_scope_value = ''
            normalized_scope_member = ''
        elif normalized_scope_type == 'member':
            normalized_scope_value = normalized_scope_value.casefold()
            normalized_scope_member = normalized_scope_member or normalized_scope_value
        normalized_data_as_of_utc = _utc_iso_z(data_as_of_utc)
        normalized_fact_fingerprint = str(fact_fingerprint or '').strip()[:256]

        init_database()
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO report_history
                (request_id, report_type, manager, technology, customer_name, status,
                 start_time, end_time, ip_address, user_agent, error_message,
                 days, word_path, excel_path, word_hash, excel_hash,
                 partial_data_warnings_json, scope_type, scope_value,
                 scope_member, data_as_of_utc, fact_fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (request_id, report_type, manager, technology, customer_name, status,
                  _utc_iso_z(start_time), _utc_iso_z(end_time),
                  ip_address or '', user_agent or '', error_message or '',
                  int(days) if days is not None else None,
                  word_path or '', excel_path or '', word_hash, excel_hash,
                  partial_warnings_json, normalized_scope_type,
                  normalized_scope_value, normalized_scope_member,
                  normalized_data_as_of_utc, normalized_fact_fingerprint,
                  _utc_iso_z(datetime.now(_tz.utc))))

        # Round 5 / Phase 6.16: append-only JSONL audit mirror.
        #
        # The SQLite ``report_history`` table can be wiped by the
        # ``/clear_logs`` admin endpoint (and is otherwise mutable via
        # any sqlite client), so it is *not* a forensic-quality audit
        # trail.  Mirror every successful insert into a JSONL file in
        # the per-user Application Support directory with mode 0640
        # and ``O_APPEND`` semantics, so the audit history survives
        # a UI-driven wipe and any tampering is at least detectable.
        # The mirror failing must not roll back the SQLite insert,
        # hence the broad ``try/except``.
        try:
            _audit_path = _os.environ.get('ADOPTIQ_AUDIT_MIRROR_PATH', '').strip()
            if not _audit_path:
                try:
                    _audit_dir = _os.path.join(_os.path.expanduser('~'), '.adoptiq')
                    _os.makedirs(_audit_dir, exist_ok=True)
                    _audit_path = _os.path.join(_audit_dir, 'report_history.audit.jsonl')
                except Exception:
                    _audit_path = ''
            if _audit_path:
                # Round 6 / Phase 6.6: cap ``partial_data_warnings``
                # before mirroring.  Without this, a report with
                # hundreds of "warning: missing source X" entries can
                # produce a single JSONL line that exceeds the POSIX
                # ``PIPE_BUF`` atomicity guarantee for ``O_APPEND``
                # writes (~4096 bytes on macOS / Linux), at which
                # point concurrent writers can interleave bytes and
                # corrupt the audit stream.  Cap both the count and
                # per-warning length so a single record stays under
                # ~16 KiB.
                _MAX_WARN_COUNT = 25
                _MAX_WARN_LEN = 240
                _warns_in = partial_data_warnings or []
                _warns_capped = []
                if isinstance(_warns_in, list):
                    for _w in _warns_in[:_MAX_WARN_COUNT]:
                        try:
                            _w_str = str(_w)
                        except Exception:
                            _w_str = "<unserializable warning>"
                        if len(_w_str) > _MAX_WARN_LEN:
                            _w_str = _w_str[:_MAX_WARN_LEN] + "...[truncated]"
                        _warns_capped.append(_w_str)
                    if len(_warns_in) > _MAX_WARN_COUNT:
                        _warns_capped.append(
                            f"...[+{len(_warns_in) - _MAX_WARN_COUNT} additional warnings truncated]"
                        )
                _record = {
                    'request_id': request_id,
                    'report_type': report_type,
                    'manager': manager,
                    'technology': technology,
                    'customer_name': customer_name,
                    'status': status,
                    'start_time': _utc_iso_z(start_time),
                    'end_time': _utc_iso_z(end_time),
                    'days': (int(days) if days is not None else None),
                    'word_path': word_path or '',
                    'excel_path': excel_path or '',
                    'word_hash': word_hash,
                    'excel_hash': excel_hash,
                    'partial_data_warnings': _warns_capped,
                    'scope_type': normalized_scope_type,
                    'scope_value': normalized_scope_value,
                    'scope_member': normalized_scope_member,
                    'data_as_of_utc': normalized_data_as_of_utc,
                    'fact_fingerprint': normalized_fact_fingerprint,
                    'created_at': _utc_iso_z(datetime.now(_tz.utc)),
                    'error_message': (error_message or '')[:512],
                }
                # Round 6 / Phase 6.6: enforce a per-record byte cap
                # so one giant record cannot dominate the mirror or
                # break atomic-append semantics.  The byte cap is the
                # absolute upper bound; the warnings cap above keeps
                # the *typical* record well under 4 KiB.
                _MAX_RECORD_BYTES = 16 * 1024

                # Round 6 / Phase 7.3: ``json.dumps(..., default=str)``
                # silently coerces non-serializable values (custom
                # objects, exceptions, pandas Timestamps) into their
                # ``__str__`` form.  That can leak module paths,
                # ``repr``-style memory addresses, or whole exception
                # tracebacks into the audit mirror -- all of which are
                # both noisy and a privacy risk.  Wrap in a strict
                # default that records a redacted placeholder and
                # logs the offending type so we get an early-warning
                # signal without crashing the mirror writer.
                def _strict_default(_obj):
                    _type_name = getattr(type(_obj), '__name__', 'unknown')
                    log_error(
                        'AUDIT_MIRROR',
                        f'Non-serializable audit field: type={_type_name}',
                        '_record_to_audit_jsonl',
                    )
                    return f"<unserializable:{_type_name}>"

                _line_bytes = (_json.dumps(_record, default=_strict_default) + '\n').encode('utf-8')
                if len(_line_bytes) > _MAX_RECORD_BYTES:
                    _record_min = {
                        'request_id': request_id,
                        'report_type': report_type,
                        'status': status,
                        'created_at': _utc_iso_z(datetime.now(_tz.utc)),
                        '_truncated': True,
                        '_original_bytes': len(_line_bytes),
                    }
                    _line_bytes = (_json.dumps(_record_min, default=_strict_default) + '\n').encode('utf-8')

                # Round 6 / Phase 6.6: rotate the audit JSONL if it
                # has grown past ``ADOPTIQ_AUDIT_MIRROR_MAX_BYTES``
                # (default 50 MiB).  We keep up to ``MAX_BACKUPS``
                # rotated copies named ``...audit.jsonl.1`` etc.
                # Rotation is best-effort; if it fails we still
                # append to the live file rather than dropping the
                # record.
                try:
                    _MAX_BYTES = int(_os.environ.get('ADOPTIQ_AUDIT_MIRROR_MAX_BYTES', '52428800'))
                    _MAX_BACKUPS = int(_os.environ.get('ADOPTIQ_AUDIT_MIRROR_MAX_BACKUPS', '5'))
                except (TypeError, ValueError):
                    _MAX_BYTES = 50 * 1024 * 1024
                    _MAX_BACKUPS = 5
                try:
                    _cur_size = _os.path.getsize(_audit_path) if _os.path.exists(_audit_path) else 0
                    if _cur_size + len(_line_bytes) > _MAX_BYTES:
                        for _i in range(_MAX_BACKUPS - 1, 0, -1):
                            _src = f"{_audit_path}.{_i}"
                            _dst = f"{_audit_path}.{_i + 1}"
                            if _os.path.exists(_src):
                                try:
                                    _os.replace(_src, _dst)
                                except Exception:
                                    pass
                        try:
                            _os.replace(_audit_path, f"{_audit_path}.1")
                        except Exception:
                            pass
                except Exception as _rot_err:
                    logger.debug("audit JSONL rotation skipped: %s", _rot_err)

                # Open with O_APPEND so concurrent writers cannot
                # truncate each other; force owner-only perms on
                # creation.
                # Round 6 / Phase 6.11: tighten the create mode from
                # 0o640 (group-readable) to 0o600 to match the
                # documented intent above.  The audit mirror can
                # carry analysis IDs, customer names, and report
                # paths; group-read is too permissive on shared
                # macOS / Linux hosts where multiple Cisco accounts
                # may be in the staff group.  Also re-chmod after
                # open in case the file already existed with a more
                # permissive mode from a prior build.
                _flags = _os.O_WRONLY | _os.O_APPEND | _os.O_CREAT
                _fd = _os.open(_audit_path, _flags, 0o600)
                try:
                    _os.write(_fd, _line_bytes)
                finally:
                    _os.close(_fd)
                try:
                    _os.chmod(_audit_path, 0o600)
                except Exception:
                    pass
        except Exception as _audit_err:
            # Mirror failure must never poison the primary insert.
            logger.debug("audit JSONL mirror skipped: %s", _audit_err)
    except Exception as e:
        logger.warning(f"Could not record report to history: {e}")

def store_report_insights(request_id: str, report_type: str, manager: str, technology: str,
                         customer_name: str, insights_dict: dict):
    """Store a run summary for learning. insights_dict can include customer_count, barrier_count, summary_line, risk_theme, etc."""
    try:
        init_database()
        import json
        insights_json = json.dumps(insights_dict) if insights_dict else "{}"
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO report_insights
                (request_id, report_type, manager, technology, customer_name, insights_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (request_id, report_type, manager or '', technology or '', customer_name or '',
                  insights_json, _r12_admin_utc_iso_z()))
            cursor.execute('''
                DELETE FROM report_insights WHERE id NOT IN (
                    SELECT id FROM report_insights ORDER BY created_at DESC LIMIT 500
                )
            ''')
    except Exception as e:
        logger.warning(f"Could not store report insights: {e}")

def get_learned_insights(manager: str, technology: str, limit: int = 5) -> str:
    """Return a short 'learned from past analyses' string for prompt injection. Uses same manager/tech when possible."""
    try:
        import json
        init_database()
        man = manager or ''
        tech = technology or ''
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT insights_json, report_type, customer_name, created_at
                FROM report_insights
                WHERE (? = '' OR manager = ?) AND (? = '' OR technology = ?)
                ORDER BY created_at DESC
                LIMIT ?
            ''', (man, man, tech, tech, limit))
            rows = cursor.fetchall()
        if not rows:
            return ""
        bullets = []
        for row in rows:
            try:
                data = json.loads(row[0]) if row[0] else {}
                report_type = row[1] or "report"
                parts = []
                if data.get("customer_count") is not None:
                    parts.append(f"{data['customer_count']} customers")
                if data.get("barrier_count") is not None:
                    parts.append(f"{data['barrier_count']} adoption barriers")
                if data.get("summary_line"):
                    s = str(data["summary_line"])
                    parts.append(s[:80] + ("..." if len(s) > 80 else ""))
                rt = data.get("risk_theme")
                if rt is not None and str(rt).strip():
                    parts.append(f"risk theme: {str(rt).strip()}")
                if parts:
                    bullets.append(" • " + " | ".join(parts) + f" ({report_type})")
            except Exception:
                continue
        if not bullets:
            return ""
        return "Learned from past analyses for this manager/technology:\n" + "\n".join(bullets[: limit])
    except Exception as e:
        logger.debug(f"get_learned_insights: {e}")
        return ""

def get_ip_info(ip_address):
    """Get IP geolocation and ISP information (mock implementation)"""
    # In production, you would use a service like ipapi.co or similar
    # For now, return mock data
    return {
        'country': 'Unknown',
        'city': 'Unknown',
        'isp': 'Unknown',
        'risk_level': 'LOW'
    }

def assess_ip_risk(ip_address, request_count, user_agent):
    """Assess IP risk level based on behavior"""
    risk_factors = 0

    # High request count
    if request_count > 1000:
        risk_factors += 3
    elif request_count > 100:
        risk_factors += 1

    # Suspicious user agent
    suspicious_agents = ['bot', 'crawler', 'scanner', 'hack', 'test']
    if any(agent in user_agent.lower() for agent in suspicious_agents):
        risk_factors += 2

    # Internal IP (usually safe)
    if ip_address.startswith(('10.', '192.168.', '172.')):
        risk_factors -= 1

    # Determine risk level
    if risk_factors >= 3:
        return 'HIGH'
    elif risk_factors >= 1:
        return 'MEDIUM'
    else:
        return 'LOW'

def log_access(ip_address, user_agent, endpoint):
    """Log access to the application with enhanced tracking"""
    timestamp = _r12_admin_utc_iso_z()

    # Get IP information
    ip_info = get_ip_info(ip_address)

    # Add to in-memory log (thread-safe)
    with monitoring_data_lock:
        monitoring_data['access_logs'].append({
            'timestamp': timestamp,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'endpoint': endpoint,
            'country': ip_info['country'],
            'city': ip_info['city']
        })

        # Update IP tracking
        monitoring_data['ip_connections'][ip_address].append({
            'timestamp': timestamp,
            'user_agent': user_agent,
            'endpoint': endpoint
        })

        # Keep only last 100 connections per IP
        if len(monitoring_data['ip_connections'][ip_address]) > 100:
            monitoring_data['ip_connections'][ip_address] = monitoring_data['ip_connections'][ip_address][-100:]

    # Log to database
    with db_connection() as conn:
        cursor = conn.cursor()

        # Get current request count
        cursor.execute('SELECT request_count FROM ip_connections WHERE ip_address = ?', (ip_address,))
        result = cursor.fetchone()
        current_count = result[0] if result else 0
        new_count = current_count + 1

        # Assess risk level
        risk_level = assess_ip_risk(ip_address, new_count, user_agent)

        # Update or insert IP connection
        cursor.execute('''
            INSERT OR REPLACE INTO ip_connections
            (ip_address, first_seen, last_seen, request_count, user_agent, country, city, isp, risk_level, created_at)
            VALUES (?,
                    COALESCE((SELECT first_seen FROM ip_connections WHERE ip_address = ?), ?),
                    ?,
                    ?,
                    ?, ?, ?, ?, ?, ?)
        ''', (ip_address, ip_address, timestamp, timestamp, new_count, user_agent,
              ip_info['country'], ip_info['city'], ip_info['isp'], risk_level, timestamp))

    # Log security event if high risk
    if risk_level == 'HIGH':
        log_security_event('HIGH_RISK_IP', ip_address, user_agent, endpoint,
                          f'High risk IP detected: {new_count} requests')

def log_security_event(event_type, ip_address, user_agent, endpoint, description):
    """Log security events"""
    timestamp = _r12_admin_utc_iso_z()

    # Add to in-memory log (thread-safe)
    with monitoring_data_lock:
        monitoring_data['security_events'].append({
            'timestamp': timestamp,
            'event_type': event_type,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'endpoint': endpoint,
            'description': description
        })

    # Log to database
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO security_events (timestamp, event_type, ip_address, user_agent, endpoint, severity, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, event_type, ip_address, user_agent, endpoint, 'HIGH', description, timestamp))

    logger.warning(f"Security event: {event_type} from {ip_address} - {description}")

def log_error(level, message, source, ip_address=None):
    """Log error with comprehensive details"""
    timestamp = _r12_admin_utc_iso_z()

    # Add to in-memory log (thread-safe)
    with monitoring_data_lock:
        monitoring_data['error_logs'].append({
            'timestamp': timestamp,
            'level': level,
            'message': message,
            'source': source,
            'ip_address': ip_address
        })

    # Log to database
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO error_logs (timestamp, level, message, source, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (timestamp, level, message, source, ip_address, timestamp))

    # Also log to file
    logger.error(f"[{source}] {message} (IP: {ip_address})")

def get_pid_for_port(port):
    """Get PID for a process using a specific port.

    Round 9 / Phase 4.2: hardened-image / minimal-container deployments
    routinely ship without ``netstat`` or ``lsof`` available on PATH.
    Previously a missing binary surfaced as a noisy ``FileNotFoundError``
    stack trace in the diagnostic tile (and a 500 in the wrapping
    handler).  Now each branch wraps the subprocess in its own
    try/except and returns ``None`` on missing binary / non-zero exit
    so the diagnostic stays a structured "no pid" rather than a
    five-line traceback the operator can't act on.
    """
    import subprocess, sys
    try:
        if sys.platform == 'darwin' or sys.platform.startswith('linux'):
            try:
                result = subprocess.run(
                    ['lsof', '-t', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'],
                    capture_output=True, text=True, timeout=5,
                )
            except FileNotFoundError:
                logger.debug("get_pid_for_port: lsof not available on PATH")
                return None
            except subprocess.TimeoutExpired:
                logger.debug("get_pid_for_port: lsof timed out for port %s", port)
                return None
            pid_str = (result.stdout or '').strip().split('\n')[0]
            return int(pid_str) if pid_str else None
        else:
            try:
                result = subprocess.run(
                    ['netstat', '-ano'], capture_output=True, text=True, timeout=5,
                )
            except FileNotFoundError:
                logger.debug("get_pid_for_port: netstat not available on PATH")
                return None
            except subprocess.TimeoutExpired:
                logger.debug("get_pid_for_port: netstat timed out")
                return None
            for line in (result.stdout or '').split('\n'):
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        try:
                            return int(parts[-1])
                        except (TypeError, ValueError):
                            continue
            return None
    except Exception as _diag_err:  # pragma: no cover - defensive
        logger.debug("get_pid_for_port unexpected failure: %s", _diag_err)
        return None

def get_server_status():
    """Get current server status with enhanced monitoring.

    Round 2 / Phase 2.3: combine the legacy TCP socket reachability
    probe with a ``/api/diag/connectivity`` health probe so the tile
    can distinguish three real states:

    * ``port_open=True, data_path_ok=True``  → fully running
    * ``port_open=True, data_path_ok=False`` → partial (HTTP up, but
      Snowflake / Keeper data path is down).
    * ``port_open=False``                    → not reachable

    Round 3 / Phase 5.5: the previous docstring also listed "LLM" in
    the dependency set, but ``/api/diag/connectivity`` only probes
    DNS → TCP/TLS → AppRole → secret-read → Snowflake. CircuIT/LLM
    health is **not** part of the data-path tile and is reported on
    its own. Mis-labelling LLM here meant a green tile gave readers
    false confidence that the AI summary path was healthy when in
    fact only Snowflake had been verified.

    Also resolves a long-standing bug where the displayed
    ``server_status['port']`` was always the hard-coded ``5000`` from
    module init (line ~91) even when the probe targeted a different
    port (e.g. 5151 from ``ADOPTIQ_MAIN_URL``).  We now bind both the
    displayed host and port to the values actually probed.
    """
    global server_process, server_status

    try:
        # Check if main app is reachable (host/port from ADOPTIQ_MAIN_URL in container)
        import socket
        host, port = _main_app_host_port()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        port_open = (result == 0)

        # Round 2 / Phase 2.3: probe data-path health.  TCP-port-open
        # alone is not a reliable readiness signal — Flask answers
        # ``/`` while Snowflake / Keeper are unreachable.
        data_path_ok = None
        data_path_detail = None
        if port_open:
            try:
                import requests as _requests  # local import to avoid hard dep at module load
                _diag = _requests.get(
                    f"http://{host}:{port}/api/diag/connectivity",
                    timeout=2,
                )
                if _diag.status_code == 200:
                    try:
                        _payload = _diag.json() or {}
                    except Exception:
                        _payload = {}
                    # Round 3 / Phase 5.2: an empty 200 body USED to
                    # be treated as healthy, which made the admin
                    # tile claim "Server Running" any time the
                    # diagnostic endpoint silently returned ``{}``
                    # without actually exercising downstream
                    # connectivity. Require the endpoint to assert
                    # ok=True (or healthy=True) explicitly; an empty
                    # payload is now treated as "unknown" so the UI
                    # surfaces it instead of green-washing it.
                    if isinstance(_payload, dict) and (
                        'ok' in _payload or 'healthy' in _payload
                    ):
                        data_path_ok = bool(
                            _payload.get('ok', _payload.get('healthy'))
                        )
                        data_path_detail = (
                            _payload.get('detail') or _payload
                        )
                    else:
                        data_path_ok = False
                        data_path_detail = (
                            "diag endpoint returned 200 with no ok/healthy field "
                            "(refusing to call this 'healthy')"
                        )
                else:
                    data_path_ok = False
                    data_path_detail = f"HTTP {_diag.status_code}"
            except Exception as _diag_err:
                data_path_ok = False
                data_path_detail = str(_diag_err)

        # Update displayed host/port to match the probed target.  The
        # legacy global was hard-coded to 5000 at module init, so an
        # admin running on 5151 would see "Port: 5000" forever.
        server_status['host'] = host
        server_status['port'] = port
        server_status['port_open'] = port_open
        server_status['data_path_ok'] = data_path_ok
        server_status['data_path_detail'] = data_path_detail
        server_status['running'] = bool(port_open and (data_path_ok in (True, None)))
        server_status['pid'] = (
            get_pid_for_port(port) if (port_open and host in ('127.0.0.1', 'localhost')) else None
        )
        server_status['last_check'] = _r12_admin_utc_iso_z()

        return server_status

    except Exception as e:
        log_error('ERROR', f'Server status check failed: {e}', 'get_server_status')
        server_status['running'] = False
        server_status['pid'] = None
        server_status['port_open'] = False
        server_status['data_path_ok'] = False
        server_status['data_path_detail'] = str(e)
        server_status['last_check'] = _r12_admin_utc_iso_z()
        return server_status

def get_system_info():
    """Get comprehensive system information"""
    try:
        # CPU information
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()

        # Memory information
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_total = memory.total / (1024**3)  # GB
        memory_available = memory.available / (1024**3)  # GB

        # Disk information
        disk = psutil.disk_usage('/')
        disk_percent = (disk.used / disk.total) * 100
        disk_total = disk.total / (1024**3)  # GB
        disk_free = disk.free / (1024**3)  # GB

        # Network information
        network = psutil.net_io_counters()

        # Process information
        processes = len(psutil.pids())

        system_info = {
            'cpu_percent': cpu_percent,
            'cpu_count': cpu_count,
            'memory_percent': memory_percent,
            'memory_total': round(memory_total, 2),
            'memory_available': round(memory_available, 2),
            'disk_percent': disk_percent,
            'disk_total': round(disk_total, 2),
            'disk_free': round(disk_free, 2),
            'network_bytes_sent': network.bytes_sent,
            'network_bytes_recv': network.bytes_recv,
            'processes': processes,
            'timestamp': _r12_admin_utc_iso_z()
        }

        # Log performance metrics
        log_performance_metrics(cpu_percent, memory_percent, disk_percent)

        return system_info

    except Exception as e:
        # Round 2 / Phase 2.2: surface failed-vs-zero state so the admin
        # tile template can render "n/a" / a failure chip instead of
        # claiming the host has 0% CPU / 0 GB of disk when ``psutil``
        # raises.  ``state='failed'`` and ``fetch_error`` distinguish a
        # genuine zero (idle host) from "could not measure".
        log_error('ERROR', f'System info retrieval failed: {e}', 'get_system_info')
        return {
            'cpu_percent': None, 'cpu_count': None,
            'memory_percent': None, 'memory_total': None, 'memory_available': None,
            'disk_percent': None, 'disk_total': None, 'disk_free': None,
            'network_bytes_sent': None, 'network_bytes_recv': None,
            'processes': None, 'timestamp': _r12_admin_utc_iso_z(),
            'state': 'failed',
            'fetch_error': str(e),
        }

def log_performance_metrics(cpu_usage, memory_usage, disk_usage):
    """Log performance metrics to database"""
    try:
        timestamp = _r12_admin_utc_iso_z()
        active_connections = len(monitoring_data['ip_connections'])

        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO performance_metrics (timestamp, cpu_usage, memory_usage, disk_usage, response_time, active_connections, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp, cpu_usage, memory_usage, disk_usage, 0.0, active_connections, timestamp))

    except Exception as e:
        log_error('ERROR', f'Performance metrics logging failed: {e}', 'log_performance_metrics')

def get_total_count(table: str):
    """Return the true `SELECT COUNT(*)` for a monitoring table.

    KPI tiles must always reflect the full database, never the
    ``LIMIT 50`` slice rendered into the page.

    Round 2 / Phase 2.2: returns ``None`` on failure so the tile
    template can render "n/a" / a failure chip rather than the
    indistinguishable ``0`` (which would be a valid count for an empty
    table).  Callers using arithmetic should ``or 0`` if they want the
    legacy behavior, but UI code MUST treat ``None`` as "fetch failed".
    """

    # Hard allow-list to defeat any caller injection (table name comes from
    # source code, but we still refuse to interpolate arbitrary identifiers).
    allowed = {"report_history", "ip_connections", "error_logs", "security_events"}
    if table not in allowed:
        # Round 12 / Phase 10.7: previously this returned ``0`` for both
        # "table is empty" and "policy rejected the table name", so the
        # admin tile rendered a confident "0" even when ``get_total_count``
        # had refused to even run the query.  Return ``None`` (the same
        # sentinel the ``except`` branch uses) so the UI renders "n/a"
        # / failure chip and operators can distinguish a real empty
        # table from a misconfigured caller.  Also log so a misuse is
        # discoverable in ``error_logs`` without crashing the request.
        log_error(
            'WARNING',
            f'get_total_count refused disallowed table: {table!r}',
            'get_total_count',
        )
        return None
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            # Identifier is from the static allow-list; safe to interpolate.
            cursor.execute(f'SELECT COUNT(*) FROM {table}')  # nosec - allow-listed identifier
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        log_error('ERROR', f'Total count query failed for {table}: {e}', 'get_total_count')
        return None


def get_total_request_count():
    """Return the SUM of request_count across every IP, not just the LIMIT 50 page.

    Round 11 / Phase 10.2: returns ``None`` on DB failure (was ``0``)
    so the template can distinguish "we know there were zero requests"
    from "the audit database is unreachable / corrupt".  Returning a
    bare ``0`` on failure silently understated the metric and let
    monitoring tiles render a green "0 requests" badge during an
    outage.  Callers must treat ``None`` as "unknown / failure".
    """
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COALESCE(SUM(request_count), 0) FROM ip_connections')
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        log_error('ERROR', f'Total request count query failed: {e}', 'get_total_request_count')
        return None


def get_report_history(limit: int = 50):
    """Get comprehensive report history.

    Round 2 / Phase 1.13 — the returned list is intentionally limited to
    the 50 most recent rows for the table view, but ``history.html``
    needs the FULL count for its "Total Analyses" tile so the tile does
    not silently understate the audit DB.  Each row exposes
    ``_total_analyses`` (the unbounded ``COUNT(*)`` from
    ``report_history``) so the template can render
    ``{{ analyses[0]._total_analyses }}`` with a "showing 50 of N"
    disclosure when the list is capped.
    """
    try:
        try:
            row_limit = max(1, min(int(limit), 1_000))
        except (TypeError, ValueError):
            row_limit = 50
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM report_history')
            try:
                total_analyses = int(cursor.fetchone()[0] or 0)
            except (TypeError, ValueError):
                total_analyses = 0

            # Round 4 / Phase 2.4: compute the "Last 7 Reports" tile
            # and "Active Managers" tile from the FULL audit table,
            # not from the LIMIT 50 page slice.  The previous
            # template did ``analyses[:7]|length`` (always 7 once we
            # had ≥7 rows, regardless of date) and
            # ``analyses|map(...)|unique|length`` over the page
            # slice, which understated the manager count whenever
            # ``report_history`` had >50 rows spread across
            # additional managers.
            try:
                # Round 6 / Phase 6.14: ``start_time`` and
                # ``created_at`` are stored as UTC ISO-Z strings (see
                # ``_utc_iso_z`` in this module).  SQLite's
                # ``datetime('now', '-7 days')`` returns a naive
                # ``YYYY-MM-DD HH:MM:SS`` form (no ``T``, no ``Z``)
                # which lexically sorts BELOW the ISO-Z strings even
                # for the same wall-clock instant -- the old
                # comparison therefore matched MORE rows than 7 days
                # back (every row that started with the date digits
                # less than the literal "7 days ago" prefix), and
                # silently broke for any month / year boundary.
                # Compute the 7-day window in Python with
                # ``datetime.now(_tz.utc) - timedelta(days=7)`` and
                # bind it as a parameterized UTC ISO-Z string so the
                # comparison is apples-to-apples.
                from datetime import timedelta as _td_p614
                _seven_ago_iso = _utc_iso_z(datetime.now(_tz.utc) - _td_p614(days=7))
                cursor.execute(
                    "SELECT COUNT(*) FROM report_history "
                    "WHERE COALESCE(start_time, created_at) >= ?",
                    (_seven_ago_iso,),
                )
                last_7_days = int(cursor.fetchone()[0] or 0)
                last_7_days_failed = False
            except Exception as _l7d_err:
                # Round 11 / Phase 10.3: previously a DB error here
                # silently coerced the metric to 0 and history.html
                # rendered a green "0 reports / last 7 days" tile while
                # the audit DB was actually unreachable. Surface a
                # failure flag so the template can render
                # "Unavailable" instead of misleading zero.
                log_error(
                    'WARNING',
                    f'last_7_days query failed: {_l7d_err}',
                    'get_report_history',
                )
                last_7_days = 0
                last_7_days_failed = True
            # Round 12 / Phase 10.3: previously a failure here set
            # ``total_managers = 0`` with no failure flag, so the
            # admin dashboard rendered "0 managers" indistinguishably
            # from a real empty database.  Round 11 / Phase 10.3
            # already established the ``last_7_days_failed`` pattern
            # (set above on the prior except branch) -- mirror that
            # pattern for ``total_managers`` so the template can
            # render "Unavailable" rather than misleading zero.
            total_managers_failed = False
            try:
                cursor.execute(
                    "SELECT COUNT(DISTINCT manager) FROM report_history "
                    "WHERE manager IS NOT NULL AND manager <> ''"
                )
                total_managers = int(cursor.fetchone()[0] or 0)
            except Exception as _tm_err:
                log_error(
                    'WARNING',
                    f'total_managers query failed: {_tm_err}',
                    'get_report_history',
                )
                total_managers = 0
                total_managers_failed = True

            # Round 4 / Phase 5.3: include the Round 3 audit columns
            # (``days``, ``word_path`` / ``excel_path`` for direct
            # download links, ``word_hash`` / ``excel_hash`` for
            # integrity / dedup, and ``partial_data_warnings_json`` so
            # ``history.html`` and ``/progress/<id>`` fallback can show
            # the warning ribbon for past runs).  Older databases that
            # were migrated via ``ALTER TABLE`` will still have these
            # columns thanks to the migration above; if any column is
            # missing the SELECT will fail and we fall back to the
            # legacy projection so we never crash the dashboard.
            # Round 5 / Phase 6.17: ``ORDER BY created_at DESC`` alone is
            # not deterministic when two rows share the same ``created_at``
            # (very common: completion + post-completion update insert in
            # the same second).  Add ``id DESC`` as a tiebreaker so the
            # admin dashboard renders a stable, reproducible order and
            # ``LIMIT 50`` cannot drop the wrong row.
            try:
                cursor.execute('''
                    SELECT request_id, report_type, manager, technology, customer_name,
                           status, start_time, end_time, ip_address, user_agent,
                           error_message, created_at,
                           days, word_path, excel_path, word_hash, excel_hash,
                           partial_data_warnings_json, scope_type, scope_value,
                           scope_member, data_as_of_utc, fact_fingerprint
                    FROM report_history
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                ''', (row_limit,))
                _have_audit_cols = True
                _have_scope_cols = True
            except Exception:
                try:
                    # Backward compatibility for a database opened before the
                    # Round 146 ALTER migration completed: retain the older
                    # artifact audit columns even when scope columns are not
                    # present yet.
                    cursor.execute('''
                        SELECT request_id, report_type, manager, technology, customer_name,
                               status, start_time, end_time, ip_address, user_agent,
                               error_message, created_at,
                               days, word_path, excel_path, word_hash, excel_hash,
                               partial_data_warnings_json
                        FROM report_history
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                    ''', (row_limit,))
                    _have_audit_cols = True
                    _have_scope_cols = False
                except Exception:
                    cursor.execute('''
                        SELECT request_id, report_type, manager, technology, customer_name, status, start_time, end_time, ip_address, user_agent, error_message, created_at
                        FROM report_history
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                    ''', (row_limit,))
                    _have_audit_cols = False
                    _have_scope_cols = False

            reports = []
            for row in cursor.fetchall():
                _rec = {
                    'request_id': row[0],
                    'report_type': row[1],
                    'manager': row[2],
                    'technology': row[3],
                    'customer_name': row[4],
                    'status': row[5],
                    'start_time': row[6],
                    'end_time': row[7],
                    'ip_address': row[8],
                    'user_agent': row[9],
                    'error_message': row[10],
                    'created_at': row[11],
                    '_total_analyses': total_analyses,
                    '_total_last_7_days': last_7_days,
                    '_total_last_7_days_failed': last_7_days_failed,
                    '_total_managers': total_managers,
                    # Round 12 / Phase 10.3: parity with
                    # ``_total_last_7_days_failed`` so the template
                    # can render "Unavailable" instead of "0".
                    '_total_managers_failed': total_managers_failed,
                }
                if _have_audit_cols and len(row) >= 18:
                    _rec.update({
                        'days': row[12],
                        'word_path': row[13],
                        'excel_path': row[14],
                        'word_hash': row[15],
                        'excel_hash': row[16],
                        'partial_data_warnings_json': row[17],
                    })
                if _have_scope_cols and len(row) >= 23:
                    _rec.update({
                        'scope_type': row[18],
                        'scope_value': row[19],
                        'scope_member': row[20],
                        'data_as_of_utc': row[21],
                        'fact_fingerprint': row[22],
                    })
                reports.append(_rec)
            if not reports:
                reports.append({
                    '_placeholder': True,
                    '_total_analyses': total_analyses,
                    '_total_last_7_days': last_7_days,
                    '_total_last_7_days_failed': last_7_days_failed,
                    '_total_managers': total_managers,
                    # Round 12 / Phase 10.3: parity with
                    # ``_total_last_7_days_failed`` so the placeholder
                    # row carries the failure flag too.
                    '_total_managers_failed': total_managers_failed,
                })
        return reports

    except Exception as e:
        log_error('ERROR', f'Report history retrieval failed: {e}', 'get_report_history')
        return []

def get_ip_connections():
    """Get enhanced IP connection statistics"""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()

            # Get IP connection stats
            # Round 13 / Phase 7.2: previously the ORDER BY only
            # used ``last_seen DESC`` which is non-deterministic when
            # the LIMIT clips a tail of rows that share the exact
            # same second-precision ``last_seen`` (the column is a
            # text ISO-8601 timestamp; bulk-import / replay test
            # fixtures routinely write hundreds of rows in the same
            # second).  Add ``id DESC`` as a secondary key so the
            # LIMIT 50 page always selects the same 50 rows for a
            # given snapshot and the admin dashboard does not
            # flicker between two equally valid orderings.
            cursor.execute('''
                SELECT ip_address, first_seen, last_seen, request_count, user_agent, country, city, risk_level
                FROM ip_connections
                ORDER BY last_seen DESC, id DESC
                LIMIT 50
            ''')

            connections = []
            for row in cursor.fetchall():
                connections.append({
                    'ip_address': row[0],
                    'first_seen': row[1],
                    'last_seen': row[2],
                    'request_count': row[3] if row[3] is not None else 0,
                    'user_agent': row[4],
                    'country': row[5],
                    'city': row[6],
                    'risk_level': row[7]
                })
        return connections

    except Exception as e:
        log_error('ERROR', f'IP connections retrieval failed: {e}', 'get_ip_connections')
        return []

def get_security_events():
    """Get recent security events"""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()

            # Round 13 / Phase 7.3: same ``id DESC`` tie-break as the
            # ip_connections page above; ``timestamp`` alone is not a
            # stable sort key when bulk-import or replay events share
            # the same second.
            cursor.execute('''
                SELECT timestamp, event_type, ip_address, user_agent, endpoint, severity, description
                FROM security_events
                ORDER BY timestamp DESC, id DESC
                LIMIT 50
            ''')

            events = []
            for row in cursor.fetchall():
                events.append({
                    'timestamp': row[0],
                    'event_type': row[1],
                    'ip_address': row[2],
                    'user_agent': row[3],
                    'endpoint': row[4],
                    'severity': row[5],
                    'description': row[6]
                })
        return events

    except Exception as e:
        log_error('ERROR', f'Security events retrieval failed: {e}', 'get_security_events')
        return []

def get_error_logs():
    """Get recent error logs"""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()

            # Round 13 / Phase 7.4: same ``id DESC`` tie-break as the
            # security_events / ip_connections pages above.
            cursor.execute('''
                SELECT timestamp, level, message, source, ip_address
                FROM error_logs
                ORDER BY timestamp DESC, id DESC
                LIMIT 50
            ''')

            errors = []
            for row in cursor.fetchall():
                errors.append({
                    'timestamp': row[0],
                    'level': row[1],
                    'message': row[2],
                    'source': row[3],
                    'ip_address': row[4]
                })
        return errors

    except Exception as e:
        log_error('ERROR', f'Error logs retrieval failed: {e}', 'get_error_logs')
        return []

def get_analytics():
    """Get comprehensive analytics data"""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()

            # Report type distribution
            cursor.execute('''
                SELECT report_type, COUNT(*) as count
                FROM report_history
                GROUP BY report_type
                ORDER BY count DESC
            ''')
            report_types = dict(cursor.fetchall())

            # Manager distribution
            cursor.execute('''
                SELECT manager, COUNT(*) as count
                FROM report_history
                GROUP BY manager
                ORDER BY count DESC
            ''')
            managers = dict(cursor.fetchall())

            # IP risk distribution
            cursor.execute('''
                SELECT risk_level, COUNT(*) as count
                FROM ip_connections
                GROUP BY risk_level
            ''')
            risk_levels = dict(cursor.fetchall())

            # Daily report count (last 7 days)
            cursor.execute('''
                SELECT DATE(created_at) as date, COUNT(*) as count
                FROM report_history
                WHERE created_at >= ?
                GROUP BY DATE(created_at)
                ORDER BY date DESC
            ''', (_utc_iso_z(datetime.now(_tz.utc) - timedelta(days=7)),))
            daily_reports = dict(cursor.fetchall())

        return {
            'report_types': report_types,
            'managers': managers,
            'risk_levels': risk_levels,
            'daily_reports': daily_reports
        }

    except Exception as e:
        log_error('ERROR', f'Analytics retrieval failed: {e}', 'get_analytics')
        return {}

# Enhanced HTML template
ENHANCED_ADMIN_TEMPLATE_V2 = """
<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>AdoptIQ Admin Dashboard v2.0 - Enhanced with Audit System</title>
    {# Round 17.4 / Phase 6.6: early-paint theme bootstrap.  Same
       contract as the main app: read ``adoptiq-theme`` from
       localStorage BEFORE first paint and apply it to <html>, so
       a returning user never sees a flash of the wrong theme.
       Constrained to "dark"|"light" so a corrupt value cannot
       inject arbitrary attribute text. #}
    <script>
        (function () {
            try {
                var stored = null;
                try { stored = window.localStorage.getItem('adoptiq-theme'); } catch (_) { /* private mode */ }
                var theme = (stored === 'light' || stored === 'dark') ? stored : 'dark';
                document.documentElement.setAttribute('data-bs-theme', theme);
            } catch (_) {
                document.documentElement.setAttribute('data-bs-theme', 'dark');
            }
        })();
    </script>
    <style>
        /*
         * Round 17.4 / Phase 6.6: shared theme tokens for the admin
         * console.  Mirrors the semantic-token layer in
         * ``templates/base.html`` so the toggle in the main app and
         * the admin dashboard agree on what "dark" and "light" mean.
         * The historical Cisco-blue look is the light default; the
         * ``[data-bs-theme="dark"]`` block below pivots the chrome
         * to charcoal + orange.  ``--risk-*`` tokens are NOT defined
         * here -- the risk-band hexes for ``.risk-high`` /
         * ``.risk-medium`` / ``.risk-low`` continue to come from
         * ``canonical_metrics.RISK_BAND_COLORS`` via Jinja so the
         * admin UI stays byte-identical with Word/Excel exports.
         *
         * Round 24 / theme-parity: light-mode tokens were drifting
         * from ``templates/base.html`` (the admin used ``#0076CE``
         * page bg + ``#3498db`` Tableau-blue accent, the main app
         * used ``#f8f9fa`` page bg + ``#00bceb`` Cisco-blue accent).
         * The values below now resolve to the same hexes as
         * ``base.html``'s ``:root`` defaults, so toggling between
         * ports 5151 and 5152 in light mode no longer reveals two
         * different palettes.  ``--bg-page`` was removed because it
         * was declared in both blocks but never referenced anywhere
         * in the inline stylesheet (consolidated with ``--bg-base``).
         * ``tests/test_round24_admin_theme_parity.py`` pins the
         * alignment so a future drift breaks CI.
         */
        :root {
            --bg-base: #f8f9fa;                                   /* Round 24 / theme-parity */
            --bg-surface: #ffffff;                                /* Round 24 / theme-parity */
            --bg-surface-raised: #ffffff;                         /* Round 24 / theme-parity */
            --border-subtle: #e9ecef;                             /* Round 24 / theme-parity */
            --text-primary: #212529;                              /* Round 24 / theme-parity */
            --text-secondary: #495057;
            --text-muted: #6c757d;                                /* Round 24 / theme-parity */
            --accent-primary: #00bceb;                            /* Round 24 / theme-parity */
            --accent-primary-hover: #0073e6;                      /* Round 24 / theme-parity */
            /* Round 152 / E9: raised from 0.35 in lockstep with
               templates/base.html.  This token is the focus ring that
               replaces ``outline: none``; at 0.35 alpha it fell below the
               3:1 non-text contrast WCAG 1.4.11 requires.  Round 24 pins
               these two declarations to agree, so they move together. */
            --accent-glow: rgba(0, 188, 235, 0.75);               /* Round 24 / theme-parity */
            --accent-glow-soft: rgba(0, 188, 235, 0.15);          /* Round 24 / theme-parity */
            --th-bg: linear-gradient(135deg, #00bceb, #0073e6);   /* Round 24 / theme-parity */
            --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.1);
            --adoptiq-orange: #ff7a1a;
            --adoptiq-orange-hover: #ff944d;
        }

        [data-bs-theme="dark"] {
            --bg-base: #0d1117;
            --bg-surface: #161b22;
            --bg-surface-raised: #21262d;
            --border-subtle: #30363d;
            --text-primary: #f0f6fc;
            --text-secondary: #c9d1d9;
            --text-muted: #8b949e;
            --accent-primary: var(--adoptiq-orange);
            --accent-primary-hover: var(--adoptiq-orange-hover);
            --accent-glow: rgba(255, 122, 26, 0.35);
            --accent-glow-soft: rgba(255, 122, 26, 0.12);
            --th-bg: linear-gradient(135deg, #1f242c, #21262d);
            --shadow-card: 0 8px 32px rgba(0, 0, 0, 0.5);
            color-scheme: dark;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: var(--bg-base, #f8f9fa);                  /* Round 24 / theme-parity */
            color: var(--text-primary);
            min-height: 100vh;
            padding: 20px;
            transition: background-color 0.25s ease, color 0.25s ease;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
        }

        .header {
            background: var(--bg-surface);
            color: var(--text-primary);
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: var(--shadow-card);
            backdrop-filter: blur(10px);
            border: 1px solid var(--border-subtle);
            position: relative;
        }

        .header h1 {
            color: var(--text-primary);
            text-align: center;
            margin-bottom: 10px;
        }

        [data-bs-theme="dark"] .header h1 {
            color: var(--accent-primary);
        }

        .header p {
            text-align: center;
            color: var(--text-muted);
            font-size: 1.1em;
        }

        .header .subtitle {
            text-align: center;
            color: var(--accent-primary);
            font-size: 0.9em;
            margin-top: 5px;
            font-style: italic;
        }

        /*
         * Round 17.4 / Phase 6.6: theme toggle, mirroring the main
         * app's button.  Anchored to top-right of the header so it
         * is consistently visible across every admin sub-page.
         */
        .theme-toggle {
            position: absolute;
            top: 16px;
            right: 16px;
            background: transparent;
            border: 2px solid var(--border-subtle);
            color: var(--text-primary);
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 1rem;
            line-height: 1;
            cursor: pointer;
            transition: all 0.25s ease;
        }
        .theme-toggle:hover {
            color: var(--accent-primary);
            border-color: var(--accent-primary);
            box-shadow: 0 2px 10px var(--accent-glow);
        }
        .theme-toggle:focus {
            outline: none;
            box-shadow: 0 0 0 3px var(--accent-glow);
        }
        .theme-toggle .theme-icon-dark { display: none; }
        .theme-toggle .theme-icon-light { display: inline; }
        [data-bs-theme="dark"] .theme-toggle .theme-icon-dark { display: inline; }
        [data-bs-theme="dark"] .theme-toggle .theme-icon-light { display: none; }

        /* Round 60: Quit / shutdown button.  Lives in the same admin
         * header strip as the theme-toggle so the navbar stays
         * consistent with the main app.  Positioned just to the LEFT
         * of #theme-toggle (which is absolute / top-right) so the two
         * controls visually pair.  Uses a red hover/focus accent to
         * flag that this action is destructive (it kills the local
         * Flask process and releases ports 5151 + 5152). */
        .quit-btn {
            position: absolute;
            top: 16px;
            right: 64px;  /* 16px header padding + 40px theme-toggle width + 8px gap */
            background: transparent;
            border: 2px solid var(--border-subtle);
            color: var(--text-primary);
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 1rem;
            line-height: 1;
            cursor: pointer;
            transition: all 0.25s ease;
        }
        .quit-btn:hover {
            color: #fff;
            border-color: #e31c3d;
            background-color: #e31c3d;
            box-shadow: 0 2px 10px rgba(227, 28, 61, 0.35);
        }
        .quit-btn:focus {
            outline: none;
            box-shadow: 0 0 0 3px rgba(227, 28, 61, 0.45);
        }
        .quit-btn[disabled] {
            opacity: 0.5;
            cursor: not-allowed;
            box-shadow: none;
        }
        /* Round 60: full-screen post-shutdown overlay.  Hidden by
         * default; the JS toggles ``hidden`` off after a successful
         * 202 ack.  Same UX as the main app for consistency. */
        #adoptiq-shutdown-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.85);
            color: #fff;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 2147483647;
            padding: 2rem;
            text-align: center;
        }
        #adoptiq-shutdown-overlay h1 {
            font-size: 2rem;
            margin-bottom: 1rem;
        }
        #adoptiq-shutdown-overlay p {
            font-size: 1.125rem;
            max-width: 32rem;
            opacity: 0.85;
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }

        .dashboard-card {
            background: var(--bg-surface);
            color: var(--text-primary);
            padding: 20px;
            border-radius: 15px;
            box-shadow: var(--shadow-card);
            backdrop-filter: blur(10px);
            border: 1px solid var(--border-subtle);
        }

        .dashboard-card h3 {
            color: var(--text-primary);
            margin-bottom: 15px;
            border-bottom: 2px solid var(--accent-primary);
            padding-bottom: 10px;
        }

        [data-bs-theme="dark"] .dashboard-card h3 {
            color: var(--accent-primary);
        }

        .status-item {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
            padding: 8px;
            background: var(--accent-glow-soft);
            border-radius: 8px;
        }

        .status-label {
            font-weight: 600;
            color: var(--text-primary);
        }

        .status-value {
            font-weight: bold;
            color: var(--accent-primary);
        }

        .status-running {
            color: #27ae60;
        }

        .status-stopped {
            color: #e74c3c;
        }

        [data-bs-theme="dark"] .status-running {
            color: #84e08a;
        }

        [data-bs-theme="dark"] .status-stopped {
            color: #ff7e7e;
        }

        /* Round 12 / Phase 5.3: previously the admin traffic-light
           classes were hard-coded with the flat-UI palette
           (#e74c3c / #f39c12 / #27ae60), which drifted from the
           ``canonical_metrics.RISK_BAND_COLORS`` map used by the
           Word/Excel exports (#d62728 / #ff7f0e / #ffd700 /
           #2ca02c).  Operators reviewing the admin console would
           then see "high risk" rendered in a different red than
           the same row in the downloadable report, breaking the
           visual legend.  We project the canonical hexes through
           Jinja at render time so the admin UI shares the single
           source of truth.  Default values mirror the canonical
           palette in case ``canonical_metrics`` cannot be imported
           (degraded test contexts). */
        .risk-high {
            color: {{ admin_risk_color_high|default('#d62728') }};
            font-weight: bold;
        }

        .risk-medium {
            color: {{ admin_risk_color_medium|default('#ffd700') }};
            font-weight: bold;
        }

        .risk-low {
            color: {{ admin_risk_color_low|default('#2ca02c') }};
            font-weight: bold;
        }

        .table-container {
            background: var(--bg-surface);
            color: var(--text-primary);
            padding: 20px;
            border-radius: 15px;
            box-shadow: var(--shadow-card);
            backdrop-filter: blur(10px);
            border: 1px solid var(--border-subtle);
            margin-bottom: 20px;
        }

        .table-container h3 {
            color: var(--text-primary);
            margin-bottom: 15px;
            border-bottom: 2px solid var(--accent-primary);
            padding-bottom: 10px;
        }

        [data-bs-theme="dark"] .table-container h3 {
            color: var(--accent-primary);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
            color: var(--text-primary);
        }

        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid var(--border-subtle);
        }

        th {
            background: var(--th-bg);
            color: white;
            font-weight: 600;
        }

        [data-bs-theme="dark"] th {
            color: var(--accent-primary);
        }

        tr:hover {
            background: var(--accent-glow-soft);
        }

        .btn {
            display: inline-block;
            padding: 10px 20px;
            margin: 5px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            text-decoration: none;
            font-weight: 600;
            transition: all 0.3s ease;
        }

        .btn-primary {
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-primary-hover));
            color: white;
        }

        .btn-success {
            background: linear-gradient(135deg, #27ae60, #229954);
            color: white;
        }

        .btn-danger {
            background: linear-gradient(135deg, #e74c3c, #c0392b);
            color: white;
        }

        .btn-warning {
            background: linear-gradient(135deg, #f39c12, #e67e22);
            color: white;
        }

        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 15px var(--accent-glow);
        }

        .alert {
            padding: 15px;
            margin: 10px 0;
            border-radius: 8px;
            font-weight: 600;
        }

        .alert-success {
            background: rgba(39, 174, 96, 0.1);
            color: #27ae60;
            border: 1px solid #27ae60;
        }

        .alert-danger {
            background: rgba(231, 76, 60, 0.1);
            color: #e74c3c;
            border: 1px solid #e74c3c;
        }

        .alert-warning {
            background: rgba(243, 156, 18, 0.1);
            color: #f39c12;
            border: 1px solid #f39c12;
        }

        [data-bs-theme="dark"] .alert-success {
            background: rgba(39, 174, 96, 0.18);
            color: #84e08a;
        }

        [data-bs-theme="dark"] .alert-danger {
            background: rgba(231, 76, 60, 0.18);
            color: #ff7e7e;
        }

        [data-bs-theme="dark"] .alert-warning {
            background: rgba(243, 156, 18, 0.18);
            color: #ffc97a;
        }

        .refresh-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: linear-gradient(135deg, var(--accent-primary), var(--accent-primary-hover));
            color: white;
            border: none;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            font-size: 24px;
            cursor: pointer;
            box-shadow: 0 4px 15px var(--accent-glow);
            transition: all 0.3s ease;
        }

        .refresh-btn:hover {
            transform: scale(1.1);
        }

        .analytics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .analytics-card {
            background: var(--bg-surface);
            color: var(--text-primary);
            padding: 15px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
            border: 1px solid var(--border-subtle);
        }

        .analytics-number {
            font-size: 2em;
            font-weight: bold;
            color: var(--accent-primary);
        }

        .analytics-label {
            color: var(--text-muted);
            margin-top: 5px;
        }

        a {
            color: var(--accent-primary);
        }
        a:hover {
            color: var(--accent-primary-hover);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            {# Round 17.4 / Phase 6.6: theme toggle button.  Click
               handler is wired by the inline ``theme-toggle.js``
               equivalent at the bottom of this template; the
               early-paint <script> in <head> already applied the
               persisted choice before first paint. #}
            <button type="button"
                    id="theme-toggle"
                    class="theme-toggle"
                    aria-label="Toggle light or dark theme"
                    aria-pressed="false"
                    title="Toggle light or dark theme">
                <span class="theme-icon-dark" aria-hidden="true">&#9728;</span>
                <span class="theme-icon-light" aria-hidden="true">&#9789;</span>
            </button>
            {# Round 60: Quit / shutdown button.  Posts to /admin_quit
               which proxies to the main app's /api/shutdown using
               X-AdoptIQ-Internal so a single SIGTERM kills both the
               main server (5151) and this admin daemon (5152, same
               PID).  Click handler is in the inline IIFE near the
               bottom of this template. #}
            <button type="button"
                    id="adoptiq-quit-btn"
                    class="quit-btn"
                    aria-label="Quit AdoptIQ and shut down the local server"
                    title="Quit AdoptIQ">
                <span aria-hidden="true">&#9211;</span>
            </button>
            <h1>🚀 AdoptIQ Admin Dashboard v2.0</h1>
            <p>Advanced Monitoring & Security Analytics</p>
            <div class="subtitle">Real-time Report Monitoring • IP Tracking • Audit System • Security Logs</div>
            {% if main_app_url %}
            <p style="margin-top: 12px;"><a href="{{ main_app_url }}" target="_blank" rel="noopener noreferrer">← Back to AdoptIQ</a></p>
            {% endif %}
        </div>

        {% if request.args.get('message') %}
        {% set _mt = request.args.get('message_type', 'info') %}
        <div class="alert alert-{{ _mt if _mt in ('info', 'success', 'warning', 'danger') else 'info' }}">
            {{ request.args.get('message') }}
        </div>
        {% endif %}

        <!-- Analytics Overview (counts come from SELECT COUNT(*); the lists
             below show the most recent 50 rows only, so headline numbers
             must NEVER be derived from list lengths.) -->
        <!-- Round 2 / Phase 2.2: failed-vs-zero state — get_total_count
             returns ``None`` on DB failure so an unreachable monitoring
             DB renders "n/a", not the indistinguishable "0". -->
        <div class="analytics-grid">
            <div class="analytics-card">
                <div class="analytics-number">{% if totals.report_history is none %}n/a{% else %}{{ totals.report_history }}{% endif %}</div>
                <div class="analytics-label">Total Reports{% if totals.report_history is none %} <small style="color:#dc3545;">source unavailable</small>{% endif %}</div>
            </div>
            <div class="analytics-card">
                <div class="analytics-number">{% if totals.ip_connections is none %}n/a{% else %}{{ totals.ip_connections }}{% endif %}</div>
                <div class="analytics-label">Unique IPs{% if totals.ip_connections is none %} <small style="color:#dc3545;">source unavailable</small>{% endif %}</div>
            </div>
            <div class="analytics-card">
                <div class="analytics-number">{% if totals.total_requests is none %}n/a{% else %}{{ totals.total_requests }}{% endif %}</div>
                <div class="analytics-label">Total Requests</div>
            </div>
            <div class="analytics-card">
                <div class="analytics-number">{% if totals.error_logs is none %}n/a{% else %}{{ totals.error_logs }}{% endif %}</div>
                <div class="analytics-label">Recent Errors{% if totals.error_logs is none %} <small style="color:#dc3545;">source unavailable</small>{% endif %}</div>
            </div>
        </div>

        <!-- Server Control -->
        <div class="dashboard-grid">
            <div class="dashboard-card">
                <h3>🖥️ Server Status</h3>
                <!-- Round 2 / Phase 2.3: surface combined health
                     (port + data path) so a degraded backend is not
                     reported as fully Running. -->
                <div class="status-item">
                    <span class="status-label">Status:</span>
                    <span class="status-value {{ 'status-running' if server_status.running else 'status-stopped' }}">
                        {% if server_status.port_open and server_status.data_path_ok %}
                            Running (data path OK)
                        {% elif server_status.port_open and server_status.data_path_ok == False %}
                            Degraded (HTTP up, data path failing)
                        {% elif server_status.port_open %}
                            Running (data path unknown)
                        {% else %}
                            Stopped
                        {% endif %}
                    </span>
                </div>
                {% if server_status.port_open and server_status.data_path_ok == False %}
                <div class="status-item">
                    <span class="status-label">Data Path Detail:</span>
                    <span class="status-value risk-high">{{ server_status.data_path_detail }}</span>
                </div>
                {% endif %}
                <div class="status-item">
                    <span class="status-label">PID:</span>
                    <span class="status-value">{{ server_status.pid or 'N/A' }}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Host:</span>
                    <span class="status-value">{{ server_status.host or 'N/A' }}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Port:</span>
                    <span class="status-value">{{ server_status.port }}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Last Check:</span>
                    <span class="status-value">{{ server_status.last_check or 'Never' }}</span>
                </div>

                <div style="margin-top: 15px;">
                    {# Round 5 / Phase 2.3: destructive server controls
                       are POST + CSRF protected, rendered as inline
                       forms instead of GET-anchor links. #}
                    {% if server_status.running %}
                    <form method="POST" action="/stop_server" style="display:inline;">
                        <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                        <button type="submit" class="btn btn-danger">Stop Server</button>
                    </form>
                    {% else %}
                    <form method="POST" action="/start_server" style="display:inline;">
                        <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                        <button type="submit" class="btn btn-success">Start Server</button>
                    </form>
                    {% endif %}
                </div>
            </div>

            <div class="dashboard-card">
                <h3>📊 System Metrics</h3>
                {% if system_info.state == 'failed' %}
                <div class="status-item">
                    <span class="status-label">Status:</span>
                    <span class="status-value status-stopped">n/a (psutil unavailable)</span>
                </div>
                {% else %}
                <div class="status-item">
                    <span class="status-label">CPU Usage:</span>
                    <span class="status-value">{% if system_info.cpu_percent is none %}n/a{% else %}{{ "%.1f"|format(system_info.cpu_percent) }}%{% endif %}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Memory Usage:</span>
                    <span class="status-value">{% if system_info.memory_percent is none %}n/a{% else %}{{ "%.1f"|format(system_info.memory_percent) }}%{% endif %}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Disk Usage:</span>
                    <span class="status-value">{% if system_info.disk_percent is none %}n/a{% else %}{{ "%.1f"|format(system_info.disk_percent) }}%{% endif %}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Active Processes:</span>
                    <span class="status-value">{% if system_info.processes is none %}n/a{% else %}{{ system_info.processes }}{% endif %}</span>
                </div>
                {% endif %}
            </div>

            <div class="dashboard-card">
                <h3>🔍 Audit Summary</h3>
                <div class="status-item">
                    <span class="status-label">Total Audits:</span>
                    <span class="status-value">{{ audit_summary.get('total_audits_completed', 0) }}</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Average Score:</span>
                    <span class="status-value">{{ "%.1f"|format(audit_summary.get('average_audit_score', 0)) }}/100</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Pass Rate:</span>
                    {% set _total = audit_summary.get('total_audits_completed', 0) %}
                    {% set _passed = audit_summary.get('audits_passed', 0) %}
                    <span class="status-value">{{ "%.1f"|format((_passed / _total * 100) if _total else 0) }}%</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Failed Audits:</span>
                    <span class="status-value risk-high">{{ audit_summary.get('audits_failed', 0) }}</span>
                </div>
            </div>
        </div>

        <!-- Currently Running Reports -->
        {% if running_reports_failed %}
        <div class="table-container">
            <h3>🚀 Currently Running Reports</h3>
            <p style="color:#dc3545;"><strong>n/a</strong> — main app unreachable; cannot determine running reports.</p>
        </div>
        {% elif running_reports %}
        <div class="table-container">
            <h3>🚀 Currently Running Reports</h3>
            <table>
                <thead>
                    <tr>
                        <th>Analysis ID</th>
                        <th>Report Type</th>
                        <th>Manager/Customer</th>
                        <th>Status</th>
                        <th>Progress</th>
                        <th>ETA</th>
                        <th>Started</th>
                        <th>IP Address</th>
                        {# Round 66 / Pass 3 (B14): grounding diagnostics column. #}
                        <th title="R27 grounding validator rejection rate (rejected / total narrative calls)">Grounding</th>
                    </tr>
                </thead>
                <tbody>
                    {% for report in running_reports %}
                    <tr>
                        <td>{{ report.analysis_id }}</td>
                        <td>{{ report.report_type }}</td>
                        <td>{{ report.manager or report.customer_name }}</td>
                        <td>{{ report.status }}</td>
                        <td>{{ report.progress }}%</td>
                        <td>{{ report.eta_display or 'Calculating...' }}</td>
                        <td>{{ report.start_time }}</td>
                        <td>{{ report.ip_address or 'N/A' }}</td>
                        {# Round 66 / Pass 3 (B14): pill colored by rate.
                           - <=10% -> green (passing the post-R66/B11 acceptance bar)
                           - 10-25% -> yellow (degradation -- inspect rejection_records via /api/grounding-diagnostics)
                           - >25% -> red (R27 validator regression -- root-cause required)
                           Pre-R66 the admin had no per-report grounding signal; a 34% rate (Build 38 acceptance) had to be back-derived from the structured log. #}
                        <td>
                            {% set _r66_rate = report.grounding_rejection_rate %}
                            {% set _r66_total = report.grounding_total_count or 0 %}
                            {% set _r66_rej = report.grounding_rejection_count or 0 %}
                            {% if _r66_total > 0 %}
                                {% set _r66_pct = (_r66_rate * 100)|round(1) %}
                                {% if _r66_rate <= 0.10 %}
                                    {% set _r66_color = '#16a34a' %}
                                {% elif _r66_rate <= 0.25 %}
                                    {% set _r66_color = '#eab308' %}
                                {% else %}
                                    {% set _r66_color = '#dc2626' %}
                                {% endif %}
                                <span title="Rejected {{ _r66_rej }} of {{ _r66_total }} narrative calls"
                                      style="display:inline-block;padding:2px 8px;border-radius:10px;background-color:{{ _r66_color }};color:#ffffff;font-size:0.85em;font-weight:600;">
                                    {{ _r66_pct }}%
                                </span>
                            {% else %}
                                <span style="color:#6b7280;font-size:0.85em;">N/A</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}

        <!-- IP Connections Table -->
        <div class="table-container">
            <h3>🌐 IP Connections & Security</h3>
            <table>
                <thead>
                    <tr>
                        <th>IP Address</th>
                        <th>Country</th>
                        <th>City</th>
                        <th>First Seen</th>
                        <th>Last Seen</th>
                        <th>Requests</th>
                        <th>Risk Level</th>
                        <th>User Agent</th>
                    </tr>
                </thead>
                <tbody>
                    {% for connection in ip_connections[:20] %}
                    <tr>
                        <td>{{ connection.ip_address }}</td>
                        <td>{{ connection.country or 'Unknown' }}</td>
                        <td>{{ connection.city or 'Unknown' }}</td>
                        <td>{{ connection.first_seen }}</td>
                        <td>{{ connection.last_seen }}</td>
                        <td>{{ connection.request_count }}</td>
                        <td class="risk-{{ (connection.risk_level or 'unknown')|lower }}">{{ connection.risk_level or 'N/A' }}</td>
                        <td>{{ (connection.user_agent or '')[:50] }}{% if (connection.user_agent or '')|length > 50 %}...{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Report History Table -->
        <div class="table-container">
            <h3>📋 Report History</h3>
            <table>
                <thead>
                    <tr>
                        <th>Request ID</th>
                        <th>Report Type</th>
                        <th>Manager</th>
                        <th>Customer</th>
                        <th>Status</th>
                        <th>Start Time</th>
                        <th>IP Address</th>
                    </tr>
                </thead>
                <tbody>
                    {% for report in report_history[:20] %}
                    <tr>
                        <td>{{ report.request_id }}</td>
                        <td>{{ report.report_type }}</td>
                        <td>{{ report.manager }}</td>
                        <td>{{ report.customer_name or 'N/A' }}</td>
                        <td>{{ report.status }}</td>
                        <td>{{ report.start_time }}</td>
                        <td>{{ report.ip_address }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Error Logs Table -->
        <div class="table-container">
            <h3>⚠️ Recent Errors</h3>
            <table>
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Level</th>
                        <th>Source</th>
                        <th>Message</th>
                        <th>IP Address</th>
                    </tr>
                </thead>
                <tbody>
                    {% for error in error_logs[:20] %}
                    <tr>
                        <td>{{ error.timestamp }}</td>
                        <td>{{ error.level }}</td>
                        <td>{{ error.source }}</td>
                        <td>{{ (error.message or '')[:100] }}{% if (error.message or '')|length > 100 %}...{% endif %}</td>
                        <td>{{ error.ip_address or 'N/A' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Audit History Table -->
        <div class="table-container">
            <h3>✅ Audit History</h3>
            <table>
                <thead>
                    <tr>
                        <th>Analysis ID</th>
                        <th>Report Type</th>
                        <th>Audit Score</th>
                        <th>Status</th>
                        <th>File Exists</th>
                        <th>BEMS Detected</th>
                        <th>Data Sources OK</th>
                        <th>IP Verified</th>
                        <th>Timestamp</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for audit in audit_history %}
                    <tr>
                        <td>{{ audit.analysis_id }}</td>
                        <td>{{ audit.report_type }}</td>
                        <td><strong>{{ audit.score }}/100</strong></td>
                        <td class="{% if audit.status in ['excellent', 'good', 'acceptable'] %}status-running{% else %}status-stopped{% endif %}">
                            {{ audit.status }}
                        </td>
                        <td>{{ '✓' if audit.checks.get('file_exists') else '✗' }}</td>
                        <td>{{ '✓' if audit.checks.get('bems_detection') else '✗' }}</td>
                        <td>{{ '✓' if audit.checks.get('data_sources') else '✗' }}</td>
                        <td>{{ '✓' if audit.checks.get('ip_security') else '✗' }}</td>
                        <td>{{ audit.timestamp }}</td>
                        <td>
                            {# Round 8 / Phase 4.8: re-audit is destructive
                               (writes audit history, calls main app), so it
                               is now a POST with the admin CSRF token instead
                               of a plain anchor that any cross-origin page
                               could trigger via <img src> or auto-redirect. #}
                            <form method="POST" action="/audit_report/{{ audit.analysis_id }}" style="display:inline;">
                                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                                <button type="submit" class="btn btn-primary" style="font-size: 0.8em; padding: 5px 10px;">Re-audit</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Action Buttons -->
        <div style="text-align: center; margin: 20px 0;">
            {# Round 5 / Phase 2.3: destructive log controls are POST + CSRF
               protected, rendered as inline forms instead of GET-anchor
               links so a cross-origin <img>/<a>/<iframe> can't trigger
               them on an authenticated admin. #}
            <form method="POST" action="/export_logs" style="display:inline;">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <button type="submit" class="btn btn-primary">Export Logs</button>
            </form>
            <form method="POST" action="/clear_logs" style="display:inline;"
                  onsubmit="return confirm('Clear all admin logs? This is irreversible.');">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <button type="submit" class="btn btn-warning">Clear Logs</button>
            </form>
            <a href="/api/analytics" class="btn btn-success">View Analytics</a>
        </div>

        {# Round 17 / Phase D.5: Corpus tile + CSRF-protected refresh action.

           Round 26 / Phase A: rename the user-visible heading from
           "CSOne Knowledge Corpus" to "AdoptIQ Intelligence" so the
           tile matches the user-facing banner on / (analyze.html).
           Internal symbols, URLs (/api/corpus/*, /corpus_refresh),
           env vars (CORPUS_KNOWLEDGE_ENABLED), and DB filenames
           are intentionally NOT renamed -- they are public contracts
           pinned by tests, sysadmin runbooks, and the encrypted-DB
           on-disk path.

           Round 26 / Phase E: also surface
           ``corpus_status.boot.in_progress`` and ``last_started_at``
           (already present on the JSON payload but never rendered),
           plus any errors recorded in
           ``corpus_status.boot.last_stats.errors`` so the operator
           can diagnose a degraded run from the dashboard without
           tailing logs. #}
        <div class="table-container">
            <h3>AdoptIQ Intelligence</h3>
            <p style="color:#6c757d; font-size:0.85em; margin-top:-0.5em;">
                Uses the prebaked local corpus immediately, then refreshes
                from generated reports, Intelligence uploads, and OneDrive
                when available so Ask AI,
                Customer 360, and Playbook can ground answers in
                real case history, resolutions, and customer
                sentiment.
            </p>
            {% if corpus_status_failed %}
                <p style="color:#dc3545;">
                    <strong>Status:</strong> n/a (main app unreachable from
                    admin process &mdash; check that AdoptIQ is running)
                </p>
            {% elif not corpus_status.enabled %}
                <p>
                    <strong>Status:</strong> disabled
                    (set <code>CORPUS_KNOWLEDGE_ENABLED=true</code> to enable)
                </p>
            {% elif not corpus_status.available %}
                <p>
                    <strong>Status:</strong> unavailable
                    {% if corpus_status.reason %}
                        &mdash; {{ corpus_status.reason }}
                    {% endif %}
                </p>
                {% if corpus_status.boot.last_error %}
                <p>
                    <strong>Last error:</strong>
                    {{ corpus_status.boot.last_error_kind or 'error' }}:
                    {{ corpus_status.boot.last_error }}
                </p>
                {% endif %}
            {% else %}
                {% if corpus_status.boot.in_progress %}
                <p style="color:#0d6efd;">
                    <strong>Status:</strong> indexing in progress
                    {% if corpus_status.boot.last_started_at %}
                        &mdash; started {{ corpus_status.boot.last_started_at }}
                    {% endif %}
                </p>
                {% else %}
                <p><strong>Status:</strong> available</p>
                {% endif %}
                <p>
                    <strong>Files indexed:</strong>
                    {{ corpus_status.corpus.files_parsed }} /
                    {{ corpus_status.corpus.files_total }}
                    &middot;
                    <strong>Customers:</strong> {{ corpus_status.corpus.customers }}
                    &middot;
                    <strong>Cases:</strong> {{ corpus_status.corpus.cases }}
                    &middot;
                    <strong>Chunks:</strong> {{ corpus_status.corpus.chunks }}
                </p>
                <p>
                    <strong>Last parsed:</strong>
                    {{ corpus_status.corpus.last_parsed_at or 'never' }}
                    &middot;
                    <strong>Last indexed:</strong>
                    {{ corpus_status.corpus.indexed_at or 'never' }}
                    &middot;
                    <strong>Schema:</strong>
                    v{{ corpus_status.corpus.schema_version }}
                </p>

                {# Round 37 / Phase 2 (extended Round 83 / Build 59):
                   OneDrive sign-in + sync state pills.

                   Round 36 replaced MSAL/Graph runtime auth with
                   "OneDrive sync presence as the auth signal" --
                   AdoptIQ trusts that the OneDrive desktop client
                   handled SSO/MFA/admin-consent and verifies by
                   probing ``Config.CSONE_ONEDRIVE_FOLDER`` for >=1
                   non-empty file.  Round 83 splits that single check
                   into TWO independent signals so the admin tile can
                   distinguish "signed in but corpus share missing"
                   from "not signed in to OneDrive at all":

                     * boot.signed_in_proxy: cross-platform probe of
                       whether the OneDrive desktop client is set up
                       at all on this host. Values: signed_in_cisco /
                       signed_in_other / not_signed_in / unknown.
                     * boot.onedrive_status: folder-specific check
                       on Config.CSONE_ONEDRIVE_FOLDER. Values:
                       synced / not_synced / unknown.

                   Round 108 / Corpus Smoothness: these are refresh-source
                   diagnostics only. Missing OneDrive no longer blocks Ask AI
                   when the local/prebaked corpus is available. #}
                {% set _od_status = corpus_status.boot.onedrive_status %}
                {% set _od_count  = corpus_status.boot.onedrive_file_count %}
                {% set _od_signin = corpus_status.boot.signed_in_proxy %}
                {% if _od_signin %}
                <p>
                    <strong>OneDrive sign-in:</strong>
                    {% if _od_signin == 'signed_in_cisco' %}
                        <span class="risk-low">signed in (Cisco)</span>
                    {% elif _od_signin == 'signed_in_other' %}
                        <span class="risk-medium">signed in (non-Cisco account)</span>
                    {% elif _od_signin == 'not_signed_in' %}
                        <span class="risk-high">not signed in</span>
                    {% else %}
                        <span style="color:#6c757d;">unknown</span>
                    {% endif %}
                </p>
                {% endif %}
                {% if _od_status %}
                <p>
                    <strong>OneDrive sync:</strong>
                    {% if _od_status == 'synced' %}
                        <span class="risk-low">synced</span>
                    {% elif _od_status == 'not_synced' %}
                        <span class="risk-medium">not synced</span>
                    {% else %}
                        <span style="color:#6c757d;">unknown</span>
                    {% endif %}
                    {% if _od_count is not none %}
                        &middot;
                        {{ _od_count }} file{{ '' if _od_count == 1 else 's' }} ready
                    {% endif %}
                    {% if _od_status != 'synced' %}
                    <span style="color:#6c757d; font-size:0.9em;">
                        &mdash; add the
                        <code>AdoptIQ_CSOne_Reports</code> shortcut to
                        OneDrive (right-click the SharePoint folder
                        and choose &ldquo;Add shortcut to OneDrive&rdquo;)
                        to improve shared-source refresh coverage
                    </span>
                    {% endif %}
                </p>
                {% endif %}

                {# Round 83 / Build 59: dedicated banner for the new
                   ``signed_in_no_corpus`` state.  Distinct from
                   ``blocked_no_onedrive`` below: the OneDrive desktop
                   client is signed in, the user just needs to add
                   the corpus share to their tree.  No need to
                   re-authenticate. #}
                {% if corpus_status.boot.source == 'signed_in_no_corpus' %}
                <p style="margin-top: 0.6em; color: #856404; background: #fff3cd; border: 1px solid #ffeeba; padding: 0.6em 0.8em; border-radius: 4px;">
                    <strong>Optional corpus share not connected:</strong>
                    The local corpus remains usable. OneDrive is signed in
                    with a Cisco account, but the AdoptIQ corpus share is not
                    in the user's OneDrive tree yet. Add it when the operator
                    wants shared-source updates included in background refresh.
                </p>
                {% endif %}

                {# Round 53 / Phase 53.4: dedicated banner for the
                   ``blocked_no_onedrive`` state.  Surfaces the same
                   remediation guidance the analyze-page panel shows
                   so the admin operator can see optional refresh-source
                   guidance without bouncing back to the user UI. Round 108
                   no longer disables refresh/rebuild/reset for this legacy
                   state because the prebaked/local corpus is the baseline.
                   Source-shape pinned by
                   ``tests/test_round53_admin_tile_blocked_state.py``. #}
                {% if corpus_status.boot.source == 'blocked_no_onedrive' %}
                <p style="margin-top: 0.6em; color: #856404; background: #fff3cd; border: 1px solid #ffeeba; padding: 0.6em 0.8em; border-radius: 4px;">
                    <strong>Optional OneDrive refresh source missing:</strong>
                    AdoptIQ ships with a prebaked local corpus and can index
                    generated reports and Intelligence uploads without
                    OneDrive. Add the <code>AdoptIQ_CSOne_Reports</code>
                    shortcut when shared-source refresh coverage is needed.
                </p>
                {% endif %}

                {# Round 108 / Corpus Smoothness: dense-retrieval quality
                   status. Hybrid-ready is preferred; lexical fallback is
                   acceptable and non-blocking when the embedder cannot load. #}
                {% if corpus_status.boot.embedder_status or corpus_status.boot.dense_retrieval_status %}
                <p>
                    <strong>Dense retrieval:</strong>
                    {% if corpus_status.boot.dense_retrieval_status == 'stale_or_lexical' %}
                        <span class="risk-medium">lexical fallback active</span>
                    {% elif corpus_status.boot.embedder_status == 'ready' or corpus_status.boot.dense_retrieval_status == 'ready' %}
                        <span class="risk-low">hybrid ready</span>
                    {% else %}
                        <span style="color:#6c757d;">{{ corpus_status.boot.embedder_status or corpus_status.boot.dense_retrieval_status }}</span>
                    {% endif %}
                    {% if corpus_status.boot.dense_vectors_upserted is not none %}
                        &middot; {{ corpus_status.boot.dense_vectors_upserted }} vector{{ '' if corpus_status.boot.dense_vectors_upserted == 1 else 's' }} updated
                    {% endif %}
                    {% if corpus_status.boot.ask_ai_retrieval_method %}
                        &middot; method: <code>{{ corpus_status.boot.ask_ai_retrieval_method }}</code>
                    {% endif %}
                    {% if corpus_status.boot.embedder_load_error or corpus_status.boot.dense_vector_error %}
                        &middot; {{ corpus_status.boot.embedder_load_error or corpus_status.boot.dense_vector_error }}
                    {% endif %}
                </p>
                {% endif %}

                {% if corpus_status.boot.encrypted_path %}
                <p>
                    <strong>Encrypted cache:</strong>
                    <code>{{ corpus_status.boot.encrypted_path }}</code>
                </p>
                {% endif %}

                {# Round 17.2 SharePoint sub-block removed in Round 37 /
                   Phase 3.  The MSAL/Graph runtime path was deleted in
                   Round 36, so ``corpus_status.boot.sharepoint`` is
                   always ``None`` and rendering an inline form for
                   ``/sharepoint_signin`` / ``/sharepoint_refresh``
                   would just produce dead UI that 404s.  OneDrive sync
                   presence is now surfaced via the
                   ``boot.onedrive_status`` row above (Round 37 /
                   Phase 2), and the ``Re-index now`` button below
                   (Round 37 / Phase 4) covers the manual refresh
                   path. #}

                {# Round 17.1 + 17.2 + 102: per-source breakdown (OneDrive
                   sync, local generated reports, and any opt-in
                   intel-uploads pre-seed). Rendered only when the
                   bootstrap recorded source-level stats; otherwise
                   the summary stats above are sufficient.  Note:
                   the pre-Round-36 SharePoint cache source is no
                   longer populated; the ``last_sources`` list now
                   never includes a ``sharepoint_csone`` row. #}
                {% if corpus_status.boot.last_sources %}
                <p style="margin-top: 0.6em;">
                    <strong>Per-source breakdown</strong>
                    <span style="color:#6c757d; font-size: 0.9em;">
                        (last bootstrap pass)
                    </span>
                </p>
                <table class="data-table" style="font-size: 0.9em;">
                    <thead>
                        <tr>
                            <th>Source</th>
                            <th>Folder</th>
                            <th>Seen</th>
                            <th>Parsed</th>
                            <th>Skipped</th>
                            <th>Failed</th>
                            <th>Chunks added</th>
                        </tr>
                    </thead>
                    <tbody>
                    {% for source in corpus_status.boot.last_sources %}
                        <tr>
                            <td>{{ source.label }}</td>
                            <td><code>{{ source.dir }}</code></td>
                            <td>{{ source.files_seen }}</td>
                            <td>{{ source.files_parsed }}</td>
                            <td>{{ source.files_skipped }}</td>
                            <td>{{ source.files_failed }}</td>
                            <td>{{ source.chunks_added }}</td>
                        </tr>
                    {% endfor %}
                    </tbody>
                </table>
                {% endif %}

                {# Round 26 / Phase E + Round 26 - review (R26-OPEN-001):
                   surface per-file error info from the most recent
                   index pass.  AdoptIQ is internal-only so filenames
                   are intentionally rendered (the original Round 17
                   PII-suppression stance no longer applies).

                   Production shape (post-R26-OPEN-001):
                     last_stats.errors       -- list of {file, reason} dicts (<=5)
                     last_stats.errors_total -- full count

                   Legacy / defensive fallback shapes still handled so
                   a stale payload can't crash the tile:
                     * int (legacy R17 / pre-R26-OPEN-001 production)
                     * list of plain strings (test fixture before the
                       error-row split landed)
                #}
                {% set _last_errors = (corpus_status.boot.last_stats or {}).get('errors') if corpus_status.boot.last_stats else none %}
                {% set _errors_total = (corpus_status.boot.last_stats or {}).get('errors_total') if corpus_status.boot.last_stats else none %}
                {% if _last_errors is number and _last_errors > 0 %}
                {# Legacy fallback: pre-R26-OPEN-001 payloads emitted
                   only the count.  Render it so a stale subprocess /
                   downgraded build still surfaces the failure. #}
                <p style="margin-top: 0.6em; color:#dc3545;">
                    <strong>Recent index errors:</strong>
                    {{ _last_errors }} file{{ '' if _last_errors == 1 else 's' }} failed
                    in the last bootstrap pass.
                    <span style="color:#6c757d; font-size: 0.9em;">
                        (Legacy payload shape; check application logs
                        for filenames.)
                    </span>
                </p>
                {% elif _last_errors and _last_errors is not number %}
                <p style="margin-top: 0.6em;">
                    <strong>Recent index errors</strong>
                    <span style="color:#6c757d; font-size: 0.9em;">
                        {% if _errors_total and _errors_total > _last_errors|length %}
                            ({{ _last_errors|length }} of {{ _errors_total }} shown)
                        {% else %}
                            (last bootstrap pass)
                        {% endif %}
                    </span>
                </p>
                <ul class="data-table" style="font-size: 0.9em; padding-left: 1.5em;">
                    {% for err in _last_errors[:5] %}
                        <li style="color:#dc3545;">
                            {% if err is mapping %}
                                <code>{{ err.get('file') or err.get('path') or '?' }}</code>
                                &mdash; {{ err.get('reason') or err.get('error') or 'error' }}
                            {% else %}
                                {{ err }}
                            {% endif %}
                        </li>
                    {% endfor %}
                </ul>
                {% endif %}
            {% endif %}

            {# Refresh button always rendered so the operator can recover from
               an "unavailable" or "disabled" state without leaving the admin
               console.  Refresh is CSRF-protected (admin token verified
               server-side before the proxy call to the main app).

               Round 26 / Phase E: relabel the buttons to match the
               user-visible "AdoptIQ Intelligence" framing.  The
               POST target stays ``/corpus_refresh`` so admin runbooks
               and existing tests keep working.

               Round 37 / Phase 4: rename "Run incremental" -> "Re-index
               now" (matches the analyze-page corpus panel labelling)
               and disable both buttons while ``boot.in_progress`` is
               true so the operator cannot stack refresh requests on
               an active index pass.

               Round 52.1 / Build28: expose the existing ``/corpus_reset``
               admin proxy as a visible operator escape hatch.  It is
               deliberately confirm-gated and disabled while indexing,
               because it preserves and rebuilds the user's local
               encrypted corpus cache from all available corpus sources. #}
            {% set _corpus_busy = corpus_status.boot.in_progress %}
            {# Round 108 / Corpus Smoothness: OneDrive states are optional
               refresh diagnostics. Only an active index pass disables these
               controls. #}
            {% set _corpus_disabled = _corpus_busy %}
            <form method="POST" action="/corpus_refresh" style="display:inline;">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <button type="submit" class="btn btn-primary"
                        {% if _corpus_disabled %}disabled{% endif %}
                        title="{% if _corpus_busy %}Indexing already in progress{% else %}Re-index from generated reports, uploads, and OneDrive when available{% endif %}">
                    Re-index now
                </button>
            </form>
            <form method="POST" action="/corpus_refresh" style="display:inline;"
                  onsubmit="return confirm('Rebuild the entire encrypted cache from scratch? This may take several minutes.');">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <input type="hidden" name="rebuild" value="1">
                <button type="submit" class="btn btn-warning"
                        {% if _corpus_disabled %}disabled{% endif %}
                        title="{% if _corpus_busy %}Indexing already in progress{% else %}Discard the encrypted cache and rebuild from all available sources{% endif %}">
                    Rebuild
                </button>
            </form>
            <form method="POST" action="/corpus_reset" style="display:inline;"
                  onsubmit="return confirm('Reset the local encrypted corpus cache? This preserves a broken copy for troubleshooting, then rebuilds from all available corpus sources.');">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <button type="submit" class="btn btn-danger"
                        {% if _corpus_disabled %}disabled{% endif %}
                        title="{% if _corpus_busy %}Indexing already in progress{% else %}Preserve and rebuild the local encrypted corpus cache{% endif %}">
                    Reset corpus
                </button>
            </form>
        </div>

        {# Round 69 / Build 43: admin-side mirror of the operator-flippable
           CircuIT model preferences.  Same Test-gates-Save UX as the
           Ask AI page + analyze page; submits via the
           /admin_settings/{ask_ai,report,llm_ping} proxy routes which
           validate the admin CSRF and forward to the main app's
           /api/settings/* endpoints with X-AdoptIQ-Internal. #}
        <div class="table-container" id="r69AdminModelPrefs">
            <h3>LLM Model Preferences (Round 69 / Build 43)</h3>
            <p style="color:#6c757d; margin-bottom:1rem;">
                Per-call-site CircuIT model overrides.  Empty value clears the override
                and falls back to <code>CIRCUIT_MODEL_NAME</code> (default
                <code>gemini-3.1-flash-lite</code>, Round 77 / Build 53).
                <strong>Test</strong> must succeed before <strong>Save</strong> is enabled
                so a typo or unprovisioned model cannot land in <code>settings.json</code>.
            </p>

            <!-- Round 73 / Phase 4 (UX-3): admin model pickers are now
                 strict 2-option <select> dropdowns matching the
                 /preferences hub.  ``_R73_ALLOWED_MODEL_IDS`` in
                 app_simple.py is the server-side source of truth; when
                 it grows, expand the <option> rows here AND on
                 templates/preferences.html together.  The admin proxy
                 routes (/admin_settings/{ask_ai,report}_model) forward
                 to the same /api/settings/* endpoints so the allow-list
                 enforcement holds at the API layer regardless of
                 origin.  Pinned by tests/test_round73_model_dropdown_allowlist.py. -->
            <form id="r69AdminAskAiForm" data-r69-admin-card="ask_ai"
                  style="border-left:4px solid #0d6efd; padding:0.75rem 1rem; margin-bottom:1rem; background:#f8f9fa;">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:0.4rem;">
                    <strong>Ask AI model</strong>
                    <span class="badge bg-secondary" data-r69-admin-active>active: -</span>
                </div>
                <div style="display:flex; gap:0.4rem; align-items:center;">
                    {# Round 77: gemini-3.1-flash-lite is the first option so it's the
                       implicit selected default when the persisted value is empty. #}
                    <select name="model_name"
                            class="form-control form-select" data-r69-admin-input
                            aria-label="Ask AI model name"
                            style="font-family:monospace; flex-grow:1;">
                        <option value="gemini-3.1-flash-lite" selected>gemini-3.1-flash-lite</option>
                        <option value="gpt-5-nano">gpt-5-nano</option>
                    </select>
                    <button type="button" class="btn btn-outline-primary"
                            data-r69-admin-test-btn>Test</button>
                    <button type="button" class="btn btn-primary"
                            data-r69-admin-save-btn disabled>Save</button>
                </div>
                <div class="small mt-1" data-r69-admin-result aria-live="polite"
                     style="min-height:1.2em; color:#6c757d;"></div>
            </form>

            <form id="r69AdminReportForm" data-r69-admin-card="report"
                  style="border-left:4px solid #198754; padding:0.75rem 1rem; background:#f8f9fa;">
                <input type="hidden" name="_admin_csrf" value="{{ admin_csrf_token }}">
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:0.4rem;">
                    <strong>Report narrative model</strong>
                    <span class="badge bg-secondary" data-r69-admin-active>active: -</span>
                </div>
                <div style="display:flex; gap:0.4rem; align-items:center;">
                    {# Round 77: gemini-3.1-flash-lite is the first option so it's the
                       implicit selected default when the persisted value is empty. #}
                    <select name="model_name"
                            class="form-control form-select" data-r69-admin-input
                            aria-label="Report narrative model name"
                            style="font-family:monospace; flex-grow:1;">
                        <option value="gemini-3.1-flash-lite" selected>gemini-3.1-flash-lite</option>
                        <option value="gpt-5-nano">gpt-5-nano</option>
                    </select>
                    <button type="button" class="btn btn-outline-primary"
                            data-r69-admin-test-btn>Test</button>
                    <button type="button" class="btn btn-primary"
                            data-r69-admin-save-btn disabled>Save</button>
                </div>
                <div class="small mt-1" data-r69-admin-result aria-live="polite"
                     style="min-height:1.2em; color:#6c757d;"></div>
                <div class="small mt-1" style="color:#856404;">
                    <i>Warning: changing this affects the R27 grounding-rejection rate;
                    re-run a sample report after Save and watch the per-customer fallback log.</i>
                </div>
            </form>
        </div>

        <div class="table-container">
            <h3>Debug Controls</h3>
            <p><strong>Verbose Debug:</strong> {{ 'ON' if verbose_debug else 'OFF' }}</p>
            <p><strong>Snowflake Queries (since reset):</strong> {% if snowflake_query_count_failed %}<span style="color:#dc3545;">n/a (debug endpoint unreachable)</span>{% else %}{{ snowflake_query_count }}{% endif %}</p>
            <button class="btn btn-warning" onclick="toggleVerboseDebug()">
                {{ 'Disable' if verbose_debug else 'Enable' }} Verbose Debug
            </button>
            <button class="btn btn-primary" onclick="resetSnowflakeQueryMetrics()">
                Reset Query Counter
            </button>
        </div>
    </div>

    <button class="refresh-btn" onclick="location.reload()">🔄</button>

    <script>
        const verboseDebugEnabled = {{ 'true' if verbose_debug else 'false' }};

        // Auto-refresh every 30 seconds
        setTimeout(function() {
            location.reload();
        }, 30000);

        // Add click handlers for better UX
        document.querySelectorAll('.btn').forEach(btn => {
            btn.addEventListener('click', function(e) {
                if (this.href.includes('clear_logs')) {
                    if (!confirm('Are you sure you want to clear all logs? This action cannot be undone.')) {
                        e.preventDefault();
                    }
                }
            });
        });

        async function toggleVerboseDebug() {
            try {
                /* Round 8 / Phase 4.7: include the admin CSRF token so the
                   POST is accepted by the now-CSRF-guarded
                   /api/debug/verbose proxy. */
                const response = await fetch('/api/debug/verbose', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-AdoptIQ-Admin-CSRF': '{{ admin_csrf_token }}',
                    },
                    body: JSON.stringify({ enabled: !verboseDebugEnabled }),
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    alert(result.error || 'Failed to toggle verbose debug mode.');
                    return;
                }
                location.reload();
            } catch (err) {
                alert('Failed to toggle verbose debug mode.');
            }
        }

        async function resetSnowflakeQueryMetrics() {
            try {
                /* Round 8 / Phase 4.7: include admin CSRF header. */
                const response = await fetch('/api/debug/verbose', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-AdoptIQ-Admin-CSRF': '{{ admin_csrf_token }}',
                    },
                    body: JSON.stringify({ enabled: verboseDebugEnabled, reset_query_metrics: true }),
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    alert(result.error || 'Failed to reset Snowflake query metrics.');
                    return;
                }
                location.reload();
            } catch (err) {
                alert('Failed to reset Snowflake query metrics.');
            }
        }

        /*
         * Round 69 / Build 43: admin-side LLM model preferences UI.
         * Self-contained ES5 (the admin template renders inline, no
         * static asset chain) that wires both forms with a Test-gates-
         * Save contract.  POST targets are the /admin_settings/* proxy
         * routes which validate the admin CSRF and forward to the main
         * app's /api/settings/* endpoints with X-AdoptIQ-Internal.
         *
         * Pre-vetting: an input change MUST disable Save (so the cached
         * "passed test" result cannot promote a typo'd model).  Save is
         * re-enabled only by a successful Test response.
         */
        (function () {
            var R69_FORMS = [
                {key: 'ask_ai', getUrl: '/api/settings/ask-ai-model', postUrl: '/admin_settings/ask_ai_model'},
                {key: 'report', getUrl: '/api/settings/report-model', postUrl: '/admin_settings/report_model'}
            ];
            var R69_PING_URL = '/admin_settings/llm_ping';

            function r69QSAll(form, sel) { return form.querySelectorAll(sel); }
            function r69Get(form, sel) { return form.querySelector(sel); }

            function r69SetResult(form, msg, kind) {
                var el = r69Get(form, '[data-r69-admin-result]');
                if (!el) return;
                el.textContent = String(msg || '');
                el.style.color = kind === 'ok' ? '#198754' : (kind === 'err' ? '#dc3545' : '#6c757d');
            }

            function r69SetActive(form, value) {
                var badge = r69Get(form, '[data-r69-admin-active]');
                if (!badge) return;
                badge.textContent = 'active: ' + (value || '-');
            }

            function r69LoadActive(form, getUrl) {
                fetch(getUrl, {method: 'GET', headers: {'Accept': 'application/json'}})
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (j) {
                        if (!j || !j.ok) return;
                        var input = r69Get(form, '[data-r69-admin-input]');
                        if (input) {
                            var persisted = j.persisted_value || '';
                            /* Round 73 / UX-3: <select> assignment for
                               an unmatched value is a silent no-op
                               which would mislead the operator.  Only
                               reflect the persisted value on the
                               control if it's selectable; the active
                               badge below carries the truth in any
                               case. */
                            if (input.tagName === 'SELECT') {
                                var matched = false;
                                for (var i = 0; i < input.options.length; i++) {
                                    if (input.options[i].value === persisted) {
                                        matched = true;
                                        break;
                                    }
                                }
                                if (matched || persisted === '') {
                                    input.value = persisted;
                                }
                            } else {
                                input.value = persisted;
                            }
                        }
                        r69SetActive(form, j.active_value);
                    })
                    .catch(function () { /* silent -- read-only path */ });
            }

            function r69BindForm(form, conf) {
                var input = r69Get(form, '[data-r69-admin-input]');
                var testBtn = r69Get(form, '[data-r69-admin-test-btn]');
                var saveBtn = r69Get(form, '[data-r69-admin-save-btn]');
                if (!input || !testBtn || !saveBtn) return;

                // Any input/selection change locks Save until next
                // successful Test.  Round 73 / UX-3 swapped the input
                // for a <select>; bind both ``input`` and ``change``
                // so the contract holds for either form.
                var lockSave = function () {
                    saveBtn.disabled = true;
                    r69SetResult(form, '', 'neutral');
                };
                input.addEventListener('input', lockSave);
                input.addEventListener('change', lockSave);

                testBtn.addEventListener('click', function () {
                    var value = (input.value || '').trim();
                    if (!value) {
                        // Empty == clear override; allow Save without ping.
                        saveBtn.disabled = false;
                        r69SetResult(form, 'Empty value will clear the override (falls back to env / config default). Click Save to confirm.', 'neutral');
                        return;
                    }
                    r69SetResult(form, 'Pinging CircuIT...', 'neutral');
                    var fd = new FormData();
                    fd.append('_admin_csrf', '{{ admin_csrf_token }}');
                    fd.append('model_name', value);
                    fetch(R69_PING_URL, {method: 'POST', body: fd})
                        .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); })
                        .then(function (pair) {
                            var status = pair[0]; var j = pair[1] || {};
                            if (status === 200 && j.ok) {
                                saveBtn.disabled = false;
                                r69SetResult(form, 'OK (' + (j.latency_ms || '?') + 'ms). You may save.', 'ok');
                            } else {
                                saveBtn.disabled = true;
                                r69SetResult(form, 'Test failed: ' + (j.error || ('HTTP ' + status)), 'err');
                            }
                        })
                        .catch(function (e) {
                            saveBtn.disabled = true;
                            r69SetResult(form, 'Test failed: network error', 'err');
                        });
                });

                saveBtn.addEventListener('click', function () {
                    var value = (input.value || '').trim();
                    var fd = new FormData();
                    fd.append('_admin_csrf', '{{ admin_csrf_token }}');
                    fd.append('model_name', value);
                    fetch(conf.postUrl, {method: 'POST', body: fd})
                        .then(function (r) { return r.json().then(function (j) { return [r.status, j]; }); })
                        .then(function (pair) {
                            var status = pair[0]; var j = pair[1] || {};
                            if (status === 200 && j.ok) {
                                r69SetActive(form, j.active_value);
                                r69SetResult(form, 'Saved. Active model: ' + (j.active_value || '-'), 'ok');
                                saveBtn.disabled = true;
                            } else {
                                r69SetResult(form, 'Save failed: ' + (j.error || ('HTTP ' + status)), 'err');
                            }
                        })
                        .catch(function () {
                            r69SetResult(form, 'Save failed: network error', 'err');
                        });
                });

                r69LoadActive(form, conf.getUrl);
            }

            function r69Init() {
                R69_FORMS.forEach(function (conf) {
                    var form = document.querySelector('[data-r69-admin-card="' + conf.key + '"]');
                    if (form) r69BindForm(form, conf);
                });
            }

            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', r69Init);
            } else {
                r69Init();
            }
        })();

        /*
         * Round 17.4 / Phase 6.6: theme toggle wiring.  This is a
         * deliberately small, self-contained ES5 implementation
         * (no external file fetch -- the admin dashboard renders a
         * single inlined template, not a Jinja partial chain) that
         * mirrors the contract of ``static/js/theme-toggle.js`` in
         * the main app:
         *   * persist user choice in localStorage under
         *     ``adoptiq-theme`` (same key as the main app so a user
         *     who toggled there sees the same theme here).
         *   * accept only "dark" | "light" -- corrupt values are
         *     ignored and we fall back to "dark".
         *   * keep ``aria-pressed``/``aria-label`` honest for screen
         *     readers.
         * The early-paint script in <head> already applied the
         * stored value before first paint, so this block only has to
         * reflect the live attribute and bind the click handler.
         */
        (function () {
            var STORAGE_KEY = 'adoptiq-theme';
            var DEFAULT_THEME = 'dark';
            var VALID_THEMES = { dark: true, light: true };

            function safeReadStored() {
                try {
                    return window.localStorage.getItem(STORAGE_KEY);
                } catch (_) {
                    return null;
                }
            }
            function safeWriteStored(theme) {
                try { window.localStorage.setItem(STORAGE_KEY, theme); } catch (_) { /* private mode */ }
            }
            function getCurrentTheme() {
                var stored = safeReadStored();
                if (VALID_THEMES[stored]) return stored;
                var attr = document.documentElement.getAttribute('data-bs-theme');
                if (VALID_THEMES[attr]) return attr;
                return DEFAULT_THEME;
            }
            function applyTheme(theme) {
                if (!VALID_THEMES[theme]) theme = DEFAULT_THEME;
                document.documentElement.setAttribute('data-bs-theme', theme);
                updateToggleAria(theme);
            }
            function updateToggleAria(theme) {
                var btn = document.getElementById('theme-toggle');
                if (!btn) return;
                var nextTheme = theme === 'dark' ? 'light' : 'dark';
                btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
                btn.setAttribute('aria-label', 'Switch to ' + nextTheme + ' theme');
            }
            function toggleTheme() {
                var current = getCurrentTheme();
                var next = current === 'dark' ? 'light' : 'dark';
                applyTheme(next);
                safeWriteStored(next);
            }
            document.addEventListener('DOMContentLoaded', function () {
                applyTheme(getCurrentTheme());
                var btn = document.getElementById('theme-toggle');
                if (btn) {
                    btn.addEventListener('click', toggleTheme);
                }
            });
        })();

        /*
         * Round 60: Quit / shutdown button click handler.
         *
         * Mirrors static/js/quit_adoptiq.js (main app) but adapted for
         * the admin context:
         *   * POSTs to /admin_quit (admin proxy) instead of
         *     /api/shutdown -- the proxy handles the X-AdoptIQ-Internal
         *     header attachment and the main app does the rest.
         *   * Uses the admin CSRF header X-AdoptIQ-Admin-CSRF instead
         *     of the Flask-WTF token (admin uses a separate session-
         *     scoped token; see _admin_csrf_token).
         *   * No Bootstrap modal in the admin template; on a 409
         *     "needs_force" response we fall back to window.confirm so
         *     we don't have to ship a modal framework.
         *   * The post-shutdown overlay is the same idea -- swap the
         *     page so the user knows the local server is down.
         */
        (function () {
            var QUIT_URL = '/admin_quit';
            var BTN_ID = 'adoptiq-quit-btn';
            var OVERLAY_ID = 'adoptiq-shutdown-overlay';
            var ADMIN_CSRF = '{{ admin_csrf_token }}';

            function disableButton(btn) {
                if (!btn) { return; }
                btn.disabled = true;
                btn.setAttribute('aria-busy', 'true');
            }
            function enableButton(btn) {
                if (!btn) { return; }
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
            }
            function showOverlay() {
                var overlay = document.getElementById(OVERLAY_ID);
                if (!overlay) { return; }
                overlay.hidden = false;
                try { overlay.focus(); } catch (_) { /* ignore */ }
            }
            function buildRunningSummary(payload) {
                var entries = (payload && payload.in_progress) ? payload.in_progress : [];
                var lines = [];
                for (var i = 0; i < entries.length; i++) {
                    var e = entries[i] || {};
                    var line = (e.type || 'analysis') + ' \u2014 ' + (e.manager || '\u2014');
                    if (typeof e.progress === 'number' && e.progress > 0) {
                        line += ' (' + e.progress + '%)';
                    }
                    lines.push(line);
                }
                var count = (payload && typeof payload.in_progress_count === 'number')
                    ? payload.in_progress_count
                    : entries.length;
                var summary = (count === 1)
                    ? 'One analysis is still running:'
                    : (count + ' analyses are still running:');
                if (lines.length) {
                    summary += '\n  - ' + lines.join('\n  - ');
                }
                summary += '\n\nForce quit anyway?';
                return summary;
            }
            function sendShutdown(force, btn) {
                disableButton(btn);
                var headers = {
                    'X-AdoptIQ-Admin-CSRF': ADMIN_CSRF,
                    'X-Requested-With': 'XMLHttpRequest'
                };
                var formBody = force ? 'force=1' : '';
                if (formBody) {
                    headers['Content-Type'] = 'application/x-www-form-urlencoded';
                }
                window.fetch(QUIT_URL, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: headers,
                    body: formBody
                }).then(function (resp) {
                    return resp.json().catch(function () { return {}; }).then(function (payload) {
                        return { status: resp.status, payload: payload };
                    });
                }).then(function (result) {
                    if (result.status === 202 && result.payload && result.payload.ok) {
                        showOverlay();
                        return;
                    }
                    if (result.status === 409 && result.payload && result.payload.needs_force) {
                        enableButton(btn);
                        var msg = buildRunningSummary(result.payload);
                        if (window.confirm(msg)) {
                            sendShutdown(true, btn);
                        }
                        return;
                    }
                    enableButton(btn);
                    var err = (result.payload && result.payload.error)
                        ? result.payload.error
                        : ('Quit failed (HTTP ' + result.status + ').');
                    alert(err);
                }).catch(function (err) {
                    enableButton(btn);
                    alert('Quit failed: ' + ((err && err.message) ? err.message : 'network error'));
                });
            }
            function onQuitClick(ev) {
                ev.preventDefault();
                var btn = ev.currentTarget || document.getElementById(BTN_ID);
                var ok = window.confirm(
                    "Quit AdoptIQ?\n\n" +
                    "The local server will stop. Both the main app (5151) and this admin dashboard (5152) will go offline. You will need to reopen the .app to use AdoptIQ again."
                );
                if (!ok) { return; }
                sendShutdown(false, btn);
            }
            document.addEventListener('DOMContentLoaded', function () {
                var btn = document.getElementById(BTN_ID);
                if (btn) {
                    btn.addEventListener('click', onQuitClick);
                }
            });
        })();
    </script>

    {# Round 60: post-shutdown overlay.  Pre-rendered + hidden so the
       JS just toggles the ``hidden`` attribute -- no innerHTML
       construction at runtime, CSP-clean. #}
    <div id="adoptiq-shutdown-overlay" hidden role="alertdialog"
         aria-modal="true" aria-labelledby="adoptiq-shutdown-overlay-title">
        <h1 id="adoptiq-shutdown-overlay-title">AdoptIQ has shut down.</h1>
        <p>The local server has stopped and ports 5151 and 5152 are released. You can close this tab. To use AdoptIQ again, reopen the .app from your Applications folder.</p>
    </div>
</body>
</html>
"""

@admin_app.before_request
def log_request():
    """Log all requests with IP and user agent, and restrict admin to local access."""
    # Round 8 / Phase 4.6: trusting ``X-Forwarded-For`` blindly when
    # ``ADOPTIQ_TRUST_PROXY_HEADERS`` is set lets *any* upstream
    # client spoof their source IP simply by sending an XFF header,
    # because the admin app does not verify that the immediate peer
    # (``REMOTE_ADDR``) is actually a proxy we trust.  Require an
    # explicit comma-separated allowlist of trusted hop IPs in
    # ``ADOPTIQ_TRUSTED_PROXY_IPS``; only honour XFF when the
    # immediate peer is on that list.
    trust_proxy_headers = os.environ.get('ADOPTIQ_TRUST_PROXY_HEADERS', '').strip().lower() in {'1', 'true', 'yes'}
    _peer_ip = request.environ.get('REMOTE_ADDR', 'unknown')
    if trust_proxy_headers:
        _trusted = {
            p.strip() for p in (os.environ.get('ADOPTIQ_TRUSTED_PROXY_IPS', '') or '').split(',')
            if p.strip()
        }
        if _peer_ip in _trusted:
            xff = request.environ.get('HTTP_X_FORWARDED_FOR', '')
            # Walk the XFF chain right-to-left and pick the
            # right-most untrusted hop -- that is the canonical
            # client IP under the standard reverse-proxy contract.
            _hops = [h.strip() for h in xff.split(',') if h.strip()] if xff else []
            ip_address = _peer_ip
            for _hop in reversed(_hops):
                if _hop not in _trusted:
                    ip_address = _hop
                    break
        else:
            logger.warning(
                "[[SECURITY]] Ignoring XFF header from non-allowlisted peer=%s; "
                "set ADOPTIQ_TRUSTED_PROXY_IPS to enable.",
                _peer_ip,
            )
            ip_address = _peer_ip
    else:
        ip_address = _peer_ip
    user_agent = request.headers.get('User-Agent', 'unknown')
    endpoint = request.endpoint or 'unknown'

    if ip_address not in ('127.0.0.1', '::1', 'localhost'):
        return Response('Forbidden: admin is local access only', status=403)

    log_access(ip_address, user_agent, endpoint)


@admin_app.after_request
def _admin_no_store_for_api(response):
    """Round 8 / Phase 4.10: forbid intermediary / browser caching for
    every admin ``/api/*`` JSON response.

    These endpoints expose live operational data (running reports,
    error logs, security events, audit summaries, proxied debug
    state).  Without ``Cache-Control: no-store`` a stale copy could
    leak between admin sessions on a shared workstation, or be
    captured by an inadvertent caching proxy.  Belt-and-braces: we
    also send ``Pragma: no-cache`` for very old HTTP/1.0 caches and
    ``Expires: 0`` for legacy proxy software.
    """
    try:
        path = request.path or ''
        if path.startswith('/api/') or path.startswith('/audit_report/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    except Exception:
        # Never let a header-shaping failure block the response.
        pass
    return response


@admin_app.route('/')
def enhanced_admin_dashboard():
    """Enhanced admin dashboard with comprehensive monitoring"""
    server_status = get_server_status()
    system_info = get_system_info()
    report_history = get_report_history()
    ip_connections = get_ip_connections()
    error_logs = get_error_logs()
    audit_data = get_audit_history(limit=20)
    audit_summary = get_audit_summary()
    # Headline KPI tiles must always reflect SELECT COUNT(*) totals,
    # not the LIMIT 50 page rendered into the table below.
    totals = {
        'report_history': get_total_count('report_history'),
        'ip_connections': get_total_count('ip_connections'),
        'error_logs': get_total_count('error_logs'),
        'security_events': get_total_count('security_events'),
        'total_requests': get_total_request_count(),
    }

    # Extract the audits list from the dictionary
    audit_history = audit_data.get('audits', [])

    # Get currently running reports from main app (MAIN_APP_URL for container)
    # Round 2 / Phase 2.2: track failed-vs-zero state.  An unreachable
    # main app must render "n/a" in the tile, not "0 running" which is
    # also a valid steady-state.
    # Round 44 / Phase 8: route through ``_live_main_url()`` so the
    # fetch URL re-reads ``ADOPTIQ_MAIN_URL`` per request.  The
    # module-level ``MAIN_APP_URL`` constant is captured at import
    # time, but ``app_simple.py`` imports this module from L157-161
    # to pull the audit helpers BEFORE L210 sets the env -- so the
    # constant stamps the unset default and the fetch goes to the
    # wrong port, painting the dashboard's "Currently Running
    # Reports" tile red ("n/a -- main app unreachable") even when
    # the main app is up.
    running_reports = []
    running_reports_failed = False
    try:
        import requests
        response = requests.get(f'{_live_main_url()}/api/status/all', timeout=2)
        if response.status_code == 200:
            all_reports_payload = response.json()
            if isinstance(all_reports_payload, dict):
                all_reports = all_reports_payload.get('statuses') or []
            elif isinstance(all_reports_payload, list):
                all_reports = all_reports_payload
            else:
                all_reports = []
            running_reports = [r for r in all_reports if r.get('status') in ['running', 'starting']]
        else:
            running_reports_failed = True
    except Exception as _fetch_err:
        logger.debug(f"Could not fetch running reports from main app: {_fetch_err}")
        running_reports_failed = True

    verbose_debug = False
    snowflake_query_count = 0
    snowflake_query_count_failed = False
    try:
        # Round 44 / Phase 8: same live-env re-read as the
        # running-reports fetch above.  Both endpoints live on the
        # main app; if one is reachable the other is too.
        debug_resp = requests.get(f'{_live_main_url()}/api/debug/verbose', timeout=2)
        if debug_resp.status_code == 200:
            debug_data = debug_resp.json()
            verbose_debug = bool(debug_data.get('verbose_debug'))
            snowflake_query_count = int(debug_data.get('snowflake_query_count', 0) or 0)
        else:
            snowflake_query_count_failed = True
    except Exception as _debug_err:
        logger.debug("Could not fetch verbose debug state from main app: %s", _debug_err)

    # Round 12 / Phase 5.3: project the canonical risk-band palette
    # into the admin template so the inline ``.risk-high``,
    # ``.risk-medium`` and ``.risk-low`` traffic-light classes share
    # the same hexes as the Word/Excel exports' canonical
    # ``RISK_BAND_COLORS`` map.  Falls back to the canonical defaults
    # if ``canonical_metrics`` cannot be imported (degraded test env).
    try:
        from canonical_metrics import (
            RISK_BAND_COLORS as _R12_ADMIN_RBC,
            RISK_BAND_COLOR_DEFAULT as _R12_ADMIN_RBC_DEFAULT,
        )
    except Exception:  # pragma: no cover - defensive
        _R12_ADMIN_RBC = {
            'CRITICAL': '#d62728',
            'HIGH': '#ff7f0e',
            'MEDIUM': '#ffd700',
            'LOW': '#2ca02c',
            'HEALTHY': '#28B463',
        }
        _R12_ADMIN_RBC_DEFAULT = '#1f77b4'
    admin_risk_color_high = _R12_ADMIN_RBC.get('HIGH', _R12_ADMIN_RBC_DEFAULT)
    admin_risk_color_medium = _R12_ADMIN_RBC.get('MEDIUM', _R12_ADMIN_RBC_DEFAULT)
    admin_risk_color_low = _R12_ADMIN_RBC.get('LOW', _R12_ADMIN_RBC_DEFAULT)

    # Round 17 / Phase D.5: Corpus status fetched from the main app.
    # The admin app is a separate Flask process; we deliberately do
    # NOT import ``corpus_retriever`` here (it would race the main
    # app's connection lifecycle).  Instead we reach across to the
    # ``/api/corpus/status`` JSON endpoint we added in app_simple.py.
    corpus_status = {
        "ok": False,
        "enabled": False,
        "available": False,
        "reason": "main app unreachable",
        "boot": {
            "completed": False,
            "in_progress": False,
            "last_started_at": None,
            "last_finished_at": None,
            "last_error": None,
            "last_error_kind": None,
            "encrypted_path": None,
            "onedrive_root": None,
            # Round 36 / Phase 1: OneDrive sync presence is the
            # auth signal for the Knowledge Corpus.  ``synced`` /
            # ``not_synced`` / ``unknown`` is computed by
            # ``corpus_bootstrap._check_onedrive_sync_status`` and
            # surfaced via ``/api/corpus/status``; the admin tile
            # renders it as a colored pill.
            "onedrive_status": None,
            "onedrive_file_count": None,
            "embedder_status": None,
            "embedder_load_error": None,
            "dense_retrieval_status": None,
            "dense_vectors_upserted": None,
            "dense_vectors_considered": None,
            "dense_vector_error": None,
            "ask_ai_retrieval_method": None,
            "last_stats": None,
            # Round 17.1: per-source breakdown rendered in the
            # Corpus tile.  ``None`` means the bootstrap has not
            # recorded source-level stats yet.
            "last_sources": None,
            # Round 17.2 / retired in Round 36: SharePoint pull
            # state.  Always ``None`` after Round 36 stripped MSAL;
            # the key is preserved for back-compat with old admin
            # builds talking to a new main app (or vice versa).
            "sharepoint": None,
        },
        "corpus": {
            "files_total": 0,
            "files_parsed": 0,
            "customers": 0,
            "cases": 0,
            "chunks": 0,
            "schema_version": 0,
            "last_parsed_at": None,
            "indexed_at": None,
        },
    }
    corpus_status_failed = True
    try:
        _r17_corpus_resp = requests.get(
            f'{_live_main_url()}/api/corpus/status',
            timeout=2,
        )
        if _r17_corpus_resp.status_code == 200:
            _r17_data = _r17_corpus_resp.json() or {}
            if isinstance(_r17_data, dict):
                # Merge over defaults so a partial response still
                # renders.
                corpus_status.update({
                    k: v for k, v in _r17_data.items()
                    if k in corpus_status
                })
                corpus_status_failed = False
    except Exception as _r17_err:  # noqa: BLE001 - tile must always render
        logger.debug("Round 17 / corpus tile fetch failed: %s", _r17_err)

    return render_template_string(ENHANCED_ADMIN_TEMPLATE_V2,
                                server_status=server_status,
                                system_info=system_info,
                                report_history=report_history,
                                ip_connections=ip_connections,
                                error_logs=error_logs,
                                totals=totals,
                                audit_history=audit_history,
                                audit_summary=audit_summary,
                                running_reports=running_reports,
                                running_reports_failed=running_reports_failed,
                                # Round 80: route through ``_live_main_url()`` so the
                                # rendered "Back to AdoptIQ" anchor href reflects the
                                # live ``ADOPTIQ_MAIN_URL`` env value, not the stale
                                # module-level constant captured at import time.
                                # Pre-R80 the .app running on a non-default port would
                                # render the link pointing at ``http://localhost:5151``
                                # (the import-time default) instead of the live port,
                                # which is the bug Brian Frazier reported. The R44 /
                                # Phase 8 helper already does the per-call env re-read
                                # for HTTP fetches; this extends the same pattern to
                                # the template-render path. Pinned by
                                # tests/test_round80_admin_back_link_uses_live_url.py.
                                main_app_url=_live_main_url(),
                                verbose_debug=verbose_debug,
                                snowflake_query_count=snowflake_query_count,
                                snowflake_query_count_failed=snowflake_query_count_failed,
                                admin_risk_color_high=admin_risk_color_high,
                                admin_risk_color_medium=admin_risk_color_medium,
                                admin_risk_color_low=admin_risk_color_low,
                                corpus_status=corpus_status,
                                corpus_status_failed=corpus_status_failed)

# Round 54 / F3 -- shared blocked-state probe used by the corpus
# refresh + reset proxies. Round 108 / Corpus Smoothness turns
# OneDrive/corpus-share state into optional refresh context, so this
# helper intentionally no-ops for back-compat with older call sites.
# Pinned by ``tests/test_round54_f3_admin_reset_gate.py``.
def _r54_corpus_is_blocked_no_onedrive() -> bool:
    """Round 108: legacy OneDrive blocker is optional refresh context."""
    return False


@admin_app.route('/corpus_refresh', methods=['POST'])
def corpus_refresh_route():
    """Round 17 / Phase D.5 -- proxy a CSRF-protected refresh request
    to the main app's ``/api/corpus/refresh`` endpoint.

    Strategy: validate the admin CSRF token first (so a hostile
    cross-origin POST cannot trigger a rebuild), then make a
    server-to-server HTTP call to the main app.  We pass ``rebuild``
    only when the form field is set so the default action is the
    cheaper incremental refresh.

    Round 108 / Corpus Smoothness: OneDrive/corpus-share state is
    optional refresh context. The route keeps proxying so local
    generated reports and uploads can refresh the corpus without
    OneDrive being present.
    """
    _require_admin_csrf()
    if _r54_corpus_is_blocked_no_onedrive():
        return redirect(url_for(
            'enhanced_admin_dashboard',
            message=(
                'Corpus refresh: optional OneDrive source missing; '
                'local refresh remains available.'
            ),
            message_type='warning',
        ))
    rebuild = '1' if (request.form.get('rebuild') == '1') else ''
    refresh_status = 'unknown'
    try:
        # We do NOT have the main-app CSRF token here, so we use the
        # server-to-server header-based auth path: most main-app
        # endpoints that accept ``X-CSRFToken`` will reject the
        # request.  The cleanest path is to pass through the admin
        # token as a header the main app validates separately.  For
        # local-only admin posture we instead mark this endpoint as
        # internal-only (admin app already binds 127.0.0.1) and
        # configure the main app to accept refresh requests from a
        # peer ``ADOPTIQ_INTERNAL_TOKEN``.  Fall back to a no-op
        # status if the token is unset.
        import requests as _r17_req
        headers = {}
        _internal_tok = os.environ.get('ADOPTIQ_INTERNAL_TOKEN')
        if _internal_tok:
            headers['X-AdoptIQ-Internal'] = _internal_tok
        body = {'rebuild': rebuild} if rebuild else {}
        resp = _r17_req.post(
            f'{_live_main_url()}/api/corpus/refresh',
            data=body,
            headers=headers,
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            if data.get('refresh_started'):
                refresh_status = 'started'
            elif data.get('refresh_error'):
                refresh_status = data.get('refresh_error')
            else:
                refresh_status = 'no-op'
        elif resp.status_code == 403:
            refresh_status = 'CSRF/auth rejected by main app'
        else:
            refresh_status = f'main app HTTP {resp.status_code}'
    except Exception as err:
        log_error(
            'WARNING',
            f'Round 17 corpus_refresh proxy failed: {type(err).__name__}',
            'corpus_refresh_route',
        )
        refresh_status = 'unreachable'
    return redirect(url_for(
        'enhanced_admin_dashboard',
        message=f'Corpus refresh: {refresh_status}',
        message_type=('success' if refresh_status == 'started' else 'warning'),
    ))


# Round 37 / Phase 3: ``/sharepoint_signin`` and ``/sharepoint_refresh``
# admin proxy routes were removed.  Their main-app upstreams
# (``/api/corpus/sharepoint/signin|signout|refresh``) were deleted in
# Round 36 when the MSAL/Graph runtime path went away, so calling
# these proxy routes would just produce a 404 from MAIN_APP_URL.
# OneDrive sync presence is now the corpus auth signal, and
# ``/corpus_refresh`` (kept) is the only manual-refresh contract the
# admin needs.


@admin_app.route('/corpus_reset', methods=['POST'])
def corpus_reset_route():
    """Round 39 / corpus crypto self-heal -- proxy a CSRF-protected
    Reset Corpus request to the main app's ``/api/corpus/reset``
    endpoint.  Mirrors the auth + transport pattern of
    :func:`corpus_refresh_route`.

    Strategy: validate the admin CSRF token first, then make a
    server-to-server HTTP call with the ``X-AdoptIQ-Internal`` header
    so the main app can authorize without us holding its CSRF token.

    Round 108 / Corpus Smoothness: OneDrive/corpus-share status is not
    a reset blocker. Reset rebuilds from generated reports, uploads, and
    OneDrive when available.
    """
    _require_admin_csrf()
    if _r54_corpus_is_blocked_no_onedrive():
        return redirect(url_for(
            'enhanced_admin_dashboard',
            message=(
                'Corpus reset: optional OneDrive source missing; '
                'local reset remains available.'
            ),
            message_type='warning',
        ))
    reset_status = 'unknown'
    try:
        import requests as _r39_req
        headers = {}
        _internal_tok = os.environ.get('ADOPTIQ_INTERNAL_TOKEN')
        if _internal_tok:
            headers['X-AdoptIQ-Internal'] = _internal_tok
        resp = _r39_req.post(
            f'{_live_main_url()}/api/corpus/reset',
            data={},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            if data.get('ok') and data.get('refresh_started'):
                reset_status = 'reset+refresh started'
            elif data.get('ok'):
                reset_status = 'reset (refresh did not start)'
            else:
                reset_status = data.get('reason') or 'reset failed'
        elif resp.status_code == 403:
            reset_status = 'CSRF/auth rejected by main app'
        else:
            reset_status = f'main app HTTP {resp.status_code}'
    except Exception as err:
        log_error(
            'WARNING',
            f'Round 39 corpus_reset proxy failed: {type(err).__name__}',
            'corpus_reset_route',
        )
        reset_status = 'unreachable'
    return redirect(url_for(
        'enhanced_admin_dashboard',
        message=f'Corpus reset: {reset_status}',
        message_type=('success' if 'started' in reset_status else 'warning'),
    ))


# ---------------------------------------------------------------------------
# Round 60: admin-side proxy for the user-facing Quit / shutdown action.
#
# The admin Flask app runs as a daemon thread INSIDE the main Flask
# process (see ``app_simple._start_admin_server_in_thread``), so a
# clean shutdown is the same physical syscall on both sides -- one
# ``signal.SIGTERM`` to PID os.getpid() releases both 5151 and 5152.
# This proxy lets the admin's "Quit AdoptIQ" button invoke the same
# main-app ``/api/shutdown`` endpoint without copying its
# in-progress-detection logic.
#
# Returns JSON (NOT a redirect) because:
#   * the admin's page is about to die too -- a 302 would race the
#     SIGTERM and either fire before the response flushes (browser
#     sees the redirect target on a dead port) or never fire at all;
#   * the JS handler wants the same payload shape as the main-app
#     ``/api/shutdown`` so the 409 ``needs_force`` modal works
#     identically from both navbars.
# ---------------------------------------------------------------------------


@admin_app.route('/admin_quit', methods=['POST'])
def admin_quit_route():
    """Round 60: proxy a CSRF-protected Quit request to the main app's
    ``/api/shutdown`` endpoint, mirroring the auth + transport pattern
    of :func:`corpus_refresh_route` / :func:`corpus_reset_route`.

    Auth: validate the admin CSRF token first (so a hostile cross-
    origin POST cannot kill the local process), then make a server-
    to-server HTTP call with the ``X-AdoptIQ-Internal`` header so the
    main app can authorize without us holding its CSRF token.

    Returns the main app's JSON response verbatim so the calling
    JavaScript can branch on 202 (success) vs 409 (needs_force) the
    same way it does when called directly from the main navbar.
    """
    _require_admin_csrf()
    force_form = (request.form.get('force') or '').strip()
    try:
        import requests as _r60_req
        headers = {}
        _internal_tok = os.environ.get('ADOPTIQ_INTERNAL_TOKEN')
        if _internal_tok:
            headers['X-AdoptIQ-Internal'] = _internal_tok
        body = {'force': force_form} if force_form else {}
        resp = _r60_req.post(
            f'{_live_main_url()}/api/shutdown',
            data=body,
            headers=headers,
            timeout=5,
        )
        # Pass through the main app's JSON + status so the browser
        # gets the canonical {ok, shutdown_in_ms, needs_force, ...}
        # contract regardless of which navbar the user clicked from.
        try:
            payload = resp.json()
        except Exception:
            payload = {'ok': False, 'error': f'main app returned non-JSON HTTP {resp.status_code}'}
        return jsonify(payload), resp.status_code
    except Exception as err:
        log_error(
            'WARNING',
            f'Round 60 admin_quit proxy failed: {type(err).__name__}',
            'admin_quit_route',
        )
        return jsonify({
            'ok': False,
            'error': 'main app unreachable',
            'detail': type(err).__name__,
        }), 502


# ---------------------------------------------------------------------------
# Round 69 / Build 43: admin-side proxies for the operator-flippable
# CircuIT model preferences.  Mirrors the auth + transport pattern of
# :func:`corpus_refresh_route` / :func:`corpus_reset_route` /
# :func:`admin_quit_route`: validate the admin CSRF token first, then
# server-to-server HTTP call to the main app with the
# ``X-AdoptIQ-Internal`` header so the main app can authorize without
# us holding its CSRF token.  Returns the main app's JSON response
# verbatim so the admin's JS can branch on success/failure the same
# way the analyze-page panel does.
# ---------------------------------------------------------------------------


def _r69_admin_proxy_post(upstream_path: str, body: Dict[str, Any], timeout: int = 12) -> Tuple[Any, int]:
    """Round 69 / Build 43: shared HTTP proxy helper for the model
    preference + ping admin routes.  ``timeout`` defaults to 12s so it
    sits comfortably above the main app's own 10s ping budget.
    """
    try:
        import requests as _r69_req
        headers = {'Content-Type': 'application/json'}
        _internal_tok = os.environ.get('ADOPTIQ_INTERNAL_TOKEN')
        if _internal_tok:
            headers['X-AdoptIQ-Internal'] = _internal_tok
        resp = _r69_req.post(
            f'{_live_main_url()}{upstream_path}',
            json=body or {},
            headers=headers,
            timeout=timeout,
        )
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {'ok': False, 'error': f'main app returned non-JSON HTTP {resp.status_code}'}
        return payload, resp.status_code
    except Exception as err:  # noqa: BLE001
        log_error(
            'WARNING',
            f'Round 69 admin proxy {upstream_path} failed: {type(err).__name__}',
            '_r69_admin_proxy_post',
        )
        return ({'ok': False, 'error': 'main app unreachable', 'detail': type(err).__name__}, 502)


@admin_app.route('/admin_settings/ask_ai_model', methods=['POST'])
def admin_settings_ask_ai_model_route():
    """Round 69 / Build 43: persist the Ask AI model preference via the
    main app's ``POST /api/settings/ask-ai-model``."""
    _require_admin_csrf()
    raw_value = request.form.get('model_name', '') or ''
    payload, status_code = _r69_admin_proxy_post(
        '/api/settings/ask-ai-model',
        {'model_name': str(raw_value).strip()},
    )
    return jsonify(payload), status_code


@admin_app.route('/admin_settings/report_model', methods=['POST'])
def admin_settings_report_model_route():
    """Round 69 / Build 43: persist the report-narrative model
    preference via the main app's ``POST /api/settings/report-model``."""
    _require_admin_csrf()
    raw_value = request.form.get('model_name', '') or ''
    payload, status_code = _r69_admin_proxy_post(
        '/api/settings/report-model',
        {'model_name': str(raw_value).strip()},
    )
    return jsonify(payload), status_code


@admin_app.route('/admin_settings/llm_ping', methods=['POST'])
def admin_settings_llm_ping_route():
    """Round 69 / Build 43: ping a CircuIT model via the main app's
    ``POST /api/llm/ping``.  Used by the admin "Test" button so the
    operator cannot save a typo or an unprovisioned model."""
    _require_admin_csrf()
    raw_value = request.form.get('model_name', '') or ''
    payload, status_code = _r69_admin_proxy_post(
        '/api/llm/ping',
        {'model_name': str(raw_value).strip()},
    )
    return jsonify(payload), status_code


@admin_app.route('/start_server', methods=['POST'])
def start_server_route():
    """Start the AdoptIQ server"""
    # Round 5 / Phase 2.3: require POST + admin CSRF token.
    _require_admin_csrf()
    result = start_server()

    if result['success']:
        return redirect(url_for('enhanced_admin_dashboard', message='Server started successfully!', message_type='success'))
    else:
        log_error('WARNING', f'Server start failed: {result.get("error", "unknown")}', 'start_server_route')
        return redirect(url_for('enhanced_admin_dashboard', message='Failed to start server. Check logs for details.', message_type='danger'))

@admin_app.route('/stop_server', methods=['POST'])
def stop_server_route():
    """Stop the AdoptIQ server"""
    # Round 5 / Phase 2.3: require POST + admin CSRF token.
    _require_admin_csrf()
    result = stop_server()

    if result['success']:
        return redirect(url_for('enhanced_admin_dashboard', message='Server stopped successfully!', message_type='success'))
    else:
        log_error('WARNING', f'Server stop failed: {result.get("error", "unknown")}', 'stop_server_route')
        return redirect(url_for('enhanced_admin_dashboard', message='Failed to stop server. Check logs for details.', message_type='danger'))

@admin_app.route('/export_logs', methods=['POST'])
def export_logs():
    """Export all logs to JSON"""
    # Round 5 / Phase 2.3: require POST + admin CSRF token.  Exporting
    # the full audit trail off-host is destructive in the sense that it
    # exfiltrates sensitive data; same protection as the other routes.
    _require_admin_csrf()
    try:
        # Round 11 / Phase 10.1: previous code wrote a naive local
        # ``datetime.now().isoformat()`` so two operators in
        # different time zones would see different "export_timestamp"
        # values for the same audit pull, and the embedded filename
        # date could disagree with the JSON payload's date across UTC
        # midnight.  Stamp UTC explicitly for both.
        _export_now_utc = datetime.now(timezone.utc)
        logs_data = {
            'report_history': get_report_history(),
            'ip_connections': get_ip_connections(),
            'error_logs': get_error_logs(),
            'security_events': get_security_events(),
            'system_metrics': list(monitoring_data['system_metrics']),
            'export_timestamp': _export_now_utc.isoformat(),
            'export_timestamp_tz': 'UTC',
        }

        export_dir = os.path.join(tempfile.gettempdir(), 'adoptiq_exports')
        os.makedirs(export_dir, exist_ok=True)
        export_file = os.path.join(
            export_dir,
            f"admin_logs_export_{_export_now_utc.strftime('%Y%m%dT%H%M%SZ')}.json",
        )
        with open(export_file, 'w', encoding='utf-8') as f:
            json.dump(logs_data, f, indent=2)

        return redirect(url_for('enhanced_admin_dashboard', message=f'Logs exported to {export_file}', message_type='success'))

    except Exception as e:
        log_error('ERROR', f'Log export failed: {e}', 'export_logs')
        return redirect(url_for('enhanced_admin_dashboard', message='Export failed. Check logs for details.', message_type='danger'))

@admin_app.route('/clear_logs', methods=['POST'])
def clear_logs():
    """Clear all logs"""
    # Round 5 / Phase 2.3: require POST + admin CSRF token.
    _require_admin_csrf()
    try:
        with db_connection() as conn:
            cursor = conn.cursor()

            # Clear all tables
            cursor.execute('DELETE FROM report_history')
            cursor.execute('DELETE FROM ip_connections')
            cursor.execute('DELETE FROM security_events')
            cursor.execute('DELETE FROM performance_metrics')
            cursor.execute('DELETE FROM error_logs')

        # Clear in-memory data
        monitoring_data['active_reports'].clear()
        monitoring_data['completed_reports'].clear()
        monitoring_data['ip_connections'].clear()
        monitoring_data['system_metrics'].clear()
        monitoring_data['error_logs'].clear()
        monitoring_data['access_logs'].clear()
        monitoring_data['security_events'].clear()
        monitoring_data['performance_metrics'].clear()

        log_error('INFO', 'All logs cleared', 'clear_logs')

        return redirect(url_for('enhanced_admin_dashboard', message='All logs cleared successfully!', message_type='success'))

    except Exception as e:
        log_error('ERROR', f'Log clear failed: {e}', 'clear_logs')
        return redirect(url_for('enhanced_admin_dashboard', message='Clear failed. Check logs for details.', message_type='danger'))

@admin_app.route('/api/reports')
def api_reports():
    """API endpoint for report history"""
    return jsonify(get_report_history())

@admin_app.route('/api/connections')
def api_connections():
    """API endpoint for IP connections"""
    return jsonify(get_ip_connections())

@admin_app.route('/api/errors')
def api_errors():
    """API endpoint for error logs"""
    return jsonify(get_error_logs())

@admin_app.route('/api/analytics')
def api_analytics():
    """API endpoint for analytics data"""
    return jsonify(get_analytics())

@admin_app.route('/api/security')
def api_security():
    """API endpoint for security events"""
    return jsonify(get_security_events())

@admin_app.route('/api/audits')
def api_audits():
    """Get audit history"""
    return jsonify(get_audit_history())

@admin_app.route('/audit_report/<analysis_id>', methods=['POST'])
def audit_report_route(analysis_id):
    """Trigger audit for a specific report.

    Round 8 / Phase 4.8: this endpoint kicks off the per-report audit
    pipeline (which scans files on disk, calls back into the main
    app, and writes audit history records).  Triggering an audit
    silently from a GET means CSRF (an attacker-crafted ``<a href>``
    or ``<img src>`` on a third-party page that an authenticated
    admin happens to load) can DoS the audit history store.
    Restrict to POST + admin CSRF token so a destructive trigger is
    only possible from inside the admin dashboard's own forms.
    """
    _require_admin_csrf()
    if not _is_valid_analysis_id(analysis_id):
        abort(400)
    result = audit_report(analysis_id)
    return jsonify(result)

@admin_app.route('/api/audit_summary')
def api_audit_summary():
    """Get audit summary statistics"""
    return jsonify(get_audit_summary())


@admin_app.route('/api/debug/verbose', methods=['GET', 'POST'])
def api_debug_verbose():
    """Proxy verbose debug state/toggle to the main app."""
    # Round 8 / Phase 4.7: require the admin CSRF token for the
    # POST (state-changing) path.  Verbose debug toggles control
    # how much sensitive diagnostic data the main app emits, so
    # an attacker who could trick a logged-in admin's browser
    # into POSTing here could silently turn on full request
    # logging.  GET (read-only) remains unauthenticated for the
    # dashboard tile.
    if request.method == 'POST':
        _require_admin_csrf()
    main_url = f'{_live_main_url()}/api/debug/verbose'
    try:
        if request.method == 'GET':
            resp = requests.get(main_url, timeout=3)
            return jsonify(resp.json()), resp.status_code
        # Round 8 / Phase 4.9: instead of forwarding whatever the
        # browser submits, project the inbound JSON body to a
        # fixed allowlist of keys with explicit type coercions.
        # This prevents the proxy from being abused to smuggle
        # additional fields into the main app's debug API (e.g.
        # if a future main-app version recognises new dangerous
        # toggles like ``log_secrets`` or ``dump_env``, those
        # cannot be silently forwarded through this proxy).
        raw_payload = request.get_json(silent=True) or {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        _ALLOWED_DEBUG_KEYS = {'enabled', 'reset_query_metrics'}
        projected_payload: dict = {}
        if 'enabled' in raw_payload:
            projected_payload['enabled'] = bool(raw_payload.get('enabled'))
        if 'reset_query_metrics' in raw_payload:
            projected_payload['reset_query_metrics'] = bool(raw_payload.get('reset_query_metrics'))
        # Defensive: log (at DEBUG only) any unexpected keys we dropped
        # so operators can spot misconfigured callers without leaking
        # the values.
        _dropped = sorted(set(raw_payload.keys()) - _ALLOWED_DEBUG_KEYS)
        if _dropped:
            logger.debug("Debug verbose proxy dropped unexpected keys: %s", _dropped)
        resp = requests.post(main_url, json=projected_payload, timeout=3)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error("Verbose debug proxy failed: %s", e)
        return jsonify({'success': False, 'error': 'Unable to reach main app debug endpoint'}), 502


@admin_app.route('/api/diag/connectivity', methods=['GET'])
def api_diag_connectivity_proxy():
    """Proxy the Keeper / Snowflake connectivity self-test to the main app.

    Round 5 hotfix. Support can run the self-test from the admin dashboard
    without needing to hit the main app URL directly. Uses a longer timeout
    (45s) because the full chain can legitimately take ~25s when Snowflake
    login is slow.
    """
    main_url = f'{_live_main_url()}/api/diag/connectivity'
    try:
        resp = requests.get(main_url, timeout=45)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error("Connectivity diag proxy failed: %s", e)
        # Round 13 / Phase 10.5: previously this returned the raw
        # exception message in ``detail``, which on requests errors
        # (e.g. ``ConnectionError``) leaks the proxied target URL,
        # internal hostnames, port numbers, and sometimes the path
        # to the local socket file -- all of which the admin tile is
        # not authorized to expose to the browser.  Redact down to a
        # generic kind label so an operator running with logs has
        # full context (logger.error above) but the browser never
        # sees the underlying URL/hostname.
        try:
            _r13_kind = type(e).__name__
        except Exception:
            _r13_kind = 'unknown_error'
        _r13_kind_to_msg = {
            'ConnectionError': 'connection refused or unreachable',
            'ConnectTimeout': 'connection timed out',
            'ReadTimeout': 'read timed out',
            'Timeout': 'request timed out',
            'SSLError': 'TLS handshake failed',
            'ProxyError': 'proxy error',
            'TooManyRedirects': 'redirect loop',
        }
        _r13_user_msg = _r13_kind_to_msg.get(_r13_kind, 'connectivity check failed')
        return jsonify({
            'ok': False,
            'error': 'Unable to reach main app /api/diag/connectivity',
            'error_kind': _r13_kind,
            'detail': _r13_user_msg,
        }), 502

_ANALYSIS_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,200}$')


def _is_valid_analysis_id(value: str) -> bool:
    """Strict validation to prevent abusive wildcard scans and malformed IDs."""
    return isinstance(value, str) and bool(_ANALYSIS_ID_RE.fullmatch(value))


def _r139_artifact_under_allowed_root(path_str: str) -> tuple[bool, str]:
    """Round 139 / Build 109: verify artifact path resolves under allowed output roots."""
    if not path_str or not str(path_str).strip():
        return False, "empty_path"
    try:
        from report_output_paths import candidate_output_roots

        target = Path(path_str).expanduser().resolve()
        if not target.is_file():
            return False, "file_missing"
        for root in candidate_output_roots(create_current=False):
            try:
                root_res = root.expanduser().resolve()
                if target == root_res or root_res in target.parents:
                    return True, "ok"
            except Exception:
                continue
        return False, "outside_allowed_roots"
    except Exception as exc:  # noqa: BLE001
        return False, f"validate_error:{type(exc).__name__}"


def audit_report(analysis_id):
    """
    Audit a generated report for accuracy, completeness, and fact-checking
    Verifies:
    - Report file exists and is complete
    - Data sources were properly consulted
    - References and citations are valid
    - BEMS escalations are properly flagged
    - Facts match source data
    """
    logger.info(f"Starting audit for analysis: {analysis_id}")

    audit_result = {
        'analysis_id': analysis_id,
        'audit_timestamp': _r12_admin_utc_iso_z(),
        'status': 'pending',
        'checks': [],
        'score': 0,
        'max_score': 70  # Round 139: implemented checks only (no stub data-source/citation points)
    }

    try:
        if not _is_valid_analysis_id(analysis_id):
            audit_result['status'] = 'error'
            audit_result['error'] = 'Invalid analysis_id format'
            return audit_result

        # Ensure database and tables exist (report_history may not exist when app_simple runs standalone)
        init_database()

        # Get report details from database (if populated)
        report_data = None
        try:
            with db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT report_type, manager, technology, customer_name, status,
                           start_time, end_time, ip_address,
                           word_path, excel_path, word_hash, excel_hash
                    FROM report_history
                    WHERE request_id = ?
                    ORDER BY created_at DESC LIMIT 1
                ''', (analysis_id,))
                report_data = cursor.fetchone()
        except sqlite3.OperationalError as _r13_db_err:
            # Round 13 / Phase 10.4: previously this swallowed the
            # ``OperationalError`` silently with ``pass`` and the
            # caller had no signal that the audit_report DB leg had
            # been skipped (e.g. report_history table missing,
            # database file corrupt).  The audit then quietly
            # downgraded to file-only mode without surfacing any
            # warning, so an operator running ``audit_report`` on a
            # broken DB saw a green "passed" report despite half the
            # checks being skipped.  Log the skip + surface it as a
            # ``checks`` entry so the audit envelope discloses that
            # the DB leg ran in degraded mode.
            try:
                logger.warning(
                    "audit_report: report_history DB leg skipped (analysis_id=%s): %s",
                    analysis_id, _r13_db_err,
                )
            except Exception:
                pass
            try:
                audit_result.setdefault('checks', []).append({
                    'check': 'report_history_db_leg',
                    'status': 'skipped',
                    'reason': 'sqlite_operational_error',
                    'detail': str(_r13_db_err)[:240],
                })
            except Exception:
                pass

        # Round 139 / Build 109: prefer persisted artifact paths from report_history.
        word_path = ''
        excel_path = ''
        word_hash = ''
        excel_hash = ''
        if report_data and len(report_data) >= 12:
            word_path = report_data[8] or ''
            excel_path = report_data[9] or ''
            word_hash = report_data[10] or ''
            excel_hash = report_data[11] or ''

        validated_artifacts: list[Path] = []
        for label, path_str, expected_hash in (
            ("word", word_path, word_hash),
            ("excel", excel_path, excel_hash),
        ):
            if not path_str:
                continue
            ok, reason = _r139_artifact_under_allowed_root(path_str)
            if ok:
                validated_artifacts.append(Path(path_str))
                import hashlib as _r139_hashlib
                try:
                    _h = _r139_hashlib.sha256()
                    with open(path_str, "rb") as _fh:
                        for _chunk in iter(lambda: _fh.read(65536), b""):
                            _h.update(_chunk)
                    live_hash = _h.hexdigest()
                    if expected_hash and live_hash != expected_hash:
                        audit_result['checks'].append({
                            'check': f'artifact_hash_{label}',
                            'status': 'fail',
                            'score': 0,
                            'message': f'{label} hash mismatch (tamper or stale path)',
                        })
                    else:
                        audit_result['checks'].append({
                            'check': f'artifact_hash_{label}',
                            'status': 'pass',
                            'score': 5,
                            'message': f'{label} artifact hash verified',
                        })
                        audit_result['score'] += 5
                except OSError as hash_err:
                    audit_result['checks'].append({
                        'check': f'artifact_hash_{label}',
                        'status': 'fail',
                        'score': 0,
                        'message': f'Could not hash {label} artifact: {hash_err}',
                    })
            else:
                audit_result['checks'].append({
                    'check': f'artifact_path_{label}',
                    'status': 'fail',
                    'score': 0,
                    'message': f'{label} artifact invalid: {reason}',
                })

        # Check 1: Verify report file exists (search outputs/ and Reports/)
        output_dirs = [Path('outputs'), Path('Reports')]
        if os.environ.get('APPDATA'):
            output_dirs.append(Path(os.environ['APPDATA']) / 'AdoptIQ' / 'outputs')
        report_files = []
        for d in output_dirs:
            if d.exists():
                report_files.extend(list(d.glob(f"*{analysis_id}*")))
        # Avoid duplicate files when multiple directories point to same location
        report_files = list({str(p.resolve()): p for p in report_files}.values())

        if validated_artifacts or report_files:
            _found_count = len(validated_artifacts) or len(report_files)
            audit_result['checks'].append({
                'check': 'file_exists',
                'status': 'pass',
                'score': 15,
                'message': f'Found {_found_count} report file(s)'
            })
            audit_result['score'] += 15
        else:
            audit_result['checks'].append({
                'check': 'file_exists',
                'status': 'fail',
                'score': 0,
                'message': 'No report files found'
            })

        # Check 2: Verify report completion (from DB or inferred from file existence)
        if report_data and report_data[4] == 'completed':
            audit_result['checks'].append({
                'check': 'completion_status',
                'status': 'pass',
                'score': 15,
                'message': 'Report completed successfully'
            })
            audit_result['score'] += 15
        elif report_files or validated_artifacts:
            audit_result['checks'].append({
                'check': 'completion_status',
                'status': 'pass',
                'score': 15,
                'message': 'Report files found (completion inferred from output)'
            })
            audit_result['score'] += 15
        else:
            audit_result['checks'].append({
                'check': 'completion_status',
                'status': 'fail',
                'score': 0,
                'message': f'Report status: {report_data[4] if report_data else "unknown"}'
            })

        # Check 3: Verify generation time is reasonable (only when report_history has data)
        if report_data and report_data[5] and report_data[6]:
            try:
                start_str = str(report_data[5]).replace('Z', '+00:00')
                end_str = str(report_data[6]).replace('Z', '+00:00')
                start = datetime.fromisoformat(start_str)
                end = datetime.fromisoformat(end_str)
                duration = (end - start).total_seconds()

                if 10 < duration < 600:  # Between 10 seconds and 10 minutes
                    audit_result['checks'].append({
                        'check': 'generation_time',
                        'status': 'pass',
                        'score': 10,
                        'message': f'Generation time: {duration:.1f} seconds'
                    })
                    audit_result['score'] += 10
                else:
                    audit_result['checks'].append({
                        'check': 'generation_time',
                        'status': 'warning',
                        'score': 5,
                        'message': f'Unusual generation time: {duration:.1f} seconds'
                    })
                    audit_result['score'] += 5
            except (TypeError, ValueError) as dt_err:
                audit_result['checks'].append({
                    'check': 'generation_time',
                    'status': 'warning',
                    'score': 0,
                    'message': f'Generation timestamp parse failed: {dt_err}'
                })
        elif report_files or validated_artifacts:
            audit_result['checks'].append({
                'check': 'generation_time',
                'status': 'pass',
                'score': 5,
                'message': 'Generation time not tracked (report_history not populated)'
            })
            audit_result['score'] += 5

        # Check 4-7: unimplemented deep checks — disclose honestly (Round 139).
        for _stub_check, _stub_msg in (
            ('data_sources', 'Not implemented: log-backed data-source verification'),
            ('bems_detection', 'Not implemented: BEMS detection verification'),
            ('references', 'Not implemented: citation parse verification'),
        ):
            audit_result['checks'].append({
                'check': _stub_check,
                'status': 'skipped',
                'score': 0,
                'message': _stub_msg,
            })

        # Check 6: Verify report formatting
        _size_probe = validated_artifacts[0] if validated_artifacts else (report_files[0] if report_files else None)
        if _size_probe:
            file_size = _size_probe.stat().st_size
            if file_size > 50000:  # Reports should be >50KB
                audit_result['checks'].append({
                    'check': 'file_size',
                    'status': 'pass',
                    'score': 10,
                    'message': f'Report size: {file_size / 1024:.1f} KB'
                })
                audit_result['score'] += 10
            else:
                audit_result['checks'].append({
                    'check': 'file_size',
                    'status': 'warning',
                    'score': 5,
                    'message': f'Report may be incomplete: {file_size / 1024:.1f} KB'
                })
                audit_result['score'] += 5

        # Check 8: IP address security check
        if report_data and report_data[7]:
            ip_risk = assess_ip_risk(report_data[7], 1, '')
            if ip_risk == 'LOW':
                audit_result['checks'].append({
                    'check': 'ip_security',
                    'status': 'pass',
                    'score': 10,
                    'message': f'IP address verified: {report_data[7]}'
                })
                audit_result['score'] += 10
            else:
                audit_result['checks'].append({
                    'check': 'ip_security',
                    'status': 'warning',
                    'score': 5,
                    'message': f'IP risk level: {ip_risk}'
                })
                audit_result['score'] += 5

        # Determine overall status (Round 139: no "excellent" on skipped checks).
        _failed = [c for c in audit_result['checks'] if c.get('status') == 'fail']
        if _failed:
            audit_result['status'] = 'needs_improvement'
        elif audit_result['score'] >= int(audit_result['max_score'] * 0.9):
            audit_result['status'] = 'good'
        elif audit_result['score'] >= int(audit_result['max_score'] * 0.6):
            audit_result['status'] = 'acceptable'
        else:
            audit_result['status'] = 'needs_improvement'

        # Log audit event
        log_security_event('audit_completed', report_data[7] if report_data else 'unknown',
                         '', analysis_id, f"Audit score: {audit_result['score']}/{audit_result['max_score']}")

        # Save audit results to database
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO audit_results
                (analysis_id, audit_timestamp, status, score, max_score, checks_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                analysis_id,
                audit_result['audit_timestamp'],
                audit_result['status'],
                audit_result['score'],
                audit_result['max_score'],
                json.dumps(audit_result['checks']),
                _r12_admin_utc_iso_z()
            ))

        logger.info(
            "Audit completed for %s: %s (%s/%s)",
            analysis_id,
            audit_result['status'],
            audit_result['score'],
            audit_result['max_score'],
        )

    except Exception as e:
        logger.error(f"Error auditing report {analysis_id}: {e}")
        audit_result['status'] = 'error'
        audit_result['error'] = 'An error occurred during report audit. See logs for details.'

    return audit_result

def get_audit_history(limit=50):
    """Get history of completed audits"""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT analysis_id, audit_timestamp, status, score, max_score, checks_json
                FROM audit_results
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))

            rows = cursor.fetchall()

        audits = []
        total_score = 0
        for row in rows:
            audits.append({
                'analysis_id': row[0],
                'timestamp': row[1],  # Changed from audit_timestamp
                'status': row[2],
                'score': row[3],
                'max_score': row[4],
                'checks': (lambda raw: {c.get('check'): c.get('status') == 'pass' for c in raw if c.get('check')} if isinstance(raw, list) else raw)(_safe_json_load(row[5])) if row[5] else {},
                'report_type': 'Report'  # Default value
            })
            total_score += row[3] if row[3] else 0

        return {
            'total_audits': len(audits),
            'audits': audits,
            'average_score': total_score / len(audits) if audits else 0,
            'last_audit': audits[0] if audits else None
        }
    except Exception as e:
        logger.error(f"Error getting audit history: {e}")
        return {
            'total_audits': 0,
            'audits': [],
            'average_score': 0,
            'last_audit': None,
            'error': 'An error occurred while retrieving audit history.'
        }

def get_audit_summary():
    """Get summary statistics of all audits"""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()

            # Get total audits and average score
            cursor.execute('SELECT COUNT(*), AVG(score) FROM audit_results')
            total, avg_score = cursor.fetchone()

            # Get score distribution
            cursor.execute('''
                SELECT
                    SUM(CASE WHEN score >= 90 THEN 1 ELSE 0 END) as excellent,
                    SUM(CASE WHEN score >= 75 AND score < 90 THEN 1 ELSE 0 END) as good,
                    SUM(CASE WHEN score >= 60 AND score < 75 THEN 1 ELSE 0 END) as acceptable,
                    SUM(CASE WHEN score < 60 THEN 1 ELSE 0 END) as needs_improvement,
                    SUM(CASE WHEN status IN ('excellent', 'good', 'acceptable') THEN 1 ELSE 0 END) as passed,
                    SUM(CASE WHEN status = 'needs_improvement' OR status = 'error' THEN 1 ELSE 0 END) as failed
                FROM audit_results
            ''')
            stats = cursor.fetchone()

            # Get last audit date
            cursor.execute('SELECT MAX(audit_timestamp) FROM audit_results')
            last_audit = cursor.fetchone()[0]

        return {
            'total_audits_completed': total or 0,
            'average_audit_score': round(avg_score, 1) if avg_score else 0,
            'audits_passed': stats[4] if stats else 0,
            'audits_failed': stats[5] if stats else 0,
            'last_audit_date': last_audit,
            'score_distribution': {
                'excellent': stats[0] if stats else 0,  # 90-100
                'good': stats[1] if stats else 0,       # 75-89
                'acceptable': stats[2] if stats else 0, # 60-74
                'needs_improvement': stats[3] if stats else 0  # <60
            }
        }
    except Exception as e:
        logger.error(f"Error getting audit summary: {e}")
        return {
            'total_audits_completed': 0,
            'average_audit_score': 0,
            'audits_passed': 0,
            'audits_failed': 0,
            'last_audit_date': None,
            'score_distribution': {
                'excellent': 0,
                'good': 0,
                'acceptable': 0,
                'needs_improvement': 0
            },
            'error': 'An error occurred while retrieving audit summary.'
        }

def start_server():
    """Start the AdoptIQ server"""
    global server_process

    try:
        if server_process and server_process.poll() is None:
            return {'success': False, 'error': 'Server is already running'}

        # Round 9 / Phase 4.1: previously ``Popen([sys.executable,
        # 'app_simple.py'])`` resolved ``app_simple.py`` against the
        # *caller's* CWD.  An admin dashboard launched via systemd /
        # macOS launchd normally inherits CWD=``/`` -- which made the
        # spawn fail with FileNotFoundError if a stray script of the
        # same name happened to live in the CWD it could redirect to a
        # *different* interpreter target.  Anchor the spawn on the
        # admin module's own directory so the path is always
        # deterministic and not influenced by inherited CWD.
        from pathlib import Path as _Path
        _app_simple_path = (_Path(__file__).resolve().parent / 'app_simple.py')
        if not _app_simple_path.is_file():
            log_error(
                'ERROR',
                f'app_simple.py not found next to admin module (looked at {_app_simple_path.name})',
                'start_server',
            )
            return {'success': False, 'error': 'Server entry point not found'}
        server_process = subprocess.Popen(
            [sys.executable, str(_app_simple_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_app_simple_path.parent),
        )

        # Wait a moment to check if it started successfully
        time.sleep(2)

        if server_process.poll() is None:
            log_error('INFO', f'AdoptIQ server started with PID {server_process.pid}', 'start_server')
            return {'success': True, 'pid': server_process.pid}
        else:
            return {'success': False, 'error': 'Server failed to start'}

    except Exception as e:
        log_error('ERROR', f'Failed to start server: {e}', 'start_server')
        return {'success': False, 'error': 'Failed to start server. See logs for details.'}

def stop_server():
    """Stop the AdoptIQ server"""
    global server_process

    try:
        if server_process and server_process.poll() is None:
            server_process.terminate()
            server_process.wait(timeout=10)
            log_error('INFO', f'AdoptIQ server stopped', 'stop_server')
            return {'success': True}
        else:
            return {'success': False, 'error': 'Server is not running'}

    except Exception as e:
        log_error('ERROR', f'Failed to stop server: {e}', 'stop_server')
        return {'success': False, 'error': 'Failed to stop server. See logs for details.'}

if __name__ == '__main__':
    try:
        # Fix Windows console encoding for emojis
        if sys.platform == 'win32' and hasattr(sys.stdout, 'buffer'):
            try:
                import codecs
                sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
                sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
            except Exception:
                pass  # Keep default encoding if this fails
        init_database()
        # Round 8 / Phase 4.11: bind to the loopback by default so the
        # admin dashboard is not unintentionally exposed on every
        # network interface of the host.  The before_request hook
        # already rejects non-loopback peers with 403, but binding
        # to 0.0.0.0 still makes the port observable to network
        # scanners and any unauthenticated client on the same LAN
        # / VPN.  Operators who genuinely need remote admin access
        # must opt in explicitly via ``ADOPTIQ_ADMIN_BIND_PUBLIC=1``
        # (which keeps the existing 0.0.0.0 binding for backward
        # compatibility) or override ``ADOPTIQ_ADMIN_HOST`` to a
        # specific interface.
        _admin_host = (os.environ.get('ADOPTIQ_ADMIN_HOST') or '').strip()
        if not _admin_host:
            _bind_public = os.environ.get('ADOPTIQ_ADMIN_BIND_PUBLIC', '').strip().lower() in {'1', 'true', 'yes'}
            # Round 14 / Phase 4.5: 0.0.0.0 here is gated by the explicit
            # ``ADOPTIQ_ADMIN_BIND_PUBLIC=1`` opt-in.  Default is loopback.
            # Keep the noqa pinned per line so the opt-in stays explicit.
            _admin_host = '0.0.0.0' if _bind_public else '127.0.0.1'  # noqa: S104 # nosec B104 - opt-in via ADOPTIQ_ADMIN_BIND_PUBLIC=1
        if _admin_host not in ('127.0.0.1', '::1', 'localhost'):
            print(
                "[SECURITY] Admin dashboard binding to non-loopback host "
                f"'{_admin_host}'. Ensure firewall + auth controls are in place; "
                "the before_request hook still restricts to loopback peers."
            )
        # Round 17.3: env-overridable port (default 5152, was 5002).
        _admin_port = _resolve_admin_port()
        print("Starting AdoptIQ Admin Dashboard v2.0...")
        print(f"Access the dashboard at: http://{_admin_host if _admin_host != '0.0.0.0' else 'localhost'}:{_admin_port}")  # noqa: S104 # nosec B104 - string compare in display label
        admin_app.run(host=_admin_host, port=_admin_port, debug=False)
    except Exception as e:
        print("Admin Console failed to start:", e)
        import traceback
        traceback.print_exc()
        sys.exit(1)
