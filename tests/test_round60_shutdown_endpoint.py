"""Round 60 -- pin the new ``POST /api/shutdown`` main-app endpoint
plus its admin-app proxy ``POST /admin_quit``.

The shutdown UX gives the user a single in-browser button to cleanly
stop the AdoptIQ process so ports 5151 and 5152 are released.  That's
worth testing because:

* it is the FIRST destructive action the user can trigger from the
  navbar -- any auth bypass would let a hostile cross-origin POST
  kill the local server;
* the in-progress detection has to be honest -- if it falsely
  reports zero running analyses, a click will silently destroy
  in-flight work; if it falsely reports running analyses when none
  exist, the user can never quit;
* the SIGTERM-based shutdown must NOT fire under pytest -- if it
  did, ``os.kill`` would terminate the test runner mid-suite.

Auth contract MUST mirror ``/api/corpus/refresh`` and
``/api/corpus/reset``:

* Flask-WTF CSRF token (browser path) **or**
* ``X-AdoptIQ-Internal`` header (server-to-server proxy path).

Action contract:

1. When at least one ``analysis_status`` entry has
   ``status == 'running'`` AND ``force=1`` was NOT supplied,
   return 409 with ``needs_force=True`` and a per-job summary so
   the browser can prompt the user.
2. Otherwise return 202 with ``shutdown_in_ms=500`` and (in
   non-TESTING mode) schedule a SIGTERM 0.5s in the future.  In
   TESTING mode return ``would_shutdown=True`` and skip the kill.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

import app_simple
import enhanced_admin_dashboard_v2 as admin_mod


# ---------------------------------------------------------------------------
# Fixtures: snapshot + restore the global analysis_status dict so each
# test starts from a clean slate (other tests in the suite may have
# left running entries lying around).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_analysis_status():
    """Every test gets a freshly-empty ``analysis_status`` and the
    original snapshot is restored on teardown so we don't leak state
    into adjacent tests."""
    saved: Dict[str, Any] = {}
    with app_simple.analysis_status_lock:
        saved = dict(app_simple.analysis_status)
        app_simple.analysis_status.clear()
    try:
        yield
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()
            app_simple.analysis_status.update(saved)


@pytest.fixture
def _seed_running_analysis():
    """Helper that adds one ``status='running'`` entry so the
    in-progress branch fires."""
    def _seed(analysis_id: str = "test-running-1", manager: str = "Test Manager",
             report_type: str = "leader", progress: int = 42,
             current_step: str = "Generating Word"):
        with app_simple.analysis_status_lock:
            app_simple.analysis_status[analysis_id] = {
                'status': 'running',
                'progress': progress,
                'manager': manager,
                'report_type': report_type,
                'start_time': '2026-04-30T10:00:00Z',
                'current_step': current_step,
            }
        return analysis_id
    return _seed


# ---------------------------------------------------------------------------
# Auth contract: dual-path, mirrors /api/corpus/refresh
# ---------------------------------------------------------------------------


def test_shutdown_rejects_missing_csrf_and_internal(app, monkeypatch):
    """With CSRF enabled and neither a token nor a matching internal
    header, /api/shutdown must return 403 -- a hostile cross-origin
    POST cannot kill the local process."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.delenv("ADOPTIQ_INTERNAL_TOKEN", raising=False)
    try:
        client = app.test_client()
        resp = client.post("/api/shutdown")
        assert resp.status_code == 403, (
            f"missing-auth POST must be 403; got {resp.status_code}"
        )
        data = resp.get_json()
        assert data is not None and data.get("ok") is False
        assert data.get("error")
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_shutdown_rejects_wrong_internal_token(app, monkeypatch):
    """A wrong ``X-AdoptIQ-Internal`` value must NOT grant access --
    the constant-time comparator must guard against close matches."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "the-real-r60-token")
    try:
        client = app.test_client()
        resp = client.post(
            "/api/shutdown",
            headers={"X-AdoptIQ-Internal": "obviously-wrong"},
        )
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_shutdown_rejects_empty_internal_token(app, monkeypatch):
    """Empty server-side token paired with empty client header must
    NOT bypass auth -- a regression here would let any cross-origin
    POST kill the process."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.delenv("ADOPTIQ_INTERNAL_TOKEN", raising=False)
    try:
        client = app.test_client()
        resp = client.post(
            "/api/shutdown",
            headers={"X-AdoptIQ-Internal": ""},
        )
        assert resp.status_code == 403
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_shutdown_accepted_with_csrf_disabled_and_no_running(app):
    """Test default (``WTF_CSRF_ENABLED=False``) plus an empty
    analysis_status -> 202 with ``would_shutdown=True`` (TESTING
    short-circuit).  This is the canonical "user clicks Quit on a
    quiet system" path."""
    client = app.test_client()
    resp = client.post("/api/shutdown")
    assert resp.status_code == 202, f"expected 202; got {resp.status_code}"
    data = resp.get_json()
    assert data is not None
    assert data.get("ok") is True
    assert data.get("would_shutdown") is True, (
        "TESTING mode must short-circuit os.kill; expected would_shutdown=True"
    )
    assert data.get("force") is False
    assert data.get("in_progress_count") == 0


def test_shutdown_accepted_with_internal_token_and_no_running(app, monkeypatch):
    """The ``X-AdoptIQ-Internal`` header must grant access when the
    server-side token matches (admin-app proxy path)."""
    app.config["WTF_CSRF_ENABLED"] = True
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-internal-r60-1")
    try:
        client = app.test_client()
        resp = client.post(
            "/api/shutdown",
            headers={"X-AdoptIQ-Internal": "test-internal-r60-1"},
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert data is not None and data.get("ok") is True
        assert data.get("would_shutdown") is True
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


# ---------------------------------------------------------------------------
# In-progress detection: 409 + needs_force
# ---------------------------------------------------------------------------


def test_shutdown_returns_409_when_analyses_running(app, _seed_running_analysis):
    """When one or more entries are ``status='running'`` and the
    caller did NOT pass ``force=1``, the endpoint must return 409
    with ``needs_force=True`` and a per-job summary."""
    _seed_running_analysis(
        analysis_id="r60-running-A",
        manager="Test Manager A",
        report_type="leader",
        progress=42,
        current_step="Generating Word",
    )
    client = app.test_client()
    resp = client.post("/api/shutdown")
    assert resp.status_code == 409, (
        f"expected 409 when running analyses exist; got {resp.status_code}"
    )
    data = resp.get_json()
    assert data is not None
    assert data.get("ok") is False
    assert data.get("needs_force") is True
    assert data.get("in_progress_count") == 1
    entries = data.get("in_progress")
    assert isinstance(entries, list) and len(entries) == 1
    entry = entries[0]
    assert entry.get("id") == "r60-running-A"
    assert entry.get("type") == "leader"
    assert entry.get("manager") == "Test Manager A"
    assert entry.get("progress") == 42
    assert entry.get("current_step") == "Generating Word"
    assert entry.get("start_time")  # nonempty


def test_shutdown_409_lists_all_running_entries(app, _seed_running_analysis):
    """A second running entry must appear in the in_progress list so
    the modal renders the full set, not just the first one."""
    _seed_running_analysis(analysis_id="r60-A", manager="Mgr A")
    _seed_running_analysis(analysis_id="r60-B", manager="Mgr B")
    client = app.test_client()
    resp = client.post("/api/shutdown")
    assert resp.status_code == 409
    data = resp.get_json()
    assert data.get("in_progress_count") == 2
    ids = sorted(entry["id"] for entry in data.get("in_progress", []))
    assert ids == ["r60-A", "r60-B"]


def test_shutdown_409_skips_non_running_entries(app, _seed_running_analysis):
    """Entries with ``status != 'running'`` must NOT trigger the 409
    branch -- otherwise a stale ``completed`` job would block every
    Quit attempt."""
    _seed_running_analysis(analysis_id="r60-running", manager="Live")
    with app_simple.analysis_status_lock:
        app_simple.analysis_status["r60-completed"] = {
            'status': 'completed',
            'progress': 100,
            'manager': 'Old',
            'report_type': 'leader',
            'start_time': '2026-04-29T10:00:00Z',
        }
        app_simple.analysis_status["r60-failed"] = {
            'status': 'failed',
            'manager': 'Bust',
            'report_type': 'compact',
        }
    client = app.test_client()
    resp = client.post("/api/shutdown")
    assert resp.status_code == 409
    data = resp.get_json()
    # Only the one truly-running entry should be reported.
    assert data.get("in_progress_count") == 1
    assert data["in_progress"][0]["id"] == "r60-running"


def test_shutdown_force_overrides_running_check(app, _seed_running_analysis):
    """``force=1`` must let the user shut down even with running
    analyses -- otherwise a stuck job would lock the process."""
    _seed_running_analysis(analysis_id="r60-running-X", manager="Stuck")
    client = app.test_client()
    resp = client.post("/api/shutdown", data={"force": "1"})
    assert resp.status_code == 202, (
        f"force=1 must override the 409 branch; got {resp.status_code}"
    )
    data = resp.get_json()
    assert data is not None and data.get("ok") is True
    assert data.get("would_shutdown") is True
    assert data.get("force") is True
    # in_progress_count should still reflect the snapshot, even when
    # we proceed -- so the audit trail records what was killed.
    assert data.get("in_progress_count") == 1


def test_shutdown_force_in_query_string_also_works(app, _seed_running_analysis):
    """Some XHR libraries put ``force`` in the query string instead of
    the form body; the endpoint must accept both."""
    _seed_running_analysis(manager="Mgr")
    client = app.test_client()
    resp = client.post("/api/shutdown?force=1")
    assert resp.status_code == 202


def test_shutdown_method_only_post(app):
    """GET / PUT / DELETE / PATCH must all yield 405 so a stale
    browser tab cannot trigger the destructive action by restoring an
    old URL."""
    client = app.test_client()
    for method in ("get", "put", "delete", "patch"):
        resp = getattr(client, method)("/api/shutdown")
        assert resp.status_code == 405, (
            f"{method.upper()} /api/shutdown must be 405; got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# SIGTERM behavior in non-TESTING mode
# ---------------------------------------------------------------------------


def test_shutdown_calls_sigterm_in_non_testing_mode(app, monkeypatch):
    """In non-TESTING mode, the endpoint must schedule a SIGTERM via
    ``threading.Timer`` so the process exits cleanly.  We monkeypatch
    ``os.kill`` and ``threading.Timer`` to record the call without
    actually killing the test runner."""
    import os
    import signal as _signal
    import threading

    app.config["TESTING"] = False
    # Round 60: env-var path is also honored; clear it so it doesn't
    # short-circuit the kill schedule we want to assert on.
    monkeypatch.delenv("ADOPTIQ_TESTING", raising=False)

    captured_kill: Dict[str, Any] = {"calls": []}
    captured_timer: Dict[str, Any] = {"calls": []}

    def _fake_kill(pid: int, sig: int) -> None:
        captured_kill["calls"].append({"pid": pid, "sig": sig})

    class _FakeTimer:
        def __init__(self, interval, fn, *args, **kwargs):
            captured_timer["calls"].append({
                "interval": interval, "fn": fn,
            })
            self.daemon = False
        def start(self):
            captured_timer["calls"][-1]["started"] = True

    monkeypatch.setattr(app_simple.os, "kill", _fake_kill)
    monkeypatch.setattr(app_simple.threading, "Timer", _FakeTimer)

    try:
        client = app.test_client()
        resp = client.post("/api/shutdown")
    finally:
        # Restore TESTING for adjacent tests sharing the app fixture.
        app.config["TESTING"] = True

    assert resp.status_code == 202
    data = resp.get_json()
    assert data is not None and data.get("ok") is True
    # Non-TESTING mode must NOT short-circuit -- shutdown_in_ms must
    # be present and would_shutdown must NOT.
    assert "shutdown_in_ms" in data, (
        "non-TESTING response must include shutdown_in_ms"
    )
    assert data.get("shutdown_in_ms") == 500
    assert "would_shutdown" not in data

    # Exactly one Timer was scheduled with the expected delay.
    assert len(captured_timer["calls"]) == 1
    assert captured_timer["calls"][0]["interval"] == 0.5
    assert captured_timer["calls"][0].get("started") is True

    # The Timer's target callback, when invoked synchronously, must
    # call os.kill(getpid, SIGTERM).  We invoke it manually here
    # because our _FakeTimer doesn't actually fire.
    captured_timer["calls"][0]["fn"]()
    assert len(captured_kill["calls"]) == 1
    kill_call = captured_kill["calls"][0]
    assert kill_call["pid"] == os.getpid(), (
        "SIGTERM must target the current PID, not another process"
    )
    assert kill_call["sig"] == _signal.SIGTERM, (
        "Round 60: SIGTERM (NOT os._exit / SIGKILL) is critical -- "
        "it lets the existing atexit handlers fire so analysis_status.json "
        "is saved and the corpus temp file is scrubbed."
    )


def test_shutdown_env_var_also_short_circuits(app, monkeypatch):
    """``ADOPTIQ_TESTING=1`` env var must also trigger the
    would_shutdown short-circuit even when ``app.config['TESTING']``
    is False -- so external test runners (smoke harnesses, CI) can
    opt in without flipping Flask config."""
    app.config["TESTING"] = False
    monkeypatch.setenv("ADOPTIQ_TESTING", "1")
    try:
        client = app.test_client()
        resp = client.post("/api/shutdown")
        assert resp.status_code == 202
        data = resp.get_json()
        assert data and data.get("would_shutdown") is True
    finally:
        app.config["TESTING"] = True


# ---------------------------------------------------------------------------
# Admin-app proxy: /admin_quit forwards to /api/shutdown with the
# X-AdoptIQ-Internal token.  Mirrors the R39 admin-corpus-reset test.
# ---------------------------------------------------------------------------


def test_admin_quit_route_requires_csrf():
    """Admin app proxy: no admin CSRF token, no header -> 403.
    Defense in depth so a malicious page cannot trigger Quit by
    cross-origin POSTing to the admin port."""
    client = admin_mod.admin_app.test_client()
    resp = client.post("/admin_quit")
    assert resp.status_code == 403


def test_admin_quit_route_rejects_wrong_csrf():
    client = admin_mod.admin_app.test_client()
    resp = client.post(
        "/admin_quit",
        data={"_admin_csrf": "not-the-right-token"},
    )
    assert resp.status_code == 403


def test_admin_quit_route_accepts_valid_csrf_and_proxies(monkeypatch):
    """Valid admin CSRF -> proxy forwards to main app's
    /api/shutdown with the X-AdoptIQ-Internal header, and returns
    the main app's JSON verbatim."""
    captured: Dict[str, Any] = {"calls": []}

    class _FakeResp:
        status_code = 202
        def json(self):
            return {
                "ok": True,
                "shutdown_in_ms": 500,
                "force": False,
                "in_progress_count": 0,
            }

    def _fake_post(url, *, data=None, headers=None, timeout=None, **kw):
        captured["calls"].append({
            "url": url,
            "data": data,
            "headers": headers,
            "timeout": timeout,
        })
        return _FakeResp()

    monkeypatch.setattr(admin_mod.requests, "post", _fake_post)
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "test-r60-internal")

    client = admin_mod.admin_app.test_client()
    with client.session_transaction() as sess:
        sess["_admin_csrf"] = "valid-admin-csrf"
    resp = client.post(
        "/admin_quit",
        data={"_admin_csrf": "valid-admin-csrf"},
    )

    # Pass-through of the main app's status code AND payload.
    assert resp.status_code == 202
    body = resp.get_json()
    assert body and body.get("ok") is True
    assert body.get("shutdown_in_ms") == 500

    # Verify the proxy actually called the main app with the right URL,
    # the X-AdoptIQ-Internal header, and an empty form body (no force).
    assert len(captured["calls"]) == 1
    call = captured["calls"][0]
    assert call["url"].endswith("/api/shutdown"), (
        f"proxy must call /api/shutdown; got {call['url']}"
    )
    assert call["headers"].get("X-AdoptIQ-Internal") == "test-r60-internal", (
        "proxy must attach X-AdoptIQ-Internal so the main app's auth gate "
        "lets the request through"
    )
    # No force on the default click; the JS only sets it when the user
    # confirms the 409 modal.
    assert call["data"] in ({}, None)


def test_admin_quit_route_forwards_force_field(monkeypatch):
    """When the admin button's JS includes ``force=1`` (after the
    user accepts the running-analyses prompt), the proxy must forward
    it so the main app's force branch fires."""
    captured: Dict[str, Any] = {"calls": []}

    class _FakeResp:
        status_code = 202
        def json(self):
            return {"ok": True, "shutdown_in_ms": 500, "force": True}

    def _fake_post(url, *, data=None, headers=None, timeout=None, **kw):
        captured["calls"].append({"data": data})
        return _FakeResp()

    monkeypatch.setattr(admin_mod.requests, "post", _fake_post)

    client = admin_mod.admin_app.test_client()
    with client.session_transaction() as sess:
        sess["_admin_csrf"] = "admin-csrf-2"
    resp = client.post(
        "/admin_quit",
        data={"_admin_csrf": "admin-csrf-2", "force": "1"},
    )
    assert resp.status_code == 202
    assert len(captured["calls"]) == 1
    assert captured["calls"][0]["data"] == {"force": "1"}, (
        "proxy must forward force=1 so the main app's 409 branch is bypassed"
    )


def test_admin_quit_route_passes_through_409(monkeypatch):
    """If the main app returns 409 needs_force, the admin proxy must
    return 409 too -- otherwise the admin's confirm modal would
    never fire and the user could not resolve a stuck job."""
    class _FakeResp:
        status_code = 409
        def json(self):
            return {
                "ok": False,
                "needs_force": True,
                "in_progress_count": 1,
                "in_progress": [
                    {"id": "x", "type": "leader", "manager": "M"},
                ],
            }

    monkeypatch.setattr(
        admin_mod.requests,
        "post",
        lambda *a, **kw: _FakeResp(),
    )

    client = admin_mod.admin_app.test_client()
    with client.session_transaction() as sess:
        sess["_admin_csrf"] = "admin-csrf-3"
    resp = client.post(
        "/admin_quit",
        data={"_admin_csrf": "admin-csrf-3"},
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body and body.get("needs_force") is True
    assert body.get("in_progress_count") == 1


def test_admin_quit_route_502_when_main_unreachable(monkeypatch):
    """If the main app is unreachable (network error) the proxy must
    return 502 (NOT a 200 with stale defaults) so the admin user sees
    a real error and doesn't think Quit succeeded."""
    def _boom(*a, **kw):
        raise admin_mod.requests.exceptions.ConnectionError("main-down")

    monkeypatch.setattr(admin_mod.requests, "post", _boom)

    client = admin_mod.admin_app.test_client()
    with client.session_transaction() as sess:
        sess["_admin_csrf"] = "admin-csrf-4"
    resp = client.post(
        "/admin_quit",
        data={"_admin_csrf": "admin-csrf-4"},
    )
    assert resp.status_code == 502
    body = resp.get_json()
    assert body and body.get("ok") is False
    assert "unreachable" in (body.get("error") or "").lower()
