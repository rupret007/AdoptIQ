"""Round 37 / Phase 1: pin the admin Server Status tile renders the
"Stop Server" form (and HIDES "Start Server") when the main app is up.

This is the user-visible bug that motivated Round 37.  Before the fix,
the packaged .app's admin tile would render the Start Server button
even though the server was alive on port 15152, because the admin
defaulted to probing localhost:5151 and never re-read the live
``ADOPTIQ_MAIN_URL`` env that ``app_simple._start_admin_server_in_thread``
now writes.

We monkeypatch ``get_server_status`` directly to avoid spinning up a
real HTTP server in CI; the env-resolution path is covered by
``test_round37_admin_main_url_resolution.py``.
"""

# ruff: noqa: E501

from __future__ import annotations

import re

import pytest


@pytest.fixture
def admin_client(monkeypatch):
    """Yield a Flask test client with ``get_server_status`` and the
    main-app proxies stubbed.  We never let the admin reach out to a
    live main app; tests are hermetic."""
    import enhanced_admin_dashboard_v2 as adm  # noqa: PLC0415

    # Stub the main-app proxies so the dashboard render does not
    # depend on live HTTP.  We return ``running=False`` by default
    # and let individual tests override.
    def _fake_running_reports(*_a, **_kw):
        class _Resp:
            status_code = 200
            def json(self):
                return []
        return _Resp()

    monkeypatch.setattr(
        "enhanced_admin_dashboard_v2.requests.get",
        _fake_running_reports,
    )

    client = adm.admin_app.test_client()
    return client, adm


def test_dashboard_hides_start_server_when_main_app_running(monkeypatch, admin_client):
    """The whole point of Round 37: ``running=True`` MUST render the
    Stop Server button and MUST NOT render the Start Server button."""
    client, adm = admin_client
    monkeypatch.setattr(
        adm,
        "get_server_status",
        lambda: {
            "running": True,
            "pid": 12345,
            "host": "127.0.0.1",
            "port": 15152,
            "port_open": True,
            "data_path_ok": True,
            "data_path_detail": None,
            "last_check": "2026-04-28T13:00:00Z",
        },
    )

    resp = client.get("/")
    assert resp.status_code == 200, f"dashboard returned HTTP {resp.status_code}"
    body = resp.get_data(as_text=True)

    assert 'action="/stop_server"' in body, (
        "Server is running but the Stop Server form is missing from the "
        "rendered dashboard. The {% if server_status.running %} branch "
        "is broken."
    )
    assert 'action="/start_server"' not in body, (
        "Server is running but the Start Server form is STILL rendered. "
        "This is the Round 37 bug -- get_server_status reported "
        "running=True but the template still shows the start form."
    )


def test_dashboard_shows_start_server_when_main_app_down(monkeypatch, admin_client):
    """Inverse pin: ``running=False`` MUST render the Start Server
    button and MUST NOT render the Stop Server button.  This is the
    legitimate use case the original button existed for (admin
    runs, main app does not)."""
    client, adm = admin_client
    monkeypatch.setattr(
        adm,
        "get_server_status",
        lambda: {
            "running": False,
            "pid": None,
            "host": None,
            "port": None,
            "port_open": False,
            "data_path_ok": False,
            "data_path_detail": "connection refused",
            "last_check": "2026-04-28T13:00:00Z",
        },
    )

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert 'action="/start_server"' in body, (
        "Server is down but the Start Server form is missing."
    )
    assert 'action="/stop_server"' not in body, (
        "Server is down but the Stop Server form is rendered."
    )


def test_server_status_default_port_renders_na_not_5000(admin_client):
    """Round 37 / Phase 1 also flipped the initial ``server_status['port']``
    from 5000 to None so the tile shows "N/A" before the first probe
    instead of falsely advertising a port the main app never bound to.

    Pin: the module-level default MUST be None, not 5000."""
    _client, adm = admin_client
    # ``server_status`` is mutated by every ``get_server_status()``
    # call, so we cannot just read it and compare -- we have to
    # introspect the module source for the literal default.
    import inspect
    src = inspect.getsource(adm)
    # Find the server_status dict literal.
    match = re.search(
        r"server_status\s*=\s*\{[^}]+\}",
        src,
        re.DOTALL,
    )
    assert match, "Could not find server_status dict literal in admin module"
    literal = match.group(0)
    # The legacy 5000 must be gone from this default.
    assert "'port': 5000" not in literal, (
        "Round 37 / Phase 1: server_status default still contains "
        "'port': 5000. Flip it to None so the tile renders 'N/A' "
        "before the first probe."
    )
    assert "\"port\": 5000" not in literal, (
        "Round 37 / Phase 1: server_status default still contains "
        '"port": 5000. Flip it to None.'
    )


def test_dashboard_renders_partial_running_state(monkeypatch, admin_client):
    """``data_path_ok=False`` (port open but Snowflake/Keeper down) is
    a valid intermediate state.  The Stop Server form should still be
    rendered (the server *is* up) but the data-path detail should
    surface to the operator."""
    client, adm = admin_client
    monkeypatch.setattr(
        adm,
        "get_server_status",
        lambda: {
            "running": True,
            "pid": 12345,
            "host": "127.0.0.1",
            "port": 15152,
            "port_open": True,
            "data_path_ok": False,
            "data_path_detail": "Snowflake connect timed out",
            "last_check": "2026-04-28T13:00:00Z",
        },
    )

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert 'action="/stop_server"' in body
    assert 'action="/start_server"' not in body
    # The detail string must surface so the operator can diagnose.
    assert "Snowflake" in body or "connect timed out" in body, (
        "data_path_detail did not surface in the rendered dashboard"
    )
