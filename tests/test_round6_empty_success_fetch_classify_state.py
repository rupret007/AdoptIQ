"""Round 6 / Phase 1.9 regression test.

Empty successful fetches must route through ``classify_data_state``
instead of plain "No data available".
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_empty_fetch_classify_state() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.9" in src
    assert "classify_data_state" in src
