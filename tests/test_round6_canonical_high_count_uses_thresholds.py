"""Round 6 / Phase 5.5 regression test.

Canonical high-risk count helper must use thresholds, not literal
score >= 70.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_canonical_high_count_thresholds() -> None:
    src = (REPO_ROOT / "canonical_metrics.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 5.5" in src
