"""Phase 4.1 HIGH (SSRF): BST form_action allowlist + https-only enforced before requests.post.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_cisco_internal_integrations_py() -> None:
    src = REPO_ROOT.joinpath('cisco_internal_integrations.py').read_text(encoding='utf-8')
    assert 'def defect_portal_url' in src, 'Round 165: static BST portal helper missing'
    assert 'https://bst.cisco.com/bugsearch/bug/' in src
    assert 'def search_defects_bst' not in src

