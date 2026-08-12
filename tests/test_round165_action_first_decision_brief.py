"""Round 165 action-first, family-aware Word decision surface regressions."""

from __future__ import annotations

import decision_report_delivery as delivery
from tests.test_round142_decision_report_delivery import _facts


def _header(table) -> tuple[str, ...]:
    return tuple(cell.text.strip() for cell in table.rows[0].cells)


def test_decision_brief_precedes_descriptive_kpis_charts_and_detailed_rollup() -> None:
    facts = _facts()
    document = delivery.build_concise_word_document(facts)
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]

    brief = delivery._decision_brief_contract(facts)
    brief_position = paragraphs.index(brief["heading"])
    assert brief["family"] == "leader"
    assert paragraphs[brief_position + 1] == brief["introduction"]
    assert brief_position < paragraphs.index("KPI and Data-Coverage Snapshot")
    assert brief_position < paragraphs.index("Charts and Trends")
    assert brief_position < paragraphs.index("Prioritized Action Plan Rollup")

    headers = [_header(table) for table in document.tables]
    assert ("Account", "Risk", "Why", "First move") in headers
    assert (
        "Account",
        "Risk",
        "Score",
        "Evidence state",
        "Top risk drivers",
        "Evidence-backed next action",
    ) not in headers
    assert ("Action Plan", "Account / owner", "Urgency", "First move") in headers

    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert contract["ok"], contract["errors"]
    assert contract["word_semantics"]["decision_brief_validated"] is True


def test_compact_risk_table_has_exact_geometry_and_tamper_blocks_publication() -> None:
    facts = _facts()
    document = delivery.build_concise_word_document(facts)
    risk_table = next(
        table
        for table in document.tables
        if _header(table) == ("Account", "Risk", "Why", "First move")
    )

    grid_widths = [
        int(column.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}w"))
        for column in risk_table._tbl.tblGrid
    ]
    assert grid_widths == [1800, 1512, 3096, 3672]
    assert sum(grid_widths) == 10080  # exact seven-inch usable report width

    risk_table.rows[1].cells[1].text = "CRITICAL | 999.0/100"
    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert not contract["ok"]
    assert any(
        "Word visible cells differ from canonical facts: Account | Risk | Why | First move"
        in error
        for error in contract["errors"]
    )


def test_family_copy_is_distinct_without_recalculating_report_metrics() -> None:
    facts = _facts()
    facts_by_family = {}
    for report_type in ("Leader", "Comprehensive", "Compact"):
        candidate = dict(facts)
        candidate["report_type"] = report_type
        candidate["legacy_adapter"] = {}
        facts_by_family[report_type] = delivery._decision_brief_contract(candidate)

    assert facts_by_family["Leader"]["heading"] == "Decision Brief: Leader Interventions"
    assert facts_by_family["Comprehensive"]["heading"] == (
        "Decision Brief: Portfolio Priorities"
    )
    assert facts_by_family["Compact"]["heading"] == (
        "Decision Brief: Immediate Customer Calls"
    )
    assert len(
        {facts_by_family[report_type]["introduction"] for report_type in facts_by_family}
    ) == 3
    assert all(
        facts_by_family[report_type]["risk_rows"]
        == facts_by_family["Leader"]["risk_rows"]
        for report_type in facts_by_family
    )
