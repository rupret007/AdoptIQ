"""Round 160 — time-travel backtest for the predictive escalation scorecard.

Replays history: for every customer and every cutoff T on a deterministic
grid, scores the customer using ONLY records dated <= T, then checks whether
a NEW P1/P2/BEMS escalation actually opened in (T, T+30].  Produces the
evidence that earns (or denies) the report's right to quote probabilities:

* banded calibration table (Jeffreys-smoothed rates, Wilson 95% intervals,
  Pluto-Tasche upper bounds for zero-event bands, PAVA monotone),
* lift@top-20% and per-band precision/recall on the NON-OVERLAPPING monthly
  cutoff subset (headline) with the weekly grid as secondary,
* event-level recall ("was the customer flagged at the last cutoff before
  the escalation?") separated from snapshot-level precision,
* mandatory baselines: base rate, the trivial "any escalation in trailing
  90d" heuristic, and the existing deterministic 0-100 risk score as a
  ranker (legacy AND magnitude_first profiles) — if the scorecard cannot
  beat these, the report says so,
* an audit block: cutoffs used, exclusions by reason code, unique events.

Deterministic end to end: no RNG, no fitted continuous curves.  Run it on
the offline fixture as a smoke test (tiny, degenerate — never quote its
numbers) and on live exports for the real calibration artifact.

Usage:
  python scripts/backtest_escalation_forecast.py --fixture tests/fixtures/report_acceptance/v1/sanitized_portfolio.json --output-dir .tmp/backtest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd


def _ensure_repo_root() -> None:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_repo_root()

from predictive_signals import (  # noqa: E402
    PREDICTIVE_HORIZON_DAYS,
    ScorecardSpec,
    banded_calibration_table,
    build_person_period_table,
    calibration_claim_level,
    escalation_event_dates,
    wilson_interval,
)


def _customers_from_fixture(payload: Mapping[str, Any]) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Regroup the sanitized fixture's member->dataset rows per customer."""
    per_customer: Dict[str, Dict[str, List[dict]]] = {}
    key_map = {
        "tac_cases": "tac_cases",
        "adoption_barriers": "adoption_barriers",
        "customer_pulse": "customer_pulse",
    }
    for _member, datasets in (payload.get("team_data") or {}).items():
        for src_key, dst_key in key_map.items():
            for row in datasets.get(src_key) or []:
                name = str(
                    row.get("BU_NAME") or row.get("Customer") or row.get("customer_name") or ""
                ).strip()
                if not name:
                    continue
                per_customer.setdefault(name, {}).setdefault(dst_key, []).append(dict(row))
    return {
        cust: {k: pd.DataFrame(rows) for k, rows in datasets.items()}
        for cust, datasets in per_customer.items()
    }


def _auc_rank(scores: np.ndarray, labels: np.ndarray) -> Optional[float]:
    """Mann-Whitney rank AUC (secondary metric; PR/lift are the headline)."""
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="stable")
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    # average ties
    combined = np.concatenate([pos, neg])
    for value in np.unique(combined):
        mask = combined == value
        if mask.sum() > 1:
            ranks[mask] = ranks[mask].mean()
    r_pos = ranks[: len(pos)].sum()
    u = r_pos - len(pos) * (len(pos) + 1) / 2.0
    return float(u / (len(pos) * len(neg)))


def _lift_at_top(scores: np.ndarray, labels: np.ndarray, share: float = 0.2) -> Optional[Dict[str, Any]]:
    """Top-share lift (after fight-churn Listing 9.2 calc_lift; MIT,
    (c) 2020 Carl Gold — logic re-implemented)."""
    n = len(scores)
    if n == 0 or labels.sum() == 0:
        return None
    k = max(int(round(n * share)), 1)
    order = np.argsort(-scores, kind="stable")
    top = labels[order[:k]]
    base = labels.mean()
    top_rate = top.mean()
    return {
        "k": int(k),
        "share": share,
        "top_rate": round(float(top_rate), 4),
        "base_rate": round(float(base), 4),
        "lift": round(float(top_rate / base), 3) if base > 0 else None,
        "events_captured": int(top.sum()),
        "events_total": int(labels.sum()),
    }


def _monthly_subset(table: pd.DataFrame, stride_days: int = 7) -> pd.DataFrame:
    """Non-overlapping cutoffs: subsample so consecutive kept cutoffs are
    >= the label horizon apart, eliminating label-window overlap.

    Round 160 adversarial fix: the step derives from the ACTUAL stride
    (ceil(horizon/stride) + 1 grid steps => spacing > horizon), so a
    --stride-days 1 run can no longer masquerade overlapping windows as the
    "monthly_nonoverlapping" headline grid."""
    import math as _math

    step = max(int(_math.ceil(PREDICTIVE_HORIZON_DAYS / max(int(stride_days), 1))) + 1, 1)
    kept = []
    for _cust, group in table.groupby("customer", sort=True):
        group = group.sort_values("as_of")
        kept.append(group.iloc[::step])
    return pd.concat(kept, ignore_index=True) if kept else table.iloc[0:0]


def _band_metrics(table: pd.DataFrame, flag_tiers: List[str]) -> Dict[str, Any]:
    flagged = table["tier"].isin(flag_tiers)
    labels = table["label"].astype(int)
    tp = int((flagged & (labels == 1)).sum())
    fp = int((flagged & (labels == 0)).sum())
    fn = int((~flagged & (labels == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    out: Dict[str, Any] = {
        "flag_tiers": flag_tiers,
        "flagged_n": int(flagged.sum()),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
    }
    if (tp + fp) > 0:
        lo, hi = wilson_interval(tp, tp + fp)
        out["precision_wilson"] = [round(lo, 4), round(hi, 4)]
    return out


def _event_level_recall(table: pd.DataFrame, flag_tiers: List[str]) -> Dict[str, Any]:
    """Each unique escalation counted ONCE, evaluated at TWO honest moments.

    Round 160 adversarial fix: positives are grouped into runs (consecutive
    positive cutoffs whose label windows share one underlying event) and the
    run's LAST row — the last evaluable cutoff before the event, where the
    most signal has accrued — backs ``events_caught_at_last_cutoff`` (the key
    previously computed the FIRST row, understating the named metric).  The
    FIRST row — the earliest warning, ~horizon days out and the harder
    criterion — is reported separately as ``events_caught_at_first_warning``.
    """
    caught_last = 0
    caught_first = 0
    events_total = 0
    for _cust, group in table.groupby("customer", sort=True):
        group = group.sort_values("as_of")
        pos = group[group["label"] == 1]
        if pos.empty:
            continue
        runs: List[List[pd.Series]] = []
        for _, row in pos.iterrows():
            if runs and (row["as_of"] - runs[-1][-1]["as_of"]).days < PREDICTIVE_HORIZON_DAYS:
                runs[-1].append(row)
            else:
                runs.append([row])
        for run in runs:
            events_total += 1
            if str(run[-1].get("tier")) in flag_tiers:
                caught_last += 1
            if str(run[0].get("tier")) in flag_tiers:
                caught_first += 1
    return {
        "events_total": events_total,
        "events_caught_at_last_cutoff": caught_last,
        "events_caught_at_first_warning": caught_first,
        "event_recall": round(caught_last / events_total, 4) if events_total else None,
        "early_warning_recall": round(caught_first / events_total, 4) if events_total else None,
    }


def _risk_score_baseline(
    customers: Mapping[str, Mapping[str, pd.DataFrame]],
    table: pd.DataFrame,
    profile: str,
) -> Optional[Dict[str, Any]]:
    """Rank by the existing deterministic 0-100 risk score at each cutoff
    (time-filtered frames) — the incumbent the scorecard must beat."""
    try:
        from predictive_signals import _dates_asof, _first_col, _CASE_OPEN_COLUMNS, _BARRIER_OPEN_COLUMNS, _PULSE_DATE_COLUMNS  # noqa: PLC0415
        from risk_scoring import compute_customer_risk_profile  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    scores: List[float] = []
    labels: List[int] = []
    date_cols = {
        "tac_cases": _CASE_OPEN_COLUMNS,
        "adoption_barriers": _BARRIER_OPEN_COLUMNS,
        "customer_pulse": _PULSE_DATE_COLUMNS,
    }
    for _, row in table.iterrows():
        frames = customers.get(row["customer"]) or {}
        t = row["as_of"]
        sliced: Dict[str, Optional[pd.DataFrame]] = {}
        for key, cols in date_cols.items():
            df = frames.get(key)
            col = _first_col(df, cols)
            if df is None or col is None:
                sliced[key] = None
                continue
            keep = _dates_asof(df[col], t)
            sliced[key] = df.loc[keep.index]
        try:
            prof = compute_customer_risk_profile(
                str(row["customer"]),
                customer_ab=sliced.get("adoption_barriers"),
                customer_csone=sliced.get("tac_cases"),
                customer_pulse=sliced.get("customer_pulse"),
                as_of=t,
                scoring_profile=profile,
            )
            scores.append(float(prof.get("risk_score_0_100") or 0.0))
            labels.append(int(row["label"]))
        except Exception:  # noqa: BLE001
            continue
    if not scores:
        return None
    arr_s = np.asarray(scores)
    arr_l = np.asarray(labels)
    return {
        "profile": profile,
        "auc": _auc_rank(arr_s, arr_l),
        "lift_top20": _lift_at_top(arr_s, arr_l),
    }


def run_backtest(
    customers: Mapping[str, Mapping[str, pd.DataFrame]],
    *,
    stride_days: int = 7,
    output_dir: Optional[Path] = None,
    label: str = "unlabeled_run",
    data_end: Any = None,
) -> Dict[str, Any]:
    """Round 160 adversarial fix — ``data_end``: pass the EXPORT timestamp.
    When omitted, the censoring anchor falls back to the max record date
    across the whole portfolio, which one future-dated typo can poison
    (silently un-censoring every customer and deflating base rates).  The
    audit block discloses which source was used so a derived anchor is
    never mistaken for an explicit one."""
    spec = ScorecardSpec()
    table = build_person_period_table(customers, stride_days=stride_days, data_end=data_end)
    if table.empty:
        return {
            "ok": False,
            "reason": (
                "no evaluable cutoffs — either no dated records, or the "
                f"history span is shorter than the {PREDICTIVE_HORIZON_DAYS}-day "
                "label horizon plus the 60-day cold-start floor. A real "
                "90-365 day export is required; this is expected on the "
                "offline fixture (its history spans ~1 month)."
            ),
        }
    evaluable = table[(table["excluded_reason"] == "") & table["label"].notna()].copy()
    audit = {
        "run_label": label,
        "customers": int(table["customer"].nunique()),
        "cutoffs_total": int(len(table)),
        "cutoffs_evaluable": int(len(evaluable)),
        "exclusions": table[table["excluded_reason"] != ""]["excluded_reason"].value_counts().to_dict(),
        "horizon_days": PREDICTIVE_HORIZON_DAYS,
        "stride_days": stride_days,
        "spec_version": spec.version,
        # Round 160 adversarial disclosures:
        "data_end_source": "explicit" if data_end is not None else "derived_max_record_date",
        "data_end": str(pd.to_datetime(data_end, utc=True)) if data_end is not None else None,
        "label_definition": "new P1/P2 case open in (T, T+30]; BEMS excluded from historical labels (undated mutable refs)",
        "known_approximations": [
            "case severity is export-time state applied to the open date; a post-open upgrade is backdated (quantify upgrade share from CSOne case history in the live round)",
            "survivorship: a current-customers export cannot see already-churned accounts",
        ],
    }
    if evaluable.empty:
        return {"ok": False, "reason": "no evaluable cutoffs", "audit": audit}

    monthly = _monthly_subset(evaluable, stride_days=stride_days)
    m_scores = monthly["points"].to_numpy(dtype=float)
    m_labels = monthly["label"].to_numpy(dtype=int)
    unique_events = _event_level_recall(evaluable, ["ELEVATED", "CRITICAL_WATCH"])
    total_events_monthly = int(m_labels.sum())
    claim = calibration_claim_level(unique_events["events_total"])

    result: Dict[str, Any] = {
        "ok": True,
        "audit": audit,
        "claim_level": claim,
        "headline_grid": "monthly_nonoverlapping",
        "monthly": {
            "n": int(len(monthly)),
            "events": total_events_monthly,
            "base_rate": round(float(m_labels.mean()), 4) if len(m_labels) else None,
            "auc_secondary": _auc_rank(m_scores, m_labels),
            "lift_top20": _lift_at_top(m_scores, m_labels),
            "flag_metrics": _band_metrics(monthly, ["ELEVATED", "CRITICAL_WATCH"]),
        },
        "event_level": unique_events,
        "bands": banded_calibration_table(m_scores, m_labels, spec),
        "weekly_secondary_note": (
            "weekly grid overlaps 30d label windows; metrics there are "
            "optimistic — headline numbers use the monthly subset"
        ),
        "baselines": {},
    }
    # Baselines: trivial heuristic + existing risk score (both profiles).
    # Round 160 adversarial fix: the heuristic runs on the SAME monthly
    # non-overlapping grid as the scorecard headline — cross-grid comparison
    # was apples-to-oranges.
    heur_scores = monthly["escalations_90d"].fillna(0).to_numpy(dtype=float)
    heur_labels = monthly["label"].to_numpy(dtype=int)
    result["baselines"]["trailing_90d_escalation_heuristic"] = {
        "grid": "monthly_nonoverlapping",
        "auc": _auc_rank(heur_scores, heur_labels),
        "lift_top20": _lift_at_top(heur_scores, heur_labels),
    }
    for profile in ("legacy", "magnitude_first"):
        baseline = _risk_score_baseline(customers, monthly, profile)
        if baseline:
            result["baselines"][f"risk_score_{profile}"] = baseline

    if claim == "insufficient_history":
        result["calibration_artifact"] = None
        result["honesty_note"] = (
            f"only {unique_events['events_total']} unique escalation events — "
            "numeric probability claims are suppressed; directional ranking only"
        )
    else:
        result["calibration_artifact"] = {
            "claim_level": claim,
            "bands": result["bands"],
            "derived_from": label,
            "spec_version": spec.version,
        }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "backtest_result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True, help="sanitized portfolio JSON")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--stride-days", type=int, default=7)
    parser.add_argument(
        "--data-end",
        default=None,
        help="EXPORT timestamp (ISO). Strongly recommended: anchors censoring; "
        "without it the max record date is used, which one future-dated typo "
        "can poison (the audit block discloses which source was used).",
    )
    args = parser.parse_args(argv)
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    customers = _customers_from_fixture(payload)
    label = "offline_fixture_smoke" if payload.get("sanitized") else "live_cisco_sources"
    result = run_backtest(
        customers,
        stride_days=args.stride_days,
        output_dir=args.output_dir,
        label=label,
        data_end=args.data_end,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
