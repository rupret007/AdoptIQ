#!/usr/bin/env bash
set -euo pipefail

# Build AdoptIQ on macOS into OUTBOX/ as .app (+optional CLI binary copy).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ADOPTIQ_VERSION="${ADOPTIQ_VERSION:-1.0.3}"
ADOPTIQ_BUILD="${ADOPTIQ_BUILD:-1}"

echo "=============================================="
echo "  AdoptIQ - Build macOS app bundle"
echo "=============================================="
echo

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
  echo "Using venv: $PYTHON_BIN"
fi

echo "Installing dependencies..."
"$PYTHON_BIN" -m pip install -q -r requirements.txt
"$PYTHON_BIN" -m pip install -q pyinstaller

echo
echo "Embedding credentials..."
if [[ ! -f "secrets.env" && -f "secrets.env.template" ]]; then
  cp "secrets.env.template" "secrets.env"
  echo "  -> Created secrets.env from template. Fill values for full build."
fi

if ! "$PYTHON_BIN" embed_credentials.py; then
  echo ""
  echo "ERROR: embed_credentials.py failed. Cannot build without credentials."
  echo "       Ensure secrets.env exists and contains required values, then re-run."
  exit 1
fi
echo "  -> Configuration embedded from secrets.env"

echo
echo "Updating version/build metadata..."
ADOPTIQ_VERSION="$ADOPTIQ_VERSION" ADOPTIQ_BUILD="$ADOPTIQ_BUILD" "$PYTHON_BIN" update_version_pc.py

echo
echo "Running PyInstaller (macOS spec)..."
"$PYTHON_BIN" -m PyInstaller --clean --noconfirm adoptiq_mac.spec

APP_PATH="dist/AdoptIQ.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Build failed: $APP_PATH not found."
  exit 1
fi

echo
echo "Signing dist/AdoptIQ.app..."
# Strip stray extended attributes (e.g. com.apple.provenance, quarantine) that
# would otherwise invalidate the deep code signature.
xattr -cr "$APP_PATH"
# Apply an adhoc deep signature on the dist bundle so the .app that ends up
# inside the DMG is the signed copy. Without this, Apple Silicon Gatekeeper /
# AMFI silently kill the app on first launch (it bounces in the Dock and dies).
codesign --force --deep --sign - --timestamp=none "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

echo
echo "Staging DMG payload..."
DMG_STAGE="$(mktemp -d -t adoptiq_dmg_stage)"
trap 'rm -rf "$DMG_STAGE"' EXIT
# ditto preserves the deep code signature, resource forks, and ACLs so the
# .app remains valid once it lands inside the read-only DMG.
ditto "$APP_PATH" "$DMG_STAGE/AdoptIQ.app"
# Drag-to-install convention: Finder renders this symlink as a folder pointing
# at /Applications so users can drop AdoptIQ.app onto it.
ln -s /Applications "$DMG_STAGE/Applications"

echo
echo "Resetting OUTBOX..."
mkdir -p OUTBOX
# Remove prior loose artifacts and any stale DMGs from previous builds so the
# directory always reflects the latest build only.
rm -f  OUTBOX/AdoptIQ OUTBOX/build_info.txt OUTBOX/.DS_Store
rm -rf OUTBOX/AdoptIQ.app
rm -f  OUTBOX/AdoptIQ-v*.dmg

echo
echo "Building DMG..."
DMG_NAME="AdoptIQ-v${ADOPTIQ_VERSION}-build${ADOPTIQ_BUILD}.dmg"
DMG_PATH="OUTBOX/${DMG_NAME}"
# UDZO == read-only, zlib-compressed; standard macOS distribution format.
hdiutil create \
  -volname "AdoptIQ" \
  -srcfolder "$DMG_STAGE" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "$DMG_PATH"

echo
echo "Signing DMG..."
codesign --force --sign - --timestamp=none "$DMG_PATH"
codesign --verify --strict "$DMG_PATH"

cp "README.md" "OUTBOX/README.md"

# Finder may recreate .DS_Store while observing OUTBOX during the build;
# strip it as the final action so the directory ships clean.
rm -f OUTBOX/.DS_Store

echo
echo "Done."
echo "$DMG_PATH"
echo "OUTBOX/README.md"
echo
echo "Mount the DMG, drag AdoptIQ.app to Applications, then open and browse to http://localhost:5001"
