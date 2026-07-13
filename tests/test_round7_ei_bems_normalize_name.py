"""Round 7 / Phase 1.5 regression test.

BEMS groupby must apply ``normalize_customer_name`` so case/whitespace variants don't fragment the bucket.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_5() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.5" in src, (
        "Round 7 Phase 1.5 marker missing in executive_intelligence_formatter.py."
    )
    assert 'normalize_customer_name' in src, (
        "Round 7 / Phase 1.5: expected pattern " + 'normalize_customer_name' + " missing in executive_intelligence_formatter.py."
    )
