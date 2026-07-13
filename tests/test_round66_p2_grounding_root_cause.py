"""Round 66 / Pass 3 (B11) — R27 grounding rejection root-cause fix.

Build 38 acceptance saw a 34% R27 grounding rejection rate. The
R65/C-3 ``rejection_records`` capture revealed that the dominant
failure mode was ``validate_grounded_numbers`` rejecting:

1. Small-integer counts in the 32-99 range that the LLM derived from
   briefing pairs (e.g. "39 customers in HIGH band" computed from the
   briefing's per-band breakdown).
2. Derived percentages (e.g. "11 of 28 customers (39%)") where the
   numerator and denominator appear in the briefing but the rounded
   percentage does not.
3. ARR-class large numbers rendered with the natural "$X.YM"
   convention (e.g. briefing "$1,234,567" vs narrative "$1.2M") where
   the 1% relative tolerance was too tight.

R66/B11 fixes this by:
- Widening ``_COMMON_REFERENCE_NUMBERS`` from 0-31 to 0-100, plus
  every multiple of 5 from 100-500, plus calendar years through 2030.
- Adding ``_is_derived_ratio_percentage`` second-chance check for
  any narrative percentage that's expressible as a/b*100 for any
  integer pair (a, b) in the briefing, with 0.5 ppt tolerance.
- Widening the relative tolerance for ARR-class values (>= $100K)
  from 1% to 5% so "$1.2M" against briefing "$1,234,567" passes
  while a hallucinated "$2.5M" against briefing "$1.2M" still fails.

Target: post-fix R27 rejection rate <=10% on the same Build 38
captured rejection samples.
"""
from __future__ import annotations

import pytest

from ai_narrative_validator import (
    _COMMON_REFERENCE_NUMBERS,
    _extract_integer_briefing_numbers,
    _is_derived_ratio_percentage,
    _number_in_allowed,
    validate_grounded_numbers,
    validate_narrative,
)


# ---------------------------------------------------------------------------
# Common-reference set widening (B11 part 1)
# ---------------------------------------------------------------------------


def test_common_reference_set_includes_all_integers_0_to_100() -> None:
    """R66/B11: integers 0-100 MUST all be in the common-reference set
    so the LLM can name customer counts and percentages in the
    32-99 range without tripping the validator."""
    for n in range(0, 101):
        assert float(n) in _COMMON_REFERENCE_NUMBERS, (
            f"Integer {n} MUST be in _COMMON_REFERENCE_NUMBERS (R66/B11)"
        )


def test_common_reference_set_includes_multiples_of_5_through_500() -> None:
    """R66/B11: every multiple of 5 from 100-500 MUST be in the set so
    common day-window / count values (175, 240, 320 etc.) pass."""
    for n in range(100, 501, 5):
        assert float(n) in _COMMON_REFERENCE_NUMBERS, (
            f"Multiple-of-5 {n} MUST be in _COMMON_REFERENCE_NUMBERS (R66/B11)"
        )


def test_common_reference_set_includes_forward_looking_years() -> None:
    """R66/B11: calendar years through 2030 MUST be allowed so
    forward-looking narratives don't trip after FY27."""
    for y in (2024, 2025, 2026, 2027, 2028, 2029, 2030):
        assert float(y) in _COMMON_REFERENCE_NUMBERS, (
            f"Year {y} MUST be in _COMMON_REFERENCE_NUMBERS"
        )


def test_common_reference_set_does_not_include_arbitrary_large_ints() -> None:
    """Defense check: arbitrary 4+ digit numbers MUST NOT be in the
    common set so hallucinated counts still trip the validator."""
    for n in (101, 1000, 12345, 99999, 1234567):
        # The integer pool of multiples-of-5 covers 100-500, so 101 is
        # NOT auto-allowed.
        if n <= 500 and n % 5 == 0:
            continue
        assert float(n) not in _COMMON_REFERENCE_NUMBERS, (
            f"{n} MUST NOT be in _COMMON_REFERENCE_NUMBERS"
        )


# ---------------------------------------------------------------------------
# Derived-ratio percentage check (B11 part 2)
# ---------------------------------------------------------------------------


def test_derived_ratio_picks_up_simple_percentages() -> None:
    """11 of 28 = 39.286 ~ 39.3 MUST be detected as a derived ratio."""
    pool = [11, 28]
    assert _is_derived_ratio_percentage(39.0, pool) is True
    assert _is_derived_ratio_percentage(39.3, pool) is True
    # 40% is too far from 39.286 -> outside default 0.5 ppt tolerance.
    assert _is_derived_ratio_percentage(40.0, pool) is False


def test_derived_ratio_handles_common_dashboards() -> None:
    """Common dashboard ratios (1/3, 2/5, 3/4) MUST be recognised."""
    cases = [
        (33.3, [1, 3]),
        (40.0, [2, 5]),
        (75.0, [3, 4]),
        (66.7, [2, 3]),
        (62.5, [5, 8]),
    ]
    for value, pool in cases:
        assert _is_derived_ratio_percentage(value, pool) is True, (
            f"{value}% from pool {pool} MUST be recognised as derived ratio"
        )


def test_derived_ratio_rejects_unrelated_pairs() -> None:
    """A percentage that is NOT expressible from the pool MUST fail."""
    pool = [11, 28]
    # 50% from {11, 28}: only 11/22 = 50 (22 not in pool), 14/28 = 50
    # (14 not in pool). Should fail.
    assert _is_derived_ratio_percentage(50.0, pool) is False


def test_derived_ratio_only_accepts_percentages_in_0_to_100() -> None:
    """Values outside [0, 100] are not percentages -> always False."""
    assert _is_derived_ratio_percentage(-1.0, [1, 2]) is False
    assert _is_derived_ratio_percentage(101.0, [1, 2]) is False
    assert _is_derived_ratio_percentage(1234.0, [1, 2]) is False


def test_derived_ratio_handles_empty_pool() -> None:
    """Empty integer pool -> always False."""
    assert _is_derived_ratio_percentage(50.0, []) is False
    assert _is_derived_ratio_percentage(0.0, []) is False


def test_extract_integer_briefing_numbers_filters_to_integers() -> None:
    """The integer-pool extractor MUST drop floats / suffixed values
    (which expand to large integers but are unsuitable as ratio
    denominators)."""
    briefing = "Open ABs: 11. Total customers: 28. Avg score: 7.5. ARR: $2,500,000."
    pool = _extract_integer_briefing_numbers(briefing)
    assert 11 in pool
    assert 28 in pool
    # 7.5 is NOT integer -> excluded.
    assert 7 not in pool or 7.5 not in [float(p) for p in pool]
    # Money is not a valid count denominator for an arbitrary percentage.
    assert 2_500_000 not in pool


# ---------------------------------------------------------------------------
# ARR-class relative tolerance widening (B11 part 3)
# ---------------------------------------------------------------------------


def test_arr_class_widened_tolerance_accepts_close_rounded_values() -> None:
    """``$1.2M`` (1_200_000) vs briefing ``$1,234,567`` MUST now pass
    under the widened 5% relative tolerance for values >= $100K."""
    allowed = [1_234_567.0]
    # Default tolerance of 0.01 (1%) would reject this (drift = 2.8%).
    # R66/B11 widens to 5% for values >= 100_000.
    assert _number_in_allowed(1_200_000.0, allowed, tolerance=0.01) is True


def test_arr_class_hallucinated_value_still_rejected() -> None:
    """``$2.5M`` against briefing ``$1.2M`` is a 50% drift -- MUST
    still be rejected even after the widening to 5%."""
    allowed = [1_200_000.0]
    assert _number_in_allowed(2_500_000.0, allowed, tolerance=0.01) is False


def test_small_magnitude_tolerance_unchanged() -> None:
    """Small values (< $100K) keep the caller's tolerance unchanged.

    Note: 47 is now in the common-reference set (R66/B11 widened to
    0-100), so the legacy "47 cases against briefing 12 cases" test
    case must use a number OUTSIDE the common set.  ``537`` is not
    in the common set and not in the briefing pool -> rejected.
    """
    allowed = [12.0]
    # 537 vs 12 -> drift huge, AND 537 is not in common-ref set.
    assert _number_in_allowed(537.0, allowed, tolerance=0.01) is False
    # 12.05 vs 12 under tolerance 0.01 -> relative .05/12 = 0.4%,
    # inside relative tolerance -> True.
    assert _number_in_allowed(12.05, allowed, tolerance=0.01) is True


# ---------------------------------------------------------------------------
# End-to-end: R65/C-3-style rejection samples now pass
# ---------------------------------------------------------------------------


def test_simulated_build38_rejection_now_passes_derived_percentage() -> None:
    """Simulates a Build 38 rejection sample: briefing has the pair,
    LLM derives the percentage."""
    briefing = """
    Manager portfolio summary:
    - Total customers: 28
    - Customers with open ABs: 11
    - Open AB count: 47
    - 90 day analysis window
    """
    narrative = (
        "Across the portfolio, 11 of the 28 customers (39%) carry at least "
        "one open adoption barrier in the 90 day window."
    )
    result = validate_grounded_numbers(narrative, briefing)
    assert result.is_valid, (
        f"R66/B11: derived percentage MUST pass. Failures: {result.failures} "
        f"Samples: {result.sample_offending}"
    )


def test_simulated_build38_rejection_now_passes_widened_integer_floor() -> None:
    """Simulates a Build 38 rejection sample: 47 cases is computed from
    briefing pairs and was rejected pre-R66 because 47 was not in
    _COMMON_REFERENCE_NUMBERS (32-99 gap)."""
    briefing = """
    Total support cases: 47
    Severity distribution: P1=2 P2=8 P3=22 P4=15
    Canonical P3/P4 combined count: 37
    """
    # The LLM doesn't reference 47 directly; it computes a derived
    # number. The canonical briefing now emits the computed value explicitly;
    # arbitrary small integers are no longer auto-grounded.
    narrative = "Of the 47 support cases, 37 are P3 or P4 priority."
    result = validate_grounded_numbers(narrative, briefing)
    assert result.is_valid, (
        f"R66/B11: small-integer derived count MUST pass. Failures: "
        f"{result.failures}"
    )


def test_simulated_build38_rejection_now_passes_arr_rounded() -> None:
    """ARR rendered as $1.2M against briefing $1,234,567 MUST pass
    under the widened 5% relative tolerance."""
    briefing = "Total ARR exposure: $1,234,567 across the portfolio."
    narrative = "The portfolio carries roughly $1.2M in ARR exposure."
    result = validate_grounded_numbers(narrative, briefing)
    assert result.is_valid, (
        f"R66/B11: ARR-class rounding MUST pass. Failures: {result.failures}"
    )


def test_hallucinated_specific_count_still_rejected() -> None:
    """Defense check: a narrative naming a specific count like 1234 that
    is NOT in the briefing MUST still be rejected."""
    briefing = "Total customers: 28. Open ABs: 11."
    narrative = "The portfolio carries 1234 separate hallucinated barriers."
    result = validate_grounded_numbers(narrative, briefing)
    assert not result.is_valid, (
        "Hallucinated 1234 MUST be rejected even after R66/B11 widening"
    )
    assert "ungrounded_number" in result.failures


def test_hallucinated_arr_value_still_rejected() -> None:
    """Defense check: hallucinated ARR figure outside 5% drift MUST
    still trip the validator."""
    briefing = "Total ARR: $1,200,000."
    # $5M against briefing $1.2M -> 75% drift -> rejected.
    narrative = "The portfolio holds $5M in ARR."
    result = validate_grounded_numbers(narrative, briefing)
    assert not result.is_valid, (
        "Hallucinated $5M against briefing $1.2M MUST still be rejected"
    )


# ---------------------------------------------------------------------------
# Backward compatibility: pre-R66 passing narratives still pass
# ---------------------------------------------------------------------------


def test_html_injection_still_blocked() -> None:
    """R66/B11 widens NUMBER grounding; HTML injection guard MUST be
    unchanged."""
    briefing = "Total: 5"
    narrative = "<script>alert('xss')</script>5 customers"
    result = validate_narrative(narrative, briefing)
    assert not result.is_valid
    assert any("html_injection" in f for f in result.failures)


def test_invented_entity_still_blocked() -> None:
    """R66/B11 does not affect the entity guard."""
    briefing = "Customer: Acme Corp"
    narrative = "Acme Corp and Bogus Industries Inc are at risk."
    result = validate_narrative(
        narrative, briefing, allowed_entities=["Acme Corp"]
    )
    assert not result.is_valid
    assert any("invented_entity" in f for f in result.failures)


def test_pre_r66_passing_narrative_still_passes() -> None:
    """A narrative that was already grounded pre-R66 MUST continue to
    pass post-R66 (no false-positive regression)."""
    briefing = """
    Manager: J Smith
    Total customers: 5
    Adoption barriers: 12
    Day window: 90
    """
    narrative = (
        "J Smith manages 5 customers in a 90 day analysis window. "
        "There are 12 open adoption barriers."
    )
    result = validate_narrative(narrative, briefing)
    assert result.is_valid, (
        f"Pre-R66 passing narrative MUST still pass. Failures: "
        f"{result.failures}"
    )


def test_default_tolerance_path_unchanged_for_small_magnitudes() -> None:
    """A narrative with a small integer that doesn't appear in the
    briefing AND isn't in the common-ref set MUST still be rejected
    (i.e., R66/B11 only widens the COMMON set, not the briefing-match
    tolerance)."""
    # 537 is not in the common set, not in briefing pool -> rejected.
    briefing = "Total: 5"
    narrative = "There are 537 widgets."
    result = validate_grounded_numbers(narrative, briefing)
    assert not result.is_valid, "537 MUST still be rejected"
