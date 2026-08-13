"""Round 67 / Build 41 (B7) -- Compact XLSX schema projection.

Build 40 acceptance found the Compact XLSX shipping with two raw
Snowflake dump sheets:

- ``Critical_Adoption_Barriers``: 259 columns
- ``Action_Plans``: 243 columns

Both contained internal SF / ETL plumbing (IS_DELETED, MAY_EDIT,
MUTE__C, audit fields, system-tracker columns), making the sheets
unusable for operators.

Round 67 / B7 fix: route both sheets through ``_r15_apply_export_schema``
which projects them to the customer-facing column subset defined in
``report_export_schema.CURATED_COLUMNS`` (~30 columns each).
"""
from __future__ import annotations

import pandas as pd
import pytest

from report_export_schema import (
    CURATED_COLUMNS,
    apply_export_schema,
)


def test_critical_adoption_barriers_has_curated_column_set() -> None:
    """R67/B7: Critical_Adoption_Barriers MUST be mapped to a curated
    column subset (so a 243-col Snowflake dump shrinks to <=81 cols).

    Round 167 intentionally spends one additional public column on the safe,
    allowlisted CSConsole record hyperlink requested by report users.
    """
    assert "Critical_Adoption_Barriers" in CURATED_COLUMNS, (
        "R67/B7: 'Critical_Adoption_Barriers' MUST be a key in CURATED_COLUMNS"
    )
    cols = CURATED_COLUMNS["Critical_Adoption_Barriers"]
    assert isinstance(cols, tuple)
    assert len(cols) > 0, "curated subset MUST not be empty"
    assert len(cols) <= 81, (
        f"R67/B7+R167: curated subset MUST be <=81 columns; got {len(cols)}"
    )
    assert "Source_Record_URL" in cols


def test_action_plans_has_curated_column_set() -> None:
    """R67/B7: Action_Plans MUST be mapped to a curated column subset
    (so a 243-col Snowflake dump shrinks to <=80 cols)."""
    assert "Action_Plans" in CURATED_COLUMNS, (
        "R67/B7: 'Action_Plans' MUST be a key in CURATED_COLUMNS"
    )
    cols = CURATED_COLUMNS["Action_Plans"]
    assert isinstance(cols, tuple)
    assert len(cols) > 0, "curated subset MUST not be empty"
    assert len(cols) <= 80, (
        f"R67/B7: curated subset MUST be <=80 columns; got {len(cols)}"
    )


def test_apply_export_schema_drops_internal_columns_for_action_plans() -> None:
    """End-to-end: a wide Snowflake-style frame MUST shrink to the
    curated subset after schema projection."""
    # Synthesise a wide frame with curated + internal columns.
    curated_cols = list(CURATED_COLUMNS["Action_Plans"])
    internal_cols = [
        "IS_DELETED",
        "MAY_EDIT",
        "MUTE__C",
        "_AUDIT_TRACKER",
        "INTERNAL_FLAG_99",
    ]
    all_cols = curated_cols + internal_cols
    df = pd.DataFrame([{col: f"v{i}" for i, col in enumerate(all_cols)}])
    # Synthetic frame must include at least the curated set plus the
    # internal columns we want to confirm are dropped.
    assert len(df.columns) == len(curated_cols) + len(internal_cols)

    projected = apply_export_schema(df, sheet_name="Action_Plans")
    # Internal columns MUST be dropped.
    for bad in internal_cols:
        assert bad not in projected.columns, (
            f"R67/B7: internal column '{bad}' MUST be dropped from Action_Plans"
        )
    # The result MUST be at most the curated set size.
    assert len(projected.columns) <= len(curated_cols), (
        f"R67/B7: projected frame MUST not exceed curated set; "
        f"got {len(projected.columns)} vs allowed {len(curated_cols)}"
    )


def test_apply_export_schema_drops_internal_columns_for_critical_abs() -> None:
    """Same end-to-end check for Critical_Adoption_Barriers."""
    curated_cols = list(CURATED_COLUMNS["Critical_Adoption_Barriers"])
    internal_cols = [
        "IS_DELETED",
        "MAY_EDIT",
        "MUTE__C",
        "_PIPELINE_MARKER",
    ]
    all_cols = curated_cols + internal_cols
    df = pd.DataFrame([{col: f"v{i}" for i, col in enumerate(all_cols)}])
    projected = apply_export_schema(df, sheet_name="Critical_Adoption_Barriers")
    for bad in internal_cols:
        assert bad not in projected.columns, (
            f"R67/B7: '{bad}' MUST be dropped from Critical_Adoption_Barriers"
        )
    assert len(projected.columns) <= len(curated_cols)


def test_compact_writer_calls_export_schema() -> None:
    """The Compact writer at app_simple.run_compact_analysis MUST
    invoke ``_r15_apply_export_schema`` for every sheet so the
    curated projection happens before write."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text(encoding="utf-8")
    assert "_r15_apply_export_schema(df_clean, sheet_name=sheet_name)" in src, (
        "R67/B7: the Compact writer MUST call _r15_apply_export_schema "
        "per sheet so curated subsets land in the produced XLSX"
    )


def test_curated_subset_includes_critical_action_plan_fields() -> None:
    """Smoke check: the curated Action_Plans subset MUST cover the
    operator-essential fields (so the projection is not over-aggressive)."""
    cols = set(CURATED_COLUMNS["Action_Plans"])
    # At least one of the standard customer-name + status-bearing fields
    # must remain after projection. We tolerate alternate naming.
    name_field_present = any(
        c in cols for c in ("BU_NAME", "Customer_Name", "RELATED_CUSTOMER__C", "Customer Name")
    )
    assert name_field_present, (
        "R67/B7: the curated Action_Plans subset MUST retain at least one "
        "customer-name field so the operator can join rows back to the "
        "renewal portfolio"
    )
