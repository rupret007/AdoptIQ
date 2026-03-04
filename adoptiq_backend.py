import os, sys, json, re, time, math, logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import warnings
import pandas as pd
import openpyxl
import hvac
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import snowflake.connector
import requests
from bs4 import BeautifulSoup
import feedparser
from openai import AzureOpenAI, APITimeoutError
from docx.shared import Pt
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from pandas import to_datetime, Timestamp, Timedelta
from docx import Document

# Enhanced executive report generation
try:
    from enhanced_adoptiq_backend import enhanced_generate_word_report
    ENHANCED_REPORTS_AVAILABLE = True
except ImportError:
    ENHANCED_REPORTS_AVAILABLE = False
    logging.getLogger(__name__).warning("Enhanced reports not available - using standard generation")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ — All‑in‑One (CircuIT-only) + External Intelligence

DEFINITIVE, UPGRADED VERSION: This script is fully debugged and re-architected to
produce a manager-focused report with a professional, readable format, including
a curated and human-readable Excel output.
"""


# Suppress the specific, harmless UserWarning from openpyxl about default styles
warnings.filterwarnings("ignore", category=UserWarning, message="Workbook contains no default style")
# Suppress the specific, harmless FutureWarning from pandas about downcasting
warnings.filterwarnings("ignore", category=FutureWarning, message="Downcasting object dtype arrays")


# === CREDENTIALS: From config (which reads .env); no hardcoded secrets ===
from config import Config
KEEPER_CONFIG = Config.KEEPER_CONFIG
SNOWFLAKE_CONFIG = Config.SNOWFLAKE_CONFIG
CIRCUIT_CONFIG = Config.CIRCUIT_CONFIG
# =================================================

# Learned insights (past analyses) — optional; inject into CircuIT prompt when available
try:
    from enhanced_admin_dashboard_v2 import get_learned_insights
except Exception:
    def get_learned_insights(manager: str, technology: str, limit: int = 5) -> str:
        return ""

# --------------------------- Requirements reminder ---------------------------
REQUIREMENTS = """
pandas
sqlalchemy
snowflake-connector-python
snowflake-sqlalchemy
openpyxl
xlsxwriter
python-docx
rich
typer
cryptography
hvac
openai
requests
beautifulsoup4
feedparser
pyinstaller
"""

# --------------------------- Team roster ---------------------------
# Load team configuration from JSON file for easy editing
def _load_team_config():
    """Load team roster and managers from JSON config file"""
    config_path = Path(__file__).parent / "team_config.json"
    try:
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Convert JSON format to tuple format for backward compatibility
            team_roster = [
                (member.get("manager", ""), member.get("name", ""), member.get("email", ""))
                for member in config.get("team_roster", [])
                if isinstance(member, dict)
            ]
            managers = config.get("managers", ["Dee Kindrick", "Brian Frazier", "Shams", "All Managers"])
            
            return team_roster, managers
        else:
            # Fallback to default if file doesn't exist
            logger.warning(f"team_config.json not found at {config_path}, using defaults")
            return _get_default_team_config()
    except Exception as e:
        logger.error(f"Error loading team config: {e}, using defaults")
        return _get_default_team_config()

def _get_default_team_config():
    """Default team configuration (fallback)"""
    team_roster = [
        ("Dee Kindrick", "Nate Hardy", "nahardy@cisco.com"),
        ("Dee Kindrick", "Eli Walsh", "elwalsh@cisco.com"),
        ("Dee Kindrick", "Cesar Ozuna", "ceozuna@cisco.com"),
        ("Dee Kindrick", "Stephen Williams", "stepwil3@cisco.com"),
        ("Dee Kindrick", "Asad Sarfaraz", "asarfara@cisco.com"),
        ("Dee Kindrick", "Prabhakar Dakinedi", "pdakined@cisco.com"),
        ("Dee Kindrick", "Michael Thompson", "mithomp2@cisco.com"),
        ("Dee Kindrick", "Xavier Pena", "xpena@cisco.com"),
        ("Dee Kindrick", "Christine Simrell", "chrsimre@cisco.com"),
        ("Dee Kindrick", "Brice Mercer", "brimerce@cisco.com"),
        ("Brian Frazier", "Angelica Hernandez Becerra", "angelihe@cisco.com"),
        ("Brian Frazier", "Jeffrey Story", "jestory@cisco.com"),
        ("Brian Frazier", "Hector Gonzalez", "hectgon2@cisco.com"),
        ("Brian Frazier", "Ujjwal Aneja", "uaneja@cisco.com"),
        ("Brian Frazier", "Jose Nerio Chavarri Espinosa", "josencha@cisco.com"),
        ("Brian Frazier", "Arpit Patel", "arpitpat@cisco.com"),
        ("Brian Frazier", "Greg Dolberry", "gdolberr@cisco.com"),
        ("Brian Frazier", "Haydee Hernandez Ceja", "hayherna@cisco.com"),
        ("Brian Frazier", "William Phillips", "willphil@cisco.com"),
        ("Josh Horowitz", "Michael Ramsey", "michrams@cisco.com"),
        ("Josh Horowitz", "Anthony Ortiz", "antortiz@cisco.com"),
        ("Josh Horowitz", "Nitish Sinha", "nitsinh2@cisco.com"),
        ("Josh Horowitz", "Samuel Tamayo", "samtamay@cisco.com"),
        ("Josh Horowitz", "Mario Pena", "marpena2@cisco.com"),
        ("Josh Horowitz", "Tim Tyler", "tityler@cisco.com"),
        ("Josh Horowitz", "Chris Clark", "christc3@cisco.com"),
        ("Josh Horowitz", "Brandon Doan", "brdoan@cisco.com"),
    ]
    managers = ["Dee Kindrick", "Brian Frazier", "Shams", "All Managers"]
    return team_roster, managers

# Load team configuration
TEAM_ROSTER, MANAGERS = _load_team_config()

TECH_CHOICES = [
    "Webex Meetings & Messaging",
    "Webex Calling",
    "Webex Contact Center",
    "Cisco UCCE",
    "Cisco UCCX",
    "All",
]

TECH_FILTERS = {
    "Webex Meetings & Messaging":[
        r'webex\s*meetings?', r'webex\s*messag(ing|e)', r'webex\s*app', r'webex\s*suite', r'collaboration',
        r'room\s*devices', r'desk\s*series', r'joining\s*a\s*meeting', r'scheduling', r'productivity\s*tools',
        r'recording', r'vidcast', r'video', r'hybrid\s*calendar', r'site\s*management', r'user\s*management',
        r'org\s*management', r's\s*s\s*p\s*t', r'c\s*v\s*i', r'edge\s*audio', r'video\s*mesh', r'edge\s*connect',
        r'webex\s*share', r'webex\s*events', r'socio', r'proactive\s*cases',
        # Additional patterns for better coverage
        r'webex\s*platform', r'webex\s*system', r'webex\s*service', r'webex\s*cloud', r'webex\s*hybrid',
        r'meeting\s*room', r'video\s*conferencing', r'web\s*conferencing', r'online\s*meeting', r'virtual\s*meeting',
        r'team\s*collaboration', r'workplace\s*collaboration', r'cisco\s*webex', r'webex\s*integration',
        r'webex\s*deployment', r'webex\s*configuration', r'webex\s*management', r'webex\s*admin'
    ],
    "Webex Calling":[
        r'webex\s*calling', r'(dedicated\s*instance|\bdi\b)',
        # Additional patterns for better coverage
        r'webex\s*calling\s*service', r'webex\s*calling\s*platform', r'webex\s*calling\s*system',
        r'cisco\s*calling', r'cloud\s*calling', r'voice\s*calling', r'pbx\s*cloud', r'cloud\s*pbx',
        r'webex\s*voice', r'voice\s*service', r'telephony', r'phone\s*system', r'calling\s*platform',
        r'webex\s*calling\s*integration', r'webex\s*calling\s*deployment', r'webex\s*calling\s*configuration'
    ],
    "Webex Contact Center":[
        r'(webex\s*contact\s*center|wxcc)',
        r'cloud\s*and\s*hybrid\s*products',
        r'contact\s*center\s*cloud',
        r'contact\s*center\s*hybrid',
        # Additional patterns for better coverage
        r'webex\s*contact\s*center\s*platform', r'webex\s*contact\s*center\s*system', r'webex\s*contact\s*center\s*service',
        r'wxcc\s*platform', r'wxcc\s*system', r'wxcc\s*service', r'contact\s*center\s*cloud', r'cloud\s*contact\s*center',
        r'webex\s*cc', r'cisco\s*contact\s*center', r'contact\s*center\s*platform', r'contact\s*center\s*system',
        r'webex\s*contact\s*center\s*integration', r'webex\s*contact\s*center\s*deployment', r'webex\s*contact\s*center\s*configuration'
    ],
    "Webex Contact Center Enterprise": [
        r'(webex\s*contact\s*center\s*enterprise|wxcc\s*enterprise)',
        r'contact\s*center\s*software',  # Only when NOT UCCX/UCCE
        r'enterprise\s*contact\s*center'
    ],
    "Cisco UCCE":[
        r'\bucce\b', r'unified\s*contact\s*center\s*enterprise', r'contact\s*center\s*enterprise',
        r'cisco\s*contact\s*center\s*enterprise', r'ucce\s*platform', r'ucce\s*system',
        r'cisco\s*ucce', r'ucce\s*deployment', r'ucce\s*configuration',
        r'contact\s*center\s*enterprise\s*management', r'ucce\s*management', r'ucce\s*integration', r'ucce\s*setup',
        # Additional patterns for better coverage
        r'ucce\s*service', r'ucce\s*cloud', r'ucce\s*hybrid', r'ucce\s*on\s*prem', r'ucce\s*on\s*premises',
        r'unified\s*cc\s*enterprise', r'cisco\s*unified\s*contact\s*center\s*enterprise', r'contact\s*center\s*enterprise\s*platform',
        r'ucce\s*admin', r'ucce\s*administration', r'ucce\s*monitoring', r'ucce\s*troubleshooting',
        # More specific patterns to avoid matching Webex Contact Center
        r'ucce\s*agent', r'ucce\s*supervisor', r'ucce\s*router', r'ucce\s*logger', r'ucce\s*peripheral',
        r'contact\s*center\s*enterprise\s*agent', r'contact\s*center\s*enterprise\s*supervisor'
    ],
    "Cisco UCCX":[
        r'\buccx\b', r'unified\s*contact\s*center\s*express',
        # Additional patterns for better coverage
        r'uccx\s*express', r'contact\s*center\s*express', r'cisco\s*uccx', r'unified\s*cc\s*express',
        r'uccx\s*platform', r'uccx\s*system', r'uccx\s*service', r'uccx\s*deployment', r'uccx\s*configuration',
        r'uccx\s*management', r'uccx\s*integration', r'uccx\s*setup', r'uccx\s*admin', r'uccx\s*administration',
        r'cisco\s*unified\s*contact\s*center\s*express', r'contact\s*center\s*express\s*platform'
    ],
    "All Contact Center":[
        # Webex Contact Center patterns
        r'(webex\s*contact\s*center|wxcc)',
        r'cloud\s*and\s*hybrid\s*products',
        r'contact\s*center\s*cloud',
        r'contact\s*center\s*hybrid',
        r'webex\s*contact\s*center\s*platform', r'webex\s*contact\s*center\s*system', r'webex\s*contact\s*center\s*service',
        r'wxcc\s*platform', r'wxcc\s*system', r'wxcc\s*service', r'contact\s*center\s*cloud', r'cloud\s*contact\s*center',
        r'webex\s*cc', r'cisco\s*contact\s*center', r'contact\s*center\s*platform', r'contact\s*center\s*system',
        # Webex Contact Center Enterprise patterns
        r'(webex\s*contact\s*center\s*enterprise|wxcc\s*enterprise)',
        r'enterprise\s*contact\s*center',
        # Cisco UCCE patterns
        r'\bucce\b', r'unified\s*contact\s*center\s*enterprise', r'contact\s*center\s*enterprise',
        r'cisco\s*contact\s*center\s*enterprise', r'ucce\s*platform', r'ucce\s*system',
        r'cisco\s*ucce', r'ucce\s*deployment', r'ucce\s*configuration',
        r'contact\s*center\s*enterprise\s*management', r'ucce\s*management', r'ucce\s*integration', r'ucce\s*setup',
        r'ucce\s*service', r'ucce\s*cloud', r'ucce\s*hybrid', r'ucce\s*on\s*prem', r'ucce\s*on\s*premises',
        r'unified\s*cc\s*enterprise', r'cisco\s*unified\s*contact\s*center\s*enterprise', r'contact\s*center\s*enterprise\s*platform',
        r'ucce\s*admin', r'ucce\s*administration', r'ucce\s*monitoring', r'ucce\s*troubleshooting',
        r'ucce\s*agent', r'ucce\s*supervisor', r'ucce\s*router', r'ucce\s*logger', r'ucce\s*peripheral',
        # Cisco UCCX patterns
        r'\buccx\b', r'unified\s*contact\s*center\s*express',
        r'uccx\s*express', r'contact\s*center\s*express', r'cisco\s*uccx', r'unified\s*cc\s*express',
        r'uccx\s*platform', r'uccx\s*system', r'uccx\s*service', r'uccx\s*deployment', r'uccx\s*configuration',
        r'uccx\s*management', r'uccx\s*integration', r'uccx\s*setup', r'uccx\s*admin', r'uccx\s*administration',
        r'cisco\s*unified\s*contact\s*center\s*express', r'contact\s*center\s*express\s*platform',
        # Generic contact center patterns
        r'contact\s*center\s*software', r'contact\s*center\s*management', r'contact\s*center\s*operations',
        r'call\s*center', r'call\s*center\s*software', r'call\s*center\s*management'
    ],
}

OFFICIAL_CATEGORIES = {
"Cisco External":[
"Not a Customer Priority","Customer Considering Competitor","Customer Internal Strategy Mis-Alignment",
"Future Intent to Adopt","Intent to Opt Out or No Value Fit (Future Follow Up)","No Budget Funding"],
"Customer Enablement/Technical Gap":[
"Additional Customer Training Required","Cisco or 3rd Party Compatibility","Configuration Assistance Needed",
"Diagnostic or Monitoring Assistance Needed","High-Level/Low-Level Design Assistance Needed","Installation Assistance Needed",
"Licensing or Smart Account Support Needed","Limited/Lack of Use Case Understanding","Migration/Upgrade Assistance Needed",
"Product Licensing Operations Issue","Professional Services Needed (Cisco AS or Partner)","TAC Support Needed or Pending TAC Cases"],
"Customer Environment Not Ready":[
"Additional HW/SW needed","Customer’s Infrastructure Not Ready"],
"Customer Perceives Product Not Ready":[
"Feature to Complete Deploy","Feature Request","Pending Known Product Bug","Product or Solution Not Fed-Ramped (or lacks certifications)"],
"Customer Needs Partner Support":[
"Partner Experience (includes Technical and/or Resource Challenge)","Partner Needs Enablement","Partner Unresponsive to Customer"],
"Cisco Internal":[
"Unable to Engage","Customer is Unresponsive","Invalid Contact","Missing Contact"],
"Adoption On Hold":[
"Deprioritized by Theater Leadership","Hold Request from Account Team","Internal CS Resource Limitations","Product/Service Seeded","Supply Chain Delay/Issue with HW"],
"Data/Documentation Issue":[
"Documentation Not Found or Insufficient","Inaccurate Eligibility","Internal Use Case Exit Criteria Issue","Internal Use Case Telemetry Issue"],
}

# --------------------------- Utilities ---------------------------

def _ensure_outputs():
    """Return outputs directory; when frozen, use platform app data dir so paths are stable for download."""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            out = Path.home() / "Library" / "Application Support" / "AdoptIQ" / "outputs"
        elif sys.platform == "win32":
            out = Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ" / "outputs"
        else:
            out = Path.home() / ".adoptiq" / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        return out
    Path("./outputs").mkdir(parents=True, exist_ok=True)
    return Path("./outputs")

def _extract_refs(text: str) -> str:
    if not text: return ""
    refs = set()
    for m in re.findall(r"\bBEMS[- ]?\d+\b", text, flags=re.IGNORECASE): refs.add(m.upper())
    for m in re.findall(r"\bCSC[a-zA-Z0-9]{6,10}\b|WXCCSA-\d+|CJPIM-\d+|CT-\d+|WXCUST-I-\d+|COLLAB-I-\d+", text, flags=re.IGNORECASE):
        refs.add(m.upper())
    return ", ".join(sorted(refs))

def _filter_tech_text(s: str, tech: str) -> bool:
    if tech == "All": return True
    if not s: return False
    s = str(s).lower()
    for pat in TECH_FILTERS[tech]:
        if re.search(pat, s): return True
    return False


def _is_wxcce_signature(text: str) -> bool:
    """Detect enterprise contact-center signatures that should not count as WxCC."""
    if not text:
        return False
    s = str(text).lower()
    enterprise_markers = (
        "webex cce",
        "webex contact center enterprise",
        "wxcc enterprise",
        "contact center enterprise",
        "unified contact center enterprise",
        " ucce",
    )
    return any(marker in s for marker in enterprise_markers)


def _filter_tech_text_enhanced(tech_field: str, sub_tech_field: str, tech: str) -> bool:
    """
    Enhanced technology filtering that prioritizes Sub Technology over Tech field
    to ensure accurate categorization (e.g., UCCX showing as 'Contact Center Software' 
    in Tech field but 'UCCX' in Sub Technology field)
    """
    if tech == "All": 
        return True
    
    # Special handling for "All Contact Center" - match any contact center technology
    if tech == "All Contact Center":
        contact_center_techs = ["Webex Contact Center", "Webex Contact Center Enterprise", "Cisco UCCE", "Cisco UCCX"]
        for contact_tech in contact_center_techs:
            if _filter_tech_text_enhanced(tech_field, sub_tech_field, contact_tech):
                return True
        return False
    
    # Convert to strings and lowercase
    tech_field = str(tech_field).lower() if tech_field else ""
    sub_tech_field = str(sub_tech_field).lower() if sub_tech_field else ""

    # Defect fix: WxCC must not include enterprise-tagged records.
    if tech == "Webex Contact Center":
        if _is_wxcce_signature(sub_tech_field) or _is_wxcce_signature(tech_field):
            return False
    
    # Priority 1: Check Sub Technology field first (most specific)
    if sub_tech_field:
        # Handle specific Sub Technology conflicts
        if tech == "Cisco UCCE" and ("webex" in sub_tech_field or "wxcc" in sub_tech_field):
            # Don't match UCCE if Sub Technology contains Webex/WXCC
            return False
        elif tech == "Cisco UCCX" and ("webex" in sub_tech_field or "wxcc" in sub_tech_field):
            # Don't match UCCX if Sub Technology contains Webex/WXCC
            return False
        elif tech == "Webex Contact Center Enterprise" and ("uccx" in sub_tech_field or "ucce" in sub_tech_field):
            # Don't match Webex Contact Center Enterprise if Sub Technology contains UCCX/UCCE
            return False
        
        # Standard pattern matching for Sub Technology
        for pat in TECH_FILTERS[tech]:
            if re.search(pat, sub_tech_field):
                return True
    
    # Priority 2: Check Tech field with conflict resolution
    if tech_field:
        # Handle specific conflicts where Tech field is ambiguous
        if tech_field == "contact center software":
            # This could be UCCX, UCCE, or Webex Contact Center Enterprise
            # Check Sub Technology to resolve the conflict
            if sub_tech_field:
                if "uccx" in sub_tech_field or "express" in sub_tech_field:
                    return tech == "Cisco UCCX"
                elif "ucce" in sub_tech_field:
                    return tech == "Cisco UCCE"
                elif _is_wxcce_signature(sub_tech_field):
                    return tech == "Webex Contact Center Enterprise"
                elif "webex contact center" in sub_tech_field or "wxcc" in sub_tech_field:
                    return tech == "Webex Contact Center"
            # If no Sub Technology, default to Webex Contact Center Enterprise
            return tech == "Webex Contact Center Enterprise"
        
        # For all other Tech field values, use standard pattern matching
        for pat in TECH_FILTERS[tech]:
            if re.search(pat, tech_field):
                return True
    
    return False

def _normalize_category(cat: str) -> str:
    if not cat: return "Uncategorized"
    c = str(cat).strip()
    for grp, subs in OFFICIAL_CATEGORIES.items():
        if c in subs: return c
    cl = c.lower()
    for grp, subs in OFFICIAL_CATEGORIES.items():
        for s in subs:
            if cl in s.lower() or s.lower() in cl: return s
    return "Uncategorized"

def _normalize_subtech(txt: str) -> str:
    if not txt: return "Unknown"
    t = str(txt).lower()
    # Check in a specific order to avoid mis-categorization
    for tech_name in ["Webex Contact Center", "Webex Calling", "Cisco UCCE", "Cisco UCCX", "Webex Meetings & Messaging"]:
        for pat in TECH_FILTERS[tech_name]:
            if re.search(pat, t):
                return tech_name
    return "Other/Unknown"

# --------------------------- IO (CSOne Excel & DB Profile) ---------------------------
LIKELY_DATE_COLS = {"Date/Time Opened","Created","Created Date","OPEN_DATE","OPEN_DATE_C","CREATED_DATE","CREATED_DATE_C"}
LIKELY_TITLE_COLS = {"Title","TITLE","SUBJECT","SUBJECT_C","NAME","ACTION_PLAN_TITLE_C"}
LIKELY_DESC_COLS = {"Problem Description","DESCRIPTION","DESCRIPTION_C","COMMENTS","COMMENTS__C"}
LIKELY_OWNER_COLS = {"Owner Email","OWNER_EMAIL","ASSIGNEE_EMAIL","ASSIGNEE","ASSIGNEE_C","OWNER"}
LIKELY_CUST_COLS  = {"Customer Name","BU_NAME","CUSTOMER","ACCOUNT","ACCOUNT_NAME"}
LIKELY_CASE_COLS  = {"SR Number","Case Number","CASE_NUMBER","SR_NUMBER"}
LIKELY_TECH_COLS  = {"Product","Technology","PRODUCT","PRODUCT_C","PRODUCT_NAME_C","CSS_PRE_UNLINK_TECHNOLOGY_NAME_C","SUCCESS_TRACK_C", "Sub Technology"}
LIKELY_SUB_COLS = {"Subscription ID", "SUBSCRIPTION_ID", "SUB_ID", "Subscription Number", "Subscription Reference Id"}

def load_csone_excel(path: Optional[Path]) -> pd.DataFrame:
    if path is None or not Path(path).exists():
        return pd.DataFrame()
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
        sheet = wb.active
        if sheet is None:
            logger.warning("CSOne workbook has no active sheet")
            return pd.DataFrame()
        header = None; start = 2
        for i, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if row and sum(1 for c in row if c) >= 3:
                header = [str(c).strip() if c else f"col_{j}" for j, c in enumerate(row)]
                start = i + 1; break
        if not header:
            return pd.DataFrame()
        rows = []
        for r in sheet.iter_rows(min_row=start, values_only=True):
            rows.append(dict(zip(header, r)))
        df = pd.DataFrame(rows)
        df.columns = [c.strip() for c in df.columns]
        
        if 'col_0' in df.columns:
            df.drop(columns=['col_0'], inplace=True)
            
        title_col = next((c for c in LIKELY_TITLE_COLS if c in df.columns), None)
        desc_col = next((c for c in LIKELY_DESC_COLS if c in df.columns), None)
        _title = df[title_col].fillna("").astype(str) if title_col else pd.Series([""] * len(df), index=df.index)
        _desc = df[desc_col].fillna("").astype(str) if desc_col else pd.Series([""] * len(df), index=df.index)
        df["bemscsc_refs"] = (_title + " " + _desc).apply(_extract_refs)
        return df
    except Exception as e:
        logger.error(f"Failed to load CSOne Excel file: {e}")
        return pd.DataFrame()

def newest_csone(folder: Path) -> Optional[Path]:
    cands = sorted(Path(folder).glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None

def load_db_profile() -> Optional[dict]:
    """Loads an optional database profile JSON file for richer context."""
    profile_path = Path("database_profile.json")
    if profile_path.exists():
        logger.info("Found database_profile.json, loading for enhanced context...")
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

# --------------------------- Snowflake ---------------------------
DSM_TABLE = "CX_DB.CX_SWSSBST_BR.dsm_assignment_data"
AB_TABLE  = "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW"

def _connect_snowflake_direct():
    """Connect to Snowflake using user/password from env (no Keeper). Used when credentials are embedded."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
    if not (SNOWFLAKE_CONFIG.get("user") and SNOWFLAKE_CONFIG.get("account") and SNOWFLAKE_CONFIG.get("password")):
        env_path = (
            Path(os.environ.get("APPDATA", str(Path.home()))) / "AdoptIQ" / ".env"
            if sys.platform == "win32"
            else Path.home() / "Library" / "Application Support" / "AdoptIQ" / ".env"
        )
        raise RuntimeError(
            f"Snowflake credentials not set. Add a .env file at {env_path} with "
            "SNOWFLAKE_USER=..., SNOWFLAKE_ACCOUNT=..., SNOWFLAKE_PASSWORD=... (and optionally SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE), "
            "then restart the app. Or add them to secrets.env and rebuild the app."
        )

    def connect():
        return snowflake.connector.connect(
            user=SNOWFLAKE_CONFIG["user"],
            password=SNOWFLAKE_CONFIG["password"],
            account=SNOWFLAKE_CONFIG["account"],
            role=SNOWFLAKE_CONFIG.get("role") or None,
            warehouse=SNOWFLAKE_CONFIG.get("warehouse") or None,
        )

    try:
        logger.info("Starting Snowflake connection (direct, no Keeper)...")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(connect)
            conn = future.result(timeout=30)
        logger.info("Snowflake connection successful")
        return conn
    except FutureTimeoutError:
        raise RuntimeError("Snowflake connection timed out after 30 seconds")
    except Exception as e:
        logger.error(f"Error connecting to Snowflake: {e}")
        raise RuntimeError(f"Failed to connect to Snowflake: {e}")


def _connect_with_keeper():
    """Connect to Snowflake: use password if set, else Keeper (works in both dev and frozen Mac app when creds are embedded)."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

    # Direct password preferred when set (dev or frozen)
    if SNOWFLAKE_CONFIG.get("password"):
        return _connect_snowflake_direct()
    # Keeper (private key) when Keeper creds are present (POC credentials; works in frozen app when embedded)
    if KEEPER_CONFIG.get("role_id") and KEEPER_CONFIG.get("secret_id"):
        pass  # fall through to Keeper flow below
    else:
        raise RuntimeError(
            "Snowflake credentials not set. Either set SNOWFLAKE_USER, SNOWFLAKE_ACCOUNT, SNOWFLAKE_PASSWORD in secrets.env (recommended for Mac app), or KEEPER_ROLE_ID and KEEPER_SECRET_ID for Keeper auth."
        )
    if not (SNOWFLAKE_CONFIG.get("user") and SNOWFLAKE_CONFIG.get("account")):
        raise RuntimeError(
            "Snowflake config not set. Add SNOWFLAKE_USER and SNOWFLAKE_ACCOUNT to your .env file (see .env.template)."
        )

    def connect_to_snowflake():
        try:
            client = hvac.Client(url=KEEPER_CONFIG["url"], namespace=KEEPER_CONFIG["namespace"])
            token = client.auth.approle.login(role_id=KEEPER_CONFIG["role_id"], secret_id=KEEPER_CONFIG["secret_id"])['auth']['client_token']
            secrets_client = hvac.Client(url=KEEPER_CONFIG["url"], namespace=KEEPER_CONFIG["namespace"], token=token)
            secret = secrets_client.read(KEEPER_CONFIG["secret_path"])['data']
            private_key_pem = secret['private_key']
            passphrase = secret['SNOWSQL_PRIVATE_KEY_PASSPHRASE']
            p_key = serialization.load_pem_private_key(private_key_pem.encode(), password=passphrase.encode(), backend=default_backend())
            pkb = p_key.private_bytes(encoding=serialization.Encoding.DER, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption())
            return snowflake.connector.connect(
                user=SNOWFLAKE_CONFIG["user"],
                private_key=pkb,
                account=SNOWFLAKE_CONFIG["account"],
                role=SNOWFLAKE_CONFIG["role"],
                warehouse=SNOWFLAKE_CONFIG["warehouse"],
            )
        except Exception as e:
            logger.error(f"Error connecting to Snowflake: {e}")
            raise RuntimeError(f"Failed to connect to Snowflake: {e}")

    try:
        logger.info("Starting Snowflake connection with Keeper (30-second timeout)...")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(connect_to_snowflake)
            connection = future.result(timeout=30)
            logger.info("Snowflake connection successful")
            return connection
    except FutureTimeoutError:
        logger.warning("Snowflake connection timed out after 30 seconds")
        raise RuntimeError("Snowflake connection timed out - database may be unavailable")
    except Exception as e:
        logger.error(f"Error connecting to Snowflake: {e}")
        raise RuntimeError(f"Failed to connect to Snowflake: {e}")

def fetch_subscription_data(subscription_id: str, days: int = 90) -> Dict[str, Any]:
    """
    Fetch comprehensive data for a specific subscription ID
    
    Args:
        subscription_id: The subscription ID to analyze
        days: Number of days to look back for data
        
    Returns:
        Dictionary containing customer info and all related data
    """
    ctx = None
    try:
        logger.info(f"[[SEARCH]] Fetching subscription data for: {subscription_id}")
        
        # Connect to Snowflake
        ctx = _connect_with_keeper()
        cur = ctx.cursor(snowflake.connector.DictCursor)
        
        # First, get account information from subscription (minimal columns - TECHNOLOGY_C/STATUS_C may not exist in all environments)
        logger.info(f"[[LIST]] Looking up account information for subscription: {subscription_id}")
        account_query = """
        SELECT ACCOUNT_ID_C, BU_NAME, SUBSCRIPTION_ID
        FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data
        WHERE SUBSCRIPTION_ID = %s
        LIMIT 1
        """
        
        cur.execute(account_query, (subscription_id,))
        account_result = cur.fetchone()
        
        if not account_result:
            logger.warning(f"No account found for Subscription ID: {subscription_id}")
            return {
                'subscription_id': subscription_id,
                'customer_name': 'Unknown Customer',
                'account_id': None,
                'found': False,
                'error': f'No account found for subscription {subscription_id}'
            }
        
        account_id = account_result['ACCOUNT_ID_C']
        customer_name = account_result['BU_NAME']
        technology = account_result.get('TECHNOLOGY_C', 'Unknown')
        sub_technology = account_result.get('SUB_TECHNOLOGY_C', 'Unknown')
        status = account_result.get('STATUS_C', 'Unknown')
        
        logger.info(f"[[OK]] Found account: {customer_name} (ID: {account_id})")
        logger.info(f"[[DATA]] Technology: {technology} | Sub-Technology: {sub_technology} | Status: {status}")
        
        # Set up date filters with parameterized queries
        date_filter_task = "AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())"
        date_filter_pulse_priority = "AND DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())"
        
        # Fetch all related data
        logger.info(f"[[CHART]] Fetching adoption barriers...")
        ab_query = f"""
        SELECT *, 'Adoption Barrier' as RECORD_SOURCE 
        FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW 
        WHERE record_type_id = '0122T000000GJfTQAW' 
        AND ACCOUNT_ID_C = %s 
        {date_filter_task}
        """
        adoption_barriers = cur.execute(ab_query, (account_id, days)).fetchall()
        
        logger.info(f"[[LIST]] Fetching action plans...")
        ap_query = f"""
        SELECT *, 'Action Plan' as RECORD_SOURCE 
        FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW 
        WHERE record_type_id = '0122T000000QHBGQA4' 
        AND ACCOUNT_ID_C = %s 
        {date_filter_task}
        """
        action_plans = cur.execute(ap_query, (account_id, days)).fetchall()
        
        logger.info(f"[EMOJI] Fetching customer pulse...")
        cp_query = f"""
        SELECT *, 'Customer Pulse' as RECORD_SOURCE 
        FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C 
        WHERE ACCOUNT__C = %s 
        {date_filter_pulse_priority}
        """
        customer_pulse = cur.execute(cp_query, (account_id, days)).fetchall()
        
        logger.info(f"[[BULLSEYE]] Fetching success priorities...")
        sp_query = f"""
        SELECT *, 'Success Priority' as RECORD_SOURCE 
        FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C 
        WHERE RELATED_CUSTOMER__C = %s 
        {date_filter_pulse_priority}
        """
        success_priorities = cur.execute(sp_query, (account_id, days)).fetchall()
        
        # Get team information (CSSM_* columns may not exist in all dsm_assignment_data schemas)
        team_data = []
        try:
            logger.info(f"[EMOJI] Fetching team information...")
            team_query = """
            SELECT CSSM_EMAIL, CSSM_NAME, CSSM_MANAGER, CSSM_MANAGER_EMAIL
            FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data
            WHERE SUBSCRIPTION_ID = %s
            """
            team_data = cur.execute(team_query, (subscription_id,)).fetchall()
        except Exception as team_err:
            logger.info(f"Team/CSSM columns not available in dsm_assignment_data: {team_err}")
            team_data = []

        # Compile results (include cssm_email for single-customer renewal manager fallback)
        subscription_data = {
            'subscription_id': subscription_id,
            'customer_name': customer_name,
            'account_id': account_id,
            'cssm_email': team_data[0].get('CSSM_EMAIL') if team_data else None,
            'technology': technology,
            'sub_technology': sub_technology,
            'status': status,
            'found': True,
            'analysis_period_days': days,
            'team_data': team_data,
            'adoption_barriers': adoption_barriers,
            'action_plans': action_plans,
            'customer_pulse': customer_pulse,
            'success_priorities': success_priorities,
            'total_records': len(adoption_barriers) + len(action_plans) + len(customer_pulse) + len(success_priorities),
            'summary': {
                'adoption_barriers_count': len(adoption_barriers),
                'action_plans_count': len(action_plans),
                'customer_pulse_count': len(customer_pulse),
                'success_priorities_count': len(success_priorities),
                'team_members_count': len(team_data)
            }
        }
        
        logger.info(f"[[OK]] Subscription data fetch complete:")
        logger.info(f"   [[DATA]] Total records: {subscription_data['total_records']}")
        logger.info(f"   [EMOJI] Adoption barriers: {len(adoption_barriers)}")
        logger.info(f"   [[LIST]] Action plans: {len(action_plans)}")
        logger.info(f"   [EMOJI] Customer pulse: {len(customer_pulse)}")
        logger.info(f"   [[BULLSEYE]] Success priorities: {len(success_priorities)}")
        logger.info(f"   [EMOJI] Team members: {len(team_data)}")
        
        return subscription_data
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error fetching subscription data: {e}")
        return {
            'subscription_id': subscription_id,
            'customer_name': 'Error',
            'account_id': None,
            'found': False,
            'error': str(e)
        }
    finally:
        try:
            cur.close()
        except Exception:
            pass
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def search_subscriptions_by_customer(customer_name: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search for subscriptions by customer name
    
    Args:
        customer_name: Customer name to search for
        limit: Maximum number of results to return
        
    Returns:
        List of subscription records
    """
    ctx = None
    try:
        logger.info(f"[[SEARCH]] Searching subscriptions for customer: {customer_name}")
        
        ctx = _connect_with_keeper()
        cur = ctx.cursor(snowflake.connector.DictCursor)
        
        # Search for subscriptions by customer name (case-insensitive, partial match).
        # Use only columns that exist in all dsm_assignment_data environments (no TECHNOLOGY_C/CSSM_* - schema varies).
        search_query = """
        SELECT SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME
        FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data
        WHERE UPPER(BU_NAME) LIKE UPPER(%s)
        ORDER BY BU_NAME
        LIMIT %s
        """
        
        cur.execute(search_query, (f'%{customer_name}%', limit))
        results = cur.fetchall()
        
        logger.info(f"[[OK]] Found {len(results)} subscriptions for customer: {customer_name}")
        return results
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error searching subscriptions: {e}")
        return []
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def get_subscription_renewal_risk(subscription_id: str, days: int = 90) -> Dict[str, Any]:
    """
    Calculate renewal risk specifically for a subscription
    
    Args:
        subscription_id: The subscription ID to analyze
        days: Number of days to look back for data
        
    Returns:
        Renewal risk analysis for the subscription
    """
    try:
        logger.info(f"[[BULLSEYE]] Calculating renewal risk for subscription: {subscription_id}")
        
        # Get subscription data
        sub_data = fetch_subscription_data(subscription_id, days)
        
        if not sub_data['found']:
            return {
                'subscription_id': subscription_id,
                'error': sub_data.get('error', 'Subscription not found'),
                'risk_score': 0,
                'risk_level': 'UNKNOWN'
            }
        
        # Convert to DataFrames for analysis
        ab_df = pd.DataFrame(sub_data['adoption_barriers']) if sub_data['adoption_barriers'] else pd.DataFrame()
        ap_df = pd.DataFrame(sub_data['action_plans']) if sub_data['action_plans'] else pd.DataFrame()
        cp_df = pd.DataFrame(sub_data['customer_pulse']) if sub_data['customer_pulse'] else pd.DataFrame()
        
        # Calculate risk components (using existing logic)
        risk_components = {}
        
        # Adoption barrier risk
        if not ab_df.empty:
            barrier_count = len(ab_df)
            critical_barriers = len(ab_df[ab_df['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)]) if 'SEVERITY_C' in ab_df.columns else 0
            open_barriers = len(ab_df[ab_df['AB_STATUS_C'].astype(str).str.contains('Open|New', case=False, na=False)]) if 'AB_STATUS_C' in ab_df.columns else 0
            
            ab_risk = min(barrier_count * 0.5 + critical_barriers * 1.5 + open_barriers * 0.3, 10)
            risk_components['adoption_barriers'] = {
                'score': ab_risk,
                'count': barrier_count,
                'critical_count': critical_barriers,
                'open_count': open_barriers
            }
        else:
            risk_components['adoption_barriers'] = {'score': 0, 'count': 0, 'critical_count': 0, 'open_count': 0}
        
        # Customer pulse risk
        if not cp_df.empty:
            poor_pulse = len(cp_df[cp_df['PULSE_RATING__C'].astype(str).str.contains('Poor|Bad', case=False, na=False)]) if 'PULSE_RATING__C' in cp_df.columns else 0
            pulse_risk = min(poor_pulse * 2.0, 10)
            risk_components['customer_pulse'] = {
                'score': pulse_risk,
                'count': len(cp_df),
                'poor_count': poor_pulse
            }
        else:
            risk_components['customer_pulse'] = {'score': 0, 'count': 0, 'poor_count': 0}
        
        # Action plan risk (unresolved plans indicate issues)
        if not ap_df.empty:
            unresolved_plans = len(ap_df[ap_df['STATUS_C'].astype(str).str.contains('Open|New|In Progress', case=False, na=False)]) if 'STATUS_C' in ap_df.columns else 0
            plan_risk = min(unresolved_plans * 0.5, 10)
            risk_components['action_plans'] = {
                'score': plan_risk,
                'count': len(ap_df),
                'unresolved_count': unresolved_plans
            }
        else:
            risk_components['action_plans'] = {'score': 0, 'count': 0, 'unresolved_count': 0}
        
        # Calculate overall risk score
        overall_risk = (
            risk_components['adoption_barriers']['score'] * 0.4 +
            risk_components['customer_pulse']['score'] * 0.3 +
            risk_components['action_plans']['score'] * 0.3
        )
        
        # Determine risk level
        if overall_risk >= 7:
            risk_level = "HIGH"
        elif overall_risk >= 4:
            risk_level = "MODERATE"
        else:
            risk_level = "LOW"
        
        # Generate recommendations
        recommendations = []
        if overall_risk >= 7:
            recommendations.extend([
                "Schedule immediate executive-level customer meeting",
                "Assign dedicated Customer Success Manager",
                "Create emergency adoption plan with weekly reviews"
            ])
        elif overall_risk >= 4:
            recommendations.extend([
                "Increase touch frequency to bi-weekly check-ins",
                "Provide targeted training and enablement resources",
                "Address open adoption barriers within 60 days"
            ])
        else:
            recommendations.extend([
                "Continue current engagement model",
                "Schedule quarterly business review"
            ])
        
        renewal_analysis = {
            'subscription_id': subscription_id,
            'customer_name': sub_data['customer_name'],
            'account_id': sub_data['account_id'],
            'technology': sub_data['technology'],
            'sub_technology': sub_data['sub_technology'],
            'status': sub_data['status'],
            'analysis_period_days': days,
            'overall_risk_score': round(overall_risk, 1),
            'risk_level': risk_level,
            'risk_components': risk_components,
            'recommendations': recommendations,
            'summary': sub_data['summary'],
            'analysis_date': datetime.now().isoformat()
        }
        
        logger.info(f"[[OK]] Renewal risk analysis complete for {sub_data['customer_name']}: {risk_level} risk ({overall_risk:.1f}/10)")
        
        return renewal_analysis
        
    except Exception as e:
        logger.error(f"[[ERROR]] Error calculating subscription renewal risk: {e}")
        return {
            'subscription_id': subscription_id,
            'error': str(e),
            'risk_score': 0,
            'risk_level': 'ERROR'
        }


def get_subscriptions_for_team(ctx, emails: List[str]) -> pd.DataFrame:
    """Gets all subscriptions and associated accounts for a list of CSSM emails with proper resource management."""
    if ctx is None:
        return pd.DataFrame()
    if not emails:
        return pd.DataFrame()
    
    cur = None
    try:
        cur = ctx.cursor()
        email_col = "PRIMARY_DSM_EMAIL" # Based on previous discovery
        
        # Use proper parameterized query to prevent SQL injection
        placeholders = ','.join(['%s'] * len(emails))
        sql = f"""SELECT DISTINCT SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME, {email_col} AS CSSM_EMAIL
                  FROM {DSM_TABLE}
                  WHERE {email_col} IN ({placeholders})"""
        
        cur.execute(sql, emails)
        rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=[c[0] for c in cur.description])
        return df
    except Exception as e:
        logger.error(f"Error fetching subscriptions: {e}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

def fetch_arr_data(ctx, account_ids: List[str]) -> pd.DataFrame:
    """
    Fetch ARR/revenue data for accounts from Snowflake
    
    NOTE: ARR fields may not exist in DSM table - this function attempts to query them
    but gracefully falls back to basic customer data if they don't exist.
    """
    if ctx is None or not account_ids:
        return pd.DataFrame()

    def _normalize_arr_df(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=[
                'ACCOUNT_ID_C', 'BU_NAME', 'SUBSCRIPTION_ID', 'TECHNOLOGY_C', 'SUB_TECHNOLOGY_C',
                'STATUS_C', 'CSSM_EMAIL', 'CSSM_NAME', 'CSSM_MANAGER',
                'ANNUAL_CONTRACT_VALUE', 'MRR', 'TCV', 'LICENSE_COUNT'
            ])
        normalized = df.copy()
        text_defaults = {
            'ACCOUNT_ID_C': '', 'BU_NAME': '', 'SUBSCRIPTION_ID': '',
            'TECHNOLOGY_C': 'Unknown', 'SUB_TECHNOLOGY_C': 'Unknown',
            'STATUS_C': '', 'CSSM_EMAIL': '', 'CSSM_NAME': '', 'CSSM_MANAGER': ''
        }
        numeric_defaults = {
            'ANNUAL_CONTRACT_VALUE': 0, 'MRR': 0, 'TCV': 0, 'LICENSE_COUNT': 0
        }
        for col, default in text_defaults.items():
            if col not in normalized.columns:
                normalized[col] = default
        for col, default in numeric_defaults.items():
            if col not in normalized.columns:
                normalized[col] = default
            normalized[col] = pd.to_numeric(normalized[col], errors='coerce').fillna(default)
        return normalized

    # Normalize/clean IDs up-front and cap batch size for query stability.
    cleaned_ids = []
    for raw_id in account_ids:
        if raw_id is None:
            continue
        aid = str(raw_id).strip()
        if aid:
            cleaned_ids.append(aid)
    cleaned_ids = list(dict.fromkeys(cleaned_ids))  # preserve order + dedupe
    if not cleaned_ids:
        return pd.DataFrame()

    batch_size = 500
    if len(cleaned_ids) > batch_size:
        frames = []
        for i in range(0, len(cleaned_ids), batch_size):
            batch_df = fetch_arr_data(ctx, cleaned_ids[i:i + batch_size])
            if batch_df is not None and not batch_df.empty:
                frames.append(_normalize_arr_df(batch_df))
        if not frames:
            return _normalize_arr_df(pd.DataFrame())
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(subset=['ACCOUNT_ID_C', 'SUBSCRIPTION_ID'], keep='first')
        return _normalize_arr_df(merged)

    cur = None
    try:
        logger.debug(f"Starting ARR query for {len(cleaned_ids)} accounts...")
        cur = ctx.cursor()
        placeholders = ','.join(['%s'] * len(cleaned_ids))

        # First try: Attempt to query with ARR fields
        sql_with_arr = f"""
        SELECT DISTINCT
          ACCOUNT_ID_C,
          BU_NAME,
          SUBSCRIPTION_ID,
          TECHNOLOGY_C,
          SUB_TECHNOLOGY_C,
          STATUS_C,
          CSSM_EMAIL,
          CSSM_NAME,
          CSSM_MANAGER,
          COALESCE(ANNUAL_CONTRACT_VALUE_C, 0) AS ANNUAL_CONTRACT_VALUE,
          COALESCE(MONTHLY_RECURRING_REVENUE_C, 0) AS MRR,
          COALESCE(TOTAL_CONTRACT_VALUE_C, 0) AS TCV,
          COALESCE(LICENSE_COUNT_C, 0) AS LICENSE_COUNT
        FROM {DSM_TABLE}
        WHERE ACCOUNT_ID_C IN ({placeholders})
          AND STATUS_C = 'ACTIVE'
        """

        logger.debug("Attempting to execute ARR SQL query with financial fields...")
        try:
            cur.execute(sql_with_arr, cleaned_ids)
            rows = cur.fetchall()
            if not rows:
                return _normalize_arr_df(pd.DataFrame())
            cols = [c[0] for c in cur.description]
            return _normalize_arr_df(pd.DataFrame(rows, columns=cols))
        except Exception as col_error:
            logger.info(f"ARR columns not found in table ({col_error}), using basic customer data instead")
            try:
                sql_basic = f"""
                SELECT DISTINCT
                  ACCOUNT_ID_C,
                  BU_NAME,
                  SUBSCRIPTION_ID,
                  COALESCE(TECHNOLOGY_C, 'Unknown') AS TECHNOLOGY_C,
                  COALESCE(SUB_TECHNOLOGY_C, 'Unknown') AS SUB_TECHNOLOGY_C,
                  STATUS_C,
                  CSSM_EMAIL,
                  CSSM_NAME,
                  CSSM_MANAGER
                FROM {DSM_TABLE}
                WHERE ACCOUNT_ID_C IN ({placeholders})
                  AND STATUS_C = 'ACTIVE'
                """
                cur.execute(sql_basic, cleaned_ids)
            except Exception as tech_err:
                logger.info(f"Technology columns not in schema ({tech_err}), using minimal customer data")
                try:
                    sql_minimal = f"""
                    SELECT DISTINCT
                      ACCOUNT_ID_C,
                      BU_NAME,
                      SUBSCRIPTION_ID,
                      STATUS_C,
                      CSSM_EMAIL,
                      CSSM_NAME,
                      CSSM_MANAGER
                    FROM {DSM_TABLE}
                    WHERE ACCOUNT_ID_C IN ({placeholders})
                      AND STATUS_C = 'ACTIVE'
                    """
                    cur.execute(sql_minimal, cleaned_ids)
                except Exception as cssm_err:
                    logger.info(f"CSSM columns not in schema ({cssm_err}), using ultra-minimal customer data")
                    sql_ultra = f"""
                    SELECT DISTINCT
                      ACCOUNT_ID_C,
                      BU_NAME,
                      SUBSCRIPTION_ID,
                      STATUS_C
                    FROM {DSM_TABLE}
                    WHERE ACCOUNT_ID_C IN ({placeholders})
                      AND STATUS_C = 'ACTIVE'
                    """
                    cur.execute(sql_ultra, cleaned_ids)

            rows = cur.fetchall()
            if not rows:
                return _normalize_arr_df(pd.DataFrame())
            cols = [c[0] for c in cur.description]
            return _normalize_arr_df(pd.DataFrame(rows, columns=cols))

    except Exception as e:
        import traceback
        logger.error(f"Error fetching ARR data: {e}\n{traceback.format_exc()}")
        return _normalize_arr_df(pd.DataFrame())
    finally:
        if cur:
            cur.close()

def fetch_support_cases_snowflake(ctx, account_ids: List[str], days: int, limit: int = 1000) -> pd.DataFrame:
    """Fetch support/TAC cases from Snowflake by account IDs.
    Tries SUPPORT_CASES; if that fails (table/perms), tries join via dsm_assignment_data.
    Used when no CSOne file is uploaded so renewal report can still show case counts.
    Returns empty DataFrame on error or if table/columns are missing.
    """
    if not ctx or not account_ids:
        return pd.DataFrame()
    if not isinstance(days, int) or days < 1 or days > 365:
        logger.warning(f"[[RENEWAL]] Invalid days parameter: {days}")
        return pd.DataFrame()
    if not isinstance(limit, int) or limit < 1:
        logger.warning(f"[[RENEWAL]] Invalid limit parameter: {limit}")
        return pd.DataFrame()
    limit = min(limit, 10000)

    def _normalize_cases_df(df: pd.DataFrame) -> pd.DataFrame:
        expected = ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'SEVERITY']
        if df is None or df.empty:
            return pd.DataFrame(columns=expected)
        normalized = df.copy()
        for col in expected:
            if col not in normalized.columns:
                normalized[col] = None
        return normalized[expected]

    # Normalize IDs and dedupe
    account_ids_clean = []
    for a in account_ids:
        if a is None:
            continue
        value = str(a).strip()
        if value:
            account_ids_clean.append(value)
    account_ids_clean = list(dict.fromkeys(account_ids_clean))
    if not account_ids_clean:
        return pd.DataFrame()

    # Batch large IN lists to avoid oversized query payloads.
    batch_size = 500
    if len(account_ids_clean) > batch_size:
        frames = []
        for i in range(0, len(account_ids_clean), batch_size):
            batch_df = fetch_support_cases_snowflake(
                ctx, account_ids_clean[i:i + batch_size], days, limit=limit
            )
            if batch_df is not None and not batch_df.empty:
                frames.append(_normalize_cases_df(batch_df))
        if not frames:
            return _normalize_cases_df(pd.DataFrame())
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(subset=['CASE_ID', 'ACCOUNT_ID'], keep='first')
        if 'CREATED_DATE' in merged.columns:
            merged = merged.sort_values('CREATED_DATE', ascending=False, na_position='last')
        return _normalize_cases_df(merged.head(limit))

    cur = None
    placeholders = ','.join(['%s'] * len(account_ids_clean))
    params = list(account_ids_clean) + [days, limit]

    # Try 1: SUPPORT_CASES with ACCOUNT_ID IN (...)
    try:
        logger.info(f"[[RENEWAL]] Attempting Snowflake support cases fetch for {len(account_ids_clean)} accounts (SUPPORT_CASES.ACCOUNT_ID)...")
        sql1 = """
        SELECT s.CASE_ID, s.ACCOUNT_ID, s.SUBJECT, s.STATUS, s.CREATED_DATE, s.SEVERITY
        FROM CX_DB.CX_SWSSBST_BR.SUPPORT_CASES s
        WHERE s.ACCOUNT_ID IN (""" + placeholders + """)
          AND s.CREATED_DATE >= DATEADD(day, -%s, CURRENT_DATE())
        ORDER BY s.CREATED_DATE DESC
        LIMIT %s
        """
        cur = ctx.cursor()
        cur.execute(sql1, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description] if cur.description else ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'SEVERITY']
        if cur:
            cur.close()
            cur = None
        if rows:
            logger.info(f"[[RENEWAL]] Snowflake SUPPORT_CASES returned {len(rows)} support cases")
            return _normalize_cases_df(pd.DataFrame(rows, columns=cols))
        logger.info(f"[[RENEWAL]] Snowflake SUPPORT_CASES returned 0 rows for scope")
        return _normalize_cases_df(pd.DataFrame())
    except Exception as e1:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
            cur = None
        logger.warning(f"[[RENEWAL]] Snowflake SUPPORT_CASES (ACCOUNT_ID) failed: {str(e1).strip()}")

    # Try 2: SUPPORT_CASES with ACCOUNT_ID_C (some schemas use _C suffix)
    try:
        logger.info(f"[[RENEWAL]] Trying SUPPORT_CASES.ACCOUNT_ID_C for {len(account_ids_clean)} accounts...")
        sql2 = """
        SELECT CASE_ID, ACCOUNT_ID_C AS ACCOUNT_ID, SUBJECT, STATUS, CREATED_DATE, SEVERITY
        FROM CX_DB.CX_SWSSBST_BR.SUPPORT_CASES
        WHERE ACCOUNT_ID_C IN (""" + placeholders + """)
          AND CREATED_DATE >= DATEADD(day, -%s, CURRENT_DATE())
        ORDER BY CREATED_DATE DESC
        LIMIT %s
        """
        cur = ctx.cursor()
        cur.execute(sql2, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description] if cur.description else ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'SEVERITY']
        if cur:
            cur.close()
            cur = None
        if rows:
            logger.info(f"[[RENEWAL]] Snowflake SUPPORT_CASES (ACCOUNT_ID_C) returned {len(rows)} support cases")
            return _normalize_cases_df(pd.DataFrame(rows, columns=cols))
    except Exception as e2:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
            cur = None
        logger.warning(f"[[RENEWAL]] Snowflake SUPPORT_CASES (ACCOUNT_ID_C) failed: {str(e2).strip()}")

    # Try 3: Join via dsm_assignment_data (same table we use for team subs)
    try:
        logger.info(f"[[RENEWAL]] Trying support cases via JOIN to dsm_assignment_data...")
        sql3 = """
        SELECT s.CASE_ID, s.ACCOUNT_ID, s.SUBJECT, s.STATUS, s.CREATED_DATE, s.SEVERITY
        FROM CX_DB.CX_SWSSBST_BR.SUPPORT_CASES s
        INNER JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data d ON TRIM(s.ACCOUNT_ID) = TRIM(d.ACCOUNT_ID_C)
        WHERE d.ACCOUNT_ID_C IN (""" + placeholders + """)
          AND s.CREATED_DATE >= DATEADD(day, -%s, CURRENT_DATE())
        ORDER BY s.CREATED_DATE DESC
        LIMIT %s
        """
        cur = ctx.cursor()
        cur.execute(sql3, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description] if cur.description else ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'SEVERITY']
        if cur:
            cur.close()
            cur = None
        if rows:
            logger.info(f"[[RENEWAL]] Snowflake support cases (via dsm join) returned {len(rows)} cases")
            return _normalize_cases_df(pd.DataFrame(rows, columns=cols))
    except Exception as e3:
        if cur:
            try:
                cur.close()
            except Exception:
                pass
        logger.warning(f"[[RENEWAL]] Snowflake support cases via dsm join failed: {str(e3).strip()}")

    return _normalize_cases_df(pd.DataFrame())


def fetch_adoption_barriers(ctx, account_ids: List[str], days: int) -> pd.DataFrame:
    """Fetch adoption barriers with proper resource management and input validation"""
    if ctx is None:
        return pd.DataFrame()
    if not account_ids: 
        return pd.DataFrame()
    
    # Validate input parameters
    if days < 1 or days > 365:
        raise ValueError("Days parameter must be between 1 and 365")
    
    cur = None
    try:
        logger.debug(f"Starting adoption barriers query for {len(account_ids)} accounts...")
        cur = ctx.cursor()
        date_expr = "DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))"
        
        # Use proper parameterized query to prevent SQL injection
        placeholders = ','.join(['%s'] * len(account_ids))
        sql = f"""
        SELECT *
        FROM {AB_TABLE}
        WHERE ACCOUNT_ID_C IN ({placeholders})
          AND {date_expr} >= DATEADD(day, -%s, CURRENT_DATE())
          AND (RECORD_TYPE_ID = '0122T000000GJfTQAW' OR RECORD_TYPE_ID IS NULL)
        """
        logger.debug("Executing SQL query...")
        cur.execute(sql, [*account_ids, days])
        logger.debug("Query executed, fetching results...")
        rows = cur.fetchall()
        logger.debug(f"Fetched {len(rows)} rows from adoption barriers query")
        if not rows: 
            return pd.DataFrame()
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        import traceback
        logger.error(f"Error fetching adoption barriers: {e}\n{traceback.format_exc()}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

def load_and_merge_data_for_subscription(subscription_id: str, days: int, csone_data: list):
    """Load and merge CSConsole data for a specific subscription ID with proper SQL injection protection"""
    ctx = _connect_with_keeper()
    cur = ctx.cursor(snowflake.connector.DictCursor)
    
    try:
        sub_id_column_dsm = 'SUBSCRIPTION_ID'
        account_id_column_dsm = 'ACCOUNT_ID_C'
        
        logging.info(f"Fetching Account ID for Subscription ID '{subscription_id}'...")
        
        # Use parameterized query to prevent SQL injection
        # Column names are hardcoded constants, so this is safe
        account_query = "SELECT ACCOUNT_ID_C, BU_NAME FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data WHERE SUBSCRIPTION_ID_C = %s LIMIT 1"
        cur.execute(account_query, (subscription_id,))
        account_result = cur.fetchone()
        
        if not account_result:
            logging.warning(f"No account found for Subscription ID '{subscription_id}' in dsm_assignment_data. Only Excel data will be used.")
            customer_name = "Unknown (Not found in CSConsole)"
            return customer_name, csone_data
            
        account_id = account_result[account_id_column_dsm]
        customer_name = account_result['BU_NAME']
        logging.info(f"Found Account ID: {account_id} for Customer: {customer_name}. Fetching related records for the last {days} days...")

        # Use parameterized queries to prevent SQL injection
        ap_query = "SELECT *, 'Action Plan' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW WHERE record_type_id = '0122T000000QHBGQA4' AND ACCOUNT_ID_C = %s AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())"
        ab_query = "SELECT *, 'Adoption Barrier' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW WHERE record_type_id = '0122T000000GJfTQAW' AND ACCOUNT_ID_C = %s AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())"
        cp_query = "SELECT *, 'Customer Pulse' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C WHERE ACCOUNT__C = %s AND DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())"
        sp_query = "SELECT *, 'Success Priority' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C WHERE RELATED_CUSTOMER__C = %s AND DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())"

        action_plans = cur.execute(ap_query, (account_id, days)).fetchall()
        adoption_barriers = cur.execute(ab_query, (account_id, days)).fetchall()
        customer_pulse = cur.execute(cp_query, (account_id, days)).fetchall()
        success_priorities = cur.execute(sp_query, (account_id, days)).fetchall()
        
        logging.info(f"Found {len(action_plans)} action plans, {len(adoption_barriers)} adoption barriers, {len(customer_pulse)} pulse records, and {len(success_priorities)} success priorities.")

        all_records = csone_data + action_plans + adoption_barriers + customer_pulse + success_priorities
        return customer_name, all_records
        
    except Exception as e:
        logging.error(f"Error in load_and_merge_data_for_subscription: {e}")
        return "Error", csone_data
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass

def fetch_csconsole_action_plans(ctx, account_ids: List[str], days: int) -> pd.DataFrame:
    """Fetch Action Plans from CSConsole with proper resource management"""
    if ctx is None:
        return pd.DataFrame()
    if not account_ids: 
        return pd.DataFrame()
    
    cur = None
    try:
        cur = ctx.cursor()
        placeholders = ','.join(['%s'] * len(account_ids))
        sql = f"""
        SELECT ap.*, dsm.BU_NAME, 'Action Plan' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW ap
        LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm ON ap.ACCOUNT_ID_C = dsm.ACCOUNT_ID_C
        WHERE ap.record_type_id = '0122T000000QHBGQA4' 
          AND ap.ACCOUNT_ID_C IN ({placeholders})
          AND DATE(ap.CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())
        """
        cur.execute(sql, [*account_ids, days])
        rows = cur.fetchall()
        if not rows: 
            return pd.DataFrame()
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        logger.error(f"Error fetching action plans: {e}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

def fetch_csconsole_customer_pulse(ctx, account_ids: List[str], days: int) -> pd.DataFrame:
    """Fetch Customer Pulse records from CSConsole with proper resource management"""
    if ctx is None:
        return pd.DataFrame()
    if not account_ids: 
        return pd.DataFrame()
    
    cur = None
    try:
        cur = ctx.cursor()
        placeholders = ','.join(['%s'] * len(account_ids))
        sql = f"""
        SELECT cp.*, dsm.BU_NAME, 'Customer Pulse' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C cp
        LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm ON cp.ACCOUNT__C = dsm.ACCOUNT_ID_C
        WHERE cp.ACCOUNT__C IN ({placeholders})
          AND DATE(cp.CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())
        """
        cur.execute(sql, [*account_ids, days])
        rows = cur.fetchall()
        if not rows: 
            return pd.DataFrame()
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        logger.error(f"Error fetching customer pulse: {e}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

def fetch_csconsole_success_priorities(ctx, account_ids: List[str], days: int) -> pd.DataFrame:
    """Fetch Success Priorities from CSConsole with proper resource management"""
    if ctx is None:
        return pd.DataFrame()
    if not account_ids: 
        return pd.DataFrame()
    
    cur = None
    try:
        cur = ctx.cursor()
        placeholders = ','.join(['%s'] * len(account_ids))
        sql = f"""
        SELECT *, 'Success Priority' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C 
        WHERE RELATED_CUSTOMER__C IN ({placeholders})
          AND DATE(CREATEDDATE) >= DATEADD(day, -%s, CURRENT_DATE())
        """
        cur.execute(sql, [*account_ids, days])
        rows = cur.fetchall()
        if not rows: 
            return pd.DataFrame()
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        logger.error(f"Error fetching success priorities: {e}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

def fetch_csconsole_adoption_barriers(ctx, account_ids: List[str], days: int) -> pd.DataFrame:
    """Fetch Adoption Barriers from CSConsole with proper resource management"""
    if ctx is None:
        return pd.DataFrame()
    if not account_ids: 
        return pd.DataFrame()
    
    cur = None
    try:
        cur = ctx.cursor()
        placeholders = ','.join(['%s'] * len(account_ids))
        sql = f"""
        SELECT *, 'Adoption Barrier' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW 
        WHERE record_type_id = '0122T000000GJfTQAW' 
          AND ACCOUNT_ID_C IN ({placeholders})
          AND DATE(CREATED_DATE) >= DATEADD(day, -%s, CURRENT_DATE())
        """
        cur.execute(sql, [*account_ids, days])
        rows = cur.fetchall()
        if not rows: 
            return pd.DataFrame()
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        logger.error(f"Error fetching adoption barriers: {e}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

# --------------------------- External Intelligence ---------------------------
HELP_URLS = [
    "https://help.webex.com/en-us/article/mqkve8/Webex-App-%7C-Release-notes",
    "https://help.webex.com/en-us/article/8dmbcr/What's-New-in-Webex-Suite",
    "https://help.webex.com/article/bsmvpdb/Webex-App-%7C-Known-issues",
]

def fetch_help_webex_bugs(timeout=25) -> List[Dict[str,str]]:
    """Fetch known bugs from help.webex.com with robust error handling and multiple strategies"""
    all_bugs = {}
    
    # Strategy 1: Search known help URLs
    for url in HELP_URLS:
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            text = soup.get_text(" ")
            
            # Look for various bug patterns - PRIORITIZE CSC format
            # First, look for CSC format (most important)
            csc_pattern = r"\bCSC[a-zA-Z0-9]{6,10}\b"
            for match in re.finditer(csc_pattern, text, re.IGNORECASE):
                bug_id = match.group(0).upper().strip()
                if bug_id and len(bug_id) > 3:
                    all_bugs[bug_id] = {
                        "bug_id": bug_id,
                        "source_url": url,
                        "title": f"Known Issue: {bug_id}",
                        "source": "help.webex.com",
                        "discovered_at": time.strftime('%Y-%m-%d %H:%M:%S')
                    }
            
            # Only look for other formats if we haven't found CSC format
            # Skip DEFECT/DEF patterns to avoid creating non-standard DEF- format
            other_patterns = [
                r"\bBUG[_-]?(\d+)\b",         # BUG-123 format (only if no CSC found)
                r"\bISSUE[_-]?(\d+)\b",       # ISSUE-123 format (only if no CSC found)
                # NOTE: Removed DEFECT pattern to avoid creating DEF- format
                # r"\bDEFECT[_-]?(\d+)\b",   # DEFECT-123 format - SKIPPED
                r"\bLIMITATION[_-]?(\d+)\b"   # LIMITATION-123 format (only if no CSC found)
            ]
            
            for pattern in other_patterns:
                for match in re.findall(pattern, text, re.IGNORECASE):
                    if isinstance(match, tuple):
                        bug_id = match[0] if match[0] else match
                    else:
                        bug_id = match
                    
                    # Clean and standardize bug ID
                    bug_id = bug_id.upper().strip()
                    # Only add if it's not already a CSC format and not a numeric-only ID
                    # Skip numeric-only IDs to avoid creating non-standard formats
                    if bug_id and len(bug_id) > 3 and not bug_id.isdigit():
                        # Check if this might be a CSC that we missed
                        if not bug_id.startswith('CSC'):
                            all_bugs[bug_id] = {
                                "bug_id": bug_id,
                                "source_url": url,
                                "title": f"Known Issue: {bug_id}",
                                "source": "help.webex.com",
                                "discovered_at": time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                        
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            continue
        except Exception as e:
            logger.warning(f"Unexpected error fetching {url}: {e}")
            continue
    
    # Strategy 2: Search for specific bug-related content
    try:
        search_terms = ["known issues", "software bugs", "defects", "limitations", "troubleshooting"]
        for term in search_terms:
            try:
                search_url = f"https://help.webex.com/en-us/search?q={term}"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                r = requests.get(search_url, headers=headers, timeout=timeout)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                
                # Look for bug-related links
                bug_links = soup.find_all('a', href=re.compile(r'help\.webex\.com.*(bug|issue|defect|limitation)', re.IGNORECASE))
                
                for link in bug_links[:3]:  # Limit to 3 per search term
                    try:
                        href = link.get('href', '')
                        if not href.startswith('http'):
                            href = f"https://help.webex.com{href}"
                        
                        # Extract bug ID from URL or text
                        bug_id = re.search(r'(bug|issue|defect|limitation)[_-]?(\d+)', href, re.IGNORECASE)
                        if not bug_id:
                            bug_id = re.search(r'(\d+)', link.get_text())
                        
                        if bug_id:
                            bug_id = bug_id.group(2) if len(bug_id.groups()) >= 2 else bug_id.group(1)
                            # Only use numeric-only IDs if we can't find a proper CSC format
                            # Prefer to skip rather than create non-standard formats like BUG_ or DEF-
                            if not bug_id.startswith(('CSC', 'BUG', 'ISSUE', 'DEF')):
                                # Try to find CSC format in the link text or URL first
                                csc_match = re.search(r'\bCSC[a-zA-Z0-9]{6,10}\b', link.get_text() + ' ' + href, re.IGNORECASE)
                                if csc_match:
                                    bug_id = csc_match.group(0).upper()
                                else:
                                    # Skip numeric-only IDs to avoid creating non-standard formats
                                    continue
                            else:
                                bug_id = bug_id.upper()
                            
                            all_bugs[bug_id] = {
                                "bug_id": bug_id,
                                "source_url": href,
                                "title": link.get_text().strip(),  # FIXED: No truncation
                                "source": "help.webex.com",
                                "discovered_at": time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                    except Exception as e:
                        logger.warning(f"Error processing bug link: {e}")
                        continue
                
                time.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                logger.warning(f"Error searching for '{term}': {e}")
                continue
                
    except Exception as e:
        logger.warning(f"Error in secondary search strategy: {e}")
    
    # Return unique bugs
    unique_bugs = list(all_bugs.values())
    logger.info(f"Successfully fetched {len(unique_bugs)} software bugs from help.webex.com")

    # Persist bugs to SQLite for historical tracking
    try:
        from incident_storage import store_historical_bugs
        if unique_bugs:
            store_historical_bugs(unique_bugs)
    except ImportError:
        logger.warning("incident_storage not available — bugs will not be persisted")
    except Exception as e:
        logger.warning(f"Error persisting bugs to storage: {e}")

    return unique_bugs

def fetch_status_webex_incident_history_playwright():
    """Use Playwright to fetch historical incidents from status.webex.com/incident/history"""
    logger.info("Fetching historical incidents using Playwright...")
    
    incidents = []
    
    try:
        from playwright.sync_api import sync_playwright
        
        url = "https://status.webex.com/incident/history?lang=en_US"
        
        with sync_playwright() as p:
            # Launch browser
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set user agent
            page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
            
            # Navigate to page
            logger.info(f"Navigating to {url}...")
            page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Wait for content to load
            logger.info("Waiting for dynamic content...")
            page.wait_for_timeout(10000)  # Wait 10 seconds
            
            # Get page content
            content = page.content()
            
            # Save for debugging
            with open('playwright_page_source.html', 'w', encoding='utf-8') as f:
                f.write(content)
            logger.debug(f"Saved page source ({len(content)} chars)")
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            
            # Look for PUB references
            pub_matches = soup.find_all(string=re.compile(r'PUB\d{7}'))
            logger.debug(f"Found {len(pub_matches)} PUB references")
            
            # Look for table rows
            rows = soup.find_all('tr')
            logger.debug(f"Found {len(rows)} table rows")
            
            # Parse incidents from table rows
            for row in rows:
                try:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 4:  # Need at least Change #, Date, Service, Description
                        continue
                    
                    # Extract PUB reference from first cell
                    pub_ref = None
                    change_cell = cells[0]
                    pub_match = re.search(r'(PUB\d{7})', change_cell.get_text())
                    if pub_match:
                        pub_ref = pub_match.group(1)
                    else:
                        continue  # Skip if no PUB reference
                    
                    # Extract other data
                    date_text = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    location_text = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    sector_text = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    service_text = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    description_text = cells[5].get_text(strip=True) if len(cells) > 5 else ""
                    
                    # Determine impact level
                    impact_level = "Low"  # Default to Low
                    desc_lower = description_text.lower()
                    if any(word in desc_lower for word in ["critical", "outage", "down", "unavailable", "failed", "complete"]):
                        impact_level = "High"
                    elif any(word in desc_lower for word in ["degraded", "slow", "intermittent", "partial", "some"]):
                        impact_level = "Medium"
                    elif any(word in desc_lower for word in ["minor", "scheduled", "planned", "maintenance"]):
                        impact_level = "Low"
                    
                    # Create incident
                    incident = {
                        "id": f"https://status.webex.com/incident/history?lang=en_US#{pub_ref}",
                        "pub_id": pub_ref,
                        "title": description_text,
                        "published": date_text,
                        "status": "resolved",
                        "impact_level": impact_level,
                        "source": "status.webex.com/history",
                        "description": description_text,
                        "service": service_text,
                        "location": location_text,
                        "sector": sector_text
                    }
                    
                    incidents.append(incident)
                    logger.debug(f"SUCCESS: {pub_ref} - {description_text[:50]}...")
                    
                except Exception as e:
                    continue
            
            browser.close()
            
        logger.info(f"Successfully parsed {len(incidents)} historical incidents")
        
    except ImportError:
        logger.info("Playwright not available, skipping...")
    except Exception as e:
        logger.warning(f"Playwright failed: {e}")
    
    return incidents

def fetch_status_incidents(timeout=25) -> List[Dict[str,str]]:
    # Import the storage system
    try:
        from incident_storage import get_historical_incidents, store_historical_incidents, get_incident_statistics
        storage_available = True
    except ImportError:
        storage_available = False
        logger.warning("Incident storage not available, using live data only")
    
    data = []
    
    # First, try to get incidents from storage
    if storage_available:
        try:
            stored_incidents = get_historical_incidents(days_back=90, limit=50)
            if stored_incidents:
                for si in stored_incidents:
                    si['_from_storage'] = True
                data.extend(stored_incidents)
                logger.info(f"Loaded {len(stored_incidents)} incidents from historical storage")
                
                stats = get_incident_statistics()
                logger.info(f"Storage contains {stats['total']} total incidents from {len(stats['sources'])} sources")
        except Exception as e:
            logger.warning(f"Error loading from storage: {e}")
    
    # Primary strategy: JSON API (returns up to 50 incidents with full detail)
    json_api_succeeded = False
    try:
        logger.info("Fetching incidents from all-incidents.json API...")
        api_url = "https://status.webex.com/all-incidents.json"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }
        
        r = requests.get(api_url, headers=headers, timeout=timeout)
        r.raise_for_status()
        api_data = r.json()
        incidents_list = api_data.get('incidents', []) if isinstance(api_data, dict) else []
        logger.info(f"JSON API returned {len(incidents_list)} incidents")
        
        impact_map = {'none': 'Low', 'minor': 'Medium', 'major': 'High', 'critical': 'High'}
        
        for inc in incidents_list:
            try:
                inc_id = inc.get('id', '')
                title = inc.get('name', '').strip()
                if not title:
                    continue
                
                inc_status = inc.get('status', 'resolved')
                if inc_status in ('investigating', 'identified', 'monitoring'):
                    mapped_status = 'active'
                else:
                    mapped_status = 'resolved'
                
                impact_raw = inc.get('impact', 'minor')
                impact_level = impact_map.get(impact_raw, 'Medium')
                
                published = inc.get('created_at', '')
                resolved_at = inc.get('resolved_at', '')
                locations = inc.get('locations', '')
                pub_id = inc.get('publicationId', '')
                inc_number = inc.get('incidentNumber', '')
                
                link = f"https://status.webex.com/incident/history?lang=en_US#{inc_id}"
                
                description = ''
                updates = inc.get('incident_updates', [])
                if updates:
                    latest = updates[0]
                    description = latest.get('body', '')[:500]
                
                affected = []
                for comp in inc.get('components', []):
                    cname = comp.get('name', '')
                    if cname:
                        affected.append(cname)
                
                data.append({
                    "id": inc_id,
                    "title": title,
                    "link": link,
                    "published": published,
                    "status": mapped_status,
                    "impact_level": impact_level,
                    "source": "status.webex.com/api",
                    "description": description,
                    "locations": locations,
                    "publication_id": pub_id,
                    "incident_number": inc_number,
                    "resolved_at": resolved_at,
                    "affected_components": ', '.join(affected),
                })
            except Exception as e:
                logger.warning(f"Error processing JSON API incident: {e}")
                continue
        
        json_api_succeeded = True
        logger.info(f"JSON API processed: {len(incidents_list)} incidents added")
        
    except Exception as e:
        logger.warning(f"Error fetching JSON API: {e}")
    
    # Fallback: RSS feed (only if JSON API failed)
    if not json_api_succeeded:
        try:
            logger.info("JSON API failed, falling back to RSS feed...")
            rss_url = "https://status.webex.com/incidents.rss"
            rss_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
            }
            
            r = requests.get(rss_url, headers=rss_headers, timeout=timeout)
            r.raise_for_status()
            
            import feedparser
            feed = feedparser.parse(r.text)
            
            for item in feed.entries[:50]:
                try:
                    title = item.get("title", "").strip()
                    link = item.get("link", "").strip()
                    pub_date_elem = item.get("published", "")
                    
                    if not title or not link:
                        continue
                    
                    published_date = time.strftime('%Y-%m-%d %H:%M:%S')
                    if pub_date_elem:
                        try:
                            pub_date_text = pub_date_elem.strip() if isinstance(pub_date_elem, str) else pub_date_elem.get_text().strip()
                            published_date = pub_date_text
                        except Exception:
                            pass
                    
                    content_to_check = title.lower()
                    description_elem = item.get("description", "")
                    if description_elem:
                        content_to_check += " " + str(description_elem).lower()
                    
                    impact_level = "Low"
                    if any(w in content_to_check for w in ["outage", "down", "unavailable", "critical", "major", "severe"]):
                        impact_level = "High"
                    elif any(w in content_to_check for w in ["degraded", "slow", "intermittent", "partial", "minor"]):
                        impact_level = "Medium"
                    
                    inc_status = "resolved"
                    if any(w in content_to_check for w in ["ongoing", "active", "investigating", "monitoring", "identified"]):
                        inc_status = "active"
                    
                    data.append({
                        "id": link,
                        "title": title,
                        "link": link,
                        "published": published_date,
                        "status": inc_status,
                        "impact_level": impact_level,
                        "source": "status.webex.com/rss"
                    })
                except Exception as e:
                    logger.warning(f"Error processing RSS item: {e}")
                    continue
            
            logger.info(f"RSS feed fallback processed: {len(data)} total incidents")
        except Exception as e:
            logger.warning(f"RSS feed fallback also failed: {e}")

    # Supplement: parse non-maintenance incidents from history.rss
    try:
        logger.info("Supplementing incidents from history.rss...")
        hist_url = "https://status.webex.com/history.rss"
        hist_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        }
        hr = requests.get(hist_url, headers=hist_headers, timeout=timeout)
        hr.raise_for_status()

        import feedparser
        hist_feed = feedparser.parse(hr.text)
        hist_added = 0

        for item in hist_feed.entries:
            try:
                title = item.get('title', '').strip()
                if not title or 'maintenance' in title.lower():
                    continue
                link = item.get('link', '').strip()
                if not link:
                    continue

                # Extract the hash fragment from the link to match JSON API IDs
                # e.g. "https://...#ec864820..." -> "ec864820..."
                rss_id = link
                if '#' in link:
                    fragment = link.split('#', 1)[1]
                    if fragment:
                        rss_id = fragment

                published = ''
                if hasattr(item, 'published_parsed') and item.published_parsed:
                    try:
                        published = datetime(*item.published_parsed[:6]).strftime('%Y-%m-%dT%H:%M:%SZ')
                    except Exception:
                        published = item.get('published', '')
                else:
                    published = item.get('published', '')

                content_to_check = title.lower()
                desc_raw = item.get('description', '') or item.get('summary', '')
                if desc_raw:
                    content_to_check += ' ' + str(desc_raw).lower()

                impact_level = 'Medium'
                if any(w in content_to_check for w in ['outage', 'down', 'unavailable', 'critical', 'major', 'severe']):
                    impact_level = 'High'
                elif any(w in content_to_check for w in ['low', 'minor', 'informational']):
                    impact_level = 'Low'

                inc_status = 'resolved'
                if any(w in content_to_check for w in ['ongoing', 'active', 'investigating', 'monitoring', 'identified']):
                    inc_status = 'active'

                data.append({
                    'id': rss_id,
                    'title': title,
                    'link': link,
                    'published': published,
                    'status': inc_status,
                    'impact_level': impact_level,
                    'source': 'status.webex.com/history.rss',
                    'description': str(desc_raw)[:500] if desc_raw else '',
                })
                hist_added += 1
            except Exception:
                continue

        logger.info(f"Supplemented {hist_added} incidents from history.rss")
    except Exception as e:
        logger.warning(f"Error supplementing from history.rss: {e}")

    # Remove duplicates and sort by published date
    unique_incidents = []
    seen_ids = set()
    for incident in data:
        if incident['id'] not in seen_ids:
            unique_incidents.append(incident)
            seen_ids.add(incident['id'])
    
    # Sort by published date (most recent first)
    unique_incidents.sort(key=lambda x: str(x.get('published') or ''), reverse=True)
    
    # Note: Sample incidents removed - using only real data from RSS feed and storage
    
    # Store new incidents in persistent storage
    if storage_available:
        try:
            new_incidents = [inc for inc in unique_incidents if not inc.get('_from_storage', False)]
            if new_incidents:
                stored_count = store_historical_incidents(new_incidents)
                logger.info(f"Stored {stored_count} new incidents in persistent storage")
        except Exception as e:
            logger.warning(f"Error storing incidents: {e}")
    
    # Debug logging
    logger.info(f"Successfully fetched {len(unique_incidents)} service incidents from status.webex.com")
    logger.debug(f"Incident sources: {set(inc.get('source', 'unknown') for inc in unique_incidents)}")
    logger.debug(f"Sample incidents: {[inc.get('id', 'no-id') for inc in unique_incidents[:5]]}")
    
    return unique_incidents

def fetch_status_maintenances(timeout=25) -> List[Dict[str, str]]:
    """Fetch scheduled maintenance events from status.webex.com history RSS feed."""
    try:
        from incident_storage import store_historical_maintenances
        storage_available = True
    except ImportError:
        storage_available = False

    data: List[Dict[str, str]] = []

    try:
        logger.info("Fetching maintenances from history.rss...")
        rss_url = "https://status.webex.com/history.rss"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        }
        r = requests.get(rss_url, headers=headers, timeout=timeout)
        r.raise_for_status()

        import feedparser
        feed = feedparser.parse(r.text)

        now_utc = datetime.utcnow()

        for item in feed.entries:
            try:
                title = item.get('title', '').strip()
                if not title:
                    continue
                if 'maintenance' not in title.lower():
                    continue

                link = item.get('link', '').strip()
                maint_id = link or title

                pub_raw = item.get('published', '')
                published = pub_raw
                if hasattr(item, 'published_parsed') and item.published_parsed:
                    try:
                        published = datetime(*item.published_parsed[:6]).strftime('%Y-%m-%dT%H:%M:%SZ')
                    except Exception:
                        pass

                try:
                    pub_dt = datetime.strptime(published[:19], '%Y-%m-%dT%H:%M:%S')
                except (ValueError, IndexError):
                    pub_dt = None

                if pub_dt is None:
                    maint_status = 'unknown'
                else:
                    maint_status = 'completed'
                    if pub_dt > now_utc:
                        maint_status = 'scheduled'

                description = ''
                desc_raw = item.get('description', '') or item.get('summary', '')
                if desc_raw:
                    description = str(desc_raw)[:500]

                data.append({
                    'id': maint_id,
                    'title': title,
                    'link': link,
                    'published': published,
                    'status': maint_status,
                    'source': 'status.webex.com/history.rss',
                    'description': description,
                })
            except Exception as e:
                logger.warning(f"Error processing maintenance RSS item: {e}")
                continue

        logger.info(f"Parsed {len(data)} maintenance events from history.rss")
    except Exception as e:
        logger.warning(f"Error fetching maintenance RSS: {e}")

    if storage_available and data:
        try:
            stored = store_historical_maintenances(data)
            logger.info(f"Stored {stored} maintenances in persistent storage")
        except Exception as e:
            logger.warning(f"Error storing maintenances: {e}")

    return data


def _correlate_incidents_with_cases(ext_incidents: List[Dict], csone_df: pd.DataFrame, ab_df: pd.DataFrame) -> Dict[str, List[Dict]]:
    """
    Correlate status.webex.com incidents with customer service cases based on:
    1. Temporal proximity (incident date vs case open date)
    2. Keyword matching (incident title/description vs case title/description)
    
    Returns a dictionary mapping incident_id to list of correlated cases
    """
    correlations = {}
    
    if not ext_incidents or (csone_df is None or csone_df.empty):
        return correlations
    
    from datetime import datetime, timedelta
    import re
    
    for incident in ext_incidents:
        incident_id = (incident.get('id') or '')
        incident_title = (incident.get('title') or '').lower()
        incident_desc = (incident.get('description') or '').lower()
        incident_published = (incident.get('published') or '')
        
        correlated_cases = []
        
        # Try to parse incident date
        incident_date = None
        if incident_published:
            try:
                parts = incident_published.split()
                if parts:
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y']:
                        try:
                            incident_date = datetime.strptime(parts[0], fmt)
                            break
                        except Exception:
                            continue
            except Exception:
                pass
        
        # Search through CSOne cases
        for idx, row in csone_df.iterrows():
            case_title = str(row.get('Title', '')).lower()
            case_desc = str(row.get('Problem Description', '')).lower()
            case_date_str = row.get('Date/Time Opened', '')
            
            # Keyword correlation
            keyword_match = False
            incident_keywords = set(re.findall(r'\b\w{4,}\b', incident_title + ' ' + incident_desc))
            case_keywords = set(re.findall(r'\b\w{4,}\b', case_title + ' ' + case_desc))
            
            # Check for significant keyword overlap (at least 2 matching keywords)
            if len(incident_keywords & case_keywords) >= 2:
                keyword_match = True
            
            # Temporal correlation (within 7 days)
            temporal_match = False
            days_diff = None
            if incident_date and case_date_str:
                try:
                    case_date = pd.to_datetime(case_date_str)
                    if isinstance(case_date, pd.Timestamp):
                        case_date = case_date.to_pydatetime()
                    if isinstance(case_date, datetime):
                        days_diff = abs((incident_date - case_date).days)
                        if days_diff <= 7:
                            temporal_match = True
                except Exception:
                    pass
            
            # If either keyword or temporal match, consider it correlated
            if keyword_match or temporal_match:
                correlated_cases.append({
                    'case': row.get('SR Number', row.get('Case Number', 'N/A')),
                    'customer': row.get('Customer Name', 'Unknown'),
                    'title': row.get('Title', 'No Title'),
                    'match_type': 'keyword' if keyword_match else 'temporal',
                    'days_diff': days_diff if temporal_match else None
                })
        
        if correlated_cases:
            correlations[incident_id] = correlated_cases
    
    return correlations

def cross_reference_refs(ab_df: pd.DataFrame, csone_df: pd.DataFrame, ext_bugs: List[Dict[str,str]]):
    # Build a set of CSC IDs from external
    ext_set = {b["bug_id"].upper() for b in ext_bugs}
    # Aggregate refs from datasets
    refs = set()
    for df in [ab_df, csone_df]:
        if df is not None and not df.empty and "bemscsc_refs" in df.columns:
            for ref_str in df["bemscsc_refs"].dropna():
                for x in ref_str.split(", "):
                    if x.startswith("CSC"):
                        refs.add(x.upper())
    matches = sorted(list(refs & ext_set))
    matched_rows = []
    if matches and ab_df is not None and not ab_df.empty:
        for _, row in ab_df.iterrows():
            ref_str = row.get("bemscsc_refs", "")
            hits = [r for r in matches if r in ref_str]
            if hits:
                matched_rows.append({
                    "source":"AdoptionBarrier","id":row.get("ID"),"customer_name":row.get("customer_name"),
                    "title":row.get("title"),"sub_technology":row.get("sub_technology"),"matches":", ".join(hits)
                })
    if matches and csone_df is not None and not csone_df.empty:
        for _, row in csone_df.iterrows():
            ref_str = row.get("bemscsc_refs", "")
            hits = [r for r in matches if r in ref_str]
            if hits:
                matched_rows.append({
                    "source":"CSOne","case":row.get("SR Number"),"customer_name":row.get("customer_name"),
                    "title":row.get("Title"),"matches":", ".join(hits)
                })
    # Create result dataframe with robust error handling
    try:
        if matched_rows:
            matched_df = pd.DataFrame(matched_rows)
            logger.info(f"Cross-reference analysis: {len(matches)} unique bug matches found in {len(matched_rows)} records")
        else:
            matched_df = pd.DataFrame(columns=["source","id","case","customer_name","title","sub_technology","matches"])
            logger.info("Cross-reference analysis: No bug matches found")
    except Exception as e:
        logger.warning(f"Error creating matched dataframe: {e}")
        matched_df = pd.DataFrame(columns=["source","id","case","customer_name","title","sub_technology","matches"])
    
    return matches, matched_df

# --------------------------- CircuIT client ---------------------------
class CircuitChatClient:
    OKTA_TOKEN_URL = "https://id.cisco.com/oauth2/default/v1/token"
    AZURE_ENDPOINT = "https://chat-ai.cisco.com"

    def __init__(self, client_id: str, client_secret: str, app_key: str, model_name: str = "gpt-4o-mini"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_key = app_key
        self.model_name = model_name
        self._access_token = None; self._expiry = 0

    def _get_token(self) -> str:
        if self._access_token and self._expiry > time.time() + 60:
            return self._access_token
        resp = requests.post(
            self.OKTA_TOKEN_URL,
            headers={"Content-Type":"application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data.get("access_token")
        if self._access_token is None:
            raise ValueError("No access_token in Okta response")
        self._expiry = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def complete(self, system_message: str, user_message: str) -> Optional[str]:
        try:
            token = self._get_token()
            client = AzureOpenAI(
                azure_endpoint=self.AZURE_ENDPOINT,
                api_key=token,
                api_version="2024-08-01-preview",
                timeout=120.0, # Set a 120-second timeout
            )
            messages = [{"role":"system","content":system_message},{"role":"user","content":user_message}]
            res = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                user=json.dumps({"appkey": self.app_key}),
                stop=["<|im_end|>"]
            )
            if not res.choices:
                return None
            return res.choices[0].message.content
        except APITimeoutError:
            logger.error("CircuIT API call timed out after 120 seconds.")
            return "ERROR: The analysis for this section timed out. The AI service may be under heavy load. Please try again later."
        except Exception as e:
            logger.error(f"CircuIT Error: {e}")
            return None

def create_enhanced_word_report(manager: str, technology: str, days: int, ab_data: pd.DataFrame, 
                               csone_data: pd.DataFrame, ai_insights: Dict, ext_bugs: List[Dict] = None,
                               ext_incidents: List[Dict] = None) -> Optional[str]:
    """Create an enhanced Word report using the new executive formatter.
    Returns None if executive_report_formatter module is not available (graceful fallback)."""
    try:
        from executive_report_formatter import ExecutiveReportFormatter
        
        # Create executive formatter
        formatter = ExecutiveReportFormatter()
        
        # Create enhanced document
        filepath = formatter.create_executive_report(
            manager, technology, days, ab_data, csone_data, ai_insights, ext_bugs, ext_incidents
        )
        
        logger.info(f"Enhanced Word report created successfully: {filepath}")
        return filepath
        
    except ImportError:
        # executive_report_formatter not available - skip enhanced report (base report still generated)
        logger.info("executive_report_formatter not available - enhanced Word report skipped")
        return None
    except Exception as e:
        logger.error(f"Error creating enhanced Word report: {e}")
        return None

# --------------------------- Writers ---------------------------
def add_executive_visual_dashboard(doc, portfolio_metrics: dict):
    """Add visual dashboard with charts for executives"""
    try:
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        import io
        
        # Try to create charts using matplotlib
        try:
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            
            # Add dashboard heading
            dashboard_heading = doc.add_heading("Portfolio Dashboard - At-A-Glance", level=2)
            dashboard_heading.runs[0].font.color.rgb = RGBColor(0, 123, 199)
            
            # Create figure with subplots for multiple charts
            fig = plt.figure(figsize=(12, 8))
            fig.patch.set_facecolor('white')
            
            # Chart 1: Portfolio Health Metrics (Top Left)
            ax1 = plt.subplot(2, 2, 1)
            metrics = ['Customers', 'Barriers', 'TAC Cases', 'BEMS']
            values = [
                portfolio_metrics.get('total_customers', 0),
                portfolio_metrics.get('total_barriers', 0),
                portfolio_metrics.get('total_cases', 0),
                portfolio_metrics.get('bems_count', 0)
            ]
            colors = ['#007BC7', '#5DBCD2', '#FFB81C', '#FF6B6B']
            bars = ax1.barh(metrics, values, color=colors)
            ax1.set_xlabel('Count', fontsize=10, fontweight='bold')
            ax1.set_title('Portfolio Metrics', fontsize=12, fontweight='bold')
            ax1.grid(axis='x', alpha=0.3)
            
            # Add value labels on bars
            for i, (bar, value) in enumerate(zip(bars, values)):
                ax1.text(value, i, f'  {int(value)}', va='center', fontweight='bold')
            
            # Chart 2: Risk Distribution (Top Right)
            ax2 = plt.subplot(2, 2, 2)
            risk_labels = ['High Risk', 'Medium Risk', 'Low Risk', 'Healthy']
            risk_values = [
                portfolio_metrics.get('high_risk_customers', 0),
                portfolio_metrics.get('medium_risk_customers', 0),
                portfolio_metrics.get('low_risk_customers', 0),
                portfolio_metrics.get('healthy_customers', 0)
            ]
            risk_colors = ['#FF6B6B', '#FFB81C', '#5DBCD2', '#28B463']
            wedges, texts, autotexts = ax2.pie(risk_values, labels=risk_labels, colors=risk_colors, 
                                                autopct='%1.0f%%', startangle=90)
            for text in texts:
                text.set_fontsize(9)
            for autotext in autotexts:
                autotext.set_color('white')
                autotext.set_fontweight('bold')
            ax2.set_title('Customer Risk Distribution', fontsize=12, fontweight='bold')
            
            # Chart 3: Case Severity Distribution (Bottom Left)
            ax3 = plt.subplot(2, 2, 3)
            severity_labels = ['P1 Critical', 'P2 High', 'P3 Medium', 'P4+ Low']
            severity_values = [
                portfolio_metrics.get('p1_cases', 0),
                portfolio_metrics.get('p2_cases', 0),
                portfolio_metrics.get('p3_cases', 0),
                portfolio_metrics.get('p4_cases', 0)
            ]
            severity_colors = ['#FF0000', '#FF6B6B', '#FFB81C', '#5DBCD2']
            bars = ax3.bar(severity_labels, severity_values, color=severity_colors)
            ax3.set_ylabel('Count', fontsize=10, fontweight='bold')
            ax3.set_title('TAC Case Severity', fontsize=12, fontweight='bold')
            ax3.grid(axis='y', alpha=0.3)
            plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')
            
            # Add value labels on bars
            for bar, value in zip(bars, severity_values):
                height = bar.get_height()
                ax3.text(bar.get_x() + bar.get_width()/2., height,
                        f'{int(value)}', ha='center', va='bottom', fontweight='bold')
            
            # Chart 4: Trend Indicator (Bottom Right)
            ax4 = plt.subplot(2, 2, 4)
            trend_data = portfolio_metrics.get('trend_direction', 'Stable')
            health_score = portfolio_metrics.get('health_score', 'C')
            
            # Create a simple gauge/indicator
            ax4.text(0.5, 0.6, f'Portfolio Health', ha='center', va='center', 
                    fontsize=14, fontweight='bold')
            ax4.text(0.5, 0.4, f'Grade: {health_score}', ha='center', va='center', 
                    fontsize=32, fontweight='bold',
                    color='#28B463' if health_score in ['A', 'B'] else '#FFB81C' if health_score == 'C' else '#FF6B6B')
            ax4.text(0.5, 0.2, f'Trend: {trend_data}', ha='center', va='center', 
                    fontsize=12, style='italic')
            ax4.set_xlim(0, 1)
            ax4.set_ylim(0, 1)
            ax4.axis('off')
            
            plt.tight_layout()
            
            # Save chart to bytes
            img_stream = io.BytesIO()
            plt.savefig(img_stream, format='png', dpi=150, bbox_inches='tight')
            img_stream.seek(0)
            plt.close()
            
            # Add chart to document
            doc.add_picture(img_stream, width=Inches(6.5))
            
            # Add space after chart
            doc.add_paragraph()
            
            return True
            
        except ImportError:
            # If matplotlib not available, create text-based visual dashboard
            doc.add_heading("Portfolio Dashboard - At-A-Glance", level=2)
            
            # Create a simple table dashboard
            table = doc.add_table(rows=3, cols=4)
            table.style = 'Light Grid Accent 1'
            
            # Row 1: Metrics
            cells = table.rows[0].cells
            cells[0].text = f"👥 Customers\n{portfolio_metrics.get('total_customers', 0)}"
            cells[1].text = f"⚠️ Barriers\n{portfolio_metrics.get('total_barriers', 0)}"
            cells[2].text = f"📞 TAC Cases\n{portfolio_metrics.get('total_cases', 0)}"
            cells[3].text = f"🔴 BEMS\n{portfolio_metrics.get('bems_count', 0)}"
            
            # Row 2: Risk
            cells = table.rows[1].cells
            cells[0].text = f"🔴 High Risk\n{portfolio_metrics.get('high_risk_customers', 0)}"
            cells[1].text = f"🟡 Medium Risk\n{portfolio_metrics.get('medium_risk_customers', 0)}"
            cells[2].text = f"🟢 Low Risk\n{portfolio_metrics.get('low_risk_customers', 0)}"
            cells[3].text = f"✅ Healthy\n{portfolio_metrics.get('healthy_customers', 0)}"
            
            # Row 3: Severity
            cells = table.rows[2].cells
            cells[0].text = f"P1 Critical\n{portfolio_metrics.get('p1_cases', 0)}"
            cells[1].text = f"P2 High\n{portfolio_metrics.get('p2_cases', 0)}"
            cells[2].text = f"Grade: {portfolio_metrics.get('health_score', 'C')}"
            cells[3].text = f"Trend: {portfolio_metrics.get('trend_direction', 'Stable')}"
            
            # Style the table
            for row in table.rows:
                for cell in row.cells:
                    cell.vertical_alignment = 1  # Center
                    for paragraph in cell.paragraphs:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for run in paragraph.runs:
                            run.font.size = Pt(11)
                            run.font.bold = True
            
            doc.add_paragraph()
            return True
            
    except Exception as e:
        logger.warning(f"Could not create visual dashboard: {e}")
        return False

def create_executive_title_page(doc, manager: str, technology: str, days: int, portfolio_metrics: dict = None):
    """Create a professional executive-style title page for portfolio reports"""
    try:
        from docx.shared import Pt, RGBColor, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from datetime import datetime
        
        # Add spacing at top
        doc.add_paragraph()
        doc.add_paragraph()
        
        # Main title
        title = doc.add_heading(f"{manager}'s Portfolio", level=1)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            title.runs[0].font.size = Pt(28)
            title.runs[0].font.bold = True
            title.runs[0].font.color.rgb = RGBColor(0, 123, 199)  # Cisco blue
        except Exception:
            pass
        
        # Subtitle
        subtitle = doc.add_paragraph(f"{technology} Executive Analysis")
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            subtitle.runs[0].font.size = Pt(18)
            subtitle.runs[0].font.color.rgb = RGBColor(100, 100, 100)
        except Exception:
            pass
        
        doc.add_paragraph()
        
        # Key metrics box (if provided)
        if portfolio_metrics:
            metrics_para = doc.add_paragraph()
            metrics_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            metrics_text = f"""
Analysis Period: {days} Days
Report Date: {datetime.now().strftime("%B %d, %Y")}
"""
            if portfolio_metrics.get('total_customers'):
                metrics_text += f"\nTotal Customers: {portfolio_metrics['total_customers']}"
            if portfolio_metrics.get('total_barriers'):
                metrics_text += f"\nActive Barriers: {portfolio_metrics['total_barriers']}"
            if portfolio_metrics.get('total_cases'):
                metrics_text += f"\nSupport Cases: {portfolio_metrics['total_cases']}"
            
            metrics_para.add_run(metrics_text.strip())
            try:
                metrics_para.runs[0].font.size = Pt(12)
                metrics_para.runs[0].font.color.rgb = RGBColor(60, 60, 60)
            except Exception:
                pass
        else:
            # Simple metadata if no metrics
            meta = doc.add_paragraph(f"Analysis Period: {days} Days\nReport Generated: {datetime.now().strftime('%B %d, %Y')}")
            meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                meta.runs[0].font.size = Pt(12)
                meta.runs[0].font.color.rgb = RGBColor(100, 100, 100)
            except Exception:
                pass
        
        doc.add_paragraph()
        doc.add_paragraph()
        
        # Confidentiality notice
        notice = doc.add_paragraph("CONFIDENTIAL - Executive Leadership Review")
        notice.alignment = WD_ALIGN_PARAGRAPH.CENTER
        try:
            notice.runs[0].font.size = Pt(10)
            notice.runs[0].font.italic = True
            notice.runs[0].font.color.rgb = RGBColor(150, 150, 150)
        except Exception:
            pass
        
        # Page break after title
        doc.add_page_break()
        
    except Exception as e:
        # If title page creation fails, just continue - don't break the report
        logger.warning(f"Could not create title page: {e}")
        pass

def append_to_word_report(doc_or_path, markdown_content: str, heading: str = None):
    """Enhanced Word report writer with professional executive-ready formatting - removes ALL markdown symbols"""
    
    if not isinstance(markdown_content, str) or not markdown_content.strip():
        return
    
    # Pre-process markdown content to ensure clean formatting
    # Remove any stray markdown symbols that aren't at line starts
    markdown_content = markdown_content.replace('**YOUR MISSION:**', 'YOUR MISSION:')
    markdown_content = markdown_content.replace('**CRITICAL REQUIREMENTS:**', 'CRITICAL REQUIREMENTS:')
    markdown_content = markdown_content.replace('**TECHNOLOGY FOCUS:**', 'TECHNOLOGY FOCUS:')
    markdown_content = markdown_content.replace('**DATA:**', 'DATA:')
    markdown_content = markdown_content.replace('**DATA SOURCES:**', 'DATA SOURCES:')
    
    # Ensure headings are on their own lines
    markdown_content = re.sub(r'([^\n])(\n)(#{1,5} )', r'\1\n\n\3', markdown_content)
    
    # Handle different parameter types for backward compatibility
    if isinstance(doc_or_path, str):
        from docx import Document
        import os
        if os.path.exists(doc_or_path):
            doc = Document(doc_or_path)
        else:
            doc = Document()
        should_save = True
        file_path = doc_or_path
    else:
        doc = doc_or_path
        should_save = False
        file_path = None
    
    # Set up professional styles first - Executive-ready formatting
    try:
        from docx.shared import Pt, RGBColor, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
        
        # Configure Normal style for maximum readability
        style = doc.styles['Normal']
        font = style.font
        font.name = 'Calibri'
        font.size = Pt(11)
        font.color.rgb = RGBColor(0, 0, 0)  # Black text for clarity
        
        # Set document margins for professional appearance
        try:
            sections = doc.sections
            for section in sections:
                section.top_margin = Inches(1)
                section.bottom_margin = Inches(1)
                section.left_margin = Inches(1)
                section.right_margin = Inches(1)
        except Exception:
            pass
        
        # Configure Heading styles with professional hierarchy
        for level in range(1, 5):
            try:
                heading_style = doc.styles[f'Heading {level}']
                heading_style.font.name = 'Calibri'
                heading_style.font.bold = True
                heading_style.font.color.rgb = RGBColor(0, 123, 199)  # Cisco blue for visual hierarchy
                
                # Set appropriate sizes for clear hierarchy
                if level == 1:
                    heading_style.font.size = Pt(18)
                    heading_style.paragraph_format.space_before = Pt(12)
                    heading_style.paragraph_format.space_after = Pt(6)
                elif level == 2:
                    heading_style.font.size = Pt(14)
                    heading_style.paragraph_format.space_before = Pt(10)
                    heading_style.paragraph_format.space_after = Pt(4)
                elif level == 3:
                    heading_style.font.size = Pt(12)
                    heading_style.paragraph_format.space_before = Pt(8)
                    heading_style.paragraph_format.space_after = Pt(3)
                else:
                    heading_style.font.size = Pt(11)
                    heading_style.paragraph_format.space_before = Pt(6)
                    heading_style.paragraph_format.space_after = Pt(2)
                
                # Keep headings with their content
                heading_style.paragraph_format.keep_with_next = True
            except Exception:
                pass
        
        # Configure List Bullet style for professional appearance
        try:
            bullet_style = doc.styles['List Bullet']
            bullet_style.font.name = 'Calibri'
            bullet_style.font.size = Pt(11)
            bullet_style.paragraph_format.space_after = Pt(4)
            bullet_style.paragraph_format.line_spacing = 1.15
            bullet_style.paragraph_format.left_indent = Inches(0.25)
        except Exception:
            pass
            
        # Configure List Number style
        try:
            number_style = doc.styles['List Number']
            number_style.font.name = 'Calibri'
            number_style.font.size = Pt(11)
            number_style.paragraph_format.space_after = Pt(4)
            number_style.paragraph_format.line_spacing = 1.15
            number_style.paragraph_format.left_indent = Inches(0.25)
        except Exception:
            pass
            
    except Exception:
        pass
    
    # Add heading if provided
    if heading:
        doc.add_heading(heading, level=1)
    
    # Add elegant section separator if this isn't the first section
    if len(doc.paragraphs) > 1:
        # Add some spacing
        doc.add_paragraph()
    
    # Helper function to process text and remove ALL markdown while preserving formatting
    def clean_and_format_text(text, paragraph):
        """Process text to remove ** and apply proper bold formatting - NO ** SYMBOLS SHOWN"""
        # First, handle the case where ** might not be paired correctly
        # Count the number of ** - if odd, just remove all of them
        count = text.count('**')
        
        if count == 0:
            # No bold markers, just add clean text
            paragraph.add_run(text.strip())
            return paragraph
        
        if count == 1 or count % 2 != 0:
            # Odd number of ** - malformed markdown, just remove all ** and don't try to bold
            clean_text = text.replace('**', '')
            run = paragraph.add_run(clean_text.strip())
            run.bold = True  # Make it bold since it was probably meant to be emphasized
            return paragraph
        
        # Even number of ** - process pairs for bold formatting
        # The ** symbols themselves are NOT added - only used to determine bold sections
        parts = text.split('**')
        for idx, part in enumerate(parts):
            if part:  # Include all parts, even if just whitespace
                # Don't strip individual parts to preserve spacing
                run = paragraph.add_run(part)
                if idx % 2 == 1:  # Odd indices are bold (text between ** pairs)
                    run.bold = True
        return paragraph
    
    # Process content with professional executive formatting
    # Remove ALL markdown symbols and convert to clean Word formatting
    lines = markdown_content.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Skip empty lines
        if not line:
            i += 1
            continue
        
        # Handle headings - remove ALL # and ** symbols completely
        if line.startswith('#####'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=4)
            try:
                h.runs[0].font.size = Pt(11)
                h.runs[0].font.bold = True
                h.runs[0].font.color.rgb = RGBColor(0, 123, 199)
            except Exception:
                pass
        elif line.startswith('####'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=4)
            try:
                h.runs[0].font.size = Pt(11)
                h.runs[0].font.bold = True
                h.runs[0].font.color.rgb = RGBColor(0, 123, 199)
            except Exception:
                pass
        elif line.startswith('###'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=3)
        elif line.startswith('##'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=2)
        elif line.startswith('#'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=1)
        
        # Handle bullet points - remove ALL markdown symbols
        elif line.startswith(('* ', '- ', '• ')):
            bullet_text = line[2:].strip()
            # Create paragraph and use helper to process ** symbols
            p = doc.add_paragraph(style='List Bullet')
            p.clear()
            clean_and_format_text(bullet_text, p)
            try:
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15
            except Exception:
                pass
        
        # Handle numbered lists - remove ALL markdown symbols
        elif len(line) > 2 and line[0].isdigit() and line[1:3] in ['. ', ') ']:
            list_text = line[line.index('.') + 1:].strip() if '.' in line else line[line.index(')') + 1:].strip()
            # Create paragraph and use helper to process ** symbols
            p = doc.add_paragraph(style='List Number')
            p.clear()
            clean_and_format_text(list_text, p)
            try:
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15
            except Exception:
                pass
        
        # Regular paragraphs - use helper to clean ALL markdown
        else:
            p = doc.add_paragraph()
            clean_and_format_text(line, p)
            try:
                from docx.shared import Pt
                from docx.enum.text import WD_ALIGN_PARAGRAPH
                p.paragraph_format.space_after = Pt(8)
                p.paragraph_format.line_spacing = 1.15
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                # Add subtle first line indent for paragraphs to improve scannability
                # (Not for single-line statements)
                if len(line) > 80:  # Only indent longer paragraphs
                    p.paragraph_format.first_line_indent = Inches(0)  # No indent - keep clean
            except Exception:
                pass
        
        i += 1
    
    # Save the document if we created it from a path
    if should_save and file_path:
        doc.save(file_path)
        return file_path
    
    return doc

def write_excel_workbook(sheets_or_path, title_or_sheets=None, csconsole_data: dict = None, manager: str = "Portfolio Manager", technology: str = "Technology Analysis", days: int = 90):
    """Enhanced Excel workbook writer with professional formatting"""
    
    # Handle different parameter combinations for backward compatibility
    if isinstance(sheets_or_path, pd.DataFrame) and isinstance(title_or_sheets, str):
        # Called as write_excel_workbook(dataframe, filename, title)
        df = sheets_or_path
        base_path = title_or_sheets
        sheets = {"Main_Data": df}
    elif isinstance(sheets_or_path, str) and isinstance(title_or_sheets, dict):
        # Called as write_excel_workbook(filename, sheets_dict)
        base_path = sheets_or_path
        sheets = title_or_sheets
    elif isinstance(sheets_or_path, dict):
        # Called as write_excel_workbook(sheets_dict) - need to generate filename
        sheets = sheets_or_path
        base_path = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    else:
        # Default case - assume it's a path and sheets
        base_path = str(sheets_or_path)
        sheets = title_or_sheets if title_or_sheets else {}
    
    try:
        # Use enhanced Excel formatter for better output
        from enhanced_excel_formatter import create_enhanced_excel_report
        
        # Extract data from sheets dict
        ab_data = sheets.get("AB_Detail_All", pd.DataFrame())
        csone_data = sheets.get("CSOne_Detail_All", pd.DataFrame())
        ext_bugs = sheets.get("External_Bugs", [])
        ext_incidents = sheets.get("External_Incidents", [])
        
        # Convert DataFrames to lists of dicts if needed
        if isinstance(ext_bugs, pd.DataFrame) and not ext_bugs.empty:
            ext_bugs = ext_bugs.to_dict('records')
        if isinstance(ext_incidents, pd.DataFrame) and not ext_incidents.empty:
            ext_incidents = ext_incidents.to_dict('records')
        
        # Create enhanced Excel report
        enhanced_path = f"{base_path}_enhanced.xlsx"
        create_enhanced_excel_report(
            enhanced_path, 
            manager,  # Use actual manager name
            technology,  # Use actual technology
            days,  # Use actual days
            ab_data, 
            csone_data,
            ext_bugs,
            ext_incidents,
            None,  # ai_insights
            csconsole_data  # Add CSConsole data
        )
        
        return enhanced_path
        
    except Exception as e:
        logger.warning(f"Enhanced Excel formatter failed, falling back to basic: {e}")
        # Fallback: write main sheets plus CSConsole data so report is accurate and complete
        csconsole_sheet_names = {
            "action_plans": "CSConsole_Action_Plans",
            "customer_pulse": "CSConsole_Customer_Pulse",
            "success_priorities": "CSConsole_Success_Priorities",
            "adoption_barriers": "CSConsole_Adoption_Barriers",
        }
        with pd.ExcelWriter(f"{base_path}.xlsx", engine="xlsxwriter") as xw:
            # Indicate fallback so readers know there's no issue
            report_info = pd.DataFrame([
                ["Export type", "Standard (fallback)"],
                ["Note", "This report was generated using the standard Excel export. The enhanced formatter was not available; all data is present and accurate."],
            ], columns=["Item", "Value"])
            report_info.to_excel(xw, sheet_name="Report_Info", index=False)
            for name, df in sheets.items():
                if df is None: continue
                if not isinstance(df, pd.DataFrame):
                    try:
                        df = pd.DataFrame(df) if df else pd.DataFrame()
                    except Exception:
                        continue
                df_copy = df.copy()
                for col in df_copy.select_dtypes(include=['datetimetz']).columns:
                    if df_copy[col].dt.tz is not None:
                        df_copy[col] = df_copy[col].dt.tz_convert(None)

                sheet = (name or "Sheet")[:31]
                if hasattr(df_copy, "to_excel"):
                    df_copy.to_excel(xw, sheet_name=sheet, index=False)
                else:
                    pd.DataFrame(df_copy).to_excel(xw, sheet_name=sheet, index=False)
            if csconsole_data:
                for key, sheet_name in csconsole_sheet_names.items():
                    df = csconsole_data.get(key)
                    if df is not None and hasattr(df, "empty") and not df.empty:
                        df_copy = df.copy()
                        for col in df_copy.select_dtypes(include=['datetimetz']).columns:
                            if df_copy[col].dt.tz is not None:
                                df_copy[col] = df_copy[col].dt.tz_convert(None)
                        df_copy.to_excel(xw, sheet_name=sheet_name[:31], index=False)
        return f"{base_path}.xlsx"

# --------------------------- LLM prompt ---------------------------
def _create_briefing_book(data_scope: str, ab_df, csone_df, ext_bugs, ext_incidents, matches, matched_df, db_profile, engagement_counts=None, csconsole_data=None, arr_data=None, arr_impact=None, feature_requests=None, software_defects=None, psirt_vulns=None):
    """Creates a detailed text block for the LLM prompt."""
    briefing = []
    briefing.append(f"## Analyst's Briefing Book for: {data_scope}")
    briefing.append("---")

    # ARR by Customer (strategic prioritization - high-value accounts need extra attention)
    if arr_data is not None and not arr_data.empty and 'BU_NAME' in arr_data.columns and 'ANNUAL_CONTRACT_VALUE' in arr_data.columns:
        arr_by_cust = arr_data.groupby('BU_NAME')['ANNUAL_CONTRACT_VALUE'].sum().sort_values(ascending=False)
        total_arr = arr_by_cust.sum()
        briefing.append("### ARR by Customer (Strategic Prioritization):")
        briefing.append("**CRITICAL:** Prioritize high-ARR customers with adoption barriers or support cases. These represent the greatest renewal risk and revenue impact.")
        for cust, arr_val in arr_by_cust.head(20).items():
            pct = (arr_val / total_arr * 100) if total_arr > 0 else 0
            briefing.append(f"- **{cust}:** ${arr_val:,.0f} ({pct:.1f}% of portfolio)")
        if len(arr_by_cust) > 20:
            briefing.append(f"- ... and {len(arr_by_cust) - 20} more customers")
        briefing.append(f"**Total Portfolio ARR:** ${total_arr:,.0f}")
        briefing.append("---")

    # ARR Impact by Issue Category (which adoption barrier types have highest revenue at risk)
    if arr_impact and arr_impact.get('top_issues'):
        briefing.append("### ARR at Risk by Issue Category:")
        briefing.append("**CRITICAL:** Prioritize interventions for issue categories with highest ARR exposure.")
        for issue_name, data in arr_impact['top_issues'][:10]:
            arr_val = data.get('arr', 0)
            cust_count = data.get('customer_count', 0)
            briefing.append(f"- **{issue_name}:** ${arr_val:,.0f} at risk ({cust_count} customers)")
        briefing.append("---")

    # Feature Requests (product gap signal - customers asking for capabilities)
    if feature_requests and feature_requests.get('total_requests', 0) > 0:
        briefing.append("### Feature Requests (Product Gap Signal):")
        briefing.append(f"**{feature_requests['total_requests']}** cases contain feature requests. ARR impact: ${feature_requests.get('total_arr_impact', 0):,.0f}")
        if feature_requests.get('top_features'):
            briefing.append("**Most requested themes:**")
            for theme, count in feature_requests['top_features'][:5]:
                briefing.append(f"- {theme}: {count} cases")
        if feature_requests.get('customer_examples'):
            top_by_arr = sorted([c for c in feature_requests['customer_examples'] if c.get('arr', 0) > 0], key=lambda x: x.get('arr', 0), reverse=True)[:5]
            if top_by_arr:
                briefing.append("**High-value customers requesting features:**")
                for c in top_by_arr:
                    briefing.append(f"- {c.get('customer', 'N/A')}: ${c.get('arr', 0):,.0f} ARR")
        briefing.append("---")

    # Software Defects & PSIRT (extracted from case text - known bugs/vulns affecting customers)
    if software_defects and software_defects.get('total_defects', 0) > 0:
        briefing.append("### Software Defects (BST/CSC IDs in Cases):")
        briefing.append(f"**{software_defects['total_defects']}** defect references in **{software_defects.get('total_cases_with_defects', 0)}** cases. These may correlate with known bugs.")
        defect_by_cust = software_defects.get('defect_by_customer', {})
        if defect_by_cust:
            for cust, refs in sorted(defect_by_cust.items(), key=lambda x: -len(x[1]))[:8]:
                briefing.append(f"- **{cust}:** {', '.join(list(refs)[:5])}{'...' if len(refs) > 5 else ''}")
        briefing.append("---")
    if psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0:
        briefing.append("### PSIRT / Security Vulnerabilities (in Cases):")
        cves = psirt_vulns.get('cve_ids', set())
        psirts = psirt_vulns.get('psirt_advisories', set())
        briefing.append(f"**{len(cves)} CVE(s), {len(psirts)} PSIRT advisory(ies)** referenced in case text. Security-sensitive.")
        vuln_by_cust = psirt_vulns.get('vulnerability_by_customer', {})
        if vuln_by_cust:
            for cust, refs in sorted(vuln_by_cust.items(), key=lambda x: -len(x[1]))[:5]:
                briefing.append(f"- **{cust}:** {', '.join(list(refs)[:3])}{'...' if len(refs) > 3 else ''}")
        briefing.append("---")

    # Metrics
    total_ab = len(ab_df) if ab_df is not None else 0
    total_csone = len(csone_df) if csone_df is not None else 0
    esc_rate, chronic_rate = _calc_rates(csone_df)
    
    # Calculate BEMS metrics (PRIMARY: Transaction ID column from CSOne Excel)
    total_bems = 0
    bems_rate = 0.0
    if csone_df is not None and not csone_df.empty:
        # Check BEMS in multiple columns (Transaction ID is primary source in CSOne Excel)
        bems_mask = pd.Series([False] * len(csone_df), index=csone_df.index)
        
        # PRIMARY: Transaction ID column (main BEMS data location)
        if 'Transaction ID' in csone_df.columns:
            bems_mask |= csone_df['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
        
        # SECONDARY: bemscsc_refs column (alternate location)
        if 'bemscsc_refs' in csone_df.columns:
            bems_mask |= csone_df['bemscsc_refs'].notna() & (csone_df['bemscsc_refs'].astype(str) != '') & (csone_df['bemscsc_refs'].astype(str) != '[]') & csone_df['bemscsc_refs'].astype(str).str.contains('BEMS', case=False, na=False)
        
        bems_cases = csone_df[bems_mask]
        total_bems = len(bems_cases)
        bems_rate = (total_bems / total_csone * 100) if total_csone > 0 else 0.0
    
    # CSConsole metrics
    csconsole_action_plans = csconsole_data.get('action_plans', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    csconsole_customer_pulse = csconsole_data.get('customer_pulse', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    csconsole_success_priorities = csconsole_data.get('success_priorities', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    csconsole_adoption_barriers = csconsole_data.get('adoption_barriers', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    
    total_action_plans = len(csconsole_action_plans) if not csconsole_action_plans.empty else 0
    total_customer_pulse = len(csconsole_customer_pulse) if not csconsole_customer_pulse.empty else 0
    total_success_priorities = len(csconsole_success_priorities) if not csconsole_success_priorities.empty else 0
    total_csconsole_adoption_barriers = len(csconsole_adoption_barriers) if not csconsole_adoption_barriers.empty else 0
    
    briefing.append("### Key Metrics:")
    briefing.append(f"* **Adoption Barriers Found:** {total_ab}")
    briefing.append(f"* **CSOne (TAC) Cases Found:** {total_csone}")
    briefing.append(f"* **BEMS Escalations (Back End Engineering):** {total_bems} ({bems_rate:.1f}% of TAC cases)")
    briefing.append(f"* **Inferred Escalation Rate (from CSOne):** {esc_rate}%")
    briefing.append("---")
    
    briefing.append("### CSConsole Data:")
    briefing.append(f"* **Action Plans:** {total_action_plans}")
    briefing.append(f"* **Customer Pulse Records:** {total_customer_pulse}")
    briefing.append(f"* **Success Priorities:** {total_success_priorities}")
    briefing.append(f"* **CSConsole Adoption Barriers:** {total_csconsole_adoption_barriers}")
    briefing.append("---")
    
    if engagement_counts is not None and not engagement_counts.empty:
        # FIXED: Show ALL customers by engagement volume
        briefing.append("### All Customers by Engagement Volume:")
        briefing.append(engagement_counts.to_string())
        briefing.append("---")

    # Severity distribution by customer (P1/P2 = high priority - quick risk overview)
    if csone_df is not None and not csone_df.empty:
        sev_col = next((c for c in ['Severity', 'Highest Priority', 'Priority'] if c in csone_df.columns), None)
        if sev_col and 'customer_name' in csone_df.columns:
            p1_mask = csone_df[sev_col].astype(str).str.contains('P1|1|Critical', case=False, na=False)
            p2_mask = csone_df[sev_col].astype(str).str.contains('P2|2|High', case=False, na=False)
            p1_by_cust = csone_df[p1_mask]['customer_name'].value_counts()
            p2_by_cust = csone_df[p2_mask]['customer_name'].value_counts()
            if not p1_by_cust.empty or not p2_by_cust.empty:
                briefing.append("### P1/P2 Cases by Customer (High-Priority Risk):")
                all_custs = set(p1_by_cust.index) | set(p2_by_cust.index)
                for cust in sorted(all_custs, key=lambda c: (-p1_by_cust.get(c, 0), -p2_by_cust.get(c, 0)))[:15]:
                    p1 = p1_by_cust.get(cust, 0)
                    p2 = p2_by_cust.get(cust, 0)
                    if p1 > 0 or p2 > 0:
                        briefing.append(f"- **{cust}:** P1: {p1} | P2: {p2}")
                briefing.append("---")

    # Summaries
    if ab_df is not None and not ab_df.empty:
        by_cat = ab_df.groupby("ab_category_final")["ID"].count().sort_values(ascending=False)
        briefing.append("### Adoption Barrier Category Summary:")
        briefing.append(json.dumps({k:int(v) for k,v in by_cat.items()}, indent=2))
        by_sub = ab_df.groupby("sub_technology")["ID"].count().sort_values(ascending=False)
        briefing.append("\n### Adoption Barrier Sub-Technology Summary:")
        briefing.append(json.dumps({k:int(v) for k,v in by_sub.items()}, indent=2))
        briefing.append("\n### All Adoption Barrier Titles for Thematic Analysis:")
        briefing.append("\n".join("- " + str(title) for title in ab_df['title'].dropna()))
        
        # Add full adoption barrier details with source citations
        briefing.append("\n### Complete Adoption Barrier Details (with Source Citations):")
        for _, row in ab_df.iterrows():
            barrier_id = row.get('ID', 'Unknown')
            title = row.get('title', 'No Title')
            description = row.get('description', 'No Description')
            customer = row.get('customer_name', 'Unknown Customer')
            
            # Format with source citation
            briefing.append(f"\n**CSConsole Record: {barrier_id}**")
            briefing.append(f"**Customer:** {customer}")
            briefing.append(f"**Title:** {title}")
            briefing.append(f"**Full Description:** {description}")
            briefing.append("---")
        
        briefing.append("---")
    else:
        briefing.append("No Adoption Barrier data found in scope.")

    # Case volume trend (month-over-month - increasing/decreasing signals risk)
    if csone_df is not None and not csone_df.empty:
        date_col = next((c for c in LIKELY_DATE_COLS if c in csone_df.columns), None)
        if date_col:
            try:
                df_trend = csone_df.copy()
                df_trend['_dt'] = pd.to_datetime(df_trend[date_col], errors='coerce')
                df_trend = df_trend.dropna(subset=['_dt'])
                if len(df_trend) >= 2:
                    monthly = df_trend.groupby(df_trend['_dt'].dt.to_period('M')).size().sort_index()
                    if len(monthly) >= 2:
                        recent = monthly.iloc[-1]
                        prior = monthly.iloc[-2]
                        change_pct = ((recent - prior) / prior * 100) if prior > 0 else 0
                        trend = "INCREASING" if change_pct > 10 else ("DECREASING" if change_pct < -10 else "STABLE")
                        briefing.append("### Case Volume Trend (Month-over-Month):")
                        briefing.append(f"**{trend}:** Most recent month: {int(recent)} cases | Prior month: {int(prior)} cases | Change: {change_pct:+.1f}%")
                        briefing.append("**ANALYTICAL NOTE:** Increasing case volume may indicate emerging issues or deteriorating customer health. Decreasing volume suggests improving stability.")
                        briefing.append("---")
            except Exception:
                pass

    # FIXED: Show ALL CSOne data
    if csone_df is not None and not csone_df.empty:
        csone_df_display = csone_df.copy()
        csone_df_display['display_id'] = csone_df_display.get('SR Number', csone_df_display.get('Case Number'))
        briefing.append("### Complete CSOne (TAC) Case Data:")
        briefing.append(_json_lite(csone_df_display, limit=len(csone_df_display), keep=["display_id","Title","Owner Email","customer_name","bemscsc_refs"]))
        
        # Add BEMS-specific analysis (Check Transaction ID and bemscsc_refs)
        bems_mask = pd.Series([False] * len(csone_df), index=csone_df.index)
        
        # PRIMARY: Transaction ID column (main BEMS data location in CSOne Excel)
        if 'Transaction ID' in csone_df.columns:
            bems_mask |= csone_df['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
        
        # SECONDARY: bemscsc_refs column
        if 'bemscsc_refs' in csone_df.columns:
            bems_mask |= csone_df['bemscsc_refs'].notna() & (csone_df['bemscsc_refs'].astype(str) != '') & (csone_df['bemscsc_refs'].astype(str) != '[]') & csone_df['bemscsc_refs'].astype(str).str.contains('BEMS', case=False, na=False)
        
        bems_cases = csone_df[bems_mask]
        
        if not bems_cases.empty:
                briefing.append(f"\n### BEMS Escalation Analysis ({len(bems_cases)} cases requiring Back End Engineering):")
                briefing.append("**CRITICAL INSIGHT:** BEMS (Back End Engineering Management System) escalations indicate complex technical issues that TAC could not resolve independently. These represent high-severity, high-complexity problems requiring specialized engineering expertise.")
                
                # Group BEMS cases by customer with detailed BEMS IDs
                bems_by_customer = bems_cases.groupby('customer_name').agg({
                    'customer_name': 'count',  # Count of BEMS
                    'bemscsc_refs': lambda x: list(x),  # List of all BEMS refs
                    'Transaction ID': lambda x: list(x) if 'Transaction ID' in bems_cases.columns else []  # List of Transaction IDs
                }).rename(columns={'customer_name': 'bems_count'})
                
                briefing.append("\n**BEMS Cases by Customer (with BEMS IDs):**")
                for customer, row in bems_by_customer.iterrows():
                    count = row['bems_count']
                    bems_refs = row.get('bemscsc_refs', [])
                    transaction_ids = row.get('Transaction ID', [])
                    
                    # Extract actual BEMS IDs from both sources
                    bems_ids = set()
                    for ref in bems_refs:
                        if ref and str(ref) != 'nan' and 'BEMS' in str(ref).upper():
                            bems_ids.add(str(ref))
                    for tid in transaction_ids:
                        if tid and str(tid) != 'nan' and 'BEMS' in str(tid).upper():
                            bems_ids.add(str(tid))
                    
                    # Format BEMS IDs with brackets for consistent citation
                    bems_id_list = ', '.join([f'[{bid}]' for bid in sorted(bems_ids)]) if bems_ids else 'No specific BEMS IDs found'
                    briefing.append(f"- **{customer}:** {count} BEMS escalation{'s' if count > 1 else ''} | **BEMS IDs:** {bems_id_list}")
                
                # FIXED: Show ALL BEMS cases for complete analysis
                briefing.append(f"\n**All BEMS Cases with Full Details (for Predictive Risk Assessment):**")
                for _, row in bems_cases.iterrows():
                    case_number = row.get('SR Number', row.get('Case Number', 'Unknown'))
                    title = row.get('Title', 'No Title')
                    customer = row.get('customer_name', 'Unknown Customer')
                    bems_refs = row.get('bemscsc_refs', 'No BEMS refs')
                    transaction_id = row.get('Transaction ID', '')
                    
                    # Show both sources of BEMS info
                    bems_info = []
                    if transaction_id and 'BEMS' in str(transaction_id).upper():
                        bems_info.append(f"Transaction ID: {transaction_id}")
                    if bems_refs and str(bems_refs) != 'No BEMS refs':
                        bems_info.append(f"BEMS Refs: {bems_refs}")
                    
                    bems_detail = ' | '.join(bems_info) if bems_info else 'BEMS detected but ID not specified'
                    briefing.append(f"- **TAC Case: {case_number}** ({customer}): {title} | **{bems_detail}**")
        
        # Add full CSOne case details with source citations
        briefing.append("\n### Complete CSOne (TAC) Case Details (with Source Citations):")
        for _, row in csone_df.iterrows():
            case_number = row.get('SR Number', row.get('Case Number', 'Unknown'))
            title = row.get('Title', 'No Title')
            description = row.get('Description', 'No Description')
            customer = row.get('customer_name', 'Unknown Customer')
            owner = row.get('Owner Email', 'Unknown Owner')
            
            # Format with source citation
            briefing.append(f"\n**TAC Case: {case_number}**")
            briefing.append(f"**Customer:** {customer}")
            briefing.append(f"**Title:** {title}")
            briefing.append(f"**Owner:** {owner}")
            briefing.append(f"**Full Description:** {description}")
            if row.get('bemscsc_refs'):
                briefing.append(f"**BEMS References:** {row.get('bemscsc_refs')}")
            briefing.append("---")
        
        briefing.append("---")
    else:
        briefing.append("No CSOne (TAC) data found in scope.")

    # CSConsole Data Details
    if csconsole_data and any(not df.empty for df in csconsole_data.values()):
        def _first_present(row, keys, default=""):
            for key in keys:
                value = row.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text and text.lower() not in ("nan", "none"):
                    return text
            return default

        briefing.append("\n### CSConsole Data Analysis:")
        briefing.append("**CRITICAL:** CSConsole data provides comprehensive customer success insights including strategic action plans, real-time customer pulse, success priorities, and additional adoption barriers. This data is essential for understanding the complete customer journey and success metrics.")
        
        # Action Plans
        if not csconsole_action_plans.empty:
            briefing.append(f"\n**Action Plans ({len(csconsole_action_plans)} records):**")
            briefing.append("**CRITICAL INSIGHT:** Action plans represent strategic initiatives to address customer adoption challenges and drive success. These are proactive measures taken by CSSMs to improve customer outcomes.")
            # FIXED: Show ALL Action Plans
            briefing.append("**All Action Plans:**")
            for _, row in csconsole_action_plans.iterrows():
                plan_id = row.get('ID', 'Unknown')
                title = row.get('SUBJECT_C', row.get('ACTION_PLAN_TITLE_C', 'No Title'))
                description = row.get('DESCRIPTION_C', 'No Description')
                status = row.get('STATUS_C', 'Unknown')
                priority = row.get('PRIORITY_C', 'Unknown')
                customer = row.get('BU_NAME', 'Unknown Customer')
                cssm = row.get('CSSM_EMAIL', 'Unknown CSSM')
                briefing.append(f"- **Action Plan [{plan_id}]** ({customer}, CSSM: {cssm}): {title}")
                briefing.append(f"  Status: {status} | Priority: {priority}")
                if description and description != 'No Description':
                    briefing.append(f"  Description: {description}")
        
        # Customer Pulse - Summary by customer first (quick reference for AI)
        if not csconsole_customer_pulse.empty:
            cust_col = next((c for c in ['BU_NAME', 'CUSTOMER_NAME', 'ACCOUNT__C'] if c in csconsole_customer_pulse.columns), None)
            score_col = next((c for c in ['PULSE_SCORE__C', 'SCORE__C', 'PULSE_SCORE', 'SCORE'] if c in csconsole_customer_pulse.columns), None)
            sentiment_col = next((c for c in ['SENTIMENT__C', 'SENTIMENT'] if c in csconsole_customer_pulse.columns), None)
            if cust_col:
                briefing.append(f"\n**Customer Pulse Summary (by Customer):**")
                for customer in csconsole_customer_pulse[cust_col].dropna().unique():
                    cust_pulse = csconsole_customer_pulse[csconsole_customer_pulse[cust_col] == customer]
                    scores = cust_pulse[score_col].dropna().tolist() if score_col else []
                    sentiments = cust_pulse[sentiment_col].dropna().tolist() if sentiment_col else []
                    latest_score = scores[-1] if scores else 'N/A'
                    latest_sentiment = sentiments[-1] if sentiments else 'N/A'
                    briefing.append(f"- **{customer}:** Score: {latest_score} | Sentiment: {latest_sentiment} | Records: {len(cust_pulse)}")
                briefing.append("")
            briefing.append(f"\n**All Customer Pulse Records ({len(csconsole_customer_pulse)} records):**")
            briefing.append("**CRITICAL INSIGHT:** Customer pulse provides real-time sentiment and engagement indicators for customer success management. This data shows customer satisfaction, engagement levels, and relationship health.")
            briefing.append("**Complete Customer Pulse Data:**")
            for _, row in csconsole_customer_pulse.iterrows():
                pulse_id = row.get('ID', 'Unknown')
                pulse_score = row.get('PULSE_SCORE__C', 'Unknown')
                sentiment = row.get('SENTIMENT__C', 'Unknown')
                engagement = row.get('ENGAGEMENT_LEVEL__C', 'Unknown')
                customer = row.get('BU_NAME', 'Unknown Customer')
                cssm = row.get('CSSM_EMAIL', 'Unknown CSSM')
                briefing.append(f"- **Pulse [{pulse_id}]** ({customer}, CSSM: {cssm})")
                briefing.append(f"  Pulse Score: {pulse_score} | Sentiment: {sentiment} | Engagement: {engagement}")
        
        # Success Priorities - FIXED: Show ALL records
        if not csconsole_success_priorities.empty:
            briefing.append(f"\n**All Success Priorities ({len(csconsole_success_priorities)} records):**")
            briefing.append("**CRITICAL INSIGHT:** Success priorities define key business outcomes and objectives for customer success initiatives. These represent the strategic goals and milestones for customer success.")
            briefing.append("**Complete Success Priorities:**")
            for _, row in csconsole_success_priorities.iterrows():
                priority_id = row.get('ID', 'Unknown')
                name = row.get('PRIORITY_NAME__C', row.get('SUCCESS_PRIORITY_NAME__C', 'No Name'))
                description = row.get('DESCRIPTION__C', 'No Description')
                status = row.get('STATUS__C', 'Unknown')
                priority_level = row.get('PRIORITY_LEVEL__C', 'Unknown')
                customer = row.get('CUSTOMER_BU_NAME__C', 'Unknown Customer')
                briefing.append(f"- **Priority {priority_id}** ({customer}): {name}")
                briefing.append(f"  Status: {status} | Priority Level: {priority_level}")
                if description and description != 'No Description':
                    briefing.append(f"  Description: {description}")
        
        # CSConsole Adoption Barriers - FIXED: Show ALL records
        if not csconsole_adoption_barriers.empty:
            briefing.append(f"\n**All CSConsole Adoption Barriers ({len(csconsole_adoption_barriers)} records):**")
            briefing.append("**CRITICAL INSIGHT:** CSConsole adoption barriers provide additional context beyond standard adoption barrier tracking. These represent specific challenges identified through customer success management processes.")
            briefing.append("**Complete CSConsole Adoption Barriers:**")
            for _, row in csconsole_adoption_barriers.iterrows():
                barrier_id = _first_present(row, ['ID', 'RECORD_ID', 'Record ID'], 'Unknown')
                title = _first_present(
                    row,
                    ['SUBJECT_C', 'TITLE_C', 'NAME_C', 'NAME', 'Barrier Title', 'TASK_TITLE_C'],
                    'No Title',
                )
                description = _first_present(
                    row,
                    ['DESCRIPTION_C', 'COMMENTS_C', 'COMMENTS', 'Description', 'LONG_DESCRIPTION_C'],
                    'No Description',
                )
                status = _first_present(row, ['AB_STATUS_C', 'STATUS_C', 'STATUS', 'Status'], 'Unknown')
                severity = _first_present(row, ['SEVERITY_C', 'PRIORITY_C', 'Severity', 'Priority'], 'Unknown')
                customer = _first_present(row, ['BU_NAME', 'CUSTOMER_NAME', 'Customer Name', 'ACCOUNT_NAME_C'], 'Unknown Customer')
                cssm = _first_present(row, ['CSSM_EMAIL', 'ASSIGNEE_EMAIL', 'Owner Email'], 'Unknown CSSM')
                briefing.append(f"- **Barrier [{barrier_id}]** ({customer}, CSSM: {cssm}): {title}")
                briefing.append(f"  Status: {status} | Severity: {severity}")
                if description and description != 'No Description':
                    briefing.append(f"  Description: {description}")
        
        briefing.append("\n**CSConsole Integration Summary:**")
        briefing.append(f"- **Total Action Plans:** {total_action_plans}")
        briefing.append(f"- **Total Customer Pulse Records:** {total_customer_pulse}")
        briefing.append(f"- **Total Success Priorities:** {total_success_priorities}")
        briefing.append(f"- **Total CSConsole Adoption Barriers:** {total_csconsole_adoption_barriers}")
        briefing.append("---")
    else:
        briefing.append("No CSConsole data found in scope.")

    # External Intelligence
    briefing.append("### External Intelligence:")
    briefing.append(f"* **Publicly Referenced Bugs (help.webex.com):** {len(ext_bugs)}")
    briefing.append(f"* **Recent Service Incidents (status.webex.com):** {len(ext_incidents)}")
    # FIXED: Show ALL matched bugs
    if matches:
        briefing.append(f"* **All Matched Public Bugs in Portfolio:** {', '.join(matches)}")
    if matched_df is not None and not matched_df.empty:
        briefing.append("\n### Records with Matched Public Bugs:")
        briefing.append(_json_lite(matched_df, limit=10))
    
    # Add detailed external intelligence analysis - FIXED: Show ALL bugs and incidents
    if ext_bugs:
        briefing.append("\n### Software Defects Analysis (help.webex.com):")
        briefing.append("**CRITICAL INSIGHT:** Publicly known software defects can significantly impact customer adoption and satisfaction. These represent known issues that may be affecting multiple customers.")
        for bug in ext_bugs:  # Show ALL bugs
            bug_id = bug.get('bug_id', 'Unknown')
            title = bug.get('title', 'No Title')
            briefing.append(f"- **Bug [{bug_id}]:** {title}")
    
    if ext_incidents:
        briefing.append("\n### Service Incident Analysis (status.webex.com):")
        briefing.append("**CRITICAL INSIGHT:** Recent service incidents can directly impact customer experience and adoption rates. These represent system-wide issues that may affect multiple customers.")
        
        # Correlate incidents with customer service cases
        incident_correlations = _correlate_incidents_with_cases(ext_incidents, csone_df, ab_df)
        
        # Show ALL incidents
        for incident in ext_incidents:
            incident_id = (incident.get('id') or 'Unknown')
            title = (incident.get('title') or 'No Title')
            status = (incident.get('status') or 'Unknown')
            published = (incident.get('published') or 'Unknown')
            
            # FIXED: Check for ALL correlations
            correlated_cases = incident_correlations.get(incident_id, [])
            if correlated_cases:
                case_list = ', '.join([f"TAC Case {c.get('case', 'N/A')}" for c in correlated_cases])
                briefing.append(f"- **Incident {incident_id}:** {title} (Status: {status}, Date: {published})")
                briefing.append(f"  → **CORRELATED WITH:** {case_list} - This service incident may have contributed to customer-reported issues")
            else:
                briefing.append(f"- **Incident {incident_id}:** {title} (Status: {status}, Date: {published})")
        
        if incident_correlations:
            briefing.append(f"\n**Correlation Summary:** {len(incident_correlations)} service incidents from status.webex.com correlate with customer service cases, indicating potential service-impacting incidents that affected customers.")
    
    briefing.append("---")

    if db_profile:
        briefing.append("### Database Schema Intelligence:")
        briefing.append("The following is a JSON profile of the source database tables. Use this to understand column names, data types, and common values when interpreting the sample data.")
        briefing.append(json.dumps(db_profile, indent=2, default=str))
        briefing.append("---")

    return "\n".join(briefing)

def _create_executive_briefing_book(manager, ab_norm, team_subs_df, technology):
    """Create a focused briefing book for executive analysis using adoption barriers"""
    briefing = []
    
    briefing.append(f"# Executive Portfolio Analysis - {manager}")
    briefing.append(f"## Technology Focus: {technology}")
    briefing.append(f"## Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    briefing.append("")
    
    # Team overview
    if not team_subs_df.empty:
        briefing.append("## Team Portfolio Overview")
        briefing.append(f"- **Total Team Subscriptions:** {len(team_subs_df)}")
        briefing.append(f"- **Unique Customers:** {team_subs_df['BU_NAME'].nunique() if 'BU_NAME' in team_subs_df.columns else 'N/A'}")
        briefing.append(f"- **Active CSSMs:** {team_subs_df['CSSM_EMAIL'].nunique() if 'CSSM_EMAIL' in team_subs_df.columns else 'N/A'}")
        briefing.append("")
        
        # Customer list - FIXED: Show ALL customers
        if 'BU_NAME' in team_subs_df.columns:
            customers = team_subs_df['BU_NAME'].dropna().unique().tolist()
            briefing.append("### Complete Customer Portfolio:")
            for customer in customers:  # Show ALL customers
                briefing.append(f"- {customer}")
            briefing.append("")
    
    # Adoption barriers analysis
    if not ab_norm.empty:
        briefing.append("## Critical Adoption Barriers Analysis")
        briefing.append(f"- **Total Adoption Barriers:** {len(ab_norm)}")
        
        # Top customers with barriers
        if 'customer_name' in ab_norm.columns:
            customer_barriers = ab_norm['customer_name'].value_counts()
            briefing.append(f"- **Customers with Barriers:** {len(customer_barriers)}")
            briefing.append("")
            
            briefing.append("### All Customers Requiring Attention:")
            # FIXED: Show ALL customers with barriers for complete visibility
            for customer, count in customer_barriers.items():
                briefing.append(f"- **{customer}**: {count} barriers")
            briefing.append("")
        
        # Barrier categories
        if 'ab_category_c' in ab_norm.columns:
            categories = ab_norm['ab_category_c'].value_counts()
            briefing.append("### All Barrier Categories:")
            # FIXED: Show ALL categories
            for category, count in categories.items():
                briefing.append(f"- **{category}**: {count} barriers")
            briefing.append("")
        
        # Severity analysis
        if 'severity_c' in ab_norm.columns:
            severity = ab_norm['severity_c'].value_counts()
            briefing.append("### Severity Distribution:")
            for sev, count in severity.items():
                briefing.append(f"- **{sev}**: {count} barriers")
            briefing.append("")
        
        # Recent barriers (last 30 days)
        if 'open_date_c' in ab_norm.columns:
            try:
                recent_barriers = ab_norm[ab_norm['open_date_c'] >= (datetime.now() - timedelta(days=30))]
                briefing.append(f"### Recent Barriers (Last 30 Days): {len(recent_barriers)}")
                if len(recent_barriers) > 0:
                    briefing.append("- Recent barriers indicate ongoing challenges requiring immediate attention")
                briefing.append("")
            except Exception:
                pass
        
        # FIXED: Show ALL barrier details for comprehensive context
        briefing.append("### Complete Barrier Details:")
        for idx, barrier in ab_norm.iterrows():
            briefing.append(f"**Barrier {idx + 1}:**")
            if 'subject_c' in barrier:
                briefing.append(f"- Subject: {barrier['subject_c']}")
            if 'customer_name' in barrier:
                briefing.append(f"- Customer: {barrier['customer_name']}")
            if 'ab_category_c' in barrier:
                briefing.append(f"- Category: {barrier['ab_category_c']}")
            if 'severity_c' in barrier:
                briefing.append(f"- Severity: {barrier['severity_c']}")
            briefing.append("")
    
    return "\n".join(briefing)

def _create_minimal_briefing_book(manager, ab_norm, team_subs_df, technology):
    """Create a minimal briefing book when data is limited"""
    briefing = []
    
    briefing.append(f"# Executive Portfolio Analysis - {manager}")
    briefing.append(f"## Technology Focus: {technology}")
    briefing.append(f"## Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    briefing.append("")
    
    briefing.append("## Data Summary")
    briefing.append(f"- **Adoption Barriers Available:** {len(ab_norm) if not ab_norm.empty else 0}")
    briefing.append(f"- **Team Subscriptions Available:** {len(team_subs_df) if not team_subs_df.empty else 0}")
    briefing.append("")
    
    if not ab_norm.empty:
        briefing.append("## Available Data Analysis")
        briefing.append("Limited data available for analysis. Focus on available adoption barriers.")
        
        # FIXED: Show ALL customers with data
        if 'customer_name' in ab_norm.columns:
            customers = ab_norm['customer_name'].unique()
            briefing.append(f"- **Customers with Data:** {len(customers)}")
            for customer in customers:
                briefing.append(f"  - {customer}")
        briefing.append("")
    
    briefing.append("## Executive Recommendations")
    briefing.append("- Limited data requires broader portfolio review")
    briefing.append("- Consider expanding data collection scope")
    briefing.append("- Focus on customer engagement and feedback collection")
    
    return "\n".join(briefing)

def _create_executive_briefing_book_with_csone(manager, ab_norm, csone_df, team_subs_df, technology,
        arr_data=None, arr_impact=None, feature_requests=None, software_defects=None, psirt_vulns=None):
    """Create a COMPREHENSIVE briefing book for executive analysis with FULL DATA for AI to generate rich insights.
    Optional kwargs (arr_data, arr_impact, feature_requests, software_defects, psirt_vulns) enrich the briefing when provided."""
    briefing = []
    
    briefing.append(f"# Executive Portfolio Analysis - {manager}")
    briefing.append(f"## Technology Focus: {technology}")
    briefing.append(f"## Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    briefing.append("")
    
    # =========================================================================
    # SECTION 1: TEAM PORTFOLIO OVERVIEW
    # =========================================================================
    if not team_subs_df.empty:
        briefing.append("## Team Portfolio Overview")
        briefing.append(f"- **Total Team Subscriptions:** {len(team_subs_df)}")
        briefing.append(f"- **Unique Customers:** {team_subs_df['BU_NAME'].nunique() if 'BU_NAME' in team_subs_df.columns else 'N/A'}")
        briefing.append(f"- **Active CSSMs:** {team_subs_df['CSSM_EMAIL'].nunique() if 'CSSM_EMAIL' in team_subs_df.columns else 'N/A'}")
        briefing.append("")
        
        # FULL customer list (not truncated - AI needs this for comprehensive analysis)
        if 'BU_NAME' in team_subs_df.columns:
            customers = team_subs_df['BU_NAME'].dropna().unique().tolist()
            briefing.append("### Complete Customer Portfolio:")
            for customer in customers:  # NO LIMIT - include all customers
                briefing.append(f"- {customer}")
            briefing.append("")
    
    # =========================================================================
    # SECTION 2: BEMS ESCALATION ANALYSIS (CRITICAL FOR EXECUTIVE VISIBILITY)
    # =========================================================================
    if not csone_df.empty:
        # Extract BEMS escalations from Transaction ID and bemscsc_refs columns
        bems_mask = pd.Series([False] * len(csone_df), index=csone_df.index)
        
        if 'Transaction ID' in csone_df.columns:
            bems_mask |= csone_df['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
        if 'bemscsc_refs' in csone_df.columns:
            bems_mask |= csone_df['bemscsc_refs'].astype(str).str.contains('BEMS', case=False, na=False)
        
        bems_cases = csone_df[bems_mask]
        total_bems = len(bems_cases)
        bems_rate = (total_bems / len(csone_df) * 100) if len(csone_df) > 0 else 0.0
        
        briefing.append("## 🔴 BEMS ESCALATION ANALYSIS (CRITICAL)")
        briefing.append(f"- **Total BEMS Escalations:** {total_bems}")
        briefing.append(f"- **BEMS Rate:** {bems_rate:.1f}% of all TAC cases")
        briefing.append("")
        
        if total_bems > 0:
            briefing.append("### BEMS Escalations by Customer (with BEMS IDs):")
            if 'customer_name' in bems_cases.columns:
                for customer in bems_cases['customer_name'].unique():
                    customer_bems = bems_cases[bems_cases['customer_name'] == customer]
                    # Extract BEMS IDs
                    bems_ids = set()
                    for _, row in customer_bems.iterrows():
                        if 'Transaction ID' in row and pd.notna(row['Transaction ID']):
                            tid = str(row['Transaction ID'])
                            if 'BEMS' in tid.upper():
                                # Extract BEMS ID patterns
                                import re
                                found = re.findall(r'BEMS\d+', tid, re.IGNORECASE)
                                bems_ids.update(found)
                        if 'bemscsc_refs' in row and pd.notna(row['bemscsc_refs']):
                            refs = str(row['bemscsc_refs'])
                            found = re.findall(r'BEMS\d+', refs, re.IGNORECASE)
                            bems_ids.update(found)
                    
                    # Format all BEMS IDs with brackets for citation
                    bems_id_list = ', '.join([f'[{bid}]' for bid in sorted(bems_ids)]) if bems_ids else 'IDs pending extraction'
                    briefing.append(f"- **{customer}:** {len(customer_bems)} BEMS escalation(s) - {bems_id_list}")
            briefing.append("")
    
    # =========================================================================
    # SECTION 3: COMPREHENSIVE SUPPORT CASES ANALYSIS
    # =========================================================================
    if not csone_df.empty:
        briefing.append("## Support Cases Analysis (CSOne/TAC)")
        briefing.append(f"- **Total Support Cases:** {len(csone_df)}")
        
        # Customers with cases - FULL LIST
        if 'customer_name' in csone_df.columns:
            customer_cases = csone_df['customer_name'].value_counts()
            briefing.append(f"- **Customers with Cases:** {len(customer_cases)}")
            briefing.append("")
            
            briefing.append("### All Customers with Support Cases (sorted by case volume):")
            for customer, count in customer_cases.items():  # ALL customers, not just top 5
                # Get BEMS count for this customer
                cust_mask = csone_df['customer_name'] == customer
                cust_cases = csone_df[cust_mask]
                cust_bems_mask = pd.Series([False] * len(cust_cases), index=cust_cases.index)
                if 'Transaction ID' in cust_cases.columns:
                    cust_bems_mask |= cust_cases['Transaction ID'].astype(str).str.contains('BEMS', case=False, na=False)
                if 'bemscsc_refs' in cust_cases.columns:
                    cust_bems_mask |= cust_cases['bemscsc_refs'].astype(str).str.contains('BEMS', case=False, na=False)
                cust_bems_count = cust_bems_mask.sum()
                
                if cust_bems_count > 0:
                    briefing.append(f"- **{customer}**: {count} cases ({cust_bems_count} BEMS escalations)")
                else:
                    briefing.append(f"- **{customer}**: {count} cases")
            briefing.append("")
        
        # Case severity analysis - FULL
        severity_col = 'Severity' if 'Severity' in csone_df.columns else ('Highest Priority' if 'Highest Priority' in csone_df.columns else None)
        if severity_col:
            severity = csone_df[severity_col].value_counts()
            briefing.append("### Case Severity Distribution:")
            for sev, count in severity.items():
                briefing.append(f"- **Severity {sev}**: {count} cases")
            briefing.append("")
        
        # P1/P2 cases - DETAILED with case numbers
        if severity_col:
            critical_cases = csone_df[csone_df[severity_col].astype(str).str.contains('1|2', na=False)]
            if not critical_cases.empty:
                # FIXED: Show ALL critical cases
                briefing.append("### ALL Critical Cases (P1/P2) - REQUIRES IMMEDIATE ATTENTION:")
                for _, case in critical_cases.iterrows():
                    case_num = case.get('SR Number', case.get('Case Number', 'Unknown'))
                    title = case.get('Title', 'No title')
                    customer = case.get('customer_name', 'Unknown')
                    status = case.get('Case Status', 'Unknown')
                    sev = case.get(severity_col, 'Unknown')
                    briefing.append(f"- TAC #{case_num} ({customer}): {title} - Severity: {sev}, Status: {status}")
                briefing.append("")
        
        # ALL Case Details - COMPREHENSIVE (for AI thematic analysis)
        briefing.append("### Complete TAC Case Details for Analysis:")
        for idx, case in csone_df.iterrows():
            case_num = case.get('SR Number', case.get('Case Number', f'Case-{idx}'))
            title = case.get('Title', 'No title')
            customer = case.get('customer_name', 'Unknown')
            status = case.get('Case Status', 'Unknown')
            sev = case.get(severity_col, 'Unknown') if severity_col else 'Unknown'
            trans_id = case.get('Transaction ID', '')
            
            # Build comprehensive case line
            case_line = f"- TAC #{case_num} | Customer: {customer} | Severity: {sev} | Status: {status}"
            if trans_id and 'BEMS' in str(trans_id).upper():
                case_line += f" | BEMS: {trans_id}"
            case_line += f" | Title: {title}"
            briefing.append(case_line)
            briefing.append("")
    
    # =========================================================================
    # SECTION 4: COMPREHENSIVE ADOPTION BARRIERS ANALYSIS  
    # =========================================================================
    if not ab_norm.empty:
        briefing.append("## Critical Adoption Barriers Analysis")
        briefing.append(f"- **Total Adoption Barriers:** {len(ab_norm)}")
        
        # Open vs Closed barriers (status visibility)
        status_col = 'AB_STATUS_C' if 'AB_STATUS_C' in ab_norm.columns else ('STATUS_C' if 'STATUS_C' in ab_norm.columns else None)
        if status_col:
            status_counts = ab_norm[status_col].astype(str).str.strip().value_counts()
            open_keywords = ['open', 'new', 'in progress', 'pending']
            closed_keywords = ['closed', 'resolved', 'completed', 'cancelled']
            open_count = sum(c for s, c in status_counts.items() if any(k in s.lower() for k in open_keywords))
            closed_count = sum(c for s, c in status_counts.items() if any(k in s.lower() for k in closed_keywords))
            other_count = len(ab_norm) - open_count - closed_count
            briefing.append(f"- **Open/Active Barriers:** {open_count} | **Closed/Resolved:** {closed_count}" + (f" | **Other:** {other_count}" if other_count > 0 else ""))
        briefing.append("")
        
        # ALL customers with barriers (not truncated)
        if 'customer_name' in ab_norm.columns:
            customer_barriers = ab_norm['customer_name'].value_counts()
            briefing.append(f"- **Customers with Barriers:** {len(customer_barriers)}")
            briefing.append("")
            
            briefing.append("### All Customers with Adoption Barriers (sorted by barrier count):")
            for customer, count in customer_barriers.items():  # ALL customers
                briefing.append(f"- **{customer}**: {count} barriers")
            briefing.append("")
        
        # Barrier categories - ALL categories
        cat_col = 'ab_category_c' if 'ab_category_c' in ab_norm.columns else ('ab_category_final' if 'ab_category_final' in ab_norm.columns else None)
        if cat_col:
            categories = ab_norm[cat_col].value_counts()
            briefing.append("### Barrier Categories (complete breakdown):")
            for category, count in categories.items():  # ALL categories
                briefing.append(f"- **{category}**: {count} barriers")
            briefing.append("")
        
        # Severity analysis - ALL severities
        sev_col = 'severity_c' if 'severity_c' in ab_norm.columns else ('SEVERITY_C' if 'SEVERITY_C' in ab_norm.columns else None)
        if sev_col:
            severity = ab_norm[sev_col].value_counts()
            briefing.append("### Barrier Severity Distribution:")
            for sev, count in severity.items():
                briefing.append(f"- **{sev}**: {count} barriers")
            briefing.append("")
        
            # Highlight HIGH/CRITICAL barriers
            high_sev = ab_norm[ab_norm[sev_col].astype(str).str.contains('High|Critical', case=False, na=False)]
            if not high_sev.empty:
                briefing.append("### HIGH/CRITICAL Severity Barriers - REQUIRES ATTENTION:")
                for _, barrier in high_sev.iterrows():
                    subj = barrier.get('SUBJECT_C', barrier.get('subject_c', barrier.get('title', 'No subject')))
                    customer = barrier.get('customer_name', 'Unknown')
                    sev = barrier.get(sev_col, 'Unknown')
                    status = barrier.get('AB_STATUS_C', barrier.get('STATUS_C', 'Unknown'))
                    briefing.append(f"- **{customer}**: {subj} - Severity: {sev}, Status: {status}")
                briefing.append("")
        
        # ALL Adoption Barrier Titles - COMPREHENSIVE for AI thematic analysis
        briefing.append("### All Adoption Barrier Titles for Thematic Analysis:")
        subj_col = 'SUBJECT_C' if 'SUBJECT_C' in ab_norm.columns else ('subject_c' if 'subject_c' in ab_norm.columns else ('title' if 'title' in ab_norm.columns else None))
        if subj_col:
            for _, barrier in ab_norm.iterrows():
                customer = barrier.get('customer_name', 'Unknown')
                subject = barrier.get(subj_col, 'No subject')
                sev = barrier.get(sev_col, 'N/A') if sev_col else 'N/A'
                cat = barrier.get(cat_col, 'Uncategorized') if cat_col else 'Uncategorized'
                briefing.append(f"- [{customer}] {subject} (Severity: {sev}, Category: {cat})")
        briefing.append("")
        
        # Complete barrier details with record IDs for traceability
        briefing.append("### Complete Adoption Barrier Details (with CSConsole Record IDs):")
        for idx, barrier in ab_norm.iterrows():
            record_id = barrier.get('ID', barrier.get('RECORD_ID', f'AB-{idx}'))
            subj = barrier.get('SUBJECT_C', barrier.get('subject_c', barrier.get('title', 'No subject')))
            customer = barrier.get('customer_name', 'Unknown')
            sev = barrier.get(sev_col, 'N/A') if sev_col else 'N/A'
            cat = barrier.get(cat_col, 'Uncategorized') if cat_col else 'Uncategorized'
            status = barrier.get('AB_STATUS_C', barrier.get('STATUS_C', 'Unknown'))
            product = barrier.get('PRODUCT_C', barrier.get('CSS_PRE_UNLINK_TECHNOLOGY_NAME_C', 'Unknown'))
            
            briefing.append(f"**CSConsole Record: {record_id}**")
            briefing.append(f"  - Customer: {customer}")
            briefing.append(f"  - Subject: {subj}")
            briefing.append(f"  - Severity: {sev}")
            briefing.append(f"  - Category: {cat}")
            briefing.append(f"  - Status: {status}")
            briefing.append(f"  - Product: {product}")
            briefing.append("")
    
    # =========================================================================
    # SECTION 4b: OPTIONAL ENRICHMENT (ARR, Feature Requests, Defects, PSIRT)
    # =========================================================================
    if arr_data is not None and not arr_data.empty and 'BU_NAME' in arr_data.columns and 'ANNUAL_CONTRACT_VALUE' in arr_data.columns:
        arr_by_cust = arr_data.groupby('BU_NAME')['ANNUAL_CONTRACT_VALUE'].sum().sort_values(ascending=False)
        total_arr = arr_by_cust.sum()
        briefing.append("## ARR by Customer (Strategic Prioritization)")
        briefing.append("**Prioritize high-ARR customers with adoption barriers or support cases.**")
        for cust, arr_val in arr_by_cust.head(20).items():
            pct = (arr_val / total_arr * 100) if total_arr > 0 else 0
            briefing.append(f"- **{cust}:** ${arr_val:,.0f} ({pct:.1f}% of portfolio)")
        if len(arr_by_cust) > 20:
            briefing.append(f"- ... and {len(arr_by_cust) - 20} more customers")
        briefing.append(f"**Total Portfolio ARR:** ${total_arr:,.0f}")
        briefing.append("")
    
    if arr_impact and arr_impact.get('top_issues'):
        briefing.append("## ARR at Risk by Issue Category")
        briefing.append("**Prioritize interventions for issue categories with highest ARR exposure.**")
        for issue_name, data in arr_impact['top_issues'][:10]:
            arr_val = data.get('arr', 0)
            cust_count = data.get('customer_count', 0)
            briefing.append(f"- **{issue_name}:** ${arr_val:,.0f} at risk ({cust_count} customers)")
        briefing.append("")
    
    if feature_requests and feature_requests.get('total_requests', 0) > 0:
        briefing.append("## Feature Requests (Product Gap Signal)")
        briefing.append(f"**{feature_requests['total_requests']}** cases contain feature requests. ARR impact: ${feature_requests.get('total_arr_impact', 0):,.0f}")
        if feature_requests.get('top_features'):
            briefing.append("**Most requested themes:**")
            for theme, count in feature_requests['top_features'][:5]:
                briefing.append(f"- {theme}: {count} cases")
        if feature_requests.get('customer_examples'):
            top_by_arr = sorted([c for c in feature_requests['customer_examples'] if c.get('arr', 0) > 0], key=lambda x: x.get('arr', 0), reverse=True)[:5]
            if top_by_arr:
                briefing.append("**High-value customers requesting features:**")
                for c in top_by_arr:
                    briefing.append(f"- {c.get('customer', 'N/A')}: ${c.get('arr', 0):,.0f} ARR")
        briefing.append("")
    
    if software_defects and software_defects.get('total_defects', 0) > 0:
        briefing.append("## Software Defects (BST/CSC IDs in Cases)")
        briefing.append(f"**{software_defects['total_defects']}** defect references in **{software_defects.get('total_cases_with_defects', 0)}** cases.")
        defect_by_cust = software_defects.get('defect_by_customer', {})
        if defect_by_cust:
            for cust, refs in sorted(defect_by_cust.items(), key=lambda x: -len(x[1]))[:8]:
                briefing.append(f"- **{cust}:** {', '.join(list(refs)[:5])}{'...' if len(refs) > 5 else ''}")
        briefing.append("")
    
    if psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0:
        briefing.append("## PSIRT / Security Vulnerabilities (in Cases)")
        cves = psirt_vulns.get('cve_ids', set())
        psirts = psirt_vulns.get('psirt_advisories', set())
        briefing.append(f"**{len(cves)} CVE(s), {len(psirts)} PSIRT advisory(ies)** referenced in case text. Security-sensitive.")
        vuln_by_cust = psirt_vulns.get('vulnerability_by_customer', {})
        if vuln_by_cust:
            for cust, refs in sorted(vuln_by_cust.items(), key=lambda x: -len(x[1]))[:5]:
                briefing.append(f"- **{cust}:** {', '.join(list(refs)[:3])}{'...' if len(refs) > 3 else ''}")
        briefing.append("")
    
    # =========================================================================
    # SECTION 5: DATA QUALITY AND SOURCES SUMMARY
    # =========================================================================
    briefing.append("## Data Sources and Quality Summary")
    briefing.append("This analysis is based on the following data sources:")
    briefing.append(f"- **CSConsole (Snowflake)**: {len(ab_norm) if not ab_norm.empty else 0} adoption barriers")
    briefing.append(f"- **CSOne (TAC Cases)**: {len(csone_df) if not csone_df.empty else 0} support cases")
    briefing.append(f"- **Team Subscriptions**: {len(team_subs_df) if not team_subs_df.empty else 0} subscriptions")
    briefing.append("")
    briefing.append("**Data Citation Requirement**: All metrics in this report are traceable to source systems:")
    briefing.append("- TAC Cases: Reference by SR Number (e.g., TAC #699864043)")
    briefing.append("- Adoption Barriers: Reference by CSConsole Record ID (e.g., CSConsole Record: aGte6000000fZUrCAM)")
    briefing.append("- BEMS Escalations: Reference by BEMS ID (e.g., BEMS01916938)")
    briefing.append("")
    
    return "\n".join(briefing)

PROMPT_PORTFOLIO_TEMPLATE = """
# {MANAGER}'s Team Portfolio - {TECHNOLOGY} Executive Overview

**YOUR MISSION:** Give executives **VISIBILITY INTO WHAT'S REALLY HAPPENING**. Surface the trouble spots, critical defects, escalations, and adoption barriers that need executive attention. Be direct, data-driven, and problem-focused.

**CRITICAL REQUIREMENTS:**
- **Show the Problems:** Don't sugarcoat - executives need to see the real issues
- **Cite Specifics:** Reference actual defect IDs, BEMS IDs, TAC case numbers, adoption barrier details
- **Quantify Impact:** How many customers? What's the business impact? 
- **Flag Escalations:** BEMS escalations are RED FLAGS - call them out explicitly
- **Define Barriers:** Clearly explain what adoption barriers are blocking customers

**TECHNOLOGY FOCUS:** This analysis covers **{TECHNOLOGY}** adoption and support within {MANAGER}'s portfolio.

---

## **Portfolio Health Score: [A/B/C/D/F]**

**Grade Justification:**
*State the grade and justify with SPECIFIC metrics: X BEMS escalations, Y critical defects affecting Z customers, chronic issues in W accounts, escalation rate at V%. Be direct about whether this portfolio is healthy, at-risk, or in crisis.*

---

## **Executive Summary: What's Really Happening**

**Portfolio Snapshot:**
• **Total Customers:** [Number]
• **Active Adoption Barriers:** [Number with severity breakdown]
• **TAC Cases:** [Total with P1/P2 count]
• **BEMS Escalations:** [Count - THIS IS CRITICAL]
• **Known Defects Impacting Portfolio:** [Count from help.webex.com]
• **Trend Direction:** [Improving/Stable/Deteriorating with evidence]

**The Truth About This Portfolio** (3-4 sentences):
*What's the real situation? Are customers struggling with specific features? Is there a pattern of escalations? Are defects blocking adoption? What's keeping customers from success? Be honest and direct.*

---

## **🔴 Critical Trouble Spots - Executive Attention Required**

*These are the RED FLAGS that need immediate visibility:*

### **BEMS Escalations** (Complex Engineering Issues)
*BEMS escalations indicate problems requiring back-end engineering - these are serious:*

• **[Customer Name]:** [X BEMS cases] - **BEMS IDs:** [List actual BEMS IDs] - [Brief problem description]
• **[Customer Name]:** [X BEMS cases] - **BEMS IDs:** [List actual BEMS IDs] - [Brief problem description]
• **[Customer Name]:** [X BEMS cases] - **BEMS IDs:** [List actual BEMS IDs] - [Brief problem description]

**Total BEMS Impact:** [X customers with Y total BEMS escalations - what this means for the portfolio]

### **Critical Defects Affecting Customers**
*Known software defects from help.webex.com that are impacting your customers:*

• **[Defect ID]:** [Short description] - **Impacts:** [List affected customers] - **Status:** [Open/Fixed/Workaround]
• **[Defect ID]:** [Short description] - **Impacts:** [List affected customers] - **Status:** [Open/Fixed/Workaround]
• **[Defect ID]:** [Short description] - **Impacts:** [List affected customers] - **Status:** [Open/Fixed/Workaround]

### **High-Severity TAC Cases**
*P1/P2 cases requiring immediate attention:*

• **[Customer]:** P1 - **Case:** [Number] - [Problem description] - [Days open]
• **[Customer]:** P2 - **Case:** [Number] - [Problem description] - [Days open]

---

## **ALL Customers in Trouble (Sorted by Risk)**

*List ALL customers with severe issues - sorted by risk level. Do NOT limit to just 5 - include every customer that has problems:*

**1. [Customer Name] - Risk Level: [HIGH/CRITICAL]**
• **Problem Summary:** [What's really going wrong?]
• **Adoption Barriers:** [X barriers] - **Key Issue:** [Main blocking issue with details]
• **TAC Cases:** [Y cases, Z are P1/P2] - **Active Problems:** [Specific issues]
• **BEMS Escalations:** [If any, with BEMS IDs like [BEMS01916938]]
• **Defects Impacting:** [List any known defects affecting this customer with IDs like [CSCxx12345]]
• **Business Impact:** [How is this affecting their business?]
• **Immediate Action Needed:** [Specific, actionable next step]

**2. [Customer Name] - Risk Level: [HIGH/MEDIUM]**
• [Same detailed format - continue for ALL troubled customers]

**3. [Customer Name] - Risk Level: [MEDIUM]**
• [Same detailed format]

**4. [Customer Name] - Risk Level: [MEDIUM]**
• [Same detailed format]

**5. [Customer Name] - Risk Level: [MEDIUM/LOW]**
• [Same detailed format]

---

## **Common Problems Across Portfolio**

*Identify the patterns - what's broken or blocking adoption across multiple customers:*

### **Problem Pattern #1: [Descriptive Name of the Issue]**
• **What's Happening:** [Clear description of the problem customers are facing]
• **Adoption Barriers:** [Specific barriers related to this - cite titles/details]
• **Customers Affected:** [List 5-7 customers experiencing this]
• **Related Defects:** [Any known bugs contributing to this]
• **Root Cause:** [What's causing this problem]
• **Business Impact:** [How this blocks adoption, causes downtime, or frustrates users]
• **Fix Required:** [What needs to happen to resolve this]

### **Problem Pattern #2: [Descriptive Name of the Issue]**
• [Same detailed format]

### **Problem Pattern #3: [Descriptive Name of the Issue]**
• [Same detailed format]

---

## **Adoption Barriers Breakdown**

*What specific barriers are blocking customer success:*

**By Type/Theme:**
• **[Barrier Theme 1]:** [X customers] - [Description and examples]
• **[Barrier Theme 2]:** [Y customers] - [Description and examples]
• **[Barrier Theme 3]:** [Z customers] - [Description and examples]

**High-Severity Barriers:**
*List 3-5 most critical adoption barriers with complete details:*
• **[Customer]:** [Full barrier description] - **Severity:** [Level] - **Status:** [Open/Working/Resolved]
• **[Customer]:** [Full barrier description] - **Severity:** [Level] - **Status:** [Open/Working/Resolved]

---

## **Executive Action Plan: What We Need To Do**

**🔥 IMMEDIATE (This Week):**
1. **[Specific Action]** - Target: [Customer/Issue] - Owner: [Name] - Why: [Critical reason]
2. **[Specific Action]** - Target: [Customer/Issue] - Owner: [Name] - Why: [Critical reason]
3. **[Specific Action]** - Target: [Customer/Issue] - Owner: [Name] - Why: [Critical reason]

**📋 SHORT-TERM (30-60 Days):**
1. **[Initiative]** - Addresses: [Problem] - Expected Impact: [Outcome]
2. **[Initiative]** - Addresses: [Problem] - Expected Impact: [Outcome]
3. **[Initiative]** - Addresses: [Problem] - Expected Impact: [Outcome]

**🎯 STRATEGIC (90+ Days):**
1. **[Strategic Investment]** - Prevents: [Future issues] - Value: [Long-term benefit]

---

## **Service Incidents & External Factors**

*Recent service incidents or platform issues affecting customers:*
• **[Incident from status.webex.com]:** [Impact on portfolio customers]
• **[Platform Issue]:** [How this is affecting adoption/experience]

---

## **Positive Momentum** (Brief)

*Quick wins and successes to balance the trouble focus:*
• [Success or resolved issue]
• [Positive trend or customer achievement]

---

**DATA SOURCES:** This analysis references specific adoption barriers (CSConsole), TAC cases (CSOne), BEMS escalations, known defects (help.webex.com), and service incidents (status.webex.com). Individual customer deep dives follow this portfolio overview.
"""

PROMPT_CUSTOMER_TEMPLATE = """
## **Role & Goal**
You are CircuIT, an expert **Principal Technical Analyst and Business Strategist**. Your mission is to perform a **DEEP DIVE** on a single customer, **{CUSTOMER_NAME}**, and synthesize their specific data into a compelling, insightful, and actionable executive narrative for their assigned CSSM, **{CSSM_NAME}**.

**TECHNOLOGY FOCUS:** This analysis is specifically focused on **{TECHNOLOGY}** technology adoption, barriers, and support cases for this customer within {MANAGER}'s team portfolio (managed by CSSMs reporting to {MANAGER}).

**CRITICAL INSTRUCTION:** When analyzing Adoption Barriers, your primary goal is to perform a **thematic analysis** of the barrier titles and descriptions specifically related to {TECHNOLOGY}. Do not simply state that they are uncategorized. Instead, read the text provided in the "All Adoption Barrier Titles for Thematic Analysis" section and group them into meaningful themes (e.g., 'Requests for Training,' 'Feature Gaps,' 'Internal Political Blockers'). Your value is in converting this unstructured text into strategic insight focused on {TECHNOLOGY}.

**SOURCE CITATION REQUIREMENT:** When referencing specific data points, you MUST include the proper source citation in parentheses:
- For CSConsole records: `(CSConsole Record: [ID])`
- For CSOne/TAC cases: `(TAC Case: [Case Number])`

**CSConsole DATA ANALYSIS REQUIREMENT:** You MUST comprehensively analyze and reference CSConsole data for this customer including:
- **Action Plans:** Strategic initiatives specific to this customer and their impact
- **Customer Pulse:** Real-time sentiment and engagement indicators for this customer
- **Success Priorities:** Key business outcomes and objectives for this customer
- **CSConsole Adoption Barriers:** Additional context beyond standard tracking for this customer
- **CSSM Attribution:** Always reference the responsible CSSM ({CSSM_NAME}) for accountability

**FULL TEXT REQUIREMENT:** When quoting or referencing specific adoption barriers or CSOne cases, you MUST include the complete, untruncated text. Do not use ellipses (...) or truncate descriptions. The complete details are provided in the "Complete Adoption Barrier Details" and "Complete CSOne (TAC) Case Details" sections.

## **Advanced Analytical Framework**
When analyzing the data for this customer, you MUST think through these lenses specifically in the context of {TECHNOLOGY}:

### **Technical Analysis**
1.  **Technical Debt:** Are their recurring issues a symptom of aging infrastructure, outdated software, or postponed maintenance related to {TECHNOLOGY}?
2.  **Operational Maturity:** Do they have robust processes for change management, upgrades, and monitoring of {TECHNOLOGY}?
3.  **Enablement Gaps:** Are their issues caused by a lack of knowledge or training on {TECHNOLOGY}?
4.  **Strategic Misalignment:** Are they using {TECHNOLOGY} in a way it wasn't intended, or is there a feature gap?

### **Business Impact Analysis**
5.  **Financial Impact:** Quantify the business cost of {TECHNOLOGY} issues (downtime, productivity loss, opportunity cost)
6.  **Competitive Risk:** How do {TECHNOLOGY} challenges affect their market position and competitive advantage
7.  **Innovation Velocity:** Impact on their ability to adopt new technologies and drive digital transformation
8.  **Customer Experience:** How {TECHNOLOGY} issues affect their end customers and service delivery

### **Predictive Analysis**
9.  **Risk Trajectory:** Where is this customer heading based on current patterns and trends
10. **Success Probability:** Likelihood of successful {TECHNOLOGY} adoption and value realization
11. **Intervention Timing:** Optimal timing for proactive engagement and support
12. **Growth Potential:** Opportunities for expanded {TECHNOLOGY} usage and business expansion

## **Output Format & Content**
Generate a detailed, customer-specific report in Markdown. Do NOT omit any headers; state 'None detected' if a section is empty.

# AdoptIQ Executive Analysis: {CUSTOMER_NAME} - {TECHNOLOGY} Focus
**Assigned CSSM:** {CSSM_NAME}
**Technology Focus:** {TECHNOLOGY}

### **Customer Health Score: [A, B, C, D, F]**
*Provide a comprehensive 3-4 sentence justification based on this customer's specific data related to {TECHNOLOGY} adoption and support. Include specific metrics, trend analysis, and strategic implications.*

### **1. Advanced Trend Analysis & Pattern Recognition**
*Identify the top 3-5 recurring issue patterns for THIS CUSTOMER specifically related to {TECHNOLOGY}. Perform sophisticated thematic analysis of their titles and descriptions to create meaningful insights.*

**Pattern 1: [Theme Name]**
- **Frequency:** [Count and percentage of this customer's {TECHNOLOGY}-related cases]
- **Severity Trend:** [Increasing, stable, or decreasing over time]
- **Root Cause Analysis:** [Deep dive into underlying causes using your analytical framework]
- **Business Impact:** [Quantified impact on operations, costs, and strategic goals]
- **Evidence:** [2-3 powerful examples with complete, untruncated text and proper source citations]

**Pattern 2: [Theme Name]**
- [Same detailed structure as Pattern 1]

**Pattern 3: [Theme Name]**
- [Same detailed structure as Pattern 1]

### **2. Comprehensive Business Impact Assessment**
*   **Operational Disruption:** *[Detailed analysis of how {TECHNOLOGY} technical issues translate into business terms, including specific metrics and examples.]*
*   **Financial Impact:** *[Quantified cost analysis including downtime, productivity loss, and opportunity costs.]*
*   **Strategic Headwinds:** *[How these {TECHNOLOGY} issues slow down this customer's strategic goals and competitive positioning.]*
*   **Customer Experience Impact:** *[How {TECHNOLOGY} issues affect their end customers and service delivery quality.]*

### **3. Advanced Customer Pulse & Sentiment Analysis**
*   **Pulse Rating:** [Good (Green), Average (Yellow), or Poor (Red)]
*   **Sentiment Indicators:** [Specific evidence of customer satisfaction, frustration, or engagement levels]
*   **Relationship Health:** [Assessment of the overall customer relationship and trust levels]
*   **Engagement Quality:** [Depth of technical discussions, proactive vs reactive interactions]
*   **Communication Patterns:** [Frequency, tone, and escalation patterns in support interactions]

### **4. Strategic Technology Assessment**
*   **{TECHNOLOGY} Maturity Level:** [Novice/Developing/Proficient/Advanced/Expert with specific evidence]
*   **Adoption Velocity:** [Rate of {TECHNOLOGY} feature adoption and expansion]
*   **Technical Competency:** [Internal team capabilities and knowledge gaps]
*   **Integration Complexity:** [Challenges with existing infrastructure and systems]
*   **Innovation Readiness:** [Willingness and capability to adopt new {TECHNOLOGY} features]

### **5. Competitive Intelligence & Market Context**
*   **Industry Benchmarking:** [How this customer's {TECHNOLOGY} adoption compares to industry peers]
*   **Competitive Positioning:** [How {TECHNOLOGY} challenges affect their market position]
*   **Market Opportunities:** [Untapped potential and expansion possibilities]
*   **Technology Evolution Impact:** [How emerging trends affect their {TECHNOLOGY} strategy]

### **6. External Intelligence & Market Factors**
*   **Software Defects Impact:** [How publicly known bugs (help.webex.com) may correlate with this customer's specific issues]
*   **Service Incident Correlation:** [Impact of recent service incidents (status.webex.com) on this customer's experience]
*   **Cross-Reference Analysis:** [Specific customer issues that align with known software defects or service incidents]
*   **External Risk Assessment:** [How external factors (bugs, incidents) affect this customer's {TECHNOLOGY} adoption and satisfaction]
*   **Engagement Quality:** [Analysis of how actively and constructively the customer engages with support]
*   **Justification:** [A comprehensive, evidence-driven summary justifying the pulse rating. Focus on customer experience with {TECHNOLOGY}, outcomes, trends, risks, case history, problem severity, recurrence, and escalation patterns.]*

### **4. Predictive Risk Assessment**
*   **Churn Risk Level:** [Low, Medium, High, Critical]
*   **Risk Factors:** [Specific indicators that suggest potential issues or opportunities, **including BEMS escalation patterns**]
*   **BEMS Risk Analysis:** [If BEMS escalations exist for this customer, **list the count and specific BEMS IDs** (e.g., "3 BEMS escalations: BEMS-12345, BEMS-67890, BEMS-11111"). If no BEMS, state "No BEMS escalations". These indicate complex technical issues requiring specialized engineering expertise]
*   **Success Probability:** [Likelihood of successful {TECHNOLOGY} adoption and value realization]
*   **Timeline Projections:** [Where this customer is heading in the next 6-12 months]
*   **Early Warning Signs:** [Specific patterns that require immediate attention, **especially BEMS escalation trends**]

### **5. Strategic Recommendations & Action Plan**
*Provide 4-6 prioritized, **S.M.A.R.T.** recommendations for this specific customer focused on {TECHNOLOGY}.*

**Recommendation 1: [Priority Level]**
- **Problem Statement:** [Detailed description of the {TECHNOLOGY}-related problem with quantified impact]
- **Root Cause:** [Underlying cause analysis]
- **Action Plan:** [Specific, concrete steps with timeline and milestones]
- **Resource Requirements:** [People, tools, budget, and time needed]
- **Owner:** [Specific role or team responsible]
- **Success Metrics:** [Measurable goals for {TECHNOLOGY} adoption/success]
- **Risk Mitigation:** [Potential challenges and how to address them]
- **Expected Outcomes:** [Quantified benefits and timeline for realization]

**Recommendation 2: [Priority Level]**
- [Same detailed structure as Recommendation 1]

**Recommendation 3: [Priority Level]**
- [Same detailed structure as Recommendation 1]

### **6. Competitive Intelligence & Market Context**
*   **Industry Benchmarking:** [How this customer's {TECHNOLOGY} adoption compares to industry standards]
*   **Competitive Positioning:** [Their {TECHNOLOGY} capabilities vs. market alternatives]
*   **Market Trends Impact:** [External factors affecting their {TECHNOLOGY} strategy]
*   **Innovation Opportunities:** [Emerging {TECHNOLOGY} capabilities they could leverage]

### **7. Success Metrics & Monitoring Plan**
*   **Key Performance Indicators:** [Specific metrics to track {TECHNOLOGY} success]
*   **Monitoring Frequency:** [How often to review progress and adjust strategy]
*   **Escalation Triggers:** [Specific conditions that require immediate intervention]
*   **Success Celebration:** [How to recognize and reinforce positive outcomes]
"""

def _json_lite(df: pd.DataFrame, limit=60, keep=None) -> str:
    if df is None or df.empty:
        return "[]"
    use = df.copy()
    if keep:
        use = use[[c for c in keep if c in use.columns]]
    return use.head(limit).to_json(orient="records")

def generate_llm_response(system_prompt: str, briefing_book: str) -> str:
    """Generic function to call the CircuIT client with timeout and fallback."""
    import signal
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
    
    def call_circuit_ai():
        """Call CircuIT AI in a separate thread with proper error handling."""
        try:
            if not (CIRCUIT_CONFIG.get("client_id") and CIRCUIT_CONFIG.get("client_secret") and CIRCUIT_CONFIG.get("app_key")):
                return "ERROR: CircuIT credentials not set. Add CIRCUIT_CLIENT_ID, CIRCUIT_CLIENT_SECRET, and CIRCUIT_APP_KEY to your .env file (see .env.template)."
            logger.info(f"[[AI]] Creating CircuIT AI client...")
            client = CircuitChatClient(
                client_id=CIRCUIT_CONFIG["client_id"],
                client_secret=CIRCUIT_CONFIG["client_secret"],
                app_key=CIRCUIT_CONFIG["app_key"],
                model_name=CIRCUIT_CONFIG["model_name"]
            )
            
            logger.info(f"[[AI]] Calling CircuIT AI with 60s timeout...")
            result = client.complete(system_prompt, briefing_book)
            return result
        except Exception as e:
            logger.error(f"[[ERROR]] CircuIT AI call failed: {e}")
            return None
    
    try:
        # Use ThreadPoolExecutor with timeout to prevent hanging
        logger.info(f"[[AI]] Starting CircuIT AI analysis with 60-second timeout...")
        
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(call_circuit_ai)
            result = future.result(timeout=60)  # 60-second timeout
            
            if result and result.strip() and not result.startswith("ERROR:"):
                logger.info(f"[[OK]] CircuIT AI response received: {len(result)} characters")
                return result
            else:
                logger.warning(f"[[WARNING]] CircuIT AI returned invalid response: {result}")
                return "ERROR: CircuIT summarization failed - invalid response."
                
    except FutureTimeoutError:
        logger.error(f"⏰ CircuIT AI call timed out after 60 seconds")
        return "ERROR: CircuIT summarization timed out. The AI service may be under heavy load. Please try again later."
    except Exception as e:
        logger.error(f"[[ERROR]] Unexpected error in CircuIT AI call: {e}")
        return "ERROR: CircuIT summarization failed due to unexpected error."

# --------------------------- Core flow ---------------------------
def _apply_scope_filter_ab(df: pd.DataFrame, tech: str, days: int) -> pd.DataFrame:
    if df is None or df.empty: 
        logger.debug("AB filter: Input DataFrame is empty or None")
        return df
    
    logger.debug(f"AB filter: Starting with {len(df)} adoption barriers")
    logger.debug(f"AB filter: Technology='{tech}', Days={days}")
    logger.debug(f"AB filter: Available columns: {list(df.columns)}")
    
    use = df.copy()
    # date
    date_cols = [c for c in ["OPEN_DATE_C","CREATED_DATE","CREATED_DATE_C"] if c in use.columns]
    if date_cols:
        logger.debug(f"AB filter: Applying date filter using column '{date_cols[0]}'")
        use["__date"] = to_datetime(use[date_cols[0]], errors="coerce", utc=True)
        cutoff = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days)
        logger.debug(f"AB filter: Date cutoff: {cutoff}")
        before_date_filter = len(use)
        use = use[use["__date"] >= cutoff]
        logger.debug(f"AB filter: After date filter: {len(use)} records (removed {before_date_filter - len(use)})")
    else:
        logger.debug("AB filter: No date columns found, skipping date filter")
        
    # tech
    if tech != "All":
        if tech == "All Contact Center":
            logger.warning("AB filter: Skipping tech filter for 'All Contact Center' (insufficient tech fields)")
            logger.debug(f"AB filter: Final result: {len(use)} adoption barriers")
            return use
        logger.debug(f"AB filter: Applying technology filter for '{tech}'")
        cols_to_search = [
            "PRODUCT_C", "PRODUCT_NAME_C", "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C",
            "SUCCESS_TRACK_C", "SUBJECT_C", "DESCRIPTION_C",
            "NAME", "C_360_PRODUCT_SERVICE_NAME_C", "TASK_TYPE_C",
            "ACTION_TYPE_C", "ACTION_PLAN_TITLE_C", "USE_CASE_BU_NAME_C",
            "PROGRAM_NAME_C", "BU_NICKNAME_C"
        ]
        cols = [c for c in cols_to_search if c in use.columns]
        logger.debug(f"AB filter: Technology filter columns: {cols}")
        mask = False
        for c in cols:
            mask = mask | use[c].astype(str).str.lower().apply(lambda t: _filter_tech_text(t, tech))
        before_tech_filter = len(use)
        filtered = use[mask]
        # If tech filter yields nothing (or unrealistically few) keep original to avoid blank report
        min_expected = max(5, int(before_tech_filter * 0.1))
        if before_tech_filter > 0 and (filtered.empty or (tech == "All Contact Center" and len(filtered) < min_expected)):
            logger.warning(f"AB filter: Tech '{tech}' matched {len(filtered)} of {before_tech_filter}; using unfiltered ABs")
        else:
            use = filtered
            logger.debug(f"AB filter: After technology filter: {len(use)} records (removed {before_tech_filter - len(use)})")
    else:
        logger.debug("AB filter: Technology='All', skipping technology filter")
        
    logger.debug(f"AB filter: Final result: {len(use)} adoption barriers")
    return use

def _apply_scope_filter_csone(df: pd.DataFrame, tech: str, days: int, sub_ids: List[str], team_customer_names: List[str]) -> pd.DataFrame:
    if df is None or df.empty: 
        logger.debug("CSOne filter: Input DataFrame is empty or None")
        return pd.DataFrame()
    
    logger.debug(f"CSOne filter: Starting with {len(df)} cases")
    logger.debug(f"CSOne filter: Technology='{tech}', Days={days}")
    logger.debug(f"CSOne filter: Team customer names: {team_customer_names[:5]}...")
    logger.debug(f"CSOne filter: Available columns: {list(df.columns)}")
    
    use = df.copy()

    sub_col = next((c for c in LIKELY_SUB_COLS if c in use.columns), None)
    cust_col = 'customer_name' # Standardized name from _prepare_csone
    
    logger.debug(f"CSOne filter: Subscription column='{sub_col}', Customer column='{cust_col}'")

    filtered_dfs = []
    if sub_col and sub_ids:
        use[sub_col] = use[sub_col].astype(str)
        sub_ids_str = [str(s) for s in sub_ids]
        
        logger.debug(f"CSOne filter: Looking for subscription IDs: {sub_ids_str[:5]}...")
        
        valid_sub_mask = use[sub_col].str.lower().str.startswith('sub', na=False)
        logger.debug(f"CSOne filter: Found {valid_sub_mask.sum()} rows with valid subscription format")
        
        by_sub = use[valid_sub_mask & use[sub_col].isin(sub_ids_str)]
        logger.debug(f"CSOne filter: Found {len(by_sub)} rows matching team subscription IDs")
        filtered_dfs.append(by_sub)
        
        no_valid_sub_df = use[~valid_sub_mask]
        if not no_valid_sub_df.empty and cust_col in no_valid_sub_df.columns:
            logger.info(f"Falling back to Customer Name filter for {len(no_valid_sub_df)} CSOne rows with non-standard Subscription IDs.")
            by_name = no_valid_sub_df[no_valid_sub_df[cust_col].isin(team_customer_names)]
            logger.debug(f"CSOne filter: Found {len(by_name)} rows matching team customer names")
            filtered_dfs.append(by_name)
    elif cust_col in use.columns:
        logger.warning("No subscription ID column found in Excel. Falling back to filtering by Customer Name.")
        by_name = use[use[cust_col].isin(team_customer_names)]
        logger.debug(f"CSOne filter: Found {len(by_name)} rows matching team customer names")
        filtered_dfs.append(by_name)
    else:
        logger.warning("Could not find Subscription ID or Customer Name column in Excel. CSOne data cannot be scoped to the team.")
        return pd.DataFrame()

    if not filtered_dfs:
        logger.debug("CSOne filter: No filtered dataframes created")
        return pd.DataFrame()
        
    use = pd.concat(filtered_dfs).drop_duplicates()
    logger.debug(f"CSOne filter: After team filtering: {len(use)} cases")

    # date filter
    date_cols = [c for c in use.columns if c in LIKELY_DATE_COLS]
    if date_cols:
        logger.debug(f"CSOne filter: Applying date filter using column '{date_cols[0]}'")
        use["__date"] = pd.to_datetime(use[date_cols[0]], errors="coerce", utc=True)
        cutoff = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days)
        logger.debug(f"CSOne filter: Date cutoff: {cutoff}")
        before_date_filter = len(use)
        use = use[use["__date"] >= cutoff]
        logger.debug(f"CSOne filter: After date filter: {len(use)} cases (removed {before_date_filter - len(use)})")
    else:
        logger.debug("CSOne filter: No date columns found, skipping date filter")
        
    # tech filter
    if tech != "All":
        logger.debug(f"CSOne filter: Applying enhanced technology filter for '{tech}'")
        
        # Check if we have both Tech and Sub Technology columns
        tech_col = next((c for c in ['Technology', 'Tech', 'PRODUCT'] if c in use.columns), None)
        sub_tech_col = next((c for c in ['Sub Technology', 'Sub_Technology', 'SUB_TECHNOLOGY'] if c in use.columns), None)
        
        logger.debug(f"CSOne filter: Tech column='{tech_col}', Sub Technology column='{sub_tech_col}'")
        
        if tech_col and sub_tech_col:
            # Use enhanced filtering with both fields
            logger.debug("CSOne filter: Using enhanced filtering with both Tech and Sub Technology fields")
            def enhanced_match(row):
                return _filter_tech_text_enhanced(
                    row.get(tech_col), 
                    row.get(sub_tech_col), 
                    tech
                )
            before_tech_filter = len(use)
            use = use[use.apply(enhanced_match, axis=1)]
            logger.debug(f"CSOne filter: After enhanced technology filter: {len(use)} cases (removed {before_tech_filter - len(use)})")
        else:
            # Fallback to original filtering
            logger.debug("CSOne filter: Using fallback filtering (missing Tech or Sub Technology columns)")
            cols = [c for c in use.columns if c in LIKELY_TECH_COLS] or [c for c in use.columns if c in LIKELY_TITLE_COLS | LIKELY_DESC_COLS]
            logger.debug(f"CSOne filter: Technology filter columns: {cols}")
            
            # Show sample technology values for debugging
            if cols:
                for col in cols[:3]:  # Show first 3 columns
                    if col in use.columns:
                        sample_values = use[col].dropna().unique()[:10]  # First 10 unique values
                        logger.debug(f"CSOne filter: Sample values in '{col}': {list(sample_values)}")
            
            def any_match(row):
                for c in cols:
                    if _filter_tech_text(row.get(c), tech): return True
                return False
            before_tech_filter = len(use)
            use = use[use.apply(any_match, axis=1)]
            logger.debug(f"CSOne filter: After fallback technology filter: {len(use)} cases (removed {before_tech_filter - len(use)})")
    else:
        logger.debug("CSOne filter: Technology='All', skipping technology filter")
        
    logger.debug(f"CSOne filter: Final result: {len(use)} cases")
    return use

def _apply_scope_filter_csone_inclusive(csone_df, technology, days):
    """Apply inclusive filtering to CSOne data for executive analysis - only technology and date filters"""
    if csone_df is None or csone_df.empty:
        return pd.DataFrame() if csone_df is None else csone_df
    
    logger.debug(f"CSOne inclusive filter: Starting with {len(csone_df)} cases")
    logger.debug(f"CSOne inclusive filter: Technology='{technology}', Days={days}")
    
    filtered_df = csone_df.copy()
    
    # Apply date filter only
    if 'Date/Time Opened' in filtered_df.columns:
        logger.debug("CSOne inclusive filter: Applying date filter using column 'Date/Time Opened'")
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_date = cutoff_date.replace(tzinfo=None)  # Remove timezone for comparison
        
        # Convert date column to datetime if needed
        try:
            filtered_df['Date/Time Opened'] = pd.to_datetime(filtered_df['Date/Time Opened'], errors='coerce')
            before_filter = len(filtered_df)
            filtered_df = filtered_df[filtered_df['Date/Time Opened'] >= cutoff_date]
            after_filter = len(filtered_df)
            logger.debug(f"CSOne inclusive filter: After date filter: {after_filter} cases (removed {before_filter - after_filter})")
        except Exception as e:
            logger.warning(f"[WARN] CSOne inclusive filter: Date filtering failed: {e}")
    
    # Apply technology filter only (more lenient)
    if technology and technology != "All Technologies":
        logger.debug(f"CSOne inclusive filter: Applying technology filter for '{technology}'")
        
        # Get technology patterns
        tech_patterns = TECH_FILTERS.get(technology, [])
        if tech_patterns:
            # Create a combined pattern for all technology patterns (non-capturing to avoid pandas warning)
            combined_pattern = '|'.join(tech_patterns)
            combined_pattern = re.sub(r'\((?![\?<])', r'(?:', combined_pattern)  # ( not followed by ? or < (lookahead/lookbehind)
            
            # Apply to relevant columns
            tech_columns = ['Sub Technology', 'Title', 'Problem Description', 'Technology Lookup: Technology Auto Number']
            available_columns = [col for col in tech_columns if col in filtered_df.columns]
            
            if available_columns:
                logger.debug(f"CSOne inclusive filter: Technology filter columns: {available_columns}")
                
                # Create a mask for any column matching the technology (suppress pandas "match groups" warning)
                mask = pd.Series([False] * len(filtered_df), index=filtered_df.index)
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='.*match groups.*', category=UserWarning)
                    for col in available_columns:
                        if not filtered_df[col].empty:
                            col_mask = filtered_df[col].astype(str).str.contains(combined_pattern, case=False, na=False, regex=True)
                            mask = mask | col_mask
                
                before_filter = len(filtered_df)
                filtered_df = filtered_df[mask]
                after_filter = len(filtered_df)
                logger.debug(f"CSOne inclusive filter: After technology filter: {after_filter} cases (removed {before_filter - after_filter})")
            else:
                logger.warning(f"[WARN] CSOne inclusive filter: No technology columns found")
    
    logger.debug(f"CSOne inclusive filter: Final result: {len(filtered_df)} cases")
    return filtered_df

def _filter_csconsole_data_by_technology(df: pd.DataFrame, technology: str, customer_names: List[str] = None) -> pd.DataFrame:
    """
    Filter CSConsole data (Action Plans, Customer Pulse, etc.) by technology and customer names.
    This ensures CSConsole data matches the technology scope selected by the user.
    
    Args:
        df: CSConsole DataFrame to filter
        technology: Technology filter (e.g., "All Contact Center", "Webex Meetings & Messaging")
        customer_names: Optional list of customer names to filter by (from team subscriptions)
    
    Returns:
        Filtered DataFrame
    """
    if df is None or df.empty:
        logger.info(f"[[FILTER]] CSConsole filter: Input DataFrame is empty or None")
        return df
    
    logger.info(f"[[FILTER]] CSConsole filter: Starting with {len(df)} records for technology '{technology}'")
    
    filtered_df = df.copy()
    
    # Filter by customer names if provided (ensures only team's customers are included)
    if customer_names:
        before_count = len(filtered_df)
        # Check both BU_NAME and CUSTOMER_NAME columns
        if 'BU_NAME' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['BU_NAME'].isin(customer_names)]
        elif 'CUSTOMER_NAME' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['CUSTOMER_NAME'].isin(customer_names)]
        
        after_count = len(filtered_df)
        logger.info(f"[[FILTER]] CSConsole filter: After customer filter: {after_count} records (removed {before_count - after_count})")
    
    # Filter by technology if not "All"
    if technology and technology not in ["All", "All Technologies"]:
        tech_patterns = TECH_FILTERS.get(technology, [])
        if tech_patterns:
            before_count = len(filtered_df)
            combined_pattern = '|'.join(tech_patterns)
            # Use non-capturing groups to avoid pandas "match groups" warning with str.contains (don't replace (? or (< lookbehind)
            combined_pattern = re.sub(r'\((?![\?<])', r'(?:', combined_pattern)
            
            # Technology columns to check in CSConsole data
            tech_columns = [
                'SUB_TECHNOLOGY_C', 'TECHNOLOGY_C',  # From dsm_assignment_data join
                'SUBJECT_C', 'DESCRIPTION_C',  # Text fields that may contain tech info
                'CSS_PRE_UNLINK_TECHNOLOGY_NAME_C', 'PRODUCT_NAME_C', 'PRODUCT_C'  # Product fields
            ]
            
            available_columns = [col for col in tech_columns if col in filtered_df.columns]
            
            if available_columns:
                logger.info(f"[[FILTER]] CSConsole filter: Checking technology in columns: {available_columns}")

                # Prefer enhanced matcher so WxCC/WxCCE disambiguation stays consistent with CSOne filtering.
                tech_col = next((c for c in ['TECHNOLOGY_C', 'CSS_PRE_UNLINK_TECHNOLOGY_NAME_C', 'PRODUCT_NAME_C', 'PRODUCT_C'] if c in filtered_df.columns), None)
                sub_tech_col = 'SUB_TECHNOLOGY_C' if 'SUB_TECHNOLOGY_C' in filtered_df.columns else None

                if tech_col or sub_tech_col:
                    mask = filtered_df.apply(
                        lambda row: _filter_tech_text_enhanced(
                            row.get(tech_col) if tech_col else "",
                            row.get(sub_tech_col) if sub_tech_col else "",
                            technology,
                        ),
                        axis=1,
                    )
                else:
                    # Fallback for non-standard datasets without explicit technology columns.
                    mask = pd.Series([False] * len(filtered_df), index=filtered_df.index)
                    with warnings.catch_warnings():
                        warnings.filterwarnings('ignore', message='.*match groups.*', category=UserWarning)
                        for col in available_columns:
                            try:
                                col_mask = filtered_df[col].astype(str).str.contains(combined_pattern, case=False, na=False, regex=True)
                                mask = mask | col_mask
                            except Exception:
                                pass

                filtered_df = filtered_df[mask]
                after_count = len(filtered_df)
                logger.info(f"[[FILTER]] CSConsole filter: After technology filter: {after_count} records (removed {before_count - after_count})")
            else:
                logger.warning(f"[[FILTER]] CSConsole filter: No technology columns found - skipping tech filter")
    
    logger.info(f"[[FILTER]] CSConsole filter: Final result: {len(filtered_df)} records")
    return filtered_df

def _prepare_ab(df: pd.DataFrame, dsm_df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    use = df.copy()
    use.rename(columns={"CUSTOMER_NAME": "customer_name"}, inplace=True, errors='ignore')
    if "BU_NAME" in use.columns: use.rename(columns={"BU_NAME":"customer_name"}, inplace=True)
    if "CSSM_EMAIL" in use.columns: use.rename(columns={"CSSM_EMAIL":"assignee_cssm_email"}, inplace=True)
    
    # Title: use first available column that looks like subject/title (EDW/CSConsole/view naming)
    _html = lambda x: re.sub(r'<.*?>', '', str(x)).strip() if x is not None and str(x) else ''
    _s = lambda c: use[c].fillna('').astype(str).apply(_html) if c in use.columns else pd.Series([''] * len(use), index=use.index)
    def _clean_text(v):
        if v is None:
            return ''
        try:
            if getattr(pd, 'isna', None) and pd.isna(v):
                return ''
        except Exception:
            pass
        s = str(v).strip()
        if not s or s.lower() in ('nan', 'none'):
            return ''
        return s

    def _first_by_hint(row, hints, strip_html=False):
        idx = getattr(row, 'index', ())
        for col in idx:
            if not isinstance(col, str):
                continue
            col_lower = col.lower()
            if not any(h in col_lower for h in hints):
                continue
            s = _clean_text(row.get(col))
            if not s:
                continue
            return _html(s) if strip_html else s
        return ''
    title_col = next((c for c in ['SUBJECT_C', 'subject_c', 'TITLE_C', 'NAME', 'Subject', 'Title', 'NAME_C'] if c in use.columns), None)
    desc_col = next((c for c in ['DESCRIPTION_C', 'description_c', 'Description', 'DESC_C'] if c in use.columns), None)
    use["title"] = _s(title_col) if title_col else pd.Series([''] * len(use), index=use.index)
    use["description"] = _s(desc_col) if desc_col else pd.Series([''] * len(use), index=use.index)
    use['title'] = use.apply(lambda r: (r['title'] or r['description']), axis=1)
    # Final fallback: infer title from any column containing subject/title/name/desc/summary
    use['title'] = use.apply(
        lambda r: (_clean_text(r.get('title')) or _first_by_hint(r, ['subject', 'title', 'name', 'desc', 'summary'], strip_html=True)),
        axis=1
    )

    # Normalize severity/status into SEVERITY_C/AB_STATUS_C so report always finds them
    sev_col = next((c for c in ['SEVERITY_C', 'severity_c', 'Severity'] if c in use.columns), None)
    use['SEVERITY_C'] = use[sev_col].fillna('').astype(str) if sev_col else pd.Series([''] * len(use), index=use.index)
    status_col = next((c for c in ['AB_STATUS_C', 'ab_status_c', 'STATUS_C', 'status_c', 'Status'] if c in use.columns), None)
    use['AB_STATUS_C'] = use[status_col].fillna('').astype(str) if status_col else pd.Series([''] * len(use), index=use.index)
    # Fallback: pull severity/status from any similar-named columns if still empty
    use['SEVERITY_C'] = use.apply(
        lambda r: (_clean_text(r.get('SEVERITY_C')) or _first_by_hint(r, ['severity', 'priority'])),
        axis=1
    )
    use['AB_STATUS_C'] = use.apply(
        lambda r: (_clean_text(r.get('AB_STATUS_C')) or _first_by_hint(r, ['status', 'state'])),
        axis=1
    )

    cust_col = use.get("customer_name") if "customer_name" in use.columns else use.get("ACCOUNT_ID_C")
    use["customer_name"] = cust_col if cust_col is not None else pd.Series(["Unknown"] * len(use), index=use.index)
    use["ab_category_final"] = use.get("AB_CATEGORY_C").apply(_normalize_category) if "AB_CATEGORY_C" in use.columns else "Uncategorized"
    _tech = lambda c: use[c].fillna("").astype(str) if c in use.columns else pd.Series([""] * len(use), index=use.index)
    tech_txt = _tech("CSS_PRE_UNLINK_TECHNOLOGY_NAME_C") + " " + _tech("PRODUCT_NAME_C") + " " + _tech("PRODUCT_C")
    use["sub_technology"] = tech_txt.apply(_normalize_subtech) if hasattr(tech_txt, "apply") else "Other/Unknown"
    use["assignee_cssm_email"] = use.get("assignee_cssm_email")
    use["bemscsc_refs"] = (use["title"].astype(str) + " " + use["description"].astype(str)).apply(_extract_refs)
    return use

def _prepare_csone(df: pd.DataFrame, team_subs_df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: 
        logger.debug("CSOne prepare: Input DataFrame is empty or None")
        return df
    
    logger.debug(f"CSOne prepare: Starting with {len(df)} cases")
    logger.debug(f"CSOne prepare: Available columns: {list(df.columns)}")
    
    use = df.copy()
    
    original_cust_col = 'customer_name_orig'
    found_customer_col = False
    for col_name in LIKELY_CUST_COLS:
        if col_name in use.columns:
            logger.debug(f"CSOne prepare: Found customer column '{col_name}', renaming to '{original_cust_col}'")
            use.rename(columns={col_name: original_cust_col}, inplace=True)
            found_customer_col = True
            break
    if not found_customer_col:
        logger.debug("CSOne prepare: No customer column found, creating default")
        use[original_cust_col] = "Unknown Customer (from Excel)"

    sub_col = next((c for c in LIKELY_SUB_COLS if c in use.columns), None)
    logger.debug(f"CSOne prepare: Subscription column='{sub_col}'")
    
    if sub_col:
        use[sub_col] = use[sub_col].astype(str)
        
        # Check if team_subs_df has data and the required column before merging
        if team_subs_df is not None and not team_subs_df.empty and 'SUBSCRIPTION_ID' in team_subs_df.columns:
            team_subs_df['SUBSCRIPTION_ID'] = team_subs_df['SUBSCRIPTION_ID'].astype(str)
            
            logger.debug(f"CSOne prepare: Merging with team subscription data ({len(team_subs_df)} team subscriptions)")
            use = pd.merge(use, team_subs_df[['SUBSCRIPTION_ID', 'BU_NAME']], left_on=sub_col, right_on='SUBSCRIPTION_ID', how='left')
            
            use['customer_name'] = use['BU_NAME'].fillna(use[original_cust_col])
            use.drop(columns=['BU_NAME', original_cust_col], inplace=True, errors='ignore')
            logger.debug(f"CSOne prepare: After merge: {len(use)} cases")
        else:
            logger.debug("CSOne prepare: No team subscription data available, using customer names from CSOne directly")
            use.rename(columns={original_cust_col: 'customer_name'}, inplace=True)
    else:
        logger.debug("CSOne prepare: No subscription column found, using customer names directly")
        use.rename(columns={original_cust_col: 'customer_name'}, inplace=True)

    title_col = next((c for c in LIKELY_TITLE_COLS if c in use.columns), None)
    desc_col = next((c for c in LIKELY_DESC_COLS if c in use.columns), None)
    _title = use[title_col].fillna("").astype(str) if title_col else pd.Series([""] * len(use), index=use.index)
    _desc = use[desc_col].fillna("").astype(str) if desc_col else pd.Series([""] * len(use), index=use.index)
    use["bemscsc_refs"] = (_title + " " + _desc).apply(_extract_refs)
    logger.debug(f"CSOne prepare: Final result: {len(use)} cases with customer names")
    return use

def _counts_by(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns: return pd.DataFrame()
    return df.groupby(col).size().reset_index(name="count").sort_values("count", ascending=False)

def _portfolio_grade(total_ab: int, esc_rate: float, chronic_rate: float) -> str:
    if total_ab >= 100 or esc_rate >= 30 or chronic_rate >= 30: return "D"
    if total_ab >= 50 or esc_rate >= 20 or chronic_rate >= 20: return "C"
    if total_ab < 10: return "B"
    return "B"

def _calc_rates(csone_df: pd.DataFrame):
    if csone_df is None or csone_df.empty: return 0.0, 0.0
    
    # Get title and description columns safely (use LIKELY_* for robustness)
    title_col = next((c for c in LIKELY_TITLE_COLS if c in csone_df.columns), None)
    desc_col = next((c for c in LIKELY_DESC_COLS if c in csone_df.columns), None)
    title_str = csone_df[title_col].fillna("").astype(str) if title_col else pd.Series([""] * len(csone_df), index=csone_df.index)
    desc_str = csone_df[desc_col].fillna("").astype(str) if desc_col else pd.Series([""] * len(csone_df), index=csone_df.index)
    txt = (title_str + " " + desc_str).str.lower()
    
    escal = int(txt.str.contains(r"\bescalat|sev[-\s]?1|urgent|executive").sum())
    chronic = 0
    total = len(csone_df)
    return round(100*escal/total,1) if total else 0.0, round(100*chronic/total,1) if total else 0.0

def _integrity_checks(ab_df: pd.DataFrame, csone_df: pd.DataFrame) -> Optional[str]:
    if (ab_df is None or ab_df.empty) and (csone_df is None or csone_df.empty):
        return "No Adoption Barriers or CSOne cases found in scope."
    if ab_df is not None and not ab_df.empty:
        essential_blank = (ab_df["title"].isna() | (ab_df["title"].astype(str).str.strip()=="")) & (ab_df["description"].isna() | (ab_df["description"].astype(str).str.strip()==""))
        if essential_blank.mean() > 0.25:
            return "Essential fields missing/blank over 25% (Title/Description) in Adoption Barriers."
        fut = 0
        for c in ["OPEN_DATE_C","CREATED_DATE","CREATED_DATE_C"]:
            if c in ab_df.columns:
                s = pd.to_datetime(ab_df[c], errors="coerce", utc=True)
                fut += int((s > pd.Timestamp.now(tz="UTC")).sum())
        if fut > 0:
            return f"Detected {fut} future-dated AB rows."
    return None

def _pick_manager() -> str:
    print("\nManagers:")
    for i, m in enumerate(MANAGERS, 1): print(f"{i}) {m}")
    choice = input("Select manager: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(MANAGERS): return MANAGERS[int(choice)-1]
    return "All Managers"

def _pick_tech() -> str:
    print("\nTech Scopes:")
    for i, t in enumerate(TECH_CHOICES, 1): print(f"{i}) {t}")
    choice = input("Select technology: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(TECH_CHOICES): return TECH_CHOICES[int(choice)-1]
    return "All"

def _pick_days(default=90) -> int:
    try:
        d = int(input(f"Days to analyze [{default}]: ").strip() or default)
        return max(1, d)
    except Exception:
        return default

def _pick_csone() -> Optional[Path]:
    here = Path(".")
    cands = sorted(here.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        v = input("No .xlsx in folder. Enter CSOne path (or blank to skip): ").strip()
        return Path(v) if v else None
    top = cands[0]
    v = input(f"Use newest CSOne file: {top.name}? [Y/n]: ").strip().lower()
    if v in ("","y","yes"): return top
    alt = input("Enter path to CSOne .xlsx (or blank to skip): ").strip()
    return Path(alt) if alt else None

def main():
    """Main function with proper resource management and error handling"""
    print("AdoptIQ — All‑in‑One (CircuIT-only) + External Intelligence")
    print("Please wait while we setup the environment...")
    
    ctx = None
    try:
        out_dir = _ensure_outputs()
        manager = _pick_manager()
        tech = _pick_tech()
        days = _pick_days(90)
        csone_path = _pick_csone()
        db_profile = load_db_profile()

        # CSSM emails in scope
        team_roster_df = pd.DataFrame(TEAM_ROSTER, columns=["manager_name","cssm_name","cssm_email"])
        if manager != "All Managers":
            team_roster_df = team_roster_df[team_roster_df["manager_name"] == manager]
        cssm_emails = team_roster_df["cssm_email"].dropna().unique().tolist()

        # === STRATEGIC UPGRADE: Use Subscription ID as the primary key ===
        print("\nConnecting to Snowflake...")
        ctx = _connect_with_keeper()
        print("Fetching subscriptions for the selected team...")
        team_subs_df = get_subscriptions_for_team(ctx, cssm_emails)
        
        if team_subs_df.empty:
            print(f"\n[WARN] No subscriptions found in DSM for the team of '{manager}'. Exiting.")
            return

        team_subs_df = team_subs_df.merge(team_roster_df, left_on="CSSM_EMAIL", right_on="cssm_email", how="left")
        cssm_lookup = pd.Series(team_subs_df.cssm_name.values, index=team_subs_df.BU_NAME).to_dict()
        sub_ids = team_subs_df["SUBSCRIPTION_ID"].dropna().unique().tolist()
        account_ids = team_subs_df["ACCOUNT_ID_C"].dropna().unique().tolist()
        team_customer_names = team_subs_df["BU_NAME"].dropna().unique().tolist()
        print(f"Found {len(sub_ids)} subscriptions across {len(account_ids)} accounts in scope.")

        print("Fetching ARR data for strategic prioritization...")
        arr_data = fetch_arr_data(ctx, account_ids) if account_ids else pd.DataFrame()
        print("Fetching Adoption Barriers...")
        ab_raw = fetch_adoption_barriers(ctx, account_ids, days)
        if not ab_raw.empty and "ACCOUNT_ID_C" in ab_raw.columns:
            ab_raw = ab_raw.merge(team_subs_df[["ACCOUNT_ID_C","BU_NAME","CSSM_EMAIL"]].drop_duplicates(), on="ACCOUNT_ID_C", how="left")

        # Fetch CSConsole data for comprehensive analysis
        print("Fetching CSConsole data (Action Plans, Customer Pulse, Success Priorities)...")
        csconsole_action_plans = fetch_csconsole_action_plans(ctx, account_ids, days)
        csconsole_customer_pulse = fetch_csconsole_customer_pulse(ctx, account_ids, days)
        csconsole_success_priorities = fetch_csconsole_success_priorities(ctx, account_ids, days)
        csconsole_adoption_barriers = fetch_csconsole_adoption_barriers(ctx, account_ids, days)
        
        print(f"Found {len(csconsole_action_plans)} action plans, {len(csconsole_customer_pulse)} customer pulse records, "
              f"{len(csconsole_success_priorities)} success priorities, and {len(csconsole_adoption_barriers)} adoption barriers from CSConsole.")

        # Keep CSConsole scope aligned to selected technology + team customers (Defect #2 parity with Flask flow).
        filtered_action_plans = _filter_csconsole_data_by_technology(csconsole_action_plans, tech, team_customer_names)
        filtered_customer_pulse = _filter_csconsole_data_by_technology(csconsole_customer_pulse, tech, team_customer_names)
        filtered_success_priorities = _filter_csconsole_data_by_technology(csconsole_success_priorities, tech, team_customer_names)
        filtered_adoption_barriers = _filter_csconsole_data_by_technology(csconsole_adoption_barriers, tech, team_customer_names)

        ab_scoped = _apply_scope_filter_ab(ab_raw, tech, days)
        ab_norm = _prepare_ab(ab_scoped, team_subs_df)

        csone_df_raw = load_csone_excel(csone_path) if csone_path else pd.DataFrame()
        csone_df_prepared = _prepare_csone(csone_df_raw, team_subs_df)
        csone_df = _apply_scope_filter_csone(csone_df_prepared, tech, days, sub_ids, team_customer_names)

        # Integrity gates
        reason = _integrity_checks(ab_norm, csone_df)
        ts = time.strftime("%Y%m%d_%H%M%S")
        tag = f"{manager.replace(' ','_')}_{tech.replace(' ','_').replace('&','and')}_{days}d_{ts}"
        base = str(out_dir / f"AdoptIQ_{tag}")
        
        # Create Word document
        doc = Document()
        
        # Fetch external intelligence
        print("Fetching external intelligence (Help Center bugs & Status incidents)...")
        ext_bugs = fetch_help_webex_bugs()
        ext_incidents = fetch_status_incidents()
        print(f"Found {len(ext_bugs)} Help Center bug references and {len(ext_incidents)} status incidents.")
        
        # Generate portfolio summary
        if not ab_norm.empty:
            ab_counts = ab_norm['customer_name'].value_counts().reset_index()
            ab_counts.columns = ['customer_name', 'ab_count']
        else:
            ab_counts = pd.DataFrame(columns=['customer_name', 'ab_count'])

        if not csone_df.empty:
            csone_counts = csone_df['customer_name'].value_counts().reset_index()
            csone_counts.columns = ['customer_name', 'csone_count']
        else:
            csone_counts = pd.DataFrame(columns=['customer_name', 'csone_count'])

        # FIXED: Show ALL customers by engagement
        engagement = pd.merge(ab_counts, csone_counts, on='customer_name', how='outer').fillna(0)
        engagement['total_engagements'] = engagement['ab_count'] + engagement['csone_count']
        engagement_summary = engagement.sort_values('total_engagements', ascending=False)

        portfolio_briefing = _create_briefing_book(f"{manager}'s Portfolio", ab_norm, csone_df, ext_bugs, ext_incidents, [], pd.DataFrame(), None, engagement_summary, {
            'action_plans': filtered_action_plans,
            'customer_pulse': filtered_customer_pulse,
            'success_priorities': filtered_success_priorities,
            'adoption_barriers': filtered_adoption_barriers
        }, arr_data=arr_data if arr_data is not None and not arr_data.empty else None)
        # Inject learned insights from past analyses when available (no extra cost when empty)
        learned = get_learned_insights(manager, tech, limit=5)
        if learned:
            portfolio_briefing = learned + "\n\n---\n\n" + portfolio_briefing
        portfolio_prompt = PROMPT_PORTFOLIO_TEMPLATE.format(MANAGER=manager, TECHNOLOGY=tech)
        portfolio_summary = generate_llm_response(portfolio_prompt, portfolio_briefing)
        append_to_word_report(doc, portfolio_summary)

        # Generate customer deep dives
        customer_series = []
        if ab_norm is not None and not ab_norm.empty and 'customer_name' in ab_norm.columns:
            customer_series.append(ab_norm['customer_name'])
        if csone_df is not None and not csone_df.empty and 'customer_name' in csone_df.columns:
            customer_series.append(csone_df['customer_name'])
        
        # Add customers from CSConsole data
        if not filtered_action_plans.empty and 'BU_NAME' in filtered_action_plans.columns:
            customer_series.append(filtered_action_plans['BU_NAME'])
        if not filtered_customer_pulse.empty and 'BU_NAME' in filtered_customer_pulse.columns:
            customer_series.append(filtered_customer_pulse['BU_NAME'])
        if not filtered_success_priorities.empty and 'CUSTOMER_BU_NAME__C' in filtered_success_priorities.columns:
            customer_series.append(filtered_success_priorities['CUSTOMER_BU_NAME__C'])
        if not filtered_adoption_barriers.empty and 'BU_NAME' in filtered_adoption_barriers.columns:
            customer_series.append(filtered_adoption_barriers['BU_NAME'])
        
        if not customer_series:
            print("\n[INFO] No customer activity found in either Adoption Barriers, CSOne, or CSConsole data. No deep dives to generate.")
            all_customers = []
        else:
            all_customers = pd.concat(customer_series).dropna().unique()

        print(f"\nFound {len(all_customers)} customers with activity. Generating deep dives...")

        for i, customer_name in enumerate(all_customers, 1):
            print(f"  ({i}/{len(all_customers)}) Generating StoryBoard for: {customer_name}...")
            cust_ab = ab_norm[ab_norm['customer_name'] == customer_name].copy() if ab_norm is not None and 'customer_name' in ab_norm else pd.DataFrame()
            cust_csone = csone_df[csone_df['customer_name'] == customer_name].copy() if csone_df is not None and 'customer_name' in csone_df else pd.DataFrame()
            
            # Filter CSConsole data for this customer
            cust_action_plans = filtered_action_plans[filtered_action_plans['BU_NAME'] == customer_name].copy() if not filtered_action_plans.empty and 'BU_NAME' in filtered_action_plans.columns else pd.DataFrame()
            cust_customer_pulse = filtered_customer_pulse[filtered_customer_pulse['BU_NAME'] == customer_name].copy() if not filtered_customer_pulse.empty and 'BU_NAME' in filtered_customer_pulse.columns else pd.DataFrame()
            cust_success_priorities = filtered_success_priorities[filtered_success_priorities['CUSTOMER_BU_NAME__C'] == customer_name].copy() if not filtered_success_priorities.empty and 'CUSTOMER_BU_NAME__C' in filtered_success_priorities.columns else pd.DataFrame()
            cust_csconsole_adoption_barriers = filtered_adoption_barriers[filtered_adoption_barriers['BU_NAME'] == customer_name].copy() if not filtered_adoption_barriers.empty and 'BU_NAME' in filtered_adoption_barriers.columns else pd.DataFrame()
            
            if cust_ab.empty and cust_csone.empty and cust_action_plans.empty and cust_customer_pulse.empty and cust_success_priorities.empty and cust_csconsole_adoption_barriers.empty:
                continue

            cssm_name = cssm_lookup.get(customer_name, "N/A")
            matches, matched_df = cross_reference_refs(cust_ab, cust_csone, ext_bugs)
            
            customer_csconsole_data = {
                'action_plans': cust_action_plans,
                'customer_pulse': cust_customer_pulse,
                'success_priorities': cust_success_priorities,
                'adoption_barriers': cust_csconsole_adoption_barriers
            }
            
            customer_briefing = _create_briefing_book(customer_name, cust_ab, cust_csone, ext_bugs, ext_incidents, matches, matched_df, db_profile, None, customer_csconsole_data)
            customer_prompt = PROMPT_CUSTOMER_TEMPLATE.format(CUSTOMER_NAME=customer_name, CSSM_NAME=cssm_name)
            
            customer_storyboard = generate_llm_response(customer_prompt, customer_briefing)
            append_to_word_report(doc, customer_storyboard)

        # Save Word document
        docx_path = f"{base}.docx"
        doc.save(docx_path)
        
        # Create enhanced Word report
        try:
            enhanced_docx_path = create_enhanced_word_report(
                manager, tech, days, ab_norm, csone_df, 
                {"portfolio_summary": {"portfolio_health_score": "B", "executive_summary": "Portfolio analysis completed successfully"}},
                ext_bugs, ext_incidents
            )
            print(f"   Enhanced Word: {enhanced_docx_path}")
        except Exception as e:
            print(f"   Enhanced Word report failed: {e}")
        
        # Write Excel file
        final_ab_output = pd.DataFrame()
        if not ab_norm.empty:
            final_ab_output = ab_norm.copy()
            final_ab_output.rename(columns={'customer_name': 'Customer Name', 'assignee_cssm_email': 'CSSM Email', 'bemscsc_refs': 'Bug/Enhancement Refs'}, inplace=True)

        final_csone_output = pd.DataFrame()
        if not csone_df.empty:
            final_csone_output = csone_df.copy()
            final_csone_output.rename(columns={'customer_name': 'Customer Name', 'Owner Email': 'Case Owner', 'bemscsc_refs': 'Bug/Enhancement Refs'}, inplace=True)

        sheets = {
            "AB_Detail_All": final_ab_output,
            "CSOne_Detail_All": final_csone_output,
            "External_Bugs": pd.DataFrame(ext_bugs),
            "External_Incidents": pd.DataFrame(ext_incidents),
            "Cross_References": matched_df if not matched_df.empty else pd.DataFrame()
        }
        write_excel_workbook(base, sheets)
        
        print(f"\nSUCCESS: Analysis complete! Files generated:")
        print(f"   Word Report: {docx_path}")
        print(f"   Excel Report: {base}.xlsx")
        
    except Exception as e:
        print(f"\nERROR: Analysis failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure database connection is properly closed
        if ctx:
            try:
                ctx.close()
                print("Database connection closed.")
            except Exception as e:
                print(f"Warning: Error closing database connection: {e}")

if __name__ == "__main__":
    main()
def enhanced_append_to_word_report(*args, **kwargs):
    '''Enhanced version with executive-level formatting'''
    # This function is for appending to Word reports, not generating them
    # So we just use the standard function
    return original_append_to_word_report(*args, **kwargs)

# Backup original function
original_append_to_word_report = append_to_word_report

# Replace with enhanced version
append_to_word_report = enhanced_append_to_word_report

PROMPT_COMPACT_EXECUTIVE_TEMPLATE = """
# {MANAGER}'s Portfolio - {TECHNOLOGY} Executive Brief

**YOUR MISSION:** Give executives **VISIBILITY INTO WHAT'S REALLY HAPPENING**. Surface the trouble spots, critical defects, escalations, and adoption barriers. Be direct and problem-focused.

**CRITICAL REQUIREMENTS:**
- **Show the Problems:** Executives need to see the real issues - don't sugarcoat
- **Cite Specifics:** Reference actual defect IDs, BEMS IDs, TAC case numbers, adoption barrier details
- **Quantify Impact:** How many customers? What's the business impact?
- **Flag Escalations:** BEMS escalations are RED FLAGS - call them out explicitly
- **Define Barriers:** Clearly explain what adoption barriers are blocking customers

---

## **Portfolio Health: [A/B/C/D/F]**
*Grade with SPECIFIC metrics: X BEMS escalations, Y defects, Z critical cases. Direct assessment: healthy/at-risk/in crisis.*

---

## **🔴 Critical Trouble Spots**

**BEMS Escalations** (Complex Engineering Issues):
• **[Customer]:** [X BEMS] - **IDs:** [List BEMS IDs] - [Problem]
• **[Customer]:** [X BEMS] - **IDs:** [List BEMS IDs] - [Problem]

**Critical Defects Affecting Portfolio:**
• **[Defect ID]:** [Description] - **Impacts:** [Customers] - **Status:** [Open/Fixed]
• **[Defect ID]:** [Description] - **Impacts:** [Customers] - **Status:** [Open/Fixed]

**High-Severity Cases (P1/P2):**
• **[Customer]:** P1/P2 - **Case:** [Number] - [Problem] - [Days open]

---

## **ALL Customers in Trouble (Complete List)**

*Include EVERY customer with problems - do NOT limit to 5:*

**1. [Customer] - Risk: [HIGH/CRITICAL]**
• **Problem:** [What's really wrong]
• **Barriers:** [X barriers] - **Key Issue:** [Specific blocking issue]
• **TAC Cases:** [Y cases, Z are P1/P2] - [Active problems]
• **BEMS:** [Count and IDs with brackets like [BEMS01916938]]
• **Defects:** [Known bugs with IDs like [CSCxx12345]]
• **Impact:** [Business effect]
• **Action:** [Immediate step needed]

**Continue for ALL remaining customers with the same detailed format**

---

## **Common Problems Across Portfolio**

**Problem #1: [Issue Name]**
• **What's Happening:** [Problem description]
• **Adoption Barriers:** [Specific barriers causing this]
• **Customers Affected:** [List 3-5 customers]
• **Related Defects:** [Bug IDs if any]
• **Business Impact:** [How this blocks adoption/causes issues]
• **Fix:** [What needs to happen]

**Problem #2-3:** [Same format]

---

## **Adoption Barriers Breakdown**

**By Theme:**
• **[Theme 1]:** [X customers] - [Description with examples]
• **[Theme 2]:** [Y customers] - [Description with examples]

**Critical Barriers:**
• **[Customer]:** [Full barrier description] - **Severity:** [Level] - **Status:** [Open/Working]
• **[Customer]:** [Full barrier description] - **Severity:** [Level] - **Status:** [Open/Working]

---

## **What We Need To Do**

**🔥 IMMEDIATE (This Week):**
1. **[Action]** - Target: [Customer/Issue] - Owner: [Name] - Why: [Critical reason]
2. **[Action]** - Target: [Customer/Issue] - Owner: [Name] - Why: [Critical reason]

**📋 SHORT-TERM (30-60 Days):**
1. **[Initiative]** - Addresses: [Problem] - Impact: [Outcome]
2. **[Initiative]** - Addresses: [Problem] - Impact: [Outcome]

---

**DATA:** {data}
"""
