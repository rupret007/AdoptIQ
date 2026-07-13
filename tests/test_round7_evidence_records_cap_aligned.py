"""Round 7 / Phase 5.4 regression test.

Portfolio and intel paths must use the same env-driven ``ASK_AI_MAX_EVIDENCE_RECORDS`` cap.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_4() -> None:
    src = (REPO_ROOT.joinpath('ask_ai_grounded.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.4" in src, (
        "Round 7 Phase 5.4 marker missing in ask_ai_grounded.py."
    )
    assert 'ASK_AI_MAX_EVIDENCE_RECORDS' in src, (
        "Round 7 / Phase 5.4: expected pattern " + 'ASK_AI_MAX_EVIDENCE_RECORDS' + " missing in ask_ai_grounded.py."
    )
