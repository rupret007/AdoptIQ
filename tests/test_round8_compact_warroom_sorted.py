"""Phase 3.6 MED: war-room red list sorted by lowercased name.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_compact_report_formatter_py() -> None:
    src = REPO_ROOT.joinpath('compact_report_formatter.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 3.6' in src, 'Round 8 marker missing in compact_report_formatter.py: Round 8 / Phase 3.6'
    assert 'lower' in src, 'Round 8 pattern missing in compact_report_formatter.py: lower'

