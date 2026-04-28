"""Round 33 / Build8: ``POST /api/corpus/sharepoint/signout`` endpoint
+ ``corpus_bootstrap.sharepoint_signout()`` helper + cache.clear().

Build7 had no user-facing way to sign out of Microsoft -- once the
device-code flow completed, the refresh token sat in the keychain
forever and there was no way to switch accounts without manually
deleting the keychain entry.  This test pins the signout path so a
future regression that:

* removes the route, or
* breaks ``KeyringTokenCache.clear()`` (e.g. forgets to also unlink
  the file fallback), or
* causes ``sharepoint_signout()`` to raise on a missing entry,

is caught in CI.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Unit: KeyringTokenCache.clear is idempotent and clears both backends
# ---------------------------------------------------------------------------


class _FakeKeyring:
    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}
        self.delete_calls: list[tuple[str, str]] = []

    def get_password(self, service, account):
        return self._store.get((service, account))

    def set_password(self, service, account, value):
        self._store[(service, account)] = value

    def delete_password(self, service, account):
        self.delete_calls.append((service, account))
        if (service, account) not in self._store:
            raise RuntimeError("no such entry")
        del self._store[(service, account)]


def test_keyring_token_cache_clear_drops_both_backends(tmp_path):
    from sharepoint_corpus_source import KeyringTokenCache

    fake = _FakeKeyring()
    fallback = tmp_path / "sharepoint_token_cache.json"
    cache = KeyringTokenCache(
        fallback_path=fallback,
        keyring_module=fake,
    )
    cache.save("blob-1")
    assert fallback.exists() is False  # save_keyring succeeded -> no fallback write
    # Force a fallback file too so the test exercises both clear paths.
    fallback.write_text("blob-2", encoding="utf-8")
    assert fallback.exists() is True

    cleared = cache.clear()
    assert cleared["keyring"] is True
    assert cleared["file"] is True
    assert fallback.exists() is False
    assert fake._store == {}

    # Idempotent: a second clear is harmless and reports both as
    # already-empty (no exceptions allowed).
    cleared2 = cache.clear()
    assert cleared2["keyring"] is False
    assert cleared2["file"] is False


def test_sharepoint_graph_client_clear_token_cache_resets_msal(monkeypatch, tmp_path):
    """``clear_token_cache`` must drop both the persisted cache and
    the in-memory MSAL handles so the next ``acquire_token_silent``
    cannot return a stale token from the previous session."""
    from sharepoint_corpus_source import KeyringTokenCache, SharePointGraphClient

    fake = _FakeKeyring()
    fallback = tmp_path / "sharepoint_token_cache.json"
    cache = KeyringTokenCache(fallback_path=fallback, keyring_module=fake)
    cache.save("blob")

    client = SharePointGraphClient(token_cache=cache)
    client._msal_app = object()
    client._msal_cache = object()
    client._pending_flow = {"foo": "bar"}

    cleared = client.clear_token_cache()
    assert cleared["keyring"] is True
    assert client._msal_app is None
    assert client._msal_cache is None
    assert client._pending_flow is None


# ---------------------------------------------------------------------------
# Endpoint: /api/corpus/sharepoint/signout
# ---------------------------------------------------------------------------


def test_signout_endpoint_calls_helper_and_returns_envelope(client, monkeypatch):
    import corpus_bootstrap as cb

    calls: list[bool] = []

    def _fake_signout():
        calls.append(True)
        return {"ok": True, "cleared": {"keyring": True, "file": False}}

    monkeypatch.setattr(cb, "sharepoint_signout", _fake_signout)

    resp = client.post("/api/corpus/sharepoint/signout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body and body.get("ok") is True
    assert body.get("cleared") == {"keyring": True, "file": False}
    assert calls == [True]


def test_signout_endpoint_swallows_helper_exception(client, monkeypatch):
    import corpus_bootstrap as cb

    def _boom():
        raise RuntimeError("token store wedged")

    monkeypatch.setattr(cb, "sharepoint_signout", _boom)

    resp = client.post("/api/corpus/sharepoint/signout")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body and body.get("ok") is False
    assert "RuntimeError" in (body.get("error") or "")


def test_signout_endpoint_csrf_required(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "WTF_CSRF_ENABLED", True)
    resp = client.post("/api/corpus/sharepoint/signout")
    assert resp.status_code == 403


def test_corpus_bootstrap_sharepoint_signout_rejects_when_disabled(monkeypatch):
    """Helper must short-circuit with a structured error rather than
    raising when the feature is disabled.  Mirrors the
    ``begin_sharepoint_signin`` contract.

    Defensive: monkeypatch both ``config.Config`` and
    ``corpus_bootstrap.Config`` (same object, but explicit) so a
    polluting earlier test that bound a value to the ``Config``
    instance attribute cannot mask the class-level override."""
    import corpus_bootstrap as cb
    from config import Config as ConfigClass

    monkeypatch.setattr(ConfigClass, "ADOPTIQ_SHAREPOINT_ENABLED", False, raising=False)
    monkeypatch.setattr(cb.Config, "ADOPTIQ_SHAREPOINT_ENABLED", False, raising=False)
    monkeypatch.setenv("ADOPTIQ_SHAREPOINT_ENABLED", "false")
    result = cb.sharepoint_signout()
    assert result.get("ok") is False, (
        f"expected ok=False when feature disabled, got {result!r}"
    )
    assert "disabled" in (result.get("error") or "").lower()
