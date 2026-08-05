"""Public AI JSON and SSE envelopes never expose internal diagnostics."""

from __future__ import annotations

import json

import pytest


SENTINEL = "token=abc host=db.internal path=/Users/private"
FORBIDDEN = (
    "token=abc",
    "db.internal",
    "/Users/private",
    "provider_secret_abc",
    "internal.cluster",
    "sql_exception",
    "private_table",
    "ghp_secret",
    '"provider_body"',
    '"provider_error"',
    '"fetch_error"',
    '"internal_path"',
)


def _assert_public(value: object) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)
    for fragment in FORBIDDEN:
        assert fragment not in serialized


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
def public_ai(monkeypatch: pytest.MonkeyPatch):
    import app_simple

    with app_simple._ask_ai_rate_lock:
        app_simple._ask_ai_rate_log.clear()
    monkeypatch.setattr(app_simple, "is_grounded_ask_ai_enabled", lambda: True)
    monkeypatch.setattr(app_simple, "_record_ask_ai_query_diag", lambda *_args: None)
    monkeypatch.setattr(
        app_simple,
        "_r74_generate_follow_up_suggestions",
        lambda **_kwargs: [],
    )
    return app_simple


def _warning() -> dict:
    return {
        "dataset": "private_table",
        "kind": "sql_exception",
        "error": SENTINEL,
        "freshness": "db.internal",
        "provider_body": {"sql": SENTINEL},
    }


def _confidence() -> dict:
    return {
        "level": "Low",
        "score": 20,
        "reasons": [SENTINEL],
        "provider": {"exception": SENTINEL},
    }


def _diag() -> dict:
    return {
        "method": "internal.cluster",
        "response_state": "partial",
        "confidence": _confidence(),
        "fetch_error": SENTINEL,
        "records": [{"exception": SENTINEL}],
    }


def _portfolio_success(request) -> dict:
    return {
        "ok": True,
        "answer": "Supported finding [Sources: SAFE-1].",
        "context_summary": "One supported record",
        "scope_context": {
            "manager": request.manager,
            "technology": request.technology,
            "days": request.days,
            "scope_type": request.scope_type,
            "scope_value": request.scope_value,
            "scope_member": request.scope_member,
            "report_analysis_id": request.report_analysis_id,
            "report_type": request.report_type,
            "data_as_of_utc": "2026-08-04T12:00:00Z",
            "fact_fingerprint": request.fact_fingerprint,
            "source_states": {"db.internal": "ghp_secret"},
            "evidence_mode": "internal.cluster",
            "internal_path": SENTINEL,
        },
        "response_state": "partial",
        "confidence": _confidence(),
        "partial_data_warnings": [_warning()],
        "retrieval_diag": _diag(),
        "evidence_records": [
            {
                "source_id": "SAFE-1",
                "source_type": "SupportCase",
                "customer": "Safe Customer",
                "timestamp": "2026-08-04T10:00:00Z",
                "text": "Supported evidence remains visible.",
                "confidence": 4,
                "bm25_rank": -3,
                "rrf_score": "0.25",
                "provider_secret_abc": SENTINEL,
                "internal.cluster": {"sql_exception": SENTINEL},
                "provider_error": "ghp_secret",
            }
        ],
        "evidence_index": [
            {
                "source_id": "SAFE-1",
                "source_type": "SupportCase",
                "snippet": "Supported evidence remains visible.",
                "provider_secret_abc": SENTINEL,
                "provider_error": "ghp_secret",
            }
        ],
        "corpus": {
            "available": False,
            "banner": SENTINEL,
            "stats": {"chunks": 0, "provider_path": SENTINEL},
        },
    }


def _failure() -> dict:
    return {
        "ok": False,
        "error": SENTINEL,
        "reason": "provider_secret_abc",
        "status_code": 503,
        "response_state": "retrieval_failed",
        "confidence": _confidence(),
        "partial_data_warnings": [_warning()],
        "retrieval_diag": _diag(),
    }


def test_portfolio_sync_and_sse_success_sanitize_diagnostics_but_keep_evidence(
    client,
    monkeypatch: pytest.MonkeyPatch,
    public_ai,
) -> None:
    persisted_diagnostics = []
    monkeypatch.setattr(
        public_ai,
        "_record_ask_ai_query_diag",
        lambda _query_id, diag: persisted_diagnostics.append(diag),
    )
    monkeypatch.setattr(
        public_ai,
        "run_portfolio_grounded_ask_ai",
        _portfolio_success,
    )
    request = {
        "question": "What needs attention?",
        "manager": public_ai.TEAM_ROSTER[0][0],
        "technology": "All",
        "days": 30,
    }

    sync = client.post("/api/ask-ai-portfolio", json=request)
    stream = client.post("/api/ask-ai-portfolio/stream", json=request)
    sync_payload = sync.get_json()
    events = _parse_sse(stream.get_data(as_text=True))

    assert sync.status_code == 200
    assert stream.status_code == 200
    assert sync_payload["response_state"] == "partial"
    assert sync_payload["retrieval_diag"] == {
        "method": "unknown",
        "response_state": "partial",
        "confidence": sync_payload["confidence"],
    }
    assert sync_payload["partial_data_warnings"][0] == {
        "dataset": "unknown",
        "kind": "partial_data",
        "effect": "may_be_incomplete",
        "error": ("This source is partially available; affected metrics may be incomplete."),
        "freshness": "unknown",
    }
    assert sync_payload["scope_context"]["source_states"] == {"unknown": "unknown"}
    assert sync_payload["scope_context"]["evidence_mode"] == "unknown"
    assert sync_payload["evidence_records"][0]["text"] == ("Supported evidence remains visible.")
    assert sync_payload["evidence_records"][0]["confidence"] == 1.0
    assert sync_payload["evidence_records"][0]["bm25_rank"] == 0
    assert sync_payload["evidence_records"][0]["rrf_score"] == 0.25
    assert set(sync_payload["evidence_index"][0]) == {
        "source_id",
        "source_type",
        "snippet",
    }
    assert events["meta"][0]["evidence_records"][0]["source_id"] == "SAFE-1"
    assert len(persisted_diagnostics) == 2
    for diag in persisted_diagnostics:
        assert diag["_r74_evidence_records"] == sync_payload["evidence_records"]
        _assert_public(diag)
    _assert_public(sync_payload)
    _assert_public(events)


def test_portfolio_sync_and_sse_failures_return_stable_safe_contract(
    client,
    monkeypatch: pytest.MonkeyPatch,
    public_ai,
) -> None:
    monkeypatch.setattr(
        public_ai,
        "run_portfolio_grounded_ask_ai",
        lambda _request: _failure(),
    )
    request = {
        "question": "What needs attention?",
        "manager": public_ai.TEAM_ROSTER[0][0],
    }

    sync = client.post("/api/ask-ai-portfolio", json=request)
    stream = client.post("/api/ask-ai-portfolio/stream", json=request)
    sync_payload = sync.get_json()
    events = _parse_sse(stream.get_data(as_text=True))
    stream_error = events["error"][0]

    assert sync.status_code == 503
    assert stream.status_code == 200
    assert sync_payload["response_state"] == "retrieval_failed"
    assert sync_payload["reason"] == "grounded_retrieval_failed"
    assert "could not be retrieved" in sync_payload["error"]
    assert stream_error["response_state"] == "retrieval_failed"
    assert stream_error["reason"] == "grounded_retrieval_failed"
    assert "could not be retrieved" in stream_error["error"]
    _assert_public(sync_payload)
    _assert_public(events)


def test_stream_pipeline_exception_is_logged_but_not_emitted(
    client,
    monkeypatch: pytest.MonkeyPatch,
    public_ai,
) -> None:
    def raise_internal(_request):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(
        public_ai,
        "run_portfolio_grounded_ask_ai",
        raise_internal,
    )
    response = client.post(
        "/api/ask-ai-portfolio/stream",
        json={
            "question": "What needs attention?",
            "manager": public_ai.TEAM_ROSTER[0][0],
        },
    )
    events = _parse_sse(response.get_data(as_text=True))

    assert response.status_code == 200
    assert events["error"][0]["reason"] == "grounded_pipeline_exception"
    assert "did not return a usable grounded response" in events["error"][0]["error"]
    _assert_public(events)


def test_stream_generator_exception_is_logged_but_not_emitted(
    client,
    monkeypatch: pytest.MonkeyPatch,
    public_ai,
) -> None:
    monkeypatch.setattr(
        public_ai,
        "run_portfolio_grounded_ask_ai",
        _portfolio_success,
    )

    def raise_internal(*_args, **_kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(public_ai, "_r74_chunk_text_for_sse", raise_internal)
    response = client.post(
        "/api/ask-ai-portfolio/stream",
        json={
            "question": "What needs attention?",
            "manager": public_ai.TEAM_ROSTER[0][0],
        },
    )
    events = _parse_sse(response.get_data(as_text=True))

    assert response.status_code == 200
    assert events["meta"]
    assert events["error"][0]["reason"] == "sse_generation_failed"
    assert events["error"][0]["response_state"] == "model_unavailable"
    assert "did not return a usable grounded response" in events["error"][0]["error"]
    _assert_public(events)


def test_portfolio_evidence_lookup_reprojects_legacy_diagnostics(
    client,
    monkeypatch: pytest.MonkeyPatch,
    public_ai,
) -> None:
    legacy_record = _portfolio_success(
        type(
            "Request",
            (),
            {
                "manager": "Manager",
                "technology": "All",
                "days": 30,
                "scope_type": "team",
                "scope_value": "",
                "scope_member": "",
                "report_analysis_id": "",
                "report_type": "",
                "fact_fingerprint": "",
            },
        )()
    )["evidence_records"][0]
    monkeypatch.setattr(
        public_ai,
        "_get_ask_ai_query_diag",
        lambda _query_id: {"_r74_evidence_records": [legacy_record]},
    )
    monkeypatch.setattr(
        public_ai,
        "_r71_diag_rate_limit_check",
        lambda _client_ip: (True, 0),
    )

    response = client.get("/api/ask-ai/evidence/query-1/SAFE-1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["record"]["source_id"] == "SAFE-1"
    assert payload["record"]["text"] == "Supported evidence remains visible."
    assert payload["record"]["confidence"] == 1.0
    _assert_public(payload)


@pytest.mark.parametrize("success", (True, False))
def test_ask_intel_sanitizes_success_and_failure_envelopes(
    client,
    monkeypatch: pytest.MonkeyPatch,
    public_ai,
    success: bool,
) -> None:
    result = _failure()
    if success:
        result.update(
            {
                "ok": True,
                "answer": "Incident resolved [Sources: INC-1].",
                "context_summary": "One supported incident",
                "data_as_of_utc": "2026-08-04T12:00:00Z",
                "scope_context": {
                    "scope_type": "external_intelligence",
                    "scope_value": "",
                    "days": 30,
                    "data_as_of_utc": "2026-08-04T12:00:00Z",
                    "internal_path": SENTINEL,
                },
                "response_state": "partial",
                "evidence_records": [
                    {
                        "source_id": "INC-1",
                        "source_type": "Incident",
                        "text": "Supported incident evidence.",
                        "provider_error": SENTINEL,
                    }
                ],
                "evidence_index": [],
                "evidence_records_used": 1,
                "evidence_records_total": 1,
            }
        )
    monkeypatch.setattr(
        public_ai,
        "run_intel_grounded_ask_ai",
        lambda _question, days: result,
    )

    response = client.post(
        "/api/ask-intel",
        json={"question": "Any incidents?", "days": 30},
    )
    payload = response.get_json()

    assert response.status_code == (200 if success else 503)
    if success:
        assert payload["evidence_records"][0]["text"] == ("Supported incident evidence.")
        assert payload["response_state"] == "partial"
    else:
        assert payload["response_state"] == "retrieval_failed"
        assert payload["reason"] == "grounded_retrieval_failed"
        assert "could not be retrieved" in payload["error"]
    _assert_public(payload)


def test_llm_json_provider_exception_returns_only_stable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import adoptiq_backend

    def raise_provider(*_args, **_kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(adoptiq_backend, "generate_llm_response", raise_provider)
    result = adoptiq_backend.generate_llm_json_response(
        "system",
        "briefing",
        {"type": "object", "properties": {}, "required": []},
    )

    assert result == {
        "ok": False,
        "error": ("ERROR: llm.provider_unavailable: The AI service did not return a usable response."),
        "reason": "llm_provider_unavailable",
    }
    _assert_public(result)


def test_grounded_failure_confidence_reason_never_mirrors_error() -> None:
    import ask_ai_grounded

    result = ask_ai_grounded._ai_failure_payload(  # noqa: SLF001
        error=SENTINEL,
        response_state="model_unavailable",
        reason="model_unavailable",
    )

    assert result["error"] == SENTINEL
    assert result["confidence"]["reasons"] == ["The AI service did not return a usable grounded response."]
    _assert_public(result["confidence"])
