"""Tests for the admin dashboard proxy of ``/api/diag/connectivity``.

Mirrors the pattern used by ``tests/test_admin_debug_toggle.py``:
monkeypatch ``requests.get`` so no real network traffic happens.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import enhanced_admin_dashboard_v2 as admin_mod


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.flask
def test_admin_diag_proxy_passes_through_success(monkeypatch):
    payload = {
        "ok": True,
        "checks": [
            {"name": "dns_keeper", "status": "ok", "ms": 15, "detail": "resolved"},
            {"name": "tcp_tls_keeper", "status": "ok", "ms": 180, "detail": "subject=keeper"},
            {"name": "keeper_approle_login", "status": "ok", "ms": 300},
            {"name": "keeper_secret_read", "status": "ok", "ms": 250},
            {"name": "snowflake_select_now", "status": "ok", "ms": 900},
        ],
        "hint": "All checks passed.",
        "truststore_active": True,
    }
    monkeypatch.setattr(
        admin_mod.requests,
        "get",
        lambda *args, **kwargs: _FakeResp(200, payload),
    )
    client = admin_mod.admin_app.test_client()
    rv = client.get("/api/diag/connectivity")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert len(data["checks"]) == 5
    assert data["truststore_active"] is True


@pytest.mark.flask
def test_admin_diag_proxy_passes_through_failure(monkeypatch):
    payload = {
        "ok": False,
        "checks": [
            {"name": "dns_keeper", "status": "ok", "ms": 12, "detail": "ok"},
            {
                "name": "tcp_tls_keeper",
                "status": "fail",
                "ms": 150,
                "error_kind": "tls_cert_verify_failed",
                "detail": "SSLCertVerificationError",
            },
        ],
        "hint": "TLS cert verification failed.",
    }
    monkeypatch.setattr(
        admin_mod.requests,
        "get",
        lambda *args, **kwargs: _FakeResp(503, payload),
    )
    client = admin_mod.admin_app.test_client()
    rv = client.get("/api/diag/connectivity")
    assert rv.status_code == 503
    data = rv.get_json()
    assert data["ok"] is False
    assert data["checks"][1]["error_kind"] == "tls_cert_verify_failed"


@pytest.mark.flask
def test_admin_diag_proxy_502s_when_main_app_down(monkeypatch):
    def _raise(*args, **kwargs):
        raise ConnectionRefusedError("main app not running")

    monkeypatch.setattr(admin_mod.requests, "get", _raise)
    client = admin_mod.admin_app.test_client()
    rv = client.get("/api/diag/connectivity")
    assert rv.status_code == 502
    data = rv.get_json()
    assert data["ok"] is False
    assert "Unable to reach" in data.get("error", "")
