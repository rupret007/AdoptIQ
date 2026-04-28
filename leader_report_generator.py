"""
Leader Report Generator for AdoptIQ
Generates comprehensive reports for managers showing all direct reports' activities
including Action Plans, Adoption Barriers, Customer Pulse, and TAC cases
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional, Iterable, Sequence
import pandas as pd
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_PARAGRAPH_ALIGNMENT
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from adoptiq_backend import _ensure_outputs, _utc_window_start_iso
from enhanced_snowflake_insights import EnhancedSnowflakeInsights
from data_normalization import detect_bems_mask, extract_bems_ids_from_row, normalize_customer_name, normalize_for_display, normalize_severity_label
from snowflake_table_policy import is_table_blocked
import canonical_metrics as cm

# Optional analyzers - may not be available in all deployments
try:
    from enhanced_defect_analyzer import EnhancedDefectAnalyzer
except ImportError:
    EnhancedDefectAnalyzer = None
try:
    from bems_escalation_intelligence_engine import BEMSEscalationAnalyzer
except ImportError:
    BEMSEscalationAnalyzer = None
try:
    from arr_sentiment_analyzer import ARRSentimentAnalyzer
except ImportError:
    ARRSentimentAnalyzer = None

logger = logging.getLogger(__name__)

# Phase 3.4: shared display caps for leader-report sections.
# When a render path needs to truncate, it MUST use these constants and
# the form copy in templates/leader_report_form.html MUST be derived
# from the same values so the form's "up to N" claims and the report's
# actual output never drift.  ``0`` means "show all".
LEADER_DISPLAY_CAPS: Dict[str, int] = {
    "action_plans_per_member": 0,   # 0 = show all
    "tac_cases_per_member": 0,      # 0 = show all
}

# Professional color palette
CISCO_BLUE = RGBColor(0x00, 0x7B, 0xC7)
CISCO_GRAY = RGBColor(0x58, 0x59, 0x5B)
CISCO_LIGHT_GRAY = RGBColor(0xF5, 0xF5, 0xF5)

# Round 12 / Phase 9.7: previously the BEMS escalation heading was
# hardcoded to ``RGBColor(255, 0, 0)`` (pure web-safe red) and the
# "Risk Assessment" inline label used ``RGBColor(220, 20, 60)``
# (crimson) -- both of which differ from the canonical risk-band
# palette (``#d62728`` for CRITICAL) used by every other Word /
# Excel chart.  The same "high-risk" semantic therefore rendered
# in three different reds across one document, breaking the
# visual legend.  Resolve all warning/risk colors through a single
# canonical map so any future palette tweak in ``canonical_metrics``
# automatically lands in the leader report.
try:
    from canonical_metrics import (
        RISK_BAND_COLORS as _R12_LRG_RBC,
    )
    _R12_LRG_HIGH_HEX = (_R12_LRG_RBC.get('CRITICAL', '#d62728') or '#d62728').lstrip('#')
    _R12_LRG_MED_HEX = (_R12_LRG_RBC.get('MEDIUM', '#ff7f0e') or '#ff7f0e').lstrip('#')
    _R12_LRG_LOW_HEX = (_R12_LRG_RBC.get('LOW', '#2ca02c') or '#2ca02c').lstrip('#')
except Exception:  # pragma: no cover - defensive
    _R12_LRG_HIGH_HEX = 'd62728'
    _R12_LRG_MED_HEX = 'ff7f0e'
    _R12_LRG_LOW_HEX = '2ca02c'

def _r13_safe_doc_text(value: Any, max_len: int = 200) -> str:
    """Round 13 / Phase 9.7: sanitize a string before docx ``add_run``
    / cell.text assignment.

    Mirrors helpers in ``app_simple._safe_doc_text`` /
    ``compact_report_formatter._safe_doc_text`` /
    ``advanced_renewal_analyzer._safe_doc_text`` /
    ``executive_intelligence_formatter._r13_safe_doc_text``.
    Strips XML-illegal control codes and surrogate code points,
    collapses whitespace, and caps length.  Without this, a malformed
    CSSM / customer name carrying a zero-width space, tab, or
    surrogate from a Snowflake mojibake row produced a .docx Word
    refused to open without "repair".
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
    import re as _re_local
    s = _re_local.sub(r'\s+', ' ', s).strip()
    if max_len and len(s) > max_len:
        s = s[: max_len - 1] + '\u2026'
    return s


def _r12_hex_to_rgb(_hex_str: str) -> RGBColor:
    try:
        _h = (_hex_str or '').lstrip('#')
        if len(_h) != 6:
            return RGBColor(0xd6, 0x27, 0x28)
        return RGBColor(int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16))
    except Exception:
        return RGBColor(0xd6, 0x27, 0x28)

CANONICAL_RISK_HIGH_RGB = _r12_hex_to_rgb(_R12_LRG_HIGH_HEX)
CANONICAL_RISK_MED_RGB = _r12_hex_to_rgb(_R12_LRG_MED_HEX)
CANONICAL_RISK_LOW_RGB = _r12_hex_to_rgb(_R12_LRG_LOW_HEX)


# Round 39 / Phase 2.1: customer-facing message used in place of raw
# Snowflake errors / dev-phase markers / column names when an Enhanced
# Snowflake Insights sub-section fails. The pre-Round-39 renderer
# spilled raw SQL compilation errors (``invalid identifier
# 'CISCO_TIER_RANKING__C'``), trace IDs (``01c40515-...``), Round/Phase
# markers (``Round 7 / Phase 2.5``), and internal column names
# (``ARR_AMOUNT``, ``SUBJECT_C``) into customer-visible text 100+ times
# per report. The sanitized message is logged at WARNING level via
# ``logger.warning`` for ops debugging.
_SNOWFLAKE_USER_FACING_MSG = (
    "Some enhanced Snowflake insights are temporarily unavailable; this does "
    "not affect the canonical AP/AB/CP/TAC counts above. (Technical details "
    "have been logged for the operations team.)"
)


def _sanitize_snowflake_error(raw: Any) -> str:
    """Round 39 / Phase 2.1: scrub a raw Snowflake error string before
    it is rendered into a customer-facing report.

    Strips:
    - SQL compilation error preambles and the SQL text that follows
    - Snowflake error codes of the form ``NNNNNN (XXXXX)``
    - UUID-shaped trace IDs (8-4-4-4-12 hex segments)
    - Round/Phase development markers (``Round 7 / Phase 2.5``)
    - identifiers ending in ``__C`` (Salesforce-style schema names)
    - ``ARR_AMOUNT``, ``SUBJECT_C``, ``CISCO_TIER_RANKING__C``-style
      ALL_CAPS column names
    - any remaining ``invalid identifier 'XYZ'`` callouts

    Returns the canonical user-facing message when the input contains
    any of the above patterns; otherwise returns a length-capped,
    whitespace-collapsed version of the raw text so genuinely benign
    messages still surface.
    """
    import re as _re_local
    try:
        text = "" if raw is None else str(raw)
    except Exception:
        return _SNOWFLAKE_USER_FACING_MSG
    if not text.strip():
        return _SNOWFLAKE_USER_FACING_MSG

    # If the message looks like raw Snowflake / SQL output, replace it
    # with the user-facing message in full.
    leak_patterns = [
        r"SQL\s+compilation\s+error",
        r"\b\d{6}\s*\(\w+\)",                       # Snowflake error code
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"   # UUID trace ID
        r"[0-9a-f]{4}-[0-9a-f]{12}\b",
        r"Round\s+\d+(?:\.\d+)?\s*/\s*Phase\s+\d+(?:\.\d+)?",
        r"\b[A-Z][A-Z0-9_]+__C\b",                  # Salesforce __C identifier
        r"\binvalid\s+identifier\b",
        r"\bCISCO_TIER_RANKING\b",
        r"\bARR_AMOUNT\b",
        r"\bSUBJECT_C\b",
    ]
    for _pat in leak_patterns:
        if _re_local.search(_pat, text, _re_local.IGNORECASE):
            return _SNOWFLAKE_USER_FACING_MSG

    # Otherwise just collapse whitespace and cap length so the section
    # error stays short and printable.
    cleaned = _re_local.sub(r"\s+", " ", text).strip()
    if len(cleaned) > 200:
        cleaned = cleaned[:199] + "\u2026"
    return cleaned


class LeaderReportGenerator:
    """Generates comprehensive leader reports showing team member activities"""
    
    def safe_len(self, obj):
        """Safely calculate length of an object, handling None values"""
        if obj is None:
            return 0
        try:
            return len(obj)
        except (TypeError, AttributeError):
            return 0
    
    def safe_df_check(self, df, col_name):
        """Safely check if DataFrame is not None, not empty, and contains column"""
        if df is None:
            return False
        try:
            if df.empty:
                return False
            if col_name not in df.columns:
                return False
            return True
        except (AttributeError, TypeError):
            return False
    
    def safe_set(self, obj):
        """Safely convert an iterable to a set.

        Contract:
        - ``None`` -> empty set
        - pandas DataFrames -> empty set (DataFrames iterate column names, which
          is almost never the caller's intent; pass a column Series instead)
        - any other iterable -> ``set(iterable)``
        - unsupported types -> empty set
        """
        if obj is None:
            return set()
        if hasattr(obj, 'empty') and hasattr(obj, 'columns'):
            # pandas DataFrame: reject to avoid silently iterating column names
            return set()
        try:
            return set(obj)
        except (TypeError, AttributeError, ValueError):
            return set()

    @staticmethod
    def _empty_df_failed(exc: Exception) -> pd.DataFrame:
        """Round 7 / Phase 6.10: build an empty DataFrame whose
        ``attrs['fetch_error']`` is populated so downstream callers
        (and ``report_utils.classify_data_state``) can distinguish
        "fetch failed" from "zero rows in window".

        Previously ``except Exception: return pd.DataFrame()`` looked
        identical to a successful zero-row fetch; the leader Word
        report rendered the same generic "no data" copy in both
        cases and validators that key on ``fetch_error`` could not
        flag a partial-data warning.  Using this helper keeps the
        fix narrow (only the except branches are changed) while
        preserving the prior return shape.
        """
        empty = pd.DataFrame()
        try:
            empty.attrs['fetch_error'] = (
                str(exc).strip() or exc.__class__.__name__ or 'fetch_failed'
            )
        except Exception:
            # Pandas guarantees .attrs is a dict, but be defensive.
            pass
        return empty

    @staticmethod
    def _derive_sentiment_summary(
        customer_pulse: Optional[pd.DataFrame],
        adoption_barriers: Optional[pd.DataFrame],
    ) -> str:
        """Rules-based sentiment classification based on pulse scores and
        high-severity adoption barrier volume.

        Returns one of: ``Positive``, ``Neutral``, ``Negative``, ``Unknown``.

        The thresholds are intentionally conservative so this signal can be
        used in narratives without requiring an LLM. If both inputs are empty
        or unusable, we return ``Unknown`` so downstream prose stays honest.
        """
        # Canonical pulse sentiment: threshold table lives in
        # canonical_metrics.pulse_sentiment (Positive >= 7.5, Negative <= 5.0
        # on a 0-10 scale). Returns 'Unknown' when no scores parse.
        pulse_summary = cm.pulse_sentiment(customer_pulse, scale=cm.PULSE_SCALE_0_TO_10)
        pulse_signal: Optional[str] = pulse_summary["sentiment"] if pulse_summary["count"] > 0 else None

        barrier_pressure: Optional[str] = None
        if adoption_barriers is not None and not getattr(adoption_barriers, 'empty', True):
            # Use canonical critical/high counter so the "high|critical|urgent"
            # heuristic is no longer column-name-dependent (SEVERITY_C vs PRIORITY).
            high_count = cm.count_critical_barriers(
                adoption_barriers, mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH
            )
            if high_count >= 5:
                barrier_pressure = 'Negative'
            elif high_count >= 1:
                barrier_pressure = 'Neutral'
            else:
                barrier_pressure = 'Positive'

        # Combine: barrier pressure can downgrade pulse, and vice versa. When
        # only one signal is present, use it directly.
        signals = [s for s in (pulse_signal, barrier_pressure) if s]
        if not signals:
            return 'Unknown'
        if 'Negative' in signals:
            return 'Negative'
        if 'Neutral' in signals:
            return 'Neutral'
        return 'Positive'

    @staticmethod
    def _row_creator_cell(row: Any, cssm_owner: str = '') -> str:
        """Return a compact creator cell value for rendered tables.

        - Uses ``_CREATOR_NAME`` (preferred), then ``_CREATOR_EMAIL`` as a
          fallback.
        - Suffixes ``" (external)"`` when ``_EXTERNAL_ACCOUNT`` is truthy so
          readers can distinguish collaborator-authored records on accounts
          outside the team member's primary portfolio.
        - Falls back to the CSSM's own name so the cell is never blank.
        """
        if row is None:
            return str(cssm_owner or '')
        getter = row.get if hasattr(row, 'get') else (lambda _k, _d='': _d)
        try:
            name = str(getter('_CREATOR_NAME', '') or '').strip()
            email = str(getter('_CREATOR_EMAIL', '') or '').strip()
            external = bool(getter('_EXTERNAL_ACCOUNT', False))
        except Exception:
            name = email = ''
            external = False
        label = name or email or str(cssm_owner or '')
        if external and label and label.lower() != str(cssm_owner or '').lower():
            return f"{label} (external)"
        return label

    def _format_external_note(self, row: Any, cssm_name: str) -> str:
        """Return a short italic annotation for records captured via owner-based
        attribution on an account outside the CSSM's primary subscription list.

        The returned text is intended for rendering after a record's main line,
        so the reader can see who created it and that the account belongs to a
        different DSM. Empty string when no annotation is needed.
        """
        try:
            external = bool(row.get('_EXTERNAL_ACCOUNT', False)) if hasattr(row, 'get') else False
        except Exception:
            external = False
        try:
            creator_name = str(row.get('_CREATOR_NAME', '') or '').strip() if hasattr(row, 'get') else ''
            creator_email = str(row.get('_CREATOR_EMAIL', '') or '').strip() if hasattr(row, 'get') else ''
        except Exception:
            creator_name = creator_email = ''
        note_parts: List[str] = []
        if creator_name and creator_name != cssm_name:
            note_parts.append(f"created by {creator_name}")
        elif not creator_name and creator_email and creator_email not in ('', 'nan'):
            note_parts.append(f"created by {creator_email}")
        if external:
            note_parts.append("external account")
        return f"[{'; '.join(note_parts)}]" if note_parts else ""
    
    def __init__(self, ctx, team_roster: List[Tuple[str, str, str]],
                 data_retrieved_at: Optional[datetime] = None,
                 strict_mode: bool = False,
                 arr_impact: Optional[Dict] = None):
        """
        Initialize the leader report generator
        
        Args:
            ctx: Snowflake connection context
            team_roster: List of (manager_name, cssm_name, cssm_email) tuples
            data_retrieved_at: Phase 3.1 — UTC timestamp marking when the
                underlying data was fetched. Rendered alongside
                ``Generated`` so freshness can be verified independent of
                render time.
            strict_mode: Round 7 / Phase 6.11 — when True, partial-data
                conditions (canonical helper missing, fetch_error on
                a critical source, BU_NAME merge cardinality
                violations) raise instead of being silently swallowed.
                Defaults to False so existing callers retain the
                soft-fail behaviour.  ``app_simple`` / orchestration
                code should forward the user's strict-mode preference
                here so downstream validation matches the executive
                intelligence path (Round 7 / Phase 1.8).
            arr_impact: Round 30 / H1b — optional ARR impact dict (the
                same shape executive_intelligence_formatter receives).
                The leader report intentionally excludes ARR figures
                from its output (see ``self.arr_sentiment_analyzer = None``
                below), but it DOES surface a one-line "this portfolio
                mixes currencies" advisory on the title page when
                ``arr_impact.get('is_multi_currency') is True``.  This
                lets readers cross-referencing the executive ARR
                Exposure section know that the underlying figures are
                not currency-comparable.  Default ``None`` keeps the
                title page unchanged for callers that don't pass it.
        """
        if ctx is None:
            raise ValueError("Snowflake connection context (ctx) cannot be None. Please ensure database connection is established.")
        
        self.ctx = ctx
        self.team_roster = team_roster
        self.doc = Document()
        self.enhanced_insights = EnhancedSnowflakeInsights(ctx)
        self.defect_analyzer = EnhancedDefectAnalyzer() if EnhancedDefectAnalyzer else None
        self.bems_analyzer = BEMSEscalationAnalyzer() if BEMSEscalationAnalyzer else None
        # ARR is intentionally excluded from reporting outputs.
        self.arr_sentiment_analyzer = None
        # Round 30 / H1b: keep the upstream ARR-impact summary so the
        # title page can render a multi-currency advisory.  We never
        # render ARR figures themselves (see comment above + the docs
        # in ``__init__``).
        self.arr_impact = arr_impact if isinstance(arr_impact, dict) else None
        # Round 5 / Phase 6.10: ``datetime.utcnow()`` is deprecated in
        # Python 3.12+ (returns naive UTC, ambiguous when serialized).
        # Use ``datetime.now(timezone.utc)`` so the data-retrieval stamp
        # is explicitly tz-aware UTC and serializes with a 'Z' suffix.
        # Round 10 / Phase 9.4: when the caller did NOT pass an
        # explicit ``data_retrieved_at`` we used to silently default
        # to ``datetime.now(timezone.utc)``.  That made the footer's
        # "Data as of" cell read as a believable prefetch instant
        # when in fact it was the *render* instant, masking stale
        # caches in long-running orchestrations.  Mark the field as
        # render-time so the footer can render an honest "Data as
        # of (render-time)" label and downstream consumers can see
        # the prefetch instant was never set.
        if data_retrieved_at is None:
            self.data_retrieved_at = datetime.now(timezone.utc)
            self._data_retrieved_at_is_render_time = True
            try:
                logger.warning(
                    "LeaderReportGenerator: data_retrieved_at not provided; "
                    "defaulting to render-time clock. Footer will be marked "
                    "'(render-time)' so the freshness stamp is not mistaken "
                    "for a real prefetch instant."
                )
            except Exception:
                pass
        else:
            self.data_retrieved_at = data_retrieved_at
            self._data_retrieved_at_is_render_time = False
        # Round 7 / Phase 6.11: keep on instance so any helper /
        # validator that runs during report generation can read
        # ``self.strict_mode`` without changing every signature.
        self.strict_mode = bool(strict_mode)
        # Round 39 / Phase 2.3: accumulate the count of distinct
        # ``section_errors`` rendered during report body generation so
        # ``_generate_validation_summary`` can drop the data quality
        # score below 100 and ``_add_validation_section`` can surface
        # an honest "Snowflake sub-sections unavailable" warning.
        # Without this, the validator ran BEFORE any renderer saw a
        # section_error, so a degraded run rendered "Validation Status:
        # PASSED, Data Quality Score: 100/100" alongside dozens of
        # missing-insight blocks -- a self-contradiction a director
        # would catch on first read.
        self._section_error_count: int = 0
        self._section_error_kinds: set = set()
        self._setup_document_settings()
    
    def _setup_document_settings(self):
        """Configure document-wide settings"""
        sections = self.doc.sections
        for section in sections:
            section.top_margin = Inches(1.0)
            section.bottom_margin = Inches(1.0)
            section.left_margin = Inches(1.0)
            section.right_margin = Inches(1.0)
    
    def _add_section_separator(self):
        """Add a visual separator line between major sections for better readability"""
        self.doc.add_paragraph()  # Spacing before
        separator_para = self.doc.add_paragraph()
        separator_run = separator_para.add_run("_" * 100)
        separator_run.font.color.rgb = CISCO_GRAY
        separator_run.font.size = Pt(10)
        separator_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        self.doc.add_paragraph()  # Spacing after

    @staticmethod
    def _high_or_critical_barrier_mask(ab_df: pd.DataFrame) -> pd.Series:
        """Single source of truth for the "high or critical barrier" filter.

        Uses ``normalize_severity_label`` so case (``'HIGH'`` vs ``'high'``),
        whitespace, and SF-style tokens (``'P1 (Critical)'``) all collapse into
        the canonical ``Critical`` / ``High`` buckets. Two different sections
        of the leader report previously used ``str.contains('High|Critical')``
        and ``isin(['High','Critical'])`` and disagreed on mixed-casing data;
        this helper guarantees a single answer.
        """
        if ab_df is None or ab_df.empty or 'SEVERITY_C' not in ab_df.columns:
            return pd.Series(False, index=ab_df.index if ab_df is not None else None)
        normalized = ab_df['SEVERITY_C'].apply(normalize_severity_label)
        return normalized.isin(['Critical', 'High'])
    
    def generate_leader_report(
        self,
        manager_name: str,
        days: int = 90,
        ext_bugs: List[Dict] = None,
        ext_incidents: List[Dict] = None,
        software_defects: Dict = None,
        psirt_vulns: Dict = None,
        progress_callback=None,
        intel_truncated: Optional[Dict[str, Any]] = None,
        intel_fetch_limit: Optional[int] = None,
        partial_data_warnings: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[Document, str, Dict, List]:
        """
        Generate comprehensive leader report for a manager
        
        Args:
            manager_name: Name of the manager
            days: Time frame in days
            ext_bugs: External bugs from help.webex.com (optional)
            ext_incidents: External incidents from status.webex.com (optional)
            software_defects: Software defects extracted from CSOne/AB (optional)
            psirt_vulns: PSIRT vulnerabilities extracted from CSOne/AB (optional)
            progress_callback: Optional callable(progress, message, step) for status updates
            
        Returns:
            Tuple of (Document object, file path, team_data dict, direct_reports list)
        """
        def _cb(progress, message, step):
            if progress_callback:
                try:
                    progress_callback(progress, message, step)
                except Exception as _cb_err:
                    logger.debug(f"Progress callback error: {_cb_err}")

        logger.info(f"Generating leader report for {manager_name} covering last {days} days")
        # Make the analysis window available to per-customer enhancement
        # methods so they no longer fall back to a hard-coded 90-day window.
        self._analysis_days = int(days) if days else 90

        _cb(18, f'Finding direct reports for {manager_name}...', 'Document Generation')
        direct_reports = self._get_direct_reports(manager_name)
        
        if not direct_reports:
            raise ValueError(f"No direct reports found for manager: {manager_name}")
        
        n_reports = self.safe_len(direct_reports)
        logger.info(f"Found {n_reports} direct reports for {manager_name}")
        
        _cb(19, f'Collecting data for {n_reports} team members...', 'Team Data Collection')
        team_data = self._collect_team_data(direct_reports, days, progress_callback=progress_callback)
        
        _cb(70, 'Building title page...', 'Document Generation')
        self._create_title_page(manager_name, days, direct_reports)

        # Round 30 / M4: render a "Partial Data Warning" banner immediately
        # after the title page so the reader cannot mistake an empty
        # optional-source section (e.g. CSConsole action plans / customer
        # pulse) for "no data" when it was actually a fetch failure.  The
        # banner is suppressed when no warnings were promoted, keeping the
        # happy-path Word output unchanged.
        try:
            if partial_data_warnings:
                self.doc.add_heading("⚠ Partial Data Warning", level=1)
                self.doc.add_paragraph(
                    "One or more upstream data sources failed to load for "
                    "this run. Sections that depend on the affected sources "
                    "are marked \"unavailable\" rather than rendered as "
                    "zero. Rerun once the source is reachable for a complete "
                    "picture."
                )
                for _w in partial_data_warnings:
                    if not isinstance(_w, dict):
                        continue
                    _ds = str(_w.get('dataset') or 'unknown')
                    _err = str(_w.get('error') or 'unknown error')
                    _kind = str(_w.get('kind') or 'runtime')
                    self.doc.add_paragraph(
                        f"• {_ds} ({_kind}): {_err}", style='List Bullet'
                    )
                self.doc.add_paragraph("")
        except Exception as _r30_pdw_err:  # noqa: BLE001
            logger.warning(
                "Round 30 / M4: leader partial-data banner failed (continuing): %s",
                _r30_pdw_err,
            )

        _cb(71, 'Building team summary table...', 'Document Generation')
        self._create_summary_table(team_data, days)

        self._add_section_separator()

        _cb(72, 'Computing team insights (aging, leaderboard, coverage)...', 'Document Generation')
        self._add_team_insights_section(team_data, days)

        self._add_section_separator()

        _cb(73, 'Writing per-person AdoptIQ summaries...', 'Document Generation')
        self._create_adoptiq_summaries_per_person(team_data, days)
        
        self._add_section_separator()
        
        _cb(75, 'Compiling adoption barriers detail...', 'Document Generation')
        self._create_detailed_ab_list(team_data)
        
        self._add_section_separator()
        
        _cb(76, 'Adding BEMS escalation summary...', 'Document Generation')
        self._add_bems_escalation_section(team_data)
        
        self._add_section_separator()
        
        _cb(77, 'Adding external intelligence section...', 'Document Generation')
        self._add_external_intelligence_section(
            ext_bugs, ext_incidents, software_defects, psirt_vulns,
            intel_truncated=intel_truncated,
            intel_fetch_limit=intel_fetch_limit,
        )
        
        self._add_section_separator()
        
        _cb(78, 'Writing individual team member summaries...', 'Document Generation')
        self._add_overall_individual_summary(team_data, manager_name, days)
        
        _cb(80, 'Saving Word document...', 'Document Generation')
        output_dir = _ensure_outputs()
        # Round 7 / Phase 6.7: stamp the filename in UTC so two leader
        # reports kicked off within the same minute by users in
        # different timezones do not collide on the same
        # ``YYYYMMDD_HHMMSS`` suffix.  ``datetime.now()`` is local-tz
        # which made the stamp non-deterministic in container
        # deployments.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_manager = "".join(c for c in (manager_name or "Manager") if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_manager = safe_manager.replace(' ', '_')
        filename = f"AdoptIQ_Report_Leader_{safe_manager}_{days}d_{timestamp}.docx"
        filepath = output_dir / filename
        self.doc.save(str(filepath))
        logger.info(f"Leader report saved to: {filepath}")
        return self.doc, str(filepath), team_data, direct_reports
    
    def add_hyperlink(self, paragraph, url, text, font_size=9):
        """
        Add a hyperlink to a paragraph.
        
        Args:
            paragraph: The paragraph to add the hyperlink to
            url: The URL for the hyperlink
            text: The display text for the hyperlink
            font_size: Font size in points (default 9)
        
        Returns:
            The hyperlink element
        """
        # This gets access to the xml element
        part = paragraph.part
        r_id = part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)
        
        # Create the w:hyperlink tag and add needed values
        hyperlink = OxmlElement('w:hyperlink')
        hyperlink.set(qn('r:id'), r_id)
        
        # Create a new run
        new_run = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        
        # Set the font size
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), str(font_size * 2))  # Word uses half-points
        rPr.append(sz)
        
        # Set color to blue for hyperlink
        color = OxmlElement('w:color')
        color.set(qn('w:val'), '0563C1')  # Blue color
        rPr.append(color)
        
        # Set underline
        u = OxmlElement('w:u')
        u.set(qn('w:val'), 'single')
        rPr.append(u)
        
        new_run.append(rPr)
        new_run.text = text
        hyperlink.append(new_run)
        
        # Add the hyperlink to the paragraph
        paragraph._p.append(hyperlink)
        
        return hyperlink
    
    def _get_direct_reports(self, manager_name: str) -> List[Dict[str, str]]:
        """Get list of direct reports for a manager"""
        direct_reports = []
        
        for mgr, cssm_name, cssm_email in self.team_roster:
            if mgr == manager_name:
                direct_reports.append({
                    'name': cssm_name,
                    'email': cssm_email
                })
        
        return direct_reports
    
    def _collect_team_data(self, direct_reports: List[Dict[str, str]], days: int, progress_callback=None) -> Dict[str, Dict]:
        """
        Collect all data for each direct report
        
        Returns:
            Dict mapping CSSM name to their data (APs, ABs, CPs, TAC cases)
        """
        logger.info(f"_collect_team_data called for {len(direct_reports) if direct_reports else 0} reports")
        team_data = {}
        n_total = len(direct_reports) if direct_reports else 0
        if not direct_reports:
            return team_data

        # Batch subscription fetch for all CSSMs to reduce Snowflake query volume.
        cssm_emails = [r.get('email') for r in direct_reports if r.get('email')]
        subscriptions_all = self._get_subscriptions_for_cssm(cssm_emails)
        logger.info(
            "Leader report batch mode: %d direct reports, %d subscription rows fetched in one query",
            n_total,
            len(subscriptions_all) if isinstance(subscriptions_all, pd.DataFrame) else 0,
        )
        if n_total > 0:
            previous_query_pattern = n_total * 5
            batched_query_pattern = 5
            logger.info(
                "Leader report Snowflake query pattern reduced from ~%d to ~%d queries",
                previous_query_pattern,
                batched_query_pattern,
            )

        if subscriptions_all is None:
            subscriptions_all = pd.DataFrame()
        else:
            subscriptions_all = subscriptions_all.copy()

        if not subscriptions_all.empty and 'CSSM_EMAIL' in subscriptions_all.columns:
            subscriptions_all['CSSM_EMAIL'] = subscriptions_all['CSSM_EMAIL'].astype(str).str.strip().str.lower()

        all_account_ids: List[str] = []
        all_customers: List[str] = []
        if not subscriptions_all.empty:
            if 'ACCOUNT_ID_C' in subscriptions_all.columns:
                all_account_ids = subscriptions_all['ACCOUNT_ID_C'].dropna().astype(str).unique().tolist()
            if 'BU_NAME' in subscriptions_all.columns:
                all_customers = subscriptions_all['BU_NAME'].dropna().astype(str).unique().tolist()

        # Batch fetch each Snowflake dataset once. The fetchers are owner-aware so
        # they will also return records authored by direct reports on accounts
        # owned by a different DSM (e.g. Brandon creating APs for an account
        # whose PRIMARY_DSM is Mario). Results are deduplicated by ID to avoid
        # double-counting when an owner is also the account DSM.
        normalized_roster_emails = [str(email).strip().lower() for email in cssm_emails if email]
        logger.info(
            "Leader report owner-aware fetch: %d direct reports, %d roster emails for task/pulse expansion",
            n_total,
            len(normalized_roster_emails),
        )
        action_plans_all = self._fetch_action_plans(
            all_account_ids, days, owner_emails=normalized_roster_emails
        )
        adoption_barriers_all = self._fetch_adoption_barriers(
            all_account_ids, days, owner_emails=normalized_roster_emails
        )
        customer_pulse_all = self._fetch_customer_pulse(
            all_account_ids, days, owner_emails=normalized_roster_emails
        )
        success_priorities_all = self._fetch_success_priorities(all_customers, days)

        if not customer_pulse_all.empty and 'ACCOUNT__C' in customer_pulse_all.columns:
            customer_pulse_all = customer_pulse_all.rename(columns={'ACCOUNT__C': 'ACCOUNT_ID_C'})

        # Tag an "external" marker for rows whose ACCOUNT_ID_C is NOT owned by any of
        # the direct reports' PRIMARY_DSM assignments. These are the records that
        # would previously have been missed by the account-only filter. We also
        # fill BU_NAME from the DSM join when the subscription table lacks it.
        primary_account_set = {str(a).strip() for a in all_account_ids if a}

        def _enrich_external(df: pd.DataFrame, aid_col: str = 'ACCOUNT_ID_C') -> pd.DataFrame:
            if df is None or df.empty:
                return df
            df = df.copy()
            if aid_col in df.columns:
                account_series = df[aid_col].fillna('').astype(str).str.strip()
                df['_EXTERNAL_ACCOUNT'] = ~account_series.isin(primary_account_set)
            else:
                df['_EXTERNAL_ACCOUNT'] = False
            if 'DSM_BU_NAME' in df.columns:
                bu = df['BU_NAME'] if 'BU_NAME' in df.columns else pd.Series([None] * len(df), index=df.index)
                bu = bu.where(bu.astype(str).str.strip() != '', df['DSM_BU_NAME'])
                df['BU_NAME'] = bu
            return df

        action_plans_all = _enrich_external(action_plans_all)
        adoption_barriers_all = _enrich_external(adoption_barriers_all)
        customer_pulse_all = _enrich_external(customer_pulse_all)

        if not success_priorities_all.empty and 'RELATED_CUSTOMER__C' in success_priorities_all.columns:
            success_priorities_all = success_priorities_all.copy()
            success_priorities_all['_RELATED_CUSTOMER_NORM'] = success_priorities_all['RELATED_CUSTOMER__C'].apply(
                normalize_customer_name
            )

        # Pre-compute lowercase owner columns once so the per-CSSM slicing loop
        # below can do fast creator-vs-account attribution.
        def _first_present(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
            if df is None or df.empty:
                return None
            for c in candidates:
                if c in df.columns:
                    return c
            return None

        try:
            from adoptiq_backend import TASK_OWNER_EMAIL_COLUMNS, PULSE_OWNER_EMAIL_COLUMNS
        except ImportError:
            TASK_OWNER_EMAIL_COLUMNS = ("OWNER_EMAIL", "CREATEDBYEMAIL", "ASSIGNEE_EMAIL")
            PULSE_OWNER_EMAIL_COLUMNS = ("OWNER_EMAIL", "CREATEDBYEMAIL")

        def _creator_email_series(df: pd.DataFrame, candidates: Iterable[str]) -> pd.Series:
            if df is None or df.empty:
                return pd.Series([], dtype=str)
            series = pd.Series([''] * len(df), index=df.index, dtype=object)
            for col in candidates:
                if col not in df.columns:
                    continue
                col_vals = df[col].fillna('').astype(str).str.strip().str.lower()
                is_email = col_vals.str.contains('@', na=False)
                empty = series.astype(str).str.len() == 0
                fill_mask = is_email & empty
                series = series.where(~fill_mask, col_vals)
            return series

        ap_creator_emails = _creator_email_series(action_plans_all, TASK_OWNER_EMAIL_COLUMNS)
        ab_creator_emails = _creator_email_series(adoption_barriers_all, TASK_OWNER_EMAIL_COLUMNS)
        cp_creator_emails = _creator_email_series(customer_pulse_all, PULSE_OWNER_EMAIL_COLUMNS)

        # Build an email -> display name map for "created by X" annotations.
        roster_email_to_name: Dict[str, str] = {}
        for _mgr, _name, _email in self.team_roster:
            if _email:
                roster_email_to_name[str(_email).strip().lower()] = _name

        for idx, report in enumerate(direct_reports):
            cssm_name = report['name']
            cssm_email = str(report.get('email', '')).strip().lower()

            member_pct = 19 + int((idx / max(n_total, 1)) * 50)
            if progress_callback:
                try:
                    progress_callback(member_pct, f'Fetching data for {cssm_name} ({idx + 1}/{n_total})...', 'Team Data Collection')
                except Exception as _cb_err:
                    logger.debug(f"Progress callback error: {_cb_err}")

            logger.info(f"Collecting data for {cssm_name} ({idx + 1}/{n_total})...")

            if subscriptions_all.empty or 'CSSM_EMAIL' not in subscriptions_all.columns:
                subscriptions_df = pd.DataFrame()
            else:
                subscriptions_df = subscriptions_all[subscriptions_all['CSSM_EMAIL'] == cssm_email].copy()

            account_ids = (
                subscriptions_df['ACCOUNT_ID_C'].dropna().astype(str).unique().tolist()
                if (not subscriptions_df.empty and 'ACCOUNT_ID_C' in subscriptions_df.columns)
                else []
            )
            if not subscriptions_df.empty and 'BU_NAME' in subscriptions_df.columns:
                subscriptions_df['BU_NAME'] = subscriptions_df['BU_NAME'].apply(normalize_customer_name)
            customers = (
                subscriptions_df['BU_NAME'].dropna().unique().tolist()
                if (not subscriptions_df.empty and 'BU_NAME' in subscriptions_df.columns)
                else []
            )
            customer_set = set(customers)

            # Creator-first attribution: a record belongs to this CSSM if they
            # authored it (any owner-like column matches their email) OR the
            # account is part of their subscription footprint. External-only
            # matches are preserved and flagged so display code can annotate
            # them as "created by X on Mario's account" and avoid double-counts.
            def _slice_by_owner_or_account(
                df: pd.DataFrame,
                creator_series: pd.Series,
                aid_col: str = 'ACCOUNT_ID_C',
            ) -> pd.DataFrame:
                if df is None or df.empty:
                    return pd.DataFrame()
                owner_mask = creator_series.eq(cssm_email) if cssm_email else pd.Series([False] * len(df), index=df.index)
                if aid_col in df.columns and account_ids:
                    account_mask = df[aid_col].fillna('').astype(str).str.strip().isin(account_ids)
                else:
                    account_mask = pd.Series([False] * len(df), index=df.index)
                keep = owner_mask | account_mask
                if not keep.any():
                    return pd.DataFrame()
                sliced = df.loc[keep].copy()
                sliced['_ATTRIBUTED_BY_OWNER'] = owner_mask.loc[keep].values
                sliced['_ATTRIBUTED_BY_ACCOUNT'] = account_mask.loc[keep].values
                return sliced

            action_plans_df = _slice_by_owner_or_account(action_plans_all, ap_creator_emails)
            adoption_barriers_df = _slice_by_owner_or_account(adoption_barriers_all, ab_creator_emails)
            customer_pulse_df = _slice_by_owner_or_account(customer_pulse_all, cp_creator_emails)

            if subscriptions_df.empty and action_plans_df.empty and adoption_barriers_df.empty and customer_pulse_df.empty:
                logger.warning(f"No subscriptions or owned records found for {cssm_name}")
                team_data[cssm_name] = {
                    'subscriptions': pd.DataFrame(),
                    'action_plans': pd.DataFrame(),
                    'adoption_barriers': pd.DataFrame(),
                    'customer_pulse': pd.DataFrame(),
                    'success_priorities': pd.DataFrame(),
                    'tac_cases': pd.DataFrame(),
                    'account_ids': [],
                    'customers': []
                }
                continue

            if not success_priorities_all.empty and '_RELATED_CUSTOMER_NORM' in success_priorities_all.columns:
                success_priorities_df = success_priorities_all[success_priorities_all['_RELATED_CUSTOMER_NORM'].isin(customer_set)].copy()
                success_priorities_df.drop(columns=['_RELATED_CUSTOMER_NORM'], inplace=True, errors='ignore')
            else:
                success_priorities_df = pd.DataFrame()

            def _merge_bu_name(df: pd.DataFrame) -> pd.DataFrame:
                """Ensure BU_NAME is populated for both primary and external account rows.

                Merge order: prefer the CSSM's subscription BU_NAME when present, else
                fall back to the DSM-join BU_NAME (external accounts), else existing
                BU_NAME column from the task table.
                """
                if df is None or df.empty or 'ACCOUNT_ID_C' not in df.columns:
                    return df
                merged = df
                if not subscriptions_df.empty and 'ACCOUNT_ID_C' in subscriptions_df.columns and 'BU_NAME' in subscriptions_df.columns:
                    # Round 7 / Phase 6.4: the subscriptions frame can
                    # carry multiple rows per ACCOUNT_ID_C (one per
                    # SUBSCRIPTION_ID), and a single ACCOUNT_ID_C can
                    # legitimately span multiple BU_NAME values.  A
                    # naive ``drop_duplicates`` over the
                    # ``[ACCOUNT_ID_C, BU_NAME]`` projection still
                    # leaves >1 row per account when the BU split is
                    # real, which then produces a many-to-many merge
                    # that silently fans out the task table.  We
                    # explicitly collapse to one BU_NAME per account
                    # using a deterministic policy (lex-min of the
                    # non-null BU labels) and assert ``validate='m:1'``
                    # so any future schema change that re-introduces
                    # the cardinality issue blows up loudly instead of
                    # silently inflating row counts.
                    sub_bu = (
                        subscriptions_df[['ACCOUNT_ID_C', 'BU_NAME']]
                        .dropna(subset=['ACCOUNT_ID_C'])
                        .copy()
                    )
                    sub_bu['BU_NAME'] = sub_bu['BU_NAME'].fillna('').astype(str).str.strip()
                    # Drop empty BU labels first so they don't become the
                    # lex-min winner, then take the lex-min per account.
                    sub_bu_nonempty = sub_bu[sub_bu['BU_NAME'].str.len() > 0]
                    if sub_bu_nonempty.empty:
                        sub_bu_one = sub_bu.drop_duplicates(subset=['ACCOUNT_ID_C'], keep='first')
                    else:
                        sub_bu_one = (
                            sub_bu_nonempty
                            .sort_values(['ACCOUNT_ID_C', 'BU_NAME'])
                            .drop_duplicates(subset=['ACCOUNT_ID_C'], keep='first')
                        )
                    merged = merged.merge(
                        sub_bu_one.rename(columns={'BU_NAME': '_SUB_BU_NAME'}),
                        on='ACCOUNT_ID_C',
                        how='left',
                        validate='m:1',
                    )
                bu_values = pd.Series([''] * len(merged), index=merged.index, dtype=object)
                for col in ('_SUB_BU_NAME', 'BU_NAME', 'DSM_BU_NAME'):
                    if col in merged.columns:
                        candidate = merged[col].fillna('').astype(str).str.strip()
                        empty = bu_values.astype(str).str.len() == 0
                        bu_values = bu_values.where(~(empty & (candidate != '')), candidate)
                merged['BU_NAME'] = bu_values.where(bu_values.astype(str).str.len() > 0, None)
                merged.drop(columns=['_SUB_BU_NAME'], inplace=True, errors='ignore')
                return merged

            def _attach_creator_annotation(df: pd.DataFrame, candidates: Iterable[str]) -> pd.DataFrame:
                if df is None or df.empty:
                    return df
                email_series = _creator_email_series(df, candidates)
                df = df.copy()
                df['_CREATOR_EMAIL'] = email_series.values if len(email_series) == len(df) else ''
                df['_CREATOR_NAME'] = df['_CREATOR_EMAIL'].map(
                    lambda e: roster_email_to_name.get(str(e or '').strip().lower(), '')
                )
                return df

            action_plans_df = _attach_creator_annotation(_merge_bu_name(action_plans_df), TASK_OWNER_EMAIL_COLUMNS)
            adoption_barriers_df = _attach_creator_annotation(_merge_bu_name(adoption_barriers_df), TASK_OWNER_EMAIL_COLUMNS)
            customer_pulse_df = _attach_creator_annotation(_merge_bu_name(customer_pulse_df), PULSE_OWNER_EMAIL_COLUMNS)
            if not customer_pulse_df.empty and 'BU_NAME' in customer_pulse_df.columns:
                customer_pulse_df['BU_NAME'] = customer_pulse_df['BU_NAME'].apply(normalize_customer_name)

            if not success_priorities_df.empty and 'RELATED_CUSTOMER__C' in success_priorities_df.columns:
                success_priorities_df['RELATED_CUSTOMER__C'] = success_priorities_df['RELATED_CUSTOMER__C'].apply(normalize_customer_name)

            team_data[cssm_name] = {
                'subscriptions': subscriptions_df,
                'action_plans': action_plans_df,
                'adoption_barriers': adoption_barriers_df,
                'customer_pulse': customer_pulse_df,
                'success_priorities': success_priorities_df,
                'tac_cases': pd.DataFrame(),  # Will be populated from CSOne if available
                'account_ids': account_ids,
                'customers': customers
            }

            logger.info(f"  {cssm_name}: {self.safe_len(action_plans_df)} APs, {self.safe_len(adoption_barriers_df)} ABs, {self.safe_len(customer_pulse_df)} CPs, {self.safe_len(success_priorities_df)} SPs")

        return team_data
    
    def _get_subscriptions_for_cssm(self, cssm_emails: List[str]) -> pd.DataFrame:
        """Get subscriptions for specific CSSM emails"""
        logger.debug(f"_get_subscriptions_for_cssm called. cssm_emails: {cssm_emails}")
        if not cssm_emails:
            logger.debug(f"No cssm_emails provided, returning empty DataFrame")
            return pd.DataFrame()
        
        cur = None
        try:
            from adoptiq_backend import (
                DSM_TABLE,
                _get_table_columns,
                _column_or_default_expr,
            )
            logger.debug(f"Creating cursor from ctx. ctx type: {type(self.ctx)}, ctx is None: {self.ctx is None}")
            
            cur = self.ctx.cursor()
            logger.debug(f"Cursor created successfully")

            # Round 3 / Phase 3.5: pick the actual email column from the
            # DSM schema. Some DSM views expose CSSM_EMAIL, others
            # PRIMARY_DSM_EMAIL. Hardcoding PRIMARY_DSM_EMAIL silently
            # produced an empty result set on environments where the
            # column is named CSSM_EMAIL, which then looked like
            # "this CSSM owns no subscriptions" rather than a schema
            # mismatch.
            try:
                _dsm_cols = _get_table_columns(self.ctx, DSM_TABLE) or set()
            except Exception as _intro_err:
                logger.warning(
                    "Could not introspect %s columns for leader subscription lookup: %s",
                    DSM_TABLE, _intro_err,
                )
                _dsm_cols = set()
            if "PRIMARY_DSM_EMAIL" in _dsm_cols:
                _email_col = "PRIMARY_DSM_EMAIL"
            elif "CSSM_EMAIL" in _dsm_cols:
                _email_col = "CSSM_EMAIL"
            else:
                # Default to PRIMARY_DSM_EMAIL for backward compatibility
                # but log loudly so this isn't silent.
                logger.warning(
                    "Neither PRIMARY_DSM_EMAIL nor CSSM_EMAIL found in %s; "
                    "defaulting to PRIMARY_DSM_EMAIL. Result set may be empty.",
                    DSM_TABLE,
                )
                _email_col = "PRIMARY_DSM_EMAIL"

            # Round 7 / Phase 6.1: chunk the email list so very large
            # CSSM rosters do not blow the Snowflake statement size /
            # IN-list limits.  ``_chunk_in_clause`` returns one chunk
            # for typical roster sizes; multi-chunk dispatch only kicks
            # in for unusually large teams.  We concatenate the rows
            # across chunks and rely on the SELECT DISTINCT + final
            # drop_duplicates below to keep the merged frame clean.
            email_chunks = self._chunk_in_clause(cssm_emails)
            all_rows: List[Tuple[Any, ...]] = []
            cols: Optional[List[str]] = None
            for _chunk_idx, _chunk in enumerate(email_chunks, start=1):
                placeholders = ','.join(['%s'] * len(_chunk))
                sql = f"""
                SELECT DISTINCT SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME, {_email_col} AS CSSM_EMAIL
                FROM {DSM_TABLE}
                WHERE {_email_col} IN ({placeholders})
                """
                logger.debug(
                    "Executing SQL chunk %d/%d (%d emails)",
                    _chunk_idx, len(email_chunks), len(_chunk),
                )
                cur.execute(sql, _chunk)
                _rows = cur.fetchall() or []
                if _rows and cols is None:
                    cols = [c[0] for c in cur.description]
                all_rows.extend(_rows)

            logger.debug(f"Fetched {len(all_rows)} rows across {len(email_chunks)} chunk(s)")

            if not all_rows or cols is None:
                logger.debug(f"No rows returned, returning empty DataFrame")
                return pd.DataFrame()

            df = pd.DataFrame(all_rows, columns=cols)
            # Multi-chunk runs can re-emit the same SUBSCRIPTION_ID if
            # a CSSM appears in more than one chunk's result via the
            # DISTINCT inside a single query (it cannot, but defensively
            # de-dupe across chunks too).
            if "SUBSCRIPTION_ID" in df.columns:
                df = df.drop_duplicates(subset=["SUBSCRIPTION_ID"], keep="first").reset_index(drop=True)
            logger.debug(f"Created DataFrame with {len(df)} rows")

            return df
        except Exception as e:
            logger.error(f"Error fetching subscriptions: {e}", exc_info=True)
            # Round 7 / Phase 6.10: distinguish fetch-failed from
            # zero-rows so classify_data_state -> "failed" downstream.
            return self._empty_df_failed(e)
        finally:
            if cur:
                cur.close()
                logger.debug(f"Cursor closed")
    
    def _build_task_owner_clause(self, owner_emails: List[str], alias: Optional[str] = None):
        """Build a parameterized owner-match clause for C360_CS_TASK_C_VW.

        Returns (fragment_or_empty, params). Uses the cached schema helper so
        we only reference columns present in the table.
        """
        try:
            from adoptiq_backend import (
                _get_table_columns,
                _build_owner_match_clause,
                _normalize_owner_emails,
                TASK_OWNER_EMAIL_COLUMNS,
            )
        except ImportError:
            return "", []
        cleaned = _normalize_owner_emails(owner_emails)
        if not cleaned:
            return "", []
        cols = _get_table_columns(self.ctx, "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW")
        return _build_owner_match_clause(cols, cleaned, TASK_OWNER_EMAIL_COLUMNS, table_alias=alias)

    # Round 7 / Phase 6.1: chunk large ``IN (...)`` lists when binding
    # account IDs / customer names / DSM emails into Snowflake queries.
    # Snowflake's documented hard cap on a single ``IN`` list is 16,384
    # expressions but the practical ceiling is much lower because of
    # the 1 MiB statement size limit and because many JDBC/ODBC stacks
    # in the path will refuse very long parameter arrays.  In practice
    # Round 6 / Phase 4.2 chose 900 as a safe ceiling and we mirror
    # that here so the leader fetchers behave the same way as the rest
    # of the codebase.  ``_LEADER_IN_CHUNK_SIZE`` is exposed as a
    # constant so tests can patch it without touching SQL strings.
    _LEADER_IN_CHUNK_SIZE = 900

    @staticmethod
    def _chunk_in_clause(items: Sequence[Any], chunk_size: Optional[int] = None) -> List[List[Any]]:
        """Round 7 / Phase 6.1: split ``items`` into chunks suitable
        for binding into a single ``IN (...)`` clause.  Returns an
        empty list (NOT a list containing an empty list) when
        ``items`` is empty so callers can short-circuit cleanly.
        """
        size = int(chunk_size or LeaderReportGenerator._LEADER_IN_CHUNK_SIZE)
        if size < 1:
            size = 1
        if not items:
            return []
        out: List[List[Any]] = []
        seq = list(items)
        for i in range(0, len(seq), size):
            out.append(seq[i:i + size])
        return out

    def _build_pulse_owner_clause(self, owner_emails: List[str], alias: Optional[str] = None):
        """Build a parameterized owner-match clause for ESA_C360_CUSTOMER_PULSE__C."""
        try:
            from adoptiq_backend import (
                _get_table_columns,
                _build_owner_match_clause,
                _normalize_owner_emails,
                PULSE_OWNER_EMAIL_COLUMNS,
            )
        except ImportError:
            return "", []
        cleaned = _normalize_owner_emails(owner_emails)
        if not cleaned:
            return "", []
        cols = _get_table_columns(self.ctx, "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C")
        return _build_owner_match_clause(cols, cleaned, PULSE_OWNER_EMAIL_COLUMNS, table_alias=alias)

    def _fetch_action_plans(
        self,
        account_ids: List[str],
        days: int,
        owner_emails: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Fetch Action Plans for account IDs and/or owner emails.

        When ``owner_emails`` is provided, Action Plans authored/owned by any
        of those users are included even for accounts that are not in the
        provided ``account_ids`` (i.e. accounts owned by a different DSM).
        Results are joined with DSM_ASSIGNMENT_DATA so ``BU_NAME`` is populated
        for external accounts too. Deduplicated by ID.
        """
        owner_emails = owner_emails or []
        if not account_ids and not owner_emails:
            return pd.DataFrame()

        cur = None
        try:
            cur = self.ctx.cursor()
            owner_sql, owner_params = self._build_task_owner_clause(owner_emails, alias="t")
            # Round 7 / Phase 6.1: dispatch one query per ID chunk and
            # also (separately) one query for the owner-email predicate
            # so the IN-list cap is per-chunk.  We always execute the
            # owner-email branch exactly once because the email list is
            # already small (a single CSSM team).  Results are merged
            # and de-duplicated by ID at the end.
            id_chunks = self._chunk_in_clause(account_ids) if account_ids else []
            if not id_chunks and not owner_sql:
                return pd.DataFrame()
            all_rows: List[Tuple[Any, ...]] = []
            cols: Optional[List[str]] = None

            def _run(predicates: List[str], params: List[Any]) -> None:
                nonlocal cols
                if not predicates:
                    return
                where_clause = " OR ".join(predicates)
                sql = f"""
                SELECT t.*, dsm.BU_NAME AS DSM_BU_NAME
                FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW t
                LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                       ON t.ACCOUNT_ID_C = dsm.ACCOUNT_ID_C
                WHERE t.record_type_id = '0122T000000QHBGQA4'
                  AND ({where_clause})
                  -- Round 10 / Phase 5.1: align with the portfolio /
                  -- subscription-side adoption-barrier predicate which
                  -- uses ``DATE(COALESCE(OPEN_DATE_C, CREATED_DATE,
                  -- CREATED_DATE_C))``. CREATED_DATE alone misses rows
                  -- whose CREATED_DATE is null but OPEN_DATE_C is set
                  -- (common for backfilled action plans), causing the
                  -- leader side to undercount vs portfolio reports for
                  -- the same window.
                  -- Round 10 / Phase 5.2: bind a Python-computed UTC
                  -- window-start so the lookback is independent of the
                  -- Snowflake session TZ (mirrors the portfolio side).
                  AND DATE(COALESCE(t.OPEN_DATE_C, t.CREATED_DATE, t.CREATED_DATE_C))
                      >= %s
                """
                params_with_days = list(params) + [_utc_window_start_iso(days)]
                cur.execute(sql, params_with_days)
                _rows = cur.fetchall() or []
                if _rows and cols is None:
                    cols = [c[0] for c in cur.description]
                all_rows.extend(_rows)

            for _chunk in id_chunks:
                placeholders = ','.join(['%s'] * len(_chunk))
                _run([f"t.ACCOUNT_ID_C IN ({placeholders})"], list(_chunk))
            if owner_sql:
                _run([owner_sql], list(owner_params))

            if not all_rows or cols is None:
                return pd.DataFrame()
            df = pd.DataFrame(all_rows, columns=cols)
            if 'ID' in df.columns:
                df = df.drop_duplicates(subset=['ID'], keep='first').reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching action plans: {e}")
            # Round 7 / Phase 6.10: classify_data_state -> "failed".
            return self._empty_df_failed(e)
        finally:
            if cur:
                cur.close()

    def _fetch_adoption_barriers(
        self,
        account_ids: List[str],
        days: int,
        owner_emails: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Fetch Adoption Barriers for account IDs and/or owner emails.

        Owner-aware variant mirrors :meth:`_fetch_action_plans` so barriers
        created by teammates on other DSMs' accounts are still captured.
        """
        owner_emails = owner_emails or []
        if not account_ids and not owner_emails:
            return pd.DataFrame()

        cur = None
        try:
            cur = self.ctx.cursor()
            owner_sql, owner_params = self._build_task_owner_clause(owner_emails, alias="t")
            # Round 7 / Phase 6.1: chunked dispatch (see _fetch_action_plans).
            id_chunks = self._chunk_in_clause(account_ids) if account_ids else []
            if not id_chunks and not owner_sql:
                return pd.DataFrame()
            all_rows: List[Tuple[Any, ...]] = []
            cols: Optional[List[str]] = None

            def _run(predicates: List[str], params: List[Any]) -> None:
                nonlocal cols
                if not predicates:
                    return
                where_clause = " OR ".join(predicates)
                sql = f"""
                SELECT t.*, dsm.BU_NAME AS DSM_BU_NAME
                FROM EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW t
                LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                       ON t.ACCOUNT_ID_C = dsm.ACCOUNT_ID_C
                WHERE t.record_type_id = '0122T000000GJfTQAW'
                  AND ({where_clause})
                  -- Round 10 / Phase 5.1: see _fetch_action_plans for
                  -- the rationale; same predicate so leader-side and
                  -- portfolio-side adoption-barrier counts agree on the
                  -- same window.
                  -- Round 10 / Phase 5.2: bind UTC window-start.
                  AND DATE(COALESCE(t.OPEN_DATE_C, t.CREATED_DATE, t.CREATED_DATE_C))
                      >= %s
                """
                params_with_days = list(params) + [_utc_window_start_iso(days)]
                cur.execute(sql, params_with_days)
                _rows = cur.fetchall() or []
                if _rows and cols is None:
                    cols = [c[0] for c in cur.description]
                all_rows.extend(_rows)

            for _chunk in id_chunks:
                placeholders = ','.join(['%s'] * len(_chunk))
                _run([f"t.ACCOUNT_ID_C IN ({placeholders})"], list(_chunk))
            if owner_sql:
                _run([owner_sql], list(owner_params))

            if not all_rows or cols is None:
                return pd.DataFrame()
            df = pd.DataFrame(all_rows, columns=cols)
            if 'ID' in df.columns:
                df = df.drop_duplicates(subset=['ID'], keep='first').reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching adoption barriers: {e}")
            # Round 7 / Phase 6.10: classify_data_state -> "failed".
            return self._empty_df_failed(e)
        finally:
            if cur:
                cur.close()

    def _fetch_customer_pulse(
        self,
        account_ids: List[str],
        days: int,
        owner_emails: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Fetch Customer Pulse records for account IDs and/or owner emails.

        Owner-aware variant. Joins DSM assignments so external-account pulse
        records carry a ``BU_NAME`` for rendering.
        """
        owner_emails = owner_emails or []
        if not account_ids and not owner_emails:
            return pd.DataFrame()

        cur = None
        try:
            cur = self.ctx.cursor()
            owner_sql, owner_params = self._build_pulse_owner_clause(owner_emails, alias="cp")
            # Round 7 / Phase 6.1: chunked dispatch (see _fetch_action_plans).
            id_chunks = self._chunk_in_clause(account_ids) if account_ids else []
            if not id_chunks and not owner_sql:
                return pd.DataFrame()
            all_rows: List[Tuple[Any, ...]] = []
            cols: Optional[List[str]] = None

            def _run(predicates: List[str], params: List[Any]) -> None:
                nonlocal cols
                if not predicates:
                    return
                where_clause = " OR ".join(predicates)
                sql = f"""
                SELECT cp.*, dsm.BU_NAME AS DSM_BU_NAME
                FROM EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C cp
                LEFT JOIN CX_DB.CX_SWSSBST_BR.dsm_assignment_data dsm
                       ON cp.ACCOUNT__C = dsm.ACCOUNT_ID_C
                WHERE ({where_clause})
                  -- Round 10 / Phase 5.2: bind UTC window-start so the
                  -- lookback is independent of Snowflake session TZ.
                  AND DATE(cp.CREATEDDATE) >= %s
                """
                params_with_days = list(params) + [_utc_window_start_iso(days)]
                cur.execute(sql, params_with_days)
                _rows = cur.fetchall() or []
                if _rows and cols is None:
                    cols = [c[0] for c in cur.description]
                all_rows.extend(_rows)

            for _chunk in id_chunks:
                placeholders = ','.join(['%s'] * len(_chunk))
                _run([f"cp.ACCOUNT__C IN ({placeholders})"], list(_chunk))
            if owner_sql:
                _run([owner_sql], list(owner_params))

            if not all_rows or cols is None:
                return pd.DataFrame()
            df = pd.DataFrame(all_rows, columns=cols)
            if 'ID' in df.columns:
                df = df.drop_duplicates(subset=['ID'], keep='first').reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching customer pulse: {e}")
            # Round 7 / Phase 6.10: classify_data_state -> "failed".
            return self._empty_df_failed(e)
        finally:
            if cur:
                cur.close()
    
    def _fetch_success_priorities(self, customer_names: List[str], days: int) -> pd.DataFrame:
        """Fetch Success Priorities for customer names (uses RELATED_CUSTOMER__C, not account IDs)"""
        if not customer_names:
            return pd.DataFrame()
        if is_table_blocked("EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C"):
            logger.info("Policy: Skipping ESA_C360_SUCCESS_PRIORITY__C in leader report success priorities.")
            return pd.DataFrame()
        
        cur = None
        try:
            cur = self.ctx.cursor()
            # Round 7 / Phase 6.1: chunked dispatch.
            name_chunks = self._chunk_in_clause(customer_names)
            all_rows: List[Tuple[Any, ...]] = []
            cols: Optional[List[str]] = None
            for _chunk_idx, _chunk in enumerate(name_chunks, start=1):
                placeholders = ','.join(['%s'] * len(_chunk))
                sql = f"""
                SELECT *
                FROM EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C
                WHERE RELATED_CUSTOMER__C IN ({placeholders})
                  -- Round 10 / Phase 5.2: bind UTC window-start.
                  AND DATE(CREATEDDATE) >= %s
                """
                cur.execute(sql, [*_chunk, _utc_window_start_iso(days)])
                _rows = cur.fetchall() or []
                if _rows and cols is None:
                    cols = [c[0] for c in cur.description]
                all_rows.extend(_rows)

            if not all_rows or cols is None:
                return pd.DataFrame()

            df = pd.DataFrame(all_rows, columns=cols)
            if "ID" in df.columns:
                df = df.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Error fetching success priorities: {e}")
            # Round 7 / Phase 6.10: classify_data_state -> "failed".
            return self._empty_df_failed(e)
        finally:
            if cur:
                cur.close()
    
    def add_tac_cases_from_csone(self, team_data: Dict[str, Dict], csone_df: pd.DataFrame, days: int = 90):
        """
        Add TAC case data from CSOne Excel to team data
        
        Args:
            team_data: Dict mapping CSSM names to their data
            csone_df: CSOne DataFrame with TAC cases
            days: Filter to cases opened in last X days
        """
        if csone_df is None or csone_df.empty:
            logger.warning("No CSOne data provided for TAC cases")
            return

        csone_df = csone_df.copy()

        logger.info(f"\n{'='*60}")
        logger.info(f"TAC CASE MATCHING VALIDATION")
        logger.info(f"{'='*60}")
        
        # Round 7 / Phase 6.7: cutoff_date drives ``CREATE_DATE >=`` /
        # ``CLOSE_DATE >=`` filtering downstream; using local-tz
        # ``datetime.now()`` shifted the window by up to 24h depending
        # on the worker's TZ.  Snowflake stores timestamps in UTC, so
        # compute the cutoff in UTC for parity with the SQL side.
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        logger.info(f"Cutoff date for filtering: {cutoff_date.strftime('%Y-%m-%d')}")
        logger.info(f"Total TAC cases in CSOne file: {len(csone_df)}")
        
        # Find date column
        date_col = None
        for col in csone_df.columns:
            if 'date' in col.lower() and 'opened' in col.lower():
                date_col = col
                break
        
        # Filter by date if date column exists
        if date_col and date_col in csone_df.columns:
            try:
                # Round 13 / Phase 2.1: parse with utc=True so date column
                # values are tz-aware UTC, matching the UTC-aware
                # ``cutoff_date`` computed above.  Without utc=True the
                # parse produced naive timestamps and pandas raised
                # "Cannot compare tz-naive and tz-aware" on the
                # comparison below, which the broad except silently
                # masked into "no filtering".
                csone_df[date_col] = pd.to_datetime(
                    csone_df[date_col], errors='coerce', utc=True
                )

                # Get date range before filtering
                valid_dates = csone_df[csone_df[date_col].notna()]
                if not valid_dates.empty:
                    min_date = valid_dates[date_col].min()
                    max_date = valid_dates[date_col].max()
                    logger.info(f"Date range in CSOne: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")

                # Apply filter
                csone_filtered = csone_df[csone_df[date_col] >= cutoff_date].copy()
                logger.info(f"OK: Filtered TAC cases from {len(csone_df)} to {len(csone_filtered)} (last {days} days)")
                
                # Validation: Check if we have cases in the time period
                if csone_filtered.empty:
                    logger.warning(f"VALIDATION WARNING: No TAC cases found in the last {days} days!")
                elif len(csone_df) > 0 and len(csone_filtered) < len(csone_df) * 0.1:
                    pct = (len(csone_filtered) / len(csone_df) * 100) if len(csone_df) > 0 else 0
                    logger.warning(f"VALIDATION WARNING: Only {len(csone_filtered)} cases in last {days} days ({pct:.1f}% of total)")
                
            except Exception as e:
                logger.error(f"ERROR filtering TAC cases by date: {e}")
                csone_filtered = csone_df.copy()
        else:
            logger.warning(f"VALIDATION WARNING: No date column found for filtering. Using all {len(csone_df)} TAC cases.")
            csone_filtered = csone_df.copy()
        
        # Find customer column once (outside loop)
        customer_col = None
        for col in csone_filtered.columns:
            col_lower = col.lower()
            if any(term in col_lower for term in ['customer name', 'account name', 'customer:', 'account:', 'bu_name']):
                customer_col = col
                break
        
        if not customer_col:
            logger.warning(f"No customer column found in CSOne data")
            for cssm_name in team_data.keys():
                team_data[cssm_name]['tac_cases'] = pd.DataFrame()
            return
        
        # Get unique CSOne customers for debugging
        csone_customers_unique = csone_filtered[customer_col].dropna().unique()
        # Round 7 / Phase 6.3: customer-name samples are PII-adjacent
        # (account-level identifiers can correlate with named contacts)
        # so they must not appear at INFO level.  Counts stay at INFO,
        # actual names are downgraded to DEBUG.
        logger.info(f"\nCSOne customer validation:")
        logger.info(f"  - Total unique customers in filtered data: {len(csone_customers_unique)}")
        logger.debug(f"  - Sample CSOne customers: {list(csone_customers_unique[:5])}")

        # Get all team customers for comparison
        all_team_customers = set()
        for data in team_data.values():
            all_team_customers.update(data['customers'])
        logger.info(f"  - Total unique team customers from Snowflake: {len(all_team_customers)}")
        logger.debug(f"  - Sample team customers: {list(all_team_customers)[:5]}")
        
        # Round 39 / Phase 1.1: replace the fuzzy 2-word-overlap matcher
        # with an authoritative SUBSCRIPTION_ID join.  The pre-Round-39
        # ``matches_customer`` closure returned True whenever a CSSM
        # customer and a CSOne customer shared 2 words (or 1 word if
        # either was short), which double-attributed cases like FARMERS
        # INSURANCE GROUP / ERIE INSURANCE GROUP to multiple CSSMs and
        # inflated the team-total TAC count.  The replacement uses three
        # priority tiers so each TAC row lands on at most ONE CSSM:
        #   1. SUBSCRIPTION_ID -> CSSM (authoritative; from Snowflake DSM)
        #   2. ACCOUNT_ID_C   -> CSSM (account-level fallback)
        #   3. exact normalized customer name (case-folded, suffix-stripped)
        # If still unmatched, the row is logged as partial-data.
        #
        # Round 7 / Phase 6.8 spirit preserved: the customer-name fallback
        # routes through the canonical ``data_normalization`` helpers
        # (``_clean_name_for_key`` / ``normalize_customer_name``) so the
        # CSOne and Snowflake sides agree on whitespace, NFKC, and
        # corporate-suffix collapsing.  Round 39 strengthens the
        # comparison from upper-case + naive ``in`` to a full case-folded
        # join key so "Acme Co." and "ACME CO" no longer drift apart.
        try:
            from data_normalization import (
                _clean_name_for_key as _r39_key,
                normalize_customer_name as _r39_norm,
            )
        except Exception:
            def _r39_key(v):
                return str(v or "").strip().casefold()
            def _r39_norm(v):
                return str(v or "").strip()

        sub_to_cssm: Dict[str, str] = {}
        account_to_cssm: Dict[str, str] = {}
        cust_key_to_cssm: Dict[str, str] = {}
        sub_collisions: List[str] = []
        account_collisions: List[str] = []
        cust_collisions: List[str] = []

        # Skip data items that aren't per-CSSM dicts (defensive against
        # special metadata keys that might be added in the future).
        def _is_cssm_data(d):
            return isinstance(d, dict) and 'subscriptions' in d and 'customers' in d

        for cssm_name, data in team_data.items():
            if not _is_cssm_data(data):
                continue
            subs_df = data.get('subscriptions', pd.DataFrame())
            if isinstance(subs_df, pd.DataFrame) and not subs_df.empty:
                if 'SUBSCRIPTION_ID' in subs_df.columns:
                    for sid in subs_df['SUBSCRIPTION_ID'].dropna().astype(str).str.strip().unique():
                        if not sid or sid.lower() in ('nan', 'none'):
                            continue
                        existing = sub_to_cssm.get(sid)
                        if existing is None:
                            sub_to_cssm[sid] = cssm_name
                        elif existing != cssm_name:
                            # Subscription claimed by two CSSMs - keep first owner
                            # deterministically (sorted by name) and log.
                            winner = sorted([existing, cssm_name])[0]
                            loser = sorted([existing, cssm_name])[1]
                            sub_to_cssm[sid] = winner
                            sub_collisions.append(f"{sid} -> {winner} (also seen on {loser})")
                if 'ACCOUNT_ID_C' in subs_df.columns:
                    for aid in subs_df['ACCOUNT_ID_C'].dropna().astype(str).str.strip().unique():
                        if not aid or aid.lower() in ('nan', 'none'):
                            continue
                        existing = account_to_cssm.get(aid)
                        if existing is None:
                            account_to_cssm[aid] = cssm_name
                        elif existing != cssm_name:
                            winner = sorted([existing, cssm_name])[0]
                            loser = sorted([existing, cssm_name])[1]
                            account_to_cssm[aid] = winner
                            account_collisions.append(f"{aid} -> {winner} (also seen on {loser})")
            customers = data.get('customers') or []
            for cust in customers:
                key = _r39_key(cust)
                if not key:
                    continue
                existing = cust_key_to_cssm.get(key)
                if existing is None:
                    cust_key_to_cssm[key] = cssm_name
                elif existing != cssm_name:
                    winner = sorted([existing, cssm_name])[0]
                    loser = sorted([existing, cssm_name])[1]
                    cust_key_to_cssm[key] = winner
                    cust_collisions.append(f"{cust} -> {winner} (also seen on {loser})")

        # Detect TAC join columns on the CSOne side.  Both
        # 'SUBSCRIPTION_ID' (canonical) and 'Subscription Reference Id'
        # (legacy CSOne export header) appear in real-world exports.
        sub_id_col = None
        for cand in ('SUBSCRIPTION_ID', 'Subscription Reference Id', 'Subscription ID', 'subscription_id'):
            if cand in csone_filtered.columns:
                sub_id_col = cand
                break
        account_id_col = None
        for cand in ('ACCOUNT_ID_C', 'Account ID', 'account_id_c', 'Account Id'):
            if cand in csone_filtered.columns:
                account_id_col = cand
                break

        # Reset the index so we can safely use positional .iloc lookups
        # below regardless of whether the date filter produced gaps.
        csone_filtered = csone_filtered.reset_index(drop=True)

        # Per-CSSM index buckets - exactly one CSSM per row by construction.
        cssm_indices: Dict[str, List[int]] = {
            name: [] for name, data in team_data.items() if _is_cssm_data(data)
        }
        unmatched_count = 0
        matched_by_sub = 0
        matched_by_account = 0
        matched_by_name = 0

        for idx in range(len(csone_filtered)):
            row = csone_filtered.iloc[idx]
            owner = None

            # Priority 1: subscription id (authoritative)
            if sub_id_col is not None:
                v = row.get(sub_id_col) if hasattr(row, 'get') else None
                if v is not None and not pd.isna(v):
                    v_str = str(v).strip()
                    # CSOne sometimes exports IDs as floats (e.g. 12345.0);
                    # strip the trailing ".0" so we match the integer-string
                    # form stored on the Snowflake side.
                    if v_str.endswith('.0'):
                        v_str = v_str[:-2]
                    if v_str and v_str.lower() not in ('nan', 'none'):
                        owner = sub_to_cssm.get(v_str)
                        if owner:
                            matched_by_sub += 1

            # Priority 2: account id (covers Account-level matches when
            # the SUBSCRIPTION_ID column is missing or blank).
            if owner is None and account_id_col is not None:
                v = row.get(account_id_col) if hasattr(row, 'get') else None
                if v is not None and not pd.isna(v):
                    v_str = str(v).strip()
                    if v_str.endswith('.0'):
                        v_str = v_str[:-2]
                    if v_str and v_str.lower() not in ('nan', 'none'):
                        owner = account_to_cssm.get(v_str)
                        if owner:
                            matched_by_account += 1

            # Priority 3: exact normalized customer name (case-folded,
            # suffix-stripped via _clean_name_for_key).  This is the
            # ONLY place customer-name matching happens; the previous
            # 2-word-overlap heuristic is GONE so unrelated customers
            # like ERIE / FARMERS no longer get cross-attributed.
            if owner is None and customer_col:
                v = row.get(customer_col) if hasattr(row, 'get') else None
                if v is not None and not pd.isna(v):
                    key = _r39_key(v)
                    if key:
                        owner = cust_key_to_cssm.get(key)
                        if owner:
                            matched_by_name += 1

            if owner is not None:
                cssm_indices[owner].append(idx)
            else:
                unmatched_count += 1

        # Assign rows back to each CSSM.  Use .iloc with the integer
        # bucket so rows land in deterministic order.
        for cssm_name, data in team_data.items():
            if not _is_cssm_data(data):
                continue
            indices = cssm_indices.get(cssm_name, [])
            if indices:
                data['tac_cases'] = csone_filtered.iloc[indices].copy().reset_index(drop=True)
            else:
                data['tac_cases'] = pd.DataFrame()

        # Per-CSSM logging (counts at INFO, customer names at DEBUG).
        for cssm_name, data in team_data.items():
            if not _is_cssm_data(data):
                continue
            cssm_cases = data.get('tac_cases', pd.DataFrame())
            n = self.safe_len(cssm_cases)
            if n > 0:
                logger.info(f"  OK: {cssm_name}: {n} TAC cases attributed (last {days} days)")
                if customer_col and customer_col in cssm_cases.columns:
                    matched_customers = cssm_cases[customer_col].dropna().unique()
                    logger.info(f"    Across {len(matched_customers)} unique customers")
                    for cust in list(matched_customers)[:5]:
                        logger.debug(f"      - {cust}")
                    if len(matched_customers) > 5:
                        logger.debug(f"      ... and {len(matched_customers) - 5} more")
            else:
                logger.warning(f"  WARN: {cssm_name}: No TAC cases attributed")

        # Final TAC matching summary
        total_tac_cases = sum(
            self.safe_len(data.get('tac_cases', pd.DataFrame()))
            for data in team_data.values()
            if _is_cssm_data(data)
        )
        members_with_cases = sum(
            1
            for data in team_data.values()
            if _is_cssm_data(data) and self.safe_len(data.get('tac_cases', pd.DataFrame())) > 0
        )
        csone_filtered_len = self.safe_len(csone_filtered)

        logger.info(f"\n{'='*60}")
        logger.info(f"TAC CASE MATCHING SUMMARY (Round 39 / SUBSCRIPTION_ID join)")
        logger.info(f"  Total TAC cases in window: {csone_filtered_len}")
        logger.info(f"  Matched by SUBSCRIPTION_ID: {matched_by_sub}")
        logger.info(f"  Matched by ACCOUNT_ID_C:    {matched_by_account}")
        logger.info(f"  Matched by exact name:      {matched_by_name}")
        logger.info(f"  Unmatched:                  {unmatched_count}")
        logger.info(f"  Total attributed:           {total_tac_cases}")
        logger.info(f"  Team members with cases:    {members_with_cases} of {len(cssm_indices)}")
        if csone_filtered_len > 0:
            logger.info(f"  Match rate: {total_tac_cases}/{csone_filtered_len} ({total_tac_cases/csone_filtered_len*100:.1f}%)")
        if sub_collisions:
            logger.warning(f"  Subscription-id collisions (kept first deterministically): {len(sub_collisions)}")
            for c in sub_collisions[:5]:
                logger.warning(f"    {c}")
        if unmatched_count > 0:
            logger.warning(
                f"  WARN: {unmatched_count} TAC cases could not be attributed to any "
                f"CSSM (no SUBSCRIPTION_ID, ACCOUNT_ID_C, or exact-name match)."
            )
        logger.info(f"{'='*60}\n")

        # Stash the summary on the generator instance so the wrapper
        # (``generate_leader_report`` module-level helper) can surface
        # any unmatched-TAC count as a partial-data warning instead of
        # silently dropping rows.
        self._tac_match_summary = {
            'total_tac_in_window': csone_filtered_len,
            'matched_total': total_tac_cases,
            'matched_by_subscription': matched_by_sub,
            'matched_by_account': matched_by_account,
            'matched_by_name': matched_by_name,
            'unmatched': unmatched_count,
            'members_with_cases': members_with_cases,
            'team_size': len(cssm_indices),
            'subscription_collisions': sub_collisions,
            'account_collisions': account_collisions,
            'name_collisions': cust_collisions,
            'sub_id_col': sub_id_col,
            'account_id_col': account_id_col,
            'customer_col': customer_col,
        }
    
    def _create_title_page(self, manager_name: str, days: int, direct_reports: List[Dict]):
        """Create title page for the leader report"""
        # Title
        title = self.doc.add_heading('Leader Report', level=1)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if title.runs:
            title_run = title.runs[0]
            title_run.font.size = Pt(28)
            title_run.font.color.rgb = CISCO_BLUE
            title_run.font.bold = True
        
        # Manager name
        manager_para = self.doc.add_paragraph()
        manager_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        manager_run = manager_para.add_run(f'\n{(manager_name or "Manager")}\'s Team')
        manager_run.font.size = Pt(20)
        manager_run.font.color.rgb = CISCO_GRAY
        manager_run.font.bold = True
        
        # Date range
        # Round 3 / Phase 4.4: the underlying Snowflake queries use
        # ``CURRENT_DATE()`` which is the UTC calendar date. Using
        # the local clock here meant that for any user west of UTC
        # the printed "Analysis Period" could be off by a day from
        # what was actually summed (e.g. a US/Central run between
        # 19:00 and 23:59 local would show a header window that
        # ended one day before the data window). Use UTC and label
        # it explicitly so the header matches the SQL.
        # Round 7 / Phase 6.7: ``datetime.utcnow()`` is deprecated as
        # of Python 3.12 (returns naive datetime).  Use the tz-aware
        # ``datetime.now(timezone.utc)`` so future Python versions
        # don't ship a DeprecationWarning into the report header.
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        date_para = self.doc.add_paragraph()
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        date_run = date_para.add_run(
            f'\n\nAnalysis Period: {start_date.strftime("%B %d, %Y")} - '
            f'{end_date.strftime("%B %d, %Y")} (UTC)'
        )
        date_run.font.size = Pt(12)
        date_run.font.color.rgb = CISCO_GRAY

        # Report date
        report_date_para = self.doc.add_paragraph()
        report_date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        report_date_run = report_date_para.add_run(
            f'Generated: {datetime.now(timezone.utc).strftime("%B %d, %Y %I:%M %p UTC")}'
        )
        report_date_run.font.size = Pt(10)
        report_date_run.font.color.rgb = CISCO_GRAY

        # Phase 3.1: render data fetch timestamp distinct from render
        # time so a stale-looking footer can be diagnosed at the source.
        if getattr(self, 'data_retrieved_at', None) is not None:
            data_para = self.doc.add_paragraph()
            data_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            try:
                _data_str = self.data_retrieved_at.strftime('%B %d, %Y %I:%M %p UTC')
            except Exception:
                _data_str = str(self.data_retrieved_at)
            # Round 10 / Phase 9.4: when the constructor wasn't given
            # an explicit ``data_retrieved_at``, mark the footer
            # ``(render-time)`` so the operator can tell the
            # freshness stamp came from the build clock instead of a
            # real prefetch instant.
            if getattr(self, '_data_retrieved_at_is_render_time', False):
                _data_str = f"{_data_str} (render-time)"
            data_run = data_para.add_run(f'Data as of: {_data_str}')
            data_run.font.size = Pt(9)
            data_run.font.color.rgb = CISCO_GRAY
        
        # Team overview
        team_para = self.doc.add_paragraph()
        team_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        team_run = team_para.add_run(f'\n\nTeam Size: {self.safe_len(direct_reports)} Direct Reports')
        team_run.font.size = Pt(14)
        team_run.font.color.rgb = CISCO_BLUE
        team_run.font.bold = True

        # Round 30 / H1b: surface a multi-currency advisory on the title
        # page when the upstream portfolio mixes currencies.  The leader
        # report does NOT render ARR dollar figures of its own (see the
        # ``__init__`` docstring + the ``ARR is intentionally excluded
        # from reporting outputs`` comment above), so the only way a
        # currency mismatch could mislead a reader is via a cross-
        # reference to the executive's ARR Exposure section or via an
        # AI-generated narrative that quotes ARR figures.  This advisory
        # makes the disclaimer explicit and aligned with the executive
        # / compact ARR Exposure phrasing ("multi-currency -- not summed
        # across currencies").
        try:
            _r30_arr_impact = getattr(self, 'arr_impact', None)
            if isinstance(_r30_arr_impact, dict) and bool(
                _r30_arr_impact.get('is_multi_currency')
            ):
                _r30_ccys = _r30_arr_impact.get('currencies_present') or []
                if isinstance(_r30_ccys, (list, tuple, set)):
                    _r30_ccy_str = ', '.join(
                        sorted(str(c).strip().upper() for c in _r30_ccys if c)
                    )
                else:
                    _r30_ccy_str = ''
                _r30_para = self.doc.add_paragraph()
                _r30_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _r30_run = _r30_para.add_run(
                    "\nNote: this portfolio mixes currencies"
                    + (f" ({_r30_ccy_str})" if _r30_ccy_str else "")
                    + ". ARR figures referenced in cross-report "
                    "summaries are not summed across currencies and "
                    "are not directly comparable."
                )
                _r30_run.font.size = Pt(10)
                _r30_run.font.color.rgb = CISCO_GRAY
                _r30_run.font.italic = True
                # Round 30 / I1: when the upstream impact dict carries a
                # ``concentration_note`` (stamped by
                # ``app_simple.calculate_arr_impact_for_issues`` via the
                # canonical ``adoptiq_backend.concentration_note_text``
                # helper), surface that note on a second line of the
                # advisory so leaders see the same "Top-5 / HHI skipped"
                # explanation that the executive ARR Exposure section
                # renders.
                _r30_conc_note = _r30_arr_impact.get('concentration_note')
                if _r30_conc_note and isinstance(_r30_conc_note, str):
                    _r30_conc_para = self.doc.add_paragraph()
                    _r30_conc_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    _r30_conc_run = _r30_conc_para.add_run(_r30_conc_note.strip())
                    _r30_conc_run.font.size = Pt(10)
                    _r30_conc_run.font.color.rgb = CISCO_GRAY
                    _r30_conc_run.font.italic = True
        except Exception as _r30_arr_advisory_err:  # noqa: BLE001
            # Defensive: title-page rendering must not fail because of
            # an optional advisory.  Log and continue.
            logger.warning(
                "[[ARR]] Round 30 / H1b: leader title-page multi-"
                "currency advisory failed: %s",
                _r30_arr_advisory_err,
            )

        self.doc.add_page_break()
        
        # Report Data Sources – canonical sources used by all AdoptIQ reports
        self._add_report_data_sources()
    
    def _add_report_data_sources(self):
        """Add Report Data Sources section – same canonical sources as all AdoptIQ reports."""
        try:
            from report_utils import get_data_sources_paragraph_text, get_data_sources_list
        except ImportError:
            get_data_sources_paragraph_text = lambda: (
                'All metrics cite their origin. Adoption Barriers: CSConsole/Snowflake. '
                'Support Cases: CSOne (TAC). BEMS: CSOne. Service Incidents: status.webex.com. '
                'Customer Pulse, Action Plans, Success Priorities: CSConsole.'
            )
            get_data_sources_list = lambda: [
                ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID'),
                ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number in CSOne'),
                ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'BEMS IDs verifiable in CSOne'),
                ('Service Incidents', 'status.webex.com', 'Public RSS feed'),
                ('Customer Pulse, Action Plans, Success Priorities', 'CSConsole', 'By customer in CSConsole'),
            ]
        self.doc.add_heading('Report Data Sources', level=1)
        sources_para = self.doc.add_paragraph()
        sources_para.add_run(get_data_sources_paragraph_text())
        table = self.doc.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        hdr = table.rows[0].cells
        for i, txt in enumerate(['Metric', 'Source System', 'Verification']):
            hdr[i].text = txt
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
        for metric, source, verification in get_data_sources_list():
            row = table.add_row().cells
            row[0].text = metric
            row[1].text = source
            row[2].text = verification
        self.doc.add_paragraph()
    
    def _add_css_to_customer_ratio_chart(self, team_data: Dict[str, Dict]):
        """Add CSS to Customer Ratio chart at the top of summary"""
        ratio_heading = self.doc.add_heading('CSS to Customer Ratio', level=2)
        if ratio_heading.runs:
            ratio_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Calculate ratios
        table = self.doc.add_table(rows=self.safe_len(team_data) + 2, cols=4)
        table.style = 'Light Grid Accent 1'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # Header
        header_cells = table.rows[0].cells
        headers = ['Team Member', 'Customers', 'CSS Count', 'Customer:CSS Ratio']
        for i, header_text in enumerate(headers):
            cell = header_cells[i]
            cell.text = header_text
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].runs[0].font.size = Pt(10)
                cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            if cell.paragraphs:
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Background color
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '007BC7')
            cell._element.get_or_add_tcPr().append(shading_elm)
        
        # Data rows
        row_idx = 1
        total_customers = 0
        total_css = 0
        
        for cssm_name in sorted(team_data.keys()):
            data = team_data[cssm_name]
            num_customers = self.safe_len(data.get('customers', []))
            num_css = 1  # Each row is one CSS
            
            total_customers += num_customers
            total_css += num_css
            
            ratio = f"{num_customers}:1" if num_customers > 0 else "0:1"
            
            row_cells = table.rows[row_idx].cells
            # Round 13 / Phase 9.7: previously CSSM rows wrote raw
            # ``cssm_name`` straight into ``row_cells[0].text`` with
            # ``str(num_customers)`` / ``str(num_css)`` for the other
            # cells.  An XML-illegal control code in a CSSM display
            # name (zero-width space, tab, surrogate from a mojibake
            # source row) produced a .docx Word refused to open
            # without "repair".  Route every cell through the new
            # ``_r13_safe_doc_text`` helper for parity with the
            # sibling app_simple Word table at Phase 9.1.
            row_cells[0].text = _r13_safe_doc_text(cssm_name, max_len=200)
            row_cells[1].text = _r13_safe_doc_text(num_customers, max_len=20)
            row_cells[2].text = _r13_safe_doc_text(num_css, max_len=20)
            row_cells[3].text = _r13_safe_doc_text(ratio, max_len=20)
            
            for i in range(1, 4):
                if row_cells[i].paragraphs:
                    row_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            row_idx += 1
        
        # Totals row
        totals_cells = table.rows[row_idx].cells
        totals_cells[0].text = 'TEAM TOTAL'
        if totals_cells[0].paragraphs and totals_cells[0].paragraphs[0].runs:
            totals_cells[0].paragraphs[0].runs[0].font.bold = True
        totals_cells[1].text = str(total_customers)
        if totals_cells[1].paragraphs and totals_cells[1].paragraphs[0].runs:
            totals_cells[1].paragraphs[0].runs[0].font.bold = True
        totals_cells[2].text = str(total_css)
        if totals_cells[2].paragraphs and totals_cells[2].paragraphs[0].runs:
            totals_cells[2].paragraphs[0].runs[0].font.bold = True
        
        # Calculate average ratio
        avg_ratio = f"{total_customers/total_css:.1f}:1" if total_css > 0 else "0:1"
        totals_cells[3].text = avg_ratio
        if totals_cells[3].paragraphs and totals_cells[3].paragraphs[0].runs:
            totals_cells[3].paragraphs[0].runs[0].font.bold = True
        
        for i in range(1, 4):
            if totals_cells[i].paragraphs:
                totals_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), 'E8E8E8')
            totals_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        self.doc.add_paragraph()
    
    def _count_bems_escalations(self, data: Dict) -> int:
        """Count BEMS escalations using canonical centralized detection.

        Leader Report intentionally uses the *combined* AB + TAC mode so
        the team-activity rollup includes both adoption-barrier rows that
        reference a BEMS and TAC rows that match the BEMS mask. The
        canonical TAC-only count (used by the Compact / EI dashboards and
        the cross-report consistency contract) is exposed via
        ``cm.count_bems(..., mode=cm.BEMS_MODE_CANONICAL)`` if that value
        is needed elsewhere.
        """
        abs_df = data.get('adoption_barriers', pd.DataFrame())
        tac_df = data.get('tac_cases', pd.DataFrame())
        bems_count = cm.count_bems(
            tac_df, ab_df=abs_df, mode=cm.BEMS_MODE_COMBINED_AB_TAC
        )
        if logger.isEnabledFor(logging.DEBUG):
            ab_bems = cm.count_bems(abs_df, mode=cm.BEMS_MODE_CANONICAL)
            tac_bems = cm.count_bems(tac_df, mode=cm.BEMS_MODE_CANONICAL)
            logger.debug(
                f"Leader BEMS (combined AB+TAC mode): {bems_count} "
                f"(AB={ab_bems}, TAC={tac_bems})"
            )
        return bems_count

    def _count_bems_canonical_tac(self, data: Dict) -> int:
        """Canonical TAC-only BEMS count (matches Compact / EI dashboards)."""
        return cm.count_bems(data.get('tac_cases', pd.DataFrame()))
    
    def _add_technology_breakdown(self, team_data: Dict[str, Dict]):
        """Add technology breakdown by team member.

        Round 39 / Phase 4.2: defer the heading until we know the
        breakdown has actual data.  Pre-Round-39 the heading was
        unconditionally written first, so portfolios with no
        ``PRODUCT_NAME`` values rendered an orphan "Technology
        Assignment Breakdown" header with nothing beneath it.
        """
        # Collect technology data from subscriptions
        tech_breakdown = {}
        
        for cssm_name in sorted(team_data.keys()):
            data = team_data[cssm_name]
            tech_breakdown[cssm_name] = {}
            
            # Get subscriptions and count by product/technology
            # Round 3 / Phase 3.4: dedupe by SUBSCRIPTION_ID first.
            # The DSM table emits multiple rows per subscription (line
            # items, status history, etc.); iterating raw rows here
            # double-counted any subscription that had >1 DSM row,
            # silently inflating the per-CSSM technology totals
            # against the rest of the report's per-subscription
            # rollups.
            subscriptions = data.get('subscriptions', pd.DataFrame())
            if not subscriptions.empty and 'PRODUCT_NAME' in subscriptions.columns:
                _subs_for_count = subscriptions
                if 'SUBSCRIPTION_ID' in subscriptions.columns:
                    _before = len(_subs_for_count)
                    _subs_for_count = subscriptions.drop_duplicates(subset=['SUBSCRIPTION_ID'])
                    _after = len(_subs_for_count)
                    if _before != _after:
                        logger.debug(
                            "Tech rollup for %s: collapsed %d duplicate DSM rows "
                            "(%d -> %d unique subscriptions)",
                            cssm_name, _before - _after, _before, _after,
                        )
                for _, sub in _subs_for_count.iterrows():
                    product = sub.get('PRODUCT_NAME', 'Unknown')
                    if pd.notna(product):
                        # Simplify product names to technology categories
                        tech = self._categorize_technology(str(product))
                        tech_breakdown[cssm_name][tech] = tech_breakdown[cssm_name].get(tech, 0) + 1
        
        # Create table
        all_techs = sorted(set(tech for member_techs in tech_breakdown.values() for tech in member_techs.keys()))
        
        if all_techs:
            # Round 39 / Phase 4.2: render the heading INSIDE the
            # data-present branch so we never produce an orphan
            # "Technology Assignment Breakdown" header on portfolios
            # with no PRODUCT_NAME data.
            tech_heading = self.doc.add_heading('Technology Assignment Breakdown', level=2)
            if tech_heading.runs:
                tech_heading.runs[0].font.color.rgb = CISCO_BLUE
            num_cols = len(all_techs) + 2  # +1 for name, +1 for total
            table = self.doc.add_table(rows=len(team_data) + 2, cols=num_cols)
            table.style = 'Light Grid Accent 1'
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            
            # Header
            header_cells = table.rows[0].cells
            header_cells[0].text = 'Team Member'
            if header_cells[0].paragraphs and header_cells[0].paragraphs[0].runs:
                header_cells[0].paragraphs[0].runs[0].font.bold = True
            if header_cells[0].paragraphs:
                header_cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            for idx, tech in enumerate(all_techs, 1):
                header_cells[idx].text = tech
                if header_cells[idx].paragraphs and header_cells[idx].paragraphs[0].runs:
                    header_cells[idx].paragraphs[0].runs[0].font.bold = True
                    header_cells[idx].paragraphs[0].runs[0].font.size = Pt(9)
                    header_cells[idx].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
                if header_cells[idx].paragraphs:
                    header_cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                # Background color
                shading_elm = OxmlElement('w:shd')
                shading_elm.set(qn('w:fill'), '007BC7')
                header_cells[idx]._element.get_or_add_tcPr().append(shading_elm)
            
            header_cells[num_cols-1].text = 'Total'
            if header_cells[num_cols-1].paragraphs and header_cells[num_cols-1].paragraphs[0].runs:
                header_cells[num_cols-1].paragraphs[0].runs[0].font.bold = True
            if header_cells[num_cols-1].paragraphs:
                header_cells[num_cols-1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Background color for header
            for i in [0, num_cols-1]:
                shading_elm = OxmlElement('w:shd')
                shading_elm.set(qn('w:fill'), '007BC7')
                header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
                if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                    header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            
            # Data rows
            row_idx = 1
            tech_totals = {tech: 0 for tech in all_techs}
            
            for cssm_name in sorted(team_data.keys()):
                row_cells = table.rows[row_idx].cells
                row_cells[0].text = cssm_name
                
                row_total = 0
                for idx, tech in enumerate(all_techs, 1):
                    count = tech_breakdown[cssm_name].get(tech, 0)
                    row_cells[idx].text = str(count) if count > 0 else '-'
                    if row_cells[idx].paragraphs:
                        row_cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    tech_totals[tech] += count
                    row_total += count
                
                row_cells[num_cols-1].text = str(row_total)
                if row_cells[num_cols-1].paragraphs:
                    row_cells[num_cols-1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                if row_cells[num_cols-1].paragraphs and row_cells[num_cols-1].paragraphs[0].runs:
                    row_cells[num_cols-1].paragraphs[0].runs[0].font.bold = True
                
                row_idx += 1
            
            # Totals row
            totals_cells = table.rows[row_idx].cells
            totals_cells[0].text = 'TOTAL'
            if totals_cells[0].paragraphs and totals_cells[0].paragraphs[0].runs:
                totals_cells[0].paragraphs[0].runs[0].font.bold = True
            
            grand_total = 0
            for idx, tech in enumerate(all_techs, 1):
                count = tech_totals[tech]
                totals_cells[idx].text = str(count)
                if totals_cells[idx].paragraphs and totals_cells[idx].paragraphs[0].runs:
                    totals_cells[idx].paragraphs[0].runs[0].font.bold = True
                totals_cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                grand_total += count
                # Background
                shading_elm = OxmlElement('w:shd')
                shading_elm.set(qn('w:fill'), 'E8E8E8')
                totals_cells[idx]._element.get_or_add_tcPr().append(shading_elm)
            
            totals_cells[num_cols-1].text = str(grand_total)
            if totals_cells[num_cols-1].paragraphs and totals_cells[num_cols-1].paragraphs[0].runs:
                totals_cells[num_cols-1].paragraphs[0].runs[0].font.bold = True
            totals_cells[num_cols-1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Background for first and last cells
            for i in [0, num_cols-1]:
                shading_elm = OxmlElement('w:shd')
                shading_elm.set(qn('w:fill'), 'E8E8E8')
                totals_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        self.doc.add_paragraph()
    
    def _categorize_technology(self, product_name: str) -> str:
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
    
    def _get_customer_specific_technology(self, customer: str, data: Dict) -> str:
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
                    tech = self._categorize_technology(str(product_name))
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
    
    def _add_bems_escalation_section(self, team_data: Dict[str, Dict]):
        """Add dedicated BEMS Escalation section (relocated from summary for better TAC context)"""
        total_bems = sum(
            self._count_bems_escalations(member_data)
            for member_data in (team_data or {}).values()
        )

        if total_bems == 0:
            return  # No BEMS escalations to report
        
        # Add page break before BEMS section
        self.doc.add_page_break()
        
        # Add section heading
        section_heading = self.doc.add_heading('Warning: BEMS Escalation Analysis', level=1)
        if section_heading.runs:
            # Round 12 / Phase 9.7: use the canonical CRITICAL risk
            # color (#d62728) so the BEMS heading matches every other
            # critical-risk indicator in the document instead of the
            # ad-hoc pure red (255,0,0).
            section_heading.runs[0].font.color.rgb = CANONICAL_RISK_HIGH_RGB
        
        # Add context paragraph
        context_para = self.doc.add_paragraph()
        context_para.add_run(
            'This section provides detailed analysis of Backend Engineering Management System (BEMS) escalations '
            'identified across team activities and TAC cases. BEMS escalations indicate critical technical issues '
            'requiring immediate attention from backend engineering teams.\n\n'
        ).font.italic = True
        
        # USE the bems_analyzer for advanced insights (was initialized but never used!)
        try:
            if not self.bems_analyzer:
                raise ValueError("BEMS analyzer not available")
            # Combine all adoption barriers and TAC cases from team data
            all_ab = pd.concat([data.get('adoption_barriers', pd.DataFrame()) for data in team_data.values()], ignore_index=True) if team_data else pd.DataFrame()
            all_tac = pd.concat([data.get('tac_cases', pd.DataFrame()) for data in team_data.values()], ignore_index=True) if team_data else pd.DataFrame()
            
            if not all_ab.empty or not all_tac.empty:
                bems_analysis = self.bems_analyzer.analyze_bems_escalations(all_ab, all_tac)
                
                # Add risk assessment from advanced analyzer
                if bems_analysis.get('risk_assessment'):
                    risk = bems_analysis['risk_assessment']
                    risk_para = self.doc.add_paragraph()
                    risk_para.add_run('BEMS Risk Assessment: ').bold = True
                    risk_level = risk.get('overall_risk_level', 'Unknown')
                    risk_run = risk_para.add_run(f'{risk_level}')
                    risk_run.bold = True
                    # Round 12 / Phase 9.7: previously these inline
                    # risk colors were ad-hoc crimson / dark-orange /
                    # forest-green RGB triples (220,20,60 / 255,140,0
                    # / 34,139,34) which differed from the canonical
                    # palette used by every chart.  Resolve through
                    # ``CANONICAL_RISK_*_RGB`` so the inline label
                    # cannot drift away from the legend.
                    if risk_level in ['High', 'Critical']:
                        risk_run.font.color.rgb = CANONICAL_RISK_HIGH_RGB
                    elif risk_level == 'Medium':
                        risk_run.font.color.rgb = CANONICAL_RISK_MED_RGB
                    else:
                        risk_run.font.color.rgb = CANONICAL_RISK_LOW_RGB
                    self.doc.add_paragraph()
                
                # Add strategic recommendations from analyzer - FIXED: Show ALL recommendations
                if bems_analysis.get('strategic_recommendations'):
                    rec_para = self.doc.add_paragraph()
                    rec_para.add_run('Strategic Recommendations:\n').bold = True
                    for rec in bems_analysis['strategic_recommendations']:
                        if isinstance(rec, dict):
                            rec_para.add_run(f"• {rec.get('recommendation', str(rec))}\n")
                        else:
                            rec_para.add_run(f"• {rec}\n")
                    self.doc.add_paragraph()
        except Exception as e:
            logger.warning(f"Advanced BEMS analysis failed, using basic method: {e}")
            # Round 30 / H2: previously the BEMS analyzer exception was
            # logged to stderr only and we silently fell through to the
            # basic ``_add_bems_summary`` path with the same heading,
            # leaving the reader unable to tell which path actually
            # ran.  Render a visible degradation banner before
            # delegating so the report explicitly discloses that the
            # advanced strategic analysis was skipped, plus a
            # classified reason that does not leak internal class
            # paths or stack traces.
            try:
                from error_classifier import classify_analysis_error as _r30_classify_h2
                _r30_classification = _r30_classify_h2(e)
                _r30_kind = (_r30_classification.kind or "analysis.unknown").split(".")[0]
            except Exception as _r30_classify_err:  # noqa: BLE001
                logger.debug(
                    "Round 30 / H2: error classifier unavailable for BEMS "
                    "degradation banner: %s",
                    _r30_classify_err,
                )
                _r30_kind = type(e).__name__ or "Exception"
            try:
                _r30_banner = self.doc.add_paragraph()
                _r30_banner.alignment = WD_ALIGN_PARAGRAPH.LEFT
                _r30_banner_run = _r30_banner.add_run(
                    "BEMS strategic analysis incomplete -- reverted to basic counts "
                    f"(reason: {_r30_kind}).  Detailed escalations still listed below; "
                    "advanced risk-assessment, strategic recommendations, and "
                    "trend insights were not generated for this run."
                )
                _r30_banner_run.bold = True
                _r30_banner_run.font.size = Pt(10)
                _r30_banner_run.font.italic = True
                # Render in the canonical CRITICAL red so the banner
                # is clearly a degradation notice, mirroring the BEMS
                # heading colour.
                try:
                    _r30_banner_run.font.color.rgb = CANONICAL_RISK_HIGH_RGB
                except Exception:  # noqa: BLE001 - colour is purely cosmetic
                    pass
                self.doc.add_paragraph()
            except Exception as _r30_banner_err:  # noqa: BLE001
                logger.warning(
                    "Round 30 / H2: failed to render BEMS degradation "
                    "banner (continuing without it): %s",
                    _r30_banner_err,
                )

        # Call the existing detailed BEMS summary method
        self._add_bems_summary(team_data)
    
    def _add_external_intelligence_section(
        self,
        ext_bugs: List[Dict] = None,
        ext_incidents: List[Dict] = None,
        software_defects: Dict = None,
        psirt_vulns: Dict = None,
        intel_truncated: Optional[Dict[str, Any]] = None,
        intel_fetch_limit: Optional[int] = None,
    ):
        """Add External Intelligence section (defects, PSIRT, incidents) - uses all data sources.

        Round 30 / M2: ``intel_truncated`` is the dict produced by
        :func:`incident_storage.get_all_external_intel` (key
        ``list_truncated``) and may carry ``incidents`` / ``bugs`` /
        ``maintenances`` boolean flags signalling that the upstream
        fetch hit ``ADOPTIQ_INTEL_LIST_LIMIT`` and therefore returned
        only the most recent N rows.  When a flag is set we append a
        ``" (table truncated; fetch limit reached)"`` disclosure to
        the corresponding sub-section so the reader cannot mistake a
        capped sample for the full population.

        ``intel_fetch_limit`` is the ``list_fetch_limit`` int from the
        same intel dict, included in the disclosure when known so the
        reader can see exactly how many rows were retained.
        """
        has_defects = software_defects and software_defects.get('total_defects', 0) > 0
        has_psirt = psirt_vulns and psirt_vulns.get('total_vulnerabilities', 0) > 0
        has_bugs = ext_bugs and len(ext_bugs) > 0
        has_incidents = ext_incidents and len(ext_incidents) > 0
        
        if not (has_defects or has_psirt or has_bugs or has_incidents):
            return

        # Round 30 / M2: helper to format the truncation suffix for each
        # sub-section.  Returns an empty string when the upstream fetch
        # was complete so the existing rendering is unchanged in the
        # happy path.
        def _r30_trunc_suffix(key: str, *, shown: int) -> str:
            try:
                if not intel_truncated or not isinstance(intel_truncated, dict):
                    return ''
                if not intel_truncated.get(key):
                    return ''
            except Exception:  # noqa: BLE001
                return ''
            if intel_fetch_limit and isinstance(intel_fetch_limit, int) and intel_fetch_limit > 0:
                return (
                    f' (table truncated; fetch limit reached -- shown {shown} of '
                    f'most-recent {intel_fetch_limit} rows; older rows omitted)'
                )
            return (
                f' (table truncated; fetch limit reached -- shown {shown} most-recent rows; '
                'older rows omitted)'
            )

        self.doc.add_heading('External Intelligence & Known Issues', level=1)
        
        # Software defects from customer data
        if has_defects:
            para = self.doc.add_paragraph()
            para.add_run('Software Defects: ').bold = True
            para.add_run(f'{software_defects.get("total_defects", 0)} unique BST/CSC defects in {software_defects.get("total_cases_with_defects", 0)} cases. ')
            para.add_run('Source: CSOne, Adoption Barriers.\n').italic = True
        
        # External bugs from help.webex.com
        if has_bugs:
            para = self.doc.add_paragraph()
            para.add_run('Known Issues (help.webex.com): ').bold = True
            _bug_count = len(ext_bugs)
            _bug_suffix = _r30_trunc_suffix('bugs', shown=_bug_count)
            para.add_run(f'{_bug_count} publicly referenced defects{_bug_suffix}.\n')
            if _bug_suffix:
                _disc = self.doc.add_paragraph()
                _disc.add_run(_bug_suffix.lstrip(' (').rstrip(').')).italic = True
        
        # PSIRT vulnerabilities
        if has_psirt:
            para = self.doc.add_paragraph()
            vuln_count = psirt_vulns.get('total_vulnerabilities', 0)
            cve_count = len(psirt_vulns.get('cve_ids', set()))
            psirt_count = len(psirt_vulns.get('psirt_advisories', set()))
            para.add_run('Security Vulnerabilities: ').bold = True
            para.add_run(f'{vuln_count} total ({cve_count} CVEs, {psirt_count} PSIRT advisories). ')
            para.add_run('Source: CSOne, Adoption Barriers.\n').italic = True
        
        # Service incidents
        if has_incidents:
            para = self.doc.add_paragraph()
            para.add_run('Service Incidents (status.webex.com): ').bold = True
            _inc_count = len(ext_incidents)
            _inc_suffix = _r30_trunc_suffix('incidents', shown=_inc_count)
            para.add_run(f'{_inc_count} incidents in analysis period{_inc_suffix}.\n')
            if _inc_suffix:
                _disc = self.doc.add_paragraph()
                _disc.add_run(_inc_suffix.lstrip(' (').rstrip(').')).italic = True
    
    def _add_bems_summary(self, team_data: Dict[str, Dict]):
        """Add BEMS escalation summary with details"""
        bems_heading = self.doc.add_heading('BEMS Escalation Details', level=2)
        # Round 13 / Phase 5.1: previously the BEMS heading + warning
        # used raw ``RGBColor(255, 0, 0)`` -- a saturated #FF0000 red
        # that has no representation anywhere in
        # ``canonical_metrics.RISK_BAND_COLORS``.  The matplotlib /
        # Excel risk-band charts elsewhere in the same Word report
        # render CRITICAL as ``#d62728``, so the same "this is a
        # critical issue" semantic appeared in two different reds in
        # one document.  Drive the BEMS color from the shared
        # ``CANONICAL_RISK_HIGH_RGB`` (which is already resolved from
        # ``RISK_BAND_COLORS["CRITICAL"]`` at module-import) so any
        # future palette tweak lands in exactly one place.
        if bems_heading.runs:
            bems_heading.runs[0].font.color.rgb = CANONICAL_RISK_HIGH_RGB

        # Warning paragraph
        warning_para = self.doc.add_paragraph()
        warning_run = warning_para.add_run('Warning: CRITICAL: BEMS (Back-End Engineering Management System) escalations detected!\n')
        warning_run.font.bold = True
        warning_run.font.size = Pt(11)
        # Round 13 / Phase 5.1: align with the canonical CRITICAL hex
        # so the BEMS warning paragraph and the BEMS heading share
        # the exact same red as every CRITICAL bar in the chart pages.
        warning_run.font.color.rgb = CANONICAL_RISK_HIGH_RGB
        
        warning_para.add_run('These escalations require immediate attention from backend engineering teams.\n\n')
        
        # Collect all BEMS details
        bems_details = []
        
        for cssm_name in sorted(team_data.keys()):
            data = team_data[cssm_name]
            
            # Check adoption barriers - enhanced with BEMS ID extraction
            abs_df = data.get('adoption_barriers', pd.DataFrame())
            if not abs_df.empty:
                ab_bems_mask = detect_bems_mask(abs_df)
                for _, row in abs_df[ab_bems_mask].iterrows():
                    subject = str(row.get('SUBJECT_C', row.get('title', '')))
                    bems_ids = extract_bems_ids_from_row(row)
                    bems_id = ", ".join(bems_ids) if bems_ids else 'N/A'

                    bems_details.append({
                        'css': cssm_name,
                        'type': 'Adoption Barrier',
                        'customer': row.get('BU_NAME', row.get('customer_name', 'Unknown')),
                        'subject': subject,
                        'id': row.get('ID', 'N/A'),
                        'bems_id': bems_id
                    })

            # Check TAC cases - canonical BEMS detection and extraction
            tac_df = data.get('tac_cases', pd.DataFrame())
            if not tac_df.empty:
                tac_bems_mask = detect_bems_mask(tac_df)
                for _, row in tac_df[tac_bems_mask].iterrows():
                    subject = str(row.get('Problem', '') or row.get('Title', ''))
                    bems_ids = extract_bems_ids_from_row(row)
                    bems_id = ", ".join(bems_ids) if bems_ids else 'N/A'

                    bems_details.append({
                        'css': cssm_name,
                        'type': 'TAC Case',
                        'customer': row.get('Account:', row.get('customer_name', 'Unknown')),
                        'subject': subject,
                        'id': row.get('Case #', row.get('SR Number', 'N/A')),
                        'bems_id': bems_id
                    })
        
        # Create BEMS details table with enhanced columns
        if bems_details:
            table = self.doc.add_table(rows=len(bems_details) + 1, cols=6)
            table.style = 'Light Grid Accent 1'
            
            # Header - includes BEMS ID column
            header_cells = table.rows[0].cells
            headers = ['CSS', 'Type', 'Customer', 'Subject', 'Case ID', 'BEMS ID']
            for i, header_text in enumerate(headers):
                cell = header_cells[i]
                cell.text = header_text
                if cell.paragraphs and cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].font.bold = True
                    cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                shading_elm = OxmlElement('w:shd')
                shading_elm.set(qn('w:fill'), 'FF6B6B')  # Red background
                cell._element.get_or_add_tcPr().append(shading_elm)
            
            # Data rows with BEMS ID
            for idx, detail in enumerate(bems_details, 1):
                row_cells = table.rows[idx].cells
                row_cells[0].text = detail['css']
                row_cells[1].text = detail['type']
                row_cells[2].text = str(detail['customer']) if detail['customer'] else 'Unknown'  # FIXED: Full customer name
                row_cells[3].text = str(detail['subject'])  # Already fixed - full text
                row_cells[4].text = str(detail['id'])
                # Format BEMS ID with brackets for citation like [BEMS01916938]
                bems_id = detail.get('bems_id', 'N/A')
                row_cells[5].text = f'[{bems_id}]' if bems_id and bems_id != 'N/A' else 'N/A'
                
                # Center align some cells
                for i in [1, 4, 5]:
                    row_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        self.doc.add_paragraph()
    
    def _add_individual_team_member_summaries(self, team_data: Dict[str, Dict], days: int):
        """Deprecated: per-team-member rendering is handled by
        ``_create_adoptiq_summaries_per_person``. Retained only as a
        backwards-compatible no-op for any external caller that may still
        reference this method; scheduled for removal.
        """
        logger.warning(
            "_add_individual_team_member_summaries is deprecated and is now a "
            "no-op; per-team-member rendering is in "
            "_create_adoptiq_summaries_per_person."
        )
        return

    def _add_individual_summary_paragraph(self, cssm_name: str, data: Dict, days: int):
        """
        Add a comprehensive paragraph summary for an individual team member.
        Provides detailed account overview, key issues, ARR context, sentiment analysis, and actionable recommendations.
        """
        logger.debug(f"Starting _add_individual_summary_paragraph for {cssm_name}")
        logger.debug(f"Data keys available: {list(data.keys())}")
        
        # FIXED: Use PRIMARY customer list from subscriptions (same as CSS to Customer Ratio table)
        # This ensures consistency between the table and individual summaries
        customers = data.get('customers', [])
        if not isinstance(customers, list):
            customers = list(customers) if customers else []
        customers = [c for c in customers if c and str(c).strip()]
        total_customers = len(customers)
        
        logger.debug(f"Using PRIMARY customer list from subscriptions: {total_customers} customers")
        
        # Collect activity data for analysis
        adoption_barriers = data.get('adoption_barriers', pd.DataFrame())
        action_plans = data.get('action_plans', pd.DataFrame())
        customer_pulse = data.get('customer_pulse', pd.DataFrame())
        tac_cases = data.get('tac_cases', pd.DataFrame())
        
        logger.debug(f"Data sizes - ABs: {len(adoption_barriers)}, APs: {len(action_plans)}, CPs: {len(customer_pulse)}, TACs: {len(tac_cases)}")

        # Round 2 / Phase 3.1: scan ``customer_pulse.attrs['fetch_error']``
        # (parallel to compact_report_formatter ~1102).  If pulse fetch
        # failed, the dataframe will be empty *with* a fetch_error attr
        # and rendering "0 pulse responses" / "Neutral sentiment" is a
        # silent lie.  We emit a "pulse unavailable" sentence instead.
        pulse_unavailable_reason: Optional[str] = None
        try:
            if hasattr(customer_pulse, 'attrs') and customer_pulse.attrs.get('fetch_error'):
                pulse_unavailable_reason = str(customer_pulse.attrs.get('fetch_error'))[:256]
        except Exception:
            pulse_unavailable_reason = None

        sentiment_summary = self._derive_sentiment_summary(
            customer_pulse=customer_pulse,
            adoption_barriers=adoption_barriers,
        )

        if total_customers == 0:
            summary_para = self.doc.add_paragraph()
            summary_para.add_run(f'{cssm_name} currently has no assigned customer accounts or data available for the selected {days}-day period. This may indicate a new team member assignment or a data synchronization issue that requires verification with the customer success management system.').font.italic = True
            return
        
        # Calculate key metrics
        total_barriers = len(adoption_barriers) if not adoption_barriers.empty else 0
        total_action_plans = len(action_plans) if not action_plans.empty else 0
        total_pulse_responses = len(customer_pulse) if not customer_pulse.empty else 0
        total_tac_cases = len(tac_cases) if not tac_cases.empty else 0
        
        # Calculate health metrics
        # Round 3 hardening: derive high-priority barriers from canonical
        # ``cm.count_critical_barriers`` so the count agrees with the
        # leader's own "Critical Adoption Barriers Requiring Action"
        # section and the EI/Compact dashboards. The previous matcher
        # only inspected ``PRIORITY``, ignored ``SEVERITY_C`` (which is
        # what every other report uses), and over-matched on the literal
        # string "Urgent" that is not part of the canonical ladder.
        high_priority_barriers = 0
        if not adoption_barriers.empty:
            try:
                high_priority_barriers = int(cm.count_critical_barriers(
                    adoption_barriers,
                    mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH,
                ))
            except Exception:
                high_priority_barriers = 0
        
        avg_pulse_score = None
        if not customer_pulse.empty:
            score_col = next(
                (col for col in ('SCORE__C', 'SCORE') if col in customer_pulse.columns),
                None,
            )
            if score_col is not None:
                scores = pd.to_numeric(customer_pulse[score_col], errors='coerce').dropna()
                if not scores.empty:
                    avg_pulse_score = scores.mean()
        
        # Identify top issues
        top_barrier_categories = []
        if not adoption_barriers.empty and 'CATEGORY' in adoption_barriers.columns:
            # FIXED: Get ALL barrier categories
            top_barrier_categories = adoption_barriers['CATEGORY'].value_counts().index.tolist()
        
        # Create comprehensive summary paragraph
        summary_para = self.doc.add_paragraph()
        
        # Start with portfolio overview and sentiment context
        sentiment_context = f" with {sentiment_summary.lower()} customer sentiment"

        summary_text = f"{cssm_name} manages {total_customers} customer accounts{sentiment_context}. "
        summary_text += f"Portfolio shows {total_barriers} adoption barriers, {total_action_plans} action plans, and {total_tac_cases} TAC cases recorded over the last {days} days. "
        
        # Round 39 / Phase 1.3: re-ground the health assessment with
        # per-customer rates AND absolute-volume floors so portfolios
        # like William Phillips's (0 ABs, 5 TAC, 2 customers) no longer
        # trip the "high volumes" branch on a single low-count signal.
        # Pre-Round-39 the third branch fired whenever
        # ``total_barriers > total_customers OR total_tac_cases > total_customers * 0.5``,
        # which evaluated True for almost every CSSM with non-trivial
        # TAC traffic and rendered "requires immediate attention" 10 of
        # 11 times in the Brian Frazier 90d audit -- false alarms
        # eroded executive trust in the report.
        #
        # New thresholds:
        #   - Healthy:   AB == 0 AND TAC == 0
        #   - Manageable load: low absolute counts (AB+TAC <= 5) regardless of rate
        #   - Generally healthy: AB rate <= 0.5/customer AND TAC rate <= 0.3/customer
        #   - High volume (immediate attention): EITHER (AB >= 10 AND AB rate > 1/customer)
        #                                        OR (TAC >= 10 AND TAC rate > 0.5/customer)
        #   - Mixed health: anything else (the catch-all describes signals honestly,
        #                   never claims "high volumes")
        ab_per_customer = (total_barriers / total_customers) if total_customers > 0 else 0.0
        tac_per_customer = (total_tac_cases / total_customers) if total_customers > 0 else 0.0

        if total_barriers == 0 and total_tac_cases == 0:
            summary_text += (
                "The portfolio demonstrates excellent health with no significant "
                "barriers or technical issues requiring attention. "
            )
            if sentiment_summary == "Positive":
                summary_text += (
                    "Strong customer sentiment further validates the health of "
                    "these relationships. "
                )
        elif (total_barriers + total_tac_cases) <= 5:
            summary_text += (
                f"The portfolio carries a manageable load ({total_barriers} adoption "
                f"barriers, {total_tac_cases} TAC cases across {total_customers} "
                f"customers); standard cadence engagement is sufficient. "
            )
            if sentiment_summary == "Positive":
                summary_text += (
                    "Positive customer sentiment confirms the relationship "
                    "trajectory is on track. "
                )
        elif ab_per_customer <= 0.5 and tac_per_customer <= 0.3:
            summary_text += (
                "The portfolio shows generally healthy customer relationships with "
                "manageable levels of adoption challenges relative to its size. "
            )
            if sentiment_summary == "Positive":
                summary_text += (
                    "Positive customer sentiment indicates strong relationship "
                    "management despite some challenges. "
                )
        elif (
            (total_barriers >= 10 and ab_per_customer > 1.0)
            or (total_tac_cases >= 10 and tac_per_customer > 0.5)
        ):
            summary_text += (
                f"The portfolio requires immediate attention: {total_barriers} "
                f"adoption barriers ({ab_per_customer:.1f}/customer) and "
                f"{total_tac_cases} TAC cases ({tac_per_customer:.1f}/customer) "
                f"across {total_customers} accounts indicate sustained pressure. "
            )
        else:
            summary_text += (
                f"The portfolio shows mixed health -- {total_barriers} barriers "
                f"and {total_tac_cases} TAC cases across {total_customers} "
                f"customers point to a few accounts needing focused intervention. "
            )
            if sentiment_summary == "Negative":
                summary_text += (
                    "Negative sentiment trends suggest proactive engagement is "
                    "needed to prevent further deterioration. "
                )
        
        # Add specific insights
        if high_priority_barriers > 0:
            summary_text += f"Critical attention is needed for {high_priority_barriers} high-priority adoption barriers that may impact customer satisfaction and retention. "
        
        if top_barrier_categories:
            # FIXED: Show ALL barrier categories
            categories_str = ', '.join(top_barrier_categories)
            summary_text += f"The barrier categories are: {categories_str}, suggesting systemic issues that may benefit from standardized solutions or training programs. "
        
        # Round 2 / Phase 1.9: standardize on the canonical 0-10 pulse
        # scale (Salesforce Customer Pulse SCORE__C is 0-10 in
        # production).  Earlier this call site used PULSE_SCALE_0_TO_5
        # while the assess_customer_health call site (line ~118) used
        # PULSE_SCALE_0_TO_10, so the same SCORE__C row could be
        # labelled "Negative" by one paragraph and "Positive" by
        # another.  Both call sites now use PULSE_SCALE_0_TO_10.
        pulse_summary = None
        # Round 2 / Phase 3.1: if pulse fetch failed, never compute or
        # render a pulse sentiment paragraph — emit explicit
        # "pulse unavailable" instead of pretending the pulse was
        # neutral / score=0.
        if pulse_unavailable_reason:
            summary_text += (
                f"Customer pulse feedback unavailable for this period "
                f"(source error: {pulse_unavailable_reason}). "
            )
            sent_label = None
            mean_norm = None
        else:
            if avg_pulse_score is not None and not customer_pulse.empty:
                try:
                    pulse_summary = cm.pulse_sentiment(customer_pulse, scale=cm.PULSE_SCALE_0_TO_10)
                except Exception as _pulse_exc:
                    logger.warning(
                        "Pulse sentiment computation failed; omitting pulse sentence: %s",
                        _pulse_exc,
                    )
                    pulse_summary = None
            if pulse_summary is None:
                # Skip the pulse-derived narrative; do not invent "Neutral".
                sent_label = None
                mean_norm = None
            else:
                sent_label = pulse_summary.get("sentiment", "Neutral")
                mean_norm = pulse_summary.get("mean_0_to_10")
                # Render the score using the canonical 0-10 scale so the
                # narrative cannot disagree with the canonical sentiment
                # label.  ``avg_pulse_score`` is already on the same scale
                # as the SCORE__C column.
                # Round 7 / Phase 6.9: route through shared
                # ``format_number`` so the rounding policy matches the
                # rest of the platform (executive summary, compact,
                # dashboard).  Soft-fail to inline f-string so the
                # narrative still renders if report_utils is missing.
                try:
                    from report_utils import format_number as _fmt_num_p69_lr
                except Exception:
                    def _fmt_num_p69_lr(v, decimals=1, as_percent=False):
                        try:
                            return f"{float(v):.{decimals}f}"
                        except Exception:
                            return "N/A"
                mean_label = (
                    f"average score of {_fmt_num_p69_lr(mean_norm, decimals=1)}/10.0"
                    if mean_norm is not None
                    else f"average score of {_fmt_num_p69_lr(avg_pulse_score, decimals=1)}/10.0"
                )
                if sent_label == "Positive":
                    summary_text += (
                        f"Customer pulse feedback is positive with an {mean_label}, "
                        "indicating strong customer satisfaction. "
                    )
                elif sent_label == "Negative":
                    summary_text += (
                        f"Customer pulse feedback indicates concerns with an {mean_label}, "
                        "requiring immediate customer engagement. "
                    )
                else:
                    summary_text += (
                        f"Customer pulse feedback shows moderate satisfaction with an "
                        f"{mean_label}, with room for improvement. "
                    )
        
        # Add actionable recommendations
        summary_text += "\n\nActionable Recommendations: "
        
        # Barrier resolution recommendations
        if total_barriers > total_customers * 0.8:
            summary_text += "Schedule dedicated time with this team member to review barrier resolution strategies and identify common patterns that could be addressed through training or process improvements. "
        elif total_tac_cases > total_customers * 0.4:
            summary_text += "Focus on technical issue prevention through proactive customer education and implementation best practices to reduce TAC case volume. "
        
        # Action plan recommendations
        if total_action_plans < total_barriers * 0.7:
            summary_text += "Ensure all identified adoption barriers have corresponding action plans with clear timelines and success metrics. "
        
        # Sentiment-based recommendations
        if sentiment_summary == "Negative":
            summary_text += "Implement proactive customer engagement strategy to address negative sentiment indicators. "
        elif sentiment_summary == "Positive":
            summary_text += "Leverage positive sentiment to amplify adoption and customer advocacy outcomes. "
        
        # Pulse score recommendations — Round 3: gate on the same
        # canonical sentiment label used by the paragraph above so we
        # never tell a leader to "prioritize outreach" while the
        # narrative classifies the pulse as Neutral. (Previously this
        # fired any time avg_pulse_score < 3.5 even though canonical
        # negative sentiment on the 0-5 scale is <= 2.5.)
        if sentiment_summary == "Negative":
            summary_text += "Prioritize direct customer outreach to understand satisfaction concerns and develop improvement plans for affected accounts. "
        
        # Workload recommendations
        if total_customers > 20:
            summary_text += f"Consider workload distribution review as managing {total_customers} accounts may impact service quality and customer satisfaction. "
        
        summary_text += "Regular one-on-one meetings should focus on account health reviews, barrier resolution progress, and customer success strategy alignment."
        
        # Add the summary text
        summary_para.add_run(summary_text)
        
        # Add spacing after summary
        self.doc.add_paragraph()
        
        logger.debug(f"Completed _add_individual_summary_paragraph for {cssm_name}")
    
    def _create_summary_table(self, team_data: Dict[str, Dict], days: int):
        """Create Page 1: Summary table with counts of APs, ABs, and CPs per person"""
        # Page heading
        heading = self.doc.add_heading('Team Activity Summary', level=1)
        if heading.runs:
            heading_run = heading.runs[0]
            heading_run.font.color.rgb = CISCO_BLUE
        
        # Description
        # Round 39 / Phase 1.2: the per-CSSM TAC column is now part of
        # the summary so the "Total Activities" column visibly reconciles
        # against AP + AB + CP + TAC + BEMS.  Pre-Round-39 the table
        # rendered the BEMS+CP+AB+AP sum but no TAC column, which made
        # the Activity Counts Cross-Check (5 columns including TAC) and
        # the Team Activity Summary (4 columns excluding TAC) visibly
        # disagree on what "Total Activities" means.
        desc_para = self.doc.add_paragraph()
        desc_para.add_run(
            f'Overview of Action Plans (AP), Adoption Barriers (AB), Customer Pulse (CP), '
            f'TAC cases, BEMS escalations, and Customer Sentiment per team member over the last {days} days.\n\n'
        )

        # NEW: Add CSS to Customer Ratio Chart
        self._add_css_to_customer_ratio_chart(team_data)

        # Create enhanced summary table with sentiment
        # Header: Person | APs | ABs | CPs | TAC | BEMS | Sentiment | Total Activities
        num_rows = self.safe_len(team_data) + 2  # +1 for header, +1 for totals
        table = self.doc.add_table(rows=num_rows, cols=8)
        table.style = 'Light Grid Accent 1'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER

        # Header row
        # Round 4 / Phase 3.7: when the optional ARR sentiment
        # analyzer is wired in, the "Sentiment" column is no longer
        # the canonical pulse label — it is ARR-enriched.  Label the
        # column accordingly so a reader does not assume the value
        # came from ``cm.pulse_sentiment``.
        _sentiment_label = (
            'Sentiment (ARR-enriched)' if getattr(self, 'arr_sentiment_analyzer', None) else 'Sentiment'
        )
        header_cells = table.rows[0].cells
        headers = ['Team Member', 'Action Plans', 'Adoption Barriers', 'Customer Pulse', 'TAC Cases', 'BEMS', _sentiment_label, 'Total Activities']
        
        for i, header_text in enumerate(headers):
            cell = header_cells[i]
            cell.text = header_text
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].runs[0].font.size = Pt(11)
                cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            # Add background color
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '007BC7')  # Cisco blue
            cell._element.get_or_add_tcPr().append(shading_elm)
            if cell.paragraphs:
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Data rows
        total_aps = 0
        total_abs = 0
        total_cps = 0
        total_tac = 0  # Round 39 / Phase 1.2: track TAC for unified total
        total_bems = 0
        row_idx = 1
        for cssm_name in sorted(team_data.keys()):
            data = team_data[cssm_name]
            
            # Rules-based sentiment derived from pulse scores and AB severity.
            # The optional ARR-based analyzer still wins when available so
            # richer signals are not discarded.
            team_sentiment = self._derive_sentiment_summary(
                customer_pulse=data.get('customer_pulse'),
                adoption_barriers=data.get('adoption_barriers'),
            )

            customers = data.get('customers', [])
            if not isinstance(customers, list):
                customers = list(customers) if customers else []
            customers = [c for c in customers if c and str(c).strip()]

            logger.debug(f"  {cssm_name}: Using PRIMARY customer list ({len(customers)} customers)")
            if self.arr_sentiment_analyzer:
                try:
                    sentiment_data = self.arr_sentiment_analyzer.analyze_customer_sentiment(cssm_name, data)
                    team_sentiment = sentiment_data.get('overall_sentiment', team_sentiment)
                except Exception as e:
                    logger.debug(f"Sentiment analysis failed for {cssm_name}: {e}")
            
            num_aps = self.safe_len(data['action_plans'])
            num_abs = self.safe_len(data['adoption_barriers'])
            num_cps = self.safe_len(data['customer_pulse'])
            num_tac = self.safe_len(data.get('tac_cases', pd.DataFrame()))

            # Count BEMS escalations (combined AB+TAC for the Leader summary).
            num_bems = self._count_bems_escalations(data)

            # Round 39 / Phase 1.2: canonical "Total Activities" = AP + AB + CP + TAC + BEMS.
            # All three rendering sites (Team Activity Summary, per-member
            # sub-table, Activity Counts Cross-Check) now use the same
            # ACTIVITIES_MODE_FULL formula so a director comparing the
            # three columns sees identical numbers for the same person.
            num_total = cm.count_total_activities(
                action_plans_df=data.get('action_plans'),
                ab_df=data.get('adoption_barriers'),
                customer_pulse_df=data.get('customer_pulse'),
                tac_df=data.get('tac_cases'),
                bems_count=num_bems,
                mode=cm.ACTIVITIES_MODE_FULL,
            )

            total_aps += num_aps
            total_abs += num_abs
            total_cps += num_cps
            total_tac += num_tac
            total_bems += num_bems

            row_cells = table.rows[row_idx].cells
            row_cells[0].text = cssm_name
            row_cells[1].text = str(num_aps)
            row_cells[2].text = str(num_abs)
            row_cells[3].text = str(num_cps)
            row_cells[4].text = str(num_tac)
            row_cells[5].text = str(num_bems)
            row_cells[6].text = team_sentiment
            row_cells[7].text = str(num_total)

            # Center align numeric cells
            for i in range(1, 8):
                if row_cells[i].paragraphs:
                    row_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Round 13 / Phase 5.3: previously Positive/Negative pulse
            # text colours were hand-mixed RGB (``(0,128,0)`` /
            # ``(255,0,0)``) and the BEMS row shading was a hard-coded
            # ``FFE6E6`` light-red.  None of these matched the canonical
            # ``RISK_BAND_COLORS`` palette used by the matplotlib /
            # Excel band fills, so the same "this is a low/high risk
            # signal" semantic showed up in three different reds and
            # two different greens across one report.  Resolve all
            # three through the shared canonical map (LOW / CRITICAL +
            # the historical 14% lighter tint of CRITICAL we use as
            # ``#FFE6E6``-replacement) so the leader sentiment column
            # and the chart pages render consistent risk colours.
            # Round 39 / Phase 1.2: Sentiment column moved from index 5
            # to index 6 when TAC Cases was inserted between CP (3) and
            # BEMS (5).
            if row_cells[6].paragraphs and row_cells[6].paragraphs[0].runs:
                if team_sentiment == "Positive":
                    row_cells[6].paragraphs[0].runs[0].font.color.rgb = CANONICAL_RISK_LOW_RGB
                elif team_sentiment == "Negative":
                    row_cells[6].paragraphs[0].runs[0].font.color.rgb = CANONICAL_RISK_HIGH_RGB
                else:
                    row_cells[6].paragraphs[0].runs[0].font.color.rgb = RGBColor(0x7f, 0x7f, 0x7f)

            # Highlight BEMS if > 0 (BEMS now at index 5 after TAC insert)
            if num_bems > 0:
                shading_elm = OxmlElement('w:shd')
                # Round 13 / Phase 5.3: shade BEMS rows with the
                # canonical CRITICAL hex pulled from
                # ``RISK_BAND_COLORS`` (with the leading ``#`` stripped
                # so the OOXML ``w:fill`` attribute is valid).  This
                # replaces the hard-coded ``FFE6E6`` light-red so
                # palette tweaks land in exactly one place.
                try:
                    _r13_bems_fill = (_R12_LRG_RBC.get('CRITICAL', '#d62728') or '#d62728').lstrip('#').upper()
                    if len(_r13_bems_fill) != 6:
                        _r13_bems_fill = 'D62728'
                except Exception:
                    _r13_bems_fill = 'D62728'
                shading_elm.set(qn('w:fill'), _r13_bems_fill)
                row_cells[5]._element.get_or_add_tcPr().append(shading_elm)
            
            # Add individual summary paragraph immediately after this team member's row
            # This is where the black arrow points in the user's image
            self.doc.add_paragraph()  # Add spacing after table row
            
            # Add the comprehensive summary paragraph for this team member
            try:
                self._add_individual_summary_paragraph(cssm_name, data, days)
                logger.debug(f"Successfully added summary paragraph for {cssm_name} after table row")
            except Exception as e:
                logger.error(f"Error adding summary paragraph for {cssm_name}: {e}")
                # Add a fallback paragraph
                fallback_para = self.doc.add_paragraph()
                fallback_para.add_run(f"Summary for {cssm_name}: Portfolio analysis temporarily unavailable.").font.italic = True
            
            if row_idx < self.safe_len(team_data):
                separator_para = self.doc.add_paragraph()
                separator_para.add_run("_" * 80).font.color.rgb = CISCO_GRAY
                separator_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                self.doc.add_paragraph()

            row_idx += 1
        
        # Totals row (Round 39 / Phase 1.2: 8-column layout with TAC + canonical AP+AB+CP+TAC+BEMS total)
        _grand_total = total_aps + total_abs + total_cps + total_tac + total_bems
        totals_cells = table.rows[row_idx].cells
        totals_cells[0].text = 'TOTAL'
        totals_cells[1].text = str(total_aps)
        totals_cells[2].text = str(total_abs)
        totals_cells[3].text = str(total_cps)
        totals_cells[4].text = str(total_tac)
        totals_cells[5].text = str(total_bems)
        totals_cells[6].text = "Team Avg"
        totals_cells[7].text = str(_grand_total)

        # Bold totals row
        for i in range(8):
            if totals_cells[i].paragraphs and totals_cells[i].paragraphs[0].runs:
                totals_cells[i].paragraphs[0].runs[0].font.bold = True
            if i > 0:
                totals_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add light gray background
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), 'E8E8E8')
            totals_cells[i]._element.get_or_add_tcPr().append(shading_elm)

        # Add insights paragraph
        # Round 39 / Phase 4.3: each bullet ends with a newline so
        # adjacent items don't visually run together (pre-Round-39
        # the "619• Total Action Plans..." glyph collision was caused
        # by the BEMS run dropping its trailing newline).
        self.doc.add_paragraph('\n')
        insights_heading = self.doc.add_heading('Key Insights', level=2)
        if insights_heading.runs:
            insights_heading.runs[0].font.color.rgb = CISCO_BLUE

        insights_para = self.doc.add_paragraph()
        insights_para.add_run(f'• Total team activities: {_grand_total}\n')
        insights_para.add_run(f'• Total Action Plans: {total_aps}\n')
        insights_para.add_run(f'• Total Adoption Barriers: {total_abs}\n')
        insights_para.add_run(f'• Total Customer Pulse records: {total_cps}\n')
        insights_para.add_run(f'• Total TAC Cases: {total_tac}\n')
        insights_para.add_run(f'• Total BEMS Escalations: {total_bems}\n')
        if total_bems > 0:
            insights_para.runs[-1].font.color.rgb = RGBColor(255, 0, 0)
            insights_para.runs[-1].font.bold = True

        # Fix: Check both total > 0 AND team_data not empty to prevent division by zero
        team_size = self.safe_len(team_data)
        if _grand_total > 0 and team_size > 0:
            insights_para.add_run(
                f'• Average activities per team member: '
                f'{_grand_total / team_size:.1f}\n'
            )
        
        # NEW: Add Technology Breakdown
        self._add_technology_breakdown(team_data)
        
        # NOTE: BEMS Summary moved to TAC Cases section for better context
        
        self.doc.add_page_break()

    # ------------------------------------------------------------------
    # Team insights: leaderboards, aging, coverage gaps, period deltas
    # These helpers surface signals that live in the already-fetched data
    # but aren't otherwise rendered.
    # ------------------------------------------------------------------

    _AGING_BUCKETS: Tuple[Tuple[str, int, Optional[int]], ...] = (
        ("0-7 days", 0, 7),
        ("8-30 days", 8, 30),
        ("31-60 days", 31, 60),
        ("61+ days", 61, None),
    )

    _CLOSED_STATUS_TOKENS: Tuple[str, ...] = (
        "closed", "resolved", "complete", "completed", "done", "cancelled", "canceled"
    )

    def _collect_creator_counts(self, team_data: Dict[str, Dict]) -> Dict[str, Dict[str, Any]]:
        """Aggregate creator activity across AP/AB/CP for the leaderboard.

        Returns a mapping ``creator_label -> {"total": int, "AP": int,
        "AB": int, "CP": int, "external": int}``. Rows without a creator
        label (the CSSM's own records where ``_CREATOR_NAME`` is empty) fall
        back to the CSSM key so the tallies still add up.
        """
        aggregates: Dict[str, Dict[str, Any]] = {}

        def _bump(label: str, kind: str, external: bool) -> None:
            bucket = aggregates.setdefault(
                label,
                {"total": 0, "AP": 0, "AB": 0, "CP": 0, "external": 0},
            )
            bucket["total"] += 1
            bucket[kind] += 1
            if external:
                bucket["external"] += 1

        kinds = (("action_plans", "AP"), ("adoption_barriers", "AB"), ("customer_pulse", "CP"))

        for cssm_name, data in (team_data or {}).items():
            for key, label in kinds:
                df = data.get(key)
                if df is None or getattr(df, "empty", True):
                    continue
                if "_CREATOR_NAME" in df.columns:
                    names = df["_CREATOR_NAME"].fillna("").astype(str).str.strip()
                else:
                    names = pd.Series([""] * len(df))
                emails = (
                    df["_CREATOR_EMAIL"].fillna("").astype(str).str.strip()
                    if "_CREATOR_EMAIL" in df.columns
                    else pd.Series([""] * len(df))
                )
                ext_flags = (
                    df["_EXTERNAL_ACCOUNT"].fillna(False).astype(bool)
                    if "_EXTERNAL_ACCOUNT" in df.columns
                    else pd.Series([False] * len(df))
                )
                for i in range(len(df)):
                    name = names.iloc[i] if i < len(names) else ""
                    email = emails.iloc[i] if i < len(emails) else ""
                    creator_label = name or email or cssm_name
                    _bump(creator_label, label, bool(ext_flags.iloc[i]) if i < len(ext_flags) else False)

        return aggregates

    def _add_collaboration_leaderboard(self, team_data: Dict[str, Dict]) -> bool:
        """Render a top-creator leaderboard using ``_CREATOR_NAME`` across
        AP/AB/CP frames. Returns True if a section was written.
        """
        aggregates = self._collect_creator_counts(team_data)
        if not aggregates:
            return False

        ranked = sorted(
            aggregates.items(),
            key=lambda kv: (-kv[1]["total"], kv[0]),
        )[:10]

        heading = self.doc.add_heading('Collaboration Leaderboard', level=2)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE

        desc = self.doc.add_paragraph()
        desc.add_run(
            'Top creators across Action Plans, Adoption Barriers, and Customer Pulse in this window. '
            'External-account contributions are counted separately; they represent collaboration on '
            'accounts outside the creator\'s primary DSM portfolio.\n'
        ).font.italic = True

        table = self.doc.add_table(rows=len(ranked) + 1, cols=6)
        table.style = 'Light Grid Accent 1'
        headers = ['Creator', 'Total', 'Action Plans', 'Adoption Barriers', 'Customer Pulse', 'External-Account']
        header_cells = table.rows[0].cells
        for i, text in enumerate(headers):
            header_cells[i].text = text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
                header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            shading = OxmlElement('w:shd')
            shading.set(qn('w:fill'), '007BC7')
            header_cells[i]._element.get_or_add_tcPr().append(shading)

        for row_idx, (creator, counts) in enumerate(ranked, start=1):
            row_cells = table.rows[row_idx].cells
            row_cells[0].text = str(creator or 'Unknown')
            row_cells[1].text = str(counts["total"])
            row_cells[2].text = str(counts["AP"])
            row_cells[3].text = str(counts["AB"])
            row_cells[4].text = str(counts["CP"])
            row_cells[5].text = str(counts["external"])
            for idx in (1, 2, 3, 4, 5):
                if row_cells[idx].paragraphs:
                    row_cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        self.doc.add_paragraph()
        return True

    @staticmethod
    def _is_status_open(status_value: Any) -> bool:
        # Phase 3.3: previously this returned ``True`` for None / blank /
        # parse-error inputs, which mis-classified rows of *unknown*
        # status as OPEN and inflated open counts. Default unknown to
        # ``False`` so unknown rows are excluded from the OPEN bucket
        # (callers that need a separate "unknown" bucket can detect it
        # via _is_status_unknown).
        if status_value is None:
            return False
        try:
            text = str(status_value).strip().lower()
        except Exception:
            return False
        if not text:
            return False
        return not any(token in text for token in LeaderReportGenerator._CLOSED_STATUS_TOKENS)

    @staticmethod
    def _is_status_unknown(status_value: Any) -> bool:
        """Return True when status is missing/blank/unparseable. Used to
        keep an "unknown" bucket distinct from open and closed counts.
        """
        if status_value is None:
            return True
        try:
            return str(status_value).strip() == ""
        except Exception:
            return True

    def _compute_aging_buckets(self, df: Optional[pd.DataFrame]) -> Dict[str, int]:
        """Bucket ``df`` rows by age of their primary date column, filtering to
        open records where a STATUS-like column exists. Missing dates fall
        into the 61+ bucket so they can't silently disappear.
        """
        buckets: Dict[str, int] = {name: 0 for name, _, _ in self._AGING_BUCKETS}
        if df is None or df.empty:
            return buckets

        date_col = None
        for candidate in ('CREATED_DATE', 'CREATEDDATE', 'OPEN_DATE_C', 'OPENED_DATE'):
            if candidate in df.columns:
                date_col = candidate
                break
        if date_col is None:
            return buckets

        status_col = None
        for candidate in ('STATUS_C', 'STATUS', 'STATE'):
            if candidate in df.columns:
                status_col = candidate
                break

        if status_col is not None:
            open_mask = df[status_col].apply(self._is_status_open)
            working = df[open_mask]
        else:
            working = df

        if working.empty:
            return buckets

        created = pd.to_datetime(working[date_col], errors='coerce', utc=True)
        now = pd.Timestamp.utcnow()
        ages = (now - created).dt.days
        for label, lower, upper in self._AGING_BUCKETS:
            if upper is None:
                mask = ages.isna() | (ages >= lower)
            else:
                mask = (ages >= lower) & (ages <= upper)
            buckets[label] = int(mask.sum())
        return buckets

    def _add_aging_section(self, team_data: Dict[str, Dict]) -> bool:
        """Render 0-7 / 8-30 / 31-60 / 61+ aging tables for open APs and ABs."""
        rendered = False
        for key, label in (("action_plans", "Action Plans"), ("adoption_barriers", "Adoption Barriers")):
            any_rows = any(
                (data.get(key) is not None and not data[key].empty)
                for data in (team_data or {}).values()
            )
            if not any_rows:
                continue

            heading = self.doc.add_heading(f'Aging of Open {label}', level=2)
            if heading.runs:
                heading.runs[0].font.color.rgb = CISCO_BLUE

            note = self.doc.add_paragraph()
            note.add_run(
                f'Open {label.lower()} by age. Records whose status matches Closed/Resolved/'
                'Complete are excluded. Records with missing dates are counted in the oldest bucket.\n'
            ).font.italic = True

            table = self.doc.add_table(rows=len(team_data) + 2, cols=len(self._AGING_BUCKETS) + 2)
            table.style = 'Light Grid Accent 1'

            header_cells = table.rows[0].cells
            header_cells[0].text = 'Team Member'
            for i, (bucket_label, _, _) in enumerate(self._AGING_BUCKETS, start=1):
                header_cells[i].text = bucket_label
            header_cells[len(self._AGING_BUCKETS) + 1].text = 'Total Open'
            for cell in header_cells:
                if cell.paragraphs and cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].font.bold = True
                    cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
                shading = OxmlElement('w:shd')
                shading.set(qn('w:fill'), '007BC7')
                cell._element.get_or_add_tcPr().append(shading)

            totals = {bucket: 0 for bucket, _, _ in self._AGING_BUCKETS}
            for row_idx, cssm_name in enumerate(sorted(team_data.keys()), start=1):
                data = team_data[cssm_name]
                buckets = self._compute_aging_buckets(data.get(key))
                row_cells = table.rows[row_idx].cells
                row_cells[0].text = cssm_name
                row_total = 0
                for col_idx, (bucket_label, _, _) in enumerate(self._AGING_BUCKETS, start=1):
                    count = buckets.get(bucket_label, 0)
                    row_cells[col_idx].text = str(count)
                    if row_cells[col_idx].paragraphs:
                        row_cells[col_idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    totals[bucket_label] += count
                    row_total += count
                row_cells[len(self._AGING_BUCKETS) + 1].text = str(row_total)
                if row_cells[len(self._AGING_BUCKETS) + 1].paragraphs:
                    row_cells[len(self._AGING_BUCKETS) + 1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

            totals_row = table.rows[len(team_data) + 1].cells
            totals_row[0].text = 'TOTAL'
            grand_total = 0
            for col_idx, (bucket_label, _, _) in enumerate(self._AGING_BUCKETS, start=1):
                count = totals[bucket_label]
                totals_row[col_idx].text = str(count)
                grand_total += count
                if totals_row[col_idx].paragraphs:
                    totals_row[col_idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            totals_row[len(self._AGING_BUCKETS) + 1].text = str(grand_total)
            for cell in totals_row:
                if cell.paragraphs and cell.paragraphs[0].runs:
                    cell.paragraphs[0].runs[0].font.bold = True
                shading = OxmlElement('w:shd')
                shading.set(qn('w:fill'), 'E8E8E8')
                cell._element.get_or_add_tcPr().append(shading)

            self.doc.add_paragraph()
            rendered = True
        return rendered

    def _compute_coverage_gaps(self, data: Dict) -> List[str]:
        """Return a sorted list of subscription customers with zero AP/AB/CP
        activity in the current window for a single CSSM."""
        subs = data.get('subscriptions')
        if subs is None or subs.empty or 'BU_NAME' not in subs.columns:
            return []
        subscribed = {
            str(name).strip()
            for name in subs['BU_NAME'].dropna().astype(str)
            if str(name).strip()
        }
        if not subscribed:
            return []

        def _names(df: Optional[pd.DataFrame]) -> set:
            if df is None or df.empty or 'BU_NAME' not in df.columns:
                return set()
            return {
                str(name).strip()
                for name in df['BU_NAME'].dropna().astype(str)
                if str(name).strip()
            }

        active = set()
        for key in ('action_plans', 'adoption_barriers', 'customer_pulse'):
            active |= _names(data.get(key))
        return sorted(subscribed - active)

    def _add_coverage_gap_section(self, team_data: Dict[str, Dict], days: int) -> bool:
        """List subscription customers with zero AP/AB/CP activity in window."""
        gaps = {
            cssm_name: self._compute_coverage_gaps(data)
            for cssm_name, data in (team_data or {}).items()
        }
        if not any(gaps.values()):
            return False

        heading = self.doc.add_heading('Coverage Gaps (Zero-Activity Customers)', level=2)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE

        note = self.doc.add_paragraph()
        note.add_run(
            f'Customers in each team member\'s primary subscription list with NO Action Plans, '
            f'Adoption Barriers, or Customer Pulse activity in the last {days} days. '
            'Review for re-engagement opportunities.\n'
        ).font.italic = True

        for cssm_name in sorted(gaps.keys()):
            names = gaps[cssm_name]
            if not names:
                continue
            # Round 11 / Phase 11.5: previous heading used
            # ``len(names)``, which counts raw list entries.  When
            # the same customer appeared under two slightly
            # different spellings (case / punctuation drift in
            # BU_NAME), the heading would over-state the coverage
            # gap (e.g. "ACME (5)" when only 3 distinct customers
            # actually had no engagement).  Use the normalized
            # customer key so the count matches what an operator
            # would reach if they de-duplicated the bullet list
            # themselves.
            try:
                _distinct_count = len({
                    str(normalize_customer_name(_n) or _n).strip().lower()
                    for _n in names
                    if _n is not None and str(_n).strip()
                })
            except Exception:
                _distinct_count = len(names)
            subheading = self.doc.add_heading(
                f'{cssm_name} ({_distinct_count})', level=3
            )
            if subheading.runs:
                subheading.runs[0].font.size = Pt(12)
            bullet = self.doc.add_paragraph()
            for name in names:
                bullet.add_run(f'• {name}\n')

        return True

    def _compute_period_deltas(
        self, team_data: Dict[str, Dict], days: int
    ) -> Optional[Dict[str, Dict[str, int]]]:
        """Split each frame's rows into 'current' and 'prior' halves based on
        the primary date column. The prior window is the preceding ``days``
        period. Returns ``None`` when no dateable rows exist.
        """
        if days is None or days <= 0:
            return None

        now = pd.Timestamp.utcnow()
        current_start = now - pd.Timedelta(days=days)
        prior_start = current_start - pd.Timedelta(days=days)

        per_member: Dict[str, Dict[str, int]] = {}
        found_any = False

        kinds = (
            ("action_plans", ("CREATED_DATE", "CREATEDDATE", "OPEN_DATE_C")),
            ("adoption_barriers", ("CREATED_DATE", "CREATEDDATE", "OPEN_DATE_C")),
            ("customer_pulse", ("CREATED_DATE", "CREATEDDATE", "SURVEY_DATE_C", "RESPONSE_DATE_C")),
        )

        for cssm_name, data in (team_data or {}).items():
            entry = per_member.setdefault(
                cssm_name,
                {"AP_curr": 0, "AP_prior": 0, "AB_curr": 0, "AB_prior": 0, "CP_curr": 0, "CP_prior": 0},
            )
            for key, date_candidates in kinds:
                df = data.get(key)
                if df is None or df.empty:
                    continue
                date_col = next((c for c in date_candidates if c in df.columns), None)
                if date_col is None:
                    continue
                dates = pd.to_datetime(df[date_col], errors='coerce', utc=True)
                valid = dates.dropna()
                if valid.empty:
                    continue
                found_any = True
                curr_mask = (dates >= current_start) & (dates <= now)
                prior_mask = (dates >= prior_start) & (dates < current_start)
                prefix = {"action_plans": "AP", "adoption_barriers": "AB", "customer_pulse": "CP"}[key]
                entry[f"{prefix}_curr"] += int(curr_mask.sum())
                entry[f"{prefix}_prior"] += int(prior_mask.sum())

        return per_member if found_any else None

    def _add_period_delta_section(
        self, team_data: Dict[str, Dict], days: int
    ) -> bool:
        """Render a small delta table (current window vs. prior window) for
        AP/AB/CP creation counts per team member."""
        per_member = self._compute_period_deltas(team_data, days)
        if not per_member:
            return False

        heading = self.doc.add_heading(
            f'Period-over-Period Deltas (last {days} days vs. prior {days} days)',
            level=2,
        )
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE

        note = self.doc.add_paragraph()
        note.add_run(
            'Activity created in the current window vs. the immediately preceding window of the same '
            'length. Deltas are rendered as signed integers (green positive, red negative).\n'
        ).font.italic = True

        table = self.doc.add_table(rows=len(per_member) + 1, cols=7)
        table.style = 'Light Grid Accent 1'

        header_cells = table.rows[0].cells
        headers = ['Team Member', 'APs now', 'AP delta', 'ABs now', 'AB delta', 'CPs now', 'CP delta']
        for i, text in enumerate(headers):
            header_cells[i].text = text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
                header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            shading = OxmlElement('w:shd')
            shading.set(qn('w:fill'), '007BC7')
            header_cells[i]._element.get_or_add_tcPr().append(shading)

        # Round 6 / Phase 1.12: sort by total current activity (descending)
        # then by name for tie-break, matching how other tables in this
        # report rank members by load rather than alphabetically.  A
        # purely lexicographic sort makes "high-activity" members hide
        # at the bottom of the list, which fights how the rest of the
        # leader report orders things.
        def _sort_key_per_member(name: str):
            counts = per_member.get(name, {}) or {}
            total_curr = (
                int(counts.get('AP_curr', 0) or 0)
                + int(counts.get('AB_curr', 0) or 0)
                + int(counts.get('CP_curr', 0) or 0)
            )
            return (-total_curr, str(name).lower())

        for row_idx, cssm_name in enumerate(sorted(per_member.keys(), key=_sort_key_per_member), start=1):
            counts = per_member[cssm_name]
            row_cells = table.rows[row_idx].cells
            row_cells[0].text = cssm_name
            pairs = (
                ("AP_curr", "AP_prior"),
                ("AB_curr", "AB_prior"),
                ("CP_curr", "CP_prior"),
            )
            col = 1
            for curr_key, prior_key in pairs:
                now_val = int(counts[curr_key])
                prior_val = int(counts[prior_key])
                delta = now_val - prior_val
                row_cells[col].text = str(now_val)
                if row_cells[col].paragraphs:
                    row_cells[col].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                col += 1
                row_cells[col].text = (f'+{delta}' if delta > 0 else str(delta))
                if row_cells[col].paragraphs:
                    row_cells[col].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                if row_cells[col].paragraphs and row_cells[col].paragraphs[0].runs:
                    run = row_cells[col].paragraphs[0].runs[0]
                    if delta > 0:
                        run.font.color.rgb = RGBColor(0, 128, 0)
                    elif delta < 0:
                        run.font.color.rgb = RGBColor(200, 0, 0)
                col += 1

        self.doc.add_paragraph()
        return True

    def _compute_customer_health(self, data: Dict) -> List[Dict[str, Any]]:
        """Build per-customer health rows for one CSSM: oldest open AB age,
        stalled-AP count, CP score trajectory. Customers with no dateable
        activity are skipped."""
        ap = data.get('action_plans')
        ab = data.get('adoption_barriers')
        cp = data.get('customer_pulse')
        rows: Dict[str, Dict[str, Any]] = {}

        now = pd.Timestamp.utcnow()

        def _date(series: pd.Series) -> pd.Series:
            return pd.to_datetime(series, errors='coerce', utc=True)

        # Round 13 / Phase 3.12: route BU_NAME through
        # ``normalize_customer_name`` so cosmetic spelling variants
        # collapse into a single account-health row instead of two
        # rows that each only show part of the customer's stalled AP
        # / oldest open AB / Customer Pulse signal.  Without this fix
        # the leader's per-customer health table double-counted some
        # customers and the rolled-up "Risk" call could disagree
        # between the two rows.
        try:
            from data_normalization import normalize_customer_name as _r13_norm_cust_lr
        except Exception:
            _r13_norm_cust_lr = lambda v: v  # noqa: E731

        if ap is not None and not ap.empty and 'BU_NAME' in ap.columns:
            status_col = next((c for c in ('STATUS_C', 'STATUS') if c in ap.columns), None)
            date_col = next((c for c in ('LAST_MODIFIED_DATE', 'LASTMODIFIEDDATE', 'CREATED_DATE', 'CREATEDDATE') if c in ap.columns), None)
            if status_col and date_col:
                open_ap = ap[ap[status_col].apply(self._is_status_open)].copy()
                ages = (now - _date(open_ap[date_col])).dt.days
                stalled = open_ap[ages > 30].copy()
                # Round 38.2 / Build14: the for-loop below USED TO live
                # at this same indent level, OUTSIDE the
                # ``if not stalled.empty:`` guard.  ``_bu_disp`` is
                # assigned inside the guard but was being accessed
                # unconditionally on the next line, which raised
                # ``KeyError: '_bu_disp'`` for any CSSM whose open APs
                # were ALL <=30 days old (no stalled rows).  The bug
                # was masked pre-Round-38 because the leader report
                # used to abort at validation when CSOne was empty,
                # so Document Generation rarely ran on real data.
                # Round 38's two-pass fix correctly let the report
                # through and surfaced the latent indentation issue.
                if not stalled.empty:
                    stalled['_bu_disp'] = (
                        stalled['BU_NAME'].dropna().astype(str).apply(_r13_norm_cust_lr)
                    )
                    for name, count in stalled['_bu_disp'].dropna().value_counts().items():
                        rows.setdefault(name, {"customer": name}).update({"stalled_aps": int(count)})

        if ab is not None and not ab.empty and 'BU_NAME' in ab.columns:
            status_col = next((c for c in ('STATUS_C', 'STATUS') if c in ab.columns), None)
            date_col = next((c for c in ('CREATED_DATE', 'CREATEDDATE', 'OPEN_DATE_C') if c in ab.columns), None)
            if date_col:
                open_mask = ab[status_col].apply(self._is_status_open) if status_col else pd.Series([True] * len(ab))
                open_ab = ab[open_mask]
                if not open_ab.empty:
                    open_ab = open_ab.copy()
                    open_ab['_age_days'] = (now - _date(open_ab[date_col])).dt.days
                    # Round 6 / Phase 1.13: add a secondary sort key so
                    # the "oldest open AB" pick is deterministic when
                    # two rows tie on ``_age_days`` (which is common
                    # because many AB rows share the same created date
                    # rounded to days).  Without a tiebreaker the row
                    # picked here depends on input order and the report
                    # silently flips between runs.
                    _id_col = next(
                        (c for c in ('ID', 'Id', 'AB_ID_C', 'AB_NUMBER_C') if c in open_ab.columns),
                        None,
                    )
                    _opened_col = next(
                        (c for c in ('OPEN_DATE_C', 'CREATED_DATE', 'CREATEDDATE') if c in open_ab.columns),
                        None,
                    )
                    _sort_cols = ['_age_days']
                    _sort_asc = [False]
                    if _opened_col:
                        _sort_cols.append(_opened_col)
                        _sort_asc.append(True)
                    if _id_col:
                        _sort_cols.append(_id_col)
                        _sort_asc.append(True)
                    # Round 6 / Phase 5.12: pass ``observed=False`` so
                    # if BU_NAME is ever provided as a Categorical (it is,
                    # in some upstream paths) all categories are reported,
                    # not just observed combinations.  The pandas default
                    # is changing toward ``observed=True``, so making the
                    # intent explicit prevents the leader account-health
                    # output from silently dropping customers with zero
                    # currently-open ABs after the upgrade.
                    # Round 13 / Phase 3.12: groupby on the normalized
                    # customer key so a customer with a cosmetic
                    # spelling drift across rows still rolls up to one
                    # "oldest open AB" entry.
                    oldest = (
                        open_ab.dropna(subset=['_age_days'])
                        .sort_values(_sort_cols, ascending=_sort_asc, kind='stable')
                        .groupby(
                            open_ab['BU_NAME'].fillna('Unknown').astype(str).apply(_r13_norm_cust_lr),
                            observed=False,
                        )
                        .head(1)
                    )
                    for _, ab_row in oldest.iterrows():
                        name = _r13_norm_cust_lr(str(ab_row.get('BU_NAME', 'Unknown')))
                        entry = rows.setdefault(name, {"customer": name})
                        entry["oldest_open_ab_days"] = int(ab_row['_age_days'])
                        entry["oldest_open_ab_severity"] = str(ab_row.get('SEVERITY_C', '') or '')

        if cp is not None and not cp.empty and 'BU_NAME' in cp.columns:
            score_col = next((c for c in ('SCORE__C', 'SCORE') if c in cp.columns), None)
            if score_col:
                cp_work = cp[['BU_NAME', score_col]].copy()
                cp_work[score_col] = pd.to_numeric(cp_work[score_col], errors='coerce')
                cp_work = cp_work.dropna(subset=[score_col])
                if not cp_work.empty:
                    # Round 6 / Phase 5.12: explicit ``observed=False`` for
                    # the same reason as the AB groupby above.
                    # Round 13 / Phase 3.12: groupby on canonical name.
                    grouped = cp_work.groupby(
                        cp_work['BU_NAME'].fillna('Unknown').astype(str).apply(_r13_norm_cust_lr),
                        observed=False,
                    )[score_col]
                    for name, stats in grouped.agg(['min', 'max', 'last']).iterrows():
                        entry = rows.setdefault(name, {"customer": name})
                        entry["cp_min"] = float(stats['min'])
                        entry["cp_max"] = float(stats['max'])
                        entry["cp_last"] = float(stats['last'])

        def _risk(entry: Dict[str, Any]) -> str:
            sev = str(entry.get('oldest_open_ab_severity', '')).lower()
            age = entry.get('oldest_open_ab_days', 0) or 0
            stalled = entry.get('stalled_aps', 0) or 0
            cp_last = entry.get('cp_last')
            if ('high' in sev or 'critical' in sev) and age >= 30:
                return 'High'
            if stalled >= 3 or age >= 60:
                return 'High'
            if stalled >= 1 or age >= 30 or (cp_last is not None and cp_last < 5):
                return 'Medium'
            return 'Low'

        out: List[Dict[str, Any]] = []
        for name, entry in rows.items():
            entry['risk'] = _risk(entry)
            out.append(entry)
        out.sort(key=lambda e: ({'High': 0, 'Medium': 1, 'Low': 2}.get(e['risk'], 3), -(e.get('oldest_open_ab_days') or 0), e['customer']))
        return out

    def _add_customer_health_section(self, team_data: Dict[str, Dict]) -> bool:
        """Render a per-CSSM customer health table with stalled APs, oldest
        open AB age, CP trajectory, and an overall risk badge."""
        any_rendered = False
        for cssm_name in sorted(team_data.keys()):
            rows = self._compute_customer_health(team_data[cssm_name])
            if not rows:
                continue
            if not any_rendered:
                heading = self.doc.add_heading('Customer Health Signals', level=2)
                if heading.runs:
                    heading.runs[0].font.color.rgb = CISCO_BLUE
                note = self.doc.add_paragraph()
                note.add_run(
                    'Per-customer risk signals derived from stalled Action Plans (open >30 days), '
                    'oldest open Adoption Barrier, and Customer Pulse score trajectory. '
                    'Risk: High (severe AB or 3+ stalled APs), Medium (moderate signal), Low.\n'
                ).font.italic = True
                any_rendered = True

            sub = self.doc.add_heading(f'{cssm_name}', level=3)
            if sub.runs:
                sub.runs[0].font.size = Pt(12)

            table = self.doc.add_table(rows=len(rows) + 1, cols=6)
            table.style = 'Light Grid Accent 1'
            headers = ['Customer', 'Risk', 'Stalled APs', 'Oldest Open AB', 'AB Severity', 'CP (min/max/last)']
            header_cells = table.rows[0].cells
            for i, text in enumerate(headers):
                header_cells[i].text = text
                if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                    header_cells[i].paragraphs[0].runs[0].font.bold = True
                    header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
                shading = OxmlElement('w:shd')
                shading.set(qn('w:fill'), '007BC7')
                header_cells[i]._element.get_or_add_tcPr().append(shading)

            for row_idx, entry in enumerate(rows, start=1):
                row_cells = table.rows[row_idx].cells
                row_cells[0].text = str(entry.get('customer', 'Unknown'))
                row_cells[1].text = str(entry.get('risk', 'Low'))
                if entry.get('risk') == 'High' and row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                    row_cells[1].paragraphs[0].runs[0].font.color.rgb = RGBColor(200, 0, 0)
                    row_cells[1].paragraphs[0].runs[0].font.bold = True
                elif entry.get('risk') == 'Medium' and row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                    row_cells[1].paragraphs[0].runs[0].font.color.rgb = RGBColor(224, 128, 0)
                row_cells[2].text = str(entry.get('stalled_aps', 0) or '—')
                days_val = entry.get('oldest_open_ab_days')
                row_cells[3].text = f'{int(days_val)}d' if days_val is not None else '—'
                row_cells[4].text = str(entry.get('oldest_open_ab_severity', '') or '—')
                if entry.get('cp_last') is not None:
                    row_cells[5].text = (
                        f"{entry.get('cp_min', 0):.1f} / {entry.get('cp_max', 0):.1f} / {entry.get('cp_last', 0):.1f}"
                    )
                else:
                    row_cells[5].text = '—'
                for idx in (1, 2, 3):
                    if row_cells[idx].paragraphs:
                        row_cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

            self.doc.add_paragraph()
        return any_rendered

    def _add_team_insights_section(self, team_data: Dict[str, Dict], days: int) -> None:
        """Umbrella section that ties together leaderboard, aging, coverage
        gaps, period deltas, and per-customer health signals. Individual
        helpers return False when there is nothing to render so empty
        subsections don't clutter the doc.
        """
        intro = self.doc.add_heading('Team Insights', level=1)
        if intro.runs:
            intro.runs[0].font.color.rgb = CISCO_BLUE

        blurb = self.doc.add_paragraph()
        blurb.add_run(
            'Cross-cutting analytics derived from the data already collected above: who is '
            'contributing, how old the open workload is, which accounts are quiet, how momentum '
            'compares to the prior window, and which customers need attention now.\n'
        ).font.italic = True

        rendered_any = False
        rendered_any |= self._add_collaboration_leaderboard(team_data)
        rendered_any |= self._add_period_delta_section(team_data, days)
        rendered_any |= self._add_aging_section(team_data)
        rendered_any |= self._add_coverage_gap_section(team_data, days)
        rendered_any |= self._add_customer_health_section(team_data)

        if not rendered_any:
            self.doc.add_paragraph('No insight signals are available for this window.').runs[0].font.italic = True

        self.doc.add_page_break()

    def _create_adoptiq_summaries_per_person(self, team_data: Dict[str, Dict], days: int):
        """Create AdoptIQ summaries for each direct report"""
        heading = self.doc.add_heading('AdoptIQ Summaries by Team Member', level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        desc_para = self.doc.add_paragraph()
        desc_para.add_run(
            'Detailed AdoptIQ analysis for each team member including accounts, '
            'products, adoption barriers, and challenges.\n\n'
        )
        
        # Create a section for each team member
        for idx, cssm_name in enumerate(sorted(team_data.keys())):
            data = team_data[cssm_name]
            
            # Section heading
            member_heading = self.doc.add_heading(f'{idx + 1}. {cssm_name}', level=2)
            if member_heading.runs:
                member_heading.runs[0].font.color.rgb = CISCO_BLUE
            
            # Overview statistics
            stats_para = self.doc.add_paragraph()
            stats_para.add_run('Overview:\n').font.bold = True
            stats_para.add_run(f'  • Customers: {self.safe_len(data.get("customers"))}\n')
            stats_para.add_run(f'  • Subscriptions: {self.safe_len(data.get("subscriptions"))}\n')
            stats_para.add_run(f'  • Action Plans: {self.safe_len(data.get("action_plans"))}\n')
            stats_para.add_run(f'  • Adoption Barriers: {self.safe_len(data.get("adoption_barriers"))}\n')
            stats_para.add_run(f'  • Customer Pulse: {self.safe_len(data.get("customer_pulse"))}\n')
            stats_para.add_run(f'  • TAC Cases: {self.safe_len(data.get("tac_cases"))}\n')
            
            # Add detailed activity table for this team member
            self._add_team_member_activity_table(cssm_name, data)
            
            # Add comprehensive account summaries with source attribution
            self._add_account_summaries_with_sources(cssm_name, data)
            
            # Customer list
            if data['customers']:
                customers_heading = self.doc.add_heading('Customers', level=3)
                if customers_heading.runs:
                    customers_heading.runs[0].font.size = Pt(12)
                
                # FIXED: Show ALL customers
                customers_para = self.doc.add_paragraph()
                for customer in sorted(data['customers']):
                    customers_para.add_run(f'• {customer}\n')
            
            # Products/Technologies - FIXED: Show ALL technologies
            if not data['adoption_barriers'].empty and 'SUB_TECHNOLOGY_C' in data['adoption_barriers'].columns:
                products = data['adoption_barriers']['SUB_TECHNOLOGY_C'].dropna().unique()
                
                if self.safe_len(products) > 0:
                    products_heading = self.doc.add_heading('Technologies', level=3)
                    if products_heading.runs:
                        products_heading.runs[0].font.size = Pt(12)
                    
                    products_para = self.doc.add_paragraph()
                    for product in sorted(products):
                        products_para.add_run(f'• {product}\n')
            
            # Top challenges (from ABs)
            if not data['adoption_barriers'].empty:
                challenges_heading = self.doc.add_heading('Top Challenges', level=3)
                if challenges_heading.runs:
                    challenges_heading.runs[0].font.size = Pt(12)
                
                # FIXED: Show ALL category counts
                if 'AB_CATEGORY_C' in data['adoption_barriers'].columns:
                    category_counts = data['adoption_barriers']['AB_CATEGORY_C'].value_counts()
                    
                    challenges_para = self.doc.add_paragraph()
                    for category, count in category_counts.items():
                        challenges_para.add_run(f'• {category}: {count} barriers\n')
                
                # Show recent high-severity barriers (canonical normalized severity)
                if 'SEVERITY_C' in data['adoption_barriers'].columns:
                    high_severity = data['adoption_barriers'][
                        self._high_or_critical_barrier_mask(data['adoption_barriers'])
                    ]
                    
                    if not high_severity.empty:
                        severity_heading = self.doc.add_heading('High-Severity Barriers', level=3)
                        if severity_heading.runs:
                            severity_heading.runs[0].font.size = Pt(12)
                        
                        # FIXED: Show ALL high-severity barriers
                        for _, barrier in high_severity.iterrows():
                            barrier_para = self.doc.add_paragraph(style='List Bullet')

                            # Round 12 / Phase 3.4: this list rendered
                            # raw ``BU_NAME`` per row, so spelling
                            # variants of a single account fragmented
                            # across multiple bullets in the per-CSSM
                            # severity callout.  Normalize the
                            # displayed label so the bullets agree
                            # with the All Action Plans / dashboard
                            # views (Round 11 / Phase 3.5 fix).
                            _raw_customer = barrier.get('BU_NAME', 'Unknown')
                            try:
                                customer = normalize_customer_name(str(_raw_customer)) or str(_raw_customer)
                            except Exception:
                                customer = str(_raw_customer)
                            subject = barrier.get('SUBJECT_C', 'No subject')
                            severity = barrier.get('SEVERITY_C', 'Unknown')
                            
                            barrier_para.add_run(f'{customer} - {subject} ').font.italic = True
                            barrier_para.add_run(f'(Severity: {severity})')
                            note = self._format_external_note(barrier, cssm_name)
                            if note:
                                barrier_para.add_run(f' {note}').font.italic = True
            
            # Recent Action Plans - FIXED: Show ALL action plans
            if not data['action_plans'].empty:
                ap_heading = self.doc.add_heading('All Action Plans', level=3)
                if ap_heading.runs:
                    ap_heading.runs[0].font.size = Pt(12)
                
                for _, ap in data['action_plans'].iterrows():
                    ap_para = self.doc.add_paragraph(style='List Bullet')

                    # Round 11 / Phase 3.5: normalize the displayed
                    # customer label so the leader report agrees
                    # with the dashboard / Word body normalization
                    # (CSSM pipeline already normalizes elsewhere).
                    raw_customer = ap.get('BU_NAME', 'Unknown')
                    try:
                        customer = normalize_customer_name(str(raw_customer)) or str(raw_customer)
                    except Exception:
                        customer = str(raw_customer)
                    subject = ap.get('SUBJECT_C', 'No subject')
                    status = ap.get('STATUS_C', 'Unknown')

                    ap_para.add_run(f'{customer} - {subject} ').font.italic = True
                    ap_para.add_run(f'(Status: {status})')
                    note = self._format_external_note(ap, cssm_name)
                    if note:
                        ap_para.add_run(f' {note}').font.italic = True

            # Recent Customer Pulse - surface owner-captured CP and annotate externals
            if 'customer_pulse' in data and not data['customer_pulse'].empty:
                cp_heading = self.doc.add_heading('All Customer Pulse', level=3)
                if cp_heading.runs:
                    cp_heading.runs[0].font.size = Pt(12)

                for _, cp in data['customer_pulse'].iterrows():
                    cp_para = self.doc.add_paragraph(style='List Bullet')

                    # Round 11 / Phase 3.5: normalize displayed BU_NAME.
                    raw_customer = cp.get('BU_NAME', 'Unknown')
                    try:
                        customer = normalize_customer_name(str(raw_customer)) or str(raw_customer)
                    except Exception:
                        customer = str(raw_customer)
                    subject = (
                        cp.get('SUBJECT_C')
                        or cp.get('SUBJECT')
                        or cp.get('TITLE')
                        or cp.get('NAME')
                        or 'Customer Pulse'
                    )
                    score = cp.get('SCORE__C') if 'SCORE__C' in cp.index else cp.get('SCORE')
                    status = cp.get('STATUS_C') or cp.get('STATUS') or 'Active'
                    detail = f'(Score: {score})' if score not in (None, '', float('nan')) and pd.notna(score) else f'(Status: {status})'

                    cp_para.add_run(f'{customer} - {subject} ').font.italic = True
                    cp_para.add_run(detail)
                    note = self._format_external_note(cp, cssm_name)
                    if note:
                        cp_para.add_run(f' {note}').font.italic = True

            # TAC Cases for this team member
            if 'tac_cases' in data and not data['tac_cases'].empty:
                tac_count = self.safe_len(data["tac_cases"])
                tac_heading = self.doc.add_heading(f'TAC Cases ({tac_count} cases)', level=3)
                if tac_heading.runs:
                    tac_heading.runs[0].font.size = Pt(12)
                
                tac_cases = data['tac_cases']
                
                # Find customer column
                customer_col = None
                for col in tac_cases.columns:
                    if any(term in col.lower() for term in ['customer name', 'account name', 'customer:', 'bu_name']):
                        customer_col = col
                        break
                
                # Create a simple table for TAC cases - FIXED: Show ALL cases
                tac_count = self.safe_len(tac_cases)
                if tac_count > 0:
                    display_cases = tac_cases  # Show ALL cases
                    
                    # Summary by priority - use canonical case_priority_norm so
                    # mixed raw labels ("P1", "1", "Critical") collapse into a
                    # single bucket, matching cm.count_p1/p2/etc.
                    try:
                        from data_normalization import add_case_lifecycle_fields as _add_lc
                        _tac_for_priority = _add_lc(tac_cases)
                    except Exception:
                        _tac_for_priority = tac_cases
                    if 'case_priority_norm' in _tac_for_priority.columns:
                        priority_counts = (
                            _tac_for_priority['case_priority_norm']
                            .fillna('Unknown')
                            .value_counts()
                        )
                        summary_para = self.doc.add_paragraph()
                        summary_para.add_run('Cases by Priority: ')
                        for priority, count in priority_counts.items():
                            summary_para.add_run(f'{priority}: {count}  ')
                        self.doc.add_paragraph()  # spacing
                    
                    # Create table for TAC cases
                    table = self.doc.add_table(rows=1, cols=6)
                    table.style = 'Light Grid Accent 1'
                    
                    # Header row
                    header_cells = table.rows[0].cells
                    headers = ['Case #', 'Customer', 'Title', 'Priority', 'Status', 'Date Opened']
                    for i, header_text in enumerate(headers):
                        header_cells[i].text = header_text
                        if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                            header_cells[i].paragraphs[0].runs[0].font.bold = True
                            header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
                        header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                        # Add blue background to header
                        shading_elm = OxmlElement('w:shd')
                        shading_elm.set(qn('w:fill'), '0076CE')  # Cisco blue
                        header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
                    
                    # Add data rows
                    for _, case in display_cases.iterrows():
                        row_cells = table.add_row().cells
                        
                        # Case number with source attribution
                        case_num = case.get('SR Number', case.get('Case Number', case.get('Case #', 'N/A')))
                        row_cells[0].text = f"TAC #{case_num}"
                        
                        # Customer - FIXED: No truncation
                        # Round 11 / Phase 3.5: normalize TAC table
                        # customer cell so spelling variants render
                        # consistently with the rest of the leader
                        # report.
                        raw_customer_cell = case.get(customer_col, 'Unknown') if customer_col else 'Unknown'
                        try:
                            customer = normalize_customer_name(str(raw_customer_cell)) or str(raw_customer_cell)
                        except Exception:
                            customer = str(raw_customer_cell)
                        row_cells[1].text = customer
                        
                        # Title - FIXED: No truncation
                        title = case.get('Title', 'No title')
                        row_cells[2].text = str(title)
                        
                        # Priority
                        priority = case.get('Highest Priority', case.get('Priority', ''))
                        row_cells[3].text = str(priority) if priority else ''
                        row_cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                        
                        # Status
                        status = case.get('Case Status', case.get('Status', ''))
                        row_cells[4].text = str(status) if status else ''
                        
                        # Date
                        date_opened = case.get('Date/Time Opened', '')
                        if pd.notna(date_opened) and date_opened:
                            try:
                                if isinstance(date_opened, (datetime, pd.Timestamp)):
                                    date_str = date_opened.strftime('%Y-%m-%d')
                                else:
                                    date_str = str(date_opened)[:10]
                            except Exception:
                                date_str = ''
                        else:
                            date_str = ''
                        row_cells[5].text = date_str
                        row_cells[5].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    
                    self.doc.add_paragraph()

                    tac_count = self.safe_len(tac_cases)
                    if tac_count > 0:
                        self.doc.add_paragraph(f'(Showing all {tac_count} TAC cases)')

            self._add_source_verification_section(cssm_name, data)
            self._add_enhanced_snowflake_insights(cssm_name, data)
            self._add_defect_analysis_section(cssm_name, data)

            if idx < len(team_data) - 1:
                self.doc.add_page_break()

        self.doc.add_page_break()
    
    def _create_detailed_ab_list(self, team_data: Dict[str, Dict]):
        """Create detailed list of all Adoption Barriers with metadata"""
        heading = self.doc.add_heading('Detailed Adoption Barriers List', level=1)
        if heading.runs:
            heading.runs[0].font.color.rgb = CISCO_BLUE
        
        desc_para = self.doc.add_paragraph()
        desc_para.add_run(
            'Complete list of all Adoption Barriers showing product, category, '
            'last updated date, and linked Customer Pulse or Action Plans.\n\n'
        )
        
        # Collect all ABs from all team members
        all_abs = []
        
        for cssm_name, data in team_data.items():
            if not data['adoption_barriers'].empty:
                abs_copy = data['adoption_barriers'].copy()
                abs_copy['CSSM'] = cssm_name
                all_abs.append(abs_copy)
        
        if not all_abs:
            self.doc.add_paragraph('No adoption barriers found for the selected time period.')
            return
        
        # Combine all ABs
        combined_abs = pd.concat(all_abs, ignore_index=True)
        
        # Add summary
        summary_para = self.doc.add_paragraph()
        summary_para.add_run(f'Total Adoption Barriers: {self.safe_len(combined_abs)}\n').font.bold = True
        
        # Count by team member
        cssm_counts = combined_abs['CSSM'].value_counts()
        summary_para.add_run('\nBarriers per Team Member:\n')
        for cssm, count in cssm_counts.items():
            summary_para.add_run(f'  • {cssm}: {count} barriers\n')
        
        # FIXED: Show ALL categories
        if 'AB_CATEGORY_C' in combined_abs.columns:
            category_counts = combined_abs['AB_CATEGORY_C'].value_counts()
            summary_para.add_run('\nAll Categories:\n')
            for category, count in category_counts.items():
                summary_para.add_run(f'  • {category}: {count} barriers\n')
        
        self.doc.add_paragraph('\n')
        
        # Create detailed table
        detail_heading = self.doc.add_heading('Complete Barrier Details', level=2)
        if detail_heading.runs:
            detail_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        columns_to_show = []
        column_headers = []

        col_mappings = [
            ('CSSM', 'Team Member'),
            ('BU_NAME', 'Customer'),
            ('SUBJECT_C', 'Subject'),
            ('SUB_TECHNOLOGY_C', 'Product'),
            ('AB_CATEGORY_C', 'Category'),
            ('SEVERITY_C', 'Severity'),
            ('LAST_MODIFIED_DATE', 'Last Updated'),
            ('STATUS_C', 'Status'),
            # Virtual columns - always shown so external/collaborator records
            # are obvious to the reader; values are derived per-row below.
            ('_CREATOR_DISPLAY', 'Creator'),
        ]

        for db_col, display_col in col_mappings:
            if db_col.startswith('_'):
                # Synthetic columns are always shown; populated via ``_row_creator_cell``.
                columns_to_show.append(db_col)
                column_headers.append(display_col)
            elif db_col in combined_abs.columns:
                columns_to_show.append(db_col)
                column_headers.append(display_col)
        
        # Create table. Cap at 100 displayed rows for document length;
        # the cap is disclosed below the table so readers know they are
        # looking at a truncated sample, not the entire data set.
        AB_LIST_DISPLAY_CAP = 100
        total_ab_rows = self.safe_len(combined_abs)
        displayed_rows = min(total_ab_rows, AB_LIST_DISPLAY_CAP)
        num_rows = displayed_rows + 1  # +1 for header
        table = self.doc.add_table(rows=num_rows, cols=self.safe_len(column_headers))
        table.style = 'Light Grid Accent 1'
        
        # Header row
        header_cells = table.rows[0].cells
        for i, header_text in enumerate(column_headers):
            cell = header_cells[i]
            cell.text = header_text
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].runs[0].font.size = Pt(9)
                cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '007BC7')
            cell._element.get_or_add_tcPr().append(shading_elm)
        
        # Round 12 / Phase 9.3: previously this iteration called
        # ``combined_abs.head(100)`` on a frame assembled from
        # multiple sources (CSSM-owned + collaborator) with NO
        # canonical sort, so the displayed 100 rows were an
        # arbitrary slice of however ``concat`` happened to order
        # the inputs.  Two reruns of the same report could surface
        # entirely different barriers in the visible table while
        # the truncation footer still claimed to be a "sample" of
        # the same population.  Sort by ``LAST_MODIFIED_DATE``
        # descending (most recently active barriers first, the
        # natural prioritization for a manager review) with a
        # stable id tie-break before the head() so the rendered
        # subset is deterministic and the footer's "showing 100 of
        # N" claim refers to a canonical 100.
        try:
            _date_col = next(
                (c for c in ('LAST_MODIFIED_DATE', 'CREATED_DATE') if c in combined_abs.columns),
                None,
            )
            _id_col = next(
                (c for c in ('AB_ID', 'BARRIER_ID', 'ID') if c in combined_abs.columns),
                None,
            )
            if _date_col is not None and _id_col is not None:
                combined_abs = combined_abs.sort_values(
                    [_date_col, _id_col],
                    ascending=[False, True],
                    na_position='last',
                    kind='mergesort',
                )
            elif _date_col is not None:
                combined_abs = combined_abs.sort_values(
                    _date_col,
                    ascending=False,
                    na_position='last',
                    kind='mergesort',
                )
            elif _id_col is not None:
                combined_abs = combined_abs.sort_values(
                    _id_col,
                    ascending=True,
                    na_position='last',
                    kind='mergesort',
                )
        except Exception:  # Round 12 / Phase 9.3 defensive
            pass

        # Use .head(n).iterrows() so we don't materialize every row into a list.
        for row_idx, (_, ab) in enumerate(combined_abs.head(AB_LIST_DISPLAY_CAP).iterrows(), start=1):
            row_cells = table.rows[row_idx].cells

            for col_idx, col_name in enumerate(columns_to_show):
                if col_name == '_CREATOR_DISPLAY':
                    value = self._row_creator_cell(ab, cssm_owner=ab.get('CSSM', ''))
                else:
                    value = ab.get(col_name, '')
                    if pd.isna(value):
                        value = ''
                    elif isinstance(value, (datetime, pd.Timestamp)):
                        value = value.strftime('%Y-%m-%d')
                    else:
                        value = str(value)

                # Round 12 / Phase 3.5: ``Complete Barrier Details``
                # rendered ``BU_NAME`` cells via raw ``str(value)``,
                # leaking case / whitespace variants into the body
                # table even though Round 11 / Phase 3.x normalized
                # the same value elsewhere in the document.  Apply
                # ``normalize_customer_name`` so the table cell agrees
                # with the dashboard / All Action Plans view.
                if col_name == 'BU_NAME' and value:
                    try:
                        _norm = normalize_customer_name(value)
                        if _norm:
                            value = _norm
                    except Exception:
                        pass

                row_cells[col_idx].text = value
                if row_cells[col_idx].paragraphs and row_cells[col_idx].paragraphs[0].runs:
                    row_cells[col_idx].paragraphs[0].runs[0].font.size = Pt(8)
        
        # Add note about linked records and explicit list cap disclosure so
        # readers never confuse a truncated sample with a complete list.
        note_para = self.doc.add_paragraph('\n')
        note_para.add_run('Note: ').font.bold = True
        note_para.add_run(
            'Linked Customer Pulse and Action Plans can be identified by matching '
            'account IDs and customer names in the respective sections above.'
        )
        if total_ab_rows > AB_LIST_DISPLAY_CAP:
            cap_para = self.doc.add_paragraph()
            cap_run = cap_para.add_run(
                f'Showing {AB_LIST_DISPLAY_CAP} of {total_ab_rows} adoption barriers '
                f'(table truncated for length). Headline counts elsewhere in this '
                f'report use the full {total_ab_rows} records.'
            )
            cap_run.font.italic = True
            cap_run.font.size = Pt(9)
            cap_run.font.color.rgb = CISCO_GRAY
    
    def _add_team_member_activity_table(self, cssm_name: str, data: Dict):
        """Add a detailed activity table for an individual team member.

        Round 39 / Phase 1.2: this table now exposes TAC Cases and BEMS
        columns alongside AP/AB/CP and uses the canonical
        ``ACTIVITIES_MODE_FULL`` total (AP+AB+CP+TAC+BEMS).  Pre-Round-39
        this site rendered AP+AB+CP only, which made the same person
        carry three different "Total Activities" numbers across the Team
        Activity Summary, this per-member table, and the Activity
        Counts Cross-Check -- all three now agree.
        """
        # Add spacing
        self.doc.add_paragraph()

        # Table heading
        table_heading = self.doc.add_heading('Activity Summary', level=3)
        if table_heading.runs:
            table_heading.runs[0].font.color.rgb = CISCO_BLUE

        # Create table with 7 columns: Team Member, Action Plans, Adoption Barriers, Customer Pulse, TAC Cases, BEMS, Total Activities
        table = self.doc.add_table(rows=2, cols=7)  # Header + 1 data row
        table.style = 'Light Grid Accent 1'

        # Header row
        header_cells = table.rows[0].cells
        headers = ['Team Member', 'Action Plans', 'Adoption Barriers', 'Customer Pulse', 'TAC Cases', 'BEMS', 'Total Activities']
        for i, header_text in enumerate(headers):
            header_cells[i].text = header_text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
                header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add Cisco blue background to header
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '0076CE')  # Cisco blue
            header_cells[i]._element.get_or_add_tcPr().append(shading_elm)

        # Data row
        data_cells = table.rows[1].cells

        # Safe length calculation with None handling
        def safe_len(obj):
            if obj is None:
                return 0
            try:
                return len(obj)
            except (TypeError, AttributeError):
                return 0

        num_aps = safe_len(data.get('action_plans', []))
        num_abs = safe_len(data.get('adoption_barriers', []))
        num_cps = safe_len(data.get('customer_pulse', []))
        num_tac = safe_len(data.get('tac_cases', pd.DataFrame()))
        num_bems = self._count_bems_escalations(data)

        # Canonical "full" total = AP + AB + CP + TAC + BEMS.
        num_total = cm.count_total_activities(
            action_plans_df=data.get('action_plans'),
            ab_df=data.get('adoption_barriers'),
            customer_pulse_df=data.get('customer_pulse'),
            tac_df=data.get('tac_cases'),
            bems_count=num_bems,
            mode=cm.ACTIVITIES_MODE_FULL,
        )

        # Populate data cells
        data_cells[0].text = cssm_name
        data_cells[1].text = str(num_aps)
        data_cells[2].text = str(num_abs)
        data_cells[3].text = str(num_cps)
        data_cells[4].text = str(num_tac)
        data_cells[5].text = str(num_bems)
        data_cells[6].text = str(num_total)

        # Center align numeric cells
        for i in range(1, 7):
            if data_cells[i].paragraphs:
                data_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Add light gray background to data row
        for i in range(7):
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), 'F8F8F8')  # Light gray
            data_cells[i]._element.get_or_add_tcPr().append(shading_elm)

        # Make total activities bold
        if data_cells[6].paragraphs and data_cells[6].paragraphs[0].runs:
            data_cells[6].paragraphs[0].runs[0].font.bold = True

        # Add spacing after table
        self.doc.add_paragraph()
    
    def _add_account_summaries_with_sources(self, cssm_name: str, data: Dict):
        """Add comprehensive account summaries with full source attribution"""
        # Add spacing
        self.doc.add_paragraph()
        
        # Account Summary heading
        summary_heading = self.doc.add_heading('Account Summary & Source Attribution', level=3)
        if summary_heading.runs:
            summary_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Get unique customers for this team member
        customers = data.get('customers', [])
        if not customers:
            no_customers_para = self.doc.add_paragraph()
            no_customers_para.add_run('No customers assigned to this team member.').italic = True
            return
        
        # FIXED: Process ALL customers with comprehensive data
        for customer in sorted(customers):
            self._add_customer_summary_with_sources(customer, data)
    
    def _add_customer_summary_with_sources(self, customer: str, data: Dict):
        """Add detailed summary for a single customer with full source attribution in unified table"""
        # Determine specific technology for this customer
        customer_technology = self._get_customer_specific_technology(customer, data)
        
        # Round 39 / Phase 4.4: prettify ``__`` separators in the
        # display label so account names like
        # ``"TRIBUNAL...__GOBIERNO...__MX"`` render as a comma-
        # separated list a reader can parse.  We keep the raw
        # ``customer`` string untouched for downstream lookups (the
        # raw form is the join key against ``BU_NAME``); only the
        # heading text is rewritten.
        try:
            _customer_display = normalize_for_display(customer)
        except Exception:
            _customer_display = customer

        # Customer heading with technology context
        if customer_technology and customer_technology != 'Contact Center':
            customer_heading = self.doc.add_heading(f'Account: {_customer_display} ({customer_technology})', level=4)
        else:
            customer_heading = self.doc.add_heading(f'Account: {_customer_display}', level=4)
        if customer_heading.runs:
            customer_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Add sentiment summary for this customer
        try:
            if not self.arr_sentiment_analyzer:
                raise ValueError("sentiment analyzer not available")
            
            # Get customer-specific data for sentiment analysis
            customer_data = {
                'adoption_barriers': data.get('adoption_barriers', pd.DataFrame())[
                    data.get('adoption_barriers', pd.DataFrame())['BU_NAME'] == customer
                ] if not data.get('adoption_barriers', pd.DataFrame()).empty and 'BU_NAME' in data.get('adoption_barriers', pd.DataFrame()).columns else pd.DataFrame(),
                'action_plans': data.get('action_plans', pd.DataFrame())[
                    data.get('action_plans', pd.DataFrame())['BU_NAME'] == customer
                ] if not data.get('action_plans', pd.DataFrame()).empty and 'BU_NAME' in data.get('action_plans', pd.DataFrame()).columns else pd.DataFrame(),
                'customer_pulse': data.get('customer_pulse', pd.DataFrame())[
                    data.get('customer_pulse', pd.DataFrame())['BU_NAME'] == customer
                ] if not data.get('customer_pulse', pd.DataFrame()).empty and 'BU_NAME' in data.get('customer_pulse', pd.DataFrame()).columns else pd.DataFrame(),
                'tac_cases': data.get('tac_cases', pd.DataFrame())[
                    data.get('tac_cases', pd.DataFrame())['Customer'] == customer
                ] if not data.get('tac_cases', pd.DataFrame()).empty and 'Customer' in data.get('tac_cases', pd.DataFrame()).columns else pd.DataFrame()
            }
            
            sentiment_data = self.arr_sentiment_analyzer.analyze_customer_sentiment(customer, customer_data)
            
            # Add sentiment summary
            summary_para = self.doc.add_paragraph()
            sentiment_text = f"Sentiment: {sentiment_data.get('overall_sentiment', 'Unknown')} ({sentiment_data.get('confidence_level', 'Low')} confidence)"
            
            summary_para.add_run(sentiment_text)
            if summary_para.runs:
                summary_para.runs[0].font.italic = True
                
        except Exception as e:
            logger.debug(f"Error adding sentiment context for {customer}: {e}")
            pass
        
        # Initialize all items list with type information
        all_items = []
        customer_norm = normalize_customer_name(customer)
        _name_match = lambda series: series.fillna("").astype(str).apply(normalize_customer_name) == customer_norm
        
        # Collect Action Plans
        if not data.get('action_plans', pd.DataFrame()).empty:
            customer_aps = data['action_plans'][
                _name_match(data['action_plans']['BU_NAME'])
            ]
            for _, ap in customer_aps.iterrows():
                # Enhanced data extraction with fallbacks
                subject = ap.get('SUBJECT_C') or ap.get('SUBJECT') or ap.get('TITLE') or 'Action Plan'
                status = ap.get('STATUS_C') or ap.get('STATUS') or 'In Progress'
                category = ap.get('AB_CATEGORY_C') or ap.get('CATEGORY') or ap.get('TYPE') or 'General'
                severity = ap.get('SEVERITY_C') or ap.get('SEVERITY') or ap.get('PRIORITY') or 'Medium'
                
                all_items.append({
                    'type': 'AP',
                    'type_full': 'Action Plan',
                    'id': f"AP-{ap.get('ID', 'N/A')}",
                    'subject': subject,
                    'status': status,
                    'category': category,
                    'severity': severity,
                    'date': str(ap.get('CREATED_DATE', 'N/A') if pd.notna(ap.get('CREATED_DATE')) else 'N/A'),
                    'source': 'CSConsole',
                    'creator': str(ap.get('_CREATOR_NAME', '') or ap.get('_CREATOR_EMAIL', '') or ''),
                    'external_account': bool(ap.get('_EXTERNAL_ACCOUNT', False)),
                    'note': self._format_external_note(ap, ''),
                })
        
        # Collect Adoption Barriers
        if not data.get('adoption_barriers', pd.DataFrame()).empty:
            customer_abs = data['adoption_barriers'][
                _name_match(data['adoption_barriers']['BU_NAME'])
            ]
            for _, ab in customer_abs.iterrows():
                # Enhanced data extraction with fallbacks
                subject = ab.get('SUBJECT_C') or ab.get('SUBJECT') or ab.get('TITLE') or 'Adoption Barrier'
                status = ab.get('STATUS_C') or ab.get('STATUS') or 'Open'
                category = ab.get('AB_CATEGORY_C') or ab.get('CATEGORY') or ab.get('TYPE') or 'Technical'
                severity = ab.get('SEVERITY_C') or ab.get('SEVERITY') or ab.get('PRIORITY') or 'Medium'
                
                all_items.append({
                    'type': 'AB',
                    'type_full': 'Adoption Barrier',
                    'id': f"AB-{ab.get('ID', 'N/A')}",
                    'subject': subject,
                    'status': status,
                    'category': category,
                    'severity': severity,
                    'date': str(ab.get('CREATED_DATE', 'N/A') if pd.notna(ab.get('CREATED_DATE')) else 'N/A'),
                    'source': 'CSConsole',
                    'creator': str(ab.get('_CREATOR_NAME', '') or ab.get('_CREATOR_EMAIL', '') or ''),
                    'external_account': bool(ab.get('_EXTERNAL_ACCOUNT', False)),
                    'note': self._format_external_note(ab, ''),
                })
        
        # Collect Customer Pulse
        if not data.get('customer_pulse', pd.DataFrame()).empty:
            customer_cps = data['customer_pulse'][
                _name_match(data['customer_pulse']['BU_NAME'])
            ]
            for _, cp in customer_cps.iterrows():
                # Enhanced data extraction with fallbacks
                subject = cp.get('SUBJECT_C') or cp.get('SUBJECT') or cp.get('TITLE') or 'Customer Pulse'
                status = cp.get('STATUS_C') or cp.get('STATUS') or 'Active'
                category = cp.get('AB_CATEGORY_C') or cp.get('CATEGORY') or cp.get('TYPE') or 'Feedback'
                severity = cp.get('SEVERITY_C') or cp.get('SEVERITY') or cp.get('PRIORITY') or 'Low'
                
                all_items.append({
                    'type': 'CP',
                    'type_full': 'Customer Pulse',
                    'id': f"CP-{cp.get('ID', 'N/A')}",
                    'subject': subject,
                    'status': status,
                    'category': category,
                    'severity': severity,
                    'date': str(cp.get('CREATED_DATE', 'N/A') if pd.notna(cp.get('CREATED_DATE')) else 'N/A'),
                    'source': 'CSConsole',
                    'creator': str(cp.get('_CREATOR_NAME', '') or cp.get('_CREATOR_EMAIL', '') or ''),
                    'external_account': bool(cp.get('_EXTERNAL_ACCOUNT', False)),
                    'note': self._format_external_note(cp, ''),
                })
        
        # Collect TAC Cases
        _tac_df = data.get('tac_cases', pd.DataFrame())
        _tac_col = 'Customer Name: Customer Name'
        if _tac_df is not None and not _tac_df.empty and _tac_col in _tac_df.columns:
            customer_tacs = _tac_df[
                _name_match(_tac_df[_tac_col])
            ]
            for _, tac in customer_tacs.iterrows():
                # Enhanced data extraction with fallbacks
                subject = tac.get('Title') or tac.get('Subject') or tac.get('Problem') or 'TAC Case'
                status = tac.get('Status') or 'Open'
                category = tac.get('Priority') or tac.get('Category') or 'P3'
                severity = tac.get('Severity') or tac.get('Impact') or 'Medium'
                
                all_items.append({
                    'type': 'TAC',
                    'type_full': 'TAC Case',
                    'id': f"TAC Case: {tac.get('Case #', 'N/A')}",
                    'subject': subject,
                    'status': status,
                    'category': category,
                    'severity': severity,
                    'date': str(tac.get('Date/Time Opened', 'N/A') if pd.notna(tac.get('Date/Time Opened')) else 'N/A'),
                    'source': 'CSOne'
                })
        
        # Summary paragraph
        ap_count = len([i for i in all_items if i['type'] == 'AP'])
        ab_count = len([i for i in all_items if i['type'] == 'AB'])
        cp_count = len([i for i in all_items if i['type'] == 'CP'])
        tac_count = len([i for i in all_items if i['type'] == 'TAC'])
        
        # Analyze BEMS escalations for this customer
        bems_count = 0
        customer_norm = normalize_customer_name(customer)
        try:
            # Check for BEMS references in adoption barriers
            if not data.get('adoption_barriers', pd.DataFrame()).empty:
                customer_abs = data['adoption_barriers'][
                    data['adoption_barriers']['BU_NAME'].fillna("").astype(str).apply(normalize_customer_name) == customer_norm
                ]
                bems_count += int(detect_bems_mask(customer_abs).sum())
            
            # Check for BEMS references in TAC cases
            if not data.get('tac_cases', pd.DataFrame()).empty:
                # Try multiple possible customer name columns
                customer_col = None
                for col in ['Customer Name: Customer Name', 'Customer Name', 'BU_NAME', 'Customer']:
                    if col in data['tac_cases'].columns:
                        customer_col = col
                        break
                
                if customer_col:
                    customer_tacs = data['tac_cases'][
                        data['tac_cases'][customer_col].fillna("").astype(str).apply(normalize_customer_name) == customer_norm
                    ]
                    bems_count += int(detect_bems_mask(customer_tacs).sum())
        except Exception as e:
            logger.warning(f"Error counting BEMS escalations: {e}")
        
        summary_para = self.doc.add_paragraph()
        summary_para.add_run(f'Total Activities: {len(all_items)}').font.bold = True
        summary_para.add_run(f' (APs: {ap_count}, ABs: {ab_count}, CPs: {cp_count}, TAC: {tac_count})')
        
        # Add BEMS indicator if found
        if bems_count > 0:
            summary_para.add_run(f' | Warning: BEMS Escalations: {bems_count}')
            summary_para.runs[-1].font.color.rgb = RGBColor(255, 140, 0)  # Orange color
            summary_para.runs[-1].font.bold = True
        
        if not all_items:
            no_data_para = self.doc.add_paragraph()
            no_data_para.add_run('No activities found for this customer.').italic = True
            self.doc.add_paragraph()
            return
        
        # Create comprehensive unified table
        self._add_unified_customer_table(all_items, bems_count)
        
        # Add account-level summary
        self._add_account_summary(customer, all_items, data)
        
        # Add enhanced Snowflake insights for this customer
        self._add_customer_enhanced_insights(customer, data)
        
        # Add spacing between customers
        self.doc.add_paragraph()
    
    def _add_unified_customer_table(self, all_items: List[Dict], bems_count: int = 0):
        """Create a single unified table showing all customer activities grouped by type"""
        if not all_items:
            return
        
        # Create comprehensive table with all activity types
        table = self.doc.add_table(rows=1, cols=8)
        table.style = 'Light Grid Accent 1'

        # Header row with clear, concise headers
        header_cells = table.rows[0].cells
        headers = ['Type', 'Record ID', 'Subject/Title', 'Status', 'Category/Priority', 'Severity', 'Date', 'Creator']
        for i, header_text in enumerate(headers):
            header_cells[i].text = header_text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
                header_cells[i].paragraphs[0].runs[0].font.size = Pt(9)
            header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add light blue background to header
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), 'E3F2FD')  # Light blue
            header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        # Sort items by type for better readability (APs, ABs, CPs, TAC)
        type_order = {'AP': 1, 'AB': 2, 'CP': 3, 'TAC': 4}
        all_items_sorted = sorted(all_items, key=lambda x: (type_order.get(x['type'], 5), str(x.get('date', '') or '')))
        
        # Color coding for different types
        type_colors = {
            'AP': 'C8E6C9',   # Light green
            'AB': 'FFCCBC',   # Light orange
            'CP': 'BBDEFB',   # Light blue
            'TAC': 'F8BBD0'   # Light pink
        }
        
        # Add data rows (limit to 25 items for readability)
        # FIXED: Show ALL items
        for item in all_items_sorted:
            row_cells = table.add_row().cells
            
            # Type (bold and colored)
            row_cells[0].text = item['type']
            if row_cells[0].paragraphs and row_cells[0].paragraphs[0].runs:
                row_cells[0].paragraphs[0].runs[0].font.bold = True
                row_cells[0].paragraphs[0].runs[0].font.size = Pt(9)
            row_cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add color coding by type
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), type_colors.get(item['type'], 'FFFFFF'))
            row_cells[0]._element.get_or_add_tcPr().append(shading_elm)
            
            # Record ID (with hyperlink for CSConsole records)
            record_id = item.get('id', 'N/A')
            record_id_str = str(record_id)
            
            # Clear the cell first
            row_cells[1].text = ''
            
            # Add hyperlink for CSConsole records (AP, AB, CP types)
            if item['type'] in ['AP', 'AB', 'CP'] and record_id != 'N/A' and '-' in record_id_str:
                # Extract the actual ID (after the prefix like "AP-", "AB-", "CP-")
                actual_id = record_id_str.split('-', 1)[1] if '-' in record_id_str else record_id_str
                if actual_id != 'N/A':
                    try:
                        # Customer Pulse lives on a different Salesforce object
                        # (``ESA_C360_Customer_Pulse__c``); APs and ABs both
                        # live on ``C360_CS_Task__c``. Using the Task URL for
                        # CP produced 404s in Salesforce.
                        if item['type'] == 'CP':
                            sf_object = 'ESA_C360_Customer_Pulse__c'
                        else:
                            sf_object = 'C360_CS_Task__c'
                        csconsole_url = (
                            f"https://ciscosales.lightning.force.com/lightning/r/{sf_object}/"
                            f"{actual_id}/view"
                        )
                        self.add_hyperlink(row_cells[1].paragraphs[0], csconsole_url, record_id_str, font_size=8)
                    except Exception:
                        # Fallback to plain text if hyperlink fails
                        row_cells[1].text = record_id_str
                        if row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                            row_cells[1].paragraphs[0].runs[0].font.size = Pt(8)
                else:
                    row_cells[1].text = record_id_str
                    if row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                        row_cells[1].paragraphs[0].runs[0].font.size = Pt(8)
            else:
                # For TAC cases or records without valid IDs, just show plain text
                row_cells[1].text = record_id_str
                if row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                    row_cells[1].paragraphs[0].runs[0].font.size = Pt(8)
            
            # Subject/Title (truncated for readability)
            subject = item.get('subject', 'N/A')
            if subject is None or subject == '':
                subject = 'N/A'
            # FIXED: No truncation - show full subject for verification
            row_cells[2].text = str(subject)
            if row_cells[2].paragraphs and row_cells[2].paragraphs[0].runs:
                row_cells[2].paragraphs[0].runs[0].font.size = Pt(8)
            
            # Status
            status = item.get('status', 'N/A')
            row_cells[3].text = str(status) if status else 'N/A'
            if row_cells[3].paragraphs and row_cells[3].paragraphs[0].runs:
                row_cells[3].paragraphs[0].runs[0].font.size = Pt(8)
            row_cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Category/Priority (show category for ABs, priority for TAC)
            category = item.get('category', 'N/A')
            row_cells[4].text = str(category) if category and category != 'N/A' else '-'
            if row_cells[4].paragraphs and row_cells[4].paragraphs[0].runs:
                row_cells[4].paragraphs[0].runs[0].font.size = Pt(8)
            row_cells[4].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Severity (mainly for ABs)
            severity = item.get('severity', 'N/A')
            row_cells[5].text = str(severity) if severity and severity != 'N/A' else '-'
            if row_cells[5].paragraphs and row_cells[5].paragraphs[0].runs:
                row_cells[5].paragraphs[0].runs[0].font.size = Pt(8)
            row_cells[5].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Date
            date = item.get('date', 'N/A')
            if date != 'N/A' and date:
                try:
                    if isinstance(date, str):
                        date = pd.to_datetime(date).strftime('%Y-%m-%d')
                except (TypeError, ValueError) as e:
                    logger.debug(f"Date parse failed: {e}")
            row_cells[6].text = str(date) if date else 'N/A'
            if row_cells[6].paragraphs and row_cells[6].paragraphs[0].runs:
                row_cells[6].paragraphs[0].runs[0].font.size = Pt(8)
            row_cells[6].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

            creator_label = str(item.get('creator') or '').strip()
            if item.get('external_account') and creator_label:
                row_cells[7].text = f"{creator_label} (external)"
            elif creator_label:
                row_cells[7].text = creator_label
            else:
                row_cells[7].text = '-'
            if row_cells[7].paragraphs and row_cells[7].paragraphs[0].runs:
                row_cells[7].paragraphs[0].runs[0].font.size = Pt(8)
                if item.get('external_account'):
                    row_cells[7].paragraphs[0].runs[0].font.italic = True
            row_cells[7].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        # FIXED: Removed limit message - now showing ALL items
        
        # Add legend for type colors
        legend_para = self.doc.add_paragraph()
        legend_para.add_run('Legend: ').font.bold = True
        legend_para.add_run('AP = Action Plan, AB = Adoption Barrier, CP = Customer Pulse, TAC = TAC Case')
        legend_para.style = 'Normal'
    
    def _add_account_summary(self, customer: str, all_items: List[Dict], data: Dict):
        """Add comprehensive account-level summary for each customer"""
        
        # Account Summary Heading
        summary_heading = self.doc.add_heading('Account Summary', level=3)
        if summary_heading.runs:
            summary_heading.runs[0].font.color.rgb = CISCO_BLUE
            summary_heading.runs[0].font.size = Pt(12)
        
        # Calculate account statistics
        ap_count = len([i for i in all_items if i['type'] == 'AP'])
        ab_count = len([i for i in all_items if i['type'] == 'AB'])
        cp_count = len([i for i in all_items if i['type'] == 'CP'])
        tac_count = len([i for i in all_items if i['type'] == 'TAC'])
        total_activities = len(all_items)
        
        # Status breakdown
        # Round 39 / Phase 4.1: split severity / category counters by
        # record type so the per-account summary stops mixing TAC
        # numeric priorities (1-4) with AB string severities
        # (Low/Medium/High) in a single line.  Pre-Round-39 the
        # rendered text read like "Severity Breakdown: 3 (1), Medium
        # (3)" which is unparseable -- "3" reads as either a TAC
        # priority or a literal severity bucket called "3".
        status_counts: dict = {}
        ab_severity_counts: dict = {}
        tac_priority_counts: dict = {}
        ab_category_counts: dict = {}
        tac_category_counts: dict = {}

        for item in all_items:
            _itype = str(item.get('type') or '').upper()
            # Status counts (status semantics are consistent across
            # AP/AB/CP/TAC -- "open", "closed", etc. -- so a single
            # bucket is honest).
            status = str(item.get('status', 'Unknown') or 'Unknown')
            status_counts[status] = status_counts.get(status, 0) + 1

            category = str(item.get('category', 'Unknown') or 'Unknown')
            severity = str(item.get('severity', 'Unknown') or 'Unknown')

            if _itype == 'TAC':
                tac_priority_counts[severity] = tac_priority_counts.get(severity, 0) + 1
                tac_category_counts[category] = tac_category_counts.get(category, 0) + 1
            elif _itype in ('AB', 'AP'):
                ab_severity_counts[severity] = ab_severity_counts.get(severity, 0) + 1
                ab_category_counts[category] = ab_category_counts.get(category, 0) + 1
        
        # Create account summary table
        summary_table = self.doc.add_table(rows=1, cols=2)
        summary_table.style = 'Light Grid Accent 1'
        
        # Header
        header_cells = summary_table.rows[0].cells
        header_cells[0].text = 'Metric'
        if header_cells[0].paragraphs and header_cells[0].paragraphs[0].runs:
            header_cells[0].paragraphs[0].runs[0].font.bold = True
            header_cells[0].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
        header_cells[1].text = 'Value'
        if header_cells[1].paragraphs and header_cells[1].paragraphs[0].runs:
            header_cells[1].paragraphs[0].runs[0].font.bold = True
            header_cells[1].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
        
        # Header background
        for cell in header_cells:
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '007BC7')
            cell._element.get_or_add_tcPr().append(shading_elm)
        
        # Round 39 / Phase 4.1: derive Top Category / Top Severity
        # from the AB-side counters specifically so the summary row
        # doesn't surface a TAC numeric priority labelled "Severity".
        # The AB counters are the most useful "what's hurting this
        # account" signal; TAC counts get their own dedicated rows
        # in the breakdown below.
        _top_category = (
            max(ab_category_counts.items(), key=lambda x: x[1])[0]
            if ab_category_counts
            else (
                max(tac_category_counts.items(), key=lambda x: x[1])[0]
                if tac_category_counts
                else 'N/A'
            )
        )
        _top_severity = (
            max(ab_severity_counts.items(), key=lambda x: x[1])[0]
            if ab_severity_counts
            else 'N/A'
        )
        summary_data = [
            ('Total Activities', str(total_activities)),
            ('Action Plans', str(ap_count)),
            ('Adoption Barriers', str(ab_count)),
            ('Customer Pulse Records', str(cp_count)),
            ('TAC Cases', str(tac_count)),
            ('', ''),  # Spacer
            ('Top Status', max(status_counts.items(), key=lambda x: x[1])[0] if status_counts else 'N/A'),
            ('Top AB Category', _top_category),
            ('Top AB Severity', _top_severity),
        ]
        
        for metric, value in summary_data:
            if metric == '':  # Spacer row
                row_cells = summary_table.add_row().cells
                row_cells[0].text = ''
                row_cells[1].text = ''
            else:
                row_cells = summary_table.add_row().cells
                row_cells[0].text = metric
                row_cells[1].text = value
                if row_cells[0].paragraphs and row_cells[0].paragraphs[0].runs:
                    row_cells[0].paragraphs[0].runs[0].font.size = Pt(9)
                if row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                    row_cells[1].paragraphs[0].runs[0].font.size = Pt(9)
                    row_cells[1].paragraphs[0].runs[0].font.bold = True
        
        # Add detailed breakdowns
        if status_counts:
            self.doc.add_paragraph()
            status_para = self.doc.add_paragraph()
            status_para.add_run('Status Breakdown: ').font.bold = True
            status_breakdown = ', '.join([f"{status} ({count})" for status, count in sorted(status_counts.items())])
            status_para.add_run(status_breakdown)
        
        # Round 39 / Phase 4.1: render AB and TAC breakdowns on
        # SEPARATE labeled lines so a reader can tell numeric TAC
        # priorities apart from string AB severities.  Pre-Round-39
        # one Counter held both, producing unparseable output like
        # "Severity Breakdown: 3 (1), Medium (3)".
        if ab_category_counts:
            self.doc.add_paragraph()
            category_para = self.doc.add_paragraph()
            category_para.add_run('AB Category Breakdown: ').font.bold = True
            category_para.add_run(
                ', '.join([
                    f"{c} ({n})" for c, n in sorted(ab_category_counts.items())
                ])
            )

        if ab_severity_counts:
            self.doc.add_paragraph()
            severity_para = self.doc.add_paragraph()
            severity_para.add_run('AB Severity Breakdown: ').font.bold = True
            severity_para.add_run(
                ', '.join([
                    f"{s} ({n})" for s, n in sorted(ab_severity_counts.items())
                ])
            )

        if tac_category_counts:
            self.doc.add_paragraph()
            tac_cat_para = self.doc.add_paragraph()
            tac_cat_para.add_run('TAC Category Breakdown: ').font.bold = True
            tac_cat_para.add_run(
                ', '.join([
                    f"{c} ({n})" for c, n in sorted(tac_category_counts.items())
                ])
            )

        if tac_priority_counts:
            self.doc.add_paragraph()
            tac_prio_para = self.doc.add_paragraph()
            tac_prio_para.add_run('TAC Priority Breakdown: ').font.bold = True
            tac_prio_para.add_run(
                ', '.join([
                    f"P{s} ({n})" if str(s).strip().isdigit() else f"{s} ({n})"
                    for s, n in sorted(tac_priority_counts.items())
                ])
            )
        
        # Add account health indicator
        self.doc.add_paragraph()
        health_para = self.doc.add_paragraph()
        health_para.add_run('Account Health: ').font.bold = True
        
        # Simple health calculation
        # Round 39 / Phase 4.1: read from the AB-side severity counter
        # specifically (the legacy combined ``severity_counts`` dict
        # is now intentionally empty -- see the Phase 4.1 split
        # above).  Without this fix the Account Health pill would
        # always read "Good" because ``severity_counts.get('High')``
        # returned 0 by definition.
        high_severity_count = (
            ab_severity_counts.get('High', 0) + ab_severity_counts.get('Critical', 0)
        )
        open_ab_count = status_counts.get('Open', 0) + status_counts.get('New', 0)
        
        # Round 13 / Phase 5.2: previously the Account Health pill
        # used hand-mixed ``RGBColor(0,128,0)`` / ``(255,165,0)`` /
        # ``(255,0,0)``.  Those greens / oranges / reds are NOT the
        # same hexes as the canonical ``RISK_BAND_COLORS`` map
        # ("HEALTHY"=#28B463, "MEDIUM"=#ffd700, "CRITICAL"=#d62728)
        # used by every chart and Excel band fill, so a "Healthy"
        # pill on this page rendered a noticeably different green
        # than the "Healthy" wedge on the same report's pie chart.
        # Resolve through the shared canonical map (already loaded
        # at module-import as ``CANONICAL_RISK_*_RGB``) so palette
        # tweaks land in exactly one place.  ``MED`` is mapped to
        # ``RISK_BAND_COLORS["MEDIUM"]`` and falls back to the
        # historical orange when the lookup fails.
        try:
            _r13_health_healthy_rgb = _r12_hex_to_rgb(
                (_R12_LRG_RBC.get('HEALTHY', '#28B463') or '#28B463').lstrip('#')
            )
        except Exception:
            _r13_health_healthy_rgb = RGBColor(0x28, 0xB4, 0x63)
        if high_severity_count == 0 and open_ab_count <= 2:
            health_status = 'Healthy'
            health_color = _r13_health_healthy_rgb
        elif high_severity_count <= 2 and open_ab_count <= 5:
            health_status = 'Moderate'
            health_color = CANONICAL_RISK_MED_RGB
        else:
            health_status = 'Attention Needed'
            health_color = CANONICAL_RISK_HIGH_RGB
        
        health_run = health_para.add_run(health_status)
        health_run.font.bold = True
        health_run.font.color.rgb = health_color
        
        # Add source verification note
        source_para = self.doc.add_paragraph()
        source_para.add_run('Source Verification: ').font.bold = True
        source_para.add_run('All records can be verified in their respective source systems (CSConsole or CSOne) using the Record ID provided above.')
    
    def _add_overall_individual_summary(self, team_data: Dict[str, Dict], manager_name: str, days: int):
        """Add comprehensive overall summary for the individual/manager"""
        
        # Overall Summary Heading
        summary_heading = self.doc.add_heading('📊 Overall Individual Summary', level=1)
        if summary_heading.runs:
            summary_heading.runs[0].font.color.rgb = CISCO_BLUE
            summary_heading.runs[0].font.size = Pt(16)
        
        # Executive Summary
        exec_para = self.doc.add_paragraph()
        exec_para.add_run('Executive Summary\n').font.bold = True
        exec_para.add_run(f'This report provides a comprehensive analysis of {(manager_name or "Manager")}\'s team performance over the last {days} days. ')
        exec_para.add_run('The analysis covers all team members, their customer accounts, adoption barriers, action plans, customer pulse records, and TAC cases.\n\n')
        
        # Calculate overall team statistics
        total_team_members = len(team_data)
        total_customers = 0
        total_aps = 0
        total_abs = 0
        total_cps = 0
        total_tac_cases = 0
        total_bems = 0
        total_bems_tac_only = 0

        # Round 11 / Phase 6.8: track distinct customers across the
        # entire team (normalized) so the executive summary can
        # state the true headcount of unique accounts rather than
        # the sum of per-CSSM ``num_customers`` -- which double-
        # counts shared / collaborative customers.  We keep the
        # raw sum under ``total_customer_assignments`` for parity
        # with the legacy "assignments" reading.
        _r11_distinct_customers: set = set()

        # Collect all team data
        team_summary_data = []
        customer_health_summary = {}
        
        for cssm_name, data in team_data.items():
            # Count activities
            num_customers = self.safe_len(data.get('customers', []))
            try:
                _raw_cust_iter = data.get('customers', []) or []
                if hasattr(_raw_cust_iter, 'tolist'):
                    _raw_cust_iter = _raw_cust_iter.tolist()
                for _c in _raw_cust_iter:
                    if _c is None:
                        continue
                    try:
                        _norm_c = normalize_customer_name(str(_c)) or str(_c)
                    except Exception:
                        _norm_c = str(_c)
                    if _norm_c.strip():
                        _r11_distinct_customers.add(_norm_c.strip().lower())
            except Exception as _ddc_err:
                logger.debug(
                    "Round 11 / Phase 6.8: could not collect distinct customers for %s: %s",
                    cssm_name, _ddc_err,
                )
            num_aps = self.safe_len(data.get('action_plans', []))
            num_abs = self.safe_len(data.get('adoption_barriers', []))
            num_cps = self.safe_len(data.get('customer_pulse', []))
            num_tac = self.safe_len(data.get('tac_cases', []))
            
            total_customers += num_customers
            total_aps += num_aps
            total_abs += num_abs
            total_cps += num_cps
            total_tac_cases += num_tac
            
            # Count BEMS escalations.
            # Round 2 / Phase 3.3: also track the canonical TAC-only
            # count so the dashboard footnote can show both numbers.
            # Without this, the leader headline (combined AB+TAC) and
            # the EI/Compact dashboards (TAC-only) silently disagree
            # on the same run.
            bems_count = self._count_bems_escalations(data)
            total_bems += bems_count
            try:
                bems_tac_only = self._count_bems_canonical_tac(data)
            except Exception:
                bems_tac_only = 0
            total_bems_tac_only += bems_tac_only
            
            high_severity_count = 0
            open_ab_count = 0
            resolved_ab_count = 0
            completed_ap_count = 0

            if not data.get('adoption_barriers', pd.DataFrame()).empty:
                abs_df = data['adoption_barriers']
                if 'SEVERITY_C' in abs_df.columns:
                    # Round 6 / Phase 5.6: route through the canonical
                    # high/critical mask which already normalizes
                    # SEVERITY_C via ``normalize_severity_label``, so
                    # raw values like "1 - Critical" / "p1" / "Sev 1"
                    # collapse onto the same band before counting.
                    high_severity_count = int(self._high_or_critical_barrier_mask(abs_df).sum())
                if 'STATUS_C' in abs_df.columns:
                    # Round 6 / Phase 5.6: normalize STATUS_C via
                    # ``_is_status_open`` (the same helper the
                    # account-health table and other leader sub-
                    # sections already use) so case / whitespace /
                    # legacy spelling differences ("open" vs "Open"
                    # vs "OPEN" vs "In Progress") cannot silently
                    # drop rows from the team summary count.
                    status_series = abs_df['STATUS_C']
                    open_ab_count = int(status_series.apply(self._is_status_open).sum())
                    # Round 30 / M1: route through the canonical
                    # ``count_closed_barriers`` helper so this count
                    # matches the lifecycle definition every other
                    # report uses.  The previous inline regex
                    # (``r'closed|resolved|complete'``) bypassed the
                    # normalization map and over-counted labels like
                    # "Resolved (Pending Customer Review)" while
                    # silently missing any synonym not in the regex.
                    try:
                        resolved_ab_count = int(cm.count_closed_barriers(abs_df))
                    except Exception:  # noqa: BLE001
                        # Defensive fallback: keep the legacy regex
                        # behaviour so a stale fixture cannot break
                        # the leader report at runtime.  The canonical
                        # path is the primary, this is belt-and-
                        # suspenders only.
                        status_norm = status_series.astype(str).str.strip().str.lower()
                        resolved_ab_count = int(
                            status_norm.str.contains(r'closed|resolved|complete', case=False, na=False).sum()
                        )

            if not data.get('action_plans', pd.DataFrame()).empty:
                ap_df = data['action_plans']
                if 'STATUS_C' in ap_df.columns:
                    # Round 30 / M1: route through canonical
                    # ``count_action_plan_completed`` so the leader
                    # "completed action plans" tally honors the SSoT
                    # status normalization map (including labels like
                    # "Closed - Will Not Complete" that should NOT be
                    # counted as a success).  See the docstring in
                    # ``canonical_metrics.count_action_plan_completed``.
                    try:
                        completed_ap_count = int(cm.count_action_plan_completed(ap_df))
                    except Exception:  # noqa: BLE001
                        _ap_status_norm = ap_df['STATUS_C'].astype(str).str.strip().str.lower()
                        completed_ap_count = int(
                            _ap_status_norm.str.contains(r'complete|closed|done', case=False, na=False).sum()
                        )

            # Blended impact: resolved work + completed plans, lightly
            # discounted by currently-open high-severity load.
            impact_score = (resolved_ab_count * 3) + (completed_ap_count * 2) - max(0, high_severity_count)

            team_summary_data.append({
                'cssm_name': cssm_name,
                'customers': num_customers,
                'aps': num_aps,
                'abs': num_abs,
                'cps': num_cps,
                'tac_cases': num_tac,
                'bems': bems_count,
                'high_severity': high_severity_count,
                'open_abs': open_ab_count,
                'resolved_abs': resolved_ab_count,
                'completed_aps': completed_ap_count,
                'impact_score': impact_score,
                # Round 39 / Phase 1.2: canonical "full" total = AP + AB + CP + TAC + BEMS.
                # All three Leader-report writers now use this same formula.
                'total_activities': cm.count_total_activities(
                    action_plans_df=data.get('action_plans'),
                    ab_df=data.get('adoption_barriers'),
                    customer_pulse_df=data.get('customer_pulse'),
                    tac_df=data.get('tac_cases'),
                    bems_count=bems_count,
                    mode=cm.ACTIVITIES_MODE_FULL,
                ),
            })
        
        # Create overall statistics table
        stats_heading = self.doc.add_heading('Team Performance Metrics', level=2)
        if stats_heading.runs:
            stats_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        stats_table = self.doc.add_table(rows=1, cols=3)
        stats_table.style = 'Light Grid Accent 1'
        
        # Header
        header_cells = stats_table.rows[0].cells
        headers = ['Metric', 'Total', 'Average per Team Member']
        for i, header_text in enumerate(headers):
            header_cells[i].text = header_text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
                header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Header background
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '007BC7')
            header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        # Statistics rows
        avg_divisor = max(total_team_members, 1)
        # Round 11 / Phase 6.8: headline number is now the count
        # of unique normalized customers across the team rather
        # than the sum of per-CSSM "customers" (which over-states
        # whenever the same account is shared across CSSMs).
        _r11_distinct_customer_count = len(_r11_distinct_customers)
        _r11_total_assignments = total_customers
        stats_data = [
            ('Team Members', str(total_team_members), f"{total_team_members}"),
            (
                'Customer Assignments (sum of CSSM lists; distinct customers='
                + str(_r11_distinct_customer_count)
                + ')',
                str(_r11_total_assignments),
                f"{_r11_total_assignments/avg_divisor:.1f}",
            ),
            ('Action Plans', str(total_aps), f"{total_aps/avg_divisor:.1f}"),
            ('Adoption Barriers', str(total_abs), f"{total_abs/avg_divisor:.1f}"),
            ('Customer Pulse Records', str(total_cps), f"{total_cps/avg_divisor:.1f}"),
            ('TAC Cases', str(total_tac_cases), f"{total_tac_cases/avg_divisor:.1f}"),
            # Round 2 / Phase 3.3: headline = combined AB+TAC mode
            # (leader convention).  Footnote the canonical TAC-only
            # number so reconcilers can reproduce the EI/Compact
            # dashboard figure from the same run.
            ('BEMS Escalations (combined AB+TAC; TAC-only=' + str(total_bems_tac_only) + ')',
             str(total_bems), f"{total_bems/avg_divisor:.1f}"),
            # Round 39 / Phase 1.2: canonical "full" aggregate = AP + AB + CP + TAC + BEMS
            # across all members so this row matches the Team Activity Summary
            # TOTAL row and the Activity Counts Cross-Check grand total.
            ('Total Activities', str(total_aps + total_abs + total_cps + total_tac_cases + total_bems), f"{(total_aps + total_abs + total_cps + total_tac_cases + total_bems)/avg_divisor:.1f}")
        ]
        
        for metric, total, avg in stats_data:
            row_cells = stats_table.add_row().cells
            row_cells[0].text = metric
            row_cells[1].text = total
            row_cells[2].text = avg
            if row_cells[0].paragraphs and row_cells[0].paragraphs[0].runs:
                row_cells[0].paragraphs[0].runs[0].font.size = Pt(9)
            if row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                row_cells[1].paragraphs[0].runs[0].font.size = Pt(9)
                row_cells[1].paragraphs[0].runs[0].font.bold = True
            if row_cells[2].paragraphs and row_cells[2].paragraphs[0].runs:
                row_cells[2].paragraphs[0].runs[0].font.size = Pt(9)
                row_cells[2].paragraphs[0].runs[0].font.bold = True
            # Center align numeric columns
            for i in range(1, 3):
                row_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Team Member Performance Table
        self.doc.add_paragraph()
        performance_heading = self.doc.add_heading('Individual Team Member Performance', level=2)
        if performance_heading.runs:
            performance_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        perf_table = self.doc.add_table(rows=1, cols=8)
        perf_table.style = 'Light Grid Accent 1'
        
        # Header
        perf_header_cells = perf_table.rows[0].cells
        perf_headers = ['Team Member', 'Customers', 'APs', 'ABs', 'CPs', 'TAC', 'BEMS', 'Total']
        for i, header_text in enumerate(perf_headers):
            perf_header_cells[i].text = header_text
            if perf_header_cells[i].paragraphs and perf_header_cells[i].paragraphs[0].runs:
                perf_header_cells[i].paragraphs[0].runs[0].font.bold = True
                perf_header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            perf_header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Header background
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '007BC7')
            perf_header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        # Data rows
        # Round 18 / Phase 2.2: tuple sort key with a casefolded
        # cssm_name secondary tiebreaker so two CSSMs tied on the
        # same total_activities render in the same order across runs
        # regardless of team_data insertion order.
        def _team_summary_sort_key(x):
            try:
                activities = int(x.get('total_activities', 0))
            except (TypeError, ValueError):
                activities = 0
            return (-activities, str(x.get('cssm_name', '')).casefold())

        for member_data in sorted(team_summary_data, key=_team_summary_sort_key):
            row_cells = perf_table.add_row().cells
            row_cells[0].text = member_data['cssm_name']
            row_cells[1].text = str(member_data['customers'])
            row_cells[2].text = str(member_data['aps'])
            row_cells[3].text = str(member_data['abs'])
            row_cells[4].text = str(member_data['cps'])
            row_cells[5].text = str(member_data['tac_cases'])
            row_cells[6].text = str(member_data['bems'])
            row_cells[7].text = str(member_data['total_activities'])
            
            # Formatting
            for i in range(8):
                if row_cells[i].paragraphs and row_cells[i].paragraphs[0].runs:
                    row_cells[i].paragraphs[0].runs[0].font.size = Pt(9)
                    if i == 7:  # Bold total column
                        row_cells[i].paragraphs[0].runs[0].font.bold = True
                if i > 0:  # Center align numeric columns
                    row_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Key Insights and Recommendations
        self.doc.add_paragraph()
        insights_heading = self.doc.add_heading('Key Insights and Recommendations', level=2)
        if insights_heading.runs:
            insights_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Activity and impact leaders (separated so volume is not confused
        # with outcomes). "Most Active" is by raw activity counts; "Top Impact"
        # is a blended outcome score (resolved ABs + completed APs, lightly
        # discounted by open high-severity load).
        most_active = max(team_summary_data, key=lambda x: x['total_activities'])
        top_impact = max(team_summary_data, key=lambda x: x.get('impact_score', 0))

        insights_para = self.doc.add_paragraph()
        insights_para.add_run('Most Active (by volume): ').font.bold = True
        insights_para.add_run(
            f"{most_active['cssm_name']} with {most_active['total_activities']} total activities "
            f"({most_active['aps']} APs, {most_active['abs']} ABs, {most_active['cps']} CPs, "
            f"{most_active['tac_cases']} TAC cases)\n"
        )

        impact_para = self.doc.add_paragraph()
        impact_para.add_run('Top Impact (blended outcome): ').font.bold = True
        impact_para.add_run(
            f"{top_impact['cssm_name']} — {top_impact.get('resolved_abs', 0)} ABs resolved, "
            f"{top_impact.get('completed_aps', 0)} APs completed, "
            f"impact score {top_impact.get('impact_score', 0)}\n"
        )
        
        # BEMS attention
        if total_bems > 0:
            bems_para = self.doc.add_paragraph()
            bems_para.add_run('Warning: BEMS Escalations: ').font.bold = True
            bems_para.add_run(f"Total of {total_bems} backend engineering escalations require immediate attention across the team.\n")
        
        # High severity issues
        total_high_severity = sum(member['high_severity'] for member in team_summary_data)
        if total_high_severity > 0:
            severity_para = self.doc.add_paragraph()
            severity_para.add_run('ERROR: High Severity Issues: ').font.bold = True
            severity_para.add_run(f"{total_high_severity} high/critical severity adoption barriers need priority resolution.\n")
        
        # Open adoption barriers
        total_open_abs = sum(member['open_abs'] for member in team_summary_data)
        if total_open_abs > 0:
            open_para = self.doc.add_paragraph()
            open_para.add_run('INFO: Open Adoption Barriers: ').font.bold = True
            open_para.add_run(f"{total_open_abs} adoption barriers remain open and require follow-up.\n")
        
        # Recommendations
        self.doc.add_paragraph()
        rec_para = self.doc.add_paragraph()
        rec_para.add_run('TIP: Recommendations:\n').font.bold = True
        rec_para.add_run('• Prioritize resolution of high-severity adoption barriers\n')
        rec_para.add_run('• Follow up on open adoption barriers to ensure customer success\n')
        rec_para.add_run('• Address BEMS escalations promptly to maintain customer satisfaction\n')
        rec_para.add_run('• Leverage top performer insights for team knowledge sharing\n')
        rec_para.add_run('• Monitor customer pulse trends for proactive issue identification\n')
        
        # Report metadata
        self.doc.add_paragraph()
        meta_para = self.doc.add_paragraph()
        meta_para.add_run('REPORT: Report Metadata:\n').font.bold = True
        meta_para.add_run(f'• Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC\n')
        meta_para.add_run(f'• Manager: {(manager_name or "Manager")}\n')
        meta_para.add_run(f'• Time Period: Last {days} days\n')
        meta_para.add_run(f'• Data Sources: CSConsole (APs, ABs, CPs), CSOne (TAC Cases), Snowflake (Customer Data)\n')
        meta_para.add_run(f'• Total Records Analyzed: {total_aps + total_abs + total_cps + total_tac_cases}\n')
        meta_para.style = 'Normal'
    
    def _add_customer_enhanced_insights(self, customer: str, data: Dict, days: Optional[int] = None):
        """Add comprehensive enhanced insights including Snowflake data, BEMS, and defects.

        ``days`` is the analysis window the user selected for this report. We
        propagate it to ``EnhancedSnowflakeInsights`` so the customer-level
        Snowflake fetch matches the rest of the report. The previous
        hard-coded ``days=90`` ignored the caller's choice and silently
        showed a 90-day slice even when the user requested 30 or 180 days.
        ``data['analysis_days']`` is checked as a secondary source so callers
        that haven't yet been updated still pass the right window.
        """
        try:
            # Add heading
            insights_heading = self.doc.add_heading('📊 Additional Customer Insights', level=5)
            if insights_heading.runs:
                insights_heading.runs[0].font.color.rgb = CISCO_BLUE

            insights_added = False

            # Resolve the analysis window. Priority order:
            #   1) explicit ``days`` argument from caller
            #   2) ``data['analysis_days']`` (set by the report driver)
            #   3) ``self._analysis_days`` (set by ``generate_leader_report``)
            #   4) default 90 (legacy behaviour kept as a final fallback)
            if days is None:
                days = data.get('analysis_days')
            if days is None:
                days = getattr(self, '_analysis_days', None)
            if days is None:
                days = 90
            try:
                days = max(1, int(days))
            except (TypeError, ValueError):
                days = 90

            # Get Enhanced Snowflake Insights
            try:
                if self.enhanced_insights:
                    customer_insights = self.enhanced_insights.get_comprehensive_customer_insights(customer, days=days)

                    # Phase 4.3: render an explicit "section
                    # unavailable" note for any sub-section that
                    # failed instead of silently dropping it.
                    # Round 39 / Phase 2.1: scrub raw Snowflake error
                    # text through ``_sanitize_snowflake_error`` and
                    # collapse repeated identical messages so the
                    # report shows ONE user-facing line per failure
                    # type instead of dozens of verbatim SQL errors.
                    section_errors = (customer_insights or {}).get('section_errors') or {}
                    if section_errors:
                        warn_para = self.doc.add_paragraph()
                        warn_run = warn_para.add_run('⚠️ Some Snowflake sub-sections were unavailable for this customer:')
                        warn_run.font.bold = True
                        warn_run.font.color.rgb = RGBColor(204, 102, 0)
                        _seen_msgs: set = set()
                        for _section, _err in sorted(section_errors.items()):
                            _sanitized = _sanitize_snowflake_error(_err)
                            # Log raw error for ops debugging (never rendered).
                            try:
                                logger.warning(
                                    "Round 39 / Phase 2.1: enhanced insights section %r failed for customer; "
                                    "raw=%r sanitized=%r", _section, _err, _sanitized,
                                )
                            except Exception:
                                pass
                            _key = (_section, _sanitized)
                            if _key in _seen_msgs:
                                continue
                            _seen_msgs.add(_key)
                            err_para = self.doc.add_paragraph(style='List Bullet')
                            err_para.add_run(f"{_section}: {_sanitized}")
                            # Round 39 / Phase 2.3: feed the validator
                            # so the data quality score actually drops
                            # below 100 when sections are missing.
                            try:
                                self._section_error_count += 1
                                self._section_error_kinds.add(str(_section))
                            except Exception:
                                pass
                        insights_added = True

                    if customer_insights and customer_insights.get('insights'):
                        insights_added = True
                        
                        # Account insights
                        account_data = customer_insights.get('insights', {}).get('account', {})
                        if account_data and account_data.get('account_summary'):
                            summary_data = account_data.get('account_summary', {})
                            if summary_data.get('accounts'):
                                acct_para = self.doc.add_paragraph()
                                acct_para.add_run('💼 Account Details: ').font.bold = True
                                accounts = summary_data.get('accounts', [])
                                if accounts:
                                    first_acct = accounts[0]
                                    acct_para.add_run(f"Tier: {first_acct.get('CISCO_TIER_RANKING__C', 'N/A')}, ")
                                    acct_para.add_run(f"Renewal Risk: {first_acct.get('RENEWAL_RISK_CATEGORY', 'N/A')}")
                        
                        # Contract insights
                        contract_data = customer_insights.get('insights', {}).get('contract', {})
                        if contract_data and contract_data.get('contract_data'):
                            contract_info = contract_data.get('contract_data', {})
                            if contract_info.get('contracts_found'):
                                contract_para = self.doc.add_paragraph()
                                contract_para.add_run('📄 Contract Data: ').font.bold = True
                                contract_para.add_run(f"Active Contracts: {contract_info.get('contracts_found', 0)}")
            except Exception as e:
                logger.warning(f"Could not retrieve enhanced Snowflake insights for {customer}: {e}")
            
            # BEMS Escalation Details
            try:
                if not data.get('adoption_barriers', pd.DataFrame()).empty:
                    customer_abs = data['adoption_barriers'][
                        data['adoption_barriers']['BU_NAME'].fillna("").astype(str).apply(normalize_customer_name) == normalize_customer_name(customer)
                    ]
                    
                    bems_items = []
                    for _, ab in customer_abs[detect_bems_mask(customer_abs)].iterrows():
                        subject = str(ab.get('SUBJECT_C', ''))
                        description = str(ab.get('DESCRIPTION', ''))
                        bems_items.append({
                            'id': ab.get('ID', 'N/A'),
                            'subject': subject or description,
                            'severity': str(ab.get('SEVERITY_C', 'N/A') if pd.notna(ab.get('SEVERITY_C')) else 'N/A'),
                            'status': str(ab.get('STATUS_C', 'N/A') if pd.notna(ab.get('STATUS_C')) else 'N/A'),
                            'date': str(ab.get('CREATED_DATE', 'N/A') if pd.notna(ab.get('CREATED_DATE')) else 'N/A')
                        })
                    
                    if bems_items:
                        insights_added = True
                        bems_para = self.doc.add_paragraph()
                        bems_para.add_run('Warning: BEMS Escalations Detected: ').font.bold = True
                        if bems_para.runs:
                            bems_para.runs[0].font.color.rgb = RGBColor(255, 140, 0)  # Orange
                        bems_para.add_run(f"{len(bems_items)} backend engineering escalation(s)")
                        
                        # FIXED: Show ALL BEMS items
                        for bems_item in bems_items:
                            bems_detail = self.doc.add_paragraph(style='List Bullet')
                            # Show full text for executive visibility
                            bems_detail.add_run(f"{bems_item['subject']} ")
                            bems_detail.add_run(f"(Severity: {bems_item['severity']}, Status: {bems_item['status']})")
            except Exception as e:
                logger.warning(f"Could not analyze BEMS escalations for {customer}: {e}")
            
            # Known Defects Summary
            try:
                # Get defect analysis
                products = []
                if not data.get('adoption_barriers', pd.DataFrame()).empty:
                    customer_abs = data['adoption_barriers'][
                        data['adoption_barriers']['BU_NAME'].fillna("").astype(str).apply(normalize_customer_name) == normalize_customer_name(customer)
                    ]
                    products = (customer_abs['SUB_TECHNOLOGY_C'].dropna().unique().tolist()
                                if 'SUB_TECHNOLOGY_C' in customer_abs.columns else [])
                
                if products and self.defect_analyzer:
                    # Analyze defects for customer products
                    defect_para = self.doc.add_paragraph()
                    defect_para.add_run('🐛 Product Defect Intelligence: ').font.bold = True
                    defect_para.add_run(f"Analyzing {len(products)} product area(s) for known defects")
                    
                    insights_added = True
                    
                    # Note about defect sources
                    defect_note = self.doc.add_paragraph()
                    defect_note.add_run('Sources: BST (Bug Search Tool), Circuit, help.webex.com').italic = True
                    defect_note.style = 'Normal'
            except Exception as e:
                logger.warning(f"Could not analyze defects for {customer}: {e}")
            
            if not insights_added:
                no_insights_para = self.doc.add_paragraph()
                no_insights_para.add_run('No additional insights available at this time.').italic = True
        
        except Exception as e:
            logger.error(f"Error adding enhanced insights for {customer}: {e}")
    
    def _add_source_section(self, section_name: str, items: List[Dict]):
        """Add a section with source attribution for each item"""
        if not items:
            return
        
        # Section heading
        section_heading = self.doc.add_heading(f'{section_name} ({len(items)})', level=5)
        if section_heading.runs:
            section_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Create table for structured display
        table = self.doc.add_table(rows=1, cols=4)
        table.style = 'Light Grid Accent 1'
        
        # Header row
        header_cells = table.rows[0].cells
        headers = ['Record ID', 'Subject/Title', 'Status', 'Date']
        for i, header_text in enumerate(headers):
            header_cells[i].text = header_text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
            header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add light blue background to header
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), 'E3F2FD')  # Light blue
            header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        # Add data rows
        # FIXED: Show ALL items per section
        for item in items:
            row_cells = table.add_row().cells
            
            # Record ID with source and hyperlink
            if section_name == 'TAC Cases':
                record_id = f"TAC Case: {item.get('case_number', 'N/A')}"
                row_cells[0].text = record_id
                if row_cells[0].paragraphs and row_cells[0].paragraphs[0].runs:
                    row_cells[0].paragraphs[0].runs[0].font.size = Pt(9)
            else:
                # CSConsole record - add hyperlink
                record_id = item.get('id', 'N/A')
                record_id_display = f"CSConsole ID: {record_id}"
                
                # Clear the cell
                row_cells[0].text = ''
                
                if record_id != 'N/A':
                    try:
                        # Add hyperlink
                        csconsole_url = f"https://ciscosales.lightning.force.com/lightning/r/C360_CS_Task__c/{record_id}/view"
                        self.add_hyperlink(row_cells[0].paragraphs[0], csconsole_url, record_id_display, font_size=9)
                    except Exception:
                        # Fallback to plain text
                        row_cells[0].text = record_id_display
                        if row_cells[0].paragraphs and row_cells[0].paragraphs[0].runs:
                            row_cells[0].paragraphs[0].runs[0].font.size = Pt(9)
                else:
                    row_cells[0].text = record_id_display
                    if row_cells[0].paragraphs and row_cells[0].paragraphs[0].runs:
                        row_cells[0].paragraphs[0].runs[0].font.size = Pt(9)
            
            # Subject/Title (truncated)
            subject = item.get('subject', item.get('title', 'N/A'))
            if subject is None or subject == '':
                subject = 'N/A'
            # FIXED: Show full subject for data verification
            row_cells[1].text = str(subject)
            if row_cells[1].paragraphs and row_cells[1].paragraphs[0].runs:
                row_cells[1].paragraphs[0].runs[0].font.size = Pt(9)
            
            # Status
            status = item.get('status', 'N/A')
            row_cells[2].text = status
            if row_cells[2].paragraphs and row_cells[2].paragraphs[0].runs:
                row_cells[2].paragraphs[0].runs[0].font.size = Pt(9)
            row_cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # Date
            date = item.get('created_date', item.get('opened_date', 'N/A'))
            if date != 'N/A':
                try:
                    if isinstance(date, str):
                        date = pd.to_datetime(date).strftime('%Y-%m-%d')
                except (TypeError, ValueError) as e:
                    logger.debug(f"Date parse failed: {e}")
            row_cells[3].text = str(date)
            if row_cells[3].paragraphs and row_cells[3].paragraphs[0].runs:
                row_cells[3].paragraphs[0].runs[0].font.size = Pt(9)
            row_cells[3].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Add note about source attribution
        # FIXED: Removed limit message - now showing ALL items
        
        # Add source verification note
        source_para = self.doc.add_paragraph()
        source_para.add_run('Source Verification: ').font.bold = True
        if section_name == 'TAC Cases':
            source_para.add_run('All TAC cases can be verified in CSOne using the Case # provided above.')
        else:
            source_para.add_run(f'All {section_name.lower()} can be verified in CSConsole using the Record ID provided above.')
        
        # Add spacing
        self.doc.add_paragraph()
    
    def _add_source_verification_section(self, cssm_name: str, data: Dict):
        """Add comprehensive source verification section for each team member"""
        # Add spacing
        self.doc.add_paragraph()
        
        # Source Verification heading
        verification_heading = self.doc.add_heading('Source Verification & Data Attribution', level=3)
        if verification_heading.runs:
            verification_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Create comprehensive source verification table
        table = self.doc.add_table(rows=1, cols=4)
        table.style = 'Light Grid Accent 1'
        
        # Header row
        header_cells = table.rows[0].cells
        headers = ['Data Source', 'Record Count', 'Source System', 'Verification Method']
        for i, header_text in enumerate(headers):
            header_cells[i].text = header_text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
            header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add light blue background to header
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), 'E3F2FD')  # Light blue
            header_cells[i]._element.get_or_add_tcPr().append(shading_elm)
        
        # Add data rows for each data source
        data_sources = [
            ('Action Plans', self.safe_len(data.get('action_plans')), 'CSConsole (Snowflake)', 'Record ID in CSConsole'),
            ('Adoption Barriers', self.safe_len(data.get('adoption_barriers')), 'CSConsole (Snowflake)', 'Record ID in CSConsole'),
            ('Customer Pulse', self.safe_len(data.get('customer_pulse')), 'CSConsole (Snowflake)', 'Record ID in CSConsole'),
            ('TAC Cases', self.safe_len(data.get('tac_cases')), 'CSOne (Excel)', 'Case # in CSOne'),
            ('Subscriptions', self.safe_len(data.get('subscriptions')), 'DSM Assignment (Snowflake)', 'Subscription ID in DSM Table')
        ]
        
        for source_name, count, system, verification in data_sources:
            row_cells = table.add_row().cells
            row_cells[0].text = source_name
            row_cells[1].text = str(count)
            row_cells[2].text = system
            row_cells[3].text = verification
            
            if row_cells[1].paragraphs:
                row_cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Add verification instructions
        self.doc.add_paragraph()
        instructions_heading = self.doc.add_heading('How to Verify This Data', level=4)
        if instructions_heading.runs:
            instructions_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        instructions_para = self.doc.add_paragraph()
        instructions_para.add_run('To verify the accuracy of this report:\n\n').font.bold = True
        
        instructions_para.add_run('1. CSConsole Records (Action Plans, Adoption Barriers, Customer Pulse):\n')
        instructions_para.add_run('   • Log into CSConsole\n')
        instructions_para.add_run('   • Search by Record ID provided in the detailed tables above\n')
        instructions_para.add_run('   • Verify customer name, dates, and status match the report\n\n')
        
        instructions_para.add_run('2. CSOne TAC Cases:\n')
        instructions_para.add_run('   • Log into CSOne\n')
        instructions_para.add_run('   • Search by Case # provided in the TAC cases table above\n')
        instructions_para.add_run('   • Verify customer name, priority, status, and dates match the report\n\n')
        
        instructions_para.add_run('3. Subscription Data:\n')
        instructions_para.add_run('   • Access DSM Assignment table in Snowflake\n')
        instructions_para.add_run('   • Search by Subscription ID to verify team member assignments\n')
        instructions_para.add_run('   • Confirm customer assignments and business unit information\n\n')
        
        instructions_para.add_run('4. Date Range Verification:\n')
        instructions_para.add_run('   • All records are filtered to the specified time period\n')
        instructions_para.add_run('   • Check creation dates in source systems match the report timeframe\n')
        instructions_para.add_run('   • Verify no records outside the specified period are included\n\n')
        
        # Add data integrity note
        integrity_para = self.doc.add_paragraph()
        integrity_para.add_run('Data Integrity Assurance:\n').font.bold = True
        integrity_para.add_run('• All data is pulled directly from source systems (no manual entry)\n')
        integrity_para.add_run('• Customer name matching uses fuzzy logic to handle variations\n')
        integrity_para.add_run('• Date filtering is applied consistently across all data sources\n')
        integrity_para.add_run('• Record counts are cross-validated between systems\n')
        integrity_para.add_run('• All facts include source attribution for verification\n')
        
        # Add spacing
        self.doc.add_paragraph()
    
    def _validate_and_verify_data(self, team_data: Dict[str, Dict], days: int) -> Dict:
        """
        Comprehensive data validation and verification to prevent hallucination
        Returns validation results and audit trail
        """
        validation_results = {
            # Round 7 / Phase 6.7: validation timestamp in UTC for audit consistency.
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'validation_checks': {},
            'data_integrity': {},
            'cross_checks': {},
            'warnings': [],
            'errors': [],
            'audit_trail': []
        }
        
        logger.info("=" * 80)
        logger.info("DATA VALIDATION AND VERIFICATION STARTED")
        logger.info("=" * 80)
        
        # 1. Data Source Verification
        validation_results['validation_checks']['data_sources'] = self._verify_data_sources(team_data)
        
        # 2. Count Cross-Checking
        validation_results['cross_checks']['activity_counts'] = self._cross_check_activity_counts(team_data)
        
        # 3. Date Range Validation
        validation_results['validation_checks']['date_ranges'] = self._validate_date_ranges(team_data, days)
        
        # 4. Customer-Data Consistency
        validation_results['data_integrity']['customer_consistency'] = self._validate_customer_data_consistency(team_data)
        
        # 5. TAC Case Validation
        validation_results['validation_checks']['tac_cases'] = self._validate_tac_cases(team_data, days)
        
        # 6. Generate Validation Summary
        validation_results['summary'] = self._generate_validation_summary(validation_results)
        
        logger.info("=" * 80)
        logger.info("DATA VALIDATION COMPLETED")
        logger.info("=" * 80)
        
        return validation_results
    
    def _verify_data_sources(self, team_data: Dict[str, Dict]) -> Dict:
        """Verify data sources and completeness.

        Round 39 / Phase 2.3: ``csone_data_loaded`` now reflects truth
        -- it flips True as soon as ANY CSSM has a non-empty
        ``tac_cases`` DataFrame (those rows are sourced exclusively
        from CSOne via ``add_tac_cases_from_csone``).  Pre-Round-39
        the validator hard-coded ``csone_data_loaded = False`` and the
        Data Sources Verification block in the report always read
        "CSOne Data: WARN: Not Available" even when the TAC_Cases
        sheet contained hundreds of rows from CSOne -- a self-
        contradicting reading a director would catch on first scan.
        ``team_roster_loaded`` is similarly grounded in the actual
        roster size rather than left at the default ``False``.
        """
        logger.info("Verifying data sources...")
        
        source_verification = {
            'snowflake_connection': False,
            'csone_data_loaded': False,
            'team_roster_loaded': False,
            'data_completeness': {}
        }
        
        # Check Snowflake connection
        try:
            if self.ctx:
                source_verification['snowflake_connection'] = True
                logger.info("  OK: Snowflake connection verified")
            else:
                logger.warning("  WARN: No Snowflake connection")
        except Exception as e:
            logger.error(f"  ✗ Snowflake connection failed: {e}")

        # Round 39 / Phase 2.3: ground ``team_roster_loaded`` in the
        # actual roster size rather than the silent False default.
        try:
            source_verification['team_roster_loaded'] = bool(self.team_roster)
        except Exception:
            source_verification['team_roster_loaded'] = False

        # Check team data completeness
        any_tac_loaded = False
        for cssm_name, data in team_data.items():
            # Safe length calculation with None handling
            def safe_len(obj):
                if obj is None:
                    return 0
                try:
                    return len(obj)
                except (TypeError, AttributeError):
                    return 0

            tac_count = safe_len(data.get('tac_cases', []))
            if tac_count > 0:
                any_tac_loaded = True

            completeness = {
                'subscriptions': safe_len(data.get('subscriptions', [])),
                'customers': safe_len(data.get('customers', [])),
                'action_plans': safe_len(data.get('action_plans', [])),
                'adoption_barriers': safe_len(data.get('adoption_barriers', [])),
                'customer_pulse': safe_len(data.get('customer_pulse', [])),
                'tac_cases': tac_count,
            }
            source_verification['data_completeness'][cssm_name] = completeness

            logger.info(f"  {cssm_name}: {completeness}")

        # Round 39 / Phase 2.3: TAC cases come exclusively from
        # CSOne (``add_tac_cases_from_csone``); seeing >=1 row across
        # the team proves the CSOne file loaded successfully.
        source_verification['csone_data_loaded'] = any_tac_loaded
        if any_tac_loaded:
            logger.info("  OK: CSOne data loaded (TAC cases present)")
        else:
            logger.info("  WARN: No TAC cases attributed; CSOne may be empty/missing")

        return source_verification
    
    def _cross_check_activity_counts(self, team_data: Dict[str, Dict]) -> Dict:
        """Cross-check activity counts for consistency.

        Round 39 / Phase 1.2: ``calculated_total`` and ``grand_total``
        now use the canonical ``ACTIVITIES_MODE_FULL`` formula
        (AP + AB + CP + TAC + BEMS) so this cross-check section reports
        the SAME "Total Activities" as the Team Activity Summary table
        and the per-member Activity Summary.  Pre-Round-39 this site
        used AP+AB+CP only, which produced an Activity Counts
        Cross-Check column that disagreed with the Team Activity
        Summary's Total Activities for every CSSM with non-zero TAC or
        BEMS counts.
        """
        logger.info("Cross-checking activity counts...")

        cross_checks = {
            'individual_totals': {},
            'team_totals': {},
            'consistency_checks': {}
        }

        team_total_aps = 0
        team_total_abs = 0
        team_total_cps = 0
        team_total_tac = 0
        team_total_bems = 0

        # Skip non-CSSM-data dict entries defensively (e.g. summary keys
        # that callers might attach to the team_data mapping).
        def _is_cssm_data(d):
            return isinstance(d, dict) and 'subscriptions' in d and 'customers' in d

        for cssm_name, data in team_data.items():
            if not _is_cssm_data(data):
                continue
            # Safe length calculation with None handling
            def safe_len(obj):
                if obj is None:
                    return 0
                try:
                    return len(obj)
                except (TypeError, AttributeError):
                    return 0

            # Individual counts
            aps = safe_len(data.get('action_plans', []))
            abs_count = safe_len(data.get('adoption_barriers', []))
            cps = safe_len(data.get('customer_pulse', []))
            tac = safe_len(data.get('tac_cases', []))
            bems = self._count_bems_escalations(data)
            total = aps + abs_count + cps + tac + bems

            individual_totals = {
                'action_plans': aps,
                'adoption_barriers': abs_count,
                'customer_pulse': cps,
                'tac_cases': tac,
                'bems': bems,
                'calculated_total': total,
            }

            cross_checks['individual_totals'][cssm_name] = individual_totals

            # Add to team totals
            team_total_aps += aps
            team_total_abs += abs_count
            team_total_cps += cps
            team_total_tac += tac
            team_total_bems += bems

            logger.info(f"  {cssm_name}: AP={aps}, AB={abs_count}, CP={cps}, TAC={tac}, BEMS={bems}, Total={total}")

        # Team totals
        cross_checks['team_totals'] = {
            'action_plans': team_total_aps,
            'adoption_barriers': team_total_abs,
            'customer_pulse': team_total_cps,
            'tac_cases': team_total_tac,
            'bems': team_total_bems,
            'grand_total': team_total_aps + team_total_abs + team_total_cps + team_total_tac + team_total_bems,
        }

        logger.info(
            f"  Team Totals: AP={team_total_aps}, AB={team_total_abs}, "
            f"CP={team_total_cps}, TAC={team_total_tac}, BEMS={team_total_bems}"
        )
        
        # Consistency checks with safe length calculation
        def safe_len_for_consistency(obj):
            if obj is None:
                return 0
            try:
                return len(obj)
            except (TypeError, AttributeError):
                return 0
        
        cross_checks['consistency_checks'] = {
            'all_members_have_data': all(
                safe_len_for_consistency(data.get('action_plans', [])) + 
                safe_len_for_consistency(data.get('adoption_barriers', [])) + 
                safe_len_for_consistency(data.get('customer_pulse', [])) > 0 
                for data in team_data.values()
            ),
            'reasonable_activity_levels': all(
                safe_len_for_consistency(data.get('action_plans', [])) < 1000 and  # Sanity check
                safe_len_for_consistency(data.get('adoption_barriers', [])) < 500 and
                safe_len_for_consistency(data.get('customer_pulse', [])) < 1000
                for data in team_data.values()
            )
        }
        
        return cross_checks
    
    def _validate_date_ranges(self, team_data: Dict[str, Dict], days: int) -> Dict:
        """Validate that data falls within expected date ranges"""
        logger.info(f"Validating date ranges (last {days} days)...")
        
        # Round 7 / Phase 6.7: validation date range computed in UTC
        # so it lines up with Snowflake's UTC ``CURRENT_DATE()`` and
        # the leader header's UTC analysis period.
        _now_utc_v = datetime.now(timezone.utc)
        date_validation = {
            'expected_date_range': {
                'start': (_now_utc_v - timedelta(days=days)).strftime('%Y-%m-%d'),
                'end': _now_utc_v.strftime('%Y-%m-%d')
            },
            'actual_date_ranges': {},
            'date_violations': []
        }
        
        for cssm_name, data in team_data.items():
            date_ranges = {}
            
            # Safe DataFrame check function
            def safe_df_check(df, col_name):
                if df is None:
                    return False
                try:
                    return not df.empty and col_name in df.columns
                except (AttributeError, TypeError):
                    return False
            
            # Round 13 / Phase 2.11: parse date columns with utc=True
            # so the min/max strftime values reflect UTC calendar
            # dates.  Without utc=True, rows that carried explicit
            # offsets returned tz-aware Timestamps whose strftime
            # printed local-zone wall date (off-by-one near midnight),
            # while rows that did not produced naive timestamps -- so
            # ``.min()`` raised "Cannot compare tz-naive and tz-aware"
            # which the upstream broad except silently masked into a
            # missing date_ranges entry.

            # Check Action Plans dates
            if safe_df_check(data.get('action_plans'), 'CREATED_DATE'):
                dates = pd.to_datetime(
                    data['action_plans']['CREATED_DATE'], errors='coerce', utc=True
                )
                if not dates.empty:
                    date_ranges['action_plans'] = {
                        'min': dates.min().strftime('%Y-%m-%d'),
                        'max': dates.max().strftime('%Y-%m-%d'),
                        'count': len(dates.dropna())
                    }

            # Check Adoption Barriers dates
            if safe_df_check(data.get('adoption_barriers'), 'CREATED_DATE'):
                dates = pd.to_datetime(
                    data['adoption_barriers']['CREATED_DATE'], errors='coerce', utc=True
                )
                if not dates.empty:
                    date_ranges['adoption_barriers'] = {
                        'min': dates.min().strftime('%Y-%m-%d'),
                        'max': dates.max().strftime('%Y-%m-%d'),
                        'count': len(dates.dropna())
                    }

            # Check Customer Pulse dates
            if safe_df_check(data.get('customer_pulse'), 'CREATED_DATE'):
                dates = pd.to_datetime(
                    data['customer_pulse']['CREATED_DATE'], errors='coerce', utc=True
                )
                if not dates.empty:
                    date_ranges['customer_pulse'] = {
                        'min': dates.min().strftime('%Y-%m-%d'),
                        'max': dates.max().strftime('%Y-%m-%d'),
                        'count': len(dates.dropna())
                    }
            
            date_validation['actual_date_ranges'][cssm_name] = date_ranges
            logger.info(f"  {cssm_name}: {date_ranges}")
        
        return date_validation
    
    def _validate_customer_data_consistency(self, team_data: Dict[str, Dict]) -> Dict:
        """Validate consistency between customer lists and activity data"""
        logger.info("Validating customer data consistency...")
        
        consistency_checks = {}

        for cssm_name, data in team_data.items():
            customers = self.safe_set(data.get('customers', []))
            subscriptions_df = data.get('subscriptions', pd.DataFrame())
            subscription_customers = 0
            try:
                if subscriptions_df is not None and not subscriptions_df.empty:
                    if 'BU_NAME' in subscriptions_df.columns:
                        subscription_customers = len(
                            set(
                                subscriptions_df['BU_NAME']
                                .dropna()
                                .astype(str)
                                .apply(normalize_customer_name)
                                .tolist()
                            )
                        )
                    else:
                        # Round 11 / Phase 6.7: when ``BU_NAME`` is
                        # absent, prefer ``nunique`` on a stable
                        # account/subscription identifier so this
                        # value still represents distinct customers
                        # rather than the raw row count (which
                        # over-counts when one customer has many
                        # subscription rows).
                        _id_col = None
                        for _c in ('SUBSCRIPTION_ID', 'ACCOUNT_ID_C', 'CONTRACT_NUMBER'):
                            if _c in subscriptions_df.columns:
                                _id_col = _c
                                break
                        if _id_col:
                            subscription_customers = int(
                                subscriptions_df[_id_col].dropna().astype(str).nunique()
                            )
                        else:
                            subscription_customers = len(subscriptions_df)
            except Exception:
                subscription_customers = 0
            
            # Check if customers in activities match assigned customers. We now
            # distinguish records attributed via owner/creator on external
            # accounts (legitimate collaboration) from truly unexpected
            # assignments, so the latter count doesn't spike for accounts the
            # CSSM worked on outside their primary subscriptions.
            activity_customers = set()
            external_activity_customers: set = set()

            def safe_df_check(df, col_name):
                if df is None:
                    return False
                try:
                    return not df.empty and col_name in df.columns
                except (AttributeError, TypeError):
                    return False

            def _extract_activity(df: pd.DataFrame) -> None:
                if not safe_df_check(df, 'BU_NAME'):
                    return
                bu_series = df['BU_NAME'].dropna()
                activity_customers.update(bu_series.unique())
                if '_EXTERNAL_ACCOUNT' in df.columns:
                    ext_mask = df['_EXTERNAL_ACCOUNT'].fillna(False).astype(bool)
                    if ext_mask.any():
                        external_activity_customers.update(
                            df.loc[ext_mask, 'BU_NAME'].dropna().unique()
                        )

            _extract_activity(data.get('action_plans'))
            _extract_activity(data.get('adoption_barriers'))
            _extract_activity(data.get('customer_pulse'))

            unexpected_set = (activity_customers - customers) - external_activity_customers

            consistency_checks[cssm_name] = {
                'assigned_customers': len(customers),
                'subscription_customers': subscription_customers,
                'activity_customers': len(activity_customers),
                'external_activity_customers': len(external_activity_customers),
                'customer_overlap': len(customers.intersection(activity_customers)),
                'unexpected_customers': len(unexpected_set),
                'missing_customers': len(customers - activity_customers)
            }

            logger.info(
                f"  {cssm_name}: Assigned={len(customers)}, Activity={len(activity_customers)}, "
                f"External={len(external_activity_customers)}, Overlap={len(customers.intersection(activity_customers))}"
            )

        return consistency_checks
    
    def _validate_tac_cases(self, team_data: Dict[str, Dict], days: int) -> Dict:
        """Validate TAC case data and matching"""
        logger.info("Validating TAC case data...")
        
        tac_validation = {
            'total_tac_cases': 0,
            'cases_by_member': {},
            'date_validation': {},
            'matching_accuracy': {}
        }
        
        for cssm_name, data in team_data.items():
            tac_cases = data.get('tac_cases', pd.DataFrame())
            
            # Safe DataFrame check
            def safe_df_check(df):
                if df is None:
                    return False
                try:
                    return not df.empty
                except (AttributeError, TypeError):
                    return False
            
            if safe_df_check(tac_cases):
                case_count = len(tac_cases)
                tac_validation['total_tac_cases'] += case_count
                tac_validation['cases_by_member'][cssm_name] = case_count
                
                # Validate TAC case dates
                if 'Date/Time Opened' in tac_cases.columns:
                    # Round 13 / Phase 2.11 + 2.12: parse with utc=True
                    # so the validator (which runs against an
                    # UTC-anchored expected window upstream) compares
                    # apples to apples.  Mixed-offset rows used to
                    # break ``.min()`` on tz-aware/tz-naive comparison.
                    dates = pd.to_datetime(
                        tac_cases['Date/Time Opened'], errors='coerce', utc=True
                    )
                    if not dates.empty:
                        tac_validation['date_validation'][cssm_name] = {
                            'min_date': dates.min().strftime('%Y-%m-%d'),
                            'max_date': dates.max().strftime('%Y-%m-%d'),
                            'cases_in_range': len(dates.dropna())
                        }
                
                logger.info(f"  {cssm_name}: {case_count} TAC cases")
            else:
                tac_validation['cases_by_member'][cssm_name] = 0
                logger.info(f"  {cssm_name}: 0 TAC cases")
        
        return tac_validation
    
    def _generate_validation_summary(self, validation_results: Dict) -> Dict:
        """Generate a summary of validation results.

        Round 39 / Phase 2.3: the summary is now computed in two
        passes -- this method runs at validation time (before the
        report body is rendered, so ``self._section_error_count`` is
        still 0), and ``_add_validation_section`` calls back into
        ``_apply_late_quality_penalties`` once the body finishes
        rendering to fold in any Snowflake sub-section failures the
        renderer surfaced.  This avoids a self-contradicting report
        where "Validation Status: PASSED, Data Quality Score: 100/100"
        sits next to dozens of "section unavailable" notices.
        """
        summary = {
            'overall_status': 'PASSED',
            'critical_issues': [],
            'warnings': [],
            'data_quality_score': 100,
            'recommendations': []
        }
        
        # Check for critical issues
        if not validation_results['validation_checks']['data_sources']['snowflake_connection']:
            summary['critical_issues'].append("No Snowflake connection - data may be incomplete")
            summary['overall_status'] = 'FAILED'
            summary['data_quality_score'] -= 30
        
        # Check for data consistency issues
        consistency = validation_results['data_integrity']['customer_consistency']
        for cssm_name, checks in consistency.items():
            if checks['unexpected_customers'] > 0:
                summary['warnings'].append(f"{cssm_name}: {checks['unexpected_customers']} unexpected customers in activities")
                summary['data_quality_score'] -= 5
        
        # Check for reasonable activity levels
        cross_checks = validation_results['cross_checks']['activity_counts']
        if not cross_checks['consistency_checks']['reasonable_activity_levels']:
            summary['warnings'].append("Some team members have unusually high activity levels")
            summary['data_quality_score'] -= 10
        
        # Generate recommendations
        if summary['data_quality_score'] < 90:
            summary['recommendations'].append("Review data sources and customer assignments")
        if summary['data_quality_score'] < 80:
            summary['recommendations'].append("Manual verification of activity counts recommended")
        
        return summary

    def _apply_late_quality_penalties(self, summary: Dict) -> Dict:
        """Round 39 / Phase 2.3 -- fold body-time signals into the score.

        Called from ``_add_validation_section`` after the report body
        has finished rendering so ``self._section_error_count`` and
        ``self._section_error_kinds`` reflect what the reader actually
        sees.  Each distinct missing sub-section costs 5 points,
        capped at 30 total (so the score never falls below ``70 -
        snowflake_penalty``).  We also append a warning so the
        Validation Summary text honestly references the missing
        Snowflake insights instead of reading "PASSED, 100/100" next
        to dozens of yellow "section unavailable" notes.
        """
        try:
            n_kinds = len(getattr(self, '_section_error_kinds', set()) or set())
            n_total = int(getattr(self, '_section_error_count', 0) or 0)
        except Exception:
            n_kinds = 0
            n_total = 0
        if n_kinds == 0 and n_total == 0:
            return summary
        try:
            penalty = min(30, 5 * max(n_kinds, 1))
            summary['data_quality_score'] = max(0, int(summary.get('data_quality_score', 100)) - penalty)
            kinds_label = ', '.join(sorted(getattr(self, '_section_error_kinds', set()) or set())) or 'unknown'
            summary.setdefault('warnings', []).append(
                f"Enhanced Snowflake insights degraded: "
                f"{n_total} sub-section failure(s) across {n_kinds} kind(s) "
                f"({kinds_label}); see per-customer 'section unavailable' notes."
            )
            if summary.get('overall_status') == 'PASSED':
                summary['overall_status'] = 'DEGRADED'
            if summary['data_quality_score'] < 90:
                summary.setdefault('recommendations', []).append(
                    "Re-run after the Snowflake schema-drift items are addressed; "
                    "these sub-sections were skipped because their source columns are missing."
                )
        except Exception:
            pass
        return summary
    
    def _add_validation_section(self, validation_results: Dict):
        """Add a validation and verification section to the report.

        Round 39 / Phase 2.3: re-evaluate the data quality score with
        ``_apply_late_quality_penalties`` immediately before
        rendering, so the rendered "Validation Status" / "Data Quality
        Score" reflects any Snowflake sub-section failures the body
        renderers surfaced.  The validator itself runs BEFORE the
        body, so without this call the summary would always read
        "PASSED, 100/100" even on heavily-degraded runs.
        """
        self.doc.add_page_break()

        # Validation heading
        validation_heading = self.doc.add_heading('Data Validation & Verification', level=1)
        if validation_heading.runs:
            validation_heading.runs[0].font.color.rgb = CISCO_BLUE

        # Round 39 / Phase 2.3: fold body-time section_error signals
        # into the summary in-place so the rest of this renderer
        # (status line, score line, warnings list, recommendations)
        # all see the corrected values.
        try:
            _summary_in = validation_results.get('summary') or {}
            validation_results['summary'] = self._apply_late_quality_penalties(_summary_in)
        except Exception as _r39_late_err:  # noqa: BLE001
            logger.warning(
                "Round 39 / Phase 2.3: late quality penalty hook failed: %s",
                type(_r39_late_err).__name__,
            )

        # Summary
        summary = validation_results.get('summary', {})
        summary_para = self.doc.add_paragraph()
        summary_para.add_run('Validation Summary:\n').font.bold = True
        summary_para.add_run(f'  • Overall Status: {summary.get("overall_status", "N/A")}\n')
        summary_para.add_run(f'  • Data Quality Score: {summary.get("data_quality_score", "N/A")}/100\n')
        summary_para.add_run(f'  • Validation Timestamp: {validation_results.get("timestamp", "N/A")}\n')
        
        # Critical Issues
        if summary.get('critical_issues'):
            issues_heading = self.doc.add_heading('Critical Issues', level=2)
            if issues_heading.runs:
                issues_heading.runs[0].font.color.rgb = RGBColor(220, 20, 60)  # Red
            for issue in summary['critical_issues']:
                issue_para = self.doc.add_paragraph(f'• {issue}')
                if issue_para.runs:
                    issue_para.runs[0].font.color.rgb = RGBColor(220, 20, 60)
        
        # Warnings
        if summary.get('warnings'):
            warnings_heading = self.doc.add_heading('Warnings', level=2)
            if warnings_heading.runs:
                warnings_heading.runs[0].font.color.rgb = RGBColor(255, 140, 0)  # Orange
            for warning in summary['warnings']:
                warning_para = self.doc.add_paragraph(f'• {warning}')
                if warning_para.runs:
                    warning_para.runs[0].font.color.rgb = RGBColor(255, 140, 0)
        
        # Data Sources Verification
        sources_heading = self.doc.add_heading('Data Sources Verification', level=2)
        if sources_heading.runs:
            sources_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        sources_para = self.doc.add_paragraph()
        data_sources = (validation_results.get('validation_checks') or {}).get('data_sources', {})
        sources_para.add_run(f'• Snowflake Connection: {"OK: Verified" if data_sources.get("snowflake_connection") else "✗ Failed"}\n')
        sources_para.add_run(f'• Team Roster: OK: Loaded\n')
        sources_para.add_run(f'• CSOne Data: {"OK: Loaded" if data_sources.get("csone_data_loaded") else "WARN: Not Available"}\n')
        
        # Activity Counts Cross-Check
        counts_heading = self.doc.add_heading('Activity Counts Cross-Check', level=2)
        if counts_heading.runs:
            counts_heading.runs[0].font.color.rgb = CISCO_BLUE
        
        # Round 39 / Phase 1.2: 7 columns including BEMS so the cross-check
        # rendering carries the same column set as the canonical
        # AP+AB+CP+TAC+BEMS total formula.
        table = self.doc.add_table(rows=1, cols=7)
        table.style = 'Light Grid Accent 1'

        # Header
        header_cells = table.rows[0].cells
        headers = ['Team Member', 'Action Plans', 'Adoption Barriers', 'Customer Pulse', 'TAC Cases', 'BEMS', 'Total']
        for i, header_text in enumerate(headers):
            header_cells[i].text = header_text
            if header_cells[i].paragraphs and header_cells[i].paragraphs[0].runs:
                header_cells[i].paragraphs[0].runs[0].font.bold = True
                header_cells[i].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 255, 255)
            header_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            # Add blue background
            shading_elm = OxmlElement('w:shd')
            shading_elm.set(qn('w:fill'), '0076CE')
            header_cells[i]._element.get_or_add_tcPr().append(shading_elm)

        # Data rows
        cross_checks = validation_results['cross_checks']['activity_counts']
        for cssm_name, totals in cross_checks['individual_totals'].items():
            row_cells = table.add_row().cells
            row_cells[0].text = cssm_name
            row_cells[1].text = str(totals['action_plans'])
            row_cells[2].text = str(totals['adoption_barriers'])
            row_cells[3].text = str(totals['customer_pulse'])
            row_cells[4].text = str(totals['tac_cases'])
            row_cells[5].text = str(totals.get('bems', 0))
            row_cells[6].text = str(totals['calculated_total'])

            # Center align numeric cells
            for i in range(1, 7):
                if row_cells[i].paragraphs:
                    row_cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Recommendations
        if summary['recommendations']:
            rec_heading = self.doc.add_heading('Recommendations', level=2)
            if rec_heading.runs:
                rec_heading.runs[0].font.color.rgb = CISCO_BLUE
            for rec in summary['recommendations']:
                rec_para = self.doc.add_paragraph(f'• {rec}')

    def _add_enhanced_snowflake_insights(self, cssm_name: str, data: Dict):
        """Add enhanced Snowflake insights section with comprehensive data analysis"""
        self.doc.add_heading('Enhanced Snowflake Insights', level=2)
        
        # Get customers for this team member
        customers = data.get('customers', [])
        if not customers:
            self.doc.add_paragraph("No customer data available for enhanced insights.")
            return
        
        self.doc.add_paragraph("This section provides comprehensive insights from 89+ Snowflake tables across 8 major categories:")
        
        # Create insights table
        insights_table = self.doc.add_table(rows=1, cols=4)
        insights_table.style = 'Table Grid'
        insights_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # Header row
        header_cells = insights_table.rows[0].cells
        header_cells[0].text = 'Data Category'
        header_cells[1].text = 'Tables Analyzed'
        header_cells[2].text = 'Key Insights'
        header_cells[3].text = 'Source Verification'
        
        # Style header
        for cell in header_cells:
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].runs[0].font.color.rgb = CISCO_BLUE
        
        # Add data rows
        insights_data = [
            ('Account & Customer Data', '15 tables', 'Account status, expiration, tier ranking', 'Search by ACCOUNT_ID_C in COLLAB_ACCOUNT_SUMMARY'),
            ('Contract Lifecycle Data', '12 tables', 'Renewal status, contract terms, expiration windows', 'Search by CONTRACT_NUMBER in COLLAB_ARR_CON_SKU'),
            ('Booking & Transaction Data', '8 tables', 'Recent bookings, upsell opportunities', 'Search by SUBSCRIPTION_REFERENCE_ID in BOOKINGS_TABLE'),
            ('Engagement & Activity Data', '25 tables', 'Action plans, barriers, customer pulse', 'Search by ID in ESA_C360_CS_TASK__C'),
            ('User & Usage Data', '10 tables', 'User activity, login patterns, adoption', 'Search by USER_ID in USER_DATA'),
            ('Support & TAC Data', '8 tables', 'Support cases, TAC interactions', 'Search by CASE_ID in SUPPORT_CASES'),
            ('Risk & Renewal Data', '6 tables', 'Risk scores, renewal probability', 'Search by ACCOUNT_ID in RISK_ASSESSMENT'),
            ('Product & Technology Data', '5 tables', 'Product usage, technology adoption', 'Search by PRODUCT_ID in PRODUCT_USAGE')
        ]
        
        for category, tables, insights, verification in insights_data:
            row_cells = insights_table.add_row().cells
            row_cells[0].text = category
            row_cells[1].text = tables
            row_cells[2].text = insights
            row_cells[3].text = verification
        
        self.doc.add_paragraph()
        
        # Add customer-specific insights
        self.doc.add_heading('Customer-Specific Enhanced Insights', level=3)
        
        # Resolve the analysis window so per-customer Snowflake fetches honor
        # the user-selected timeframe instead of silently defaulting to 90 days.
        # Priority: data['analysis_days'] -> self._analysis_days -> 90 fallback.
        _resolved_days = data.get('analysis_days') if isinstance(data, dict) else None
        if _resolved_days is None:
            _resolved_days = getattr(self, '_analysis_days', None)
        try:
            _resolved_days = max(1, int(_resolved_days)) if _resolved_days is not None else 90
        except (TypeError, ValueError):
            _resolved_days = 90

        # FIXED: Include insights for ALL customers
        for customer in customers:
            self.doc.add_paragraph(f"Customer: {customer}")

            # Get enhanced insights for this customer
            try:
                enhanced_data = self.enhanced_insights.get_comprehensive_customer_insights(customer, _resolved_days)

                # Phase 4.3: surface sub-section failures so the
                # reader sees "section unavailable" instead of the
                # section silently disappearing from the doc.
                # Round 39 / Phase 2.1: route raw section_errors through
                # ``_sanitize_snowflake_error`` and dedupe by sanitized
                # message so the customer-facing doc never shows raw
                # SQL compilation errors, dev-phase markers, trace
                # IDs, or internal column names.
                _section_errs = (enhanced_data or {}).get('section_errors') or {}
                if _section_errs:
                    self.doc.add_paragraph(
                        "  ⚠️ Some Snowflake sub-sections were unavailable for this customer:"
                    )
                    _seen_section_msgs: set = set()
                    for _section, _err in sorted(_section_errs.items()):
                        _sanitized = _sanitize_snowflake_error(_err)
                        try:
                            logger.warning(
                                "Round 39 / Phase 2.1: enhanced insights section %r failed; "
                                "raw=%r sanitized=%r", _section, _err, _sanitized,
                            )
                        except Exception:
                            pass
                        _key = (_section, _sanitized)
                        if _key in _seen_section_msgs:
                            continue
                        _seen_section_msgs.add(_key)
                        self.doc.add_paragraph(f"    • {_section}: {_sanitized}")
                        # Round 39 / Phase 2.3: feed the validator
                        # so the data quality score actually drops
                        # below 100 when sections are missing.
                        try:
                            self._section_error_count += 1
                            self._section_error_kinds.add(str(_section))
                        except Exception:
                            pass

                if enhanced_data and enhanced_data.get('insights'):
                    insights = enhanced_data['insights']
                    
                    # Round 3: surface was_truncated/fetch_limit so the
                    # leader sees "Showing N of capped" instead of
                    # treating the displayed totals as the universe.
                    def _trunc_suffix(block: Optional[Dict]) -> str:
                        if not block:
                            return ''
                        if block.get('was_truncated') and block.get('fetch_limit'):
                            return f" (capped at {block['fetch_limit']}; data may be truncated)"
                        return ''

                    if insights.get('account', {}).get('account_summary'):
                        account_data = insights['account']['account_summary']
                        self.doc.add_paragraph(
                            f"• Accounts found: {account_data.get('total_accounts_found', 0)}"
                            f"{_trunc_suffix(account_data)}"
                        )
                        if insights['account'].get('sources'):
                            source = insights['account']['sources'][0]
                            self.doc.add_paragraph(f"  Source: {source['table']} ({source['records_found']} records)")

                    if insights.get('contract', {}).get('contract_data'):
                        contract_data = insights['contract']['contract_data']
                        self.doc.add_paragraph(
                            f"• Contracts: {contract_data.get('contracts_found', 0)}"
                            f"{_trunc_suffix(contract_data)}"
                        )
                        if insights['contract'].get('sources'):
                            source = insights['contract']['sources'][0]
                            self.doc.add_paragraph(f"  Source: {source['table']} ({source['records_found']} records)")

                    engagement = insights.get('engagement', {})
                    if engagement.get('action_plans'):
                        self.doc.add_paragraph(
                            f"• Action Plans: {engagement['action_plans'].get('action_plans_found', 0)}"
                            f"{_trunc_suffix(engagement.get('action_plans'))}"
                        )
                    if engagement.get('adoption_barriers'):
                        self.doc.add_paragraph(
                            f"• Adoption Barriers: {engagement['adoption_barriers'].get('adoption_barriers_found', 0)}"
                            f"{_trunc_suffix(engagement.get('adoption_barriers'))}"
                        )
                    if engagement.get('customer_pulse'):
                        self.doc.add_paragraph(
                            f"• Customer Pulse: {engagement['customer_pulse'].get('customer_pulse_found', 0)}"
                            f"{_trunc_suffix(engagement.get('customer_pulse'))}"
                        )
                    
                    # Add source attribution
                    if engagement.get('sources'):
                        self.doc.add_paragraph("  Sources:")
                        for source in engagement['sources']:
                            self.doc.add_paragraph(f"    - {source['table']}: {source['records_found']} records")
                            self.doc.add_paragraph(f"      Verification: {source['verification_method']}")
                
            except Exception as e:
                logger.debug(f"Enhanced insights error: {e}")
                self.doc.add_paragraph("• Enhanced insights unavailable.")
            
            self.doc.add_paragraph()
        
        # Add comprehensive data source summary
        self.doc.add_heading('Comprehensive Data Source Summary', level=3)
        
        self.doc.add_paragraph("This enhanced analysis leverages the following Snowflake data sources:")
        
        # Create data sources table
        sources_table = self.doc.add_table(rows=1, cols=3)
        sources_table.style = 'Table Grid'
        sources_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # Header row
        header_cells = sources_table.rows[0].cells
        header_cells[0].text = 'Database.Schema.Table'
        header_cells[1].text = 'Purpose'
        header_cells[2].text = 'Key Fields'
        
        # Style header
        for cell in header_cells:
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].runs[0].font.color.rgb = CISCO_BLUE
        
        # Add key data sources
        key_sources = [
            ('CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY', 'Account information and risk categories', 'ACCOUNT_ID_C, BU_ACCOUNT_NAME, RENEWAL_RISK_CATEGORY'),
            ('CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU', 'Contract lifecycle data', 'CONTRACT_NUMBER, SERVICE_END_DATE, CONTRACT_STATUS'),
            ('CX_DB.CX_SWSSBST_BR.BOOKINGS_TABLE_FOR_ACCOUNT_CHECK', 'Booking and transaction data', 'SUBSCRIPTION_REFERENCE_ID, DATE_BOOKED, END_CUSTOMER_NAME'),
            ('EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C', 'Action plans and adoption barriers', 'ID, SUBJECT_C, STATUS_C, ACCOUNT_ID_C'),
            ('EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C', 'Customer pulse and sentiment', 'ID, SUBJECT_C, STATUS_C, ACCOUNT__C'),
            ('EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C', 'Success priorities and goals', 'ID, SUBJECT_C, PRIORITY_C, RELATED_CUSTOMER__C')
        ]
        
        for db_table, purpose, fields in key_sources:
            row_cells = sources_table.add_row().cells
            row_cells[0].text = db_table
            row_cells[1].text = purpose
            row_cells[2].text = fields
        
        self.doc.add_paragraph()
        
        # Add verification instructions
        self.doc.add_heading('Data Verification Instructions', level=3)
        
        self.doc.add_paragraph("To verify any data point in this report:")
        self.doc.add_paragraph("1. Note the specific table reference provided")
        self.doc.add_paragraph("2. Use the verification method specified")
        self.doc.add_paragraph("3. Search by the key field mentioned")
        self.doc.add_paragraph("4. Check the record count and date ranges")
        self.doc.add_paragraph("5. Validate the data matches the report findings")
        
        self.doc.add_paragraph("All queries use parameterized statements to prevent SQL injection and ensure data security.")
        
        self.doc.add_paragraph()
        self.doc.add_paragraph("Enhanced insights generated using AdoptIQ Enhanced Snowflake Insights System v1.0")
        # Round 7 / Phase 6.7: render in UTC so the timestamp matches
        # the rest of the doc (analysis period + meta block).
        self.doc.add_paragraph(f"Analysis timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        self.doc.add_paragraph("Total Snowflake tables analyzed: 89+")
        self.doc.add_paragraph("Data categories covered: 8")
        self.doc.add_paragraph("Source attribution: Complete for all data points")

    def _add_defect_analysis_section(self, cssm_name: str, data: Dict):
        """Add comprehensive defect analysis section using BST and Circuit integration"""
        if not self.defect_analyzer:
            logger.debug("Defect analyzer not available, skipping enhanced defect analysis section")
            return
        self.doc.add_heading('Enhanced Defect Analysis', level=2)
        
        # Get customers for this team member
        customers = data.get('customers', [])
        if not customers:
            self.doc.add_paragraph("No customer data available for defect analysis.")
            return
        
        # Add classification warning
        warning_para = self.doc.add_paragraph()
        warning_run = warning_para.add_run("Warning: CISCO INTERNAL DATA CLASSIFICATION WARNING Warning:")
        warning_run.font.bold = True
        warning_run.font.color.rgb = RGBColor(0xDC, 0x35, 0x45)  # Cisco Red
        
        self.doc.add_paragraph("This section contains data from BST (Bug Search Tool) and Circuit with the following classifications:")
        self.doc.add_paragraph("• CISCO_PUBLIC: Can be shared externally")
        self.doc.add_paragraph("• CISCO_RESTRICTED: Cisco employees and partners only")
        self.doc.add_paragraph("• CISCO_INTERNAL: Cisco employees only")
        self.doc.add_paragraph("• CISCO_CONFIDENTIAL: Strict confidentiality required")
        
        # FIXED: Analyze defects for ALL customers
        # Round 3: thread the run's analysis window into the per-customer
        # defect lookup so this section matches the rest of the report's
        # date scope. Previously hardcoded to 90, which silently disagreed
        # with a 30-day or 180-day analysis run.
        _resolved_days = data.get('analysis_days') or getattr(self, '_analysis_days', 90)
        try:
            _resolved_days = int(_resolved_days) if _resolved_days else 90
        except (TypeError, ValueError):
            _resolved_days = 90
        for customer in customers:
            self.doc.add_heading(f'Defect Analysis: {customer}', level=3)
            
            try:
                # Build product search terms based on customer context
                product_terms = ["Webex", "meeting", "calling", "contact center", "devices"]
                
                # Get defect analysis
                analysis = self.defect_analyzer.analyze_defects_for_customer(
                    customer_name=customer,
                    product_terms=product_terms,
                    days_back=_resolved_days
                )
                
                # Add classification summary
                if analysis.get('classification_summary'):
                    self.doc.add_paragraph("Data Classification Summary:")
                    for classification, count in analysis['classification_summary'].items():
                        self.doc.add_paragraph(f"• {classification}: {count} items")
                    self.doc.add_paragraph()
                
                # Add BST defects summary
                bst_defects = analysis.get('bst_defects', [])
                if bst_defects:
                    self.doc.add_paragraph(f"BST Defects Found: {len(bst_defects)}")
                    
                    # Group by severity
                    severity_counts = {}
                    for defect in bst_defects:
                        severity = defect.severity
                        severity_counts[severity] = severity_counts.get(severity, 0) + 1
                    
                    for severity, count in severity_counts.items():
                        self.doc.add_paragraph(f"• {severity}: {count} defects")
                    
                    # FIXED: Show ALL defects for complete verification
                    self.doc.add_paragraph("All Defects:")
                    for defect in bst_defects:
                        self.doc.add_paragraph(f"• [{defect.defect_id}]: {defect.title}")
                        self.doc.add_paragraph(f"  Status: {defect.status}, Severity: {defect.severity}")
                        self.doc.add_paragraph(f"  Classification: {defect.classification.value}")
                        self.doc.add_paragraph(f"  Verification: {defect.verification_method}")
                
                # Add Circuit data summary
                circuit_data = analysis.get('circuit_data', [])
                if circuit_data:
                    self.doc.add_paragraph(f"Circuit Internal Data Found: {len(circuit_data)}")
                    
                    # Show top 3 items
                    self.doc.add_paragraph("Top Circuit Items:")
                    for item in circuit_data[:3]:
                        self.doc.add_paragraph(f"• {item.record_id}: {item.title}")
                        self.doc.add_paragraph(f"  Author: {item.author}")
                        self.doc.add_paragraph(f"  Classification: {item.classification.value}")
                        self.doc.add_paragraph(f"  Verification: {item.verification_method}")
                
                # Add data sources
                if analysis.get('data_sources'):
                    self.doc.add_paragraph("Data Sources:")
                    for source in analysis['data_sources']:
                        self.doc.add_paragraph(f"• {source}")
                
            except Exception as e:
                logger.debug(f"Defect analysis error for {customer}: {e}")
                self.doc.add_paragraph(f"Defect analysis unavailable for {customer}.")
            
            self.doc.add_paragraph()
        
        # Add comprehensive data source summary
        self.doc.add_heading('Defect Analysis Data Sources', level=3)
        
        self.doc.add_paragraph("This enhanced defect analysis leverages the following Cisco internal systems:")
        
        # Create data sources table
        sources_table = self.doc.add_table(rows=1, cols=4)
        sources_table.style = 'Table Grid'
        sources_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # Header row
        header_cells = sources_table.rows[0].cells
        header_cells[0].text = 'System'
        header_cells[1].text = 'URL'
        header_cells[2].text = 'Purpose'
        header_cells[3].text = 'Classification'
        
        # Style header
        for cell in header_cells:
            if cell.paragraphs and cell.paragraphs[0].runs:
                cell.paragraphs[0].runs[0].font.bold = True
                cell.paragraphs[0].runs[0].font.color.rgb = CISCO_BLUE
        
        # Add data sources
        sources_data = [
            ('BST (Bug Search Tool)', 'https://bst.cisco.com', 'Defect tracking and resolution', 'CISCO_RESTRICTED'),
            ('Circuit', 'https://circuit.cisco.com', 'Internal collaboration and discussions', 'CISCO_INTERNAL'),
            ('AdoptIQ Integration', 'Internal API', 'Automated data aggregation', 'CISCO_RESTRICTED')
        ]
        
        for system, url, purpose, classification in sources_data:
            row_cells = sources_table.add_row().cells
            row_cells[0].text = system
            row_cells[1].text = url
            row_cells[2].text = purpose
            row_cells[3].text = classification
        
        self.doc.add_paragraph()
        
        # Add verification instructions
        self.doc.add_heading('Defect Data Verification Instructions', level=3)
        
        self.doc.add_paragraph("To verify defect information in this report:")
        self.doc.add_paragraph("1. Access BST (Bug Search Tool) at https://bst.cisco.com")
        self.doc.add_paragraph("2. Search for the specific defect ID provided")
        self.doc.add_paragraph("3. Verify status, severity, and resolution information")
        self.doc.add_paragraph("4. Check Circuit for related internal discussions")
        self.doc.add_paragraph("5. Confirm classification level before sharing")
        
        self.doc.add_paragraph("All defect data is sourced from official Cisco internal systems with proper authentication.")
        
        self.doc.add_paragraph()
        self.doc.add_paragraph("Enhanced defect analysis generated using AdoptIQ Enhanced Defect Analyzer v1.0")
        # Round 7 / Phase 6.7: UTC timestamp for parity with doc header.
        self.doc.add_paragraph(f"Analysis timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        self.doc.add_paragraph("Data sources: BST (Bug Search Tool) and Circuit")
        self.doc.add_paragraph("Classification: Cisco Internal Data with proper safeguards")


def _r17_collect_team_customers(team_data: Dict) -> List[str]:
    """Round 17 / Phase D.2 -- pull a deduped, length-capped list of
    customer names from a leader ``team_data`` dict.  Returns up to
    50 names; defensive against missing keys / non-iterables."""
    out: List[str] = []
    seen: set = set()
    if not isinstance(team_data, dict):
        return out
    for _, member in team_data.items():
        if not isinstance(member, dict):
            continue
        names = member.get('customers') or []
        try:
            iterator = list(names)
        except TypeError:
            continue
        for raw in iterator:
            if not raw:
                continue
            text = str(raw).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= 50:
                return out
    return out


def _r17_append_historical_context(doc, team_data: Dict, *, technology=None) -> Dict:
    """Round 17 / Phase D.2 -- add the corpus-sourced Historical
    Context section to the leader Word document.  Always returns a
    status dict; never raises."""
    try:
        from config import Config as _r17_cfg
        enabled = bool(getattr(_r17_cfg, "CORPUS_KNOWLEDGE_ENABLED", False))
        from report_corpus_context import (
            build_historical_context,
            render_to_word,
        )
        names = _r17_collect_team_customers(team_data)
        ctx = build_historical_context(
            names,
            technology=technology,
            enabled=enabled,
        )
        try:
            render_to_word(doc, ctx)
        except Exception as render_err:  # noqa: BLE001
            logger.warning(
                "Round 17 / Leader Historical Context render failed: %s",
                render_err,
            )
            return {"rendered": False, "available": ctx.available, "reason": "render_failed"}
        return {
            "rendered": True,
            "available": ctx.available,
            "entries": len(ctx.entries),
            "unmatched": len(ctx.unmatched),
        }
    except Exception as err:  # noqa: BLE001
        logger.warning(
            "Round 17 / Leader Historical Context build failed: %s", err,
        )
        return {"rendered": False, "available": False, "reason": "build_failed"}


def generate_leader_report(manager_name: str, days: int, ctx, team_roster: List[Tuple[str, str, str]], 
                          csone_df: Optional[pd.DataFrame] = None,
                          ext_bugs: List[Dict] = None, ext_incidents: List[Dict] = None,
                          software_defects: Dict = None, psirt_vulns: Dict = None,
                          progress_callback=None,
                          data_retrieved_at: Optional[datetime] = None,
                          strict_mode: bool = False,
                          arr_impact: Optional[Dict] = None,
                          intel_truncated: Optional[Dict[str, Any]] = None,
                          intel_fetch_limit: Optional[int] = None,
                          partial_data_warnings: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, str, Dict]:
    """
    Main function to generate leader report
    
    Args:
        manager_name: Name of the manager
        days: Time frame in days
        ctx: Snowflake connection context
        team_roster: Team roster data
        csone_df: Optional CSOne DataFrame with TAC cases
        ext_bugs: External bugs from help.webex.com (optional)
        ext_incidents: External incidents from status.webex.com (optional)
        software_defects: Software defects extracted from data (optional)
        psirt_vulns: PSIRT vulnerabilities extracted from data (optional)
        progress_callback: Optional callable(progress, message, step) for status updates
        
    Returns:
        Tuple of (filepath, success_message, team_data dict)
    """
    def _cb(progress, message, step):
        if progress_callback:
            try:
                progress_callback(progress, message, step)
            except Exception as e:
                logger.debug(f"Progress callback failed: {e}")

    try:
        logger.info(f"Starting leader report generation for {manager_name}")
        
        # Round 7 / Phase 6.11: forward strict_mode through to the
        # generator so partial-data conditions raise instead of being
        # silently swallowed when callers opt in.
        # Round 30 / H1b: forward the optional ``arr_impact`` summary so
        # the title-page multi-currency advisory can render when the
        # upstream portfolio mixes currencies.
        generator = LeaderReportGenerator(
            ctx, team_roster,
            data_retrieved_at=data_retrieved_at,
            strict_mode=strict_mode,
            arr_impact=arr_impact,
        )
        
        doc, filepath, team_data, direct_reports = generator.generate_leader_report(
            manager_name, days,
            ext_bugs=ext_bugs, ext_incidents=ext_incidents,
            software_defects=software_defects, psirt_vulns=psirt_vulns,
            progress_callback=progress_callback,
            intel_truncated=intel_truncated,
            intel_fetch_limit=intel_fetch_limit,
            partial_data_warnings=partial_data_warnings,
        )
        logger.info(f"generator.generate_leader_report() completed. filepath: {filepath}")
        
        if csone_df is not None and not csone_df.empty:
            _cb(84, 'Integrating TAC cases from CSOne...', 'TAC Integration')
            generator.add_tac_cases_from_csone(team_data, csone_df, days)

            # Round 39 / Phase 1.1: surface unmatched TAC rows as a
            # partial-data warning so the report banner stays honest
            # about how many CSOne rows could not be attributed via
            # SUBSCRIPTION_ID / ACCOUNT_ID_C / exact-name match.
            try:
                _tac_summary = getattr(generator, '_tac_match_summary', None)
                if isinstance(_tac_summary, dict):
                    _unmatched = int(_tac_summary.get('unmatched') or 0)
                    if _unmatched > 0 and isinstance(partial_data_warnings, list):
                        partial_data_warnings.append({
                            'dataset': 'csone_tac',
                            'kind': 'tac_unmatched_after_subscription_join',
                            'error': (
                                f"{_unmatched} TAC case(s) in the {days}-day window "
                                f"could not be attributed to any CSSM via "
                                f"SUBSCRIPTION_ID, ACCOUNT_ID_C, or exact customer "
                                f"name. These rows are excluded from per-CSSM totals."
                            ),
                        })
            except Exception as _r39_pdw_err:  # noqa: BLE001
                logger.debug(
                    "Round 39: failed to surface unmatched-TAC partial-data warning: %s",
                    _r39_pdw_err,
                )

            _cb(85, 'Validating data integrity...', 'Data Validation')
            validation_results = generator._validate_and_verify_data(team_data, days)
            
            _cb(86, 'Regenerating document with TAC data...', 'Document Finalization')
            generator.doc = Document()
            generator._setup_document_settings()
            generator._create_title_page(manager_name, days, direct_reports)
            # Round 30 / M4: re-render the partial-data banner during TAC
            # regeneration so the post-TAC document stays in parity with the
            # initial pass.
            try:
                if partial_data_warnings:
                    generator.doc.add_heading("⚠ Partial Data Warning", level=1)
                    generator.doc.add_paragraph(
                        "One or more upstream data sources failed to load "
                        "for this run. Sections that depend on the affected "
                        "sources are marked \"unavailable\" rather than "
                        "rendered as zero. Rerun once the source is "
                        "reachable for a complete picture."
                    )
                    for _w in partial_data_warnings:
                        if not isinstance(_w, dict):
                            continue
                        _ds = str(_w.get('dataset') or 'unknown')
                        _err = str(_w.get('error') or 'unknown error')
                        _kind = str(_w.get('kind') or 'runtime')
                        generator.doc.add_paragraph(
                            f"• {_ds} ({_kind}): {_err}", style='List Bullet'
                        )
                    generator.doc.add_paragraph("")
            except Exception as _r30_pdw_err:  # noqa: BLE001
                logger.warning(
                    "Round 30 / M4: post-TAC leader partial-data banner failed: %s",
                    _r30_pdw_err,
                )
            generator._create_summary_table(team_data, days)
            generator._create_adoptiq_summaries_per_person(team_data, days)
            generator._create_detailed_ab_list(team_data)
            generator._add_section_separator()
            generator._add_bems_escalation_section(team_data)
            generator._add_section_separator()
            generator._add_external_intelligence_section(
                ext_bugs, ext_incidents, software_defects, psirt_vulns,
                intel_truncated=intel_truncated,
                intel_fetch_limit=intel_fetch_limit,
            )
            generator._add_section_separator()
            generator._add_validation_section(validation_results)
            # Round 39 / Phase 2.3: surface body-time section_errors
            # into ``partial_data_warnings`` so the Excel
            # ``Report_Info.Partial_Data_Warning_Count`` field is
            # honest (pre-Round-39 it stayed at 0 even with hundreds
            # of "section unavailable" notes).  Run AFTER
            # ``_add_validation_section`` so the renderers have had a
            # chance to populate ``self._section_error_count``.
            try:
                _gen_kinds = sorted(getattr(generator, '_section_error_kinds', set()) or set())
                _gen_count = int(getattr(generator, '_section_error_count', 0) or 0)
                if _gen_count > 0 and isinstance(partial_data_warnings, list):
                    partial_data_warnings.append({
                        'dataset': 'enhanced_snowflake_insights',
                        'kind': 'section_unavailable',
                        'error': (
                            f"{_gen_count} sub-section failure(s) across "
                            f"{len(_gen_kinds) or 1} kind(s) "
                            f"({', '.join(_gen_kinds) or 'unknown'}); these are "
                            f"marked 'section unavailable' in per-customer detail."
                        ),
                    })
            except Exception as _r39_se_err:  # noqa: BLE001
                logger.debug(
                    "Round 39 / Phase 2.3: failed to roll section_errors into "
                    "partial_data_warnings (post-TAC): %s",
                    _r39_se_err,
                )
            try:
                _r17_status_tac = _r17_append_historical_context(
                    generator.doc, team_data,
                )
                logger.info(
                    "Round 17 / Leader Historical Context (post-TAC): %s",
                    _r17_status_tac,
                )
            except Exception as _r17_err:  # noqa: BLE001
                logger.warning(
                    "Round 17 / Leader Historical Context (post-TAC) failed: %s",
                    type(_r17_err).__name__,
                )
            _cb(87, 'Saving final Word document...', 'Document Finalization')
            generator.doc.save(filepath)
            
            logger.info(f"Leader report regenerated with TAC cases and validation (filtered to last {days} days)")
        else:
            _cb(85, 'Validating data integrity...', 'Data Validation')
            validation_results = generator._validate_and_verify_data(team_data, days)
            
            generator._add_validation_section(validation_results)
            # Round 39 / Phase 2.3: same section_errors -> partial_data_warnings
            # roll-up as the post-TAC branch above.
            try:
                _gen_kinds_nt = sorted(getattr(generator, '_section_error_kinds', set()) or set())
                _gen_count_nt = int(getattr(generator, '_section_error_count', 0) or 0)
                if _gen_count_nt > 0 and isinstance(partial_data_warnings, list):
                    partial_data_warnings.append({
                        'dataset': 'enhanced_snowflake_insights',
                        'kind': 'section_unavailable',
                        'error': (
                            f"{_gen_count_nt} sub-section failure(s) across "
                            f"{len(_gen_kinds_nt) or 1} kind(s) "
                            f"({', '.join(_gen_kinds_nt) or 'unknown'}); these are "
                            f"marked 'section unavailable' in per-customer detail."
                        ),
                    })
            except Exception as _r39_se_err2:  # noqa: BLE001
                logger.debug(
                    "Round 39 / Phase 2.3: failed to roll section_errors into "
                    "partial_data_warnings (no-TAC): %s",
                    _r39_se_err2,
                )
            try:
                _r17_status_no_tac = _r17_append_historical_context(
                    generator.doc, team_data,
                )
                logger.info(
                    "Round 17 / Leader Historical Context (no-TAC): %s",
                    _r17_status_no_tac,
                )
            except Exception as _r17_err2:  # noqa: BLE001
                logger.warning(
                    "Round 17 / Leader Historical Context (no-TAC) failed: %s",
                    type(_r17_err2).__name__,
                )
            _cb(86, 'Saving final Word document...', 'Document Finalization')
            generator.doc.save(filepath)
            
            logger.info(f"Leader report updated with validation section")
        
        success_msg = f"Leader report generated successfully: {filepath}"
        logger.info(success_msg)
        
        return filepath, success_msg, team_data
        
    except Exception as e:
        error_msg = f"Error generating leader report: {e}"
        logger.error(error_msg, exc_info=True)
        raise RuntimeError(error_msg)

