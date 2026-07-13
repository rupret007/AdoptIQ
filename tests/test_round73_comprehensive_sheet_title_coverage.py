"""Round 73 / Phase 3 (F8): Comprehensive Report_Info Sheet_Title coverage.

Pre-Round 73 the Comprehensive XLSX writer
(``adoptiq_backend.write_excel_workbook``) generated a
``Sheet_Title:<sheet>`` row in Report_Info ONLY for the regular
``sheets`` dict, with ``Summary`` explicitly excluded. The Comprehensive
writer ALSO writes:

* The R15 ``Summary`` sheet via a separate ``_r15_write_summary_sheet``
  call (this sheet is the one the user lands on when they double-click
  the file -- no title row is the most painful gap).
* The four ``CSConsole_*`` sheets (``CSConsole_Action_Plans`` /
  ``CSConsole_Customer_Pulse`` / ``CSConsole_Success_Priorities`` /
  ``CSConsole_Adoption_Barriers``) via a separate ``csconsole_sheet_names``
  write loop that consumes the ``csconsole_data`` argument.

Neither set was represented in ``sheets`` at the point the Sheet_Title
loop ran, so the Build 46 audit found Comprehensive Report_Info missing
``Sheet_Title:Summary`` AND any ``Sheet_Title:CSConsole_*`` rows -- a
parity gap with the R65/R-1 Compact / Renewal / Leader writers (which
DO carry ``Sheet_Title:Summary``).

This file pins the F8 fix at three layers:

* Source-shape pin on ``adoptiq_backend.py`` -- the R73 / F8 marker
  comment must be present so a future revert fails this assertion.
* Runtime pin via direct ``write_excel_workbook`` invocation -- the
  produced XLSX must carry ``Sheet_Title:Summary`` AND every populated
  ``Sheet_Title:CSConsole_*`` row.
* Regression pin on the existing ``sheets`` dict iteration -- regular
  sheets STILL get a Sheet_Title row (R66/B5 contract preserved).
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Source-shape pin: R73/F8 marker + the three concrete code shapes the
# fix introduced.
# ---------------------------------------------------------------------------


def test_source_marker_for_r73_f8_is_present():
    body = (PROJECT_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 73 / Phase 3 (F8)" in body, (
        "Round 73 / F8: source marker missing -- the Sheet_Title coverage "
        "backfill may have been reverted"
    )


def test_summary_is_appended_unconditionally_to_data_sheet_names():
    """The R73/F8 fix prepends ``Summary`` to the Sheet_Title backfill
    list before the ``sheets`` loop runs."""

    body = (PROJECT_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The unconditional ``Summary`` append must be present and clearly
    # comment-marked so a future refactor cannot silently drop it.
    assert (
        '_r66_b5_data_sheet_names.append("Summary")' in body
    ), (
        "Round 73 / F8: Comprehensive writer must unconditionally append "
        '\"Summary\" to the Sheet_Title backfill list'
    )


def test_csconsole_sheet_titles_are_iterated_via_csconsole_data():
    """The R73/F8 fix iterates ``csconsole_sheet_names`` and only appends
    rows for non-empty source DataFrames -- mirroring the gating the
    actual writer uses below."""

    body = (PROJECT_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    # The iteration body (uses ``csconsole_data.get(_ck)`` + non-empty
    # check) must be present.
    assert "for _ck, _csname in csconsole_sheet_names.items():" in body, (
        "Round 73 / F8: Comprehensive writer must iterate "
        "csconsole_sheet_names to backfill Sheet_Title rows for the "
        "CSConsole_* sheets"
    )
    assert "csconsole_data.get(_ck)" in body, (
        "Round 73 / F8: Comprehensive writer must source the per-sheet "
        "DataFrame from csconsole_data for the Sheet_Title backfill"
    )


# ---------------------------------------------------------------------------
# Runtime pin: build a synthetic Comprehensive workbook with non-empty
# CSConsole sheets and verify the produced Report_Info carries every
# expected Sheet_Title row.
# ---------------------------------------------------------------------------


def _build_minimal_sheets_payload(pd):
    """Build the smallest possible ``sheets`` + ``csconsole_data`` pair
    that exercises every R73/F8 code path."""

    sheets = {
        "AB_Detail_All": pd.DataFrame(
            {
                "BU_NAME": ["Acme", "Beta"],
                "STATUS_C": ["Open", "Closed"],
            }
        ),
        "Action_Plans": pd.DataFrame(
            {
                "Action Plan ID": ["AP-1", "AP-2"],
                "Status": ["Open", "Open"],
            }
        ),
    }
    csconsole_data = {
        "action_plans": pd.DataFrame(
            {
                "ID": ["CS-AP-1", "CS-AP-2"],
                "Status": ["Open", "Closed"],
            }
        ),
        "customer_pulse": pd.DataFrame(
            {
                "BU_NAME": ["Acme", "Beta"],
                "Note": ["healthy", "at risk"],
            }
        ),
        "success_priorities": pd.DataFrame(
            {
                "BU_NAME": ["Acme"],
                "Priority": ["adoption"],
            }
        ),
        "adoption_barriers": pd.DataFrame(
            {
                "BU_NAME": ["Acme"],
                "Barrier": ["onboarding"],
            }
        ),
    }
    return sheets, csconsole_data


def test_comprehensive_report_info_carries_summary_sheet_title(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets, csconsole_data = _build_minimal_sheets_payload(pd)
    base_path = str(tmp_path / "comp_report_info_summary")
    write_excel_workbook(
        base_path,
        sheets,
        csconsole_data=csconsole_data,
        manager="Test Manager",
        technology="Contact Center",
        days=90,
    )

    out_path = Path(f"{base_path}.xlsx")
    assert out_path.exists(), "Round 73 / F8: Comprehensive XLSX not produced"

    info_df = pd.read_excel(out_path, sheet_name="Report_Info")
    assert "Item" in info_df.columns, (
        f"Round 73 / F8: Report_Info missing Item column; saw {list(info_df.columns)!r}"
    )
    items = set(info_df["Item"].astype(str).tolist())
    assert "Sheet_Title:Summary" in items, (
        f"Round 73 / F8: Report_Info missing Sheet_Title:Summary; saw {sorted(items)!r}"
    )


def test_comprehensive_report_info_carries_csconsole_customer_pulse_title(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets, csconsole_data = _build_minimal_sheets_payload(pd)
    base_path = str(tmp_path / "comp_report_info_csc_pulse")
    write_excel_workbook(
        base_path,
        sheets,
        csconsole_data=csconsole_data,
        manager="Test Manager",
        technology="Contact Center",
        days=90,
    )

    out_path = Path(f"{base_path}.xlsx")
    info_df = pd.read_excel(out_path, sheet_name="Report_Info")
    items = set(info_df["Item"].astype(str).tolist())
    assert "Sheet_Title:CSConsole_Customer_Pulse" in items, (
        "Round 73 / F8: Report_Info missing Sheet_Title:CSConsole_Customer_Pulse; "
        f"saw {sorted(items)!r}"
    )


def test_comprehensive_report_info_carries_all_populated_csconsole_sheet_titles(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets, csconsole_data = _build_minimal_sheets_payload(pd)
    base_path = str(tmp_path / "comp_report_info_all_csc")
    write_excel_workbook(
        base_path,
        sheets,
        csconsole_data=csconsole_data,
        manager="Test Manager",
        technology="Contact Center",
        days=90,
    )

    out_path = Path(f"{base_path}.xlsx")
    info_df = pd.read_excel(out_path, sheet_name="Report_Info")
    items = set(info_df["Item"].astype(str).tolist())
    expected_csconsole = {
        "Sheet_Title:CSConsole_Action_Plans",
        "Sheet_Title:CSConsole_Customer_Pulse",
        "Sheet_Title:CSConsole_Success_Priorities",
        "Sheet_Title:CSConsole_Adoption_Barriers",
    }
    missing = expected_csconsole - items
    assert not missing, (
        f"Round 73 / F8: Report_Info missing CSConsole Sheet_Title rows: {sorted(missing)!r}; "
        f"got items {sorted(items)!r}"
    )


def test_comprehensive_report_info_skips_empty_csconsole_sheets(tmp_path):
    """A CSConsole source DataFrame that is empty (or None) must NOT
    generate a Sheet_Title row -- mirrors the writer's actual gating."""

    pd = pytest.importorskip("pandas")
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets = {
        "AB_Detail_All": pd.DataFrame({"BU_NAME": ["Acme"], "STATUS_C": ["Open"]}),
    }
    # Only customer_pulse has data; the other three are empty/None.
    csconsole_data = {
        "action_plans": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(
            {"BU_NAME": ["Acme"], "Note": ["healthy"]}
        ),
        "success_priorities": None,
        "adoption_barriers": pd.DataFrame(),
    }
    base_path = str(tmp_path / "comp_report_info_partial_csc")
    write_excel_workbook(
        base_path,
        sheets,
        csconsole_data=csconsole_data,
        manager="Test Manager",
        technology="Contact Center",
        days=90,
    )

    out_path = Path(f"{base_path}.xlsx")
    info_df = pd.read_excel(out_path, sheet_name="Report_Info")
    items = set(info_df["Item"].astype(str).tolist())
    # Only the populated CSConsole sheet should get a title row.
    assert "Sheet_Title:CSConsole_Customer_Pulse" in items
    assert "Sheet_Title:CSConsole_Action_Plans" not in items, (
        f"Round 73 / F8: empty CSConsole_Action_Plans should NOT get a Sheet_Title row; "
        f"saw {sorted(items)!r}"
    )
    assert "Sheet_Title:CSConsole_Success_Priorities" not in items
    assert "Sheet_Title:CSConsole_Adoption_Barriers" not in items


def test_comprehensive_report_info_preserves_regular_sheet_titles(tmp_path):
    """R66/B5 contract preserved: regular ``sheets`` dict entries STILL
    get a Sheet_Title row."""

    pd = pytest.importorskip("pandas")
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets, csconsole_data = _build_minimal_sheets_payload(pd)
    base_path = str(tmp_path / "comp_report_info_regular")
    write_excel_workbook(
        base_path,
        sheets,
        csconsole_data=csconsole_data,
        manager="Test Manager",
        technology="Contact Center",
        days=90,
    )

    out_path = Path(f"{base_path}.xlsx")
    info_df = pd.read_excel(out_path, sheet_name="Report_Info")
    items = set(info_df["Item"].astype(str).tolist())
    # The R66/B5 ``sheets`` iteration must still produce these.
    assert "Sheet_Title:AB_Detail_All" in items
    assert "Sheet_Title:Action_Plans" in items
    # Self-referential exclusion preserved.
    assert "Sheet_Title:Report_Info" not in items


def test_summary_sheet_actually_exists_in_workbook(tmp_path):
    """The R73/F8 backfill assumes ``Summary`` is always written by
    ``_r15_write_summary_sheet``. Pin that the Summary sheet IS in the
    workbook -- if a future refactor stops writing it, this test fails
    loud BEFORE the F8 backfill creates a misleading Sheet_Title."""

    pd = pytest.importorskip("pandas")
    pytest.importorskip("xlsxwriter")
    pytest.importorskip("openpyxl")
    import openpyxl  # noqa: PLC0415

    from adoptiq_backend import write_excel_workbook  # noqa: PLC0415

    sheets, csconsole_data = _build_minimal_sheets_payload(pd)
    base_path = str(tmp_path / "comp_summary_sheet_exists")
    write_excel_workbook(
        base_path,
        sheets,
        csconsole_data=csconsole_data,
        manager="Test Manager",
        technology="Contact Center",
        days=90,
    )

    out_path = Path(f"{base_path}.xlsx")
    wb = openpyxl.load_workbook(out_path, read_only=True)
    try:
        assert "Summary" in wb.sheetnames, (
            f"Round 73 / F8: Summary sheet missing from workbook; "
            f"saw {wb.sheetnames!r}"
        )
    finally:
        wb.close()
