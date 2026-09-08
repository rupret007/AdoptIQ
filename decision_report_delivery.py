"""Round 142 concise, source-backed Leader/Comprehensive delivery contract.

The module deliberately separates facts from rendering.  ``build_report_facts``
is the only layer that aggregates report data.  Word charts, KPI tables, and the
Source Data workbook all consume those same immutable-ish fact frames; renderers
must not recalculate counts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import tempfile
from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

import canonical_metrics as cm
import predictive_signals as ps
from data_normalization import (
    _clean_name_for_key,
    add_case_lifecycle_fields,
    alias_join_keys_for_name,
    classify_case_type,
    customer_names_match,
    detect_bems_mask,
    normalize_customer_name,
    normalize_priority_label,
    normalize_severity_label,
    normalize_status_label,
    normalize_subtechnology_label,
    strip_html_from_dataframe,
)
from report_word_styling import add_banded_top_n_table
from report_completeness_audit import (
    audit_source_data_frames,
    audit_word_placeholders,
    audit_written_source_hyperlinks,
)
from risk_scoring import compute_customer_risk_profile, compute_portfolio_risk_summary
from source_record_links import (
    SOURCE_RECORD_URL_COLUMN,
    build_source_record_url,
    csconsole_object_for_source,
    is_allowed_source_record_url,
)

logger = logging.getLogger(__name__)

# Manager reports are decision briefs, not raw-record appendices.  The paired
# Source Data workbook carries complete records; 1,500 words leaves room for
# five prioritized plans and four chart explanations without recreating the
# repeated activity dump shown in the original Leader-report feedback.
WORD_BUDGET_DEFAULT = 1500
# The manager-facing Word report is a decision brief, not the record archive.
# Keep the five highest-priority items in Word and retain every selected-scope
# row in the paired Source Data workbook.
TOP_ITEM_LIMIT_DEFAULT = 5
# Round 165: Leader Team is the manager's complete roster view.  The former
# 15-row cap silently contradicted that contract for larger teams.  Keep only
# a pathological-scope safety ceiling: 64 rows is comfortably above an
# ordinary configured manager roster while still bounding the 1,500-word
# decision brief if malformed input fans out into apparent members.  Accounts
# and Action Plans retain their intentionally selective TOP_ITEM_LIMIT.
MEMBER_ITEM_LIMIT_DEFAULT = 64
# Renewal and Subscription reports need their defining commercial/contract
# facts in Word, but the manager feedback explicitly rejects another raw-data
# appendix.  Select at most one representative from each decision category
# first, then fill the remaining bounded slots deterministically.  Every
# omitted fact remains in the separately named Source Data workbook.
REPORT_SPECIFIC_FACT_LIMIT = 8
FORBIDDEN_RAW_WORD_HEADINGS = frozenset(
    {
        "all action plans",
        "all customer pulse",
        "detailed adoption barriers",
        "detailed adoption barrier list",
        "all tac cases",
        "all support cases",
    }
)

_FRAME_KEYS = OrderedDict(
    [
        ("subscriptions", "Subscriptions"),
        ("action_plans", "Action_Plans"),
        ("adoption_barriers", "Adoption_Barriers"),
        ("customer_pulse", "Customer_Pulse"),
        ("tac_cases", "TAC_Cases"),
        ("success_priorities", "Success_Priorities"),
    ]
)

_SOURCE_CONTRACT_SHEETS = (
    "Metric_Lineage",
    "Chart_Data",
    "Evidence_Links",
    "Action_Plans",
    "Adoption_Barriers",
    "Customer_Pulse",
    "TAC_Cases",
    "BEMS",
    "Subscriptions",
    "Success_Priorities",
    "External_Incidents",
    "External_Bugs",
    "Defect_Correlations",
    "Risk_Components",
    "Member_Summary",
    "Account_Summary",
)
# Round 143: public, ordered artifact inventory for acceptance tooling.  The
# acceptance runner must reuse the writer's contract instead of maintaining a
# competing list that could drift when the canonical workbook evolves.
SOURCE_DATA_SHEET_NAMES = ("Report_Info", *_SOURCE_CONTRACT_SHEETS)
_CROSS_FAMILY_PARITY_COLUMN = "Cross_Family_Parity"
_FAMILY_PRESENTATION_FACT = "family_specific_presentation_fact"
_PUBLIC_CONTEXT_COLUMN_ORDER = (
    "Record_ID",
    "Record_ID_Data_Quality",
    SOURCE_RECORD_URL_COLUMN,
    "CSSM",
    "Scope_Type",
    "Scope_Value",
    "Source_System",
    "Attributed_Team_Members",
)

# These executive insights are factual report claims, not decorative prose.
# Their exact text and evidence identity are frozen in ``build_report_facts``
# before the Word/workbook fingerprint is minted.  The renderer and semantic
# validator consume the same ordered payload and must never recalculate them.
_DECISION_INSIGHT_ORDER = (
    "run_delta",
    "support_themes",
    "support_operating_health",
    "window_momentum",
    "predictive_outlook",
)
_DECISION_INSIGHT_PREFIXES = {
    "run_delta": "Since the last comparable report:",
    "support_themes": "Support themes (TAC):",
    "support_operating_health": "Support operating health (TAC):",
    "window_momentum": "Momentum within this window:",
    "predictive_outlook": ("Predictive outlook (next 30 days, deterministic scorecard):"),
}
# Round 171: ``run_delta`` leads _DECISION_INSIGHT_ORDER deliberately — it is
# the direct answer to the "What Is Changing" heading, so on families with an
# ``insight_limit`` it must never be the line that gets trimmed.  Offline
# acceptance fixtures run with no prior report, so the insight is structurally
# absent there and the pinned oracles (and the relative order of the other
# four insights) are untouched.
_SUPPORT_THEME_TECH_COLUMNS = (
    "sub_technology",
    "SUB_TECHNOLOGY",
    "Sub Technology",
    "Tech.",
    "Tech",
    "Technology",
    "TECHNOLOGY",
    "Product",
    "PRODUCT_NAME",
)
_PULSE_DATE_COLUMNS = ("PULSE_DATE_C", "Pulse Date")
_PULSE_VALUE_COLUMNS = ("SCORE__C", "Pulse Score", "SCORE", "PULSE_SCORE")

_SOURCE_SYSTEM_BY_KEY: Dict[str, str] = {
    "subscriptions": "Snowflake subscriptions",
    "action_plans": "Snowflake C360 Action Plans",
    "adoption_barriers": "Snowflake C360 Adoption Barriers",
    "customer_pulse": "Snowflake C360 Customer Pulse",
    "tac_cases": "CSOne",
    "success_priorities": "Snowflake C360 Success Priorities",
    "external_incidents": "status.webex.com",
    "external_bugs": "help.webex.com",
    "defect_correlations": "AdoptIQ exact CSC correlation",
}

_ID_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "subscriptions": ("Record_ID", "SUBSCRIPTION_ID", "Subscription ID", "ID"),
    "action_plans": cm.ACTION_PLAN_ID_COLUMNS,
    "adoption_barriers": ("Record_ID", "ID", "BARRIER_ID", "Id"),
    "customer_pulse": ("Record_ID", "ID", "PULSE_ID", "Id"),
    "tac_cases": (
        "Record_ID",
        "SR Number",
        "Case Number",
        "Case #",
        "CASE_NUMBER",
        "case_id",
        "ID",
    ),
    "success_priorities": ("Record_ID", "ID", "SUCCESS_PRIORITY_ID", "Id"),
    "external_incidents": ("Record_ID", "id", "incident_number", "ID"),
    "external_bugs": ("Record_ID", "bug_id", "id", "ID"),
    "defect_correlations": ("Record_ID", "CSC_ID"),
}

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
_ACCOUNT_ID_COLUMNS = (
    "ACCOUNT_ID_C",
    "ACCOUNT_ID",
    "ACCOUNT__C",
    "ACCOUNT__C_ID",
    "DSM_ACCOUNT_ID_C",
    "ACCOUNTID",
    "Account ID",
    "Account Id",
    "AccountId",
    "account_id",
    "CUSTOMER_ID_C",
    "CUSTOMER_ID",
    "Customer ID",
    "customer_id",
)
_MEMBER_NAME_COLUMNS = (
    "cssm_name",
    "CSSM",
    "CSSM_NAME",
    "Team_Member",
    "TEAM_MEMBER",
    "Owner Name",
    "OWNER_NAME",
)
_MEMBER_EMAIL_COLUMNS = (
    "CSSM_EMAIL",
    "cssm_email",
    "PRIMARY_DSM_EMAIL",
    "PRIMARY_CSSM_EMAIL",
    "assignee_cssm_email",
    "OWNER_EMAIL",
)
_OWNER_COLUMNS = (
    "NEXT_ACTION_OWNER_C",
    "Next Action Owner",
    "NEXT_ACTION_OWNER",
    "ASSIGNEE_C",
    "Assignee",
    "Owner Name",
    "OWNER_NAME",
    "Owner",
    "OWNER",
    "CSSM",
    "cssm_name",
    "CSSM_NAME",
    "Team_Member",
    "TEAM_MEMBER",
    "CSSM_EMAIL",
    "cssm_email",
    "PRIMARY_DSM_EMAIL",
    "PRIMARY_CSSM_EMAIL",
    "assignee_cssm_email",
    "OWNER_EMAIL",
    "_CREATOR_NAME",
)
_NEXT_ACTION_COLUMNS = (
    "NEXT_ACTION_C",
    "NEXT_STEP_C",
    "CURRENT_STATUS_AND_NOTES_C",
    "ACTION_C",
)
_PRIORITY_COLUMNS = ("PRIORITY_C", "Priority", "PRIORITY", "SEVERITY_C")
_ACTION_PLAN_SOURCE_STATUS_COLUMNS = (
    "STATUS_C",
    "Status",
    "STATUS",
    "ACTION_PLAN_STATUS_C",
)


def _bounded_manager_text(value: Any, *, limit: int, fallback: str) -> str:
    """Bound repeated decision-surface text while preserving full source rows."""

    text = _clean_token(value)
    if not text or text.casefold() in {"unknown", "n/a", "none", "null", "nan"}:
        return fallback
    ceiling = max(int(limit), 40)
    if len(text) <= ceiling:
        return text
    prefix = text[: max(ceiling - 36, 1)].rsplit(" ", 1)[0].rstrip(" ,;:")
    return (prefix or text[: max(ceiling - 36, 1)]).rstrip() + "… [complete in Source Data]"


def _action_plan_status_display(row: Mapping[str, Any]) -> str:
    """Translate the canonical unresolved bucket into an exact source reason."""

    bucket = str(row.get("AdoptIQ_Status_Bucket") or "").strip()
    if bucket and bucket != "Unknown":
        return bucket
    raw_status = _bounded_manager_text(
        _first_value(row, _ACTION_PLAN_SOURCE_STATUS_COLUMNS),
        limit=90,
        fallback="",
    )
    return f"Unmapped: {raw_status}" if raw_status else "Status not provided"


def _action_plan_chart_category_display(category: Any, *, age_series: bool) -> str:
    category_label = str(category or "").strip()
    if category_label != "Unknown":
        return category_label
    return "Created date unavailable" if age_series else "Status unresolved"


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted((_json_safe(item) for item in value), key=lambda item: str(item))
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            pass
    return value


def fact_contract_fingerprint(
    facts: Mapping[str, Any],
    *,
    source_digests: Optional[Mapping[str, str]] = None,
) -> str:
    """Hash the semantic facts visible in Word and the Source Data File."""

    chart_rows = (
        facts["chart_data"]
        .loc[
            :,
            ["Metric_Key", "Value", "Source_State", "Period_Start"],
        ]
        .to_dict("records")
    )
    payload = {
        "report_type": facts["report_type"],
        "scope_type": facts["scope_type"],
        "scope_value": facts["scope_value"],
        "manager_name": facts["manager_name"],
        "technology": facts.get("technology") or "",
        "days": facts["days"],
        "as_of_utc": facts["as_of_utc"],
        "data_as_of_state": facts.get("data_as_of_state") or "available",
        "data_as_of_detail": facts.get("data_as_of_detail") or "",
        "retrieval_attempted_at_utc": (facts.get("retrieval_attempted_at_utc") or ""),
        "evaluation_as_of_utc": facts.get("evaluation_as_of_utc") or "",
        "data_mode": facts.get("data_mode") or "",
        "live_validation_performed": facts.get("live_validation_performed"),
        "source_observation_route_diagnostics": facts.get(
            "source_observation_route_diagnostics"
        )
        or {},
        "kpis": facts["kpis"],
        "action_plan_lifecycle": {
            key: facts["action_plan_lifecycle"].get(key)
            for key in (
                "total",
                "open",
                "overdue",
                "due_soon",
                "open_other",
                "completed",
                "blocked_on_hold",
                "unknown",
                "unresolved_total",
                "unknown_age",
                "missing_title",
                "missing_record_id",
                "age_band_counts",
                "source_state",
            )
        },
        "chart_rows": chart_rows,
        "member_summary": facts.get("member_summary"),
        "account_summary": facts.get("account_summary"),
        "decision_signals": facts.get("decision_signals") or [],
        "decision_insights": facts.get("decision_insights") or {},
        "report_specific_decision_facts": _report_specific_decision_fact_bundle(facts),
        "source_coverage": facts["source_coverage"].to_dict("records"),
        "tac_case_type_coverage": facts.get("tac_case_type_coverage") or {},
        "partial_data_warnings": facts.get("partial_data_warnings") or [],
        "source_sheet_digests": dict(source_digests if source_digests is not None else _source_contract_digests(facts)),
    }
    serialized = json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _clean_token(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    token = str(value).strip()
    if token.casefold() in {"", "nan", "none", "null", "unknown"}:
        return ""
    return token


def _customer_match_key(value: Any) -> str:
    """Return the exact normalized customer-label key used for identity.

    Legal suffixes are intentionally preserved.  ``Acme Inc`` and
    ``Acme LLC`` may be related strings, but they are not proof of the same
    customer and therefore must remain separate KPI/risk identities.
    """

    token = _clean_token(value)
    return normalize_customer_name(token).casefold() if token else ""


def _customer_fuzzy_join_key(value: Any) -> str:
    """Return a permissive alias key for guarded joins only.

    Callers must prove that the key resolves to exactly one authoritative
    identity before using it.  It must never be used to form the canonical
    customer universe or to count customers.
    """

    token = _clean_token(value)
    return _clean_name_for_key(token) if token else ""


def _account_match_key(value: Any) -> str:
    return _clean_token(value).casefold()


def _first_value(row: Mapping[str, Any], candidates: Sequence[str], default: str = "") -> str:
    for column in candidates:
        try:
            value = row.get(column)
        except Exception:  # noqa: BLE001
            value = None
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        token = str(value).strip()
        if token and token.lower() not in {"nan", "none", "null"}:
            return token
    return default


def _first_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    return cm.first_populated_column(df, candidates)


def _combine_attribution(values: Iterable[Any]) -> str:
    tokens = sorted({str(value).strip() for value in values if str(value).strip()})
    return "; ".join(tokens)


def _source_observation_route_diagnostics(
    sources: Mapping[str, pd.DataFrame],
) -> Dict[str, Dict[str, Any]]:
    """Expose privacy-safe diagnostics for non-semantic collection routes."""

    diagnostics: Dict[str, Dict[str, Any]] = {}
    for key, frame in sorted(sources.items()):
        if not isinstance(frame, pd.DataFrame):
            continue
        routes = sorted(
            {
                _clean_token(value)
                for value in (getattr(frame, "attrs", {}) or {}).get(
                    "source_observation_routes",
                    [],
                )
                if _clean_token(value)
            },
            key=lambda value: (value.casefold(), value),
        )
        if not routes:
            continue
        diagnostics[key] = {
            "route_count": len(routes),
            "route_sha256": hashlib.sha256(
                json.dumps(routes, ensure_ascii=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
        }
    return diagnostics


def _with_public_record_id(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Copy the selected source identifier into the public ``Record_ID`` column."""

    use = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    candidates = _ID_CANDIDATES.get(key, ("Record_ID", "ID"))
    record_ids = pd.Series([""] * len(use), index=use.index, dtype="object")
    for column in candidates:
        if column not in use.columns:
            continue
        values = use[column].map(_clean_token)
        take = record_ids.eq("") & values.ne("")
        record_ids.loc[take] = values.loc[take]
    if "Record_ID" in use.columns:
        use["Record_ID"] = record_ids
    else:
        use.insert(0, "Record_ID", record_ids)
    if "Record_ID_Data_Quality" not in use.columns:
        use["Record_ID_Data_Quality"] = use["Record_ID"].map(
            lambda value: "OK" if str(value).strip() else "Missing stable source ID"
        )
    # Never trust or preserve a caller-supplied URL.  Supported Salesforce
    # objects are allow-listed centrally and the public link is derived from
    # the canonical source key plus stable source ID.  Unsupported sources do
    # not gain a blank URL column, keeping TAC/external sheets uncluttered.
    if csconsole_object_for_source(key):
        use[SOURCE_RECORD_URL_COLUMN] = use["Record_ID"].map(lambda value: build_source_record_url(key, value))
    return use


def aggregate_team_frames(
    team_data: Optional[Mapping[str, Mapping[str, Any]]],
    *,
    scope_type: str,
    scope_value: str,
    preserve_action_plan_observations: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Create one attribution-preserving frame per source.

    Stable IDs normally collapse here. Report-fact construction requests that
    Action Plan observations survive until
    :func:`canonical_metrics.build_action_plan_lifecycle`, where lifecycle-
    driving conflicts can be detected and quarantined instead of being
    resolved by whichever source row happened to arrive first.
    """

    result: Dict[str, pd.DataFrame] = {}
    team_data = team_data or {}
    for key in _FRAME_KEYS:
        parts: List[pd.DataFrame] = []
        attrs: Dict[str, Any] = {}
        reconciliation: Dict[str, Any] = {}
        supplied_members = 0
        for member in sorted(team_data):
            bundle = team_data.get(member) or {}
            frame = bundle.get(key)
            if not isinstance(frame, pd.DataFrame):
                continue
            supplied_members += 1
            source_mode = _clean_token(
                (getattr(frame, "attrs", {}) or {}).get("source_mode")
            )
            if source_mode:
                attrs.setdefault("source_modes", []).append(source_mode)
            frame_state = cm.source_data_state(frame)
            attrs.setdefault("source_states", []).append(frame_state["state"])
            frame_attrs = dict(getattr(frame, "attrs", {}) or {})
            observation_routes = list(frame_attrs.get("source_observation_routes") or [])
            if "Source_System" in frame.columns:
                observation_routes.extend(
                    _clean_token(value)
                    for value in frame["Source_System"].tolist()
                    if _clean_token(value)
                    and _clean_token(value) != _SOURCE_SYSTEM_BY_KEY[key]
                )
            if observation_routes:
                attrs.setdefault("source_observation_routes", []).extend(observation_routes)
            for attr_name in (
                "stable_id_conflicting_record_count",
                "stable_id_quarantined_observation_count",
            ):
                # Partitioning copies one portfolio source-state attr onto
                # each member slice.  ``max`` carries that aggregate count
                # once instead of multiplying it by roster fan-out.
                attrs[attr_name] = max(
                    int(attrs.get(attr_name) or 0),
                    int(frame_attrs.get(attr_name) or 0),
                )
            if frame_state["state"] == "unavailable":
                attrs.setdefault("unavailable_details", []).append(frame_state["detail"])
            elif frame_state["state"] == "failed":
                attrs.setdefault("fetch_errors", []).append(frame_state["detail"])
            elif frame_state["state"] in {"partial", "stale"}:
                attrs.setdefault("partial_details", []).append(frame_state["detail"])
            if frame.empty:
                continue
            copy = frame.copy()
            member_label = "Unassigned / Portfolio" if str(member).startswith("__") else str(member)
            copy["CSSM"] = member_label
            parts.append(copy)
        if parts:
            combined = pd.concat(parts, ignore_index=True, sort=False)
        else:
            combined = pd.DataFrame()
        combined = _with_public_record_id(combined, key)
        if "CSSM" not in combined.columns:
            combined["CSSM"] = pd.Series(dtype="object")
        id_column = "Record_ID" if "Record_ID" in combined.columns else None
        if id_column and not combined.empty:
            tokens = combined[id_column].fillna("").astype(str).str.strip()
            with_id = combined.loc[tokens.ne("")].copy()
            without_id = combined.loc[tokens.eq("")].copy()
            if not with_id.empty:
                normalized_ids = with_id[id_column].fillna("").astype(str).str.strip().str.casefold()
                attribution = (
                    with_id.assign(__adoptiq_attribution_id=normalized_ids)
                    .groupby("__adoptiq_attribution_id", dropna=False)["CSSM"]
                    .agg(_combine_attribution)
                )
                with_id["Attributed_Team_Members"] = normalized_ids.map(attribution)
                if key != "action_plans" or not preserve_action_plan_observations:
                    with_id, reconciliation = cm.reconcile_stable_id_observations(
                        with_id,
                        id_candidates=(id_column,),
                        source_label=_FRAME_KEYS[key],
                    )
                    if reconciliation.get("state") == "partial":
                        attrs.setdefault("partial_details", []).append(
                            str(reconciliation.get("detail") or "").strip()
                        )
                    attrs["stable_id_conflicting_record_count"] = int(
                        attrs.get("stable_id_conflicting_record_count") or 0
                    ) + int(reconciliation.get("conflicting_stable_id_count") or 0)
                    attrs["stable_id_quarantined_observation_count"] = int(
                        attrs.get("stable_id_quarantined_observation_count") or 0
                    ) + int(reconciliation.get("quarantined_observation_count") or 0)
            if not without_id.empty:
                partition_key = "_AdoptIQ_Partition_Row_Key"
                if partition_key in without_id.columns:
                    keyed = without_id.loc[without_id[partition_key].map(_clean_token).ne("")].copy()
                    unkeyed = without_id.loc[without_id[partition_key].map(_clean_token).eq("")].copy()
                    if not keyed.empty:
                        attribution = keyed.groupby(partition_key, dropna=False)["CSSM"].agg(_combine_attribution)
                        keyed = (
                            keyed.sort_values([partition_key, "CSSM"], kind="stable")
                            .drop_duplicates(subset=[partition_key], keep="first")
                            .copy()
                        )
                        keyed["Attributed_Team_Members"] = keyed[partition_key].map(attribution)
                    if not unkeyed.empty:
                        unkeyed["Attributed_Team_Members"] = unkeyed["CSSM"].astype(str)
                    without_id = pd.concat([keyed, unkeyed], ignore_index=True, sort=False)
                else:
                    without_id["Attributed_Team_Members"] = without_id["CSSM"].astype(str)
            combined = pd.concat([with_id, without_id], ignore_index=True, sort=False)
        elif "Attributed_Team_Members" not in combined.columns:
            combined["Attributed_Team_Members"] = combined.get("CSSM", pd.Series(dtype="object"))
        combined["Scope_Type"] = scope_type
        combined["Scope_Value"] = scope_value
        # Source_System names the logical upstream system, not the query path,
        # report family, or intermediate workbook used to transport a row.
        # Keep those route labels in attrs for diagnostics and make the public
        # fact contract identical for identical scoped source observations.
        combined["Source_System"] = _SOURCE_SYSTEM_BY_KEY[key]
        source_observation_routes = sorted(
            set(attrs.get("source_observation_routes") or []),
            key=lambda value: (value.casefold(), value),
        )
        if source_observation_routes:
            combined.attrs["source_observation_routes"] = source_observation_routes
        if supplied_members == 0:
            combined.attrs["source_unavailable"] = True
            combined.attrs["source_unavailable_detail"] = (
                f"{_FRAME_KEYS[key]} source frame not supplied for the selected scope"
            )
        elif supplied_members < len(team_data):
            combined.attrs["partial"] = True
            attrs.setdefault("fetch_errors", []).append(
                f"source frame supplied for {supplied_members} of {len(team_data)} member bundles"
            )
        if attrs.get("fetch_errors"):
            combined.attrs["fetch_error"] = "; ".join(sorted(set(attrs["fetch_errors"])))
            combined.attrs["fetch_error_partial"] = bool(not combined.empty)
        source_states = set(attrs.get("source_states") or [])
        if source_states == {"unavailable"}:
            combined.attrs["source_unavailable"] = True
            combined.attrs["source_unavailable_detail"] = "; ".join(
                sorted(set(attrs.get("unavailable_details") or ["source frame not supplied"]))
            )
        elif "unavailable" in source_states:
            combined.attrs["partial"] = True
        if "stale" in source_states:
            combined.attrs["stale"] = True
        if "partial" in source_states:
            combined.attrs["partial"] = True
        if attrs.get("partial_details"):
            combined.attrs["partial"] = True
            combined.attrs["source_mode_detail"] = "; ".join(sorted(set(attrs["partial_details"])))
        if reconciliation or attrs.get("stable_id_conflicting_record_count"):
            combined.attrs["stable_id_conflicting_record_count"] = int(
                attrs.get("stable_id_conflicting_record_count") or 0
            )
            combined.attrs["stable_id_quarantined_observation_count"] = int(
                attrs.get("stable_id_quarantined_observation_count") or 0
            )
        if reconciliation:
            combined.attrs["stable_id_compatible_duplicate_observation_count"] = int(
                reconciliation.get("compatible_duplicate_observation_count") or 0
            )
            combined.attrs["stable_id_missing_observation_count"] = int(
                reconciliation.get("missing_id_observation_count") or 0
            )
        source_modes = sorted(set(attrs.get("source_modes") or []))
        if len(source_modes) == 1:
            combined.attrs["source_mode"] = source_modes[0]
        elif source_modes:
            combined.attrs["source_mode"] = "mixed"
            combined.attrs["source_modes"] = source_modes
        if key != "action_plans" or not preserve_action_plan_observations:
            combined = combined.drop(
                columns=["_AdoptIQ_Partition_Row_Key"],
                errors="ignore",
            )
        finalized = combined.reset_index(drop=True)
        finalized.attrs.update(combined.attrs)
        result[key] = finalized
    return result


_AB_SOURCE_TECH_COLUMNS = (
    "SUB_TECHNOLOGY_C",
    "TECHNOLOGY_C",
    "PRODUCT_NAME_C",
    "PRODUCT_C",
    "Sub Technology",
    "Technology",
    "Product Name",
    "Product",
)
_SUBSCRIPTION_TECH_COLUMNS = (
    "SUB_TECHNOLOGY_C",
    "TECHNOLOGY_C",
    "PRODUCT_NAME",
    "Product Name",
    "Product",
)
_ACCOUNT_ID_COLUMNS = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "Account ID",
    "ACCOUNT_ID",
)


def _enrich_barrier_technology_from_subscriptions(
    barriers: pd.DataFrame,
    subscriptions: pd.DataFrame,
) -> pd.DataFrame:
    """Fill missing AB technology only from one authoritative account value.

    A source-native AB technology wins.  Otherwise the scoped subscription
    rows may enrich it only when all populated rows for that account resolve
    to exactly one canonical sub-technology.  Missing or ambiguous account
    evidence remains explicitly unclassified; no query order or arbitrary
    first subscription is allowed to choose a customer fact.
    """

    if not isinstance(barriers, pd.DataFrame):
        return pd.DataFrame()
    attrs = dict(getattr(barriers, "attrs", {}) or {})
    result = barriers.copy()

    def _specific_technology(row: pd.Series, columns: Sequence[str]) -> str:
        for column in columns:
            raw_value = _first_value(row, (column,))
            if not raw_value:
                continue
            normalized_value = normalize_subtechnology_label(raw_value)
            if normalized_value != "Other / Unclassified":
                return normalized_value
        return ""

    technologies_by_account: Dict[str, set[str]] = {}
    if isinstance(subscriptions, pd.DataFrame) and not subscriptions.empty:
        for _, row in subscriptions.iterrows():
            account_key = _account_match_key(_first_value(row, _ACCOUNT_ID_COLUMNS))
            normalized = _specific_technology(row, _SUBSCRIPTION_TECH_COLUMNS)
            if not account_key or not normalized:
                continue
            technologies_by_account.setdefault(account_key, set()).add(normalized)

    values: List[str] = []
    unique_enrichments = 0
    ambiguous_accounts = 0
    missing_accounts = 0
    for _, row in result.iterrows():
        normalized_explicit = _specific_technology(row, _AB_SOURCE_TECH_COLUMNS)
        derived_marker = str(
            row.get("_AdoptIQ_Subtechnology_Derived", "")
        ).strip().casefold() in {"true", "1", "yes"}
        if not normalized_explicit and not derived_marker:
            normalized_explicit = _specific_technology(row, ("sub_technology",))
        if normalized_explicit:
            values.append(normalized_explicit)
            continue
        account_key = _account_match_key(_first_value(row, _ACCOUNT_ID_COLUMNS))
        candidates = technologies_by_account.get(account_key, set())
        if len(candidates) == 1:
            values.append(next(iter(candidates)))
            unique_enrichments += 1
        else:
            values.append("Other / Unclassified")
            if candidates:
                ambiguous_accounts += 1
            else:
                missing_accounts += 1
    result["sub_technology"] = values
    result.attrs.update(attrs)
    result.attrs.update(
        {
            "ab_technology_unique_subscription_enrichment_count": unique_enrichments,
            "ab_technology_ambiguous_subscription_count": ambiguous_accounts,
            "ab_technology_missing_subscription_count": missing_accounts,
        }
    )
    return result


def _decorate_standalone_source(
    frame: pd.DataFrame,
    *,
    key: str,
    scope_type: str,
    scope_value: str,
) -> pd.DataFrame:
    """Add the public provenance contract to a non-team-bundle source."""

    use = _with_public_record_id(frame, key)
    use["Scope_Type"] = str(scope_type)
    use["Scope_Value"] = str(scope_value)
    fallback_source = _SOURCE_SYSTEM_BY_KEY[key]
    source_attrs = dict(getattr(use, "attrs", {}) or {})
    observation_routes = list(source_attrs.get("source_observation_routes") or [])
    if "Source_System" in use.columns:
        observation_routes.extend(
            _clean_token(value)
            for value in use["Source_System"].tolist()
            if _clean_token(value)
            and _clean_token(value) != fallback_source
        )
    use["Source_System"] = fallback_source
    if observation_routes:
        use.attrs["source_observation_routes"] = sorted(
            set(observation_routes),
            key=lambda value: (value.casefold(), value),
        )
    if "Attributed_Team_Members" not in use.columns:
        use["Attributed_Team_Members"] = ""
    if "CSSM" not in use.columns:
        use["CSSM"] = ""
    return use


def _external_frame(
    records: Any,
    *,
    possible_cap: Optional[int] = None,
) -> pd.DataFrame:
    """Normalize an external feed while preserving its availability attrs."""

    if isinstance(records, pd.DataFrame):
        built = records.copy()
        built.attrs.update(dict(getattr(records, "attrs", {}) or {}))
        return built
    built = pd.DataFrame(list(records or []))
    metadata = [
        row.get("_window_meta")
        for row in (records or [])
        if isinstance(row, Mapping) and isinstance(row.get("_window_meta"), Mapping)
    ]
    if any(bool(item.get("truncated")) for item in metadata):
        built.attrs["partial"] = True
        built.attrs["source_mode_detail"] = "external feed reached its configured row/window cap; older records omitted"
    if any(bool(item.get("stale")) for item in metadata):
        built.attrs["stale"] = True
    if possible_cap and len(built) >= int(possible_cap):
        built.attrs["partial"] = True
        built.attrs.setdefault(
            "source_mode_detail",
            f"external feed returned the configured {possible_cap}-record cap; additional records may be omitted",
        )
    return built


def partition_portfolio_by_member(
    *,
    subscriptions: Optional[pd.DataFrame],
    action_plans: Optional[pd.DataFrame],
    adoption_barriers: Optional[pd.DataFrame],
    customer_pulse: Optional[pd.DataFrame],
    tac_cases: Optional[pd.DataFrame],
    success_priorities: Optional[pd.DataFrame] = None,
    fallback_member: str = "Portfolio",
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Partition already-scoped portfolio frames by subscription ownership.

    Comprehensive fetches portfolio frames in one pass.  This helper converts
    them into the same member-bundle shape the Leader contract consumes,
    without re-fetching data or widening scope.
    """

    subs = subscriptions.copy() if isinstance(subscriptions, pd.DataFrame) else pd.DataFrame()
    subscription_attrs = dict(getattr(subscriptions, "attrs", {}) or {})

    # Email is the stable member key.  Display names remain presentation
    # labels only, because two different people can legitimately share one.
    row_members: List[Dict[str, Any]] = []
    for position, (_, row) in enumerate(subs.iterrows()):
        row_members.append(
            {
                "position": position,
                "name": _first_value(row, _MEMBER_NAME_COLUMNS),
                "email": _first_value(row, _MEMBER_EMAIL_COLUMNS).casefold(),
            }
        )
    name_to_emails: Dict[str, set] = {}
    for item in row_members:
        if item["name"] and item["email"]:
            name_to_emails.setdefault(item["name"].casefold(), set()).add(item["email"])

    entities: Dict[str, Dict[str, Any]] = {}
    ambiguous_subscription_positions: List[int] = []
    for item in row_members:
        name_key = item["name"].casefold() if item["name"] else ""
        if item["email"]:
            entity_key = f"email:{item['email']}"
        elif name_key and len(name_to_emails.get(name_key, set())) == 1:
            entity_key = f"email:{next(iter(name_to_emails[name_key]))}"
        elif name_key and len(name_to_emails.get(name_key, set())) > 1:
            # A name-only subscription row cannot be attributed between two
            # same-name people without guessing.
            ambiguous_subscription_positions.append(item["position"])
            continue
        elif name_key:
            entity_key = f"name:{name_key}"
        else:
            ambiguous_subscription_positions.append(item["position"])
            continue
        entity = entities.setdefault(
            entity_key,
            {"positions": [], "names": set(), "emails": set()},
        )
        entity["positions"].append(item["position"])
        if item["name"]:
            entity["names"].add(item["name"])
        if item["email"]:
            entity["emails"].add(item["email"])

    if not entities and not ambiguous_subscription_positions:
        fallback = _clean_token(fallback_member) or "Portfolio"
        entities[f"name:{fallback.casefold()}"] = {
            "positions": list(range(len(subs))),
            "names": {fallback},
            "emails": set(),
        }

    base_labels: Dict[str, str] = {}
    for entity_key, entity in entities.items():
        if entity["names"]:
            base_labels[entity_key] = sorted(entity["names"], key=lambda value: (value.casefold(), value))[0]
        elif entity["emails"]:
            base_labels[entity_key] = sorted(entity["emails"])[0]
        else:
            base_labels[entity_key] = _clean_token(fallback_member) or "Portfolio"
    label_counts: Dict[str, int] = {}
    for label in base_labels.values():
        label_counts[label.casefold()] = label_counts.get(label.casefold(), 0) + 1
    member_labels: Dict[str, str] = {}
    for entity_key, entity in entities.items():
        label = base_labels[entity_key]
        if label_counts.get(label.casefold(), 0) > 1:
            discriminator = sorted(entity["emails"])[0] if entity["emails"] else entity_key
            label = f"{label} <{discriminator}>"
        member_labels[entity_key] = label

    source_frames = {
        "action_plans": action_plans,
        "adoption_barriers": adoption_barriers,
        "customer_pulse": customer_pulse,
        "tac_cases": tac_cases,
        "success_priorities": success_priorities,
    }
    result: Dict[str, Dict[str, pd.DataFrame]] = {}
    ownership: Dict[str, Dict[str, set]] = {}
    for entity_key in sorted(entities, key=lambda key: (member_labels[key].casefold(), key)):
        member = member_labels[entity_key]
        entity = entities[entity_key]
        member_subs = subs.iloc[entity["positions"]].copy().reset_index(drop=True)
        member_subs.attrs.update(subscription_attrs)
        customer_keys = {
            _customer_match_key(value)
            for column in _CUSTOMER_COLUMNS
            if column in member_subs.columns
            for value in member_subs[column].tolist()
            if _customer_match_key(value)
        }
        account_keys = {
            _account_match_key(value)
            for column in _ACCOUNT_ID_COLUMNS
            if column in member_subs.columns
            for value in member_subs[column].tolist()
            if _account_match_key(value)
        }
        customer_fuzzy_keys = {
            _customer_fuzzy_join_key(value)
            for column in _CUSTOMER_COLUMNS
            if column in member_subs.columns
            for value in member_subs[column].tolist()
            if _customer_fuzzy_join_key(value)
        }
        name_aliases = {name.casefold() for name in entity["names"] if name}
        email_aliases = {email.casefold() for email in entity["emails"] if email}
        ownership[member] = {
            "customers": customer_keys,
            "customer_fuzzy": customer_fuzzy_keys,
            "accounts": account_keys,
            "name_aliases": name_aliases,
            "email_aliases": email_aliases,
        }
        result[member] = {"subscriptions": member_subs}

    members = sorted(ownership, key=lambda value: value.casefold())

    unassigned_key = "__Unassigned_Portfolio__"
    unassigned_used = bool(ambiguous_subscription_positions)
    if unassigned_used:
        unassigned_subs = subs.iloc[ambiguous_subscription_positions].copy().reset_index(drop=True)
        unassigned_subs.attrs.update(subscription_attrs)
        result[unassigned_key] = {
            "subscriptions": unassigned_subs,
            "_adoptiq_unassigned_bundle": True,
        }
    for source_key, raw in source_frames.items():
        frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
        source_attrs = dict(getattr(raw, "attrs", {}) or {})
        if frame.empty:
            for member in members:
                empty = frame.copy()
                empty.attrs.update(source_attrs)
                result[member][source_key] = empty
            continue

        frame = frame.reset_index(drop=True)
        frame["_AdoptIQ_Partition_Row_Key"] = [f"{source_key}:{position}" for position in range(len(frame))]
        assigned: Dict[str, List[int]] = {member: [] for member in members}
        unassigned: List[int] = []
        for position, row in frame.iterrows():
            row_accounts = {
                _account_match_key(row.get(column))
                for column in _ACCOUNT_ID_COLUMNS
                if column in frame.columns and _account_match_key(row.get(column))
            }
            account_targets = [
                member
                for member in members
                if row_accounts & ownership[member]["accounts"]
            ]
            direct_tokens = {
                _clean_token(row.get(column)).casefold()
                for column in _OWNER_COLUMNS
                if column in frame.columns and _clean_token(row.get(column))
            }
            direct_emails = {token for token in direct_tokens if "@" in token}
            direct_names = direct_tokens - direct_emails
            direct_members = [member for member in members if direct_emails & ownership[member]["email_aliases"]]
            if not direct_members and direct_names:
                name_matches = [member for member in members if direct_names & ownership[member]["name_aliases"]]
                # A duplicate display name is not a safe direct attribution.
                if len(name_matches) == 1:
                    direct_members = name_matches
            # A stable scoped account is the authoritative portfolio/team
            # attribution and may legitimately map to multiple members. A
            # source-reported next-action owner or case contact remains in its
            # original field, but must not silently collapse shared account
            # ownership in Member_Summary. Direct attribution is the fallback
            # only when no stable account mapping exists.
            if account_targets:
                targets = account_targets
            elif direct_members:
                targets = direct_members
            else:
                row_customers = {
                    _customer_match_key(row.get(column))
                    for column in _CUSTOMER_COLUMNS
                    if column in frame.columns and _customer_match_key(row.get(column))
                }
                targets = [member for member in members if row_customers & ownership[member]["customers"]]
                if not targets:
                    row_fuzzy_customers = {
                        _customer_fuzzy_join_key(row.get(column))
                        for column in _CUSTOMER_COLUMNS
                        if column in frame.columns and _customer_fuzzy_join_key(row.get(column))
                    }
                    fuzzy_targets = [
                        member for member in members if row_fuzzy_customers & ownership[member]["customer_fuzzy"]
                    ]
                    # Legal-suffix-tolerant attribution is allowed only when
                    # the fuzzy alias identifies one member unambiguously.
                    targets = fuzzy_targets if len(fuzzy_targets) == 1 else []
            if targets:
                for member in targets:
                    assigned[member].append(position)
            else:
                unassigned.append(position)

        for member in members:
            sliced = frame.iloc[assigned[member]].copy().reset_index(drop=True)
            sliced.attrs.update(source_attrs)
            result[member][source_key] = sliced
        if unassigned:
            if not unassigned_used:
                result[unassigned_key] = {
                    "subscriptions": subs.iloc[0:0].copy(),
                    "_adoptiq_unassigned_bundle": True,
                }
                unassigned_used = True
            sliced = frame.iloc[unassigned].copy().reset_index(drop=True)
            sliced.attrs.update(source_attrs)
            result[unassigned_key][source_key] = sliced
        elif unassigned_used:
            empty = frame.iloc[0:0].copy()
            empty.attrs.update(source_attrs)
            result[unassigned_key][source_key] = empty

    if unassigned_used:
        for source_key, raw in source_frames.items():
            if source_key not in result[unassigned_key]:
                empty = raw.iloc[0:0].copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
                empty.attrs.update(dict(getattr(raw, "attrs", {}) or {}))
                result[unassigned_key][source_key] = empty
    return result


def _normalize_scoped_team_attribution(
    team_data: Mapping[str, Mapping[str, Any]],
    *,
    scope_type: str,
    scope_value: str,
) -> Mapping[str, Mapping[str, Any]]:
    """Re-project fully identified team bundles through scoped account ownership.

    Leader collection can return the same scoped account through more than one
    roster member while an account-backed source record appears in only one
    member's fetch result.  Comprehensive already partitions its portfolio
    frames through :func:`partition_portfolio_by_member`, so trusting the
    incidental bundle for Leader made the two reports disagree about
    ``Attributed_Team_Members`` and understated shared-account work in the
    member rollup.

    This normalization is deliberately conservative:

    * it runs only for a multi-member scope where every named member has a
      subscription row;
    * an existing partition-row key proves the caller already performed the
      projection, making the operation idempotent for Comprehensive;
    * the projected roster must resolve back to exactly the same named-member
      set, otherwise the original already-authorized bundles are returned
      unchanged rather than guessing an identity; and
    * only rows already present inside the selected scope participate, so this
      cannot widen authorization or trigger another source query.

    Full source-state attrs are retained from the original team aggregation.
    Stable account ownership is authoritative for attribution; direct record
    ownership remains available in the untouched source fields.
    """

    original = dict(team_data or {})
    named_members = [
        str(member)
        for member in original
        if not str(member).startswith("__")
    ]
    if len(named_members) < 2:
        return team_data

    for member in named_members:
        bundle = original.get(member) or {}
        subscriptions = bundle.get("subscriptions")
        if not isinstance(subscriptions, pd.DataFrame) or subscriptions.empty:
            return team_data

    for bundle in original.values():
        if not isinstance(bundle, Mapping):
            continue
        for source_key in _FRAME_KEYS:
            frame = bundle.get(source_key)
            if (
                source_key != "subscriptions"
                and isinstance(frame, pd.DataFrame)
                and "_AdoptIQ_Partition_Row_Key" in frame.columns
            ):
                return team_data

    aggregate_attrs = aggregate_team_frames(
        original,
        scope_type=scope_type,
        scope_value=scope_value,
        preserve_action_plan_observations=True,
    )
    portfolio: Dict[str, pd.DataFrame] = {}
    for source_key in _FRAME_KEYS:
        parts: List[pd.DataFrame] = []
        for member, bundle in sorted(original.items(), key=lambda item: str(item[0]).casefold()):
            if not isinstance(bundle, Mapping):
                continue
            frame = bundle.get(source_key)
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            copy = frame.copy()
            copy["CSSM"] = (
                "Unassigned / Portfolio"
                if str(member).startswith("__")
                else str(member)
            )
            parts.append(copy)
        combined = (
            pd.concat(parts, ignore_index=True, sort=False)
            if parts
            else pd.DataFrame()
        )
        combined.attrs.update(
            dict(getattr(aggregate_attrs.get(source_key), "attrs", {}) or {})
        )
        portfolio[source_key] = combined

    projected = partition_portfolio_by_member(
        subscriptions=portfolio["subscriptions"],
        action_plans=portfolio["action_plans"],
        adoption_barriers=portfolio["adoption_barriers"],
        customer_pulse=portfolio["customer_pulse"],
        tac_cases=portfolio["tac_cases"],
        success_priorities=portfolio["success_priorities"],
        fallback_member="Unassigned / Portfolio",
    )
    expected_members = {member.strip().casefold() for member in named_members}
    projected_members = {
        str(member).strip().casefold()
        for member in projected
        if not str(member).startswith("__")
    }
    if projected_members != expected_members:
        logger.warning(
            "Scoped attribution projection withheld because subscription identities "
            "did not reconcile to the configured report roster"
        )
        return team_data
    return projected


def _row_tokens(row: Mapping[str, Any], candidates: Sequence[str], normalizer: Any) -> set:
    return {token for column in candidates if (token := normalizer(row.get(column)))}


def _r153_alias_group_key(display: object) -> str:
    """Round 153 / Tier 4: a stable key for a customer's alias group, or "".

    ``alias_join_keys_for_name`` returns the whole Round 132 alias group for a
    registered name (e.g. every NYU variant maps to the same 5-key set) and a
    single self-fold key for everyone else.  A name is registry-grouped iff
    that set has more than one member, so we return a stable group key ONLY in
    that case.  Non-registered names return "" and therefore keep the existing
    suffix-sensitive exact-label behaviour untouched -- which is why this fix
    causes zero movement in any fixture whose customers are not in the
    registry (the shipped acceptance fixture is Acme/Beta/Gamma).
    """
    try:
        keys = alias_join_keys_for_name(display)
    except Exception:  # noqa: BLE001 - never break identity resolution
        return ""
    keys = {str(k).strip() for k in (keys or set()) if str(k).strip()}
    if len(keys) <= 1:
        return ""
    return min(keys)


def _r153_detect_split_alias_groups(
    identities: Sequence[Mapping[str, Any]],
) -> List[str]:
    """Round 153 / Tier 4: error strings for any split Round 132 alias group.

    The customer universe must never resolve one alias group to two
    identities.  Inert for non-registered names (no group key), so it can
    only fire on a genuine regression of the alias collapse.
    """
    out: List[str] = []
    seen_groups: Dict[str, Any] = {}
    for identity in identities or ():
        for key in identity.get("exact_name_keys") or ():
            group = _r153_alias_group_key(key)
            if not group:
                continue
            prior = seen_groups.get(group)
            if prior is not None and prior != identity.get("identity_key"):
                out.append(
                    "customer alias group split across two identities: "
                    f"{identity.get('base_label')!r} shares alias group {group!r} "
                    "with another identity; expected one canonical customer"
                )
            seen_groups[group] = identity.get("identity_key")
    return out


def _canonical_customer_identities(
    frames: Mapping[str, pd.DataFrame],
) -> List[Dict[str, Any]]:
    """Build the ID-first customer universe used by every decision metric.

    Stable account IDs are authoritative.  Records without an ID join an
    authoritative identity by exact normalized label, or by a fuzzy alias
    only when that alias names exactly one ID-backed identity.  Remaining
    records form suffix-sensitive exact-label identities.  This prevents
    legal-suffix folding from merging distinct accounts while still allowing
    a guarded ``Acme Incorporated`` -> ``Acme Inc`` source join.
    """

    source_order = (
        "subscriptions",
        "action_plans",
        "adoption_barriers",
        "customer_pulse",
        "tac_cases",
        "success_priorities",
    )
    observations: List[Dict[str, Any]] = []
    for source_rank, source_key in enumerate(source_order):
        frame = frames.get(source_key)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for row_rank, (_, row) in enumerate(frame.iterrows()):
            account_ids = tuple(sorted(_row_tokens(row, _ACCOUNT_ID_COLUMNS, _account_match_key)))
            display = _first_value(row, _CUSTOMER_COLUMNS)
            exact_name = _customer_match_key(display)
            if not account_ids and not exact_name:
                continue
            observations.append(
                {
                    "source_rank": source_rank,
                    "row_rank": row_rank,
                    "account_ids": account_ids,
                    "display": normalize_customer_name(display) if display else "",
                    "exact_name": exact_name,
                    "fuzzy_name": _customer_fuzzy_join_key(display),
                }
            )

    parent: Dict[str, str] = {}

    def find(token: str) -> str:
        parent.setdefault(token, token)
        while parent[token] != token:
            parent[token] = parent[parent[token]]
            token = parent[token]
        return token

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        winner, loser = sorted((left_root, right_root))
        parent[loser] = winner

    for observation in observations:
        ids = observation["account_ids"]
        for account_id in ids:
            find(account_id)
        for account_id in ids[1:]:
            union(ids[0], account_id)

    stable: Dict[str, Dict[str, Any]] = {}
    for observation in observations:
        ids = observation["account_ids"]
        if not ids:
            continue
        root = find(ids[0])
        identity = stable.setdefault(
            root,
            {
                "account_ids": set(),
                "exact_name_keys": set(),
                "fuzzy_name_keys": set(),
                "alias_group_keys": set(),
                "display_candidates": [],
            },
        )
        identity["account_ids"].update(ids)
        if observation["exact_name"]:
            identity["exact_name_keys"].add(observation["exact_name"])
        if observation["fuzzy_name"]:
            identity["fuzzy_name_keys"].add(observation["fuzzy_name"])
        alias_group_key = _r153_alias_group_key(observation["display"])
        if alias_group_key:
            identity["alias_group_keys"].add(alias_group_key)
        if observation["display"]:
            identity["display_candidates"].append(
                (observation["source_rank"], observation["row_rank"], observation["display"])
            )

    # Co-occurring ID aliases may have been unioned after an earlier
    # observation.  Consolidate by final root before resolving name-only rows.
    consolidated: Dict[str, Dict[str, Any]] = {}
    for old_root, identity in stable.items():
        root = find(old_root)
        target = consolidated.setdefault(
            root,
            {
                "account_ids": set(),
                "exact_name_keys": set(),
                "fuzzy_name_keys": set(),
                "alias_group_keys": set(),
                "display_candidates": [],
            },
        )
        for field in (
            "account_ids",
            "exact_name_keys",
            "fuzzy_name_keys",
            "alias_group_keys",
        ):
            target[field].update(identity[field])
        target["display_candidates"].extend(identity["display_candidates"])
    stable = consolidated

    def stable_candidates(field: str, token: str) -> List[str]:
        if not token:
            return []
        return sorted(root for root, identity in stable.items() if token in identity[field])

    name_only: Dict[str, Dict[str, Any]] = {}
    for observation in observations:
        if observation["account_ids"] or not observation["exact_name"]:
            continue
        candidates = stable_candidates("exact_name_keys", observation["exact_name"])
        if not candidates and observation["fuzzy_name"]:
            candidates = stable_candidates("fuzzy_name_keys", observation["fuzzy_name"])
        alias_group_key = _r153_alias_group_key(observation["display"] or observation["exact_name"])
        if not candidates and alias_group_key:
            # Registered aliases are authoritative cross-source joins, but
            # never stronger than stable identity.  Attach an ID-less alias
            # only when the registry names exactly one ID-backed customer.
            # If two account IDs expose the same alias group, ambiguity keeps
            # those ID-backed customers distinct and the row is not guessed.
            candidates = stable_candidates("alias_group_keys", alias_group_key)
        if len(candidates) == 1:
            identity = stable[candidates[0]]
            identity["exact_name_keys"].add(observation["exact_name"])
            if observation["fuzzy_name"]:
                identity["fuzzy_name_keys"].add(observation["fuzzy_name"])
            if alias_group_key:
                identity["alias_group_keys"].add(alias_group_key)
            if observation["display"]:
                identity["display_candidates"].append(
                    (observation["source_rank"], observation["row_rank"], observation["display"])
                )
            continue
        if len(candidates) > 1:
            # An ID-less row cannot be safely assigned when two authoritative
            # accounts expose the same exact label.
            continue
        # Round 153 / Tier 4: collapse ID-less rows that name the same
        # Round 132 alias group (e.g. "NYU MEDICAL CENTER" and "NYU LANGONE
        # HEALTH SYSTEMS").  Pre-fix each formed its own name-only identity,
        # so the organisation was counted twice, its evidence split across two
        # partial slices (understating BOTH risk scores), and duplicated in
        # Account_Summary.  Key by the alias group when one exists; otherwise
        # fall back to the suffix-sensitive exact label, so non-registered
        # customers are entirely unaffected.
        name_only_key = f"aliasgroup:{alias_group_key}" if alias_group_key else observation["exact_name"]
        identity = name_only.setdefault(
            name_only_key,
            {
                "account_ids": set(),
                "exact_name_keys": {observation["exact_name"]},
                "fuzzy_name_keys": set(),
                "display_candidates": [],
            },
        )
        identity["exact_name_keys"].add(observation["exact_name"])
        if observation["fuzzy_name"]:
            identity["fuzzy_name_keys"].add(observation["fuzzy_name"])
        if observation["display"]:
            identity["display_candidates"].append(
                (observation["source_rank"], observation["row_rank"], observation["display"])
            )

    identities: List[Dict[str, Any]] = []
    for root, identity in sorted(stable.items()):
        candidates = sorted(identity["display_candidates"])
        base_label = candidates[0][2] if candidates else f"Account {sorted(identity['account_ids'])[0]}"
        identities.append(
            {
                "identity_key": "account:" + "|".join(sorted(identity["account_ids"])),
                "base_label": base_label,
                "account_ids": tuple(sorted(identity["account_ids"])),
                "exact_name_keys": tuple(sorted(identity["exact_name_keys"])),
                "fuzzy_name_keys": tuple(sorted(identity["fuzzy_name_keys"])),
            }
        )
    for exact_name, identity in sorted(name_only.items()):
        candidates = sorted(identity["display_candidates"])
        base_label = candidates[0][2] if candidates else exact_name
        identities.append(
            {
                "identity_key": f"name:{exact_name}",
                "base_label": base_label,
                "account_ids": (),
                "exact_name_keys": (exact_name,),
                "fuzzy_name_keys": tuple(sorted(identity["fuzzy_name_keys"])),
            }
        )

    duplicate_labels: Dict[str, int] = {}
    for identity in identities:
        key = _customer_match_key(identity["base_label"])
        duplicate_labels[key] = duplicate_labels.get(key, 0) + 1
    for identity in identities:
        label = identity["base_label"]
        if duplicate_labels.get(_customer_match_key(label), 0) > 1:
            discriminator = identity["account_ids"][0] if identity["account_ids"] else identity["identity_key"]
            label = f"{label} ({discriminator})"
        identity["label"] = label
    return sorted(identities, key=lambda item: (str(item["label"]).casefold(), item["identity_key"]))


def _customer_mask(frame: pd.DataFrame, customer: str) -> pd.Series:
    """Exact-label mask retained for narrow compatibility helpers."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.Series(False, index=frame.index if isinstance(frame, pd.DataFrame) else None)
    target = _customer_match_key(customer)
    mask = pd.Series(False, index=frame.index)
    for column in _CUSTOMER_COLUMNS:
        if column in frame.columns:
            mask = mask | frame[column].map(_customer_match_key).eq(target)
    return mask


def _customers_from_frames(frames: Mapping[str, pd.DataFrame]) -> List[str]:
    return [identity["label"] for identity in _canonical_customer_identities(frames)]


def _customer_frame_for_identity(
    frame: pd.DataFrame,
    identity: Mapping[str, Any],
    identities: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    source_attrs = dict(getattr(frame, "attrs", {}) or {})
    if frame.empty:
        empty = frame.iloc[0:0].copy()
        empty.attrs.update(source_attrs)
        return empty

    target_key = str(identity.get("identity_key") or "")
    target_accounts = set(identity.get("account_ids") or ())
    exact_owners: Dict[str, set] = {}
    fuzzy_stable_owners: Dict[str, set] = {}
    for candidate in identities:
        candidate_key = str(candidate.get("identity_key") or "")
        for name_key in candidate.get("exact_name_keys") or ():
            exact_owners.setdefault(str(name_key), set()).add(candidate_key)
        if candidate.get("account_ids"):
            for fuzzy_key in candidate.get("fuzzy_name_keys") or ():
                fuzzy_stable_owners.setdefault(str(fuzzy_key), set()).add(candidate_key)

    selected: List[Any] = []
    for index, row in frame.iterrows():
        row_accounts = _row_tokens(row, _ACCOUNT_ID_COLUMNS, _account_match_key)
        if row_accounts:
            if row_accounts & target_accounts:
                selected.append(index)
            continue
        display = _first_value(row, _CUSTOMER_COLUMNS)
        exact_name = _customer_match_key(display)
        exact_candidates = exact_owners.get(exact_name, set()) if exact_name else set()
        if len(exact_candidates) == 1:
            if target_key in exact_candidates:
                selected.append(index)
            continue
        if exact_candidates:
            continue
        fuzzy_name = _customer_fuzzy_join_key(display)
        fuzzy_candidates = fuzzy_stable_owners.get(fuzzy_name, set()) if fuzzy_name else set()
        if len(fuzzy_candidates) == 1 and target_key in fuzzy_candidates:
            selected.append(index)
    result = frame.loc[selected].copy().reset_index(drop=True)
    result.attrs.update(source_attrs)
    return result


def _identity_from_profile(customer: str, profile: Mapping[str, Any]) -> Dict[str, Any]:
    identity = profile.get("canonical_identity")
    if isinstance(identity, Mapping):
        return dict(identity)
    return {
        "identity_key": f"name:{_customer_match_key(customer)}",
        "label": customer,
        "account_ids": (),
        "exact_name_keys": (_customer_match_key(customer),),
        "fuzzy_name_keys": (_customer_fuzzy_join_key(customer),),
    }


def _customer_incidents(
    incidents: Optional[Sequence[Mapping[str, Any]]],
    customer: str,
    *,
    identity: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return only incidents explicitly tagged to one canonical customer.

    Status-page rows without a customer/account tag remain portfolio context;
    they are not smeared into every customer's weighted risk component.
    """

    # Round 148: preserve the established report-scoring contract.  Explicitly
    # customer-tagged feeds are sliced; portfolio status incidents without any
    # customer field flow to every profile and remain formula-capped.
    if isinstance(incidents, pd.DataFrame):
        records = incidents.to_dict("records")
    else:
        records = [dict(item) for item in (incidents or []) if isinstance(item, Mapping)]
    if not records or not customer:
        return records
    name_fields = ("customer_name", "BU_NAME", "Customer", "Customer Name")
    account_fields = (
        "customer_id",
        "ACCOUNT_ID_C",
        "ACCOUNT_ID",
        "account_id",
        "Customer ID",
    )
    tagging_fields = (*name_fields, *account_fields)
    if not any(any(_clean_token(record.get(field)) for field in tagging_fields) for record in records):
        return []
    target_accounts = {
        _account_match_key(value) for value in ((identity or {}).get("account_ids") or ()) if _account_match_key(value)
    }
    return [
        record
        for record in records
        if any(
            _clean_token(record.get(field))
            # Round 148: retain the Round 132 alias-aware cross-source join
            # contract for customer-tagged incident feeds.
            and customer_names_match(str(record[field]), customer)
            for field in name_fields
        )
        or bool(
            target_accounts
            & {
                _account_match_key(record.get(field))
                for field in account_fields
                if _account_match_key(record.get(field))
            }
        )
    ]


def _build_risk_profiles(
    frames: Mapping[str, pd.DataFrame],
    *,
    days: int,
    as_of: Any,
    external_incidents: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    identities = _canonical_customer_identities(frames)
    if isinstance(external_incidents, pd.DataFrame):
        incident_state = cm.source_data_state(external_incidents)
    elif external_incidents is None:
        incident_state = {"state": "unavailable", "detail": "incident source not supplied"}
    else:
        incident_state = cm.source_data_state(pd.DataFrame(list(external_incidents)))
    profiles: Dict[str, Dict[str, Any]] = {}
    for identity in identities:
        customer = str(identity["label"])
        profile = compute_customer_risk_profile(
            customer,
            customer_ab=_customer_frame_for_identity(
                frames.get("adoption_barriers", pd.DataFrame()), identity, identities
            ),
            customer_csone=_customer_frame_for_identity(frames.get("tac_cases", pd.DataFrame()), identity, identities),
            customer_pulse=_customer_frame_for_identity(
                frames.get("customer_pulse", pd.DataFrame()), identity, identities
            ),
            customer_action_plans=_customer_frame_for_identity(
                frames.get("action_plans", pd.DataFrame()), identity, identities
            ),
            customer_subs=_customer_frame_for_identity(
                frames.get("subscriptions", pd.DataFrame()), identity, identities
            ),
            ext_incidents=_customer_incidents(
                external_incidents,
                customer,
                identity=identity,
            ),
            incident_source_state=str(incident_state.get("state") or "unavailable"),
            incident_source_detail=str(incident_state.get("detail") or ""),
            recent_window_days=days,
            as_of=as_of,
        )
        profile["canonical_identity"] = dict(identity)
        profiles[customer] = profile
    return profiles


def _priority_rank(value: Any) -> int:
    label = str(normalize_priority_label(value) or "Unknown").upper()
    return {"P1": 0, "CRITICAL": 0, "P2": 1, "HIGH": 1, "P3": 2, "MEDIUM": 2, "P4": 3, "LOW": 3}.get(label, 4)


def _prioritized_action_plan_rows(lifecycle: Mapping[str, Any], limit: int) -> List[List[Any]]:
    frame = lifecycle.get("records")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    use = frame.loc[
        frame["AdoptIQ_Status_Bucket"].isin(["Overdue", "Blocked / On Hold", "Due Soon", "Open", "Unknown"])
    ].copy()
    if use.empty:
        return []
    status_rank = {"Overdue": 0, "Blocked / On Hold": 1, "Due Soon": 2, "Open": 3, "Unknown": 4}
    priority_column = _first_column(use, _PRIORITY_COLUMNS)
    use["__status_rank"] = use["AdoptIQ_Status_Bucket"].map(status_rank).fillna(9)
    use["__priority_rank"] = use[priority_column].map(_priority_rank) if priority_column else 4
    use["__due_sort"] = pd.to_datetime(use["AdoptIQ_Due_Date"], errors="coerce", utc=True)
    use["__id_sort"] = use["AdoptIQ_Record_ID"].fillna("").astype(str)
    use = use.sort_values(
        ["__status_rank", "__priority_rank", "__due_sort", "__id_sort"],
        kind="stable",
        na_position="last",
    ).head(max(int(limit), 1))
    rows: List[List[Any]] = []
    for _, row in use.iterrows():
        due = row.get("AdoptIQ_Due_Date")
        due_text = "Unavailable" if pd.isna(due) else pd.Timestamp(due).strftime("%Y-%m-%d")
        age = row.get("AdoptIQ_Age_Days")
        age_text = "Unavailable" if pd.isna(age) else str(int(age))
        rows.append(
            [
                row.get("AdoptIQ_Record_ID") or "Missing source ID",
                _bounded_manager_text(
                    _first_value(row, _CUSTOMER_COLUMNS),
                    limit=100,
                    fallback="Account unavailable",
                ),
                _bounded_manager_text(
                    _first_value(row, _OWNER_COLUMNS),
                    limit=90,
                    fallback="Owner unavailable",
                ),
                _bounded_manager_text(
                    row.get("AdoptIQ_Title"),
                    limit=140,
                    fallback="Title unavailable",
                ),
                _action_plan_status_display(row),
                "Due date not provided" if due_text == "Unavailable" else due_text,
                "Created date not provided" if age_text == "Unavailable" else age_text,
                _bounded_manager_text(
                    _first_value(row, _NEXT_ACTION_COLUMNS),
                    limit=200,
                    fallback="Next action unavailable",
                ),
                _bounded_manager_text(
                    _first_value(row, _PRIORITY_COLUMNS),
                    limit=60,
                    fallback="Priority not provided",
                ),
            ]
        )
    return rows


_COMPLETE_DERIVED_SOURCE_STATES = frozenset({"available", "zero"})


def _derived_source_state(values: Iterable[Any]) -> str:
    """Return one fail-closed state for a value derived from several sources."""

    states = [str(value or "unavailable").strip().casefold() for value in values]
    if not states:
        return "unavailable"
    if all(state in _COMPLETE_DERIVED_SOURCE_STATES for state in states):
        return "zero" if all(state == "zero" for state in states) else "available"
    if all(state in {"failed", "unavailable", "unknown"} for state in states):
        return "unavailable"
    return "partial"


def _derived_value(value: Any, source_state: str) -> Any:
    """Expose a computed value only when all of its contributors are complete."""

    return value if source_state in _COMPLETE_DERIVED_SOURCE_STATES else None


def _retained_count_value(value: Any, source_state: str) -> Any:
    """Retain an exact scoped count when rows exist under incomplete coverage.

    A partial or stale source cannot support a complete total, but the count of
    rows that survived authorization, identity, technology, and date-window
    gates is still exact evidence.  Preserve that value for lower-bound display;
    failed or unavailable sources remain withheld.
    """

    normalized = str(source_state or "unavailable").strip().casefold()
    if normalized in {"available", "zero", "partial", "stale"}:
        return value
    return None


def _build_member_summary(
    team_data: Mapping[str, Mapping[str, Any]],
    *,
    as_of: Any,
    limit: Optional[int],
) -> List[List[Any]]:
    ranked_rows: List[Tuple[Tuple[Any, ...], List[Any]]] = []
    for member in sorted(team_data):
        bundle = team_data.get(member) or {}
        if not isinstance(bundle, Mapping):
            continue
        member_label = "Unassigned / Portfolio" if str(member).startswith("__") else str(member)
        aps = bundle.get("action_plans") if isinstance(bundle.get("action_plans"), pd.DataFrame) else pd.DataFrame()
        lifecycle = cm.build_action_plan_lifecycle(aps, as_of=as_of)
        customers = len(
            _canonical_customer_identities(
                {
                    key: (bundle.get(key) if isinstance(bundle.get(key), pd.DataFrame) else pd.DataFrame())
                    for key in _FRAME_KEYS
                }
            )
        )
        barriers_frame = bundle.get("adoption_barriers")
        tac_frame = bundle.get("tac_cases")
        barriers = cm.count_total_barriers(barriers_frame)
        tac_cases = cm.count_total_tac(tac_frame)

        contributing_states = {source: cm.source_data_state(bundle.get(source))["state"] for source in _FRAME_KEYS}
        customer_state = _derived_source_state(contributing_states.values())
        action_plan_state = str(lifecycle.get("source_state") or "unavailable").casefold()
        barrier_state = str(cm.source_data_state(barriers_frame)["state"] or "unavailable").casefold()
        tac_state = str(cm.source_data_state(tac_frame)["state"] or "unavailable").casefold()
        row = [
            member_label,
            _retained_count_value(customers, customer_state),
            _retained_count_value(lifecycle["open"], action_plan_state),
            _retained_count_value(lifecycle["overdue"], action_plan_state),
            _retained_count_value(barriers, barrier_state),
            _retained_count_value(tac_cases, tac_state),
            customer_state,
            action_plan_state,
            action_plan_state,
            barrier_state,
            tac_state,
        ]
        ranked_rows.append(
            (
                (
                    -int(row[3] or 0),
                    -int(row[2] or 0),
                    -int(row[4] or 0),
                    -int(row[5] or 0),
                    member_label.casefold(),
                ),
                row,
            )
        )
    rows = [row for _, row in sorted(ranked_rows, key=lambda item: item[0])]
    if limit is None:
        return rows
    return rows[: max(int(limit), 1)]


def _build_account_summary(
    risk_profiles: Mapping[str, Mapping[str, Any]],
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: Any,
    limit: Optional[int],
) -> List[List[Any]]:
    identities = [_identity_from_profile(customer, profile) for customer, profile in risk_profiles.items()]
    risk_source_state = _derived_source_state(
        cm.source_data_state(frames.get(source))["state"]
        for source in (
            "subscriptions",
            "action_plans",
            "adoption_barriers",
            "customer_pulse",
            "tac_cases",
        )
    )
    action_plan_state = str(cm.source_data_state(frames.get("action_plans"))["state"] or "unavailable").casefold()
    barrier_state = str(cm.source_data_state(frames.get("adoption_barriers"))["state"] or "unavailable").casefold()
    tac_state = str(cm.source_data_state(frames.get("tac_cases"))["state"] or "unavailable").casefold()
    if risk_source_state in _COMPLETE_DERIVED_SOURCE_STATES:
        ranked = sorted(
            risk_profiles.items(),
            key=lambda item: (
                -float(item[1].get("risk_score_0_100", 0.0) or 0.0),
                item[0],
            ),
        )
    else:
        # Ranking by a withheld score would still reveal information about the
        # incomplete computation.  Fall back to the stable public label.
        ranked = sorted(risk_profiles.items(), key=lambda item: item[0].casefold())
    rows: List[List[Any]] = []
    selected = ranked if limit is None else ranked[: max(int(limit), 1)]
    for customer, profile in selected:
        identity = _identity_from_profile(customer, profile)
        customer_ap = _customer_frame_for_identity(frames.get("action_plans", pd.DataFrame()), identity, identities)
        lifecycle = cm.build_action_plan_lifecycle(customer_ap, as_of=as_of)
        rows.append(
            [
                customer,
                _derived_value(profile.get("risk_band", "UNKNOWN"), risk_source_state),
                _derived_value(profile.get("risk_score_0_100", 0.0), risk_source_state),
                _retained_count_value(lifecycle["open"], action_plan_state),
                _retained_count_value(lifecycle["overdue"], action_plan_state),
                _retained_count_value(
                    cm.count_critical_barriers(
                        _customer_frame_for_identity(
                            frames.get("adoption_barriers", pd.DataFrame()), identity, identities
                        )
                    ),
                    barrier_state,
                ),
                _retained_count_value(
                    cm.count_total_tac(
                        _customer_frame_for_identity(frames.get("tac_cases", pd.DataFrame()), identity, identities)
                    ),
                    tac_state,
                ),
                risk_source_state,
                risk_source_state,
                action_plan_state,
                action_plan_state,
                barrier_state,
                tac_state,
            ]
        )
    return rows


def _source_coverage(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for key, sheet in _FRAME_KEYS.items():
        frame = frames.get(key)
        state = cm.source_data_state(frame)
        if state["state"] == "available":
            informational_detail = str(
                (getattr(frame, "attrs", {}) or {}).get("source_mode_detail") or ""
            ).strip()
            if informational_detail:
                state = {**state, "detail": informational_detail}
        count = None
        if state["state"] not in {"failed", "unavailable"}:
            count = cm.count_distinct_records_by_id(frame, id_candidates=_ID_CANDIDATES[key])
        rows.append(
            {
                "Source_Sheet": sheet,
                "Source_State": state["state"],
                "Record_Count": count,
                "Detail": state["detail"],
            }
        )
    return pd.DataFrame(rows)


def _decorate_tac_case_type_quality(frame: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Publish explainable TAC type coverage without retaining classifier text.

    The canonical classifier can separate break/fix and provisioning requests
    when the source exposes enough evidence.  A row outside those two classes
    is not an arbitrary ``Unknown``: it means the available case-type/title/
    problem fields did not support either classification.  Preserve that exact
    reason beside each row and quantify coverage for report provenance.
    """

    use = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    attrs = dict(getattr(frame, "attrs", {}) or {})
    if use.empty:
        use["case_type_class"] = pd.Series(dtype="object")
        use["case_type_data_quality"] = pd.Series(dtype="object")
        use.attrs.update(attrs)
        return use, {
            "total": 0,
            "classified": 0,
            "not_derivable": 0,
            "coverage_percent": None,
            "break_fix": 0,
            "provisioning": 0,
        }

    existing = use.get(
        "case_type_class",
        pd.Series("", index=use.index, dtype="object"),
    ).fillna("").astype(str).str.strip().str.casefold()
    normalized_existing = existing.replace(
        {
            "break-fix": "break_fix_technical",
            "break fix": "break_fix_technical",
            "technical": "break_fix_technical",
            "provisioning": "provisioning_request",
        }
    )
    derived = use.apply(classify_case_type, axis=1)
    valid = {"break_fix_technical", "provisioning_request"}
    resolved = normalized_existing.where(normalized_existing.isin(valid), derived)
    resolved = resolved.where(resolved.isin(valid), "unknown")
    use["case_type_class"] = resolved
    use["case_type_data_quality"] = resolved.map(
        {
            "break_fix_technical": (
                "Classified from available case type, title, or problem evidence"
            ),
            "provisioning_request": (
                "Classified from available case type, title, or problem evidence"
            ),
            "unknown": (
                "Not derivable: available case type, title, and problem evidence "
                "did not support break/fix or provisioning classification"
            ),
        }
    )
    counts = resolved.value_counts().to_dict()
    classified = int(sum(int(counts.get(label, 0)) for label in valid))
    total = int(len(use))
    use.attrs.update(attrs)
    return use, {
        "total": total,
        "classified": classified,
        "not_derivable": int(total - classified),
        "coverage_percent": round((classified / total) * 100, 1) if total else None,
        "break_fix": int(counts.get("break_fix_technical", 0)),
        "provisioning": int(counts.get("provisioning_request", 0)),
    }


_DECISION_SIGNAL_SPECS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    [
        (
            "subscriptions",
            {
                "sheet": "Subscriptions",
                "title": (
                    "PRODUCT_NAME",
                    "SUB_TECHNOLOGY_C",
                    "TECHNOLOGY_C",
                    "Subscription Name",
                ),
                "status": ("STATUS_C", "Status", "SUBSCRIPTION_STATUS"),
                "priority": ("RENEWAL_RISK_CATEGORY", "Renewal Risk Category"),
                "date": (
                    "RENEWAL_DATE_C",
                    "END_DATE_C",
                    "SUBSCRIPTION_END_DATE_C",
                    "END_DATE",
                    "Subscription End Date",
                ),
                "date_label": "renewal/end",
                "date_mode": "nearest",
                "action": ("Validate renewal timing, adoption evidence, and ownership for this subscription."),
            },
        ),
        (
            "action_plans",
            {
                "sheet": "Action_Plans",
                "title": (
                    "AdoptIQ_Title",
                    "ACTION_PLAN_TITLE_C",
                    "SUBJECT_C",
                    "Title",
                ),
                "status": ("AdoptIQ_Status_Bucket", "STATUS_C", "Status"),
                "priority": _PRIORITY_COLUMNS,
                "date": ("AdoptIQ_Due_Date", "DUE_DATE_C", "Due Date"),
                "date_label": "due",
                "date_mode": "nearest",
                "recorded_action": _NEXT_ACTION_COLUMNS,
                "action": "Confirm an accountable owner and complete the recorded next step.",
            },
        ),
        (
            "adoption_barriers",
            {
                "sheet": "Adoption_Barriers",
                "title": ("SUBJECT_C", "TITLE_C", "Title", "DESCRIPTION_C"),
                "status": ("AB_STATUS_C", "STATUS_C", "Status"),
                "priority": ("SEVERITY_C", "PRIORITY_C", "Severity", "Priority"),
                "date": ("DUE_DATE_C", "OPEN_DATE_C", "Created Date"),
                "date_label": "dated",
                "date_mode": "latest",
                "recorded_action": _NEXT_ACTION_COLUMNS,
                "action": "Assign an owner and dated mitigation for this adoption barrier.",
            },
        ),
        (
            "customer_pulse",
            {
                "sheet": "Customer_Pulse",
                "title": (
                    "PULSE_RATING__C",
                    "CUSTOMER_PULSE__C",
                    "Pulse Rating",
                    "Customer Pulse",
                ),
                "status": ("STATUS__C", "STATUS_C", "Status"),
                "priority": (),
                "date": (
                    "PULSE_DATE_C",
                    "CREATED_DATE_C",
                    "Created Date",
                    "Date",
                ),
                "date_label": "pulse date",
                "date_mode": "latest",
                "recorded_action": ("COMMENTS__C", "COMMENTS_C", "Comments"),
                "action": "Validate the pulse driver with the customer and close the feedback loop.",
            },
        ),
        (
            "tac_cases",
            {
                "sheet": "TAC_Cases",
                "title": (
                    "Title",
                    "Problem Description",
                    "PROBLEM_DESCRIPTION",
                    "SUBJECT",
                ),
                "status": ("case_status_norm", "Case Status", "STATUS"),
                "priority": (
                    "case_priority_norm",
                    "Severity",
                    "Priority",
                    "SEVERITY",
                ),
                "date": ("open_date", "Date/Time Opened", "OPEN_DATE"),
                "date_label": "opened",
                "date_mode": "latest",
                "action": "Confirm case ownership, the next customer update, and any escalation path.",
            },
        ),
        (
            "success_priorities",
            {
                "sheet": "Success_Priorities",
                "title": (
                    "SUCCESS_PRIORITY_TITLE__C",
                    "SUBJECT_C",
                    "Title",
                    "NAME",
                ),
                "status": ("STATUS__C", "STATUS_C", "Status"),
                "priority": ("PRIORITY_C", "Priority"),
                "date": ("DUE_DATE_C", "CREATED_DATE_C", "Created Date"),
                "date_label": "dated",
                "date_mode": "latest",
                "recorded_action": _NEXT_ACTION_COLUMNS,
                "action": "Align the next success-plan action, owner, and date to this priority.",
            },
        ),
        (
            "external_incidents",
            {
                "sheet": "External_Incidents",
                "title": ("title", "name", "summary", "incident_number"),
                "status": ("status", "incident_status"),
                "priority": ("impact_level", "severity", "impact"),
                "date": ("published", "updated_at", "created_at", "date"),
                "date_label": "published",
                "date_mode": "latest",
                "action": (
                    "Assess selected-scope impact and publish mitigation; do not infer impact from an incident alone."
                ),
            },
        ),
        (
            "external_bugs",
            {
                "sheet": "External_Bugs",
                "title": ("title", "headline", "summary", "bug_id"),
                "status": ("status", "bug_status"),
                "priority": ("severity", "priority"),
                "date": ("discovered_at", "published", "updated_at", "date"),
                "date_label": "discovered",
                "date_mode": "latest",
                "action": (
                    "Check whether affected features intersect scoped subscriptions and track remediation; do not assume impact."
                ),
            },
        ),
    ]
)


def _decision_signal_text(value: Any, *, limit: int = 110) -> str:
    """Return compact, plain source text suitable for a decision table."""

    text = _r153_strip_source_chrome(_clean_token(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    shortened = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return (shortened or text[: limit - 1]).rstrip() + "…"


def _build_canonical_defect_correlations(
    frames: Mapping[str, pd.DataFrame],
    *,
    external_bugs: pd.DataFrame,
    scope_type: str,
    scope_value: str,
) -> Dict[str, Any]:
    """Build exact scoped CSC evidence using the shared correlation helper."""

    from defect_correlation import build_defect_correlation_bundle

    identities = _canonical_customer_identities(frames)

    def resolve_identity(row: Mapping[str, Any], _source_sheet: str) -> Optional[Dict[str, str]]:
        row_accounts = _row_tokens(row, _ACCOUNT_ID_COLUMNS, _account_match_key)
        candidates = [identity for identity in identities if row_accounts & set(identity.get("account_ids") or ())]
        if not candidates:
            display = _first_value(row, _CUSTOMER_COLUMNS)
            exact_key = _customer_match_key(display)
            candidates = [
                identity
                for identity in identities
                if exact_key and exact_key in set(identity.get("exact_name_keys") or ())
            ]
        if not candidates:
            display = _first_value(row, _CUSTOMER_COLUMNS)
            fuzzy_key = _customer_fuzzy_join_key(display)
            candidates = [
                identity
                for identity in identities
                if fuzzy_key and identity.get("account_ids") and fuzzy_key in set(identity.get("fuzzy_name_keys") or ())
            ]
        if len(candidates) != 1:
            return None
        identity = candidates[0]
        accounts = tuple(identity.get("account_ids") or ())
        return {
            "identity_key": str(identity.get("identity_key") or ""),
            "account_id": str(accounts[0]) if accounts else "",
            "customer_name": str(identity.get("label") or identity.get("base_label") or ""),
        }

    bundle = build_defect_correlation_bundle(
        frames.get("tac_cases"),
        frames.get("adoption_barriers"),
        external_bugs,
        identity_resolver=resolve_identity,
    )
    coverage = dict(bundle.get("coverage") or {})
    identity_resolution = dict(coverage.get("identity_resolution") or {})
    visible_records: List[Mapping[str, Any]] = []
    delivery_quarantined_records = 0
    delivery_quarantined_observations = 0
    for record in bundle.get("records") or []:
        identity_key = _clean_token(record.get("identity_key"))
        customer = _clean_token(record.get("customer_name"))
        if (
            not identity_key
            or identity_key.casefold() == "unknown"
            or not customer
            or customer.casefold() == "unknown"
        ):
            delivery_quarantined_records += 1
            delivery_quarantined_observations += max(
                int(record.get("parent_record_count") or 0),
                1,
            )
            continue
        visible_records.append(record)
    if delivery_quarantined_records:
        identity_resolution.update(
            {
                "state": "partial",
                "delivery_quarantined_record_count": delivery_quarantined_records,
                "delivery_quarantined_observation_count": delivery_quarantined_observations,
                "detail": (
                    f"{delivery_quarantined_records} defect-correlation record(s), representing "
                    f"{delivery_quarantined_observations} source row(s), were withheld because "
                    "no canonical customer identity was available"
                ),
            }
        )
    coverage["identity_resolution"] = identity_resolution
    bundle["coverage"] = coverage
    bundle["records"] = visible_records
    source_states = [
        str((metadata or {}).get("state") or "unavailable")
        for metadata in (coverage.get("sources") or {}).values()
        if isinstance(metadata, Mapping)
    ]
    identity_state = str(identity_resolution.get("state") or "").casefold()
    if identity_state:
        source_states.append(identity_state)
    correlation_state = _combined_decision_insight_state(source_states)
    if not source_states:
        correlation_state = "unavailable"
    rows: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    source_frames = {
        "TAC_Cases": frames.get("tac_cases", pd.DataFrame()),
        "Adoption_Barriers": frames.get("adoption_barriers", pd.DataFrame()),
    }
    for position, record in enumerate(visible_records):
        csc_id = _clean_token(record.get("csc_id"))
        customer = _clean_token(record.get("customer_name"))
        identity_key = _clean_token(record.get("identity_key"))
        evidence_key = evidence_entity_key(
            "defect_correlation",
            f"{identity_key}|{csc_id}",
        )
        verified = bool(record.get("verified_external_match"))
        parent_count = int(record.get("parent_record_count") or 0)
        status = _clean_token(record.get("verified_external_status"))
        severity = _clean_token(record.get("verified_external_severity"))
        version = _clean_token(record.get("verified_external_version"))
        verified_detail = "; ".join(
            value
            for value in (
                f"status {status}" if status else "",
                f"severity {severity}" if severity else "",
                f"version {version}" if version else "",
            )
            if value
        )
        signal_text = (
            f"Exact {csc_id} correlation: {parent_count} scoped TAC/barrier record(s) "
            + (
                "match external defect metadata" + (f" ({verified_detail})" if verified_detail else "")
                if verified
                else "contain the reference; external defect metadata is not verified"
            )
            + "."
        )
        action_text = _clean_token(record.get("action_context"))
        attributed_members: List[str] = []
        source_owners: List[str] = []
        for parent in record.get("parent_records") or []:
            parent_frame = source_frames.get(_clean_token(parent.get("source_sheet")), pd.DataFrame())
            try:
                parent_position = int(parent.get("source_position") or 0) - 1
            except (TypeError, ValueError):
                parent_position = -1
            if not isinstance(parent_frame, pd.DataFrame) or not (0 <= parent_position < len(parent_frame)):
                continue
            parent_row = parent_frame.iloc[parent_position]
            attributed_members.extend(
                token.strip()
                for token in _clean_token(parent_row.get("Attributed_Team_Members")).split(";")
                if token.strip()
            )
            owner = _clean_token(parent_row.get("CSSM"))
            if owner:
                source_owners.append(owner)
        row = {
            "Metric_Key": evidence_key,
            "Record_ID": csc_id,
            "Record_ID_Data_Quality": "OK" if csc_id else "Missing stable CSC ID",
            "CSC_ID": csc_id,
            "Customer": customer,
            "Customer_Identity": identity_key,
            "Account_ID": _clean_token(record.get("account_id")),
            "Association_Method": _clean_token(record.get("association_method")),
            "Parent_Source_Sheets": "; ".join(record.get("parent_source_sheets") or []),
            "Parent_Record_Count": parent_count,
            "Parent_Records_JSON": json.dumps(record.get("parent_records") or [], sort_keys=True, default=str),
            "Verified_External_Match": verified,
            "Verified_External_Bug_ID": _clean_token(record.get("verified_external_bug_id")),
            "Verified_External_Status": status,
            "Verified_External_Severity": severity,
            "Verified_External_Version": version,
            "Verified_External_Title": _clean_token(record.get("verified_external_title")),
            "External_Source_Positions_JSON": json.dumps(record.get("verified_external_source_positions") or []),
            "Action_Context": action_text,
            "Coverage_State": correlation_state,
            "Scope_Type": str(scope_type),
            "Scope_Value": str(scope_value),
            "Source_System": "AdoptIQ exact CSC correlation",
            "Attributed_Team_Members": "; ".join(sorted(set(attributed_members), key=str.casefold)),
            "CSSM": "; ".join(sorted(set(source_owners), key=str.casefold)),
        }
        rows.append(row)
        signals.append(
            {
                "source_key": "defect_correlations",
                "source_sheet": "Defect_Correlations",
                "source_state": correlation_state,
                "source_state_label": _COVERAGE_STATE_LABELS.get(
                    correlation_state,
                    _humanize_identifier(correlation_state, fallback="Unavailable"),
                ),
                "account": customer,
                "record_id": csc_id,
                "source_position": position,
                "signal": signal_text,
                "decision_implication": action_text,
                "evidence_key": evidence_key,
            }
        )
    frame = pd.DataFrame(rows)
    required_columns = (
        "Metric_Key",
        "Record_ID",
        "Record_ID_Data_Quality",
        "CSC_ID",
        "Customer",
        "Customer_Identity",
        "Account_ID",
        "Association_Method",
        "Parent_Source_Sheets",
        "Parent_Record_Count",
        "Parent_Records_JSON",
        "Verified_External_Match",
        "Verified_External_Bug_ID",
        "Verified_External_Status",
        "Verified_External_Severity",
        "Verified_External_Version",
        "Verified_External_Title",
        "External_Source_Positions_JSON",
        "Action_Context",
        "Coverage_State",
        "Scope_Type",
        "Scope_Value",
        "Source_System",
        "Attributed_Team_Members",
        "CSSM",
    )
    for column in required_columns:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="object")
    frame = frame.loc[:, list(required_columns)]
    if correlation_state == "unavailable":
        frame.attrs["source_unavailable"] = True
        frame.attrs["source_unavailable_detail"] = "CSC correlation inputs unavailable; see embedded coverage"
    elif correlation_state == "partial":
        frame.attrs["partial"] = True
        frame.attrs["source_mode_detail"] = "CSC correlation used incomplete source coverage"
    bundle["frame"] = frame
    bundle["signals"] = signals
    bundle["source_state"] = correlation_state
    return bundle


def _build_cross_source_decision_signals(
    frames: Mapping[str, pd.DataFrame],
    *,
    external_incidents: pd.DataFrame,
    external_bugs: pd.DataFrame,
    scope_value: str,
    as_of: Any,
) -> List[Dict[str, Any]]:
    """Select one exact, decision-relevant signal from every applicable source.

    The canonical risk score keeps its validated formula.  Sources that are
    contextual rather than weighted inputs (notably Success Priorities and
    external bugs) still influence the report's action context instead of
    disappearing into a source-count appendix.  Missing sources produce an
    explicit no-conclusion row; they are never converted to zero evidence.
    """

    source_frames: Dict[str, pd.DataFrame] = {
        **{
            key: (frames.get(key) if isinstance(frames.get(key), pd.DataFrame) else pd.DataFrame())
            for key in _FRAME_KEYS
        },
        "external_incidents": external_incidents,
        "external_bugs": external_bugs,
    }
    as_of_ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    open_states = {
        "open",
        "active",
        "at risk",
        "critical",
        "escalated",
        "blocked",
        "on hold",
        "monitoring",
        "in progress",
        "pending",
    }
    terminal_states = {"closed", "completed", "resolved", "cancelled", "canceled"}

    def first(row: Mapping[str, Any], columns: Sequence[str]) -> str:
        return _decision_signal_text(_first_value(row, columns))

    def status_rank(value: Any) -> int:
        token = _clean_token(value).casefold().replace("_", " ")
        if token in open_states:
            return 0
        if token in terminal_states:
            return 2
        return 1

    def date_value(row: Mapping[str, Any], columns: Sequence[str]) -> pd.Timestamp:
        raw = _first_value(row, columns)
        return pd.to_datetime(raw, errors="coerce", utc=True)

    signals: List[Dict[str, Any]] = []
    for source_key, spec in _DECISION_SIGNAL_SPECS.items():
        raw = source_frames.get(source_key)
        frame = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame()
        state_meta = cm.source_data_state(frame)
        state = str(state_meta.get("state") or "unavailable").casefold()
        sheet = str(spec["sheet"])
        state_label = _COVERAGE_STATE_LABELS.get(
            state,
            _humanize_identifier(state, fallback="Unavailable"),
        )

        if frame.empty:
            if state == "zero":
                signal_text = "No scoped records returned."
                action_text = "No source-driven action is asserted; continue monitoring."
            else:
                signal_text = f"No complete source conclusion is available ({state_label.casefold()} coverage)."
                action_text = "Restore or verify this source before relying on it for a decision."
            evidence_key = evidence_entity_key("decision_signal", f"{sheet}|{state}|no-row")
            signals.append(
                {
                    "source_key": source_key,
                    "source_sheet": sheet,
                    "source_state": state,
                    "source_state_label": state_label,
                    "account": _decision_signal_text(scope_value) or "Selected scope",
                    "record_id": "",
                    "source_position": None,
                    "signal": signal_text,
                    "decision_implication": action_text,
                    "evidence_key": evidence_key,
                }
            )
            continue

        # Preserve source row positions while ranking.  Sanitization happens
        # only when rendering text, so Evidence_Links still resolves the exact
        # original row in the content-digested workbook.
        ranked: List[Tuple[Tuple[Any, ...], int, Mapping[str, Any]]] = []
        for position, (_, row) in enumerate(frame.iterrows()):
            status = _first_value(row, spec.get("status") or ())
            priority = _first_value(row, spec.get("priority") or ())
            date = date_value(row, spec.get("date") or ())
            if pd.isna(date):
                date_rank: float = float("inf")
            elif spec.get("date_mode") == "nearest" and pd.notna(as_of_ts):
                date_rank = abs(float((date - as_of_ts).total_seconds()))
            else:
                date_rank = -float(date.timestamp())
            record_id = _clean_token(row.get("Record_ID"))
            ranked.append(
                (
                    (
                        status_rank(status),
                        _priority_rank(priority),
                        date_rank,
                        record_id.casefold(),
                        position,
                    ),
                    position,
                    row,
                )
            )
        _, selected_position, selected = min(ranked, key=lambda item: item[0])
        record_id = _decision_signal_text(selected.get("Record_ID"))
        title = first(selected, spec.get("title") or ())
        status = first(selected, spec.get("status") or ())
        priority = first(selected, spec.get("priority") or ())
        selected_date = date_value(selected, spec.get("date") or ())

        headline_parts = [part for part in (record_id, title) if part]
        headline = " — ".join(headline_parts) or "Selected source record"
        qualifiers: List[str] = []
        if status:
            qualifiers.append(f"status {status}")
        if priority:
            qualifiers.append(f"priority/severity {priority}")
        if pd.notna(selected_date):
            qualifiers.append(f"{spec.get('date_label') or 'dated'} {selected_date.strftime('%Y-%m-%d')}")
        signal_text = headline + ("; " + "; ".join(qualifiers) if qualifiers else "")

        recorded_action = first(selected, spec.get("recorded_action") or ())
        action_text = f"Execute recorded next step: {recorded_action}" if recorded_action else str(spec["action"])
        if state not in {"available", "zero"}:
            action_text = f"Treat this as retained evidence only ({state_label.casefold()} coverage). " + action_text
        account = first(selected, _CUSTOMER_COLUMNS)
        evidence_basis = record_id or f"row-{selected_position + 2}"
        signals.append(
            {
                "source_key": source_key,
                "source_sheet": sheet,
                "source_state": state,
                "source_state_label": state_label,
                "account": account or (_decision_signal_text(scope_value) or "Selected scope"),
                "record_id": record_id,
                "source_position": selected_position,
                "signal": signal_text,
                "decision_implication": action_text,
                "evidence_key": evidence_entity_key("decision_signal", f"{sheet}|{evidence_basis}"),
            }
        )
    return signals


def _decision_insight_source_state(frame: Any) -> str:
    """Return the canonical availability state for one insight input."""

    if not isinstance(frame, pd.DataFrame):
        return "unavailable"
    return str(cm.source_data_state(frame).get("state") or "unavailable").casefold()


def _combined_decision_insight_state(states: Iterable[str]) -> str:
    """Combine only the complete sources that actually contributed a claim."""

    normalized = [str(value or "unavailable").casefold() for value in states]
    if not normalized:
        return "unavailable"
    if any(value not in {"available", "zero"} for value in normalized):
        return "partial"
    return "zero" if all(value == "zero" for value in normalized) else "available"


def build_canonical_predictive_outlooks(
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: Any,
    calibration: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the exact ID-first predictive bundle shared by reports and Ask AI.

    The customer universe comes from every canonical source.  Each predictive
    input is then sliced through :func:`_customer_frame_for_identity`, so a
    TAC alias or account-ID-only row cannot split or disappear from the
    outlook.  Coverage is returned for every identity, including customers
    whose forecast is withheld for failed TAC or insufficient dated history.
    """

    evaluation_as_of = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(evaluation_as_of):
        raise ValueError("canonical predictive outlooks require a valid as_of timestamp")
    identities = _canonical_customer_identities(frames)
    outlooks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    coverage_by_customer: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    slices_by_customer: "OrderedDict[str, Dict[str, pd.DataFrame]]" = OrderedDict()
    for identity in identities:
        customer = str(identity.get("label") or identity.get("base_label") or "").strip()
        if not customer:
            continue
        customer_frames = {
            source_key: _customer_frame_for_identity(
                frames.get(source_key, pd.DataFrame()),
                identity,
                identities,
            )
            for source_key in (
                "tac_cases",
                "adoption_barriers",
                "customer_pulse",
            )
        }
        coverage = ps.predictive_source_coverage(customer_frames)
        outlook = ps.escalation_outlook(
            customer_frames,
            evaluation_as_of,
            calibration=calibration,
        )
        coverage_row = dict(coverage)
        if not coverage["forecast_available"]:
            coverage_row["outlook_state"] = "unavailable"
        elif outlook is None:
            coverage_row["outlook_state"] = "insufficient_history"
        else:
            coverage_row["outlook_state"] = "forecast"
            outlooks[customer] = outlook
        coverage_by_customer[customer] = coverage_row
        slices_by_customer[customer] = customer_frames
    return {
        "evaluation_as_of_utc": evaluation_as_of.isoformat(),
        "identities": identities,
        "outlooks": outlooks,
        "coverage_by_customer": coverage_by_customer,
        # Kept internal to the canonical bundle so the report can freeze exact
        # workbook row positions without re-slicing names independently.
        "customer_slices": slices_by_customer,
    }


def _positions_from_mask(mask: pd.Series) -> List[int]:
    """Freeze positional workbook row identities from a boolean source mask."""

    return [int(position) for position, selected in enumerate(mask.fillna(False).astype(bool).tolist()) if selected]


def _build_decision_insights(
    frames: Mapping[str, pd.DataFrame],
    *,
    as_of: Any,
    days: int,
) -> "OrderedDict[str, Dict[str, Any]]":
    """Freeze every derived executive-insight claim before artifact rendering.

    These structures deliberately contain the exact visible paragraph, the
    evaluation clock, the canonical derivation inputs, and positional source
    rows.  Any exception from a canonical helper propagates and blocks report
    publication; silently dropping a factual insight is not a safe fallback.
    """

    evaluation_as_of = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(evaluation_as_of):
        raise ValueError("decision insights require a valid evaluation as_of timestamp")
    try:
        requested_days = int(days or 0)
    except (TypeError, ValueError):
        requested_days = 90
    if requested_days <= 0:
        requested_days = 90
    window_start, window_end, bounded_days = cm.reporting_window_bounds(
        as_of=evaluation_as_of,
        days=requested_days,
    )
    insights: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    tac = frames.get("tac_cases")
    tac = tac if isinstance(tac, pd.DataFrame) else pd.DataFrame()
    tac_state = _decision_insight_source_state(tac)

    # Support-theme rollup -------------------------------------------------
    if tac_state in {"available", "zero"}:
        themes = cm.tac_theme_summary(tac)
        if themes:
            tech_column = next(
                (column for column in _SUPPORT_THEME_TECH_COLUMNS if column in tac.columns),
                None,
            )
            if tech_column is None:
                raise ValueError("support themes were produced without a resolvable TAC technology field")
            wanted_themes = {
                str(theme.get("label") or "").strip().casefold()
                for theme in themes
                if str(theme.get("label") or "").strip()
            }
            support_positions = _positions_from_mask(
                tac[tech_column].fillna("").astype(str).str.strip().str.casefold().isin(wanted_themes)
            )
            expected_theme_rows = sum(int(theme.get("case_count") or 0) for theme in themes)
            if len(support_positions) != expected_theme_rows:
                raise ValueError("support-theme evidence rows do not reconcile to the frozen theme counts")
            parts: List[str] = []
            frozen_themes: List[Dict[str, Any]] = []
            for theme in themes:
                label = str(theme.get("label") or "").strip()
                case_count = int(theme.get("case_count") or 0)
                escalated_count = int(theme.get("escalated_count") or 0)
                part = f"{label} — {case_count} case(s)"
                if escalated_count:
                    part += f" ({escalated_count} escalated)"
                parts.append(part)
                frozen_themes.append(
                    {
                        "label": label,
                        "case_count": case_count,
                        "escalated_count": escalated_count,
                    }
                )
            prefix = _DECISION_INSIGHT_PREFIXES["support_themes"]
            # Current-side scope projection is canonical report logic.  Let
            # any failure propagate and block publication; only the optional
            # corpus side is allowed to fail soft below.
            current_customers = _customers_from_frames(frames)
            current_technologies = [item["label"] for item in frozen_themes]
            corpus_claim: Optional[Dict[str, Any]] = None
            try:
                from report_corpus_context import build_support_theme_corpus_claim

                corpus_claim = build_support_theme_corpus_claim(
                    current_customers,
                    current_technologies,
                )
            except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
                logger.debug(
                    "Round 172 support-theme corpus grounding unavailable: %s",
                    type(exc).__name__,
                )
                corpus_claim = None

            paragraph_text = f"{prefix} " + "; ".join(parts) + "."
            source_sheets = ["TAC_Cases"]
            source_states = {"TAC_Cases": tac_state}
            source_positions: Dict[str, List[int]] = {"TAC_Cases": support_positions}
            evidence_filters = {
                "TAC_Cases": (
                    f"{tech_column} is one of the frozen top support themes; canonical collapsed TAC case rows"
                )
            }
            canonical_function = "canonical_metrics.tac_theme_summary"
            source_fields = f"{tech_column}; Severity / canonical priority"
            filters = "selected scope; top three specific TAC technology themes"
            corpus_claims: List[Dict[str, Any]] = []
            if isinstance(corpus_claim, Mapping):
                corpus_sentence = _clean_token(corpus_claim.get("sentence"))
                if corpus_sentence:
                    paragraph_text += f" {corpus_sentence}"
                    source_sheets.append("Report_Info")
                    source_states["Report_Info"] = "available"
                    source_positions["Report_Info"] = []
                    evidence_filters["Report_Info"] = (
                        "exact content-addressed receipt from corpus_retriever; aggregate claim only, no raw corpus row"
                    )
                    canonical_function += "; report_corpus_context.build_support_theme_corpus_claim"
                    source_fields += "; Report_Info corpus retriever receipt"
                    filters += "; scoped customer + current TAC technology + recurring corpus theme exact match"
                    corpus_claims.append(dict(corpus_claim))
            paragraph_text += " Full case list in the Source Data workbook (TAC_Cases)."
            insights["support_themes"] = {
                "metric_key": "insight.support_themes",
                "display_label": "Support themes (TAC)",
                "paragraph_prefix": prefix,
                "paragraph_text": paragraph_text,
                "canonical_function": canonical_function,
                "source_sheets": source_sheets,
                "source_states": source_states,
                "source_positions": source_positions,
                "evidence_filters": evidence_filters,
                "source_fields": source_fields,
                "filters": filters,
                "grouping": "case-insensitive technology label",
                "deduplication": "canonical collapsed TAC case ID",
                "empty_state": "paragraph omitted when no specific technology theme exists",
                "source_state": tac_state,
                "evaluation_as_of_utc": evaluation_as_of.isoformat(),
                "themes": frozen_themes,
            }
            if corpus_claims:
                insights["support_themes"]["corpus_claims"] = corpus_claims

    # Support operating health -------------------------------------------
    # The real CSOne corpus has strong opened/closed-date and owner-change
    # coverage.  Freeze those fields into concise manager signals while
    # retaining exact source-row evidence and distinct denominators.
    if tac_state in {"available", "zero"}:
        operating_health = cm.tac_operating_health(
            tac,
            as_of=evaluation_as_of,
        )
        if operating_health:
            parts: List[str] = []
            closure_count = int(operating_health.get("closed_case_count") or 0)
            if closure_count:
                parts.append(
                    "median time to close "
                    f"{float(operating_health['close_time_median_days']):g} days; "
                    "90th percentile "
                    f"{float(operating_health['close_time_p90_days']):g} days across "
                    f"{closure_count} closed case(s) with valid opened/closed timestamps"
                )
            ownership_count = int(operating_health.get("ownership_observed_count") or 0)
            if ownership_count:
                threshold = int(operating_health["ownership_churn_threshold"])
                churn_count = int(operating_health.get("ownership_churn_count") or 0)
                churn_rate = float(operating_health.get("ownership_churn_rate_percent") or 0.0)
                parts.append(
                    f"ownership changed at least {threshold} times on {churn_count} of "
                    f"{ownership_count} case(s) with owner-change history ({churn_rate:g}%)"
                )
            evidence_positions = sorted(
                {
                    *[int(value) for value in operating_health.get("closure_positions") or []],
                    *[int(value) for value in operating_health.get("ownership_positions") or []],
                }
            )
            if not parts or not evidence_positions:
                raise ValueError("support operating health lacks a factual claim or evidence rows")
            source_fields = [
                _clean_token(operating_health.get("open_date_column")),
                _clean_token(operating_health.get("close_date_column")),
                _clean_token(operating_health.get("owner_change_column")),
            ]
            prefix = _DECISION_INSIGHT_PREFIXES["support_operating_health"]
            # Round 173: current-side scope projection is canonical report
            # logic.  Let any failure propagate and block publication; only
            # the optional corpus side is allowed to fail soft below.
            current_customers = _customers_from_frames(frames)
            current_technologies = [
                str(theme.get("label") or "").strip()
                for theme in cm.tac_theme_summary(tac)
            ]
            corpus_claim: Optional[Dict[str, Any]] = None
            try:
                from report_corpus_context import build_support_operating_health_corpus_claim

                corpus_claim = build_support_operating_health_corpus_claim(
                    current_customers,
                    current_technologies,
                )
            except Exception as exc:  # noqa: BLE001 - missing corpus fails soft
                logger.debug(
                    "Round 173 operating-health corpus grounding unavailable: %s",
                    type(exc).__name__,
                )
                corpus_claim = None

            paragraph_text = f"{prefix} " + "; ".join(parts) + "."
            source_sheets = ["TAC_Cases"]
            source_states = {"TAC_Cases": tac_state}
            source_positions_map: Dict[str, List[int]] = {"TAC_Cases": evidence_positions}
            evidence_filters = {
                "TAC_Cases": (
                    "canonical collapsed TAC rows with a valid non-negative close duration "
                    "ending no later than the evaluation clock and/or a populated non-negative "
                    "owner-change count"
                )
            }
            canonical_function = "canonical_metrics.tac_operating_health"
            source_fields_text = "; ".join(value for value in source_fields if value)
            filters = (
                "selected scope; valid opened/closed timestamps ending at or before the "
                "evaluation clock; owner-change denominator includes populated non-negative values only"
            )
            corpus_claims: List[Dict[str, Any]] = []
            if isinstance(corpus_claim, Mapping):
                corpus_sentence = _clean_token(corpus_claim.get("sentence"))
                if corpus_sentence:
                    paragraph_text += f" {corpus_sentence}"
                    source_sheets.append("Report_Info")
                    source_states["Report_Info"] = "available"
                    source_positions_map["Report_Info"] = []
                    evidence_filters["Report_Info"] = (
                        "exact content-addressed receipt from corpus_retriever; aggregate claim only, no raw corpus row"
                    )
                    canonical_function += "; report_corpus_context.build_support_operating_health_corpus_claim"
                    source_fields_text += "; Report_Info corpus retriever receipt"
                    filters += (
                        "; scoped customer closure precedent + current TAC technology "
                        "+ recurring corpus theme exact match"
                    )
                    corpus_claims.append(dict(corpus_claim))
            insights["support_operating_health"] = {
                "metric_key": "insight.support_operating_health",
                "display_label": "Support operating health (TAC)",
                "paragraph_prefix": prefix,
                "paragraph_text": paragraph_text,
                "canonical_function": canonical_function,
                "source_sheets": source_sheets,
                "source_states": source_states,
                "source_positions": source_positions_map,
                "evidence_filters": evidence_filters,
                "source_fields": source_fields_text,
                "filters": filters,
                "grouping": "selected-scope logical TAC cases",
                "deduplication": "canonical collapsed TAC case ID",
                "empty_state": (
                    "paragraph omitted when neither valid closure durations nor numeric owner-change history exists"
                ),
                "source_state": tac_state,
                "evaluation_as_of_utc": evaluation_as_of.isoformat(),
                "operating_health": {
                    key: _json_safe(value)
                    for key, value in operating_health.items()
                    if key not in {"closure_positions", "ownership_positions"}
                },
            }
            if corpus_claims:
                insights["support_operating_health"]["corpus_claims"] = corpus_claims

    # Within-window momentum ----------------------------------------------
    momentum_specs = (
        (
            "TAC cases opened",
            "tac_cases",
            "TAC_Cases",
            ("open_date", "Date/Time Opened"),
        ),
        (
            "Adoption barriers opened",
            "adoption_barriers",
            "Adoption_Barriers",
            ("OPEN_DATE_C", "Open Date", "CREATED_DATE_C", "Created Date"),
        ),
        (
            "Action plans created",
            "action_plans",
            "Action_Plans",
            ("CREATED_DATE_C", "Created Date"),
        ),
    )
    momentum_parts: List[str] = []
    momentum_components: List[Dict[str, Any]] = []
    momentum_positions: "OrderedDict[str, List[int]]" = OrderedDict()
    momentum_states: "OrderedDict[str, str]" = OrderedDict()
    momentum_filters: "OrderedDict[str, str]" = OrderedDict()
    momentum_source_fields: List[str] = []
    for label, frame_key, sheet_name, date_columns in momentum_specs:
        raw_frame = frames.get(frame_key)
        frame = raw_frame if isinstance(raw_frame, pd.DataFrame) else pd.DataFrame()
        frame_state = _decision_insight_source_state(frame)
        if frame_state not in {"available", "zero"}:
            continue
        momentum = cm.window_momentum(
            frame,
            date_columns=date_columns,
            as_of=evaluation_as_of,
            days=bounded_days,
        )
        if not momentum:
            continue
        date_column = str(momentum["date_column"])
        parsed = cm._r158_parse_dates_utc(frame[date_column])
        in_window = parsed.notna() & parsed.between(
            window_start,
            window_end,
            inclusive="both",
        )
        evidence_mask = in_window | parsed.isna()
        positions = _positions_from_mask(evidence_mask)
        expected_rows = int(momentum["first_half"]) + int(momentum["second_half"]) + int(momentum.get("undated") or 0)
        if len(positions) != expected_rows:
            raise ValueError(f"{sheet_name} momentum evidence rows do not reconcile to frozen counts")
        part = (
            f"{label} {momentum['direction']} — {int(momentum['second_half'])} in the last "
            f"{float(momentum['half_days']):g} days vs {int(momentum['first_half'])} "
            "in the prior half"
        )
        if momentum.get("undated"):
            part += f" ({int(momentum['undated'])} undated excluded)"
        momentum_parts.append(part)
        momentum_components.append(
            {
                "kind": "record_count",
                "label": label,
                "source_sheet": sheet_name,
                "date_column": date_column,
                "direction": str(momentum["direction"]),
                "window_days": int(momentum["window_days"]),
                "half_days": float(momentum["half_days"]),
                "first_half": int(momentum["first_half"]),
                "second_half": int(momentum["second_half"]),
                "undated": int(momentum.get("undated") or 0),
            }
        )
        momentum_positions[sheet_name] = positions
        momentum_states[sheet_name] = frame_state
        momentum_filters[sheet_name] = f"{date_column} within the evaluation window or undated and explicitly excluded"
        momentum_source_fields.append(f"{sheet_name}: {date_column}")

    pulse_frame_raw = frames.get("customer_pulse")
    pulse_frame = pulse_frame_raw if isinstance(pulse_frame_raw, pd.DataFrame) else pd.DataFrame()
    pulse_state = _decision_insight_source_state(pulse_frame)
    if pulse_state in {"available", "zero"}:
        pulse_momentum = cm.pulse_score_momentum(
            pulse_frame,
            as_of=evaluation_as_of,
            days=bounded_days,
        )
        if pulse_momentum:
            pulse_date_column = next(
                (column for column in _PULSE_DATE_COLUMNS if column in pulse_frame.columns),
                None,
            )
            pulse_value_column = next(
                (column for column in _PULSE_VALUE_COLUMNS if column in pulse_frame.columns),
                None,
            )
            if pulse_date_column is None or pulse_value_column is None:
                raise ValueError("pulse momentum was produced without resolvable date and score fields")
            pulse_dates = cm._r158_parse_dates_utc(pulse_frame[pulse_date_column])
            pulse_values = pd.to_numeric(pulse_frame[pulse_value_column], errors="coerce")
            pulse_mask = (
                pulse_dates.notna()
                & pulse_values.notna()
                & pulse_dates.between(window_start, window_end, inclusive="both")
            )
            pulse_positions = _positions_from_mask(pulse_mask)
            expected_pulse_rows = int(pulse_momentum["first_half_count"]) + int(pulse_momentum["second_half_count"])
            if len(pulse_positions) != expected_pulse_rows:
                raise ValueError("Customer_Pulse momentum evidence rows do not reconcile to frozen counts")
            momentum_parts.append(
                f"Pulse {pulse_momentum['direction']} — avg score "
                f"{float(pulse_momentum['first_half_avg']):g} → "
                f"{float(pulse_momentum['second_half_avg']):g}"
            )
            momentum_components.append(
                {
                    "kind": "pulse_average",
                    "label": "Pulse",
                    "source_sheet": "Customer_Pulse",
                    "date_column": pulse_date_column,
                    "value_column": pulse_value_column,
                    "direction": str(pulse_momentum["direction"]),
                    "window_days": int(pulse_momentum["window_days"]),
                    "first_half_avg": float(pulse_momentum["first_half_avg"]),
                    "second_half_avg": float(pulse_momentum["second_half_avg"]),
                    "first_half_count": int(pulse_momentum["first_half_count"]),
                    "second_half_count": int(pulse_momentum["second_half_count"]),
                }
            )
            momentum_positions["Customer_Pulse"] = pulse_positions
            momentum_states["Customer_Pulse"] = pulse_state
            momentum_filters["Customer_Pulse"] = (
                f"numeric {pulse_value_column} with {pulse_date_column} inside the evaluation window"
            )
            momentum_source_fields.append(f"Customer_Pulse: {pulse_date_column}; {pulse_value_column}")

    if momentum_parts:
        prefix = _DECISION_INSIGHT_PREFIXES["window_momentum"]
        insights["window_momentum"] = {
            "metric_key": "insight.window_momentum",
            "display_label": "Momentum within this window",
            "paragraph_prefix": prefix,
            "paragraph_text": f"{prefix} " + "; ".join(momentum_parts) + ".",
            "canonical_function": ("canonical_metrics.window_momentum; canonical_metrics.pulse_score_momentum"),
            "source_sheets": list(momentum_positions),
            "source_states": dict(momentum_states),
            "source_positions": dict(momentum_positions),
            "evidence_filters": dict(momentum_filters),
            "source_fields": "; ".join(momentum_source_fields),
            "filters": (
                f"selected scope; {bounded_days}-day window ending at the "
                "evaluation clock; first half versus second half"
            ),
            "grouping": "source and analysis-window half",
            "deduplication": "canonical source record ID before deterministic window split",
            "empty_state": "component omitted when dated evidence cannot support a comparison",
            "source_state": _combined_decision_insight_state(momentum_states.values()),
            "evaluation_as_of_utc": evaluation_as_of.isoformat(),
            "components": momentum_components,
        }

    # Predictive escalation outlook ---------------------------------------
    predictive_bundle = build_canonical_predictive_outlooks(
        frames,
        as_of=evaluation_as_of,
    )
    identities = predictive_bundle["identities"]
    identity_by_label = {str(identity.get("label") or ""): identity for identity in identities}
    scored = [
        (customer, outlook)
        for customer, outlook in predictive_bundle["outlooks"].items()
        if outlook.get("tier") in {"ELEVATED", "CRITICAL_WATCH"}
    ]
    scored.sort(key=lambda item: (-int(item[1]["points"]), item[0].casefold()))
    selected_scored = scored[:5]

    predictive_sources: "OrderedDict[str, pd.DataFrame]" = OrderedDict(
        [
            ("TAC_Cases", tac),
            (
                "Adoption_Barriers",
                frames.get("adoption_barriers")
                if isinstance(frames.get("adoption_barriers"), pd.DataFrame)
                else pd.DataFrame(),
            ),
            ("Customer_Pulse", pulse_frame),
        ]
    )
    predictive_states: "OrderedDict[str, str]" = OrderedDict(
        (sheet_name, _decision_insight_source_state(source_frame))
        for sheet_name, source_frame in predictive_sources.items()
    )

    frozen_outlooks: List[Dict[str, Any]] = []
    predictive_lines: List[str] = []
    for customer, outlook in selected_scored:
        contributors = [{"label": str(label), "points": int(points)} for label, points in outlook["contributors"]]
        why = "; ".join(f"{item['label']} (+{item['points']})" for item in contributors)
        if outlook.get("coverage_state") == "partial":
            missing = ", ".join(
                f"{_humanize_identifier(source, fallback='Report data')} "
                f"({outlook.get('source_states', {}).get(source, 'unavailable')})"
                for source in outlook.get("missing_sources") or []
            )
            claim = "partial coverage — lower-bound relative signal; missing complete sources: " + (
                missing or "not identified"
            )
        elif outlook["calibration_state"] == "calibrated":
            claim = (
                f"{int(outlook['observed_events'])} of {int(outlook['observed_n'])} "
                "historical customer-periods like this escalated within "
                f"{int(outlook['horizon_days'])} days"
            )
        else:
            claim = "uncalibrated prior — relative ranking only"
        tier_label = str(outlook["tier"]).replace("_", " ").title()
        predictive_lines.append(f"{customer} — {tier_label} ({int(outlook['points'])} pts): {why} [{claim}]")
        # Round 175: fail-closed peer likely-next on the existing scorecard
        # line. No new insight key and no corpus receipt — thin evidence
        # leaves the line unchanged so R172/R173 retrieval counts hold.
        try:
            from report_corpus_context import format_ranked_peer_guidance_clause

            peer_suffix = format_ranked_peer_guidance_clause(
                customer, include_likely_next=True
            )
        except Exception:  # noqa: BLE001 - optional corpus fails closed
            peer_suffix = ""
        if peer_suffix and "will " not in peer_suffix.casefold():
            predictive_lines[-1] = f"{predictive_lines[-1]} {peer_suffix}"
        frozen_outlook: Dict[str, Any] = {
            "customer": customer,
            "identity_key": str(identity_by_label.get(customer, {}).get("identity_key") or ""),
            "tier": str(outlook["tier"]),
            "points": int(outlook["points"]),
            "contributors": contributors,
            "calibration_state": str(outlook["calibration_state"]),
            "coverage_state": str(outlook.get("coverage_state") or "unavailable"),
            "relative_signal_state": str(outlook.get("relative_signal_state") or "unavailable"),
            "source_states": dict(outlook.get("source_states") or {}),
            "missing_sources": list(outlook.get("missing_sources") or []),
            "claim": claim,
            "horizon_days": int(outlook["horizon_days"]),
            "spec_version": str(outlook.get("spec_version") or ""),
        }
        for key in (
            "claim_level",
            "observed_rate",
            "observed_events",
            "observed_n",
            "wilson_low",
            "wilson_high",
        ):
            if key in outlook:
                frozen_outlook[key] = _json_safe(outlook[key])
        frozen_outlooks.append(frozen_outlook)

    predictive_positions: "OrderedDict[str, List[int]]" = OrderedDict()
    predictive_filters: "OrderedDict[str, str]" = OrderedDict()
    source_key_by_sheet = {
        "TAC_Cases": "tac_cases",
        "Adoption_Barriers": "adoption_barriers",
        "Customer_Pulse": "customer_pulse",
    }
    for sheet_name, source_frame in predictive_sources.items():
        positions: set[int] = set()
        if isinstance(source_frame, pd.DataFrame) and not source_frame.empty:
            positioned = source_frame.copy()
            positioned["_AdoptIQ_Predictive_Position"] = range(len(positioned))
            for customer, _ in selected_scored:
                identity = identity_by_label.get(customer)
                if not identity:
                    continue
                selected = _customer_frame_for_identity(positioned, identity, identities)
                positions.update(
                    int(value) for value in selected.get("_AdoptIQ_Predictive_Position", pd.Series(dtype=int)).tolist()
                )
        predictive_positions[sheet_name] = sorted(positions)
        predictive_filters[sheet_name] = (
            "ID-first canonical customer slices for displayed outlooks; "
            f"{source_key_by_sheet[sheet_name]} features evaluated at "
            f"{evaluation_as_of.isoformat()}"
        )

    tac_forecast_blocked = tac_state in {"failed", "unavailable"}
    if predictive_lines or tac_forecast_blocked:
        prefix = _DECISION_INSIGHT_PREFIXES["predictive_outlook"]
        if tac_forecast_blocked:
            paragraph_text = (
                f"{prefix} Forecast unavailable because TAC Cases coverage is {tac_state}; "
                "no predictive tier, points, or probability is asserted."
            )
            predictive_state = "unavailable"
        else:
            paragraph_text = (
                f"{prefix} " + " | ".join(predictive_lines) + ". Method and validation: PREDICTIVE_INTELLIGENCE.md; "
                "calibrate on live history via scripts/backtest_escalation_forecast.py."
            )
            predictive_state = _combined_decision_insight_state(predictive_states.values())
        insights["predictive_outlook"] = {
            "metric_key": "insight.predictive_outlook_30d",
            "display_label": "Predictive outlook (next 30 days)",
            "paragraph_prefix": prefix,
            "paragraph_text": paragraph_text,
            "canonical_function": (
                "decision_report_delivery.build_canonical_predictive_outlooks; predictive_signals.escalation_outlook"
            ),
            "source_sheets": list(predictive_sources),
            "source_states": dict(predictive_states),
            "source_positions": dict(predictive_positions),
            "evidence_filters": dict(predictive_filters),
            "source_fields": (
                "stable account/customer identity; TAC open/close date and severity; "
                "Adoption Barrier open date, severity and technology; Customer Pulse date and score"
            ),
            "filters": (
                "all-source ID-first customer universe; deterministic 30-day scorecard at the "
                "evaluation clock; Elevated or Critical Watch; top five by points"
            ),
            "grouping": "ID-first customer predictive-score ranking",
            "deduplication": (
                "stable account identity, then unambiguous registered/exact customer alias; "
                "canonical source-specific stable record IDs"
            ),
            "empty_state": (
                "forecast explicitly unavailable when TAC failed/unavailable; otherwise paragraph "
                "omitted when no customer clears cold-start and Elevated thresholds"
            ),
            "source_state": predictive_state,
            "evaluation_as_of_utc": evaluation_as_of.isoformat(),
            "customer_universe": [str(identity.get("label") or "") for identity in identities],
            "coverage_by_customer": {
                customer: {key: _json_safe(value) for key, value in coverage.items() if key != "source_details"}
                for customer, coverage in predictive_bundle["coverage_by_customer"].items()
            },
            "outlooks": frozen_outlooks,
        }

    return insights


class _RunDeltaCurrentSideError(RuntimeError):
    """Round 171: the CURRENT run failed to project — must block publication."""


_R171_BAND_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "healthy": 0}
_R171_VISIBLE_KPI_MOVES = 4
_R171_VISIBLE_BAND_MOVES = 5
_R171_PAYLOAD_ITEM_CAP = 50
_R171_AP_CHANGE_LABELS = {
    "completed": "completed",
    "new": "new",
    "became_overdue": "became overdue",
    "reopened": "reopened",
    "absent": "no longer present",
    "owner_changed": "updated",
    "due_date_changed": "updated",
    "status_changed": "updated",
}


def _r171_format_number(value: Any) -> str:
    number = None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _run_delta_after_view(
    facts: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    """Round 171: project the current run through the workspace snapshot code.

    Builds the same canonical sheet set the workbook writer will emit (minus
    the not-yet-mintable fingerprint) and projects it with the exact code the
    Manager Decision Workspace uses on written artifacts, so run-over-run
    comparison always compares artifact-truth to artifact-truth.  Also returns
    the sheet records so movement claims can cite real current-side row
    positions.  Any failure here is a CURRENT-report integrity problem and
    must propagate.
    """

    import manager_decision_workspace as mdw  # noqa: PLC0415 - lazy, mirrors repo pattern

    sheets = build_source_data_sheets(facts, _skip_contract_fingerprint=True)
    records_by_sheet: Dict[str, List[Dict[str, Any]]] = {}
    for name, frame in sheets.items():
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            safe = frame.where(pd.notna(frame), None)
            records_by_sheet[name] = safe.to_dict("records")
        else:
            records_by_sheet[name] = []

    info: Dict[str, str] = {}
    for record in records_by_sheet.get("Report_Info", []):
        item = str(record.get("Item") or "").strip()
        if item:
            value = record.get("Value")
            info[item] = "" if value is None else str(value)

    def read(name: str, limit: Any = None) -> List[Dict[str, Any]]:
        records = records_by_sheet.get(name, [])
        if limit is None:
            return records
        return records[: int(limit)]

    return mdw.snapshot_from_sheet_records(info=info, read=read), records_by_sheet


def _build_run_delta_insight(
    facts: Mapping[str, Any],
    prior_snapshot: Mapping[str, Any],
    prior_meta: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Round 171: freeze run-over-run movement as a canonical decision insight.

    Reuses the Round 146 ``compare_snapshots`` contract (never a second
    differ) against an in-memory projection of the current run, and extends it
    with per-customer risk-band transitions computed from the same projected
    ``accounts`` rows the workspace shows.  Returns ``None`` only for
    principled absence: no prior, a non-canonical/formula-bearing/legacy
    prior, or a prior whose scope is not comparable.  Once both sides are
    comparable, internal failures raise — a movement claim may be absent, but
    it may never be silently wrong.
    """

    import manager_decision_workspace as mdw  # noqa: PLC0415 - lazy, mirrors repo pattern

    if not isinstance(prior_snapshot, Mapping):
        return None
    if not prior_snapshot.get("canonical_snapshot"):
        return None
    prior_fingerprint = str(prior_snapshot.get("fact_fingerprint") or "").strip()
    if not prior_fingerprint:
        return None
    if int(prior_snapshot.get("formula_cells") or 0):
        return None

    try:
        after_view, current_records = _run_delta_after_view(facts)
    except Exception as exc:  # noqa: BLE001 - reclassified as a current-side block
        raise _RunDeltaCurrentSideError(
            "current-run canonical projection failed during run-over-run comparison"
        ) from exc
    comparison = mdw.compare_snapshots(prior_snapshot, after_view)
    if comparison.get("comparison_state") != "comparable":
        return None

    # Per-customer risk-band transitions (extension of the metric comparison,
    # from the same projected artifact rows on both sides).
    def _account_bands(snapshot: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        bands: Dict[str, Dict[str, Any]] = {}
        for item in snapshot.get("accounts") or []:
            if not isinstance(item, Mapping):
                continue
            customer = str(item.get("customer") or "").strip()
            band = str(item.get("risk_band") or "").strip()
            if customer and band:
                bands[customer.casefold()] = {
                    "customer": customer,
                    "band": band,
                    "score": item.get("risk_score_0_100"),
                }
        return bands

    before_bands = _account_bands(prior_snapshot)
    after_bands = _account_bands(after_view)
    band_transitions: List[Dict[str, Any]] = []
    for key in sorted(set(before_bands) & set(after_bands)):
        old = before_bands[key]
        new = after_bands[key]
        if old["band"].casefold() == new["band"].casefold():
            continue
        old_rank = _R171_BAND_SEVERITY.get(old["band"].casefold(), -1)
        new_rank = _R171_BAND_SEVERITY.get(new["band"].casefold(), -1)
        band_transitions.append(
            {
                "customer": new["customer"],
                "before": old["band"],
                "after": new["band"],
                "direction": (
                    "worsened"
                    if new_rank > old_rank
                    else "improved"
                    if new_rank < old_rank
                    else "changed"
                ),
                "severity_rank": new_rank,
            }
        )
    band_transitions.sort(
        key=lambda item: (
            item["direction"] != "worsened",
            -item["severity_rank"],
            item["customer"].casefold(),
        )
    )
    appeared = sorted(set(after_bands) - set(before_bands))
    disappeared = sorted(set(before_bands) - set(after_bands))

    metric_changes = [
        change
        for change in comparison.get("metric_changes") or []
        if isinstance(change, Mapping)
    ]
    ranked_moves = sorted(
        (change for change in metric_changes if change.get("delta") is not None),
        key=lambda change: (-abs(float(change["delta"])), str(change.get("metric_key") or "")),
    )
    one_sided_moves = [change for change in metric_changes if change.get("delta") is None]

    ap_changes = [
        change
        for change in comparison.get("action_plan_changes") or []
        if isinstance(change, Mapping)
    ]
    ap_counts: "OrderedDict[str, int]" = OrderedDict()
    for change in ap_changes:
        label = _R171_AP_CHANGE_LABELS.get(str(change.get("change") or ""), "updated")
        ap_counts[label] = ap_counts.get(label, 0) + 1

    # ------------------------------------------------------------------ text
    prior_clock = str(
        prior_snapshot.get("evaluation_as_of_utc")
        or prior_snapshot.get("data_as_of_utc")
        or ""
    ).strip()
    prior_stamp = prior_clock[:10] if len(prior_clock) >= 10 else "an earlier run"
    anchor = f"vs the {prior_stamp} report (fingerprint {prior_fingerprint[:12]})"

    parts: List[str] = []
    if band_transitions:
        visible_bands = band_transitions[:_R171_VISIBLE_BAND_MOVES]
        rendered = ", ".join(
            f"{item['customer']} {item['before']}→{item['after']}" for item in visible_bands
        )
        suffix = (
            f" and {len(band_transitions) - len(visible_bands)} more"
            if len(band_transitions) > len(visible_bands)
            else ""
        )
        parts.append(f"risk bands moved for {rendered}{suffix}")
    if ranked_moves:
        visible_moves = ranked_moves[:_R171_VISIBLE_KPI_MOVES]
        rendered = ", ".join(
            f"{str(change.get('label') or change.get('metric_key'))} "
            f"{_r171_format_number(change.get('before'))}→{_r171_format_number(change.get('after'))} "
            f"({'+' if float(change['delta']) > 0 else ''}{_r171_format_number(change.get('delta'))})"
            for change in visible_moves
        )
        suffix = (
            f" and {len(ranked_moves) - len(visible_moves)} more KPI change(s)"
            if len(ranked_moves) > len(visible_moves)
            else ""
        )
        parts.append(f"KPIs moved: {rendered}{suffix}")
    if ap_counts:
        rendered = ", ".join(f"{count} {label}" for label, count in ap_counts.items())
        parts.append(f"Action Plans: {rendered}")

    caveat_sentences: List[str] = []
    for caveat in comparison.get("caveats") or []:
        text = str(caveat or "").strip()
        if text:
            caveat_sentences.append(text if text.endswith(".") else text + ".")
    if one_sided_moves:
        caveat_sentences.append(
            f"{len(one_sided_moves)} KPI value(s) became reportable or stopped being "
            "reportable between the runs and are excluded from claimed movement."
        )
    if appeared or disappeared:
        caveat_sentences.append(
            f"Customer universe changed ({len(appeared)} appeared, {len(disappeared)} "
            "no longer present); band movement is claimed only for customers in both runs."
        )

    prefix = _DECISION_INSIGHT_PREFIXES["run_delta"]
    if parts:
        body = f"{anchor}: " + "; ".join(parts) + "."
    else:
        body = (
            f"{anchor}: no material movement in comparable KPIs, customer risk bands, "
            "or Action Plans."
        )
    paragraph_text = f"{prefix} {body}"
    if caveat_sentences:
        paragraph_text += " " + " ".join(caveat_sentences)

    # ------------------------------------------------- sheets, states, evidence
    # The workspace's binding rule is deliberate: an "available" insight must
    # cite at least one real source row.  Movement claims therefore carry
    # genuine current-side receipts — the changed Action Plan rows by stable
    # ID, the transitioned customers' Account_Summary rows, and the moved
    # KPIs' current denominator rows.  The prior side is identified by its
    # immutable fact fingerprint in every Filter_Rule.
    def _positions_matching(
        records: List[Dict[str, Any]],
        columns: Sequence[str],
        wanted_values: Sequence[Any],
    ) -> List[int]:
        wanted = {
            str(value).strip().casefold()
            for value in wanted_values
            if str(value or "").strip()
        }
        if not wanted:
            return []
        positions: List[int] = []
        for index, record in enumerate(records):
            for column in columns:
                value = record.get(column)
                if value is not None and str(value).strip().casefold() in wanted:
                    positions.append(index)
                    break
        return positions

    source_positions: Dict[str, List[int]] = {}
    evidence_filters: Dict[str, str] = {}
    anchor_note = (
        "run-over-run comparison against the immutable prior artifact "
        f"(fact fingerprint {prior_fingerprint})"
    )

    for change in ranked_moves[:_R171_VISIBLE_KPI_MOVES]:
        for token in str(change.get("source_sheet") or "").split(";"):
            token = token.strip()
            if not token or token in source_positions:
                continue
            records = current_records.get(token) or []
            if not records:
                continue
            source_positions[token] = list(range(len(records)))
            evidence_filters[token] = (
                f"{anchor_note}; complete current-side denominator rows behind the "
                "moved KPI value(s) on this sheet"
            )

    if band_transitions:
        records = current_records.get("Account_Summary") or []
        positions = _positions_matching(
            records,
            ("Account", "Customer_Name", "Customer", "BU_NAME"),
            [item["customer"] for item in band_transitions],
        )
        if positions:
            source_positions["Account_Summary"] = positions
            evidence_filters["Account_Summary"] = (
                f"{anchor_note}; current-side account rows for the customers whose "
                "risk band moved between the runs"
            )

    if ap_counts:
        records = current_records.get("Action_Plans") or []
        changed_ids = [change.get("record_id") for change in ap_changes]
        positions = _positions_matching(
            records,
            ("Record_ID", "ID", "Action Plan ID", "AP_ID"),
            changed_ids,
        )
        if not positions and records:
            positions = list(range(len(records)))
        if positions:
            source_positions["Action_Plans"] = positions
            evidence_filters["Action_Plans"] = (
                f"{anchor_note}; current-side Action Plan rows for the stable record "
                "IDs whose lifecycle changed between the runs (rows no longer present "
                "exist only in the prior artifact)"
            )

    if not source_positions:
        # Steady state (or all movement on vanished rows): the honest receipt is
        # the compared customer universe itself.
        records = current_records.get("Account_Summary") or []
        if records:
            source_positions["Account_Summary"] = list(range(len(records)))
            evidence_filters["Account_Summary"] = (
                f"{anchor_note}; complete compared customer universe — no comparable "
                "KPI, risk-band, or Action Plan movement was found"
            )
    if not source_positions:
        # No row anywhere to cite (degenerate empty portfolio): a movement
        # claim without a receipt is not made.
        return None

    source_sheets = sorted(source_positions)

    after_states = {
        str(key): str(value or "").strip().casefold() or "unknown"
        for key, value in (after_view.get("source_states") or {}).items()
    }

    def _sheet_state(sheet_name: str) -> str:
        for key, value in after_states.items():
            if key.casefold().replace(" ", "_") == sheet_name.casefold().replace(" ", "_"):
                return value
        return "available"

    source_states = {sheet: _sheet_state(sheet) for sheet in source_sheets}
    # Mirror the workspace's generic insight evidence-state rule exactly so the
    # projection's evidence binding stays valid for this key.
    if any(state not in {"available", "zero"} for state in source_states.values()):
        insight_state = "partial"
    elif source_states and all(state == "zero" for state in source_states.values()):
        insight_state = "zero"
    else:
        insight_state = "available"

    meta = prior_meta if isinstance(prior_meta, Mapping) else {}
    frozen_comparison = {
        "business_change_count": int(comparison.get("business_change_count") or 0),
        "source_driven_change_count": int(comparison.get("source_driven_change_count") or 0),
        "source_change_count": int(comparison.get("source_change_count") or 0),
        "metric_changes": [
            {
                "metric_key": str(change.get("metric_key") or ""),
                "label": str(change.get("label") or ""),
                "before": _json_safe(change.get("before")),
                "after": _json_safe(change.get("after")),
                "delta": _json_safe(change.get("delta")),
                "source_sheet": str(change.get("source_sheet") or ""),
            }
            for change in ranked_moves[:_R171_PAYLOAD_ITEM_CAP]
        ],
        "band_transitions": [
            {key: _json_safe(value) for key, value in item.items()}
            for item in band_transitions[:_R171_PAYLOAD_ITEM_CAP]
        ],
        "action_plan_change_counts": dict(ap_counts),
        "customer_universe": {
            "appeared": len(appeared),
            "no_longer_present": len(disappeared),
        },
        "one_sided_metric_count": len(one_sided_moves),
        "source_state_changes": [
            {key: _json_safe(value) for key, value in item.items()}
            for item in (comparison.get("source_state_changes") or [])[:_R171_PAYLOAD_ITEM_CAP]
            if isinstance(item, Mapping)
        ],
        "caveats": caveat_sentences,
    }

    return {
        "metric_key": "insight.run_delta",
        "display_label": "Movement since last report",
        "paragraph_prefix": prefix,
        "paragraph_text": paragraph_text,
        "canonical_function": (
            "manager_decision_workspace.compare_snapshots; "
            "decision_report_delivery._build_run_delta_insight"
        ),
        "source_sheets": source_sheets,
        "source_states": source_states,
        "source_positions": source_positions,
        "evidence_filters": evidence_filters,
        "source_fields": (
            "canonical KPI Metric_Lineage values; Account_Summary risk bands; "
            "Action_Plans stable record IDs and status/owner/due-date fields; "
            "prior artifact fact fingerprint"
        ),
        "filters": (
            "most recent completed prior report with identical family, manager, "
            "technology, scope, and window; business movement only where both "
            "runs' source coverage is comparable"
        ),
        "grouping": "movement family (risk bands, KPIs, Action Plans)",
        "deduplication": (
            "canonical metric keys; casefolded customer identity; stable Action "
            "Plan record IDs"
        ),
        "empty_state": (
            "paragraph omitted when no completed comparable prior report exists "
            "for this exact scope"
        ),
        "source_state": insight_state,
        "evaluation_as_of_utc": str(facts.get("evaluation_as_of_utc") or ""),
        "prior_report": {
            "fact_fingerprint": prior_fingerprint,
            "analysis_id": str(meta.get("analysis_id") or ""),
            "completed_at": str(meta.get("completed_at") or ""),
            "evaluation_as_of_utc": prior_clock,
        },
        "comparison": frozen_comparison,
    }


def _risk_chart_series(summary: Mapping[str, Any]) -> pd.DataFrame:
    counts = summary.get("risk_band_counts", {}) or {}
    source_state = str(summary.get("source_state") or "available")
    source_unavailable = source_state in {"failed", "unavailable"}
    return pd.DataFrame(
        [
            {
                "Series": "Risk distribution",
                "Category": band,
                "Value": None if source_unavailable else int(counts.get(band, 0)),
                "Source_State": source_state,
            }
            for band in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY")
        ]
    )


def _lineage_row(
    *,
    key: str,
    label: str,
    value: Any,
    scope_type: str,
    scope_value: str,
    canonical_function: str,
    source_sheet: str,
    source_fields: str,
    filters: str,
    grouping: str,
    dedupe: str,
    empty_state: str,
    source_state: str,
    unit: str = "records",
    caveat: str = "",
    preserve_incomplete_claim: bool = False,
    cross_family_parity: str = "",
) -> Dict[str, Any]:
    normalized_state = str(source_state or "unavailable").strip().casefold()
    if normalized_state not in {"available", "zero"} and not preserve_incomplete_claim:
        value = None
        withheld = f"Metric value withheld because the contributing source state is {normalized_state}."
        caveat = " ".join(part for part in (str(caveat).strip(), withheld) if part)
    return {
        "Metric_Key": key,
        "Display_Label": label,
        "Metric_Value": value,
        "Unit": unit,
        "Scope_Type": scope_type,
        "Scope_Value": scope_value,
        "Canonical_Function": canonical_function,
        "Source_Sheet": source_sheet,
        "Source_Fields": source_fields,
        "Filters": filters,
        "Grouping": grouping,
        "Deduplication": dedupe,
        "Empty_State": empty_state,
        "Source_State": normalized_state,
        "Caveat": caveat,
        _CROSS_FAMILY_PARITY_COLUMN: cross_family_parity,
    }


def _retained_customer_count_is_disclosable(value: Any, source_state: Any) -> bool:
    """Return whether a non-complete customer count is still an exact claim.

    ``kpi.customers`` is a distinct count of canonical identities in retained
    selected-scope rows.  Partial coverage makes that count a lower bound, and
    stale coverage makes it historical; neither condition makes the retained
    count itself unknown.  Failed/unavailable states remain withheld.
    """

    normalized_state = str(source_state or "unavailable").strip().casefold()
    return bool(_clean_token(value)) and normalized_state in {"partial", "stale"}


def _retained_customer_count_caveat(source_state: Any) -> str:
    """Describe the exact limitation on a disclosed retained customer count."""

    normalized_state = str(source_state or "unavailable").strip().casefold()
    if normalized_state == "partial":
        return (
            "Metric_Value is the exact count of canonical customer identities "
            "evidenced in retained selected-scope rows; it is a partial-coverage "
            "lower bound, not a complete portfolio total."
        )
    if normalized_state == "stale":
        return (
            "Metric_Value is the exact count of canonical customer identities "
            "evidenced in retained stale selected-scope rows; it is not a current "
            "portfolio total."
        )
    return ""


def _build_lineage(facts: Mapping[str, Any]) -> pd.DataFrame:
    scope_type = facts["scope_type"]
    scope_value = facts["scope_value"]
    days = facts["days"]
    ap = facts["action_plan_lifecycle"]
    coverage = facts["source_coverage"].set_index("Source_Sheet")
    rows: List[Dict[str, Any]] = []

    def state(sheet: str) -> str:
        if sheet == "Report_Info":
            return "available"
        if sheet == "Risk_Components":
            return str(facts["risk_summary"].get("source_state") or "unavailable")
        if sheet == "BEMS":
            sheet = "TAC_Cases"
        if ";" in sheet:
            states = [state(part.strip()) for part in sheet.split(";") if part.strip()]
            if not states:
                return "unavailable"
            known = [value for value in states if value not in {"failed", "unavailable"}]
            if len(known) != len(states) or any(value in {"partial", "stale"} for value in states):
                return "partial" if known else "unavailable"
            if all(value == "zero" for value in states):
                return "zero"
            return "available"
        try:
            return str(coverage.loc[sheet, "Source_State"])
        except Exception:  # noqa: BLE001
            return "unavailable"

    ap_unavailable = str(ap.get("source_state")) in {"failed", "unavailable"}
    risk_unavailable = str(facts["risk_summary"].get("source_state")) in {"failed", "unavailable"}

    metric_contracts = [
        (
            "kpi.team_members",
            "Team members",
            facts["kpis"]["team_members"],
            "canonical_metrics.count_team_members",
            "Report_Info",
            "validated member bundles",
            "distinct email-keyed member bundles; duplicate display names disambiguated",
        ),
        (
            "kpi.customers",
            "Customers",
            facts["kpis"]["customers"],
            "decision_report_delivery._canonical_customer_identities",
            "Subscriptions; Action_Plans; Adoption_Barriers; Customer_Pulse; TAC_Cases; Success_Priorities",
            "stable account/customer ID; exact normalized customer label",
            "stable ID first; otherwise exact suffix-sensitive label; fuzzy alias only for an unambiguous ID-backed join",
        ),
        (
            "kpi.subscriptions",
            "Subscriptions",
            facts["kpis"]["subscriptions"],
            "canonical_metrics.count_distinct_records_by_id",
            "Subscriptions",
            "Record_ID / subscription ID",
            "distinct stable source ID",
        ),
        (
            "kpi.action_plans_total",
            "Action Plans",
            None if ap_unavailable else ap["total"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            str(ap["field_selection"]),
            ap["deduplication_rule"],
        ),
        (
            "kpi.action_plans_open",
            "Open Action Plans",
            None if ap_unavailable else ap["open"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            "status + due date",
            ap["deduplication_rule"],
        ),
        (
            "kpi.action_plans_overdue",
            "Overdue Action Plans",
            None if ap_unavailable else ap["overdue"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            "status + due date",
            ap["deduplication_rule"],
        ),
        (
            "kpi.action_plans_due_soon",
            "Action Plans Due Soon",
            None if ap_unavailable else ap["due_soon"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            "status + due date",
            ap["deduplication_rule"],
        ),
        (
            "kpi.action_plans_completed",
            "Completed Action Plans",
            None if ap_unavailable else ap["completed"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            "status",
            ap["deduplication_rule"],
        ),
        (
            "kpi.action_plans_blocked",
            "Blocked / On Hold Action Plans",
            None if ap_unavailable else ap["blocked_on_hold"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            "status",
            ap["deduplication_rule"],
        ),
        (
            "kpi.action_plans_unknown",
            "Unknown Action Plan Status",
            None if ap_unavailable else ap["unknown"],
            "canonical_metrics.build_action_plan_lifecycle",
            "Action_Plans",
            "status",
            ap["deduplication_rule"],
        ),
        (
            "kpi.adoption_barriers",
            "Adoption Barriers",
            facts["kpis"]["adoption_barriers"],
            "canonical_metrics.count_total_barriers",
            "Adoption_Barriers",
            "Record_ID / ID",
            "distinct stable ID",
        ),
        (
            "kpi.customer_pulse",
            "Customer Pulse",
            facts["kpis"]["customer_pulse"],
            "canonical_metrics.count_total_customer_pulse",
            "Customer_Pulse",
            "Record_ID / ID",
            "distinct stable ID",
        ),
        (
            "kpi.tac_cases",
            "TAC Cases",
            facts["kpis"]["tac_cases"],
            "canonical_metrics.count_total_tac",
            "TAC_Cases",
            "resolved TAC case ID",
            "canonical TAC collapse",
        ),
        (
            "kpi.bems",
            "BEMS escalations (TAC subset)",
            facts["kpis"]["bems"],
            "canonical_metrics.count_bems",
            "BEMS",
            "TAC BEMS identifiers",
            "canonical collapsed TAC case ID; subset, not added to activity total",
        ),
        (
            "kpi.success_priorities",
            "Success Priorities",
            facts["kpis"]["success_priorities"],
            "canonical_metrics.count_distinct_records_by_id",
            "Success_Priorities",
            "Record_ID / success priority ID",
            "distinct stable source ID",
        ),
        (
            "kpi.external_incidents",
            "External Incidents",
            facts["kpis"]["external_incidents"],
            "canonical_metrics.count_distinct_records_by_id",
            "External_Incidents",
            "Record_ID / incident ID",
            "distinct stable source ID",
        ),
        (
            "kpi.external_bugs",
            "External Bugs",
            facts["kpis"]["external_bugs"],
            "canonical_metrics.count_distinct_records_by_id",
            "External_Bugs",
            "Record_ID / bug ID",
            "distinct stable source ID",
        ),
        (
            "kpi.high_risk_customers",
            "High-risk customers",
            None if risk_unavailable else facts["kpis"]["high_risk_customers"],
            "risk_scoring.compute_portfolio_risk_summary",
            "Risk_Components",
            "risk_band",
            "distinct ID-first, suffix-sensitive canonical customer",
        ),
    ]
    for key, label, value, function, sheet, fields, dedupe in metric_contracts:
        metric_source_state = state(sheet)
        disclose_retained_customer_count = (
            key == "kpi.customers"
            and _retained_customer_count_is_disclosable(value, metric_source_state)
        )
        rows.append(
            _lineage_row(
                key=key,
                label=label,
                value=value,
                scope_type=scope_type,
                scope_value=scope_value,
                canonical_function=function,
                source_sheet=sheet,
                source_fields=fields,
                filters=f"selected scope; {days}-day window",
                grouping="portfolio" if scope_type == "team" else scope_type,
                dedupe=dedupe,
                empty_state="0 only when source state is zero; otherwise unavailable/partial",
                source_state=metric_source_state,
                caveat=(
                    _retained_customer_count_caveat(metric_source_state)
                    if disclose_retained_customer_count
                    else ""
                ),
                preserve_incomplete_claim=disclose_retained_customer_count,
            )
        )

    member_columns = (
        (
            "customers",
            1,
            6,
            "Subscriptions; Action_Plans; Adoption_Barriers; Customer_Pulse; TAC_Cases; Success_Priorities",
        ),
        ("open_action_plans", 2, 7, "Action_Plans"),
        ("overdue_action_plans", 3, 8, "Action_Plans"),
        ("barriers", 4, 9, "Adoption_Barriers"),
        ("tac_cases", 5, 10, "TAC_Cases"),
    )
    for member_row in facts.get("member_summary") or []:
        member_identity = evidence_entity_key("member", member_row[0])
        for metric_name, index, state_index, contributing_sheets in member_columns:
            row_state = (
                str(member_row[state_index] or "unavailable").casefold()
                if len(member_row) > state_index
                else state(contributing_sheets)
            )
            rows.append(
                _lineage_row(
                    key=f"summary.{member_identity}.{metric_name}",
                    label=f"{member_row[0]} — {metric_name.replace('_', ' ')}",
                    value=member_row[index],
                    scope_type=scope_type,
                    scope_value=scope_value,
                    canonical_function="decision_report_delivery._build_member_summary",
                    source_sheet="Member_Summary",
                    source_fields=metric_name,
                    filters=f"selected scope; {days}-day window; top-N ranking",
                    grouping="team member",
                    dedupe="email-keyed member identity plus canonical source-specific stable record IDs",
                    empty_state="0 only when contributing source states are zero",
                    source_state=row_state,
                    # Round 152 / C2: make the shared-attribution rule
                    # auditable, not just disclosed in prose.  A reader
                    # reconciling Member_Summary against the team headline in
                    # the workbook needs to know why the columns do not sum.
                    caveat=(
                        "Shared records are attributed to every named team member, so member "
                        "rows can exceed the team total; the team total counts each record once."
                    ),
                )
            )

    member_rows_by_label = {
        str(row[0]): row for row in (facts.get("member_summary") or [])
    }
    for intervention_row in _leader_intervention_rows(facts):
        member_identity = evidence_entity_key("member", intervention_row[0])
        source_member_row = member_rows_by_label.get(str(intervention_row[0]))
        contributor_states = (
            [source_member_row[7], source_member_row[9], source_member_row[10]]
            if source_member_row is not None
            else ["unavailable"]
        )
        rows.append(
            _lineage_row(
                key=f"summary.{member_identity}.manager_intervention",
                label=f"{intervention_row[0]} — manager intervention",
                value=intervention_row[6],
                scope_type=scope_type,
                scope_value=scope_value,
                canonical_function="decision_report_delivery._leader_intervention_rows",
                source_sheet="Member_Summary",
                source_fields="Customers; Open_AP; Overdue_AP; Barriers; TAC_Cases; source states",
                filters=f"selected scope; {days}-day window; deterministic intervention rules",
                grouping="team member",
                dedupe="email-keyed member identity plus canonical source-specific stable record IDs",
                empty_state="explicit validate/no-intervention review only when contributing source states are complete",
                source_state=_derived_source_state(contributor_states),
                unit="manager intervention",
                caveat=(
                    "Shared records are attributed to every named team member, so member rows can exceed the team "
                    "total. Deterministic next-review guidance is derived only from the visible member counts and "
                    "source states; it is not an LLM-authored fact."
                ),
                preserve_incomplete_claim=True,
                cross_family_parity=_FAMILY_PRESENTATION_FACT,
            )
        )

    account_columns = (
        ("risk_score", 2, 8, "Subscriptions; Action_Plans; Adoption_Barriers; Customer_Pulse; TAC_Cases"),
        ("open_action_plans", 3, 9, "Action_Plans"),
        ("overdue_action_plans", 4, 10, "Action_Plans"),
        ("critical_high_barriers", 5, 11, "Adoption_Barriers"),
        ("tac_cases", 6, 12, "TAC_Cases"),
    )
    for account_row in facts.get("account_summary") or []:
        account_slug = _customer_match_key(account_row[0]).replace(" ", "_") or "unknown"
        for metric_name, index, state_index, contributing_sheets in account_columns:
            row_state = (
                str(account_row[state_index] or "unavailable").casefold()
                if len(account_row) > state_index
                else state(contributing_sheets)
            )
            rows.append(
                _lineage_row(
                    key=f"summary.account.{account_slug}.{metric_name}",
                    label=f"{account_row[0]} — {metric_name.replace('_', ' ')}",
                    value=account_row[index],
                    scope_type=scope_type,
                    scope_value=scope_value,
                    canonical_function="decision_report_delivery._build_account_summary",
                    source_sheet="Account_Summary; Risk_Components",
                    source_fields=metric_name,
                    filters=f"selected scope; {days}-day window; top-N risk ranking",
                    grouping="account",
                    dedupe="ID-first, suffix-sensitive customer identity and source-specific stable record IDs",
                    empty_state="0 only when contributing source states are zero",
                    source_state=row_state,
                    unit="score (0–100)" if metric_name == "risk_score" else "records",
                )
            )

    for review_row in _comprehensive_account_evidence_rows(facts):
        account_slug = _customer_match_key(review_row[0]).replace(" ", "_") or "unknown"
        matching_account = next(
            (
                row
                for row in facts.get("account_summary") or []
                if _customer_match_key(row[0]) == _customer_match_key(review_row[0])
            ),
            None,
        )
        contributor_states = (
            [matching_account[9], matching_account[11], matching_account[12]]
            if matching_account is not None
            else ["unavailable"]
        )
        rows.append(
            _lineage_row(
                key=f"summary.account.{account_slug}.review_focus",
                label=f"{review_row[0]} — evidence review focus",
                value=review_row[6],
                scope_type=scope_type,
                scope_value=scope_value,
                canonical_function="decision_report_delivery._comprehensive_account_evidence_rows",
                source_sheet="Account_Summary",
                source_fields="Open_AP; Overdue_AP; Critical_High_Barriers; TAC_Cases; source states",
                filters=f"selected scope; {days}-day window; deterministic evidence-review rules",
                grouping="account",
                dedupe="ID-first, suffix-sensitive canonical customer identity and source-specific stable record IDs",
                empty_state="explicit confirm-zero guidance only when contributing source states are complete",
                source_state=_derived_source_state(contributor_states),
                unit="review focus",
                caveat=(
                    "Deterministic review guidance derived only from exact retained counts and source states; "
                    "it is not an LLM-authored fact or a new risk weight."
                ),
                preserve_incomplete_claim=True,
                cross_family_parity=_FAMILY_PRESENTATION_FACT,
            )
        )

    defect_signals = {
        str(signal.get("evidence_key") or ""): signal
        for signal in (facts.get("decision_signals") or [])
        if signal.get("source_key") == "defect_correlations"
    }
    for record in (facts.get("defect_correlation_bundle") or {}).get("records") or []:
        identity_key = _clean_token(record.get("identity_key"))
        csc_id = _clean_token(record.get("csc_id"))
        metric_key = evidence_entity_key(
            "defect_correlation",
            f"{identity_key}|{csc_id}",
        )
        signal = defect_signals.get(metric_key, {})
        source_sheets = list(record.get("parent_source_sheets") or [])
        if record.get("verified_external_match"):
            source_sheets.append("External_Bugs")
        source_sheets.append("Defect_Correlations")
        rows.append(
            _lineage_row(
                key=metric_key,
                label=f"Exact defect correlation — {csc_id}",
                value=_clean_token(signal.get("signal")),
                scope_type=scope_type,
                scope_value=scope_value,
                canonical_function="defect_correlation.build_defect_correlation_bundle",
                source_sheet="; ".join(dict.fromkeys(source_sheets)),
                source_fields=(
                    "CSC reference fields; stable customer/account identity; external bug ID, status, severity, version"
                ),
                filters="criteria-scoped exact case-insensitive official CSC identifier match",
                grouping="canonical customer identity and CSC ID",
                dedupe="one record per canonical customer identity and CSC ID",
                empty_state=("no row only when criteria-scoped TAC/Barrier evidence has no official CSC reference"),
                source_state=str(
                    signal.get("source_state")
                    or (facts.get("defect_correlation_bundle") or {}).get("source_state")
                    or "unavailable"
                ),
                unit="exact correlation",
                caveat=_clean_token(record.get("action_context")),
                preserve_incomplete_claim=True,
            )
        )

    decision_insights = facts.get("decision_insights") or {}
    if not isinstance(decision_insights, Mapping):
        raise ValueError("canonical decision_insights must be a mapping")
    unexpected_insights = sorted(set(decision_insights) - set(_DECISION_INSIGHT_ORDER))
    if unexpected_insights:
        raise ValueError("canonical decision_insights contains unsupported keys: " + ", ".join(unexpected_insights))
    for insight_name in _DECISION_INSIGHT_ORDER:
        insight = decision_insights.get(insight_name)
        if not insight:
            continue
        if not isinstance(insight, Mapping):
            raise ValueError(f"canonical decision insight {insight_name} must be a mapping")
        metric_key = _clean_token(insight.get("metric_key"))
        paragraph_text = _clean_token(insight.get("paragraph_text"))
        source_sheets = [_clean_token(value) for value in (insight.get("source_sheets") or []) if _clean_token(value)]
        if not metric_key or not paragraph_text or not source_sheets:
            raise ValueError(f"canonical decision insight {insight_name} lacks claim or source identity")
        rows.append(
            _lineage_row(
                key=metric_key,
                label=_clean_token(insight.get("display_label")) or insight_name,
                value=paragraph_text,
                scope_type=scope_type,
                scope_value=scope_value,
                canonical_function=_clean_token(insight.get("canonical_function")),
                source_sheet="; ".join(source_sheets),
                source_fields=_clean_token(insight.get("source_fields")),
                filters=_clean_token(insight.get("filters")),
                grouping=_clean_token(insight.get("grouping")),
                dedupe=_clean_token(insight.get("deduplication")),
                empty_state=_clean_token(insight.get("empty_state")),
                source_state=_clean_token(insight.get("source_state")),
                unit="frozen claim",
                caveat=(
                    "Predictive score is a relative ranking, not a probability, unless "
                    "the frozen claim explicitly carries live calibration evidence."
                    if insight_name == "predictive_outlook"
                    else ""
                ),
                preserve_incomplete_claim=True,
            )
        )

    for _, chart_row in facts["chart_data"].iterrows():
        rows.append(
            _lineage_row(
                key=str(chart_row["Metric_Key"]),
                label=str(chart_row["Display_Label"]),
                value=chart_row.get("Value"),
                scope_type=scope_type,
                scope_value=scope_value,
                canonical_function=str(chart_row["Canonical_Function"]),
                source_sheet=str(chart_row["Source_Sheet"]),
                source_fields=str(chart_row.get("Source_Fields") or ""),
                filters=f"selected scope; {days}-day window",
                grouping=str(chart_row.get("Grouping") or "category"),
                dedupe=str(chart_row.get("Deduplication") or "canonical source record ID"),
                empty_state="chart omitted with coverage explanation when unavailable",
                source_state=str(chart_row.get("Source_State") or "available"),
                unit=str(chart_row.get("Unit") or "records"),
                caveat=str(chart_row.get("Caveat") or ""),
            )
        )
    lineage = pd.DataFrame(rows)
    if "Metric_Key" in lineage.columns:
        duplicate_keys = sorted(
            set(
                lineage.loc[
                    lineage["Metric_Key"].fillna("").astype(str).duplicated(keep=False),
                    "Metric_Key",
                ].astype(str)
            )
        )
        if duplicate_keys:
            raise ValueError(
                "Canonical Metric_Lineage contains duplicate evidence identities: " + ", ".join(duplicate_keys[:20])
            )
    return lineage


def _build_chart_data(
    activity_mix: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    risk_summary: Mapping[str, Any],
    trend: Mapping[str, Any],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for _, row in activity_mix["series"].iterrows():
        slug = re.sub(r"[^a-z0-9]+", "_", str(row["Category"]).lower()).strip("_")
        rows.append(
            {
                "Chart_ID": "activity_mix",
                "Metric_Key": f"chart.activity_mix.{slug}",
                "Display_Label": f"Activity mix — {row['Category']}",
                "Series": "Activity mix",
                "Category": row["Category"],
                "Period_Start": pd.NaT,
                "Value": row["Value"],
                "Unit": "records",
                "Source_Sheet": str(row["Category"]).replace(" ", "_"),
                "Source_Fields": "canonical stable record ID",
                "Canonical_Function": "canonical_metrics.build_activity_mix",
                "Grouping": "source type",
                "Deduplication": "source-specific stable record ID",
                "Source_State": row["Source_State"],
                "Caveat": "BEMS is a TAC subset and is not added to this series.",
            }
        )
    for _, row in cm.action_plan_chart_series(dict(lifecycle)).iterrows():
        slug = re.sub(r"[^a-z0-9]+", "_", str(row["Category"]).lower()).strip("_")
        is_age_series = str(row["Series"]) == cm.ACTION_PLAN_AGE_SERIES
        display_category = _action_plan_chart_category_display(
            row["Category"],
            age_series=is_age_series,
        )
        metric_family = "age" if is_age_series else "status"
        grouping = (
            "mutually exclusive unresolved-plan age band; completed plans excluded"
            if is_age_series
            else "mutually exclusive lifecycle status bucket; all plans included"
        )
        caveat = (
            "Age is measured from the selected created/open date to the explicit "
            "evaluation as-of date; completed plans are excluded. Missing or future "
            "created/open dates are reported as Created date unavailable."
            if is_age_series
            else (
                f"Due Soon means 0–{lifecycle['due_soon_days']} days from as-of date. "
                f"{lifecycle.get('source_state_detail') or ''}"
            ).strip()
        )
        rows.append(
            {
                "Chart_ID": "action_plan_status_aging",
                "Metric_Key": f"chart.action_plan_{metric_family}.{slug}",
                "Display_Label": f"{row['Series']} — {display_category}",
                "Series": row["Series"],
                # Keep the canonical bucket for internal partition/evidence
                # reconciliation.  Public export maps it to Display_Category.
                "Category": row["Category"],
                "Display_Category": display_category,
                "Period_Start": pd.NaT,
                "Value": row["Value"],
                "Unit": "records",
                "Source_Sheet": "Action_Plans",
                "Source_Fields": str(lifecycle["field_selection"]),
                "Canonical_Function": "canonical_metrics.action_plan_chart_series",
                "Grouping": grouping,
                "Deduplication": lifecycle["deduplication_rule"],
                "Source_State": row.get("Source_State") or lifecycle.get("source_state") or "available",
                "Caveat": caveat,
            }
        )
    for _, row in _risk_chart_series(risk_summary).iterrows():
        slug = str(row["Category"]).lower()
        rows.append(
            {
                "Chart_ID": "risk_distribution",
                "Metric_Key": f"chart.risk_distribution.{slug}",
                "Display_Label": f"Risk distribution — {row['Category']}",
                "Series": row["Series"],
                "Category": row["Category"],
                "Period_Start": pd.NaT,
                "Value": row["Value"],
                "Unit": "customers",
                "Source_Sheet": "Risk_Components",
                "Source_Fields": "risk_score_0_100; risk_band",
                "Canonical_Function": "risk_scoring.compute_portfolio_risk_summary",
                "Grouping": "canonical risk band",
                "Deduplication": "distinct ID-first, suffix-sensitive canonical customer",
                "Source_State": (row.get("Source_State") or risk_summary.get("source_state") or "unavailable"),
                "Caveat": str(risk_summary.get("source_state_detail") or ""),
            }
        )
    for _, row in trend["series"].iterrows():
        period = pd.Timestamp(row["Period_Start"]).strftime("%Y-%m-%d")
        source_slug = re.sub(r"[^a-z0-9]+", "_", str(row["Source"]).lower()).strip("_")
        rows.append(
            {
                "Chart_ID": "activity_trend",
                "Metric_Key": f"chart.activity_trend.{source_slug}.{period}",
                "Display_Label": f"Activity trend — {row['Source']} — {period}",
                "Series": row["Source"],
                "Category": row["Source"],
                "Period_Start": row["Period_Start"],
                "Value": int(row["Value"]),
                "Unit": "records",
                "Source_Sheet": str(row["Source"]).replace(" ", "_"),
                "Source_Fields": "selected date field in trend coverage",
                "Canonical_Function": "canonical_metrics.build_activity_trend",
                "Grouping": f"weekly ({trend['frequency']})",
                "Deduplication": "source-specific stable record ID before period grouping",
                "Source_State": row.get("Source_State") or "available",
                "Caveat": "Records with missing/invalid dates are excluded and disclosed in coverage.",
            }
        )
    chart_data = pd.DataFrame(rows)
    if chart_data.empty:
        return chart_data

    normalized_states = (
        chart_data["Source_State"].fillna("unavailable").astype(str).str.casefold()
    )
    incomplete_mask = ~normalized_states.isin({"available", "zero"})
    if incomplete_mask.any():
        chart_data.loc[incomplete_mask, "Value"] = None
        withheld_note = (
            "Chart value withheld because this source series "
            "is partial, stale, failed, or unavailable."
        )
        chart_data.loc[incomplete_mask, "Caveat"] = chart_data.loc[incomplete_mask, "Caveat"].map(
            lambda value: " ".join(part for part in (str(value or "").strip(), withheld_note) if part)
        )
    return chart_data


def build_report_facts(
    team_data: Mapping[str, Mapping[str, Any]],
    *,
    report_type: str,
    scope_type: str,
    scope_value: str,
    manager_name: str,
    technology: str = "",
    days: int,
    as_of: Any,
    data_as_of_utc: Any = None,
    # Round 153 / Tier 3: default fails closed.  ``as_of_utc`` is the
    # source-retrieval clock and must never be impersonated by the
    # evaluation/generation clock; a caller that supplies no retrieval
    # state must land on the honest 'unavailable' branch, not a verified
    # freshness claim.
    data_as_of_state: str = "unknown",
    data_as_of_detail: str = "",
    retrieval_attempted_at_utc: Any = "",
    data_mode: str = "",
    live_validation_performed: Optional[bool] = None,
    external_incidents: Optional[Sequence[Mapping[str, Any]]] = None,
    external_bugs: Optional[Sequence[Mapping[str, Any]]] = None,
    partial_data_warnings: Optional[Sequence[Mapping[str, Any]]] = None,
    top_item_limit: int = TOP_ITEM_LIMIT_DEFAULT,
    prior_snapshot: Optional[Mapping[str, Any]] = None,
    prior_snapshot_meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the single fact bundle consumed by Word and Source Data.

    Round 171: ``prior_snapshot`` (a canonical workspace snapshot of the most
    recent completed same-scope report, resolved by the caller) enables the
    frozen run-over-run movement insight.  ``None`` — the default, and the
    only value offline acceptance ever passes — leaves every artifact
    byte-identical to the pre-171 contract.
    """

    as_of_ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(as_of_ts):
        raise ValueError("build_report_facts requires a valid explicit as_of timestamp")
    if data_as_of_utc is None:
        # Round 153 / Tier 3: was ``public_as_of_utc = as_of_ts.isoformat()``
        # -- i.e. the evaluation clock stamped as verified source
        # freshness.  A missing retrieval clock now yields a blank public
        # as-of, which forces the honest 'Data as of unavailable' subtitle.
        public_as_of_utc = ""
    elif not str(data_as_of_utc).strip():
        public_as_of_utc = ""
    else:
        public_as_of_ts = pd.to_datetime(data_as_of_utc, errors="coerce", utc=True)
        if pd.isna(public_as_of_ts):
            raise ValueError("build_report_facts data_as_of_utc must be blank or a valid timestamp")
        public_as_of_utc = public_as_of_ts.isoformat()
    normalized_as_of_state = str(data_as_of_state or "available").strip().casefold()
    if normalized_as_of_state not in {
        "available",
        "partial",
        "stale",
        "failed",
        "unavailable",
        "unknown",
    }:
        normalized_as_of_state = "unknown"
    if not public_as_of_utc and normalized_as_of_state == "available":
        normalized_as_of_state = "unavailable"
    normalized_as_of_detail = str(data_as_of_detail or "").strip()
    if not normalized_as_of_detail:
        normalized_as_of_detail = (
            "Source retrieval timestamp recorded."
            if normalized_as_of_state == "available"
            else "Source freshness is not fully available for this report run."
        )
    retrieval_attempted_ts = pd.to_datetime(retrieval_attempted_at_utc, errors="coerce", utc=True)
    normalized_retrieval_attempted_at = "" if pd.isna(retrieval_attempted_ts) else retrieval_attempted_ts.isoformat()
    team_data = _normalize_scoped_team_attribution(
        team_data,
        scope_type=scope_type,
        scope_value=scope_value,
    )
    frames = aggregate_team_frames(
        team_data,
        scope_type=scope_type,
        scope_value=scope_value,
        preserve_action_plan_observations=True,
    )
    frames["adoption_barriers"] = _enrich_barrier_technology_from_subscriptions(
        frames["adoption_barriers"],
        frames["subscriptions"],
    )
    # The paired workbook exposes TAC lifecycle ages as canonical evidence.
    # Rebuild those fields against the report clock after aggregation so the
    # same frozen facts cannot acquire different ages on a later machine or
    # day.  Preserve source-state attrs; pandas transformations do not promise
    # that custom provenance survives every version/path.
    tac_attrs = dict(getattr(frames["tac_cases"], "attrs", {}) or {})
    frames["tac_cases"] = add_case_lifecycle_fields(
        frames["tac_cases"],
        as_of=as_of_ts,
    )
    frames["tac_cases"].attrs.update(tac_attrs)
    frames["tac_cases"], tac_case_type_coverage = _decorate_tac_case_type_quality(
        frames["tac_cases"]
    )
    normalized_warnings: List[Dict[str, Any]] = []
    for warning in partial_data_warnings or ():
        if isinstance(warning, Mapping):
            normalized_warnings.append(dict(warning))
        elif _clean_token(warning):
            normalized_warnings.append(
                {
                    "dataset": "Report data",
                    "kind": "partial",
                    "effect": _clean_token(warning),
                }
            )
    # A locally generated artifact can look indistinguishable from a live
    # production report once it leaves the acceptance folder.  Carry the
    # guarded fixture provenance from the source frames into the canonical
    # facts so Word and Source Data both disclose it.  This is deliberately a
    # warning only: fixture rows still exercise the complete report path, but
    # they can never be presented as live Cisco validation.
    fixture_mode = any(
        str((getattr(frame, "attrs", {}) or {}).get("source_mode") or "")
        .strip()
        .casefold()
        == "local_acceptance_fixture"
        for frame in frames.values()
        if isinstance(frame, pd.DataFrame)
    )
    normalized_data_mode = _clean_token(data_mode)
    if not normalized_data_mode:
        normalized_data_mode = (
            "Guarded offline fixture"
            if fixture_mode
            else "Application source path"
        )
    normalized_live_validation: Optional[bool]
    if live_validation_performed is None:
        normalized_live_validation = False if fixture_mode else None
    else:
        normalized_live_validation = bool(live_validation_performed)
    # Fixture provenance is authoritative: a caller cannot relabel guarded
    # local data as live merely by passing an optimistic flag.
    if fixture_mode:
        normalized_live_validation = False
    if fixture_mode and not any(
        "fixture" in str(warning.get("kind") or "").casefold()
        or "offline test" in str(warning.get("effect") or "").casefold()
        for warning in normalized_warnings
    ):
        normalized_warnings.append(
            {
                "dataset": "Live source validation",
                "kind": "local_acceptance_fixture",
                "effect": (
                    "This report uses guarded, sanitized offline test data. "
                    "Snowflake and other live Cisco sources were not queried or validated."
                ),
            }
        )
    for source_key, frame in frames.items():
        conflict_count = int(
            (getattr(frame, "attrs", {}) or {}).get(
                "stable_id_conflicting_record_count",
                0,
            )
            or 0
        )
        if not conflict_count:
            continue
        quarantined_count = int(
            (getattr(frame, "attrs", {}) or {}).get(
                "stable_id_quarantined_observation_count",
                0,
            )
            or 0
        )
        normalized_warnings.append(
            {
                "dataset": _FRAME_KEYS.get(source_key, source_key),
                "kind": "stable_id_conflict",
                "effect": (
                    f"{conflict_count} conflicting stable-ID record(s), "
                    f"representing {quarantined_count} source observation(s), "
                    "were quarantined from published facts."
                ),
            }
        )
    lifecycle = cm.build_action_plan_lifecycle(frames["action_plans"], as_of=as_of_ts)
    if int(lifecycle.get("conflicting_stable_id_count") or 0):
        normalized_warnings.append(
            {
                "dataset": _FRAME_KEYS["action_plans"],
                "kind": "stable_id_conflict",
                "effect": (
                    f"{int(lifecycle.get('conflicting_stable_id_count') or 0)} "
                    "conflicting stable-ID record(s), representing "
                    f"{int(lifecycle.get('quarantined_conflicting_observation_count') or 0)} "
                    "source observation(s), were quarantined from published facts."
                ),
            }
        )
    frames["action_plans"] = lifecycle["records"].copy()
    frames["action_plans"].attrs.update(
        {
            "fetch_error": lifecycle.get("source_state_detail")
            if lifecycle.get("source_state") in {"failed", "partial"}
            else None,
            "fetch_error_partial": lifecycle.get("source_state") == "partial",
            "stale": lifecycle.get("source_state") == "stale",
            "source_unavailable": lifecycle.get("source_state") == "unavailable",
            "source_unavailable_detail": lifecycle.get("source_state_detail"),
            "source_mode_detail": lifecycle.get("source_state_detail"),
        }
    )
    # External sources must be normalized and source-state decorated before
    # risk scoring.  Scoring raw ``None``/lists first collapsed a failed feed
    # into a healthy zero and fanned untagged portfolio incidents into every
    # customer profile.
    external_incidents_frame = _external_frame(external_incidents)
    external_bugs_frame = _external_frame(external_bugs, possible_cap=500)
    if external_incidents is None:
        external_incidents_frame.attrs.update(
            {
                "source_unavailable": True,
                "source_unavailable_detail": "external incident source not supplied",
            }
        )
    if external_bugs is None:
        external_bugs_frame.attrs.update(
            {
                "source_unavailable": True,
                "source_unavailable_detail": "external bug source not supplied",
            }
        )
    external_incidents_frame = _decorate_standalone_source(
        external_incidents_frame,
        key="external_incidents",
        scope_type=scope_type,
        scope_value=scope_value,
    )
    external_bugs_frame = _decorate_standalone_source(
        external_bugs_frame,
        key="external_bugs",
        scope_type=scope_type,
        scope_value=scope_value,
    )
    activity_mix = cm.build_activity_mix(
        action_plans_df=frames["action_plans"],
        ab_df=frames["adoption_barriers"],
        customer_pulse_df=frames["customer_pulse"],
        tac_df=frames["tac_cases"],
    )
    trend = cm.build_activity_trend(
        action_plans_df=frames["action_plans"],
        ab_df=frames["adoption_barriers"],
        customer_pulse_df=frames["customer_pulse"],
        tac_df=frames["tac_cases"],
        as_of=as_of_ts,
        days=days,
    )
    risk_profiles = _build_risk_profiles(
        frames,
        days=days,
        as_of=as_of_ts,
        external_incidents=external_incidents_frame,
    )
    risk_summary = compute_portfolio_risk_summary(risk_profiles)
    risk_input_states = {
        key: cm.source_data_state(frame)["state"]
        for key, frame in {
            "subscriptions": frames["subscriptions"],
            "action_plans": frames["action_plans"],
            "adoption_barriers": frames["adoption_barriers"],
            "customer_pulse": frames["customer_pulse"],
            "tac_cases": frames["tac_cases"],
            "external_incidents": external_incidents_frame,
        }.items()
    }
    incomplete_risk_sources = sorted(
        key for key, state in risk_input_states.items() if state in {"failed", "unavailable", "partial", "stale"}
    )
    if risk_profiles:
        risk_source_state = "partial" if incomplete_risk_sources else "available"
    elif any(state in {"failed", "unavailable"} for state in risk_input_states.values()):
        risk_source_state = "unavailable"
    else:
        risk_source_state = "zero"
    risk_summary["source_state"] = risk_source_state
    risk_summary["source_state_detail"] = (
        "Incomplete risk evidence: " + ", ".join(incomplete_risk_sources)
        if incomplete_risk_sources
        else "Risk evidence sources available"
    )

    tac_for_bems, _ = cm.deduplicate_records_by_id(
        frames["tac_cases"],
        id_candidates=_ID_CANDIDATES["tac_cases"],
    )
    try:
        bems = tac_for_bems.loc[detect_bems_mask(tac_for_bems)].copy()
    except Exception:  # noqa: BLE001
        bems = pd.DataFrame()
    if not bems.empty:
        bems["BEMS_Source_Type"] = "TAC Case"
    bems = _with_public_record_id(bems, "tac_cases")
    bems["Scope_Type"] = scope_type
    bems["Scope_Value"] = scope_value
    if "Source_System" not in bems.columns:
        bems["Source_System"] = "CSOne / CSConsole"
    else:
        bems["Source_System"] = bems["Source_System"].fillna("CSOne / CSConsole")
    if "Attributed_Team_Members" not in bems.columns:
        bems["Attributed_Team_Members"] = bems.get("CSSM", pd.Series(dtype="object"))
    if "CSSM" not in bems.columns:
        bems["CSSM"] = pd.Series(dtype="object")

    kpis = {
        "team_members": cm.count_team_members(dict(team_data)),
        "customers": len(risk_profiles),
        "subscriptions": cm.count_distinct_records_by_id(
            frames["subscriptions"],
            id_candidates=_ID_CANDIDATES["subscriptions"],
        ),
        "action_plans_total": lifecycle["total"],
        "action_plans_open": lifecycle["open"],
        "action_plans_overdue": lifecycle["overdue"],
        "action_plans_due_soon": lifecycle["due_soon"],
        "action_plans_completed": lifecycle["completed"],
        "action_plans_blocked": lifecycle["blocked_on_hold"],
        "action_plans_unknown": lifecycle["unknown"],
        "adoption_barriers": cm.count_total_barriers(frames["adoption_barriers"]),
        "customer_pulse": cm.count_total_customer_pulse(frames["customer_pulse"]),
        "tac_cases": cm.count_total_tac(frames["tac_cases"]),
        "bems": cm.count_bems(frames["tac_cases"]),
        "success_priorities": cm.count_distinct_records_by_id(
            frames["success_priorities"],
            id_candidates=_ID_CANDIDATES["success_priorities"],
        ),
        "known_total_activities": activity_mix["known_total"],
        "high_risk_customers": int(risk_summary.get("high_risk_customers", 0)),
    }
    kpis["external_incidents"] = cm.count_distinct_records_by_id(
        external_incidents_frame,
        id_candidates=_ID_CANDIDATES["external_incidents"],
    )
    kpis["external_bugs"] = cm.count_distinct_records_by_id(
        external_bugs_frame,
        id_candidates=_ID_CANDIDATES["external_bugs"],
    )
    defect_correlation_bundle = _build_canonical_defect_correlations(
        frames,
        external_bugs=external_bugs_frame,
        scope_type=scope_type,
        scope_value=scope_value,
    )
    defect_correlations_frame = defect_correlation_bundle["frame"]
    defect_identity_coverage = (
        (defect_correlation_bundle.get("coverage") or {}).get("identity_resolution")
        or {}
    )
    quarantined_defect_rows = int(
        defect_identity_coverage.get("quarantined_observation_count") or 0
    ) + int(defect_identity_coverage.get("delivery_quarantined_observation_count") or 0)
    if (
        str(defect_identity_coverage.get("state") or "").casefold() == "partial"
        and quarantined_defect_rows
    ):
        normalized_warnings.append(
            {
                "dataset": "Defect correlations",
                "kind": "identity_resolution_partial",
                "effect": (
                    f"{quarantined_defect_rows} CSC-bearing source row(s) were retained in "
                    "their source sheets but withheld from account-level defect correlations "
                    "because no canonical customer identity was available."
                ),
            }
        )
    decision_signals = _build_cross_source_decision_signals(
        frames,
        external_incidents=external_incidents_frame,
        external_bugs=external_bugs_frame,
        scope_value=scope_value,
        as_of=as_of_ts,
    )
    decision_signals.extend(defect_correlation_bundle.get("signals") or [])
    decision_insights = _build_decision_insights(
        frames,
        as_of=as_of_ts,
        days=days,
    )
    source_coverage = _source_coverage(frames)
    tac_coverage_mask = source_coverage["Source_Sheet"].eq("TAC_Cases")
    if bool(tac_coverage_mask.any()) and tac_case_type_coverage["total"]:
        coverage_detail = (
            f"Case-type classification coverage {tac_case_type_coverage['classified']}/"
            f"{tac_case_type_coverage['total']} "
            f"({tac_case_type_coverage['coverage_percent']:.1f}%); "
            f"{tac_case_type_coverage['not_derivable']} row(s) could not be separated "
            "into break/fix or provisioning from available source fields."
        )
        source_coverage.loc[tac_coverage_mask, "Detail"] = source_coverage.loc[
            tac_coverage_mask, "Detail"
        ].map(lambda value: " ".join(part for part in (str(value or "").strip(), coverage_detail) if part))
    bems_inputs = [cm.source_data_state(frames["tac_cases"])["state"]]
    if all(value in {"failed", "unavailable"} for value in bems_inputs):
        bems_state = "unavailable"
    elif any(value in {"failed", "unavailable", "partial", "stale"} for value in bems_inputs):
        bems_state = "partial"
    else:
        bems_state = "available" if not bems.empty else "zero"
    # Build the extension from records instead of concatenating potentially
    # all-NA ``Record_Count`` columns.  Pandas 2.2+ warns that concat's dtype
    # inference for that case is changing; an explicit record rebuild keeps
    # the user-visible values and column order deterministic across supported
    # pandas versions.
    source_coverage_rows = source_coverage.to_dict(orient="records")
    source_coverage_rows.extend(
        [
            {
                "Source_Sheet": "BEMS",
                "Source_State": bems_state,
                "Record_Count": None if bems_state == "unavailable" else int(len(bems)),
                "Detail": "Derived TAC escalation subset; not additive activity.",
            },
            {
                "Source_Sheet": "External_Incidents",
                "Source_State": cm.source_data_state(external_incidents_frame)["state"],
                "Record_Count": (
                    None
                    if cm.source_data_state(external_incidents_frame)["state"] in {"failed", "unavailable"}
                    else int(len(external_incidents_frame))
                ),
                "Detail": cm.source_data_state(external_incidents_frame)["detail"],
            },
            {
                "Source_Sheet": "External_Bugs",
                "Source_State": cm.source_data_state(external_bugs_frame)["state"],
                "Record_Count": (
                    None
                    if cm.source_data_state(external_bugs_frame)["state"] in {"failed", "unavailable"}
                    else int(len(external_bugs_frame))
                ),
                "Detail": cm.source_data_state(external_bugs_frame)["detail"],
            },
            {
                "Source_Sheet": "Defect_Correlations",
                "Source_State": str(defect_correlation_bundle.get("source_state") or "unavailable"),
                "Record_Count": (
                    None
                    if str(defect_correlation_bundle.get("source_state") or "unavailable") == "unavailable"
                    else int(len(defect_correlations_frame))
                ),
                "Detail": (
                    "Exact scoped CSC joins across TAC, Adoption Barriers, and external bugs; "
                    "no independent numeric risk weight. "
                    + str(defect_identity_coverage.get("detail") or "")
                ),
            },
        ]
    )
    source_coverage = pd.DataFrame(
        source_coverage_rows,
        columns=["Source_Sheet", "Source_State", "Record_Count", "Detail"],
    )
    raw_member_summary = _build_member_summary(
        team_data,
        as_of=as_of_ts,
        limit=None,
    )
    # Portfolio records that cannot be mapped to one named owner still drive
    # account and portfolio totals and remain in the raw Source Data sheets.
    # They must not appear as a person in a "Team-Member Summary" or inflate
    # the number of attributable direct reports.
    unassigned_portfolio_summary = next(
        (row for row in raw_member_summary if str(row[0]).strip().casefold() == "unassigned / portfolio"),
        None,
    )
    member_summary_all = [
        row for row in raw_member_summary if str(row[0]).strip().casefold() != "unassigned / portfolio"
    ]
    account_summary_all = _build_account_summary(
        risk_profiles,
        frames,
        as_of=as_of_ts,
        limit=None,
    )
    facts: Dict[str, Any] = {
        "report_type": str(report_type),
        "scope_type": str(scope_type),
        "scope_value": str(scope_value),
        "manager_name": str(manager_name),
        "technology": str(technology or ""),
        "days": int(days),
        # ``evaluation_as_of_utc`` drives deterministic aging calculations.
        # ``as_of_utc`` is only the public source-retrieval clock and may be
        # blank; attempt, evaluation, or generation time must never impersonate
        # source freshness.
        "as_of_utc": public_as_of_utc,
        "data_as_of_state": normalized_as_of_state,
        "data_as_of_detail": normalized_as_of_detail,
        "retrieval_attempted_at_utc": normalized_retrieval_attempted_at,
        "evaluation_as_of_utc": as_of_ts.isoformat(),
        "data_mode": normalized_data_mode,
        "live_validation_performed": normalized_live_validation,
        "frames": frames,
        # Query/adapter routes are diagnostic provenance, not customer facts.
        # Publish only counts and digests in-memory so they remain auditable
        # without leaking local paths or making report-family transport part
        # of exact cross-family semantic parity.
        "source_observation_route_diagnostics": _source_observation_route_diagnostics(
            {
                **frames,
                "external_incidents": external_incidents_frame,
                "external_bugs": external_bugs_frame,
            }
        ),
        "bems": bems,
        "external_incidents": external_incidents_frame,
        "external_bugs": external_bugs_frame,
        "defect_correlations": defect_correlations_frame,
        "defect_correlation_bundle": defect_correlation_bundle,
        "decision_signals": decision_signals,
        "decision_insights": decision_insights,
        "partial_data_warnings": normalized_warnings,
        "action_plan_lifecycle": lifecycle,
        "activity_mix": activity_mix,
        "activity_trend": trend,
        "risk_profiles": risk_profiles,
        "risk_summary": risk_summary,
        "kpis": kpis,
        "source_coverage": source_coverage,
        "tac_case_type_coverage": tac_case_type_coverage,
        "ranking_criteria": (
            "Top items rank overdue first, then blocked/on hold, due soon, open, and "
            "source-status unresolved; "
            "ties use canonical priority, due date, and stable source ID."
        ),
        "top_action_plans": _prioritized_action_plan_rows(lifecycle, top_item_limit),
        # Round 165: this ceiling protects only against pathological roster
        # fan-out.  Ordinary configured Leader teams render every named member;
        # accounts and Action Plans keep the standard decision-focused top-N.
        # The overflow disclosure below remains for an abnormal >64-row scope.
        "member_summary": member_summary_all[: max(int(MEMBER_ITEM_LIMIT_DEFAULT), 1)],
        "member_summary_all": member_summary_all,
        "member_summary_omitted": max(len(member_summary_all) - max(int(MEMBER_ITEM_LIMIT_DEFAULT), 1), 0),
        "unassigned_portfolio_summary": unassigned_portfolio_summary,
        "account_summary": account_summary_all[: max(int(top_item_limit), 1)],
        "account_summary_all": account_summary_all,
        "account_summary_omitted": max(len(account_summary_all) - max(int(top_item_limit), 1), 0),
    }
    facts["chart_data"] = _build_chart_data(activity_mix, lifecycle, risk_summary, trend)
    facts["metric_lineage"] = _build_lineage(facts)
    if prior_snapshot is not None:
        # Round 171: the current-side projection must be sound (its failures
        # propagate), but a structurally broken PRIOR may only cost us the
        # comparison, never the report.  Comparable-but-inconsistent claims
        # still raise inside the builder by design.
        after_error: Optional[Exception] = None
        try:
            run_delta = _build_run_delta_insight(
                facts,
                prior_snapshot,
                prior_meta=prior_snapshot_meta,
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            if isinstance(exc, _RunDeltaCurrentSideError):
                raise
            after_error = exc
            run_delta = None
        if after_error is not None:
            logger.warning(
                "Round 171: run-over-run comparison skipped (prior-side failure: %s)",
                type(after_error).__name__,
            )
        if run_delta:
            facts["decision_insights"]["run_delta"] = run_delta
            facts["metric_lineage"] = _build_lineage(facts)
    return facts


def evidence_entity_key(kind: object, value: object) -> str:
    """Return a stable public key for one record-backed decision entity."""

    kind_token = re.sub(r"[^a-z0-9]+", "_", str(kind or "entity").casefold()).strip("_")
    raw = _clean_token(value) or "missing"
    slug = re.sub(r"[^a-z0-9]+", "_", raw.casefold()).strip("_")[:48] or "record"
    digest = hashlib.sha256(f"{kind_token}|{raw}".encode("utf-8")).hexdigest()[:10]
    return f"{kind_token}.{slug}.{digest}"


_CHART_LINEAGE_FAMILIES: "OrderedDict[str, Tuple[str, ...]]" = OrderedDict(
    [
        ("activity_mix", ("chart.activity_mix.*",)),
        (
            "action_plan_status_aging",
            ("chart.action_plan_status.*", "chart.action_plan_age.*"),
        ),
        ("risk_distribution", ("chart.risk_distribution.*",)),
        ("activity_trend", ("chart.activity_trend.*",)),
    ]
)


def _chart_lineage_family_tokens(chart_ids: Iterable[object]) -> List[str]:
    """Return the real Metric_Lineage families rendered for chart groups.

    ``action_plan_status_aging`` is a presentation/chart ID, not an evidence
    family.  Its two panels are independently traceable through the canonical
    ``chart.action_plan_status.*`` and ``chart.action_plan_age.*`` keys.
    """

    tokens: List[str] = []
    for chart_id in chart_ids:
        chart_id_token = _clean_token(chart_id)
        families = _CHART_LINEAGE_FAMILIES.get(
            chart_id_token,
            (f"chart.{chart_id_token}.*",) if chart_id_token else (),
        )
        for family in families:
            if family not in tokens:
                tokens.append(family)
    return tokens


def _artifact_reference_text(location: str, tokens: object) -> str:
    """Freeze one visible Source Data reference with explicit resolvable keys."""

    if isinstance(tokens, str):
        token_text = tokens.strip()
    else:
        token_text = "; ".join(_clean_token(token) for token in (tokens or []) if _clean_token(token))
    return f"[Source: Source Data File → {location} / {token_text}]"


def _keys_matching_reference(token: object, available_keys: Iterable[object]) -> set[str]:
    """Resolve an exact evidence key or one explicit ``family.*`` reference."""

    reference = _clean_token(token)
    keys = {_clean_token(key) for key in available_keys if _clean_token(key)}
    if reference.endswith(".*"):
        prefix = reference[:-1]
        return {key for key in keys if key.startswith(prefix)}
    return {reference} if reference in keys else set()


def _expected_chart_reference_groups(facts: Mapping[str, Any]) -> List[Tuple[str, ...]]:
    """Return the exact chart-family citations the Word renderer must expose."""

    chart_data = facts.get("chart_data")
    if not isinstance(chart_data, pd.DataFrame):
        chart_data = pd.DataFrame()
    present_chart_ids = set(chart_data.get("Chart_ID", pd.Series(dtype=str)).dropna().astype(str))
    chart_ids = tuple(chart_id for chart_id in _CHART_LINEAGE_FAMILIES if chart_id in present_chart_ids)
    if not chart_ids:
        return []
    return [tuple(_chart_lineage_family_tokens([chart_id])) for chart_id in chart_ids]


def _evidence_row_fingerprint(row: Mapping[str, Any]) -> str:
    payload = {
        str(column): _digest_cell(value)
        for column, value in sorted(row.items(), key=lambda item: str(item[0]))
        if not str(column).startswith("_")
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_evidence_links(
    facts: Mapping[str, Any],
    sheets: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Map every visible canonical claim to exact rows in the paired workbook.

    ``Source_Row_Number`` is the 1-based Excel row after the header.  It makes
    even a source record with a missing native ID exactly resolvable inside the
    immutable, fingerprinted workbook.  ``Source_Row_SHA256`` detects row
    substitution or movement before a UI/AI drill-down is returned.
    """

    columns = [
        "Evidence_Key",
        "Evidence_Type",
        "Display_Label",
        "Metric_Value",
        "Unit",
        "Evidence_Role",
        _CROSS_FAMILY_PARITY_COLUMN,
        "Source_Sheet",
        "Source_Row_Number",
        "Source_Row_SHA256",
        "Record_ID",
        "Record_ID_Data_Quality",
        SOURCE_RECORD_URL_COLUMN,
        "Source_State",
        "Filter_Rule",
        "Scope_Type",
        "Scope_Value",
        "Data_As_Of_UTC",
        "Chart_ID",
        "Chart_Series",
        "Chart_Category",
        "Contribution_Value",
    ]
    rows: List[Dict[str, Any]] = []
    linked_keys: set[str] = set()
    lineage = facts.get("metric_lineage")
    if not isinstance(lineage, pd.DataFrame):
        lineage = pd.DataFrame()
    lineage_by_key = {
        str(row.get("Metric_Key") or ""): row
        for _, row in lineage.iterrows()
        if str(row.get("Metric_Key") or "").strip()
    }

    def add_rows(
        evidence_key: str,
        *,
        evidence_type: str,
        label: str,
        sheet_name: str,
        positions: Optional[Sequence[int]] = None,
        role: str = "supporting_record",
        source_state: str = "available",
        filter_rule: str = "selected canonical scope",
        metric_value: Any = None,
        unit: str = "records",
        chart_id: str = "",
        chart_series: str = "",
        chart_category: str = "",
        cross_family_parity: str = "",
        preserve_incomplete_claim: bool = False,
    ) -> None:
        frame = sheets.get(sheet_name, pd.DataFrame())
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame()
        normalized_state = str(source_state or "unknown").casefold()
        source_is_complete = normalized_state in {"available", "zero"}
        if not source_is_complete and not preserve_incomplete_claim:
            metric_value = None
        exported_frame = _prepare_export_frame(frame, sheet_name)
        selected_positions = list(range(len(frame))) if positions is None else [int(item) for item in positions]
        selected_positions = [item for item in selected_positions if 0 <= item < len(frame)]
        linked_keys.add(evidence_key)
        if not selected_positions:
            rows.append(
                {
                    "Evidence_Key": evidence_key,
                    "Evidence_Type": evidence_type,
                    "Display_Label": label,
                    "Metric_Value": metric_value,
                    "Unit": unit,
                    "Evidence_Role": (
                        "unavailable_state"
                        if normalized_state in {"failed", "unavailable"}
                        else "zero_state"
                        if normalized_state == "zero" or metric_value == 0
                        else "derivation_state"
                    ),
                    _CROSS_FAMILY_PARITY_COLUMN: cross_family_parity,
                    "Source_Sheet": sheet_name,
                    "Source_Row_Number": None,
                    "Source_Row_SHA256": "",
                    "Record_ID": "",
                    "Record_ID_Data_Quality": "No source row; see Source_State and Filter_Rule",
                    SOURCE_RECORD_URL_COLUMN: "",
                    "Source_State": normalized_state or "unknown",
                    "Filter_Rule": filter_rule,
                    "Scope_Type": facts["scope_type"],
                    "Scope_Value": facts["scope_value"],
                    "Data_As_Of_UTC": facts["as_of_utc"],
                    "Chart_ID": chart_id,
                    "Chart_Series": chart_series,
                    "Chart_Category": chart_category,
                    "Contribution_Value": (
                        0
                        if normalized_state not in {"failed", "unavailable"}
                        and (normalized_state == "zero" or metric_value == 0)
                        else None
                    ),
                }
            )
            return
        for position in selected_positions:
            record = frame.iloc[position].to_dict()
            exported_record = exported_frame.iloc[position].to_dict()
            record_id = _clean_token(record.get("Record_ID"))
            # Supported CSConsole evidence must retain the native stable ID.
            # Falling back to a Metric_Key made a row locally resolvable while
            # silently losing the external record link the manager needs.
            if not record_id and not csconsole_object_for_source(sheet_name):
                record_id = _clean_token(record.get("Metric_Key"))
            quality = _clean_token(record.get("Record_ID_Data_Quality"))
            if not quality:
                quality = (
                    "OK"
                    if record_id
                    else "Stable source ID missing; external record link unavailable"
                )
            source_record_url = _clean_token(exported_record.get(SOURCE_RECORD_URL_COLUMN))
            rows.append(
                {
                    "Evidence_Key": evidence_key,
                    "Evidence_Type": evidence_type,
                    "Display_Label": label,
                    "Metric_Value": metric_value,
                    "Unit": unit,
                    "Evidence_Role": role,
                    _CROSS_FAMILY_PARITY_COLUMN: cross_family_parity,
                    "Source_Sheet": sheet_name,
                    "Source_Row_Number": position + 2,
                    "Source_Row_SHA256": _evidence_row_fingerprint(exported_record),
                    "Record_ID": record_id,
                    "Record_ID_Data_Quality": quality,
                    SOURCE_RECORD_URL_COLUMN: source_record_url,
                    "Source_State": normalized_state,
                    "Filter_Rule": filter_rule,
                    "Scope_Type": facts["scope_type"],
                    "Scope_Value": facts["scope_value"],
                    "Data_As_Of_UTC": facts["as_of_utc"],
                    "Chart_ID": chart_id,
                    "Chart_Series": chart_series,
                    "Chart_Category": chart_category,
                    "Contribution_Value": 1 if source_is_complete else None,
                }
            )

    def positions_where(sheet_name: str, predicate: Any) -> List[int]:
        frame = sheets.get(sheet_name, pd.DataFrame())
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return []
        selected: List[int] = []
        for position, (_, record) in enumerate(frame.iterrows()):
            try:
                if bool(predicate(record)):
                    selected.append(position)
            except Exception:  # noqa: BLE001
                continue
        return selected

    ap_bucket_contract = {
        "kpi.action_plans_total": {"Overdue", "Due Soon", "Open", "Completed", "Blocked / On Hold", "Unknown"},
        "kpi.action_plans_open": {"Overdue", "Due Soon", "Open"},
        "kpi.action_plans_overdue": {"Overdue"},
        "kpi.action_plans_due_soon": {"Due Soon"},
        "kpi.action_plans_completed": {"Completed"},
        "kpi.action_plans_blocked": {"Blocked / On Hold"},
        "kpi.action_plans_unknown": {"Unknown"},
    }
    direct_contracts: Dict[str, Tuple[str, Any, str]] = {
        "kpi.team_members": (
            "Member_Summary",
            lambda row: _clean_token(row.get("Team_Member")).casefold() != "unassigned / portfolio",
            "one canonical attributed member summary row per member; unassigned portfolio rows excluded",
        ),
        "kpi.customers": ("Account_Summary", lambda _row: True, "one canonical account summary row per customer"),
        "kpi.subscriptions": (
            "Subscriptions",
            lambda row: not _clean_token(row.get("Legacy_Record_Type")),
            "distinct canonical subscription records; adapter-preserved family fact rows excluded",
        ),
        "kpi.adoption_barriers": (
            "Adoption_Barriers",
            lambda _row: True,
            "distinct canonical Adoption Barrier records",
        ),
        "kpi.customer_pulse": ("Customer_Pulse", lambda _row: True, "distinct canonical Customer Pulse records"),
        "kpi.tac_cases": ("TAC_Cases", lambda _row: True, "collapsed canonical TAC case records"),
        "kpi.bems": ("BEMS", lambda _row: True, "TAC records classified as BEMS"),
        "kpi.success_priorities": (
            "Success_Priorities",
            lambda _row: True,
            "distinct canonical Success Priority records",
        ),
        "kpi.external_incidents": (
            "External_Incidents",
            lambda _row: True,
            "distinct selected-scope external incident records",
        ),
        "kpi.external_bugs": ("External_Bugs", lambda _row: True, "distinct selected-scope external bug records"),
        "kpi.high_risk_customers": (
            "Account_Summary",
            lambda row: _clean_token(row.get("Risk_Band")).upper() in {"CRITICAL", "HIGH"},
            "canonical customer risk band is CRITICAL or HIGH",
        ),
    }
    for metric_key, (sheet_name, predicate, filter_rule) in direct_contracts.items():
        meta = lineage_by_key.get(metric_key, {})
        metric_source_state = str(meta.get("Source_State") or "unknown")
        disclose_retained_customer_count = (
            metric_key == "kpi.customers"
            and _retained_customer_count_is_disclosable(
                meta.get("Metric_Value"),
                metric_source_state,
            )
        )
        add_rows(
            metric_key,
            evidence_type="metric",
            label=str(meta.get("Display_Label") or metric_key),
            sheet_name=sheet_name,
            positions=positions_where(sheet_name, predicate),
            source_state=metric_source_state,
            filter_rule=filter_rule,
            metric_value=meta.get("Metric_Value"),
            unit=str(meta.get("Unit") or "records"),
            preserve_incomplete_claim=disclose_retained_customer_count,
        )
    for metric_key, allowed_buckets in ap_bucket_contract.items():
        meta = lineage_by_key.get(metric_key, {})
        add_rows(
            metric_key,
            evidence_type="metric",
            label=str(meta.get("Display_Label") or metric_key),
            sheet_name="Action_Plans",
            positions=positions_where(
                "Action_Plans",
                lambda row, buckets=allowed_buckets: str(row.get("AdoptIQ_Status_Bucket") or "").strip() in buckets,
            ),
            source_state=str(meta.get("Source_State") or "unknown"),
            filter_rule="canonical Action Plan lifecycle bucket in " + ", ".join(sorted(allowed_buckets)),
            metric_value=meta.get("Metric_Value"),
            unit=str(meta.get("Unit") or "records"),
        )

    for metric_key, meta in lineage_by_key.items():
        if metric_key.startswith("legacy.family."):
            sheet_name = _clean_token(meta.get("Source_Sheet")) or "Subscriptions"
            add_rows(
                metric_key,
                evidence_type="reported_fact",
                label=str(meta.get("Display_Label") or metric_key),
                sheet_name=sheet_name,
                positions=positions_where(
                    sheet_name,
                    lambda row, wanted=metric_key: _clean_token(row.get("Metric_Key")) == wanted,
                ),
                role="legacy_family_reported_fact",
                source_state=str(meta.get("Source_State") or "available"),
                filter_rule=str(meta.get("Filters") or "exact preserved family-specific workbook fact"),
                metric_value=meta.get("Metric_Value"),
                unit=str(meta.get("Unit") or "reported value"),
            )
        elif metric_key.startswith("summary.member."):
            summary_key = metric_key.rsplit(".", 1)[0]
            add_rows(
                metric_key,
                evidence_type="summary",
                label=str(meta.get("Display_Label") or metric_key),
                sheet_name="Member_Summary",
                positions=positions_where(
                    "Member_Summary",
                    lambda row, wanted=summary_key: _clean_token(row.get("Metric_Key")) == wanted,
                ),
                role="derived_summary_row",
                source_state=str(meta.get("Source_State") or "unknown"),
                filter_rule=str(meta.get("Filters") or "canonical member summary"),
                metric_value=meta.get("Metric_Value"),
                unit=str(meta.get("Unit") or "records"),
                cross_family_parity=str(
                    meta.get(_CROSS_FAMILY_PARITY_COLUMN) or ""
                ),
            )
        elif metric_key.startswith("summary.account."):
            summary_key = metric_key.rsplit(".", 1)[0]
            add_rows(
                metric_key,
                evidence_type="summary",
                label=str(meta.get("Display_Label") or metric_key),
                sheet_name="Account_Summary",
                positions=positions_where(
                    "Account_Summary",
                    lambda row, wanted=summary_key: _clean_token(row.get("Metric_Key")) == wanted,
                ),
                role="derived_summary_row",
                source_state=str(meta.get("Source_State") or "unknown"),
                filter_rule=str(meta.get("Filters") or "canonical account summary"),
                metric_value=meta.get("Metric_Value"),
                unit=str(meta.get("Unit") or "records"),
                cross_family_parity=str(
                    meta.get(_CROSS_FAMILY_PARITY_COLUMN) or ""
                ),
            )

    trend_coverage = facts.get("activity_trend", {}).get("coverage")
    if not isinstance(trend_coverage, pd.DataFrame):
        trend_coverage = pd.DataFrame()
    for _, chart in facts["chart_data"].iterrows():
        metric_key = str(chart.get("Metric_Key") or "")
        chart_id = str(chart.get("Chart_ID") or "")
        sheet_name = str(chart.get("Source_Sheet") or "")
        category = str(chart.get("Category") or "")
        public_chart_category = (
            _clean_token(chart.get("Display_Category")) or category
        )
        positions: List[int] = []
        filter_rule = str(chart.get("Grouping") or "canonical chart grouping")
        if chart_id == "activity_mix":
            positions = list(range(len(sheets.get(sheet_name, pd.DataFrame()))))
        elif chart_id == "action_plan_status_aging":
            age_series = str(chart.get("Series") or "") == cm.ACTION_PLAN_AGE_SERIES
            bucket_column = "AdoptIQ_Age_Band" if age_series else "AdoptIQ_Status_Bucket"
            positions = positions_where(
                "Action_Plans",
                lambda row, wanted=category, column=bucket_column: str(row.get(column) or "").strip() == wanted,
            )
            sheet_name = "Action_Plans"
        elif chart_id == "risk_distribution":
            positions = positions_where(
                "Account_Summary",
                lambda row, wanted=category: _clean_token(row.get("Risk_Band")).upper() == wanted.upper(),
            )
            sheet_name = "Account_Summary"
        elif chart_id == "activity_trend":
            coverage_match = trend_coverage.loc[
                trend_coverage.get("Source", pd.Series(dtype="object")).fillna("").astype(str)
                == str(chart.get("Series") or "")
            ]
            date_field = str(coverage_match.iloc[0].get("Date_Field") or "") if not coverage_match.empty else ""
            frame = sheets.get(sheet_name, pd.DataFrame())
            if isinstance(frame, pd.DataFrame) and date_field in frame.columns:
                source_timestamps = cm._r158_parse_dates_utc(frame[date_field])
                dates = source_timestamps.dt.normalize()
                try:
                    periods = (
                        dates.dt.tz_localize(None)
                        .dt.to_period(str(facts.get("activity_trend", {}).get("frequency") or "W-SUN"))
                        .dt.start_time
                    )
                except Exception:  # noqa: BLE001
                    periods = dates.dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time
                period_start = pd.to_datetime(periods, errors="coerce", utc=True)
                wanted_period = pd.to_datetime(chart.get("Period_Start"), errors="coerce", utc=True)
                # Round 148: the canonical trend groups only records inside
                # the requested analysis window.  A first or final partial
                # calendar week must link that same subset, not every source
                # row whose date falls in the surrounding weekly period.
                window_start = pd.to_datetime(
                    facts.get("activity_trend", {}).get("window_start_utc"),
                    errors="coerce",
                    utc=True,
                )
                window_end = pd.to_datetime(
                    facts.get("activity_trend", {}).get("as_of_utc"),
                    errors="coerce",
                    utc=True,
                )
                in_window = (
                    source_timestamps.between(
                        window_start,
                        window_end,
                        inclusive="both",
                    )
                    if pd.notna(window_start) and pd.notna(window_end)
                    else source_timestamps.notna()
                )
                positions = [
                    position
                    for position, value in enumerate(period_start)
                    if (
                        pd.notna(value)
                        and pd.notna(wanted_period)
                        and value == wanted_period
                        and bool(in_window.iloc[position])
                    )
                ]
                filter_rule = f"{date_field} falls in canonical weekly period {wanted_period.date()}"
        add_rows(
            metric_key,
            evidence_type="chart_point",
            label=str(chart.get("Display_Label") or metric_key),
            sheet_name=sheet_name,
            positions=positions,
            source_state=str(chart.get("Source_State") or "unknown"),
            filter_rule=filter_rule,
            metric_value=chart.get("Value"),
            unit=str(chart.get("Unit") or "records"),
            chart_id=chart_id,
            chart_series=str(chart.get("Series") or ""),
            chart_category=public_chart_category,
        )

    action_frame = sheets.get("Action_Plans", pd.DataFrame())
    if isinstance(action_frame, pd.DataFrame):
        for position, (_, record) in enumerate(action_frame.iterrows()):
            source_id = _clean_token(record.get("Record_ID")) or f"row-{position + 2}"
            add_rows(
                evidence_entity_key("action_plan", source_id),
                evidence_type="action_plan",
                label=_clean_token(record.get("AdoptIQ_Title")) or "Action Plan",
                sheet_name="Action_Plans",
                positions=[position],
                role="action_plan_record",
                source_state=str(facts.get("action_plan_lifecycle", {}).get("source_state") or "unknown"),
                filter_rule="exact selected Action Plan record",
            )
    account_frame = sheets.get("Account_Summary", pd.DataFrame())
    if isinstance(account_frame, pd.DataFrame):
        for position, (_, record) in enumerate(account_frame.iterrows()):
            customer = _clean_token(record.get("Account")) or f"row-{position + 2}"
            add_rows(
                evidence_entity_key("account", customer),
                evidence_type="account",
                label=customer,
                sheet_name="Account_Summary",
                positions=[position],
                role="derived_account_summary_row",
                source_state=str(facts.get("risk_summary", {}).get("source_state") or "unknown"),
                filter_rule="exact canonical account summary row",
            )
            # The Word decision table renders the deterministic next action
            # from this account's risk profile.  Link that recommendation to
            # both its visible account summary and every component row used by
            # the risk engine, so a recommendation can never exist as an
            # untraceable narrative-only claim.
            recommendation_key = evidence_entity_key("recommendation_account", customer)
            add_rows(
                recommendation_key,
                evidence_type="recommendation",
                label=f"Risk recommendation for {customer}",
                sheet_name="Account_Summary",
                positions=[position],
                role="recommendation_account_summary",
                source_state=str(facts.get("risk_summary", {}).get("source_state") or "unknown"),
                filter_rule="exact canonical account summary supporting the risk recommendation",
            )
            add_rows(
                recommendation_key,
                evidence_type="recommendation",
                label=f"Risk recommendation for {customer}",
                sheet_name="Risk_Components",
                positions=positions_where(
                    "Risk_Components",
                    lambda row, wanted=customer: (
                        _customer_match_key(row.get("Customer")) == _customer_match_key(wanted)
                    ),
                ),
                role="recommendation_risk_component",
                source_state=str(facts.get("risk_summary", {}).get("source_state") or "unknown"),
                filter_rule="all deterministic risk-component rows for the recommended account",
            )
    for top_row in facts.get("top_action_plans") or []:
        source_id = _clean_token(top_row[0] if top_row else "")
        if not source_id:
            continue
        add_rows(
            evidence_entity_key("recommendation", source_id),
            evidence_type="recommendation",
            label=f"Prioritized Action Plan {source_id}",
            sheet_name="Action_Plans",
            positions=positions_where(
                "Action_Plans",
                lambda row, wanted=source_id: _clean_token(row.get("Record_ID")) == wanted,
            ),
            role="recommended_action_record",
            source_state=str(facts.get("action_plan_lifecycle", {}).get("source_state") or "unknown"),
            filter_rule=str(facts.get("ranking_criteria") or "canonical top-N ranking"),
        )

    for signal in facts.get("decision_signals") or []:
        try:
            position = signal.get("source_position")
            positions = [] if position is None else [int(position)]
        except (TypeError, ValueError):
            positions = []
        add_rows(
            str(signal.get("evidence_key") or ""),
            evidence_type="decision_signal",
            label=f"{signal.get('source_sheet')}: {signal.get('signal')}",
            sheet_name=str(signal.get("source_sheet") or ""),
            positions=positions,
            role="prioritized_cross_source_signal",
            source_state=str(signal.get("source_state") or "unknown"),
            filter_rule=(
                "criteria-scoped source; deterministic status, severity/priority, date, and stable-ID ranking"
            ),
        )

    correlation_coverage = (facts.get("defect_correlation_bundle") or {}).get("coverage") or {}
    correlation_source_coverage = correlation_coverage.get("sources") or {}
    for record in (facts.get("defect_correlation_bundle") or {}).get("records") or []:
        metric_key = evidence_entity_key(
            "defect_correlation",
            f"{_clean_token(record.get('identity_key'))}|{_clean_token(record.get('csc_id'))}",
        )
        for parent in record.get("parent_records") or []:
            sheet_name = _clean_token(parent.get("source_sheet"))
            try:
                position = max(int(parent.get("source_position") or 1) - 1, 0)
            except (TypeError, ValueError):
                position = 0
            parent_state = (
                correlation_source_coverage.get(sheet_name, {}).get("state")
                if isinstance(correlation_source_coverage, Mapping)
                else "unknown"
            )
            add_rows(
                metric_key,
                evidence_type="defect_correlation",
                label=f"{record.get('csc_id')} parent evidence",
                sheet_name=sheet_name,
                positions=[position],
                role="exact_csc_parent_record",
                source_state=str(parent_state or "unknown"),
                filter_rule=(
                    f"exact normalized {record.get('csc_id')} reference in fields "
                    + ", ".join(parent.get("matched_fields") or [])
                ),
                metric_value=record.get("csc_id"),
                unit="exact correlation",
            )
        if record.get("verified_external_match"):
            external_state = (
                correlation_source_coverage.get("External_Bugs", {}).get("state")
                if isinstance(correlation_source_coverage, Mapping)
                else "unknown"
            )
            add_rows(
                metric_key,
                evidence_type="defect_correlation",
                label=f"{record.get('csc_id')} verified external metadata",
                sheet_name="External_Bugs",
                positions=[
                    max(int(position) - 1, 0) for position in record.get("verified_external_source_positions") or []
                ],
                role="exact_csc_external_bug_record",
                source_state=str(external_state or "unknown"),
                filter_rule="exact normalized CSC bug ID equality",
                metric_value=record.get("csc_id"),
                unit="exact correlation",
            )

    decision_insights = facts.get("decision_insights") or {}
    for insight_name in _DECISION_INSIGHT_ORDER:
        insight = decision_insights.get(insight_name) if isinstance(decision_insights, Mapping) else None
        if not isinstance(insight, Mapping):
            continue
        evidence_key = _clean_token(insight.get("metric_key"))
        source_positions = insight.get("source_positions") or {}
        source_states = insight.get("source_states") or {}
        evidence_filters = insight.get("evidence_filters") or {}
        if not evidence_key:
            continue
        for sheet_name in insight.get("source_sheets") or []:
            sheet_name = _clean_token(sheet_name)
            if not sheet_name:
                continue
            raw_positions = source_positions.get(sheet_name, []) if isinstance(source_positions, Mapping) else []
            positions = [int(value) for value in raw_positions] if isinstance(raw_positions, (list, tuple)) else []
            role = "derived_insight_source_record"
            filter_rule = (
                _clean_token(evidence_filters.get(sheet_name))
                if isinstance(evidence_filters, Mapping)
                else _clean_token(insight.get("filters"))
            )
            if sheet_name == "Report_Info":
                receipt_ids = {
                    _clean_token(claim.get("receipt_id"))
                    for claim in (insight.get("corpus_claims") or [])
                    if isinstance(claim, Mapping) and _clean_token(claim.get("receipt_id"))
                }
                positions = positions_where(
                    "Report_Info",
                    lambda row, wanted=receipt_ids: _clean_token(row.get("Item")) in wanted,
                )
                if len(positions) != len(receipt_ids) or not positions:
                    raise ValueError(f"canonical decision insight {insight_name} lacks an exact corpus receipt row")
                role = "corpus_retriever_receipt"
                filter_rule = (
                    "exact content-addressed corpus_retriever receipt; aggregate claim only, no raw corpus row"
                )
            add_rows(
                evidence_key,
                evidence_type="insight",
                label=_clean_token(insight.get("display_label")) or insight_name,
                sheet_name=sheet_name,
                positions=positions,
                role=role,
                source_state=(
                    _clean_token(source_states.get(sheet_name))
                    if isinstance(source_states, Mapping)
                    else _clean_token(insight.get("source_state"))
                ),
                filter_rule=filter_rule,
                metric_value=insight.get("paragraph_text"),
                unit="frozen claim",
            )

    missing_lineage = sorted(set(lineage_by_key) - linked_keys)
    if missing_lineage:
        raise ValueError(
            "Evidence_Links lacks an exact mapping for visible lineage keys: " + ", ".join(missing_lineage[:20])
        )
    return pd.DataFrame(rows, columns=columns)


def build_source_data_sheets(
    facts: Mapping[str, Any],
    *,
    additional_sheets: Optional[Mapping[str, pd.DataFrame]] = None,
    _skip_contract_fingerprint: bool = False,
) -> "OrderedDict[str, pd.DataFrame]":
    """Return the always-present canonical companion workbook sheets."""

    source_digests = {} if _skip_contract_fingerprint else _source_contract_digests(facts)
    contract_fingerprint = (
        "" if _skip_contract_fingerprint else fact_contract_fingerprint(facts, source_digests=source_digests)
    )

    info_rows: List[Dict[str, Any]] = [
        {"Item": "Report_Type", "Value": facts["report_type"], "Detail": "concise decision report"},
        {"Item": "Manager", "Value": facts["manager_name"], "Detail": "selected manager"},
        {"Item": "Technology", "Value": facts.get("technology") or "All", "Detail": "selected technology"},
        {"Item": "Scope_Type", "Value": facts["scope_type"], "Detail": "team, member, or customer"},
        {"Item": "Scope_Value", "Value": facts["scope_value"], "Detail": "validated selected scope"},
        {"Item": "Days", "Value": facts["days"], "Detail": "analysis window"},
        {
            "Item": "Data_As_Of_UTC",
            "Value": facts["as_of_utc"],
            "Detail": "source retrieval clock only; blank when unavailable",
        },
        {
            "Item": "Data_As_Of_State",
            "Value": facts.get("data_as_of_state") or "unknown",
            "Detail": facts.get("data_as_of_detail") or "",
        },
        {
            "Item": "Retrieval_Attempted_At_UTC",
            "Value": facts.get("retrieval_attempted_at_utc") or "",
            "Detail": "retrieval-attempt clock; not a source data-as-of claim",
        },
        {
            "Item": "Evaluation_As_Of_UTC",
            "Value": facts.get("evaluation_as_of_utc") or "",
            "Detail": ("deterministic aging clock only; not a source-freshness claim"),
        },
        {
            "Item": "Data_Mode",
            "Value": facts.get("data_mode") or "Application source path",
            "Detail": (
                "guarded offline fixture versus application source execution; "
                "this field does not itself attest live-source accuracy"
            ),
        },
        {
            "Item": "Live_Source_Validation",
            "Value": (
                "Yes"
                if facts.get("live_validation_performed") is True
                else "No"
                if facts.get("live_validation_performed") is False
                else "Not independently attested"
            ),
            "Detail": (
                "No for guarded offline fixtures; live Cisco validation must be "
                "explicitly attested by the authorized runtime"
            ),
        },
        {
            "Item": "Fact_Contract_SHA256",
            "Value": contract_fingerprint,
            "Detail": "semantic Word/Source Data/chart fact fingerprint",
        },
        {
            "Item": "Due_Soon_Days",
            "Value": facts["action_plan_lifecycle"]["due_soon_days"],
            "Detail": "inclusive horizon",
        },
        {
            "Item": "Action_Plan_Age_Bands",
            "Value": "; ".join(cm.ACTION_PLAN_AGE_BAND_ORDER),
            "Detail": (
                "unresolved plans only; completed plans excluded; age uses selected "
                "created/open date and explicit evaluation as-of clock"
            ),
        },
        {
            "Item": "Activity_Total_State",
            "Value": facts["activity_mix"]["total_state"],
            "Detail": facts["activity_mix"]["definition"],
        },
        {
            "Item": "TAC_Case_Type_Classified",
            "Value": (facts.get("tac_case_type_coverage") or {}).get("classified", 0),
            "Detail": "Rows classified as break/fix or provisioning from available source evidence",
        },
        {
            "Item": "TAC_Case_Type_Not_Derivable",
            "Value": (facts.get("tac_case_type_coverage") or {}).get("not_derivable", 0),
            "Detail": (
                "Rows whose available case type, title, and problem evidence did not support "
                "break/fix or provisioning classification"
            ),
        },
        {
            "Item": "TAC_Case_Type_Coverage_Pct",
            "Value": (facts.get("tac_case_type_coverage") or {}).get("coverage_percent"),
            "Detail": "Classified TAC rows divided by selected-scope TAC rows",
        },
        {
            "Item": "Partial_Data_Warning_Count",
            "Value": len(facts["partial_data_warnings"]),
            "Detail": "see following rows",
        },
    ]
    # Round 172: publish content-addressed corpus receipts in an existing
    # canonical sheet only when a corpus sentence is actually present.  This
    # keeps no-corpus workbooks byte-stable while giving each published claim
    # an exact, fingerprintable Evidence_Links target.
    seen_corpus_receipts: set[str] = set()
    decision_insights = facts.get("decision_insights") or {}
    if isinstance(decision_insights, Mapping):
        for insight_name in _DECISION_INSIGHT_ORDER:
            insight = decision_insights.get(insight_name)
            if not isinstance(insight, Mapping):
                continue
            for claim in insight.get("corpus_claims") or []:
                if not isinstance(claim, Mapping):
                    raise ValueError(f"canonical decision insight {insight_name} has an invalid corpus receipt")
                receipt_id = _clean_token(claim.get("receipt_id"))
                receipt_sha256 = _clean_token(claim.get("receipt_sha256"))
                receipt_payload = claim.get("receipt_payload")
                sentence = _clean_token(claim.get("sentence"))
                if not receipt_id or not re.fullmatch(r"Corpus_Retriever_Receipt:[0-9a-f]{16}", receipt_id):
                    raise ValueError(f"canonical decision insight {insight_name} has an invalid corpus receipt ID")
                if receipt_id in seen_corpus_receipts:
                    raise ValueError(f"canonical decision insight {insight_name} repeats corpus receipt {receipt_id}")
                if not isinstance(receipt_payload, Mapping):
                    raise ValueError(f"canonical decision insight {insight_name} lacks a corpus receipt payload")
                payload_claim = receipt_payload.get("claim")
                if not isinstance(payload_claim, Mapping):
                    raise ValueError(f"canonical decision insight {insight_name} lacks a corpus receipt claim")
                serialized_receipt = json.dumps(
                    _json_safe(receipt_payload),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                actual_sha256 = hashlib.sha256(serialized_receipt.encode("utf-8")).hexdigest()
                payload_sentence = _clean_token(payload_claim.get("sentence"))
                if (
                    receipt_sha256 != actual_sha256
                    or receipt_id != f"Corpus_Retriever_Receipt:{actual_sha256[:16]}"
                    or not sentence
                    or payload_sentence != sentence
                    or sentence not in _clean_token(insight.get("paragraph_text"))
                ):
                    raise ValueError(f"canonical decision insight {insight_name} has a mismatched corpus receipt")
                seen_corpus_receipts.add(receipt_id)
                info_rows.append(
                    {
                        "Item": receipt_id,
                        "Value": receipt_sha256,
                        "Detail": serialized_receipt,
                    }
                )

    # Receipt rows intentionally precede variable digest/state rows. Their
    # Excel row positions therefore stay identical while the canonical digest
    # pass builds with ``_skip_contract_fingerprint=True``.
    for sheet_name, digest in source_digests.items():
        info_rows.append(
            {
                "Item": f"Sheet_SHA256:{sheet_name}",
                "Value": digest,
                "Detail": "deterministic digest of all public rows and columns",
            }
        )
    for _, row in facts["source_coverage"].iterrows():
        info_rows.append(
            {
                "Item": f"Source_State:{row['Source_Sheet']}",
                "Value": row["Source_State"],
                "Detail": row["Detail"],
            }
        )
    for index, warning in enumerate(facts["partial_data_warnings"], 1):
        info_rows.append(
            {
                "Item": f"Partial_Data_Warning_{index}",
                "Value": str(warning.get("dataset") or "unknown"),
                "Detail": str(warning.get("error") or warning.get("effect") or warning.get("kind") or "")[:500],
            }
        )

    chart_data = facts["chart_data"].copy()
    # Internal ``Category`` retains canonical buckets (including ``Unknown``)
    # so partitions and evidence locators remain exact.  The exported workbook
    # should never make a manager decode that sentinel; publish the explicit
    # display reason while Metric_Key preserves the stable canonical identity.
    if "Display_Category" in chart_data.columns:
        public_category = chart_data["Display_Category"].fillna("").astype(str).str.strip()
        chart_data["Category"] = chart_data["Category"].where(
            public_category.eq(""),
            public_category,
        )
        chart_data = chart_data.drop(columns=["Display_Category"])
    trend_coverage = facts["activity_trend"]["coverage"].copy()
    if not trend_coverage.empty:
        trend_coverage.insert(0, "Chart_ID", "activity_trend_coverage")
        trend_coverage.insert(1, "Metric_Key", "")
        trend_coverage.insert(2, "Display_Label", "Activity trend coverage")
        trend_coverage.insert(3, "Series", "Coverage")
        trend_coverage.insert(4, "Category", trend_coverage["Source"])
        trend_coverage.insert(5, "Period_Start", pd.NaT)
        trend_coverage["Value"] = trend_coverage["Dateable_Records"]
        trend_coverage["Unit"] = "records"
        trend_coverage["Source_Sheet"] = trend_coverage["Source"].str.replace(" ", "_", regex=False)
        trend_coverage["Canonical_Function"] = "canonical_metrics.build_activity_trend"
        chart_data = pd.concat([chart_data, trend_coverage], ignore_index=True, sort=False)

    frames = facts["frames"]
    sheets: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
    sheets["Report_Info"] = pd.DataFrame(info_rows)
    sheets["Metric_Lineage"] = facts["metric_lineage"].copy()
    sheets["Chart_Data"] = chart_data
    sheets["Action_Plans"] = frames["action_plans"].copy()
    sheets["Adoption_Barriers"] = frames["adoption_barriers"].copy()
    sheets["Customer_Pulse"] = frames["customer_pulse"].copy()
    sheets["TAC_Cases"] = frames["tac_cases"].copy()
    sheets["BEMS"] = facts["bems"].copy()
    sheets["Subscriptions"] = frames["subscriptions"].copy()
    sheets["Success_Priorities"] = frames["success_priorities"].copy()
    sheets["External_Incidents"] = facts["external_incidents"].copy()
    sheets["External_Bugs"] = facts["external_bugs"].copy()
    sheets["Defect_Correlations"] = facts["defect_correlations"].copy()
    risk_rows: List[Dict[str, Any]] = []
    for customer, profile in sorted(facts["risk_profiles"].items()):
        identity = _identity_from_profile(customer, profile)
        components = profile.get("components") or {}
        if not components:
            components = {"portfolio_risk": {"score": profile.get("risk_score_0_100"), "details": {}}}
        for component_name, component in sorted(components.items()):
            component = component or {}
            risk_rows.append(
                {
                    "Metric_Key": f"risk.{_customer_match_key(customer) or 'unknown'}.{component_name}",
                    "Customer": customer,
                    "Customer_Identity": identity["identity_key"],
                    "Account_IDs": "; ".join(identity.get("account_ids") or ()),
                    "Identity_Basis": (
                        "stable account/customer ID"
                        if identity.get("account_ids")
                        else "exact normalized customer label"
                    ),
                    "Risk_Score_0_100": profile.get("risk_score_0_100"),
                    "Risk_Band": profile.get("risk_band"),
                    "Component": component_name,
                    "Component_Score_0_100": component.get("score"),
                    "Component_Details_JSON": json.dumps(
                        component.get("details") or {},
                        sort_keys=True,
                        default=str,
                    ),
                    "Scope_Type": facts["scope_type"],
                    "Scope_Value": facts["scope_value"],
                    "Source_System": "AdoptIQ deterministic risk engine",
                }
            )
    sheets["Risk_Components"] = pd.DataFrame(risk_rows)
    sheets["Member_Summary"] = pd.DataFrame(
        facts.get("member_summary_all") or [],
        columns=[
            "Team_Member",
            "Customers",
            "Open_AP",
            "Overdue_AP",
            "Barriers",
            "TAC_Cases",
            "Customers_Source_State",
            "Open_AP_Source_State",
            "Overdue_AP_Source_State",
            "Barriers_Source_State",
            "TAC_Cases_Source_State",
        ],
    )
    if not sheets["Member_Summary"].empty:
        sheets["Member_Summary"].insert(
            0,
            "Metric_Key",
            [f"summary.{evidence_entity_key('member', value)}" for value in sheets["Member_Summary"]["Team_Member"]],
        )
        duplicate_member_keys = sorted(
            set(
                sheets["Member_Summary"]
                .loc[
                    sheets["Member_Summary"]["Metric_Key"].duplicated(keep=False),
                    "Metric_Key",
                ]
                .astype(str)
            )
        )
        if duplicate_member_keys:
            raise ValueError(
                "Member_Summary contains duplicate evidence identities: " + ", ".join(duplicate_member_keys[:20])
            )
        sheets["Member_Summary"]["Scope_Type"] = facts["scope_type"]
        sheets["Member_Summary"]["Scope_Value"] = facts["scope_value"]
        sheets["Member_Summary"]["Source_System"] = "AdoptIQ canonical metrics"
    sheets["Account_Summary"] = pd.DataFrame(
        facts.get("account_summary_all") or [],
        columns=[
            "Account",
            "Risk_Band",
            "Risk_Score_0_100",
            "Open_AP",
            "Overdue_AP",
            "Critical_High_Barriers",
            "TAC_Cases",
            "Risk_Band_Source_State",
            "Risk_Score_0_100_Source_State",
            "Open_AP_Source_State",
            "Overdue_AP_Source_State",
            "Critical_High_Barriers_Source_State",
            "TAC_Cases_Source_State",
        ],
    )
    if not sheets["Account_Summary"].empty:
        sheets["Account_Summary"].insert(
            0,
            "Metric_Key",
            [
                f"summary.account.{_customer_match_key(value).replace(' ', '_') or 'unknown'}"
                for value in sheets["Account_Summary"]["Account"]
            ],
        )
        sheets["Account_Summary"]["Scope_Type"] = facts["scope_type"]
        sheets["Account_Summary"]["Scope_Value"] = facts["scope_value"]
        sheets["Account_Summary"]["Source_System"] = "AdoptIQ canonical metrics and risk engine"
    sheets["Evidence_Links"] = _build_evidence_links(facts, sheets)
    if additional_sheets:
        raise ValueError(
            "Additional Source Data sheets are not allowed outside the canonical content-digested contract"
        )
    return OrderedDict((name, sheets[name]) for name in SOURCE_DATA_SHEET_NAMES)


def source_data_path_for_word(word_path: Any) -> Path:
    """Return ``AdoptIQ_Source_Data_*`` path paired to a Word report."""

    path = Path(word_path)
    name = path.stem
    if name.startswith("AdoptIQ_Report_"):
        name = "AdoptIQ_Source_Data_" + name[len("AdoptIQ_Report_") :]
    elif not name.startswith("AdoptIQ_Source_Data_"):
        name = "AdoptIQ_Source_Data_" + name
    return path.with_name(name).with_suffix(".xlsx")


_PUBLIC_CONTEXT_WITH_LINK = tuple(_PUBLIC_CONTEXT_COLUMN_ORDER)
_PUBLIC_CONTEXT_WITHOUT_LINK = tuple(
    column for column in _PUBLIC_CONTEXT_COLUMN_ORDER if column != SOURCE_RECORD_URL_COLUMN
)

# A canonical Source Data workbook is a contract, not a dump of whichever
# intermediate DataFrame a report family happened to use.  These fixed public
# projections retain source-native evidence and the shared attribution/link
# contract while preventing route-only aliases and duplicate derived columns
# from changing row fingerprints for the same scoped observations.
_CANONICAL_SOURCE_EXPORT_COLUMNS: Mapping[str, Tuple[str, ...]] = {
    "Adoption_Barriers": (
        *_PUBLIC_CONTEXT_WITH_LINK,
        "ID",
        "NAME",
        "Customer Name",
        "Account ID",
        "Account Manager",
        "Assignee",
        "CSSM Email",
        "Business Unit",
        "Theater",
        "Sales Level 4",
        "Sales Level 5",
        "Subject",
        "title",
        "Description",
        "Barrier Type",
        "Barrier Level",
        "Barrier Category",
        "Barrier Category (Final)",
        "Feature",
        "Product",
        "Product Name",
        "sub_technology",
        "Adoption Barrier Status",
        "Status",
        "Status (Normalized)",
        "Severity",
        "Severity (Normalized)",
        "Priority",
        "Escalated",
        "Hold Reason",
        "Waiting For",
        "Waiting For Detail",
        "Partner Issue",
        "Reason",
        "Closed Reason",
        "Closure Reason",
        "Open Date",
        "Due Date",
        "Original Due Date",
        "Closed Date",
        "Age (Days)",
        "Days in Stage",
        "Hold Days",
        "Annual Order Value",
        "Product ARR",
        "Service ARR",
        "Gross Retention Rate",
        "BU Health Score",
        "Use Case Health Score",
        "Solution Domain Health Score",
        "Action Plan Title",
        "Action",
        "Action Type",
        "Action Sub-Type",
        "Next Action",
        "Next Step",
        "Next Action Owner",
        "Next Action Due Date",
        "TAC Case Number",
        "Linked Cases (Count)",
        "Linked Cases",
        "Linked CTAs (Count)",
        "Linked Activities (Count)",
        "bemscsc_refs",
        "Comments",
        "Current Status / Notes",
        "Closure Comments",
        "Feedback Comments",
    ),
    "Customer_Pulse": (
        *_PUBLIC_CONTEXT_WITH_LINK,
        "ID",
        "PULSE_ID",
        "NAME",
        "Customer Name",
        "Account ID",
        "Customer Pulse",
        "Customer Pulse Color",
        "Pulse Rating",
        "Pulse Score",
        "Pulse Date",
        "As Of Date",
        "Product",
        "Comments",
        "Owner",
        "RECORD_SOURCE",
    ),
    "TAC_Cases": (
        *_PUBLIC_CONTEXT_WITH_LINK,
        "Customer",
        "Customer Name",
        "Account ID",
        "Subscription Reference Id",
        "Subscription ID",
        "Product",
        "Tech.",
        "Severity",
        "Severity (Normalized)",
        "Service Tier",
        "Highest Priority",
        "Priority (Normalized)",
        "SR Number",
        "Case Number",
        "Title",
        "Case Status",
        "Case Status (Normalized)",
        "Is Open",
        "Is Closed",
        "Case Classification",
        "Case Type",
        "Case Type Data Quality",
        "Is BEMS",
        "Transaction ID",
        "bemscsc_refs",
        "Date/Time Opened",
        "open_date",
        "Date/Time Closed",
        "closed_date",
        "Open Age (Days)",
        "Closed Age (Days)",
        "Case Owner",
        "Current Contact Email",
        "Case Origin",
        "Problem Code",
        "Resolution Code",
        "Problem Description",
        "Problem Details",
        "CSE Action Plan",
        "Last Cisco Update",
        "Resolution Summary",
        "Customer Activity",
        "# of Case Owner Changes",
    ),
    "BEMS": (
        *_PUBLIC_CONTEXT_WITH_LINK,
        "Customer",
        "Customer Name",
        "Account ID",
        "Subscription Reference Id",
        "Subscription ID",
        "SR Number",
        "Case Number",
        "Transaction ID",
        "BEMS_ID",
        "BEMS_REF",
        "bemscsc_refs",
        "Is BEMS",
        "Title",
        "Severity",
        "Severity (Normalized)",
        "Highest Priority",
        "Priority (Normalized)",
        "Case Status",
        "Case Status (Normalized)",
        "Is Open",
        "Is Closed",
        "Case Classification",
        "Case Type",
        "Case Type Data Quality",
        "Date/Time Opened",
        "open_date",
        "Date/Time Closed",
        "closed_date",
        "Open Age (Days)",
        "Closed Age (Days)",
        "Case Owner",
        "Current Contact Email",
        "Problem Description",
        "Problem Details",
        "CSE Action Plan",
        "Last Cisco Update",
        "Resolution Summary",
        "Customer Activity",
    ),
    "Subscriptions": (
        *_PUBLIC_CONTEXT_WITHOUT_LINK,
        "Subscription ID",
        "Account ID",
        "Customer Name",
        "PRODUCT_NAME",
        "CSSM Email",
        "TECHNOLOGY_C",
        "SUB_TECHNOLOGY_C",
        "Status",
        "Renewal Date",
        "Start Date",
        "End Date",
        "ARR",
        "Currency",
        # The adapter may append explicitly typed family presentation facts.
        # Keep their closed schema in every family so canonical subscription
        # row hashes do not depend on whether such rows happened to exist.
        "Metric_Key",
        "Legacy_Record_Type",
        "Legacy_Report_Family",
        "Legacy_Source_Sheet",
        "Legacy_Source_Row_Number",
        "Legacy_Source_Field",
        "Legacy_Fact_Label",
        "Legacy_Fact_Value",
    ),
    "Success_Priorities": (
        *_PUBLIC_CONTEXT_WITH_LINK,
        "ID",
        "Account ID",
        "Related Customer",
        "Success Priority Title",
        "Description",
        "Priority Level",
        "Status",
        "Open Date",
        "Closed Date",
        "Owner",
        "Comments",
        "CSSM Email",
    ),
}


def _first_public_value(frame: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    """Return the first substantive value across equivalent public aliases."""

    result = pd.Series([""] * len(frame), index=frame.index, dtype="object")
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column].map(_clean_token)
        take = result.map(_clean_token).eq("") & values.ne("")
        result.loc[take] = frame.loc[take, column]
    return result


def _canonical_public_datetime(value: Any) -> str:
    """Return one type-stable UTC representation for a public date cell."""

    token = _clean_token(value)
    if not token:
        return ""
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return token
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_public_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    token = _clean_token(value).casefold()
    if token in {"true", "1", "yes", "y"}:
        return True
    if token in {"false", "0", "no", "n"}:
        return False
    return _clean_token(value)


def _canonical_public_integer(value: Any) -> Any:
    token = _clean_token(value)
    if not token:
        return ""
    if isinstance(value, bool):
        return token
    try:
        number = Decimal(token)
    except Exception:
        return token
    if not number.is_finite() or number != number.to_integral_value():
        return token
    return int(number)


def _canonical_public_number(value: Any) -> str:
    token = _clean_token(value)
    if not token or isinstance(value, bool):
        return token
    try:
        number = Decimal(token)
    except Exception:
        return token
    if not number.is_finite():
        return token
    return format(number.normalize(), "f")


def _canonical_source_export_frame(frame: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    """Apply the fixed semantic schema shared by all four report routes."""

    columns = _CANONICAL_SOURCE_EXPORT_COLUMNS.get(sheet_name)
    if not columns:
        return frame
    use = frame.copy()

    if sheet_name == "Adoption_Barriers":
        use["Customer Name"] = _first_public_value(
            use,
            ("Customer Name", "Customer Name_2", "BU_NAME", "customer_name"),
        )
        status = _first_public_value(use, ("Adoption Barrier Status", "Status"))
        severity = _first_public_value(use, ("Severity", "Priority"))
        subject = _first_public_value(use, ("Subject", "title"))
        description = _first_public_value(use, ("Description", "description_2"))
        category = _first_public_value(
            use,
            ("Barrier Category (Final)", "Barrier Category", "Barrier Type"),
        )
        existing_subtechnology = _first_public_value(use, ("sub_technology",))
        technology_evidence = _first_public_value(
            use,
            ("Product Name", "Product", "Feature"),
        )
        derived_subtechnology = technology_evidence.map(
            normalize_subtechnology_label
        )
        use["Adoption Barrier Status"] = status
        use["Status"] = status
        use["Status (Normalized)"] = status.map(normalize_status_label)
        use["Severity"] = severity
        use["Severity (Normalized)"] = severity.map(normalize_severity_label)
        use["Subject"] = subject
        use["title"] = subject
        use["Description"] = description
        use["Barrier Category (Final)"] = category.where(
            category.map(_clean_token).ne(""),
            "Uncategorized",
        )
        existing_specific = existing_subtechnology.map(_clean_token).ne("")
        use["sub_technology"] = existing_subtechnology.where(
            existing_specific,
            derived_subtechnology,
        )
        for column in (
            "Open Date",
            "Due Date",
            "Original Due Date",
            "Closed Date",
            "Next Action Due Date",
        ):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_datetime)
        for column in (
            "Age (Days)",
            "Days in Stage",
            "Hold Days",
            "Linked Cases (Count)",
            "Linked CTAs (Count)",
            "Linked Activities (Count)",
        ):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_integer)
        for column in (
            "Annual Order Value",
            "Product ARR",
            "Service ARR",
            "Gross Retention Rate",
            "BU Health Score",
            "Use Case Health Score",
            "Solution Domain Health Score",
        ):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_number)
        for column in ("Escalated", "Partner Issue"):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_bool)
        prior_references = _first_public_value(use, ("bemscsc_refs",))
        reference_text = subject.map(_clean_token) + " " + description.map(_clean_token)

        reference_pattern = (
            r"(?:BEMS[- ]?\d+|CSC[a-zA-Z0-9]{6,10}|WXCCSA-\d+|CJPIM-\d+|"
            r"CT-\d+|WXCUST-I-\d+|COLLAB-I-\d+)"
        )

        def _canonical_references(existing: Any, text: str) -> str:
            references = {
                token.strip().upper()
                for token in re.split(r"\s*[,;|]\s*", _clean_token(existing))
                if token.strip()
                and re.fullmatch(reference_pattern, token.strip(), flags=re.IGNORECASE)
            }
            references.update(
                match.upper()
                for match in re.findall(
                    reference_pattern,
                    text,
                    flags=re.IGNORECASE,
                )
            )
            return ", ".join(sorted(references))

        use["bemscsc_refs"] = [
            _canonical_references(existing, text)
            for existing, text in zip(prior_references, reference_text)
        ]
    elif sheet_name == "Customer_Pulse":
        use["Account ID"] = _first_public_value(
            use,
            ("Account ID", "Account ID_2", "ACCOUNT_ID_C", "ACCOUNT__C"),
        )
        for column in ("Pulse Date", "As Of Date"):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_datetime)
        if "Pulse Score" in use.columns:
            use["Pulse Score"] = use["Pulse Score"].map(_canonical_public_number)
    elif sheet_name in {"TAC_Cases", "BEMS"}:
        customer = _first_public_value(use, ("Customer", "Customer Name"))
        use["Customer"] = customer
        use["Customer Name"] = customer
        for column in (
            "Date/Time Opened",
            "open_date",
            "Date/Time Closed",
            "closed_date",
        ):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_datetime)
        for column in ("Is Open", "Is Closed", "Is BEMS"):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_bool)
        for column in ("Open Age (Days)", "Closed Age (Days)"):
            if column in use.columns:
                use[column] = use[column].map(_canonical_public_integer)
        if "# of Case Owner Changes" in use.columns:
            use["# of Case Owner Changes"] = use["# of Case Owner Changes"].map(
                _canonical_public_integer
            )
        if "Highest Priority" in use.columns:
            use["Highest Priority"] = use["Highest Priority"].map(
                _canonical_public_bool
            )
    elif sheet_name == "Subscriptions":
        use["Status"] = _first_public_value(
            use,
            ("Status", "SUBSCRIPTION_STATUS", "Subscription Status"),
        )
        use["Renewal Date"] = _first_public_value(
            use,
            ("Renewal Date", "RENEWAL_DATE", "RENEWAL_DATE_C"),
        ).map(_canonical_public_datetime)
        use["Start Date"] = _first_public_value(
            use,
            ("Start Date", "START_DATE", "START_DATE_C", "CONTRACT_START_DATE"),
        ).map(_canonical_public_datetime)
        use["End Date"] = _first_public_value(
            use,
            (
                "End Date",
                "END_DATE",
                "END_DATE_C",
                "CONTRACT_END_DATE",
                "EXPIRATION_DATE",
            ),
        ).map(_canonical_public_datetime)
        use["ARR"] = _first_public_value(
            use,
            ("ARR", "ANNUAL_RECURRING_REVENUE", "SUBSCRIPTION_ARR"),
        ).map(_canonical_public_number)
        currency = _first_public_value(
            use,
            ("Currency", "CURRENCY", "CURRENCY_CODE"),
        )
        use["Currency"] = currency.map(
            lambda value: (
                _clean_token(value).upper()
                if re.fullmatch(r"[A-Za-z]{3}", _clean_token(value))
                else _clean_token(value)[:12]
            )
        )
    elif sheet_name == "Success_Priorities":
        use["Related Customer"] = _first_public_value(
            use,
            ("Related Customer", "RELATED_CUSTOMER__C", "Customer Name", "BU_NAME"),
        )
        use["Success Priority Title"] = _first_public_value(
            use,
            (
                "Success Priority Title",
                "SUCCESS_PRIORITY_TITLE__C",
                "SUCCESS_PRIORITY_NAME__C",
                "PRIORITY_NAME__C",
                "Subject",
                "NAME",
                "TITLE_C",
            ),
        )
        use["Description"] = _first_public_value(
            use,
            ("Description", "DESCRIPTION__C", "DESCRIPTION_C"),
        )
        use["Priority Level"] = _first_public_value(
            use,
            ("Priority Level", "PRIORITY_LEVEL__C", "PRIORITY_C"),
        )
        use["Status"] = _first_public_value(
            use,
            ("Status", "STATUS__C", "STATUS_C"),
        )
        use["Open Date"] = _first_public_value(
            use,
            ("Open Date", "OPEN_DATE_C"),
        ).map(_canonical_public_datetime)
        use["Closed Date"] = _first_public_value(
            use,
            ("Closed Date", "CLOSED_DATE_C", "RESOLVED_DATE"),
        ).map(_canonical_public_datetime)
        use["Owner"] = _first_public_value(use, ("Owner", "OWNERID", "OWNER_C"))
        use["Comments"] = _first_public_value(
            use,
            ("Comments", "COMMENTS__C", "COMMENTS_C"),
        )

    for column in columns:
        if column not in use.columns:
            use[column] = ""
    return use.loc[:, list(columns)]


def _prepare_export_frame(raw: Any, sheet_name: str) -> pd.DataFrame:
    """Apply the exact public-column projection used by the XLSX writer."""

    from report_export_schema import apply_export_schema  # noqa: PLC0415

    frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    frame = frame.drop(
        columns=[column for column in frame.columns if str(column).startswith("_")],
        errors="ignore",
    )
    frame = apply_export_schema(frame, sheet_name=sheet_name)
    frame = _canonical_source_export_frame(frame, sheet_name)
    # Round 148: every canonical Source Data sheet is part of the public
    # workbook contract.  Normalize Snowflake/external rich text at this shared
    # export boundary so evidence hashes describe the same clean cells that
    # recipients and post-write validators read.
    frame = strip_html_from_dataframe(frame)
    context_columns = [column for column in _PUBLIC_CONTEXT_COLUMN_ORDER if column in frame.columns]
    frame = frame.loc[
        :,
        context_columns + [column for column in frame.columns if column not in context_columns],
    ]
    for column in frame.columns:
        if frame[column].dtype != object:
            continue
        frame[column] = frame[column].map(
            lambda value: (
                # Round 148: xlsxwriter serializes Decimal values from object
                # columns as text.  Normalize that public representation
                # before evidence fingerprints are computed so reopening the
                # workbook verifies the same bytes/semantics.
                str(value)
                if isinstance(value, Decimal)
                else json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))
                if isinstance(value, (Mapping, list, tuple, set))
                else value
            )
        )
    if frame.shape[1] == 0:
        frame = pd.DataFrame(columns=["Record_ID"])
    return frame


def validate_evidence_links(
    sheets: Mapping[str, pd.DataFrame],
    *,
    already_exported: bool = False,
) -> Dict[str, Any]:
    """Validate that every evidence locator resolves to the exact workbook row."""

    errors: List[str] = []
    links = sheets.get("Evidence_Links", pd.DataFrame())
    if not isinstance(links, pd.DataFrame):
        links = pd.DataFrame()
    required_columns = {
        "Evidence_Key",
        "Evidence_Type",
        "Source_Sheet",
        "Source_Row_Number",
        "Source_Row_SHA256",
        "Record_ID",
        SOURCE_RECORD_URL_COLUMN,
        "Source_State",
        "Evidence_Role",
    }
    missing_columns = sorted(required_columns - set(links.columns))
    if missing_columns:
        errors.append("Evidence_Links missing columns: " + ", ".join(missing_columns))
        return {"ok": False, "errors": errors, "resolved_rows": 0, "evidence_keys": 0}

    prepared_cache: Dict[str, pd.DataFrame] = {}

    def source_frame(sheet_name: str) -> pd.DataFrame:
        if sheet_name not in prepared_cache:
            raw = sheets.get(sheet_name, pd.DataFrame())
            if not isinstance(raw, pd.DataFrame):
                raw = pd.DataFrame()
            prepared_cache[sheet_name] = raw.copy() if already_exported else _prepare_export_frame(raw, sheet_name)
        return prepared_cache[sheet_name]

    resolved_rows = 0
    locators_by_key: Dict[str, set[Tuple[str, int]]] = {}
    for index, link in links.iterrows():
        key = _clean_token(link.get("Evidence_Key"))
        sheet_name = _clean_token(link.get("Source_Sheet"))
        if not key:
            errors.append(f"Evidence_Links row {index + 2} lacks Evidence_Key")
            continue
        if sheet_name not in sheets:
            errors.append(f"{key} references missing sheet {sheet_name or '<blank>'}")
            continue
        row_number = pd.to_numeric(link.get("Source_Row_Number"), errors="coerce")
        if pd.isna(row_number):
            if _clean_token(link.get("Source_Row_SHA256")) or _clean_token(link.get("Record_ID")):
                errors.append(f"{key} has record identity without a Source_Row_Number")
            continue
        row_number_int = int(row_number)
        position = row_number_int - 2
        frame = source_frame(sheet_name)
        if row_number_int < 2 or position >= len(frame):
            errors.append(f"{key} references out-of-range {sheet_name}!{row_number_int}")
            continue
        locator = (sheet_name, row_number_int)
        if locator in locators_by_key.setdefault(key, set()):
            errors.append(f"{key} repeats evidence locator {sheet_name}!{row_number_int}")
            continue
        locators_by_key[key].add(locator)
        record = frame.iloc[position].to_dict()
        expected_hash = _clean_token(link.get("Source_Row_SHA256"))
        actual_hash = _evidence_row_fingerprint(record)
        if expected_hash != actual_hash:
            errors.append(f"{key} row fingerprint mismatch at {sheet_name}!{row_number_int}")
        expected_id = _clean_token(link.get("Record_ID"))
        actual_id = _clean_token(record.get("Record_ID")) or _clean_token(record.get("Metric_Key"))
        if expected_id and expected_id != actual_id:
            errors.append(f"{key} Record_ID mismatch at {sheet_name}!{row_number_int}")
        expected_url = _clean_token(link.get(SOURCE_RECORD_URL_COLUMN))
        actual_url = _clean_token(record.get(SOURCE_RECORD_URL_COLUMN))
        canonical_url = build_source_record_url(sheet_name, actual_id)
        if expected_url != actual_url:
            errors.append(f"{key} {SOURCE_RECORD_URL_COLUMN} mismatch at {sheet_name}!{row_number_int}")
        if actual_url and (not is_allowed_source_record_url(actual_url) or actual_url != canonical_url):
            errors.append(f"{key} has a noncanonical source-record URL at {sheet_name}!{row_number_int}")
        elif canonical_url and not actual_url:
            errors.append(f"{key} lacks a CSConsole source-record URL at {sheet_name}!{row_number_int}")
        resolved_rows += 1

    lineage = sheets.get("Metric_Lineage", pd.DataFrame())
    if isinstance(lineage, pd.DataFrame) and "Metric_Key" in lineage.columns:
        duplicate_lineage_keys = sorted(
            set(
                lineage.loc[
                    lineage["Metric_Key"].fillna("").astype(str).duplicated(keep=False),
                    "Metric_Key",
                ].astype(str)
            )
        )
        if duplicate_lineage_keys:
            errors.append(
                "Metric_Lineage contains duplicate evidence identities: " + ", ".join(duplicate_lineage_keys[:20])
            )
    lineage_keys = (
        set(lineage.get("Metric_Key", pd.Series(dtype="object")).fillna("").astype(str))
        if isinstance(lineage, pd.DataFrame)
        else set()
    )
    evidence_keys = set(links["Evidence_Key"].fillna("").astype(str))
    missing_lineage = sorted(key for key in lineage_keys if key and key not in evidence_keys)
    if missing_lineage:
        errors.append("visible Metric_Lineage keys lack Evidence_Links: " + ", ".join(missing_lineage[:20]))

    countable_types = {"metric", "chart_point"}
    for evidence_key, group in links.groupby("Evidence_Key", sort=True, dropna=False):
        if str(group.iloc[0].get("Evidence_Type") or "") not in countable_types:
            continue
        metric_value = pd.to_numeric(group.iloc[0].get("Metric_Value"), errors="coerce")
        if pd.isna(metric_value):
            continue
        linked_count = int(pd.to_numeric(group["Source_Row_Number"], errors="coerce").notna().sum())
        if float(metric_value).is_integer() and linked_count != int(metric_value):
            errors.append(f"{evidence_key} resolves {linked_count} row(s), expected {int(metric_value)}")
    return {
        "ok": not errors,
        "errors": errors,
        "resolved_rows": resolved_rows,
        "evidence_keys": len({key for key in evidence_keys if key}),
        "lineage_keys": len({key for key in lineage_keys if key}),
    }


def _digest_cell(value: Any) -> Any:
    """Normalize one pre/post-Excel cell for a stable content digest."""

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime, date)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return {"datetime": timestamp.isoformat()}
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        try:
            return _digest_cell(value.item())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, (int, float, Decimal)):
        # Round 148: XLSX stores numeric cells as IEEE-754 doubles with
        # Excel's 15-significant-digit precision.  Normalize Python floats to
        # that public precision before hashing so harmless binary round-trip
        # noise cannot invalidate otherwise identical evidence rows.
        number = Decimal(format(value, ".15g")) if isinstance(value, float) else Decimal(str(value))
        if not number.is_finite():
            return None
        normalized = number.normalize()
        return {"number": format(normalized, "f")}
    if isinstance(value, str):
        return value if value != "" else None
    return str(value)


def _frame_content_digest(
    frame: pd.DataFrame,
    *,
    sheet_name: str,
    already_exported: bool = False,
) -> str:
    """Hash every public row and column, independent of row/column ordering."""

    prepared = (
        frame.copy()
        if already_exported and isinstance(frame, pd.DataFrame)
        else _prepare_export_frame(frame, sheet_name)
    )
    if prepared.shape[1] == 0:
        prepared = pd.DataFrame(columns=["Record_ID"])
    columns = sorted(str(column) for column in prepared.columns)
    normalized_rows: List[str] = []
    for _, row in prepared.iterrows():
        normalized = {column: _digest_cell(row.get(column)) for column in columns}
        normalized_rows.append(json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str))
    payload = {
        "columns": columns,
        "rows": sorted(normalized_rows),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _source_contract_digests(facts: Mapping[str, Any]) -> "OrderedDict[str, str]":
    """Build deterministic digests for every canonical non-Report_Info sheet."""

    core_sheets = build_source_data_sheets(
        facts,
        _skip_contract_fingerprint=True,
    )
    return OrderedDict(
        (
            sheet_name,
            _frame_content_digest(core_sheets.get(sheet_name, pd.DataFrame()), sheet_name=sheet_name),
        )
        for sheet_name in _SOURCE_CONTRACT_SHEETS
    )


def _report_info_semantic_digest(
    frame: pd.DataFrame,
    *,
    already_exported: bool = False,
) -> str:
    """Hash Report_Info while excluding only its self-referential hashes.

    Scope, as-of, warnings, source states, and their explanatory details are
    delivery facts too. They cannot be included in the workbook fingerprint
    recursively, but they must still be compared directly with canonical
    Report_Info content during both pre-write and post-write validation.
    """

    prepared = (
        frame.copy()
        if already_exported and isinstance(frame, pd.DataFrame)
        else _prepare_export_frame(frame, "Report_Info")
    )
    items = prepared.get("Item", pd.Series(dtype="object")).fillna("").astype(str)
    semantic = prepared.loc[items.ne("Fact_Contract_SHA256") & ~items.str.startswith("Sheet_SHA256:")].copy()
    return _frame_content_digest(
        semantic,
        sheet_name="Report_Info",
        already_exported=True,
    )


def write_source_data_workbook(path: Any, sheets: Mapping[str, pd.DataFrame]) -> str:
    """Write a polished deterministic Source Data workbook from prepared sheets."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(
        target,
        engine="xlsxwriter",
        datetime_format="yyyy-mm-dd hh:mm:ss",
        engine_kwargs={
            "options": {
                "strings_to_formulas": False,
                "strings_to_urls": False,
            }
        },
    ) as writer:
        workbook = writer.book
        try:
            report_info = sheets.get("Report_Info", pd.DataFrame())
            as_of_values = report_info.loc[
                report_info.get("Item", pd.Series(dtype=str)).eq("Data_As_Of_UTC"),
                "Value",
            ]
            created_utc = pd.to_datetime(as_of_values.iloc[0], errors="coerce", utc=True)
            if pd.isna(created_utc):
                raise ValueError("source retrieval clock is unavailable")
            created = created_utc.tz_localize(None)
            workbook.set_properties({"created": created.to_pydatetime()})
        except Exception:  # noqa: BLE001
            pass
        header = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#0070C0",
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
            }
        )
        wrapped_body = workbook.add_format({"text_wrap": True, "valign": "top"})
        hyperlink_body = workbook.add_format(
            {
                "text_wrap": True,
                "valign": "top",
                "font_color": "#0563C1",
                "underline": True,
            }
        )
        period_start_body = workbook.add_format({"num_format": "yyyy-mm-dd", "valign": "top"})
        used_sheet_names: set[str] = set()
        for sheet_name, raw in sheets.items():
            frame = _prepare_export_frame(raw, sheet_name)
            # Excel stores naive datetimes.  Canonical calculations remain UTC
            # aware; only the workbook serialization drops tzinfo after the
            # UTC conversion so the visible wall-clock value is unambiguous.
            for column in frame.columns:
                try:
                    if isinstance(frame[column].dtype, pd.DatetimeTZDtype):
                        frame[column] = frame[column].dt.tz_convert("UTC").dt.tz_localize(None)
                    elif frame[column].dtype == object:
                        frame[column] = frame[column].map(
                            lambda value: (
                                value.tz_convert("UTC").tz_localize(None)
                                if isinstance(value, pd.Timestamp) and value.tzinfo is not None
                                else value
                            )
                        )
                except Exception:  # noqa: BLE001
                    continue
            base_name = str(sheet_name)[:31] or "Sheet"
            safe_name = base_name
            suffix = 2
            while safe_name.casefold() in used_sheet_names:
                suffix_token = f"_{suffix}"
                safe_name = f"{base_name[: 31 - len(suffix_token)]}{suffix_token}"
                suffix += 1
            used_sheet_names.add(safe_name.casefold())
            frame.to_excel(writer, sheet_name=safe_name, index=False)
            worksheet = writer.sheets[safe_name]
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
            worksheet.set_row(0, 24)
            column_widths: List[float] = []
            for column_number, column in enumerate(frame.columns):
                worksheet.write(0, column_number, column, header)
                try:
                    values = frame[column].fillna("").astype(str).map(len)
                    width = max(len(str(column)), int(values.max()) if not values.empty else 0)
                except Exception:  # noqa: BLE001
                    width = len(str(column))
                display_width = min(max(width + 2, 10), 48)
                if str(column) == "Period_Start":
                    display_width = max(display_width, 14)
                column_widths.append(float(display_width))
                worksheet.set_column(
                    column_number,
                    column_number,
                    display_width,
                    period_start_body if str(column) == "Period_Start" else wrapped_body,
                )
                if str(column) == "Period_Start":
                    for row_number, value in enumerate(frame[column], start=1):
                        if pd.isna(value):
                            worksheet.write_blank(
                                row_number,
                                column_number,
                                None,
                                period_start_body,
                            )
                            continue
                        try:
                            timestamp = pd.Timestamp(value)
                            if timestamp.tzinfo is not None:
                                timestamp = timestamp.tz_convert("UTC").tz_localize(None)
                            worksheet.write_datetime(
                                row_number,
                                column_number,
                                timestamp.to_pydatetime(),
                                period_start_body,
                            )
                        except Exception:  # noqa: BLE001
                            worksheet.write(
                                row_number,
                                column_number,
                                str(value),
                                wrapped_body,
                            )
                elif str(column) == SOURCE_RECORD_URL_COLUMN:
                    for row_number, value in enumerate(frame[column], start=1):
                        url = _clean_token(value)
                        if not url:
                            worksheet.write_blank(
                                row_number,
                                column_number,
                                None,
                                wrapped_body,
                            )
                        elif is_allowed_source_record_url(url):
                            worksheet.write_url(
                                row_number,
                                column_number,
                                url,
                                hyperlink_body,
                                string=url,
                            )
                        else:
                            worksheet.write(
                                row_number,
                                column_number,
                                url,
                                wrapped_body,
                            )

            # Fixed-width audit columns keep the workbook navigable, while
            # calculated row heights expose provenance strings, hashes, and
            # structured component details instead of silently clipping them.
            for row_number, values in enumerate(
                frame.itertuples(index=False, name=None),
                start=1,
            ):
                visual_lines = 1
                for column, value, display_width in zip(
                    frame.columns,
                    values,
                    column_widths,
                ):
                    try:
                        if pd.isna(value):
                            continue
                    except (TypeError, ValueError):
                        pass
                    display_value = str(value)
                    if str(column) == "Period_Start":
                        try:
                            display_value = pd.Timestamp(value).strftime("%Y-%m-%d")
                        except Exception:  # noqa: BLE001
                            pass
                    characters_per_line = max(int(display_width * 1.15), 1)
                    lines = sum(
                        max(1, math.ceil(len(part) / characters_per_line))
                        for part in display_value.splitlines() or [""]
                    )
                    visual_lines = max(visual_lines, lines)
                if visual_lines > 1:
                    worksheet.set_row(row_number, min(15 * visual_lines + 3, 300))
    return str(target)


def validate_written_source_workbook(path: Any, facts: Mapping[str, Any]) -> Dict[str, Any]:
    """Reopen a written workbook and verify its public semantic contract."""

    from openpyxl import load_workbook  # noqa: PLC0415
    from report_export_schema import is_internal_column  # noqa: PLC0415

    target = Path(path)
    errors: List[str] = []
    required = {"Report_Info", *_SOURCE_CONTRACT_SHEETS}
    if not target.is_file():
        return {"ok": False, "errors": [f"Source Data File not found: {target}"]}
    # Normal mode is intentional: acceptance must verify the actual hyperlink
    # relationship, not merely the URL-looking cell text.
    workbook = load_workbook(target, read_only=False, data_only=False)
    sheet_names = set(workbook.sheetnames)
    missing = sorted(required - sheet_names)
    if missing:
        errors.append("written workbook missing sheets: " + ", ".join(missing))

    formula_cells = 0
    source_url_cells = 0
    source_url_hyperlinks = 0
    internal_headers: List[str] = []
    sheet_shapes: Dict[str, Tuple[int, int]] = {}
    serialized_frames: Dict[str, pd.DataFrame] = {}
    for worksheet in workbook.worksheets:
        rows = worksheet.iter_rows()
        try:
            header_row = next(rows)
        except StopIteration:
            sheet_shapes[worksheet.title] = (0, 0)
            continue
        headers = [cell.value for cell in header_row]
        for header_value in headers:
            if header_value is not None and is_internal_column(str(header_value)):
                internal_headers.append(f"{worksheet.title}:{header_value}")
        row_count = 0
        serialized_rows: List[List[Any]] = []
        for row in rows:
            row_count += 1
            formula_cells += sum(1 for cell in row if cell.data_type == "f")
            for column_number, cell in enumerate(row):
                if column_number >= len(headers) or headers[column_number] != SOURCE_RECORD_URL_COLUMN:
                    continue
                url = _clean_token(cell.value)
                if not url:
                    continue
                source_url_cells += 1
                hyperlink_target = _clean_token(getattr(getattr(cell, "hyperlink", None), "target", ""))
                if not is_allowed_source_record_url(url):
                    errors.append(f"{worksheet.title}!{cell.coordinate} contains a disallowed source-record URL")
                elif hyperlink_target != url:
                    errors.append(
                        f"{worksheet.title}!{cell.coordinate} is not a matching clickable source-record hyperlink"
                    )
                else:
                    source_url_hyperlinks += 1
            serialized_rows.append([cell.value for cell in row])
        sheet_shapes[worksheet.title] = (row_count, len(headers))
        serialized_frames[worksheet.title] = pd.DataFrame(
            serialized_rows,
            columns=headers,
        )
    workbook.close()
    if formula_cells:
        errors.append(f"written workbook contains {formula_cells} formula cell(s)")
    if internal_headers:
        errors.append("internal headers leaked: " + ", ".join(internal_headers[:10]))

    def read_sheet(name: str) -> pd.DataFrame:
        return serialized_frames.get(name, pd.DataFrame()).copy()

    action_plans = read_sheet("Action_Plans")
    if len(action_plans) != int(facts["action_plan_lifecycle"]["total"]):
        errors.append(
            "written Action_Plans row count does not match canonical distinct total: "
            f"{len(action_plans)} != {facts['action_plan_lifecycle']['total']}"
        )
    if "AdoptIQ_Status_Bucket" in action_plans.columns:
        written_buckets = action_plans["AdoptIQ_Status_Bucket"].fillna("Unknown").value_counts().to_dict()
        expected_buckets = facts["action_plan_lifecycle"]["bucket_counts"]
        if any(int(written_buckets.get(key, 0)) != int(value) for key, value in expected_buckets.items()):
            errors.append("written Action_Plans lifecycle buckets do not match canonical facts")
    elif int(facts["action_plan_lifecycle"]["total"]):
        errors.append("written Action_Plans lacks AdoptIQ_Status_Bucket")
    if "AdoptIQ_Age_Band" in action_plans.columns:
        written_age_bands = action_plans["AdoptIQ_Age_Band"].fillna("Unknown")
        expected_age_bands = facts["action_plan_lifecycle"]["age_band_counts"]
        age_counts = written_age_bands.value_counts().to_dict()
        if any(int(age_counts.get(key, 0)) != int(value) for key, value in expected_age_bands.items()):
            errors.append("written Action_Plans unresolved age bands do not match canonical facts")
        if "AdoptIQ_Status_Bucket" in action_plans.columns:
            completed_mask = action_plans["AdoptIQ_Status_Bucket"].eq("Completed")
            excluded_label = cm.ACTION_PLAN_COMPLETED_AGE_LABEL
            if not written_age_bands.loc[completed_mask].eq(excluded_label).all():
                errors.append("written Action_Plans completed rows are not excluded from aging")
            allowed_age_bands = set(cm.ACTION_PLAN_AGE_BAND_ORDER)
            if not written_age_bands.loc[~completed_mask].isin(allowed_age_bands).all():
                errors.append("written Action_Plans unresolved rows lack a canonical age band")
    elif int(facts["action_plan_lifecycle"]["total"]):
        errors.append("written Action_Plans lacks AdoptIQ_Age_Band")

    bems = read_sheet("BEMS")
    if len(bems) != int(facts["kpis"]["bems"]):
        errors.append(f"written BEMS rows {len(bems)} != canonical KPI {facts['kpis']['bems']}")

    chart_data = read_sheet("Chart_Data")
    expected_chart = facts["chart_data"].copy()
    written_chart = chart_data.loc[
        chart_data.get("Metric_Key", pd.Series(dtype="object")).fillna("").astype(str).ne("")
    ].copy()
    expected_keys = set(expected_chart["Metric_Key"].fillna("").astype(str))
    written_keys = set(written_chart.get("Metric_Key", pd.Series(dtype=str)).fillna("").astype(str))
    if written_keys != expected_keys:
        errors.append("written Chart_Data metric keys differ from canonical chart series")
    if expected_keys and {"Metric_Key", "Value", "Source_State"}.issubset(written_chart.columns):
        expected_by_key = expected_chart.set_index("Metric_Key")
        written_by_key = written_chart.set_index("Metric_Key")
        for metric_key in sorted(expected_keys):
            expected_value = expected_by_key.loc[metric_key, "Value"]
            written_value = written_by_key.loc[metric_key, "Value"]
            both_missing = pd.isna(expected_value) and pd.isna(written_value)
            if not both_missing:
                try:
                    equal_value = float(expected_value) == float(written_value)
                except Exception:  # noqa: BLE001
                    equal_value = str(expected_value) == str(written_value)
                if not equal_value:
                    errors.append(f"written Chart_Data value mismatch for {metric_key}")
            if str(expected_by_key.loc[metric_key, "Source_State"]) != str(
                written_by_key.loc[metric_key, "Source_State"]
            ):
                errors.append(f"written Chart_Data source state mismatch for {metric_key}")

    lineage = read_sheet("Metric_Lineage")
    written_lineage_keys = set(lineage.get("Metric_Key", pd.Series(dtype=str)).fillna("").astype(str))
    expected_lineage_keys = set(facts["metric_lineage"]["Metric_Key"].fillna("").astype(str))
    if written_lineage_keys != expected_lineage_keys:
        errors.append("written Metric_Lineage keys differ from canonical visible claims")

    written_evidence = validate_evidence_links(
        serialized_frames,
        already_exported=True,
    )
    errors.extend(written_evidence["errors"])
    completeness = audit_source_data_frames(serialized_frames)
    errors.extend(completeness["errors"])
    source_hyperlinks = audit_written_source_hyperlinks(target)
    errors.extend(f"Source_Record_URL hyperlink contract: {item}" for item in source_hyperlinks["errors"])

    report_info = read_sheet("Report_Info")
    expected_report_info = build_source_data_sheets(
        facts,
        _skip_contract_fingerprint=True,
    )["Report_Info"]
    if _report_info_semantic_digest(
        report_info,
        already_exported=True,
    ) != _report_info_semantic_digest(expected_report_info):
        errors.append(
            "written Report_Info scope, provenance, warning, or source-state content differs from canonical facts"
        )
    info_items = set(report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str))
    expected_state_items = {f"Source_State:{value}" for value in facts["source_coverage"]["Source_Sheet"].astype(str)}
    if not expected_state_items.issubset(info_items):
        errors.append("written Report_Info is missing one or more source-state rows")
    expected_digests = _source_contract_digests(facts)
    for sheet_name, expected_digest in expected_digests.items():
        actual_digest = _frame_content_digest(
            read_sheet(sheet_name),
            sheet_name=sheet_name,
            already_exported=True,
        )
        if actual_digest != expected_digest:
            errors.append(f"written {sheet_name} content digest differs from canonical source rows")
        digest_item = f"Sheet_SHA256:{sheet_name}"
        digest_rows = report_info.loc[
            report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str) == digest_item
        ]
        if len(digest_rows) != 1:
            errors.append(f"written Report_Info must contain exactly one {digest_item} row")
        elif str(digest_rows.iloc[0].get("Value") or "") != expected_digest:
            errors.append(f"written Report_Info digest differs for {sheet_name}")
    expected_fingerprint = fact_contract_fingerprint(facts, source_digests=expected_digests)
    fingerprint_rows = report_info.loc[
        report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str) == "Fact_Contract_SHA256"
    ]
    if len(fingerprint_rows) != 1:
        errors.append("written Report_Info must contain exactly one Fact_Contract_SHA256 row")
    elif str(fingerprint_rows.iloc[0].get("Value") or "") != expected_fingerprint:
        errors.append("written Report_Info fact fingerprint differs from canonical facts")

    return {
        "ok": not errors,
        "errors": errors,
        "path": str(target),
        "sheet_names": sorted(sheet_names),
        "sheet_shapes": sheet_shapes,
        "formula_cells": formula_cells,
        "source_url_cells": source_url_cells,
        "source_url_hyperlinks": source_url_hyperlinks,
        "evidence": written_evidence,
        "completeness": completeness,
        "source_hyperlinks": source_hyperlinks,
    }


def _set_picture_alt_text(shape: Any, title: str, description: str) -> None:
    try:
        properties = shape._inline.docPr
        properties.set("title", str(title))
        properties.set("descr", str(description))
    except Exception:  # noqa: BLE001
        logger.debug("Round 142 chart alt text could not be applied", exc_info=True)


def _chart_palette(chart_id: str, categories: Sequence[str]) -> List[str]:
    if chart_id == "risk_distribution":
        return [cm.RISK_BAND_COLORS.get(str(category).upper(), cm.RISK_BAND_COLOR_DEFAULT) for category in categories]
    if chart_id == "action_plan_status_aging":
        palette = {
            "Overdue": "#C0392B",
            "Due Soon": "#FFB81C",
            "Open": "#0070C0",
            "Blocked / On Hold": "#7F6000",
            "Completed": "#2E8B57",
            "0–14 days": "#2E8B57",
            "15–30 days": "#00A6A6",
            "31–60 days": "#0070C0",
            "61–90 days": "#E67E22",
            ">90 days": "#C0392B",
            "Unknown": "#7F7F7F",
            "Status unresolved": "#7F7F7F",
            "Created date unavailable": "#7F7F7F",
        }
        return [palette.get(str(category), "#0070C0") for category in categories]
    return ["#0070C0", "#00BCEB", "#6ABF4B", "#FFB81C", "#C0392B"][: len(categories)]


def _chart_needs_detail_panel(
    values: Iterable[Any],
    *,
    dominance_ratio: float = 20.0,
) -> bool:
    """Return whether one positive series visually suppresses the others.

    The detail panel repeats lower-volume facts at a useful scale; it never
    transforms values or replaces the all-source view.  Zeroes are retained in
    the chart but do not create a mathematically meaningless ratio.
    """

    positive = sorted(
        float(value)
        for value in values
        if pd.notna(value) and float(value) > 0
    )
    if len(positive) < 2:
        return False
    return positive[-1] / positive[-2] >= float(dominance_ratio)


def _label_horizontal_bars(axis: Any, bars: Any, values: Sequence[float]) -> None:
    upper = max(max(values, default=0.0) * 1.24, 1.0)
    axis.set_xlim(0, upper)
    for bar, value in zip(bars, values):
        axis.text(
            bar.get_width() + upper * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{int(value)}",
            ha="left",
            va="center",
            fontsize=9,
        )


def _render_chart_image(chart_id: str, chart_rows: pd.DataFrame, target: Path) -> bool:
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.warning("Round 142 chart rendering unavailable: %s", exc)
        return False

    try:
        # Keep charts readable without letting four figures dominate the
        # manager-facing Word report.  The full values and provenance remain
        # available in Chart_Data in the paired Source Data workbook.
        if chart_id == "action_plan_status_aging":
            use = chart_rows.dropna(subset=["Value"]).copy()
            status_label = cm.ACTION_PLAN_STATUS_SERIES
            age_label = cm.ACTION_PLAN_AGE_SERIES
            status_rows = use.loc[use["Series"].astype(str) == status_label]
            age_rows = use.loc[use["Series"].astype(str) == age_label]
            if status_rows.empty or age_rows.empty:
                return False
            fig, axes = plt.subplots(
                nrows=1,
                ncols=2,
                figsize=(8.4, 4.4),
                constrained_layout=True,
            )
            for axis, panel_rows, panel_title in (
                (axes[0], status_rows, "Lifecycle status · all plans"),
                (axes[1], age_rows, "Age · unresolved only"),
            ):
                canonical_categories = panel_rows["Category"].astype(str).tolist()
                categories = [
                    _action_plan_chart_category_display(
                        category,
                        age_series=panel_title.startswith("Age"),
                    )
                    for category in canonical_categories
                ]
                values = panel_rows["Value"].astype(float).tolist()
                colors = _chart_palette(chart_id, canonical_categories)
                bars = axis.barh(
                    categories,
                    values,
                    color=colors,
                    edgecolor="#FFFFFF",
                )
                axis.set_title(panel_title, fontsize=10, fontweight="bold")
                axis.set_xlabel("Distinct plans")
                axis.invert_yaxis()
                upper = max(max(values, default=0) * 1.22, 1.0)
                axis.set_xlim(0, upper)
                for bar, value in zip(bars, values):
                    axis.text(
                        bar.get_width() + upper * 0.02,
                        bar.get_y() + bar.get_height() / 2,
                        f"{int(value)}",
                        ha="left",
                        va="center",
                        fontsize=9,
                    )
                axis.grid(axis="x", alpha=0.2)
                axis.spines["top"].set_visible(False)
                axis.spines["right"].set_visible(False)
            fig.suptitle("Action Plan Status and Aging", fontsize=12, fontweight="bold")
        elif chart_id not in {"activity_mix", "activity_trend"}:
            fig, axis = plt.subplots(figsize=(8.4, 3.6), constrained_layout=True)
        if chart_id == "activity_trend":
            prepared: List[Tuple[str, pd.DataFrame]] = []
            maxima: List[Tuple[str, float]] = []
            for series, group in chart_rows.groupby("Series", sort=True):
                use = group.dropna(subset=["Period_Start", "Value"]).sort_values("Period_Start")
                if use.empty:
                    continue
                label = str(series)
                prepared.append((label, use))
                maxima.append((label, float(use["Value"].astype(float).max())))
            if not prepared:
                return False
            split_detail = _chart_needs_detail_panel(value for _, value in maxima)
            if split_detail:
                dominant_series = max(maxima, key=lambda item: item[1])[0]
                fig, axes = plt.subplots(
                    nrows=1,
                    ncols=2,
                    figsize=(8.4, 3.8),
                    constrained_layout=True,
                )
                panels = (
                    (axes[0], prepared, "All sources"),
                    (
                        axes[1],
                        [item for item in prepared if item[0] != dominant_series],
                        "Lower-volume sources · detail",
                    ),
                )
            else:
                fig, axis = plt.subplots(figsize=(8.4, 3.6), constrained_layout=True)
                panels = ((axis, prepared, "Activity Trend"),)
            plotted = False
            for panel_axis, panel_series, panel_title in panels:
                for series, use in panel_series:
                    panel_axis.plot(
                        use["Period_Start"],
                        use["Value"],
                        marker="o",
                        linewidth=2,
                        label=series,
                    )
                    plotted = True
                panel_axis.set_title(panel_title)
                panel_axis.set_xlabel("Week starting")
                panel_axis.set_ylabel("Distinct records")
                # All count charts use a truthful zero baseline.  In
                # particular, the lower-volume detail panel must not magnify
                # a 1 -> 2 change by silently truncating its y-axis.
                panel_axis.set_ylim(bottom=0)
                panel_axis.legend(loc="best", fontsize=7)
                panel_axis.grid(axis="y", alpha=0.2)
                panel_axis.spines["top"].set_visible(False)
                panel_axis.spines["right"].set_visible(False)
            if not plotted:
                plt.close(fig)
                return False
            if split_detail:
                fig.suptitle(
                    "Activity Trend · lower-volume detail repeats exact source values",
                    fontsize=11,
                    fontweight="bold",
                )
            fig.autofmt_xdate(rotation=30)
        elif chart_id == "activity_mix":
            use = chart_rows.dropna(subset=["Value"]).copy()
            if use.empty:
                return False
            categories = use["Category"].astype(str).tolist()
            values = use["Value"].astype(float).tolist()
            colors = _chart_palette(chart_id, categories)
            split_detail = _chart_needs_detail_panel(values)
            if split_detail:
                dominant_index = max(range(len(values)), key=values.__getitem__)
                detail_indices = [index for index in range(len(values)) if index != dominant_index]
                fig, axes = plt.subplots(
                    nrows=1,
                    ncols=2,
                    figsize=(8.4, 4.0),
                    constrained_layout=True,
                )
                panels = (
                    (axes[0], list(range(len(values))), "All sources"),
                    (axes[1], detail_indices, "Lower-volume sources · detail"),
                )
            else:
                fig, axis = plt.subplots(figsize=(8.4, 3.6), constrained_layout=True)
                panels = ((axis, list(range(len(values))), "Activity Mix by Source"),)
            for panel_axis, indices, panel_title in panels:
                panel_categories = [categories[index] for index in indices]
                panel_values = [values[index] for index in indices]
                panel_colors = [colors[index] for index in indices]
                bars = panel_axis.barh(
                    panel_categories,
                    panel_values,
                    color=panel_colors,
                    edgecolor="#FFFFFF",
                )
                panel_axis.set_title(panel_title, fontsize=10, fontweight="bold")
                panel_axis.set_xlabel("Distinct records")
                panel_axis.invert_yaxis()
                _label_horizontal_bars(panel_axis, bars, panel_values)
                panel_axis.grid(axis="x", alpha=0.2)
                panel_axis.spines["top"].set_visible(False)
                panel_axis.spines["right"].set_visible(False)
            if split_detail:
                fig.suptitle(
                    "Activity Mix by Source · lower-volume detail repeats exact values",
                    fontsize=11,
                    fontweight="bold",
                )
        elif chart_id != "action_plan_status_aging":
            use = chart_rows.dropna(subset=["Value"]).copy()
            if use.empty:
                plt.close(fig)
                return False
            categories = use["Category"].astype(str).tolist()
            values = use["Value"].astype(float).tolist()
            colors = _chart_palette(chart_id, categories)
            bars = axis.bar(categories, values, color=colors, edgecolor="#FFFFFF")
            titles = {
                "activity_mix": "Activity Mix by Source",
                "risk_distribution": "Customer Risk Distribution",
            }
            axis.set_title(titles.get(chart_id, chart_id.replace("_", " ").title()))
            axis.set_ylabel("Distinct records" if chart_id != "risk_distribution" else "Customers")
            axis.tick_params(axis="x", rotation=25)
            for bar, value in zip(bars, values):
                axis.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{int(value)}", ha="center", va="bottom"
                )
        if chart_id not in {"action_plan_status_aging", "activity_mix", "activity_trend"}:
            axis.grid(axis="y", alpha=0.2)
            axis.spines["top"].set_visible(False)
            axis.spines["right"].set_visible(False)
        fig.savefig(target, dpi=180, facecolor="white")
        plt.close(fig)
        return target.exists() and target.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("Round 142 chart '%s' failed: %s", chart_id, exc)
        try:
            plt.close("all")
        except Exception:  # noqa: BLE001
            pass
        return False


_REPORT_SPECIFIC_CATEGORY_ORDER = (
    "subscription_identity",
    "arr",
    "renewal_date",
    "renewal_probability",
    "status",
    "term",
    "risk",
    "recommendation",
    "customer_identity",
    "product",
    "analysis_metadata",
    "other",
)


def _report_specific_family(facts: Mapping[str, Any]) -> str:
    """Return the adapter-owned family eligible for a bounded Word section."""

    adapter = facts.get("legacy_adapter")
    family = str(adapter.get("report_family") or "").strip().casefold() if isinstance(adapter, Mapping) else ""
    if family in {"renewal", "subscription"}:
        return family
    report_type = str(facts.get("report_type") or "").strip().casefold()
    if "renewal" in report_type:
        return "renewal"
    if "subscription" in report_type:
        return "subscription"
    return ""


def _report_specific_fact_category(label: object, source_field: object) -> str:
    """Classify one preserved fact without interpreting or changing its value."""

    signal = re.sub(
        r"\s+",
        " ",
        f"{label or ''} {source_field or ''}".replace("_", " ").strip().casefold(),
    )
    if re.search(
        r"\b(?:analysis date|analysis period|next review date|report generated|"
        r"generated at|data as of|retrieved at)\b",
        signal,
    ):
        return "analysis_metadata"
    if re.search(
        r"\bsubscription\s*(?:id|identifier|number|#)\b|"
        r"\bcontract\s*(?:id|identifier|number|#)\b",
        signal,
    ):
        return "subscription_identity"
    if re.search(r"\barr\b|\bannual recurring revenue\b", signal):
        return "arr"
    if re.search(r"\brenewal\s+probability\b|\bprobability\b", signal):
        # This is a source-reported commercial signal, not AdoptIQ's
        # calculated canonical risk score.  Keep it visible even when the
        # canonical risk model correctly withholds a ranking for partial
        # evidence; the label makes the distinction explicit.
        return "renewal_probability"
    if re.search(r"\bdate\b|\bexpiration\b|\bexpiry\b|\bcontract\s+end\b", signal):
        return "renewal_date"
    if re.search(r"\bstatus\b|\bstate\b", signal):
        return "status"
    if re.search(r"\bterm\b|\bduration\b|\btenure\b", signal):
        return "term"
    if re.search(r"\brisk\b", signal):
        return "risk"
    if re.search(
        r"\brecommend(?:ation|ed)?\b|\bnext action\b|\bnext step\b|\bdecision\b",
        signal,
    ):
        return "recommendation"
    if re.search(r"\bcustomer\b|\baccount\b", signal):
        return "customer_identity"
    if re.search(r"\btechnology\b|\bproduct\b|\boffer\b|\bservice\b", signal):
        return "product"
    return "other"


def _manager_fact_label(
    family: str,
    label: object,
    source_field: object,
) -> str:
    """Return a concise manager-facing label without changing the source fact."""

    raw = str(label or source_field or "Reported fact").strip()
    token = re.sub(r"[^a-z0-9]", "", f"{source_field or ''} {raw}".casefold())
    if family == "renewal" and "overallriskscore" in token:
        return "Source-reported overall risk score (0–10)"
    if family == "renewal" and "renewalprobability" in token:
        return "Source-reported renewal probability (%)"
    if family == "renewal" and "arramount" in token:
        return "ARR (source currency)"
    if "riskscore0100" in token:
        return "Source-reported risk score (0–100)"
    if "riskscore010" in token:
        return "Source-reported risk score (0–10)"
    special = {
        "analysisdate": "Analysis date (report metadata)",
        "analysisperiod": "Analysis period (report metadata)",
        "nextreviewdate": (
            "Suggested next review date (legacy analysis)"
            if family == "renewal"
            else "Next review date"
        ),
    }
    compact = re.sub(r"[^a-z0-9]", "", raw.casefold())
    return special.get(compact, _humanize_identifier(raw, fallback="Reported fact"))


def _reported_fact_display_value(
    value: object,
    *,
    source_field: object = "",
    limit: int = 320,
) -> str:
    """Render a source scalar while making generic missing markers explicit."""

    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    field_signal = str(source_field or "").replace("_", " ").casefold()
    if "date" in field_signal and not isinstance(value, (datetime, date)):
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.notna(parsed):
            # The exact timestamp remains untouched in the paired workbook.
            # A decision report needs a readable calendar date, not a raw
            # serializer value with microseconds and timezone punctuation.
            value = parsed.date()
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
            text = value.date().isoformat()
        else:
            text = value.isoformat()
    elif isinstance(value, date):
        text = value.isoformat()
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    if not text:
        return ""
    if text.casefold() in {"unknown", "n/a", "na", "none", "null", "nan", "<na>"}:
        # The raw source marker remains untouched in the Source Data row.
        # Manager-facing Word should explain its meaning instead of repeating
        # a context-free placeholder.
        return "Not provided by source"
    if len(text) <= max(int(limit), 1):
        return text
    return text[: max(int(limit) - 35, 1)].rstrip() + "… [complete value in Source Data]"


def _report_specific_field_priority(
    category: str,
    source_field: object,
    label: object,
) -> int:
    """Prefer decision fields over audit metadata within each fact category."""

    signal = re.sub(
        r"[^a-z0-9]",
        "",
        f"{source_field or ''} {label or ''}".casefold(),
    )
    ranked_patterns: Mapping[str, Sequence[str]] = {
        "renewal_date": (
            "renewaldate",
            "expirationdate",
            "expirydate",
            "contractenddate",
            "enddate",
            "duedate",
        ),
        "risk": (
            "riskband",
            "riskcategory",
            "riskscore0100",
            "riskscore010",
            "overallriskscore",
        ),
        "renewal_probability": (
            "renewalprobability",
            "probability",
        ),
        "analysis_metadata": (
            "nextreviewdate",
            "analysisperiod",
            "analysisdate",
            "generatedat",
        ),
    }
    for position, pattern in enumerate(ranked_patterns.get(category, ())):
        if pattern in signal:
            return position
    return 99


def _is_manager_decision_fact(
    *,
    source_sheet: object,
    source_field: object,
    label: object,
    value: object,
) -> bool:
    """Keep audit facts in Source Data without crowding the manager surface.

    Several legacy workbooks publish summary *counts* such as
    ``Key_Findings=2`` and ``Recommendations=4``.  Those values describe the
    shape of the old report, not the customer or renewal decision.  They stay
    fully preserved and evidence-linked in the paired workbook, but the Word
    report prioritizes the underlying findings/recommendations and actual
    timing, value, status, risk, and ownership facts instead.
    """

    signal = re.sub(
        r"[^a-z0-9]",
        "",
        f"{source_field or ''} {label or ''}".casefold(),
    )
    field_token = re.sub(r"[^a-z0-9]", "", str(source_field or "").casefold())
    label_token = re.sub(r"[^a-z0-9]", "", str(label or "").casefold())
    source = re.sub(r"[^a-z0-9]", "", str(source_sheet or "").casefold())
    summary_counter = any(
        token in signal
        for token in (
            "keyfindings",
            "findingcount",
            "keyrecommendations",
            "recommendationcount",
            "actionitemcount",
        )
    ) or (
        source == "keymetrics"
        and (
            field_token in {"findings", "keyfindings", "recommendations", "actionitems"}
            or label_token in {"findings", "keyfindings", "recommendations", "actionitems"}
        )
    )
    if not summary_counter:
        return True
    if isinstance(value, bool):
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(numeric)


def _report_specific_decision_fact_bundle(
    facts: Mapping[str, Any],
    *,
    limit: int = REPORT_SPECIFIC_FACT_LIMIT,
) -> Dict[str, Any]:
    """Select a concise, exact-keyed Renewal/Subscription decision surface.

    Eligible values come only from rows emitted by the legacy-family adapter.
    The selection diversifies ARR/date/status/term/risk/recommendation/identity
    before filling any remaining slots, so a large portfolio cannot crowd the
    section with one repeated fact type.  Complete rows remain in Subscriptions,
    Metric_Lineage, and Evidence_Links in the paired Source Data workbook.
    """

    family = _report_specific_family(facts)
    try:
        bounded_limit = max(1, min(int(limit), REPORT_SPECIFIC_FACT_LIMIT))
    except (TypeError, ValueError):
        bounded_limit = REPORT_SPECIFIC_FACT_LIMIT
    empty = {
        "family": family,
        "limit": bounded_limit,
        "total_available": 0,
        "selected_count": 0,
        "rows": [],
    }
    if family not in {"renewal", "subscription"}:
        return empty
    frames = facts.get("frames")
    subscriptions = frames.get("subscriptions") if isinstance(frames, Mapping) else None
    if not isinstance(subscriptions, pd.DataFrame) or subscriptions.empty:
        return empty

    key_prefix = f"legacy.family.{family}."
    sibling_values: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for sibling_position, (_, sibling) in enumerate(subscriptions.iterrows(), 1):
        if str(sibling.get("Legacy_Record_Type") or "").strip() != "Family-specific reported fact":
            continue
        sibling_key = str(sibling.get("Metric_Key") or "").strip()
        if not sibling_key.startswith(key_prefix):
            continue
        sibling_sheet = str(sibling.get("Legacy_Source_Sheet") or "Legacy report").strip().casefold()
        try:
            sibling_row = str(int(sibling.get("Legacy_Source_Row_Number")))
        except (TypeError, ValueError):
            sibling_row = str(sibling_position + 1)
        sibling_field = re.sub(
            r"[^a-z0-9]",
            "",
            str(sibling.get("Legacy_Source_Field") or "").casefold(),
        )
        if sibling_field:
            sibling_values.setdefault((sibling_sheet, sibling_row), {})[sibling_field] = sibling.get(
                "Legacy_Fact_Value"
            )
    candidates: List[Dict[str, Any]] = []
    total_retained = 0
    manager_surface_omitted = 0
    canonical_risk_state = str(
        ((facts.get("risk_summary") or {}).get("source_state") or "unavailable")
    ).strip().casefold()
    seen_keys: set[str] = set()
    for position, (_, row) in enumerate(subscriptions.iterrows(), 1):
        if str(row.get("Legacy_Record_Type") or "").strip() != ("Family-specific reported fact"):
            continue
        evidence_key = str(row.get("Metric_Key") or "").strip()
        if not evidence_key.startswith(key_prefix) or evidence_key in seen_keys:
            continue
        total_retained += 1
        fact_label = str(row.get("Legacy_Fact_Label") or row.get("Legacy_Source_Field") or "Reported fact").strip()
        source_field = str(row.get("Legacy_Source_Field") or "").strip()
        source_sheet = str(row.get("Legacy_Source_Sheet") or "Legacy report").strip()
        category = _report_specific_fact_category(fact_label, source_field)
        if not _is_manager_decision_fact(
            source_sheet=source_sheet,
            source_field=source_field,
            label=fact_label,
            value=row.get("Legacy_Fact_Value"),
        ):
            seen_keys.add(evidence_key)
            manager_surface_omitted += 1
            continue
        source_sheet_key = re.sub(
            r"[^a-z0-9]",
            "",
            source_sheet.casefold(),
        )
        source_field_key = re.sub(
            r"[^a-z0-9]",
            "",
            source_field.casefold(),
        )
        if family == "renewal":
            # Risk_Components are already represented by the canonical risk
            # model and its dedicated workbook sheet. Legacy numeric priority
            # rows are ordering metadata, not standalone manager decisions.
            # Most importantly, do not resurrect a legacy risk score or a
            # risk-derived recommendation after the canonical contract has
            # correctly withheld risk ranking for incomplete evidence.
            suppress = (
                source_sheet_key == "riskcomponents"
                or (
                    source_sheet_key == "recommendations"
                    and source_field_key == "priority"
                )
                or (
                    category in {"risk", "recommendation"}
                    and canonical_risk_state not in {"available", "zero"}
                )
                or (
                    category == "analysis_metadata"
                    and "nextreviewdate" not in source_field_key
                )
            )
            if suppress:
                seen_keys.add(evidence_key)
                manager_surface_omitted += 1
                continue
        reported_value = _reported_fact_display_value(
            row.get("Legacy_Fact_Value"),
            source_field=source_field,
        )
        if not fact_label or not reported_value:
            continue
        seen_keys.add(evidence_key)
        context = ""
        for context_field in (
            "BU_NAME",
            "Customer",
            "Customer Name",
            "SUBSCRIPTION_ID",
            "ACCOUNT_ID_C",
        ):
            context_value = _reported_fact_display_value(row.get(context_field), limit=120)
            if context_value:
                context = context_value
                break
        display_parts = []
        if context and context.casefold() not in fact_label.casefold():
            display_parts.append(context)
        manager_label = _manager_fact_label(family, fact_label, source_field)
        if (
            family not in {"renewal", "subscription"}
            and source_sheet
            and source_sheet.casefold() not in fact_label.casefold()
        ):
            display_parts.append(_humanize_identifier(source_sheet, fallback="Legacy report"))
        display_parts.append(manager_label)
        display_label = " — ".join(display_parts)
        try:
            source_row_number: Any = int(row.get("Legacy_Source_Row_Number"))
        except (TypeError, ValueError):
            source_row_number = position + 1
        siblings = sibling_values.get((source_sheet.casefold(), str(source_row_number)), {})
        if category == "arr":
            currency = _reported_fact_display_value(siblings.get("currency"), limit=16)
            try:
                amount = float(row.get("Legacy_Fact_Value"))
            except (TypeError, ValueError):
                amount = math.nan
            if math.isfinite(amount) and (currency or "currency" in siblings):
                decimals = 0 if amount.is_integer() else 2
                formatted_amount = f"{amount:,.{decimals}f}"
                if decimals:
                    formatted_amount = formatted_amount.rstrip("0").rstrip(".")
                if currency and currency != "Not provided by source":
                    reported_value = f"{currency} {formatted_amount}"
                elif "currency" in siblings:
                    reported_value = f"{formatted_amount} (currency not provided by source)"
                else:
                    reported_value = formatted_amount
        elif category == "renewal_probability":
            try:
                probability = float(row.get("Legacy_Fact_Value"))
            except (TypeError, ValueError):
                probability = math.nan
            if math.isfinite(probability):
                decimals = 0 if probability.is_integer() else 1
                reported_value = f"{probability:.{decimals}f}%"
        candidates.append(
            {
                "fact": display_label,
                "reported_value": reported_value,
                "evidence_key": evidence_key,
                "category": category,
                "context": context,
                "source_sheet": source_sheet,
                "source_row_number": source_row_number,
                "source_field": source_field,
                "field_priority": _report_specific_field_priority(
                    _report_specific_fact_category(fact_label, source_field),
                    source_field,
                    fact_label,
                ),
            }
        )

    category_rank = {category: index for index, category in enumerate(_REPORT_SPECIFIC_CATEGORY_ORDER)}
    candidates.sort(
        key=lambda item: (
            category_rank.get(str(item.get("category")), 999),
            int(item.get("field_priority") or 0),
            str(item.get("fact") or "").casefold(),
            int(item.get("source_row_number") or 0),
            str(item.get("evidence_key") or ""),
        )
    )
    selected: List[Dict[str, Any]] = []
    selected_keys: set[str] = set()
    semantic_keys: set[Tuple[str, str, str]] = set()

    def add_candidate(candidate: Mapping[str, Any]) -> None:
        evidence_key = str(candidate.get("evidence_key") or "")
        semantic_key = (
            str(candidate.get("context") or "").casefold(),
            str(candidate.get("category") or ""),
            str(candidate.get("reported_value") or "").casefold(),
        )
        if len(selected) >= bounded_limit or evidence_key in selected_keys or semantic_key in semantic_keys:
            return
        selected.append(dict(candidate))
        selected_keys.add(evidence_key)
        semantic_keys.add(semantic_key)

    candidates_by_category = {
        category: [candidate for candidate in candidates if candidate.get("category") == category]
        for category in _REPORT_SPECIFIC_CATEGORY_ORDER
    }
    max_category_rows = max(
        (len(rows) for rows in candidates_by_category.values()),
        default=0,
    )
    # Round-robin keeps a large portfolio from filling this bounded manager
    # surface with one repetitive field type. Audit metadata gets one slot at
    # most; exact remaining rows still live in the paired workbook.
    for category_position in range(max_category_rows):
        for category in _REPORT_SPECIFIC_CATEGORY_ORDER:
            if category == "analysis_metadata" and category_position > 0:
                continue
            category_candidates = candidates_by_category[category]
            if category_position < len(category_candidates):
                add_candidate(category_candidates[category_position])
            if len(selected) >= bounded_limit:
                break
        if len(selected) >= bounded_limit:
            break

    return {
        "family": family,
        "limit": bounded_limit,
        "total_available": total_retained,
        "manager_surface_eligible": len(candidates),
        "manager_surface_omitted": manager_surface_omitted,
        "selected_count": len(selected),
        "rows": selected,
    }


def _decision_brief_family(facts: Mapping[str, Any]) -> str:
    """Return the public decision job for the selected report family."""

    family = _report_specific_family(facts)
    if family:
        return family
    report_type = str(facts.get("report_type") or "").strip().casefold()
    if "leader" in report_type:
        return "leader"
    if "comprehensive" in report_type:
        return "comprehensive"
    if "compact" in report_type or "executive intelligence" in report_type:
        return "compact"
    return "portfolio"


def _report_surface_policy(facts: Mapping[str, Any]) -> Dict[str, Any]:
    """Define a distinct manager job for each Word report family.

    The paired workbook always retains the complete canonical source contract.
    This policy changes only what earns space in the decision document, which
    prevents Compact and Leader from becoming differently titled copies of the
    Comprehensive deep dive.
    """

    family = _decision_brief_family(facts)
    policies: Mapping[str, Mapping[str, Any]] = {
        "compact": {
            "kpi_heading": "Decision Snapshot",
            "kpi_mode": "compact",
            "show_source_coverage": False,
            "show_decision_signals": False,
            "show_lifecycle_rollup": False,
            "show_detailed_action_rollup": False,
            "show_summary": True,
            "insight_limit": 2,
            "detailed_action_limit": 0,
        },
        "leader": {
            "kpi_heading": "Management Snapshot",
            "kpi_mode": "leader",
            "show_source_coverage": True,
            "show_decision_signals": False,
            "show_lifecycle_rollup": True,
            "show_detailed_action_rollup": True,
            "show_summary": True,
            "insight_limit": 3,
            "detailed_action_limit": 3,
        },
        "comprehensive": {
            "kpi_heading": "KPI and Data-Coverage Snapshot",
            "kpi_mode": "full",
            "show_source_coverage": True,
            "show_decision_signals": True,
            "show_lifecycle_rollup": True,
            "show_detailed_action_rollup": True,
            "show_summary": True,
            "insight_limit": None,
            "detailed_action_limit": 3,
        },
        "renewal": {
            "kpi_heading": "Renewal Health Snapshot",
            "kpi_mode": "renewal",
            "show_source_coverage": True,
            "show_decision_signals": True,
            "show_lifecycle_rollup": False,
            "show_detailed_action_rollup": True,
            "show_summary": True,
            "insight_limit": 3,
            "detailed_action_limit": 4,
        },
        "subscription": {
            "kpi_heading": "Subscription Health Snapshot",
            "kpi_mode": "subscription",
            "show_source_coverage": True,
            "show_decision_signals": True,
            "show_lifecycle_rollup": False,
            "show_detailed_action_rollup": True,
            "show_summary": True,
            "insight_limit": 3,
            "detailed_action_limit": 4,
        },
        "portfolio": {
            "kpi_heading": "KPI and Data-Coverage Snapshot",
            "kpi_mode": "full",
            "show_source_coverage": True,
            "show_decision_signals": True,
            "show_lifecycle_rollup": True,
            "show_detailed_action_rollup": True,
            "show_summary": True,
            "insight_limit": None,
            "detailed_action_limit": 5,
        },
    }
    return {"family": family, **dict(policies.get(family, policies["portfolio"]))}


_DECISION_BRIEF_COPY = {
    "leader": (
        "Leader Interventions",
        "Manager focus: intervene first on the highest-risk accounts and the "
        "team-owned overdue or blocked plans most likely to change customer outcomes.",
    ),
    "comprehensive": (
        "Portfolio Priorities",
        "Portfolio focus: act first on compound-risk accounts, cross-source blockers, "
        "and overdue or blocked execution commitments.",
    ),
    "compact": (
        "Immediate Customer Calls",
        "Immediate focus: make the highest-value customer calls first, using the "
        "strongest available risk drivers and execution commitments.",
    ),
    "renewal": (
        "Renewal Decisions",
        "Renewal focus: connect commercial timing and value with adoption, support, "
        "execution risk, ownership, and the next evidence-backed move.",
    ),
    "subscription": (
        "Subscription Decisions",
        "Subscription focus: confirm entitlement and term, then connect adoption "
        "health, support friction, ownership, and the next evidence-backed move.",
    ),
    "portfolio": (
        "Immediate Priorities",
        "Decision focus: act first on the strongest customer-risk evidence and the "
        "overdue or blocked execution commitments most likely to change outcomes.",
    ),
}


def _compact_risk_decision_rows(facts: Mapping[str, Any]) -> List[List[Any]]:
    """Convert canonical risk rows into a readable four-column decision surface."""

    risk_state = str((facts.get("risk_summary") or {}).get("source_state") or "unavailable").strip().casefold()
    if risk_state not in {"available", "zero"}:
        return []
    compact_rows: List[List[Any]] = []
    for account, band, score, _state, drivers, action in _visible_risk_decision_rows(facts):
        try:
            score_text = f"{float(score):.1f}/100"
        except (TypeError, ValueError):
            score_text = str(score)
        compact_rows.append([account, f"{band} | {score_text}", drivers, action])
    return compact_rows


def _decision_brief_action_rows(
    facts: Mapping[str, Any],
    *,
    limit: int = 3,
) -> List[List[str]]:
    """Return the three most urgent already-ranked Action Plan moves."""

    lifecycle = facts.get("action_plan_lifecycle") or {}
    state = str(lifecycle.get("source_state") or "unavailable").strip().casefold()
    if state not in {"available", "zero", "partial"}:
        return []
    rows: List[List[str]] = []
    for row in _decision_brief_selected_action_plans(facts, limit=limit):
        due = str(row[5] or "").strip()
        urgency_parts = [
            str(row[8] or "Priority not provided"),
            str(row[4] or "Status not provided"),
        ]
        if due:
            urgency_parts.append(due if due == "Due date not provided" else f"due {due}")
        account_owner = (
            " / ".join(part for part in (str(row[1] or "").strip(), str(row[2] or "").strip()) if part)
            or "Unassigned / Portfolio"
        )
        rows.append(
            [
                f"{row[0]}: {row[3]}",
                account_owner,
                " | ".join(urgency_parts),
                str(row[7] or "Resolve the disclosed evidence gap."),
            ]
        )
    return rows


def _decision_brief_selected_action_plans(
    facts: Mapping[str, Any],
    *,
    limit: int = 3,
) -> List[List[Any]]:
    """Return the exact already-ranked plans rendered in the action-first brief."""

    lifecycle = facts.get("action_plan_lifecycle") or {}
    state = str(lifecycle.get("source_state") or "unavailable").strip().casefold()
    if state not in {"available", "zero", "partial"}:
        return []
    return list(facts.get("top_action_plans") or [])[: max(int(limit), 1)]


def _decision_brief_action_evidence_keys(
    facts: Mapping[str, Any],
    *,
    limit: int = 3,
) -> List[str]:
    """Bind every brief Action Plan row to its exact recommendation evidence key."""

    keys: List[str] = []
    for row in _decision_brief_selected_action_plans(facts, limit=limit):
        source_id = _clean_token(row[0] if row else "") or "Missing source ID"
        key = evidence_entity_key("recommendation", source_id)
        if key not in keys:
            keys.append(key)
    return keys


def _decision_brief_contract(facts: Mapping[str, Any]) -> Dict[str, Any]:
    """Freeze the exact family-aware, action-first Word decision surface."""

    family = _decision_brief_family(facts)
    title, introduction = _DECISION_BRIEF_COPY[family]
    risk_state = str((facts.get("risk_summary") or {}).get("source_state") or "unavailable").strip().casefold()
    lifecycle = facts.get("action_plan_lifecycle") or {}
    action_plan_state = str(lifecycle.get("source_state") or "unavailable").strip().casefold()
    risk_rows = _compact_risk_decision_rows(facts)
    action_rows = _decision_brief_action_rows(facts)
    action_evidence_keys = _decision_brief_action_evidence_keys(facts)
    state_label = _COVERAGE_STATE_LABELS.get(
        risk_state,
        _humanize_identifier(risk_state, fallback="Unavailable"),
    )
    action_state_label = _COVERAGE_STATE_LABELS.get(
        action_plan_state,
        _humanize_identifier(action_plan_state, fallback="Unavailable"),
    )
    return {
        "family": family,
        "heading": f"Decision Brief: {title}",
        "introduction": introduction,
        "risk_rows": risk_rows,
        "risk_gap": (
            "Customer risk ranking is withheld because evidence coverage is "
            f"{state_label}; resolve the disclosed source gaps before prioritizing "
            "accounts."
            if not risk_rows and risk_state not in {"available", "zero"}
            else ("No customer-level risk profile could be calculated for this scope." if not risk_rows else "")
        ),
        "action_rows": action_rows,
        "action_caveat": (
            "Known retained Action Plan moves are shown below as exact evidence, "
            "but the list is a lower bound because source coverage is partial."
            if action_rows and action_plan_state == "partial"
            else ""
        ),
        "action_evidence_keys": action_evidence_keys,
        "action_source_reference": (
            _artifact_reference_text("Evidence_Links", action_evidence_keys) if action_evidence_keys else ""
        ),
        "action_gap": (
            "No retained unresolved Action Plan move is available, but source coverage "
            "is Partial; this is not evidence that the selected scope has zero open plans."
            if not action_rows and action_plan_state == "partial"
            else "Immediate Action Plan moves are withheld because source coverage is "
            f"{action_state_label}; resolve the disclosed source gap before assigning "
            "execution priorities."
            if not action_rows and action_plan_state not in {"available", "zero"}
            else ("No unresolved Action Plans require an immediate move in this scope." if not action_rows else "")
        ),
    }


def _render_detailed_action_rollup(
    facts: Mapping[str, Any],
    decision_brief: Mapping[str, Any],
) -> bool:
    """Avoid repeating the same small partial plan set twice in concise reports."""

    if not _report_surface_policy(facts)["show_detailed_action_rollup"]:
        return False
    top_rows = list(facts.get("top_action_plans") or [])
    if not top_rows:
        return False
    report_type = str(facts.get("report_type") or "").strip().casefold()
    lifecycle_state = str(
        (facts.get("action_plan_lifecycle") or {}).get("source_state")
        or "unavailable"
    ).strip().casefold()
    brief_rows = list(decision_brief.get("action_rows") or [])
    if (
        "leader" not in report_type
        and lifecycle_state == "partial"
        and brief_rows
    ):
        return False
    return True


def _detailed_action_rollup_rows(
    facts: Mapping[str, Any],
    decision_brief: Mapping[str, Any],
) -> List[List[Any]]:
    """Return the bounded Word-only Action Plan detail selection.

    The canonical fact bundle and paired workbook retain every selected-scope
    Action Plan.  This helper limits only the duplicated manager-facing Word
    rollup so Leader stays useful rather than overwhelming; semantic and link
    validators consume this same list and therefore cannot silently disagree
    with the renderer.
    """

    if not _render_detailed_action_rollup(facts, decision_brief):
        return []
    limit = int(_report_surface_policy(facts).get("detailed_action_limit") or 0)
    if limit <= 0:
        return []
    return list(facts.get("top_action_plans") or [])[:limit]


def _report_specific_heading(family: str) -> str:
    return {
        "renewal": "Renewal Decision Facts",
        "subscription": "Subscription Decision Facts",
    }.get(str(family or "").casefold(), "Report-Specific Decision Facts")


def _comprehensive_account_evidence_rows(
    facts: Mapping[str, Any],
) -> List[List[Any]]:
    """Return a non-ranked account evidence view for Comprehensive reports.

    Canonical risk may be withheld when any contributor is incomplete, but
    exact scoped rows that were retained still answer a different, useful
    question: where is execution/support evidence concentrated? This table
    deliberately avoids a risk score and labels every partial count as a lower
    bound, so added depth cannot imply a ranking the evidence does not support.
    """

    if _decision_brief_family(facts) != "comprehensive":
        return []
    rows: List[List[Any]] = []
    for row in facts.get("account_summary") or []:
        action_state = str(row[9] or "unavailable").strip().casefold()
        barrier_state = str(row[11] or "unavailable").strip().casefold()
        tac_state = str(row[12] or "unavailable").strip().casefold()
        states = {action_state, barrier_state, tac_state}
        if states <= {"available", "zero"}:
            posture = "Complete scoped evidence"
        elif states & {"failed", "unavailable", "unknown"}:
            posture = "One or more fields unavailable"
        elif "stale" in states:
            posture = "Stale retained evidence"
        else:
            posture = "Partial retained lower bounds"
        try:
            open_plans = int(row[3] or 0)
        except (TypeError, ValueError):
            open_plans = 0
        try:
            overdue_plans = int(row[4] or 0)
        except (TypeError, ValueError):
            overdue_plans = 0
        try:
            critical_barriers = int(row[5] or 0)
        except (TypeError, ValueError):
            critical_barriers = 0
        try:
            tac_cases = int(row[6] or 0)
        except (TypeError, ValueError):
            tac_cases = 0
        if states & {"failed", "unavailable", "unknown"}:
            review_focus = "Resolve unavailable evidence before drawing an account conclusion."
        elif overdue_plans and critical_barriers:
            review_focus = "Reset overdue commitments and escalate critical/high adoption barriers."
        elif overdue_plans:
            review_focus = "Confirm an owner and recovery date for overdue commitments."
        elif critical_barriers:
            review_focus = "Assign an escalation path for critical/high adoption barriers."
        elif tac_cases and open_plans:
            review_focus = "Connect support themes to open customer-success commitments."
        elif tac_cases:
            review_focus = "Review support themes and confirm whether proactive follow-up is needed."
        elif open_plans:
            review_focus = "Validate ownership, due dates, and evidence of progress on open plans."
        else:
            review_focus = "Confirm that zero retained exceptions reflects complete source coverage."
        rows.append(
            [
                row[0],
                posture,
                _coverage_aware_compact_display(row[3], action_state),
                _coverage_aware_compact_display(row[4], action_state),
                _coverage_aware_compact_display(row[5], barrier_state),
                _coverage_aware_compact_display(row[6], tac_state),
                review_focus,
            ]
        )
    return rows


def _leader_intervention_rows(facts: Mapping[str, Any]) -> List[List[Any]]:
    """Translate exact member rollups into deterministic manager interventions."""

    if _decision_brief_family(facts) != "leader" or str(facts.get("scope_type") or "").casefold() != "team":
        return []
    rows: List[List[Any]] = []
    for member in facts.get("member_summary") or []:
        action_state = str(member[7] or "unavailable").strip().casefold()
        barrier_state = str(member[9] or "unavailable").strip().casefold()
        tac_state = str(member[10] or "unavailable").strip().casefold()
        try:
            open_plans = int(member[2] or 0)
        except (TypeError, ValueError):
            open_plans = 0
        try:
            overdue_plans = int(member[3] or 0)
        except (TypeError, ValueError):
            overdue_plans = 0
        try:
            barriers = int(member[4] or 0)
        except (TypeError, ValueError):
            barriers = 0
        try:
            tac_cases = int(member[5] or 0)
        except (TypeError, ValueError):
            tac_cases = 0
        states = {action_state, barrier_state, tac_state}
        if states & {"failed", "unavailable", "unknown"}:
            intervention = "Restore missing evidence, then set a manager checkpoint."
        elif overdue_plans:
            intervention = "Review overdue commitments; confirm owner, recovery date, and escalation need."
        elif barriers and open_plans:
            intervention = "Connect open commitments to barrier removal and confirm the next checkpoint."
        elif barriers:
            intervention = "Review barrier ownership and decide whether manager escalation is needed."
        elif tac_cases and open_plans:
            intervention = "Connect recurring support work to open success-plan commitments."
        elif open_plans:
            intervention = "Confirm due dates and evidence of progress for open commitments."
        elif tac_cases:
            intervention = "Review support concentration and validate proactive customer follow-up."
        else:
            intervention = "Validate account coverage and confirm no intervention is currently required."
        rows.append(
            [
                member[0],
                _coverage_aware_compact_display(member[1], member[6]),
                _coverage_aware_compact_display(open_plans, action_state),
                _coverage_aware_compact_display(overdue_plans, action_state),
                _coverage_aware_compact_display(barriers, barrier_state),
                _coverage_aware_compact_display(tac_cases, tac_state),
                intervention,
            ]
        )
    return rows


def _has_shared_team_attribution(facts: Mapping[str, Any]) -> bool:
    """Return true only when one retained record has multiple named owners."""

    frames = facts.get("frames")
    if not isinstance(frames, Mapping):
        return False
    for frame in frames.values():
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        if "Attributed_Team_Members" not in frame.columns:
            continue
        for value in frame["Attributed_Team_Members"].tolist():
            names = {
                token.strip().casefold()
                for token in re.split(r"\s*(?:;|\|)\s*", _clean_token(value))
                if token.strip()
                and token.strip().casefold() != "unassigned / portfolio"
            }
            if len(names) > 1:
                return True
    return False


def _visible_decision_signal_rows(facts: Mapping[str, Any]) -> List[List[Any]]:
    """Keep exact signals/actions while dropping workbook-only evidence chrome."""

    rows: List[List[Any]] = []
    items = list(facts.get("decision_signals") or [])
    # Leader stays an intervention brief rather than repeating the full
    # Comprehensive signal inventory.  A verified exact CSC correlation is a
    # materially different exception: hiding it would make an auditable defect
    # linkage available only in the workbook.  Surface only those exact joins
    # (bounded) in Leader; Comprehensive/Renewal/Subscription retain their
    # complete family-appropriate signal view.
    if _decision_brief_family(facts) == "leader":
        items = [
            item
            for item in items
            if str(item.get("source_key") or "").strip() == "defect_correlations"
        ][:3]
    for item in items:
        account = str(item.get("account") or "Portfolio").strip()
        signal = str(item.get("signal") or "No conclusion available.").strip()
        scoped_signal = signal if account.casefold() == "portfolio" else f"{account}: {signal}"
        rows.append(
            [
                _humanize_identifier(item.get("source_sheet"), fallback="Report data"),
                item.get("source_state_label"),
                scoped_signal,
                item.get("decision_implication"),
            ]
        )
    return rows


def _add_source_reference(doc: Document, metric_keys: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(_artifact_reference_text("Metric_Lineage", metric_keys))
    run.italic = True
    run.font.size = Pt(7.5)
    run.font.color.rgb = RGBColor(0x58, 0x59, 0x5B)


def _add_evidence_reference(doc: Document, evidence_keys: Sequence[str]) -> None:
    """Render exact row-backed keys from the companion Evidence_Links sheet."""

    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(_artifact_reference_text("Evidence_Links", evidence_keys))
    run.italic = True
    run.font.size = Pt(7.5)
    run.font.color.rgb = RGBColor(0x58, 0x59, 0x5B)


def _replace_cell_with_source_record_link(
    cell: Any,
    *,
    source_key: str,
    record_id: Any,
    display_text: Optional[str] = None,
) -> bool:
    """Turn an existing Word table cell into one safe external record link."""

    url = build_source_record_url(source_key, record_id)
    if not url or not is_allowed_source_record_url(url):
        return False
    try:
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415

        text = str(display_text if display_text is not None else cell.text)
        cell.text = ""
        paragraph = cell.paragraphs[0]
        relationship_id = paragraph.part.relate_to(
            url,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            is_external=True,
        )
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), relationship_id)
        run = OxmlElement("w:r")
        run_properties = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        run_properties.extend([color, underline])
        run.append(run_properties)
        text_node = OxmlElement("w:t")
        text_node.text = text
        run.append(text_node)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Could not add source-record hyperlink to Word table", exc_info=True)
        return False


def _apply_word_table_geometry(
    table: Any,
    widths_inches: Sequence[float],
    *,
    cell_margin_twips: int = 90,
) -> None:
    """Apply exact, readable geometry to one manager-facing decision table."""

    if table is None or not widths_inches:
        return
    try:
        from docx.oxml import OxmlElement  # noqa: PLC0415
        from docx.oxml.ns import qn  # noqa: PLC0415

        widths = [max(int(round(float(width) * 1440)), 1) for width in widths_inches]
        if not table.rows or len(table.rows[0].cells) != len(widths):
            return
        table.autofit = False
        table_width = sum(widths)
        tbl_pr = table._tbl.tblPr
        tbl_w = tbl_pr.find(qn("w:tblW"))
        if tbl_w is None:
            tbl_w = OxmlElement("w:tblW")
            tbl_pr.insert(0, tbl_w)
        tbl_w.set(qn("w:type"), "dxa")
        tbl_w.set(qn("w:w"), str(table_width))

        tbl_grid = table._tbl.tblGrid
        for grid_col in list(tbl_grid):
            tbl_grid.remove(grid_col)
        for width in widths:
            grid_col = OxmlElement("w:gridCol")
            grid_col.set(qn("w:w"), str(width))
            tbl_grid.append(grid_col)

        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                tc_pr = cell._tc.get_or_add_tcPr()
                tc_w = tc_pr.find(qn("w:tcW"))
                if tc_w is None:
                    tc_w = OxmlElement("w:tcW")
                    tc_pr.append(tc_w)
                tc_w.set(qn("w:type"), "dxa")
                tc_w.set(qn("w:w"), str(width))
                tc_mar = tc_pr.find(qn("w:tcMar"))
                if tc_mar is None:
                    tc_mar = OxmlElement("w:tcMar")
                    tc_pr.append(tc_mar)
                for edge in ("top", "left", "bottom", "right"):
                    margin = tc_mar.find(qn(f"w:{edge}"))
                    if margin is None:
                        margin = OxmlElement(f"w:{edge}")
                        tc_mar.append(margin)
                    margin.set(qn("w:w"), str(max(int(cell_margin_twips), 0)))
                    margin.set(qn("w:type"), "dxa")
    except Exception:  # noqa: BLE001
        logger.debug("Round 165 decision-table geometry could not be applied", exc_info=True)


_SOURCE_DISPLAY_LABELS = {
    "action_plans": "Action Plans",
    "Action_Plans": "Action Plans",
    "adoption_barriers": "Adoption Barriers",
    "Adoption_Barriers": "Adoption Barriers",
    "customer_pulse": "Customer Pulse",
    "Customer_Pulse": "Customer Pulse",
    "csone_tac": "TAC Cases",
    "tac_cases": "TAC Cases",
    "TAC_Cases": "TAC Cases",
    "bems": "BEMS",
    "BEMS": "BEMS",
    "subscriptions": "Subscriptions",
    "Subscriptions": "Subscriptions",
    "success_priorities": "Success Priorities",
    "Success_Priorities": "Success Priorities",
    "external_incidents": "External Incidents",
    "External_Incidents": "External Incidents",
    "external_bugs": "External Bugs",
    "External_Bugs": "External Bugs",
    "defect_correlations": "Defect Correlations",
    "Defect_Correlations": "Defect Correlations",
    "risk_components": "Risk Components",
    "Risk_Components": "Risk Components",
    "csconsole_action_plans": "CSConsole Action Plans",
    "csconsole_adoption_barriers": "CSConsole Adoption Barriers",
    "csconsole_customer_pulse": "CSConsole Customer Pulse",
    "csconsole_success_priorities": "CSConsole Success Priorities",
}

_COVERAGE_STATE_LABELS = {
    "available": "Available",
    "complete": "Available",
    "partial": "Partial",
    "stale": "Stale",
    "failed": "Unavailable",
    "unavailable": "Unavailable",
    "filtered": "Filtered to scope",
    "local_fixture_guarded": "Offline test data",
}


def _coverage_aware_display(value: Any, state: object) -> Any:
    """Show useful retained evidence without presenting it as complete.

    A partial or stale frame can still contain exact, validated rows. Calling
    their count simply ``Unavailable`` hid useful evidence and contradicted
    the row-level tables below it. The public value now says what is known and
    labels it as a lower bound; failed/unavailable sources remain unavailable.
    """

    normalized = str(state or "unavailable").strip().casefold()
    has_retained_value = bool(_clean_token(value))
    if normalized in {"available", "zero", "complete"}:
        return value if has_retained_value else "Unavailable"
    if normalized == "partial":
        return (
            f"Known retained: {value} (Partial lower bound)"
            if has_retained_value
            else "Unavailable — no retained count"
        )
    if normalized == "stale":
        return (
            f"Known retained: {value} (Stale)"
            if has_retained_value
            else "Unavailable — no retained count"
        )
    if normalized in {"failed", "unavailable", "unknown"}:
        return "Unavailable"
    label = _COVERAGE_STATE_LABELS.get(
        normalized,
        _humanize_identifier(normalized, fallback="Unavailable"),
    )
    return "Unavailable" if str(label).casefold() == "unavailable" else f"Unavailable ({label})"


def _coverage_aware_compact_display(value: Any, state: object) -> Any:
    """Render dense decision-table counts without hiding source limitations.

    Multi-column manager tables do not have room for the full
    ``Known retained: N (Partial lower bound)`` phrase in every metric cell.
    Repeating it caused narrow columns to wrap one character at a time in the
    rendered Leader and Comprehensive reports.  ``At least N`` preserves the
    exact lower-bound meaning; nearby table copy and Source Coverage retain the
    full source-state explanation.
    """

    normalized = str(state or "unavailable").strip().casefold()
    has_retained_value = bool(_clean_token(value))
    if normalized in {"available", "zero", "complete"}:
        return value if has_retained_value else "Unavailable"
    if normalized == "partial":
        return f"At least {value}" if has_retained_value else "Unavailable"
    if normalized == "stale":
        return f"{value} (stale)" if has_retained_value else "Unavailable"
    if normalized in {"failed", "unavailable", "unknown"}:
        return "Unavailable"
    label = _COVERAGE_STATE_LABELS.get(
        normalized,
        _humanize_identifier(normalized, fallback="Unavailable"),
    )
    return "Unavailable" if str(label).casefold() == "unavailable" else f"Unavailable ({label})"


def _coverage_aware_scalar_display(value: Any, state: object) -> Any:
    """Render a computed scalar without falsely turning it into a lower bound.

    Counts from a partial source can truthfully be labelled ``At least N``.
    Composite scores cannot: missing inputs may move a score in either
    direction.  Retain a computed scalar only with an explicit input-quality
    qualifier, and fail closed when no usable scalar or source exists.
    """

    normalized = str(state or "unavailable").strip().casefold()
    has_retained_value = bool(_clean_token(value))
    if normalized in {"available", "zero", "complete"}:
        return value if has_retained_value else "Unavailable"
    if normalized == "partial":
        return f"{value} (partial inputs)" if has_retained_value else "Unavailable (Partial)"
    if normalized == "stale":
        return f"{value} (stale inputs)" if has_retained_value else "Unavailable (Stale)"
    if normalized in {"failed", "unavailable", "unknown"}:
        return "Unavailable"
    label = _COVERAGE_STATE_LABELS.get(
        normalized,
        _humanize_identifier(normalized, fallback="Unavailable"),
    )
    return "Unavailable" if str(label).casefold() == "unavailable" else f"Unavailable ({label})"


def _word_kpi_rows(facts: Mapping[str, Any]) -> List[List[Any]]:
    """Return the family-sized KPI surface without changing canonical values."""

    kpis = facts["kpis"]
    lifecycle = facts["action_plan_lifecycle"]
    lifecycle_state = str(lifecycle.get("source_state") or "available")
    risk_state = str(facts["risk_summary"].get("source_state") or "available")
    coverage_by_sheet = facts["source_coverage"].set_index("Source_Sheet")

    def source_state(sheet_name: str) -> str:
        try:
            return str(coverage_by_sheet.loc[sheet_name, "Source_State"])
        except Exception:  # noqa: BLE001
            return "unavailable"

    core_states = [
        source_state(sheet_name)
        for sheet_name in (
            "Subscriptions",
            "Action_Plans",
            "Adoption_Barriers",
            "Customer_Pulse",
            "TAC_Cases",
            "Success_Priorities",
        )
    ]
    if any(state in {"failed", "unavailable", "partial", "stale"} for state in core_states):
        customer_state = (
            "unavailable" if all(state in {"failed", "unavailable"} for state in core_states) else "partial"
        )
    else:
        customer_state = "available"

    rows: List[List[Any]] = [
        ["Customers", _coverage_aware_display(kpis["customers"], customer_state), "kpi.customers"],
        [
            "Subscriptions",
            _coverage_aware_display(kpis["subscriptions"], source_state("Subscriptions")),
            "kpi.subscriptions",
        ],
    ]
    if str(facts.get("scope_type") or "").casefold() in {"team", "member"}:
        rows.append(["Team members", kpis["team_members"], "kpi.team_members"])
    rows.extend(
        [
            ["Action Plans", _coverage_aware_display(lifecycle["total"], lifecycle_state), "kpi.action_plans_total"],
            [
                "Open / overdue / due soon",
                _coverage_aware_display(
                    f"{lifecycle['open']} / {lifecycle['overdue']} / {lifecycle['due_soon']}",
                    lifecycle_state,
                ),
                "kpi.action_plans_open",
            ],
            [
                "Completed / blocked / status unresolved",
                _coverage_aware_display(
                    f"{lifecycle['completed']} / {lifecycle['blocked_on_hold']} / {lifecycle['unknown']}",
                    lifecycle_state,
                ),
                "kpi.action_plans_completed",
            ],
            [
                "Adoption Barriers",
                _coverage_aware_display(kpis["adoption_barriers"], source_state("Adoption_Barriers")),
                "kpi.adoption_barriers",
            ],
            [
                "Customer Pulse",
                _coverage_aware_display(kpis["customer_pulse"], source_state("Customer_Pulse")),
                "kpi.customer_pulse",
            ],
            ["TAC Cases", _coverage_aware_display(kpis["tac_cases"], source_state("TAC_Cases")), "kpi.tac_cases"],
            ["BEMS (TAC subset)", _coverage_aware_display(kpis["bems"], source_state("BEMS")), "kpi.bems"],
            [
                "Success Priorities",
                _coverage_aware_display(kpis["success_priorities"], source_state("Success_Priorities")),
                "kpi.success_priorities",
            ],
            [
                "External Incidents",
                _coverage_aware_display(kpis["external_incidents"], source_state("External_Incidents")),
                "kpi.external_incidents",
            ],
            [
                "External Bugs",
                _coverage_aware_display(kpis["external_bugs"], source_state("External_Bugs")),
                "kpi.external_bugs",
            ],
            [
                "High-risk customers",
                _coverage_aware_display(kpis["high_risk_customers"], risk_state),
                "kpi.high_risk_customers",
            ],
        ]
    )
    mode = str(_report_surface_policy(facts)["kpi_mode"])
    allowed_by_mode = {
        "compact": {
            "Customers",
            "Subscriptions",
            "Open / overdue / due soon",
            "Adoption Barriers",
            "TAC Cases",
            "High-risk customers",
        },
        "leader": {
            "Customers",
            "Subscriptions",
            "Team members",
            "Action Plans",
            "Open / overdue / due soon",
            "Adoption Barriers",
            "TAC Cases",
            "High-risk customers",
        },
        "renewal": {
            "Customers",
            "Subscriptions",
            "Open / overdue / due soon",
            "Adoption Barriers",
            "Customer Pulse",
            "TAC Cases",
            "High-risk customers",
        },
        "subscription": {
            "Customers",
            "Subscriptions",
            "Open / overdue / due soon",
            "Adoption Barriers",
            "Customer Pulse",
            "TAC Cases",
            "High-risk customers",
        },
    }
    allowed = allowed_by_mode.get(mode)
    return rows if allowed is None else [row for row in rows if row[0] in allowed]


def _warning_is_validation_provenance(warning: Mapping[str, Any]) -> bool:
    """Return whether a warning describes validation mode, not missing data.

    Guarded-fixture provenance must be prominent, but it must not imply that
    the fixture's own source frames are incomplete.  Keeping those two ideas
    separate lets local acceptance say both truths at once: the simulated
    inputs were complete, and no live Cisco system was validated.
    """

    kind = str(warning.get("kind") or "").strip().casefold()
    dataset = str(warning.get("dataset") or warning.get("source") or "").strip().casefold()
    effect = str(
        warning.get("effect")
        or warning.get("detail")
        or warning.get("message")
        or ""
    ).strip().casefold()
    return bool(
        "fixture" in kind
        or dataset == "live source validation"
        or (kind == "deferred" and "no live" in effect)
    )


def _report_purpose_copy(report_type: Any) -> str:
    """Describe the decision job of each report family without changing facts."""

    family = str(report_type or "").strip().casefold()
    if family == "leader":
        lead = (
            "Use this report to choose manager interventions, assign owners, and track "
            "follow-through across the selected team, member, or customer."
        )
    elif family == "comprehensive":
        lead = (
            "Use this report for a structured portfolio and account deep dive: reconcile "
            "cross-source evidence, identify gaps, and prepare informed customer reviews."
        )
    elif family == "compact":
        lead = (
            "Use this report as a short decision call sheet for the few customer moves "
            "that need attention now."
        )
    elif family == "renewal":
        lead = (
            "Use this report to connect renewal timing and source-reported commercial "
            "facts with adoption, support, and retention actions."
        )
    elif family == "subscription":
        lead = (
            "Use this report to inspect one authorized subscription's term, entitlement, "
            "adoption, support context, and next move."
        )
    else:
        lead = "Use this report to make evidence-backed customer decisions for the selected scope."
    return (
        f"{lead} Complete activity, case, and source records remain in the separately "
        "named Source Data File."
    )


def _word_scope_subtitle(facts: Mapping[str, Any]) -> str:
    """Return the exact manager-visible scope statement for the Word report."""

    parts = [
        str(facts.get("manager_name") or ""),
        (f"{str(facts.get('scope_type') or '').title()}: {str(facts.get('scope_value') or '')}"),
    ]
    technology = str(facts.get("technology") or "").strip()
    if technology or str(facts.get("report_type") or "").casefold() == "leader":
        parts.append(f"Technology: {technology or 'All'}")
    parts.append(f"{facts.get('days')}-day window")
    return " • ".join(parts)


def _executive_summary_contract(facts: Mapping[str, Any]) -> Dict[str, str]:
    """Freeze the exact visible executive-summary claims and citations.

    The summary is the first decision surface a manager reads.  Keeping its
    deterministic prose in one helper lets the renderer and the semantic
    validator compare exact text, so a wrong customer, Action Plan, or
    activity count cannot survive publication merely because the tables are
    correct.
    """

    kpis = facts["kpis"]
    ap = facts["action_plan_lifecycle"]
    ap_state = str(ap.get("source_state") or "available")
    coverage_by_sheet = facts["source_coverage"].set_index("Source_Sheet")

    def source_state(sheet: str) -> str:
        try:
            return str(coverage_by_sheet.loc[sheet, "Source_State"])
        except Exception:  # noqa: BLE001
            return "unavailable"

    core_customer_states = [
        source_state(sheet)
        for sheet in (
            "Subscriptions",
            "Action_Plans",
            "Adoption_Barriers",
            "Customer_Pulse",
            "TAC_Cases",
            "Success_Priorities",
        )
    ]
    if any(state in {"failed", "unavailable", "partial", "stale"} for state in core_customer_states):
        customer_state = (
            "unavailable" if all(state in {"failed", "unavailable"} for state in core_customer_states) else "partial"
        )
    else:
        customer_state = "available"

    data_warnings = [
        warning
        for warning in (facts.get("partial_data_warnings") or [])
        if isinstance(warning, Mapping) and not _warning_is_validation_provenance(warning)
    ]
    fixture_validation_only = bool(facts.get("partial_data_warnings")) and not data_warnings
    fixture_validation_only = fixture_validation_only or (
        "fixture" in str(facts.get("data_mode") or "").casefold()
        and facts.get("live_validation_performed") is False
    )
    coverage_is_limited = bool(data_warnings) or not facts["activity_mix"]["is_complete"]
    coverage_is_limited = coverage_is_limited or any(
        state in {"failed", "unavailable", "partial", "stale"} for state in core_customer_states
    )
    if data_warnings:
        coverage_sentence = "Coverage has disclosed limitations; review the warning and Source Data File before acting."
    elif coverage_is_limited:
        coverage_sentence = (
            "Coverage has disclosed limitations; review Source Coverage and the Source Data File before acting."
        )
    elif fixture_validation_only:
        coverage_sentence = (
            "Coverage is complete for this guarded offline fixture; live Cisco source "
            "validation was not performed."
        )
    else:
        coverage_sentence = "Coverage is complete across the validated sources for this scope."

    if ap_state == "partial":
        action_plan_sentence = (
            f"Known retained Action Plans: {ap['total']} total; {ap['open']} open, "
            f"{ap['overdue']} overdue, and {ap['due_soon']} due within "
            f"{ap['due_soon_days']} days. These are lower bounds because source "
            "coverage is partial."
        )
    elif ap_state == "stale":
        action_plan_sentence = (
            f"Known retained Action Plans: {ap['total']} total; {ap['open']} open, "
            f"{ap['overdue']} overdue, and {ap['due_soon']} due within "
            f"{ap['due_soon_days']} days. These values are stale and require refresh "
            "before assignment."
        )
    elif ap_state not in {"available", "zero"}:
        action_plan_sentence = (
            "Action Plan metrics are unavailable for this run because coverage is "
            f"{_COVERAGE_STATE_LABELS.get(ap_state, ap_state.title())}; retained rows "
            "remain in the Source Data File, but no complete lifecycle count is asserted."
        )
    else:
        overdue_verb = "is" if int(ap["overdue"]) == 1 else "are"
        due_soon_verb = "is" if int(ap["due_soon"]) == 1 else "are"
        action_plan_sentence = (
            f"Open Action Plans: {ap['open']}. Of these, {ap['overdue']} {overdue_verb} "
            f"overdue and {ap['due_soon']} {due_soon_verb} due within "
            f"{ap['due_soon_days']} days."
        )

    customer_noun = "customer" if int(kpis["customers"] or 0) == 1 else "customers"
    member_noun = "team member" if int(kpis["team_members"] or 0) == 1 else "team members"
    show_team_member_claim = str(facts.get("scope_type") or "").casefold() in {
        "team",
        "member",
    }
    if customer_state == "partial":
        customer_verb = "is" if int(kpis["customers"] or 0) == 1 else "are"
        scope_coverage_sentence = (
            f"At least {kpis['customers']} {customer_noun} {customer_verb} evidenced in retained "
            "selected-scope records (partial lower bound)"
        )
        if show_team_member_claim:
            scope_coverage_sentence += f"; the scope covers {kpis['team_members']} {member_noun}"
    elif customer_state == "stale":
        scope_coverage_sentence = (
            f"Known retained scope: {kpis['customers']} {customer_noun} (stale coverage)"
        )
        if show_team_member_claim:
            scope_coverage_sentence += f" and {kpis['team_members']} {member_noun}"
    elif customer_state not in {"available", "zero"}:
        customer_label = _COVERAGE_STATE_LABELS.get(
            customer_state,
            customer_state.title(),
        )
        scope_coverage_sentence = (
            f"The customer count for the selected scope is unavailable ({customer_label} source coverage)"
        )
        if show_team_member_claim:
            scope_coverage_sentence += f"; the scope covers {kpis['team_members']} {member_noun}"
    else:
        scope_coverage_sentence = f"The selected scope covers {kpis['customers']} {customer_noun}"
        if show_team_member_claim:
            scope_coverage_sentence += f" and {kpis['team_members']} {member_noun}"

    if facts["activity_mix"]["is_complete"]:
        known_activity_sentence = (
            f"Known activity total: {kpis['known_total_activities']} distinct records "
            "across available Action Plans, barriers, pulse, and TAC sources."
        )
    else:
        known_activity_sentence = (
            f"Known retained activity lower bound: {kpis['known_total_activities']} "
            "distinct records across Action Plans, barriers, pulse, and TAC; incomplete "
            "sources prevent a complete total."
        )

    source_keys = "kpi.customers; "
    if show_team_member_claim:
        source_keys += "kpi.team_members; "
    source_keys += "kpi.action_plans_open; kpi.action_plans_overdue; kpi.action_plans_due_soon; chart.activity_mix.*"
    return {
        "summary": (f"{scope_coverage_sentence}. {action_plan_sentence} {known_activity_sentence} {coverage_sentence}"),
        "summary_source": (f"[Source: Source Data File → Metric_Lineage / {source_keys}]"),
        "source_keys": source_keys,
        "purpose": _report_purpose_copy(facts.get("report_type")),
        "customer_state": customer_state,
        "show_team_member_claim": "1" if show_team_member_claim else "0",
    }


def _rendered_decision_insight_names(facts: Mapping[str, Any]) -> List[str]:
    """Return the canonical insight subset appropriate for this report job.

    Comprehensive keeps the complete deterministic deep dive.  Leader,
    Renewal, and Subscription retain three decision-useful lines, while
    Compact stays intentionally brief.  Facts and evidence remain present in
    the paired workbook regardless of Word-surface selection.
    """

    available = [
        name
        for name in _DECISION_INSIGHT_ORDER
        if isinstance(facts.get("decision_insights"), Mapping)
        and facts["decision_insights"].get(name)
    ]
    limit = _report_surface_policy(facts).get("insight_limit")
    if limit is None:
        return available
    return available[: max(int(limit), 0)]


def _add_frozen_decision_insights(doc: Document, facts: Mapping[str, Any]) -> None:
    """Render only fingerprinted insight prose with exact adjacent lineage."""

    decision_insights = facts.get("decision_insights") or {}
    if not isinstance(decision_insights, Mapping):
        raise ValueError("canonical decision_insights must be a mapping")
    rendered_names = _rendered_decision_insight_names(facts)
    if rendered_names:
        insight_heading = doc.add_heading("What Is Changing", level=3)
        insight_heading.paragraph_format.keep_with_next = True
    for insight_name in rendered_names:
        insight = decision_insights.get(insight_name)
        if not insight:
            continue
        if not isinstance(insight, Mapping):
            raise ValueError(f"canonical decision insight {insight_name} must be a mapping")
        prefix = _clean_token(insight.get("paragraph_prefix"))
        paragraph_text = _clean_token(insight.get("paragraph_text"))
        metric_key = _clean_token(insight.get("metric_key"))
        if (
            prefix != _DECISION_INSIGHT_PREFIXES[insight_name]
            or not paragraph_text.startswith(prefix + " ")
            or not metric_key
        ):
            raise ValueError(f"canonical decision insight {insight_name} has an invalid frozen render contract")
        paragraph = doc.add_paragraph()
        prefix_run = paragraph.add_run(prefix + " ")
        prefix_run.bold = True
        paragraph.add_run(paragraph_text[len(prefix) + 1 :])
        _add_source_reference(doc, metric_key)


def _r153_strip_source_chrome(text: object) -> str:
    """Round 153 / Tier 1: strip the ``[Source: ...]`` inline-source chrome.

    ``risk_scoring`` embeds a full provenance suffix in every ``risk_factors``
    string (``format_inline_source`` -> ``[Source: CSConsole / Snowflake ...;
    Field(s): ...; Verification: ...]``).  That belongs in ``Metric_Lineage``,
    not in a Word decision cell, so remove it before the driver text is shown.
    """
    cleaned = re.sub(r"\s*\[Source:[^\]]*\]", "", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _r153_top_risk_drivers(profile: Mapping[str, Any], *, limit: int = 2) -> str:
    """Round 153 / Tier 1: the customer-specific 'why' for the decision row.

    ``risk_scoring`` already computes per-customer ``risk_factors`` (e.g. "2
    critical/high adoption barriers", "1 escalated TAC cases (P1/P2)") and
    returns them, but the decision table discarded them and rendered a
    band-level constant instead -- so two customers in the same band got
    byte-identical rows.  Surface the real drivers here.
    """
    factors = profile.get("risk_factors") or []
    rendered = [_r153_strip_source_chrome(factor) for factor in factors if _r153_strip_source_chrome(factor)]
    if not rendered:
        return "No single dominant risk driver; see component scores in Risk_Components."
    return "; ".join(rendered[: max(1, int(limit))])


def _visible_risk_decision_rows(facts: Mapping[str, Any]) -> List[List[Any]]:
    """Build high-stakes risk rows with claim-level evidence-state labels."""

    risk_state = str((facts.get("risk_summary") or {}).get("source_state") or "unavailable").strip().casefold()
    state_label = _COVERAGE_STATE_LABELS.get(
        risk_state,
        _humanize_identifier(risk_state, fallback="Unavailable"),
    )
    ranked_risks = sorted(
        (facts.get("risk_profiles") or {}).items(),
        key=lambda item: (
            -float(item[1].get("risk_score_0_100", 0.0) or 0.0),
            item[0],
        ),
    )[:5]
    rows: List[List[Any]] = []
    for customer, profile in ranked_risks:
        risk_band: Any = profile.get("risk_band", "UNKNOWN")
        risk_score: Any = profile.get("risk_score_0_100", 0.0)
        if risk_state not in {"available", "zero"}:
            risk_band = f"Unavailable ({state_label})"
            risk_score = f"Unavailable ({state_label})"
            drivers = f"Unavailable ({state_label})"
            recommendations = ["Resolve the disclosed evidence gaps before using a risk ranking or recommendation."]
        else:
            drivers = _r153_top_risk_drivers(profile)
            recommendations = profile.get("recommendations") or [
                "No evidence-backed recommendation is available; resolve the disclosed evidence gaps."
            ]
        # Round 155: prefer the deterministic, driver-specific next action over
        # the band-level boilerplate, so two same-band customers get different,
        # actionable next steps naming their actual acute signal and lever.
        # Falls back to the band recommendation when no specific action exists.
        if risk_state in {"available", "zero"}:
            action = _r153_strip_source_chrome(profile.get("next_best_action") or str(recommendations[0]))
        else:
            action = str(recommendations[0])
        rows.append(
            [
                customer,
                risk_band,
                risk_score,
                state_label,
                # Round 153 / Tier 1: the customer-specific 'why' now sits
                # beside the 'what', so two customers in the same band no
                # longer produce identical rows.
                drivers,
                action,
            ]
        )
    return rows


def _humanize_identifier(value: object, *, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if raw in _SOURCE_DISPLAY_LABELS:
        return _SOURCE_DISPLAY_LABELS[raw]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw.replace("_", " ").replace("-", " "))
    return re.sub(r"\s+", " ", words).strip().title() or fallback


def _public_warning_copy(warning: Mapping[str, Any]) -> Tuple[str, str, str]:
    """Return manager-readable warning copy without implementation details."""

    source = _humanize_identifier(warning.get("dataset"), fallback="Report data")
    kind = str(warning.get("kind") or "partial").strip().lower()
    raw_effect = re.sub(
        r"\s+",
        " ",
        str(warning.get("effect") or warning.get("error") or "").strip(),
    )
    count_match = re.search(r"\b(\d+)\b", raw_effect)
    count_prefix = f"{count_match.group(1)} " if count_match else "Some "

    if kind == "freshness_unavailable":
        return (
            "Data freshness",
            "Unavailable",
            "No completed source-retrieval timestamp is available. Any displayed retrieval-attempt clock is not proof that source data was fresh.",
        )
    if kind == "freshness_partial":
        return (
            "Data freshness",
            "Partial",
            "The retrieval clock is preserved, but one or more source fetches did not complete. Verify affected source coverage before acting.",
        )
    if kind == "tac_unmatched_after_subscription_join":
        return (
            "TAC Cases",
            "Partial assignment",
            f"{count_prefix}TAC case record(s) could not be assigned to a team member. "
            "They remain available in the Source Data File under the unassigned portfolio.",
        )
    if kind in {
        "fetch_failed",
        "source_unavailable",
        "technology_scope_unavailable",
        "timeout",
        "runtime",
    }:
        if kind == "technology_scope_unavailable":
            return (
                source,
                "Unavailable",
                "Authoritative technology evidence was unavailable for this source. "
                "Its rows were withheld from the selected technology scope and are not counted as zero.",
            )
        return (
            source,
            "Unavailable",
            "This source could not be retrieved for this run. Its metrics are shown as unavailable, not zero.",
        )
    if kind == "technology_scope_partial":
        return (
            source,
            "Partial",
            "Rows without authoritative technology evidence were excluded. Retained records are shown as a lower bound, not a complete total.",
        )
    if kind == "optional_fetch_failed":
        return (
            source,
            "Unavailable",
            "This optional source could not be retrieved. Other validated report metrics remain available.",
        )
    if kind in {"missing_input", "no_onedrive_sync"}:
        return (
            source,
            "Not supplied",
            "This source was not supplied for this run. Its metrics are shown as unavailable, not zero.",
        )
    if kind in {"tech_filter_empty_after_scope", "autodiscovered_empty_after_scope"}:
        return (
            source,
            "No scoped records",
            "No validated records from this source matched the selected report scope.",
        )
    if kind == "tech_filter_scope_excluded":
        return (
            source,
            "Outside scope",
            "Records outside the selected team-member or customer scope were excluded.",
        )
    if kind == "tech_filter_widened":
        return (
            source,
            "Broader coverage",
            "This source could not be narrowed fully to the selected scope; review its Source Data rows before acting.",
        )
    if kind == "consistency_check_failed":
        return (
            source,
            "Needs review",
            "A cross-check did not reconcile. Treat the affected metric as incomplete and review the Source Data File.",
        )
    if "fixture" in kind or (kind == "deferred" and "no live" in raw_effect.casefold()):
        return (
            "Live source validation",
            "Offline test data",
            "This report uses guarded offline test data and is not a live production result.",
        )
    if kind == "stale":
        return (
            source,
            "Stale",
            "This source is older than the report window. Its age is disclosed in the Source Data File.",
        )
    return (
        source,
        "Partial",
        "This source has a disclosed coverage limitation. Review the Source Data File before acting on its metrics.",
    )


def _prioritized_public_warnings(
    warnings: Sequence[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """Put decision-critical provenance and source failures first in Word.

    The workbook retains every warning, but the concise Word report displays
    only five.  Stable input ordering is not meaningful enough to decide which
    disclosures a manager sees.  In particular, guarded fixture provenance
    must never be pushed into the overflow by routine scope exclusions.
    """

    def priority(warning: Mapping[str, Any]) -> int:
        kind = str(warning.get("kind") or "").strip().casefold()
        dataset = str(warning.get("dataset") or warning.get("source") or "").strip().casefold()
        effect = str(
            warning.get("effect")
            or warning.get("detail")
            or warning.get("message")
            or ""
        ).strip().casefold()
        if (
            "fixture" in kind
            or dataset == "live source validation"
            or (kind == "deferred" and "no live" in effect)
        ):
            return 0
        if kind in {
            "fetch_error",
            "fetch_failed",
            "failed",
            "optional_fetch_failed",
            "source_unavailable",
            "technology_scope_unavailable",
            "timeout",
            "runtime",
        }:
            return 1
        if kind in {"partial", "stale", "technology_scope_partial"}:
            return 2
        if kind in {
            "missing_input",
            "no_onedrive_sync",
            "tech_filter_empty_after_scope",
            "autodiscovered_empty_after_scope",
        }:
            return 3
        if kind in {
            "tech_filter_scope_excluded",
            "tech_filter_widened",
            "consistency_check_failed",
        }:
            return 4
        return 5

    return [
        warning
        for _index, warning in sorted(
            enumerate(warnings),
            key=lambda item: (priority(item[1]), item[0]),
        )
    ]


def _add_partial_warning(doc: Document, warnings: Sequence[Mapping[str, Any]]) -> None:
    if not warnings:
        return
    doc.add_heading("Data Coverage Warning", level=2)
    doc.add_paragraph(
        "One or more sources were unavailable, partial, stale, filtered, or represented by offline fixtures. "
        "Metrics remain visible only where the source state supports them; unavailable data is not shown as zero."
    )

    def concise_effect(value: object, limit: int = 180) -> str:
        text = re.sub(r"\s+", " ", str(value or "See Source Data File")).strip()
        if len(text) <= limit:
            return text
        shortened = text[: max(limit - 1, 1)].rsplit(" ", 1)[0].rstrip(" ,;:")
        return (shortened or text[: max(limit - 1, 1)]).rstrip() + "…"

    _visible_warning_limit = 5
    all_warnings = _prioritized_public_warnings(warnings)
    rows = []
    for warning in all_warnings[:_visible_warning_limit]:
        source, state, effect = _public_warning_copy(warning)
        rows.append(
            [
                source,
                state,
                concise_effect(effect),
            ]
        )
    add_banded_top_n_table(doc, ["Source", "Coverage", "What this means"], rows)
    # Round 152 / C1: this table silently dropped warnings 6..N.  Every other
    # truncated section in the document discloses its overflow (member rows,
    # account rows, action plans), and this is the one section whose entire
    # purpose is honest disclosure -- so hiding its own truncation was the
    # worst place in the report to do it.  A live ACC run with R93 scope
    # exclusions plus per-source states clears five easily.  The full list is
    # already retained in the workbook's ``Report_Info`` sheet as
    # ``Partial_Data_Warning_N`` rows, so the pointer is actionable.
    _omitted_warnings = max(0, len(all_warnings) - _visible_warning_limit)
    if _omitted_warnings:
        doc.add_paragraph(
            f"{_omitted_warnings} additional coverage warning(s) are listed in the "
            "Source Data File (Report_Info sheet). "
            "[Source: Report_Info Partial_Data_Warning rows]"
        )


def build_concise_word_document(
    facts: Mapping[str, Any],
    *,
    word_budget: int = WORD_BUDGET_DEFAULT,
) -> Document:
    """Render the concise default Word artifact from a shared fact bundle."""

    doc = Document()
    # Keep the manager-facing report dense enough to avoid orphaned citation
    # pages without shrinking charts or sacrificing readable table text.  The
    # Word default leaves considerably more paragraph whitespace, which can
    # push only the final lineage note onto an otherwise blank page.
    normal_style = doc.styles["Normal"]
    normal_style.font.size = Pt(10)
    normal_style.paragraph_format.space_after = Pt(2)
    doc.core_properties.identifier = fact_contract_fingerprint(facts)
    for section in doc.sections:
        section.top_margin = Inches(0.55)
        section.bottom_margin = Inches(0.55)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)
    title = doc.add_heading(f"AdoptIQ {facts['report_type']} Decision Report", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if title.runs:
        title.runs[0].font.color.rgb = RGBColor(0x00, 0x7B, 0xC7)
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(_word_scope_subtitle(facts)).bold = True
    data_as_of_state = str(facts.get("data_as_of_state") or "unknown").strip().casefold()
    public_as_of = pd.to_datetime(facts.get("as_of_utc"), errors="coerce", utc=True)
    retrieval_attempted_at = pd.to_datetime(facts.get("retrieval_attempted_at_utc"), errors="coerce", utc=True)
    if data_as_of_state == "available" and not pd.isna(public_as_of):
        stamp_text = f"Data as of {public_as_of.strftime('%Y-%m-%d %H:%M UTC')}"
    elif not pd.isna(public_as_of):
        state_label = _COVERAGE_STATE_LABELS.get(
            data_as_of_state,
            _humanize_identifier(data_as_of_state, fallback="Unavailable"),
        )
        stamp_text = (
            f"Retrieval clock {public_as_of.strftime('%Y-%m-%d %H:%M UTC')} — source freshness {state_label.casefold()}"
        )
    elif not pd.isna(retrieval_attempted_at):
        stamp_text = (
            f"Data as of unavailable — retrieval attempted {retrieval_attempted_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    else:
        stamp_text = "Data as of unavailable — no source retrieval timestamp recorded"
    stamp = doc.add_paragraph(stamp_text)
    stamp.alignment = WD_ALIGN_PARAGRAPH.CENTER

    _add_partial_warning(doc, facts["partial_data_warnings"])

    doc.add_heading("Executive Summary", level=2)
    kpis = facts["kpis"]
    ap = facts["action_plan_lifecycle"]
    ap_state = str(ap.get("source_state") or "available")
    risk_state = str(facts["risk_summary"].get("source_state") or "available")

    executive_summary = _executive_summary_contract(facts)
    customer_state = executive_summary["customer_state"]
    show_team_member_claim = executive_summary["show_team_member_claim"] == "1"

    coverage_by_sheet = facts["source_coverage"].set_index("Source_Sheet")

    def source_state(sheet: str) -> str:
        try:
            return str(coverage_by_sheet.loc[sheet, "Source_State"])
        except Exception:  # noqa: BLE001
            return "unavailable"

    doc.add_paragraph(executive_summary["summary"])
    _add_source_reference(doc, executive_summary["source_keys"])
    doc.add_paragraph(executive_summary["purpose"])

    decision_brief = _decision_brief_contract(facts)
    surface_policy = _report_surface_policy(facts)
    brief_heading = doc.add_heading(decision_brief["heading"], level=2)
    brief_heading.paragraph_format.keep_with_next = True
    doc.add_paragraph(decision_brief["introduction"])

    risk_rows = list(decision_brief["risk_rows"])
    if risk_rows:
        risk_heading = doc.add_heading("Top Customer Decisions", level=3)
        risk_heading.paragraph_format.keep_with_next = True
        risk_table = add_banded_top_n_table(
            doc,
            ["Account", "Risk", "Why", "First move"],
            risk_rows,
        )
        _apply_word_table_geometry(risk_table, [1.25, 1.05, 2.15, 2.55])
        _add_source_reference(
            doc,
            "kpi.high_risk_customers; chart.risk_distribution.*; "
            "Evidence_Links / recommendation_account.*; Risk_Components",
        )
    else:
        doc.add_paragraph(decision_brief["risk_gap"])

    action_rows = list(decision_brief["action_rows"])
    if action_rows:
        action_heading = doc.add_heading("Immediate Action Plan Moves", level=3)
        action_heading.paragraph_format.keep_with_next = True
        if decision_brief.get("action_caveat"):
            doc.add_paragraph(str(decision_brief["action_caveat"]))
        action_table = add_banded_top_n_table(
            doc,
            [
                "Action Plan",
                "Account / next-action owner",
                "Urgency",
                "First move",
            ],
            action_rows,
        )
        for table_row, source_row in zip(
            action_table.rows[1:] if action_table is not None else [],
            _decision_brief_selected_action_plans(facts),
        ):
            _replace_cell_with_source_record_link(
                table_row.cells[0],
                source_key="action_plans",
                record_id=source_row[0],
            )
        _apply_word_table_geometry(action_table, [1.65, 1.3, 1.45, 2.6])
        _add_evidence_reference(doc, decision_brief["action_evidence_keys"])
    else:
        doc.add_paragraph(decision_brief["action_gap"])

    comprehensive_rows = _comprehensive_account_evidence_rows(facts)
    if comprehensive_rows:
        evidence_heading = doc.add_heading("Account Evidence Deep Dive", level=3)
        evidence_heading.paragraph_format.keep_with_next = True
        doc.add_paragraph(
            "This is an evidence inventory, not a risk ranking. 'At least N' is an "
            "exact retained lower bound from a partial source; stale and unavailable "
            "states are labeled directly. Complete account records remain in the "
            "Source Data File."
        )
        evidence_table = add_banded_top_n_table(
            doc,
            [
                "Account",
                "Evidence posture",
                "Open AP",
                "Overdue AP",
                "Critical/high barriers",
                "TAC",
                "Review focus",
            ],
            comprehensive_rows,
        )
        _apply_word_table_geometry(evidence_table, [1.0, 1.2, 0.7, 0.75, 0.9, 0.5, 1.95])
        _add_source_reference(
            doc,
            "summary.account.* → Account_Summary; exact rows → Action_Plans / "
            "Adoption_Barriers / TAC_Cases",
        )

    report_specific = _report_specific_decision_fact_bundle(facts)
    report_specific_rows = list(report_specific.get("rows") or [])
    if report_specific_rows:
        family = str(report_specific.get("family") or "")
        family_heading = doc.add_heading(_report_specific_heading(family), level=3)
        family_heading.paragraph_format.keep_with_next = True
        family_focus = (
            "Decision-relevant facts preserved from the legacy Renewal analysis are "
            "shown below; canonical coverage gates suppress unsupported risk claims."
            if family == "renewal"
            else (
                "Source-reported entitlement, term, status, adoption, support, and "
                "next-move facts are prioritized below in manager-friendly form."
            )
        )
        doc.add_paragraph(
            family_focus + " Complete records and exact evidence keys remain in the paired Source Data File."
        )
        family_table = add_banded_top_n_table(
            doc,
            ["Decision fact", "Reported value"],
            [[row.get("fact"), row.get("reported_value")] for row in report_specific_rows],
        )
        _apply_word_table_geometry(family_table, [4.65, 2.35])
        total_retained = int(report_specific.get("total_available") or 0)
        total_eligible = int(report_specific.get("manager_surface_eligible") or 0)
        shown = len(report_specific_rows)
        if total_retained > shown:
            eligible_copy = (
                f"Showing {shown} of {total_eligible} decision-relevant facts"
                if total_eligible > shown
                else f"Showing all {shown} decision-relevant fact(s)"
            )
            doc.add_paragraph(
                f"{eligible_copy}; all {total_retained} retained legacy-analysis facts "
                "remain in the Source Data File for audit."
            )
        _add_source_reference(
            doc,
            "exact legacy.family.* Evidence_Links keys for the facts shown above",
        )

    _add_frozen_decision_insights(doc, facts)

    doc.add_heading(str(surface_policy["kpi_heading"]), level=2)
    kpi_rows = _word_kpi_rows(facts)
    add_banded_top_n_table(doc, ["Metric", "Value", "Lineage key"], kpi_rows)
    if surface_policy["show_source_coverage"]:
        source_coverage_heading = doc.add_heading("Source Coverage", level=3)
        source_coverage_heading.paragraph_format.keep_with_next = True
        coverage_rows = []
        for _, row in facts["source_coverage"].iterrows():
            count = "Unavailable" if pd.isna(row["Record_Count"]) else int(row["Record_Count"])
            raw_state = str(row["Source_State"] or "unavailable").strip().lower()
            coverage_rows.append(
                [
                    _humanize_identifier(row["Source_Sheet"], fallback="Report data"),
                    _COVERAGE_STATE_LABELS.get(raw_state, _humanize_identifier(raw_state, fallback="Unavailable")),
                    count,
                ]
            )
        coverage_table = add_banded_top_n_table(
            doc,
            ["Source", "State", "Distinct records"],
            coverage_rows,
        )
        # The coverage table is intentionally compact and should read as one
        # scanable inventory.  Tighter cell margins keep its final row from
        # becoming an orphan above Charts and Trends in the standard Leader
        # artifact while preserving font size and the exact row contract.
        _apply_word_table_geometry(
            coverage_table,
            [2.35, 2.35, 2.3],
            cell_margin_twips=45,
        )
        _add_source_reference(doc, "Metric_Lineage and Report_Info Source_State:* rows")
    else:
        doc.add_paragraph(
            "Complete source coverage, freshness, record counts, and data-quality states remain in "
            "Report_Info and Metric_Lineage in the paired Source Data File."
        )

    decision_signal_rows = _visible_decision_signal_rows(facts)
    show_leader_verified_defects = (
        _decision_brief_family(facts) == "leader" and bool(decision_signal_rows)
    )
    if decision_signal_rows and (
        surface_policy["show_decision_signals"] or show_leader_verified_defects
    ):
        doc.add_heading(
            "Verified Defect Correlations"
            if show_leader_verified_defects
            else "Cross-Source Decision Signals",
            level=2,
        )
        doc.add_paragraph(
            (
                "Exact CSC references verified across scoped TAC/barrier evidence and "
                "external defect metadata are shown here. They do not add an independent "
                "numeric risk weight."
                if show_leader_verified_defects
                else "Supporting source detail follows the action-first brief. Every scoped source "
                "contributes a signal or an explicit no-conclusion state without inventing a "
                "new numeric risk weight."
            )
        )
        signal_table = add_banded_top_n_table(
            doc,
            [
                "Source",
                "State",
                "Scope / signal",
                "Decision implication",
            ],
            decision_signal_rows,
        )
        _apply_word_table_geometry(signal_table, [1.25, 0.85, 2.45, 2.45])
        _add_source_reference(
            doc,
            "exact decision_signal.* Evidence_Links keys for the signals shown above",
        )

    doc.add_heading("Charts and Trends", level=2)
    chart_titles = OrderedDict(
        [
            ("activity_mix", "Activity Mix by Source"),
            ("action_plan_status_aging", "Action Plan Status and Aging"),
            ("risk_distribution", "Customer Risk Distribution"),
            ("activity_trend", "Activity Trend"),
        ]
    )
    prepared_charts: "OrderedDict[str, Tuple[str, pd.DataFrame, pd.DataFrame, set[str]]]" = OrderedDict()
    for chart_id, chart_title in chart_titles.items():
        rows = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == chart_id].copy()
        available = rows.dropna(subset=["Value"])
        if chart_id == "activity_trend":
            available = available.dropna(subset=["Period_Start"])
        states = set(
            rows.get("Source_State", pd.Series(dtype="object")).fillna("unavailable").astype(str).str.casefold()
        )
        prepared_charts[chart_id] = (chart_title, rows, available, states)

    with tempfile.TemporaryDirectory(prefix="adoptiq-r142-charts-") as temp_dir:
        for chart_id, (chart_title, rows, available, states) in prepared_charts.items():
            if available.empty:
                doc.add_heading(chart_title, level=3)
                if states - {"available", "zero"}:
                    doc.add_paragraph(
                        "Chart withheld because one or more contributing sources is partial, "
                        "stale, failed, or unavailable. See Chart_Data and Report_Info for "
                        "coverage details and retained source rows."
                    )
                else:
                    doc.add_paragraph(
                        "Chart unavailable because this scope has no validated, dateable source series. "
                        "See Chart_Data and Report_Info for coverage details."
                    )
                if not rows.empty:
                    _add_source_reference(
                        doc,
                        "; ".join(_chart_lineage_family_tokens([chart_id])),
                    )
                continue
            target = Path(temp_dir) / f"{chart_id}.png"
            if _render_chart_image(chart_id, available, target):
                doc.add_heading(chart_title, level=3)
                shape = doc.add_picture(str(target), width=Inches(6.7))
                _set_picture_alt_text(
                    shape,
                    chart_title,
                    f"{chart_title}. Values are listed in the companion Source Data File Chart_Data sheet.",
                )
                _add_source_reference(
                    doc,
                    "; ".join(_chart_lineage_family_tokens([chart_id])),
                )
            else:
                raise ValueError(
                    f"Expected populated chart {chart_id!r} could not be rendered; "
                    "the report was not published. Validated series remain in "
                    "the Source Data File Chart_Data sheet."
                )

    if surface_policy["show_lifecycle_rollup"]:
        doc.add_heading("Prioritized Action Plan Rollup", level=2)
        doc.add_paragraph(facts["ranking_criteria"])
    status_rows = [
        ["Open", ap["open"]],
        ["Overdue", ap["overdue"]],
        ["Due Soon", ap["due_soon"]],
        ["Completed", ap["completed"]],
        ["Blocked / On Hold", ap["blocked_on_hold"]],
        ["Status unresolved", ap["unknown"]],
    ]
    if surface_policy["show_lifecycle_rollup"]:
        if ap_state not in {"available", "zero"}:
            doc.add_paragraph(
                "Action Plan lifecycle rollup is withheld because the source is incomplete. "
                "Known retained moves remain in the Decision Brief and Source Data File; "
                "see Report_Info for coverage details."
            )
        else:
            add_banded_top_n_table(doc, ["Lifecycle", "Distinct plans"], status_rows)
            doc.add_paragraph(
                "Unresolved-plan age uses the selected created/open date and the report's "
                "explicit evaluation as-of date. Completed plans are excluded."
            )
            add_banded_top_n_table(
                doc,
                ["Unresolved-plan age", "Distinct plans"],
                [
                    [
                        _action_plan_chart_category_display(
                            band,
                            age_series=True,
                        ),
                        ap["age_band_counts"][band],
                    ]
                    for band in cm.ACTION_PLAN_AGE_BAND_ORDER
                ],
            )
    detailed_action_rows = _detailed_action_rollup_rows(
        facts,
        decision_brief,
    )
    if detailed_action_rows:
        # Adjacent Word tables can be interpreted as one table by compatible
        # renderers, causing the lifecycle header to repeat above selected
        # Action Plan rows on the next page.  A one-point separator preserves
        # the two independent table contracts without adding visible clutter.
        table_separator = doc.add_paragraph()
        table_separator.paragraph_format.space_before = Pt(0)
        table_separator.paragraph_format.space_after = Pt(0)
        table_separator.paragraph_format.line_spacing = Pt(1)
        compact_action_rows = [
            [row[0], row[1], row[2], row[4], row[5], row[6], row[8]] for row in detailed_action_rows
        ]
        action_rollup_table = add_banded_top_n_table(
            doc,
            ["Record ID", "Account", "Owner", "Status", "Due", "Age (days)", "Priority"],
            compact_action_rows,
        )
        _apply_word_table_geometry(
            action_rollup_table,
            [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            cell_margin_twips=45,
        )
        for table_row, source_row in zip(
            action_rollup_table.rows[1:] if action_rollup_table is not None else [],
            detailed_action_rows,
        ):
            _replace_cell_with_source_record_link(
                table_row.cells[0],
                source_key="action_plans",
                record_id=source_row[0],
            )
        detail_heading = doc.add_paragraph("Selected-plan detail (title and next action):")
        detail_heading.paragraph_format.keep_with_next = True
        for row in detailed_action_rows:
            detail = doc.add_paragraph(style="List Number")
            detail.add_run(f"{row[0]} — {row[3]}. ").bold = True
            detail.add_run(f"Next action: {row[7]}")
        full_plan_count = int((facts.get("action_plan_lifecycle") or {}).get("total") or 0)
        if full_plan_count > len(detailed_action_rows):
            doc.add_paragraph(
                "This Word view intentionally limits detailed rows to the highest-priority "
                "plans; the complete selected-scope Action Plan set remains in the paired "
                "Source Data File (Action_Plans). "
                + _artifact_reference_text("Metric_Lineage", "kpi.action_plans_total")
            )
    elif surface_policy["show_lifecycle_rollup"] and facts["top_action_plans"]:
        doc.add_paragraph(
            "Every known retained Action Plan move is already shown in the Decision "
            "Brief above. The complete row set remains in the Source Data File; the "
            "partial source state means this is not a complete portfolio ranking."
        )
    elif surface_policy["show_lifecycle_rollup"] and ap_state in {"available", "zero"}:
        doc.add_paragraph("No open, blocked, due-soon, overdue, or source-status-unresolved Action Plans were found.")
    if surface_policy["show_lifecycle_rollup"] and (
        ap["missing_title"] or ap["missing_record_id"] or ap["unknown"] or ap["unknown_age"]
    ):
        doc.add_paragraph(
            f"Data quality: {ap['missing_title']} plan(s) use “Title unavailable”; "
            f"{ap['missing_record_id']} plan(s) lack a stable source ID; "
            f"{ap['unknown']} plan(s) have a missing or unmapped source status; "
            f"{ap['unknown_age']} unresolved plan(s) lack a usable created/open date. "
            "Exact raw values and row-level reasons remain in Action_Plans → "
            "AdoptIQ_Data_Quality."
        )
    if surface_policy["show_lifecycle_rollup"]:
        _add_source_reference(
            doc,
            "kpi.action_plans_*; chart.action_plan_status.*; chart.action_plan_age.*",
        )

    heading = (
        "Owner Follow-Through"
        if _decision_brief_family(facts) == "leader" and facts["scope_type"] == "team"
        else ("Top Team-Member Summary" if facts["scope_type"] == "team" else "Top Account Summary")
    )
    doc.add_heading(heading, level=2)
    if facts["scope_type"] == "team":
        doc.add_paragraph(
            "Ranked by overdue Action Plans, then open Action Plans, barriers, TAC volume, and team-member name. "
            "'At least N' is an exact retained lower bound from a partial source."
        )
        if facts["member_summary"]:
            leader_rows = _leader_intervention_rows(facts)
            member_rows = leader_rows or [
                [
                    row[0],
                    _coverage_aware_compact_display(row[1], customer_state),
                    _coverage_aware_compact_display(row[2], ap_state),
                    _coverage_aware_compact_display(row[3], ap_state),
                    _coverage_aware_compact_display(row[4], source_state("Adoption_Barriers")),
                    _coverage_aware_compact_display(row[5], source_state("TAC_Cases")),
                ]
                for row in facts["member_summary"]
            ]
            member_headers = ["Team member", "Customers", "Open AP", "Overdue AP", "Barriers", "TAC"]
            member_widths = None
            if leader_rows:
                member_headers.append("Manager intervention")
                # Keep short metric headers intact while preserving most of
                # the row for the intervention narrative.  The previous
                # 0.65-inch Customers cell wrapped mid-word in the rendered
                # Leader report ("Custome" / "rs").
                member_widths = [1.0, 0.85, 0.65, 0.75, 0.65, 0.5, 2.6]
            member_table = add_banded_top_n_table(doc, member_headers, member_rows)
            if member_widths:
                _apply_word_table_geometry(member_table, member_widths)
            # Round 147 evidence contract: the lineage reference must be the
            # element immediately following the member table.  Keep it there.
            _add_source_reference(
                doc,
                "summary.member.* → Member_Summary",
            )
            # Round 152 / C2: member rows legitimately do not sum to the team
            # totals -- a record shared by two team members is attributed to
            # both (DSM secondary attribution), while the team total counts
            # each record once.  That is the intended design, but nothing in
            # the Word report or the workbook said so, so a manager who added
            # the column found it disagreeing with the headline and had no way
            # to tell a design choice from a bug.  Measured on the offline
            # fixture: team open/overdue 4/2 vs member sum 5/3.
            if _has_shared_team_attribution(facts):
                doc.add_paragraph(
                    "A record shared by more than one team member is attributed to each of them, so "
                    "member rows can add up to more than the team total. The team total counts each "
                    "record once."
                )
        else:
            doc.add_paragraph("No portfolio records could be attributed to a named team member for this scope.")
        if facts.get("member_summary_omitted"):
            doc.add_paragraph(
                f"{facts['member_summary_omitted']} additional team-member row(s) are in the Source Data File."
            )
        if facts.get("unassigned_portfolio_summary"):
            doc.add_paragraph(
                "Records without a verified named owner are excluded from this ranking and retained as "
                "Unassigned / Portfolio in the Source Data File."
            )
    else:
        # Round 152 / C4: when no account resolved to a canonical identity the
        # renderer used to print the ranking claim followed by a header-only
        # grid with zero data rows, while the very next section printed the
        # correct empty-state sentence.  The team branch above has always had
        # an ``else`` for this; the account branch did not.  Mirror it.
        if not facts.get("account_summary"):
            doc.add_paragraph("No account could be resolved to a canonical customer identity for this scope.")
        else:
            doc.add_paragraph(
                "Ranked by canonical risk score descending, then account name. "
                "'At least N' is an exact retained lower bound from a partial source."
            )
            account_rows = []
            for row in facts["account_summary"]:
                risk_band = row[1]
                if risk_state not in {"available", "zero"}:
                    label = _COVERAGE_STATE_LABELS.get(risk_state, risk_state.title())
                    risk_band = f"Unavailable ({label})"
                account_rows.append(
                    [
                        row[0],
                        risk_band,
                        _coverage_aware_scalar_display(row[2], risk_state),
                        _coverage_aware_compact_display(row[3], ap_state),
                        _coverage_aware_compact_display(row[4], ap_state),
                        _coverage_aware_compact_display(
                            row[5], source_state("Adoption_Barriers")
                        ),
                        _coverage_aware_compact_display(row[6], source_state("TAC_Cases")),
                    ]
                )
            add_banded_top_n_table(
                doc,
                ["Account", "Risk band", "Risk score", "Open AP", "Overdue AP", "Critical/high barriers", "TAC"],
                account_rows,
            )
        _add_source_reference(
            doc,
            "summary.account.* → Account_Summary; risk.* → Risk_Components",
        )
        if facts.get("account_summary_omitted"):
            doc.add_paragraph(
                f"{facts['account_summary_omitted']} additional account row(s) are in the Source Data File."
            )
    lineage_note = doc.add_paragraph()
    lineage_note.paragraph_format.space_before = Pt(0)
    lineage_note.paragraph_format.space_after = Pt(0)
    lineage_note.paragraph_format.keep_together = True
    lineage_label = lineage_note.add_run("Source and Lineage Note — ")
    lineage_label.bold = True
    lineage_label.font.color.rgb = RGBColor(0x00, 0x7B, 0xC7)
    lineage_label.font.size = Pt(8.5)
    lineage_detail = lineage_note.add_run(
        "Complete scoped records and Metric_Lineage are in the paired Source Data File."
    )
    lineage_detail.font.size = Pt(8.5)

    result = validate_word_content(doc, word_budget=word_budget)
    if result["errors"]:
        raise ValueError("Concise Word contract failed: " + "; ".join(result["errors"]))
    return doc


def document_word_count(doc: Document) -> int:
    """Count visible paragraph and table-cell words in a Word document."""

    text_parts = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text_parts.append(cell.text)
    return sum(len(re.findall(r"\b\w+[\w'-]*\b", text or "")) for text in text_parts)


def validate_word_content(doc: Document, *, word_budget: int = WORD_BUDGET_DEFAULT) -> Dict[str, Any]:
    """Validate content budget and exclusion of raw-record appendix headings."""

    word_count = document_word_count(doc)
    headings = {
        paragraph.text.strip().lower()
        for paragraph in doc.paragraphs
        if str(getattr(paragraph.style, "name", "")).lower().startswith("heading")
    }
    forbidden = sorted(headings & FORBIDDEN_RAW_WORD_HEADINGS)
    errors: List[str] = []
    if word_count > int(word_budget):
        errors.append(f"word budget exceeded: {word_count} > {word_budget}")
    if forbidden:
        errors.append("forbidden raw-record headings present: " + ", ".join(forbidden))
    return {
        "word_count": word_count,
        "word_budget": int(word_budget),
        "forbidden_headings": forbidden,
        "inline_shapes": len(doc.inline_shapes),
        "errors": errors,
    }


def _word_cell_text(value: Any) -> str:
    return "" if value is None else str(value)


def _expected_visible_word_tables(
    facts: Mapping[str, Any],
) -> Dict[Tuple[str, ...], List[List[str]]]:
    """Return the exact visible table cells expected from canonical facts."""

    expected: Dict[Tuple[str, ...], List[List[Any]]] = {}
    warnings = _prioritized_public_warnings(
        list(facts.get("partial_data_warnings") or [])
    )[:5]
    if warnings:
        warning_rows: List[List[Any]] = []
        for warning in warnings:
            source, state, effect = _public_warning_copy(warning)
            effect_text = re.sub(r"\s+", " ", str(effect or "See Source Data File")).strip()
            if len(effect_text) > 180:
                shortened = effect_text[:179].rsplit(" ", 1)[0].rstrip(" ,;:")
                effect_text = (shortened or effect_text[:179]).rstrip() + "…"
            warning_rows.append([source, state, effect_text])
        expected[("Source", "Coverage", "What this means")] = warning_rows

    kpis = facts["kpis"]
    lifecycle = facts["action_plan_lifecycle"]
    lifecycle_state = str(lifecycle.get("source_state") or "available")
    risk_state = str(facts["risk_summary"].get("source_state") or "available")
    coverage_by_sheet = facts["source_coverage"].set_index("Source_Sheet")

    def source_state(sheet_name: str) -> str:
        try:
            return str(coverage_by_sheet.loc[sheet_name, "Source_State"])
        except Exception:  # noqa: BLE001
            return "unavailable"

    customer_states = [
        source_state(sheet_name)
        for sheet_name in (
            "Subscriptions",
            "Action_Plans",
            "Adoption_Barriers",
            "Customer_Pulse",
            "TAC_Cases",
            "Success_Priorities",
        )
    ]
    if any(state in {"failed", "unavailable", "partial", "stale"} for state in customer_states):
        customer_state = (
            "unavailable" if all(state in {"failed", "unavailable"} for state in customer_states) else "partial"
        )
    else:
        customer_state = "available"

    surface_policy = _report_surface_policy(facts)
    expected[("Metric", "Value", "Lineage key")] = _word_kpi_rows(facts)

    coverage_rows: List[List[Any]] = []
    for _, row in facts["source_coverage"].iterrows():
        raw_state = str(row["Source_State"] or "unavailable").strip().lower()
        coverage_rows.append(
            [
                _humanize_identifier(row["Source_Sheet"], fallback="Report data"),
                _COVERAGE_STATE_LABELS.get(
                    raw_state,
                    _humanize_identifier(raw_state, fallback="Unavailable"),
                ),
                "Unavailable" if pd.isna(row["Record_Count"]) else int(row["Record_Count"]),
            ]
        )
    if surface_policy["show_source_coverage"]:
        expected[("Source", "State", "Distinct records")] = coverage_rows

    decision_signal_rows = _visible_decision_signal_rows(facts)
    if decision_signal_rows and (
        surface_policy["show_decision_signals"]
        or _decision_brief_family(facts) == "leader"
    ):
        expected[
            (
                "Source",
                "State",
                "Scope / signal",
                "Decision implication",
            )
        ] = decision_signal_rows

    comprehensive_rows = _comprehensive_account_evidence_rows(facts)
    if comprehensive_rows:
        expected[
            (
                "Account",
                "Evidence posture",
                "Open AP",
                "Overdue AP",
                "Critical/high barriers",
                "TAC",
                "Review focus",
            )
        ] = comprehensive_rows

    report_specific = _report_specific_decision_fact_bundle(facts)
    report_specific_rows = list(report_specific.get("rows") or [])
    if report_specific_rows:
        expected[
            (
                "Decision fact",
                "Reported value",
            )
        ] = [
            [
                row.get("fact"),
                row.get("reported_value"),
            ]
            for row in report_specific_rows
        ]

    decision_brief = _decision_brief_contract(facts)
    if decision_brief["risk_rows"]:
        expected[("Account", "Risk", "Why", "First move")] = list(decision_brief["risk_rows"])
    if decision_brief["action_rows"]:
        expected[
            (
                "Action Plan",
                "Account / next-action owner",
                "Urgency",
                "First move",
            )
        ] = list(decision_brief["action_rows"])

    if surface_policy["show_lifecycle_rollup"] and lifecycle_state in {"available", "zero"}:
        expected[("Lifecycle", "Distinct plans")] = [
            ["Open", lifecycle["open"]],
            ["Overdue", lifecycle["overdue"]],
            ["Due Soon", lifecycle["due_soon"]],
            ["Completed", lifecycle["completed"]],
            ["Blocked / On Hold", lifecycle["blocked_on_hold"]],
            ["Status unresolved", lifecycle["unknown"]],
        ]
        expected[("Unresolved-plan age", "Distinct plans")] = [
            [
                _action_plan_chart_category_display(band, age_series=True),
                lifecycle["age_band_counts"][band],
            ]
            for band in cm.ACTION_PLAN_AGE_BAND_ORDER
        ]
    decision_brief = _decision_brief_contract(facts)
    detailed_action_rows = _detailed_action_rollup_rows(facts, decision_brief)
    if detailed_action_rows:
        expected[
            (
                "Record ID",
                "Account",
                "Owner",
                "Status",
                "Due",
                "Age (days)",
                "Priority",
            )
        ] = [[row[index] for index in (0, 1, 2, 4, 5, 6, 8)] for row in detailed_action_rows]

    if str(facts.get("scope_type") or "").casefold() == "team":
        if facts.get("member_summary"):
            leader_rows = _leader_intervention_rows(facts)
            if leader_rows:
                expected[
                    (
                        "Team member",
                        "Customers",
                        "Open AP",
                        "Overdue AP",
                        "Barriers",
                        "TAC",
                        "Manager intervention",
                    )
                ] = leader_rows
            else:
                expected[
                    (
                        "Team member",
                        "Customers",
                        "Open AP",
                        "Overdue AP",
                        "Barriers",
                        "TAC",
                    )
                ] = [
                    [
                        row[0],
                        _coverage_aware_compact_display(row[1], customer_state),
                        _coverage_aware_compact_display(row[2], lifecycle_state),
                        _coverage_aware_compact_display(row[3], lifecycle_state),
                        _coverage_aware_compact_display(
                            row[4], source_state("Adoption_Barriers")
                        ),
                        _coverage_aware_compact_display(row[5], source_state("TAC_Cases")),
                    ]
                    for row in facts["member_summary"]
                ]
    elif facts.get("account_summary"):
        # Round 152 / C4: an empty account summary now renders an
        # empty-state sentence instead of a header-only grid, so the
        # contract must stop expecting that table when there are no rows.
        account_rows: List[List[Any]] = []
        for row in facts.get("account_summary") or []:
            risk_band = row[1]
            if risk_state not in {"available", "zero"}:
                label = _COVERAGE_STATE_LABELS.get(risk_state, risk_state.title())
                risk_band = f"Unavailable ({label})"
            account_rows.append(
                [
                    row[0],
                    risk_band,
                    _coverage_aware_scalar_display(row[2], risk_state),
                    _coverage_aware_compact_display(row[3], lifecycle_state),
                    _coverage_aware_compact_display(row[4], lifecycle_state),
                    _coverage_aware_compact_display(
                        row[5], source_state("Adoption_Barriers")
                    ),
                    _coverage_aware_compact_display(row[6], source_state("TAC_Cases")),
                ]
            )
        expected[
            (
                "Account",
                "Risk band",
                "Risk score",
                "Open AP",
                "Overdue AP",
                "Critical/high barriers",
                "TAC",
            )
        ] = account_rows

    return {header: [[_word_cell_text(value) for value in row] for row in rows] for header, rows in expected.items()}


def validate_word_semantics(
    facts: Mapping[str, Any],
    doc: Document,
) -> Dict[str, Any]:
    """Reconcile visible decision tables with their canonical fact rows.

    The document fingerprint proves artifact identity, but it cannot detect a
    renderer that places the wrong owner or account value into an otherwise
    correctly identified document. This pass re-reads the visible table cells
    and selected-plan detail paragraphs immediately before publication.
    """

    errors: List[str] = []
    tables: Dict[Tuple[str, ...], List[List[str]]] = {}
    for table in doc.tables:
        if not table.rows:
            continue
        header = tuple(cell.text.strip() for cell in table.rows[0].cells)
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows[1:]]
        if header in tables:
            errors.append("Word contains duplicate decision table header: " + " | ".join(header))
            continue
        tables[header] = rows

    expected_tables = _expected_visible_word_tables(facts)
    for header, expected_rows in expected_tables.items():
        actual_rows = tables.get(header)
        label = " | ".join(header)
        if actual_rows is None:
            errors.append(f"Word is missing canonical table: {label}")
        elif actual_rows != expected_rows:
            errors.append(f"Word visible cells differ from canonical facts: {label}")
    unexpected_headers = sorted(set(tables) - set(expected_tables))
    for header in unexpected_headers:
        errors.append("Word contains a table outside the canonical visible contract: " + " | ".join(header))

    report_specific = _report_specific_decision_fact_bundle(facts)
    report_specific_rows = list(report_specific.get("rows") or [])
    family = str(report_specific.get("family") or "")
    expected_report_specific_heading = _report_specific_heading(family)
    report_specific_heading_count = sum(
        paragraph.text.strip() == expected_report_specific_heading
        for paragraph in doc.paragraphs
        if str(getattr(paragraph.style, "name", "")).casefold().startswith("heading")
    )
    if report_specific_rows and report_specific_heading_count != 1:
        errors.append(f"Word must contain exactly one {expected_report_specific_heading} heading")
    elif not report_specific_rows and report_specific_heading_count:
        errors.append("Word contains a report-specific facts heading without canonical rows")
    lineage = facts.get("metric_lineage")
    lineage_keys = (
        set(lineage.get("Metric_Key", pd.Series(dtype=str)).dropna().astype(str))
        if isinstance(lineage, pd.DataFrame)
        else set()
    )
    expected_prefix = f"legacy.family.{family}." if family else ""
    for item in report_specific_rows:
        evidence_key = str(item.get("evidence_key") or "")
        if not expected_prefix or not evidence_key.startswith(expected_prefix):
            errors.append("Word report-specific fact is not bound to its exact report family")
        if evidence_key not in lineage_keys:
            errors.append(f"Word report-specific fact lacks Metric_Lineage: {evidence_key}")

    expected_scope_subtitle = _word_scope_subtitle(facts)
    actual_scope_subtitle = doc.paragraphs[1].text.strip() if len(doc.paragraphs) > 1 else ""
    if actual_scope_subtitle != expected_scope_subtitle:
        errors.append("Word visible scope subtitle differs from canonical facts")

    visible_paragraphs = [paragraph.text.strip() for paragraph in doc.paragraphs]
    executive_summary = _executive_summary_contract(facts)
    executive_heading_positions = [
        index
        for index, paragraph in enumerate(doc.paragraphs)
        if paragraph.text.strip() == "Executive Summary"
        and str(getattr(paragraph.style, "name", "")).casefold().startswith("heading")
    ]
    executive_summary_validated = False
    if len(executive_heading_positions) != 1:
        errors.append("Word must contain exactly one Executive Summary heading")
    else:
        heading_position = executive_heading_positions[0]
        expected_executive_sequence = [
            executive_summary["summary"],
            executive_summary["summary_source"],
            executive_summary["purpose"],
        ]
        actual_executive_sequence = visible_paragraphs[heading_position + 1 : heading_position + 4]
        if actual_executive_sequence != expected_executive_sequence:
            errors.append("Word Executive Summary text, citation, or adjacency differs from canonical facts")
        elif all(visible_paragraphs.count(text) == 1 for text in expected_executive_sequence):
            executive_summary_validated = True
        else:
            errors.append("Word Executive Summary text or citation is duplicated outside its canonical section")

    decision_brief = _decision_brief_contract(facts)
    decision_brief_heading_positions = [
        index
        for index, paragraph in enumerate(doc.paragraphs)
        if paragraph.text.strip() == decision_brief["heading"]
        and str(getattr(paragraph.style, "name", "")).casefold().startswith("heading")
    ]
    decision_brief_validated = False
    if len(decision_brief_heading_positions) != 1:
        errors.append("Word must contain exactly one family-aware canonical Decision Brief heading")
    else:
        brief_position = decision_brief_heading_positions[0]
        if (
            brief_position + 1 >= len(visible_paragraphs)
            or visible_paragraphs[brief_position + 1] != decision_brief["introduction"]
            or visible_paragraphs.count(decision_brief["introduction"]) != 1
        ):
            errors.append("Word Decision Brief introduction or adjacency differs from canonical facts")
        else:
            decision_brief_validated = True
    for rows_key, gap_key, label in (
        ("risk_rows", "risk_gap", "risk"),
        ("action_rows", "action_gap", "Action Plan"),
    ):
        gap_text = str(decision_brief[gap_key] or "")
        gap_count = visible_paragraphs.count(gap_text) if gap_text else 0
        if decision_brief[rows_key] and gap_count:
            errors.append(f"Word Decision Brief exposes a {label} gap beside canonical rows")
            decision_brief_validated = False
        elif not decision_brief[rows_key] and gap_count != 1:
            errors.append(f"Word Decision Brief must contain exactly one canonical {label} gap")
            decision_brief_validated = False

    action_reference = str(decision_brief.get("action_source_reference") or "")
    action_reference_count = visible_paragraphs.count(action_reference) if action_reference else 0
    if decision_brief["action_rows"] and action_reference_count != 1:
        errors.append(
            "Word Immediate Action Plan moves must cite their exact selected recommendation Evidence_Links keys"
        )
        decision_brief_validated = False
    elif not decision_brief["action_rows"] and action_reference_count:
        errors.append("Word cites Action Plan recommendation evidence without rendered moves")
        decision_brief_validated = False

    chart_references_validated = 0
    for reference_group in _expected_chart_reference_groups(facts):
        expected_reference = _artifact_reference_text("Metric_Lineage", reference_group)
        if visible_paragraphs.count(expected_reference) != 1:
            errors.append(
                "Word chart citation is missing, duplicated, or names a noncanonical "
                "chart evidence family: " + "; ".join(reference_group)
            )
            continue
        unresolved = [token for token in reference_group if not _keys_matching_reference(token, lineage_keys)]
        if unresolved:
            errors.append("Word chart citation lacks canonical Metric_Lineage matches: " + ", ".join(unresolved))
            continue
        chart_references_validated += 1

    decision_insights = facts.get("decision_insights") or {}
    validated_insight_count = 0
    if not isinstance(decision_insights, Mapping):
        errors.append("canonical decision_insights must be a mapping")
        decision_insights = {}
    unexpected_insights = sorted(set(decision_insights) - set(_DECISION_INSIGHT_ORDER))
    if unexpected_insights:
        errors.append("canonical decision_insights contains unsupported keys: " + ", ".join(unexpected_insights))
    rendered_insight_names = set(_rendered_decision_insight_names(facts))
    for insight_name in _DECISION_INSIGHT_ORDER:
        prefix = _DECISION_INSIGHT_PREFIXES[insight_name]
        matching_positions = [index for index, text in enumerate(visible_paragraphs) if text.startswith(prefix)]
        insight = decision_insights.get(insight_name)
        if not insight:
            if matching_positions:
                errors.append(f"Word contains {insight_name} without a canonical frozen insight")
            continue
        if insight_name not in rendered_insight_names:
            if matching_positions:
                errors.append(f"Word contains policy-suppressed {insight_name}")
            continue
        if not isinstance(insight, Mapping):
            errors.append(f"canonical decision insight {insight_name} must be a mapping")
            continue
        expected_text = _clean_token(insight.get("paragraph_text"))
        expected_prefix = _clean_token(insight.get("paragraph_prefix"))
        metric_key = _clean_token(insight.get("metric_key"))
        insight_clock = pd.to_datetime(insight.get("evaluation_as_of_utc"), errors="coerce", utc=True)
        fact_clock = pd.to_datetime(facts.get("evaluation_as_of_utc"), errors="coerce", utc=True)
        if expected_prefix != prefix or not expected_text.startswith(prefix + " "):
            errors.append(f"canonical decision insight {insight_name} has an invalid frozen paragraph")
        if pd.isna(insight_clock) or pd.isna(fact_clock) or insight_clock != fact_clock:
            errors.append(f"canonical decision insight {insight_name} is not anchored to the evaluation clock")
        lineage_rows = (
            lineage.loc[lineage.get("Metric_Key", pd.Series(dtype=str)).fillna("").astype(str) == metric_key]
            if isinstance(lineage, pd.DataFrame) and metric_key
            else pd.DataFrame()
        )
        if not metric_key or len(lineage_rows) != 1:
            errors.append(f"Word decision insight {insight_name} lacks Metric_Lineage: {metric_key or '<blank>'}")
        elif _clean_token(lineage_rows.iloc[0].get("Metric_Value")) != expected_text:
            errors.append(f"Word decision insight {insight_name} differs from Metric_Lineage value")
        if len(matching_positions) != 1:
            errors.append(f"Word must contain exactly one canonical {insight_name} paragraph")
            continue
        paragraph_position = matching_positions[0]
        if visible_paragraphs[paragraph_position] != expected_text:
            errors.append(f"Word {insight_name} paragraph differs from canonical frozen facts")
        expected_reference = f"[Source: Source Data File → Metric_Lineage / {metric_key}]"
        reference_positions = [index for index, text in enumerate(visible_paragraphs) if text == expected_reference]
        if reference_positions != [paragraph_position + 1]:
            errors.append(f"Word {insight_name} citation is missing, duplicated, or not adjacent")
        if (
            visible_paragraphs[paragraph_position] == expected_text
            and reference_positions == [paragraph_position + 1]
            and metric_key in lineage_keys
        ):
            validated_insight_count += 1

    paragraph_text = set(visible_paragraphs)
    detailed_action_rows = _detailed_action_rollup_rows(facts, decision_brief)
    if detailed_action_rows:
        for row in detailed_action_rows:
            expected_detail = f"{row[0]} — {row[3]}. Next action: {row[7]}"
            if expected_detail not in paragraph_text:
                errors.append(f"Word selected Action Plan detail differs for {row[0]}")

    expected_record_link_counts: Dict[str, int] = {}
    selected_brief_ids = {_clean_token(row[0]) for row in _decision_brief_selected_action_plans(facts) if row}
    detailed_action_ids = {
        _clean_token(row[0]) for row in detailed_action_rows if row
    }
    for row in facts.get("top_action_plans") or []:
        record_id = _clean_token(row[0] if row else "")
        url = build_source_record_url("action_plans", record_id)
        if not url:
            continue
        expected_count = int(record_id in selected_brief_ids)
        if record_id in detailed_action_ids:
            expected_count += 1
        if expected_count:
            expected_record_link_counts[url] = expected_count

    actual_record_link_counts: Dict[str, int] = {}
    try:
        from docx.oxml.ns import qn  # noqa: PLC0415

        for table in doc.tables:
            for table_row in table.rows:
                for cell in table_row.cells:
                    for hyperlink in cell._tc.xpath(".//w:hyperlink"):
                        relationship_id = hyperlink.get(qn("r:id"))
                        relationship = cell.part.rels.get(relationship_id)
                        target = _clean_token(getattr(relationship, "target_ref", ""))
                        if "ciscosales.lightning.force.com" not in target.casefold():
                            continue
                        if not is_allowed_source_record_url(target):
                            errors.append("Word contains a disallowed CSConsole hyperlink target")
                            continue
                        actual_record_link_counts[target] = actual_record_link_counts.get(target, 0) + 1
    except Exception:  # noqa: BLE001
        errors.append("Word source-record hyperlink relationships could not be validated")
    if actual_record_link_counts != expected_record_link_counts:
        errors.append("Word CSConsole source-record hyperlinks differ from selected canonical Action Plan rows")

    return {
        "ok": not errors,
        "errors": errors,
        "table_count": len(doc.tables),
        "validated_table_count": len(expected_tables),
        "selected_action_count": len(facts.get("top_action_plans") or []),
        "report_specific_fact_count": len(report_specific_rows),
        "report_specific_heading_validated": (report_specific_heading_count == (1 if report_specific_rows else 0)),
        "risk_decision_count": len(facts.get("risk_profiles") or {}),
        "scope_subtitle_validated": actual_scope_subtitle == expected_scope_subtitle,
        "executive_summary_validated": executive_summary_validated,
        "decision_brief_validated": decision_brief_validated,
        "action_reference_validated": (action_reference_count == (1 if decision_brief["action_rows"] else 0)),
        "chart_reference_group_count": len(_expected_chart_reference_groups(facts)),
        "validated_chart_reference_group_count": chart_references_validated,
        "decision_insight_count": len(rendered_insight_names),
        "validated_decision_insight_count": validated_insight_count,
        "source_record_hyperlink_count": sum(actual_record_link_counts.values()),
    }


def validate_cross_artifact_contract(
    facts: Mapping[str, Any],
    sheets: Mapping[str, pd.DataFrame],
    doc: Optional[Document] = None,
    *,
    word_budget: int = WORD_BUDGET_DEFAULT,
) -> Dict[str, Any]:
    """Prove Word/Source Data/chart facts reconcile before artifacts ship."""

    errors: List[str] = []
    required = {"Report_Info", *_SOURCE_CONTRACT_SHEETS}
    missing = sorted(required - set(sheets))
    if missing:
        errors.append("missing Source Data sheets: " + ", ".join(missing))
    completeness = audit_source_data_frames(sheets)
    errors.extend(completeness["errors"])

    # Round 153 / Tier 4: the customer universe must never split one Round 132
    # alias group into two identities.  This is the durable half of the fix --
    # it converts the recurring "same org counted twice" drift into a
    # publication-blocking error, the same way Round 152's default-deny
    # endpoint check did.  It can only fire when the registry actually groups a
    # name in scope, so it is inert for non-registered customers.
    _frames = facts.get("frames")
    if isinstance(_frames, Mapping):
        errors.extend(_r153_detect_split_alias_groups(_canonical_customer_identities(_frames)))

    expected_digests = _source_contract_digests(facts)
    for sheet_name, expected_digest in expected_digests.items():
        actual_digest = _frame_content_digest(
            sheets.get(sheet_name, pd.DataFrame()),
            sheet_name=sheet_name,
        )
        if actual_digest != expected_digest:
            errors.append(f"{sheet_name} content digest differs from canonical source rows")
    expected_fingerprint = fact_contract_fingerprint(facts, source_digests=expected_digests)
    report_info = sheets.get("Report_Info", pd.DataFrame())
    expected_report_info = build_source_data_sheets(
        facts,
        _skip_contract_fingerprint=True,
    )["Report_Info"]
    if not isinstance(report_info, pd.DataFrame) or _report_info_semantic_digest(
        report_info,
    ) != _report_info_semantic_digest(expected_report_info):
        errors.append("Report_Info scope, provenance, warning, or source-state content differs from canonical facts")
    fingerprint_rows = (
        report_info.loc[report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str) == "Fact_Contract_SHA256"]
        if isinstance(report_info, pd.DataFrame)
        else pd.DataFrame()
    )
    if len(fingerprint_rows) != 1:
        errors.append("Report_Info must contain exactly one Fact_Contract_SHA256 row")
    elif str(fingerprint_rows.iloc[0].get("Value") or "") != expected_fingerprint:
        errors.append("Source Data fact fingerprint differs from canonical facts")
    for sheet_name, expected_digest in expected_digests.items():
        digest_item = f"Sheet_SHA256:{sheet_name}"
        digest_rows = report_info.loc[
            report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str) == digest_item
        ]
        if len(digest_rows) != 1:
            errors.append(f"Report_Info must contain exactly one {digest_item} row")
        elif str(digest_rows.iloc[0].get("Value") or "") != expected_digest:
            errors.append(f"Report_Info digest differs for {sheet_name}")

    ap = facts["action_plan_lifecycle"]
    if sum(ap["bucket_counts"].values()) != ap["total"]:
        errors.append("Action Plan lifecycle buckets do not partition the distinct total")
    if sum(ap["age_band_counts"].values()) != ap["unresolved_total"]:
        errors.append("Action Plan age bands do not partition the unresolved-plan total")
    ap_chart = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == "action_plan_status_aging"]
    ap_chart_complete = set(ap_chart["Source_State"].fillna("unavailable").astype(str).str.casefold()).issubset(
        {"available", "zero"}
    )
    ap_status_chart = ap_chart.loc[ap_chart["Series"].astype(str) == cm.ACTION_PLAN_STATUS_SERIES]
    ap_age_chart = ap_chart.loc[ap_chart["Series"].astype(str) == cm.ACTION_PLAN_AGE_SERIES]
    if ap_chart_complete and (
        int(ap_status_chart["Value"].fillna(0).sum()) != ap["total"]
        or set(ap_status_chart["Category"].astype(str)) != set(cm.ACTION_PLAN_BUCKET_ORDER)
    ):
        errors.append("Action Plan status chart does not partition the lifecycle total")
    if ap_chart_complete and (
        int(ap_age_chart["Value"].fillna(0).sum()) != ap["unresolved_total"]
        or set(ap_age_chart["Category"].astype(str)) != set(cm.ACTION_PLAN_AGE_BAND_ORDER)
    ):
        errors.append("Action Plan age chart does not partition the unresolved-plan total")
    elif (
        ap_chart["Value"].notna()
        & ~ap_chart["Source_State"]
        .fillna("unavailable")
        .astype(str)
        .str.casefold()
        .isin({"available", "zero"})
    ).any():
        errors.append("Action Plan chart exposes values from incomplete source coverage")
    risk_chart = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == "risk_distribution"]
    risk_chart_complete = set(risk_chart["Source_State"].fillna("unavailable").astype(str).str.casefold()).issubset(
        {"available", "zero"}
    )
    if risk_chart_complete and int(risk_chart["Value"].fillna(0).sum()) != int(
        facts["risk_summary"].get("total_customers", 0)
    ):
        errors.append("risk chart does not partition the scored customer universe")
    elif (
        risk_chart["Value"].notna()
        & ~risk_chart["Source_State"]
        .fillna("unavailable")
        .astype(str)
        .str.casefold()
        .isin({"available", "zero"})
    ).any():
        errors.append("risk chart exposes values from incomplete source coverage")
    for chart_id, chart_rows in facts["chart_data"].groupby("Chart_ID", sort=True):
        incomplete_with_value = chart_rows[
            chart_rows["Value"].notna()
            & ~chart_rows["Source_State"]
            .fillna("unavailable")
            .astype(str)
            .str.casefold()
            .isin({"available", "zero"})
        ]
        if not incomplete_with_value.empty:
            errors.append(f"{chart_id} chart exposes values from incomplete source coverage")

    action_sheet = sheets.get("Action_Plans", pd.DataFrame())
    if len(action_sheet) != int(ap["total"]):
        errors.append(f"Action_Plans sheet rows {len(action_sheet)} != canonical total {ap['total']}")
    if "AdoptIQ_Status_Bucket" in action_sheet.columns:
        sheet_buckets = action_sheet["AdoptIQ_Status_Bucket"].fillna("Unknown").value_counts().to_dict()
        if any(int(sheet_buckets.get(key, 0)) != int(value) for key, value in ap["bucket_counts"].items()):
            errors.append("Action_Plans sheet lifecycle buckets differ from canonical facts")
    elif ap["total"]:
        errors.append("Action_Plans sheet lacks AdoptIQ_Status_Bucket")
    if "AdoptIQ_Age_Band" in action_sheet.columns:
        sheet_age_bands = action_sheet["AdoptIQ_Age_Band"].fillna("Unknown")
        sheet_age_counts = sheet_age_bands.value_counts().to_dict()
        if any(int(sheet_age_counts.get(key, 0)) != int(value) for key, value in ap["age_band_counts"].items()):
            errors.append("Action_Plans sheet unresolved age bands differ from canonical facts")
        if "AdoptIQ_Status_Bucket" in action_sheet.columns:
            completed_mask = action_sheet["AdoptIQ_Status_Bucket"].eq("Completed")
            if not sheet_age_bands.loc[completed_mask].eq(cm.ACTION_PLAN_COMPLETED_AGE_LABEL).all():
                errors.append("Action_Plans completed rows are not excluded from aging")
            if not sheet_age_bands.loc[~completed_mask].isin(cm.ACTION_PLAN_AGE_BAND_ORDER).all():
                errors.append("Action_Plans unresolved rows lack a canonical age band")
    elif ap["total"]:
        errors.append("Action_Plans sheet lacks AdoptIQ_Age_Band")

    bems_sheet = sheets.get("BEMS", pd.DataFrame())
    if len(bems_sheet) != int(facts["kpis"]["bems"]):
        errors.append(f"BEMS sheet rows {len(bems_sheet)} != canonical BEMS KPI {facts['kpis']['bems']}")

    chart_sheet = sheets.get("Chart_Data", pd.DataFrame())
    chart_sheet_visible = (
        chart_sheet.loc[chart_sheet.get("Metric_Key", pd.Series(dtype="object")).fillna("").astype(str).ne("")]
        if isinstance(chart_sheet, pd.DataFrame)
        else pd.DataFrame()
    )
    expected_chart_keys = set(facts["chart_data"]["Metric_Key"].fillna("").astype(str))
    sheet_chart_keys = set(chart_sheet_visible.get("Metric_Key", pd.Series(dtype=str)).astype(str))
    if sheet_chart_keys != expected_chart_keys:
        errors.append("Chart_Data sheet metric keys differ from canonical chart series")
    elif {"Value", "Source_State"}.issubset(chart_sheet_visible.columns):
        expected_chart = facts["chart_data"].set_index("Metric_Key")
        actual_chart = chart_sheet_visible.set_index("Metric_Key")
        for metric_key in sorted(expected_chart_keys):
            expected_value = expected_chart.loc[metric_key, "Value"]
            actual_value = actual_chart.loc[metric_key, "Value"]
            if not (pd.isna(expected_value) and pd.isna(actual_value)):
                try:
                    equal_value = float(expected_value) == float(actual_value)
                except Exception:  # noqa: BLE001
                    equal_value = str(expected_value) == str(actual_value)
                if not equal_value:
                    errors.append(f"Chart_Data value mismatch for {metric_key}")
            if str(expected_chart.loc[metric_key, "Source_State"]) != str(actual_chart.loc[metric_key, "Source_State"]):
                errors.append(f"Chart_Data source state mismatch for {metric_key}")

    source_sheets = (
        "Action_Plans",
        "Adoption_Barriers",
        "Customer_Pulse",
        "TAC_Cases",
        "BEMS",
        "Subscriptions",
        "Success_Priorities",
        "External_Incidents",
        "External_Bugs",
        "Defect_Correlations",
    )
    context_columns = {"Record_ID", "CSSM", "Scope_Type", "Scope_Value", "Source_System", "Attributed_Team_Members"}
    for sheet_name in source_sheets:
        frame = sheets.get(sheet_name, pd.DataFrame())
        missing_context = sorted(context_columns - set(frame.columns))
        if missing_context:
            errors.append(f"{sheet_name} lacks public provenance columns: {', '.join(missing_context)}")
            continue
        if not frame.empty:
            native_candidates = _ID_CANDIDATES.get(
                next((key for key, value in _FRAME_KEYS.items() if value == sheet_name), ""),
                ("Record_ID", "ID", "id"),
            )
            for _, row in frame.iterrows():
                # Round 148: use the same missing-ID semantics as
                # ``_with_public_record_id``.  Live DSM rows can carry the
                # literal ``Unknown`` in SUBSCRIPTION_ID; that is a disclosed
                # missing stable ID, not a source ID that failed publication.
                native_id = next(
                    (token for token in (_clean_token(row.get(column)) for column in native_candidates) if token),
                    "",
                )
                if native_id and not _clean_token(row.get("Record_ID")):
                    errors.append(f"{sheet_name} contains a source ID without public Record_ID")
                    break

    lineage = sheets.get("Metric_Lineage", pd.DataFrame())
    if isinstance(lineage, pd.DataFrame) and "Metric_Key" in lineage.columns:
        duplicate_lineage_keys = sorted(
            set(
                lineage.loc[
                    lineage["Metric_Key"].fillna("").astype(str).duplicated(keep=False),
                    "Metric_Key",
                ].astype(str)
            )
        )
        if duplicate_lineage_keys:
            errors.append(
                "Metric_Lineage contains duplicate evidence identities: " + ", ".join(duplicate_lineage_keys[:20])
            )
    lineage_keys = set(lineage.get("Metric_Key", pd.Series(dtype=str)).dropna().astype(str))
    chart_keys = set(facts["chart_data"]["Metric_Key"].dropna().astype(str))
    missing_lineage = sorted(chart_keys - lineage_keys)
    if missing_lineage:
        errors.append(f"{len(missing_lineage)} chart series lack Metric_Lineage rows")
    for key in (
        "kpi.customers",
        "kpi.team_members",
        "kpi.action_plans_total",
        "kpi.action_plans_open",
        "kpi.adoption_barriers",
        "kpi.customer_pulse",
        "kpi.tac_cases",
        "kpi.bems",
        "kpi.high_risk_customers",
    ):
        if key not in lineage_keys:
            errors.append(f"visible KPI lacks lineage: {key}")
    expected_lineage_keys = set(facts["metric_lineage"]["Metric_Key"].dropna().astype(str))
    if lineage_keys != expected_lineage_keys:
        errors.append("Metric_Lineage sheet keys differ from canonical visible-claim lineage")

    evidence_result = validate_evidence_links(sheets)
    errors.extend(evidence_result["errors"])
    evidence_frame = sheets.get("Evidence_Links", pd.DataFrame())
    evidence_keys = (
        set(evidence_frame.get("Evidence_Key", pd.Series(dtype=str)).fillna("").astype(str))
        if isinstance(evidence_frame, pd.DataFrame)
        else set()
    )
    decision_brief = _decision_brief_contract(facts)
    for evidence_key in decision_brief.get("action_evidence_keys") or []:
        if not _keys_matching_reference(evidence_key, evidence_keys):
            errors.append(
                f"rendered Immediate Action Plan source reference does not resolve in Evidence_Links: {evidence_key}"
            )
    for reference_group in _expected_chart_reference_groups(facts):
        for token in reference_group:
            if not _keys_matching_reference(token, lineage_keys):
                errors.append("rendered chart source family does not resolve in Metric_Lineage: " + token)
            if not _keys_matching_reference(token, evidence_keys):
                errors.append("rendered chart source family does not resolve in Evidence_Links: " + token)
    correlation_records = (facts.get("defect_correlation_bundle") or {}).get("records") or []
    expected_correlation_keys = {
        evidence_entity_key(
            "defect_correlation",
            f"{_clean_token(record.get('identity_key'))}|{_clean_token(record.get('csc_id'))}",
        )
        for record in correlation_records
    }
    correlation_sheet = sheets.get("Defect_Correlations", pd.DataFrame())
    actual_correlation_keys = set(correlation_sheet.get("Metric_Key", pd.Series(dtype=str)).dropna().astype(str))
    if actual_correlation_keys != expected_correlation_keys:
        errors.append("Defect_Correlations keys differ from exact scoped correlation facts")
    correlation_signal_keys = {
        str(signal.get("evidence_key") or "")
        for signal in (facts.get("decision_signals") or [])
        if signal.get("source_key") == "defect_correlations"
    }
    if correlation_signal_keys != expected_correlation_keys:
        errors.append("exact defect correlations are missing from visible decision signals")
    if not expected_correlation_keys.issubset(lineage_keys):
        errors.append("exact defect correlations are missing Metric_Lineage rows")
    if expected_correlation_keys and isinstance(evidence_frame, pd.DataFrame):
        for metric_key in sorted(expected_correlation_keys):
            linked = evidence_frame.loc[
                evidence_frame.get("Evidence_Key", pd.Series(dtype=str)).fillna("").astype(str) == metric_key
            ]
            roles = set(linked.get("Evidence_Role", pd.Series(dtype=str)).astype(str))
            if "prioritized_cross_source_signal" not in roles:
                errors.append(f"{metric_key} lacks visible decision-signal evidence")
            if "exact_csc_parent_record" not in roles:
                errors.append(f"{metric_key} lacks exact TAC/Barrier parent evidence")
            matching_record = next(
                (
                    record
                    for record in correlation_records
                    if evidence_entity_key(
                        "defect_correlation",
                        f"{_clean_token(record.get('identity_key'))}|{_clean_token(record.get('csc_id'))}",
                    )
                    == metric_key
                ),
                {},
            )
            if matching_record.get("verified_external_match") and "exact_csc_external_bug_record" not in roles:
                errors.append(f"{metric_key} lacks verified External_Bugs evidence")

    risk_components = sheets.get("Risk_Components", pd.DataFrame())
    expected_risk_customers = int(facts["risk_summary"].get("total_customers", 0))
    actual_risk_customers = (
        int(risk_components["Customer"].dropna().astype(str).nunique()) if "Customer" in risk_components.columns else 0
    )
    if actual_risk_customers != expected_risk_customers:
        errors.append(
            f"Risk_Components customer count {actual_risk_customers} != scored universe {expected_risk_customers}"
        )

    word_result = None
    word_semantics = None
    word_placeholders = None
    if doc is not None:
        word_result = validate_word_content(doc, word_budget=word_budget)
        errors.extend(word_result["errors"])
        word_semantics = validate_word_semantics(facts, doc)
        errors.extend(word_semantics["errors"])
        word_placeholders = audit_word_placeholders(doc)
        errors.extend(word_placeholders["errors"])
        if str(doc.core_properties.identifier or "") != expected_fingerprint:
            errors.append("Word fact fingerprint differs from canonical facts")
        available_charts = 0
        for chart_id in ("activity_mix", "action_plan_status_aging", "risk_distribution", "activity_trend"):
            rows = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == chart_id]
            if chart_id == "activity_trend":
                rows = rows.dropna(subset=["Period_Start", "Value"])
            else:
                rows = rows.dropna(subset=["Value"])
            if not rows.empty:
                available_charts += 1
        # Missing matplotlib is an environment dependency failure, not a valid
        # silent pass when data supports charts.
        if len(doc.inline_shapes) < available_charts:
            errors.append(
                f"embedded chart count {len(doc.inline_shapes)} is below available series count {available_charts}"
            )
    return {
        "ok": not errors,
        "errors": errors,
        "word": word_result,
        "word_semantics": word_semantics,
        "word_placeholders": word_placeholders,
        "completeness": completeness,
        "evidence": evidence_result,
        "required_sheets": sorted(required),
        "chart_series_count": int(len(facts["chart_data"])),
        "lineage_count": int(len(lineage)),
    }


__all__ = [
    "FORBIDDEN_RAW_WORD_HEADINGS",
    "REPORT_SPECIFIC_FACT_LIMIT",
    "SOURCE_DATA_SHEET_NAMES",
    "TOP_ITEM_LIMIT_DEFAULT",
    "WORD_BUDGET_DEFAULT",
    "aggregate_team_frames",
    "build_concise_word_document",
    "build_report_facts",
    "build_source_data_sheets",
    "document_word_count",
    "fact_contract_fingerprint",
    "evidence_entity_key",
    "partition_portfolio_by_member",
    "source_data_path_for_word",
    "validate_cross_artifact_contract",
    "validate_evidence_links",
    "validate_written_source_workbook",
    "validate_word_content",
    "validate_word_semantics",
    "write_source_data_workbook",
]
