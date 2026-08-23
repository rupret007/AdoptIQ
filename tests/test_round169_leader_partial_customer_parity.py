"""Round 169 retained-customer lower-bound parity regressions."""

from __future__ import annotations

import base64

import pandas as pd

import decision_report_delivery as delivery
from report_iteration_loop import (
    compare_kpi_parity,
    extract_docx_kpis,
    extract_xlsx_kpis,
)


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_CORE_CUSTOMER_SOURCES = {
    "Subscriptions",
    "Action_Plans",
    "Adoption_Barriers",
    "Customer_Pulse",
    "TAC_Cases",
    "Success_Priorities",
}


def _partial_customer_facts() -> dict:
    subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-1",
                "ACCOUNT_ID_C": "ACC-1",
                "BU_NAME": "Acme Corporation",
            },
            {
                "SUBSCRIPTION_ID": "SUB-2",
                "ACCOUNT_ID_C": "ACC-2",
                "BU_NAME": "Beta Industries",
            },
        ]
    )
    tac_cases = pd.DataFrame(
        [
            {
                "SR Number": "700000001",
                "Customer": "Acme Corporation",
                "Severity": "2",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-10",
            }
        ]
    )
    tac_cases.attrs["partial"] = True
    tac_cases.attrs["source_mode_detail"] = (
        "one conflicting source record was quarantined"
    )
    empty = pd.DataFrame()
    return delivery.build_report_facts(
        {
            "Synthetic Member": {
                "subscriptions": subscriptions,
                "action_plans": empty.copy(),
                "adoption_barriers": empty.copy(),
                "customer_pulse": empty.copy(),
                "tac_cases": tac_cases,
                "success_priorities": empty.copy(),
            }
        },
        report_type="Leader",
        scope_type="team",
        scope_value="Entire team",
        manager_name="Synthetic Manager",
        days=90,
        as_of="2026-08-03T21:00:00Z",
    )


def test_partial_customer_count_is_an_evidence_linked_lower_bound() -> None:
    facts = _partial_customer_facts()
    sheets = delivery.build_source_data_sheets(facts)
    lineage = sheets["Metric_Lineage"].loc[
        sheets["Metric_Lineage"]["Metric_Key"].eq("kpi.customers")
    ].iloc[0]
    evidence = sheets["Evidence_Links"].loc[
        sheets["Evidence_Links"]["Evidence_Key"].eq("kpi.customers")
    ]

    assert lineage["Metric_Value"] == 2
    assert lineage["Source_State"] == "partial"
    assert "partial-coverage lower bound" in lineage["Caveat"]
    assert set(evidence["Metric_Value"]) == {2}
    assert set(evidence["Source_State"]) == {"partial"}
    assert len(evidence) == 2
    assert evidence["Source_Row_Number"].notna().all()
    contract = delivery.validate_cross_artifact_contract(facts, sheets)
    assert contract["ok"], contract["errors"]


def test_partial_leader_docx_and_xlsx_disclose_the_same_customer_count(
    tmp_path,
    monkeypatch,
) -> None:
    facts = _partial_customer_facts()
    monkeypatch.setattr(
        delivery,
        "_render_chart_image",
        lambda _chart_id, _rows, target: target.write_bytes(_TINY_PNG) > 0,
    )
    docx_path = tmp_path / "AdoptIQ_Report_Leader_Synthetic_Manager_Team.docx"
    xlsx_path = delivery.source_data_path_for_word(docx_path)
    document = delivery.build_concise_word_document(facts)
    document.save(docx_path)
    sheets = delivery.build_source_data_sheets(facts)
    delivery.write_source_data_workbook(xlsx_path, sheets)

    written_contract = delivery.validate_written_source_workbook(xlsx_path, facts)
    assert written_contract["ok"], written_contract["errors"]
    docx_kpis = extract_docx_kpis(docx_path)
    xlsx_kpis = extract_xlsx_kpis(xlsx_path)
    parity = compare_kpi_parity(
        docx_kpis,
        xlsx_kpis,
        strict=True,
        required_keys=("manager", "team_members", "total_customers", "window_days"),
    )

    assert docx_kpis["values"]["total_customers"] == "2"
    assert xlsx_kpis["values"]["total_customers"] == "2"
    assert "total_customers" not in xlsx_kpis["withheld_kpis"]
    assert parity.passed, parity.details
    assert parity.details["availability_mismatches"] == []


def test_failed_customer_sources_still_withhold_the_count() -> None:
    facts = _partial_customer_facts()
    coverage = facts["source_coverage"].copy()
    core_mask = coverage["Source_Sheet"].isin(_CORE_CUSTOMER_SOURCES)
    coverage.loc[core_mask, "Source_State"] = "unavailable"
    coverage.loc[core_mask, "Detail"] = "synthetic source failure"
    facts["source_coverage"] = coverage

    lineage = delivery._build_lineage(facts)  # noqa: SLF001 - fail-closed contract pin
    customers = lineage.loc[lineage["Metric_Key"].eq("kpi.customers")].iloc[0]

    assert pd.isna(customers["Metric_Value"])
    assert customers["Source_State"] == "unavailable"
    assert "Metric value withheld" in customers["Caveat"]
