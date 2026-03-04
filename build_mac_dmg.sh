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

cp -R "$APP_PATH" "$STAGING_DIR/AdoptIQ.app"
ln -s /Applications "$STAGING_DIR/Applications"
if [[ -f "OUTBOX/README.md" ]]; then
  cp "OUTBOX/README.md" "$STAGING_DIR/README.md"
elif [[ -f "README.md" ]]; then
  cp "README.md" "$STAGING_DIR/README.md"
fi

hdiutil create -volname "AdoptIQ" -srcfolder "$STAGING_DIR" -ov -format UDZO "$DMG_PATH" >/dev/null

echo "Created DMG: $DMG_PATH"
