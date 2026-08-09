"""Deterministic weighted risk scoring used across all report types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Round 156: opt-in magnitude-first scoring profile.
#
# Three component scorers (adoption-barrier severity & openness, action plans,
# and contract) historically score by *proportion* — e.g. action plans use
# ``unresolved_ratio * 100`` so a customer with 1 open plan of 1 scores 100
# (maximum) while a customer with 3 open of 20 scores 15.  For a risk model
# whose whole job is to rank *who to call first*, that inverts the ordering:
# it rewards customers who complete nothing (small denominator) and penalizes
# customers doing real remediation (large denominator), and it makes one
# critical barrier indistinguishable from four.  Industry health-scoring
# guidance (Gainsight; churn back-testing practice) treats magnitude of severe,
# open work as the risk-relevant quantity and validates any weighting change
# against real churn/escalation outcomes before it goes to production.
#
# The ``magnitude_first`` profile makes magnitude primary and proportion
# secondary, bounded to the same 0-100 envelope with the cap discipline of the
# (already-sound) support-case scorer.  It is OPT-IN: the default stays
# ``legacy`` so every shipped report, oracle, and test is byte-identical until
# the work machine validates the new ordering on live Brian-Frazier data and
# promotes it to the default in a dedicated round.  See RISK_LOGIC_EVALUATION.md.
RISK_SCORING_PROFILE_LEGACY = "legacy"
RISK_SCORING_PROFILE_MAGNITUDE_FIRST = "magnitude_first"
_VALID_RISK_SCORING_PROFILES = frozenset(
    {RISK_SCORING_PROFILE_LEGACY, RISK_SCORING_PROFILE_MAGNITUDE_FIRST}
)


def resolve_risk_scoring_profile(profile: Optional[str] = None) -> str:
    """Resolve the active risk-scoring profile.

    Precedence: an explicit ``profile`` argument > the
    ``ADOPTIQ_RISK_SCORING_PROFILE`` environment variable >
    ``config.Config.RISK_SCORING_PROFILE`` (when importable) > the default
    ``legacy``.  Any unrecognized value resolves to ``legacy`` so a typo can
    never silently activate a different scoring model in production.
    """
    candidates: List[Any] = []
    if profile is not None:
        candidates.append(profile)
    else:
        import os  # local import keeps module import side-effect-free

        env_val = os.environ.get("ADOPTIQ_RISK_SCORING_PROFILE")
        if env_val:
            candidates.append(env_val)
        else:
            try:
                import config as _config  # optional; absent in some test envs

                cfg_val = getattr(
                    getattr(_config, "Config", None), "RISK_SCORING_PROFILE", None
                )
                if cfg_val:
                    candidates.append(cfg_val)
            except Exception:  # noqa: BLE001 - config is best-effort here
                pass
    for candidate in candidates:
        norm = str(candidate).strip().lower().replace("-", "_")
        if norm in _VALID_RISK_SCORING_PROFILES:
            return norm
    return RISK_SCORING_PROFILE_LEGACY


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
    return band_to_health_grade(
        profile.get("risk_band"),
        profile.get("risk_score_0_100"),
    )


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


def _resolve_risk_as_of_utc(as_of: Any = None) -> pd.Timestamp:
    """Return one valid UTC clock for all time-sensitive risk components.

    Existing callers that omit ``as_of`` retain render-time behavior.  An
    explicitly supplied value is fail-closed: invalid values must not silently
    fall back to the wall clock because that would make a report irreproducible.
    """

    if as_of is None:
        return pd.Timestamp(datetime.now(timezone.utc))
    try:
        resolved = pd.Timestamp(as_of)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("compute_customer_risk_profile requires a valid explicit as_of timestamp") from exc
    if pd.isna(resolved):
        raise ValueError("compute_customer_risk_profile requires a valid explicit as_of timestamp")
    if resolved.tzinfo is None:
        return resolved.tz_localize("UTC")
    return resolved.tz_convert("UTC")


def _score_adoption_barriers(
    customer_ab: pd.DataFrame,
    *,
    as_of: Any = None,
    recompute_existing_age: bool = False,
    scoring_profile: Optional[str] = None,
) -> Dict[str, Any]:
    if customer_ab is None or customer_ab.empty:
        return {"score": 0.0, "details": {"count": 0, "critical_high_count": 0, "open_count": 0, "aging_open_count": 0}}

    use = customer_ab.copy()
    if "severity_norm" not in use.columns:
        sev_col = next((c for c in ("SEVERITY_C", "severity_c", "Severity") if c in use.columns), None)
        use["severity_norm"] = use[sev_col].apply(normalize_severity_label) if sev_col else "Unknown"
    if "status_norm" not in use.columns:
        status_col = next((c for c in ("AB_STATUS_C", "STATUS_C", "Status") if c in use.columns), None)
        use["status_norm"] = use[status_col].apply(normalize_status_label) if status_col else "Unknown"
    date_col = next(
        (
            c
            for c in ("OPEN_DATE_C", "CREATED_DATE", "CREATED_DATE_C", "CREATEDDATE")
            if c in use.columns
        ),
        None,
    )
    # When a report supplies an explicit clock, recompute from the source date
    # even if an upstream normalizer already added ``open_age_days`` using its
    # own render-time clock.  With no explicit clock, preserve the established
    # behavior and reuse a populated upstream age column when available.
    # Round 7 / Phase 3.2: the fallback clock is resolved with
    # ``datetime.now(timezone.utc)`` rather than deprecated ``utcnow()``.
    # Round 13 / Phase 2.2: source dates are parsed with ``utc=True`` before
    # subtraction, preserving offset-aware UTC age behavior.  Round 142's
    # explicit ``as_of`` path continues to use the same UTC subtraction.
    if date_col and (recompute_existing_age or "open_age_days" not in use.columns):
        dt = pd.to_datetime(use[date_col], errors="coerce", utc=True)
        use["open_age_days"] = (_resolve_risk_as_of_utc(as_of) - dt).dt.days
    elif "open_age_days" not in use.columns:
        use["open_age_days"] = pd.NA

    import canonical_metrics as cm  # local import avoids module-cycle risk

    # Round 53.1: score logical adoption-barrier records, not Snowflake fan-out
    # rows. Duplicate assignee/detail rows with the same ID should not inflate
    # risk factors or push the customer into a higher band.
    count = cm.count_total_barriers(use)

    if "ID" in use.columns:
        def _record_key(row: pd.Series) -> str:
            raw = row.get("ID")
            if pd.notna(raw) and str(raw).strip():
                return f"id::{str(raw).strip()}"
            return f"row::{row.name}"

        use["_r531_record_key"] = use.apply(_record_key, axis=1)
    else:
        use["_r531_record_key"] = [f"row::{idx}" for idx in use.index]

    _severity_weights = use["severity_norm"].apply(_severity_weight)
    record_severity_weight = _severity_weights.groupby(use["_r531_record_key"]).max()
    _severity_mass = float(record_severity_weight.sum())  # 0-4 per record
    open_count = cm.count_open_barriers(use)
    _aging_mask = (
        use["status_norm"].eq("Open")
        & (pd.to_numeric(use["open_age_days"], errors="coerce") >= 60)
    )
    aging_open_count = int(use.loc[_aging_mask, "_r531_record_key"].nunique())
    aging_points = min(float(aging_open_count) * 8.0, 20.0)
    volume_points = min(float(count) * 2.0, 15.0)

    _profile = resolve_risk_scoring_profile(scoring_profile)
    if _profile == RISK_SCORING_PROFILE_MAGNITUDE_FIRST:
        # Round 156: magnitude-primary. Four critical barriers must outscore
        # one; ten open barriers must outscore one open barrier. Severity mass
        # (Σ per-record 0-4 weight) and open *count* lead, each capped, with a
        # small secondary term so a small account is still elevated.
        #
        # Round 156 deep-verification: the secondary severity term is the
        # WORST severity present (max record weight / 4), NOT the mean ratio.
        # A mean ratio dips when a lower-severity record is added, which made
        # "add one open Low barrier" DECREASE the component by up to 0.44 pts
        # at capped all-critical bases (brute-force monotonicity hunt).  The
        # max-share term is non-decreasing under record addition, so the
        # magnitude_first component is strictly monotone: adding an open
        # barrier of any severity can never lower the score.  (The open-ratio
        # term is already monotone for added OPEN records since open<=total.)
        _worst_severity_share = (
            float(record_severity_weight.max()) / 4.0 if count else 0.0
        )
        severity_points = min(_severity_mass * 3.0, 40.0) + _worst_severity_share * 5.0
        open_points = min(float(open_count) * 6.0, 25.0) + (
            open_count / max(count, 1)
        ) * 5.0
    else:
        # Legacy (default): proportion-based severity & openness. Preserved
        # byte-for-byte so every shipped oracle/test is unchanged.
        severity_points = _severity_mass / max(count * 4, 1) * 45
        open_points = (open_count / max(count, 1)) * 30
    score = _clamp(severity_points + open_points + aging_points + volume_points)

    critical_high_count = cm.count_critical_barriers(
        use,
        mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH,
    )
    return {
        "score": score,
        "details": {
            "count": count,
            "critical_high_count": critical_high_count,
            "open_count": open_count,
            "aging_open_count": aging_open_count,
        },
    }


def _score_support_cases(
    customer_csone: pd.DataFrame,
    *,
    recent_window_days: int = 30,
    as_of: Any = None,
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

    use = add_case_lifecycle_fields(customer_csone)
    count = len(use)
    volume_points = min(float(count) * 2.5, 25.0)
    # Use cm.count_escalated so the risk score and the headline "Escalated"
    # tile in every report agree on the same definition of P1+P2.
    import canonical_metrics as cm  # local import avoids any future cycle
    escalated_count = int(cm.count_escalated(use))
    escalated_points = min(float(escalated_count) * 9.0, 35.0)
    bems_count = int(use["is_bems"].sum()) if "is_bems" in use.columns else int(detect_bems_mask(use).sum())
    bems_points = min(float(bems_count) * 12.0, 30.0)

    recent_count = 0
    if "open_date" in use.columns:
        # Round 13 / Phase 2.3: parse the lifecycle ``open_date`` with
        # utc=True so rows that carry explicit offsets (or were already
        # parsed tz-aware upstream) compare cleanly against a UTC
        # cutoff.  The previous implementation forced everything to
        # tz-naive via ``tz_localize(None)``, which both hid timezone
        # bugs and shifted the cutoff by up to 24h depending on the
        # worker's local zone.
        component_as_of = _resolve_risk_as_of_utc(as_of)
        cutoff = component_as_of - pd.to_timedelta(int(recent_window_days), unit="D")
        opened_at = pd.to_datetime(use["open_date"], errors="coerce", utc=True)
        recent_count = int(
            opened_at.between(cutoff, component_as_of, inclusive="both").sum()
        )
    recent_points = min(float(recent_count) * 2.5, 15.0)
    score = _clamp(volume_points + escalated_points + bems_points + recent_points)

    return {
        "score": score,
        "details": {
            "count": count,
            "escalated_count": escalated_count,
            "bems_count": bems_count,
            "recent_count": recent_count,
            "recent_window_days": int(recent_window_days),
            "break_fix_count": int((use["case_type_class"] == "break_fix_technical").sum()) if "case_type_class" in use.columns else 0,
            "provisioning_count": int((use["case_type_class"] == "provisioning_request").sum()) if "case_type_class" in use.columns else 0,
        },
    }


def _score_customer_pulse(
    customer_pulse: pd.DataFrame,
    *,
    scoring_profile: Optional[str] = None,
) -> Dict[str, Any]:
    # Round 156 deep-verification: under the (opt-in) magnitude_first profile,
    # MISSING sentiment data is excluded from the composite (score=None, like
    # the Round 7 contract sentinel) instead of scoring 0.0.  Scoring absent
    # pulse rows as 0.0 weighs "nobody recorded sentiment" into the composite
    # as if sentiment were measured healthy, diluting real risk at weight
    # 0.15 — the Round 2 / Phase 3.2 comment below documented the intent to
    # exclude, but the composite never honored ``excluded_from_score``.  The
    # legacy path is preserved byte-for-byte (default reports unchanged).
    _mf = (
        resolve_risk_scoring_profile(scoring_profile)
        == RISK_SCORING_PROFILE_MAGNITUDE_FIRST
    )
    if customer_pulse is None or customer_pulse.empty:
        if _mf:
            return {
                "score": None,
                "details": {
                    "count": 0,
                    "poor_bad_count": 0,
                    "backfill_excluded_count": 0,
                    "data_state": "missing",
                    "excluded_from_score": True,
                },
            }
        return {"score": 0.0, "details": {"count": 0, "poor_bad_count": 0, "backfill_excluded_count": 0}}

    raw_count = len(customer_pulse)
    use = _exclude_backfill_pulse_rows(customer_pulse)
    if use.empty:
        if _mf:
            return {
                "score": None,
                "details": {
                    "count": 0,
                    "poor_bad_count": 0,
                    "backfill_excluded_count": raw_count,
                    "data_state": "missing",
                    "excluded_from_score": True,
                },
            }
        return {"score": 0.0, "details": {"count": 0, "poor_bad_count": 0, "backfill_excluded_count": raw_count}}
    # Round 4 / Phase 3.5: align column priority with
    # ``cm.pulse_sentiment``.  The canonical helper prefers the
    # numeric ``SCORE__C`` field (then ``SCORE`` / ``PULSE_SCORE``)
    # and only falls back to a text rating when the numeric score is
    # absent.  The legacy implementation here ignored ``SCORE__C``
    # entirely, which produced narratives where the canonical pulse
    # paragraph said "healthy" but the risk score said "poor" (or
    # vice versa) for the same data.  Try the numeric path first.
    # Round 148: the canonical delivery adapter recomputes risk from the
    # customer-facing workbook.  Preserve the numeric pulse signal after
    # report_export_schema renames PULSE_SCORE to ``Pulse Score``.
    score_col = next(
        (c for c in ("SCORE__C", "SCORE", "PULSE_SCORE", "Pulse Score") if c in use.columns),
        None,
    )
    if score_col is not None:
        numeric_scores = pd.to_numeric(use[score_col], errors="coerce").dropna()
        if not numeric_scores.empty:
            from canonical_metrics import (
                PULSE_NEGATIVE_THRESHOLD_0_TO_10 as _NEG,
                PULSE_POSITIVE_THRESHOLD_0_TO_10 as _POS,
            )
            count = int(len(numeric_scores))
            poor_bad_count = int((numeric_scores <= _NEG).sum())
            neutral_count = int(((numeric_scores > _NEG) & (numeric_scores < _POS)).sum())
            poor_ratio = poor_bad_count / max(count, 1)
            neutral_ratio = neutral_count / max(count, 1)
            score = _clamp(poor_ratio * 100 + neutral_ratio * 30)
            return {
                "score": score,
                "details": {
                    "count": count,
                    "poor_bad_count": poor_bad_count,
                    "backfill_excluded_count": max(raw_count - count, 0),
                    "score_column": score_col,
                },
            }

    rating_col = next(
        (
            c
            for c in (
                "PULSE_RATING__C",
                "PULSE_RATING",
                "Rating",
                "RATING",
                "rating",  # Round 100: curated Compact Customer_Pulse export.
                "Pulse Rating",  # Round 148: friendly export-schema header.
                "Customer Pulse",
                "Customer Pulse Color",
            )
            if c in use.columns
        ),
        None,
    )
    if not rating_col:
        # Round 2 / Phase 3.2: missing rating column is a metadata
        # gap (the upstream feed did not return a rating field), NOT
        # evidence of "5.0 badness".  The legacy code returned a
        # fixed mid-band score that bled into composite risk and
        # falsely elevated otherwise-clean accounts.  Treat as a
        # neutral / excluded component and surface the gap via
        # ``no_rating_column`` so the composite scorer can choose to
        # exclude pulse from the weighted average.
        return {
            # Round 156: under magnitude_first the documented exclusion is
            # finally honored — ``None`` renormalizes the composite over the
            # measured components.  Legacy keeps 0.0 (composite ignored the
            # ``excluded_from_score`` flag historically; unchanged by default).
            "score": None if _mf else 0.0,
            "details": {
                "count": len(use),
                "poor_bad_count": 0,
                "backfill_excluded_count": max(raw_count - len(use), 0),
                "no_rating_column": True,
                "excluded_from_score": True,
            },
        }

    # Round 3: classify pulse ratings via canonical buckets so the
    # poor/neutral counts agree with ``cm.pulse_sentiment`` and the
    # narrative paragraphs the leader report shows. The previous
    # substring regex flagged "high risk" as poor but missed common
    # synonyms like "very poor" / "needs improvement" that the canonical
    # bucket recognizes (and conversely matched "fair-condition" as
    # neutral when it should be ignored).
    ratings = use[rating_col].fillna("").astype(str)
    _norm_buckets = ratings.map(_canonicalize_pulse_rating)
    poor_bad_count = int((_norm_buckets == "poor").sum())
    neutral_count = int((_norm_buckets == "neutral").sum())
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


def _score_action_plans(
    action_plans: pd.DataFrame,
    *,
    scoring_profile: Optional[str] = None,
) -> Dict[str, Any]:
    if action_plans is None or action_plans.empty:
        return {"score": 0.0, "details": {"count": 0, "unresolved_count": 0}}
    use = action_plans.copy()
    _profile = resolve_risk_scoring_profile(scoring_profile)
    status_col = next((c for c in ("STATUS_C", "STATUS__C", "Status") if c in use.columns), None)
    if status_col and _profile == RISK_SCORING_PROFILE_MAGNITUDE_FIRST:
        # Round 156 deep-verification: classify via the CANONICAL lifecycle
        # bucket instead of the legacy substring regex.  The regex
        # ``closed|resolved|complete|done`` matches *substrings*, so a plan
        # whose status is literally "Unresolved" (contains "resolved"),
        # "Incomplete" (contains "complete"), or "Abandoned" (contains
        # "done") was silently counted as RESOLVED — understating risk with
        # exactly the false-match class canonical_metrics R64 eliminated.
        # ``resolved`` here means canonical bucket == "Completed"; blocked /
        # cancelled / unknown plans remain unresolved commitments, matching
        # both canonical views (lifecycle bucket and count_open's closed-set).
        import canonical_metrics as cm  # local import avoids module-cycle risk

        _buckets = use[status_col].map(cm._action_plan_status_bucket)  # noqa: SLF001
        unresolved_count = int((_buckets != "Completed").sum())
    elif status_col:
        resolved_mask = use[status_col].fillna("").astype(str).str.contains(
            r"closed|resolved|complete|done", case=False, regex=True
        )
        unresolved_count = int((~resolved_mask.fillna(False)).sum())
    else:
        unresolved_count = len(use)
    count = len(use)
    unresolved_ratio = unresolved_count / max(count, 1)
    if _profile == RISK_SCORING_PROFILE_MAGNITUDE_FIRST:
        # Round 156: the *number* of open commitments is the risk-relevant
        # quantity. One open plan of one no longer maxes the dimension; three
        # open of twenty (real remediation load) outscores it.
        #   A(1 of 1) -> 12 + 40  = 52 ; B(3 of 20) -> 36 + 6 = 42 ;
        #   C(5 of 5) -> 60 + 40  = 100
        score = _clamp(min(float(unresolved_count) * 12.0, 60.0) + unresolved_ratio * 40.0)
    else:
        # Legacy (default): pure ratio, preserved byte-for-byte.
        score = _clamp(unresolved_ratio * 100)
    return {"score": score, "details": {"count": count, "unresolved_count": unresolved_count}}


def _score_incidents(ext_incidents: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
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
                "count_cap_applied": False,
            },
        }
    count = len(ext_incidents)
    active_count = 0
    high_impact_count = 0
    critical_impact_count = 0
    for incident in ext_incidents:
        status = str(incident.get("status", "")).strip().lower()
        impact = str(incident.get("impact_level", "")).strip().lower()
        # Accept the new normalized vocabulary AND the legacy raw words
        # in case any caller still passes raw Statuspage payloads.
        if status in {"active", "investigating", "identified", "monitoring", "major_outage"}:
            active_count += 1
        # Round 2 / Phase 5.5: ``Critical`` is now preserved as a
        # distinct impact band by ``fetch_status_incidents``.  Score
        # critical incidents at a higher weight than ``High`` so a
        # full outage doesn't read as equivalent to a high-impact
        # event.
        if impact == "critical" or status == "major_outage":
            critical_impact_count += 1
            high_impact_count += 1  # critical is also high-impact for legacy callers
        elif impact in {"high", "major"}:
            high_impact_count += 1
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
            "count_cap_applied": bool(count > _count_capped),
        },
    }


def _score_contract(
    customer_subs: pd.DataFrame,
    *,
    scoring_profile: Optional[str] = None,
) -> Dict[str, Any]:
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
    inactive_subs = 0
    provisioning_subs = 0
    unknown_status_subs = 0
    # Round 3: classify renewal risk via a canonical category lookup
    # rather than free-text regex. The previous substring match would
    # match "Highest Quality" as "high" (false positive) and miss
    # "CRT" / "CRIT" abbreviations (false negative).
    if "RENEWAL_RISK_CATEGORY" in use.columns:
        _cats = use["RENEWAL_RISK_CATEGORY"].fillna("").astype(str).map(_canonicalize_renewal_category)
        high_risk_subs = int(_cats.isin({"high", "critical"}).sum())
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
        unknown_status_subs = int((_statuses == "unknown").sum())
    count = len(use)
    if resolve_risk_scoring_profile(scoring_profile) == RISK_SCORING_PROFILE_MAGNITUDE_FIRST:
        # Round 156: magnitude-primary. Three at-risk subs of twenty (a real
        # renewal exposure) must outscore one at-risk sub of one. Absolute
        # counts lead, each capped, with a small proportion term.
        #   1 high-risk of 1  -> 18 + 15 = 33 ; 3 high-risk of 20 -> 54 + 2.25 = 56
        _at_risk_ratio = (high_risk_subs + inactive_subs) / max(count, 1)
        score = _clamp(
            min(float(high_risk_subs) * 18.0, 55.0)
            + min(float(inactive_subs) * 10.0, 30.0)
            + _at_risk_ratio * 15.0
        )
    else:
        # Legacy (default): proportion-based, preserved byte-for-byte.
        score = _clamp((high_risk_subs / max(count, 1)) * 70 + (inactive_subs / max(count, 1)) * 40)
    return {
        "score": score,
        "details": {
            "count": count,
            "high_risk_subs": high_risk_subs,
            "inactive_subs": inactive_subs,
            "provisioning_subs": provisioning_subs,
            "unknown_status_subs": unknown_status_subs,
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

    total_activity = (
        (0 if customer_ab is None else len(customer_ab))
        + (0 if customer_csone is None else len(customer_csone))
        + (0 if customer_pulse is None else len(customer_pulse))
        + (0 if action_plans is None else len(action_plans))
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
        return {"score": 30.0, "details": {"total_activity": 0, **base_meta}}
    if total_activity > 20:
        return {"score": 60.0, "details": {"total_activity": total_activity, **base_meta}}
    if total_activity > 10:
        return {"score": 40.0, "details": {"total_activity": total_activity, **base_meta}}
    return {"score": 15.0, "details": {"total_activity": total_activity, **base_meta}}


_R155_TECH_COLUMN_CANDIDATES = (
    "sub_technology",
    "SUB_TECHNOLOGY_C",
    "Sub Technology",
    "SUB_TECH_C",
    "TECHNOLOGY_C",
    "Technology",
    "sub_tech",
)
_R155_OPEN_STATUS_CANDIDATES = ("AB_STATUS_C", "STATUS_C", "STATUS__C", "Status", "status_norm")
_R155_UNSPECIFIC_TECH = {"", "other", "unknown", "other / unclassified", "other/unknown", "n/a", "nan", "unclassified"}


def _r155_tech_series(df: Optional[pd.DataFrame]) -> Optional["pd.Series"]:
    """Round 155 / B2: the sub-technology of each OPEN row, or None.

    Deterministic and defensive: probes the same candidate technology and
    status columns the component scorers use, keeps only open rows, and drops
    unspecific buckets so a genuine ``Webex Calling`` overlap is not diluted by
    ``Other / Unclassified``.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    tech_col = next((c for c in _R155_TECH_COLUMN_CANDIDATES if c in df.columns), None)
    if tech_col is None:
        return None
    use = df
    status_col = next((c for c in _R155_OPEN_STATUS_CANDIDATES if c in df.columns), None)
    if status_col is not None:
        status = use[status_col].fillna("").astype(str).str.strip().str.casefold()
        # "open" is the primary signal; closed/resolved/completed are excluded.
        closed = status.str.contains("close|resolv|complete|cancel", regex=True, na=False)
        use = use[~closed]
    if use.empty:
        return None
    tech = use[tech_col].fillna("").astype(str).str.strip()
    tech = tech[~tech.str.casefold().isin(_R155_UNSPECIFIC_TECH)]
    return tech if not tech.empty else None


def _r155_compound_risk_factor(
    customer_ab: Optional[pd.DataFrame],
    customer_csone: Optional[pd.DataFrame],
) -> Optional[str]:
    """Round 155 / B2: the compound-risk signal — barriers AND cases, same tech.

    The single most differentiating customer-success insight: when a customer
    has both an open adoption barrier and an open TAC case in the *same*
    technology area, that is a compound risk one focused action can clear. The
    app's AI prompt already asked for this correlation (adoptiq_backend.py
    CORRELATION MANDATE); this makes it a deterministic, auditable signal so it
    can flow into the concise report's driver column without the model
    inventing it. Returns the top overlapping technology, or None.
    """
    ab_tech = _r155_tech_series(customer_ab)
    tac_tech = _r155_tech_series(customer_csone)
    if ab_tech is None or tac_tech is None:
        return None
    ab_counts = ab_tech.value_counts()
    tac_counts = tac_tech.value_counts()
    # Match case-insensitively but display the barrier-side label.
    ab_by_key = {str(k).casefold(): (str(k), int(v)) for k, v in ab_counts.items()}
    tac_by_key = {str(k).casefold(): int(v) for k, v in tac_counts.items()}
    overlaps = []
    for key, (label, ab_n) in ab_by_key.items():
        if key in tac_by_key:
            overlaps.append((ab_n + tac_by_key[key], label, ab_n, tac_by_key[key]))
    if not overlaps:
        return None
    overlaps.sort(reverse=True)
    _combined, label, ab_n, tac_n = overlaps[0]
    return (
        f"Compound risk in {label}: {ab_n} open barrier(s) + {tac_n} open TAC case(s) "
        "in the same technology — one focused action can clear the connected cluster "
        f"{format_inline_source('Adoption Barriers + Support Cases (TAC)', fields=['sub_technology', 'AB_STATUS_C', 'Status'])}"
    )


def _r155_next_best_action(
    *,
    compound: Optional[str],
    ab: Dict[str, Any],
    support: Dict[str, Any],
    pulse: Dict[str, Any],
    actions: Dict[str, Any],
    contract: Dict[str, Any],
    risk_band: str,
    recommendations: List[str],
) -> str:
    """Round 155: the specific, deterministic 'do this first' for a customer.

    Replaces the band-level boilerplate that made every same-band customer's
    recommendation identical (audit R151-04).  Orders by how acute the signal
    is and how directly a CSM can act on it, and names the actual count and
    lever so the action is concrete.  Falls back to the band recommendation
    only when no specific signal is present.
    """
    # 1. Compound risk -- one coordinated fix clears the largest connected
    #    cluster.  The highest-leverage move when it exists.
    if compound:
        tech = ""
        try:
            # "Compound risk in {Tech}: ..." -> Tech
            tech = compound.split("Compound risk in ", 1)[1].split(":", 1)[0].strip()
        except Exception:  # noqa: BLE001
            tech = ""
        if tech:
            return (
                f"Coordinate one {tech} remediation: the open barrier(s) and TAC case(s) "
                "share this technology, so a single root-cause fix clears both."
            )

    # 2. BEMS break-fix escalations -- engineering-owned, time-critical.
    bems = int(support.get("bems_count", 0) or 0)
    if bems > 0:
        return (
            f"Drive the {bems} BEMS break-fix escalation(s) to a committed engineering "
            "ETA; these are the acute, engineering-owned risk."
        )

    # 3. Escalated (P1/P2) TAC cases -- weekly resolution cadence.
    escalated = int(support.get("escalated_count", 0) or 0)
    if escalated > 0:
        return (
            f"Run a weekly review to resolve the {escalated} escalated P1/P2 TAC case(s); "
            "escalations are the strongest near-term churn signal."
        )

    # 4. Aging barriers (open > 60 days) -- stalled adoption needing an owner.
    aging = int(ab.get("aging_open_count", 0) or 0)
    if aging > 0:
        return (
            f"Assign an owner and a target date to the {aging} adoption barrier(s) open "
            "over 60 days; stalled barriers block adoption and renewal."
        )

    # 5. Critical/high barriers -- adoption blockers.
    crit = int(ab.get("critical_high_count", 0) or 0)
    if crit > 0:
        return (
            f"Work the {crit} critical/high adoption barrier(s) with the customer's "
            "technical owner; these are the active blockers to value realization."
        )

    # 6. Poor/bad pulse -- sentiment risk.
    poor = int(pulse.get("poor_bad_count", 0) or 0)
    if poor > 0:
        return (
            f"Schedule an executive touchpoint: {poor} poor/bad pulse record(s) signal "
            "eroding sentiment before it becomes a renewal risk."
        )

    # 7. Unresolved action plans -- follow-through gap.
    unresolved = int(actions.get("unresolved_count", 0) or 0)
    if unresolved > 0:
        return (
            f"Close out the {unresolved} unresolved action plan(s) with due dates; open "
            "plans are commitments the customer is tracking."
        )

    # 8. Contract-level risk (inactive / provisioning / high-risk subs).
    high_risk_subs = int(contract.get("high_risk_subs", 0) or contract.get("high_risk_sub_count", 0) or 0)
    if high_risk_subs > 0:
        return (
            f"Review the {high_risk_subs} at-risk subscription(s) ahead of renewal; "
            "confirm adoption and value before the decision window."
        )

    # 9. No acute signal -- fall back to the band cadence.
    return recommendations[0] if recommendations else "Maintain standard success cadence and monitor emerging risks."


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
    as_of: Any = None,
    scoring_profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute deterministic weighted customer risk score (0-100).

    Phase 3.4: ``recent_window_days`` lets callers thread the
    report-level analysis horizon (e.g. 90) into the support-case
    momentum scorer instead of always using a hardcoded 30-day window.

    Round 142: ``as_of`` pins every time-sensitive component to the same
    explicit UTC clock.  It remains optional for backward compatibility;
    omitted callers retain render-time behavior.

    Round 156: ``scoring_profile`` selects the component scoring model
    (``legacy`` default vs. opt-in ``magnitude_first``).  Resolved via
    :func:`resolve_risk_scoring_profile`, so ``None`` honors the
    ``ADOPTIQ_RISK_SCORING_PROFILE`` env / ``Config`` setting and defaults
    to ``legacy`` — leaving every shipped report byte-identical.
    """
    risk_as_of = _resolve_risk_as_of_utc(as_of)
    active_profile = resolve_risk_scoring_profile(scoring_profile)
    pulse_input = customer_pulse if customer_pulse is not None else pd.DataFrame()
    pulse_for_scoring = _exclude_backfill_pulse_rows(pulse_input)
    ab_component = _score_adoption_barriers(
        customer_ab if customer_ab is not None else pd.DataFrame(),
        as_of=risk_as_of,
        recompute_existing_age=as_of is not None,
        scoring_profile=active_profile,
    )
    support_component = _score_support_cases(
        customer_csone if customer_csone is not None else pd.DataFrame(),
        recent_window_days=recent_window_days,
        as_of=risk_as_of,
    )
    pulse_component = _score_customer_pulse(pulse_input, scoring_profile=active_profile)
    action_component = _score_action_plans(
        customer_action_plans if customer_action_plans is not None else pd.DataFrame(),
        scoring_profile=active_profile,
    )
    incident_component = _score_incidents(ext_incidents)
    contract_component = _score_contract(
        customer_subs if customer_subs is not None else pd.DataFrame(),
        scoring_profile=active_profile,
    )
    engagement_component = _score_engagement(
        customer_ab if customer_ab is not None else pd.DataFrame(),
        customer_csone if customer_csone is not None else pd.DataFrame(),
        pulse_for_scoring,
        customer_action_plans if customer_action_plans is not None else pd.DataFrame(),
    )

    # Round 7 / Phase 3.3: contract sub-score may now be ``None`` when
    # subscription data is missing. Treat the missing component as
    # weight-zero so the composite is renormalised over the remaining
    # signals instead of multiplying by ``None``.
    _components_for_weighting = [
        (ab_component["score"], weights.adoption_barriers),
        (support_component["score"], weights.support_cases),
        (pulse_component["score"], weights.customer_pulse),
        (action_component["score"], weights.action_plans),
        (incident_component["score"], weights.incidents),
        (contract_component["score"], weights.contract),
        (engagement_component["score"], weights.engagement),
    ]
    _present = [(s, w) for (s, w) in _components_for_weighting if s is not None]
    _total_weight = sum(w for _, w in _present)
    if _total_weight <= 0:
        weighted_score = 0.0
    else:
        weighted_score = sum(s * w for s, w in _present) / _total_weight * sum(
            w for _, w in _components_for_weighting
        )
    score_0_100 = round(_clamp(weighted_score), 1)
    score_0_10 = round(score_0_100 / 10.0, 1)
    risk_band = _risk_band(score_0_100)

    risk_factors: List[str] = []
    # Round 155 / B2: the compound-risk correlation leads the driver list so it
    # survives the concise report's top-2 truncation — it is the most
    # actionable single signal a CSM can get.
    _r155_compound = _r155_compound_risk_factor(customer_ab, customer_csone)
    if _r155_compound:
        risk_factors.append(_r155_compound)
    # Round 157 / B4: technology-concentration driver.  When a customer's open
    # TAC cases cluster in one technology (>=2 cases, majority share), name it
    # — "3 cases" becomes "3 cases, concentrated in Webex Calling", telling
    # the CSM *where* the pain is, deterministically from the CSOne export.
    # Skipped when the compound factor already names the technology overlap.
    if not _r155_compound:
        _r157_tech = _r155_tech_series(customer_csone)
        if _r157_tech is not None and len(_r157_tech) >= 2:
            _r157_counts = _r157_tech.value_counts()
            _r157_top_label = str(_r157_counts.index[0])
            _r157_top_n = int(_r157_counts.iloc[0])
            if _r157_top_n * 2 > int(len(_r157_tech)):
                risk_factors.append(
                    f"Open TAC cases concentrated in {_r157_top_label} "
                    f"({_r157_top_n} of {int(len(_r157_tech))} open cases) "
                    f"{format_inline_source('Support Cases (TAC)', fields=['sub_technology', 'Status'])}"
                )
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

    # Round 155 / B2+: the single most actionable next step, derived
    # deterministically from the customer's actual acute signal -- not the
    # band-level boilerplate above.  A CSM reads this and knows exactly what to
    # do first and why.  Priority orders by how acute and how directly the CSM
    # can act on each lever.
    next_best_action = _r155_next_best_action(
        compound=_r155_compound,
        ab=ab_component["details"],
        support=support_component["details"],
        pulse=pulse_component["details"],
        actions=action_component["details"],
        contract=contract_component["details"],
        risk_band=risk_band,
        recommendations=recommendations,
    )

    return {
        "customer_name": customer_name,
        "risk_as_of_utc": risk_as_of.isoformat(),
        "risk_score_0_100": score_0_100,
        "risk_score_0_10": score_0_10,
        "risk_band": risk_band,
        "next_best_action": next_best_action,
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
    avg_score = round(sum(scores) / max(len(scores), 1), 1)
    max_score = round(max(scores) if scores else 0.0, 1)
    result = {
        "total_customers": len(profiles),
        "average_risk_score_0_100": avg_score,
        "highest_risk_score_0_100": max_score,
        "high_risk_customers": high_risk,
        "medium_risk_customers": band_counts["MEDIUM"],
        "low_risk_customers": band_counts["LOW"],
        "healthy_customers": band_counts["HEALTHY"],
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
    return band_to_health_grade(
        None,
        portfolio_summary.get("average_risk_score_0_100"),
    )
