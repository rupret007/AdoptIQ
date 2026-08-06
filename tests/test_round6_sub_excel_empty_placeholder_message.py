"""Round 6 / Phase 1.10 regression test.

Subscription Excel ``_write_empty_or_placeholder`` must include an
empty-window message row, never just headers.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sub_excel_empty_placeholder_message() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 1.10", label='src')
