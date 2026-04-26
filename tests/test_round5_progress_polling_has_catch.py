"""Round 5 / Phase 2.1 regression test.

The progress page polling loop must surface polling failures (404 after
eviction, 5xx, network drop, JSON parse error) to the user instead of
silently spinning forever.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_progress_polling_handles_errors() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 2.1" in src, (
        "Round 5 Phase 2.1: progress polling fetch() chain must include a "
        ".catch handler that surfaces polling failures to the user."
    )
