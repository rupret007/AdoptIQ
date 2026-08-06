"""Round 9 / Phase 3.3: cisco redirect-following calls validate netloc allowlist."""
from __future__ import annotations
from source_shape_utils import assert_in_source

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_marker_cisco_netloc_allowlist() -> None:
    src = REPO_ROOT.joinpath('cisco_internal_integrations.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 3.3' in src, (
        'Round 9 / Phase 3.3 marker missing in cisco_internal_integrations.py'
    )
    assert_in_source(src, '_CISCO_ALLOWED_NETLOC_HOSTS', label='src')
    assert_in_source(src, 'def _is_cisco_netloc(', label='src')


def test_is_cisco_netloc_accepts_cisco_subdomains() -> None:
    from cisco_internal_integrations import _is_cisco_netloc
    assert _is_cisco_netloc('https://api.cisco.com/foo') is True
    assert _is_cisco_netloc('https://apix.cisco.com/foo') is True
    assert _is_cisco_netloc('https://cisco.com/foo') is True


def test_is_cisco_netloc_rejects_non_cisco() -> None:
    from cisco_internal_integrations import _is_cisco_netloc
    assert _is_cisco_netloc('https://evil.com/foo') is False
    assert _is_cisco_netloc('https://cisco.com.evil.com/foo') is False
    # Non-https rejected.
    assert _is_cisco_netloc('http://api.cisco.com/foo') is False
    # Empty / malformed rejected.
    assert _is_cisco_netloc('') is False
    assert _is_cisco_netloc('not-a-url') is False
