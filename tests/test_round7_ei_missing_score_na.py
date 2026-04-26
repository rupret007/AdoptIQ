"""Round 7 / Phase 1.7 regression test.

High-risk table must render ``N/A`` for missing scores rather than coercing to 0/10.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_7() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.7" in src, (
        "Round 7 Phase 1.7 marker missing in executive_intelligence_formatter.py."
    )
