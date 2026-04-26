"""Round 21 / R20-NEXT-004 — Canonical-layer KPI diff vs golden fixture.

Mission (per QUALITY_AUDIT.md L1299-1416 "Round 19 — Report Accuracy
Golden Fixture"; this round delivers Phases 2-3-4-SSoT):

    KPI computed by canonical_metrics  ==  hand-computed expected value

The synthetic fixtures in ``tests/fixtures/round19/golden.py`` give us
a known input. The ``EXPECTED_KPIS`` dict in the same module gives us
hand-computed expected values for every KPI in the registry. This test
file is the diff harness: each test calls a single canonical helper
(or ``build_portfolio_metrics``) against the fixture and asserts the
emitted value matches the expected value byte-for-byte.

Why the diff harness lives at the canonical layer first
-------------------------------------------------------
``canonical_metrics`` is the SSoT every report formatter consumes.
Pinning it against a known input means:

1. Any future drift in a helper (e.g. a regex change in the priority
   normalizer, a band threshold tweak, a count semantics change) is
   detected by ONE test failing with a clear diff.
2. The downstream Round 21.1 formatter-render tests can trust the
   numbers they extract from .docx / .xlsx -- if the canonical layer
   matches the expected dict and the formatter doesn't, the bug is in
   the formatter (or the long-deferred R20-NEXT-001 closure-binding
   bug), not in the metric helpers.
3. The 4 reconciliation invariants documented in the registry (priority
   sum == total_cases, band sum == len(risk_profiles), high_risk ==
   critical + high_only, open + closed <= total) are pinned as
   first-class tests so a future SSoT regression points directly at
   the broken invariant.

Hard rule: this test file does NOT modify any source code. It ADDS
test artifacts that pin the SSoT contract end-to-end against a single
known input. R20-NEXT-001 (closure-binding bug in generate_report /
generate_excel) stays deferred.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the golden module importable as a top-level module without
# requiring tests/ or tests/fixtures/ to be Python packages. This
# mirrors the conftest.py pattern of putting PROJECT_ROOT on sys.path.
_GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "round19"
if str(_GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GOLDEN_DIR))

import golden  # noqa: E402  -- path-relative import shim above

import canonical_metrics as cm  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ab_df():
    return golden.make_ab_df()


@pytest.fixture
def csone_df():
    return golden.make_csone_df()


@pytest.fixture
def pulse_df():
    return golden.make_pulse_df()


@pytest.fixture
def risk_profiles():
    return golden.make_risk_profiles()


@pytest.fixture
def extra_frames():
    return golden.make_extra_frames()


@pytest.fixture
def expected():
    return golden.EXPECTED_KPIS


# ---------------------------------------------------------------------------
# Per-helper exact-match tests (one per KPI in the Round 19 registry)
# ---------------------------------------------------------------------------


def test_count_total_tac_matches_expected(csone_df, expected):
    assert cm.count_total_tac(csone_df) == expected["total_cases"]


def test_count_p1_matches_expected(csone_df, expected):
    assert cm.count_p1(csone_df) == expected["p1_cases"]


def test_count_p2_matches_expected(csone_df, expected):
    assert cm.count_p2(csone_df) == expected["p2_cases"]


def test_count_p3_matches_expected(csone_df, expected):
    assert cm.count_p3(csone_df) == expected["p3_cases"]


def test_count_p4_matches_expected(csone_df, expected):
    assert cm.count_p4(csone_df) == expected["p4_cases"]


def test_count_unknown_priority_matches_expected(csone_df, expected):
    assert cm.count_unknown_priority(csone_df) == expected["unknown_priority_cases"]


def test_count_priority_breakdown_matches_expected(csone_df, expected):
    breakdown = cm.count_priority_breakdown(csone_df)
    assert breakdown == {
        "P1": expected["p1_cases"],
        "P2": expected["p2_cases"],
        "P3": expected["p3_cases"],
        "P4": expected["p4_cases"],
        "Unknown": expected["unknown_priority_cases"],
    }


def test_count_escalated_matches_expected(csone_df, expected):
    assert cm.count_escalated(csone_df) == expected["count_escalated"]


def test_count_open_tac_matches_expected(csone_df, expected):
    assert cm.count_open_tac(csone_df) == expected["count_open_tac"]


def test_count_closed_tac_matches_expected(csone_df, expected):
    assert cm.count_closed_tac(csone_df) == expected["count_closed_tac"]


def test_count_break_fix_matches_expected(csone_df, expected):
    assert cm.count_break_fix(csone_df) == expected["break_fix_cases"]


def test_count_provisioning_matches_expected(csone_df, expected):
    assert cm.count_provisioning(csone_df) == expected["provisioning_cases"]


def test_count_bems_canonical_mode_matches_expected(csone_df, expected):
    assert cm.count_bems(csone_df) == expected["bems_count"]


def test_bems_rate_matches_expected(csone_df, expected):
    assert cm.bems_rate(csone_df) == expected["bems_rate"]


def test_count_total_barriers_matches_expected(ab_df, expected):
    assert cm.count_total_barriers(ab_df) == expected["total_barriers"]


def test_count_critical_barriers_matches_expected(ab_df, expected):
    # Default mode is critical_or_high (registry note L1378).
    assert cm.count_critical_barriers(ab_df) == expected["count_critical_barriers"]


def test_count_open_barriers_matches_expected(ab_df, expected):
    assert cm.count_open_barriers(ab_df) == expected["count_open_barriers"]


def test_count_customers_full_universe_matches_expected(
    ab_df, csone_df, pulse_df, extra_frames, expected
):
    actual = cm.count_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        pulse_df=pulse_df,
        extra_frames=extra_frames,
    )
    assert actual == expected["total_customers"]


def test_compute_high_risk_count_matches_expected(risk_profiles, expected):
    assert cm.compute_high_risk_count(risk_profiles) == expected["high_risk_customers"]


def test_pulse_sentiment_matches_expected(pulse_df, expected):
    actual = cm.pulse_sentiment(pulse_df)
    expected_pulse = expected["pulse"]
    # Compare each documented field explicitly so a single drift produces
    # a clear assertion error pointing at the offending key.
    for key in (
        "count",
        "mean_0_to_10",
        "positive",
        "neutral",
        "negative",
        "sentiment",
        "has_backfill_flag",
    ):
        assert actual[key] == expected_pulse[key], (
            f"pulse_sentiment[{key}] expected {expected_pulse[key]!r} "
            f"got {actual[key]!r}"
        )


# ---------------------------------------------------------------------------
# Integration test: build_portfolio_metrics is the single payload that
# every report path consumes via report_consistency.validate_report_consistency.
# This is the test that, if it goes red, indicates a real cross-report
# contract regression.
# ---------------------------------------------------------------------------


def test_build_portfolio_metrics_matches_expected(
    ab_df, csone_df, pulse_df, risk_profiles, extra_frames, expected
):
    payload = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_profiles=risk_profiles,
        # pulse contributes customer names too; pass it as an extra frame
        # so the count_customers union sees every source the registry
        # documents at QUALITY_AUDIT.md L1303-1305.
        extra_customer_frames=[*extra_frames, pulse_df],
    )

    keys_to_match = (
        "total_customers",
        "total_barriers",
        "total_cases",
        "bems_count",
        "critical_p1",
        "high_p2",
        "p1_cases",
        "p2_cases",
        "p3_cases",
        "p4_cases",
        "unknown_priority_cases",
        "break_fix_cases",
        "provisioning_cases",
        "high_risk_customers",
        "critical_risk_customers",
        "high_only_risk_customers",
        "medium_risk_customers",
        "low_risk_customers",
        "healthy_customers",
        "risk_scale",
    )
    for key in keys_to_match:
        assert payload[key] == expected[key], (
            f"build_portfolio_metrics[{key}] expected {expected[key]!r} "
            f"got {payload[key]!r}"
        )


# ---------------------------------------------------------------------------
# Reconciliation invariants (registry L1344-1358).
# Each invariant is its own test so a regression points at the broken
# rule directly, not at a single composite assertion.
# ---------------------------------------------------------------------------


def test_invariant_priority_sum_equals_total_cases(csone_df):
    """Invariant 1: P1 + P2 + P3 + P4 + Unknown == total_cases."""
    breakdown = cm.count_priority_breakdown(csone_df)
    total = cm.count_total_tac(csone_df)
    assert (
        breakdown["P1"]
        + breakdown["P2"]
        + breakdown["P3"]
        + breakdown["P4"]
        + breakdown["Unknown"]
        == total
    )


def test_invariant_band_sum_equals_risk_universe_size(risk_profiles):
    """Invariant 2: critical + high_only + medium + low + healthy ==
    len(risk_profiles)."""
    payload = cm.build_portfolio_metrics(
        ab_df=None,
        csone_df=None,
        risk_profiles=risk_profiles,
    )
    band_total = (
        payload["critical_risk_customers"]
        + payload["high_only_risk_customers"]
        + payload["medium_risk_customers"]
        + payload["low_risk_customers"]
        + payload["healthy_customers"]
    )
    assert band_total == len(risk_profiles)


def test_invariant_high_risk_equals_critical_plus_high_only(risk_profiles):
    """Invariant 3: high_risk_customers == critical_risk + high_only_risk.

    This is the rule that prevents the "executive pie says 4, narrative
    table shows 9" drift documented at QUALITY_AUDIT.md L1389-1394.
    """
    payload = cm.build_portfolio_metrics(
        ab_df=None,
        csone_df=None,
        risk_profiles=risk_profiles,
    )
    assert (
        payload["high_risk_customers"]
        == payload["critical_risk_customers"] + payload["high_only_risk_customers"]
    )


def test_invariant_open_plus_closed_at_most_total_cases(csone_df):
    """Invariant 4: open_tac + closed_tac <= total_cases.

    Inequality (not equality) because Unknown lifecycle rows are
    legitimately neither Open nor Closed -- the fixture exercises
    this with 2 such rows.
    """
    open_tac = cm.count_open_tac(csone_df)
    closed_tac = cm.count_closed_tac(csone_df)
    total = cm.count_total_tac(csone_df)
    assert open_tac + closed_tac <= total
    # Also assert the strict inequality is exercised by the fixture so
    # a future fixture edit that drops the Unknown lifecycle rows will
    # prompt this test author to re-check coverage.
    assert open_tac + closed_tac < total, (
        "Round 21 fixture should include at least 1 Unknown-lifecycle row "
        "so the inequality branch of invariant 4 is exercised."
    )


# ---------------------------------------------------------------------------
# Excel Summary sheet — labeled-row order pin.
# ---------------------------------------------------------------------------


def test_excel_summary_row_order_matches_documented_sequence(
    ab_df, csone_df, expected
):
    """``report_export_styling.build_summary_rows`` emits labeled rows
    in the documented order (Round 15 / Phase 5.3 contract pinned at
    QUALITY_AUDIT.md L1388-1403). The Excel writer relies on this order
    to decide which row carries which KPI; reordering breaks downstream
    parity tests that read the workbook back in.
    """
    from report_export_styling import build_summary_rows

    rows = build_summary_rows(
        sheets={"AB_Detail_All": ab_df, "CSOne_Detail_All": csone_df},
        csconsole_data=None,
    )

    actual_labels = tuple(label for label, _ in rows)
    expected_labels = expected["excel_summary_label_order"]
    assert actual_labels == expected_labels


def test_excel_summary_kpi_values_use_canonical_metrics(
    ab_df, csone_df, expected
):
    """The summary rows pull every KPI from canonical_metrics so the
    workbook agrees with the Word report numbers (Round 15 / Phase 5.3,
    QUALITY_AUDIT.md L1388-1403).

    This test reads the canonical-metrics-driven values back out of the
    summary rows and asserts they match the hand-computed expected dict
    -- catches the failure mode that landed in Round 15 / Phase 2 where
    customers was rendering "--" because the frames were passed
    positionally instead of as keyword args.
    """
    from report_export_styling import build_summary_rows

    rows = dict(
        build_summary_rows(
            sheets={"AB_Detail_All": ab_df, "CSOne_Detail_All": csone_df},
            csconsole_data=None,
        )
    )

    # Per-KPI cross-check. Values are formatted (string) so compare
    # against the formatted expected value via the same _format_kpi
    # convention (integers render bare, e.g. "20").
    assert rows["Customers in portfolio"] == str(
        # Note: build_summary_rows does NOT pass extra_frames or risk
        # profiles, so customers count here only sees ab_df + csone_df
        # + (optionally) cs_pulse from csconsole_data. We pass no
        # csconsole_data, so the universe is ab_df ∪ csone_df =
        # {AcmeCorp, BetaInc, GammaLLC} = 3.
        3
    )
    assert rows["Adoption barriers (total)"] == str(expected["total_barriers"])
    assert rows["Adoption barriers (critical)"] == str(
        expected["count_critical_barriers"]
    )
    assert rows["Adoption barriers (open)"] == str(expected["count_open_barriers"])
    assert rows["TAC cases (total)"] == str(expected["total_cases"])
    assert rows["TAC cases (P1)"] == str(expected["p1_cases"])
    assert rows["TAC cases (open)"] == str(expected["count_open_tac"])
    assert rows["Escalations"] == str(expected["count_escalated"])
    assert rows["BEMS / break-fix"] == str(expected["bems_count"])
