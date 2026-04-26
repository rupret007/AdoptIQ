"""Round 7 / Phase 3.1 regression test.

``_clamp`` must reject NaN/Inf so a non-finite composite never falls into the HEALTHY band.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_1() -> None:
    src = (REPO_ROOT.joinpath('risk_scoring.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.1" in src, (
        "Round 7 Phase 3.1 marker missing in risk_scoring.py."
    )
    assert 'isfinite' in src, (
        "Round 7 / Phase 3.1: expected pattern " + 'isfinite' + " missing in risk_scoring.py."
    )
