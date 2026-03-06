"""Deterministic weighted risk scoring used across all report types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from data_normalization import (
    add_case_lifecycle_fields,
    detect_bems_mask,
    normalize_priority_label,
    normalize_severity_label,
    normalize_status_label,
)
from report_utils import format_inline_source


@dataclass(frozen=True)
class RiskWeights:
    adoption_barriers: float = 0.28
    support_cases: float = 0.27
    customer_pulse: float = 0.15
    action_plans: float = 0.10
    incidents: float = 0.08
    contract: float = 0.08
    engagement: float = 0.04


def _clamp(value: float, floor: float = 0.0, ceiling: float = 100.0) -> float:
    return max(floor, min(ceiling, float(value)))


def _severity_weight(value: Any) -> int:
    sev = normalize_severity_label(value)
    return {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}.get(sev, 0)


def _priority_weight(value: Any) -> int:
    pri = normalize_priority_label(value)
    return {"P1": 4, "P2": 3, "P3": 2, "P4": 1}.get(pri, 0)


def _risk_band(score_0_100: float) -> str:
    if score_0_100 >= 75:
        return "CRITICAL"
    if score_0_100 >= 55:
        return "HIGH"
    if score_0_100 >= 35:
        return "MEDIUM"
    if score_0_100 >= 15:
        return "LOW"
    return "HEALTHY"


def _exclude_backfill_pulse_rows(customer_pulse: Optional[pd.DataFrame]) -> pd.DataFrame:
    if customer_pulse is None or customer_pulse.empty:
        return pd.DataFrame()
    use = customer_pulse.copy()
    if "PULSE_BACKFILL" not in use.columns:
        return use
    backfill_series = use["PULSE_BACKFILL"]
    if pd.api.types.is_bool_dtype(backfill_series):
        backfill_mask = backfill_series.fillna(False)
    else:
        backfill_mask = (
            backfill_series.fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"1", "true", "yes", "y", "on"})
        )
    return use[~backfill_mask]


def _score_adoption_barriers(customer_ab: pd.DataFrame) -> Dict[str, Any]:
    if customer_ab is None or customer_ab.empty:
        return {"score": 0.0, "details": {"count": 0, "critical_high_count": 0, "open_count": 0, "aging_open_count": 0}}

    use = customer_ab.copy()
    if "severity_norm" not in use.columns:
        sev_col = next((c for c in ("SEVERITY_C", "severity_c", "Severity") if c in use.columns), None)
        use["severity_norm"] = use[sev_col].apply(normalize_severity_label) if sev_col else "Unknown"
    if "status_norm" not in use.columns:
        status_col = next((c for c in ("AB_STATUS_C", "STATUS_C", "Status") if c in use.columns), None)
        use["status_norm"] = use[status_col].apply(normalize_status_label) if status_col else "Unknown"
    if "open_age_days" not in use.columns:
        date_col = next((c for c in ("OPEN_DATE_C", "CREATED_DATE", "CREATED_DATE_C", "CREATEDDATE") if c in use.columns), None)
        if date_col:
            dt = pd.to_datetime(use[date_col], errors="coerce")
            use["open_age_days"] = (pd.Timestamp(datetime.utcnow()) - dt).dt.days
        else:
            use["open_age_days"] = pd.NA

    count = len(use)
    severity_points = sum(_severity_weight(v) for v in use["severity_norm"]) / max(count * 4, 1) * 45
    open_count = int(use["status_norm"].eq("Open").sum())
    open_points = (open_count / max(count, 1)) * 30
    aging_open_count = int(((use["status_norm"].eq("Open")) & (pd.to_numeric(use["open_age_days"], errors="coerce") >= 60)).sum())
    aging_points = min(float(aging_open_count) * 8.0, 20.0)
    volume_points = min(float(count) * 2.0, 15.0)
    score = _clamp(severity_points + open_points + aging_points + volume_points)

    critical_high_count = int(use["severity_norm"].isin(["Critical", "High"]).sum())
    return {
        "score": score,
        "details": {
            "count": count,
            "critical_high_count": critical_high_count,
            "open_count": open_count,
            "aging_open_count": aging_open_count,
        },
    }


def _score_support_cases(customer_csone: pd.DataFrame) -> Dict[str, Any]:
    if customer_csone is None or customer_csone.empty:
        return {"score": 0.0, "details": {"count": 0, "escalated_count": 0, "bems_count": 0, "recent_count": 0}}

    use = add_case_lifecycle_fields(customer_csone)
    count = len(use)
    volume_points = min(float(count) * 2.5, 25.0)
    escalated_count = int(use["case_priority_norm"].isin(["P1", "P2"]).sum())
    escalated_points = min(float(escalated_count) * 9.0, 35.0)
    bems_count = int(use["is_bems"].sum()) if "is_bems" in use.columns else int(detect_bems_mask(use).sum())
    bems_points = min(float(bems_count) * 12.0, 30.0)

    recent_count = 0
    if "open_date" in use.columns:
        cutoff = pd.Timestamp(datetime.utcnow() - timedelta(days=30))
        recent_count = int((pd.to_datetime(use["open_date"], errors="coerce") >= cutoff).sum())
    recent_points = min(float(recent_count) * 2.5, 15.0)
    score = _clamp(volume_points + escalated_points + bems_points + recent_points)

    return {
        "score": score,
        "details": {
            "count": count,
            "escalated_count": escalated_count,
            "bems_count": bems_count,
            "recent_count": recent_count,
            "break_fix_count": int((use["case_type_class"] == "break_fix_technical").sum()) if "case_type_class" in use.columns else 0,
            "provisioning_count": int((use["case_type_class"] == "provisioning_request").sum()) if "case_type_class" in use.columns else 0,
        },
    }


def _score_customer_pulse(customer_pulse: pd.DataFrame) -> Dict[str, Any]:
    if customer_pulse is None or customer_pulse.empty:
        return {"score": 0.0, "details": {"count": 0, "poor_bad_count": 0, "backfill_excluded_count": 0}}

    raw_count = len(customer_pulse)
    use = _exclude_backfill_pulse_rows(customer_pulse)
    if use.empty:
        return {"score": 0.0, "details": {"count": 0, "poor_bad_count": 0, "backfill_excluded_count": raw_count}}
    rating_col = next((c for c in ("PULSE_RATING__C", "PULSE_RATING", "Rating", "RATING") if c in use.columns), None)
    if not rating_col:
        return {
            "score": 5.0,
            "details": {"count": len(use), "poor_bad_count": 0, "backfill_excluded_count": max(raw_count - len(use), 0)},
        }

    ratings = use[rating_col].fillna("").astype(str)
    poor_bad_count = int(ratings.str.contains(r"poor|bad|red|critical|high\s*risk", case=False, regex=True).sum())
    neutral_count = int(ratings.str.contains(r"neutral|fair|amber|yellow", case=False, regex=True).sum())
    count = len(use)
    poor_ratio = poor_bad_count / max(count, 1)
    neutral_ratio = neutral_count / max(count, 1)
    score = _clamp(poor_ratio * 100 + neutral_ratio * 30)
    return {
        "score": score,
        "details": {
            "count": count,
            "poor_bad_count": poor_bad_count,
            "backfill_excluded_count": max(raw_count - count, 0),
        },
    }


def _score_action_plans(action_plans: pd.DataFrame) -> Dict[str, Any]:
    if action_plans is None or action_plans.empty:
        return {"score": 0.0, "details": {"count": 0, "unresolved_count": 0}}
    use = action_plans.copy()
    status_col = next((c for c in ("STATUS_C", "STATUS__C", "Status") if c in use.columns), None)
    if status_col:
        resolved_mask = use[status_col].fillna("").astype(str).str.contains(
            r"closed|resolved|complete|done", case=False, regex=True
        )
        unresolved_count = int((~resolved_mask.fillna(False)).sum())
    else:
        unresolved_count = len(use)
    count = len(use)
    unresolved_ratio = unresolved_count / max(count, 1)
    score = _clamp(unresolved_ratio * 100)
    return {"score": score, "details": {"count": count, "unresolved_count": unresolved_count}}


def _score_incidents(ext_incidents: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not ext_incidents:
        return {"score": 0.0, "details": {"count": 0, "high_impact_count": 0}}
    count = len(ext_incidents)
    high_impact_count = sum(
        1
        for incident in ext_incidents
        if str(incident.get("status", "")).lower() in {"investigating", "identified", "monitoring", "major_outage"}
    )
    score = _clamp(min(count * 6.0 + high_impact_count * 12.0, 100.0))
    return {"score": score, "details": {"count": count, "high_impact_count": high_impact_count}}


def _score_contract(customer_subs: pd.DataFrame) -> Dict[str, Any]:
    if customer_subs is None or customer_subs.empty:
        return {"score": 8.0, "details": {"count": 0, "high_risk_subs": 0, "inactive_subs": 0}}
    use = customer_subs.copy()
    high_risk_subs = 0
    inactive_subs = 0
    if "RENEWAL_RISK_CATEGORY" in use.columns:
        high_risk_subs = int(use["RENEWAL_RISK_CATEGORY"].fillna("").astype(str).str.contains(r"high|critical", case=False, regex=True).sum())
    if "STATUS_C" in use.columns:
        inactive_subs = int(use["STATUS_C"].fillna("").astype(str).str.contains(r"inactive|expired|cancel", case=False, regex=True).sum())
    count = len(use)
    score = _clamp((high_risk_subs / max(count, 1)) * 70 + (inactive_subs / max(count, 1)) * 40)
    return {"score": score, "details": {"count": count, "high_risk_subs": high_risk_subs, "inactive_subs": inactive_subs}}


def _score_engagement(customer_ab: pd.DataFrame, customer_csone: pd.DataFrame, customer_pulse: pd.DataFrame, action_plans: pd.DataFrame) -> Dict[str, Any]:
    total_activity = (
        (0 if customer_ab is None else len(customer_ab))
        + (0 if customer_csone is None else len(customer_csone))
        + (0 if customer_pulse is None else len(customer_pulse))
        + (0 if action_plans is None else len(action_plans))
    )
    if total_activity == 0:
        return {"score": 30.0, "details": {"total_activity": 0}}
    if total_activity > 20:
        return {"score": 60.0, "details": {"total_activity": total_activity}}
    if total_activity > 10:
        return {"score": 40.0, "details": {"total_activity": total_activity}}
    return {"score": 15.0, "details": {"total_activity": total_activity}}


def compute_customer_risk_profile(
    customer_name: str,
    customer_ab: Optional[pd.DataFrame] = None,
    customer_csone: Optional[pd.DataFrame] = None,
    customer_pulse: Optional[pd.DataFrame] = None,
    customer_action_plans: Optional[pd.DataFrame] = None,
    customer_subs: Optional[pd.DataFrame] = None,
    ext_incidents: Optional[List[Dict[str, Any]]] = None,
    weights: RiskWeights = RiskWeights(),
) -> Dict[str, Any]:
    """Compute deterministic weighted customer risk score (0-100)."""
    pulse_input = customer_pulse if customer_pulse is not None else pd.DataFrame()
    pulse_for_scoring = _exclude_backfill_pulse_rows(pulse_input)
    ab_component = _score_adoption_barriers(customer_ab if customer_ab is not None else pd.DataFrame())
    support_component = _score_support_cases(customer_csone if customer_csone is not None else pd.DataFrame())
    pulse_component = _score_customer_pulse(pulse_input)
    action_component = _score_action_plans(customer_action_plans if customer_action_plans is not None else pd.DataFrame())
    incident_component = _score_incidents(ext_incidents)
    contract_component = _score_contract(customer_subs if customer_subs is not None else pd.DataFrame())
    engagement_component = _score_engagement(
        customer_ab if customer_ab is not None else pd.DataFrame(),
        customer_csone if customer_csone is not None else pd.DataFrame(),
        pulse_for_scoring,
        customer_action_plans if customer_action_plans is not None else pd.DataFrame(),
    )

    weighted_score = (
        ab_component["score"] * weights.adoption_barriers
        + support_component["score"] * weights.support_cases
        + pulse_component["score"] * weights.customer_pulse
        + action_component["score"] * weights.action_plans
        + incident_component["score"] * weights.incidents
        + contract_component["score"] * weights.contract
        + engagement_component["score"] * weights.engagement
    )
    score_0_100 = round(_clamp(weighted_score), 1)
    score_0_10 = round(score_0_100 / 10.0, 1)
    risk_band = _risk_band(score_0_100)

    risk_factors: List[str] = []
    if ab_component["details"].get("critical_high_count", 0) > 0:
        risk_factors.append(
            f"{ab_component['details']['critical_high_count']} critical/high adoption barriers "
            f"{format_inline_source('Adoption Barriers', fields=['SEVERITY_C', 'AB_STATUS_C'])}"
        )
    if support_component["details"].get("escalated_count", 0) > 0:
        risk_factors.append(
            f"{support_component['details']['escalated_count']} escalated TAC cases (P1/P2) "
            f"{format_inline_source('Support Cases (TAC)', fields=['Severity', 'Status', 'Case #'])}"
        )
    if support_component["details"].get("bems_count", 0) > 0:
        risk_factors.append(
            f"{support_component['details']['bems_count']} BEMS escalations "
            f"{format_inline_source('BEMS Escalations', fields=['Transaction ID', 'bemscsc_refs'])}"
        )
    if pulse_component["details"].get("poor_bad_count", 0) > 0:
        risk_factors.append(
            f"{pulse_component['details']['poor_bad_count']} poor/bad customer pulse records "
            f"{format_inline_source('Customer Pulse', fields=['PULSE_RATING__C'])}"
        )
    if action_component["details"].get("unresolved_count", 0) > 0:
        risk_factors.append(
            f"{action_component['details']['unresolved_count']} unresolved action plans "
            f"{format_inline_source('Action Plans', fields=['STATUS_C'])}"
        )

    key_findings = [
        (
            f"Adoption barriers analyzed: {ab_component['details'].get('count', 0)} "
            f"{format_inline_source('Adoption Barriers', fields=['SEVERITY_C', 'AB_STATUS_C'])}"
        ),
        (
            f"Support cases analyzed: {support_component['details'].get('count', 0)} "
            f"{format_inline_source('Support Cases (TAC)', fields=['Case #', 'Severity', 'Status'])}"
        ),
        (
            f"Customer pulse records analyzed: {pulse_component['details'].get('count', 0)} "
            f"{format_inline_source('Customer Pulse', fields=['PULSE_RATING__C'])}"
        ),
        (
            f"Derived risk score: {score_0_100}/100 ({risk_band}) "
            f"{format_inline_source('Derived Metric', source_override='Deterministic weighted AdoptIQ risk engine', verification_override='Recompute from normalized component metrics')}"
        ),
    ]

    recommendations: List[str] = []
    if risk_band in {"CRITICAL", "HIGH"}:
        recommendations.extend(
            [
                "Prioritize immediate executive review for top-risk accounts.",
                "Resolve open critical barriers and P1/P2 TAC cases with weekly tracking.",
                "Escalate BEMS break-fix cases to engineering owners with ETA commitments.",
            ]
        )
    elif risk_band == "MEDIUM":
        recommendations.extend(
            [
                "Create proactive remediation plans for unresolved barriers and TAC backlog.",
                "Increase customer touch cadence and track pulse trend changes.",
            ]
        )
    else:
        recommendations.extend(
            [
                "Maintain standard success cadence and monitor emerging risks.",
            ]
        )

    return {
        "customer_name": customer_name,
        "risk_score_0_100": score_0_100,
        "risk_score_0_10": score_0_10,
        "risk_band": risk_band,
        "components": {
            "adoption_barriers": ab_component,
            "support_cases": support_component,
            "customer_pulse": pulse_component,
            "action_plans": action_component,
            "incidents": incident_component,
            "contract": contract_component,
            "engagement": engagement_component,
        },
        "risk_factors": risk_factors,
        "key_findings": key_findings,
        "recommendations": recommendations,
    }


def compute_portfolio_risk_summary(
    risk_profiles: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Aggregate customer risk profiles into canonical portfolio-level metrics."""
    profiles = risk_profiles or {}
    if not profiles:
        return {
            "total_customers": 0,
            "average_risk_score_0_100": 0.0,
            "highest_risk_score_0_100": 0.0,
            "high_risk_customers": 0,
            "medium_risk_customers": 0,
            "low_risk_customers": 0,
            "healthy_customers": 0,
            "risk_band_counts": {
                "CRITICAL": 0,
                "HIGH": 0,
                "MEDIUM": 0,
                "LOW": 0,
                "HEALTHY": 0,
            },
        }

    scores: List[float] = []
    band_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "HEALTHY": 0}
    for profile in profiles.values():
        score = float(profile.get("risk_score_0_100", 0.0) or 0.0)
        band = str(profile.get("risk_band", _risk_band(score))).upper().strip()
        if band not in band_counts:
            band = _risk_band(score)
        band_counts[band] += 1
        scores.append(score)

    high_risk = band_counts["CRITICAL"] + band_counts["HIGH"]
    avg_score = round(sum(scores) / max(len(scores), 1), 1)
    max_score = round(max(scores) if scores else 0.0, 1)
    return {
        "total_customers": len(profiles),
        "average_risk_score_0_100": avg_score,
        "highest_risk_score_0_100": max_score,
        "high_risk_customers": high_risk,
        "medium_risk_customers": band_counts["MEDIUM"],
        "low_risk_customers": band_counts["LOW"],
        "healthy_customers": band_counts["HEALTHY"],
        "risk_band_counts": band_counts,
    }

