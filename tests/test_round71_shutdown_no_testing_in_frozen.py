"""Round 71 / Phase 1 (#8) -- shutdown TESTING short-circuit hardened.

Pre-R71 ``/api/shutdown`` honored ``ADOPTIQ_TESTING=1`` env var as a
"would_shutdown=True, do nothing" short-circuit.  The intent was to
let pytest exercise the endpoint without killing itself.  But in a
frozen build (the .app / .exe operators actually run), an attacker
who could set the env var (e.g. via a phishing-installed launchd job)
could turn ``/api/shutdown`` into a permanent no-op so the operator
clicking "Quit" never actually killed the process.

Round 71 / Phase 1 (#8) mutes the env var in frozen builds; only the
Flask config flag (set by the pytest fixture inside the same process)
survives there.
"""

from __future__ import annotations

import os
import sys
from unittest import mock


def test_round71_pytest_path_still_short_circuits_via_flask_config(client) -> None:
    """The Flask config TESTING flag (set by conftest.py) MUST still
    short-circuit the shutdown so pytest doesn't terminate itself."""
    resp = client.post("/api/shutdown")
    # The endpoint should accept the request (CSRF disabled in tests),
    # see TESTING=True via Flask config, and respond with 202 +
    # would_shutdown=True.
    assert resp.status_code == 202, (
        f"Pytest TESTING path should short-circuit with 202; got {resp.status_code}."
    )
    body = resp.get_json()
    assert body and body.get("would_shutdown") is True, (
        f"Pytest TESTING path should respond with would_shutdown=True; "
        f"got body={body!r}."
    )


def test_round71_env_var_testing_ignored_in_frozen_builds() -> None:
    """In frozen builds (``sys.frozen=True``), ``ADOPTIQ_TESTING=1``
    env var MUST be ignored -- only ``app.config['TESTING']`` should
    short-circuit the shutdown.  This is the pre-R71 spoof bypass."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text(encoding="utf-8")
    # Source-shape pin: the handler must explicitly check is_frozen and
    # mute env_testing.
    assert "if env_testing and is_frozen:" in src, (
        "Round 71 / Phase 1 (#8): /api/shutdown handler must include "
        "``if env_testing and is_frozen:`` branch that mutes the env-var "
        "TESTING short-circuit in packaged .app / .exe builds."
    )
    assert "env_testing = False" in src, (
        "Round 71 / Phase 1 (#8): the frozen-build branch must explicitly "
        "set env_testing=False so the live shutdown path runs."
    )


def test_round71_flask_config_takes_precedence_over_env_var() -> None:
    """Even when ``ADOPTIQ_TESTING=1`` is set, the Flask config flag
    is the only signal honored in a frozen build.  Confirm the OR-pair
    in the source so a future refactor cannot quietly invert it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app_simple.py").read_text(encoding="utf-8")
    assert "in_testing_mode = flask_testing or env_testing" in src, (
        "Round 71 / Phase 1 (#8): in_testing_mode must remain ``flask_testing "
        "or env_testing`` so the Flask config flag (set inside the process by "
        "the pytest fixture) always survives, regardless of frozen state."
    )
