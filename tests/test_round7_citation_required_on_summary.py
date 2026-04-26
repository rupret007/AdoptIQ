"""Round 7 / Phase 5.2 regression test.

``compose_grounded_answer`` must demote uncited qualitative summary/action sentences to ``unknowns``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_2() -> None:
    src = (REPO_ROOT.joinpath('ask_ai_grounded.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.2" in src, (
        "Round 7 Phase 5.2 marker missing in ask_ai_grounded.py."
    )
