from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_round106_release_dmg_requires_unblock_assets() -> None:
    src = _read("build_mac_dmg.sh")

    assert 'UNBLOCK_SRC="scripts/mac/Unblock_AdoptIQ.command"' in src
    assert 'README_FIRST_SRC="scripts/mac/READ_ME_FIRST.txt"' in src
    assert 'tr -d \'\\r\' < "$UNBLOCK_SRC"' in src
    assert 'chmod +x "$STAGING_DIR/Unblock AdoptIQ.command"' in src
    assert 'bash -n "$STAGING_DIR/Unblock AdoptIQ.command"' in src
    assert "release DMG cannot ship without the unblock helper" in src
    assert "release DMG cannot ship without READ_ME_FIRST.txt" in src


def test_round106_unblock_helper_targets_installed_app_quarantine() -> None:
    src = _read("scripts/mac/Unblock_AdoptIQ.command")

    assert 'APP="/Applications/AdoptIQ.app"' in src
    assert "xattr -dr com.apple.quarantine" in src
    assert "/usr/bin/open \"$APP\"" in src


def test_round106_read_me_first_names_dmg_privacy_approval() -> None:
    src = _read("scripts/mac/READ_ME_FIRST.txt")

    assert "Apple could not verify" in src
    assert "System Settings > Privacy & Security" in src
    assert "Open Anyway" in src
    assert "Unblock AdoptIQ.command" in src


def test_round106_build_info_mentions_gatekeeper_order() -> None:
    src = _read("build_mac_dmg.sh")

    assert "Install notes:" in src
    assert "approve it in System Settings > Privacy & Security" in src
    assert "run Unblock AdoptIQ.command" in src
