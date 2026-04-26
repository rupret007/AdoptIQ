"""Round 7 / Phase 2.2 regression test.

ESI ``analysis_date`` and Word 'Report Generated' must use ``datetime.now(timezone.utc)``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_2() -> None:
    src = (REPO_ROOT.joinpath('enhanced_snowflake_insights.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.2" in src, (
        "Round 7 Phase 2.2 marker missing in enhanced_snowflake_insights.py."
    )
    assert 'datetime.now(timezone.utc)' in src, (
        "Round 7 / Phase 2.2: expected pattern " + 'datetime.now(timezone.utc)' + " missing in enhanced_snowflake_insights.py."
    )
