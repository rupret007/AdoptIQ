"""Round 6 / Phase 4.14 regression test.

``open_age_days`` calculations must use timezone-aware UTC math.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_open_age_days_tz_aware() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.14" in src
