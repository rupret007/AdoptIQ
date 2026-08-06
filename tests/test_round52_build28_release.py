"""Round 52.1 / Build28 release packaging contracts."""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _build_mac_dmg_source() -> str:
    return (ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8")


def test_build_mac_dmg_signs_final_richer_dmg_before_mirroring():
    """The wrapper recreates the final DMG after build_mac.sh, so it
    must sign that final file before copying it to OneDrive mirrors."""
    src = _build_mac_dmg_source()

    create_idx = src.index('hdiutil create -volname "AdoptIQ" -srcfolder "$STAGING_DIR"')
    sign_idx = src.index('codesign --force --sign - --timestamp=none "$DMG_PATH"', create_idx)
    verify_idx = src.index('codesign --verify --strict "$DMG_PATH"', sign_idx)
    mirror_idx = src.index("MAC_STAGING_DIR=", create_idx)

    assert create_idx < sign_idx < verify_idx < mirror_idx


def test_build_mac_dmg_writes_build_info_before_staging_copy():
    """Mac staging promises build_info.txt; pin that the script
    creates it before the staging mirror copy block reads it."""
    src = _build_mac_dmg_source()

    write_idx = src.index('BUILD_INFO_PATH="OUTBOX/build_info.txt"')
    artifact_idx = src.index('echo "Artifact: $(basename "$DMG_PATH")"', write_idx)
    copy_idx = src.index('copy_or_die "OUTBOX/build_info.txt"', artifact_idx)

    assert write_idx < artifact_idx < copy_idx


def test_build_mac_dmg_retries_onedrive_app_signing():
    """The loose OneDrive .app mirror needs a settle/retry loop because
    OneDrive can inject xattrs immediately after ditto finishes."""
    src = _build_mac_dmg_source()

    retry_comment_idx = src.index("OneDrive can inject xattrs shortly after")
    loop_idx = src.index("for i in 1 2 3; do", retry_comment_idx)
    strip_idx = src.index('xattr -cr "$dest"', loop_idx)
    verify_idx = src.index('codesign --verify --deep --strict "$dest"', strip_idx)

    assert retry_comment_idx < loop_idx < strip_idx < verify_idx


def test_admin_intelligence_tile_has_visible_reset_corpus_form():
    """Source-shape pin for the visible admin reset affordance."""
    src = (ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")

    assert_in_source(src, 'action="/corpus_reset"', label='src')
    assert_in_source(src, "Reset corpus", label='src')
    assert_in_source(src, "Reset the local encrypted corpus cache", label='src')
