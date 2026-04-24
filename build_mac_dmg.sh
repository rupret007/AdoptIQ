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
# Mirror release artifacts to OneDrive.
#
# Two destinations, two whitelists:
#
#   1. MAC_STAGING_DIR (default: AI Projects/Staging/AdoptIQ_MAC/OUTBOX)
#      - DMG + README + build_info.txt
#      - Cross-platform drop zone: build_pc.bat ALSO writes the PC payload
#        (AdoptIQ.exe + helpers) into this same folder.
#
#   2. MAC_OUTBOX_DIR (default: AI Projects/OUTBOX/AdoptIQ)
#      - DMG + README + AdoptIQ.app
#      - Mac-only drop zone for direct install / inspection of the .app.
#
# Both mirrors:
#   - preserve .DS_Store so Finder does not keep regenerating it
#   - prune OneDrive sync-conflict copies of README.md / build_info.txt
#     (e.g. README-MACHINENAME-XXXX.md), which the previous exact-match
#     whitelist would not catch
#   - skip with a warning (do not fail) if the destination's parent does
#     not exist, so the script still runs on Macs without OneDrive set up
#
# Override either destination by exporting MAC_STAGING_DIR or
# MAC_OUTBOX_DIR before running.
# ---------------------------------------------------------------------------
MAC_STAGING_DIR="${MAC_STAGING_DIR:-$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/OUTBOX}"
MAC_OUTBOX_DIR="${MAC_OUTBOX_DIR:-$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ}"
DMG_NAME="$(basename "$DMG_PATH")"

# cp -f wrapper with a clear error if the file is locked (e.g. DMG mounted
# in Finder, or .app currently running).
copy_or_die() {
  local src="$1"
  local dest="$2"
  local context="$3"
  if ! cp -f "$src" "$dest"; then
    echo "ERROR: Failed to copy $(basename "$src") to ${context}."
    echo "       If the DMG is currently mounted, run:"
    echo "         hdiutil detach /Volumes/AdoptIQ"
    echo "       If AdoptIQ.app is running, quit it first."
    echo "       Then re-run ./build_mac_dmg.sh."
    exit 1
  fi
}

# rm -rf with retry. OneDrive can hold files inside a synced .app bundle
# briefly after we touch them, which causes "Directory not empty" on the
# first attempt. Retries the delete a few times before giving up so we
# don't end up with a hollow .app shell in the staging mirrors.
rm_rf_retry() {
  local target="$1"
  local i
  for i in 1 2 3 4; do
    if rm -rf "$target" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  rm -rf "$target" 2>/dev/null || true
}

# ditto wrapper for the .app bundle, then strip OneDrive-added xattrs and
# re-apply an adhoc deep signature. OneDrive injects metadata xattrs
# (com.apple.metadata:*, com.apple.FinderInfo, com.microsoft.OneDrive.*)
# into files it syncs, which invalidate the original deep signature and
# cause "resource fork, Finder information, or similar detritus not
# allowed" from codesign --verify. Stripping + re-signing at the
# destination produces a usable .app for users who copy it to
# /Applications.
#
# Best-effort: codesign on a OneDrive path can still race with continuing
# OneDrive metadata writes. If --verify fails we warn but do NOT fail the
# build, since the canonical install path on macOS is the DMG (which
# carries its own signed bundle), and the .app at this destination is for
# inspection / drag-to-Applications.
ditto_or_die() {
  local src="$1"
  local dest="$2"
  local context="$3"
  rm_rf_retry "$dest"
  if ! ditto "$src" "$dest"; then
    echo "ERROR: Failed to ditto $(basename "$src") to ${context}."
    echo "       If AdoptIQ.app is running at $dest, quit it first."
    echo "       Then re-run ./build_mac_dmg.sh."
    exit 1
  fi
  xattr -cr "$dest" 2>/dev/null || true
  if ! codesign --force --deep --sign - --timestamp=none "$dest" >/dev/null 2>&1; then
    echo "WARNING: Failed to re-codesign $dest. The .app may not launch from"
    echo "         this OneDrive folder. Users should install via the DMG or"
    echo "         drag the .app to /Applications first."
  elif ! codesign --verify --deep --strict "$dest" >/dev/null 2>&1; then
    echo "WARNING: codesign --verify failed on $dest after re-signing."
    echo "         OneDrive may be re-injecting xattrs. Users should install"
    echo "         via the DMG or drag the .app to /Applications first."
  fi
}

# --- Destination 1: Staging mirror (DMG + README + build_info) -------------
if [[ ! -d "$MAC_STAGING_DIR" ]]; then
  echo
  echo "WARNING: Mac staging dir not found, skipping staging mirror:"
  echo "         $MAC_STAGING_DIR"
  echo "         Set MAC_STAGING_DIR=... or create the folder to enable sync."
else
  echo
  echo "Syncing lean release payload to Staging mirror:"
  echo "  $MAC_STAGING_DIR"

  while IFS= read -r -d '' staged_entry; do
    name="$(basename "$staged_entry")"
    case "$name" in
      "$DMG_NAME"|"README.md"|"build_info.txt"|".DS_Store")
        ;;
      README-*.md|build_info-*.txt)
        # OneDrive sync-conflict variants (e.g. README-JESTORY-M-02NP.md)
        rm_rf_retry "$staged_entry" ;;
      *)
        rm_rf_retry "$staged_entry" ;;
    esac
  done < <(find "$MAC_STAGING_DIR" -mindepth 1 -maxdepth 1 -print0)

  copy_or_die "$DMG_PATH" "$MAC_STAGING_DIR/$DMG_NAME" "Staging mirror"
  if [[ -f "OUTBOX/README.md" ]]; then
    copy_or_die "OUTBOX/README.md" "$MAC_STAGING_DIR/README.md" "Staging mirror"
  elif [[ -f "README.md" ]]; then
    copy_or_die "README.md" "$MAC_STAGING_DIR/README.md" "Staging mirror"
  fi
  if [[ -f "OUTBOX/build_info.txt" ]]; then
    copy_or_die "OUTBOX/build_info.txt" "$MAC_STAGING_DIR/build_info.txt" "Staging mirror"
  fi

  echo "Staging mirror now contains:"
  echo "  - $DMG_NAME"
  echo "  - README.md"
  echo "  - build_info.txt"
fi

# --- Destination 2: OUTBOX mirror (DMG + README + AdoptIQ.app) -------------
MAC_OUTBOX_PARENT="$(dirname "$MAC_OUTBOX_DIR")"
if [[ ! -d "$MAC_OUTBOX_PARENT" ]]; then
  echo
  echo "WARNING: Mac OUTBOX parent not found, skipping OUTBOX mirror:"
  echo "         $MAC_OUTBOX_PARENT"
  echo "         Set MAC_OUTBOX_DIR=... or create the parent to enable sync."
else
  mkdir -p "$MAC_OUTBOX_DIR"
  echo
  echo "Syncing release payload to OUTBOX mirror:"
  echo "  $MAC_OUTBOX_DIR"

  while IFS= read -r -d '' staged_entry; do
    name="$(basename "$staged_entry")"
    case "$name" in
      "$DMG_NAME"|"README.md"|"AdoptIQ.app"|".DS_Store")
        ;;
      README-*.md|AdoptIQ-*.app)
        # OneDrive sync-conflict variants
        rm_rf_retry "$staged_entry" ;;
      *)
        rm_rf_retry "$staged_entry" ;;
    esac
  done < <(find "$MAC_OUTBOX_DIR" -mindepth 1 -maxdepth 1 -print0)

  copy_or_die "$DMG_PATH" "$MAC_OUTBOX_DIR/$DMG_NAME" "OUTBOX mirror"
  if [[ -f "OUTBOX/README.md" ]]; then
    copy_or_die "OUTBOX/README.md" "$MAC_OUTBOX_DIR/README.md" "OUTBOX mirror"
  elif [[ -f "README.md" ]]; then
    copy_or_die "README.md" "$MAC_OUTBOX_DIR/README.md" "OUTBOX mirror"
  fi
  if [[ -d "OUTBOX/AdoptIQ.app" ]]; then
    ditto_or_die "OUTBOX/AdoptIQ.app" "$MAC_OUTBOX_DIR/AdoptIQ.app" "OUTBOX mirror"
  else
    echo "WARNING: OUTBOX/AdoptIQ.app missing, OUTBOX mirror will not contain the .app."
  fi

  echo "OUTBOX mirror now contains:"
  echo "  - $DMG_NAME"
  echo "  - README.md"
  echo "  - AdoptIQ.app"
fi
