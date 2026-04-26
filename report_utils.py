#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Report Utilities
Shared formatting, risk scoring explanation, metadata, and canonical data sources for all report types.
"""

from datetime import datetime, timezone
from typing import Optional, Union, Any, List, Tuple
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
            value = pd.to_datetime(value, errors="coerce")
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

    bands = (
        f"Categories: CRITICAL (>={_fmt(crit)}), "
        f"HIGH ({_fmt(high)}-{_fmt(crit - 1)}), "
        f"MEDIUM ({_fmt(med)}-{_fmt(high - 1)}), "
        f"LOW ({_fmt(low)}-{_fmt(med - 1)}), "
        f"HEALTHY (<{_fmt(low)})."
    )

    return (
        "\nRisk Score Methodology (0-100 scale):\n"
        "* Deterministic weighted model (same across reports): "
        "Adoption Barriers 28%, Support Cases 27%, Customer Pulse 15%, "
        "Action Plans 10%, Incidents 8%, Contract 8%, Engagement 4%.\n"
        "* Adoption barriers use derived severity, open/closed state, "
        "and aging (60+ day open barriers increase risk).\n"
        "* Support cases use normalized priority (P1-P4), BEMS "
        "detection, recency, and case-type mix (break-fix vs "
        "provisioning).\n"
        "* Customer pulse uses Poor/Bad trend impact; action plans "
        "use unresolved ratio.\n"
        "* Missing fields are treated as unknown (neutral/"
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
