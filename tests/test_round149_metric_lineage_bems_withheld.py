"""Round 149 — Metric_Lineage kpi.bems unavailable must override legacy bems=0 heuristic."""

from pathlib import Path

import pandas as pd
import pytest

from report_iteration_loop import compare_kpi_parity, extract_xlsx_kpis


def test_metric_lineage_bems_tac_subset_label_withholds_legacy_zero(tmp_path) -> None:
    path = tmp_path / "source-data.xlsx"
    tac = pd.DataFrame(columns=["Case #", "Status"])
    lineage = pd.DataFrame(
        [
            {
                "Metric_Key": "kpi.bems",
                "Display_Label": "BEMS escalations (TAC subset)",
                "Metric_Value": None,
                "Source_State": "unavailable",
            },
            {
                "Metric_Key": "kpi.team_members",
                "Display_Label": "Team members",
                "Metric_Value": 1,
                "Source_State": "available",
            },
        ]
    )
    report_info = pd.DataFrame([{"Item": "Manager", "Value": "All Managers"}])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        tac.to_excel(writer, sheet_name="TAC_Cases", index=False)
        lineage.to_excel(writer, sheet_name="Metric_Lineage", index=False)
        report_info.to_excel(writer, sheet_name="Report_Info", index=False)

    kpis = extract_xlsx_kpis(path)
    assert "bems" not in kpis["values"]
    assert "bems" in kpis["withheld_kpis"]
    assert kpis["values"]["team_members"] == "1"


@pytest.mark.skipif(
    not Path(
        "/Users/jestory/Downloads/adoptiq_build111_final_build111-final-sweep-r149b-20260806T151438Z/"
        "AdoptIQ_Source_Data_Compact_All_Managers_All_Contact_Center_90d_1786029508__"
        "data-loop-build111-final-sweep-r149b-20260806T151438Z__scenario-compact__ts-20260806T152658Z.xlsx"
    ).exists(),
    reason="Build 111 compact artifact not present on this machine",
)
def test_build111_compact_source_data_bems_withheld_not_zero() -> None:
    path = Path(
        "/Users/jestory/Downloads/adoptiq_build111_final_build111-final-sweep-r149b-20260806T151438Z/"
        "AdoptIQ_Source_Data_Compact_All_Managers_All_Contact_Center_90d_1786029508__"
        "data-loop-build111-final-sweep-r149b-20260806T151438Z__scenario-compact__ts-20260806T152658Z.xlsx"
    )
    kpis = extract_xlsx_kpis(path)
    assert "bems" not in kpis["values"]
    assert "bems" in kpis["withheld_kpis"]


def test_bems_withheld_matches_docx_for_parity_gate() -> None:
    docx_kpis = {
        "values": {"team_members": "1"},
        "withheld_kpis": ["bems", "support_cases", "total_customers"],
    }
    xlsx_kpis = {
        "values": {"team_members": "1"},
        "withheld_kpis": ["bems", "support_cases", "total_customers"],
    }
    parity = compare_kpi_parity(docx_kpis, xlsx_kpis, strict=True, required_keys=("team_members",))
    assert parity.passed is True
    assert parity.details["reason"] == "compared_common_kpis"
