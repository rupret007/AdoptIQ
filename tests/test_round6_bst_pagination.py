"""Round 6 / Phase 4.4 regression test.

Round 165 removed the fictional BST API client; static portal links remain.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bst_pagination_removed_in_round_165() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert "def search_defects_bst" not in src
    assert "def defect_portal_url" in src
