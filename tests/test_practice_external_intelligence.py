"""Round 179: practice-aware External Intelligence HTTP and default parity contracts."""

from __future__ import annotations

import io
import hashlib
import json
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest

import adoptiq_backend
import adoptiq_settings
import app_simple
import incident_storage
import practice_config


_ACTIONS = (
    ("POST", "/api/refresh-external-intel"),
    ("POST", "/api/ask-intel"),
    ("GET", "/api/export-intel"),
    ("POST", "/api/import-intel"),
)


@pytest.fixture(autouse=True)
def _default_practice(monkeypatch):
    monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)


@pytest.fixture
def blocked_services(monkeypatch):
    """Security requests must not read/write shared data, fetch feeds, or call AI."""
    mocks = []
    for module, names in (
        (incident_storage, (
            "get_incident_statistics", "get_maintenance_statistics",
            "get_all_external_intel", "export_all_data", "import_all_data",
        )),
        (adoptiq_backend, (
            "fetch_status_incidents", "fetch_help_webex_bugs", "fetch_status_maintenances",
        )),
        (app_simple, (
            "_r144_start_external_intel_refresh", "run_intel_grounded_ask_ai",
            "generate_llm_response", "_check_ask_ai_throttle",
        )),
    ):
        for name in names:
            mock = Mock(side_effect=AssertionError(f"Security called {name}"))
            monkeypatch.setattr(module, name, mock)
            mocks.append(mock)
    yield
    for mock in mocks:
        mock.assert_not_called()


@pytest.fixture
def collaboration_intel(monkeypatch):
    inc_stats = {"total": 12, "active": 1, "resolved": 11, "newest": "9999-01-01"}
    maint_stats = {"total": 2, "newest": "9999-01-01"}
    stored = {
        "incidents": [{"title": "Collaboration cached incident", "published": "2026-09-01"}],
        "bugs": [],
        "maintenances": [],
        "incident_stats": inc_stats,
        "bug_stats": {"total": 0},
        "maintenance_stats": maint_stats,
    }
    monkeypatch.setattr(incident_storage, "get_incident_statistics", lambda: inc_stats)
    monkeypatch.setattr(incident_storage, "get_maintenance_statistics", lambda: maint_stats)
    get_intel = Mock(return_value=stored)
    monkeypatch.setattr(incident_storage, "get_all_external_intel", get_intel)
    refresh = Mock(return_value="started")
    monkeypatch.setattr(app_simple, "_r144_start_external_intel_refresh", refresh)
    return get_intel, refresh


@pytest.mark.parametrize(("setting", "env", "expected"), [
    (None, None, "collaboration"),
    (None, "collaboration", "collaboration"),
    (None, "invalid", "collaboration"),
    (None, "security", "security"),
    (None, " SECURITY ", "security"),
    ("collaboration", "security", "collaboration"),
    ("security", "collaboration", "security"),
    ("security", None, "security"),
])
def test_page_resolves_practice_from_ssot(
    client, monkeypatch, collaboration_intel, setting, env, expected,
):
    if setting is not None:
        adoptiq_settings.save_settings({"practice": setting})
    if env is not None:
        monkeypatch.setenv("ADOPTIQ_PRACTICE", env)
    response = client.get("/external-intelligence?days=90&practice=collaboration")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    get_intel, refresh = collaboration_intel
    refresh.assert_not_called()
    if expected == "security":
        assert "Security practice" in html
        assert "External intelligence unavailable" in html
        assert "Coverage is unknown" in html
        assert "Collaboration cached incident" not in html
        get_intel.assert_not_called()
    else:
        assert "Collaboration cached incident" in html
        assert "Refresh Live Data" in html
        assert "https://status.webex.com" in html
        assert "https://help.webex.com" in html
        assert "External intelligence unavailable" not in html
        get_intel.assert_called_once_with(days_back=90)


def test_security_page_has_no_feed_controls_counts_or_collaboration_data(
    client, monkeypatch, blocked_services,
):
    monkeypatch.setenv("ADOPTIQ_PRACTICE", "security")
    response = client.get("/external-intelligence")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")
    notice = page.find("div", attrs={"role": "status", "aria-labelledby": "intel-unavailable-title"})
    assert notice is not None
    assert "have not been verified" in notice.get_text()
    assert "does not mean there are no incidents" in notice.get_text()
    for token in ("status.webex.com", "help.webex.com", "statTotalIncidents", "askAiBtn",
                  "/api/refresh-external-intel", "/api/ask-intel", "/api/import-intel", "/api/export-intel"):
        assert token not in html


def test_practice_change_applies_to_next_request_without_restart(
    client, monkeypatch, collaboration_intel,
):
    monkeypatch.setenv("ADOPTIQ_PRACTICE", "collaboration")
    assert b"Collaboration cached incident" in client.get("/external-intelligence").data
    adoptiq_settings.save_settings({"practice": "security"})
    assert b"Security practice" in client.get("/external-intelligence").data
    adoptiq_settings.save_settings({"practice": "collaboration"})
    assert b"Collaboration cached incident" in client.get("/external-intelligence").data


@pytest.mark.parametrize("practice", [None, "collaboration"])
def test_collaboration_page_matches_pre_slice_render(
    client, monkeypatch, collaboration_intel, practice,
):
    if practice is not None:
        monkeypatch.setenv("ADOPTIQ_PRACTICE", practice)
    response = client.get("/external-intelligence?days=90")
    assert response.status_code == 200
    main = response.get_data(as_text=True).split('<main id="main-content" tabindex="-1">', 1)[1].split("</main>", 1)[0]
    # Round 191: recaptured after merging #25 @ 420f24b (Round 185/186 date,
    # aria-label, and fixture-label polish on the Collaboration template).
    # Still pins that Slice 2 does not alter Collaboration intel HTML vs that
    # baseline. The page body excludes the shared shell CSRF/build footer.
    assert hashlib.sha256(main.encode()).hexdigest() == "f9d59a1a9d75415ed09b6375d9fc6dfaa61fcad20af44ae75c2188521b0a4899"


@pytest.mark.parametrize(("method", "path"), _ACTIONS)
def test_security_actions_reject_client_override_before_any_work(
    client, monkeypatch, blocked_services, method, path,
):
    monkeypatch.setenv("ADOPTIQ_PRACTICE", "security")
    kwargs = {"json": {"practice": "collaboration", "question": "Summarize incidents"}}
    if path == "/api/import-intel":
        kwargs = {"data": {
            "practice": "collaboration",
            "file": (io.BytesIO(b'{"schema_version": 1, "incidents": []}'), "intel.json"),
        }}
    response = client.open(
        path + "?practice=collaboration", method=method,
        **kwargs,
    )
    assert response.status_code == 409
    assert response.get_json() == {
        "ok": False,
        "success": False,
        "state": "not_configured",
        "error_code": "external_intel_not_configured",
        "error": practice_config.get_external_intel_profile().unavailable_message,
        "practice": "security",
    }
    assert "Content-Disposition" not in response.headers


@pytest.mark.parametrize("path", [path for method, path in _ACTIONS if method == "POST"])
def test_security_actions_preserve_csrf_checks(client, monkeypatch, blocked_services, path):
    monkeypatch.setenv("ADOPTIQ_PRACTICE", "security")
    monkeypatch.setitem(app_simple.app.config, "WTF_CSRF_ENABLED", True)
    rejected = client.post(path, json={})
    assert rejected.status_code == 403
    assert rejected.get_json()["error"] == "CSRF validation failed"
    page = BeautifulSoup(client.get("/external-intelligence").data, "html.parser")
    token = page.find("meta", attrs={"name": "csrf-token"})["content"]
    response = client.post(path, json={}, headers={"X-CSRFToken": token})
    assert response.status_code == 409
    assert response.get_json()["state"] == "not_configured"


@pytest.mark.parametrize(("method", "path"), _ACTIONS)
def test_security_actions_preserve_local_access_checks(
    client, monkeypatch, blocked_services, method, path,
):
    monkeypatch.setenv("ADOPTIQ_PRACTICE", "security")
    response = client.open(path, method=method, environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "Forbidden: local access only"


def test_collaboration_cold_page_still_queues_refresh(client, monkeypatch, collaboration_intel):
    monkeypatch.setattr(incident_storage, "get_incident_statistics", lambda: {"total": 0, "newest": ""})
    monkeypatch.setattr(incident_storage, "get_maintenance_statistics", lambda: {"total": 0, "newest": ""})
    response = client.get("/external-intelligence?days=invalid")
    assert response.status_code == 200
    assert b"refreshing in the background" in response.data
    get_intel, refresh = collaboration_intel
    get_intel.assert_called_once_with(days_back=365)
    refresh.assert_called_once_with()


def test_collaboration_refresh_keeps_response_and_all_three_feeds(client, monkeypatch):
    fetches = []
    for name in ("fetch_status_incidents", "fetch_help_webex_bugs", "fetch_status_maintenances"):
        fetch = Mock(return_value=[{"title": "Collaboration fixture"}])
        monkeypatch.setattr(adoptiq_backend, name, fetch)
        fetches.append(fetch)
    response = client.post("/api/refresh-external-intel", json={})
    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True, "success": True, "partial": False, "incidents": 1, "bugs": 1, "maintenances": 1,
        "incidents_state": "present", "bugs_state": "present", "maintenances_state": "present",
        "fetch_errors": {"incidents": [], "bugs": [], "maintenances": []},
    }
    for fetch in fetches:
        fetch.assert_called_once_with(timeout=20)


def test_collaboration_export_and_import_keep_payloads(client, monkeypatch):
    data = {"schema_version": 1, "incidents": [{"title": "Collaboration fixture"}]}
    export = Mock(return_value=data)
    monkeypatch.setattr(incident_storage, "export_all_data", export)
    response = client.get("/api/export-intel?index=1&page=2&page_size=10")
    assert response.status_code == 200
    assert response.get_json() == data
    assert 'attachment; filename="AdoptIQ-Intel-Export-' in response.headers["Content-Disposition"]
    export.assert_called_once_with(page=1, page_size=10)

    imported = Mock(return_value={"incidents": 1})
    monkeypatch.setattr(incident_storage, "import_all_data", imported)
    response = client.post("/api/import-intel", data={
        "file": (io.BytesIO(json.dumps(data).encode()), "intel.json"),
    })
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "success": True, "imported": {"incidents": 1}}
    imported.assert_called_once_with(data)


def test_collaboration_ask_intel_still_uses_grounded_evidence(client, monkeypatch):
    monkeypatch.setattr(app_simple, "_check_ask_ai_throttle", lambda: None)
    monkeypatch.setattr(app_simple, "is_grounded_ask_ai_enabled", lambda: True)
    grounded = Mock(return_value={"ok": True, "answer": "Stored incident [INC-1]"})
    monkeypatch.setattr(app_simple, "run_intel_grounded_ask_ai", grounded)
    response = client.post("/api/ask-intel", json={"question": "Summarize incidents", "days": 30})
    assert response.status_code == 200
    assert response.get_json()["answer"] == "Stored incident [INC-1]"
    grounded.assert_called_once_with("Summarize incidents", days=30)


def test_external_intel_profile_honors_bound_admission_snapshot(monkeypatch, tmp_path):
    """Round 191: bound workers keep admitted practice; HTTP stays live."""
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
    adoptiq_settings.save_settings({"practice": "collaboration"})
    collab_snap = practice_config.capture_practice_filter_snapshot()
    adoptiq_settings.save_settings({"practice": "security"})
    security_snap = practice_config.capture_practice_filter_snapshot()

    live = practice_config.get_external_intel_profile()
    assert live.practice == "security"
    assert live.enabled is False

    with practice_config.bound_job_practice_snapshot(collab_snap):
        bound = practice_config.get_external_intel_profile()
    assert bound.practice == "collaboration"
    assert bound.enabled is True
    assert practice_config.get_external_intel_profile().practice == "security"

    adoptiq_settings.save_settings({"practice": "collaboration"})
    with practice_config.bound_job_practice_snapshot(security_snap):
        bound_sec = practice_config.get_external_intel_profile()
    assert bound_sec.practice == "security"
    assert bound_sec.enabled is False
    assert practice_config.get_external_intel_profile().practice == "collaboration"


def test_external_intel_http_routes_are_not_report_start_gates():
    """Round 191: intel page/APIs are new HTTP requests, not shared start-gate routes."""
    import inspect

    for name in (
        "external_intelligence",
        "refresh_external_intel",
        "export_intel",
        "ask_intel",
        "import_intel",
        "_external_intel_unavailable_response",
    ):
        src = inspect.getsource(getattr(app_simple, name))
        assert "_slice1_reject_if_collaboration_technology_unavailable" not in src
        assert "capture_practice_filter_snapshot" not in src
    helper = inspect.getsource(practice_config.get_external_intel_profile)
    assert "get_bound_job_practice_snapshot" in helper
    assert "get_active_practice" in helper
