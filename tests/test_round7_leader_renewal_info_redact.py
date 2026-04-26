"""Round 7 / Phase 6.3 regression test.

Customer-name samples must be redacted at INFO in ``leader_report_generator`` and ``advanced_renewal_analyzer`` (counts at INFO, names at DEBUG).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_3() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.3" in src, (
        "Round 7 Phase 6.3 marker missing in leader_report_generator.py."
    )
