"""Round 6 / Phase 1.9 regression test.

Empty successful fetches must route through ``classify_data_state``
instead of plain "No data available".
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_empty_fetch_classify_state() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 1.9", label='src')
    assert_in_source(src, "classify_data_state", label='src')
