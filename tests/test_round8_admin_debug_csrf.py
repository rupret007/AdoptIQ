"""Phase 4.7 MED: api_debug_verbose POST + dashboard fetch require CSRF token.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_enhanced_admin_dashboard_v2_py() -> None:
    src = REPO_ROOT.joinpath('enhanced_admin_dashboard_v2.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 4.7' in src, 'Round 8 marker missing in enhanced_admin_dashboard_v2.py: Round 8 / Phase 4.7'
    assert '_require_admin_csrf' in src, 'Round 8 pattern missing in enhanced_admin_dashboard_v2.py: _require_admin_csrf'
    assert 'X-AdoptIQ-Admin-CSRF' in src, 'Round 8 pattern missing in enhanced_admin_dashboard_v2.py: X-AdoptIQ-Admin-CSRF'

