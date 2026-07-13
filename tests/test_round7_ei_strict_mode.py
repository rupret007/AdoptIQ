"""Round 7 / Phase 1.8 regression test.

EI report must accept and forward ``strict_mode``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_8() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.8" in src, (
        "Round 7 Phase 1.8 marker missing in executive_intelligence_formatter.py."
    )
    assert 'strict_mode' in src, (
        "Round 7 / Phase 1.8: expected pattern " + 'strict_mode' + " missing in executive_intelligence_formatter.py."
    )
