"""Round 6 / Phase 3.4 regression test.

Portfolio prompt must instruct the LLM to list only customers that
appear in the briefing payload.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_portfolio_prompt_briefing_only() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 3.4", label='src')
