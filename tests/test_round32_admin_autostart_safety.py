"""Round 32 / Phase 2.D regression: ``_start_admin_server_in_thread``
must (a) be a no-op under pytest, (b) be idempotent across calls,
(c) survive an OSError (port already bound) without crashing the main
app, and (d) actually call ``admin_app.run`` on the happy path.
"""
from __future__ import annotations

import logging
import sys

import pytest


def test_returns_quietly_under_pytest(monkeypatch):
    """PYTEST_CURRENT_TEST is always set inside pytest; the helper must
    short-circuit so test fixtures don't accidentally bind 5152."""
    import app_simple
    # Sanity: pytest sets this for every active test
    assert "PYTEST_CURRENT_TEST" in __import__("os").environ
    # Reset the idempotent guard so we can re-enter the helper.
    monkeypatch.setattr(sys, "_adoptiq_admin_started", False, raising=False)
    app_simple._start_admin_server_in_thread()
    assert getattr(sys, "_adoptiq_admin_started", False) is False


def test_idempotent_when_already_started(monkeypatch):
    """Once started in a process, a second call must be a no-op so
    a dev-time auto-reloader can't double-bind the port."""
    import app_simple
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sys, "_adoptiq_admin_started", True, raising=False)

    called: list[bool] = []

    def _no_thread_should_start(*args, **kwargs):
        called.append(True)
        raise AssertionError("threading.Thread must not be invoked")

    import threading
    monkeypatch.setattr(threading, "Thread", _no_thread_should_start)
    app_simple._start_admin_server_in_thread()
    assert called == []


def test_oserror_does_not_propagate(monkeypatch, caplog):
    """When the admin port is already bound (OSError on
    ``admin_app.run``), the helper logs a WARNING and the main app
    proceeds.  Run synchronously by neutering threading.Thread so we
    can observe the log without a race."""
    import app_simple
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sys, "_adoptiq_admin_started", False, raising=False)

    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    import threading
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    class _FakeApp:
        def run(self, **kwargs):
            raise OSError("[Errno 48] Address already in use")

    import enhanced_admin_dashboard_v2 as ead
    monkeypatch.setattr(ead, "admin_app", _FakeApp(), raising=False)
    monkeypatch.setattr(ead, "_resolve_admin_port", lambda: 5152, raising=False)

    app_logger = logging.getLogger("app_simple")
    prior_propagate = app_logger.propagate
    app_logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="app_simple"):
            # Must not raise
            app_simple._start_admin_server_in_thread()
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("admin port" in m and "5152" in m for m in warn_msgs), warn_msgs
    finally:
        app_logger.propagate = prior_propagate


def test_happy_path_invokes_admin_app_run(monkeypatch):
    import app_simple
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sys, "_adoptiq_admin_started", False, raising=False)

    class _SyncThread:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    import threading
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    invocations: list[dict] = []

    class _FakeApp:
        def run(self, **kwargs):
            invocations.append(kwargs)

    import enhanced_admin_dashboard_v2 as ead
    monkeypatch.setattr(ead, "admin_app", _FakeApp(), raising=False)
    monkeypatch.setattr(ead, "_resolve_admin_port", lambda: 5152, raising=False)

    app_simple._start_admin_server_in_thread()
    assert len(invocations) == 1
    kw = invocations[0]
    assert kw.get("host") == "127.0.0.1"
    assert kw.get("port") == 5152
    assert kw.get("debug") is False
    assert kw.get("use_reloader") is False


def test_admin_console_link_in_base_html() -> None:
    """The Admin Console nav link must be present in base.html so
    users in the packaged .app can reach it from any page."""
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parent.parent / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    assert "http://127.0.0.1:5152/" in src
    assert 'target="_blank"' in src
    assert 'rel="noopener noreferrer"' in src
