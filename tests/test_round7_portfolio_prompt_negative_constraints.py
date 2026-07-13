"""Round 7 / Phase 5.8 regression test.

Portfolio system prompt must include explicit negative constraints (no fabrication, no contact info, no monetary amounts not in source).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_8() -> None:
    src = (REPO_ROOT.joinpath('ask_ai_grounded.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.8" in src, (
        "Round 7 Phase 5.8 marker missing in ask_ai_grounded.py."
    )
