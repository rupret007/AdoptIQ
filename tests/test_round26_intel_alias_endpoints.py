"""Round 26 / Phase B -- User-facing ``/api/intel/*`` alias endpoints.

Pins parity between ``GET /api/intel/status`` and ``GET /api/corpus/status``,
method restrictions on the intel routes, and that ``POST /api/intel/refresh``
delegates to :func:`app_simple.api_corpus_refresh` (same auth + payload shape).
"""

from __future__ import annotations

import pytest

# Contract keys on the corpus/intel status payload (see
# ``_r17_corpus_status_payload``).  The app may add others (e.g.
# ``success`` mirrored from ``ok`` by the Round 12 JSON shim).
_REQUIRED_STATUS_TOP_KEYS = frozenset(
    ("ok", "enabled", "available", "reason", "boot", "corpus")
)


def test_intel_status_returns_same_shape_as_corpus_status(client):
    intel_resp = client.get("/api/intel/status")
    corpus_resp = client.get("/api/corpus/status")
    assert intel_resp.status_code == 200
    assert corpus_resp.status_code == 200
    intel_data = intel_resp.get_json()
    corpus_data = corpus_resp.get_json()
    assert isinstance(intel_data, dict) and isinstance(corpus_data, dict)
    assert _REQUIRED_STATUS_TOP_KEYS <= set(intel_data.keys())
    assert _REQUIRED_STATUS_TOP_KEYS <= set(corpus_data.keys())
    assert set(intel_data.keys()) == set(corpus_data.keys())
    assert set(intel_data["boot"].keys()) == set(corpus_data["boot"].keys())
    assert set(intel_data["corpus"].keys()) == set(corpus_data["corpus"].keys())


def test_intel_status_get_only(client):
    resp = client.post("/api/intel/status")
    assert resp.status_code == 405


def test_intel_refresh_delegates_to_corpus_refresh(client):
    resp = client.post("/api/intel/refresh")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    assert "refresh_started" in data
    for key in ("boot", "corpus", "available", "ok", "enabled", "reason"):
        assert key in data, f"missing key {key!r}"


def test_intel_refresh_post_only(client):
    resp = client.get("/api/intel/refresh")
    assert resp.status_code == 405


def test_intel_refresh_rejects_unauthenticated_when_csrf_enabled(app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        client = app.test_client()
        resp = client.post("/api/intel/refresh")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data is not None
        assert data.get("ok") is False
        assert "CSRF" in (data.get("error") or "")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_intel_refresh_accepts_internal_token_when_csrf_enabled(app, monkeypatch):
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-intel-tok")
    try:
        client = app.test_client()
        resp = client.post(
            "/api/intel/refresh",
            headers={"X-AdoptIQ-Internal": "test-intel-tok"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "refresh_started" in data
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
