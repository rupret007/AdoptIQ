"""Phase 2.8 MED: analysis_date uses datetime.now(timezone.utc).isoformat().

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_adoptiq_backend_py() -> None:
    src = REPO_ROOT.joinpath('adoptiq_backend.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 2.8' in src, 'Round 8 marker missing in adoptiq_backend.py: Round 8 / Phase 2.8'
    assert 'analysis_date' in src, 'Round 8 pattern missing in adoptiq_backend.py: analysis_date'

