"""Round 16 / Phase 3 -- AI narrative validator tests.

Pins the contract of ``ai_narrative_validator``:

- ``validate_no_html_injection`` rejects HTML / JS injection vectors.
- ``validate_grounded_numbers`` rejects numbers absent from the
  briefing while accepting common-reference numbers.
- ``validate_no_invented_entities`` rejects customer-name candidates
  not in the allow-list.
- ``validate_narrative`` aggregates the three.
- The wiring in ``app_simple.py`` substitutes the placeholder when
  validation fails, never silently passes the LLM text through.

The tests are deterministic: no live LLM calls, no time-of-day
randomness, no HTTP.  Fixture briefings are inline strings.
"""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

import pytest

import ai_narrative_validator as anv


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 3.1 -- HTML / JS injection.
# ---------------------------------------------------------------------------


def test_phase_3_1_accepts_clean_text():
    res = anv.validate_no_html_injection("This portfolio has 12 customers.")
    assert res.is_valid
    assert res.failures == ()


@pytest.mark.parametrize(
    ("payload", "label"),
    [
        ("<script>alert(1)</script>", "html_injection:script_tag"),
        ("<SCRIPT src='/x'></script>", "html_injection:script_tag"),
        ("<iframe src='evil'>", "html_injection:iframe_tag"),
        ("<svg onload=alert(1)>", "html_injection:svg_tag"),
        ("<object data='evil'></object>", "html_injection:object_tag"),
        ("<embed src='evil'>", "html_injection:embed_tag"),
        ('<a onclick="x()" href="#">click</a>', "html_injection:on_event_handler"),
        ('Visit javascript:alert(1)', "html_injection:javascript_url"),
        ("Open data:text/html;base64,PHNjcmlwdD4=", "html_injection:data_url_html"),
    ],
)
def test_phase_3_1_rejects_each_injection_vector(payload, label):
    res = anv.validate_no_html_injection(payload)
    assert not res.is_valid
    assert label in res.failures, f"Expected failure {label}, got {res.failures}"
    assert label in res.sample_offending


def test_phase_3_1_handles_none_and_empty_safely():
    """Defensive: validator must not crash on None / empty."""
    assert anv.validate_no_html_injection(None).is_valid
    assert anv.validate_no_html_injection("").is_valid
    assert anv.validate_no_html_injection(12345).is_valid  # non-string


# ---------------------------------------------------------------------------
# Phase 3.2 -- grounded numbers.
# ---------------------------------------------------------------------------

GROUNDED_BRIEFING = (
    "Manager: Test User. Portfolio contains 12 customers, 7 critical "
    "barriers, and 3 P1 cases. Total ARR at risk: $1,250,000. "
    "Top customer: Acme Corp at $500,000 (40%). Window: 90 days."
)


def test_phase_3_2_accepts_grounded_narrative():
    text = (
        "The portfolio has 12 customers with 7 critical barriers and "
        "3 P1 cases. The top customer represents 40% of the at-risk "
        "ARR ($500,000). 90 days analyzed."
    )
    res = anv.validate_grounded_numbers(text, GROUNDED_BRIEFING)
    assert res.is_valid, f"unexpected failures: {res.failures} / {res.sample_offending}"


def test_phase_3_2_accepts_structural_reference_numbers():
    """Structural ranking and calendar windows do not assert domain facts."""
    text = (
        "Top 5 risks are grouped into 30 day, 60 day, and 90 day windows."
    )
    res = anv.validate_grounded_numbers(text, "Briefing has zero numbers.")
    assert res.is_valid


def test_phase_3_2_rejects_unsupported_small_domain_numbers():
    """Small/common values still need evidence when they make factual claims."""
    text = "There are 0 escalations, 1 critical case, and confidence is 75%."
    res = anv.validate_grounded_numbers(text, "Briefing contains no metrics.")
    assert not res.is_valid
    assert "ungrounded_number" in res.failures


def test_phase_3_2_rejects_hallucinated_count():
    """The narrative quotes a hallucinated count -> must fail with
    ``ungrounded_number``.

    Round 66 / Pass 3 (B11): pre-R66 this test used "47 customers" /
    "23 barriers" -- both small integers under 32-99.  R66/B11 widened
    ``_COMMON_REFERENCE_NUMBERS`` to cover all integers 0-100 so the
    validator stops false-positive rejecting derived counts in that
    range.  Update the hallucination to use a 4-digit count outside
    the common set so the rejection-of-hallucination contract is still
    pinned.
    """
    text = "The portfolio has 1234 customers and 567 critical barriers."
    res = anv.validate_grounded_numbers(text, GROUNDED_BRIEFING)
    assert not res.is_valid
    assert "ungrounded_number" in res.failures
    assert "1234" in res.sample_offending["ungrounded_number"]


def test_phase_3_2_rejects_hallucinated_arr_amount():
    """A made-up ARR figure must trip the validator even when the
    briefing carries a different ARR number."""
    text = "Total ARR exposed: $9,876,543 across the portfolio."
    res = anv.validate_grounded_numbers(text, GROUNDED_BRIEFING)
    assert not res.is_valid
    assert "ungrounded_number" in res.failures


def test_phase_3_2_tolerance_handles_rounding():
    """A narrative that rounds a briefing number (e.g. briefing 0.7503,
    narrative 75%) must NOT trip the validator at default tolerance."""
    text = "The exposure is 75% of ARR."
    briefing = "ARR exposure ratio: 75% (computed from 0.7503)"
    res = anv.validate_grounded_numbers(text, briefing)
    assert res.is_valid


def test_phase_3_2_oversized_input_is_rejected_safely():
    """Pathological inputs must fail with ``oversized``, not OOM."""
    huge = "12 customers. " * 25_000  # ~325KB
    res = anv.validate_grounded_numbers(huge, GROUNDED_BRIEFING)
    assert not res.is_valid
    assert "oversized" in res.failures


def test_phase_3_2_handles_suffix_multiplier():
    """``$2.5M`` in the narrative must match a briefing that prints
    ``2,500,000`` so we don't reject correctly-grounded ARR amounts
    just because the formatter chose a friendlier suffix."""
    text = "Customer X has $2.5M of ARR exposed."
    briefing = "ARR_at_risk_for_X = $2,500,000"
    res = anv.validate_grounded_numbers(text, briefing)
    assert res.is_valid


@pytest.mark.parametrize(
    ("narrative", "briefing"),
    [
        ("Failure rate is 5%.", "There are 5 cases."),
        ("ARR is $5.", "There are 5 customers."),
        ("There are 5,000,000 customers.", "ARR is $5M."),
        ("Loss increased −5%.", "Growth increased 5%."),
        ("EUR 5M is exposed.", "USD 5M is exposed."),
        ("The window is 90 months.", "The window is 90 days."),
        ("Year: 2025.", "Customers: 2025."),
        ("5% of cases are P1.", "Customer adoption is 5%."),
    ],
)
def test_phase_3_2_rejects_equal_values_with_different_meanings(
    narrative: str,
    briefing: str,
):
    assert not anv.validate_grounded_numbers(narrative, briefing).is_valid


def test_phase_3_2_validates_every_range_endpoint():
    result = anv.validate_grounded_numbers(
        "Between 5-999 customers are affected.",
        "Affected customers: 5.",
    )

    assert not result.is_valid
    assert "999" in result.sample_offending["ungrounded_number"]


# ---------------------------------------------------------------------------
# Phase 3.3 -- invented entities.
# ---------------------------------------------------------------------------


def test_phase_3_3_accepts_listed_customers():
    text = "Acme Corp and Beta Inc are the top accounts."
    res = anv.validate_no_invented_entities(text, ["Acme Corp", "Beta Inc"])
    assert res.is_valid


def test_phase_3_3_rejects_invented_customer():
    text = "Phantom Corp leads the portfolio at $1M."
    res = anv.validate_no_invented_entities(text, ["Acme Corp", "Beta Inc"])
    assert not res.is_valid
    assert "invented_entity" in res.failures
    assert "Phantom Corp" in res.sample_offending["invented_entity"]


def test_phase_3_3_handles_punctuation_and_case_drift():
    """`acme corp.` must match `Acme Corp` after normalization."""
    text = "acme corp. is performing well; ACME Corp continues to grow."
    res = anv.validate_no_invented_entities(text, ["Acme Corp"])
    assert res.is_valid


def test_phase_3_3_no_candidates_returns_valid():
    """If the narrative has no entity-shaped names, the validator must
    pass even when the allow-list is empty."""
    text = "Three priorities are open this quarter."
    res = anv.validate_no_invented_entities(text, [])
    assert res.is_valid


# ---------------------------------------------------------------------------
# Phase 3.x -- aggregate validate_narrative.
# ---------------------------------------------------------------------------


def test_phase_3_4_aggregate_passes_when_all_validators_pass():
    text = (
        "Acme Corp leads the portfolio at $500,000. 12 customers reviewed."
    )
    res = anv.validate_narrative(
        text,
        GROUNDED_BRIEFING,
        allowed_entities=["Acme Corp"],
    )
    assert res.is_valid


def test_phase_3_4_aggregate_collects_multiple_failures():
    text = (
        "<script>x</script> Phantom Corp posted $9,876,543 of ARR risk."
    )
    res = anv.validate_narrative(
        text,
        GROUNDED_BRIEFING,
        allowed_entities=["Acme Corp"],
    )
    assert not res.is_valid
    # Expect at least three categories of failure: html, ungrounded, invented.
    fail_text = ",".join(res.failures)
    assert "html_injection" in fail_text
    assert "ungrounded_number" in fail_text
    assert "invented_entity" in fail_text


def test_phase_3_4_marker_present_in_validator_module():
    src = _read("ai_narrative_validator.py")
    assert "Round 16 / Phase 3" in src, (
        "Round 16 / Phase 3 marker must remain in ai_narrative_validator.py"
    )


def test_phase_3_4_marker_present_at_app_simple_integration():
    src = _read("app_simple.py")
    assert "Round 16 / Phase 3.4" in src, (
        "Round 16 / Phase 3.4 marker must remain at the app_simple "
        "narrative gate; otherwise the integration has been reverted."
    )
    # The integration must call validate_narrative and use the
    # placeholder constant on failure.
    assert "ai_narrative_validator" in src
    assert "GROUNDING_FAILURE_PLACEHOLDER" in src
    assert "validate_narrative" in src


def test_phase_3_4_placeholder_constant_is_user_facing():
    """The placeholder string must be informative and never empty."""
    assert isinstance(anv.GROUNDING_FAILURE_PLACEHOLDER, str)
    assert len(anv.GROUNDING_FAILURE_PLACEHOLDER) > 50
    assert "data" in anv.GROUNDING_FAILURE_PLACEHOLDER.lower()


def test_phase_3_4_module_public_api_surface():
    """The module's __all__ must expose the documented symbols.

    Round 17 / Phase E extends this surface with
    ``is_corpus_chunk_safe`` so the Customer 360 + Playbook routes
    (and the report pre-fill helper) can run the same hostile-
    content filter that Round 16 introduced for narrative
    validation.  Update both ``__all__`` and this expected set
    when adding new public symbols.
    """
    expected = {
        "GROUNDING_FAILURE_PLACEHOLDER",
        "ValidationResult",
        "validate_grounded_numbers",
        "validate_narrative",
        "validate_no_html_injection",
        "validate_no_invented_entities",
        "is_corpus_chunk_safe",  # Round 17 / Phase E.
    }
    assert set(anv.__all__) == expected


def test_phase_3_4_validation_result_is_immutable():
    """The result type is frozen; rebinding fields must raise.

    ``@dataclass(frozen=True)`` raises ``dataclasses.FrozenInstanceError``
    on attribute assignment; pin against that specific class instead of a
    blanket ``Exception`` so a future refactor that downgrades immutability
    to a plain dataclass (which would silently *succeed* the assignment)
    is caught.
    """
    res = anv.ValidationResult(is_valid=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.is_valid = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Smoke: importing the module is idempotent and produces no side-effects.
# ---------------------------------------------------------------------------


def test_phase_3_module_reload_is_safe():
    """The validator must be importable and re-importable without
    side-effects on global state."""
    importlib.reload(anv)
    assert hasattr(anv, "validate_narrative")
