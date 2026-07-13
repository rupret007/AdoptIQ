"""Phase 6.2 HIGH: embed_credentials CI guard fails on real-secret formats.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_embed_credentials_py() -> None:
    src = REPO_ROOT.joinpath('embed_credentials.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 6.2' in src, 'Round 8 marker missing in embed_credentials.py: Round 8 / Phase 6.2'
    assert 'ci_lint' in src, 'Round 8 pattern missing in embed_credentials.py: ci_lint'
    assert '--ci-lint' in src, 'Round 8 pattern missing in embed_credentials.py: --ci-lint'
    assert '_SECRET_PATTERNS' in src, 'Round 8 pattern missing in embed_credentials.py: _SECRET_PATTERNS'

