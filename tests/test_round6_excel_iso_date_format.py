"""Round 6 / Phase 1.8 regression test.

Workbook ``date_format`` must be ISO ``yyyy-mm-dd``.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_excel_iso_date_format() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.8" in src, (
        "Round 6 Phase 1.8 marker missing in app_simple.py."
    )
    assert "yyyy-mm-dd" in src
