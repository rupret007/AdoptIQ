"""Round 7 / Phase 1.6 regression test.

Vulnerability-by-customer iteration must use explicit ``sorted(..., key=...)`` to be deterministic.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_6() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.6" in src, (
        "Round 7 Phase 1.6 marker missing in executive_intelligence_formatter.py."
    )
    assert 'sorted(' in src, (
        "Round 7 / Phase 1.6: expected pattern " + 'sorted(' + " missing in executive_intelligence_formatter.py."
    )
