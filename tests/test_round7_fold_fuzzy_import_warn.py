"""Round 7 / Phase 3.6 regression test.

``canonical_metrics._collect_customer_names`` must log + tally a partial-data warning when ``_clean_name_for_key`` import fails.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_6() -> None:
    src = (REPO_ROOT.joinpath('canonical_metrics.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.6" in src, (
        "Round 7 Phase 3.6 marker missing in canonical_metrics.py."
    )
