# AdoptIQ Executive Analyzer - Configuration
# All secrets and DB credentials come from environment variables (.env). No hardcoded credentials.
# Version and build - updated by build scripts (macOS/Windows) before packaging.
ADOPTIQ_VERSION = "1.0.4"
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


def _csone_onedrive_candidates() -> list[str]:
    """Round 17.2: priority-ordered list of paths to search for a
    synced OneDrive copy of ``AdoptIQ_CSOne_Reports``.  Modern macOS
    (Big Sur+) puts the OneDrive sync under
    ``~/Library/CloudStorage/OneDrive-Cisco/...``; older installations
    kept the human-readable ``OneDrive - Cisco`` directory directly
    under ``~``; some users nested everything under a ``Documents/``
    subfolder.  We try each candidate in this order and pick the
    first one that exists.
    """
    home = os.path.expanduser('~')
    return [
        # Modern macOS Cloud-Storage location (most users today).
        os.path.join(home, 'Library', 'CloudStorage', 'OneDrive-Cisco',
                     'AI Projects', 'AdoptIQ_CSOne_Reports'),
        # Modern macOS Cloud-Storage with the legacy ``Documents/`` nesting.
        os.path.join(home, 'Library', 'CloudStorage', 'OneDrive-Cisco',
                     'Documents', 'AI Projects', 'AdoptIQ_CSOne_Reports'),
        # Pre-Big Sur macOS / Windows symlink shape.
        os.path.join(home, 'OneDrive - Cisco',
                     'AI Projects', 'AdoptIQ_CSOne_Reports'),
        # Legacy default with the ``Documents/`` segment.
        os.path.join(home, 'OneDrive - Cisco', 'Documents',
                     'AI Projects', 'AdoptIQ_CSOne_Reports'),
    ]


def _resolve_csone_onedrive_folder() -> str:
    """Round 17.2: return the first existing OneDrive candidate, or
    fall back to the modern Cloud-Storage path so error messages
    still point at the right "expected" location when nothing is
    synced.  Honors ``CSONE_ONEDRIVE_FOLDER`` env override
    unconditionally so power users can pin any path."""
    override = os.environ.get('CSONE_ONEDRIVE_FOLDER')
    if override:
        return override
    for candidate in _csone_onedrive_candidates():
        try:
            if os.path.isdir(candidate):
                return candidate
        except Exception:
            continue
    return _csone_onedrive_candidates()[0]


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
    # Round 17.2: auto-discover the synced OneDrive folder across the
    # paths Microsoft / Apple have used at different points in time.
    # See ``_csone_onedrive_candidates`` / ``_resolve_csone_onedrive_folder``
    # for the priority list; the first existing directory wins.
    CSONE_ONEDRIVE_FOLDER = _resolve_csone_onedrive_folder()

    # Round 17.1: corpus also indexes the runtime user's Downloads
    # folder, filtered to ``AdoptIQ_*`` / ``AdoptIQ Enhanced ...``
    # filenames (see ``corpus_indexer._USER_REPORT_NAME_RE``).  The
    # walker is non-recursive so unrelated user files in nested
    # directories (e.g. ``~/Downloads/Photos/``) are never opened.
    # ``CSONE_INCLUDE_USER_DOWNLOADS=false`` skips the Downloads
    # source entirely; ``CSONE_USER_DOWNLOADS_DIR`` overrides the
    # default ``~/Downloads`` location for the rare site-specific
    # case where reports land elsewhere.
    CSONE_INCLUDE_USER_DOWNLOADS = (
        str(os.environ.get('CSONE_INCLUDE_USER_DOWNLOADS', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )
    CSONE_USER_DOWNLOADS_DIR = os.environ.get('CSONE_USER_DOWNLOADS_DIR') or str(
        Path.home() / 'Downloads'
    )

    # Round 17: CSOne Knowledge Corpus feature flag.
    #
    # When enabled (env ``CORPUS_KNOWLEDGE_ENABLED`` truthy), AdoptIQ
    # builds an encrypted local cache of the CSOne report corpus from
    # ``CSONE_ONEDRIVE_FOLDER`` on first launch and surfaces it through
    # Ask AI / Customer 360 / Playbook / Admin tile.  The OneDrive
    # client itself enforces the SharePoint ACL -- if the user has no
    # local sync the corpus surfaces as ``CorpusUnavailable`` and every
    # caller falls back to today's behavior.  Default: off, so existing
    # deployments are unchanged until explicitly enabled.
    CORPUS_KNOWLEDGE_ENABLED = (
        str(os.environ.get('CORPUS_KNOWLEDGE_ENABLED', 'false')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )

    # Round 17.2: SharePoint Microsoft Graph corpus source.
    #
    # When enabled, AdoptIQ pulls raw CSOne report files from a
    # delegated-share folder URL using the Microsoft Graph PowerShell
    # public client + device-code flow, caches them under
    # ``ADOPTIQ_SHAREPOINT_CACHE_DIR`` (default
    # ``~/.adoptiq/cache/sharepoint_csone``) and indexes that cache
    # before falling through to ``CSONE_ONEDRIVE_FOLDER`` and
    # ``CSONE_USER_DOWNLOADS_DIR``.  No tenant-specific app
    # registration is required; the client id is the public
    # Microsoft Graph PowerShell value baked into MSAL samples and is
    # explicitly NOT a secret.  The user signs in once via the
    # device-code flow; the refresh token is persisted in the macOS
    # Keychain (via ``keyring``) with a 0600 file fallback under
    # ``~/.adoptiq``.  Defaults: feature is on by default once a
    # share URL is configured; auth state being absent simply skips
    # the source on the first launch.
    ADOPTIQ_SHAREPOINT_ENABLED = (
        str(os.environ.get('ADOPTIQ_SHAREPOINT_ENABLED', 'true')).strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )
    ADOPTIQ_SHAREPOINT_FOLDER_URL = (
        os.environ.get('ADOPTIQ_SHAREPOINT_FOLDER_URL')
        or 'https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports?csf=1&web=1&e=d5qzVl'
    )
    # Microsoft-owned public client id ("Microsoft Graph PowerShell").
    # Public, not a secret -- listed in MSAL sample code.  Operators
    # who run their own Azure AD app can override.
    ADOPTIQ_SHAREPOINT_CLIENT_ID = (
        os.environ.get('ADOPTIQ_SHAREPOINT_CLIENT_ID')
        or '14d82eec-204b-4c2f-b7e8-296a70dab67e'
    )
    ADOPTIQ_SHAREPOINT_AUTHORITY = (
        os.environ.get('ADOPTIQ_SHAREPOINT_AUTHORITY')
        or 'https://login.microsoftonline.com/common'
    )
    ADOPTIQ_SHAREPOINT_CACHE_DIR = os.environ.get(
        'ADOPTIQ_SHAREPOINT_CACHE_DIR'
    ) or str(Path.home() / '.adoptiq' / 'cache' / 'sharepoint_csone')
    # Per-file cap (50 MiB) -- aligned with corpus_indexer's
    # ``_MAX_PARSE_BYTES`` so files we download will not be silently
    # rejected by the indexer.  Operators with larger reports can
    # raise via env, but be aware the indexer will skip them.
    ADOPTIQ_SHAREPOINT_MAX_FILE_BYTES = int(
        os.environ.get('ADOPTIQ_SHAREPOINT_MAX_FILE_BYTES') or (50 * 1024 * 1024)
    )

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
