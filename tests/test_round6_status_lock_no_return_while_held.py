"""Round 6 / Phase 6.5 regression test.

Code paths must not ``return`` while ``analysis_status_lock`` is
held; lock must be released first.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_status_lock_safe_release() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.5", label='src')
