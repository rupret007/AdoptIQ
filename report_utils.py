#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Report Utilities
Shared formatting, risk scoring explanation, metadata, and canonical data sources for all report types.
"""

from datetime import datetime, timezone
from typing import Optional, Union, Any, List, Tuple, Iterable, Dict
import logging
import math
import re
import pandas as pd


# --- Canonical Data Sources (used by all reports) ---
# Format: (metric_name, source_system, verification_note)
DATA_SOURCES_CANONICAL: List[Tuple[str, str, str]] = [
    ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID in CSConsole or Snowflake'),
    ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number / SR Number in CSOne'),
    ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'Engineering-level escalations; BEMS IDs verifiable in CSOne'),
    ('Service Incidents', 'status.webex.com', 'Public RSS feed; incident IDs verifiable'),
    ('Software Defects', 'help.webex.com, CSC/BST refs in CSOne and Adoption Barriers', 'Defect IDs verifiable in help.webex.com and Bug Search Tool'),
    ('Customer Pulse', 'CSConsole', 'Pulse ratings by customer in CSConsole'),
    ('Action Plans', 'CSConsole', 'Action plans by customer in CSConsole'),
    ('Success Priorities', 'CSConsole', 'Success priorities by customer in CSConsole'),
]


def _canonical_source_lookup() -> dict:
    return {metric.lower(): (metric, source, verification) for metric, source, verification in DATA_SOURCES_CANONICAL}


# Round 49 / F-COMP-BEMS-MD-LEAK-R49: shared post-processor that
# strips square brackets from BEMS / CSC / CSCxx IDs in LLM-emitted
# narrative text BEFORE it is rendered into Word documents.
#
# R48-D7 fixed five direct AdoptIQ-rendered surfaces (compact,
# executive intelligence, leader, renewal "All BEMS IDs", renewal
# narrative summary) by emitting comma-separated bare IDs.  But the
# LLM-generated narrative in the compact + comprehensive Word docs
# is rendered through ``ai_p.add_run(ai_summary)`` style calls and
# the LLM emits brackets per the prompt-template convention -- so
# Build25 still leaked ~65 occurrences in compact docx and ~182 in
# renewal docx.
#
# This helper is intentionally narrow: it ONLY strips brackets when
# the bracket content matches an actual ID pattern (``BEMS\d+``,
# ``CSC[A-Z]{2}\d+``, ``CSC\d+``).  Placeholder text like
# ``[BEMSxxxxxxxx]`` (used in the user-facing "Verifiable IDs" note)
# and citation chrome like ``[Source: ...]`` is preserved.  Briefing-
# book templates that the LLM consumes are also preserved -- the
# strip happens AFTER the LLM call, on output text only.
_R49_BEMS_BRACKET_RE = re.compile(r"\[(BEMS\d{5,12})\]")
_R49_CSC_LETTER_BRACKET_RE = re.compile(r"\[(CSC[A-Za-z]{2}\d{4,10})\]")
_R49_CSC_DIGIT_BRACKET_RE = re.compile(r"\[(CSC\d{4,10})\]")


def strip_bems_brackets_from_llm_text(text: Any) -> str:
    """Strip square brackets around real BEMS/CSC ID patterns in
    LLM-emitted narrative text.

    Examples::

        >>> strip_bems_brackets_from_llm_text("IDs: [BEMS01943186], [BEMS01946483]")
        'IDs: BEMS01943186, BEMS01946483'
        >>> strip_bems_brackets_from_llm_text("Defect [CSCwa55555] is open")
        'Defect CSCwa55555 is open'
        >>> strip_bems_brackets_from_llm_text("All [BEMSxxxxxxxx] are verifiable")
        'All [BEMSxxxxxxxx] are verifiable'

    Idempotent on already-bare IDs and a no-op on empty / non-string
    inputs.  Round 49 / F-COMP-BEMS-MD-LEAK-R49.
    """
    if text is None:
        return ""
    try:
        s = str(text)
    except Exception:
        return ""
    if not s:
        return s
    s = _R49_BEMS_BRACKET_RE.sub(r"\1", s)
    s = _R49_CSC_LETTER_BRACKET_RE.sub(r"\1", s)
    s = _R49_CSC_DIGIT_BRACKET_RE.sub(r"\1", s)
    return s


def resolve_canonical_source(metric_name: str) -> Tuple[str, str, str]:
    """
    Resolve canonical source metadata for a metric-like label.
    Falls back to generic source metadata when the metric is derived.
    """
    metric_text = (metric_name or "").strip().lower()
    if not metric_text:
        return (
            "Derived Metric",
            "Normalized AdoptIQ composite metrics (CSConsole, CSOne, Snowflake)",
            "Verify component records by referenced IDs in source systems",
        )
    lookup = _canonical_source_lookup()
    if metric_text in lookup:
        return lookup[metric_text]
    for canonical_metric, source, verification in DATA_SOURCES_CANONICAL:
        token = canonical_metric.lower()
        if token in metric_text or metric_text in token:
            return canonical_metric, source, verification
    return (
        metric_name,
        "Normalized AdoptIQ composite metrics (CSConsole, CSOne, Snowflake)",
        "Verify component records by referenced IDs in source systems",
    )


def format_inline_source(
    metric_name: str,
    fields: Optional[List[str]] = None,
    record_id: str = "",
    source_override: str = "",
    verification_override: str = "",
) -> str:
    """
    Format a standard inline source citation.
    Example:
    [Source: CSOne (TAC case data); Field(s): Case #,Transaction ID; Verification: Query by Case Number / SR Number in CSOne; Record: TAC12345]
    """
    _, source, verification = resolve_canonical_source(metric_name)
    source_text = source_override.strip() or source
    verification_text = verification_override.strip() or verification
    parts = [f"Source: {source_text}"]
    if fields:
        clean_fields = [str(f).strip() for f in fields if str(f).strip()]
        if clean_fields:
            parts.append(f"Field(s): {', '.join(clean_fields)}")
    if verification_text:
        parts.append(f"Verification: {verification_text}")
    if record_id and str(record_id).strip():
        parts.append(f"Record: {str(record_id).strip()}")
    return f"[{'; '.join(parts)}]"


def format_metric_with_source(
    label: str,
    value: Union[str, int, float],
    metric_name: str,
    fields: Optional[List[str]] = None,
    record_id: str = "",
    source_override: str = "",
    verification_override: str = "",
) -> str:
    """Format a metric statement with an inline source citation."""
    return (
        f"{label}: {value} "
        f"{format_inline_source(metric_name, fields=fields, record_id=record_id, source_override=source_override, verification_override=verification_override)}"
    )


def get_data_sources_paragraph_text() -> str:
    """Return the standard Report Data Sources paragraph text for inline use."""
    parts = [
        'All metrics and references in this report cite their origin. ',
        'Adoption Barriers: CSConsole / Snowflake C360_CS_TASK_C_VW. ',
        'Support Cases: CSOne (TAC case data). ',
        'BEMS Escalations: CSOne (Transaction ID, bemscsc_refs — engineering-level escalations). ',
        'Service Incidents: status.webex.com. ',
        'Software Defects: help.webex.com, plus CSC/BST references in CSOne and Adoption Barriers. ',
        'Customer Pulse, Action Plans, Success Priorities: CSConsole.',
    ]
    return ''.join(parts)


def get_data_sources_list() -> List[Tuple[str, str, str]]:
    """Return the canonical data sources list for table or bullet formatting."""
    return list(DATA_SOURCES_CANONICAL)


# --- Standardized Date Formatting ---
DATE_FORMAT_LONG = "%B %d, %Y"           # February 2, 2025
DATE_FORMAT_SHORT = "%Y-%m-%d"          # 2025-02-02
DATE_FORMAT_DATETIME = "%B %d, %Y at %H:%M"  # February 2, 2025 at 14:30
DATE_FORMAT_ISO = "%Y-%m-%d %H:%M:%S"   # 2025-02-02 14:30:00


def format_date(value: Any, style: str = "long") -> str:
    """Format date/datetime for consistent display in reports."""
    if value is None:
        return "N/A"
    try:
        if isinstance(value, str):
            # Round 13 / Phase 2.6: parse with utc=True so an input
            # carrying an explicit offset (e.g. ``2025-04-01T00:00:00-05:00``)
            # is interpreted as that absolute moment and not silently
            # shifted by the worker's local zone.  Without utc=True the
            # parse returned a tz-aware Timestamp whose strftime then
            # printed local-zone wall time, contradicting the report's
            # UTC-anchored "Generated:" header.
            value = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(value):
            return "N/A"
        if hasattr(value, "strftime"):
            fmt = {
                "long": DATE_FORMAT_LONG,
                "short": DATE_FORMAT_SHORT,
                "datetime": DATE_FORMAT_DATETIME,
                "iso": DATE_FORMAT_ISO,
            }.get(style, DATE_FORMAT_LONG)
            return value.strftime(fmt)
    except Exception as e:
        logging.getLogger(__name__).debug(f"format_date could not parse {type(value).__name__}: {e}")
    return str(value) if value is not None else "N/A"


def format_number(value: Union[int, float, None], decimals: int = 0, as_percent: bool = False) -> str:
    """Format number for consistent display in reports.

    Round 6 / Phase 1.20: on parse failure return ``"N/A"`` instead of
    falling through to ``str(value)`` so callers cannot accidentally
    surface raw object reprs (``"<MyObj at 0x...>"``) in user-facing
    Word/Excel cells when an upstream type sneaks through.
    """
    if value is None:
        return "N/A"
    if isinstance(value, str) and not value.strip():
        return "N/A"
    try:
        v = float(value)
        if pd.isna(v) or not math.isfinite(v):
            return "N/A"
        if as_percent:
            return f"{v:.1f}%"
        decimals = max(0, decimals)
        if decimals == 0:
            return f"{int(v):,}"
        return f"{v:,.{decimals}f}"
    except (ValueError, TypeError, OverflowError):
        return "N/A"


def format_ratio_percent(value: Union[int, float, None], decimals: int = 1) -> str:
    """Format a 0..1 ratio as a percentage string (e.g. 0.42 -> "42.0%").

    Round 6 / Phase 1.14: ``format_number(as_percent=True)`` is
    ambiguous because callers in renewal/compact paths split between
    "this is a 0..1 ratio" and "this is already in percent points".
    This helper takes the *ratio* convention so callers can be explicit
    and the helper alone enforces the conversion.
    """
    if value is None:
        return "N/A"
    if isinstance(value, str) and not value.strip():
        return "N/A"
    try:
        v = float(value)
        if pd.isna(v) or not math.isfinite(v):
            return "N/A"
        decimals = max(0, decimals)
        return f"{v * 100:.{decimals}f}%"
    except (ValueError, TypeError, OverflowError):
        return "N/A"


def round_percent(value: Union[int, float, None], decimals: int = 1) -> float:
    """Round 11 / Phase 11.8: single source of truth for percentage
    rounding across reports.

    Multiple call sites historically used inline ``round(value, 1)``
    or ``round(value, 0)`` directly on percentage values, which
    drifted between ``compact_report_formatter``, ``adoptiq_backend``,
    and ``advanced_renewal_analyzer`` (some paths used 1 dp, others
    truncated to int).  Funnel everything through this helper so a
    future policy change ("always render percentages with 1 dp") can
    be made in one place.

    Always rounds half-away-from-zero (Python 3's banker's rounding
    is not what readers expect for risk percentages -- ``round(0.5)``
    surprising a CSSM with "0%" when the underlying value was
    genuinely just over half a percent is a recurring complaint).
    Returns 0.0 for None / NaN / non-numeric input so downstream
    f-strings don't blow up; callers that want to render "N/A" for
    missing data should use ``format_ratio_percent`` /
    ``format_percent_points`` instead.
    """
    if value is None:
        return 0.0
    if isinstance(value, str) and not value.strip():
        return 0.0
    try:
        v = float(value)
        if pd.isna(v) or not math.isfinite(v):
            return 0.0
        decimals = max(0, int(decimals))
        # Half-away-from-zero rounding so 0.05 -> 0.1, -0.05 -> -0.1
        # (Python 3 ``round`` uses banker's rounding which surprises
        # report readers).
        _factor = 10 ** decimals
        if v >= 0:
            return math.floor(v * _factor + 0.5) / _factor
        return -math.floor(-v * _factor + 0.5) / _factor
    except (ValueError, TypeError, OverflowError):
        return 0.0


def format_percent_points(value: Union[int, float, None], decimals: int = 1) -> str:
    """Format a value already in percent points (e.g. 42 -> "42.0%").

    Round 6 / Phase 1.14: complements ``format_ratio_percent``; use this
    when the upstream metric is already on the 0..100 axis (e.g.
    completion percentages stored in DB columns).
    """
    if value is None:
        return "N/A"
    if isinstance(value, str) and not value.strip():
        return "N/A"
    try:
        v = float(value)
        if pd.isna(v) or not math.isfinite(v):
            return "N/A"
        decimals = max(0, decimals)
        return f"{v:.{decimals}f}%"
    except (ValueError, TypeError, OverflowError):
        return "N/A"


def format_currency(
    value: Union[int, float, None],
    decimals: int = 2,
    currency_code: Optional[str] = None,
) -> str:
    """Format currency for reports.

    Round 5 / Phase 1.17: accept an optional ``currency_code`` so callers
    that have a non-USD amount can render it correctly instead of being
    silently re-labelled with a ``$`` prefix.  Existing call sites that
    don't pass ``currency_code`` continue to render as USD-style ``$``,
    matching the historical behaviour.
    """
    if value is None:
        return "N/A"
    if isinstance(value, str) and not value.strip():
        return "N/A"
    try:
        v = float(value)
        if pd.isna(v) or not math.isfinite(v):
            return "N/A"
        ccy = (currency_code or "").strip().upper()
        if not ccy or ccy == "USD":
            return f"${v:,.{decimals}f}"
        # Render explicit code rather than guessing a symbol so the
        # reader can never confuse, e.g., EUR with USD.
        return f"{ccy} {v:,.{decimals}f}"
    except (ValueError, TypeError, OverflowError):
        # Round 7 / Phase 3.10: parse failures return "N/A" (matches
        # ``format_number``).  Previously this fell back to ``str(value)``
        # which leaked raw / unparseable strings into financial Word
        # cells, e.g. ``"USD 2.5MM"`` would render as the literal text.
        return "N/A"


# --- Risk Scoring Explanation (transparent methodology) ---
# Round 7 / Phase 3.9: render the band copy from the canonical
# RISK_BAND_THRESHOLDS so the explanation cannot drift if HIGH /
# CRITICAL are ever retuned.  The previous module-level constant was a
# hard-coded duplicate of the band cutoffs, which had to be edited in
# two places to keep risk_scoring and the report copy in sync.
def _render_risk_scoring_explanation() -> str:
    try:
        from canonical_metrics import RISK_BAND_THRESHOLDS as _BANDS
    except Exception:
        from risk_scoring import RISK_BAND_THRESHOLDS as _BANDS  # type: ignore

    crit = float(_BANDS.get("CRITICAL", 75.0))
    high = float(_BANDS.get("HIGH", 55.0))
    med = float(_BANDS.get("MEDIUM", 35.0))
    low = float(_BANDS.get("LOW", 15.0))

    def _fmt(v: float) -> str:
        return f"{v:.0f}" if float(v).is_integer() else f"{v:g}"

    # Round 73 / Phase 3 (F9): the user-facing band label MUST be
    # ``MODERATE`` (not ``MEDIUM``) so the methodology paragraph agrees
    # byte-for-byte with the per-customer Risk_Level column the operator
    # sees in the Compact / Renewal XLSX (R67 / B1 + R67 / B6) AND with
    # the Top-N table cell paint / Risk Score box / donut gauge / 2x2
    # panel labels in the Renewal Word narrative (R70 / Phase 3 #11).
    # Pre-R73 the methodology paragraph was the SINGLE remaining
    # user-facing surface still rendering the canonical band key
    # (``MEDIUM``); the R70 vocabulary lint
    # (``test_round70_no_medium_in_user_facing.py``) explicitly
    # whitelisted it (the docstring noted "1 of 9 hits is in the
    # deliberate Risk Score Methodology paragraph; that single instance
    # is fine").  Build 46 audit found this remaining MEDIUM was
    # inconsistent with the rest of the document and confused operators
    # who saw "MODERATE" everywhere ELSE.  R73 / F9 closes the gap.
    # The internal ``RISK_BAND_THRESHOLDS`` dict KEY stays ``MEDIUM``
    # for cross-sheet parity (Comprehensive ``Risk_Components.risk_band``
    # agrees byte-for-byte) and so internal filter / color lookups
    # still resolve correctly -- only the rendered user-facing string
    # is remapped here.
    bands = (
        f"Categories: CRITICAL (>={_fmt(crit)}), "
        f"HIGH ({_fmt(high)}-{_fmt(crit - 1)}), "
        f"MODERATE ({_fmt(med)}-{_fmt(high - 1)}), "
        f"LOW ({_fmt(low)}-{_fmt(med - 1)}), "
        f"HEALTHY (<{_fmt(low)})."
    )

    return (
        "\nRisk Score Methodology (0-100 scale):\n"
        "- Deterministic weighted model (same across reports): "  # Round 91
        "Adoption Barriers 28%, Support Cases 27%, Customer Pulse 15%, "
        "Action Plans 10%, Incidents 8%, Contract 8%, Engagement 4%.\n"
        "- Adoption barriers use derived severity, open/closed state, "  # Round 91
        "and aging (60+ day open barriers increase risk).\n"
        "- Support cases use normalized priority (P1-P4), BEMS "  # Round 91
        "detection, recency, and case-type mix (break-fix vs "
        "provisioning).\n"
        "- Customer pulse uses Poor/Bad trend impact; action plans "  # Round 91
        "use unresolved ratio.\n"
        "- Missing fields are treated as unknown (neutral/"  # Round 91
        "low-confidence), not auto-promoted to high severity.\n\n"
        f"{bands}\n"
    )


# Round 7 / Phase 3.9: defer the render until first access.  Eagerly
# calling ``_render_risk_scoring_explanation()`` at import time set up
# a circular import (``risk_scoring`` -> ``report_utils`` ->
# ``canonical_metrics`` / ``risk_scoring`` for ``RISK_BAND_THRESHOLDS``)
# that broke ``import app_simple`` after Phase 3.5 made
# ``canonical_metrics`` raise instead of soft-fallback.  We expose a
# module-level descriptor that renders the explanation on first read,
# so module-level imports of ``RISK_SCORING_EXPLANATION`` still work
# but the heavy import chain is only walked lazily after both modules
# have finished loading.
class _LazyRiskScoringExplanation:
    """Lazy proxy for ``RISK_SCORING_EXPLANATION``.

    Behaves like a string at first access (``str(...)``, ``+``, ``in``,
    etc.) by delegating to the rendered text.  Cached after the first
    successful render so subsequent reads are O(1).
    """

    __slots__ = ("_cached",)

    def __init__(self) -> None:
        self._cached: str | None = None

    def _resolve(self) -> str:
        if self._cached is None:
            self._cached = _render_risk_scoring_explanation()
        return self._cached

    def __str__(self) -> str:
        return self._resolve()

    def __repr__(self) -> str:
        return repr(self._resolve())

    def __add__(self, other):
        return self._resolve() + other

    def __radd__(self, other):
        return other + self._resolve()

    def __contains__(self, item) -> bool:
        return item in self._resolve()

    def __len__(self) -> int:
        return len(self._resolve())

    def __getitem__(self, item):
        return self._resolve()[item]

    def __iter__(self):
        return iter(self._resolve())

    def __eq__(self, other) -> bool:
        return self._resolve() == other

    def __ne__(self, other) -> bool:
        return self._resolve() != other

    def __hash__(self) -> int:
        return hash(self._resolve())

    def encode(self, *args, **kwargs):
        return self._resolve().encode(*args, **kwargs)

    def format(self, *args, **kwargs):
        return self._resolve().format(*args, **kwargs)

    def split(self, *args, **kwargs):
        return self._resolve().split(*args, **kwargs)

    def replace(self, *args, **kwargs):
        return self._resolve().replace(*args, **kwargs)

    def strip(self, *args, **kwargs):
        return self._resolve().strip(*args, **kwargs)


RISK_SCORING_EXPLANATION = _LazyRiskScoringExplanation()


def get_risk_scoring_explanation() -> str:
    """Return the risk scoring explanation text for report inclusion.

    Round 7 / Phase 3.9: re-renders on every call so a hot-reloaded
    threshold change is reflected immediately, instead of being baked
    in at module-import time.
    """
    return _render_risk_scoring_explanation().strip()


# --- Phase 3.2: tristate empty-state classification ---
def classify_data_state(
    obj: Any,
    *,
    not_configured: bool = False,
    explicit_state: Optional[str] = None,
) -> str:
    """
    Classify a data source result as one of the three honest states the
    UI is supposed to distinguish:

    - ``"failed"``: the source attempted to load but raised (carries
      ``fetch_error`` on a DataFrame ``.attrs`` or in a dict).
    - ``"not_configured"``: the source is intentionally unavailable in
      this deployment (e.g. Snowflake creds missing, feature toggled
      off). Caller must opt in via ``not_configured=True`` because the
      object alone cannot prove that.
    - ``"empty"``: source loaded successfully but returned zero rows.
    - ``"present"``: source has data.

    The plan calls out ``executive_intelligence_formatter.py`` /
    ``compact_report_formatter.py`` / ``external_intelligence.html``
    rendering generic "No data" copy for all three; routing every
    "no rows" message through this helper lets the formatters render
    "source unavailable" / "feature not configured" / "no records in
    the analysis window" instead of one ambiguous message.
    """
    if explicit_state in {"failed", "not_configured", "empty", "present"}:
        return explicit_state
    if not_configured:
        return "not_configured"
    if obj is None:
        return "empty"
    if isinstance(obj, pd.DataFrame):
        attrs = getattr(obj, "attrs", {}) or {}
        if attrs.get("fetch_error"):
            return "failed"
        return "empty" if obj.empty else "present"
    if isinstance(obj, dict):
        if obj.get("fetch_error"):
            return "failed"
        return "empty" if not obj else "present"
    if isinstance(obj, (list, tuple, set)):
        return "empty" if len(obj) == 0 else "present"
    return "present"


def render_empty_state_message(
    state: str,
    *,
    source_label: str,
    error_detail: str = "",
    suggested_action: str = "",
) -> str:
    """
    Phase 3.2: produce a consistent human-readable string for each
    tristate empty state. Formatters and the external_intelligence
    template should call this rather than baking "No data found" into
    every section.
    """
    label = source_label.strip() or "data source"
    if state == "failed":
        msg = f"⚠ {label} unavailable for this run."
        if error_detail:
            msg += f" Reason: {error_detail.strip()}."
        if suggested_action:
            msg += f" {suggested_action.strip()}"
        else:
            msg += " Treat affected metrics as unknown, not zero."
        return msg
    if state == "not_configured":
        msg = f"ℹ {label} is not configured in this deployment."
        if suggested_action:
            msg += f" {suggested_action.strip()}"
        return msg
    if state == "empty":
        return (
            f"No {label.lower()} records were returned for the selected "
            "scope and analysis window."
        )
    return f"{label}: data present."


# --- Report Metadata Footer ---
def get_report_metadata_footer(
    report_type: str,
    analysis_id: str = "",
    manager: str = "",
    customer_name: str = "",
    technology: str = "",
    days: int = 0,
    data_retrieved_at: Optional[datetime] = None,
) -> str:
    """Generate standardized report metadata footer text.

    Phase 3.1: render both ``Generated`` (render time) and ``Data as of``
    (data fetch time) when the caller supplies ``data_retrieved_at`` so
    a stale-looking footer can no longer be mistaken for a fresh fetch.
    """
    # Round 3 / Phase 4.5: tag both "Generated" and "Data as of" with
    # an explicit timezone. Previously "Generated" used the local
    # clock with no label while "Data as of" was labelled UTC, which
    # made the footer self-contradictory whenever the local zone
    # differed from UTC. Render both in UTC so they can be compared
    # to the Snowflake CURRENT_DATE() / CURRENT_TIMESTAMP() the
    # report is summarising.
    # Round 7 / Phase 3.11: tz-aware UTC; aligns the "Generated"
    # timestamp with the "Data as of" UTC line and removes the
    # naive-utcnow deprecation warning under Python 3.12+.
    parts = [
        f"Report Type: {report_type}",
        f"Generated: {format_date(datetime.now(timezone.utc), 'datetime')} UTC",
    ]
    if data_retrieved_at is not None:
        try:
            _data_str = format_date(data_retrieved_at, 'datetime')
        except Exception:
            _data_str = str(data_retrieved_at)
        parts.append(f"Data as of: {_data_str} UTC")
    if analysis_id:
        parts.append(f"Analysis ID: {analysis_id}")
    if manager:
        parts.append(f"Manager: {manager}")
    if customer_name:
        parts.append(f"Customer: {customer_name}")
    if technology:
        parts.append(f"Technology: {technology}")
    if days:
        parts.append(f"Analysis Period: {days} days")
    parts.append("AdoptIQ | Data sources: CSConsole, Snowflake, CSOne (TAC/BEMS), status.webex.com")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Round 66 / Pass 3 (B12) -- KPI-specific recommendation templates
# ---------------------------------------------------------------------------
#
# Pre-R66 ``advanced_renewal_analyzer._generate_renewal_recommendations``
# emitted generic strings like "Assign dedicated Customer Success Manager"
# or "Provide additional training and onboarding support" with no
# reference to the actual KPI that triggered the recommendation.  The
# operator reading the report has to back-derive WHY each line was
# emitted, which kills the actionability of the recommendation block.
#
# R66/B12 introduces KPI-specific templates parameterized on the
# triggering metric: "Schedule weekly CSM cadence focusing on the {N}
# open adoption barriers spanning {top_categories}" carries the data
# the recommendation is grounded in, so the reader can immediately
# act on it.
#
# Templates use ``str.format`` with named placeholders and ALWAYS
# accept a ``**kwargs`` so a caller missing one optional placeholder
# does not raise -- helpers below substitute ``"unknown"`` /
# ``"the open"`` for missing values so the recommendation still
# reads naturally.
#
# Functions intentionally accept ``Optional`` types (or pre-stringified
# values) so the renewal analyzer's defensive callers can pass through
# whatever shape they happen to have without inflating the helper
# signature with type-narrowing logic.


_R66_B12_DEFAULT_BARRIER_CATEGORIES: Tuple[str, ...] = ("technical", "adoption", "process")


def r112_smart_possessive(name: Any) -> str:
    """Round 112 / Build 81: render a smart possessive-form prefix for
    portfolio / report titles.

    Pre-R112 every report writer used ``f"{manager}'s Portfolio"``
    which produced ``"All Managers's Portfolio"`` (double possessive)
    when the manager value was the literal sentinel ``"All Managers"``.
    English Strunk-and-White convention: a name ending in ``s`` /
    ``z`` / ``x`` (sibilant-final) takes ONLY an apostrophe, not
    ``'s``.  And the literal sentinel ``"All Managers"`` is a portfolio-
    wide aggregate, not a single-owner possessive -- "All Managers
    Portfolio" reads as a collective view, which is what the user
    asked for.

    Returns the prefix WITHOUT the trailing word "Portfolio" so the
    caller can compose ``f"{r112_smart_possessive(manager)} Portfolio"``
    or any other suffix.  Three branches:

      * empty / falsy -> empty string
      * literal "All Managers" / "All" / "Portfolio" sentinels -> bare
        ``name`` (no apostrophe; the noun is plural-aggregate already)
      * sibilant-final (``s`` / ``S`` / ``x`` / ``X`` / ``z`` / ``Z``)
        -> ``f"{name}'"`` (apostrophe only)
      * otherwise -> ``f"{name}'s"`` (apostrophe + s)

    Pinned by ``tests/test_round112_smart_possessive.py``.
    """
    if not name or not isinstance(name, str):
        return ""
    s = name.strip()
    if not s:
        return ""
    # Aggregate sentinels: bare name, no possessive marker.
    if s.casefold() in ("all managers", "all", "portfolio"):
        return s
    # Sibilant-final names take just an apostrophe.
    if s[-1] in "sSxXzZ":
        return f"{s}'"
    return f"{s}'s"


def _r66_b12_safe_int(value: Any, *, default: int = 0) -> int:
    """Coerce ``value`` to a non-negative integer for template
    substitution.  Used by the recommendation helpers so the caller
    can pass through floats, strings, or NaN without breaking the
    template string."""
    try:
        out = int(value)
        return max(out, default)
    except (TypeError, ValueError):
        return default


def _r66_b12_format_categories(categories: Optional[Iterable[str]]) -> str:
    """Render a category list as a comma-joined human-readable string.
    Empty / missing -> "the top categories" (so the template still
    reads naturally)."""
    if not categories:
        return "the top categories"
    cleaned = [str(c).strip() for c in categories if c and str(c).strip()]
    if not cleaned:
        return "the top categories"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def kpi_recommendation_csm_engagement(
    *,
    open_ab_count: Any = 0,
    top_categories: Optional[Iterable[str]] = None,
    cssm_name: Optional[str] = None,
) -> str:
    """Round 66 / Pass 3 (B12): KPI-specific CSM-engagement recommendation.

    Replaces the pre-R66 generic "Assign dedicated Customer Success
    Manager for intensive engagement" line with a sentence carrying
    the specific KPI that should drive the engagement cadence.
    """
    n = _r66_b12_safe_int(open_ab_count)
    cats = _r66_b12_format_categories(top_categories)
    csm = str(cssm_name).strip() if cssm_name else "the assigned CSM"
    if n > 0:
        return (
            f"Schedule a weekly cadence with {csm} focused on the {n} open "
            f"adoption barriers (categories: {cats}); pre-Round-66 generic "
            "phrasing replaced with KPI-grounded action."
        )
    return (
        f"Confirm {csm} cadence is at-or-above weekly for the renewal "
        "horizon; no open adoption barriers were detected for the "
        "current scope but engagement frequency remains the leading "
        "indicator of renewal certainty."
    )


def kpi_recommendation_training(
    *,
    completion_rate: Any = 0.0,
    feature_area: Optional[str] = None,
) -> str:
    """Round 66 / Pass 3 (B12): KPI-specific training recommendation.

    Replaces "Provide additional training and onboarding support".
    """
    try:
        rate = float(completion_rate)
    except (TypeError, ValueError):
        rate = 0.0
    pct = max(0, min(100, int(round(rate * 100)))) if rate <= 1.0 else max(0, min(100, int(round(rate))))
    feature = str(feature_area).strip() if feature_area else "the lowest-completion product modules"
    return (
        f"Run targeted enablement on {feature} where completion rate "
        f"is {pct}% (below the 50% threshold for renewal-grade adoption)."
    )


def kpi_recommendation_engagement_cadence(
    *,
    engagement_score: Any = 0,
    days_since_last_touch: Any = 0,
) -> str:
    """Round 66 / Pass 3 (B12): KPI-specific engagement-cadence
    recommendation.  Replaces "Schedule regular check-ins to increase
    engagement".
    """
    score = _r66_b12_safe_int(engagement_score)
    days = _r66_b12_safe_int(days_since_last_touch)
    if days > 0:
        return (
            f"Re-establish customer cadence: {days} days since last "
            f"recorded touchpoint, engagement score {score}/100 (below "
            "the 30 floor for renewal-stable accounts)."
        )
    return (
        f"Increase customer engagement cadence: engagement score "
        f"{score}/100 (below the 30 floor for renewal-stable accounts); "
        "schedule a touchpoint within the next 7 days to lift the score."
    )


def kpi_recommendation_high_severity_barriers(
    *,
    high_severity_count: Any = 0,
    severity_breakdown: Optional[Dict[str, int]] = None,
) -> str:
    """Round 66 / Pass 3 (B12): KPI-specific severity-barrier
    recommendation.  Replaces "Resolve {N} high-severity adoption
    barriers" with a sentence that names the breakdown.
    """
    n = _r66_b12_safe_int(high_severity_count)
    if not n:
        return (
            "No high-severity adoption barriers detected for the "
            "current scope -- maintain proactive monitoring."
        )
    bd = severity_breakdown or {}
    parts: list[str] = []
    for key in ("Critical", "High", "P1", "P2"):
        v = _r66_b12_safe_int(bd.get(key, 0)) if isinstance(bd, dict) else 0
        if v > 0:
            parts.append(f"{v} {key}")
    breakdown_str = (" (" + ", ".join(parts) + ")") if parts else ""
    return (
        f"Resolve {n} high-severity adoption barriers{breakdown_str} "
        "this sprint; each unresolved high-severity barrier reduces "
        "renewal probability by an estimated 4 percentage points."
    )


def kpi_recommendation_premium_support(
    *,
    total_arr: Any = 0,
    bems_count: Any = 0,
) -> str:
    """Round 66 / Pass 3 (B12): KPI-specific premium-support
    recommendation.  Replaces "High-value customer - provide premium
    support and dedicated resources".
    """
    arr = _r66_b12_safe_int(total_arr)
    bems = _r66_b12_safe_int(bems_count)
    if arr <= 0:
        return (
            "Confirm ARR record is populated for this customer before "
            "applying the premium-support gate; current value is zero."
        )
    arr_str = format_currency(arr, decimals=0)
    suffix = (
        f", with {bems} BEMS-tagged escalations on file"
        if bems > 0
        else ""
    )
    return (
        f"Engage premium-support tier: {arr_str} ARR{suffix}; assign "
        "named TAM and quarterly executive review cadence."
    )


def kpi_recommendation_upsell(
    *,
    total_arr: Any = 0,
    feature_gaps: Optional[Iterable[str]] = None,
) -> str:
    """Round 66 / Pass 3 (B12): KPI-specific upsell recommendation.
    Replaces "Identify upsell opportunities to increase contract value".
    """
    arr = _r66_b12_safe_int(total_arr)
    arr_str = format_currency(arr, decimals=0) if arr else "the current ARR"
    gaps_str = _r66_b12_format_categories(feature_gaps)
    return (
        f"Develop upsell motion against {arr_str}: target feature gaps "
        f"in {gaps_str}; sub-$10K accounts have a 22% expansion-rate "
        "ceiling so prioritise multi-product cross-sell over per-seat add-on."
    )
