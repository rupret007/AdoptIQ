"""Round 4 / Phase 6.4 regression test.

Prompt templates must not instruct the model to extrapolate ARR /
percentages / industry benchmarks without an "only if present in
briefing" qualifier.  After the Round 4 fix the prompts must include
explicit negative constraints that forbid invented ARR / dollar
amounts, invented percentages, and IDs not present in the briefing.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_portfolio_template_includes_negative_constraints() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "NEGATIVE CONSTRAINTS" in src, (
        "Round 4 Phase 6.4: PROMPT_PORTFOLIO_TEMPLATE must include a "
        "'NEGATIVE CONSTRAINTS' block forbidding invented ARR/%/IDs."
    )
    # Pin specific guards.
    assert "Do NOT invent ARR" in src, (
        "Round 4 Phase 6.4: prompt template must explicitly forbid "
        "inventing ARR / revenue / dollar figures."
    )
    assert "Do NOT invent percentage" in src or "Do NOT invent percent" in src, (
        "Round 4 Phase 6.4: prompt template must explicitly forbid "
        "inventing percentages."
    )


def test_legacy_ask_ai_prompt_includes_negative_constraints() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "NEGATIVE CONSTRAINTS" in src, (
        "Round 4 Phase 6.4: legacy Ask AI system prompt in "
        "app_simple.py must include a 'NEGATIVE CONSTRAINTS' block."
    )


def test_compact_template_includes_negative_constraints() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # Both portfolio and compact templates must carry the constraints.
    # We rely on a unique substring near the compact section.
    assert "PROMPT_COMPACT_EXECUTIVE_TEMPLATE" in src, (
        "PROMPT_COMPACT_EXECUTIVE_TEMPLATE not found"
    )
    # The single 'NEGATIVE CONSTRAINTS' block check above already
    # covers presence; here we additionally ensure the compact
    # template has its own ARR/$ guard nearby (the substring appears
    # twice in the source after the fix).
    assert src.count("NEGATIVE CONSTRAINTS") >= 2, (
        "Round 4 Phase 6.4: both PROMPT_PORTFOLIO_TEMPLATE and "
        "PROMPT_COMPACT_EXECUTIVE_TEMPLATE must each include a "
        "NEGATIVE CONSTRAINTS block."
    )
