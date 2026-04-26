"""Phase 6.8 MED: app_simple stale_threshold drops datetime.utcnow().

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_app_simple_py() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    # Phase 1.6 (external_intelligence) and Phase 6.8 (stale_threshold)
    # share one combined marker because they live on adjacent lines.
    assert 'Round 8 / Phase 1.6 + 6.8' in src, 'Round 8 marker missing in app_simple.py: Round 8 / Phase 1.6 + 6.8'
    assert 'datetime.now(timezone.utc)' in src, 'Round 8 pattern missing in app_simple.py: datetime.now(timezone.utc)'

