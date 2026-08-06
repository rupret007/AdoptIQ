"""Round 97: TACTrack-style startup splash parity for AdoptIQ."""
from __future__ import annotations
from source_shape_utils import assert_in_source, assert_not_in_source


import os
from pathlib import Path

import app_simple


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ping_returns_plain_ok(client):
    response = client.get("/ping")

    assert response.status_code == 200
    assert response.data == b"OK"
    assert response.content_type.startswith("text/plain")


def test_open_browser_url_uses_usr_bin_open_on_macos(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.setattr(app_simple.subprocess, "Popen", FakePopen)

    assert app_simple._open_browser_url("http://localhost:5151/") is True
    assert captured["argv"] == ["/usr/bin/open", "http://localhost:5151/"]
    assert captured["kwargs"]["stdout"] is app_simple.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is app_simple.subprocess.DEVNULL


def test_open_browser_url_rejects_blank_url(monkeypatch):
    captured = {"called": False}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["called"] = True

    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.setattr(app_simple.subprocess, "Popen", FakePopen)

    assert app_simple._open_browser_url("   ") is False
    assert captured["called"] is False


def test_open_browser_url_falls_back_to_webbrowser_off_macos(monkeypatch):
    captured = {}

    def fake_open(url):
        captured["url"] = url
        return True

    monkeypatch.setattr(app_simple.sys, "platform", "linux")
    monkeypatch.setattr(app_simple.webbrowser, "open", fake_open)

    assert app_simple._open_browser_url("http://localhost:5151/") is True
    assert captured["url"] == "http://localhost:5151/"


def test_launcher_splash_marker_suppresses_delayed_browser_open():
    assert app_simple._launcher_splash_already_shown({"ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "1"})
    assert app_simple._launcher_splash_already_shown({"ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "true"})
    assert app_simple._launcher_splash_already_shown({"ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "on"})
    assert not app_simple._launcher_splash_already_shown({})
    assert not app_simple._launcher_splash_already_shown({"ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "0"})


def test_startup_splash_html_polls_ping_and_redirects():
    html = app_simple._startup_splash_html(5151)

    assert_in_source(html, "AdoptIQ is starting...", label='html')
    assert_in_source(html, "http://localhost:5151/ping", label='html')
    assert_in_source(html, "http://localhost:5151/", label='html')
    assert_in_source(html, "waitForAdoptIQ", label='html')
    assert_in_source(html, "window.location.replace(appUrl)", label='html')
    assert_in_source(html, "Reports remain safe to run while the knowledge corpus indexes", label='html')


def test_open_startup_splash_uses_usr_bin_open_for_frozen_macos(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.setattr(app_simple.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_simple.subprocess, "Popen", FakePopen)

    try:
        assert app_simple._open_startup_splash(5151) is True
        assert captured["argv"][0] == "/usr/bin/open"
        splash_path = captured["argv"][1]
        assert splash_path.endswith(".html")
        html = Path(splash_path).read_text(encoding="utf-8")
        assert_in_source(html, "AdoptIQ is starting...", label='html')
        assert_in_source(html, "http://localhost:5151/ping", label='html')
        assert captured["kwargs"]["stdout"] is app_simple.subprocess.DEVNULL
        assert captured["kwargs"]["stderr"] is app_simple.subprocess.DEVNULL
    finally:
        if captured.get("argv"):
            try:
                os.remove(captured["argv"][1])
            except OSError:
                pass


def test_open_startup_splash_noops_outside_frozen_macos(monkeypatch):
    captured = {"called": False}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["called"] = True

    monkeypatch.setattr(app_simple.subprocess, "Popen", FakePopen)

    monkeypatch.setattr(app_simple.sys, "platform", "linux")
    monkeypatch.setattr(app_simple.sys, "frozen", True, raising=False)
    assert app_simple._open_startup_splash(5151) is False

    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.setattr(app_simple.sys, "frozen", False, raising=False)
    assert app_simple._open_startup_splash(5151) is False

    assert captured["called"] is False


def test_open_startup_splash_rejects_invalid_ports(monkeypatch):
    captured = {"called": False}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["called"] = True

    monkeypatch.setattr(app_simple.sys, "platform", "darwin")
    monkeypatch.setattr(app_simple.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_simple.subprocess, "Popen", FakePopen)

    assert app_simple._open_startup_splash(0) is False
    assert app_simple._open_startup_splash(70000) is False
    assert app_simple._open_startup_splash("not-a-port") is False
    assert captured["called"] is False


def test_build_mac_installs_launcher_wrapper():
    source = (REPO_ROOT / "build_mac.sh").read_text(encoding="utf-8")

    assert_in_source(source, "Round 97: mirror TACTrack's macOS launch UX", label='source')
    assert_in_source(source, "Round 99: do not exec the long-running browser-only Flask process", label='source')
    assert_in_source(source, "APP_BINARY=\"$MACOS_DIR/AdoptIQ.bin\"", label='source')
    assert_in_source(source, "mv \"$APP_EXECUTABLE\" \"$APP_BINARY\"", label='source')
    assert_in_source(source, "ADOPTIQ_LAUNCHER_SPLASH_SHOWN=1", label='source')
    assert_in_source(source, 'const pingUrl = appUrl + "ping";', label='source')
    assert_in_source(source, "nohup \"$APP_DIR/AdoptIQ.bin\" \"$@\" >/dev/null 2>&1 &", label='source')
    assert_in_source(source, "disown \"$!\" 2>/dev/null || true", label='source')
    assert_not_in_source(source, "exec \"$APP_DIR/AdoptIQ.bin\" \"$@\"", label='source')


def test_round97_2_build_smoke_checks_startup_and_status_endpoints():
    source = (REPO_ROOT / "scripts" / "test_build_smoke.sh").read_text(encoding="utf-8")

    assert_in_source(source, "Default: dist/AdoptIQ.app", label='source')
    assert_in_source(source, 'DEFAULT_APP_PATH="$ROOT_DIR/dist/AdoptIQ.app"', label='source')
    assert_in_source(source, "Waiting for /ping readiness", label='source')
    assert_in_source(source, "GET /ping -> OK", label='source')
    assert_in_source(source, "GET /api/version -> valid build JSON", label='source')
    assert_in_source(source, "process_started_at_utc", label='source')
    assert_in_source(source, "restart_required", label='source')
    assert_in_source(source, "GET /api/status/all -> valid JSON", label='source')
    assert_in_source(source, "GET /api/corpus/status -> valid corpus JSON", label='source')


def test_main_startup_uses_launcher_marker_to_suppress_duplicate_browser_tab():
    source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    marker_idx = source.find("launcher_splash_shown = _launcher_splash_already_shown()")
    assert marker_idx > 0

    window = source[marker_idx : marker_idx + 1200]
    assert_in_source(window, "if not launcher_splash_shown:", label='window')
    assert_in_source(window, "_open_startup_splash(PORT)", label='window')
    assert_in_source(window, "threading.Thread(target=_open_browser", label='window')
    assert_in_source(window, "_open_browser_url('http://localhost:%s/' % PORT)", label='window')
