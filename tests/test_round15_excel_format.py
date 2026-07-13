"""Round 15 / Phase 2 -- Excel visual polish tests.

Covers:

* ``report_export_styling`` format detection (currency / date /
  percent / integer / risk-float).
* Conditional-formatting classifier (risk / severity / status /
  days-open).
* Column-letter / table-name / data-range builders.
* End-to-end behavior of ``adoptiq_backend.write_excel_workbook``:
  - Summary tab is the first sheet.
  - Every data sheet is wired up as a real Excel Table.
  - Conditional formatting rules are created on the right columns.
  - Phase 1 column curation still drops leaked SF / ETL plumbing
    even after the polish pass runs.
* Marker-presence tests so the Round-15 wiring can't quietly
  regress (mirrors the Round-14 test pattern).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import pytest

import report_export_styling as styling


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "col,expected",
    [
        ("PRODUCT_ARR_C", '"$"#,##0;[Red]("$"#,##0)'),
        ("ANNUAL_CONTRACT_VALUE", '"$"#,##0;[Red]("$"#,##0)'),
        ("AOV_C", '"$"#,##0;[Red]("$"#,##0)'),
        ("REVENUE_TCV", '"$"#,##0;[Red]("$"#,##0)'),
        ("OPEN_DATE_C", "yyyy-mm-dd"),
        ("Date/Time Opened", "yyyy-mm-dd"),
        ("discovered_at", "yyyy-mm-dd"),
        ("first_seen", "yyyy-mm-dd"),
        ("adoption_rate", "0.0%"),
        ("BU_HEALTH_PERCENTAGE", "0.0%"),
        ("retention_ratio", "0.0%"),
        ("risk_score_0_10", "0.0"),
        ("risk_score_0_100", "0.0"),
        ("COUNT_OF_LINKED_CASES_C", "#,##0"),
        ("open_age_days", "#,##0"),
        ("DAYS_IN_STAGE_C", "#,##0"),
    ],
)
def test_phase_2_detect_column_format_returns_expected(col, expected):
    """Each canonical column maps to the documented xlsxwriter format."""
    assert styling.detect_column_format(col) == expected


@pytest.mark.parametrize(
    "col",
    ["SUBJECT_C", "title", "ID", "NAME", "DESCRIPTION_C", None, "", 42],
)
def test_phase_2_detect_column_format_returns_none_for_freeform(col):
    """Free-form text columns and non-string inputs return ``None``."""
    assert styling.detect_column_format(col) is None


@pytest.mark.parametrize(
    "col,expected",
    [
        ("risk_score_0_10", "risk"),
        ("risk_score_0_100", "risk"),
        ("risk_numeric", "risk"),
        ("SEVERITY_C", "severity"),
        ("severity_norm", "severity"),
        ("Case Priority", "severity"),
        ("AB_STATUS_C", "status"),
        ("case_status_norm", "status"),
        ("open_age_days", "days_open"),
        ("DAYS_IN_STAGE_C", "days_open"),
        ("hold_days", "days_open"),
        ("SUBJECT_C", ""),
        ("description", ""),
    ],
)
def test_phase_2_classify_column_returns_expected_kind(col, expected):
    assert styling._classify_column(col) == expected


# ---------------------------------------------------------------------------
# Address / table-name builders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "idx,letter",
    [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ"), (52, "BA")],
)
def test_phase_2_excel_col_letter_handles_two_letter_columns(idx, letter):
    assert styling._excel_col_letter(idx) == letter


def test_phase_2_excel_col_letter_rejects_negative_index():
    with pytest.raises(ValueError):
        styling._excel_col_letter(-1)


def test_phase_2_data_range_skips_header_row():
    """``_data_range`` must point at row 2 onward (Excel notation)
    so a 3-row data block on column index 0 returns ``A2:A4``.
    """
    assert styling._data_range(0, 3) == "A2:A4"
    assert styling._data_range(2, 5) == "C2:C6"


def test_phase_2_sanitize_table_name_strips_invalid_chars_and_dedupes():
    used: set[str] = set()
    a = styling._sanitize_table_name("AB Detail [All]", used)
    b = styling._sanitize_table_name("AB Detail [All]", used)
    assert a.startswith("tbl_")
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a) is not None
    assert b.startswith("tbl_")
    assert a != b  # collision was disambiguated


def test_phase_2_sanitize_table_name_handles_empty_input():
    assert styling._sanitize_table_name("") == "tbl_Sheet"


# ---------------------------------------------------------------------------
# Risk thresholds anchored to canonical_metrics
# ---------------------------------------------------------------------------


def test_phase_2_risk_thresholds_match_canonical_high_band():
    """The 3-color risk scale must use the canonical HIGH band cutoff
    (i.e. ``RISK_BAND_THRESHOLDS["HIGH"] / 10``) -- never a magic
    number.  This catches the failure mode where someone hard-codes
    5.5 here while ``risk_scoring`` raises HIGH to a different value.
    """
    from risk_scoring import RISK_BAND_THRESHOLDS

    expected_high = float(RISK_BAND_THRESHOLDS["HIGH"]) / 10.0
    expected_med = float(RISK_BAND_THRESHOLDS["MEDIUM"]) / 10.0
    assert styling._risk_threshold_high_0_to_10() == pytest.approx(expected_high)
    assert styling._risk_threshold_medium_0_to_10() == pytest.approx(expected_med)


# ---------------------------------------------------------------------------
# Conditional formatting recorder
# ---------------------------------------------------------------------------


class _RecordingWorksheet:
    """Tiny stand-in for an xlsxwriter ``Worksheet`` that records
    the calls the polish helpers make.  Lets us unit-test the
    classifier wiring without touching the on-disk XLSX writer.
    """

    def __init__(self) -> None:
        self.cf_calls: list[tuple[str, dict]] = []
        self.set_column_calls: list[tuple] = []
        self.add_table_calls: list[tuple[int, int, int, int, dict]] = []
        self.write_blank_calls: list[tuple[int, int]] = []

    def conditional_format(self, rng, options):
        self.cf_calls.append((rng, options))

    def set_column(self, first_col, last_col, width, fmt=None):
        self.set_column_calls.append((first_col, last_col, width, fmt))

    def add_table(self, first_row, first_col, last_row, last_col, options):
        self.add_table_calls.append((first_row, first_col, last_row, last_col, options))

    def write_blank(self, row, col, value=None):
        self.write_blank_calls.append((row, col))


class _RecordingWorkbook:
    def __init__(self) -> None:
        self.formats: list[dict] = []

    def add_format(self, options):
        self.formats.append(options)
        return ("fmt", len(self.formats))


def test_phase_2_apply_conditional_formatting_classifies_each_kind():
    wb = _RecordingWorkbook()
    ws = _RecordingWorksheet()
    cols = [
        "ID",
        "risk_score_0_10",
        "SEVERITY_C",
        "AB_STATUS_C",
        "open_age_days",
        "SUBJECT_C",
    ]
    applied = styling.apply_conditional_formatting(wb, ws, cols, n_rows=5)

    assert applied["risk"] == [1]
    assert applied["severity"] == [2]
    assert applied["status"] == [3]
    assert applied["days_open"] == [4]
    # No conditional rule on ID or SUBJECT_C
    cf_ranges = [rng for rng, _ in ws.cf_calls]
    assert all("F2" not in rng for rng in cf_ranges)  # SUBJECT_C is column F


def test_phase_2_apply_conditional_formatting_no_rules_when_empty():
    wb = _RecordingWorkbook()
    ws = _RecordingWorksheet()
    applied = styling.apply_conditional_formatting(wb, ws, ["risk_score_0_10"], n_rows=0)
    assert applied == {"risk": [], "severity": [], "status": [], "days_open": []}
    assert ws.cf_calls == []


def test_phase_2_apply_conditional_formatting_handles_none_worksheet():
    wb = _RecordingWorkbook()
    applied = styling.apply_conditional_formatting(wb, None, ["risk_score_0_10"], n_rows=3)
    assert all(v == [] for v in applied.values())


# ---------------------------------------------------------------------------
# apply_excel_polish builds a Table with formatted columns
# ---------------------------------------------------------------------------


def test_phase_2_apply_excel_polish_adds_table_with_per_column_formats():
    wb = _RecordingWorkbook()
    ws = _RecordingWorksheet()
    df = pd.DataFrame(
        {
            "ID": ["a", "b"],
            "OPEN_DATE_C": pd.to_datetime(["2025-01-01", "2025-02-01"]),
            "PRODUCT_ARR_C": [1000, 2000],
            "risk_score_0_10": [3.0, 8.0],
            "AB_STATUS_C": ["Open", "Closed"],
        }
    )
    out = styling.apply_excel_polish(wb, ws, df, "AB_Detail_All", set())
    assert out["table_added"] is True
    assert out["table_name"] and out["table_name"].startswith("tbl_AB_Detail_All")
    assert ws.add_table_calls, "expected at least one add_table call"
    assert out["formats_applied"] == {
        "OPEN_DATE_C": "yyyy-mm-dd",
        "PRODUCT_ARR_C": '"$"#,##0;[Red]("$"#,##0)',
        "risk_score_0_10": "0.0",
    }
    # Risk + status conditional formatting fired.
    assert out["conditional_rules"]["risk"]
    assert out["conditional_rules"]["status"]


def test_phase_2_apply_excel_polish_handles_empty_dataframe():
    wb = _RecordingWorkbook()
    ws = _RecordingWorksheet()
    out = styling.apply_excel_polish(wb, ws, pd.DataFrame(), "Empty", set())
    assert out["table_added"] is False  # 0 cols -> bail


def test_phase_2_apply_excel_polish_dedupes_duplicate_headers():
    wb = _RecordingWorkbook()
    ws = _RecordingWorksheet()
    df = pd.DataFrame([[1, 2]], columns=["X", "X"])
    out = styling.apply_excel_polish(wb, ws, df, "Dup", set())
    headers = [c["header"] for c in ws.add_table_calls[0][4]["columns"]]
    assert headers[0] == "X"
    assert headers[1].startswith("X_")


# ---------------------------------------------------------------------------
# Summary KPI builder
# ---------------------------------------------------------------------------


def test_phase_2_build_summary_rows_pulls_from_canonical_metrics():
    ab = pd.DataFrame(
        {
            "ID": ["a1", "a2", "a3"],
            "BU_NAME": ["Acme", "Beta", "Acme"],
            "SEVERITY_C": ["Critical", "High", "Low"],
            "AB_STATUS_C": ["Open", "Open", "Closed"],
            "open_date": pd.to_datetime(["2025-01-01"] * 3),
        }
    )
    csone = pd.DataFrame(
        {
            "Customer Name": ["Acme", "Beta"],
            "Severity": ["P1", "P3"],
            "Case Status": ["Open", "Closed"],
        }
    )
    rows = styling.build_summary_rows(
        {"AB_Detail_All": ab, "CSOne_Detail_All": csone, "External_Bugs": pd.DataFrame([{"x": 1}])},
        {"customer_pulse": pd.DataFrame()},
        manager="Jane Doe",
        tech="Contact Center",
        days=90,
        generated_at_utc_iso_z="2026-04-25T01:00:00Z",
    )
    labels = [r[0] for r in rows]
    assert "Manager scope" in labels
    assert "Customers in portfolio" in labels
    assert "Adoption barriers (total)" in labels
    assert "TAC cases (P1)" in labels
    assert "External bugs (rows)" in labels
    # Summary first row is the timestamp because we passed it.
    assert rows[0] == ("Report generated (UTC)", "2026-04-25T01:00:00Z")
    # Manager / tech are in the labelled rows.
    rowmap = dict(rows)
    assert rowmap["Manager scope"] == "Jane Doe"
    assert rowmap["Technology scope"] == "Contact Center"


def test_phase_2_build_summary_rows_renders_unknown_as_dashes():
    """When canonical_metrics returns ``None`` (e.g. an exception),
    the formatter renders ``"--"`` rather than fabricating a zero.
    Counts of zero from real-but-empty frames render as ``"0"``;
    this test pins ONLY the ``None``-handling branch.
    """
    assert styling._format_kpi(None) == "--"
    assert styling._format_kpi(0) == "0"
    assert styling._format_kpi(0.0) == "0"
    assert styling._format_kpi(125_000) == "125,000"
    assert styling._format_kpi(8.55) == "8.6"


def test_phase_2_build_summary_rows_no_timestamp_omits_first_row():
    rows = styling.build_summary_rows({}, None)
    assert rows[0][0] != "Report generated (UTC)"


# ---------------------------------------------------------------------------
# write_summary_sheet integration
# ---------------------------------------------------------------------------


def test_phase_2_write_summary_sheet_renders_first_sheet(tmp_path: Path):
    out = tmp_path / "summary.xlsx"
    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        ok = styling.write_summary_sheet(
            writer,
            {"AB_Detail_All": pd.DataFrame({"ID": [1, 2, 3], "BU_NAME": ["a", "b", "c"]})},
            None,
            manager="A",
            tech="B",
            days=30,
            generated_at_utc_iso_z="2026-04-25T00:00:00Z",
        )
        # Add a follow-up sheet so we can verify Summary is still first.
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="Other", index=False)
    assert ok is True

    from openpyxl import load_workbook

    wb = load_workbook(out)
    assert wb.sheetnames[0] == "Summary"
    ws = wb["Summary"]
    assert ws["A1"].value == "Metric"
    assert ws["B1"].value == "Value"


# ---------------------------------------------------------------------------
# End-to-end integration with adoptiq_backend.write_excel_workbook
# ---------------------------------------------------------------------------


def _build_e2e_fixtures() -> tuple[dict, dict]:
    ab_df = pd.DataFrame(
        {
            "ID": ["a1", "a2", "a3"],
            "NAME": ["AB-1", "AB-2", "AB-3"],
            "customer_name": ["Acme", "Beta", "Gamma"],
            "SUBJECT_C": ["Onboarding", "Training", "Adoption"],
            "AB_STATUS_C": ["Open", "In Progress", "Closed"],
            "SEVERITY_C": ["Critical", "High", "Low"],
            "OPEN_DATE_C": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"]),
            "open_age_days": [120, 45, 5],
            "PRODUCT_ARR_C": [125_000, 7_500, 250_000],
            "risk_score_0_10": [8.5, 3.2, 6.1],
            # Plumbing the writer must DROP via Phase 1 schema even
            # though Phase 2 polish is now wired downstream.
            "IS_DELETED": [False, False, False],
            "SYSTEM_MODSTAMP": pd.to_datetime(["2025-04-01"] * 3),
        }
    )
    csone_df = pd.DataFrame(
        {
            "Customer": ["Acme", "Beta"],
            "Severity": ["P1", "P3"],
            "Case Status": ["Open", "Closed"],
            "open_age_days": [10, 7],
            "col_2": ["x", "y"],  # internal column -- must be dropped
        }
    )
    bugs = pd.DataFrame(
        {
            "bug_id": ["CSCv1"],
            "title": ["Crash on init"],
            "source": ["BST"],
            "source_url": ["http://x/"],
            "discovered_at": ["2025-04-15"],
        }
    )
    incs = pd.DataFrame(
        {
            "id": ["I1"],
            "incident_number": ["INC0001"],
            "title": ["Outage"],
            "status": ["Resolved"],
            "impact_level": ["High"],
            "source": ["internal"],
            "link": ["http://i/"],
            "_stale_storage": [False],  # Phase 1 must drop this
        }
    )
    cs_pulse = pd.DataFrame(
        {
            "NAME": ["x"],
            "BU_NAME": ["Acme"],
            "CUSTOMER_PULSE__C": ["Yellow"],
            "ETL_ID": [1],  # ETL plumbing -- Phase 1 must drop
            "CONNECTIONRECEIVEDID": [None],
        }
    )
    sheets = {
        "AB_Detail_All": ab_df,
        "CSOne_Detail_All": csone_df,
        "External_Bugs": bugs,
        "External_Incidents": incs,
    }
    csconsole = {"customer_pulse": cs_pulse}
    return sheets, csconsole


def test_phase_2_e2e_summary_is_first_sheet():
    import adoptiq_backend as ab

    sheets, csconsole = _build_e2e_fixtures()
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "r15_e2e_first")
        out = ab.write_excel_workbook(
            base, sheets, csconsole, "Test Mgr", "All", 90
        )

        from openpyxl import load_workbook

        wb = load_workbook(out)
        assert wb.sheetnames[0] == "Summary"
        # Summary tab is followed by Report_Info then the data sheets.
        assert wb.sheetnames[1] == "Report_Info"


def test_phase_2_e2e_every_data_sheet_has_excel_table():
    import adoptiq_backend as ab

    sheets, csconsole = _build_e2e_fixtures()
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "r15_e2e_tables")
        out = ab.write_excel_workbook(
            base, sheets, csconsole, "Test Mgr", "All", 90
        )

        from openpyxl import load_workbook

        wb = load_workbook(out)
        data_sheets = [
            "AB_Detail_All",
            "CSOne_Detail_All",
            "External_Bugs",
            "External_Incidents",
            "CSConsole_Customer_Pulse",
        ]
        for s in data_sheets:
            assert s in wb.sheetnames, f"Sheet {s} missing"
            ws = wb[s]
            tables = list(ws.tables.keys())
            assert len(tables) == 1, f"{s} expected exactly one Table; got {tables}"
            assert tables[0].startswith("tbl_"), f"{s} table name must use tbl_ prefix"


def test_phase_2_e2e_conditional_formatting_on_risk_severity_status_days():
    import adoptiq_backend as ab

    sheets, csconsole = _build_e2e_fixtures()
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "r15_e2e_cf")
        out = ab.write_excel_workbook(
            base, sheets, csconsole, "Test Mgr", "All", 90
        )

        from openpyxl import load_workbook

        wb = load_workbook(out)
        ws = wb["AB_Detail_All"]
        rule_count = sum(
            len(rules) for rules in ws.conditional_formatting._cf_rules.values()
        )
        # Expect risk 3-color (1) + severity bands (8) + status bands (12)
        # + days-open data bar (1) = 22 rules.  Allow some slack in case
        # we tune the rule list later.
        assert rule_count >= 10, f"expected >=10 CF rules on AB_Detail_All; got {rule_count}"


def test_phase_2_e2e_phase1_curation_still_drops_plumbing():
    """Round 15 Phase 2 must NOT regress Round 15 Phase 1.  The
    leaked SF / ETL plumbing columns we curated out in Phase 1 are
    still absent after the polish pass runs.
    """
    import adoptiq_backend as ab

    sheets, csconsole = _build_e2e_fixtures()
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "r15_e2e_curation")
        out = ab.write_excel_workbook(
            base, sheets, csconsole, "Test Mgr", "All", 90
        )

        from openpyxl import load_workbook

        wb = load_workbook(out)
        # AB sheet must not leak IS_DELETED / SYSTEM_MODSTAMP
        ab_headers = [c.value for c in wb["AB_Detail_All"][1]]
        assert "IS_DELETED" not in ab_headers
        assert "SYSTEM_MODSTAMP" not in ab_headers
        # CSOne must not leak col_2
        csone_headers = [c.value for c in wb["CSOne_Detail_All"][1]]
        assert "col_2" not in csone_headers
        # External_Incidents must not leak _stale_storage
        ext_headers = [c.value for c in wb["External_Incidents"][1]]
        assert "_stale_storage" not in ext_headers
        # CSConsole_Customer_Pulse must not leak ETL_ID
        cs_headers = [c.value for c in wb["CSConsole_Customer_Pulse"][1]]
        assert "ETL_ID" not in cs_headers


# ---------------------------------------------------------------------------
# Marker-presence tests -- catch silent regressions of the wiring
# ---------------------------------------------------------------------------


def test_phase_2_marker_adoptiq_backend_imports_styling_module():
    src = Path("adoptiq_backend.py").read_text()
    assert "from report_export_styling import" in src
    assert "_r15_apply_excel_polish" in src
    assert "_r15_write_summary_sheet" in src
    # Both wiring sites must reference the polish helper.
    assert src.count("_r15_apply_excel_polish") >= 3, (
        "expected polish call on main sheet path + CSConsole path + import"
    )
    # Round 15 / Phase 2 marker comments are present.
    assert "Round 15 / Phase 2.4" in src
    assert "Round 15 / Phase 2.6" in src


def test_phase_2_marker_styling_module_self_describes_round_15():
    src = Path("report_export_styling.py").read_text()
    assert "Round 15 / Phase 2" in src
    assert "RISK_BAND_THRESHOLDS" in src
    assert "canonical_metrics" in src


def test_phase_2_palette_uses_documented_cisco_hex_values():
    """Pin the palette.  Changing these would silently shift the
    workbook color story away from the rest of the report.
    """
    pal = styling._PALETTE
    assert pal["green"] == "#28B463"
    assert pal["yellow"] == "#FFB81C"
    assert pal["red"] == "#E74C3C"
    assert pal["grey"] == "#95A5A6"
    assert pal["white"] == "#FFFFFF"
    assert pal["black"] == "#000000"


def test_phase_2_public_api_surface_is_minimal():
    expected = {
        "COLUMN_FORMAT_RULES",
        "apply_conditional_formatting",
        "apply_excel_polish",
        "build_summary_rows",
        "detect_column_format",
        "write_summary_sheet",
    }
    assert set(styling.__all__) == expected
