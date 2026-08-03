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

Round 5 / Phase 6.18 -- on data freshness
-----------------------------------------
Functions in this module DO NOT inject a "data was retrieved at ..."
or "as-of" timestamp into their return values.  Freshness is a
property of the *call site* (the worker that fetched the underlying
DataFrames), not of the canonical math layer.  Callers must therefore:

- Capture ``data_retrieved_at`` (UTC, tz-aware) immediately *after*
  the upstream Snowflake / CSOne fetches complete (see
  ``snowflake_prefetch.AnalysisRunContext`` and
  ``leader_report_generator``).
- Pass that timestamp into the report formatter / Word writer / Ask
  AI grounded prompt so the headline ("Generated: ... UTC") and the
  "Source data retrieved at ..." disclaimer agree.
- NEVER call ``datetime.now()`` inside a canonical metric function to
  derive freshness -- by the time the metric runs the data could
  already be many minutes old.

Headline metrics (e.g. ``build_portfolio_metrics``,
``compute_high_risk_count``) therefore return *only* the math; the
report writer is responsible for stamping the "as-of" line above
the table.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Round 71 / Phase 4 (#20): module-local logger for provenance / diagnostic
# warnings emitted from canonical metric helpers.  Pure functions never
# do I/O, but we DO log at WARN when a defensive fallback is taken so the
# operator can correlate the report's surfaced count with WHY it landed
# on a non-obvious value.
logger = logging.getLogger(__name__)

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
# Round 11 / Phase 5.1 -- shared risk-band color constant
# ---------------------------------------------------------------------------
# Single source of truth for the matplotlib/Word/HTML risk-band
# palette so every report path renders the same color for the same
# canonical band.  Keys are the canonical band labels emitted by
# the risk classifier.  Use ``RISK_BAND_COLORS.get(band, RISK_BAND_COLOR_DEFAULT)``
# to pick a color so an unexpected label never crashes the chart.
RISK_BAND_COLORS: Dict[str, str] = {
    "CRITICAL": "#d62728",
    "HIGH": "#ff7f0e",
    "MEDIUM": "#ffd700",
    "LOW": "#2ca02c",
    "HEALTHY": "#28B463",
    "UNKNOWN": "#7f7f7f",
}
RISK_BAND_COLOR_DEFAULT: str = "#1f77b4"

# Round 11 / Phase 5.2 -- Word/matplotlib portfolio palette.
# Charts in ``adoptiq_backend.append_to_word_report`` and Word
# cell shading were both hard-coded with these hexes.  Keep both
# call sites pointing at this dict so future tweaks land in
# exactly one place.
RISK_BAND_PORTFOLIO_COLORS: Dict[str, str] = {
    "Critical Risk": "#C0392B",
    "High Risk": "#FF6B6B",
    "Medium Risk": "#FFB81C",
    "Low Risk": "#5DBCD2",
    "Healthy": "#28B463",
    "Critical + High": "#FF6B6B",
}


# ---------------------------------------------------------------------------
# Round 13 / Phase 5.7 -- Portfolio Health-grade canonical color map
# ---------------------------------------------------------------------------
# ``adoptiq_backend.create_subscription_summary_chart`` previously
# rendered the "Grade: A/B/C/D/F" text in
# ``#28B463`` / ``#FFB81C`` / ``#FF6B6B`` inline -- duplicating the
# ``RISK_BAND_PORTFOLIO_COLORS`` "Healthy" / "Medium Risk" / "High Risk"
# hexes but with no shared definition.  Future palette tweaks therefore
# would have to chase every inline ternary instead of landing in one
# place.  Centralize the A-F to-hex mapping so the chart, any future
# Excel grade cell, and dashboards all resolve the same colours.
HEALTH_GRADE_COLORS: Dict[str, str] = {
    "A": RISK_BAND_PORTFOLIO_COLORS.get("Healthy", "#28B463"),
    "B": RISK_BAND_PORTFOLIO_COLORS.get("Healthy", "#28B463"),
    "C": RISK_BAND_PORTFOLIO_COLORS.get("Medium Risk", "#FFB81C"),
    "D": RISK_BAND_PORTFOLIO_COLORS.get("High Risk", "#FF6B6B"),
    "F": RISK_BAND_PORTFOLIO_COLORS.get("High Risk", "#FF6B6B"),
}
HEALTH_GRADE_COLOR_DEFAULT: str = RISK_BAND_PORTFOLIO_COLORS.get("Medium Risk", "#FFB81C")


def get_health_grade_color(grade: str) -> str:
    """Round 13 / Phase 5.7: resolve a portfolio health grade to its
    canonical hex.

    Accepts ``"A"`` through ``"F"`` (case-insensitive); falls back to
    ``HEALTH_GRADE_COLOR_DEFAULT`` for unknown labels so an unexpected
    grade label never crashes the chart.
    """

    try:
        key = (grade or "").strip().upper()
    except Exception:
        return HEALTH_GRADE_COLOR_DEFAULT
    return HEALTH_GRADE_COLORS.get(key, HEALTH_GRADE_COLOR_DEFAULT)


# ---------------------------------------------------------------------------
# Round 12 / Phase 11.7 -- fold_fuzzy degradation counter
# ---------------------------------------------------------------------------
# When ``_collect_customer_names(..., fold_fuzzy=True)`` cannot import
# ``data_normalization._clean_name_for_key`` we *silently* fall back to
# the un-folded set, which can re-introduce ``Cisco Systems`` /
# ``Cisco Systems, Inc.`` double-counts.  Round 7 / Phase 3.6 added a
# logger.warning, but logger output is easy to swallow in subprocess
# / packaged builds.  This module-level counter is bumped on every
# degradation so callers (and the verbose debug API) can detect the
# regression deterministically.
_R12_FOLD_FUZZY_DEGRADATIONS: int = 0


def get_fold_fuzzy_degradation_count() -> int:
    """Return how many times ``fold_fuzzy`` silently degraded.

    Round 12 / Phase 11.7: callers should treat any non-zero return as
    a partial-data signal and either re-emit the underlying warning or
    surface it via ``partial_data_warnings`` so analysts can see that
    customer-name folding was disabled for at least one metric in
    the current process.
    """

    return int(_R12_FOLD_FUZZY_DEGRADATIONS)


def reset_fold_fuzzy_degradation_count() -> None:
    """Reset the Round 12 / Phase 11.7 fold_fuzzy degradation counter.

    Intended for tests so each scenario starts from zero.
    """

    global _R12_FOLD_FUZZY_DEGRADATIONS
    _R12_FOLD_FUZZY_DEGRADATIONS = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_empty(df: Optional[pd.DataFrame]) -> bool:
    return df is None or len(df) == 0


def _safe_len(df: Optional[pd.DataFrame]) -> int:
    return 0 if _is_empty(df) else int(len(df))


def _collapsed_tac_df(csone_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Round 139: collapse fan-out TAC rows before canonical counting."""
    if _is_empty(csone_df):
        return csone_df
    try:
        from data_normalization import collapse_tac_cases, drop_provenance_rows

        # Round 140: provenance-only sheets must not inflate TAC KPIs.
        stripped = drop_provenance_rows(csone_df)
        if _is_empty(stripped):
            return stripped
        return collapse_tac_cases(stripped)
    except Exception:
        return csone_df


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
    csone_df = _collapsed_tac_df(csone_df)
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
        # Round 3: CSConsole pulse / success-priority frames carry the
        # customer name in ``RELATED_CUSTOMER__C`` (and sometimes a
        # ``CUSTOMER_BU_NAME__C``); include them so subscription-only
        # / pulse-only customers are part of the canonical universe.
        "RELATED_CUSTOMER__C",
        "CUSTOMER_BU_NAME__C",
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


_ACCOUNT_ID_COLS = (
    "ACCOUNT_ID_C",
    "ACCOUNT_ID",
    "ACCOUNT__C",
    "ACCOUNT__C_ID",
)


def _account_ids_from_frame(df: Optional[pd.DataFrame]) -> Iterable[str]:
    if _is_empty(df):
        return []
    out: List[str] = []
    for col in _ACCOUNT_ID_COLS:
        if col in df.columns:
            out.extend(
                str(v).strip()
                for v in df[col].dropna().tolist()
                if str(v).strip()
            )
    return out


def _collect_customer_names(
    frames: Sequence[Optional[pd.DataFrame]],
    extra_frames: Optional[Sequence[pd.DataFrame]],
    extra_names: Optional[Sequence[str]],
    account_to_customer: Optional[Dict[str, str]],
    drop_unknown: bool,
    fold_fuzzy: bool = False,
) -> set:
    """Internal: union of normalized customer names across all sources.

    Adds account-id backfill via ``account_to_customer`` so any frame
    that lacks a customer-name column but does carry an ``ACCOUNT_ID``-
    style column (TAC support cases, success priorities, etc.) still
    contributes its accounts when a subscription mapping is provided.

    Round 2 / Phase 4.6: ``fold_fuzzy`` controls whether names are
    folded by ``data_normalization._clean_name_for_key`` (e.g. drop
    ``Inc``/``LLC``/punctuation) so ``"Cisco Systems"`` and
    ``"Cisco Systems, Inc."`` count as one customer.  The default is
    **False** because every Round-1 phase 1 / phase 5 metric used the
    literal-string-unique policy; ``fold_fuzzy=True`` is opt-in for
    callers that want the looser legal-suffix-tolerant count.
    """
    seen: set = set()
    all_frames: List[Optional[pd.DataFrame]] = list(frames)
    if extra_frames:
        all_frames.extend(extra_frames)
    for frame in all_frames:
        for name in _customer_names_from_frame(frame):
            seen.add(name)
        if account_to_customer:
            for acct in _account_ids_from_frame(frame):
                mapped = account_to_customer.get(acct)
                if mapped:
                    seen.add(normalize_customer_name(mapped))
    for raw in extra_names or ():
        seen.add(normalize_customer_name(raw))
    if drop_unknown:
        seen.discard("Unknown")
        seen.discard("")
    if fold_fuzzy:
        # Fold names through the same key cleaner used by
        # ``data_normalization.resolve_customer_name`` so callers that
        # opt in get the same looser equality the lookup table uses.
        try:
            from data_normalization import _clean_name_for_key as _ckey
        except Exception as _ckey_err:
            # Round 7 / Phase 3.6: previously this returned the
            # un-folded ``seen`` set silently, which could double-count
            # ``"Cisco Systems"`` and ``"Cisco Systems, Inc."`` whenever
            # ``data_normalization`` failed to import.  Log a warning
            # and stash the error on the returned set so the caller's
            # ``partial_data_warnings`` block can surface the loss of
            # fuzzy folding.
            try:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "Round 7 / Phase 3.6: data_normalization._clean_name_for_key "
                    "unavailable (%s); fold_fuzzy disabled for this call.",
                    _ckey_err,
                )
            except Exception:
                pass  # noqa: PIE790
            # Round 12 / Phase 11.7: previously the only signal that
            # fold_fuzzy had silently degraded was a Round 7 / Phase
            # 3.6 logger.warning emission.  When the logger itself was
            # misconfigured (or the warning was swallowed by a noisy
            # parent), callers had *no* programmatic signal that fuzzy
            # folding had been requested but skipped, and downstream
            # ``Cisco Systems`` / ``Cisco Systems, Inc.`` double-counts
            # silently re-appeared.  Bump a process-wide counter so
            # callers (and ``partial_data_warnings``) can introspect.
            try:
                global _R12_FOLD_FUZZY_DEGRADATIONS
                _R12_FOLD_FUZZY_DEGRADATIONS += 1
            except Exception:
                # Round 12 / Phase 11.7: never let the side-channel
                # crash a real call -- the counter is purely
                # diagnostic.
                pass
            return _r132_collapse_alias_customer_names(seen, all_frames)
        folded: Dict[str, str] = {}
        for raw in seen:
            key = _ckey(raw)
            if not key:
                # No clean key (empty / Unknown): keep the original
                # so we don't accidentally drop entries.
                folded.setdefault(raw, raw)
                continue
            # Prefer the longer original spelling for the chosen
            # canonical name (more specific) when collisions occur.
            existing = folded.get(key)
            if existing is None or len(raw) > len(existing):
                folded[key] = raw
        return _r132_collapse_alias_customer_names(set(folded.values()), all_frames)
    return _r132_collapse_alias_customer_names(seen, all_frames)


def _r132_collapse_alias_customer_names(seen: set, all_frames: Sequence[Optional[pd.DataFrame]]) -> set:
    """Round 132 / Build 102: merge configured alias variants in the universe."""
    subs_for_alias = None
    for frame in all_frames:
        if frame is not None and not frame.empty and "BU_NAME" in frame.columns:
            subs_for_alias = frame
            break
    try:
        from data_normalization import collapse_customer_name_set as _collapse_aliases

        return _collapse_aliases(seen, team_subs_df=subs_for_alias)
    except Exception:
        return seen


def count_customers(
    *,
    ab_df: Optional[pd.DataFrame] = None,
    csone_df: Optional[pd.DataFrame] = None,
    subs_df: Optional[pd.DataFrame] = None,
    action_plans_df: Optional[pd.DataFrame] = None,
    pulse_df: Optional[pd.DataFrame] = None,
    extra_frames: Optional[Sequence[pd.DataFrame]] = None,
    extra_names: Optional[Sequence[str]] = None,
    account_to_customer: Optional[Dict[str, str]] = None,
    drop_unknown: bool = True,
    fold_fuzzy: bool = False,
) -> int:
    """Count unique customers across every supplied source.

    The canonical universe is the union of normalized customer names
    found in any provided DataFrame. ``Unknown`` is excluded by default
    so that header tiles and per-customer sections agree.

    Round 3 hardening: ``account_to_customer`` enables
    ACCOUNT_ID → BU_NAME backfill so frames that only carry an account
    identifier (renewal/contract/Snowflake exports) still contribute to
    the headline count, matching what the dashboard's
    ``_get_all_customers_from_all_sources`` does.

    Round 2 / Phase 4.6: ``fold_fuzzy`` (default ``False``) lets
    callers opt in to legal-suffix-/punctuation-tolerant equality
    via :func:`data_normalization._clean_name_for_key`.  All Round-1
    fixes assumed literal-string-unique counts, so the default keeps
    that behaviour while documenting the policy.
    """

    return len(
        _collect_customer_names(
            (ab_df, csone_df, subs_df, action_plans_df, pulse_df),
            extra_frames,
            extra_names,
            account_to_customer,
            drop_unknown,
            fold_fuzzy=fold_fuzzy,
        )
    )


def list_customers(
    *,
    ab_df: Optional[pd.DataFrame] = None,
    csone_df: Optional[pd.DataFrame] = None,
    subs_df: Optional[pd.DataFrame] = None,
    action_plans_df: Optional[pd.DataFrame] = None,
    pulse_df: Optional[pd.DataFrame] = None,
    extra_frames: Optional[Sequence[pd.DataFrame]] = None,
    extra_names: Optional[Sequence[str]] = None,
    account_to_customer: Optional[Dict[str, str]] = None,
    drop_unknown: bool = True,
    fold_fuzzy: bool = False,
) -> List[str]:
    """Return the sorted canonical customer universe as a list."""

    return sorted(
        _collect_customer_names(
            (ab_df, csone_df, subs_df, action_plans_df, pulse_df),
            extra_frames,
            extra_names,
            account_to_customer,
            drop_unknown,
            fold_fuzzy=fold_fuzzy,
        )
    )


# ---------------------------------------------------------------------------
# TAC / Support cases
# ---------------------------------------------------------------------------


def count_total_tac(csone_df: Optional[pd.DataFrame]) -> int:
    """Return total distinct TAC/support cases (post-collapse)."""

    return _safe_len(_collapsed_tac_df(csone_df))


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
        collapsed = _collapsed_tac_df(csone_df)
        return int(detect_bems_mask(collapsed).sum())

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
    """Total Adoption Barrier count (post-normalization).

    Round 25 / Phase F: count *distinct* barrier IDs rather than rows.

    Pre-Round 25 this returned ``_safe_len(ab_df)`` -- one count per
    row.  But the Snowflake AB extract fans out to one row *per
    assignee* (or per status-history change), so a single barrier with
    three assignees inflated the headline by 3x.  The reference Brian
    Frazier / All Contact Center / 90d report had 71 rows but only 67
    distinct ``ID`` values; the dashboard tile read ``Active Barriers:
    71`` while a recipient who actually pivoted the Excel export found
    67.  Fix by using ``nunique`` on the ``ID`` column when present.

    Behaviour-preserving fallback: if the frame has no ``ID`` column
    (older fixture data, an unnamed dataframe, etc.) we fall back to
    the row count so callers do not regress to zero in those edge
    cases.  ``None``/empty inputs still yield 0.
    """

    if ab_df is None or len(ab_df) == 0:
        return 0
    try:
        cols = getattr(ab_df, "columns", [])
    except Exception:
        cols = []
    if "ID" in cols:
        try:
            distinct = int(ab_df["ID"].dropna().nunique())
        except Exception:
            # Defensive: an unhashable ID column should not degrade the
            # report.  Fall through to the rowcount path.
            distinct = 0
        # Round 53.3: when the ``ID`` column exists but every value is
        # null/blank (broken extract, CSV without IDs, mocked frame),
        # ``nunique()`` returns 0 even for a non-empty frame and would
        # collapse the dashboard tile to 0. Fall through to the
        # rowcount path so the headline does not lie about there being
        # zero barriers when the data clearly has rows.
        if distinct > 0:
            return distinct
    return _safe_len(ab_df)


def _count_distinct_by_id(df: Optional[pd.DataFrame]) -> int:
    """Round 124: distinct-``ID`` count with a behaviour-preserving rowcount fallback.

    Mirrors ``count_total_barriers`` exactly: counts distinct non-null ``ID``
    values when the column is present and non-empty, otherwise falls back to the
    raw row count (older fixtures / unnamed frames / all-null ID columns) so the
    caller never regresses to a misleading zero.
    """

    if df is None or len(df) == 0:
        return 0
    try:
        cols = getattr(df, "columns", [])
    except Exception:  # noqa: BLE001
        cols = []
    if "ID" in cols:
        try:
            tokens = df["ID"].fillna("").astype(str).str.strip()
            missing = tokens.eq("") | tokens.str.casefold().isin({"nan", "none", "null"})
            distinct = int(tokens.loc[~missing].nunique()) + int(missing.sum())
        except Exception:  # noqa: BLE001
            distinct = 0
        if distinct > 0:
            return distinct
    return _safe_len(df)


def count_total_action_plans(ap_df: Optional[pd.DataFrame]) -> int:
    """Count Action Plans by row-coalesced stable ID, retaining missing-ID rows.

    The Leader report's per-CSSM action-plan frames each carry the same Snowflake
    ``ID`` when a plan's account is shared across CSSMs (the R72
    ``_ATTRIBUTED_BY_ACCOUNT`` pathway), so a raw ``sum(len(...))`` across CSSMs
    over-counts the team headline (Build 92: Brian 655 vs the 553-row deduped
    XLSX sheet). Counting distinct ``ID`` makes the Key Insights bullet agree
    with the workbook -- parity with the R78/B2 sheet dedup.
    """

    if _is_empty(ap_df):
        return 0
    record_ids, _ = _coalesce_row_values(
        ap_df,
        ACTION_PLAN_ID_COLUMNS,
        stringify=True,
    )
    tokens = record_ids.fillna("").astype(str).str.strip()
    with_id = int(tokens.loc[tokens.ne("")].nunique())
    without_id = int(tokens.eq("").sum())
    return with_id + without_id


def count_total_customer_pulse(cp_df: Optional[pd.DataFrame]) -> int:
    """Round 124: total Customer Pulse record count as distinct ``ID``.

    Same shared-account fan-out as ``count_total_action_plans`` (Build 92:
    Brian 44 vs the 39-row deduped XLSX sheet). Parity with the R108 sheet dedup.
    """

    return _count_distinct_by_id(cp_df)


def _count_barrier_records(
    ab_df: Optional[pd.DataFrame],
    mask: Optional[pd.Series] = None,
) -> int:
    """Round 53: count filtered adoption barriers as records, not fan-out rows."""

    if _is_empty(ab_df):
        return 0
    frame = ab_df
    if mask is not None:
        try:
            aligned = mask.reindex(ab_df.index, fill_value=False).astype(bool)
            frame = ab_df.loc[aligned]
        except Exception:
            frame = ab_df.iloc[0:0]
    if _is_empty(frame):
        return 0
    try:
        if "ID" in getattr(frame, "columns", []):
            distinct = int(frame["ID"].dropna().nunique())
            # Round 53.3: mirror ``count_total_barriers`` -- if every
            # ID is null we cannot dedupe, so fall back to the filtered
            # row count instead of returning 0 for a non-empty frame.
            if distinct > 0:
                return distinct
    except Exception:
        pass
    return _safe_len(frame)


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
        # Round 53: dedupe by barrier ID after filtering; Snowflake AB extracts
        # can fan out one logical barrier into multiple assignee/detail rows.
        return _count_barrier_records(ab_df, series == "Critical")
    # Round 53: same record semantics for the dashboard's Critical/High band.
    return _count_barrier_records(ab_df, series.isin(["Critical", "High"]))


def _select_first_populated_status_column(
    ab_df: pd.DataFrame, candidates: Tuple[str, ...]
) -> Optional[str]:
    """Round 72 / Build 46 (Finding 2): pick the first candidate column
    that actually has at least one non-empty value.

    Pre-R72 the ``count_open_barriers`` / ``count_closed_barriers``
    helpers picked ``AB_STATUS_C`` first if it was present in the
    DataFrame at all, even when it was 100% NaN -- which is exactly
    the shape produced by the leader-report Snowflake AB extract
    (``AB_STATUS_C`` joins from a sister table that historically
    isn't populated, while the canonical ``STATUS_C`` carries the
    real "Open" / "Resolved" / "Cancelled" labels).  The pre-R72
    naive ``next(c for c in candidates if c in ab_df.columns)`` pick
    routed every per-CSSM ``count_open_barriers`` call into a
    column whose ``normalize_status_label`` output was always
    ``"Unknown"`` -- so the leader DOCX wrote "Open Adoption
    Barriers: 0" while the leader XLSX ``Adoption_Barriers`` sheet
    correctly read 63 distinct Open barriers via the canonical
    rename ``STATUS_C -> Status``.

    The fix: enumerate the candidate columns in the documented
    priority order, and skip any column that has no non-empty
    values.  Falling through to the next candidate keeps the
    helper functions usable in pipelines that rely on the
    secondary fallback (e.g. CSV imports that only carry
    ``AB_STATUS_C``) without regressing the leader path.
    Returns ``None`` if none of the candidates carry any data.
    """

    for col in candidates:
        if col not in ab_df.columns:
            continue
        try:
            non_empty = ab_df[col].fillna("").astype(str).str.strip().str.len() > 0
        except Exception:  # noqa: BLE001
            # Defensive: a non-string-coercible column shouldn't
            # be picked silently -- skip it and keep looking.
            continue
        if bool(non_empty.any()):
            return col
    return None


def count_open_barriers(ab_df: Optional[pd.DataFrame]) -> int:
    """Open AB count using canonical status normalization."""

    if _is_empty(ab_df):
        return 0
    if "case_status_norm" in ab_df.columns:
        series = ab_df["case_status_norm"].fillna("").astype(str)
    elif "status_norm" in ab_df.columns:
        series = ab_df["status_norm"].fillna("").astype(str)
    else:
        # Round 72 / Build 46 (Finding 2): prefer the canonical
        # ``STATUS_C`` ahead of the historically-blank
        # ``AB_STATUS_C`` so the leader / renewal / compact pipelines
        # all converge on the same "Open" denominator. The helper
        # ``_select_first_populated_status_column`` skips any
        # candidate that is present but 100% NA, keeping the
        # secondary fallbacks usable for pipelines that only carry
        # ``AB_STATUS_C``.  Pre-R72 the leader DOCX wrote "Open
        # Adoption Barriers: 0" because per-CSSM ``abs_df`` carried
        # both ``STATUS_C`` (real values) and ``AB_STATUS_C``
        # (all-NaN, by Snowflake join shape), and the naive
        # ``next(c for c in candidates if c in ab_df.columns)`` pick
        # routed straight into the empty column.
        candidates = ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
        col = _select_first_populated_status_column(ab_df, candidates)
        if col is None:
            return 0
        series = ab_df[col].fillna("").astype(str).apply(normalize_status_label)
    # Round 53: open barriers are distinct barrier records, not duplicate
    # assignee/detail rows.
    return _count_barrier_records(ab_df, series == "Open")


def count_closed_barriers(ab_df: Optional[pd.DataFrame]) -> int:
    """Round 30 / M1: Closed AB count using canonical status normalization.

    Mirrors :func:`count_open_barriers` for the closed/resolved side
    so leader / executive / compact reports stop using inline regexes
    like ``status.contains(r'closed|resolved|complete', case=False)``
    that conflate "Resolved" with "Resolved (Pending Customer Review)"
    or miss synonyms when SF rolls out a new status label.

    Status labels are normalized via :func:`normalize_status_label`
    (the same helper used by the lifecycle pipeline).  Any label
    whose normalized form is ``"Closed"`` is counted -- this includes
    "Closed", "Resolved", "Complete", "Completed", "Done", and any
    synonyms wired into the normalization map.
    """

    if _is_empty(ab_df):
        return 0
    if "case_status_norm" in ab_df.columns:
        series = ab_df["case_status_norm"].fillna("").astype(str)
    elif "status_norm" in ab_df.columns:
        series = ab_df["status_norm"].fillna("").astype(str)
    else:
        # Round 72 / Build 46 (Finding 2): mirror ``count_open_barriers``
        # priority + skip-empty logic so the closed-side count
        # also converges on the canonical ``STATUS_C`` when the
        # legacy ``AB_STATUS_C`` is present-but-blank in the
        # leader/renewal Snowflake AB extract.  Without the
        # symmetric fix the closed denominator silently drifted
        # to zero on the same per-CSSM frames.
        candidates = ("STATUS_C", "AB_STATUS_C", "Status", "STATUS")
        col = _select_first_populated_status_column(ab_df, candidates)
        if col is None:
            return 0
        series = ab_df[col].fillna("").astype(str).apply(normalize_status_label)
    # Round 53.1: closed barriers use the same distinct-record semantics as
    # open / critical counts so status-mix narratives do not re-inflate rows.
    return _count_barrier_records(ab_df, series == "Closed")


def count_customers_with_barriers(ab_df: Optional[pd.DataFrame]) -> int:
    """Round 30 / M3: distinct customers with >=1 adoption barrier.

    Single source of truth for the "Customers with Barriers" tile that
    appears in the leader, executive, and compact reports.  Without
    this helper, each report inlined ``.nunique()`` over a different
    candidate column (``customer_name`` vs ``BU_NAME`` vs
    ``ACCOUNT_ID_C``) and used a different (or no) normalization
    pass, so cosmetic spelling drift -- non-breaking spaces, casing,
    trailing punctuation -- inflated the tile relative to the
    voice-of-customer narrative below it.

    Behaviour:

    - Picks the first available customer-name column from
      ``customer_name`` -> ``BU_NAME`` -> ``CUSTOMER_NAME`` ->
      ``ACCOUNT_NAME``.  Falls back to ``ACCOUNT_ID_C`` if no
      label-bearing column is present.
    - Normalizes via :func:`normalize_customer_name` so cosmetic
      variants ("Acme Co", "Acme co.", "Acme Co\xa0") collapse onto
      one canonical key.
    - Filters out empty / null normalized names.
    - Returns ``0`` for empty / ``None`` frames.

    The arithmetic ("Average Barriers per Customer") that downstream
    callers compute against this denominator therefore agrees across
    all three report types.
    """

    if _is_empty(ab_df):
        return 0
    candidates = (
        "customer_name",
        "BU_NAME",
        "CUSTOMER_NAME",
        "ACCOUNT_NAME",
        "ACCOUNT_ID_C",
    )
    col = next((c for c in candidates if c in ab_df.columns), None)
    if col is None:
        return 0
    try:
        series = ab_df[col].fillna("").astype(str)
    except Exception:  # noqa: BLE001
        return 0
    if col == "ACCOUNT_ID_C":
        keys = series.str.strip()
    else:
        keys = series.apply(normalize_customer_name).fillna("").astype(str).str.strip()
    keys = keys[keys != ""]
    return int(keys.nunique())


_AP_CLOSED_STATUSES = frozenset({
    # Round 64 / Phase 2 (B2): canonical "closed" set used to derive the
    # open count from a dedicated Action_Plans frame. All comparisons are
    # case-insensitive and the candidate's whitespace is collapsed before
    # membership check (so "Completed -  Successful" with double-space
    # also matches). Values mined from the CSConsole STATUS_C field
    # (R57 audit notes; see report_source_injector.py:469 for the LLM
    # status-breakdown reference).
    "completed - successful",
    "completed - unsuccessful",
    "closed - cancelled",
    "closed - canceled",
    "closed",
})

_AP_STATUS_COLUMN_CANDIDATES = (
    "STATUS_C",
    "AP_STATUS_C",
    "Status",
    "STATUS",
    "status",
    "case_status_norm",
    "status_norm",
)


_R124_DASH_TO_ASCII = str.maketrans(  # Round 124
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
    }
)


def _normalize_ap_status_for_open_check(value: Any) -> str:
    """Lower + whitespace-collapse a single status cell for membership check."""
    if value is None:
        return ""
    try:
        text = str(value).strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    if not text:
        return ""
    # Round 124: fold Unicode dashes (en-dash U+2013, em-dash, figure dash,
    # minus sign, non-breaking hyphen) to ASCII "-" BEFORE membership check.
    # CSConsole exports "Closed - Cancelled" with a U+2013 en-dash, which did
    # not match the ASCII-hyphen entries in ``_AP_CLOSED_STATUSES`` -- so three
    # cancelled action plans leaked into the OPEN count (119 vs the true 116).
    text = text.translate(_R124_DASH_TO_ASCII)
    # Collapse internal whitespace so "completed -  successful" -> "completed - successful".
    return " ".join(text.split())


def count_open_action_plans(
    ab_df: Optional[pd.DataFrame],
    ap_df: Optional[pd.DataFrame] = None,
) -> int:
    """Deterministic count of currently-open action plans.

    Round 64 / Phase 2 (B2) extends the Round 62 / B helper with an
    optional ``ap_df`` parameter so the comprehensive XLSX flow (which
    now ships a dedicated ``Action_Plans`` sheet -- mirroring the
    Compact and Leader flows -- instead of leaning on the empty
    ``AB_Detail_All.Action Plan Title`` column) can derive the count
    from real action-plan rows.

    Resolution order:

    1. If ``ap_df`` is provided AND non-empty AND has a recognizable
       status column (``STATUS_C`` / ``AP_STATUS_C`` / ``Status`` /
       ``STATUS`` / ``status`` / ``case_status_norm`` / ``status_norm``),
       count rows whose normalized status is NOT in the closed set
       (``Completed - Successful``, ``Completed - Unsuccessful``,
       ``Closed - Cancelled``, ``Closed``). The check is
       case-insensitive and whitespace-collapsed so cosmetic variants
       (``"completed -  successful"`` etc.) still match.
    2. If ``ap_df`` is provided but has NO recognizable status column,
       count all non-null rows (treat them all as "currently
       represented"). This matches Compact's earlier behavior when the
       Snowflake projection skipped STATUS_C.
    3. If ``ap_df`` is None or empty: fall back to the Round 62 / B
       AB_Detail_All path (count non-empty ``Action Plan Title``
       cells). Preserves R62/B back-compat for the renewal scenario
       and for any older workbook that pre-dates the dedicated sheet.

    Returns 0 in every fully-empty / fully-missing branch.
    """

    # Round 64 / Phase 2 (B2) -- dedicated Action_Plans sheet path.
    if ap_df is not None and not _is_empty(ap_df):
        # Round 65 / Phase 1 (C-2): the comprehensive XLSX always-write
        # fallback may stamp a single provenance row when both CSConsole
        # and Snowflake returned zero APs (so the operator sees honest
        # provenance instead of a missing sheet). Skip those rows from
        # the open-count so the Summary KPI reads 0 in that case (not 1).
        if "_adoptiq_provenance_row" in ap_df.columns:
            try:
                _mask = ap_df["_adoptiq_provenance_row"].fillna(False).astype(bool)
                ap_df = ap_df.loc[~_mask]
            except Exception:  # noqa: BLE001 - defensive
                pass
            if _is_empty(ap_df):
                return 0
        # Round 66 / Pass 1 (B3): legacy XLSX back-compat -- workbooks
        # generated by Build N where N < 38 carry the provenance row
        # WITHOUT the ``_adoptiq_provenance_row`` marker column (the
        # marker column was added in R65/C-2). Older workbooks stamp
        # only the human-visible ``AdoptIQ_Status`` column with the
        # sentinel string ``"EMPTY"``. Without this fallback, an
        # operator re-running ``count_open_action_plans`` against an
        # older workbook (e.g. via the QA harness or a re-render of
        # an archived run) would see the open-count read 1 instead
        # of the truthful 0. Detect the heuristic by checking for an
        # ``AdoptIQ_Status == 'EMPTY'`` row when the marker column is
        # absent. The match is case-insensitive + whitespace-tolerant
        # so trivially-malformed exports still resolve.
        elif "AdoptIQ_Status" in ap_df.columns:
            try:
                _status_norm = (
                    ap_df["AdoptIQ_Status"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )
                _legacy_provenance_mask = _status_norm == "EMPTY"
                if _legacy_provenance_mask.any():
                    ap_df = ap_df.loc[~_legacy_provenance_mask]
                    if _is_empty(ap_df):
                        return 0
            except Exception:  # noqa: BLE001 - defensive
                pass
        status_col = next(
            (c for c in _AP_STATUS_COLUMN_CANDIDATES if c in ap_df.columns),
            None,
        )
        if status_col is None:
            # Round 71 / Phase 4 (#20): NO recognizable status column.
            # Pre-R71 the helper returned ``int(len(ap_df))``, which
            # over-counted every row as "open" -- a Snowflake export
            # that had stripped the STATUS_C projection (or a bare
            # CSConsole sheet missing every status candidate column)
            # silently inflated the headline KPI to the row total.
            # Return 0 instead and emit a WARN so the operator can
            # see the missing-column condition in the analysis log.
            # The pre-R71 behaviour was a leftover of "Compact's
            # pre-Round-30 fallback"; with R64/B2 wiring the
            # ``Action_Plans`` sheet through this helper, the
            # missing-column condition is now an honest data-quality
            # signal we should not paper over.
            try:
                _r71_known_cols = list(ap_df.columns)[:8]
            except Exception:  # noqa: BLE001
                _r71_known_cols = []
            try:
                logger.warning(
                    "Round 71 / Phase 4 (#20): count_open_action_plans "
                    "received a non-empty ap_df with no recognized "
                    "status column (candidates=%s). Returning 0 to "
                    "avoid over-counting; first columns=%s",
                    list(_AP_STATUS_COLUMN_CANDIDATES),
                    _r71_known_cols,
                )
            except Exception:  # noqa: BLE001
                pass
            return 0
        try:
            series = ap_df[status_col].apply(_normalize_ap_status_for_open_check)
        except Exception:  # noqa: BLE001 - defensive against weird mixed dtypes
            return 0
        # An empty/blank status is treated as OPEN (the safer half: an
        # action plan with no status is more likely an unfinished /
        # in-flight item than an explicitly closed one).
        is_closed = series.isin(_AP_CLOSED_STATUSES)
        return int((~is_closed).sum())

    # Round 62 / B back-compat path -- AB_Detail_All embedded title column.
    if _is_empty(ab_df):
        return 0
    candidates = ("Action Plan Title", "action_plan_title", "AP_TITLE_C", "ACTION_PLAN_TITLE")
    col = next((c for c in candidates if c in ab_df.columns), None)
    if col is None:
        return 0
    try:
        series = ab_df[col].fillna("").astype(str).str.strip()
    except Exception:  # noqa: BLE001 - defensive against weird mixed dtypes
        return 0
    return int((series != "").sum())


def count_action_plan_completed(ap_df: Optional[pd.DataFrame]) -> int:
    """Round 30 / M1: Completed action-plan count via canonical normalization.

    Action plans use the same ``STATUS_C`` schema as adoption
    barriers and TAC cases (Round 6 / Phase 5.6 confirmed).  This
    helper centralizes the "completed" definition (anything whose
    normalized status is ``"Closed"``) so the leader report's
    "completed_aps" column and any future executive / compact
    surfaces share one source of truth.

    Without this helper, the leader report previously used
    ``status.str.contains(r'complete|closed|done', case=False)``
    which (a) does NOT use the canonical normalization map, (b) can
    over-count when a label like "Closed - Will Not Complete" should
    not be considered a successful completion, and (c) drifts as new
    SF status labels are introduced.
    """

    if _is_empty(ap_df):
        return 0
    if "case_status_norm" in ap_df.columns:
        series = ap_df["case_status_norm"].fillna("").astype(str)
    elif "status_norm" in ap_df.columns:
        series = ap_df["status_norm"].fillna("").astype(str)
    else:
        candidates = ("AP_STATUS_C", "STATUS_C", "Status", "STATUS")
        col = next((c for c in candidates if c in ap_df.columns), None)
        if col is None:
            return 0
        series = ap_df[col].fillna("").astype(str).apply(normalize_status_label)
    return int((series == "Closed").sum())


# ---------------------------------------------------------------------------
# Round 142 -- decision-report Action Plan lifecycle and chart aggregations
# ---------------------------------------------------------------------------

# These column contracts intentionally live beside the existing Action Plan
# counters.  Report formatters may choose presentation labels, but they must
# not rediscover identifiers, statuses, dates, or lifecycle buckets inline.
ACTION_PLAN_ID_COLUMNS: Tuple[str, ...] = (
    "Record_ID",
    "ID",
    "ACTION_PLAN_ID",
    "AP_ID",
    "TASK_ID",
    "Id",
    "id",
)
ACTION_PLAN_TITLE_COLUMNS: Tuple[str, ...] = (
    "ACTION_PLAN_TITLE_C",
    "SUBJECT_C",
    "TITLE_C",
    "NAME",
    "Title",
    "title",
)
ACTION_PLAN_DUE_DATE_COLUMNS: Tuple[str, ...] = (
    "DUE_DATE_C",
    "NEXT_ACTION_DUE_DATE_C",
    "DUE_DATE",
    "Due Date",
    "TARGET_DATE_C",
)
ACTION_PLAN_CREATED_DATE_COLUMNS: Tuple[str, ...] = (
    "CREATED_DATE_C",
    "OPEN_DATE_C",
    "CREATED_DATE",
    "CREATEDDATE",
    "Created Date",
)

ACTION_PLAN_BUCKET_ORDER: Tuple[str, ...] = (
    "Overdue",
    "Due Soon",
    "Open",
    "Blocked / On Hold",
    "Completed",
    "Unknown",
)


def first_populated_column(df: Optional[pd.DataFrame], candidates: Sequence[str]) -> Optional[str]:
    """Return the first candidate with at least one substantive value.

    Round 142 makes the helper public because report lineage must state the
    exact field selected.  Merely checking column presence is insufficient:
    Snowflake views often expose a preferred column that is entirely null
    while a legacy fallback contains the actual values.
    """

    if _is_empty(df):
        return None
    for column in candidates:
        if column not in df.columns:
            continue
        try:
            populated = df[column].notna() & df[column].astype(str).str.strip().ne("")
        except Exception:  # noqa: BLE001
            continue
        if bool(populated.any()):
            return column
    return None


def deduplicate_records_by_id(
    df: Optional[pd.DataFrame],
    *,
    id_candidates: Sequence[str] = ("Record_ID", "ID", "Id", "id"),
) -> Tuple[pd.DataFrame, Optional[str]]:
    """Copy and deduplicate records on the first populated stable-ID field.

    Rows with a missing identifier are deliberately retained.  Dropping them
    would turn a data-quality problem into a false zero; inventing an ID would
    falsely imply source-system traceability.  When no populated ID column is
    available, the copied frame is returned unchanged and the selected column
    is ``None``.
    """

    if df is None:
        return pd.DataFrame(), None
    source_attrs = dict(getattr(df, "attrs", {}) or {})
    try:
        use = df.copy()
    except Exception:  # noqa: BLE001
        return pd.DataFrame(), None
    if use.empty:
        return use, first_populated_column(use, id_candidates)
    id_column = first_populated_column(use, id_candidates)
    if id_column is None:
        result = use.reset_index(drop=True)
        result.attrs.update(source_attrs)
        return result, None
    try:
        tokens = use[id_column].fillna("").astype(str).str.strip()
        with_id = use.loc[tokens.ne("")].copy()
        without_id = use.loc[tokens.eq("")].copy()
        with_id["__adoptiq_id_sort"] = tokens.loc[with_id.index]
        with_id = (
            with_id.sort_values("__adoptiq_id_sort", kind="stable")
            .drop_duplicates(subset=["__adoptiq_id_sort"], keep="first")
            .drop(columns=["__adoptiq_id_sort"])
        )
        result = pd.concat([with_id, without_id], ignore_index=True)
        result.attrs.update(source_attrs)
        return result, id_column
    except Exception:  # noqa: BLE001
        result = use.reset_index(drop=True)
        result.attrs.update(source_attrs)
        return result, id_column


def count_distinct_records_by_id(
    df: Optional[pd.DataFrame],
    *,
    id_candidates: Sequence[str] = ("Record_ID", "ID", "Id", "id"),
) -> int:
    """Return the canonical count produced by :func:`deduplicate_records_by_id`."""

    deduped, _ = deduplicate_records_by_id(df, id_candidates=id_candidates)
    return int(len(deduped))


def _coalesce_row_values(
    df: pd.DataFrame,
    candidates: Sequence[str],
    *,
    stringify: bool = False,
) -> Tuple[pd.Series, List[str]]:
    """Resolve the first substantive candidate independently for every row."""

    result = pd.Series([None] * len(df), index=df.index, dtype="object")
    used: List[str] = []
    for column in candidates:
        if column not in df.columns:
            continue
        values = df[column]
        try:
            substantive = values.notna() & values.astype(str).str.strip().ne("")
        except Exception:  # noqa: BLE001
            substantive = values.notna()
        take = result.isna() & substantive
        if not bool(take.any()):
            continue
        replacement = values.loc[take]
        if stringify:
            replacement = replacement.astype(str).str.strip()
        result.loc[take] = replacement
        used.append(column)
    return result, used


def _action_plan_status_bucket(value: Any) -> str:
    """Map a source Action Plan status into a non-overlapping lifecycle band.

    Exact normalized domain values are used instead of substring matching so
    ``Inactive`` is not treated as ``Active`` and ``Incomplete`` /
    ``Unresolved`` are not mistaken for completed work.
    """

    token = _normalize_ap_status_for_open_check(value)
    if not token:
        return "Unknown"
    blocked = {
        "blocked",
        "on hold",
        "on-hold",
        "paused",
        "cancelled",
        "canceled",
    }
    completed = {
        "complete",
        "completed",
        "resolved",
        "done",
        "closed",
        "completed - successful",
        "completed - unsuccessful",
        "closed - successful",
        "closed - unsuccessful",
        "closed - cancelled",
        "closed - canceled",
    }
    open_statuses = {
        "open",
        "new request",
        "on track",
        "off track",
        "off trajectory",
        "at risk",
        "in progress",
        "active",
        "pending",
        "not started",
    }
    if token in blocked or token.startswith("blocked -") or token.startswith("on hold -"):
        return "Blocked / On Hold"
    if token in completed or token.startswith("completed -") or token.startswith("closed -"):
        return "Completed"
    if token in open_statuses or token.startswith("in progress -"):
        return "Open"
    return "Unknown"


def build_action_plan_lifecycle(
    ap_df: Optional[pd.DataFrame],
    *,
    as_of: Any,
    due_soon_days: int = 14,
) -> Dict[str, Any]:
    """Return canonical, distinct Action Plan lifecycle metrics and rows.

    ``as_of`` is mandatory so fixture runs are deterministic and a report can
    never age plans against an implicit render-time clock.  ``Open`` is the
    canonical active total.  ``Overdue`` and ``Due Soon`` are mutually
    exclusive subsets of that total; ``Open (other)`` is the remainder.
    Unknown-status rows never silently enter an open or completed bucket.
    """

    try:
        horizon = max(int(due_soon_days), 0)
    except Exception:  # noqa: BLE001
        horizon = 14
    as_of_ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(as_of_ts):
        raise ValueError("build_action_plan_lifecycle requires a valid explicit as_of timestamp")
    as_of_day = as_of_ts.normalize()

    source_state = source_data_state(ap_df)
    source_attrs = dict(getattr(ap_df, "attrs", {}) or {})
    records = ap_df.copy() if isinstance(ap_df, pd.DataFrame) else pd.DataFrame()
    record_ids, id_columns = _coalesce_row_values(
        records,
        ACTION_PLAN_ID_COLUMNS,
        stringify=True,
    )
    records["__adoptiq_coalesced_record_id"] = record_ids.fillna("").astype(str).str.strip()
    if not records.empty:
        with_id = records.loc[records["__adoptiq_coalesced_record_id"].ne("")].copy()
        without_id = records.loc[records["__adoptiq_coalesced_record_id"].eq("")].copy()
        with_id = (
            with_id.sort_values("__adoptiq_coalesced_record_id", kind="stable")
            .drop_duplicates(subset=["__adoptiq_coalesced_record_id"], keep="first")
        )
        records = pd.concat([with_id, without_id], ignore_index=True, sort=False)
    records.attrs.update(source_attrs)

    titles, title_columns = _coalesce_row_values(
        records,
        ACTION_PLAN_TITLE_COLUMNS,
        stringify=True,
    )
    statuses, status_columns = _coalesce_row_values(
        records,
        _AP_STATUS_COLUMN_CANDIDATES,
        stringify=True,
    )
    due_values, due_columns = _coalesce_row_values(records, ACTION_PLAN_DUE_DATE_COLUMNS)
    created_values, created_columns = _coalesce_row_values(records, ACTION_PLAN_CREATED_DATE_COLUMNS)

    enriched = records.copy()
    if enriched.empty:
        for name in (
            "AdoptIQ_Record_ID",
            "AdoptIQ_Title",
            "AdoptIQ_Status_Bucket",
            "AdoptIQ_Due_Date",
            "AdoptIQ_Age_Days",
            "AdoptIQ_Due_Days",
            "AdoptIQ_Data_Quality",
        ):
            enriched[name] = pd.Series(dtype="object")
    else:
        record_ids = enriched["__adoptiq_coalesced_record_id"].fillna("").astype(str).str.strip()
        titles = titles.reindex(enriched.index).fillna("").astype(str).str.strip()
        buckets = statuses.reindex(enriched.index).map(_action_plan_status_bucket)
        due_dates = pd.to_datetime(due_values.reindex(enriched.index), errors="coerce", utc=True).dt.normalize()
        created_dates = pd.to_datetime(created_values.reindex(enriched.index), errors="coerce", utc=True).dt.normalize()

        due_days = (due_dates - as_of_day).dt.days
        age_days = (as_of_day - created_dates).dt.days
        open_mask = buckets.eq("Open")
        overdue_mask = open_mask & due_days.notna() & due_days.lt(0)
        due_soon_mask = open_mask & due_days.notna() & due_days.between(0, horizon, inclusive="both")
        buckets = buckets.mask(overdue_mask, "Overdue")
        buckets = buckets.mask(due_soon_mask, "Due Soon")

        quality = pd.Series(["OK"] * len(enriched), index=enriched.index, dtype="object")
        quality = quality.mask(record_ids.eq(""), "Missing stable source ID")
        quality = quality.mask(titles.eq(""), "Missing title")
        both_missing = record_ids.eq("") & titles.eq("")
        quality = quality.mask(both_missing, "Missing stable source ID; missing title")

        enriched["AdoptIQ_Record_ID"] = record_ids
        enriched["AdoptIQ_Title"] = titles.mask(titles.eq(""), "Title unavailable")
        enriched["AdoptIQ_Status_Bucket"] = buckets
        enriched["AdoptIQ_Due_Date"] = due_dates
        enriched["AdoptIQ_Age_Days"] = age_days.where(age_days.ge(0))
        enriched["AdoptIQ_Due_Days"] = due_days
        enriched["AdoptIQ_Data_Quality"] = quality

    enriched = enriched.drop(columns=["__adoptiq_coalesced_record_id"], errors="ignore")

    enriched.attrs.update(dict(getattr(records, "attrs", {}) or {}))
    bucket_counts = {
        bucket: int((enriched["AdoptIQ_Status_Bucket"] == bucket).sum())
        for bucket in ACTION_PLAN_BUCKET_ORDER
    }
    open_total = bucket_counts["Overdue"] + bucket_counts["Due Soon"] + bucket_counts["Open"]
    missing_title_count = int((enriched["AdoptIQ_Title"] == "Title unavailable").sum())
    missing_id_count = int((enriched["AdoptIQ_Record_ID"] == "").sum())
    return {
        "records": enriched,
        "total": int(len(enriched)),
        "open": open_total,
        "overdue": bucket_counts["Overdue"],
        "due_soon": bucket_counts["Due Soon"],
        "open_other": bucket_counts["Open"],
        "completed": bucket_counts["Completed"],
        "blocked_on_hold": bucket_counts["Blocked / On Hold"],
        "unknown": bucket_counts["Unknown"],
        "missing_title": missing_title_count,
        "missing_record_id": missing_id_count,
        "bucket_counts": bucket_counts,
        "field_selection": {
            "id": id_columns[0] if len(id_columns) == 1 else (id_columns or None),
            "title": title_columns[0] if len(title_columns) == 1 else (title_columns or None),
            "status": status_columns[0] if len(status_columns) == 1 else (status_columns or None),
            "due_date": due_columns[0] if len(due_columns) == 1 else (due_columns or None),
            "created_date": (
                created_columns[0]
                if len(created_columns) == 1
                else (created_columns or None)
            ),
        },
        "as_of_utc": as_of_day.isoformat(),
        "due_soon_days": horizon,
        "deduplication_rule": (
            f"distinct ID using row-coalesced columns {id_columns}; missing-ID rows retained"
            if id_columns
            else "no populated stable-ID field; rows retained and flagged"
        ),
        "source_state": source_state["state"],
        "source_state_detail": source_state["detail"],
    }


def count_team_members(team_data: Optional[Dict[str, Any]]) -> int:
    """Round 142 canonical count of populated team-member bundles.

    A member key is counted once when its value is a mapping.  Metadata keys
    or malformed values are excluded, making the count stable when a report
    bundle carries auxiliary top-level entries.
    """

    if not isinstance(team_data, dict):
        return 0
    return sum(
        1
        for key, value in team_data.items()
        if str(key).strip()
        and not str(key).startswith("__")
        and isinstance(value, dict)
        and not bool(value.get("_adoptiq_unassigned_bundle"))
    )


def action_plan_chart_series(lifecycle: Dict[str, Any]) -> pd.DataFrame:
    """Return the exact non-overlapping Action Plan series used by charts."""

    counts = (lifecycle or {}).get("bucket_counts", {}) or {}
    source_state = str((lifecycle or {}).get("source_state") or "available")
    source_unavailable = source_state in {"failed", "unavailable"}
    return pd.DataFrame(
        [
            {
                "Series": "Action Plan status and aging",
                "Category": bucket,
                "Value": None if source_unavailable else int(counts.get(bucket, 0)),
                "Source_State": source_state,
            }
            for bucket in ACTION_PLAN_BUCKET_ORDER
        ]
    )


_ACTIVITY_SOURCE_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "Action Plans": {
        "id_candidates": ACTION_PLAN_ID_COLUMNS,
        "date_candidates": ACTION_PLAN_CREATED_DATE_COLUMNS,
        "counter": count_total_action_plans,
    },
    "Adoption Barriers": {
        "id_candidates": ("Record_ID", "ID", "BARRIER_ID", "Id", "id"),
        "date_candidates": ("OPEN_DATE_C", "CREATED_DATE_C", "open_date", "Created Date"),
        "counter": count_total_barriers,
    },
    "Customer Pulse": {
        "id_candidates": ("Record_ID", "ID", "PULSE_ID", "Id", "id"),
        "date_candidates": (
            "PULSE_DATE_C",
            "CREATED_DATE_C",
            "DATE_C",
            "Created Date",
            "created_at",
        ),
        "counter": count_total_customer_pulse,
    },
    "TAC Cases": {
        "id_candidates": (
            "Record_ID",
            "SR Number",
            "Case Number",
            "Case #",
            "CASE_NUMBER",
            "case_id",
            "ID",
        ),
        "date_candidates": (
            "Date/Time Opened",
            "OPEN_DATE_C",
            "open_date",
            "Date Opened",
            "CREATED_DATE_C",
        ),
        "counter": count_total_tac,
    },
}


def source_data_state(df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Classify source availability without collapsing failure into zero."""

    if df is None:
        return {"state": "unavailable", "detail": "source frame not supplied"}
    attrs = getattr(df, "attrs", {}) or {}
    if attrs.get("source_unavailable"):
        return {
            "state": "unavailable",
            "detail": str(attrs.get("source_unavailable_detail") or "source frame not supplied"),
        }
    fetch_error = attrs.get("fetch_error")
    is_stale = bool(attrs.get("stale") or attrs.get("is_stale") or attrs.get("_stale_storage"))
    is_partial = bool(attrs.get("fetch_error_partial") or attrs.get("partial"))
    if fetch_error and getattr(df, "empty", True):
        return {"state": "failed", "detail": str(fetch_error)}
    if fetch_error or is_partial:
        return {
            "state": "partial",
            "detail": str(
                fetch_error
                or attrs.get("source_mode_detail")
                or "partial source"
            ),
        }
    if is_stale:
        return {
            "state": "stale",
            "detail": str(attrs.get("source_mode_detail") or "source marked stale"),
        }
    if getattr(df, "empty", True):
        return {"state": "zero", "detail": "successful source returned zero records"}
    return {"state": "available", "detail": "source records available"}


def build_activity_mix(
    *,
    action_plans_df: Optional[pd.DataFrame],
    ab_df: Optional[pd.DataFrame],
    customer_pulse_df: Optional[pd.DataFrame],
    tac_df: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    """Build canonical distinct activity counts without double-counting BEMS.

    BEMS is deliberately absent from the additive series because it is a TAC
    subset.  Callers may show a separate BEMS annotation, but it must not be
    added to ``total``.  Failed/unavailable sources carry ``Value=None`` so a
    plotter cannot silently draw them as zero.
    """

    frames = {
        "Action Plans": action_plans_df,
        "Adoption Barriers": ab_df,
        "Customer Pulse": customer_pulse_df,
        "TAC Cases": tac_df,
    }
    rows: List[Dict[str, Any]] = []
    known_total = 0
    incomplete = False
    for source, frame in frames.items():
        state = source_data_state(frame)
        if state["state"] in {"unavailable", "failed"}:
            value = None
            incomplete = True
        else:
            counter = _ACTIVITY_SOURCE_CONTRACTS[source]["counter"]
            value = int(counter(frame))
            known_total += value
            if state["state"] in {"partial", "stale"}:
                incomplete = True
        rows.append(
            {
                "Series": "Activity mix",
                "Category": source,
                "Value": value,
                "Source_State": state["state"],
                "State_Detail": state["detail"],
                "Included_In_Total": True,
            }
        )
    return {
        "series": pd.DataFrame(rows),
        "known_total": known_total,
        "is_complete": not incomplete,
        "total_state": "complete" if not incomplete else "partial",
        "definition": "distinct Action Plans + Adoption Barriers + Customer Pulse + TAC Cases; BEMS is a TAC subset",
    }


def build_activity_trend(
    *,
    action_plans_df: Optional[pd.DataFrame],
    ab_df: Optional[pd.DataFrame],
    customer_pulse_df: Optional[pd.DataFrame],
    tac_df: Optional[pd.DataFrame],
    as_of: Any,
    days: int,
    frequency: str = "W-SUN",
) -> Dict[str, Any]:
    """Return deterministic dateable activity counts by source and period.

    The input is deduplicated by each source's canonical stable-ID candidates
    before grouping.  Missing or invalid dates are excluded and surfaced in a
    coverage table; they are never assigned to an arbitrary period.
    """

    as_of_ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    if pd.isna(as_of_ts):
        raise ValueError("build_activity_trend requires a valid explicit as_of timestamp")
    try:
        window_days = max(int(days), 1)
    except Exception:  # noqa: BLE001
        window_days = 90
    end_day = as_of_ts.normalize()
    start_day = end_day - pd.Timedelta(days=window_days - 1)
    frames = {
        "Action Plans": action_plans_df,
        "Adoption Barriers": ab_df,
        "Customer Pulse": customer_pulse_df,
        "TAC Cases": tac_df,
    }
    trend_parts: List[pd.DataFrame] = []
    coverage_rows: List[Dict[str, Any]] = []
    for source, frame in frames.items():
        state = source_data_state(frame)
        contract = _ACTIVITY_SOURCE_CONTRACTS[source]
        deduped, id_column = deduplicate_records_by_id(
            frame,
            id_candidates=contract["id_candidates"],
        )
        date_column = first_populated_column(deduped, contract["date_candidates"])
        total_records = int(len(deduped))
        if date_column is None:
            coverage_rows.append(
                {
                    "Source": source,
                    "Source_State": state["state"],
                    "Date_Field": "",
                    "ID_Field": id_column or "",
                    "Records": total_records if state["state"] not in {"failed", "unavailable"} else None,
                    "Dateable_Records": 0,
                    "Excluded_Invalid_or_Missing_Date": total_records,
                    "Excluded_Outside_Window": 0,
                    "Trend_State": "unavailable" if total_records else state["state"],
                }
            )
            continue
        dates = pd.to_datetime(deduped[date_column], errors="coerce", utc=True).dt.normalize()
        in_window = dates.notna() & dates.between(start_day, end_day, inclusive="both")
        usable = deduped.loc[in_window].copy()
        usable["__adoptiq_activity_date"] = dates.loc[in_window]
        if not usable.empty:
            # ``W-SUN`` means Monday-through-Sunday periods.  Converting the
            # period start back to UTC yields a stable, timezone-aware key.
            try:
                periods = usable["__adoptiq_activity_date"].dt.tz_localize(None).dt.to_period(frequency).dt.start_time
            except Exception:  # noqa: BLE001
                periods = usable["__adoptiq_activity_date"].dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time
            usable["Period_Start"] = pd.to_datetime(periods, utc=True)
            grouped = usable.groupby("Period_Start", as_index=False, dropna=False).size()
            grouped = grouped.rename(columns={"size": "Value"})
            grouped.insert(0, "Source_State", state["state"])
            grouped.insert(0, "Source", source)
            grouped.insert(0, "Series", "Activity trend")
            trend_parts.append(grouped)
        dateable = int(in_window.sum())
        invalid_or_missing = int(dates.isna().sum())
        outside_window = int((dates.notna() & ~dates.between(start_day, end_day, inclusive="both")).sum())
        coverage_rows.append(
            {
                "Source": source,
                "Source_State": state["state"],
                "Date_Field": date_column,
                "ID_Field": id_column or "",
                "Records": total_records if state["state"] not in {"failed", "unavailable"} else None,
                "Dateable_Records": dateable,
                "Excluded_Invalid_or_Missing_Date": invalid_or_missing,
                "Excluded_Outside_Window": outside_window,
                "Trend_State": "available" if dateable else "unavailable",
            }
        )
    trend = (
        pd.concat(trend_parts, ignore_index=True)
        if trend_parts
        else pd.DataFrame(columns=["Series", "Source", "Source_State", "Period_Start", "Value"])
    )
    if not trend.empty:
        trend = trend.sort_values(["Period_Start", "Source"], kind="stable").reset_index(drop=True)
    return {
        "series": trend,
        "coverage": pd.DataFrame(coverage_rows),
        "as_of_utc": end_day.isoformat(),
        "window_start_utc": start_day.isoformat(),
        "frequency": frequency,
        "has_data": not trend.empty,
    }


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
    # Round 53.1: adoption-barrier activity uses the same logical record
    # count as report KPIs; raw rows can fan out by assignee/detail.
    ab = count_total_barriers(ab_df)
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


# Round 5 / Phase 5.16: pull the canonical band thresholds from the
# scorer so we don't keep a literal ``55.0`` HIGH cutoff (or
# equivalent) drifting in this module.  ``risk_scoring`` is the
# authoritative source of truth; if (for any reason) it cannot be
# imported at module-load time, fall back to the documented
# defaults so no caller is left with an undefined symbol.
try:
    from risk_scoring import RISK_BAND_THRESHOLDS as _RISK_BAND_THRESHOLDS_FROM_SCORER
    RISK_BAND_THRESHOLDS = dict(_RISK_BAND_THRESHOLDS_FROM_SCORER)
except Exception as _rs_import_err:  # pragma: no cover - defensive only
    # Round 7 / Phase 3.5: previously this branch silently created a
    # second source of truth (a hard-coded dict that could drift from
    # ``risk_scoring.RISK_BAND_THRESHOLDS``).  Raise instead so any
    # import failure is loud and unambiguous; callers MUST be able to
    # import the canonical thresholds.
    raise ImportError(
        "Round 7 / Phase 3.5: canonical_metrics requires "
        "risk_scoring.RISK_BAND_THRESHOLDS as the single source of "
        f"truth; refusing to fall back to literal defaults. Cause: "
        f"{type(_rs_import_err).__name__}: {_rs_import_err}"
    ) from _rs_import_err
# Round 7 / Phase 3.5: the literal-fallback dict that previously lived
# here has been removed.  Keeping the unreachable block below would
# compile but would re-introduce the silent-drift surface that the
# audit found.  An explicit raise above replaces it.
if False:  # noqa: SIM108  -- retained for diff-readability across audit rounds
    RISK_BAND_THRESHOLDS = {
        "CRITICAL": 75.0,
        "HIGH": 55.0,
        "MEDIUM": 35.0,
        "LOW": 15.0,
        "HEALTHY": 0.0,
    }


def compute_high_risk_count(
    risk_profiles: Optional[Dict[str, Dict[str, Any]]],
    *,
    scale: str = RISK_SCALE_0_TO_100,
    high_threshold_0_to_10: float = 5.5,
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
          ``high_threshold_0_to_10`` (default 5.5, which equals the
          0-100 band cutoff at ``RISK_BAND_THRESHOLDS["HIGH"]/10``).
          The 0-10 branch ALSO honors ``risk_band == "CRITICAL"|"HIGH"``
          and ``color == "red"`` so the headline count and any narrative
          table filtered by ``is_high_risk_profile`` agree byte-for-byte.
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
                # Round 6 / Phase 5.5: derive the cutoff from the
                # canonical RISK_BAND_THRESHOLDS so this branch
                # cannot drift if HIGH is ever retuned (e.g. raised
                # to 60 or lowered to 50).  The previous literal
                # 55.0 silently disagreed with downstream callers
                # that already used RISK_BAND_THRESHOLDS["HIGH"].
                if float(score) >= float(RISK_BAND_THRESHOLDS["HIGH"]):
                    count += 1
            except (TypeError, ValueError):
                continue
        return count

    # 0-10 legacy scale
    count = 0
    threshold = float(high_threshold_0_to_10)
    for profile in risk_profiles.values():
        # Phase 1.5: also honor the 0-100 band override on the 0-10 branch
        # so a profile flagged ``risk_band="HIGH"`` with score=5.4 (just
        # below the 0-10 default) is still counted, matching is_high_risk_profile.
        band = str(profile.get("risk_band", "")).upper().strip()
        if band in {"CRITICAL", "HIGH"}:
            count += 1
            continue
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


def is_high_risk_profile(
    profile: Optional[Dict[str, Any]],
    *,
    scale: str = RISK_SCALE_0_TO_100,
    high_threshold_0_to_10: float = 5.5,
) -> bool:
    """Return True when ``profile`` matches the canonical high-risk
    definition used by :func:`compute_high_risk_count`.

    Use this to filter rows shown in narrative tables so that the
    headline count and the row list stay in lockstep. Without this,
    callers tend to use ad-hoc ``score >= 6`` filters that miss the
    ``color == "red"`` short-circuit / band overrides and so produce
    a "dashboard says 12, but the table only shows 9" mismatch.
    """

    if not profile or not isinstance(profile, dict):
        return False

    if scale == RISK_SCALE_0_TO_100:
        band = str(profile.get("risk_band", "")).upper().strip()
        if band in {"CRITICAL", "HIGH"}:
            return True
        score = profile.get("risk_score_0_100")
        if score is None:
            score10 = profile.get("risk_score_0_10")
            if score10 is None:
                return False
            try:
                score = float(score10) * 10.0
            except (TypeError, ValueError):
                return False
        try:
            # Round 5 / Phase 5.16: derive the HIGH cutoff from the
            # canonical ``RISK_BAND_THRESHOLDS`` so it can never drift
            # from the scorer / band-split helpers.
            return float(score) >= float(RISK_BAND_THRESHOLDS["HIGH"])
        except (TypeError, ValueError):
            return False

    # 0-10 legacy scale
    # Round 4 / Phase 1.5: honor the canonical risk_band first so the
    # 0-10 branch agrees with the 0-100 branch above.  Without this,
    # a profile carrying ``risk_band='HIGH'`` (or CRITICAL) but a
    # 0-10 score of 5.4 was excluded by the legacy ``score >= 6.0``
    # threshold even though the rest of the report classifies it as
    # HIGH.  The Excel ``High_Risk_Customers`` sheet (which calls
    # this helper on the 0-10 scale) silently dropped those rows.
    band = str(profile.get("risk_band", "")).upper().strip()
    if band in {"CRITICAL", "HIGH"}:
        return True
    color = str(profile.get("color", "")).strip().lower()
    if color == "red":
        return True
    score = profile.get("risk_score_0_10", profile.get("score"))
    if score is None and "risk_score_0_100" in profile:
        try:
            score = float(profile["risk_score_0_100"]) / 10.0
        except (TypeError, ValueError):
            score = None
    try:
        return score is not None and float(score) >= float(high_threshold_0_to_10)
    except (TypeError, ValueError):
        return False


def count_score_range(
    risk_profiles: Optional[Dict[str, Dict[str, Any]]],
    *,
    low: float,
    high: float,
    scale: str = RISK_SCALE_0_TO_100,
    inclusive: str = "left",
) -> int:
    """Round 4: Count profiles whose numeric risk score falls in [low, high).

    Distinct from :func:`compute_high_risk_count` which buckets by the
    canonical CRITICAL/HIGH/MEDIUM/LOW band labels.  Use this helper
    when narratives need to count, e.g., "Score 4-6 (Watch)" customers
    on the legacy 0-10 scale — those rows do *not* line up exactly
    with band MEDIUM (35-55 on 0-100 ≈ 3.5-5.5 on 0-10), so conflating
    the two is the source of the long-standing
    ``moderate_risk_customers`` vs ``medium_risk_customers`` drift.

    Parameters
    ----------
    risk_profiles:
        Mapping of customer_name -> profile dict (same shape accepted
        by :func:`compute_high_risk_count`).
    low, high:
        Inclusive lower bound, exclusive upper bound by default.  Both
        are interpreted on the requested ``scale``.
    scale:
        ``"0_to_100"`` (default, canonical) or ``"0_to_10"`` (legacy).
    inclusive:
        ``"left"`` (default), ``"right"``, ``"both"``, or ``"neither"``.
    """

    if scale not in _RISK_SCALES:
        raise ValueError(
            f"count_score_range: unknown scale {scale!r}. "
            f"Allowed scales: {sorted(_RISK_SCALES)}"
        )
    if inclusive not in {"left", "right", "both", "neither"}:
        raise ValueError(
            f"count_score_range: unknown inclusive {inclusive!r}. "
            f"Allowed: 'left', 'right', 'both', 'neither'"
        )
    if not risk_profiles:
        return 0
    try:
        lo = float(low)
        hi = float(high)
    except (TypeError, ValueError):
        return 0
    if hi < lo:
        lo, hi = hi, lo

    count = 0
    for profile in risk_profiles.values():
        if scale == RISK_SCALE_0_TO_100:
            score = profile.get("risk_score_0_100")
            if score is None:
                s10 = profile.get("risk_score_0_10", profile.get("score"))
                if s10 is None:
                    continue
                try:
                    score = float(s10) * 10.0
                except (TypeError, ValueError):
                    continue
        else:
            score = profile.get("risk_score_0_10", profile.get("score"))
            if score is None and "risk_score_0_100" in profile:
                try:
                    score = float(profile["risk_score_0_100"]) / 10.0
                except (TypeError, ValueError):
                    continue
        try:
            sv = float(score)
        except (TypeError, ValueError):
            continue
        if inclusive == "left":
            in_range = lo <= sv < hi
        elif inclusive == "right":
            in_range = lo < sv <= hi
        elif inclusive == "both":
            in_range = lo <= sv <= hi
        else:
            in_range = lo < sv < hi
        if in_range:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Customer Pulse sentiment
# ---------------------------------------------------------------------------


# Pulse sentiment thresholds operate on a 0-10 numeric scale. The
# legacy ``_derive_sentiment_summary`` used 7.5/5.0; the narrative
# elsewhere referred to a ``/5.0`` scale. We canonicalize to 0-10 and
# document conversion explicitly.
#
# Round 6 / Phase 5.13: these pulse thresholds are deliberately
# INDEPENDENT of ``RISK_BAND_THRESHOLDS`` even though they share
# similar magnitudes (7.5 / 5.0).  Pulse measures customer sentiment
# from the CSConsole CUSTOMER_PULSE__C feed, and risk bands measure
# adoption / support / billing risk from a blended scoring model.
# Tying them together (e.g. setting POSITIVE = RISK_BAND_THRESHOLDS
# ["CRITICAL"]/10) would mean any future risk-band tuning silently
# re-bucketed sentiment data, which would be a hard-to-trace
# correctness regression.  When the product team needs to retune
# pulse, it should be done here in isolation; the risk model is
# tuned in ``risk_scoring.RISK_BAND_THRESHOLDS``.  Keep the two
# decoupled.
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


_BACKFILL_FLAG_COLS = (
    "is_backfilled",
    "is_backfill",
    "backfilled",
    "is_synthetic",
    "synthetic",
)


def _split_backfill_mask(pulse_df: Optional[pd.DataFrame]) -> Optional[pd.Series]:
    """Return a boolean mask of rows that look like backfilled / synthetic
    pulses, or ``None`` if no backfill flag column is present.
    """

    if _is_empty(pulse_df):
        return None
    flag_col = next(
        (c for c in _BACKFILL_FLAG_COLS if c in pulse_df.columns),
        None,
    )
    if not flag_col:
        return None
    try:
        return pulse_df[flag_col].fillna(False).astype(bool)
    except Exception:
        return None


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

    Round 5 / Phase 5.6: when ``pulse_df`` carries a backfill flag
    column (``is_backfilled``, ``is_synthetic``, etc.) a parallel
    ``customer_observed`` summary is included on the returned dict
    that excludes those rows.  Without this, callers that wanted
    "what did the customer actually say" had no way to get it from
    the canonical helper -- forcing them to re-implement filtering
    inline and disagree with the headline number.
    """

    if scale not in _PULSE_SCALES:
        raise ValueError(
            f"pulse_sentiment: unknown scale {scale!r}. "
            f"Allowed scales: {sorted(_PULSE_SCALES)}"
        )

    def _summarize(series: pd.Series) -> Dict[str, Any]:
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

    series = _coerce_pulse_score_series(pulse_df, scale)
    summary = _summarize(series)

    backfill_mask = _split_backfill_mask(pulse_df)
    if backfill_mask is not None and not _is_empty(pulse_df):
        try:
            observed_df = pulse_df.loc[~backfill_mask]
            observed_series = _coerce_pulse_score_series(observed_df, scale)
            observed_summary = _summarize(observed_series)
            backfill_count = int(backfill_mask.sum())
            summary["customer_observed"] = observed_summary
            summary["backfilled_excluded_count"] = backfill_count
            summary["has_backfill_flag"] = True
        except Exception:
            summary["has_backfill_flag"] = False
    else:
        summary["has_backfill_flag"] = False

    return summary


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
    account_to_customer: Optional[Dict[str, str]] = None,
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
            account_to_customer=account_to_customer,
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
        # Medium / Low / Healthy band counts.
        # Round 5 / Phase 5.7: previously the band split was only
        # populated when ``risk_scale == RISK_SCALE_0_TO_100`` and the
        # 0-10 callers ended up with no band breakdown at all (the
        # "Critical vs High vs Medium vs Low vs Healthy" pie was
        # silently zero on every 0-10 path).  Compute the band split
        # for both scales so charts agree byte-for-byte regardless of
        # how risk was scored.
        band_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "HEALTHY": 0}
        for profile in risk_profiles.values():
            band = str(profile.get("risk_band", "")).upper().strip()
            if band in band_counts:
                band_counts[band] += 1
                continue
            # Fallback when ``risk_band`` is missing: derive from the
            # numeric score using canonical thresholds.  This ensures
            # the 0-10 branch (which historically only carried a raw
            # ``score`` field) still produces a band split.
            score_0_100 = profile.get("risk_score_0_100")
            if score_0_100 is None:
                score_0_10 = profile.get("risk_score_0_10")
                if score_0_10 is None:
                    score_0_10 = profile.get("score")
                try:
                    score_0_100 = float(score_0_10) * 10.0 if score_0_10 is not None else None
                except (TypeError, ValueError):
                    score_0_100 = None
            try:
                if score_0_100 is None:
                    continue
                s = float(score_0_100)
            except (TypeError, ValueError):
                continue
            if s >= RISK_BAND_THRESHOLDS["CRITICAL"]:
                band_counts["CRITICAL"] += 1
            elif s >= RISK_BAND_THRESHOLDS["HIGH"]:
                band_counts["HIGH"] += 1
            elif s >= RISK_BAND_THRESHOLDS["MEDIUM"]:
                band_counts["MEDIUM"] += 1
            elif s >= RISK_BAND_THRESHOLDS["LOW"]:
                band_counts["LOW"] += 1
            else:
                band_counts["HEALTHY"] += 1
        # Expose both the rolled-up "high_risk" (CRITICAL + HIGH) used
        # by the headline tile *and* the split bands so charts can
        # render an accurate "Critical vs High" breakdown without
        # silently relabeling. Round 3 fix for the executive risk
        # pie's misleading "High Risk" wedge.
        payload["critical_risk_customers"] = band_counts["CRITICAL"]
        payload["high_only_risk_customers"] = band_counts["HIGH"]
        payload["medium_risk_customers"] = band_counts["MEDIUM"]
        payload["low_risk_customers"] = band_counts["LOW"]
        payload["healthy_customers"] = band_counts["HEALTHY"]
        payload["risk_scale"] = risk_scale

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
    "RISK_BAND_THRESHOLDS",
    "bems_rate",
    "build_portfolio_metrics",
    "compute_high_risk_count",
    "is_high_risk_profile",
    "count_score_range",
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
