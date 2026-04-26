"""Round 9 / Phase 5.3: subscription-search.js stores subscription objects in JS-side Map."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_subscription_search_data_attr() -> None:
    src = REPO_ROOT.joinpath('static', 'js', 'subscription-search.js').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 5.3' in src, (
        'Round 9 / Phase 5.3 marker missing in static/js/subscription-search.js'
    )
    assert '_subRegistry' in src, (
        'static/js/subscription-search.js: in-memory Map registry missing'
    )
    # Old data-subscription JSON-in-HTML pattern must be gone.
    assert 'data-subscription=' not in src, (
        'static/js/subscription-search.js: data-subscription JSON-in-HTML still present'
    )
