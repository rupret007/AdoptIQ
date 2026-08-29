"""Round 172: first-class corpus grounding for the support-theme insight.

The existing support-theme insight is deepened only when the Round 17
retriever can reconcile a current scoped customer + TAC technology to a safe
historical theme.  Every published corpus sentence carries a content-addressed
Report_Info receipt resolved by Evidence_Links.  Corpus absence or mismatch is
an exact no-op so pre-Round-172 no-corpus oracles remain byte-stable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import corpus_retriever as cr
import decision_report_delivery as delivery
import report_corpus_context
from corpus_indexer import index_folder, open_corpus_db
from tests.test_round157_reporting_ask_ai import _facts_with_tac


AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "round17"
NO_CORPUS_FINGERPRINT = "12d6607df3ed91b85863a40a4c54ee6218e8bc1a1cc876ee6b82b0fea66ebe1c"
NO_CORPUS_PARAGRAPH = (
    "Support themes (TAC): Webex Calling — 2 case(s) (1 escalated). "
    "Full case list in the Source Data workbook (TAC_Cases)."
)


@pytest.fixture(autouse=True)
def _clear_corpus_connection():
    cr.configure_connection(None)
    yield
    cr.configure_connection(None)


@pytest.fixture
def configured_round17_corpus(tmp_path: Path):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    for name in (
        "synthetic_cases.csv",
        "synthetic_barriers.csv",
        "synthetic_pulse.csv",
    ):
        shutil.copy2(FIXTURES / name, corpus_root / name)
    connection = open_corpus_db(tmp_path / "corpus.db")
    index_folder(connection, corpus_root)
    cr.configure_connection(connection)
    try:
        yield connection
    finally:
        cr.configure_connection(None)
        connection.close()


def _team_data(
    *,
    customer: str = "Synthetic Alpha",
    technology: str = "security",
) -> dict[str, dict[str, pd.DataFrame]]:
    return {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "SUBSCRIPTION_ID": "S1",
                        "BU_NAME": customer,
                        "STATUS_C": "Active",
                    }
                ]
            ),
            "action_plans": pd.DataFrame(
                [
                    {
                        "ID": "AP1",
                        "BU_NAME": customer,
                        "SUBJECT_C": "Review authentication",
                        "STATUS_C": "Open",
                    }
                ]
            ),
            "adoption_barriers": pd.DataFrame(
                [
                    {
                        "ID": "AB1",
                        "BU_NAME": customer,
                        "SEVERITY_C": "High",
                        "AB_STATUS_C": "Open",
                        "OPEN_DATE_C": "2026-07-01",
                    }
                ]
            ),
            "customer_pulse": pd.DataFrame(
                [
                    {
                        "ID": "CP1",
                        "BU_NAME": customer,
                        "SCORE__C": 8,
                        "PULSE_DATE_C": "2026-07-20",
                    }
                ]
            ),
            "tac_cases": pd.DataFrame(
                [
                    {
                        "SR Number": "T1",
                        "BU_NAME": customer,
                        "Severity": "P2",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-30",
                        "Tech.": technology,
                    },
                    {
                        "SR Number": "T2",
                        "BU_NAME": customer,
                        "Severity": "P3",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-29",
                        "Tech.": technology,
                    },
                ]
            ),
            "success_priorities": pd.DataFrame(),
        }
    }


def _build_report(
    *,
    customer: str = "Synthetic Alpha",
    technology: str = "security",
) -> dict:
    return delivery.build_report_facts(
        _team_data(customer=customer, technology=technology),
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera's Team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF.isoformat(),
        data_as_of_state="available",
        data_mode="offline_fixture",
        live_validation_performed=False,
    )


def _receipt_rows(sheets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    info = sheets["Report_Info"]
    return info.loc[
        info["Item"].fillna("").astype(str).str.startswith(
            "Corpus_Retriever_Receipt:"
        )
    ]


def test_no_corpus_preserves_exact_round157_oracle() -> None:
    cr.configure_connection(None)
    module, facts = _facts_with_tac(
        [
            {
                "SR Number": "1",
                "BU_NAME": "Acme",
                "Severity": "P1",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-30",
                "Tech.": "Webex Calling",
            },
            {
                "SR Number": "2",
                "BU_NAME": "Acme",
                "Severity": "P3",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-28",
                "Tech.": "Webex Calling",
            },
        ]
    )

    insight = facts["decision_insights"]["support_themes"]
    assert insight["paragraph_text"] == NO_CORPUS_PARAGRAPH
    assert "corpus_claims" not in insight
    assert module.fact_contract_fingerprint(facts) == NO_CORPUS_FINGERPRINT
    sheets = module.build_source_data_sheets(facts)
    assert len(sheets["Report_Info"]) == 46
    assert len(sheets["Evidence_Links"]) == 80
    assert _receipt_rows(sheets).empty


def test_corpus_match_publishes_one_scoped_claim_and_three_retrievals(
    configured_round17_corpus,
) -> None:
    facts = _build_report()
    insight = facts["decision_insights"]["support_themes"]

    sentence = (
        "Prior corpus pattern for Synthetic Alpha: authentication recurred "
        "1 occurrence in security."
    )
    assert sentence in insight["paragraph_text"]
    assert insight["source_sheets"] == ["TAC_Cases", "Report_Info"]
    assert len(insight["corpus_claims"]) == 1

    claim = insight["corpus_claims"][0]
    assert claim["sentence"] == sentence
    serialized = json.dumps(
        claim["receipt_payload"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == claim[
        "receipt_sha256"
    ]
    assert claim["receipt_id"] == (
        "Corpus_Retriever_Receipt:" + claim["receipt_sha256"][:16]
    )
    methods = [item["method"] for item in claim["receipt_payload"]["retrievals"]]
    assert methods == [
        "corpus_retriever.get_customer_history",
        "corpus_retriever.get_recurring_themes",
        "corpus_retriever.get_resolutions_for",
    ]
    assert "source_filename" not in serialized
    assert "synthetic_cases.csv" not in serialized


def test_corpus_no_match_and_unsafe_chunk_both_fail_soft(
    configured_round17_corpus,
    monkeypatch,
) -> None:
    mismatch = _build_report(technology="Webex Calling")
    mismatch_insight = mismatch["decision_insights"]["support_themes"]
    assert "corpus_claims" not in mismatch_insight
    assert "Prior corpus pattern" not in mismatch_insight["paragraph_text"]

    monkeypatch.setattr(report_corpus_context, "_is_safe_chunk", lambda _text: False)
    unsafe = _build_report()
    unsafe_insight = unsafe["decision_insights"]["support_themes"]
    assert "corpus_claims" not in unsafe_insight
    assert "Prior corpus pattern" not in unsafe_insight["paragraph_text"]


def test_broken_current_scope_projection_blocks_publication(monkeypatch) -> None:
    def broken_projection(_frames):
        raise RuntimeError("broken current-side projection")

    monkeypatch.setattr(delivery, "_customers_from_frames", broken_projection)
    with pytest.raises(RuntimeError, match="broken current-side projection"):
        _build_report()


def test_optional_resolution_requires_customer_history_intersection(
    configured_round17_corpus,
    monkeypatch,
) -> None:
    original_get_history = cr.get_customer_history
    resolution = cr.ResolutionRecord(
        method_text="Rotate the service token and rerun provisioning.",
        technology="security",
        theme="authentication",
        source_filename="synthetic_barriers.csv",
        source_section="resolution",
    )

    def history_with_resolution(*args, **kwargs):
        return replace(
            original_get_history(*args, **kwargs),
            top_resolutions=(resolution,),
        )

    monkeypatch.setattr(cr, "get_customer_history", history_with_resolution)
    monkeypatch.setattr(cr, "get_resolutions_for", lambda *_args, **_kwargs: [resolution])
    facts = _build_report()
    text = facts["decision_insights"]["support_themes"]["paragraph_text"]
    assert (
        "a previously observed resolution was Rotate the service token and "
        "rerun provisioning" in text
    )


def test_receipt_resolves_in_word_workbook_lineage_and_evidence(
    configured_round17_corpus,
    tmp_path: Path,
) -> None:
    facts = _build_report()
    insight = facts["decision_insights"]["support_themes"]
    sheets = delivery.build_source_data_sheets(facts)
    document = delivery.build_concise_word_document(facts)

    lineage = sheets["Metric_Lineage"]
    lineage_row = lineage.loc[
        lineage["Metric_Key"] == "insight.support_themes"
    ]
    assert len(lineage_row) == 1
    assert lineage_row.iloc[0]["Metric_Value"] == insight["paragraph_text"]

    receipt_rows = _receipt_rows(sheets)
    assert len(receipt_rows) == 1
    receipt = receipt_rows.iloc[0]
    assert receipt["Value"] == insight["corpus_claims"][0]["receipt_sha256"]

    links = sheets["Evidence_Links"]
    receipt_links = links.loc[
        (links["Evidence_Key"] == "insight.support_themes")
        & (links["Evidence_Role"] == "corpus_retriever_receipt")
    ]
    assert len(receipt_links) == 1
    link = receipt_links.iloc[0]
    assert link["Source_Sheet"] == "Report_Info"
    assert pd.notna(link["Source_Row_Number"])
    assert len(str(link["Source_Row_SHA256"])) == 64

    paragraph_texts = [paragraph.text for paragraph in document.paragraphs]
    assert paragraph_texts.count(insight["paragraph_text"]) == 1
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert contract["ok"], contract["errors"]

    workbook = tmp_path / "round172.xlsx"
    delivery.write_source_data_workbook(workbook, sheets)
    written = delivery.validate_written_source_workbook(workbook, facts)
    assert written["ok"], written["errors"]


def test_corpus_claim_changes_and_stabilizes_fact_fingerprint(
    configured_round17_corpus,
) -> None:
    first = _build_report()
    second = _build_report()
    with_corpus = delivery.fact_contract_fingerprint(first)
    assert delivery.fact_contract_fingerprint(second) == with_corpus

    cr.configure_connection(None)
    without = _build_report()
    assert "corpus_claims" not in without["decision_insights"]["support_themes"]
    assert delivery.fact_contract_fingerprint(without) != with_corpus


def test_tampered_corpus_receipt_blocks_workbook_publication(
    configured_round17_corpus,
) -> None:
    facts = copy.deepcopy(_build_report())
    facts["decision_insights"]["support_themes"]["corpus_claims"][0][
        "receipt_sha256"
    ] = "0" * 64
    with pytest.raises(ValueError, match="mismatched corpus receipt"):
        delivery.build_source_data_sheets(facts)
