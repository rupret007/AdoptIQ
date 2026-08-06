"""Round 6 / Phase 1.7 regression test.

High-risk row issue counts must join via ``normalize_customer_name``
on both sides.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_high_risk_row_normalized_join() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.7" in src, (
        "Round 6 Phase 1.7 marker missing in app_simple.py."
    )
    assert_in_source(src, "normalize_customer_name", label='src')
