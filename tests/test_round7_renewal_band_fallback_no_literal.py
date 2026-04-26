"""Round 7 / Phase 6.13 regression test.

``advanced_renewal_analyzer`` must raise on
``risk_scoring.RISK_BAND_THRESHOLDS`` import failure rather than
fall back to a literal duplicate dict, so the renewal Word report
cannot disagree with the canonical band thresholds.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_13() -> None:
    src = (REPO_ROOT.joinpath("advanced_renewal_analyzer.py")).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.13" in src, (
        "Round 7 Phase 6.13 marker missing in advanced_renewal_analyzer.py."
    )
    assert "raise RuntimeError(" in src, (
        "Round 7 Phase 6.13: expected raise on RISK_BAND_THRESHOLDS import failure missing."
    )
    # The literal-dict fallback must be gone -- we should not see the
    # old assignment ``_RISK_BAND_THRESHOLDS_0_100 = { ... }`` rebuilt
    # as a Python dict.  (Comments describing the old behavior are
    # allowed; only the actual rebinding is forbidden.)
    assert "_RISK_BAND_THRESHOLDS_0_100 = {" not in src, (
        "Round 7 Phase 6.13: literal _RISK_BAND_THRESHOLDS_0_100 fallback still present."
    )
