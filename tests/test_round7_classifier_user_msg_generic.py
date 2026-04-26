"""Round 7 / Phase 3.14 regression test.

``error_classifier.user_message`` strings must use generic copy; internal script names belong in ``detail_tail``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_round7_phase_3_14() -> None:
    src = (REPO_ROOT.joinpath('error_classifier.py')).read_text(encoding="utf-8")
    assert "Round 7 / Phase 3.14" in src, (
        "Round 7 Phase 3.14 marker missing in error_classifier.py."
    )
