#!/usr/bin/env bash
set -euo pipefail

# Build AdoptIQ.app, then create a DMG in OUTBOX.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Round 35 + Round 36 / native-corpus: bake the AdoptIQ Knowledge
# Corpus into an encrypted SQLite snapshot BEFORE PyInstaller runs so
# the spec file can pick up the four artifacts (corpus.db.enc,
# sentinel.json, corpus.db.salt, corpus.sentinel.lock.json) under
# ``bake/``.  The salt filename is ``corpus.db.salt`` (NOT
# ``salt.bin``) -- it is pinned by ``corpus_crypto._salt_path_for``
# which derives the salt path from the encrypted DB via
# ``with_suffix(".salt")``.
#
# Round 36: the MSAL/Graph device-code path was removed.  The bake
# now reads a local directory (the OneDrive desktop client's mirror
# of the canonical AdoptIQ corpus folder).  Source resolution order:
#   1. ADOPTIQ_BAKE_FIXTURE_DIR env var     (build operator override)
#   2. Config.CSONE_ONEDRIVE_FOLDER         (default: the operator's
#                                           OneDrive sync mirror)
# Both must point at a real local directory containing parseable
# files; the bake refuses to commit an empty corpus.
#
# Skip-mode controls (any one of these turns the bake into a no-op
# that emits a marker file):
#   * ADOPTIQ_BAKE_CORPUS=0  (env)
#   * pass --no-bake on the build command line via
#     ADOPTIQ_BAKE_EXTRA_ARGS
#
# When skipped, the spec file's ``baked_corpus`` data entries
# gracefully degrade because the bake artifacts are absent (the spec
# file uses a ``Path.exists()`` check; see ``adoptiq_mac.spec``).
# The runtime bootstrap then auto-mints a fresh local sentinel and
# the daily refresh worker re-indexes from
# Config.CSONE_ONEDRIVE_FOLDER once OneDrive sync catches up --
# legacy / pre-Round-35 behavior.
echo
echo "=============================================="
echo "  Round 35/36: Baking AdoptIQ Knowledge Corpus"
echo "=============================================="
echo
BAKE_FLAG="${ADOPTIQ_BAKE_CORPUS:-1}"
BAKE_EXTRA_ARGS="${ADOPTIQ_BAKE_EXTRA_ARGS:-}"
# Round 36: ADOPTIQ_BAKE_FIXTURE_DIR overrides the default
# Config.CSONE_ONEDRIVE_FOLDER source.  Quoted explicitly because the
# canonical OneDrive mirror path contains spaces
# ("OneDrive-Cisco/AI Projects/...").
BAKE_FIXTURE_DIR="${ADOPTIQ_BAKE_FIXTURE_DIR:-}"
# Round 36: ADOPTIQ_BAKE_AUTH_MODE is accepted for back-compat (the
# old build harness exported it) but no longer affects bake behavior;
# the bake_corpus.py script logs a deprecation warning when it sees
# the flag.  Operators do not need to set it.
BAKE_AUTH_MODE_LEGACY="${ADOPTIQ_BAKE_AUTH_MODE:-}"
BAKE_PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  BAKE_PYTHON_BIN=".venv/bin/python"
fi
if [[ "$BAKE_FLAG" == "0" || "$BAKE_FLAG" == "false" || "$BAKE_FLAG" == "no" ]]; then
  echo "ADOPTIQ_BAKE_CORPUS=$BAKE_FLAG -- skipping corpus bake"
  "$BAKE_PYTHON_BIN" scripts/bake_corpus.py --bake-dir bake --no-bake
else
  if [[ -n "$BAKE_FIXTURE_DIR" ]]; then
    echo "Bake source: $BAKE_FIXTURE_DIR (ADOPTIQ_BAKE_FIXTURE_DIR override)"
    if ! "$BAKE_PYTHON_BIN" scripts/bake_corpus.py \
          --bake-dir bake \
          --source "$BAKE_FIXTURE_DIR" \
          $BAKE_EXTRA_ARGS; then
      echo
      echo "ERROR: bake_corpus.py failed.  To skip the bake (the"
      echo "       runtime daily refresh will repopulate the corpus"
      echo "       from Config.CSONE_ONEDRIVE_FOLDER on first launch)"
      echo "       re-run with: ADOPTIQ_BAKE_CORPUS=0 ./build_mac_dmg.sh"
      exit 1
    fi
  else
    echo "Bake source: Config.CSONE_ONEDRIVE_FOLDER (default)"
    if ! "$BAKE_PYTHON_BIN" scripts/bake_corpus.py \
          --bake-dir bake \
          $BAKE_EXTRA_ARGS; then
      echo
      echo "ERROR: bake_corpus.py failed.  Pass ADOPTIQ_BAKE_FIXTURE_DIR"
      echo "       to point at a different local directory, or skip the"
      echo "       bake entirely with: ADOPTIQ_BAKE_CORPUS=0 ./build_mac_dmg.sh"
      exit 1
    fi
  fi
fi
echo

./build_mac.sh

# Round 28 / pipeline-drift workaround (matches the documented note
# in the round-28 plan):
#
# ``build_mac.sh`` builds the .app into ``dist/AdoptIQ.app``, signs
# it there, ships its own leaner DMG into ``OUTBOX/``, and then
# explicitly ``rm -rf OUTBOX/AdoptIQ.app`` to keep OUTBOX free of
# stale bundles.  This script then expects ``OUTBOX/AdoptIQ.app``
# to exist so it can stage the richer DMG (with Unblock helper +
# READ_ME_FIRST + README).  Without a fallback the chained build
# always failed at the precondition check below.
#
# The fallback re-stages the canonical signed bundle from
# ``dist/AdoptIQ.app`` into ``OUTBOX/AdoptIQ.app`` using ``ditto``
# (preserves Mach-O signatures and resource forks), strips OneDrive-
# style xattrs, and re-applies a clean adhoc deep signature.  Same
# recipe the OneDrive-mirror branch below uses for its own
# .app copies, so the staged bundle behaves identically.
APP_PATH="OUTBOX/AdoptIQ.app"
if [[ ! -d "$APP_PATH" ]]; then
  if [[ -d "dist/AdoptIQ.app" ]]; then
    echo
    echo "Re-staging dist/AdoptIQ.app -> OUTBOX/AdoptIQ.app"
    echo "(build_mac.sh removes OUTBOX/AdoptIQ.app; this is the documented"
    echo " workaround so build_mac_dmg.sh can build the richer DMG.)"
    ditto "dist/AdoptIQ.app" "$APP_PATH"
    xattr -cr "$APP_PATH" 2>/dev/null || true
    codesign --force --deep --sign - --timestamp=none "$APP_PATH"
    codesign --verify --deep --strict "$APP_PATH"
  else
    echo "Expected app bundle missing: $APP_PATH"
    echo "(also tried fallback dist/AdoptIQ.app; both are absent)"
    exit 1
  fi
fi

PYTHON_FOR_VER="${PYTHON_BIN:-python3}"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_FOR_VER=".venv/bin/python"
fi
VERSION="${ADOPTIQ_VERSION:-$("$PYTHON_FOR_VER" -c 'from config import ADOPTIQ_VERSION; print(ADOPTIQ_VERSION)')}"
BUILD="${ADOPTIQ_BUILD:-$("$PYTHON_FOR_VER" -c 'from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)')}"
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

# Round 52.1 / Build28: ``build_mac.sh`` signs its lean DMG, but this
# wrapper replaces it with the richer drag-to-Applications DMG above.
# Sign and verify the final artifact BEFORE any mirror copy so every
# staged DMG is the release-ready signed file.
echo "Signing final DMG: $DMG_PATH"
codesign --force --sign - --timestamp=none "$DMG_PATH"
codesign --verify --strict "$DMG_PATH"

# Round 52.1 / Build28: the staging mirror contract has long advertised
# build_info.txt, but the Mac path removed the file in build_mac.sh and
# never recreated it.  Keep this small metadata file outside the DMG and
# mirror it with the release payload for operators.
BUILD_INFO_PATH="OUTBOX/build_info.txt"
{
  echo "AdoptIQ v${VERSION} build ${BUILD}"
  echo "Built: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "Artifact: $(basename "$DMG_PATH")"
} > "$BUILD_INFO_PATH"
echo "Wrote build info: $BUILD_INFO_PATH"

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
  # Round 52.1 / Build28: OneDrive can inject xattrs shortly after the
  # copy finishes.  Give it a few settle/retry passes so the loose app
  # mirror is verifiable too, not just the canonical signed DMG.
  local signed_ok=0
  local i
  for i in 1 2 3; do
    xattr -cr "$dest" 2>/dev/null || true
    if codesign --force --deep --sign - --timestamp=none "$dest" >/dev/null 2>&1; then
      sleep "$i"
      xattr -cr "$dest" 2>/dev/null || true
      if codesign --verify --deep --strict "$dest" >/dev/null 2>&1; then
        signed_ok=1
        break
      fi
    fi
    sleep "$i"
  done
  if [[ "$signed_ok" != "1" ]]; then
    echo "WARNING: Failed to produce a verifiable signature on $dest."
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
