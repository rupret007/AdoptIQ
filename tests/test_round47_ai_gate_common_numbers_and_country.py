"""Round 47 / R47-AI-GATE-COMMON + R47-AI-GATE-COUNTRY -- regression
tests proving that the AI-narrative validator no longer rejects the
high-frequency benign patterns documented in the Build23 / Brian
Frazier comprehensive-report log (32 grounding withholds vs the
historical ~5 ceiling).

Diagnostic evidence (transcribed from
``~/.adoptiq/adoptiq.20569.log`` for run ``1777445582``):

- ``ungrounded_number': '12'`` cited in 21 of 32 customer storyboards
  (the validator's frozen-set stopped at 10 then jumped to 14, so the
  most common executive figure -- "12-month outlook" / "the past 12
  months" -- was always rejected).
- ``invented_entity': 'EQUITABLE HOLDINGS LLC'`` (and similar without
  the trailing ISO country-code token) cited in ~7 of 32 because the
  briefing's allow-list carried ``"EQUITABLE HOLDINGS LLC US"`` and
  the candidate-vs-allowed comparison was exact-equal only.

Both code paths are tightened to the diagnosed root causes only --
arbitrary five-digit numbers, ARR amounts, and unrelated invented
entities still trip the validator, so the safety property of the gate
is preserved.
"""

from __future__ import annotations

import pytest

import ai_narrative_validator as anv


# ---------------------------------------------------------------------------
# R47-AI-GATE-COMMON: months 11/12/13 (and other small calendar ints)
# now ground without needing to appear in the briefing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    [
        # The exact tokens the Build23 log captured as ``ungrounded_number``.
        "12",  # most common -- 21 of 32 rejections
        "11",
        "13",
        "15",
        "16",
        "17",
        "18",
        "19",
        "21",
        "22",
        "24",
        "26",
        "27",
        "28",
        "29",
        "31",
    ],
)
def test_round47_calendar_months_ground_without_briefing_match(literal: str) -> None:
    """Months 11-13 plus all small ints up to 31 should ground even if
    the briefing never prints them as a literal -- they are calendar
    facts not domain claims."""

    narrative = (
        "The portfolio outlook over the next "
        + literal
        + " months remains stable based on observed trends."
    )
    briefing = "Customer: Acme Corp. Adoption barriers: 0. TAC cases: 0."
    result = anv.validate_grounded_numbers(narrative, briefing)
    assert result.is_valid, f"Literal '{literal}' should have grounded; failures={result.failures} samples={result.sample_offending}"


@pytest.mark.parametrize(
    "literal",
    [
        # The exact percentages the Build23 log captured as ungrounded.
        "16.7%",
        "22.2%",
        "27.3%",
        "33.3%",
        "38.9%",
        "45.5%",
        "62.5%",
        "66.7%",
    ],
)
def test_round47_common_fractional_percentages_ground(literal: str) -> None:
    """A formatted percentage passes when the canonical briefing emits it."""

    narrative = "About " + literal + " of the affected items show the same root cause."
    briefing = f"Customer: Acme. Canonical affected-item rate: {literal}."
    result = anv.validate_grounded_numbers(narrative, briefing)
    assert result.is_valid, f"Percentage '{literal}' should have grounded; failures={result.failures} samples={result.sample_offending}"


def test_round47_unusual_number_still_ungrounded() -> None:
    """Safety property: an arbitrary 5-digit number with no briefing
    anchor must still be rejected, otherwise the validator would let
    the LLM smuggle in fabricated counts."""

    narrative = "The customer reports 47213 outstanding action items."
    briefing = "Customer: Acme Corp. Adoption barriers: 0. TAC cases: 0."
    result = anv.validate_grounded_numbers(narrative, briefing)
    assert not result.is_valid
    assert "ungrounded_number" in result.failures
    assert "47213" in (result.sample_offending.get("ungrounded_number") or "")


# ---------------------------------------------------------------------------
# R47-AI-GATE-COUNTRY: trailing country-code suffix tolerance for the
# entity allow-list.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "narrative_name,allowed_name",
    [
        # Real-world entries pulled verbatim from the Build23 log.
        ("Equitable Holdings LLC", "EQUITABLE HOLDINGS LLC US"),
        ("United Health Group", "UNITED HEALTH GROUP US"),
        ("Verisign Incorporated", "VERISIGN INCORPORATED US"),
        ("KRAFT GROUP LLC", "KRAFT GROUP LLC US"),
        ("Wintrust Financial Corporation", "WINTRUST FINANCIAL CORPORATION US"),
        ("The Charles Schwab Corporation", "THE CHARLES SCHWAB CORPORATION US"),
        # Reverse direction: narrative carries the suffix, allowed list does not.
        ("Acme Corp US", "ACME CORP"),
    ],
)
def test_round47_entity_country_code_tolerated(
    narrative_name: str, allowed_name: str
) -> None:
    """Trailing 2-letter ISO country codes on either the narrative
    candidate or the allowed-list entry must not produce
    ``invented_entity`` rejections after Round 47."""

    narrative = "We met with " + narrative_name + " yesterday to review their renewal."
    result = anv.validate_no_invented_entities(narrative, [allowed_name])
    assert result.is_valid, (
        f"'{narrative_name}' should match allowed '{allowed_name}'; "
        f"failures={result.failures} samples={result.sample_offending}"
    )


def test_round47_truly_invented_entity_still_rejected() -> None:
    """Safety property: a genuinely invented customer name (no country-
    code shenanigans, just made up) must still trip the validator."""

    narrative = "Initialscope Holdings Inc reported a downturn in usage."
    result = anv.validate_no_invented_entities(
        narrative, ["EQUITABLE HOLDINGS LLC US", "ACME CORP"]
    )
    assert not result.is_valid
    assert "invented_entity" in result.failures


def test_round47_too_short_substring_does_not_match() -> None:
    """Safety property: the substring fallback must not accept tiny
    overlaps like ``"Foo Inc"`` matching every allowed company that
    ends in ``Inc``."""

    narrative = "Foo Inc had a rough quarter."
    result = anv.validate_no_invented_entities(
        narrative, ["EQUITABLE HOLDINGS LLC US", "ACME CORP"]
    )
    assert not result.is_valid
    assert "invented_entity" in result.failures


# ---------------------------------------------------------------------------
# Combined: end-to-end ``validate_narrative`` pass for the most common
# log pattern from Build23.
# ---------------------------------------------------------------------------


def test_round47_build23_failure_pattern_now_passes() -> None:
    """The most common Build23 storyboard rejection pattern -- a
    customer narrative quoting "12 months" plus the customer's name
    without country code -- must now pass end-to-end through
    ``validate_narrative``."""

    narrative = (
        "Equitable Holdings LLC has shown stable adoption metrics over the "
        "past 12 months. Approximately 66.7% of escalated cases were "
        "resolved within SLA."
    )
    briefing = (
        "Customer: EQUITABLE HOLDINGS LLC US\n"
        "Cases: 9 (3 escalated; 2 resolved within SLA)\n"
    )
    result = anv.validate_narrative(
        narrative, briefing, allowed_entities=["EQUITABLE HOLDINGS LLC US"]
    )
    assert result.is_valid, (
        "Build23-style narrative should now ground; "
        f"failures={result.failures} samples={result.sample_offending}"
    )
