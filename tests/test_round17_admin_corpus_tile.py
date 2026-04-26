"""Round 17 / Phase F.1 -- Admin corpus tile contracts.

Pins the admin dashboard's Corpus tile (``/api/corpus/status`` on
the main app + ``/corpus_refresh`` proxy on the admin app):

- The main-app status endpoint returns a stable JSON shape (boot
  + corpus subkeys) and never raises -- the tile must always
  render.
- The status payload never embeds raw customer data.
- The admin-app refresh proxy is CSRF-protected (Round 5 pattern).
- The main-app refresh endpoint accepts either a CSRF token or
  the internal token; missing both returns HTTP 403.
- The internal-token path only succeeds when
  ``ADOPTIQ_INTERNAL_TOKEN`` is set on both ends -- empty values
  must NOT match (constant-time comparator).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import app_simple
import corpus_retriever
import enhanced_admin_dashboard_v2 as admin_mod
from corpus_indexer import index_folder, open_corpus_db


_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "round17"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    for name in (
        "synthetic_cases.csv",
        "synthetic_barriers.csv",
        "synthetic_pulse.csv",
    ):
        (root / name).write_bytes((_FIXTURES_ROOT / name).read_bytes())
    return root


@pytest.fixture
def configured_corpus(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    db_path = tmp_path / "corpus.db"
    conn = open_corpus_db(db_path)
    try:
        index_folder(conn, root)
        corpus_retriever.configure_connection(conn)
        yield conn
    finally:
        corpus_retriever.configure_connection(None)
        try:
            conn.close()
        except sqlite3.DatabaseError:
            pass


# ---------------------------------------------------------------------------
# /api/corpus/status -- payload shape contract.
# ---------------------------------------------------------------------------


def test_status_endpoint_returns_stable_shape(client):
    resp = client.get("/api/corpus/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)
    # Top-level keys are stable.
    for key in (
        "ok",
        "enabled",
        "available",
        "reason",
        "boot",
        "corpus",
    ):
        assert key in data, f"missing top-level key {key}"
    # Boot subkeys.
    boot = data["boot"]
    for key in (
        "started",
        "in_progress",
        "completed",
        "last_started_at",
        "last_finished_at",
        "last_error",
        "last_error_kind",
        "last_stats",
        "encrypted_path",
        "onedrive_root",
    ):
        assert key in boot, f"missing boot.{key}"
    # Corpus subkeys.
    corpus = data["corpus"]
    for key in (
        "files_total",
        "files_parsed",
        "customers",
        "cases",
        "chunks",
        "schema_version",
        "last_parsed_at",
        "indexed_at",
    ):
        assert key in corpus, f"missing corpus.{key}"


def test_status_endpoint_renders_when_corpus_unconfigured(client):
    corpus_retriever.configure_connection(None)
    resp = client.get("/api/corpus/status")
    # The endpoint must return 200 even when the corpus is
    # unavailable so the admin tile can render its banner.
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is False
    # ``reason`` is non-empty when unavailable.
    assert data.get("reason") not in (None, "")


def test_status_endpoint_payload_never_embeds_customer_data(
    client, configured_corpus
):
    resp = client.get("/api/corpus/status")
    data = resp.get_json()
    # The status payload is purely counts + timestamps.  It must
    # never embed customer names, case summaries, or sentiment
    # text.  Walk the JSON and assert no synthetic fixture name
    # appears in any string value.
    forbidden = ("Synthetic Alpha", "Synthetic Beta", "Synthetic Gamma")
    payload_str = repr(data)
    for name in forbidden:
        assert name not in payload_str, (
            f"customer name {name!r} leaked into status payload"
        )


def test_status_endpoint_payload_helper_is_safe_against_imports(
    monkeypatch
):
    # ``_r17_corpus_status_payload`` must never raise even when
    # ``corpus_bootstrap`` or ``corpus_retriever`` blows up
    # during import.  Force both imports to fail and verify a
    # default-shaped payload is produced.
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __import__

    def _broken(name, *args, **kwargs):
        if name in {"corpus_bootstrap", "corpus_retriever"}:
            raise ImportError(f"forced {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _broken)
    payload = app_simple._r17_corpus_status_payload()
    assert isinstance(payload, dict)
    assert "boot" in payload and "corpus" in payload


# ---------------------------------------------------------------------------
# /api/corpus/refresh -- main-app auth contracts.
# ---------------------------------------------------------------------------


def test_refresh_endpoint_rejects_unauthenticated_when_csrf_enabled(app):
    # Re-enable CSRF for the main app, then the refresh endpoint
    # must require either a valid CSRF token or the internal token.
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        client = app.test_client()
        resp = client.post("/api/corpus/refresh")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data and data.get("error")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_refresh_endpoint_internal_token_grants_access(app, monkeypatch):
    # When the internal token is configured on the server and
    # supplied by the caller (matching), the refresh runs even
    # without a CSRF token.
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-internal-token-1")
    try:
        client = app.test_client()
        resp = client.post(
            "/api/corpus/refresh",
            headers={"X-AdoptIQ-Internal": "test-internal-token-1"},
        )
        # 200 means the auth gate let us through; the refresh
        # itself may report ``refresh_started=False`` because
        # corpus_bootstrap is not enabled in tests.
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "refresh_started" in data
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_refresh_endpoint_internal_token_mismatch_is_rejected(
    app, monkeypatch
):
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "the-real-token")
    try:
        client = app.test_client()
        resp = client.post(
            "/api/corpus/refresh",
            headers={"X-AdoptIQ-Internal": "obviously-wrong"},
        )
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_refresh_endpoint_empty_internal_token_does_not_grant_access(
    app, monkeypatch
):
    # Both env-var and header empty must NOT bypass auth (constant-
    # time comparator must guard against the empty-string match).
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.delenv("ADOPTIQ_INTERNAL_TOKEN", raising=False)
    try:
        client = app.test_client()
        resp = client.post(
            "/api/corpus/refresh",
            headers={"X-AdoptIQ-Internal": ""},
        )
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_refresh_endpoint_returns_status_payload_on_success(
    app, monkeypatch
):
    # CSRF disabled (test default) -- refresh runs and the
    # response merges the status payload with ``refresh_started``.
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    resp = client.post("/api/corpus/refresh")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "refresh_started" in data
    assert "boot" in data
    assert "corpus" in data


# ---------------------------------------------------------------------------
# Admin app -- /corpus_refresh proxy CSRF gate.
# ---------------------------------------------------------------------------


def test_admin_corpus_refresh_route_requires_csrf():
    client = admin_mod.admin_app.test_client()
    # No admin CSRF token, no header -> 403.
    resp = client.post("/corpus_refresh")
    assert resp.status_code == 403


def test_admin_corpus_refresh_route_rejects_wrong_csrf():
    client = admin_mod.admin_app.test_client()
    resp = client.post(
        "/corpus_refresh",
        data={"_admin_csrf": "not-the-right-token"},
    )
    assert resp.status_code == 403


def test_admin_corpus_refresh_route_accepts_valid_csrf(monkeypatch):
    # Stub out ``requests.post`` so the admin app does not need to
    # reach the main app over the wire.
    class _FakeResp:
        status_code = 200

        def json(self):
            return {"refresh_started": True}

    monkeypatch.setattr(
        admin_mod.requests,
        "post",
        lambda *a, **kw: _FakeResp(),
    )
    client = admin_mod.admin_app.test_client()
    # Prime the session with an admin CSRF token by issuing a GET
    # to the dashboard -- the context processor mints one.
    with client.session_transaction() as sess:
        sess["_admin_csrf"] = "valid-admin-csrf-token-value"
    resp = client.post(
        "/corpus_refresh",
        data={"_admin_csrf": "valid-admin-csrf-token-value"},
    )
    # Successful CSRF + happy proxy redirects back to the admin
    # dashboard with a status-bearing query string.
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    # The location query string is URL-encoded; match either form.
    assert "Corpus+refresh" in location or "Corpus refresh" in location


# ---------------------------------------------------------------------------
# Source-level CSRF marker (mirrors test_round5_admin_destructive...).
# ---------------------------------------------------------------------------


def test_admin_corpus_refresh_route_calls_require_admin_csrf():
    src = (
        Path(__file__).resolve().parent.parent
        / "enhanced_admin_dashboard_v2.py"
    ).read_text(encoding="utf-8")
    # Locate the corpus_refresh_route definition and assert the
    # CSRF guard is its first statement.
    idx = src.find("def corpus_refresh_route(")
    assert idx > 0, "corpus_refresh_route must exist"
    body = src[idx : idx + 600]
    assert "_require_admin_csrf()" in body, (
        "Round 17 / Phase D.5: corpus_refresh_route must call "
        "_require_admin_csrf() before mutating state."
    )


# ---------------------------------------------------------------------------
# Round 17.1 -- per-source breakdown contract.
# ---------------------------------------------------------------------------


def test_status_payload_exposes_per_source_breakdown_keys(client):
    """The Corpus tile shows per-source counts (OneDrive vs the
    runtime user's Downloads).  Pin the keys so the renderer can
    rely on them.

    The actual numbers may be ``None`` when the bootstrap has not
    run yet (test boot-state).  We only assert the keys exist so
    the renderer's fallback path is well-defined.
    """
    resp = client.get("/api/corpus/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "boot" in data and isinstance(data["boot"], dict)
    assert "last_sources" in data["boot"], (
        "Round 17.1: boot.last_sources is the per-source breakdown "
        "the admin Corpus tile renders."
    )
    assert "corpus" in data and isinstance(data["corpus"], dict)
    assert "sources" in data["corpus"], (
        "Round 17.1: corpus.sources mirrors boot.last_sources at "
        "the top of the corpus subkey for renderer convenience."
    )


def test_status_payload_per_source_shape_when_populated(monkeypatch, client):
    """When ``corpus_bootstrap`` has run and recorded per-source
    stats, each entry must contain at minimum ``label`` (display
    name), ``path`` (folder), and ``stats`` (counts dict).  Pin the
    contract by injecting a synthetic boot state."""
    import corpus_bootstrap

    fake_state = corpus_bootstrap.CorpusBootState(
        started=True,
        in_progress=False,
        completed=True,
        last_started_at="2026-04-25T00:00:00Z",
        last_finished_at="2026-04-25T00:00:01Z",
        last_error=None,
        last_error_kind=None,
        last_stats=None,
        encrypted_path="/tmp/corpus.db",
        onedrive_root="/Users/test/OneDrive",
        last_sources=[
            {
                "label": "OneDrive",
                "path": "/Users/test/OneDrive/AdoptIQ_CSOne_Reports",
                "stats": {
                    "files_seen": 12,
                    "files_parsed": 10,
                    "files_failed": 2,
                },
            },
            {
                "label": "Downloads",
                "path": "/Users/test/Downloads",
                "stats": {
                    "files_seen": 4,
                    "files_parsed": 4,
                    "files_failed": 0,
                },
            },
        ],
    )

    monkeypatch.setattr(corpus_bootstrap, "get_state", lambda: fake_state)

    resp = client.get("/api/corpus/status")
    assert resp.status_code == 200
    data = resp.get_json()
    sources = data["boot"]["last_sources"]
    assert isinstance(sources, list) and len(sources) == 2
    labels = {entry["label"] for entry in sources}
    assert labels == {"OneDrive", "Downloads"}
    for entry in sources:
        assert "path" in entry
        assert "stats" in entry
        for key in ("files_seen", "files_parsed", "files_failed"):
            assert key in entry["stats"], (
                f"per-source stats missing {key!r} for {entry['label']!r}"
            )
