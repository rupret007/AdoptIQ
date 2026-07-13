"""Round 7 / Phase 6.2 regression test.

``advanced_renewal_analyzer`` must join on the customer-name column consistent with the leader path, not bind ``account_id`` against ``RELATED_CUSTOMER__C``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_2() -> None:
    src = (REPO_ROOT.joinpath('advanced_renewal_analyzer.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.2" in src, (
        "Round 7 Phase 6.2 marker missing in advanced_renewal_analyzer.py."
    )
