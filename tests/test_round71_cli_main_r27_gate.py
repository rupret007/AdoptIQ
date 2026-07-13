"""Round 71 / Phase 3 (#16) -- CLI ``main()`` portfolio_summary +
customer_storyboard append paths route through R27 grounding gate.

Pre-R71 ``adoptiq_backend.main()`` appended raw LLM output for both
the portfolio summary and per-customer storyboards directly to the
Word doc, bypassing the ``ai_narrative_validator`` gate that the
HTTP analysis paths enforce.  The CLI is the path operators reach
for via ``python adoptiq_backend.py`` and the bake / debug flows.

Round 71 / Phase 3 (#16) wires the gate in for both append sites with
fail-closed substitution on rejection or validator exception.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_backend() -> str:
    return (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8", errors="replace")


def test_round71_cli_portfolio_summary_routes_through_validator() -> None:
    """The CLI portfolio_summary append site MUST call
    ``ai_narrative_validator.validate_narrative`` (aliased as
    ``_r71_anv_cli``) before passing the text into
    ``append_to_word_report``."""
    src = _read_backend()
    assert "import ai_narrative_validator as _r71_anv_cli" in src, (
        "Round 71 / Phase 3 (#16): CLI portfolio_summary must import "
        "ai_narrative_validator (aliased as _r71_anv_cli)."
    )
    assert "_r71_anv_cli.validate_narrative(" in src, (
        "Round 71 / Phase 3 (#16): CLI portfolio_summary must call "
        "_r71_anv_cli.validate_narrative(...) on the LLM output."
    )


def test_round71_cli_portfolio_summary_substitutes_placeholder_on_invalid() -> None:
    """When the validator rejects the portfolio_summary text, the CLI
    MUST substitute ``GROUNDING_FAILURE_PLACEHOLDER`` (fail-closed)."""
    src = _read_backend()
    assert "_r71_safe_portfolio_summary = _r71_anv_cli.GROUNDING_FAILURE_PLACEHOLDER" in src, (
        "Round 71 / Phase 3 (#16): CLI portfolio_summary must substitute "
        "GROUNDING_FAILURE_PLACEHOLDER on validator rejection."
    )


def test_round71_cli_portfolio_summary_uses_safe_value_in_append() -> None:
    """The Word doc MUST receive ``_r71_safe_portfolio_summary`` (the
    validated value), not the raw ``portfolio_summary`` variable."""
    src = _read_backend()
    assert "append_to_word_report(doc, _r71_safe_portfolio_summary)" in src, (
        "Round 71 / Phase 3 (#16): the Word doc must consume the "
        "VALIDATED ``_r71_safe_portfolio_summary`` value (not the raw "
        "LLM output)."
    )


def test_round71_cli_customer_storyboard_routes_through_validator() -> None:
    """The CLI per-customer storyboard append site MUST also route
    through the grounding gate."""
    src = _read_backend()
    assert "_r71_safe_storyboard" in src, (
        "Round 71 / Phase 3 (#16): CLI per-customer storyboard must "
        "introduce a _r71_safe_storyboard variable that holds the "
        "validated text before append."
    )
    assert "append_to_word_report(doc, _r71_safe_storyboard)" in src, (
        "Round 71 / Phase 3 (#16): CLI per-customer storyboard must "
        "consume the VALIDATED ``_r71_safe_storyboard`` value."
    )


def test_round71_cli_customer_storyboard_substitutes_placeholder_on_invalid() -> None:
    """When the validator rejects a per-customer storyboard, the CLI
    MUST substitute the placeholder for that customer (fail-closed)."""
    src = _read_backend()
    assert "_r71_safe_storyboard = _r71_anv_cust.GROUNDING_FAILURE_PLACEHOLDER" in src, (
        "Round 71 / Phase 3 (#16): CLI per-customer storyboard must "
        "substitute GROUNDING_FAILURE_PLACEHOLDER on validator rejection."
    )
