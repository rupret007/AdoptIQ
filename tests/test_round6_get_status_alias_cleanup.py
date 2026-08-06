"""Round 6 / Phase 6.16 regression test.

``_SENSITIVE_ENDPOINTS`` must not list a stale ``get_status_alias``
endpoint that was never registered.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_get_status_alias_cleanup() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.16", label='src')
    assert "'get_status_alias'" not in src
