"""Round 145 matrix contracts for the explicit local acceptance runtime."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from unittest.mock import patch

import pandas as pd

from executive_intelligence_formatter import ExecutiveIntelligenceFormatter
from report_iteration_loop import (
    MATRIX_TECHNOLOGY_CHOICES,
    build_local_acceptance_option_matrix,
    extract_xlsx_kpis,
)
from scripts import run_report_option_matrix as matrix_runner


def test_local_matrix_covers_every_report_technology_and_leader_scope() -> None:
    matrix = build_local_acceptance_option_matrix()
    technologies = set(MATRIX_TECHNOLOGY_CHOICES)

    comprehensive = {
        item.payload.get("technology")
        for item in matrix.values()
        if item.payload.get("report_type") == "comprehensive"
    }
    compact = {
        item.payload.get("technology")
        for item in matrix.values()
        if item.endpoint == "/start_compact_analysis"
    }
    renewal = {
        item.payload.get("technology")
        for item in matrix.values()
        if item.payload.get("report_type") == "renewal_portfolio"
    }
    leader_scopes = {
        item.payload.get("scope_type")
        for item in matrix.values()
        if item.endpoint == "/start_leader_report"
    }

    assert technologies <= comprehensive
    assert technologies <= compact
    assert technologies <= renewal
    assert {"team", "member", "customer"} <= leader_scopes
    assert "g_renewal_single_customer" in matrix
    assert "g_subscription_analysis" in matrix
    assert all(
        item.payload.get("manager") in {None, "Local Fixture Manager", "All Managers"}
        for item in matrix.values()
    )


def test_local_matrix_scope_values_are_sanitized_and_deterministic() -> None:
    matrix = build_local_acceptance_option_matrix()
    assert matrix["d_leader_member"].payload["scope_value"].endswith(
        "@example.invalid"
    )
    assert matrix["d_leader_customer"].payload["scope_value"] == "Acme Corporation"
    assert matrix["g_subscription_analysis"].payload["subscription_id"] == "SUB-001"


def test_local_matrix_runner_rejects_normal_or_live_connectivity_mode() -> None:
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value={"ok": True, "mode": "snowflake"},
        ),
    ):
        code = matrix_runner.main(["--blocks", "A", "--local-acceptance"])
    assert code == 5


def test_compact_provenance_uses_canonical_distinct_barrier_count() -> None:
    formatter = ExecutiveIntelligenceFormatter()
    barriers = pd.DataFrame(
        [
            {"ID": "AB-001", "BU_NAME": "Acme"},
            {"ID": "AB-001", "BU_NAME": "Acme"},
            {"ID": "AB-002", "BU_NAME": "Beta"},
        ]
    )
    formatter.add_data_citations_section(barriers, pd.DataFrame())
    text = "\n".join(paragraph.text for paragraph in formatter.doc.paragraphs)
    assert_in_source(text, "Adoption Barriers: 2 distinct task(s)", label='text')
    assert_in_source(text, "deduplicated by stable source ID", label='text')


def test_parity_harness_treats_explicit_empty_contract_as_zero(tmp_path) -> None:
    path = tmp_path / "source-data.xlsx"
    empty_contract = pd.DataFrame(
        [
            {
                "Status": "EMPTY",
                "Dataset": "All_Adoption_Barriers",
                "Message": "No records were returned for the selected scope.",
                "Generated_At": "2026-08-03 21:00:00 UTC",
            }
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        empty_contract.to_excel(writer, sheet_name="All_Adoption_Barriers", index=False)
        empty_contract.assign(Dataset="All_Support_Cases").to_excel(
            writer,
            sheet_name="All_Support_Cases",
            index=False,
        )
        pd.DataFrame([{"Message": "No data available for this analysis"}]).to_excel(
            writer,
            sheet_name="Customer_Adoption_Barriers",
            index=False,
        )

    values = extract_xlsx_kpis(path)["values"]

    assert values["adoption_barriers"] == "0"
    assert values["support_cases"] == "0"
    assert values["bems"] == "0"


def test_metric_lineage_overrides_legacy_action_plan_open_heuristic(tmp_path) -> None:
    path = tmp_path / "decision-source-data.xlsx"
    plans = pd.DataFrame(
        [
            {"ID": "AP-001", "STATUS_C": "Open"},
            {"ID": "AP-002", "STATUS_C": "Custom workflow"},
            {"ID": "AP-003", "STATUS_C": "Completed - Successful"},
        ]
    )
    lineage = pd.DataFrame(
        [
            {
                "Metric_Key": "kpi.action_plans_total",
                "Display_Label": "Action Plans",
                "Metric_Value": 3,
            },
            {
                "Metric_Key": "kpi.action_plans_open",
                "Display_Label": "Open Action Plans",
                "Metric_Value": 1,
            },
        ]
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        plans.to_excel(writer, sheet_name="Action_Plans", index=False)
        lineage.to_excel(writer, sheet_name="Metric_Lineage", index=False)

    values = extract_xlsx_kpis(path)["values"]

    assert values["action_plans"] == "3"
    assert values["open_action_plans"] == "1"
