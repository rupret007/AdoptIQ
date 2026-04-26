"""Round 7 / Phase 6.9 regression test.

Number formatting in leader/renewal modules must route through ``format_ratio_percent`` / ``format_number``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_9() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.9" in src, (
        "Round 7 Phase 6.9 marker missing in leader_report_generator.py."
    )
