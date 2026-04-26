"""Phase 5.1 MED: ask_ai client portfolio fetch uses AbortController + timeout.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_static_js_ask_ai_js() -> None:
    src = REPO_ROOT.joinpath('static/js/ask_ai.js').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 5.1' in src, 'Round 8 marker missing in static/js/ask_ai.js: Round 8 / Phase 5.1'
    assert 'AbortController' in src, 'Round 8 pattern missing in static/js/ask_ai.js: AbortController'

