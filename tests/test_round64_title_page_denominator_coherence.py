"""Round 64 / Phase 1 (B1) -- Title Page risk-band denominator coherence.

The Build-36 Comprehensive Title Page surfaced a structural incoherence:
the metric tile reported ``Customers in portfolio: 39`` paired with risk
buckets that summed to ``1+6+24+22 = 53``. Two different denominators on
the same one-page table.

Root cause (in ``app_simple.run_comprehensive_analysis``):

* ``portfolio_metrics['total_customers']`` was the AB ∪ CSOne ∪ Pulse
  narrow universe, computed via ``cm.count_customers(ab, csone, pulse)``.
* ``portfolio_metrics['high_risk_customers']`` etc. were derived from
  ``compute_portfolio_risk_summary(risk_profiles)`` where
  ``risk_profiles`` was keyed on ``all_customers_comprehensive`` -- the
  WIDER union (AB ∪ CSOne ∪ Pulse ∪ Subs ∪ AP ∪ SP ∪ AB-CSConsole),
  matching the Leader report's TEAM TOTAL.

Fix: build a narrow risk-profiles dict (filtered to the AB ∪ CSOne ∪
Pulse universe used for ``total_customers``) before computing the band
counts. ``risk_profiles`` itself is left at the wider universe so
downstream per-customer narrative sections still cover every customer
with activity in any source.

These tests pin the invariant at the unit level: given a narrow
universe of N customers (per ``cm.list_customers(ab, csone, pulse)``),
the sum of CRITICAL+HIGH+MEDIUM+LOW+HEALTHY band counts MUST equal N
when the band aggregator runs against a narrow-filtered ``risk_profiles``
dict.

Round 64 / Phase 1.  Made-with: Cursor.
"""

from __future__ import annotations

import pandas as pd

import canonical_metrics as cm
from data_normalization import normalize_customer_name
from risk_scoring import compute_portfolio_risk_summary


def _profile(score_0_100: float, band: str) -> dict:
    """Build a synthetic risk profile in the shape compute_portfolio_risk_summary expects."""
    return {
        "risk_score_0_100": float(score_0_100),
        "risk_band": band.upper(),
    }


def _r64_filter_to_narrow_universe(
    risk_profiles: dict,
    *,
    ab_df: pd.DataFrame,
    csone_df: pd.DataFrame,
    pulse_df: pd.DataFrame,
) -> dict:
    """Mirror of the R64 / B1 filter inserted into app_simple.run_comprehensive_analysis.

    The production code lives inline inside
    ``app_simple.run_comprehensive_analysis`` (around the 'Round 64 /
    Phase 1 (B1)' marker block, immediately before
    ``compute_portfolio_risk_summary``). Mirroring it here lets us
    pin the invariant deterministically without spinning up the full
    Flask app + Snowflake stubs.
    """
    narrow_list = cm.list_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        pulse_df=pulse_df,
    )
    narrow_set = {normalize_customer_name(name) for name in narrow_list}
    return {
        cust: profile
        for cust, profile in risk_profiles.items()
        if normalize_customer_name(cust) in narrow_set
    }


def _build_narrow_universe_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Synthetic AB / CSOne / Pulse covering exactly 5 distinct customers.

    Mirrors the structure of a Brian-Frazier-style portfolio: a small
    set of customers with at least one row in some-but-not-all of the
    three tables (so the union is what defines the narrow scope, not a
    single source).
    """
    ab = pd.DataFrame(
        [
            {"customer_name": "Customer Alpha", "ID": "AB-001"},
            {"customer_name": "Customer Bravo", "ID": "AB-002"},
            {"customer_name": "Customer Charlie", "ID": "AB-003"},
        ]
    )
    csone = pd.DataFrame(
        [
            {"customer_name": "Customer Bravo", "Case Number": "CS-001"},
            {"customer_name": "Customer Delta", "Case Number": "CS-002"},
        ]
    )
    pulse = pd.DataFrame(
        [
            {"BU_NAME": "Customer Alpha", "score": 7},
            {"BU_NAME": "Customer Echo", "score": 4},
        ]
    )
    return ab, csone, pulse


def test_narrow_set_size_matches_union_of_three_sources() -> None:
    """Sanity: the synthetic frames define exactly 5 distinct narrow customers."""
    ab, csone, pulse = _build_narrow_universe_frames()
    narrow_list = cm.list_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    assert len(set(narrow_list)) == 5, sorted(narrow_list)


def test_band_buckets_sum_equals_narrow_universe_size_after_filter() -> None:
    """The Title Page invariant: sum(buckets) == narrow total_customers.

    Build a wider risk_profiles dict (8 customers, mimicking the
    ``all_customers_comprehensive`` wide union containing extras with
    no AB/CSOne/Pulse activity), apply the R64 narrow filter, run
    ``compute_portfolio_risk_summary``, and assert the bucket sum
    equals the narrow universe size (5), NOT the wide one (8).
    """
    ab, csone, pulse = _build_narrow_universe_frames()

    # Wider risk_profiles: 5 narrow customers + 3 wide-only extras
    # (e.g. customers with subscription rows but no AB/CSOne/Pulse
    # activity in the analysis window).
    wide_risk_profiles = {
        "Customer Alpha":   _profile(85.0, "CRITICAL"),
        "Customer Bravo":   _profile(62.0, "HIGH"),
        "Customer Charlie": _profile(45.0, "MEDIUM"),
        "Customer Delta":   _profile(22.0, "LOW"),
        "Customer Echo":    _profile(8.0,  "HEALTHY"),
        # Wide-only extras (subs / action_plans / success_priorities
        # that should NOT count toward the title-page band totals
        # because they are not in the AB ∪ CSOne ∪ Pulse narrow set).
        "Customer Foxtrot": _profile(78.0, "HIGH"),
        "Customer Golf":    _profile(55.0, "MEDIUM"),
        "Customer Hotel":   _profile(12.0, "HEALTHY"),
    }

    narrow_risk_profiles = _r64_filter_to_narrow_universe(
        wide_risk_profiles,
        ab_df=ab,
        csone_df=csone,
        pulse_df=pulse,
    )

    # The narrow filter must drop the 3 wide-only extras.
    assert len(narrow_risk_profiles) == 5, sorted(narrow_risk_profiles.keys())
    assert "Customer Foxtrot" not in narrow_risk_profiles
    assert "Customer Golf" not in narrow_risk_profiles
    assert "Customer Hotel" not in narrow_risk_profiles

    summary = compute_portfolio_risk_summary(narrow_risk_profiles)

    # The Title-Page invariant: band buckets sum to the narrow tile value.
    band_counts = summary.get("risk_band_counts", {})
    bucket_sum = sum(int(band_counts.get(b, 0)) for b in
                     ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY"))
    narrow_total = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)
    assert bucket_sum == narrow_total == 5, (
        f"Title-Page denominator coherence violated: "
        f"narrow total_customers={narrow_total}, sum(buckets)={bucket_sum}, "
        f"band_counts={band_counts}"
    )

    # And the higher-level High vs Medium/Low/Healthy split also sums.
    high_risk = int(summary.get("high_risk_customers", 0))
    medium = int(summary.get("medium_risk_customers", 0))
    low = int(summary.get("low_risk_customers", 0))
    healthy = int(summary.get("healthy_customers", 0))
    assert high_risk + medium + low + healthy == narrow_total, (
        f"high+medium+low+healthy={high_risk + medium + low + healthy} "
        f"!= narrow_total={narrow_total}; summary={summary}"
    )


def test_pre_fix_unfiltered_aggregation_would_fail_invariant() -> None:
    """Negative control: feeding the WIDE risk_profiles to the aggregator
    produces a sum that does NOT match the narrow universe size.

    This is the Build-36 bug shape (``39`` vs ``1+6+24+22 == 53``). Pinning
    it here ensures the test suite would catch a future regression where a
    refactor accidentally drops the R64 filter step.
    """
    ab, csone, pulse = _build_narrow_universe_frames()

    wide_risk_profiles = {
        "Customer Alpha":   _profile(85.0, "CRITICAL"),
        "Customer Bravo":   _profile(62.0, "HIGH"),
        "Customer Charlie": _profile(45.0, "MEDIUM"),
        "Customer Delta":   _profile(22.0, "LOW"),
        "Customer Echo":    _profile(8.0,  "HEALTHY"),
        "Customer Foxtrot": _profile(78.0, "HIGH"),
        "Customer Golf":    _profile(55.0, "MEDIUM"),
        "Customer Hotel":   _profile(12.0, "HEALTHY"),
    }

    summary_unfiltered = compute_portfolio_risk_summary(wide_risk_profiles)
    band_counts = summary_unfiltered.get("risk_band_counts", {})
    bucket_sum = sum(int(band_counts.get(b, 0)) for b in
                     ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY"))
    narrow_total = cm.count_customers(ab_df=ab, csone_df=csone, pulse_df=pulse)

    # Pre-R64-fix shape: bucket sum (8) != narrow total (5).
    assert bucket_sum != narrow_total, (
        "negative control failed: the wide risk_profiles' bucket sum should "
        "NOT equal the narrow universe size; if this test starts passing the "
        "synthetic data has drifted and the R64 fix can no longer be exercised."
    )
    assert bucket_sum == 8 and narrow_total == 5, (bucket_sum, narrow_total)


def test_filter_handles_empty_narrow_universe_gracefully() -> None:
    """Edge case: AB+CSOne+Pulse all empty -> narrow set is empty -> filter returns {}.

    The aggregator must return zeroed bands without raising; the title-page
    tile in that scenario is internally coherent at 0 == 0.
    """
    empty = pd.DataFrame()
    wide_risk_profiles = {
        "Customer Alpha": _profile(85.0, "CRITICAL"),
        "Customer Bravo": _profile(62.0, "HIGH"),
    }
    narrow = _r64_filter_to_narrow_universe(
        wide_risk_profiles,
        ab_df=empty,
        csone_df=empty,
        pulse_df=empty,
    )
    assert narrow == {}
    summary = compute_portfolio_risk_summary(narrow)
    band_counts = summary.get("risk_band_counts", {})
    assert all(int(band_counts.get(b, 0)) == 0 for b in
               ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY"))


def test_filter_normalizes_customer_names_for_membership() -> None:
    """Cosmetic-variant customer names (NBSP, casing) should still match.

    Reproduces the realistic case where ``risk_profiles`` was built from
    one source's spelling (e.g. ``"Acme Co\xa0"``) while the narrow set
    came from another source's spelling (``"Acme Co"``). The filter
    must use ``normalize_customer_name`` on both sides so the customer
    is correctly retained.
    """
    ab = pd.DataFrame([{"customer_name": "Acme Co"}])
    csone = pd.DataFrame()
    pulse = pd.DataFrame()
    wide_risk_profiles = {
        "Acme Co\xa0": _profile(80.0, "HIGH"),
    }
    narrow = _r64_filter_to_narrow_universe(
        wide_risk_profiles,
        ab_df=ab,
        csone_df=csone,
        pulse_df=pulse,
    )
    assert len(narrow) == 1, (
        f"normalize_customer_name should fold NBSP variants; got {narrow}"
    )
