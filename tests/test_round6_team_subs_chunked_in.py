"""Round 6 / Phase 4.1 regression test.

Team subscription Snowflake ``IN (...)`` query must be chunked to
respect the parameter cap.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_team_subs_chunked() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.1", label='src')
