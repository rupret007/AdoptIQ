"""Round 147 claim-to-cited-record entailment safeguards."""

from __future__ import annotations

from typing import Any

import pytest

import ask_ai_grounded as grounded


EVIDENCE = [
    {
        "source_id": "CASE-1",
        "source_type": "SupportCase",
        "customer": "Acme Corp",
        "timestamp": "2026-08-01T10:00:00Z",
        "text": (
            "Subject: SSO outage | Status: Open | Severity: P1 | "
            "Impacted users: 3"
        ),
    },
    {
        "source_id": "CASE-2",
        "source_type": "SupportCase",
        "customer": "Beta LLC",
        "timestamp": "2026-08-02T10:00:00Z",
        "text": (
            "Subject: Training request | Status: Closed | Severity: P3 | "
            "Impacted users: 1"
        ),
    },
    {
        "source_id": "AP-3",
        "source_type": "ActionPlan",
        "customer": "Acme Corp",
        "timestamp": "2026-08-03T10:00:00Z",
        "text": "Subject: Training adoption plan | Status: Completed",
    },
]
ALLOWED = {"CASE-1", "CASE-2", "AP-3"}


def _compose(
    statement: str,
    citation: str,
    *,
    canonical_numbers: set[str] | None = None,
    include_evidence: bool = True,
) -> tuple[str, int]:
    payload = {
        "executive_summary": "",
        "claims": [{"statement": statement, "citations": [citation]}],
        "actions": [],
        "unknowns": [],
    }
    kwargs: dict[str, Any] = {}
    if include_evidence:
        kwargs["evidence_records"] = EVIDENCE
    return grounded.compose_grounded_answer(
        payload,
        ALLOWED,
        canonical_numbers or set(),
        **kwargs,
    )


def test_matched_claim_is_rendered_with_its_exact_cited_record() -> None:
    answer, rejected = _compose(
        "Acme Corp's CASE-1 open P1 SSO outage impacted 3 users on 2026-08-01.",
        "CASE-1",
    )

    assert rejected == 0
    assert "### Supported Findings" in answer
    assert "[Sources: CASE-1]" in answer
    assert "Suppressed claim" not in answer


def test_unrelated_but_allowed_citation_is_demoted() -> None:
    answer, rejected = _compose(
        "Acme Corp experienced a password reset outage.",
        "AP-3",
    )

    assert rejected == 1
    assert "### Supported Findings" not in answer
    assert "### Evidence Gaps" in answer
    assert "cited records do not support it" in answer


def test_cross_row_entity_status_bleed_is_demoted() -> None:
    payload = {
        "executive_summary": "",
        "claims": [{
            "statement": "Acme Corp's SSO outage is closed.",
            "citations": ["CASE-1", "CASE-2"],
        }],
        "actions": [],
        "unknowns": [],
    }

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        ALLOWED,
        evidence_records=EVIDENCE,
    )

    assert rejected == 1
    assert "### Supported Findings" not in answer
    assert "Acme Corp's SSO outage is closed" not in answer


def test_compound_claim_can_use_one_exact_row_per_entity_bound_fact() -> None:
    payload = {
        "executive_summary": "",
        "claims": [{
            "statement": (
                "Acme Corp has an open P1 SSO outage and a completed "
                "training adoption plan."
            ),
            "citations": ["CASE-1", "AP-3"],
        }],
        "actions": [],
        "unknowns": [],
    }

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        ALLOWED,
        evidence_records=EVIDENCE,
    )

    assert rejected == 0
    assert "### Supported Findings" in answer
    assert "[Sources: AP-3, CASE-1]" in answer


def test_compound_claim_keeps_explicit_id_bound_to_elided_attributes() -> None:
    evidence = [
        EVIDENCE[0],
        {
            **EVIDENCE[1],
            "customer": "Acme Corp",
        },
    ]
    payload = {
        "executive_summary": "",
        "claims": [{
            "statement": "Acme Corp's CASE-1 SSO outage is open and P3.",
            "citations": ["CASE-1", "CASE-2"],
        }],
        "actions": [],
        "unknowns": [],
    }

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        {"CASE-1", "CASE-2"},
        evidence_records=evidence,
    )

    assert rejected == 1
    assert "### Supported Findings" not in answer


@pytest.mark.parametrize(
    "statement",
    [
        # CASE-2 is a valid ID in the bounded corpus, but not in CASE-1.
        "Acme Corp's CASE-2 SSO outage is open.",
        # Beta is present elsewhere in the corpus, but not in cited CASE-1.
        "Beta LLC has an open SSO outage.",
        # Status and severity contradict cited CASE-1.
        "Acme Corp's SSO outage is closed.",
        "Acme Corp's SSO outage is P3.",
        # Non-canonical count/date occur elsewhere or nowhere, not in CASE-1.
        "Acme Corp's SSO outage impacted 4 users.",
        "Acme Corp's SSO outage occurred on 2026-08-02.",
    ],
)
def test_wrong_id_customer_status_severity_number_or_date_is_demoted(
    statement: str,
) -> None:
    answer, rejected = _compose(statement, "CASE-1")

    assert rejected == 1
    assert "### Supported Findings" not in answer
    assert statement not in answer
    assert "Unverified content was not repeated" in answer


def test_global_canonical_number_cannot_authorize_unrelated_customer_claim() -> None:
    answer, rejected = _compose(
        "Acme Corp has 9 open cases.",
        "CASE-1",
        canonical_numbers={"9"},
    )

    assert rejected == 1
    assert "### Supported Findings" not in answer


def test_exact_canonical_metric_record_can_support_its_aggregate() -> None:
    metric = {
        "source_id": "METRIC-OPEN-CASES-1234ABCD",
        "source_type": "CanonicalMetric",
        "customer": "Portfolio",
        "text": "Canonical metric open_cases: 9. Scope: team Local Fixture Manager.",
    }
    payload = {
        "executive_summary": "",
        "claims": [{
            "statement": "The portfolio has 9 open cases.",
            "citations": ["METRIC-OPEN-CASES-1234ABCD"],
        }],
        "actions": [],
        "unknowns": [],
    }

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        {"METRIC-OPEN-CASES-1234ABCD"},
        evidence_records=[metric],
    )

    assert rejected == 0
    assert "9 open cases" in answer


@pytest.mark.parametrize(
    "statement",
    [
        "Acme Corp's SSO outage caused churn.",
        "Acme Corp will churn because of the SSO outage.",
        "Acme Corp's SSO outage was not open.",
        "Terminate the owner for negligence related to CASE-1.",
    ],
)
def test_unsupported_causality_prediction_negation_or_high_impact_action_is_demoted(
    statement: str,
) -> None:
    answer, rejected = _compose(statement, "CASE-1")

    assert rejected == 1
    assert "### Supported Findings" not in answer


@pytest.mark.parametrize(
    "statement",
    [
        "Acme Corp loves the SSO outage.",
        "Acme Corp has a green healthy SSO outage.",
        "Acme Corp fired the SSO owner.",
        "Acme Corp lost the contract after the SSO outage.",
        "Acme Corp renewed after the SSO outage.",
        "Acme Corp is satisfied with the SSO outage.",
    ],
)
def test_salient_terms_must_exist_in_the_exact_cited_record(statement: str) -> None:
    answer, rejected = _compose(statement, "CASE-1")

    assert rejected == 1
    assert "### Supported Findings" not in answer


def test_record_body_id_cannot_alias_an_unrelated_source_row() -> None:
    evidence = [
        *EVIDENCE,
        {
            "source_id": "AP-ALIAS",
            "source_type": "ActionPlan",
            "customer": "Beta LLC",
            "text": "Unrelated action that merely mentions CASE-ALIAS in notes.",
        },
    ]
    payload = {
        "executive_summary": "",
        "claims": [{
            "statement": "Beta LLC has an unrelated action.",
            "citations": ["CASE-ALIAS"],
        }],
        "actions": [],
        "unknowns": [],
    }

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        {"CASE-ALIAS"},
        evidence_records=evidence,
    )

    assert rejected == 1
    assert "### Supported Findings" not in answer


@pytest.mark.parametrize("field", ["executive_summary", "actions"])
def test_summary_and_actions_use_the_same_entailment_gate(field: str) -> None:
    payload = {
        "executive_summary": "",
        "claims": [],
        "actions": [],
        "unknowns": [],
    }
    unsupported = "Acme will churn because CASE-1 shows an SSO outage."
    payload[field] = [unsupported] if field == "actions" else unsupported

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        ALLOWED,
        evidence_records=EVIDENCE,
    )

    assert rejected >= 1
    assert unsupported not in answer.split("### Evidence Gaps", 1)[0]


@pytest.mark.parametrize("field", ["executive_summary", "actions"])
def test_safe_opener_cannot_smuggle_an_unsupported_claim(field: str) -> None:
    unsupported = (
        "Based on the available evidence, Acme Corp will churn because "
        "CASE-1 shows an outage."
    )
    payload = {
        "executive_summary": "",
        "claims": [],
        "actions": [],
        "unknowns": [],
    }
    payload[field] = [unsupported] if field == "actions" else unsupported

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        ALLOWED,
        evidence_records=EVIDENCE,
    )

    assert rejected >= 1
    assert unsupported not in answer.split("### Evidence Gaps", 1)[0]


def test_omitted_evidence_records_preserves_legacy_and_eval_behavior() -> None:
    answer, rejected = _compose(
        "Acme Corp experienced a password reset outage.",
        "AP-3",
        include_evidence=False,
    )

    assert rejected == 0
    assert "### Supported Findings" in answer
    assert "[Sources: AP-3]" in answer


def test_allowed_citation_without_a_bounded_record_fails_closed() -> None:
    payload = {
        "executive_summary": "",
        "claims": [
            {
                "statement": "Acme Corp has an open SSO outage.",
                "citations": ["CASE-ALLOWED-BUT-DROPPED"],
            }
        ],
        "actions": [],
        "unknowns": [],
    }

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        {"CASE-ALLOWED-BUT-DROPPED"},
        evidence_records=EVIDENCE,
    )

    assert rejected == 1
    assert "### Supported Findings" not in answer
    assert "cited records do not support it" in answer


def test_ask_intel_passes_its_actual_bounded_records_to_entailment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adoptiq_backend
    import incident_storage

    monkeypatch.setattr(
        incident_storage,
        "get_all_external_intel",
        lambda **_kwargs: {
            "incidents": [
                {
                    "id": "INC-147A",
                    "status": "resolved",
                    "title": "Service recovered",
                    "impact_level": "minor",
                    "description": "Recovery completed.",
                    "published": "2026-08-01T10:00:00Z",
                },
                {
                    "id": "INC-147B",
                    "status": "active",
                    "title": "Authentication degradation",
                    "impact_level": "major",
                    "description": "Investigation continues.",
                    "published": "2026-08-02T10:00:00Z",
                },
            ],
            "maintenances": [],
            "bugs": [],
            "fetch_errors": {},
            "list_truncated": {},
            "source_states": {"status_feed": "available"},
        },
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "generate_llm_json_response",
        lambda *_args, **_kwargs: {
            "ok": True,
            "data": {
                "executive_summary": "",
                "claims": [
                    {
                        "statement": "INC-147A is a resolved service incident.",
                        "citations": ["INC-147B"],
                    }
                ],
                "actions": [],
                "unknowns": [],
            },
        },
    )

    result = grounded.run_intel_grounded_ask_ai("What recovered?", 30)

    assert result["ok"] is True
    assert "### Supported Findings" not in result["answer"]
    assert "cited records do not support it" in result["answer"]
    assert "citation_rejections=1" in result["context_summary"]
    assert result["response_state"] == "validation_failed"
