"""Round 7 / Phase 1.3 regression test.

EI 'Generated' timestamp must use ``datetime.now(timezone.utc)`` to match the 'Data as of UTC' line.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_1_3() -> None:
    src = (REPO_ROOT.joinpath('executive_intelligence_formatter.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 1.3" in src, (
        "Round 7 Phase 1.3 marker missing in executive_intelligence_formatter.py."
    )
    assert 'datetime.now(timezone.utc)' in src, (
        "Round 7 / Phase 1.3: expected pattern " + 'datetime.now(timezone.utc)' + " missing in executive_intelligence_formatter.py."
    )
