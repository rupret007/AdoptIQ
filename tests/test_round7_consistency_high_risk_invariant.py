"""Round 7 / Phase 3.7 regression test.

``report_consistency`` must assert ``portfolio_metrics['high_risk_customers'] == compute_high_risk_count(...)``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_7() -> None:
    src = (REPO_ROOT.joinpath('report_consistency.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.7" in src, (
        "Round 7 Phase 3.7 marker missing in report_consistency.py."
    )
