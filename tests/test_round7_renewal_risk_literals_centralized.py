"""Round 7 / Phase 6.6 regression test.

Renewal-risk literals must be centralized as named constants in ``risk_scoring`` (``RENEWAL_RISK_INCREMENTS`` / ``RENEWAL_ARR_THRESHOLDS``).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_6_6() -> None:
    src = (REPO_ROOT.joinpath('risk_scoring.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 6.6" in src, (
        "Round 7 Phase 6.6 marker missing in risk_scoring.py."
    )
    assert 'RENEWAL_RISK_INCREMENTS' in src, (
        "Round 7 / Phase 6.6: expected pattern " + 'RENEWAL_RISK_INCREMENTS' + " missing in risk_scoring.py."
    )
    assert 'RENEWAL_ARR_THRESHOLDS' in src, (
        "Round 7 / Phase 6.6: expected pattern " + 'RENEWAL_ARR_THRESHOLDS' + " missing in risk_scoring.py."
    )
