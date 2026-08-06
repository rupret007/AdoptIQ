"""Round 17.3: ADOPTIQ_PORT / ADOPTIQ_ADMIN_PORT override behaviour.

The plan ships two tiny resolver helpers (``app_simple._resolve_main_port``
and ``enhanced_admin_dashboard_v2._resolve_admin_port``) so the ports can
move without a rebuild.  These tests pin both the new defaults (5151 /
5152) and the env-override hook so a future silent revert breaks the
suite.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def main_app():
    """Import (or reload) ``app_simple`` once per test, and return it."""
    mod = importlib.import_module("app_simple")
    return mod


@pytest.fixture()
def admin_app():
    """Import (or reload) ``enhanced_admin_dashboard_v2`` once per test."""
    mod = importlib.import_module("enhanced_admin_dashboard_v2")
    return mod


def test_main_app_default_port_is_5151(monkeypatch, main_app):
    """With ``ADOPTIQ_PORT`` unset the main app must resolve to 5151.

    Round 17.3 chose 5151 to sit adjacent to the user's other 5150
    ("Van Halen") app and out of the macOS AirPlay Receiver / Flask
    default 5000-5001 conflict zone.
    """
    monkeypatch.delenv("ADOPTIQ_PORT", raising=False)
    assert main_app._DEFAULT_MAIN_PORT == 5151
    assert main_app._resolve_main_port() == 5151


def test_main_app_honors_adoptiq_port_env(main_app):
    """Setting ``ADOPTIQ_PORT`` to a valid integer must override the default.

    Pass the env via the helper's optional ``env`` argument so the test
    is hermetic and doesn't depend on real process state.  Non-integer
    and out-of-range values must fall back to the default rather than
    crashing the launcher.
    """
    assert main_app._resolve_main_port({"ADOPTIQ_PORT": "6000"}) == 6000
    # Edge: blank string is treated as "unset" and falls back.
    assert main_app._resolve_main_port({"ADOPTIQ_PORT": "  "}) == 5151
    # Edge: garbage falls back, doesn't raise.
    assert main_app._resolve_main_port({"ADOPTIQ_PORT": "abc"}) == 5151
    # Edge: out of TCP range falls back.
    assert main_app._resolve_main_port({"ADOPTIQ_PORT": "70000"}) == 5151
    assert main_app._resolve_main_port({"ADOPTIQ_PORT": "0"}) == 5151


def test_admin_dashboard_default_port_is_5152_and_honors_env(monkeypatch, admin_app):
    """Admin port must default to 5152 and honour ``ADOPTIQ_ADMIN_PORT``.

    Also asserts the live main-app URL helpers default to port 5151 when
    ``ADOPTIQ_MAIN_URL`` is unset -- the two changes ship together and a
    partial revert (only one side moved) would leave the admin tile
    pointing at a dead port.
    """
    monkeypatch.delenv("ADOPTIQ_ADMIN_PORT", raising=False)
    monkeypatch.delenv("ADOPTIQ_MAIN_URL", raising=False)
    assert admin_app._DEFAULT_ADMIN_PORT == 5152
    assert admin_app._resolve_admin_port() == 5152
    # Env override + edge cases.
    assert admin_app._resolve_admin_port({"ADOPTIQ_ADMIN_PORT": "7000"}) == 7000
    assert admin_app._resolve_admin_port({"ADOPTIQ_ADMIN_PORT": "abc"}) == 5152
    assert admin_app._resolve_admin_port({"ADOPTIQ_ADMIN_PORT": "70000"}) == 5152
    # Round 148: runtime URL helpers (not import-time MAIN_APP_URL) must
    # honour the 5151 default when the env var is absent.
    assert admin_app._live_main_url().endswith(":5151")
    _, main_port = admin_app._main_app_host_port()
    assert main_port == 5151
