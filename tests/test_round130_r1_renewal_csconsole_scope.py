"""Renewal CSConsole technology-scope regression pins.

Round 162.2 supersedes the two-source Round-125 contract: all four CSConsole
customer sources use a fail-closed scope helper. Missing authoritative
technology fields are reported as unavailable instead of retaining the full
manager-wide source.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import os

import pytest


def _app_simple_src() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app_simple.py"), encoding="utf-8") as fh:
        return fh.read()


def test_renewal_all_csconsole_sources_use_strict_scope_helper() -> None:
    src = _app_simple_src()
    assert_in_source(src, "Round 162.2", label='src')
    assert_in_source(src, '"action_plans": csconsole_action_plans', label='src')
    assert_in_source(src, '"customer_pulse": csconsole_customer_pulse', label='src')
    assert_in_source(src, '"success_priorities": csconsole_success_priorities', label='src')
    assert_in_source(src, '"csconsole_adoption_barriers": csconsole_adoption_barriers', label='src')
    assert_in_source(src, "_r162_scope_renewal_csconsole_source(", label='src')


def test_renewal_filter_passes_account_ids() -> None:
    src = _app_simple_src()
    assert_in_source(src, "account_ids=account_ids", label='src')


def test_renewal_scope_filter_fails_closed() -> None:
    src = _app_simple_src()
    assert_in_source(src, "Technology scoping failed for %s; source withheld", label='src')
    assert_in_source(src, "_r162_mark_technology_scope_unavailable(", label='src')


def test_renewal_ab_still_uses_apply_scope_filter_ab() -> None:
    src = _app_simple_src()
    assert_in_source(src, "ab_scoped = _apply_scope_filter_ab(ab_raw, technology, days)", label='src')


def test_round130_marker_in_adoptiq_backend_retry() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "adoptiq_backend.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert_in_source(src, "_R130_SNOWFLAKE_CONNECT_ATTEMPTS", label='src')
    assert_in_source(src, "_connect_with_keeper_impl", label='src')


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
