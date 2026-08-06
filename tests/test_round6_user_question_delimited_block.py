"""Round 6 / Phase 3.3 regression test.

User question must be embedded in a delimited block (e.g., XML tags
or fenced) before sending to the LLM; defense against prompt
injection.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_user_question_delimited_block() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 3.3", label='src')
