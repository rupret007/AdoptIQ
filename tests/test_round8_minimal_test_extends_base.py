"""Phase 5.3 MED: minimal_test.html extends base.html.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_templates_minimal_test_html() -> None:
    src = REPO_ROOT.joinpath('templates/minimal_test.html').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 5.3' in src, 'Round 8 marker missing in templates/minimal_test.html: Round 8 / Phase 5.3'
    assert 'extends' in src, 'Round 8 pattern missing in templates/minimal_test.html: extends'
    assert 'base.html' in src, 'Round 8 pattern missing in templates/minimal_test.html: base.html'

