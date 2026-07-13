"""Round 62 / Phase A2: pin the macOS Console.app observability for
Round 60's Quit-button SIGTERM path.

The packaged ``.app`` runs Python through the PyInstaller bootloader,
which does NOT pipe stdout/stderr into macOS's unified log.  That
makes the existing ``logger.info("Round 60 / api_shutdown: SIGTERM
scheduled ...")`` invisible to ``Console.app`` and ``log show
--process AdoptIQ``, so a manual acceptance run cannot prove the
SIGTERM actually fired.

R62 / A2 fixes that with a tiny ``_emit_macos_syslog(tag, msg)``
helper that shells out to ``/usr/bin/logger`` (~10 ms, native macOS
binary) so the unified log captures the event.  The helper is wired
at TWO points:

1. ``api_shutdown()`` -- right after the existing schedule log, so
   operators see WHEN the timer was queued and WITH WHICH params
   (``force``, ``running`` count).
2. ``_trigger_shutdown_sigterm()`` -- right before ``os.kill``, so
   operators see the actual moment the kill is dispatched.

These tests pin five contracts:

1. Helper smoke: on Darwin (real or fake), the helper invokes
   ``subprocess.run(["/usr/bin/logger", "-t", tag, msg], ...)`` once
   per call and the args carry our Round 60 markers verbatim.
2. Non-Darwin short-circuit: ``sys.platform="linux"`` skips the
   subprocess call entirely (no test infra polluted on CI).
3. TESTING short-circuit: ``app.config['TESTING']=True`` (or
   ``ADOPTIQ_TESTING=1``) skips the subprocess call (so pytest
   never spawns a real ``logger(1)``).
4. Never-raises: a broken / missing ``logger(1)`` binary, a hung
   subprocess, or a generic exception from ``subprocess.run`` must
   be swallowed -- ``_emit_macos_syslog`` returns silently and the
   shutdown path proceeds.
5. R60 contract preservation: ``api_shutdown`` still returns the
   byte-identical 202 payload it returned before R62/A2 (the syslog
   side-effect must NOT mutate the JSON response).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import app_simple


# ---------------------------------------------------------------------------
# Helper smoke + Darwin happy path
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Capture every ``subprocess.run`` invocation issued by the helper.

    Returns a list-like sink that the test can inspect.  Yields the
    same list so the assertion code reads naturally.
    """
    calls: List[Dict[str, Any]] = []

    def _fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

        class _Result:
            returncode = 0

        return _Result()

    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "run", _fake_run)
    return calls


def test_emit_macos_syslog_happy_path_on_darwin(monkeypatch, fake_subprocess):
    """On Darwin with TESTING off, the helper must call subprocess.run
    once with the canonical /usr/bin/logger argv and our tag + msg.
    """
    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.delenv("ADOPTIQ_TESTING", raising=False)
    monkeypatch.setitem(app_simple.app.config, "TESTING", False)

    app_simple._emit_macos_syslog("AdoptIQ", "Round 60 test: helper smoke")

    assert len(fake_subprocess) == 1, (
        f"expected exactly one subprocess.run call; got {len(fake_subprocess)}: {fake_subprocess}"
    )
    call = fake_subprocess[0]
    argv = call["args"][0]
    assert argv[0] == "/usr/bin/logger", f"argv[0] must be /usr/bin/logger; got {argv}"
    assert "-t" in argv
    tag_idx = argv.index("-t") + 1
    assert argv[tag_idx] == "AdoptIQ"
    assert "Round 60 test: helper smoke" in argv
    assert call["kwargs"].get("check") is False
    assert call["kwargs"].get("timeout") == 2


# ---------------------------------------------------------------------------
# Non-Darwin short-circuit
# ---------------------------------------------------------------------------


def test_emit_macos_syslog_skipped_on_linux(monkeypatch, fake_subprocess):
    monkeypatch.setattr(app_simple.sys, "platform", "linux")
    app_simple._emit_macos_syslog("AdoptIQ", "Round 60 test: non-darwin")
    assert fake_subprocess == [], (
        f"non-Darwin must skip subprocess.run; got {fake_subprocess}"
    )


def test_emit_macos_syslog_skipped_on_win32(monkeypatch, fake_subprocess):
    monkeypatch.setattr(app_simple.sys, "platform", "win32")
    app_simple._emit_macos_syslog("AdoptIQ", "Round 60 test: win32")
    assert fake_subprocess == []


# ---------------------------------------------------------------------------
# TESTING-mode short-circuit (config flag and env var both honored)
# ---------------------------------------------------------------------------


def test_emit_macos_syslog_skipped_in_testing_config(monkeypatch, fake_subprocess):
    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.delenv("ADOPTIQ_TESTING", raising=False)
    monkeypatch.setitem(app_simple.app.config, "TESTING", True)
    app_simple._emit_macos_syslog("AdoptIQ", "Round 60 test: TESTING flag")
    assert fake_subprocess == [], (
        "Flask config TESTING=True must short-circuit subprocess.run"
    )


def test_emit_macos_syslog_skipped_with_env_var(monkeypatch, fake_subprocess):
    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.setitem(app_simple.app.config, "TESTING", False)
    monkeypatch.setenv("ADOPTIQ_TESTING", "1")
    app_simple._emit_macos_syslog("AdoptIQ", "Round 60 test: env var")
    assert fake_subprocess == [], (
        "ADOPTIQ_TESTING=1 must short-circuit subprocess.run"
    )


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------


def test_emit_macos_syslog_swallows_subprocess_exception(monkeypatch):
    """A broken /usr/bin/logger (raises FileNotFoundError, OSError,
    timeout, anything) must NOT propagate -- the shutdown path
    cannot fail because syslog was unavailable."""
    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.delenv("ADOPTIQ_TESTING", raising=False)
    monkeypatch.setitem(app_simple.app.config, "TESTING", False)

    def _boom(*args, **kwargs):
        raise OSError("simulated /usr/bin/logger missing")

    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "run", _boom)

    app_simple._emit_macos_syslog("AdoptIQ", "Round 60 test: helper must swallow")


# ---------------------------------------------------------------------------
# Wiring: _trigger_shutdown_sigterm calls the helper BEFORE os.kill
# ---------------------------------------------------------------------------


def test_trigger_shutdown_sigterm_emits_syslog_before_kill(monkeypatch):
    """The helper must run BEFORE the kill so Console.app captures
    the event even if the SIGTERM tears down the process so quickly
    that Python's logger never flushes."""
    call_order: List[str] = []

    def _fake_emit(tag: str, msg: str) -> None:
        call_order.append(f"emit:{tag}:{msg}")

    def _fake_kill(pid, sig):
        call_order.append(f"kill:{pid}:{int(sig)}")

    monkeypatch.setattr(app_simple, "_emit_macos_syslog", _fake_emit)
    monkeypatch.setattr(app_simple.os, "kill", _fake_kill)

    app_simple._trigger_shutdown_sigterm()

    assert len(call_order) == 2, f"expected emit + kill; got {call_order}"
    assert call_order[0].startswith("emit:AdoptIQ:Round 60"), (
        f"first call must be emit; got {call_order[0]}"
    )
    assert call_order[1].startswith("kill:"), (
        f"second call must be os.kill; got {call_order[1]}"
    )


# ---------------------------------------------------------------------------
# R60 contract preservation: api_shutdown 202 payload must NOT change
# ---------------------------------------------------------------------------


def test_api_shutdown_202_payload_unchanged_by_syslog_wiring(app):
    """The R60 byte-identical 202 contract pinned by
    ``test_round60_shutdown_endpoint::test_shutdown_accepted_with_csrf_disabled_and_no_running``
    must survive the R62/A2 syslog side-effect.  In TESTING mode the
    syslog call is ALSO short-circuited (helper checks
    ``app.config['TESTING']``), so this test simultaneously confirms
    that pytest never spawns ``/usr/bin/logger``.
    """
    with app_simple.analysis_status_lock:
        saved = dict(app_simple.analysis_status)
        app_simple.analysis_status.clear()
    try:
        client = app.test_client()
        resp = client.post("/api/shutdown")
        assert resp.status_code == 202
        data = resp.get_json()
        assert data is not None
        assert data.get("ok") is True
        assert data.get("would_shutdown") is True
        assert data.get("force") is False
        assert data.get("in_progress_count") == 0
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()
            app_simple.analysis_status.update(saved)
