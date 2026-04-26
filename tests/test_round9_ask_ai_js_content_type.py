"""Round 9 / Phase 5.2: static/js/ask_ai.js guards .json() with r.ok + Content-Type."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_ask_ai_js_content_type() -> None:
    src = REPO_ROOT.joinpath('static', 'js', 'ask_ai.js').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 5.2' in src, (
        'Round 9 / Phase 5.2 marker missing in static/js/ask_ai.js'
    )
    assert '_askAiJson' in src, (
        'static/js/ask_ai.js: _askAiJson guard helper missing'
    )
    assert 'content-type' in src.lower(), (
        'static/js/ask_ai.js: Content-Type check missing'
    )
    assert 'r.ok' in src, (
        'static/js/ask_ai.js: r.ok guard missing'
    )
