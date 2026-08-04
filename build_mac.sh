#!/usr/bin/env bash
set -euo pipefail

# Build AdoptIQ on macOS into OUTBOX/ as .app (+optional CLI binary copy).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ADOPTIQ_VERSION="${ADOPTIQ_VERSION:-}"
ADOPTIQ_BUILD="${ADOPTIQ_BUILD:-}"
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

echo "=============================================="
echo "  AdoptIQ - Build macOS app bundle"
echo "=============================================="
echo

# Round 123 / Build 92: honor an inherited PYTHON_BIN (e.g. from
# build_mac_dmg.sh, which already does so at its line 57) before falling
# back to bare ``python3``.  Pre-R123 this line hard-coded ``python3`` and
# silently ignored the env var, so a release build invoked as
# ``PYTHON_BIN=/usr/local/bin/python3 bash build_mac_dmg.sh`` baked the
# corpus with 3.11 but installed deps + ran PyInstaller under whatever
# bare ``python3`` resolved to (CommandLineTools 3.9), which cannot
# satisfy ``truststore>=0.9.0`` and aborted the build.  Explicit env wins,
# then ``.venv``, then default -- matching the wrapper's contract.
if [[ -n "${PYTHON_BIN:-}" ]]; then
  echo "Using inherited PYTHON_BIN: $PYTHON_BIN"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
  echo "Using venv: $PYTHON_BIN"
else
  PYTHON_BIN="python3"
fi

echo "Installing dependencies..."
"$PYTHON_BIN" -m pip install -q -r requirements.txt
"$PYTHON_BIN" -m pip install -q pyinstaller

echo
if [[ "$ADOPTIQ_DEVELOPER_ONLY" == "1" ]]; then
  echo "Developer-only candidate: bundled credentials are disabled."
  echo "  -> The build spec excludes _bundled_secrets even if a local build artifact exists."
else
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
fi

echo
echo "Updating version/build metadata..."
ADOPTIQ_VERSION="$ADOPTIQ_VERSION" ADOPTIQ_BUILD="$ADOPTIQ_BUILD" "$PYTHON_BIN" update_version_pc.py
ADOPTIQ_VERSION="$("$PYTHON_BIN" -c 'from config import ADOPTIQ_VERSION; print(ADOPTIQ_VERSION)')"
ADOPTIQ_BUILD="$("$PYTHON_BIN" -c 'from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)')"
# Round 89 / F3: export the resolved values so the PyInstaller subprocess
# (and the ``adoptiq_mac.spec`` Info.plist block inside it) inherits them.
# Without ``export``, the shell variables stay local to this script and
# ``os.environ.get("ADOPTIQ_VERSION")`` inside the spec returns None, falling
# through to the spec's hard-coded ``"1.0.3"`` / ``"1"`` fallbacks (the
# Build 65 acceptance bug).  The R67/Phase 4 fix landed the read-back from
# config.py here but missed the export.
export ADOPTIQ_VERSION
export ADOPTIQ_BUILD
echo "  -> Resolved version v${ADOPTIQ_VERSION} build ${ADOPTIQ_BUILD}"

echo
echo "Running PyInstaller (macOS spec)..."
"$PYTHON_BIN" -m PyInstaller --clean --noconfirm adoptiq_mac.spec

APP_PATH="dist/AdoptIQ.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Build failed: $APP_PATH not found."
  exit 1
fi

echo
echo "Installing TACTrack-style startup splash launcher..."
# Round 97: mirror TACTrack's macOS launch UX.  The shell launcher opens a
# tiny local splash immediately, then starts the PyInstaller binary.  The Python
# process sees ADOPTIQ_LAUNCHER_SPLASH_SHOWN=1 and suppresses its delayed
# webbrowser.open call so users do not get duplicate tabs.
MACOS_DIR="$APP_PATH/Contents/MacOS"
APP_EXECUTABLE="$MACOS_DIR/AdoptIQ"
APP_BINARY="$MACOS_DIR/AdoptIQ.bin"
if [[ ! -x "$APP_EXECUTABLE" ]]; then
  echo "Build failed: expected executable $APP_EXECUTABLE not found."
  exit 1
fi
mv "$APP_EXECUTABLE" "$APP_BINARY"
cat > "$APP_EXECUTABLE" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${ADOPTIQ_PORT:-5151}"
case "$PORT" in
  ''|*[!0-9]*) PORT="5151" ;;
esac
if (( PORT < 1 || PORT > 65535 )); then
  PORT="5151"
fi

TMP_PARENT="${TMPDIR:-/tmp}"
TMP_PARENT="${TMP_PARENT%/}"
SPLASH_BASE="$(mktemp "$TMP_PARENT/adoptiq-starting.XXXXXX")"
SPLASH_FILE="${SPLASH_BASE}.html"
mv "$SPLASH_BASE" "$SPLASH_FILE"

cat > "$SPLASH_FILE" <<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AdoptIQ is starting</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background: #06172b;
      color: #ffffff;
    }
    main {
      max-width: 34rem;
      padding: 2rem;
      text-align: center;
    }
    .spinner {
      width: 2.75rem;
      height: 2.75rem;
      margin: 0 auto 1.25rem;
      border: 0.3rem solid rgba(255,255,255,0.3);
      border-top-color: #35c7ff;
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    p {
      color: #d7dde8;
      line-height: 1.45;
    }
    a {
      color: #35c7ff;
    }
  </style>
</head>
<body>
  <main>
    <div class="spinner" aria-hidden="true"></div>
    <h1>AdoptIQ is starting...</h1>
    <p>Your browser will open AdoptIQ automatically when it is ready.</p>
    <p>If this page does not redirect, open <a href="http://localhost:${PORT}/">http://localhost:${PORT}/</a>.</p>
  </main>
  <script>
    const appUrl = "http://localhost:${PORT}/";
    const pingUrl = appUrl + "ping";
    async function waitForAdoptIQ() {
      try {
        const response = await fetch(pingUrl, { cache: "no-store" });
        if (!response.ok) { throw new Error("status " + response.status); }
        const body = await response.text();
        if (body.trim() !== "OK") { throw new Error("unexpected ping body"); }
        window.location.replace(appUrl);
      } catch (error) {
        window.setTimeout(waitForAdoptIQ, 1000);
      }
    }
    waitForAdoptIQ();
  </script>
</body>
</html>
HTML

/usr/bin/open "$SPLASH_FILE" >/dev/null 2>&1 || true
export ADOPTIQ_LAUNCHER_SPLASH_SHOWN=1
# Round 99: do not exec the long-running browser-only Flask process from the
# Finder-launched wrapper.  Exiting the wrapper lets Launch Services finish the
# app launch promptly (so the Dock icon stops bouncing) while the real server
# continues independently for the splash page and browser UI.
nohup "$APP_DIR/AdoptIQ.bin" "$@" >/dev/null 2>&1 &
disown "$!" 2>/dev/null || true
exit 0
LAUNCHER
chmod +x "$APP_EXECUTABLE"

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
echo "Resetting candidate output: $OUTBOX_DIR"
mkdir -p "$OUTBOX_DIR"
# Remove prior loose artifacts and any stale DMGs from previous builds so the
# directory always reflects the latest build only.
rm -f  "$OUTBOX_DIR/AdoptIQ" "$OUTBOX_DIR/build_info.txt" "$OUTBOX_DIR/.DS_Store"
rm -rf "$OUTBOX_DIR/AdoptIQ.app"
find "$OUTBOX_DIR" -maxdepth 1 -type f -name 'AdoptIQ-v*.dmg' -delete

echo
echo "Building DMG..."
DMG_NAME="AdoptIQ-v${ADOPTIQ_VERSION}-build${ADOPTIQ_BUILD}.dmg"
DMG_PATH="$OUTBOX_DIR/${DMG_NAME}"
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

cp "README.md" "$OUTBOX_DIR/README.md"

# Finder may recreate .DS_Store while observing OUTBOX during the build;
# strip it as the final action so the directory ships clean.
rm -f "$OUTBOX_DIR/.DS_Store"

echo
echo "Done."
echo "$DMG_PATH"
echo "$OUTBOX_DIR/README.md"
echo
echo "Mount the DMG, drag AdoptIQ.app to Applications, then open and browse to http://localhost:5151"
