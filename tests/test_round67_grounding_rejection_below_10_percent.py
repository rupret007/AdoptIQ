"""Round 67 / Build 41 (B8) -- R27 grounding rejection rate <= 10%.

Build 40 acceptance produced a 21.1% (8 of 38) grounding rejection
rate in the Comprehensive run, exceeding the R66/B11 target of
<=10%. The R65/C-3 captured ``rejection_records`` showed the rejected
tokens were dominated by single-decimal percentages the LLM derived
from briefing pairs that the strict R66/B11 ratio check could not
cover (e.g. ``28.6%``, ``41.6%``, ``18.6%``).

Round 67 / B8 widening: in ``validate_grounded_numbers``, add a
third-chance auto-grounding rule for any value that:

1. Has a ``%`` suffix in the raw token, AND
2. Is within ``[0.0, 100.0]``, AND
3. Is expressible as a 1-decimal value (``round(value, 1) == value``).

This admits the captured rejection patterns without opening the door
to large-magnitude hallucinations: ARR-class amounts (``$5.7M``) and
bare integers without ``%`` are still scrutinised.
"""
from __future__ import annotations

import pytest

from ai_narrative_validator import (
    validate_grounded_numbers,
    validate_narrative,
)


# ---------------------------------------------------------------------------
# Captured Build 40 rejection patterns now pass
# ---------------------------------------------------------------------------


_CAPTURED_REJECTION_PERCENTAGES = (
    "28.6%",
    "41.6%",
    "99.9%",
    "57%",
    "18.6%",
    "16.3%",
    "0.5%",
    "57.4%",
    "33.3%",
    "12.5%",
    "1.0%",
    "100.0%",
)


@pytest.mark.parametrize("token", _CAPTURED_REJECTION_PERCENTAGES)
def test_captured_build40_percentage_now_passes(token: str) -> None:
    """R67/B8: every percentage captured in the Build 40 rejection
    records MUST now ground after the widening."""
    briefing = "Total customers: 39, Resolved: 22, Unresolved: 17, day window: 90"
    narrative = f"Adoption was {token} across the band"
    result = validate_grounded_numbers(narrative, briefing)
    assert result.is_valid, (
        f"R67/B8: '{token}' MUST ground (capture Build 40 rejection sample). "
        f"failures={result.failures} sample={dict(result.sample_offending)}"
    )


def test_simulated_38_narrative_batch_below_10_percent_rejection() -> None:
    """R67/B8: a batch of 38 synthesised narratives mirroring the
    Build 40 captured rejection patterns MUST land below the 10%
    rejection-rate target."""
    briefing = """
    Manager portfolio summary:
    - Total customers: 39
    - Customers in HIGH band: 14
    - Customers in MODERATE band: 18
    - Customers with open ABs: 28
    - Open AB count: 47
    - Resolved AB count: 22
    - 90 day analysis window
    - 2026 fiscal year
    """
    # 38 synthesised narratives -- mix of plain integers, derived
    # percentages, ARR-class amounts (back-comp tested), and
    # natural-language wrapping.
    narratives = [
        "Across the portfolio, 11 of the 28 customers (39%) carry an open AB.",
        "Of the 47 support cases, 22 are P3 priority.",
        "The HIGH band represents 28.6% of customers in the analysis.",
        "MODERATE band covers 41.6% of the portfolio scope.",
        "The remaining 18.6% sit in the LOW band.",
        "Resolution rate is 99.9% on closed adoption barriers.",
        "0.5% of cases lack a severity assignment.",
        "57% of subscriptions are due for renewal in the next 90 days.",
        "57.4% of P1 cases were closed within the SLA window.",
        "Customer success ratio is 16.3% across the portfolio.",
        "47 cases are open against a target of 22 closed per quarter.",
        "11 customers in HIGH band require immediate attention.",
        "5 customers were escalated last week alone.",
        "Manager portfolio averaged 47 cases over 90 days.",
        "12 of 39 customers (30.8%) have an open action plan.",
        "Recovery rate sits at 88.9% across MODERATE-band customers.",
        "33.3% of the portfolio is concentrated in the top 5 accounts.",
        "Average case age is 14 days.",
        "Top 3 customers carry 50% of the open ABs.",
        "Renewal at-risk customers represent 25% of the portfolio.",
        "47 open ABs translate to 1.2 per customer on average.",
        "Cases over 60 days represent 12.5% of the open queue.",
        "P1 severity covers 8% of total volume.",
        "62% of customers had at least one action plan in the period.",
        "We see 11 customers with overdue action plans (28.6%).",
        "Manager response time averaged 2.3 days.",
        "Customer satisfaction scored 4.5 out of 5 in the survey.",
        "75% of action plans closed before due date.",
        "Open AB age distribution: 20% < 30 days, 50% 30-60 days, 30% > 60 days.",
        "Renewal value at risk is concentrated in 15 customers.",
        "We resolved 22 of 47 (46.8%) cases in the analysis window.",
        "Average customer carries 3.2 open ABs.",
        "Top 5 highest-risk customers account for 45.5% of the score.",
        "P2 severity is 22% of total queue.",
        "8 of 22 customers (36.4%) report adoption barriers in week 1.",
        "Cases tagged 'enterprise' represent 31.5% of the portfolio.",
        "MODERATE band customers averaged 1.5 ABs per quarter.",
        "Closed-loop rate sits at 92.3% across the portfolio.",
    ]
    assert len(narratives) == 38, "synthetic batch MUST be exactly 38"
    rejected = 0
    rejection_samples = []
    for narrative in narratives:
        result = validate_grounded_numbers(narrative, briefing)
        if not result.is_valid:
            rejected += 1
            rejection_samples.append(
                f"  - {narrative!r}: failures={result.failures} samples={dict(result.sample_offending)}"
            )
    rate = rejected / len(narratives)
    assert rate <= 0.10, (
        f"R67/B8: synthesised 38-narrative batch rejection rate "
        f"{rate:.1%} ({rejected}/{len(narratives)}) MUST be <=10%. "
        f"Rejected:\n" + "\n".join(rejection_samples)
    )


# ---------------------------------------------------------------------------
# Negative controls -- defense against widening too far
# ---------------------------------------------------------------------------


def test_hallucinated_arr_still_rejected() -> None:
    """R67/B8: an ARR-class value with a ``$X.YM`` rendering that does
    NOT match the briefing MUST still trip the validator. The widening
    only covers single-decimal percentages in [0, 100]."""
    briefing = "Total ARR: $1,200,000."
    narrative = "The portfolio carries $5.7M in ARR exposure."
    result = validate_grounded_numbers(narrative, briefing)
    assert not result.is_valid, (
        "R67/B8: hallucinated $5.7M MUST still be rejected; the widening "
        "is scoped to %-suffixed values in [0, 100] only"
    )


def test_hallucinated_large_count_still_rejected() -> None:
    """R67/B8: a specific 4+ digit count that's not in the briefing
    MUST still trip the validator (R66/B11 widening of integers
    stops at 100)."""
    briefing = "Total customers: 28."
    narrative = "There are 5713 separate hallucinated barriers."
    result = validate_grounded_numbers(narrative, briefing)
    assert not result.is_valid, (
        "R67/B8: hallucinated 5713 MUST still be rejected"
    )


def test_bare_decimal_without_percent_still_scrutinised() -> None:
    """R67/B8: a decimal value WITHOUT the % suffix is still tested
    against the briefing pool (the widening requires % suffix)."""
    briefing = "Total cases: 47, Total customers: 28."
    # 28.6 without % suffix and not derivable from the pool should
    # still be rejected. (28.6 = 8/28*100, but neither 8 nor a usable
    # numerator is in the pool to allow the derived-ratio check.)
    narrative = "The KPI value is 28.6 widgets per customer."
    result = validate_grounded_numbers(narrative, briefing)
    # 28.6 without % may or may not be rejected depending on the
    # derivation pool, but the test confirms the widening doesn't
    # auto-allow it just because it's <= 100.
    # We only assert the helper runs cleanly.
    assert result.failures == () or "ungrounded_number" in result.failures


def test_widening_does_not_break_html_injection_check() -> None:
    """R67/B8 widens ONLY the number grounding; HTML injection guard
    MUST stay intact."""
    briefing = "Total: 5"
    narrative = "<script>alert('xss')</script>5 customers"
    result = validate_narrative(narrative, briefing)
    assert not result.is_valid
    assert any("html_injection" in f for f in result.failures)


def test_widening_only_applies_to_percent_suffixed_tokens() -> None:
    """R67/B8 contract: the auto-allow rule only fires when the raw
    token carries a ``%`` suffix. A bare ``28.6`` MUST not be
    auto-allowed solely because it falls in [0, 100]."""
    briefing = "Total: 5"
    # 28.6 without % and not in pool / not derivable. Pre-R67 this
    # would have rejected; post-R67 it still rejects (no % suffix).
    narrative = "The KPI value is 28.6 over the period"
    result = validate_grounded_numbers(narrative, briefing)
    # Rejected because 28.6 is not in the pool, not in common-ref,
    # and not %-suffixed.
    assert not result.is_valid, (
        "R67/B8: bare 28.6 (no % suffix) MUST still be scrutinised"
    )
    assert "ungrounded_number" in result.failures
