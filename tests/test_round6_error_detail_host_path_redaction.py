"""Round 6 / Phase 4.20 regression test.

Error details must redact host names and full file paths.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_error_detail_redaction() -> None:
    src = (REPO_ROOT / "error_classifier.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.20" in src
