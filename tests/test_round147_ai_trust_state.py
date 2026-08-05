"""Round 147 server-owned Ask AI trust-state contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ask_ai_grounded as grounded


def test_complete_verified_response_is_high_confidence() -> None:
    trust = grounded.build_ai_trust_state(
        source_states={"Action_Plans": "available", "TAC_Cases": "zero"},
        expected_sources=("Action_Plans", "TAC_Cases"),
        canonical_verified=True,
    )

    assert trust == {
        "response_state": "ok",
        "confidence": {
            "level": "High",
            "score": 100,
            "reasons": [
                "Declared sources are complete and canonical metric checks passed."
            ],
        },
    }


def test_partial_metric_list_or_missing_expected_source_cannot_be_high() -> None:
    partial_verification = grounded.build_ai_trust_state(
        source_states={"Action_Plans": "available", "TAC_Cases": "zero"},
        expected_sources=("Action_Plans", "TAC_Cases"),
        canonical_verified=["total_customers"],
    )
    missing_source = grounded.build_ai_trust_state(
        source_states={"Action_Plans": "available"},
        expected_sources=("Action_Plans", "TAC_Cases"),
        canonical_verified=True,
    )

    assert partial_verification["confidence"]["level"] != "High"
    assert missing_source["response_state"] == "partial"
    assert missing_source["confidence"]["level"] != "High"
    assert "Expected sources were not declared" in " ".join(
        missing_source["confidence"]["reasons"]
    )


@pytest.mark.parametrize(
    ("signals", "expected_state"),
    [
        ({"source_states": {"TAC_Cases": "failed"}}, "partial"),
        ({"source_states": {"TAC_Cases": "partial"}}, "partial"),
        ({"source_states": {"TAC_Cases": "stale"}}, "stale"),
        (
            {
                "source_states": {"TAC_Cases": "available"},
                "partial_warnings": [{"kind": "runtime", "dataset": "pulse"}],
            },
            "partial",
        ),
        (
            {
                "source_states": {"TAC_Cases": "available"},
                "evidence_truncated": True,
            },
            "partial",
        ),
        (
            {
                "source_states": {"TAC_Cases": "available"},
                "account_batch_truncated": True,
            },
            "partial",
        ),
    ],
)
def test_incomplete_stale_truncated_or_failed_response_is_never_high(
    signals: dict[str, object],
    expected_state: str,
) -> None:
    trust = grounded.build_ai_trust_state(
        canonical_verified=True,
        **signals,
    )

    assert trust["response_state"] == expected_state
    assert trust["confidence"]["level"] != "High"
    assert trust["confidence"]["score"] <= 79
    assert trust["confidence"]["reasons"]


def test_validation_failure_is_low_and_canonical_correction_is_not_high() -> None:
    rejected = grounded.build_ai_trust_state(
        source_states={"Action_Plans": "available"},
        canonical_verified=True,
        validation_failures=1,
    )
    corrected = grounded.build_ai_trust_state(
        source_states={"Action_Plans": "available"},
        canonical_verified=True,
        canonical_corrections=[{"kpi": "total_customers"}],
    )

    assert rejected["response_state"] == "validation_failed"
    assert rejected["confidence"]["level"] == "Low"
    assert rejected["confidence"]["score"] <= 39
    assert corrected["response_state"] == "ok"
    assert corrected["confidence"]["level"] == "Medium"


def _report_request(*, source_state: str = "available") -> grounded.AskAIRequest:
    bundle = {
        "schema": "report-bound-facts/v1",
        "canonical_snapshot": True,
        "analysis_id": "leader-147",
        "fact_fingerprint": "sha256:round147",
        "data_as_of_utc": "2026-08-04T12:00:00Z",
        "manager": "Manager One",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "scope_member": "",
        "source_states": {"Action_Plans": source_state},
        "decision_metrics": [
            {
                "metric_key": "kpi.action_plans_open",
                "label": "Open Action Plans",
                "value": 2,
                "display_value": "2",
                "unit": "records",
                "source_state": source_state,
                "source_sheet": "Action_Plans",
            }
        ],
        "action_plans": [],
        "accounts": [],
    }
    return grounded.AskAIRequest(
        question="How many action plans are open?",
        manager="Manager One",
        technology="All",
        days=90,
        report_analysis_id="leader-147",
        report_type="leader",
        data_as_of_utc="2026-08-04T12:00:00Z",
        fact_fingerprint="sha256:round147",
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )


def test_report_bound_answer_carries_matching_top_level_and_diagnostic_trust() -> None:
    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        _report_request(),
        {"scope_type": "team", "report_analysis_id": "leader-147"},
    )

    assert result["ok"] is True
    assert result["response_state"] == "partial"
    assert result["confidence"]["level"] == "Low"
    assert result["retrieval_diag"]["response_state"] == result["response_state"]
    assert result["retrieval_diag"]["confidence"] == result["confidence"]


def test_report_bound_unavailable_source_is_partial_and_never_high() -> None:
    result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
        _report_request(source_state="unavailable"),
        {"scope_type": "team", "report_analysis_id": "leader-147"},
    )

    assert result["response_state"] == "partial"
    assert result["confidence"]["level"] != "High"
    assert result["confidence"]["score"] <= 79


def test_ask_intel_success_carries_server_trust_state(
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
                    "id": "INC-147",
                    "status": "resolved",
                    "title": "Service recovered",
                    "impact_level": "minor",
                    "description": "Recovery completed.",
                    "published": "2026-08-04T10:00:00Z",
                }
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
                        "statement": "The service incident is resolved.",
                        "citations": ["INC-147"],
                    }
                ],
                "actions": [],
                "unknowns": [],
            },
        },
    )

    result = grounded.run_intel_grounded_ask_ai("What is the incident status?", 30)

    assert result["ok"] is True
    assert result["response_state"] == "ok"
    # Ask Intel has no canonical portfolio KPI check, so it is never inferred High.
    assert result["confidence"]["level"] == "Medium"
    assert result["confidence"]["score"] == 80


def test_confidence_browser_code_uses_only_server_contract() -> None:
    source = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "js"
        / "r95_confidence_band.js"
    ).read_text(encoding="utf-8")

    assert "diag.confidence" in source
    assert "payload.confidence" in source
    assert "legacy ungrounded responses are not server-scored" in source
    assert "canonical_corrections.length" not in source
    assert "risk_profiles_coverage" not in source
