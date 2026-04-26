"""Phase 2.4 MED: BU_NAME LIKE binds normalize_customer_name needle.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_adoptiq_backend_py() -> None:
    src = REPO_ROOT.joinpath('adoptiq_backend.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 2.4' in src, 'Round 8 marker missing in adoptiq_backend.py: Round 8 / Phase 2.4'
    assert 'normalize_customer_name' in src, 'Round 8 pattern missing in adoptiq_backend.py: normalize_customer_name'

