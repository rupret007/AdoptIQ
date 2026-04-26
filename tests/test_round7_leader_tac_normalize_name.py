"""Round 7 / Phase 6.8 regression test.

TAC matching in ``leader_report_generator`` must route through ``normalize_customer_name`` for parity with Snowflake-side joins.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_8() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.8" in src, (
        "Round 7 Phase 6.8 marker missing in leader_report_generator.py."
    )
    assert 'normalize_customer_name' in src, (
        "Round 7 / Phase 6.8: expected pattern " + 'normalize_customer_name' + " missing in leader_report_generator.py."
    )
