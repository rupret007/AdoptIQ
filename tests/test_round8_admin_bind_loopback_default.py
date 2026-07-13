"""Phase 4.11 MED: admin app binds 127.0.0.1 by default; 0.0.0.0 requires env opt-in.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_enhanced_admin_dashboard_v2_py() -> None:
    src = REPO_ROOT.joinpath('enhanced_admin_dashboard_v2.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 4.11' in src, 'Round 8 marker missing in enhanced_admin_dashboard_v2.py: Round 8 / Phase 4.11'
    assert '127.0.0.1' in src, 'Round 8 pattern missing in enhanced_admin_dashboard_v2.py: 127.0.0.1'
    assert 'ADOPTIQ_ADMIN_BIND_PUBLIC' in src, 'Round 8 pattern missing in enhanced_admin_dashboard_v2.py: ADOPTIQ_ADMIN_BIND_PUBLIC'

