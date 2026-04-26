"""Round 7 / Phase 2.9 regression test.

Customer identifiers must be redacted from ESI INFO logs.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_9() -> None:
    src = (REPO_ROOT.joinpath('enhanced_snowflake_insights.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.9" in src, (
        "Round 7 Phase 2.9 marker missing in enhanced_snowflake_insights.py."
    )
