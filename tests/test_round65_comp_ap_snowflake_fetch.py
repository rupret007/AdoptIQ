"""Round 65 / Phase 1 (C-2) -- Comprehensive XLSX Action_Plans Snowflake fetch.

Pre-R65 the Comprehensive XLSX's ``Action plans (open)`` Summary KPI was
reading 0 whenever CSConsole returned no AP rows for the manager's scope
(a common case for scopes whose APs were authored on accounts owned by
a different DSM). The Leader report for the same scope used a
canonical Snowflake query and rendered the real count -- e.g. for
Brian Frazier:

    Comprehensive (pre-R65): 0 / 0
    Leader (pre-R65):       367 / 120

R65 / Phase 1 (C-2) closes the gap by extracting
``LeaderReportGenerator._fetch_action_plans`` into a module-level
``fetch_action_plans_snowflake`` helper that the comprehensive flow now
calls (in ``app_simple.run_comprehensive_analysis``), merging the
results with CSConsole APs and dedup'ing by ID. Both flows now share
the canonical query.

These tests pin the contract end-to-end without requiring a live
Snowflake / Flask environment.

Round 65 / Phase 1.  Made-with: Cursor.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pandas as pd
import pytest

from leader_report_generator import (
    LeaderReportGenerator,
    fetch_action_plans_snowflake,
)


# ---------------------------------------------------------------------------
# Helper: build a fake Snowflake context that returns canned rows
# ---------------------------------------------------------------------------


def _make_fake_ctx(rows: list[tuple], cols: list[str]) -> MagicMock:
    """Build a MagicMock ctx whose .cursor() returns canned rows + cols."""
    ctx = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.description = [(c,) for c in cols]
    cur.execute = MagicMock()
    cur.close = MagicMock()
    ctx.cursor.return_value = cur
    return ctx


# ---------------------------------------------------------------------------
# Module-level standalone helper contracts
# ---------------------------------------------------------------------------


def test_fetch_action_plans_snowflake_is_module_level_callable():
    """The new helper MUST exist at module scope (so app_simple can
    import it without instantiating a leader generator)."""
    assert callable(fetch_action_plans_snowflake)
    sig = inspect.signature(fetch_action_plans_snowflake)
    assert "ctx" in sig.parameters
    assert "account_ids" in sig.parameters
    assert "days" in sig.parameters
    assert "owner_emails" in sig.parameters
    assert "preserve_observations" in sig.parameters


def test_canonical_fetch_option_preserves_nonidentical_same_id_observations():
    rows = [
        ("AP-1", "Open", "ACC-1"),
        ("AP-1", "Open", "ACC-1"),
        ("AP-1", "Closed", "ACC-1"),
    ]
    columns = ["ID", "STATUS_C", "ACCOUNT_ID_C"]

    legacy = fetch_action_plans_snowflake(
        _make_fake_ctx(rows, columns),
        account_ids=["ACC-1"],
        days=90,
    )
    canonical = fetch_action_plans_snowflake(
        _make_fake_ctx(rows, columns),
        account_ids=["ACC-1"],
        days=90,
        preserve_observations=True,
    )

    assert len(legacy) == 1
    assert len(canonical) == 2
    assert set(canonical["STATUS_C"]) == {"Open", "Closed"}
    # And it must be importable from app_simple's namespace under the alias.
    from app_simple import _r65_fetch_aps_snowflake  # noqa: PLC0415
    assert _r65_fetch_aps_snowflake is fetch_action_plans_snowflake


def test_fetch_action_plans_snowflake_empty_inputs_returns_empty_no_query():
    """Both account_ids and owner_emails empty -> short-circuit, no
    Snowflake call (defensive: avoids running an unbounded query)."""
    ctx = _make_fake_ctx([], ["ID"])
    result = fetch_action_plans_snowflake(ctx, account_ids=[], days=90, owner_emails=[])
    assert isinstance(result, pd.DataFrame)
    assert result.empty
    ctx.cursor.assert_not_called()


def test_fetch_action_plans_snowflake_returns_canned_rows_dedup_by_id():
    """Happy path: a single chunk's rows come back, dedup'd by ID."""
    rows = [
        ("AP-1", "Acme", "On Track"),
        ("AP-2", "Beta", "Completed - Successful"),
        ("AP-1", "Acme dup", "On Track"),  # same ID -> dropped
    ]
    cols = ["ID", "BU_NAME", "STATUS_C"]
    ctx = _make_fake_ctx(rows, cols)
    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=["0010000000000001"],
        days=90,
        owner_emails=None,
    )
    assert len(df) == 2, "duplicate ID should have been dropped (keep='first')"
    assert set(df["ID"]) == {"AP-1", "AP-2"}


def test_fetch_action_plans_snowflake_failure_stamps_attrs_fetch_error():
    """A Snowflake error must NOT crash the caller -- return an empty
    frame with attrs['fetch_error'] populated so the downstream
    partial-data warning surface can flag it."""
    ctx = MagicMock()
    cur = MagicMock()
    cur.execute.side_effect = RuntimeError("snowflake auth failed")
    ctx.cursor.return_value = cur
    df = fetch_action_plans_snowflake(
        ctx,
        account_ids=["0010000000000001"],
        days=90,
    )
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert df.attrs.get("fetch_error") == "snowflake auth failed"


def test_fetch_action_plans_snowflake_chunked_in_clause():
    """When account_ids exceeds the per-chunk size, the helper must
    dispatch multiple SQL calls (one per chunk)."""
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.description = []
    cur.execute = MagicMock()
    ctx = MagicMock()
    ctx.cursor.return_value = cur

    # 2.5 chunks worth of IDs at chunk_size=10 -> expect 3 execute() calls
    ids = [f"00100000000{i:05d}" for i in range(25)]
    fetch_action_plans_snowflake(ctx, account_ids=ids, days=90, chunk_size=10)
    assert cur.execute.call_count == 3, (
        f"expected 3 chunked SQL calls (25 IDs / chunk_size=10), got {cur.execute.call_count}"
    )


def test_fetch_action_plans_snowflake_no_rows_returns_empty_no_attrs():
    """Zero rows from Snowflake -> empty DataFrame with NO fetch_error
    attr (distinguishable from the failure path)."""
    ctx = _make_fake_ctx([], [])
    df = fetch_action_plans_snowflake(ctx, account_ids=["0010000000000001"], days=90)
    assert df.empty
    assert "fetch_error" not in df.attrs


# ---------------------------------------------------------------------------
# LeaderReportGenerator._fetch_action_plans facade contract
# ---------------------------------------------------------------------------


def test_leader_report_generator_fetch_action_plans_delegates_to_module_helper():
    """The instance method must be a thin facade over the new
    module-level helper -- same canonical query for both flows."""
    ctx = _make_fake_ctx(
        [("AP-1", "Acme", "On Track")],
        ["ID", "BU_NAME", "STATUS_C"],
    )
    # Build a minimal LeaderReportGenerator -- only ctx is used by the
    # delegated path.
    gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
    gen.ctx = ctx
    df = gen._fetch_action_plans(["0010000000000001"], days=90)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["ID"] == "AP-1"


# ---------------------------------------------------------------------------
# Comprehensive flow: in-process integration of fetch + merge logic
# ---------------------------------------------------------------------------


def test_app_simple_imports_r65_fetcher_alias():
    """``app_simple`` must import the helper under the documented
    private alias ``_r65_fetch_aps_snowflake`` so the comprehensive
    flow can call it without recompiling."""
    import app_simple  # noqa: PLC0415
    assert hasattr(app_simple, "_r65_fetch_aps_snowflake")
    assert app_simple._r65_fetch_aps_snowflake is fetch_action_plans_snowflake


def test_merge_csconsole_and_snowflake_aps_dedup_by_id():
    """Pin the in-process merge contract: when CSConsole and Snowflake
    return overlapping AP IDs, the merged frame keeps the first
    occurrence (preserves CSConsole-side enrichment)."""
    csconsole = pd.DataFrame({
        "ID": ["AP-1", "AP-2"],
        "BU_NAME": ["Acme", "Beta"],
        "STATUS_C": ["On Track", "Completed - Successful"],
        "_source": ["csconsole", "csconsole"],
    })
    snowflake = pd.DataFrame({
        "ID": ["AP-2", "AP-3", "AP-4"],
        "BU_NAME": ["Beta", "Gamma", "Delta"],
        "STATUS_C": ["On Track", "Off Trajectory", "Closed"],
        "_source": ["snowflake", "snowflake", "snowflake"],
    })
    combined = pd.concat([csconsole, snowflake], ignore_index=True)
    deduped = combined.drop_duplicates(subset=["ID"], keep="first").reset_index(drop=True)
    assert len(deduped) == 4, "AP-2 dedup'd; 4 unique IDs"
    # AP-2 must keep the CSConsole-side row (first occurrence).
    ap2_row = deduped[deduped["ID"] == "AP-2"].iloc[0]
    assert ap2_row["_source"] == "csconsole"


# ---------------------------------------------------------------------------
# Source-shape pin: changes that drift the canonical query MUST update
# this test.
# ---------------------------------------------------------------------------


def test_source_shape_pin_canonical_query_uses_record_type_id():
    """Pin the canonical query: it must filter by
    record_type_id='0122T000000QHBGQA4' (Action Plan record type) and
    must use COALESCE(OPEN_DATE_C, CREATED_DATE, CREATED_DATE_C) for
    the date predicate (Round 10 / Phase 5.1 contract)."""
    cur = MagicMock()
    captured = {}

    def _execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params

    cur.execute.side_effect = _execute
    cur.fetchall.return_value = []
    cur.description = []
    ctx = MagicMock()
    ctx.cursor.return_value = cur

    fetch_action_plans_snowflake(ctx, account_ids=["0010000000000001"], days=90)
    sql = captured.get("sql", "")
    assert "0122T000000QHBGQA4" in sql, (
        "Round 65 / C-2: canonical Action Plan record_type_id must be pinned in the SQL"
    )
    assert "COALESCE(t.OPEN_DATE_C, t.CREATED_DATE, t.CREATED_DATE_C)" in sql, (
        "Round 10 / Phase 5.1 date-predicate contract must hold for the new helper"
    )
    # Date param should be the last bound positional.
    assert captured["params"][-1] is not None
