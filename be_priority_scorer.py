"""Round 79 / Build 55 / Phase 1 (B1): independent BE-engineering priority
scorer for adoption barriers.

Why this module exists
----------------------
The Build 53 acceptance audit confirmed a long-standing usability gap that
Round 78 did not close: when a CSConsole portfolio carries 161 ABs all
marked CSConsole_Severity = "High", neither the Comprehensive nor the
Leader DOCX gives a Cisco backend-engineering reader an actionable answer
to "which 5-10 of these should we focus on first?".  The root cause is
that the existing risk pipeline (``risk_scoring.compute_customer_risk_profile``)
delivers a *customer*-level composite -- exactly what the renewal CSE/CSM
needs -- but the BE engineer wants a *barrier*-level priority that is
INDEPENDENT of CSConsole field values (which the CSConsole UI default
heavily skews to "High").

This module ships the deterministic half of the Round 79 hybrid design:

1. ``compute_be_priority_score(ab_row, customer_risk, customer_pulse, customer_arr)``
   returns a 0-100 BE-priority score per AB, weighting six signals:

   - severity (CAPPED to 0.7 for High so all-High portfolios still vary)
   - customer composite risk (R65 / R67 0-100 scale)
   - customer pulse negativity (R66 threshold 5.0)
   - barrier age in days (R64 60d aging baseline; saturates at 90)
   - content signal (deterministic regex over title + description)
   - escalation flag (AB_ESCALATE_C true OR BEMS reference in body)

2. ``compute_be_priority_scores_for_frame(ab_norm, risk_profiles, pulse_df)``
   vectorises the per-row scorer across an entire AB DataFrame and threads
   the customer context lookups (risk + pulse) so callers do not have to
   re-implement the join logic.

3. ``compute_be_focus_areas(ab_scored, max_per_tech, min_cluster_score)``
   rolls per-AB scores up to a per-(sub_technology, ab_category_final)
   cluster ranking with a deterministic ordering contract: Technology ASC,
   Cluster_Focus_Score DESC, Theme ASC.  This is the SSoT the new
   ``BE_Focus_Areas`` XLSX sheet and the matching Word section read.

The LLM second-pass (``be_priority_llm_classifier.classify_top_n_be_barriers``)
runs AFTER this module's deterministic ranking and only LABELS the top-N
with a 6-enum triage tag.  The deterministic score from this module is
ALWAYS the canonical sort key for both the XLSX rank and the Word section
ordering -- the LLM never changes the score, it only adds context.

Determinism contract
--------------------
* Same input frame -> same output bytes (mergesort + index preservation).
* No wall-clock dependencies in the score formula.
* No LLM calls, no network calls, no random sources.
* Severity weight CAPPED so all-High portfolios show meaningful score
  variance instead of pinning the entire portfolio at 100.

Made-with: Cursor.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from data_normalization import normalize_severity_label

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round 121 / G3: bare-sentinel display relabel for BE Focus Areas.
# ---------------------------------------------------------------------------
#
# ``compute_be_focus_areas`` normalises blank / NaN ``sub_technology`` and
# ``ab_category_final`` to the literal ``"Unknown"`` so the groupby key is
# stable.  That sentinel then surfaces verbatim in the user-facing tables --
# e.g. the Leader BE Focus Areas row ``Unknown | 482.6 | ...`` and the
# Comprehensive ``Other/Unknown`` technology heading.  ``relabel_unclassified``
# maps ONLY the exact bare-sentinel tokens (not substrings) to the friendlier
# ``"Other / Unclassified"`` label at the DISPLAY layer -- the grouping /
# scoring keys are untouched so row counts and ordering are preserved.
_R121_UNCLASSIFIED_TOKENS = frozenset(
    {
        "",
        "unknown",
        "other/unknown",
        "other / unknown",
        "nan",
        "none",
        "n/a",
        "na",
        "unclassified",
        "uncategorized",  # Round 124 / F8: _normalize_category emits this sentinel
    }
)
_R121_UNCLASSIFIED_LABEL = "Other / Unclassified"


def relabel_unclassified(value: Any) -> str:
    """Round 121 / G3: map a bare sentinel to ``"Other / Unclassified"``.

    Conservative -- only an exact (case/whitespace-insensitive) match against
    a known sentinel token is relabeled, so genuine labels that merely
    *contain* the word "unknown" (e.g. ``"Unknown Protocol"``) are preserved.
    """

    text = "" if value is None else str(value).strip()
    if text.lower() in _R121_UNCLASSIFIED_TOKENS:
        return _R121_UNCLASSIFIED_LABEL
    return text


# ---------------------------------------------------------------------------
# Round 79 / B1: tunable signal weights.  Sum is exactly 1.0 so the final
# ``score = sum(signal * weight) * 100`` is bounded to [0, 100].
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BePriorityWeights:
    """Round 79 / B1: weighted signals composing the 0-100 BE-priority score."""

    severity_capped: float = 0.20
    customer_risk: float = 0.25
    pulse_negativity: float = 0.15
    age: float = 0.20
    content_signal: float = 0.15
    escalation: float = 0.05


_DEFAULT_WEIGHTS = BePriorityWeights()


# Round 79 / B1: severity translates to a CAPPED 0-1 weight so even an
# all-High portfolio shows score variance instead of every barrier
# pinning at 100.  With these caps the maximum severity contribution is:
#
#     Critical = 1.0 * 0.20 * 100 =  20 points
#     High     = 0.7 * 0.20 * 100 =  14 points
#     Medium   = 0.4 * 0.20 * 100 =   8 points
#     Low      = 0.2 * 0.20 * 100 =   4 points
#     Unknown  = 0.3 * 0.20 * 100 =   6 points (between Low and Medium)
#
# So an "all-High" portfolio with maxed-out other signals reaches at most
# 14 + 25 + 15 + 20 + 15 + 5 = 94 -- only Critical can hit 100.
_SEVERITY_CAPPED: Dict[str, float] = {
    "Critical": 1.0,
    "High": 0.7,
    "Medium": 0.4,
    "Low": 0.2,
    "Unknown": 0.3,
}


# Round 79 / B1: deterministic content classifier (regex only, no NLP).
# Strong tokens boost the score (real engineering blockers); weak tokens
# depress the score (training / enhancement / how-do-I).  Mixed contexts
# net out via the [0, 1] clamp.
_STRONG_BLOCKER_RE = re.compile(
    r"\b("
    r"data\s+loss"
    r"|outage"
    r"|production\s+(?:down|broken)"
    r"|blocker"
    r"|blocking"
    r"|regression"
    r"|crash(?:es|ed|ing)?"
    r"|cannot\s+(?:login|access|deploy)"
    r"|critical\s+failure"
    r"|cve-\d{4}"
    r"|psirt"
    r"|security\s+(?:vuln|issue|exposure)"
    r")\b",
    re.IGNORECASE,
)
_WEAK_NON_BLOCKER_RE = re.compile(
    r"\b("
    r"would\s+like"
    r"|enhancement"
    r"|nice\s+to\s+have"
    r"|training"
    r"|documentation"
    r"|how\s+do\s+i"
    r"|tutorial"
    r"|walkthrough"
    r"|onboarding\s+(?:question|help)"
    r")\b",
    re.IGNORECASE,
)

# Round 79 / B1: BEMS reference detector (matches BEMS, BEMSCS, BEMSCSC,
# and assorted suffixes that the Cisco internal escalation queue uses).
_BEMS_REF_RE = re.compile(r"\bbems(?:csc|cs)?[a-z0-9]*\b", re.IGNORECASE)


def _content_signal(text: Optional[str]) -> float:
    """Return a 0-1 content signal from concatenated AB title + description.

    Strong blocker tokens are worth +0.3 each; weak non-blocker tokens
    are worth -0.2 each.  Result is clamped to [0, 1].  ``None`` and
    non-string inputs return 0 (no signal).
    """

    if not text or not isinstance(text, str):
        return 0.0
    s = text.lower()
    strong = len(_STRONG_BLOCKER_RE.findall(s))
    weak = len(_WEAK_NON_BLOCKER_RE.findall(s))
    raw = strong * 0.3 - weak * 0.2
    if raw <= 0.0:
        return 0.0
    if raw >= 1.0:
        return 1.0
    return raw


def _open_age_days(row: pd.Series) -> float:
    """Resolve open_age_days defensively.

    Returns 0.0 when the column is missing, NaN, infinite, or non-numeric.
    Negative values (a row dated in the future) are clamped to 0 so the
    downstream age-weight saturation arithmetic stays well-defined.
    """

    try:
        v = row.get("open_age_days")
    except Exception:  # noqa: BLE001
        return 0.0
    if v is None:
        return 0.0
    try:
        if pd.isna(v):
            return 0.0
    except (TypeError, ValueError):
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f) or f < 0.0:
        return 0.0
    return f


def _resolve_severity_norm(row: pd.Series) -> str:
    """Resolve severity_norm with a fall-back through the raw Snowflake
    severity columns when ``_prepare_ab`` did not run upstream."""

    try:
        sev = row.get("severity_norm")
    except Exception:  # noqa: BLE001
        sev = None
    if isinstance(sev, str) and sev.strip():
        return sev.strip()
    for raw_col in ("SEVERITY_C", "severity_c", "Severity"):
        try:
            raw = row.get(raw_col)
        except Exception:  # noqa: BLE001
            continue
        if raw is not None:
            try:
                norm = normalize_severity_label(raw)
            except Exception:  # noqa: BLE001
                continue
            if norm:
                return norm
    return "Unknown"


def _severity_capped_weight(row: pd.Series) -> float:
    sev = _resolve_severity_norm(row)
    return _SEVERITY_CAPPED.get(sev, _SEVERITY_CAPPED["Unknown"])


def _pulse_weight(pulse: Optional[float]) -> float:
    """Pulse 0-10; lower = more negative -> higher weight.

    ``None`` and non-finite values map to a neutral 0.5 (the mid-range).
    Pulse <= 5 (R66 threshold) maps to a positive weight that increases
    monotonically as pulse drops.  Pulse > 5 saturates to 0.
    """

    if pulse is None:
        return 0.5
    try:
        p = float(pulse)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(p):
        return 0.5
    raw = (5.0 - p) / 5.0
    if raw <= 0.0:
        return 0.0
    if raw >= 1.0:
        return 1.0
    return raw


def _age_weight(open_age_days: float) -> float:
    """Linear ramp 0->1 across days 0->90, then saturate at 1.0."""

    if open_age_days <= 0.0:
        return 0.0
    if open_age_days >= 90.0:
        return 1.0
    return open_age_days / 90.0


def _customer_risk_weight(customer_risk: Optional[float]) -> float:
    if customer_risk is None:
        return 0.0
    try:
        v = float(customer_risk)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    if v <= 0.0:
        return 0.0
    if v >= 100.0:
        return 1.0
    return v / 100.0


def _is_truthy_flag(value: Any) -> bool:
    """Round 79 / B1: defensive truthiness for the AB_ESCALATE_C column.

    The column shows up as bool, int, float (0/1), or string ("true"/"yes"/
    "1") depending on the upstream Snowflake -> pandas coercion path.
    """

    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            if pd.isna(value):
                return False
        except (TypeError, ValueError):
            return False
        return float(value) >= 1.0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "t"}
    return False


def _escalation_signal(row: pd.Series) -> float:
    """1.0 when AB_ESCALATE_C true OR BEMS reference found in title /
    description.  0.0 otherwise."""

    try:
        flag = row.get("AB_ESCALATE_C")
    except Exception:  # noqa: BLE001
        flag = None
    if _is_truthy_flag(flag):
        return 1.0

    parts: List[str] = []
    for col in ("title", "description"):
        try:
            v = row.get(col)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if not parts:
        return 0.0
    try:
        if _BEMS_REF_RE.search(" ".join(parts)):
            return 1.0
    except Exception:  # noqa: BLE001
        return 0.0
    return 0.0


def compute_be_priority_score(
    ab_row: pd.Series,
    *,
    customer_risk: Optional[float] = None,
    customer_pulse: Optional[float] = None,
    customer_arr: Optional[float] = None,  # noqa: ARG001 (reserved for R80)
    weights: BePriorityWeights = _DEFAULT_WEIGHTS,
) -> Dict[str, Any]:
    """Compute a deterministic 0-100 BE-engineering priority score for
    a single adoption barrier row.

    Round 79 / B1 contract:

    * Returns ``{"score": float, "components": dict, "contributions": dict,
      "top_signal": str}``.
    * Score is rounded to 1 decimal place so XLSX cells render cleanly.
    * The returned ``components`` dict is the per-signal 0-1 weight
      BEFORE multiplication by the weight constant; ``contributions`` is
      the post-multiplication 0-100-space contribution.  Keeping both
      lets the test suite pin the formula and lets the operator-facing
      diagnostic surface explain WHY a given score landed where it did.

    Determinism: same row + same kwargs -> same dict, byte-for-byte.
    No wall-clock or random calls.
    """

    sev_w = _severity_capped_weight(ab_row)
    cust_w = _customer_risk_weight(customer_risk)
    pulse_w = _pulse_weight(customer_pulse)
    age_w = _age_weight(_open_age_days(ab_row))
    title = ""
    desc = ""
    try:
        t = ab_row.get("title")
        if isinstance(t, str):
            title = t
    except Exception:  # noqa: BLE001
        title = ""
    try:
        d = ab_row.get("description")
        if isinstance(d, str):
            desc = d
    except Exception:  # noqa: BLE001
        desc = ""
    content_w = _content_signal(f"{title}\n{desc}".strip())
    esc_w = _escalation_signal(ab_row)

    components_raw: Dict[str, float] = {
        "severity_capped_norm": float(sev_w),
        "customer_risk_norm": float(cust_w),
        "pulse_neg_norm": float(pulse_w),
        "age_norm": float(age_w),
        "content_norm": float(content_w),
        "escalation_norm": float(esc_w),
    }

    contributions: Dict[str, float] = {
        "severity_capped": sev_w * weights.severity_capped * 100.0,
        "customer_risk": cust_w * weights.customer_risk * 100.0,
        "pulse_negativity": pulse_w * weights.pulse_negativity * 100.0,
        "age": age_w * weights.age * 100.0,
        "content_signal": content_w * weights.content_signal * 100.0,
        "escalation": esc_w * weights.escalation * 100.0,
    }
    total = sum(contributions.values())
    if not math.isfinite(total):
        total = 0.0
    if total < 0.0:
        total = 0.0
    elif total > 100.0:
        total = 100.0

    if total > 0.0:
        top_signal = max(contributions.items(), key=lambda kv: kv[1])[0]
    else:
        top_signal = "none"

    return {
        "score": round(float(total), 1),
        "components": components_raw,
        "contributions": contributions,
        "top_signal": top_signal,
    }


def _resolve_pulse_by_customer(pulse_df: Optional[pd.DataFrame]) -> Dict[str, float]:
    """Aggregate the pulse score per customer (mean over the scope window).

    Returns an empty dict when ``pulse_df`` is None / empty / missing the
    expected columns -- callers fall back to the neutral (None) weight
    which maps to 0.5 inside ``_pulse_weight``.
    """

    if pulse_df is None or getattr(pulse_df, "empty", True):
        return {}
    try:
        cust_col = next(
            (
                c
                for c in (
                    "BU_NAME",
                    "customer_name",
                    "CUSTOMER_NAME",
                    "Customer",
                )
                if c in pulse_df.columns
            ),
            None,
        )
        score_col = next(
            (
                c
                for c in (
                    "PULSE_SCORE_C",
                    "pulse_score",
                    "PULSE",
                    "Pulse",
                    "Pulse_Score",
                    # Round 79 / B2: the canonical CSConsole / Snowflake pulse
                    # column naming used elsewhere in the codebase
                    # (canonical_metrics.PULSE_NEGATIVE_THRESHOLD_0_TO_10).
                    "pulse_score_0_to_10",
                    "Pulse_Score_0_to_10",
                )
                if c in pulse_df.columns
            ),
            None,
        )
        if not cust_col or not score_col:
            return {}
        grouped = (
            pulse_df[[cust_col, score_col]]
            .dropna()
            .groupby(cust_col)[score_col]
            .mean()
        )
        # Round 79 / B2: lowercase the keys so the lookup at the consumer
        # side is case-insensitive (CSConsole pulse rows occasionally arrive
        # with "ACME CORP" while AB rows carry "Acme Corp").
        return {
            str(k).strip().lower(): float(v)
            for k, v in grouped.items()
            if str(k).strip()
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Round 79 / B1: pulse aggregation skipped (%s)", exc)
        return {}


def _resolve_customer_risk(
    customer_name: Any,
    risk_profiles: Optional[Dict[str, Any]],
) -> Optional[float]:
    """Pull the per-customer risk_score from the canonical risk_profiles dict.

    Falls back through ``risk_score`` -> ``risk_score_0_100`` to honor the
    R67/B1 cross-format parity contract; returns ``None`` when the
    customer is unknown to the risk pipeline.
    """

    if not customer_name or not risk_profiles:
        return None
    try:
        key = str(customer_name).strip()
        if not key:
            return None
        profile = risk_profiles.get(key)
        if not isinstance(profile, dict):
            return None
        for cand in ("risk_score", "risk_score_0_100"):
            v = profile.get(cand)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None
    except Exception:  # noqa: BLE001
        return None


def compute_be_priority_scores_for_frame(
    ab_norm: pd.DataFrame,
    risk_profiles: Optional[Dict[str, Any]] = None,
    pulse_df: Optional[pd.DataFrame] = None,
    weights: BePriorityWeights = _DEFAULT_WEIGHTS,
) -> pd.DataFrame:
    """Round 79 / B1: vectorize compute_be_priority_score across an AB frame.

    Returns a copy of ``ab_norm`` enriched with the BE-priority columns
    (``be_priority_score``, ``be_top_signal``, and the six 0-1 component
    columns).  ``None`` / empty input -> empty DataFrame (callers must
    handle this).  Index is preserved so downstream joins still line up.
    """

    if ab_norm is None or ab_norm.empty:
        return pd.DataFrame()

    df = ab_norm.copy()
    pulse_by_customer = _resolve_pulse_by_customer(pulse_df)

    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            cust_name = (
                row.get("customer_name")
                or row.get("BU_NAME")
                or row.get("CUSTOMER_NAME")
            )
        except Exception:  # noqa: BLE001
            cust_name = None
        cust_risk = _resolve_customer_risk(cust_name, risk_profiles)
        cust_pulse: Optional[float] = None
        if pulse_by_customer and cust_name:
            cust_pulse = pulse_by_customer.get(str(cust_name).strip().lower())
        result = compute_be_priority_score(
            row,
            customer_risk=cust_risk,
            customer_pulse=cust_pulse,
            weights=weights,
        )
        rows.append({
            "be_priority_score": result["score"],
            "be_top_signal": result["top_signal"],
            "be_severity_capped_norm": result["components"]["severity_capped_norm"],
            "be_customer_risk_norm": result["components"]["customer_risk_norm"],
            "be_pulse_neg_norm": result["components"]["pulse_neg_norm"],
            "be_age_norm": result["components"]["age_norm"],
            "be_content_norm": result["components"]["content_norm"],
            "be_escalation_norm": result["components"]["escalation_norm"],
            # Round 79 / B2: expose the raw customer-context inputs so
            # downstream consumers (XLSX BE_Priority_Barriers and the Word
            # narrative) can render the operator-facing values without
            # rejoining ``risk_profiles`` and ``pulse_df``. None when the
            # lookup did not find the customer.
            "be_customer_risk": cust_risk,
            "be_customer_pulse": cust_pulse,
        })
    enrich = pd.DataFrame(rows, index=df.index)
    return pd.concat([df, enrich], axis=1)


def _format_provenance_row(message: str) -> Dict[str, Any]:
    """Round 79 / B1: provenance-row factory matching the R67/B2 contract."""

    return {
        "_adoptiq_provenance_row": True,
        "AdoptIQ_Status": "EMPTY",
        "AdoptIQ_Source": "be_priority_scorer.compute_be_focus_areas",
        "AdoptIQ_Message": message,
    }


def compute_be_focus_areas(
    ab_scored: pd.DataFrame,
    *,
    max_per_tech: int = 10,
    min_cluster_score: float = 30.0,
) -> pd.DataFrame:
    """Round 79 / Phase 3 (B3): per-technology focus-area roll-up.

    Groups ABs by ``(sub_technology, ab_category_final)`` and ranks each
    cluster by the composite ``Cluster_Focus_Score``:

        cluster_focus_score = (
            sum(be_priority_score)        * 0.35
          + true_blocker_count            * 5.0
          + customers_affected            * 3.0
          + open_count                    * 1.0
        )

    Filters out clusters whose ``Cluster_Focus_Score`` is below
    ``min_cluster_score`` (default 30.0; an env-tunable
    ``BE_PRIORITY_MIN_CLUSTER_SCORE`` flips this), then caps each
    technology to its top ``max_per_tech`` clusters.

    Sort order (deterministic, R67/B1 SSoT determinism rule):
        1. Technology ASC
        2. Cluster_Focus_Score DESC
        3. Theme ASC

    Empty input or all-clusters-filtered -> single provenance row.
    """

    if ab_scored is None or ab_scored.empty:
        return pd.DataFrame([_format_provenance_row(
            "No adoption barriers in scope. BE focus areas cannot be "
            "computed when the AB universe is empty."
        )])

    df = ab_scored.copy()

    # Normalize required columns defensively so a malformed upstream
    # frame still produces a useful (or honest empty) rollup.
    if "sub_technology" not in df.columns:
        df["sub_technology"] = "Unknown"
    if "ab_category_final" not in df.columns:
        df["ab_category_final"] = "Unknown"
    if "be_priority_score" not in df.columns:
        df["be_priority_score"] = 0.0
    if "customer_name" not in df.columns:
        if "BU_NAME" in df.columns:
            df["customer_name"] = df["BU_NAME"]
        else:
            df["customer_name"] = "Unknown Customer"
    if "be_llm_class" not in df.columns:
        df["be_llm_class"] = "UNCLASSIFIED"

    df["sub_technology"] = (
        df["sub_technology"].fillna("Unknown").astype(str).str.strip()
    )
    df["ab_category_final"] = (
        df["ab_category_final"].fillna("Unknown").astype(str).str.strip()
    )
    df.loc[df["sub_technology"].isin(["", "nan", "None"]), "sub_technology"] = "Unknown"
    df.loc[
        df["ab_category_final"].isin(["", "nan", "None"]), "ab_category_final"
    ] = "Unknown"

    rows: List[Dict[str, Any]] = []
    for (tech, theme), group in df.groupby(
        ["sub_technology", "ab_category_final"], sort=True
    ):
        be_scores = pd.to_numeric(
            group["be_priority_score"], errors="coerce"
        ).fillna(0.0)
        sum_be = float(be_scores.sum())
        avg_be = float(be_scores.mean()) if len(be_scores) else 0.0
        try:
            true_blockers = int(
                (
                    group["be_llm_class"].astype(str).str.upper().str.strip()
                    == "TRUE_BLOCKER"
                ).sum()
            )
        except Exception:  # noqa: BLE001
            true_blockers = 0
        cust_set = sorted(
            {
                str(c).strip()
                for c in group["customer_name"].dropna()
                if str(c).strip()
            }
        )
        customers_affected = len(cust_set)
        open_count = int(len(group))
        cluster_focus_score = (
            sum_be * 0.35
            + true_blockers * 5.0
            + customers_affected * 3.0
            + open_count * 1.0
        )
        if cluster_focus_score < min_cluster_score:
            continue

        # Sample issues: top 3 ABs in the cluster by BE-priority score
        # (NOT by CSConsole severity).  Ties broken by ID ASC for
        # determinism.
        try:
            sort_cols = ["be_priority_score"]
            sort_asc: List[bool] = [False]
            if "ID" in group.columns:
                sort_cols.append("ID")
                sort_asc.append(True)
            sample_titles = (
                group.sort_values(sort_cols, ascending=sort_asc, kind="mergesort")[
                    "title"
                ]
                .head(3)
                .astype(str)
                .tolist()
            )
        except Exception:  # noqa: BLE001
            sample_titles = []
        sample_issues = " | ".join(
            t[:120] for t in sample_titles if t and t.lower() != "nan"
        )
        top_customers = ", ".join(cust_set[:5])

        rows.append({
            # Round 121 / G3: relabel bare sentinel at the display layer only.
            # The groupby keys (tech, theme) are untouched so row counts and
            # ordering are preserved.
            "Technology": relabel_unclassified(tech),
            "Theme": relabel_unclassified(theme),
            "Customers_Affected": customers_affected,
            "Open_Barriers": open_count,
            "Avg_BE_Priority": round(avg_be, 1),
            "True_Blocker_Count": true_blockers,
            "Top_Customers": top_customers,
            "Sample_Issues": sample_issues,
            "Cluster_Focus_Score": round(cluster_focus_score, 1),
        })

    if not rows:
        return pd.DataFrame([_format_provenance_row(
            f"All clusters fell below the min_cluster_score={min_cluster_score:.1f} "
            "threshold; no BE focus areas surfaced for this scope."
        )])

    out = pd.DataFrame(rows)
    out = out.sort_values(
        ["Technology", "Cluster_Focus_Score", "Theme"],
        ascending=[True, False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    # Cap to top max_per_tech per technology while preserving the
    # global Technology-ASC, Score-DESC, Theme-ASC order.
    capped: List[pd.DataFrame] = []
    for _tech, grp in out.groupby("Technology", sort=False):
        capped.append(grp.head(max_per_tech).copy())
    if capped:
        out = pd.concat(capped, ignore_index=True)
    out = out.sort_values(
        ["Technology", "Cluster_Focus_Score", "Theme"],
        ascending=[True, False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    out["Tech_Rank"] = out.groupby("Technology", sort=False).cumcount() + 1

    cols = [
        "Tech_Rank",
        "Technology",
        "Theme",
        "Customers_Affected",
        "Open_Barriers",
        "Avg_BE_Priority",
        "True_Blocker_Count",
        "Top_Customers",
        "Sample_Issues",
        "Cluster_Focus_Score",
    ]
    return out[cols]


__all__ = [
    "BePriorityWeights",
    "compute_be_priority_score",
    "compute_be_priority_scores_for_frame",
    "compute_be_focus_areas",
]
