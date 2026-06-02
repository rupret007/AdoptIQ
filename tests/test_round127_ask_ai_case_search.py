"""Round 127 / Build 96 — Ask AI case-search enumeration backend."""

from __future__ import annotations

import pandas as pd
import pytest

from ask_ai_grounded import (
    EvidenceRecord,
    _detect_case_search_intent,
    _portfolio_records_from_payload,
    _r127_prefilter_dataframe,
    _records_from_dataframe,
    build_retrieval_plan,
)


def test_detect_case_search_intent_ediscovery():
    assert _detect_case_search_intent(
        "Which customer has a compliance report mentioning eDiscovery for terminated users?"
    )


def test_detect_case_search_intent_negative_control():
    assert not _detect_case_search_intent("What is our total ARR at risk this quarter?")


def test_build_retrieval_plan_case_search_caps():
    plan = build_retrieval_plan("List all support cases mentioning ediscovery")
    assert plan["intent"] == "case_search_enumeration"
    assert plan["max_evidence_rows"] >= 400


def test_prefilter_keeps_matching_description_row():
    df = pd.DataFrame(
        [
            {"CASE_ID": "C1", "SUBJECT": "billing", "DESCRIPTION": "unrelated"},
            {"CASE_ID": "C2", "SUBJECT": "compliance", "DESCRIPTION": "eDiscovery hold for terminated user"},
        ]
    )
    out = _r127_prefilter_dataframe(df, "ediscovery terminated", ("SUBJECT", "DESCRIPTION"))
    assert len(out) == 1
    assert out.iloc[0]["CASE_ID"] == "C2"


def test_records_from_dataframe_maps_account_to_customer():
    df = pd.DataFrame(
        [
            {
                "CASE_ID": "CASE-99",
                "SUBJECT": "eDiscovery export",
                "DESCRIPTION": "terminated user mailbox",
                "ACCOUNT_ID": "ACC-1",
            }
        ]
    )
    records, ids = _records_from_dataframe(
        df,
        source_type="SupportCase",
        id_columns=("CASE_ID",),
        text_columns=("SUBJECT", "DESCRIPTION"),
        customer_columns=("ACCOUNT_ID",),
        timestamp_columns=(),
        max_rows=50,
        question="ediscovery",
        account_to_customer={"ACC-1": "ACME CORP"},
    )
    assert len(records) == 1
    assert records[0].customer == "ACME CORP"
    assert "eDiscovery" in records[0].text or "ediscovery" in records[0].text.lower()
    assert any("CASE" in x and "99" in x for x in ids)


def test_portfolio_support_case_includes_description_columns():
    payload = {
        "support_cases_snowflake": pd.DataFrame(
            [
                {
                    "CASE_ID": "CASE-1",
                    "SUBJECT": "hold",
                    "DESCRIPTION": "ediscovery terminated",
                    "ACCOUNT_ID": "A1",
                }
            ]
        ),
        "adoption_barriers": pd.DataFrame(),
    }
    records, _ = _portfolio_records_from_payload(
        payload,
        question="ediscovery",
        max_evidence_rows=400,
        account_to_customer={"A1": "BIG BANK"},
    )
    sc = [r for r in records if r.source_type == "SupportCase"]
    assert len(sc) == 1
    assert sc[0].customer == "BIG BANK"
    assert "Description:" in sc[0].text


def test_corpus_hybrid_search_fn_exists():
    import corpus_retriever as cr

    assert callable(getattr(cr, "search_playbook_hybrid", None))


def test_search_playbook_hybrid_lexical_fallback(monkeypatch):
    import corpus_retriever as cr
    from corpus_retriever import Chunk

    monkeypatch.setattr(
        cr,
        "search_playbook",
        lambda *a, **k: [
            Chunk(text="alpha", technology=None, theme=None, customer_name=None, score=1.0)
        ],
    )

    class _Cfg:
        ASK_AI_RETRIEVAL_METHOD = "lexical"

    monkeypatch.setattr("config.Config", _Cfg, raising=False)
    out = cr.search_playbook_hybrid("alpha", top_k=1)
    assert len(out) == 1
    assert out[0].text == "alpha"
