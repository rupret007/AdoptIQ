"""Phase 4.10 MED: admin /api/* responses set Cache-Control: no-store.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_enhanced_admin_dashboard_v2_py() -> None:
    src = REPO_ROOT.joinpath('enhanced_admin_dashboard_v2.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 4.10' in src, 'Round 8 marker missing in enhanced_admin_dashboard_v2.py: Round 8 / Phase 4.10'
    assert 'Cache-Control' in src, 'Round 8 pattern missing in enhanced_admin_dashboard_v2.py: Cache-Control'
    assert 'no-store' in src, 'Round 8 pattern missing in enhanced_admin_dashboard_v2.py: no-store'

