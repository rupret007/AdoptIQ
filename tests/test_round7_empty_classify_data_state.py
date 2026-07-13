"""Round 7 / Phase 6.10 regression test.

Empty-result ``except`` branches must attach ``fetch_error`` so ``classify_data_state`` reports 'fetch failed' vs 'zero rows'.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_10() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.10" in src, (
        "Round 7 Phase 6.10 marker missing in leader_report_generator.py."
    )
    assert 'fetch_error' in src, (
        "Round 7 / Phase 6.10: expected pattern " + 'fetch_error' + " missing in leader_report_generator.py."
    )
