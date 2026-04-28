"""Round 37 / Phase 1: pin the admin module reads the live ``ADOPTIQ_MAIN_URL``
env per call instead of the import-time constant.

Why this matters:

The packaged .app boots `app_simple.py`, which knows the live main port
(``_resolve_main_port()``).  Before Round 37 the admin module captured
``MAIN_APP_URL`` once at import and never re-read the env, so when
``app_simple._start_admin_server_in_thread`` later set
``ADOPTIQ_MAIN_URL=http://127.0.0.1:15152`` the admin still pointed at
the stale ``localhost:5151`` fallback and rendered "Start Server"
forever while the main app was alive on 15152.

Round 37 fixes this two ways:

1. The parent process sets ``ADOPTIQ_MAIN_URL`` BEFORE importing the
   admin module (this test does NOT cover that -- see
   ``test_round37_admin_start_server_button_hidden_when_running.py``).
2. ``_main_app_host_port()`` re-reads ``os.environ.get(
   'ADOPTIQ_MAIN_URL')`` per call.  This test pins (2).
"""

# ruff: noqa: E501

from __future__ import annotations

import importlib
import os
import re

import pytest


def _import_admin_module():
    """Import the admin module fresh.  We do not call
    ``importlib.reload`` because the admin Flask routes are registered
    at import time and a reload would race the global Flask app
    against the test session.  Instead we rely on the per-call
    ``os.environ.get`` lookup we are testing."""
    import enhanced_admin_dashboard_v2 as adm  # noqa: PLC0415
    return adm


def test_main_app_host_port_reads_live_env_first(monkeypatch):
    """Setting ADOPTIQ_MAIN_URL after import MUST be visible to
    ``_main_app_host_port()`` -- the legacy module-level
    ``MAIN_APP_URL`` constant must NOT shadow the live env."""
    adm = _import_admin_module()
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:15152")
    host, port = adm._main_app_host_port()
    assert host == "127.0.0.1", f"expected 127.0.0.1, got {host!r}"
    assert port == 15152, (
        f"expected port 15152 from the live env, got {port!r}; "
        "_main_app_host_port is still reading the import-time constant"
    )


def test_main_app_host_port_picks_up_env_change_within_one_call(monkeypatch):
    """Defense-in-depth pin: a later env mutation MUST take effect on
    the very next call (no module reload required).  Catches a
    regression where someone caches the parsed URL in a closure."""
    adm = _import_admin_module()
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:5151")
    host_a, port_a = adm._main_app_host_port()
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:15152")
    host_b, port_b = adm._main_app_host_port()
    assert host_a == host_b == "127.0.0.1"
    assert port_a == 5151
    assert port_b == 15152, (
        f"second call returned port {port_b}; expected 15152 -- "
        "_main_app_host_port appears to cache the parsed URL"
    )


def test_main_app_host_port_falls_back_when_env_unset(monkeypatch):
    """When ``ADOPTIQ_MAIN_URL`` is unset, the function must fall back
    to the documented default (``localhost:5151``) rather than
    crashing on a None URL."""
    adm = _import_admin_module()
    monkeypatch.delenv("ADOPTIQ_MAIN_URL", raising=False)
    host, port = adm._main_app_host_port()
    # Either the import-time captured constant (still localhost:5151
    # by default) or the explicit fallback inside the helper.  Both
    # are acceptable so long as the answer is a valid loopback
    # address on the documented default port.
    assert host in ("localhost", "127.0.0.1"), host
    assert port == 5151, port


def test_main_app_host_port_does_not_crash_on_garbage_url(monkeypatch):
    """Round 37 pin: if a misconfigured deployment writes garbage into
    ``ADOPTIQ_MAIN_URL`` (e.g. ``"not a url"``), the helper must
    return SOMETHING usable rather than blowing up the dashboard
    render.  The bare-except in the helper guarantees this."""
    adm = _import_admin_module()
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "this-is-not-a-url")
    host, port = adm._main_app_host_port()
    assert isinstance(host, str) and host
    assert isinstance(port, int) and 1 <= port <= 65535


def test_app_simple_writes_adoptiq_main_url_before_admin_import():
    """Source-level pin: ``_start_admin_server_in_thread`` MUST set
    ``os.environ['ADOPTIQ_MAIN_URL']`` BEFORE the
    ``from enhanced_admin_dashboard_v2 import ...`` line.  Otherwise
    the admin module captures the stale default and (since
    Python module imports are cached) we never get a chance to
    fix it."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_path = os.path.join(repo_root, "app_simple.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        text = fh.read()

    # Locate the function body.
    func_match = re.search(
        r"def _start_admin_server_in_thread\(\) -> None:.*?(?=\n(?:def |class |@))",
        text,
        re.DOTALL,
    )
    assert func_match, (
        "Could not find _start_admin_server_in_thread in app_simple.py; "
        "did the function get renamed?"
    )
    body = func_match.group(0)

    # The env write must appear in the function body...
    env_write_pos = body.find("ADOPTIQ_MAIN_URL")
    assert env_write_pos != -1, (
        "_start_admin_server_in_thread does NOT write ADOPTIQ_MAIN_URL; "
        "Round 37 / Phase 1 requires this so the admin module captures "
        "the live main port at import time."
    )

    # ...AND it must appear BEFORE the admin module import.
    import_pos = body.find("from enhanced_admin_dashboard_v2 import")
    assert import_pos != -1, (
        "Could not find the admin module import in "
        "_start_admin_server_in_thread; the function shape changed."
    )
    assert env_write_pos < import_pos, (
        "ADOPTIQ_MAIN_URL is set AFTER the admin module is imported; "
        "Python module imports are cached, so the admin captures the "
        "stale default. Move the env write above the import."
    )


@pytest.mark.parametrize(
    "url, expected_host, expected_port",
    [
        ("http://127.0.0.1:15152", "127.0.0.1", 15152),
        ("http://localhost:5151", "localhost", 5151),
        ("http://127.0.0.1:65535", "127.0.0.1", 65535),
    ],
)
def test_main_app_host_port_parses_canonical_urls(monkeypatch, url, expected_host, expected_port):
    """Spot-check the URL parser on the URLs we actually deploy
    (.app uses 15152 in container; 5151 is the dev default)."""
    adm = _import_admin_module()
    monkeypatch.setenv("ADOPTIQ_MAIN_URL", url)
    host, port = adm._main_app_host_port()
    assert host == expected_host, host
    assert port == expected_port, port
