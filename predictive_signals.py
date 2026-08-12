"""Round 160 — deterministic predictive escalation engine (pure pandas/numpy).

Predicts P(customer opens a NEW P1/P2 TAC case or BEMS escalation within the
next 30 days) as a transparent, additive points scorecard — the discrete-time
hazard / credit-scorecard architecture, chosen over ML black boxes for
auditability (every point traceable to a named signal and a dated record).

Research basis (Round 160 four-agent research sweep + foreground review;
full synthesis in PREDICTIVE_INTELLIGENCE.md):

* Escalation history is the #1 validated predictor (IBM/UVic field study,
  Montgomery & Damian — 2.5M tickets; SupportLogic production thresholds).
* Trend beats level for case velocity; severity DYNAMICS beat severity level.
* Sentiment trajectory leads outcomes by ~4–9 weeks; staleness is neutral,
  never positive.
* Small-sample honesty: banded observed rates with Jeffreys smoothing and
  Wilson intervals (Brown, Cai & DasGupta 2001); PAVA monotonicity; the
  Pluto & Tasche most-prudent upper bound for zero-event bands; Van Calster
  et al. 2019's calibration hierarchy caps what a small portfolio may claim.
* Strict temporal discipline: features from records dated <= T only, labels
  strictly in (T, T+horizon]; right-censored cutoffs dropped; customers
  with an open P1/P2 at T (date-reconstructed) excluded from evaluation
  (they are reported, not "predicted").

Attribution (formulas re-implemented, no code imported):
* WoE/points/PDO scaling and KS/PSI formulas after scorecardpy
  (MIT, (c) Shichen Xie).
* Observation-grid ("time-travel") construction after fight-churn
  (MIT, (c) 2020 Carl Gold, "Fighting Churn With Data", Listing 4.4).
* PAVA isotonic pooling after Barlow et al. (1972); cf. scikit-learn
  isotonic.py (BSD-3) as a reference implementation.

LEAKAGE DISCIPLINE (hardened by the Round 160 adversarial review):
* Timestamps are strict by construction: features read records dated <= T,
  labels strictly (T, T+30], openness is reconstructed from open/close
  dates (never a status column), and injecting future-dated rows provably
  cannot change features at T (tested).
* Mutable current-state columns (barrier/action-plan status, renewal-risk
  category, the lifetime-max "Highest Priority" field) are EXCLUDED from
  this layer entirely: a current export cannot say what they held at a
  historical cutoff T.
* BEMS designation lives in mutable, undated fields — it is therefore
  EXCLUDED from backtest labels/features (``include_bems=False``) and used
  only for production scoring at T=now, where current state is current.
* KNOWN APPROXIMATION, disclosed not hidden: the export stores each case's
  CURRENT severity, not severity-at-open — a post-open upgrade to P1 is
  backdated to the open date in historical cutoffs.  A snapshot export
  cannot do better; the live round should quantify the upgrade share from
  CSOne case history, and the backtest audit block restates this limit on
  every run.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from canonical_metrics import _r158_parse_dates_utc, source_data_state
from data_normalization import detect_bems_mask, normalize_priority_label

PREDICTIVE_HORIZON_DAYS = 30
MIN_HISTORY_DAYS = 60
PREDICTIVE_CALIBRATION_PATH_ENV = "ADOPTIQ_PREDICTIVE_CALIBRATION_PATH"
_MAX_CALIBRATION_BYTES = 5 * 1024 * 1024
_PREDICTIVE_SOURCE_KEYS = (
    "tac_cases",
    "adoption_barriers",
    "customer_pulse",
)
_COMPLETE_SOURCE_STATES = frozenset({"available", "zero"})
_INCOMPLETE_SOURCE_STATES = frozenset(
    {"failed", "unavailable", "partial", "stale"}
)

_CASE_OPEN_COLUMNS = ("open_date", "Date/Time Opened", "OPEN_DATE_C", "Open Date")
_CASE_CLOSE_COLUMNS = ("closed_date", "Date/Time Closed", "CLOSED_DATE_C", "Closed Date")
# Round 160 adversarial fix: "Highest Priority" is a LIFETIME-MAX field — a
# later upgrade rewrites it, so reading it at a historical cutoff leaks the
# future.  It is excluded everywhere (production included, for consistency).
_CASE_SEVERITY_COLUMNS = ("Severity", "severity_norm", "case_priority_norm")
_BARRIER_OPEN_COLUMNS = ("OPEN_DATE_C", "Open Date", "CREATED_DATE_C", "Created Date")
_BARRIER_SEVERITY_COLUMNS = ("SEVERITY_C", "severity_norm", "Severity")
_PULSE_DATE_COLUMNS = ("PULSE_DATE_C", "Pulse Date")
_PULSE_SCORE_COLUMNS = ("SCORE__C", "Pulse Score", "SCORE", "PULSE_SCORE")
_TECH_COLUMNS = (
    "sub_technology", "SUB_TECHNOLOGY", "Sub Technology", "Tech.", "Tech",
    "Technology", "TECHNOLOGY", "Product", "PRODUCT_NAME",
)

_PULSE_NEGATIVE_THRESHOLD = 4.0  # 0-10 scale; aligned with canonical negative band


def _first_col(df: Optional[pd.DataFrame], candidates: Sequence[str]) -> Optional[str]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    return next((c for c in candidates if c in df.columns), None)


def _dates_asof(series: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    """THE single feature-side convention: records dated <= T belong to
    features.  (Labels use `_dates_in_label_window`, strictly after T.)"""
    parsed = _r158_parse_dates_utc(series)
    return parsed[parsed.notna() & (parsed <= as_of)]


def _dates_in_label_window(series: pd.Series, as_of: pd.Timestamp, horizon_days: int) -> pd.Series:
    """THE single label-side convention: strictly (T, T + horizon]."""
    parsed = _r158_parse_dates_utc(series)
    end = as_of + pd.to_timedelta(int(horizon_days), unit="D")
    return parsed[parsed.notna() & (parsed > as_of) & (parsed <= end)]


def _resolve_as_of(as_of: Any) -> Optional[pd.Timestamp]:
    ts = pd.to_datetime(as_of, errors="coerce", utc=True)
    return None if pd.isna(ts) else ts


def escalation_event_dates(
    csone_df: Optional[pd.DataFrame],
    *,
    include_bems: bool = True,
) -> pd.Series:
    """Deduplicated escalation event stream: open dates of P1/P2 cases,
    optionally UNION BEMS-flagged cases.  One case = one event even when it
    is both P1 and BEMS (the research flagged double-counting this overlap
    as a hazard inflator).  Returns a Series of UTC timestamps (may be empty).

    Round 160 adversarial fix — ``include_bems``: BEMS designation is
    detected from MUTABLE, undated fields (Transaction ID / refs / free
    text).  At the export's current time that is valid state; backdated to a
    historical cutoff it leaks the future (a BEMS raise after T would change
    the event stream at T).  The BACKTEST therefore runs with
    ``include_bems=False`` — historical labels and features use dated P1/P2
    opens only — while production scoring at T=now keeps BEMS.
    """
    open_col = _first_col(csone_df, _CASE_OPEN_COLUMNS)
    if open_col is None:
        return pd.Series([], dtype="datetime64[ns, UTC]")
    sev_col = _first_col(csone_df, _CASE_SEVERITY_COLUMNS)
    if sev_col is not None:
        p12 = csone_df[sev_col].map(normalize_priority_label).isin(["P1", "P2"])
    else:
        p12 = pd.Series(False, index=csone_df.index)
    mask = p12
    if include_bems:
        try:
            bems = detect_bems_mask(csone_df)
            bems = bems.reindex(csone_df.index).fillna(False).astype(bool)
        except Exception:  # noqa: BLE001 - BEMS detection is additive
            bems = pd.Series(False, index=csone_df.index)
        mask = p12 | bems
    if not bool(mask.any()):
        return pd.Series([], dtype="datetime64[ns, UTC]")
    dates = _r158_parse_dates_utc(csone_df.loc[mask, open_col])
    return dates.dropna()


def _open_case_mask_asof(csone_df: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Point-in-time openness reconstructed from DATES ONLY: open iff
    open_date <= T and (no close date or close date > T).  Never reads a
    status column — current status is export-time state, not T-time state."""
    open_col = _first_col(csone_df, _CASE_OPEN_COLUMNS)
    if open_col is None:
        return pd.Series(False, index=csone_df.index)
    opened = _r158_parse_dates_utc(csone_df[open_col])
    close_col = _first_col(csone_df, _CASE_CLOSE_COLUMNS)
    if close_col is not None:
        closed = _r158_parse_dates_utc(csone_df[close_col])
        return opened.notna() & (opened <= as_of) & (closed.isna() | (closed > as_of))
    return opened.notna() & (opened <= as_of)


# ---------------------------------------------------------------------------
# Feature extraction (pure; one customer; strictly as-of T)
# ---------------------------------------------------------------------------
def snapshot_features(
    customer_frames: Mapping[str, Optional[pd.DataFrame]],
    as_of: Any,
    *,
    include_bems: bool = True,
) -> Optional[Dict[str, Any]]:
    """Compute the pre-registered feature set for ONE customer as of T.

    ``customer_frames`` keys: ``tac_cases``, ``adoption_barriers``,
    ``customer_pulse`` (missing/None frames are simply absent signals).
    Returns None when the customer has < MIN_HISTORY_DAYS of dated history
    before T (cold start: emit "insufficient history", never a confident
    low score).
    """
    T = _resolve_as_of(as_of)
    if T is None:
        return None
    tac = customer_frames.get("tac_cases")
    ab = customer_frames.get("adoption_barriers")
    pulse = customer_frames.get("customer_pulse")

    # --- history span gate (cold start) -----------------------------------
    earliest: Optional[pd.Timestamp] = None
    for df, cols in ((tac, _CASE_OPEN_COLUMNS), (ab, _BARRIER_OPEN_COLUMNS), (pulse, _PULSE_DATE_COLUMNS)):
        col = _first_col(df, cols)
        if col is None:
            continue
        dated = _dates_asof(df[col], T)
        if not dated.empty:
            first = dated.min()
            earliest = first if earliest is None else min(earliest, first)
    if earliest is None or (T - earliest).days < MIN_HISTORY_DAYS:
        return None

    feats: Dict[str, Any] = {"as_of": T.isoformat()}

    # --- case velocity + acceleration -------------------------------------
    open_col = _first_col(tac, _CASE_OPEN_COLUMNS)
    if open_col is not None:
        opened = _dates_asof(tac[open_col], T)
        d30 = T - pd.Timedelta(days=30)
        d60 = T - pd.Timedelta(days=60)
        d90 = T - pd.Timedelta(days=90)
        n30 = int((opened > d30).sum())
        n31_60 = int(((opened > d60) & (opened <= d30)).sum())
        n90 = int((opened > d90).sum())
        baseline_monthly = n90 / 3.0
        feats["cases_opened_30d"] = n30
        feats["velocity_ratio"] = round(n30 / max(baseline_monthly, 1.0), 3)
        feats["velocity_accelerating"] = bool(n30 - n31_60 > 0 and n30 >= 3)
    else:
        feats["cases_opened_30d"] = 0
        feats["velocity_ratio"] = 0.0
        feats["velocity_accelerating"] = False

    # --- escalation recency + windowed counts ------------------------------
    events = escalation_event_dates(tac, include_bems=include_bems)
    events = events[events <= T]
    if events.empty:
        feats["days_since_last_escalation"] = None
        feats["escalations_90d"] = 0
    else:
        feats["days_since_last_escalation"] = int((T - events.max()).days)
        feats["escalations_90d"] = int((events > T - pd.Timedelta(days=90)).sum())

    # --- severity-mix shift -------------------------------------------------
    sev_col = _first_col(tac, _CASE_SEVERITY_COLUMNS)
    feats["new_p12_14d"] = 0
    feats["p12_share_shift"] = False
    if open_col is not None and sev_col is not None:
        opened_all = _r158_parse_dates_utc(tac[open_col])
        p12_mask = tac[sev_col].map(normalize_priority_label).isin(["P1", "P2"])
        in14 = opened_all.notna() & (opened_all > T - pd.Timedelta(days=14)) & (opened_all <= T)
        feats["new_p12_14d"] = int((in14 & p12_mask).sum())
        in30 = opened_all.notna() & (opened_all > T - pd.Timedelta(days=30)) & (opened_all <= T)
        prior90 = opened_all.notna() & (opened_all > T - pd.Timedelta(days=120)) & (opened_all <= T - pd.Timedelta(days=30))
        n_in30 = int(in30.sum())
        if n_in30 >= 2 and int(prior90.sum()) > 0:
            share30 = float((in30 & p12_mask).sum()) / n_in30
            share_prior = float((prior90 & p12_mask).sum()) / int(prior90.sum())
            feats["p12_share_shift"] = bool(share_prior > 0 and share30 > 1.5 * share_prior) or (
                share_prior == 0 and share30 > 0
            )

    # --- severity-weighted open backlog + aging (date-reconstructed) --------
    feats["weighted_open_backlog"] = 0.0
    feats["oldest_open_p12_age_days"] = None
    if isinstance(tac, pd.DataFrame) and not tac.empty and open_col is not None:
        open_mask = _open_case_mask_asof(tac, T)
        if bool(open_mask.any()):
            weights = {"P1": 8.0, "P2": 5.0, "P3": 2.0, "P4": 1.0}
            if sev_col is not None:
                sev_norm = tac[sev_col].map(normalize_priority_label)
            else:
                sev_norm = pd.Series("", index=tac.index)
            feats["weighted_open_backlog"] = float(
                sum(weights.get(s, 1.0) for s in sev_norm[open_mask])
            )
            p12_open = open_mask & sev_norm.isin(["P1", "P2"])
            if bool(p12_open.any()):
                opened_dates = _r158_parse_dates_utc(tac[open_col])
                oldest = opened_dates[p12_open].min()
                if pd.notna(oldest):
                    feats["oldest_open_p12_age_days"] = int((T - oldest).days)

    # --- pulse trajectory ----------------------------------------------------
    feats["pulse_last"] = None
    feats["pulse_declines"] = 0
    feats["pulse_stale"] = True
    pdate_col = _first_col(pulse, _PULSE_DATE_COLUMNS)
    pscore_col = _first_col(pulse, _PULSE_SCORE_COLUMNS)
    if pdate_col is not None and pscore_col is not None:
        pdates = _r158_parse_dates_utc(pulse[pdate_col])
        pvals = pd.to_numeric(pulse[pscore_col], errors="coerce")
        ok = pdates.notna() & pvals.notna() & (pdates <= T)
        if bool(ok.any()):
            ordered = pvals[ok].iloc[np.argsort(pdates[ok].values, kind="stable")]
            feats["pulse_last"] = float(ordered.iloc[-1])
            feats["pulse_stale"] = bool((T - pdates[ok].max()).days > 90)
            declines = 0
            vals = ordered.tolist()
            for prev, nxt in zip(vals[:-1], vals[1:]):
                declines = declines + 1 if nxt < prev else 0
            feats["pulse_declines"] = int(min(declines, 2))

    # --- barrier open-events in trailing windows (dated events only) --------
    b_open_col = _first_col(ab, _BARRIER_OPEN_COLUMNS)
    feats["barriers_opened_90d"] = 0
    feats["critical_barrier_opened_90d"] = False
    if b_open_col is not None:
        b_dates = _r158_parse_dates_utc(ab[b_open_col])
        b_in90 = b_dates.notna() & (b_dates > T - pd.Timedelta(days=90)) & (b_dates <= T)
        feats["barriers_opened_90d"] = int(b_in90.sum())
        b_sev_col = _first_col(ab, _BARRIER_SEVERITY_COLUMNS)
        if b_sev_col is not None and bool(b_in90.any()):
            sev_text = ab.loc[b_in90, b_sev_col].fillna("").astype(str).str.casefold()
            feats["critical_barrier_opened_90d"] = bool(
                sev_text.str.startswith(("critical", "high")).any()
            )

    # --- same-tech compounding (dated case opens x dated barrier opens) -----
    feats["compound_tech_flag"] = False
    tac_tech_col = _first_col(tac, _TECH_COLUMNS)
    ab_tech_col = _first_col(ab, _TECH_COLUMNS)
    if tac_tech_col is not None and ab_tech_col is not None and open_col is not None and b_open_col is not None:
        opened_all = _r158_parse_dates_utc(tac[open_col])
        c60 = opened_all.notna() & (opened_all > T - pd.Timedelta(days=60)) & (opened_all <= T)
        b_dates = _r158_parse_dates_utc(ab[b_open_col])
        b120 = b_dates.notna() & (b_dates > T - pd.Timedelta(days=120)) & (b_dates <= T)
        if bool(c60.any()) and bool(b120.any()):
            case_techs = (
                tac.loc[c60, tac_tech_col].fillna("").astype(str).str.strip().str.casefold()
            )
            case_tech_counts = case_techs[case_techs != ""].value_counts()
            barrier_techs = set(
                ab.loc[b120, ab_tech_col].fillna("").astype(str).str.strip().str.casefold()
            ) - {""}
            feats["compound_tech_flag"] = any(
                tech in barrier_techs and count >= 2
                for tech, count in case_tech_counts.items()
            )
    return feats


# ---------------------------------------------------------------------------
# Scorecard (pre-registered bins & prior points; PDO-scaled when refit)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScorecardSpec:
    """Pre-registered scorecard: fixed bins, prior points, tier thresholds.

    The prior points encode the research evidence ordering (escalation
    recency dominates; trend beats level; sentiment trajectory over level)
    and are explicitly UNCALIBRATED until a live backtest derives banded
    observed rates.  6 feature families + 1 interaction — deliberately far
    below the freely-fitted-model threshold for this portfolio size (van
    Smeden 2019 / Riley pmsampsize: constrain hard, don't pretend to fit).
    """

    tier_thresholds: Tuple[Tuple[str, int], ...] = (
        ("CRITICAL_WATCH", 70),
        ("ELEVATED", 45),
        ("MODERATE", 22),
        ("LOW", 0),
    )
    version: str = "r160-prior-1"


def score_snapshot(features: Mapping[str, Any], spec: ScorecardSpec = ScorecardSpec()) -> Dict[str, Any]:
    """Additive points with named contributors.  Deterministic; every point
    traceable to a feature bin.  Monotone by construction: a worse bin never
    carries fewer points within its feature."""
    contributors: List[Tuple[str, int]] = []

    def add(label: str, points: int) -> None:
        if points > 0:
            contributors.append((label, int(points)))

    dsl = features.get("days_since_last_escalation")
    if dsl is not None:
        if dsl <= 30:
            add("escalation in last 30d", 24)
        elif dsl <= 90:
            add("escalation in last 31-90d", 16)
        elif dsl <= 180:
            add("escalation in last 91-180d", 8)
    esc90 = int(features.get("escalations_90d") or 0)
    if esc90 >= 4:
        add("4+ escalations in 90d", 20)
    elif esc90 >= 2:
        add("2-3 escalations in 90d", 12)
    elif esc90 == 1:
        add("1 escalation in 90d", 6)

    vr = float(features.get("velocity_ratio") or 0.0)
    n30 = int(features.get("cases_opened_30d") or 0)
    if vr > 3.0 and n30 >= 3:
        add("case velocity >3x baseline", 14)
    elif vr > 1.5 and n30 >= 2:
        add("case velocity >1.5x baseline", 8)
    elif vr > 0.75 and n30 >= 1:
        add("case velocity near baseline", 2)
    if features.get("velocity_accelerating"):
        add("case volume accelerating", 4)

    if int(features.get("new_p12_14d") or 0) > 0:
        add("new P1/P2 opened in last 14d", 10)
    if features.get("p12_share_shift"):
        add("P1/P2 share rising vs baseline", 6)

    backlog = float(features.get("weighted_open_backlog") or 0.0)
    if backlog > 15:
        add("heavy open-case backlog", 12)
    elif backlog >= 8:
        add("elevated open-case backlog", 8)
    elif backlog >= 1:
        add("open-case backlog", 3)
    oldest = features.get("oldest_open_p12_age_days")
    if oldest is not None and oldest > 30:
        add("open P1/P2 older than 30d", 6)

    pulse_last = features.get("pulse_last")
    if not features.get("pulse_stale", True) and pulse_last is not None:
        if float(pulse_last) <= _PULSE_NEGATIVE_THRESHOLD:
            add("latest pulse negative", 8)
        declines = int(features.get("pulse_declines") or 0)
        if declines >= 2:
            add("two consecutive pulse declines", 8)
        elif declines == 1:
            add("pulse declining", 4)

    b90 = int(features.get("barriers_opened_90d") or 0)
    if b90 >= 2:
        add("2+ barriers opened in 90d", 8)
    elif b90 == 1:
        add("barrier opened in 90d", 4)
    if features.get("critical_barrier_opened_90d"):
        add("critical/high barrier opened in 90d", 4)

    if features.get("compound_tech_flag"):
        add("barrier + case cluster on same technology", 10)

    total = int(sum(p for _, p in contributors))
    tier = next(
        (name for name, threshold in spec.tier_thresholds if total >= threshold),
        "LOW",
    )
    return {
        "points": total,
        "tier": tier,
        "contributors": contributors,
        "spec_version": spec.version,
    }


# ---------------------------------------------------------------------------
# Person-period table (fight-churn Listing 4.4 pattern; MIT, (c) Carl Gold)
# ---------------------------------------------------------------------------
def build_person_period_table(
    customers: Mapping[str, Mapping[str, Optional[pd.DataFrame]]],
    *,
    horizon_days: int = PREDICTIVE_HORIZON_DAYS,
    stride_days: int = 7,
    data_end: Any = None,
) -> pd.DataFrame:
    """Snapshot grid across all customers: one row per (customer, cutoff T)
    with features, label, and exclusion reason codes.

    Discipline (research-mandated, hardened by the Round 160 adversarial
    review):
    * label = 1 iff a NEW P1/P2 case OPEN is dated in (T, T + horizon] —
      same convention functions as production scoring.  BEMS is EXCLUDED
      from historical labels/features (``include_bems=False``): BEMS
      designation lives in mutable, undated fields, so backdating it to a
      historical cutoff would leak the future.
    * KNOWN APPROXIMATION (disclosed, not hidden): the export records each
      case's CURRENT severity, not its severity at open.  A case opened at
      P3 and later upgraded to P1 is treated as a P1 open at its open date.
      A snapshot export cannot do better; the live round should quantify
      the upgrade share from CSOne case history if available.
    * cutoffs within `horizon` of ``data_end`` are excluded (censored) —
      their labels are unknowable, and labeling them 0 miscalibrates.
      Callers SHOULD pass the export timestamp as ``data_end``; when
      omitted it falls back to the max record date across the portfolio,
      which a single future-dated typo can poison — the backtest audit
      discloses which source was used.
    * cutoffs where the customer already has an open P1/P2 at T (dates
      reconstructed) are excluded (already_escalated) — predicting an
      ongoing fire is not early warning.
    * cold-start cutoffs (insufficient history) are excluded with a reason.
    Excluded rows are RETAINED with ``excluded_reason`` set, so the audit
    artifact can disclose exactly what was left out and why.
    """
    all_rows: List[Dict[str, Any]] = []
    global_end = _resolve_as_of(data_end)
    if global_end is None:
        max_dates: List[pd.Timestamp] = []
        for frames in customers.values():
            for key, cols in (
                ("tac_cases", _CASE_OPEN_COLUMNS),
                ("adoption_barriers", _BARRIER_OPEN_COLUMNS),
                ("customer_pulse", _PULSE_DATE_COLUMNS),
            ):
                col = _first_col(frames.get(key), cols)
                if col is not None:
                    parsed = _r158_parse_dates_utc(frames[key][col]).dropna()
                    if not parsed.empty:
                        max_dates.append(parsed.max())
        if not max_dates:
            return pd.DataFrame()
        global_end = max(max_dates)

    for customer, frames in sorted(customers.items()):
        tac = frames.get("tac_cases")
        first_date = None  # type: Optional[pd.Timestamp]
        for key, cols in (
            ("tac_cases", _CASE_OPEN_COLUMNS),
            ("adoption_barriers", _BARRIER_OPEN_COLUMNS),
            ("customer_pulse", _PULSE_DATE_COLUMNS),
        ):
            col = _first_col(frames.get(key), cols)
            if col is not None:
                parsed = _r158_parse_dates_utc(frames[key][col]).dropna()
                if not parsed.empty:
                    candidate = parsed.min()
                    first_date = candidate if first_date is None else min(first_date, candidate)
        if first_date is None:
            continue
        # Backtest mode: dated P1/P2 opens only (BEMS excluded — see docstring)
        events = escalation_event_dates(tac, include_bems=False)
        T = first_date + pd.Timedelta(days=MIN_HISTORY_DAYS)
        while T <= global_end:
            row: Dict[str, Any] = {"customer": customer, "as_of": T}
            if (global_end - T).days < horizon_days:
                row["excluded_reason"] = "censored_window"
                row["label"] = None
            else:
                open_col = _first_col(tac, _CASE_OPEN_COLUMNS)
                sev_col = _first_col(tac, _CASE_SEVERITY_COLUMNS)
                already = False
                if open_col is not None and sev_col is not None:
                    open_mask = _open_case_mask_asof(tac, T)
                    p12 = tac[sev_col].map(normalize_priority_label).isin(["P1", "P2"])
                    already = bool((open_mask & p12).any())
                if already:
                    row["excluded_reason"] = "already_escalated_at_T"
                    row["label"] = None
                else:
                    label_events = _dates_in_label_window(
                        pd.Series(events.values), T, horizon_days
                    )
                    row["label"] = int(not label_events.empty)
                    feats = snapshot_features(frames, T, include_bems=False)
                    if feats is None:
                        row["excluded_reason"] = "insufficient_history"
                        row["label"] = None
                    else:
                        row["excluded_reason"] = ""
                        scored = score_snapshot(feats)
                        row.update({k: v for k, v in feats.items() if k != "as_of"})
                        row["points"] = scored["points"]
                        row["tier"] = scored["tier"]
            all_rows.append(row)
            T = T + pd.Timedelta(days=int(stride_days))
    return pd.DataFrame(all_rows)


# ---------------------------------------------------------------------------
# Small-sample honest statistics (pure numpy)
# ---------------------------------------------------------------------------
def wilson_interval(events: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """Wilson score interval (Brown, Cai & DasGupta 2001 recommendation)."""
    if n <= 0:
        return (0.0, 1.0)
    p = events / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def jeffreys_rate(events: int, n: int) -> float:
    """Jeffreys shrunk point estimate (x+0.5)/(n+1)."""
    return (events + 0.5) / (n + 1.0) if n >= 0 else 0.0


def prudent_upper_bound(n: int, confidence: float = 0.75) -> float:
    """Pluto & Tasche most-prudent estimate for a ZERO-event band: the
    one-sided upper confidence bound 1 - (1 - confidence)^(1/n), so a band
    that never escalated reports "could be up to X%" instead of 0%."""
    if n <= 0:
        return 1.0
    return 1.0 - (1.0 - confidence) ** (1.0 / n)


def pava_monotone(rates: Sequence[float], weights: Sequence[float]) -> List[float]:
    """Pool-adjacent-violators (Barlow et al. 1972): enforce non-decreasing
    rates across ordered score bands by weighted pooling."""
    blocks: List[List[float]] = [[float(r), float(w)] for r, w in zip(rates, weights)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:
            r1, w1 = blocks[i]
            r2, w2 = blocks[i + 1]
            merged = [(r1 * w1 + r2 * w2) / max(w1 + w2, 1e-12), w1 + w2]
            blocks[i : i + 2] = [merged]
            i = max(i - 1, 0)
        else:
            i += 1
    # Rebuild per-band expansion: walk blocks proportionally to input weights
    expanded: List[float] = []
    block_iter = iter(blocks)
    current = next(block_iter)
    remaining = current[1]
    for w in weights:
        while remaining <= 1e-12:
            current = next(block_iter)
            remaining = current[1]
        expanded.append(current[0])
        remaining -= float(w)
    return expanded


def calibration_claim_level(total_events: int) -> str:
    """The honesty ladder (research: <10 events no numeric claims; 10-29
    low-confidence with intervals; >=30 normal with intervals)."""
    if total_events < 10:
        return "insufficient_history"
    if total_events < 30:
        return "low_confidence"
    return "normal"


def banded_calibration_table(
    points: Sequence[float],
    labels: Sequence[int],
    spec: ScorecardSpec = ScorecardSpec(),
) -> List[Dict[str, Any]]:
    """Observed escalation rate per tier band with Jeffreys smoothing,
    Wilson intervals, zero-event prudent upper bounds, and PAVA
    monotonicity — the score→probability mapping earned from history."""
    pts = np.asarray(list(points), dtype=float)
    lab = np.asarray(list(labels), dtype=float)
    bands: List[Dict[str, Any]] = []
    ordered = list(reversed(spec.tier_thresholds))  # LOW..CRITICAL_WATCH ascending
    for i, (name, low) in enumerate(ordered):
        high = ordered[i + 1][1] if i + 1 < len(ordered) else None
        mask = (pts >= low) if high is None else ((pts >= low) & (pts < high))
        n = int(mask.sum())
        events = int(lab[mask].sum()) if n else 0
        lo, hi = wilson_interval(events, n)
        bands.append(
            {
                "band": name,
                "min_points": int(low),
                "n": n,
                "events": events,
                "rate_smoothed": round(jeffreys_rate(events, n), 4) if n else None,
                "wilson_low": round(lo, 4) if n else None,
                "wilson_high": round(hi, 4) if n else None,
                "zero_event_upper_bound": (
                    round(prudent_upper_bound(n), 4) if n and events == 0 else None
                ),
            }
        )
    populated = [b for b in bands if b["n"] > 0]
    if len(populated) >= 2:
        mono = pava_monotone(
            [b["rate_smoothed"] for b in populated],
            [b["n"] for b in populated],
        )
        for b, rate in zip(populated, mono):
            b["rate_monotone"] = round(float(rate), 4)
    return bands


def population_stability_index(expected: Sequence[float], actual: Sequence[float], spec: ScorecardSpec = ScorecardSpec()) -> float:
    """PSI over tier bands (scorecardpy formula).  <0.1 stable, 0.1-0.25
    investigate, >0.25 recalibrate."""
    exp = np.asarray(list(expected), dtype=float)
    act = np.asarray(list(actual), dtype=float)
    edges = sorted(t for _, t in spec.tier_thresholds)
    def _shares(arr: np.ndarray) -> np.ndarray:
        counts = []
        bounds = edges + [float("inf")]
        for lo, hi in zip(bounds[:-1], bounds[1:]):
            counts.append(((arr >= lo) & (arr < hi)).sum())
        shares = np.asarray(counts, dtype=float)
        shares = np.clip(shares / max(shares.sum(), 1.0), 1e-4, None)
        return shares / shares.sum()
    e, a = _shares(exp), _shares(act)
    return float(np.sum((a - e) * np.log(a / e)))


# ---------------------------------------------------------------------------
# Production API
# ---------------------------------------------------------------------------
def predictive_source_coverage(
    customer_frames: Mapping[str, Optional[pd.DataFrame]],
    *,
    source_states: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Classify whether the escalation scorecard has complete evidence.

    ``zero`` means the source was successfully queried and returned no rows;
    it is therefore complete evidence and is never conflated with a failed or
    missing feed.  TAC is the forecast target/history source, so a failed or
    unavailable TAC feed blocks forecasting.  Retained partial/stale evidence
    from any source can still produce a *lower-bound relative signal*, never a
    full-coverage or calibrated forecast.

    ``source_states`` is an optional compatibility seam for callers that hold
    source state separately from a sliced DataFrame.  Omitted callers retain
    the original frame-only API and state is derived from canonical attrs.
    """

    overrides = source_states or {}
    resolved_states: Dict[str, str] = {}
    source_details: Dict[str, str] = {}
    for key in _PREDICTIVE_SOURCE_KEYS:
        override = str(overrides.get(key) or "").strip().casefold()
        if override in _COMPLETE_SOURCE_STATES | _INCOMPLETE_SOURCE_STATES:
            state = override
            detail = f"source state supplied by caller: {state}"
        else:
            metadata = source_data_state(customer_frames.get(key))
            state = str(metadata.get("state") or "unavailable").strip().casefold()
            if state not in _COMPLETE_SOURCE_STATES | _INCOMPLETE_SOURCE_STATES:
                state = "unavailable"
            detail = str(metadata.get("detail") or state)
        resolved_states[key] = state
        source_details[key] = detail

    target_state = resolved_states["tac_cases"]
    missing_sources = [
        key for key in _PREDICTIVE_SOURCE_KEYS if resolved_states[key] not in _COMPLETE_SOURCE_STATES
    ]
    unavailable_sources = [
        key
        for key in _PREDICTIVE_SOURCE_KEYS
        if resolved_states[key] in {"failed", "unavailable"}
    ]
    degraded_sources = [
        key
        for key in _PREDICTIVE_SOURCE_KEYS
        if resolved_states[key] in {"partial", "stale"}
    ]
    forecast_available = target_state not in {"failed", "unavailable"}
    if not forecast_available:
        coverage_state = "unavailable"
        relative_signal_state = "unavailable"
    elif missing_sources:
        coverage_state = "partial"
        relative_signal_state = "lower_bound_relative_signal"
    else:
        coverage_state = "available"
        relative_signal_state = "full_relative_signal"

    if coverage_state == "available":
        detail = "All predictive sources have complete available/zero coverage."
    elif coverage_state == "unavailable":
        detail = (
            "Forecast unavailable because TAC coverage is "
            f"{target_state}: {source_details['tac_cases']}"
        )
    else:
        detail = "Lower-bound relative signal; incomplete predictive sources: " + ", ".join(
            f"{key}={resolved_states[key]}" for key in missing_sources
        )

    return {
        "coverage_state": coverage_state,
        "forecast_available": forecast_available,
        "relative_signal_state": relative_signal_state,
        "source_states": resolved_states,
        "source_details": source_details,
        # A source is listed here whenever *complete* evidence is missing,
        # including partial/stale retained rows.  This makes omission explicit
        # while the more specific lists preserve the reason.
        "missing_sources": missing_sources,
        "unavailable_sources": unavailable_sources,
        "degraded_sources": degraded_sources,
        "complete_sources": [
            key for key in _PREDICTIVE_SOURCE_KEYS if key not in missing_sources
        ],
        "detail": detail,
    }


def load_calibration_artifact(
    configured_path: Optional[os.PathLike[str] | str] = None,
) -> Optional[Dict[str, Any]]:
    """Load one explicitly configured, live-source calibration artifact.

    There is deliberately no working-directory search or bundled-fixture
    fallback.  The caller must pass an absolute JSON path or configure
    ``ADOPTIQ_PREDICTIVE_CALIBRATION_PATH``.  Missing, invalid, oversized,
    symlinked, or non-live artifacts fail soft to ``None`` so production falls
    back to the transparent uncalibrated relative scorecard.
    """

    raw_path = configured_path
    if raw_path is None:
        raw_path = os.getenv(PREDICTIVE_CALIBRATION_PATH_ENV, "")
    token = str(raw_path or "").strip()
    if not token:
        return None
    try:
        candidate = Path(token).expanduser()
        if not candidate.is_absolute() or candidate.suffix.casefold() != ".json":
            return None
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.stat().st_size > _MAX_CALIBRATION_BYTES:
            return None
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("derived_from") or "") != "live_cisco_sources":
        return None
    if str(payload.get("claim_level") or "") not in {
        "insufficient_history",
        "low_confidence",
        "normal",
    }:
        return None
    bands = payload.get("bands")
    if not isinstance(bands, list) or not all(isinstance(item, dict) for item in bands):
        return None
    return payload


def escalation_outlook(
    customer_frames: Mapping[str, Optional[pd.DataFrame]],
    as_of: Any,
    *,
    spec: ScorecardSpec = ScorecardSpec(),
    calibration: Optional[Mapping[str, Any]] = None,
    calibration_path: Optional[os.PathLike[str] | str] = None,
    source_states: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """The production entry point: tier + contributors + honest claim state
    for one customer as of now.  Returns None on cold start (insufficient
    history) — the caller renders "insufficient history", never a
    confident LOW.

    ``calibration``: optional artifact produced by the live backtest
    (scripts/backtest_escalation_forecast.py).  Absent → the outlook is a
    RELATIVE ranking ("uncalibrated_prior") and must not be phrased as a
    probability.  Present → the matching band's observed rate and interval
    are attached, quotable as "X of N historical customer-periods like
    this escalated within 30 days".
    """
    coverage = predictive_source_coverage(
        customer_frames,
        source_states=source_states,
    )
    if not coverage["forecast_available"]:
        return None

    feats = snapshot_features(customer_frames, as_of)
    if feats is None:
        return None
    scored = score_snapshot(feats, spec)
    # Round 160 adversarial fix: contributors are the LARGEST drivers, not
    # the first three in feature-family code order (which silently dropped
    # e.g. the compound-tech interaction whenever three earlier families
    # fired).
    _top_contributors = sorted(scored["contributors"], key=lambda t: (-t[1], t[0]))[:3]
    result: Dict[str, Any] = {
        "tier": scored["tier"],
        "points": scored["points"],
        "contributors": _top_contributors,
        "horizon_days": PREDICTIVE_HORIZON_DAYS,
        "calibration_state": "uncalibrated_prior",
        "spec_version": scored["spec_version"],
        "coverage_state": coverage["coverage_state"],
        "relative_signal_state": coverage["relative_signal_state"],
        "source_states": coverage["source_states"],
        "source_details": coverage["source_details"],
        "missing_sources": coverage["missing_sources"],
        "unavailable_sources": coverage["unavailable_sources"],
        "degraded_sources": coverage["degraded_sources"],
        "coverage_detail": coverage["detail"],
    }
    effective_calibration: Optional[Mapping[str, Any]] = calibration
    if effective_calibration is None:
        effective_calibration = load_calibration_artifact(calibration_path)
    if effective_calibration and coverage["coverage_state"] == "available":
        bands = effective_calibration.get("bands") or []
        match = next((b for b in bands if b.get("band") == scored["tier"]), None)
        claim = str(effective_calibration.get("claim_level") or "")
        # Round 160 adversarial fix: provenance gate — an artifact minted
        # from the offline fixture (or any *_smoke run) must never be quoted
        # as historical evidence.  Only live-source artifacts calibrate.
        provenance = str(effective_calibration.get("derived_from") or "")
        provenance_ok = provenance == "live_cisco_sources"
        if provenance_ok and match and match.get("n") and claim in {"low_confidence", "normal"}:
            result["calibration_state"] = "calibrated"
            result["claim_level"] = claim
            result["observed_rate"] = match.get("rate_monotone", match.get("rate_smoothed"))
            result["observed_events"] = match.get("events")
            result["observed_n"] = match.get("n")
            result["wilson_low"] = match.get("wilson_low")
            result["wilson_high"] = match.get("wilson_high")
    elif coverage["coverage_state"] == "partial":
        result["calibration_state"] = "withheld_incomplete_coverage"
    return result
