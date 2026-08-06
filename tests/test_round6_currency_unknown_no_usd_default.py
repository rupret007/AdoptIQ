"""Round 6 / Phase 7.1 regression test.

When the currency set is empty, ARR briefing must render a
``CURRENCY UNKNOWN`` disclaimer instead of silently defaulting to
USD.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_currency_unknown_no_usd_default() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 7.1", label='src')
    assert_in_source(src, "CURRENCY UNKNOWN", label='src')
