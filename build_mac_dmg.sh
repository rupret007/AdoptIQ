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
