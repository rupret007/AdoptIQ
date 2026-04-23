"""
Canonical metric calculators for AdoptIQ.

Single source of truth for every cross-report count. All report paths
(Leader, Compact, Executive Intelligence, Comprehensive, Admin dashboard)
MUST call these functions instead of reimplementing the math inline.

Design principles
-----------------
1. Pure functions: no mutation of inputs, no side effects, no I/O.
2. Where multiple legitimate definitions exist for the "same" label
   (e.g. BEMS counted on TAC only vs combined with Adoption Barriers),
   the function exposes an explicit ``mode`` parameter with a named
   default. Callers MUST opt into non-default modes; silent drift is
   not allowed.
3. Counts are computed on the normalized columns produced by
   ``data_normalization.add_case_lifecycle_fields`` whenever those
   fields exist on the supplied DataFrame; otherwise the function
   normalizes on the fly to keep behaviour identical to the canonical
   pipeline.
4. Every function returns a plain ``int``/``float``/``dict`` with
   explicit semantics. No mutable shared state.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from data_normalization import (
    add_case_lifecycle_fields,
    detect_bems_mask,
    extract_bems_ids_from_row,
    normalize_customer_name,
    normalize_priority_label,
    normalize_severity_label,
    normalize_status_label,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_empty(df: Optional[pd.DataFrame]) -> bool:
    return df is None or len(df) == 0


def _safe_len(df: Optional[pd.DataFrame]) -> int:
    return 0 if _is_empty(df) else int(len(df))


def _ensure_priority_norm(csone_df: Optional[pd.DataFrame]) -> pd.Series:
    """Return a ``case_priority_norm`` series for a TAC/CSOne frame.

    If the column already exists (because the caller invoked
    ``add_case_lifecycle_fields`` upstream) it is returned as-is.
    Otherwise, the canonical priority normalizer is applied to the first
    available raw priority/severity column. Missing columns yield
    ``"Unknown"`` for every row -- never raised exceptions, never raw
    ``Severity`` string matches.
    """

    if _is_empty(csone_df):
        return pd.Series(dtype=str)
    if "case_priority_norm" in csone_df.columns:
        return csone_df["case_priority_norm"].fillna("Unknown").astype(str)

    candidates = ("Severity", "SEVERITY", "SEVERITY_C", "Highest Priority", "Priority", "PRIORITY", "PRIORITY_C")
    sev_col = next((c for c in candidates if c in csone_df.columns), None)
    if sev_col is None:
        return pd.Series(["Unknown"] * len(csone_df), index=csone_df.index, dtype=str)
    return csone_df[sev_col].fillna("").astype(str).apply(normalize_priority_label)


def _ensure_severity_norm(ab_df: Optional[pd.DataFrame]) -> pd.Series:
    """Return a ``severity_norm`` series for an Adoption Barrier frame."""

    if _is_empty(ab_df):
        return pd.Series(dtype=str)
    if "severity_norm" in ab_df.columns:
        return ab_df["severity_norm"].fillna("Unknown").astype(str)

    candidates = ("SEVERITY_C", "severity_c", "Severity", "SEVERITY", "PRIORITY", "Priority", "PRIORITY_C")
    sev_col = next((c for c in candidates if c in ab_df.columns), None)
    if sev_col is None:
        return pd.Series(["Unknown"] * len(ab_df), index=ab_df.index, dtype=str)
    return ab_df[sev_col].fillna("").astype(str).apply(normalize_severity_label)


def _customer_names_from_frame(df: Optional[pd.DataFrame]) -> Iterable[str]:
    if _is_empty(df):
        return []
    cols_in_priority = (
        "customer_name",
        "customer_name_norm",
        "Customer Name",
        "BU_NAME",
        "CUSTOMER_NAME",
        "Customer",
        "ACCOUNT_NAME",
    )
    out: List[str] = []
    for col in cols_in_priority:
        if col in df.columns:
            out.extend(
                normalize_customer_name(v)
                for v in df[col].dropna().astype(str).tolist()
            )
            # Once a primary column has been used, keep iterating other
            # columns too so that a frame with only ``BU_NAME`` is still
            # picked up alongside one that has both ``customer_name`` and
            # ``BU_NAME``.
    return out


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def count_customers(
    *,
    ab_df: Optional[pd.DataFrame] = None,
    csone_df: Optional[pd.DataFrame] = None,
    subs_df: Optional[pd.DataFrame] = None,
    action_plans_df: Optional[pd.DataFrame] = None,
    pulse_df: Optional[pd.DataFrame] = None,
    extra_frames: Optional[Sequence[pd.DataFrame]] = None,
    extra_names: Optional[Sequence[str]] = None,
    drop_unknown: bool = True,
) -> int:
    """Count unique customers across every supplied source.

    The canonical universe is the union of normalized customer names
    found in any provided DataFrame. ``Unknown`` is excluded by default
    so that header tiles and per-customer sections agree.
    """

    seen = set()
    for frame in (ab_df, csone_df, subs_df, action_plans_df, pulse_df):
        for name in _customer_names_from_frame(frame):
            seen.add(name)
    for frame in extra_frames or ():
        for name in _customer_names_from_frame(frame):
            seen.add(name)
    for raw in extra_names or ():
        seen.add(normalize_customer_name(raw))
    if drop_unknown:
        seen.discard("Unknown")
        seen.discard("")
    return len(seen)


def list_customers(
    *,
    ab_df: Optional[pd.DataFrame] = None,
    csone_df: Optional[pd.DataFrame] = None,
    subs_df: Optional[pd.DataFrame] = None,
    action_plans_df: Optional[pd.DataFrame] = None,
    pulse_df: Optional[pd.DataFrame] = None,
    extra_frames: Optional[Sequence[pd.DataFrame]] = None,
    extra_names: Optional[Sequence[str]] = None,
    drop_unknown: bool = True,
) -> List[str]:
    """Return the sorted canonical customer universe as a list."""

    seen = set()
    for frame in (ab_df, csone_df, subs_df, action_plans_df, pulse_df):
        for name in _customer_names_from_frame(frame):
            seen.add(name)
    for frame in extra_frames or ():
        for name in _customer_names_from_frame(frame):
            seen.add(name)
    for raw in extra_names or ():
        seen.add(normalize_customer_name(raw))
    if drop_unknown:
        seen.discard("Unknown")
        seen.discard("")
    return sorted(seen)


# ---------------------------------------------------------------------------
# TAC / Support cases
# ---------------------------------------------------------------------------


def count_total_tac(csone_df: Optional[pd.DataFrame]) -> int:
    """Return total TAC/support case row count (post-normalization)."""

    return _safe_len(csone_df)


def count_p1(csone_df: Optional[pd.DataFrame]) -> int:
    """Critical (P1) case count from canonical normalized priority."""

    series = _ensure_priority_norm(csone_df)
    if series.empty:
        return 0
    return int((series == "P1").sum())


def count_p2(csone_df: Optional[pd.DataFrame]) -> int:
    """High (P2) case count from canonical normalized priority."""

    series = _ensure_priority_norm(csone_df)
    if series.empty:
        return 0
    return int((series == "P2").sum())


def count_p3(csone_df: Optional[pd.DataFrame]) -> int:
    """Medium (P3) case count from canonical normalized priority."""

    series = _ensure_priority_norm(csone_df)
    if series.empty:
        return 0
    return int((series == "P3").sum())


def count_p4(csone_df: Optional[pd.DataFrame]) -> int:
    """Low (P4) case count from canonical normalized priority."""

    series = _ensure_priority_norm(csone_df)
    if series.empty:
        return 0
    return int((series == "P4").sum())


def count_unknown_priority(csone_df: Optional[pd.DataFrame]) -> int:
    """TAC rows whose priority/severity could not be normalized."""

    series = _ensure_priority_norm(csone_df)
    if series.empty:
        return 0
    return int((series == "Unknown").sum())


def count_priority_breakdown(csone_df: Optional[pd.DataFrame]) -> Dict[str, int]:
    """Return all P1-P4 + Unknown counts in a single dict.

    Sum of values equals ``count_total_tac(csone_df)`` exactly, so any
    bucket chart should reconcile with the headline TAC total.
    """

    series = _ensure_priority_norm(csone_df)
    return {
        "P1": int((series == "P1").sum()),
        "P2": int((series == "P2").sum()),
        "P3": int((series == "P3").sum()),
        "P4": int((series == "P4").sum()),
        "Unknown": int((series == "Unknown").sum()),
    }


def count_escalated(csone_df: Optional[pd.DataFrame]) -> int:
    """P1+P2 escalated case count (single canonical definition)."""

    series = _ensure_priority_norm(csone_df)
    if series.empty:
        return 0
    return int(series.isin(["P1", "P2"]).sum())


def count_open_tac(csone_df: Optional[pd.DataFrame]) -> int:
    """Open TAC case count using normalized lifecycle fields."""

    if _is_empty(csone_df):
        return 0
    if "is_open" in csone_df.columns:
        return int(csone_df["is_open"].fillna(False).astype(bool).sum())
    enriched = add_case_lifecycle_fields(csone_df)
    if "is_open" not in enriched.columns:
        return 0
    return int(enriched["is_open"].fillna(False).astype(bool).sum())


def count_closed_tac(csone_df: Optional[pd.DataFrame]) -> int:
    """Closed TAC case count using normalized lifecycle fields."""

    if _is_empty(csone_df):
        return 0
    if "is_closed" in csone_df.columns:
        return int(csone_df["is_closed"].fillna(False).astype(bool).sum())
    enriched = add_case_lifecycle_fields(csone_df)
    if "is_closed" not in enriched.columns:
        return 0
    return int(enriched["is_closed"].fillna(False).astype(bool).sum())


def count_break_fix(csone_df: Optional[pd.DataFrame]) -> int:
    """Break/fix case count using canonical case-type classifier."""

    if _is_empty(csone_df):
        return 0
    if "case_type_class" in csone_df.columns:
        series = csone_df["case_type_class"].fillna("").astype(str)
    else:
        enriched = add_case_lifecycle_fields(csone_df)
        if "case_type_class" not in enriched.columns:
            return 0
        series = enriched["case_type_class"].fillna("").astype(str)
    return int((series == "break_fix_technical").sum())


def count_provisioning(csone_df: Optional[pd.DataFrame]) -> int:
    """Provisioning request count using canonical case-type classifier."""

    if _is_empty(csone_df):
        return 0
    if "case_type_class" in csone_df.columns:
        series = csone_df["case_type_class"].fillna("").astype(str)
    else:
        enriched = add_case_lifecycle_fields(csone_df)
        if "case_type_class" not in enriched.columns:
            return 0
        series = enriched["case_type_class"].fillna("").astype(str)
    return int((series == "provisioning_request").sum())


# ---------------------------------------------------------------------------
# BEMS
# ---------------------------------------------------------------------------


# Canonical BEMS modes. Callers MUST pass one of these by name; there
# is no "smart" default that varies based on which fields are present.
BEMS_MODE_CANONICAL = "canonical_tac_rows"
BEMS_MODE_COMBINED_AB_TAC = "combined_ab_tac_rows"
BEMS_MODE_UNIQUE_IDS = "unique_ids"

_BEMS_MODES = {BEMS_MODE_CANONICAL, BEMS_MODE_COMBINED_AB_TAC, BEMS_MODE_UNIQUE_IDS}


def count_bems(
    csone_df: Optional[pd.DataFrame],
    *,
    ab_df: Optional[pd.DataFrame] = None,
    mode: str = BEMS_MODE_CANONICAL,
) -> int:
    """Count BEMS escalations.

    Parameters
    ----------
    csone_df:
        TAC/CSOne dataframe (canonical BEMS source).
    ab_df:
        Adoption Barrier dataframe; only used when ``mode`` is
        ``"combined_ab_tac_rows"``.
    mode:
        - ``"canonical_tac_rows"`` (default): count rows in
          ``csone_df`` whose row matches the BEMS detection mask.
          This is the value the cross-report consistency contract
          enforces, the same value that appears on the executive
          dashboards, and the value that the admin tile shows.
        - ``"combined_ab_tac_rows"``: AB rows + TAC rows (the legacy
          Leader Report definition). Use only inside the Leader
          Report's explicit "BEMS escalations across activities"
          rollup.
        - ``"unique_ids"``: count of distinct BEMS reference IDs
          extracted from the row text (e.g. ``BEMS-12345``). Useful
          for narrative sentences such as
          *"this customer has 3 unique BEMS references"*.

    The return value is always an ``int``.
    """

    if mode not in _BEMS_MODES:
        raise ValueError(
            f"count_bems: unknown mode {mode!r}. "
            f"Allowed modes: {sorted(_BEMS_MODES)}"
        )

    if mode == BEMS_MODE_CANONICAL:
        if _is_empty(csone_df):
            return 0
        return int(detect_bems_mask(csone_df).sum())

    if mode == BEMS_MODE_COMBINED_AB_TAC:
        ab_count = 0
        tac_count = 0
        if not _is_empty(ab_df):
            ab_count = int(detect_bems_mask(ab_df).sum())
        if not _is_empty(csone_df):
            tac_count = int(detect_bems_mask(csone_df).sum())
        return ab_count + tac_count

    if mode == BEMS_MODE_UNIQUE_IDS:
        ids = set()
        for frame in (csone_df, ab_df):
            if _is_empty(frame):
                continue
            for _, row in frame.iterrows():
                for token in extract_bems_ids_from_row(row):
                    if token:
                        ids.add(token)
        return len(ids)

    return 0  # unreachable; mode validated above


def bems_rate(csone_df: Optional[pd.DataFrame]) -> float:
    """Return canonical BEMS rate (TAC BEMS rows / total TAC rows)."""

    total = count_total_tac(csone_df)
    if total <= 0:
        return 0.0
    return round(count_bems(csone_df) / total * 100.0, 2)


# ---------------------------------------------------------------------------
# Adoption Barriers
# ---------------------------------------------------------------------------


# Canonical Critical-AB modes. Like BEMS, callers must opt in by name.
CRITICAL_AB_MODE_CRITICAL_ONLY = "critical_only"
CRITICAL_AB_MODE_CRITICAL_OR_HIGH = "critical_or_high"

_CRITICAL_AB_MODES = {CRITICAL_AB_MODE_CRITICAL_ONLY, CRITICAL_AB_MODE_CRITICAL_OR_HIGH}


def count_total_barriers(ab_df: Optional[pd.DataFrame]) -> int:
    """Total Adoption Barrier row count (post-normalization)."""

    return _safe_len(ab_df)


def count_critical_barriers(
    ab_df: Optional[pd.DataFrame],
    *,
    mode: str = CRITICAL_AB_MODE_CRITICAL_OR_HIGH,
) -> int:
    """Count "critical" adoption barriers.

    Parameters
    ----------
    ab_df:
        Adoption Barrier dataframe.
    mode:
        - ``"critical_or_high"`` (default): canonical "critical/high"
          definition that matches the executive dashboard label
          *"Critical Adoption Barriers"*. Counts rows whose normalized
          severity is ``Critical`` or ``High`` (i.e. P1 or P2).
        - ``"critical_only"``: strict Critical-only definition for the
          legacy ``_generate_key_concerns`` text. Counts rows whose
          normalized severity is exactly ``Critical`` (P1).

    Severity normalization is performed via
    ``data_normalization.normalize_severity_label`` so that the count
    is invariant to the exact column name (``SEVERITY_C`` vs
    ``Severity`` vs ``PRIORITY``).
    """

    if mode not in _CRITICAL_AB_MODES:
        raise ValueError(
            f"count_critical_barriers: unknown mode {mode!r}. "
            f"Allowed modes: {sorted(_CRITICAL_AB_MODES)}"
        )

    series = _ensure_severity_norm(ab_df)
    if series.empty:
        return 0
    if mode == CRITICAL_AB_MODE_CRITICAL_ONLY:
        return int((series == "Critical").sum())
    return int(series.isin(["Critical", "High"]).sum())


def count_open_barriers(ab_df: Optional[pd.DataFrame]) -> int:
    """Open AB count using canonical status normalization."""

    if _is_empty(ab_df):
        return 0
    if "case_status_norm" in ab_df.columns:
        series = ab_df["case_status_norm"].fillna("").astype(str)
    elif "status_norm" in ab_df.columns:
        series = ab_df["status_norm"].fillna("").astype(str)
    else:
        candidates = ("AB_STATUS_C", "STATUS_C", "Status", "STATUS")
        col = next((c for c in candidates if c in ab_df.columns), None)
        if col is None:
            return 0
        series = ab_df[col].fillna("").astype(str).apply(normalize_status_label)
    return int((series == "Open").sum())


# ---------------------------------------------------------------------------
# Total Activities (Leader Report)
# ---------------------------------------------------------------------------


# Three legitimate "Total Activities" definitions. Each is named so
# that every call site is self-documenting and the linter can detect
# silent drift.
ACTIVITIES_MODE_LEADER_SUMMARY = "leader_summary"  # AP+AB+CP+BEMS (top of report)
ACTIVITIES_MODE_MEMBER_TABLE = "member_table"      # AP+AB+CP (per-member table)
ACTIVITIES_MODE_OVERALL_SUMMARY = "overall_summary"  # AP+AB+CP+TAC (overall recap)
ACTIVITIES_MODE_FULL = "full"                      # AP+AB+CP+TAC+BEMS (everything)

_ACTIVITY_MODES = {
    ACTIVITIES_MODE_LEADER_SUMMARY,
    ACTIVITIES_MODE_MEMBER_TABLE,
    ACTIVITIES_MODE_OVERALL_SUMMARY,
    ACTIVITIES_MODE_FULL,
}


def count_total_activities(
    *,
    action_plans_df: Optional[pd.DataFrame] = None,
    ab_df: Optional[pd.DataFrame] = None,
    customer_pulse_df: Optional[pd.DataFrame] = None,
    tac_df: Optional[pd.DataFrame] = None,
    bems_count: Optional[int] = None,
    mode: str = ACTIVITIES_MODE_FULL,
) -> int:
    """Count "Total Activities" using one of the named definitions.

    The Leader Report historically rendered three different totals
    under the same column header. Each call site must now name the
    mode it intends; any future reader can grep the codebase for
    a definition by name.

    Modes
    -----
    - ``leader_summary``: ``AP + AB + CP + BEMS`` (the Team Activity
      Summary table in the Leader Report).
    - ``member_table``:   ``AP + AB + CP`` (the per-member detail
      table, intentionally excludes TAC and BEMS).
    - ``overall_summary``: ``AP + AB + CP + TAC`` (the Overall
      Individual Summary at the end of the Leader Report).
    - ``full``: ``AP + AB + CP + TAC + BEMS`` (recommended canonical
      total when no historical compatibility constraint applies).

    ``bems_count`` is passed in by the caller because BEMS counting
    depends on whether the caller wants TAC-only or combined; the
    activity counter does not know which mode to pick. If ``None``
    and the mode requires BEMS, BEMS contribution is 0.
    """

    if mode not in _ACTIVITY_MODES:
        raise ValueError(
            f"count_total_activities: unknown mode {mode!r}. "
            f"Allowed modes: {sorted(_ACTIVITY_MODES)}"
        )

    ap = _safe_len(action_plans_df)
    ab = _safe_len(ab_df)
    cp = _safe_len(customer_pulse_df)
    tac = _safe_len(tac_df)
    bems = int(bems_count or 0)

    if mode == ACTIVITIES_MODE_LEADER_SUMMARY:
        return ap + ab + cp + bems
    if mode == ACTIVITIES_MODE_MEMBER_TABLE:
        return ap + ab + cp
    if mode == ACTIVITIES_MODE_OVERALL_SUMMARY:
        return ap + ab + cp + tac
    return ap + ab + cp + tac + bems


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


# Canonical risk scales. The 0-100 banded model in ``risk_scoring.py``
# is the authoritative model; the legacy 0-10 model is preserved only
# for documents that explicitly request it.
RISK_SCALE_0_TO_100 = "0_to_100"
RISK_SCALE_0_TO_10 = "0_to_10"

_RISK_SCALES = {RISK_SCALE_0_TO_100, RISK_SCALE_0_TO_10}


def compute_high_risk_count(
    risk_profiles: Optional[Dict[str, Dict[str, Any]]],
    *,
    scale: str = RISK_SCALE_0_TO_100,
    high_threshold_0_to_10: float = 6.0,
) -> int:
    """Count high-risk customers in a portfolio.

    Parameters
    ----------
    risk_profiles:
        Mapping of customer_name -> profile dict produced by
        ``risk_scoring.compute_customer_risk_profile`` (or any dict
        with ``risk_score_0_100``, ``risk_score_0_10``, or ``score``
        / ``color`` legacy keys).
    scale:
        - ``"0_to_100"`` (default): canonical. High-risk = bands
          ``CRITICAL`` + ``HIGH`` (i.e. score >= 55).
        - ``"0_to_10"``: legacy. High-risk = ``risk_score_0_10`` >=
          ``high_threshold_0_to_10`` (default 6.0).
    """

    if scale not in _RISK_SCALES:
        raise ValueError(
            f"compute_high_risk_count: unknown scale {scale!r}. "
            f"Allowed scales: {sorted(_RISK_SCALES)}"
        )

    if not risk_profiles:
        return 0

    if scale == RISK_SCALE_0_TO_100:
        count = 0
        for profile in risk_profiles.values():
            band = str(profile.get("risk_band", "")).upper().strip()
            if band in {"CRITICAL", "HIGH"}:
                count += 1
                continue
            score = profile.get("risk_score_0_100")
            if score is None:
                # Legacy 0-10 scaled rescue: convert a 0-10 score back
                # to 0-100 so we can apply the canonical band cut.
                score10 = profile.get("risk_score_0_10")
                if score10 is None:
                    continue
                try:
                    score = float(score10) * 10.0
                except (TypeError, ValueError):
                    continue
            try:
                if float(score) >= 55.0:
                    count += 1
            except (TypeError, ValueError):
                continue
        return count

    # 0-10 legacy scale
    count = 0
    threshold = float(high_threshold_0_to_10)
    for profile in risk_profiles.values():
        # Prefer explicit color flag if present (legacy compact path).
        color = str(profile.get("color", "")).strip().lower()
        if color in {"red"}:
            count += 1
            continue
        score = profile.get("risk_score_0_10", profile.get("score"))
        if score is None and "risk_score_0_100" in profile:
            try:
                score = float(profile["risk_score_0_100"]) / 10.0
            except (TypeError, ValueError):
                score = None
        try:
            if score is not None and float(score) >= threshold:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


# ---------------------------------------------------------------------------
# Customer Pulse sentiment
# ---------------------------------------------------------------------------


# Pulse sentiment thresholds operate on a 0-10 numeric scale. The
# legacy ``_derive_sentiment_summary`` used 7.5/5.0; the narrative
# elsewhere referred to a ``/5.0`` scale. We canonicalize to 0-10 and
# document conversion explicitly.
PULSE_POSITIVE_THRESHOLD_0_TO_10 = 7.5
PULSE_NEGATIVE_THRESHOLD_0_TO_10 = 5.0

PULSE_SCALE_0_TO_10 = "0_to_10"
PULSE_SCALE_0_TO_5 = "0_to_5"

_PULSE_SCALES = {PULSE_SCALE_0_TO_10, PULSE_SCALE_0_TO_5}


def _coerce_pulse_score_series(
    pulse_df: Optional[pd.DataFrame],
    scale: str,
) -> pd.Series:
    """Return a 0-10 normalized pulse score series."""

    if _is_empty(pulse_df):
        return pd.Series(dtype=float)
    candidates = ("SCORE__C", "SCORE", "PULSE_SCORE", "Rating", "RATING")
    col = next((c for c in candidates if c in pulse_df.columns), None)
    if col is None:
        return pd.Series(dtype=float)
    series = pd.to_numeric(pulse_df[col], errors="coerce")
    if scale == PULSE_SCALE_0_TO_5:
        series = series * 2.0
    return series.dropna()


def pulse_sentiment(
    pulse_df: Optional[pd.DataFrame],
    *,
    scale: str = PULSE_SCALE_0_TO_10,
) -> Dict[str, Any]:
    """Compute canonical pulse sentiment summary.

    Returns a dict with ``count``, ``mean_0_to_10``, ``positive``,
    ``neutral``, ``negative``, and ``sentiment`` (string) fields.
    The ``sentiment`` label is determined from the mean using the
    canonical thresholds (Positive >= 7.5, Negative <= 5.0).
    """

    if scale not in _PULSE_SCALES:
        raise ValueError(
            f"pulse_sentiment: unknown scale {scale!r}. "
            f"Allowed scales: {sorted(_PULSE_SCALES)}"
        )

    series = _coerce_pulse_score_series(pulse_df, scale)
    if series.empty:
        return {
            "count": 0,
            "mean_0_to_10": None,
            "positive": 0,
            "neutral": 0,
            "negative": 0,
            "sentiment": "Unknown",
        }
    mean_val = float(series.mean())
    positive = int((series >= PULSE_POSITIVE_THRESHOLD_0_TO_10).sum())
    negative = int((series <= PULSE_NEGATIVE_THRESHOLD_0_TO_10).sum())
    neutral = int(len(series) - positive - negative)

    if mean_val >= PULSE_POSITIVE_THRESHOLD_0_TO_10:
        label = "Positive"
    elif mean_val <= PULSE_NEGATIVE_THRESHOLD_0_TO_10:
        label = "Negative"
    else:
        label = "Neutral"

    return {
        "count": int(len(series)),
        "mean_0_to_10": round(mean_val, 2),
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
        "sentiment": label,
    }


# ---------------------------------------------------------------------------
# Portfolio metrics convenience builder
# ---------------------------------------------------------------------------


def build_portfolio_metrics(
    *,
    ab_df: Optional[pd.DataFrame],
    csone_df: Optional[pd.DataFrame],
    risk_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    extra_customer_frames: Optional[Sequence[pd.DataFrame]] = None,
    extra_customer_names: Optional[Sequence[str]] = None,
    risk_scale: str = RISK_SCALE_0_TO_100,
    defects: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical ``PortfolioMetricsContract`` payload.

    All values are computed via the canonical helpers above so that the
    payload always matches the values rendered into the document. This
    payload is the one to pass to
    ``report_consistency.validate_report_consistency`` to enforce
    parity from any report path.
    """

    priority_buckets = count_priority_breakdown(csone_df)
    bems = count_bems(csone_df)
    payload: Dict[str, Any] = {
        "total_customers": count_customers(
            ab_df=ab_df,
            csone_df=csone_df,
            extra_frames=extra_customer_frames,
            extra_names=extra_customer_names,
        ),
        "total_barriers": count_total_barriers(ab_df),
        "total_cases": count_total_tac(csone_df),
        "bems_count": bems,
        "critical_p1": priority_buckets["P1"],
        "high_p2": priority_buckets["P2"],
        "p1_cases": priority_buckets["P1"],
        "p2_cases": priority_buckets["P2"],
        "p3_cases": priority_buckets["P3"],
        "p4_cases": priority_buckets["P4"],
        "unknown_priority_cases": priority_buckets["Unknown"],
        "break_fix_cases": count_break_fix(csone_df),
        "provisioning_cases": count_provisioning(csone_df),
    }

    if defects:
        payload["defects_count"] = int(defects.get("total_defects", 0) or 0)
        payload["security_advisories_count"] = int(
            defects.get("security_advisories_count", 0) or 0
        )

    if risk_profiles is not None:
        payload["high_risk_customers"] = compute_high_risk_count(
            risk_profiles, scale=risk_scale
        )
        # Medium / Low / Healthy band counts when 0-100 model used.
        if risk_scale == RISK_SCALE_0_TO_100:
            band_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "HEALTHY": 0}
            for profile in risk_profiles.values():
                band = str(profile.get("risk_band", "")).upper().strip()
                if band in band_counts:
                    band_counts[band] += 1
            payload["medium_risk_customers"] = band_counts["MEDIUM"]
            payload["low_risk_customers"] = band_counts["LOW"]
            payload["healthy_customers"] = band_counts["HEALTHY"]

    return payload


__all__ = [
    "BEMS_MODE_CANONICAL",
    "BEMS_MODE_COMBINED_AB_TAC",
    "BEMS_MODE_UNIQUE_IDS",
    "CRITICAL_AB_MODE_CRITICAL_ONLY",
    "CRITICAL_AB_MODE_CRITICAL_OR_HIGH",
    "ACTIVITIES_MODE_LEADER_SUMMARY",
    "ACTIVITIES_MODE_MEMBER_TABLE",
    "ACTIVITIES_MODE_OVERALL_SUMMARY",
    "ACTIVITIES_MODE_FULL",
    "RISK_SCALE_0_TO_100",
    "RISK_SCALE_0_TO_10",
    "PULSE_SCALE_0_TO_10",
    "PULSE_SCALE_0_TO_5",
    "PULSE_POSITIVE_THRESHOLD_0_TO_10",
    "PULSE_NEGATIVE_THRESHOLD_0_TO_10",
    "bems_rate",
    "build_portfolio_metrics",
    "compute_high_risk_count",
    "count_bems",
    "count_break_fix",
    "count_closed_tac",
    "count_critical_barriers",
    "count_customers",
    "count_escalated",
    "count_open_barriers",
    "count_open_tac",
    "count_p1",
    "count_p2",
    "count_p3",
    "count_p4",
    "count_priority_breakdown",
    "count_provisioning",
    "count_total_activities",
    "count_total_barriers",
    "count_total_tac",
    "count_unknown_priority",
    "list_customers",
    "pulse_sentiment",
]
