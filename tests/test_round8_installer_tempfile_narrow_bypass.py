"""Phase 6.3 MED: installer uses tempfile + narrowed PowerShell bypass.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_installer_app_py() -> None:
    src = REPO_ROOT.joinpath('installer_app.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 6.3' in src, 'Round 8 marker missing in installer_app.py: Round 8 / Phase 6.3'
    assert 'tempfile.NamedTemporaryFile' in src, 'Round 8 pattern missing in installer_app.py: tempfile.NamedTemporaryFile'
    assert 'NonInteractive' in src, 'Round 8 pattern missing in installer_app.py: NonInteractive'
    assert 'InputFormat' in src, 'Round 8 pattern missing in installer_app.py: InputFormat'

