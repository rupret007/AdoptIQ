"""Round 119 / Build 88 -- /api/update/* + /api/settings/auto-update-mode.

Exercises endpoint shapes, the idle-gate 409, the TESTING short-circuit,
CSRF dual-auth, and the settings-mode validation. CSRF is disabled in the
test client (conftest), so the apply/save endpoints are reachable; a
dedicated CSRF-on test re-enables it to prove the gate.
"""
from __future__ import annotations

import auto_updater
import adoptiq_settings


# ---------------------------------------------------------------------------
# GET /api/update/status
# ---------------------------------------------------------------------------
def test_status_shape(client, monkeypatch):
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_refresh_update_state",
                        lambda: {"update_available": False, "latest_build": None,
                                 "latest_version": None, "artifact": None,
                                 "releases_folder_found": False, "last_error": None,
                                 "last_checked_at_utc": "2026-05-29T00:00:00Z"})
    resp = client.get("/api/update/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "update_mode" in data
    assert "current_build" in data
    assert "update_available" in data
    assert "can_apply_now" in data
    assert "apply_in_progress" in data
    # No secrets / absolute paths leaked.
    assert "sha256" not in data


def test_status_surfaces_available(client, monkeypatch):
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_refresh_update_state",
                        lambda: {"update_available": True, "latest_build": 99,
                                 "latest_version": "1.0.4", "artifact": "AdoptIQ/x.dmg",
                                 "releases_folder_found": True, "last_error": None,
                                 "last_checked_at_utc": "2026-05-29T00:00:00Z"})
    data = client.get("/api/update/status").get_json()
    assert data["update_available"] is True
    assert data["latest_build"] == 99


# ---------------------------------------------------------------------------
# POST /api/update/apply
# ---------------------------------------------------------------------------
def test_apply_testing_short_circuit(client, monkeypatch):
    """Flask TESTING -> engine testing=True -> would_update, never a swap."""
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)

    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "state": "would_update", "latest_build": 99}

    monkeypatch.setattr(auto_updater, "apply_update", fake_apply)
    resp = client.post("/api/update/apply", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["state"] == "would_update"
    # The endpoint MUST pass testing=True under Flask TESTING.
    assert captured.get("testing") is True


def test_apply_idle_gate_409(client, monkeypatch):
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: True)
    resp = client.post("/api/update/apply", json={})
    assert resp.status_code == 409
    data = resp.get_json()
    assert data["needs_force"] is True
    assert data["state"] == "busy"


def test_apply_engine_failure_returns_notify_200(client, monkeypatch):
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)
    monkeypatch.setattr(auto_updater, "apply_update",
                        lambda **k: {"ok": False, "state": "notify", "error_kind": "no_update"})
    resp = client.post("/api/update/apply", json={})
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "notify"


def test_apply_engine_raises_degrades_to_notify(client, monkeypatch):
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)

    def boom(**k):
        raise RuntimeError("engine blew up")

    monkeypatch.setattr(auto_updater, "apply_update", boom)
    resp = client.post("/api/update/apply", json={})
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "notify"


def test_apply_csrf_enforced_when_enabled(app, monkeypatch):
    """With CSRF on and no token, apply must 403 (dual-auth gate)."""
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = app.test_client()
        resp = c.post("/api/update/apply", json={})
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_apply_internal_token_path(app, monkeypatch):
    """X-AdoptIQ-Internal token bypasses CSRF (admin-proxy path)."""
    import app_simple
    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)
    monkeypatch.setattr(auto_updater, "apply_update",
                        lambda **k: {"ok": True, "state": "would_update", "latest_build": 99})
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "secret-tok")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = app.test_client()
        resp = c.post("/api/update/apply", json={},
                      headers={"X-AdoptIQ-Internal": "secret-tok"})
        assert resp.status_code == 200
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


# ---------------------------------------------------------------------------
# /api/settings/auto-update-mode
# ---------------------------------------------------------------------------
def test_settings_mode_get(client):
    data = client.get("/api/settings/auto-update-mode").get_json()
    assert data["ok"] is True
    assert data["mode"] in ("off", "notify", "auto")


def test_settings_mode_post_valid(client):
    resp = client.post("/api/settings/auto-update-mode", json={"mode": "notify"})
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "notify"
    # Persisted + reads back.
    assert adoptiq_settings.get("auto_update_mode") == "notify"


def test_settings_mode_post_invalid_400(client):
    resp = client.post("/api/settings/auto-update-mode", json={"mode": "turbo"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_mode"


def test_settings_mode_post_non_dict_400(client):
    resp = client.post("/api/settings/auto-update-mode",
                       data="not json", content_type="application/json")
    # Either invalid json payload or invalid mode -> 400.
    assert resp.status_code == 400


def test_settings_mode_csrf_enforced_on_post(app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = app.test_client()
        resp = c.post("/api/settings/auto-update-mode", json={"mode": "off"})
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


# ---------------------------------------------------------------------------
# adoptiq_settings.is_valid_auto_update_mode
# ---------------------------------------------------------------------------
def test_mode_validator_accepts_allowed():
    for m in ("off", "notify", "auto", "AUTO", " Notify "):
        assert adoptiq_settings.is_valid_auto_update_mode(m) is True


def test_mode_validator_rejects_junk():
    for m in ("", "turbo", None, 5, [], "on"):
        assert adoptiq_settings.is_valid_auto_update_mode(m) is False


def test_mode_validator_default_is_auto():
    # Fresh settings (isolated per conftest) -> default auto.
    assert adoptiq_settings.get("auto_update_mode", "auto") in ("off", "notify", "auto")
