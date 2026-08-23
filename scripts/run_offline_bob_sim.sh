#!/usr/bin/env bash
# Round 168 / Round 169 / Round 169.1 / Round 169.2 Cloud+Bob offline simulation entrypoint.
# Never claims live Cisco accuracy. Never copies real CSOne into Git.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PY:-python3}"
PROFILE="${OFFLINE_SIM_PROFILE:-full}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/.adoptiq-acceptance/offline-bob-sim}"
SYNTHETIC_DIR="${SYNTHETIC_CSONE_DIR:-$ROOT/testdata/synthetic_csone}"
SKIP_PRODUCTION="${OFFLINE_SIM_SKIP_PRODUCTION_SIMULATION:-0}"
RESOLVE_ONLY="${OFFLINE_SIM_RESOLVE_ONLY:-0}"
SUMMARY="$OUTPUT_DIR/offline_bob_sim_summary.json"

usage() {
  cat <<'EOF'
Usage: scripts/run_offline_bob_sim.sh [--profile pr|full] [--skip-production-simulation] [--resolve-only]

Cloud/Bob offline loop. No work Mac, Keeper, live Cisco, or customer rows required.

  --profile pr     verify + lab + Round 169 metamorphic + fixture KPI
                   metamorphic + pipeline smoke + Jeff stubs + synthetic CSOne
  --profile full   pr plus production-simulation when a synthetic or external
                   corpus path exists (A-G matrix / Ask AI / degraded HTTP)
  --resolve-only   print corpus resolution JSON and exit (no gates)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --skip-production-simulation)
      SKIP_PRODUCTION=1
      shift
      ;;
    --resolve-only)
      RESOLVE_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$PROFILE" != "pr" && "$PROFILE" != "full" ]]; then
  echo "OFFLINE_SIM_PROFILE must be pr or full" >&2
  exit 2
fi

mkdir -p "$OUTPUT_DIR"

_abs_dir() {
  # Round 168: normalize trailing slashes so repo-boundary checks stay exact.
  local path="$1"
  if [[ -d "$path" ]]; then
    (cd "$path" && pwd)
  else
    echo "$path"
  fi
}

resolve_corpus() {
  local requested=""
  local requested_abs=""
  local synthetic_abs=""
  synthetic_abs="$(_abs_dir "$SYNTHETIC_DIR")"
  if [[ -n "${CSONE_CORPUS_DIR:-}" ]]; then
    requested="$CSONE_CORPUS_DIR"
    requested_abs="$(_abs_dir "$requested")"
    if [[ -d "$requested_abs" ]] && compgen -G "$requested_abs/*.xlsx" > /dev/null; then
      if [[ "$requested_abs" == "$ROOT" || "$requested_abs" == "$ROOT/"* ]]; then
        if [[ "$requested_abs" == "$synthetic_abs" || "$requested_abs" == "$ROOT/testdata/synthetic_csone" ]]; then
          echo "$requested_abs|synthetic_checked_in|operator CSONE_CORPUS_DIR points at the checked-in synthetic corpus"
          return 0
        fi
        echo "|blocked_in_repo_corpus|CSONE_CORPUS_DIR is inside the repo and is not testdata/synthetic_csone — refusing so real CSOne cannot be committed"
        return 0
      fi
      echo "$requested_abs|external_operator_dir|CSONE_CORPUS_DIR is an external workbook directory (not copied into Git)"
      return 0
    fi
    echo "|blocked_missing_external|CSONE_CORPUS_DIR is set but is not a directory of .xlsx files"
    return 0
  fi
  if [[ -d "$synthetic_abs" ]] && compgen -G "$synthetic_abs/*.xlsx" > /dev/null; then
    echo "$synthetic_abs|synthetic_checked_in|checked-in testdata/synthetic_csone"
    return 0
  fi
  echo "|skipped_no_corpus|no checked-in synthetic CSOne workbooks and CSONE_CORPUS_DIR unset"
}

IFS='|' read -r CORPUS_DIR CORPUS_KIND CORPUS_DETAIL < <(resolve_corpus)

if [[ "$RESOLVE_ONLY" == "1" ]]; then
  "$PY" - <<PY
import json
print(json.dumps({
    "schema_version": "offline-bob-sim-resolve/v1",
    "round": 169,
    "live_validation_performed": False,
    "production_accuracy_claimed": False,
    "release_ready": False,
    "profile": "$PROFILE",
    "corpus_kind": "$CORPUS_KIND",
    "corpus_detail": "$CORPUS_DETAIL",
    "corpus_dir_recorded": bool("$CORPUS_DIR"),
}, sort_keys=True))
PY
  exit 0
fi

echo "=== AdoptIQ offline Bob sim (Round 169.1) ==="
echo "profile=$PROFILE"
echo "python=$PY"
echo "output=$OUTPUT_DIR"
echo "corpus_kind=$CORPUS_KIND"
echo "live_validation_performed=false"
echo "production_accuracy_claimed=false"

run_gate() {
  local name="$1"
  shift
  echo
  echo "--- gate: $name ---"
  "$@"
}

VERIFY_OK=0
LAB_OK=0
HTTP_OK=0
META_OK=0
FIXTURE_META_OK=0
PIPELINE_OK=0
STUBS_OK=0
REPLAY_OK=0
PROD_OK=0
SURFACE_OK=0
CLASSIFY_OK=0
METRICS_OK=0
CONTRACTS_OK=0
HTTP_STATUS="skipped"
REPLAY_STATUS="skipped"
PROD_STATUS="skipped"

set +e
# Round 169.1: prove workflow + make targets exist before the long gates.
run_gate offline-sim-ci-surface "$PY" "$ROOT/scripts/check_offline_sim_ci_surface.py"
SURFACE_OK=$?
# Round 169.3: empty-runner hosted jobs never started; not a missing target; not billing.
run_gate hosted-actions-classify "$PY" "$ROOT/scripts/classify_hosted_actions_failure.py" \
  --input "$ROOT/testdata/hosted_actions/empty_runner_billing.json" \
  --require-kind hosted_runner_not_assigned \
  --require-reason job_never_started \
  --require-not-missing-target \
  --require-not-billing
CLASSIFY_OK=$?
run_gate verify make verify PY="$PY"
VERIFY_OK=$?
run_gate local-acceptance-lab make local-acceptance-lab PY="$PY"
LAB_OK=$?
# Round 169.1: use the official Round 169 SSoT metamorphic gate, not the
# complementary fixture-KPI script. Makefile metamorphic-acceptance stays
# pointed at run_round169_metamorphic_acceptance.py.
run_gate metamorphic-acceptance "$PY" "$ROOT/scripts/run_round169_metamorphic_acceptance.py" \
  --max-seconds "${MAX_SECONDS:-300}"
META_OK=$?
# Complementary Round 168 fixture KPI invariance (row-order / rebuild / NYU).
run_gate fixture-kpi-metamorphic "$PY" "$ROOT/scripts/run_metamorphic_acceptance.py" \
  --summary-path "$OUTPUT_DIR/fixture_kpi_metamorphic_summary.json"
FIXTURE_META_OK=$?
# Round 169: ingest → canonical reports/workbook → manager UX (no live Cisco).
run_gate offline-pipeline-smoke "$PY" "$ROOT/scripts/run_offline_pipeline_smoke.py" \
  --output-dir "$OUTPUT_DIR/pipeline-smoke"
PIPELINE_OK=$?
run_gate jeff-only-stubs "$PY" "$ROOT/scripts/run_jeff_only_stubs.py" \
  --output-dir "$OUTPUT_DIR/jeff-only-stubs"
STUBS_OK=$?
# Round 169.2: synthetic CSOne through load_csone_excel + canonical_metrics.
run_gate synthetic-csone-metrics "$PY" "$ROOT/scripts/run_synthetic_csone_metrics.py"
METRICS_OK=$?
# Round 169.5: fixture Snowflake DB-API source contracts (no Keeper / live Cisco).
run_gate source-contracts "$PY" "$ROOT/scripts/run_local_source_contracts.py" \
  --enable-local-fixtures
CONTRACTS_OK=$?

echo
echo "--- gate: local-acceptance-http ---"
if [[ "$PROFILE" == "full" ]]; then
  echo "SKIP: production-simulation already boots the guarded HTTP surface."
  echo "Standalone 23-scenario HTTP remains: make local-acceptance-http"
  HTTP_STATUS="covered_by_production_simulation"
else
  echo "SKIP: profile=pr. Pipeline smoke hits manager UX via Flask test client."
  echo "Full HTTP + A-G matrix: make offline-sim"
  HTTP_STATUS="skipped_profile_pr"
fi
HTTP_OK=0

# Round 169.5: blocked in-repo / missing-external corpus kinds FAIL.
# skipped_no_corpus stays an honest skip. Do not skip-pass a blocker.
CORPUS_ACTION="$("$PY" "$ROOT/scripts/offline_sim_scorecard.py" --corpus-action "$CORPUS_KIND")"
if [[ "$CORPUS_ACTION" == "fail" ]]; then
  echo
  echo "--- gate: csone-corpus-replay ---"
  echo "FAIL (fail-closed corpus blocker): $CORPUS_DETAIL"
  echo "blocked_in_repo_corpus and blocked_missing_external cannot skip-pass."
  REPLAY_STATUS="failed_${CORPUS_KIND}"
  REPLAY_OK=1
  echo
  echo "--- gate: production-simulation ---"
  echo "FAIL: corpus blocker also blocks production-simulation CSOne replay."
  PROD_STATUS="failed_${CORPUS_KIND}"
  PROD_OK=1
elif [[ "$CORPUS_ACTION" == "run" && -n "$CORPUS_DIR" ]]; then
  run_gate synthetic-or-external-csone-replay make csone-corpus-replay \
    PY="$PY" CSONE_CORPUS_DIR="$CORPUS_DIR"
  REPLAY_OK=$?
  REPLAY_STATUS="ran"
  if [[ "$SKIP_PRODUCTION" == "1" ]]; then
    echo
    echo "--- gate: production-simulation ---"
    echo "SKIP: OFFLINE_SIM_SKIP_PRODUCTION_SIMULATION=1"
    PROD_STATUS="skipped_by_flag"
    PROD_OK=0
  elif [[ "$PROFILE" == "pr" ]]; then
    echo
    echo "--- gate: production-simulation ---"
    echo "SKIP: profile=pr. Use --profile full to run the extensive local matrix."
    PROD_STATUS="skipped_profile_pr"
    PROD_OK=0
  else
    run_gate production-simulation make production-simulation \
      PY="$PY" \
      CSONE_CORPUS_DIR="$CORPUS_DIR" \
      OUTPUT_DIR="$OUTPUT_DIR/production-simulation"
    PROD_OK=$?
    PROD_STATUS="ran"
  fi
elif [[ "$CORPUS_ACTION" == "skip" ]]; then
  echo
  echo "--- gate: csone-corpus-replay ---"
  echo "SKIP (honest, no corpus): $CORPUS_DETAIL"
  echo "Need either testdata/synthetic_csone/*.xlsx or an EXTERNAL CSONE_CORPUS_DIR."
  echo "Do not commit real CSOne exports."
  REPLAY_STATUS="skipped_no_corpus"
  REPLAY_OK=0
  echo
  echo "--- gate: production-simulation ---"
  echo "SKIP (honest, no corpus): production-simulation with CSOne replay needs a corpus."
  echo "Fixture-only coverage already ran via verify + lab + pipeline smoke + metamorphic."
  echo "Jeff-only: live CSOne folder on the work Mac. See WORK_MAC_CURSOR_HANDOFF.md."
  PROD_STATUS="skipped_no_corpus"
  PROD_OK=0
else
  echo
  echo "--- gate: csone-corpus-replay ---"
  echo "FAIL: unknown corpus action '$CORPUS_ACTION'"
  REPLAY_STATUS="failed_unknown_corpus_action"
  REPLAY_OK=1
  PROD_STATUS="failed_unknown_corpus_action"
  PROD_OK=1
fi
set -e

OVERALL=0
if [[ $SURFACE_OK -ne 0 || $CLASSIFY_OK -ne 0 || $VERIFY_OK -ne 0 || $LAB_OK -ne 0 || $HTTP_OK -ne 0 || $META_OK -ne 0 || $FIXTURE_META_OK -ne 0 || $PIPELINE_OK -ne 0 || $STUBS_OK -ne 0 || $METRICS_OK -ne 0 || $CONTRACTS_OK -ne 0 || $REPLAY_OK -ne 0 || $PROD_OK -ne 0 ]]; then
  OVERALL=1
fi

"$PY" - <<PY
import json
from pathlib import Path
payload = {
    "schema_version": "offline-bob-sim/v6",
    "round": "169.5",
    "ready_for_live_cisco": False,
    "sanitized": True,
    "live_validation_performed": False,
    "production_accuracy_claimed": False,
    "release_ready": False,
    "manual_source_reconciliation_complete": False,
    "profile": "$PROFILE",
    "corpus_kind": "$CORPUS_KIND",
    "corpus_detail": "$CORPUS_DETAIL",
    "corpus_dir_recorded": bool("$CORPUS_DIR"),
    "handoff": "WORK_MAC_CURSOR_HANDOFF.md",
    "gates": {
        "offline_sim_ci_surface": {"exit_code": $SURFACE_OK, "status": "ran"},
        "hosted_actions_classify": {"exit_code": $CLASSIFY_OK, "status": "ran"},
        "verify": {"exit_code": $VERIFY_OK, "status": "ran"},
        "local_acceptance_lab": {"exit_code": $LAB_OK, "status": "ran"},
        "metamorphic_acceptance": {"exit_code": $META_OK, "status": "ran"},
        "fixture_kpi_metamorphic": {"exit_code": $FIXTURE_META_OK, "status": "ran"},
        "offline_pipeline_smoke": {"exit_code": $PIPELINE_OK, "status": "ran"},
        "jeff_only_stubs": {"exit_code": $STUBS_OK, "status": "ran"},
        "synthetic_csone_metrics": {"exit_code": $METRICS_OK, "status": "ran"},
        "source_contracts": {"exit_code": $CONTRACTS_OK, "status": "ran"},
        "local_acceptance_http": {"exit_code": $HTTP_OK, "status": "$HTTP_STATUS"},
        "csone_corpus_replay": {"exit_code": $REPLAY_OK, "status": "$REPLAY_STATUS"},
        "production_simulation": {"exit_code": $PROD_OK, "status": "$PROD_STATUS"},
    },
    "all_passed": $OVERALL == 0,
}
path = Path("$SUMMARY")
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
print(json.dumps(payload, sort_keys=True))
PY

# Round 169.5: attach PASS/FAIL/SKIPPED/UNKNOWN. Skips are not passes.
SCORE_RC=0
"$PY" "$ROOT/scripts/offline_sim_scorecard.py" --enrich-bob-summary "$SUMMARY" || SCORE_RC=$?
if [[ $SCORE_RC -ne 0 ]]; then
  OVERALL=1
fi

echo
if [[ $OVERALL -eq 0 ]]; then
  echo "offline Bob sim PASSED (still live_validation_performed=false; ready_for_live_cisco=false)."
else
  echo "offline Bob sim FAILED. See $SUMMARY" >&2
fi
exit "$OVERALL"
