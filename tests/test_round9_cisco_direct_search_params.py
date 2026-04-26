"""Round 9 / Phase 3.2: cisco direct-search params digested at INFO."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_cisco_direct_search_params() -> None:
    src = REPO_ROOT.joinpath('cisco_internal_integrations.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 3.2' in src, (
        'Round 9 / Phase 3.2 marker missing in cisco_internal_integrations.py'
    )
    # The query digest pattern must be applied for direct-search INFO logs.
    assert '_query_digest(' in src, (
        'cisco_internal_integrations.py: _query_digest helper missing'
    )
