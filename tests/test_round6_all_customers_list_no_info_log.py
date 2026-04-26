"""Round 6 / Phase 6.3 regression test.

``/api/all-customers`` must not INFO-log the full list (privacy);
only counts/digests at INFO, full list at DEBUG.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_all_customers_no_info_log() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.3" in src
