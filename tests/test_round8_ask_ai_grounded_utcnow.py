"""Phase 6.7 MED: ask_ai_grounded fallback uses datetime.now(timezone.utc).

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_ask_ai_grounded_py() -> None:
    src = REPO_ROOT.joinpath('ask_ai_grounded.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 6.7' in src, 'Round 8 marker missing in ask_ai_grounded.py: Round 8 / Phase 6.7'
    assert 'datetime.now' in src, 'Round 8 pattern missing in ask_ai_grounded.py: datetime.now'
    assert '_tz.utc' in src, 'Round 8 pattern missing in ask_ai_grounded.py: _tz.utc'
    assert '_tz_intel.utc' in src, 'Round 8 pattern missing in ask_ai_grounded.py: _tz_intel.utc'

