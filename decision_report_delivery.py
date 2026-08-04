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
from data_normalization import (
    _clean_name_for_key,
    detect_bems_mask,
    normalize_customer_name,
    normalize_priority_label,
)
from report_word_styling import add_banded_top_n_table
from risk_scoring import compute_customer_risk_profile, compute_portfolio_risk_summary

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
    "Action_Plans",
    "Adoption_Barriers",
    "Customer_Pulse",
    "TAC_Cases",
    "BEMS",
    "Subscriptions",
    "Success_Priorities",
    "External_Incidents",
    "External_Bugs",
    "Risk_Components",
    "Member_Summary",
    "Account_Summary",
)
# Round 143: public, ordered artifact inventory for acceptance tooling.  The
# acceptance runner must reuse the writer's contract instead of maintaining a
# competing list that could drift when the canonical workbook evolves.
SOURCE_DATA_SHEET_NAMES = ("Report_Info", *_SOURCE_CONTRACT_SHEETS)
_PUBLIC_CONTEXT_COLUMN_ORDER = (
    "Record_ID",
    "Record_ID_Data_Quality",
    "CSSM",
    "Scope_Type",
    "Scope_Value",
    "Source_System",
    "Attributed_Team_Members",
)

_SOURCE_SYSTEM_BY_KEY: Dict[str, str] = {
    "subscriptions": "Snowflake subscriptions",
    "action_plans": "Snowflake C360 Action Plans",
    "adoption_barriers": "Snowflake C360 Adoption Barriers",
    "customer_pulse": "Snowflake C360 Customer Pulse",
    "tac_cases": "CSOne",
    "success_priorities": "Snowflake C360 Success Priorities",
    "external_incidents": "status.webex.com",
    "external_bugs": "help.webex.com",
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
    "NEXT_ACTION_OWNER_C",
    "ASSIGNEE_C",
    "Owner Name",
    "OWNER_NAME",
    "_CREATOR_NAME",
)
_NEXT_ACTION_COLUMNS = (
    "NEXT_ACTION_C",
    "NEXT_STEP_C",
    "CURRENT_STATUS_AND_NOTES_C",
    "ACTION_C",
)
_PRIORITY_COLUMNS = ("PRIORITY_C", "Priority", "PRIORITY", "SEVERITY_C")


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
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
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

    chart_rows = facts["chart_data"].loc[
        :,
        ["Metric_Key", "Value", "Source_State", "Period_Start"],
    ].to_dict("records")
    payload = {
        "report_type": facts["report_type"],
        "scope_type": facts["scope_type"],
        "scope_value": facts["scope_value"],
        "manager_name": facts["manager_name"],
        "days": facts["days"],
        "as_of_utc": facts["as_of_utc"],
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
                "missing_title",
                "missing_record_id",
                "source_state",
            )
        },
        "chart_rows": chart_rows,
        "member_summary": facts.get("member_summary"),
        "account_summary": facts.get("account_summary"),
        "source_coverage": facts["source_coverage"].to_dict("records"),
        "partial_data_warnings": facts.get("partial_data_warnings") or [],
        "source_sheet_digests": dict(
            source_digests
            if source_digests is not None
            else _source_contract_digests(facts)
        ),
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
    return use


def aggregate_team_frames(
    team_data: Optional[Mapping[str, Mapping[str, Any]]],
    *,
    scope_type: str,
    scope_value: str,
) -> Dict[str, pd.DataFrame]:
    """Create one deduplicated, attribution-preserving frame per source."""

    result: Dict[str, pd.DataFrame] = {}
    team_data = team_data or {}
    for key in _FRAME_KEYS:
        parts: List[pd.DataFrame] = []
        attrs: Dict[str, Any] = {}
        supplied_members = 0
        for member in sorted(team_data):
            bundle = team_data.get(member) or {}
            frame = bundle.get(key)
            if not isinstance(frame, pd.DataFrame):
                continue
            supplied_members += 1
            frame_state = cm.source_data_state(frame)
            attrs.setdefault("source_states", []).append(frame_state["state"])
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
                attribution = with_id.groupby(id_column, dropna=False)["CSSM"].agg(_combine_attribution)
                with_id = (
                    with_id.sort_values([id_column, "CSSM"], kind="stable")
                    .drop_duplicates(subset=[id_column], keep="first")
                    .copy()
                )
                with_id["Attributed_Team_Members"] = with_id[id_column].map(attribution)
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
        if "Source_System" not in combined.columns:
            combined["Source_System"] = _SOURCE_SYSTEM_BY_KEY[key]
        else:
            fallback_source = _SOURCE_SYSTEM_BY_KEY[key]
            combined["Source_System"] = combined["Source_System"].map(
                lambda value: _clean_token(value) or fallback_source
            )
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
            combined.attrs["source_mode_detail"] = "; ".join(
                sorted(set(attrs["partial_details"]))
            )
        combined = combined.drop(columns=["_AdoptIQ_Partition_Row_Key"], errors="ignore")
        finalized = combined.reset_index(drop=True)
        finalized.attrs.update(combined.attrs)
        result[key] = finalized
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
    if "Source_System" not in use.columns:
        use["Source_System"] = fallback_source
    else:
        use["Source_System"] = use["Source_System"].map(
            lambda value: _clean_token(value) or fallback_source
        )
    if "Attributed_Team_Members" not in use.columns:
        use["Attributed_Team_Members"] = ""
    if "CSSM" not in use.columns:
        use["CSSM"] = ""
    return use


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
        frame["_AdoptIQ_Partition_Row_Key"] = [
            f"{source_key}:{position}" for position in range(len(frame))
        ]
        assigned: Dict[str, List[int]] = {member: [] for member in members}
        unassigned: List[int] = []
        for position, row in frame.iterrows():
            direct_tokens = {
                _clean_token(row.get(column)).casefold()
                for column in _OWNER_COLUMNS
                if column in frame.columns and _clean_token(row.get(column))
            }
            direct_emails = {token for token in direct_tokens if "@" in token}
            direct_names = direct_tokens - direct_emails
            direct_members = [
                member
                for member in members
                if direct_emails & ownership[member]["email_aliases"]
            ]
            if not direct_members and direct_names:
                name_matches = [
                    member
                    for member in members
                    if direct_names & ownership[member]["name_aliases"]
                ]
                # A duplicate display name is not a safe direct attribution.
                if len(name_matches) == 1:
                    direct_members = name_matches
            if direct_members:
                targets = direct_members
            else:
                row_customers = {
                    _customer_match_key(row.get(column))
                    for column in _CUSTOMER_COLUMNS
                    if column in frame.columns and _customer_match_key(row.get(column))
                }
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
                if account_targets:
                    targets = account_targets
                else:
                    targets = [
                        member
                        for member in members
                        if row_customers & ownership[member]["customers"]
                    ]
                if not targets:
                    row_fuzzy_customers = {
                        _customer_fuzzy_join_key(row.get(column))
                        for column in _CUSTOMER_COLUMNS
                        if column in frame.columns and _customer_fuzzy_join_key(row.get(column))
                    }
                    fuzzy_targets = [
                        member
                        for member in members
                        if row_fuzzy_customers & ownership[member]["customer_fuzzy"]
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


def _row_tokens(row: Mapping[str, Any], candidates: Sequence[str], normalizer: Any) -> set:
    return {
        token
        for column in candidates
        if (token := normalizer(row.get(column)))
    }


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
                "display_candidates": [],
            },
        )
        identity["account_ids"].update(ids)
        if observation["exact_name"]:
            identity["exact_name_keys"].add(observation["exact_name"])
        if observation["fuzzy_name"]:
            identity["fuzzy_name_keys"].add(observation["fuzzy_name"])
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
                "display_candidates": [],
            },
        )
        for field in ("account_ids", "exact_name_keys", "fuzzy_name_keys"):
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
        if len(candidates) == 1:
            identity = stable[candidates[0]]
            identity["exact_name_keys"].add(observation["exact_name"])
            if observation["fuzzy_name"]:
                identity["fuzzy_name_keys"].add(observation["fuzzy_name"])
            if observation["display"]:
                identity["display_candidates"].append(
                    (observation["source_rank"], observation["row_rank"], observation["display"])
                )
            continue
        if len(candidates) > 1:
            # An ID-less row cannot be safely assigned when two authoritative
            # accounts expose the same exact label.
            continue
        identity = name_only.setdefault(
            observation["exact_name"],
            {
                "account_ids": set(),
                "exact_name_keys": {observation["exact_name"]},
                "fuzzy_name_keys": set(),
                "display_candidates": [],
            },
        )
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
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=list(frame.columns) if isinstance(frame, pd.DataFrame) else None)

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
    return frame.loc[selected].copy().reset_index(drop=True)


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


def _build_risk_profiles(
    frames: Mapping[str, pd.DataFrame],
    *,
    days: int,
    as_of: Any,
) -> Dict[str, Dict[str, Any]]:
    identities = _canonical_customer_identities(frames)
    profiles: Dict[str, Dict[str, Any]] = {}
    for identity in identities:
        customer = str(identity["label"])
        profile = compute_customer_risk_profile(
            customer,
            customer_ab=_customer_frame_for_identity(
                frames.get("adoption_barriers", pd.DataFrame()), identity, identities
            ),
            customer_csone=_customer_frame_for_identity(
                frames.get("tac_cases", pd.DataFrame()), identity, identities
            ),
            customer_pulse=_customer_frame_for_identity(
                frames.get("customer_pulse", pd.DataFrame()), identity, identities
            ),
            customer_action_plans=_customer_frame_for_identity(
                frames.get("action_plans", pd.DataFrame()), identity, identities
            ),
            customer_subs=_customer_frame_for_identity(
                frames.get("subscriptions", pd.DataFrame()), identity, identities
            ),
            # Portfolio-wide status incidents have no customer key and must not
            # be copied onto every account's score.
            ext_incidents=None,
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
        frame["AdoptIQ_Status_Bucket"].isin(
            ["Overdue", "Blocked / On Hold", "Due Soon", "Open", "Unknown"]
        )
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
                _first_value(row, _CUSTOMER_COLUMNS, "Account unavailable"),
                _first_value(row, _OWNER_COLUMNS, "Owner unavailable"),
                row.get("AdoptIQ_Title") or "Title unavailable",
                row.get("AdoptIQ_Status_Bucket") or "Unknown",
                due_text,
                age_text,
                _first_value(row, _NEXT_ACTION_COLUMNS, "Next action unavailable"),
                _first_value(row, _PRIORITY_COLUMNS, "Unknown"),
            ]
        )
    return rows


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
                    key: (
                        bundle.get(key)
                        if isinstance(bundle.get(key), pd.DataFrame)
                        else pd.DataFrame()
                    )
                    for key in _FRAME_KEYS
                }
            )
        )
        barriers_frame = bundle.get("adoption_barriers")
        tac_frame = bundle.get("tac_cases")
        barriers = cm.count_total_barriers(barriers_frame)
        tac_cases = cm.count_total_tac(tac_frame)

        def state_value(value: int, state: str) -> Any:
            if state in {"failed", "unavailable"}:
                return "Unavailable"
            if state in {"partial", "stale"}:
                return f"{value} ({state})"
            return value

        contributing_states = [
            cm.source_data_state(bundle.get(source))["state"]
            for source in ("subscriptions", "action_plans", "adoption_barriers", "customer_pulse", "tac_cases")
        ]
        if all(state in {"failed", "unavailable"} for state in contributing_states):
            customer_state = "unavailable"
        elif any(state in {"failed", "unavailable", "partial", "stale"} for state in contributing_states):
            customer_state = "partial"
        else:
            customer_state = "available"
        row = [
            member_label,
            state_value(customers, customer_state),
            state_value(lifecycle["open"], lifecycle["source_state"]),
            state_value(lifecycle["overdue"], lifecycle["source_state"]),
            state_value(barriers, cm.source_data_state(barriers_frame)["state"]),
            state_value(tac_cases, cm.source_data_state(tac_frame)["state"]),
        ]
        ranked_rows.append(
            (
                (
                    -int(lifecycle["overdue"]),
                    -int(lifecycle["open"]),
                    -int(barriers),
                    -int(tac_cases),
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
    identities = [
        _identity_from_profile(customer, profile)
        for customer, profile in risk_profiles.items()
    ]
    ranked = sorted(
        risk_profiles.items(),
        key=lambda item: (-float(item[1].get("risk_score_0_100", 0.0) or 0.0), item[0]),
    )
    rows: List[List[Any]] = []
    selected = ranked if limit is None else ranked[: max(int(limit), 1)]
    for customer, profile in selected:
        identity = _identity_from_profile(customer, profile)
        customer_ap = _customer_frame_for_identity(
            frames.get("action_plans", pd.DataFrame()), identity, identities
        )
        lifecycle = cm.build_action_plan_lifecycle(customer_ap, as_of=as_of)
        rows.append(
            [
                customer,
                profile.get("risk_band", "UNKNOWN"),
                profile.get("risk_score_0_100", 0.0),
                lifecycle["open"],
                lifecycle["overdue"],
                cm.count_critical_barriers(
                    _customer_frame_for_identity(
                        frames.get("adoption_barriers", pd.DataFrame()), identity, identities
                    )
                ),
                cm.count_total_tac(
                    _customer_frame_for_identity(
                        frames.get("tac_cases", pd.DataFrame()), identity, identities
                    )
                ),
            ]
        )
    return rows


def _source_coverage(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for key, sheet in _FRAME_KEYS.items():
        frame = frames.get(key)
        state = cm.source_data_state(frame)
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
) -> Dict[str, Any]:
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
        "Source_State": source_state,
        "Caveat": caveat,
    }


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
        ("kpi.team_members", "Team members", facts["kpis"]["team_members"], "canonical_metrics.count_team_members", "Report_Info", "validated member bundles", "distinct email-keyed member bundles; duplicate display names disambiguated"),
        ("kpi.customers", "Customers", facts["kpis"]["customers"], "decision_report_delivery._canonical_customer_identities", "Subscriptions; Action_Plans; Adoption_Barriers; Customer_Pulse; TAC_Cases; Success_Priorities", "stable account/customer ID; exact normalized customer label", "stable ID first; otherwise exact suffix-sensitive label; fuzzy alias only for an unambiguous ID-backed join"),
        ("kpi.action_plans_total", "Action Plans", None if ap_unavailable else ap["total"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", str(ap["field_selection"]), ap["deduplication_rule"]),
        ("kpi.action_plans_open", "Open Action Plans", None if ap_unavailable else ap["open"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", "status + due date", ap["deduplication_rule"]),
        ("kpi.action_plans_overdue", "Overdue Action Plans", None if ap_unavailable else ap["overdue"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", "status + due date", ap["deduplication_rule"]),
        ("kpi.action_plans_due_soon", "Action Plans Due Soon", None if ap_unavailable else ap["due_soon"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", "status + due date", ap["deduplication_rule"]),
        ("kpi.action_plans_completed", "Completed Action Plans", None if ap_unavailable else ap["completed"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", "status", ap["deduplication_rule"]),
        ("kpi.action_plans_blocked", "Blocked / On Hold Action Plans", None if ap_unavailable else ap["blocked_on_hold"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", "status", ap["deduplication_rule"]),
        ("kpi.action_plans_unknown", "Unknown Action Plan Status", None if ap_unavailable else ap["unknown"], "canonical_metrics.build_action_plan_lifecycle", "Action_Plans", "status", ap["deduplication_rule"]),
        ("kpi.adoption_barriers", "Adoption Barriers", facts["kpis"]["adoption_barriers"], "canonical_metrics.count_total_barriers", "Adoption_Barriers", "Record_ID / ID", "distinct stable ID"),
        ("kpi.customer_pulse", "Customer Pulse", facts["kpis"]["customer_pulse"], "canonical_metrics.count_total_customer_pulse", "Customer_Pulse", "Record_ID / ID", "distinct stable ID"),
        ("kpi.tac_cases", "TAC Cases", facts["kpis"]["tac_cases"], "canonical_metrics.count_total_tac", "TAC_Cases", "resolved TAC case ID", "canonical TAC collapse"),
        ("kpi.bems", "BEMS escalations (TAC subset)", facts["kpis"]["bems"], "canonical_metrics.count_bems", "BEMS", "TAC BEMS identifiers", "canonical collapsed TAC case ID; subset, not added to activity total"),
        ("kpi.high_risk_customers", "High-risk customers", None if risk_unavailable else facts["kpis"]["high_risk_customers"], "risk_scoring.compute_portfolio_risk_summary", "Risk_Components", "risk_band", "distinct ID-first, suffix-sensitive canonical customer"),
    ]
    for key, label, value, function, sheet, fields, dedupe in metric_contracts:
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
                source_state=state(sheet),
            )
        )

    member_columns = (
        ("customers", 1),
        ("open_action_plans", 2),
        ("overdue_action_plans", 3),
        ("barriers", 4),
        ("tac_cases", 5),
    )
    for member_row in facts.get("member_summary") or []:
        member_slug = re.sub(r"[^a-z0-9]+", "_", str(member_row[0]).lower()).strip("_") or "unknown"
        for metric_name, index in member_columns:
            rows.append(
                _lineage_row(
                    key=f"summary.member.{member_slug}.{metric_name}",
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
                    source_state=state(
                        "Subscriptions; Action_Plans; Adoption_Barriers; TAC_Cases"
                    ),
                )
            )

    account_columns = (
        ("risk_score", 2),
        ("open_action_plans", 3),
        ("overdue_action_plans", 4),
        ("critical_high_barriers", 5),
        ("tac_cases", 6),
    )
    for account_row in facts.get("account_summary") or []:
        account_slug = _customer_match_key(account_row[0]).replace(" ", "_") or "unknown"
        for metric_name, index in account_columns:
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
                    source_state=state(
                        "Subscriptions; Action_Plans; Adoption_Barriers; TAC_Cases"
                    ),
                    unit="score (0–100)" if metric_name == "risk_score" else "records",
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
    return pd.DataFrame(rows)


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
        rows.append(
            {
                "Chart_ID": "action_plan_status_aging",
                "Metric_Key": f"chart.action_plan_status.{slug}",
                "Display_Label": f"Action Plan status and aging — {row['Category']}",
                "Series": row["Series"],
                "Category": row["Category"],
                "Period_Start": pd.NaT,
                "Value": row["Value"],
                "Unit": "records",
                "Source_Sheet": "Action_Plans",
                "Source_Fields": str(lifecycle["field_selection"]),
                "Canonical_Function": "canonical_metrics.action_plan_chart_series",
                "Grouping": "mutually exclusive lifecycle bucket",
                "Deduplication": lifecycle["deduplication_rule"],
                "Source_State": row.get("Source_State") or lifecycle.get("source_state") or "available",
                "Caveat": (
                    f"Due Soon means 0–{lifecycle['due_soon_days']} days from as-of date. "
                    f"{lifecycle.get('source_state_detail') or ''}"
                ).strip(),
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
                "Source_State": row.get("Source_State") or "available",
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
    return pd.DataFrame(rows)


def build_report_facts(
    team_data: Mapping[str, Mapping[str, Any]],
    *,
    report_type: str,
    scope_type: str,
    scope_value: str,
    manager_name: str,
    days: int,
    as_of: Any,
    external_incidents: Optional[Sequence[Mapping[str, Any]]] = None,
    external_bugs: Optional[Sequence[Mapping[str, Any]]] = None,
    partial_data_warnings: Optional[Sequence[Mapping[str, Any]]] = None,
    top_item_limit: int = TOP_ITEM_LIMIT_DEFAULT,
) -> Dict[str, Any]:
    """Build the single fact bundle consumed by Word and Source Data."""

    as_of_ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(as_of_ts):
        raise ValueError("build_report_facts requires a valid explicit as_of timestamp")
    frames = aggregate_team_frames(team_data, scope_type=scope_type, scope_value=scope_value)
    lifecycle = cm.build_action_plan_lifecycle(frames["action_plans"], as_of=as_of_ts)
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
        }
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
    risk_profiles = _build_risk_profiles(frames, days=days, as_of=as_of_ts)
    risk_summary = compute_portfolio_risk_summary(risk_profiles)
    risk_input_states = {
        key: cm.source_data_state(frames[key])["state"]
        for key in ("subscriptions", "action_plans", "adoption_barriers", "customer_pulse", "tac_cases")
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
        "known_total_activities": activity_mix["known_total"],
        "high_risk_customers": int(risk_summary.get("high_risk_customers", 0)),
    }
    def _external_frame(records: Any, *, possible_cap: Optional[int] = None) -> pd.DataFrame:
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
            built.attrs["source_mode_detail"] = (
                "external feed reached its configured row/window cap; older records omitted"
            )
        if any(bool(item.get("stale")) for item in metadata):
            built.attrs["stale"] = True
        if possible_cap and len(built) >= int(possible_cap):
            built.attrs["partial"] = True
            built.attrs.setdefault(
                "source_mode_detail",
                f"external feed returned the configured {possible_cap}-record cap; "
                "additional records may be omitted",
            )
        return built

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
    source_coverage = _source_coverage(frames)
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
        ]
    )
    source_coverage = pd.DataFrame(
        source_coverage_rows,
        columns=["Source_Sheet", "Source_State", "Record_Count", "Detail"],
    )
    member_summary_all = _build_member_summary(team_data, as_of=as_of_ts, limit=None)
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
        "days": int(days),
        "as_of_utc": as_of_ts.isoformat(),
        "frames": frames,
        "bems": bems,
        "external_incidents": external_incidents_frame,
        "external_bugs": external_bugs_frame,
        "partial_data_warnings": list(partial_data_warnings or []),
        "action_plan_lifecycle": lifecycle,
        "activity_mix": activity_mix,
        "activity_trend": trend,
        "risk_profiles": risk_profiles,
        "risk_summary": risk_summary,
        "kpis": kpis,
        "source_coverage": source_coverage,
        "ranking_criteria": (
            "Top items rank overdue first, then blocked/on hold, due soon, open, and unknown; "
            "ties use canonical priority, due date, and stable source ID."
        ),
        "top_action_plans": _prioritized_action_plan_rows(lifecycle, top_item_limit),
        "member_summary": member_summary_all[: max(int(top_item_limit), 1)],
        "member_summary_all": member_summary_all,
        "member_summary_omitted": max(len(member_summary_all) - max(int(top_item_limit), 1), 0),
        "account_summary": account_summary_all[: max(int(top_item_limit), 1)],
        "account_summary_all": account_summary_all,
        "account_summary_omitted": max(len(account_summary_all) - max(int(top_item_limit), 1), 0),
    }
    facts["chart_data"] = _build_chart_data(activity_mix, lifecycle, risk_summary, trend)
    facts["metric_lineage"] = _build_lineage(facts)
    return facts


def build_source_data_sheets(
    facts: Mapping[str, Any],
    *,
    additional_sheets: Optional[Mapping[str, pd.DataFrame]] = None,
    _skip_contract_fingerprint: bool = False,
) -> "OrderedDict[str, pd.DataFrame]":
    """Return the always-present canonical companion workbook sheets."""

    source_digests = (
        {}
        if _skip_contract_fingerprint
        else _source_contract_digests(facts)
    )
    contract_fingerprint = (
        ""
        if _skip_contract_fingerprint
        else fact_contract_fingerprint(facts, source_digests=source_digests)
    )

    info_rows: List[Dict[str, Any]] = [
        {"Item": "Report_Type", "Value": facts["report_type"], "Detail": "concise decision report"},
        {"Item": "Manager", "Value": facts["manager_name"], "Detail": "selected manager"},
        {"Item": "Scope_Type", "Value": facts["scope_type"], "Detail": "team, member, or customer"},
        {"Item": "Scope_Value", "Value": facts["scope_value"], "Detail": "validated selected scope"},
        {"Item": "Days", "Value": facts["days"], "Detail": "analysis window"},
        {"Item": "Data_As_Of_UTC", "Value": facts["as_of_utc"], "Detail": "explicit deterministic aging clock"},
        {
            "Item": "Fact_Contract_SHA256",
            "Value": contract_fingerprint,
            "Detail": "semantic Word/Source Data/chart fact fingerprint",
        },
        {"Item": "Due_Soon_Days", "Value": facts["action_plan_lifecycle"]["due_soon_days"], "Detail": "inclusive horizon"},
        {"Item": "Activity_Total_State", "Value": facts["activity_mix"]["total_state"], "Detail": facts["activity_mix"]["definition"]},
        {"Item": "Partial_Data_Warning_Count", "Value": len(facts["partial_data_warnings"]), "Detail": "see following rows"},
    ]
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
        columns=["Team_Member", "Customers", "Open_AP", "Overdue_AP", "Barriers", "TAC_Cases"],
    )
    if not sheets["Member_Summary"].empty:
        sheets["Member_Summary"].insert(
            0,
            "Metric_Key",
            [
                f"summary.member.{re.sub(r'[^a-z0-9]+', '_', str(value).lower()).strip('_')}"
                for value in sheets["Member_Summary"]["Team_Member"]
            ],
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
    if additional_sheets:
        raise ValueError(
            "Additional Source Data sheets are not allowed outside the canonical "
            "content-digested contract"
        )
    return sheets


def source_data_path_for_word(word_path: Any) -> Path:
    """Return ``AdoptIQ_Source_Data_*`` path paired to a Word report."""

    path = Path(word_path)
    name = path.stem
    if name.startswith("AdoptIQ_Report_"):
        name = "AdoptIQ_Source_Data_" + name[len("AdoptIQ_Report_") :]
    elif not name.startswith("AdoptIQ_Source_Data_"):
        name = "AdoptIQ_Source_Data_" + name
    return path.with_name(name).with_suffix(".xlsx")


def _prepare_export_frame(raw: Any, sheet_name: str) -> pd.DataFrame:
    """Apply the exact public-column projection used by the XLSX writer."""

    from report_export_schema import apply_export_schema  # noqa: PLC0415

    frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame()
    frame = frame.drop(
        columns=[column for column in frame.columns if str(column).startswith("_")],
        errors="ignore",
    )
    frame = apply_export_schema(frame, sheet_name=sheet_name)
    context_columns = [
        column for column in _PUBLIC_CONTEXT_COLUMN_ORDER if column in frame.columns
    ]
    frame = frame.loc[
        :,
        context_columns + [column for column in frame.columns if column not in context_columns],
    ]
    for column in frame.columns:
        if frame[column].dtype != object:
            continue
        frame[column] = frame[column].map(
            lambda value: (
                json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))
                if isinstance(value, (Mapping, list, tuple, set))
                else value
            )
        )
    if frame.shape[1] == 0:
        frame = pd.DataFrame(columns=["Record_ID"])
    return frame


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
        number = Decimal(str(value))
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
        normalized = {
            column: _digest_cell(row.get(column))
            for column in columns
        }
        normalized_rows.append(
            json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
        )
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
    semantic = prepared.loc[
        items.ne("Fact_Contract_SHA256") & ~items.str.startswith("Sheet_SHA256:")
    ].copy()
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
            created = pd.to_datetime(as_of_values.iloc[0], errors="raise", utc=True).tz_localize(None)
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
        period_start_body = workbook.add_format(
            {"num_format": "yyyy-mm-dd", "valign": "top"}
        )
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
                safe_name = f"{base_name[:31 - len(suffix_token)]}{suffix_token}"
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
    workbook = load_workbook(target, read_only=True, data_only=False)
    sheet_names = set(workbook.sheetnames)
    missing = sorted(required - sheet_names)
    if missing:
        errors.append("written workbook missing sheets: " + ", ".join(missing))

    formula_cells = 0
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
    written_lineage_keys = set(
        lineage.get("Metric_Key", pd.Series(dtype=str)).fillna("").astype(str)
    )
    expected_lineage_keys = set(facts["metric_lineage"]["Metric_Key"].fillna("").astype(str))
    if written_lineage_keys != expected_lineage_keys:
        errors.append("written Metric_Lineage keys differ from canonical visible claims")

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
            "written Report_Info scope, provenance, warning, or source-state content "
            "differs from canonical facts"
        )
    info_items = set(report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str))
    expected_state_items = {
        f"Source_State:{value}" for value in facts["source_coverage"]["Source_Sheet"].astype(str)
    }
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
        report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str)
        == "Fact_Contract_SHA256"
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
            "Unknown": "#7F7F7F",
        }
        return [palette.get(str(category), "#0070C0") for category in categories]
    return ["#0070C0", "#00BCEB", "#6ABF4B", "#FFB81C", "#C0392B"][: len(categories)]


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
        fig, axis = plt.subplots(figsize=(8.4, 3.6), constrained_layout=True)
        if chart_id == "activity_trend":
            plotted = False
            for series, group in chart_rows.groupby("Series", sort=True):
                use = group.dropna(subset=["Period_Start", "Value"]).sort_values("Period_Start")
                if use.empty:
                    continue
                axis.plot(use["Period_Start"], use["Value"], marker="o", linewidth=2, label=str(series))
                plotted = True
            if not plotted:
                plt.close(fig)
                return False
            axis.set_title("Activity Trend")
            axis.set_xlabel("Week starting")
            axis.set_ylabel("Distinct records")
            axis.legend(loc="best", fontsize=8)
            fig.autofmt_xdate(rotation=30)
        else:
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
                "action_plan_status_aging": "Action Plan Status and Aging",
                "risk_distribution": "Customer Risk Distribution",
            }
            axis.set_title(titles.get(chart_id, chart_id.replace("_", " ").title()))
            axis.set_ylabel("Distinct records" if chart_id != "risk_distribution" else "Customers")
            axis.tick_params(axis="x", rotation=25)
            for bar, value in zip(bars, values):
                axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{int(value)}", ha="center", va="bottom")
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


def _add_source_reference(doc: Document, metric_keys: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run(f"[Source: Source Data File → Metric_Lineage / {metric_keys}]")
    run.italic = True
    run.font.size = Pt(7.5)
    run.font.color.rgb = RGBColor(0x58, 0x59, 0x5B)


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
    "risk_components": "Risk Components",
    "Risk_Components": "Risk Components",
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

    if kind == "tac_unmatched_after_subscription_join":
        return (
            "TAC Cases",
            "Partial assignment",
            f"{count_prefix}TAC case record(s) could not be assigned to a team member. "
            "They remain available in the Source Data File under the unassigned portfolio.",
        )
    if kind in {"fetch_failed", "source_unavailable", "timeout", "runtime"}:
        return (
            source,
            "Unavailable",
            "This source could not be retrieved for this run. Its metrics are shown as unavailable, not zero.",
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

    rows = []
    for warning in list(warnings)[:5]:
        source, state, effect = _public_warning_copy(warning)
        rows.append(
            [
                source,
                state,
                concise_effect(effect),
            ]
        )
    add_banded_top_n_table(doc, ["Source", "Coverage", "What this means"], rows)


def build_concise_word_document(
    facts: Mapping[str, Any],
    *,
    word_budget: int = WORD_BUDGET_DEFAULT,
) -> Document:
    """Render the concise default Word artifact from a shared fact bundle."""

    doc = Document()
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
    subtitle.add_run(
        f"{facts['manager_name']} • {str(facts['scope_type']).title()}: {facts['scope_value']} • "
        f"{facts['days']}-day window"
    ).bold = True
    stamp = doc.add_paragraph(f"Data as of {pd.Timestamp(facts['as_of_utc']).strftime('%Y-%m-%d %H:%M UTC')}")
    stamp.alignment = WD_ALIGN_PARAGRAPH.CENTER

    _add_partial_warning(doc, facts["partial_data_warnings"])

    doc.add_heading("Executive Summary", level=2)
    kpis = facts["kpis"]
    ap = facts["action_plan_lifecycle"]
    ap_state = str(ap.get("source_state") or "available")
    risk_state = str(facts["risk_summary"].get("source_state") or "available")

    coverage_by_sheet = facts["source_coverage"].set_index("Source_Sheet")

    def source_state(sheet: str) -> str:
        try:
            return str(coverage_by_sheet.loc[sheet, "Source_State"])
        except Exception:  # noqa: BLE001
            return "unavailable"

    core_customer_states = [
        source_state(sheet)
        for sheet in ("Subscriptions", "Action_Plans", "Adoption_Barriers", "Customer_Pulse", "TAC_Cases")
    ]
    if any(state in {"failed", "unavailable", "partial", "stale"} for state in core_customer_states):
        customer_state = (
            "unavailable"
            if all(state in {"failed", "unavailable"} for state in core_customer_states)
            else "partial"
        )
    else:
        customer_state = "available"

    def display_count(value: Any, state: str) -> Any:
        if state in {"failed", "unavailable"}:
            return "Unavailable"
        if state in {"partial", "stale"}:
            return f"{value} ({_COVERAGE_STATE_LABELS.get(state, state.title())})"
        return value

    coverage_is_limited = bool(facts["partial_data_warnings"]) or not facts["activity_mix"]["is_complete"]
    coverage_is_limited = coverage_is_limited or any(
        state in {"failed", "unavailable", "partial", "stale"}
        for state in core_customer_states
    )
    if facts["partial_data_warnings"]:
        coverage_sentence = (
            "Coverage has disclosed limitations; review the warning and Source Data File before acting."
        )
    elif coverage_is_limited:
        coverage_sentence = (
            "Coverage has disclosed limitations; review Source Coverage and the Source Data File before acting."
        )
    else:
        coverage_sentence = "Coverage is complete across the validated sources for this scope."
    if ap_state in {"failed", "unavailable"}:
        action_plan_sentence = (
            "Action Plan data is unavailable for this run, so no zero count or lifecycle conclusion is asserted."
        )
    else:
        overdue_verb = "is" if int(ap["overdue"]) == 1 else "are"
        due_soon_verb = "is" if int(ap["due_soon"]) == 1 else "are"
        action_plan_sentence = (
            f"Open Action Plans: {ap['open']}. Of these, {ap['overdue']} {overdue_verb} overdue and "
            f"{ap['due_soon']} {due_soon_verb} due within "
            f"{ap['due_soon_days']} days"
            + (f" ({ap_state} source)." if ap_state in {"partial", "stale"} else ".")
        )
    doc.add_paragraph(
        f"The selected scope covers {display_count(kpis['customers'], customer_state)} customers and "
        f"{kpis['team_members']} team members. "
        f"{action_plan_sentence} Known activity totals {kpis['known_total_activities']} distinct records "
        f"across available Action Plans, barriers, pulse, and TAC sources. {coverage_sentence}"
    )
    _add_source_reference(
        doc,
        "kpi.customers; kpi.team_members; kpi.action_plans_open; kpi.action_plans_overdue; "
        "kpi.action_plans_due_soon; chart.activity_mix.*",
    )
    doc.add_paragraph(
        "This report keeps decision metrics, account summaries, and prioritized actions concise. "
        "Complete activity, case, and source records are in the separately named Source Data File."
    )

    doc.add_heading("KPI and Data-Coverage Snapshot", level=2)
    kpi_rows = [
        ["Customers", display_count(kpis["customers"], customer_state), "kpi.customers"],
        ["Team members", kpis["team_members"], "kpi.team_members"],
        ["Action Plans", display_count(ap["total"], ap_state), "kpi.action_plans_total"],
        [
            "Open / overdue / due soon",
            display_count(f"{ap['open']} / {ap['overdue']} / {ap['due_soon']}", ap_state),
            "kpi.action_plans_open",
        ],
        [
            "Completed / blocked / unknown",
            display_count(f"{ap['completed']} / {ap['blocked_on_hold']} / {ap['unknown']}", ap_state),
            "kpi.action_plans_completed",
        ],
        ["Adoption Barriers", display_count(kpis["adoption_barriers"], source_state("Adoption_Barriers")), "kpi.adoption_barriers"],
        ["Customer Pulse", display_count(kpis["customer_pulse"], source_state("Customer_Pulse")), "kpi.customer_pulse"],
        ["TAC Cases", display_count(kpis["tac_cases"], source_state("TAC_Cases")), "kpi.tac_cases"],
        ["BEMS (TAC subset)", display_count(kpis["bems"], source_state("BEMS")), "kpi.bems"],
        ["High-risk customers", display_count(kpis["high_risk_customers"], risk_state), "kpi.high_risk_customers"],
    ]
    add_banded_top_n_table(doc, ["Metric", "Value", "Lineage key"], kpi_rows)
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
    add_banded_top_n_table(doc, ["Source", "State", "Distinct records"], coverage_rows)
    _add_source_reference(doc, "Metric_Lineage and Report_Info Source_State:* rows")

    doc.add_heading("Charts and Trends", level=2)
    chart_titles = OrderedDict(
        [
            ("activity_mix", "Activity Mix by Source"),
            ("action_plan_status_aging", "Action Plan Status and Aging"),
            ("risk_distribution", "Customer Risk Distribution"),
            ("activity_trend", "Activity Trend"),
        ]
    )
    with tempfile.TemporaryDirectory(prefix="adoptiq-r142-charts-") as temp_dir:
        for chart_id, chart_title in chart_titles.items():
            rows = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == chart_id].copy()
            available = rows.dropna(subset=["Value"])
            if chart_id == "activity_trend":
                available = available.dropna(subset=["Period_Start"])
            if available.empty:
                doc.add_heading(chart_title, level=3)
                doc.add_paragraph(
                    "Chart unavailable because this scope has no validated, dateable source series. "
                    "See Chart_Data and Report_Info for coverage details."
                )
                _add_source_reference(doc, f"chart.{chart_id}.*")
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
                _add_source_reference(doc, f"chart.{chart_id}.*")
            else:
                doc.add_heading(chart_title, level=3)
                doc.add_paragraph(
                    "Chart rendering was unavailable. Validated series remain in the Source Data File Chart_Data sheet."
                )
                _add_source_reference(doc, f"chart.{chart_id}.*")

    doc.add_heading("Prioritized Action Plan Rollup", level=2)
    doc.add_paragraph(facts["ranking_criteria"])
    status_rows = [
        ["Open", ap["open"]],
        ["Overdue", ap["overdue"]],
        ["Due Soon", ap["due_soon"]],
        ["Completed", ap["completed"]],
        ["Blocked / On Hold", ap["blocked_on_hold"]],
        ["Unknown", ap["unknown"]],
    ]
    if ap_state in {"failed", "unavailable"}:
        doc.add_paragraph(
            "Action Plan lifecycle rollup is unavailable because the source was not successfully supplied. "
            "See Report_Info for the source state and failure detail."
        )
    else:
        add_banded_top_n_table(doc, ["Lifecycle", "Distinct plans"], status_rows)
    if ap_state not in {"failed", "unavailable"} and facts["top_action_plans"]:
        # Adjacent Word tables can be interpreted as one table by compatible
        # renderers, causing the lifecycle header to repeat above selected
        # Action Plan rows on the next page.  A one-point separator preserves
        # the two independent table contracts without adding visible clutter.
        table_separator = doc.add_paragraph()
        table_separator.paragraph_format.space_before = Pt(0)
        table_separator.paragraph_format.space_after = Pt(0)
        table_separator.paragraph_format.line_spacing = Pt(1)
        compact_action_rows = [
            [row[0], row[1], row[2], row[4], row[5], row[6], row[8]]
            for row in facts["top_action_plans"]
        ]
        add_banded_top_n_table(
            doc,
            ["Record ID", "Account", "Owner", "Status", "Due", "Age (days)", "Priority"],
            compact_action_rows,
        )
        detail_heading = doc.add_paragraph("Selected-plan detail (title and next action):")
        detail_heading.paragraph_format.keep_with_next = True
        for row in facts["top_action_plans"]:
            detail = doc.add_paragraph(style="List Number")
            detail.add_run(f"{row[0]} — {row[3]}. ").bold = True
            detail.add_run(f"Next action: {row[7]}")
    elif ap_state not in {"failed", "unavailable"}:
        doc.add_paragraph("No open, blocked, due-soon, overdue, or unknown-status Action Plans were found.")
    if ap["missing_title"] or ap["missing_record_id"]:
        doc.add_paragraph(
            f"Data quality: {ap['missing_title']} plan(s) use “Title unavailable”; "
            f"{ap['missing_record_id']} plan(s) lack a stable source ID."
        )
    _add_source_reference(doc, "kpi.action_plans_*; chart.action_plan_status.*")

    heading = "Top Team-Member Summary" if facts["scope_type"] == "team" else "Top Account Summary"
    doc.add_heading(heading, level=2)
    if facts["scope_type"] == "team":
        doc.add_paragraph(
            "Ranked by overdue Action Plans, then open Action Plans, barriers, TAC volume, and team-member name."
        )
        add_banded_top_n_table(
            doc,
            ["Team member", "Customers", "Open AP", "Overdue AP", "Barriers", "TAC"],
            facts["member_summary"],
        )
        if facts.get("member_summary_omitted"):
            doc.add_paragraph(
                f"{facts['member_summary_omitted']} additional team-member row(s) are in the Source Data File."
            )
    else:
        doc.add_paragraph("Ranked by canonical risk score descending, then account name.")
        account_rows = []
        for row in facts["account_summary"]:
            risk_band = row[1]
            if risk_state in {"failed", "unavailable"}:
                risk_band = "Unavailable"
            elif risk_state in {"partial", "stale"}:
                risk_band = f"{risk_band} ({risk_state})"
            account_rows.append(
                [
                    row[0],
                    risk_band,
                    display_count(row[2], risk_state),
                    display_count(row[3], ap_state),
                    display_count(row[4], ap_state),
                    display_count(row[5], source_state("Adoption_Barriers")),
                    display_count(row[6], source_state("TAC_Cases")),
                ]
            )
        add_banded_top_n_table(
            doc,
            ["Account", "Risk band", "Risk score", "Open AP", "Overdue AP", "Critical/high barriers", "TAC"],
            account_rows,
        )
        if facts.get("account_summary_omitted"):
            doc.add_paragraph(
                f"{facts['account_summary_omitted']} additional account row(s) are in the Source Data File."
            )
    _add_source_reference(doc, "Member_Summary; Account_Summary; Risk_Components")

    decision_heading = doc.add_heading(
        "Risks, Decisions, and Recommended Next Actions",
        level=2,
    )
    decision_heading.paragraph_format.keep_with_next = True
    ranked_risks = sorted(
        facts["risk_profiles"].items(),
        key=lambda item: (-float(item[1].get("risk_score_0_100", 0.0) or 0.0), item[0]),
    )[:5]
    if ranked_risks:
        risk_rows = []
        for customer, profile in ranked_risks:
            recommendations = profile.get("recommendations") or [
                "No evidence-backed recommendation is available; resolve the disclosed evidence gaps."
            ]
            risk_rows.append(
                [
                    customer,
                    profile.get("risk_band", "UNKNOWN"),
                    profile.get("risk_score_0_100", 0.0),
                    str(recommendations[0]),
                ]
            )
        add_banded_top_n_table(doc, ["Account", "Risk", "Score", "Evidence-backed next action"], risk_rows)
    else:
        doc.add_paragraph("No customer-level risk profile could be calculated for this scope.")
    doc.add_paragraph(
        "Decision focus: resolve overdue or blocked plans first. Escalate only where structured evidence supports "
        "it, and close evidence gaps before drawing customer or operational conclusions."
    )
    _add_source_reference(doc, "kpi.high_risk_customers; chart.risk_distribution.*; Risk_Components")

    lineage_note = doc.add_paragraph()
    lineage_note.paragraph_format.space_before = Pt(0)
    lineage_note.paragraph_format.space_after = Pt(0)
    lineage_note.paragraph_format.keep_together = True
    lineage_label = lineage_note.add_run("Source and Lineage Note — ")
    lineage_label.bold = True
    lineage_label.font.color.rgb = RGBColor(0x00, 0x7B, 0xC7)
    lineage_label.font.size = Pt(8.5)
    lineage_detail = lineage_note.add_run(
        "Complete selected-scope records and Metric_Lineage are in the separately named AdoptIQ Source Data File."
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
        errors.append(
            "Report_Info scope, provenance, warning, or source-state content differs "
            "from canonical facts"
        )
    fingerprint_rows = report_info.loc[
        report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str)
        == "Fact_Contract_SHA256"
    ] if isinstance(report_info, pd.DataFrame) else pd.DataFrame()
    if len(fingerprint_rows) != 1:
        errors.append("Report_Info must contain exactly one Fact_Contract_SHA256 row")
    elif str(fingerprint_rows.iloc[0].get("Value") or "") != expected_fingerprint:
        errors.append("Source Data fact fingerprint differs from canonical facts")
    for sheet_name, expected_digest in expected_digests.items():
        digest_item = f"Sheet_SHA256:{sheet_name}"
        digest_rows = report_info.loc[
            report_info.get("Item", pd.Series(dtype=str)).fillna("").astype(str)
            == digest_item
        ]
        if len(digest_rows) != 1:
            errors.append(f"Report_Info must contain exactly one {digest_item} row")
        elif str(digest_rows.iloc[0].get("Value") or "") != expected_digest:
            errors.append(f"Report_Info digest differs for {sheet_name}")

    ap = facts["action_plan_lifecycle"]
    if sum(ap["bucket_counts"].values()) != ap["total"]:
        errors.append("Action Plan lifecycle buckets do not partition the distinct total")
    ap_chart = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == "action_plan_status_aging"]
    if int(ap_chart["Value"].fillna(0).sum()) != ap["total"]:
        errors.append("Action Plan chart series does not reconcile to lifecycle total")
    risk_chart = facts["chart_data"].loc[facts["chart_data"]["Chart_ID"] == "risk_distribution"]
    if int(risk_chart["Value"].fillna(0).sum()) != int(facts["risk_summary"].get("total_customers", 0)):
        errors.append("risk chart does not partition the scored customer universe")

    action_sheet = sheets.get("Action_Plans", pd.DataFrame())
    if len(action_sheet) != int(ap["total"]):
        errors.append(f"Action_Plans sheet rows {len(action_sheet)} != canonical total {ap['total']}")
    if "AdoptIQ_Status_Bucket" in action_sheet.columns:
        sheet_buckets = action_sheet["AdoptIQ_Status_Bucket"].fillna("Unknown").value_counts().to_dict()
        if any(int(sheet_buckets.get(key, 0)) != int(value) for key, value in ap["bucket_counts"].items()):
            errors.append("Action_Plans sheet lifecycle buckets differ from canonical facts")
    elif ap["total"]:
        errors.append("Action_Plans sheet lacks AdoptIQ_Status_Bucket")

    bems_sheet = sheets.get("BEMS", pd.DataFrame())
    if len(bems_sheet) != int(facts["kpis"]["bems"]):
        errors.append(f"BEMS sheet rows {len(bems_sheet)} != canonical BEMS KPI {facts['kpis']['bems']}")

    chart_sheet = sheets.get("Chart_Data", pd.DataFrame())
    chart_sheet_visible = chart_sheet.loc[
        chart_sheet.get("Metric_Key", pd.Series(dtype="object")).fillna("").astype(str).ne("")
    ] if isinstance(chart_sheet, pd.DataFrame) else pd.DataFrame()
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
            if str(expected_chart.loc[metric_key, "Source_State"]) != str(
                actual_chart.loc[metric_key, "Source_State"]
            ):
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
                native_id = _first_value(row, native_candidates)
                if native_id and not _clean_token(row.get("Record_ID")):
                    errors.append(f"{sheet_name} contains a source ID without public Record_ID")
                    break

    lineage = sheets.get("Metric_Lineage", pd.DataFrame())
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

    risk_components = sheets.get("Risk_Components", pd.DataFrame())
    expected_risk_customers = int(facts["risk_summary"].get("total_customers", 0))
    actual_risk_customers = (
        int(risk_components["Customer"].dropna().astype(str).nunique())
        if "Customer" in risk_components.columns
        else 0
    )
    if actual_risk_customers != expected_risk_customers:
        errors.append(
            f"Risk_Components customer count {actual_risk_customers} != scored universe {expected_risk_customers}"
        )

    word_result = None
    if doc is not None:
        word_result = validate_word_content(doc, word_budget=word_budget)
        errors.extend(word_result["errors"])
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
        "required_sheets": sorted(required),
        "chart_series_count": int(len(facts["chart_data"])),
        "lineage_count": int(len(lineage)),
    }


__all__ = [
    "FORBIDDEN_RAW_WORD_HEADINGS",
    "SOURCE_DATA_SHEET_NAMES",
    "TOP_ITEM_LIMIT_DEFAULT",
    "WORD_BUDGET_DEFAULT",
    "aggregate_team_frames",
    "build_concise_word_document",
    "build_report_facts",
    "build_source_data_sheets",
    "document_word_count",
    "fact_contract_fingerprint",
    "partition_portfolio_by_member",
    "source_data_path_for_word",
    "validate_cross_artifact_contract",
    "validate_written_source_workbook",
    "validate_word_content",
    "write_source_data_workbook",
]
