"""Phase 6.1 HIGH: _bundled_secrets documented as not confidential + 0o600 perms + import warning.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker__bundled_secrets_py() -> None:
    src = REPO_ROOT.joinpath('_bundled_secrets.py').read_text(encoding='utf-8')
    assert 'NOT A CONFIDENTIALITY BOUNDARY' in src, 'Round 8 pattern missing in _bundled_secrets.py: NOT A CONFIDENTIALITY BOUNDARY'
    assert '0o600' in src, 'Round 8 pattern missing in _bundled_secrets.py: 0o600'
    assert 'logging' in src, 'Round 8 pattern missing in _bundled_secrets.py: logging'

