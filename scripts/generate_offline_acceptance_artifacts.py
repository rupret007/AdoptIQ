#!/usr/bin/env python3
"""Round 142: generate deterministic offline decision-report artifacts.

The harness intentionally imports only the public ``decision_report_delivery``
surface.  It never imports the Flask app, report routes, or Snowflake clients.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "report_acceptance" / "v1" / "sanitized_portfolio.json"
)
SUPPORTED_SCOPES = ("team", "member", "customer", "comprehensive")
SOURCE_KEYS = (
    "subscriptions",
    "action_plans",
    "adoption_barriers",
    "customer_pulse",
    "tac_cases",
    "success_priorities",
)
OFFLINE_SOURCE_SYSTEM = "Offline sanitized fixture — live Snowflake/CSOne validation deferred"
OFFLINE_SOURCE_DETAIL = (
    "Sanitized deterministic fixture data; no live Snowflake, CSConsole, or CSOne "
    "validation was performed in this environment."
)
_CUSTOMER_COLUMNS = (
    "BU_NAME",
    "Customer Name",
    "customer_name",
    "Customer",
    "ACCOUNT_NAME",
    "Account",
    "RELATED_CUSTOMER__C",
    "CUSTOMER_BU_NAME__C",
)
_ACCOUNT_COLUMNS = ("ACCOUNT_ID_C", "Account ID", "ACCOUNT__C", "account_id")
def _ensure_repo_root_on_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def load_sanitized_fixture(path: Path = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    """Load and minimally validate the versioned, synthetic acceptance data."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("fixture_version") != "report_acceptance/v1":
        raise ValueError("unsupported acceptance fixture version")
    if payload.get("sanitized") is not True:
        raise ValueError("offline acceptance fixture must declare sanitized=true")
    team_data = payload.get("team_data")
    if not isinstance(team_data, dict) or len(team_data) != 2:
        raise ValueError("offline acceptance fixture must contain exactly two team members")
    expected_by_scope = payload.get("expected_by_scope")
    if not isinstance(expected_by_scope, dict) or set(expected_by_scope) != set(
        SUPPORTED_SCOPES
    ):
        raise ValueError("offline acceptance fixture must define an oracle for every scope")
    return payload


def _frames_from_payload(payload: Mapping[str, Any]) -> dict[str, dict[str, pd.DataFrame]]:
    team_data: dict[str, dict[str, pd.DataFrame]] = {}
    for member, raw_bundle in sorted((payload.get("team_data") or {}).items()):
        bundle = raw_bundle if isinstance(raw_bundle, Mapping) else {}
        team_data[str(member)] = {
            key: pd.DataFrame(list(bundle.get(key) or []))
            for key in SOURCE_KEYS
        }
    return team_data


def _stamp_offline_fixture_sources(
    team_data: Mapping[str, Mapping[str, pd.DataFrame]],
) -> dict[str, dict[str, pd.DataFrame]]:
    """Make fixture provenance and deferred live acceptance explicit."""

    stamped: dict[str, dict[str, pd.DataFrame]] = {}
    for member, bundle in team_data.items():
        stamped[str(member)] = {}
        for key, raw in bundle.items():
            frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
            frame["Source_System"] = OFFLINE_SOURCE_SYSTEM
            frame.attrs.update(dict(getattr(raw, "attrs", {}) or {}))
            frame.attrs.update(
                {
                    "partial": True,
                    "source_mode": "offline_fixture",
                    "source_mode_detail": OFFLINE_SOURCE_DETAIL,
                }
            )
            stamped[str(member)][str(key)] = frame
    return stamped


def _normalized_token(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _customer_filter(
    frame: pd.DataFrame,
    *,
    customer: str,
    account_ids: set[str],
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    customer_token = _normalized_token(customer)
    mask = pd.Series(False, index=frame.index)
    for column in _CUSTOMER_COLUMNS:
        if column in frame.columns:
            mask = mask | frame[column].map(_normalized_token).eq(customer_token)
    for column in _ACCOUNT_COLUMNS:
        if column in frame.columns:
            mask = mask | frame[column].fillna("").astype(str).str.strip().isin(account_ids)
    return frame.loc[mask].copy().reset_index(drop=True)


def _comprehensive_team_data(
    team_data: Mapping[str, Mapping[str, pd.DataFrame]],
    delivery: Any,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Exercise the public comprehensive portfolio-to-member partition seam."""

    subscription_parts: list[pd.DataFrame] = []
    source_parts: dict[str, list[pd.DataFrame]] = {
        key: [] for key in SOURCE_KEYS if key != "subscriptions"
    }
    for member, bundle in sorted(team_data.items()):
        subscriptions = bundle.get("subscriptions", pd.DataFrame()).copy()
        if not subscriptions.empty:
            subscriptions["CSSM"] = str(member)
            subscription_parts.append(subscriptions)
        for key in source_parts:
            frame = bundle.get(key, pd.DataFrame())
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                source = frame.copy()
                source["CSSM"] = str(member)
                source_parts[key].append(source)

    subscriptions = (
        pd.concat(subscription_parts, ignore_index=True, sort=False)
        if subscription_parts
        else pd.DataFrame()
    )
    portfolio: dict[str, pd.DataFrame] = {}
    for key, parts in source_parts.items():
        portfolio[key] = (
            pd.concat(parts, ignore_index=True, sort=False)
            if parts
            else pd.DataFrame()
        )

    return delivery.partition_portfolio_by_member(
        subscriptions=subscriptions,
        action_plans=portfolio["action_plans"],
        adoption_barriers=portfolio["adoption_barriers"],
        customer_pulse=portfolio["customer_pulse"],
        tac_cases=portfolio["tac_cases"],
        success_priorities=portfolio["success_priorities"],
        fallback_member="Portfolio",
    )


def build_scope_fixture(
    payload: Mapping[str, Any],
    requested_scope: str,
    delivery: Any,
) -> tuple[dict[str, dict[str, pd.DataFrame]], dict[str, str]]:
    """Return scoped team bundles plus the public report/scope labels."""

    if requested_scope not in SUPPORTED_SCOPES:
        raise ValueError(f"unsupported scope: {requested_scope}")
    team_data = _frames_from_payload(payload)
    manager = str(payload["manager_name"])
    defaults = payload.get("defaults") or {}

    if requested_scope == "team":
        return team_data, {
            "report_type": "Leader",
            "scope_type": "team",
            "scope_value": f"{manager} team",
        }
    if requested_scope == "member":
        member = str(defaults.get("member") or sorted(team_data)[0])
        if member not in team_data:
            raise ValueError(f"fixture default member is unavailable: {member}")
        return {member: team_data[member]}, {
            "report_type": "Leader",
            "scope_type": "member",
            "scope_value": member,
        }
    if requested_scope == "customer":
        customer = str(defaults.get("customer") or "")
        account_ids: set[str] = set()
        for bundle in team_data.values():
            subscriptions = bundle["subscriptions"]
            customer_mask = pd.Series(False, index=subscriptions.index)
            for column in _CUSTOMER_COLUMNS:
                if column in subscriptions.columns:
                    customer_mask = customer_mask | subscriptions[column].map(_normalized_token).eq(
                        _normalized_token(customer)
                    )
            for column in _ACCOUNT_COLUMNS:
                if column in subscriptions.columns:
                    account_ids.update(
                        subscriptions.loc[customer_mask, column]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .tolist()
                    )
        selected: dict[str, dict[str, pd.DataFrame]] = {}
        for member, bundle in team_data.items():
            scoped_bundle = {
                key: _customer_filter(frame, customer=customer, account_ids=account_ids)
                for key, frame in bundle.items()
            }
            if any(not frame.empty for frame in scoped_bundle.values()):
                selected[member] = scoped_bundle
        return selected, {
            "report_type": "Leader",
            "scope_type": "customer",
            "scope_value": customer,
        }

    return _comprehensive_team_data(team_data, delivery), {
        "report_type": "Comprehensive",
        "scope_type": "team",
        "scope_value": f"{manager} comprehensive portfolio",
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()
    if value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    normalized = _json_safe(payload)
    path.write_text(
        json.dumps(normalized, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_serialized_source_sheets(path: Path) -> dict[str, pd.DataFrame]:
    """Read back the delivered workbook so manifests describe shipped data."""

    with pd.ExcelFile(path) as workbook:
        return {
            str(sheet_name): workbook.parse(sheet_name=sheet_name)
            for sheet_name in workbook.sheet_names
        }


def _normalize_ooxml_archive(path: Path, *, as_of: pd.Timestamp) -> None:
    """Remove wall-clock ZIP/core metadata from a generated DOCX or XLSX."""

    timestamp = as_of.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ").encode("ascii")
    archive_stamp = (1980, 1, 1, 0, 0, 0)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            temporary_path,
            "w",
        ) as target:
            target.comment = source.comment
            for source_info in source.infolist():
                data = source.read(source_info.filename)
                if source_info.filename == "docProps/core.xml":
                    for tag in (b"created", b"modified"):
                        data = re.sub(
                            rb"(<dcterms:"
                            + tag
                            + rb"\b[^>]*>)[^<]*(</dcterms:"
                            + tag
                            + rb">)",
                            lambda match: match.group(1) + timestamp + match.group(2),
                            data,
                        )
                target_info = zipfile.ZipInfo(source_info.filename, archive_stamp)
                target_info.compress_type = source_info.compress_type
                target_info.comment = source_info.comment
                target_info.internal_attr = source_info.internal_attr
                target_info.external_attr = source_info.external_attr
                target_info.create_system = source_info.create_system
                target_info.flag_bits = source_info.flag_bits
                target.writestr(target_info, data)
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _artifact_token(scope: str, scope_value: str, as_of: pd.Timestamp) -> str:
    value_slug = re.sub(r"[^A-Za-z0-9]+", "_", scope_value).strip("_") or "Scope"
    timestamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    return f"{scope.title()}_{value_slug}_{timestamp}"


def _lifecycle_measurement(lifecycle: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "total",
        "open",
        "overdue",
        "due_soon",
        "open_other",
        "completed",
        "blocked_on_hold",
        "unknown",
        "missing_title",
        "missing_record_id",
        "bucket_counts",
        "field_selection",
        "as_of_utc",
        "due_soon_days",
        "deduplication_rule",
        "source_state",
        "source_state_detail",
    )
    return {key: lifecycle.get(key) for key in keys if key in lifecycle}


def _measurement_manifest(
    *,
    requested_scope: str,
    facts: Mapping[str, Any],
    serialized_sheets: Mapping[str, pd.DataFrame],
    contract: Mapping[str, Any],
    word_path: Path,
    workbook_path: Path,
) -> dict[str, Any]:
    word = contract.get("word") or {}
    return {
        "schema_version": "report_acceptance/v1",
        "requested_scope": requested_scope,
        "report_type": facts["report_type"],
        "scope_type": facts["scope_type"],
        "scope_value": facts["scope_value"],
        "as_of_utc": facts["as_of_utc"],
        "days": facts["days"],
        "artifacts": {
            "word": word_path.name,
            "source_data": workbook_path.name,
        },
        "kpis": dict(facts["kpis"]),
        "action_plan_lifecycle": _lifecycle_measurement(facts["action_plan_lifecycle"]),
        "document": {
            "word_count": word.get("word_count"),
            "word_budget": word.get("word_budget"),
            "embedded_chart_count": word.get("inline_shapes"),
        },
        "source_sheets": [
            {
                "sheet": name,
                "rows": int(len(frame)),
                "columns": int(len(frame.columns)),
            }
            for name, frame in serialized_sheets.items()
        ],
        "source_coverage": facts["source_coverage"].to_dict(orient="records"),
        "chart_series_count": int(len(facts["chart_data"])),
        "metric_lineage_count": int(len(facts["metric_lineage"])),
    }


def _parity_manifest(
    *,
    requested_scope: str,
    facts: Mapping[str, Any],
    sheets: Mapping[str, pd.DataFrame],
    serialized_sheets: Mapping[str, pd.DataFrame],
    contract: Mapping[str, Any],
    written_workbook_contract: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    lifecycle = facts["action_plan_lifecycle"]
    ap_chart = facts["chart_data"].loc[
        facts["chart_data"]["Chart_ID"] == "action_plan_status_aging"
    ]
    risk_chart = facts["chart_data"].loc[
        facts["chart_data"]["Chart_ID"] == "risk_distribution"
    ]
    shared_attribution_observed = bool(
        sheets["Action_Plans"]
        .get("Attributed_Team_Members", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.contains(";", regex=False)
        .any()
    )
    shared_attribution_expected = requested_scope != "member"
    serialized_action_plans = serialized_sheets.get("Action_Plans", pd.DataFrame())
    required_lifecycle_columns = {
        "Record_ID",
        "AdoptIQ_Title",
        "AdoptIQ_Status_Bucket",
        "AdoptIQ_Data_Quality",
        "Scope_Type",
        "Scope_Value",
        "Source_System",
        "Attributed_Team_Members",
    }
    internal_columns = {
        "ETL_ID",
        "IS_DELETED",
        "RECORD_TYPE_ID",
        "DELETE_FLAG",
        "CREATEDDATE",
        "_ATTRIBUTED_BY_ACCOUNT",
    }
    expected_kpis = dict(expected.get("kpis") or {})
    expected_lifecycle = dict(expected.get("action_plan_lifecycle") or {})
    expected_source_rows = dict(expected.get("source_rows") or {})
    expected_chart_totals = dict(expected.get("chart_totals") or {})
    expected_chart_values = dict(expected.get("chart_values") or {})
    chart_by_key = facts["chart_data"].set_index("Metric_Key")
    actual_oracle = {
        "kpis": {
            key: facts["kpis"].get(key)
            for key in expected_kpis
        },
        "action_plan_lifecycle": {
            key: lifecycle.get(key)
            for key in expected_lifecycle
        },
        "source_rows": {
            key: int(len(serialized_sheets.get(key, pd.DataFrame())))
            for key in expected_source_rows
        },
        "chart_totals": {
            key: int(
                facts["chart_data"]
                .loc[facts["chart_data"]["Chart_ID"] == key, "Value"]
                .fillna(0)
                .sum()
            )
            for key in expected_chart_totals
        },
        "chart_values": {
            key: int(chart_by_key.loc[key, "Value"])
            if key in chart_by_key.index and pd.notna(chart_by_key.loc[key, "Value"])
            else None
            for key in expected_chart_values
        },
    }
    checks = {
        "cross_artifact_contract": bool(contract.get("ok")),
        "written_workbook_contract": bool(written_workbook_contract.get("ok")),
        "written_workbook_has_no_formulas": (
            int(written_workbook_contract.get("formula_cells") or 0) == 0
        ),
        "action_plan_source_rows_equal_distinct_total": (
            int(len(sheets["Action_Plans"])) == int(lifecycle["total"])
        ),
        "action_plan_lifecycle_is_partition": (
            int(sum(lifecycle["bucket_counts"].values())) == int(lifecycle["total"])
        ),
        "action_plan_chart_equals_distinct_total": (
            int(ap_chart["Value"].fillna(0).sum()) == int(lifecycle["total"])
        ),
        "risk_chart_equals_scored_customers": (
            int(risk_chart["Value"].fillna(0).sum())
            == int(facts["risk_summary"].get("total_customers", 0))
        ),
        "missing_title_disclosed": int(lifecycle.get("missing_title", 0)) > 0,
        "missing_record_id_disclosed": int(lifecycle.get("missing_record_id", 0)) > 0,
        "shared_attribution_contract": (
            shared_attribution_observed == shared_attribution_expected
        ),
        "serialized_sheet_inventory_matches_prepared_inventory": (
            list(serialized_sheets) == list(sheets)
        ),
        "serialized_action_plan_rows_equal_distinct_total": (
            int(len(serialized_action_plans)) == int(lifecycle["total"])
        ),
        "serialized_action_plan_contract_columns_present": (
            required_lifecycle_columns.issubset(serialized_action_plans.columns)
        ),
        "serialized_workbook_denies_internal_columns": not any(
            internal_columns.intersection(frame.columns)
            for frame in serialized_sheets.values()
        ),
        "fixture_kpis_match_oracle": actual_oracle["kpis"] == expected_kpis,
        "fixture_action_plan_lifecycle_matches_oracle": (
            actual_oracle["action_plan_lifecycle"] == expected_lifecycle
        ),
        "fixture_source_rows_match_oracle": (
            actual_oracle["source_rows"] == expected_source_rows
        ),
        "fixture_chart_totals_match_oracle": (
            actual_oracle["chart_totals"] == expected_chart_totals
        ),
        "fixture_chart_values_match_oracle": (
            actual_oracle["chart_values"] == expected_chart_values
        ),
    }
    return {
        "schema_version": "report_acceptance/v1",
        "requested_scope": requested_scope,
        "ok": bool(contract.get("ok")) and all(checks.values()),
        "errors": list(contract.get("errors") or []),
        "checks": checks,
        "shared_attribution": {
            "expected": shared_attribution_expected,
            "observed": shared_attribution_observed,
        },
        "fixture_oracle": {
            "expected": dict(expected),
            "actual": actual_oracle,
        },
        "required_sheets": list(contract.get("required_sheets") or []),
        "chart_series_count": int(contract.get("chart_series_count") or 0),
        "lineage_count": int(contract.get("lineage_count") or 0),
        "written_workbook": {
            "ok": bool(written_workbook_contract.get("ok")),
            "formula_cells": int(written_workbook_contract.get("formula_cells") or 0),
            "sheet_names": list(written_workbook_contract.get("sheet_names") or []),
        },
    }


def _chart_manifest(
    *,
    requested_scope: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    chart_data = facts["chart_data"].copy()
    sort_columns = [
        column
        for column in ("Chart_ID", "Series", "Category", "Period_Start", "Metric_Key")
        if column in chart_data.columns
    ]
    if sort_columns:
        chart_data = chart_data.sort_values(sort_columns, kind="stable", na_position="last")
    groups = []
    for chart_id, group in chart_data.groupby("Chart_ID", sort=True):
        groups.append(
            {
                "chart_id": str(chart_id),
                "series_rows": int(len(group)),
                "available_value_rows": int(group["Value"].notna().sum()),
                "source_states": sorted(
                    set(group.get("Source_State", pd.Series(dtype=str)).dropna().astype(str))
                ),
            }
        )
    return {
        "schema_version": "report_acceptance/v1",
        "requested_scope": requested_scope,
        "as_of_utc": facts["as_of_utc"],
        "charts": groups,
        "series": chart_data.to_dict(orient="records"),
        "trend_coverage": facts["activity_trend"]["coverage"].to_dict(orient="records"),
    }


def generate_acceptance_artifacts(
    *,
    scope: str,
    as_of: str,
    output_dir: Path,
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
    manager_name: str = "",
    days: int | None = None,
) -> dict[str, Any]:
    """Generate one paired artifact set and its deterministic manifests."""

    _ensure_repo_root_on_path()
    import decision_report_delivery as delivery  # noqa: PLC0415

    as_of_ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(as_of_ts):
        raise ValueError("--as-of must be a valid timestamp")
    payload = dict(load_sanitized_fixture(fixture_path))
    # Round 143: the supported orchestrator requires and records the same
    # operator-selected manager/window in every artifact.  This relabels only
    # the sanitized fixture context; it does not change fixture records or
    # pretend that the selected manager's live portfolio was queried.
    if str(manager_name or "").strip():
        payload["manager_name"] = str(manager_name).strip()
    if days is not None:
        if not 1 <= int(days) <= 365:
            raise ValueError("days must be between 1 and 365")
        payload["days"] = int(days)
    team_data, labels = build_scope_fixture(payload, scope, delivery)
    if not team_data:
        raise ValueError(f"sanitized fixture produced no records for scope={scope}")
    team_data = _stamp_offline_fixture_sources(team_data)
    def _offline_external_frame(rows: Any) -> pd.DataFrame:
        frame = pd.DataFrame([dict(row) for row in rows or []])
        frame["Source_System"] = OFFLINE_SOURCE_SYSTEM
        frame.attrs.update(
            {
                "partial": True,
                "source_mode": "offline_fixture",
                "source_mode_detail": OFFLINE_SOURCE_DETAIL,
            }
        )
        return frame

    external_incidents = _offline_external_frame(payload.get("external_incidents"))
    external_bugs = _offline_external_frame(payload.get("external_bugs"))
    partial_data_warnings = list(payload.get("partial_data_warnings") or [])
    partial_data_warnings.append(
        {
            "dataset": "Live Snowflake/CSConsole/CSOne acceptance",
            "kind": "deferred",
            "effect": OFFLINE_SOURCE_DETAIL,
        }
    )

    facts = delivery.build_report_facts(
        team_data,
        report_type=labels["report_type"],
        scope_type=labels["scope_type"],
        scope_value=labels["scope_value"],
        manager_name=str(payload["manager_name"]),
        days=int(payload["days"]),
        as_of=as_of_ts,
        external_incidents=external_incidents,
        external_bugs=external_bugs,
        partial_data_warnings=partial_data_warnings,
    )
    sheets = delivery.build_source_data_sheets(facts)
    document = delivery.build_concise_word_document(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    if not contract.get("ok"):
        raise RuntimeError("offline acceptance parity failed: " + "; ".join(contract["errors"]))

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    token = _artifact_token(scope, labels["scope_value"], as_of_ts)
    word_path = output_dir / f"AdoptIQ_Report_{labels['report_type']}_{token}.docx"
    document.save(word_path)
    workbook_path = delivery.source_data_path_for_word(word_path)
    delivery.write_source_data_workbook(workbook_path, sheets)
    _normalize_ooxml_archive(word_path, as_of=as_of_ts)
    _normalize_ooxml_archive(workbook_path, as_of=as_of_ts)
    serialized_sheets = _read_serialized_source_sheets(workbook_path)
    written_workbook_contract = delivery.validate_written_source_workbook(
        workbook_path,
        facts,
    )
    if not written_workbook_contract.get("ok"):
        raise RuntimeError(
            "offline acceptance written-workbook validation failed: "
            + "; ".join(written_workbook_contract["errors"])
        )
    from docx import Document  # noqa: PLC0415

    reopened_document = Document(word_path)
    contract = delivery.validate_cross_artifact_contract(
        facts,
        sheets,
        reopened_document,
    )
    if not contract.get("ok"):
        raise RuntimeError(
            "offline acceptance reopened-artifact parity failed: "
            + "; ".join(contract["errors"])
        )

    measurement_path = output_dir / f"AdoptIQ_Measurement_{token}.json"
    parity_path = output_dir / f"AdoptIQ_Parity_{token}.json"
    chart_path = output_dir / f"AdoptIQ_Charts_{token}.json"
    measurement = _measurement_manifest(
        requested_scope=scope,
        facts=facts,
        serialized_sheets=serialized_sheets,
        contract=contract,
        word_path=word_path,
        workbook_path=workbook_path,
    )
    parity = _parity_manifest(
        requested_scope=scope,
        facts=facts,
        sheets=sheets,
        serialized_sheets=serialized_sheets,
        contract=contract,
        written_workbook_contract=written_workbook_contract,
        expected=payload["expected_by_scope"][scope],
    )
    charts = _chart_manifest(requested_scope=scope, facts=facts)
    if not parity["ok"]:
        failed = sorted(name for name, passed in parity["checks"].items() if not passed)
        raise RuntimeError("offline acceptance manifest checks failed: " + ", ".join(failed))
    _write_json(measurement_path, measurement)
    _write_json(parity_path, parity)
    _write_json(chart_path, charts)

    return {
        "scope": scope,
        "report_type": labels["report_type"],
        "scope_type": labels["scope_type"],
        "scope_value": labels["scope_value"],
        "as_of_utc": as_of_ts.isoformat(),
        "word_path": str(word_path),
        "source_data_path": str(workbook_path),
        "measurement_manifest_path": str(measurement_path),
        "parity_manifest_path": str(parity_path),
        "chart_manifest_path": str(chart_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic offline AdoptIQ acceptance artifacts.",
    )
    parser.add_argument(
        "--scope",
        required=True,
        choices=SUPPORTED_SCOPES,
        help="Acceptance scenario: team, member, customer, or comprehensive",
    )
    parser.add_argument(
        "--as-of",
        required=True,
        help="Explicit ISO timestamp used for Action Plan aging and report lineage",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for the paired artifacts and JSON manifests",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = generate_acceptance_artifacts(
            scope=args.scope,
            as_of=args.as_of,
            output_dir=args.output_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
