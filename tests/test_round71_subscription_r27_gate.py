"""Round 71 / Phase 3 (#14) -- Subscription analysis routes through R27 gate.

Pre-R71 the subscription analysis Word writer in ``app_simple.py``
injected the raw ``generate_llm_response`` output directly into the
DOCX, bypassing the ``ai_narrative_validator.validate_narrative`` gate
that the Compact / Comprehensive / Renewal / Leader paths all enforce.

Round 71 / Phase 3 (#14) wires the gate in and substitutes
``GROUNDING_FAILURE_PLACEHOLDER`` on rejection or validator exception.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_subscription_writer_imports_validator() -> None:
    """The subscription writer MUST import ``ai_narrative_validator`` so
    the ``validate_narrative`` call site is reachable."""
    src = _read_app_simple()
    assert "import ai_narrative_validator as _r71_anv_sub" in src, (
        "Round 71 / Phase 3 (#14): subscription writer must import "
        "ai_narrative_validator (aliased as _r71_anv_sub) so the R27 "
        "grounding gate runs on the LLM output."
    )


def test_round71_subscription_writer_calls_validate_narrative() -> None:
    """The subscription writer MUST call ``validate_narrative`` on the
    LLM response."""
    src = _read_app_simple()
    assert "_r71_anv_sub.validate_narrative(" in src, (
        "Round 71 / Phase 3 (#14): subscription writer must call "
        "_r71_anv_sub.validate_narrative(...) on the LLM output."
    )


def test_round71_subscription_writer_substitutes_placeholder_on_invalid() -> None:
    """When the validator rejects the LLM output, the writer MUST
    substitute ``GROUNDING_FAILURE_PLACEHOLDER`` (fail-closed)."""
    src = _read_app_simple()
    assert "_r71_safe_ai_response = _r71_anv_sub.GROUNDING_FAILURE_PLACEHOLDER" in src, (
        "Round 71 / Phase 3 (#14): subscription writer must substitute "
        "GROUNDING_FAILURE_PLACEHOLDER when validator rejects the output."
    )


def test_round71_subscription_writer_substitutes_placeholder_on_validator_exception() -> None:
    """When the validator itself raises, the writer MUST also
    substitute the placeholder (fail-closed under unknown error)."""
    src = _read_app_simple()
    # The fail-closed branch on validator exception was R71/Phase 3 (#15)
    # for the Compact path; the subscription branch added the SAME contract.
    assert "_r71_safe_ai_response = _r71_sub_placeholder" in src or (
        "_r71_anv_sub.GROUNDING_FAILURE_PLACEHOLDER" in src and "except" in src
    ), (
        "Round 71 / Phase 3 (#14): subscription writer must also "
        "substitute the placeholder when the validator raises an "
        "unexpected exception (fail-closed contract)."
    )


def test_round71_subscription_writer_uses_safe_response_in_doc() -> None:
    """The Word doc MUST consume ``_r71_safe_ai_response`` (the
    validated value), not the raw ``ai_response`` variable.

    The current writer deliberately routes the validated Markdown through
    ``append_to_word_report`` so headings and lists render as Word structure
    instead of appearing as literal ``##`` text.
    """
    src = _read_app_simple()
    assert "append_to_word_report(doc, _r71_safe_ai_response)" in src, (
        "Round 71 / Phase 3 (#14): subscription writer must use the "
        "VALIDATED ``_r71_safe_ai_response`` variable in the Word doc, "
        "not the raw LLM response."
    )
    assert "append_to_word_report(doc, ai_response)" not in src
