"""Round 73 / Phase 3 (F6): canonical ``Item / Value`` Report_Info schema.

Pre-Round 73 the Renewal writer used ``{'Field': ..., 'Value': ...}`` pairs
and the Leader writer used ``{'Field': ..., 'Value': ..., 'Detail': ...,
'Generated_At': ...}`` 4-col rows.  Compact, Comprehensive, and the Round 64
provenance helper had already standardised on ``Item / Value``.  This drift
broke downstream consumers (and the Build 46 audit harness) that ran
``pd.read_excel("Report_Info")[["Item", "Value"]]`` -- the call returned a
clean DataFrame for Compact / Comprehensive and a ``KeyError`` for Renewal /
Leader.

R73 / F6 collapses the schema across every writer:

* Renewal: ``Item / Value`` (was ``Field / Value``).
* Leader: ``Item / Value / Detail / Generated_At`` (was
  ``Field / Value / Detail / Generated_At``).  The 4-col legacy shape is
  preserved -- the per-row Detail and Generated_At columns carry provenance
  the other writers don't need -- but the FIRST TWO columns are now the
  canonical ``Item / Value`` subset so a downstream consumer that only
  cares about Item/Value pairs can read every report format with the same
  one-liner.

This file pins the source-shape (string-grep on app_simple.py + the helper
module) AND the runtime contract (calling the helper directly, eyeballing
the records list).
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helper-level pins -- the helper now writes ``Item`` not ``Field``.
# ---------------------------------------------------------------------------


def test_append_records_4col_uses_canonical_item_key():
    """``append_build_label_records_4col`` writes ``Item`` (not ``Field``).

    R73 / F6: the Leader writer's 4-col Report_Info schema is now keyed
    on ``Item`` so the first-column header matches Compact / Renewal /
    Comprehensive.
    """

    from _r68_build_label import append_build_label_records_4col  # noqa: PLC0415

    records: list = []
    append_build_label_records_4col(records)
    assert len(records) == 4
    for r in records:
        assert "Item" in r, f"Round 73 / F6: 4-col record missing Item key: {r!r}"
        assert "Value" in r, f"Round 73 / F6: 4-col record missing Value key: {r!r}"
        assert "Field" not in r, (
            f"Round 73 / F6: 4-col record carries legacy Field key: {r!r}"
        )
        assert set(r.keys()) == {"Item", "Value", "Detail", "Generated_At"}


def test_append_records_4col_first_two_cols_are_item_value_subset():
    """The 4-col shape's first two columns must form the canonical 2-col
    ``Item / Value`` subset so a downstream consumer running
    ``pd.read_excel("Report_Info")[["Item", "Value"]]`` works on Leader."""

    pd = pytest.importorskip("pandas")

    from _r68_build_label import append_build_label_records_4col  # noqa: PLC0415

    records: list = []
    append_build_label_records_4col(records)
    df = pd.DataFrame(records)
    # Project to the canonical Item / Value subset; the call must succeed
    # without a KeyError on the Leader 4-col shape.
    sub = df[["Item", "Value"]]
    assert len(sub) == 4
    items = set(sub["Item"].astype(str).tolist())
    assert {
        "App_Version",
        "App_Build",
        "Process_Started_At_UTC",
        "Report_Generated_At_UTC",
    } <= items


def test_append_records_default_key_is_item():
    """Default-arg pin: ``append_build_label_records`` uses ``Item`` as the
    default key_field so callers that don't pass an override get the
    canonical schema."""

    import inspect

    from _r68_build_label import append_build_label_records  # noqa: PLC0415

    sig = inspect.signature(append_build_label_records)
    assert sig.parameters["key_field"].default == "Item"
    assert sig.parameters["value_field"].default == "Value"


# ---------------------------------------------------------------------------
# Source-shape pins on app_simple.py -- the Renewal + Leader writers must
# emit dicts keyed on ``Item`` (not ``Field``) for Report_Info construction.
# ---------------------------------------------------------------------------


def test_renewal_report_info_source_uses_item_key():
    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Round 73 / Phase 3 (F6) marker comment must be present so a future
    # revert that drops the canonical schema fails this assertion.
    assert "Round 73 / Phase 3 (F6)" in body, (
        "Round 73 / F6: source marker missing -- canonical Renewal Report_Info "
        "schema may have been reverted"
    )
    # Specifically: the Renewal _report_info_rows construction must use
    # ``'Item': '...'`` not ``'Field': '...'`` for Report_Type / Customer_Name /
    # Technology / Manager / Days / Analysis_Id / Generated_At_UTC /
    # Partial_Data_Warning_Count / Sheet_Title:* keys.
    expected_renewal_item_rows = (
        "{'Item': 'Report_Type', 'Value': str(renewal_type or 'renewal')},",
        "{'Item': 'Customer_Name', 'Value': str(customer_name_for_report)},",
        "{'Item': 'Days', 'Value': str(days)},",
        "{'Item': 'Analysis_Id', 'Value': str(analysis_id)},",
    )
    for needle in expected_renewal_item_rows:
        assert needle in body, (
            f"Round 73 / F6: Renewal Report_Info row {needle!r} not found "
            "in app_simple.py -- the canonical Item/Value schema is missing"
        )


def test_renewal_report_info_source_no_legacy_field_keys():
    """No remaining ``'Field': '...'`` strings in the Renewal Report_Info
    construction block."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Look for any of the pre-R73 Renewal-specific keys that would indicate
    # the rename was incomplete.
    legacy_renewal_field_rows = (
        "{'Field': 'Report_Type'",
        "{'Field': 'Customer_Name'",
        "{'Field': 'Technology', 'Value': str(technology",
        "{'Field': 'Days', 'Value': str(days)}",
        "{'Field': 'Analysis_Id'",
    )
    for legacy in legacy_renewal_field_rows:
        assert legacy not in body, (
            f"Round 73 / F6: legacy Renewal Report_Info row {legacy!r} still "
            "present in app_simple.py -- the rename to Item/Value is incomplete"
        )


def test_leader_report_info_source_uses_item_key():
    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Leader-specific R73 marker comment.
    assert (
        "Round 73 / Phase 3 (F6): standardize Leader Report_Info" in body
    ), (
        "Round 73 / F6: Leader source marker missing -- canonical Leader "
        "Report_Info schema may have been reverted"
    )
    # The Leader 4-col rows must now use 'Item' as the first key.
    expected_leader_item_rows = (
        "{'Item': 'Status', 'Value': 'PARTIAL'",
        "{'Item': 'Manager', 'Value': str(manager",
        "{'Item': 'Sheets_Written',",
    )
    for needle in expected_leader_item_rows:
        assert needle in body, (
            f"Round 73 / F6: Leader Report_Info row starting {needle!r} not "
            "found in app_simple.py -- the canonical Item/Value schema is "
            "missing for Leader"
        )


def test_renewal_build_label_call_uses_item_keys():
    """The Renewal writer's ``append_build_label_records`` call must pass
    ``key_field='Item'`` (was ``key_field='Field'`` pre-R73)."""

    body = (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert (
        "key_field='Item', value_field='Value'" in body
        or 'key_field="Item"' in body
    ), (
        "Round 73 / F6: Renewal append_build_label_records must use "
        "key_field='Item' (canonical) not key_field='Field' (legacy)"
    )
    assert "key_field='Field'" not in body, (
        "Round 73 / F6: Renewal append_build_label_records still uses "
        "legacy key_field='Field' -- rename incomplete"
    )


def test_leader_4col_helper_source_uses_item_first_column():
    """The shared 4-col helper module emits ``Item`` (not ``Field``) as the
    first column header."""

    body = (PROJECT_ROOT / "_r68_build_label.py").read_text(encoding="utf-8")
    # The 4-col helper body must contain ``"Item": item,`` (canonical) and
    # NOT ``"Field": item,`` (legacy).
    assert '"Item": item,' in body, (
        "Round 73 / F6: append_build_label_records_4col must use 'Item' as "
        "the first column header (canonical schema)"
    )
    assert '"Field": item,' not in body, (
        "Round 73 / F6: append_build_label_records_4col still emits the "
        "legacy 'Field' header -- rename incomplete"
    )


# ---------------------------------------------------------------------------
# End-to-end runtime pin: Renewal-shaped + Leader-shaped Report_Info
# DataFrames built via the helpers must carry the canonical Item column.
# ---------------------------------------------------------------------------


def test_runtime_renewal_shape_carries_item_column():
    """Build a minimal Renewal-shaped Report_Info DataFrame the way
    app_simple.py does and verify the resulting workbook reads back with
    the canonical ``Item`` column header."""

    pd = pytest.importorskip("pandas")

    from _r68_build_label import append_build_label_records  # noqa: PLC0415

    rows = [
        {"Item": "Report_Type", "Value": "renewal_portfolio"},
        {"Item": "Days", "Value": "90"},
    ]
    append_build_label_records(rows, key_field="Item", value_field="Value")
    df = pd.DataFrame(rows)
    assert "Item" in df.columns
    assert "Value" in df.columns
    assert "Field" not in df.columns
    items = set(df["Item"].astype(str).tolist())
    assert {"Report_Type", "Days", "App_Version", "App_Build"} <= items


def test_runtime_leader_shape_carries_item_column_and_legacy_4col():
    """Build a minimal Leader-shaped 4-col Report_Info DataFrame the way
    app_simple.py does and verify the resulting workbook carries the
    canonical ``Item / Value / Detail / Generated_At`` schema."""

    pd = pytest.importorskip("pandas")

    from _r68_build_label import append_build_label_records_4col  # noqa: PLC0415

    rows = [
        {"Item": "Status", "Value": "OK", "Detail": "", "Generated_At": "2026-05-03T17:00:00Z"},
        {"Item": "Manager", "Value": "Brian Frazier", "Detail": "", "Generated_At": ""},
    ]
    append_build_label_records_4col(rows)
    df = pd.DataFrame(rows)
    # All four canonical columns must be present.
    assert {"Item", "Value", "Detail", "Generated_At"} == set(df.columns)
    assert "Field" not in df.columns

    # The Item/Value subset must be projectable in one call.
    sub = df[["Item", "Value"]]
    assert len(sub) == len(df)
    items = set(sub["Item"].astype(str).tolist())
    assert {
        "Status",
        "Manager",
        "App_Version",
        "App_Build",
        "Process_Started_At_UTC",
        "Report_Generated_At_UTC",
    } <= items
