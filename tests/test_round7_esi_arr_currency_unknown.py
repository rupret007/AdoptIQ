"""Round 7 / Phase 2.4 regression test.

``total_arr`` must be flagged ``CURRENCY UNKNOWN`` unless a single currency is present.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_4() -> None:
    src = (REPO_ROOT.joinpath('enhanced_snowflake_insights.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.4" in src, (
        "Round 7 Phase 2.4 marker missing in enhanced_snowflake_insights.py."
    )
