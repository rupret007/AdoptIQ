#!/usr/bin/env bash
set -euo pipefail

# Build AdoptIQ on macOS into OUTBOX/ as .app (+optional CLI binary copy).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ADOPTIQ_VERSION="${ADOPTIQ_VERSION:-1.0.2}"
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
echo "Creating OUTBOX..."
mkdir -p OUTBOX
rm -rf "OUTBOX/AdoptIQ.app"
cp -R "$APP_PATH" "OUTBOX/AdoptIQ.app"
cp "README.md" "OUTBOX/README.md"
echo "AdoptIQ v${ADOPTIQ_VERSION} build ${ADOPTIQ_BUILD}" > OUTBOX/build_info.txt
echo "Built: $(date)" >> OUTBOX/build_info.txt

if [[ -x "dist/AdoptIQ/AdoptIQ" ]]; then
  cp "dist/AdoptIQ/AdoptIQ" "OUTBOX/AdoptIQ"
fi

echo
echo "Done."
echo "OUTBOX/AdoptIQ.app"
echo "OUTBOX/README.md"
echo
echo "Launch app and open http://localhost:5001"
