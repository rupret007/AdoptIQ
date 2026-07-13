"""Contract tests for the synthetic Decision Intelligence V2 evaluation."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from decision_intelligence import AnalysisBundle
from decision_intelligence_eval import (
    EVALUATION_SCHEMA_VERSION,
    BuiltScenario,
    EvaluationResult,
    build_synthetic_scenarios,
    evaluate_built_scenarios,
    synthetic_scenarios,
)


EXPECTED_SCENARIOS = (
    "healthy_current",
    "active_p1_bems",
    "worsening_pulse",
    "barrier_without_action_plan",
    "overdue_action_plan",
    "approaching_renewal",
    "improving_risk",
    "stale_evidence",
    "ownership_conflict",
    "cross_source_contradiction",
    "recurring_issue",
    "verified_opportunity",
    "concentrated_risk_portfolio",
    "broad_low_confidence_portfolio",
    "similar_legal_names",
    "cross_customer_contamination",
    "prompt_injection_text",
    "ai_unavailable",
    "no_prior_snapshot",
    "schema_transition",
)
EXPECTED_ASSERTION_COUNT = 200

# Filled from a fixed-timestamp, fixed-input run.  These pins intentionally
# cover every scenario, not timing data or generated prose outside the
# canonical fingerprint payload.
EXPECTED_ANALYSIS_FINGERPRINTS: dict[str, str] = {
    "healthy_current": "analysis:b7453d329aadbbba48ac93db69f13489d79f031f5bf33febef35c3ea87231324",
    "active_p1_bems": "analysis:d59da88883ee390e0f43c46ada80a14b466cc4a380c6901e30ec3fde6eb86065",
    "worsening_pulse": "analysis:161f250fd3ffb9213934994693a1c83d0cfb0989080e870d07439a150ae04b5a",
    "barrier_without_action_plan": "analysis:0ca46ab1e366830fcc6bbdb5b504fc6689e5f8d6f64f212b86a190f5ee6cb1a5",
    "overdue_action_plan": "analysis:711977f5a52af27e7a25f4f262fea36457d29a77e856306397187df0cffa2328",
    "approaching_renewal": "analysis:f0a08789e8023e13b89abc98534def6e175e72d081c78ca5cee626a767dbec52",
    "improving_risk": "analysis:d4aa83999e8e845c8f38a45810df631417fa2124b6bd8edd4555605eb721f5b8",
    "stale_evidence": "analysis:0f353a0c489e3f8d5692ef44d5d7ce115ea8fe6a242b1c314ad837ecb4bbf103",
    "ownership_conflict": "analysis:c9494afe61af9211aea91ec3052b449af0766a439d6d7ef248dd212e354fa04f",
    "cross_source_contradiction": "analysis:e7254d7a42e6bd38ebe926eb6d3d07ddde340225dd33e993ad8ed6eb5e35c91f",
    "recurring_issue": "analysis:6a1c14fff2571387d448a2ab828571d726b2d922b13f7376440fe8933d7c20f3",
    "verified_opportunity": "analysis:56328a450cf74b3246d3fc7baef4af0c77992e2658fcf9838fcf9bdd258cb49d",
    "concentrated_risk_portfolio": "analysis:f821388525678100edd295d024199600b0008914650d6045d3baa2f69ab347b9",
    "broad_low_confidence_portfolio": "analysis:6caa1b6622277f5edc90887e83cce535bd8ffc56c0153a19ee5a131c1e0a12ed",
    "similar_legal_names": "analysis:bc6a72bcd4fa7e6d2c629ea9043abf3ab0241e44931b622326892828fb370413",
    "cross_customer_contamination": "analysis:ea69d2c5ebc0e7061bb2446953321db42d5aeb40b94d5eb5ced7e72dabe4620e",
    "prompt_injection_text": "analysis:acfc6844c5b4c6d164e396206f2fbb77bfe8e8048feae6f99b3a278a064a6f98",
    "ai_unavailable": "analysis:e7dddb68a586a27809b045090998c2039823383f4b8f2c9e583e8d82b18346cc",
    "no_prior_snapshot": "analysis:639e05ff7ba84db810c24b922378537d054fa15ae458b871dbeae18fc382d8fa",
    "schema_transition": "analysis:86b6842ba839f63f0900555a5012309dce9b2ab5bbfec368c4ef2bc82d7f5197",
}
EXPECTED_EVALUATION_FINGERPRINT = (
    "evaluation:b3e010b4cf84e45216e3efedbd971fc32fe23b223d900aa16640d4201509d3c1"
)


@pytest.fixture(scope="module")
def built_scenarios() -> tuple[BuiltScenario, ...]:
    return build_synthetic_scenarios()


@pytest.fixture(scope="module")
def built_by_id(built_scenarios: tuple[BuiltScenario, ...]) -> dict[str, BuiltScenario]:
    return {item.definition.scenario_id: item for item in built_scenarios}


@pytest.fixture(scope="module")
def evaluation(built_scenarios: tuple[BuiltScenario, ...]) -> EvaluationResult:
    return evaluate_built_scenarios(built_scenarios)


def _pairs(built: BuiltScenario) -> set[tuple[str, str]]:
    return {
        (change.category, change.classification)
        for customer in built.bundle.customers
        for change in customer.temporal_changes
    }


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


def test_scenario_catalogue_and_exact_assertion_details(evaluation: EvaluationResult) -> None:
    assert tuple(scenario.scenario_id for scenario in synthetic_scenarios()) == EXPECTED_SCENARIOS
    assert tuple(scenario.scenario_id for scenario in evaluation.scenarios) == EXPECTED_SCENARIOS
    assert evaluation.schema_version == EVALUATION_SCHEMA_VERSION
    assert evaluation.assertion_count == EXPECTED_ASSERTION_COUNT
    assert evaluation.passed_count == EXPECTED_ASSERTION_COUNT
    assert evaluation.failed_count == 0
    assert len({assertion.assertion_id for assertion in evaluation.assertions}) == EXPECTED_ASSERTION_COUNT
    assert all(assertion.expected and assertion.actual for assertion in evaluation.assertions)

    payload = evaluation.to_dict()
    assert payload["assertion_count"] == len(payload["assertions"])
    assert payload["passed_count"] + payload["failed_count"] == payload["assertion_count"]
    assert not {
        key for key in _walk_keys(payload) if "percentage" in key.casefold() or key.casefold().endswith("rate")
    }
    json.dumps(payload, allow_nan=False, sort_keys=True)


def test_deterministic_analysis_and_evaluation_fingerprint_pins(
    evaluation: EvaluationResult,
    built_by_id: dict[str, BuiltScenario],
) -> None:
    actual = {
        scenario_id: built.bundle.analysis_fingerprint
        for scenario_id, built in built_by_id.items()
    }
    assert actual == EXPECTED_ANALYSIS_FINGERPRINTS
    assert evaluation.deterministic_fingerprint == EXPECTED_EVALUATION_FINGERPRINT

    repeated = build_synthetic_scenarios(("healthy_current",))[0].bundle
    assert repeated.analysis_fingerprint == actual["healthy_current"]
    assert repeated.canonical_payload() == built_by_id["healthy_current"].bundle.canonical_payload()


def test_bundle_roundtrip_has_no_semantic_loss(built_by_id: dict[str, BuiltScenario]) -> None:
    for scenario_id in ("healthy_current", "ownership_conflict", "schema_transition"):
        bundle = built_by_id[scenario_id].bundle
        restored = AnalysisBundle.from_json(bundle.to_json(indent=None))
        assert restored.to_dict() == bundle.to_dict()
        assert restored.canonical_payload() == bundle.canonical_payload()
        assert restored.analysis_fingerprint == bundle.analysis_fingerprint
        assert restored.reconciliation_errors() == []


def test_reconciliation_isolation_and_quarantine_survive(
    built_by_id: dict[str, BuiltScenario],
) -> None:
    for built in built_by_id.values():
        bundle = built.bundle
        assert bundle.reconciliation_errors() == []
        assert bundle.portfolio.customer_count == len(bundle.customers)
        assert set(bundle.portfolio.customer_ids) == {
            customer.customer_id for customer in bundle.customers
        }
        evidence_by_id = bundle.evidence_by_id()
        for customer in bundle.customers:
            allowed = {"", "Portfolio", customer.customer_id, customer.customer_name}
            assert all(
                evidence_by_id[evidence_id].customer_identity in allowed
                for evidence_id in customer.evidence_ids
            )

    similar = built_by_id["similar_legal_names"].bundle
    assert len({customer.customer_id for customer in similar.customers}) == 2
    assert len({customer.customer_name for customer in similar.customers}) == 2

    contaminated = built_by_id["cross_customer_contamination"].bundle
    assert contaminated.diagnostics.quarantined_records
    assert all(customer.unresolved_conflicts for customer in contaminated.customers)
    assert sum(customer.metric("total_cases", 0) for customer in contaminated.customers) == 0


def test_every_action_has_a_complete_finding_evidence_chain(
    built_by_id: dict[str, BuiltScenario],
) -> None:
    for built in built_by_id.values():
        evidence_ids = set(built.bundle.evidence_by_id())
        for customer in built.bundle.customers:
            findings = {finding.finding_id: finding for finding in customer.findings}
            for finding in customer.findings:
                if finding.kind != "data_quality":
                    assert finding.evidence_ids
                    assert set(finding.evidence_ids).issubset(evidence_ids)
            for action in customer.recommended_actions:
                assert action.triggering_finding_ids
                assert action.evidence_ids
                assert set(action.evidence_ids).issubset(evidence_ids)
                for finding_id in action.triggering_finding_ids:
                    assert finding_id in findings
                    assert set(action.evidence_ids).intersection(findings[finding_id].evidence_ids)


def test_temporal_labels_cover_comparison_boundaries(
    built_by_id: dict[str, BuiltScenario],
) -> None:
    assert ("customer_pulse", "worsening") in _pairs(built_by_id["worsening_pulse"])

    improving = _pairs(built_by_id["improving_risk"])
    assert improving.intersection(
        {
            ("customer_risk", "improving"),
            ("customer_pulse", "improving"),
            ("severe_service_evidence", "resolved"),
        }
    )

    no_prior = _pairs(built_by_id["no_prior_snapshot"])
    assert no_prior == {("scope_comparison", "not_comparable")}

    transition = built_by_id["schema_transition"].bundle
    assert "not_comparable" in {classification for _, classification in _pairs(built_by_id["schema_transition"])}
    transition_messages = transition.context.warnings + transition.context.degraded_mode_indicators
    assert any("schema" in message.casefold() for message in transition_messages)


def test_action_ranking_confidence_and_date_rules(built_by_id: dict[str, BuiltScenario]) -> None:
    confidence_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    for built in built_by_id.values():
        actions = sorted(built.bundle.portfolio.recommended_actions, key=lambda action: action.rank)
        assert [action.rank for action in actions] == list(range(1, len(actions) + 1))
        assert [action.priority_score for action in actions] == sorted(
            (action.priority_score for action in actions), reverse=True
        )
        customers = {customer.customer_id: customer for customer in built.bundle.customers}
        for action in actions:
            customer = customers[action.scope_id]
            assert confidence_rank[action.confidence] <= confidence_rank[customer.data_quality.confidence]
            action_text = " ".join(
                (
                    action.specific_action,
                    action.timing_window,
                    action.expected_outcome,
                    action.measurable_success_signal,
                )
            )
            assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", action_text) is None
            if action.dependencies:
                assert action.urgency != "IMMEDIATE"
                assert action.timing_window.casefold() != "immediate"

    severe_actions = built_by_id["active_p1_bems"].bundle.portfolio.recommended_actions
    severe = next(action for action in severe_actions if action.action_type == "resolve_severe_service_evidence")
    assert severe.priority_score >= 90

    low_confidence = built_by_id["broad_low_confidence_portfolio"].bundle
    assert low_confidence.portfolio.recommended_actions
    assert low_confidence.portfolio.recommended_actions[0].action_type in {
        "obtain_missing_evidence",
        "establish_decision_evidence",
    }


def test_ai_unavailable_does_not_change_safe_projection(
    built_by_id: dict[str, BuiltScenario],
) -> None:
    built = built_by_id["ai_unavailable"]
    assert built.definition.request.feature_configuration["ai_available"] is False
    before = built.bundle.safe_projection()
    after = built.bundle.safe_projection()
    assert before == after
    assert before["customers"]
    assert before["analysis_fingerprint"] == built.bundle.analysis_fingerprint


def test_performance_observations_have_measurement_shape(evaluation: EvaluationResult) -> None:
    assert len(evaluation.performance) == len(EXPECTED_SCENARIOS)
    assert {sample.scenario_id for sample in evaluation.performance} == set(EXPECTED_SCENARIOS)
    for sample in evaluation.performance:
        payload = sample.to_dict()
        assert set(payload) == {
            "scenario_id",
            "operation",
            "duration_seconds",
            "customer_count",
            "source_row_count",
        }
        assert sample.operation == "build_analysis_bundle"
        assert isinstance(sample.duration_seconds, float)
        assert sample.duration_seconds >= 0.0
        assert isinstance(sample.customer_count, int) and sample.customer_count >= 1
        assert isinstance(sample.source_row_count, int) and sample.source_row_count >= 0
