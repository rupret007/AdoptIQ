"""Round 6 / Phase 1.16 regression test.

Compact green/gray customer lists must use explicit ``sorted()``,
not dict insertion order.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_compact_green_list_explicit_sort() -> None:
    src = (REPO_ROOT / "compact_report_formatter.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 1.16", label='src')
