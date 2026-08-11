#!/usr/bin/env bash
# AdoptIQ — one-command dev setup + launch (run from source).
#
# Purpose: move a branch onto any machine (your work computer or your Mac) and
# have it FUNCTION with a single command. It does not rebuild the packaged app
# and it changes no connection code — on the work machine it uses your existing
# Cisco config / Keeper / VPN exactly as the normal app does.
#
#   Usage:
#     ./run_dev.sh            # set up the venv, install deps, launch the app
#     ./run_dev.sh --setup    # set up only (venv + deps), do not launch
#     ./run_dev.sh --admin     # also launch the admin dashboard (port 5152)
#
# Safe to re-run: it reuses the venv and only reinstalls if requirements changed.
set -euo pipefail

cd "$(dirname "$0")"
VENV=".venv311"
STAMP="$VENV/.requirements.sha"

# --- pick a Python 3.11 (falls back to python3 with a warning) ---------------
PY=""
for cand in python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver="$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "")"
        if [ "$ver" = "3.11" ]; then PY="$cand"; break; fi
        [ -z "$PY" ] && PY="$cand"   # remember a fallback
    fi
done
[ -n "$PY" ] || { echo "ERROR: no python found on PATH"; exit 1; }
ver="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
if [ "$ver" != "3.11" ]; then
    echo "WARNING: using Python $ver, not 3.11. The suite floor and pip-audit"
    echo "         are validated on 3.11; a different minor may drift. Continuing."
fi
echo "[run_dev] Python: $("$PY" --version 2>&1) ($PY)"

# --- venv --------------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
    echo "[run_dev] creating venv at $VENV"
    "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"

# --- deps (only reinstall when requirements.txt changed) ---------------------
req_sha="$("$VPY" - <<'EOF'
import hashlib,sys
print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())
EOF
)"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$req_sha" ]; then
    echo "[run_dev] installing dependencies (this can take a few minutes the first time)"
    "$VPY" -m pip install -q --upgrade pip
    "$VPY" -m pip install -q -r requirements.txt
    echo "$req_sha" > "$STAMP"
else
    echo "[run_dev] dependencies already current"
fi

# --- setup-only exit ---------------------------------------------------------
if [ "${1:-}" = "--setup" ]; then
    echo "[run_dev] setup complete. Launch with: $VPY app_simple.py"
    exit 0
fi

# --- optional admin dashboard ------------------------------------------------
if [ "${1:-}" = "--admin" ]; then
    echo "[run_dev] starting admin dashboard on http://127.0.0.1:5152"
    "$VPY" enhanced_admin_dashboard_v2.py &
fi

# --- launch ------------------------------------------------------------------
echo "[run_dev] starting AdoptIQ on http://localhost:5151  (Ctrl-C to stop)"
echo "[run_dev] on the work machine this uses your live Cisco config + VPN;"
echo "[run_dev] with no VPN it starts but live source fetches will report unavailable."
exec "$VPY" app_simple.py
