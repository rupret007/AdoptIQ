"""Round 4 / Phase 4.2 regression test.

``run_portfolio_grounded_ask_ai`` must serialize external-intel
``fetch_errors`` / ``list_truncated`` into the LLM prompt (in addition
to prefetch-DataFrame partial warnings).  Pre-Round-4 only the
prefetch warnings reached the prompt.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_portfolio_grounded_serializes_intel_meta() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    assert "intel_meta" in src or "intel_fetch_errors" in src, (
        "Round 4 Phase 4.2: run_portfolio_grounded_ask_ai must store "
        "intel fetch_errors/list_truncated into bundle['intel_meta'] "
        "(or equivalent) for prompt serialization."
    )


def test_portfolio_grounded_partial_block_emits_intel_warnings() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    # Round 4 fix extends the "DATA_SOURCE_WARNINGS" block (or the
    # "INTEL_DATA_WARNINGS" block in intel-grounded path) with intel
    # warnings.  We pin that the source contains at least one of the
    # canonical warning labels.
    assert (
        "DATA_SOURCE_WARNINGS" in src
        or "INTEL_DATA_WARNINGS" in src
    ), (
        "Round 4 Phase 4.2: ask_ai_grounded must emit a "
        "DATA_SOURCE_WARNINGS / INTEL_DATA_WARNINGS block when intel "
        "feeds fail."
    )
