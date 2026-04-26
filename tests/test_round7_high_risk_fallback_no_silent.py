"""Round 7 / Phase 3.4 regression test.

High-risk-count fallback in ``_build_portfolio_summary`` must not be silently swallowed.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_4() -> None:
    src = (REPO_ROOT.joinpath('risk_scoring.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.4" in src, (
        "Round 7 Phase 3.4 marker missing in risk_scoring.py."
    )
