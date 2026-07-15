#!/usr/bin/env bash
# Smoke test for AdoptIQ Mac build: launch app, hit key endpoints, quit.
# Usage: ./scripts/test_build_smoke.sh [path/to/AdoptIQ.app]
#   Default: dist/AdoptIQ.app (the app bundle produced by build_mac.sh)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEFAULT_APP_PATH="$ROOT_DIR/dist/AdoptIQ.app"
if [[ ! -d "$DEFAULT_APP_PATH" && -d "$ROOT_DIR/OUTBOX/AdoptIQ.app" ]]; then
  DEFAULT_APP_PATH="$ROOT_DIR/OUTBOX/AdoptIQ.app"
fi
APP_PATH="${1:-$DEFAULT_APP_PATH}"
PORT=5151
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

# Wait for startup readiness. Round 97.2: poll the TACTrack-style
# /ping endpoint before exercising heavier routes so splash/readiness
# regressions fail fast.
echo -n "Waiting for /ping readiness (up to ${WAIT_TIMEOUT}s)..."
for i in $(seq 1 "$WAIT_TIMEOUT"); do
  if [[ "$(curl -s "http://localhost:$PORT/ping" 2>/dev/null || true)" == "OK" ]]; then
    echo " ready (${i}s)"
    break
  fi
  sleep 1
  echo -n "."
  if [[ $i -eq $WAIT_TIMEOUT ]]; then
    echo " TIMEOUT"
    echo "FAIL: Server did not respond on port $PORT within ${WAIT_TIMEOUT}s"
    echo "HINT: disk full or CPU-bound cold start — run 'make preflight-acceptance',"
    echo "      quit other heavy jobs, then retry smoke with an idle machine."
    osascript -e 'quit app "AdoptIQ"' 2>/dev/null || true
    exit 1
  fi
done

PASS=0

# GET /ping
PING_RESP=$(curl -s "http://localhost:$PORT/ping" 2>/dev/null || echo "")
if [[ "$PING_RESP" == "OK" ]]; then
  echo "PASS: GET /ping -> OK"
else
  echo "FAIL: GET /ping -> '$PING_RESP' (expected OK)"
  PASS=1
fi

# GET /
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/")
if [[ "$CODE" == "200" ]]; then
  echo "PASS: GET / -> $CODE"
else
  echo "FAIL: GET / -> $CODE (expected 200)"
  PASS=1
fi

# GET /api/version
RESP=$(curl -s "http://localhost:$PORT/api/version" 2>/dev/null || echo "")
if echo "$RESP" | python3 -c "import sys,json; p=json.load(sys.stdin); required={'ok','version','build','process_started_at_utc','restart_required'}; raise SystemExit(0 if required.issubset(p) else 1)" 2>/dev/null; then
  echo "PASS: GET /api/version -> valid build JSON"
else
  echo "FAIL: GET /api/version -> missing required build fields"
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

# GET /api/corpus/status
RESP=$(curl -s "http://localhost:$PORT/api/corpus/status" 2>/dev/null || echo "")
if echo "$RESP" | python3 -c "import sys,json; p=json.load(sys.stdin); raise SystemExit(0 if isinstance(p, dict) and 'boot' in p else 1)" 2>/dev/null; then
  echo "PASS: GET /api/corpus/status -> valid corpus JSON"
else
  echo "FAIL: GET /api/corpus/status -> missing boot status"
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
