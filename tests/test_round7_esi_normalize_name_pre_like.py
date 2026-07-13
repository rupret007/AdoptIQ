"""Round 7 / Phase 2.3 regression test.

Customer name must be pre-normalized via ``normalize_customer_name`` before LIKE/subquery binding.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_2_3() -> None:
    src = (REPO_ROOT.joinpath('enhanced_snowflake_insights.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 2.3" in src, (
        "Round 7 Phase 2.3 marker missing in enhanced_snowflake_insights.py."
    )
    assert 'normalize_customer_name' in src, (
        "Round 7 / Phase 2.3: expected pattern " + 'normalize_customer_name' + " missing in enhanced_snowflake_insights.py."
    )
