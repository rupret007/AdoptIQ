"""Ask AI parity regressions for canonical risk and frozen-report intent."""

from __future__ import annotations

import json

import pandas as pd

import ask_ai_grounded as grounded
import decision_report_delivery as delivery


AS_OF = pd.Timestamp("2026-08-04T12:00:00Z")


def test_source_only_customers_are_all_risk_profiled() -> None:
    frames = {
        "subscriptions": pd.DataFrame([
            {"ACCOUNT_ID_C": "S-1", "BU_NAME": "Subscription Only"},
        ]),
        "action_plans": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "A-1",
                "BU_NAME": "Action Plan Only",
                "ID": "AP-1",
                "STATUS_C": "Open",
            },
        ]),
        "adoption_barriers": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "B-1",
                "BU_NAME": "Barrier Only",
                "ID": "AB-1",
                "SEVERITY_C": "High",
            },
        ]),
        "customer_pulse": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "P-1",
                "BU_NAME": "Pulse Only",
                "PULSE_RATING__C": "Poor",
            },
        ]),
        "tac_cases": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "T-1",
                "BU_NAME": "TAC Only",
                "CASE_ID": "CASE-1",
                "STATUS": "Open",
            },
        ]),
        "success_priorities": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "SP-1",
                "BU_NAME": "Success Priority Only",
                "ID": "SP-1",
            },
        ]),
    }

    profiles, identities, streaming = (
        grounded._build_ask_ai_canonical_risk_profiles(  # noqa: SLF001
            frames,
            days=90,
            as_of=AS_OF,
        )
    )

    expected = {
        "Subscription Only",
        "Action Plan Only",
        "Barrier Only",
        "Pulse Only",
        "TAC Only",
        "Success Priority Only",
    }
    assert streaming is False
    assert set(profiles) == expected
    assert {item["label"] for item in identities} == expected


def test_alias_collapse_order_incidents_and_as_of_match_report_builder() -> None:
    frames = {
        "subscriptions": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "001",
                "BU_NAME": "Legacy Brand",
                "SUBSCRIPTION_ID": "SUB-1",
            },
        ]),
        "action_plans": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "001",
                "BU_NAME": "New Brand",
                "ID": "AP-1",
                "STATUS_C": "Open",
            },
        ]),
        "adoption_barriers": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "001",
                "BU_NAME": "Brand Alias",
                "ID": "AB-1",
                "SEVERITY_C": "High",
                "AB_STATUS_C": "Open",
            },
        ]),
        "customer_pulse": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "001",
                "BU_NAME": "New Brand",
                "PULSE_RATING__C": "Poor",
            },
        ]),
        "tac_cases": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "001",
                "BU_NAME": "Brand Alias",
                "CASE_ID": "CASE-1",
                "STATUS": "Open",
                "SEVERITY": "P2",
            },
        ]),
        "success_priorities": pd.DataFrame([
            {
                "ACCOUNT_ID_C": "002",
                "BU_NAME": "Zulu Only",
                "ID": "SP-1",
            },
        ]),
    }
    incidents = [{
        "customer_name": "Legacy Brand",
        "status": "active",
        "impact_level": "High",
    }]

    ask_profiles, identities, streaming = (
        grounded._build_ask_ai_canonical_risk_profiles(  # noqa: SLF001
            frames,
            days=90,
            as_of=AS_OF,
            external_incidents=incidents,
        )
    )
    report_profiles = delivery._build_risk_profiles(  # noqa: SLF001
        frames,
        days=90,
        as_of=AS_OF,
        external_incidents=incidents,
    )

    assert streaming is False
    assert list(ask_profiles) == list(report_profiles) == [
        "Legacy Brand",
        "Zulu Only",
    ]
    assert ask_profiles == report_profiles
    assert identities[0]["account_ids"] == ("001",)
    assert identities[0]["exact_name_keys"] == (
        "brand alias",
        "legacy brand",
        "new brand",
    )
    assert ask_profiles["Legacy Brand"]["risk_as_of_utc"] == AS_OF.isoformat()
    assert (
        ask_profiles["Legacy Brand"]["components"]["incidents"]["details"]["count"]
        == 1
    )
    assert (
        ask_profiles["Zulu Only"]["components"]["incidents"]["details"]["count"]
        == 0
    )


def _legacy_report_request(*, question: str, turn_question: str) -> grounded.AskAIRequest:
    bundle = {
        "schema": "report-bound-facts/v1",
        "canonical_snapshot": True,
        "analysis_id": "leader-intent-v1",
        "fact_fingerprint": "sha256:intent-v1",
        "data_as_of_utc": AS_OF.isoformat(),
        "manager": "Manager One",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "scope_member": "",
        "source_states": {
            "Action_Plans": "available",
            "Account_Summary": "available",
        },
        "decision_metrics": [{
            "metric_key": "kpi.customers",
            "label": "Customers",
            "value": 1,
            "source_state": "available",
            "source_sheet": "Account_Summary",
        }],
        "action_plans": [{
            "record_id": "AP-PRIOR",
            "customer": "Acme",
            "title": "Prior-turn plan",
            "status": "Open",
            "owner": "Alex",
        }],
        "accounts": [{
            "customer": "Acme",
            "risk_band": "HIGH",
            "risk_score_0_100": 72,
            "open_action_plans": 1,
            "overdue_action_plans": 0,
        }],
    }
    return grounded.AskAIRequest(
        question=question,
        turn_question=turn_question,
        manager="Manager One",
        technology="All",
        days=90,
        report_analysis_id="leader-intent-v1",
        report_type="leader",
        data_as_of_utc=AS_OF.isoformat(),
        fact_fingerprint="sha256:intent-v1",
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )


def test_legacy_frozen_report_uses_current_turn_not_prior_intent() -> None:
    req = _legacy_report_request(
        question=(
            "Prior user: Show the action plans.\n"
            "Current user: Which customers are at risk?"
        ),
        turn_question="Which customers are at risk?",
    )

    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        req,
        {"scope_type": "team", "report_analysis_id": "leader-intent-v1"},
    )

    assert result["ok"] is True
    assert "Acme: risk band HIGH" in result["answer"]
    assert "Action Plan AP-PRIOR" not in result["answer"]
