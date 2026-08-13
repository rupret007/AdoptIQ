"""Round 53 regression tests for report accuracy findings."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import openpyxl
import pandas as pd
from docx import Document

import canonical_metrics as cm
from report_iteration_loop import (
    compare_kpi_parity,
    evaluate_report_quality,
    extract_and_write_quality,
    extract_docx_kpis,
    extract_xlsx_kpis,
)


def test_round53_adoption_barrier_open_and_critical_counts_dedupe_ids():
    """Round 53: open / critical AB KPIs count records, not fan-out rows."""

    barriers = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-1", "ab-2", "ab-3", "ab-3", "ab-4"],
            "Status": ["Open", "Open", "Open", "Resolved", "Resolved", "Cancelled"],
            "Severity": ["High", "High", "Medium", "High", "High", "High"],
        }
    )

    assert cm.count_total_barriers(barriers) == 4
    assert cm.count_open_barriers(barriers) == 2
    assert cm.count_critical_barriers(barriers) == 3


def test_round53_harness_recomputes_ab_count_from_detail_sheet(tmp_path: Path):
    """Round 53: strict harness catches DOCX row-count claims against XLSX IDs."""

    docx_path = tmp_path / "compact.docx"
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Adoption Barriers"
    table.rows[0].cells[1].text = "4"
    doc.save(docx_path)

    xlsx_path = tmp_path / "compact.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "All_Adoption_Barriers"
    sheet.append(["ID", "Customer Name", "Status", "Severity"])
    sheet.append(["ab-1", "Acme", "Open", "High"])
    sheet.append(["ab-1", "Acme", "Open", "High"])
    sheet.append(["ab-2", "Beta", "Open", "Medium"])
    sheet.append(["ab-3", "Gamma", "Resolved", "High"])
    workbook.save(xlsx_path)
    workbook.close()

    parity = compare_kpi_parity(
        extract_docx_kpis(docx_path),
        extract_xlsx_kpis(xlsx_path),
        strict=True,
    )

    assert parity.passed is False
    assert parity.details["mismatches"]["adoption_barriers"] == {"docx": "4", "xlsx": "3"}


def test_round53_harness_splits_leader_team_members_from_customers(tmp_path: Path):
    """Round 53: direct-report count and customer count are separate KPIs."""

    docx_path = tmp_path / "leader.docx"
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Team Members"
    table.rows[0].cells[1].text = "2"
    table.rows[1].cells[0].text = "Customers"
    table.rows[1].cells[1].text = "9"
    doc.save(docx_path)

    xlsx_path = tmp_path / "leader.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Team_Summary"
    sheet.append(["Team_Member", "Num_Customers", "Num_TAC_Cases"])
    sheet.append(["Member A", 5, 6])
    sheet.append(["Member B", 4, 3])
    workbook.save(xlsx_path)
    workbook.close()

    docx_values = extract_docx_kpis(docx_path)["values"]
    xlsx_values = extract_xlsx_kpis(xlsx_path)["values"]

    assert docx_values["team_members"] == "2"
    assert xlsx_values["team_members"] == "2"
    assert docx_values["total_customers"] == "9"
    assert xlsx_values["total_customers"] == "9"


def test_round53_renewal_summary_title_row_not_counted_as_customer(tmp_path: Path):
    """Round 53: Renewal_Summary title rows do not inflate customer count."""

    xlsx_path = tmp_path / "renewal.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Renewal_Summary"
    sheet.append(["Renewal Summary - All Managers Renewal Analysis"])
    sheet.append(["Customer", "Overall_Risk_Score", "Risk_Level"])
    sheet.append(["Acme", 10, "HEALTHY"])
    sheet.append(["Beta", 20, "LOW"])
    sheet.append(["Gamma", 30, "LOW"])
    workbook.save(xlsx_path)
    workbook.close()

    assert extract_xlsx_kpis(xlsx_path)["values"]["total_customers"] == "3"


def test_round53_action_plan_detail_sheet_overwrites_stale_summary(tmp_path: Path):
    """Round 53.2: detail sheets are the source of truth over summary tiles."""

    xlsx_path = tmp_path / "compact.xlsx"
    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["Metric", "Value"])
    summary.append(["Action Plans", "100"])
    detail = workbook.create_sheet("Customer_Action_Plans")
    detail.append(["ID", "Customer", "Status"])
    detail.append(["ap-1", "Acme", "Open"])
    detail.append(["ap-2", "Beta", "Open"])
    workbook.save(xlsx_path)
    workbook.close()

    assert extract_xlsx_kpis(xlsx_path)["values"]["action_plans"] == "2"


def test_round53_strict_parity_fails_when_no_common_kpis():
    """Round 53.2: strict mode fails closed when formats have no overlap."""

    parity = compare_kpi_parity(
        {"values": {"manager": "All Managers"}},
        {"values": {"window_days": "90"}},
        strict=True,
        required_keys=("manager", "window_days"),
    )

    assert parity.passed is False
    assert parity.details["reason"] == "no_common_kpis"


def test_round533_non_strict_parity_still_fails_on_real_mismatch():
    """Round 53.3: non-strict callers must still see real cross-format drift.

    Pre-Round-53.3 the harness collapsed parity to True whenever
    ``strict`` was False, which meant a DOCX that claimed
    ``Adoption Barriers: 4`` while the XLSX detail sheet recorded
    ``3`` would silently pass. The supervisor passes ``--strict``
    today, but any ad-hoc caller using the harness without strict
    would have falsely declared success on real numeric drift.
    """

    parity = compare_kpi_parity(
        {"values": {"adoption_barriers": "4", "window_days": "90"}},
        {"values": {"adoption_barriers": "3", "window_days": "90"}},
        strict=False,
    )

    assert parity.passed is False
    assert parity.details["reason"] == "compared_common_kpis"
    assert parity.details["mismatches"]["adoption_barriers"] == {
        "docx": "4",
        "xlsx": "3",
    }


def test_round53_quality_gate_fails_strict_unbacked_metric_claim(tmp_path: Path):
    """Round 53: strict quality review enforces source backing next to metrics."""

    docx_path = tmp_path / "report.docx"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Support Cases"
    table.rows[0].cells[1].text = "12"
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="compact",
        strict=True,
    )

    assert gate.passed is False
    assert payload["unbacked_metric_claim_count"] == 1
    assert any("lack adjacent [Source:]" in error for error in gate.details["errors"])


def test_round53_quality_gate_accepts_adjacent_source_column(tmp_path: Path):
    """Round 53: a metric row with an adjacent source cell is source-backed."""

    docx_path = tmp_path / "report.docx"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_heading("Metric Source Backing", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "Support Cases"
    table.rows[0].cells[1].text = "12"
    table.rows[0].cells[2].text = "[Source: CSOne; Field(s): SR Number]"
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="compact",
        strict=True,
    )

    assert gate.passed is True, gate.details
    assert payload["metric_claim_count"] == 1
    assert payload["unbacked_metric_claim_count"] == 0
    assert payload["source_citation_count"] == 1


def test_quality_gate_rejects_missing_charts_for_complete_source_scenario(
    tmp_path: Path,
) -> None:
    """Complete fixture anchors cannot pass with a silently empty chart layer."""

    docx_path = tmp_path / "complete-without-charts.docx"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_heading("Decision Detail", level=2)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Decision"
    table.rows[0].cells[1].text = "Review"
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="renewal",
        strict=True,
        expected_min_charts=4,
    )

    assert gate.passed is False
    assert payload["chart_count"] == 0
    assert payload["expected_min_charts"] == 4
    assert any("requires at least 4" in error for error in payload["errors"])


def test_round53_quality_gate_rejects_paragraph_source_laundering(tmp_path: Path):
    """Round 53.2: one paragraph-level source cannot back every prior number."""

    docx_path = tmp_path / "report.docx"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph("Support Cases: 12; Adoption Barriers: 5 [Source: CSOne]")
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="compact",
        strict=True,
    )

    assert gate.passed is False
    assert payload["unbacked_metric_claim_count"] == 1
    assert payload["unbacked_metric_claims"][0]["canonical"] == "support_cases"


def test_round53_quality_gate_flags_uncited_narrative_numbers(tmp_path: Path):
    """Round 53.2: numeric narrative facts outside KPI aliases still need sources."""

    docx_path = tmp_path / "report.docx"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph("There are 12 severe issues requiring immediate review.")
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="compact",
        strict=True,
    )

    assert gate.passed is False
    assert payload["uncited_numeric_paragraph_count"] == 1


def test_round53_quality_gate_team_total_claims_match_kpi_row_selection(tmp_path: Path):
    """Round 53.2: strict source checks inspect the same Team Total row as KPI extraction."""

    docx_path = tmp_path / "leader.docx"
    doc = Document()
    doc.add_heading("Team Summary", level=1)
    table = doc.add_table(rows=3, cols=3)
    table.rows[0].cells[0].text = "Team Member"
    table.rows[0].cells[1].text = "Action Plans"
    table.rows[0].cells[2].text = "TAC Cases"
    table.rows[1].cells[0].text = "Member A"
    table.rows[1].cells[1].text = "1"
    table.rows[1].cells[2].text = "2"
    table.rows[2].cells[0].text = "Team Total"
    table.rows[2].cells[1].text = "3"
    table.rows[2].cells[2].text = "4"
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="leader",
        strict=True,
    )

    assert gate.passed is False
    assert payload["metric_claim_count"] == 2
    assert {claim["canonical"] for claim in payload["unbacked_metric_claims"]} == {
        "action_plans",
        "support_cases",
    }


def test_round53_quality_gate_surfaces_chart_opportunity(tmp_path: Path):
    """Round 53: quality review can recommend visual improvements."""

    docx_path = tmp_path / "report.docx"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_heading("Metric Source Backing", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "Total Customers"
    table.rows[0].cells[1].text = "52"
    table.rows[0].cells[2].text = "[Source: Snowflake; Field(s): Customer]"
    doc.save(docx_path)

    payload, gate = evaluate_report_quality(
        docx_path,
        None,
        scenario_key="comprehensive",
        strict=True,
    )

    assert gate.passed is True, gate.details
    assert any(item["kind"] == "chart_opportunity" for item in payload["recommendations"])


def test_round53_quality_sidecar_is_written(tmp_path: Path):
    """Round 53: live harness writes machine-readable quality sidecars."""

    docx_path = tmp_path / "report.docx"
    sidecar_path = tmp_path / "report.quality.json"
    doc = Document()
    doc.add_heading("Executive Summary", level=1)
    doc.add_heading("Metric Source Backing", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text = "Support Cases"
    table.rows[0].cells[1].text = "12"
    table.rows[0].cells[2].text = "[Source: CSOne]"
    doc.save(docx_path)

    payload, gate = extract_and_write_quality(
        docx_path,
        None,
        sidecar_path,
        scenario_key="compact",
        strict=True,
    )

    assert gate.passed is True
    assert sidecar_path.exists()
    assert payload["quality"]["passed"] is True


def test_round531_closed_barrier_count_dedupes_ids():
    """Round 53.1: closed AB counts use the same record unit as open/critical."""

    barriers = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-1", "ab-2", "ab-3"],
            "Status": ["Resolved", "Resolved", "Open", "Closed"],
            "Severity": ["High", "High", "Medium", "Low"],
        }
    )

    assert cm.count_closed_barriers(barriers) == 2


def test_round531_total_activities_uses_distinct_barrier_records():
    """Round 53.1: activity totals share the report's AB record unit."""

    barriers = pd.DataFrame({"ID": ["ab-1", "ab-1", "ab-2"]})
    action_plans = pd.DataFrame({"ID": ["ap-1"]})

    assert cm.count_total_activities(
        action_plans_df=action_plans,
        ab_df=barriers,
        mode=cm.ACTIVITIES_MODE_MEMBER_TABLE,
    ) == 3


def test_round531_risk_scoring_dedupes_ab_fanout_in_details():
    """Round 53.1: risk factors and findings cite distinct barrier records."""

    from risk_scoring import compute_customer_risk_profile

    barriers = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-1", "ab-2"],
            "customer_name": ["Acme", "Acme", "Acme"],
            "AB_STATUS_C": ["Open", "Open", "Resolved"],
            "SEVERITY_C": ["High", "High", "Medium"],
            "OPEN_DATE_C": ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z"],
        }
    )

    profile = compute_customer_risk_profile(
        customer_name="Acme",
        customer_ab=barriers,
        customer_csone=pd.DataFrame(),
        customer_pulse=pd.DataFrame(),
        customer_action_plans=pd.DataFrame(),
        customer_subs=pd.DataFrame(),
        ext_incidents=None,
    )
    details = profile["components"]["adoption_barriers"]["details"]

    assert details["count"] == 2
    assert details["open_count"] == 1
    assert details["critical_high_count"] == 1
    assert any("1 critical/high adoption barriers" in item for item in profile["risk_factors"])
    assert any("Adoption barriers analyzed: 2" in item for item in profile["key_findings"])


def test_round531_simple_renewal_risk_reports_distinct_ab_count():
    """Round 53.1: renewal analysis count is the source-cited barrier ledger."""

    from app_simple import _calculate_simple_renewal_risk

    barriers = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-1", "ab-2"],
            "customer_name": ["Acme", "Acme", "Acme"],
            "AB_STATUS_C": ["Open", "Open", "Resolved"],
            "SEVERITY_C": ["High", "High", "Medium"],
        }
    )

    analysis = _calculate_simple_renewal_risk(
        "Acme",
        barriers,
        pd.DataFrame(),
        pd.DataFrame(),
        days=90,
    )

    assert analysis["adoption_barriers_count"] == 2


def test_round531_compact_voice_section_uses_distinct_barrier_counts():
    """Round 53.1: compact VoC totals and averages are record-based."""

    from compact_report_formatter import CompactReportFormatter

    formatter = CompactReportFormatter()
    barriers = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-1", "ab-2"],
            "customer_name": ["Acme", "Acme", "Acme"],
            "AB_STATUS_C": ["Open", "Open", "Resolved"],
            "SEVERITY_C": ["High", "High", "Medium"],
            "SUBJECT_C": ["Issue A", "Issue A", "Issue B"],
        }
    )

    formatter.add_adoption_barriers_voice_section(barriers)
    text = "\n".join(paragraph.text for paragraph in formatter.doc.paragraphs)

    assert_in_source(text, "Total Adoption Barriers: 2", label='text')
    assert_in_source(text, "Average Barriers per Customer: 2.0", label='text')
    assert text.count("Issue A") == 1


def test_round531_leader_summary_uses_distinct_barrier_records():
    """Round 53.1: leader opening summary does not use fan-out row count."""

    from unittest import mock

    from leader_report_generator import LeaderReportGenerator

    generator = LeaderReportGenerator(mock.MagicMock(), [("manager@example.com", "CSSM", "Manager")])
    generator._derive_sentiment_summary = mock.MagicMock(return_value="Neutral")
    data = {
        "customers": ["Acme"],
        "subscriptions": pd.DataFrame(),
        "adoption_barriers": pd.DataFrame(
            {
                "ID": ["ab-1", "ab-1", "ab-2"],
                "BU_NAME": ["Acme", "Acme", "Acme"],
                "AB_STATUS_C": ["Open", "Open", "Resolved"],
                "SEVERITY_C": ["High", "High", "Medium"],
            }
        ),
        "action_plans": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "tac_cases": pd.DataFrame(),
    }

    generator._add_individual_summary_paragraph("CSSM", data, days=90)
    text = "\n".join(paragraph.text for paragraph in generator.doc.paragraphs)

    assert_in_source(text, "Portfolio shows 2 adoption barriers", label='text')


def test_round531_data_source_summary_details_use_barrier_records():
    """Round 53.1: validation source summary explains record counts, not rows."""

    from data_source_validator import get_data_source_summary

    barriers = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-1", "ab-2"],
            "Status": ["Open", "Open", "Resolved"],
        }
    )

    summary = get_data_source_summary(
        snowflake_ctx=None,
        team_subs_df=pd.DataFrame(),
        ab_data=barriers,
        csone_data=pd.DataFrame(),
    )

    assert summary["adoption_barriers"]["row_count"] == 3
    assert summary["adoption_barriers"]["details"] == "2 adoption barrier records found"


# ---------------------------------------------------------------------------
# Round 53.2 -- regenerated report verification follow-ups
# ---------------------------------------------------------------------------


def test_round532_leader_team_total_uses_distinct_ab_records_not_sum_of_per_cssm():
    """Round 53.2: cross-CSSM AB fan-out must not inflate the team TOTAL row.

    Source-of-truth is the deduped union of every per-CSSM slice. The
    leader DOCX previously emitted ``sum(per-CSSM distinct counts)``,
    which double-counted barriers shared by customers visible to two
    or more CSSMs. The fix recomputes ``total_abs`` from the union
    via ``cm.count_total_barriers`` so the TOTAL row and the
    "Total Adoption Barriers" Key Insights bullet match the
    workbook's Adoption_Barriers sheet (distinct-by-ID).
    """

    from docx import Document

    from leader_report_generator import LeaderReportGenerator

    cssm_a_abs = pd.DataFrame(
        {
            "ID": ["ab-1", "ab-2", "ab-3", "ab-4"],
            "BU_NAME": ["Acme", "Acme", "Beta", "Beta"],
            "STATUS_C": ["Open", "Open", "Open", "Resolved"],
            "SEVERITY_C": ["High", "Medium", "High", "Medium"],
        }
    )
    cssm_b_abs = pd.DataFrame(
        {
            "ID": ["ab-3", "ab-4", "ab-5"],
            "BU_NAME": ["Beta", "Beta", "Gamma"],
            "STATUS_C": ["Open", "Resolved", "Open"],
            "SEVERITY_C": ["High", "Medium", "High"],
        }
    )

    team_data = {
        "CSSM-A": {
            "customers": ["Acme", "Beta"],
            "adoption_barriers": cssm_a_abs,
            "action_plans": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
            "support_cases": pd.DataFrame(),
        },
        "CSSM-B": {
            "customers": ["Beta", "Gamma"],
            "adoption_barriers": cssm_b_abs,
            "action_plans": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
            "support_cases": pd.DataFrame(),
        },
    }

    per_cssm_sum = cm.count_total_barriers(cssm_a_abs) + cm.count_total_barriers(cssm_b_abs)
    distinct_total = cm.count_total_barriers(
        pd.concat([cssm_a_abs, cssm_b_abs], ignore_index=True, sort=False)
    )
    assert per_cssm_sum == 7, "fixture should expose cross-CSSM duplication"
    assert distinct_total == 5, "distinct-by-ID across the union must be 5"
    assert per_cssm_sum != distinct_total, "fixture must trigger the regression"

    gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
    gen.doc = Document()
    gen.arr_sentiment_analyzer = None
    gen.cancellation_check = lambda: False

    gen._create_summary_table(team_data, days=90)

    rendered_text = "\n".join(p.text for p in gen.doc.paragraphs)
    rendered_text += "\n" + "\n".join(
        " | ".join(c.text.strip() for c in row.cells)
        for tbl in gen.doc.tables
        for row in tbl.rows
    )

    assert f"Total Adoption Barriers: {distinct_total}" in rendered_text
    assert f"Total Adoption Barriers: {per_cssm_sum}" not in rendered_text

    last_table = gen.doc.tables[-1]
    totals_row = last_table.rows[-1]
    totals_cells = [c.text.strip() for c in totals_row.cells]
    assert totals_cells[0] == "TOTAL"
    assert totals_cells[2] == str(distinct_total), (
        f"team activity TOTAL row AB cell should be the distinct count "
        f"({distinct_total}), got {totals_cells[2]}"
    )


def test_round532_renewal_three_plus_open_label_filters_to_open_status():
    """Round 53.2: '3+ open adoption barriers' must count Open-status only.

    Pre-fix, the renewal portfolio Key Findings prose said
    "X customer(s) have 3+ open adoption barriers" but X was the
    distinct-by-ID count across ALL statuses (Open, Resolved,
    Cancelled). For a portfolio where 20 customers have 3+ ABs of any
    status but only 16 have 3+ OPEN ABs, the prose claim and the
    workbook-backed truth disagreed by 4. The
    ``_r532_count_customers_with_min_open_barriers`` helper now pins
    the renewal renderer to the canonical Open-status filter; the
    inline renderer block delegates to it so the label and the
    number share a single source of truth.
    """

    from app_simple import _r532_count_customers_with_min_open_barriers

    customer_ab = pd.DataFrame(
        {
            "ID": [f"ab-{i}" for i in range(1, 11)],
            "customer_name": [
                "Acme",
                "Acme",
                "Acme",
                "Beta",
                "Beta",
                "Beta",
                "Gamma",
                "Gamma",
                "Gamma",
                "Delta",
            ],
            "STATUS_C": [
                "Open",
                "Open",
                "Resolved",
                "Open",
                "Open",
                "Open",
                "Resolved",
                "Resolved",
                "Resolved",
                "Open",
            ],
        }
    )

    # Sanity: 4 customers have >=3 distinct ABs across all statuses,
    # but only Beta has >=3 distinct OPEN ABs.
    by_cust_all = customer_ab.drop_duplicates(subset=["ID"]).copy()
    assert (by_cust_all["customer_name"].value_counts() >= 3).sum() == 3

    open_threshold = _r532_count_customers_with_min_open_barriers(
        customer_ab, "customer_name", threshold=3
    )
    assert open_threshold == 1

    # Fan-out rows must not inflate the count.
    fanned = pd.concat([customer_ab, customer_ab], ignore_index=True)
    assert (
        _r532_count_customers_with_min_open_barriers(fanned, "customer_name", threshold=3)
        == 1
    )

    # Empty / missing-column inputs are safe.
    assert _r532_count_customers_with_min_open_barriers(
        pd.DataFrame(), "customer_name", threshold=3
    ) == 0
    assert _r532_count_customers_with_min_open_barriers(
        pd.DataFrame({"foo": [1, 2]}), "customer_name", threshold=3
    ) == 0

    # Frame without a status column falls back to the all-status
    # threshold so we never accidentally drop everything to zero.
    no_status = customer_ab.drop(columns=["STATUS_C"])
    assert _r532_count_customers_with_min_open_barriers(
        no_status, "customer_name", threshold=3
    ) == 3
