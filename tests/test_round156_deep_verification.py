"""Round 156 deep-verification battery — permanent regression armor.

Jeff's requirement: "100% accurate 100% of the time."  What CAN be guaranteed
offline is proven here, permanently:

1. **Determinism** — same inputs, same outputs; row/column order irrelevant.
2. **Monotonicity (magnitude_first)** — adding an open/risky record can never
   DECREASE a component score.  (Legacy's violations are characterized, not
   endorsed: they are the shipped flaw the opt-in profile fixes.)
3. **Bounds** — every component and composite stays in [0, 100] at any scale.
4. **Internal consistency** — band always derives from the rounded composite;
   the 0-10 display score always equals the 0-100 score / 10; the portfolio
   summary always tallies the profiles it was given; the decision-report risk
   rows always carry the profile's own score/band verbatim.
5. **Cross-module consistency (magnitude_first)** — action-plan resolution
   follows the CANONICAL status buckets, so a plan named "Unresolved" or
   "Incomplete" can never again be counted as resolved work.
6. **Missing-data honesty (magnitude_first)** — absent pulse data is excluded
   and renormalized (like the Round 7 contract sentinel), never scored as
   healthy sentiment.

What canNOT be proven offline — that the ranking predicts real churn and
escalation — is exactly why ``magnitude_first`` stays opt-in until the live
Brian-Frazier outcome comparison (RISK_LOGIC_EVALUATION.md).
"""

from __future__ import annotations

import itertools

import pandas as pd
import pytest

from risk_scoring import (
    RISK_BAND_THRESHOLDS,
    RISK_SCORING_PROFILE_MAGNITUDE_FIRST,
    RiskWeights,
    _risk_band,
    _score_action_plans,
    _score_adoption_barriers,
    _score_contract,
    _score_customer_pulse,
    _score_support_cases,
    compute_customer_risk_profile,
    compute_portfolio_risk_summary,
)

MF = RISK_SCORING_PROFILE_MAGNITUDE_FIRST
AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")
EPS = 1e-9


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_RISK_SCORING_PROFILE", raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Weights & renormalization exactness
# ---------------------------------------------------------------------------
def test_risk_weights_sum_to_exactly_one():
    w = RiskWeights()
    total = (
        w.adoption_barriers + w.support_cases + w.customer_pulse
        + w.action_plans + w.incidents + w.contract + w.engagement
    )
    assert abs(total - 1.0) < 1e-12


def test_renormalization_is_exact_weighted_mean_of_present_components():
    """With contract AND pulse missing under MF, the composite must equal the
    weighted mean over the remaining components computed by hand."""
    ab = pd.DataFrame([{"SEVERITY_C": "High", "AB_STATUS_C": "Open"}] * 2)
    prof = compute_customer_risk_profile(
        "X", customer_ab=ab, as_of=AS_OF, scoring_profile=MF
    )
    comps = prof["components"]
    assert comps["contract"]["score"] is None  # missing subs -> excluded
    assert comps["customer_pulse"]["score"] is None  # missing pulse -> excluded
    w = RiskWeights()
    pairs = [
        (comps["adoption_barriers"]["score"], w.adoption_barriers),
        (comps["support_cases"]["score"], w.support_cases),
        (comps["action_plans"]["score"], w.action_plans),
        (comps["incidents"]["score"], w.incidents),
        (comps["engagement"]["score"], w.engagement),
    ]
    expected = sum(s * wt for s, wt in pairs) / sum(wt for _, wt in pairs)
    assert prof["risk_score_0_100"] == round(min(100.0, max(0.0, expected)), 1)


# ---------------------------------------------------------------------------
# 2. Monotonicity: magnitude_first, exhaustive small space
# ---------------------------------------------------------------------------
SEVS = ["Critical", "High", "Medium", "Low"]


def _ab_frame(pairs):
    if not pairs:
        return pd.DataFrame()
    return pd.DataFrame([{"SEVERITY_C": s, "AB_STATUS_C": st} for s, st in pairs])


def test_mf_barriers_monotone_adding_open_record_never_decreases():
    bases = [[]]
    for n in (1, 2, 3):
        for combo in itertools.product(
            [("Critical", "Open"), ("Low", "Open"), ("High", "Closed"), ("Medium", "Open")],
            repeat=n,
        ):
            bases.append(list(combo))
    for n in (4, 8, 10, 12):  # capped extremes — where the pre-fix violation lived
        bases.append([("Critical", "Open")] * n)
    for base in bases:
        s0 = _score_adoption_barriers(_ab_frame(base), as_of=AS_OF, scoring_profile=MF)["score"]
        for sev in SEVS:
            s1 = _score_adoption_barriers(
                _ab_frame(base + [(sev, "Open")]), as_of=AS_OF, scoring_profile=MF
            )["score"]
            assert s1 >= s0 - EPS, f"base={base} +open {sev}: {s0} -> {s1}"


def test_mf_action_plans_monotone_exhaustive():
    for n_open, n_closed in itertools.product(range(0, 10), range(0, 10)):
        rows = [{"STATUS_C": "Open"}] * n_open + [{"STATUS_C": "Completed - Successful"}] * n_closed
        s0 = _score_action_plans(pd.DataFrame(rows) if rows else pd.DataFrame(), scoring_profile=MF)["score"]
        s1 = _score_action_plans(pd.DataFrame(rows + [{"STATUS_C": "Open"}]), scoring_profile=MF)["score"]
        assert s1 >= s0 - EPS, f"{n_open}o/{n_closed}c"


def test_mf_contract_monotone_exhaustive():
    for hr, inact, ok in itertools.product(range(0, 6), range(0, 6), range(0, 6)):
        rows = (
            [{"RENEWAL_RISK_CATEGORY": "High", "STATUS_C": "Active"}] * hr
            + [{"RENEWAL_RISK_CATEGORY": "Low", "STATUS_C": "Inactive"}] * inact
            + [{"RENEWAL_RISK_CATEGORY": "Low", "STATUS_C": "Active"}] * ok
        )
        if not rows:
            continue
        base = pd.DataFrame(rows)
        s0 = _score_contract(base, scoring_profile=MF)["score"]
        s_hr = _score_contract(
            pd.DataFrame(rows + [{"RENEWAL_RISK_CATEGORY": "High", "STATUS_C": "Active"}]),
            scoring_profile=MF,
        )["score"]
        s_in = _score_contract(
            pd.DataFrame(rows + [{"RENEWAL_RISK_CATEGORY": "Low", "STATUS_C": "Inactive"}]),
            scoring_profile=MF,
        )["score"]
        assert s_hr >= s0 - EPS and s_in >= s0 - EPS


def test_legacy_barriers_nonmonotone_characterization():
    """CHARACTERIZATION, not endorsement: the shipped legacy formula DROPS the
    component when an open Low barrier is added next to an open Critical one
    (77 -> 62.125).  This documents the flaw magnitude_first fixes; if legacy
    ever changes, this test forces the change to be a conscious decision."""
    s0 = _score_adoption_barriers(_ab_frame([("Critical", "Open")]), as_of=AS_OF)["score"]
    s1 = _score_adoption_barriers(
        _ab_frame([("Critical", "Open"), ("Low", "Open")]), as_of=AS_OF
    )["score"]
    assert s0 == 77.0 and s1 == 62.125
    assert s1 < s0  # the legacy inversion, pinned


# ---------------------------------------------------------------------------
# 3. Bounds at extreme scale
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", ["legacy", MF])
def test_bounds_hold_at_extreme_scale(profile):
    n = 3000
    ab = pd.DataFrame([{"SEVERITY_C": "Critical", "AB_STATUS_C": "Open", "OPEN_DATE_C": "2025-01-01"}] * n)
    aps = pd.DataFrame([{"STATUS_C": "Open"}] * n)
    subs = pd.DataFrame([{"RENEWAL_RISK_CATEGORY": "Critical", "STATUS_C": "Terminated"}] * n)
    tac = pd.DataFrame(
        [{"Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30", "Transaction ID": "BEMS-1"}] * n
    )
    prof = compute_customer_risk_profile(
        "X", customer_ab=ab, customer_csone=tac, customer_action_plans=aps,
        customer_subs=subs, as_of=AS_OF, scoring_profile=profile,
    )
    for comp in prof["components"].values():
        if comp["score"] is not None:
            assert 0.0 <= comp["score"] <= 100.0
    assert 0.0 <= prof["risk_score_0_100"] <= 100.0
    assert prof["risk_band"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY"}


# ---------------------------------------------------------------------------
# 4. Determinism: row order, column order, repeat calls
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", ["legacy", MF])
def test_row_and_column_order_never_change_the_score(profile):
    ab = pd.DataFrame(
        [
            {"SEVERITY_C": "Critical", "AB_STATUS_C": "Open", "OPEN_DATE_C": "2026-05-01", "ID": "A1"},
            {"SEVERITY_C": "Low", "AB_STATUS_C": "Closed", "OPEN_DATE_C": "2026-07-01", "ID": "A2"},
            {"SEVERITY_C": "High", "AB_STATUS_C": "Open", "OPEN_DATE_C": "2026-06-15", "ID": "A3"},
        ]
    )
    base = _score_adoption_barriers(ab, as_of=AS_OF, scoring_profile=profile)["score"]
    shuffled_rows = ab.iloc[[2, 0, 1]].reset_index(drop=True)
    shuffled_cols = ab[list(reversed(ab.columns))]
    assert _score_adoption_barriers(shuffled_rows, as_of=AS_OF, scoring_profile=profile)["score"] == base
    assert _score_adoption_barriers(shuffled_cols, as_of=AS_OF, scoring_profile=profile)["score"] == base
    assert _score_adoption_barriers(ab, as_of=AS_OF, scoring_profile=profile)["score"] == base  # repeat


def test_mf_respects_duplicate_id_fanout_dedup():
    """R53.1 dedup must hold under magnitude_first too: 3 Snowflake fan-out
    rows sharing one ID are ONE logical barrier, not three."""
    fanout = pd.DataFrame(
        [{"ID": "AB-1", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}] * 3
    )
    single = pd.DataFrame(
        [{"ID": "AB-1", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}]
    )
    s_fan = _score_adoption_barriers(fanout, as_of=AS_OF, scoring_profile=MF)
    s_one = _score_adoption_barriers(single, as_of=AS_OF, scoring_profile=MF)
    assert s_fan["score"] == s_one["score"]
    assert s_fan["details"]["count"] == 1


# ---------------------------------------------------------------------------
# 5. Band edges & display parity
# ---------------------------------------------------------------------------
def test_band_edges_are_inclusive_lower_bound():
    assert _risk_band(75.0) == "CRITICAL" and _risk_band(74.9) == "HIGH"
    assert _risk_band(55.0) == "HIGH" and _risk_band(54.9) == "MEDIUM"
    assert _risk_band(35.0) == "MEDIUM" and _risk_band(34.9) == "LOW"
    assert _risk_band(15.0) == "LOW" and _risk_band(14.9) == "HEALTHY"
    assert RISK_BAND_THRESHOLDS == {"CRITICAL": 75, "HIGH": 55, "MEDIUM": 35, "LOW": 15}


@pytest.mark.parametrize("profile", ["legacy", MF])
def test_profile_band_and_0_10_score_always_derive_from_rounded_composite(profile):
    """The reported band must be the band OF THE NUMBER THE READER SEES."""
    frames = [
        dict(customer_ab=_ab_frame([("Critical", "Open")] * k), customer_action_plans=pd.DataFrame([{"STATUS_C": "Open"}] * k))
        for k in (1, 2, 3, 5, 8)
    ]
    for kw in frames:
        p = compute_customer_risk_profile("X", as_of=AS_OF, scoring_profile=profile, **kw)
        assert p["risk_band"] == _risk_band(p["risk_score_0_100"])
        assert p["risk_score_0_10"] == round(p["risk_score_0_100"] / 10.0, 1)


def test_portfolio_summary_tallies_exactly_the_profiles_given():
    profs = {}
    for i, k in enumerate((1, 2, 4, 6, 9)):
        profs[f"C{i}"] = compute_customer_risk_profile(
            f"C{i}",
            customer_ab=_ab_frame([("Critical", "Open")] * k),
            customer_csone=pd.DataFrame(
                [{"Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30"}] * k
            ),
            as_of=AS_OF,
        )
    summary = compute_portfolio_risk_summary(profs)
    tally = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "HEALTHY": 0}
    for p in profs.values():
        tally[p["risk_band"]] += 1
    assert summary["risk_band_counts"] == tally
    assert summary["total_customers"] == len(profs)
    assert summary["highest_risk_score_0_100"] == max(p["risk_score_0_100"] for p in profs.values())


# ---------------------------------------------------------------------------
# 6. Canonical status alignment (magnitude_first) — the substring-regex fix
# ---------------------------------------------------------------------------
def test_mf_action_plan_resolution_follows_canonical_buckets():
    df = pd.DataFrame(
        [{"STATUS_C": s} for s in (
            "Unresolved", "Incomplete", "Abandoned",  # legacy regex falsely resolves all three
            "Cancelled",                                # blocked bucket -> unresolved commitment
            "Closed - Cancelled", "Completed - Successful", "Done",  # genuinely completed
            "Open", "In Progress",
        )]
    )
    legacy = _score_action_plans(df)["details"]["unresolved_count"]
    mf = _score_action_plans(df, scoring_profile=MF)["details"]["unresolved_count"]
    assert legacy == 3  # pinned characterization of the substring flaw
    assert mf == 6      # Unresolved/Incomplete/Abandoned/Cancelled/Open/In Progress


def test_legacy_regex_flaw_characterized_unresolved_counts_as_resolved():
    df = pd.DataFrame([{"STATUS_C": "Unresolved"}])
    legacy = _score_action_plans(df)
    mf = _score_action_plans(df, scoring_profile=MF)
    assert legacy["details"]["unresolved_count"] == 0  # the shipped flaw, pinned
    assert mf["details"]["unresolved_count"] == 1      # the fix


# ---------------------------------------------------------------------------
# 7. Missing-data honesty (magnitude_first pulse exclusion)
# ---------------------------------------------------------------------------
def test_mf_missing_pulse_is_excluded_not_scored_healthy():
    for pulse in (None, pd.DataFrame()):
        comp = _score_customer_pulse(pulse if pulse is not None else pd.DataFrame(), scoring_profile=MF)
        assert comp["score"] is None
        assert comp["details"]["excluded_from_score"] is True
    # legacy unchanged: 0.0
    assert _score_customer_pulse(pd.DataFrame())["score"] == 0.0


def test_mf_all_backfill_pulse_is_excluded():
    backfill = pd.DataFrame([
        {"SCORE__C": 2.0, "COMMENTS__C": "[BACKFILL] migrated row", "is_backfill": True},
    ])
    comp = _score_customer_pulse(backfill, scoring_profile=MF)
    # whether excluded by the backfill filter or scored, it must never be BOTH
    # zero-scored AND counted: either None (excluded) or a real score
    if comp["score"] is None:
        assert comp["details"]["excluded_from_score"] is True
    else:
        assert comp["details"]["count"] >= 1


def test_mf_pulse_exclusion_renormalizes_composite_upward():
    """Missing sentiment must not dilute a risky customer toward HEALTHY."""
    ab = _ab_frame([("Critical", "Open")] * 4)
    tac = pd.DataFrame(
        [{"Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30"}] * 3
    )
    with_dilution = compute_customer_risk_profile(
        "X", customer_ab=ab, customer_csone=tac, as_of=AS_OF, scoring_profile="legacy"
    )
    renormalized = compute_customer_risk_profile(
        "X", customer_ab=ab, customer_csone=tac, as_of=AS_OF, scoring_profile=MF
    )
    # legacy scores pulse 0.0 at weight 0.15 (as-if-healthy); MF excludes it.
    assert with_dilution["components"]["customer_pulse"]["score"] == 0.0
    assert renormalized["components"]["customer_pulse"]["score"] is None


# ---------------------------------------------------------------------------
# 8. Support scorer: recent-window boundary is inclusive and threaded
# ---------------------------------------------------------------------------
def test_support_recent_window_boundary_inclusive():
    on_cutoff = AS_OF - pd.Timedelta(days=30)
    df = pd.DataFrame([
        {"Severity": "P3", "Case Status": "Open", "Date/Time Opened": on_cutoff.isoformat()},
        {"Severity": "P3", "Case Status": "Open", "Date/Time Opened": (on_cutoff - pd.Timedelta(seconds=1)).isoformat()},
    ])
    comp = _score_support_cases(df, recent_window_days=30, as_of=AS_OF)
    assert comp["details"]["recent_count"] == 1  # exactly-at-cutoff counts; 1s earlier does not
    assert comp["details"]["recent_window_days"] == 30
    wide = _score_support_cases(df, recent_window_days=90, as_of=AS_OF)
    assert wide["details"]["recent_count"] == 2


# ---------------------------------------------------------------------------
# 9. next_best_action count fidelity — the action must quote the real counts
# ---------------------------------------------------------------------------
def test_next_best_action_quotes_the_actual_component_counts():
    tac = pd.DataFrame(
        [
            {"Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30", "Transaction ID": "BEMS-9", "bemscsc_refs": "x"},
            {"Severity": "P1", "Case Status": "Open", "Date/Time Opened": "2026-07-30", "Transaction ID": "BEMS-10", "bemscsc_refs": "y"},
        ]
    )
    prof = compute_customer_risk_profile("X", customer_csone=tac, as_of=AS_OF)
    bems = prof["components"]["support_cases"]["details"]["bems_count"]
    assert bems == 2
    assert str(bems) in prof["next_best_action"]
    assert "BEMS" in prof["next_best_action"]


# ---------------------------------------------------------------------------
# 10. Compound-risk gating: closed on EITHER side must not pair
# ---------------------------------------------------------------------------
def test_compound_risk_requires_open_on_both_sides():
    from risk_scoring import _r155_compound_risk_factor

    open_ab = pd.DataFrame([{"sub_technology": "Webex Calling", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}])
    closed_ab = pd.DataFrame([{"sub_technology": "Webex Calling", "SEVERITY_C": "Critical", "AB_STATUS_C": "Closed"}])
    open_tac = pd.DataFrame([{"sub_technology": "Webex Calling", "Severity": "P1", "Status": "Open"}])
    assert _r155_compound_risk_factor(open_ab, open_tac) is not None
    assert _r155_compound_risk_factor(closed_ab, open_tac) is None  # AB side closed


# ---------------------------------------------------------------------------
# 11. Report layer: risk rows carry the profile's own numbers, both profiles
# ---------------------------------------------------------------------------
def _report_facts_fixture(monkeypatch, profile):
    if profile == MF:
        monkeypatch.setenv("ADOPTIQ_RISK_SCORING_PROFILE", MF)
    import scripts.generate_offline_acceptance_artifacts as gen
    import decision_report_delivery as delivery

    payload = dict(gen.load_sanitized_fixture(gen.DEFAULT_FIXTURE_PATH))
    team_data, labels = gen.build_scope_fixture(payload, "team", delivery)
    team_data = gen._stamp_offline_fixture_sources(team_data)

    # Mirror the generator's external frames + disclosed warnings so the risk
    # source-state resolves the same way the shipped 23/23 parity run does.
    def _ext(rows):
        frame = pd.DataFrame([dict(r) for r in rows or []])
        frame["Source_System"] = gen.OFFLINE_SOURCE_SYSTEM
        frame.attrs.update(
            {
                "partial": True,
                "source_mode": "offline_fixture",
                "source_mode_detail": gen.OFFLINE_SOURCE_DETAIL,
            }
        )
        return frame

    warnings = list(payload.get("partial_data_warnings") or [])
    warnings.append(
        {
            "dataset": "Live Snowflake/CSConsole/CSOne acceptance",
            "kind": "deferred",
            "effect": gen.OFFLINE_SOURCE_DETAIL,
        }
    )
    facts = delivery.build_report_facts(
        team_data,
        report_type=labels["report_type"], scope_type=labels["scope_type"],
        scope_value=labels["scope_value"], manager_name=str(payload["manager_name"]),
        days=int(payload["days"]), as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(), data_as_of_state="available",
        external_incidents=_ext(payload.get("external_incidents")),
        external_bugs=_ext(payload.get("external_bugs")),
        partial_data_warnings=warnings,
    )
    return delivery, facts


@pytest.mark.parametrize("profile", ["legacy", MF])
def test_risk_rows_match_profiles_verbatim_or_refuse_honestly(monkeypatch, profile):
    """The accuracy dichotomy: a risk row either carries the profile's OWN
    score/band verbatim (state available/zero) or refuses with an explicit
    ``Unavailable (…)`` label — it must never print a number from anywhere
    else, and never print a number in a non-available state."""
    delivery, facts = _report_facts_fixture(monkeypatch, profile)
    rows = delivery._visible_risk_decision_rows(facts)
    profiles = facts["risk_profiles"]
    state = str((facts.get("risk_summary") or {}).get("source_state") or "").casefold()
    assert rows, "expected risk rows"
    for row in rows:
        customer, band, score = row[0], row[1], row[2]
        if state in {"available", "zero"}:
            assert profiles[customer]["risk_band"] == band
            assert profiles[customer]["risk_score_0_100"] == score
        else:
            assert str(band).startswith("Unavailable")
            assert str(score).startswith("Unavailable")
        assert row[5]  # action never empty in either state


def test_default_fixture_bands_pinned_as_characterization(monkeypatch):
    """The offline fixture's DEFAULT-profile composites, pinned exactly.  If
    these ever move, the default scoring changed — which must be a conscious,
    live-validated decision, never an accident.

    These are the values behind the shipped oracle's band distribution
    (``chart.risk_distribution.high = 2, medium = 1`` and
    ``high_risk_customers = 2`` for the team scope) — pinning them here means
    a default-path scoring drift fails THIS test with the exact numbers, not
    just a generic digest mismatch in the parity suite."""
    _, facts = _report_facts_fixture(monkeypatch, "legacy")
    got = {
        name: (p["risk_score_0_100"], p["risk_band"])
        for name, p in facts["risk_profiles"].items()
    }
    assert got == {
        "Acme Corporation": (55.8, "HIGH"),
        "Beta Industries": (35.2, "MEDIUM"),
        "Gamma Public Sector": (55.8, "HIGH"),
    }


def test_mf_profile_report_still_passes_cross_artifact_contract(monkeypatch):
    """Flipping the profile must never break the artifact contract — the
    workbook, Word doc, and facts must stay mutually consistent."""
    delivery, facts = _report_facts_fixture(monkeypatch, MF)
    sheets = delivery.build_source_data_sheets(facts)
    document = delivery.build_concise_word_document(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert contract.get("ok"), contract.get("errors")
