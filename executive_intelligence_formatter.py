"""
Executive Intelligence Formatter for AdoptIQ
Creates executive-ready compact reports with intelligence-driven insights.
Used for Compact Analysis Reports.
"""

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import logging
import re
from data_normalization import (
    add_case_lifecycle_fields,
    detect_bems_mask,
    extract_bems_ids_from_row,
    normalize_customer_name,
    normalize_composite_customer_key,  # Round 125 / B4
)
from report_consistency import validate_report_consistency
from report_utils import (
    format_inline_source,
    format_metric_with_source,
    format_number,
    format_ratio_percent,
    format_percent_points,
    strip_bems_brackets_from_llm_text,
)
import canonical_metrics as cm
# Round 16 / Phase 5.2: pull the banded top-N table helper from the
# Round-15 Word styling SSoT so the executive-intelligence top-N
# tables get the Cisco-blue header / alternating-row banding instead
# of the inherited ``Light Grid Accent 1`` default.
from report_word_styling import add_banded_top_n_table as _r16_add_banded_top_n_table

logger = logging.getLogger(__name__)


# Round 117 / Build 86: local fallback mirroring adoptiq_backend._R78_STUB_RE
# (the SSoT). Used only if the lazy import below fails so a broken import edge
# can never block Compact report generation -- the canonical regex still wins.
_R117_STUB_RE_FALLBACK = re.compile(
    r'^\*{0,2}[A-Z][\w &/\-]+\*{0,2}\s*:\s*\*{0,2}\s*[Dd]ata\s+[Uu]navailable'
    r'\s*\*{0,2}\.?\s*\*{0,2}\s*$',
)


# Round 131 / Build 100 (F1): strip inline [Source: ...] chrome from a risk-factor
# snippet before using it as the Key Issues cell text.
_R131_SOURCE_CITATION_TAIL_RE = re.compile(
    r"\s*\[Source:[^\]]+\]",
    flags=re.IGNORECASE,
)


def _r131_format_high_risk_key_issues(data: dict) -> str:
    """Render the High-Risk Customers table Key Issues column.

    Pre-R131 the cell only counted ``ab_count`` / ``case_count`` keys that
    ``calculate_renewal_risk_scores`` never populated, so incident-/pulse-driven
    HIGH rows (e.g. WINTRUST, FARMERS, NATIONAL GRID on Build 100) showed bare
    ``N/A`` despite a non-trivial canonical risk band.
    """
    if not isinstance(data, dict):
        return "No open barriers or cases in scope"

    issues: list[str] = []
    for count_key, label in (
        ("ab_count", "barriers"),
        ("case_count", "cases"),
        ("pulse_count", "pulse"),
        ("ap_count", "action plans"),
    ):
        try:
            count = int(data.get(count_key) or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            issues.append(f"{count} {label}")

    if issues:
        return ", ".join(issues)

    factors = data.get("risk_factors") or []
    if isinstance(factors, list) and factors:
        snippet = _R131_SOURCE_CITATION_TAIL_RE.sub("", str(factors[0])).strip()
        if snippet:
            return snippet[:120]

    band = str(data.get("risk_band") or "").strip()
    if band:
        return f"Elevated {band.lower()} band (no open barriers or cases in scope)"
    return "No open barriers or cases in scope"


def _r117_is_stub_bullet(bullet_text: str) -> bool:
    """True when the ENTIRE bullet is a "<Category>: Data unavailable." stub.

    Parity with the Comprehensive path (R78/B1). Prefers the SSoT regex from
    ``adoptiq_backend`` and falls back to the local copy on any import failure.
    """
    if not bullet_text:
        return False
    try:
        from adoptiq_backend import _R78_STUB_RE as _stub_re  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        _stub_re = _R117_STUB_RE_FALLBACK
    try:
        return bool(_stub_re.match(bullet_text.strip()))
    except Exception:  # noqa: BLE001
        return False


# Professional color palette
CISCO_BLUE = RGBColor(0x00, 0x7B, 0xC7)
CISCO_DARK_BLUE = RGBColor(0x00, 0x4F, 0x8B)
CISCO_LIGHT_BLUE = RGBColor(0x5C, 0xC7, 0xF0)
CISCO_GRAY = RGBColor(0x58, 0x59, 0x5B)
CISCO_LIGHT_GRAY = RGBColor(0xF5, 0xF5, 0xF5)

# Status colors
# Round 13 / Phase 5.4: previously this module exported a parallel
# traffic-light palette (``EXCELLENT_GREEN`` 0x008B00,
# ``SUCCESS_GREEN`` 0x28A745, ``WARNING_ORANGE`` 0xFF8C00,
# ``DANGER_RED`` 0xDC143C) that diverged from the canonical
# matplotlib / Excel band palette in ``canonical_metrics``
# (CRITICAL=#d62728, HIGH=#ff7f0e, LOW=#2ca02c, HEALTHY=#28B463).
# That meant the EI-formatter Word output rendered "danger" in a
# brighter red than the corresponding matplotlib chart, and
# "success" in two slightly different greens across one document.
# Re-resolve the four traffic-light aliases from the canonical
# ``RISK_BAND_COLORS`` map at import time.  The historical CRITICAL
# darkening (``CRITICAL_RED`` was ``0x8B0000``) is preserved as a
# 50% saturation of the canonical CRITICAL since we have no canonical
# "darker than CRITICAL" entry; if that ever changes we can drop the
# fallback.
try:
    from canonical_metrics import RISK_BAND_COLORS as _R13_EI_RBC

    def _r13_ei_hex_to_rgb(_hex_str: str, _fallback: 'RGBColor') -> 'RGBColor':
        try:
            _h = (_hex_str or '').lstrip('#')
            if len(_h) != 6:
                return _fallback
            return RGBColor(int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16))
        except Exception:
            return _fallback

    EXCELLENT_GREEN = _r13_ei_hex_to_rgb(
        _R13_EI_RBC.get('HEALTHY', '#28B463'),
        RGBColor(0x28, 0xB4, 0x63),
    )
    SUCCESS_GREEN = _r13_ei_hex_to_rgb(
        _R13_EI_RBC.get('LOW', '#2ca02c'),
        RGBColor(0x2c, 0xa0, 0x2c),
    )
    WARNING_ORANGE = _r13_ei_hex_to_rgb(
        _R13_EI_RBC.get('HIGH', '#ff7f0e'),
        RGBColor(0xff, 0x7f, 0x0e),
    )
    DANGER_RED = _r13_ei_hex_to_rgb(
        _R13_EI_RBC.get('CRITICAL', '#d62728'),
        RGBColor(0xd6, 0x27, 0x28),
    )
    # We don't have a canonical "darker than CRITICAL" hex; keep the
    # historical dark-red fallback so Word rendering of CRITICAL_RED
    # stays distinguishable from DANGER_RED in legends.
    CRITICAL_RED = RGBColor(0x8B, 0x00, 0x00)
except Exception:  # pragma: no cover - defensive
    EXCELLENT_GREEN = RGBColor(0x00, 0x8B, 0x00)
    SUCCESS_GREEN = RGBColor(0x28, 0xA7, 0x45)
    WARNING_ORANGE = RGBColor(0xFF, 0x8C, 0x00)
    DANGER_RED = RGBColor(0xDC, 0x14, 0x3C)
    CRITICAL_RED = RGBColor(0x8B, 0x00, 0x00)


def _r13_safe_doc_text(value: Any, max_len: int = 200) -> str:
    """Round 13 / Phase 9.3: sanitize a string before docx ``add_run``.

    Mirrors the helper in ``app_simple._safe_doc_text`` /
    ``compact_report_formatter._safe_doc_text`` /
    ``advanced_renewal_analyzer._safe_doc_text`` so the EI formatter
    can route raw customer / count strings through the same
    XML-illegal-control-code stripper before they reach python-docx.
    Without this guard, a customer name carrying a zero-width space,
    tab, or surrogate code point produced a .docx that Word refused
    to open without "repair".
    """
    try:
        s = "" if value is None else str(value)
    except Exception:
        return ""
    cleaned: list[str] = []
    for ch in s:
        cp = ord(ch)
        if cp < 0x20 and ch not in ('\t', '\n', '\r'):
            continue
        if 0xD800 <= cp <= 0xDFFF:
            continue
        cleaned.append(ch)
    s = ''.join(cleaned)
    s = re.sub(r'\s+', ' ', s).strip()
    if max_len and len(s) > max_len:
        s = s[: max_len - 1] + '\u2026'
    return s


def _ensure_inline_source_claim(
    text: Any,
    metric_name: str = "Derived Metric",
    fields: Optional[List[str]] = None,
) -> str:
    claim = str(text or "").strip()
    if not claim:
        return ""
    if re.search(r"\[\s*source\s*:", claim, flags=re.IGNORECASE):
        return claim
    return f"{claim} {format_inline_source(metric_name, fields=fields or [])}"


# Round 118 / Build 87: dedup TAC/CSOne case rows by case identifier.
#
# The Build 86 Compact acceptance audit found case # 700840277 (WINTRUST
# FINANCIAL CORPORATION US) rendered THREE byte-identical times in the
# TAC Lifecycle Snapshot table -- and ``canonical_metrics.count_total_tac``
# is ``_safe_len(csone_df)``, so the inflated row set also over-counted
# the "Total Support Cases" KPI.  Root cause: the R40 per-customer
# TAC -> subscription join fans a single case out to one row per matched
# subscription, so a case tied to N subscriptions appears N times.  A
# TAC ``Case #`` is the unique support-case identifier, so collapsing on
# it with ``keep='first'`` is the canonical de-fan (mirrors the R78/B2
# Leader Action_Plans dedup-by-ID contract).  Applied at BOTH
# ``csone_norm`` ingestion points so the count KPI and the rendered
# table agree.  No-op when no case-id column is present (the frame keeps
# its original rows) so non-TAC callers are unaffected.
_R118_TAC_CASE_ID_CANDIDATES = ("Case #", "SR Number", "CaseNumber", "case_id", "CASE_NUMBER")


def _r118_dedup_tac_cases(df: Any) -> Any:
    """Round 139: thin wrapper over ``data_normalization.collapse_tac_cases``."""
    try:
        from data_normalization import collapse_tac_cases as _r139_collapse_tac_cases

        return _r139_collapse_tac_cases(df)
    except Exception:  # noqa: BLE001 - dedup must never block report generation
        logger.debug("Round 139: TAC collapse wrapper skipped (non-fatal)", exc_info=False)
        return df


# Round 44 / Phase 1: NaN-safe Days Open renderer for the compact TAC
# lifecycle table.  Pre-Round-44 the table did
# ``str(row.get('open_age_days', 'N/A'))`` -- when ``open_age_days``
# was missing/NaN the cell rendered as the literal string ``"nan"``
# (because ``str(float('nan')) == 'nan'``).  Audited Build-20 compact
# output had 100% of the 20 sampled lifecycle rows render ``nan``, even
# for rows whose ``open_date`` and ``closed_date`` were both populated
# and parseable -- which means the upstream ``open_age_days`` column is
# unreliable on the compact path and the renderer must backfill from
# the dates instead of trusting it.
def _format_open_age_days(row: Any) -> str:
    """Return a display-safe Days Open value for a TAC lifecycle row.

    Resolution order:
      1. If ``open_age_days`` parses to a non-negative integer, use it.
      2. Else, if ``open_date`` and ``closed_date`` are both parseable
         datetimes, render ``max(0, (closed - opened).days)``.
      3. Else, if only ``open_date`` is parseable, render the count of
         days from opened to today (UTC) -- still "open" at audit time.
      4. Else, return ``"\u2014"`` (em-dash).  Never returns the literal
         string ``"nan"``.
    """
    try:
        raw = row.get('open_age_days', None)
    except Exception:
        raw = None
    if raw is not None:
        try:
            if not (isinstance(raw, float) and pd.isna(raw)):
                if pd.notna(raw):
                    val = int(float(raw))
                    if val >= 0:
                        return str(val)
        except (ValueError, TypeError):
            pass

    def _coerce_dt(value: Any):
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        try:
            ts = pd.to_datetime(value, errors='coerce', utc=False)
        except Exception:
            return None
        if ts is None or pd.isna(ts):
            return None
        return ts

    try:
        opened = _coerce_dt(row.get('open_date', row.get('Date/Time Opened', None)))
    except Exception:
        opened = None
    try:
        closed = _coerce_dt(row.get('closed_date', None))
    except Exception:
        closed = None

    if opened is not None and closed is not None:
        try:
            delta_days = (closed - opened).days
            return str(max(0, int(delta_days)))
        except Exception:
            pass

    if opened is not None:
        try:
            now_utc = pd.Timestamp.utcnow()
            opened_naive = opened.tz_localize(None) if getattr(opened, 'tzinfo', None) is not None else opened
            now_naive = now_utc.tz_localize(None) if getattr(now_utc, 'tzinfo', None) is not None else now_utc
            delta_days = (now_naive - opened_naive).days
            return str(max(0, int(delta_days)))
        except Exception:
            pass

    return "\u2014"


# Round 121 / G4b: support-case Type display relabel.
_R121_TYPE_SENTINELS = frozenset({"", "unknown", "nan", "none", "n/a", "na"})


def _r121_display_case_type(value: Any) -> str:
    """Round 121 / G4b: render the support-case ``Type`` cell display-friendly.

    ``data_normalization.classify_case_type`` returns the literal
    ``"unknown"`` sentinel when a case cannot be classified.  Pre-R121 the TAC
    lifecycle table rendered that bare sentinel in the ``Type`` column.  This
    helper relabels ONLY the bare sentinel tokens to ``"Unclassified"`` (the
    same friendly wording G3 uses for BE Focus Areas) -- genuine classified
    types like ``"break_fix_technical"`` are passed through unchanged.  The
    underlying ``case_type_class`` value is NOT mutated; this is display-only.
    """

    text = "" if value is None else str(value).strip()
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            text = ""
    except (TypeError, ValueError):
        pass
    if text.lower() in _R121_TYPE_SENTINELS:
        return "Unclassified"
    return text


class ExecutiveIntelligenceFormatter:
    """
    Executive Intelligence Formatter for compact, insight-driven reports.
    Focuses on actionable intelligence and risk assessment.
    """

    def __init__(self, output_path: str = None):
        """Initialize the formatter"""
        self.doc = Document()
        self.output_path = output_path
        self._setup_document_settings()
        self._create_professional_styles()

    def _setup_document_settings(self):
        """Configure document-wide settings"""
        sections = self.doc.sections
        for section in sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)

    def _create_professional_styles(self):
        """Create professional document styles"""
        styles = self.doc.styles

        # Executive Title
        if 'Executive Title' not in [s.name for s in styles]:
            title_style = styles.add_style('Executive Title', WD_STYLE_TYPE.PARAGRAPH)
            title_style.font.name = 'Segoe UI'
            title_style.font.size = Pt(24)
            title_style.font.bold = True
            title_style.font.color.rgb = CISCO_BLUE
            title_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
            title_style.paragraph_format.space_after = Pt(20)

        # Section Header
        if 'Section Header' not in [s.name for s in styles]:
            header_style = styles.add_style('Section Header', WD_STYLE_TYPE.PARAGRAPH)
            header_style.font.name = 'Segoe UI'
            header_style.font.size = Pt(14)
            header_style.font.bold = True
            header_style.font.color.rgb = CISCO_DARK_BLUE
            header_style.paragraph_format.space_before = Pt(16)
            header_style.paragraph_format.space_after = Pt(8)

    def _clean_text(self, text: str) -> str:
        """Clean markdown and formatting from text"""
        if not text:
            return ""

        # Remove markdown
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        text = re.sub(r'`(.*?)`', r'\1', text)
        text = re.sub(r'---+\s*', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    def add_title_page(self, manager: str, technology: str, days: int,
                        data_retrieved_at: Optional[datetime] = None):
        """Add executive title page.

        Phase 3.1: ``data_retrieved_at`` (UTC) is propagated from the
        ``AnalysisRunContext`` so the cover page can render both the
        render time (``Generated``) and the data fetch time
        (``Data as of``). This is the difference between blaming the
        report for stale numbers vs. blaming the source.
        """
        # Logo/Branding
        logo_para = self.doc.add_paragraph()
        logo_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        logo_run = logo_para.add_run("● CISCO AdoptIQ")
        logo_run.font.name = 'Segoe UI'
        logo_run.font.size = Pt(16)
        logo_run.font.color.rgb = CISCO_BLUE
        logo_run.font.bold = True

        self.doc.add_paragraph()

        # Main Title
        title_para = self.doc.add_paragraph()
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title_para.add_run(f"Executive Intelligence Report")
        title_run.font.name = 'Segoe UI'
        title_run.font.size = Pt(24)
        title_run.font.bold = True
        title_run.font.color.rgb = CISCO_BLUE

        # Subtitle
        subtitle_para = self.doc.add_paragraph()
        subtitle_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subtitle_run = subtitle_para.add_run(f"{manager or 'N/A'} | {technology or 'N/A'} | {days or 'N/A'} Day Analysis")
        subtitle_run.font.name = 'Segoe UI'
        subtitle_run.font.size = Pt(14)
        subtitle_run.font.color.rgb = CISCO_GRAY

        # Date
        self.doc.add_paragraph()
        date_para = self.doc.add_paragraph()
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # Round 7 / Phase 1.3: render the cover-page "Generated"
        # timestamp in UTC so it agrees with the "Data as of" line
        # below (which is already labelled UTC).  Local timezone here
        # made the cover page disagree with itself depending on the
        # server's locale.
        date_run = date_para.add_run(
            f"Generated: {datetime.now(timezone.utc).strftime('%B %d, %Y')} UTC"
        )
        date_run.font.name = 'Segoe UI'
        date_run.font.size = Pt(11)
        date_run.font.color.rgb = CISCO_GRAY

        if data_retrieved_at is not None:
            data_para = self.doc.add_paragraph()
            data_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Round 7 / Phase 1.10: narrow this except so unexpected
            # failures are logged + propagated.  ``strftime`` only
            # raises ``AttributeError`` (non-datetime input) or
            # ``ValueError`` (locale issue); anything else here is a
            # real bug we want to see in logs rather than silently
            # converting to ``str(data_retrieved_at)``.
            try:
                _data_str = data_retrieved_at.strftime('%B %d, %Y at %H:%M UTC')
            except (AttributeError, ValueError) as _strftime_err:
                logger.warning(
                    "Round 7 / Phase 1.10: data_retrieved_at strftime "
                    "failed (type=%s): %s",
                    type(data_retrieved_at).__name__, _strftime_err,
                )
                _data_str = str(data_retrieved_at)
            data_run = data_para.add_run(f"Data as of: {_data_str}")
            data_run.font.name = 'Segoe UI'
            data_run.font.size = Pt(10)
            data_run.font.color.rgb = CISCO_GRAY

    def add_executive_dashboard(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame,
                               risk_scores: Dict[str, Any], risk_summary: Dict[str, Any],
                               team_subs_df: pd.DataFrame = None,
                               csconsole_action_plans: pd.DataFrame = None,
                               csconsole_customer_pulse: pd.DataFrame = None,
                               csconsole_success_priorities: pd.DataFrame = None,
                               csconsole_adoption_barriers: pd.DataFrame = None,
                               software_defects: Dict = None, psirt_vulns: Dict = None):
        """Add executive dashboard with key metrics using ALL data sources"""
        if csone_data is None:
            csone_data = pd.DataFrame()
        if ab_data is None:
            ab_data = pd.DataFrame()
        self.doc.add_paragraph()

        # Dashboard header
        header = self.doc.add_heading('At-a-Glance Dashboard', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE

        # Create metrics table - expanded to include software defects and vulnerabilities (7 columns total)
        metrics_table = self.doc.add_table(rows=2, cols=7)
        metrics_table.style = 'Light Grid Accent 1'

        # CRITICAL FIX: Use comprehensive function to get ALL customers from ALL available data sources
        # This ensures consistent customer counts across all report types
        # team_subs_df is the PRIMARY source (unfiltered, contains all customers assigned to manager)
        try:
            from app_simple import _get_all_customers_from_all_sources
            all_customers_set = _get_all_customers_from_all_sources(
                ab_norm=ab_data if ab_data is not None else pd.DataFrame(),
                csone_df=csone_data if csone_data is not None else pd.DataFrame(),
                team_subs_df=team_subs_df if team_subs_df is not None else pd.DataFrame(),
                csconsole_action_plans=csconsole_action_plans if csconsole_action_plans is not None else pd.DataFrame(),
                csconsole_customer_pulse=csconsole_customer_pulse if csconsole_customer_pulse is not None else pd.DataFrame(),
                csconsole_success_priorities=csconsole_success_priorities if csconsole_success_priorities is not None else pd.DataFrame(),
                csconsole_adoption_barriers=csconsole_adoption_barriers if csconsole_adoption_barriers is not None else pd.DataFrame()
            )
            total_customers = len(all_customers_set)
            logger.info(f"[[CUSTOMER_COUNT]] Executive Intelligence Report - Total unique customers from all data sources: {total_customers}")
            logger.info(f"[[CUSTOMER_COUNT]] Team subscriptions (PRIMARY SOURCE): {len(team_subs_df['BU_NAME'].unique()) if team_subs_df is not None and not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns else 0} customers")
            logger.info(f"[[CUSTOMER_COUNT]] Team subscriptions DataFrame has {len(team_subs_df) if team_subs_df is not None else 0} rows")
            if team_subs_df is not None and not team_subs_df.empty and 'BU_NAME' in team_subs_df.columns:
                # Round 7 / Phase 1.9: redact BU_NAME samples in
                # INFO logs.  This line previously printed the raw
                # first 5 customer names which is PII for shared log
                # destinations (Splunk / cloud aggregator).  At INFO
                # we now emit a count + a hashed digest; the full
                # sample is moved to DEBUG behind a feature flag so
                # operators can still pull it locally when
                # troubleshooting.
                _bu_unique = team_subs_df['BU_NAME'].dropna().unique()
                _sample_n = min(5, len(_bu_unique))
                try:
                    import hashlib as _hl
                    _digest = _hl.sha256(
                        '|'.join(sorted(str(x) for x in _bu_unique[:_sample_n])).encode('utf-8')
                    ).hexdigest()[:8]
                except Exception:
                    _digest = "unavailable"
                logger.info(
                    "[[CUSTOMER_COUNT]] team_subs_df sample: count=%d digest=%s",
                    _sample_n, _digest,
                )
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "[[CUSTOMER_COUNT]] Sample customers from team_subs_df: %s",
                        _bu_unique[:_sample_n].tolist(),
                    )
        except (ImportError, AttributeError) as e:
            logger.warning(f"[[WARNING]] Could not import _get_all_customers_from_all_sources, using cm.count_customers fallback: {e}")
            # Canonical fallback: union of normalized names across every
            # available source. This keeps EI in agreement with Compact /
            # Leader / Renewal which all use cm.count_customers, instead
            # of returning a smaller subs_df-only or AB-only count.
            total_customers = cm.count_customers(
                ab_df=ab_data if ab_data is not None else pd.DataFrame(),
                csone_df=csone_data if csone_data is not None else pd.DataFrame(),
                subs_df=team_subs_df if team_subs_df is not None else pd.DataFrame(),
                action_plans_df=csconsole_action_plans if csconsole_action_plans is not None else pd.DataFrame(),
                pulse_df=csconsole_customer_pulse if csconsole_customer_pulse is not None else pd.DataFrame(),
                extra_frames=[
                    csconsole_success_priorities if csconsole_success_priorities is not None else pd.DataFrame(),
                    csconsole_adoption_barriers if csconsole_adoption_barriers is not None else pd.DataFrame(),
                ],
            )
        csone_norm = add_case_lifecycle_fields(csone_data if csone_data is not None else pd.DataFrame())
        # Round 118 / Build 87: de-fan cross-subscription duplicate case
        # rows BEFORE counting so the KPI matches the rendered table.
        csone_norm = _r118_dedup_tac_cases(csone_norm)
        total_cases = cm.count_total_tac(csone_norm)

        # Canonical priority and BEMS counts shared with Compact / Leader.
        p1_count = cm.count_p1(csone_norm)
        p2_count = cm.count_p2(csone_norm)
        bems_count = cm.count_bems(csone_norm)

        # Extract software defects and PSIRT vulnerabilities counts
        defect_count = 0
        vuln_count = 0
        if software_defects:
            defect_count = software_defects.get('total_defects', 0)
        if psirt_vulns:
            vuln_count = psirt_vulns.get('total_vulnerabilities', 0)

        # Header row - expanded to include software defects and vulnerabilities
        headers = ['Total Customers', 'Support Cases', 'Critical (P1)', 'High (P2)', 'BEMS Escalations', 'Software Defects', 'Security Vulnerabilities']
        for i, header_text in enumerate(headers):
            cell = metrics_table.rows[0].cells[i]
            cell.text = header_text
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(10)
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Values row - expanded to include software defects and vulnerabilities
        values = [str(total_customers), str(total_cases), str(p1_count), str(p2_count), str(bems_count), str(defect_count), str(vuln_count)]
        colors = [None, None, DANGER_RED if p1_count > 0 else None, WARNING_ORANGE if p2_count > 0 else None, CRITICAL_RED if bems_count > 0 else None, WARNING_ORANGE if defect_count > 0 else None, DANGER_RED if vuln_count > 0 else None]

        for i, (value, color) in enumerate(zip(values, colors)):
            cell = metrics_table.rows[1].cells[i]
            cell.text = value
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(16)
                    run.bold = True
                    if color:
                        run.font.color.rgb = color
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Inline source-backed metrics for dashboard facts
        src_para = self.doc.add_paragraph()
        src_para.add_run("Metric source backing:\n").bold = True
        dashboard_metric_facts = [
            format_metric_with_source(
                "Total Customers",
                total_customers,
                "Derived Metric",
                # Round 44 / Phase 6: friendly field labels so the
                # source-citation paragraph shows business names
                # (Customer Name, Account ID) instead of raw
                # Snowflake _C-suffixed columns.
                fields=["Customer Name", "Account ID"],
                source_override="Normalized customer set from team subscriptions + CSConsole + CSOne",
                verification_override="Cross-check customer IDs/names in source exports",
            ),
            format_metric_with_source("Support Cases", total_cases, "Support Cases (TAC)", fields=["Case #", "Severity", "Status"]),
            format_metric_with_source("Critical (P1)", p1_count, "Support Cases (TAC)", fields=["Severity"]),
            format_metric_with_source("High (P2)", p2_count, "Support Cases (TAC)", fields=["Severity"]),
            format_metric_with_source("BEMS Escalations", bems_count, "BEMS Escalations", fields=["Transaction ID", "bemscsc_refs"]),
            format_metric_with_source("Software Defects", defect_count, "Software Defects", fields=["CSC ID", "BST ID"]),
            format_metric_with_source(
                "Security Vulnerabilities",
                vuln_count,
                # Source label was previously "Service Incidents" which is the
                # label for status.webex.com incidents — Security
                # Vulnerabilities come from PSIRT, not the incidents feed.
                "PSIRT Advisories",
                fields=["CVE ID", "Advisory ID"],
                source_override="PSIRT advisories and vulnerability feeds",
                verification_override="Verify advisory/CVE identifiers in PSIRT and public advisories",
            ),
        ]
        for fact in dashboard_metric_facts:
            bullet = self.doc.add_paragraph(style='List Bullet')
            bullet.add_run(fact)

        self.doc.add_paragraph()

        # Risk Summary
        if risk_summary:
            # Round 7 / Phase 1.4: route counts/scores through the
            # shared ``format_number`` / ``format_ratio_percent``
            # helpers so the EI Word matches the rounding/percent
            # convention used by every other report.  Previously we
            # printed ``risk_summary['overall_risk_score']`` raw,
            # which surfaced floats like ``5.123456789`` and integers
            # without thousands separators.
            risk_para = self.doc.add_paragraph()
            risk_para.add_run('Risk Summary: ').font.bold = True
            _overall = risk_summary.get('overall_risk_score')
            _overall_str = (
                'N/A' if _overall in (None, 'N/A')
                else format_number(_overall, decimals=1)
            )
            risk_para.add_run(f"Overall Risk Score: {_overall_str} | ")
            # Round 10 / Phase 4.1: derive the headline high-risk count from
            # the *same* ``cm.is_high_risk_profile`` predicate that
            # ``add_risk_analysis_section`` uses to populate the
            # high-risk-customer table. Previously this read
            # ``risk_summary['high_risk_customers']`` which can disagree
            # with the predicate (the summary may be sourced from a
            # different scale / fallback path), so the dashboard would
            # claim "12 high-risk customers" while the table below it
            # listed only 9. Re-derive from ``risk_scores`` so the two
            # surfaces always agree.
            try:
                _ei_high_count = sum(
                    1 for _v in (risk_scores or {}).values()
                    if cm.is_high_risk_profile(_v, scale=cm.RISK_SCALE_0_TO_10)
                )
            except Exception:
                _ei_high_count = int(risk_summary.get('high_risk_customers', 0) or 0)
            risk_para.add_run(
                f"High Risk Customers: {format_number(_ei_high_count, decimals=0)} | "
            )
            # Round 4: clarify the legacy ``moderate_risk_customers`` key
            # (score 4-6 on 0-10 scale) is the score-range "Watch" bucket
            # and is *not* the same as the canonical band MEDIUM
            # (35-55 on 0-100 scale).  Prefer the canonical band count
            # (medium_risk_customers) when available so the narrative
            # ties out with the executive donut.
            _medium_band = risk_summary.get('medium_risk_customers')
            if _medium_band is not None:
                # Round 118 / Build 87: the canonical band vocabulary is
                # MODERATE (not MEDIUM) per the R67/B1 cross-format parity
                # contract -- the rest of the Compact narrative + the
                # Renewal report already render "MODERATE".  Pre-R118 this
                # lone Risk Summary line still said "Medium Risk (band)",
                # so the same customer's band was spelled two different ways
                # in one report.  The underlying canonical key stays
                # ``medium_risk_customers`` (the 35-55 / 0-100 band); only
                # the user-facing word is corrected to MODERATE.
                risk_para.add_run(
                    f"Moderate Risk (band): {format_number(_medium_band, decimals=0)}"
                )
                _watch = risk_summary.get('moderate_risk_customers')
                if _watch is not None and _watch != _medium_band:
                    risk_para.add_run(
                        f" | Score 4-6 (Watch, 0-10 scale): {format_number(_watch, decimals=0)}"
                    )
            else:
                risk_para.add_run(
                    f"Score 4-6 (Watch, 0-10 scale): {format_number(risk_summary.get('moderate_risk_customers', 0), decimals=0)}"
                )

    def add_executive_summary(self, ai_insights: Dict[str, Any]):
        """Add AI-generated executive summary"""
        header = self.doc.add_heading('Executive Summary', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE

        summary_text = ""
        if ai_insights:
            if 'executive_summary' in ai_insights:
                summary_text = ai_insights['executive_summary']
            elif 'portfolio_summary' in ai_insights and 'executive_summary' in ai_insights['portfolio_summary']:
                summary_text = ai_insights['portfolio_summary']['executive_summary']
            elif 'raw_response' in ai_insights:
                summary_text = ai_insights['raw_response']

        if summary_text:
            clean_text = self._clean_text(summary_text)
            # Round 50 / F-COMP-BEMS-MD-LEAK-EI-PATH: compact production
            # docx generation flows through ExecutiveIntelligenceFormatter
            # (run_compact_analysis -> create_executive_intelligence_report),
            # not the fallback markdown parser. Strip brackets around real
            # BEMS/CSC IDs here so the LLM summary cannot leak
            # "[BEMS01943186]" style tokens into shipped compact reports.
            clean_text = strip_bems_brackets_from_llm_text(clean_text)
            self._parse_and_add_content(clean_text)
        else:
            # Round 3 / Phase 2.2: this branch fires AFTER a completed
            # run. The previous "AI-generated insights are being
            # processed. Please check back shortly." copy implied a
            # transient pipeline state and invited readers to refresh.
            # In reality the LLM either returned no parsable summary
            # or the call failed entirely. Be honest so a reader does
            # not assume content is on the way.
            para = self.doc.add_paragraph()
            run = para.add_run(
                "AI summary unavailable for this run."
            )
            run.bold = True
            _err_text = ""
            if isinstance(ai_insights, dict):
                _err_text = (
                    str(
                        ai_insights.get("llm_error")
                        or ai_insights.get("error")
                        or ai_insights.get("failure_reason")
                        or ""
                    ).strip()
                )
            if _err_text:
                detail = self.doc.add_paragraph()
                detail_run = detail.add_run(f"Reason: {_err_text}")
                detail_run.italic = True
            else:
                detail = self.doc.add_paragraph()
                detail_run = detail.add_run(
                    "The model did not return a parsable executive summary "
                    "for this run. The metrics, tables, and charts elsewhere "
                    "in this report remain accurate; only the AI narrative "
                    "is missing."
                )
                detail_run.italic = True
        provenance = self.doc.add_paragraph()
        provenance.add_run(
            "Severity provenance: TAC severity comes from CSOne case priority/severity fields; "
            "Customer Pulse uses CSConsole pulse ratings (Red/Amber/Green) and recency, not TAC severity."
        )
        provenance.add_run(
            # Round 44 / Phase 6: friendly field labels (Pulse Rating,
            # Created Date) instead of raw Snowflake _C-suffixed
            # columns.  Severity already uses the friendly form.
            f" {format_inline_source('Derived Metric', fields=['Severity', 'Pulse Rating', 'Created Date'])}"
        )

    def _parse_and_add_content(self, text: str):
        """Parse text content and add to document with proper formatting"""
        if not text:
            return

        sections = text.split('\n\n')

        for section in sections:
            if not section.strip():
                continue

            lines = section.strip().split('\n')

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Round 11 / Phase 9.5: previous rule treated *any*
                # short line ending in ``:`` as an H2 heading, so LLM
                # prose like ``Caution:`` or ``Note:`` got promoted
                # to a section heading and broke document structure.
                # Require an explicit markdown ``##``/``###`` prefix
                # OR a Title-Cased label that is short, contains no
                # internal sentence punctuation, and is followed by
                # nothing (the colon is the entire payload, not the
                # lead-in to a sentence).
                _heading_md = re.match(r'^(#{2,3})\s+(.+)$', line)
                _heading_label = (
                    line.endswith(':')
                    and len(line) < 60
                    and ':' == line[-1]
                    and ',' not in line
                    and ';' not in line
                    and '.' not in line[:-1]
                    and line.rstrip(':').strip()[:1].isupper()
                    and not line.lower().startswith((
                        'caution:', 'note:', 'warning:', 'tip:',
                        'example:', 'example -', 'context:', 'reminder:',
                        'important:', 'fyi:', 'as of:', 'see:',
                    ))
                )
                if _heading_md or _heading_label:
                    if _heading_md:
                        _level = len(_heading_md.group(1)) - 1
                        header_text = _heading_md.group(2).strip()
                    else:
                        _level = 2
                        header_text = line.rstrip(':')
                    header = self.doc.add_heading(header_text, level=_level)
                    if header.runs:
                        header.runs[0].font.color.rgb = CISCO_DARK_BLUE
                        header.runs[0].font.size = Pt(12)

                # Bullet points
                elif line.startswith(('•', '-', '*')):
                    bullet_text = line.lstrip('•-* ')
                    # Round 117 / Build 86: parity with the Comprehensive
                    # path (R78/B1) -- drop pure "<Category>: Data unavailable."
                    # acknowledgement stubs so the Compact executive summary
                    # never ships an empty bullet. The Comprehensive narrative
                    # uses adoptiq_backend.append_to_word_report which already
                    # filters these; Compact uses THIS formatter, which was the
                    # lone holdout. SSoT regex is adoptiq_backend._R78_STUB_RE;
                    # lazy-import with a local fallback so a broken import edge
                    # never blocks report generation.
                    if _r117_is_stub_bullet(bullet_text):
                        continue
                    para = self.doc.add_paragraph(style='List Bullet')
                    run = para.add_run(
                        _ensure_inline_source_claim(
                            bullet_text,
                            "Derived Metric",
                            fields=["customer_name", "Case #", "ID"],
                        )
                    )
                    run.font.name = 'Segoe UI'
                    run.font.size = Pt(11)

                # Numbered items
                elif re.match(r'^\d+[\.\)]\s', line):
                    number_text = re.sub(r'^\d+[\.\)]\s*', '', line)
                    para = self.doc.add_paragraph(style='List Number')
                    run = para.add_run(
                        _ensure_inline_source_claim(
                            number_text,
                            "Derived Metric",
                            fields=["customer_name", "Case #", "ID"],
                        )
                    )
                    run.font.name = 'Segoe UI'
                    run.font.size = Pt(11)

                # Regular paragraph
                else:
                    para = self.doc.add_paragraph()
                    run = para.add_run(
                        _ensure_inline_source_claim(
                            line,
                            "Derived Metric",
                            fields=["customer_name", "Case #", "ID"],
                        )
                    )
                    run.font.name = 'Segoe UI'
                    run.font.size = Pt(11)

    def add_risk_analysis_section(self, risk_scores: Dict[str, Any], ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add detailed risk analysis section"""
        header = self.doc.add_heading('Risk Analysis', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE

        # High-risk customers — Round 3: route through the canonical
        # predicate so the table contents always match the headline /
        # dashboard count produced by ``cm.compute_high_risk_count``.
        # This catches rows that were classified red/CRITICAL/HIGH via
        # band override but whose raw ``score`` is below 6.
        high_risk = {
            k: v for k, v in (risk_scores or {}).items()
            if cm.is_high_risk_profile(v, scale=cm.RISK_SCALE_0_TO_10)
        }

        if high_risk:
            subheader = self.doc.add_heading('High-Risk Customers Requiring Immediate Attention', level=2)
            if subheader.runs:
                subheader.runs[0].font.color.rgb = DANGER_RED

            # Create table for high-risk customers
            if len(high_risk) > 0:
                table = self.doc.add_table(rows=1, cols=3)
                table.style = 'Light Grid Accent 1'

                # Header
                headers = ['Customer', 'Risk Score', 'Key Issues']
                for i, h in enumerate(headers):
                    table.rows[0].cells[i].text = h
                    if table.rows[0].cells[i].paragraphs:
                        cell_para = table.rows[0].cells[i].paragraphs[0]
                        if cell_para.runs:
                            cell_para.runs[0].font.bold = True

                # FIXED: Show ALL high-risk customers
                # Round 18 / Phase 2.1: tuple sort key with a casefolded
                # name secondary tiebreaker so two customers tied on the
                # same score render in the same order across runs
                # regardless of upstream dict insertion order.
                def _high_risk_sort_key(item):
                    name, profile = item
                    try:
                        score = (
                            float(profile.get('score', 0))
                            if isinstance(profile, dict) else 0.0
                        )
                    except (TypeError, ValueError):
                        score = 0.0
                    return (-score, str(name).casefold())

                for customer, data in sorted(
                    high_risk.items(),
                    key=_high_risk_sort_key,
                ):
                    row = table.add_row().cells
                    # Round 11 / Phase 3.4: normalize the displayed
                    # cell so spelling variants of the same account
                    # collapse to a single canonical label – matches
                    # the dashboard / Word body normalization.
                    try:
                        # Round 125 / B4: collapse the Snowflake composite key
                        # (``ELEVANCE_ELEVANCE HEALTH_US``) to the readable name
                        # BEFORE the generic display normalize, so the compact
                        # high-risk DOCX table no longer leaks the raw join key
                        # (matches the Excel High_Risk_Customers fix in
                        # app_simple).
                        _disp = normalize_composite_customer_key(str(customer))
                        _disp = normalize_customer_name(_disp) or _disp or str(customer)
                    except Exception:
                        _disp = str(customer)
                    row[0].text = _disp  # Full normalized customer name
                    # Round 7 / Phase 1.7: render N/A for missing /
                    # NaN / non-finite scores instead of coercing to
                    # ``0`` and printing ``0/10``.  A literal "0/10"
                    # in this column reads as "lowest possible risk"
                    # to executives, which is the exact opposite of
                    # "score not computed" -- the row was selected
                    # for inclusion precisely because the canonical
                    # band classifier flagged it as HIGH/CRITICAL.
                    _score = data.get('score')
                    _score_is_missing = (
                        _score is None
                        or (isinstance(_score, float) and not (_score == _score))
                    )
                    if _score_is_missing:
                        row[1].text = "N/A"
                    else:
                        row[1].text = f"{format_number(_score, decimals=1)}/10"

                    # Round 131 / F1: honest Key Issues — counts when present,
                    # else dominant risk-factor / band label (never bare N/A).
                    row[2].text = _r131_format_high_risk_key_issues(
                        data if isinstance(data, dict) else {}
                    )
        else:
            para = self.doc.add_paragraph()
            para.add_run("No high-risk customers identified in this analysis period.")

    def add_bems_escalation_section(self, csone_data: pd.DataFrame):
        """Add BEMS escalation analysis section - CRITICAL for executive visibility"""
        header = self.doc.add_heading('🚨 BEMS Escalation Analysis', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = DANGER_RED

        # Intro paragraph
        intro = self.doc.add_paragraph()
        intro.add_run("BEMS (Back-End Engineering Management System) escalations indicate complex technical issues "
                     "requiring specialized backend engineering attention. These are critical indicators of customer risk.")

        # Extract BEMS data
        total_bems = 0
        bems_by_customer = {}
        bems_ids = []
        break_fix_total = 0
        provisioning_total = 0
        csone_norm = pd.DataFrame()

        if csone_data is not None and not csone_data.empty:
            csone_norm = add_case_lifecycle_fields(csone_data)
            # Round 118 / Build 87: de-fan cross-subscription duplicate
            # case rows so the TAC Lifecycle Snapshot never repeats a Case #
            # (Build 86 audit: WINTRUST 700840277 appeared 3x).
            csone_norm = _r118_dedup_tac_cases(csone_norm)
            bems_mask = detect_bems_mask(csone_norm)
            bems_cases = csone_norm[bems_mask]
            total_bems = len(bems_cases)
            # Round 10 / Phase 4.2: ``break_fix_total`` and
            # ``provisioning_total`` were previously computed across the
            # ENTIRE ``csone_norm`` frame (every TAC case in scope) but
            # then rendered under the BEMS subheading and labelled "TAC
            # Case Type Split". Readers reasonably interpreted those as
            # BEMS-only counts. Restrict to the BEMS subset so the
            # numbers match the surrounding section, matching the
            # principle of least surprise.
            if 'case_type_class' in bems_cases.columns and not bems_cases.empty:
                break_fix_total = int((bems_cases['case_type_class'] == 'break_fix_technical').sum())
                provisioning_total = int((bems_cases['case_type_class'] == 'provisioning_request').sum())
            else:
                break_fix_total = 0
                provisioning_total = 0

            # Group by customer
            if not bems_cases.empty and 'customer_name' in bems_cases.columns:
                # Round 7 / Phase 1.5: normalize customer_name before
                # the groupby so spelling variants ("Acme, Inc.",
                # "ACME INC", "Acme Inc.") collapse into a single
                # bucket the same way every other Round 6/7 surface
                # already does.  Otherwise the BEMS-by-customer
                # rollup over-counts unique customers vs. the
                # dashboard tile.
                _bems_norm = bems_cases.copy()
                _bems_norm['customer_name'] = (
                    _bems_norm['customer_name'].apply(normalize_customer_name)
                )
                bems_by_customer = _bems_norm.groupby('customer_name').size().to_dict()

                # Extract BEMS IDs
                for _, row in bems_cases.iterrows():
                    bems_ids.extend(extract_bems_ids_from_row(row))

        # Summary metrics
        metrics_para = self.doc.add_paragraph()
        metrics_para.add_run('Summary Metrics:\n').bold = True
        metrics_para.add_run(f'• Total BEMS Escalations: ')
        count_run = metrics_para.add_run(f'{total_bems}')
        count_run.bold = True
        if total_bems > 0:
            count_run.font.color.rgb = DANGER_RED
        metrics_para.add_run(
            f" {format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
        )
        metrics_para.add_run(f'\n• Customers Affected: {len(bems_by_customer)}\n')
        metrics_para.add_run(
            # Round 10 / Phase 4.2: relabel + scope clearly to BEMS-only.
            f"• BEMS Case Type Split: break-fix/technical={break_fix_total}, provisioning requests={provisioning_total} "
            f"{format_inline_source('BEMS Escalations', fields=['Case #', 'Title', 'Status'])}\n"
        )
        self.doc.add_paragraph('BEMS Case Type Breakdown', style='Heading 3')
        split_table = self.doc.add_table(rows=3, cols=2)
        split_table.style = 'Light Grid Accent 1'
        split_table.rows[0].cells[0].text = "Case Type"
        split_table.rows[0].cells[1].text = "Count"
        split_table.rows[1].cells[0].text = "Break-fix / Technical"
        split_table.rows[1].cells[1].text = str(break_fix_total)
        split_table.rows[2].cells[0].text = "Provisioning Request"
        split_table.rows[2].cells[1].text = str(provisioning_total)

        if not csone_norm.empty:
            self.doc.add_paragraph('TAC Lifecycle Snapshot (Opened / Closed / Days Open)', style='Heading 3')
            _LIFECYCLE_SAMPLE_LIMIT = 20
            # Round 12 / Phase 9.2: previously this snapshot took
            # ``csone_norm.head(20)`` over an arbitrary upstream sort,
            # so two consecutive runs of the same report could surface
            # different "first 20" cases in the lifecycle table -- the
            # truncation disclosure footer became factually unstable
            # because there was no canonical 20.  Sort by opened-date
            # descending (most recent first, the natural lifecycle
            # snapshot semantic) with a stable identifier tie-break
            # so the rendered slice is deterministic and reproducible.
            try:
                _date_col = next(
                    (c for c in ('open_date', 'Date/Time Opened', 'OpenedDate') if c in csone_norm.columns),
                    None,
                )
                _id_col = next(
                    (c for c in ('Case #', 'SR Number', 'CaseNumber', 'case_id') if c in csone_norm.columns),
                    None,
                )
                if _date_col is not None and _id_col is not None:
                    _sorted = csone_norm.sort_values(
                        [_date_col, _id_col],
                        ascending=[False, True],
                        na_position='last',
                        kind='mergesort',
                    )
                elif _date_col is not None:
                    _sorted = csone_norm.sort_values(
                        _date_col,
                        ascending=False,
                        na_position='last',
                        kind='mergesort',
                    )
                elif _id_col is not None:
                    _sorted = csone_norm.sort_values(
                        _id_col,
                        ascending=True,
                        na_position='last',
                        kind='mergesort',
                    )
                else:
                    _sorted = csone_norm
            except Exception:  # Round 12 / Phase 9.2 defensive
                _sorted = csone_norm
            sample = _sorted.head(_LIFECYCLE_SAMPLE_LIMIT)
            _total_for_lifecycle = len(csone_norm)
            lifecycle_table = self.doc.add_table(rows=len(sample) + 1, cols=7)
            lifecycle_table.style = 'Light Grid Accent 1'
            headers = ["Case #", "Customer", "Status", "Opened", "Closed", "Days Open", "Type"]
            for idx, header_text in enumerate(headers):
                lifecycle_table.rows[0].cells[idx].text = header_text
            for ridx, (_, row) in enumerate(sample.iterrows(), 1):
                lifecycle_table.rows[ridx].cells[0].text = str(row.get('Case #', row.get('SR Number', 'N/A')))
                # Round 11 / Phase 3.4: normalize the customer cell
                # so the TAC lifecycle table agrees with the BEMS
                # rollup / dashboard tile.
                _raw_cust = row.get('customer_name', row.get('Customer Name', 'N/A'))
                try:
                    _norm_cust = normalize_customer_name(str(_raw_cust)) or str(_raw_cust)
                except Exception:
                    _norm_cust = str(_raw_cust)
                lifecycle_table.rows[ridx].cells[1].text = _norm_cust
                lifecycle_table.rows[ridx].cells[2].text = str(row.get('case_status_norm', row.get('Status', 'N/A')))
                lifecycle_table.rows[ridx].cells[3].text = str(row.get('open_date', row.get('Date/Time Opened', 'N/A')))
                lifecycle_table.rows[ridx].cells[4].text = str(row.get('closed_date', 'N/A'))
                # Round 44 / Phase 1: route Days Open through the
                # NaN-safe helper so the cell never renders the
                # literal string "nan" when ``open_age_days`` is
                # missing/NaN.  Backfills from open/closed dates.
                lifecycle_table.rows[ridx].cells[5].text = _format_open_age_days(row)
                # Round 121 / G4b: relabel the bare "unknown" sentinel to
                # "Unclassified" at the display layer (underlying
                # case_type_class is untouched).
                lifecycle_table.rows[ridx].cells[6].text = _r121_display_case_type(
                    row.get('case_type_class', 'unknown')
                )
            # Truncation disclosure: tell the reader when only a sample is shown
            # so the table never silently under-reports lifecycle coverage.
            if _total_for_lifecycle > _LIFECYCLE_SAMPLE_LIMIT:
                _trunc_para = self.doc.add_paragraph()
                _trunc_run = _trunc_para.add_run(
                    f"Showing {_LIFECYCLE_SAMPLE_LIMIT} of {_total_for_lifecycle} cases "
                    "(table truncated; full set available in raw exports)."
                )
                _trunc_run.italic = True

        if bems_ids:
            metrics_para.add_run(f'• BEMS IDs: ')
            # Round 48 / F-COMP-BEMS-MD-LEAK: emit comma-separated BEMS
            # IDs WITHOUT square brackets.  Pre-Round 48 the renderer
            # emitted "[BEMS01916938], [BEMS01952872], ..." which Word
            # readers (and downstream markdown tooling that some
            # operators paste these into) interpret as the head of an
            # unfinished markdown link `[text](url)`.  The brackets
            # stay in the briefing book the LLM ingests (see
            # adoptiq_backend.py briefing builders) because the LLM
            # uses them as citation anchors -- but never in the final
            # rendered Word output.
            ids_run = metrics_para.add_run(', '.join(str(bid) for bid in bems_ids))
            ids_run.font.color.rgb = CISCO_GRAY

        # Customer breakdown
        if bems_by_customer:
            self.doc.add_paragraph()
            breakdown_para = self.doc.add_paragraph()
            breakdown_para.add_run('BEMS Escalations by Customer:\n').bold = True

            # FIXED: Show ALL customers with BEMS escalations
            # Round 13 / Phase 9.3: previously this rendered each
            # ``customer`` raw via ``add_run(f'• {customer}: ')`` and
            # sorted by count alone (``key=lambda x: x[1]``).  Two bugs:
            #   1. A customer name carrying an XML-illegal control code
            #      (zero-width space, tab, surrogate from a Snowflake
            #      mojibake row) silently broke the .docx; Word would
            #      refuse to open it without "repair".
            #   2. Count-only sort produced unstable output: two
            #      customers with the same escalation count flipped
            #      order between runs / between Python builds, so
            #      diffing two compact reports showed phantom changes.
            # Route names through the local ``_r13_safe_doc_text``
            # helper and add a stable secondary tie-break by
            # case-folded customer name (mirrors Phase 6.7's compact
            # formatter rule).
            _r13_bems_sorted = sorted(
                bems_by_customer.items(),
                key=lambda x: (-int(x[1] or 0), str(x[0]).casefold()),
            )
            for customer, count in _r13_bems_sorted:
                _r13_cust = _r13_safe_doc_text(customer, max_len=200)
                breakdown_para.add_run(f'• {_r13_cust}: ')
                _r13_count_text = _r13_safe_doc_text(
                    f'{int(count or 0)} escalation(s)', max_len=80
                )
                count_run = breakdown_para.add_run(_r13_count_text)
                count_run.font.color.rgb = DANGER_RED if count > 1 else WARNING_ORANGE
                breakdown_para.add_run('\n')
        else:
            no_bems = self.doc.add_paragraph()
            no_bems.add_run('✅ No BEMS escalations detected in this analysis period - positive indicator.')

    def add_software_defects_section(self, software_defects: Dict):
        """Add Software Defects section - BST/CSC IDs extracted from data"""
        header = self.doc.add_heading('Software Defects (BST/CSC IDs)', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = DANGER_RED

        defect_count = software_defects.get('total_defects', 0)
        cases_with_defects = software_defects.get('total_cases_with_defects', 0)

        summary_para = self.doc.add_paragraph()
        summary_para.add_run(f'Total Software Defects Identified: ').bold = True
        summary_para.add_run(f'{defect_count} unique BST/CSC defects found in {cases_with_defects} support cases.')

        defect_by_customer = software_defects.get('defect_by_customer', {})
        if defect_by_customer:
            self.doc.add_paragraph()
            # Round 16 / Phase 5.2: substitute the Round-15 banded
            # top-N helper.  Same data shape (per-customer defect
            # rollup), but the helper provides the Cisco-blue header
            # treatment and alternating-row fill that the rest of the
            # Round-15 surfaces use, so the executive-intelligence
            # report no longer carries the ``Light Grid Accent 1``
            # outlier table.
            _r16_defect_headers = ["Customer Name", "Defect Count", "Defect IDs"]
            _r16_defect_rows = []
            for customer, defects in sorted(defect_by_customer.items()):
                defect_list = sorted(set(defects or []))
                try:
                    _disp_cust = normalize_customer_name(str(customer)) or str(customer)
                except Exception:
                    _disp_cust = str(customer)
                _r16_defect_rows.append([
                    _disp_cust,
                    str(len(defect_list)),
                    ", ".join([f"[{d}]" for d in defect_list]),
                ])
            table = _r16_add_banded_top_n_table(
                self.doc,
                headers=_r16_defect_headers,
                rows=_r16_defect_rows,
            )
            if table is None:
                # Defensive fallback: if helper unavailable, keep the
                # legacy Light-Grid-Accent build alive so the report
                # still surfaces the per-customer defect rollup.
                table = self.doc.add_table(rows=len(_r16_defect_rows) + 1, cols=3)
                table.style = 'Light Grid Accent 1'
                headers = table.rows[0].cells
                for i, txt in enumerate(_r16_defect_headers):
                    headers[i].text = txt
                    if headers[i].paragraphs and headers[i].paragraphs[0].runs:
                        headers[i].paragraphs[0].runs[0].bold = True
                for r_idx, row_vals in enumerate(_r16_defect_rows, 1):
                    row = table.rows[r_idx].cells
                    for c_idx, v in enumerate(row_vals):
                        row[c_idx].text = v

    def add_psirt_vulnerabilities_section(self, psirt_vulns: Dict):
        """Add PSIRT Vulnerabilities section - CVEs and PSIRT advisories extracted from data"""
        header = self.doc.add_heading('Security Vulnerabilities (CVEs & PSIRT)', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = DANGER_RED

        vuln_count = psirt_vulns.get('total_vulnerabilities', 0)
        cve_ids = psirt_vulns.get('cve_ids', set())
        psirt_advisories = psirt_vulns.get('psirt_advisories', set())

        summary_para = self.doc.add_paragraph()
        summary_para.add_run(f'Total Vulnerability References: ').bold = True
        # Round 10 / Phase 4.3: ``vuln_count`` (``total_vulnerabilities``)
        # counts every CVE / PSIRT *occurrence* across cases (duplicates
        # included), while ``cve_ids`` / ``psirt_advisories`` are
        # de-duplicated sets of distinct identifiers. The previous label
        # ("Total Vulnerabilities Identified: 47 (12 CVEs, 8 PSIRT
        # advisories)") read as if 47 = 12 + 8 + something, which it
        # never does and confused executives reading the section. Make
        # the relationship explicit so readers can reconcile the two
        # numbers without having to read the source code.
        summary_para.add_run(
            f'{vuln_count} reference(s) across cases / extracts; '
            f'{len(cve_ids)} distinct CVEs and {len(psirt_advisories)} distinct PSIRT advisories.'
        )

        if cve_ids:
            self.doc.add_paragraph()
            cve_para = self.doc.add_paragraph()
            cve_para.add_run('CVE IDs: ').bold = True
            cve_para.add_run(', '.join([f'[{cve}]' for cve in sorted(cve_ids)]))

        if psirt_advisories:
            self.doc.add_paragraph()
            psirt_para = self.doc.add_paragraph()
            psirt_para.add_run('PSIRT Advisories: ').bold = True
            psirt_para.add_run(', '.join([f'[{psirt}]' for psirt in sorted(psirt_advisories)]))

        vulnerability_by_customer = psirt_vulns.get('vulnerability_by_customer', {})
        if vulnerability_by_customer:
            self.doc.add_paragraph()
            # Round 7 / Phase 1.6: iterate via explicit ``sorted(...)``
            # so the rendered order is deterministic across runs (Python
            # dict insertion order is stable per-process but depends on
            # upstream join ordering).  The sibling defects block
            # already does the same; bring this section into parity so
            # diffs across two consecutive runs of the same data don't
            # show spurious row-order changes.
            for customer, vulns in sorted(
                vulnerability_by_customer.items(), key=lambda _kv: str(_kv[0]).lower()
            ):
                vuln_list = sorted(set(vulns))
                customer_para = self.doc.add_paragraph()
                customer_para.add_run(f'• {customer}: ').bold = True
                customer_para.add_run(', '.join([f'[{v}]' for v in vuln_list]))

    def add_known_defects_section(
        self,
        ext_bugs: List = None,
        intel_truncated: Optional[Dict[str, Any]] = None,
        intel_fetch_limit: Optional[int] = None,
    ):
        """Add Known Software Defects section - CRITICAL for matching example reports.

        Round 30 / M2: when the upstream ``incident_storage`` fetch hit
        ``ADOPTIQ_INTEL_LIST_LIMIT`` for bugs, append a truncation
        disclosure right next to the summary line so a reader cannot
        mistake a capped sample (e.g. "newest 1,000 of N") for the
        complete population.
        """
        header = self.doc.add_heading('Known Software Defects (help.webex.com)', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = WARNING_ORANGE

        intro = self.doc.add_paragraph()
        intro.add_run("Known software defects from help.webex.com that may be impacting portfolio customers:")

        if ext_bugs and len(ext_bugs) > 0:
            # Summary
            summary_para = self.doc.add_paragraph()
            summary_para.add_run('Total Known Defects: ').bold = True
            summary_para.add_run(f'{len(ext_bugs)}')
            # Round 30 / M2: truncation disclosure for the bugs list.
            try:
                _bugs_truncated = bool(
                    isinstance(intel_truncated, dict)
                    and intel_truncated.get('bugs')
                )
            except Exception:  # noqa: BLE001
                _bugs_truncated = False
            if _bugs_truncated:
                _trunc_para = self.doc.add_paragraph()
                if intel_fetch_limit and isinstance(intel_fetch_limit, int) and intel_fetch_limit > 0:
                    _msg = (
                        f"Table truncated; fetch limit reached -- shown {len(ext_bugs)} "
                        f"of most-recent {intel_fetch_limit} rows; older rows omitted."
                    )
                else:
                    _msg = (
                        f"Table truncated; fetch limit reached -- shown {len(ext_bugs)} "
                        "most-recent rows; older rows omitted."
                    )
                _trunc_run = _trunc_para.add_run(_msg)
                _trunc_run.italic = True

            # FIXED: Display ALL defects for complete visibility
            self.doc.add_paragraph()
            for bug in ext_bugs:
                bug_id = bug.get('bug_id', 'Unknown')
                title = bug.get('title', 'No description available')
                source = bug.get('source_url', '')

                bug_para = self.doc.add_paragraph()
                # Format defect ID for easy citation and verification
                bug_run = bug_para.add_run(f'• [{bug_id}]: ')
                bug_run.bold = True
                bug_run.font.color.rgb = CISCO_BLUE
                bug_para.add_run(title)
                if source:
                    bug_para.add_run(f'\n  Source: {source}').font.size = Pt(9)
        else:
            # Phase 3.2: route the "no defects" copy through the
            # tristate classifier so a fetch failure is rendered as
            # "unavailable" instead of "no defects detected".
            from report_utils import classify_data_state, render_empty_state_message
            _state = classify_data_state(ext_bugs)
            no_defects = self.doc.add_paragraph()
            if _state == "present":
                no_defects.add_run('✅ No publicly documented software defects detected affecting this portfolio.')
            else:
                no_defects.add_run(render_empty_state_message(
                    _state, source_label='Software defects feed (help.webex.com)'
                ))

    def add_service_incidents_section(
        self,
        ext_incidents: List = None,
        intel_truncated: Optional[Dict[str, Any]] = None,
        intel_fetch_limit: Optional[int] = None,
    ):
        """Add Service Incidents section from status.webex.com.

        Round 30 / M2: thread the storage-layer ``list_truncated``
        flag for incidents into the Word output so the reader sees
        an explicit "table truncated" disclosure when the upstream
        feed returned a capped sample.
        """
        header = self.doc.add_heading('Recent Service Incidents', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = WARNING_ORANGE

        intro = self.doc.add_paragraph()
        intro.add_run("Recent service incidents from status.webex.com that may have impacted portfolio customers:")

        if ext_incidents and len(ext_incidents) > 0:
            # Round 4 / Phase 5.5: surface "served from local cache" so
            # readers know the live status.webex feed was unreachable
            # for this run and the section reflects the SQLite cache.
            try:
                _cache_only = (
                    bool(ext_incidents[0].get('_served_from_local_cache'))
                    or bool(
                        (ext_incidents[-1].get('_window_meta') or {})
                        .get('served_from_local_cache')
                    )
                )
            except Exception:
                _cache_only = False
            if _cache_only:
                cache_para = self.doc.add_paragraph()
                cache_run = cache_para.add_run(
                    "[Notice] These incidents were served from the local cache because the live "
                    "status.webex feed was unreachable for this run. Treat freshness "
                    "with caution; counts and IDs reflect the most recent successful poll."
                )
                cache_run.bold = True

            # Summary
            summary_para = self.doc.add_paragraph()
            summary_para.add_run('Total Incidents: ').bold = True
            summary_para.add_run(f'{len(ext_incidents)}')
            if _cache_only:
                summary_para.add_run(' (cached)')

            # Round 30 / M2: truncation disclosure for incidents.
            try:
                _inc_truncated = bool(
                    isinstance(intel_truncated, dict)
                    and intel_truncated.get('incidents')
                )
            except Exception:  # noqa: BLE001
                _inc_truncated = False
            if _inc_truncated:
                _trunc_para = self.doc.add_paragraph()
                if intel_fetch_limit and isinstance(intel_fetch_limit, int) and intel_fetch_limit > 0:
                    _msg = (
                        f"Table truncated; fetch limit reached -- shown {len(ext_incidents)} "
                        f"of most-recent {intel_fetch_limit} rows; older rows omitted."
                    )
                else:
                    _msg = (
                        f"Table truncated; fetch limit reached -- shown {len(ext_incidents)} "
                        "most-recent rows; older rows omitted."
                    )
                _trunc_run = _trunc_para.add_run(_msg)
                _trunc_run.italic = True

            # FIXED: Display ALL incidents for complete visibility
            self.doc.add_paragraph()
            for incident in ext_incidents:
                inc_id = incident.get('id', incident.get('pub_id', 'Unknown'))
                title = incident.get('title', 'No description')
                date = incident.get('published', incident.get('date', 'Unknown date'))

                inc_para = self.doc.add_paragraph()
                inc_run = inc_para.add_run(f'• {inc_id}: ')
                inc_run.bold = True
                inc_para.add_run(f'{title} ({date})')
        else:
            # Phase 3.2: tristate empty-state classification
            from report_utils import classify_data_state, render_empty_state_message
            _state = classify_data_state(ext_incidents)
            no_incidents = self.doc.add_paragraph()
            if _state == "present":
                no_incidents.add_run('✅ No significant service incidents detected in this analysis period.')
            else:
                no_incidents.add_run(render_empty_state_message(
                    _state, source_label='Service incidents feed (status.webex.com)'
                ))

    def add_recommendations_section(self, ai_insights: Dict[str, Any]):
        """Add strategic recommendations section.

        Round 3 / Phase 2.1: previously this method accepted
        ``ai_insights`` and never read it; the same five canned
        bullets shipped under the "Strategic Recommendations"
        header regardless of model output, sitting next to real
        AI sections. Now we prefer model-derived recommendations
        when present and explicitly label the canned playbook
        list as static when we fall back.
        """
        header = self.doc.add_heading('Strategic Recommendations', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE

        ai_recs: List[str] = []
        if isinstance(ai_insights, dict):
            for key in (
                "recommendations",
                "strategic_recommendations",
                "next_steps",
                "actions",
            ):
                _v = ai_insights.get(key)
                if isinstance(_v, list):
                    ai_recs.extend([str(x).strip() for x in _v if str(x).strip()])
                elif isinstance(_v, str) and _v.strip():
                    ai_recs.append(_v.strip())
            # Some pipelines nest under portfolio_summary.
            _ps = ai_insights.get("portfolio_summary")
            if isinstance(_ps, dict):
                for key in (
                    "recommendations",
                    "strategic_recommendations",
                    "next_steps",
                ):
                    _v = _ps.get(key)
                    if isinstance(_v, list):
                        ai_recs.extend(
                            [str(x).strip() for x in _v if str(x).strip()]
                        )

        if ai_recs:
            for rec in ai_recs:
                para = self.doc.add_paragraph(style='List Bullet')
                run = para.add_run(rec)
                run.font.name = 'Segoe UI'
                run.font.size = Pt(11)
            return

        label_para = self.doc.add_paragraph()
        label_run = label_para.add_run(
            "Static playbook (no AI-generated recommendations available for this run):"
        )
        label_run.italic = True
        label_run.font.name = 'Segoe UI'
        label_run.font.size = Pt(10)

        recommendations = [
            "Prioritize resolution of high-severity adoption barriers",
            "Implement proactive outreach for high-risk customers",
            "Schedule executive business reviews for customers with BEMS escalations",
            "Develop targeted success plans for moderate-risk accounts",
            "Monitor renewal dates and initiate early engagement strategy",
        ]

        for rec in recommendations:
            para = self.doc.add_paragraph(style='List Bullet')
            run = para.add_run(rec)
            run.font.name = 'Segoe UI'
            run.font.size = Pt(11)

    def add_data_citations_section(self, ab_data: pd.DataFrame, csone_data: pd.DataFrame):
        """Add Data Citations section – uses canonical data sources (same across all AdoptIQ reports)."""
        header = self.doc.add_heading('Report Data Sources', level=1)
        if header.runs:
            header.runs[0].font.color.rgb = CISCO_BLUE

        intro = self.doc.add_paragraph()
        intro.add_run("All data in this report is traceable. Same canonical sources used across all AdoptIQ reports.")

        try:
            from report_utils import get_data_sources_paragraph_text, get_data_sources_list
        except ImportError:
            get_data_sources_paragraph_text = lambda: (
                'Adoption Barriers: CSConsole/Snowflake. Support Cases: CSOne (TAC). '
                'BEMS: CSOne. Service Incidents: status.webex.com. Software Defects: help.webex.com. '
                'Customer Pulse, Action Plans, Success Priorities: CSConsole.'
            )
            get_data_sources_list = lambda: [
                ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID'),
                ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number in CSOne'),
                ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'BEMS IDs verifiable in CSOne'),
                ('Service Incidents', 'status.webex.com', 'Public RSS feed'),
                ('Software Defects', 'help.webex.com, CSC/BST refs', 'Defect IDs verifiable'),
                ('Customer Pulse, Action Plans, Success Priorities', 'CSConsole', 'By customer in CSConsole'),
            ]
        sources_para = self.doc.add_paragraph()
        sources_para.add_run(get_data_sources_paragraph_text())
        self.doc.add_paragraph()
        rec_para = self.doc.add_paragraph()
        rec_para.add_run("Record counts in this run:").bold = True
        # Round 10 / Phase 4.4: spell out the unit on each line so a
        # reader can answer "is this rows in the source export, distinct
        # cases, or distinct customers?" without having to open the
        # source code. Previously several rows said "records" (which is
        # ambiguous between rows and entities) and others didn't say
        # anything at all (BEMS / RSS / AI). Make the units explicit and
        # mark known-non-numeric rows as such.
        _csone_rows = len(csone_data) if csone_data is not None and not csone_data.empty else 0
        _ab_rows = len(ab_data) if ab_data is not None and not ab_data.empty else 0
        citations = [
            f"• CSOne TAC Cases: {_csone_rows} row(s) (each row = one TAC case in scope window)",
            f"• Adoption Barriers: {_ab_rows} row(s) (each row = one adoption-barrier task in scope window)",
            "• BEMS Escalations: extracted from CSOne Transaction ID / bemscsc_refs (counted per BEMS ID, not per case)",
            "• Service Incidents: status.webex.com RSS feed (counted per published incident entry)",
            "• AI Insights: CircuIT AI narrative grounded in the above sources (no independent counts)",
        ]
        for cite in citations:
            cite_para = self.doc.add_paragraph()
            cite_para.add_run(cite)
            cite_para.paragraph_format.left_indent = Inches(0.25)
        self.doc.add_paragraph()
        note = self.doc.add_paragraph()
        note.add_run("🔗 Verifiable IDs: ").bold = True
        note.add_run("All [BEMSxxxxxxxx], [CSCxxxxxxx], and TAC case numbers can be validated in their source systems.")

    def save(self, filepath: str = None):
        """Save the document"""
        save_path = filepath or self.output_path
        if save_path:
            # Round 68 / Build 42 (A1): stamp the build label in the
            # executive intelligence Word report's section footer so an
            # auditor can spot a stale-binary report at a glance.  Helper
            # is internally defensive (logs at debug on failure).
            try:
                from _r68_build_label import apply_word_footer as _r68_apply_word_footer  # noqa: PLC0415
                _r68_apply_word_footer(self.doc)
            except Exception as _r68_err:  # noqa: BLE001
                import logging as _r68_logging
                # Round 73 / Phase 1 (F1): promoted to warning so the
                # next missing-footer regression surfaces in the admin
                # error log instead of hiding under the default debug
                # threshold.
                _r68_logging.getLogger(__name__).warning(
                    "Round 68 / A1: executive_intelligence word footer skipped: %s", _r68_err,
                )
            self.doc.save(save_path)
            # Round 9 / Phase 6.4: ``save_path`` is host-absolute and on
            # shipped desktop installs embeds the operator's home /
            # OneDrive root + customer folder slugs.  Surface only the
            # basename at INFO; full path stays at DEBUG for local
            # troubleshooting (parity with app_simple Phase 1.1).
            try:
                import os as _os_p64
                _save_basename = _os_p64.path.basename(str(save_path))
            except Exception:
                _save_basename = ''
            logger.info(f"Executive Intelligence Report saved (file={_save_basename})")
            logger.debug(f"Executive Intelligence Report saved to (verbose): {save_path}")
            return save_path
        else:
            raise ValueError("No output path specified")


def create_executive_intelligence_report(analysis_id: str, manager: str, technology: str, days: int,
                                        ab_data: pd.DataFrame, csone_data: pd.DataFrame,
                                        ai_insights: Dict[str, Any], ext_bugs: List = None,
                                        ext_incidents: List = None, risk_scores: Dict = None,
                                        risk_summary: Dict = None, output_path: str = None,
                                        arr_data: pd.DataFrame = None, arr_impact: Dict = None,
                                        chart_paths: List[str] = None, feature_requests: Dict = None,
                                        team_subs_df: pd.DataFrame = None,
                                        csconsole_action_plans: pd.DataFrame = None,
                                        csconsole_customer_pulse: pd.DataFrame = None,
                                        csconsole_success_priorities: pd.DataFrame = None,
                                        csconsole_adoption_barriers: pd.DataFrame = None,
                                        software_defects: Dict = None, psirt_vulns: Dict = None,
                                        partial_data_warnings: List[Dict[str, Any]] = None,
                                        data_retrieved_at: Optional[datetime] = None,
                                        strict_mode: bool = False,
                                        intel_truncated: Optional[Dict[str, Any]] = None,
                                        intel_fetch_limit: Optional[int] = None) -> str:
    """
    Create an Executive Intelligence Report.

    Args:
        analysis_id: Unique analysis identifier
        manager: Manager name
        technology: Technology focus
        days: Analysis period in days
        ab_data: Adoption barriers DataFrame
        csone_data: CSOne cases DataFrame
        ai_insights: AI-generated insights dictionary
        ext_bugs: External bugs list (optional)
        ext_incidents: External incidents list (optional)
        risk_scores: Risk scores dictionary (optional)
        risk_summary: Risk summary dictionary (optional)
        output_path: Output file path
        chart_paths: Chart image paths (optional)
        team_subs_df: Subscription roster (optional, used for customer counting)
        partial_data_warnings: Per-source fetch warning rows (optional)
        data_retrieved_at: Timestamp of upstream fetch (optional)
        strict_mode: When True, missing canonical inputs raise instead of
            silently degrading (Round 7 / Phase 1.8).

    Round 7 / Phase 1.1: ``arr_data``, ``arr_impact``, and
    ``feature_requests`` remain in the signature for backward
    compatibility with existing callers (notably ``app_simple.py``),
    but they are intentionally **NOT** wired into the EI Word output --
    the executive intelligence report derives ARR / impact / feature
    context from the canonical helpers and the AI insights payload, not
    from these standalone frames.  Passing non-None values for them
    used to give callers a false sense of coverage; the function now
    emits a WARNING when that happens so the discrepancy is visible
    instead of silent.  If a future audit decides to render these in
    the EI doc, do it explicitly here -- do not just remove the
    warning.

    Returns:
        Path to the saved report
    """
    # Round 7 / Phase 1.8: when ``strict_mode=True`` the caller has
    # opted into "fail loud on missing canonical inputs" semantics
    # (matches the renewal/leader strict-mode contract added in Round
    # 6 / Phase 1.5).  Validate the canonical inputs the EI report
    # cannot meaningfully render without, and raise rather than
    # silently fall through to a placeholder report.
    if strict_mode:
        _missing = []
        if ab_data is None:
            _missing.append("ab_data")
        if csone_data is None:
            _missing.append("csone_data")
        if not isinstance(ai_insights, (dict, str)) or not ai_insights:
            _missing.append("ai_insights")
        if _missing:
            raise ValueError(
                "Round 7 / Phase 1.8: strict_mode=True but the "
                "executive intelligence report is missing canonical "
                f"inputs: {', '.join(_missing)}.  Refusing to render a "
                "partial executive document."
            )

    # Round 7 / Phase 1.1: surface the "accepted but not rendered"
    # mismatch instead of silently dropping the inputs.
    # Round 13 / Phase 1.9: ``arr_data`` and ``arr_impact`` are now
    # rendered into a small "ARR Exposure" section after the executive
    # summary (multi-currency-safe).  They no longer appear in the
    # _unused_inputs warning list.  ``feature_requests`` continues to
    # be sourced from ai_insights/risk_scores; warn only if it is
    # passed without an outlet.
    _unused_inputs = []
    if feature_requests:
        _unused_inputs.append("feature_requests")
    if _unused_inputs:
        logger.warning(
            "[EI-REPORT] Round 7 / Phase 1.1: caller passed non-empty "
            "values for %s but the EI Word writer does not render those "
            "frames; ARR/feature coverage is sourced from "
            "ai_insights/risk_scores instead.  Either drop these args "
            "from the call site or add an explicit section here.",
            ", ".join(_unused_inputs),
        )

    formatter = ExecutiveIntelligenceFormatter(output_path)

    # Add title page
    formatter.add_title_page(manager, technology, days, data_retrieved_at=data_retrieved_at)

    # Add page break after title
    formatter.doc.add_page_break()

    # Phase 1.3b: render a "Partial Data" banner up front so any reader sees
    # which sources failed to load before they trust any number below. The
    # warning list is the ``{'dataset', 'error', 'kind'}`` shape produced by
    # ``snowflake_prefetch.collect_fetch_warnings``.
    if partial_data_warnings:
        try:
            warn_heading = formatter.doc.add_heading("⚠ Partial Data Warning", level=1)
            # Round 126 / B1: kind-aware preamble via the SSoT classifier so a
            # pure scope-exclusion no longer renders the "failed to load" copy.
            from data_normalization import (
                partial_data_banner_preamble as _r126_banner_preamble,
            )
            formatter.doc.add_paragraph(
                _r126_banner_preamble(partial_data_warnings, report_label="this run")
            )
            for _w in partial_data_warnings:
                _ds = str(_w.get('dataset') or 'unknown')
                _err = str(_w.get('error') or 'unknown error')
                _kind = str(_w.get('kind') or 'runtime')
                formatter.doc.add_paragraph(f"• {_ds} ({_kind}): {_err}", style='List Bullet')
            formatter.doc.add_paragraph("")
        except Exception as _banner_err:
            # Round 7 / Phase 1.10: keep the broad except (we're in a
            # banner that cannot fail the whole report), but log with
            # ``exc_info`` so the underlying type/traceback reaches
            # the operator instead of just the str() rendering.
            logger.warning(
                "Round 7 / Phase 1.10: could not render partial-data "
                "banner (type=%s): %s",
                type(_banner_err).__name__, _banner_err,
                exc_info=True,
            )

    # Add executive dashboard with all data sources for accurate customer counting
    formatter.add_executive_dashboard(ab_data, csone_data, risk_scores or {}, risk_summary or {},
                                     team_subs_df=team_subs_df if team_subs_df is not None else pd.DataFrame(),
                                     csconsole_action_plans=csconsole_action_plans if csconsole_action_plans is not None else pd.DataFrame(),
                                     csconsole_customer_pulse=csconsole_customer_pulse if csconsole_customer_pulse is not None else pd.DataFrame(),
                                     csconsole_success_priorities=csconsole_success_priorities if csconsole_success_priorities is not None else pd.DataFrame(),
                                     csconsole_adoption_barriers=csconsole_adoption_barriers if csconsole_adoption_barriers is not None else pd.DataFrame(),
                                     software_defects=software_defects if software_defects is not None else {'total_defects': 0, 'total_cases_with_defects': 0, 'defect_by_customer': {}},
                                     psirt_vulns=psirt_vulns if psirt_vulns is not None else {'total_vulnerabilities': 0, 'cve_ids': set(), 'psirt_advisories': set(), 'vulnerability_by_customer': {}})

    # Add executive summary
    formatter.add_executive_summary(ai_insights)

    # Round 13 / Phase 1.9: render an "ARR Exposure" section using the
    # ``arr_data`` frame and ``arr_impact`` summary the caller already
    # passes in.  Multi-currency-safe: when the upstream frame mixes
    # currencies we disclose totals_by_currency rather than printing a
    # bogus single-currency headline.  Single-currency portfolios get
    # the resolved currency code prefix instead of a hardcoded "$".
    try:
        # Round 30 / M5: enforce the ARR-frame attrs contract before
        # we consume ``arr_data.attrs`` (or ``arr_impact['is_multi_currency']``)
        # below.  ``_assert_arr_attrs`` warns (or raises in strict mode)
        # when an upstream caller forgot to route ``arr_data`` through
        # ``_normalize_arr_df``, preventing the multi-currency
        # disclosure from silently degrading to single-currency
        # rendering.
        try:
            from adoptiq_backend import _assert_arr_attrs as _r30_assert_arr
            _r30_assert_arr(
                arr_data,
                function_name='executive_intelligence_formatter.arr_exposure',
            )
        except ImportError:
            pass
        _has_arr_data = (
            arr_data is not None
            and getattr(arr_data, "empty", True) is False
        )
        _has_arr_impact = bool(arr_impact)
        if _has_arr_data or _has_arr_impact:
            formatter.doc.add_heading("ARR Exposure", level=1)
            _arr_p = formatter.doc.add_paragraph()
            _ai = arr_impact or {}
            _is_mixed_arr = bool(_ai.get('is_multi_currency'))
            _by_ccy_arr = _ai.get('arr_by_currency') or _ai.get('totals_by_currency') or {}
            _arr_ccy = (str(_ai.get('currency') or '').strip().upper()
                        or 'USD')
            if _is_mixed_arr and isinstance(_by_ccy_arr, dict) and _by_ccy_arr:
                _arr_p.add_run(
                    "Total Portfolio ARR (multi-currency -- not summed across currencies):"
                ).bold = True
                for _ccy_lbl, _amt in sorted(_by_ccy_arr.items()):
                    formatter.doc.add_paragraph(
                        f"  {_ccy_lbl}: {float(_amt or 0):,.2f}",
                        style='List Bullet',
                    )
            else:
                _total_p = (
                    _ai.get('total_portfolio_arr')
                    or _ai.get('total_arr')
                    or 0
                )
                _arr_p.add_run(
                    f"Total Portfolio ARR: {_arr_ccy} {float(_total_p or 0):,.2f}"
                ).bold = True
            if _has_arr_data:
                try:
                    _row_count = int(getattr(arr_data, 'shape', (0, 0))[0])
                except Exception:
                    _row_count = 0
                formatter.doc.add_paragraph(
                    f"ARR records analyzed: {_row_count:,}"
                )
            # Round 30 / I1: surface the concentration "skipped" note
            # adjacent to the multi-currency disclosure so the reader
            # understands why the usual Top-5 / HHI callouts are
            # missing.  ``arr_impact['concentration_note']`` is stamped
            # by ``app_simple.calculate_arr_impact_for_issues`` (and
            # mirrors the backend-supplied ``note`` text from
            # ``derive_portfolio_intelligence``).  The helper returns
            # ``None`` for single-currency portfolios, so a falsy
            # check is sufficient.
            try:
                _r30_conc_note = (
                    (_ai or {}).get('concentration_note')
                    if isinstance(_ai, dict) else None
                )
                if _r30_conc_note and isinstance(_r30_conc_note, str):
                    _r30_note_p = formatter.doc.add_paragraph()
                    _r30_note_run = _r30_note_p.add_run(_r30_conc_note.strip())
                    _r30_note_run.italic = True
            except Exception as _r30_conc_err:  # noqa: BLE001
                logger.warning(
                    "Round 30 / I1: concentration note render failed: %s",
                    _r30_conc_err,
                )
    except Exception as _arr_render_err:
        logger.warning(
            "Round 13 / Phase 1.9: ARR Exposure section render failed: %s",
            _arr_render_err,
        )

    # Add risk analysis
    if risk_scores:
        formatter.add_risk_analysis_section(risk_scores, ab_data, csone_data)

    # Add BEMS escalation analysis (CRITICAL section that was missing)
    formatter.add_bems_escalation_section(csone_data)

    # Add Software Defects section (extracted from data - BST/CSC IDs)
    if software_defects and software_defects.get('total_defects', 0) > 0:
        formatter.add_software_defects_section(software_defects)

    # Add PSIRT Vulnerabilities section (extracted from data - CVEs/PSIRT)
    if psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0:
        formatter.add_psirt_vulnerabilities_section(psirt_vulns)

    # Add Known Defects section (from help.webex.com)
    formatter.add_known_defects_section(
        ext_bugs,
        intel_truncated=intel_truncated,
        intel_fetch_limit=intel_fetch_limit,
    )

    # Add Service Incidents section (from status.webex.com)
    formatter.add_service_incidents_section(
        ext_incidents,
        intel_truncated=intel_truncated,
        intel_fetch_limit=intel_fetch_limit,
    )

    # Add recommendations
    formatter.add_recommendations_section(ai_insights)

    # Round 3 / Phase 1.1: embed any matplotlib chart PNGs the caller
    # generated for this run. Previously ``chart_paths`` was accepted
    # in the signature and documented but never written into the
    # document, so EI Word reports silently shipped without the
    # charts adjacent surfaces (Compact, Comprehensive) embedded.
    if chart_paths:
        try:
            import os as _os
            from docx.shared import Inches as _Inches
            visual_heading_added = False
            for _cp in chart_paths:
                if not _cp:
                    continue
                if not _os.path.exists(_cp):
                    logger.warning(
                        "Chart path missing during EI embed: %s", _cp
                    )
                    continue
                try:
                    if not visual_heading_added:
                        formatter.doc.add_heading("Visual Analysis", level=1)
                        visual_heading_added = True
                    # Round 13 / Phase 9.10: previously the chart PNG
                    # was embedded with no ``docPr`` description.
                    # Screen readers and Word's Accessibility Checker
                    # therefore announced only the temp/file name,
                    # losing all chart context.  Stamp alt text from
                    # the file basename so accessibility tools (and
                    # exported HTML/PDF copies) announce a meaningful
                    # chart label.
                    _r13_picture = formatter.doc.add_picture(_cp, width=_Inches(6.5))
                    try:
                        _r13_basename = _os.path.basename(str(_cp))
                        _r13_chart_title = (
                            _r13_basename.replace('_', ' ')
                            .replace('-', ' ')
                            .rsplit('.', 1)[0]
                            .strip()
                            .title()
                        ) or 'Visual Analysis Chart'
                        _r13_alt = (
                            f"{_r13_chart_title}: chart embedded in the "
                            "Visual Analysis section of this report."
                        )
                        _r13_inline = getattr(_r13_picture, '_inline', None)
                        if _r13_inline is not None:
                            try:
                                _r13_doc_pr = _r13_inline.docPr
                                _r13_doc_pr.set('descr', _r13_alt)
                                if not _r13_doc_pr.get('title'):
                                    _r13_doc_pr.set('title', _r13_chart_title[:120])
                            except Exception:
                                pass
                            try:
                                _r13_ns = '{http://schemas.openxmlformats.org/drawingml/2006/main}cNvPr'
                                for _r13_cnv in _r13_inline.iter(_r13_ns):
                                    _r13_cnv.set('descr', _r13_alt)
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception as _pic_err:
                    logger.warning(
                        "Could not embed chart %s in EI report: %s",
                        _cp, _pic_err,
                    )
        except Exception as _charts_err:
            logger.warning(
                "Could not render Visual Analysis section: %s", _charts_err
            )

    # Add Data Citations section (NEW - enables data verification)
    formatter.add_data_citations_section(ab_data, csone_data)

    # Enforce source-backed factual claims before saving.
    factual_claims: List[str] = []
    if isinstance(risk_scores, dict):
        for profile in risk_scores.values():
            if isinstance(profile, dict):
                factual_claims.extend(profile.get("risk_factors", []) or [])
                factual_claims.extend(profile.get("key_findings", []) or [])
    ai_claim = ""
    if isinstance(ai_insights, dict):
        ai_claim = (
            ai_insights.get("executive_summary")
            or (ai_insights.get("portfolio_summary") or {}).get("executive_summary")
            or ai_insights.get("raw_response")
            or ""
        )
    elif isinstance(ai_insights, str):
        ai_claim = ai_insights
    if ai_claim:
        factual_claims.append(ai_claim)
    factual_claims = [
        _ensure_inline_source_claim(
            claim,
            "Derived Metric",
            fields=["customer_name", "Case #", "ID", "Severity", "Status"],
        )
        for claim in factual_claims
        if str(claim or "").strip()
    ]
    ab_for_check = ab_data if ab_data is not None else pd.DataFrame()
    csone_for_check = add_case_lifecycle_fields(
        csone_data if csone_data is not None else pd.DataFrame()
    )
    # Round 3 hardening: pass ALL the same multi-source frames the EI
    # dashboard tile uses so ``portfolio_metrics["total_customers"]``
    # matches the headline tile bit-for-bit. Previously this call only
    # passed (ab_df, csone_df) and so subscription-only customers were
    # counted in the dashboard but not in the validator/portfolio total.
    # Round 7 / Phase 1.2: surface a partial-data warning row whenever
    # the account-to-customer lookup cannot be built.  Previously this
    # except branch silently set ``_account_to_customer = {}``, which
    # let the downstream count_customers refinement fall back to the
    # account-map-blind tally and silently disagree with the dashboard
    # / validator parity that this section is supposed to enforce.
    try:
        from app_simple import build_customer_lookup as _build_cust_lookup
        _cust_lookup = _build_cust_lookup(team_subs_df)
        _account_to_customer = _cust_lookup.get("account_to_customer", {}) or {}
    except Exception as _acc_lookup_err:
        _account_to_customer = {}
        logger.warning(
            "Round 7 / Phase 1.2: build_customer_lookup failed; "
            "executive total_customers will fall back to the "
            "account-map-blind count: %s",
            _acc_lookup_err,
        )
        if not isinstance(partial_data_warnings, list):
            partial_data_warnings = []
        partial_data_warnings.append({
            "dataset": "account_to_customer",
            "kind": "lookup_failed",
            "error": "Account-to-customer lookup unavailable; subscription-only customers may be undercounted in totals.",
        })
    _ei_extra_frames = [
        f for f in (
            team_subs_df,
            csconsole_action_plans,
            csconsole_customer_pulse,
            csconsole_success_priorities,
            csconsole_adoption_barriers,
        )
        if f is not None and not (hasattr(f, 'empty') and f.empty)
    ]
    portfolio_metrics = cm.build_portfolio_metrics(
        ab_df=ab_for_check,
        csone_df=csone_for_check,
        risk_profiles=risk_scores if isinstance(risk_scores, dict) else {},
        risk_scale=cm.RISK_SCALE_0_TO_10,
        extra_customer_frames=_ei_extra_frames,
    )
    # Round 101: preserve the report path's declared high-risk scale when
    # this formatter rebuilds portfolio_metrics for the consistency gate.
    # Compact renders the legacy 0-10/color-aware high-risk count; dropping
    # this key made the validator compare that count against the default
    # 0-100 helper and abort otherwise valid live reports.
    if isinstance(risk_summary, dict) and risk_summary.get("high_risk_scale"):
        portfolio_metrics["high_risk_scale"] = risk_summary.get("high_risk_scale")
    # Re-run total_customers via cm.count_customers with the account map
    # because build_portfolio_metrics' count_customers call doesn't
    # expose account_to_customer; route this single value through the
    # canonical helper directly to ensure parity with the dashboard.
    # Phase 3.3: do NOT silently swallow count_customers failures here.
    # That bypassed the canonical helper and let
    # build_portfolio_metrics' (account-map-blind) count win, which
    # disagreed with the leader/renewal dashboards. Surface the failure.
    #
    # Round 25 / Phase A: pin ``total_customers`` to the narrow
    # AB ∪ CSOne ∪ Pulse universe so the EI Word headline and the
    # Excel Summary row both derive from the same call shape.  The
    # wider extras-aware count is preserved as
    # ``portfolio_metrics["total_customers_with_extras"]`` for any
    # downstream consumer (defect linkage / per-section coverage)
    # that legitimately needs it.
    try:
        portfolio_metrics["total_customers_with_extras"] = cm.count_customers(
            ab_df=ab_for_check,
            csone_df=csone_for_check,
            pulse_df=csconsole_customer_pulse,
            extra_frames=_ei_extra_frames,
            account_to_customer=_account_to_customer,
        )
        portfolio_metrics["total_customers"] = cm.count_customers(
            ab_df=ab_for_check,
            csone_df=csone_for_check,
            pulse_df=csconsole_customer_pulse,
        )
    except Exception as _cc_exc:
        logger.error(
            "Canonical count_customers refinement failed for executive report: %s",
            _cc_exc,
            exc_info=True,
        )
        # Mark the metric as unavailable rather than letting the
        # account-map-blind count silently win. Downstream
        # validate_report_consistency already raises on missing
        # totals, which is the correct loud failure mode.
        portfolio_metrics["total_customers_error"] = str(_cc_exc)
        portfolio_metrics.setdefault("partial_data_warnings", []).append(
            f"Total customer count unavailable: {_cc_exc}"
        )
    # Round 22 / R22-001: thread _ei_extra_frames + _account_to_customer
    # through the validator so its customer universe matches portfolio_metrics
    # exactly.  build_portfolio_metrics(extra_customer_frames=_ei_extra_frames)
    # was already passing extras at L1715, but the validator was running
    # AB+CSOne-only -- which meant ANY customer that appears only in a
    # csconsole frame (action plan / pulse / etc.) caused the validator to
    # compute a smaller total_customers than portfolio_metrics, raising
    # "Portfolio metric mismatch" on every realistic dataset.
    consistency = validate_report_consistency(
        ab_for_check,
        csone_for_check,
        portfolio_metrics=portfolio_metrics,
        risk_data=risk_scores if isinstance(risk_scores, dict) else {},
        defects=software_defects if isinstance(software_defects, dict) else {},
        factual_claims=factual_claims,
        extra_frames=_ei_extra_frames or None,
        account_to_customer=_account_to_customer,
        pulse_df=csconsole_customer_pulse,
    )
    if not consistency["is_valid"]:
        raise ValueError(f"Executive consistency checks failed: {'; '.join(consistency['errors'])}")
    if consistency["warnings"]:
        logger.warning("[[CONSISTENCY]] Executive report warnings: %s", consistency["warnings"])

    # Save and return
    return formatter.save(output_path)

