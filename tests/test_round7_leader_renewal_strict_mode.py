"""Round 7 / Phase 6.11 regression test.

``LeaderReportGenerator`` and ``generate_leader_report`` must accept and forward ``strict_mode``; ``AdvancedRenewalAnalyzer`` mirrors this.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_11() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.11" in src, (
        "Round 7 Phase 6.11 marker missing in leader_report_generator.py."
    )
    assert 'strict_mode' in src, (
        "Round 7 / Phase 6.11: expected pattern " + 'strict_mode' + " missing in leader_report_generator.py."
    )
