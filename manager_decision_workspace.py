"""Manager Decision Workspace domain helpers.

Round 146 keeps workspace behavior outside the Flask monolith so report scope
previews, artifact summaries, history filtering, and comparisons share one
deterministic contract.  The module reads only already-generated, formula-free
Source Data workbooks; it never reaches Snowflake or an LLM.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from openpyxl import load_workbook


WORKSPACE_SCHEMA = "manager-decision-workspace/v1"
MAX_WORKBOOK_BYTES = 100 * 1024 * 1024
MAX_SHEET_ROWS = 50_000
MAX_PUBLIC_ITEMS = 250

REPORT_TYPES: Mapping[str, Mapping[str, Any]] = {
    "leader": {
        "label": "Leader",
        "purpose": "Manager decisions with Team, Member, and Customer drill-down",
        "sources": ("Subscriptions", "Action Plans", "Adoption Barriers", "Customer Pulse", "TAC Cases"),
        "scopes": ("team", "member", "customer"),
    },
    "comprehensive": {
        "label": "Comprehensive",
        "purpose": "Broad portfolio decisions with complete paired source data",
        "sources": ("Subscriptions", "Action Plans", "Adoption Barriers", "Customer Pulse", "TAC Cases", "External Intelligence"),
        "scopes": ("team", "customer"),
    },
    "compact": {
        "label": "Compact",
        "purpose": "Fast executive briefing focused on material exceptions",
        "sources": ("Subscriptions", "Action Plans", "Adoption Barriers", "Customer Pulse", "TAC Cases"),
        "scopes": ("team", "customer"),
    },
    "renewal_portfolio": {
        "label": "Renewal portfolio",
        "purpose": "Portfolio renewal decisions and risk concentration",
        "sources": ("Subscriptions", "Action Plans", "Adoption Barriers", "Customer Pulse", "TAC Cases", "External Intelligence"),
        "scopes": ("team",),
    },
    "renewal": {
        "label": "Renewal customer",
        "purpose": "Deep renewal-risk explanation for one customer",
        "sources": ("Subscriptions", "Action Plans", "Adoption Barriers", "Customer Pulse", "TAC Cases", "External Intelligence"),
        "scopes": ("customer",),
    },
    "subscription": {
        "label": "Subscription analysis",
        "purpose": "Product adoption, barriers, support, and renewal evidence",
        "sources": ("Subscription", "Action Plans", "Adoption Barriers", "Customer Pulse", "TAC Cases"),
        "scopes": ("subscription",),
    },
}

_TERMINAL_STATUSES = {"completed", "success", "done"}
_COMPLETED_AP_STATUSES = {"completed", "complete", "closed", "done", "resolved", "cancelled", "canceled"}
_COMPARABLE_SOURCE_STATES = {"available", "zero"}
_SAFE_KEY_RE = re.compile(r"[^a-z0-9]+")
_EMAIL_RE = re.compile(r"(?P<email>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)

_CHART_METADATA: Mapping[str, tuple[str, str]] = {
    "activity_mix": (
        "Activity mix",
        "Complete source-record counts by activity type for the selected scope.",
    ),
    "action_plan_status_aging": (
        "Action Plan status and aging",
        "Mutually exclusive Action Plan lifecycle buckets at the report as-of time.",
    ),
    "risk_distribution": (
        "Customer risk distribution",
        "Customers grouped by the report's canonical risk band.",
    ),
    "activity_trend": (
        "Activity trend",
        "Dated source records grouped into the report's weekly periods.",
    ),
}

_SOURCE_LABELS: Mapping[str, str] = {
    "action_plans": "Action Plans",
    "adoption_barriers": "Adoption Barriers",
    "customer_pulse": "Customer Pulse",
    "success_priorities": "Success Priorities",
    "subscriptions": "Subscriptions",
    "tac_cases": "TAC Cases",
    "csone": "TAC Cases",
    "csone_tac": "TAC Cases",
    "csone_tac_cases": "TAC Cases",
    "external_incidents": "External incidents",
    "external_bugs": "External bugs",
}


def _text(value: object, limit: int = 1_000) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.casefold() in {"nan", "none", "nat"}:
        return ""
    return text[:limit]


def _key(value: object) -> str:
    return _SAFE_KEY_RE.sub("_", _text(value).casefold()).strip("_")


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, 6)
    return int(rounded) if rounded.is_integer() else rounded


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"1", "true", "yes", "y", "overdue"}


def _first(record: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    folded = {_key(name): value for name, value in record.items()}
    for alias in aliases:
        value = folded.get(_key(alias))
        if _text(value):
            return value
    return None


def _info_value(info: Mapping[str, Any], aliases: Iterable[str]) -> str:
    folded = {_key(name): value for name, value in info.items()}
    for alias in aliases:
        value = folded.get(_key(alias))
        if _text(value):
            return _text(value, 4_000)
    return ""


def _public_label(value: object) -> str:
    normalized = _key(value)
    if normalized in _SOURCE_LABELS:
        return _SOURCE_LABELS[normalized]
    words = re.sub(r"[_-]+", " ", _text(value, 120)).strip()
    return words.title() if words else "A report source"


def _public_warning_copy(warning: object) -> str:
    """Convert internal warning shapes into bounded manager-facing copy."""

    source = "A report source"
    tokens: list[str] = []
    if isinstance(warning, Mapping):
        source = _public_label(
            warning.get("dataset")
            or warning.get("source")
            or warning.get("source_sheet")
        )
        tokens = [
            _text(warning.get(name), 1_000).casefold()
            for name in ("kind", "state", "source_state", "error", "effect", "detail")
            if _text(warning.get(name))
        ]
    else:
        raw_warning = _text(warning, 1_000)
        prefix = re.match(r"^([A-Za-z0-9_-]+)(?:\s*\(|\s*\||\s*:)", raw_warning)
        if prefix:
            source = _public_label(prefix.group(1))
        tokens = [raw_warning.casefold()]
    combined = " ".join(tokens)
    if not combined:
        return ""
    if "fixture" in combined or "local acceptance" in combined or "offline test" in combined:
        return "This report uses controlled local test data; no live source validation was performed."
    if "stale" in combined or "older than" in combined:
        return f"{source} may be older than expected; confirm timing before acting on time-sensitive details."
    if any(token in combined for token in ("failed", "unavailable", "fetch_error", "could not")):
        return f"{source} could not be retrieved; related metrics are shown as unavailable, not zero."
    if "scope_excluded" in combined or "filter" in combined or "excluded" in combined:
        return f"{source} was limited to records matching the selected scope; excluded records are not counted."
    if any(token in combined for token in ("partial", "truncat", "limit")):
        return f"{source} is only partially available; related totals may be incomplete."
    if "formula" in combined:
        return "This workbook contains formulas and is not eligible for canonical report comparison."
    return f"{source} has a recorded coverage limitation; review the available Source Data before acting."


def _source_warnings(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        copy_value = _public_warning_copy(value)
        key = copy_value.casefold()
        if copy_value and key not in seen:
            result.append(copy_value)
            seen.add(key)
    return result


def _parse_utc(value: object) -> datetime | None:
    raw = _text(value, 120)
    if not raw:
        return None
    candidate = raw.replace(" UTC", "+00:00")
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status_is_completed(value: object) -> bool:
    status_key = _key(value)
    completed_keys = {_key(item) for item in _COMPLETED_AP_STATUSES}
    return status_key in completed_keys or any(
        status_key.startswith(prefix)
        for prefix in ("completed_", "closed_", "resolved_", "cancelled_", "canceled_")
    )


def validate_workspace_selection(
    *,
    report_type: object,
    manager: object,
    technology: object,
    days: object,
    scope_type: object = "team",
    scope_value: object = "",
    subscription_id: object = "",
    allowed_managers: Iterable[str] = (),
    allowed_technologies: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate and normalize one pre-generation workspace selection."""

    normalized_report = _text(report_type).casefold()
    if normalized_report not in REPORT_TYPES:
        raise ValueError("Unsupported report type.")
    normalized_scope = _text(scope_type or "team").casefold()
    allowed_scopes = set(REPORT_TYPES[normalized_report]["scopes"])
    if normalized_scope not in allowed_scopes:
        raise ValueError(
            f"{REPORT_TYPES[normalized_report]['label']} does not support the selected scope."
        )
    normalized_manager = _text(manager)
    manager_allowlist = {_text(item) for item in allowed_managers if _text(item)}
    if normalized_report not in {"renewal", "subscription"}:
        if not normalized_manager:
            raise ValueError("Select a manager.")
        if manager_allowlist and normalized_manager not in manager_allowlist:
            raise ValueError("The selected manager is not available.")
    normalized_technology = _text(technology or "All")
    tech_allowlist = {_text(item) for item in allowed_technologies if _text(item)}
    if tech_allowlist and normalized_technology not in tech_allowlist:
        raise ValueError("The selected technology is not available.")
    try:
        normalized_days = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("Analysis days must be a whole number.") from exc
    if not 1 <= normalized_days <= 365:
        raise ValueError("Analysis days must be between 1 and 365.")
    normalized_value = _text(scope_value)
    normalized_subscription = _text(subscription_id)
    if normalized_scope in {"member", "customer"} and not normalized_value:
        raise ValueError(f"Select an individual {normalized_scope}.")
    if normalized_scope == "subscription" and not normalized_subscription:
        raise ValueError("Select a subscription.")
    return {
        "report_type": normalized_report,
        "report_label": REPORT_TYPES[normalized_report]["label"],
        "manager": normalized_manager,
        "technology": normalized_technology,
        "days": normalized_days,
        "scope_type": normalized_scope,
        "scope_value": normalized_value,
        "subscription_id": normalized_subscription,
    }


def build_scope_preview(
    selection: Mapping[str, Any],
    *,
    members: Sequence[Mapping[str, Any]] = (),
    customers: Sequence[Mapping[str, Any] | str] = (),
    source_mode: str = "live",
    source_state: str = "unknown",
    warning: str = "",
    data_as_of_utc: str = "",
) -> dict[str, Any]:
    """Build the concise, non-claiming scope preview shown before a run."""

    report_type = _text(selection.get("report_type")).casefold()
    spec = REPORT_TYPES[report_type]
    scope_type = _text(selection.get("scope_type") or "team").casefold()
    scope_value = _text(selection.get("scope_value"))
    manager = _text(selection.get("manager"))
    technology = _text(selection.get("technology") or "All")
    days = int(selection.get("days") or 90)
    member_count = len({
        _text(item.get("email") or item.get("value") or item.get("name")).casefold()
        for item in members
        if isinstance(item, Mapping) and _text(item.get("email") or item.get("value") or item.get("name"))
    })
    customer_values: set[str] = set()
    for item in customers:
        value = item.get("value") or item.get("label") if isinstance(item, Mapping) else item
        if _text(value):
            customer_values.add(_text(value).casefold())
    customer_count: int | None = len(customer_values) if customers else None
    if scope_type == "team":
        scope_label = f"{manager or 'Selected manager'} team"
    elif scope_type == "member":
        scope_label = scope_value or "Selected team member"
    elif scope_type == "customer":
        scope_label = scope_value or "Selected customer"
    else:
        scope_label = _text(selection.get("subscription_id")) or "Selected subscription"
    availability = _text(source_state or "unknown").casefold()
    state_message = {
        "available": "Scope data is available for this preview.",
        "partial": "The preview is partial; the generated report will name missing sources.",
        "stale": "The preview uses stale source context and will disclose that limitation.",
        "failed": "Scope data could not be reached; generation may fail honestly.",
        "unavailable": "Scope data is unavailable; no zero-count claim is being made.",
        "zero": "The selected scope is available and contains zero matching records.",
    }.get(availability, "Source coverage will be confirmed during generation.")
    source_mode_value = _text(source_mode or "live").casefold()
    is_fixture = source_mode_value.startswith("local") or "fixture" in source_mode_value
    limitations = [state_message]
    if warning:
        limitations.append(_text(warning, 500))
    if is_fixture:
        limitations.append("Controlled local fixture only; no live validation was performed.")
    return {
        "schema": WORKSPACE_SCHEMA,
        "report_type": report_type,
        "report_label": spec["label"],
        "purpose": spec["purpose"],
        "manager": manager,
        "technology": technology,
        "days": days,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "scope_label": scope_label,
        "member_count": member_count if member_count else None,
        "customer_count": customer_count,
        "expected_sources": list(spec["sources"]),
        "source_mode": source_mode_value,
        "source_state": availability,
        "data_as_of_utc": _text(data_as_of_utc),
        "limitations": limitations,
        "deliverables": ["Decision-focused Word report", "Paired Source Data workbook"],
        "ask_ai_binding": {
            "manager": manager,
            "technology": technology,
            "days": days,
            "scope_type": scope_type,
            "scope_value": scope_value,
        },
        "live_validation_performed": False if is_fixture else None,
    }


def _sheet_records(worksheet: Any, *, limit: int = MAX_SHEET_ROWS) -> tuple[list[dict[str, Any]], int, int]:
    rows = worksheet.iter_rows(values_only=False)
    try:
        header_cells = next(rows)
    except StopIteration:
        return [], 0, 0
    headers = [_text(cell.value, 200) for cell in header_cells]
    records: list[dict[str, Any]] = []
    formula_count = sum(
        1 for cell in header_cells if getattr(cell, "data_type", "") == "f"
    )
    total_rows = 0
    for cells in rows:
        total_rows += 1
        formula_count += sum(1 for cell in cells if getattr(cell, "data_type", "") == "f")
        if len(records) >= limit:
            continue
        record = {
            header: cell.value
            for header, cell in zip(headers, cells)
            if header
        }
        if any(_text(value) for value in record.values()):
            records.append(record)
    return records, total_rows, formula_count


def _action_plan_projection(
    record: Mapping[str, Any],
    row_number: int,
    *,
    as_of_utc: object = "",
    source_sheet: str = "Action_Plans",
) -> dict[str, Any]:
    record_id = _text(_first(record, ("Record_ID", "ID", "Action Plan ID", "TASK_ID")), 240)
    status = _text(_first(record, ("AdoptIQ_Status_Bucket", "Status", "STATUS_C")), 120)
    due_date = _text(_first(record, ("AdoptIQ_Due_Date", "Due Date", "DUE_DATE_C", "TARGET_COMPLETION_DATE_C")), 80)
    customer = _text(_first(record, ("Customer", "BU_NAME", "Account", "Customer_Name")), 240)
    due_at = _parse_utc(due_date)
    as_of = _parse_utc(as_of_utc)
    completed = _status_is_completed(status)
    derived_overdue = bool(due_at and as_of and due_at < as_of and not completed)
    return {
        "record_id": record_id,
        "record_key": record_id.casefold() if record_id else f"missing-id-row-{row_number}",
        "record_id_quality": "stable" if record_id else "missing",
        "customer": customer,
        "customer_key": customer.casefold(),
        "title": _text(_first(record, ("Title", "SUBJECT_C", "Subject", "NAME")), 360) or "Title unavailable",
        "status": status or "Unknown",
        "status_key": _key(status or "unknown"),
        "owner": _text(_first(record, ("Owner", "OWNER_NAME", "OWNER_NAME_C", "ASSIGNEE_NAME_C", "OWNER_EMAIL_C", "Next Action Owner", "NEXT_ACTION_OWNER_C")), 240) or "Unassigned",
        "due_date": due_date,
        "priority": _text(_first(record, ("Priority", "PRIORITY_C", "AdoptIQ_Priority")), 80),
        "next_action": _text(_first(record, ("Next Action", "NEXT_ACTION_C", "NEXT_STEP_C")), 500),
        "is_overdue": (
            _as_bool(_first(record, ("AdoptIQ_Is_Overdue", "Is Overdue", "Overdue")))
            or _key(status) == "overdue"
            or derived_overdue
        ),
        "source_sheet": source_sheet,
    }


def _risk_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    customer = _text(_first(record, ("Account", "Customer_Name", "Customer", "BU_NAME")), 240)
    risk_score = _number(
        _first(
            record,
            (
                "Risk_Score_0_100",
                "Risk Score 0 100",
                "Overall_Risk_Score",
                "Risk_Score_0_10",
            ),
        )
    )
    if risk_score is not None and risk_score <= 10 and not _text(
        _first(record, ("Risk_Score_0_100", "Risk Score 0 100"))
    ):
        risk_score = round(float(risk_score) * 10, 1)
    return {
        "customer": customer,
        "customer_key": customer.casefold(),
        "risk_band": _text(_first(record, ("Risk_Band", "Risk Band", "Risk_Level", "Risk Category")), 80).upper(),
        "risk_score_0_100": risk_score,
        "open_action_plans": _number(_first(record, ("Open_AP", "Open Action Plans"))),
        "overdue_action_plans": _number(_first(record, ("Overdue_AP", "Overdue Action Plans"))),
        "tac_cases": _number(_first(record, ("TAC_Cases", "TAC Cases", "Support_Cases", "Support Cases"))),
        "source_sheet": "Account_Summary",
    }


def _stable_fingerprint(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metric_projection(
    metric_key: str,
    label: str,
    value: object,
    *,
    unit: str,
    source_state: str,
    source_sheet: str,
    provenance: str,
) -> dict[str, Any] | None:
    number = _number(value)
    if number is None:
        return None
    return {
        "metric_key": metric_key,
        "label": label,
        "value": number,
        "display_value": _text(number, 120),
        "unit": unit,
        "source_state": _text(source_state, 80).casefold() or "unknown",
        "source_sheet": source_sheet,
        "provenance": provenance,
    }


def _aggregate_source_state(values: Iterable[object]) -> str:
    states = [_text(value, 80).casefold() for value in values if _text(value)]
    if not states:
        return "unknown"
    order = {
        "available": 0,
        "zero": 1,
        "unknown": 2,
        "stale": 3,
        "partial": 4,
        "unavailable": 5,
        "failed": 6,
    }
    return max(states, key=lambda state: order.get(state, 2))


def _chart_projection(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        chart_id = _text(record.get("Chart_ID"), 120).casefold()
        metric_key = _text(record.get("Metric_Key"), 300)
        if not chart_id or not metric_key or chart_id.endswith("_coverage"):
            continue
        grouped.setdefault(chart_id, []).append(record)
    desired = list(_CHART_METADATA)
    ordered_ids = [item for item in desired if item in grouped]
    ordered_ids.extend(sorted(set(grouped) - set(ordered_ids)))
    charts: list[dict[str, Any]] = []
    for chart_id in ordered_ids:
        rows = grouped[chart_id]
        label, description = _CHART_METADATA.get(
            chart_id,
            (_public_label(chart_id), "Validated values from the paired Source Data workbook."),
        )
        points: list[dict[str, Any]] = []
        units = {_text(row.get("Unit"), 80) for row in rows if _text(row.get("Unit"))}
        for row in rows:
            category = _text(row.get("Category") or row.get("Series"), 240)
            period = _text(row.get("Period_Start"), 80)[:10]
            point_label = (
                " — ".join(part for part in (_text(row.get("Series"), 160), period) if part)
                if chart_id == "activity_trend"
                else category
            )
            value = _number(row.get("Value"))
            if not point_label or value is None:
                continue
            points.append({
                "label": point_label,
                "value": value,
                "display_value": _text(value, 120),
                "metric_key": _text(row.get("Metric_Key"), 300),
                "source_state": _text(row.get("Source_State"), 80).casefold() or "unknown",
            })
        if not points:
            continue
        charts.append({
            "chart_key": chart_id,
            "label": label,
            "description": description,
            "unit": next(iter(units)) if len(units) == 1 else "",
            "source_state": _aggregate_source_state(point["source_state"] for point in points),
            "series": points[:MAX_PUBLIC_ITEMS],
            "provenance": "canonical Chart_Data",
        })
    return charts


def _record_is_unavailable(record: Mapping[str, Any]) -> bool:
    content = " ".join(_text(value, 240).casefold() for value in record.values())
    return any(
        marker in content
        for marker in ("data_unavailable", "data unavailable", "source unavailable", "fetch failed")
    )


def _record_is_placeholder(record: Mapping[str, Any]) -> bool:
    content = " ".join(_text(value, 240).casefold() for value in record.values())
    return any(
        marker in content
        for marker in (
            "no records in the analysis window",
            "no matching records",
            " - zero",
            " - empty",
        )
    )


def _usable_records(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        record
        for record in records
        if not _as_bool(record.get("_adoptiq_provenance_row"))
        and _text(record.get("AdoptIQ_Status")).upper() != "EMPTY"
        and not _record_is_unavailable(record)
        and not _record_is_placeholder(record)
    ]


def _legacy_report_type(info: Mapping[str, Any]) -> str:
    export_type = _key(_info_value(info, ("Report_Type", "Export type", "Export_Type")))
    if "compact" in export_type:
        return "compact"
    if "renewal_portfolio" in export_type:
        return "renewal_portfolio"
    if "renewal" in export_type:
        return "renewal"
    if "subscription" in export_type:
        return "subscription"
    if "comprehensive" in export_type:
        return "comprehensive"
    if "leader" in export_type:
        return "leader"
    return export_type


def _legacy_workbook_projection(
    *,
    info: Mapping[str, Any],
    sheet_names: Sequence[str],
    read: Any,
) -> dict[str, Any]:
    """Project retired workbook shapes without claiming canonical parity."""

    cache: dict[str, list[dict[str, Any]]] = {}

    def records(name: str) -> list[dict[str, Any]]:
        if name not in cache:
            cache[name] = read(name, MAX_SHEET_ROWS)
        return cache[name]

    def first_sheet(names: Sequence[str]) -> tuple[str, list[dict[str, Any]]]:
        for name in names:
            if name in sheet_names:
                return name, records(name)
        return "", []

    report_type = _legacy_report_type(info)
    as_of_utc = _info_value(
        info,
        ("Data_As_Of_UTC", "Report_Generated_At_UTC", "Generated at (UTC)"),
    )
    action_sheet, raw_actions = first_sheet(("Action_Plans", "Customer_Action_Plans"))
    action_records = _usable_records(raw_actions)
    action_plans = [
        _action_plan_projection(
            record,
            index,
            as_of_utc=as_of_utc,
            source_sheet=action_sheet or "Action_Plans",
        )
        for index, record in enumerate(action_records, 2)
    ]
    action_plans.sort(
        key=lambda item: (
            not item["is_overdue"],
            item["due_date"] or "9999-12-31",
            item["customer"].casefold(),
            item["record_key"],
        )
    )

    barrier_sheet, raw_barriers = first_sheet(
        ("All_Adoption_Barriers", "Customer_Adoption_Barriers", "Adoption_Barriers")
    )
    tac_sheet, raw_tac = first_sheet(
        ("All_Support_Cases", "Customer_Support_Cases", "TAC_Cases", "Escalated_Cases")
    )
    pulse_sheet, raw_pulse = first_sheet(("Customer_Customer_Pulse", "Customer_Pulse"))
    priority_sheet, raw_priorities = first_sheet(
        ("Customer_Success_Priorities", "Success_Priorities")
    )
    risk_sheet, raw_risk = first_sheet(("Account_Summary", "Renewal_Summary", "Risk_Summary"))
    barriers = _usable_records(raw_barriers)
    tac_cases = _usable_records(raw_tac)
    pulse = _usable_records(raw_pulse)
    priorities = _usable_records(raw_priorities)
    risk_records = _usable_records(raw_risk)
    accounts = [item for item in (_risk_projection(record) for record in risk_records) if item["customer"]]

    def state(sheet_name: str, raw: Sequence[Mapping[str, Any]], usable: Sequence[Mapping[str, Any]]) -> str:
        if not sheet_name:
            return "unknown"
        if raw and not usable:
            return "unavailable" if any(_record_is_unavailable(item) for item in raw) else "zero"
        return "available" if usable else "zero"

    source_states = {
        "Action_Plans": state(action_sheet, raw_actions, action_records),
        "Adoption_Barriers": state(barrier_sheet, raw_barriers, barriers),
        "Customer_Pulse": state(pulse_sheet, raw_pulse, pulse),
        "Success_Priorities": state(priority_sheet, raw_priorities, priorities),
        "TAC_Cases": state(tac_sheet, raw_tac, tac_cases),
    }

    by_customer_actions: dict[str, list[dict[str, Any]]] = {}
    for item in action_plans:
        if item["customer_key"]:
            by_customer_actions.setdefault(item["customer_key"], []).append(item)
    tac_by_customer: dict[str, int] = {}
    for record in tac_cases:
        customer = _text(_first(record, ("Customer", "Customer Name", "Customer_Name", "BU_NAME")), 240)
        if customer:
            tac_by_customer[customer.casefold()] = tac_by_customer.get(customer.casefold(), 0) + 1
    for account in accounts:
        related_actions = by_customer_actions.get(account["customer_key"], [])
        if account["open_action_plans"] is None:
            account["open_action_plans"] = sum(
                not _is_completed_status(item.get("status")) for item in related_actions
            )
        if account["overdue_action_plans"] is None:
            account["overdue_action_plans"] = sum(bool(item.get("is_overdue")) for item in related_actions)
        if account["tac_cases"] is None:
            account["tac_cases"] = tac_by_customer.get(account["customer_key"], 0)
        account["source_sheet"] = risk_sheet
    accounts.sort(
        key=lambda item: (-(float(item["risk_score_0_100"] or 0)), item["customer"].casefold())
    )

    dashboard_sheet, dashboard_rows = first_sheet(("Executive_Dashboard", "Summary", "Key_Metrics"))
    dashboard_values: dict[str, object] = {}
    for record in dashboard_rows:
        label = _first(record, ("Metric", "Item", "Name"))
        if _text(label):
            dashboard_values[_key(label)] = _first(record, ("Value", "Metric_Value"))

    customers = {account["customer_key"] for account in accounts if account["customer_key"]}
    for collection in (action_records, barriers, tac_cases, pulse, priorities):
        for record in collection:
            customer = _text(
                _first(record, ("Customer", "Customer Name", "Customer_Name", "BU_NAME", "RELATED_CUSTOMER__C")),
                240,
            )
            if customer:
                customers.add(customer.casefold())
    customer_count: object = len(customers) if customers else (
        dashboard_values.get("total_customers_analyzed")
        or dashboard_values.get("customers")
        or dashboard_values.get("customer_count")
    )
    open_actions = [item for item in action_plans if not _is_completed_status(item.get("status"))]
    overdue_actions = [item for item in open_actions if item.get("is_overdue")]
    high_risk = [
        item for item in accounts
        if _key(item.get("risk_band")) in {"critical", "high"}
    ]
    def count_when_known(count: int, source_state: str) -> int | None:
        return count if source_state in {"available", "zero", "partial", "stale"} else None

    metric_inputs = (
        ("kpi.customers", "Customers", customer_count, "records", "available" if customers else "unknown", risk_sheet or dashboard_sheet),
        ("kpi.action_plans_total", "Action Plans", count_when_known(len(action_plans), source_states["Action_Plans"]), "records", source_states["Action_Plans"], action_sheet),
        ("kpi.action_plans_open", "Open Action Plans", count_when_known(len(open_actions), source_states["Action_Plans"]), "records", source_states["Action_Plans"], action_sheet),
        ("kpi.action_plans_overdue", "Overdue Action Plans", count_when_known(len(overdue_actions), source_states["Action_Plans"]), "records", source_states["Action_Plans"], action_sheet),
        ("kpi.adoption_barriers", "Adoption Barriers", count_when_known(len(barriers), source_states["Adoption_Barriers"]), "records", source_states["Adoption_Barriers"], barrier_sheet),
        ("kpi.tac_cases", "TAC Cases", count_when_known(len(tac_cases), source_states["TAC_Cases"]), "records", source_states["TAC_Cases"], tac_sheet),
        ("kpi.high_risk_customers", "High-risk customers", len(high_risk) if accounts else None, "customers", "available" if accounts else "unknown", risk_sheet),
    )
    metrics = [
        metric
        for values in metric_inputs
        if (metric := _metric_projection(
            values[0], values[1], values[2], unit=values[3],
            source_state=values[4], source_sheet=values[5],
            provenance="compatibility projection from named Source Data sheets",
        )) is not None
    ]

    activity_points = [
        {"label": label, "value": count, "display_value": str(count), "source_state": source_states[source]}
        for label, count, source in (
            ("Action Plans", len(action_plans), "Action_Plans"),
            ("Adoption Barriers", len(barriers), "Adoption_Barriers"),
            ("Customer Pulse", len(pulse), "Customer_Pulse"),
            ("TAC Cases", len(tac_cases), "TAC_Cases"),
        )
        if source_states[source] not in {"unknown", "unavailable", "failed"}
    ]
    action_buckets = [
        ("Open", sum(not item.get("is_overdue") for item in open_actions)),
        ("Overdue", len(overdue_actions)),
        ("Completed", sum(_is_completed_status(item.get("status")) for item in action_plans)),
    ]
    risk_bands: dict[str, int] = {}
    for account in accounts:
        label = _text(account.get("risk_band"), 80).upper() or "UNKNOWN"
        risk_bands[label] = risk_bands.get(label, 0) + 1
    charts: list[dict[str, Any]] = []
    if activity_points:
        charts.append({
            "chart_key": "activity_mix",
            "label": _CHART_METADATA["activity_mix"][0],
            "description": _CHART_METADATA["activity_mix"][1],
            "unit": "records",
            "source_state": _aggregate_source_state(point["source_state"] for point in activity_points),
            "series": activity_points,
            "provenance": "compatibility projection from named Source Data sheets",
        })
    if action_sheet and source_states["Action_Plans"] not in {"unavailable", "failed"}:
        charts.append({
            "chart_key": "action_plan_status_aging",
            "label": _CHART_METADATA["action_plan_status_aging"][0],
            "description": _CHART_METADATA["action_plan_status_aging"][1],
            "unit": "records",
            "source_state": source_states["Action_Plans"],
            "series": [
                {"label": label, "value": value, "display_value": str(value), "source_state": source_states["Action_Plans"]}
                for label, value in action_buckets
            ],
            "provenance": "compatibility projection from Action Plan rows",
        })
    if risk_bands:
        charts.append({
            "chart_key": "risk_distribution",
            "label": _CHART_METADATA["risk_distribution"][0],
            "description": _CHART_METADATA["risk_distribution"][1],
            "unit": "customers",
            "source_state": "available",
            "series": [
                {"label": label.title(), "value": value, "display_value": str(value), "source_state": "available"}
                for label, value in sorted(risk_bands.items())
            ],
            "provenance": f"compatibility projection from {risk_sheet}",
        })

    customer_name = _info_value(info, ("Customer Name", "Customer_Name"))
    subscription_id = _info_value(info, ("Subscription ID", "Subscription_Id"))
    scope_type = "subscription" if subscription_id else (
        "customer"
        if report_type == "renewal" and customer_name and "portfolio" not in customer_name.casefold()
        else "team"
    )
    scope_value = subscription_id if subscription_id else (customer_name if scope_type == "customer" else "")
    days = _number(_info_value(info, ("Days", "Analysis Period (Days)", "Analysis_Period")))
    return {
        "report_type": report_type,
        "manager": _info_value(info, ("Manager",)),
        "technology": _info_value(info, ("Technology",)),
        "scope_type": scope_type,
        "scope_value": scope_value,
        "days": days,
        "data_as_of_utc": as_of_utc,
        "metrics": sorted(metrics, key=lambda item: item["metric_key"]),
        "source_states": dict(sorted(source_states.items())),
        "action_plans": action_plans,
        "accounts": accounts,
        "charts": charts,
        "canonical_snapshot": False,
        "snapshot_contract": "legacy-compatibility-projection/v1",
        "compatibility_source_sheets": sorted(cache),
    }


def _canonical_workbook_projection(
    *,
    info: Mapping[str, Any],
    read: Any,
) -> dict[str, Any]:
    lineage_records = read("Metric_Lineage", 10_000)
    metrics: list[dict[str, Any]] = []
    for record in lineage_records:
        metric_key = _text(record.get("Metric_Key"), 300)
        if not metric_key.startswith("kpi."):
            continue
        metrics.append({
            "metric_key": metric_key,
            "label": _text(record.get("Display_Label"), 240) or metric_key,
            "value": _number(record.get("Metric_Value")),
            "display_value": _text(record.get("Metric_Value"), 120),
            "unit": _text(record.get("Unit"), 80),
            "source_state": _text(record.get("Source_State"), 80).casefold() or "unknown",
            "source_sheet": _text(record.get("Source_Sheet"), 300),
            "provenance": "canonical Metric_Lineage",
        })
    metrics.sort(key=lambda item: item["metric_key"])

    source_states = {
        key.split(":", 1)[1]: value.casefold()
        for key, value in info.items()
        if key.startswith("Source_State:")
    }
    if not source_states:
        for metric in metrics:
            for source_sheet in metric["source_sheet"].split(";"):
                source_sheet = source_sheet.strip()
                if source_sheet and source_sheet not in source_states:
                    source_states[source_sheet] = metric["source_state"]

    as_of_utc = _info_value(info, ("Data_As_Of_UTC",))
    action_records = read("Action_Plans", MAX_SHEET_ROWS)
    action_plans = [
        _action_plan_projection(record, index, as_of_utc=as_of_utc)
        for index, record in enumerate(action_records, 2)
        if not _as_bool(record.get("_adoptiq_provenance_row"))
        and _text(record.get("AdoptIQ_Status")).upper() != "EMPTY"
    ]
    action_plans.sort(
        key=lambda item: (
            not item["is_overdue"],
            item["due_date"] or "9999-12-31",
            item["customer"].casefold(),
            item["record_key"],
        )
    )

    account_records = read("Account_Summary", MAX_SHEET_ROWS)
    accounts = [_risk_projection(record) for record in account_records]
    accounts = [item for item in accounts if item["customer"]]
    accounts.sort(
        key=lambda item: (
            -(float(item["risk_score_0_100"] or 0)),
            item["customer"].casefold(),
        )
    )
    scope_type = _info_value(info, ("Scope_Type",)) or "team"
    scope_value = _info_value(info, ("Scope_Value",))
    email_match = _EMAIL_RE.search(scope_value) if _key(scope_type) == "member" else None
    return {
        "report_type": _info_value(info, ("Report_Type",)).casefold(),
        "manager": _info_value(info, ("Manager",)),
        "technology": _info_value(info, ("Technology",)),
        "scope_type": scope_type.casefold(),
        "scope_value": scope_value,
        "scope_member": email_match.group("email").casefold() if email_match else "",
        "days": _number(_info_value(info, ("Days",))),
        "data_as_of_utc": as_of_utc,
        "metrics": metrics,
        "source_states": dict(sorted(source_states.items())),
        "action_plans": action_plans,
        "accounts": accounts,
        "charts": _chart_projection(read("Chart_Data", MAX_SHEET_ROWS)),
        "canonical_snapshot": True,
        "snapshot_contract": "canonical-source-data/v1",
    }


def _load_workbook_snapshot(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    del mtime_ns
    path = Path(path_text)
    if size > MAX_WORKBOOK_BYTES:
        raise ValueError("Source Data workbook exceeds the workspace size limit.")
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        sheet_names = list(workbook.sheetnames)
        shapes: dict[str, dict[str, int]] = {}
        formulas = 0

        record_cache: dict[str, list[dict[str, Any]]] = {}

        def read(name: str, limit: int = MAX_SHEET_ROWS) -> list[dict[str, Any]]:
            nonlocal formulas
            if name not in workbook.sheetnames:
                return []
            if name not in record_cache:
                records, total_rows, formula_count = _sheet_records(
                    workbook[name], limit=limit
                )
                formulas += formula_count
                shapes[name] = {
                    "rows": total_rows,
                    "columns": int(getattr(workbook[name], "max_column", 0) or 0),
                }
                record_cache[name] = records
            return record_cache[name][:limit]

        info_records = read("Report_Info", 2_000)
        info: dict[str, str] = {}
        for record in info_records:
            item = _text(_first(record, ("Item", "Field", "Key")), 300)
            if item:
                info[item] = _text(_first(record, ("Value", "Metric_Value")), 4_000)

        canonical = all(
            name in sheet_names
            for name in ("Report_Info", "Metric_Lineage", "Chart_Data", "Action_Plans", "Account_Summary")
        ) and bool(info.get("Fact_Contract_SHA256"))
        public_core = (
            _canonical_workbook_projection(info=info, read=read)
            if canonical
            else _legacy_workbook_projection(
                info=info,
                sheet_names=sheet_names,
                read=read,
            )
        )
        # Inspect formula cells in every remaining sheet. Formula-free output
        # is a canonical Source Data requirement and must not depend on which
        # cards happened to be rendered in the workspace.
        for sheet_name in sheet_names:
            if sheet_name not in record_cache:
                read(sheet_name, 0)

        warning_inputs: list[object] = []
        for record in info_records:
            item = _text(_first(record, ("Item", "Field", "Key")), 300)
            item_key = _key(item)
            if item_key == "partial_data_warning" or re.fullmatch(
                r"partial_data_warning_\d+", item_key
            ):
                warning_value = _first(record, ("Value", "Metric_Value"))
                warning_detail = _first(record, ("Detail", "Description"))
                warning_inputs.append(
                    {"source": warning_value, "detail": warning_detail}
                    if _text(warning_detail)
                    else warning_value
                )
        if formulas:
            warning_inputs.append("formula")
        warnings = _source_warnings(warning_inputs)
        if not canonical:
            compatibility_copy = (
                "This older workbook is shown through a compatibility projection; "
                "generate a new report before using canonical comparison."
            )
            if compatibility_copy not in warnings:
                warnings.append(compatibility_copy)
        fingerprint = info.get("Fact_Contract_SHA256") or _stable_fingerprint(public_core)
        action_plans = list(public_core.get("action_plans") or [])
        return {
            "schema": WORKSPACE_SCHEMA,
            **public_core,
            "fact_fingerprint": fingerprint,
            "workbook_sha256": _file_sha256(path),
            "sheet_names": sheet_names,
            "sheet_shapes": shapes,
            "formula_cells": formulas,
            "warnings": warnings,
            "source_warnings": warnings,
            "action_plan_count": len(action_plans),
            "missing_action_plan_id_count": sum(1 for item in action_plans if item["record_id_quality"] == "missing"),
        }
    finally:
        workbook.close()


@lru_cache(maxsize=64)
def _cached_snapshot(path_text: str, mtime_ns: int, size: int) -> dict[str, Any]:
    return _load_workbook_snapshot(path_text, mtime_ns, size)


def load_workbook_snapshot(path: str | Path) -> dict[str, Any]:
    """Read a bounded Source Data snapshot, cached by path/mtime/size."""

    resolved = Path(path).expanduser().resolve()
    if resolved.suffix.casefold() != ".xlsx" or not resolved.is_file():
        raise ValueError("A readable Source Data .xlsx file is required.")
    stat = resolved.stat()
    return copy.deepcopy(_cached_snapshot(str(resolved), stat.st_mtime_ns, stat.st_size))


def snapshot_from_status(status: Mapping[str, Any], workbook_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Merge public run status with its canonical workbook snapshot."""

    snapshot = dict(workbook_snapshot or {})
    workbook_scope_value = _text(snapshot.get("scope_value"), 300)
    # Status is the server-canonical request envelope.  Workbook Scope_Value is
    # intentionally presentation-friendly for some report writers (for
    # example, a Leader member's display name), so it must not replace the
    # authorization identifier used by report-bound Ask AI.  Subscription
    # reports historically omitted Scope_Type and rendered a customer label in
    # Scope_Value; the persisted subscription_id is the only canonical value.
    report_type = _text(
        status.get("report_type") or snapshot.get("report_type"), 80
    ).casefold()
    scope_type = _text(
        status.get("scope_type")
        or (
            "subscription"
            if status.get("subscription_id")
            else snapshot.get("scope_type")
            or (
                "subscription"
                if report_type == "subscription"
                else "customer" if status.get("customer_name") else "team"
            )
        ),
        80,
    ).casefold()
    if scope_type == "subscription":
        scope_value = _text(
            status.get("subscription_id")
            or status.get("scope_value")
            or snapshot.get("scope_value"),
            300,
        )
    else:
        scope_value = _text(
            status.get("scope_value")
            or snapshot.get("scope_value")
            or status.get("customer_name"),
            300,
        )
    scope_member = _text(
        status.get("scope_member")
        or snapshot.get("scope_member")
        or status.get("member_email"),
        320,
    )
    binding_available = True
    binding_notice = ""
    status_has_canonical_scope = bool(status.get("scope_type")) and (
        scope_type == "team"
        or bool(status.get("scope_value") or status.get("subscription_id"))
    )
    if report_type == "leader" and scope_type == "member" and not status_has_canonical_scope:
        email_match = _EMAIL_RE.search(scope_value)
        if email_match:
            scope_value = email_match.group("email").casefold()
            scope_member = scope_value
        else:
            binding_available = False
    elif report_type == "leader" and scope_type == "customer" and not status_has_canonical_scope:
        # Historical Leader customer workbooks may store a presentation label
        # such as "Customer (Member Name)". That composite label is not an
        # authorization identifier, so old rows fail closed instead of
        # widening or querying an unverified customer scope.
        if re.search(r"\s+\([^()]+\)\s*$", scope_value):
            binding_available = False
    if not binding_available:
        binding_notice = (
            "Ask AI is unavailable for this older scoped report because its "
            "canonical authorization scope was not retained. Generate a new report to ask scoped questions."
        )
    snapshot.update({
        "schema": WORKSPACE_SCHEMA,
        "analysis_id": _text(status.get("analysis_id"), 500),
        "status": _text(status.get("status") or "unknown", 80).casefold(),
        "report_type": report_type,
        "manager": _text(snapshot.get("manager") or status.get("manager"), 240),
        "technology": _text(snapshot.get("technology") or status.get("technology") or status.get("tech"), 240),
        "scope_type": scope_type,
        "scope_value": scope_value,
        "scope_member": scope_member,
        "days": _number(snapshot.get("days") if snapshot.get("days") is not None else status.get("days")),
        "data_as_of_utc": _text(snapshot.get("data_as_of_utc") or status.get("data_retrieved_at") or status.get("data_as_of_utc"), 120),
        "started_at": _text(status.get("start_time") or status.get("started_at"), 120),
        "completed_at": _text(status.get("completion_time") or status.get("end_time"), 120),
        "word_available": bool(status.get("word_available") or status.get("word_report") or status.get("report_path")),
        "excel_available": bool(status.get("excel_available") or status.get("excel_report")),
        "ask_ai_binding_available": binding_available,
        "ask_ai_binding_notice": binding_notice,
    })
    snapshot["scope_label"] = _text(status.get("scope_display"), 300) or workbook_scope_value or snapshot["scope_value"] or (
        f"{snapshot['manager']} team" if snapshot["manager"] else "Portfolio"
    )
    snapshot["source_limitations"] = [
        {"source": _public_label(source), "state": state}
        for source, state in sorted((snapshot.get("source_states") or {}).items())
        if state not in {"available", "zero"}
    ]
    source_warnings: list[str] = []
    seen_warnings: set[str] = set()
    for warning in (
        [_text(item, 1_000) for item in snapshot.get("source_warnings") or []]
        + _source_warnings(status.get("partial_data_warnings") or [])
    ):
        warning_key = warning.casefold()
        if warning and warning_key not in seen_warnings:
            source_warnings.append(warning)
            seen_warnings.add(warning_key)
    snapshot["source_warnings"] = source_warnings
    if binding_notice:
        snapshot["source_warnings"].append(binding_notice)
    snapshot["decision_metrics"] = [
        item for item in snapshot.get("metrics", [])
        if item.get("metric_key") in {
            "kpi.customers", "kpi.team_members", "kpi.action_plans_total", "kpi.action_plans_open",
            "kpi.action_plans_overdue", "kpi.action_plans_due_soon",
            "kpi.adoption_barriers", "kpi.tac_cases", "kpi.bems",
            "kpi.high_risk_customers",
        }
    ]
    snapshot["top_action_plans"] = list(snapshot.get("action_plans") or [])[:20]
    snapshot["top_accounts"] = list(snapshot.get("accounts") or [])[:12]
    return snapshot


def _ap_index(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        _text(item.get("record_id")).casefold(): item
        for item in snapshot.get("action_plans", [])
        if isinstance(item, Mapping) and _text(item.get("record_id"))
    }


def _is_completed_status(value: object) -> bool:
    return _status_is_completed(value)


def _comparison_source_state(snapshot: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """Return the least-comparable state declared for one metric's lineage."""

    declared_state = _text(item.get("source_state"), 80).casefold() or "unknown"
    source_states = {
        _key(source): _text(state, 80).casefold() or "unknown"
        for source, state in (snapshot.get("source_states") or {}).items()
    }
    lineage_states = [
        source_states[_key(source)]
        for source in _text(item.get("source_sheet"), 300).split(";")
        if _key(source) in source_states
    ]
    if declared_state != "unknown":
        lineage_states.append(declared_state)
    return _aggregate_source_state(lineage_states) if lineage_states else declared_state


def _action_plan_source_state(snapshot: Mapping[str, Any]) -> str:
    """Resolve Action Plan coverage without inferring availability from row presence."""

    states = [
        _text(state, 80).casefold() or "unknown"
        for source, state in (snapshot.get("source_states") or {}).items()
        if _key(source) in {"action_plans", "customer_action_plans"}
    ]
    return _aggregate_source_state(states) if states else "unknown"


def compare_snapshots(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two canonical report snapshots with source-change separation."""

    before_metrics = {item["metric_key"]: item for item in before.get("metrics", []) if isinstance(item, Mapping) and item.get("metric_key")}
    after_metrics = {item["metric_key"]: item for item in after.get("metrics", []) if isinstance(item, Mapping) and item.get("metric_key")}
    metric_changes: list[dict[str, Any]] = []
    source_driven_metric_changes: list[dict[str, Any]] = []
    for metric_key in sorted(set(before_metrics) | set(after_metrics)):
        old = before_metrics.get(metric_key, {})
        new = after_metrics.get(metric_key, {})
        old_value = _number(old.get("value"))
        new_value = _number(new.get("value"))
        if old_value == new_value:
            continue
        before_source_state = _comparison_source_state(before, old)
        after_source_state = _comparison_source_state(after, new)
        old_source_sheet = _text(old.get("source_sheet"), 300)
        new_source_sheet = _text(new.get("source_sheet"), 300)
        change = {
            "metric_key": metric_key,
            "label": _text(new.get("label") or old.get("label") or metric_key, 240),
            "before": old_value,
            "after": new_value,
            "delta": (new_value - old_value) if old_value is not None and new_value is not None else None,
            "source_sheet": new_source_sheet or old_source_sheet,
            "before_source_state": before_source_state,
            "after_source_state": after_source_state,
        }
        comparable = (
            bool(old)
            and bool(new)
            and before_source_state in _COMPARABLE_SOURCE_STATES
            and after_source_state in _COMPARABLE_SOURCE_STATES
            and _key(old_source_sheet) == _key(new_source_sheet)
        )
        if comparable:
            metric_changes.append(change)
        else:
            change["classification"] = "source_availability"
            source_driven_metric_changes.append(change)

    before_states = dict(before.get("source_states") or {})
    after_states = dict(after.get("source_states") or {})
    source_changes = [
        {"source": source, "before": before_states.get(source, "unknown"), "after": after_states.get(source, "unknown")}
        for source in sorted(set(before_states) | set(after_states))
        if before_states.get(source, "unknown") != after_states.get(source, "unknown")
    ]

    old_aps = _ap_index(before)
    new_aps = _ap_index(after)
    detected_action_changes: list[dict[str, Any]] = []
    for record_key in sorted(set(old_aps) | set(new_aps)):
        old = old_aps.get(record_key)
        new = new_aps.get(record_key)
        if old is None and new is not None:
            detected_action_changes.append({"record_id": new["record_id"], "change": "new", "customer": new.get("customer"), "title": new.get("title")})
            continue
        if new is None and old is not None:
            detected_action_changes.append({"record_id": old["record_id"], "change": "absent", "customer": old.get("customer"), "title": old.get("title")})
            continue
        assert old is not None and new is not None
        if not _is_completed_status(old.get("status")) and _is_completed_status(new.get("status")):
            detected_action_changes.append({"record_id": new["record_id"], "change": "completed", "before": old.get("status"), "after": new.get("status")})
        elif _is_completed_status(old.get("status")) and not _is_completed_status(new.get("status")):
            detected_action_changes.append({"record_id": new["record_id"], "change": "reopened", "before": old.get("status"), "after": new.get("status")})
        if not old.get("is_overdue") and new.get("is_overdue"):
            detected_action_changes.append({"record_id": new["record_id"], "change": "became_overdue", "before": old.get("due_date"), "after": new.get("due_date")})
        for field, change_name in (("owner", "owner_changed"), ("due_date", "due_date_changed"), ("status", "status_changed")):
            if _text(old.get(field)) != _text(new.get(field)):
                detected_action_changes.append({"record_id": new["record_id"], "change": change_name, "before": old.get(field), "after": new.get(field)})

    before_action_state = _action_plan_source_state(before)
    after_action_state = _action_plan_source_state(after)
    action_sources_comparable = (
        before_action_state in _COMPARABLE_SOURCE_STATES
        and after_action_state in _COMPARABLE_SOURCE_STATES
    )
    if action_sources_comparable:
        action_changes = detected_action_changes
        source_driven_action_changes: list[dict[str, Any]] = []
    else:
        action_changes = []
        source_driven_action_changes = [
            {
                **change,
                "classification": "source_availability",
                "before_source_state": before_action_state,
                "after_source_state": after_action_state,
            }
            for change in detected_action_changes
        ]

    same_scope = all(
        _text(before.get(field)).casefold() == _text(after.get(field)).casefold()
        for field in ("manager", "technology", "scope_type", "scope_value")
    ) and _number(before.get("days")) == _number(after.get("days"))
    caveats = []
    if not same_scope:
        caveats.append("The selected reports do not have the same manager, technology, scope, and window.")
    if source_changes:
        caveats.append("Source coverage changed; non-comparable KPI and Action Plan differences are excluded from business movement.")
    if source_driven_metric_changes or source_driven_action_changes:
        caveats.append(
            f"Excluded {len(source_driven_metric_changes)} KPI and "
            f"{len(source_driven_action_changes)} Action Plan difference(s) because source coverage was not comparable."
        )
    if before.get("missing_action_plan_id_count") or after.get("missing_action_plan_id_count"):
        caveats.append("Action Plans without stable IDs are retained in each artifact but cannot be compared reliably across runs.")
    return {
        "schema": WORKSPACE_SCHEMA,
        "same_scope": same_scope,
        "before_fingerprint": _text(before.get("fact_fingerprint"), 128),
        "after_fingerprint": _text(after.get("fact_fingerprint"), 128),
        "metric_changes": metric_changes[:MAX_PUBLIC_ITEMS],
        "action_plan_changes": action_changes[:MAX_PUBLIC_ITEMS],
        "source_driven_metric_changes": source_driven_metric_changes[:MAX_PUBLIC_ITEMS],
        "source_driven_action_plan_changes": source_driven_action_changes[:MAX_PUBLIC_ITEMS],
        "source_state_changes": source_changes,
        "business_change_count": len(metric_changes) + len(action_changes),
        "source_driven_change_count": len(source_driven_metric_changes) + len(source_driven_action_changes),
        "source_change_count": len(source_changes),
        "caveats": caveats,
    }


def normalize_history_record(record: Mapping[str, Any]) -> dict[str, Any]:
    report_type = _text(record.get("report_type"), 80).casefold()
    return {
        "analysis_id": _text(record.get("request_id") or record.get("analysis_id"), 500),
        "report_type": report_type,
        "report_label": REPORT_TYPES.get(report_type, {}).get("label", report_type.replace("_", " ").title() or "Report"),
        "manager": _text(record.get("manager"), 240),
        "technology": _text(record.get("technology") or record.get("tech"), 240),
        "customer": _text(record.get("customer_name") or record.get("scope_value"), 300),
        "scope_type": _text(record.get("scope_type") or ("customer" if record.get("customer_name") else "team"), 80).casefold(),
        "days": _number(record.get("days")),
        "status": _text(record.get("status"), 80).casefold(),
        "started_at": _text(record.get("start_time") or record.get("started_at"), 120),
        "completed_at": _text(record.get("end_time") or record.get("completion_time") or record.get("created_at"), 120),
        "word_available": bool(record.get("word_path") or record.get("word_report") or record.get("report_path")),
        "excel_available": bool(record.get("excel_path") or record.get("excel_report")),
        "is_completed": _text(record.get("status")).casefold() in _TERMINAL_STATUSES,
    }


def filter_history(
    records: Iterable[Mapping[str, Any]],
    *,
    manager: str = "",
    report_type: str = "",
    scope_type: str = "",
    customer: str = "",
    technology: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    """Normalize and filter history without exposing filesystem paths."""

    wanted = {
        "manager": _text(manager).casefold(),
        "report_type": _text(report_type).casefold(),
        "scope_type": _text(scope_type).casefold(),
        "customer": _text(customer).casefold(),
        "technology": _text(technology).casefold(),
        "status": _text(status).casefold(),
    }
    normalized = [normalize_history_record(record) for record in records if not record.get("_placeholder")]
    result = []
    for record in normalized:
        if wanted["manager"] and record["manager"].casefold() != wanted["manager"]:
            continue
        if wanted["report_type"] and record["report_type"] != wanted["report_type"]:
            continue
        if wanted["scope_type"] and record["scope_type"] != wanted["scope_type"]:
            continue
        if wanted["customer"] and wanted["customer"] not in record["customer"].casefold():
            continue
        if wanted["technology"] and record["technology"].casefold() != wanted["technology"]:
            continue
        if wanted["status"] and record["status"] != wanted["status"]:
            continue
        result.append(record)
    result.sort(key=lambda item: (item["completed_at"], item["analysis_id"]), reverse=True)
    return result


def ask_ai_binding(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable public context that binds Ask AI to one report."""

    binding_available = snapshot.get("ask_ai_binding_available") is not False
    return {
        "analysis_id": _text(snapshot.get("analysis_id"), 500),
        "report_type": _text(snapshot.get("report_type"), 80),
        "manager": _text(snapshot.get("manager"), 240),
        "technology": _text(snapshot.get("technology"), 240),
        "days": _number(snapshot.get("days")),
        "scope_type": _text(snapshot.get("scope_type") or "team", 80),
        "scope_value": _text(snapshot.get("scope_value"), 300) if binding_available else "",
        "scope_member": _text(snapshot.get("scope_member"), 320) if binding_available else "",
        "data_as_of_utc": _text(snapshot.get("data_as_of_utc"), 120),
        "fact_fingerprint": _text(snapshot.get("fact_fingerprint"), 128),
        "source_states": dict(sorted((snapshot.get("source_states") or {}).items())),
        "decision_metrics": list(snapshot.get("decision_metrics") or []),
        "binding_available": binding_available,
        "binding_notice": _text(snapshot.get("ask_ai_binding_notice"), 500),
        "live_validation_performed": None,
    }
