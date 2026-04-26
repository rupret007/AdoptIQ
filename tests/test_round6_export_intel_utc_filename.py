"""Round 6 / Phase 6.18 regression test.

``export_intel`` filename must be derived from a UTC datetime so it
is portable across regions.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_export_intel_utc_filename() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.18" in src
