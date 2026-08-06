"""Round 6 / Phase 3.11 regression test.

Briefing payload must be injected as the user message (delimited
data block), not concatenated into the system prompt.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_briefing_in_user_message() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 3.11", label='src')
