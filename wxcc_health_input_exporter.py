#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Round 134 — deterministic WxCC health-check input exporter (LLM-free).

Assembles Snowflake / CSOne / CSConsole / external-intel data into a
plain-text file for the WxCC Health Checks orchestrator.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import canonical_metrics as cm
from adoptiq_backend import (
    _apply_scope_filter_ab,
    _apply_scope_filter_csone,
    _connect_with_keeper,
    _filter_csconsole_data_by_technology,
    _prepare_csone,
    cross_reference_refs,
    fetch_adoption_barriers,
    fetch_help_webex_bugs,
    fetch_status_incidents,
    fetch_subscription_data,
    load_csone_excel,
    search_subscriptions_by_customer,
)
from config import Config
from data_normalization import (
    add_case_lifecycle_fields,
    customer_names_match,
    merge_customer_join_keys_dtype_safe,
    normalize_customer_name,
)
from risk_scoring import compute_customer_risk_profile
from snowflake_prefetch import AnalysisRunContext, prefetch_ask_ai_grounded

logger = logging.getLogger(__name__)

NOT_AVAILABLE = "Not available in source data"

_CUSTOMER_COLS: Tuple[str, ...] = (
    "BU_NAME",
    "Customer Name",
    "CUSTOMER_NAME",
    "RELATED_CUSTOMER__C",
    "customer_name",
    "Account Name",
    "USE_CASE_BU_NAME_C",
    "NAME",
)

_TECH_ALIASES: Dict[str, str] = {
    "wxcc": "Webex Contact Center",
    "wxcce": "Webex Contact Center Enterprise",
    "ucce": "Cisco UCCE",
    "uccx": "Cisco UCCX",
    "acc": "All Contact Center",
    "wem": "Webex Meetings & Messaging",
    "wec": "Webex Calling",
}

# Round 136: WxCC Health Check exporter is scoped to cloud WxCC only (not Enterprise/UCCE/ACC).
WXCC_HEALTH_CHECK_TECHNOLOGY = "Webex Contact Center"
_WXCC_HEALTH_CHECK_TECH_MSG = (
    "WxCC Health Check requires Webex Contact Center technology scope."
)

_SECTION_ORDER: Tuple[str, ...] = (
    "Queue performance",
    "Agent availability",
    "Platform and integrations",
    "Adoption barriers",
    "Support cases (TAC)",
    "Customer pulse",
    "Action plans",
    "Contract / renewal",
    "Additional notes",
)

_SEVERITY_ORDER: Dict[str, int] = {
    "P1": 0,
    "CRITICAL": 0,
    "P2": 1,
    "HIGH": 1,
    "P3": 2,
    "MEDIUM": 2,
    "MODERATE": 2,
    "P4": 3,
    "LOW": 3,
}


class WxccExportError(Exception):
  """Round 134: typed export failure with stable ``code`` for CLI/API."""

  def __init__(self, code: str, message: str) -> None:
    super().__init__(message)
    self.code = code
    self.message = message


@dataclass
class SourceDiagnostic:
    key: str
    label: str
    status: str  # used | unavailable | skipped
    row_count: int = 0
    reason: str = ""
    extras: Dict[str, str] = field(default_factory=dict)


@dataclass
class CustomerScope:
    canonical_name: str
    customer_query: str
    subscription_id: Optional[str]
    days: int
    team_subs_df: pd.DataFrame
    account_ids: List[str]


@dataclass
class WxccHealthContext:
    scope: CustomerScope
    technology: str
    days: int
    period_start: datetime
    period_end: datetime
    ab_df: pd.DataFrame
    csone_df: pd.DataFrame
    tac_df: pd.DataFrame
    action_plans_df: pd.DataFrame
    pulse_df: pd.DataFrame
    subs_slice: pd.DataFrame
    ext_incidents: List[Dict[str, Any]]
    matched_bugs: List[Dict[str, Any]]
    risk_profile: Optional[Dict[str, Any]]
    partial_data_warnings: List[Dict[str, Any]]
    source_diagnostics: List[SourceDiagnostic]


@dataclass
class ExportResult:
    text: str
    canonical_customer_name: str
    output_path: Optional[Path] = None
    partial_data_warnings: List[Dict[str, Any]] = field(default_factory=list)


def _customer_digest(name: str) -> str:
    return hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:12]


def normalize_technology_arg(raw: Optional[str]) -> str:
    """Map CLI/UI aliases (``wxcc``) to canonical ``Config.TECH_CHOICES`` labels."""
    if raw is None or not str(raw).strip():
        return "Webex Contact Center"
    text = str(raw).strip()
    compact = re.sub(r"[\s\-_]+", "", text.lower())
    if compact in _TECH_ALIASES:
        return _TECH_ALIASES[compact]
    for choice in Config.TECH_CHOICES:
        if choice.lower() == text.lower():
            return choice
    if text == "All Contact Center":
        return text
    return text


def validate_wxcc_health_check_technology(raw: Optional[str]) -> str:
    """Round 136: reject non-WxCC technology for the health-check export path."""
    canonical = normalize_technology_arg(raw)
    if canonical != WXCC_HEALTH_CHECK_TECHNOLOGY:
        raise WxccExportError("validation", _WXCC_HEALTH_CHECK_TECH_MSG)
    return canonical


def _record_diag(
    diags: List[SourceDiagnostic],
    *,
    key: str,
    label: str,
    status: str,
    row_count: int = 0,
    reason: str = "",
    extras: Optional[Dict[str, str]] = None,
) -> None:
    diags.append(
        SourceDiagnostic(
            key=key,
            label=label,
            status=status,
            row_count=row_count,
            reason=reason,
            extras=dict(extras or {}),
        )
    )


def _df_row_count(df: Optional[pd.DataFrame]) -> int:
    if df is None or df.empty:
        return 0
    return int(len(df))


def _ab_id_column(df: pd.DataFrame) -> Optional[str]:
    for col in ("ID", "Id", "RECORD_ID", "RECORD_ID_C"):
        if col in df.columns:
            return col
    return None


def _concat_dedupe_ab(*frames: Optional[pd.DataFrame]) -> pd.DataFrame:
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return pd.DataFrame()
    merged = pd.concat(parts, ignore_index=True)
    id_col = _ab_id_column(merged)
    if id_col:
        merged = merged.drop_duplicates(subset=[id_col], keep="first")
    return merged


def slice_df_by_customer(
    df: Optional[pd.DataFrame],
    customer_name: str,
    team_subs_df: pd.DataFrame,
) -> pd.DataFrame:
    """Round 134: alias-aware customer slice (not exact-string equality)."""
    if df is None:
        return pd.DataFrame()
    if df.empty or not customer_name:
        return df.copy()
    for col in _CUSTOMER_COLS:
        if col not in df.columns:
            continue
        try:
            mask = df[col].apply(
                lambda value: customer_names_match(value, customer_name, team_subs_df=team_subs_df)
            )
            if mask.any():
                return df.loc[mask].copy()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Round 134: slice failed for column %s: %s", col, exc)
    # Retain attrs such as ``fetch_error`` so the risk engine can distinguish
    # unavailable source evidence from an observed zero-row result.
    return df.iloc[0:0].copy()


def _filter_customer_tagged_incidents(
    ext_incidents: Optional[List[Dict[str, Any]]],
    customer_name: str,
) -> List[Dict[str, Any]]:
    """Mirror app_simple._r65_filter_customer_tagged_incidents without importing app_simple."""
    if not ext_incidents or not customer_name:
        return list(ext_incidents or [])
    norm_target = normalize_customer_name(customer_name)
    if not norm_target:
        return list(ext_incidents)
    has_tagging_field = False
    for inc in ext_incidents:
        if not isinstance(inc, dict):
            continue
        for field_name in ("customer_id", "customer_name", "BU_NAME"):
            if inc.get(field_name):
                has_tagging_field = True
                break
        if has_tagging_field:
            break
    if not has_tagging_field:
        return list(ext_incidents)
    matched: List[Dict[str, Any]] = []
    for inc in ext_incidents:
        if not isinstance(inc, dict):
            continue
        for field_name in ("customer_id", "customer_name", "BU_NAME"):
            val = inc.get(field_name)
            if val and normalize_customer_name(str(val)) == norm_target:
                matched.append(inc)
                break
    return matched


def _combined_tac_df(csone_df: pd.DataFrame, support_cases_df: pd.DataFrame) -> pd.DataFrame:
    if csone_df is not None and not csone_df.empty:
        return csone_df
    return support_cases_df if support_cases_df is not None else pd.DataFrame()


def _severity_rank(value: Any) -> int:
    token = str(value or "").strip().upper()
    return _SEVERITY_ORDER.get(token, 99)


def _first_present_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _cell_str(value: Any, limit: int = 280) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def resolve_customer_scope(
    *,
    customer: Optional[str] = None,
    subscription_id: Optional[str] = None,
    days: int = 90,
) -> CustomerScope:
    """Resolve team subscription frame for a single customer (Compact parity)."""
    try:
        days_i = int(days)
    except (TypeError, ValueError) as exc:
        raise WxccExportError("validation", "days must be an integer between 1 and 365") from exc
    if days_i < 1 or days_i > 365:
        raise WxccExportError("validation", "days must be between 1 and 365")

    customer_q = (customer or "").strip()
    sub_id = (subscription_id or "").strip() or None
    if not sub_id and not customer_q:
        raise WxccExportError("validation", "customer_name or subscription_id is required")

    if sub_id:
        sub_data = fetch_subscription_data(sub_id, days_i)
        if not sub_data or not sub_data.get("found"):
            raise WxccExportError("customer_not_found", f"No subscription found for id '{sub_id}'")
        canonical = sub_data.get("customer_name") or "Unknown"
        team_subs_df = pd.DataFrame(
            {
                "BU_NAME": [canonical],
                "ACCOUNT_ID_C": [sub_data.get("account_id")],
                "SUBSCRIPTION_ID": [sub_id],
                "CSSM_EMAIL": [sub_data.get("cssm_email", "") or ""],
            }
        )
        logger.info(
            "Round 134 / wxcc export scope: subscription digest=%s customer_digest=%s",
            _customer_digest(sub_id),
            _customer_digest(canonical),
        )
    else:
        sub_results = search_subscriptions_by_customer(customer_q, limit=50)
        if not sub_results:
            raise WxccExportError("customer_not_found", f"No subscriptions found for customer '{customer_q}'")
        canonical = sub_results[0].get("BU_NAME") or customer_q
        same_customer = [r for r in sub_results if (r.get("BU_NAME") or "") == canonical]
        if not same_customer:
            same_customer = [sub_results[0]]
        team_subs_df = pd.DataFrame(
            {
                "BU_NAME": [r.get("BU_NAME") or canonical for r in same_customer],
                "ACCOUNT_ID_C": [r.get("ACCOUNT_ID_C") for r in same_customer],
                "SUBSCRIPTION_ID": [r.get("SUBSCRIPTION_ID") for r in same_customer],
                "CSSM_EMAIL": [r.get("CSSM_EMAIL", "") or "" for r in same_customer],
            }
        )
        logger.info(
            "Round 134 / wxcc export scope: customer_digest=%s subs=%d",
            _customer_digest(canonical),
            len(same_customer),
        )

    account_ids = (
        team_subs_df["ACCOUNT_ID_C"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().unique().tolist()
        if "ACCOUNT_ID_C" in team_subs_df.columns
        else []
    )
    return CustomerScope(
        canonical_name=canonical,
        customer_query=customer_q or canonical,
        subscription_id=sub_id,
        days=days_i,
        team_subs_df=team_subs_df,
        account_ids=account_ids,
    )


def fetch_customer_datasets(
    scope: CustomerScope,
    technology: str,
    *,
    csone_path: Optional[str] = None,
    csone_sync_status: Optional[str] = None,
) -> WxccHealthContext:
    """Fetch and slice all datasets for one customer export."""
    diags: List[SourceDiagnostic] = []
    warnings: List[Dict[str, Any]] = []
    tech = normalize_technology_arg(technology)
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=scope.days)

    ctx = _connect_with_keeper()
    if ctx is None:
        _record_diag(
            diags,
            key="snowflake",
            label="Snowflake connection",
            status="unavailable",
            reason="snowflake_connect_failed",
        )
        raise WxccExportError("snowflake_connect_failed", "Snowflake connection unavailable")

    _record_diag(
        diags,
        key="snowflake_subscriptions",
        label="Snowflake subscriptions",
        status="used",
        row_count=_df_row_count(scope.team_subs_df),
        extras={"account_ids": str(len(scope.account_ids))},
    )

    owner_emails: List[str] = []
    if "CSSM_EMAIL" in scope.team_subs_df.columns:
        owner_emails = (
            scope.team_subs_df["CSSM_EMAIL"].dropna().astype(str).str.strip().str.lower().unique().tolist()
        )

    prefetch_bundle: Dict[str, Any] = {}
    try:
        run_ctx = AnalysisRunContext.build(
            ctx,
            scope.account_ids,
            scope.days,
            customer_names=[scope.canonical_name],
            owner_emails=owner_emails,
        )
        prefetch_bundle = prefetch_ask_ai_grounded(run_ctx) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Round 134: prefetch failed for digest=%s: %s", _customer_digest(scope.canonical_name), exc)
        warnings.append({"kind": "prefetch_failed", "message": type(exc).__name__})

    cs_ab = prefetch_bundle.get("csconsole_adoption_barriers", pd.DataFrame())
    cs_ap = prefetch_bundle.get("csconsole_action_plans", pd.DataFrame())
    cs_pulse = prefetch_bundle.get("csconsole_customer_pulse", pd.DataFrame())
    support_cases_sf = prefetch_bundle.get("support_cases_snowflake", pd.DataFrame())

    ab_account = pd.DataFrame()
    if scope.account_ids:
        try:
            ab_account = fetch_adoption_barriers(ctx, scope.account_ids, scope.days)
        except Exception as exc:  # noqa: BLE001
            warnings.append({"kind": "adoption_barriers_fetch_failed", "message": str(exc)[:200]})
            _record_diag(
                diags,
                key="snowflake_ab_fetch",
                label="Snowflake adoption_barriers (AB fetch)",
                status="unavailable",
                reason=type(exc).__name__,
            )
    else:
        _record_diag(
            diags,
            key="snowflake_ab_fetch",
            label="Snowflake adoption_barriers (AB fetch)",
            status="unavailable",
            reason="no_account_ids",
        )

    ab_merged = _concat_dedupe_ab(cs_ab, ab_account)
    if not ab_merged.empty and "ACCOUNT_ID_C" in ab_merged.columns and not scope.team_subs_df.empty:
        try:
            ab_merged = merge_customer_join_keys_dtype_safe(
                ab_merged,
                scope.team_subs_df,
                right_columns=("ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Round 134: AB BU_NAME merge failed: %s", exc)

    ab_scoped = _apply_scope_filter_ab(ab_merged, tech, scope.days)
    cs_ap_scoped = _filter_csconsole_data_by_technology(cs_ap, tech) if not cs_ap.empty else cs_ap
    cs_pulse_scoped = _filter_csconsole_data_by_technology(cs_pulse, tech) if not cs_pulse.empty else cs_pulse

    csone_raw = pd.DataFrame()
    if csone_path:
        try:
            csone_raw = load_csone_excel(Path(csone_path))
            csone_raw = _prepare_csone(csone_raw, scope.team_subs_df)
            csone_raw = _apply_scope_filter_csone(csone_raw, tech, scope.days)
            _record_diag(
                diags,
                key="csone",
                label="CSOne TAC/BEMS",
                status="used",
                row_count=_df_row_count(csone_raw),
                extras={
                    "basename": Path(csone_path).name,
                    "sync": csone_sync_status or "provided",
                },
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append({"kind": "csone_load_failed", "message": str(exc)[:200]})
            _record_diag(
                diags,
                key="csone",
                label="CSOne TAC/BEMS",
                status="unavailable",
                reason=type(exc).__name__,
                extras={"basename": Path(csone_path).name},
            )
    else:
        _record_diag(
            diags,
            key="csone",
            label="CSOne TAC/BEMS",
            status="unavailable",
            reason=csone_sync_status or "csone_path_not_provided",
        )

    sf_attrs = getattr(support_cases_sf, "attrs", {}) or {}
    sf_kind = str(sf_attrs.get("fetch_error_kind") or "")
    _record_diag(
        diags,
        key="snowflake_support_cases",
        label="Snowflake support_cases (TAC)",
        status="used" if _df_row_count(support_cases_sf) or not sf_kind else "unavailable",
        row_count=_df_row_count(support_cases_sf),
        reason=sf_kind,
        extras={
            k: str(sf_attrs.get(k, ""))
            for k in ("fetch_error_kind", "scope_account_count", "scope_window_days")
            if sf_attrs.get(k) is not None
        },
    )

    for key, label, frame in (
        ("snowflake_csconsole_ap", "Snowflake CSConsole action_plans", cs_ap_scoped),
        ("snowflake_csconsole_pulse", "Snowflake CSConsole customer_pulse", cs_pulse_scoped),
        ("snowflake_csconsole_ab", "Snowflake CSConsole adoption_barriers", ab_scoped),
    ):
        _record_diag(
            diags,
            key=key,
            label=label,
            status="used",
            row_count=_df_row_count(frame),
        )

    ext_incidents: List[Dict[str, Any]] = []
    try:
        ext_incidents = list(fetch_status_incidents(days_back=scope.days) or [])
        _record_diag(
            diags,
            key="status_incidents",
            label="status.webex.com incidents",
            status="used",
            row_count=len(ext_incidents),
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append({"kind": "incidents_fetch_failed", "message": str(exc)[:200]})
        _record_diag(
            diags,
            key="status_incidents",
            label="status.webex.com incidents",
            status="unavailable",
            reason=type(exc).__name__,
        )

    ext_bugs: List[Dict[str, Any]] = []
    try:
        ext_bugs = list(fetch_help_webex_bugs() or [])
        _record_diag(
            diags,
            key="help_bugs",
            label="help.webex.com defects",
            status="used",
            row_count=len(ext_bugs),
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append({"kind": "bugs_fetch_failed", "message": str(exc)[:200]})
        _record_diag(
            diags,
            key="help_bugs",
            label="help.webex.com defects",
            status="unavailable",
            reason=type(exc).__name__,
        )

    customer = scope.canonical_name
    ab_slice = slice_df_by_customer(ab_scoped, customer, scope.team_subs_df)
    csone_slice = slice_df_by_customer(csone_raw, customer, scope.team_subs_df)
    ap_slice = slice_df_by_customer(cs_ap_scoped, customer, scope.team_subs_df)
    pulse_slice = slice_df_by_customer(cs_pulse_scoped, customer, scope.team_subs_df)
    sf_slice = slice_df_by_customer(support_cases_sf, customer, scope.team_subs_df)
    subs_slice = slice_df_by_customer(scope.team_subs_df, customer, scope.team_subs_df)
    if subs_slice.empty:
        subs_slice = scope.team_subs_df.copy()

    tac_slice = _combined_tac_df(csone_slice, sf_slice)
    tac_norm = add_case_lifecycle_fields(tac_slice) if not tac_slice.empty else tac_slice

    matched_bugs: List[Dict[str, Any]] = []
    if ext_bugs:
        _matches, matched_df = cross_reference_refs(ab_slice, tac_norm, ext_bugs)
        if matched_df is not None and not matched_df.empty:
            matched_bugs = matched_df.to_dict(orient="records")

    filtered_incidents = _filter_customer_tagged_incidents(ext_incidents, customer)
    risk_profile: Optional[Dict[str, Any]] = None
    try:
        risk_profile = compute_customer_risk_profile(
            customer_name=customer,
            customer_ab=ab_slice,
            customer_csone=tac_norm,
            customer_pulse=pulse_slice,
            customer_action_plans=ap_slice,
            customer_subs=subs_slice,
            ext_incidents=filtered_incidents,
            recent_window_days=scope.days,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append({"kind": "risk_profile_failed", "message": type(exc).__name__})

    return WxccHealthContext(
        scope=scope,
        technology=tech,
        days=scope.days,
        period_start=period_start,
        period_end=period_end,
        ab_df=ab_slice,
        csone_df=csone_slice,
        tac_df=tac_norm,
        action_plans_df=ap_slice,
        pulse_df=pulse_slice,
        subs_slice=subs_slice,
        ext_incidents=filtered_incidents,
        matched_bugs=matched_bugs,
        risk_profile=risk_profile,
        partial_data_warnings=warnings,
        source_diagnostics=diags,
    )


def _render_debug_sources(ctx: WxccHealthContext) -> List[str]:
    lines = ["Data sources used (debug):"]
    used_any = False
    for diag in ctx.source_diagnostics:
        if diag.status != "used":
            continue
        used_any = True
        extra_bits = []
        for k, v in diag.extras.items():
            if v:
                extra_bits.append(f"{k}={v}")
        suffix = f" | {' | '.join(extra_bits)}" if extra_bits else ""
        lines.append(f"- {diag.label}: used | {diag.row_count} row(s){suffix}")
    if not used_any:
        lines.append(f"- {NOT_AVAILABLE}")

    lines.append("")
    lines.append("Data sources unavailable (debug):")
    unavail = [d for d in ctx.source_diagnostics if d.status == "unavailable"]
    if not unavail:
        lines.append("- (none)")
    else:
        for diag in unavail:
            reason = diag.reason or "unavailable"
            lines.append(f"- {diag.key}: {reason}")
    lines.append("---")
    return lines


def _render_top_barriers(ab_df: pd.DataFrame, limit: int = 5) -> List[str]:
    if ab_df is None or ab_df.empty:
        return [f"- {NOT_AVAILABLE}"]
    work = ab_df.copy()
    sev_col = _first_present_column(work, ("SEVERITY_C", "Severity", "severity", "AB_SEVERITY_C"))
    age_col = _first_present_column(work, ("open_age_days", "Age (Days)", "DAYS_OPEN_C", "OPEN_AGE_DAYS"))
    title_col = _first_present_column(work, tuple(Config.LIKELY_TITLE_COLS))
    if sev_col:
        work["_sev_rank"] = work[sev_col].map(_severity_rank)
    else:
        work["_sev_rank"] = 99
    if age_col:
        work["_age"] = pd.to_numeric(work[age_col], errors="coerce").fillna(-1)
    else:
        work["_age"] = -1
    work = work.sort_values(["_sev_rank", "_age"], ascending=[True, False])
    lines: List[str] = []
    for _, row in work.head(limit).iterrows():
        title = _cell_str(row.get(title_col)) if title_col else "Adoption barrier"
        sev = _cell_str(row.get(sev_col)) if sev_col else ""
        age = _cell_str(row.get(age_col)) if age_col else ""
        meta = ", ".join(x for x in (f"Severity: {sev}" if sev else "", f"Days open: {age}" if age else "") if x)
        lines.append(f"- {title}" + (f" ({meta})" if meta else ""))
    return lines or [f"- {NOT_AVAILABLE}"]


def _render_top_cases(tac_df: pd.DataFrame, limit: int = 5) -> List[str]:
    if tac_df is None or tac_df.empty:
        return [f"- {NOT_AVAILABLE}"]
    work = tac_df.copy()
    sev_col = _first_present_column(work, ("Severity", "severity", "case_priority_norm", "Priority"))
    title_col = _first_present_column(work, ("Title", "TITLE", "SUBJECT", "SUBJECT_C", "Problem Details"))
    id_col = _first_present_column(work, ("Case #", "SR Number", "CaseNumber", "CASE_NUMBER"))
    if sev_col:
        work["_sev_rank"] = work[sev_col].map(_severity_rank)
    else:
        work["_sev_rank"] = 99
    work = work.sort_values("_sev_rank", ascending=True)
    lines: List[str] = []
    for _, row in work.head(limit).iterrows():
        case_id = _cell_str(row.get(id_col)) if id_col else ""
        title = _cell_str(row.get(title_col)) if title_col else "Support case"
        sev = _cell_str(row.get(sev_col)) if sev_col else ""
        prefix = f"[{case_id}] " if case_id else ""
        suffix = f" (Severity: {sev})" if sev else ""
        lines.append(f"- {prefix}{title}{suffix}")
    return lines or [f"- {NOT_AVAILABLE}"]


def _render_pulse(pulse_df: pd.DataFrame, limit: int = 3) -> List[str]:
    if pulse_df is None or pulse_df.empty:
        return [f"- {NOT_AVAILABLE}"]
    rating_col = _first_present_column(
        pulse_df,
        ("CUSTOMER_PULSE__C", "PULSE_RATING__C", "PULSE_RATING", "rating", "Customer Pulse"),
    )
    comment_col = _first_present_column(
        pulse_df,
        ("COMMENTS__C", "Comments", "COMMENTS", "DESCRIPTION_C", "DESCRIPTION"),
    )
    lines: List[str] = []
    for _, row in pulse_df.head(limit).iterrows():
        rating = _cell_str(row.get(rating_col)) if rating_col else ""
        comment = _cell_str(row.get(comment_col), limit=400) if comment_col else ""
        if rating and comment:
            lines.append(f"- Pulse: {rating} — {comment}")
        elif rating:
            lines.append(f"- Pulse: {rating}")
        elif comment:
            lines.append(f"- {comment}")
    return lines or [f"- {NOT_AVAILABLE}"]


def _render_action_plans(ap_df: pd.DataFrame, limit: int = 3) -> List[str]:
    if ap_df is None or ap_df.empty:
        return [f"- {NOT_AVAILABLE}"]
    title_col = _first_present_column(ap_df, ("SUBJECT_C", "ACTION_PLAN_TITLE_C", "Title", "NAME"))
    status_col = _first_present_column(ap_df, ("Status", "STATUS", "STAGE", "State"))
    lines: List[str] = []
    for _, row in ap_df.head(limit).iterrows():
        title = _cell_str(row.get(title_col)) if title_col else "Action plan"
        status = _cell_str(row.get(status_col)) if status_col else ""
        lines.append(f"- {title}" + (f" (Status: {status})" if status else ""))
    return lines or [f"- {NOT_AVAILABLE}"]


def _render_contract(subs_df: pd.DataFrame) -> List[str]:
    if subs_df is None or subs_df.empty:
        return [f"- {NOT_AVAILABLE}"]
    row = subs_df.iloc[0]
    lines: List[str] = []
    for label, candidates in (
        ("Subscription status", ("STATUS_C", "STATUS", "Subscription_Status")),
        ("Renewal risk category", ("RENEWAL_RISK_CATEGORY", "Renewal_Risk_Category")),
        ("ARR", ("ARR", "ARR_AMOUNT", "ANNUAL_RECURRING_REVENUE")),
    ):
        col = _first_present_column(subs_df, candidates)
        if col:
            val = _cell_str(row.get(col))
            if val:
                lines.append(f"- {label}: {val}")
    return lines or [f"- {NOT_AVAILABLE}"]


def build_wxcc_health_text(ctx: WxccHealthContext) -> str:
    """Render deterministic WxCC input plain text from a populated context."""
    customer = ctx.scope.canonical_name
    start_s = ctx.period_start.strftime("%Y-%m-%d")
    end_s = ctx.period_end.strftime("%Y-%m-%d")

    header: List[str] = [
        "---",
        f"WxCC Contact Center Health Snapshot ({customer})",
        f"Reporting period: {start_s} to {end_s} ({ctx.days} days)",
        f"Technology scope: {ctx.technology}",
    ]
    if ctx.risk_profile:
        score = ctx.risk_profile.get("risk_score_0_100")
        band = ctx.risk_profile.get("risk_band")
        if score is not None and band:
            header.append(f"Risk score: {score} ({band})")
    header.extend(_render_debug_sources(ctx))

    open_ab = cm.count_open_barriers(ctx.ab_df)
    crit_ab = cm.count_critical_barriers(ctx.ab_df)
    open_ap = cm.count_open_action_plans(ctx.ab_df, ap_df=ctx.action_plans_df)
    total_tac = cm.count_total_tac(ctx.tac_df)
    open_tac = cm.count_open_tac(ctx.tac_df)
    p1 = cm.count_p1(ctx.tac_df)
    bems = cm.count_bems(ctx.tac_df, ab_df=ctx.ab_df)
    pulse_total = cm.count_total_customer_pulse(ctx.pulse_df)

    sections: Dict[str, List[str]] = {
        "Queue performance": [
            f"- Queue metrics: {NOT_AVAILABLE}",
            f"- Service level / ASA: {NOT_AVAILABLE}",
        ],
        "Agent availability": [
            f"- Agent states: {NOT_AVAILABLE}",
            f"- Longest wait / callbacks pending: {NOT_AVAILABLE}",
        ],
        "Platform and integrations": [
            f"- Service incidents (portfolio window): {len(ctx.ext_incidents)}",
            f"- Known defects cross-referenced to customer AB/TAC: {len(ctx.matched_bugs)}",
        ],
        "Adoption barriers": [
            f"- Open adoption barriers: {open_ab}",
            f"- Critical/high adoption barriers: {crit_ab}",
            *_render_top_barriers(ctx.ab_df),
        ],
        "Support cases (TAC)": [
            f"- Total TAC cases: {total_tac}",
            f"- Open TAC cases: {open_tac}",
            f"- P1 cases: {p1}",
            f"- BEMS escalations: {bems}",
            *_render_top_cases(ctx.tac_df),
        ],
        "Customer pulse": [
            f"- Pulse records: {pulse_total}",
            *_render_pulse(ctx.pulse_df),
        ],
        "Action plans": [
            f"- Open action plans: {open_ap}",
            *_render_action_plans(ctx.action_plans_df),
        ],
        "Contract / renewal": _render_contract(ctx.subs_slice),
        "Additional notes": [],
    }

    if ctx.partial_data_warnings:
        for item in ctx.partial_data_warnings:
            kind = item.get("kind", "warning")
            msg = item.get("message", "")
            sections["Additional notes"].append(f"- Partial data ({kind}): {msg}")
    if not sections["Additional notes"]:
        sections["Additional notes"].append("- None")

    body: List[str] = []
    for name in _SECTION_ORDER:
        body.append(name)
        body.extend(sections[name])
        body.append("")

    return "\n".join(header + [""] + body).rstrip() + "\n"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def export_wxcc_health_input(
    *,
    customer: Optional[str] = None,
    subscription_id: Optional[str] = None,
    technology: str = "Webex Contact Center",
    days: int = 90,
    output_path: Optional[str | Path] = None,
    csone_path: Optional[str] = None,
    csone_sync_status: Optional[str] = None,
) -> ExportResult:
    """Orchestrate scope resolution, fetch, render, and optional atomic write."""
    technology = validate_wxcc_health_check_technology(technology)
    scope = resolve_customer_scope(customer=customer, subscription_id=subscription_id, days=days)
    ctx = fetch_customer_datasets(
        scope,
        technology,
        csone_path=csone_path,
        csone_sync_status=csone_sync_status,
    )
    text = build_wxcc_health_text(ctx)

    has_signal = any(
        [
            _df_row_count(ctx.ab_df),
            _df_row_count(ctx.tac_df),
            _df_row_count(ctx.pulse_df),
            _df_row_count(ctx.action_plans_df),
            len(ctx.ext_incidents),
        ]
    )
    if not has_signal:
        raise WxccExportError(
            "empty_export",
            "No customer data available for export after fetch and slice",
        )

    written: Optional[Path] = None
    if output_path is not None:
        written = Path(output_path).expanduser().resolve()
        _atomic_write_text(written, text)

    return ExportResult(
        text=text,
        canonical_customer_name=scope.canonical_name,
        output_path=written,
        partial_data_warnings=list(ctx.partial_data_warnings),
    )


def safe_download_filename(customer_name: str) -> str:
    slug = re.sub(r"[^\w\-.]+", "_", (customer_name or "customer").strip())[:80]
    return f"{slug or 'customer'}_wxcc_health_input.txt"
