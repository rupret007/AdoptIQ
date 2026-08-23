"""Canonical delivery adapter for legacy Compact, Renewal, and Subscription artifacts.

The legacy report writers use several workbook sheet names and public column
labels for the same source datasets.  This module converts those already-
scoped workbook records into the shared decision-report fact contract without
re-fetching data or widening scope.

The legacy workbook is retained as input evidence.  The concise Word target and
its paired ``AdoptIQ_Source_Data_*.xlsx`` workbook are assembled in temporary
files, reopened, and validated before either target is replaced.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document

import canonical_metrics as cm
import decision_report_delivery as delivery


class CanonicalReportAdapterError(RuntimeError):
    """Raised when a legacy artifact cannot satisfy the canonical contract."""


_CORE_SHEET_ALIASES: Mapping[str, tuple[str, ...]] = {
    "subscriptions": (
        "Subscriptions",
        "Subscription_Details",
        "Subscription_Data",
    ),
    "action_plans": (
        "Action_Plans",
        "Customer_Action_Plans",
        "CSConsole_Action_Plans",
    ),
    "adoption_barriers": (
        "Adoption_Barriers",
        "All_Adoption_Barriers",
        "Customer_Adoption_Barriers",
        "AB_Detail_All",
        "Critical_Adoption_Barriers",
    ),
    "customer_pulse": (
        "Customer_Pulse",
        "Customer_Customer_Pulse",
        "CSConsole_Customer_Pulse",
    ),
    "tac_cases": (
        "TAC_Cases",
        "All_Support_Cases",
        "Customer_Support_Cases",
        "CSOne_Detail_All",
        "Escalated_Cases",
    ),
    "success_priorities": (
        "Success_Priorities",
        "Customer_Success_Priorities",
        "CSConsole_Success_Priorities",
    ),
}

_CANONICAL_SHEET_BY_KEY: Mapping[str, str] = {
    "subscriptions": "Subscriptions",
    "action_plans": "Action_Plans",
    "adoption_barriers": "Adoption_Barriers",
    "customer_pulse": "Customer_Pulse",
    "tac_cases": "TAC_Cases",
    "success_priorities": "Success_Priorities",
}

# Logical source systems are part of the canonical fact contract.  A workbook
# sheet is a transport, not a source system, so legacy adapters must not turn
# paths such as ``All_Support_Cases`` into public provenance claims.
_CANONICAL_SOURCE_SYSTEM_BY_KEY: Mapping[str, str] = {
    "subscriptions": "Snowflake subscriptions",
    "action_plans": "Snowflake C360 Action Plans",
    "adoption_barriers": "Snowflake C360 Adoption Barriers",
    "customer_pulse": "Snowflake C360 Customer Pulse",
    "tac_cases": "CSOne",
    "success_priorities": "Snowflake C360 Success Priorities",
    "external_incidents": "status.webex.com",
    "external_bugs": "help.webex.com",
}

_SUBSCRIPTION_FALLBACK_ALIASES: Mapping[str, tuple[str, ...]] = {
    # Compact and Renewal now export their real scoped subscription rows.
    # Their Risk/Renewal summaries are derived family facts and cannot safely
    # stand in for subscription IDs, products, technology, status, or terms.
    # If an older workbook lacks a subscription sheet, the source is marked
    # unavailable while the summary remains preserved as family facts.
    "compact": (),
    "renewal": (),
    "subscription": ("Summary",),
}

# These sheets contain report-family facts rather than raw source records.
# They must not disappear merely because one of them was also used as an
# identity fallback for the canonical ``Subscriptions`` sheet.
_FAMILY_FACT_SHEET_ALIASES = (
    # Compact presentation/subset sheets.  Their canonical source records are
    # already carried by All_Adoption_Barriers / All_Support_Cases; treating
    # these expected views as unmapped evidence produced four false
    # "legacy_sheet_quarantined" warnings and made an otherwise identical
    # Compact scope look less trustworthy than the other report families.
    "Executive_Dashboard",
    "High_Risk_Customers",
    "Critical_Adoption_Barriers",
    "Escalated_Cases",
    "Analysis_Summary",
    "Risk_Summary",
    "Renewal_Summary",
    "Renewal_Commercial_Facts",
    "Subscription_Summary",
    "Summary",
    "Account_Summary",
    "Risk_Components",
    "Risk Components",
    "Recommendations",
    "Key_Metrics",
    "Data_Sources",
    "Data Sources",
)

_RISK_SCORE_FIELD_TOKENS = {
    "overallriskscore",
    "renewalriskscore",
    "riskscore",
    "riskscore010",
    "riskscore0100",
}
_RISK_BAND_FIELD_TOKENS = {
    "overallriskband",
    "renewalriskband",
    "riskband",
    "risklevel",
}
_COMPACT_DISPLAY_RISK_SCORE_FIELD_TOKENS = {
    "overallriskscore",
    "riskscore010",
}
_COMPACT_RISK_SCORE_DISPOSITION = "Legacy 0-10 display score excluded; use Account_Summary.Risk_Score_0_100"
_AMBIGUOUS_RISK_DISPOSITION = (
    "Ambiguous customer label; legacy risk claim quarantined; use Account_Summary canonical risk where available"
)
_CUSTOMER_FIELD_TOKENS = {
    "account",
    "accountname",
    "buname",
    "customer",
    "customername",
    "relatedcustomer",
}
_ACCOUNT_ID_FIELD_TOKENS = {
    "accountid",
    "accountidc",
    "accountc",
    "selectedaccountid",
}
_SUBSCRIPTION_ID_FIELD_TOKENS = {
    "subscriptionid",
    "selectedsubscriptionid",
}
_CSSM_FIELD_TOKENS = {
    "cssm",
    "cssmname",
    "teammember",
}
_FAMILY_CONTEXT_FIELD_TOKENS = (
    _CUSTOMER_FIELD_TOKENS
    | _ACCOUNT_ID_FIELD_TOKENS
    | _SUBSCRIPTION_ID_FIELD_TOKENS
    | _CSSM_FIELD_TOKENS
    | {
        "attributedteammembers",
        "recordid",
        "recordiddataquality",
        "scopetype",
        "scopevalue",
        "sourcesystem",
    }
)

_EXTERNAL_SHEET_ALIASES: Mapping[str, tuple[str, ...]] = {
    "external_incidents": (
        "External_Incidents",
        "External Incidents",
        "Incidents",
    ),
    "external_bugs": (
        "External_Bugs",
        "External Bugs",
        "External_Defects",
        "External Defects",
        "Bugs",
    ),
}

_PUBLIC_CONTEXT_HEADERS: Mapping[str, tuple[str, ...]] = {
    "Record_ID": ("Record ID", "Record Id"),
    "Record_ID_Data_Quality": ("Record ID Data Quality",),
    "Scope_Type": ("Scope Type",),
    "Scope_Value": ("Scope Value",),
    "Source_System": ("Source System",),
    "Attributed_Team_Members": ("Attributed Team Members",),
    "CSSM_EMAIL": ("CSSM Email",),
}

_ATTRIBUTION_COLUMNS = (
    "Attributed_Team_Members",
    "Attributed Team Members",
    "CSSM",
    "CSSM_NAME",
    "CSSM Name",
    "Team_Member",
    "Team Member",
    "CSSM_EMAIL",
    "CSSM Email",
    "PRIMARY_DSM_EMAIL",
    "PRIMARY_CSSM_EMAIL",
    "assignee_cssm_email",
)
_UNASSIGNED_BUNDLE = "__legacy_unassigned__"
_UNASSIGNED_ATTRIBUTION_LABELS = {
    _UNASSIGNED_BUNDLE,
    "unassigned",
    "unassigned / portfolio",
    "portfolio / unassigned",
}

_PUBLIC_HEADERS_BY_KEY: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "subscriptions": {
        "SUBSCRIPTION_ID": ("Subscription ID", "Subscription Id"),
        "ACCOUNT_ID_C": ("Account ID", "Account Id"),
        "BU_NAME": ("Customer Name", "Customer_Name", "Account Name"),
        "STATUS_C": ("Status",),
    },
    "action_plans": {
        "ACCOUNT_ID_C": ("Account ID", "Account Id"),
        "BU_NAME": ("Customer Name", "Customer_Name", "Account Name"),
        "SUBJECT_C": ("Subject",),
        "ACTION_PLAN_TITLE_C": ("Action Plan Title",),
        "DESCRIPTION_C": ("Description",),
        "STATUS_C": ("Status",),
        "PRIORITY_C": ("Priority",),
        "OPEN_DATE_C": ("Open Date",),
        "CREATED_DATE_C": ("Created Date",),
        "DUE_DATE_C": ("Due Date",),
        "CLOSED_DATE_C": ("Closed Date",),
        "NEXT_ACTION_C": ("Next Action",),
        "NEXT_STEP_C": ("Next Step",),
        "NEXT_ACTION_OWNER_C": ("Next Action Owner",),
        "ASSIGNEE_C": ("Assignee",),
        "COMMENTS_C": ("Comments",),
        "CURRENT_STATUS_AND_NOTES_C": ("Current Status / Notes",),
    },
    "adoption_barriers": {
        "ACCOUNT_ID_C": ("Account ID", "Account Id"),
        "BU_NAME": ("Customer Name", "Customer_Name", "Account Name"),
        "SUBJECT_C": ("Subject",),
        "DESCRIPTION_C": ("Description",),
        "AB_STATUS_C": ("Adoption Barrier Status",),
        "STATUS_C": ("Status",),
        "SEVERITY_C": ("Severity",),
        "PRIORITY_C": ("Priority",),
        "OPEN_DATE_C": ("Open Date",),
        "DUE_DATE_C": ("Due Date",),
        "CLOSED_DATE_C": ("Closed Date",),
        "AB_CATEGORY_C": ("Barrier Category",),
        "ab_category_final": ("Barrier Category (Final)",),
    },
    "customer_pulse": {
        "ACCOUNT__C": ("Account ID", "Account Id"),
        "BU_NAME": ("Customer Name", "Customer_Name", "Account Name"),
        "CUSTOMER_PULSE__C": ("Customer Pulse",),
        "PULSE_RATING__C": ("Pulse Rating",),
        "SCORE__C": ("Pulse Score",),
        "PULSE_DATE_C": ("Pulse Date",),
        "COMMENTS__C": ("Comments",),
    },
    "tac_cases": {
        "Customer": ("Customer Name", "Customer_Name", "Account Name"),
        "ACCOUNT_ID_C": ("Account ID", "Account Id"),
        "SUBSCRIPTION_ID": ("Subscription ID", "Subscription Id"),
        "case_status_norm": ("Case Status (Normalized)",),
        "case_priority_norm": ("Priority (Normalized)",),
        "severity_norm": ("Severity (Normalized)",),
        "is_open": ("Is Open",),
        "is_closed": ("Is Closed",),
        "open_date": ("Open Date (Normalized)",),
        "closed_date": ("Closed Date (Normalized)",),
        "open_age_days": ("Open Age (Days)",),
        "closed_age_days": ("Closed Age (Days)",),
    },
    "success_priorities": {
        "ACCOUNT_ID_C": ("Account ID", "Account Id"),
        "RELATED_CUSTOMER__C": (
            "Related Customer",
            "Customer Name",
            "Customer_Name",
        ),
        "SUCCESS_PRIORITY_TITLE__C": ("Success Priority Title",),
        "STATUS_C": ("Status",),
        "CREATED_DATE_C": ("Created Date",),
    },
}

_PLACEHOLDER_STATES = {
    "data unavailable": "unavailable",
    "unavailable": "unavailable",
    "source unavailable": "unavailable",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "empty": "zero",
    "zero": "zero",
    "no data": "zero",
    "partial": "partial",
    "stale": "stale",
}

_STATE_ATTR_KEYS = (
    "source_unavailable",
    "source_unavailable_detail",
    "fetch_error",
    "fetch_error_kind",
    "fetch_error_partial",
    "partial",
    "stale",
    "is_stale",
    "source_mode_detail",
)


def _token(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _name_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _token(value).casefold())


def _report_family(report_type: Any) -> str:
    value = _token(report_type).casefold()
    if "compact" in value or "executive" in value:
        return "compact"
    if "renewal" in value:
        return "renewal"
    if "subscription" in value:
        return "subscription"
    raise CanonicalReportAdapterError("report_type must identify a Compact, Renewal, or Subscription artifact")


def _sheet_lookup(sheet_names: Iterable[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name in sheet_names:
        key = _name_token(name)
        if key in lookup and lookup[key] != name:
            raise CanonicalReportAdapterError(f"ambiguous legacy workbook sheet names: {lookup[key]!r} and {name!r}")
        lookup[key] = name
    return lookup


def _first_sheet(
    lookup: Mapping[str, str],
    aliases: Sequence[str],
) -> str | None:
    for alias in aliases:
        actual = lookup.get(_name_token(alias))
        if actual:
            return actual
    return None


def _read_report_info(excel: pd.ExcelFile, lookup: Mapping[str, str]) -> tuple[dict[str, Any], list[Any]]:
    name = _first_sheet(lookup, ("Report_Info", "Report Info"))
    if not name:
        return {}, []
    frame = pd.read_excel(excel, sheet_name=name, dtype=object)
    if frame.empty or not {"Item", "Value"}.issubset(frame.columns):
        return {}, []
    info: dict[str, Any] = {}
    warnings: list[Any] = []
    for _, row in frame.iterrows():
        item = _token(row.get("Item"))
        if not item:
            continue
        value = row.get("Value")
        info[item] = value
        # ``Partial_Data_Warning_Count`` is a ledger total, not a warning.
        # Treating the perfectly healthy value ``0`` as warning text degraded
        # every canonical source to ``partial`` and withheld otherwise valid
        # charts.  Only the legacy unnumbered warning row and explicitly
        # numbered warning rows carry warning payloads.
        warning_key = _name_token(item)
        if (warning_key == "partialdatawarning" or re.fullmatch(r"partialdatawarning\d+", warning_key)) and _token(
            value
        ):
            warnings.append(value)
    by_token = {_name_token(key): _token(value) for key, value in info.items()}
    live_validation = by_token.get("livevalidationperformed", "").casefold()
    data_mode = by_token.get("datamode", "")
    fixture_mode = any(
        marker in data_mode.casefold() for marker in ("fixture", "guarded local", "offline acceptance", "synthetic")
    )
    if fixture_mode or live_validation in {"no", "false", "0", "not performed"}:
        warnings.append(
            {
                "dataset": "Live source validation",
                "kind": "fixture" if fixture_mode else "deferred",
                "effect": (
                    "This report uses guarded, sanitized offline test data. "
                    "Snowflake and other live Cisco sources were not queried or validated."
                    if fixture_mode
                    else "Live source validation was not performed for this report run."
                ),
            }
        )
    return info, warnings


def _info_value(info: Mapping[str, Any], *aliases: str) -> Any:
    by_token = {_name_token(key): value for key, value in info.items()}
    for alias in aliases:
        value = by_token.get(_name_token(alias))
        if _token(value):
            return value
    return None


def _bounded_info_detail(value: Any) -> str:
    """Return a workbook-carried source detail safe for public propagation."""

    detail = re.sub(r"[\x00-\x1f\x7f]+", " ", _token(value))
    detail = re.sub(r"\s+", " ", detail).strip()
    # Source-detail cells are prose. Formula-like content is never a valid
    # coverage explanation and must not be carried into a regenerated XLSX.
    if not detail or detail.startswith(("=", "+", "-", "@")):
        return ""
    return detail[:500]


def _source_state_from_info(
    info: Mapping[str, Any],
    *,
    canonical_sheet: str,
    legacy_sheet: str,
) -> tuple[str | None, str]:
    canonical_token = _name_token(canonical_sheet)
    legacy_token = _name_token(legacy_sheet)
    declared_detail = _bounded_info_detail(
        _info_value(
            info,
            f"Source_Detail:{canonical_sheet}",
            f"Source_Detail:{legacy_sheet}",
            f"Data_Detail:{canonical_sheet}",
            f"Data_Detail:{legacy_sheet}",
        )
    )
    for item, raw_value in info.items():
        item_token = _name_token(item)
        if item_token in {
            f"sourcestate{canonical_token}",
            f"sourcestate{legacy_token}",
            f"datastate{canonical_token}",
            f"datastate{legacy_token}",
        }:
            state = _normalize_state(raw_value)
            return (
                state,
                declared_detail
                or f"Legacy Report_Info: {item}={_token(raw_value)}",
            )
    if canonical_sheet == "TAC_Cases":
        value = _info_value(info, "Support Case Source State")
        if value is not None:
            return (
                _normalize_state(value),
                _token(_info_value(info, "Support Case Coverage Note"))
                or f"Legacy Report_Info: Support Case Source State={_token(value)}",
            )
    return None, ""


def _normalize_state(value: Any) -> str | None:
    token = re.sub(r"[_-]+", " ", _token(value).casefold())
    token = re.sub(r"\s+", " ", token).strip()
    if not token:
        return None
    for marker, state in _PLACEHOLDER_STATES.items():
        if token == marker or token.startswith(f"{marker} "):
            return state
    if token in {"available", "healthy", "success"}:
        return "available"
    return None


def _row_placeholder_state(row: pd.Series) -> tuple[str | None, str]:
    values = {_name_token(column): _token(value) for column, value in row.items()}
    populated = [value for value in values.values() if value]
    if not populated:
        return "zero", "blank legacy placeholder row"

    envelope_keys = {
        "dataset",
        "message",
        "reason",
        "detail",
        "generatedat",
        "generatedatutc",
        "sourcestate",
        "recordcount",
        "coveragenote",
        "adoptiqstatus",
        "adoptiqmessage",
        "adoptiqprovenance",
    }
    has_envelope = bool(set(values) & envelope_keys)
    state_candidates = (
        values.get("sourcestate"),
        values.get("adoptiqstatus"),
        values.get("status"),
        values.get("riskcomponent"),
        values.get("component"),
    )
    for candidate in state_candidates:
        state = _normalize_state(candidate)
        if state and (has_envelope or state in {"failed", "unavailable"}):
            detail = (
                values.get("coveragenote")
                or values.get("detail")
                or values.get("message")
                or values.get("adoptiqmessage")
                or candidate
            )
            return state, detail

    combined = " | ".join(populated)
    combined_token = combined.casefold()
    message_markers = (
        "no data available",
        "no records in the analysis window",
        "no records were returned",
        "successful source returned zero",
        "data unavailable",
        "source was unavailable",
        "source unavailable",
        "fetch failed",
    )
    if any(marker in combined_token for marker in message_markers):
        state = "unavailable" if any(marker in combined_token for marker in ("unavailable", "fetch failed")) else "zero"
        return state, combined[:500]
    if has_envelope and any(value.casefold().endswith(" - empty") for value in populated):
        return "zero", combined[:500]
    return None, ""


def _state_priority(state: str) -> int:
    return {
        "failed": 5,
        "unavailable": 4,
        "partial": 3,
        "stale": 2,
        "zero": 1,
        "available": 0,
    }.get(state, 0)


def _apply_state_attrs(
    frame: pd.DataFrame,
    *,
    state: str | None,
    detail: str,
) -> pd.DataFrame:
    result = frame.copy()
    existing = dict(getattr(frame, "attrs", {}) or {})
    for key in _STATE_ATTR_KEYS:
        existing.pop(key, None)
    result.attrs.update(existing)
    if state in {None, "available", "zero"}:
        return result
    public_detail = _token(detail)[:500] or f"legacy source state: {state}"
    if state == "unavailable":
        if result.empty:
            result.attrs["source_unavailable"] = True
            result.attrs["source_unavailable_detail"] = public_detail
        else:
            result.attrs["partial"] = True
            result.attrs["source_mode_detail"] = (
                f"Rows were retained, but the legacy workbook also marked this source unavailable: {public_detail}"
            )
    elif state == "failed":
        result.attrs["fetch_error"] = public_detail
        if not result.empty:
            result.attrs["fetch_error_partial"] = True
    elif state == "partial":
        result.attrs["partial"] = True
        result.attrs["source_mode_detail"] = public_detail
    elif state == "stale":
        result.attrs["stale"] = True
        result.attrs["source_mode_detail"] = public_detail
    return result


def _strip_placeholder_rows(
    frame: pd.DataFrame,
    *,
    declared_state: str | None,
    declared_detail: str,
) -> pd.DataFrame:
    if frame.empty:
        return _apply_state_attrs(
            frame,
            state=declared_state or "zero",
            detail=declared_detail,
        )

    placeholder_states: list[tuple[str, str]] = []
    usable_positions: list[int] = []
    pending_metadata_positions: list[int] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        state, detail = _row_placeholder_state(row)
        if state:
            placeholder_states.append((state, detail))
            continue
        populated = [_token(value) for value in row.tolist() if _token(value)]
        if populated and all(value.casefold().startswith("generated:") for value in populated):
            pending_metadata_positions.append(position)
            continue
        usable_positions.append(position)

    if pending_metadata_positions and not placeholder_states:
        usable_positions.extend(pending_metadata_positions)
    usable_positions.sort()
    result = frame.iloc[usable_positions].copy().reset_index(drop=True)
    result.attrs.update(dict(getattr(frame, "attrs", {}) or {}))

    effective_state = declared_state
    details = [_token(declared_detail)] if _token(declared_detail) else []
    if placeholder_states:
        placeholder_state = max(placeholder_states, key=lambda item: _state_priority(item[0]))[0]
        if effective_state is None or _state_priority(placeholder_state) > _state_priority(effective_state):
            effective_state = placeholder_state
        details.extend(detail for _, detail in placeholder_states if _token(detail))
    # Round 166 / P0-A: legacy Compact workbooks can stamp Report_Info
    # Source_State=Zero while scoped subscription rows remain on the
    # sheet (risk-derived universe without a DSM roster row).  Reconcile
    # instead of aborting canonical delivery; keep raise for the inverse
    # contradiction (declared available with no substantive rows).
    if not result.empty and effective_state == "zero":
        effective_state = "partial"
        details.append("Round 166: reconciled zero Source_State with non-empty substantive rows")
    return _apply_state_attrs(
        result,
        state=effective_state or ("available" if not result.empty else "zero"),
        detail="; ".join(dict.fromkeys(details))[:500],
    )


def _substantive_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Return nonblank, non-placeholder legacy rows with source indexes intact."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=getattr(frame, "columns", ()))
    positions: list[int] = []
    for position, (_, row) in enumerate(frame.iterrows()):
        if not any(_token(value) for value in row.tolist()):
            continue
        state, _detail = _row_placeholder_state(row)
        if state:
            continue
        positions.append(position)
    return frame.iloc[positions].copy()


def _safe_family_fact_frame(frame: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    """Apply the public export boundary before retaining family-only facts."""

    from report_export_schema import apply_export_schema  # noqa: PLC0415

    substantive = _substantive_rows(frame)
    if substantive.empty:
        return substantive
    safe = apply_export_schema(substantive, sheet_name=sheet_name)
    if not isinstance(safe, pd.DataFrame):
        return pd.DataFrame()
    safe = safe.dropna(axis=1, how="all")
    blank_columns = [column for column in safe.columns if not any(_token(value) for value in safe[column])]
    return safe.drop(columns=blank_columns, errors="ignore")


def _supersede_compact_display_risk_scores(
    frame: pd.DataFrame,
    *,
    sheet_name: Any,
    disclose: bool = False,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Remove obsolete Compact 0-10 display scores from canonical evidence.

    ``Risk_Summary.Overall_Risk_Score`` and ``Risk_Score_0_10`` are the same
    rounded 0-10 display value produced by the legacy Compact scorer.  The
    canonical decision contract independently recalculates the authoritative
    0-100 value in ``Account_Summary``.  Treating either display alias as an
    additional exact canonical fact creates false contradictions (for example
    2.4/10 versus a recalculated 22.3/100) and, worse, can publish two different
    risk values as if both were authoritative.

    Only those two exact Compact aliases are superseded.  An explicit
    ``Risk_Score_0_100`` remains subject to strict contradiction detection, as
    do all Renewal/Subscription scores and the Compact risk band/level.
    """

    if _name_token(sheet_name) != _name_token("Risk_Summary"):
        return frame, ()
    dropped = tuple(
        str(column) for column in frame.columns if _name_token(column) in _COMPACT_DISPLAY_RISK_SCORE_FIELD_TOKENS
    )
    if not dropped:
        return frame, ()
    for position, (source_index, row) in enumerate(frame.iterrows()):
        try:
            source_row_number = int(source_index) + 2
        except (TypeError, ValueError):
            source_row_number = position + 2
        normalized_scores = [
            (
                column,
                row.get(column),
                _risk_score(
                    row.get(column),
                    column,
                    sheet_name=sheet_name,
                ),
            )
            for column in dropped
            if _token(row.get(column))
        ]
        numeric_scores = [item for item in normalized_scores if item[2] is not None]
        if len(numeric_scores) > 1:
            first_column, first_raw, first_score = numeric_scores[0]
            for other_column, other_raw, other_score in numeric_scores[1:]:
                if _risk_values_conflict(
                    first_score,
                    other_score,
                    kind="score",
                ):
                    raise CanonicalReportAdapterError(
                        "unresolved Compact display-risk contradiction: "
                        f"{sheet_name}!row {source_row_number} "
                        f"{first_column}={first_raw} conflicts with "
                        f"{other_column}={other_raw}"
                    )

        if not numeric_scores:
            continue
        from risk_scoring import _risk_band as score_risk_band  # noqa: PLC0415

        score_band = score_risk_band(float(numeric_scores[0][2]))
        for column, raw_band in row.items():
            if _name_token(column) not in _RISK_BAND_FIELD_TOKENS:
                continue
            reported_band = _risk_band(raw_band)
            if reported_band is None:
                continue
            if _risk_values_conflict(
                score_band,
                reported_band,
                kind="band",
            ):
                raise CanonicalReportAdapterError(
                    "unresolved Compact display-risk contradiction: "
                    f"{sheet_name}!row {source_row_number} "
                    f"{numeric_scores[0][0]}={numeric_scores[0][1]} implies "
                    f"{score_band}, but {column}={raw_band}"
                )
    attrs = dict(getattr(frame, "attrs", {}) or {})
    result = frame.drop(columns=list(dropped), errors="ignore").copy()
    if disclose:
        result["Legacy_Risk_Score_Disposition"] = _COMPACT_RISK_SCORE_DISPOSITION
    result.attrs.update(attrs)
    result.attrs["superseded_legacy_risk_fields"] = list(dropped)
    return result, dropped


def _ambiguous_family_customer_labels(
    family_frames: Mapping[str, pd.DataFrame],
) -> dict[str, tuple[str, ...]]:
    """Find distinct source labels that collapse to one comparison key.

    A repeated byte-equivalent label is not an ambiguity declaration and must
    continue through strict contradiction validation.  Case/punctuation
    variants such as ``ACME CORPORATION`` and ``Acme Corporation`` are unsafe
    to merge when the legacy family sheet lacks a stable account ID, so the
    collision itself becomes a declared partial-data condition.
    """

    labels_by_key: dict[str, set[str]] = {}
    for sheet_name, frame in family_frames.items():
        if _name_token(sheet_name) != _name_token("Risk_Summary"):
            continue
        for _, row in frame.iterrows():
            label = _record_value(row.to_dict(), _CUSTOMER_FIELD_TOKENS)
            label_text = _token(label)
            label_key = _name_token(label_text)
            if label_text and label_key:
                labels_by_key.setdefault(label_key, set()).add(label_text)
    return {
        key: tuple(sorted(labels, key=lambda value: (value.casefold(), value)))
        for key, labels in sorted(labels_by_key.items())
        if len(labels) > 1
    }


def _ambiguous_subscription_customer_labels(
    frame: pd.DataFrame,
) -> dict[str, tuple[str, ...]]:
    """Find display labels that cannot resolve to one stable account ID.

    Compact's legacy ``Risk_Summary`` can omit account IDs even though the
    scoped subscription rows contain them. A normalized name attached to two
    IDs, or one ID attached to conflicting normalized names, makes a
    name-only risk claim unsafe. The canonical account-level calculation
    remains usable; only the ambiguous legacy claim is quarantined.
    """

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    labels_by_key: dict[str, set[str]] = {}
    account_ids_by_key: dict[str, set[str]] = {}
    keys_by_account_id: dict[str, set[str]] = {}
    for _, row in frame.iterrows():
        record = row.to_dict()
        label_text = _token(_record_value(record, _CUSTOMER_FIELD_TOKENS))
        label_key = _name_token(label_text)
        account_id = _token(_record_value(record, _ACCOUNT_ID_FIELD_TOKENS)).casefold()
        if not label_text or not label_key or not account_id:
            continue
        labels_by_key.setdefault(label_key, set()).add(label_text)
        account_ids_by_key.setdefault(label_key, set()).add(account_id)
        keys_by_account_id.setdefault(account_id, set()).add(label_key)

    ambiguous_keys = {key for key, account_ids in account_ids_by_key.items() if len(account_ids) > 1}
    for name_keys in keys_by_account_id.values():
        if len(name_keys) > 1:
            ambiguous_keys.update(name_keys)
    return {
        key: tuple(sorted(labels_by_key.get(key, ()), key=lambda value: (value.casefold(), value)))
        for key in sorted(ambiguous_keys)
    }


def _quarantine_ambiguous_family_risk_claims(
    frame: pd.DataFrame,
    *,
    sheet_name: Any,
    ambiguous_customer_keys: set[str],
    disclose: bool = False,
) -> tuple[pd.DataFrame, tuple[str, ...], int]:
    """Blank only unresolvable family-risk cells for declared ambiguities."""

    if _name_token(sheet_name) != _name_token("Risk_Summary") or not ambiguous_customer_keys or frame.empty:
        return frame, (), 0
    customer_columns = [column for column in frame.columns if _name_token(column) in _CUSTOMER_FIELD_TOKENS]
    risk_columns = [
        column
        for column in frame.columns
        if _name_token(column) in (_RISK_SCORE_FIELD_TOKENS | _RISK_BAND_FIELD_TOKENS)
    ]
    if not customer_columns or not risk_columns:
        return frame, (), 0
    mask = pd.Series(False, index=frame.index)
    for customer_column in customer_columns:
        mask |= frame[customer_column].map(_name_token).isin(ambiguous_customer_keys)
    if not bool(mask.any()):
        return frame, (), 0
    attrs = dict(getattr(frame, "attrs", {}) or {})
    result = frame.copy()
    result.loc[mask, risk_columns] = pd.NA
    if disclose:
        if "Legacy_Risk_Band_Disposition" not in result.columns:
            result["Legacy_Risk_Band_Disposition"] = pd.NA
        result.loc[mask, "Legacy_Risk_Band_Disposition"] = _AMBIGUOUS_RISK_DISPOSITION
    result.attrs.update(attrs)
    result.attrs["ambiguous_family_risk_quarantined"] = True
    return result, tuple(str(column) for column in risk_columns), int(mask.sum())


def _record_value(record: Mapping[str, Any], tokens: set[str]) -> Any:
    for column, value in record.items():
        if _name_token(column) in tokens and _token(value):
            return value
    return None


def _metric_slug(value: Any, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _token(value).casefold()).strip("_")
    return (slug or fallback)[:64]


def _reported_unit(field: Any, value: Any) -> str:
    field_token = _name_token(field)
    if "date" in field_token:
        return "reported date"
    if "percent" in field_token or field_token.endswith("pct"):
        return "reported percent"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "reported value"
    return "reported number" if pd.notna(number) else "reported value"


def _family_fact_projection(
    family_frames: Mapping[str, pd.DataFrame],
    *,
    family: str,
    info: Mapping[str, Any],
    scope_type: str,
    scope_value: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Normalize family-only workbook cells into traceable reported facts."""

    fact_rows: list[dict[str, Any]] = []
    lineage_rows: list[dict[str, Any]] = []
    mapped_counts: dict[str, int] = {}
    for sheet_name in sorted(family_frames, key=lambda value: value.casefold()):
        frame = family_frames[sheet_name]
        sheet_slug = _metric_slug(sheet_name, fallback="sheet")
        sheet_count = 0
        for position, (source_index, source_row) in enumerate(frame.iterrows()):
            record = source_row.to_dict()
            try:
                source_row_number = int(source_index) + 2
            except (TypeError, ValueError):
                source_row_number = position + 2

            customer = _record_value(record, _CUSTOMER_FIELD_TOKENS)
            if not _token(customer):
                customer = _info_value(info, "Customer Name", "Customer_Name")
            if not _token(customer) and scope_type.casefold() == "customer":
                customer = scope_value
            account_id = _record_value(record, _ACCOUNT_ID_FIELD_TOKENS)
            if not _token(account_id):
                account_id = _info_value(info, "Selected Account ID", "Account ID")
            subscription_id = _record_value(record, _SUBSCRIPTION_ID_FIELD_TOKENS)
            if not _token(subscription_id):
                subscription_id = _info_value(
                    info,
                    "Subscription ID",
                    "Selected Subscription ID",
                )
            cssm = _record_value(record, _CSSM_FIELD_TOKENS)

            by_token = {_name_token(column): column for column in record}
            metric_column = next(
                (by_token[token] for token in ("metric", "metricname", "keymetric") if token in by_token),
                None,
            )
            value_column = by_token.get("value")
            handled_columns: set[Any] = set()
            fact_cells: list[tuple[Any, str, Any]] = []
            if (
                metric_column is not None
                and value_column is not None
                and _token(record.get(metric_column))
                and _token(record.get(value_column))
            ):
                fact_cells.append(
                    (
                        value_column,
                        _token(record.get(metric_column)),
                        record.get(value_column),
                    )
                )
                handled_columns.update({metric_column, value_column})

            for column, value in record.items():
                if column in handled_columns or not _token(value):
                    continue
                if _name_token(column) in _FAMILY_CONTEXT_FIELD_TOKENS:
                    continue
                fact_cells.append((column, _token(column), value))

            if not fact_cells:
                # A context-only row is still accounted for without inventing
                # a decision metric; its source-owned identity remains public.
                fact_cells.append(
                    (
                        "Legacy_Row_Context",
                        "Legacy source row context",
                        "See preserved identity columns",
                    )
                )

            for ordinal, (source_field, display_label, value) in enumerate(
                fact_cells,
                1,
            ):
                field_slug = _metric_slug(source_field, fallback=f"field_{ordinal}")
                uniqueness = hashlib.sha256(
                    (f"{family}|{sheet_name}|{source_row_number}|{source_field}|{ordinal}").encode("utf-8")
                ).hexdigest()[:8]
                metric_key = f"legacy.family.{family}.{sheet_slug}.r{source_row_number}.{field_slug}.{uniqueness}"
                record_id = f"LEGACY-{family.upper()}-{uniqueness.upper()}"
                fact_row: dict[str, Any] = {
                    "Record_ID": record_id,
                    "Record_ID_Data_Quality": ("Adapter-generated stable key from source sheet, row, and field"),
                    "Metric_Key": metric_key,
                    "Scope_Type": scope_type,
                    "Scope_Value": scope_value,
                    "Source_System": f"Legacy workbook → {sheet_name}",
                    "Legacy_Record_Type": "Family-specific reported fact",
                    "Legacy_Report_Family": family,
                    "Legacy_Source_Sheet": sheet_name,
                    "Legacy_Source_Row_Number": source_row_number,
                    "Legacy_Source_Field": _token(source_field),
                    "Legacy_Fact_Label": display_label,
                    "Legacy_Fact_Value": value,
                }
                if _token(customer):
                    fact_row["BU_NAME"] = customer
                if _token(account_id):
                    fact_row["ACCOUNT_ID_C"] = account_id
                if _token(subscription_id):
                    fact_row["SUBSCRIPTION_ID"] = subscription_id
                if _token(cssm):
                    fact_row["CSSM"] = cssm
                fact_rows.append(fact_row)
                lineage_rows.append(
                    {
                        "Metric_Key": metric_key,
                        "Display_Label": f"{sheet_name} — {display_label}",
                        "Metric_Value": value,
                        "Unit": _reported_unit(source_field, value),
                        "Scope_Type": scope_type,
                        "Scope_Value": scope_value,
                        "Canonical_Function": ("canonical_report_adapter._family_fact_projection"),
                        "Source_Sheet": "Subscriptions",
                        "Source_Fields": (
                            "Legacy_Fact_Value; Legacy_Source_Sheet; Legacy_Source_Row_Number; Legacy_Source_Field"
                        ),
                        "Filters": (f"legacy workbook sheet={sheet_name}; source row={source_row_number}"),
                        "Grouping": "legacy family-specific reported fact",
                        "Deduplication": ("exact source sheet + row number + field name"),
                        "Empty_State": "blank cells are not emitted as facts",
                        "Source_State": "available",
                        "Caveat": (
                            "Reported by the legacy artifact and preserved for audit; "
                            "it is canonical only where explicitly reconciled."
                        ),
                    }
                )
                sheet_count += 1
        mapped_counts[sheet_name] = sheet_count
    return (
        pd.DataFrame(fact_rows),
        pd.DataFrame(lineage_rows),
        mapped_counts,
    )


def _implicit_subscription_risk_scale(field: Any, sheet_name: Any) -> bool:
    return _name_token(field) == "renewalriskscore" and _name_token(sheet_name) in {"summary", "subscriptionsummary"}


def _implicit_compact_risk_scale(field: Any, sheet_name: Any) -> bool:
    """Return whether an ambiguously named Compact score is on 0-10."""

    return (
        _name_token(sheet_name) == _name_token("Risk_Summary")
        and _name_token(field) in _COMPACT_DISPLAY_RISK_SCORE_FIELD_TOKENS
    )


def _renewal_overall_risk_uses_ten_point_scale(
    value: Any,
    field: Any,
    sheet_name: Any,
    record: Mapping[Any, Any] | None,
) -> bool:
    """Resolve the current Renewal display alias without magnitude guessing."""

    if (
        _name_token(sheet_name) != _name_token("Renewal_Summary")
        or _name_token(field) != "overallriskscore"
        or not record
    ):
        return False
    by_token = {_name_token(key): candidate for key, candidate in record.items()}
    value_text = _token(value).replace(",", "").rstrip("%")
    if not value_text:
        return False
    try:
        numeric_value = float(value_text)
    except (TypeError, ValueError):
        return False
    if not pd.notna(numeric_value):
        return False

    # Round 148: presence alone is not evidence of scale. A partial workbook
    # may carry an explicit sibling with ``Unavailable`` or NaN, while a
    # historical Overall_Risk_Score still uses 0-100. Require a finite sibling
    # that numerically agrees with the display alias.
    explicit_ten_text = _token(by_token.get("riskscore010")).replace(",", "").rstrip("%")
    if explicit_ten_text:
        try:
            numeric_ten = float(explicit_ten_text)
        except (TypeError, ValueError):
            numeric_ten = float("nan")
        if pd.notna(numeric_ten) and abs(numeric_value - numeric_ten) <= 0.11:
            return True

    explicit_hundred = by_token.get("riskscore0100")
    hundred_text = _token(explicit_hundred).replace(",", "").rstrip("%")
    if not hundred_text:
        return False
    try:
        numeric_hundred = float(hundred_text)
    except (TypeError, ValueError):
        return False
    if not pd.notna(numeric_hundred):
        return False
    return abs((numeric_value * 10.0) - numeric_hundred) < abs(numeric_value - numeric_hundred)


def _risk_source_uses_ten_point_scale(
    value: Any,
    field: Any,
    *,
    sheet_name: Any,
    record: Mapping[Any, Any] | None,
) -> bool:
    field_token = _name_token(field)
    return bool(
        (field_token.endswith("010") and not field_token.endswith("0100"))
        or _implicit_subscription_risk_scale(field, sheet_name)
        or _implicit_compact_risk_scale(field, sheet_name)
        or _renewal_overall_risk_uses_ten_point_scale(
            value,
            field,
            sheet_name,
            record,
        )
    )


def _risk_score(
    value: Any,
    field: Any,
    *,
    sheet_name: Any = "",
    record: Mapping[Any, Any] | None = None,
) -> float | None:
    value_text = _token(value).replace(",", "").rstrip("%")
    if not value_text:
        return None
    try:
        score = float(value_text)
    except (TypeError, ValueError):
        return None
    if not pd.notna(score):
        return None
    # Round 148: current Renewal workbooks publish Overall_Risk_Score and
    # Risk_Score_0_10 on the display scale beside Risk_Score_0_100. Resolve
    # that alias from explicit sibling fields while preserving the historical
    # Renewal workbook shape where a lone Overall_Risk_Score was 0-100.
    if _risk_source_uses_ten_point_scale(
        value,
        field,
        sheet_name=sheet_name,
        record=record,
    ):
        score *= 10.0
    return round(score, 6)


def _risk_band(value: Any) -> str | None:
    band_text = _token(value).casefold()
    if not band_text or band_text in {
        "n/a",
        "na",
        "not available",
        "unavailable",
        "unknown",
    }:
        return None
    if band_text.startswith("crit") or band_text in {"red", "p1"}:
        return "CRITICAL"
    if band_text.startswith("high") or band_text == "p2":
        return "HIGH"
    if band_text.startswith("med") or band_text in {"amber", "yellow", "moderate"}:
        return "MEDIUM"
    if band_text.startswith("low"):
        return "LOW"
    if band_text in {"healthy", "green", "good"}:
        return "HEALTHY"
    return band_text.upper()


def _family_risk_claims(
    family_frames: Mapping[str, pd.DataFrame],
    *,
    info: Mapping[str, Any],
    scope_type: str,
    scope_value: str,
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for sheet_name, frame in family_frames.items():
        for position, (source_index, source_row) in enumerate(frame.iterrows()):
            record = source_row.to_dict()
            try:
                source_row_number = int(source_index) + 2
            except (TypeError, ValueError):
                source_row_number = position + 2
            customer = _record_value(record, _CUSTOMER_FIELD_TOKENS)
            if not _token(customer):
                customer = _info_value(info, "Customer Name", "Customer_Name")
            if not _token(customer) and scope_type.casefold() == "customer":
                customer = scope_value

            by_token = {_name_token(column): column for column in record}
            metric_column = next(
                (by_token[token] for token in ("metric", "metricname", "keymetric") if token in by_token),
                None,
            )
            value_column = by_token.get("value")
            candidates: list[tuple[Any, Any]] = list(record.items())
            if metric_column is not None and value_column is not None:
                metric_label = record.get(metric_column)
                candidates.append((metric_label, record.get(value_column)))

            seen: set[tuple[str, str]] = set()
            for field, raw_value in candidates:
                field_token = _name_token(field)
                if field_token in _RISK_SCORE_FIELD_TOKENS:
                    value = _risk_score(
                        raw_value,
                        field,
                        sheet_name=sheet_name,
                        record=record,
                    )
                    kind = "score"
                elif field_token in _RISK_BAND_FIELD_TOKENS:
                    value = _risk_band(raw_value)
                    kind = "band"
                else:
                    continue
                if value is None:
                    continue
                dedupe_key = (kind, _token(value))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                claims.append(
                    {
                        "customer": _token(customer),
                        "customer_key": _name_token(customer),
                        "kind": kind,
                        "value": value,
                        "raw_value": raw_value,
                        "sheet": sheet_name,
                        "row": source_row_number,
                        "field": _token(field),
                        "score_tolerance": (
                            0.51
                            if kind == "score"
                            and _risk_source_uses_ten_point_scale(
                                raw_value,
                                field,
                                sheet_name=sheet_name,
                                record=record,
                            )
                            else 0.11
                        ),
                    }
                )
    return claims


def _risk_values_conflict(
    left: Any,
    right: Any,
    *,
    kind: str,
    score_tolerance: float = 0.11,
) -> bool:
    if kind == "score":
        return abs(float(left) - float(right)) > float(score_tolerance)
    return _token(left).upper() != _token(right).upper()


def _validate_family_risk_claims(
    family_frames: Mapping[str, pd.DataFrame],
    facts: Mapping[str, Any],
    *,
    info: Mapping[str, Any],
    scope_type: str,
    scope_value: str,
) -> list[dict[str, Any]]:
    """Fail closed when reported family risk contradicts another exact fact."""

    claims = _family_risk_claims(
        family_frames,
        info=info,
        scope_type=scope_type,
        scope_value=scope_value,
    )
    canonical_by_customer: dict[str, dict[str, Any]] = {}
    for row in facts.get("account_summary_all") or ():
        if len(row) < 3:
            continue
        canonical_by_customer[_name_token(row[0])] = {
            "customer": _token(row[0]),
            "band": _risk_band(row[1]),
            "score": _risk_score(row[2], "Risk_Score_0_100"),
        }

    errors: list[str] = []
    claims_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for claim in claims:
        customer_key = claim["customer_key"]
        if not customer_key and len(canonical_by_customer) == 1:
            customer_key = next(iter(canonical_by_customer))
        claims_by_identity.setdefault((customer_key, claim["kind"]), []).append(claim)

    for (customer_key, kind), grouped_claims in claims_by_identity.items():
        first = grouped_claims[0]
        for other in grouped_claims[1:]:
            if _risk_values_conflict(
                first["value"],
                other["value"],
                kind=kind,
                score_tolerance=max(
                    float(first.get("score_tolerance") or 0.11),
                    float(other.get("score_tolerance") or 0.11),
                ),
            ):
                customer_label = (
                    first["customer"] or canonical_by_customer.get(customer_key, {}).get("customer") or scope_value
                )
                errors.append(
                    f"{customer_label}: {first['sheet']}!row {first['row']} "
                    f"{first['field']}={first['raw_value']} conflicts with "
                    f"{other['sheet']}!row {other['row']} "
                    f"{other['field']}={other['raw_value']}"
                )

        canonical = canonical_by_customer.get(customer_key)
        if canonical is None:
            continue
        canonical_value = canonical.get(kind)
        if canonical_value is None:
            continue
        for claim in grouped_claims:
            if _risk_values_conflict(
                claim["value"],
                canonical_value,
                kind=kind,
                score_tolerance=float(claim.get("score_tolerance") or 0.11),
            ):
                canonical_field = "Risk_Score_0_100" if kind == "score" else "Risk_Band"
                errors.append(
                    f"{canonical['customer']}: {claim['sheet']}!row {claim['row']} "
                    f"{claim['field']}={claim['raw_value']} conflicts with canonical "
                    f"Account_Summary {canonical_field}={canonical_value}"
                )

    if errors:
        raise CanonicalReportAdapterError(
            "unresolved family-specific risk contradiction: " + "; ".join(list(dict.fromkeys(errors))[:8])
        )
    return claims


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.isna() | series.map(lambda value: isinstance(value, str) and not value.strip())


def _comparison_value(value: Any) -> Any:
    if not _token(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return _token(value)


def _values_equivalent(left: Any, right: Any, *, target: str) -> bool:
    left_value = _comparison_value(left)
    right_value = _comparison_value(right)
    if left_value == right_value:
        return True
    if "date" in target.casefold():
        left_date = pd.to_datetime(left, errors="coerce", utc=True)
        right_date = pd.to_datetime(right, errors="coerce", utc=True)
        if pd.notna(left_date) and pd.notna(right_date):
            return bool(left_date == right_date)
    try:
        return float(left_value) == float(right_value)
    except (TypeError, ValueError):
        return False


def _coalesce_public_headers(
    frame: pd.DataFrame,
    header_map: Mapping[str, Sequence[str]],
) -> pd.DataFrame:
    result = frame.copy()
    attrs = dict(getattr(frame, "attrs", {}) or {})
    for target, aliases in header_map.items():
        candidate_tokens = {_name_token(target), *(_name_token(alias) for alias in aliases)}
        matching = [column for column in result.columns if _name_token(column) in candidate_tokens]
        if not matching:
            continue
        target_column = next((column for column in matching if str(column) == target), matching[0])
        if target_column != target:
            result = result.rename(columns={target_column: target})
        for alias_column in list(matching):
            if alias_column == target_column:
                continue
            if alias_column not in result.columns:
                continue
            target_blank = _blank_mask(result[target])
            alias_blank = _blank_mask(result[alias_column])
            conflict = (
                ~target_blank
                & ~alias_blank
                & pd.Series(
                    [
                        not _values_equivalent(left, right, target=target)
                        for left, right in zip(result[target], result[alias_column])
                    ],
                    index=result.index,
                )
            )
            if bool(conflict.any()):
                raise CanonicalReportAdapterError(
                    f"legacy workbook has conflicting values for equivalent headers {target!r} and {alias_column!r}"
                )
            result.loc[target_blank & ~alias_blank, target] = result.loc[target_blank & ~alias_blank, alias_column]
            result = result.drop(columns=[alias_column])
    result.attrs.update(attrs)
    return result


def _normalize_public_headers(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    mappings: "OrderedDict[str, tuple[str, ...]]" = OrderedDict()
    mappings.update(_PUBLIC_CONTEXT_HEADERS)
    mappings.update(_PUBLIC_HEADERS_BY_KEY.get(key, {}))
    return _coalesce_public_headers(frame, mappings)


def _series_matches(series: pd.Series, predicate: Any) -> bool:
    values = [_token(value) for value in series if _token(value)]
    return bool(values) and sum(bool(predicate(value)) for value in values) / len(values) >= 0.8


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, pd.Timestamp):
        return True
    return bool(re.match(r"^\d{4}-\d{1,2}-\d{1,2}(?:[ T].*)?$", _token(value)))


def _looks_like_priority(value: Any) -> bool:
    return bool(
        re.match(
            r"^(?:p[0-4]|critical|high|medium|low|urgent)$",
            _token(value).casefold(),
        )
    )


def _looks_like_customer(value: Any) -> bool:
    token = _token(value)
    return bool(
        token
        and not _looks_like_date(token)
        and not _looks_like_priority(token)
        and re.search(r"[A-Za-z]", token)
        and (" " in token or re.search(r"(?:corp|inc|llc|ltd|company|sector)$", token, re.I))
    )


def _looks_like_account_id(value: Any) -> bool:
    token = _token(value)
    return bool(token and " " not in token and re.search(r"\d", token) and re.match(r"^[A-Za-z0-9_.:-]+$", token))


def _repair_known_subscription_header_shift(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Repair the pre-R146 Subscription writer's positional-header defect.

    That writer wrote values from a curated frame but restamped headers from
    the raw frame.  Repairs below are permutations only: no cell value is
    invented or discarded, and each pattern requires strong type/shape
    evidence before it is applied.
    """

    result = frame.copy()
    result.attrs.update(dict(getattr(frame, "attrs", {}) or {}))
    repairs = list(result.attrs.get("legacy_header_repairs") or [])

    account_column = "ACCOUNT__C" if key == "customer_pulse" else "ACCOUNT_ID_C"
    if {account_column, "BU_NAME"}.issubset(result.columns):
        if _series_matches(result[account_column], _looks_like_customer) and _series_matches(
            result["BU_NAME"], _looks_like_account_id
        ):
            account_values = result[account_column].copy()
            result[account_column] = result["BU_NAME"]
            result["BU_NAME"] = account_values
            repairs.append(f"{key}:customer-account-position")

    if key == "action_plans" and {"DUE_DATE_C", "PRIORITY_C"}.issubset(result.columns):
        if _series_matches(result["DUE_DATE_C"], _looks_like_priority) and _series_matches(
            result["PRIORITY_C"], _looks_like_date
        ):
            due_values = result["DUE_DATE_C"].copy()
            result["DUE_DATE_C"] = result["PRIORITY_C"]
            result["PRIORITY_C"] = due_values
            repairs.append("action_plans:priority-due-date-position")

    if key == "adoption_barriers" and {
        "SEVERITY_C",
        "OPEN_DATE_C",
        "DESCRIPTION_C",
    }.issubset(result.columns):
        description_values = result["SEVERITY_C"]
        priority_values = result["OPEN_DATE_C"]
        date_values = result["DESCRIPTION_C"]
        if (
            _series_matches(priority_values, _looks_like_priority)
            and _series_matches(date_values, _looks_like_date)
            and not _series_matches(description_values, _looks_like_priority)
            and not _series_matches(description_values, _looks_like_date)
        ):
            result["DESCRIPTION_C"] = description_values.copy()
            result["SEVERITY_C"] = priority_values.copy()
            result["OPEN_DATE_C"] = date_values.copy()
            repairs.append("adoption_barriers:description-severity-open-date-position")

    if repairs:
        result.attrs["legacy_header_repairs"] = list(dict.fromkeys(repairs))
    return result


def _subscription_summary_frame(
    frame: pd.DataFrame,
    *,
    info: Mapping[str, Any],
) -> pd.DataFrame:
    if not {"Metric", "Value"}.issubset(frame.columns):
        return frame
    record: OrderedDict[str, Any] = OrderedDict()
    for _, row in frame.iterrows():
        metric = _token(row.get("Metric"))
        if metric:
            record[metric] = row.get("Value")
    for label, aliases in (
        ("Subscription ID", ("Subscription ID", "Subscription_Id")),
        ("Account ID", ("Selected Account ID", "Account ID")),
        ("Customer Name", ("Customer Name", "Customer_Name")),
        ("Technology", ("Technology",)),
    ):
        if not _token(record.get(label)):
            value = _info_value(info, *aliases)
            if value is not None:
                record[label] = value
    result = pd.DataFrame([record]) if record else frame.iloc[0:0].copy()
    result.attrs.update(dict(getattr(frame, "attrs", {}) or {}))
    return result


def _missing_source_frame(canonical_sheet: str) -> pd.DataFrame:
    frame = pd.DataFrame()
    frame.attrs["source_unavailable"] = True
    frame.attrs["source_unavailable_detail"] = f"No compatible legacy workbook sheet was present for {canonical_sheet}"
    return frame


def _load_source_frame(
    excel: pd.ExcelFile,
    *,
    lookup: Mapping[str, str],
    aliases: Sequence[str],
    key: str,
    canonical_sheet: str,
    info: Mapping[str, Any],
    subscription_summary: bool = False,
    repair_subscription_shift: bool = False,
    source_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str | None]:
    sheet_name = _first_sheet(lookup, aliases)
    if source_frame is not None:
        source_attrs = dict(getattr(source_frame, "attrs", {}) or {})
        frame = _normalize_public_headers(source_frame.copy(), key)
        if "Source_System" in frame.columns:
            observation_routes = {
                _token(value)
                for value in source_attrs.get("source_observation_routes") or []
                if _token(value)
            }
            observation_routes.update(
                _token(value)
                for value in frame["Source_System"].tolist()
                if _token(value)
            )
            source_attrs["source_observation_routes"] = sorted(
                observation_routes,
                key=lambda value: (value.casefold(), value),
            )
        frame["Source_System"] = _CANONICAL_SOURCE_SYSTEM_BY_KEY[key]
        frame.attrs.update(source_attrs)
        return frame, sheet_name
    if not sheet_name:
        return _missing_source_frame(canonical_sheet), None
    frame = pd.read_excel(excel, sheet_name=sheet_name, dtype=object)
    declared_state, declared_detail = _source_state_from_info(
        info,
        canonical_sheet=canonical_sheet,
        legacy_sheet=sheet_name,
    )
    frame = _strip_placeholder_rows(
        frame,
        declared_state=declared_state,
        declared_detail=declared_detail,
    )
    if repair_subscription_shift:
        frame = _repair_known_subscription_header_shift(frame, key)
    if subscription_summary:
        frame = _subscription_summary_frame(frame, info=info)
    frame = _normalize_public_headers(frame, key)
    frame["Source_System"] = _CANONICAL_SOURCE_SYSTEM_BY_KEY[key]
    return frame, sheet_name


def _normalize_warnings(
    explicit: Sequence[Any] | None,
    workbook_warnings: Sequence[Any],
) -> list[dict[str, Any]]:
    def structured_warning(value: Any) -> dict[str, Any] | None:
        if isinstance(value, Mapping):
            return dict(value)
        text = _token(value)
        if not text:
            return None
        # Compact historically serialized warnings as
        # ``dataset (kind): detail`` while Renewal used
        # ``dataset | kind | detail``. Recover that structure instead of
        # labeling the string ``legacy_workbook`` and degrading every
        # canonical source to partial/failed.
        pipe_parts = [part.strip() for part in text.split("|", 2)]
        if len(pipe_parts) == 3 and pipe_parts[0] and pipe_parts[1]:
            return {
                "dataset": pipe_parts[0],
                "kind": pipe_parts[1],
                "effect": pipe_parts[2],
            }
        parenthetical = re.match(
            r"^\s*([^():|]+?)\s*\(([^()]+)\)\s*:\s*(.+?)\s*$",
            text,
        )
        if parenthetical:
            return {
                "dataset": parenthetical.group(1).strip(),
                "kind": parenthetical.group(2).strip(),
                "effect": parenthetical.group(3).strip(),
            }
        return {
            "dataset": "legacy_workbook",
            "kind": "legacy_partial_data_warning",
            "effect": text[:500],
        }

    # These warnings describe one source-level scope decision, not a sequence
    # of independent events. Compact supplies the structured warning directly
    # and also serializes it into its legacy workbook; details can differ
    # slightly (``effect`` versus ``error``) even though both represent the
    # same decision. Keep the richer direct warning once instead of showing a
    # manager the same limitation twice.
    source_scope_singletons = {
        "autodiscovered_empty_after_scope",
        "tech_filter_empty_after_scope",
        "tech_filter_scope_excluded",
        "technology_scope_partial",
        "technology_scope_unavailable",
    }

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for warning in [*(explicit or ()), *workbook_warnings]:
        parsed = structured_warning(warning)
        if parsed is None:
            continue
        detail = next(
            (
                _token(parsed.get(field))
                for field in ("effect", "error", "message", "reason")
                if _token(parsed.get(field))
            ),
            "",
        )
        dataset_key = _name_token(parsed.get("dataset"))
        kind_key = _name_token(parsed.get("kind"))
        normalized_kind = re.sub(r"[^a-z0-9]+", "_", _token(parsed.get("kind")).casefold()).strip("_")
        if normalized_kind in source_scope_singletons:
            semantic_key = ("source_scope", dataset_key, kind_key)
        else:
            semantic_key = (
                "detail",
                dataset_key,
                kind_key,
                re.sub(r"\s+", " ", detail).strip().casefold(),
            )
        if semantic_key in seen:
            continue
        seen.add(semantic_key)
        normalized.append(parsed)
    return normalized


_WARNING_DATASET_EXTRAS: Mapping[str, tuple[str, ...]] = {
    "subscriptions": ("team_subs", "team_subscriptions", "risk_summary", "renewal_summary"),
    "action_plans": ("csconsole_action_plans", "customer_action_plans"),
    "adoption_barriers": (
        "csconsole_adoption_barriers",
        "customer_adoption_barriers",
    ),
    "customer_pulse": ("csconsole_customer_pulse", "customer_customer_pulse"),
    "tac_cases": ("csone", "csone_tac_cases", "support_cases"),
    "success_priorities": (
        "csconsole_success_priorities",
        "customer_success_priorities",
    ),
    "external_incidents": ("external_intelligence_incidents",),
    "external_bugs": ("external_defects", "external_intelligence_bugs"),
}


def _warning_source_state(warning: Mapping[str, Any]) -> str | None:
    """Translate a partial-data warning into a canonical source state."""

    kind = _name_token(warning.get("kind"))
    if kind in {"sourceunavailable", "technologyscopeunavailable"}:
        return "unavailable"
    if kind in {
        "fixture",
        "deferred",
        "legacysheetquarantined",
        "techfilteremptyafterscope",
        "autodiscoveredemptyafterscope",
        "techfilterscopeexcluded",
    }:
        return None
    combined = " ".join(
        _token(warning.get(field)).casefold()
        for field in ("kind", "error", "effect", "message", "reason")
        if _token(warning.get(field))
    )
    if "stale" in combined:
        return "stale"
    failed_markers = (
        "fetch failed",
        "fetch_failed",
        "fetch error",
        "fetch_error",
        "runtime",
        "timeout",
        "unavailable",
        "schema drift",
        "schema_drift",
        "missing input",
        "missing_input",
        "no onedrive sync",
        "no_onedrive_sync",
    )
    if any(marker in combined for marker in failed_markers):
        return "failed"
    return "partial"


def _warning_detail(warning: Mapping[str, Any]) -> str:
    detail = next(
        (
            _token(warning.get(field))
            for field in ("effect", "error", "message", "reason", "kind")
            if _token(warning.get(field))
        ),
        "legacy partial-data warning",
    )
    dataset = _token(warning.get("dataset")) or "legacy workbook"
    return f"{dataset}: {detail}"[:500]


def _warning_target_keys(
    warning: Mapping[str, Any],
    *,
    mapped_sheets: Mapping[str, str],
    available_keys: Iterable[str],
) -> tuple[str, ...]:
    """Resolve warning dataset labels to the source frames they qualify."""

    dataset_token = _name_token(warning.get("dataset"))
    available = tuple(available_keys)
    if dataset_token in {"legacyworkbook", "reportdata", "all", "allsources"}:
        return available
    if dataset_token in {"csconsolebundle", "renewalcsconsolebundle"}:
        return tuple(
            key
            for key in (
                "action_plans",
                "adoption_barriers",
                "customer_pulse",
                "success_priorities",
            )
            if key in available
        )

    matched: list[str] = []
    for key in available:
        aliases: list[str] = [key]
        aliases.extend(_WARNING_DATASET_EXTRAS.get(key, ()))
        if key in _CORE_SHEET_ALIASES:
            aliases.extend(_CORE_SHEET_ALIASES[key])
            aliases.append(_CANONICAL_SHEET_BY_KEY[key])
        if key in _EXTERNAL_SHEET_ALIASES:
            aliases.extend(_EXTERNAL_SHEET_ALIASES[key])
        if mapped_sheets.get(key):
            aliases.append(mapped_sheets[key])
        if dataset_token and dataset_token in {_name_token(alias) for alias in aliases}:
            matched.append(key)
    return tuple(matched)


def _apply_warning_source_states(
    frames: Mapping[str, pd.DataFrame],
    external_frames: Mapping[str, pd.DataFrame],
    warnings: Sequence[Mapping[str, Any]],
    *,
    mapped_sheets: Mapping[str, str],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Carry warning-led availability into the frames used for all facts."""

    combined = {**frames, **external_frames}
    for warning in warnings:
        warning_state = _warning_source_state(warning)
        if warning_state is None:
            continue
        for key in _warning_target_keys(
            warning,
            mapped_sheets=mapped_sheets,
            available_keys=combined,
        ):
            frame = combined[key]
            current = cm.source_data_state(frame)
            # Adoption Barriers is a merged Snowflake + CSConsole surface. If
            # the merged frame already proves partial coverage, a warning that
            # only the CSConsole component cannot enforce technology scope must
            # not relabel the whole source unavailable.  That erased the fact
            # that another component was successfully evaluated and caused
            # Compact/Renewal to disagree with Comprehensive for identical
            # source frames.
            component_partial = (
                key == "adoption_barriers"
                and current.get("state") == "partial"
                and _name_token(warning.get("dataset"))
                == _name_token("csconsole_adoption_barriers")
                and warning_state in {"failed", "unavailable"}
            )
            effective_warning_state = "partial" if component_partial else warning_state
            if _state_priority(current["state"]) > _state_priority(effective_warning_state):
                continue
            details = [
                detail
                for detail in (current.get("detail"), _warning_detail(warning))
                if _token(detail)
                and not (current.get("state") in {"available", "zero"} and detail == current.get("detail"))
            ]
            combined[key] = _apply_state_attrs(
                frame,
                state=effective_warning_state,
                detail="; ".join(dict.fromkeys(details))[:500],
            )
    return (
        {key: combined[key] for key in frames},
        {key: combined[key] for key in external_frames},
    )


def _attribution_labels(
    record: Mapping[str, Any],
    *,
    member_display_names_by_email: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return source-owned team attribution without inventing a manager.

    ``Attributed_Team_Members`` is preferred because canonical legacy exports
    may retain more than one responsible CSSM for a shared source record.  A
    semicolon or pipe is the canonical multi-value separator; commas remain
    part of a person's display name.
    """

    for column in _ATTRIBUTION_COLUMNS:
        if column not in record:
            continue
        raw_value = record.get(column)
        if isinstance(raw_value, (list, tuple, set)):
            candidates = [_token(value) for value in raw_value]
        else:
            token = _token(raw_value)
            candidates = re.split(r"\s*(?:;|\|)\s*", token) if token else []
        display_names = member_display_names_by_email or {}
        labels: dict[str, str] = {}
        for candidate in candidates:
            label = _token(candidate)
            if not label:
                continue
            label = display_names.get(label.casefold(), label)
            # A prior legacy export may already contain the presentation-only
            # placeholder ``Unassigned / Portfolio`` while a later, more
            # authoritative field on the same row carries CSSM_EMAIL.  Do not
            # let that placeholder short-circuit the source-owned identity.
            # If no real identity exists, returning no labels below still
            # routes the row through the explicit non-countable bundle.
            if label.casefold() in _UNASSIGNED_ATTRIBUTION_LABELS:
                continue
            labels.setdefault(label.casefold(), label)
        if labels:
            return tuple(labels[key] for key in sorted(labels))
    return ()


def _project_scoped_account_attribution(
    frames: Mapping[str, pd.DataFrame],
    *,
    member_display_names_by_email: Mapping[str, str] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Carry scoped subscription ownership onto account-backed source rows.

    Legacy report workbooks often retain team attribution only on their
    subscription sheet.  Treating Action Plans, barriers, pulse, TAC cases,
    and Success Priorities as unassigned in the canonical adapter made member
    rollups read as zero even when every row had a stable account ID and the
    same workbook declared that account's team member.  It could also leak a
    source-reported owner from another team into a manager-specific summary.

    The scoped subscription frame is already authorized by the production
    report route.  Build a stable-account mapping from it, including shared
    accounts, and use that mapping as the report-attribution identity.  Keep
    any source-reported attribution in a separate audit column; rows without a
    stable mapped account retain their direct source attribution or remain
    explicitly unassigned.  No customer-name inference is permitted.
    """

    projected = {key: frame.copy() if isinstance(frame, pd.DataFrame) else frame for key, frame in frames.items()}
    subscriptions = projected.get("subscriptions", pd.DataFrame())
    if not isinstance(subscriptions, pd.DataFrame) or subscriptions.empty:
        return projected, {}

    by_account: dict[str, dict[str, str]] = {}
    for _, row in subscriptions.iterrows():
        record = row.to_dict()
        account_id = _token(_record_value(record, _ACCOUNT_ID_FIELD_TOKENS)).casefold()
        if not account_id:
            continue
        labels = _attribution_labels(
            record,
            member_display_names_by_email=member_display_names_by_email,
        )
        if not labels:
            continue
        account_labels = by_account.setdefault(account_id, {})
        for label in labels:
            if label == _UNASSIGNED_BUNDLE:
                continue
            account_labels.setdefault(label.casefold(), label)

    counts: dict[str, int] = {}
    for source_key, frame in list(projected.items()):
        if source_key == "subscriptions" or not isinstance(frame, pd.DataFrame):
            continue
        attrs = dict(getattr(frame, "attrs", {}) or {})
        use = frame.copy()
        projected_rows = 0
        for index, row in use.iterrows():
            record = row.to_dict()
            account_id = _token(_record_value(record, _ACCOUNT_ID_FIELD_TOKENS)).casefold()
            account_labels = by_account.get(account_id, {})
            if not account_labels:
                continue
            direct = _attribution_labels(
                record,
                member_display_names_by_email=member_display_names_by_email,
            )
            if direct:
                use.loc[index, "Source_Reported_Attribution"] = "; ".join(direct)
            inherited = [account_labels[key] for key in sorted(account_labels)]
            use.loc[index, "Attributed_Team_Members"] = "; ".join(inherited)
            use.loc[index, "Attribution_Basis"] = "Scoped subscription account ownership"
            projected_rows += 1
        use.attrs.update(attrs)
        use.attrs["account_attribution_projected_rows"] = projected_rows
        projected[source_key] = use
        if projected_rows:
            counts[source_key] = projected_rows
    return projected, counts


def _build_attributed_team_data(
    frames: Mapping[str, pd.DataFrame],
    *,
    member_display_names_by_email: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Partition already-scoped legacy rows by their retained attribution.

    The previous adapter wrapped all rows in a bundle named after the selected
    manager (or, for Subscription, the customer).  The canonical aggregator
    correctly treats a bundle key as authoritative CSSM attribution, so that
    shortcut replaced real owners and created a fictitious team member.  This
    projection retains every row, duplicates shared-attribution rows into each
    source-owned member bundle, and uses an explicitly non-countable portfolio
    bundle only where a row carries no attribution.
    """

    display_by_key: dict[str, str] = {}
    display_names = {
        str(email).strip().casefold(): _token(name)
        for email, name in (member_display_names_by_email or {}).items()
        if str(email).strip() and _token(name)
    }
    assignments: dict[str, dict[int, tuple[str, ...]]] = {}
    needs_unassigned = False
    for source_key, frame in frames.items():
        source_assignments: dict[int, tuple[str, ...]] = {}
        if isinstance(frame, pd.DataFrame):
            for position, (_, record) in enumerate(frame.iterrows()):
                labels = _attribution_labels(
                    record,
                    member_display_names_by_email=display_names,
                )
                if not labels:
                    labels = (_UNASSIGNED_BUNDLE,)
                    needs_unassigned = True
                folded_labels: list[str] = []
                for label in labels:
                    folded = label.casefold()
                    if label != _UNASSIGNED_BUNDLE:
                        existing = display_by_key.get(folded)
                        if existing is None or (label.casefold(), label) < (
                            existing.casefold(),
                            existing,
                        ):
                            display_by_key[folded] = label
                    folded_labels.append(folded)
                source_assignments[position] = tuple(dict.fromkeys(folded_labels))
        assignments[source_key] = source_assignments

    if needs_unassigned or not display_by_key:
        display_by_key[_UNASSIGNED_BUNDLE] = _UNASSIGNED_BUNDLE

    team_data: dict[str, dict[str, Any]] = {}
    for folded_label in sorted(display_by_key):
        display_label = display_by_key[folded_label]
        bundle: dict[str, Any] = {}
        for source_key, frame in frames.items():
            source_frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
            positions = [
                position for position, labels in assignments.get(source_key, {}).items() if folded_label in labels
            ]
            selected = source_frame.iloc[positions].copy()
            if not selected.empty:
                # Shared-attribution rows are intentionally copied into more
                # than one member bundle. Stable IDs are merged later by ID;
                # malformed-but-retained rows without an ID need this
                # private source-position key so the aggregator can merge the
                # copies and preserve the complete attribution union instead
                # of publishing one apparent row per member.
                selected["_AdoptIQ_Partition_Row_Key"] = [f"{source_key}:{position}" for position in positions]
            selected.attrs.update(dict(getattr(source_frame, "attrs", {}) or {}))
            bundle[source_key] = selected
        if display_label == _UNASSIGNED_BUNDLE:
            bundle["_adoptiq_unassigned_bundle"] = True
        team_data[display_label] = bundle
    return team_data


def _temporary_output(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.stem}.canonical-",
        suffix=target.suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _temporary_backup(target: Path) -> Path:
    """Reserve a same-directory path for an exact pre-install target copy."""

    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.stem}.rollback-",
        suffix=target.suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _install_validated_pair(
    temporary_word: Path,
    target_word: Path,
    temporary_source_data: Path,
    target_source_data: Path,
) -> None:
    """Install a validated DOCX/XLSX pair or restore the prior pair exactly."""

    pair = (
        ("Source Data", temporary_source_data, target_source_data),
        ("Word", temporary_word, target_word),
    )
    backups: dict[Path, Path] = {}
    installed: set[Path] = set()
    installation_started = False
    try:
        # Snapshot both visible targets before changing either.  Copies keep
        # the old pair readable while the rollback material is prepared.
        for _label, _temporary, target in pair:
            if not target.exists():
                continue
            backup = _temporary_backup(target)
            backups[target] = backup
            shutil.copy2(target, backup)

        installation_started = True
        for _label, temporary, target in pair:
            os.replace(temporary, target)
            installed.add(target)
    except Exception as install_error:
        rollback_errors: list[str] = []
        if installation_started:
            for label, temporary, target in reversed(pair):
                # A successful replace consumes the temporary.  Checking both
                # signals also handles a replace wrapper that raises only
                # after the underlying filesystem operation completed.
                target_changed = target in installed or not temporary.exists()
                if not target_changed:
                    continue
                backup = backups.get(target)
                try:
                    if backup is not None and backup.exists():
                        os.replace(backup, target)
                    else:
                        target.unlink(missing_ok=True)
                except OSError as rollback_error:
                    rollback_errors.append(f"{label}: {type(rollback_error).__name__}")
        for backup in backups.values():
            try:
                backup.unlink(missing_ok=True)
            except OSError as cleanup_error:
                rollback_errors.append(f"backup cleanup: {type(cleanup_error).__name__}")
        rollback_state = (
            "rollback completed" if not rollback_errors else "rollback incomplete: " + ", ".join(rollback_errors)
        )
        raise CanonicalReportAdapterError(
            f"canonical artifact pair installation failed: {type(install_error).__name__}; {rollback_state}"
        ) from install_error
    else:
        for backup in backups.values():
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                # The validated pair is already installed. A same-directory
                # rollback copy is harmless and preferable to falsely
                # reporting that only one canonical target was published.
                pass


def _contract_error(stage: str, result: Mapping[str, Any]) -> CanonicalReportAdapterError:
    errors = result.get("errors") or ["unknown validation failure"]
    summary = "; ".join(str(error) for error in errors[:8])
    return CanonicalReportAdapterError(f"{stage} failed: {summary}")


def _footer_contract(document: Document) -> dict[str, Any]:
    section_count = len(document.sections)
    stamped_sections = sum(
        1
        for section in document.sections
        if any(paragraph.text.strip().startswith("AdoptIQ v") for paragraph in section.footer.paragraphs)
    )
    return {
        "ok": section_count > 0 and stamped_sections == section_count,
        "sections": section_count,
        "stamped_sections": stamped_sections,
    }


def _apply_required_build_footer(document: Document) -> dict[str, Any]:
    """Best-effort in-memory stamp before serialization (may defer to R74 on disk)."""

    from _r68_build_label import apply_word_footer  # noqa: PLC0415

    if apply_word_footer(document):
        result = _footer_contract(document)
        if result["ok"]:
            return result
    return {"ok": False, "sections": len(document.sections), "stamped_sections": 0}


def _ensure_build_footer_on_disk(word_path: Path) -> dict[str, Any]:
    """Round 149 / Build 111: fail-closed footer via R74 zip enforcer after save.

    Frozen builds can miss ``docx.enum.*`` submodules so in-memory
    ``apply_word_footer`` returns ``False`` even though the R74 post-save
    XML enforcer (already used by Comprehensive / Leader / Renewal) succeeds.
    """

    from _r74_footer_enforcer import enforce_build_label_footer  # noqa: PLC0415

    diag = enforce_build_label_footer(word_path)
    reason = str(diag.get("reason") or "")
    if not diag.get("injected") and reason != "already stamped":
        raise CanonicalReportAdapterError(
            "canonical Word build footer could not be applied" + (f" ({reason})" if reason else "")
        )
    document = Document(word_path)
    result = _footer_contract(document)
    if not result["ok"]:
        raise _contract_error(
            "canonical Word build footer validation",
            {
                "errors": [f"stamped {result['stamped_sections']} of {result['sections']} section(s)"],
            },
        )
    return result


def canonicalize_legacy_artifacts(
    word_path: Any,
    workbook_path: Any,
    *,
    report_type: str,
    manager_name: str,
    technology: str,
    scope_type: str,
    scope_value: str,
    days: int,
    as_of: Any,
    data_as_of_utc: Any = None,
    data_as_of_state: str = "available",
    data_as_of_detail: str = "",
    retrieval_attempted_at_utc: Any = "",
    partial_data_warnings: Sequence[Any] = (),
    member_display_names_by_email: Mapping[str, str] | None = None,
    source_frame_overrides: Mapping[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Replace a legacy Word target with a validated canonical decision pair.

    The input workbook is read only until a complete canonical pair has been
    serialized and validated. Its known legacy sheets are partitioned by any
    retained source attribution, preserving every usable row and carrying
    empty/unavailable/partial/stale state through DataFrame attrs. A distinct
    legacy workbook is retired only after both canonical targets are replaced.
    The returned ``word_path`` is the supplied Word target; ``source_data_path``
    is its separately named canonical companion workbook.
    """

    target_word = Path(word_path).expanduser().resolve()
    source_workbook = Path(workbook_path).expanduser().resolve()
    if target_word.suffix.casefold() != ".docx":
        raise CanonicalReportAdapterError("word_path must use the .docx extension")
    if not target_word.is_file():
        raise CanonicalReportAdapterError(f"legacy Word artifact not found: {target_word}")
    if source_workbook.suffix.casefold() not in {".xlsx", ".xlsm", ".xls"}:
        raise CanonicalReportAdapterError("workbook_path must be an Excel workbook")
    if not source_workbook.is_file():
        raise CanonicalReportAdapterError(f"legacy workbook artifact not found: {source_workbook}")
    if int(days) <= 0:
        raise CanonicalReportAdapterError("days must be a positive integer")
    if not _token(manager_name):
        raise CanonicalReportAdapterError("manager_name is required")
    if not _token(scope_type) or not _token(scope_value):
        raise CanonicalReportAdapterError("scope_type and scope_value are required")

    source_overrides = dict(source_frame_overrides or {})
    allowed_override_keys = set(_CORE_SHEET_ALIASES) | set(_EXTERNAL_SHEET_ALIASES)
    unknown_override_keys = sorted(set(source_overrides) - allowed_override_keys)
    if unknown_override_keys:
        raise CanonicalReportAdapterError(
            "source_frame_overrides contains unsupported source key(s): "
            + ", ".join(unknown_override_keys)
        )
    invalid_override_keys = sorted(
        key for key, frame in source_overrides.items() if not isinstance(frame, pd.DataFrame)
    )
    if invalid_override_keys:
        raise CanonicalReportAdapterError(
            "source_frame_overrides values must be pandas DataFrames: "
            + ", ".join(invalid_override_keys)
        )

    family = _report_family(report_type)
    target_source_data = delivery.source_data_path_for_word(target_word).resolve()
    temporary_word: Path | None = None
    temporary_source_data: Path | None = None
    legacy_workbook_retired = False

    try:
        with pd.ExcelFile(source_workbook) as excel:
            lookup = _sheet_lookup(excel.sheet_names)
            info, workbook_warnings = _read_report_info(excel, lookup)
            mapped_sheets: dict[str, str] = {}
            frames: dict[str, pd.DataFrame] = {}
            superseded_family_risk_fields: dict[str, set[str]] = {}
            for key, aliases in _CORE_SHEET_ALIASES.items():
                use_aliases = aliases
                subscription_summary = False
                if key == "subscriptions":
                    use_aliases = (*aliases, *_SUBSCRIPTION_FALLBACK_ALIASES[family])
                    selected = _first_sheet(lookup, use_aliases)
                    subscription_summary = bool(selected and _name_token(selected) == _name_token("Summary"))
                frame, selected_sheet = _load_source_frame(
                    excel,
                    lookup=lookup,
                    aliases=use_aliases,
                    key=key,
                    canonical_sheet=_CANONICAL_SHEET_BY_KEY[key],
                    info=info,
                    subscription_summary=subscription_summary,
                    repair_subscription_shift=family == "subscription",
                    source_frame=source_overrides.get(key),
                )
                if family == "compact" and key == "subscriptions" and selected_sheet is not None:
                    frame, superseded = _supersede_compact_display_risk_scores(
                        frame,
                        sheet_name=selected_sheet,
                        disclose=True,
                    )
                    if superseded:
                        superseded_family_risk_fields.setdefault(
                            selected_sheet,
                            set(),
                        ).update(superseded)
                frames[key] = frame
                if selected_sheet:
                    mapped_sheets[key] = selected_sheet

            external_frames: dict[str, pd.DataFrame] = {}
            for key, aliases in _EXTERNAL_SHEET_ALIASES.items():
                canonical_sheet = "External_Incidents" if key == "external_incidents" else "External_Bugs"
                frame, selected_sheet = _load_source_frame(
                    excel,
                    lookup=lookup,
                    aliases=aliases,
                    key=key,
                    canonical_sheet=canonical_sheet,
                    info=info,
                    source_frame=source_overrides.get(key),
                )
                external_frames[key] = frame
                if selected_sheet:
                    mapped_sheets[key] = selected_sheet

            selected_by_sheet = {sheet_name: key for key, sheet_name in mapped_sheets.items()}
            family_fact_tokens = {_name_token(alias) for alias in _FAMILY_FACT_SHEET_ALIASES}
            report_info_tokens = {
                _name_token("Report_Info"),
                _name_token("Report Info"),
            }
            family_fact_frames: dict[str, pd.DataFrame] = {}
            ambiguous_family_labels: dict[str, tuple[str, ...]] = {}
            ambiguous_subscription_labels: dict[str, tuple[str, ...]] = {}
            ambiguous_family_risk_fields: set[str] = set()
            ambiguous_family_risk_rows = 0
            ambiguity_warnings: list[dict[str, Any]] = []
            sheet_disposition: dict[str, str] = {}
            substantive_sheets: list[str] = []
            quarantined_sheets: list[str] = []
            for sheet_name in excel.sheet_names:
                sheet_token = _name_token(sheet_name)
                if sheet_token in report_info_tokens:
                    sheet_disposition[sheet_name] = "mapped_metadata"
                    continue
                if sheet_token in family_fact_tokens:
                    raw_family_frame = pd.read_excel(
                        excel,
                        sheet_name=sheet_name,
                        dtype=object,
                    )
                    substantive = _substantive_rows(raw_family_frame)
                    if substantive.empty:
                        sheet_disposition[sheet_name] = "non_substantive_empty"
                        continue
                    substantive_sheets.append(sheet_name)
                    safe_family_frame = _safe_family_fact_frame(
                        raw_family_frame,
                        sheet_name,
                    )
                    if family == "compact":
                        safe_family_frame, superseded = _supersede_compact_display_risk_scores(
                            safe_family_frame,
                            sheet_name=sheet_name,
                        )
                        if superseded:
                            superseded_family_risk_fields.setdefault(
                                sheet_name,
                                set(),
                            ).update(superseded)
                    if safe_family_frame.shape[1] == 0:
                        sheet_disposition[sheet_name] = "quarantined_no_public_fields"
                        quarantined_sheets.append(sheet_name)
                        continue
                    family_fact_frames[sheet_name] = safe_family_frame
                    sheet_disposition[sheet_name] = (
                        "mapped_source_and_family_facts" if sheet_name in selected_by_sheet else "mapped_family_facts"
                    )
                    continue
                if sheet_name in selected_by_sheet:
                    sheet_disposition[sheet_name] = f"mapped_source:{selected_by_sheet[sheet_name]}"
                    substantive_sheets.append(sheet_name)
                    continue

                raw_unmapped_frame = pd.read_excel(
                    excel,
                    sheet_name=sheet_name,
                    dtype=object,
                )
                if _substantive_rows(raw_unmapped_frame).empty:
                    sheet_disposition[sheet_name] = "non_substantive_empty"
                    continue
                substantive_sheets.append(sheet_name)
                quarantined_sheets.append(sheet_name)
                sheet_disposition[sheet_name] = "quarantined_unmapped_substantive"

            if family == "compact":
                ambiguous_family_labels = _ambiguous_family_customer_labels(family_fact_frames)
                ambiguous_subscription_labels = _ambiguous_subscription_customer_labels(
                    frames.get("subscriptions", pd.DataFrame())
                )
                ambiguous_keys = set(ambiguous_family_labels) | set(ambiguous_subscription_labels)
                if ambiguous_keys:
                    selected_subscription_sheet = mapped_sheets.get(
                        "subscriptions",
                        "",
                    )
                    (
                        frames["subscriptions"],
                        core_quarantined_fields,
                        core_quarantined_rows,
                    ) = _quarantine_ambiguous_family_risk_claims(
                        frames["subscriptions"],
                        sheet_name=selected_subscription_sheet,
                        ambiguous_customer_keys=ambiguous_keys,
                        disclose=True,
                    )
                    ambiguous_family_risk_fields.update(core_quarantined_fields)
                    ambiguous_family_risk_rows = max(
                        ambiguous_family_risk_rows,
                        core_quarantined_rows,
                    )
                    for sheet_name, family_frame in list(family_fact_frames.items()):
                        (
                            family_fact_frames[sheet_name],
                            quarantined_fields,
                            quarantined_rows,
                        ) = _quarantine_ambiguous_family_risk_claims(
                            family_frame,
                            sheet_name=sheet_name,
                            ambiguous_customer_keys=ambiguous_keys,
                        )
                        ambiguous_family_risk_fields.update(quarantined_fields)
                        ambiguous_family_risk_rows = max(
                            ambiguous_family_risk_rows,
                            quarantined_rows,
                        )
                    labels = sorted(
                        {
                            label
                            for collision in (
                                *ambiguous_family_labels.values(),
                                *ambiguous_subscription_labels.values(),
                            )
                            for label in collision
                        },
                        key=lambda value: (value.casefold(), value),
                    )
                    ambiguity_warnings.append(
                        {
                            "dataset": "Subscriptions",
                            "kind": "ambiguous_customer_name",
                            "source_sheet": "Risk_Summary",
                            "effect": (
                                "Legacy Risk_Summary customer labels could not resolve "
                                "to one stable account identity "
                                f"({'; '.join(labels)}). Their family-risk cells were "
                                "quarantined; canonical "
                                "Account_Summary risk uses only unambiguous account-level "
                                "evidence, so subscription and risk coverage are partial."
                            )[:500],
                        }
                    )

        if not mapped_sheets:
            raise CanonicalReportAdapterError("legacy workbook does not contain a recognized report source sheet")
        if quarantined_sheets and source_workbook == target_source_data:
            raise CanonicalReportAdapterError(
                "legacy workbook contains quarantined substantive sheet(s) but shares "
                "the canonical Source Data path, so replacing it would destroy the "
                "only retained evidence: " + ", ".join(sorted(quarantined_sheets))
            )

        quarantine_warnings = [
            {
                "dataset": sheet_name,
                "kind": "legacy_sheet_quarantined",
                "effect": (
                    "This substantive legacy sheet was not mapped into the canonical "
                    "contract; the original workbook was retained for review."
                ),
            }
            for sheet_name in sorted(quarantined_sheets)
        ]
        normalized_warnings = _normalize_warnings(
            partial_data_warnings,
            [*workbook_warnings, *quarantine_warnings, *ambiguity_warnings],
        )
        frames, external_frames = _apply_warning_source_states(
            frames,
            external_frames,
            normalized_warnings,
            mapped_sheets=mapped_sheets,
        )
        frames, account_attribution_counts = _project_scoped_account_attribution(
            frames,
            member_display_names_by_email=member_display_names_by_email,
        )
        team_data = _build_attributed_team_data(
            frames,
            member_display_names_by_email=member_display_names_by_email,
        )
        fact_kwargs: dict[str, Any] = {
            "report_type": _token(report_type),
            "scope_type": _token(scope_type),
            "scope_value": _token(scope_value),
            "manager_name": _token(manager_name),
            "days": int(days),
            "as_of": as_of,
            "data_as_of_utc": data_as_of_utc,
            "data_as_of_state": data_as_of_state,
            "data_as_of_detail": data_as_of_detail,
            "retrieval_attempted_at_utc": retrieval_attempted_at_utc,
            "data_mode": _token(_info_value(info, "Data Mode", "Data_Mode")),
            "live_validation_performed": (
                True
                if _token(
                    _info_value(
                        info,
                        "Live Validation Performed",
                        "Live_Source_Validation",
                    )
                ).casefold()
                in {"yes", "true", "1", "performed"}
                else False
                if _token(
                    _info_value(
                        info,
                        "Live Validation Performed",
                        "Live_Source_Validation",
                    )
                ).casefold()
                in {"no", "false", "0", "not performed"}
                else None
            ),
            "external_incidents": external_frames["external_incidents"],
            "external_bugs": external_frames["external_bugs"],
            "partial_data_warnings": normalized_warnings,
        }
        if "technology" in inspect.signature(delivery.build_report_facts).parameters:
            fact_kwargs["technology"] = _token(technology)
        facts = delivery.build_report_facts(team_data, **fact_kwargs)
        risk_claims = _validate_family_risk_claims(
            family_fact_frames,
            facts,
            info=info,
            scope_type=_token(scope_type),
            scope_value=_token(scope_value),
        )
        family_fact_rows, family_lineage, family_fact_counts = _family_fact_projection(
            family_fact_frames,
            family=family,
            info=info,
            scope_type=_token(scope_type),
            scope_value=_token(scope_value),
        )
        if not family_fact_rows.empty:
            subscriptions = facts["frames"]["subscriptions"]
            subscription_attrs = dict(getattr(subscriptions, "attrs", {}) or {})
            subscriptions = pd.concat(
                [subscriptions, family_fact_rows],
                ignore_index=True,
                sort=False,
            )
            subscriptions.attrs.update(subscription_attrs)
            facts["frames"]["subscriptions"] = subscriptions
            # Source coverage describes the canonical subscription source,
            # not family-specific presentation facts.  Their typed row count
            # remains in ``legacy_adapter.mapped_family_fact_counts`` and the
            # rows/lineage/evidence stay in the workbook, but changing this
            # shared detail by family made identical source coverage appear
            # semantically different.
        if not family_lineage.empty:
            facts["metric_lineage"] = pd.concat(
                [facts["metric_lineage"], family_lineage],
                ignore_index=True,
                sort=False,
            )
        facts["legacy_adapter"] = {
            "source_workbook": str(source_workbook),
            "mapped_sheets": dict(sorted(mapped_sheets.items())),
            "report_family": family,
            "sheet_disposition": dict(sorted(sheet_disposition.items())),
            "substantive_sheets": sorted(set(substantive_sheets)),
            "mapped_family_fact_counts": dict(sorted(family_fact_counts.items())),
            "quarantined_sheets": sorted(set(quarantined_sheets)),
            "risk_claims_reconciled": risk_claims,
            "superseded_family_risk_fields": {
                sheet_name: sorted(fields) for sheet_name, fields in sorted(superseded_family_risk_fields.items())
            },
            "ambiguous_family_customer_labels": {
                key: list(labels) for key, labels in sorted(ambiguous_family_labels.items())
            },
            "ambiguous_subscription_customer_labels": {
                key: list(labels) for key, labels in sorted(ambiguous_subscription_labels.items())
            },
            "ambiguous_family_risk_quarantine": {
                "rows": ambiguous_family_risk_rows,
                "fields": sorted(ambiguous_family_risk_fields),
            },
            "canonical_risk_source": "Account_Summary.Risk_Score_0_100",
            "account_attribution_projected_counts": dict(sorted(account_attribution_counts.items())),
            "header_repairs": sorted(
                {
                    str(repair)
                    for frame in frames.values()
                    for repair in (frame.attrs.get("legacy_header_repairs") or [])
                }
            ),
        }

        document = delivery.build_concise_word_document(facts)
        _apply_required_build_footer(document)  # Round 149: best-effort pre-save
        sheets = delivery.build_source_data_sheets(facts)
        prewrite_contract = delivery.validate_cross_artifact_contract(
            facts,
            sheets,
            document,
        )
        if not prewrite_contract.get("ok"):
            raise _contract_error("canonical cross-artifact validation", prewrite_contract)

        temporary_word = _temporary_output(target_word)
        temporary_source_data = _temporary_output(target_source_data)
        document.save(temporary_word)
        footer_contract = _ensure_build_footer_on_disk(temporary_word)  # Round 149
        delivery.write_source_data_workbook(temporary_source_data, sheets)

        serialized_document = Document(temporary_word)
        serialized_footer_contract = _footer_contract(serialized_document)
        if not serialized_footer_contract.get("ok"):
            raise _contract_error(
                "serialized Word build footer validation",
                {"errors": ["canonical build footer was not retained after serialization"]},
            )
        serialized_contract = delivery.validate_cross_artifact_contract(
            facts,
            sheets,
            serialized_document,
        )
        if not serialized_contract.get("ok"):
            raise _contract_error("serialized Word validation", serialized_contract)
        written_workbook_contract = delivery.validate_written_source_workbook(
            temporary_source_data,
            facts,
        )
        if not written_workbook_contract.get("ok"):
            raise _contract_error(
                "written Source Data validation",
                written_workbook_contract,
            )

        _install_validated_pair(
            temporary_word,
            target_word,
            temporary_source_data,
            target_source_data,
        )
        temporary_word = None
        temporary_source_data = None
        if source_workbook != target_source_data and not quarantined_sheets:
            try:
                source_workbook.unlink()
            except OSError as exc:
                raise CanonicalReportAdapterError(
                    "validated canonical artifacts were written, but the legacy "
                    f"intermediate workbook could not be retired: {type(exc).__name__}"
                ) from exc
            legacy_workbook_retired = True
        contract = dict(serialized_contract)
        contract.update(
            {
                "ok": True,
                "cross_artifact": prewrite_contract,
                "serialized_word": serialized_contract,
                "written_source_data": written_workbook_contract,
                "footer": serialized_footer_contract,
                "prewrite_footer": footer_contract,
                "mapped_sheets": dict(sorted(mapped_sheets.items())),
                "sheet_disposition": dict(sorted(sheet_disposition.items())),
                "substantive_sheets": sorted(set(substantive_sheets)),
                "quarantined_sheets": sorted(set(quarantined_sheets)),
                "legacy_workbook_retired": legacy_workbook_retired,
            }
        )
        return {
            "word_path": str(target_word),
            "source_data_path": str(target_source_data),
            "facts": facts,
            "contract": contract,
            "legacy_workbook_retired": legacy_workbook_retired,
        }
    except CanonicalReportAdapterError:
        raise
    except Exception as exc:
        raise CanonicalReportAdapterError(
            f"legacy artifact canonicalization failed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        for temporary in (temporary_word, temporary_source_data):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = [
    "CanonicalReportAdapterError",
    "canonicalize_legacy_artifacts",
]
