"""Round 39 / corpus crypto self-heal -- pin the new
``/api/corpus/reset`` endpoint (manual escape hatch for users whose
encrypted corpus has become undecryptable) plus its admin-app proxy
``/corpus_reset``.

Auth contract MUST mirror ``/api/corpus/refresh``:

* Flask-WTF CSRF token (browser path) **or**
* ``X-AdoptIQ-Internal`` header (server-to-server proxy path).

Action contract:

1. Preserve the user's four corpus artifacts as
   ``<name>.broken-<utc>`` (single rolling backup so disk usage is
   bounded at one 280-MB sidecar set).
2. Trigger ``request_refresh(rebuild=True)`` so the bootstrap path
   then reinstalls the bundled baked snapshot and reindexes.

Method contract: POST only (GET / PUT / DELETE / PATCH must yield
405) so the destructive action cannot be triggered by a misclick on
a stale browser tab restoring an old GET URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app_simple
import corpus_bootstrap
import enhanced_admin_dashboard_v2 as admin_mod


_FAKE_BAKE_FILES = (
    "corpus.db.enc",
    "sentinel.json",
    "corpus.db.salt",
    "corpus.sentinel.lock.json",
)


def _seed_user_dir(user_dir: Path) -> None:
    user_dir.mkdir(parents=True, exist_ok=True)
    for fname in _FAKE_BAKE_FILES:
        (user_dir / fname).write_bytes(
            f"adoptiq-user-fixture-{fname}".encode("utf-8")
        )


@pytest.fixture(autouse=True)
def _isolate_corpus_dirs(tmp_path, monkeypatch):
    """Point the bootstrap module at tmp dirs so tests never touch
    the real user's knowledge directory.  Also stub
    ``request_refresh`` to a recording lambda so we can assert it
    was called with ``rebuild=True`` without having to spin up the
    indexer thread."""
    fake_user_dir = tmp_path / "user_corpus"
    monkeypatch.setattr(
        corpus_bootstrap, "_user_corpus_dir", lambda: fake_user_dir,
    )
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


@pytest.fixture
def calls_recorder(monkeypatch):
    """Capture ``request_refresh`` invocations for assertion without
    spinning up a real bootstrap thread."""
    captured = {"calls": []}

    def _fake_request_refresh(*, rebuild: bool = False) -> bool:
        captured["calls"].append({"rebuild": rebuild})
        return True

    monkeypatch.setattr(
        corpus_bootstrap, "request_refresh", _fake_request_refresh,
    )
    return captured


# ---------------------------------------------------------------------------
# Happy path: POST with valid auth -> 200 + reset + refresh
# ---------------------------------------------------------------------------


def test_reset_endpoint_happy_path_with_csrf_disabled(
    app, calls_recorder,
):
    """In the test default (``WTF_CSRF_ENABLED=False``) the endpoint
    accepts the POST, preserves the four user files, and triggers a
    rebuild refresh."""
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    client = app.test_client()

    resp = client.post("/api/corpus/reset")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data.get("ok") is True
    assert data.get("preserved_count") == 4, (
        f"expected 4 preserved files, got {data.get('preserved_count')}"
    )
    assert data.get("preserved_as_suffix"), (
        "response must include the .broken-<ts> suffix so support can "
        "find the rolled-back files on disk"
    )
    assert data.get("refresh_started") is True

    # Verify request_refresh was called with rebuild=True so the
    # bootstrap pass that replaces the broken corpus does a full
    # rebuild, not just an incremental delta.
    assert len(calls_recorder["calls"]) == 1
    assert calls_recorder["calls"][0]["rebuild"] is True

    # Verify the four .broken-<ts> sidecars were created on disk.
    suffix = data["preserved_as_suffix"]
    for fname in _FAKE_BAKE_FILES:
        sidecar = user_dir / f"{fname}.broken-{suffix}"
        assert sidecar.exists(), (
            f"preserved sidecar {sidecar.name} missing from disk"
        )


# ---------------------------------------------------------------------------
# Auth contract: CSRF + internal-token dual-path mirroring /refresh
# ---------------------------------------------------------------------------


def test_reset_endpoint_rejects_unauthenticated_when_csrf_enabled(app):
    """With CSRF enabled and no token / no internal header, the
    endpoint must return 403 -- a hostile cross-origin POST cannot
    silently wipe the corpus."""
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        client = app.test_client()
        resp = client.post("/api/corpus/reset")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data and data.get("error")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_reset_endpoint_internal_token_grants_access(
    app, monkeypatch, calls_recorder,
):
    """The ``X-AdoptIQ-Internal`` header must grant access when the
    server-side token matches (admin-app proxy path)."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-internal-r39-1")
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    try:
        client = app.test_client()
        resp = client.post(
            "/api/corpus/reset",
            headers={"X-AdoptIQ-Internal": "test-internal-r39-1"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("ok") is True
        # Auth gate let us through and the reset ran.
        assert len(calls_recorder["calls"]) == 1
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_reset_endpoint_internal_token_mismatch_is_rejected(
    app, monkeypatch,
):
    """A wrong ``X-AdoptIQ-Internal`` value must NOT grant access --
    the constant-time comparator must guard against close matches."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "the-real-r39-token")
    try:
        client = app.test_client()
        resp = client.post(
            "/api/corpus/reset",
            headers={"X-AdoptIQ-Internal": "obviously-wrong"},
        )
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_reset_endpoint_empty_internal_token_does_not_grant_access(
    app, monkeypatch,
):
    """Empty server-side token paired with empty client header must
    NOT bypass auth (an empty == empty regression would let anyone
    reset the corpus)."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.delenv("ADOPTIQ_INTERNAL_TOKEN", raising=False)
    try:
        client = app.test_client()
        resp = client.post(
            "/api/corpus/reset",
            headers={"X-AdoptIQ-Internal": ""},
        )
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


# ---------------------------------------------------------------------------
# Method contract: POST only
# ---------------------------------------------------------------------------


def test_reset_endpoint_rejects_non_post_methods(app):
    """GET / PUT / DELETE / PATCH must all yield 405 so a stale
    browser tab restoring an old URL cannot trigger the destructive
    action."""
    client = app.test_client()
    for method in ("get", "put", "delete", "patch"):
        resp = getattr(client, method)("/api/corpus/reset")
        assert resp.status_code == 405, (
            f"{method.upper()} /api/corpus/reset must be 405; got "
            f"{resp.status_code}"
        )


# ---------------------------------------------------------------------------
# /api/intel/reset alias parity
# ---------------------------------------------------------------------------


def test_intel_reset_alias_delegates_to_corpus_reset(
    app, calls_recorder,
):
    """The user-facing alias ``/api/intel/reset`` must delegate to
    ``/api/corpus/reset`` so the analyze-page panel button does not
    have to reference admin URLs."""
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    client = app.test_client()

    resp = client.post("/api/intel/reset")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data and data.get("ok") is True
    assert data.get("preserved_count") == 4
    assert data.get("refresh_started") is True
    # Same single rebuild=True call as the canonical endpoint.
    assert len(calls_recorder["calls"]) == 1
    assert calls_recorder["calls"][0]["rebuild"] is True


# ---------------------------------------------------------------------------
# Idempotency / no-corpus edge case
# ---------------------------------------------------------------------------


def test_reset_endpoint_when_no_corpus_present(
    app, calls_recorder, tmp_path,
):
    """If the user dir is empty (no corpus to preserve), the endpoint
    must NOT crash -- it should return 200 with
    ``preserved_count=0`` and skip the refresh trigger."""
    # The fixture's user_dir does not exist on disk yet; do not seed it.
    client = app.test_client()
    resp = client.post("/api/corpus/reset")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    # ``ok=True`` with preserved_count=0 -- no corpus on disk to
    # preserve, but the call still completes cleanly.
    assert data.get("ok") is True
    assert data.get("preserved_count") == 0
    assert data.get("preserved_as_suffix") is None


# ---------------------------------------------------------------------------
# Admin app -- /corpus_reset proxy CSRF gate
# ---------------------------------------------------------------------------


def test_admin_corpus_reset_route_requires_csrf():
    """Admin app proxy: no admin CSRF token, no header -> 403."""
    client = admin_mod.admin_app.test_client()
    resp = client.post("/corpus_reset")
    assert resp.status_code == 403


def test_admin_corpus_reset_route_rejects_wrong_csrf():
    client = admin_mod.admin_app.test_client()
    resp = client.post(
        "/corpus_reset",
        data={"_admin_csrf": "not-the-right-token"},
    )
    assert resp.status_code == 403


def test_admin_corpus_reset_route_accepts_valid_csrf(monkeypatch):
    """Valid admin CSRF -> proxy forwards to main app, redirects back
    with a status-bearing query string."""
    class _FakeResp:
        status_code = 200

        def json(self):
            return {
                "ok": True,
                "preserved_count": 4,
                "preserved_as_suffix": "20260101T000000Z",
                "refresh_started": True,
            }

    monkeypatch.setattr(
        admin_mod.requests,
        "post",
        lambda *a, **kw: _FakeResp(),
    )
    client = admin_mod.admin_app.test_client()
    with client.session_transaction() as sess:
        sess["_admin_csrf"] = "valid-admin-csrf-token-value"
    resp = client.post(
        "/corpus_reset",
        data={"_admin_csrf": "valid-admin-csrf-token-value"},
    )
    assert resp.status_code in (302, 303), (
        f"expected redirect after successful reset; got {resp.status_code}"
    )
    location = resp.headers.get("Location", "")
    assert "Corpus+reset" in location or "Corpus reset" in location, (
        f"redirect must include reset status in query string; "
        f"got {location!r}"
    )


# ---------------------------------------------------------------------------
# Source-level marker: admin proxy must call _require_admin_csrf first
# ---------------------------------------------------------------------------


def test_admin_corpus_reset_route_calls_require_admin_csrf():
    """Pin the source shape: ``corpus_reset_route`` must call
    ``_require_admin_csrf()`` as its first statement (Round 5 pattern)
    so a future refactor cannot accidentally ungate the route."""
    src = (
        Path(__file__).resolve().parent.parent
        / "enhanced_admin_dashboard_v2.py"
    ).read_text(encoding="utf-8")
    idx = src.find("def corpus_reset_route(")
    assert idx > 0, "corpus_reset_route must be defined"
    # Round 54 / F3 expanded the route docstring + added a blocked-
    # state short-circuit before the proxy call.  Bump the slice so
    # the assertion still finds the CSRF guard which is the FIRST
    # executable statement after the docstring.
    body = src[idx : idx + 2500]
    assert "_require_admin_csrf()" in body, (
        "Round 39: corpus_reset_route must call _require_admin_csrf() "
        "before mutating state."
    )
    # Defense in depth: pin the call ordering -- the CSRF guard MUST
    # come before the F3 short-circuit so unauthenticated callers
    # cannot probe corpus state via the gate's redirect message.
    csrf_pos = body.find("_require_admin_csrf()")
    blocked_pos = body.find("_r54_corpus_is_blocked_no_onedrive")
    assert csrf_pos < blocked_pos or blocked_pos == -1, (
        "Round 54 / F3: CSRF guard must come BEFORE the blocked-state "
        "short-circuit so unauthenticated callers cannot probe state."
    )
