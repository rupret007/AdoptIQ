"""Round 86 / Build 62 (P0/F2): CSConsole_Action_Plans must be projected
through the same ``_CURATED_ACTION_PLANS`` schema as ``Action_Plans``.

Pre-R86: Build 61 Comprehensive XLSX shipped two AP sheets:
- ``Action_Plans``: 30 cols (curated, R67/B7)
- ``CSConsole_Action_Plans``: 242 cols (raw Snowflake dump, with
  ``CSDF_SYNC_ID_C``, ``GS_C_360_SUCCESS_PRIORITY_C``, ``IS_DELETED``,
  ``MAY_EDIT``, ``MUTE__C``, ``OWNER_ID_C``, and ~210 other internal
  markers leaking through)

Post-R86: ``CSConsole_Action_Plans`` is mapped to the same curated set
in ``CURATED_COLUMNS`` so both sheets ship at ~30 customer-facing cols.

The two sheets continue to coexist (back-compat for downstream consumers
that read ``CSConsole_*`` names specifically); only the projection
changes.
"""

from __future__ import annotations

import pandas as pd

import report_export_schema


# Round 86 / Build 62
def test_csconsole_action_plans_in_curated_columns_map():
    """The fix: ``CSConsole_Action_Plans`` is in the curated map."""
    assert "CSConsole_Action_Plans" in report_export_schema.CURATED_COLUMNS


# Round 86 / Build 62
def test_csconsole_action_plans_uses_same_curated_set_as_action_plans():
    """Sheet parity: same column projection for both AP sheets."""
    assert (
        report_export_schema.CURATED_COLUMNS["CSConsole_Action_Plans"]
        is report_export_schema.CURATED_COLUMNS["Action_Plans"]
    )


# Round 86 / Build 62
def test_csconsole_action_plans_curated_set_is_action_plans_tuple():
    """Verify the curated set is the canonical AP tuple."""
    expected = report_export_schema._CURATED_ACTION_PLANS  # type: ignore[attr-defined]
    actual = report_export_schema.CURATED_COLUMNS["CSConsole_Action_Plans"]
    assert actual is expected


# Round 86 / Build 62
def test_curated_action_plans_drops_internal_markers():
    """Negative control: the curated set MUST NOT contain internal markers."""
    curated = report_export_schema.CURATED_COLUMNS["CSConsole_Action_Plans"]
    blocked_substrings = (
        "IS_DELETED",
        "MAY_EDIT",
        "MUTE__C",
        "CSDF_SYNC",
        "GS_C_",
        "SFDC_",
        "OWNER_ID_C",
    )
    for col in curated:
        upper = str(col).upper()
        for marker in blocked_substrings:
            assert marker not in upper, (
                f"Curated AP set leaks internal marker {marker} via column "
                f"{col!r} -- regression of R67/B7 + R86 contracts."
            )


# Round 86 / Build 62
def test_curated_action_plans_count_at_most_50_cols():
    """The curated set stays bounded despite the R167 drill-through URL."""
    curated = report_export_schema.CURATED_COLUMNS["CSConsole_Action_Plans"]
    assert len(curated) <= 50, (
        f"Curated AP set has {len(curated)} cols -- raw dump regression"
    )
    assert "Source_Record_URL" in curated


# Round 86 / Build 62
def test_curated_action_plans_carries_customer_facing_columns():
    """Positive: curated set MUST carry the customer-facing AP fields
    (using their canonical Snowflake column names)."""
    curated = set(report_export_schema.CURATED_COLUMNS["CSConsole_Action_Plans"])
    expected_subset = {
        "ACCOUNT_ID_C",
        "ACCOUNT_MANAGER_C",
        "ACTION_PLAN_TITLE_C",
        "STATUS_C",
    }
    missing = expected_subset - curated
    assert not missing, (
        f"Curated AP set missing customer-facing columns: {missing}"
    )


# Round 86 / Build 62
def test_writer_routes_csconsole_action_plans_through_apply_export_schema():
    """End-to-end: writer's curated projection drops the raw 242-col dump
    when the sheet is named ``CSConsole_Action_Plans``."""
    raw_columns = list(report_export_schema._CURATED_ACTION_PLANS) + [
        "IS_DELETED",
        "MAY_EDIT",
        "CSDF_SYNC_ID_C",
        "GS_C_360_SUCCESS_PRIORITY_C",
        "OWNER_ID_C",
    ]
    raw_df = pd.DataFrame(
        [{col: f"raw_{i}" for col in raw_columns} for i in range(3)]
    )
    assert raw_df.shape[1] == len(raw_columns)
    assert raw_df.shape[1] > 30, "fixture pre-condition broken"

    projected = report_export_schema.apply_export_schema(
        raw_df, "CSConsole_Action_Plans"
    )
    # Internal markers must be dropped.
    assert "IS_DELETED" not in projected.columns
    assert "MAY_EDIT" not in projected.columns
    assert "CSDF_SYNC_ID_C" not in projected.columns
    assert "GS_C_360_SUCCESS_PRIORITY_C" not in projected.columns
    assert "OWNER_ID_C" not in projected.columns
    # Customer-facing columns are renamed via SHEET_HEADER_RENAMES.
    # Verify both raw + friendly forms are accepted (defense in depth).
    cols = set(projected.columns)
    assert "ACCOUNT_ID_C" in cols or "Account ID" in cols
    assert "ACTION_PLAN_TITLE_C" in cols or "Action Plan Title" in cols
    assert projected.shape[1] <= 50, (
        f"projected df has {projected.shape[1]} cols -- raw dump regression"
    )


# Round 86 / Build 62
def test_back_compat_csconsole_customer_pulse_unchanged():
    """Round 86 must not regress the existing CSConsole_Customer_Pulse map."""
    assert "CSConsole_Customer_Pulse" in report_export_schema.CURATED_COLUMNS
    assert (
        report_export_schema.CURATED_COLUMNS["CSConsole_Customer_Pulse"]
        is report_export_schema._CURATED_CSCONSOLE_CUSTOMER_PULSE
    )
