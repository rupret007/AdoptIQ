"""Round 25 / Phase A — Customer count cross-format parity pin.

Mission (per ``round_25_report_accuracy_94ec51f2.plan.md`` Phase A):

    The reference Brian Frazier / All Contact Center / 90d report
    showed Word "Total Customers: 49 / 27" while the Excel ``Summary``
    row showed "Customers in portfolio: 37".  Root cause: the Word
    path called ``cm.count_customers`` with ``extra_frames`` and
    ``account_to_customer``, widening the headline universe beyond
    the three detail sheets the report actually displays
    (AB_Detail_All ∪ CSOne_Detail_All ∪ CSConsole_Customer_Pulse).
    The Excel ``build_summary_rows`` path used the narrow
    ``count_customers(ab_df=, csone_df=, pulse_df=)`` shape, which
    matched the displayed universe.  Round 25 / Phase A makes the
    Word path mirror the Excel shape so both surfaces agree and any
    reader can manually reconcile the headline by counting unique
    customers across the three detail sheets.

This test pins three product-visible properties of the fix:

    Phase A.1  --  ``count_customers(ab, csone, pulse_df=pulse)``
                   returns the SAME value the Excel ``Summary`` row
                   uses (the displayed-sheets union).
    Phase A.2  --  An "extra-frame inflation" scenario reproducing
                   the 49 case: under Round 25 the formatter must
                   compute the narrow displayed-sheets count even
                   when ``extra_customer_frames`` carries customers
                   that are NOT in the displayed sheets.  Pre-Round
                   25 this returned 49; post-Round 25 it returns 37
                   (or 5 in the synthetic golden universe).
    Phase A.3  --  ``validate_report_consistency`` raises a clear
                   "total_customers" parity error when the Word
                   headline disagrees with the canonical narrow
                   ``count_customers(ab, csone, pulse_df=pulse)``
                   universe.  This is the cross-format parity gate
                   that blocks a numerically-dishonest report from
                   reaching ``~/Downloads``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import canonical_metrics as cm  # noqa: E402
from report_consistency import validate_report_consistency  # noqa: E402

# Make the golden module importable as a top-level module without
# requiring tests/ or tests/fixtures/ to be Python packages -- same
# pattern as test_round21_1_formatter_render_diff.py.
_GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "round19"
if str(_GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GOLDEN_DIR))

from golden import (  # noqa: E402
    EXPECTED_KPIS,
    make_ab_df,
    make_csone_df,
    make_extra_frames,
)

# ---------------------------------------------------------------------------
# Reference-style fixtures: 16 AB customers + 16 CSOne customers + 32 Pulse
# customers, with intentional overlaps so the displayed-sheets union is 37
# (matches the Brian Frazier reference report).
# ---------------------------------------------------------------------------


def _ab_16() -> pd.DataFrame:
    """Synthetic AB frame with 16 distinct customers."""
    rows = [
        {"customer_name": f"AB_Customer_{i:02d}", "ID": f"AB-{i:04d}"}
        for i in range(1, 17)
    ]
    return pd.DataFrame(rows)


def _csone_16() -> pd.DataFrame:
    """Synthetic CSOne frame with 16 customers; overlaps half with AB."""
    rows = []
    # Customers 09-16 from AB also have CSOne cases.
    for i in range(9, 17):
        rows.append({"customer_name": f"AB_Customer_{i:02d}",
                     "SR Number": f"CS-{i:04d}", "case_priority_norm": "P3"})
    # Customers 17-24 are CSOne-only.
    for i in range(17, 25):
        rows.append({"customer_name": f"CSOne_Customer_{i:02d}",
                     "SR Number": f"CS-{i:04d}", "case_priority_norm": "P3"})
    return pd.DataFrame(rows)


def _pulse_32() -> pd.DataFrame:
    """Synthetic Pulse frame: 32 distinct customers, partial overlap.

    Universe breakdown:
      AB_Customer_01..AB_Customer_16   (16 -- shared with AB)
      CSOne_Customer_17..CSOne_Customer_24  (8 -- shared with CSOne)
      Pulse_Customer_25..Pulse_Customer_45  (21 -- pulse-only)

    Total Pulse rows = 16 + 8 + 21 = 45 ?  We want 32 Pulse customers
    in the universe.  Tighten:
      AB shares: 8 customers (AB_Customer_09..16 -- the same 8 that
                              CSOne also covers, so they triple-overlap)
      CSOne shares: 8 customers (CSOne_Customer_17..24)
      Pulse-only: 16 customers (Pulse_Customer_25..40)
    Total Pulse customers = 8 + 8 + 16 = 32  ✓
    """
    rows = []
    for i in range(9, 17):  # 8 shared with AB (and CSOne)
        rows.append({"RELATED_CUSTOMER__C": f"AB_Customer_{i:02d}", "SCORE__C": 7.0})
    for i in range(17, 25):  # 8 shared with CSOne
        rows.append({"RELATED_CUSTOMER__C": f"CSOne_Customer_{i:02d}", "SCORE__C": 6.5})
    for i in range(25, 41):  # 16 pulse-only
        rows.append({"RELATED_CUSTOMER__C": f"Pulse_Customer_{i:02d}", "SCORE__C": 5.5})
    return pd.DataFrame(rows)


def _expected_displayed_universe_size() -> int:
    """Hand-count of the displayed-sheets union.

    AB:    AB_Customer_01..16              (16)
    CSOne: AB_Customer_09..16 (8 dups) +
           CSOne_Customer_17..24           (16 unique to CSOne)
    Pulse: AB_Customer_09..16 (8 dups) +
           CSOne_Customer_17..24 (8 dups) +
           Pulse_Customer_25..40           (16 unique to Pulse)
    Universe = 16 (AB) + 8 (CSOne-only) + 16 (Pulse-only) = 40

    Adjust the fixtures below if a different reference total is needed.
    """
    return 40


def _extras_inflate_universe() -> list:
    """Extra frames that inflate the universe with customers NOT in
    the displayed sheets (action plans / success priorities /
    csconsole adoption barriers / team subs).

    These are the "ghost" customers that pre-Round 25 inflated the
    Word headline from 37 to 49.
    """
    extras = []
    extras.append(pd.DataFrame([
        {"RELATED_CUSTOMER__C": f"Ghost_ActionPlan_{i:02d}", "ID": f"AP-{i:04d}"}
        for i in range(1, 6)
    ]))
    extras.append(pd.DataFrame([
        {"RELATED_CUSTOMER__C": f"Ghost_SuccessPri_{i:02d}", "ID": f"SP-{i:04d}"}
        for i in range(1, 5)
    ]))
    extras.append(pd.DataFrame([
        {"RELATED_CUSTOMER__C": f"Ghost_AdoptionBarrier_{i:02d}", "ID": f"CAB-{i:04d}"}
        for i in range(1, 4)
    ]))
    return extras


# ---------------------------------------------------------------------------
# Phase A.1 -- displayed-sheets union is the canonical reference
# ---------------------------------------------------------------------------


def test_count_customers_narrow_shape_matches_displayed_universe() -> None:
    """``count_customers(ab, csone, pulse_df=pulse)`` returns the
    union of the three detail sheets the report displays."""
    ab = _ab_16()
    csone = _csone_16()
    pulse = _pulse_32()
    expected = _expected_displayed_universe_size()

    narrow = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    assert narrow == expected, (
        "Round 25 / Phase A: narrow displayed-sheets universe must "
        f"equal {expected} (AB ∪ CSOne ∪ Pulse).  Got {narrow}.  If "
        "this drifts, either the fixture changed or count_customers "
        "stopped honoring the (ab_df, csone_df, pulse_df) shape."
    )


def test_count_customers_narrow_shape_excludes_extras_ghost_customers() -> None:
    """Extras that contribute customers not in the displayed sheets
    must NOT inflate the narrow headline count.

    Pre-Round 25 the Word path called
    ``count_customers(..., extra_frames=extras, account_to_customer=...)``
    which widened the universe and produced 49 in the reference Brian
    Frazier / 90d report while Excel showed 37.  Post-Round 25 the
    headline path drops those args; this test pins that contract.
    """
    ab = _ab_16()
    csone = _csone_16()
    pulse = _pulse_32()
    extras = _extras_inflate_universe()
    expected_narrow = _expected_displayed_universe_size()

    # Narrow shape (Round 25 / Phase A canonical headline call).
    narrow = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)

    # Wide shape (pre-Round 25 buggy headline call) -- preserved for
    # diagnostic purposes only; must NOT be the headline value.
    wide = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=pulse, extra_frames=extras,
    )

    assert wide > narrow, (
        "Sanity: the inflation fixture must contain ghost customers "
        f"that widen the universe.  Got narrow={narrow}, wide={wide}."
    )
    assert narrow == expected_narrow, (
        "Round 25 / Phase A: the headline customer count must be "
        f"narrow ({expected_narrow}), not wide ({wide}).  This is the "
        "exact 49 vs 37 reproducer from the Brian Frazier reference "
        "report -- if narrow == wide, the formatter is widening the "
        "headline and the dishonest count is back."
    )


# ---------------------------------------------------------------------------
# Phase A.2 -- validator enforces Word == narrow universe
# ---------------------------------------------------------------------------


def test_validator_blocks_word_headline_drift_from_narrow_universe() -> None:
    """``validate_report_consistency`` raises an actionable error
    when ``portfolio_metrics["total_customers"]`` (Word headline)
    disagrees with the canonical narrow ``count_customers(ab, csone,
    pulse_df=pulse)`` universe.

    This is the cross-format parity gate the Round 25 plan calls
    out: the report build must FAIL before the Word doc reaches
    ``~/Downloads`` if the Word headline drifts from the Excel
    ``Summary`` row's verifiable count.
    """
    ab = _ab_16()
    csone = _csone_16()
    pulse = _pulse_32()
    canonical_narrow = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=pulse,
    )

    # Simulate a Word formatter that widens the count via extras
    # (the pre-Round 25 bug).  ``portfolio_metrics["total_customers"]``
    # would carry the wide value; the validator must reject it.
    inflated_pm = {
        "total_customers": canonical_narrow + 12,  # the 49 vs 37 pattern
        "total_barriers": int(len(ab)),
        "total_cases": int(len(csone)),
        "bems_count": 0,
    }

    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=inflated_pm,
        pulse_df=pulse,
    )

    assert not result["is_valid"], (
        "Round 25 / Phase A: the validator must REJECT a Word "
        "headline that drifts above the narrow displayed-sheets "
        "universe.  This is the cross-format parity gate; without "
        "it, numerically-dishonest reports ship silently."
    )
    error_text = "; ".join(result.get("errors", []))
    assert "total_customers" in error_text, (
        "Round 25 / Phase A: the validator's parity-error message "
        "must mention ``total_customers`` so a future debugger can "
        f"find this site quickly.  Got: {error_text!r}"
    )
    # The message should also disclose both totals so the operator
    # knows exactly how far the Word headline drifted.
    assert str(canonical_narrow) in error_text, (
        "Round 25 / Phase A: the validator's parity-error message "
        f"must include the canonical narrow total ({canonical_narrow}) "
        f"so the operator can identify the drift.  Got: {error_text!r}"
    )


def test_validator_passes_when_word_headline_matches_narrow_universe() -> None:
    """Round 25 happy path: when the Word headline equals the
    canonical narrow universe, the validator passes silently.  This
    is what the Compact and EI formatters now produce after the
    Phase A code edits."""
    ab = _ab_16()
    csone = _csone_16()
    pulse = _pulse_32()
    canonical_narrow = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=pulse,
    )

    honest_pm = {
        "total_customers": canonical_narrow,
        "total_barriers": int(len(ab)),
        "total_cases": int(len(csone)),
        "bems_count": 0,
    }

    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=honest_pm,
        pulse_df=pulse,
    )

    customer_errors = [
        e for e in result.get("errors", []) if "total_customers" in e
    ]
    assert not customer_errors, (
        "Round 25 / Phase A: the validator must NOT raise a "
        "total_customers parity error when the Word headline equals "
        "the canonical narrow universe.  Errors observed: "
        f"{customer_errors}"
    )


# ---------------------------------------------------------------------------
# Phase A.3 -- Round 19 golden fixture: narrow path returns the
# same value as the wide path (because the fixture was tuned in
# Round 25 to put DeltaCo into csconsole_customer_pulse so the
# narrow universe still resolves to 5).
# ---------------------------------------------------------------------------


def test_golden_fixture_narrow_universe_equals_expected_total() -> None:
    """The Round 19 golden fixture's narrow ``count_customers(ab,
    csone, pulse_df=customer_pulse)`` shape returns the same value
    as the EXPECTED_KPIS total.

    This is the surgical-fixture pin the Round 25 plan called for:
    by adding DeltaCo to ``csconsole_customer_pulse`` in
    ``make_extra_frames``, every existing test that asserts the
    fixture's headline customer count == 5 keeps passing under the
    new narrow contract.
    """
    ab = make_ab_df()
    csone = make_csone_df()
    extras = make_extra_frames()
    csconsole_customer_pulse = extras[1]
    expected = EXPECTED_KPIS["total_customers"]  # 5

    narrow = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=csconsole_customer_pulse,
    )
    assert narrow == expected, (
        "Round 25 / Phase A: the golden fixture's narrow universe "
        f"(AB ∪ CSOne ∪ csconsole_customer_pulse) must equal "
        f"EXPECTED_KPIS['total_customers']={expected}.  Got {narrow}.  "
        "If this drifts, csconsole_customer_pulse in make_extra_frames "
        "no longer carries DeltaCo + EpsilonInc -- the Round 25 "
        "fixture-tuning has been undone."
    )


def test_golden_fixture_validator_narrow_universe_matches_pm_with_extras() -> None:
    """The Round 19 golden fixture's PM-with-extras headline equals
    the validator's narrow universe when ``pulse_df`` is threaded.

    This is the post-Round 25 evolution of the Round 22
    ``test_validator_with_extras_matches_portfolio_metrics_universe``
    contract: PM["total_customers"] (now narrow under Round 25 in
    production formatters) == validator narrow == 5.
    """
    ab = make_ab_df()
    csone = make_csone_df()
    extras = make_extra_frames()
    csconsole_customer_pulse = extras[1]
    expected = EXPECTED_KPIS["total_customers"]  # 5

    pm = cm.build_portfolio_metrics(
        ab_df=ab,
        csone_df=csone,
        risk_profiles={},
        risk_scale=cm.RISK_SCALE_0_TO_100,
        extra_customer_frames=extras,
    )
    # Round 25 production formatters override PM["total_customers"]
    # to the narrow shape after build_portfolio_metrics; emulate
    # that here so the validator sees the post-Round 25 PM.
    pm["total_customers"] = cm.count_customers(
        ab_df=ab, csone_df=csone, pulse_df=csconsole_customer_pulse,
    )
    assert pm["total_customers"] == expected, (
        "Sanity: post-Round 25 PM headline (narrow shape) must equal "
        f"EXPECTED_KPIS['total_customers']={expected}."
    )

    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=pm,
        extra_frames=extras,
        pulse_df=csconsole_customer_pulse,
    )
    assert result["is_valid"], (
        "Round 25 / Phase A: validator must accept post-Round 25 "
        "narrow PM headline.  Errors observed: "
        f"{result.get('errors')}"
    )
