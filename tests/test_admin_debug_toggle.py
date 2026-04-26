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
def test_admin_debug_proxy_get(monkeypatch):
    monkeypatch.setattr(
        admin_mod.requests,
        "get",
        lambda *args, **kwargs: _FakeResp(200, {"success": True, "verbose_debug": True}),
    )
    client = admin_mod.admin_app.test_client()
    rv = client.get("/api/debug/verbose")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert data["verbose_debug"] is True


@pytest.mark.flask
def test_admin_debug_proxy_post(monkeypatch):
    monkeypatch.setattr(
        admin_mod.requests,
        "post",
        lambda *args, **kwargs: _FakeResp(200, {"success": True, "verbose_debug": False}),
    )
    client = admin_mod.admin_app.test_client()
    # Round 8 / Phase 4.7: api_debug_verbose POST now requires the
    # per-session admin CSRF token.  Mint one via the session and
    # forward it as the X-AdoptIQ-Admin-CSRF header.
    with client.session_transaction() as sess:
        sess['_admin_csrf'] = 'test-csrf-token'
    rv = client.post(
        "/api/debug/verbose",
        json={"enabled": False},
        headers={'X-AdoptIQ-Admin-CSRF': 'test-csrf-token'},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert data["verbose_debug"] is False


@pytest.mark.flask
def test_admin_debug_proxy_post_rejects_missing_csrf():
    """Round 8 / Phase 4.7 regression: a POST without the CSRF token
    must be rejected with HTTP 403 to prevent cross-origin abuse of
    the verbose-debug toggle.
    """
    client = admin_mod.admin_app.test_client()
    rv = client.post("/api/debug/verbose", json={"enabled": False})
    assert rv.status_code == 403
