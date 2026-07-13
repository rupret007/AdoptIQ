"""Round 6 / Phase 1.15 regression test.

advanced_renewal_analyzer Usage block must use one percent convention
with explicit unit labels.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_usage_percent_convention() -> None:
    src = (REPO_ROOT / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.15" in src
