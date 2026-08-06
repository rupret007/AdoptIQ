"""Round 128 / Build 97 -- Relaunch-to-update CTA + apply lock.

Pins Chrome-style manual relaunch in auto mode, status fields
``can_apply_now`` / ``apply_in_progress``, and the process-wide apply
lock that prevents double swapper spawn.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def test_base_html_relaunch_button_label():
  src = _read("templates/base.html")
  assert_in_source(src, "r119-update-install-btn", label='src')
  assert_in_source(src, "Relaunch to update", label='src')
  assert "Install update now" not in src


def test_restart_banner_js_always_shows_button_in_auto_mode():
  src = _read("static/js/r68_restart_banner.js")
  assert_in_source(src, "Round 128", label='src')
  assert_in_source(src, "Relaunch to update", label='src')
  # Build 97: auto mode must not hide the CTA (pre-R128 used d-none on btn).
  auto_block = src.split("if (mode === 'auto')")[1].split("} else if (detail)")[0]
  assert "d-none" not in auto_block or "btn.classList.add('d-none')" not in auto_block
  assert_in_source(src, "_R128_UPDATE_POLL_MS", label='src')
  assert_in_source(src, "can_apply_now", label='src')


def test_preferences_relaunch_button_label():
  src = _read("templates/preferences.html")
  assert_in_source(src, "Relaunch to update", label='src')


def test_r119_js_shows_install_in_auto_when_update_available():
  src = _read("static/js/r119_auto_update.js")
  assert_in_source(src, "Round 128", label='src')
  assert_in_source(src, "Relaunch to update", label='src')
  assert_in_source(src, "can_apply_now", label='src')


def test_update_status_includes_can_apply_now_and_apply_in_progress(client, monkeypatch):
  import app_simple

  monkeypatch.setattr(app_simple, "_r119_refresh_update_state", lambda: {
      "update_available": True,
      "latest_build": 99,
      "latest_version": "1.0.4",
  })
  monkeypatch.setattr(app_simple, "_r119_get_auto_update_mode", lambda: "auto")
  monkeypatch.setattr(app_simple, "_r119_current_build", lambda: 97)
  monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)

  with app_simple._r119_update_lock:
      app_simple._r128_apply_in_progress = False

  resp = client.get("/api/update/status")
  assert resp.status_code == 200
  data = resp.get_json()
  assert data["can_apply_now"] is True
  assert data["apply_in_progress"] is False


def test_update_status_can_apply_now_false_when_busy(client, monkeypatch):
  import app_simple

  monkeypatch.setattr(app_simple, "_r119_refresh_update_state", lambda: {})
  monkeypatch.setattr(app_simple, "_r119_get_auto_update_mode", lambda: "notify")
  monkeypatch.setattr(app_simple, "_r119_current_build", lambda: 97)
  monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: True)

  resp = client.get("/api/update/status")
  data = resp.get_json()
  assert data["can_apply_now"] is False


def test_r128_invoke_apply_update_blocks_while_in_progress(monkeypatch):
    """Concurrent worker + Relaunch: second caller must not spawn again."""
    import app_simple

    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)

    with app_simple._r119_update_lock:
        app_simple._r128_apply_in_progress = True

    blocked = app_simple._r128_invoke_apply_update(testing=True)
    assert blocked["state"] == "applying"
    assert blocked.get("reason") == "already_in_progress"

    with app_simple._r119_update_lock:
        app_simple._r128_apply_in_progress = False


def test_r128_invoke_apply_update_delegates_to_engine(monkeypatch):
    import app_simple

    monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)
    monkeypatch.setattr(
        "auto_updater.apply_update",
        lambda **k: {"ok": True, "state": "would_update", "latest_build": 99},
    )
    with app_simple._r119_update_lock:
        app_simple._r128_apply_in_progress = False

    result = app_simple._r128_invoke_apply_update(testing=True)
    assert result["state"] == "would_update"


def test_api_update_apply_uses_locked_helper(client, monkeypatch):
  import app_simple

  monkeypatch.setattr(app_simple, "_r17_2_authorize_corpus_admin", lambda: None)
  monkeypatch.setattr(app_simple, "_r119_update_is_busy", lambda: False)

  mock_invoke = MagicMock(return_value={"ok": True, "state": "would_update", "latest_build": 99})
  monkeypatch.setattr(app_simple, "_r128_invoke_apply_update", mock_invoke)
  monkeypatch.setattr(app_simple, "_r128_record_apply_attempt", lambda _r: None)

  resp = client.post("/api/update/apply", json={})
  assert resp.status_code == 200
  mock_invoke.assert_called_once()
