#!/usr/bin/env bash
# Smoke test for AdoptIQ Mac build: launch app, hit key endpoints, quit.
# Usage: ./scripts/test_build_smoke.sh [path/to/AdoptIQ.app]
#   Default: OUTBOX/AdoptIQ.app

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_PATH="${1:-$ROOT_DIR/OUTBOX/AdoptIQ.app}"
PORT=5001
WAIT_TIMEOUT=20

if [[ ! -d "$APP_PATH" ]]; then
  echo "ERROR: App not found: $APP_PATH"
  exit 1
fi

echo "=============================================="
echo "  AdoptIQ Mac build smoke test"
echo "=============================================="
echo "App: $APP_PATH"
echo "Port: $PORT"
echo

# Ensure no existing AdoptIQ on port
if lsof -i ":$PORT" -t >/dev/null 2>&1; then
  echo "Port $PORT is in use. Please quit any running AdoptIQ instance, then re-run."
  exit 1
fi

# Launch app
echo "Launching AdoptIQ.app..."
open "$APP_PATH"

# Wait for port to be ready
echo -n "Waiting for server (up to ${WAIT_TIMEOUT}s)..."
for i in $(seq 1 "$WAIT_TIMEOUT"); do
  if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/" 2>/dev/null | grep -q 200; then
    echo " ready (${i}s)"
    break
  fi
  sleep 1
  echo -n "."
  if [[ $i -eq $WAIT_TIMEOUT ]]; then
    echo " TIMEOUT"
    echo "FAIL: Server did not respond on port $PORT within ${WAIT_TIMEOUT}s"
    osascript -e 'quit app "AdoptIQ"' 2>/dev/null || true
    exit 1
  fi
done

PASS=0

# GET /
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/")
if [[ "$CODE" == "200" ]]; then
  echo "PASS: GET / -> $CODE"
else
  echo "FAIL: GET / -> $CODE (expected 200)"
  PASS=1
fi

# GET /api/status/all
RESP=$(curl -s "http://localhost:$PORT/api/status/all" 2>/dev/null || echo "")
if echo "$RESP" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  echo "PASS: GET /api/status/all -> valid JSON"
else
  echo "FAIL: GET /api/status/all -> invalid or empty JSON"
  PASS=1
fi

# Quit app
echo
echo "Quitting AdoptIQ..."
pkill -f "AdoptIQ.app" 2>/dev/null || osascript -e 'quit app "AdoptIQ"' 2>/dev/null || true
sleep 1

# Verify port is free
if lsof -i ":$PORT" -t >/dev/null 2>&1; then
  echo "WARN: Port $PORT still in use after quit"
else
  echo "PASS: Port $PORT free after quit"
fi

echo
if [[ $PASS -eq 0 ]]; then
  echo "Smoke test PASSED"
  exit 0
else
  echo "Smoke test FAILED"
  exit 1
fi
