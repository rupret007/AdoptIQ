"""Round 14 behavioral tests.

These tests exercise the actual code paths that Round 14 fixes, so a
regression that re-introduces the bug (e.g. by reverting the marker
comment) fails loudly here instead of silently.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source, count_in_source

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Phase 1.1 — Flask bind host defaults to loopback
# ---------------------------------------------------------------------------

def test_phase_1_1_bind_host_default_is_loopback() -> None:
    """The Round 14 default must not be ``0.0.0.0``.

    Reading the source rather than executing ``app.run`` because the
    bind decision lives in the ``__main__`` block.
    """
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The Round 14 / Phase 1.1 block must wire the default through
    # ADOPTIQ_BIND_PUBLIC, not hardcode 0.0.0.0 as the default.
    assert_in_source(src, "ADOPTIQ_BIND_PUBLIC", label="src")
    # And the resolved default branch must select 127.0.0.1 when neither
    # ADOPTIQ_BIND_HOST nor ADOPTIQ_BIND_PUBLIC is set.
    assert_in_source(
        src,
        "_bind_host = '0.0.0.0' if _bind_public else '127.0.0.1'",
        label="src",
    )


# ---------------------------------------------------------------------------
# Phase 2.1 — data_contracts.validate_row_contract no longer raises NameError
# ---------------------------------------------------------------------------

def test_phase_2_1_validate_row_contract_returns_columns() -> None:
    """The success path must return a sorted list of present columns,
    not raise ``NameError: name 'columns' is not defined``.
    """
    from data_contracts import validate_row_contract

    df = pd.DataFrame(
        {
            "BU_NAME": [],
            "SUBSCRIPTION_NUMBER": [],
            "ANNUAL_RECURRING_REVENUE": [],
        }
    )
    result = validate_row_contract(df, dataset="subscriptions")
    assert result["is_valid"] is True
    assert result["missing_slots"] == []
    # The Round 14 fix renamed the source variable to columns_raw.
    assert "BU_NAME" in result["present_columns"]
    assert result["present_columns"] == sorted(result["present_columns"])


def test_phase_2_1_validate_row_contract_failure_path_still_works() -> None:
    """The Round 14 fix must not regress the failure-return path."""
    from data_contracts import validate_row_contract

    df = pd.DataFrame({"FOO_COLUMN": []})
    result = validate_row_contract(df, dataset="subscriptions")
    assert result["is_valid"] is False
    assert result["missing_slots"], "missing slots should be reported"
    assert "FOO_COLUMN" in result["present_columns"]


# ---------------------------------------------------------------------------
# Phase 2.2 — admin dashboard helpers reachable at module scope
# ---------------------------------------------------------------------------

def test_phase_2_2_admin_dashboard_helpers_module_scope() -> None:
    """``_utc_iso_z`` and ``_tz`` must be importable at module scope so
    ``get_report_history`` and ``get_analytics`` stop silently failing.
    """
    import enhanced_admin_dashboard_v2 as ead

    assert hasattr(ead, "_utc_iso_z"), "_utc_iso_z must be at module scope"
    assert hasattr(ead, "_tz"), "_tz must be at module scope"
    assert ead._tz is timezone

    # Datetime input round-trips to a Z-suffixed string.
    out = ead._utc_iso_z(datetime(2026, 4, 26, 1, 2, 3, tzinfo=timezone.utc))
    assert out.endswith("Z"), out
    assert out.startswith("2026-04-26T01:02:03"), out

    # Bare string with Z passes through.
    assert ead._utc_iso_z("2026-04-26T00:00:00Z") == "2026-04-26T00:00:00Z"

    # Empty / None returns ''.
    assert ead._utc_iso_z(None) == ""
    assert ead._utc_iso_z("") == ""


def test_phase_2_2_get_analytics_does_not_silently_fail() -> None:
    """``get_analytics`` previously returned ``{}`` because of NameError on
    ``_utc_iso_z``.  The Round 14 fix must let it return the real shape.
    """
    from enhanced_admin_dashboard_v2 import get_analytics

    out = get_analytics()
    # Even on an empty database, the contract is "return a dict with the
    # 4 well-known keys" -- not "return an empty dict because of a
    # silent NameError".
    assert set(out.keys()) >= {
        "report_types",
        "managers",
        "risk_levels",
        "daily_reports",
    }


def test_phase_2_2_get_report_history_seven_day_query_succeeds(caplog) -> None:
    """``get_report_history`` previously logged
    ``[get_report_history] last_7_days query failed: name '_utc_iso_z'
    is not defined``.  Round 14 must let the query succeed.
    """
    from enhanced_admin_dashboard_v2 import get_report_history

    with caplog.at_level(logging.ERROR):
        out = get_report_history()
    # No NameError messages must appear.
    failures = [
        rec.message
        for rec in caplog.records
        if "_utc_iso_z" in rec.message or "_tz" in rec.message
    ]
    assert failures == [], failures
    # The placeholder row must report the 7-day query as not-failed.
    if out and isinstance(out[0], dict) and out[0].get("_placeholder"):
        assert out[0].get("_total_last_7_days_failed") is False, out[0]


# ---------------------------------------------------------------------------
# Phase 2.3 — adoptiq_backend OrderedDict annotations resolve cleanly
# ---------------------------------------------------------------------------

def test_phase_2_3_orderdict_resolves_at_module_scope() -> None:
    """The Round 14 fix imports ``OrderedDict`` directly so the quoted
    annotations on the LRU caches resolve under ``get_type_hints``.
    """
    import adoptiq_backend

    assert hasattr(adoptiq_backend, "OrderedDict")
    assert adoptiq_backend.OrderedDict is adoptiq_backend._OrderedDictForSchemaCache, (
        "Round 14 / Phase 2.3 expects the alias to point at the unaliased OrderedDict."
    )


# ---------------------------------------------------------------------------
# Phase 2.4 — `locals().get(...)` antipattern replaced
# ---------------------------------------------------------------------------

def test_phase_2_4_no_more_in_locals_antipattern_for_known_names() -> None:
    """The Round 14 fix replaced the bare-name antipattern at four
    specific sites in ``app_simple.py``.  This test enforces the
    invariant by name so a future revert is loud.
    """
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")

    forbidden_pairs = [
        ("csone_df_prepared", "'csone_df_prepared' in locals()"),
        ("csone_path", "'csone_path' in locals()"),
    ]
    for name, snippet in forbidden_pairs:
        assert_not_in_source(src, snippet, label='src')

    # The replacement uses _scope_locals.get so it should appear at
    # least three times.
    assert count_in_source(src, "_scope_locals.get(") >= 3, (
        "Round 14 / Phase 2.4 replacement should appear at all known sites."
    )


# ---------------------------------------------------------------------------
# Phase 3.1 — shutdown handler is defensive
# ---------------------------------------------------------------------------

def test_phase_3_1_shutdown_handler_swallows_closed_stream() -> None:
    """``_shutdown_handler`` must not raise when the logger's underlying
    stream has already been closed by interpreter shutdown.
    """
    import app_simple

    original_logger = app_simple.logger

    class _ClosedStreamLogger:
        def info(self, *_a, **_kw):
            raise ValueError("I/O operation on closed file")

        def error(self, *_a, **_kw):
            raise ValueError("I/O operation on closed file")

    with mock.patch.object(app_simple, "logger", _ClosedStreamLogger()):
        with mock.patch.object(app_simple, "save_analysis_status", lambda: None):
            # Must not raise.
            app_simple._shutdown_handler()

    # Sanity: real logger restored.
    assert app_simple.logger is original_logger
