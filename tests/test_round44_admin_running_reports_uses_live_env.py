"""Round 44 / Phase 8 regression test.

Pin the admin-dashboard running-reports + verbose-debug HTTP fetches so
they re-read ``ADOPTIQ_MAIN_URL`` per call instead of trusting the
module-level ``MAIN_APP_URL`` constant captured at import time.

Pre-fix the dashboard's ``get_dashboard()`` view at
``enhanced_admin_dashboard_v2.py:2988 / :3002`` did:

    response = requests.get(f'{MAIN_APP_URL.rstrip("/")}/api/status/all', ...)
    debug_resp = requests.get(f'{MAIN_APP_URL.rstrip("/")}/api/debug/verbose', ...)

But ``app_simple.py:157-161`` eagerly imports the admin module to pull
the audit helpers BEFORE ``app_simple.py:210`` writes
``os.environ['ADOPTIQ_MAIN_URL']``.  So ``MAIN_APP_URL`` was captured
when the env was unset, defaulting to ``http://localhost:5151``.  When
the packaged ``.app`` ran the main UI on a non-default port (port-in-
use fallback), the dashboard's "Currently Running Reports" tile
rendered "n/a -- main app unreachable" in red even though the main app
was up and reachable -- the fetch was just going to the wrong port.

Round 44 / Phase 8 introduces ``_live_main_url()`` mirroring
``_main_app_host_port()``'s Round 37 re-read pattern and routes both
HTTP fetches through it.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import enhanced_admin_dashboard_v2 as admin_mod


REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_PATH = REPO_ROOT / "enhanced_admin_dashboard_v2.py"


def test_live_main_url_helper_exists() -> None:
    """The Round 44 / Phase 8 helper MUST be exposed at module level."""

    assert hasattr(admin_mod, "_live_main_url"), (
        "enhanced_admin_dashboard_v2 must expose _live_main_url per "
        "Round 44 / Phase 8."
    )


def test_live_main_url_re_reads_env_per_call(monkeypatch) -> None:
    """``_live_main_url()`` MUST re-read ``ADOPTIQ_MAIN_URL`` per call,
    so monkeypatching the env AFTER the module was imported still
    affects the returned URL.  This is the whole point of the helper:
    the module-level ``MAIN_APP_URL`` constant alone is captured at
    import time and would otherwise be permanently stale."""

    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:15999")
    assert admin_mod._live_main_url() == "http://127.0.0.1:15999"

    monkeypatch.setenv("ADOPTIQ_MAIN_URL", "http://127.0.0.1:25888/")
    # Trailing slash MUST be stripped so callers can safely concat
    # f"{_live_main_url()}/api/...".
    assert admin_mod._live_main_url() == "http://127.0.0.1:25888"


def test_live_main_url_falls_back_when_env_unset(monkeypatch) -> None:
    """When the env is unset entirely, the helper falls back to the
    captured ``MAIN_APP_URL`` constant (which itself defaults to
    ``http://localhost:5151``)."""

    monkeypatch.delenv("ADOPTIQ_MAIN_URL", raising=False)
    out = admin_mod._live_main_url()
    # Don't pin the exact default since dev shells may have set their own
    # MAIN_APP_URL during import; just require a non-empty URL with no
    # trailing slash.
    assert out, "fallback URL must not be empty"
    assert not out.endswith("/")
    assert out.startswith("http")


def test_dashboard_fetches_use_live_main_url() -> None:
    """The two ``requests.get`` sites in ``get_dashboard()`` MUST route
    through ``_live_main_url()`` instead of the stale module-level
    ``MAIN_APP_URL`` constant."""

    src = ADMIN_PATH.read_text(encoding="utf-8")
    # Round 44 / Phase 8: both fetches MUST use the helper.
    assert (
        "requests.get(f'{_live_main_url()}/api/status/all'" in src
    ), (
        "Round 44 / Phase 8: running-reports fetch must use "
        "_live_main_url()."
    )
    assert (
        "requests.get(f'{_live_main_url()}/api/debug/verbose'" in src
    ), (
        "Round 44 / Phase 8: verbose-debug fetch must use "
        "_live_main_url()."
    )
    # Defense in depth: the pre-fix raw-MAIN_APP_URL substrings must not
    # appear in get_dashboard()'s scope.  Search for the specific pre-
    # fix patterns that were live in Build-20.
    assert (
        "requests.get(f'{MAIN_APP_URL.rstrip(\"/\")}/api/status/all'"
        not in src
    )
    assert (
        "requests.get(f'{MAIN_APP_URL.rstrip(\"/\")}/api/debug/verbose'"
        not in src
    )
