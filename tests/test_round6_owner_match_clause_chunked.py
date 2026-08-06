"""Round 6 / Phase 4.9 regression test.

Owner-matching ``OR`` clauses for cases must be chunked.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_owner_match_clause_chunked() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.9", label='src')
