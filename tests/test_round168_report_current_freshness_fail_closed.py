"""Current-state report questions fail closed when freshness is not trustworthy."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

import ask_ai_grounded as grounded
from tests.test_round147_report_ai_usefulness import (
    FINGERPRINT,
    _action_group,
    _action_row,
    _request,
    _risk_group,
    _run_at_as_of,
)


STALE_AS_OF = "2020-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "question",
    (
        "Which customers are in the most recent risk view?",
        "Show the newest action plan records.",
        "What is the present state of this account?",
        "Which customers need attention at present?",
        "What needs attention right now?",
    ),
)
def test_current_intent_recognizes_common_operational_phrasing(question: str) -> None:
    intent = grounded._r147_report_question_intent(question)  # noqa: SLF001

    assert intent["wants_current"] is True


def _assert_current_answer_withheld(
    result: dict,
    *,
    forbidden: tuple[str, ...],
) -> None:
    assert result["ok"] is True
    assert result["response_state"] in {"partial", "stale"}
    assert result["confidence"]["level"] != "High"
    assert result["canonical_verified"] is False
    assert result["canonical_headline"] == {}
    assert result["evidence_records"] == []
    assert result["evidence_index"] == []
    assert result["evidence_records_used"] == 0
    assert result["evidence_records_total"] == 0
    assert result["account_total"] == 0
    assert result["retrieval_diag"]["direct_question_answered"] is False
    assert result["retrieval_diag"]["question_intent"]["current"] is True
    citation = result["retrieval_diag"]["report_citation_contract"]
    assert citation["required"] is False
    assert citation["all_citations_resolved"] is True
    assert citation["citation_count"] == 0
    assert "cannot establish the current state" in result["answer"]
    assert "No frozen customer, count, score, or record finding" in result["answer"]
    assert "### Supported Findings" not in result["answer"]
    assert "### Exact Source Records" not in result["answer"]
    assert "### Exact Next Actions" not in result["answer"]
    serialized = json.dumps(result, sort_keys=True)
    for value in forbidden:
        assert value not in serialized


@pytest.mark.parametrize(
    ("question", "groups", "forbidden"),
    (
        (
            "Which customers currently need attention?",
            [_risk_group("Acme Current Leak", 54.2)],
            ("Acme Current Leak", "acme_current_leak", "54.2"),
        ),
        (
            "Show the current action plan records.",
            [
                _action_group(
                    "recommendation.current_records",
                    [
                        _action_row(
                            "AP-STALE-RECORD",
                            status="Overdue",
                            owner="Stale Owner",
                            next_action="Call stale sponsor",
                            row_number=2,
                        )
                    ],
                    evidence_type="recommendation",
                )
            ],
            ("AP-STALE-RECORD", "Stale Owner", "Call stale sponsor"),
        ),
        (
            "How many customers currently need attention?",
            [_risk_group("Count Leak Customer", 61.7)],
            ("Count Leak Customer", "count_leak_customer", "61.7"),
        ),
        (
            "Does Specific Stale Customer currently need attention?",
            [_risk_group("Specific Stale Customer", 88.4)],
            ("Specific Stale Customer", "specific_stale_customer", "88.4"),
        ),
    ),
)
def test_stale_current_questions_never_surface_frozen_findings(
    question: str,
    groups: list[dict],
    forbidden: tuple[str, ...],
) -> None:
    result = _run_at_as_of(question, groups, STALE_AS_OF)

    _assert_current_answer_withheld(result, forbidden=forbidden)


def test_current_aged_snapshot_with_unavailable_data_state_is_withheld() -> None:
    request = _request(
        "Which customers currently need attention?",
        [_risk_group("Unavailable Clock Customer", 73.9)],
    )
    bundle = json.loads(request.report_fact_bundle)
    bundle["data_as_of_state"] = "unavailable"
    request = replace(
        request,
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )

    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        request,
        {"scope_type": "team", "report_analysis_id": request.report_analysis_id},
    )

    assert result["snapshot_freshness"]["state"] == "current"
    assert result["retrieval_diag"]["data_as_of_state"] == "unavailable"
    _assert_current_answer_withheld(
        result,
        forbidden=("Unavailable Clock Customer", "unavailable_clock_customer", "73.9"),
    )


def test_current_aged_v2_snapshot_missing_data_state_is_withheld() -> None:
    request = _request(
        "Which customers currently need attention?",
        [_risk_group("Missing State Customer", 79.1)],
    )
    bundle = json.loads(request.report_fact_bundle)
    bundle.pop("data_as_of_state", None)
    request = replace(
        request,
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )

    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        request,
        {"scope_type": "team", "report_analysis_id": request.report_analysis_id},
    )

    assert result["snapshot_freshness"]["state"] == "current"
    assert result["retrieval_diag"]["data_as_of_state"] == "unavailable"
    _assert_current_answer_withheld(
        result,
        forbidden=("Missing State Customer", "missing_state_customer", "79.1"),
    )


def _legacy_request(question: str) -> grounded.AskAIRequest:
    bundle = {
        "schema": "report-bound-facts/v1",
        "canonical_snapshot": True,
        "analysis_id": "legacy-round168-current",
        "fact_fingerprint": FINGERPRINT,
        "data_as_of_utc": STALE_AS_OF,
        "manager": "Manager One",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "scope_member": "",
        "source_states": {"Account_Summary": "available"},
        "decision_metrics": [
            {
                "metric_key": "kpi.customers_at_risk",
                "label": "Customers at risk",
                "value": 1,
                "display_value": "1",
                "unit": "records",
                "source_state": "available",
                "source_sheet": "Account_Summary",
            }
        ],
        "action_plans": [],
        "accounts": [
            {
                "customer": "Legacy Stale Customer",
                "risk_band": "HIGH",
                "risk_score_0_100": 92,
                "open_action_plans": 3,
                "overdue_action_plans": 2,
            }
        ],
    }
    return grounded.AskAIRequest(
        question=question,
        manager="Manager One",
        technology="All",
        days=90,
        report_analysis_id="legacy-round168-current",
        report_type="leader",
        data_as_of_utc=STALE_AS_OF,
        evaluation_utc="2026-08-13T12:00:00Z",
        fact_fingerprint=FINGERPRINT,
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )


def test_legacy_v1_without_trustworthy_freshness_cannot_answer_current() -> None:
    request = _legacy_request("Which customers currently need attention?")

    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        request,
        {"scope_type": "team", "report_analysis_id": request.report_analysis_id},
    )

    assert result["retrieval_diag"]["method"] == (
        "immutable_report_snapshot_legacy_projection"
    )
    assert result["retrieval_diag"]["evidence_contract"] == (
        "legacy-report-projection/v1"
    )
    assert result["retrieval_diag"]["data_as_of_state"] == "unavailable"
    _assert_current_answer_withheld(
        result,
        forbidden=("Legacy Stale Customer", "92", "customers_at_risk"),
    )


def test_legacy_v1_explicit_historical_question_retains_as_of_facts() -> None:
    request = _legacy_request(
        "Which customers needed attention in this report as of 2020?"
    )

    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        request,
        {"scope_type": "team", "report_analysis_id": request.report_analysis_id},
    )

    assert result["ok"] is True
    assert "as of 2020-01-01T00:00:00Z" in result["answer"]
    assert "Legacy Stale Customer: risk band HIGH" in result["answer"]
    assert result["retrieval_diag"].get("direct_question_answered") is not False
