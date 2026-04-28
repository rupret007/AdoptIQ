"""Round 34 / H1 -- admin auto-start must enforce the loopback gate.

Pre-Round-34 ``app_simple._start_admin_server_in_thread`` read
``ADOPTIQ_ADMIN_HOST`` directly, with no reference to the
``ADOPTIQ_ADMIN_BIND_PUBLIC=1`` safety gate the standalone
``__main__`` path in ``enhanced_admin_dashboard_v2.py`` enforces.
An operator (or a misconfigured deployment) that set
``ADOPTIQ_ADMIN_HOST=0.0.0.0`` would silently expose the admin
console to every interface, bypassing the documented opt-in.

CLAUDE.md is explicit: "Admin dashboard binds to loopback
(127.0.0.1) by default.  Don't change this default without the
ADOPTIQ_ADMIN_BIND_PUBLIC=1 escape hatch."

Round 34 / H1 contract:

* ``ADOPTIQ_ADMIN_HOST`` unset             -> bind 127.0.0.1.
* ``ADOPTIQ_ADMIN_HOST=127.0.0.1``         -> bind 127.0.0.1 (no-op).
* ``ADOPTIQ_ADMIN_HOST=localhost``         -> bind localhost (loopback).
* ``ADOPTIQ_ADMIN_HOST=::1``               -> bind ::1 (loopback).
* ``ADOPTIQ_ADMIN_HOST=0.0.0.0`` *no opt-in* -> log WARN, fall back
                                                to 127.0.0.1.
* ``ADOPTIQ_ADMIN_HOST=10.0.0.5`` *no opt-in* -> same downgrade.
* ``ADOPTIQ_ADMIN_HOST=0.0.0.0`` + ``ADOPTIQ_ADMIN_BIND_PUBLIC=1``
                                              -> bind 0.0.0.0 (operator
                                                 opted in).

These tests pin every branch by patching
``threading.Thread`` synchronous and capturing the host kwarg
``admin_app.run`` is called with.
"""
from __future__ import annotations

import logging
import sys

import pytest


class _SyncThread:
    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _install_fake_admin(monkeypatch):
    """Replace admin_app.run + _resolve_admin_port + threading.Thread
    so we can observe the host kwarg without binding any real port."""
    invocations: list[dict] = []

    class _FakeApp:
        def run(self, **kwargs):
            invocations.append(kwargs)

    import enhanced_admin_dashboard_v2 as ead
    monkeypatch.setattr(ead, "admin_app", _FakeApp(), raising=False)
    monkeypatch.setattr(ead, "_resolve_admin_port", lambda: 5152, raising=False)
    import threading
    monkeypatch.setattr(threading, "Thread", _SyncThread)
    return invocations


@pytest.mark.parametrize(
    "host_env,expected_host",
    [
        (None, "127.0.0.1"),
        ("", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
        ("::1", "::1"),
    ],
)
def test_h1_loopback_hosts_pass_through(monkeypatch, host_env, expected_host):
    """All loopback values for ADOPTIQ_ADMIN_HOST must pass through
    even WITHOUT ADOPTIQ_ADMIN_BIND_PUBLIC -- they're inherently
    safe."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ADOPTIQ_ADMIN_BIND_PUBLIC", raising=False)
    monkeypatch.setattr(sys, "_adoptiq_admin_started", False, raising=False)
    if host_env is None:
        monkeypatch.delenv("ADOPTIQ_ADMIN_HOST", raising=False)
    else:
        monkeypatch.setenv("ADOPTIQ_ADMIN_HOST", host_env)

    invocations = _install_fake_admin(monkeypatch)

    import app_simple
    app_simple._start_admin_server_in_thread()

    assert len(invocations) == 1
    assert invocations[0]["host"] == expected_host


@pytest.mark.parametrize(
    "host_env",
    ["0.0.0.0", "10.0.0.5", "192.168.1.1", "lab.internal.example"],
)
def test_h1_non_loopback_without_optin_downgrades_to_127(
    monkeypatch, caplog, host_env,
):
    """Non-loopback ADOPTIQ_ADMIN_HOST WITHOUT
    ADOPTIQ_ADMIN_BIND_PUBLIC=1 must be downgraded to 127.0.0.1 with
    a WARN log so the operator sees the downgrade."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ADOPTIQ_ADMIN_BIND_PUBLIC", raising=False)
    monkeypatch.setenv("ADOPTIQ_ADMIN_HOST", host_env)
    monkeypatch.setattr(sys, "_adoptiq_admin_started", False, raising=False)

    invocations = _install_fake_admin(monkeypatch)
    app_logger = logging.getLogger("app_simple")
    prior = app_logger.propagate
    app_logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="app_simple"):
            import app_simple
            app_simple._start_admin_server_in_thread()
    finally:
        app_logger.propagate = prior

    assert len(invocations) == 1
    assert invocations[0]["host"] == "127.0.0.1", (
        f"non-loopback host {host_env!r} should be downgraded to "
        f"127.0.0.1; got {invocations[0]['host']!r}"
    )
    warn_msgs = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ]
    assert any("Round 34 / H1" in m for m in warn_msgs), (
        f"WARN downgrade log missing; saw {warn_msgs!r}"
    )
    assert any(host_env in m for m in warn_msgs), (
        "WARN downgrade log must mention the rejected host so the "
        "operator can grep for it"
    )


@pytest.mark.parametrize(
    "bind_public_value",
    ["1", "true", "TRUE", "yes", "on"],
)
def test_h1_non_loopback_with_optin_passes_through(
    monkeypatch, bind_public_value,
):
    """When ADOPTIQ_ADMIN_BIND_PUBLIC is set to a truthy value, a
    non-loopback ADOPTIQ_ADMIN_HOST must be honored (the operator
    explicitly opted in)."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ADOPTIQ_ADMIN_HOST", "0.0.0.0")
    monkeypatch.setenv("ADOPTIQ_ADMIN_BIND_PUBLIC", bind_public_value)
    monkeypatch.setattr(sys, "_adoptiq_admin_started", False, raising=False)

    invocations = _install_fake_admin(monkeypatch)

    import app_simple
    app_simple._start_admin_server_in_thread()

    assert len(invocations) == 1
    assert invocations[0]["host"] == "0.0.0.0", (
        f"opt-in flag {bind_public_value!r} should allow 0.0.0.0; "
        f"got {invocations[0]['host']!r}"
    )


def test_h1_default_state_remains_loopback_127():
    """Source-shape pin: the helper's default-host literal must stay
    at 127.0.0.1.  A future refactor that flips the default to
    0.0.0.0 (e.g., "to make LAN preview work out of the box") would
    silently weaken the loopback contract."""
    import app_simple
    import inspect

    src = inspect.getsource(app_simple._start_admin_server_in_thread)
    # The fallback literal must be the loopback IPv4.
    assert '"127.0.0.1"' in src
    # The H1 marker must be present so a future polish round can
    # find this code via ``git diff | grep 'Round 34 / H1'``.
    assert "Round 34 / H1" in src
    # The opt-in env var must be referenced -- otherwise the gate is
    # not actually wired.
    assert "ADOPTIQ_ADMIN_BIND_PUBLIC" in src
