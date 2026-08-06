"""Round 6 / Phase 5.1 regression test.

Executive summary risk bands must come from
``RISK_BAND_THRESHOLDS``, not literal numbers.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_exec_summary_canonical_bands() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 5.1", label='src')
    assert_in_source(src, "RISK_BAND_THRESHOLDS", label='src')
