"""Round 9 / Phase 3.1: cisco_internal_integrations.py error logs use response body digest."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_response_body_digest_helper() -> None:
    src = REPO_ROOT.joinpath('cisco_internal_integrations.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 3.1' in src, (
        'Round 9 / Phase 3.1 marker missing in cisco_internal_integrations.py'
    )
    assert 'def _response_body_digest(' in src, (
        'cisco_internal_integrations.py: _response_body_digest helper missing'
    )
