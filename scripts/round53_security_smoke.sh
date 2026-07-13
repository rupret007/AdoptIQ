#!/usr/bin/env bash
# Round 53 / Phase 53.6 -- legacy/developer security smoke test for
# the corpus offline-decryption hardening.
#
# Round 96: shipping builds no longer bundle corpus data.  This script
# still validates the cryptographic sentinel contract against a
# developer-created temporary corpus artifact, but it is NOT a proof
# that the current .app embeds a corpus snapshot.
#
# Validates the four end-to-end acceptance scenarios that the
# Round 53 hardening was built to satisfy:
#
#   1. POSITIVE / authorized: bake -> spec -> install -> open
#      succeeds when the OneDrive sentinel is present.
#   2. NEGATIVE / unauthorized: copying a temporary encrypted corpus
#      artifact to a machine without the OneDrive sentinel MUST fail
#      to decrypt (the old app-bundle exfiltration model is gone in
#      Round 96, but the sentinel gate still matters for local files).
#   3. ROTATION: rotating the OneDrive sentinel MUST brick a
#      pre-rotation install (no silent acceptance of the new key).
#   4. UI BLOCKED STATE: when the OneDrive sentinel is unavailable,
#      the corpus boot state must surface ``blocked_no_onedrive``
#      and the panel must render the actionable CTA.
#
# Each scenario runs in a tmp dir so re-runs are clean.  Exits 0
# on full pass; non-zero on the first failure.  Intended to be
# invoked from QA / CI (after the Mac build) and locally before
# every shipped DMG.
#
# Usage:
#   ./scripts/round53_security_smoke.sh
#
# Environment overrides:
#   PYTHON       -- python interpreter (default: python3)
#   KEEP_TMP     -- when set, do not delete the scratch dir
#                   (useful for forensic inspection after a failure)

set -euo pipefail

PYTHON="${PYTHON:-python3}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="$(mktemp -d -t round53_security_smoke_XXXX)"

if [[ -z "${KEEP_TMP:-}" ]]; then
    trap 'rm -rf "$SCRATCH"' EXIT
fi

echo "=============================================="
echo "  Round 53 corpus security smoke test"
echo "=============================================="
echo "Repo:    $ROOT_DIR"
echo "Scratch: $SCRATCH"
echo "Python:  $($PYTHON --version)"
echo

cd "$ROOT_DIR"

# --------------------------------------------------------------------------
# Common helpers
# --------------------------------------------------------------------------

# Drop a CSV fixture so the indexer has something to ingest.
seed_fixture() {
    local dst="$1"
    mkdir -p "$dst"
    cat > "$dst/tac_demo.csv" <<'CSV'
customer_name,technology,case_number,severity_norm,case_status_norm,Title
Acme Corp,Routing,12345,Sev3,Open,Routing flap on edge
Wile Coyote,Wireless,12346,Sev2,Open,AP onboarding stuck in DHCP
Roadrunner LLC,Security,12347,Sev1,Closed,Tunnel re-key failure
CSV
}

# Drop a non-empty doc so _check_onedrive_sync_status returns "synced".
seed_synced_onedrive() {
    local dst="$1"
    mkdir -p "$dst"
    printf 'sync_marker' > "$dst/synced_doc.docx"
}

# --------------------------------------------------------------------------
# Scenario 1: POSITIVE -- authorized open succeeds
# --------------------------------------------------------------------------

echo "Scenario 1: POSITIVE / authorized open"
S1="$SCRATCH/scenario_1_positive"
mkdir -p "$S1"
seed_fixture "$S1/fixture"
seed_synced_onedrive "$S1/onedrive"

"$PYTHON" scripts/mint_corpus_sentinel.py \
    --onedrive-root "$S1/onedrive" \
    > "$S1/mint.log" 2>&1
echo "  mint: ok ($(wc -c < "$S1/onedrive/adoptiq_corpus_sentinel.json") bytes)"

"$PYTHON" scripts/bake_corpus.py \
    --bake-dir "$S1/bake" \
    --source "$S1/fixture" \
    --onedrive-sentinel-root "$S1/onedrive" \
    > "$S1/bake.log" 2>&1
echo "  bake: ok"

# Verify the bake produced ONLY the 2 expected artifacts.
expected_files=("corpus.db.enc" "corpus.db.salt")
forbidden_files=("sentinel.json" "corpus.sentinel.lock.json")
for f in "${expected_files[@]}"; do
    if [[ ! -f "$S1/bake/$f" ]]; then
        echo "  FAIL: expected bake artifact $f missing"
        exit 1
    fi
done
for f in "${forbidden_files[@]}"; do
    if [[ -f "$S1/bake/$f" ]]; then
        echo "  FAIL: Round 53 contract violated -- $f present in bake dir"
        exit 1
    fi
done
echo "  bake artifacts: 2 expected, 0 forbidden  PASS"

# Decrypt round-trip with the OneDrive sentinel in place.
"$PYTHON" - <<PY
import sys
sys.path.insert(0, '$ROOT_DIR')
from corpus_crypto import open_corpus_for_user
handle = open_corpus_for_user(
    onedrive_root='$S1/onedrive',
    encrypted_path='$S1/bake/corpus.db.enc',
    create_if_missing=False,
    allow_local_sentinel=False,
)
try:
    cur = handle.conn.execute('SELECT count(*) FROM customers')
    n = int(cur.fetchone()[0])
    if n < 1:
        print(f'FAIL: corpus has {n} customers; expected >= 1')
        sys.exit(1)
    print(f'  positive open: {n} customer(s) decrypted  PASS')
finally:
    handle.close(persist=False)
PY

echo

# --------------------------------------------------------------------------
# Scenario 2: NEGATIVE -- copied runtime corpus on unauthorized machine
# --------------------------------------------------------------------------

echo "Scenario 2: NEGATIVE / unauthorized open"
S2="$SCRATCH/scenario_2_negative"
mkdir -p "$S2/exfil"

# Simulate "attacker copies encrypted runtime corpus files to their own
# machine" by copying ONLY the ciphertext + salt.  Round 96 release
# builds do not ship these files in the app bundle.
cp "$S1/bake/corpus.db.enc" "$S2/exfil/"
cp "$S1/bake/corpus.db.salt" "$S2/exfil/"

# Confirm no OneDrive sentinel is reachable from the attacker's path.
# Try to open with an empty / unconfigured OneDrive root.
"$PYTHON" - <<PY
import sys
sys.path.insert(0, '$ROOT_DIR')
from corpus_crypto import open_corpus_for_user, CorpusCryptoError
try:
    handle = open_corpus_for_user(
        onedrive_root=None,
        encrypted_path='$S2/exfil/corpus.db.enc',
        create_if_missing=False,
        allow_local_sentinel=False,
    )
    handle.close(persist=False)
    print('  FAIL: copied corpus opened WITHOUT OneDrive sentinel.')
    print('        This is the QUALITY_AUDIT.md Round 52.2 regression.')
    print('        Round 53 hardening is NOT in effect.')
    sys.exit(1)
except CorpusCryptoError as e:
    print(f'  negative open fails-closed as expected: {type(e).__name__}')
    print('  copied corpus is NOT offline-decryptable  PASS')
PY

echo

# --------------------------------------------------------------------------
# Scenario 3: ROTATION -- pre-rotation install bricks
# --------------------------------------------------------------------------

echo "Scenario 3: SENTINEL ROTATION"
S3="$SCRATCH/scenario_3_rotation"
mkdir -p "$S3"

# Use the Scenario 1 bake (sealed under the original sentinel).
# Now rotate the sentinel.  Round 54 / F4: --force requires a typed
# ROTATE confirmation by default; --yes bypasses for unattended
# pipelines (the smoke script qualifies -- this is non-interactive
# regression testing, not an operator-driven rotation).
"$PYTHON" scripts/mint_corpus_sentinel.py \
    --onedrive-root "$S1/onedrive" --force --yes \
    > "$S3/rotate.log" 2>&1

# Try to open the Scenario 1 bake under the NEW sentinel.  Expected
# to fail (lock pin or auth-tag mismatch) -- a "silent re-key" would
# be a HIGH severity regression.
"$PYTHON" - <<PY
import sys, shutil
sys.path.insert(0, '$ROOT_DIR')
# Fresh copy to avoid contaminating Scenario 1's bake dir.
shutil.copytree('$S1/bake', '$S3/copy_of_s1_bake')
from corpus_crypto import open_corpus_for_user, CorpusCryptoError
try:
    handle = open_corpus_for_user(
        onedrive_root='$S1/onedrive',  # rotated sentinel lives here now
        encrypted_path='$S3/copy_of_s1_bake/corpus.db.enc',
        create_if_missing=False,
        allow_local_sentinel=False,
    )
    handle.close(persist=False)
    print('  FAIL: pre-rotation corpus opened under rotated sentinel.')
    print('        This means the bake is silently re-keyable -- a HIGH')
    print('        severity regression of the Round 53 contract.')
    sys.exit(1)
except CorpusCryptoError as e:
    print(f'  rotation bricks pre-rotation install: {type(e).__name__}')
    print('  no silent re-key  PASS')
except Exception as e:
    # Any exception is acceptable here -- "fail-closed" is the contract.
    print(f'  rotation bricks pre-rotation install: {type(e).__name__}')
    print('  no silent re-key  PASS')
PY

echo

# --------------------------------------------------------------------------
# Scenario 4: UI BLOCKED STATE -- corpus_bootstrap surfaces blocked_no_onedrive
# --------------------------------------------------------------------------

echo "Scenario 4: UI BLOCKED STATE"
"$PYTHON" - <<PY
import os, sys, tempfile
sys.path.insert(0, '$ROOT_DIR')
import corpus_bootstrap
import config as live_config

# Point the bootstrap at a fresh tmp user dir + an empty OneDrive
# (no sentinel).  This is the "user runs a Round-53 build but has
# not signed in to OneDrive" path.
with tempfile.TemporaryDirectory(prefix='r53_smoke_user_') as tmp:
    user_dir = os.path.join(tmp, 'knowledge')
    os.makedirs(user_dir, exist_ok=True)
    corpus_bootstrap._user_corpus_dir = lambda: __import__('pathlib').Path(user_dir)
    # Wipe Config.CSONE_ONEDRIVE_FOLDER so the gate fires loud.
    live_config.Config.CSONE_ONEDRIVE_FOLDER = None
    corpus_bootstrap.Config.CSONE_ONEDRIVE_FOLDER = None
    corpus_bootstrap.reset_for_tests()

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    if state.source != 'blocked_no_onedrive':
        print(f'  FAIL: state.source={state.source!r}; expected blocked_no_onedrive')
        sys.exit(1)
    if state.last_error_kind != 'no_onedrive_sentinel':
        print(f'  FAIL: state.last_error_kind={state.last_error_kind!r}')
        sys.exit(1)
    if not (state.last_error and 'OneDrive' in state.last_error):
        print(f'  FAIL: remediation missing OneDrive ref: {state.last_error!r}')
        sys.exit(1)
    print('  blocked_no_onedrive surfaced with actionable remediation  PASS')
PY

echo
echo "=============================================="
echo "  All Round 53 security scenarios PASS"
echo "=============================================="
