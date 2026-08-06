"""Round 27 - LLM scaffold-leak sanitizer for the customer health grade.

The PROMPT_CUSTOMER_TEMPLATE in ``adoptiq_backend.py`` includes the
literal ``[A, B, C, D, F]`` enumeration after the
``Customer Health Score:`` label as guidance to the model.  The LLM
is supposed to substitute its chosen letter, but in the v1.0.4
build 1 run for ``THE CHARLES SCHWAB CORPORATION US`` it preserved
the brackets (``Customer Health Score: [C]``) while five other
customers in the same report rendered cleanly (``... C``, ``... F``).

``_sanitize_llm_grade_brackets`` post-processes the LLM markdown
right before docx rendering and strips those stray brackets without
touching legitimate bracketed evidence elsewhere in the report.

The same function is also wired into
``ExecutiveReportBuilder._enforce_ai_output_contract`` so the
``parse_ai_output_and_add`` boundary is also covered.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import pytest

from adoptiq_backend import _sanitize_llm_grade_brackets
from executive_report_builder import ExecutiveReportBuilder


# -----------------------------------------------------------------
# Core sanitizer behaviour
# -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("dirty", "clean"),
    [
        ("Customer Health Score: [C]", "Customer Health Score: C"),
        ("Customer Health Score: [A]", "Customer Health Score: A"),
        ("Customer Health Score: [B]", "Customer Health Score: B"),
        ("Customer Health Score: [D]", "Customer Health Score: D"),
        ("Customer Health Score: [F]", "Customer Health Score: F"),
    ],
)
def test_sanitizer_strips_single_letter_brackets(dirty: str, clean: str) -> None:
    """Each grade A/B/C/D/F should have its surrounding brackets
    stripped while everything else in the line stays intact."""
    assert _sanitize_llm_grade_brackets(dirty) == clean


def test_sanitizer_handles_whitespace_inside_brackets() -> None:
    """The LLM occasionally emits ``[ C ]`` with padding.  The
    sanitizer must accept that as the same leak."""
    assert (
        _sanitize_llm_grade_brackets("Customer Health Score: [ C ]")
        == "Customer Health Score: C"
    )
    assert (
        _sanitize_llm_grade_brackets("Customer Health Score:  [B]")
        == "Customer Health Score:  B"
    )


def test_sanitizer_is_case_insensitive_on_grade_letter() -> None:
    """Lower-case grade letters can leak the same way; the regex
    should rewrite both forms.  Case is preserved (not upper-cased)
    so any future style enforcement runs at a different layer."""
    assert (
        _sanitize_llm_grade_brackets("Customer Health Score: [c]")
        == "Customer Health Score: c"
    )
    assert (
        _sanitize_llm_grade_brackets("Customer Health Score: [f]")
        == "Customer Health Score: f"
    )


def test_sanitizer_is_idempotent_on_clean_input() -> None:
    """Already-clean text must round-trip unchanged.  Running the
    sanitizer twice must produce the same output as running it once."""
    clean = "Customer Health Score: C"
    assert _sanitize_llm_grade_brackets(clean) == clean
    assert (
        _sanitize_llm_grade_brackets(_sanitize_llm_grade_brackets(clean))
        == clean
    )


def test_sanitizer_does_not_mutate_unrelated_bracketed_text() -> None:
    """The regex is anchored on the literal ``Customer Health Score:``
    label so other bracketed scaffolding in the LLM output (theme
    placeholders, evidence lists, etc.) must NOT be rewritten."""
    samples = [
        "[Theme Name]",
        "Trend: [Increasing, stable, or decreasing]",
        "Evidence: [2-3 powerful examples drawn from the data]",
        "Other Grades: [A, B, C, D, F]",  # outside the health score
        "Health: [pending]",  # different label
        "Score: [C]",  # different label (no Customer Health Score: prefix)
    ]
    for sample in samples:
        assert _sanitize_llm_grade_brackets(sample) == sample, (
            f"Round 27 regression: sanitizer rewrote unrelated text: {sample!r}"
        )


def test_sanitizer_handles_multiple_occurrences_in_one_blob() -> None:
    """A long markdown report may contain several customer sections
    each with its own ``Customer Health Score: [X]`` line.  All
    occurrences must be rewritten in a single pass."""
    blob = (
        "## Customer A\n"
        "### **Customer Health Score: [B]**\n"
        "narrative...\n"
        "## Customer B\n"
        "### **Customer Health Score: [F]**\n"
        "narrative...\n"
    )
    out = _sanitize_llm_grade_brackets(blob)
    assert "[B]" not in out
    assert "[F]" not in out
    assert "Customer Health Score: B" in out
    assert "Customer Health Score: F" in out
    # Headings, bold markers, and surrounding markdown must survive.
    assert "### **Customer Health Score: B**" in out
    assert "### **Customer Health Score: F**" in out


# -----------------------------------------------------------------
# Defensive contract
# -----------------------------------------------------------------


def test_sanitizer_returns_input_unchanged_for_non_string() -> None:
    """The sanitizer must be defensive: a None / int / etc. caller
    shouldn't crash the docx pipeline.  Non-string inputs come back
    as-is."""
    assert _sanitize_llm_grade_brackets(None) is None  # type: ignore[arg-type]
    assert _sanitize_llm_grade_brackets("") == ""
    assert _sanitize_llm_grade_brackets(42) == 42  # type: ignore[arg-type]


# -----------------------------------------------------------------
# Wiring: confirm the sanitizer is on the customer markdown path
# -----------------------------------------------------------------


def test_sanitizer_is_invoked_by_enforce_ai_output_contract() -> None:
    """``ExecutiveReportBuilder._enforce_ai_output_contract`` is the
    single chokepoint for AI markdown that flows through
    ``parse_ai_output_and_add``.  This test pins that the contract
    method calls the sanitizer."""
    raw = (
        "=== BEGIN AI_REPORT ===\n"
        "## THE CHARLES SCHWAB CORPORATION US\n"
        "### **Customer Health Score: [C]**\n"
        "narrative goes here\n"
        "=== END AI_REPORT ===\n"
    )
    cleaned = ExecutiveReportBuilder._enforce_ai_output_contract(raw)
    assert "Customer Health Score: C" in cleaned, (
        "Round 27 regression: _enforce_ai_output_contract is no longer "
        "running the bracket sanitizer."
    )
    assert "[C]" not in cleaned


def test_append_to_word_report_module_carries_sanitizer_call() -> None:
    """``append_to_word_report`` is the central docx writer used both
    by the web flow (via ``parse_ai_output_and_add``) and by the
    legacy CLI flow (which bypasses that boundary).  Pin the call
    site by static substring search so a future refactor can't
    drop the protection silently for the CLI path."""
    src = Path(__file__).parent.parent / "adoptiq_backend.py"
    text = src.read_text(encoding="utf-8")
    assert_in_source(text, "def append_to_word_report(", label='text')
    # Must define and reference the sanitizer.
    assert_in_source(text, "def _sanitize_llm_grade_brackets(", label='text')
    # The writer must call the sanitizer at least once.
    writer_idx = text.find("def append_to_word_report(")
    assert writer_idx != -1
    # Bound the slice to the next top-level def to keep the search
    # inside the writer function body.
    body = text[writer_idx:writer_idx + 6000]
    assert "_sanitize_llm_grade_brackets(markdown_content)" in body, (
        "Round 27 regression: append_to_word_report no longer calls "
        "_sanitize_llm_grade_brackets on its markdown_content input."
    )


def test_prompt_template_no_longer_uses_bracketed_grade_enumeration() -> None:
    """The prompt template at ``PROMPT_CUSTOMER_TEMPLATE`` previously
    contained ``[A, B, C, D, F]`` after the health-score label, which
    is the exact scaffolding the LLM occasionally preserved verbatim.
    Round 27 replaces it with explicit prose ('one letter A | B | C |
    D | F, no brackets').  Pin both the removal of the old form and
    the presence of the new one."""
    src = Path(__file__).parent.parent / "adoptiq_backend.py"
    text = src.read_text(encoding="utf-8")
    # ``### **Customer Health Score: [A, B, C, D, F]**`` is the
    # exact heading-formatted form that appears only inside the
    # prompt template -- using the heading prefix here lets us pin
    # the change without matching the explanatory comments inside
    # ``_sanitize_llm_grade_brackets`` (which legitimately document
    # the historical leak).
    assert "### **Customer Health Score: [A, B, C, D, F]**" not in text, (
        "Round 27 regression: PROMPT_CUSTOMER_TEMPLATE still contains "
        "the bracketed grade enumeration that was the original leak "
        "vector."
    )
    assert "### **Customer Health Score: <one letter" in text, (
        "Round 27 regression: PROMPT_CUSTOMER_TEMPLATE no longer "
        "carries the explicit prose instruction for the grade letter."
    )
