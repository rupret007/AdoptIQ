import os, sys, json, re, time, math, logging, threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Iterable, Tuple
import warnings

# Prefer the OS trust store (macOS Keychain, Windows cert store, Linux system
# CAs) over certifi's bundle. This resolves the common "on VPN but still fails"
# symptom where Cisco corporate TLS inspection re-signs keeper.cisco.com with
# an internal CA that certifi does not ship. Must happen before urllib3 /
# requests / hvac / snowflake-connector create any SSLContext.
try:  # pragma: no cover - platform / version dependent
    import truststore as _truststore  # type: ignore
    _truststore.inject_into_ssl()
    os.environ.setdefault("ADOPTIQ_TRUSTSTORE_INJECTED", "1")
except Exception as _ts_err:  # noqa: BLE001 - best-effort, fall back to certifi
    logging.getLogger(__name__).debug(
        "truststore not available (%s); falling back to certifi bundle",
        _ts_err,
    )

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
from data_normalization import (
    ACCOUNT_COLUMN_CANDIDATES,
    add_case_lifecycle_fields,
    build_customer_lookup,
    detect_bems_mask,
    extract_bems_ids_from_row,
    normalize_customer_name,
    normalize_priority_label,
    normalize_severity_label,
    normalize_status_label,
    parse_datetime_series,
)
from risk_scoring import compute_customer_risk_profile
# Round 15 / Phase 1.1: route every Excel sheet through the customer-facing
# column SSoT so the fallback writer below never re-leaks SF/ETL plumbing
# (IS_DELETED, MAY_EDIT, _stale_storage, EDWSF_*, etc.).
from report_export_schema import apply_export_schema as _r15_apply_export_schema

# Round 15 / Phase 2.1: visual polish helpers -- convert the written
# data range into a true Excel Table, apply per-column number formats
# (currency / date / percent / integer), layer conditional formatting
# rules anchored to the canonical RISK_BAND_THRESHOLDS, and prepend a
# Summary KPI sheet pulled from canonical_metrics.
from report_export_styling import (
    apply_excel_polish as _r15_apply_excel_polish,
    write_summary_sheet as _r15_write_summary_sheet,
)
from report_utils import (
    format_inline_source,
    format_number as _r12_format_number,
    # Round 12 / Phase 11.1: Round 11 / Phase 11.8 added ``round_percent``
    # as the canonical percentage rounding helper but no production
    # module imported it.  Pull it in here so the legacy ``round(x, 1)``
    # call sites in this module can be migrated to a single
    # half-away-from-zero implementation (banker's rounding surprises
    # CSSMs reading "0%" for genuinely 0.5%+ risk percentages).
    round_percent as _r12_round_percent,
)
from snowflake_table_policy import TablePolicyViolation, guard_sql, guard_table, is_table_blocked
from config import Config

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

# Snowflake query metrics for query-volume baseline and optimization validation.
_SNOWFLAKE_QUERY_METRICS = {"count": 0, "samples": []}
_SNOWFLAKE_QUERY_METRICS_LOCK = threading.Lock()
_SNOWFLAKE_QUERY_SAMPLES_MAX = 50


def _r13_set_picture_alt_text(picture: Any, alt_text: str) -> None:
    """Round 13 / Phase 8.6 + 9.10: stamp accessibility alt text on a python-docx picture.

    Default ``doc.add_picture`` calls leave the embedded image without
    a docPr description, which means screen readers and Word's
    Accessibility Checker announce the chart only by filename
    ("portfolio_dashboard_2025...png").  Write the supplied
    ``alt_text`` to ``inline.docPr.descr`` (long description) and
    ``inline.docPr.title`` (short title), and mirror to ``cNvPr.descr``
    where available for legacy consumers.  Silently no-ops on any
    failure so a missing schema attribute or non-inline shape never
    crashes a report build.
    """
    if not alt_text or picture is None:
        return
    try:
        inline = getattr(picture, "_inline", None)
        if inline is None:
            return
        text = str(alt_text)
        try:
            doc_pr = inline.docPr
            doc_pr.set("descr", text)
            if not doc_pr.get("title"):
                doc_pr.set("title", text[:120])
        except Exception:
            pass
        try:
            ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}cNvPr"
            for c_nv_pr in inline.iter(ns):
                c_nv_pr.set("descr", text)
        except Exception:
            pass
    except Exception:
        pass


def _record_snowflake_query(sql: Any) -> None:
    preview = " ".join(str(sql).split())
    if len(preview) > 220:
        preview = preview[:220] + "..."
    with _SNOWFLAKE_QUERY_METRICS_LOCK:
        _SNOWFLAKE_QUERY_METRICS["count"] += 1
        samples = _SNOWFLAKE_QUERY_METRICS["samples"]
        if len(samples) < _SNOWFLAKE_QUERY_SAMPLES_MAX:
            samples.append(preview)


def reset_snowflake_query_metrics() -> None:
    with _SNOWFLAKE_QUERY_METRICS_LOCK:
        _SNOWFLAKE_QUERY_METRICS["count"] = 0
        _SNOWFLAKE_QUERY_METRICS["samples"] = []


def get_snowflake_query_metrics() -> Dict[str, Any]:
    with _SNOWFLAKE_QUERY_METRICS_LOCK:
        return {
            "count": int(_SNOWFLAKE_QUERY_METRICS["count"]),
            "samples": list(_SNOWFLAKE_QUERY_METRICS["samples"]),
        }


def _is_snowflake_access_issue(exc: Exception) -> bool:
    """Identify schema/permission failures that should downgrade to warnings."""
    msg = str(exc or "").lower()
    patterns = (
        "invalid identifier",
        "sql compilation error",
        "object does not exist",
        "does not exist or not authorized",
        "not authorized",
        "insufficient privileges",
        "permission denied",
        "access denied",
    )
    return any(p in msg for p in patterns)


def _log_snowflake_fallback(context: str, exc: Exception) -> None:
    """Log concise warning-level fallback for expected Snowflake access issues."""
    if _is_snowflake_access_issue(exc):
        logger.warning("%s skipped due to Snowflake access/schema limitations: %s", context, str(exc).strip())
    else:
        logger.error("%s failed: %s", context, exc)


def _stable_failure_kind(exc: BaseException) -> str:
    """Round 6 / Phase 7.4: map an exception to a small, stable enum.

    Previously the per-subsection failure dicts emitted
    ``failure_kind = type(e).__name__``.  That value was useful for
    server-side debugging but it leaks bundled-driver class names
    (``SnowflakeAccessIssue``, ``ProgrammingError``,
    ``InternalServerError``) directly into structured outputs that
    downstream code or dashboards may render to operators or even
    ship to the LLM as part of a briefing.  Collapse to a small,
    stable enum so the public contract is portable across driver
    upgrades and so the mapping cannot accidentally surface internal
    layout to an end user.

    Allowed return values:
      * ``"snowflake"`` - the failure originated in the Snowflake
        client / EDW path (access issue or programmatic error).
      * ``"value"``     - a generic ``ValueError`` / ``TypeError`` /
        ``KeyError`` that signals bad upstream data shape.
      * ``"unknown"``   - everything else.

    The original class name is still recoverable from the bundled
    log line; only the *serialized* enum is normalized.
    """
    try:
        if _is_snowflake_access_issue(exc):
            return "snowflake"
    except Exception:
        pass  # noqa: PIE790
    _name = (getattr(type(exc), "__name__", "") or "").lower()
    if "snowflake" in _name or "programming" in _name or "operational" in _name:
        return "snowflake"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "value"
    return "unknown"


def _utc_window_start_iso(days: int) -> str:
    """Round 8 / Phase 2.3: compute an explicit UTC window-start date.

    Snowflake's ``CURRENT_DATE()`` is evaluated in the *session* time
    zone, which means a leader report run at 02:00 UTC by an account
    whose Snowflake session sits in a non-UTC TZ silently slid the
    lookback window into the wrong day.  Round 7 / Phase 2.1 fixed
    this in ``enhanced_snowflake_insights.py`` but left equivalent
    ``DATEADD(day, -%s, CURRENT_DATE())`` patterns here.  Use this
    helper to compute ``today_utc - days`` in Python and bind the
    resulting ISO date as a parameter, so the returned data set is
    independent of Snowflake session TZ.
    """
    try:
        _days = int(days)
    except (TypeError, ValueError):
        _days = 0
    if _days < 0:
        _days = 0
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=_days)
    return cutoff.isoformat()


def _utc_today_iso() -> str:
    """Round 8 / Phase 2.3: explicit UTC ``today`` for SERVICE_END_DATE
    style filters that previously used ``CURRENT_DATE()``."""
    return datetime.now(timezone.utc).date().isoformat()


def _safe_div(num: Any, den: Any, default: float = 0.0) -> float:
    """Round 9 / Phase 2.3: NaN-safe / zero-safe ratio helper.

    Centralises the ``num / den`` guard previously open-coded at every
    ARR-share, case-rate, and pulse-ratio site in this module.  Returns
    ``default`` when either operand is ``None``, non-finite (``NaN`` /
    ``inf``), or when the denominator is zero / negative -- exactly the
    contract the new ``total_arr`` and HHI guards (Phase 2.1 / 2.2)
    rely on.  The previous open-coded form (``num / den if den > 0 else 0``)
    silently fell through on ``NaN > 0 == False`` *and* propagated
    ``NaN`` through later arithmetic, which is how the misleading 73 %
    concentration figures were ending up in the report when a single
    customer row had a missing ARR value.

    Importantly, this helper *does not* raise on bad input -- callers
    use it inside the LLM-facing insight payload where a thrown
    exception would lose the rest of the section.
    """
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return float(default)
    if not (math.isfinite(n) and math.isfinite(d)):
        return float(default)
    if d <= 0:
        return float(default)
    result = n / d
    if not math.isfinite(result):
        return float(default)
    return float(result)


def _assert_arr_attrs(
    df: Optional[pd.DataFrame],
    *,
    function_name: str = "<unknown>",
    strict: Optional[bool] = None,
) -> None:
    """Round 30 / M5: enforce the ARR-frame ``attrs`` contract.

    Every DataFrame that reaches an ARR-consuming function (multi-
    currency disclosure renderers, ``calculate_arr_impact_for_issues``,
    ``analyze_feature_requests``, the executive / compact ARR-Exposure
    sections, etc.) MUST have been routed through
    ``_normalize_arr_df`` so the following ``attrs`` keys are stamped:

    - ``is_multi_currency`` -- boolean, ``True`` when the frame mixes
      distinct non-UNKNOWN currency codes.
    - ``currencies_present`` -- sorted list of distinct currency codes
      observed in the frame.

    The contract is foundational for the multi-currency disclosure
    surfaces (executive ARR Exposure, compact ARR Exposure, leader
    title-page advisory).  A frame that bypasses ``_normalize_arr_df``
    silently degrades all three surfaces to single-currency rendering,
    which is how a portfolio with mixed currencies could end up
    rendered as a single comparable headline ARR.

    Behaviour:

    - Empty / ``None`` frames are tolerated (no-op).  The empty frame
      returned by ``_normalize_arr_df(pd.DataFrame())`` already carries
      the stamped attrs, but legacy "placeholder" empty frames built
      via ``pd.DataFrame()`` outside the normalizer are still in use
      in some test fixtures, so requiring attrs on empty frames would
      be an unnecessary back-compat break.
    - Non-empty frames missing either attr trigger a structured WARN
      log by default.  When ``strict`` is ``True`` (or
      ``ADOPTIQ_STRICT_MODE`` env var is enabled) the function raises
      ``ValueError`` so test runs and CI gates catch silent drift.
    - The check is cheap (just two ``in`` lookups on a dict) so call
      sites can guard every entry into an ARR consumer without
      performance concern.

    Phased rollout: this helper is currently warn-by-default so the
    contract can be observed without breaking any caller that has not
    yet been migrated.  Once every ARR consumer has been audited and
    the WARNING is silent for one full verification cycle, flip the
    default to ``strict=True`` (Round 31 follow-up).
    """
    if df is None:
        return
    try:
        if getattr(df, 'empty', True):
            return
    except Exception:  # noqa: BLE001
        return
    try:
        attrs = dict(getattr(df, 'attrs', {}) or {})
    except Exception:  # noqa: BLE001
        attrs = {}
    missing = [
        k for k in ('is_multi_currency', 'currencies_present')
        if k not in attrs
    ]
    if not missing:
        return
    # Resolve strictness: explicit param wins, then env var, else warn.
    if strict is None:
        try:
            _env = str(os.environ.get('ADOPTIQ_STRICT_MODE', '0')).strip().lower()
            strict = _env in ('1', 'true', 'yes', 'on')
        except Exception:  # noqa: BLE001
            strict = False
    msg = (
        "arr.attrs.missing"
    )
    extra = {
        'event': 'arr.attrs.missing',
        'function_name': function_name,
        'missing_attrs': missing,
        'columns_present': list(getattr(df, 'columns', []))[:25],
        'row_count': int(getattr(df, 'shape', (0, 0))[0]),
        'note': 'frame did not pass through _normalize_arr_df; '
                'multi-currency disclosure may be silently dropped',
    }
    if strict:
        logger.error(msg, extra=extra)
        raise ValueError(
            f"ARR frame reaching {function_name} is missing attrs "
            f"{missing}; route the frame through _normalize_arr_df "
            f"before consuming it."
        )
    logger.warning(msg, extra=extra)


# Round 30 / I1: canonical concentration "skipped" note text.  The
# multi-currency branch of ``derive_portfolio_intelligence`` stamps
# ``not_comparable_across_currencies: True`` on the concentration
# insight along with a ``note`` describing why percent / HHI / top-N
# rankings were skipped.  Renderers (executive ARR Exposure, leader
# title-page advisory, compact ARR Exposure) call
# ``concentration_note_text`` to surface that note adjacent to the
# multi-currency ARR disclosure so the reader understands why the
# usual "Top 5 by ARR" callout is missing.
CONCENTRATION_MULTICURRENCY_NOTE: str = (
    "Concentration analysis skipped: portfolio mixes currencies, so "
    "Top 5 / Top 10 percentages and the HHI index are not comparable. "
    "Use the per-currency ARR breakdown for prioritisation."
)


def concentration_note_text(concentration: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the renderer-ready concentration note text, or ``None``.

    Round 30 / I1 helper.  ``concentration`` is the dict produced by
    :func:`derive_portfolio_intelligence` under
    ``insights['concentration']``.  When the upstream portfolio mixes
    currencies, the dict carries ``not_comparable_across_currencies:
    True`` and a backend-supplied ``note``.  Renderers should surface
    that note adjacent to the multi-currency ARR disclosure rather
    than silently suppressing the concentration sub-section.

    Returns ``None`` for single-currency portfolios (or when the dict
    is missing / malformed) so callers can use the result directly in
    a truthy guard:

    .. code-block:: python

        note = concentration_note_text(concentration_insight)
        if note:
            doc.add_paragraph(note, style='Intense Quote')

    Behaviour:

    - Returns the backend-supplied ``note`` text verbatim when present
      (so a future change to the backend phrasing flows through to
      every renderer with no further code change).
    - Falls back to the module-level
      :data:`CONCENTRATION_MULTICURRENCY_NOTE` constant when
      ``not_comparable_across_currencies`` is set but the ``note`` key
      is empty or missing -- guards against an upstream regression
      that drops the note text but keeps the flag.
    - Returns ``None`` when the concentration dict is ``None`` / empty
      / missing the ``not_comparable_across_currencies`` flag, so
      single-currency portfolios render no advisory.
    """
    if not concentration or not isinstance(concentration, dict):
        return None
    if not concentration.get('not_comparable_across_currencies'):
        return None
    note = concentration.get('note')
    if isinstance(note, str) and note.strip():
        return note.strip()
    return CONCENTRATION_MULTICURRENCY_NOTE


def _safe_annotate_with_contract(
    df: 'pd.DataFrame',
    *,
    dataset: str,
    source_label: str,
) -> 'pd.DataFrame':
    """Round 34 / D -- fail-loud wrapper around
    :func:`data_contracts.annotate_with_contract`.

    Pre-Round-34: every caller wrapped ``annotate_with_contract`` in
    ``try/except Exception as _annot_err: logger.debug(...)``.  When
    annotation crashed for any reason (import failure, attribute
    issue, an unexpected raise inside the contract code), the error
    was logged at DEBUG and the dataframe carried NO
    ``fetch_error`` / ``fetch_error_kind`` annotation.  Downstream
    code therefore treated the frame as "valid" and the operator's
    only signal was a single DEBUG line they would never see in
    production.  The "thin report" symptom (executive summary
    rendering almost nothing because a critical dataframe lost its
    schema_drift signal) was the visible failure.

    Fix: WARN-level log of the failure, plus an explicit
    ``fetch_error`` stamp with kind ``contract_annotation_failure``
    so the existing partial-data banner pipeline (analyze.html
    banner + Word "⚠ Partial Data Warning" section + Excel
    Data_Unavailable rows) actually surfaces the loss.

    The wrapper is best-effort about stamping (it must NEVER raise
    from a load path), so a stamp failure also falls through to
    WARN -- but the load itself completes.
    """
    try:
        from data_contracts import annotate_with_contract as _annotate
        _annotate(df, dataset=dataset)
        return df
    except Exception as annot_err:  # noqa: BLE001 - defensive
        logger.warning(
            "Round 34 / D: annotate_with_contract(%s) failed on %s "
            "load (%s: %s); stamping fetch_error so downstream "
            "renderers surface the contract-annotation gap",
            dataset, source_label,
            type(annot_err).__name__, annot_err,
        )
        # Best-effort stamp.  ``df.attrs`` is a plain dict on
        # pandas DataFrames; only fails if df is not a DataFrame
        # (also possible -- caller may have passed None).
        try:
            if hasattr(df, 'attrs'):
                # Don't clobber an existing fetch_error -- the
                # original failure is more informative.
                if not df.attrs.get('fetch_error'):
                    df.attrs['fetch_error'] = (
                        f"contract_annotation_failure on {dataset}: "
                        f"{type(annot_err).__name__}"
                    )
                    df.attrs['fetch_error_kind'] = 'contract_annotation_failure'
                    df.attrs.setdefault('fetch_error_dataset', dataset)
        except Exception as stamp_err:  # noqa: BLE001 - never raise
            logger.warning(
                "Round 34 / D: could not stamp fetch_error after "
                "contract failure on %s: %s",
                dataset, stamp_err,
            )
        return df


def _empty_df_with_fetch_error(dataset: str, exc: Exception) -> pd.DataFrame:
    """
    Return an empty DataFrame whose ``.attrs`` carries the source dataset
    name and the failure reason. This is the canonical signal Phase 1.3a
    introduces so downstream code (snowflake_prefetch, validators, formatters)
    can distinguish "source failed" from "source returned zero rows".

    Use whenever an exception in a Snowflake / CSConsole / external fetcher
    would previously have returned ``pd.DataFrame()`` silently.
    """
    df = pd.DataFrame()
    try:
        df.attrs["fetch_error"] = str(exc).strip() or exc.__class__.__name__
        df.attrs["fetch_error_dataset"] = dataset
        df.attrs["fetch_error_kind"] = (
            "access_or_schema" if _is_snowflake_access_issue(exc) else "runtime"
        )
    except Exception:
        # ``DataFrame.attrs`` is a plain dict but be defensive in case a
        # downstream pandas patch breaks assignment; never raise from here.
        pass
    return df


class _InstrumentedSnowflakeCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, *args, **kwargs):
        try:
            guard_sql(sql)
        except TablePolicyViolation as policy_err:
            logger.warning("Snowflake query blocked by table policy: %s", policy_err)
            raise
        try:
            _record_snowflake_query(sql)
        except Exception:
            # Instrumentation must never block query execution.
            pass
        return self._cursor.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        try:
            guard_sql(sql)
        except TablePolicyViolation as policy_err:
            logger.warning("Snowflake query blocked by table policy: %s", policy_err)
            raise
        try:
            _record_snowflake_query(sql)
        except Exception:
            # Instrumentation must never block query execution.
            pass
        return self._cursor.executemany(sql, *args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._cursor, item)


class _InstrumentedSnowflakeConnection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _InstrumentedSnowflakeCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, item):
        return getattr(self._conn, item)


def _instrument_snowflake_connection(conn):
    if isinstance(conn, _InstrumentedSnowflakeConnection):
        return conn
    # Round 30 / L2: pin every Snowflake session to UTC immediately on
    # connect so server-side ``CURRENT_TIMESTAMP`` / ``SYSDATE`` and any
    # implicit ``TIMESTAMP_LTZ`` -> ``TIMESTAMP_NTZ`` coercions land in
    # UTC.  Without this, a session that inherits the warehouse default
    # (typically ``America/Los_Angeles`` for Cisco's account) returns
    # tz-naive timestamps that look like UTC but are actually wall-clock
    # local, breaking 30-day window math in the compact / executive
    # reports.  Belt-and-suspenders alongside the
    # ``_assert_datetime_columns_tz_aware`` helper we call after each
    # prefetch.
    try:
        cur = conn.cursor()
        try:
            cur.execute("ALTER SESSION SET TIMEZONE = 'UTC'")
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as _tz_err:  # noqa: BLE001
        logger.warning(
            "Round 30 / L2: failed to ALTER SESSION SET TIMEZONE='UTC' "
            "(continuing): %s",
            _tz_err,
        )
    return _InstrumentedSnowflakeConnection(conn)

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
# Round 14 / Phase 4.1: ``Config`` is already imported at the top of the
# module (line ~62 with the rest of the first-party imports), so this
# second ``from config import Config`` was a redundant redefinition that
# tripped pyflakes F811 and could mask future import-shadowing bugs.
# Drop the duplicate import; the ``Config`` symbol from the original
# import is in scope here.
KEEPER_CONFIG = Config.KEEPER_CONFIG
SNOWFLAKE_CONFIG = Config.SNOWFLAKE_CONFIG
CIRCUIT_CONFIG = Config.CIRCUIT_CONFIG
# =================================================

# Learned insights (past analyses) — optional; inject into CircuIT prompt when available
try:
    from enhanced_admin_dashboard_v2 import get_learned_insights
except Exception as _import_err:
    logging.getLogger(__name__).debug("enhanced_admin_dashboard_v2 unavailable, using stub: %s", _import_err)
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
    """Default team configuration (fallback when team_config.json is
    missing or unreadable).

    Round 80: rewritten to byte-for-byte parity with the post-R80
    team_config.json. Pre-R80 the fallback drifted (referenced a
    non-existent ``Josh Horowitz`` manager, placed ``Asad Sarfaraz``
    under Dee Kindrick instead of the correct manager, and was missing
    the entire ``Shams`` block); per ``.cursor/rules/adoptiq.mdc``
    Tier 3 the fallback MUST stay aligned with the JSON SSoT so an
    operator who somehow loses ``team_config.json`` sees the canonical
    roster, not a 2024-era ghost. Pinned by
    ``tests/test_round80_team_roster_includes_new_managers.py::
    test_default_fallback_matches_json_roster_byte_for_byte``.
    """
    # Round 80
    team_roster = [
        # Dee Kindrick (12 reports, alphabetical by name)
        ("Dee Kindrick", "Anthony Ortiz", "antortiz@cisco.com"),
        ("Dee Kindrick", "Brice Mercer", "brimerce@cisco.com"),
        ("Dee Kindrick", "Cesar Ozuna", "ceozuna@cisco.com"),
        ("Dee Kindrick", "Chris Clark", "christc3@cisco.com"),
        ("Dee Kindrick", "Christine Simrell", "chrsimre@cisco.com"),
        ("Dee Kindrick", "Eli Walsh", "elwalsh@cisco.com"),
        ("Dee Kindrick", "Michael Ramsey", "michrams@cisco.com"),
        ("Dee Kindrick", "Michael Thompson", "mithomp2@cisco.com"),
        ("Dee Kindrick", "Nate Hardy", "nahardy@cisco.com"),
        ("Dee Kindrick", "Prabhakar Dakinedi", "pdakined@cisco.com"),
        ("Dee Kindrick", "Stephen Williams", "stepwil3@cisco.com"),
        ("Dee Kindrick", "Xavier Pena", "xpena@cisco.com"),
        # Brian Frazier (9 reports, post-R80 -- Angelica + Samuel
        # Tamayo moved to Mithun Sakthivel Subramanian)
        ("Brian Frazier", "Arpit Patel", "arpitpat@cisco.com"),
        ("Brian Frazier", "Brandon Doan", "brdoan@cisco.com"),
        ("Brian Frazier", "Greg Dolberry", "gdolberr@cisco.com"),
        ("Brian Frazier", "Haydee Hernandez Ceja", "hayherna@cisco.com"),
        ("Brian Frazier", "Jeffrey Story", "jestory@cisco.com"),
        ("Brian Frazier", "Jose Nerio Chavarri Espinosa", "josencha@cisco.com"),
        ("Brian Frazier", "Mario Pena", "marpena2@cisco.com"),
        ("Brian Frazier", "Nitish Sinha", "nitsinh2@cisco.com"),
        ("Brian Frazier", "William Phillips", "willphil@cisco.com"),
        # Paresh Jadhav (8 reports, NEW R80)
        ("Paresh Jadhav", "Avinash Vinu", "avinu@cisco.com"),
        ("Paresh Jadhav", "Gagandeep Kaur Walia", "gagwalia@cisco.com"),
        ("Paresh Jadhav", "Ian Gagnon", "igagnon@cisco.com"),
        ("Paresh Jadhav", "Kohei Kobayashi", "kkitawak@cisco.com"),
        ("Paresh Jadhav", "Samuel Sugandaran", "ssuganda@cisco.com"),
        ("Paresh Jadhav", "Sei Tonomi", "stonomi@cisco.com"),
        ("Paresh Jadhav", "Seitaro Shinagawa", "sshinaga@cisco.com"),
        ("Paresh Jadhav", "Timothy Brown", "timothbr@cisco.com"),
        # Mithun Sakthivel Subramanian (8 reports, NEW R80)
        ("Mithun Sakthivel Subramanian", "Angelica Hernandez Becerra", "angelihe@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Asad Sarfaraz", "asarfara@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Balaji Kandasamy Shanmugam", "balakand@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Mariyam Hachikyan", "mhachiky@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Prashant Yadav", "prashyad@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Ramon Gonzalez Reyes", "ramongo@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Samuel Tamayo", "samtamay@cisco.com"),
        ("Mithun Sakthivel Subramanian", "Shagul Hameed", "shaghame@cisco.com"),
        # Shams (5 reports)
        ("Shams", "Daniel O'Flaherty", "doflaher@cisco.com"),
        ("Shams", "Hector Gonzalez", "hectgon2@cisco.com"),
        ("Shams", "Ron Estillore", "restillo@cisco.com"),
        ("Shams", "Tim Tyler", "tityler@cisco.com"),
        ("Shams", "Ujjwal Aneja", "uaneja@cisco.com"),
    ]
    managers = [
        "Dee Kindrick",
        "Brian Frazier",
        "Paresh Jadhav",
        "Mithun Sakthivel Subramanian",
        "Shams",
        "All Managers",
    ]
    return team_roster, managers

# Load team configuration
TEAM_ROSTER, MANAGERS = _load_team_config()

TECH_CHOICES = [
    "Webex Meetings & Messaging",
    "Webex Calling",
    "Webex Contact Center",
    "Cisco UCCE",
    "Cisco UCCX",
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


# Round 32 / Phase 1.C: synonym map for ``All <X>`` wildcard fallbacks
# in CSConsole technology filtering.  Keep terms broad (substrings,
# not anchored regex) — the strict per-bucket ``TECH_FILTERS`` already
# caught the canonical patterns; this map is the safety net for text
# that says "Contact Center" or "Cloud Contact Center" without the
# Webex / Cisco prefix that the strict patterns demand.
_ALL_BUCKET_FUZZY_TERMS: Dict[str, Tuple[str, ...]] = {
    "all contact center": (
        "contact center", "wxcc", "ucce", "uccx", "call center",
        "ccx", "ccp", "webex cc",
    ),
    "all webex calling": (
        "webex calling", "wxc ", "calling cloud", "cloud calling",
    ),
    "all webex meetings & messaging": (
        "webex meetings", "webex messaging", "messaging cloud",
        "meetings cloud",
    ),
}


def _filter_tech_text_fuzzy_all_bucket(tech_field: Any, sub_tech_field: Any, tech: str) -> bool:
    """Permissive substring match used as a last-resort fallback for
    ``All <X>`` filters in :func:`_filter_tech_text_enhanced`.  Returns
    True when *any* synonym term for the bucket appears as a substring
    of either field.  Logs at INFO when a match would have been
    dropped by the strict matcher so the next thin-report incident
    has a clear breadcrumb.
    """
    bucket = (tech or "").strip().lower()
    terms = _ALL_BUCKET_FUZZY_TERMS.get(bucket)
    if not terms:
        # Generic ``All <X>`` fallback when the bucket isn't pre-registered:
        # require the suffix word(s) to appear as a substring.
        if not bucket.startswith("all "):
            return False
        suffix = bucket[len("all "):].strip()
        if not suffix or len(suffix) < 4:
            return False
        terms = (suffix,)
    haystack = " ".join(
        s.lower() for s in (str(tech_field or ""), str(sub_tech_field or ""))
    )
    if not haystack.strip():
        return False
    for term in terms:
        if term and term in haystack:
            logger.info(
                "[[FILTER]] Round 32 / Phase 1.C: fuzzy %s fallback matched on "
                "term=%r tech_field=%r sub_tech_field=%r",
                tech, term, str(tech_field)[:80], str(sub_tech_field)[:80],
            )
            return True
    return False


def _filter_tech_text_enhanced(tech_field: str, sub_tech_field: str, tech: str) -> bool:
    """
    Enhanced technology filtering that prioritizes Sub Technology over Tech field
    to ensure accurate categorization (e.g., UCCX showing as 'Contact Center Software'
    in Tech field but 'UCCX' in Sub Technology field)

    Round 32 / Phase 1.C: any ``All <X>`` bucket (e.g. ``All Contact
    Center``) now also falls back to a permissive substring match on
    the suffix when none of the strict regex patterns hit.  Build6
    silently dropped 2 of 2 CSConsole action plans for a real
    Contact-Center customer because the row's tech text was
    well-formed but didn't match any of the 30+ explicit regexes;
    see ~/.adoptiq/adoptiq.46198.log line 230.
    """
    if tech == "All":
        return True

    # Special handling for "All Contact Center" - match any contact center technology
    if tech == "All Contact Center":
        contact_center_techs = ["Webex Contact Center", "Webex Contact Center Enterprise", "Cisco UCCE", "Cisco UCCX"]
        for contact_tech in contact_center_techs:
            if _filter_tech_text_enhanced(tech_field, sub_tech_field, contact_tech):
                return True
        # Round 32 / Phase 1.C: fuzzy fallback for "All Contact Center"
        # so legitimately-tagged but non-canonical text (e.g. plain
        # "Contact Center" or "Cloud Contact Center" without the
        # "Webex" prefix) still survives the filter.
        if _filter_tech_text_fuzzy_all_bucket(tech_field, sub_tech_field, tech):
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
    for pattern, mapped_value in getattr(Config, "SUB_TECHNOLOGY_MAPPINGS", {}).items():
        if re.search(pattern, t):
            return mapped_value
    # Check in a specific order to avoid mis-categorization.
    for tech_name in [
        "Webex Contact Center Enterprise",
        "Webex Contact Center",
        "Cisco UCCE",
        "Cisco UCCX",
        "Webex Calling",
        "Webex Meetings & Messaging",
    ]:
        for pat in TECH_FILTERS[tech_name]:
            if re.search(pat, t):
                return tech_name
    if "contact center" in t or "wxcc" in t:
        return "All Contact Center"
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
        # Round 2 / Phase 4.4: stamp tac_cases and bems_rows row
        # contracts on the CSOne load so consistency / contract drift
        # detectors fire here just as they do on the Snowflake
        # prefetch path.  Without this, an upstream column rename in
        # the Excel template would silently zero out TAC/BEMS counts
        # downstream.
        # Round 34 / D: route through fail-loud helper so any
        # contract-annotation crash stamps fetch_error on the df
        # instead of silently dropping the schema_drift signal.
        _safe_annotate_with_contract(df, dataset="tac_cases", source_label="csone_excel")
        _safe_annotate_with_contract(df, dataset="bems_rows", source_label="csone_excel")
        return df
    except Exception as e:
        logger.error(f"Failed to load CSOne Excel file: {e}")
        empty = pd.DataFrame()
        try:
            empty.attrs["fetch_error"] = str(e).strip() or e.__class__.__name__
            empty.attrs["fetch_error_dataset"] = "csone_excel"
            empty.attrs["fetch_error_kind"] = "load_failure"
        except Exception:
            pass
        return empty

def newest_csone(folder: Path) -> Optional[Path]:
    cands = sorted(Path(folder).glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None

def load_db_profile() -> Optional[dict]:
    """Loads an optional database profile JSON file for richer context.

    Round 6 / Phase 4.18: previously this resolved ``database_profile.json``
    relative to the *current working directory*, which silently lost the
    file the moment the user launched the frozen Mac app from outside the
    bundle (e.g. by double-clicking the .app while their shell cwd was
    ``$HOME``).  Resolve relative to this module's location first (which
    is inside the PyInstaller bundle for the frozen app), then fall back
    to the cwd for legacy dev usage.
    """
    candidates: List[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parent / "database_profile.json")
    except Exception:
        pass
    # Frozen executables expose the bundle directory via sys._MEIPASS.
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        try:
            candidates.append(Path(_meipass) / "database_profile.json")
        except Exception:
            pass
    candidates.append(Path("database_profile.json"))

    for profile_path in candidates:
        try:
            if profile_path.exists():
                logger.info("Found %s, loading for enhanced context...", profile_path)
                with open(profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning("Failed to load DB profile %s: %s", profile_path, exc)
    return None

# --------------------------- Snowflake ---------------------------
DSM_TABLE = "CX_DB.CX_SWSSBST_BR.dsm_assignment_data"
AB_TABLE  = "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW"

# Round 82 / Build 58: account-attribution correctness via primary +
# secondary DSM email column UNION.  Pre-R82 ``get_subscriptions_for_team``
# walked a hardcoded 4-column tuple (``PRIMARY_DSM_EMAIL`` ->
# ``CSSM_EMAIL`` -> ``ASSIGNEE_EMAIL`` -> ``OWNER_EMAIL``) and stopped at
# the FIRST match.  When a roster email lives only in a "secondary"
# column (Brian's team had this symptom -- specific accounts missing
# from the per-CSSM slice because the email lived in ``SECONDARY_DSM_EMAIL``
# / ``BACKUP_DSM_EMAIL`` / similar), those subscriptions never entered
# ``team_subs_df`` and downstream ``CSSM_EMAIL == cssm_email`` slicing
# in ``leader_report_generator`` returned empty per-CSSM frames.
#
# The R82 fix splits the contract:
#   * ``_R82_PRIMARY_DSM_EMAIL_COLUMNS`` -- trusted DSM-owner columns;
#     first hit wins for the primary slice (preserves pre-R82 ordering
#     so the "I expect to see Alice's primary subscriptions" contract
#     never regresses).
#   * ``_R82_SECONDARY_DSM_EMAIL_CANDIDATES`` -- conservative naming
#     patterns for secondary owner columns.  Each candidate that is
#     PRESENT in the DSM table (and not already in the primary set)
#     generates an additional parameterised ``SELECT ... WHERE
#     <secondary_col> IN (...)`` query.  The merged result is
#     deduplicated on ``(SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME,
#     CSSM_EMAIL)`` so the same row never double-counts even if
#     primary + secondary both hold the same email.
#
# The candidate list is intentionally CONSERVATIVE (specific naming
# patterns, not "any column ending in _EMAIL") so a stray column like
# ``CASE_COMMENT_EMAIL`` cannot silently widen the WHERE clause.  When
# Brian's environment surfaces a column we did not anticipate, the new
# ``introspect_dsm_columns()`` helper + ``/api/diag/dsm-columns`` admin
# endpoint enumerate the FULL column list so an operator can name the
# real secondary column for a follow-on commit.
_R82_PRIMARY_DSM_EMAIL_COLUMNS: Tuple[str, ...] = (
    "PRIMARY_DSM_EMAIL",
    "CSSM_EMAIL",
    "ASSIGNEE_EMAIL",
    "OWNER_EMAIL",
)
_R82_SECONDARY_DSM_EMAIL_CANDIDATES: Tuple[str, ...] = (
    "SECONDARY_DSM_EMAIL",
    "SECONDARY_CSSM_EMAIL",
    "BACKUP_DSM_EMAIL",
    "BACKUP_CSSM_EMAIL",
    "DELEGATE_DSM_EMAIL",
    "DELEGATE_CSSM_EMAIL",
    "OWNER_EMAIL_2",
    "OWNER_EMAIL_BACKUP",
    "CSSM_EMAIL_2",
    "DSM_EMAIL_2",
)

# Cache stores (columns, fetched_at_monotonic). TTL bounds staleness so that
# schema additions (e.g. a new owner-email column) are picked up within an hour
# without requiring a process restart.
# Round 8 / Phase 2.11: cap the schema cache with an LRU policy so
# that pathological callers (e.g. a code path that builds dynamic
# fully-qualified table names from user input or from a wide
# catalog scan) cannot grow this cache without bound and leak
# process memory.  ``OrderedDict`` gives us cheap LRU eviction by
# popping from the front when we exceed the cap.
# Round 14 / Phase 2.3: previously this only imported ``OrderedDict`` under
# the alias ``_OrderedDictForSchemaCache``, so the quoted annotations on the
# next two ``OrderedDict[...]`` declarations referenced an undefined name
# when ``typing.get_type_hints`` walked the module.  Bind the unaliased
# symbol too so static analysis (ruff F821) and runtime introspection
# both resolve cleanly.  The aliased name is preserved for backwards
# compatibility with any caller that imported it.
from collections import OrderedDict  # noqa: E402
_OrderedDictForSchemaCache = OrderedDict

_TABLE_COLUMN_CACHE: "OrderedDict[str, Tuple[set[str], float]]" = _OrderedDictForSchemaCache()
_TABLE_COLUMN_CACHE_LOCK = threading.Lock()
_TABLE_COLUMN_CACHE_TTL_SECONDS = int(os.environ.get("ADOPTIQ_TABLE_COLUMN_CACHE_TTL", "3600"))
_TABLE_COLUMN_CACHE_MAX_ENTRIES = max(
    16, int(os.environ.get("ADOPTIQ_TABLE_COLUMN_CACHE_MAX", "256"))
)
# Phase 4.2: a separate (much shorter) TTL for *failed* introspections.
# A 1-hour cache of an empty column set silently strips owner-aware
# filters from every query in that hour. Re-try quickly so a transient
# Snowflake hiccup does not poison the next 60 minutes of queries.
_TABLE_COLUMN_CACHE_FAIL_TTL_SECONDS = int(
    os.environ.get("ADOPTIQ_TABLE_COLUMN_CACHE_FAIL_TTL", "60")
)
# Module-level registry of recent introspection failures so other
# layers (prefetch, leader report) can surface them as
# partial_data_warnings instead of treating an empty column set as
# "this column simply does not exist".
# Round 13 / Phase 11.3: previously this was a plain ``Dict`` and
# the only way a key was ever evicted was via
# ``get_recent_column_introspection_failures`` running the
# expiry sweep (caller-driven).  A long-running process that
# never called the report builders accumulated unbounded
# entries -- one per (transient) failure -- and a runaway
# Snowflake outage that flapped through hundreds of distinct
# tables would inflate this dict indefinitely.  Use an
# ``OrderedDict`` with an explicit LRU cap; insert/touch on
# ``move_to_end`` and evict from the front when the cap is
# exceeded.  The cap is generous (256 by default, env override)
# so legitimate failure registries are never thrown out
# in normal operation.
_TABLE_COLUMN_INTROSPECTION_FAILURES: "OrderedDict[str, Tuple[str, float]]" = (
    _OrderedDictForSchemaCache()
)
_TABLE_COLUMN_INTROSPECTION_FAILURES_MAX_ENTRIES = max(
    16,
    int(
        os.environ.get(
            "ADOPTIQ_TABLE_COLUMN_INTROSPECTION_FAILURES_MAX",
            str(_TABLE_COLUMN_CACHE_MAX_ENTRIES),
        )
    ),
)


def get_recent_column_introspection_failures(max_age_seconds: int = 3600) -> Dict[str, str]:
    """Return ``{table: error_message}`` for introspection failures that
    happened within ``max_age_seconds``.  Used by report assemblers to
    annotate ``partial_data_warnings``.
    """
    now = time.monotonic()
    out: Dict[str, str] = {}
    with _TABLE_COLUMN_CACHE_LOCK:
        for table, (msg, ts) in list(_TABLE_COLUMN_INTROSPECTION_FAILURES.items()):
            if now - ts <= max_age_seconds:
                out[table] = msg
            else:
                _TABLE_COLUMN_INTROSPECTION_FAILURES.pop(table, None)
    return out


def _normalize_table_name(table_name: str) -> str:
    return str(table_name or "").replace('"', "").strip().upper()


def invalidate_table_column_cache(table_name: Optional[str] = None) -> None:
    """Drop cached schema info for one table (or all tables when ``None``)."""
    with _TABLE_COLUMN_CACHE_LOCK:
        if table_name is None:
            _TABLE_COLUMN_CACHE.clear()
            return
        _TABLE_COLUMN_CACHE.pop(_normalize_table_name(table_name), None)


def _get_table_columns(ctx, table_name: str) -> set[str]:
    """
    Get table columns using a lightweight preview query.
    This avoids exception-driven probing for optional columns.

    The result is cached per table with a TTL (``ADOPTIQ_TABLE_COLUMN_CACHE_TTL``
    seconds, default 3600) so long-running processes pick up schema additions
    without needing a restart.
    """
    if ctx is None:
        return set()
    cache_key = _normalize_table_name(table_name)
    # Round 52 / partial-data-warning fix #2: short-circuit BEFORE we cache,
    # probe, OR register an introspection failure when the table is on the
    # snowflake_table_policy block-list. A blocked table is a deliberate
    # policy decision, not an operational outage, so it MUST NOT surface as
    # a partial-data warning ("column_introspection_failure" with
    # ``schema:EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C``). Returning an empty
    # column set keeps every caller's "is column X available?" check honest --
    # they will simply see no columns and degrade gracefully -- without ever
    # touching the cursor or recording a failure for the user banner.
    try:
        if is_table_blocked(table_name):
            return set()
    except Exception:
        pass
    now = time.monotonic()
    with _TABLE_COLUMN_CACHE_LOCK:
        entry = _TABLE_COLUMN_CACHE.get(cache_key)
        if entry is not None:
            cached_cols, cached_at = entry
            # Round 8 / Phase 2.11: touch the entry so it stays MRU.
            try:
                _TABLE_COLUMN_CACHE.move_to_end(cache_key)
            except Exception:
                pass
            # Phase 4.2: use the short failure TTL when the cached
            # entry is the sentinel empty set, so a Snowflake hiccup
            # does not poison the next hour of queries with stripped
            # owner-aware filters.
            ttl = (
                _TABLE_COLUMN_CACHE_FAIL_TTL_SECONDS
                if not cached_cols
                else _TABLE_COLUMN_CACHE_TTL_SECONDS
            )
            if now - cached_at < ttl:
                return set(cached_cols)
            # Expired — fall through and refresh.

    def _attempt() -> Tuple[bool, set, str]:
        cur_local = None
        try:
            guard_table(table_name)
            cur_local = ctx.cursor()
            # Round 13 / Phase 7.6: this ``LIMIT 1`` is a *schema probe*
            # whose only goal is to read ``cur.description`` (column
            # names + types) for the introspection cache.  We
            # intentionally do NOT add an ``ORDER BY`` here: an
            # ``ORDER BY <some_col>`` would require us to know a
            # column exists ahead of time (the very thing we are
            # introspecting), and ``ORDER BY 1`` would force a sort
            # on every column type including unsortable ones (VARIANT
            # / ARRAY).  We discard ``rows`` entirely; only the
            # cursor description is consumed.  The non-determinism
            # of which row Snowflake returns is therefore harmless
            # because we never read any row payload from this query.
            cur_local.execute(f"SELECT * FROM {table_name} LIMIT 1")
            cols_local: set = set()
            for meta in (cur_local.description or []):
                if not meta:
                    continue
                col_name = str(meta[0] or "").strip().upper()
                if col_name:
                    cols_local.add(col_name)
            return True, cols_local, ""
        except Exception as exc:  # noqa: BLE001
            return False, set(), str(exc)
        finally:
            if cur_local is not None:
                try:
                    cur_local.close()
                except Exception:
                    pass

    ok, cols, err_msg = _attempt()
    if not ok:
        # Phase 4.2: retry once before caching the empty sentinel so a
        # transient connection error doesn't strip owner-aware filters
        # for ``_TABLE_COLUMN_CACHE_FAIL_TTL_SECONDS`` either.
        logger.warning(
            "Column introspection for %s failed: %s — retrying once.",
            table_name,
            err_msg,
        )
        ok, cols, err_msg = _attempt()

    if ok:
        with _TABLE_COLUMN_CACHE_LOCK:
            _TABLE_COLUMN_CACHE[cache_key] = (set(cols), time.monotonic())
            try:
                _TABLE_COLUMN_CACHE.move_to_end(cache_key)
                # Round 8 / Phase 2.11: enforce LRU cap.
                while len(_TABLE_COLUMN_CACHE) > _TABLE_COLUMN_CACHE_MAX_ENTRIES:
                    _TABLE_COLUMN_CACHE.popitem(last=False)
            except Exception:
                pass
            _TABLE_COLUMN_INTROSPECTION_FAILURES.pop(cache_key, None)
        return cols

    logger.warning(
        "Could not introspect columns for %s after retry: %s. "
        "Returning empty column set; downstream queries will be more "
        "permissive (Phase 4.2 surfaces this in partial_data_warnings).",
        table_name,
        err_msg,
    )
    with _TABLE_COLUMN_CACHE_LOCK:
        _TABLE_COLUMN_CACHE[cache_key] = (set(), time.monotonic())
        try:
            _TABLE_COLUMN_CACHE.move_to_end(cache_key)
            # Round 8 / Phase 2.11: enforce LRU cap on the failure
            # sentinel path as well.
            while len(_TABLE_COLUMN_CACHE) > _TABLE_COLUMN_CACHE_MAX_ENTRIES:
                _TABLE_COLUMN_CACHE.popitem(last=False)
        except Exception:
            pass
        _TABLE_COLUMN_INTROSPECTION_FAILURES[cache_key] = (err_msg, time.monotonic())
        # Round 13 / Phase 11.3: enforce LRU cap on the failure
        # registry so a long-running outage that flaps through
        # many distinct tables cannot grow this dict without bound.
        try:
            _TABLE_COLUMN_INTROSPECTION_FAILURES.move_to_end(cache_key)
            while (
                len(_TABLE_COLUMN_INTROSPECTION_FAILURES)
                > _TABLE_COLUMN_INTROSPECTION_FAILURES_MAX_ENTRIES
            ):
                _TABLE_COLUMN_INTROSPECTION_FAILURES.popitem(last=False)
        except Exception:
            pass
    return set()


def _column_or_default_expr(available_columns: set[str], column_name: str, default_sql: Optional[str], alias: Optional[str] = None) -> Optional[str]:
    col = str(column_name or "").strip().upper()
    if not col:
        return None
    out_alias = (alias or col).strip().upper()
    if col in available_columns:
        return f"{col} AS {out_alias}" if out_alias != col else col
    if default_sql is None:
        return None
    return f"{default_sql} AS {out_alias}"


# Columns likely to identify the creator/owner of a task (Action Plan / Adoption Barrier)
# row in EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW. We match the first that exists in the
# physical table schema to keep queries resilient to Snowflake view changes.
TASK_OWNER_EMAIL_COLUMNS: Tuple[str, ...] = (
    "OWNER_EMAIL",
    "OWNEREMAIL",
    "CREATEDBYEMAIL",
    "CREATEDBY_EMAIL",
    "LASTMODIFIEDBYEMAIL",
    "LAST_MODIFIED_BY_EMAIL",
    "ASSIGNEE_EMAIL",
    "ASSIGNEE_C",
    "PLAN_OWNER_C",
    "PLAN_OWNER_NAME_FORMULA_C",
    "PLAN_OWNER_NAME_C",
    "OWNER_NAME",
    "OWNER",
)

# Same intent for the Customer Pulse table (ESA_C360_CUSTOMER_PULSE__C).
PULSE_OWNER_EMAIL_COLUMNS: Tuple[str, ...] = (
    "OWNER_EMAIL",
    "OWNEREMAIL",
    "CREATEDBYEMAIL",
    "CREATEDBY_EMAIL",
    "LASTMODIFIEDBYEMAIL",
    "LAST_MODIFIED_BY_EMAIL",
    "ASSIGNEE_EMAIL",
    "OWNER_NAME",
    "OWNER",
)


def _normalize_owner_emails(owner_emails: Optional[Iterable[Any]]) -> List[str]:
    """Normalize a collection of owner emails to a de-duplicated lowercase list.

    Input is treated as untrusted; we strip whitespace, lowercase, and discard any
    value that does not look like an email address (must contain '@').
    """
    if not owner_emails:
        return []
    seen: set = set()
    cleaned: List[str] = []
    for raw in owner_emails:
        if raw is None:
            continue
        try:
            txt = str(raw).strip().lower()
        except Exception:
            continue
        if not txt or "@" not in txt:
            continue
        if txt in seen:
            continue
        seen.add(txt)
        cleaned.append(txt)
    return cleaned


def _build_owner_match_clause(
    available_columns: set[str],
    owner_emails: List[str],
    candidate_columns: Iterable[str],
    table_alias: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    """Build a SQL fragment matching rows where any candidate email column matches
    any of the provided owner_emails (case-insensitive, trimmed).

    Returns (sql_fragment_or_empty_string, params).

    The fragment intentionally does not include a leading AND/OR; callers decide
    how to combine it with other predicates. If no matching columns are present
    in the table schema, returns ("", []) so callers can safely skip.
    """
    if not owner_emails:
        return "", []
    available_upper = {str(c or "").strip().upper() for c in (available_columns or set())}
    alias_prefix = f"{table_alias}." if table_alias else ""
    col_exprs: List[str] = []
    for col in candidate_columns:
        col_u = str(col or "").strip().upper()
        if col_u and col_u in available_upper:
            col_exprs.append(f"LOWER(TRIM({alias_prefix}{col_u}))")
    if not col_exprs:
        return "", []

    # Round 6 / Phase 4.9: chunk the email list so the resulting
    # IN(...) clause does not blow past Snowflake's per-statement
    # parameter limits (~16k binds). For small lists this is identical
    # to the previous single-IN behaviour.
    try:
        _chunk_size = int(os.environ.get("ADOPTIQ_OWNER_EMAIL_IN_CHUNK_SIZE", "500"))
    except Exception:
        _chunk_size = 500
    if _chunk_size <= 0:
        _chunk_size = 500
    emails = list(owner_emails)
    chunks = [emails[i:i + _chunk_size] for i in range(0, len(emails), _chunk_size)]

    fragments: List[str] = []
    params: List[Any] = []
    for chunk in chunks:
        placeholders = ",".join(["%s"] * len(chunk))
        ors = [f"{expr} IN ({placeholders})" for expr in col_exprs]
        fragments.append("(" + " OR ".join(ors) + ")")
        for _ in col_exprs:
            params.extend(chunk)
    fragment = "(" + " OR ".join(fragments) + ")"
    return fragment, params

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
        return _instrument_snowflake_connection(conn)
    except FutureTimeoutError:
        raise RuntimeError("Snowflake connection timed out after 30 seconds")
    except Exception as e:
        # Round 8 / Phase 2.1: previously the ``RuntimeError`` message embedded
        # the verbatim driver exception text (account hostnames, library
        # tracebacks, file paths) and bubbled all the way to HTTP error
        # bodies / client logs.  Strip the driver text from the user-facing
        # message and chain the original via ``from e`` so it is still
        # available to logging.error(..., exc_info=True) for support.
        logger.error("Error connecting to Snowflake", exc_info=True)
        raise RuntimeError("Failed to connect to Snowflake") from e


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
            # Round 8 / Phase 2.1: same redaction as ``_connect_snowflake_direct``;
            # do not propagate driver detail in the public ``RuntimeError`` text.
            logger.error("Error connecting to Snowflake (Keeper path)", exc_info=True)
            raise RuntimeError("Failed to connect to Snowflake") from e

    try:
        logger.info("Starting Snowflake connection with Keeper (30-second timeout)...")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(connect_to_snowflake)
            connection = future.result(timeout=30)
            logger.info("Snowflake connection successful")
            return _instrument_snowflake_connection(connection)
    except FutureTimeoutError:
        logger.warning("Snowflake connection timed out after 30 seconds")
        raise RuntimeError("Snowflake connection timed out - database may be unavailable")
    except Exception as e:
        # Round 8 / Phase 2.1: do not include verbatim ``e`` in the
        # publicly-rendered ``RuntimeError``; chain via ``from e`` so logs
        # keep the driver detail.
        logger.error("Error connecting to Snowflake (Keeper outer)", exc_info=True)
        raise RuntimeError("Failed to connect to Snowflake") from e

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
        # Round 5 / Phase 6.2: subscription IDs are customer-attributable
        # in the operator log stream; demote to DEBUG.
        logger.debug(f"[[SEARCH]] Fetching subscription data for: {subscription_id}")

        # Connect to Snowflake
        ctx = _connect_with_keeper()
        if ctx is None:
            raise RuntimeError("Unable to establish Snowflake connection")
        cur = ctx.cursor(snowflake.connector.DictCursor)

        dsm_columns = _get_table_columns(ctx, DSM_TABLE)

        # First, get account information from subscription (schema-aware column selection)
        logger.debug(f"[[LIST]] Looking up account information for subscription: {subscription_id}")
        # Round 3 / Phase 3.2: also pull RENEWAL_RISK_CATEGORY so the
        # downstream contract-risk scorer in compute_customer_risk_profile
        # actually receives the DSM signal. Previously this column was
        # never selected, so ``customer_subs[RENEWAL_RISK_CATEGORY]``
        # was always blank and the contract risk component scored 0
        # for every subscription regardless of DSM's own renewal
        # category (CRITICAL/HIGH/MEDIUM/LOW).
        account_select_exprs = [
            _column_or_default_expr(dsm_columns, "ACCOUNT_ID_C", "NULL"),
            _column_or_default_expr(dsm_columns, "BU_NAME", "'Unknown Customer'"),
            _column_or_default_expr(dsm_columns, "SUBSCRIPTION_ID", "NULL"),
            _column_or_default_expr(dsm_columns, "TECHNOLOGY_C", "'Unknown'"),
            _column_or_default_expr(dsm_columns, "SUB_TECHNOLOGY_C", "'Unknown'"),
            _column_or_default_expr(dsm_columns, "STATUS_C", "'Unknown'"),
            _column_or_default_expr(dsm_columns, "RENEWAL_RISK_CATEGORY", "NULL"),
        ]
        # Round 3 / Phase 3.1: ``LIMIT 1`` without a deterministic
        # ``ORDER BY`` returns whichever DSM row Snowflake decides to
        # surface first. With multiple rows for the same SUBSCRIPTION_ID
        # (line items, status history, etc.) different runs can land
        # on different account_id / customer_name pairs and produce
        # subtly different reports. Fetch up to 5 rows, pick the
        # newest-modified, and warn on duplicates so the lookup is
        # deterministic and visible.
        account_query = f"""
        SELECT {", ".join(e for e in account_select_exprs if e)},
               {_column_or_default_expr(dsm_columns, "MODIFIED_DATE", "NULL")} AS _ROW_MODIFIED_DATE,
               {_column_or_default_expr(dsm_columns, "CREATED_DATE", "NULL")} AS _ROW_CREATED_DATE
        FROM {DSM_TABLE}
        WHERE SUBSCRIPTION_ID = %s
        ORDER BY MODIFIED_DATE DESC NULLS LAST,
                 CREATED_DATE DESC NULLS LAST,
                 ACCOUNT_ID_C
        LIMIT 5
        """

        cur.execute(account_query, (subscription_id,))
        _account_rows = cur.fetchall() or []
        if len(_account_rows) > 1:
            try:
                _distinct_accts = {
                    str((r or {}).get("ACCOUNT_ID_C") or "")
                    for r in _account_rows
                }
                _distinct_accts.discard("")
                if len(_distinct_accts) > 1:
                    logger.warning(
                        "Subscription %s maps to %d distinct ACCOUNT_ID_C values in %s "
                        "(%s); taking the newest-modified row to keep this lookup "
                        "deterministic. Investigate DSM data quality.",
                        subscription_id,
                        len(_distinct_accts),
                        DSM_TABLE,
                        sorted(_distinct_accts),
                    )
                else:
                    logger.info(
                        "Subscription %s has %d DSM rows but they share a single account; "
                        "using newest-modified row.",
                        subscription_id,
                        len(_account_rows),
                    )
            except Exception as _dup_err:
                logger.debug(
                    "Could not summarize duplicate DSM rows for %s: %s",
                    subscription_id,
                    _dup_err,
                )
        account_result = _account_rows[0] if _account_rows else None

        if not account_result:
            # Round 8 / Phase 2.12: redact the raw Subscription ID
            # in WARNING-level logs (which routinely flow into
            # shared aggregators).  We log a short SHA-256 digest
            # so support can still correlate without leaking the
            # raw identifier in plain text.  The full ID remains
            # available at DEBUG.
            try:
                import hashlib as _h
                _sub_digest = _h.sha256(str(subscription_id).encode('utf-8', 'replace')).hexdigest()[:12]
            except Exception:
                _sub_digest = '?'
            logger.warning("No account found for Subscription ID (digest=%s)", _sub_digest)
            logger.debug("No account found for Subscription ID: %s", subscription_id)
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
        # Round 3 / Phase 3.2: capture DSM's renewal_risk_category so
        # downstream contract scoring can read it.
        renewal_risk_category = account_result.get('RENEWAL_RISK_CATEGORY')

        # Round 5 / Phase 6.2: customer name + account ID is PII (or
        # at least customer-attributable internal data) and should
        # not appear at INFO in the steady-state log stream.  Keep
        # the operator-friendly "found" signal at INFO without
        # identifiers and stash the identifying detail at DEBUG.
        logger.info("[[OK]] Subscription account resolved")
        logger.debug(
            "[[OK]] Found account: %s (ID: %s) | Technology: %s | Sub-Technology: %s | Status: %s",
            customer_name, account_id, technology, sub_technology, status,
        )

        # Set up date filters with parameterized queries.
        # Round 8 / Phase 2.3: bind an explicit Python-computed UTC
        # window-start date instead of ``DATEADD(day, -%s, CURRENT_DATE())``
        # so the lookback window is independent of Snowflake session TZ.
        date_filter_task = "AND DATE(CREATED_DATE) >= %s"
        date_filter_pulse_priority = "AND DATE(CREATEDDATE) >= %s"
        _window_start = _utc_window_start_iso(days)

        # Fetch all related data
        logger.info(f"[[CHART]] Fetching adoption barriers...")
        ab_query = f"""
        SELECT *, 'Adoption Barrier' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
        WHERE record_type_id = '0122T000000GJfTQAW'
        AND ACCOUNT_ID_C = %s
        {date_filter_task}
        """
        cur.execute(ab_query, (account_id, _window_start))
        adoption_barriers = cur.fetchall()

        logger.info(f"[[LIST]] Fetching action plans...")
        ap_query = f"""
        SELECT *, 'Action Plan' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
        WHERE record_type_id = '0122T000000QHBGQA4'
        AND ACCOUNT_ID_C = %s
        {date_filter_task}
        """
        cur.execute(ap_query, (account_id, _window_start))
        action_plans = cur.fetchall()

        logger.info(f"[EMOJI] Fetching customer pulse...")
        cp_query = f"""
        SELECT *, 'Customer Pulse' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C
        WHERE ACCOUNT__C = %s
        {date_filter_pulse_priority}
        """
        cur.execute(cp_query, (account_id, _window_start))
        customer_pulse = cur.fetchall()

        success_priorities = []
        if is_table_blocked("EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"):
            logger.info("[[BULLSEYE]] Success priorities query skipped by Snowflake table policy.")
        else:
            logger.info(f"[[BULLSEYE]] Fetching success priorities...")
            sp_query = f"""
            SELECT *, 'Success Priority' as RECORD_SOURCE
            FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C
            WHERE RELATED_CUSTOMER__C = %s
            {date_filter_pulse_priority}
            """
            cur.execute(sp_query, (customer_name, _window_start))
            success_priorities = cur.fetchall()

        # Get team information using schema-aware select expressions.
        logger.info(f"[EMOJI] Fetching team information...")
        team_select_exprs = [
            _column_or_default_expr(dsm_columns, "CSSM_EMAIL", "NULL"),
            _column_or_default_expr(dsm_columns, "CSSM_NAME", "NULL"),
            _column_or_default_expr(dsm_columns, "CSSM_MANAGER", "NULL"),
            _column_or_default_expr(dsm_columns, "CSSM_MANAGER_EMAIL", "NULL"),
        ]
        # Round 11 / Phase 7.4: ``team_data[0]`` was previously
        # picked from an unordered result set; if a subscription
        # has multiple historical owner rows the first one
        # depended on Snowflake's internal storage order.  Order
        # by CSSM_EMAIL plus most-recent MODIFIED_DATE so the
        # primary owner picked here is reproducible across runs.
        team_query = f"""
        SELECT {", ".join(e for e in team_select_exprs if e)}
        FROM {DSM_TABLE}
        WHERE SUBSCRIPTION_ID = %s
        ORDER BY CSSM_EMAIL ASC NULLS LAST,
                 {_column_or_default_expr(dsm_columns, "MODIFIED_DATE", "NULL")} DESC NULLS LAST
        """
        cur.execute(team_query, (subscription_id,))
        team_data = cur.fetchall()

        # Compile results (include cssm_email for single-customer renewal manager fallback)
        import canonical_metrics as _r531_sub_cm
        _ab_record_count = _r531_sub_cm.count_total_barriers(adoption_barriers)
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
            'total_records': _ab_record_count + len(action_plans) + len(customer_pulse) + len(success_priorities),
            # Round 3 / Phase 3.2: surface DSM's renewal_risk_category
            # so the renewal_risk endpoint can feed it into
            # compute_customer_risk_profile via customer_subs.
            'renewal_risk_category': renewal_risk_category,
            'summary': {
                'adoption_barriers_count': _ab_record_count,
                'action_plans_count': len(action_plans),
                'customer_pulse_count': len(customer_pulse),
                'success_priorities_count': len(success_priorities),
                'team_members_count': len(team_data),
                'renewal_risk_category': renewal_risk_category,
            }
        }

        logger.info(f"[[OK]] Subscription data fetch complete:")
        logger.info(f"   [[DATA]] Total records: {subscription_data['total_records']}")
        logger.info(f"   [EMOJI] Adoption barrier records: {_ab_record_count}")
        logger.info(f"   [[LIST]] Action plans: {len(action_plans)}")
        logger.info(f"   [EMOJI] Customer pulse: {len(customer_pulse)}")
        logger.info(f"   [[BULLSEYE]] Success priorities: {len(success_priorities)}")
        logger.info(f"   [EMOJI] Team members: {len(team_data)}")

        return subscription_data

    except Exception as e:
        # Round 5 / Phase 4.3: include a structured ``failure_kind`` /
        # ``error_code`` so the UI and validators can distinguish a
        # connection failure from a query failure from a not-found
        # result.  The vague "An error occurred" string is fine for
        # display, but downstream code (and ops dashboards) need the
        # exception type and the underlying error code (when the
        # driver provides one) to triage without grepping logs.
        # Round 6 / Phase 7.4: serialize ``failure_kind`` as a stable
        # enum so the JSON response we hand back to the UI cannot leak
        # internal driver class names (``ProgrammingError`` etc.) to
        # the customer-facing path.  The verbose class name is still
        # logged below for ops triage.
        _failure_kind = _stable_failure_kind(e)
        _failure_kind_internal = type(e).__name__
        _error_code = None
        for _attr in ('errno', 'sqlcode', 'errno', 'code'):
            try:
                _v = getattr(e, _attr, None)
                if _v not in (None, ''):
                    _error_code = str(_v)
                    break
            except Exception:
                continue
        logger.error(
            f"[[ERROR]] Error fetching subscription data: {e} "
            f"(failure_kind={_failure_kind} failure_kind_internal={_failure_kind_internal} error_code={_error_code})"
        )
        return {
            'subscription_id': subscription_id,
            'customer_name': 'Error',
            'account_id': None,
            'found': False,
            'error': 'An error occurred while fetching subscription data. Please try again.',
            'failure_kind': _failure_kind,
            'error_code': _error_code,
        }
    finally:
        if 'cur' in locals() and cur is not None:
            try:
                cur.close()
            except Exception as e:
                logger.debug(f"Error closing cursor: {e}")
        if ctx is not None:
            try:
                ctx.close()
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")


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
        # Round 5 / Phase 6.2: customer name is PII-adjacent; demote to DEBUG.
        # Round 12 / Phase 11.6: even at DEBUG, raw customer names
        # routinely flow into log aggregators that long-outlive the
        # process and are read by audiences with broader access than
        # the analyst running the search.  Log a SHA-256 digest of
        # the customer name (matching the redaction pattern Round 8
        # / Phase 2.12 established for Subscription IDs) so support
        # can still correlate without leaking the raw identifier.
        try:
            import hashlib as _r12_h
            _r12_cust_digest = _r12_h.sha256(
                str(customer_name or '').encode('utf-8', 'replace')
            ).hexdigest()[:12]
        except Exception:
            _r12_cust_digest = '?'
        logger.debug(
            "[[SEARCH]] Searching subscriptions for customer (digest=%s)",
            _r12_cust_digest,
        )

        ctx = _connect_with_keeper()
        cur = ctx.cursor(snowflake.connector.DictCursor)
        try:
            # Round 12 / Phase 4.1: Snowflake LIMIT is enforced
            # server-side, but this function only returned the raw
            # row list with no signal that the result set was capped.
            # Callers therefore could not distinguish "complete set"
            # from "truncated to N rows" -- the typeahead silently
            # showed a partial answer.  We keep the function
            # signature stable (still returns ``List[Dict]``) and
            # rely on the caller to detect truncation via
            # ``len(results) >= limit``; the matching JSON layer in
            # ``app_simple.search_subscriptions`` now exposes
            # ``results_truncated`` / ``may_have_more`` / ``limit``
            # so the UI can render a "showing N of many" hint.
            search_query = """
            SELECT SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME
            FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data
            WHERE UPPER(BU_NAME) LIKE UPPER(%s)
            ORDER BY BU_NAME, ACCOUNT_ID_C, SUBSCRIPTION_ID
            LIMIT %s
            """
            # Round 8 / Phase 2.4: pre-normalize the LIKE needle through
            # ``normalize_customer_name`` (NFKC + whitespace collapse +
            # internal-suffix stripping) so the search matches the same
            # canonical form used by ``data_normalization`` merge logic.
            # Previously a name with a stray U+00A0 (non-breaking space)
            # or full-width characters would hit Snowflake unchanged and
            # silently miss its row, while the post-fetch merge would
            # have matched it.
            try:
                _needle = normalize_customer_name(customer_name) if customer_name else ''
            except Exception:
                _needle = (customer_name or '')
            if not _needle or _needle == 'Unknown':
                _needle = (customer_name or '').strip()
            cur.execute(search_query, (f'%{_needle}%', limit))
            results = cur.fetchall()

            logger.info(f"[[OK]] Found {len(results)} subscriptions for customer search")
            # Round 12 / Phase 11.6: redact via SHA-256 digest as above.
            logger.debug(
                "[[OK]] Subscription search customer (digest=%s)",
                _r12_cust_digest,
            )
            return results
        finally:
            cur.close()

    except Exception as e:
        # Round 5 / Phase 4.13: previously this returned ``[]`` on any
        # exception, which is identical to "no subscriptions matched
        # the customer name".  The UI then showed "No matches" for
        # what was actually a Snowflake outage / auth failure /
        # malformed query, hiding a real problem from the user.
        # Return a single-element sentinel list whose record carries
        # ``_search_error`` so the caller can branch:
        #   results = search_subscriptions_by_customer(...)
        #   if results and isinstance(results[0], dict) and results[0].get('_search_error'):
        #       # surface upstream failure
        # The first element is intentionally NOT a real subscription
        # row, so any code that iterates and accesses
        # ``SUBSCRIPTION_ID`` will get ``None`` (and existing
        # ``len(results)`` checks still see "1 result" -> the UI
        # should show the error string instead of a fake match).
        logger.error(f"[[ERROR]] Error searching subscriptions: {e}")
        return [{
            '_search_error': 'An error occurred while searching subscriptions. Please try again.',
            # Round 6 / Phase 7.4: stable enum, not raw class name.
            '_failure_kind': _stable_failure_kind(e),
            'SUBSCRIPTION_ID': None,
            'ACCOUNT_ID_C': None,
            'BU_NAME': None,
        }]
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")


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
        logger.debug(f"[[BULLSEYE]] Calculating renewal risk for subscription: {subscription_id}")

        # Get subscription data
        sub_data = fetch_subscription_data(subscription_id, days)

        if not sub_data['found']:
            # Round 3 / Phase 5.3: return ``null`` and a state field
            # instead of ``risk_score=0`` when we genuinely don't
            # know the score. Callers / dashboards were treating
            # the literal ``0`` as a real "no risk" reading and
            # silently bucketing missing subscriptions as healthy.
            return {
                'subscription_id': subscription_id,
                'error': sub_data.get('error', 'Subscription not found'),
                'risk_score': None,
                'risk_level': 'UNKNOWN',
                'state': 'unavailable',
            }

        # Convert to DataFrames for analysis
        ab_df = pd.DataFrame(sub_data['adoption_barriers']) if sub_data['adoption_barriers'] else pd.DataFrame()
        ap_df = pd.DataFrame(sub_data['action_plans']) if sub_data['action_plans'] else pd.DataFrame()
        cp_df = pd.DataFrame(sub_data['customer_pulse']) if sub_data['customer_pulse'] else pd.DataFrame()

        profile = compute_customer_risk_profile(
            customer_name=sub_data.get("customer_name", subscription_id),
            customer_ab=ab_df,
            customer_csone=pd.DataFrame(),
            customer_pulse=cp_df,
            customer_action_plans=ap_df,
            customer_subs=pd.DataFrame([{
                "RENEWAL_RISK_CATEGORY": sub_data.get("summary", {}).get("renewal_risk_category", ""),
                "STATUS_C": sub_data.get("status", ""),
            }]),
            ext_incidents=None,
            # Round 3 / Phase 4.2: thread the subscription's analysis
            # window so the support-case "recent" component lines up
            # with the period being scored. Default 30 retained
            # for back-compat when ``days`` is not provided.
            recent_window_days=int(days) if days else 30,
        )
        risk_components = profile["components"]
        overall_risk = profile["risk_score_0_10"]
        risk_level = profile["risk_band"]

        # Round 8 / Phase 2.7: drive the recommendation buckets off
        # the canonical risk band (``risk_band`` field, populated by
        # ``risk_scoring._risk_band`` against
        # ``RISK_BAND_THRESHOLDS``) instead of arbitrary >=7/>=4
        # cutoffs on the 0-10 score.  Previously a customer scored
        # 6.9 (canonically HIGH) would silently fall into the
        # "MEDIUM/check-ins" bucket while the headline label said
        # HIGH.  Aligning here removes that contradiction and makes
        # band re-tuning a single-source change in
        # ``risk_scoring.RISK_BAND_THRESHOLDS``.
        recommendations = []
        _band_norm = (risk_level or 'UNKNOWN').upper().strip()
        if _band_norm in ('CRITICAL', 'HIGH'):
            recommendations.extend([
                "Schedule immediate executive-level customer meeting",
                "Assign dedicated Customer Success Manager",
                "Create emergency adoption plan with weekly reviews"
            ])
        elif _band_norm == 'MEDIUM':
            recommendations.extend([
                "Increase touch frequency to bi-weekly check-ins",
                "Provide targeted training and enablement resources",
                "Address open adoption barriers within 60 days"
            ])
        elif _band_norm in ('LOW', 'HEALTHY'):
            recommendations.extend([
                "Continue current engagement model",
                "Schedule quarterly business review"
            ])
        else:
            recommendations.extend([
                "Risk band is UNKNOWN; investigate scoring inputs before recommending action."
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
            'risk_score_0_100': profile["risk_score_0_100"],
            'risk_components': risk_components,
            'recommendations': recommendations,
            'summary': sub_data['summary'],
            # Round 8 / Phase 2.8: emit ``analysis_date`` as a
            # timezone-aware UTC ISO-8601 string instead of a naive
            # local timestamp.  Naive timestamps were previously
            # being concatenated into Snowflake-side ISO comparisons
            # and audit logs, where a missing TZ would either be
            # interpreted as UTC or as the Snowflake session TZ
            # depending on context, drifting +/- the wall-clock
            # offset.
            'analysis_date': datetime.now(timezone.utc).isoformat()
        }

        logger.info(f"[[OK]] Renewal risk analysis complete: {risk_level} risk ({overall_risk:.1f}/10)")
        logger.debug(f"[[OK]] Renewal risk customer: {sub_data['customer_name']}")

        return renewal_analysis

    except Exception as e:
        # Round 5 / Phase 4.1: align the exception path with the
        # ``not found`` path -- return ``risk_score=None`` /
        # ``risk_level='UNKNOWN'`` and an explicit
        # ``state='unavailable'`` instead of ``risk_score=0``.
        # Returning a literal zero risk on a fetch / scoring failure
        # was indistinguishable from "this subscription is in great
        # health" and silently bucketed errored subscriptions into
        # the safe band, masking the real failure mode.
        logger.error(f"[[ERROR]] Error calculating subscription renewal risk: {e}")
        return {
            'subscription_id': subscription_id,
            'error': 'An error occurred while calculating renewal risk. Please try again.',
            'risk_score': None,
            'risk_level': 'UNKNOWN',
            'state': 'unavailable',
            # Round 6 / Phase 7.4: stable enum, not raw class name.
            'failure_kind': _stable_failure_kind(e),
        }


def introspect_dsm_columns(ctx) -> Dict[str, Any]:
    """Round 82 / Phase A1: enumerate columns in the DSM assignment table.

    Returns a structured payload showing the full column inventory plus
    which primary / secondary email columns are present.  Designed to
    drive the ``/api/diag/dsm-columns`` admin endpoint so an operator
    can identify the exact secondary-CSSM column name in their
    Snowflake environment WITHOUT opening a separate Snowflake session.

    Never raises -- returns a structured ``error_kind`` field on
    failure so the endpoint can surface "Snowflake unreachable" vs
    "table missing" cleanly.

    Contract::

        {
            "table": "CX_DB.CX_SWSSBST_BR.dsm_assignment_data",
            "columns": [...sorted list of column names...],
            "primary_email_columns_present": [...subset of _R82_PRIMARY_DSM_EMAIL_COLUMNS...],
            "secondary_email_candidates_present": [...subset of _R82_SECONDARY_DSM_EMAIL_CANDIDATES...],
            "all_email_like_columns": [...sorted *_EMAIL columns...],
            "introspected_at": "<UTC ISO-8601>",
            "ok": True | False,
            "error_kind": "<stable enum>" | None,
        }

    The ``all_email_like_columns`` slot is the operator escape hatch:
    if the secondary column lives outside our conservative
    ``_R82_SECONDARY_DSM_EMAIL_CANDIDATES`` list, the operator can see
    it surface here and submit a follow-on PR adding it to the
    candidates tuple.
    """
    payload: Dict[str, Any] = {
        "table": DSM_TABLE,
        "columns": [],
        "primary_email_columns_present": [],
        "secondary_email_candidates_present": [],
        "all_email_like_columns": [],
        "introspected_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "error_kind": None,
    }
    if ctx is None:
        payload["error_kind"] = "snowflake_context_unavailable"
        return payload
    try:
        available_columns = _get_table_columns(ctx, DSM_TABLE)
        if not available_columns:
            payload["error_kind"] = "dsm_table_introspection_empty"
            return payload
        sorted_cols = sorted(str(c).upper() for c in available_columns)
        payload["columns"] = sorted_cols
        payload["primary_email_columns_present"] = [
            col for col in _R82_PRIMARY_DSM_EMAIL_COLUMNS if col in available_columns
        ]
        payload["secondary_email_candidates_present"] = [
            col for col in _R82_SECONDARY_DSM_EMAIL_CANDIDATES if col in available_columns
        ]
        payload["all_email_like_columns"] = [
            c for c in sorted_cols if c.endswith("_EMAIL") or c.endswith("_EMAIL_2") or c.endswith("_EMAIL_BACKUP")
        ]
        payload["ok"] = True
        return payload
    except Exception as err:
        payload["error_kind"] = _stable_failure_kind(err)
        logger.warning(
            "Round 82 / introspect_dsm_columns failed: %s",
            type(err).__name__,
            exc_info=False,
        )
        return payload


def get_subscriptions_for_team(ctx, emails: List[str]) -> pd.DataFrame:
    """Gets all subscriptions and associated accounts for a list of CSSM emails with proper resource management.

    Round 82 / Phase A: UNIONs across the primary + any present
    secondary DSM email columns so a roster email that lives only in
    a secondary column (e.g. ``SECONDARY_DSM_EMAIL`` /
    ``BACKUP_DSM_EMAIL``) still pulls the matching subscriptions into
    ``team_subs_df``.  Pre-R82 the function picked the FIRST primary
    column that existed and stopped, silently dropping every row whose
    only matching email was in a secondary column.  See the SSoT
    comment near ``_R82_PRIMARY_DSM_EMAIL_COLUMNS`` for the contract.

    The returned DataFrame's ``attrs`` dict carries
    ``_r82_team_subs_diag`` -- a structured rollup that callers (the
    leader path's status writer in particular) can persist into
    ``analysis_status['team_subs_diag']`` so an operator inspecting
    a run can see how many additional rows came from secondary columns
    vs primary, and which secondary column names actually matched.
    """
    if ctx is None:
        return pd.DataFrame()
    if not emails:
        return pd.DataFrame()

    cur = None
    try:
        cur = ctx.cursor()
        available_columns = _get_table_columns(ctx, DSM_TABLE)

        # Round 82 / Phase A: identify primary + secondary email columns.
        # Primary preserves pre-R82 first-hit ordering exactly so the
        # "expected primary subscription" contract can never regress.
        primary_email_col: Optional[str] = next(
            (col for col in _R82_PRIMARY_DSM_EMAIL_COLUMNS if col in available_columns),
            None,
        )
        secondary_email_cols: List[str] = [
            col for col in _R82_SECONDARY_DSM_EMAIL_CANDIDATES
            if col in available_columns and col != primary_email_col
        ]

        if not primary_email_col and not secondary_email_cols:
            logger.warning(
                "Round 82 / get_subscriptions_for_team: no primary OR secondary "
                "CSSM/owner email columns found in %s; returning empty team "
                "subscription set",
                DSM_TABLE,
            )
            empty = pd.DataFrame(columns=["SUBSCRIPTION_ID", "ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"])
            try:
                empty.attrs["_r82_team_subs_diag"] = {
                    "primary_email_column_used": None,
                    "secondary_email_columns_used": [],
                    "primary_rows": 0,
                    "secondary_rows": 0,
                    "merged_rows": 0,
                    "duplicate_rows_dropped": 0,
                    "introspected_at": datetime.now(timezone.utc).isoformat(),
                    "error_kind": "no_email_columns_found",
                }
            except Exception:
                pass
            return empty

        # Build the canonical SELECT body once -- the trailing
        # ``CSSM_EMAIL`` alias swaps per-query so each row carries the
        # email value from the column that matched it.
        base_select_exprs = [
            _column_or_default_expr(available_columns, "SUBSCRIPTION_ID", "NULL"),
            _column_or_default_expr(available_columns, "ACCOUNT_ID_C", "NULL"),
            _column_or_default_expr(available_columns, "BU_NAME", "''"),
        ]
        base_select_clause = ", ".join(e for e in base_select_exprs if e)

        EMAIL_CHUNK_SIZE = 500
        deduped_emails: List[str] = []
        _seen_emails: set = set()
        for _e in emails:
            _ek = str(_e).strip().lower()
            if not _ek or _ek in _seen_emails:
                continue
            _seen_emails.add(_ek)
            deduped_emails.append(_e)

        # Round 82 / Phase A: per-source-column collection so the diag
        # can attribute rows back to which physical column matched.
        # ``_query_one_email_col`` runs the chunked IN-clause query for
        # a single email column and returns the row list + column
        # descriptor.  We invoke it once per (primary + secondaries)
        # column and merge client-side, deduping after the union.
        def _query_one_email_col(email_col: str) -> Tuple[List[Any], Optional[List[Any]]]:
            local_rows: List[Any] = []
            local_descr: Optional[List[Any]] = None
            select_clause = f"{base_select_clause}, {email_col} AS CSSM_EMAIL"
            for i in range(0, len(deduped_emails), EMAIL_CHUNK_SIZE):
                chunk = deduped_emails[i:i + EMAIL_CHUNK_SIZE]
                placeholders = ','.join(['%s'] * len(chunk))
                sql = (
                    f"SELECT DISTINCT {select_clause} "
                    f"FROM {DSM_TABLE} "
                    f"WHERE {email_col} IN ({placeholders})"
                )
                cur.execute(sql, chunk)
                chunk_rows = cur.fetchall()
                if local_descr is None:
                    local_descr = cur.description
                local_rows.extend(chunk_rows)
            return local_rows, local_descr

        all_rows: List[Any] = []
        col_descr: Optional[List[Any]] = None
        primary_row_count = 0
        secondary_rows_per_col: Dict[str, int] = {}

        if primary_email_col:
            p_rows, p_descr = _query_one_email_col(primary_email_col)
            primary_row_count = len(p_rows)
            if col_descr is None:
                col_descr = p_descr
            all_rows.extend(p_rows)

        for sec_col in secondary_email_cols:
            s_rows, s_descr = _query_one_email_col(sec_col)
            secondary_rows_per_col[sec_col] = len(s_rows)
            if col_descr is None:
                col_descr = s_descr
            all_rows.extend(s_rows)

        if col_descr is None:
            empty = pd.DataFrame(columns=["SUBSCRIPTION_ID", "ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"])
            try:
                empty.attrs["_r82_team_subs_diag"] = {
                    "primary_email_column_used": primary_email_col,
                    "secondary_email_columns_used": secondary_email_cols,
                    "primary_rows": 0,
                    "secondary_rows": 0,
                    "merged_rows": 0,
                    "duplicate_rows_dropped": 0,
                    "introspected_at": datetime.now(timezone.utc).isoformat(),
                    "error_kind": "no_rows_returned_from_any_column",
                }
            except Exception:
                pass
            return empty

        df = pd.DataFrame(all_rows, columns=[c[0] for c in col_descr])
        merged_rows_pre_dedup = len(df)
        if not df.empty:
            try:
                df = df.drop_duplicates(
                    subset=[c for c in ("SUBSCRIPTION_ID", "ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL") if c in df.columns],
                    keep="first",
                ).reset_index(drop=True)
            except Exception:
                df = df.drop_duplicates().reset_index(drop=True)
        merged_rows_post_dedup = len(df)
        for required_col in ("SUBSCRIPTION_ID", "ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"):
            if required_col not in df.columns:
                df[required_col] = ""

        # Round 82 / Phase A4: stamp the diag rollup on ``df.attrs`` so
        # leader / comprehensive / compact callers can persist it onto
        # ``analysis_status['team_subs_diag']``.  Structured log emitted
        # at INFO once per call so the same data is visible in the
        # rotating file log without touching analysis_status.
        secondary_total = sum(secondary_rows_per_col.values())
        diag = {
            "primary_email_column_used": primary_email_col,
            "secondary_email_columns_used": secondary_email_cols,
            "primary_rows": primary_row_count,
            "secondary_rows": secondary_total,
            "secondary_rows_per_col": secondary_rows_per_col,
            "merged_rows": merged_rows_post_dedup,
            "duplicate_rows_dropped": max(0, merged_rows_pre_dedup - merged_rows_post_dedup),
            "introspected_at": datetime.now(timezone.utc).isoformat(),
            "error_kind": None,
        }
        try:
            df.attrs["_r82_team_subs_diag"] = diag
        except Exception:
            pass
        if secondary_email_cols:
            logger.info(
                "Round 82 / get_subscriptions_for_team: primary_col=%s primary_rows=%d "
                "secondary_cols=%s secondary_rows=%d merged_rows=%d duplicates_dropped=%d",
                primary_email_col,
                primary_row_count,
                secondary_email_cols,
                secondary_total,
                merged_rows_post_dedup,
                diag["duplicate_rows_dropped"],
            )
        # Round 2 / Phase 4.4: stamp the row contract so downstream
        # callers (consistency validator, contracts dashboard) can
        # detect schema drift on this load path.  Previously only the
        # snowflake_prefetch path called annotate_with_contract, so
        # direct fetch_team_subscriptions consumers had no contract.
        # Round 34 / D: fail-loud wrapper.
        # Round 52 / partial-data-warning fix #1: use the new
        # ``team_subscriptions`` contract (no ARR slot) instead of the
        # full ``subscriptions`` contract. This roster query intentionally
        # does NOT SELECT ARR -- ARR is loaded by ``fetch_arr_data`` which
        # keeps the strict ``subscriptions`` contract -- so annotating with
        # the full contract was producing a permanent ``schema_drift``
        # warning ("missing slot(s) arr") on every renewal run despite the
        # data being correct.
        _safe_annotate_with_contract(df, dataset="team_subscriptions", source_label="team_subscriptions")
        return df
    except Exception as e:
        _log_snowflake_fallback("Team subscriptions query", e)
        return _empty_df_with_fetch_error("team_subscriptions", e)
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
        """Round 30 / M5: canonical ARR-frame normalizer + attrs stamper.

        Contract: every ARR-bearing DataFrame that reaches a downstream
        consumer (renderers, impact calculators, sentiment analyzers)
        MUST be routed through this function.  In addition to filling
        in default columns and coercing numeric types, this function
        stamps the following ``attrs`` keys (read by the multi-currency
        disclosure surfaces):

        - ``is_multi_currency`` -- ``True`` when the frame contains more
          than one distinct non-UNKNOWN ``CURRENCY_CODE``.
        - ``currencies_present`` -- sorted list of distinct currency
          codes observed in the frame (including ``UNKNOWN`` if any
          row had a missing / blank currency).

        Empty frames returned by this function still carry the stamped
        attrs (both default to ``False`` / ``[]``).  The Round 30 / M5
        helper ``_assert_arr_attrs`` checks the contract at every
        ARR-consuming entry point so a frame that bypasses this
        normalizer raises (strict mode) or warns (default) instead of
        silently degrading the multi-currency disclosure.
        """
        if df is None or df.empty:
            empty = pd.DataFrame(columns=[
                'ACCOUNT_ID_C', 'BU_NAME', 'SUBSCRIPTION_ID', 'TECHNOLOGY_C', 'SUB_TECHNOLOGY_C',
                'STATUS_C', 'CSSM_EMAIL', 'CSSM_NAME', 'CSSM_MANAGER',
                'ANNUAL_CONTRACT_VALUE', 'MRR', 'TCV', 'LICENSE_COUNT',
                'CURRENCY_CODE'
            ])
            # Round 30 / M5: stamp the empty frame with the attrs
            # contract so callers reading attrs on a placeholder don't
            # see a missing key (which would also bypass the
            # ``_assert_arr_attrs`` warning even though the frame is
            # technically conformant).
            empty.attrs['is_multi_currency'] = False
            empty.attrs['currencies_present'] = []
            return empty
        normalized = df.copy()
        text_defaults = {
            'ACCOUNT_ID_C': '', 'BU_NAME': '', 'SUBSCRIPTION_ID': '',
            'TECHNOLOGY_C': 'Unknown', 'SUB_TECHNOLOGY_C': 'Unknown',
            'STATUS_C': '', 'CSSM_EMAIL': '', 'CSSM_NAME': '', 'CSSM_MANAGER': '',
            # Round 2 / Phase 1.1: every row carries its CURRENCY_CODE so
            # downstream sums can bucket by currency rather than blindly
            # adding values across mixed currencies.  When the source
            # column is missing we fall back to 'UNKNOWN' (NOT 'USD'),
            # so callers can opt to either drop the row from totals or
            # surface a multi-currency warning.
            'CURRENCY_CODE': 'UNKNOWN',
        }
        numeric_defaults = {
            'ANNUAL_CONTRACT_VALUE': 0, 'MRR': 0, 'TCV': 0, 'LICENSE_COUNT': 0
        }
        for col, default in text_defaults.items():
            if col not in normalized.columns:
                normalized[col] = default
        # Normalize currency code casing so downstream groupby is stable.
        try:
            normalized['CURRENCY_CODE'] = (
                normalized['CURRENCY_CODE'].astype(str).str.strip().str.upper().replace('', 'UNKNOWN')
            )
        except Exception:
            pass
        for col, default in numeric_defaults.items():
            if col not in normalized.columns:
                normalized[col] = default
            normalized[col] = pd.to_numeric(normalized[col], errors='coerce').fillna(default)

        # Stamp a multi-currency warning on the frame attrs so any
        # renderer that sums across rows can detect the situation
        # without re-discovering it.
        # Round 28: explicit column guard + structured WARNING on miss
        # so the multi-currency disclosure is not silently dropped when
        # an upstream schema drift removes CURRENCY_CODE from the
        # frame.  Previously the bare ``try / except Exception`` block
        # masked KeyError on missing columns and the report rendered
        # as if the portfolio were single-currency USD.
        if 'CURRENCY_CODE' in normalized.columns:
            try:
                ccys = sorted(c for c in normalized['CURRENCY_CODE'].dropna().unique() if c)
                distinct_ccys = [c for c in ccys if c and c != 'UNKNOWN']
                normalized.attrs['currencies_present'] = ccys
                normalized.attrs['is_multi_currency'] = len(distinct_ccys) > 1
            except Exception as _ccy_err:
                logger.warning(
                    "currency.code.scan_failed",
                    extra={
                        'event': 'currency.code.scan_failed',
                        'error_class': type(_ccy_err).__name__,
                        'columns_present': list(normalized.columns)[:25],
                    },
                )
                normalized.attrs['currencies_present'] = []
                normalized.attrs['is_multi_currency'] = False
        else:
            logger.warning(
                "currency.code.missing",
                extra={
                    'event': 'currency.code.missing',
                    'columns_present': list(normalized.columns)[:25],
                    'note': 'is_multi_currency defaulted to False; '
                            'multi-currency disclosure suppressed for '
                            'this normalized frame',
                },
            )
            normalized.attrs['currencies_present'] = []
            normalized.attrs['is_multi_currency'] = False
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

        available_columns = _get_table_columns(ctx, DSM_TABLE)
        if "ACCOUNT_ID_C" not in available_columns:
            logger.warning("ACCOUNT_ID_C column unavailable in %s; cannot fetch ARR data", DSM_TABLE)
            return _normalize_arr_df(pd.DataFrame())

        # Round 10 / Phase 5.3: case-fold + trim STATUS_C so rows whose
        # provider stamps the column as ``Active``, ``active``, or
        # ``ACTIVE `` (trailing space, common on CSV-derived ETLs) all
        # match. Previously the literal-equal predicate silently dropped
        # those rows, undercounting ARR vs the source system.
        status_filter = "AND UPPER(TRIM(STATUS_C)) = 'ACTIVE'" if "STATUS_C" in available_columns else ""
        if not status_filter:
            logger.info("STATUS_C column unavailable in %s; skipping active-status filter", DSM_TABLE)

        # Round 10 / Phase 9.3: track which monetary columns were
        # missing from the introspected schema so we can stamp an
        # ``arr_schema_degraded`` warning on the returned DataFrame's
        # ``attrs``.  Without this, a Snowflake column rename or a
        # downstream view that drops ``ANNUAL_CONTRACT_VALUE_C``
        # produces a frame full of ``0`` ARR values with no
        # ``fetch_error`` -- the report would silently advertise a
        # "$0 portfolio" headline even though the data is simply
        # missing the field.
        _missing_monetary_columns: list[str] = []
        if "ANNUAL_CONTRACT_VALUE_C" not in available_columns:
            _missing_monetary_columns.append("ANNUAL_CONTRACT_VALUE_C")
        if "MONTHLY_RECURRING_REVENUE_C" not in available_columns:
            _missing_monetary_columns.append("MONTHLY_RECURRING_REVENUE_C")
        if "TOTAL_CONTRACT_VALUE_C" not in available_columns:
            _missing_monetary_columns.append("TOTAL_CONTRACT_VALUE_C")
        if "LICENSE_COUNT_C" not in available_columns:
            _missing_monetary_columns.append("LICENSE_COUNT_C")

        select_exprs = [
            _column_or_default_expr(available_columns, "ACCOUNT_ID_C", "NULL"),
            _column_or_default_expr(available_columns, "BU_NAME", "''"),
            _column_or_default_expr(available_columns, "SUBSCRIPTION_ID", "''"),
            _column_or_default_expr(available_columns, "TECHNOLOGY_C", "'Unknown'"),
            _column_or_default_expr(available_columns, "SUB_TECHNOLOGY_C", "'Unknown'"),
            _column_or_default_expr(available_columns, "STATUS_C", "''"),
            _column_or_default_expr(available_columns, "CSSM_EMAIL", "''"),
            _column_or_default_expr(available_columns, "CSSM_NAME", "''"),
            _column_or_default_expr(available_columns, "CSSM_MANAGER", "''"),
            (
                "COALESCE(ANNUAL_CONTRACT_VALUE_C, 0) AS ANNUAL_CONTRACT_VALUE"
                if "ANNUAL_CONTRACT_VALUE_C" in available_columns
                else "0 AS ANNUAL_CONTRACT_VALUE"
            ),
            (
                "COALESCE(MONTHLY_RECURRING_REVENUE_C, 0) AS MRR"
                if "MONTHLY_RECURRING_REVENUE_C" in available_columns
                else "0 AS MRR"
            ),
            (
                "COALESCE(TOTAL_CONTRACT_VALUE_C, 0) AS TCV"
                if "TOTAL_CONTRACT_VALUE_C" in available_columns
                else "0 AS TCV"
            ),
            (
                "COALESCE(LICENSE_COUNT_C, 0) AS LICENSE_COUNT"
                if "LICENSE_COUNT_C" in available_columns
                else "0 AS LICENSE_COUNT"
            ),
            # Round 2 / Phase 1.1: surface the per-row currency so
            # ARR/MRR/TCV totals can be bucketed by currency rather
            # than summed blindly across mixed currencies.  Try a few
            # known column names; default to 'UNKNOWN' if none is
            # present so the renderer can mark the metric as untrusted.
            (
                "UPPER(COALESCE(CURRENCY_CODE_C, '')) AS CURRENCY_CODE"
                if "CURRENCY_CODE_C" in available_columns
                else (
                    "UPPER(COALESCE(CURRENCY_C, '')) AS CURRENCY_CODE"
                    if "CURRENCY_C" in available_columns
                    else (
                        "UPPER(COALESCE(CURRENCY, '')) AS CURRENCY_CODE"
                        if "CURRENCY" in available_columns
                        else "'UNKNOWN' AS CURRENCY_CODE"
                    )
                )
            ),
        ]
        # Round 11 / Phase 7.5: ``SELECT DISTINCT`` collapses
        # duplicates but is non-deterministic when one
        # (ACCOUNT_ID_C, SUBSCRIPTION_ID) tuple has multiple
        # historical rows that differ on at least one
        # non-key column (CSSM_EMAIL change, CSSM_NAME
        # rebrand, ARR adjustment).  In that case DISTINCT
        # yields *all* of them and downstream dedupe picks an
        # arbitrary representative, so the same query can
        # return slightly different ARR per account on
        # successive runs.  Replace with a windowed query
        # that keeps the most recently modified row per
        # (ACCOUNT_ID_C, SUBSCRIPTION_ID), with a fallback
        # ordering when MODIFIED_DATE is unavailable.
        _modified_date_expr = _column_or_default_expr(
            available_columns, "MODIFIED_DATE", "NULL"
        )
        _created_date_expr = _column_or_default_expr(
            available_columns, "CREATED_DATE", "NULL"
        )
        _subscription_id_expr = (
            "SUBSCRIPTION_ID" if "SUBSCRIPTION_ID" in available_columns else "''"
        )
        sql_with_arr = f"""
        SELECT
          {", ".join(e for e in select_exprs if e)}
        FROM {DSM_TABLE}
        WHERE ACCOUNT_ID_C IN ({placeholders})
          {status_filter}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY ACCOUNT_ID_C, {_subscription_id_expr}
            ORDER BY {_modified_date_expr} DESC NULLS LAST,
                     {_created_date_expr} DESC NULLS LAST,
                     ACCOUNT_ID_C
        ) = 1
        """

        logger.debug("Executing schema-aware ARR SQL query")
        cur.execute(sql_with_arr, cleaned_ids)
        rows = cur.fetchall()
        if not rows:
            empty = _normalize_arr_df(pd.DataFrame())
            # Round 34 / D: fail-loud wrapper (empty-arr branch).
            _safe_annotate_with_contract(empty, dataset="subscriptions", source_label="arr_empty")
            return empty
        cols = [c[0] for c in cur.description]
        result = _normalize_arr_df(pd.DataFrame(rows, columns=cols))
        # Round 2 / Phase 4.4: stamp the row contract on the ARR
        # frame so the same drift detection that works on the
        # snowflake_prefetch path also fires when callers reach
        # fetch_arr_data directly.
        # Round 34 / D: fail-loud wrapper.
        _safe_annotate_with_contract(result, dataset="subscriptions", source_label="arr_data")
        # Round 10 / Phase 9.3: stamp ``arr_schema_degraded`` on the
        # frame's ``attrs`` when one or more monetary columns were
        # absent from the introspected schema and we substituted
        # ``0 AS ...``.  Downstream report assembly (the headline
        # ARR sentence in particular) MUST consult this flag and
        # add a ``partial_data_warnings`` entry instead of rendering
        # "$0 portfolio" as a real number.
        if _missing_monetary_columns:
            try:
                result.attrs['arr_schema_degraded'] = True
                result.attrs['arr_schema_missing_columns'] = list(_missing_monetary_columns)
                result.attrs['partial_data_warning'] = (
                    f"fetch_arr_data: monetary columns missing from schema "
                    f"({', '.join(_missing_monetary_columns)}); ARR/MRR/TCV/license "
                    "columns substituted with literal 0 -- portfolio-value totals "
                    "are NOT trustworthy until the source schema is restored."
                )
                logger.warning(
                    "fetch_arr_data: schema degraded; missing columns: %s",
                    ", ".join(_missing_monetary_columns),
                )
            except Exception as _attr_err:
                logger.debug("could not stamp arr_schema_degraded attrs: %s", _attr_err)
        return result

    except Exception as e:
        _log_snowflake_fallback("ARR data query", e)
        normalized_empty = _normalize_arr_df(pd.DataFrame())
        try:
            normalized_empty.attrs["fetch_error"] = str(e).strip() or e.__class__.__name__
            normalized_empty.attrs["fetch_error_dataset"] = "arr_data"
            normalized_empty.attrs["fetch_error_kind"] = (
                "access_or_schema" if _is_snowflake_access_issue(e) else "runtime"
            )
        except Exception:
            pass
        return normalized_empty
    finally:
        if cur:
            cur.close()

def fetch_support_cases_snowflake(ctx, account_ids: List[str], days: int, limit: int = 50000) -> pd.DataFrame:
    """Fetch support/TAC cases from Snowflake by account IDs.

    Tries SUPPORT_CASES; if that fails (table/perms), tries join via dsm_assignment_data.
    Used when no CSOne file is uploaded so renewal report can still show case counts.

    The default ``limit`` was raised from 1000 to 50000 because the prior
    cap silently truncated portfolios with high case volume, causing every
    downstream metric (BEMS, P1/P2/P3/P4, total cases, customer counts)
    to under-report. When the result equals ``limit``, the returned
    DataFrame is annotated via ``df.attrs['was_truncated'] = True`` and a
    warning is emitted so callers can surface a "result truncated"
    banner in their reports.

    Returns empty DataFrame on error or if table/columns are missing.
    """
    # Round 2 / Phase 1.12: when SUPPORT_CASES cannot be fetched
    # (missing context, invalid days, blocked by table policy), return
    # a normalized empty frame whose ``attrs`` carry an explicit
    # ``fetch_error`` and ``fetch_error_kind`` so downstream renderers
    # (renewal narrative, Ask AI grounded prompt) can show "support
    # cases unavailable" instead of treating zero rows as zero cases.
    def _empty_with_error(kind: str, message: str) -> pd.DataFrame:
        empty = pd.DataFrame()
        empty.attrs['fetch_error'] = message
        empty.attrs['fetch_error_kind'] = kind
        return empty

    if not ctx or not account_ids:
        return pd.DataFrame()  # genuinely empty (no scope) -> not an error
    if not isinstance(days, int) or days < 1 or days > 365:
        logger.warning(f"[[RENEWAL]] Invalid days parameter: {days}")
        return _empty_with_error('invalid_days', f"Invalid days parameter: {days}")
    if not isinstance(limit, int) or limit < 1:
        logger.warning(f"[[RENEWAL]] Invalid limit parameter: {limit}")
        return _empty_with_error('invalid_limit', f"Invalid limit parameter: {limit}")
    # Hard ceiling stays at 100k for memory safety, but this is well above
    # any real-world portfolio (largest manager < 5k cases / 90d).
    limit = min(limit, 100000)

    def _normalize_cases_df(df: pd.DataFrame) -> pd.DataFrame:
        expected = ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'CLOSED_DATE', 'SEVERITY']
        derived = ['case_status_norm', 'case_priority_norm', 'open_date', 'closed_date', 'is_open', 'open_age_days']
        if df is None or df.empty:
            empty = pd.DataFrame(columns=expected + derived)
            empty.attrs['was_truncated'] = False
            empty.attrs['fetch_limit'] = limit
            return empty
        normalized = df.copy()
        for col in expected:
            if col not in normalized.columns:
                normalized[col] = None
        # Round 5 / Phase 5.4: previously we re-implemented status /
        # priority / open-date normalization inline.  That duplicated
        # the rules baked into ``add_case_lifecycle_fields`` (which
        # also handles the "status unknown but closed_date present =>
        # closed" edge case, severity bucketing, BEMS detection,
        # customer-name resolution, etc.) and the two paths drifted.
        # Route through the canonical helper and project back down
        # to the schema this fetcher promises to its callers.
        try:
            enriched = add_case_lifecycle_fields(normalized)
        except Exception as _enrich_err:
            logger.debug(
                "Renewal cases _normalize_cases_df: add_case_lifecycle_fields "
                "fell back to inline normalization (%s)",
                _enrich_err,
            )
            enriched = normalized.copy()
            enriched["case_status_norm"] = enriched["STATUS"].apply(normalize_status_label)
            enriched["case_priority_norm"] = enriched["SEVERITY"].apply(normalize_priority_label)
            enriched["open_date"] = parse_datetime_series(enriched["CREATED_DATE"])
            enriched["closed_date"] = parse_datetime_series(enriched["CLOSED_DATE"])
            enriched["is_open"] = enriched["case_status_norm"].eq("Open")
            # Round 6 / Phase 4.14: tz-aware UTC reference so the
            # subtraction does not warn / coerce when ``open_date``
            # carries a timezone, and so the age does not silently
            # shift by the host's local UTC offset.
            _now_utc = pd.Timestamp(datetime.now(timezone.utc))
            _open_dt = enriched["open_date"]
            try:
                # Make sure both sides are tz-aware (UTC) so the
                # subtraction is unambiguous.
                if getattr(_open_dt.dt, 'tz', None) is None:
                    _open_dt = _open_dt.dt.tz_localize('UTC')
                else:
                    _open_dt = _open_dt.dt.tz_convert('UTC')
            except Exception:
                pass
            enriched["open_age_days"] = (
                (_now_utc - _open_dt).dt.days.where(enriched["is_open"], other=pd.NA)
            )
        # ``add_case_lifecycle_fields`` does not include the legacy
        # ``open_age_days`` projection if open_date is NaT for the
        # whole frame; ensure all promised columns exist before
        # subsetting.
        for _need in derived:
            if _need not in enriched.columns:
                enriched[_need] = pd.NA
        out = enriched[expected + derived]
        # Surface truncation so report code can warn the user.
        out.attrs['was_truncated'] = bool(len(out) >= limit)
        out.attrs['fetch_limit'] = limit
        if out.attrs['was_truncated']:
            logger.warning(
                f"[[RENEWAL]] Snowflake support-case fetch hit limit={limit}; "
                "result is TRUNCATED. Counts in downstream reports may under-report."
            )
        return out

    if is_table_blocked("CX_DB.CX_SWSSBST_BR.SUPPORT_CASES"):
        logger.info("[[RENEWAL]] SUPPORT_CASES disabled by Snowflake table policy; returning empty support cases.")
        # Round 2 / Phase 1.12: tag the empty frame so renderers
        # render an "unavailable" tristate instead of "zero cases".
        _blocked = _normalize_cases_df(pd.DataFrame())
        _blocked.attrs['fetch_error'] = 'SUPPORT_CASES blocked by Snowflake table policy'
        _blocked.attrs['fetch_error_kind'] = 'table_policy_violation'
        return _blocked

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
            # Round 12 / Phase 11.4: previously ``sort_values`` ran in
            # the default (quicksort) algorithm, which is *not* stable
            # -- two rows with the same ``CREATED_DATE`` could swap on
            # repeat runs and the ``head(limit)`` slice would then
            # return a different "Top N" set even when the underlying
            # data was identical.  Use ``kind='stable'`` and add a
            # ``CASE_ID`` (or ``ACCOUNT_ID``) tie-break so equal
            # timestamps always resolve in lexicographic order.
            _r12_secondary = next(
                (c for c in ('CASE_ID', 'CASE_NUMBER', 'ACCOUNT_ID') if c in merged.columns),
                None,
            )
            if _r12_secondary is not None:
                merged = merged.sort_values(
                    ['CREATED_DATE', _r12_secondary],
                    ascending=[False, True],
                    na_position='last',
                    kind='stable',
                )
            else:
                merged = merged.sort_values(
                    'CREATED_DATE', ascending=False, na_position='last', kind='stable'
                )
        return _normalize_cases_df(merged.head(limit))

    cur = None
    placeholders = ','.join(['%s'] * len(account_ids_clean))
    # Round 8 / Phase 2.3: bind explicit Python-computed UTC window
    # start instead of ``DATEADD(day, -%s, CURRENT_DATE())`` so the
    # support-cases lookback window is independent of Snowflake
    # session TZ.
    _support_window_start = _utc_window_start_iso(days)
    params = list(account_ids_clean) + [_support_window_start, limit]

    # Round 4: detect a real ``CLOSED_DATE`` column (or known synonyms)
    # so case-resolution analytics actually have a closure timestamp
    # instead of always seeing ``NULL``.  The previous SQL hardcoded
    # ``NULL AS CLOSED_DATE`` even when the underlying table exposed
    # the column, which silently broke open-vs-closed splits and TTR.
    _SUPPORT_CASES_TABLE = "CX_DB.CX_SWSSBST_BR.SUPPORT_CASES"
    try:
        _support_cols = _get_table_columns(ctx, _SUPPORT_CASES_TABLE)
    except Exception as _col_err:
        logger.debug("Support cases column probe failed: %s", _col_err)
        _support_cols = set()
    _CLOSE_COL_CANDIDATES = (
        "CLOSED_DATE",
        "CLOSEDDATE",
        "CLOSED_AT",
        "DATE_CLOSED",
        "RESOLVED_DATE",
        "RESOLUTION_DATE",
        "LASTMODIFIEDDATE",
        "LAST_MODIFIED_DATE",
    )
    _close_col = next((c for c in _CLOSE_COL_CANDIDATES if c in _support_cols), None)
    _close_select = f"s.{_close_col} AS CLOSED_DATE" if _close_col else "NULL AS CLOSED_DATE"
    _close_select_unaliased = f"{_close_col} AS CLOSED_DATE" if _close_col else "NULL AS CLOSED_DATE"

    # Try 1: SUPPORT_CASES with ACCOUNT_ID IN (...)
    try:
        logger.info(f"[[RENEWAL]] Attempting Snowflake support cases fetch for {len(account_ids_clean)} accounts (SUPPORT_CASES.ACCOUNT_ID)...")
        # Round 13 / Phase 7.1: previously the ORDER BY only sorted on
        # ``s.CREATED_DATE DESC`` -- which is non-deterministic when
        # the LIMIT clips a tail of cases that share the exact same
        # second-precision timestamp (Snowflake CREATED_DATE is to
        # the second; bulk-import jobs routinely write hundreds of
        # rows in the same second).  Add ``s.CASE_ID ASC`` as a
        # secondary key so the LIMIT keeps a stable, run-to-run
        # identical sample.
        sql1 = f"""
        SELECT s.CASE_ID, s.ACCOUNT_ID, s.SUBJECT, s.STATUS, s.CREATED_DATE, {_close_select}, s.SEVERITY
        FROM {_SUPPORT_CASES_TABLE} s
        WHERE s.ACCOUNT_ID IN ({placeholders})
          AND s.CREATED_DATE >= %s
        ORDER BY s.CREATED_DATE DESC, s.CASE_ID ASC
        LIMIT %s
        """
        cur = ctx.cursor()
        cur.execute(sql1, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description] if cur.description else ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'CLOSED_DATE', 'SEVERITY']
        if cur:
            cur.close()
            cur = None
        if rows:
            logger.info(f"[[RENEWAL]] Snowflake SUPPORT_CASES returned {len(rows)} support cases")
            return _normalize_cases_df(pd.DataFrame(rows, columns=cols))
        # Round 66 / Pass 2 (B10): when SUPPORT_CASES Try 1 returns 0
        # rows for the requested manager+technology scope, we are
        # currently returning an empty DataFrame WITHOUT trying the
        # ACCOUNT_ID_C / dsm_assignment_data fallbacks (those only
        # fire on exception, not on empty-result). Pre-R66 the only
        # signal was a ``logger.info`` line that the operator could
        # easily miss in a busy log. Promote to ``warning`` AND
        # attach a structured ``fetch_error_kind = 'empty_for_scope'``
        # marker on the returned DataFrame's ``attrs`` so the renewal
        # narrative gate, the ``partial_data_warnings`` surface in
        # ``analysis_status``, and the Ask AI grounding briefing can
        # all see "Snowflake returned 0 cases for the requested scope"
        # as a first-class signal rather than silently treating it as
        # "0 cases happened in this window".
        logger.warning(
            "[[RENEWAL]] Snowflake SUPPORT_CASES returned 0 rows for scope "
            "(accounts=%d, days=%d) -- confirm SUPPORT_CASES is populated for "
            "this manager+technology window before treating zero as real data.",
            len(account_ids_clean),
            days,
        )
        _empty = _normalize_cases_df(pd.DataFrame())
        _empty.attrs['fetch_error_kind'] = 'empty_for_scope'
        _empty.attrs['fetch_error'] = (
            f"SUPPORT_CASES returned 0 rows for scope "
            f"(accounts={len(account_ids_clean)}, days={days})"
        )
        _empty.attrs['scope_account_count'] = len(account_ids_clean)
        _empty.attrs['scope_window_days'] = int(days)
        return _empty
    except Exception as e1:
        if cur:
            try:
                cur.close()
            except Exception as ce:
                logger.debug(f"Error closing cursor: {ce}")
            cur = None
        logger.warning(f"[[RENEWAL]] Snowflake SUPPORT_CASES (ACCOUNT_ID) failed: {str(e1).strip()}")

    # Try 2: SUPPORT_CASES with ACCOUNT_ID_C (some schemas use _C suffix)
    try:
        logger.info(f"[[RENEWAL]] Trying SUPPORT_CASES.ACCOUNT_ID_C for {len(account_ids_clean)} accounts...")
        # Round 13 / Phase 7.1: same CASE_ID tie-break as the
        # ACCOUNT_ID variant above.
        sql2 = f"""
        SELECT CASE_ID, ACCOUNT_ID_C AS ACCOUNT_ID, SUBJECT, STATUS, CREATED_DATE, {_close_select_unaliased}, SEVERITY
        FROM {_SUPPORT_CASES_TABLE}
        WHERE ACCOUNT_ID_C IN ({placeholders})
          AND CREATED_DATE >= %s
        ORDER BY CREATED_DATE DESC, CASE_ID ASC
        LIMIT %s
        """
        cur = ctx.cursor()
        cur.execute(sql2, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description] if cur.description else ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'CLOSED_DATE', 'SEVERITY']
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
            except Exception as ce:
                logger.debug(f"Error closing cursor: {ce}")
            cur = None
        logger.warning(f"[[RENEWAL]] Snowflake SUPPORT_CASES (ACCOUNT_ID_C) failed: {str(e2).strip()}")

    # Try 3: Join via dsm_assignment_data (same table we use for team subs)
    try:
        logger.info(f"[[RENEWAL]] Trying support cases via JOIN to dsm_assignment_data...")
        # Round 13 / Phase 7.1: same CASE_ID tie-break as the
        # SUPPORT_CASES variants above.
        sql3 = f"""
        SELECT s.CASE_ID, s.ACCOUNT_ID, s.SUBJECT, s.STATUS, s.CREATED_DATE, {_close_select}, s.SEVERITY
        FROM {_SUPPORT_CASES_TABLE} s
        INNER JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data d ON TRIM(s.ACCOUNT_ID) = TRIM(d.ACCOUNT_ID_C)
        WHERE d.ACCOUNT_ID_C IN ({placeholders})
          AND s.CREATED_DATE >= %s
        ORDER BY s.CREATED_DATE DESC, s.CASE_ID ASC
        LIMIT %s
        """
        cur = ctx.cursor()
        cur.execute(sql3, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description] if cur.description else ['CASE_ID', 'ACCOUNT_ID', 'SUBJECT', 'STATUS', 'CREATED_DATE', 'CLOSED_DATE', 'SEVERITY']
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
            except Exception as ce:
                logger.debug(f"Error closing cursor: {ce}")
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
        # Canonical AB date column priority order. Using COALESCE here keeps
        # the renewal-style "OPEN_DATE_C if set else CREATED_DATE" behaviour
        # but is now identical to the CSConsole/Leader paths that read AB.
        date_expr = "DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))"

        # Use proper parameterized query to prevent SQL injection.
        # NOTE: "RECORD_TYPE_ID IS NULL" was previously included but pulled
        # in non-AB rows (Action Plans, Customer Pulse, etc) whenever a row
        # in the shared C360_CS_TASK_C_VW view had no record type. We now
        # only count rows whose RECORD_TYPE_ID matches the AB record type.
        placeholders = ','.join(['%s'] * len(account_ids))
        sql = f"""
        SELECT *
        FROM {AB_TABLE}
        WHERE ACCOUNT_ID_C IN ({placeholders})
          AND {date_expr} >= %s
          AND RECORD_TYPE_ID = '0122T000000GJfTQAW'
        """
        # Round 8 / Phase 2.3: bind explicit Python-computed UTC window
        # start (TZ-independent) instead of ``DATEADD(day, -%s, CURRENT_DATE())``.
        logger.debug("Executing SQL query...")
        cur.execute(sql, [*account_ids, _utc_window_start_iso(days)])
        logger.debug("Query executed, fetching results...")
        rows = cur.fetchall()
        logger.debug(f"Fetched {len(rows)} rows from adoption barriers query")
        if not rows:
            return pd.DataFrame()
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        _log_snowflake_fallback("Adoption barriers query", e)
        return _empty_df_with_fetch_error("adoption_barriers", e)
    finally:
        if cur:
            cur.close()

def load_and_merge_data_for_subscription(subscription_id: str, days: int, csone_data: list):
    """Load and merge CSConsole data for a specific subscription ID with proper SQL injection protection"""
    ctx = _connect_with_keeper()
    if ctx is None:
        return "Error", csone_data
    cur = None

    try:
        cur = ctx.cursor(snowflake.connector.DictCursor)
        sub_id_column_dsm = 'SUBSCRIPTION_ID'
        account_id_column_dsm = 'ACCOUNT_ID_C'

        logging.info(f"Fetching Account ID for Subscription ID '{subscription_id}'...")

        # Use parameterized query to prevent SQL injection
        # Column names are hardcoded constants, so this is safe
        # Round 11 / Phase 7.3: when a subscription has multiple
        # rows in ``dsm_assignment_data`` (e.g. historical
        # snapshots), the previous ``LIMIT 1`` returned an
        # arbitrary row.  Match the convention used by
        # ``fetch_subscription_data`` and prefer the most
        # recently modified row.  ``MODIFIED_DATE`` is allowed
        # to be NULL, so push NULLs to the bottom and use
        # ACCOUNT_ID_C as a stable secondary key.
        account_query = (
            "SELECT ACCOUNT_ID_C, BU_NAME FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data "
            "WHERE SUBSCRIPTION_ID = %s "
            "ORDER BY MODIFIED_DATE DESC NULLS LAST, ACCOUNT_ID_C ASC "
            "LIMIT 1"
        )
        cur.execute(account_query, (subscription_id,))
        account_result = cur.fetchone()
        if not account_result:
            # Backward-compatible fallback for environments that still expose SUBSCRIPTION_ID_C.
            legacy_query = (
                "SELECT ACCOUNT_ID_C, BU_NAME FROM CX_DB.CX_SWSSBST_BR.dsm_assignment_data "
                "WHERE SUBSCRIPTION_ID_C = %s "
                "ORDER BY MODIFIED_DATE DESC NULLS LAST, ACCOUNT_ID_C ASC "
                "LIMIT 1"
            )
            cur.execute(legacy_query, (subscription_id,))
            account_result = cur.fetchone()

        if not account_result:
            logging.warning(f"No account found for Subscription ID '{subscription_id}' in dsm_assignment_data. Only Excel data will be used.")
            customer_name = "Unknown (Not found in CSConsole)"
            return customer_name, csone_data

        account_id = account_result[account_id_column_dsm]
        customer_name = account_result['BU_NAME']
        logging.info(f"Found Account ID: {account_id} for Customer: {customer_name}. Fetching related records for the last {days} days...")

        # Use parameterized queries to prevent SQL injection.
        # Round 8 / Phase 2.3: bind explicit Python-computed UTC window
        # start instead of ``DATEADD(day, -%s, CURRENT_DATE())`` so the
        # lookback window is independent of Snowflake session TZ.
        # Round 11 / Phase 2.1: AB rows previously filtered on
        # ``DATE(CREATED_DATE)`` only, while the canonical
        # ``fetch_adoption_barriers`` path uses
        # ``DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))``
        # (Round 6 work).  That meant a barrier whose ``OPEN_DATE_C``
        # was inside the window but ``CREATED_DATE`` was outside (or
        # null) would be visible in the portfolio path but invisible
        # in the per-subscription path.  Reuse the canonical
        # COALESCE expression so the two surfaces agree.
        ab_date_expr = "DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))"
        ap_query = "SELECT *, 'Action Plan' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW WHERE record_type_id = '0122T000000QHBGQA4' AND ACCOUNT_ID_C = %s AND DATE(CREATED_DATE) >= %s"
        ab_query = (
            "SELECT *, 'Adoption Barrier' as RECORD_SOURCE "
            "FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW "
            "WHERE record_type_id = '0122T000000GJfTQAW' "
            "AND ACCOUNT_ID_C = %s "
            f"AND {ab_date_expr} >= %s"
        )
        cp_query = "SELECT *, 'Customer Pulse' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C WHERE ACCOUNT__C = %s AND DATE(CREATEDDATE) >= %s"
        sp_query = "SELECT *, 'Success Priority' as RECORD_SOURCE FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C WHERE RELATED_CUSTOMER__C = %s AND DATE(CREATEDDATE) >= %s"
        _legacy_window_start = _utc_window_start_iso(days)

        cur.execute(ap_query, (account_id, _legacy_window_start))
        action_plans = cur.fetchall()
        cur.execute(ab_query, (account_id, _legacy_window_start))
        adoption_barriers = cur.fetchall()
        cur.execute(cp_query, (account_id, _legacy_window_start))
        customer_pulse = cur.fetchall()
        success_priorities = []
        if is_table_blocked("EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"):
            logger.info("Success priorities query skipped by Snowflake table policy.")
        else:
            cur.execute(sp_query, (customer_name, _legacy_window_start))
            success_priorities = cur.fetchall()

        logging.info(f"Found {len(action_plans)} action plans, {len(adoption_barriers)} adoption barriers, {len(customer_pulse)} pulse records, and {len(success_priorities)} success priorities.")

        all_records = csone_data + action_plans + adoption_barriers + customer_pulse + success_priorities
        return customer_name, all_records

    except Exception as e:
        logging.error(f"Error in load_and_merge_data_for_subscription: {e}")
        return "Error", csone_data
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception as e:
                logger.debug(f"Error closing cursor: {e}")
        try:
            ctx.close()
        except Exception as e:
            logger.debug(f"Error closing connection: {e}")

# Round 6 / Phase 4.2: shared chunk size for CSConsole IN clauses.
# Snowflake's bind/parser limits make extremely large IN clauses
# fragile (and many connectors batch binds in network packets).
# 500 keeps a healthy safety margin while still cutting round-trips
# for the typical 1k-5k-account portfolios.
_CSCONSOLE_IN_CHUNK_SIZE = 500


def _execute_in_chunks(cur, sql_template: str, in_values: List[Any], extra_params_before: Optional[List[Any]] = None, extra_params_after: Optional[List[Any]] = None, chunk_size: int = _CSCONSOLE_IN_CHUNK_SIZE) -> Tuple[List[Any], Optional[List[Any]]]:
    """Run ``sql_template`` once per chunk of ``in_values``.

    Round 6 / Phase 4.2 helper.  ``sql_template`` MUST contain
    exactly one ``{IN_CLAUSE}`` token where the ``IN (..)``
    placeholders are spliced.  The helper executes the template once
    per chunk, prepending ``extra_params_before`` and appending
    ``extra_params_after`` to the bind list each time.  The combined
    rows and the cursor description from the last successful execute
    are returned; the caller is responsible for de-duplicating rows
    that may appear in more than one chunk.
    """
    rows: List[Any] = []
    description: Optional[List[Any]] = None
    if not in_values:
        return rows, description
    pre = list(extra_params_before or [])
    post = list(extra_params_after or [])
    n = len(in_values)
    for i in range(0, n, max(int(chunk_size or 1), 1)):
        chunk = in_values[i:i + chunk_size]
        placeholders = ','.join(['%s'] * len(chunk))
        sql = sql_template.replace("{IN_CLAUSE}", f"({placeholders})")
        params = pre + list(chunk) + post
        cur.execute(sql, params)
        chunk_rows = cur.fetchall()
        if chunk_rows:
            rows.extend(chunk_rows)
        if description is None:
            description = cur.description
    return rows, description


def fetch_csconsole_action_plans(
    ctx,
    account_ids: List[str],
    days: int,
    owner_emails: Optional[Iterable[Any]] = None,
) -> pd.DataFrame:
    """Fetch Action Plans from CSConsole with proper resource management.

    When ``owner_emails`` is provided, rows created/owned by any of those users
    are also returned even if the account is not in ``account_ids`` (e.g. a CSSM
    collaborating on another team's account). Results are de-duplicated by row ID.
    """
    if ctx is None:
        return pd.DataFrame()
    normalized_owners = _normalize_owner_emails(owner_emails)
    if not account_ids and not normalized_owners:
        return pd.DataFrame()

    cur = None
    try:
        cur = ctx.cursor()
        # Round 6 / Phase 4.2: split the original "OR account-IN OR owner-clause"
        # query into two independent queries so the account_ids IN
        # clause can be chunked.  Combine results and dedupe by ID.
        all_rows: List[Any] = []
        descr: Optional[List[Any]] = None
        if account_ids:
            sql_template = """
            SELECT ap.*, dsm.BU_NAME, 'Action Plan' as RECORD_SOURCE
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW ap
            LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                   ON ap.ACCOUNT_ID_C = dsm.ACCOUNT_ID_C
            WHERE ap.record_type_id = '0122T000000QHBGQA4'
              AND ap.ACCOUNT_ID_C IN {IN_CLAUSE}
              AND DATE(ap.CREATED_DATE) >= %s
            """
            # Round 8 / Phase 2.3: bind explicit Python-computed UTC
            # window start instead of session-TZ ``CURRENT_DATE()``.
            rows, d = _execute_in_chunks(
                cur, sql_template, list(account_ids), extra_params_after=[_utc_window_start_iso(days)]
            )
            if rows:
                all_rows.extend(rows)
            if d is not None:
                descr = d
        if normalized_owners:
            task_cols = _get_table_columns(ctx, "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW")
            owner_sql, owner_params = _build_owner_match_clause(
                task_cols, normalized_owners, TASK_OWNER_EMAIL_COLUMNS, table_alias="ap"
            )
            if owner_sql:
                sql_owner = f"""
                SELECT ap.*, dsm.BU_NAME, 'Action Plan' as RECORD_SOURCE
                FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW ap
                LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                       ON ap.ACCOUNT_ID_C = dsm.ACCOUNT_ID_C
                WHERE ap.record_type_id = '0122T000000QHBGQA4'
                  AND ({owner_sql})
                  AND DATE(ap.CREATED_DATE) >= %s
                """
                # Round 8 / Phase 2.3: bind UTC window start.
                cur.execute(sql_owner, [*owner_params, _utc_window_start_iso(days)])
                owner_rows = cur.fetchall()
                if owner_rows:
                    all_rows.extend(owner_rows)
                if descr is None:
                    descr = cur.description
            else:
                logger.info(
                    "Action Plans: no owner-like columns available in task view; "
                    "skipping owner-based expansion."
                )
        if not all_rows or descr is None:
            return pd.DataFrame()
        cols = [c[0] for c in descr]
        df = pd.DataFrame(all_rows, columns=cols)
        if "ID" in df.columns:
            df = df.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
        return df
    except Exception as e:
        _log_snowflake_fallback("CSConsole action plans query", e)
        return _empty_df_with_fetch_error("csconsole_action_plans", e)
    finally:
        if cur:
            cur.close()

def fetch_csconsole_customer_pulse(
    ctx,
    account_ids: List[str],
    days: int,
    owner_emails: Optional[Iterable[Any]] = None,
) -> pd.DataFrame:
    """Fetch Customer Pulse records from CSConsole with proper resource management.

    When ``owner_emails`` is provided, pulse records created/owned by any of the
    given users are also returned regardless of account ownership, to cover
    collaborators who are not on the account's primary team. Deduplicated by ID.
    """
    if ctx is None:
        return pd.DataFrame()
    normalized_owners = _normalize_owner_emails(owner_emails)
    if not account_ids and not normalized_owners:
        return pd.DataFrame()

    cur = None
    try:
        cur = ctx.cursor()
        # Round 6 / Phase 4.2: chunk the account IN clause; run the
        # owner-clause as a second query when present.
        all_rows: List[Any] = []
        descr: Optional[List[Any]] = None
        if account_ids:
            sql_template = """
            SELECT cp.*, dsm.BU_NAME, 'Customer Pulse' as RECORD_SOURCE
            FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C cp
            LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                   ON cp.ACCOUNT__C = dsm.ACCOUNT_ID_C
            WHERE cp.ACCOUNT__C IN {IN_CLAUSE}
              AND DATE(cp.CREATEDDATE) >= %s
            """
            # Round 8 / Phase 2.3: bind UTC window start.
            rows, d = _execute_in_chunks(
                cur, sql_template, list(account_ids), extra_params_after=[_utc_window_start_iso(days)]
            )
            if rows:
                all_rows.extend(rows)
            if d is not None:
                descr = d
        if normalized_owners:
            pulse_cols = _get_table_columns(ctx, "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C")
            owner_sql, owner_params = _build_owner_match_clause(
                pulse_cols, normalized_owners, PULSE_OWNER_EMAIL_COLUMNS, table_alias="cp"
            )
            if owner_sql:
                sql_owner = f"""
                SELECT cp.*, dsm.BU_NAME, 'Customer Pulse' as RECORD_SOURCE
                FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C cp
                LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                       ON cp.ACCOUNT__C = dsm.ACCOUNT_ID_C
                WHERE ({owner_sql})
                  AND DATE(cp.CREATEDDATE) >= %s
                """
                # Round 8 / Phase 2.3: bind UTC window start.
                cur.execute(sql_owner, [*owner_params, _utc_window_start_iso(days)])
                owner_rows = cur.fetchall()
                if owner_rows:
                    all_rows.extend(owner_rows)
                if descr is None:
                    descr = cur.description
            else:
                logger.info(
                    "Customer Pulse: no owner-like columns available in pulse table; "
                    "skipping owner-based expansion."
                )
        if not all_rows or descr is None:
            return pd.DataFrame()
        cols = [c[0] for c in descr]
        df = pd.DataFrame(all_rows, columns=cols)
        if "ID" in df.columns:
            df = df.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
        return df
    except Exception as e:
        _log_snowflake_fallback("CSConsole customer pulse query", e)
        return _empty_df_with_fetch_error("csconsole_customer_pulse", e)
    finally:
        if cur:
            cur.close()

def fetch_csconsole_success_priorities(ctx, customer_identifiers: List[str], days: int) -> pd.DataFrame:
    """Fetch Success Priorities from CSConsole (RELATED_CUSTOMER__C) with proper resource management."""
    if ctx is None:
        return pd.DataFrame()
    if not customer_identifiers:
        return pd.DataFrame()
    if is_table_blocked("EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"):
        logger.info("Success priorities table blocked by Snowflake table policy; returning empty result.")
        return pd.DataFrame()

    cur = None
    try:
        cur = ctx.cursor()
        # Round 6 / Phase 4.2: chunk the RELATED_CUSTOMER__C IN list
        # so very wide portfolios do not hit Snowflake's IN-clause
        # bind limit.  Dedupe by ID after combining chunks.
        sql_template = """
        SELECT *, 'Success Priority' as RECORD_SOURCE
        FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C
        WHERE RELATED_CUSTOMER__C IN {IN_CLAUSE}
          AND DATE(CREATEDDATE) >= %s
        """
        # Round 8 / Phase 2.3: bind UTC window start.
        rows, descr = _execute_in_chunks(
            cur, sql_template, list(customer_identifiers), extra_params_after=[_utc_window_start_iso(days)]
        )
        if not rows or descr is None:
            return pd.DataFrame()
        cols = [c[0] for c in descr]
        df = pd.DataFrame(rows, columns=cols)
        if "ID" in df.columns:
            df = df.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
        return df
    except Exception as e:
        _log_snowflake_fallback("CSConsole success priorities query", e)
        return _empty_df_with_fetch_error("csconsole_success_priorities", e)
    finally:
        if cur:
            cur.close()

def fetch_csconsole_adoption_barriers(
    ctx,
    account_ids: List[str],
    days: int,
    owner_emails: Optional[Iterable[Any]] = None,
) -> pd.DataFrame:
    """Fetch Adoption Barriers from CSConsole with proper resource management.

    When ``owner_emails`` is provided, barriers created/owned by those users are
    also returned regardless of account team, so collaborators outside the
    active working team are captured. Results are de-duplicated by row ID.
    """
    if ctx is None:
        return pd.DataFrame()
    normalized_owners = _normalize_owner_emails(owner_emails)
    if not account_ids and not normalized_owners:
        return pd.DataFrame()

    cur = None
    try:
        cur = ctx.cursor()
        # Round 6 / Phase 4.2: chunk the account-id IN clause and run
        # the owner clause separately.  Combine and dedupe by ID.
        all_rows: List[Any] = []
        descr: Optional[List[Any]] = None
        if account_ids:
            sql_template = """
            SELECT *, 'Adoption Barrier' as RECORD_SOURCE
            FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
            WHERE record_type_id = '0122T000000GJfTQAW'
              AND ACCOUNT_ID_C IN {IN_CLAUSE}
              AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                  >= %s
            """
            # Round 8 / Phase 2.3: bind UTC window start.
            rows, d = _execute_in_chunks(
                cur, sql_template, list(account_ids), extra_params_after=[_utc_window_start_iso(days)]
            )
            if rows:
                all_rows.extend(rows)
            if d is not None:
                descr = d
        if normalized_owners:
            task_cols = _get_table_columns(ctx, "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW")
            owner_sql, owner_params = _build_owner_match_clause(
                task_cols, normalized_owners, TASK_OWNER_EMAIL_COLUMNS, table_alias=None
            )
            if owner_sql:
                sql_owner = f"""
                SELECT *, 'Adoption Barrier' as RECORD_SOURCE
                FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
                WHERE record_type_id = '0122T000000GJfTQAW'
                  AND ({owner_sql})
                  AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                      >= %s
                """
                # Round 8 / Phase 2.3: bind UTC window start.
                cur.execute(sql_owner, [*owner_params, _utc_window_start_iso(days)])
                owner_rows = cur.fetchall()
                if owner_rows:
                    all_rows.extend(owner_rows)
                if descr is None:
                    descr = cur.description
            else:
                logger.info(
                    "Adoption Barriers: no owner-like columns available in task view; "
                    "skipping owner-based expansion."
                )
        if not all_rows or descr is None:
            return pd.DataFrame()
        cols = [c[0] for c in descr]
        df = pd.DataFrame(all_rows, columns=cols)
        if "ID" in df.columns:
            df = df.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
        return df
    except Exception as e:
        _log_snowflake_fallback("CSConsole adoption barriers query", e)
        return _empty_df_with_fetch_error("csconsole_adoption_barriers", e)
    finally:
        if cur:
            cur.close()


def fetch_period_comparison(ctx, account_ids, days):
    """Compare current vs previous period metrics for trend analysis.

    Compares adoption barriers, customer pulse, and action plans between
    the current period (last N days) and previous period (N to 2N days ago).
    Returns a dict with counts and percentage changes.
    """
    if ctx is None or not account_ids:
        return {}

    comparison = {}
    cur = None
    # Round 6 / Phase 4.2: chunk the account-id list and aggregate
    # the per-chunk results in Python so very wide portfolios do not
    # exceed Snowflake's IN-clause bind limit.  For SUM aggregates
    # we can simply add chunk results together; for AVG we collect
    # SUM + COUNT per chunk and recompute AVG = sum/count overall.
    account_ids = list(account_ids)
    _chunks = [
        account_ids[i:i + _CSCONSOLE_IN_CHUNK_SIZE]
        for i in range(0, len(account_ids), _CSCONSOLE_IN_CHUNK_SIZE)
    ] or [account_ids]
    try:
        cur = ctx.cursor()

        # Adoption barriers: current vs previous
        # Round 3 / Phase 4.1: make the two windows disjoint so the
        # boundary day (today - days) is not counted in both buckets.
        # New convention:
        #   current:  D >= today - days
        #   previous: D >= today - 2*days  AND  D < today - days
        # Round 8 / Phase 2.3: compute the window edges in Python (UTC)
        # so the buckets do not slide with Snowflake session TZ.
        _curr_start = _utc_window_start_iso(days)
        _prev_start = _utc_window_start_iso(days * 2)
        ab_curr_total = 0
        ab_prev_total = 0
        for _chunk in _chunks:
            _ph = ", ".join(["%s"] * len(_chunk))
            ab_query = f"""
                SELECT
                    SUM(CASE WHEN DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                             >= %s THEN 1 ELSE 0 END) AS current_period,
                    SUM(CASE WHEN DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                             >= %s
                         AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                             <  %s THEN 1 ELSE 0 END) AS previous_period
                FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
                WHERE ACCOUNT_ID_C IN ({_ph})
                  AND RECORD_TYPE_ID = '0122T000000GJfTQAW'
                  AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                      >= %s
            """
            cur.execute(ab_query, (_curr_start, _prev_start, _curr_start, *_chunk, _prev_start))
            row = cur.fetchone()
            if row:
                ab_curr_total += int(row[0] or 0)
                ab_prev_total += int(row[1] or 0)
        curr, prev = ab_curr_total, ab_prev_total
        # Round 12 / Phase 11.1: route percent change through canonical
        # ``_r12_round_percent`` helper.
        # Round 30 / L3: replace inline ``/ if > 0`` ternary with the
        # canonical ``_safe_div`` so NaN/None/inf inputs collapse to 0
        # instead of silently propagating through the percentage.
        pct = _r12_round_percent(_safe_div(curr - prev, prev) * 100, 1)
        comparison['adoption_barriers'] = {
            'current': curr, 'previous': prev,
            'change_pct': pct,
            'trend': 'increasing' if pct > 10 else 'decreasing' if pct < -10 else 'stable'
        }

        # Customer pulse: average score current vs previous
        try:
            # Phase 4.1 / Phase 4.2: disjoint current / previous
            # windows; chunked + Python-side aggregation.  We pull
            # SUM and COUNT per window so the global AVG is correct
            # across chunks (a simple AVG-of-AVGs would weight chunks
            # unequally if they contained different row counts).
            curr_sum = 0.0
            curr_cnt = 0
            prev_sum = 0.0
            prev_cnt = 0
            for _chunk in _chunks:
                _ph = ", ".join(["%s"] * len(_chunk))
                pulse_query = f"""
                    SELECT
                        SUM(CASE WHEN DATE(CREATEDDATE) >= %s
                                 THEN SCORE__C END) AS curr_sum,
                        COUNT(CASE WHEN DATE(CREATEDDATE) >= %s
                                   AND SCORE__C IS NOT NULL THEN 1 END) AS curr_cnt,
                        SUM(CASE WHEN DATE(CREATEDDATE) >= %s
                                  AND DATE(CREATEDDATE) <  %s
                                 THEN SCORE__C END) AS prev_sum,
                        COUNT(CASE WHEN DATE(CREATEDDATE) >= %s
                                    AND DATE(CREATEDDATE) <  %s
                                    AND SCORE__C IS NOT NULL THEN 1 END) AS prev_cnt
                    FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C
                    WHERE ACCOUNT__C IN ({_ph})
                      AND DATE(CREATEDDATE) >= %s
                """
                # Round 8 / Phase 2.3: bind Python-computed UTC window
                # edges instead of session-TZ DATEADD().
                cur.execute(
                    pulse_query,
                    (_curr_start, _curr_start, _prev_start, _curr_start, _prev_start, _curr_start, *_chunk, _prev_start),
                )
                row = cur.fetchone()
                if row:
                    if row[0] is not None:
                        curr_sum += float(row[0])
                    curr_cnt += int(row[1] or 0)
                    if row[2] is not None:
                        prev_sum += float(row[2])
                    prev_cnt += int(row[3] or 0)
            if curr_cnt > 0:
                curr_avg = curr_sum / curr_cnt
                # Round 30 / L3: route through ``_safe_div`` with the
                # current-window average as the default so a missing
                # prior window yields a no-change comparison instead of
                # a divide-by-zero or NaN.
                prev_avg = _safe_div(prev_sum, prev_cnt, default=curr_avg)
                comparison['customer_pulse'] = {
                    'current_avg': round(curr_avg, 1),
                    'previous_avg': round(prev_avg, 1),
                    'change': round(curr_avg - prev_avg, 1),
                    'trend': 'improving' if curr_avg > prev_avg else 'declining' if curr_avg < prev_avg else 'stable'
                }
        except Exception as _trend_err:
            # Round 5 / Phase 4.14: the customer-pulse subsection
            # used to swallow its exception and silently leave
            # ``comparison['customer_pulse']`` unset, indistinguishable
            # from "no pulse activity in the window".  Surface the
            # subsection failure on a structured ``subsection_errors``
            # dict so downstream callers / dashboards can render
            # "pulse trend unavailable due to data fetch error" rather
            # than treating the missing field as healthy data.
            # Round 8 / Phase 2.2: previously the dict carried the raw
            # ``str(_trend_err)`` which leaks SQLState codes, hostnames,
            # and library traceback fragments to anyone reading the JSON
            # result.  Replace the verbatim text with a stable
            # ``error_kind`` and a generic user-facing message; full
            # detail still lives in the WARNING log below.
            logger.warning("Period comparison customer_pulse subsection failed", exc_info=True)
            comparison.setdefault('subsection_errors', {})['customer_pulse'] = {
                'error_kind': _stable_failure_kind(_trend_err),
                'user_message': 'See logs for details',
                # Round 6 / Phase 7.4: keep the legacy ``failure_kind``
                # alias for downstream consumers that already key off it.
                'failure_kind': _stable_failure_kind(_trend_err),
            }

        # Action plans: current vs previous
        try:
            # Phase 4.1: disjoint windows; Phase 4.2: chunk + Python-side aggregation.
            ap_curr_total = 0
            ap_prev_total = 0
            for _chunk in _chunks:
                _ph = ", ".join(["%s"] * len(_chunk))
                ap_query = f"""
                    SELECT
                        SUM(CASE WHEN DATE(CREATED_DATE) >= %s THEN 1 ELSE 0 END),
                        SUM(CASE WHEN DATE(CREATED_DATE) >= %s
                                  AND DATE(CREATED_DATE) <  %s THEN 1 ELSE 0 END)
                    FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
                    WHERE record_type_id = '0122T000000QHBGQA4'
                      AND ACCOUNT_ID_C IN ({_ph})
                      AND DATE(CREATED_DATE) >= %s
                """
                # Round 8 / Phase 2.3: bind Python-computed UTC window edges.
                cur.execute(ap_query, (_curr_start, _prev_start, _curr_start, *_chunk, _prev_start))
                row = cur.fetchone()
                if row:
                    ap_curr_total += int(row[0] or 0)
                    ap_prev_total += int(row[1] or 0)
            curr, prev = ap_curr_total, ap_prev_total
            # Round 12 / Phase 11.1: canonical percent rounding.
            # Round 30 / L3: route through ``_safe_div`` for consistent
            # zero/NaN handling.
            pct = _r12_round_percent(_safe_div(curr - prev, prev) * 100, 1)
            comparison['action_plans'] = {
                'current': curr, 'previous': prev,
                'change_pct': pct,
                'trend': 'increasing' if pct > 10 else 'decreasing' if pct < -10 else 'stable'
            }
        except Exception as e:
            # Round 5 / Phase 4.14: same disambiguation for the
            # action-plans subsection.
            # Round 8 / Phase 2.2: same redaction as customer_pulse above.
            logger.warning("Period comparison action_plans subsection failed", exc_info=True)
            comparison.setdefault('subsection_errors', {})['action_plans'] = {
                'error_kind': _stable_failure_kind(e),
                'user_message': 'See logs for details',
                'failure_kind': _stable_failure_kind(e),
            }

    except Exception as e:
        # Phase 1.3a: surface the failure so the prefetch / formatter layer
        # can distinguish "no comparison available because of error" from
        # "no comparison available because period had zero activity".
        # Round 8 / Phase 2.2: ``fetch_error`` previously held ``str(e)``;
        # this ended up serialized into JSON responses and surfaced driver
        # exception text to clients.  Replace with the same structured
        # ``error_kind`` payload used by subsection errors.
        logger.warning("Period comparison query failed", exc_info=True)
        comparison['fetch_error'] = {
            'error_kind': _stable_failure_kind(e),
            'user_message': 'See logs for details',
        }
        comparison['fetch_error_dataset'] = 'period_comparison'
    finally:
        if cur:
            try:
                cur.close()
            except Exception as _close_err:
                logger.debug("Cursor close error (period comparison): %s", _close_err)
    return comparison


def fetch_barrier_velocity(ctx, account_ids, days):
    """Calculate the rate of barrier creation and resolution over time.

    Breaks the analysis period into weekly buckets and computes
    new-barrier and closed-barrier counts per week, plus a net velocity.
    """
    if ctx is None or not account_ids:
        return {}

    velocity = {}
    cur = None
    # Round 6 / Phase 4.2: chunk the IN clause and merge per-week
    # counts in Python so wide portfolios do not exceed Snowflake's
    # IN-clause bind limit.  GROUP BY week_start across chunks is
    # re-aggregated by adding new + closed per week_start.
    account_ids = list(account_ids)
    _chunks = [
        account_ids[i:i + _CSCONSOLE_IN_CHUNK_SIZE]
        for i in range(0, len(account_ids), _CSCONSOLE_IN_CHUNK_SIZE)
    ] or [account_ids]
    try:
        cur = ctx.cursor()
        merged: Dict[str, Dict[str, int]] = {}
        for _chunk in _chunks:
            _ph = ", ".join(["%s"] * len(_chunk))
            query = f"""
                SELECT
                    DATE_TRUNC('week', COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C)) AS week_start,
                    COUNT(*) AS new_barriers,
                    SUM(CASE WHEN UPPER(STATUS_C) IN ('CLOSED', 'RESOLVED', 'COMPLETED') THEN 1 ELSE 0 END) AS closed_barriers
                FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW
                WHERE ACCOUNT_ID_C IN ({_ph})
                  AND RECORD_TYPE_ID = '0122T000000GJfTQAW'
                  AND DATE(COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C))
                      >= %s
                GROUP BY week_start
                ORDER BY week_start DESC
            """
            # Round 8 / Phase 2.3: bind UTC window start.
            cur.execute(query, (*_chunk, _utc_window_start_iso(days)))
            for row in cur.fetchall():
                # Round 12 / Phase 2.4: ``str(row[0])[:10]`` brittle-slices
                # the first 10 chars of whatever the Snowflake driver
                # decides to return for ``DATE_TRUNC('week', ...)``.
                # Different driver versions / connection types return
                # ``date`` / ``datetime`` / ``str`` here -- under a
                # numpy.datetime64 path that string slice yields
                # ``"2025-04-2"`` (truncated 9-char) for some weeks and
                # silently corrupts the dict key.  Parse to a real
                # ``pd.Timestamp`` and emit a canonical ``YYYY-MM-DD``
                # string regardless of source representation.
                if row[0] is None:
                    wk = 'Unknown'
                else:
                    try:
                        wk_ts = pd.to_datetime(row[0], errors='coerce', utc=True)
                        if pd.isna(wk_ts):
                            wk = 'Unknown'
                        else:
                            wk = wk_ts.strftime('%Y-%m-%d')
                    except Exception:
                        wk = str(row[0])[:10] or 'Unknown'
                bucket = merged.setdefault(wk, {'new': 0, 'closed': 0})
                bucket['new'] += int(row[1] or 0)
                bucket['closed'] += int(row[2] or 0)

        weeks = []
        total_new = total_closed = 0
        for wk in sorted(merged.keys(), reverse=True):
            new_ct = merged[wk]['new']
            closed_ct = merged[wk]['closed']
            weeks.append({'week': wk, 'new': new_ct, 'closed': closed_ct, 'net': new_ct - closed_ct})
            total_new += new_ct
            total_closed += closed_ct

        n_weeks = max(len(weeks), 1)
        # Round 4: surface ``weeks_total`` so consumers cannot misread
        # ``len(weeks)`` (capped at 12 below) as the analysis window.
        velocity = {
            'weeks': weeks[:12],
            'weeks_total': len(weeks),
            'weeks_displayed': min(len(weeks), 12),
            'weeks_truncated': len(weeks) > 12,
            # Round 12 / Phase 11.1: route percentage / per-week averages
            # through ``_r12_round_percent`` so all callers share a
            # single half-away-from-zero rounding (Python's banker's
            # rounding surprises CSSMs reading "0%" for 0.5%+ risk).
            'avg_new_per_week': _r12_round_percent(total_new / n_weeks, 1),
            'avg_closed_per_week': _r12_round_percent(total_closed / n_weeks, 1),
            'net_velocity_per_week': _r12_round_percent((total_new - total_closed) / n_weeks, 1),
            # Round 30 / L3: ``_safe_div`` so an empty period or a
            # NaN-coerced ``total_new`` produces ``0%`` instead of
            # raising or polluting the rounding helper.
            'resolution_rate_pct': _r12_round_percent(_safe_div(total_closed, total_new) * 100, 1),
        }

    except Exception as e:
        # Phase 1.3a: same rationale as fetch_period_comparison; record the
        # error in the returned dict so callers can show "barrier velocity
        # unavailable" instead of an empty chart.
        logger.warning(f"Barrier velocity query failed: {e}")
        velocity['fetch_error'] = str(e).strip() or e.__class__.__name__
        velocity['fetch_error_dataset'] = 'barrier_velocity'
    finally:
        if cur:
            try:
                cur.close()
            except Exception as _close_err:
                logger.debug("Cursor close error (barrier velocity): %s", _close_err)
    return velocity


def calculate_arr_at_risk(arr_df, ab_df, cases_df=None):
    """Calculate total ARR tied to accounts with active adoption barriers or cases.

    Returns a breakdown of ARR by risk tier and the total portfolio ARR at risk.
    """
    if arr_df is None or arr_df.empty:
        return {}
    if ab_df is None:
        ab_df = pd.DataFrame()
    if cases_df is None:
        cases_df = pd.DataFrame()

    result = {}
    try:
        arr_col = 'ANNUAL_CONTRACT_VALUE' if 'ANNUAL_CONTRACT_VALUE' in arr_df.columns else None
        acct_col = 'ACCOUNT_ID_C' if 'ACCOUNT_ID_C' in arr_df.columns else None
        if not arr_col or not acct_col:
            return {}

        # Round 2 / Phase 1.1: detect mixed-currency ARR rows.  Summing
        # USD + EUR + JPY blindly produces a meaningless headline; we
        # surface a flag so renderers can either restrict to one
        # currency or render a per-currency breakdown.
        currency_col = 'CURRENCY_CODE' if 'CURRENCY_CODE' in arr_df.columns else None
        per_currency_breakdown: Dict[str, float] = {}
        is_multi_currency = bool(arr_df.attrs.get('is_multi_currency')) if hasattr(arr_df, 'attrs') else False
        # Round 13 / Phase 1.5: track whether ambiguous (NULL/blank)
        # CURRENCY_CODE rows exist; mixing rows with unknown currency
        # alongside known currencies (or even alongside each other) is
        # NOT comparable and must NOT collapse into a single
        # ``is_multi_currency=False`` answer.
        has_unknown_currency = False
        if currency_col:
            try:
                grouped = (
                    arr_df.groupby(currency_col, dropna=False)[arr_col]
                    .sum(min_count=0)
                    .to_dict()
                )
                per_currency_breakdown = {
                    str(k or 'UNKNOWN'): float(v or 0.0) for k, v in grouped.items()
                }
                distinct_known = [c for c in per_currency_breakdown.keys() if c and c != 'UNKNOWN']
                # Round 13 / Phase 1.5: NULL/blank currency rows are
                # treated as non-comparable; presence of an UNKNOWN
                # bucket alongside any other currency, or as the sole
                # currency, forces is_multi_currency=True so summed
                # totals are NOT presented as a comparable headline.
                has_unknown_currency = 'UNKNOWN' in per_currency_breakdown
                is_multi_currency = (
                    is_multi_currency
                    or len(distinct_known) > 1
                    or (has_unknown_currency and (len(distinct_known) >= 1
                                                   or len(per_currency_breakdown) > 1))
                )
            except Exception:
                per_currency_breakdown = {}
        result['arr_by_currency'] = per_currency_breakdown
        result['is_multi_currency'] = bool(is_multi_currency)
        # Round 13 / Phase 1.5: surface the ambiguity flag so renderers
        # can show "currency unknown -- not comparable" instead of a
        # bogus single-currency total.
        result['has_unknown_currency'] = bool(has_unknown_currency)
        if per_currency_breakdown:
            result['currencies_present'] = sorted(per_currency_breakdown.keys())

        total_arr = arr_df[arr_col].sum(skipna=True)
        if pd.isna(total_arr):
            total_arr = 0.0
        # When mixed currencies are present, leave the headline at 0
        # and rely on ``arr_by_currency`` so consumers do not display a
        # misleading single number.  Single-currency portfolios behave
        # exactly as before.
        if is_multi_currency:
            result['total_portfolio_arr'] = 0.0
            result['total_portfolio_arr_unsafe_sum'] = float(total_arr)
        else:
            result['total_portfolio_arr'] = float(total_arr)

        troubled_accounts = set()
        critical_accounts = set()

        # Round 5 / Phase 5.3: previously, ANY adoption barrier on an
        # account marked it "at risk" and inflated the headline ARR
        # number with closed / resolved / informational barriers from
        # months ago.  Restrict to OPEN, customer-impacting barriers
        # so the ARR-at-risk figure reflects current exposure.
        # Status normalisation reuses ``normalize_status_label`` /
        # ``add_case_lifecycle_fields`` heuristics: open => not in
        # the closed/resolved/withdrawn set.
        _CLOSED_STATUS_TOKENS = {
            'closed', 'resolved', 'completed', 'cancelled', 'withdrawn',
            'rejected', 'duplicate', 'won-not-fixed',
        }

        def _is_open_status(val: Any) -> bool:
            try:
                s = str(val or '').strip().lower()
            except Exception:
                return True  # if we can't tell, keep counting (safer)
            if not s:
                return True
            for tok in _CLOSED_STATUS_TOKENS:
                if tok in s:
                    return False
            return True

        if not ab_df.empty and 'ACCOUNT_ID_C' in ab_df.columns:
            ab_open_df = ab_df
            status_col = next(
                (c for c in ('AB_STATUS_C', 'STATUS_C', 'status_norm', 'STATUS', 'Status')
                 if c in ab_df.columns),
                None,
            )
            if status_col:
                try:
                    open_mask = ab_df[status_col].apply(_is_open_status)
                    ab_open_df = ab_df.loc[open_mask]
                except Exception:
                    ab_open_df = ab_df
            troubled_accounts.update(ab_open_df['ACCOUNT_ID_C'].dropna().unique())
            try:
                from data_normalization import normalize_severity_label as _norm_sev
                sev_col = next(
                    (c for c in ('severity_norm', 'SEVERITY_C', 'severity_c', 'Severity', 'PRIORITY')
                     if c in ab_open_df.columns),
                    None,
                )
                if sev_col:
                    if sev_col == 'severity_norm':
                        sev_norm = ab_open_df[sev_col].fillna('').astype(str)
                    else:
                        sev_norm = ab_open_df[sev_col].apply(_norm_sev).fillna('').astype(str)
                    crit_mask = sev_norm.isin(['Critical', 'High'])
                    critical_accounts.update(ab_open_df.loc[crit_mask, 'ACCOUNT_ID_C'].dropna().unique())
            except Exception:
                pass

        if not cases_df.empty:
            case_acct = 'ACCOUNT_ID' if 'ACCOUNT_ID' in cases_df.columns else 'ACCOUNT_ID_C' if 'ACCOUNT_ID_C' in cases_df.columns else None
            if case_acct:
                # Round 5 / Phase 5.3: only OPEN cases count for
                # ARR-at-risk -- a long-resolved case from a year ago
                # should not put the customer's renewal in jeopardy
                # today.
                cases_open = cases_df
                case_status_col = next(
                    (c for c in ('STATUS', 'Status', 'CASE_STATUS', 'STATUS_C', 'status_norm')
                     if c in cases_df.columns),
                    None,
                )
                if case_status_col:
                    try:
                        cases_open = cases_df.loc[cases_df[case_status_col].apply(_is_open_status)]
                    except Exception:
                        cases_open = cases_df
                troubled_accounts.update(cases_open[case_acct].dropna().unique())

        # Round 5 / Phase 5.3: filter the ARR base to ACTIVE
        # subscriptions before summing.  An account whose only
        # subscription is already terminated / cancelled has no ARR
        # to put at risk.  The filter is applied conservatively:
        # if no STATUS_C / SUBSCRIPTION_STATUS column is present we
        # leave the ARR base alone (back-compat with callers that
        # already pre-filter).
        arr_active_df = arr_df
        sub_status_col = next(
            (c for c in ('STATUS_C', 'SUBSCRIPTION_STATUS', 'STATUS', 'Status')
             if c in arr_df.columns),
            None,
        )
        if sub_status_col:
            try:
                arr_active_df = arr_df.loc[arr_df[sub_status_col].apply(_is_open_status)]
            except Exception:
                arr_active_df = arr_df
        result['active_subs_excluded_count'] = int(len(arr_df) - len(arr_active_df))

        # Round 28: route ARR sums through ``pd.to_numeric(errors='coerce')``
        # for parity with the safe pattern in ``compute_metrics_from_frame``
        # (`adoptiq_backend.py` _arr_sum block).  Previously a single
        # NaN-bearing or string-coerced ARR row would propagate NaN
        # through the subsequent percent / band math and surface as
        # ``"$nan"`` in the rendered report.
        at_risk_mask = arr_active_df[acct_col].isin(troubled_accounts)
        _arr_at_risk_series = pd.to_numeric(
            arr_active_df.loc[at_risk_mask, arr_col], errors='coerce'
        ).fillna(0)
        arr_at_risk = float(_arr_at_risk_series.sum())

        critical_mask = arr_active_df[acct_col].isin(critical_accounts)
        _arr_critical_series = pd.to_numeric(
            arr_active_df.loc[critical_mask, arr_col], errors='coerce'
        ).fillna(0)
        arr_critical = float(_arr_critical_series.sum())

        # Round 2 / Phase 1.1: when the portfolio is multi-currency,
        # all aggregate amounts are unsafe sums; expose them as
        # ``*_unsafe_sum`` and zero the safe headline so the report
        # is forced to show the per-currency breakdown instead.
        if is_multi_currency:
            result['arr_at_risk'] = 0.0
            result['arr_at_risk_unsafe_sum'] = float(arr_at_risk)
            result['arr_critical'] = 0.0
            result['arr_critical_unsafe_sum'] = float(arr_critical)
            result['arr_healthy'] = 0.0
            result['pct_at_risk'] = 0.0
            result['pct_critical'] = 0.0
        else:
            result['arr_at_risk'] = float(arr_at_risk)
            result['arr_critical'] = float(arr_critical)
            result['arr_healthy'] = float(total_arr - arr_at_risk)
            # Round 12 / Phase 11.1: route ARR-at-risk percentages through
            # ``_r12_round_percent`` for half-away-from-zero parity.
            # Round 30 / L3: ``_safe_div`` collapses to 0 for empty
            # portfolios / non-finite totals so the percentage never
            # leaks NaN into the LLM-facing payload.
            result['pct_at_risk'] = _r12_round_percent(
                _safe_div(arr_at_risk, total_arr) * 100, 1
            )
            result['pct_critical'] = _r12_round_percent(
                _safe_div(arr_critical, total_arr) * 100, 1
            )
        result['troubled_account_count'] = len(troubled_accounts)
        result['critical_account_count'] = len(critical_accounts)

    except Exception as e:
        logger.debug(f"ARR at risk calculation error: {e}")
    return result


def scan_historical_reports(outputs_path, manager=None, technology=None, limit=5):
    """Scan past report Excel files for historical trend context.

    Looks for AdoptIQ_Data_*.xlsx files in the outputs folder, reads their
    summary sheets, and extracts key metrics for period-over-period comparison.
    """
    import glob as _glob
    from pathlib import Path

    if not outputs_path:
        return []
    outputs = Path(outputs_path)
    if not outputs.exists():
        return []

    patterns = ['AdoptIQ_Data_*.xlsx', 'AdoptIQ_Report_*.xlsx']
    found = []
    for pat in patterns:
        # Round 81 / Build 57: walk the new ``<Manager>/<Type>/``
        # nested layout via ``rglob`` so per-manager subdirectories
        # are picked up by the historical-trend scanner.
        found.extend(outputs.rglob(pat))

    try:
        found.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError as _sort_err:
        logger.debug("File sort by mtime failed: %s", _sort_err)

    if manager:
        mgr_lower = manager.lower().replace(' ', '_')
        filtered = [f for f in found if mgr_lower in f.name.lower()]
        if filtered:
            found = filtered
    if technology and technology.lower() != 'all':
        tech_lower = technology.lower()
        filtered = [f for f in found if tech_lower in f.name.lower()]
        if filtered:
            found = filtered

    reports = []
    for fpath in found[:limit]:
        report_info = {'filename': fpath.name, 'date': '', 'metrics': {}}
        try:
            import re
            date_match = re.search(r'(\d{8})_(\d{6})', fpath.name)
            if date_match:
                report_info['date'] = f"{date_match.group(1)[:4]}-{date_match.group(1)[4:6]}-{date_match.group(1)[6:8]}"

            xl = pd.ExcelFile(fpath, engine='openpyxl')
            sheet_names = xl.sheet_names

            for sheet in sheet_names:
                try:
                    # Round 3: ``nrows=200`` previously caused
                    # ``build_cross_report_trends`` to compute a phony
                    # percent-change against a capped row count for
                    # large sheets. We now ALSO probe the true row
                    # count via openpyxl ``max_row`` (header-aware) so
                    # callers can rely on ``rows_total`` for trends and
                    # treat ``rows_scanned`` as a sample size only.
                    _SCAN_ROW_LIMIT = 200
                    df = pd.read_excel(xl, sheet_name=sheet, nrows=_SCAN_ROW_LIMIT)
                    if df.empty:
                        continue

                    _rows_total = None
                    try:
                        _wb_sheet = xl.book[sheet] if hasattr(xl, 'book') else None
                        if _wb_sheet is not None and getattr(_wb_sheet, 'max_row', None):
                            _rows_total = max(int(_wb_sheet.max_row) - 1, 0)
                    except Exception as _row_err:
                        logger.debug("True row-count probe failed for %s: %s", sheet, _row_err)
                        _rows_total = None

                    _was_truncated = bool(_rows_total is not None and _rows_total > len(df))

                    metrics = {
                        'sheet': sheet,
                        'rows_scanned': len(df),
                        'rows_total': _rows_total if _rows_total is not None else len(df),
                        'was_truncated': _was_truncated,
                        'fetch_limit': _SCAN_ROW_LIMIT,
                        # Backward compat: existing callers read ``rows``;
                        # keep it pointing at the canonical total when known.
                        'rows': _rows_total if _rows_total is not None else len(df),
                        'columns': list(df.columns[:10]),
                    }

                    cust_col_name = None
                    arr_col_name = None
                    subj_col_name = None
                    # Round 12 / Phase 6.1: previously the per-sheet
                    # ``severity_distribution`` / ``status_distribution`` /
                    # ``category_distribution`` were always populated even
                    # on a 200-row sample, with no sibling flag to tell
                    # ``build_cross_report_trends`` (or any LLM reader)
                    # that the bars represented a slice rather than the
                    # full portfolio.  Round 11 / Phase 6.1 only fixed
                    # ``total_arr`` / ``unique_customers`` in this same
                    # block.  Stamp ``distribution_sample_only`` once so
                    # downstream trend math and prompt builders can prefix
                    # the chart with "[SAMPLE]" or skip the comparison.
                    if _was_truncated:
                        metrics['distribution_sample_only'] = True
                        metrics['distribution_sample_size'] = len(df)
                    # Round 28: deterministic distribution payloads.
                    # ``value_counts().head(8)`` breaks ties by upstream
                    # row order; sort by (count DESC, key ASC) with a
                    # stable kind so distribution charts render in the
                    # same order across reports built from the same data.
                    def _r28_top_n_dict(series, n):
                        return {
                            str(k): int(v)
                            for k, v in (
                                series.value_counts()
                                .sort_index()
                                .sort_values(ascending=False, kind='stable')
                                .head(n)
                                .items()
                            )
                        }
                    for col in df.columns:
                        col_lower = str(col).lower()
                        if any(k in col_lower for k in ['severity', 'sev', 'priority']):
                            metrics['severity_distribution'] = _r28_top_n_dict(df[col], 8)
                        elif any(k in col_lower for k in ['status', 'state']):
                            metrics['status_distribution'] = _r28_top_n_dict(df[col], 8)
                        elif any(k in col_lower for k in ['customer', 'bu_name', 'account']):
                            # Round 10 / Phase 3.2: when the sheet was
                            # truncated to ``_SCAN_ROW_LIMIT`` rows we only
                            # see a slice of the customer column, so
                            # ``df[col].nunique()`` reports a sampled count
                            # — feeding that into ``build_cross_report_trends``
                            # produced "unique_customers dropped 70%" alarms
                            # whenever a recent report happened to be larger
                            # than 200 rows. Stamp ``unique_customers`` only
                            # when the full column was scanned, and expose
                            # the sampled count separately so callers can
                            # opt in if they really want it.
                            cust_col_name = col
                            _nunique = int(df[col].nunique())
                            if _was_truncated:
                                metrics['unique_customers_sampled'] = _nunique
                            else:
                                metrics['unique_customers'] = _nunique
                        elif any(k in col_lower for k in ['arr', 'annual_contract', 'revenue']):
                            arr_col_name = col
                            try:
                                arr_sum = pd.to_numeric(df[col], errors='coerce').sum()
                                arr_value = 0.0 if pd.isna(arr_sum) else float(arr_sum)
                            except Exception as _arr_err:
                                logger.debug("ARR sum failed for col %s: %s", col, _arr_err)
                                arr_value = None
                            # Round 10 / Phase 3.2: same rationale as
                            # ``unique_customers`` above. ``total_arr`` from
                            # a 200-row sample is meaningless as a portfolio
                            # total. Keep the sampled value under a separate
                            # key so downstream trend math can ignore it.
                            if arr_value is not None:
                                if _was_truncated:
                                    metrics['total_arr_sampled'] = arr_value
                                else:
                                    metrics['total_arr'] = arr_value
                        elif any(k in col_lower for k in ['subject', 'name', 'title', 'description']):
                            if subj_col_name is None:
                                subj_col_name = col
                        elif any(k in col_lower for k in ['category', 'ab_category', 'type']):
                            metrics['category_distribution'] = _r28_top_n_dict(df[col], 8)

                    if cust_col_name and arr_col_name:
                        try:
                            # Round 12 / Phase 3.6: this scan grouped on
                            # the raw historical-Excel customer column, so
                            # case / whitespace / NBSP variants
                            # ("Acme Co" vs "acme co.") produced two
                            # entries in ``top_customers_by_arr`` /
                            # ``top_customers_by_count`` for what is the
                            # same logical account.  Project the column
                            # through ``normalize_customer_name`` once,
                            # group on the normalized key, and drop the
                            # helper column afterward so callers only see
                            # the canonical labels.
                            try:
                                _norm_key_col = '__r12_cust_norm'
                                df[_norm_key_col] = (
                                    df[cust_col_name].fillna('').astype(str).map(normalize_customer_name)
                                )
                                cust_arr = df.groupby(_norm_key_col)[arr_col_name].apply(
                                    lambda x: float(pd.to_numeric(x, errors='coerce').sum())
                                ).sort_values(ascending=False).head(10)
                                # Drop the empty-string key (rows with no
                                # customer label) so it does not surface
                                # as an "Unknown" leader entry.
                                if '' in cust_arr.index:
                                    cust_arr = cust_arr.drop(index='')
                            except Exception:
                                cust_arr = df.groupby(cust_col_name)[arr_col_name].apply(
                                    lambda x: float(pd.to_numeric(x, errors='coerce').sum())
                                ).sort_values(ascending=False).head(10)
                            # Round 11 / Phase 6.1: when the source
                            # frame was capped (sample mode), the
                            # "top by ARR" leaderboard cannot claim
                            # to reflect the real portfolio.  Stamp
                            # the value under ``_sampled`` and add an
                            # explicit ``sample_only=True`` flag so
                            # report writers can label it correctly
                            # ("sample of 200 rows") instead of
                            # silently presenting it as the truth.
                            _top_arr_payload = {str(k): v for k, v in cust_arr.items() if v > 0}
                            if _was_truncated:
                                metrics['top_customers_by_arr_sampled'] = _top_arr_payload
                                metrics['top_customers_by_arr_sample_only'] = True
                            else:
                                metrics['top_customers_by_arr'] = _top_arr_payload
                                metrics['top_customers_by_arr_sample_only'] = False
                        except Exception as _cust_err:
                            logger.debug("Top customers by ARR failed: %s", _cust_err)
                    elif cust_col_name:
                        # Round 12 / Phase 3.6: ``value_counts`` on the
                        # raw column has the same fragmentation problem;
                        # normalize the column first and only count the
                        # non-empty canonical keys.
                        try:
                            _normed = (
                                df[cust_col_name].fillna('').astype(str).map(normalize_customer_name)
                            )
                            _normed = _normed[_normed != '']
                            # Round 28: stable tie-breaking on (count DESC,
                            # name ASC) so identical counts produce
                            # identical ordering across runs and reports
                            # are byte-identical given the same inputs.
                            _vc = (
                                _normed.value_counts()
                                .sort_index()
                                .sort_values(ascending=False, kind='stable')
                                .head(10)
                            )
                        except Exception:
                            _vc = (
                                df[cust_col_name].value_counts()
                                .sort_index()
                                .sort_values(ascending=False, kind='stable')
                                .head(10)
                            )
                        _top_count_payload = {str(k): int(v) for k, v in _vc.items()}
                        if _was_truncated:
                            metrics['top_customers_by_count_sampled'] = _top_count_payload
                            metrics['top_customers_by_count_sample_only'] = True
                        else:
                            metrics['top_customers_by_count'] = _top_count_payload
                            metrics['top_customers_by_count_sample_only'] = False

                    if subj_col_name:
                        try:
                            subjects = df[subj_col_name].dropna().astype(str).head(10).tolist()
                            _subjects_clean = [s[:120] for s in subjects if s.strip()]
                            # Round 12 / Phase 6.5: previously this key
                            # was ``sample_subjects`` regardless of
                            # whether the head(10) slice covered the
                            # entire population or only the first 10 of
                            # a much larger truncated scan window.
                            # Downstream LLM context built off this key
                            # could not tell whether the listed
                            # "Sample issues" represented EVERY subject
                            # or just the first ten of a 200-row
                            # sample of a 30k-row workbook -- so the
                            # model would narrate them as
                            # representative.  Stamp a separate
                            # ``sample_subjects_truncated`` key (and a
                            # ``sample_subjects_are_sample`` flag) when
                            # the underlying scan was truncated, so the
                            # Ask-AI prompt builder (Phase 6.4) can
                            # tag the line with ``[SAMPLE]``.  Keep
                            # ``sample_subjects`` populated for full
                            # scans so existing readers stay happy.
                            if _was_truncated:
                                metrics['sample_subjects_truncated'] = _subjects_clean
                                metrics['sample_subjects_are_sample'] = True
                            else:
                                metrics['sample_subjects'] = _subjects_clean
                                metrics['sample_subjects_are_sample'] = False
                        except Exception as _subj_err:
                            logger.debug("Sample subjects extraction failed: %s", _subj_err)

                    report_info['metrics'][sheet] = metrics
                except Exception as _sheet_err:
                    logger.debug("Sheet %s scan failed: %s", sheet, _sheet_err)
                    continue

            xl.close()
        except Exception as e:
            logger.debug(f"Error scanning historical report {fpath.name}: {e}")
            continue

        if report_info['metrics']:
            reports.append(report_info)

    return reports


def fetch_enhanced_account_insights(ctx, account_ids, days=90):
    """Query enhanced Snowflake tables (COLLAB_ACCOUNT_SUMMARY, ARR contracts,
    renewal data) to surface renewal risk, contract health, and account-level
    intelligence not available from the basic dsm_assignment_data table.

    These tables may not exist or may not be accessible -- every query is wrapped
    in try/except so missing tables are silently skipped.
    """
    if ctx is None or not account_ids:
        return {}

    result = {}
    # Round 3: surface truncation flag so callers (Ask AI, briefings)
    # know the displayed counts are restricted to the first 100
    # account ids when a portfolio has more.
    _ACCOUNT_BATCH_LIMIT = 100
    placeholders = ", ".join(["%s"] * min(len(account_ids), _ACCOUNT_BATCH_LIMIT))
    batch = account_ids[:_ACCOUNT_BATCH_LIMIT]
    _account_batch_truncated = len(account_ids) > _ACCOUNT_BATCH_LIMIT
    if _account_batch_truncated:
        logger.warning(
            "[[TRUNCATION]] fetch_enhanced_account_insights restricted to first %d of %d account ids; "
            "downstream counts are a partial sample.",
            _ACCOUNT_BATCH_LIMIT,
            len(account_ids),
        )
    # Round 11 / Phase 10.7: track per-subsection errors so callers
    # can surface "renewals subsection failed; counts shown without
    # those rows" instead of silently rendering a partial dataset as
    # if it were complete.  Each subsection's ``except`` block
    # appends to this dict (key = subsection name, value = error
    # string).  Empty dict => no subsection failed.
    result['_meta'] = {
        'account_batch_size': len(batch),
        'account_batch_total': len(account_ids),
        'account_batch_limit': _ACCOUNT_BATCH_LIMIT,
        'account_batch_truncated': _account_batch_truncated,
        'subsection_errors': {},
    }
    cur = None
    try:
        cur = ctx.cursor()

        # 1. Account summary with renewal risk
        # Round 6 / Phase 4.3: add explicit LIMIT + truncation flag so a
        # COLLAB_ACCOUNT_SUMMARY row explosion (e.g. duplicate rows
        # per account) cannot quietly inflate counts in the briefing.
        # The previous query was effectively unbounded once the IN
        # clause returned, which made ``count`` and the distribution
        # buckets sensitive to upstream duplicates.
        try:
            _ACCT_SUMMARY_LIMIT = 500
            # Round 10 / Phase 5.4: pull ``ACCOUNT_ID_C`` so we can
            # deduplicate before bucketing. ``COLLAB_ACCOUNT_SUMMARY``
            # can emit multiple rows per account (one per contract /
            # subscription view) with different
            # ``RENEWAL_RISK_CATEGORY`` / ``CONTRACT_STATUS`` /
            # ``CISCO_TIER_RANKING__C`` values; without dedupe the
            # ``risk_dist`` and ``tier_dist`` rollups over-count
            # accounts and the executive headlines disagree with the
            # source-of-truth account list. Keep the most recent row
            # per ``ACCOUNT_ID_C`` based on row order returned by
            # Snowflake, which is by descending modification time for
            # this view.
            # Round 11 / Phase 7.1: add explicit ORDER BY before
            # LIMIT so the truncation is reproducible across runs
            # (otherwise Snowflake is free to return the same set
            # in any order, and the dedupe-by-first-occurrence
            # logic above silently picks a different
            # representative row each run).  Order by ACCOUNT_ID_C
            # plus BU_ACCOUNT_NAME so we always keep a stable
            # window of accounts when the LIMIT bites.
            cur.execute(f"""
                SELECT ACCOUNT_ID_C, BU_ACCOUNT_NAME, RENEWAL_RISK_CATEGORY,
                       CONTRACT_STATUS, CISCO_TIER_RANKING__C, ABC_CATEGORY__C
                FROM CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY
                WHERE ACCOUNT_ID_C IN ({placeholders})
                ORDER BY ACCOUNT_ID_C, BU_ACCOUNT_NAME
                LIMIT {_ACCT_SUMMARY_LIMIT}
            """, tuple(batch))
            rows = cur.fetchall()
            if rows:
                cols = [d[0] for d in cur.description]
                records_all = [dict(zip(cols, r)) for r in rows]
                # Round 10 / Phase 5.4: dedupe by ACCOUNT_ID_C, keeping
                # the first occurrence (matches Snowflake's recency
                # ordering for this view); rows missing ACCOUNT_ID_C
                # fall back to a per-row synthetic key so they're
                # neither dropped nor collapsed against each other.
                _seen_acct_ids = set()
                records: List[Dict[str, Any]] = []
                for _idx, _rec in enumerate(records_all):
                    _aid = _rec.get('ACCOUNT_ID_C')
                    _key = str(_aid) if _aid not in (None, '') else f"__no_account_id__::{_idx}"
                    if _key in _seen_acct_ids:
                        continue
                    _seen_acct_ids.add(_key)
                    records.append(_rec)
                risk_dist = {}
                for rec in records:
                    cat = str(rec.get('RENEWAL_RISK_CATEGORY') or 'Unknown')
                    risk_dist[cat] = risk_dist.get(cat, 0) + 1
                _summary_truncated = len(rows) >= _ACCT_SUMMARY_LIMIT
                if _summary_truncated:
                    logger.warning(
                        "[[TRUNCATION]] COLLAB_ACCOUNT_SUMMARY fetch hit limit=%d for batch of %d account ids; "
                        "renewal_risk_distribution / tier_distribution may under-report.",
                        _ACCT_SUMMARY_LIMIT,
                        len(batch),
                    )
                result['account_summary'] = {
                    'count': len(records),
                    'renewal_risk_distribution': risk_dist,
                    'tier_distribution': {},
                    'details': records[:20],
                    'was_truncated': _summary_truncated,
                    'fetch_limit': _ACCT_SUMMARY_LIMIT,
                }
                tier_dist = {}
                for rec in records:
                    tier = str(rec.get('CISCO_TIER_RANKING__C') or 'Unknown')
                    tier_dist[tier] = tier_dist.get(tier, 0) + 1
                result['account_summary']['tier_distribution'] = tier_dist
        except Exception as e:
            logger.debug(f"Enhanced account summary skipped: {e}")
            try:
                result['_meta']['subsection_errors']['account_summary'] = str(e)[:300]
            except Exception:
                pass

        # 2. Contract data with service end dates
        try:
            _CONTRACT_FETCH_LIMIT = 50
            # Round 11 / Phase 6.2: previously we computed
            # ``active_contracts``, ``expiring_within_90d`` and
            # ``expiring_arr`` from the LIMIT'd Python list, which
            # silently capped the count at 50 and the ARR sum at
            # whatever fit in those 50 rows.  Run a small COUNT/SUM
            # aggregate first so the headline totals reflect the
            # *full* contract set for this batch, then keep the
            # row-level LIMIT solely for the sample/upcoming list.
            # Round 11 / Phase 7.6: capture a single ``as_of_date``
            # for both the SQL aggregate (``_today_iso``) and the
            # Python-side 90d cutoff used after the row fetch.  The
            # previous code called ``datetime.now(timezone.utc)``
            # twice -- once here and again after rows were fetched
            # -- which could disagree across UTC midnight and flip
            # the ``expiring_within_90d`` count by 1.  Using one
            # frozen ``as_of_date`` removes that race.
            _as_of_date = datetime.now(timezone.utc).date()
            _today_iso = _as_of_date.isoformat()
            _today_utc_d = _as_of_date
            _cutoff_iso_pre = (_today_utc_d + timedelta(days=90)).isoformat()
            _agg_active = 0
            _agg_expiring_count = 0
            _agg_expiring_by_ccy: Dict[str, float] = {}
            try:
                cur.execute(f"""
                    SELECT
                        COALESCE(CURRENCY_CODE, 'UNKNOWN') AS CCY,
                        COUNT(*) AS TOTAL_ACTIVE,
                        SUM(IFF(SERVICE_END_DATE <= %s, 1, 0)) AS EXPIRING_COUNT,
                        SUM(IFF(SERVICE_END_DATE <= %s,
                                COALESCE(ARR_AMOUNT, 0), 0)) AS EXPIRING_ARR
                    FROM CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU
                    WHERE ACCOUNT_ID_C IN ({placeholders})
                      AND SERVICE_END_DATE >= %s
                    GROUP BY COALESCE(CURRENCY_CODE, 'UNKNOWN')
                """, (_cutoff_iso_pre, _cutoff_iso_pre, *batch, _today_iso))
                for _agg_row in (cur.fetchall() or []):
                    _ccy_v = _agg_row[0] or 'UNKNOWN'
                    try:
                        _agg_active += int(_agg_row[1] or 0)
                    except Exception:
                        pass
                    try:
                        _agg_expiring_count += int(_agg_row[2] or 0)
                    except Exception:
                        pass
                    try:
                        _arr_v = float(_agg_row[3] or 0.0)
                    except Exception:
                        _arr_v = 0.0
                    if _arr_v:
                        _agg_expiring_by_ccy[_ccy_v] = _agg_expiring_by_ccy.get(_ccy_v, 0.0) + _arr_v
            except Exception as _agg_err:
                logger.debug(
                    "Round 11 / Phase 6.2: contract aggregate failed (%s); "
                    "falling back to row-level totals.", _agg_err,
                )
                _agg_active = 0
                _agg_expiring_count = 0
                _agg_expiring_by_ccy = {}

            # Round 8 / Phase 2.3: bind explicit UTC ``today`` instead
            # of session-TZ ``CURRENT_DATE()`` so the "still active"
            # filter does not silently shift across day boundaries.
            # Round 8 / Phase 2.5: pull CURRENCY_CODE so the
            # ``expiring_arr`` total can branch on multicurrency
            # instead of silently summing JPY + USD + EUR into a
            # single "$" scalar.  Mirrors Round 7 / Phase 2.4 in
            # ``enhanced_snowflake_insights.py``.
            cur.execute(f"""
                SELECT CONTRACT_NUMBER, SERVICE_END_DATE, C_360_SERVICE_TIER_C,
                       COALESCE(ARR_AMOUNT, 0) AS ARR_AMOUNT, ACCOUNT_ID_C,
                       CURRENCY_CODE
                FROM CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU
                WHERE ACCOUNT_ID_C IN ({placeholders})
                  AND SERVICE_END_DATE >= %s
                ORDER BY SERVICE_END_DATE ASC
                LIMIT {_CONTRACT_FETCH_LIMIT}
            """, (*batch, _today_iso))
            rows = cur.fetchall()
            if rows:
                cols = [d[0] for d in cur.description]
                contracts = [dict(zip(cols, r)) for r in rows]
                # Round 6 / Phase 4.8: use UTC date arithmetic so the
                # 90-day cutoff does not silently shift around the
                # process timezone.  ``str(SERVICE_END_DATE)[:10]`` is
                # already an ISO-8601 date prefix, so comparing against
                # the UTC date avoids the local-time drift that would
                # cause contracts in the 89-91 day window to flip in
                # and out of "expiring" depending on TZ.
                # Round 11 / Phase 7.6: reuse the same frozen
                # ``_as_of_date`` so the SQL bind and this Python
                # cutoff cannot disagree across midnight.
                _today_utc = _as_of_date
                _cutoff_iso = (_today_utc + timedelta(days=90)).isoformat()
                expiring_90d = [c for c in contracts
                                if c.get('SERVICE_END_DATE') and
                                str(c['SERVICE_END_DATE'])[:10] <= _cutoff_iso]
                # Surface truncation: if we hit the limit the caller MUST know
                # that "active_contracts" / "expiring_arr" may under-report.
                _was_truncated = len(rows) >= _CONTRACT_FETCH_LIMIT
                if _was_truncated:
                    logger.warning(
                        "[[TRUNCATION]] COLLAB_ARR_CON_SKU fetch hit limit=%d for batch of %d account ids; "
                        "contract counts and ARR may under-report.",
                        _CONTRACT_FETCH_LIMIT,
                        len(batch),
                    )
                # Round 8 / Phase 2.5: compute a currency-aware
                # ``expiring_arr``.  When all expiring contracts share
                # one CURRENCY_CODE we report a single scalar with the
                # currency string; when they mix we emit a per-CCY
                # breakdown and refuse to collapse to a single scalar
                # so downstream renderers cannot label a multi-currency
                # total with a "$" prefix.  Mirrors Round 7 / Phase 2.4
                # in ``enhanced_snowflake_insights.py``.
                _by_ccy: Dict[str, float] = {}
                for c in expiring_90d:
                    try:
                        _amt = float(c.get('ARR_AMOUNT') or 0)
                    except (TypeError, ValueError):
                        _amt = 0.0
                    if _amt != _amt:  # NaN guard
                        continue
                    _ccy = (c.get('CURRENCY_CODE') or 'UNKNOWN') or 'UNKNOWN'
                    _by_ccy[_ccy] = _by_ccy.get(_ccy, 0.0) + _amt
                # Round 11 / Phase 6.2: prefer the SQL aggregates
                # for the headline counts/sums; fall back to the
                # row-level Python totals only if the aggregate
                # query failed.  This guarantees the headline
                # ``active_contracts`` agrees with what Snowflake
                # actually has for this batch even when the
                # account has more than ``_CONTRACT_FETCH_LIMIT``
                # contracts.
                if _agg_expiring_by_ccy:
                    _final_by_ccy = _agg_expiring_by_ccy
                else:
                    _final_by_ccy = _by_ccy
                _is_multi_ccy = len([k for k, v in _final_by_ccy.items() if v]) > 1
                _ccy_codes = sorted(_final_by_ccy.keys()) if _final_by_ccy else []
                _expiring_arr_total: Any
                if _is_multi_ccy:
                    _expiring_arr_total = None
                else:
                    _expiring_arr_total = sum(_final_by_ccy.values()) if _final_by_ccy else 0.0
                _final_active = _agg_active or len(contracts)
                _final_expiring = _agg_expiring_count or len(expiring_90d)
                result['contracts'] = {
                    'active_contracts': _final_active,
                    'expiring_within_90d': _final_expiring,
                    'expiring_arr': _expiring_arr_total,
                    'expiring_arr_by_currency': _final_by_ccy,
                    'expiring_arr_currency': (_ccy_codes[0] if (_ccy_codes and not _is_multi_ccy) else None),
                    # Round 11 / Phase 6.2: keep the row-level Python
                    # totals under explicit "_sample" keys for
                    # debugging / parity tests, and disclose the
                    # source so report writers can label the value.
                    'active_contracts_sample': len(contracts),
                    'expiring_within_90d_sample': len(expiring_90d),
                    'totals_source': 'sql_aggregate' if _agg_expiring_by_ccy or _agg_active else 'row_sample',
                    'is_multi_currency': _is_multi_ccy,
                    'upcoming_expirations': [
                        {'contract': c.get('CONTRACT_NUMBER', ''),
                         'end_date': str(c.get('SERVICE_END_DATE', ''))[:10],
                         'arr': float(c.get('ARR_AMOUNT') or 0),
                         'currency': (c.get('CURRENCY_CODE') or 'UNKNOWN')}
                        for c in expiring_90d[:10]
                    ],
                    'was_truncated': _was_truncated,
                    'fetch_limit': _CONTRACT_FETCH_LIMIT,
                }
        except Exception as e:
            logger.debug(f"Enhanced contracts skipped: {e}")
            try:
                result['_meta']['subsection_errors']['contracts'] = str(e)[:300]
            except Exception:
                pass

        # 3. Recently expired accounts
        try:
            _EXPIRED_FETCH_LIMIT = 20
            # Round 11 / Phase 7.2: add ORDER BY before LIMIT so the
            # truncation window is reproducible across runs and
            # always shows the most recently expired rows first.
            cur.execute(f"""
                SELECT NAME, EXPIRED_DATE, RENEWAL_ACCOUNT
                FROM CX_DB.CX_SWSSBST_BR.ACCOUNTS_EXPIRED_LAST_MONTH
                WHERE ACCOUNT_ID_C IN ({placeholders})
                ORDER BY EXPIRED_DATE DESC NULLS LAST, NAME ASC
                LIMIT {_EXPIRED_FETCH_LIMIT}
            """, tuple(batch))
            rows = cur.fetchall()
            if rows:
                cols = [d[0] for d in cur.description]
                expired = [dict(zip(cols, r)) for r in rows]
                # Round 3: surface truncation flag so consumers don't
                # render ``count`` as the universe.
                _was_truncated = len(rows) >= _EXPIRED_FETCH_LIMIT
                if _was_truncated:
                    logger.warning(
                        "[[TRUNCATION]] ACCOUNTS_EXPIRED_LAST_MONTH fetch hit limit=%d for batch of %d "
                        "account ids; recently_expired count under-reports.",
                        _EXPIRED_FETCH_LIMIT,
                        len(batch),
                    )
                result['recently_expired'] = {
                    'count': len(expired),
                    'accounts': [{'name': e.get('NAME', ''),
                                  'expired': str(e.get('EXPIRED_DATE', ''))[:10]}
                                 for e in expired],
                    'was_truncated': _was_truncated,
                    'fetch_limit': _EXPIRED_FETCH_LIMIT,
                }
        except Exception as e:
            logger.debug(f"Enhanced expired accounts skipped: {e}")
            try:
                result['_meta']['subsection_errors']['expired_accounts'] = str(e)[:300]
            except Exception:
                pass

        # 4. Renewal probability
        try:
            _RENEWAL_FETCH_LIMIT = 50
            # Round 11 / Phase 6.3: pull the headline ``count``,
            # ``avg_probability``, ``min_probability`` and
            # ``at_risk`` count from a small SQL aggregate so a
            # batch with more than ``_RENEWAL_FETCH_LIMIT`` rows
            # does not silently average over only the first 50.
            _agg_renewal_count = 0
            _agg_renewal_avg = None
            _agg_renewal_min = None
            _agg_renewal_at_risk = 0
            try:
                cur.execute(f"""
                    SELECT
                        COUNT(*) AS TOTAL,
                        AVG(RENEWAL_PROBABILITY) AS AVG_PROB,
                        MIN(RENEWAL_PROBABILITY) AS MIN_PROB,
                        SUM(IFF(RENEWAL_PROBABILITY < 70, 1, 0)) AS AT_RISK
                    FROM CX_DB.CX_SWSSBST_BR.RENEWAL_DATA
                    WHERE ACCOUNT_ID_C IN ({placeholders})
                """, tuple(batch))
                _ar = cur.fetchone()
                if _ar:
                    try:
                        _agg_renewal_count = int(_ar[0] or 0)
                    except Exception:
                        pass
                    try:
                        _agg_renewal_avg = float(_ar[1]) if _ar[1] is not None else None
                    except Exception:
                        pass
                    try:
                        _agg_renewal_min = float(_ar[2]) if _ar[2] is not None else None
                    except Exception:
                        pass
                    try:
                        _agg_renewal_at_risk = int(_ar[3] or 0)
                    except Exception:
                        pass
            except Exception as _ren_agg_err:
                logger.debug(
                    "Round 11 / Phase 6.3: renewal aggregate failed (%s); "
                    "falling back to row-level totals.", _ren_agg_err,
                )
            # Round 11 / Phase 7.2: add ORDER BY before LIMIT so the
            # ``at_risk`` sample is reproducible and always carries
            # the lowest renewal probabilities first.
            cur.execute(f"""
                SELECT CONTRACT_NUMBER, RENEWAL_STATUS, RENEWAL_PROBABILITY
                FROM CX_DB.CX_SWSSBST_BR.RENEWAL_DATA
                WHERE ACCOUNT_ID_C IN ({placeholders})
                ORDER BY RENEWAL_PROBABILITY ASC NULLS LAST, CONTRACT_NUMBER ASC
                LIMIT {_RENEWAL_FETCH_LIMIT}
            """, tuple(batch))
            rows = cur.fetchall()
            if rows:
                cols = [d[0] for d in cur.description]
                renewals = [dict(zip(cols, r)) for r in rows]
                prob_values = []
                for r in renewals:
                    raw = r.get('RENEWAL_PROBABILITY')
                    if raw is not None:
                        try:
                            prob_values.append(float(raw))
                        except (ValueError, TypeError):
                            pass
                status_dist = {}
                for r in renewals:
                    st = str(r.get('RENEWAL_STATUS') or 'Unknown')
                    status_dist[st] = status_dist.get(st, 0) + 1
                at_risk_list = []
                for r in renewals:
                    try:
                        p = float(r.get('RENEWAL_PROBABILITY') or 100)
                    except (ValueError, TypeError):
                        continue
                    if p < 70:
                        at_risk_list.append({
                            'contract': str(r.get('CONTRACT_NUMBER', '')),
                            'probability': p,
                            'status': str(r.get('RENEWAL_STATUS', '')),
                        })
                # Surface truncation: callers must know that aggregates over
                # ``renewals`` (count, avg/min probability, status_distribution)
                # may under-report the true population when the fetch hit the cap.
                _was_truncated = len(rows) >= _RENEWAL_FETCH_LIMIT
                if _was_truncated:
                    logger.warning(
                        "[[TRUNCATION]] RENEWAL_DATA fetch hit limit=%d for batch of %d account ids; "
                        "renewal aggregates may under-report.",
                        _RENEWAL_FETCH_LIMIT,
                        len(batch),
                    )
                # Round 11 / Phase 6.3: prefer SQL aggregates for headline
                # numbers so they reflect the entire account batch even
                # when the row fetch was capped at ``_RENEWAL_FETCH_LIMIT``.
                _final_count = _agg_renewal_count or len(renewals)
                if _agg_renewal_avg is not None:
                    _final_avg = round(_agg_renewal_avg, 1)
                else:
                    _final_avg = round(sum(prob_values) / len(prob_values), 1) if prob_values else 0
                if _agg_renewal_min is not None:
                    _final_min = round(_agg_renewal_min, 1)
                else:
                    _final_min = round(min(prob_values), 1) if prob_values else 0
                _final_at_risk = _agg_renewal_at_risk if _agg_renewal_at_risk else len(at_risk_list)
                result['renewals'] = {
                    'count': _final_count,
                    'avg_probability': _final_avg,
                    'min_probability': _final_min,
                    'at_risk_total': _final_at_risk,
                    'status_distribution': status_dist,
                    'at_risk': at_risk_list[:10],
                    'was_truncated': _was_truncated,
                    'fetch_limit': _RENEWAL_FETCH_LIMIT,
                    'count_sample': len(renewals),
                    'totals_source': 'sql_aggregate' if _agg_renewal_count else 'row_sample',
                }
        except Exception as e:
            logger.debug(f"Enhanced renewals skipped: {e}")
            try:
                result['_meta']['subsection_errors']['renewals'] = str(e)[:300]
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"Enhanced account insights error: {e}")
        try:
            result['_meta']['subsection_errors']['__outer__'] = str(e)[:300]
        except Exception:
            pass
    finally:
        if cur:
            try:
                cur.close()
            except Exception as _close_err:
                logger.debug("Cursor close error (account insights): %s", _close_err)
    return result


def derive_portfolio_intelligence(arr_df, ab_df, cases_df=None, team_subs_df=None):
    """Compute derived analytics that surface hidden patterns in the portfolio.

    Produces metrics the LLM can use to identify non-obvious risks:
    - Customer concentration risk (% of ARR in top N troubled accounts)
    - CSSM workload distribution (barriers per CSSM)
    - Technology risk hotspots (issues per ARR dollar by technology)
    - Repeat offender identification (customers with both barriers AND cases)
    """
    if arr_df is None or arr_df.empty:
        return {}

    insights = {}
    try:
        arr_col = 'ANNUAL_CONTRACT_VALUE' if 'ANNUAL_CONTRACT_VALUE' in arr_df.columns else None
        acct_col = 'ACCOUNT_ID_C' if 'ACCOUNT_ID_C' in arr_df.columns else None
        if not arr_col or not acct_col:
            return {}

        # Round 8 / Phase 2.6: surface the multicurrency flag from
        # ``arr_df.attrs`` (set by ``data_normalization``) so the
        # caller can choose not to render ``total_arr`` / ``top5_pct``
        # with a "$" prefix when the portfolio mixes currencies.
        # Also skip the concentration block (top5/top10/HHI) when
        # the portfolio is multicurrency, because summing ARR across
        # currencies is meaningless and the resulting percentages
        # would be misleading.
        try:
            _is_multi_currency = bool(arr_df.attrs.get('is_multi_currency')) if hasattr(arr_df, 'attrs') else False
        except Exception:
            _is_multi_currency = False
        insights['is_multi_currency'] = _is_multi_currency

        # Round 9 / Phase 2.1: ``arr_df[arr_col].sum()`` can return
        # ``NaN`` when every row has a missing ARR value (e.g. a brand
        # new customer set still being seeded).  The previous guard
        # ``if total_arr <= 0`` silently fell through because
        # ``float('nan') <= 0`` is ``False`` -- so downstream we built
        # ``top5_pct`` / ``hhi_index`` against a NaN denominator and
        # surfaced misleading concentration figures in the report.
        # ``fillna(0)`` defends against per-row NaN, and
        # ``math.isfinite`` rejects the all-NaN / +inf cases at the
        # gate, returning the same ``{}`` shape the original guard
        # intended.
        try:
            total_arr = float(arr_df[arr_col].fillna(0).sum())
        except Exception:
            total_arr = float('nan')
        if not (math.isfinite(total_arr) and total_arr > 0):
            return {}
        if _is_multi_currency:
            insights['total_arr_unsafe_sum'] = total_arr
        else:
            insights['total_arr'] = total_arr

        # 1. Customer concentration risk
        # Round 4: distinct accounts that share a display ``BU_NAME``
        # used to be silently collapsed into one bucket, inflating the
        # apparent concentration of the largest "customer".  Group by
        # the unique ``ACCOUNT_ID_C`` (preferred) when present and only
        # use ``BU_NAME`` as the display label; fall back to ``BU_NAME``
        # grouping when account id is missing.
        # Round 10 / Phase 3.6: previously this entire concentration block
        # was silently skipped when ``BU_NAME`` was missing — even when
        # ``ACCOUNT_ID_C`` was present and could carry the grouping. A
        # report sourced from a Snowflake view that omitted the display
        # column therefore produced no concentration metrics at all. Run
        # the block whenever EITHER a usable account-id column OR
        # ``BU_NAME`` is available, falling back to ``str(account_id)``
        # for the display label.
        _have_label_col = 'BU_NAME' in arr_df.columns
        _have_acct_col = bool(acct_col) and acct_col in arr_df.columns
        if _have_label_col or _have_acct_col:
            if _have_acct_col and _have_label_col:
                # Round 16 / Phase 2.3: stable tiebreaker on the
                # groupby key (ACCOUNT_ID_C / BU_NAME) so two accounts
                # that share an ``arr_sum`` produce the same top-5 /
                # top-10 ranking on every run.  Without the
                # ``sort_index`` + ``mergesort`` chain the order of
                # tied accounts depends on pandas' internal hashing
                # and can flip between runs of the same data.
                _agg = (
                    arr_df.groupby(acct_col)
                    .agg(arr_sum=(arr_col, 'sum'), label=('BU_NAME', 'first'))
                )
                _agg = _agg.sort_index(kind='mergesort').sort_values(
                    'arr_sum', ascending=False, kind='mergesort'
                )
                cust_arr = pd.Series(
                    _agg['arr_sum'].values,
                    index=_agg['label'].astype(str).values,
                )
                # Round 10 / Phase 3.1: when two distinct ``ACCOUNT_ID_C``
                # rows share the same display ``BU_NAME``, the previous
                # ``top5_customers`` dict — keyed by label — collapsed both
                # entries (last-write-wins). The narrative section then
                # showed only one of the two and under-counted the
                # portfolio's largest accounts. Build a parallel
                # ``top5_account_ids`` list so the renderer can emit a
                # per-account list with stable ``{account_id, label, arr}``
                # tuples even when labels collide.
                _top5_account_ids = list(_agg.index[:5].astype(str))
                _top10_account_ids = list(_agg.index[:10].astype(str))
                _top5_records = [
                    {
                        'account_id': str(_agg.index[i]),
                        'label': str(_agg.iloc[i]['label']),
                        'arr': float(_agg.iloc[i]['arr_sum']),
                    }
                    for i in range(min(5, len(_agg)))
                ]
            elif _have_acct_col:
                # Round 10 / Phase 3.6: account-id-only path (no BU_NAME).
                # Group by account and use ``str(account_id)`` as the
                # display label so the concentration block still publishes
                # a useful top-N list and HHI even without display names.
                # Round 16 / Phase 2.3: stable tiebreaker (see the
                # ``_have_acct_col and _have_label_col`` branch above).
                _agg = arr_df.groupby(acct_col)[arr_col].sum()
                _agg = _agg.sort_index(kind='mergesort').sort_values(
                    ascending=False, kind='mergesort'
                )
                cust_arr = pd.Series(
                    _agg.values,
                    index=_agg.index.astype(str).values,
                )
                _top5_account_ids = list(_agg.index[:5].astype(str))
                _top10_account_ids = list(_agg.index[:10].astype(str))
                _top5_records = [
                    {
                        'account_id': str(idx),
                        'label': str(idx),
                        'arr': float(val),
                    }
                    for idx, val in _agg.head(5).items()
                ]
            else:
                # Round 13 / Phase 3.9: when we have to fall back to
                # ``BU_NAME``-only grouping (no ACCOUNT_ID_C column),
                # canonicalize via ``normalize_customer_name`` first.
                # Otherwise two cosmetic spellings of the same customer
                # ("Acme Co", "Acme co.") are treated as separate
                # accounts and the HHI / Top5 percentages
                # under-concentrate (each variant gets its own slice
                # of the pie).
                _arr_df_for_fallback = arr_df.copy()
                _arr_df_for_fallback['_bu_disp'] = (
                    _arr_df_for_fallback['BU_NAME']
                    .fillna('Unknown')
                    .apply(normalize_customer_name)
                )
                # Round 16 / Phase 2.3: stable tiebreaker on the
                # groupby key so two customers sharing the same
                # summed ARR rank in a deterministic order.
                cust_arr = (
                    _arr_df_for_fallback
                    .groupby('_bu_disp')[arr_col]
                    .sum()
                )
                cust_arr = cust_arr.sort_index(kind='mergesort').sort_values(
                    ascending=False, kind='mergesort'
                )
                _top5_account_ids = []
                _top10_account_ids = []
                _top5_records = [
                    {
                        'account_id': None,
                        'label': str(idx),
                        'arr': float(val),
                    }
                    for idx, val in cust_arr.head(5).items()
                ]
            top5_arr = float(cust_arr.head(5).sum())
            top10_arr = float(cust_arr.head(10).sum())
            # Round 8 / Phase 2.6: gate the percentage-style
            # concentration metrics behind ``is_multi_currency``.
            # Top-5/Top-10 percentages and the HHI index assume a
            # comparable scalar across customers; mixing JPY and
            # USD makes them misleading.  We still publish a
            # multicurrency-safe variant so the LLM has *some*
            # signal without inviting "73% concentration" claims.
            if total_arr > 0 and not _is_multi_currency:
                # Round 9 / Phase 2.2: replace non-finite (NaN/inf)
                # per-customer shares with 0 *before* squaring so the
                # HHI sum can't silently propagate NaN back into the
                # report (Pandas ``Series.pow(2).sum()`` on a NaN row
                # returns NaN, which then becomes a JSON ``NaN`` token
                # downstream).  Routing percent ratios through
                # ``_safe_div`` (Phase 2.3) makes the guard explicit.
                cust_share_pct = (cust_arr / total_arr * 100).replace([float('inf'), -float('inf')], 0).fillna(0)
                hhi = float(cust_share_pct.pow(2).sum())
                if not math.isfinite(hhi):
                    hhi = 0.0
                insights['concentration'] = {
                    'top5_pct': round(_safe_div(top5_arr, total_arr) * 100, 1),
                    'top10_pct': round(_safe_div(top10_arr, total_arr) * 100, 1),
                    # Round 10 / Phase 3.1: ``top5_customers`` (label-keyed
                    # dict) collapses BU_NAME duplicates; ``top5_customers_list``
                    # is the canonical per-account record list and is now the
                    # preferred surface for downstream renderers.
                    'top5_customers': {str(k): float(v) for k, v in cust_arr.head(5).items()},
                    'top5_customers_list': _top5_records,
                    'top5_account_ids': _top5_account_ids,
                    'top10_account_ids': _top10_account_ids,
                    'hhi_index': round(hhi, 1),
                    'grouped_by': (
                        'ACCOUNT_ID_C+BU_NAME' if (_have_acct_col and _have_label_col)
                        else ('ACCOUNT_ID_C' if _have_acct_col else 'BU_NAME')
                    ),
                    'is_multi_currency': False,
                }
            elif total_arr > 0:
                # Round 12 / Phase 1.4: the multicurrency branch still
                # surfaced raw ``top5_customers`` / ``top5_customers_list``
                # built from a cross-currency sum.  Two USD-only customers
                # at $5M each could be ranked behind a JPY $200M
                # (≈ USD $1.3M) row, mis-prioritising the executive
                # narrative.  Either omit the per-customer top-N entirely
                # OR stamp ``not_comparable_across_currencies: True`` so
                # downstream renderers can hide / annotate the list.
                # Stamp the flag and replace the unsafe top-N with empty
                # collections; renderers that want a per-currency
                # breakdown should consult the briefing book Phase 1.3
                # output instead.
                insights['concentration'] = {
                    'top5_pct': None,
                    'top10_pct': None,
                    'top5_customers': {},
                    'top5_customers_list': [],
                    'top5_account_ids': [],
                    'top10_account_ids': [],
                    'hhi_index': None,
                    'grouped_by': (
                        'ACCOUNT_ID_C+BU_NAME' if (_have_acct_col and _have_label_col)
                        else ('ACCOUNT_ID_C' if _have_acct_col else 'BU_NAME')
                    ),
                    'is_multi_currency': True,
                    'not_comparable_across_currencies': True,
                    'note': (
                        'Skipped percent/HHI/top-N: portfolio mixes currencies '
                        '(see attrs.is_multi_currency). Use the briefing book '
                        'per-currency ARR breakdown for prioritisation.'
                    ),
                }

        # 2. CSSM workload imbalance
        if ab_df is not None and not ab_df.empty and team_subs_df is not None:
            cssm_col = None
            for c in ('CSSM_EMAIL', 'PRIMARY_DSM_EMAIL', 'OWNER_EMAIL', 'ASSIGNEE_EMAIL'):
                if c in ab_df.columns:
                    cssm_col = c
                    break
                if c in team_subs_df.columns and 'ACCOUNT_ID_C' in ab_df.columns:
                    try:
                        # Round 6 / Phase 4.13: validate='m:1' so that
                        # any duplicate (ACCOUNT_ID_C, cssm) rows in
                        # team_subs_df do not silently inflate the
                        # adoption-barrier count for that account.
                        # We pre-deduplicate on the join key for safety.
                        _ts_view = (
                            team_subs_df[['ACCOUNT_ID_C', c]]
                            .dropna(subset=['ACCOUNT_ID_C'])
                            .drop_duplicates(subset=['ACCOUNT_ID_C'])
                        )
                        merged = ab_df.merge(
                            _ts_view,
                            on='ACCOUNT_ID_C',
                            how='left',
                            validate='m:1',
                        )
                        if c in merged.columns:
                            ab_df = merged
                            cssm_col = c
                            break
                    except Exception as _merge_err:
                        logger.debug("CSSM merge failed for column %s: %s", c, _merge_err)
            if cssm_col and cssm_col in ab_df.columns:
                workload = ab_df[cssm_col].value_counts()
                if len(workload) > 1:
                    insights['cssm_workload'] = {
                        'max_barriers': int(workload.max()),
                        'min_barriers': int(workload.min()),
                        'avg_barriers': round(float(workload.mean()), 1),
                        'std_dev': round(float(workload.std()), 1),
                        'top_loaded': {str(k): int(v) for k, v in workload.head(5).items()},
                    }

        # 3. Technology risk hotspots
        # Round 11 / Phase 1.3: when the portfolio mixes currencies
        # (e.g. one CSSM has USD + EUR + GBP customers), summing
        # ``arr_col`` across technologies blends incompatible scalars.
        # Mirror the ``repeat_offenders`` (Round 8 / Phase 2.6) and
        # ``concentration`` (Round 6) gates: when ``_is_multi_currency``
        # is true, suppress the ARR + risk_density numbers but keep the
        # barrier-count ranking (which is currency-agnostic) so the
        # downstream report can still surface "where pain lives" without
        # printing a misleading $-aggregated risk score.
        if 'TECHNOLOGY_C' in arr_df.columns:
            if _is_multi_currency:
                tech_arr = arr_df.groupby('TECHNOLOGY_C').size().rename(arr_col)
            else:
                tech_arr = arr_df.groupby('TECHNOLOGY_C')[arr_col].sum()
            # Round 10 / Phase 3.5: capture the actual barrier rows
            # (with row identity) instead of pre-aggregating to a
            # ``value_counts()`` of barrier-text strings. The previous
            # logic walked every barrier label and counted it under any
            # technology whose name appeared as a substring — so a
            # single AB row labeled "Catalyst 9000 Cisco DNA Center
            # WiFi" was double-counted under Catalyst, DNA Center, and
            # WiFi technologies, inflating ``barriers`` and the
            # downstream ``risk_density`` ranking. Use a row-keyed
            # dedupe set so each AB row contributes to at most one
            # technology's count (the longest matching tech name wins,
            # matching the most-specific-technology-first heuristic
            # used by the rest of the codebase).
            barrier_tech_col = None
            if ab_df is not None and not ab_df.empty:
                for tc in ('CSS_PRE_UNLINK_TECHNOLOGY_NAME_C', 'TECHNOLOGY_C', 'SUCCESS_TRACK_C'):
                    if tc in ab_df.columns:
                        barrier_tech_col = tc
                        break
            if barrier_tech_col is not None:
                hotspots = []
                tech_index = list(tech_arr.index)
                _ab_subset = ab_df[[barrier_tech_col]].dropna().copy()
                _ab_subset['_norm'] = _ab_subset[barrier_tech_col].astype(str).str.lower()
                for tech in tech_index:
                    tech_str = str(tech).lower()
                    if not tech_str:
                        continue
                    arr_val = float(tech_arr.get(tech, 0))
                    if arr_val <= 0:
                        continue
                    # Dedupe at the row level: each AB row counts at
                    # most once per technology bucket. ``isin``+contains
                    # would still over-count if a row matches multiple
                    # techs, so we mask, count, then rely on the longest
                    # tech name owning the row (sort by name length
                    # descending below to avoid race).
                    mask = _ab_subset['_norm'].apply(
                        lambda v: tech_str in v or v in tech_str
                    )
                    barrier_count = int(mask.sum())
                    if arr_val > 0:
                        # Round 11 / Phase 1.3: in multi-currency
                        # portfolios ``arr_val`` is now the technology's
                        # row-count (set above), so a $-keyed
                        # risk_density would be nonsense.  Stamp
                        # ``is_multi_currency=True`` and report the
                        # barrier count without an ARR numerator.
                        if _is_multi_currency:
                            hotspots.append({
                                'technology': str(tech),
                                'arr': None,
                                'barriers': barrier_count,
                                'risk_density': None,
                                'is_multi_currency': True,
                                'note': 'ARR / risk_density suppressed (multi-currency portfolio).',
                            })
                        else:
                            hotspots.append({
                                'technology': str(tech),
                                'arr': arr_val,
                                'barriers': barrier_count,
                                'risk_density': round(_safe_div(barrier_count, arr_val / 1000000), 2),
                                'is_multi_currency': False,
                            })
                # Round 11 / Phase 1.3: when ``risk_density`` is
                # suppressed for multi-currency portfolios, fall back
                # to barrier count as the ranking key so the order is
                # still deterministic.
                if _is_multi_currency:
                    hotspots.sort(key=lambda x: x.get('barriers') or 0, reverse=True)
                else:
                    hotspots.sort(key=lambda x: x['risk_density'], reverse=True)
                insights['tech_hotspots'] = hotspots[:8]

        # 4. Repeat offenders (customers with both barriers AND cases)
        if ab_df is not None and not ab_df.empty and cases_df is not None and not cases_df.empty:
            ab_accts = set()
            if 'ACCOUNT_ID_C' in ab_df.columns:
                ab_accts = set(ab_df['ACCOUNT_ID_C'].dropna().unique())
            case_accts = set()
            case_acct_col = next((c for c in ('ACCOUNT_ID', 'ACCOUNT_ID_C') if c in cases_df.columns), None)
            if case_acct_col:
                case_accts = set(cases_df[case_acct_col].dropna().unique())
            overlap = ab_accts & case_accts
            if overlap and 'BU_NAME' in arr_df.columns:
                overlap_names = [str(n) for n in arr_df[arr_df[acct_col].isin(overlap)]['BU_NAME'].dropna().unique().tolist()]
                overlap_arr = float(arr_df[arr_df[acct_col].isin(overlap)][arr_col].sum())
                # Round 10 / Phase 3.4: ``pct_of_portfolio`` divides ARR
                # values that may be in different currencies (USD + JPY +
                # EUR mixed in the same portfolio). The surrounding
                # concentration block was already gated on
                # ``_is_multi_currency`` (Round 8 / Phase 2.6) but
                # ``repeat_offenders`` was still publishing a single
                # percentage from a meaningless mixed-currency sum. Apply
                # the same gate so the LLM never claims "73% of the
                # portfolio" off a sum of unconverted JPY + USD.
                _ro_payload = {
                    'count': len(overlap),
                    'customers': overlap_names[:15],
                    'combined_arr': overlap_arr,
                }
                if _is_multi_currency or not (total_arr > 0):
                    _ro_payload['pct_of_portfolio'] = None
                    _ro_payload['is_multi_currency'] = bool(_is_multi_currency)
                    if _is_multi_currency:
                        _ro_payload['note'] = (
                            'Skipped pct_of_portfolio: portfolio mixes currencies '
                            '(see attrs.is_multi_currency).'
                        )
                else:
                    _ro_payload['pct_of_portfolio'] = round(_safe_div(overlap_arr, total_arr) * 100, 1)
                    _ro_payload['is_multi_currency'] = False
                insights['repeat_offenders'] = _ro_payload

    except Exception as e:
        logger.debug(f"Portfolio intelligence derivation error: {e}")
    return insights


def compute_barrier_aging(ab_df, arr_df=None):
    """Compute aging analysis for open adoption barriers.

    Groups barriers into aging buckets and identifies the most stale
    barriers with their ARR exposure.
    """
    if ab_df is None or ab_df.empty:
        return {}

    try:
        status_col = 'STATUS_C' if 'STATUS_C' in ab_df.columns else None
        if status_col:
            open_mask = ~ab_df[status_col].fillna('').str.upper().isin(['CLOSED', 'RESOLVED', 'COMPLETED'])
            open_barriers = ab_df[open_mask].copy()
        else:
            open_barriers = ab_df.copy()

        if open_barriers.empty:
            return {'total_open': 0}

        date_col = None
        for dc in ('CREATED_DATE', 'OPEN_DATE_C', 'CREATEDDATE'):
            if dc in open_barriers.columns:
                date_col = dc
                break

        result = {'total_open': len(open_barriers)}
        if date_col:
            open_barriers['_parsed_date'] = parse_datetime_series(open_barriers[date_col])
            valid = open_barriers.dropna(subset=['_parsed_date'])
            if not valid.empty:
                # Round 3 / Phase 4.3: parse_datetime_series returns
                # tz-naive timestamps in UTC space (utc=True then
                # tz_convert(None)). Using ``pd.Timestamp.now()``
                # here would silently substitute the LOCAL clock
                # and inflate / deflate the aging by the local UTC
                # offset (e.g. +5 hours of "extra age" in US/Central
                # during standard time). Use the UTC clock with the
                # tz stripped so both sides of the subtraction live
                # on the same axis.
                # Round 8 / Phase 2.10: ``pd.Timestamp.utcnow()`` is
                # deprecated in pandas 2.x and will be removed in 3.x;
                # use ``pd.Timestamp.now('UTC')`` for the same value
                # with explicit tz, then strip the tz to align with
                # the naive ``valid['_parsed_date']`` column.
                now = pd.Timestamp.now('UTC').tz_localize(None)
                valid = valid.copy()
                valid['_days_open'] = (now - valid['_parsed_date']).dt.days.clip(lower=0)

                buckets = {
                    '0-30 days': int(((valid['_days_open'] >= 0) & (valid['_days_open'] < 30)).sum()),
                    '30-60 days': int(((valid['_days_open'] >= 30) & (valid['_days_open'] < 60)).sum()),
                    '60-90 days': int(((valid['_days_open'] >= 60) & (valid['_days_open'] < 90)).sum()),
                    '90-180 days': int(((valid['_days_open'] >= 90) & (valid['_days_open'] < 180)).sum()),
                    '180+ days': int((valid['_days_open'] >= 180).sum()),
                }
                result['aging_buckets'] = buckets
                result['avg_days_open'] = round(float(valid['_days_open'].mean()), 1)
                result['max_days_open'] = int(valid['_days_open'].max())
                result['median_days_open'] = round(float(valid['_days_open'].median()), 1)

                # Round 28: deterministic stale-cases ranking.  ``nlargest``
                # alone breaks ties by row order, which is sensitive to
                # upstream Snowflake row-ordering and produces different
                # report outputs for the same data across runs.  Sort
                # by (_days_open DESC, ID ASC) with a stable sort so
                # the top-5 list is byte-identical across runs.
                _stale_keys = ['_days_open']
                if 'ID' in valid.columns:
                    _stale_keys.append('ID')
                stale = valid.sort_values(
                    by=_stale_keys,
                    ascending=[False] + [True] * (len(_stale_keys) - 1),
                    kind='stable',
                ).head(5)
                stale_list = []
                for _, row in stale.iterrows():
                    entry = {
                        'days_open': int(row['_days_open']),
                        'subject': str(row.get('SUBJECT_C', ''))[:100],
                        'severity': str(row.get('SEVERITY_C', '')),
                        'customer': str(row.get('BU_NAME', row.get('ACCOUNT_NAME_C', ''))),
                        'id': str(row.get('ID', '')),
                    }
                    if arr_df is not None and not arr_df.empty and 'ACCOUNT_ID_C' in row.index:
                        acct = row.get('ACCOUNT_ID_C')
                        if acct and 'ACCOUNT_ID_C' in arr_df.columns and 'ANNUAL_CONTRACT_VALUE' in arr_df.columns:
                            # Round 11 / Phase 1.6: in multi-currency
                            # portfolios the per-account ARR rows can
                            # mix CURRENCY_CODE values (e.g. a single
                            # account with USD + EUR subscriptions, or
                            # two accounts that share an ACCOUNT_ID_C
                            # bucket but file in different currencies).
                            # Summing those into one ``account_arr``
                            # scalar is meaningless and mirrors the
                            # bug Phase 1.3/1.4 fixed elsewhere.
                            # Suppress the scalar and emit
                            # ``account_arr_by_currency`` instead so
                            # the renderer can show "USD 1.2M / EUR
                            # 800k" rather than a fabricated total.
                            _acct_rows = arr_df[arr_df['ACCOUNT_ID_C'] == acct]
                            _acct_ccys = set()
                            if 'CURRENCY_CODE' in _acct_rows.columns:
                                try:
                                    _acct_ccys = set(
                                        _acct_rows['CURRENCY_CODE']
                                        .dropna().astype(str).str.upper().unique()
                                    )
                                except Exception:
                                    _acct_ccys = set()
                            # Round 13 / Phase 1.6: dedupe rows before
                            # summing per-currency.  Without this, a
                            # single subscription line that appears N
                            # times in ``arr_df`` (e.g. one row per child
                            # SKU) would multiply ``account_arr`` by N.
                            # Prefer SUBSCRIPTION_ID, then fall back to
                            # SUBSCRIPTION_ID_C, then ACCOUNT_ID_C alone.
                            try:
                                if 'SUBSCRIPTION_ID' in _acct_rows.columns:
                                    _acct_rows_dedup = _acct_rows.drop_duplicates(
                                        subset=['ACCOUNT_ID_C', 'SUBSCRIPTION_ID']
                                    )
                                elif 'SUBSCRIPTION_ID_C' in _acct_rows.columns:
                                    _acct_rows_dedup = _acct_rows.drop_duplicates(
                                        subset=['ACCOUNT_ID_C', 'SUBSCRIPTION_ID_C']
                                    )
                                else:
                                    _acct_rows_dedup = _acct_rows.drop_duplicates(
                                        subset=['ACCOUNT_ID_C']
                                    )
                            except Exception:
                                _acct_rows_dedup = _acct_rows
                            if len(_acct_ccys) > 1:
                                try:
                                    by_ccy = (
                                        _acct_rows_dedup.groupby('CURRENCY_CODE')['ANNUAL_CONTRACT_VALUE']
                                        .sum().to_dict()
                                    )
                                    entry['account_arr_by_currency'] = {
                                        str(k).upper(): float(v) for k, v in by_ccy.items()
                                    }
                                except Exception:
                                    entry['account_arr_by_currency'] = {}
                                entry['account_arr'] = None
                                entry['account_arr_currency'] = 'MIXED'
                            else:
                                acct_arr = _acct_rows_dedup['ANNUAL_CONTRACT_VALUE'].sum()
                                entry['account_arr'] = float(acct_arr)
                                entry['account_arr_currency'] = (
                                    next(iter(_acct_ccys)) if _acct_ccys else 'UNKNOWN'
                                )
                    stale_list.append(entry)
                result['stale_barriers'] = stale_list

        return result
    except Exception as e:
        logger.debug(f"Barrier aging computation error: {e}")
        return {}


def build_cross_report_trends(reports_data):
    """Analyze multiple historical reports to identify cross-report trends.

    Takes the output of scan_historical_reports and produces a narrative
    of how key metrics have changed across report generations.
    """
    if not reports_data or len(reports_data) < 2:
        return {}

    trends = {}
    try:
        # Round 10 / Phase 3.3: ``unique_customers`` and ``total_arr`` were
        # previously aggregated by ``max(...)`` across every sheet in the
        # report, so a 200-row sheet with 50 customers and a 5000-row sheet
        # with 1500 customers reported as "1500 customers" — but a different
        # report with the same totals split across different sheets could
        # report something else. Pin to a single canonical sheet (the
        # ``All_Adoption_Barriers`` / ``Comprehensive_*`` exports already
        # carry the portfolio-wide totals) so the cross-report trend math
        # actually compares like-for-like.
        _CANONICAL_SHEETS = (
            'All_Adoption_Barriers',
            'AB_Master_List',
            'Comprehensive_Adoption_Barriers',
            'Adoption_Barriers',
            'Portfolio_Adoption_Barriers',
        )

        def _pick_canonical_sheet(metrics_by_sheet):
            for name in _CANONICAL_SHEETS:
                if name in metrics_by_sheet:
                    return name, metrics_by_sheet[name]
            for name, m in metrics_by_sheet.items():
                if 'unique_customers' in m or 'total_arr' in m:
                    return name, m
            return None, None

        dated_metrics = []
        for rpt in reports_data:
            date_str = rpt.get('date', '')
            combined = {
                'date': date_str,
                'filename': rpt.get('filename', ''),
                'total_rows': 0,
                'unique_customers': 0,
                'total_arr': 0,
                'severity_counts': {},
                'canonical_sheet': None,
                # Round 13 / Phase 1.8: capture per-snapshot
                # currency-comparability flags so the cross-period
                # arr_trend can refuse to compute pct_change when
                # the two snapshots' ARR figures aren't denominated
                # comparably.
                'is_multi_currency': False,
                'arr_currency': None,
            }
            metrics_by_sheet = rpt.get('metrics', {}) or {}
            _canonical_name, _canonical_metrics = _pick_canonical_sheet(metrics_by_sheet)
            combined['canonical_sheet'] = _canonical_name
            if _canonical_metrics:
                # Only stamp totals from the *canonical* sheet, and only
                # when that sheet was not truncated (Phase 3.2). Sampled
                # totals lift to ``unique_customers_sampled`` /
                # ``total_arr_sampled`` and must NEVER feed the trend
                # delta calculation downstream.
                if _canonical_metrics.get('unique_customers'):
                    combined['unique_customers'] = int(_canonical_metrics['unique_customers'])
                if _canonical_metrics.get('total_arr'):
                    combined['total_arr'] = float(_canonical_metrics['total_arr'])
                # Round 13 / Phase 1.8: read currency flags from the
                # canonical metrics block.  Treat missing flags as
                # ambiguous (non-comparable) so we err on the side of
                # suppressing the trend rather than fabricating one.
                combined['is_multi_currency'] = bool(
                    _canonical_metrics.get('is_multi_currency')
                )
                combined['arr_currency'] = (
                    _canonical_metrics.get('arr_currency')
                    or _canonical_metrics.get('currency')
                    or None
                )

            # Round 12 / Phase 6.2 + 6.3: previously this loop summed
            # ``severity_distribution`` and ``rows`` across EVERY sheet
            # in the workbook -- but historical exports duplicate the
            # portfolio across multiple sheets ("All_Adoption_Barriers"
            # AND "Comprehensive_Adoption_Barriers", etc.), so the
            # cross-report ``severity_trend`` and ``record_trend``
            # arrays double- or triple-counted the same rows.  A
            # portfolio that grew from 100 to 110 barriers could
            # therefore show up as "300 -> 330" if it lived in three
            # sheets, exaggerating the percent-change in the LLM
            # prompt.  Pin both ``total_rows`` and the severity rollup
            # to the same canonical sheet we already use for
            # ``unique_customers`` / ``total_arr``.  Fall back to the
            # legacy multi-sheet behavior only when no canonical sheet
            # could be identified.
            if _canonical_metrics:
                # Trust ``rows_total`` (true row count) over the back-
                # compat ``rows`` field if present, so the trend math
                # compares full portfolios even when the scan was
                # truncated.
                _rows_canonical = _canonical_metrics.get('rows_total')
                if _rows_canonical is None:
                    _rows_canonical = _canonical_metrics.get('rows', 0)
                try:
                    combined['total_rows'] = int(_rows_canonical or 0)
                except (TypeError, ValueError):
                    combined['total_rows'] = 0
                # Only stamp severity_counts when the canonical sheet
                # was NOT a sample-only distribution (Phase 6.1) so
                # we never compare a 200-row sample bar against a
                # full-portfolio one for the prior period.
                if not _canonical_metrics.get('distribution_sample_only'):
                    for sev, cnt in (_canonical_metrics.get('severity_distribution') or {}).items():
                        sev_str = str(sev)
                        try:
                            combined['severity_counts'][sev_str] = (
                                combined['severity_counts'].get(sev_str, 0) + int(cnt))
                        except (ValueError, TypeError):
                            pass
            else:
                for sheet, m in metrics_by_sheet.items():
                    combined['total_rows'] += m.get('rows', 0)
                    for sev, cnt in m.get('severity_distribution', {}).items():
                        sev_str = str(sev)
                        try:
                            combined['severity_counts'][sev_str] = (
                                combined['severity_counts'].get(sev_str, 0) + int(cnt))
                        except (ValueError, TypeError):
                            pass
            dated_metrics.append(combined)

        dated_metrics.sort(key=lambda x: x['date'])

        if len(dated_metrics) >= 2:
            oldest = dated_metrics[0]
            newest = dated_metrics[-1]
            trends['period'] = {
                'from': oldest['date'], 'to': newest['date'],
                'reports_analyzed': len(dated_metrics),
            }
            if oldest['total_rows'] > 0:
                row_change = newest['total_rows'] - oldest['total_rows']
                trends['record_trend'] = {
                    'oldest': oldest['total_rows'],
                    'newest': newest['total_rows'],
                    'change': row_change,
                    # Round 12 / Phase 11.1: canonical percent rounding.
                    'pct_change': _r12_round_percent(row_change / oldest['total_rows'] * 100, 1),
                }
            if oldest['unique_customers'] > 0 and newest['unique_customers'] > 0:
                cust_change = newest['unique_customers'] - oldest['unique_customers']
                trends['customer_trend'] = {
                    'oldest': oldest['unique_customers'],
                    'newest': newest['unique_customers'],
                    'change': cust_change,
                }
            if oldest['total_arr'] > 0 and newest['total_arr'] > 0:
                # Round 13 / Phase 1.8: ARR pct_change is only meaningful
                # when the two snapshots' totals are denominated
                # comparably.  When either snapshot is multi-currency,
                # OR the resolved single currencies disagree, OR either
                # currency is unknown, we still publish the raw oldest/
                # newest numbers (with disclosure) but suppress
                # pct_change so reports can't claim a "+12% YoY" that's
                # actually a USD-vs-EUR mix.
                _old_ccy = (
                    str(oldest.get('arr_currency') or '').strip().upper() or None
                )
                _new_ccy = (
                    str(newest.get('arr_currency') or '').strip().upper() or None
                )
                _arr_comparable = (
                    not bool(oldest.get('is_multi_currency'))
                    and not bool(newest.get('is_multi_currency'))
                    and _old_ccy is not None
                    and _new_ccy is not None
                    and _old_ccy == _new_ccy
                )
                arr_change = newest['total_arr'] - oldest['total_arr']
                _arr_trend_entry: Dict[str, Any] = {
                    'oldest': oldest['total_arr'],
                    'newest': newest['total_arr'],
                    'change': arr_change,
                    # Round 13 / Phase 1.8: explicit comparability flag
                    # so report renderers know whether to print a
                    # percentage or a "currency-not-comparable" label.
                    'currency_comparable': bool(_arr_comparable),
                    'oldest_currency': _old_ccy,
                    'newest_currency': _new_ccy,
                }
                if _arr_comparable:
                    # Round 12 / Phase 11.1: canonical percent rounding.
                    _arr_trend_entry['pct_change'] = _r12_round_percent(
                        arr_change / oldest['total_arr'] * 100, 1
                    )
                else:
                    _arr_trend_entry['pct_change'] = None
                    _arr_trend_entry['note'] = (
                        'pct_change suppressed: snapshots not currency-comparable'
                    )
                trends['arr_trend'] = _arr_trend_entry

            if oldest['severity_counts'] and newest['severity_counts']:
                sev_trend = {}
                all_sevs = set(list(oldest['severity_counts'].keys()) + list(newest['severity_counts'].keys()))
                for sev in all_sevs:
                    old_cnt = oldest['severity_counts'].get(sev, 0)
                    new_cnt = newest['severity_counts'].get(sev, 0)
                    if old_cnt or new_cnt:
                        sev_trend[sev] = {
                            'oldest': old_cnt, 'newest': new_cnt,
                            'change': new_cnt - old_cnt,
                        }
                if sev_trend:
                    trends['severity_trend'] = sev_trend

        all_customer_sets = []
        for rpt in reports_data:
            cust_set = set()
            for sheet, m_data in (rpt.get('metrics') or {}).items():
                if isinstance(m_data, dict):
                    # Round 11 / Phase 6.1: also pick up the
                    # sampled variants so a portfolio that was
                    # truncated mid-fetch still contributes its
                    # top customers to the cross-report intersect.
                    for k in (
                        'top_customers_by_arr',
                        'top_customers_by_arr_sampled',
                        'top_customers_by_count',
                        'top_customers_by_count_sampled',
                    ):
                        if k in m_data and isinstance(m_data[k], dict):
                            cust_set.update(str(k) for k in m_data[k].keys() if k is not None)
            all_customer_sets.append(cust_set)
        if len(all_customer_sets) >= 2 and any(all_customer_sets):
            non_empty = [s for s in all_customer_sets if s]
            if len(non_empty) >= 2:
                recurring = set.intersection(*non_empty)
                recurring.discard('nan')
                recurring.discard('None')
                if recurring:
                    trends['recurring_customers'] = {
                        'count': len(recurring),
                        'names': sorted(list(recurring))[:15],
                        'appears_in_all_reports': True,
                    }

    except Exception as e:
        logger.debug(f"Cross-report trend analysis error: {e}")
    return trends


# --------------------------- External Intelligence ---------------------------
# Round 5 / Phase 4.6: previously the help.webex.com URLs were
# hardcoded at module import time, which meant that:
#   - air-gapped / on-prem deployments could not change them
#   - any vendor URL change required a code release
#   - tests that ran on machines without internet could not stub
#     out the targets
# Allow the operator to override via ``ADOPTIQ_HELP_URLS``
# (newline OR comma separated) and fall back to the previously
# hardcoded set.
_DEFAULT_HELP_URLS = [
    "https://help.webex.com/en-us/article/mqkve8/Webex-App-%7C-Release-notes",
    "https://help.webex.com/en-us/article/8dmbcr/What's-New-in-Webex-Suite",
    "https://help.webex.com/article/bsmvpdb/Webex-App-%7C-Known-issues",
]


def _load_help_urls_from_env() -> List[str]:
    raw = os.environ.get("ADOPTIQ_HELP_URLS", "").strip()
    if not raw:
        return list(_DEFAULT_HELP_URLS)
    parts: List[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        v = chunk.strip()
        if v and v.lower().startswith(("http://", "https://")):
            parts.append(v)
    return parts or list(_DEFAULT_HELP_URLS)


HELP_URLS = _load_help_urls_from_env()


# Round 5 / Phase 4.6: same env-overridable treatment for the
# status.webex.com endpoints (HTML history, all-incidents JSON,
# current incidents RSS, historical RSS).  Override via
# ``ADOPTIQ_STATUS_<KEY>`` env vars or
# ``ADOPTIQ_STATUS_BASE_URL`` to swap the host wholesale.
def _status_url(name: str, default: str) -> str:
    base = os.environ.get("ADOPTIQ_STATUS_BASE_URL", "").rstrip("/")
    override = os.environ.get(f"ADOPTIQ_STATUS_{name}", "").strip()
    if override:
        return override
    if base:
        # Replace just the host portion, keep path/query of default.
        try:
            from urllib.parse import urlparse, urlunparse
            d = urlparse(default)
            b = urlparse(base if "://" in base else f"https://{base}")
            return urlunparse((b.scheme or d.scheme, b.netloc or d.netloc, d.path, d.params, d.query, d.fragment))
        except Exception:
            return default
    return default


STATUS_HISTORY_HTML_URL = _status_url("HISTORY_HTML_URL", "https://status.webex.com/incident/history?lang=en_US")
STATUS_ALL_INCIDENTS_JSON_URL = _status_url("ALL_INCIDENTS_JSON_URL", "https://status.webex.com/all-incidents.json")
STATUS_INCIDENTS_RSS_URL = _status_url("INCIDENTS_RSS_URL", "https://status.webex.com/incidents.rss")
STATUS_HISTORY_RSS_URL = _status_url("HISTORY_RSS_URL", "https://status.webex.com/history.rss")


# Round 5 / Phase 4.7 / 4.8: cap response body size before
# parsing.  ``response.json()`` and ``BeautifulSoup`` both happily
# materialise multi-MB responses into RAM, which is a DoS vector
# for an upstream that returns an unexpectedly huge / malformed
# blob.  These helpers stream up to a cap and raise if exceeded.
_DEFAULT_HTTP_BODY_CAP = int(os.environ.get("ADOPTIQ_HTTP_BODY_CAP_BYTES", str(8 * 1024 * 1024)))  # 8 MB


def _read_response_capped(resp: Any, cap: int = _DEFAULT_HTTP_BODY_CAP) -> bytes:
    """Read a ``requests.Response`` body, streaming, with a hard cap.

    Raises ``ValueError`` if the body exceeds ``cap`` bytes.  Callers
    that want graceful degradation should wrap the call.
    """
    chunks: List[bytes] = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > cap:
                raise ValueError(
                    f"HTTP body exceeded cap of {cap} bytes "
                    f"(read at least {total} bytes from {getattr(resp, 'url', '?')})"
                )
            chunks.append(chunk)
    except ValueError:
        raise
    except Exception:
        # Fall back to .content if streaming is not available
        body = getattr(resp, 'content', b'') or b''
        if len(body) > cap:
            raise ValueError(
                f"HTTP body exceeded cap of {cap} bytes "
                f"(content length {len(body)} from {getattr(resp, 'url', '?')})"
            )
        return body
    return b"".join(chunks)


def _response_json_capped(resp: Any, cap: int = _DEFAULT_HTTP_BODY_CAP) -> Any:
    """``response.json()`` with a body-size cap (Phase 4.7)."""
    body = _read_response_capped(resp, cap=cap)
    import json as _json
    return _json.loads(body.decode(getattr(resp, 'encoding', None) or 'utf-8', errors='replace'))


def _response_text_capped(resp: Any, cap: int = _DEFAULT_HTTP_BODY_CAP) -> str:
    """``response.text`` with a body-size cap (Phase 4.8)."""
    body = _read_response_capped(resp, cap=cap)
    enc = getattr(resp, 'encoding', None) or 'utf-8'
    try:
        return body.decode(enc, errors='replace')
    except Exception:
        return body.decode('utf-8', errors='replace')

def fetch_help_webex_bugs(timeout=25) -> List[Dict[str,str]]:
    """Fetch known bugs from help.webex.com with robust error handling and multiple strategies.

    Round 2 / Phase 5.2: per-URL failures are accumulated into a
    ``fetch_errors`` list and stamped onto the returned list as
    ``list.fetch_errors`` (and also returned via the
    ``fetch_help_webex_bugs_with_errors`` helper) so callers can
    distinguish "every source we tried was unavailable" from "all
    sources returned cleanly with no bugs".
    """
    all_bugs = {}
    fetch_errors: List[Dict[str, str]] = []
    sources_attempted = 0
    sources_failed = 0

    # Strategy 1: Search known help URLs
    for url in HELP_URLS:
        sources_attempted += 1
        try:
            # Round 5 / Phase 4.8: stream + cap the body so a giant /
            # malicious response cannot OOM the process via
            # BeautifulSoup.
            r = requests.get(url, timeout=timeout, stream=True)
            r.raise_for_status()
            soup = BeautifulSoup(_response_text_capped(r), "html.parser")
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
            sources_failed += 1
            fetch_errors.append({
                'url': url,
                'kind': 'request_error',
                'error': str(e) or e.__class__.__name__,
            })
            continue
        except Exception as e:
            logger.warning(f"Unexpected error fetching {url}: {e}")
            sources_failed += 1
            fetch_errors.append({
                'url': url,
                'kind': 'unexpected_error',
                'error': str(e) or e.__class__.__name__,
            })
            continue

    # Strategy 2: Search for specific bug-related content
    try:
        search_terms = ["known issues", "software bugs", "defects", "limitations", "troubleshooting"]
        for term in search_terms:
            sources_attempted += 1
            try:
                search_url = f"https://help.webex.com/en-us/search?q={term}"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }

                # Round 5 / Phase 4.8: stream + cap to bound memory.
                r = requests.get(search_url, headers=headers, timeout=timeout, stream=True)
                r.raise_for_status()
                soup = BeautifulSoup(_response_text_capped(r), "html.parser")

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
                sources_failed += 1
                fetch_errors.append({
                    'url': f"search:{term}",
                    'kind': 'search_error',
                    'error': str(e) or e.__class__.__name__,
                })
                continue

    except Exception as e:
        logger.warning(f"Error in secondary search strategy: {e}")
        fetch_errors.append({
            'url': 'search_strategy',
            'kind': 'strategy_error',
            'error': str(e) or e.__class__.__name__,
        })

    # Return unique bugs
    # Round 2 / Phase 5.2: subclass list so callers can introspect
    # ``.fetch_errors`` / ``.all_sources_failed`` without changing the
    # public return type or breaking existing iteration callers.
    class _BugsResult(list):
        fetch_errors: List[Dict[str, str]] = []
        sources_attempted: int = 0
        sources_failed: int = 0
        all_sources_failed: bool = False

    unique_bugs = _BugsResult(all_bugs.values())
    unique_bugs.fetch_errors = list(fetch_errors)
    unique_bugs.sources_attempted = sources_attempted
    unique_bugs.sources_failed = sources_failed
    unique_bugs.all_sources_failed = (
        sources_attempted > 0 and sources_failed >= sources_attempted
    )
    logger.info(
        "Successfully fetched %d software bugs from help.webex.com (sources_attempted=%d, sources_failed=%d)",
        len(unique_bugs), sources_attempted, sources_failed,
    )

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
    # Round 5 / Phase 4.16: track per-run parse skip counters at
    # function scope so we can compare "rows seen" vs "rows
    # successfully parsed" after the Playwright context exits.
    _rows_seen = 0
    _row_skip_count = 0
    _first_skip_err: Optional[str] = None

    try:
        from playwright.sync_api import sync_playwright

        url = STATUS_HISTORY_HTML_URL

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

            # Round 5 / Phase 4.4: previously this dropped the FULL
            # rendered page source into the cwd as
            # ``playwright_page_source.html`` on EVERY run.  That
            # file:
            #   - was unbounded in size (status pages can be MBs)
            #   - was world-readable in many deployments
            #   - leaked customer names, ticket IDs, and any
            #     account-specific UI strings rendered by the page
            #   - was kept FOREVER (overwritten only on next run)
            # Now: only write it when the operator explicitly opts in
            # via DEBUG (or ``ADOPTIQ_DEBUG_PLAYWRIGHT_DUMP=1``), cap
            # the dump to 256 KB, and put it under the OS tempdir
            # with a per-run unique name so concurrent runs do not
            # clobber each other's evidence.
            try:
                _dump_enabled = (
                    logger.isEnabledFor(logging.DEBUG)
                    or os.environ.get('ADOPTIQ_DEBUG_PLAYWRIGHT_DUMP', '0') == '1'
                )
            except Exception:
                _dump_enabled = False
            if _dump_enabled:
                try:
                    import tempfile as _tf
                    _DUMP_CAP = 256 * 1024  # 256 KB
                    _truncated = len(content) > _DUMP_CAP
                    _payload = content[:_DUMP_CAP] if _truncated else content
                    _fd, _path = _tf.mkstemp(
                        prefix='adoptiq_playwright_',
                        suffix='.html',
                    )
                    try:
                        with os.fdopen(_fd, 'w', encoding='utf-8') as f:
                            f.write(_payload)
                            if _truncated:
                                f.write(
                                    f"\n<!-- truncated: {len(content) - _DUMP_CAP} chars omitted -->"
                                )
                        # Restrict to owner-only since this can contain
                        # sensitive customer-facing UI strings.
                        try:
                            os.chmod(_path, 0o600)
                        except Exception:
                            pass  # noqa: PIE790
                        logger.debug(
                            f"Saved Playwright page source dump to {_path} "
                            f"({len(_payload)} of {len(content)} chars{' [TRUNCATED]' if _truncated else ''})"
                        )
                    except Exception:
                        try:
                            os.close(_fd)
                        except Exception:
                            pass  # noqa: PIE790
                except Exception as _dump_err:
                    logger.debug(f"Could not write Playwright debug dump: {_dump_err}")

            # Parse with BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')

            # Look for PUB references
            pub_matches = soup.find_all(string=re.compile(r'PUB\d{7}'))
            logger.debug(f"Found {len(pub_matches)} PUB references")

            # Look for table rows
            rows = soup.find_all('tr')
            _rows_seen = len(rows)
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
                    # Round 5 / Phase 4.16: count and remember the
                    # first parse failure so we can surface it below
                    # if EVERY row was skipped (which would otherwise
                    # silently look identical to "the page rendered
                    # zero incidents").
                    _row_skip_count += 1
                    if _first_skip_err is None:
                        _first_skip_err = f"{type(e).__name__}: {e}"
                    continue

            browser.close()

        # Round 5 / Phase 4.16: if rows were present but every single
        # one failed to parse, that is not "no incidents" -- that is
        # almost certainly a layout / DOM change on the upstream page.
        # Promote that to a warning instead of silently returning [].
        if _rows_seen > 0 and len(incidents) == 0 and _row_skip_count >= _rows_seen:
            logger.warning(
                "Playwright incident-history parser saw %d rows but skipped ALL "
                "of them (first error: %s). Likely a status.webex.com layout "
                "change; downstream callers will see zero historical incidents.",
                _rows_seen, _first_skip_err or 'unknown',
            )
        elif _row_skip_count > 0:
            logger.info(
                "Playwright incident-history parser skipped %d of %d rows during parse.",
                _row_skip_count, _rows_seen,
            )

        logger.info(f"Successfully parsed {len(incidents)} historical incidents")

    except ImportError:
        logger.info("Playwright not available, skipping...")
    except Exception as e:
        logger.warning(f"Playwright failed: {e}")

    return incidents

def fetch_status_incidents(timeout=25, days_back: Optional[int] = None) -> List[Dict[str,str]]:
    """Fetch Webex Status incidents.

    Round 2 / Phase 1.7 — ``days_back`` is now an explicit parameter so
    callers (renewal/leader narrative, EI generator) can constrain the
    window to the report period.  When ``days_back`` is None the legacy
    365-day window is used and a ``window_default_used=True`` marker is
    surfaced on the returned list (via attribute on the wrapping dict
    where applicable) so downstream renderers can disclose the default.

    The merged feed is also tagged with ``_window_truncated=True`` on the
    last record when storage hits the per-fetch cap so renewal/leader
    narratives can surface "showing N of M" disclosures instead of
    silently treating the cap as authoritative.
    """
    # Import the storage system
    try:
        from incident_storage import get_historical_incidents, store_historical_incidents, get_incident_statistics
        storage_available = True
    except ImportError:
        storage_available = False
        logger.warning("Incident storage not available, using live data only")

    # Normalize the window — keep legacy behavior when caller does not
    # pass a value (so existing call sites do not regress) but cap to a
    # sensible ceiling.
    _effective_days = int(days_back) if isinstance(days_back, (int, float)) and days_back and days_back > 0 else 365
    _per_fetch_cap = 500
    _window_default_used = days_back is None

    data = []

    # First, try to get incidents from storage
    if storage_available:
        try:
            stored_incidents = get_historical_incidents(days_back=_effective_days, limit=_per_fetch_cap)
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
        api_url = STATUS_ALL_INCIDENTS_JSON_URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }

        # Round 5 / Phase 4.7: stream + cap the response body before
        # parsing JSON.  An upstream that returns a malformed
        # multi-GB body (or a confused proxy serving a binary blob)
        # would otherwise OOM the process when ``r.json()`` reads
        # the entire content into memory.
        r = requests.get(api_url, headers=headers, timeout=timeout, stream=True)
        r.raise_for_status()
        api_data = _response_json_capped(r)
        incidents_list = api_data.get('incidents', []) if isinstance(api_data, dict) else []
        logger.info(f"JSON API returned {len(incidents_list)} incidents")

        # Round 2 / Phase 5.5: preserve the Statuspage 'critical' label
        # instead of collapsing it into 'High'.  Critical = full outage
        # / customer-impacting; downstream scoring and the EI / leader
        # dashboards now have a distinct band so a critical incident
        # is not visually equal to a single high-impact event.
        impact_map = {'none': 'Low', 'minor': 'Medium', 'major': 'High', 'critical': 'Critical'}

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

    # Supplement: incidents.rss (always runs to catch items the JSON API may not include)
    try:
        logger.info("Supplementing incidents from incidents.rss...")
        rss_url = STATUS_INCIDENTS_RSS_URL
        rss_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        }

        # Round 5 / Phase 4.8: stream + cap RSS body before parse.
        r = requests.get(rss_url, headers=rss_headers, timeout=timeout, stream=True)
        r.raise_for_status()

        import feedparser
        feed = feedparser.parse(_response_text_capped(r))
        rss_added = 0

        for item in feed.entries[:50]:
            try:
                title = item.get("title", "").strip()
                link = item.get("link", "").strip()
                if not title or not link:
                    continue

                rss_id = link
                if '#' in link:
                    fragment = link.split('#', 1)[1]
                    if fragment:
                        rss_id = fragment

                published_date = ''
                if hasattr(item, 'published_parsed') and item.published_parsed:
                    try:
                        published_date = datetime(*item.published_parsed[:6]).strftime('%Y-%m-%dT%H:%M:%SZ')
                    except Exception as _date_err:
                        logger.debug("RSS published_parsed conversion failed: %s", _date_err)
                        published_date = item.get('published', '')
                else:
                    published_date = item.get('published', '')

                content_to_check = title.lower()
                description_elem = item.get("description", "")
                if description_elem:
                    content_to_check += " " + str(description_elem).lower()

                impact_level = "Medium"
                if any(w in content_to_check for w in ["outage", "down", "unavailable", "critical", "major", "severe"]):
                    impact_level = "High"
                elif any(w in content_to_check for w in ["low", "minor", "informational"]):
                    impact_level = "Low"

                inc_status = "resolved"
                if any(w in content_to_check for w in ["ongoing", "active", "investigating", "monitoring", "identified"]):
                    inc_status = "active"

                data.append({
                    "id": rss_id,
                    "title": title,
                    "link": link,
                    "published": published_date,
                    "status": inc_status,
                    "impact_level": impact_level,
                    "source": "status.webex.com/rss",
                    "description": str(description_elem)[:500] if description_elem else '',
                })
                rss_added += 1
            except Exception as e:
                logger.warning(f"Error processing RSS item: {e}")
                continue

        logger.info(f"Supplemented {rss_added} incidents from incidents.rss")
    except Exception as e:
        logger.warning(f"Error supplementing from incidents.rss: {e}")

    # Supplement: parse non-maintenance incidents from history.rss
    try:
        logger.info("Supplementing incidents from history.rss...")
        hist_url = STATUS_HISTORY_RSS_URL
        hist_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        }
        # Round 5 / Phase 4.8: stream + cap.
        hr = requests.get(hist_url, headers=hist_headers, timeout=timeout, stream=True)
        hr.raise_for_status()

        import feedparser
        hist_feed = feedparser.parse(_response_text_capped(hr))
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
                    except Exception as _date_err:
                        logger.debug("history.rss published_parsed conversion failed: %s", _date_err)
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
            except Exception as _rss_err:
                logger.debug("Skipped history.rss entry: %s", _rss_err)
                continue

        logger.info(f"Supplemented {hist_added} incidents from history.rss")
    except Exception as e:
        logger.warning(f"Error supplementing from history.rss: {e}")

    # Round 4 / Phase 3.2: dedup with explicit precedence so a fresh
    # live-API row replaces the stale storage row for the same id.
    # Previously we kept the FIRST occurrence (storage rows were
    # prepended), which meant ``_from_storage=True`` always won even
    # when the live feed had returned a newer status / description.
    # New rule: prefer non-``_from_storage`` first; among equals,
    # prefer the row with the later ``published`` / ``last_seen``.
    def _row_published_ts(_inc):
        for _key in ('published', 'last_seen', 'resolved_at'):
            _val = _inc.get(_key)
            if _val:
                try:
                    return pd.to_datetime(_val, errors='coerce', utc=True)
                except Exception:
                    return None
        return None

    by_id: dict = {}
    for incident in data:
        _id = incident.get('id')
        if not _id:
            continue
        existing = by_id.get(_id)
        if existing is None:
            by_id[_id] = incident
            continue
        # Prefer live (non-storage) over storage.
        existing_storage = bool(existing.get('_from_storage'))
        new_storage = bool(incident.get('_from_storage'))
        if existing_storage and not new_storage:
            by_id[_id] = incident
            continue
        if new_storage and not existing_storage:
            continue
        # Same source bucket — pick the newer timestamp.
        ets = _row_published_ts(existing)
        nts = _row_published_ts(incident)
        try:
            if nts is not None and (ets is None or nts > ets):
                by_id[_id] = incident
        except Exception:
            pass
    unique_incidents = list(by_id.values())

    # Sort by published date (most recent first).
    # Round 11 / Phase 2.5: previous ``str(published)`` lexicographic
    # sort silently mis-ordered mixed-format timestamps – e.g.
    # ``"Mon, 04 Mar 2024 14:23:00 GMT"`` (RSS / RFC 2822) sorted
    # *before* ``"2024-03-04T14:23:00Z"`` because ``M < 2``.  Parse
    # to UTC-aware ``pd.Timestamp`` and use a sentinel
    # (``Timestamp.min``) for unparseable / missing values so the
    # sort is total and reproducible.
    def _parsed_published_ts(_inc):
        for _key in ('published', 'last_seen', 'resolved_at'):
            _val = _inc.get(_key)
            if not _val:
                continue
            try:
                _ts = pd.to_datetime(_val, errors='coerce', utc=True)
            except Exception:
                _ts = None
            if _ts is not None and not pd.isna(_ts):
                return _ts
        # Sentinel: datetime.min in UTC sorts to the end when reverse=True.
        try:
            return pd.Timestamp.min.tz_localize('UTC')
        except Exception:
            return pd.Timestamp('1970-01-01', tz='UTC')

    try:
        unique_incidents.sort(key=_parsed_published_ts, reverse=True)
    except Exception as _sort_err:
        logger.debug(
            "Round 11 / Phase 2.5: parsed-timestamp sort failed "
            "(%s); falling back to lexicographic sort.",
            _sort_err,
        )
        unique_incidents.sort(
            key=lambda x: str(x.get('published') or ''), reverse=True
        )

    # Round 2 / Phase 1.7 — apply explicit window when caller specified
    # one and tag truncation so the renewal/leader narrative can show
    # "showing N of M (capped)" instead of silently treating the cap as
    # authoritative.
    pre_window_count = len(unique_incidents)
    try:
        if not _window_default_used:
            # Round 5 / Phase 4.9: published timestamps in the merged
            # feed are a mixture of formats:
            #   - ``2024-03-04T14:23:00Z`` (statuspage JSON)
            #   - ``Mon, 04 Mar 2024 14:23:00 GMT`` (RSS / RFC 2822)
            #   - tz-naive ISO from feedparser's ``published_parsed``
            # The previous strict ``strptime('%Y-%m-%dT%H:%M:%S')``
            # path silently mis-classified anything with a ``Z`` /
            # offset / RFC-2822 string as "not parseable" and kept
            # it (line 4121 returned True), polluting the window with
            # items from outside ``days_back``.  Use
            # ``pd.to_datetime(..., utc=True)`` so every common
            # format normalises to a UTC-aware Timestamp and the
            # cutoff comparison is apples to apples.
            # Round 8 / Phase 2.10: ``pd.Timestamp.utcnow()`` is
            # deprecated; use ``pd.Timestamp.now('UTC')`` for the
            # same UTC-aware value.
            _cutoff_dt = pd.Timestamp.now('UTC') - pd.Timedelta(days=_effective_days)
            try:
                _cutoff_dt = _cutoff_dt.tz_convert('UTC')
            except Exception:
                # tz_localize for naive Timestamps
                try:
                    _cutoff_dt = _cutoff_dt.tz_localize('UTC')
                except Exception:
                    pass  # noqa: PIE790
            # Round 11 / Phase 2.4: when the caller asked for an
            # explicit ``days_back`` window, undated incidents must
            # NOT be silently kept inside the window – that gave
            # callers like the renewal dashboard a count that
            # included rows whose ``published`` was unknown / older
            # than the window.  Quarantine them in
            # ``undated_incidents`` so the caller can render an
            # explicit "N undated incidents excluded from the
            # X-day window" disclaimer.  Rows with malformed but
            # parseable dates are still compared against the
            # cutoff (they are NOT considered undated).
            undated_incidents: list = []

            def _within(inc):
                pub = inc.get('published')
                if not pub:
                    inc.setdefault('undated', True)
                    undated_incidents.append(inc)
                    return False
                try:
                    ts = pd.to_datetime(pub, utc=True, errors='coerce')
                except Exception:
                    inc.setdefault('undated', True)
                    undated_incidents.append(inc)
                    return False
                if ts is None or pd.isna(ts):
                    inc.setdefault('undated', True)
                    undated_incidents.append(inc)
                    return False
                try:
                    return ts >= _cutoff_dt
                except Exception:
                    return True
            unique_incidents = [inc for inc in unique_incidents if _within(inc)]
            # Surface the quarantined rows on the function frame so
            # downstream callers (renewal dashboard / Word Customer
            # Health Dashboard) can either render or ignore them
            # explicitly.
            try:
                _undated_count_for_log = len(undated_incidents)
                if _undated_count_for_log:
                    logger.info(
                        "Quarantined %d undated incidents from explicit "
                        "days_back=%d window",
                        _undated_count_for_log,
                        int(_effective_days),
                    )
            except Exception:
                pass
    except Exception as _win_err:
        logger.debug(f"days_back filter skipped: {_win_err}")

    truncated = (pre_window_count >= _per_fetch_cap) or (len(unique_incidents) >= _per_fetch_cap)

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
    logger.info(
        "Successfully fetched %d service incidents from status.webex.com "
        "(window=%d days%s, truncated=%s)",
        len(unique_incidents), _effective_days,
        ' [default]' if _window_default_used else '',
        truncated,
    )
    logger.debug(f"Incident sources: {set(inc.get('source', 'unknown') for inc in unique_incidents)}")
    logger.debug(f"Sample incidents: {[inc.get('id', 'no-id') for inc in unique_incidents[:5]]}")

    # Round 4 / Phase 5.5: detect "served from local cache" state.
    # If every incident in the final result has ``_from_storage=True``
    # AND none of the live sources successfully appended live records
    # this run, then the dataset the UI/LLM is about to consume is
    # entirely the local SQLite cache.  Stamping a meta flag lets
    # ``executive_intelligence_formatter`` and the ``/external``
    # template surface "Served from local cache (live API
    # unreachable)" instead of presenting the cached snapshot as if
    # it were the live status.webex feed.
    served_from_local_cache = bool(unique_incidents) and all(
        bool(inc.get('_from_storage')) for inc in unique_incidents
    ) and not json_api_succeeded

    # Round 11 / Phase 10.6: derive a ``stale_storage`` flag so the UI
    # / report can clearly signal "the freshest incident the local
    # cache has is more than 24h old" even when a few live records
    # were appended.  Without this, served_from_local_cache=False
    # could still be paired with a cache that has not been refreshed
    # in days, and the consumer would have no way to know.
    stale_storage = False
    try:
        if served_from_local_cache or any(inc.get('_from_storage') for inc in unique_incidents):
            _now_utc = pd.Timestamp.now('UTC')
            _newest_storage_ts = None
            for _inc in unique_incidents:
                if not _inc.get('_from_storage'):
                    continue
                _ts = _parsed_published_ts(_inc)
                if _ts is None or pd.isna(_ts):
                    continue
                if _newest_storage_ts is None or _ts > _newest_storage_ts:
                    _newest_storage_ts = _ts
            if _newest_storage_ts is not None:
                _age_hours = (_now_utc - _newest_storage_ts).total_seconds() / 3600.0
                stale_storage = _age_hours > 24.0
            else:
                # No parseable storage timestamp = treat as stale so
                # downstream surfaces an honest warning.
                stale_storage = served_from_local_cache
    except Exception as _stale_err:
        logger.debug(
            "Round 11 / Phase 10.6: stale_storage detection skipped (%s)",
            _stale_err,
        )

    # Tag the last record so downstream renderers can surface truncation
    # without changing the public return shape (a list of dicts).
    if unique_incidents:
        try:
            # Round 11 / Phase 2.4: surface ``undated_count`` so the
            # caller can render an explicit disclaimer instead of
            # silently combining undated rows with the windowed total.
            try:
                _undated_count = len(undated_incidents)  # type: ignore[name-defined]
            except NameError:
                _undated_count = 0
            unique_incidents[-1]['_window_meta'] = {
                'days_back': _effective_days,
                'window_default_used': _window_default_used,
                'truncated': truncated,
                'cap': _per_fetch_cap,
                'pre_window_count': pre_window_count,
                'post_window_count': len(unique_incidents),
                'served_from_local_cache': served_from_local_cache,
                'stale_storage': stale_storage,
                'undated_count': _undated_count,
            }
            if served_from_local_cache:
                # First record gets the visible flag so the formatter
                # can disclose it on every page that lists incidents.
                unique_incidents[0]['_served_from_local_cache'] = True
            if stale_storage:
                unique_incidents[0]['_stale_storage'] = True
        except Exception:
            pass

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
        rss_url = STATUS_HISTORY_RSS_URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        }
        # Round 5 / Phase 4.8: stream + cap.
        r = requests.get(rss_url, headers=headers, timeout=timeout, stream=True)
        r.raise_for_status()

        import feedparser
        feed = feedparser.parse(_response_text_capped(r))

        # Round 8 / Phase 2.9: ``datetime.utcnow()`` is deprecated
        # in Python 3.12+ and returns a naive datetime that is
        # ambiguous when compared to feed timestamps that may carry
        # an explicit ``Z`` / offset.  Use ``datetime.now(UTC)`` and
        # then strip the tzinfo so the comparison against the
        # ``%Y-%m-%dT%H:%M:%S`` parsed feed value (also naive)
        # remains apples-to-apples.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

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
                    except Exception as _dt_err:
                        logger.debug(f"Feed date parse failed: {_dt_err}")

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

    # Round 4 / Phase 3.1: previously bailed out when ``csone_df`` was
    # empty even though the function accepted ``ab_df``.  Customer
    # adoption barriers can also reference outage language ("Webex
    # meeting failures", "service degradation") so we now correlate
    # incidents against AB rows in addition to CSOne when either side
    # has data.  We only return early when BOTH datasets are missing.
    csone_empty = csone_df is None or getattr(csone_df, 'empty', True)
    ab_empty = ab_df is None or getattr(ab_df, 'empty', True)
    if not ext_incidents or (csone_empty and ab_empty):
        return correlations

    from datetime import datetime, timedelta
    import re

    for incident in ext_incidents:
        incident_id = (incident.get('id') or '')
        incident_title = (incident.get('title') or '').lower()
        incident_desc = (incident.get('description') or '').lower()
        incident_published = (incident.get('published') or '')

        correlated_cases = []

        # Try to parse incident date.
        # Round 3 / Phase 4.6: status.webex.com publishes ISO 8601
        # timestamps like ``2024-03-04T14:23:00Z`` which the previous
        # ``strptime`` loop could not match (no Z-aware format and
        # ``str.split()`` left the whole token attached). It also
        # tried ``%m/%d/%Y`` then ``%d/%m/%Y`` for the same string,
        # which silently picked one interpretation for ambiguous
        # values like ``03/04/2024``. Use a tz-aware parser that
        # honours the ISO ``Z`` suffix and only falls back to the
        # explicitly unambiguous ``%Y-%m-%d`` form.
        incident_date = None
        if incident_published:
            try:
                _parsed = pd.to_datetime(
                    incident_published, errors="coerce", utc=True
                )
                if _parsed is not None and not pd.isna(_parsed):
                    try:
                        incident_date = _parsed.tz_convert(None).to_pydatetime()
                    except Exception:
                        incident_date = _parsed.to_pydatetime()
            except Exception as _xref_err:
                logger.debug(f"Cross-reference ISO date parse failed: {_xref_err}")
            if incident_date is None:
                # Last-resort: explicit YYYY-MM-DD only. Do NOT try
                # %m/%d vs %d/%m because ambiguous strings would
                # silently pick one and skew the temporal match.
                try:
                    parts = incident_published.split()
                    if parts:
                        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                            try:
                                incident_date = datetime.strptime(parts[0], fmt)
                                break
                            except Exception as _fmt_err:
                                logger.debug(
                                    "Date format %s did not match %s: %s",
                                    fmt, parts[0], _fmt_err,
                                )
                                continue
                        if incident_date is None:
                            logger.debug(
                                "Skipping ambiguous incident date %r (no ISO/YYYY-MM-DD form)",
                                incident_published,
                            )
                except Exception as _legacy_err:
                    logger.debug(
                        "Cross-reference legacy date parse failed: %s",
                        _legacy_err,
                    )

        incident_keywords = set(re.findall(r'\b\w{4,}\b', incident_title + ' ' + incident_desc))

        def _scan_dataframe(_df, *, source: str, title_cols, desc_cols, date_cols, case_cols, customer_cols):
            """Round 4 / Phase 3.1: shared scan over CSOne or AB rows."""
            if _df is None or getattr(_df, 'empty', True):
                return
            try:
                _iter = _df.iterrows()
            except Exception:
                return
            for _idx, row in _iter:
                def _first(cols):
                    for _c in cols:
                        if _c in row and row.get(_c) not in (None, ''):
                            return row.get(_c)
                    return ''
                case_title = str(_first(title_cols) or '').lower()
                case_desc = str(_first(desc_cols) or '').lower()
                case_date_str = _first(date_cols)

                case_keywords = set(re.findall(r'\b\w{4,}\b', case_title + ' ' + case_desc))
                keyword_match = len(incident_keywords & case_keywords) >= 2

                temporal_match = False
                days_diff = None
                if incident_date and case_date_str:
                    try:
                        # Round 13 / Phase 2.4: parse the case-side date
                        # with utc=True so a case row carrying its own
                        # offset (or already tz-aware) compares cleanly
                        # against the UTC-anchored incident_date.  The
                        # previous implementation dropped tz info, then
                        # subtracted from a naive (or sometimes UTC-
                        # naive) incident_date, skewing the +/-7 day
                        # window by up to 24h.
                        case_date = pd.to_datetime(case_date_str, errors="coerce", utc=True)
                        if isinstance(case_date, pd.Timestamp) and not pd.isna(case_date):
                            try:
                                case_date_dt = case_date.tz_convert(None).to_pydatetime()
                            except Exception:
                                case_date_dt = case_date.to_pydatetime()
                        else:
                            case_date_dt = None
                        if isinstance(case_date_dt, datetime):
                            days_diff = abs((incident_date - case_date_dt).days)
                            if days_diff <= 7:
                                temporal_match = True
                    except Exception as _temp_err:
                        logger.debug("Temporal correlation parse failed: %s", _temp_err)

                if keyword_match or temporal_match:
                    correlated_cases.append({
                        'case': _first(case_cols) or 'N/A',
                        'customer': _first(customer_cols) or 'Unknown',
                        'title': _first(title_cols) or 'No Title',
                        'match_type': 'keyword' if keyword_match else 'temporal',
                        'days_diff': days_diff if temporal_match else None,
                        'source': source,
                    })

        # Search through CSOne cases
        _scan_dataframe(
            csone_df,
            source='tac',
            title_cols=['Title'],
            desc_cols=['Problem Description'],
            date_cols=['Date/Time Opened'],
            case_cols=['SR Number', 'Case Number'],
            customer_cols=['Customer Name'],
        )

        # Round 4 / Phase 3.1: also search through Adoption Barriers.
        # AB columns vary across exports; try the most common shapes.
        _scan_dataframe(
            ab_df,
            source='ab',
            title_cols=['title', 'Title', 'subject', 'Subject', 'NAME'],
            desc_cols=['description', 'Description', 'notes', 'Notes', 'PROBLEM_DESCRIPTION'],
            date_cols=['date_created', 'CREATED_DATE', 'created_date', 'open_date', 'OPEN_DATE'],
            case_cols=['id', 'ID', 'barrier_id', 'BARRIER_ID', 'NAME'],
            customer_cols=['customer_name', 'Customer Name', 'CUSTOMER_NAME', 'account', 'ACCOUNT'],
        )

        if correlated_cases:
            correlations[incident_id] = correlated_cases

    return correlations

def cross_reference_refs(ab_df: pd.DataFrame, csone_df: pd.DataFrame, ext_bugs: List[Dict[str,str]]):
    # Build a set of CSC IDs from external
    ext_set = {b.get("bug_id", "").upper() for b in ext_bugs if b.get("bug_id")}
    # Aggregate refs from datasets
    refs = set()
    for df in [ab_df, csone_df]:
        if df is not None and not df.empty and "bemscsc_refs" in df.columns:
            for ref_str in df["bemscsc_refs"].dropna():
                for x in ref_str.split(", "):
                    if x.startswith("CSC"):
                        refs.add(x.upper())
    matches = sorted(list(refs & ext_set))
    # Round 71 / Phase 4 (#22): resolve title columns flexibly via the
    # canonical LIKELY_TITLE_COLS allow-list rather than hardcoding
    # ``row.get("title")`` / ``row.get("Title")``.  Pre-R71 a CSOne
    # export that shipped its title under ``SUBJECT`` / ``SUBJECT_C`` /
    # ``NAME`` / ``ACTION_PLAN_TITLE_C`` (all already in
    # LIKELY_TITLE_COLS for the rest of the codebase) silently lost the
    # title in the cross-reference matched-rows snapshot -- the
    # downstream Word "matched bugs" table then rendered a blank
    # title cell.
    _r71_ab_title_col = None
    if ab_df is not None and not ab_df.empty:
        _r71_ab_title_col = next((c for c in LIKELY_TITLE_COLS if c in ab_df.columns), None)
        if _r71_ab_title_col is None and "title" in ab_df.columns:
            _r71_ab_title_col = "title"
    _r71_csone_title_col = None
    if csone_df is not None and not csone_df.empty:
        _r71_csone_title_col = next((c for c in LIKELY_TITLE_COLS if c in csone_df.columns), None)
    matched_rows = []
    if matches and ab_df is not None and not ab_df.empty:
        for _, row in ab_df.iterrows():
            ref_str = row.get("bemscsc_refs", "")
            hits = [r for r in matches if r in ref_str]
            if hits:
                matched_rows.append({
                    "source":"AdoptionBarrier","id":row.get("ID"),"customer_name":row.get("customer_name"),
                    "title": (row.get(_r71_ab_title_col) if _r71_ab_title_col else row.get("title")),
                    "sub_technology":row.get("sub_technology"),"matches":", ".join(hits)
                })
    if matches and csone_df is not None and not csone_df.empty:
        for _, row in csone_df.iterrows():
            ref_str = row.get("bemscsc_refs", "")
            hits = [r for r in matches if r in ref_str]
            if hits:
                matched_rows.append({
                    "source":"CSOne","case":row.get("SR Number"),"customer_name":row.get("customer_name"),
                    "title": (row.get(_r71_csone_title_col) if _r71_csone_title_col else row.get("Title")),
                    "matches":", ".join(hits)
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
    # Round 6 / Phase 3.14: the Okta token URL, the Azure endpoint
    # used to reach CircuIT, and the Azure OpenAI API version are now
    # all environment-configurable.  Hard-coding them made it
    # impossible to point a non-prod deployment at a staging Okta
    # tenant or to upgrade to a newer Azure preview API without
    # editing source.  The defaults preserve the previous prod
    # behaviour exactly.
    OKTA_TOKEN_URL = os.getenv(
        "ADOPTIQ_CIRCUIT_OKTA_TOKEN_URL",
        "https://id.cisco.com/oauth2/default/v1/token",
    )
    AZURE_ENDPOINT = os.getenv(
        "ADOPTIQ_CIRCUIT_AZURE_ENDPOINT",
        "https://chat-ai.cisco.com",
    )
    AZURE_API_VERSION = os.getenv(
        "ADOPTIQ_CIRCUIT_AZURE_API_VERSION",
        "2024-08-01-preview",
    )

    def __init__(self, client_id: str, client_secret: str, app_key: str, model_name: Optional[str] = None):
        # Round 5 / Phase 3.13: do NOT default the model silently.
        # Hard-coding ``gpt-5-nano`` here meant any caller that
        # forgot to supply a model -- or any environment where the
        # ``CIRCUIT_MODEL_NAME`` config drifted -- ended up running
        # against the cheapest/smallest tier without anyone noticing.
        # That silently downgrades report quality and makes A/B
        # comparisons across runs incoherent.  Require an explicit
        # model name from the caller; raise loudly if missing.
        if not model_name or not str(model_name).strip():
            raise ValueError(
                "CircuitChatClient requires an explicit ``model_name`` -- "
                "no default model is provided so deployments must opt into "
                "their model tier (e.g. via CIRCUIT_MODEL_NAME)."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_key = app_key
        self.model_name = str(model_name).strip()
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
        # Round 6 / Phase 4.10: validate the JSON shape before
        # touching keys.  Okta has historically returned HTML error
        # pages with a 200 status when behind a captive portal /
        # zscaler interception, and a non-dict payload would crash
        # the .get(...) calls below with a confusing AttributeError.
        try:
            data = resp.json()
        except Exception as exc:
            raise ValueError(
                "Okta token response was not valid JSON"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"Okta token response was not a JSON object (got {type(data).__name__})"
            )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("No access_token in Okta response")
        expires_in = data.get("expires_in", 3600)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 3600
        if expires_in <= 0:
            expires_in = 3600
        self._access_token = token
        self._expiry = time.time() + expires_in
        return self._access_token

    # Round 6 / Phase 3.7: declare the single source of truth for the
    # CircuIT call timeout in seconds.  Both the AzureOpenAI client
    # (HTTP-level timeout) and the ThreadPoolExecutor future used in
    # ``generate_llm_response`` MUST use this value so we cannot end
    # up in a state where the executor's wall-clock budget is shorter
    # than the underlying HTTP client's timeout (the executor would
    # cancel a request the client still considered healthy, leaving
    # the worker thread alive until the client finally gave up).
    REQUEST_TIMEOUT_SECONDS = 120

    # Round 7 / Phase 5.5: jittered exponential backoff parameters for
    # ``llm.rate_limit_429`` retries.  Capped to a small number of
    # attempts because the upstream Azure OpenAI service typically
    # only needs a brief cool-off (sub-second) and we do not want to
    # blow past the executor wall clock in
    # ``generate_llm_response`` (which is REQUEST_TIMEOUT_SECONDS + 5).
    # ``RATE_LIMIT_RETRY_MAX_ATTEMPTS`` includes the initial attempt:
    # a value of 3 means at most two additional retries.
    RATE_LIMIT_RETRY_MAX_ATTEMPTS = 3
    RATE_LIMIT_RETRY_BASE_SECONDS = 0.5
    RATE_LIMIT_RETRY_MAX_SECONDS = 4.0

    def complete(self, system_message: str, user_message: str) -> Optional[str]:
        # Round 7 / Phase 5.5: wrap the original single-attempt body in
        # a small retry loop scoped *only* to ``llm.rate_limit_429``
        # outcomes.  Previously a single 429 from upstream surfaced as
        # a hard ``ERROR: llm.rate_limit_429: ...`` to the caller, even
        # though the canonical playbook for 429 is "wait briefly and
        # retry with jitter" -- a deterministic transient.  Other
        # error kinds (timeout, content_filter, unauthorized,
        # parse_fail, generic runtime) are still returned on the
        # first attempt because they are not safely retryable.
        import random as _r
        import time as _t
        last_result: Optional[str] = None
        for _attempt in range(1, self.RATE_LIMIT_RETRY_MAX_ATTEMPTS + 1):
            last_result = self._complete_once(system_message, user_message)
            if not (
                isinstance(last_result, str)
                and last_result.startswith("ERROR: llm.rate_limit_429")
            ):
                return last_result
            if _attempt >= self.RATE_LIMIT_RETRY_MAX_ATTEMPTS:
                break
            _backoff = min(
                self.RATE_LIMIT_RETRY_MAX_SECONDS,
                self.RATE_LIMIT_RETRY_BASE_SECONDS * (2 ** (_attempt - 1)),
            )
            # Full-jitter: pick a random sleep in [0, backoff] so a
            # fleet of concurrent callers does not synchronize.
            # Round 13 / Phase 11.1: previously this called
            # ``random.uniform(0, backoff)`` and ``time.sleep(...)``
            # unconditionally, which made every test that exercised
            # the 429 retry path timing-dependent (the wall clock
            # actually slept up to 4s per attempt) and used
            # ``random.uniform`` from the global RNG -- so reproducing
            # a CI-only timing regression locally was effectively
            # impossible.  In test mode honor a deterministic
            # backoff: use the *cap* of the exponential window
            # (instead of a random draw) and skip the actual
            # ``time.sleep`` call.  Production behavior is unchanged.
            _r13_test_mode = bool(
                os.environ.get("ADOPTIQ_TEST_MODE")
                or os.environ.get("PYTEST_CURRENT_TEST")
            )
            if _r13_test_mode:
                _sleep = float(_backoff)  # deterministic upper bound
            else:
                _sleep = _r.uniform(0.0, _backoff)
            logger.warning(
                "CircuIT 429 rate-limit on attempt %d/%d; sleeping %.2fs before retry%s",
                _attempt,
                self.RATE_LIMIT_RETRY_MAX_ATTEMPTS,
                _sleep,
                " (test-mode: sleep elided)" if _r13_test_mode else "",
            )
            if not _r13_test_mode:
                _t.sleep(_sleep)
        return last_result

    def _complete_once(self, system_message: str, user_message: str) -> Optional[str]:
        try:
            token = self._get_token()
            client = AzureOpenAI(
                azure_endpoint=self.AZURE_ENDPOINT,
                api_key=token,
                # Round 6 / Phase 3.14: API version comes from env
                # (with a stable default) so the deployment can be
                # rolled forward without code edits.
                api_version=self.AZURE_API_VERSION,
                # Round 6 / Phase 3.7: use the shared constant.
                timeout=float(self.REQUEST_TIMEOUT_SECONDS),
            )
            messages = [{"role":"system","content":system_message},{"role":"user","content":user_message}]
            # Round 4 / Phase 6.5: pin temperature + max_tokens explicitly
            # so the same briefing yields reproducible answers across runs
            # (no silent SDK-default temperature drift) and so the model
            # can no longer be cut off without us also knowing how big the
            # ceiling was. Use a low temperature for grounded /
            # report-style prose, and a generous (but bounded)
            # ``max_completion_tokens`` so finish_reason='length' is
            # genuinely informative. Fall back gracefully if the SDK
            # rejects either kwarg (e.g., older versions do not accept
            # ``max_completion_tokens`` and require ``max_tokens``).
            _llm_call_params: Dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "user": json.dumps({"appkey": self.app_key}),
                "stop": ["<|im_end|>"],
                "temperature": 0.2,
                "max_completion_tokens": 4096,
            }
            try:
                res = client.chat.completions.create(**_llm_call_params)
            except TypeError:
                # Older AzureOpenAI signature: swap to ``max_tokens``.
                _llm_call_params.pop("max_completion_tokens", None)
                _llm_call_params["max_tokens"] = 4096
                res = client.chat.completions.create(**_llm_call_params)
            except Exception as _param_err:
                # Some Azure deployments reject ``temperature`` (gpt-5
                # family is fixed at 1.0) or ``max_completion_tokens``.
                # Strip the offending kwargs and retry once so we never
                # regress to the old "no params at all" behaviour.
                _msg = str(_param_err).lower()
                _retried = False
                if "temperature" in _msg and "temperature" in _llm_call_params:
                    _llm_call_params.pop("temperature", None)
                    _retried = True
                if (
                    "max_completion_tokens" in _msg
                    and "max_completion_tokens" in _llm_call_params
                ):
                    _llm_call_params.pop("max_completion_tokens", None)
                    _llm_call_params["max_tokens"] = 4096
                    _retried = True
                if not _retried:
                    raise
                res = client.chat.completions.create(**_llm_call_params)
            if not res.choices:
                # Round 4 / Phase 4.7: classify "no choices" as a
                # parse/empty failure so callers can show
                # "ERROR: model returned no choices (parse_fail)"
                # instead of a silent ``None``.
                logger.error("CircuIT response had no choices (parse_fail) model=%s", self.model_name)
                return "ERROR: parse_fail: model returned no choices."
            # Round 4 / Phase 4.7: classify content_filter trips so the
            # downstream UI can surface a precise reason instead of the
            # text body which may be empty / placeholder.
            try:
                _choice0 = res.choices[0]
                _finish_for_cf = getattr(_choice0, "finish_reason", None) or (
                    getattr(_choice0, "model_extra", {}) or {}
                ).get("finish_reason")
                if str(_finish_for_cf or "").lower() in ("content_filter", "contentfilter"):
                    logger.error(
                        "CircuIT response triggered content_filter model=%s",
                        self.model_name,
                    )
                    return "ERROR: content_filter: model refused to answer (policy)."
            except (AttributeError, IndexError, TypeError) as _r13_cf_err:
                # Round 13 / Phase 11.6: previously this was a bare
                # ``except Exception: pass`` that swallowed *every*
                # error -- including programming bugs.  Narrow to the
                # exact attribute / index / type errors that the
                # SDK shape can legitimately throw, and log a warning
                # so an operator knows we silently bypassed the
                # content-filter classification step (and therefore
                # the model's refusal reason will surface as a
                # generic ``parse_fail`` instead of
                # ``content_filter`` downstream).
                try:
                    logger.warning(
                        "CircuIT content_filter classification skipped (model=%s): %s",
                        self.model_name, _r13_cf_err,
                    )
                except Exception:
                    pass
            # Round 3 / Phase 2.5: warn loudly when the completion was
            # cut short by max_tokens. ``finish_reason='length'`` means
            # the JSON / narrative we just returned is structurally
            # truncated. Callers downstream parse this as if it were
            # complete and silently drop fields. Logging the
            # truncation here gives operators a single grep target
            # ('LLM truncated') without changing the return contract.
            _content = res.choices[0].message.content
            try:
                _choice = res.choices[0]
                _finish = getattr(_choice, "finish_reason", None) or (
                    getattr(_choice, "model_extra", {}) or {}
                ).get("finish_reason")
                if str(_finish or "").lower() == "length":
                    _model_label = getattr(self, "model_name", "<unknown>")
                    logger.warning(
                        "LLM truncated (finish_reason='length') model=%s; "
                        "downstream JSON/text parsers may see partial output",
                        _model_label,
                    )
                    # Round 3 / Phase 2.5: also append an explicit
                    # user-visible marker so downstream renderers
                    # cannot present a length-truncated answer as if
                    # it were complete. The marker is plain text so
                    # readers (and tests) can grep for it; JSON
                    # parsers that ignore trailing text won't be
                    # broken by it.
                    if isinstance(_content, str):
                        _content = (
                            f"{_content}\n\n[TRUNCATED: response may be incomplete "
                            f"(finish_reason='length')]"
                        )
            except Exception as _fr_err:
                logger.debug(
                    "Could not inspect finish_reason on LLM response: %s", _fr_err
                )
            # Round 4 / Phase 6.5: emit a single structured log line per
            # successful LLM call so operators can audit which model /
            # params / finish_reason produced each answer.  Avoid logging
            # the prompt/response bodies (PII / size).
            try:
                _finish_log = getattr(res.choices[0], "finish_reason", None) or (
                    getattr(res.choices[0], "model_extra", {}) or {}
                ).get("finish_reason")
                _usage = getattr(res, "usage", None)
                _ptokens = getattr(_usage, "prompt_tokens", None) if _usage else None
                _ctokens = getattr(_usage, "completion_tokens", None) if _usage else None
                logger.info(
                    "LLM call ok model=%s temperature=%s max_tokens=%s "
                    "finish_reason=%s prompt_tokens=%s completion_tokens=%s",
                    self.model_name,
                    _llm_call_params.get("temperature", "<default>"),
                    _llm_call_params.get(
                        "max_completion_tokens",
                        _llm_call_params.get("max_tokens", "<default>"),
                    ),
                    _finish_log,
                    _ptokens,
                    _ctokens,
                )
            except Exception as _log_err:
                logger.debug("LLM call logging failed: %s", _log_err)
            return _content
        except APITimeoutError:
            # Round 6 / Phase 3.7: log + report the actual configured
            # timeout, not a stale 120s literal.
            logger.error(
                "CircuIT API call timed out after %s seconds.",
                self.REQUEST_TIMEOUT_SECONDS,
            )
            return (
                f"ERROR: llm.timeout_{self.REQUEST_TIMEOUT_SECONDS}s: "
                "The analysis for this section timed out. "
                "The AI service may be under heavy load. Please try again later."
            )
        except Exception as e:
            # Round 4 / Phase 4.7: classify common transient and
            # policy-driven failures (HTTP 429, 5xx, content_filter,
            # JSON parse) so the UI / Excel / report layer can show
            # a stable, machine-readable error instead of a silent
            # ``None`` (which prior code rendered as empty).
            try:
                _status_code = (
                    getattr(e, "status_code", None)
                    or getattr(getattr(e, "response", None), "status_code", None)
                )
            except Exception:
                _status_code = None
            _err_text = str(e) or e.__class__.__name__
            # Round 5 / Phase 6.12: namespace the CircuIT error kinds
            # under the ``llm.*`` prefix so dashboards can group LLM
            # failures consistently with the analysis-side classifier
            # (which uses ``analysis.*``).  See
            # ``error_classifier.AnalysisErrorClassification`` for the
            # full taxonomy.
            _kind = "llm.runtime"
            try:
                # Round 6 / Phase 3.10: classify HTTP 401/403 explicitly
                # so the UI can distinguish "credentials are bad / token
                # expired" from a generic upstream failure.  Without
                # this, an expired Okta token returned the same opaque
                # ``llm.runtime`` as a transient 5xx and operators had
                # no way to know they needed to rotate the token.
                _err_lower = _err_text.lower()
                if _status_code == 401 or "401" in _err_text or "unauthorized" in _err_lower:
                    _kind = "llm.unauthorized"
                elif _status_code == 403 or "403" in _err_text or "forbidden" in _err_lower:
                    _kind = "llm.forbidden"
                elif _status_code == 429 or "rate limit" in _err_lower or "429" in _err_text:
                    _kind = "llm.rate_limit_429"
                elif isinstance(_status_code, int) and 500 <= _status_code < 600:
                    _kind = f"llm.server_error_{_status_code}"
                elif "5" in str(_status_code or "") and str(_status_code or "").startswith("5"):
                    _kind = f"llm.server_error_{_status_code}"
                elif "content_filter" in _err_lower or "responsibleaipolicyviolation" in _err_lower:
                    _kind = "llm.content_filter"
                elif "json" in _err_lower and ("decode" in _err_lower or "parse" in _err_lower):
                    _kind = "llm.parse_fail"
            except Exception:
                _kind = "llm.runtime"
            logger.error(
                "CircuIT Error kind=%s status=%s err=%s",
                _kind, _status_code, _err_text,
            )
            # Map to a user-visible ERROR:<kind>: <msg>.  Callers that
            # currently treat ``None`` as "skip" will instead see a
            # classified ERROR string that the UI/Excel can surface.
            return f"ERROR: {_kind}: {_err_text}"

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
            if dashboard_heading.runs:
                dashboard_heading.runs[0].font.color.rgb = RGBColor(0, 123, 199)

            # Create figure with subplots for multiple charts
            fig = plt.figure(figsize=(12, 8))
            fig.patch.set_facecolor('white')

            # Chart 1: Portfolio Health Metrics (Top Left)
            ax1 = plt.subplot(2, 2, 1)
            # Round 11 / Phase 8.5: the previous chart shared a single
            # "Count" x-axis across "Customers", "Barriers", "TAC
            # Cases" and "BEMS" -- four mutually incomparable units.
            # Disclose the unit on each bar label so the reader does
            # not infer a comparison between, e.g., "20 customers" and
            # "20 BEMS escalations".
            _metric_units = {
                'Customers': 'customers',
                'Barriers': 'barriers',
                'TAC Cases': 'TAC cases',
                'BEMS': 'BEMS escalations',
            }
            metrics = ['Customers', 'Barriers', 'TAC Cases', 'BEMS']
            values = [
                portfolio_metrics.get('total_customers', 0),
                portfolio_metrics.get('total_barriers', 0),
                portfolio_metrics.get('total_cases', 0),
                portfolio_metrics.get('bems_count', 0)
            ]
            # Round 12 / Phase 5.5: previously this "Portfolio
            # Metrics" bar used a warm-rainbow palette
            # (``['#007BC7', '#5DBCD2', '#FFB81C', '#FF6B6B']``) where
            # the orange / red hues *also* anchor the canonical
            # ``RISK_BAND_PORTFOLIO_COLORS`` ramp on the adjacent risk
            # pie -- so the BEMS bar visually "looked" critical even
            # though it is just a count of escalations.  These four
            # bars (Customers / Barriers / TAC Cases / BEMS) are
            # mutually incomparable count units with NO ordinal risk
            # meaning, so we switch to a single-hue Cisco-blue
            # magnitude palette that cannot be mistaken for the risk
            # ramp.  Darker shades for larger values keep "magnitude"
            # readable without leaking risk semantics.
            colors = ['#0B3D67', '#1B6BA0', '#3899D1', '#7CC2EA']
            bars = ax1.barh(metrics, values, color=colors)
            ax1.set_xlabel('Count (mixed units; see per-bar label)', fontsize=10, fontweight='bold')
            ax1.set_title('Portfolio Metrics', fontsize=12, fontweight='bold')
            ax1.grid(axis='x', alpha=0.3)

            for i, (bar, value, label) in enumerate(zip(bars, values, metrics)):
                _unit = _metric_units.get(label, '')
                ax1.text(value, i, f'  {int(value)} {_unit}'.rstrip(), va='center', fontweight='bold')

            # Chart 2: Risk Distribution (Top Right)
            # Round 3: split the legacy "High Risk" wedge — which was
            # actually CRITICAL + HIGH — into two distinct slices so the
            # pie always agrees with the canonical band cut from
            # ``risk_scoring.RISK_BAND_THRESHOLDS``. If split bands are
            # not available we fall back to a clearly labeled
            # "Critical + High" wedge so users are not misled by a
            # rolled-up category labeled simply "High Risk".
            ax2 = plt.subplot(2, 2, 2)
            # Round 11 / Phase 5.2: pull the palette from the
            # canonical_metrics shared constant so the Word
            # chart's colors and any future Word-table shading
            # for the same labels can never drift apart.
            # Round 12 / Phase 5.4: previously the inline fallback dict
            # below silently shadowed any future schema change in
            # ``canonical_metrics.RISK_BAND_PORTFOLIO_COLORS`` (e.g. a
            # new "Critical + High" rollup hue).  When the import
            # succeeded the fallback was harmless, but in any partial
            # build / unit-test context where the import failed we
            # froze a stale palette into the Word doc that no longer
            # matched the canonical map -- the very drift Phase 5
            # fights.  Resolve through ``getattr`` against the
            # canonical module so the fallback is the empty dict and
            # ``risk_colors`` falls back to the per-wedge ``#7f7f7f``
            # neutral, never to a stale named hex.
            try:
                import canonical_metrics as _R12_CMOD
            except Exception:
                _R12_CMOD = None  # type: ignore
            _R11_PORTFOLIO_COLORS = getattr(
                _R12_CMOD, 'RISK_BAND_PORTFOLIO_COLORS', {}
            ) if _R12_CMOD is not None else {}
            _critical_count = portfolio_metrics.get('critical_risk_customers')
            _high_only_count = portfolio_metrics.get('high_only_risk_customers')
            if _critical_count is not None and _high_only_count is not None:
                risk_labels = ['Critical Risk', 'High Risk', 'Medium Risk', 'Low Risk', 'Healthy']
                risk_values = [
                    int(_critical_count),
                    int(_high_only_count),
                    portfolio_metrics.get('medium_risk_customers', 0),
                    portfolio_metrics.get('low_risk_customers', 0),
                    portfolio_metrics.get('healthy_customers', 0),
                ]
                risk_colors = [_R11_PORTFOLIO_COLORS.get(_lbl, '#7f7f7f') for _lbl in risk_labels]
            else:
                risk_labels = ['Critical + High', 'Medium Risk', 'Low Risk', 'Healthy']
                risk_values = [
                    portfolio_metrics.get('high_risk_customers', 0),
                    portfolio_metrics.get('medium_risk_customers', 0),
                    portfolio_metrics.get('low_risk_customers', 0),
                    portfolio_metrics.get('healthy_customers', 0),
                ]
                risk_colors = [_R11_PORTFOLIO_COLORS.get(_lbl, '#7f7f7f') for _lbl in risk_labels]
            # Round 10 / Phase 3.8: ``%1.0f%%`` (whole percent) hid every
            # slice below 1% as "0%", so the visible labels could sum to
            # less than 100 and a real 0.4% Critical band rendered "0%"
            # next to a 99.6% green wedge — actively misleading. Use a
            # custom autopct that renders "<1%" for non-zero small
            # slices and 1 decimal place otherwise so the printed
            # percents always sum to ~100.
            def _r10_autopct(p):
                if p <= 0:
                    return ''
                if p < 1.0:
                    return '<1%'
                return f'{p:.1f}%'
            # Round 11 / Phase 8.6: drop zero-count bands before
            # rendering so empty wedges (which can otherwise produce
            # confusing legend entries with no slice area) cannot
            # mislead the reader into thinking a band exists.  Keep
            # the parallel labels/values/colors arrays in sync.
            try:
                _filtered = [
                    (_lbl, int(_val), _clr)
                    for _lbl, _val, _clr in zip(risk_labels, risk_values, risk_colors)
                    if int(_val or 0) > 0
                ]
                if _filtered:
                    # Round 12 / Phase 8.4: previously the surviving
                    # wedges came out of this filter in the order of
                    # the *upstream* ``risk_labels`` list -- which was
                    # canonical for the split-band branch but could
                    # leak whichever order callers happened to assemble
                    # ``portfolio_metrics`` for the rolled-up branch.
                    # Pin the wedge order to a single canonical band
                    # constant (Critical -> Healthy, descending in
                    # severity) so the pie's clockwise sweep is
                    # identical across every report regardless of
                    # which branch supplied the values.  Unknown
                    # labels keep their relative order at the end.
                    _R12_CANON_BAND_ORDER = [
                        'Critical Risk',
                        'Critical + High',
                        'High Risk',
                        'Medium Risk',
                        'Low Risk',
                        'Healthy',
                    ]
                    _r12_band_pos = {
                        _b: _i for _i, _b in enumerate(_R12_CANON_BAND_ORDER)
                    }
                    _filtered.sort(
                        key=lambda _t: (
                            _r12_band_pos.get(_t[0], len(_R12_CANON_BAND_ORDER)),
                            _t[0],
                        )
                    )
                    risk_labels = [t[0] for t in _filtered]
                    risk_values = [t[1] for t in _filtered]
                    risk_colors = [t[2] for t in _filtered]
            except Exception as _zero_err:
                logger.debug(
                    "Round 11 / Phase 8.6: zero-band filter skipped (%s)",
                    _zero_err,
                )
            wedges, texts, autotexts = ax2.pie(risk_values, labels=risk_labels, colors=risk_colors,
                                                autopct=_r10_autopct, startangle=90)
            # Round 13 / Phase 8.1: previously this pie was rendered
            # without ``set_aspect('equal')``.  Matplotlib's default
            # ``axes.box_aspect`` is the figure's data-ratio, which
            # for a 2x2 ``plt.subplot`` cell on a non-square figure
            # quietly squashes the wedges into ovals -- a 25% slice
            # then *looks* like 30%+ at the long end and a 15% slice
            # looks like 12% at the short end.  Force a 1:1 aspect
            # ratio so wedge area is proportional to value, which is
            # the whole point of a pie chart.  Falls back silently on
            # ``Exception`` so a stub ``ax2`` does not crash the
            # report.
            try:
                ax2.set_aspect('equal')
            except Exception:
                pass
            for text in texts:
                text.set_fontsize(9)
            for autotext in autotexts:
                autotext.set_color('white')
                autotext.set_fontweight('bold')
            ax2.set_title('Customer Risk Distribution', fontsize=12, fontweight='bold')

            # Chart 3: Case Severity Distribution (Bottom Left)
            ax3 = plt.subplot(2, 2, 3)
            severity_labels = ['P1 Critical', 'P2 High', 'P3 Medium', 'P4+ Low']
            p1_cases = int(portfolio_metrics.get('critical_p1', portfolio_metrics.get('p1_cases', 0)) or 0)
            p2_cases = int(portfolio_metrics.get('high_p2', portfolio_metrics.get('p2_cases', 0)) or 0)
            p3_cases = int(portfolio_metrics.get('p3_cases', 0) or 0)
            p4_cases = int(portfolio_metrics.get('p4_cases', 0) or 0)
            severity_values = [
                p1_cases,
                p2_cases,
                p3_cases,
                p4_cases,
            ]
            # Round 12 / Phase 5.2: previously the TAC severity bar
            # used an ad-hoc inline palette
            # (``['#FF0000', '#FF6B6B', '#FFB81C', '#5DBCD2']``) that
            # disagreed with the canonical ``SEVERITY_COLORS`` dict
            # used by every other priority chart in
            # ``app_simple.py`` (P1=#d62728, P2=#ff7f0e, P3=#ffd700,
            # P4=#2ca02c).  The same P1 bucket therefore rendered
            # bright red here but a deeper red in the executive
            # case-mix pie a few pages later, breaking the visual
            # legend.  Resolve every bar color from the same shared
            # constant so the Word doc's bar agrees with every pie.
            try:
                from app_simple import SEVERITY_COLORS as _R12_SEV_COLORS
                from app_simple import SEVERITY_COLOR_DEFAULT as _R12_SEV_DEFAULT
            except Exception:
                _R12_SEV_COLORS = {
                    'P1': '#d62728',
                    'P2': '#ff7f0e',
                    'P3': '#ffd700',
                    'P4': '#2ca02c',
                }
                _R12_SEV_DEFAULT = '#1f77b4'
            severity_colors = [
                _R12_SEV_COLORS.get('P1', _R12_SEV_DEFAULT),
                _R12_SEV_COLORS.get('P2', _R12_SEV_DEFAULT),
                _R12_SEV_COLORS.get('P3', _R12_SEV_DEFAULT),
                _R12_SEV_COLORS.get('P4', _R12_SEV_DEFAULT),
            ]
            bars = ax3.bar(severity_labels, severity_values, color=severity_colors)
            ax3.set_ylabel('Count', fontsize=10, fontweight='bold')
            ax3.set_title('TAC Case Severity', fontsize=12, fontweight='bold')
            ax3.grid(axis='y', alpha=0.3)
            plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

            # Round 13 / Phase 8.2: previously the TAC severity bar
            # encoded priority entirely through colour.  That meant
            # readers with deuteranopia / protanopia (~5% of the
            # male population) saw the P1 (red) and P3 (yellow)
            # bars as nearly the same hue, and ad-hoc readers had
            # no legend mapping P1->Critical / P2->High / P3->Medium
            # / P4->Low.  Add a small legend keyed by the same
            # canonical SEVERITY_COLORS palette already used by the
            # bars so the chart is self-describing without relying
            # on colour alone, and so the legend stays aligned with
            # the rest of the report's priority palette.  The
            # ``rectangle`` proxy artists let us emit a discrete
            # legend without an extra call to ``ax3.bar``.
            try:
                import matplotlib.patches as _r13_mpatches
                _r13_handles = [
                    _r13_mpatches.Patch(color=severity_colors[0], label='P1 - Critical'),
                    _r13_mpatches.Patch(color=severity_colors[1], label='P2 - High'),
                    _r13_mpatches.Patch(color=severity_colors[2], label='P3 - Medium'),
                    _r13_mpatches.Patch(color=severity_colors[3], label='P4+ Low'),
                ]
                ax3.legend(
                    handles=_r13_handles,
                    loc='upper right',
                    fontsize=7,
                    framealpha=0.85,
                    title='Priority',
                    title_fontsize=8,
                )
            except Exception as _r13_legend_err:
                logger.debug(
                    "Round 13 / Phase 8.2: TAC severity legend skipped (%s)",
                    _r13_legend_err,
                )

            # Add value labels on bars
            for bar, value in zip(bars, severity_values):
                height = bar.get_height()
                ax3.text(bar.get_x() + bar.get_width()/2., height,
                        f'{int(value)}', ha='center', va='bottom', fontweight='bold')

            # Chart 4: Trend Indicator (Bottom Right)
            ax4 = plt.subplot(2, 2, 4)
            # Round 3 / Phase 1.3: do not fabricate "Stable" when no
            # comparable prior-period trend has been computed. If the
            # caller did not provide a trend, render "n/a" so a
            # reader cannot mistake a default for analytics.
            _trend_raw = portfolio_metrics.get('trend_direction')
            trend_data = (
                str(_trend_raw)
                if _trend_raw not in (None, '', 'None')
                else 'n/a (not computed)'
            )
            health_score = portfolio_metrics.get('health_score', 'C')

            # Create a simple gauge/indicator
            ax4.text(0.5, 0.6, f'Portfolio Health', ha='center', va='center',
                    fontsize=14, fontweight='bold')
            # Round 13 / Phase 5.7: resolve the Grade colour through
            # ``canonical_metrics.HEALTH_GRADE_COLORS`` instead of
            # inlining the same ``#28B463``/``#FFB81C``/``#FF6B6B``
            # ternary that drifted from
            # ``RISK_BAND_PORTFOLIO_COLORS``.  Future palette tweaks
            # land in exactly one place.
            try:
                from canonical_metrics import get_health_grade_color as _r13_grade_color
                _r13_grade_color_hex = _r13_grade_color(str(health_score))
            except Exception:
                _r13_grade_color_hex = (
                    '#28B463' if health_score in ['A', 'B']
                    else '#FFB81C' if health_score == 'C'
                    else '#FF6B6B'
                )
            ax4.text(0.5, 0.4, f'Grade: {health_score}', ha='center', va='center',
                    fontsize=32, fontweight='bold',
                    color=_r13_grade_color_hex)
            ax4.text(0.5, 0.2, f'Trend: {trend_data}', ha='center', va='center',
                    fontsize=12, style='italic')
            ax4.set_xlim(0, 1)
            ax4.set_ylim(0, 1)
            ax4.axis('off')
            fig.text(
                0.02,
                0.01,
                f"BEMS split: break-fix={portfolio_metrics.get('break_fix_cases', 0)} | provisioning={portfolio_metrics.get('provisioning_cases', 0)}",
                fontsize=9,
            )

            # Round 12 / Phase 8.2: ``fig.text`` placed at y=0.01 risks
            # being clipped by ``tight_layout()`` on some matplotlib
            # backends (especially when the four subplots already eat
            # the bottom margin).  Reserve an explicit bottom strip
            # via ``rect=[left, bottom, right, top]`` so the BEMS
            # split caption is guaranteed to render in the saved PNG.
            try:
                plt.tight_layout(rect=[0, 0.04, 1, 0.98])
            except Exception:  # Round 12 / Phase 8.2 defensive
                plt.tight_layout()

            # Save chart to bytes
            img_stream = io.BytesIO()
            # Round 12 / Phase 8.1: previously this was ``dpi=150`` while
            # every ``app_simple.py`` chart (renewal donut, executive
            # health, panels 1-4) saved at ``dpi=300``.  When both
            # families of PNGs ended up in the same Word doc the
            # backend's executive dashboard appeared visibly softer
            # than the renewal panels on a high-DPI display, breaking
            # the at-a-glance polish of the report.  Unify on 300 so
            # all chart families render at the same target resolution.
            plt.savefig(img_stream, format='png', dpi=300, bbox_inches='tight')
            img_stream.seek(0)
            # Round 13 / Phase 8.5: previously this was ``plt.close()``
            # which closes "the current figure" -- i.e. whichever
            # figure was last activated by matplotlib globally.  In
            # a server process that builds many charts back-to-back
            # (and especially when called from worker threads) the
            # current-figure pointer can race with the figure we
            # actually want to release, leading to slow figure leaks
            # over the lifetime of a long-running daemon.  Pass the
            # explicit ``fig`` handle so we always release the exact
            # figure we just rendered, regardless of any concurrent
            # ``plt.figure()`` calls in other threads.
            try:
                plt.close(fig)
            except Exception:
                plt.close()

            # Add chart to document
            # Round 13 / Phase 8.6 + 9.10: previously ``add_picture``
            # was called without an alt-text follow-up, so the
            # embedded portfolio dashboard PNG had no docPr
            # description.  Screen readers and Word's Accessibility
            # Checker therefore announced only the BytesIO/temp name,
            # losing all chart context.  Stamp a static alt text
            # describing the four-panel dashboard so accessibility
            # tooling and exported HTML/PDF copies announce it as
            # "Portfolio Dashboard - At-A-Glance".
            _r13_dashboard_picture = doc.add_picture(img_stream, width=Inches(6.5))
            try:
                _r13_set_picture_alt_text(
                    _r13_dashboard_picture,
                    "Portfolio Dashboard - At-A-Glance: four-panel chart showing "
                    "portfolio health metrics, customer risk distribution, TAC case "
                    "severity, and team summary.",
                )
            except Exception:
                pass

            # Add space after chart
            doc.add_paragraph()

            unknown_priority_cases = int(portfolio_metrics.get('unknown_priority_cases', 0) or 0)
            if unknown_priority_cases > 0:
                note = doc.add_paragraph()
                note.add_run('Severity Mapping Note: ').bold = True
                note.add_run(
                    f'{unknown_priority_cases} TAC case(s) had unknown or non-standard priority labels and are excluded from P1/P2/P3/P4 buckets.'
                )

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
            # Round 3 / Phase 1.4: ``high_risk_customers`` from
            # ``compute_portfolio_risk_summary`` is band CRITICAL +
            # band HIGH (see risk_scoring.py:609). Labeling that cell
            # "High Risk" understates Critical exposure to readers.
            # Relabel honestly as "Critical + High" so the cell value
            # matches its caption.
            cells = table.rows[1].cells
            cells[0].text = f"🔴 Critical + High\n{portfolio_metrics.get('high_risk_customers', 0)}"
            cells[1].text = f"🟡 Medium Risk\n{portfolio_metrics.get('medium_risk_customers', 0)}"
            cells[2].text = f"🟢 Low Risk\n{portfolio_metrics.get('low_risk_customers', 0)}"
            cells[3].text = f"✅ Healthy\n{portfolio_metrics.get('healthy_customers', 0)}"

            # Row 3: Severity
            cells = table.rows[2].cells
            p1_cases = int(portfolio_metrics.get('critical_p1', portfolio_metrics.get('p1_cases', 0)) or 0)
            p2_cases = int(portfolio_metrics.get('high_p2', portfolio_metrics.get('p2_cases', 0)) or 0)
            cells[0].text = f"P1 Critical\n{p1_cases}"
            cells[1].text = f"P2 High\n{p2_cases}"
            cells[2].text = f"Break-fix / Provisioning\n{portfolio_metrics.get('break_fix_cases', 0)} / {portfolio_metrics.get('provisioning_cases', 0)}"
            # Round 3 / Phase 1.3: avoid the hardcoded 'Stable' default
            # so the cell honestly reads "n/a (not computed)" when no
            # comparable prior-period trend has been wired in.
            _trend_cell_raw = portfolio_metrics.get('trend_direction')
            _trend_cell = (
                str(_trend_cell_raw)
                if _trend_cell_raw not in (None, '', 'None')
                else 'n/a (not computed)'
            )
            cells[3].text = f"Grade: {portfolio_metrics.get('health_score', 'C')}\nTrend: {_trend_cell}"

            # Style the table
            for row in table.rows:
                for cell in row.cells:
                    cell.vertical_alignment = 1  # Center
                    for paragraph in cell.paragraphs:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for run in paragraph.runs:
                            run.font.size = Pt(11)
                            run.font.bold = True

            unknown_priority_cases = int(portfolio_metrics.get('unknown_priority_cases', 0) or 0)
            if unknown_priority_cases > 0:
                note = doc.add_paragraph()
                note.add_run('Severity Mapping Note: ').bold = True
                note.add_run(
                    f'{unknown_priority_cases} TAC case(s) had unknown or non-standard priority labels and are excluded from P1/P2/P3/P4 buckets.'
                )

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
        if title.runs:
            title.runs[0].font.size = Pt(28)
            title.runs[0].font.bold = True
            title.runs[0].font.color.rgb = RGBColor(0, 123, 199)

        # Subtitle
        subtitle = doc.add_paragraph(f"{technology} Executive Analysis")
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if subtitle.runs:
            subtitle.runs[0].font.size = Pt(18)
            subtitle.runs[0].font.color.rgb = RGBColor(100, 100, 100)

        doc.add_paragraph()

        # Key metrics box (if provided)
        if portfolio_metrics:
            metrics_para = doc.add_paragraph()
            metrics_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # Round 12 / Phase 10.5: previously stamped the title page
            # via ``datetime.now().strftime(...)`` (HOST-LOCAL clock,
            # no timezone marker).  An operator running the same plan
            # in EU vs. PT could see a "Report Date" 12 hours apart
            # for the same logical run, breaking cross-region audit
            # parity.  Anchor on UTC and label the timezone so the
            # title page matches the UTC ISO-Z timestamps Round 5 /
            # Phase 6.3 already standardized for ``store_report_history``
            # and Round 12 / Phase 10.4 standardized for the admin
            # persisted columns.
            _r12_now_utc = datetime.now(timezone.utc)
            metrics_text = f"""
Analysis Period: {days} Days
Report Date: {_r12_now_utc.strftime("%B %d, %Y")} UTC
"""
            # Round 10 / Phase 3.9: previously these used truthy checks
            # which silently dropped legitimate zero values. A portfolio
            # with zero open barriers is a meaningful signal — not "no
            # data" — and should still render "Active Barriers: 0" on
            # the title page so the operator can confirm the analysis
            # actually ran. Switch to explicit key-presence checks.
            if 'total_customers' in portfolio_metrics and portfolio_metrics['total_customers'] is not None:
                metrics_text += f"\nTotal Customers: {portfolio_metrics['total_customers']}"
            if 'total_barriers' in portfolio_metrics and portfolio_metrics['total_barriers'] is not None:
                # Round 53: this value is the distinct total barrier count,
                # not the active/open-only count.
                metrics_text += f"\nTotal Adoption Barriers: {portfolio_metrics['total_barriers']}"
            if 'total_cases' in portfolio_metrics and portfolio_metrics['total_cases'] is not None:
                metrics_text += f"\nSupport Cases: {portfolio_metrics['total_cases']}"

            metrics_para.add_run(metrics_text.strip())
            if metrics_para.runs:
                metrics_para.runs[0].font.size = Pt(12)
                metrics_para.runs[0].font.color.rgb = RGBColor(60, 60, 60)
        else:
            # Round 12 / Phase 10.5: anchor on UTC and label the timezone so
            # the fallback meta line is reproducible across hosts (see the
            # matching change in the ``portfolio_metrics`` branch above).
            meta = doc.add_paragraph(
                f"Analysis Period: {days} Days\n"
                f"Report Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')} UTC"
            )
            meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if meta.runs:
                meta.runs[0].font.size = Pt(12)
                meta.runs[0].font.color.rgb = RGBColor(100, 100, 100)

        doc.add_paragraph()
        doc.add_paragraph()

        notice = doc.add_paragraph("CONFIDENTIAL - Executive Leadership Review")
        notice.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if notice.runs:
            notice.runs[0].font.size = Pt(10)
            notice.runs[0].font.italic = True
            notice.runs[0].font.color.rgb = RGBColor(150, 150, 150)

        # Page break after title
        doc.add_page_break()

        # Round 15 / Phase 3.6: the gold docx had no executive
        # summary table at the top of the report -- the only KPI
        # surface was the buried 3x4 "Portfolio Dashboard" table,
        # which has no parity with the Excel ``Summary`` sheet.
        # Render a dedicated Heading-2 summary table here so the
        # first thing on page 2 of the report is the same KPIs the
        # workbook leads with.  Defensive: any failure logs and
        # falls through silently.
        try:
            import report_word_styling as _r15_word_styling  # noqa: PLC0415
            _r15_metrics = portfolio_metrics or {}
            _r15_kpi_rows: list[tuple[str, str]] = [
                ("Manager scope", str(manager) if manager else "All"),
                ("Technology scope", str(technology) if technology else "All"),
                ("Window (days)", str(days) if days is not None else "--"),
            ]
            for _r15_label, _r15_key in (
                ("Customers in portfolio", "total_customers"),
                ("Adoption barriers (total)", "total_barriers"),
                ("TAC cases (total)", "total_cases"),
                ("BEMS / break-fix", "bems_count"),
                ("Critical + High risk", "high_risk_customers"),
                ("Medium risk", "medium_risk_customers"),
                ("Low risk", "low_risk_customers"),
                ("Healthy", "healthy_customers"),
                ("P1 / Critical cases", "critical_p1"),
                ("P2 / High cases", "high_p2"),
            ):
                if _r15_key in _r15_metrics and _r15_metrics[_r15_key] is not None:
                    _r15_kpi_rows.append((_r15_label, str(_r15_metrics[_r15_key])))
            _r15_word_styling.add_executive_summary_table(
                doc,
                _r15_kpi_rows,
                title="Executive Summary",
                title_level=2,
            )
            doc.add_paragraph()
        except Exception as _r15_summary_err:
            logger.debug(
                "Round 15 / Phase 3.6: executive summary table skipped (%s)",
                _r15_summary_err,
            )

    except Exception as e:
        # If title page creation fails, just continue - don't break the report
        logger.warning(f"Could not create title page: {e}")
        pass

# Round 11 / Phase 9.4: shared regex for ordered-list ordinals so
# items >= 10 (e.g., "10. Action item") render as numbered list
# entries instead of falling through to plain paragraphs.
_RE_NUMBERED_LIST_ITEM = re.compile(r'^\d+[\.\)]\s')


# Round 78 / B1 - Comprehensive narrative stub-bullet filter.
# Build 53 acceptance audit found the per-customer storyboard LLM
# emitting ~170 stub bullets like "Industry Benchmarking: Data
# unavailable." per Comprehensive run.  These come from the
# PROMPT_CUSTOMER_TEMPLATE asking the model to fill 8 sub-categories
# (Industry Benchmarking, Technical Competency, Adoption Velocity,
# Competitive Positioning, Integration Complexity, Operational
# Disruption, Sentiment Indicators, Communication Patterns) and the
# briefing not having data for many of them.  The bullets are
# accurate (LLM honestly admits the gap) but they're noise -- when
# the line is *only* "<Category>: Data unavailable." we want to
# suppress the bullet so the user sees only sub-categories with real
# signal.  Critical contract: the regex matches ONLY when the entire
# bullet is the stub.  Bullets like "Operational Disruption: Data
# unavailable. No active incidents." (substantive narrative after
# the marker) and "Strategic Headwinds: ... (SP-ID: data
# unavailable) ..." (parenthetical, not the whole bullet) MUST be
# preserved.  See ``_R78_STUB_RE`` doctest in the regression file.
_R78_STUB_RE = re.compile(
    r'^\*{0,2}[A-Z][\w &/\-]+\*{0,2}\s*:\s*\*{0,2}\s*[Dd]ata\s+[Uu]navailable'
    r'\s*\*{0,2}\.?\s*\*{0,2}\s*$',
)


# Round 27 - LLM compliance leak post-processor.
# The PROMPT_CUSTOMER_TEMPLATE includes the literal string
# ``Customer Health Score: [A, B, C, D, F]`` (and similar bracketed
# enumerations) as instructions to the model -- the LLM is supposed
# to substitute its chosen letter / value.  Across runs the LLM
# almost always complies cleanly, but occasionally preserves the
# brackets verbatim (e.g., ``Customer Health Score: [C]``), which
# leaks the prompt scaffolding into the executive Word report.
#
# Anchored on the ``Customer Health Score:`` label so we cannot
# accidentally rewrite legitimate bracketed evidence (e.g., the
# ``[Theme Name]`` placeholders the LLM correctly fills with
# bracket-stripped content elsewhere in the same report).
_RE_HEALTH_GRADE_BRACKETS = re.compile(
    r'(Customer Health Score:\s*)\[\s*([A-Fa-f])\s*\]'
)


def _sanitize_llm_grade_brackets(text: str) -> str:
    """Round 27: strip stray brackets around the customer health grade.

    Returns ``text`` unchanged when no leak is present; idempotent on
    already-clean input.  Case is preserved for the grade letter so
    an unexpected lowercase grade still survives the rewrite (the
    next layer can decide whether to upper-case).

    Pure function; no I/O.  Public-ish (single underscore prefix)
    only because tests exercise it directly to pin the contract.
    """
    if not isinstance(text, str) or not text:
        return text
    return _RE_HEALTH_GRADE_BRACKETS.sub(r'\1\2', text)


def append_to_word_report(doc_or_path, markdown_content: str, heading: str = None):
    """Enhanced Word report writer with professional executive-ready formatting - removes ALL markdown symbols"""

    # Round 15 / Phase 3.1: lazy-import the Word styling SSoT so the
    # markdown -> Heading-N mapping lives in exactly one place
    # (``report_word_styling.markdown_heading_level``) and the
    # heading-discipline regression test can pin the contract there.
    import report_word_styling as _r15_word_styling

    if not isinstance(markdown_content, str) or not markdown_content.strip():
        return

    # Round 27: post-process LLM scaffold leaks (bracketed grade
    # letters) before any of the per-line markdown passes below.
    # See ``_sanitize_llm_grade_brackets`` for rationale.
    markdown_content = _sanitize_llm_grade_brackets(markdown_content)

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
        except Exception as _margin_err:
            logger.debug(f"Margin setup skipped: {_margin_err}")

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
            except Exception as _style_err:
                logger.debug(f"Heading style setup skipped for level {level}: {_style_err}")

        # Configure List Bullet style for professional appearance
        try:
            bullet_style = doc.styles['List Bullet']
            bullet_style.font.name = 'Calibri'
            bullet_style.font.size = Pt(11)
            bullet_style.paragraph_format.space_after = Pt(4)
            bullet_style.paragraph_format.line_spacing = 1.15
            bullet_style.paragraph_format.left_indent = Inches(0.25)
        except Exception as _bs_err:
            logger.debug("List Bullet style config skipped: %s", _bs_err)

        # Configure List Number style
        try:
            number_style = doc.styles['List Number']
            number_style.font.name = 'Calibri'
            number_style.font.size = Pt(11)
            number_style.paragraph_format.space_after = Pt(4)
            number_style.paragraph_format.line_spacing = 1.15
            number_style.paragraph_format.left_indent = Inches(0.25)
        except Exception as _ns_err:
            logger.debug("List Number style config skipped: %s", _ns_err)

    except Exception as _style_err:
        logger.debug("Document style configuration skipped: %s", _style_err)

    # Round 15 / Phase 3.1: the section heading argument used to land
    # on Heading 1, so every per-customer / per-section ``append_to_word_report``
    # call stamped another H1 entry into the docx (the gold report
    # had 38 of them).  Heading 1 is reserved for the document title
    # set by ``create_executive_title_page``; section headings
    # passed in here drop to Heading 2 so the document has a proper
    # H1 -> H2 -> H3 hierarchy.
    if heading:
        doc.add_heading(heading, level=_r15_word_styling.MIN_BODY_HEADING_LEVEL)

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

        # Round 15 / Phase 3.1: previously every markdown ``# heading``
        # in LLM-generated content collapsed onto Heading 1, producing
        # the 38-H1 gold docx (and a TOC where every customer section
        # was a top-level chapter).  Reserve Heading 1 for the
        # document title page only and demote ``# / ## / ### / ####``
        # by one level via ``report_word_styling.markdown_heading_level``
        # so the document body has a real H2 -> H3 -> H4 hierarchy.
        # Handle headings - remove ALL # and ** symbols completely
        if line.startswith('#####'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=_r15_word_styling.markdown_heading_level(5))
            if h.runs:
                h.runs[0].font.size = Pt(11)
                h.runs[0].font.bold = True
                h.runs[0].font.color.rgb = RGBColor(0, 123, 199)
        elif line.startswith('####'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=_r15_word_styling.markdown_heading_level(4))
            if h.runs:
                h.runs[0].font.size = Pt(11)
                h.runs[0].font.bold = True
                h.runs[0].font.color.rgb = RGBColor(0, 123, 199)
        elif line.startswith('###'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=_r15_word_styling.markdown_heading_level(3))
        elif line.startswith('##'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=_r15_word_styling.markdown_heading_level(2))
        elif line.startswith('#'):
            heading_text = line.lstrip('#').strip().replace('**', '')
            h = doc.add_heading(heading_text, level=_r15_word_styling.markdown_heading_level(1))

        # Handle bullet points - remove ALL markdown symbols
        elif line.startswith(('* ', '- ', '• ')):
            bullet_text = line[2:].strip()
            # Round 78 / B1: drop pure "<Category>: Data unavailable." stub
            # bullets so the Comprehensive narrative isn't drowned in LLM-
            # honest empty acknowledgements (Build 53 audit: 170 stubs).
            # Match constraint enforces that the entire bullet must be the
            # stub -- bullets with substantive follow-on narrative are
            # preserved.  Mirror the empty-line-skip convention at line
            # 8313 (i += 1 + continue) so the while-loop doesn't infinite-
            # loop on the unincremented index.
            if _R78_STUB_RE.match(bullet_text):
                i += 1
                continue
            # Create paragraph and use helper to process ** symbols
            p = doc.add_paragraph(style='List Bullet')
            p.clear()
            clean_and_format_text(bullet_text, p)
            try:
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15
            except Exception as _fmt_err:
                logger.debug("Bullet paragraph format skipped: %s", _fmt_err)

        # Handle numbered lists - remove ALL markdown symbols
        # Round 11 / Phase 9.4: previous match required the digit at
        # position 0 followed by ``. `` / ``) `` at positions 1-2,
        # which only catches single-digit ordinals 0-9.  Items 10, 11,
        # 12, ... fell through to plain paragraphs and broke list
        # formatting.  Use a regex that matches one-or-more digits.
        elif _RE_NUMBERED_LIST_ITEM.match(line):
            _ord_match = _RE_NUMBERED_LIST_ITEM.match(line)
            list_text = line[_ord_match.end():].strip()
            # Create paragraph and use helper to process ** symbols
            p = doc.add_paragraph(style='List Number')
            p.clear()
            clean_and_format_text(list_text, p)
            try:
                p.paragraph_format.space_after = Pt(6)
                p.paragraph_format.line_spacing = 1.15
            except Exception as _fmt_err:
                logger.debug("Numbered list paragraph format skipped: %s", _fmt_err)

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
            except Exception as _fmt_err:
                logger.debug("Paragraph format skipped: %s", _fmt_err)

        i += 1

    # Save the document if we created it from a path
    if should_save and file_path:
        # Round 68 / Build 42 (A1): stamp the build label in the section
        # footer so an auditor can spot a stale-binary report.  Helper is
        # internally defensive (logs at debug on failure); we still wrap
        # so a python-docx API drift cannot brick the save.
        try:
            from _r68_build_label import apply_word_footer as _r68_apply_word_footer  # noqa: PLC0415
            _r68_apply_word_footer(doc)
        except Exception as _r68_err:  # noqa: BLE001
            # Round 73 / Phase 1 (F1): promoted to warning so the next
            # missing-footer regression surfaces in the admin error log
            # instead of hiding under the default debug threshold.
            logger.warning("Round 68 / A1: append_to_word_report footer skipped: %s", _r68_err)
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
        # Round 15 / Phase 5.1: previously this default filename was
        # stamped via ``datetime.now()`` (HOST-LOCAL clock, no tz
        # suffix).  Two operators running the same workbook export in
        # different regions on the same UTC second got *different*
        # filenames -- and a UK operator who ran it during BST got a
        # filename one hour ahead of a colleague in UTC.  Anchor on
        # UTC and append a ``Z`` so the suffix matches the UTC ISO-Z
        # timestamps Round 5 / Phase 6.3 + Round 12 / Phase 10.4
        # standardised for ``store_report_history`` and the admin
        # persisted columns.  Behaviour-preserving for any caller
        # that supplies its own filename via the other branches.
        base_path = f"report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}Z"
    else:
        # Default case - assume it's a path and sheets
        base_path = str(sheets_or_path)
        sheets = title_or_sheets if title_or_sheets else {}

    # Round 25 / Phase D: prior to this round the writer wrapped the
    # call body in a `try: from enhanced_excel_formatter ... except: ...`
    # fallback.  ``enhanced_excel_formatter`` was never present in the
    # repository (verified with ``rg`` -- there is no module file, no
    # entry-point, no historical commit that ever shipped one), so every
    # report fell through to the ``except`` branch and the generated
    # ``Report_Info`` sheet announced ``Export type: Standard (fallback)``
    # plus a reassuring note that ``the enhanced formatter was not
    # available``.  That framing was dishonest -- the inline body is the
    # canonical writer and there is no enhanced path to fall back from.
    # Promote the body to the function-level path and rewrite the
    # Report_Info row to reflect reality (see lower in this function).
    csconsole_sheet_names = {
        "action_plans": "CSConsole_Action_Plans",
        "customer_pulse": "CSConsole_Customer_Pulse",
        "success_priorities": "CSConsole_Success_Priorities",
        "adoption_barriers": "CSConsole_Adoption_Barriers",
    }
    import numpy as _np

    def _defang_formulas(df: pd.DataFrame) -> pd.DataFrame:
        """Prefix string cells starting with =, +, -, or @ with a quote to prevent Excel formula injection."""
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].apply(
                    lambda v: "'" + v if isinstance(v, str) and v and v[0] in ('=', '+', '-', '@') else v
                )
        return df

    def _r12_sanitize_sheet_name(raw) -> str:
        """Round 12 / Phase 9.8: produce a sheet name Excel will
        accept.  Excel rejects ``[`` ``]`` ``:`` ``*`` ``?``
        ``/`` ``\\`` and the leading/trailing apostrophe, and
        silently truncates beyond 31 characters.  Strip the
        invalid characters to ``_`` (preserving readability)
        then truncate to 31.  Fall back to ``"Sheet"`` for
        empty / None / all-invalid inputs so the writer never
        fails on a degenerate name.
        """
        try:
            _name = str(raw) if raw is not None else "Sheet"
        except Exception:
            _name = "Sheet"
        for _bad in ('[', ']', ':', '*', '?', '/', '\\'):
            _name = _name.replace(_bad, '_')
        _name = _name.strip("'").strip()
        if not _name:
            _name = "Sheet"
        return _name[:31]
    with pd.ExcelWriter(f"{base_path}.xlsx", engine="xlsxwriter") as xw:
        # Round 15 / Phase 2.4: Summary sheet first.  This is the
        # tab the recipient lands on when they double-click the
        # file; pre-Round-15 they landed on ``Report_Info`` (which
        # was a 3-row metadata stub).  KPIs are sourced from the
        # ``canonical_metrics`` helpers so the workbook summary
        # never disagrees with the Word-report narrative.
        try:
            _r15_summary_ts: str | None = None
            try:
                _r15_summary_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                _r15_summary_ts = None
            _r15_write_summary_sheet(
                xw,
                sheets,
                csconsole_data,
                manager=manager,
                tech=technology,
                days=days,
                generated_at_utc_iso_z=_r15_summary_ts,
            )
        except Exception as _summary_err:
            logger.debug("Round 15 summary sheet skipped: %s", _summary_err)

        # Round 15 / Phase 2.5: workbook-wide table-name registry
        # so the polish pass can guarantee uniqueness across every
        # sheet (Excel rejects duplicate Table names).
        _r15_used_table_names: set[str] = set()

        # Round 25 / Phase D: rewrite the metadata rows to reflect
        # reality.  Pre-Round-25 these said ``Standard (fallback)`` plus
        # ``the enhanced formatter was not available`` -- but that
        # ``enhanced_excel_formatter`` module never existed, so every
        # report shipped with the same misleading framing.  Drop the
        # ``(fallback)`` suffix and replace the apologetic note with a
        # neutral generation-timestamp record.  ``Generated at (UTC)``
        # is computed inline so timezone drift on the host clock does
        # not desync the metadata from the workbook's other UTC-Z
        # stamps (Summary sheet, filename suffix, etc.).
        try:
            _r25d_generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            _r25d_generated_at = ""
        # Round 66 / Pass 2 (B5): the Comprehensive XLSX (this writer's
        # primary consumer) was missing the ``Sheet_Title:<sheet>``
        # parity rows that the Compact / Renewal / Leader writers
        # already emit (R65 / R-1). Without them, a downstream
        # ``pd.read_excel`` consumer of the Comprehensive workbook has
        # no in-band way to retrieve the descriptive title for each
        # data sheet (the data sheets themselves carry only column
        # headers in row 0, no merged title row above). Build 38
        # acceptance smoke surfaced this as a parity gap. Pre-stamp
        # one row per data sheet so the consumer can recover the
        # title from ``Item == 'Sheet_Title:<sheet>'``. Defensive: a
        # malformed sheet name cannot break Report_Info because
        # ``str()`` is wrapped.
        _r66_b5_report_info_rows: list[list[str]] = [
            ["Export type", "Standard"],
            ["Generated at (UTC)", _r25d_generated_at],
            ["Manager", str(manager) if manager is not None else ""],
            ["Technology", str(technology) if technology is not None else ""],
            ["Days", str(days) if days is not None else ""],
        ]
        # Round 68 / Build 42 (A1): stamp App_Version / App_Build /
        # Process_Started_At_UTC / Report_Generated_At_UTC so an auditor
        # opening this Comprehensive XLSX can spot a stale-binary report
        # at a glance.  Build 41 acceptance shipped reports from a
        # pre-Build-41 process because the operator did not quit and
        # re-launch after install -- without this label the user had no
        # in-band way to spot it.  The helper itself never raises (logs
        # at debug on failure) so a missing label cannot brick the
        # Report_Info write.
        try:
            from _r68_build_label import append_build_label_rows_pairs as _r68_append_pairs  # noqa: PLC0415

            _r68_append_pairs(_r66_b5_report_info_rows)
        except Exception as _r68_err:  # noqa: BLE001
            logger.debug("Round 68 / A1: build label append skipped: %s", _r68_err)
        try:
            _r66_b5_data_sheet_names: list[str] = []
            # Round 73 / Phase 3 (F8): pre-R73 the Sheet_Title row generator
            # iterated ONLY ``(sheets or {}).keys()`` and explicitly excluded
            # ``Summary`` -- but the Comprehensive writer ALSO writes the R15
            # ``Summary`` sheet via ``_r15_write_summary_sheet`` (above) and
            # the CSConsole sheets (``CSConsole_Action_Plans`` /
            # ``CSConsole_Customer_Pulse`` / ``CSConsole_Success_Priorities`` /
            # ``CSConsole_Adoption_Barriers``) via the separate
            # ``csconsole_sheet_names`` write loop below. Neither set was
            # represented in ``sheets`` at this point, so Report_Info shipped
            # without ``Sheet_Title:Summary`` or any ``Sheet_Title:CSConsole_*``
            # rows. Build 46 audit found this as a parity gap with the R65/R-1
            # Compact / Renewal / Leader writers (which DO carry
            # ``Sheet_Title:Summary``). The fix below stamps both sets up
            # front so the operator's ``pd.read_excel`` consumer recovers the
            # title for every written sheet.
            #
            # Always backfill ``Summary`` -- ``_r15_write_summary_sheet`` is
            # called unconditionally above. ``Report_Info`` itself stays
            # excluded (it would be self-referential and the operator would
            # never need a title for it).
            _r66_b5_data_sheet_names.append("Summary")
            for _sheet_name in (sheets or {}).keys():
                try:
                    _safe_name = str(_sheet_name) if _sheet_name is not None else ""
                except Exception:
                    _safe_name = ""
                if _safe_name and _safe_name not in {"Summary", "Report_Info"}:
                    _r66_b5_data_sheet_names.append(_safe_name)
            # R73 / F8: also iterate the csconsole_sheet_names mapping and
            # pre-stamp a title row for any sheet whose source DataFrame is
            # non-empty (matches the same gating the actual writer below
            # uses at L8862: ``if df is not None and ... and not df.empty``).
            # This is intentionally separate from the ``sheets`` loop so a
            # future refactor that moves CSConsole writes elsewhere does
            # not silently re-introduce the parity gap.
            try:
                if csconsole_data:
                    for _ck, _csname in csconsole_sheet_names.items():
                        try:
                            _cdf = csconsole_data.get(_ck)
                        except Exception:
                            _cdf = None
                        if (
                            _cdf is not None
                            and hasattr(_cdf, "empty")
                            and not _cdf.empty
                            and _csname
                            and _csname not in {"Summary", "Report_Info"}
                            and _csname not in _r66_b5_data_sheet_names
                        ):
                            _r66_b5_data_sheet_names.append(_csname)
            except Exception as _r73_f8_err:  # noqa: BLE001
                logger.debug(
                    "Round 73 / F8: csconsole Sheet_Title backfill skipped: %s",
                    _r73_f8_err,
                )
            for _safe_name in _r66_b5_data_sheet_names:
                # Title format mirrors the R65/R-1 Compact pattern:
                # ``"<Sheet Name> - <Manager> Portfolio Analysis"``.
                _safe_manager = str(manager) if manager is not None else "Portfolio Manager"
                _r66_b5_report_info_rows.append([
                    f"Sheet_Title:{_safe_name}",
                    f"{_safe_name.replace('_', ' ')} - {_safe_manager} Portfolio Analysis",
                ])
        except Exception as _r66_b5_err:
            logger.debug("Round 66 / B5: Sheet_Title pre-stamp skipped: %s", _r66_b5_err)
        report_info = pd.DataFrame(_r66_b5_report_info_rows, columns=["Item", "Value"])
        report_info.to_excel(xw, sheet_name="Report_Info", index=False)
        # Round 67 / Build 41 (B3): log the produced Report_Info shape
        # so production drift is greppable in structured logs. Build
        # 40 acceptance reports surfaced a Report_Info with only 2
        # rows ("Export type" + "Generated at (UTC)") despite the
        # R66/B5 contract requiring Manager / Technology / Days +
        # one ``Sheet_Title:<sheet>`` row per data sheet. Without
        # this log, the operator can only spot the regression by
        # opening the produced XLSX in Excel by hand.
        try:
            _r67_b3_data_sheets = locals().get("_r66_b5_data_sheet_names", []) or []
            logger.info(
                "[XLSX] Round 67 / B3: Report_Info written with %d rows for "
                "sheets=%s manager=%r technology=%r days=%r",
                len(_r66_b5_report_info_rows),
                sorted(_r67_b3_data_sheets),
                manager, technology, days,
            )
        except Exception:  # noqa: BLE001
            pass
        _used_sheet_names = {"Summary", "Report_Info"}

        # Round 13 / Phase 9.8: previously the fallback path emitted
        # raw ``df.to_excel(...)`` with no header bolding, no
        # frozen panes, no column-width autofit, and no zebra
        # striping.  When the enhanced formatter was available the
        # workbook had a polished header / autofit pass; when it
        # wasn't (e.g. xlsxwriter unavailable, or the formatter
        # raised), the user opened a wall of unformatted text and
        # blamed the report.  Add a minimal styling pass that
        # mirrors the enhanced formatter's *visual* contract:
        # bold header row, autofiltered table, frozen header,
        # and width-fit columns (capped at a sane max so a
        # rogue 50KB cell value can't blow up the workbook).
        try:
            _r13_book = xw.book  # xlsxwriter.Workbook
            _r13_header_fmt = _r13_book.add_format({
                'bold': True,
                'bg_color': '#0076CE',
                'font_color': '#FFFFFF',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter',
            })
        except Exception:
            _r13_header_fmt = None

        def _r13_fallback_apply_styling(_df, _ws):
            """Round 13 / Phase 9.8: bold header, autofilter, freeze
            pane, and per-column width autofit.  Defensive --
            styling is best-effort and never breaks the export."""
            if _ws is None or _df is None:
                return
            try:
                _ncols = int(_df.shape[1])
                _nrows = int(_df.shape[0])
            except Exception:
                return
            if _ncols <= 0:
                return
            try:
                if _r13_header_fmt is not None:
                    for _col_idx, _col_name in enumerate(list(_df.columns)):
                        _ws.write(0, _col_idx, str(_col_name), _r13_header_fmt)
            except Exception:
                pass
            try:
                _ws.freeze_panes(1, 0)
            except Exception:
                pass
            try:
                if _nrows > 0:
                    _ws.autofilter(0, 0, _nrows, max(0, _ncols - 1))
            except Exception:
                pass
            try:
                for _col_idx, _col_name in enumerate(list(_df.columns)):
                    try:
                        _series = _df.iloc[:, _col_idx]
                        _max_len = max(
                            int(_series.astype(str).map(len).max() or 0),
                            len(str(_col_name)),
                        )
                    except Exception:
                        _max_len = len(str(_col_name))
                    # Cap to 60 so a giant free-text cell can't
                    # explode the column width (Excel UI gets
                    # unusable past ~80 chars).
                    _ws.set_column(_col_idx, _col_idx, min(60, max(8, _max_len + 2)))
            except Exception:
                pass

        try:
            _r13_fallback_apply_styling(report_info, xw.sheets.get("Report_Info"))
        except Exception:
            pass

        # Round 25 / Phase F.2: HTML strip pass on Excel object columns.
        # Snowflake rich-text views were leaking ``<a href...>``,
        # ``<img src...>``, and ``<p>``/``<strong>`` markup into
        # ``AB_Detail_All`` and ``CSConsole_Customer_Pulse`` cells.
        # Excel renders ``<`` literally so the recipient saw raw HTML
        # in place of the intended text.  Strip tags + unescape
        # entities (``&amp;`` -> ``&``) before write so the workbook
        # ships clean, human-readable text.
        try:
            from data_normalization import strip_html_from_dataframe as _r25f_strip_html
        except Exception:
            _r25f_strip_html = None

        for name, df in sheets.items():
            if df is None: continue
            if not isinstance(df, pd.DataFrame):
                try:
                    df = pd.DataFrame(df) if df else pd.DataFrame()
                except Exception as _conv_err:
                    logger.debug(f"Skipping sheet '{name}': cannot convert to DataFrame: {_conv_err}")
                    continue
            df_copy = df.copy()
            for col in df_copy.select_dtypes(include=['datetimetz']).columns:
                if df_copy[col].dt.tz is not None:
                    df_copy[col] = df_copy[col].dt.tz_convert(None)
            df_copy = df_copy.replace([_np.inf, -_np.inf], _np.nan)
            df_copy = _defang_formulas(df_copy)
            # Round 25 / Phase F.2: scrub HTML markup from sheets known
            # to be sourced from Snowflake / CSConsole rich-text views.
            # Round 66 / Pass 1 (B4) widens the allow-list to include
            # the Action_Plans / CSConsole_* family -- Build 38
            # acceptance smoke surfaced raw ``<a href...>`` and
            # ``<p><strong>`` markup leaking through the
            # comprehensive-XLSX Action_Plans sheet (added in R64/B2)
            # and the CSConsole pass-through sheets (added when the
            # filtered_action_plans / filtered_success_priorities /
            # filtered_adoption_barriers frames are written through
            # for downstream debugging). All listed sheets pass
            # through ``_strip_html_safe`` (BeautifulSoup primary,
            # regex fallback) so dangerous block elements
            # (``<script>``, ``<style>``) are decomposed natively
            # rather than leaving body text behind.
            _R66_HTML_STRIP_SHEETS = (
                # R25 baseline.
                "AB_Detail_All",
                "CSConsole_Customer_Pulse",
                # R66/B4 additions -- known to leak Snowflake markup.
                "Action_Plans",
                "CSConsole_Action_Plans",
                "CSConsole_Adoption_Barriers",
                "CSConsole_Success_Priorities",
                # Compact / Renewal AB sheets sometimes carry the same
                # rich-text fields when fetched from C360_CS_TASK_C_VW.
                "Adoption_Barriers",
                "Customer_Pulse",
                "Success_Priorities",
                # Round 67 / Build 41 (B4): the Comprehensive XLSX's
                # ``CSOne_Detail_All`` "Problem Details" / "Resolution
                # Details" cells leak ``<br />``, ``<agent name>``,
                # and ``&#34;`` entities verbatim from the Snowflake
                # CSOne export. The rich-text round-trip happens
                # because Snowflake stores these fields as HTML and
                # the comprehensive flow writes the raw frame through
                # without scrubbing. Adding the sheet here routes it
                # through the same ``_strip_html_safe`` (BeautifulSoup
                # primary, regex fallback) the other AB / AP sheets
                # already use.
                "CSOne_Detail_All",
                # Round 70 / Phase 3 (#10): Build 43 acceptance audit
                # surfaced HTML markup leaking into 4 additional sheets
                # that R67/B4 missed:
                #   * ``All_Support_Cases`` (Compact) -- 4 cells with
                #     ``persona : Admin\nOrgType : Customer ...``
                #     entity-encoded rich-text from CSOne.
                #   * ``Customer_Support_Cases`` (Renewal) -- same shape,
                #     4 cells.
                #   * ``External_Incidents`` (Comprehensive + Leader) --
                #     1 cell with
                #     ``<font size="3"><strong>...</strong></font><br />``
                #     from the Webex Status incident description field
                #     when the upstream HTML normalizer skipped it.
                #   * ``TAC_Cases`` (Leader) -- 1 cell with the same
                #     rich-text leakage as CSOne_Detail_All.
                # All four pass through the same ``_strip_html_safe``
                # path the other allow-listed sheets use.
                "All_Support_Cases",
                "Customer_Support_Cases",
                "External_Incidents",
                "TAC_Cases",
                # Round 79 / Build 55 (B2): the new BE-priority sheets
                # carry CSConsole-sourced ``Description`` and ``Title``
                # cells projected from the underlying AB rows. These
                # cells routinely contain ``<br />``, ``<p>``, and
                # entity-encoded markup that Snowflake stores verbatim.
                # Adding the two new sheets here routes them through
                # the same ``_strip_html_safe`` path the upstream
                # ``Adoption_Barriers`` / ``AB_Detail_All`` sheets
                # already use so the BE-priority output never leaks
                # raw HTML to the operator.
                "BE_Priority_Barriers",
                "BE_Focus_Areas",
            )
            if _r25f_strip_html is not None and name in _R66_HTML_STRIP_SHEETS:
                try:
                    df_copy = _r25f_strip_html(df_copy)
                except Exception as _strip_err:
                    logger.debug(
                        "Round 25 / Phase F.2 (R66/B4 widened): HTML "
                        "strip skipped for sheet '%s': %s",
                        name,
                        _strip_err,
                    )

            # Round 15 / Phase 1.2: project every sheet through the
            # customer-facing column SSoT before write.  Defensive --
            # if the schema call raises for any reason we fall back
            # to the un-curated frame so the report still ships.
            try:
                df_copy = _r15_apply_export_schema(df_copy, sheet_name=name)
            except Exception as _schema_err:
                logger.debug(
                    "Round 15 export schema skipped for sheet '%s': %s",
                    name,
                    _schema_err,
                )

            # Round 12 / Phase 9.8: previously the fallback sheet
            # name was only truncated to 31 characters but the
            # Excel-invalid characters ``[ ] : * ? / \`` (and the
            # leading/trailing apostrophe) were NOT stripped, so
            # an upstream ``name`` like
            # ``"Adoption Barriers (P1*/P2)"`` would land with the
            # ``*`` intact and openpyxl would raise
            # ``InvalidWorkbookException`` mid-write -- losing
            # every still-pending sheet.  Sanitize before the
            # truncation step so the workbook never sees a name
            # Excel will reject.
            sheet = _r12_sanitize_sheet_name(name)
            base_sheet = sheet
            suffix = 2
            while sheet in _used_sheet_names:
                sheet = f"{base_sheet[:28]}_{suffix}"
                suffix += 1
            _used_sheet_names.add(sheet)
            if hasattr(df_copy, "to_excel"):
                df_copy.to_excel(xw, sheet_name=sheet, index=False)
            else:
                pd.DataFrame(df_copy).to_excel(xw, sheet_name=sheet, index=False)
            # Round 13 / Phase 9.8: apply the shared fallback
            # styling pass so this fallback workbook visually
            # matches the enhanced formatter's contract.
            try:
                _r13_fallback_apply_styling(df_copy, xw.sheets.get(sheet))
            except Exception:
                pass
            # Round 15 / Phase 2.6: convert to a real Excel Table
            # (banded rows / native filter UX) and layer per-column
            # formats + conditional formatting (risk 3-color scale,
            # severity tier bands, status grey/yellow/red, days-open
            # data bar).  Defensive -- the previous styling pass
            # already shipped a usable sheet if this fails.
            try:
                _r15_apply_excel_polish(
                    _r13_book,
                    xw.sheets.get(sheet),
                    df_copy,
                    sheet,
                    _r15_used_table_names,
                )
            except Exception as _polish_err:
                logger.debug(
                    "Round 15 visual polish skipped for sheet '%s': %s",
                    sheet,
                    _polish_err,
                )
        if csconsole_data:
            for key, sheet_name in csconsole_sheet_names.items():
                df = csconsole_data.get(key)
                if df is not None and hasattr(df, "empty") and not df.empty:
                    df_copy = df.copy()
                    for col in df_copy.select_dtypes(include=['datetimetz']).columns:
                        if df_copy[col].dt.tz is not None:
                            df_copy[col] = df_copy[col].dt.tz_convert(None)
                    df_copy = _defang_formulas(df_copy)
                    # Round 25 / Phase F.2: HTML strip pass for the
                    # CSConsole_Customer_Pulse branch.  This branch is
                    # responsible for half of the leaked ``<a>`` /
                    # ``<img>`` cells in the reference report (the AB
                    # branch covers the other half).
                    if _r25f_strip_html is not None and sheet_name == "CSConsole_Customer_Pulse":
                        try:
                            df_copy = _r25f_strip_html(df_copy)
                        except Exception as _strip_err:
                            logger.debug(
                                "Round 25 / Phase F.2: HTML strip "
                                "skipped for CSConsole sheet '%s': %s",
                                sheet_name,
                                _strip_err,
                            )
                    # Round 15 / Phase 1.2: same column-curation
                    # filter for the CSConsole branch -- this is
                    # where ``CSConsole_Customer_Pulse`` was leaking
                    # ETL_ID / DELETE_FLAG / EDWSF_* etc.
                    try:
                        df_copy = _r15_apply_export_schema(df_copy, sheet_name=sheet_name)
                    except Exception as _schema_err:
                        logger.debug(
                            "Round 15 export schema skipped for sheet '%s': %s",
                            sheet_name,
                            _schema_err,
                        )
                    # Round 12 / Phase 9.8: see comment above --
                    # ``csconsole_sheet_names`` is hard-coded today
                    # but a future caller could easily inject an
                    # invalid character via key rename, so route
                    # through the same sanitizer.
                    _r13_cs_sheet = _r12_sanitize_sheet_name(sheet_name)
                    df_copy.to_excel(
                        xw,
                        sheet_name=_r13_cs_sheet,
                        index=False,
                    )
                    # Round 13 / Phase 9.8: apply the same fallback
                    # styling pass to CSConsole sheets so the
                    # workbook is uniformly styled.
                    try:
                        _r13_fallback_apply_styling(df_copy, xw.sheets.get(_r13_cs_sheet))
                    except Exception:
                        pass
                    # Round 15 / Phase 2.7: same Round-15 polish pass
                    # for CSConsole sheets so the table / conditional-
                    # formatting contract is uniform across the
                    # workbook (CSConsole_Customer_Pulse used to
                    # ship as a flat unformatted dump even after
                    # Phase 1 column curation).
                    try:
                        _r15_apply_excel_polish(
                            _r13_book,
                            xw.sheets.get(_r13_cs_sheet),
                            df_copy,
                            _r13_cs_sheet,
                            _r15_used_table_names,
                        )
                    except Exception as _polish_err:
                        logger.debug(
                            "Round 15 visual polish skipped for CSConsole sheet '%s': %s",
                            _r13_cs_sheet,
                            _polish_err,
                        )
    return f"{base_path}.xlsx"

# --------------------------- LLM prompt ---------------------------


class _R79BriefingDisabled(Exception):
    """Round 79 / B6 sentinel raised inside the per-AB briefing emit
    block when ``Config.BE_PRIORITY_BRIEFING_ENABLED`` is False. Caught
    by the outer try/except on the same iteration so the loop falls
    through to the shared ``briefing.append('---')`` row close without
    appending the BE-priority context fields. Used purely as control
    flow to keep the legacy 4-line shape intact under kill-switch."""


def _create_briefing_book(data_scope: str, ab_df, csone_df, ext_bugs, ext_incidents, matches, matched_df, db_profile, engagement_counts=None, csconsole_data=None, arr_data=None, arr_impact=None, feature_requests=None, software_defects=None, psirt_vulns=None, risk_profiles=None):
    """Creates a detailed text block for the LLM prompt.

    Round 25 / Phase C: ``risk_profiles`` (a dict of customer_name ->
    profile dict containing ``risk_score`` and/or ``risk_level``) is
    optional.  When supplied, the briefing emits a
    ``Canonical Risk Bands`` section that the
    ``PROMPT_PORTFOLIO_TEMPLATE`` "All Customers in Trouble" block
    binds the LLM to.  The post-render validator
    (``report_consistency.validate_word_numeric_drift``) then asserts
    the number of narrated ``Risk Level: CRITICAL`` /
    ``Risk Level: HIGH`` lines equals the canonical
    ``high_risk_customers`` count.
    """
    briefing = []
    briefing.append(f"## Analyst's Briefing Book for: {data_scope}")
    briefing.append("---")

    # Round 25 / Phase C: emit the canonical risk-band section first so
    # the LLM sees the authoritative band assignments before any of the
    # downstream "customers in trouble" prompts.  Pre-Round 25 the LLM
    # had no anchor for the Risk Level labels and would free-style
    # compound bands ("HIGH/CRITICAL") or invent CRITICAL where the
    # canonical pipeline reported HIGH (the reference Brian Frazier /
    # All Contact Center / 90d report enumerated FARMERS=CRITICAL,
    # NATIONAL GRID=CRITICAL, WINTRUST=HIGH while the dashboard tile
    # said Critical+High = 1).  This section is the truth source the
    # post-render validator's CRITICAL+HIGH count enforces.
    if risk_profiles:
        # Round 25 / Phase C: derive each customer's authoritative band
        # from the canonical 0-100 thresholds.  Honor an explicit
        # ``risk_level`` on the profile (the canonical scorer already
        # populates this) before falling back to a score-based bucketing.
        try:
            from risk_scoring import RISK_BAND_THRESHOLDS as _R25C_THRESHOLDS
        except Exception:
            _R25C_THRESHOLDS = {
                "CRITICAL": 80.0,
                "HIGH": 60.0,
                "MEDIUM": 40.0,
                "LOW": 20.0,
            }

        def _r25c_band_from_score(score: float) -> str:
            try:
                s = float(score)
            except (TypeError, ValueError):
                return "UNKNOWN"
            if s >= float(_R25C_THRESHOLDS.get("CRITICAL", 80.0)):
                return "CRITICAL"
            if s >= float(_R25C_THRESHOLDS.get("HIGH", 60.0)):
                return "HIGH"
            if s >= float(_R25C_THRESHOLDS.get("MEDIUM", 40.0)):
                return "MEDIUM"
            if s >= float(_R25C_THRESHOLDS.get("LOW", 20.0)):
                return "LOW"
            return "HEALTHY"

        briefing.append("### Canonical Risk Bands (Round 25 / Phase C)")
        briefing.append(
            "**These bands are computed by the canonical risk-scoring "
            "pipeline. Use them VERBATIM when assigning a Risk Level "
            "to any customer below. Do NOT invent compound labels "
            "(no \"HIGH/CRITICAL\", no \"MEDIUM/LOW\") and do NOT "
            "promote/demote bands. The five canonical labels are: "
            "CRITICAL, HIGH, MEDIUM, LOW, HEALTHY.**"
        )
        try:
            _r25c_rows: list[tuple[str, float, str]] = []
            for _r25c_name, _r25c_profile in (risk_profiles or {}).items():
                if not _r25c_name:
                    continue
                if not isinstance(_r25c_profile, dict):
                    continue
                try:
                    _r25c_score = float(_r25c_profile.get("risk_score") or 0.0)
                except (TypeError, ValueError):
                    _r25c_score = 0.0
                _r25c_label = _r25c_profile.get("risk_level")
                _r25c_label_str = str(_r25c_label or "").upper().strip()
                if _r25c_label_str not in {
                    "CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY",
                }:
                    _r25c_label_str = _r25c_band_from_score(_r25c_score)
                _r25c_rows.append((str(_r25c_name), _r25c_score, _r25c_label_str))
            _r25c_rank = {
                "CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "HEALTHY": 4,
            }
            _r25c_rows.sort(
                key=lambda r: (
                    _r25c_rank.get(r[2], 5),
                    -float(r[1]),
                    r[0].lower(),
                )
            )
            for _r25c_name, _r25c_score, _r25c_label_str in _r25c_rows:
                briefing.append(
                    f"- **{_r25c_name}** -- Risk Level: {_r25c_label_str} "
                    f"(canonical risk_score={_r25c_score:.1f})"
                )
            if not _r25c_rows:
                briefing.append(
                    "_No risk profiles available for this run._ "
                    "Narrate this gap explicitly in the report rather "
                    "than inventing risk levels."
                )
        except Exception as _r25c_emit_err:
            logger.warning(
                "Round 25 / Phase C: failed to emit canonical risk-band "
                "list (%s); LLM will fall back to free-form bands.",
                _r25c_emit_err,
            )
        briefing.append("")
        briefing.append("---")

    # ARR by Customer (strategic prioritization - high-value accounts need extra attention)
    # Round 11 / Phase 1.4: compute a *single* ``_briefing_amount_prefix``
    # / suffix pair from the ARR currency set up-front so EVERY downstream
    # ARR-bearing line (ARR by Customer, ARR by Issue Category, Feature
    # Requests, Software Defects/PSIRT customer ARR, etc.) renders the
    # same currency disclaimer.  Previously only the customer block at
    # ~6748 was gated; lines ~6766/6772/6782 (issue ARR, feature ARR)
    # printed ``$`` unconditionally even when the portfolio mixed
    # currencies, which contradicted the "do NOT sum across currencies"
    # warning we just printed three lines above.  Default to no prefix +
    # ``(currency unknown)`` suffix when the currency set is empty so we
    # don't fabricate USD.
    _briefing_amount_prefix = ''
    _briefing_amount_suffix = ''
    if arr_data is not None and not arr_data.empty and 'BU_NAME' in arr_data.columns and 'ANNUAL_CONTRACT_VALUE' in arr_data.columns:
        # Round 5 / Phase 3.3: bucket ARR by CURRENCY_CODE before
        # summing/displaying so a portfolio that mixes USD/EUR/GBP rows
        # is not silently summed under a single ``$`` prefix (which
        # would inflate or deflate the headline by FX-rate magnitudes
        # and prompt the LLM to extrapolate against a wrong total).
        # If only a single currency appears we keep the existing,
        # familiar single-block rendering for backwards compatibility.
        _has_ccy = 'CURRENCY_CODE' in arr_data.columns
        try:
            _ccy_set = set(arr_data['CURRENCY_CODE'].dropna().astype(str).str.upper().unique()) if _has_ccy else set()
        except Exception:
            _ccy_set = set()
        if not _ccy_set:
            _briefing_amount_suffix = ' (currency unknown)'
        elif len(_ccy_set) == 1:
            _ccy_only = next(iter(_ccy_set))
            _briefing_amount_prefix = '$' if _ccy_only == 'USD' else f"{_ccy_only} "
        else:
            _briefing_amount_suffix = ' (MIXED currencies — do not sum)'
        if _has_ccy and len(_ccy_set) > 1:
            briefing.append("### ARR by Customer (Strategic Prioritization, MIXED CURRENCY):")
            briefing.append(
                "**CRITICAL:** This portfolio contains rows in multiple currencies "
                f"({', '.join(sorted(_ccy_set))}). Totals are reported per currency; "
                "do NOT sum across currencies and do NOT extrapolate a single "
                "consolidated $ figure -- there is no FX conversion applied here."
            )
            # Round 12 / Phase 1.3: ``groupby(['CURRENCY_CODE', 'BU_NAME'])``
            # collides two distinct ACCOUNT_ID_Cs that share a display
            # name within the same currency bucket -- the same class of
            # bug Round 11 / Phase 1.5 fixed for the single-currency
            # branch.  Roll up on ACCOUNT_ID_C within each currency
            # and project BU_NAME as a label.  Fall back to the
            # legacy BU_NAME-only grouping when ACCOUNT_ID_C is
            # unavailable so older fixtures still render.
            grouped = None
            try:
                if 'ACCOUNT_ID_C' in arr_data.columns:
                    _per_acct_ccy = (
                        arr_data[
                            ['ACCOUNT_ID_C', 'BU_NAME', 'CURRENCY_CODE', 'ANNUAL_CONTRACT_VALUE']
                        ]
                        .dropna(subset=['ACCOUNT_ID_C'])
                        .groupby(['CURRENCY_CODE', 'ACCOUNT_ID_C'], as_index=False)
                        .agg({'BU_NAME': 'first', 'ANNUAL_CONTRACT_VALUE': 'sum'})
                    )
                    grouped = _per_acct_ccy
                else:
                    _legacy = (
                        arr_data
                        .groupby(['CURRENCY_CODE', 'BU_NAME'])['ANNUAL_CONTRACT_VALUE']
                        .sum()
                        .reset_index()
                    )
                    _legacy['ACCOUNT_ID_C'] = _legacy['BU_NAME']
                    grouped = _legacy
            except Exception as _grp_err:
                logger.debug("Briefing mixed-currency rollup failed: %s", _grp_err)
                grouped = None
            if grouped is not None and not grouped.empty:
                for ccy in sorted(_ccy_set):
                    try:
                        sub = grouped[grouped['CURRENCY_CODE'].astype(str).str.upper() == ccy].copy()
                        sub = sub.sort_values(
                            ['ANNUAL_CONTRACT_VALUE', 'ACCOUNT_ID_C'],
                            ascending=[False, True],
                            kind='mergesort',
                        )
                    except Exception:
                        continue
                    sub_total = float(sub['ANNUAL_CONTRACT_VALUE'].sum())
                    # Round 12 / Phase 9.1: previously this block used
                    # raw ``f"{x:,.0f}"`` formatting while CHD / EI
                    # money lines (Round 11 / Phase 9.6) route through
                    # ``format_number`` -- so the same money value
                    # could render as ``$1,234,567`` here and ``N/A``
                    # in CHD if the underlying ARR was NaN/None.  Use
                    # the shared helper so the briefing inherits the
                    # same NaN/None/inf hardening and the entire
                    # report shows a single canonical money string.
                    briefing.append(f"#### {ccy} bucket (total: {ccy} {_r12_format_number(sub_total)})")
                    for _, _row in sub.head(20).iterrows():
                        _label = _row.get('BU_NAME') or _row.get('ACCOUNT_ID_C')
                        _arr_val = float(_row['ANNUAL_CONTRACT_VALUE'])
                        # Round 30 / L3: route per-currency bucket share
                        # through ``_safe_div`` to honour the canonical
                        # zero/NaN contract.
                        pct = _safe_div(_arr_val, sub_total) * 100
                        briefing.append(f"- **{_label}:** {ccy} {_r12_format_number(_arr_val)} ({pct:.1f}% of {ccy} bucket)")
                    if len(sub) > 20:
                        briefing.append(f"- ... and {len(sub) - 20} more {ccy} customers")
            briefing.append("**Total Portfolio ARR:** MIXED — see per-currency totals above; do not sum across currencies.")
            briefing.append("---")
        else:
            # Round 11 / Phase 1.5: ``groupby('BU_NAME')`` collides
            # accounts that happen to share a display name (R10 / Phase
            # 3.1 fixed the same class of bug for portfolio
            # concentration -- but the briefing book customer block
            # never adopted ACCOUNT_ID_C as the primary key).  Roll up
            # ARR by ACCOUNT_ID_C when available and use BU_NAME purely
            # as a label so two distinct accounts with identical
            # display names render as two rows, not one fabricated
            # mega-customer.
            if 'ACCOUNT_ID_C' in arr_data.columns:
                try:
                    _per_acct = (
                        arr_data[['ACCOUNT_ID_C', 'BU_NAME', 'ANNUAL_CONTRACT_VALUE']]
                        .dropna(subset=['ACCOUNT_ID_C'])
                        .groupby('ACCOUNT_ID_C', as_index=False)
                        .agg({'BU_NAME': 'first', 'ANNUAL_CONTRACT_VALUE': 'sum'})
                        .sort_values('ANNUAL_CONTRACT_VALUE', ascending=False)
                    )
                    arr_by_cust = pd.Series(
                        _per_acct['ANNUAL_CONTRACT_VALUE'].values,
                        index=_per_acct['BU_NAME'].values,
                    )
                except Exception as _per_acct_err:
                    logger.debug("Briefing-1 per-account ARR rollup failed: %s", _per_acct_err)
                    # Round 13 / Phase 3.9: fallback rollup must canonicalize
                    # BU_NAME so cosmetic variants don't fan out into
                    # separate "customers" with under-concentrated shares.
                    _arr_data_norm = arr_data.copy()
                    _arr_data_norm['_bu_disp'] = (
                        _arr_data_norm['BU_NAME'].fillna('Unknown').apply(normalize_customer_name)
                    )
                    arr_by_cust = _arr_data_norm.groupby('_bu_disp')['ANNUAL_CONTRACT_VALUE'].sum().sort_values(ascending=False)
            else:
                # Round 13 / Phase 3.9: same as the except branch above.
                _arr_data_norm = arr_data.copy()
                _arr_data_norm['_bu_disp'] = (
                    _arr_data_norm['BU_NAME'].fillna('Unknown').apply(normalize_customer_name)
                )
                arr_by_cust = _arr_data_norm.groupby('_bu_disp')['ANNUAL_CONTRACT_VALUE'].sum().sort_values(ascending=False)
            total_arr = arr_by_cust.sum()
            # Round 6 / Phase 7.1: when ``CURRENCY_CODE`` is missing
            # entirely or every value is null/empty, the previous
            # build silently labelled the totals as ``USD`` -- which
            # is a factual claim the data does not actually support
            # and causes the LLM to extrapolate against a wrong base.
            # Branch explicitly on the empty-set case and render an
            # "amounts shown without currency unit" disclaimer instead
            # of fabricating a ``$`` prefix.
            if not _ccy_set:
                briefing.append("### ARR by Customer (Strategic Prioritization, CURRENCY UNKNOWN):")
                briefing.append(
                    "**CRITICAL:** ARR rows do not carry a CURRENCY_CODE; "
                    "amounts below are reported as raw numbers without a "
                    "currency unit.  Do NOT assume USD and do NOT compare "
                    "against any external currency benchmark."
                )
                for cust, arr_val in arr_by_cust.head(20).items():
                    # Round 30 / L3: ``_safe_div`` for canonical
                    # zero/NaN handling on portfolio share.
                    pct = _safe_div(arr_val, total_arr) * 100
                    briefing.append(f"- **{cust}:** {_r12_format_number(arr_val)} ({pct:.1f}% of portfolio, currency unknown)")
                if len(arr_by_cust) > 20:
                    briefing.append(f"- ... and {len(arr_by_cust) - 20} more customers")
                briefing.append(f"**Total Portfolio ARR:** {_r12_format_number(total_arr)} (currency unknown)")
                briefing.append("---")
            else:
                _ccy_label = next(iter(_ccy_set))
                _prefix = '$' if _ccy_label == 'USD' else f"{_ccy_label} "
                briefing.append("### ARR by Customer (Strategic Prioritization):")
                briefing.append("**CRITICAL:** Prioritize high-ARR customers with adoption barriers or support cases. These represent the greatest renewal risk and revenue impact.")
                for cust, arr_val in arr_by_cust.head(20).items():
                    # Round 30 / L3: ``_safe_div`` for canonical
                    # zero/NaN handling on portfolio share.
                    pct = _safe_div(arr_val, total_arr) * 100
                    briefing.append(f"- **{cust}:** {_prefix}{_r12_format_number(arr_val)} ({pct:.1f}% of portfolio)")
                if len(arr_by_cust) > 20:
                    briefing.append(f"- ... and {len(arr_by_cust) - 20} more customers")
                briefing.append(f"**Total Portfolio ARR:** {_prefix}{_r12_format_number(total_arr)}")
                briefing.append("---")

    # ARR Impact by Issue Category (which adoption barrier types have highest revenue at risk)
    # Round 11 / Phase 1.4: gate the ``$`` prefix on the briefing-wide
    # currency disclaimer computed from ``arr_data`` above; never print
    # ``$`` for non-USD or mixed-currency portfolios.
    if arr_impact and arr_impact.get('top_issues'):
        briefing.append("### ARR at Risk by Issue Category:")
        briefing.append("**CRITICAL:** Prioritize interventions for issue categories with highest ARR exposure.")
        for issue_name, data in arr_impact['top_issues'][:10]:
            arr_val = data.get('arr', 0)
            cust_count = data.get('customer_count', 0)
            briefing.append(
                f"- **{issue_name}:** {_briefing_amount_prefix}{_r12_format_number(arr_val)} at risk "
                f"({cust_count} customers){_briefing_amount_suffix}"
            )
        briefing.append("---")

    # Feature Requests (product gap signal - customers asking for capabilities)
    if feature_requests and feature_requests.get('total_requests', 0) > 0:
        briefing.append("### Feature Requests (Product Gap Signal):")
        # Round 12 / Phase 1.1: when ``analyze_feature_requests``
        # detected mixed currencies, ``arr_impact_comparable`` is
        # False and ``total_arr_impact`` is intentionally 0.  Surface
        # an explicit "n/a (mixed currencies)" rather than printing
        # "$0" which would look like the customers have no ARR.
        if feature_requests.get('arr_impact_comparable', True):
            _arr_impact_str = (
                f"{_briefing_amount_prefix}"
                f"{_r12_format_number(feature_requests.get('total_arr_impact', 0))}"
                f"{_briefing_amount_suffix}"
            )
        else:
            _ccys = feature_requests.get('arr_impact_currencies') or []
            _arr_impact_str = (
                "n/a (mixed currencies"
                + (": " + ", ".join(_ccys) if _ccys else "")
                + ")"
            )
        briefing.append(
            f"**{feature_requests['total_requests']}** cases contain feature requests. "
            f"ARR impact: {_arr_impact_str}"
        )
        if feature_requests.get('top_features'):
            briefing.append("**Most requested themes:**")
            for theme, count in feature_requests['top_features'][:5]:
                briefing.append(f"- {theme}: {count} cases")
        if feature_requests.get('customer_examples'):
            top_by_arr = sorted([c for c in feature_requests['customer_examples'] if c.get('arr', 0) > 0], key=lambda x: x.get('arr', 0), reverse=True)[:5]
            if top_by_arr:
                briefing.append("**High-value customers requesting features:**")
                for c in top_by_arr:
                    briefing.append(
                        f"- {c.get('customer', 'N/A')}: "
                        f"{_briefing_amount_prefix}{_r12_format_number(c.get('arr', 0))} ARR"
                        f"{_briefing_amount_suffix}"
                    )
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
    import canonical_metrics as _r531_metrics_cm
    # Round 53.1: the cited ID-backed barrier metric is a distinct barrier
    # record count, not the raw export row count.
    total_ab = _r531_metrics_cm.count_total_barriers(ab_df)
    total_csone = len(csone_df) if csone_df is not None else 0
    esc_rate, chronic_rate = _calc_rates(csone_df)

    # Calculate BEMS metrics (PRIMARY: Transaction ID column from CSOne Excel)
    total_bems = 0
    bems_rate = 0.0
    if csone_df is not None and not csone_df.empty:
        csone_norm = add_case_lifecycle_fields(csone_df)
        bems_mask = detect_bems_mask(csone_norm)
        bems_cases = csone_norm[bems_mask]
        total_bems = len(bems_cases)
        # Round 30 / L3: ``_safe_div`` so an empty CSone universe
        # returns 0% instead of leaking a divide-by-zero into the
        # briefing book.
        bems_rate = _safe_div(total_bems, total_csone) * 100

    # CSConsole metrics
    csconsole_action_plans = csconsole_data.get('action_plans', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    csconsole_customer_pulse = csconsole_data.get('customer_pulse', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    csconsole_success_priorities = csconsole_data.get('success_priorities', pd.DataFrame()) if csconsole_data else pd.DataFrame()
    csconsole_adoption_barriers = csconsole_data.get('adoption_barriers', pd.DataFrame()) if csconsole_data else pd.DataFrame()

    total_action_plans = len(csconsole_action_plans) if not csconsole_action_plans.empty else 0
    total_customer_pulse = len(csconsole_customer_pulse) if not csconsole_customer_pulse.empty else 0
    total_success_priorities = len(csconsole_success_priorities) if not csconsole_success_priorities.empty else 0
    total_csconsole_adoption_barriers = (
        _r531_metrics_cm.count_total_barriers(csconsole_adoption_barriers)
        if not csconsole_adoption_barriers.empty else 0
    )

    briefing.append("### Key Metrics:")
    briefing.append(
        f"* **Adoption Barriers Found:** {total_ab} "
        f"{format_inline_source('Adoption Barriers', fields=['ID'])}"
    )
    briefing.append(
        f"* **CSOne (TAC) Cases Found:** {total_csone} "
        f"{format_inline_source('Support Cases (TAC)', fields=['Case Number', 'SR Number'])}"
    )
    briefing.append(
        f"* **BEMS Escalations (Back End Engineering):** {total_bems} ({bems_rate:.1f}% of TAC cases) "
        f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
    )
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
    # Uses canonical normalized priority (case_priority_norm via normalize_priority_label)
    # so we never misclassify "P10" / "S12" / mixed-case labels as P1/P2.
    if csone_df is not None and not csone_df.empty and 'customer_name' in csone_df.columns:
        _csone_norm_for_sev = add_case_lifecycle_fields(csone_df)
        if 'case_priority_norm' in _csone_norm_for_sev.columns:
            # Round 13 / Phase 3.1: count P1/P2 against the normalized
            # customer key so two source rows that differ only in
            # whitespace/case (e.g. ``"acme co."`` vs ``"Acme Co"``)
            # contribute to the same row in the briefing.  Without
            # normalization the same customer appeared as two entries
            # and the top-15 sort silently dropped one of them.
            _sev_df = _csone_norm_for_sev.copy()
            _sev_df['_cust_disp'] = _sev_df['customer_name'].apply(normalize_customer_name)
            p1_mask = _sev_df['case_priority_norm'] == 'P1'
            p2_mask = _sev_df['case_priority_norm'] == 'P2'
            p1_by_cust = _sev_df[p1_mask]['_cust_disp'].value_counts()
            p2_by_cust = _sev_df[p2_mask]['_cust_disp'].value_counts()
            if not p1_by_cust.empty or not p2_by_cust.empty:
                briefing.append("### P1/P2 Cases by Customer (High-Priority Risk):")
                all_custs = set(p1_by_cust.index) | set(p2_by_cust.index)
                for cust in sorted(all_custs, key=lambda c: (-p1_by_cust.get(c, 0), -p2_by_cust.get(c, 0), str(c)))[:15]:
                    p1 = int(p1_by_cust.get(cust, 0))
                    p2 = int(p2_by_cust.get(cust, 0))
                    if p1 > 0 or p2 > 0:
                        briefing.append(f"- **{cust}:** P1: {p1} | P2: {p2}")
                briefing.append("---")

    # Summaries
    if ab_df is not None and not ab_df.empty:
        by_cat = ab_df.groupby("ab_category_final")["ID"].count().sort_values(ascending=False)
        briefing.append("### Adoption Barrier Category Summary:")
        briefing.append(json.dumps({str(k):int(v) for k,v in by_cat.items() if pd.notna(k)}, indent=2))
        by_sub = ab_df.groupby("sub_technology")["ID"].count().sort_values(ascending=False)
        briefing.append("\n### Adoption Barrier Sub-Technology Summary:")
        briefing.append(json.dumps({str(k):int(v) for k,v in by_sub.items() if pd.notna(k)}, indent=2))
        briefing.append("\n### All Adoption Barrier Titles for Thematic Analysis:")
        briefing.append("\n".join("- " + str(title) for title in ab_df['title'].dropna()))

        # Add full adoption barrier details with source citations
        briefing.append("\n### Complete Adoption Barrier Details (with Source Citations):")
        for _, row in ab_df.iterrows():
            barrier_id = row.get('ID', 'Unknown')
            title = row.get('title', 'No Title')
            description = row.get('description', 'No Description')
            # Round 13 / Phase 3.4: route the customer name through
            # ``normalize_customer_name`` so cosmetic variants of the
            # same upstream value (NBSP, doubled spaces, stray dots)
            # render identically to other briefing sections that
            # already normalize.  Mirrors the TAC-case branch lower
            # in the file.
            _raw_customer = row.get('customer_name', 'Unknown Customer')
            try:
                customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown Customer'
                if not customer or customer == 'Unknown':
                    customer = 'Unknown Customer'
            except Exception:
                customer = _raw_customer or 'Unknown Customer'

            # Format with source citation
            briefing.append(f"\n**CSConsole Record: {barrier_id}**")
            briefing.append(f"**Customer:** {customer}")
            briefing.append(f"**Title:** {title}")
            briefing.append(f"**Full Description:** {description}")

            # Round 79 / Build 55 (B6): emit additional BE-priority context
            # fields when present on the row. The scorer (R79/B1) enriches
            # ``ab_norm`` upstream with ``be_priority_score`` and
            # ``be_top_signal``; the LLM classifier (R79/B2) adds
            # ``be_llm_class``. ``severity_norm`` and ``open_age_days``
            # are written by ``_prepare_ab`` and routinely available.
            # The briefing book renders each field only when it has a
            # non-empty value so legacy callers (compact / renewal that
            # don't run the BE-priority scorer) see the original 4-line
            # block unchanged. Operator kill-switch:
            # ``Config.BE_PRIORITY_BRIEFING_ENABLED=False`` falls back to
            # the pre-Round-79 4-line shape (defensive: lets the
            # operator bypass the new fields without redeploying if a
            # future issue is traced to them). Pinned by
            # ``tests/test_round79_b6_briefing_enrichment.py``.
            # Round 79 / Build 55 (B6 robust kill-switch lookup): some sibling
            # tests (R32 / R33 / R35 / R69 / R77) call
            # ``importlib.reload(config)`` which mints a NEW ``Config`` class
            # while the ``from config import Config`` binding above still
            # points at the ORIGINAL class.  A test that monkeypatches
            # ``BE_PRIORITY_BRIEFING_ENABLED=False`` on the post-reload class
            # is invisible to a stale ``Config`` reference, so the kill-switch
            # would silently no-op.  Re-resolve through ``sys.modules['config']``
            # on every call so the live class is consulted.  Falls back to the
            # captured ``Config`` reference if the module entry is missing.
            try:
                _r79_live_cfg = sys.modules.get('config')
                _r79_live_class = (
                    getattr(_r79_live_cfg, 'Config', None)
                    if _r79_live_cfg is not None
                    else None
                )
                if _r79_live_class is None:
                    _r79_live_class = Config
                _r79_briefing_enabled = bool(
                    getattr(_r79_live_class, 'BE_PRIORITY_BRIEFING_ENABLED', True)
                )
            except Exception:  # noqa: BLE001
                _r79_briefing_enabled = True
            try:
                if not _r79_briefing_enabled:
                    raise _R79BriefingDisabled()
                _r79_severity = row.get('severity_norm')
                if _r79_severity is not None and not (
                    isinstance(_r79_severity, float) and pd.isna(_r79_severity)
                ):
                    _r79_severity_str = str(_r79_severity).strip()
                    if _r79_severity_str:
                        briefing.append(f"**CSConsole_Severity:** {_r79_severity_str}")

                _r79_be_score = row.get('be_priority_score')
                if _r79_be_score is not None and not (
                    isinstance(_r79_be_score, float) and pd.isna(_r79_be_score)
                ):
                    try:
                        _r79_score_val = float(_r79_be_score)
                        briefing.append(
                            f"**Independent_BE_Priority:** {_r79_score_val:.1f}"
                        )
                    except (TypeError, ValueError):
                        pass

                _r79_be_class = row.get('be_llm_class')
                if (
                    _r79_be_class is not None
                    and isinstance(_r79_be_class, str)
                    and _r79_be_class.strip()
                    and _r79_be_class.strip().upper() != "UNCLASSIFIED"
                ):
                    briefing.append(
                        f"**Independent_BE_Class:** {_r79_be_class.strip()}"
                    )

                _r79_days_open = row.get('open_age_days')
                if _r79_days_open is not None and not (
                    isinstance(_r79_days_open, float) and pd.isna(_r79_days_open)
                ):
                    try:
                        _r79_days_int = int(round(float(_r79_days_open)))
                        if _r79_days_int >= 0:
                            briefing.append(f"**Days_Open:** {_r79_days_int}")
                    except (TypeError, ValueError):
                        pass
            except _R79BriefingDisabled:
                # Kill-switch path -- legacy 4-line briefing shape only.
                pass
            except Exception as _r79_brief_err:  # noqa: BLE001
                logger.debug(
                    "Round 79 / B6: BE-priority briefing enrichment skipped "
                    "for barrier %s (%s)",
                    barrier_id,
                    _r79_brief_err,
                )

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
                # Round 13 / Phase 2.5: parse with utc=True so a row whose
                # original timestamp falls on the boundary between two
                # months in the worker's local zone but is the same
                # calendar month in UTC is bucketed by the UTC month
                # (matching the rest of the report which is UTC-anchored).
                df_trend['_dt'] = pd.to_datetime(
                    df_trend[date_col], errors='coerce', utc=True
                )
                df_trend = df_trend.dropna(subset=['_dt'])
                if len(df_trend) >= 2:
                    monthly = df_trend.groupby(df_trend['_dt'].dt.to_period('M')).size().sort_index()
                    if len(monthly) >= 2:
                        recent = monthly.iloc[-1]
                        prior = monthly.iloc[-2]
                        # Round 30 / L3: ``_safe_div`` for the
                        # month-over-month change so a missing prior
                        # period yields 0% rather than NaN.
                        change_pct = _safe_div(recent - prior, prior) * 100
                        trend = "INCREASING" if change_pct > 10 else ("DECREASING" if change_pct < -10 else "STABLE")
                        briefing.append("### Case Volume Trend (Month-over-Month):")
                        briefing.append(f"**{trend}:** Most recent month: {int(recent)} cases | Prior month: {int(prior)} cases | Change: {change_pct:+.1f}%")
                        briefing.append("**ANALYTICAL NOTE:** Increasing case volume may indicate emerging issues or deteriorating customer health. Decreasing volume suggests improving stability.")
                        briefing.append("---")
            except Exception as _case_trend_err:
                logger.debug(f"Case volume trend calculation skipped: {_case_trend_err}")

    # FIXED: Show ALL CSOne data
    if csone_df is not None and not csone_df.empty:
        csone_df_display = csone_df.copy()
        csone_df_display['display_id'] = csone_df_display.get('SR Number', csone_df_display.get('Case Number'))
        briefing.append("### Complete CSOne (TAC) Case Data:")
        briefing.append(_json_lite(csone_df_display, limit=len(csone_df_display), keep=["display_id","Title","Owner Email","customer_name","bemscsc_refs"]))

        # Add BEMS-specific analysis using canonical detector
        csone_norm = add_case_lifecycle_fields(csone_df)
        bems_mask = detect_bems_mask(csone_norm)
        bems_cases = csone_norm[bems_mask]

        if not bems_cases.empty:
                briefing.append(f"\n### BEMS Escalation Analysis ({len(bems_cases)} cases requiring Back End Engineering):")
                briefing.append("**CRITICAL INSIGHT:** BEMS (Back End Engineering Management System) escalations indicate complex technical issues that TAC could not resolve independently. These represent high-severity, high-complexity problems requiring specialized engineering expertise.")

                # Round 12 / Phase 3.1: ``groupby('customer_name')`` was
                # raw, so ``"acme co."`` and ``"Acme Co"`` produced
                # two separate rows in the BEMS-by-customer block --
                # mirroring the class of bug Round 11 / Phase 3.x
                # fixed elsewhere.  Project a normalized key column
                # via ``data_normalization.normalize_customer_name``,
                # group on the normalized key, and surface the
                # canonical/display label via the per-group mode so a
                # single BEMS rollup row is emitted per logical
                # customer.
                try:
                    bems_cases = bems_cases.copy()
                    bems_cases['_r12_cust_key'] = (
                        bems_cases['customer_name']
                        .fillna('')
                        .astype(str)
                        .map(normalize_customer_name)
                    )
                except Exception:
                    bems_cases['_r12_cust_key'] = bems_cases.get('customer_name', '')
                bems_by_customer = bems_cases.groupby('_r12_cust_key').agg({
                    'customer_name': lambda s: (
                        s.dropna().mode().iloc[0] if not s.dropna().empty else ''
                    ),
                    'bemscsc_refs': lambda x: list(x),  # List of all BEMS refs
                    'Transaction ID': lambda x: list(x) if 'Transaction ID' in bems_cases.columns else []  # List of Transaction IDs
                })
                bems_by_customer['bems_count'] = bems_cases.groupby('_r12_cust_key').size()

                briefing.append("\n**BEMS Cases by Customer (with BEMS IDs):**")
                for _cust_key, row in bems_by_customer.iterrows():
                    customer = row.get('customer_name') or _cust_key
                    count = row['bems_count']
                    bems_refs = row.get('bemscsc_refs', [])
                    transaction_ids = row.get('Transaction ID', [])

                    # Extract actual BEMS IDs from both sources
                    bems_ids = set()
                    for ref in bems_refs:
                        if ref and str(ref) != 'nan':
                            bems_ids.update(extract_bems_ids_from_row(pd.Series({"bemscsc_refs": ref})))
                    for tid in transaction_ids:
                        if tid and str(tid) != 'nan':
                            bems_ids.update(extract_bems_ids_from_row(pd.Series({"Transaction ID": tid})))

                    # Format BEMS IDs with brackets for consistent citation
                    bems_id_list = ', '.join([f'[{bid}]' for bid in sorted(bems_ids)]) if bems_ids else 'No specific BEMS IDs found'
                    briefing.append(
                        f"- **{customer}:** {count} BEMS escalation{'s' if count > 1 else ''} | **BEMS IDs:** {bems_id_list} "
                        f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
                    )

                # FIXED: Show ALL BEMS cases for complete analysis
                briefing.append(f"\n**All BEMS Cases with Full Details (for Predictive Risk Assessment):**")
                for _, row in bems_cases.iterrows():
                    case_number = row.get('SR Number', row.get('Case Number', 'Unknown'))
                    title = row.get('Title', 'No Title')
                    # Round 13 / Phase 3.5: route customer through
                    # normalize_customer_name so the per-case BEMS
                    # listing shows the same canonical spelling as the
                    # rollup section above (which already normalizes
                    # via ``_r12_cust_key``).  Without this the rollup
                    # said "5 BEMS escalations for Acme Co" and the
                    # detail block listed three of them under "Acme
                    # Co" and two under "Acme co.".
                    _raw_customer = row.get('customer_name', 'Unknown Customer')
                    try:
                        customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown Customer'
                        if not customer or customer == 'Unknown':
                            customer = 'Unknown Customer'
                    except Exception:
                        customer = _raw_customer or 'Unknown Customer'
                    bems_refs = row.get('bemscsc_refs', 'No BEMS refs')
                    transaction_id = row.get('Transaction ID', '')

                    # Show both sources of BEMS info
                    bems_info = []
                    extracted_ids = extract_bems_ids_from_row(row)
                    if extracted_ids and transaction_id:
                        bems_info.append(f"Transaction ID: {transaction_id}")
                    if bems_refs and str(bems_refs) != 'No BEMS refs':
                        bems_info.append(f"BEMS Refs: {bems_refs}")
                    if extracted_ids:
                        bems_info.append(f"Extracted IDs: {', '.join(extracted_ids)}")

                    bems_detail = ' | '.join(bems_info) if bems_info else 'BEMS detected but ID not specified'
                    briefing.append(f"- **TAC Case: {case_number}** ({customer}): {title} | **{bems_detail}**")

        # Add full CSOne case details with source citations
        briefing.append("\n### Complete CSOne (TAC) Case Details (with Source Citations):")
        for _, row in csone_df.iterrows():
            case_number = row.get('SR Number', row.get('Case Number', 'Unknown'))
            title = row.get('Title', 'No Title')
            description = row.get('Description', 'No Description')
            # Round 12 / Phase 3.2: this loop renders raw
            # ``customer_name`` directly into the briefing body, so
            # case-only spelling drift (extra whitespace, case
            # variation, NBSPs) leaks straight into the model context
            # and surfaces as duplicate "customer" buckets.  Run the
            # value through ``normalize_customer_name`` so every
            # occurrence collapses to the same canonical label.
            _raw_customer = row.get('customer_name', 'Unknown Customer')
            try:
                customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown Customer'
                if not customer:
                    customer = 'Unknown Customer'
            except Exception:
                customer = _raw_customer or 'Unknown Customer'
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
    if csconsole_data and any(hasattr(df, 'empty') and not df.empty for df in csconsole_data.values() if df is not None):
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
        # Round 3: surface truncation explicitly so the LLM (and human
        # reader) sees that "10 sample rows" does not equal the total.
        _matched_total = len(matched_df)
        if _matched_total > 10:
            briefing.append(
                f"(first 10 of {_matched_total} rows; sample only — full list available in source data)"
            )
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

    # Round 6 / Phase 3.12: enforce a per-section character cap.  A
    # single runaway section (e.g. a JSON db_profile dump or a long
    # customer/defect list) used to be able to dominate the entire
    # briefing budget and starve every other section before the
    # downstream prompt-budget truncator could reason about which
    # sections actually mattered.  Apply a per-section ceiling so the
    # LLM at least sees the *header* of every section, with an
    # explicit truncation marker so it knows the section was capped.
    return _apply_per_section_cap("\n".join(briefing), max_chars=12000)


def _apply_per_section_cap(briefing_text: str, max_chars: int = 12000) -> str:
    """Cap each ``---``-delimited section of a briefing book.

    Round 6 / Phase 3.12 helper.  Sections in
    ``_create_briefing_book`` are separated by ``---`` lines.  This
    walker keeps each section's header line(s) and truncates the
    body to ``max_chars`` characters, appending a stable
    ``[SECTION TRUNCATED: kept N of M chars due to per-section cap]``
    marker so the LLM (and tests) can detect the cap.

    The cap is intentionally generous (12k chars by default) so it
    only fires for pathologically large sections; smaller sections
    pass through untouched.
    """
    try:
        if not briefing_text:
            return briefing_text or ""
        try:
            cap = int(max_chars)
        except (TypeError, ValueError):
            cap = 12000
        if cap <= 0:
            return briefing_text
        sections = briefing_text.split("\n---\n")
        out: List[str] = []
        for section in sections:
            if len(section) <= cap:
                out.append(section)
                continue
            head = section[:cap]
            # Avoid cutting in the middle of a markdown bullet line so
            # the marker reads cleanly.  Trim back to the previous
            # newline if one exists in the last 200 chars.
            try:
                cut = head.rfind("\n", max(0, cap - 200))
                if cut > 0:
                    head = head[:cut]
            except Exception:
                pass
            marker = (
                f"\n[SECTION TRUNCATED: kept {len(head)} of {len(section)} "
                "chars due to per-section cap]"
            )
            out.append(head + marker)
        return "\n---\n".join(out)
    except Exception as _cap_err:
        try:
            logger.debug("_apply_per_section_cap failed: %s", _cap_err)
        except Exception:
            pass
        return briefing_text

def _create_executive_briefing_book(manager, ab_norm, team_subs_df, technology):
    """Create a focused briefing book for executive analysis using adoption barriers"""
    briefing = []

    briefing.append(f"# Executive Portfolio Analysis - {manager}")
    briefing.append(f"## Technology Focus: {technology}")
    # Round 12 / Phase 10.5: anchor briefing analysis date on UTC and tag the
    # timezone so cross-region operators see the same logical timestamp.
    briefing.append(f"## Analysis Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    briefing.append("")

    # Team overview
    if not team_subs_df.empty:
        briefing.append("## Team Portfolio Overview")
        briefing.append(f"- **Total Team Subscriptions:** {len(team_subs_df)}")
        # Round 2 / Phase 4.2: route through canonical
        # ``cm.count_customers`` so this briefing line agrees with
        # every other report's "Unique Customers" headline.
        try:
            import canonical_metrics as _cm
            from data_normalization import build_customer_lookup as _build_lookup
            _lookup = _build_lookup(team_subs_df)
            _a2c = (_lookup or {}).get("account_to_customer", {}) or {}
            _unique_customers = _cm.count_customers(
                ab_df=team_subs_df,
                csone_df=None,
                account_to_customer=_a2c or None,
            )
        except Exception:
            _unique_customers = (
                team_subs_df['BU_NAME'].nunique() if 'BU_NAME' in team_subs_df.columns else 'N/A'
            )
        briefing.append(f"- **Unique Customers:** {_unique_customers}")
        briefing.append(f"- **Active CSSMs:** {team_subs_df['CSSM_EMAIL'].nunique() if 'CSSM_EMAIL' in team_subs_df.columns else 'N/A'}")
        briefing.append("")

        # Customer list - FIXED: Show ALL customers
        if 'BU_NAME' in team_subs_df.columns:
            # Round 13 / Phase 3.2: normalize before .unique() so two
            # raw spellings of the same customer (e.g. trailing
            # whitespace, NBSP, casing) collapse into a single
            # portfolio entry.  Without this, the briefing rendered
            # "Acme Co" twice (once for each variant) and the listed
            # count contradicted the unique-customer headline above.
            customers = sorted(
                {
                    normalize_customer_name(_v)
                    for _v in team_subs_df['BU_NAME'].dropna().tolist()
                    if normalize_customer_name(_v) and normalize_customer_name(_v) != "Unknown"
                }
            )
            briefing.append("### Complete Customer Portfolio:")
            for customer in customers:  # Show ALL customers
                briefing.append(f"- {customer}")
            briefing.append("")

    # Adoption barriers analysis
    if not ab_norm.empty:
        briefing.append("## Critical Adoption Barriers Analysis")
        import canonical_metrics as _r531_cm

        def _r531_barrier_records(df: pd.DataFrame) -> pd.DataFrame:
            """Round 53.1: collapse Snowflake fan-out rows to barrier records."""

            if df is None or df.empty:
                return pd.DataFrame()
            if 'ID' not in df.columns:
                return df
            _with_id = df[df['ID'].notna()].drop_duplicates(subset=['ID'])
            _without_id = df[df['ID'].isna()]
            return pd.concat([_with_id, _without_id], ignore_index=True)

        ab_records = _r531_barrier_records(ab_norm)
        briefing.append(f"- **Total Adoption Barriers:** {_r531_cm.count_total_barriers(ab_norm)}")

        # Top customers with barriers
        if 'customer_name' in ab_norm.columns:
            # Round 13 / Phase 3.5/3.7: normalize before value_counts so
            # cosmetic spelling drift (whitespace, NBSPs, casing) does
            # not split a single customer into multiple briefing lines.
            _ab_min = ab_records.copy()
            _ab_min['_cust_disp'] = (
                _ab_min['customer_name'].fillna('Unknown').apply(normalize_customer_name)
            )
            customer_barriers = _ab_min['_cust_disp'].value_counts()
            briefing.append(f"- **Customers with Barriers:** {len(customer_barriers)}")
            briefing.append("")

            briefing.append("### All Customers Requiring Attention:")
            # FIXED: Show ALL customers with barriers for complete visibility
            for customer, count in customer_barriers.items():
                briefing.append(f"- **{customer}**: {count} barriers")
            briefing.append("")

        # Barrier categories
        if 'ab_category_c' in ab_records.columns:
            categories = ab_records['ab_category_c'].value_counts()
            briefing.append("### All Barrier Categories:")
            # FIXED: Show ALL categories
            for category, count in categories.items():
                briefing.append(f"- **{category}**: {count} barriers")
            briefing.append("")

        # Severity analysis
        if 'severity_c' in ab_records.columns:
            severity = ab_records['severity_c'].value_counts()
            briefing.append("### Severity Distribution:")
            for sev, count in severity.items():
                briefing.append(f"- **{sev}**: {count} barriers")
            briefing.append("")

        # Recent barriers (short-horizon spotlight). Round 3: this 30-day
        # window is intentional — it surfaces *new momentum* regardless
        # of the broader analysis window. Rename the heading to make
        # that explicit so readers don't assume it tracks the run's
        # configured ``days``.
        if 'open_date_c' in ab_norm.columns:
            try:
                # Round 12 / Phase 2.1: ``datetime.now()`` (naive local
                # clock) was previously compared against ``open_date_c``
                # values which are normalized to UTC in the upstream
                # parser.  On a machine in (e.g.) Tokyo the cutoff
                # could miss or include barriers by ~9 hours depending
                # on local DST and the Snowflake row's UTC time.  Use
                # ``datetime.now(timezone.utc)`` so the cutoff is the
                # same scalar regardless of host clock, mirroring the
                # frozen ``_as_of_date`` Round 11 / Phase 7.6 used in
                # the SQL-side path.  Also coerce ``open_date_c`` to a
                # tz-aware UTC series so the comparison does not raise
                # when one side carries a tz and the other does not.
                _r12_now_utc = datetime.now(timezone.utc)
                _r12_cutoff = _r12_now_utc - timedelta(days=30)
                _r12_open = pd.to_datetime(
                    ab_norm['open_date_c'], utc=True, errors='coerce',
                )
                recent_barriers = ab_norm[_r12_open >= _r12_cutoff]
                # Round 4: clarify that this is a fixed 30-day spotlight
                # nested inside the surrounding analysis window so
                # readers do not assume it tracks the run's configured
                # ``days``.  The spotlight is intentionally short-horizon
                # (always 30 days) regardless of the broader window.
                briefing.append(
                    f"### Recent Barriers (fixed 30-day spotlight, independent of analysis window): {len(recent_barriers)}"
                )
                if len(recent_barriers) > 0:
                    briefing.append("- Recent barriers indicate ongoing challenges requiring immediate attention")
                briefing.append("")
            except Exception as _barrier_trend_err:
                logger.debug(f"Barrier trend analysis skipped: {_barrier_trend_err}")

        briefing.append("### Complete Barrier Details:")
        for idx, barrier in ab_norm.iterrows():
            barrier_id = barrier.get('ID', barrier.get('id', ''))
            label = f"[AB-ID: {barrier_id}]" if barrier_id else f"Barrier {idx + 1}"
            briefing.append(f"**{label}:**")
            if 'subject_c' in barrier:
                briefing.append(f"- Subject: {barrier['subject_c']}")
            elif 'SUBJECT_C' in barrier:
                briefing.append(f"- Subject: {barrier['SUBJECT_C']}")
            elif 'title' in barrier:
                briefing.append(f"- Subject: {barrier['title']}")
            if 'customer_name' in barrier:
                # Round 13 / Phase 3.5: normalize per-barrier customer
                # so the canonical spelling matches the rollup above.
                _raw_b_cust = barrier.get('customer_name', '')
                try:
                    _cust_disp = normalize_customer_name(_raw_b_cust) if _raw_b_cust else ''
                except Exception:
                    _cust_disp = _raw_b_cust
                briefing.append(f"- Customer: {_cust_disp}")
            if 'ab_category_c' in barrier:
                briefing.append(f"- Category: {barrier['ab_category_c']}")
            elif 'AB_CATEGORY_C' in barrier:
                briefing.append(f"- Category: {barrier['AB_CATEGORY_C']}")
            if 'severity_c' in barrier:
                briefing.append(f"- Severity: {barrier['severity_c']}")
            elif 'SEVERITY_C' in barrier:
                briefing.append(f"- Severity: {barrier['SEVERITY_C']}")
            briefing.append("")

    return "\n".join(briefing)

def _create_minimal_briefing_book(manager, ab_norm, team_subs_df, technology):
    """Create a minimal briefing book when data is limited"""
    if ab_norm is None:
        ab_norm = pd.DataFrame()
    if team_subs_df is None:
        team_subs_df = pd.DataFrame()
    briefing = []

    briefing.append(f"# Executive Portfolio Analysis - {manager}")
    briefing.append(f"## Technology Focus: {technology}")
    # Round 12 / Phase 10.5: anchor briefing analysis date on UTC and tag the
    # timezone so cross-region operators see the same logical timestamp.
    briefing.append(f"## Analysis Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    briefing.append("")

    briefing.append("## Data Summary")
    import canonical_metrics as _r531_limited_cm
    briefing.append(f"- **Adoption Barriers Available:** {_r531_limited_cm.count_total_barriers(ab_norm)}")
    briefing.append(f"- **Team Subscriptions Available:** {len(team_subs_df) if not team_subs_df.empty else 0}")
    briefing.append("")

    if not ab_norm.empty:
        briefing.append("## Available Data Analysis")
        briefing.append("Limited data available for analysis. Focus on available adoption barriers.")

        # FIXED: Show ALL customers with data
        if 'customer_name' in ab_norm.columns:
            # Round 13 / Phase 3.8: dedupe on the canonical
            # ``normalize_customer_name`` value so a customer that
            # appears with both "Acme co." and "Acme Co" is listed
            # once, and the count headline matches the bullet list.
            customers = sorted(
                {
                    normalize_customer_name(_v)
                    for _v in ab_norm['customer_name'].dropna().tolist()
                    if normalize_customer_name(_v)
                }
            )
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
        arr_data=None, arr_impact=None, feature_requests=None, software_defects=None, psirt_vulns=None,
        ext_incidents=None, ext_bugs=None):
    """Create a COMPREHENSIVE briefing book for executive analysis with FULL DATA for AI to generate rich insights.
    Optional kwargs (arr_data, arr_impact, feature_requests, software_defects, psirt_vulns,
    ext_incidents, ext_bugs) enrich the briefing when provided.

    Round 4 / Phase 5.2: ``ext_incidents`` / ``ext_bugs`` were previously dropped on
    the compact path, leaving the LLM blind to status.webex incidents and
    help.webex defects. They are now serialized as a dedicated section so
    grounded analysis can cite real intel IDs instead of speculating.
    """
    if ab_norm is None:
        ab_norm = pd.DataFrame()
    if csone_df is None:
        csone_df = pd.DataFrame()
    if team_subs_df is None:
        team_subs_df = pd.DataFrame()
    briefing = []

    briefing.append(f"# Executive Portfolio Analysis - {manager}")
    briefing.append(f"## Technology Focus: {technology}")
    # Round 12 / Phase 10.5: anchor briefing analysis date on UTC and tag the
    # timezone so cross-region operators see the same logical timestamp.
    briefing.append(f"## Analysis Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    briefing.append("")

    # =========================================================================
    # SECTION 1: TEAM PORTFOLIO OVERVIEW
    # =========================================================================
    if not team_subs_df.empty:
        briefing.append("## Team Portfolio Overview")
        briefing.append(f"- **Total Team Subscriptions:** {len(team_subs_df)}")
        # Round 2 / Phase 4.2: route through canonical
        # ``cm.count_customers`` so this briefing line agrees with
        # every other report's "Unique Customers" headline.
        try:
            import canonical_metrics as _cm
            from data_normalization import build_customer_lookup as _build_lookup
            _lookup = _build_lookup(team_subs_df)
            _a2c = (_lookup or {}).get("account_to_customer", {}) or {}
            _unique_customers = _cm.count_customers(
                ab_df=team_subs_df,
                csone_df=None,
                account_to_customer=_a2c or None,
            )
        except Exception:
            _unique_customers = (
                team_subs_df['BU_NAME'].nunique() if 'BU_NAME' in team_subs_df.columns else 'N/A'
            )
        briefing.append(f"- **Unique Customers:** {_unique_customers}")
        briefing.append(f"- **Active CSSMs:** {team_subs_df['CSSM_EMAIL'].nunique() if 'CSSM_EMAIL' in team_subs_df.columns else 'N/A'}")
        briefing.append("")

        # FULL customer list (not truncated - AI needs this for comprehensive analysis)
        if 'BU_NAME' in team_subs_df.columns:
            # Round 13 / Phase 3.3: normalize and dedupe so the AI does
            # not see the same customer twice with cosmetic variants
            # (whitespace, NBSP, capitalization).  Mirrors Phase 3.2 in
            # the prior portfolio block above so both listings stay in
            # sync with the unique-customer headline.
            customers = sorted(
                {
                    normalize_customer_name(_v)
                    for _v in team_subs_df['BU_NAME'].dropna().tolist()
                    if normalize_customer_name(_v) and normalize_customer_name(_v) != "Unknown"
                }
            )
            briefing.append("### Complete Customer Portfolio:")
            for customer in customers:  # NO LIMIT - include all customers
                briefing.append(f"- {customer}")
            briefing.append("")

    # =========================================================================
    # SECTION 2: BEMS ESCALATION ANALYSIS (CRITICAL FOR EXECUTIVE VISIBILITY)
    # =========================================================================
    if not csone_df.empty:
        csone_norm = add_case_lifecycle_fields(csone_df)
        bems_mask = detect_bems_mask(csone_norm)
        bems_cases = csone_norm[bems_mask]
        total_bems = len(bems_cases)
        bems_rate = (total_bems / len(csone_df) * 100) if len(csone_df) > 0 else 0.0

        briefing.append("## 🔴 BEMS ESCALATION ANALYSIS (CRITICAL)")
        briefing.append(
            f"- **Total BEMS Escalations:** {total_bems} "
            f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
        )
        briefing.append(
            f"- **BEMS Rate:** {bems_rate:.1f}% of all TAC cases "
            f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
        )
        briefing.append("")

        if total_bems > 0:
            briefing.append("### BEMS Escalations by Customer (with BEMS IDs):")
            if 'customer_name' in bems_cases.columns:
                for customer in bems_cases['customer_name'].unique():
                    customer_bems = bems_cases[bems_cases['customer_name'] == customer]
                    # Extract BEMS IDs
                    bems_ids = set()
                    for _, row in customer_bems.iterrows():
                        bems_ids.update(extract_bems_ids_from_row(row))

                    # Format all BEMS IDs with brackets for citation
                    bems_id_list = ', '.join([f'[{bid}]' for bid in sorted(bems_ids)]) if bems_ids else 'IDs pending extraction'
                    briefing.append(
                        f"- **{customer}:** {len(customer_bems)} BEMS escalation(s) - {bems_id_list} "
                        f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
                    )
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
                cust_bems_count = int(detect_bems_mask(add_case_lifecycle_fields(cust_cases)).sum())

                if cust_bems_count > 0:
                    briefing.append(f"- **{customer}**: {count} cases ({cust_bems_count} BEMS escalations)")
                else:
                    briefing.append(f"- **{customer}**: {count} cases")
            briefing.append("")

        # Case severity analysis - FULL (canonical normalized priority for accuracy)
        # Use case_priority_norm (computed via normalize_priority_label) so the
        # distribution agrees with cm.count_p1/p2/etc. and the dashboards.
        # Falls back to a raw column only if normalization is unavailable.
        if 'case_priority_norm' in csone_norm.columns:
            severity = csone_norm['case_priority_norm'].fillna('Unknown').value_counts()
            briefing.append("### Case Severity Distribution (normalized P1-P4):")
            for sev, count in severity.items():
                briefing.append(f"- **Severity {sev}**: {count} cases")
            briefing.append("")

        # P1/P2 critical cases - DETAILED with case numbers, using normalized priority
        # so we never misclassify "P10" or "S12" as P1/P2 (the previous str.contains('1|2')
        # mask did exactly that and produced false positives in user-facing briefings).
        if 'case_priority_norm' in csone_norm.columns:
            critical_cases = csone_norm[csone_norm['case_priority_norm'].isin(['P1', 'P2'])]
            if not critical_cases.empty:
                # Show ALL critical cases (no truncation)
                briefing.append("### ALL Critical Cases (P1/P2) - REQUIRES IMMEDIATE ATTENTION:")
                for _, case in critical_cases.iterrows():
                    case_num = case.get('SR Number', case.get('Case Number', 'Unknown'))
                    title = case.get('Title', 'No title')
                    # Round 13 / Phase 3.6: normalize the customer label so
                    # the executive briefing's "ALL Critical Cases" listing
                    # matches the canonical spelling used elsewhere in the
                    # same briefing (rollups + ARR sections).
                    _raw_customer = case.get('customer_name', 'Unknown')
                    try:
                        customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown'
                    except Exception:
                        customer = _raw_customer or 'Unknown'
                    status = case.get('Case Status', 'Unknown')
                    sev = case.get('case_priority_norm', 'Unknown')
                    briefing.append(f"- TAC #{case_num} ({customer}): {title} - Severity: {sev}, Status: {status}")
                briefing.append("")

        # ALL Case Details - COMPREHENSIVE (for AI thematic analysis)
        # Iterate the normalized frame so the per-case Severity printed here
        # matches case_priority_norm used in the distribution + critical lists above.
        briefing.append("### Complete TAC Case Details for Analysis:")
        for idx, case in csone_norm.iterrows():
            case_num = case.get('SR Number', case.get('Case Number', f'Case-{idx}'))
            title = case.get('Title', 'No title')
            # Round 13 / Phase 3.6: normalize customer for the
            # comprehensive case-detail emission, matching the
            # critical-cases block above so cosmetic variants do not
            # surface as separate customer buckets in the model
            # context.
            _raw_customer = case.get('customer_name', 'Unknown')
            try:
                customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown'
            except Exception:
                customer = _raw_customer or 'Unknown'
            status = case.get('Case Status', 'Unknown')
            sev = case.get('case_priority_norm', case.get('Severity', case.get('Highest Priority', 'Unknown')))
            trans_id = case.get('Transaction ID', '')

            # Build comprehensive case line
            case_line = f"- TAC #{case_num} | Customer: {customer} | Severity: {sev} | Status: {status}"
            row_bems_ids = extract_bems_ids_from_row(case)
            if row_bems_ids:
                case_line += f" | BEMS: {', '.join(row_bems_ids)}"
            case_line += f" | Title: {title}"
            briefing.append(case_line)
            briefing.append("")

    # =========================================================================
    # SECTION 4: COMPREHENSIVE ADOPTION BARRIERS ANALYSIS
    # =========================================================================
    import canonical_metrics as _r531_full_cm
    if not ab_norm.empty:
        briefing.append("## Critical Adoption Barriers Analysis")
        def _r531_barrier_records_full(df: pd.DataFrame) -> pd.DataFrame:
            """Round 53.1: collapse duplicate AB fan-out rows for briefing counts."""

            if df is None or df.empty:
                return pd.DataFrame()
            if 'ID' not in df.columns:
                return df
            _with_id = df[df['ID'].notna()].drop_duplicates(subset=['ID'])
            _without_id = df[df['ID'].isna()]
            return pd.concat([_with_id, _without_id], ignore_index=True)

        ab_records = _r531_barrier_records_full(ab_norm)
        total_barrier_records = _r531_full_cm.count_total_barriers(ab_norm)
        briefing.append(f"- **Total Adoption Barriers:** {total_barrier_records}")

        # Open vs Closed barriers (status visibility)
        status_col = 'AB_STATUS_C' if 'AB_STATUS_C' in ab_norm.columns else ('STATUS_C' if 'STATUS_C' in ab_norm.columns else None)
        if status_col:
            open_count = _r531_full_cm.count_open_barriers(ab_norm)
            closed_count = _r531_full_cm.count_closed_barriers(ab_norm)
            other_count = max(total_barrier_records - open_count - closed_count, 0)
            briefing.append(f"- **Open/Active Barriers:** {open_count} | **Closed/Resolved:** {closed_count}" + (f" | **Other:** {other_count}" if other_count > 0 else ""))
        briefing.append("")

        # ALL customers with barriers (not truncated)
        if 'customer_name' in ab_norm.columns:
            # Round 13 / Phase 3.7: normalize before value_counts so a
            # customer that appears with both "Acme co." and "Acme Co"
            # rolls up into a single bucket.  Without this the
            # "Customers with Barriers" headline overcounted by the
            # number of cosmetic variants and the listing emitted the
            # same customer multiple times under sub-totals that did
            # not match the headline.
            _ab_for_count = ab_records.copy()
            _ab_for_count['_cust_disp'] = (
                _ab_for_count['customer_name']
                .fillna('Unknown')
                .apply(normalize_customer_name)
            )
            customer_barriers = _ab_for_count['_cust_disp'].value_counts()
            briefing.append(f"- **Customers with Barriers:** {len(customer_barriers)}")
            briefing.append("")

            briefing.append("### All Customers with Adoption Barriers (sorted by barrier count):")
            for customer, count in customer_barriers.items():  # ALL customers
                briefing.append(f"- **{customer}**: {count} barriers")
            briefing.append("")

        # Barrier categories - ALL categories
        cat_col = 'ab_category_c' if 'ab_category_c' in ab_norm.columns else ('ab_category_final' if 'ab_category_final' in ab_norm.columns else None)
        if cat_col:
            categories = ab_records[cat_col].value_counts()
            briefing.append("### Barrier Categories (complete breakdown):")
            for category, count in categories.items():  # ALL categories
                briefing.append(f"- **{category}**: {count} barriers")
            briefing.append("")

        # Severity analysis - ALL severities
        sev_col = 'severity_c' if 'severity_c' in ab_norm.columns else ('SEVERITY_C' if 'SEVERITY_C' in ab_norm.columns else None)
        if sev_col:
            severity = ab_records[sev_col].value_counts()
            briefing.append("### Barrier Severity Distribution:")
            for sev, count in severity.items():
                briefing.append(f"- **{sev}**: {count} barriers")
            briefing.append("")

            # Highlight HIGH/CRITICAL barriers using canonical severity
            # normalization to keep parity with leader/EI/compact reports
            # and avoid substring false positives like "Highest" or
            # "Critical-but-resolved" labels.
            try:
                from data_normalization import normalize_severity_label as _norm_sev
                _sev_series = ab_records[sev_col].apply(_norm_sev).fillna('').astype(str)
                high_sev = ab_records[_sev_series.isin(['Critical', 'High'])]
            except Exception:
                high_sev = ab_norm.iloc[0:0]
            if not high_sev.empty:
                briefing.append("### HIGH/CRITICAL Severity Barriers - REQUIRES ATTENTION:")
                for _hi_idx, barrier in high_sev.iterrows():
                    subj = barrier.get('SUBJECT_C', barrier.get('subject_c', barrier.get('title', 'No subject')))
                    # Round 13 / Phase 3.7: normalize customer for the
                    # high/critical barrier listing so the bullet matches
                    # the customer label used in the rollup section above.
                    _raw_customer = barrier.get('customer_name', 'Unknown')
                    try:
                        customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown'
                    except Exception:
                        customer = _raw_customer or 'Unknown'
                    sev = barrier.get(sev_col, 'Unknown')
                    status = barrier.get('AB_STATUS_C', barrier.get('STATUS_C', 'Unknown'))
                    b_id = barrier.get('ID', barrier.get('id', ''))
                    id_tag = f" [AB-ID: {b_id}]" if b_id else ''
                    briefing.append(f"- **{customer}**: {subj} - Severity: {sev}, Status: {status}{id_tag}")
                briefing.append("")

        # ALL Adoption Barrier Titles - COMPREHENSIVE for AI thematic analysis
        briefing.append("### All Adoption Barrier Titles for Thematic Analysis:")
        subj_col = 'SUBJECT_C' if 'SUBJECT_C' in ab_norm.columns else ('subject_c' if 'subject_c' in ab_norm.columns else ('title' if 'title' in ab_norm.columns else None))
        if subj_col:
            for _, barrier in ab_records.iterrows():
                # Round 13 / Phase 3.7: normalize customer for the
                # thematic-analysis listing so cosmetic variants
                # don't fan out into multiple model-context buckets.
                _raw_customer = barrier.get('customer_name', 'Unknown')
                try:
                    customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown'
                except Exception:
                    customer = _raw_customer or 'Unknown'
                subject = barrier.get(subj_col, 'No subject')
                sev = barrier.get(sev_col, 'N/A') if sev_col else 'N/A'
                cat = barrier.get(cat_col, 'Uncategorized') if cat_col else 'Uncategorized'
                b_id = barrier.get('ID', barrier.get('id', ''))
                id_tag = f" [AB-ID: {b_id}]" if b_id else ''
                briefing.append(f"- [{customer}] {subject} (Severity: {sev}, Category: {cat}){id_tag}")
        briefing.append("")

        # Complete barrier details with record IDs for traceability
        briefing.append("### Complete Adoption Barrier Details (with CSConsole Record IDs):")
        for idx, barrier in ab_records.iterrows():
            record_id = barrier.get('ID', barrier.get('RECORD_ID', f'AB-{idx}'))
            subj = barrier.get('SUBJECT_C', barrier.get('subject_c', barrier.get('title', 'No subject')))
            # Round 13 / Phase 3.7: normalize customer for the complete
            # barrier-detail emission as well.
            _raw_customer = barrier.get('customer_name', 'Unknown')
            try:
                customer = normalize_customer_name(_raw_customer) if _raw_customer else 'Unknown'
            except Exception:
                customer = _raw_customer or 'Unknown'
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
    # Round 11 / Phase 1.4 (second briefing path): same currency-aware
    # prefix/suffix gating applied here so the secondary "## ARR by
    # Customer" / "## ARR at Risk" / "## Feature Requests" sections do
    # not silently print ``$`` for non-USD or mixed-currency portfolios.
    # Round 11 / Phase 1.5: ``groupby('BU_NAME')`` collides distinct
    # ACCOUNT_ID_C values that share the same display name (R10 / Phase
    # 3.1 fixed the same class of bug on portfolio concentration).
    # Group on ACCOUNT_ID_C when available and use BU_NAME purely as a
    # display label so a $50M and $200k account that happen to share a
    # display string are not folded into one "$50.2M customer" row.
    if arr_data is not None and not arr_data.empty and 'BU_NAME' in arr_data.columns and 'ANNUAL_CONTRACT_VALUE' in arr_data.columns:
        _has_ccy_b = 'CURRENCY_CODE' in arr_data.columns
        try:
            _ccy_set_b = set(arr_data['CURRENCY_CODE'].dropna().astype(str).str.upper().unique()) if _has_ccy_b else set()
        except Exception:
            _ccy_set_b = set()
        if not _ccy_set_b:
            _amt_pre_b, _amt_suf_b = '', ' (currency unknown)'
        elif len(_ccy_set_b) == 1:
            _ccy_only_b = next(iter(_ccy_set_b))
            _amt_pre_b, _amt_suf_b = ('$' if _ccy_only_b == 'USD' else f"{_ccy_only_b} "), ''
        else:
            _amt_pre_b, _amt_suf_b = '', ' (MIXED currencies — do not sum)'

        if 'ACCOUNT_ID_C' in arr_data.columns:
            try:
                _per_acct = (
                    arr_data[['ACCOUNT_ID_C', 'BU_NAME', 'ANNUAL_CONTRACT_VALUE']]
                    .dropna(subset=['ACCOUNT_ID_C'])
                    .groupby('ACCOUNT_ID_C', as_index=False)
                    .agg({'BU_NAME': 'first', 'ANNUAL_CONTRACT_VALUE': 'sum'})
                    .sort_values('ANNUAL_CONTRACT_VALUE', ascending=False)
                )
                arr_by_cust_pairs = list(zip(_per_acct['BU_NAME'], _per_acct['ANNUAL_CONTRACT_VALUE']))
                total_arr = float(_per_acct['ANNUAL_CONTRACT_VALUE'].sum())
            except Exception as _per_acct_err:
                logger.debug("Briefing per-account ARR rollup failed: %s", _per_acct_err)
                # Round 13 / Phase 3.9: canonicalize BU_NAME first so
                # cosmetic spelling drift doesn't fragment the customer
                # rollup (otherwise % concentration is artificially low).
                _arr_data_norm = arr_data.copy()
                _arr_data_norm['_bu_disp'] = (
                    _arr_data_norm['BU_NAME'].fillna('Unknown').apply(normalize_customer_name)
                )
                arr_by_cust = _arr_data_norm.groupby('_bu_disp')['ANNUAL_CONTRACT_VALUE'].sum().sort_values(ascending=False)
                arr_by_cust_pairs = list(arr_by_cust.items())
                total_arr = float(arr_by_cust.sum())
        else:
            # Round 13 / Phase 3.9: same as the except branch above.
            _arr_data_norm = arr_data.copy()
            _arr_data_norm['_bu_disp'] = (
                _arr_data_norm['BU_NAME'].fillna('Unknown').apply(normalize_customer_name)
            )
            arr_by_cust = _arr_data_norm.groupby('_bu_disp')['ANNUAL_CONTRACT_VALUE'].sum().sort_values(ascending=False)
            arr_by_cust_pairs = list(arr_by_cust.items())
            total_arr = float(arr_by_cust.sum())

        briefing.append("## ARR by Customer (Strategic Prioritization)")
        briefing.append("**Prioritize high-ARR customers with adoption barriers or support cases.**")
        for cust, arr_val in arr_by_cust_pairs[:20]:
            # Round 30 / L3: ``_safe_div`` for canonical zero/NaN
            # handling on portfolio share.
            pct = _safe_div(arr_val, total_arr) * 100
            briefing.append(f"- **{cust}:** {_amt_pre_b}{_r12_format_number(arr_val)} ({pct:.1f}% of portfolio){_amt_suf_b}")
        if len(arr_by_cust_pairs) > 20:
            briefing.append(f"- ... and {len(arr_by_cust_pairs) - 20} more customers")
        briefing.append(f"**Total Portfolio ARR:** {_amt_pre_b}{_r12_format_number(total_arr)}{_amt_suf_b}")
        briefing.append("")
    else:
        _amt_pre_b, _amt_suf_b = '', ' (currency unknown)'

    if arr_impact and arr_impact.get('top_issues'):
        briefing.append("## ARR at Risk by Issue Category")
        briefing.append("**Prioritize interventions for issue categories with highest ARR exposure.**")
        for issue_name, data in arr_impact['top_issues'][:10]:
            arr_val = data.get('arr', 0)
            cust_count = data.get('customer_count', 0)
            briefing.append(
                f"- **{issue_name}:** {_amt_pre_b}{_r12_format_number(arr_val)} at risk "
                f"({cust_count} customers){_amt_suf_b}"
            )
        briefing.append("")

    if feature_requests and feature_requests.get('total_requests', 0) > 0:
        briefing.append("## Feature Requests (Product Gap Signal)")
        # Round 12 / Phase 1.1: honour ``arr_impact_comparable`` to
        # avoid printing a misleading "$0" figure when the underlying
        # ARR data spans multiple currencies.
        if feature_requests.get('arr_impact_comparable', True):
            _arr_impact_str = (
                f"{_amt_pre_b}{_r12_format_number(feature_requests.get('total_arr_impact', 0))}{_amt_suf_b}"
            )
        else:
            _ccys = feature_requests.get('arr_impact_currencies') or []
            _arr_impact_str = (
                "n/a (mixed currencies"
                + (": " + ", ".join(_ccys) if _ccys else "")
                + ")"
            )
        briefing.append(
            f"**{feature_requests['total_requests']}** cases contain feature requests. "
            f"ARR impact: {_arr_impact_str}"
        )
        if feature_requests.get('top_features'):
            briefing.append("**Most requested themes:**")
            for theme, count in feature_requests['top_features'][:5]:
                briefing.append(f"- {theme}: {count} cases")
        if feature_requests.get('customer_examples'):
            top_by_arr = sorted([c for c in feature_requests['customer_examples'] if c.get('arr', 0) > 0], key=lambda x: x.get('arr', 0), reverse=True)[:5]
            if top_by_arr:
                briefing.append("**High-value customers requesting features:**")
                for c in top_by_arr:
                    briefing.append(
                        f"- {c.get('customer', 'N/A')}: "
                        f"{_amt_pre_b}{_r12_format_number(c.get('arr', 0))} ARR{_amt_suf_b}"
                    )
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
    # SECTION 4b (Round 4 / Phase 5.2): EXTERNAL INTELLIGENCE
    # status.webex.com incidents and help.webex defects.  These are
    # serialized so the LLM can cite real incident IDs / bug IDs and
    # not invent them.  The compact path now passes ``ext_incidents``
    # and ``ext_bugs`` through; the comprehensive path passes them via
    # the ``_create_briefing_book`` helper above.  When omitted (older
    # callers) we explicitly disclose unavailability instead of
    # implying zero.
    # =========================================================================
    if ext_incidents is None and ext_bugs is None:
        briefing.append("## External Intelligence (status.webex / help.webex)")
        briefing.append(
            "- External intelligence was NOT supplied for this run; treat as "
            "data-unavailable, not zero."
        )
        briefing.append("")
    else:
        briefing.append("## External Intelligence (status.webex / help.webex)")
        try:
            _ei = ext_incidents or []
            _eb = ext_bugs or []
            briefing.append(f"- **Service Incidents (status.webex.com):** {len(_ei)}")
            briefing.append(f"- **Bug References (help.webex.com):** {len(_eb)}")
            if _ei:
                briefing.append("")
                briefing.append("### Recent Service Incidents (top 15 by published date):")
                try:
                    _ei_sorted = sorted(
                        _ei,
                        key=lambda x: str(x.get('published') or x.get('last_seen') or ''),
                        reverse=True,
                    )
                except Exception:
                    _ei_sorted = list(_ei)
                for inc in _ei_sorted[:15]:
                    _iid = inc.get('id') or inc.get('incident_id') or ''
                    _title = (inc.get('title') or inc.get('summary') or '').strip()
                    _impact = inc.get('impact') or inc.get('severity') or ''
                    _pub = inc.get('published') or inc.get('last_seen') or ''
                    briefing.append(
                        f"- [{_iid}] {_title[:160]} (impact={_impact}, published={_pub})"
                    )
            if _eb:
                briefing.append("")
                briefing.append("### Help Center Bug References (top 15):")
                for bug in _eb[:15]:
                    _bid = bug.get('bug_id') or bug.get('id') or ''
                    _btitle = (bug.get('title') or bug.get('summary') or '').strip()
                    briefing.append(f"- [{_bid}] {_btitle[:160]}")
        except Exception as _ei_err:
            briefing.append(
                f"- (External intel could not be serialized: {_ei_err}; treat as unavailable.)"
            )
        briefing.append("")

    # =========================================================================
    # SECTION 5: DATA QUALITY AND SOURCES SUMMARY
    # =========================================================================
    briefing.append("## Data Sources and Quality Summary")
    briefing.append("This analysis is based on the following data sources:")
    briefing.append(f"- **CSConsole (Snowflake)**: {_r531_full_cm.count_total_barriers(ab_norm)} adoption barrier records")
    briefing.append(f"- **CSOne (TAC Cases)**: {len(csone_df) if not csone_df.empty else 0} support cases")
    briefing.append(f"- **Team Subscriptions**: {len(team_subs_df) if not team_subs_df.empty else 0} subscriptions")
    # Round 4 / Phase 5.2: also disclose external intel availability so the
    # LLM cannot tacitly assume "no incidents" when the feed is unavailable.
    if ext_incidents is None and ext_bugs is None:
        briefing.append(
            "- **External Intelligence**: NOT supplied (treat as data-unavailable, not zero)"
        )
    else:
        briefing.append(
            f"- **External Intelligence**: {len(ext_incidents or [])} incidents, "
            f"{len(ext_bugs or [])} bug references"
        )
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

**🔒 CANONICAL TOTALS (Round 25 / Phase B — MUST use these exact values):**
The following totals are computed by the canonical metrics pipeline directly from the displayed data sheets (Adoption Barriers ∪ TAC ∪ Customer Pulse).  When the **Executive Summary: What's Really Happening** "Portfolio Snapshot" block below names a count, you MUST use the value listed here verbatim.  Do NOT recompute, round, summarize as a range, or substitute "approximately" -- a downstream validator compares your rendered numbers to these canonical values and will block the report build on any drift.
- **Total Customers:** {TOTAL_CUSTOMERS}
- **Total Adoption Barriers:** {TOTAL_BARRIERS}
- **TAC Cases (total):** {TAC_CASES}
- **TAC Cases (P1 / Critical):** {P1_CASES}
- **TAC Cases (P2 / High):** {P2_CASES}
- **BEMS Escalations:** {BEMS_ESCALATIONS}

**CRITICAL REQUIREMENTS:**
- **Show the Problems:** Don't sugarcoat - executives need to see the real issues
- **Cite Specifics:** Reference actual record identifiers from the data: AB-IDs for adoption barriers, SP-IDs for success priorities, AP-IDs for action plans, CSC IDs for software defects, BEMS IDs for escalations, Case IDs for TAC cases, and incident IDs for service disruptions. These identifiers let readers verify and follow up on each claim
- **Quantify Impact:** How many customers? What's the business impact?
- **Flag Escalations:** BEMS escalations are RED FLAGS - call them out explicitly
- **Define Barriers:** Clearly explain what adoption barriers are blocking customers

**TECHNOLOGY FOCUS:** This analysis covers **{TECHNOLOGY}** adoption and support within {MANAGER}'s portfolio.

**REASONING PROTOCOL (follow this before writing):**
1. **IDENTIFY** the single most critical signal first: BEMS escalations > P1/P2 cases > critical adoption barriers > software defects > general barriers
2. **CROSS-REFERENCE** across data domains: Do customers with BEMS also have adoption barriers? Do defect IDs in cases match known bugs? Do high-ARR customers overlap with high-barrier counts?
3. **QUANTIFY** the revenue exposure ONLY IF ARR fields are present in the briefing - otherwise state "ARR data not available for this run".
4. **SYNTHESIZE** into a narrative that connects the dots - don't just list data, explain what it means together

**DATA QUALITY NOTE:** If any data section appears incomplete, has unusual patterns (e.g., zero barriers for a large portfolio, missing severity fields), or shows anomalies, explicitly call this out. State what data may be missing and how it affects your confidence in the analysis.

**Round 4 / Phase 6.4 — NEGATIVE CONSTRAINTS (HARD RULES):**
- Do NOT invent ARR / revenue / dollar figures. If "ARR" or "$" does not appear in the briefing book above, state "ARR data not provided" and do not estimate, project, or extrapolate dollar values.
- Do NOT invent percentage figures (e.g., "85% adoption", "30% churn risk") that are not explicitly present in the briefing book. If a percentage is needed and not present, write "(% not available)".
- Do NOT extrapolate counts. Only quote counts (BEMS, TAC, AB, defects, customers, etc.) that appear verbatim in the briefing's `Data Sources and Quality Summary` or in a labeled section. If you must summarize, prefix with "Per the briefing book, …" so readers know it is sourced.
- Do NOT cite IDs (TAC, AB, BEMS, CSC, incident, action plan) that are not literally present in the briefing book. Echoing an ID that the briefing did not list is a hallucination.
- If a section's data is `NOT supplied` / `unavailable` per the briefing, you MUST write "data unavailable" rather than implying zero or improvising filler text.

---

## **Portfolio Health Score: [A/B/C/D/F]**

**Grade Justification:**
*State the grade and justify with SPECIFIC metrics drawn from the briefing book: BEMS escalation count, critical-defect count, count of affected customers, and chronic-issue count.  Quote the numbers EXACTLY as they appear in the briefing -- do NOT compute new ratios or rates (e.g. "escalation rate at V%") that the briefing does not already state.  If a rate was not provided, write "(rate not available)" instead of estimating it.  Be direct about whether this portfolio is healthy, at-risk, or in crisis.*

---

## **Executive Summary: What's Really Happening**

**Portfolio Snapshot:** (use the CANONICAL TOTALS above verbatim)
• **Total Customers:** {TOTAL_CUSTOMERS}
• **Total Adoption Barriers:** {TOTAL_BARRIERS} (provide severity breakdown narrative -- do NOT change the total)
• **TAC Cases:** {TAC_CASES} (with {P1_CASES} P1 and {P2_CASES} P2)
• **BEMS Escalations:** {BEMS_ESCALATIONS} - THIS IS CRITICAL
• **Known Defects Impacting Portfolio:** [Count from help.webex.com -- cite the briefing's Help Center section verbatim]
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

## **All Customers in Trouble (from this Briefing, Sorted by Risk)**

*List every customer **listed in this briefing book** that has severe issues, sorted by risk level. Do NOT limit to just 5. Round 6 / Phase 3.4: This is intentionally scoped to "customers listed in the briefing" rather than "ALL customers" -- if the briefing was truncated or partial, only the customers it actually contains are valid; do not invent or extrapolate to customers it does not name. If the briefing notes a partial fetch, say so explicitly here.*

**🔒 Round 25 / Phase C — RISK BAND BINDING (READ BEFORE WRITING):**
The briefing book contains a section titled **"Canonical Risk Bands (Round 25 / Phase C)"** with each customer's authoritative risk band (CRITICAL / HIGH / MEDIUM / LOW / HEALTHY) computed by the canonical risk-scoring pipeline.  When you assign a `Risk Level:` to any customer below, you MUST use the band listed there verbatim.  Specifically:

- Use ONLY these five labels: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `HEALTHY`.
- Do NOT invent compound labels like "HIGH/CRITICAL" or "MEDIUM/LOW".  Pick the one canonical band the briefing assigns.
- Do NOT promote, demote, merge, or substitute bands ("MODERATE" → MEDIUM is a substitution; do not do this).
- Do NOT list a customer as CRITICAL or HIGH unless the briefing's "Canonical Risk Bands" section says so.  A downstream validator counts narrated CRITICAL+HIGH lines and compares to the canonical `high_risk_customers` count -- any drift blocks the report build.

If the briefing's canonical risk-band section is missing or truncated, say so explicitly in the narrative ("canonical risk-band data was unavailable for this run") and do not free-style.

**1. [Customer Name] - Risk Level: [CRITICAL]**
• **Problem Summary:** [What's really going wrong?]
• **Adoption Barriers:** [X barriers] - **Key Issue:** [Main blocking issue with details]
• **Total Support Cases (90d):** [Y] - **Open + critical (P1+P2):** [Z] ([W are P2])
  *Round 48 / F-COMP-TAC-LABEL-AMBIGUITY: do NOT emit a generic
  "TAC Cases: N cases" line.  Use the canonical pair "Total Support
  Cases (90d)" + "Open + critical (P1+P2)" so the reader can
  reconcile the bullet against the at-a-glance dashboard.*
• **Active Problems:** [Specific issues]
• **BEMS Escalations:** [If any, with BEMS IDs like [BEMS01916938]]
• **Defects Impacting:** [List any known defects affecting this customer with IDs like [CSCxx12345]]
• **Business Impact:** [How is this affecting their business?]
• **Immediate Action Needed:** [Specific, actionable next step]

**2. [Customer Name] - Risk Level: [HIGH]**
• [Same detailed format - continue for ALL troubled customers]

**3. [Customer Name] - Risk Level: [MEDIUM]**
• [Same detailed format]

**4. [Customer Name] - Risk Level: [MEDIUM]**
• [Same detailed format]

**5. [Customer Name] - Risk Level: [LOW]**
• [Same detailed format]

*Round 25 / Phase C: each `Risk Level: [X]` MUST be one of the five canonical bands (CRITICAL, HIGH, MEDIUM, LOW, HEALTHY) -- never a compound like "HIGH/CRITICAL" or "MEDIUM/LOW".*

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
- For adoption barriers: `(AB-ID: [ID])`
- For success priorities: `(SP-ID: [ID])`
- For action plans: `(AP-ID: [ID])`
- For CSOne/TAC cases: `(TAC Case: [Case Number])`
- For software defects: `(CSC ID: [ID])`
- For BEMS escalations: `(BEMS: [ID])`
- For service incidents: `(Incident ID: [ID])`

**CSConsole DATA ANALYSIS REQUIREMENT:** You MUST comprehensively analyze and reference CSConsole data for this customer including:
- **Action Plans:** Strategic initiatives specific to this customer and their impact
- **Customer Pulse:** Real-time sentiment and engagement indicators for this customer
- **Success Priorities:** Key business outcomes and objectives for this customer
- **CSConsole Adoption Barriers:** Additional context beyond standard tracking for this customer
- **CSSM Attribution:** Always reference the responsible CSSM ({CSSM_NAME}) for accountability

**FULL TEXT REQUIREMENT:** When quoting or referencing specific adoption barriers or CSOne cases, include the complete text **as it appears in the briefing for this customer**. Round 6 / Phase 3.18: the briefing book DOES truncate long sections (per-section character cap and field-level truncation are applied upstream); when the briefing already shows ellipses or a `[SECTION TRUNCATED: ...]` / `[Evidence truncated: ...]` marker for a value, quote the briefing exactly (including the marker). Do NOT invent the missing text and do NOT silently drop the truncation marker — readers must be able to see that the underlying source was capped. The "Complete Adoption Barrier Details" and "Complete CSOne (TAC) Case Details" sections contain the most complete copy of each item that the briefing was able to fit; treat that as the authoritative source.

**Round 6 / Phase 3.5 — NEGATIVE CONSTRAINTS (HARD RULES):**
- Do NOT invent ARR / revenue / dollar figures. If "ARR" or "$" does not appear in the briefing for {CUSTOMER_NAME}, write "ARR data not provided" and do not estimate or extrapolate dollar values.
- Do NOT invent percentage figures (e.g., "85% adoption", "30% churn risk") that are not explicitly present in the briefing. If a percentage is needed and not present, write "(% not available)".
- Do NOT extrapolate counts. Only quote counts (BEMS, TAC, AB, defects, incidents, action plans, etc.) that appear verbatim in the briefing for {CUSTOMER_NAME}. If you must summarize, prefix with "Per the briefing book, …" so readers know it is sourced.
- Do NOT cite IDs (TAC, AB, BEMS, CSC, incident, action plan, success priority) that are not literally present in the briefing for {CUSTOMER_NAME}. Echoing an ID that the briefing did not list is a hallucination.
- If a section's data is `NOT supplied` / `unavailable` / `None detected` per the briefing, you MUST write "data unavailable" rather than implying zero or improvising filler text.
- Do NOT introduce facts about other customers, other technologies, or other CSSMs that are not explicitly present in the briefing.

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
Generate a detailed, customer-specific report in Markdown. Do NOT omit any headers; state 'data unavailable' if a section is empty (Round 45 / Phase 6: replaces the prior 'None detected' wording so it stops contradicting the NEGATIVE CONSTRAINT at the top of this prompt — see ``L10657`` which already mandates 'data unavailable').

# AdoptIQ Executive Analysis: {CUSTOMER_NAME} - {TECHNOLOGY} Focus
**Assigned CSSM:** {CSSM_NAME}
**Technology Focus:** {TECHNOLOGY}

### **Customer Health Score: <one letter A | B | C | D | F, no brackets, no quotes>**
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

### **6. Predictive Risk Assessment**
*   **Churn Risk Level:** [Low, Medium, High, Critical]
*   **Risk Factors:** [Specific indicators that suggest potential issues or opportunities, **including BEMS escalation patterns**]
*   **BEMS Risk Analysis:** [If BEMS escalations exist for this customer, **list the count and specific BEMS IDs** (e.g., "3 BEMS escalations: BEMS-12345, BEMS-67890, BEMS-11111"). If no BEMS, state "No BEMS escalations". These indicate complex technical issues requiring specialized engineering expertise]
*   **Success Probability:** [Likelihood of successful {TECHNOLOGY} adoption and value realization]
*   **Timeline Projections:** [Where this customer is heading in the next 6-12 months]
*   **Early Warning Signs:** [Specific patterns that require immediate attention, **especially BEMS escalation trends**]

### **7. Strategic Recommendations & Action Plan**
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

### **8. External Intelligence & Competitive Context**
*   **Software Defects Impact:** [How publicly known bugs (help.webex.com) correlate with this customer's specific issues]
*   **Service Incident Correlation:** [Impact of recent service incidents (status.webex.com) on this customer's experience]
*   **Cross-Reference Analysis:** [Specific customer issues that align with known software defects or service incidents]
*   **Industry Benchmarking:** [How this customer's {TECHNOLOGY} adoption compares to industry peers]
*   **Competitive Positioning:** [Their {TECHNOLOGY} capabilities vs. market alternatives]
*   **Innovation Opportunities:** [Emerging {TECHNOLOGY} capabilities they could leverage]

### **9. Success Metrics & Monitoring Plan**
*   **Key Performance Indicators:** [Specific metrics to track {TECHNOLOGY} success]
*   **Monitoring Frequency:** [How often to review progress and adjust strategy]
*   **Escalation Triggers:** [Specific conditions that require immediate intervention]
*   **Success Celebration:** [How to recognize and reinforce positive outcomes]

**CORRELATION MANDATE:** Before finalizing your analysis, you MUST connect adoption barriers with TAC case themes and external bugs. If a customer has both barriers and cases about the same technology area, that is a compound risk signal. Identify the single most impactful action that would address the largest cluster of connected issues.
"""

def _json_lite(df: pd.DataFrame, limit=60, keep=None) -> str:
    if df is None or df.empty:
        return "[]"
    use = df.copy()
    if keep:
        use = use[[c for c in keep if c in use.columns]]
    # Round 12 / Phase 7.3: previously this helper called
    # ``use.head(limit)`` directly on a frame in arbitrary upstream
    # order, so the LLM context was non-deterministic across reruns
    # of the same report -- two runs of the same briefing could
    # contain different "first 60" rows and the model would narrate
    # different exemplars.  Sort on the most stable identifier
    # column we can find before slicing so the head() window is
    # reproducible.  Use ``kind='stable'`` to preserve original
    # order for ties.  Fail open to the legacy head() if the sort
    # itself raises (e.g. mixed-type columns).
    try:
        _stable_keys = [
            'display_id',
            'SR Number',
            'Case Number',
            'CASE_ID',
            'CASE_NUMBER',
            'AB_ID',
            'BARRIER_ID',
            'ID',
            'BEMS_ID',
        ]
        _sort_col = next((c for c in _stable_keys if c in use.columns), None)
        if _sort_col is not None:
            use = use.sort_values(
                _sort_col,
                kind='stable',
                ascending=True,
                na_position='last',
            )
    except Exception:  # Round 12 / Phase 7.3 defensive
        pass
    return use.head(limit).to_json(orient="records")

# Round 45 / Phase 6: regex-based post-process pass that scrubs the
# residual "None detected" / "(SP-ID: None detected)" wording the LLM
# was emitting in the comprehensive narrative even though the prompt
# (L10657) already mandates "data unavailable".  The 2026-04-28
# build-20 comprehensive Word artifact contained 8 paragraphs with
# this leak because the OUTPUT FORMAT instruction at L10682 used to
# say `state 'None detected' if a section is empty`, contradicting
# the NEGATIVE CONSTRAINT.  Phase 6 fixes the prompt, but the model
# may still echo the old wording on cached or edge-case responses
# (especially for the SP-ID parenthetical), so this regex pass acts
# as a belt-and-suspenders post-fix that runs on every successful
# LLM response.  Pure / deterministic / cheap (~6 regex passes on
# strings that are already in memory).
_R45_NONE_DETECTED_PATTERNS: tuple = (
    # SP-ID parenthetical specifically.  Order matters -- fix the
    # parenthetical before the bare phrase so the inner text is
    # rewritten to a friendlier label, not just the literal phrase.
    (re.compile(r"\(SP-ID:\s*None detected\)", re.IGNORECASE), "(SP-ID: not provided)"),
    # Bare phrase.  Word boundary on both sides so we don't break
    # accidental embedded substrings like "phenomenon detected".
    (re.compile(r"\bNone detected\b", re.IGNORECASE), "data unavailable"),
)


def _r45_clean_llm_chrome(text: str) -> str:
    """Round 45 / Phase 6: scrub residual 'None detected' chrome from
    a successful LLM response so the comprehensive Word artifact
    consistently shows 'data unavailable' (matching the NEGATIVE
    CONSTRAINT at adoptiq_backend.py:10657).

    Pure / safe -- never raises.  Returns ``text`` unchanged on any
    error so a regex regression cannot block the report build.
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        out = text
        for pat, repl in _R45_NONE_DETECTED_PATTERNS:
            out = pat.sub(repl, out)
        return out
    except Exception:  # noqa: BLE001 -- never let the post-process
        # break the report build; the worst case is the residual
        # chrome stays in the narrative (same as pre-Round-45).
        return text


def generate_llm_response(
    system_prompt: str,
    briefing_book: str,
    *,
    model_name: Optional[str] = None,
) -> str:
    """Generic function to call the CircuIT client with timeout and fallback.

    Round 69 / Build 43: ``model_name`` is now keyword-only and
    optional.  When supplied (non-empty), it overrides
    ``CIRCUIT_CONFIG['model_name']`` for THIS call only -- the global
    is not mutated, so the per-request model resolved by
    ``model_resolver.get_active_*_model()`` cleanly threads through
    without contaminating sibling call sites.  When omitted/None/empty,
    behaviour is byte-identical to the pre-R69 path so no existing
    test or call site needs to change.
    """
    import signal
    import time
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

    # Round 69 / Build 43: resolve the effective model name once, BEFORE
    # entering the worker thread so the log line below can name it.
    # Empty string is treated the same as None (= fall back to global).
    _effective_model_name = (
        str(model_name).strip()
        if (model_name and isinstance(model_name, str) and str(model_name).strip())
        else CIRCUIT_CONFIG.get("model_name", "")
    )

    def call_circuit_ai():
        """Call CircuIT AI in a separate thread with proper error handling."""
        try:
            if not (CIRCUIT_CONFIG.get("client_id") and CIRCUIT_CONFIG.get("client_secret") and CIRCUIT_CONFIG.get("app_key")):
                return "ERROR: CircuIT credentials not set. Add CIRCUIT_CLIENT_ID, CIRCUIT_CLIENT_SECRET, and CIRCUIT_APP_KEY to your .env file (see .env.template)."
            logger.info(f"[[AI]] Creating CircuIT AI client (model=%s)...", _effective_model_name)
            client = CircuitChatClient(
                client_id=CIRCUIT_CONFIG["client_id"],
                client_secret=CIRCUIT_CONFIG["client_secret"],
                app_key=CIRCUIT_CONFIG["app_key"],
                model_name=_effective_model_name,  # Round 69 / Build 43
            )

            # Round 6 / Phase 3.7: log using the shared timeout
            # constant rather than a hard-coded literal.
            logger.info(
                f"[[AI]] Calling CircuIT AI with {CircuitChatClient.REQUEST_TIMEOUT_SECONDS}s timeout..."
            )
            result = client.complete(system_prompt, briefing_book)
            return result
        except Exception as e:
            logger.error(f"[[ERROR]] CircuIT AI call failed: {e}")
            return None

    # Round 6 / Phase 3.7: align the future.result() wall-clock budget
    # with the underlying CircuIT HTTP timeout, plus a small grace
    # window for thread bookkeeping and JSON parsing on the way back.
    # Previously the executor enforced a 60s deadline while the HTTP
    # client allowed 120s, so we could cancel a still-healthy call.
    _circuit_timeout_s = int(CircuitChatClient.REQUEST_TIMEOUT_SECONDS) + 5

    try:
        logger.info(
            f"[[AI]] Starting CircuIT AI analysis with {_circuit_timeout_s}-second timeout..."
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(call_circuit_ai)
            result = future.result(timeout=_circuit_timeout_s)

            if result and isinstance(result, str) and result.startswith("ERROR:"):
                # Round 6 / Phase 3.1: preserve a classified error
                # verbatim.  The downstream caller can already
                # interpret ``ERROR: content_filter:`` /
                # ``ERROR: llm.rate_limit_429:`` etc. and wants the
                # ``error_kind`` intact.  Previously this branch
                # collapsed every classified failure into a generic
                # "summarization failed" string and lost all
                # diagnostic information.
                #
                # Round 7 / Phase 5.6: do not log the first 120 chars
                # of the classified ERROR string -- that body often
                # contains the upstream provider's raw exception
                # message which can leak prompt fragments, stack
                # frames, internal endpoints, customer identifiers
                # echoed back from the model, or transient JWTs from
                # the auth handshake.  Log only the parsed
                # ``kind=...`` token plus an 8-char ``digest=...`` of
                # the full body so operators can correlate identical
                # failures across log lines without exposing the
                # payload.  The digest uses SHA-256 (truncated for
                # readability); the full body is still returned to
                # the caller for downstream branching.
                import hashlib as _h
                # Round 14 / Phase 4.4: ``_kind_token`` is the parsed
                # error classifier (e.g. ``timeout``, ``rate_limited``)
                # not a credential -- ``S105`` flags the variable name
                # because of the substring ``token``.  Pin a noqa per
                # line so the false positive is documented in context.
                _kind_token = "unknown"  # noqa: S105 -- error-kind label, not a secret
                try:
                    _after = result[len("ERROR:"):].lstrip()
                    _kind_token = _after.split(":", 1)[0].strip() or "unknown"  # noqa: S105 -- error-kind label
                except Exception:
                    _kind_token = "unknown"  # noqa: S105 -- error-kind label
                try:
                    _digest = _h.sha256(result.encode("utf-8", errors="replace")).hexdigest()[:8]
                except Exception:
                    _digest = "n/a"
                logger.warning(
                    "[[WARNING]] CircuIT AI returned classified ERROR kind=%s digest=%s len=%d",
                    _kind_token,
                    _digest,
                    len(result),
                )
                return result
            if result and result.strip():
                # Round 45 / Phase 6: scrub residual "None detected" /
                # "(SP-ID: None detected)" chrome before returning.  The
                # post-process is idempotent and pure -- the worst case
                # is no-op when the model already followed the prompt.
                _cleaned = _r45_clean_llm_chrome(result)
                if _cleaned != result:
                    logger.info(
                        "[[AI]] Round 45 / Phase 6: scrubbed residual "
                        "'None detected' chrome from LLM response (len_in=%d, len_out=%d)",
                        len(result), len(_cleaned),
                    )
                logger.info(f"[[OK]] CircuIT AI response received: {len(_cleaned)} characters")
                return _cleaned
            else:
                logger.warning(f"[[WARNING]] CircuIT AI returned invalid response: {result}")
                return "ERROR: llm.empty_response: CircuIT summarization failed - invalid response."

    except FutureTimeoutError:
        logger.error(
            f"⏰ CircuIT AI call timed out after {_circuit_timeout_s} seconds"
        )
        # Round 6 / Phase 3.6: include a stable ``llm.timeout`` error
        # kind so callers can branch on it.  Round 6 / Phase 3.7: the
        # ``_<seconds>s`` suffix now reflects the *aligned* wall-clock
        # budget rather than a stale "60s" literal.
        return (
            f"ERROR: llm.timeout_{_circuit_timeout_s}s: CircuIT summarization timed out. "
            "The AI service may be under heavy load. Please try again later."
        )
    except Exception as e:
        # Round 6 / Phase 3.6: include the real exception kind +
        # message in the ERROR string so downstream logs can correlate
        # the failure without losing the underlying cause.  We cap the
        # exception text to keep the user-facing line tidy.
        _e_msg = str(e)
        if len(_e_msg) > 200:
            _e_msg = _e_msg[:200] + '...'
        logger.error("[[ERROR]] Unexpected error in CircuIT AI call: %s", e)
        return f"ERROR: llm.wrapped: {_e_msg}"


def generate_llm_json_response(
    system_prompt: str,
    briefing_book: str,
    schema: Dict[str, Any],
    *,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Request JSON-only LLM output and parse it with strict key checks.
    Falls back gracefully when the model returns wrapped markdown.

    Round 69 / Build 43: ``model_name`` is keyword-only and optional;
    when supplied it threads through to ``generate_llm_response`` for
    this call only, leaving sibling call sites unaffected.
    """
    schema_keys = sorted((schema or {}).get("properties", {}).keys())
    required_keys = sorted((schema or {}).get("required", []))
    schema_hint = json.dumps(schema or {}, separators=(",", ":"), default=str)
    constrained_prompt = (
        f"{briefing_book}\n\n"
        "Return ONLY a valid JSON object. No markdown, no prose outside JSON.\n"
        f"Schema keys: {', '.join(schema_keys)}\n"
        f"Required keys: {', '.join(required_keys)}\n"
        f"JSON schema: {schema_hint}\n"
    )
    # Round 69 / Build 43: only thread ``model_name`` when explicitly
    # supplied so legacy test patches that mock ``generate_llm_response``
    # with a 2-arg lambda (no ``**kwargs``) still work.  ``None`` falls
    # through to the resolver inside ``generate_llm_response`` anyway.
    if model_name:
        raw = generate_llm_response(system_prompt, constrained_prompt, model_name=model_name)
    else:
        raw = generate_llm_response(system_prompt, constrained_prompt)
    if not raw or str(raw).startswith("ERROR:"):
        return {"ok": False, "error": raw or "ERROR: empty response", "raw": raw}

    # Round 5 / Phase 3.15: in JSON mode, ``finish_reason='length'``
    # is not a recoverable warning -- a length-truncated JSON object
    # may still be syntactically parseable (e.g. the model emitted
    # ``"claims": []`` early before getting cut off) and silently
    # downgrade the answer to "no findings".  ``CircuitChatClient``
    # appends an explicit ``[TRUNCATED: ...finish_reason='length']``
    # marker to length-truncated responses; detect that marker here
    # and fail closed so the caller does not mistake a truncated
    # answer for a complete one.
    if "finish_reason='length'" in str(raw) or "[TRUNCATED:" in str(raw):
        return {
            "ok": False,
            "error": (
                "ERROR: LLM response was length-truncated (finish_reason='length'); "
                "JSON output may be incomplete and is rejected to avoid silent "
                "downgrade. Reduce briefing size or raise model max_tokens."
            ),
            "raw": raw,
        }

    payload = None
    text = str(raw).strip()
    # Round 7 / Phase 5.1: route the JSON salvage path through the
    # shared ``ask_ai_grounded._extract_json_object`` brace-depth
    # walker (introduced in Round 6 / Phase 3.8) instead of the
    # legacy greedy ``re.search(r"\{[\s\S]*\}", text)``.  The greedy
    # regex spans from the *first* ``{`` to the *last* ``}`` even
    # when those belong to two unrelated objects (e.g. a model that
    # prepends a short rationale block before the real JSON), which
    # produced a mangled blob that always failed ``json.loads`` --
    # turning a recoverable parse into a hard failure.  The walker
    # respects strings/escapes and tries successive balanced objects
    # in document order, matching the behaviour of every other
    # JSON-from-LLM path in the codebase.  If the helper cannot be
    # imported (cycle or missing module), we fall back to ``loads``
    # only -- never to the greedy regex, which is unsafe.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        try:
            from ask_ai_grounded import _extract_json_object as _ej
        except Exception:
            _ej = None
        if _ej is not None:
            try:
                parsed = _ej(text)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = None

    if payload is None:
        return {"ok": False, "error": "ERROR: model did not return parseable JSON", "raw": raw}

    missing = [key for key in required_keys if key not in payload]
    if missing:
        return {"ok": False, "error": f"ERROR: JSON missing required keys: {missing}", "raw": raw, "data": payload}

    # Round 7 / Phase 5.3: enforce ``additionalProperties: false`` via
    # a real ``jsonschema`` validator at the boundary instead of
    # relying on the prompt text alone.  Previously the JSON schema
    # was only inlined into the prompt as a hint, which meant the
    # model could (and occasionally did) ship extra top-level keys
    # ("notes", "summary", "metadata") that downstream code silently
    # ignored -- masking prompt-spec drift and letting unmodelled
    # fields slip into the answer.  We:
    #   1. clone the schema so we never mutate the caller's dict;
    #   2. inject ``additionalProperties: false`` only when the
    #      caller did not already set it (preserves explicit opt-out);
    #   3. use ``Draft202012Validator`` and collect *all* errors so
    #      the user sees the full list, not just the first;
    #   4. fail closed with ``ok: False`` and the full error list so
    #      callers can branch on schema mismatches without parsing
    #      free-text error strings;
    #   5. silently no-op if ``jsonschema`` is unavailable in the
    #      environment (rather than crashing the whole pipeline).
    if isinstance(schema, dict) and schema:
        try:
            import copy as _copy_schema
            from jsonschema import Draft202012Validator  # type: ignore
            _enforced_schema = _copy_schema.deepcopy(schema)
            if (
                _enforced_schema.get("type", "object") == "object"
                and "additionalProperties" not in _enforced_schema
            ):
                _enforced_schema["additionalProperties"] = False
            _validator = Draft202012Validator(_enforced_schema)
            _schema_errors = sorted(
                _validator.iter_errors(payload),
                key=lambda e: list(e.absolute_path),
            )
            if _schema_errors:
                _first = _schema_errors[0]
                _err_summary = (
                    f"jsonschema: {len(_schema_errors)} violation(s); first at "
                    f"path {list(_first.absolute_path) or '<root>'}: {_first.message}"
                )
                return {
                    "ok": False,
                    "error": f"ERROR: schema validation failed: {_err_summary}",
                    "raw": raw,
                    "data": payload,
                    "schema_errors": [
                        {
                            "path": list(e.absolute_path),
                            "validator": e.validator,
                            "message": e.message,
                        }
                        for e in _schema_errors[:20]
                    ],
                }
        except ImportError:
            # ``jsonschema`` is an optional dependency in some deploy
            # environments.  Log once and continue with the legacy
            # claim/string-list checks below; do not silently approve
            # arbitrary payloads.
            # Round 10 / Phase 6.4: in *production* (FLASK_ENV=production
            # / ADOPTIQ_ENV=production), missing ``jsonschema`` is a
            # hard error rather than a soft warning. The Round 7
            # /Phase 5.3 hardening exists specifically because legacy
            # validation alone is not strong enough; silently
            # downgrading in production hides the regression that the
            # validator was meant to catch. Dev/test still soft-fall
            # back so local hacking on a fresh checkout works.
            try:
                import os as _os_p64_jsonschema
                _env_name = (
                    _os_p64_jsonschema.environ.get("ADOPTIQ_ENV")
                    or _os_p64_jsonschema.environ.get("FLASK_ENV")
                    or ""
                ).strip().lower()
            except Exception:
                _env_name = ""
            if _env_name == "production":
                return {
                    "ok": False,
                    "error": (
                        "ERROR: jsonschema is not installed but ADOPTIQ_ENV/FLASK_ENV is "
                        "production; refusing to validate LLM JSON with the legacy "
                        "claim-shape check alone (Round 10 / Phase 6.4). Install the "
                        "'jsonschema' package."
                    ),
                    "raw": raw,
                    "data": payload,
                }
            logger.warning(
                "[[WARNING]] Round 7 / Phase 5.3: jsonschema is not installed; "
                "additionalProperties:false enforcement is skipped.  Install "
                "the 'jsonschema' package to harden LLM JSON validation."
            )
        except Exception as _schema_exc:
            logger.warning(
                "[[WARNING]] Round 7 / Phase 5.3: jsonschema validator raised "
                "%s: %s; continuing with legacy validation.",
                type(_schema_exc).__name__,
                _schema_exc,
            )

    # Round 4 / Phase 6.6: validate per-claim shape and FAIL CLOSED on
    # malformed entries.  Many of our LLM JSON responses include a
    # ``claims`` (or ``items``) array where each entry is supposed to
    # be ``{"text": str, "citations": list[str]}``.  Previously we
    # accepted whatever the model returned, which let it ship strings
    # in place of objects, missing ``citations`` arrays, or
    # ``citations`` that were dicts/None.  Downstream code then
    # silently dropped citations and the answer became ungrounded.
    # We now scan any list-of-dict field whose first element looks
    # claim-shaped and reject the whole payload if any entry is
    # malformed.  The legacy "{ok:true,data:...}" envelope is
    # preserved so existing callers that don't ship claim arrays are
    # unaffected.
    def _looks_like_claim_obj(obj: Any) -> bool:
        return (
            isinstance(obj, dict)
            and ("text" in obj or "claim" in obj or "statement" in obj)
        )

    def _validate_claim(obj: Any, *, require_citations: bool = False) -> Optional[str]:
        if not isinstance(obj, dict):
            return f"claim is {type(obj).__name__}, expected object"
        _text = obj.get("text") or obj.get("claim") or obj.get("statement")
        if not isinstance(_text, str) or not _text.strip():
            return "claim missing non-empty 'text' (or 'claim'/'statement')"
        # Round 5 / Phase 3.6: when this object is *under a claim key*
        # (claims/items/findings) the prompt contract requires every
        # claim to ship with at least one verifiable source ID in
        # ``citations``.  Otherwise the model can output unverifiable
        # narrative claims that still pass the validator -- defeating
        # the grounding hardening.  Plain claim-shaped fields outside
        # the claim-key set keep the legacy (looser) behaviour.
        if require_citations:
            _cits = obj.get("citations")
            if not isinstance(_cits, list) or not _cits:
                return "claim under claim-key must include non-empty 'citations'"
            if not any(isinstance(_c, str) and _c.strip() for _c in _cits):
                return "'citations' must contain at least one non-empty string"
        if "citations" in obj:
            _cits = obj.get("citations")
            if not isinstance(_cits, list):
                return f"'citations' is {type(_cits).__name__}, expected list"
            for _c in _cits:
                if not isinstance(_c, str) or not _c.strip():
                    return "'citations' entries must be non-empty strings"
        return None

    # Keys whose arrays are *expected* to be claim-shaped objects.
    # If any of these keys exists and contains a non-dict entry, fail
    # closed. This catches the model returning ``"claims": ["bare
    # string"]`` (which would otherwise look like "no findings").
    #
    # Round 5 / Phase 3.1: drop ``actions`` and ``unknowns`` from this
    # set.  These two fields are explicitly documented in the prompt
    # contract as ``list[str]`` (recommended next steps and unanswered
    # sub-questions, respectively).  Forcing them to be claim-shaped
    # dicts caused the validator to reject correct payloads like
    # ``"actions": ["Schedule QBR"]`` and turn a successful run into a
    # ``ok: False`` error -- the *opposite* of the intended hardening.
    _CLAIM_KEYS = {"claims", "items", "findings"}
    _STRING_LIST_KEYS = {"actions", "unknowns"}
    # Validate string-list keys early: every entry must be a non-empty
    # string.  This still catches the model returning ``"actions":
    # [{"do": "x"}]`` (object instead of string) which is also wrong
    # per the prompt contract.
    try:
        for _slk in _STRING_LIST_KEYS:
            _slv = payload.get(_slk)
            if not isinstance(_slv, list):
                continue
            for _slidx, _slentry in enumerate(_slv):
                if not isinstance(_slentry, str) or not _slentry.strip():
                    return {
                        "ok": False,
                        "error": (
                            f"ERROR: malformed entry at {_slk}[{_slidx}]: "
                            f"expected non-empty string, got {type(_slentry).__name__}"
                        ),
                        "raw": raw,
                        "data": payload,
                    }
    except Exception:
        pass  # noqa: PIE790  # validation is best-effort; fall through to claim-key check
    try:
        for _key, _val in list(payload.items()):
            if not isinstance(_val, list) or not _val:
                continue
            _is_claim_key = str(_key).lower() in _CLAIM_KEYS
            _first_looks_claim = _looks_like_claim_obj(_val[0])
            # Validate when the key name is in the claim-key set OR
            # the first element already looks claim-shaped (legacy
            # behaviour).
            if not (_is_claim_key or _first_looks_claim):
                continue
            for _idx, _entry in enumerate(_val):
                # For claim-keys: every entry must be a dict.
                if _is_claim_key and not isinstance(_entry, dict):
                    return {
                        "ok": False,
                        "error": (
                            f"ERROR: malformed claim at {_key}[{_idx}]: "
                            f"entry is {type(_entry).__name__}, expected object"
                        ),
                        "raw": raw,
                        "data": payload,
                    }
                # For non-claim-keys whose first entry was claim-shaped,
                # only validate dict entries (ignore mixed shapes).
                if not isinstance(_entry, dict):
                    continue
                _problem = _validate_claim(_entry, require_citations=_is_claim_key)
                if _problem:
                    return {
                        "ok": False,
                        "error": (
                            f"ERROR: malformed claim at {_key}[{_idx}]: {_problem}"
                        ),
                        "raw": raw,
                        "data": payload,
                    }
    except Exception as _claim_err:
        # Defensive: if the validator itself crashes, fail closed.
        logger.warning("Claim-shape validation crashed: %s", _claim_err)
        return {
            "ok": False,
            "error": f"ERROR: claim-shape validation failed: {_claim_err}",
            "raw": raw,
            "data": payload,
        }

    return {"ok": True, "data": payload, "raw": raw}

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
            # Round 4 / Phase 4.6: stamp the widening on ``use.attrs``
            # so downstream code (compact / comprehensive analysis,
            # validators, prompts) can promote a ``partial_data_warnings``
            # entry like "AB tech filter '<tech>' matched only X/Y;
            # using unfiltered set" instead of silently surfacing the
            # broader population as if it were the requested scope.
            try:
                use.attrs['tech_filter_widened'] = True
                use.attrs['tech_filter_requested'] = str(tech)
                use.attrs['tech_filter_matched'] = int(len(filtered))
                use.attrs['tech_filter_total'] = int(before_tech_filter)
                use.attrs['tech_filter_warning'] = (
                    f"AB tech filter '{tech}' matched only {len(filtered)} of {before_tech_filter} "
                    f"adoption barriers; widened to unfiltered set."
                )
            except Exception:
                pass
        else:
            use = filtered
            logger.debug(f"AB filter: After technology filter: {len(use)} records (removed {before_tech_filter - len(use)})")
    else:
        logger.debug("AB filter: Technology='All', skipping technology filter")

    logger.debug(f"AB filter: Final result: {len(use)} adoption barriers")
    return use

def _apply_scope_filter_csone(
    df: pd.DataFrame,
    tech: str,
    days: int,
    sub_ids: List[str],
    team_customer_names: List[str],
    include_all_cases: bool = True,
) -> pd.DataFrame:
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
    # Product decision: CSOne should show all TAC cases regardless of open/closed age.
    # Keep optional support for strict date windows via include_all_cases=False.
    if include_all_cases:
        logger.debug("CSOne filter: include_all_cases=True, skipping date filter")
    else:
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

def _apply_scope_filter_csone_inclusive(csone_df, technology, days, include_all_cases: bool = True):
    """Apply inclusive filtering to CSOne data for executive analysis - only technology and date filters"""
    if csone_df is None or csone_df.empty:
        return pd.DataFrame() if csone_df is None else csone_df

    logger.debug(f"CSOne inclusive filter: Starting with {len(csone_df)} cases")
    logger.debug(f"CSOne inclusive filter: Technology='{technology}', Days={days}")

    filtered_df = csone_df.copy()

    # Apply date filter only when strict mode is requested.
    if not include_all_cases and 'Date/Time Opened' in filtered_df.columns:
        logger.debug("CSOne inclusive filter: Applying date filter using column 'Date/Time Opened'")
        # Round 12 / Phase 2.2: ``datetime.now() - timedelta(days=days)``
        # used the host's local clock, then forcibly stripped tz on
        # both sides for the comparison.  On a Tokyo host that
        # silently shifts the cutoff by ~9h vs UTC and on DST
        # boundaries can include / exclude a full day's CSOne cases.
        # Anchor with ``datetime.now(timezone.utc)`` and parse the
        # column as tz-aware UTC so the comparison is unambiguous
        # regardless of host timezone (mirrors Round 11 / Phase 2.x
        # tz-aware filter pattern).
        cutoff_date = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days)

        # Convert date column to datetime if needed
        try:
            filtered_df['Date/Time Opened'] = pd.to_datetime(
                filtered_df['Date/Time Opened'], errors='coerce', utc=True,
            )
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

def _filter_csconsole_data_by_technology(
    df: pd.DataFrame,
    technology: str,
    customer_names: List[str] = None,
    account_ids: List[str] = None,
) -> pd.DataFrame:
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

    def _normalize_account_id_token(value: Any) -> str:
        token = str(value or "").strip().upper()
        if not token or token in {"NONE", "NAN", "NULL"}:
            return ""
        return token

    def _expand_account_id_tokens(values: List[Any]) -> tuple[set[str], set[str]]:
        exact: set[str] = set()
        sf15: set[str] = set()
        for value in values or []:
            token = _normalize_account_id_token(value)
            if not token:
                continue
            exact.add(token)
            if len(token) >= 15:
                sf15.add(token[:15])
        return exact, sf15

    scoped_exact, scoped_sf15 = _expand_account_id_tokens(account_ids or [])

    def _account_scope_mask(frame: pd.DataFrame) -> pd.Series | None:
        if frame is None or frame.empty:
            return None
        if not scoped_exact:
            return None
        account_col = next((c for c in ACCOUNT_COLUMN_CANDIDATES if c in frame.columns), None)
        if not account_col:
            return None
        normalized = (
            frame[account_col]
            .fillna("")
            .astype(str)
            .apply(_normalize_account_id_token)
        )
        mask = normalized.isin(scoped_exact)
        if scoped_sf15:
            mask = mask | normalized.str[:15].isin(scoped_sf15)
        return mask

    # Filter by customer names if provided (ensures only team's customers are included)
    if customer_names or scoped_exact:
        before_count = len(filtered_df)
        normalized_targets = {
            normalize_customer_name(name)
            for name in (customer_names or [])
            if normalize_customer_name(name) != "Unknown"
        }
        customer_filter_cols = (
            'customer_name',
            'BU_NAME',
            'CUSTOMER_NAME',
            'Customer Name',
            'CUSTOMER_BU_NAME__C',
            'RELATED_CUSTOMER__C',
        )
        candidate_col = next((c for c in customer_filter_cols if c in filtered_df.columns), None)
        account_mask = _account_scope_mask(filtered_df)
        customer_mask = None
        if candidate_col and normalized_targets:
            customer_series = filtered_df[candidate_col].fillna('').astype(str).apply(normalize_customer_name)
            customer_mask = customer_series.isin(normalized_targets)
        if customer_mask is not None and account_mask is not None:
            filtered_df = filtered_df[customer_mask | account_mask]
        elif customer_mask is not None:
            filtered_df = filtered_df[customer_mask]
        elif account_mask is not None:
            filtered_df = filtered_df[account_mask]

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
                            except Exception as _filter_err:
                                logger.debug(f"Column filter '{col}' skipped: {_filter_err}")
                    if not mask.any():
                        fallback_mask = _account_scope_mask(filtered_df)
                        if fallback_mask is not None and fallback_mask.any():
                            logger.warning(
                                "[[FILTER]] CSConsole filter: no technology text matches; using account-scope fallback for %d rows",
                                int(fallback_mask.sum()),
                            )
                            mask = fallback_mask

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
        except (TypeError, ValueError):
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

    customer_lookup = build_customer_lookup(dsm_df)
    use["customer_name"] = use.apply(lambda row: normalize_customer_name(row.get("customer_name")), axis=1)
    use["customer_name"] = use.apply(
        lambda row: normalize_customer_name(
            customer_lookup.get("account_to_customer", {}).get(str(row.get("ACCOUNT_ID_C", "")).strip(), row.get("customer_name"))
        ),
        axis=1,
    )
    use["customer_name_norm"] = use["customer_name"].apply(normalize_customer_name)
    use["ab_category_final"] = use.get("AB_CATEGORY_C").apply(_normalize_category) if "AB_CATEGORY_C" in use.columns else "Uncategorized"
    _tech = lambda c: use[c].fillna("").astype(str) if c in use.columns else pd.Series([""] * len(use), index=use.index)
    tech_txt = (
        _tech("SUB_TECHNOLOGY_C")
        + " "
        + _tech("TECHNOLOGY_C")
        + " "
        + _tech("CSS_PRE_UNLINK_TECHNOLOGY_NAME_C")
        + " "
        + _tech("PRODUCT_NAME_C")
        + " "
        + _tech("PRODUCT_C")
        + " "
        + use["title"].fillna("").astype(str)
        + " "
        + use["description"].fillna("").astype(str)
    )
    use["sub_technology"] = tech_txt.apply(_normalize_subtech) if hasattr(tech_txt, "apply") else "Other/Unknown"
    use["severity_norm"] = use["SEVERITY_C"].apply(normalize_severity_label)
    use["status_norm"] = use["AB_STATUS_C"].apply(normalize_status_label)
    date_col = next((c for c in ["OPEN_DATE_C", "CREATED_DATE", "CREATED_DATE_C", "CREATEDDATE"] if c in use.columns), None)
    close_col = next((c for c in ["CLOSED_DATE_C", "CLOSED_DATE", "RESOLVED_DATE", "LASTMODIFIEDDATE"] if c in use.columns), None)
    use["open_date"] = parse_datetime_series(use[date_col]) if date_col else pd.NaT
    use["closed_date"] = parse_datetime_series(use[close_col]) if close_col else pd.NaT
    # Round 6 / Phase 4.14: tz-aware UTC reference (see same change
    # in renewal _normalize_cases_df).
    _now_utc = pd.Timestamp(datetime.now(timezone.utc))
    _open_dt = use["open_date"]
    try:
        if getattr(_open_dt.dt, 'tz', None) is None:
            _open_dt = _open_dt.dt.tz_localize('UTC')
        else:
            _open_dt = _open_dt.dt.tz_convert('UTC')
    except Exception:
        pass
    use["open_age_days"] = (
        (_now_utc - _open_dt).dt.days.where(use["status_norm"].eq("Open"), other=pd.NA)
    )
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
            # Round 6 / Phase 4.5: dedupe team_subs by SUBSCRIPTION_ID
            # *before* merging and use ``validate='m:1'`` so a duplicated
            # subscription row in the source can never silently fan out
            # CSOne case rows into multiple copies (which would inflate
            # every downstream count: barriers, escalations, defects).
            # If a true duplicate exists we keep the first observed row
            # (deterministic for the same input) and emit a warning so
            # the upstream fetch can be inspected.
            _team_subs_view = team_subs_df[['SUBSCRIPTION_ID', 'BU_NAME']].copy()
            _team_subs_view['SUBSCRIPTION_ID'] = _team_subs_view['SUBSCRIPTION_ID'].astype(str)
            _pre_dedupe = len(_team_subs_view)
            _team_subs_view = _team_subs_view.drop_duplicates(
                subset=['SUBSCRIPTION_ID'], keep='first'
            ).reset_index(drop=True)
            _post_dedupe = len(_team_subs_view)
            if _pre_dedupe != _post_dedupe:
                logger.warning(
                    "CSOne prepare: team_subs had %d duplicate SUBSCRIPTION_ID row(s); "
                    "deduped to %d before merge to prevent count inflation.",
                    _pre_dedupe - _post_dedupe, _post_dedupe,
                )

            logger.debug(
                f"CSOne prepare: Merging with team subscription data "
                f"({_post_dedupe} unique team subscriptions)"
            )
            try:
                use = pd.merge(
                    use,
                    _team_subs_view,
                    left_on=sub_col,
                    right_on='SUBSCRIPTION_ID',
                    how='left',
                    validate='m:1',
                )
            except Exception as _merge_err:
                logger.error(
                    "CSOne prepare: m:1 merge validation failed (%s); "
                    "falling back to plain left-merge but counts may be inflated.",
                    _merge_err,
                )
                use = pd.merge(
                    use,
                    _team_subs_view,
                    left_on=sub_col,
                    right_on='SUBSCRIPTION_ID',
                    how='left',
                )

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
    use = add_case_lifecycle_fields(use, customer_lookup=build_customer_lookup(team_subs_df))
    # Keep compatibility columns used throughout report generation.
    if "case_priority_norm" in use.columns and "Severity" not in use.columns:
        use["Severity"] = use["case_priority_norm"]
    if "case_status_norm" in use.columns and "Case Status" not in use.columns:
        use["Case Status"] = use["case_status_norm"]
    if "open_date" in use.columns and "Date/Time Opened" not in use.columns:
        use["Date/Time Opened"] = use["open_date"]
    logger.debug(f"CSOne prepare: Final result: {len(use)} cases with customer names")
    return use

def _counts_by(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns: return pd.DataFrame()
    # Round 12 / Phase 11.4: previously this sort ran with the default
    # quicksort algorithm and *no* secondary key, so two groups with
    # identical ``count`` could swap positions on repeat runs and any
    # downstream ``head(N)`` slice would render a different "Top N"
    # set.  Add a stable kind and a tie-break on the grouped column so
    # equal counts always resolve in alphabetical order.
    return (
        df.groupby(col)
        .size()
        .reset_index(name="count")
        .sort_values(
            ["count", col],
            ascending=[False, True],
            kind="stable",
        )
    )

def _portfolio_grade(total_ab: int, esc_rate: float, chronic_rate: float) -> str:
    if total_ab >= 100 or esc_rate >= 30 or chronic_rate >= 30: return "D"
    if total_ab >= 50 or esc_rate >= 20 or chronic_rate >= 20: return "C"
    if total_ab < 10: return "B"
    return "B"

def _calc_rates(csone_df: pd.DataFrame):
    """Return (escalation_rate_pct, chronic_rate_pct) for a CSOne frame.

    Round 3 hardening: escalation rate is now derived from canonical
    ``case_priority_norm`` (P1+P2) instead of free-text title/description
    regex which produced false positives for any case whose subject
    contained "urgent" / "executive" / "escalated" without actually being
    an escalated severity.
    """
    if csone_df is None or csone_df.empty:
        return 0.0, 0.0
    try:
        import canonical_metrics as _cm
        escal = int(_cm.count_escalated(csone_df))
    except Exception:
        escal = 0
    chronic = 0
    total = len(csone_df)
    return (
        # Round 12 / Phase 11.1: route escalation / chronic percentages
        # through canonical helper for half-away-from-zero rounding.
        _r12_round_percent(100 * escal / total, 1) if total else 0.0,
        _r12_round_percent(100 * chronic / total, 1) if total else 0.0,
    )

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

        # Round 6 / Phase 4.13: validate the team-roster merge to catch
        # roster data quality issues early (a CSSM email mapped to two
        # roster rows would otherwise silently double-count every
        # subscription owned by that CSSM).  Falls back to a plain
        # left-merge if validation fails so we still produce a report.
        try:
            team_subs_df = team_subs_df.merge(
                team_roster_df,
                left_on="CSSM_EMAIL",
                right_on="cssm_email",
                how="left",
                validate="m:1",
            )
        except Exception as _merge_err:
            logger.warning(
                "Leader path: team roster m:1 merge validation failed (%s); "
                "falling back to plain left-merge but counts may be inflated.",
                _merge_err,
            )
            team_subs_df = team_subs_df.merge(
                team_roster_df,
                left_on="CSSM_EMAIL",
                right_on="cssm_email",
                how="left",
            )
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
            # Round 6 / Phase 4.13: pre-deduplicate on ACCOUNT_ID_C
            # and validate='m:1' so duplicate (account_id, BU_NAME)
            # rows in the team subscription view do not double-count
            # adoption barriers per account.
            _ab_view = (
                team_subs_df[["ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"]]
                .dropna(subset=["ACCOUNT_ID_C"])
                .drop_duplicates(subset=["ACCOUNT_ID_C"])
            )
            try:
                ab_raw = ab_raw.merge(
                    _ab_view,
                    on="ACCOUNT_ID_C",
                    how="left",
                    validate="m:1",
                )
            except Exception as _merge_err:
                logger.warning(
                    "Leader path: adoption-barrier m:1 merge validation failed "
                    "(%s); falling back to plain left-merge.",
                    _merge_err,
                )
                ab_raw = ab_raw.merge(
                    _ab_view,
                    on="ACCOUNT_ID_C",
                    how="left",
                )

        # Fetch CSConsole data for comprehensive analysis
        print("Fetching CSConsole data (Action Plans, Customer Pulse, Success Priorities)...")
        csconsole_action_plans = fetch_csconsole_action_plans(ctx, account_ids, days)
        csconsole_customer_pulse = fetch_csconsole_customer_pulse(ctx, account_ids, days)
        csconsole_success_priorities = fetch_csconsole_success_priorities(ctx, team_customer_names, days)
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
        # Round 14 / Phase 4.6: previously this stored the integrity
        # check result in ``reason`` and discarded it (ruff F841), which
        # silently neutered the gate on the CLI report path -- the
        # Flask flow in ``app_simple.py`` correctly inspects ``reason``
        # and short-circuits on non-empty.  Log the reason at WARNING
        # here so operators running the CLI report still see when the
        # integrity check fired, without changing the CLI's "always
        # produce a report" contract.
        _integrity_reason = _integrity_checks(ab_norm, csone_df)
        if _integrity_reason:
            logger.warning(
                "[[INTEGRITY]] Integrity check fired on CLI report path: %s",
                _integrity_reason,
            )
        # Round 13 / Phase 2.10: ``time.strftime`` reads the worker's
        # local zone, so two operators running ``adoptiq_backend.py``
        # at the same instant in different zones produced different
        # ``AdoptIQ_<...>_<ts>`` filenames -- the on-disk artifact
        # name no longer matched the UTC-anchored "Generated:" header
        # inside the Word/Excel files.  Stamp the filename with UTC.
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        tag = f"{manager.replace(' ','_')}_{tech.replace(' ','_').replace('&','and')}_{days}d_{ts}"
        base = str(out_dir / f"AdoptIQ_{tag}")

        # Create Word document
        doc = Document()

        # Fetch external intelligence
        print("Fetching external intelligence (Help Center bugs & Status incidents)...")
        ext_bugs = fetch_help_webex_bugs()
        # Round 2 / Phase 1.7: thread report window if available in scope
        try:
            _inc_days = int(days)  # noqa: F821
        except Exception:
            _inc_days = 365
        ext_incidents = fetch_status_incidents(days_back=_inc_days)
        print(f"Found {len(ext_bugs)} Help Center bug references and {len(ext_incidents)} status incidents.")

        # Generate portfolio summary.
        #
        # Round 5 / Phase 5.12: previously the AB / CSOne value
        # counts were grouped on the raw ``customer_name`` column,
        # which meant "Acme, Inc.", "Acme Inc", and "ACME inc."
        # were three separate rows in the engagement frame and the
        # subsequent outer-merge produced double-counted /
        # mis-merged buckets.  Normalize via
        # ``normalize_customer_name`` so the merge keys are
        # canonical and AB + CSOne for the same customer line up
        # on a single row.
        if not ab_norm.empty:
            ab_counts = (
                ab_norm['customer_name']
                .fillna('')
                .astype(str)
                .apply(normalize_customer_name)
                .value_counts()
                .reset_index()
            )
            ab_counts.columns = ['customer_name', 'ab_count']
        else:
            ab_counts = pd.DataFrame(columns=['customer_name', 'ab_count'])

        if not csone_df.empty:
            csone_counts = (
                csone_df['customer_name']
                .fillna('')
                .astype(str)
                .apply(normalize_customer_name)
                .value_counts()
                .reset_index()
            )
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
        # Round 25 / Phase B: pin canonical totals into the prompt
        # body so the LLM cannot free-style numbers that disagree
        # with the canonical pipeline.  The CLI path doesn't pre-
        # build a portfolio_metrics dict (the comprehensive Flask
        # path does that); compute the headline totals inline via
        # the canonical helpers so the prompt substitutions are
        # always populated with verifiable values.
        try:
            import canonical_metrics as _r25b_cm
            _r25b_pulse_for_count = locals().get("filtered_customer_pulse")
            _r25b_total_customers = int(_r25b_cm.count_customers(
                ab_df=ab_norm, csone_df=csone_df,
                pulse_df=_r25b_pulse_for_count,
            ) or 0)
            _r25b_total_barriers = int(_r25b_cm.count_total_barriers(ab_norm) or 0)
            _r25b_tac_cases = int(_r25b_cm.count_total_tac(csone_df) or 0)
            _r25b_p1 = int(_r25b_cm.count_p1(csone_df) or 0)
            _r25b_p2 = int(_r25b_cm.count_p2(csone_df) or 0)
            _r25b_bems = int(_r25b_cm.count_bems(csone_df) or 0)
        except Exception as _r25b_err:
            logger.warning(
                "Round 25 / Phase B: canonical totals fallback to 0 (CLI path): %s",
                _r25b_err,
            )
            _r25b_total_customers = _r25b_total_barriers = _r25b_tac_cases = 0
            _r25b_p1 = _r25b_p2 = _r25b_bems = 0
        portfolio_prompt = PROMPT_PORTFOLIO_TEMPLATE.format(
            MANAGER=manager,
            TECHNOLOGY=tech,
            TOTAL_CUSTOMERS=_r25b_total_customers,
            TOTAL_BARRIERS=_r25b_total_barriers,
            TAC_CASES=_r25b_tac_cases,
            P1_CASES=_r25b_p1,
            P2_CASES=_r25b_p2,
            BEMS_ESCALATIONS=_r25b_bems,
        )
        # Round 69 / Build 43: thread the operator-selected report model
        # into the CLI portfolio-summary path.  Lazy import so a missing
        # ``model_resolver`` cannot break the CLI path.
        try:
            from model_resolver import get_active_report_model as _r69_get_report_model
            _r69_report_model = _r69_get_report_model()
        except Exception:  # noqa: BLE001
            _r69_report_model = None
        portfolio_summary = generate_llm_response(portfolio_prompt, portfolio_briefing, model_name=_r69_report_model)
        # Round 25 / Phase B: post-render numeric drift validator.  See
        # the matching block in ``app_simple.py`` for the full rationale.
        # The validator scans the LLM-rendered ``portfolio_summary``
        # before it lands in the Word doc and blocks the build on any
        # drift between the narrated numbers and the canonical totals
        # passed to the prompt above.
        if portfolio_summary and not portfolio_summary.startswith("ERROR:"):
            try:
                from report_consistency import (
                    validate_word_numeric_drift as _r25b_validator,
                    validate_word_risk_band_claims as _r25c_validator,
                )
                _r25b_drift_strict = str(
                    os.getenv("ADOPTIQ_NONSTRICT_R25B", "0")
                ).strip().lower() not in {"1", "true", "yes", "on"}
                _r25b_drift_result = _r25b_validator(
                    portfolio_summary,
                    canonical_totals={
                        "total_customers": _r25b_total_customers,
                        "total_barriers": _r25b_total_barriers,
                        "total_cases": _r25b_tac_cases,
                        "bems_count": _r25b_bems,
                    },
                    raise_on_drift=_r25b_drift_strict,
                )
                if _r25b_drift_result.get("warnings"):
                    logger.warning(
                        "[R25B] Portfolio numeric drift validator warnings (CLI path): %s",
                        _r25b_drift_result["warnings"],
                    )
                # Round 25 / Phase C: CLI path doesn't compute a
                # portfolio_metrics dict (the comprehensive flow does);
                # skip the risk-band validator unless future work wires
                # a canonical high_risk_customers value through here.
            except ValueError as _r25b_drift_err:
                logger.error(
                    "[R25B/R25C] Portfolio drift detected (CLI path); blocking report build: %s",
                    _r25b_drift_err,
                )
                raise
            except Exception as _r25b_other_err:
                logger.warning(
                    "[R25B/R25C] Portfolio drift validator unavailable (CLI path) (%s); proceeding without strict check.",
                    _r25b_other_err,
                )
        # Round 71 / Phase 3 (#16): apply the R16/R27 grounding gate
        # to the CLI portfolio_summary append path.  Pre-R71 the CLI
        # main() narrative bypassed validate_narrative entirely
        # (the gate was only wired into the Flask report writers in
        # app_simple.py).  Operators using the CLI to regenerate
        # reports headlessly (e.g. nightly cron, build smoke
        # harness) would get raw LLM text in the docx with no
        # grounding check -- including any HTML injection or
        # invented numbers the upstream validator would have caught.
        # Apply the same fail-CLOSED contract: substitute the
        # canonical placeholder on rejection OR validator exception.
        _r71_safe_portfolio_summary = portfolio_summary
        if isinstance(portfolio_summary, str) and portfolio_summary.strip() and not portfolio_summary.startswith("ERROR:"):
            try:
                import ai_narrative_validator as _r71_anv_cli
                _r71_cli_anv_result = _r71_anv_cli.validate_narrative(
                    portfolio_summary,
                    portfolio_briefing,
                )
                if not _r71_cli_anv_result.is_valid:
                    logger.warning(
                        "Round 71 / Phase 3 (#16): CLI portfolio narrative "
                        "failed grounding validation; substituting "
                        "placeholder. failures=%s",
                        list(_r71_cli_anv_result.failures),
                    )
                    _r71_safe_portfolio_summary = _r71_anv_cli.GROUNDING_FAILURE_PLACEHOLDER
            except Exception as _r71_cli_anv_err:
                logger.warning(
                    "Round 71 / Phase 3 (#16): CLI portfolio narrative "
                    "validator raised unexpectedly (%s); substituting "
                    "placeholder for safety.",
                    _r71_cli_anv_err,
                )
                try:
                    from ai_narrative_validator import GROUNDING_FAILURE_PLACEHOLDER as _r71_cli_placeholder
                    _r71_safe_portfolio_summary = _r71_cli_placeholder
                except Exception:
                    _r71_safe_portfolio_summary = (
                        "AI narrative withheld (grounding validator "
                        "unavailable). The data tabs in this report "
                        "remain authoritative."
                    )
        append_to_word_report(doc, _r71_safe_portfolio_summary)

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
            # Round 10 / Phase 9.2: ``ab_norm`` / ``csone_df`` already
            # carry their ``customer_name`` columns through
            # ``normalize_customer_name`` (Round 6 / Phase 3.x), but
            # the CSConsole branches above push raw ``BU_NAME`` /
            # ``CUSTOMER_BU_NAME__C`` strings into ``customer_series``.
            # Strict equality between a normalized AB key and an
            # un-normalized BU_NAME string then silently drops the
            # deep-dive section for any customer whose names differ
            # by trailing whitespace, casing, or LLC/Inc suffix.
            # Normalize via ``data_normalization.normalize_customer_name``
            # so the iterator key matches the ``customer_name``
            # column in every per-customer ``.copy()`` filter below.
            try:
                from data_normalization import normalize_customer_name as _norm_cust
            except Exception:
                _norm_cust = lambda x: x  # noqa: E731
            _raw_customers = pd.concat(customer_series).dropna().tolist()
            _seen_norm = set()
            all_customers = []
            for _raw in _raw_customers:
                try:
                    _key = _norm_cust(_raw)
                except Exception:
                    _key = _raw
                if _key in _seen_norm:
                    continue
                _seen_norm.add(_key)
                all_customers.append(_key)

        print(f"\nFound {len(all_customers)} customers with activity. Generating deep dives...")

        # Round 10 / Phase 9.2: also normalize the per-frame customer
        # columns ONCE so each per-customer filter is a like-for-like
        # equality check on the normalized form.  This avoids
        # repeating ``.apply(_norm_cust)`` inside every loop iteration
        # and prevents the same whitespace/casing drift from
        # silently losing AB/CSOne rows.
        try:
            from data_normalization import normalize_customer_name as _norm_cust
        except Exception:
            _norm_cust = lambda x: x  # noqa: E731
        if ab_norm is not None and 'customer_name' in getattr(ab_norm, 'columns', []):
            try:
                ab_norm = ab_norm.assign(_r10_cust_key=ab_norm['customer_name'].apply(_norm_cust))
            except Exception:
                pass
        if csone_df is not None and 'customer_name' in getattr(csone_df, 'columns', []):
            try:
                csone_df = csone_df.assign(_r10_cust_key=csone_df['customer_name'].apply(_norm_cust))
            except Exception:
                pass
        try:
            if not filtered_action_plans.empty and 'BU_NAME' in filtered_action_plans.columns:
                filtered_action_plans = filtered_action_plans.assign(
                    _r10_cust_key=filtered_action_plans['BU_NAME'].apply(_norm_cust)
                )
            if not filtered_customer_pulse.empty and 'BU_NAME' in filtered_customer_pulse.columns:
                filtered_customer_pulse = filtered_customer_pulse.assign(
                    _r10_cust_key=filtered_customer_pulse['BU_NAME'].apply(_norm_cust)
                )
            if not filtered_success_priorities.empty and 'CUSTOMER_BU_NAME__C' in filtered_success_priorities.columns:
                filtered_success_priorities = filtered_success_priorities.assign(
                    _r10_cust_key=filtered_success_priorities['CUSTOMER_BU_NAME__C'].apply(_norm_cust)
                )
            if not filtered_adoption_barriers.empty and 'BU_NAME' in filtered_adoption_barriers.columns:
                filtered_adoption_barriers = filtered_adoption_barriers.assign(
                    _r10_cust_key=filtered_adoption_barriers['BU_NAME'].apply(_norm_cust)
                )
        except Exception:
            pass

        for i, customer_name in enumerate(all_customers, 1):
            print(f"  ({i}/{len(all_customers)}) Generating StoryBoard for: {customer_name}...")
            # Round 10 / Phase 9.2: filter on the normalized key column
            # so a normalized iterator key cannot miss its own rows.
            cust_ab = ab_norm[ab_norm['_r10_cust_key'] == customer_name].copy() if ab_norm is not None and '_r10_cust_key' in getattr(ab_norm, 'columns', []) else pd.DataFrame()
            cust_csone = csone_df[csone_df['_r10_cust_key'] == customer_name].copy() if csone_df is not None and '_r10_cust_key' in getattr(csone_df, 'columns', []) else pd.DataFrame()

            cust_action_plans = filtered_action_plans[filtered_action_plans['_r10_cust_key'] == customer_name].copy() if not filtered_action_plans.empty and '_r10_cust_key' in filtered_action_plans.columns else pd.DataFrame()
            cust_customer_pulse = filtered_customer_pulse[filtered_customer_pulse['_r10_cust_key'] == customer_name].copy() if not filtered_customer_pulse.empty and '_r10_cust_key' in filtered_customer_pulse.columns else pd.DataFrame()
            cust_success_priorities = filtered_success_priorities[filtered_success_priorities['_r10_cust_key'] == customer_name].copy() if not filtered_success_priorities.empty and '_r10_cust_key' in filtered_success_priorities.columns else pd.DataFrame()
            cust_csconsole_adoption_barriers = filtered_adoption_barriers[filtered_adoption_barriers['_r10_cust_key'] == customer_name].copy() if not filtered_adoption_barriers.empty and '_r10_cust_key' in filtered_adoption_barriers.columns else pd.DataFrame()

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
            # Round 5 / Phase 3.2: pass TECHNOLOGY and MANAGER as well so
            # the prompt template's ``{TECHNOLOGY}`` / ``{MANAGER}``
            # placeholders are filled.  Previously only CUSTOMER_NAME
            # and CSSM_NAME were passed, which raised a ``KeyError``
            # during ``str.format`` and caused the entire per-customer
            # storyboard to fall back to a generic template -- the
            # technology focus instructions were silently dropped.
            customer_prompt = PROMPT_CUSTOMER_TEMPLATE.format(
                CUSTOMER_NAME=customer_name,
                CSSM_NAME=cssm_name,
                TECHNOLOGY=tech,
                MANAGER=manager,
            )

            # Round 69 / Build 43: thread the operator-selected report
            # model into the CLI per-customer storyboard path.  Resolve
            # once per customer so a UI flip mid-run takes effect on the
            # next customer narrative without restarting the analysis.
            try:
                from model_resolver import get_active_report_model as _r69_get_report_model
                _r69_report_model = _r69_get_report_model()
            except Exception:  # noqa: BLE001
                _r69_report_model = None
            customer_storyboard = generate_llm_response(customer_prompt, customer_briefing, model_name=_r69_report_model)
            # Round 71 / Phase 3 (#16): same R27 grounding gate on
            # the CLI per-customer storyboard append path.
            _r71_safe_storyboard = customer_storyboard
            if isinstance(customer_storyboard, str) and customer_storyboard.strip() and not customer_storyboard.startswith("ERROR:"):
                try:
                    import ai_narrative_validator as _r71_anv_cust
                    _r71_cust_anv_result = _r71_anv_cust.validate_narrative(
                        customer_storyboard,
                        customer_briefing,
                    )
                    if not _r71_cust_anv_result.is_valid:
                        logger.warning(
                            "Round 71 / Phase 3 (#16): CLI customer storyboard "
                            "narrative for %s failed grounding validation; "
                            "substituting placeholder. failures=%s",
                            customer_name,
                            list(_r71_cust_anv_result.failures),
                        )
                        _r71_safe_storyboard = _r71_anv_cust.GROUNDING_FAILURE_PLACEHOLDER
                except Exception as _r71_cust_anv_err:
                    logger.warning(
                        "Round 71 / Phase 3 (#16): CLI customer storyboard "
                        "narrative validator raised unexpectedly for %s (%s); "
                        "substituting placeholder for safety.",
                        customer_name,
                        _r71_cust_anv_err,
                    )
                    try:
                        from ai_narrative_validator import GROUNDING_FAILURE_PLACEHOLDER as _r71_cust_placeholder
                        _r71_safe_storyboard = _r71_cust_placeholder
                    except Exception:
                        _r71_safe_storyboard = (
                            "AI narrative withheld (grounding validator "
                            "unavailable). The data tabs in this report "
                            "remain authoritative."
                        )
            append_to_word_report(doc, _r71_safe_storyboard)

        # Save Word document
        docx_path = f"{base}.docx"
        # Round 68 / Build 42 (A1): stamp build label on the legacy
        # comprehensive Word path so the file carries a visible
        # version + build marker.
        try:
            from _r68_build_label import apply_word_footer as _r68_apply_word_footer  # noqa: PLC0415
            _r68_apply_word_footer(doc)
        except Exception as _r68_err:  # noqa: BLE001
            # Round 73 / Phase 1 (F1): promoted to warning so the next
            # missing-footer regression surfaces in the admin error log
            # instead of hiding under the default debug threshold.
            logger.warning("Round 68 / A1: legacy comprehensive footer skipped: %s", _r68_err)
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
- **Cite Specifics:** Reference actual record identifiers from the data: AB-IDs for adoption barriers, SP-IDs for success priorities, AP-IDs for action plans, CSC IDs for software defects, BEMS IDs for escalations, Case IDs for TAC cases, and incident IDs for service disruptions. These identifiers let readers verify and follow up on each claim
- **Quantify Impact:** How many customers? What's the business impact?
- **Flag Escalations:** BEMS escalations are RED FLAGS - call them out explicitly
- **Define Barriers:** Clearly explain what adoption barriers are blocking customers

**SIGNAL PRIORITY (surface in this order):**
1. **BEMS escalations** - complex engineering issues requiring backend support (highest priority)
2. **P1/P2 TAC cases** - active high-severity customer issues
3. **Critical adoption barriers** - issues blocking customers from using the product
4. **Software defects (CSC IDs)** - known bugs affecting customers
5. **General barriers and cases** - broader portfolio trends

**DATA QUALITY:** If any section has zero data when the portfolio is large, flag it as a potential data gap rather than assuming no issues exist.

**Round 4 / Phase 6.4 — NEGATIVE CONSTRAINTS (HARD RULES):**
- Do NOT invent ARR / revenue / dollar figures. If "ARR" or "$" does not appear in the briefing book, write "ARR data not provided" — never estimate or extrapolate.
- Do NOT invent percentages (%). If a percentage is not present in the briefing, write "(% not available)".
- Do NOT cite TAC, AB, BEMS, CSC, action plan, or incident IDs that are not literally present in the briefing book above.
- If "External Intelligence: NOT supplied" appears in the briefing, you MUST treat external incidents/defects as data-unavailable and not silently treat as zero.
- If a section has zero rows but the briefing labels it "data unavailable" or "fetch_error", write "data unavailable" rather than implying clean state.

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

## **Customers in Trouble (from the briefing book)**

*List EACH customer that the briefing book identifies as having problems. Round 10 / Phase 6.3: do NOT extrapolate beyond the customers that appear in the briefing -- "EVERY"/"ALL" framing previously encouraged the model to invent rows when the briefing was truncated.*

**1. [Customer] - Risk: [HIGH/CRITICAL]**
• **Problem:** [What's really wrong]
• **Barriers:** [X barriers] - **Key Issue:** [Specific blocking issue]
• **Total Support Cases (90d):** [Y] - **Open + critical (P1+P2):** [Z] ([W are P2])
  *Round 48 / F-COMP-TAC-LABEL-AMBIGUITY: do NOT emit a generic
  "TAC Cases: N cases" line.  The canonical labels are "Total
  Support Cases (90d)" for the window total and "Open + critical
  (P1+P2)" for the high-severity slice.  If a number is unavailable
  in the briefing, write "(not in briefing)" rather than padding to
  zero or omitting the label entirely.*
• **BEMS:** [Count and IDs with brackets like [BEMS01916938]]
• **Defects:** [Known bugs with IDs like [CSCxx12345]]
• **Impact:** [Business effect]
• **Action:** [Immediate step needed]

**Continue for the remaining customers that the briefing book lists, using the same detailed format. Stop when the briefing book's customer list is exhausted; do NOT pad with placeholder customers.**

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

# Round 6 / Phase 3.11: the briefing data is NOT embedded in this
# system prompt anymore.  Instead, callers MUST pass the briefing
# book as the *user* message to ``generate_llm_response``.  Mixing
# the data into the system prompt (a) inflates token cost when
# templates are cached, (b) makes prompt-injection harder to reason
# about because rules and data share the same trust frame, and (c)
# defeats Azure's content-policy boundary which treats system and
# user messages differently.  The user message will be the briefing
# book in its entirety.
"""
