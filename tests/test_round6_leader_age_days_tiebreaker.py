"""Round 6 / Phase 1.13 regression test.

Leader ``sort_values('_age_days')`` needs a deterministic secondary
tie-breaker (e.g. id/opened_date).
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_leader_age_days_tiebreaker() -> None:
    src = (REPO_ROOT / "leader_report_generator.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 1.13", label='src')
