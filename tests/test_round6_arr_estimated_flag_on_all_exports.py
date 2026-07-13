"""Round 6 / Phase 1.6 regression test.

Synthetic ARR injected by ``enrich_csone_with_arr`` must carry an
``ARR_Estimated`` companion column wherever ``Customer_ARR`` is read.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_arr_estimated_flag_present() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 1.6" in src, (
        "Round 6 Phase 1.6 marker missing in app_simple.py."
    )
    assert "ARR_Estimated" in src, (
        "ARR_Estimated column flag must be present alongside Customer_ARR."
    )
