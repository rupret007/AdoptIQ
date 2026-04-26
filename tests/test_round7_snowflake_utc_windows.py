"""Round 7 / Phase 2.1 regression test.

Snowflake date windows must be computed in Python as explicit UTC, not via session-tz CURRENT_DATE().
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_1() -> None:
    src = (REPO_ROOT.joinpath('enhanced_snowflake_insights.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.1" in src, (
        "Round 7 Phase 2.1 marker missing in enhanced_snowflake_insights.py."
    )
    assert 'datetime.now(timezone.utc)' in src, (
        "Round 7 / Phase 2.1: expected pattern " + 'datetime.now(timezone.utc)' + " missing in enhanced_snowflake_insights.py."
    )
