from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_round106_release_dmg_requires_unblock_assets() -> None:
    src = _read("build_mac_dmg.sh")

    assert_in_source(src, 'UNBLOCK_SRC="scripts/mac/Unblock_AdoptIQ.command"', label='src')
    assert_in_source(src, 'README_FIRST_SRC="scripts/mac/READ_ME_FIRST.txt"', label='src')
    assert_in_source(src, 'tr -d \'\\r\' < "$UNBLOCK_SRC"', label='src')
    assert_in_source(src, 'chmod +x "$STAGING_DIR/Unblock AdoptIQ.command"', label='src')
    assert_in_source(src, 'bash -n "$STAGING_DIR/Unblock AdoptIQ.command"', label='src')
    assert_in_source(src, "release DMG cannot ship without the unblock helper", label='src')
    assert_in_source(src, "release DMG cannot ship without READ_ME_FIRST.txt", label='src')


def test_round106_unblock_helper_targets_installed_app_quarantine() -> None:
    src = _read("scripts/mac/Unblock_AdoptIQ.command")

    assert_in_source(src, 'APP="/Applications/AdoptIQ.app"', label='src')
    assert_in_source(src, "xattr -dr com.apple.quarantine", label='src')
    assert_in_source(src, "/usr/bin/open \"$APP\"", label='src')


def test_round106_read_me_first_names_dmg_privacy_approval() -> None:
    src = _read("scripts/mac/READ_ME_FIRST.txt")

    assert_in_source(src, "Apple could not verify", label='src')
    assert_in_source(src, "System Settings > Privacy & Security", label='src')
    assert_in_source(src, "Open Anyway", label='src')
    assert_in_source(src, "Unblock AdoptIQ.command", label='src')


def test_round106_build_info_mentions_gatekeeper_order() -> None:
    src = _read("build_mac_dmg.sh")

    assert_in_source(src, "Install notes:", label='src')
    assert_in_source(src, "approve it in System Settings > Privacy & Security", label='src')
    assert_in_source(src, "run Unblock AdoptIQ.command", label='src')
