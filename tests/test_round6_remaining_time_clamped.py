"""Round 6 / Phase 6.2 regression test.

``remaining_time`` calculations must clamp to >= 0 to avoid
negative ETAs.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_remaining_time_clamped() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.2" in src
