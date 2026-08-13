"""Round 145 real-route coverage for the guarded local runtime adapters."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import local_acceptance_lab as lab
import local_acceptance_runtime as runtime


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app_module():
    import app_simple

    original_csrf = app_simple.app.config.get("WTF_CSRF_ENABLED", True)
    original_testing = app_simple.app.config.get("TESTING", False)
    app_simple.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    yield app_simple
    app_simple.app.config.update(
        TESTING=original_testing,
        WTF_CSRF_ENABLED=original_csrf,
    )


@pytest.fixture()
def healthy_runtime(app_module):
    bundle = lab.build_scenario_bundle("healthy")
    installation = runtime.install_runtime_adapters(bundle, app_module)
    try:
        yield bundle, app_module, app_module.app.test_client()
    finally:
        installation.restore()


def test_runtime_installation_is_explicit_reversible_and_fixture_stamped(app_module) -> None:
    bundle = lab.build_scenario_bundle("healthy")
    original = app_module.get_subscriptions_for_team

    installation = runtime.install_runtime_adapters(bundle, app_module)
    try:
        assert app_module.app.config["LOCAL_ACCEPTANCE_MODE"] is True
        frame = app_module.get_subscriptions_for_team(
            runtime._LocalConnection(),  # noqa: SLF001 - adapter contract test
            ["fixture.owner1@example.invalid"],
        )
        assert set(frame["FIXTURE_MEMBER"]) == {"Alex Rivera"}
        assert frame.attrs["source_mode"] == lab.SOURCE_MODE
        assert frame.attrs["live_validation_performed"] is False
        prefetch_context = app_module.AnalysisRunContext.build(
            runtime._LocalConnection(),  # noqa: SLF001 - adapter contract test
            ["ACC-001"],
            90,
        )
        assert prefetch_context.data_retrieved_at.isoformat().replace("+00:00", "Z") == bundle.as_of_utc
    finally:
        installation.restore()

    assert app_module.get_subscriptions_for_team is original
    assert "LOCAL_ACCEPTANCE_MODE" not in app_module.app.config


def test_runtime_exposes_real_all_managers_sentinel_and_two_manager_roster(app_module) -> None:
    installation = runtime.install_runtime_adapters(lab.build_scenario_bundle("multi_manager"), app_module)
    try:
        assert app_module.MANAGERS == [
            "All Managers",
            "Local Fixture Manager",
            "Second Fixture Manager",
        ]
        assert {manager for manager, _name, _email in app_module.TEAM_ROSTER} == {
            "Local Fixture Manager",
            "Second Fixture Manager",
        }
    finally:
        installation.restore()


def test_runtime_source_adapters_canonicalize_duplicate_source_ids(app_module) -> None:
    bundle = lab.build_scenario_bundle("healthy")
    installation = runtime.install_runtime_adapters(bundle, app_module)
    try:
        action_plans = app_module.fetch_csconsole_action_plans(
            None,
            ["ACC-001", "ACC-002", "ACC-003"],
            90,
        )
        pulse = app_module.fetch_csconsole_customer_pulse(
            None,
            ["ACC-001", "ACC-002", "ACC-003"],
            90,
        )
        tac_cases = app_module.load_csone_excel(None)
        secondary = app_module._r65_fetch_aps_snowflake(None, [], 90)
    finally:
        installation.restore()

    assert len(bundle.frame("action_plans")) == 8
    assert len(action_plans) == 7
    assert action_plans.loc[action_plans["ID"].fillna("").ne(""), "ID"].is_unique
    assert set(action_plans["CSSM_EMAIL"].dropna()) == {
        "fixture.owner1@example.invalid",
        "fixture.owner2@example.invalid",
    }
    assert len(pulse) == 3
    assert pulse["ID"].is_unique
    assert set(tac_cases["CSSM_EMAIL"].dropna()) == {
        "fixture.owner1@example.invalid",
        "fixture.owner2@example.invalid",
    }
    assert secondary.empty
    assert secondary.attrs["secondary_source_role"] == "no_distinct_fixture_rows"


@pytest.mark.parametrize(
    ("scenario", "dataset", "warning_kind"),
    [
        ("partial", "support_cases", "source_partial"),
        ("stale", "external_incidents", "source_stale"),
        ("unavailable", "customer_pulse", "source_unavailable"),
        ("truncated", "activities", "truncation"),
    ],
)
def test_explicit_source_states_surface_as_honest_fetch_warnings(
    scenario: str,
    dataset: str,
    warning_kind: str,
) -> None:
    from snowflake_prefetch import collect_fetch_warnings

    bundle = lab.build_scenario_bundle(scenario)
    warnings = collect_fetch_warnings({dataset: bundle.frame(dataset)})

    assert any(item["kind"] == warning_kind for item in warnings)
    assert all("traceback" not in item["error"].casefold() for item in warnings)


def test_real_connectivity_corpus_and_intel_status_routes(healthy_runtime) -> None:
    bundle, _app, client = healthy_runtime

    connectivity = client.get("/api/diag/connectivity")
    assert connectivity.status_code == 200
    assert connectivity.get_json()["mode"] == lab.SOURCE_MODE
    assert connectivity.get_json()["live_validation_performed"] is False

    for route in ("/api/corpus/status", "/api/intel/status"):
        response = client.get(route)
        payload = response.get_json()
        assert response.status_code == 200
        assert payload["enabled"] is True
        assert payload["available"] is True
        assert payload["corpus"]["chunks"] == bundle.expected_counts["corpus_chunks"]


def test_real_customer_360_and_playbook_routes_use_fixture_corpus(healthy_runtime) -> None:
    _bundle, _app, client = healthy_runtime

    customer = client.get("/customer/Acme%20Corporation")
    assert customer.status_code == 200
    assert b"Registration authentication outage" in customer.data

    playbook = client.post(
        "/playbook",
        data={
            "technology": "Webex Calling",
            "theme": "configuration",
            "query": "certificate registration",
        },
    )
    assert playbook.status_code == 200
    assert b"certificate chain" in playbook.data.lower()
    assert b"ignore all previous instructions" not in playbook.data.lower()


def test_real_ask_ai_sync_diagnostics_and_exact_evidence_resolution(healthy_runtime) -> None:
    _bundle, _app, client = healthy_runtime
    response = client.post(
        "/api/ask-ai-portfolio",
        json={
            "question": "What needs manager attention?",
            "manager": "Local Fixture Manager",
            "technology": "All",
            "days": 90,
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["mode"] == "grounded"
    assert payload["retrieval_method"] in {"hybrid", "lexical"}
    assert payload["evidence_records"]
    assert len({row["source_id"] for row in payload["evidence_records"]}) == len(payload["evidence_records"])
    citation = re.search(r"\[Sources?:\s*([^,\]]+)", payload["answer"])
    assert citation is not None
    cited_source_id = citation.group(1).strip()
    assert cited_source_id in {row["source_id"] for row in payload["evidence_records"]}

    query_id = payload["query_id"]
    source_id = cited_source_id
    diag = client.get(f"/api/ask-ai/diagnostics/{query_id}")
    evidence = client.get(f"/api/ask-ai/evidence/{query_id}/{source_id}")

    assert diag.status_code == 200
    assert diag.get_json()["retrieval_diag"]["method"] in {"hybrid", "lexical"}
    assert evidence.status_code == 200
    assert evidence.get_json()["record"]["source_id"] == source_id


def test_real_ask_ai_sync_stream_fact_and_evidence_parity(healthy_runtime) -> None:
    _bundle, _app, client = healthy_runtime
    request = {
        "question": "Summarize the Action Plan and support priorities.",
        "manager": "Local Fixture Manager",
        "technology": "All",
        "days": 90,
        "conversation_history": [{"q": "What is the scope?", "a": "Use only the current fixture snapshot."}],
    }
    sync_payload = client.post("/api/ask-ai-portfolio", json=request).get_json()
    stream_response = client.post("/api/ask-ai-portfolio/stream", json=request)
    stream_text = stream_response.get_data(as_text=True)

    assert stream_response.status_code == 200
    assert "event: meta" in stream_text
    assert "event: done" in stream_text
    for record in sync_payload["evidence_records"]:
        assert record["source_id"] in stream_text
    for fact in ("Supported Findings", "Calling migration dependency"):
        assert fact in sync_payload["answer"]
        assert fact in stream_text


def test_unanswerable_question_discloses_evidence_gap(healthy_runtime) -> None:
    _bundle, _app, client = healthy_runtime
    response = client.post(
        "/api/ask-ai-portfolio",
        json={"question": "What is today's stock price?", "days": 90},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert "Insufficient evidence" in payload["answer"]
    assert "no estimate was made" in payload["answer"]


def test_real_ask_intel_external_page_and_export(healthy_runtime) -> None:
    _bundle, _app, client = healthy_runtime
    ask = client.post(
        "/api/ask-intel",
        json={"question": "Which incident should I review?", "days": 90},
    )
    page = client.get("/external-intelligence?days=90")
    export = client.get("/api/export-intel")

    assert ask.status_code == 200
    ask_answer = ask.get_json()["answer"]
    assert "[Sources:" in ask_answer
    assert any(source_id in ask_answer for source_id in ("INC-001", "MAINT-001", "BUG-001"))
    assert page.status_code == 200
    assert b"Synthetic regional service degradation" in page.data
    assert export.status_code == 200
    exported = json.loads(export.data)
    assert exported["sanitized"] is True
    assert exported["live_validation_performed"] is False


def test_real_subscription_search_detail_and_risk_routes(healthy_runtime) -> None:
    _bundle, _app, client = healthy_runtime
    search = client.post(
        "/search_subscriptions",
        json={"customer_name": "Acme", "limit": 10},
    )
    detail = client.get("/subscription_analysis/SUB-001")
    risk = client.get("/subscription_renewal_risk/SUB-001")

    assert search.status_code == 200
    assert search.get_json()["subscriptions"]
    assert detail.status_code == 200
    assert detail.get_json()["subscription_data"]["found"] is True
    assert risk.status_code == 200
    assert risk.get_json()["renewal_analysis"]["found"] is True


@pytest.mark.parametrize(
    ("scenario", "status_code", "safe_error"),
    [
        ("provider_timeout", 504, "timed out"),
        ("provider_rate_limit", 429, "rate limited"),
        ("provider_unavailable", 503, "unavailable"),
        ("provider_malformed", 502, "unusable response"),
    ],
)
def test_provider_failures_are_honest_and_do_not_leak_raw_errors(
    app_module,
    scenario: str,
    status_code: int,
    safe_error: str,
) -> None:
    installation = runtime.install_runtime_adapters(lab.build_scenario_bundle(scenario), app_module)
    try:
        response = app_module.app.test_client().post(
            "/api/ask-ai-portfolio",
            json={"question": "What needs attention?", "days": 90},
        )
    finally:
        installation.restore()

    payload = response.get_json()
    assert response.status_code == status_code
    assert payload["ok"] is False
    assert safe_error in payload["error"]
    serialized = json.dumps(payload).casefold()
    assert "traceback" not in serialized
    assert "/users/" not in serialized


def test_runtime_runner_fails_closed_without_explicit_flag() -> None:
    process = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_local_acceptance_app.py"),
            "--validate-only",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 2
    assert json.loads(process.stderr)["error_kind"] == "LocalAcceptanceSafetyError"


def test_report_ai_classifier_returns_bounded_deterministic_json(app_module) -> None:
    installation = runtime.install_runtime_adapters(lab.build_scenario_bundle("healthy"), app_module)
    try:
        raw = app_module.generate_llm_response(
            "You are a Cisco backend-engineering triage analyst.",
            "[AB-001 - score 91.0]\nTitle: fixture\n[AB-002 - score 72.5]\nTitle: fixture",
        )
        payload = json.loads(raw)
    finally:
        installation.restore()

    assert [item["id"] for item in payload] == ["AB-001", "AB-002"]
    assert {item["class"] for item in payload} == {"TRUE_BLOCKER"}


def test_compact_contract_alias_and_fixture_csone_path_are_explicit(app_module) -> None:
    installation = runtime.install_runtime_adapters(lab.build_scenario_bundle("healthy"), app_module)
    try:
        barriers = app_module.fetch_adoption_barriers(None, ["ACC-001"], 90)
        fixture_path = app_module.get_latest_csone_from_folder()
        resolved = app_module._resolve_csone_path_safe(fixture_path)
    finally:
        installation.restore()

    assert barriers["customer_name"].tolist() == ["Acme Corporation"]
    assert resolved == fixture_path
    assert fixture_path.endswith("tests/fixtures/local_acceptance/v1/manifest.json")


def test_csone_fixture_exposes_production_technology_scope_columns(app_module) -> None:
    installation = runtime.install_runtime_adapters(lab.build_scenario_bundle("healthy"), app_module)
    try:
        cases = app_module.load_csone_excel("ignored-local-pointer")
        team_subscriptions = app_module.get_subscriptions_for_team(
            None,
            [
                "fixture.owner1@example.invalid",
                "fixture.owner2@example.invalid",
            ],
        )
        prepared = app_module._prepare_csone(cases, team_subscriptions)
        scoped = app_module._apply_scope_filter_csone(
            prepared,
            "All Contact Center",
            90,
            team_subscriptions["SUBSCRIPTION_ID"].tolist(),
            team_subscriptions["BU_NAME"].tolist(),
        )
    finally:
        installation.restore()

    assert {"Technology", "Sub Technology"}.issubset(cases.columns)
    assert scoped["SR Number"].tolist() == [
        "SR-003",
        "SR-004",
        "SR-005",
        "SR-006",
    ]
