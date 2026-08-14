"""Round 168 fail-closed regressions for acceptance JSON boolean contracts."""

from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import report_iteration_loop as iteration
from scripts import run_ai_feature_acceptance as ai_acceptance
from scripts import run_decision_report_acceptance as report_acceptance


MISSING = object()
INVALID_TRUE_FLAGS = ("false", 1, MISSING)


class _Response:
    def __init__(
        self,
        payload: Any = None,
        *,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}
        self.content = b""

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _set_or_remove(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is MISSING:
        payload.pop(key, None)
    else:
        payload[key] = value


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_connectivity_requires_literal_json_true(
    monkeypatch: pytest.MonkeyPatch,
    invalid: Any,
) -> None:
    connectivity: dict[str, Any] = {"ok": True}
    _set_or_remove(connectivity, "ok", invalid)

    class Session:
        def get(self, url: str, *, timeout: int) -> _Response:
            del timeout
            if url.endswith("/ping"):
                return _Response(status_code=200, text="OK")
            if url.endswith("/api/diag/connectivity"):
                return _Response(connectivity)
            raise AssertionError(url)

    monkeypatch.setattr(report_acceptance.requests, "Session", Session)

    result = report_acceptance.probe_live_sources("http://127.0.0.1:5151")

    assert result["snowflake"]["ok"] is False
    assert result["ok"] is False


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_explicit_live_mode_rejects_tampered_connectivity_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: Any,
) -> None:
    connectivity: dict[str, Any] = {"ok": True}
    _set_or_remove(connectivity, "ok", invalid)
    monkeypatch.setattr(
        report_acceptance,
        "probe_live_sources",
        lambda _base_url: connectivity,
    )

    exit_code = report_acceptance.main(
        [
            "--mode",
            "live",
            "--manager",
            "Fixture Manager",
            "--days",
            "90",
            "--as-of",
            "2026-08-03T12:00:00Z",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 5


def _decision_client(tmp_path: Path) -> report_acceptance.LiveDecisionReportClient:
    return report_acceptance.LiveDecisionReportClient(
        base_url="http://127.0.0.1:5151",
        output_dir=tmp_path,
        poll_interval=0.25,
        scenario_timeout=60,
        request_timeout=10,
        download_timeout=30,
        csone_file=None,
    )


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_start_and_scope_responses_require_literal_json_true(
    tmp_path: Path,
    invalid: Any,
) -> None:
    payload: dict[str, Any] = {"success": True, "analysis_id": "fixture"}
    _set_or_remove(payload, "success", invalid)

    class Session:
        def post(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response(payload)

        def get(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response(payload)

    client = _decision_client(tmp_path)
    client.session = Session()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="start failed"):
        client._post_start("/start_leader_report", {})  # noqa: SLF001
    with pytest.raises(RuntimeError, match="scope-options request failed"):
        client.scope_options("Fixture Manager")


@pytest.mark.parametrize("flag", ("word_available", "excel_available"))
@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_artifact_availability_requires_literal_json_true(
    tmp_path: Path,
    flag: str,
    invalid: Any,
) -> None:
    status: dict[str, Any] = {
        "status": "completed",
        "word_available": True,
        "excel_available": True,
    }
    _set_or_remove(status, flag, invalid)
    client = _decision_client(tmp_path)
    client._post_start = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "status_code": 200,
        "payload": {"success": True, "analysis_id": "fixture"},
    }

    class Session:
        def get(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response(status)

    client.session = Session()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="both required artifacts"):
        client.run(endpoint="/start_leader_report", payload={}, scope_key="team")


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_repeatability_rejects_nonliteral_scope_pass(invalid: Any) -> None:
    def acceptance_pass(value: Any) -> dict[str, Any]:
        scopes: dict[str, Any] = {}
        for scope in report_acceptance.SUPPORTED_SCOPES:
            result: dict[str, Any] = {"ok": True}
            _set_or_remove(result, "ok", value)
            scopes[scope] = result
        return {"scopes": scopes}

    result = report_acceptance.compare_passes(
        [acceptance_pass(invalid), acceptance_pass(True)],
        live=True,
    )

    assert result["ok"] is False


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_customer_scope_availability_requires_literal_json_true(
    invalid: Any,
) -> None:
    customers: dict[str, Any] = {
        "customers_available": True,
        "customers": [{"value": "Fixture Customer"}],
    }
    _set_or_remove(customers, "customers_available", invalid)

    class Client:
        def scope_options(
            self,
            _manager: str,
            *,
            member_email: str = "",
            include_customers: bool = False,
        ) -> dict[str, Any]:
            del member_email
            if include_customers:
                return customers
            return {"members": [{"name": "Fixture", "email": "fixture@example.test"}]}

    with pytest.raises(RuntimeError, match="authorization cannot be proven"):
        report_acceptance._choose_live_scopes(  # noqa: SLF001
            Client(),  # type: ignore[arg-type]
            manager="Fixture Manager",
            member_email="",
            customer_name="",
            customer_member_email="",
        )


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_decision_negative_scope_probe_requires_explicit_json_false(
    tmp_path: Path,
    invalid: Any,
) -> None:
    payload: dict[str, Any] = {"success": False}
    _set_or_remove(payload, "success", invalid)
    client = _decision_client(tmp_path)
    client._post_start = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
        "status_code": 400,
        "payload": payload,
    }

    result = client.negative_scope_probe(manager="Fixture Manager")

    assert result["outside_manager_member_rejected"] is False
    assert result["ok"] is False


def _portfolio_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "grounded",
        "answer": "One open plan. [Sources: AP-001]",
        "query_id": "valid-query_123",
        "retrieval_method": "hybrid",
        "model_name": "fixture-model",
        "evidence_index": [{"source_id": "AP-001", "source_type": "ActionPlan"}],
        "evidence_records": [{"source_id": "AP-001", "source_type": "ActionPlan"}],
        "evidence_truncated": False,
        "account_batch_truncated": False,
    }


@pytest.mark.parametrize("flag", ("ok", "evidence_truncated", "account_batch_truncated"))
@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_ai_response_contract_rejects_nonliteral_boolean_flags(
    flag: str,
    invalid: Any,
) -> None:
    payload = _portfolio_payload()
    _set_or_remove(payload, flag, invalid)

    errors = ai_acceptance.validate_portfolio_payload(
        payload,
        require_citations=True,
        require_evidence_gap=False,
    )
    redacted = ai_acceptance._scenario_redaction(  # noqa: SLF001
        case=ai_acceptance.QuestionCase(
            key="fixture",
            route="portfolio_sync",
            question="fixture",
        ),
        payload=payload,
        errors=errors,
        duration_ms=1,
        evidence_lookup_ok=True,
        diagnostics_ok=True,
    )

    assert errors
    assert redacted["ok"] is False


@pytest.mark.parametrize(
    "field",
    ("evidence_records_used", "evidence_records_total"),
)
@pytest.mark.parametrize(
    "invalid",
    (True, "200", 200.0, 200.5, -1, 1 << 63, None, MISSING),
)
def test_ai_truncation_counts_require_exact_json_integers(
    field: str,
    invalid: Any,
) -> None:
    payload = _portfolio_payload()
    payload.update(
        evidence_truncated=True,
        evidence_records_used=200,
        evidence_records_total=277,
    )
    _set_or_remove(payload, field, invalid)

    errors = ai_acceptance.validate_portfolio_payload(
        payload,
        require_citations=True,
        require_evidence_gap=False,
    )

    assert "evidence truncation metadata is missing or incoherent" in errors


@pytest.mark.parametrize(
    "invalid",
    (True, "3", 3.0, 3.5, -1, 1 << 63, None, MISSING),
)
def test_ai_canonical_headline_counts_require_exact_json_integers(
    invalid: Any,
) -> None:
    payload = _portfolio_payload()
    headline: dict[str, Any] = {"customers": 3}
    _set_or_remove(headline, "customers", invalid)
    payload["canonical_headline"] = headline

    assert ai_acceptance.validate_canonical_headline(payload, {"customers": 3})


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_ask_intel_response_requires_literal_json_true(invalid: Any) -> None:
    payload: dict[str, Any] = {
        "ok": True,
        "mode": "grounded",
        "answer": "One incident. [Sources: INC-001]",
    }
    _set_or_remove(payload, "ok", invalid)

    assert ai_acceptance.validate_ask_intel_payload(payload)


@pytest.mark.parametrize("event_name", ("meta", "done"))
@pytest.mark.parametrize("invalid", ("false", 1, False))
def test_ai_stream_explicit_ok_requires_literal_json_true(
    event_name: str,
    invalid: Any,
) -> None:
    meta: dict[str, Any] = {"query_id": "fixture"}
    done: dict[str, Any] = {"completed_at": "2026-08-14T00:00:00Z"}
    (meta if event_name == "meta" else done)["ok"] = invalid
    payload = ai_acceptance._stream_payload(  # noqa: SLF001
        (("meta", meta), ("data", {"chunk": "answer"}), ("done", done))
    )

    assert payload["ok"] is False


@pytest.mark.parametrize("missing_event", ("meta", "done"))
def test_ai_stream_requires_both_contract_events(missing_event: str) -> None:
    events = [
        ("meta", {"query_id": "fixture"}),
        ("data", {"chunk": "answer"}),
        ("done", {"completed_at": "2026-08-14T00:00:00Z"}),
    ]

    payload = ai_acceptance._stream_payload(  # noqa: SLF001
        tuple(event for event in events if event[0] != missing_event)
    )

    assert payload["ok"] is False


class _AiPreflightClient:
    def __init__(self, *, path: str = "", key: str = "", value: Any = None) -> None:
        self.path = path
        self.key = key
        self.value = value
        self.csrf_token = "fixture"

    def _tamper(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(payload)
        if path == self.path:
            if self.key == "boot.in_progress":
                boot = payload.setdefault("boot", {})
                _set_or_remove(boot, "in_progress", self.value)
            elif self.key.startswith("corpus."):
                corpus = payload.setdefault("corpus", {})
                _set_or_remove(corpus, self.key.split(".", 1)[1], self.value)
            else:
                _set_or_remove(payload, self.key, self.value)
        return payload

    def get_json(self, path: str, *, params: Any = None) -> tuple[int, dict[str, Any]]:
        del params
        corpus = {
            "ok": True,
            "enabled": True,
            "available": True,
            "boot": {"in_progress": False, "ask_ai_retrieval_method": "hybrid"},
            "corpus": {"customers": 2, "cases": 4, "chunks": 8},
        }
        payloads = {
            "/api/version": {"ok": True},
            "/api/diag/connectivity": {"ok": True},
            "/api/corpus/status": corpus,
            "/api/intel/status": corpus,
            "/api/settings/ask-ai-model": {"ok": True, "active_value": "fixture"},
            "/api/settings/report-model": {"ok": True, "active_value": "fixture"},
            "/api/ask-ai/suggestions": {
                "ok": True,
                "suggestions": ["one", "two", "three", "four"],
            },
            "/api/export-intel": {
                "schema_version": "fixture/v1",
                "totals": {"incidents": 1, "bugs": 1, "maintenances": 1},
                "truncated": {"incidents": 0, "bugs": 0, "maintenances": 0},
                "records": [],
            },
        }
        if path not in payloads:
            raise AssertionError(path)
        return 200, self._tamper(path, payloads[path])

    def post_json(
        self,
        path: str,
        _payload: dict[str, Any],
        *,
        expensive: bool,
    ) -> tuple[int, dict[str, Any]]:
        del expensive
        assert path == "/api/llm/ping"
        return 200, self._tamper(path, {"ok": True})

    def get_page(self, path: str) -> tuple[int, str, str]:
        if path.startswith("/customer/"):
            return 200, "text/html", (
                "<h1>Customer 360</h1><h2>Fixture Customer</h2>"
                "<h2>Cases timeline</h2>"
            )
        if path == "/external-intelligence":
            return 200, "text/html", "<h1>External Intelligence</h1>"
        raise AssertionError(path)

    def post_form_page(
        self,
        path: str,
        _payload: dict[str, Any],
        *,
        expensive: bool,
    ) -> tuple[int, str, str]:
        del expensive
        assert path == "/playbook"
        return 200, "text/html", (
            "<h1>Troubleshooting Playbook</h1>"
            "<h2>Search matches for: registration authentication outage</h2>"
        )


@pytest.mark.parametrize(
    "path",
    (
        "/api/version",
        "/api/diag/connectivity",
        "/api/corpus/status",
        "/api/intel/status",
        "/api/settings/ask-ai-model",
        "/api/settings/report-model",
        "/api/ask-ai/suggestions",
        "/api/llm/ping",
    ),
)
@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_ai_preflight_endpoint_ok_flags_require_literal_json_true(
    path: str,
    invalid: Any,
) -> None:
    client = _AiPreflightClient(path=path, key="ok", value=invalid)

    _redacted, _sensitive, errors = ai_acceptance._run_preflight(  # noqa: SLF001
        client,  # type: ignore[arg-type]
        manager="Fixture Manager",
        technology="All",
        days=90,
        customer_name="Fixture Customer",
    )

    assert errors


@pytest.mark.parametrize("flag", ("enabled", "available"))
@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_ai_corpus_availability_requires_literal_json_true(
    flag: str,
    invalid: Any,
) -> None:
    client = _AiPreflightClient(
        path="/api/corpus/status",
        key=flag,
        value=invalid,
    )

    redacted, _sensitive, errors = ai_acceptance._run_preflight(  # noqa: SLF001
        client,  # type: ignore[arg-type]
        manager="Fixture Manager",
        technology="All",
        days=90,
        customer_name="Fixture Customer",
    )

    assert errors
    assert redacted["corpus_status"][flag] is False


@pytest.mark.parametrize("invalid", ("false", 0, 1, MISSING))
def test_ai_corpus_boot_state_requires_literal_json_false(invalid: Any) -> None:
    client = _AiPreflightClient(
        path="/api/corpus/status",
        key="boot.in_progress",
        value=invalid,
    )

    _redacted, _sensitive, errors = ai_acceptance._run_preflight(  # noqa: SLF001
        client,  # type: ignore[arg-type]
        manager="Fixture Manager",
        technology="All",
        days=90,
        customer_name="Fixture Customer",
    )

    assert "encrypted corpus indexing is still in progress" in errors


@pytest.mark.parametrize("field", ("customers", "cases", "chunks"))
@pytest.mark.parametrize(
    "invalid",
    (True, "2", 2.0, 2.5, -1, 1 << 63, None, MISSING),
)
def test_ai_corpus_counts_require_exact_json_integers(
    field: str,
    invalid: Any,
) -> None:
    client = _AiPreflightClient(
        path="/api/corpus/status",
        key=f"corpus.{field}",
        value=invalid,
    )

    _redacted, _sensitive, errors = ai_acceptance._run_preflight(  # noqa: SLF001
        client,  # type: ignore[arg-type]
        manager="Fixture Manager",
        technology="All",
        days=90,
        customer_name="Fixture Customer",
    )

    assert any(f"corpus {field[:-1] if field.endswith('s') else field} count" in item for item in errors)


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_ai_diagnostics_and_evidence_flags_require_literal_json_true(
    invalid: Any,
) -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def get_json(self, _path: str) -> tuple[int, dict[str, Any]]:
            self.calls += 1
            if self.calls == 1:
                payload: dict[str, Any] = {"ok": True}
            else:
                payload = {"ok": True, "record": {"source_id": "AP-001"}}
            _set_or_remove(payload, "ok", invalid)
            return 200, payload

    evidence_ok, diagnostics_ok, _evidence = (
        ai_acceptance._lookup_evidence_and_diagnostics(  # noqa: SLF001
            Client(),  # type: ignore[arg-type]
            {
                "query_id": "valid-query_123",
                "answer": "One open plan. [Sources: AP-001]",
            },
        )
    )

    assert diagnostics_ok is False
    assert evidence_ok is False


def _iteration_args(tmp_path: Path) -> Namespace:
    return Namespace(
        base_url="http://127.0.0.1:5151",
        downloads_dir=str(tmp_path),
        iterations=1,
        scenarios="all",
        poll_interval=1.0,
        timeout=60,
        run_id="round168",
        stop_on_failure=False,
        baseline_mode="off",
        min_docx_similarity=0.35,
        min_sheet_overlap=0.5,
        min_header_similarity=0.3,
        min_docx_chars=200,
        strict=False,
        min_docx_numeric_similarity=0.8,
        min_docx_table_numeric_similarity=0.95,
        max_xlsx_row_delta_ratio=0.2,
        max_xlsx_row_delta_abs=25,
    )


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_iteration_start_requires_literal_json_true(
    tmp_path: Path,
    invalid: Any,
) -> None:
    payload: dict[str, Any] = {"success": True, "analysis_id": "fixture"}
    _set_or_remove(payload, "success", invalid)

    class Session:
        def post(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response(payload)

    runner = iteration.LiveReportRunner(iteration.build_runner_config(_iteration_args(tmp_path)))
    runner.csrf_token = "fixture"
    runner.session = Session()  # type: ignore[assignment]
    scenario = iteration.Scenario("fixture", "/start_analysis", "form", {})

    with pytest.raises(RuntimeError, match="Scenario start failed"):
        runner._start_scenario(scenario)  # noqa: SLF001


@pytest.mark.parametrize("flag", ("word_available", "excel_available"))
@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_iteration_artifact_availability_requires_literal_json_true(
    tmp_path: Path,
    flag: str,
    invalid: Any,
) -> None:
    status: dict[str, Any] = {
        "status": "completed",
        "word_available": True,
        "excel_available": True,
    }
    _set_or_remove(status, flag, invalid)
    runner = iteration.LiveReportRunner(iteration.build_runner_config(_iteration_args(tmp_path)))
    runner._start_scenario = lambda _scenario: ("", {})  # type: ignore[method-assign]
    runner._poll_status = lambda _analysis_id: (status, [])  # type: ignore[method-assign]
    scenario = iteration.Scenario("fixture", "/start_analysis", "form", {})

    result = runner.run_scenario(scenario)

    assert result.operational.passed is False
    assert result.all_passed is False
    assert result.failure_phase == "operational_gate"


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_iteration_version_preflight_requires_literal_json_true(
    invalid: Any,
) -> None:
    version: dict[str, Any] = {
        "ok": True,
        "version": "fixture",
        "build": "168",
        "process_started_at_utc": "2026-08-14T00:00:00Z",
        "restart_required": False,
    }
    _set_or_remove(version, "ok", invalid)

    class Session:
        def get(self, url: str, *, timeout: float) -> _Response:
            del timeout
            if url.endswith("/ping"):
                return _Response(status_code=200, text="OK")
            if url.endswith("/api/version"):
                return _Response(version)
            if url.endswith("/api/status/all"):
                return _Response({"analyses": []})
            if url.endswith(("/api/corpus/status", "/api/intel/status")):
                return _Response({"boot": {}})
            raise AssertionError(url)

    payload, gate = iteration.evaluate_app_health(
        Session(),  # type: ignore[arg-type]
        "http://127.0.0.1:5151",
    )

    assert gate.passed is False
    assert "/api/version" in payload["failed_required_paths"]


@pytest.mark.parametrize("invalid", INVALID_TRUE_FLAGS)
def test_iteration_main_requires_literal_projected_pass(
    monkeypatch: pytest.MonkeyPatch,
    invalid: Any,
) -> None:
    monkeypatch.setattr(iteration, "build_runner_config", lambda _args: object())
    summary: dict[str, Any] = {"all_passed": True}
    _set_or_remove(summary, "all_passed", invalid)
    monkeypatch.setattr(iteration, "run_iterations", lambda _config: summary)

    assert iteration.main([]) == 2


@pytest.mark.parametrize("invalid", (True, 1.0, "1", None, MISSING))
def test_iteration_baseline_manifest_version_requires_exact_json_integer(
    tmp_path: Path,
    invalid: Any,
) -> None:
    payload: dict[str, Any] = {"version": 1, "scenarios": {}}
    _set_or_remove(payload, "version", invalid)
    manifest = tmp_path / "baseline.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="version"):
        iteration.load_baseline_manifest(manifest)


@pytest.mark.parametrize("invalid", (True, 4.0, "4", None, MISSING))
def test_iteration_baseline_size_requires_exact_json_integer(
    tmp_path: Path,
    invalid: Any,
) -> None:
    artifact = tmp_path / "fixture.docx"
    artifact.write_bytes(b"data")
    entry: dict[str, Any] = {
        "path": artifact.name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size_bytes": artifact.stat().st_size,
    }
    _set_or_remove(entry, "size_bytes", invalid)
    manifest = tmp_path / "baseline.json"
    manifest.write_text(
        json.dumps(
            {"version": 1, "scenarios": {"fixture": {"docx": entry}}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="positive size_bytes"):
        iteration.load_baseline_manifest(manifest)
