"""Round 6 / Phase 6.16 regression test.

``_SENSITIVE_ENDPOINTS`` must not list a stale ``get_status_alias``
endpoint that was never registered.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_get_status_alias_cleanup() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.16" in src
    assert "'get_status_alias'" not in src
