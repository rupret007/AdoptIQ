"""Round 7 / Phase 3.9 regression test.

``RISK_SCORING_EXPLANATION`` (in ``report_utils``) must be rendered
from ``RISK_BAND_THRESHOLDS`` so the explanation copy cannot drift
from the canonical band thresholds.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_9() -> None:
    src = (REPO_ROOT.joinpath("report_utils.py")).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.9" in src, (
        "Round 7 Phase 3.9 marker missing in report_utils.py."
    )
    assert "RISK_BAND_THRESHOLDS" in src, (
        "Round 7 / Phase 3.9: RISK_BAND_THRESHOLDS reference missing in report_utils.py."
    )
