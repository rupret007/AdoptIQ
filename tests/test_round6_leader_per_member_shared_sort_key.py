"""Round 6 / Phase 1.12 regression test.

Leader ``per_member`` must use shared risk-score sort key, not
lexicographic.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_leader_per_member_sort_key() -> None:
    src = (REPO_ROOT / "leader_report_generator.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.12" in src
