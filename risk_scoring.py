"""Deterministic weighted risk scoring used across all report types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from data_normalization import (
    _clean_name_for_key,
    add_case_lifecycle_fields,
    coalesce_nonempty_columns,
    detect_bems_mask,
    normalize_customer_name,
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
    """Clamp a numeric value into [floor, ceiling].

    Round 7 / Phase 3.1: explicitly reject NaN/Inf so a non-finite
    composite score cannot silently fall into the HEALTHY band.
    Callers must handle ``ValueError`` (typically by surfacing a
    ``partial_data_warnings`` entry rather than emitting a "low risk"
    band that does not actually reflect the data).
    """
    f = float(value)
    if not math.isfinite(f):
        raise ValueError(
            f"Round 7 / Phase 3.1: refusing to clamp non-finite risk value: {value!r}"
        )
    return max(floor, min(ceiling, f))


def _severity_weight(value: Any) -> int:
    sev = normalize_severity_label(value)
    return {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}.get(sev, 0)


def _priority_weight(value: Any) -> int:
    pri = normalize_priority_label(value)
    return {"P1": 4, "P2": 3, "P3": 2, "P4": 1}.get(pri, 0)


def _canonicalize_renewal_category(value: Any) -> str:
    """Map a free-text renewal-risk category to a canonical bucket
    among {"critical","high","medium","low","healthy",""}.

    Round 3: replaces the previous ``str.contains(r"high|critical")``
    regex which over-matched (e.g. "Highest Quality" → high) and
    under-matched common abbreviations.
    """
    text = "" if value is None else str(value).strip().lower()
    if not text or text in {"nan", "none", "null"}:
        return ""
    if text.startswith("crit") or text in {"crt", "p1"}:
        return "critical"
    if text.startswith("high") and not text.startswith("highest"):
        return "high"
    # "Highest" treated as a literal extreme of high, not as "high quality".
    if text == "highest":
        return "critical"
    if text.startswith("med") or text in {"moderate", "amber"}:
        return "medium"
    if text.startswith("low") or text in {"minor"}:
        return "low"
    if text in {"healthy", "green", "good"}:
        return "healthy"
    return ""


def _canonicalize_subscription_status(value: Any) -> str:
    """Map a free-text subscription status into
    {"active","inactive","expired","cancelled","suspended",
     "terminated","provisioning","unknown",""}.

    Replaces a substring regex that would match "active-cancellation"
    as both active and cancellation simultaneously.

    Round 3 / Phase 3.6: previously "TERMINATED" silently fell through
    to "" (unknown) and "PROVISIONING" was equally invisible; both
    were therefore excluded from the inactive count yet not counted
    as active either, which understated true contract risk. Map
    them explicitly:

      - terminated -> "terminated" (counts as inactive for risk)
      - provisioning -> "provisioning" (NOT active yet; flagged
        separately so reports can disclose it)
      - any other non-empty text -> "unknown" so callers can choose
        to flag the data quality issue rather than treat it as
        active by default.
    """
    text = "" if value is None else str(value).strip().lower()
    if not text or text in {"nan", "none", "null"}:
        return ""
    if "cancel" in text:
        return "cancelled"
    if "termin" in text:
        return "terminated"
    if "provision" in text:
        return "provisioning"
    if "expir" in text:
        return "expired"
    if "suspend" in text:
        return "suspended"
    if text == "inactive" or "inactive" in text:
        return "inactive"
    if text == "active" or text.startswith("active"):
        return "active"
    return "unknown"


def _canonicalize_pulse_rating(value: Any) -> str:
    """Map a free-text pulse rating to {"poor","neutral","positive",""}.

    Round 3: replaces the previous substring regex inside
    ``_score_customer_pulse`` so the poor/neutral counts here always
    align with the canonical narrative bucketing used elsewhere.
    """
    text = "" if value is None else str(value).strip().lower()
    if not text or text in {"nan", "none", "null"}:
        return ""
    poor_tokens = (
        "poor", "very poor", "bad", "very bad", "red", "critical",
        "high risk", "high-risk", "needs improvement", "negative",
    )
    if any(tok in text for tok in poor_tokens):
        return "poor"
    neutral_tokens = (
        "neutral", "fair", "amber", "yellow", "moderate", "average",
    )
    if any(tok in text for tok in neutral_tokens):
        return "neutral"
    positive_tokens = (
        "good", "great", "green", "excellent", "positive", "strong",
        "healthy",
    )
    if any(tok in text for tok in positive_tokens):
        return "positive"
    return ""


# Canonical 0-100 risk-band thresholds. Exposed so chart code (e.g. the
# renewal donut) can pick wedge colors from the same numbers as the
# textual band label below — preventing a score of 72 from being drawn
# orange while the same figure labels it CRITICAL.
RISK_BAND_THRESHOLDS = {
    "CRITICAL": 75,
    "HIGH": 55,
    "MEDIUM": 35,
    "LOW": 15,
}
# A benign health conclusion is withheld until at least half of the configured
# signal weight is usable.  This aligns the publish/no-publish boundary with
# the MEDIUM confidence boundary: a minority of the evidence may identify a
# concrete severe condition (handled by guardrails below), but it is not enough
# to establish that a customer is healthy.  This is a conservative operating
# threshold, not a claim of predictive calibration, and should be revisited
# with authorized outcome data.
MIN_EVIDENCE_COVERAGE_FOR_HEALTH_ASSESSMENT = 0.50
# Within a non-empty source, fewer than half of the logical records being
# interpretable is a feed-quality failure, not partial proof of health.
MIN_COMPONENT_RECORD_COMPLETENESS_FOR_SCORING = 0.50
# Round 5 / Phase 5.17: band edges are inclusive on the LOWER bound
# and exclusive on the UPPER bound, ie. a band is the half-open
# interval ``[lo, hi)`` on the 0-100 scale (which is ``[lo/10, hi/10)``
# on the 0-10 displayed scale).  Spelled out:
#
#     CRITICAL : [75, 100]   (or [7.5, 10] on the 0-10 scale)
#     HIGH     : [55, 75)    (or [5.5, 7.5))
#     MEDIUM   : [35, 55)    (or [3.5, 5.5))
#     LOW      : [15, 35)    (or [1.5, 3.5))
#     HEALTHY  : [ 0, 15)    (or [0.0, 1.5))
#
# Narrative wording (Executive Takeaway, donut legends, recommendation
# headers) MUST use this convention so a customer scored 5.5 lands in
# HIGH (not MEDIUM) and a customer scored 7.5 lands in CRITICAL (not
# HIGH).  Any new chart / sentence that introduces a different
# inclusive/exclusive boundary is a bug.
RISK_BAND_EDGES_DOC = (
    "Bands are half-open intervals [lo, hi) on the 0-100 scale: "
    "CRITICAL>=75, HIGH 55-74.999, MEDIUM 35-54.999, LOW 15-34.999, "
    "HEALTHY 0-14.999. Equivalent on the 0-10 scale: CRITICAL>=7.5, "
    "HIGH 5.5-7.499, MEDIUM 3.5-5.499, LOW 1.5-3.499, HEALTHY 0-1.499."
)


def get_default_risk_band_thresholds() -> dict:
    """Return a copy of the canonical RISK_BAND_THRESHOLDS dictionary.

    Round 6 / Phase 5.16: callers that need a *fallback* when the
    canonical import path is unavailable (e.g. excel writers in a
    partially-frozen bundle) should call this helper so the fallback
    values track any future tuning of the thresholds.  Encoding the
    numbers inline as ``{"HIGH": 55, "MEDIUM": 35, ...}`` is forbidden
    because it silently drifts from the canonical mapping above.
    """
    return dict(RISK_BAND_THRESHOLDS)


# Round 7 / Phase 6.6: renewal-risk scoring constants, lifted out of
# ``advanced_renewal_analyzer._calculate_renewal_risk_score``.  These
# replace the literal magic numbers (10, 15, 20, 25, 30 increments and
# the 100_000 / 10_000 ARR gates) that previously lived inline so any
# future tuning happens in exactly one place and the renewal narrative
# stays consistent with the rest of the platform's risk story.  Keep
# the dict shape stable -- ``advanced_renewal_analyzer`` reads the keys
# by name; renames are breaking changes.
RENEWAL_RISK_INCREMENTS = {
    # Contract risk
    "expiring_contract_per_unit": 10,
    "manual_renewal_majority": 15,
    "auto_renewal_majority_credit": -10,
    # Financial risk
    "high_value_arr_credit": -15,
    "low_value_arr_penalty": 10,
    "high_discount_penalty": 20,
    # Usage / adoption risk
    "low_completion_penalty": 25,
    "high_completion_credit": -20,
    "low_engagement_penalty": 20,
    "high_engagement_credit": -15,
    # Support engagement
    "low_support_engagement_penalty": 15,
    "high_support_engagement_credit": -10,
    "low_support_completion_penalty": 15,
    # Adoption health
    "poor_adoption_health_penalty": 30,
    "good_adoption_health_credit": -20,
    "many_high_severity_barriers_penalty": 25,
}

# Round 28: formal contract for RENEWAL_ARR_THRESHOLDS consumers.
#
# Currency-bearing keys (``high_value_arr``, ``low_value_arr``,
# ``high_discount_pct``) are denominated in the basis currency stamped
# at ``RENEWAL_ARR_THRESHOLDS["currency_basis"]`` (USD).  Consumers MUST:
#
#   1. Skip the threshold compare when
#      ``financial_metrics["is_multi_currency"]`` is True OR
#      ``financial_metrics["currency"].upper() != currency_basis``.
#   2. When skipping, append a disclosure factor naming the customer's
#      currency and the basis currency so the omission is visible in
#      the rendered narrative (e.g. ``"ARR gates skipped: customer
#      currency EUR differs from threshold basis USD"``).
#
# Keys that score dimensionless ratios or 0-100 scores
# (``low_completion_rate``, ``high_completion_rate``,
# ``low_engagement_score``, ``high_engagement_score``,
# ``low_support_completion_rate``, ``poor_adoption_health_score``,
# ``good_adoption_health_score``, ``many_high_severity_barriers``,
# ``starting_renewal_score``) are NOT subject to the currency contract
# above -- they apply uniformly regardless of currency.
#
# This contract is enforced by Round 28 parametric tests under
# ``tests/test_round28_renewal_arr_thresholds_multicurrency_parametric.py``.
RENEWAL_ARR_THRESHOLDS = {
    # ARR (annual recurring revenue) thresholds in *unspecified*
    # currency (advanced_renewal_analyzer is currency-aware as of
    # Round 7 / Phase 6.5; when ``is_multi_currency`` is True these
    # gates are not applied).  Numbers carried over from the legacy
    # inline literals so existing report semantics are preserved.
    # Round 13 / Phase 1.7: explicitly stamp these gates as
    # USD-equivalent so downstream callers know to either:
    #   (a) skip the gate entirely when ``financial_metrics.currency``
    #       is non-USD or ``is_multi_currency`` is True, or
    #   (b) convert the customer's portfolio total to USD-equivalent
    #       before comparing.  Mirrors the multi-currency disclosure
    #       contract added in Phase 1.1/1.3.
    # Round 28: AdoptIQ ships strategy (a) -- skip-the-gate -- because
    #   we do not have a sourced/dated FX feed; faking one would make
    #   reports less honest.  Future rounds may add an opt-in
    #   FX-normalize path behind a config flag without breaking this
    #   contract.
    "currency_basis": "USD",  # Round 13 / Phase 1.7; reaffirmed Round 28
    "high_value_arr": 100_000,
    "low_value_arr": 10_000,
    "high_discount_pct": 50,
    "low_completion_rate": 0.5,
    "high_completion_rate": 0.8,
    "low_engagement_score": 30,
    "high_engagement_score": 70,
    "low_support_completion_rate": 0.6,
    "poor_adoption_health_score": 50,
    "good_adoption_health_score": 80,
    "many_high_severity_barriers": 3,
    "starting_renewal_score": 50,
}


def _risk_band(score_0_100: float) -> str:
    if score_0_100 >= RISK_BAND_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    if score_0_100 >= RISK_BAND_THRESHOLDS["HIGH"]:
        return "HIGH"
    if score_0_100 >= RISK_BAND_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    if score_0_100 >= RISK_BAND_THRESHOLDS["LOW"]:
        return "LOW"
    return "HEALTHY"


# Round 123 / Build 92: canonical band -> letter-grade SSoT.
# The Comprehensive per-customer ``Customer Health Score: <letter>`` and the
# ``Portfolio Health Score`` were historically LLM-discretion and could
# contradict the canonical risk band on the SAME report (a HEALTHY customer
# graded ``F``, a HIGH customer graded ``C``).  This mapping is the single
# source of truth that grounds the letter to the canonical band so the
# narrative grade always agrees with the ``Risk_Components`` sheet.
_HEALTH_GRADE_BY_BAND = {
    "HEALTHY": "A",
    "LOW": "B",
    "MEDIUM": "C",
    "MODERATE": "C",  # display-vocabulary alias for the MEDIUM band
    "HIGH": "D",
    "CRITICAL": "F",
    "UNKNOWN": "N/A",
}


def band_to_health_grade(band, score=None) -> str:
    """Map a canonical risk band to a single health-grade letter A-F.

    Round 123 / Build 92.  ``band`` is one of the canonical labels
    (``HEALTHY`` / ``LOW`` / ``MEDIUM`` / ``MODERATE`` / ``HIGH`` /
    ``CRITICAL``, case- and whitespace-insensitive).  When ``band`` is
    missing or unrecognised, fall back to deriving the band from
    ``score`` (a 0-100 risk score) via :func:`_risk_band` so the helper
    never returns an out-of-range grade.  Defaults to ``A`` only when
    neither a usable band nor a usable score is supplied (the
    no-signal / healthiest assumption, matching the HEALTHY band).

    Pure function; no I/O.  This is the SSoT both the per-customer and
    the portfolio health-grade stamps consult so the letter can never
    drift from the canonical band.
    """
    if band is not None:
        key = str(band).upper().strip()
        if key in _HEALTH_GRADE_BY_BAND:
            return _HEALTH_GRADE_BY_BAND[key]
    if score is not None:
        try:
            return _HEALTH_GRADE_BY_BAND[_risk_band(float(score))]
        except (TypeError, ValueError, KeyError):
            pass
    return "A"


def health_grade_for_profile(profile) -> str:
    """Resolve the canonical health-grade letter for a risk profile dict.

    Round 123 / Build 92.  Reads ``risk_band`` (preferred) and falls
    back to ``risk_score_0_100`` from a profile dict produced by
    :func:`compute_customer_risk_profile`.  Returns ``A`` for an empty
    / non-dict profile so the caller never has to guard the shape.
    """
    if not isinstance(profile, dict):
        return "A"
    if str(profile.get("risk_assessment_state") or "").upper() == "UNAVAILABLE":
        return "N/A"
    return band_to_health_grade(
        profile.get("risk_band"),
        profile.get("risk_score_0_100"),
    )


def _exclude_backfill_pulse_rows(customer_pulse: Optional[pd.DataFrame]) -> pd.DataFrame:
    import canonical_metrics as cm

    return cm.exclude_backfilled_pulse_rows(customer_pulse)


def _score_adoption_barriers(customer_ab: pd.DataFrame) -> Dict[str, Any]:
    if customer_ab is None or customer_ab.empty:
        return {
            "score": 0.0,
            "details": {
                "count": 0,
                "critical_high_count": 0,
                "open_count": 0,
                "aging_open_count": 0,
                "data_state": "observed_empty",
                "evidence_fraction": 1.0,
            },
        }

    use = customer_ab.copy()
    severity_values, _ = coalesce_nonempty_columns(
        use,
        ("severity_norm", "SEVERITY_C", "severity_c", "Severity", "PRIORITY_C", "Priority"),
    )
    status_values, _ = coalesce_nonempty_columns(
        use,
        ("status_norm", "AB_STATUS_C", "STATUS_C", "Status", "STATUS"),
    )
    use["severity_norm"] = severity_values.map(normalize_severity_label)
    use["status_norm"] = status_values.map(normalize_status_label)

    existing_age = (
        pd.to_numeric(use["open_age_days"], errors="coerce")
        if "open_age_days" in use.columns
        else pd.Series(float("nan"), index=use.index)
    )
    date_values, date_columns = coalesce_nonempty_columns(
        use,
        ("OPEN_DATE_C", "OPEN_DATE", "CREATED_DATE", "CREATED_DATE_C", "CREATEDDATE"),
    )
    if date_columns:
        dt = pd.to_datetime(date_values, errors="coerce", utc=True)
        _now_utc = pd.Timestamp(datetime.now(timezone.utc))
        computed_age = (_now_utc - dt).dt.days
        use["open_age_days"] = existing_age.fillna(computed_age)
    else:
        use["open_age_days"] = existing_age

    history_conflicts: List[str] = []
    if "ID" in use.columns:
        ids = use["ID"].fillna("").astype(str).str.strip()
        positions = pd.Series(range(len(use)), index=use.index)
        keys = ids.str.upper().where(
            ids.ne(""), positions.map(lambda pos: f"__ROW_WITHOUT_ID__{pos}")
        )
        updated = pd.Series(pd.NaT, index=use.index, dtype="datetime64[ns, UTC]")
        for candidate in (
            "LAST_MODIFIED_DATE",
            "LASTMODIFIEDDATE",
            "UPDATED_AT",
            "UPDATED_DATE",
            "CLOSED_DATE_C",
            "CLOSED_DATE",
            "OPEN_DATE_C",
            "CREATED_DATE",
        ):
            if candidate in use.columns:
                updated = updated.fillna(
                    pd.to_datetime(use[candidate], errors="coerce", utc=True)
                )
        severity_rank = use["severity_norm"].map(
            {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
        ).fillna(0)
        open_rank = use["status_norm"].eq("Open").astype(int)
        ordering = pd.DataFrame(
            {
                "record_key": keys,
                "updated": updated,
                "open_rank": open_rank,
                "severity_rank": severity_rank,
                "row_position": positions,
            }
        ).sort_values(
            ["record_key", "updated", "open_rank", "severity_rank", "row_position"],
            ascending=[True, False, False, False, True],
            na_position="last",
            kind="mergesort",
        )
        history_signals = pd.DataFrame(
            {
                "barrier_id": ids.str.upper(),
                "severity": use["severity_norm"],
                "status": use["status_norm"],
            }
        )
        history_signals = history_signals.loc[
            history_signals["barrier_id"].ne("")
        ]
        if not history_signals.empty:
            history_counts = history_signals.groupby(
                "barrier_id", sort=True
            ).agg(
                severity_states=("severity", "nunique"),
                status_states=("status", "nunique"),
            )
            history_conflicts = history_counts.index[
                (history_counts["severity_states"] > 1)
                | (history_counts["status_states"] > 1)
            ].tolist()
        selected = (
            ordering.drop_duplicates("record_key", keep="first")["row_position"]
            .sort_values()
            .astype(int)
            .tolist()
        )
        use = use.iloc[selected].copy().reset_index(drop=True)

    import canonical_metrics as cm  # local import avoids module-cycle risk

    # Round 53.1: score logical adoption-barrier records, not Snowflake fan-out
    # rows. Duplicate assignee/detail rows with the same ID should not inflate
    # risk factors or push the customer into a higher band.
    count = cm.count_total_barriers(use)
    known_status = use["status_norm"].isin({"Open", "Closed"})
    known_severity = use["severity_norm"].isin(
        {"Critical", "High", "Medium", "Low"}
    )
    assessed_mask = known_status & known_severity
    assessed_count = int(assessed_mask.sum())
    unknown_status_count = int((~known_status).sum())
    unknown_severity_count = int((~known_severity).sum())
    assessed_fraction = assessed_count / max(count, 1)
    evidence_fraction = (
        (int(known_status.sum()) + int(known_severity.sum()))
        / max(count * 2, 1)
    )

    if "ID" in use.columns:
        def _record_key(row: pd.Series) -> str:
            raw = row.get("ID")
            if pd.notna(raw) and str(raw).strip():
                return f"id::{str(raw).strip()}"
            return f"row::{row.name}"

        use["_r531_record_key"] = use.apply(_record_key, axis=1)
    else:
        use["_r531_record_key"] = [f"row::{idx}" for idx in use.index]
    scored_use = use.loc[assessed_mask].copy()

    # pandas 3 preserves the source StringDtype through ``Series.apply`` in
    # cases where pandas 2 produced an integer series.  A subsequent groupby
    # sum can therefore concatenate strings (or return ``''`` for an empty
    # group), making ``float(sum)`` fail.  Normalize the deterministic weight
    # to numeric explicitly; unknown values remain the established zero.
    _severity_weights = pd.to_numeric(
        scored_use["severity_norm"].apply(_severity_weight),
        errors="coerce",
    ).fillna(0.0).where(
        scored_use["status_norm"].eq("Open"), other=0.0
    )
    record_severity_weight = _severity_weights.groupby(
        scored_use["_r531_record_key"]
    ).max()
    severity_points = (
        float(record_severity_weight.sum()) / max(assessed_count * 4, 1) * 45
    )

    open_count = cm.count_open_barriers(scored_use)
    open_points = (open_count / max(assessed_count, 1)) * 30
    _aging_mask = (
        scored_use["status_norm"].eq("Open")
        & (pd.to_numeric(scored_use["open_age_days"], errors="coerce") >= 60)
    )
    aging_open_count = int(
        scored_use.loc[_aging_mask, "_r531_record_key"].nunique()
    )
    aging_points = min(float(aging_open_count) * 8.0, 20.0)
    volume_points = min(float(assessed_count) * 2.0, 15.0)
    computed_score = _clamp(
        severity_points + open_points + aging_points + volume_points
    )
    score: Optional[float] = (
        computed_score
        if assessed_fraction >= MIN_COMPONENT_RECORD_COMPLETENESS_FOR_SCORING
        else None
    )

    active_mask = scored_use["status_norm"].eq("Open")
    critical_high_count = int(
        (
            active_mask
            & scored_use["severity_norm"].isin(["Critical", "High"])
        ).sum()
    )
    critical_high_all_count = cm.count_critical_barriers(
        scored_use, mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH
    )
    critical_count = int(
        (active_mask & scored_use["severity_norm"].eq("Critical")).sum()
    )
    high_count = int(
        (active_mask & scored_use["severity_norm"].eq("High")).sum()
    )
    return {
        "score": score,
        "details": {
            "count": count,
            "critical_high_count": critical_high_count,
            "critical_high_all_count": critical_high_all_count,
            "critical_count": critical_count,
            "high_count": high_count,
            "open_count": open_count,
            "aging_open_count": aging_open_count,
            "history_conflict_ids": history_conflicts[:50],
            "assessed_count": assessed_count,
            "unknown_status_count": unknown_status_count,
            "unknown_severity_count": unknown_severity_count,
            "evidence_fraction": round(evidence_fraction, 3),
            "data_state": (
                "unusable"
                if score is None
                else ("partial" if assessed_count < count else "observed")
            ),
            "data_state_reason": (
                f"only {assessed_count} of {count} adoption barriers had both a recognized status and severity"
                if assessed_count < count
                else ""
            ),
            "excluded_from_score": score is None,
        },
    }


def _score_support_cases(
    customer_csone: pd.DataFrame,
    *,
    recent_window_days: int = 30,
) -> Dict[str, Any]:
    """Phase 3.4: ``recent_window_days`` is now thread-able from the
    caller's analysis horizon. Previously this used a hardcoded
    ``timedelta(days=30)`` no matter what window the report covered,
    which contradicted the report header (e.g. a 90-day report still
    quoted "30-day" momentum risk in narrative).
    """
    if customer_csone is None or customer_csone.empty:
        return {
            "score": 0.0,
            "details": {
                "count": 0,
                "escalated_count": 0,
                "bems_count": 0,
                "recent_count": 0,
                "recent_window_days": int(recent_window_days),
            },
        }

    import canonical_metrics as cm  # local import avoids any future cycle

    logical_cases = cm.deduplicate_tac_cases(customer_csone)
    dedup_diag = dict(getattr(logical_cases, "attrs", {}).get("tac_dedup") or {})
    use = add_case_lifecycle_fields(logical_cases)
    count = len(use)
    status = use.get(
        "case_status_norm", pd.Series("Unknown", index=use.index)
    ).fillna("Unknown").astype(str)
    priority = use.get(
        "case_priority_norm", pd.Series("Unknown", index=use.index)
    ).fillna("Unknown").astype(str)
    known_status = status.isin({"Open", "Closed"})
    known_priority = priority.isin({"P1", "P2", "P3", "P4"})
    assessed_mask = status.eq("Closed") | (status.eq("Open") & known_priority)
    assessed_count = int(assessed_mask.sum())
    unknown_status_count = int((~known_status).sum())
    unknown_priority_count = int((known_status & ~known_priority).sum())
    assessed_fraction = assessed_count / max(count, 1)
    evidence_fraction = (
        (int(known_status.sum()) + int(known_priority.sum()))
        / max(count * 2, 1)
    )
    scored_use = use.loc[assessed_mask].copy()
    scored_priority = priority.loc[assessed_mask]
    open_mask = scored_use.get(
        "is_open", pd.Series(False, index=scored_use.index)
    ).fillna(False).astype(bool)
    active_p1_count = int((open_mask & scored_priority.eq("P1")).sum())
    active_p2_count = int((open_mask & scored_priority.eq("P2")).sum())
    active_escalated_count = active_p1_count + active_p2_count
    open_count = int(open_mask.sum())
    volume_points = min(float(open_count) * 3.0, 25.0)
    # Keep the all-period counts for traceability, but calibrate current risk
    # from unresolved/current cases so a closed 2020 P1 does not look urgent.
    escalated_count = int(cm.count_escalated(scored_use))
    escalated_points = min(float(active_escalated_count) * 18.0, 45.0)
    bems_count = (
        int(scored_use["is_bems"].sum())
        if "is_bems" in scored_use.columns
        else int(detect_bems_mask(scored_use).sum())
    )
    active_bems_count = int(
        (
            open_mask
            & scored_use.get(
                "is_bems", pd.Series(False, index=scored_use.index)
            ).fillna(False).astype(bool)
        ).sum()
    )
    bems_points = min(float(active_bems_count) * 22.0, 35.0)

    recent_count = 0
    if "open_date" in scored_use.columns:
        # Round 13 / Phase 2.3: parse the lifecycle ``open_date`` with
        # utc=True so rows that carry explicit offsets (or were already
        # parsed tz-aware upstream) compare cleanly against a UTC
        # cutoff.  The previous implementation forced everything to
        # tz-naive via ``tz_localize(None)``, which both hid timezone
        # bugs and shifted the cutoff by up to 24h depending on the
        # worker's local zone.
        cutoff = pd.Timestamp(
            datetime.now(timezone.utc) - timedelta(days=int(recent_window_days))
        )
        recent_count = int(
            (
                pd.to_datetime(
                    scored_use["open_date"], errors="coerce", utc=True
                )
                >= cutoff
            ).sum()
        )
    recent_points = min(float(recent_count) * 1.5, 15.0)
    computed_score = _clamp(
        volume_points + escalated_points + bems_points + recent_points
    )
    score: Optional[float] = (
        computed_score
        if assessed_fraction >= MIN_COMPONENT_RECORD_COMPLETENESS_FOR_SCORING
        else None
    )

    return {
        "score": score,
        "details": {
            "count": count,
            "open_count": open_count,
            "escalated_count": escalated_count,
            "active_escalated_count": active_escalated_count,
            "active_p1_count": active_p1_count,
            "active_p2_count": active_p2_count,
            "bems_count": bems_count,
            "active_bems_count": active_bems_count,
            "recent_count": recent_count,
            "recent_window_days": int(recent_window_days),
            "break_fix_count": int((scored_use["case_type_class"] == "break_fix_technical").sum()) if "case_type_class" in scored_use.columns else 0,
            "provisioning_count": int((scored_use["case_type_class"] == "provisioning_request").sum()) if "case_type_class" in scored_use.columns else 0,
            "duplicates_removed": int(dedup_diag.get("duplicates_removed", 0) or 0),
            "conflicting_case_ids": list(dedup_diag.get("conflicting_case_ids") or []),
            "assessed_count": assessed_count,
            "unknown_status_count": unknown_status_count,
            "unknown_priority_count": unknown_priority_count,
            "evidence_fraction": round(evidence_fraction, 3),
            "data_state": (
                "unusable"
                if score is None
                else (
                    "partial"
                    if unknown_status_count or unknown_priority_count
                    else "observed"
                )
            ),
            "data_state_reason": (
                f"{unknown_status_count} support cases had unknown lifecycle and {unknown_priority_count} had unknown priority"
                if unknown_status_count or unknown_priority_count
                else ""
            ),
            "excluded_from_score": score is None,
        },
    }


def _score_customer_pulse(customer_pulse: pd.DataFrame) -> Dict[str, Any]:
    if customer_pulse is None or customer_pulse.empty:
        return {
            "score": None,
            "details": {
                "count": 0,
                "poor_bad_count": 0,
                "backfill_excluded_count": 0,
                "data_state": "observed_empty",
                "data_state_reason": "no customer-pulse records were observed",
                "excluded_from_score": True,
            },
        }

    import canonical_metrics as cm

    logical_pulses = cm.deduplicate_customer_pulse(customer_pulse)
    dedup_diag = dict(
        getattr(logical_pulses, "attrs", {}).get("customer_pulse_dedup") or {}
    )
    use = _exclude_backfill_pulse_rows(logical_pulses)
    backfill_excluded_count = int(len(logical_pulses) - len(use))
    if use.empty:
        return {
            "score": None,
            "details": {
                "count": 0,
                "poor_bad_count": 0,
                "backfill_excluded_count": backfill_excluded_count,
                "duplicates_removed": int(
                    dedup_diag.get("duplicates_removed", 0) or 0
                ),
                "data_state": "backfill_only",
                "excluded_from_score": True,
            },
        }

    score_col = next(
        (c for c in ("SCORE__C", "SCORE", "PULSE_SCORE") if c in use.columns),
        None,
    )
    rating_col = next(
        (
            c
            for c in (
                "PULSE_RATING__C",
                "PULSE_RATING",
                "Rating",
                "RATING",
                "rating",  # Round 100: curated Compact Customer_Pulse export.
                "Customer Pulse",
                "Customer Pulse Color",
            )
            if c in use.columns
        ),
        None,
    )
    if score_col is None and rating_col is None:
        return {
            "score": None,
            "details": {
                "count": len(use),
                "poor_bad_count": 0,
                "backfill_excluded_count": backfill_excluded_count,
                "duplicates_removed": int(
                    dedup_diag.get("duplicates_removed", 0) or 0
                ),
                "no_rating_column": True,
                "data_state": "unusable",
                "excluded_from_score": True,
            },
        }

    from canonical_metrics import (
        PULSE_NEGATIVE_THRESHOLD_0_TO_10 as _NEG,
        PULSE_POSITIVE_THRESHOLD_0_TO_10 as _POS,
    )

    buckets = pd.Series("", index=use.index, dtype=str)
    numeric = (
        pd.to_numeric(use[score_col], errors="coerce")
        if score_col is not None
        else pd.Series(float("nan"), index=use.index)
    )
    finite_numeric = numeric.map(
        lambda value: bool(pd.notna(value) and math.isfinite(float(value)))
    )
    numeric_mask = finite_numeric & numeric.between(0, 10, inclusive="both")
    invalid_numeric_count = int((numeric.notna() & ~numeric_mask).sum())
    buckets.loc[numeric_mask & (numeric <= _NEG)] = "poor"
    buckets.loc[numeric_mask & (numeric > _NEG) & (numeric < _POS)] = "neutral"
    buckets.loc[numeric_mask & (numeric >= _POS)] = "positive"
    if rating_col is not None:
        rating_buckets = use[rating_col].fillna("").astype(str).map(_canonicalize_pulse_rating)
        fallback_mask = ~numeric_mask & rating_buckets.ne("")
        buckets.loc[fallback_mask] = rating_buckets.loc[fallback_mask]

    valid = buckets.ne("")
    count = int(valid.sum())
    if count == 0:
        return {
            "score": None,
            "details": {
                "count": 0,
                "record_count": int(len(use)),
                "poor_bad_count": 0,
                "backfill_excluded_count": backfill_excluded_count,
                "duplicates_removed": int(
                    dedup_diag.get("duplicates_removed", 0) or 0
                ),
                "missing_rating_count": int(len(use)),
                "invalid_numeric_count": invalid_numeric_count,
                "data_state": "unusable",
                "data_state_reason": "no finite 0-10 score or recognized pulse rating was available",
                "excluded_from_score": True,
            },
        }
    poor_bad_count = int((buckets == "poor").sum())
    neutral_count = int((buckets == "neutral").sum())
    poor_ratio = poor_bad_count / max(count, 1)
    neutral_ratio = neutral_count / max(count, 1)
    computed_score = _clamp(poor_ratio * 100 + neutral_ratio * 30)
    missing_rating_count = int((~valid).sum())
    evidence_fraction = count / max(len(use), 1)
    score: Optional[float] = (
        computed_score
        if evidence_fraction >= MIN_COMPONENT_RECORD_COMPLETENESS_FOR_SCORING
        else None
    )
    return {
        "score": score,
        "details": {
            "count": count,
            "record_count": int(len(use)),
            "poor_bad_count": poor_bad_count,
            "neutral_count": neutral_count,
            "backfill_excluded_count": backfill_excluded_count,
            "missing_rating_count": missing_rating_count,
            "invalid_numeric_count": invalid_numeric_count,
            "duplicates_removed": int(dedup_diag.get("duplicates_removed", 0) or 0),
            "score_column": score_col,
            "rating_column": rating_col,
            "evidence_fraction": round(evidence_fraction, 3),
            "data_state": (
                "unusable"
                if score is None
                else ("partial" if missing_rating_count else "observed")
            ),
            "data_state_reason": (
                f"only {count} of {len(use)} customer-pulse records had a recognized rating"
                if missing_rating_count
                else ""
            ),
            "excluded_from_score": score is None,
        },
    }


def _score_action_plans(action_plans: pd.DataFrame) -> Dict[str, Any]:
    if action_plans is None or action_plans.empty:
        return {
            "score": 0.0,
            "details": {
                "count": 0,
                "unresolved_count": 0,
                "data_state": "observed_empty",
            },
        }
    import canonical_metrics as cm

    use = cm.deduplicate_action_plans(action_plans)
    dedup_diag = dict(getattr(use, "attrs", {}).get("action_plan_dedup") or {})
    count = len(use)
    status_values, status_columns = coalesce_nonempty_columns(
        use,
        (
            "STATUS_C",
            "AP_STATUS_C",
            "Status",
            "STATUS",
            "status",
            "case_status_norm",
            "status_norm",
        ),
    )
    normalized = status_values.map(normalize_status_label)
    known_status = normalized.isin({"Open", "Closed"})
    assessed_count = int(known_status.sum())
    unknown_status_count = int(count - assessed_count)
    unresolved_count = int(normalized.eq("Open").sum())
    if not status_columns or assessed_count == 0:
        return {
            "score": None,
            "details": {
                "count": count,
                "unresolved_count": 0,
                "assessed_status_count": 0,
                "unknown_status_count": count,
                "status_columns": status_columns,
                "duplicates_removed": int(dedup_diag.get("duplicates_removed", 0) or 0),
                "data_state": "unusable",
                "data_state_reason": "no recognized action-plan status was available",
                "excluded_from_score": True,
            },
        }
    unresolved_ratio = unresolved_count / assessed_count
    computed_score = _clamp(unresolved_ratio * 100)
    evidence_fraction = assessed_count / max(count, 1)
    score: Optional[float] = (
        computed_score
        if evidence_fraction >= MIN_COMPONENT_RECORD_COMPLETENESS_FOR_SCORING
        else None
    )
    return {
        "score": score,
        "details": {
            "count": count,
            "unresolved_count": unresolved_count,
            "assessed_status_count": assessed_count,
            "unknown_status_count": unknown_status_count,
            "status_columns": status_columns,
            "duplicates_removed": int(dedup_diag.get("duplicates_removed", 0) or 0),
            "evidence_fraction": round(evidence_fraction, 3),
            "data_state": (
                "unusable"
                if score is None
                else ("partial" if unknown_status_count else "observed")
            ),
            "data_state_reason": (
                f"only {assessed_count} of {count} action plans had a recognized status"
                if unknown_status_count
                else ""
            ),
            "excluded_from_score": score is None,
        },
    }


def _score_incidents(
    ext_incidents: Optional[List[Dict[str, Any]]],
    *,
    customer_name: str = "",
) -> Dict[str, Any]:
    """Score Webex Status incidents.

    Round 2 / Phase 1.8 — ``fetch_status_incidents`` normalizes the raw
    Statuspage feed to ``status in {'active','resolved'}`` and
    ``impact_level in {'Low','Medium','High'}``.  The previous status
    allow-list (``investigating/identified/monitoring/major_outage``)
    never matched the normalized payload, so ``high_impact_count`` was
    silently always 0 and the impact-weighted score component was dead.
    Count both an active-status signal AND a High impact_level signal so
    the canonical score reflects severity for both ongoing incidents and
    historical High-impact items.
    """
    if not ext_incidents:
        return {
            "score": 0.0,
            "details": {
                "count": 0,
                # Round 65 / R-2: emit the same ``count_capped`` /
                # ``count_cap_applied`` keys the populated branch
                # surfaces, so the Risk_Components sheet can render
                # a stable schema across empty / populated paths.
                "count_capped": 0,
                "active_count": 0,
                "high_impact_count": 0,
                "critical_impact_count": 0,
                "active_high_impact_count": 0,
                "active_critical_impact_count": 0,
                "customer_attributed_count": 0,
                "attributed_active_high_impact_count": 0,
                "attributed_active_critical_impact_count": 0,
                "untagged_count": 0,
                "count_cap_applied": False,
            },
        }
    count = len(ext_incidents)
    active_count = 0
    high_impact_count = 0
    critical_impact_count = 0
    active_high_impact_count = 0
    active_critical_impact_count = 0
    customer_attributed_count = 0
    attributed_active_high_impact_count = 0
    attributed_active_critical_impact_count = 0
    untagged_count = 0
    target_customer_key = _clean_name_for_key(
        normalize_customer_name(customer_name)
    )
    for incident in ext_incidents:
        if not isinstance(incident, dict):
            continue
        status = str(incident.get("status", "")).strip().lower()
        impact = str(incident.get("impact_level", "")).strip().lower()
        attribution_values = [
            incident.get(field)
            for field in ("customer_name", "BU_NAME", "customer_id")
            if str(incident.get(field) or "").strip()
        ]
        if not attribution_values:
            untagged_count += 1
        is_customer_attributed = bool(
            target_customer_key
            and target_customer_key != "unknown"
            and any(
                _clean_name_for_key(normalize_customer_name(value))
                == target_customer_key
                for value in attribution_values
            )
        )
        if is_customer_attributed:
            customer_attributed_count += 1
        # Accept the new normalized vocabulary AND the legacy raw words
        # in case any caller still passes raw Statuspage payloads.
        is_active = status in {
            "active",
            "investigating",
            "identified",
            "monitoring",
            "major_outage",
        }
        if is_active:
            active_count += 1
        # Round 2 / Phase 5.5: ``Critical`` is now preserved as a
        # distinct impact band by ``fetch_status_incidents``.  Score
        # critical incidents at a higher weight than ``High`` so a
        # full outage doesn't read as equivalent to a high-impact
        # event.
        if impact == "critical" or status == "major_outage":
            critical_impact_count += 1
            high_impact_count += 1  # critical is also high-impact for legacy callers
            if is_active:
                active_critical_impact_count += 1
                active_high_impact_count += 1
                if is_customer_attributed:
                    attributed_active_critical_impact_count += 1
                    attributed_active_high_impact_count += 1
        elif impact in {"high", "major"}:
            high_impact_count += 1
            if is_active:
                active_high_impact_count += 1
                if is_customer_attributed:
                    attributed_active_high_impact_count += 1
    # Round 65 / R-2: Build 37 surfaced an Incidents-component
    # saturation bug — the per-customer scorer was being fed the
    # portfolio-wide ``ext_incidents`` list (Webex Status feed has no
    # customer_id field; every customer received the same 33-element
    # list) and the unbounded ``count * 6.0`` term clamped the
    # component to 100 for every account.  Cap the raw-count weight
    # at 5 (`min(count, 5) * 6.0` -> max 30 of the 100-pt budget) so
    # a high portfolio-shared count can NEVER dominate the score on
    # its own; severity (active/high/critical) still differentiates
    # accounts, and a customer-level caller that genuinely tags
    # incidents per-customer (caller-side fix in app_simple.py) is
    # unaffected because the active/high/critical weights are not
    # capped.  ``count_capped`` is exposed in details so the
    # Risk_Components sheet can disclose the cap explicitly.
    _count_capped = min(int(count), 5)
    score = _clamp(
        min(
            _count_capped * 6.0
            + active_count * 8.0
            + high_impact_count * 12.0
            + critical_impact_count * 8.0,  # extra weight on critical
            100.0,
        )
    )
    return {
        "score": score,
        "details": {
            "count": count,
            "count_capped": _count_capped,
            "active_count": active_count,
            "high_impact_count": high_impact_count,
            "critical_impact_count": critical_impact_count,
            "active_high_impact_count": active_high_impact_count,
            "active_critical_impact_count": active_critical_impact_count,
            "customer_attributed_count": customer_attributed_count,
            "attributed_active_high_impact_count": attributed_active_high_impact_count,
            "attributed_active_critical_impact_count": attributed_active_critical_impact_count,
            "untagged_count": untagged_count,
            "count_cap_applied": bool(count > _count_capped),
        },
    }


def _score_contract(customer_subs: pd.DataFrame) -> Dict[str, Any]:
    if customer_subs is None or customer_subs.empty:
        # Round 7 / Phase 3.3: emit a sentinel result instead of the
        # arbitrary 8.0 base score.  ``score`` is None and the details
        # dict carries a ``data_state`` flag so the composite layer
        # can distinguish "no subscription data" from "low-risk
        # subscription portfolio".
        return {
            "score": None,
            "details": {
                "count": 0,
                "high_risk_subs": 0,
                "inactive_subs": 0,
                "provisioning_subs": 0,
                "unknown_status_subs": 0,
                "data_state": "missing",
                "data_state_reason": (
                    "Round 7 / Phase 3.3: no subscription rows for customer; "
                    "contract sub-score withheld."
                ),
            },
        }
    use = customer_subs.copy()
    high_risk_subs = 0
    critical_risk_subs = 0
    high_only_risk_subs = 0
    inactive_subs = 0
    provisioning_subs = 0
    unknown_status_subs = 0
    count = len(use)
    _cats = pd.Series("", index=use.index, dtype=str)
    # Round 3: classify renewal risk via a canonical category lookup
    # rather than free-text regex. The previous substring match would
    # match "Highest Quality" as "high" (false positive) and miss
    # "CRT" / "CRIT" abbreviations (false negative).
    if "RENEWAL_RISK_CATEGORY" in use.columns:
        _cats = use["RENEWAL_RISK_CATEGORY"].fillna("").astype(str).map(_canonicalize_renewal_category)
        high_risk_subs = int(_cats.isin({"high", "critical"}).sum())
        critical_risk_subs = int(_cats.eq("critical").sum())
        high_only_risk_subs = int(_cats.eq("high").sum())
    _statuses = pd.Series("", index=use.index, dtype=str)
    if "STATUS_C" in use.columns:
        _statuses = use["STATUS_C"].fillna("").astype(str).map(_canonicalize_subscription_status)
        # Round 3 / Phase 3.6: include "terminated" in the inactive
        # bucket (it was silently dropped before) and surface
        # provisioning / unknown counts so the renewal report can
        # disclose them rather than silently treating them as
        # active.
        inactive_subs = int(
            _statuses.isin({"inactive", "expired", "cancelled", "terminated"}).sum()
        )
        provisioning_subs = int((_statuses == "provisioning").sum())
    known_status = _statuses.isin(
        {
            "active",
            "inactive",
            "expired",
            "cancelled",
            "suspended",
            "terminated",
            "provisioning",
        }
    )
    known_risk = _cats.isin({"critical", "high", "medium", "low", "healthy"})
    unknown_status_subs = int((~known_status).sum())
    unknown_risk_category_subs = int((~known_risk).sum())
    assessed_records = int((known_status | known_risk).sum())
    assessed_fraction = assessed_records / max(count, 1)
    evidence_fraction = (
        (int(known_status.sum()) + int(known_risk.sum())) / max(count * 2, 1)
    )
    computed_score = _clamp(
        (high_risk_subs / max(int(known_risk.sum()), 1)) * 70
        + (inactive_subs / max(int(known_status.sum()), 1)) * 40
    )
    score: Optional[float] = (
        computed_score
        if assessed_fraction >= MIN_COMPONENT_RECORD_COMPLETENESS_FOR_SCORING
        else None
    )
    return {
        "score": score,
        "details": {
            "count": count,
            "high_risk_subs": high_risk_subs,
            "critical_risk_subs": critical_risk_subs,
            "high_only_risk_subs": high_only_risk_subs,
            "inactive_subs": inactive_subs,
            "provisioning_subs": provisioning_subs,
            "unknown_status_subs": unknown_status_subs,
            "unknown_risk_category_subs": unknown_risk_category_subs,
            "assessed_count": assessed_records,
            "evidence_fraction": round(evidence_fraction, 3),
            "data_state": (
                "unusable"
                if score is None
                else (
                    "partial"
                    if unknown_status_subs or unknown_risk_category_subs
                    else "observed"
                )
            ),
            "data_state_reason": (
                f"{unknown_status_subs} subscriptions had unknown status and {unknown_risk_category_subs} had unknown renewal-risk category"
                if unknown_status_subs or unknown_risk_category_subs
                else ""
            ),
            "excluded_from_score": score is None,
        },
    }


def _score_engagement(customer_ab: pd.DataFrame, customer_csone: pd.DataFrame, customer_pulse: pd.DataFrame, action_plans: pd.DataFrame) -> Dict[str, Any]:
    """Score the customer's *activity volume* across AB / TAC / pulse /
    action plans.

    Round 5 / Phase 5.11: this component is historically labeled
    ``engagement``, which is misleading -- "engagement" sounds
    positive ("the customer is engaged with us") whereas the
    function actually treats *more activity* as *higher risk*
    (lots of barriers + lots of cases + lots of pulses ==
    higher score).  Renaming the public key would be a breaking
    contract change for downstream consumers; instead, surface a
    ``component_kind="activity_volume"`` discriminator and a
    ``description`` so any reader (LLM or otherwise) can tell
    that this score is "how loud is this account on our
    surfaces", not "how engaged are they in adoption".
    """

    import canonical_metrics as cm

    total_activity = (
        (0 if customer_ab is None else cm.count_total_barriers(customer_ab))
        + (0 if customer_csone is None else cm.count_total_tac(customer_csone))
        + (0 if customer_pulse is None else cm.count_total_customer_pulse(customer_pulse))
        + (0 if action_plans is None else cm.count_total_action_plans(action_plans))
    )
    base_meta = {
        "component_kind": "activity_volume",
        "description": (
            "Activity-volume risk: counts of AB + TAC + pulse + action-plan "
            "rows in the analysis window. More activity is treated as more "
            "risk (NOT a measure of customer engagement quality)."
        ),
    }
    if total_activity == 0:
        return {"score": 0.0, "details": {"total_activity": 0, **base_meta}}
    if total_activity > 20:
        return {"score": 60.0, "details": {"total_activity": total_activity, **base_meta}}
    if total_activity > 10:
        return {"score": 40.0, "details": {"total_activity": total_activity, **base_meta}}
    return {"score": 15.0, "details": {"total_activity": total_activity, **base_meta}}


def compute_customer_risk_profile(
    customer_name: str,
    customer_ab: Optional[pd.DataFrame] = None,
    customer_csone: Optional[pd.DataFrame] = None,
    customer_pulse: Optional[pd.DataFrame] = None,
    customer_action_plans: Optional[pd.DataFrame] = None,
    customer_subs: Optional[pd.DataFrame] = None,
    ext_incidents: Optional[List[Dict[str, Any]]] = None,
    weights: RiskWeights = RiskWeights(),
    *,
    recent_window_days: int = 30,
) -> Dict[str, Any]:
    """Compute deterministic weighted customer risk score (0-100).

    Phase 3.4: ``recent_window_days`` lets callers thread the
    report-level analysis horizon (e.g. 90) into the support-case
    momentum scorer instead of always using a hardcoded 30-day window.
    """
    import canonical_metrics as cm

    support_input = (
        customer_csone if customer_csone is not None else pd.DataFrame()
    )
    pulse_input = customer_pulse if customer_pulse is not None else pd.DataFrame()
    action_input = (
        customer_action_plans
        if customer_action_plans is not None
        else pd.DataFrame()
    )
    # Canonicalize once up front so ownership conflicts discovered inside the
    # canonicalizers remain available to the evidence-quality layer.  Scoring
    # helpers may safely canonicalize these idempotently, but quality checks
    # must inspect the generated attrs rather than only the caller's raw frame.
    logical_support_input = cm.deduplicate_tac_cases(support_input)
    logical_pulse_input = cm.deduplicate_customer_pulse(pulse_input)
    logical_action_input = cm.deduplicate_action_plans(action_input)
    pulse_for_scoring = _exclude_backfill_pulse_rows(logical_pulse_input)
    ab_component = _score_adoption_barriers(customer_ab if customer_ab is not None else pd.DataFrame())
    support_component = _score_support_cases(
        support_input,
        recent_window_days=recent_window_days,
    )
    pulse_component = _score_customer_pulse(pulse_input)
    action_component = _score_action_plans(action_input)
    incident_component = _score_incidents(
        ext_incidents,
        customer_name=customer_name,
    )
    contract_component = _score_contract(customer_subs if customer_subs is not None else pd.DataFrame())
    engagement_component = _score_engagement(
        customer_ab if customer_ab is not None else pd.DataFrame(),
        logical_support_input,
        pulse_for_scoring,
        logical_action_input,
    )

    support_quality_source = (
        None if customer_csone is None else logical_support_input
    )
    pulse_quality_source = (
        None if customer_pulse is None else logical_pulse_input
    )
    action_quality_source = (
        None if customer_action_plans is None else logical_action_input
    )

    def _mark_unavailable(component: Dict[str, Any], reason: str) -> Dict[str, Any]:
        out = dict(component)
        details = dict(out.get("details") or {})
        details.update(
            {
                "data_state": "missing",
                "data_state_reason": reason,
                "excluded_from_score": True,
            }
        )
        out["score"] = None
        out["details"] = details
        return out

    def _source_unavailability_reason(source: Any, label: str) -> Optional[str]:
        if source is None:
            return f"{label} source not provided"
        attrs = getattr(source, "attrs", None)
        if not isinstance(attrs, dict):
            return None
        fetch_error = str(attrs.get("fetch_error") or "").strip()
        fetch_error_kind = str(attrs.get("fetch_error_kind") or "").strip()
        if fetch_error or fetch_error_kind:
            detail = fetch_error_kind or "fetch_error"
            return f"{label} source unavailable ({detail})"
        ownership_diag = attrs.get("cross_customer_id_conflicts") or {}
        quarantined_rows = int(ownership_diag.get("quarantined_rows", 0) or 0)
        if quarantined_rows and bool(getattr(source, "empty", False)):
            return (
                f"{label} source contained only logical IDs with conflicting "
                "customer ownership"
            )
        return None

    def _source_ownership_conflict(source: Any) -> Dict[str, Any]:
        attrs = getattr(source, "attrs", None)
        if not isinstance(attrs, dict):
            return {}
        diag = dict(attrs.get("cross_customer_id_conflicts") or {})
        if int(diag.get("quarantined_rows", 0) or 0) <= 0:
            return {}
        return diag

    ownership_conflicts = {
        label: diag
        for label, source in (
            ("adoption_barriers", customer_ab),
            ("support_cases", support_quality_source),
            ("customer_pulse", pulse_quality_source),
            ("action_plans", action_quality_source),
            ("contract", customer_subs),
        )
        for diag in (_source_ownership_conflict(source),)
        if diag
    }

    # ``None`` means the caller did not provide this source.  An explicitly
    # empty frame/list means the source was observed and contained no records.
    # Fetchers also return empty frames stamped with ``fetch_error``; those are
    # failures, not observed zeroes, and must be removed from the denominator.
    ab_unavailable = _source_unavailability_reason(customer_ab, "adoption-barrier")
    support_unavailable = _source_unavailability_reason(
        support_quality_source, "support-case"
    )
    pulse_unavailable = _source_unavailability_reason(
        pulse_quality_source, "customer-pulse"
    )
    action_unavailable = _source_unavailability_reason(
        action_quality_source, "action-plan"
    )
    contract_unavailable = _source_unavailability_reason(customer_subs, "subscription")
    if ab_unavailable:
        ab_component = _mark_unavailable(ab_component, ab_unavailable)
    if support_unavailable:
        support_component = _mark_unavailable(support_component, support_unavailable)
    if pulse_unavailable:
        pulse_component = _mark_unavailable(pulse_component, pulse_unavailable)
    if action_unavailable:
        action_component = _mark_unavailable(action_component, action_unavailable)
    if contract_unavailable:
        contract_component = _mark_unavailable(contract_component, contract_unavailable)
    if ext_incidents is None:
        incident_component = _mark_unavailable(incident_component, "incident source not provided")
    activity_failures = [
        reason
        for reason in (
            ab_unavailable,
            support_unavailable,
            pulse_unavailable,
            action_unavailable,
        )
        if reason
    ]
    if activity_failures:
        engagement_component = _mark_unavailable(
            engagement_component,
            "activity-volume evidence incomplete: " + "; ".join(activity_failures),
        )

    # Round 7 / Phase 3.3: contract sub-score may now be ``None`` when
    # subscription data is missing. Treat the missing component as
    # weight-zero so the composite is renormalised over the remaining
    # signals instead of multiplying by ``None``.
    _components_for_weighting = [
        (ab_component, weights.adoption_barriers),
        (support_component, weights.support_cases),
        (pulse_component, weights.customer_pulse),
        (action_component, weights.action_plans),
        (incident_component, weights.incidents),
        (contract_component, weights.contract),
        (engagement_component, weights.engagement),
    ]

    def _effective_component_fraction(component: Dict[str, Any]) -> float:
        if component.get("score") is None:
            return 0.0
        raw = (component.get("details") or {}).get("evidence_fraction", 1.0)
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 0.0

    _present = [
        (component["score"], weight * _effective_component_fraction(component))
        for component, weight in _components_for_weighting
        if component.get("score") is not None
        and _effective_component_fraction(component) > 0
    ]
    _total_weight = sum(effective_weight for _, effective_weight in _present)
    _configured_weight = sum(weight for _, weight in _components_for_weighting)
    _score_evidence_coverage = (
        _total_weight / _configured_weight if _configured_weight else 0.0
    )
    _has_weighted_evidence = _total_weight > 0
    if _has_weighted_evidence:
        weighted_average_score = (
            sum(s * w for s, w in _present)
            / _total_weight
            * _configured_weight
        )
        # External incidents are one-sided hazard evidence, not a compensating
        # health signal.  Renormalizing a newly available, positive incident
        # component into an already high-risk profile could otherwise *lower*
        # the score (for example 77 -> 69 when three active incidents were
        # added).  Preserve the score implied by the non-incident evidence and
        # apply the bounded incident weight as an uplift; keep the ordinary
        # weighted average when it is more conservative.  A zero/empty
        # incident feed therefore cannot make unrelated risk disappear.
        non_incident_components = [
            item
            for index, item in enumerate(_components_for_weighting)
            if index != 4
        ]
        non_incident_present = [
            (
                component["score"],
                weight * _effective_component_fraction(component),
            )
            for component, weight in non_incident_components
            if component.get("score") is not None
            and _effective_component_fraction(component) > 0
        ]
        non_incident_weight = sum(weight for _, weight in non_incident_present)
        non_incident_score = (
            sum(score * weight for score, weight in non_incident_present)
            / non_incident_weight
            * _configured_weight
            if non_incident_weight > 0
            else 0.0
        )
        incident_effective_weight = (
            weights.incidents * _effective_component_fraction(incident_component)
        )
        incident_risk_uplift = float(incident_component.get("score") or 0.0) * (
            incident_effective_weight
        )
        weighted_score = max(
            weighted_average_score,
            non_incident_score + incident_risk_uplift,
        )
        score_before_guardrail: Optional[float] = round(_clamp(weighted_score), 1)
    else:
        weighted_average_score = 0.0
        incident_risk_uplift = 0.0
        weighted_score = 0.0
        score_before_guardrail = None
    guardrail_floor = 0.0
    guardrail_reasons: List[str] = []
    if support_component["details"].get("active_p1_count", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["HIGH"]))
        guardrail_reasons.append("active P1 support case")
    if support_component["details"].get("active_bems_count", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["HIGH"]))
        guardrail_reasons.append("active BEMS escalation")
    if ab_component["details"].get("critical_count", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["HIGH"]))
        guardrail_reasons.append("critical adoption barrier")
    if support_component["details"].get("active_p2_count", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["MEDIUM"]))
        guardrail_reasons.append("active P2 support case")
    if ab_component["details"].get("high_count", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["MEDIUM"]))
        guardrail_reasons.append("high-severity adoption barrier")
    if contract_component["details"].get("critical_risk_subs", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["HIGH"]))
        guardrail_reasons.append("critical renewal-risk subscription")
    elif contract_component["details"].get("high_only_risk_subs", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["MEDIUM"]))
        guardrail_reasons.append("high renewal-risk subscription")
    if contract_component["details"].get("inactive_subs", 0) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["MEDIUM"]))
        guardrail_reasons.append("inactive subscription")
    if incident_component["details"].get(
        "attributed_active_critical_impact_count", 0
    ) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["HIGH"]))
        guardrail_reasons.append("customer-attributed active critical external incident")
    elif incident_component["details"].get(
        "attributed_active_high_impact_count", 0
    ) > 0:
        guardrail_floor = max(guardrail_floor, float(RISK_BAND_THRESHOLDS["MEDIUM"]))
        guardrail_reasons.append("customer-attributed active high-impact external incident")
    _blocking_record_quality_components = [
        label
        for label, component in (
            ("adoption_barriers", ab_component),
            ("support_cases", support_component),
            ("customer_pulse", pulse_component),
            ("action_plans", action_component),
            ("contract", contract_component),
        )
        if (component.get("details") or {}).get("data_state") == "unusable"
        and max(
            int((component.get("details") or {}).get("count", 0) or 0),
            int((component.get("details") or {}).get("record_count", 0) or 0),
        ) > 0
    ]
    # A healthy/low conclusion needs broad enough coverage to be meaningful.
    # A directly observed material guardrail (P1/P2/BEMS/critical-high AB) may
    # still publish the conservative risk floor even with sparse other feeds.
    ownership_conflict_at_health_boundary = bool(ownership_conflicts) and (
        _score_evidence_coverage
        <= MIN_EVIDENCE_COVERAGE_FOR_HEALTH_ASSESSMENT + 1e-12
    )
    assessment_available = _has_weighted_evidence and (
        (
            _score_evidence_coverage
            >= MIN_EVIDENCE_COVERAGE_FOR_HEALTH_ASSESSMENT
            and not _blocking_record_quality_components
            and not ownership_conflict_at_health_boundary
        )
        or guardrail_floor > 0
    )
    if assessment_available:
        score_0_100: Optional[float] = round(
            max(float(score_before_guardrail or 0.0), guardrail_floor), 1
        )
        score_0_10: Optional[float] = round(score_0_100 / 10.0, 1)
        risk_band = _risk_band(score_0_100)
        risk_assessment_state = "SCORED"
    else:
        score_0_100 = None
        score_0_10 = None
        score_before_guardrail = None
        risk_band = "UNKNOWN"
        risk_assessment_state = (
            "INSUFFICIENT_EVIDENCE" if _has_weighted_evidence else "UNAVAILABLE"
        )

    risk_factors: List[str] = []
    if ab_component["details"].get("critical_high_count", 0) > 0:
        risk_factors.append(
            f"{ab_component['details']['critical_high_count']} critical/high adoption barriers "
            f"{format_inline_source('Adoption Barriers', fields=['SEVERITY_C', 'AB_STATUS_C'])}"
        )
    if support_component["details"].get("active_escalated_count", 0) > 0:
        risk_factors.append(
            f"{support_component['details']['active_escalated_count']} active escalated TAC cases (P1/P2) "
            f"{format_inline_source('Support Cases (TAC)', fields=['Severity', 'Status', 'Case #'])}"
        )
    if support_component["details"].get("active_bems_count", 0) > 0:
        risk_factors.append(
            f"{support_component['details']['active_bems_count']} active BEMS escalations "
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
            (
                f"Derived risk score: {score_0_100}/100 ({risk_band}) "
                f"{format_inline_source('Derived Metric', source_override='Deterministic weighted AdoptIQ risk engine', verification_override='Recompute from normalized component metrics')}"
            )
            if assessment_available
            else (
                "Risk assessment unavailable: evidence coverage, ownership integrity, or record completeness was below the "
                "minimum needed for a reliable health conclusion."
                if _has_weighted_evidence
                else "Risk assessment unavailable: no weighted evidence source was provided."
            )
        ),
    ]

    component_labels = (
        ("adoption_barriers", ab_component, weights.adoption_barriers),
        ("support_cases", support_component, weights.support_cases),
        ("customer_pulse", pulse_component, weights.customer_pulse),
        ("action_plans", action_component, weights.action_plans),
        ("incidents", incident_component, weights.incidents),
        ("contract", contract_component, weights.contract),
        ("activity_volume", engagement_component, weights.engagement),
    )
    evidence_weight_total = sum(weight for _, _, weight in component_labels)
    evidence_weight_present = sum(
        weight * _effective_component_fraction(component)
        for _, component, weight in component_labels
        if component.get("score") is not None
    )
    evidence_coverage = round(
        evidence_weight_present / evidence_weight_total if evidence_weight_total else 0.0,
        3,
    )
    missing_components = [
        label for label, component, _ in component_labels if component.get("score") is None
    ]
    partial_components = [
        label
        for label, component, _ in component_labels
        if (component.get("details") or {}).get("data_state") == "partial"
    ]
    partial_components.extend(
        label
        for label in ownership_conflicts
        if label not in missing_components and label not in partial_components
    )
    if _blocking_record_quality_components or ownership_conflict_at_health_boundary:
        confidence_band = "LOW"
    elif evidence_coverage >= 0.75 and not partial_components:
        confidence_band = "HIGH"
    elif evidence_coverage >= MIN_EVIDENCE_COVERAGE_FOR_HEALTH_ASSESSMENT:
        confidence_band = "MEDIUM"
    else:
        confidence_band = "LOW"
    evidence_quality = {
        "coverage_ratio": evidence_coverage,
        "confidence_band": confidence_band,
        "available_components": [
            label for label, component, _ in component_labels if component.get("score") is not None
        ],
        "missing_components": missing_components,
        "partial_components": partial_components,
        "ownership_conflicts": ownership_conflicts,
        "ownership_conflict_boundary_blocked": ownership_conflict_at_health_boundary,
        "caveats": [
            f"{label}: {(component.get('details') or {}).get('data_state_reason', 'unavailable or unusable')}"
            for label, component, _ in component_labels
            if component.get("score") is None
            or (component.get("details") or {}).get("data_state") == "partial"
        ]
        + [
            f"{label}: quarantined {int(diag.get('quarantined_rows', 0) or 0)} row(s) "
            "whose logical ID appeared under multiple customers"
            for label, diag in ownership_conflicts.items()
        ],
    }

    next_best_actions: List[Dict[str, Any]] = []

    def _add_action(
        priority: int,
        action: str,
        reason: str,
        sources: List[str],
        *,
        likely_owner: str,
        urgency: str,
        expected_outcome: str,
        effort: str = "Medium",
        dependencies: Optional[List[str]] = None,
    ) -> None:
        next_best_actions.append(
            {
                "priority": priority,
                "action": action,
                "reason": reason,
                "evidence_sources": sources,
                "likely_owner": likely_owner,
                "urgency": urgency,
                "expected_outcome": expected_outcome,
                "effort": effort,
                "confidence": confidence_band,
                "dependencies": list(dependencies or []),
            }
        )

    if support_component["details"].get("active_p1_count", 0) > 0:
        _add_action(
            1,
            "Assign an executive owner and daily resolution checkpoint for active P1 cases.",
            "Active P1 support evidence triggers the HIGH-risk guardrail.",
            ["Support Cases (TAC)"],
            likely_owner="Executive sponsor and TAC case owner",
            urgency="Immediate",
            expected_outcome="Restore a time-bound resolution path and reduce unresolved critical impact.",
            dependencies=["Named case owner", "Current resolution ETA"],
        )
    if support_component["details"].get("active_bems_count", 0) > 0:
        _add_action(
            1,
            "Escalate active BEMS items to engineering with a named owner and committed ETA.",
            "Active BEMS evidence requires an engineering closure path.",
            ["BEMS Escalations", "Support Cases (TAC)"],
            likely_owner="Engineering escalation owner",
            urgency="Immediate",
            expected_outcome="Establish accountable engineering progress and a customer-safe ETA.",
            dependencies=["Engineering owner", "Validated BEMS linkage"],
        )
    if incident_component["details"].get(
        "attributed_active_critical_impact_count", 0
    ) > 0:
        _add_action(
            1,
            "Activate a critical-incident customer impact review with a named owner and update cadence.",
            "A customer-attributed active critical external incident triggers the HIGH-risk guardrail.",
            ["External Incidents"],
            likely_owner="Incident commander and Customer Success Manager",
            urgency="Immediate",
            expected_outcome="Confirm customer impact, mitigation, and the next authoritative status update.",
            dependencies=["Current incident status", "Named incident commander"],
        )
    elif incident_component["details"].get(
        "attributed_active_high_impact_count", 0
    ) > 0:
        _add_action(
            2,
            "Assess customer impact from the active high-impact incident and set an update cadence.",
            "A customer-attributed active high-impact external incident triggers the MEDIUM-risk guardrail.",
            ["External Incidents"],
            likely_owner="Customer Success Manager and incident owner",
            urgency="Within 1 business day",
            expected_outcome="Establish whether the incident affects adoption or renewal work and communicate next steps.",
            dependencies=["Current incident status"],
        )
    if ab_component["details"].get("critical_high_count", 0) > 0:
        _missing_plan = action_component["details"].get("count", 0) == 0
        _add_action(
            1 if ab_component["details"].get("critical_count", 0) else 2,
            (
                "Create and assign a dated remediation plan for each critical/high adoption barrier."
                if _missing_plan
                else "Review the dated remediation plan for each critical/high adoption barrier."
            ),
            (
                "Open high-severity adoption blockers are present with no linked action-plan evidence."
                if _missing_plan
                else "Open high-severity adoption blockers are directly observed."
            ),
            ["Adoption Barriers", "Action Plans"],
            likely_owner="Customer Success Manager and barrier owner",
            urgency="Immediate" if ab_component["details"].get("critical_count", 0) else "This week",
            expected_outcome="Convert each adoption blocker into owned, dated recovery work.",
            dependencies=["Barrier owner", "Customer-agreed completion date"],
        )
    if (
        pulse_component["details"].get("poor_bad_count", 0) > 0
        and support_component["details"].get("active_escalated_count", 0) > 0
    ):
        _add_action(
            1,
            "Run one recovery review that joins active P1/P2 case resolution to a dated customer-pulse follow-up.",
            "Negative customer sentiment and active escalated support evidence coincide.",
            ["Customer Pulse", "Support Cases (TAC)"],
            likely_owner="Customer Success Manager and TAC case owner",
            urgency="Within 2 business days",
            expected_outcome="Align technical recovery with the customer's stated experience and verify improvement.",
            dependencies=["Customer availability", "Current case resolution plan"],
        )
    if pulse_component["details"].get("poor_bad_count", 0) > 0:
        _add_action(
            2,
            "Run a customer recovery conversation and record the next pulse after agreed actions.",
            "Poor/negative customer pulse evidence is present.",
            ["Customer Pulse"],
            likely_owner="Customer Success Manager",
            urgency="This week",
            expected_outcome="Capture the customer's priority concerns and measure whether recovery actions help.",
            dependencies=["Customer availability"],
        )
    if action_component["details"].get("unresolved_count", 0) > 0:
        _add_action(
            2,
            "Assign owners and due dates to unresolved action plans, then review weekly.",
            "Unresolved action-plan records remain open.",
            ["Action Plans"],
            likely_owner="Customer Success Manager and action owners",
            urgency="This week",
            expected_outcome="Turn open commitments into accountable, measurable completion work.",
            dependencies=["Named owner for each open action"],
        )
    if contract_component["details"].get("high_risk_subs", 0) > 0 or contract_component["details"].get("inactive_subs", 0) > 0:
        _add_action(
            1,
            "Review renewal posture and contract status with the account team this week.",
            "High-risk or inactive subscription evidence is present.",
            ["Subscriptions"],
            likely_owner="Renewal owner and account team",
            urgency="This week",
            expected_outcome="Confirm commercial exposure, ownership, and the next renewal decision milestone.",
            dependencies=["Current renewal date and commercial status"],
        )
    if (
        evidence_coverage < MIN_EVIDENCE_COVERAGE_FOR_HEALTH_ASSESSMENT
        or _blocking_record_quality_components
        or ownership_conflict_at_health_boundary
    ):
        _add_action(
            1,
            "Validate the missing evidence sources before treating this score as a health assessment.",
            (
                "One or more non-empty sources had too few interpretable records: "
                + ", ".join(_blocking_record_quality_components)
                if _blocking_record_quality_components
                else (
                    "Logical-record ownership conflicts leave the usable evidence "
                    "at the minimum health-publication boundary."
                    if ownership_conflict_at_health_boundary
                    else f"Only {evidence_coverage:.0%} of weighted evidence is available."
                )
            ),
            sorted(set(missing_components + _blocking_record_quality_components)),
            likely_owner="Report operator or data steward",
            urgency="Before the next decision review",
            expected_outcome="Prevent a low-evidence score from being mistaken for verified customer health.",
            effort="Low",
            dependencies=["Access to the missing authorized source feeds"],
        )
    elif partial_components:
        _add_action(
            2,
            "Resolve the partial source records before relying on fine-grained risk comparisons.",
            "Some source rows could not be fully interpreted: "
            + ", ".join(partial_components),
            partial_components,
            likely_owner="Report operator or data steward",
            urgency="Before the next decision review",
            expected_outcome="Restore complete, comparable component evidence and remove the data-quality caveat.",
            effort="Low",
            dependencies=["Corrected status, severity, priority, or rating fields"],
        )
    if not next_best_actions:
        _add_action(
            3,
            "Maintain the normal success cadence and monitor for new evidence.",
            "No current high-severity signal is present in the available sources.",
            evidence_quality["available_components"],
            likely_owner="Customer Success Manager",
            urgency="Normal cadence",
            expected_outcome="Preserve coverage while avoiding unsupported urgency.",
            effort="Low",
        )
    next_best_actions.sort(key=lambda item: (int(item["priority"]), item["action"]))
    recommendations = [item["action"] for item in next_best_actions]

    return {
        "customer_name": customer_name,
        "risk_score_0_100": score_0_100,
        "risk_score_0_10": score_0_10,
        "risk_band": risk_band,
        "risk_assessment_state": risk_assessment_state,
        "score_before_guardrail": score_before_guardrail,
        "weighted_average_score": round(_clamp(weighted_average_score), 1),
        "incident_risk_uplift": round(incident_risk_uplift, 1),
        "guardrail_floor": guardrail_floor,
        "guardrail_reasons": guardrail_reasons,
        "evidence_quality": evidence_quality,
        "components": {
            "adoption_barriers": ab_component,
            "support_cases": support_component,
            "customer_pulse": pulse_component,
            "action_plans": action_component,
            "incidents": incident_component,
            "contract": contract_component,
            # Round 5 / Phase 5.11: kept under the legacy
            # ``engagement`` key for back-compat with persisted
            # profiles and downstream consumers, but the helper now
            # tags the details with ``component_kind=activity_volume``
            # so renderers can disambiguate when needed.
            "engagement": engagement_component,
            "activity_volume": engagement_component,
        },
        "risk_factors": risk_factors,
        "key_findings": key_findings,
        "recommendations": recommendations,
        "next_best_actions": next_best_actions,
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
            "unknown_risk_customers": 0,
            "scored_customers": 0,
            "risk_band_counts": {
                "CRITICAL": 0,
                "HIGH": 0,
                "MEDIUM": 0,
                "LOW": 0,
                "HEALTHY": 0,
                "UNKNOWN": 0,
            },
        }

    scores: List[float] = []
    band_counts = {
        "CRITICAL": 0,
        "HIGH": 0,
        "MEDIUM": 0,
        "LOW": 0,
        "HEALTHY": 0,
        "UNKNOWN": 0,
    }
    for profile in profiles.values():
        if (
            str(profile.get("risk_assessment_state") or "").upper() == "UNAVAILABLE"
            or str(profile.get("risk_band") or "").upper() == "UNKNOWN"
            or profile.get("risk_score_0_100") is None
        ):
            band_counts["UNKNOWN"] += 1
            continue
        score = float(profile.get("risk_score_0_100", 0.0) or 0.0)
        band = str(profile.get("risk_band", _risk_band(score))).upper().strip()
        if band not in band_counts:
            band = _risk_band(score)
        band_counts[band] += 1
        scores.append(score)

    # Round 5 / Phase 5.15: previously this was the canonical
    # ``high_risk_customers`` source-of-truth and computed
    # ``CRITICAL + HIGH`` directly.  ``canonical_metrics.compute_high_risk_count``
    # is the cross-report definition (it also honors the
    # ``color == 'red'`` legacy flag and the 0-10 score branch).  Route
    # through that helper so the portfolio summary cannot disagree with
    # the canonical headline tile.
    # Round 7 / Phase 3.4: previously this except branch silently fell
    # back to ``CRITICAL + HIGH`` band counts -- which can disagree
    # with the canonical headline tile and hide a real
    # ``canonical_metrics`` import / runtime failure.  Now we log a
    # warning and stamp a ``portfolio_high_risk_fallback`` flag onto
    # the returned dict so report_consistency can surface "fallback in
    # use" instead of pretending nothing went wrong.
    _portfolio_fallback_reason: Optional[str] = None
    try:
        from canonical_metrics import (
            compute_high_risk_count as _cm_count_high,
            RISK_SCALE_0_TO_100 as _CM_RISK_0_100,
        )
        high_risk = _cm_count_high(profiles, scale=_CM_RISK_0_100)
    except Exception as _cm_err:
        try:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Round 7 / Phase 3.4: canonical_metrics.compute_high_risk_count "
                "unavailable (%s); falling back to band-tally count -- this is "
                "not silent, downstream consistency check will flag it.",
                _cm_err,
            )
        except Exception:
            pass  # noqa: PIE790
        high_risk = band_counts["CRITICAL"] + band_counts["HIGH"]
        _portfolio_fallback_reason = (
            f"canonical_metrics import or call failed: "
            f"{type(_cm_err).__name__}: {_cm_err}"
        )
    avg_score: Optional[float] = (
        round(sum(scores) / len(scores), 1) if scores else None
    )
    max_score: Optional[float] = round(max(scores), 1) if scores else None
    result = {
        "total_customers": len(profiles),
        "average_risk_score_0_100": avg_score,
        "highest_risk_score_0_100": max_score,
        "high_risk_customers": high_risk,
        "medium_risk_customers": band_counts["MEDIUM"],
        "low_risk_customers": band_counts["LOW"],
        "healthy_customers": band_counts["HEALTHY"],
        "unknown_risk_customers": band_counts["UNKNOWN"],
        "scored_customers": len(scores),
        "risk_band_counts": band_counts,
    }
    # Round 7 / Phase 3.4: surface a non-silent fallback marker so
    # report_consistency / dashboards can warn the operator.
    if _portfolio_fallback_reason is not None:
        result["portfolio_high_risk_fallback"] = True
        result["portfolio_high_risk_fallback_reason"] = _portfolio_fallback_reason
    return result


def portfolio_health_grade(portfolio_summary) -> str:
    """Map a portfolio risk summary to a single health-grade letter A-F.

    Round 123 / Build 92.  Grades the portfolio from its canonical
    ``average_risk_score_0_100`` through the same band thresholds the
    per-customer grade uses, so the ``Portfolio Health Score`` letter
    can never drift from the canonical band math.  Accepts the dict
    returned by :func:`compute_portfolio_risk_summary`; returns ``A``
    for an empty / non-dict summary.

    Average-based (not worst-case) is intentional: the portfolio grade
    is an overall-health reading, and the per-customer grades + the
    risk-band distribution already surface the worst customers.

    Pure function; no I/O.
    """
    if not isinstance(portfolio_summary, dict):
        return "A"
    if (
        "scored_customers" in portfolio_summary
        and int(portfolio_summary.get("scored_customers") or 0) == 0
    ):
        return "N/A"
    return band_to_health_grade(
        None,
        portfolio_summary.get("average_risk_score_0_100"),
    )
