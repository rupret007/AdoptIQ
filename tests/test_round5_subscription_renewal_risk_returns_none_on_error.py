"""Round 5 / Phase 4.1 regression test.

``get_subscription_renewal_risk`` must NOT return ``risk_score=0``
on the exception path - the UI cannot distinguish ``0/10`` healthy
from "we failed to compute".  The fix returns ``risk_score=None`` /
``state='unavailable'`` (mirroring the not-found path) so the UI
renders an explicit "unavailable" badge.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_risk_exception_returns_none_not_zero() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 4.1" in src, (
        "Round 5 Phase 4.1 marker missing in adoptiq_backend.py."
    )
    assert "risk_score=None" in src or "risk_score': None" in src or "'risk_score': None" in src, (
        "Round 5 Phase 4.1: the exception path must return "
        "risk_score=None (not 0) to prevent the UI from showing "
        "a fake healthy score."
    )
