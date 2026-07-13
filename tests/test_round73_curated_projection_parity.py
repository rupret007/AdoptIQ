"""Round 73 / Phase 2 (F3 + F4) -- curated-projection parity for
Renewal/Compact data sheets.

Build 46 acceptance audit found 3 sheets bypassing the customer-facing
column SSoT (``report_export_schema.CURATED_COLUMNS``):

* F3: Renewal ``Customer_Action_Plans`` shipped 243 cols (raw Snowflake
  dump from ``C360_CS_TASK_C_VW``) -- the Compact sibling
  ``Action_Plans`` shipped at ~30 cols.  Cross-format drift: same source
  data, opposite projection contract.
* F4: Renewal ``Customer_Support_Cases`` (~51 cols) and Compact
  ``All_Support_Cases`` -- both CSOne-shaped subsets, both fell through
  to denylist-only filtering and leaked the full raw ``CSOne_Detail_All``
  projection while ``CSOne_Detail_All`` itself is curated to ~42 cols.

Root cause: the Compact / Renewal inline writers ALREADY route every
sheet through ``_r15_apply_export_schema(df_clean, sheet_name=name)``
(``app_simple.py`` L10767 + L14760).  The bug was upstream in
``CURATED_COLUMNS`` -- the 3 sheet names above were missing entries,
so the projection fell through to denylist-only filtering.

Fix: register the missing sheet names in ``CURATED_COLUMNS``:

* ``Customer_Action_Plans`` -> ``_CURATED_ACTION_PLANS``
* ``All_Support_Cases`` -> ``_CURATED_CSONE_DETAIL_ALL``
* ``Customer_Support_Cases`` -> ``_CURATED_CSONE_DETAIL_ALL``

This module pins the registration AND the runtime parity contract
(Compact ``Action_Plans`` and Renewal ``Customer_Action_Plans`` produce
identically-shaped curated projections for the same input frame).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Registration pin: every targeted sheet must have a curated allow-list
# ---------------------------------------------------------------------------


_R73_REQUIRED_REGISTRATIONS = [
    # (sheet_name, expected_curated_set_attr)
    ("Customer_Action_Plans", "_CURATED_ACTION_PLANS"),
    ("All_Support_Cases", "_CURATED_CSONE_DETAIL_ALL"),
    ("Customer_Support_Cases", "_CURATED_CSONE_DETAIL_ALL"),
]


@pytest.mark.parametrize(
    ("sheet_name", "expected_attr"), _R73_REQUIRED_REGISTRATIONS,
)
def test_curated_columns_registers_sheet_against_canonical_set(
    sheet_name: str, expected_attr: str,
) -> None:
    """Every sheet name surfaced in the Build 46 audit must be in
    ``CURATED_COLUMNS`` mapped to the canonical curated set.  A future
    refactor that drops one of these registrations will fail loud here.
    """

    from report_export_schema import CURATED_COLUMNS  # noqa: PLC0415
    import report_export_schema as schema_mod  # noqa: PLC0415

    assert sheet_name in CURATED_COLUMNS, (
        f"Round 73 / F3-F4: '{sheet_name}' missing from CURATED_COLUMNS -- "
        f"the projection will fall through to denylist-only filtering and "
        f"leak the raw Snowflake dump."
    )
    expected_set = getattr(schema_mod, expected_attr)
    assert CURATED_COLUMNS[sheet_name] is expected_set, (
        f"Round 73 / F3-F4: '{sheet_name}' must map to {expected_attr} "
        f"(got a different curated set)."
    )


# ---------------------------------------------------------------------------
# Runtime parity: Compact and Renewal sibling sheets project to the same shape
# ---------------------------------------------------------------------------


def _make_synthetic_ap_frame(pd):
    """A 2-row AP frame with a wide column mix: curated columns,
    SF-plumbing columns, and unknown columns.  Exercises the full
    denylist + curated-projection pipeline.
    """

    return pd.DataFrame([
        {
            "ID": "AP001",
            "NAME": "Test AP 1",
            "BU_NAME": "Customer A",
            "SUBJECT_C": "Test subject 1",
            "STATUS_C": "Open",
            "OPEN_DATE_C": "2025-01-01",
            # SF/ETL plumbing -- must be dropped by the denylist
            "IS_DELETED": False,
            "MAY_EDIT": True,
            "SYSTEM_MODSTAMP": "2025-01-01T00:00:00",
            "RECORD_TYPE_ID": "0122T000000QHBGQA4",
            "EDWSF_INTERNAL_ID": 12345,
            # Unknown column NOT in curated set -- must be dropped
            "UNKNOWN_RAW_COLUMN_C": "noise",
            "RANDOM_LEGACY_COL": "more noise",
        },
        {
            "ID": "AP002",
            "NAME": "Test AP 2",
            "BU_NAME": "Customer B",
            "SUBJECT_C": "Test subject 2",
            "STATUS_C": "Closed",
            "OPEN_DATE_C": "2025-02-01",
            "IS_DELETED": False,
            "MAY_EDIT": True,
            "SYSTEM_MODSTAMP": "2025-02-01T00:00:00",
            "RECORD_TYPE_ID": "0122T000000QHBGQA4",
            "EDWSF_INTERNAL_ID": 67890,
            "UNKNOWN_RAW_COLUMN_C": "noise",
            "RANDOM_LEGACY_COL": "more noise",
        },
    ])


def test_compact_and_renewal_action_plans_project_to_same_columns() -> None:
    """Cross-format parity: Compact ``Action_Plans`` (already in
    CURATED_COLUMNS pre-R73) and Renewal ``Customer_Action_Plans``
    (newly registered in R73) MUST project the same input frame to the
    same column set.  Pre-R73 they diverged by ~210 cols.
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import apply_export_schema  # noqa: PLC0415

    df = _make_synthetic_ap_frame(pd)

    compact_out = apply_export_schema(df.copy(), sheet_name="Action_Plans")
    renewal_out = apply_export_schema(df.copy(), sheet_name="Customer_Action_Plans")

    assert list(compact_out.columns) == list(renewal_out.columns), (
        f"Round 73 / F3: Compact and Renewal Action Plans projections diverge.\n"
        f"  Compact (Action_Plans): {list(compact_out.columns)}\n"
        f"  Renewal (Customer_Action_Plans): {list(renewal_out.columns)}"
    )
    # Both must be curated -- SF plumbing dropped, raw unknowns dropped.
    for forbidden in ("IS_DELETED", "MAY_EDIT", "SYSTEM_MODSTAMP",
                      "RECORD_TYPE_ID", "EDWSF_INTERNAL_ID",
                      "UNKNOWN_RAW_COLUMN_C", "RANDOM_LEGACY_COL"):
        assert forbidden not in compact_out.columns, (
            f"Round 73 / F3: Compact projection leaked {forbidden!r}"
        )
        assert forbidden not in renewal_out.columns, (
            f"Round 73 / F3: Renewal projection leaked {forbidden!r}"
        )


def _make_synthetic_support_case_frame(pd):
    """A 2-row support case frame mirroring the CSOne shape with a
    healthy mix of curated + SF plumbing + unknown columns.
    """

    return pd.DataFrame([
        {
            "Customer": "Customer A",
            "SUBSCRIPTION_ID": "SUB001",
            "SR Number": "SR-12345",
            "Title": "Test case 1",
            "Severity": "P1",
            "Case Status": "Open",
            "Case Owner": "owner@cisco.com",
            # SF / ETL plumbing
            "IS_DELETED": False,
            "SYSTEM_MODSTAMP": "2025-01-01T00:00:00",
            "EDWSF_INTERNAL_ID": 12345,
            # Unknown raw column
            "RAW_SOQL_REL_LABEL": "noise",
        },
        {
            "Customer": "Customer B",
            "SUBSCRIPTION_ID": "SUB002",
            "SR Number": "SR-67890",
            "Title": "Test case 2",
            "Severity": "P2",
            "Case Status": "Closed",
            "Case Owner": "owner2@cisco.com",
            "IS_DELETED": False,
            "SYSTEM_MODSTAMP": "2025-02-01T00:00:00",
            "EDWSF_INTERNAL_ID": 67890,
            "RAW_SOQL_REL_LABEL": "noise",
        },
    ])


def test_compact_and_renewal_support_cases_project_to_same_columns() -> None:
    """Cross-format parity: Compact ``All_Support_Cases`` and Renewal
    ``Customer_Support_Cases`` MUST project the same input frame to
    the same column set, AND that set must match ``CSOne_Detail_All``
    (all three are CSOne-shaped subsets).
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import apply_export_schema  # noqa: PLC0415

    df = _make_synthetic_support_case_frame(pd)

    compact_out = apply_export_schema(df.copy(), sheet_name="All_Support_Cases")
    renewal_out = apply_export_schema(df.copy(), sheet_name="Customer_Support_Cases")
    csone_out = apply_export_schema(df.copy(), sheet_name="CSOne_Detail_All")

    assert list(compact_out.columns) == list(renewal_out.columns), (
        f"Round 73 / F4: Compact / Renewal support-case projections diverge.\n"
        f"  Compact (All_Support_Cases): {list(compact_out.columns)}\n"
        f"  Renewal (Customer_Support_Cases): {list(renewal_out.columns)}"
    )
    assert list(compact_out.columns) == list(csone_out.columns), (
        f"Round 73 / F4: Support-case sheets must project identically to "
        f"CSOne_Detail_All (same curated set).\n"
        f"  Support cases: {list(compact_out.columns)}\n"
        f"  CSOne_Detail_All: {list(csone_out.columns)}"
    )
    # SF plumbing must be dropped from every support-case projection.
    for forbidden in ("IS_DELETED", "SYSTEM_MODSTAMP",
                      "EDWSF_INTERNAL_ID", "RAW_SOQL_REL_LABEL"):
        assert forbidden not in compact_out.columns
        assert forbidden not in renewal_out.columns


def test_renewal_customer_action_plans_does_not_leak_raw_dump() -> None:
    """Direct regression on F3: a synthetic 50-col raw frame must
    project to the curated set (``len <= len(_CURATED_ACTION_PLANS)``)
    when sheet_name='Customer_Action_Plans' -- pre-R73 it would have
    survived projection at all 50 cols.
    """

    pd = pytest.importorskip("pandas")
    from report_export_schema import (  # noqa: PLC0415
        apply_export_schema,
        _CURATED_ACTION_PLANS,
    )

    raw_cols = {f"RAW_COL_{i:03d}_C": f"value_{i}" for i in range(50)}
    raw_cols.update({
        "ID": "AP001",
        "NAME": "AP",
        "BU_NAME": "Customer X",
        "SUBJECT_C": "Subject",
    })
    df = pd.DataFrame([raw_cols])

    out = apply_export_schema(df, sheet_name="Customer_Action_Plans")

    assert out.shape[1] <= len(_CURATED_ACTION_PLANS), (
        f"Round 73 / F3: Customer_Action_Plans projection survived too "
        f"many cols -- got {out.shape[1]}, expected <= "
        f"{len(_CURATED_ACTION_PLANS)}."
    )
    # Forensic: none of the 50 raw nonsense columns should survive.
    for i in range(50):
        forbidden = f"RAW_COL_{i:03d}_C"
        assert forbidden not in out.columns, (
            f"Round 73 / F3: raw column {forbidden!r} leaked through "
            f"the Customer_Action_Plans projection."
        )
