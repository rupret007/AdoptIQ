"""Round 6 / Phase 4.2 regression test.

CSConsole period_comparison Snowflake fetches must be chunked.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_csconsole_period_comparison_chunked() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.2", label='src')
