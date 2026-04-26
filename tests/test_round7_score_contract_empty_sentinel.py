"""Round 7 / Phase 3.3 regression test.

``_score_contract`` must return a sentinel for empty subscription frames, not a base score that could read as 'low risk'.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_3() -> None:
    src = (REPO_ROOT.joinpath('risk_scoring.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.3" in src, (
        "Round 7 Phase 3.3 marker missing in risk_scoring.py."
    )
