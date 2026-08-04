"""Round 146 Subscription report/source-data accuracy regressions."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
from docx import Document
from openpyxl import load_workbook

import local_acceptance_lab as lab
import local_acceptance_runtime as runtime
from report_iteration_loop import (
    compare_kpi_parity,
    evaluate_report_quality,
    extract_docx_kpis,
    extract_xlsx_kpis,
)


def _document_text(path: Path) -> str:
    doc = Document(path)
    parts = [paragraph.text for paragraph in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _run_subscription(
    tmp_path: Path,
    *,
    analysis_id: str,
    support_fetch: Callable[..., pd.DataFrame] | None = None,
) -> tuple[Path, Path]:
    import app_simple

    installation = runtime.install_runtime_adapters(
        lab.build_scenario_bundle("healthy"), app_simple
    )
    original_support_fetch = app_simple.fetch_support_cases_snowflake
    if support_fetch is not None:
        app_simple.fetch_support_cases_snowflake = support_fetch

    replacements = {
        "_r81_resolve_report_output_dir": lambda *_args, **_kwargs: tmp_path,
        "save_analysis_status": lambda: None,
        "record_report_completion": lambda *_args, **_kwargs: None,
        "store_report_insights": lambda *_args, **_kwargs: None,
        "_r92_write_corpus_sidecars": lambda *_args, **_kwargs: None,
        "auto_audit_report": lambda *_args, **_kwargs: None,
    }
    originals = {name: getattr(app_simple, name) for name in replacements}
    for name, replacement in replacements.items():
        setattr(app_simple, name, replacement)

    app_simple.analysis_status[analysis_id] = {
        "subscription_id": "SUB-001",
        "days": 90,
        "report_type": "subscription",
        "start_time": "2026-08-03T21:00:00Z",
        "status": "initializing",
        "phase_timings": {},
        "completed_steps": [],
    }
    try:
        app_simple.run_subscription_analysis(analysis_id)
        status = dict(app_simple.analysis_status[analysis_id])
        assert status["status"] == "completed"
        return Path(status["word_report"]), Path(status["excel_report"])
    finally:
        app_simple.analysis_status.pop(analysis_id, None)
        app_simple.fetch_support_cases_snowflake = original_support_fetch
        installation.restore()
        for name, original in originals.items():
            setattr(app_simple, name, original)


def _assert_formula_free(path: Path) -> None:
    workbook = load_workbook(path, data_only=False, read_only=True)
    try:
        formulas = [
            (sheet.title, cell.coordinate, cell.value)
            for sheet in workbook.worksheets
            for row in sheet.iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        ]
    finally:
        workbook.close()
    assert formulas == []


def test_subscription_source_data_headers_match_values_and_deep_dive_scope(
    tmp_path: Path,
) -> None:
    word_path, source_path = _run_subscription(
        tmp_path,
        analysis_id="sub_SUB-001_1460000100",
    )

    with pd.ExcelFile(source_path) as workbook:
        assert {
            "Report_Info",
            "Summary",
            "Risk_Components",
            "Subscriptions",
            "TAC_Cases",
            "Action_Plans",
            "Adoption_Barriers",
            "Customer_Pulse",
            "Success_Priorities",
        }.issubset(workbook.sheet_names)
        action_plans = pd.read_excel(workbook, sheet_name="Action_Plans")
        tac_cases = pd.read_excel(workbook, sheet_name="TAC_Cases")
        subscriptions = pd.read_excel(workbook, sheet_name="Subscriptions")
        risk_components = pd.read_excel(workbook, sheet_name="Risk_Components")
        report_info = pd.read_excel(workbook, sheet_name="Report_Info")
        success_priorities = pd.read_excel(
            workbook, sheet_name="Success_Priorities"
        )

    # Regression for the pre-R146 shifted-row defect: the curated header must
    # describe the value beneath it, including the manager-action fields.
    assert {"ID", "Customer Name", "Status", "Due Date"}.issubset(
        action_plans.columns
    )
    ap_001 = action_plans.set_index("ID").loc["AP-001"]
    assert ap_001["Customer Name"] == "Acme Corporation"
    assert ap_001["Status"] == "Open"
    assert str(ap_001["Due Date"]).startswith("2026-08-01")

    assert len(subscriptions) == 1
    assert subscriptions.loc[0, "Subscription ID"] == "SUB-001"
    assert subscriptions.loc[0, "Account ID"] == "ACC-001"
    assert subscriptions.loc[0, "Customer Name"] == "Acme Corporation"

    assert tac_cases["SR Number"].tolist() == ["CASE-001"]
    assert tac_cases["Account ID"].tolist() == ["ACC-001"]
    assert tac_cases["Subscription Reference Id"].tolist() == ["SUB-001"]
    assert tac_cases["Priority (Normalized)"].tolist() == ["P1"]
    assert tac_cases["Case Status (Normalized)"].tolist() == ["Open"]
    assert tac_cases["Is Open"].tolist() == [True]
    support_component = risk_components.loc[
        risk_components["Component"].eq("Support Cases")
    ].iloc[0]
    assert support_component["Count"] == 1
    assert 0 <= support_component["Score (0-100)"] <= 100
    activity_component = risk_components.loc[
        risk_components["Component"].eq("Activity Volume")
    ].iloc[0]
    assert activity_component["Count"] == (
        len(action_plans) + len(tac_cases) + 1 + 1
    )

    info = dict(zip(report_info["Item"], report_info["Value"], strict=True))
    assert info["Support Case Source State"] == "Available"
    assert info["Live Validation Performed"] == "No"
    assert info["Data Mode"] == "Guarded local acceptance snapshot"
    assert all(
        not str(column).startswith(("FIXTURE_", "LOCAL_ACCEPTANCE_"))
        for column in success_priorities.columns
    )

    word_text = _document_text(word_path)
    assert len(word_text.split()) < 900
    assert "Complete source records are in the paired Source Data File" in word_text
    assert "live source validation was not performed" in word_text
    assert "CASE-001" not in word_text
    assert "1 verified support case record(s)" in word_text
    # Canonical KPI labels must expose the workbook-comparable count first;
    # the separate cited 0-100 score must not be misread as that count.
    assert (
        "Adoption Barriers: 1 [Source: Risk_Components]\n"
        "Component risk score: 77.0/100 [Source: Risk_Components]"
    ) in word_text
    parity = compare_kpi_parity(
        extract_docx_kpis(word_path),
        extract_xlsx_kpis(source_path),
        strict=True,
    )
    assert parity.passed, parity.details
    quality_payload, quality = evaluate_report_quality(
        word_path,
        source_path,
        scenario_key="g_subscription_analysis",
        strict=True,
    )
    assert quality.passed, quality_payload
    assert quality_payload["unbacked_metric_claim_count"] == 0
    assert all(
        unsafe not in (word_text + source_path.read_bytes().decode("latin1", "ignore"))
        for unsafe in ("fetch_error", "Traceback", "/Users/")
    )
    _assert_formula_free(source_path)


def test_subscription_tac_unavailable_is_public_and_not_a_false_zero(
    tmp_path: Path,
) -> None:
    def unavailable_support_cases(*_args, **_kwargs) -> pd.DataFrame:
        frame = pd.DataFrame()
        frame.attrs["fetch_error"] = (
            "ProgrammingError: secret connection detail at /Users/internal/path"
        )
        frame.attrs["fetch_error_kind"] = "access_or_schema"
        return frame

    word_path, source_path = _run_subscription(
        tmp_path,
        analysis_id="sub_SUB-001_1460000101",
        support_fetch=unavailable_support_cases,
    )
    tac_cases = pd.read_excel(source_path, sheet_name="TAC_Cases")
    report_info = pd.read_excel(source_path, sheet_name="Report_Info")
    word_text = _document_text(word_path)
    public_text = "\n".join(
        [
            word_text,
            tac_cases.to_csv(index=False),
            report_info.to_csv(index=False),
        ]
    )

    assert tac_cases.loc[0, "Source State"] == "Unavailable"
    assert tac_cases.loc[0, "Record Count"] == 0
    assert "unavailable" in tac_cases.loc[0, "Coverage Note"].casefold()
    assert "no records are presented as a verified zero" not in public_text.casefold()
    assert "Support case records were unavailable for this report run" in word_text
    for unsafe in (
        "ProgrammingError",
        "access_or_schema",
        "fetch_error",
        "/Users/",
        "Traceback",
    ):
        assert unsafe not in public_text
    _assert_formula_free(source_path)
