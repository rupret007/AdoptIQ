"""Round 97: TACTrack-style startup splash parity for AdoptIQ."""

from __future__ import annotations

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

    assert "AdoptIQ is starting..." in html
    assert "http://localhost:5151/ping" in html
    assert "http://localhost:5151/" in html
    assert "waitForAdoptIQ" in html
    assert "window.location.replace(appUrl)" in html
    assert "Reports remain safe to run while the knowledge corpus indexes" in html


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
        assert "AdoptIQ is starting..." in html
        assert "http://localhost:5151/ping" in html
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

    assert "Round 97: mirror TACTrack's macOS launch UX" in source
    assert "Round 99: do not exec the long-running browser-only Flask process" in source
    assert "APP_BINARY=\"$MACOS_DIR/AdoptIQ.bin\"" in source
    assert "mv \"$APP_EXECUTABLE\" \"$APP_BINARY\"" in source
    assert "ADOPTIQ_LAUNCHER_SPLASH_SHOWN=1" in source
    assert 'const pingUrl = appUrl + "ping";' in source
    assert "nohup \"$APP_DIR/AdoptIQ.bin\" \"$@\" >/dev/null 2>&1 &" in source
    assert "disown \"$!\" 2>/dev/null || true" in source
    assert "exec \"$APP_DIR/AdoptIQ.bin\" \"$@\"" not in source


def test_round97_2_build_smoke_checks_startup_and_status_endpoints():
    source = (REPO_ROOT / "scripts" / "test_build_smoke.sh").read_text(encoding="utf-8")

    assert "Default: dist/AdoptIQ.app" in source
    assert 'DEFAULT_APP_PATH="$ROOT_DIR/dist/AdoptIQ.app"' in source
    assert "Waiting for /ping readiness" in source
    assert "GET /ping -> OK" in source
    assert "GET /api/version -> valid build JSON" in source
    assert "process_started_at_utc" in source
    assert "restart_required" in source
    assert "GET /api/status/all -> valid JSON" in source
    assert "GET /api/corpus/status -> valid corpus JSON" in source


def test_main_startup_uses_launcher_marker_to_suppress_duplicate_browser_tab():
    source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    marker_idx = source.find("launcher_splash_shown = _launcher_splash_already_shown()")
    assert marker_idx > 0

    window = source[marker_idx : marker_idx + 1200]
    assert "if not launcher_splash_shown:" in window
    assert "_open_startup_splash(PORT)" in window
    assert "threading.Thread(target=_open_browser" in window
    assert "_open_browser_url('http://localhost:%s/' % PORT)" in window
