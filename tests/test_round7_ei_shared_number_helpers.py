"""Round 7 / Phase 1.4 regression test.

EI counts/scores must route through ``format_number`` / ``format_ratio_percent`` so rounding policy is shared.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_4() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.4" in src, (
        "Round 7 Phase 1.4 marker missing in executive_intelligence_formatter.py."
    )
    assert 'format_number' in src, (
        "Round 7 / Phase 1.4: expected pattern " + 'format_number' + " missing in executive_intelligence_formatter.py."
    )
    assert 'format_ratio_percent' in src, (
        "Round 7 / Phase 1.4: expected pattern " + 'format_ratio_percent' + " missing in executive_intelligence_formatter.py."
    )
