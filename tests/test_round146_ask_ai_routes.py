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

    with app_simple._ask_ai_rate_lock:
        app_simple._ask_ai_rate_log.clear()
    monkeypatch.setattr(app_simple, "is_grounded_ask_ai_enabled", lambda: True)
    monkeypatch.setattr(app_simple, "_record_ask_ai_query_diag", lambda *_args: None)
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
