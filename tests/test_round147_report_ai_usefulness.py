"""Report-bound Ask AI exact-value and decision-usefulness regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ask_ai_grounded as grounded
import decision_report_delivery as delivery
import manager_decision_workspace as workspace
from tests.test_round142_decision_report_delivery import _facts


AS_OF = "2026-08-04T12:00:00Z"
FINGERPRINT = "sha256:round147-usefulness"


def _request(
    question: str,
    groups: list[dict],
    *,
    turn_question: str = "",
) -> grounded.AskAIRequest:
    bundle = {
        "schema": "report-bound-facts/v2",
        "canonical_snapshot": True,
        "analysis_id": "leader-147-usefulness",
        "fact_fingerprint": FINGERPRINT,
        "data_as_of_utc": AS_OF,
        "manager": "Manager One",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "scope_member": "",
        "source_states": {
            "Account_Summary": "available",
            "Action_Plans": "available",
        },
        "decision_metrics": [],
        "evidence_contract": "canonical-evidence-links/v1",
        "exact_evidence": {
            "schema": "report-bound-evidence/v1",
            "evidence_contract": "canonical-evidence-links/v1",
            "fact_fingerprint": FINGERPRINT,
            "data_as_of_utc": AS_OF,
            "truncated": False,
            "groups": groups,
        },
        "action_plans": [],
        "accounts": [],
    }
    return grounded.AskAIRequest(
        question=question,
        turn_question=turn_question,
        manager="Manager One",
        technology="All",
        days=90,
        report_analysis_id="leader-147-usefulness",
        report_type="leader",
        data_as_of_utc=AS_OF,
        fact_fingerprint=FINGERPRINT,
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )


def _run(question: str, groups: list[dict]) -> dict:
    return grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        _request(question, groups),
        {"scope_type": "team", "report_analysis_id": "leader-147-usefulness"},
    )


def _risk_group(
    customer: str,
    value: float,
    *,
    row_value: float | None = None,
    state: str = "available",
    row_number: int = 2,
    include_proof: bool = True,
) -> dict:
    slug = customer.casefold().replace(" ", "_")
    proof = None
    if include_proof:
        proof = {
            "field": "Risk_Score_0_100",
            "value": value if row_value is None else row_value,
            "source_sheet": "Account_Summary",
            "source_row_number": row_number,
        }
    return {
        "evidence_key": f"summary.account.{slug}.risk_score",
        "label": f"{customer} — risk score",
        "evidence_type": "summary",
        "metric_value": value,
        "unit": "score (0–100)",
        "evidence_roles": ["derived_summary_row"],
        "source_state": state,
        "total_records": 1,
        "records": [{
            "source_sheet": "Account_Summary",
            "source_row_number": row_number,
            "record_id": f"summary.account.{slug}",
            "record_id_quality": "OK",
            "customer": customer,
            "title": "",
            "status": "MEDIUM",
            "date": "",
            "owner": "",
            "summary": f"Risk Band: MEDIUM | Risk Score 0 100: {row_value if row_value is not None else value}",
            "metric_value_evidence": proof,
        }],
        "limitations": [],
        "scope_label": "Manager One team",
        "truncated": False,
    }


def _action_group(
    key: str,
    records: list[dict],
    *,
    value: int | None = None,
    evidence_type: str = "action_plan",
) -> dict:
    return {
        "evidence_key": key,
        "label": key.replace("_", " "),
        "evidence_type": evidence_type,
        "metric_value": value,
        "unit": "records",
        "evidence_roles": ["supporting_record"],
        "source_state": "available",
        "total_records": len(records),
        "records": records,
        "limitations": [],
        "scope_label": "Manager One team",
        "truncated": False,
    }


def _action_row(
    record_id: str,
    *,
    status: str,
    owner: str,
    next_action: str,
    row_number: int,
) -> dict:
    return {
        "source_sheet": "Action_Plans",
        "source_row_number": row_number,
        "record_id": record_id,
        "record_id_quality": "OK",
        "customer": "Acme",
        "title": f"Plan {record_id}",
        "status": status,
        "date": "2026-08-10",
        "owner": owner,
        "summary": f"Next Action: {next_action}",
    }


def test_exact_non_count_scalar_is_asserted_from_independent_row_proof() -> None:
    result = _run("What is Acme's risk score?", [_risk_group("Acme", 54.2)])

    assert result["ok"] is True
    assert result["response_state"] == "ok"
    assert result["confidence"]["level"] == "High"
    assert "Verified value Acme — risk score: 54.2 score (0–100)" in result["answer"]
    assert result["canonical_headline"] == {
        "summary.account.acme.risk_score": 54.2,
    }
    assert result["retrieval_diag"]["direct_question_answered"] is True
    assert "RPT-SCALAR-" in result["answer"]
    assert "RPT-ROW-" in result["answer"]


def test_scalar_group_tamper_mismatch_fails_closed() -> None:
    result = _run(
        "What is Acme's risk score?",
        [_risk_group("Acme", 54.2, row_value=91.7)],
    )

    assert result["ok"] is False
    assert result["response_state"] == "validation_failed"
    assert result["reason"] == "unreconciled_report_scalar_metric"


@pytest.mark.parametrize("state", ["partial", "unavailable"])
def test_incomplete_scalar_source_withholds_value_and_confidence(state: str) -> None:
    result = _run(
        "What is Acme's risk score?",
        [_risk_group("Acme", 54.2, state=state)],
    )

    assert result["ok"] is True
    assert "Verified value" not in result["answer"]
    assert "54.2 score" not in result["answer"]
    assert result["canonical_headline"] == {}
    assert result["response_state"] == "partial"
    assert result["confidence"]["level"] != "High"
    assert result["retrieval_diag"]["direct_question_answered"] is False


def test_ambiguous_direct_scalar_withholds_all_values() -> None:
    result = _run(
        "What is the risk score?",
        [
            _risk_group("Acme", 54.2, row_number=2),
            _risk_group("Beta", 31.4, row_number=3),
        ],
    )

    assert result["ok"] is True
    assert "Multiple exact scalar values match" in result["answer"]
    assert "Verified value" not in result["answer"]
    assert result["canonical_headline"] == {}
    assert result["response_state"] == "partial"
    assert result["confidence"]["level"] != "High"


def test_direct_value_without_exact_scalar_is_not_high_confidence_success() -> None:
    group = _risk_group("Acme", 54.2, include_proof=False)
    result = _run("What is Acme's risk score?", [group])

    assert result["ok"] is True
    assert "not backed by a unique, complete exact-row scalar contract" in result["answer"]
    assert result["response_state"] == "partial"
    assert result["confidence"]["level"] != "High"
    assert result["retrieval_diag"]["direct_question_answered"] is False


def test_status_specific_record_question_excludes_other_status_rows() -> None:
    overdue = _action_row(
        "AP-OVERDUE", status="Overdue", owner="Alex", next_action="Call sponsor",
        row_number=2,
    )
    open_row = _action_row(
        "AP-OPEN", status="Open", owner="Blair", next_action="Prepare demo",
        row_number=3,
    )
    result = _run(
        "Show overdue action plan records.",
        [
            _action_group(
                "kpi.action_plans_overdue", [overdue], value=1,
                evidence_type="metric",
            ),
            _action_group(
                "kpi.action_plans_open", [open_row], value=1,
                evidence_type="metric",
            ),
            # The same underlying workbook row can support a KPI and a
            # recommendation. The answer must enumerate the record once.
            _action_group(
                "recommendation.overdue_ap", [overdue],
                evidence_type="recommendation",
            ),
        ],
    )

    assert result["ok"] is True
    assert "### Exact Source Records" in result["answer"]
    assert result["answer"].count("record AP-OVERDUE") == 1
    assert "record AP-OPEN" not in result["answer"]


def test_exact_frozen_report_uses_current_turn_not_prior_status_intent() -> None:
    overdue = _action_row(
        "AP-OVERDUE", status="Overdue", owner="Alex",
        next_action="Call sponsor", row_number=2,
    )
    completed = _action_row(
        "AP-COMPLETED", status="Completed", owner="Blair",
        next_action="Archive plan", row_number=3,
    )
    req = _request(
        (
            "Prior user: Show overdue action plan records.\n"
            "Current user: Show completed action plan records."
        ),
        [
            _action_group(
                "kpi.action_plans_overdue", [overdue], value=1,
                evidence_type="metric",
            ),
            _action_group(
                "kpi.action_plans_completed", [completed], value=1,
                evidence_type="metric",
            ),
        ],
        turn_question="Show completed action plan records.",
    )

    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        req,
        {"scope_type": "team", "report_analysis_id": "leader-147-usefulness"},
    )

    assert result["ok"] is True
    assert result["retrieval_diag"]["question_intent"]["status"] == "completed"
    assert "record AP-COMPLETED" in result["answer"]
    assert "record AP-OVERDUE" not in result["answer"]


def test_next_action_question_surfaces_only_matching_owner_rows() -> None:
    alex = _action_row(
        "AP-ALEX", status="Overdue", owner="Alex Rivera",
        next_action="Schedule the adoption workshop", row_number=2,
    )
    blair = _action_row(
        "AP-BLAIR", status="Open", owner="Blair Chen",
        next_action="Prepare the configuration review", row_number=3,
    )
    result = _run(
        "What should Alex do next?",
        [
            _action_group("recommendation.ap_alex", [alex], evidence_type="recommendation"),
            _action_group("recommendation.ap_blair", [blair], evidence_type="recommendation"),
        ],
    )

    assert result["ok"] is True
    assert "### Exact Next Actions" in result["answer"]
    assert "record AP-ALEX" in result["answer"]
    assert "Schedule the adoption workshop" in result["answer"]
    assert "record AP-BLAIR" not in result["answer"]


def test_suppressed_unsupported_claim_never_echoes_unverified_content() -> None:
    fabricated = "Fabrikam owes $9,991.17 under fabricated case CASE-99117."
    answer, rejected = grounded.compose_grounded_answer(
        {
            "executive_summary": "",
            "claims": [{"statement": fabricated, "citations": ["CASE-1"]}],
            "actions": [],
            "unknowns": [],
        },
        {"CASE-1"},
        evidence_records=[{
            "source_id": "CASE-1",
            "customer": "Acme",
            "text": "Case CASE-1 status Open.",
        }],
    )

    assert rejected == 1
    assert fabricated not in answer
    assert "Fabrikam" not in answer
    assert "9,991.17" not in answer
    assert "CASE-99117" not in answer
    assert "Unverified content was not repeated" in answer


def test_workbook_selector_prioritizes_customer_scalar_and_exact_field(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "AdoptIQ_Source_Data_AI_Usefulness.xlsx"
    facts = _facts()
    delivery.write_source_data_workbook(
        source_path,
        delivery.build_source_data_sheets(facts),
    )
    snapshot = workspace.load_workbook_snapshot(source_path)

    selected = workspace.select_report_bound_evidence(
        source_path,
        snapshot,
        "What is Acme Corporation's risk score?",
        max_keys=3,
        max_records=10,
    )

    group = selected["groups"][0]
    assert group["evidence_key"] == "summary.account.acme_corporation.risk_score"
    # Untagged portfolio incidents remain context-only and cannot be smeared
    # into an individual customer's canonical risk score.
    assert group["metric_value"] == 52
    assert group["records"][0]["metric_value_evidence"] == {
        "field": "Risk_Score_0_100",
        "value": 52,
        "source_sheet": "Account_Summary",
        "source_row_number": 2,
    }


def test_workbook_selector_prioritizes_status_and_decision_groups(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "AdoptIQ_Source_Data_AI_Actions.xlsx"
    facts = _facts()
    delivery.write_source_data_workbook(
        source_path,
        delivery.build_source_data_sheets(facts),
    )
    snapshot = workspace.load_workbook_snapshot(source_path)

    overdue = workspace.select_report_bound_evidence(
        source_path,
        snapshot,
        "Show overdue action plan records.",
        max_keys=4,
        max_records=20,
    )
    next_actions = workspace.select_report_bound_evidence(
        source_path,
        snapshot,
        "What should Alex do next?",
        max_keys=12,
        max_records=60,
    )

    assert overdue["groups"][0]["evidence_key"] == "kpi.action_plans_overdue"
    assert {row["status"] for row in overdue["groups"][0]["records"]} == {
        "Overdue",
    }
    action_rows = [
        row
        for group in next_actions["groups"]
        if group["evidence_type"] in {"action_plan", "recommendation"}
        for row in group["records"]
        if row["owner"] == "Alex Rivera" and "Next Action:" in row["summary"]
    ]
    assert action_rows
