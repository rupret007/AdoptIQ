"""Phase 5.2 MED: ask_ai inline script extracted to /static/js/ask_ai.js + CSP tightened.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_templates_ask_ai_html() -> None:
    src = REPO_ROOT.joinpath('templates/ask_ai.html').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 5.2' in src, 'Round 8 marker missing in templates/ask_ai.html: Round 8 / Phase 5.2'
    assert 'Content-Security-Policy' in src, 'Round 8 pattern missing in templates/ask_ai.html: Content-Security-Policy'
    assert 'ask_ai.js' in src, 'Round 8 pattern missing in templates/ask_ai.html: ask_ai.js'

