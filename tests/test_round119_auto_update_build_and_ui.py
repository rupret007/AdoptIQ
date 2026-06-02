"""Round 119 / Build 88 -- build-script + spec + UI source-shape pins.

These are source-shape assertions (the build scripts run on their own hosts;
the UI runs in the browser).  They pin the contract so a future refactor that
drops the manifest emit, the versioned artifact, the hidden import, or the
DOM-safe rendering fails loud in CI.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# build_mac_dmg.sh
# ---------------------------------------------------------------------------
def test_mac_build_emits_manifest():
    src = _read("build_mac_dmg.sh")
    assert "write_release_manifest.py" in src
    assert "--platform mac" in src
    assert "shasum -a 256" in src
    assert "latest.json" in src


# ---------------------------------------------------------------------------
# build_pc.bat
# ---------------------------------------------------------------------------
def test_pc_build_emits_manifest_and_versioned_exe():
    src = _read("build_pc.bat")
    assert "write_release_manifest.py" in src
    assert "--platform pc" in src
    assert "VERSIONED_EXE" in src
    assert "latest.json" in src
    # SHA256 computed via PowerShell Get-FileHash.
    assert "Get-FileHash" in src


# ---------------------------------------------------------------------------
# PyInstaller specs bundle auto_updater as a hidden import
# ---------------------------------------------------------------------------
def test_mac_spec_hidden_imports_auto_updater():
    assert "auto_updater" in _read("adoptiq_mac.spec")


def test_pc_spec_hidden_imports_auto_updater():
    assert "auto_updater" in _read("adoptiq_pc.spec")


# ---------------------------------------------------------------------------
# config.py releases discovery
# ---------------------------------------------------------------------------
def test_config_releases_helpers_present():
    src = _read("config.py")
    assert "_releases_candidates" in src
    assert "_resolve_releases_folder" in src
    assert "ADOPTIQ_RELEASES_FOLDER" in src


def test_config_releases_folder_resolves_to_string():
    from config import Config
    assert isinstance(Config.ADOPTIQ_RELEASES_FOLDER, str)
    assert "OUTBOX" in Config.ADOPTIQ_RELEASES_FOLDER


# ---------------------------------------------------------------------------
# Preferences card + JS source-shape
# ---------------------------------------------------------------------------
def test_preferences_card_present():
    src = _read("templates/preferences.html")
    assert "data-auto-update-card" in src
    assert "data-auto-update-mode-select" in src
    assert "r119_auto_update.js" in src


def test_r119_js_dom_safe():
    src = _read("static/js/r119_auto_update.js")
    # No XSS sinks; CSRF header used; endpoints referenced.
    assert ".innerHTML" not in src
    assert "X-CSRFToken" in src
    assert "/api/update/status" in src
    assert "/api/settings/auto-update-mode" in src
    assert "/api/update/apply" in src


def test_base_html_update_banner_present():
    src = _read("templates/base.html")
    assert "r119-update-available-banner" in src
    assert "r119-update-install-btn" in src
    assert "Relaunch to update" in src


def test_restart_banner_js_polls_update_status():
    src = _read("static/js/r68_restart_banner.js")
    assert "/api/update/status" in src
    assert "/api/update/apply" in src
    # DOM-safe rendering only.
    assert ".innerHTML" not in src
