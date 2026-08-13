#!/usr/bin/env bash
set -euo pipefail

# Build AdoptIQ.app, then create a DMG in OUTBOX.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# A production build generates this ignored fallback module immediately before
# PyInstaller consumes it. Never leave the reversible credential bundle sitting
# in the source tree after the build succeeds or fails.
cleanup_generated_secrets() {
  rm -f "$ROOT_DIR/_bundled_secrets.py"
}
trap cleanup_generated_secrets EXIT

# Round 107 / Build 76: shipping builds bake and bundle the local
# Ask AI corpus again so first launch has corpus data immediately.
# Runtime refresh remains available for generated reports and operator
# uploads, but the initial corpus is a build artifact.
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
# Bake controls:
#   * ADOPTIQ_BAKE_CORPUS=1  (env, default shipping path)
#   * ADOPTIQ_BAKE_FIXTURE_DIR=<path>
#
# Skip-mode controls (developer-only; emits a marker file and removes
# stale local bake artifacts):
#   * ADOPTIQ_BAKE_CORPUS=0  (env)
#   * pass --no-bake on the build command line via
#     ADOPTIQ_BAKE_EXTRA_ARGS
echo
echo "=============================================="
echo "  Round 107: Prebaked AdoptIQ Knowledge Corpus"
echo "=============================================="
echo
DEVELOPER_ONLY_RAW="${ADOPTIQ_DEVELOPER_ONLY:-0}"
case "$DEVELOPER_ONLY_RAW" in
  1|true|TRUE|yes|YES|on|ON) ADOPTIQ_DEVELOPER_ONLY=1 ;;
  *) ADOPTIQ_DEVELOPER_ONLY=0 ;;
esac
export ADOPTIQ_DEVELOPER_ONLY
OUTBOX_DIR="${ADOPTIQ_OUTBOX_DIR:-OUTBOX}"
if [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" && -z "${ADOPTIQ_OUTBOX_DIR:-}" ]]; then
  OUTBOX_DIR="$ROOT_DIR/developer_candidates"
fi
export ADOPTIQ_OUTBOX_DIR="$OUTBOX_DIR"
BAKE_FLAG="${ADOPTIQ_BAKE_CORPUS:-1}"
if [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" ]]; then
  BAKE_FLAG=0
  export ADOPTIQ_BAKE_CORPUS=0
  echo "Developer-only candidate active: forcing a corpus-free build."
fi
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
# Round 71 / Phase 7 (#35): probe ``.venv/bin/python`` ONCE and unify
# under ``PYTHON_BIN`` so the bake step (which runs BEFORE
# ``./build_mac.sh``) and the post-build version-readback step both
# use the same interpreter.  Pre-R71 the bake step bound to a
# ``BAKE_PYTHON_BIN`` local while ``PYTHON_FOR_VER`` was a third name
# below -- three names for the same probe, with no shared definition
# and silently divergent fallbacks if anyone added env-var overrides
# later.  Mirrors the contract in ``build_mac.sh`` (line 17-21).
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi
BAKE_PYTHON_BIN="$PYTHON_BIN"
RELEASE_SOURCE_COMMIT=""
RELEASE_MODEL_MANIFEST="embeddings/release_fastembed_cache/adoptiq_model_manifest.json"

# Round 164 / Build 113: release packaging now runs a read-only, fail-closed
# readiness gate before the expensive corpus bake. This validates the clean
# source commit, owner-only ignored secrets.env, all-source integration key
# presence, approved corpus inputs, native host architecture, model/toolchain,
# disk space, and output path safety without printing or generating secrets.
if [[ "${ADOPTIQ_RELEASE_GATE:-0}" == "1" ]]; then
  if [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" ]]; then
    echo "ERROR: a developer-only candidate cannot pass ADOPTIQ_RELEASE_GATE=1."
    exit 1
  fi
  case "$BAKE_FLAG" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *)
      echo "ERROR: ADOPTIQ_RELEASE_GATE=1 requires ADOPTIQ_BAKE_CORPUS=1."
      exit 1 ;;
  esac
  if [[ " $BAKE_EXTRA_ARGS " == *" --no-bake "* ]]; then
    echo "ERROR: ADOPTIQ_BAKE_EXTRA_ARGS cannot include --no-bake for a release."
    exit 1
  fi
  if [[ -z "${ADOPTIQ_CSONE_CORPUS_DIR:-}" ]]; then
    echo "ERROR: ADOPTIQ_RELEASE_GATE=1 requires ADOPTIQ_CSONE_CORPUS_DIR."
    echo "       Point it at an approved external directory of current CSOne exports."
    exit 1
  fi
  echo
  echo "=============================================="
  echo "  Build 113: macOS release preflight"
  echo "=============================================="
  export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
  PREFLIGHT_ARGS=(
    --expected-version "${ADOPTIQ_VERSION:-}"
    --expected-build "${ADOPTIQ_BUILD:-}"
    --summary "${ADOPTIQ_MAC_PREFLIGHT_SUMMARY:-/tmp/adoptiq-mac-release-preflight.json}"
  )
  if [[ -n "${ADOPTIQ_EXPECTED_COMMIT:-}" ]]; then
    PREFLIGHT_ARGS+=(--expected-commit "$ADOPTIQ_EXPECTED_COMMIT")
  fi
  if [[ -n "${ADOPTIQ_EXPECTED_ARCH:-}" ]]; then
    PREFLIGHT_ARGS+=(--expected-arch "$ADOPTIQ_EXPECTED_ARCH")
  fi
  "$PYTHON_BIN" scripts/preflight_mac_release.py "${PREFLIGHT_ARGS[@]}"

  # Bind the release attempt before the production simulation and later bake.
  RELEASE_SOURCE_COMMIT="$(git rev-parse HEAD)"
  RELEASE_VERSION="$($PYTHON_BIN -c 'from config import ADOPTIQ_VERSION; print(ADOPTIQ_VERSION)')"
  RELEASE_BUILD="$($PYTHON_BIN -c 'from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)')"
fi

# Round 167: every direct macOS candidate, not only a release-gated package,
# must prove the production-like report/source/AI matrix before corpus baking,
# PyInstaller, signing, OUTBOX mutation, or manifest work.  CI invokes the same
# runner independently.  Real CSOne replay joins the gate when the operator
# supplies ADOPTIQ_CSONE_CORPUS_DIR; source rows never enter the repository.
echo
echo "=============================================="
echo "  Pre-build production simulation"
echo "=============================================="
PREBUILD_OUTPUT="${ADOPTIQ_PREBUILD_SIMULATION_OUTPUT:-/tmp/adoptiq-prebuild-production-simulation}"
PREBUILD_SOURCE_COMMIT="$(git rev-parse HEAD)"
PREBUILD_SOURCE_STATE="$(git status --porcelain=v1 --untracked-files=all)"
PREBUILD_ARGS=(
  --output-dir "$PREBUILD_OUTPUT"
  local
  --days "${ADOPTIQ_PREBUILD_SIMULATION_DAYS:-90}"
  --csone-replay-max-rows "${ADOPTIQ_CSONE_REPLAY_MAX_ROWS:-600}"
)
if [[ -n "${ADOPTIQ_CSONE_CORPUS_DIR:-}" ]]; then
  PREBUILD_ARGS+=(--csone-corpus-dir "$ADOPTIQ_CSONE_CORPUS_DIR")
fi
"$PYTHON_BIN" scripts/run_round146_acceptance.py "${PREBUILD_ARGS[@]}"
if [[ "$(git rev-parse HEAD)" != "$PREBUILD_SOURCE_COMMIT" ]] \
    || [[ "$(git status --porcelain=v1 --untracked-files=all)" != "$PREBUILD_SOURCE_STATE" ]]; then
  echo "ERROR: source changed during pre-build production simulation."
  exit 1
fi

if [[ "${ADOPTIQ_RELEASE_GATE:-0}" == "1" ]]; then
  # The gate is green, so the attempt may now remove same-name local evidence.
  # A failed bake must never leave an older DMG that can be mistaken for this
  # attempt's candidate.
  rm -f \
    "$OUTBOX_DIR/AdoptIQ-v${RELEASE_VERSION}-build${RELEASE_BUILD}.dmg" \
    "$OUTBOX_DIR/build_info.txt" \
    "$OUTBOX_DIR/latest.json"
fi

if [[ "$BAKE_FLAG" == "0" || "$BAKE_FLAG" == "false" || "$BAKE_FLAG" == "no" ]]; then
  echo "ADOPTIQ_BAKE_CORPUS=$BAKE_FLAG -- developer-only skip of prebaked corpus"
  "$BAKE_PYTHON_BIN" scripts/bake_corpus.py --bake-dir bake --no-bake
else
  echo "ADOPTIQ_BAKE_CORPUS=$BAKE_FLAG -- baking corpus for bundled first-launch use."
  if [[ -n "$BAKE_FIXTURE_DIR" ]]; then
    echo "Bake source: $BAKE_FIXTURE_DIR (ADOPTIQ_BAKE_FIXTURE_DIR override)"
    if ! "$BAKE_PYTHON_BIN" scripts/bake_corpus.py \
          --bake-dir bake \
          --source "$BAKE_FIXTURE_DIR" \
          $BAKE_EXTRA_ARGS; then
      echo
      echo "ERROR: bake_corpus.py failed.  Build 76 requires a prebaked"
      echo "       corpus for shipping. Fix the source path or rerun with"
      echo "       ADOPTIQ_BAKE_CORPUS=0 only for developer iteration."
      exit 1
    fi
  else
    # Round 83 / Build 59: surface the exact path the bake script
    # auto-selected so the operator can see at a glance whether the
    # canonical R80 leaf or the R83 owner-style fallback was picked.
    # Before R83 the bake step printed a generic "Config.CSONE_ONEDRIVE_FOLDER
    # (default)" line that obscured which candidate actually worked.
    echo "Bake source: auto-detect via _csone_onedrive_candidates() walk"
    AUTO_DETECT_PATH=$("$BAKE_PYTHON_BIN" -c "
from config import _csone_onedrive_candidates
import os
for c in _csone_onedrive_candidates():
    if os.path.isdir(c):
        print(c)
        break
" 2>/dev/null)
    if [[ -n "$AUTO_DETECT_PATH" ]]; then
      echo "  auto-detected: $AUTO_DETECT_PATH"
    else
      echo "  (no candidate exists on disk -- bake will likely fail;"
      echo "   set ADOPTIQ_BAKE_FIXTURE_DIR to override)"
    fi
    if ! "$BAKE_PYTHON_BIN" scripts/bake_corpus.py \
          --bake-dir bake \
          $BAKE_EXTRA_ARGS; then
      echo
      echo "ERROR: bake_corpus.py failed.  Round 83 / Build 59 walks"
      echo "       _csone_onedrive_candidates() automatically (canonical"
      echo "       R80 leaf, then R83 owner-style fallback).  If none of"
      echo "       those four candidates exist on disk, the bake fails."
      echo
      echo "       Remediation options:"
      echo "         1. Set ADOPTIQ_BAKE_FIXTURE_DIR to point at a local"
      echo "            directory containing the corpus + sentinel."
      echo "         2. Add the AdoptIQ corpus shortcut to OneDrive on"
      echo "            this build host (right-click the SharePoint"
      echo "            folder, choose 'Add shortcut to OneDrive')."
      echo "         3. Developer-only skip of the bake:"
      echo "            ADOPTIQ_BAKE_CORPUS=0 ./build_mac_dmg.sh"
      exit 1
    fi
  fi
fi
echo

if [[ "${ADOPTIQ_RELEASE_GATE:-0}" == "1" ]]; then
  MODEL_CACHE_DIR="${ADOPTIQ_FASTEMBED_CACHE:-${FASTEMBED_CACHE_PATH:-}}"
  if [[ -z "$MODEL_CACHE_DIR" ]]; then
    echo "ERROR: set ADOPTIQ_FASTEMBED_CACHE to the validated persistent model cache."
    exit 1
  fi
  "$PYTHON_BIN" scripts/stage_release_models.py --source "$MODEL_CACHE_DIR"
  if [[ ! -f "$RELEASE_MODEL_MANIFEST" ]]; then
    echo "ERROR: release model staging did not produce its integrity manifest."
    exit 1
  fi

  # A long corpus bake must not allow a concurrent checkout or edit to produce
  # an artifact assembled from mixed source revisions.
  if [[ "$(git rev-parse HEAD)" != "$RELEASE_SOURCE_COMMIT" ]]; then
    echo "ERROR: source commit changed during the release attempt; start again."
    exit 1
  fi
  if [[ -n "$(git status --porcelain=v1 --untracked-files=all)" ]]; then
    echo "ERROR: source tree changed during the release attempt; start again."
    exit 1
  fi
fi

# Round 107: opt-in release gate. Shipping builds now hard-fail when
# the prebaked corpus artifacts are missing because first-launch Ask AI
# readiness depends on the bundle carrying a corpus snapshot.
if [[ "${ADOPTIQ_RELEASE_GATE:-0}" == "1" ]]; then
  echo
  echo "=============================================="
  echo "  Round 107: ADOPTIQ_RELEASE_GATE=1 active"
  echo "=============================================="
  if [[ -f "bake/.bake-skipped" ]]; then
    echo
    echo "ERROR: ADOPTIQ_RELEASE_GATE=1 but bake/.bake-skipped is present."
    echo "       Shipping builds must bake and bundle the corpus."
    echo "       Re-run with ADOPTIQ_BAKE_CORPUS=1."
    exit 1
  fi
  if [[ ! -f "bake/corpus.db.enc" || ! -f "bake/corpus.db.salt" || ! -f "bake/sentinel.json" ]]; then
    echo
    echo "ERROR: ADOPTIQ_RELEASE_GATE=1 requires bake/corpus.db.enc,"
    echo "       bake/corpus.db.salt, and bake/sentinel.json."
    echo "       Aborting release."
    exit 1
  fi
  echo "  Prebaked corpus artifacts present, gate satisfied."
  echo
fi

./build_mac.sh

if [[ "${ADOPTIQ_RELEASE_GATE:-0}" == "1" ]]; then
  if [[ "$(git rev-parse HEAD)" != "$RELEASE_SOURCE_COMMIT" ]] \
      || [[ -n "$(git status --porcelain=v1 --untracked-files=all)" ]]; then
    echo "ERROR: source changed while PyInstaller was running; discard this candidate."
    exit 1
  fi
fi

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
APP_PATH="$OUTBOX_DIR/AdoptIQ.app"
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

# Round 71 / Phase 7 (#35): reuse the unified ``PYTHON_BIN`` probe
# from above so the version readback uses the same interpreter as the
# bake step (and the same probe ``build_mac.sh`` itself uses).  Pre-R71
# this block introduced a third ``PYTHON_FOR_VER`` local that
# duplicated the venv probe inline.
VERSION="${ADOPTIQ_VERSION:-$("$PYTHON_BIN" -c 'from config import ADOPTIQ_VERSION; print(ADOPTIQ_VERSION)')}"
BUILD="${ADOPTIQ_BUILD:-$("$PYTHON_BIN" -c 'from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)')}"
DMG_PATH="$OUTBOX_DIR/AdoptIQ-v${VERSION}-build${BUILD}.dmg"

rm -f "$DMG_PATH"

# Stage a classic drag-to-Applications DMG layout.
# Contents:
# - AdoptIQ.app
# - Applications (symlink)
# - README.md
STAGING_DIR="$(mktemp -d -t adoptiq_dmg_stage.XXXXXXXX)"
cleanup() {
  rm -rf "$STAGING_DIR" >/dev/null 2>&1 || true
  cleanup_generated_secrets
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
  echo "ERROR: $UNBLOCK_SRC missing; release DMG cannot ship without the unblock helper."
  exit 1
fi
if [[ -f "$README_FIRST_SRC" ]]; then
  tr -d '\r' < "$README_FIRST_SRC" > "$STAGING_DIR/READ_ME_FIRST.txt"
else
  echo "ERROR: $README_FIRST_SRC missing; release DMG cannot ship without READ_ME_FIRST.txt."
  exit 1
fi

if [[ -f "$OUTBOX_DIR/README.md" ]]; then
  cp "$OUTBOX_DIR/README.md" "$STAGING_DIR/README.md"
elif [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" ]]; then
  echo "ERROR: generated developer-candidate README is missing from $OUTBOX_DIR."
  exit 1
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
if [[ "$OUTBOX_DIR" != "OUTBOX" ]]; then
  BUILD_INFO_PATH="$OUTBOX_DIR/build_info.txt"
fi
DEPENDENCY_FINGERPRINT="$("$PYTHON_BIN" -m pip freeze | LC_ALL=C sort | shasum -a 256 | awk '{print $1}')"
{
  echo "AdoptIQ v${VERSION} build ${BUILD}"
  echo "Built: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "Source commit: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "Native architecture: $(uname -m)"
  echo "Dependency environment SHA-256: $DEPENDENCY_FINGERPRINT"
  echo "Direct constraints SHA-256: $(shasum -a 256 constraints-build113.txt | awk '{print $1}')"
  if [[ -f "$RELEASE_MODEL_MANIFEST" ]]; then
    echo "Release model manifest SHA-256: $(shasum -a 256 "$RELEASE_MODEL_MANIFEST" | awk '{print $1}')"
  fi
  echo "Artifact: $(basename "$DMG_PATH")"
  echo "Install notes: if macOS blocks the DMG, approve it in System Settings > Privacy & Security; then drag AdoptIQ.app to Applications and run Unblock AdoptIQ.command from the DMG."
} > "$BUILD_INFO_PATH"
echo "Wrote build info: $BUILD_INFO_PATH"

if [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" ]]; then
  DMG_SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
  DMG_SIZE_BYTES="$(stat -f%z "$DMG_PATH" 2>/dev/null || echo 0)"
  echo "Developer-only candidate complete (not production-ready)."
  echo "Artifact: $DMG_PATH"
  echo "SHA-256: $DMG_SHA256"
  echo "Bytes: $DMG_SIZE_BYTES"
  echo "Release manifests and external mirrors were intentionally skipped."
  exit 0
fi

# ---------------------------------------------------------------------------
# Round 119 / Build 88: emit the machine-readable auto-update manifest.
#
# The cross-platform auto-updater (auto_updater.py) reads
# ``AI Projects/OUTBOX/latest.json`` to decide whether a newer build is
# available.  ``artifact`` is stored RELATIVE to the OUTBOX root
# (``AdoptIQ/<dmg>``) so the consumer can join it onto whatever local
# OneDrive mount they have.  scripts/write_release_manifest.py is
# merge-aware so writing the mac slot here never clobbers the pc slot
# that build_pc.bat writes from the Windows build host.
# ---------------------------------------------------------------------------
DMG_SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
DMG_SIZE_BYTES="$(stat -f%z "$DMG_PATH" 2>/dev/null || echo 0)"
LATEST_JSON_LOCAL="$OUTBOX_DIR/latest.json"
echo "Computing release manifest (sha256=${DMG_SHA256})"
"$PYTHON_BIN" scripts/write_release_manifest.py \
  --manifest "$LATEST_JSON_LOCAL" \
  --platform mac \
  --version "$VERSION" \
  --build "$BUILD" \
  --artifact "AdoptIQ/$(basename "$DMG_PATH")" \
  --sha256 "$DMG_SHA256" \
  --size "$DMG_SIZE_BYTES"
echo "Wrote release manifest: $LATEST_JSON_LOCAL"

# Round 164 / Build 113: packaging and publication are separate decisions.
# The default stops here with a local, checksummed candidate so install/smoke,
# live all-source reconciliation, and visual review happen before any consumer
# can auto-update. Legacy OneDrive mirroring is an explicit operator action.
PUBLISH_RELEASE_RAW="${ADOPTIQ_PUBLISH_RELEASE:-0}"
case "$PUBLISH_RELEASE_RAW" in
  1|true|TRUE|yes|YES|on|ON) ADOPTIQ_PUBLISH_RELEASE=1 ;;
  *) ADOPTIQ_PUBLISH_RELEASE=0 ;;
esac
if [[ "$ADOPTIQ_PUBLISH_RELEASE" == "1" ]]; then
  echo
  echo "ERROR: Direct publish during packaging is retired for release candidates."
  echo "       Keep ADOPTIQ_PUBLISH_RELEASE=0, verify the staged candidate,"
  echo "       then run scripts/promote_mac_release.py as documented in"
  echo "       NEXT_MACHINE_PROMPT.md."
  exit 1
fi
echo
echo "Build ${BUILD} candidate staged locally; publication intentionally skipped."
echo "  DMG: $DMG_PATH"
echo "  Manifest: $LATEST_JSON_LOCAL"
echo "Run packaged smoke + live reconciliation, then use the explicit"
echo "promotion step in NEXT_MACHINE_PROMPT.md."
exit 0

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

assert_safe_release_mirror() {
  local candidate="$1"
  local expected_tail="$2"
  if [[ -L "$candidate" ]]; then
    echo "ERROR: Refusing symlinked release mirror: $candidate"
    exit 1
  fi
  case "$candidate" in
    /|"$HOME"|"$ROOT_DIR"|"$ROOT_DIR/"|/Applications|/Users)
      echo "ERROR: Refusing unsafe release mirror: $candidate"
      exit 1 ;;
  esac
  if [[ "$candidate" != *"$expected_tail" ]]; then
    echo "ERROR: Release mirror does not end with managed path $expected_tail"
    echo "       $candidate"
    exit 1
  fi
}

assert_safe_release_mirror "$MAC_STAGING_DIR" "/AI Projects/Staging/AdoptIQ_MAC/OUTBOX"
assert_safe_release_mirror "$MAC_OUTBOX_DIR" "/AI Projects/OUTBOX/AdoptIQ"

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
      "$DMG_NAME"|"README.md"|"build_info.txt"|".DS_Store"|"AdoptIQ.exe"|"Run_AdoptIQ.bat"|"Unblock_AdoptIQ.bat"|"READ_ME_FIRST.txt")
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

  # Round 119 / Build 88: write the auto-update manifest to the OUTBOX
  # *root* (one level above the AdoptIQ/ subfolder).  Run the merge-aware
  # writer directly against the root manifest so an existing pc slot
  # (written by build_pc.bat from the Windows host) is preserved, then
  # copy the merged result back to the local OUTBOX/latest.json so the
  # local record matches what consumers will read.
  LATEST_JSON_ROOT="$MAC_OUTBOX_PARENT/latest.json"
  if "$PYTHON_BIN" scripts/write_release_manifest.py \
        --manifest "$LATEST_JSON_ROOT" \
        --platform mac \
        --version "$VERSION" \
        --build "$BUILD" \
        --artifact "AdoptIQ/$DMG_NAME" \
        --sha256 "$DMG_SHA256" \
        --size "$DMG_SIZE_BYTES"; then
    echo "  - latest.json (merged) -> $LATEST_JSON_ROOT"
    cp -f "$LATEST_JSON_ROOT" "$LATEST_JSON_LOCAL" 2>/dev/null || true
  else
    echo "WARNING: failed to write $LATEST_JSON_ROOT; auto-update manifest not refreshed at the Releases root."
  fi

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
