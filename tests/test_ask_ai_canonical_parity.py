"""Phase 5 / Phase 2.1 + 2.3 regression test.

Ask AI grounded prompts must inject a CANONICAL_HEADLINE block built
from ``cm.build_portfolio_metrics`` over the SAME multi-source frames
the report path uses.  Without that block the LLM "guesses" headline
numbers from a 120-row sample and disagrees with the report it's
supposed to be answering questions about.

This test verifies:
1. The grounded module imports/uses ``canonical_metrics``.
2. ``run_portfolio_grounded_ask_ai`` references the
   ``CANONICAL_HEADLINE`` block (so the model receives the
   authoritative numbers).
3. ``context_summary`` uses ``cm.count_customers`` (Phase 2.3) rather
   than the legacy ``BU_NAME.nunique()`` shortcut.
"""
from __future__ import annotations

import inspect

import ask_ai_grounded as grounded


def test_grounded_module_imports_canonical_metrics() -> None:
    """The module must import canonical_metrics so headline numbers
    are computed the same way the report does."""
    src = inspect.getsource(grounded)
    assert "import canonical_metrics" in src or "from canonical_metrics" in src, (
        "ask_ai_grounded must import canonical_metrics so it builds "
        "headline numbers from the same helpers the report uses."
    )


def test_run_portfolio_grounded_ask_ai_emits_canonical_headline_block() -> None:
    src = inspect.getsource(grounded.run_portfolio_grounded_ask_ai)
    assert "CANONICAL_HEADLINE" in src, (
        "Grounded prompt must include a CANONICAL_HEADLINE block "
        "(Phase 2.1) so the LLM cannot disagree with the report on "
        "the headline portfolio numbers."
    )
    assert "build_portfolio_metrics" in src, (
        "CANONICAL_HEADLINE block must be sourced from "
        "cm.build_portfolio_metrics so it matches the report path."
    )
    assert "non-negotiable" in src.lower(), (
        "Prompt must instruct the model that CANONICAL_HEADLINE is "
        "non-negotiable, otherwise the model can paraphrase numbers."
    )


def test_run_portfolio_grounded_ask_ai_uses_cm_count_customers() -> None:
    """Phase 2.3: context_summary customer count must use
    ``cm.count_customers`` as its primary source (a fallback to
    ``BU_NAME.nunique()`` is allowed only inside an ``except`` branch
    so the badge agrees with the report headline in the happy path).
    """
    src = inspect.getsource(grounded.run_portfolio_grounded_ask_ai)
    assert "count_customers" in src, (
        "context_summary must call cm.count_customers so the Ask AI "
        "badge matches the canonical customer count used by reports."
    )
    # The legacy shortcut that previously *unconditionally* drove the
    # badge value must now appear only as a fallback inside an except
    # branch (Phase 2.3).  We assert that ``cm.count_customers``
    # appears in the source BEFORE the legacy shortcut, which is a
    # strong proxy for "primary path uses the canonical helper".
    primary_idx = src.find("count_customers(")
    legacy_idx = src.find("BU_NAME'].nunique()")
    assert primary_idx >= 0, (
        "cm.count_customers(...) call must appear in the grounded "
        "Ask AI path (Phase 2.3)."
    )
    if legacy_idx >= 0:
        assert primary_idx < legacy_idx, (
            "cm.count_customers must be the primary path; the legacy "
            "BU_NAME.nunique() shortcut is only acceptable as a "
            "fallback (and therefore must appear AFTER the primary call)."
        )
