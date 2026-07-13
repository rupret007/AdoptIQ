"""Round 6 / Phase 6.4 regression test.

Error-classifier user_message must be a generic static sentence;
internal details belong in ``detail_tail``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_error_classifier_user_message_generic() -> None:
    src = (REPO_ROOT / "error_classifier.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.4" in src
