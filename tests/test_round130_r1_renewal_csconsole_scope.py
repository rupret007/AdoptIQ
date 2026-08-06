"""Round 130 — R1 renewal CSConsole technology-scope regression pins.

Round 125 / C1 scopes Renewal ``csconsole_action_plans`` and
``csconsole_customer_pulse`` through ``_filter_csconsole_data_by_technology``
for non-``All`` technology scopes (ACC parity with AB).  This file pins the
wiring in ``app_simple.run_customer_renewal_analysis`` without a live
Snowflake run.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import os

import pytest


def _app_simple_src() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app_simple.py"), encoding="utf-8") as fh:
        return fh.read()


def test_renewal_csconsole_ap_pulse_filter_present() -> None:
    src = _app_simple_src()
    assert_in_source(src, "Round 125 / C1", label='src')
    # Round 139: Action Plans use authoritative-tech scope helper (not raw tech filter).
    assert_in_source(src, "csconsole_action_plans = _scope_action_plans_for_report(", label='src')
    assert_in_source(src, "csconsole_customer_pulse = _filter_csconsole_data_by_technology(", label='src')


def test_renewal_filter_passes_account_ids() -> None:
    src = _app_simple_src()
    assert_in_source(src, "account_ids=account_ids", label='src')


def test_renewal_scope_filter_guarded_try_except() -> None:
    src = _app_simple_src()
    assert_in_source(src, "_r125_ren_scope_err", label='src')
    assert_in_source(src, "CSConsole AP/Pulse technology", label='src')


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
