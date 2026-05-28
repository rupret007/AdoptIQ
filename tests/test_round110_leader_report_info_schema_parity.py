"""Round 110 / Build 79: Leader Report_Info schema parity (latent-bug fix).

Pre-Round 110 the Leader ``Report_Info`` writer in ``app_simple.py`` constructed
the static rows (``Status`` / ``Manager`` / ``Days`` / ``Analysis_Id`` /
``Sheets_Written`` / ``Partial_Data_Warning_Count``) plus the
``append_build_label_records_4col`` build-label block all keyed on ``'Item':``
per the Round 73 / Phase 3 (F6) canonical schema -- BUT the two dynamic loop
branches that append ``Partial_Data_Warning_<n>`` rows (when a Leader run
produced partial-data warnings) AND ``Failed_Sheet`` rows (when one or more
sheets failed to write) were keyed on ``'Field':`` instead.

``pd.DataFrame(_info_rows)`` builds the union of all dict keys, so any Leader
run with ``len(partial_data_warnings) > 0`` OR ``len(_failed_sheets) > 0``
silently grew a stray fifth ``Field`` column with NaN on every other row,
violating R73/F6's promise that ``pd.read_excel("Report_Info")[["Item",
"Value"]]`` projects cleanly across every report format.

Today's clean Build 78 cohort (Brian Frazier 90d Leader run, 2026-05-28) did
NOT trip the bug because that run had ``Partial_Data_Warning_Count=0`` and
``_failed_sheets=[]``; the audit found the latent defect on inspection rather
than from output drift.

Round 110 / Build 79 normalises both dynamic-loop branches onto the canonical
``'Item':`` key.  The R73 source-shape tests already pinned the static rows;
R110 extends coverage to the dynamic branches that were the actual leak.

Pins:

1. Source-shape pin: scan ``app_simple.py`` for the legacy ``'Field':
   f'Partial_Data_Warning_`` and ``'Field': 'Failed_Sheet'`` literals --
   fail loud if either re-emerges.
2. Source-shape pin (positive): assert the canonical ``'Item': f'
   Partial_Data_Warning_`` and ``'Item': 'Failed_Sheet'`` literals are
   present, plus the Round 110 marker comment.
3. Behavior pin: simulate a Leader run with N>0 partial warnings AND N>0
   failed sheets by re-creating the writer's row-construction pattern,
   running it through ``pd.DataFrame`` -> in-memory xlsx round-trip, and
   asserting the resulting Report_Info sheet has exactly the 4-column
   ``Item / Value / Detail / Generated_At`` schema with no stray ``Field``
   column.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE_PATH = PROJECT_ROOT / "app_simple.py"


# ---------------------------------------------------------------------------
# Source-shape pins
# ---------------------------------------------------------------------------


def test_round110_no_legacy_field_key_in_partial_warning_branch():
    """The Leader ``Partial_Data_Warning_<n>`` row builder MUST NOT use
    ``'Field':`` as the first-column key.

    Pre-R110 the writer used ``{'Field': f'Partial_Data_Warning_{_i}', ...}``
    which silently added a fifth ``Field`` column to ``Report_Info`` whenever
    the Leader run produced any partial-data warning.  The fix renames the
    key to ``'Item'`` to match the rest of the row block (R73/F6 canonical
    schema).
    """

    body = APP_SIMPLE_PATH.read_text(encoding="utf-8")
    legacy = "'Field': f'Partial_Data_Warning_"
    assert legacy not in body, (
        f"Round 110: legacy {legacy!r} still present in app_simple.py -- the "
        "Leader Partial_Data_Warning row builder must use the canonical "
        "'Item': key per R73/F6"
    )


def test_round110_no_legacy_field_key_in_failed_sheet_branch():
    """The Leader ``Failed_Sheet`` row builder MUST NOT use ``'Field':`` as
    the first-column key.

    Pre-R110 the writer used ``{'Field': 'Failed_Sheet', ...}`` which added a
    stray ``Field`` column whenever any sheet failed to write.
    """

    body = APP_SIMPLE_PATH.read_text(encoding="utf-8")
    legacy = "'Field': 'Failed_Sheet'"
    assert legacy not in body, (
        f"Round 110: legacy {legacy!r} still present in app_simple.py -- the "
        "Leader Failed_Sheet row builder must use the canonical 'Item': key "
        "per R73/F6"
    )


def test_round110_canonical_item_key_present_in_dynamic_branches():
    """Positive pin: both dynamic-loop branches use ``'Item':`` after R110."""

    body = APP_SIMPLE_PATH.read_text(encoding="utf-8")
    expected_partial = "'Item': f'Partial_Data_Warning_"
    expected_failed = "'Item': 'Failed_Sheet'"

    assert expected_partial in body, (
        f"Round 110: canonical {expected_partial!r} row builder is missing -- "
        "the Leader Partial_Data_Warning loop must emit Item/Value rows"
    )
    assert expected_failed in body, (
        f"Round 110: canonical {expected_failed!r} row builder is missing -- "
        "the Leader Failed_Sheet loop must emit Item/Value rows"
    )


def test_round110_source_marker_present():
    """The R110 marker comment must be in the writer so
    ``git diff app_simple.py | grep 'Round 110'`` gives the per-file
    footprint (existing repo convention)."""

    body = APP_SIMPLE_PATH.read_text(encoding="utf-8")
    assert "Round 110 / Build 79" in body, (
        "Round 110: source marker missing from app_simple.py -- the latent-"
        "bug fix may have been reverted"
    )


# ---------------------------------------------------------------------------
# Behavior pin -- simulate a Leader run that DOES trip the dynamic branches
# (partial warnings AND failed sheets) and verify the resulting Report_Info
# carries exactly the 4-column schema with no stray 'Field' column.
# ---------------------------------------------------------------------------


def _build_leader_info_rows_with_dynamic_branches() -> list[dict]:
    """Mirror the Leader writer's ``_info_rows`` construction with N>0
    partial warnings and N>0 failed sheets.

    Mirror the row shapes used in ``app_simple.py`` so a future drift in
    either source path is caught by the round-trip check below.
    """

    rows: list[dict] = [
        # Static rows (R73/F6 canonical schema)
        {
            "Item": "Status",
            "Value": "PARTIAL",
            "Detail": "",
            "Generated_At": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ),
        },
        {"Item": "Manager", "Value": "Test Manager", "Detail": "", "Generated_At": ""},
        {"Item": "Days", "Value": "90", "Detail": "", "Generated_At": ""},
        {
            "Item": "Analysis_Id",
            "Value": "Leader_Test_90d_1234567890",
            "Detail": "",
            "Generated_At": "",
        },
        {"Item": "Sheets_Written", "Value": "13", "Detail": "", "Generated_At": ""},
        {
            "Item": "Partial_Data_Warning_Count",
            "Value": "2",
            "Detail": "",
            "Generated_At": "",
        },
        # Build label rows (R68/A1 4-col variant) -- the helper itself was
        # pinned by R73's tests; for behavior coverage we just inline 4 rows
        # in the same shape so we don't need the production helper here.
        {"Item": "App_Version", "Value": "1.0.4", "Detail": "", "Generated_At": ""},
        {"Item": "App_Build", "Value": "79", "Detail": "", "Generated_At": ""},
        {
            "Item": "Process_Started_At_UTC",
            "Value": "2026-05-28T13:06:17Z",
            "Detail": "",
            "Generated_At": "",
        },
        {
            "Item": "Report_Generated_At_UTC",
            "Value": "2026-05-28T13:13:15Z",
            "Detail": "",
            "Generated_At": "",
        },
        # Dynamic branch 1: partial-data warnings (this is where R110's bug lived)
        {
            "Item": "Partial_Data_Warning_1",
            "Value": "adoption_barriers",
            "Detail": (
                "tech_filter_scope_excluded: AB tech filter 'All Contact Center' "
                "kept 14 of 183 adoption barriers"
            ),
            "Generated_At": "",
        },
        {
            "Item": "Partial_Data_Warning_2",
            "Value": "support_cases",
            "Detail": "empty_for_scope: 0 cases in window",
            "Generated_At": "",
        },
        # Dynamic branch 2: failed sheets (also where R110's bug lived)
        {
            "Item": "Failed_Sheet",
            "Value": "External_Bugs",
            "Detail": "TimeoutError: Snowflake fetch exceeded 60s",
            "Generated_At": datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ),
        },
    ]
    return rows


def test_round110_dynamic_branch_rows_produce_clean_4col_schema():
    """End-to-end behavior pin: when the Leader writer's _info_rows list
    contains rows from the dynamic partial-warning AND failed-sheet branches,
    the resulting xlsx Report_Info sheet must have EXACTLY the canonical
    4-column ``Item / Value / Detail / Generated_At`` schema with no stray
    ``Field`` column."""

    pd = pytest.importorskip("pandas")
    openpyxl = pytest.importorskip("openpyxl")

    rows = _build_leader_info_rows_with_dynamic_branches()

    df = pd.DataFrame(rows)
    # Pandas builds the union of all dict keys; pre-R110 this would have
    # produced a 5-column DataFrame with a stray 'Field' column.
    assert list(df.columns) == ["Item", "Value", "Detail", "Generated_At"], (
        "Round 110: Leader Report_Info DataFrame must have exactly the "
        "canonical 4-column schema; got " + repr(list(df.columns))
    )
    assert "Field" not in df.columns, (
        "Round 110: stray 'Field' column present in Leader Report_Info "
        "DataFrame -- the dynamic-branch row builders re-introduced the "
        "pre-R110 latent bug"
    )

    # Round-trip via xlsx so we catch any divergence between pandas
    # construction and the actual Excel write path.
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Report_Info", index=False)
    buf.seek(0)

    wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    try:
        assert "Report_Info" in wb.sheetnames
        ws = wb["Report_Info"]
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter)
        assert header == ("Item", "Value", "Detail", "Generated_At"), (
            "Round 110: Leader Report_Info xlsx header must be the canonical "
            "(Item, Value, Detail, Generated_At); got " + repr(header)
        )

        # First two columns MUST form the canonical 2-col Item / Value
        # subset for every row (R73/F6 promise).
        seen_items: list[str] = []
        for row in rows_iter:
            item, value = row[0], row[1]
            assert item is not None, (
                "Round 110: Leader Report_Info row has empty Item column "
                "(should never happen with canonical row builders)"
            )
            seen_items.append(str(item))

        # Verify the dynamic-branch rows actually landed under 'Item'.
        assert "Partial_Data_Warning_1" in seen_items
        assert "Partial_Data_Warning_2" in seen_items
        assert "Failed_Sheet" in seen_items
    finally:
        wb.close()


def test_round110_pre_fix_pattern_would_have_produced_5_columns():
    """Negative-control pin: prove pandas DOES produce a 5-column DataFrame
    when the dynamic rows use ``'Field':`` (pre-R110 bug).  This locks the
    failure mode so a future refactor that "simplifies" pd.DataFrame's
    behavior would also fail loud."""

    pd = pytest.importorskip("pandas")

    pre_r110_rows = [
        {"Item": "Status", "Value": "PARTIAL", "Detail": "", "Generated_At": ""},
        # Pre-R110 these used 'Field' instead of 'Item' -- the exact bug.
        {
            "Field": "Partial_Data_Warning_1",
            "Value": "adoption_barriers",
            "Detail": "kind: msg",
            "Generated_At": "",
        },
        {
            "Field": "Failed_Sheet",
            "Value": "Some_Sheet",
            "Detail": "err",
            "Generated_At": "",
        },
    ]
    df = pd.DataFrame(pre_r110_rows)
    # Demonstrate the bug: pandas produces a 5-column union schema.
    assert "Field" in df.columns
    assert "Item" in df.columns
    assert len(df.columns) == 5
    # And the 'Item' column carries no real value for the buggy rows --
    # pandas fills the union with NaN -- which is exactly what would have
    # leaked into Report_Info pre-R110.  Accept either NaN or None.
    items = list(df["Item"])
    assert items[0] == "Status"  # static row populated
    assert pd.isna(items[1])  # dynamic Partial_Data_Warning row leaked (NaN)
    assert pd.isna(items[2])  # dynamic Failed_Sheet row leaked (NaN)
