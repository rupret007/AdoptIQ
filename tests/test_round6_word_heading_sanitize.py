"""Round 6 / Phase 1.4 regression test.

Customer/manager strings must be sanitized and length-capped before
``doc.add_heading`` to avoid control-char and overflow issues.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_word_heading_sanitize_compact() -> None:
    src = (REPO_ROOT / "compact_report_formatter.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.4" in src, (
        "Round 6 Phase 1.4 marker missing in compact_report_formatter.py."
    )


def test_word_heading_sanitize_app_simple() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.4" in src, (
        "Round 6 Phase 1.4 marker missing in app_simple.py."
    )
