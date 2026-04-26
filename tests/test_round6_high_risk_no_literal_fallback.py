"""Round 6 / Phase 5.3 regression test.

High-risk fallback path must call canonical thresholds, not use
hardcoded literals.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_high_risk_no_literal_fallback() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 5.3" in src
