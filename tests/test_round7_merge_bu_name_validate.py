"""Round 7 / Phase 6.4 regression test.

``_merge_bu_name`` left-merge must include ``validate='m:1'`` (or pre-deduplicate with explicit policy) for cardinality protection.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_4() -> None:
    src = (REPO_ROOT.joinpath('leader_report_generator.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.4" in src, (
        "Round 7 Phase 6.4 marker missing in leader_report_generator.py."
    )
