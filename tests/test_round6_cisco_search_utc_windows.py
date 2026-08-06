"""Round 6 / Phase 4.7 regression test.

Cisco search "last N days" windows must be expressed in UTC, not
local-time.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_cisco_search_utc_windows() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.7", label='src')
