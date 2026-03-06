#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Report Utilities
Shared formatting, risk scoring explanation, metadata, and canonical data sources for all report types.
"""

from datetime import datetime
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
    """Format number for consistent display in reports."""
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
        return str(value) if value is not None else "N/A"


def format_currency(value: Union[int, float, None], decimals: int = 2) -> str:
    """Format currency for reports."""
    if value is None:
        return "N/A"
    if isinstance(value, str) and not value.strip():
        return "N/A"
    try:
        v = float(value)
        if pd.isna(v) or not math.isfinite(v):
            return "N/A"
        return f"${v:,.{decimals}f}"
    except (ValueError, TypeError, OverflowError):
        return str(value) if value is not None else "N/A"


# --- Risk Scoring Explanation (transparent methodology) ---
RISK_SCORING_EXPLANATION = """
Risk Score Methodology (0–100 scale):
• Deterministic weighted model (same across reports): Adoption Barriers 28%, Support Cases 27%, Customer Pulse 15%, Action Plans 10%, Incidents 8%, Contract 8%, Engagement 4%.
• Adoption barriers use derived severity, open/closed state, and aging (60+ day open barriers increase risk).
• Support cases use normalized priority (P1-P4), BEMS detection, recency, and case-type mix (break-fix vs provisioning).
• Customer pulse uses Poor/Bad trend impact; action plans use unresolved ratio.
• Missing fields are treated as unknown (neutral/low-confidence), not auto-promoted to high severity.

Categories: CRITICAL (≥75), HIGH (55–74), MEDIUM (35–54), LOW (15–34), HEALTHY (<15).
"""


def get_risk_scoring_explanation() -> str:
    """Return the risk scoring explanation text for report inclusion."""
    return RISK_SCORING_EXPLANATION.strip()


# --- Report Metadata Footer ---
def get_report_metadata_footer(
    report_type: str,
    analysis_id: str = "",
    manager: str = "",
    customer_name: str = "",
    technology: str = "",
    days: int = 0,
) -> str:
    """Generate standardized report metadata footer text."""
    parts = [
        f"Report Type: {report_type}",
        f"Generated: {format_date(datetime.now(), 'datetime')}",
    ]
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
