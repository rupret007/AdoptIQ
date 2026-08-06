"""Round 6 / Phase 6.1 regression test.

``_SENSITIVE_ENDPOINTS`` must include all data-reading, export, and
expensive API endpoints (so non-localhost callers cannot reach
them).
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sensitive_endpoints_extended() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.1", label='src')
    assert_in_source(src, "_SENSITIVE_ENDPOINTS", label='src')
