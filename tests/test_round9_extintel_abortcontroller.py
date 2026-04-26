"""Round 9 / Phase 5.1: external_intelligence.html refresh fetch uses AbortController."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_extintel_abortcontroller() -> None:
    src = REPO_ROOT.joinpath('templates', 'external_intelligence.html').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 5.1' in src, (
        'Round 9 / Phase 5.1 marker missing in templates/external_intelligence.html'
    )
    assert 'AbortController' in src, (
        'templates/external_intelligence.html: AbortController missing'
    )
    assert "AbortError" in src, (
        'templates/external_intelligence.html: AbortError handling missing'
    )
    # 90s timeout matches Round 8 / Phase 5.1 in ask_ai.js.
    assert '90' in src, (
        'templates/external_intelligence.html: 90s timeout reference missing'
    )
