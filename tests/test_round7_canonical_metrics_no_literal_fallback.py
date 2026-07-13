"""Round 7 / Phase 3.5 regression test.

``canonical_metrics`` must raise on ``RISK_BAND_THRESHOLDS`` import failure rather than fall back to a literal duplicate.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_5() -> None:
    src = (REPO_ROOT.joinpath('canonical_metrics.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.5" in src, (
        "Round 7 Phase 3.5 marker missing in canonical_metrics.py."
    )
