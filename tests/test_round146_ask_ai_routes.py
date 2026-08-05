"""Round 146 report-bound Ask AI Flask contracts."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from dataclasses import asdict
from importlib.machinery import ModuleSpec

import pytest


# Route tests exercise server-owned bindings only and never need Snowflake.
if importlib.util.find_spec("snowflake") is None:
    snowflake_package = types.ModuleType("snowflake")
    snowflake_package.__path__ = []  # type: ignore[attr-defined]
    snowflake_package.__spec__ = ModuleSpec("snowflake", loader=None, is_package=True)
    snowflake_connector = types.ModuleType("snowflake.connector")
    snowflake_connector.__spec__ = ModuleSpec(
        "snowflake.connector", loader=None
    )
    snowflake_connector.DictCursor = object
    snowflake_connector.connect = lambda *_args, **_kwargs: None
    snowflake_package.connector = snowflake_connector
    sys.modules["snowflake"] = snowflake_package
    sys.modules["snowflake.connector"] = snowflake_connector


def _scope_context(request) -> dict:
    return {
        "manager": request.manager,
        "technology": request.technology,
        "days": request.days,
        "scope_type": request.scope_type,
        "scope_value": request.scope_value,
        "scope_member": request.scope_member,
        "report_analysis_id": request.report_analysis_id,
        "report_type": request.report_type,
        "data_as_of_utc": request.data_as_of_utc,
        "fact_fingerprint": request.fact_fingerprint,
    }


def _grounded_success(request) -> dict:
    return {
        "ok": True,
        "answer": "Bound answer [AP-1]",
        "context_summary": "Bound report facts",
        "retrieval_diag": {"method": "round146-test"},
        "evidence_records": [],
        "evidence_index": [],
        "scope_context": _scope_context(request),
        "response_state": "partial",
        "confidence": {
            "level": "Medium",
            "score": 72,
            "reasons": ["One optional source was unavailable."],
        },
    }


def _parse_sse(body: str) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        event_name = ""
        payload = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        if event_name and isinstance(payload, dict):
            events.setdefault(event_name, []).append(payload)
    return events


@pytest.fixture
def ask_ai_test_state(monkeypatch):
    import app_simple
    import manager_decision_workspace as decision_workspace

    with app_simple._ask_ai_rate_lock:
        app_simple._ask_ai_rate_log.clear()
    monkeypatch.setattr(app_simple, "is_grounded_ask_ai_enabled", lambda: True)
    monkeypatch.setattr(app_simple, "_record_ask_ai_query_diag", lambda *_args: None)
    real_binding = decision_workspace.ask_ai_binding

    def exact_test_binding(snapshot):
        binding = real_binding(snapshot)
        binding.update({
            "evidence_available": True,
            "evidence_contract": "canonical-evidence-links/v1",
            "evidence_manifest": [{
                "evidence_key": "kpi.action_plans_open",
                "evidence_type": "metric",
                "label": "Open Action Plans",
                "total_records": 0,
            }],
        })
        return binding

    monkeypatch.setattr(decision_workspace, "ask_ai_binding", exact_test_binding)
    monkeypatch.setattr(
        decision_workspace,
        "select_report_bound_evidence",
        lambda _path, snapshot, _question, **_kwargs: {
            "schema": "report-bound-evidence/v1",
            "evidence_contract": "canonical-evidence-links/v1",
            "fact_fingerprint": snapshot.get("fact_fingerprint", ""),
            "data_as_of_utc": snapshot.get("data_as_of_utc", ""),
            "selected_key_count": 1,
            "manifest_key_count": 1,
            "returned_record_count": 0,
            "selected_record_count": 0,
            "truncated": False,
            "groups": [{
                "evidence_key": "kpi.action_plans_open",
                "label": "Open Action Plans",
                "evidence_type": "metric",
                "source_state": "zero",
                "total_records": 0,
                "records": [],
                "limitations": ["Explicit zero-state derivation."],
                "data_as_of_utc": snapshot.get("data_as_of_utc", ""),
                "fact_fingerprint": snapshot.get("fact_fingerprint", ""),
            }],
        },
    )
    monkeypatch.setattr(app_simple, "_r146_status_for_workspace", lambda _aid: {})
    monkeypatch.setattr(
        app_simple, "_r146_workspace_artifact", lambda _status, kind: "/tmp/test.xlsx" if kind == "excel" else None
    )
    artifact_hash = "a" * 64
    monkeypatch.setattr(
        app_simple,
        "_r146_persisted_excel_hash",
        lambda *_args: artifact_hash,
    )
    monkeypatch.setattr(
        app_simple,
        "_r146_file_sha256",
        lambda *_args: artifact_hash,
    )
    return app_simple


def test_report_id_overrides_every_client_scope_field(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    manager, _member_name, member_email = app_simple.TEAM_ROSTER[0]
    analysis_id = "round146-bound-member"
    snapshot = {
        "analysis_id": analysis_id,
        "report_type": "leader",
        "manager": manager,
        "technology": "Webex Calling",
        "days": 120,
        "scope_type": "member",
        "scope_value": member_email,
        "scope_member": member_email,
        "data_as_of_utc": "2026-08-03T12:00:00Z",
        "fact_fingerprint": "sha256:server-owned",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda requested: (snapshot, 200) if requested == analysis_id else (None, 404),
    )
    captured = []

    def fake_grounded(request):
        captured.append(request)
        return _grounded_success(request)

    monkeypatch.setattr(app_simple, "run_portfolio_grounded_ask_ai", fake_grounded)
    monkeypatch.setattr(
        app_simple,
        "_r74_generate_follow_up_suggestions",
        lambda **_kwargs: ["Same scoped follow-up"],
    )

    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What needs attention?",
            "report_analysis_id": analysis_id,
            "manager": "Hostile Manager",
            "technology": "Hostile Technology",
            "days": 1,
            "scope_type": "customer",
            "scope_value": "Outside Customer",
            "scope_member": "outside@example.com",
            "report_type": "compact",
            "data_as_of_utc": "1900-01-01T00:00:00Z",
            "fact_fingerprint": "client-fingerprint",
        },
    )

    assert response.status_code == 200
    assert len(captured) == 1
    request = captured[0]
    assert request.manager == manager
    assert request.technology == "Webex Calling"
    assert request.days == 120
    assert request.scope_type == "member"
    assert request.scope_value == member_email
    assert request.scope_member == member_email
    assert request.report_analysis_id == analysis_id
    assert request.report_type == "leader"
    assert request.data_as_of_utc == "2026-08-03T12:00:00Z"
    assert request.fact_fingerprint == "sha256:server-owned"
    assert response.get_json()["scope_context"] == _scope_context(request)


def test_sync_report_bound_request_fails_closed_when_grounded_ai_is_disabled(
    client, monkeypatch, ask_ai_test_state
) -> None:
    """A frozen report must never fall through to the live legacy path."""

    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    analysis_id = "round147-grounded-disabled"
    snapshot = {
        "analysis_id": analysis_id,
        "canonical_snapshot": True,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "data_as_of_utc": "2026-08-04T12:00:00Z",
        "fact_fingerprint": "sha256:grounded-disabled",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda requested: (snapshot, 200)
        if requested == analysis_id
        else (None, 404),
    )
    monkeypatch.setattr(app_simple, "is_grounded_ask_ai_enabled", lambda: False)
    grounded_calls = []
    monkeypatch.setattr(
        app_simple,
        "run_portfolio_grounded_ask_ai",
        lambda request: grounded_calls.append(request) or _grounded_success(request),
    )

    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What needs attention?",
            "report_analysis_id": analysis_id,
        },
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["mode"] == "grounded"
    assert payload["response_state"] == "model_unavailable"
    assert payload["reason"] == "grounded_ai_disabled"
    assert payload["fallback_available"] is False
    assert payload["retrieval_diag"] == {"method": "disabled"}
    assert payload["scope_context"]["report_analysis_id"] == analysis_id
    assert "no live or legacy fallback" in payload["error"].lower()
    assert grounded_calls == []


@pytest.mark.parametrize(
    ("persisted_hash", "artifact_hash"),
    [
        ("", "a" * 64),
        ("not-a-sha256", "a" * 64),
        ("a" * 64, "b" * 64),
    ],
    ids=("missing", "malformed", "mismatch"),
)
def test_report_bound_ask_ai_requires_matching_persisted_artifact_hash(
    client,
    monkeypatch,
    ask_ai_test_state,
    persisted_hash: str,
    artifact_hash: str,
) -> None:
    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    analysis_id = "round147-artifact-proof"
    snapshot = {
        "analysis_id": analysis_id,
        "canonical_snapshot": True,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "data_as_of_utc": "2026-08-04T12:00:00Z",
        "fact_fingerprint": "sha256:artifact-proof",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda requested: (snapshot, 200) if requested == analysis_id else (None, 404),
    )
    monkeypatch.setattr(
        app_simple,
        "_r146_persisted_excel_hash",
        lambda *_args: persisted_hash,
    )
    monkeypatch.setattr(
        app_simple,
        "_r146_file_sha256",
        lambda *_args: artifact_hash,
    )
    grounded_calls = []
    monkeypatch.setattr(
        app_simple,
        "run_portfolio_grounded_ask_ai",
        lambda request: grounded_calls.append(request) or _grounded_success(request),
    )

    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What needs attention?",
            "report_analysis_id": analysis_id,
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["response_state"] == "validation_failed"
    assert "persisted artifact integrity proof" in payload["error"].lower()
    assert grounded_calls == []


def test_same_origin_report_page_retains_scope_for_followup_post(
    client, monkeypatch, ask_ai_test_state
) -> None:
    """Existing Ask AI JS can retain the report through its page Referer."""

    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    analysis_id = "round146-referer-followup"
    snapshot = {
        "analysis_id": analysis_id,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "customer",
        "scope_value": "Acme Corp",
        "fact_fingerprint": "sha256:referer-bound",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda requested: (snapshot, 200) if requested == analysis_id else (None, 404),
    )
    captured = []

    def fake_grounded(request):
        captured.append(request)
        return _grounded_success(request)

    monkeypatch.setattr(app_simple, "run_portfolio_grounded_ask_ai", fake_grounded)
    monkeypatch.setattr(
        app_simple,
        "_r74_generate_follow_up_suggestions",
        lambda **_kwargs: ["Same-customer follow-up"],
    )
    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What should I ask next?",
            "manager": "Ignored Client Manager",
        },
        headers={
            "Referer": f"http://localhost/ask-ai?report_analysis_id={analysis_id}"
        },
    )

    assert response.status_code == 200
    assert captured[0].report_analysis_id == analysis_id
    assert captured[0].scope_type == "customer"
    assert captured[0].scope_value == "Acme Corp"
    assert response.get_json()["scope_context"]["fact_fingerprint"] == (
        "sha256:referer-bound"
    )


def test_report_binding_rejects_member_from_another_manager(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    outside = next(
        (row for row in app_simple.TEAM_ROSTER if row[0] != manager),
        None,
    )
    if outside is None:
        pytest.skip("The configured roster has only one manager.")
    analysis_id = "round146-cross-scope"
    snapshot = {
        "analysis_id": analysis_id,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "member",
        "scope_value": outside[2],
        "scope_member": outside[2],
        "fact_fingerprint": "sha256:cross-scope",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda _requested: (snapshot, 200),
    )

    from ask_ai_grounded import validate_ask_ai_scope_request

    def validate_only(request):
        try:
            validate_ask_ai_scope_request(request, app_simple.TEAM_ROSTER)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "status_code": 400}
        raise AssertionError("Cross-manager member scope was accepted")

    monkeypatch.setattr(app_simple, "run_portfolio_grounded_ask_ai", validate_only)
    request_body = {
        "question": "Show this member's priorities.",
        "report_analysis_id": analysis_id,
        "allow_legacy_fallback": True,
    }
    response = client.post(
        "/api/ask-ai-portfolio",
        json=request_body,
    )
    stream_response = client.post(
        "/api/ask-ai-portfolio/stream",
        json=request_body,
    )

    assert response.status_code == 400
    assert stream_response.status_code == 400
    payload = response.get_json()
    stream_payload = stream_response.get_json()
    assert payload["ok"] is False
    assert "selected manager" in payload["error"].lower()
    assert payload["scope_context"]["report_analysis_id"] == analysis_id
    assert stream_payload["scope_context"] == payload["scope_context"]


def test_sync_stream_and_followups_retain_identical_report_scope(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    analysis_id = "round146-bound-customer"
    snapshot = {
        "analysis_id": analysis_id,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "customer",
        "scope_value": "Acme Corp",
        "scope_member": "",
        "data_as_of_utc": "2026-08-03T15:30:00Z",
        "fact_fingerprint": "sha256:customer-report",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda _requested: (snapshot, 200),
    )
    requests = []

    def fake_grounded(request):
        requests.append(request)
        return _grounded_success(request)

    follow_up_scopes = []

    def fake_followups(**kwargs):
        follow_up_scopes.append(dict(kwargs["scope_filters"]))
        return ["What changed in this same customer scope?"]

    monkeypatch.setattr(app_simple, "run_portfolio_grounded_ask_ai", fake_grounded)
    monkeypatch.setattr(
        app_simple, "_r74_generate_follow_up_suggestions", fake_followups
    )
    request_body = {
        "question": "What needs attention?",
        "report_analysis_id": analysis_id,
        "manager": "Ignored Client Manager",
        "days": 2,
    }

    sync_response = client.post("/api/ask-ai-portfolio", json=request_body)
    stream_response = client.post("/api/ask-ai-portfolio/stream", json=request_body)
    stream_events = _parse_sse(stream_response.get_data(as_text=True))

    assert sync_response.status_code == 200
    assert stream_response.status_code == 200
    assert len(requests) == 2
    assert asdict(requests[0]) == asdict(requests[1])
    expected_scope = _scope_context(requests[0])
    assert sync_response.get_json()["scope_context"] == expected_scope
    assert stream_events["meta"][0]["scope_context"] == expected_scope
    assert stream_events["done"][0]["scope_context"] == expected_scope
    assert sync_response.get_json()["response_state"] == "partial"
    assert stream_events["meta"][0]["response_state"] == "partial"
    assert stream_events["done"][0]["response_state"] == "partial"
    assert sync_response.get_json()["confidence"] == stream_events["meta"][0]["confidence"]
    assert stream_events["done"][0]["confidence"] == stream_events["meta"][0]["confidence"]
    assert stream_events["done"][0]["follow_up_suggestions"] == [
        "What changed in this same customer scope?"
    ]
    assert follow_up_scopes == [expected_scope, expected_scope]


def test_legacy_request_without_report_id_keeps_team_defaults(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    captured = []

    def fake_grounded(request):
        captured.append(request)
        return _grounded_success(request)

    monkeypatch.setattr(app_simple, "run_portfolio_grounded_ask_ai", fake_grounded)
    monkeypatch.setattr(
        app_simple,
        "_r74_generate_follow_up_suggestions",
        lambda **_kwargs: [],
    )
    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "Portfolio status?",
            "manager": manager,
            "technology": "All",
            "days": 30,
        },
    )

    assert response.status_code == 200
    request = captured[0]
    assert request.manager == manager
    assert request.days == 30
    assert request.scope_type == "team"
    assert request.scope_value == ""
    assert request.report_analysis_id == ""
    assert request.fact_fingerprint == ""


def test_report_bound_page_marker_without_id_fails_closed_for_sync_and_stream(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    called = []
    monkeypatch.setattr(
        app_simple,
        "run_portfolio_grounded_ask_ai",
        lambda request: called.append(request) or _grounded_success(request),
    )
    headers = {"X-AdoptIQ-Report-Context": "bound"}
    body = {"question": "What needs attention?", "report_context_mode": "bound"}

    sync_response = client.post(
        "/api/ask-ai-portfolio", json=body, headers=headers
    )
    stream_response = client.post(
        "/api/ask-ai-portfolio/stream", json=body, headers=headers
    )

    assert sync_response.status_code == 409
    assert stream_response.status_code == 409
    assert called == []
    for response in (sync_response, stream_response):
        payload = response.get_json()
        assert payload["ok"] is False
        assert "not downgraded" in payload["error"].lower()
        assert payload["response_state"] == "validation_failed"
        assert payload["confidence"] == {}
        assert payload["retrieval_diag"]["method"] == "not_started"


def test_report_bound_page_get_rejects_missing_report_reference(
    client, ask_ai_test_state
) -> None:
    response = client.get("/ask-ai?report_analysis_id=")

    assert response.status_code == 409
    assert "not downgraded" in response.get_data(as_text=True).lower()


def test_report_bound_request_never_uses_unscoped_legacy_fallback(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    analysis_id = "round146-no-unscoped-fallback"
    snapshot = {
        "analysis_id": analysis_id,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "fact_fingerprint": "sha256:bound-team",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda _requested: (snapshot, 200),
    )
    monkeypatch.setattr(
        app_simple,
        "run_portfolio_grounded_ask_ai",
        lambda request: {
            "ok": False,
            "fallback_to_legacy": True,
            "reason": "fixture stop",
            "scope_context": _scope_context(request),
        },
    )

    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What changed?",
            "report_analysis_id": analysis_id,
            "allow_legacy_fallback": True,
        },
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["fallback_available"] is False
    assert "unscoped fallback was not used" in payload["error"].lower()
    assert payload["scope_context"]["report_analysis_id"] == analysis_id


def test_ask_intel_success_preserves_safe_evidence_freshness_and_trust(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    monkeypatch.setattr(
        app_simple,
        "run_intel_grounded_ask_ai",
        lambda _question, days: {
            "ok": True,
            "answer": "Incident is resolved [Sources: INC-147].",
            "context_summary": "one bounded intel record",
            "data_as_of_utc": "2026-08-04T12:00:00Z",
            "scope_context": {
                "scope_type": "external_intelligence",
                "scope_value": "",
                "days": days,
                "data_as_of_utc": "2026-08-04T12:00:00Z",
                "internal_path": "/private/report.xlsx",
            },
            "evidence_records": [{
                "source_id": "INC-147",
                "source_type": "Incident",
                "customer": "Portfolio",
                "timestamp": "2026-08-04T10:00:00Z",
                "text": "[resolved] Service recovered",
                "snippet": "Service recovered",
                "confidence": 0.9,
                "provider_api_key": "must-not-leak",
                "source_path": "/private/intel.json",
            }],
            "evidence_index": [{
                "source_id": "INC-147",
                "source_type": "Incident",
                "customer": "Portfolio",
                "timestamp": "2026-08-04T10:00:00Z",
                "snippet": "Service recovered",
                "secret": "must-not-leak",
            }],
            "evidence_records_used": 1,
            "evidence_records_total": 7,
            "evidence_truncated": True,
            "account_batch_truncated": False,
            "partial_data_warnings": [{
                "dataset": "intel:status",
                "error": "Feed was truncated.",
                "kind": "external_intel_truncation",
                "traceback": "/private/module.py",
            }],
            "response_state": "partial",
            "confidence": {
                "level": "Medium",
                "score": 65,
                "reasons": ["The evidence set was truncated."],
                "provider_secret": "must-not-leak",
            },
            "retrieval_diag": {
                "method": "bounded_external_intelligence",
                "data_as_of_utc": "2026-08-04T12:00:00Z",
                "response_state": "partial",
                "confidence": {
                    "level": "Medium",
                    "score": 65,
                    "reasons": ["The evidence set was truncated."],
                    "provider_secret": "must-not-leak",
                },
                "provider_path": "/private/provider",
                "api_key": "must-not-leak",
            },
        },
    )

    response = client.post(
        "/api/ask-intel",
        json={"question": "What happened?", "days": 30},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["response_state"] == "partial"
    assert payload["confidence"]["level"] == "Medium"
    assert payload["data_as_of_utc"] == "2026-08-04T12:00:00Z"
    assert payload["scope_context"] == {
        "scope_type": "external_intelligence",
        "scope_value": "",
        "days": 30,
        "data_as_of_utc": "2026-08-04T12:00:00Z",
    }
    assert payload["evidence_records_used"] == 1
    assert payload["evidence_records_total"] == 7
    assert payload["evidence_truncated"] is True
    assert payload["evidence_records"][0]["source_id"] == "INC-147"
    assert payload["evidence_index"][0]["source_id"] == "INC-147"
    assert payload["retrieval_diag"]["method"] == "bounded_external_intelligence"
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in ("must-not-leak", "/private/", "provider_path", "traceback"):
        assert forbidden not in serialized


def test_ask_intel_clean_no_data_keeps_success_envelope(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    monkeypatch.setattr(
        app_simple,
        "run_intel_grounded_ask_ai",
        lambda _question, days: {
            "ok": True,
            "answer": f"No records were available in the last {days} days.",
            "context_summary": "no records",
            "data_as_of_utc": "2026-08-04T12:00:00Z",
            "scope_context": {
                "scope_type": "external_intelligence",
                "scope_value": "",
                "days": days,
                "data_as_of_utc": "2026-08-04T12:00:00Z",
            },
            "evidence_records": [],
            "evidence_index": [],
            "evidence_records_used": 0,
            "evidence_records_total": 0,
            "evidence_truncated": False,
            "account_batch_truncated": False,
            "partial_data_warnings": [],
            "response_state": "no_data",
            "confidence": {
                "level": "Low",
                "score": 0,
                "reasons": ["No evidence records were available."],
            },
            "retrieval_diag": {
                "method": "no_data",
                "response_state": "no_data",
            },
        },
    )

    response = client.post(
        "/api/ask-intel",
        json={"question": "Any incidents?", "days": 14},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["response_state"] == "no_data"
    assert payload["confidence"]["score"] == 0
    assert payload["scope_context"]["days"] == 14
    assert payload["evidence_records"] == []
    assert payload["evidence_index"] == []
    assert payload["evidence_records_used"] == 0
    assert payload["evidence_records_total"] == 0


def test_report_binding_without_fact_fingerprint_is_rejected(
    client, monkeypatch, ask_ai_test_state
) -> None:
    app_simple = ask_ai_test_state
    manager = app_simple.TEAM_ROSTER[0][0]
    analysis_id = "round146-missing-fingerprint"
    snapshot = {
        "analysis_id": analysis_id,
        "report_type": "leader",
        "manager": manager,
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "fact_fingerprint": "",
    }
    monkeypatch.setattr(
        app_simple,
        "_r146_workspace_snapshot",
        lambda _requested: (snapshot, 200),
    )

    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What decision comes first?",
            "report_analysis_id": analysis_id,
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["ok"] is False
    assert "verifiable fact binding" in payload["error"]


def test_subscription_snapshot_uses_subscription_id_as_canonical_scope() -> None:
    import manager_decision_workspace as decision_workspace

    snapshot = decision_workspace.snapshot_from_status(
        {
            "analysis_id": "sub-SUB-146",
            "status": "completed",
            "report_type": "comprehensive",
            "subscription_id": "SUB-146",
            "days": 90,
        },
        {
            "report_type": "comprehensive",
            "scope_type": "customer",
            "scope_value": "Presentation Customer Label",
        },
    )
    binding = decision_workspace.ask_ai_binding(snapshot)

    assert snapshot["scope_type"] == "subscription"
    assert snapshot["scope_value"] == "SUB-146"
    assert binding["scope_type"] == "subscription"
    assert binding["scope_value"] == "SUB-146"
