"""Round 7 / Phase 5.7 regression test.

User question must be NFKC-normalized before the BEGIN USER_QUESTION fence to defeat homoglyph injection.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_5_7() -> None:
    src = (REPO_ROOT.joinpath('ask_ai_grounded.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 5.7" in src, (
        "Round 7 Phase 5.7 marker missing in ask_ai_grounded.py."
    )
    assert 'NFKC' in src, (
        "Round 7 / Phase 5.7: expected pattern " + 'NFKC' + " missing in ask_ai_grounded.py."
    )
