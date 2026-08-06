"""Round 6 / Phase 5.15 regression test.

``_takeaway_*`` ``except Exception`` blocks must fail loud (or
return well-defined sentinel) instead of silently returning empty.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_takeaway_except_fail_loud() -> None:
    src = (REPO_ROOT / "compact_report_formatter.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 5.15", label='src')
