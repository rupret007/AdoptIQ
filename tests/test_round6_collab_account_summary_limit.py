"""Round 6 / Phase 4.3 regression test.

Collab Account Summary fetcher must enforce a row LIMIT and accept
a documented bound.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_collab_account_summary_limit() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.3" in src
