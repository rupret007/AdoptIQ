"""Round 165 Word citations must resolve to canonical companion-workbook keys."""

from __future__ import annotations

from collections import OrderedDict

import decision_report_delivery as delivery
from tests.test_round142_decision_report_delivery import _facts


def _paragraphs(document) -> list[str]:
    return [paragraph.text.strip() for paragraph in document.paragraphs]


def test_action_brief_cites_exact_selected_recommendation_evidence_keys() -> None:
    facts = _facts()
    sheets = delivery.build_source_data_sheets(facts)
    document = delivery.build_concise_word_document(facts)
    brief = delivery._decision_brief_contract(facts)

    expected_keys = [
        delivery.evidence_entity_key("recommendation", row[0])
        for row in facts["top_action_plans"][:3]
    ]
    assert brief["action_evidence_keys"] == expected_keys
    assert _paragraphs(document).count(brief["action_source_reference"]) == 1
    assert set(expected_keys).issubset(set(sheets["Evidence_Links"]["Evidence_Key"]))
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert contract["ok"], contract["errors"]

    citation = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.strip() == brief["action_source_reference"]
    )
    citation.text = citation.text.replace(expected_keys[0], "recommendation.missing")
    tampered = delivery.validate_word_semantics(facts, document)
    assert not tampered["ok"]
    assert any("exact selected recommendation" in error for error in tampered["errors"])


def test_all_withheld_chart_citation_uses_real_status_and_age_families() -> None:
    facts = _facts()
    facts["chart_data"] = facts["chart_data"].copy()
    facts["chart_data"]["Value"] = None
    document = delivery.build_concise_word_document(facts)
    reference_groups = delivery._expected_chart_reference_groups(facts)

    assert len(reference_groups) == 1
    group = reference_groups[0]
    assert "chart.action_plan_status.*" in group
    assert "chart.action_plan_age.*" in group
    assert "chart.action_plan_status_aging.*" not in group
    expected = delivery._artifact_reference_text("Metric_Lineage", group)
    assert _paragraphs(document).count(expected) == 1
    semantic = delivery.validate_word_semantics(facts, document)
    assert semantic["ok"], semantic["errors"]

    citation = next(
        paragraph for paragraph in document.paragraphs if paragraph.text.strip() == expected
    )
    citation.text = citation.text.replace(
        "chart.action_plan_status.*; chart.action_plan_age.*",
        "chart.action_plan_status_aging.*",
    )
    tampered = delivery.validate_word_semantics(facts, document)
    assert not tampered["ok"]
    assert any("noncanonical chart evidence family" in error for error in tampered["errors"])


def test_cross_artifact_contract_rejects_missing_rendered_reference_keys() -> None:
    facts = _facts()
    sheets = delivery.build_source_data_sheets(facts)
    missing_recommendation = delivery._decision_brief_contract(facts)[
        "action_evidence_keys"
    ][0]
    tampered = OrderedDict((name, frame.copy()) for name, frame in sheets.items())
    links = tampered["Evidence_Links"]
    tampered["Evidence_Links"] = links.loc[
        links["Evidence_Key"] != missing_recommendation
    ].copy()

    contract = delivery.validate_cross_artifact_contract(facts, tampered)
    assert not contract["ok"]
    assert any(
        "Immediate Action Plan source reference does not resolve" in error
        for error in contract["errors"]
    )
