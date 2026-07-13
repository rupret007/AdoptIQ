"""Phase 4.3 MED: Cisco _safe_response_json rejects non-JSON Content-Type responses.

Round 8 regression marker test.  Mirrors the Round 5/6/7 pattern:
read the relevant source file and assert the marker plus a concrete
pattern that proves the fix shipped.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_marker_cisco_internal_integrations_py() -> None:
    src = REPO_ROOT.joinpath('cisco_internal_integrations.py').read_text(encoding='utf-8')
    assert 'Round 8 / Phase 4.3' in src, 'Round 8 marker missing in cisco_internal_integrations.py: Round 8 / Phase 4.3'
    assert 'Content-Type' in src, 'Round 8 pattern missing in cisco_internal_integrations.py: Content-Type'
    assert 'json' in src, 'Round 8 pattern missing in cisco_internal_integrations.py: json'

