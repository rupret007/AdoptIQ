"""Round 147 completion tests for exact report and AI failure contracts."""

from __future__ import annotations

import json

import pandas as pd
import pytest

import ask_ai_grounded as grounded
from tests.ask_ai_eval import predicates
from tests.ask_ai_eval.runner import PortfolioBundle, Question, evaluate_question


AS_OF = "2026-08-04T12:00:00Z"
FINGERPRINT = "sha256:round147-exact"


def _report_request(
    *,
    question: str = "How many action plans are open?",
    groups: list[dict] | None = None,
) -> grounded.AskAIRequest:
    bundle = {
        "schema": "report-bound-facts/v2",
        "canonical_snapshot": True,
        "analysis_id": "leader-147-exact",
        "fact_fingerprint": FINGERPRINT,
        "data_as_of_utc": AS_OF,
        "data_as_of_state": "available",
        "manager": "Manager One",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "scope_member": "",
        "source_states": {"Action_Plans": "available"},
        # Deliberately hostile projection: v2 must ignore this value.
        "decision_metrics": [{"metric_key": "projected.metric", "value": 999}],
        "evidence_contract": "canonical-evidence-links/v1",
        "exact_evidence": {
            "schema": "report-bound-evidence/v1",
            "evidence_contract": "canonical-evidence-links/v1",
            "fact_fingerprint": FINGERPRINT,
            "data_as_of_utc": AS_OF,
            "truncated": False,
            "groups": groups or [],
        },
        "action_plans": [{"record_id": "PROJECTED-1"}],
        "accounts": [{"customer": "Projected Customer"}],
    }
    return grounded.AskAIRequest(
        question=question,
        manager="Manager One",
        technology="All",
        days=90,
        report_analysis_id="leader-147-exact",
        report_type="leader",
        data_as_of_utc=AS_OF,
        # Round 169: keep this zero/positive exact-evidence fixture inside the
        # report freshness window without depending on the wall clock.
        evaluation_utc="2026-08-05T12:00:00Z",
        fact_fingerprint=FINGERPRINT,
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )


def _metric_group(value: int = 2, *, include_rows: bool = True) -> dict:
    records = [
        {
            "source_sheet": "Action_Plans",
            "source_row_number": 2,
            "record_id": "AP-001",
            "record_id_quality": "OK",
            "customer": "Acme",
            "title": "Confirm adoption owner",
            "status": "Open",
            "date": "2026-08-10",
            "owner": "Alex",
            "summary": "Next action: Schedule workshop",
        },
        {
            "source_sheet": "Action_Plans",
            "source_row_number": 3,
            "record_id": "AP-002",
            "record_id_quality": "OK",
            "customer": "Beta",
            "title": "Resolve rollout blocker",
            "status": "Open",
            "date": "2026-08-12",
            "owner": "Blair",
            "summary": "Next action: Validate configuration",
        },
    ] if include_rows else []
    return {
        "evidence_key": "kpi.action_plans_open",
        "label": "Open Action Plans",
        "evidence_type": "metric",
        "metric_value": value,
        "unit": "records",
        "evidence_roles": ["supporting_record"] if value else ["zero_state"],
        "source_state": "available" if value else "zero",
        "total_records": value,
        "records": records,
        "limitations": [],
        "scope_label": "Manager One team",
    }


def _run_report(req: grounded.AskAIRequest) -> dict:
    return grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        req,
        {"scope_type": "team", "report_analysis_id": "leader-147-exact"},
    )


def test_v2_metric_question_answers_reconciled_value_without_row_dump() -> None:
    result = _run_report(_report_request(groups=[_metric_group()]))

    assert result["ok"] is True
    assert "Verified metric Open Action Plans: 2 records" in result["answer"]
    assert "### Exact Source Records" not in result["answer"]
    assert "PROJECTED-1" not in result["answer"]
    assert "999" not in result["answer"]
    assert result["canonical_headline"] == {"kpi.action_plans_open": 2}
    assert any(
        record["source_type"] == "FrozenReportMetric"
        and record["source_id"].startswith("RPT-METRIC-")
        for record in result["evidence_records"]
    )
    assert sum(
        record["source_type"] == "FrozenReportExactRow"
        for record in result["evidence_records"]
    ) == 2


def test_v2_zero_metric_is_explicit_supported_finding_not_no_data() -> None:
    result = _run_report(
        _report_request(groups=[_metric_group(value=0, include_rows=False)])
    )

    assert result["ok"] is True
    assert result["response_state"] == "ok"
    assert "Verified metric Open Action Plans: 0 records" in result["answer"]
    assert result["canonical_headline"] == {"kpi.action_plans_open": 0}
    assert result["evidence_records_total"] == 1


def test_v2_record_deep_dive_enumerates_exact_rows_after_metric() -> None:
    result = _run_report(
        _report_request(
            question="Show the actual records and details for open action plans.",
            groups=[_metric_group()],
        )
    )

    assert result["ok"] is True
    assert result["answer"].index("Verified metric") < result["answer"].index(
        "### Exact Source Records"
    )
    assert "record AP-001" in result["answer"]
    assert "record AP-002" in result["answer"]


def test_v2_positive_metric_mismatch_fails_closed() -> None:
    group = _metric_group()
    group["total_records"] = 1

    result = _run_report(_report_request(groups=[group]))

    assert result["ok"] is False
    assert result["response_state"] == "validation_failed"
    assert result["reason"] == "unreconciled_report_positive_metric"


def test_v2_empty_exact_groups_returns_true_no_data() -> None:
    result = _run_report(_report_request(groups=[]))

    assert result["ok"] is True
    assert result["response_state"] == "no_data"
    assert result["evidence_records"] == []


def test_duplicate_source_id_cannot_merge_conflicting_rows() -> None:
    payload = {
        "executive_summary": "",
        "claims": [{
            "statement": "Acme support case is closed.",
            "citations": ["CASE-1"],
        }],
        "actions": [],
        "unknowns": [],
    }
    records = [
        {"source_id": "CASE-1", "customer": "Acme", "text": "Status: Open"},
        {"source_id": "CASE-1", "customer": "Beta", "text": "Status: Closed"},
    ]

    answer, rejected = grounded.compose_grounded_answer(
        payload,
        {"CASE-1"},
        evidence_records=records,
    )

    assert rejected >= 1
    assert "[Sources: CASE-1]" not in answer
    assert "Suppressed claim" in answer


def test_portfolio_pipeline_fails_closed_before_entailment_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conflicting normalized IDs must be unusable in the real pipeline."""

    import adoptiq_backend
    import incident_storage

    class Connection:
        def close(self) -> None:
            return None

    subscriptions = pd.DataFrame([{
        "ACCOUNT_ID_C": "A-1",
        "SUBSCRIPTION_ID": "SUB-1",
        "BU_NAME": "Acme",
        "CSSM_EMAIL": "a@example.com",
    }])
    conflicting_cases = pd.DataFrame([
        {
            "CASE_ID": "CASE-1",
            "ACCOUNT_ID": "A-1",
            "BU_NAME": "Acme",
            "SUBJECT": "Support case account issue",
            "STATUS": "Open",
        },
        {
            "CASE_ID": "case-1",
            "ACCOUNT_ID": "A-2",
            "BU_NAME": "Beta",
            "SUBJECT": "Support case account issue",
            "STATUS": "Closed",
        },
    ])

    monkeypatch.setattr(
        adoptiq_backend,
        "TEAM_ROSTER",
        (("Manager One", "Alex", "a@example.com"),),
    )
    monkeypatch.setattr(
        adoptiq_backend, "_connect_with_keeper", lambda: Connection()
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "get_subscriptions_for_team",
        lambda *_args, **_kwargs: subscriptions,
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "compute_barrier_aging",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "scan_historical_reports",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "build_cross_report_trends",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        incident_storage,
        "get_all_external_intel",
        lambda **_kwargs: {
            "incidents": [],
            "maintenances": [],
            "bugs": [],
            "fetch_errors": {},
            "source_states": {},
            "list_truncated": {},
        },
    )

    def fake_prefetch(_run_ctx, *, include_datasets):
        bundle = {
            name: pd.DataFrame()
            for name in include_datasets
        }
        bundle["support_cases_snowflake"] = conflicting_cases.copy()
        return bundle

    captured_prompt = []

    def fake_model(_system_prompt, user_prompt, _schema, **_kwargs):
        captured_prompt.append(user_prompt)
        return {
            "ok": True,
            "data": {
                "executive_summary": "",
                "claims": [{
                    "statement": "Acme support case CASE-1 is closed.",
                    "citations": ["CASE-1"],
                }],
                "actions": [],
                "unknowns": [],
            },
        }

    monkeypatch.setattr(grounded, "prefetch_ask_ai_grounded", fake_prefetch)
    monkeypatch.setattr(
        adoptiq_backend, "generate_llm_json_response", fake_model
    )

    result = grounded.run_portfolio_grounded_ask_ai(grounded.AskAIRequest(
        question="Find support case CASE-1.",
        manager="Manager One",
        technology="All",
        days=90,
    ))

    assert result["ok"] is True
    assert result["response_state"] == "validation_failed"
    assert "[Sources: CASE-1]" not in result["answer"]
    assert all(
        grounded._normalize_claim_id(record["source_id"]) != "CASE-1"  # noqa: SLF001
        for record in result["evidence_records"]
    )
    whitelist = captured_prompt[0].split("Citation whitelist (must use exactly):", 1)[1]
    whitelist = whitelist.split("\n", 1)[0]
    assert "CASE-1" not in whitelist


def test_subscription_counts_remain_grounded_without_account_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adoptiq_backend

    class Connection:
        def close(self) -> None:
            return None

    subscriptions = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-1", "BU_NAME": "Acme", "CSSM_EMAIL": "a@example.com"},
        {"SUBSCRIPTION_ID": "SUB-2", "BU_NAME": "Beta", "CSSM_EMAIL": "a@example.com"},
    ])
    monkeypatch.setattr(
        adoptiq_backend,
        "TEAM_ROSTER",
        (("Manager One", "Alex", "a@example.com"),),
    )
    monkeypatch.setattr(adoptiq_backend, "_connect_with_keeper", lambda: Connection())
    monkeypatch.setattr(
        adoptiq_backend,
        "get_subscriptions_for_team",
        lambda *_args, **_kwargs: subscriptions,
    )

    result = grounded.run_portfolio_grounded_ask_ai(grounded.AskAIRequest(
        question="How many subscriptions are in scope?",
        manager="Manager One",
        technology="All",
        days=90,
    ))

    assert result["ok"] is True
    assert result["response_state"] == "partial"
    assert result["canonical_headline"]["total_subscriptions"] == 2
    assert "Canonical total subscriptions: 2" in result["answer"]
    assert result["retrieval_diag"]["method"] == "scoped_subscription_aggregates"


def test_trend_aggregates_become_stable_scoped_evidence() -> None:
    binding = grounded.AskAIContextBinding(
        manager="Manager One",
        technology="All",
        days=90,
        scope_type="team",
    )
    records, states = grounded._r147_trend_evidence_records(  # noqa: SLF001
        {
            "period_comparison": {"current": {"barriers": 5}, "previous": {"barriers": 3}},
            "barrier_velocity": {"opened": 4, "closed": 2},
            "cross_report_trends": {
                "_source_state": "unsupported_scope",
                "reason": "individual history is not addressable",
            },
        },
        scope_binding=binding,
        timestamp=AS_OF,
        include_cross_report=True,
    )

    assert states == {
        "period_comparison": "available",
        "barrier_velocity": "available",
        "cross_report_trends": "unsupported_scope",
    }
    assert len({record.source_id for record in records}) == len(records)
    assert all("Scope: team Manager One" in record.text for record in records)
    assert any("unsupported_scope" in record.text for record in records)


def test_intel_clean_empty_and_failures_use_common_response_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adoptiq_backend
    import incident_storage

    model_called = False

    def model(*_args, **_kwargs):
        nonlocal model_called
        model_called = True
        return {"ok": False, "error": "offline"}

    monkeypatch.setattr(adoptiq_backend, "generate_llm_json_response", model)
    monkeypatch.setattr(
        incident_storage,
        "get_all_external_intel",
        lambda **_kwargs: {
            "incidents": [], "maintenances": [], "bugs": [],
            "fetch_errors": {}, "source_states": {}, "list_truncated": {},
        },
    )
    empty = grounded.run_intel_grounded_ask_ai("Any incidents?", 30)
    assert empty["ok"] is True
    assert empty["response_state"] == "no_data"
    assert model_called is False
    assert empty["data_as_of_utc"] == empty["scope_context"]["data_as_of_utc"]

    monkeypatch.setattr(
        incident_storage,
        "get_all_external_intel",
        lambda **_kwargs: {
            "incidents": [], "maintenances": [], "bugs": [],
            "fetch_errors": {"status": "timeout"},
            "source_states": {"status": "failed"}, "list_truncated": {},
        },
    )
    failed = grounded.run_intel_grounded_ask_ai("Any incidents?", 30)
    assert failed["ok"] is False
    assert failed["response_state"] == "retrieval_failed"
    assert failed["confidence"]["level"] == "Low"
    assert failed["retrieval_diag"]["response_state"] == "retrieval_failed"


def test_eval_requires_entailing_row_and_rendered_citation() -> None:
    bundle = PortfolioBundle(
        portfolio_id="adversarial",
        adoption_barriers=pd.DataFrame(),
        support_cases=pd.DataFrame([
            {"CASE_ID": "CASE-1", "BU_NAME": "Acme", "SUBJECT": "Rollout", "STATUS": "Open"},
            {"CASE_ID": "CASE-1", "BU_NAME": "Beta", "SUBJECT": "Upgrade", "STATUS": "Closed"},
        ]),
        customer_pulse=pd.DataFrame(),
        success_priorities=pd.DataFrame(),
        action_plans=pd.DataFrame(),
    )
    question = Question(
        id="adversarial-duplicate-id",
        portfolio="adversarial",
        category="grounding",
        question="Is Acme case CASE-1 closed?",
        predicates=[{"type": "must_cite_source_id", "expected_id": "CASE-1"}],
    )

    class Client:
        def call(self, *_args, **_kwargs):
            return {
                "executive_summary": "",
                "claims": [{
                    "statement": "Acme support case CASE-1 is closed.",
                    "citations": ["CASE-1"],
                }],
                "actions": [],
                "unknowns": [],
            }

    result = evaluate_question(question, bundle, Client())  # type: ignore[arg-type]

    assert result.passed is False
    assert result.rejected_count >= 1
    assert result.predicate_results[0]["passed"] is False
    allowed_only, _ = predicates.must_cite_source_id(
        "No citation was rendered.",
        "CASE-1",
        sources_seen={"CASE-1"},
    )
    assert allowed_only is False
