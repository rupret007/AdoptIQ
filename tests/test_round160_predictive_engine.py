"""Round 160 — predictive escalation engine: the accuracy contract.

The engine's claims are only as good as these guarantees:

1. **No future leakage** — a record dated after T can never change features
   at T (the single property that separates honest backtests from fiction).
2. **One boundary convention** — records dated exactly T belong to features,
   the label window is strictly (T, T+30].
3. **Honest exclusions** — censored cutoffs, already-escalated customers,
   and cold starts are excluded WITH reason codes, never silently labeled.
4. **Deterministic, monotone scoring** — same input same output; a worse
   signal never lowers points; contributors sum to the total.
5. **Honest statistics** — Wilson/Jeffreys/PAVA/prudent-upper-bound match
   known values; the claim ladder suppresses numbers below 10 events.
6. **Honest surfaces** — the report paragraph and Ask AI lines state the
   calibration state; uncalibrated output is a ranking, never a probability.
"""

from __future__ import annotations

import pandas as pd
import pytest

import predictive_signals as ps

AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_RISK_SCORING_PROFILE", raising=False)
    yield


def _tac(*rows):
    return pd.DataFrame(list(rows))


def _base_history():
    """A customer with ~5 months of history before AS_OF."""
    return {
        "tac_cases": _tac(
            {"Date/Time Opened": "2026-03-10", "Severity": "P3", "Date/Time Closed": "2026-03-25"},
            {"Date/Time Opened": "2026-06-15", "Severity": "P2", "Date/Time Closed": "2026-07-01"},
            {"Date/Time Opened": "2026-07-25", "Severity": "P1"},
        ),
        "adoption_barriers": pd.DataFrame(
            [{"OPEN_DATE_C": "2026-06-01", "SEVERITY_C": "High", "sub_technology": "Webex Calling"}]
        ),
        "customer_pulse": pd.DataFrame(
            [
                {"PULSE_DATE_C": "2026-05-01", "SCORE__C": 7.0},
                {"PULSE_DATE_C": "2026-07-20", "SCORE__C": 5.0},
            ]
        ),
    }


# ---------------------------------------------------------------------------
# 1. Leakage: the killer property
# ---------------------------------------------------------------------------
def test_future_records_can_never_change_features():
    frames = _base_history()
    before = ps.snapshot_features(frames, AS_OF)
    polluted = dict(frames)
    polluted["tac_cases"] = pd.concat(
        [
            frames["tac_cases"],
            _tac(
                {"Date/Time Opened": "2026-08-10", "Severity": "P1"},   # after T
                {"Date/Time Opened": "2026-09-01", "Severity": "P1", "Transaction ID": "BEMS-9"},
            ),
        ],
        ignore_index=True,
    )
    polluted["customer_pulse"] = pd.concat(
        [frames["customer_pulse"], pd.DataFrame([{"PULSE_DATE_C": "2026-08-20", "SCORE__C": 1.0}])],
        ignore_index=True,
    )
    polluted["adoption_barriers"] = pd.concat(
        [frames["adoption_barriers"], pd.DataFrame([{"OPEN_DATE_C": "2026-08-15", "SEVERITY_C": "Critical"}])],
        ignore_index=True,
    )
    after = ps.snapshot_features(polluted, AS_OF)
    assert before == after, "a record dated after T changed features at T — leakage"


def test_boundary_convention_T_in_features_not_label():
    exactly_T = _tac({"Date/Time Opened": AS_OF.isoformat(), "Severity": "P1"})
    events = ps.escalation_event_dates(exactly_T)
    # feature side: <= T includes it
    assert len(ps._dates_asof(pd.Series(events.values), AS_OF)) == 1
    # label side: strictly after T excludes it
    assert len(ps._dates_in_label_window(pd.Series(events.values), AS_OF, 30)) == 0
    # one second after T flips it to the label window
    later = _tac({"Date/Time Opened": (AS_OF + pd.Timedelta(seconds=1)).isoformat(), "Severity": "P1"})
    ev2 = ps.escalation_event_dates(later)
    assert len(ps._dates_in_label_window(pd.Series(ev2.values), AS_OF, 30)) == 1


def test_openness_reconstructed_from_dates_never_status():
    """A case closed AFTER T must count as open AT T even though the export's
    current status says closed."""
    tac = _tac(
        {"Date/Time Opened": "2026-07-01", "Severity": "P1",
         "Date/Time Closed": "2026-08-20", "Case Status": "Closed"},
    )
    mask = ps._open_case_mask_asof(tac, AS_OF)
    assert bool(mask.iloc[0]) is True


# ---------------------------------------------------------------------------
# 2. Cold start & event stream
# ---------------------------------------------------------------------------
def test_cold_start_returns_none_not_low():
    short = {"tac_cases": _tac({"Date/Time Opened": "2026-07-25", "Severity": "P1"})}
    assert ps.snapshot_features(short, AS_OF) is None
    assert ps.escalation_outlook(short, AS_OF) is None


def test_event_stream_dedups_bems_p1_overlap():
    """A P1 case that is ALSO BEMS is one event, not two."""
    tac = _tac(
        {"Date/Time Opened": "2026-07-25", "Severity": "P1",
         "Transaction ID": "BEMS-1", "bemscsc_refs": "x"},
    )
    events = ps.escalation_event_dates(tac)
    assert len(events) == 1


def test_p3_non_bems_is_not_an_escalation_event():
    tac = _tac({"Date/Time Opened": "2026-07-25", "Severity": "P3"})
    assert len(ps.escalation_event_dates(tac)) == 0


# ---------------------------------------------------------------------------
# 3. Person-period table: labels, censoring, exclusions
# ---------------------------------------------------------------------------
def _history_with_planted_escalation():
    """Customer whose escalation on 2026-07-10 should be predicted by the
    cutoff(s) in (2026-06-10, 2026-07-10)."""
    return {
        "Planted": {
            "tac_cases": _tac(
                {"Date/Time Opened": "2026-01-15", "Severity": "P3", "Date/Time Closed": "2026-02-01"},
                {"Date/Time Opened": "2026-04-01", "Severity": "P2", "Date/Time Closed": "2026-04-20"},
                {"Date/Time Opened": "2026-07-10", "Severity": "P1", "Date/Time Closed": "2026-07-30"},
            ),
        },
        "Quiet": {
            "tac_cases": _tac(
                {"Date/Time Opened": "2026-01-20", "Severity": "P4", "Date/Time Closed": "2026-02-10"},
                {"Date/Time Opened": "2026-05-05", "Severity": "P3", "Date/Time Closed": "2026-05-15"},
            ),
        },
    }


def test_person_period_labels_and_censoring():
    table = ps.build_person_period_table(
        _history_with_planted_escalation(), stride_days=7, data_end="2026-08-03T12:00:00Z"
    )
    assert not table.empty
    # censored rows exist and carry the reason, with no label
    censored = table[table["excluded_reason"] == "censored_window"]
    assert not censored.empty and censored["label"].isna().all()
    # every censored cutoff is within 30d of data end
    assert ((pd.Timestamp("2026-08-03T12:00:00Z") - censored["as_of"]).dt.days < 30).all()
    # the planted escalation is labeled 1 at some prior evaluable cutoff
    planted = table[(table["customer"] == "Planted") & (table["excluded_reason"] == "")]
    assert (planted["label"] == 1).any()
    # the quiet customer never labels 1
    quiet = table[(table["customer"] == "Quiet") & (table["excluded_reason"] == "")]
    assert not (quiet["label"] == 1).any()


def test_already_escalated_at_T_is_excluded_not_predicted():
    frames = {
        "Fire": {
            "tac_cases": _tac(
                {"Date/Time Opened": "2026-01-15", "Severity": "P3", "Date/Time Closed": "2026-02-01"},
                # open P1 covering June: cutoffs inside are excluded
                {"Date/Time Opened": "2026-06-01", "Severity": "P1", "Date/Time Closed": "2026-07-15"},
            ),
        }
    }
    table = ps.build_person_period_table(frames, stride_days=7, data_end="2026-09-30T00:00:00Z")
    excluded = table[table["excluded_reason"] == "already_escalated_at_T"]
    assert not excluded.empty
    assert (excluded["as_of"] >= pd.Timestamp("2026-06-01", tz="UTC")).all()
    assert (excluded["as_of"] <= pd.Timestamp("2026-07-15", tz="UTC")).all()


# ---------------------------------------------------------------------------
# 4. Scoring: determinism, monotonicity, contributor integrity
# ---------------------------------------------------------------------------
def test_score_is_deterministic_and_contributors_sum():
    feats = ps.snapshot_features(_base_history(), AS_OF)
    s1 = ps.score_snapshot(feats)
    s2 = ps.score_snapshot(feats)
    assert s1 == s2
    assert sum(p for _, p in s1["contributors"]) == s1["points"]


def test_more_escalations_never_lower_points():
    frames = _base_history()
    base = ps.score_snapshot(ps.snapshot_features(frames, AS_OF))["points"]
    worse = dict(frames)
    worse["tac_cases"] = pd.concat(
        [
            frames["tac_cases"],
            _tac(
                {"Date/Time Opened": "2026-07-28", "Severity": "P1"},
                {"Date/Time Opened": "2026-07-29", "Severity": "P2"},
                {"Date/Time Opened": "2026-07-30", "Severity": "P1"},
            ),
        ],
        ignore_index=True,
    )
    escalated = ps.score_snapshot(ps.snapshot_features(worse, AS_OF))["points"]
    assert escalated >= base


def test_stale_pulse_is_neutral_never_positive():
    frames = _base_history()
    stale = dict(frames)
    stale["customer_pulse"] = pd.DataFrame(
        [{"PULSE_DATE_C": "2026-03-01", "SCORE__C": 1.0}]  # terrible but >90d old
    )
    scored = ps.score_snapshot(ps.snapshot_features(stale, AS_OF))
    assert not any("pulse" in label.lower() for label, _ in scored["contributors"])


# ---------------------------------------------------------------------------
# 5. Honest statistics
# ---------------------------------------------------------------------------
def test_wilson_interval_known_values():
    lo, hi = ps.wilson_interval(6, 10)
    assert 0.30 < lo < 0.32 and 0.82 < hi < 0.84  # canonical 6/10 -> [.313,.832]
    lo0, hi0 = ps.wilson_interval(0, 0)
    assert (lo0, hi0) == (0.0, 1.0)


def test_jeffreys_and_prudent_bounds():
    assert ps.jeffreys_rate(0, 9) == pytest.approx(0.05)
    # rule-of-three flavor: zero events in 30 still allows a real rate
    assert ps.prudent_upper_bound(30) > 0.04
    assert ps.prudent_upper_bound(0) == 1.0


def test_pava_enforces_monotone_rates():
    rates = [0.10, 0.05, 0.20]  # middle band violates
    out = ps.pava_monotone(rates, [10, 10, 10])
    assert out == sorted(out)
    assert out[0] == pytest.approx(out[1])  # violating pair pooled


def test_claim_ladder_gates():
    assert ps.calibration_claim_level(9) == "insufficient_history"
    assert ps.calibration_claim_level(10) == "low_confidence"
    assert ps.calibration_claim_level(30) == "normal"


def test_banded_calibration_table_shapes_and_zero_event_bound():
    points = [5, 10, 30, 50, 80, 90]
    labels = [0, 0, 0, 1, 1, 1]
    bands = ps.banded_calibration_table(points, labels)
    names = [b["band"] for b in bands]
    assert names == ["LOW", "MODERATE", "ELEVATED", "CRITICAL_WATCH"]
    low = next(b for b in bands if b["band"] == "LOW")
    assert low["events"] == 0 and low["zero_event_upper_bound"] is not None


# ---------------------------------------------------------------------------
# 6. Production API honesty
# ---------------------------------------------------------------------------
def test_outlook_uncalibrated_state_and_calibrated_attachment():
    frames = _base_history()
    out = ps.escalation_outlook(frames, AS_OF)
    assert out is not None
    assert out["calibration_state"] == "uncalibrated_prior"
    assert "observed_rate" not in out
    calibration = {
        "claim_level": "normal",
        "derived_from": "live_cisco_sources",
        "bands": [
            {"band": out["tier"], "n": 40, "events": 12,
             "rate_smoothed": 0.3049, "rate_monotone": 0.3049,
             "wilson_low": 0.18, "wilson_high": 0.45},
        ],
    }
    calibrated = ps.escalation_outlook(frames, AS_OF, calibration=calibration)
    assert calibrated["calibration_state"] == "calibrated"
    assert calibrated["observed_n"] == 40 and calibrated["observed_events"] == 12


def test_outlook_ignores_insufficient_calibration():
    frames = _base_history()
    weak = {"claim_level": "insufficient_history", "derived_from": "live_cisco_sources", "bands": [
        {"band": "ELEVATED", "n": 3, "events": 1, "rate_smoothed": 0.375},
    ]}
    out = ps.escalation_outlook(frames, AS_OF, calibration=weak)
    assert out["calibration_state"] == "uncalibrated_prior"


# ---------------------------------------------------------------------------
# 9. Round 160 adversarial-review regression armor
#    (every test below reproduces a demonstrated attacker counterexample)
# ---------------------------------------------------------------------------
def test_adv_fixture_smoke_calibration_never_quotes_as_evidence():
    """Provenance gate: an artifact minted from the offline fixture must
    never be presented as historical evidence."""
    frames = _base_history()
    tier = ps.escalation_outlook(frames, AS_OF)["tier"]
    smoke = {
        "claim_level": "normal",
        "derived_from": "offline_fixture_smoke",
        "bands": [{"band": tier, "n": 40, "events": 12, "rate_smoothed": 0.3}],
    }
    out = ps.escalation_outlook(frames, AS_OF, calibration=smoke)
    assert out["calibration_state"] == "uncalibrated_prior"
    assert "observed_rate" not in out


def test_adv_highest_priority_lifetime_max_is_ignored():
    """'Highest Priority' is a lifetime-max field — a post-T upgrade rewrites
    it, so it must never feed features (the attacker's leakage demo)."""
    frames = {
        "tac_cases": _tac(
            {"Date/Time Opened": "2026-03-10", "Severity": "P3", "Highest Priority": "P1",
             "Date/Time Closed": "2026-03-25"},
            {"Date/Time Opened": "2026-06-15", "Severity": "P3", "Highest Priority": "P1"},
        ),
    }
    feats = ps.snapshot_features(frames, AS_OF)
    assert feats["days_since_last_escalation"] is None
    assert feats["escalations_90d"] == 0
    assert feats["new_p12_14d"] == 0


def test_adv_bems_excluded_from_backtest_stream_included_in_production():
    """BEMS refs are mutable and undated: historical labels/features must not
    use them; production scoring at T=now may."""
    tac = _tac(
        {"Date/Time Opened": "2026-07-25", "Severity": "P3",
         "Transaction ID": "BEMS-9", "bemscsc_refs": "x"},
    )
    assert len(ps.escalation_event_dates(tac, include_bems=False)) == 0
    assert len(ps.escalation_event_dates(tac, include_bems=True)) == 1
    frames = {
        "tac_cases": pd.concat(
            [tac, _tac({"Date/Time Opened": "2026-03-01", "Severity": "P4",
                        "Date/Time Closed": "2026-03-10"})],
            ignore_index=True,
        )
    }
    backtest_feats = ps.snapshot_features(frames, AS_OF, include_bems=False)
    production_feats = ps.snapshot_features(frames, AS_OF, include_bems=True)
    assert backtest_feats["escalations_90d"] == 0
    assert production_feats["escalations_90d"] == 1


def test_adv_person_period_labels_never_use_bems():
    """The attacker's label-corruption world: a BEMS-flagged P3 must not
    create historical labels (its designation date is unknowable)."""
    frames = {
        "BemsOnly": {
            "tac_cases": _tac(
                {"Date/Time Opened": "2026-01-15", "Severity": "P4", "Date/Time Closed": "2026-02-01"},
                {"Date/Time Opened": "2026-05-20", "Severity": "P3",
                 "Transaction ID": "BEMS-9", "bemscsc_refs": "x", "Date/Time Closed": "2026-06-10"},
            ),
        }
    }
    table = ps.build_person_period_table(frames, stride_days=7, data_end="2026-08-03T12:00:00Z")
    evaluable = table[table["excluded_reason"] == ""]
    assert not (evaluable["label"] == 1).any()


def test_adv_explicit_data_end_immune_to_future_dated_typo():
    """One 2027 typo row must not un-censor the portfolio when the export
    timestamp is passed explicitly (the attacker's contamination demo)."""
    good = {
        "tac_cases": _tac(
            {"Date/Time Opened": "2026-01-15", "Severity": "P2", "Date/Time Closed": "2026-02-01"},
            {"Date/Time Opened": "2026-06-15", "Severity": "P2", "Date/Time Closed": "2026-07-01"},
        ),
    }
    typo = {
        "tac_cases": _tac(
            {"Date/Time Opened": "2026-02-01", "Severity": "P3", "Date/Time Closed": "2026-02-20"},
            {"Date/Time Opened": "2027-06-01", "Severity": "P3"},  # the typo
        ),
    }
    export_end = "2026-08-01T00:00:00Z"
    anchored = ps.build_person_period_table(
        {"B": good, "A": typo}, stride_days=7, data_end=export_end
    )
    b_rows = anchored[anchored["customer"] == "B"]
    assert (b_rows["as_of"] <= pd.Timestamp(export_end)).all()
    censored = b_rows[b_rows["excluded_reason"] == "censored_window"]
    assert not censored.empty
    assert ((pd.Timestamp(export_end) - censored["as_of"]).dt.days < 30).all()


def test_adv_event_recall_evaluates_last_cutoff_of_each_run():
    """The attacker's exact counterexample: LOW at the first positive
    cutoffs, ELEVATED at the last one before the event — the named metric
    must count it caught (and the first-warning variant must not)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backtest_escalation_forecast import _event_level_recall

    rows = pd.DataFrame(
        [
            {"customer": "C", "as_of": pd.Timestamp("2026-05-04", tz="UTC"), "label": 1, "tier": "LOW"},
            {"customer": "C", "as_of": pd.Timestamp("2026-05-11", tz="UTC"), "label": 1, "tier": "LOW"},
            {"customer": "C", "as_of": pd.Timestamp("2026-05-18", tz="UTC"), "label": 1, "tier": "ELEVATED"},
            {"customer": "C", "as_of": pd.Timestamp("2026-05-25", tz="UTC"), "label": 1, "tier": "ELEVATED"},
        ]
    )
    out = _event_level_recall(rows, ["ELEVATED", "CRITICAL_WATCH"])
    assert out["events_total"] == 1
    assert out["events_caught_at_last_cutoff"] == 1
    assert out["events_caught_at_first_warning"] == 0


def test_adv_monthly_subset_spacing_respects_any_stride():
    """--stride-days 1 must not masquerade overlapping windows as the
    non-overlapping headline grid."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backtest_escalation_forecast import _monthly_subset

    rows = pd.DataFrame(
        [
            {"customer": "C", "as_of": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=i), "label": 0, "tier": "LOW"}
            for i in range(120)
        ]
    )
    kept = _monthly_subset(rows, stride_days=1).sort_values("as_of")
    gaps = kept["as_of"].diff().dropna().dt.days
    assert (gaps > 30).all()


def test_adv_contributors_are_largest_not_first():
    """The compound-tech interaction (+10) must not be dropped just because
    three smaller families fired earlier in code order."""
    feats = {
        "days_since_last_escalation": 150,   # +8
        "escalations_90d": 0,
        "velocity_ratio": 1.0, "cases_opened_30d": 1,  # +2
        "velocity_accelerating": False,
        "new_p12_14d": 0, "p12_share_shift": False,
        "weighted_open_backlog": 2.0,        # +3
        "oldest_open_p12_age_days": None,
        "pulse_last": None, "pulse_declines": 0, "pulse_stale": True,
        "barriers_opened_90d": 1,            # +4
        "critical_barrier_opened_90d": False,
        "compound_tech_flag": True,          # +10 — the largest
    }
    scored = ps.score_snapshot(feats)
    top3 = sorted(scored["contributors"], key=lambda t: (-t[1], t[0]))[:3]
    assert top3[0][1] == 10 and "same technology" in top3[0][0]


# ---------------------------------------------------------------------------
# 7. Backtest harness end-to-end on planted-signal synthetic history
# ---------------------------------------------------------------------------
def test_backtest_finds_planted_signal():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backtest_escalation_forecast import run_backtest

    customers = {}
    # five escalation-prone customers: escalations every ~6 weeks
    for i in range(5):
        rows = [
            {"Date/Time Opened": f"2026-0{m}-1{i % 3}", "Severity": "P1",
             "Date/Time Closed": f"2026-0{m}-2{i % 3}"}
            for m in (1, 2, 4, 5, 7)
        ]
        rows.append({"Date/Time Opened": "2026-03-05", "Severity": "P3", "Date/Time Closed": "2026-03-15"})
        customers[f"Hot{i}"] = {"tac_cases": pd.DataFrame(rows)}
    # five quiet customers: sparse P3/P4 only
    for i in range(5):
        customers[f"Cold{i}"] = {
            "tac_cases": pd.DataFrame([
                {"Date/Time Opened": "2026-01-15", "Severity": "P4", "Date/Time Closed": "2026-01-25"},
                {"Date/Time Opened": "2026-05-10", "Severity": "P3", "Date/Time Closed": "2026-05-20"},
            ])
        }
    result = run_backtest(customers, stride_days=7)
    assert result["ok"], result
    lift = result["monthly"]["lift_top20"]
    assert lift is not None and lift["lift"] is not None and lift["lift"] > 1.0
    assert result["event_level"]["events_total"] > 0
    assert result["baselines"]["trailing_90d_escalation_heuristic"]["auc"] is not None
    # bands are present and ordered
    assert [b["band"] for b in result["bands"]] == ["LOW", "MODERATE", "ELEVATED", "CRITICAL_WATCH"]


# ---------------------------------------------------------------------------
# 8. Surfaces: report paragraph + Ask AI lines state calibration honestly
# ---------------------------------------------------------------------------
def _facts_with_predictive_customer():
    import decision_report_delivery as delivery

    team_data = {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame([{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme", "STATUS_C": "Active"}]),
            "action_plans": pd.DataFrame([{"ID": "AP1", "BU_NAME": "Acme", "SUBJECT_C": "t", "STATUS_C": "Open"}]),
            "adoption_barriers": pd.DataFrame([
                {"ID": "AB1", "BU_NAME": "Acme", "SEVERITY_C": "High", "AB_STATUS_C": "Open",
                 "OPEN_DATE_C": "2026-06-01", "sub_technology": "Webex Calling"},
            ]),
            "customer_pulse": pd.DataFrame([
                {"ID": "P1", "BU_NAME": "Acme", "SCORE__C": 7.0, "PULSE_DATE_C": "2026-04-15"},
                {"ID": "P2", "BU_NAME": "Acme", "SCORE__C": 3.0, "PULSE_DATE_C": "2026-07-20"},
            ]),
            "tac_cases": pd.DataFrame([
                {"SR Number": "1", "BU_NAME": "Acme", "Customer": "Acme", "Severity": "P1",
                 "Case Status": "Open", "Date/Time Opened": "2026-07-25"},
                {"SR Number": "2", "BU_NAME": "Acme", "Customer": "Acme", "Severity": "P2",
                 "Case Status": "Closed", "Date/Time Opened": "2026-06-10", "Date/Time Closed": "2026-06-25"},
                {"SR Number": "3", "BU_NAME": "Acme", "Customer": "Acme", "Severity": "P3",
                 "Case Status": "Closed", "Date/Time Opened": "2026-03-05", "Date/Time Closed": "2026-03-20"},
            ]),
            "success_priorities": pd.DataFrame(),
        }
    }
    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader", scope_type="team", scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera", days=90, as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(), data_as_of_state="available",
    )
    return delivery, facts


def test_report_outlook_paragraph_renders_with_uncalibrated_disclosure():
    delivery, facts = _facts_with_predictive_customer()
    doc = delivery.build_concise_word_document(facts)
    lines = [p.text for p in doc.paragraphs if p.text.startswith("Predictive outlook")]
    assert len(lines) == 1
    line = lines[0]
    assert "Acme" in line
    assert "uncalibrated prior — relative ranking only" in line
    assert "backtest_escalation_forecast.py" in line
    # contract still holds (paragraph, not a table)
    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, doc)
    assert contract.get("ok"), contract.get("errors")


def test_ask_ai_decision_context_carries_outlook_line():
    from ask_ai_grounded import build_decision_context_block
    from risk_scoring import compute_customer_risk_profile

    prof = compute_customer_risk_profile("Acme", as_of=AS_OF)
    outlooks = {
        "Acme": {
            "tier": "ELEVATED", "points": 62, "calibration_state": "uncalibrated_prior",
            "horizon_days": 30, "contributors": [("escalation in last 30d", 24)],
        }
    }
    block = build_decision_context_block({"Acme": prof}, outlooks=outlooks)
    assert "outlook_30d: ELEVATED (62 pts" in block
    assert "not a probability" in block
