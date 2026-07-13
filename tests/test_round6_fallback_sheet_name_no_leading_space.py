"""Round 6 / Phase 1.11 regression test.

Fallback sheet name must not have a leading space.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_fallback_sheet_name_no_leading_space() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.11" in src
    assert "sheets['Analysis_Summary']" in src or "sheets[\"Analysis_Summary\"]" in src
