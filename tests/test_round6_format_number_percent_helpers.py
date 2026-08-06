"""Round 6 / Phase 1.14 regression test.

``format_number(as_percent=True)`` is ambiguous; split into
``format_ratio_percent`` / ``format_percent_points``.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_format_number_percent_helpers() -> None:
    src = (REPO_ROOT / "report_utils.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 1.14", label='src')
    assert_in_source(src, "format_ratio_percent", label='src')
    assert_in_source(src, "format_percent_points", label='src')
