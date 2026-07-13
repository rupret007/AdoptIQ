"""Round 32 / Phase 2.E regression: ``POST /api/settings/intelligence``
must persist to ``settings.json``, mutate ``Config`` in-process, and
trigger a corpus refresh on enable.  Auth is required (CSRF token or
``X-AdoptIQ-Internal`` header) — fixtures disable WTF_CSRF_ENABLED so
we can focus on the persistence/Config mutation path.
"""
from __future__ import annotations

import json
import sys

import pytest

from config import Config


@pytest.fixture(autouse=True)
def _isolate_settings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    yield


def _settings_file_path():
    import adoptiq_settings as s
    return s._settings_path()


def test_csrf_required_when_enabled(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "WTF_CSRF_ENABLED", True)
    resp = client.post(
        "/api/settings/intelligence",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body and body.get("ok") is False
    assert "CSRF" in (body.get("error") or "")


def test_internal_token_bypasses_csrf(client, monkeypatch):
    """``X-AdoptIQ-Internal`` matching ADOPTIQ_INTERNAL_TOKEN is the
    documented server-to-server auth path."""
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "shared-secret-token")
    monkeypatch.setitem(client.application.config, "WTF_CSRF_ENABLED", True)
    resp = client.post(
        "/api/settings/intelligence",
        data=json.dumps({"enabled": False}),
        content_type="application/json",
        headers={"X-AdoptIQ-Internal": "shared-secret-token"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body and body.get("ok") is True


def test_missing_enabled_field_returns_400(client):
    resp = client.post(
        "/api/settings/intelligence",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body and body.get("ok") is False
    assert "enabled" in (body.get("error") or "")


def test_disable_persists_and_mutates_config(client, monkeypatch):
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    resp = client.post(
        "/api/settings/intelligence",
        data=json.dumps({"enabled": False}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # Round 12 / Phase 11.3 after-request shim mirrors "ok" -> "success",
    # so we assert on the keys we own and ignore extras.
    assert body.get("ok") is True
    assert body.get("enabled") is False
    assert body.get("refresh_started") is False
    on_disk = json.loads(_settings_file_path().read_text(encoding="utf-8"))
    assert on_disk == {"corpus_knowledge_enabled": False}
    assert Config.CORPUS_KNOWLEDGE_ENABLED is False


def test_enable_kicks_corpus_refresh(client, monkeypatch):
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False, raising=False)
    import corpus_bootstrap as cb

    refresh_calls: list[dict] = []

    def _fake_request_refresh(rebuild=False):
        refresh_calls.append({"rebuild": rebuild})
        return True

    monkeypatch.setattr(cb, "request_refresh", _fake_request_refresh)

    resp = client.post(
        "/api/settings/intelligence",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("enabled") is True
    assert body.get("refresh_started") is True
    assert refresh_calls == [{"rebuild": False}]
    assert Config.CORPUS_KNOWLEDGE_ENABLED is True
    on_disk = json.loads(_settings_file_path().read_text(encoding="utf-8"))
    assert on_disk == {"corpus_knowledge_enabled": True}


def test_enable_surfaces_refresh_failure_without_500(client, monkeypatch):
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False, raising=False)
    import corpus_bootstrap as cb

    def _boom(rebuild=False):
        raise RuntimeError("indexer wedged")

    monkeypatch.setattr(cb, "request_refresh", _boom)

    resp = client.post(
        "/api/settings/intelligence",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("enabled") is True
    assert body.get("refresh_started") is False
    assert body.get("refresh_error") == "RuntimeError"
