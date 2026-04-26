"""Round 7 / Phase 2.5 regression test.

ESI must enforce ``is_table_allowed`` (allowlist), not just ``is_table_blocked``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_5() -> None:
    src = (REPO_ROOT.joinpath('enhanced_snowflake_insights.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.5" in src, (
        "Round 7 Phase 2.5 marker missing in enhanced_snowflake_insights.py."
    )
    assert 'is_table_allowed' in src, (
        "Round 7 / Phase 2.5: expected pattern " + 'is_table_allowed' + " missing in enhanced_snowflake_insights.py."
    )
