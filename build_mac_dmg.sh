#!/usr/bin/env bash
set -euo pipefail

# Build AdoptIQ.app, then create a DMG in OUTBOX.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

./build_mac.sh

APP_PATH="OUTBOX/AdoptIQ.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Expected app bundle missing: $APP_PATH"
  exit 1
fi

VERSION="${ADOPTIQ_VERSION:-1.0.3}"
BUILD="${ADOPTIQ_BUILD:-1}"
DMG_PATH="OUTBOX/AdoptIQ-v${VERSION}-build${BUILD}.dmg"

rm -f "$DMG_PATH"

# Stage a classic drag-to-Applications DMG layout.
# Contents:
# - AdoptIQ.app
# - Applications (symlink)
# - README.md
STAGING_DIR="$(mktemp -d -t adoptiq_dmg_stage.XXXXXXXX)"
cleanup() {
  rm -rf "$STAGING_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Use ditto (not cp -R) to preserve Mach-O code signatures, resource forks
# and extended attributes inside the bundle.
ditto "$APP_PATH" "$STAGING_DIR/AdoptIQ.app"
# Strip stray xattrs that would otherwise invalidate the deep adhoc signature
# (com.apple.provenance, quarantine, finder tags, etc.).
xattr -cr "$STAGING_DIR/AdoptIQ.app"
# Re-apply a clean adhoc deep signature on the bundle that ships in the DMG.
# Without this, Apple Silicon Gatekeeper / AMFI silently kill the app on first
# launch (the Dock icon bounces and dies with no error window).
codesign --force --deep --sign - --timestamp=none "$STAGING_DIR/AdoptIQ.app"
codesign --verify --deep --strict "$STAGING_DIR/AdoptIQ.app"

ln -s /Applications "$STAGING_DIR/Applications"

# Ship the user-facing helper that clears Gatekeeper quarantine after install,
# and a plain-text install guide they can preview from inside the DMG window.
UNBLOCK_SRC="scripts/mac/Unblock_AdoptIQ.command"
README_FIRST_SRC="scripts/mac/READ_ME_FIRST.txt"
if [[ -f "$UNBLOCK_SRC" ]]; then
  # Strip CR bytes during copy. CRLF line endings (e.g. from a Windows checkout
  # or an editor that auto-converts) make bash treat each line as ending in ^M
  # and the script fails with "set: -: invalid option" / "command not found"
  # before it can clear the quarantine xattr. Always normalise to LF in the DMG.
  tr -d '\r' < "$UNBLOCK_SRC" > "$STAGING_DIR/Unblock AdoptIQ.command"
  chmod +x "$STAGING_DIR/Unblock AdoptIQ.command"
  bash -n "$STAGING_DIR/Unblock AdoptIQ.command" \
    || { echo "ERROR: staged Unblock command has bash syntax errors"; exit 1; }
else
  echo "WARNING: $UNBLOCK_SRC missing; DMG will not include the unblock helper."
fi
if [[ -f "$README_FIRST_SRC" ]]; then
  tr -d '\r' < "$README_FIRST_SRC" > "$STAGING_DIR/READ_ME_FIRST.txt"
fi

if [[ -f "OUTBOX/README.md" ]]; then
  cp "OUTBOX/README.md" "$STAGING_DIR/README.md"
elif [[ -f "README.md" ]]; then
  cp "README.md" "$STAGING_DIR/README.md"
fi

hdiutil create -volname "AdoptIQ" -srcfolder "$STAGING_DIR" -ov -format UDZO "$DMG_PATH" >/dev/null

echo "Created DMG: $DMG_PATH"

# ---------------------------------------------------------------------------
# Mirror the lean release payload to the OneDrive staging OUTBOX.
#
# Mirrors the behavior of build_pc.bat which copies the PC payload into both
# AdoptIQ_PC and AdoptIQ_MAC/OUTBOX. After this step, AdoptIQ_MAC/OUTBOX is
# the single drop-zone holding the latest user-facing artifacts for both
# platforms (Mac DMG + PC EXE + helpers).
#
# Whitelist (anything else in MAC_STAGING_DIR that doesn't match is purged,
# .DS_Store preserved):
#   - AdoptIQ-v${VERSION}-build${BUILD}.dmg
#   - README.md
#   - build_info.txt
#
# Override the destination by exporting MAC_STAGING_DIR before running.
# ---------------------------------------------------------------------------
MAC_STAGING_DIR="${MAC_STAGING_DIR:-$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/OUTBOX}"
DMG_NAME="$(basename "$DMG_PATH")"

if [[ ! -d "$MAC_STAGING_DIR" ]]; then
  echo
  echo "WARNING: Mac staging dir not found, skipping staging mirror:"
  echo "         $MAC_STAGING_DIR"
  echo "         Set MAC_STAGING_DIR=... or create the folder to enable sync."
else
  echo
  echo "Syncing lean release payload to staging:"
  echo "  $MAC_STAGING_DIR"

  # Prune anything not in the whitelist. Preserve .DS_Store so Finder does
  # not keep regenerating it. Use -print0 / read -d '' to handle spaces in
  # filenames safely. Includes directories (e.g. AdoptIQ.app bundle) so a
  # loose .app from a previous in-folder build is removed when the DMG is
  # the canonical install path.
  while IFS= read -r -d '' staged_entry; do
    name="$(basename "$staged_entry")"
    case "$name" in
      "$DMG_NAME"|"README.md"|"build_info.txt"|".DS_Store")
        ;;
      *)
        rm -rf "$staged_entry" || true
        ;;
    esac
  done < <(find "$MAC_STAGING_DIR" -mindepth 1 -maxdepth 1 -print0)

  # Copy the three payload files. README.md and build_info.txt are taken
  # from OUTBOX/ so the staged copies match exactly what build_mac.sh just
  # produced for this build.
  copy_or_die() {
    local src="$1"
    local dest="$2"
    if ! cp -f "$src" "$dest"; then
      echo "ERROR: Failed to copy $(basename "$src") to staging."
      echo "       If the DMG is currently mounted, run:"
      echo "         hdiutil detach /Volumes/AdoptIQ"
      echo "       then re-run ./build_mac_dmg.sh."
      exit 1
    fi
  }

  copy_or_die "$DMG_PATH" "$MAC_STAGING_DIR/$DMG_NAME"
  if [[ -f "OUTBOX/README.md" ]]; then
    copy_or_die "OUTBOX/README.md" "$MAC_STAGING_DIR/README.md"
  elif [[ -f "README.md" ]]; then
    copy_or_die "README.md" "$MAC_STAGING_DIR/README.md"
  fi
  if [[ -f "OUTBOX/build_info.txt" ]]; then
    copy_or_die "OUTBOX/build_info.txt" "$MAC_STAGING_DIR/build_info.txt"
  fi

  echo "Staging payload now contains:"
  echo "  - $DMG_NAME"
  echo "  - README.md"
  echo "  - build_info.txt"
fi
