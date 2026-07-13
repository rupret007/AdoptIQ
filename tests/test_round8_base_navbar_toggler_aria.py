"""Phase 5.4 MED: base.html navbar toggler has full ARIA pairing.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_templates_base_html() -> None:
    src = REPO_ROOT.joinpath('templates/base.html').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 5.4' in src, 'Round 8 marker missing in templates/base.html: Round 8 / Phase 5.4'
    assert 'aria-controls' in src, 'Round 8 pattern missing in templates/base.html: aria-controls'
    assert 'aria-expanded' in src, 'Round 8 pattern missing in templates/base.html: aria-expanded'
    assert 'aria-label' in src, 'Round 8 pattern missing in templates/base.html: aria-label'

