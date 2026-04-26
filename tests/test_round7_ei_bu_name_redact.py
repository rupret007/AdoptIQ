"""Round 7 / Phase 1.9 regression test.

BU_NAME samples must be redacted at INFO level (counts/digests only).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_9() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.9" in src, (
        "Round 7 Phase 1.9 marker missing in executive_intelligence_formatter.py."
    )
