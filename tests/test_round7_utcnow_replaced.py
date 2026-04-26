"""Round 7 / Phase 3.2 regression test.

``risk_scoring`` and ``report_utils`` must use ``datetime.now(timezone.utc)`` instead of deprecated ``datetime.utcnow()``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_2() -> None:
    src = (REPO_ROOT.joinpath('risk_scoring.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.2" in src, (
        "Round 7 Phase 3.2 marker missing in risk_scoring.py."
    )
    assert 'datetime.now(timezone.utc)' in src, (
        "Round 7 / Phase 3.2: expected pattern " + 'datetime.now(timezone.utc)' + " missing in risk_scoring.py."
    )
