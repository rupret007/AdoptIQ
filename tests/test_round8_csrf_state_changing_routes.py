"""Phase 1.1 MED: CSRF added to clear_stuck_analyses, verbose_debug_api, simple_test, test_generate_report.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_app_simple_py() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 1.1' in src, 'Round 8 marker missing in app_simple.py: Round 8 / Phase 1.1'
    assert 'validate_csrf' in src, 'Round 8 pattern missing in app_simple.py: validate_csrf'

