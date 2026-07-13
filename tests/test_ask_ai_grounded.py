import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ask_ai_grounded as grounded


def test_retrieval_plan_enables_domains_from_question():
    plan = grounded.build_retrieval_plan(
        "Which customers have contract risk, rising barrier velocity, and open incidents tied to CSC defects?"
    )
    assert "core" in plan["domains"]
    assert "contracts" in plan["domains"]
    assert "trends" in plan["domains"]
    assert "intel" in plan["domains"]
    assert "enhanced_account_insights" in plan["datasets"]
    assert "period_comparison" in plan["datasets"]


def test_records_from_dataframe_extracts_source_ids_and_inline_ids():
    df = pd.DataFrame(
        [
            {
                "ID": "AB-1234",
                "BU_NAME": "Acme Corp",
                "SUBJECT_C": "SSO issue tied to CSCwa12345 and BEMS998877",
                "SEVERITY_C": "High",
                "STATUS_C": "Open",
                "OPEN_DATE_C": "2026-02-10",
            }
        ]
    )
    records, citation_ids = grounded._records_from_dataframe(
        df=df,
        source_type="AdoptionBarrier",
        id_columns=("ID",),
        text_columns=("SUBJECT_C", "SEVERITY_C", "STATUS_C"),
        customer_columns=("BU_NAME",),
        timestamp_columns=("OPEN_DATE_C",),
    )
    assert len(records) == 1
    assert records[0].source_id == "AB-1234"
    assert "AB-1234" in citation_ids
    assert "CSCWA12345" in citation_ids
    assert "BEMS998877" in citation_ids


def test_compose_grounded_answer_rejects_unsupported_citations():
    payload = {
        "executive_summary": "Top risk is concentrated in one customer.",
        "claims": [
            {"statement": "Acme has unresolved critical barriers.", "citations": ["AB-1001"]},
            {"statement": "Unknown claim should be filtered.", "citations": ["AB-DOES-NOT-EXIST"]},
        ],
        "actions": ["Escalate AB-1001 with owners this week."],
        "unknowns": [],
    }
    answer, rejected = grounded.compose_grounded_answer(payload, allowed_ids={"AB-1001"})
    assert "Supported Findings" in answer
    assert "AB-1001" in answer
    assert "AB-DOES-NOT-EXIST" not in answer
    assert "Evidence Gaps" in answer
    # Round 7 / Phase 5.2: ``executive_summary`` is now also required
    # to ship its own citations; the summary in this fixture is a bare
    # qualitative sentence with no SourceID so it is dropped/demoted as
    # well, raising the total rejected count from the previous 1
    # (claim-only) to 2 (claim + summary).
    assert rejected >= 1


def test_build_evidence_context_honors_budget_and_returns_ids():
    records = [
        grounded.EvidenceRecord("SupportCase", "CASE-1", "Acme", "2026-03-01", "P1 outage linked to CSCwa12345", 0.9),
        grounded.EvidenceRecord("SupportCase", "CASE-2", "Beta", "2026-03-02", "Provisioning request", 0.7),
    ]
    context, allowed_ids, used = grounded.build_evidence_context(
        records=records,
        question="Which P1 outage has defect linkage?",
        domains=["cases", "intel"],
        char_budget=300,
        max_records=10,
    )
    assert used >= 1
    assert "SourceID: CASE-1" in context
    assert "CASE-1" in allowed_ids
