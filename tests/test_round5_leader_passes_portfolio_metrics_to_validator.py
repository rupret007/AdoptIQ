"""Round 5 / Phase 5.1 regression test.

The leader path must call ``validate_report_consistency(...)`` *with*
``portfolio_metrics=`` so the entire portfolio-block parity check in
``report_consistency.py`` runs (Compact / Executive Intelligence pass
it; the leader path used to skip it).
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_leader_validator_call_includes_portfolio_metrics() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 5.1" in src, (
        "Round 5 Phase 5.1 marker missing in app_simple.py."
    )
    assert "portfolio_metrics=" in src, (
        "Round 5 Phase 5.1: leader path must pass portfolio_metrics=... "
        "to validate_report_consistency."
    )
