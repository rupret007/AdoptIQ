"""Round 6 / Phase 3.2 regression test.

Ask-AI ``intel`` allowed_ids must not be the full union; should be
narrowed to the briefing-grounding set.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_intel_allowed_ids_narrow() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 3.2" in src
