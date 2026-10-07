"""Push 2004 / Round 192: JSON start_analysis parity, ask-intel days, intel windows."""

from __future__ import annotations

import inspect
import threading

import adoptiq_settings
import app_simple
import incident_storage
import practice_config as pc
import pytest


@pytest.fixture
def security_practice(monkeypatch, tmp_path):
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
    adoptiq_settings.save_settings({"practice": "security"})
    assert pc.get_active_practice() == pc.PRACTICE_SECURITY


def test_start_analysis_json_client_uses_shared_practice_gate():
    """Round 192: JSON clients still go through the Round 190 shared gate."""
    src = inspect.getsource(app_simple.start_analysis)
    assert "_slice1_reject_if_collaboration_technology_unavailable" in src
    assert "_blocked, _admitted_practice" in src
    assert "capture_practice_filter_snapshot" not in src
    assert "_slice1_admitted_practice_snapshot_payload(_admitted_practice)" in src
    assert "is_json_client = is_ajax or request.is_json" in src
    gate_src = inspect.getsource(app_simple._slice1_reject_if_collaboration_technology_unavailable)
    assert "capture_practice_filter_snapshot" in gate_src
    ask_src = inspect.getsource(app_simple.ask_intel)
    assert "_slice1_reject_if_collaboration_technology_unavailable" not in ask_src
    intel_src = inspect.getsource(app_simple.external_intelligence)
    assert "_slice1_reject_if_collaboration_technology_unavailable" not in intel_src


def test_start_analysis_security_json_without_ajax_header_returns_409(client, security_practice):
    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
    resp = client.post(
        "/start_analysis",
        json={"manager": "Brian Frazier", "technology": "All", "report_type": "comprehensive"},
    )
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["error"] == pc.ERROR_COLLAB_TECH_UNAVAILABLE
    assert data["ok"] is False
    with app_simple.analysis_status_lock:
        assert app_simple.analysis_status == {}


def test_start_analysis_json_without_ajax_header_persists_admitted_snapshot(
    client, monkeypatch, tmp_path
):
    """Round 192: JSON-without-AJAX still queues the gate-captured snapshot."""
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
    adoptiq_settings.save_settings({"practice": "collaboration"})

    def _flip_folder_diag(*_args, **_kwargs):
        adoptiq_settings.save_settings({"practice": "security"})
        assert pc.get_active_practice() == pc.PRACTICE_SECURITY
        return None, "unknown", 0

    monkeypatch.setattr(app_simple, "get_latest_csone_from_folder_diag", _flip_folder_diag)

    class _NoWorker:
        def __init__(self, *args, **kwargs):
            self.daemon = False

        def start(self):
            return None

    monkeypatch.setattr(threading, "Thread", _NoWorker)
    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()

    resp = client.post(
        "/start_analysis",
        json={
            "manager": "Brian Frazier",
            "technology": "Webex Calling",
            "days": 90,
            "report_type": "comprehensive",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    assert pc.get_active_practice() == pc.PRACTICE_SECURITY
    with app_simple.analysis_status_lock:
        assert len(app_simple.analysis_status) == 1
        status = next(iter(app_simple.analysis_status.values()))
        raw = status.get("practice_filter_snapshot")
    snapshot = pc.practice_filter_snapshot_from_mapping(raw)
    assert snapshot is not None
    assert snapshot.practice == pc.PRACTICE_COLLABORATION
    assert snapshot.pack_available is True
    assert "Webex Calling" in snapshot.backend_tech_filters


def test_start_analysis_json_client_uses_json_validation_not_wtforms(client, monkeypatch, tmp_path):
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
    monkeypatch.setitem(app_simple.app.config, "WTF_CSRF_ENABLED", False)
    resp = client.post(
        "/start_analysis",
        json={
            "manager": "Not A Real Manager",
            "technology": "All",
            "report_type": "comprehensive",
        },
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "Invalid manager" in data["error"]


def test_ask_intel_rejects_invalid_days(client, monkeypatch):
    monkeypatch.setitem(app_simple.app.config, "WTF_CSRF_ENABLED", False)
    for payload, fragment in (
        ({"question": "Summarize incidents", "days": "not-a-number"}, "valid number"),
        ({"question": "Summarize incidents", "days": 0}, "between 1 and 365"),
        ({"question": "Summarize incidents", "days": 9999}, "between 1 and 365"),
    ):
        resp = client.post("/api/ask-intel", json=payload)
        assert resp.status_code == 400
        assert fragment in resp.get_json()["error"]


def test_normalize_external_intel_days_back_rejects_non_positive():
    assert app_simple.normalize_external_intel_days_back(-30) == 365
    assert app_simple.normalize_external_intel_days_back(0) == 365
    assert app_simple.normalize_external_intel_days_back(90) == 90


def test_external_intelligence_negative_days_query_uses_default_window(client, monkeypatch):
    inc_stats = {"total": 12, "active": 1, "resolved": 11, "newest": "9999-01-01"}
    maint_stats = {"total": 2, "newest": "9999-01-01"}
    stored = {
        "incidents": [],
        "bugs": [],
        "maintenances": [],
        "incident_stats": inc_stats,
        "bug_stats": {"total": 0},
        "maintenance_stats": maint_stats,
    }
    monkeypatch.setattr(incident_storage, "get_incident_statistics", lambda: inc_stats)
    monkeypatch.setattr(incident_storage, "get_maintenance_statistics", lambda: maint_stats)
    calls = []

    def _get_all(days_back=365):
        calls.append(days_back)
        return stored

    monkeypatch.setattr(incident_storage, "get_all_external_intel", _get_all)
    monkeypatch.setattr(app_simple, "_r144_start_external_intel_refresh", lambda: None)
    client.get("/external-intelligence?days=-90")
    assert calls == [365]
