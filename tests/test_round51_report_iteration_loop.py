"""Round 51 regression tests for live report iteration harness."""

from __future__ import annotations

import zipfile
from argparse import Namespace
from pathlib import Path

import openpyxl

from report_iteration_loop import (
    LiveReportRunner,
    build_debug_filename,
    build_runner_config,
    build_scenario_map,
    compare_docx_against_baseline,
    compare_kpi_parity,
    compare_xlsx_against_baseline,
    extract_csrf_token,
    extract_docx_kpis,
    extract_xlsx_kpis,
    select_latest_baseline,
    validate_docx_structure,
    validate_xlsx_structure,
)


def _write_docx_like(path: Path, text: str) -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)


def _write_xlsx(path: Path, sheets: dict[str, list[str]], data_rows: int = 1) -> None:
    workbook = openpyxl.Workbook()
    first = True
    for name, headers in sheets.items():
        if first:
            sheet = workbook.active
            sheet.title = name
            first = False
        else:
            sheet = workbook.create_sheet(title=name)
        for idx, header in enumerate(headers, start=1):
            sheet.cell(row=1, column=idx, value=header)
        for row_idx in range(2, data_rows + 2):
            for col_idx, _header in enumerate(headers, start=1):
                sheet.cell(row=row_idx, column=col_idx, value=f"r{row_idx}v{col_idx}")
    workbook.save(path)
    workbook.close()


def _args(**overrides):
    defaults = {
        "base_url": "http://127.0.0.1:5151",
        "downloads_dir": "~/Downloads",
        "iterations": 1,
        "scenarios": "all",
        "poll_interval": 5.0,
        "timeout": 1800,
        "run_id": "test",
        "stop_on_failure": False,
        "baseline_mode": "latest",
        "strict": False,
        "min_docx_similarity": 0.35,
        "min_sheet_overlap": 0.5,
        "min_header_similarity": 0.3,
        "min_docx_chars": 200,
        "min_docx_numeric_similarity": 0.8,
        "max_xlsx_row_delta_ratio": 0.2,
        "max_xlsx_row_delta_abs": 25,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def test_round51_scenario_map_matches_requested_matrix():
    scenarios = build_scenario_map()
    assert set(scenarios) == {"comprehensive", "compact", "renewal", "leader"}
    assert scenarios["comprehensive"].payload["manager"] == "Brian Frazier"
    assert scenarios["comprehensive"].payload["technology"] == "All Contact Center"
    assert scenarios["compact"].payload["manager"] == "All Managers"
    assert scenarios["renewal"].payload["report_type"] == "renewal_portfolio"
    assert scenarios["leader"].endpoint == "/start_leader_report"


def test_round51_extract_csrf_and_debug_filename():
    html = '<meta name="csrf-token" content="abc123" />'
    assert extract_csrf_token(html) == "abc123"

    debug_name = build_debug_filename(
        "AdoptIQ_Report_Leader_Brian_Frazier_90d_123.docx",
        run_id="run-1",
        scenario_key="leader",
        timestamp="20260429T170000Z",
    )
    assert debug_name.startswith("AdoptIQ_Report_Leader_Brian_Frazier_90d_123__data-loop-run-1__scenario-leader")
    assert debug_name.endswith(".docx")


def test_round51_baseline_selection_uses_latest_matching_file(tmp_path: Path):
    newest = tmp_path / "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_222.docx"
    older = tmp_path / "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_111.docx"
    ignored = tmp_path / "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_333__data-loop-current.docx"
    other = tmp_path / "AdoptIQ_Report_Leader_Brian_Frazier_90d_444.docx"

    for path in (older, newest, ignored, other):
        path.write_text("x", encoding="utf-8")

    older.touch()
    newest.touch()

    selected = select_latest_baseline(
        downloads_dir=tmp_path,
        scenario_key="compact",
        extension="docx",
        run_id="current",
        current_debug_name="current.docx",
    )
    assert selected == newest


def test_round51_docx_structural_and_baseline_diff(tmp_path: Path):
    current = tmp_path / "current.docx"
    baseline = tmp_path / "baseline.docx"
    _write_docx_like(current, "AdoptIQ summary customer risk and support health overview for contact center adoption.")
    _write_docx_like(baseline, "AdoptIQ summary customer risk and support health overview for contact center adoption.")

    structural = validate_docx_structure(current, min_chars=30)
    assert structural.passed is True

    diff = compare_docx_against_baseline(current, baseline, min_similarity=0.4)
    assert diff.passed is True
    assert diff.details["similarity"] >= 0.4


def test_round51_xlsx_structural_and_baseline_diff(tmp_path: Path):
    current = tmp_path / "current.xlsx"
    baseline = tmp_path / "baseline.xlsx"

    _write_xlsx(
        current,
        {
            "Summary": ["Metric", "Value"],
            "Risk_Overview": ["Customer", "Risk Score"],
        },
    )
    _write_xlsx(
        baseline,
        {
            "Summary": ["Metric", "Value"],
            "Risk_Overview": ["Customer", "Risk Score"],
        },
    )

    structural = validate_xlsx_structure(current)
    assert structural.passed is True

    diff = compare_xlsx_against_baseline(
        current,
        baseline,
        min_sheet_overlap=0.5,
        min_header_similarity=0.3,
    )
    assert diff.passed is True
    assert diff.details["sheet_overlap"] >= 0.5


def test_round51_strict_docx_numeric_drift_fails_even_when_words_match(tmp_path: Path):
    current = tmp_path / "current.docx"
    baseline = tmp_path / "baseline.docx"
    _write_docx_like(current, "Total Customers 12 Support Cases 99 Renewal Risk Score 17.5")
    _write_docx_like(baseline, "Total Customers 52 Support Cases 176 Renewal Risk Score 14.8")

    diff = compare_docx_against_baseline(
        current,
        baseline,
        min_similarity=0.5,
        strict=True,
        min_numeric_similarity=0.8,
    )

    assert diff.passed is False
    assert diff.details["similarity"] >= 0.5
    assert diff.details["numeric_similarity"] < 0.8
    assert "12" in diff.details["numeric_only_in_current"]


def test_round51_strict_xlsx_row_count_drift_is_reported(tmp_path: Path):
    current = tmp_path / "current.xlsx"
    baseline = tmp_path / "baseline.xlsx"
    sheets = {"Summary": ["Metric", "Value"], "Report_Info": ["Field", "Value"]}
    _write_xlsx(current, sheets, data_rows=80)
    _write_xlsx(baseline, sheets, data_rows=2)

    diff = compare_xlsx_against_baseline(
        current,
        baseline,
        min_sheet_overlap=0.8,
        min_header_similarity=0.8,
        strict=True,
        max_row_delta_ratio=0.2,
        max_row_delta_abs=5,
    )

    assert diff.passed is False
    assert diff.details["row_deltas"]["summary"]["delta"] > 5


def test_round51_xlsx_structure_requires_expected_sheets(tmp_path: Path):
    current = tmp_path / "current.xlsx"
    _write_xlsx(current, {"Summary": ["Metric", "Value"]})

    structural = validate_xlsx_structure(current, expected_sheets=("summary", "report_info"))

    assert structural.passed is False
    assert structural.details["missing_expected_sheets"] == ["report_info"]


def test_round51_kpi_sidecar_extracts_and_compares_docx_xlsx_values(tmp_path: Path):
    docx_path = tmp_path / "report.docx"
    xlsx_path = tmp_path / "data.xlsx"
    _write_docx_like(docx_path, "placeholder")

    # Replace the tiny XML docx with a python-docx table so KPI extraction
    # covers the same table shape as real reports.
    from docx import Document

    # Round 52 (Phase 2): use a 3-column header table so the harness'
    # "headers + values row" heuristic applies. 2-column tables are now
    # treated as label/value because the renewal Customer Health Dashboard
    # and leader Team Performance Metrics both use that 2-column shape.
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "Total Customers"
    table.rows[0].cells[1].text = "Support Cases"
    table.rows[0].cells[2].text = "Critical (P1)"
    table.rows[1].cells[0].text = "52"
    table.rows[1].cells[1].text = "176"
    table.rows[1].cells[2].text = "5"
    doc.save(docx_path)

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    sheet.append(["Metric", "Value"])
    sheet.append(["Customers in portfolio", "52"])
    sheet.append(["Total Support Cases", "176"])
    workbook.create_sheet("Report_Info").append(["Field", "Value"])
    workbook.save(xlsx_path)
    workbook.close()

    docx_kpis = extract_docx_kpis(docx_path)
    xlsx_kpis = extract_xlsx_kpis(xlsx_path)
    parity = compare_kpi_parity(docx_kpis, xlsx_kpis, strict=True)

    assert parity.passed is True
    assert "total_customers" in parity.details["common_kpis"]
    assert "support_cases" in parity.details["common_kpis"]


def test_round51_build_runner_config_strict_raises_thresholds():
    config = build_runner_config(_args(strict=True, min_docx_similarity=0.1))

    assert config.strict is True
    assert config.min_docx_similarity >= 0.55
    assert config.min_sheet_overlap >= 0.85
    assert config.min_header_similarity >= 0.8


def test_round51_local_http_headers_forward_secure_session_cookie(tmp_path: Path):
    config = build_runner_config(_args(downloads_dir=str(tmp_path)))
    runner = LiveReportRunner(config)
    runner.csrf_token = "csrf"
    runner.session_cookie_value = "session-value"

    headers = runner._headers(include_json_content_type=True)

    assert headers["X-CSRFToken"] == "csrf"
    assert headers["Cookie"] == "session=session-value"
    assert headers["Content-Type"] == "application/json"
