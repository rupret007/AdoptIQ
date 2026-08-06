"""Round 87 / Phase 3 — launcher auto-quit-stale (source-shape + behavior).

Pre-R87 the duplicate-launch branch in ``app_simple.py`` routed the
user back to the already-running instance via R38.1's
``webbrowser.open + sys.exit(0)``.  That was correct for "user
double-clicked twice in 10 seconds" but wrong for "user installed
Build N+1 and double-clicked from /Applications while Build N is
still on port 5151".  In the upgrade-launch case the user got routed
to the OLD build with a "Restart required" banner and had to manually
quit + relaunch.

R87 / Phase 3 detects the stale-binary case and force-quits the older
instance so the new build can boot in its place.  Three new helpers
(``_query_running_instance_started_at``, ``_running_instance_is_stale``,
``_force_quit_existing_adoptiq``) carry the detection and kill logic;
the duplicate-launch branch is patched to call them when frozen.

The detection chain is intentionally narrow:
  * Frozen-only.  Dev iteration (``python app_simple.py``) must remain
    predictable; never auto-kill a terminal-session run.
  * Compares the running instance's ``process_started_at_utc`` against
    ``Path(sys.executable).stat().st_mtime`` with a 1.0 s tolerance.
  * Any failure -- bad port, version probe timeout, malformed JSON --
    falls back to the R38.1 re-route (preserves prior behavior).

The kill is ``os.kill(pid, SIGTERM)``, not the HTTP
``/api/shutdown`` endpoint.  HTTP requires shared CSRF or
``ADOPTIQ_INTERNAL_TOKEN`` between processes -- the new launcher
process can't share that cleanly without a sidecar pidfile + key
scheme.  SIGTERM still fires the ``atexit`` handlers in the running
process (``_shutdown_handler`` saves analysis_status,
``_r17_corpus_shutdown`` scrubs the corpus temp file).

Pinned by these tests:
  Source-shape (no behavior, just code-presence guarantees):
    * ``test_round_marker_present`` -- ``# Round 87 / Phase 3`` anchors
      the new bash lines so ``git diff app_simple.py | grep
      'Round 87'`` shows the per-file footprint.
    * ``test_helper_signatures_present`` -- the three helpers are
      defined at module level with the documented signatures.
    * ``test_duplicate_launch_branch_uses_r87_helpers`` -- the
      ``if _probe_existing_adoptiq(PORT):`` block now calls
      ``_running_instance_is_stale`` and ``_force_quit_existing_adoptiq``.
    * ``test_kill_uses_sigterm_not_sigkill`` -- the canonical signal is
      SIGTERM so atexit handlers fire.
    * ``test_frozen_only_guard`` -- the auto-kill chain is gated on
      ``getattr(sys, 'frozen', False)``.
    * ``test_dialog_logic_wrapped_in_post_block_check`` -- the second
      ``if not available:`` exists so the post-force-quit success path
      skips the in-use dialog cleanly.

  Behavior (mocked-IO unit tests, no real Flask or sockets):
    * ``test_query_running_instance_started_at_returns_none_on_bad_port``
    * ``test_query_running_instance_started_at_parses_payload``
    * ``test_running_instance_is_stale_false_when_not_frozen``
    * ``test_running_instance_is_stale_true_when_newer_binary``
    * ``test_running_instance_is_stale_false_when_older_binary``
    * ``test_force_quit_returns_false_when_no_pid``
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, count_in_source

import json
from io import BytesIO
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app_simple.py"


def _read_app_simple() -> str:
    assert APP_PATH.exists(), f"missing app_simple.py at {APP_PATH}"
    return APP_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins
# ---------------------------------------------------------------------------


def test_round_marker_present() -> None:
    """``# Round 87 / Phase 3`` anchors the new code for git-diff."""
    body = _read_app_simple()
    occurrences = count_in_source(body, "# Round 87 / Phase 3")
    assert occurrences >= 3, (
        f"expected >=3 'Round 87 / Phase 3' markers in app_simple.py "
        f"(helpers + branch + post-block guard); found {occurrences}"
    )


def test_helper_signatures_present() -> None:
    """The three R87 helpers exist with documented signatures."""
    body = _read_app_simple()
    assert_in_source(body, "def _query_running_instance_started_at(", label='body')
    assert_in_source(body, "def _running_instance_is_stale(", label='body')
    assert_in_source(body, "def _force_quit_existing_adoptiq(", label='body')


def test_duplicate_launch_branch_uses_r87_helpers() -> None:
    """The ``if _probe_existing_adoptiq(PORT):`` block calls the new helpers."""
    body = _read_app_simple()

    # Locate the duplicate-launch branch via the probe call site.
    probe_idx = body.find("if _probe_existing_adoptiq(PORT):")
    assert probe_idx >= 0, "missing the canonical probe call site"

    # Slice forward ~6000 chars to cover the new R87 logic without
    # accidentally matching the helper definitions earlier in the file.
    branch_window = body[probe_idx : probe_idx + 6000]

    assert "_running_instance_is_stale(PORT" in branch_window, (
        "the probe branch must call _running_instance_is_stale to "
        "detect the stale-binary case"
    )
    assert "_force_quit_existing_adoptiq(PORT" in branch_window, (
        "the probe branch must call _force_quit_existing_adoptiq to "
        "kill the stale instance"
    )


def test_kill_uses_sigterm_not_sigkill() -> None:
    """Canonical kill is SIGTERM so atexit handlers fire."""
    body = _read_app_simple()

    # Locate the helper itself.
    helper_idx = body.find("def _force_quit_existing_adoptiq(")
    assert helper_idx >= 0
    helper_body = body[helper_idx : helper_idx + 3000]

    assert "SIGTERM" in helper_body, (
        "_force_quit_existing_adoptiq must use SIGTERM (NOT SIGKILL) "
        "so the running process's atexit handlers fire and "
        "analysis_status / corpus temp file are cleaned up"
    )
    # The CALL to SIGKILL is what we forbid -- helper docstrings can
    # mention the word "SIGKILL" in negative-control commentary.  Look
    # for the actual usage pattern (``signal.SIGKILL`` or
    # ``_signal.SIGKILL``) instead of a bare substring.
    forbidden_patterns = (
        "signal.SIGKILL",
        ".SIGKILL)",
        ".SIGKILL,",
        ".SIGKILL\n",
    )
    for pat in forbidden_patterns:
        assert pat not in helper_body, (
            f"_force_quit_existing_adoptiq must NOT call SIGKILL "
            f"(matched forbidden pattern {pat!r}) -- atexit handlers "
            f"don't fire on SIGKILL and the running process would leak "
            f"a plaintext corpus temp file in $TMPDIR"
        )


def test_frozen_only_guard() -> None:
    """The auto-kill chain is gated on sys.frozen."""
    body = _read_app_simple()
    helper_idx = body.find("def _running_instance_is_stale(")
    assert helper_idx >= 0
    helper_body = body[helper_idx : helper_idx + 3000]

    assert_in_source(
        helper_body,
        "getattr(sys, 'frozen', False)",
        label="helper_body",
    )


def test_dialog_logic_wrapped_in_post_block_check() -> None:
    """The second ``if not available:`` exists so post-force-quit success
    skips the in-use dialog cleanly."""
    body = _read_app_simple()

    # The first match is the original outer guard around the probe
    # branch; the second is the new R87 wrapper around the dialog
    # logic.  Both must be present.
    occurrences = [
        i for i in range(len(body)) if body.startswith("    if not available:", i)
    ]
    assert len(occurrences) >= 2, (
        f"expected >=2 top-level 'if not available:' blocks (one around "
        f"the probe branch, one around the dialog logic); found "
        f"{len(occurrences)}"
    )


# ---------------------------------------------------------------------------
# Behavior pins (mocked IO)
# ---------------------------------------------------------------------------


def _import_app_simple_helpers():
    """Import the three R87 helpers without spinning up Flask state.

    ``app_simple`` is a heavy import; reuse whatever conftest already
    has loaded so we don't pay the Snowflake / corpus bootstrap cost
    per test.
    """
    import importlib

    return importlib.import_module("app_simple")


def test_query_running_instance_started_at_returns_none_on_bad_port():
    app_simple = _import_app_simple_helpers()
    # Negative port, out-of-range port, non-int port all return None.
    assert app_simple._query_running_instance_started_at(-1) is None
    assert app_simple._query_running_instance_started_at(70000) is None
    assert app_simple._query_running_instance_started_at("not-a-number") is None


def test_query_running_instance_started_at_parses_payload():
    app_simple = _import_app_simple_helpers()

    payload = {
        "ok": True,
        "version": "1.0.4",
        "build": "63",
        "process_started_at_utc": "2026-05-04T12:00:00+00:00",
        "code_loaded_at_utc": "2026-05-04T11:00:00+00:00",
        "dmg_install_at_utc": "2026-05-04T13:00:00+00:00",
        "restart_required": True,
        "frozen": True,
    }
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = json.dumps(payload).encode("utf-8")
    fake_resp.__enter__ = lambda self_: fake_resp
    fake_resp.__exit__ = lambda *args, **kwargs: False

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        got = app_simple._query_running_instance_started_at(5151)

    assert got == "2026-05-04T12:00:00+00:00"


def test_query_running_instance_started_at_returns_none_on_urlopen_failure():
    app_simple = _import_app_simple_helpers()
    with mock.patch(
        "urllib.request.urlopen", side_effect=ConnectionRefusedError("nope")
    ):
        assert app_simple._query_running_instance_started_at(5151) is None


def test_query_running_instance_started_at_returns_none_on_malformed_json():
    app_simple = _import_app_simple_helpers()

    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = b"not-valid-json{{{"
    fake_resp.__enter__ = lambda self_: fake_resp
    fake_resp.__exit__ = lambda *args, **kwargs: False

    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        assert app_simple._query_running_instance_started_at(5151) is None


def test_running_instance_is_stale_false_when_not_frozen():
    app_simple = _import_app_simple_helpers()
    # In dev mode (sys.frozen is unset/False), the helper short-circuits
    # to False even when the probe would have detected a stale running
    # instance.  Pinned so we never auto-kill a `python app_simple.py`
    # process running in a terminal.
    with mock.patch("sys.frozen", False, create=True):
        result = app_simple._running_instance_is_stale(5151, our_mtime=99999999.0)
    assert result is False


def test_running_instance_is_stale_true_when_newer_binary():
    app_simple = _import_app_simple_helpers()

    # Running instance started at epoch 1700000000.
    # Our binary mtime is epoch 1800000000 -- 100M seconds newer.
    # Frozen=True (simulating packaged .app).
    started_iso = "2023-11-14T22:13:20+00:00"  # ~ epoch 1700000000

    with mock.patch.object(
        app_simple,
        "_query_running_instance_started_at",
        return_value=started_iso,
    ), mock.patch("sys.frozen", True, create=True):
        result = app_simple._running_instance_is_stale(5151, our_mtime=1800000000.0)

    assert result is True


def test_running_instance_is_stale_false_when_older_binary():
    app_simple = _import_app_simple_helpers()

    # Running instance started AFTER our binary mtime -- we're the
    # stale binary, the running one is newer (or equal).  Should
    # return False so we fall back to the R38.1 re-route.
    started_iso = "2026-12-01T00:00:00+00:00"  # ~ epoch 1796774400

    with mock.patch.object(
        app_simple,
        "_query_running_instance_started_at",
        return_value=started_iso,
    ), mock.patch("sys.frozen", True, create=True):
        result = app_simple._running_instance_is_stale(5151, our_mtime=1700000000.0)

    assert result is False


def test_running_instance_is_stale_handles_zulu_shorthand():
    """``Z`` suffix (RFC 3339 Zulu form) must parse correctly."""
    app_simple = _import_app_simple_helpers()
    started_iso = "2023-11-14T22:13:20Z"  # same instant as previous test

    with mock.patch.object(
        app_simple,
        "_query_running_instance_started_at",
        return_value=started_iso,
    ), mock.patch("sys.frozen", True, create=True):
        result = app_simple._running_instance_is_stale(5151, our_mtime=1800000000.0)

    assert result is True


def test_force_quit_returns_false_when_no_pid():
    app_simple = _import_app_simple_helpers()
    # Pid=None and the port-probe also returns no pid -> False.
    with mock.patch.object(
        app_simple, "_check_port_available", return_value=(False, None, None)
    ):
        assert app_simple._force_quit_existing_adoptiq(5151, pid=None) is False


def test_force_quit_returns_false_when_kill_raises():
    app_simple = _import_app_simple_helpers()
    # PermissionError simulates a kill against a process owned by another
    # user (which shouldn't happen for a launcher path, but we still
    # want the helper to fail soft instead of crashing the boot path).
    with mock.patch("os.kill", side_effect=PermissionError("not allowed")):
        result = app_simple._force_quit_existing_adoptiq(5151, pid=12345)
    assert result is False


def test_force_quit_polls_until_port_free():
    app_simple = _import_app_simple_helpers()

    # Simulate port freeing on the third poll.
    poll_results = [
        (False, None, None),  # still bound
        (False, None, None),  # still bound
        (True, None, None),  # finally free
    ]

    with mock.patch("os.kill") as kill_mock, mock.patch.object(
        app_simple, "_check_port_available", side_effect=poll_results
    ):
        result = app_simple._force_quit_existing_adoptiq(
            5151, pid=12345, timeout=5.0, poll_interval=0.01
        )

    assert result is True
    assert kill_mock.call_count == 1, (
        "force-quit should send exactly one SIGTERM, not retry the kill"
    )


def test_force_quit_returns_false_on_timeout():
    app_simple = _import_app_simple_helpers()

    # Port never frees.
    with mock.patch("os.kill"), mock.patch.object(
        app_simple,
        "_check_port_available",
        return_value=(False, None, None),
    ):
        result = app_simple._force_quit_existing_adoptiq(
            5151, pid=12345, timeout=0.05, poll_interval=0.01
        )

    assert result is False
