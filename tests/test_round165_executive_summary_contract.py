"""Round 165 fail-closed contract for the manager-facing Executive Summary."""

from __future__ import annotations

import pytest

from tests.test_round160_predictive_engine import _facts_with_predictive_customer


def _document_bundle():
    delivery, facts = _facts_with_predictive_customer()
    document = delivery.build_concise_word_document(facts)
    sheets = delivery.build_source_data_sheets(facts)
    heading_position = next(
        index
        for index, paragraph in enumerate(document.paragraphs)
        if paragraph.text.strip() == "Executive Summary"
    )
    return delivery, facts, sheets, document, heading_position


def test_executive_summary_exact_sequence_is_semantically_validated() -> None:
    delivery, facts, sheets, document, heading_position = _document_bundle()
    expected = delivery._executive_summary_contract(facts)

    assert [
        paragraph.text.strip()
        for paragraph in document.paragraphs[
            heading_position + 1 : heading_position + 4
        ]
    ] == [
        expected["summary"],
        expected["summary_source"],
        expected["purpose"],
    ]

    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert contract["ok"], contract["errors"]
    assert contract["word_semantics"]["executive_summary_validated"] is True


@pytest.mark.parametrize(
    ("offset", "tamper"),
    [
        (
            1,
            lambda text: text.replace(
                "Open Action Plans: 1",
                "Open Action Plans: 999",
            ),
        ),
        (
            2,
            lambda text: text.replace("kpi.customers", "kpi.customers.fabricated"),
        ),
        (
            3,
            lambda text: text.replace(
                "Complete activity, case, and source records",
                "Incomplete fabricated records",
            ),
        ),
    ],
    ids=("summary-fact", "source-citation", "purpose"),
)
def test_executive_summary_tampering_blocks_publication(offset, tamper) -> None:
    delivery, facts, sheets, document, heading_position = _document_bundle()
    paragraph = document.paragraphs[heading_position + offset]
    tampered = tamper(paragraph.text)
    assert tampered != paragraph.text
    paragraph.text = tampered

    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert not contract["ok"]
    assert any(
        "Word Executive Summary text, citation, or adjacency differs"
        in error
        for error in contract["errors"]
    )
    assert contract["word_semantics"]["executive_summary_validated"] is False


def test_executive_summary_duplicate_blocks_publication() -> None:
    delivery, facts, sheets, document, _ = _document_bundle()
    document.add_paragraph(delivery._executive_summary_contract(facts)["summary"])

    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert not contract["ok"]
    assert any(
        "Word Executive Summary text or citation is duplicated" in error
        for error in contract["errors"]
    )
