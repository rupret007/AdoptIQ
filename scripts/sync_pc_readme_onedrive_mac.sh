#!/usr/bin/env bash
# Round 127 — sync repo README.md to Mac-synced OneDrive PC drop folders.
# Does NOT build AdoptIQ.exe; run build_pc.bat on Windows for the versioned EXE
# and latest.json pc slot merge.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
README="$ROOT/README.md"
for dest in \
  "$ROOT/OUTBOX/README.md" \
  "$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ_PC/README.md" \
  "$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_PC/README.md"
do
  if [[ -d "$(dirname "$dest")" ]]; then
    cp "$README" "$dest"
    echo "Synced README -> $dest"
  else
    echo "SKIP (missing dir): $dest"
  fi
done
echo ""
echo "Next: on a Windows build host, run build_pc.bat to publish"
echo "  AdoptIQ-v1.0.4-build<NN>.exe + merge latest.json pc slot into OneDrive OUTBOX."
