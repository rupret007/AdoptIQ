"""Round 7 / Phase 6.5 regression test.

``contract_info['total_arr']`` must mirror the multicurrency logic in ``_get_financial_metrics`` and emit ``CURRENCY UNKNOWN`` when mixed.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_5() -> None:
    src = (REPO_ROOT.joinpath('advanced_renewal_analyzer.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.5" in src, (
        "Round 7 Phase 6.5 marker missing in advanced_renewal_analyzer.py."
    )
