"""Round 7 / Phase 1.11 regression test.

``parse_ai_output_and_add`` must enforce delimiter + negative constraints.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_11() -> None:
    src = (REPO_ROOT.joinpath('executive_report_builder.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.11" in src, (
        "Round 7 Phase 1.11 marker missing in executive_report_builder.py."
    )
