"""Round 9 / Phase 3.2: cisco query params digested at INFO (PSIRT paths)."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_cisco_direct_search_params() -> None:
    src = REPO_ROOT.joinpath('cisco_internal_integrations.py').read_text(encoding='utf-8')
    assert '_query_digest(' in src, (
        'cisco_internal_integrations.py: _query_digest helper missing'
    )
    assert 'query_digest=%s' in src, (
        'cisco_internal_integrations.py: structured query_digest logging missing'
    )
