#!/usr/bin/env bash
# Round 140: acceptance preflight — disk space + optional soak artifact prune.
# Usage: bash scripts/preflight_acceptance.sh [--prune-soak] [--prune-days N]
# Exit 0 when free space >= ADOPTIQ_MIN_FREE_GB (default 5) on the data volume.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MIN_GB="${ADOPTIQ_MIN_FREE_GB:-5}"
PRUNE_SOAK=0
PRUNE_DAYS=7

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prune-soak)
      PRUNE_SOAK=1
      shift
      ;;
    --prune-days)
      PRUNE_DAYS="${2:-7}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: bash scripts/preflight_acceptance.sh [--prune-soak] [--prune-days N]"
      echo "  ADOPTIQ_MIN_FREE_GB  minimum free GiB required (default: 5)"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

_data_volume() {
  if [[ -d /System/Volumes/Data ]]; then
    echo /System/Volumes/Data
  else
    echo /
  fi
}

VOL="$(_data_volume)"
AVAIL_KB="$(df -k "$VOL" | awk 'NR==2 {print $4}')"
AVAIL_GB="$(awk -v k="$AVAIL_KB" 'BEGIN { printf "%.2f", k / 1024 / 1024 }')"
MIN_KB=$((MIN_GB * 1024 * 1024))

echo "=============================================="
echo "  AdoptIQ acceptance preflight (Round 140)"
echo "=============================================="
echo "Data volume: $VOL"
echo "Free space:  ${AVAIL_GB} GiB (threshold: ${MIN_GB} GiB)"
echo

if [[ "$PRUNE_SOAK" -eq 1 ]]; then
  echo "Pruning old soak dirs under ~/Downloads/adoptiq_report_soak_* (>${PRUNE_DAYS}d)..."
  PRUNED=0
  while IFS= read -r -d '' dir; do
    rm -rf "$dir"
    echo "  removed: $dir"
    PRUNED=$((PRUNED + 1))
  done < <(find "$HOME/Downloads" -maxdepth 1 -type d -name 'adoptiq_report_soak_*' -mtime "+${PRUNE_DAYS}" -print0 2>/dev/null || true)
  echo "Pruned ${PRUNED} soak directory(ies)."
  AVAIL_KB="$(df -k "$VOL" | awk 'NR==2 {print $4}')"
  AVAIL_GB="$(awk -v k="$AVAIL_KB" 'BEGIN { printf "%.2f", k / 1024 / 1024 }')"
  echo "Free space after prune: ${AVAIL_GB} GiB"
  echo
fi

echo "Largest local candidates (safe to review):"
{
  [[ -d dist ]] && du -sh dist 2>/dev/null || true
  [[ -d build ]] && du -sh build 2>/dev/null || true
  [[ -d OUTBOX ]] && du -sh OUTBOX 2>/dev/null || true
  du -sh "$HOME/Downloads"/adoptiq_report_soak_* 2>/dev/null || true
  du -sh /tmp/tactrack-* 2>/dev/null || true
} | sort -hr | head -15 || true
echo

if [[ "$AVAIL_KB" -lt "$MIN_KB" ]]; then
  echo "FAIL: need at least ${MIN_GB} GiB free on ${VOL}; have ${AVAIL_GB} GiB."
  echo "Hints: prune soak dirs ( --prune-soak ), remove dist/build after DMG,"
  echo "        clear /tmp/tactrack-* venvs; never delete ~/Documents/AdoptIQ\\ Reports/"
  exit 1
fi

echo "PASS: acceptance preflight (${AVAIL_GB} GiB free >= ${MIN_GB} GiB)."
exit 0
