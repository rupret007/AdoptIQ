"""Round 15 / Phase 0 -- Export quality smoke tests.

These tests anchor the gold-standard fixture (the customer-facing workbook
the user pointed at as "bland") and capture the bad state we're fixing in
Round 15. As Phases 1-3 land, the post-fix-invariant tests are unskipped
or moved into the dedicated Phase 1/2/3 test files
(test_round15_excel_columns.py, test_round15_excel_format.py,
test_round15_word_format.py).

The fixtures live at:
    tests/fixtures/round15/gold_data.xlsx
    tests/fixtures/round15/gold_report.docx
"""

from __future__ import annotations

import importlib
from pathlib import Path

import openpyxl
import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "round15"
GOLD_XLSX = FIXTURE_DIR / "gold_data.xlsx"
GOLD_DOCX = FIXTURE_DIR / "gold_report.docx"


def test_phase_0_gold_fixtures_present():
    """Round 15 / Phase 0: gold-standard fixtures must ship with the suite."""
    assert GOLD_XLSX.exists(), f"Missing fixture: {GOLD_XLSX}"
    assert GOLD_DOCX.exists(), f"Missing fixture: {GOLD_DOCX}"
    assert GOLD_XLSX.stat().st_size > 0
    assert GOLD_DOCX.stat().st_size > 0


def test_phase_0_gold_xlsx_baseline_shape():
    """Documents the *pre*-Round-15 sheet inventory of the gold workbook.

    This is the state we're improving away from -- the assertions below are
    intentionally describing the bad/baseline state so a future change that
    e.g. adds the Summary tab or curates the columns will deliberately update
    this test (forcing the author to acknowledge the regression direction).
    """
    wb = openpyxl.load_workbook(GOLD_XLSX, data_only=False, read_only=True)
    try:
        assert wb.sheetnames == [
            "Report_Info",
            "AB_Detail_All",
            "CSOne_Detail_All",
            "External_Bugs",
            "External_Incidents",
            "CSConsole_Customer_Pulse",
        ]
    finally:
        wb.close()


def test_phase_0_gold_xlsx_documents_known_blandness():
    """The findings driving Phases 1-3, asserted directly against the fixture.

    This test exists so anyone re-running the audit can grep
    'Round 15 / Phase 0' and immediately see which workbook flaws Round 15
    was responding to.
    """
    wb = openpyxl.load_workbook(GOLD_XLSX, data_only=False, read_only=True)
    try:
        ab = wb["AB_Detail_All"]
        assert ab.max_column >= 200, (
            "AB_Detail_All used to be ~272 cols wide -- if this drops below "
            "200, Round 15 / Phase 1 column curation has likely landed and "
            "this baseline test should be retired."
        )

        csone_headers = [c.value for c in next(wb["CSOne_Detail_All"].iter_rows(min_row=1, max_row=1))]
        assert "col_2" in csone_headers, (
            "CSOne_Detail_All baseline still has the 'col_2' header oddity. "
            "Phase 1.x should rename or drop this."
        )
        assert "Customer Name: Customer Name" in csone_headers, (
            "CSOne_Detail_All baseline still has Salesforce-relationship "
            "header label. Phase 1.x should rename to 'Customer'."
        )

        ext_inc_headers = [c.value for c in next(wb["External_Incidents"].iter_rows(min_row=1, max_row=1))]
        for leak in ("_stale_storage", "_from_storage", "_window_meta"):
            assert leak in ext_inc_headers, (
                f"External_Incidents baseline still leaks {leak!r}. "
                "Phase 1.x denylist should drop it."
            )
    finally:
        wb.close()


def test_phase_0_export_schema_module_optional():
    """SSoT module ``report_export_schema`` is created in Round 15 / Phase 1.

    Until then, this test simply documents the contract; once Phase 1 lands,
    it verifies the SSoT exposes the expected names.
    """
    try:
        mod = importlib.import_module("report_export_schema")
    except ImportError:
        pytest.skip("Round 15 / Phase 1 not yet landed -- SSoT module absent")
    assert hasattr(mod, "INTERNAL_COLUMN_DENYLIST"), (
        "report_export_schema must export INTERNAL_COLUMN_DENYLIST"
    )
    assert hasattr(mod, "CURATED_COLUMNS"), (
        "report_export_schema must export CURATED_COLUMNS mapping"
    )
    assert hasattr(mod, "is_internal_column"), (
        "report_export_schema must export is_internal_column(name) helper"
    )
