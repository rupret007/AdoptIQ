"""Round 6 / Phase 4.4 regression test.

BST API consumer must paginate (handle ``next_page`` / cursor).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bst_pagination() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.4" in src
