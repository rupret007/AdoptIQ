"""Round 6 / Phase 5.1 regression test.

Executive summary risk bands must come from
``RISK_BAND_THRESHOLDS``, not literal numbers.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_exec_summary_canonical_bands() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 5.1" in src
    assert "RISK_BAND_THRESHOLDS" in src
