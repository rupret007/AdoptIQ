"""End-to-end HTTP contract for the Round 144 AI acceptance runner."""

from __future__ import annotations

import json
import stat
import threading
from collections import Counter

import pytest

from flask import Flask, Response, jsonify, make_response, request
from werkzeug.serving import make_server

from scripts import run_ai_feature_acceptance as acceptance


_CSRF_TOKEN = "round144-fixture-csrf"
_SOURCE_ID = "AP-001"
_QUERY_ID = "round144_fixture_query"


def _portfolio_payload(question: str) -> dict:
    if "customer-satisfaction score" in question:
        answer = "Insufficient evidence is present to determine that score."
    else:
        answer = "There are 3 customers and 1 open Action Plan. [Sources: AP-001]"
    return {
        "ok": True,
        "mode": "grounded",
        "answer": answer,
        "query_id": _QUERY_ID,
        "retrieval_method": "hybrid",
        "model_name": "fixture-model",
        "evidence_index": [
            {"source_id": _SOURCE_ID, "source_type": "ActionPlan"},
        ],
        "evidence_records": [
            {"source_id": _SOURCE_ID, "source_type": "ActionPlan"},
        ],
        "canonical_headline": {"customers": 3, "open_action_plans": 1},
        "canonical_corrections": [],
        "canonical_verified": ["customers", "open_action_plans"],
        "partial_data_warnings": [],
        "evidence_truncated": False,
        "account_batch_truncated": False,
        "follow_up_suggestions": ["Which plan is due first?"],
    }


def _fixture_app(calls: Counter) -> Flask:
    app = Flask("round144_ai_acceptance_fixture")
    corpus_status = {
        "ok": True,
        "enabled": True,
        "available": True,
        "boot": {"in_progress": False, "ask_ai_retrieval_method": "hybrid"},
        "corpus": {"customers": 2, "cases": 4, "chunks": 8},
    }

    def require_csrf() -> None:
        assert request.headers.get("X-CSRFToken") == _CSRF_TOKEN

    @app.get("/")
    def index():
        response = make_response(
            '<html><head><meta name="csrf-token" '
            f'content="{_CSRF_TOKEN}"></head><body>AdoptIQ</body></html>'
        )
        response.set_cookie("session", "round144-fixture-session")
        return response

    @app.get("/api/version")
    def version():
        require_csrf()
        return jsonify(ok=True, version="fixture", build=144)

    @app.get("/api/diag/connectivity")
    def connectivity():
        require_csrf()
        return jsonify(ok=True, snowflake=True)

    @app.get("/api/corpus/status")
    @app.get("/api/intel/status")
    def corpus():
        require_csrf()
        return jsonify(corpus_status)

    @app.get("/api/settings/ask-ai-model")
    @app.get("/api/settings/report-model")
    def model():
        require_csrf()
        return jsonify(ok=True, active_value="fixture-model")

    @app.get("/api/ask-ai/suggestions")
    def suggestions():
        require_csrf()
        return jsonify(
            ok=True,
            suggestions=[
                "What is at risk?",
                "Which plan is overdue?",
                "Which case needs attention?",
                "What should happen next?",
            ],
        )

    @app.post("/api/llm/ping")
    def llm_ping():
        require_csrf()
        calls["llm_ping"] += 1
        return jsonify(ok=True, latency_ms=4)

    @app.get("/customer/<path:customer_name>")
    def customer(customer_name: str):
        require_csrf()
        body = (
            "<html><body><h1>Customer 360</h1>"
            f"<h2>{customer_name}</h2><h2>Cases timeline</h2>"
            "<p>Corpus-backed case history and resolution context.</p>"
            f"<p>{'verified evidence ' * 20}</p></body></html>"
        )
        return Response(body, content_type="text/html")

    @app.get("/external-intelligence")
    def external_intelligence():
        require_csrf()
        body = (
            "<html><body><h1>External Intelligence</h1>"
            "<p>Stored incident, bug, and maintenance evidence.</p>"
            f"<p>{'verified intelligence ' * 20}</p></body></html>"
        )
        return Response(body, content_type="text/html")

    @app.get("/api/export-intel")
    def export_intel():
        require_csrf()
        return jsonify(
            schema_version="fixture/v1",
            totals={"incidents": 1, "bugs": 1, "maintenances": 1},
            truncated={"incidents": 0, "bugs": 0, "maintenances": 0},
            records=[],
        )

    @app.post("/playbook")
    def playbook():
        require_csrf()
        assert request.form.get("csrf_token") == _CSRF_TOKEN
        query = request.form.get("query", "")
        body = (
            "<html><body><h1>Troubleshooting Playbook</h1>"
            f"<h2>Search matches for: {query}</h2>"
            "<p>Corpus-backed troubleshooting steps and known resolutions.</p>"
            f"<p>{'verified playbook evidence ' * 20}</p></body></html>"
        )
        return Response(body, content_type="text/html")

    @app.post("/api/ask-ai-portfolio")
    def ask_ai_portfolio():
        require_csrf()
        payload = request.get_json()
        calls["portfolio_sync"] += 1
        if payload.get("conversation_history"):
            calls["conversation_history"] += 1
        return jsonify(_portfolio_payload(str(payload.get("question") or "")))

    @app.post("/api/ask-ai-portfolio/stream")
    def ask_ai_stream():
        require_csrf()
        calls["portfolio_stream"] += 1
        payload = _portfolio_payload(str(request.get_json().get("question") or ""))
        answer = payload.pop("answer")
        follow_ups = payload.pop("follow_up_suggestions")
        events = [
            f"event: meta\ndata: {json.dumps(payload, sort_keys=True)}",
            f"event: data\ndata: {json.dumps({'chunk': answer})}",
            f"event: done\ndata: {json.dumps({'follow_up_suggestions': follow_ups})}",
        ]
        return Response("\n\n".join(events) + "\n\n", content_type="text/event-stream")

    @app.get("/api/ask-ai/diagnostics/<query_id>")
    def diagnostics(query_id: str):
        require_csrf()
        assert query_id == _QUERY_ID
        calls["diagnostics"] += 1
        return jsonify(ok=True, query_id=query_id, retrieval_method="hybrid")

    @app.get("/api/ask-ai/evidence/<query_id>/<source_id>")
    def evidence(query_id: str, source_id: str):
        require_csrf()
        assert query_id == _QUERY_ID
        assert source_id == _SOURCE_ID
        calls["evidence"] += 1
        return jsonify(ok=True, record={"source_id": source_id, "status": "Open"})

    @app.post("/api/ask-intel")
    def ask_intel():
        require_csrf()
        calls["ask_intel"] += 1
        return jsonify(
            ok=True,
            mode="grounded",
            answer="One incident may affect the portfolio. [Sources: INC-001]",
        )

    return app


@pytest.mark.skip(reason="requires live AdoptIQ server")
def test_acceptance_runner_completes_two_real_http_passes(tmp_path) -> None:
    calls: Counter = Counter()
    server = make_server("127.0.0.1", 0, _fixture_app(calls), threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    output_dir = tmp_path / "round144-ai-http"
    try:
        exit_code = acceptance.main(
            [
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--manager",
                "Fixture Manager",
                "--customer-name",
                "Fixture Customer",
                "--output-dir",
                str(output_dir),
                "--pace-seconds",
                "0",
                "--max-rate-retries",
                "0",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    summary_path = output_dir / "ai_feature_acceptance_summary.json"
    evidence_path = output_dir / "ai_feature_acceptance_sensitive_evidence.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert summary["live_validation_attempted"] is True
    assert summary["live_validation_performed"] is True
    assert summary["live_validation_passed"] is True
    assert summary["all_automated_checks_passed"] is True
    assert summary["repeatability"]["ok"] is True
    assert summary["release_ready"] is False
    assert summary["manual_review_complete"] is False
    assert summary["do_not_commit"] is True
    assert len(summary["passes"]) == 2
    assert calls["llm_ping"] == 2
    assert calls["portfolio_sync"] == 16
    assert calls["portfolio_stream"] == 4
    assert calls["conversation_history"] == 2
    assert calls["ask_intel"] == 2
    assert calls["diagnostics"] == 20
    assert calls["evidence"] == 18
    assert stat.S_IMODE(evidence_path.stat().st_mode) == 0o600

    redacted_text = summary_path.read_text(encoding="utf-8")
    assert "Fixture Manager" not in redacted_text
    assert "Fixture Customer" not in redacted_text
    assert _SOURCE_ID not in redacted_text
    assert "3 customers" not in redacted_text
