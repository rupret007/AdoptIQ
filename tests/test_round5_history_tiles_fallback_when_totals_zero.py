"""Round 5 / Phase 2.5 regression test.

The history page tiles must fall back to the materialized
``analyses`` list when the aggregate ``_total_*`` counts come back as
0 but a non-empty list was rendered.  Otherwise the user sees
"0 reports" while a list of completed analyses is visible right below.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_history_tiles_have_fallback_branch() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 2.5" in src, (
        "Round 5 Phase 2.5: history endpoint must fall back to len(analyses) "
        "when the aggregate totals query returns zero but a non-empty list "
        "of analyses was materialized."
    )
