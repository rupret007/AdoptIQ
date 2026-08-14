# AdoptIQ — authoritative next-machine runbook (Round 168 / pending Build 116)

This is the only active release runbook. Historical commands in Git history,
`CURSOR_MAC_BUILD_INSTRUCTIONS.md`, and earlier quality entries are evidence only.
Use `WORK_MACHINE_BUILD116_PROMPT.md` as the copy-ready prompt.

## Current truth

- Version/source build: v1.0.4 Build 116.
- External source: `https://github.com/rupret007/AdoptIQ`, branch `main`.
- Preserve the work Mac's existing internal remote and Cisco integrations.
- Build 115 is invalidated. Its immutable identity remains under
  `release_candidates/macos-build115/`; never install, accept, promote, overwrite, or
  recreate it.
- Build 116 is source-only. No candidate path/hash/size/time, frozen smoke, live
  acceptance, manual review, release readiness, or production-accuracy claim exists.
- Every paired Source Data workbook has exactly 17 sheets in canonical order:
  `Report_Info`, `Metric_Lineage`, `Chart_Data`, `Evidence_Links`, `Action_Plans`,
  `Adoption_Barriers`, `Customer_Pulse`, `TAC_Cases`, `BEMS`, `Subscriptions`,
  `Success_Priorities`, `External_Incidents`, `External_Bugs`,
  `Defect_Correlations`, `Risk_Components`, `Member_Summary`, `Account_Summary`.

## Stop 1 — preserve source before mutation

```bash
set -euo pipefail
cd /path/to/AdoptIQ
git status --short --branch
git fetch rupret007 main
git checkout main
git pull --ff-only rupret007 main
git status --short --branch
git rev-parse HEAD
```

Require a clean expected Round 168 source tree. Inspect, but do not alter or expose,
the internal remote. Never stash, reset, clean, force-push, merge unrelated history,
print credentials/customer rows, or copy ignored reports/runtime state into Git.

Use Python 3.12 and tracked `constraints-build113.txt`; that name identifies validated
dependency lineage, not the current app build. Do not invent a new constraints file.

## Gate 1 — source verification

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints-build113.txt
make verify PY=.venv/bin/python
```

Require Ruff clean, Bandit zero HIGH/MEDIUM, dependency audit clean, full pytest green
except classified environment-only skips, and deterministic Ask AI evaluation green.
Record exact counts. Do not change behavior or weaken an assertion just to erase red.

## Gate 2 — extensive production-like simulation

Point to an approved external CSOne export folder. Never copy it into Git.

```bash
make production-simulation \
  PY=.venv/bin/python \
  CSONE_CORPUS_DIR='/approved/external/AdoptIQ_CSOne_Reports' \
  OUTPUT_DIR='.adoptiq-acceptance/build116-prebuild'
```

Require exact expected/completed inventories; all configured A–G report selections;
all technologies; two named managers and All Managers; Leader Team/Member/Customer;
portfolio/customer Compact, Comprehensive, and Renewal; Subscription; degraded source
states; manager isolation; manager workspace/history; Ask AI sync/SSE; corpus replay;
two deterministic passes; and one zero-exit R114 audit for every DOCX/XLSX pair.

Fail on any timeout, skip, malformed/duplicate/extra result, missing chart/link, literal
`undefined`/`null`, unexplained Unknown, false zero, cross-scope record, source-ID or
attribution mismatch, freshness/state drift, missing paired artifact, or incomplete
negative control. Require a meaningful cross-family source comparison. The summary
must still state `live_validation_performed=false`,
`production_accuracy_claimed=false`, and `release_ready=false`.

## Gate 3 — authorized metadata-only Snowflake opportunity inventory

Run only with VPN and existing authorized configuration:

```bash
.venv/bin/python scripts/profile_snowflake_capabilities.py \
  --live-metadata \
  --confirm-authorized-live-metadata \
  --summary /approved/external/evidence/build116-snowflake-capabilities.json
```

This may inspect allow-listed schema metadata, never customer rows. Preserve
`row_values_queried=false`. Connection/permission failure is unavailable/blocked, not
zero, and errors must be sanitized. Never widen table policy merely to make a field
appear. For any proposed source field, document owner/meaning, stable join key and
cardinality, scope authorization, null/duplicate/late-arrival behavior, source event
and load clocks, decision value, canonical target, and regression/live tests.

## Gate 4 — authorized live source reconciliation before packaging

Start the normal source app with the existing work-machine configuration; do not use
guarded fixtures as live proof. Preflight `/ping`, `/api/version`, connectivity,
corpus/intelligence, DSM columns, and source warnings. Run two independent passes for
all report families, two named managers plus All Managers, Leader Team/Member/Customer,
portfolio/customer scopes, one authorized subscription, and all A–G selections.

Reconcile Word facts and four chart series to `Chart_Data`; claims to
`Metric_Lineage`; citations and links to `Evidence_Links` plus the exact source row.
Open at least one authorized Action Plan, Barrier, Pulse, and Success Priority link;
confirm object, record, account/claim, and scope. Check Action Plan lifecycle/dates,
TAC/BEMS distinct IDs, renewal outlook/exposure versus AdoptIQ risk, shared attribution,
freshness/partial/stale disclosures, lower bounds, pagination, clipping, charts, and
unexplained unknowns. Exercise Ask AI sync/SSE, report-bound frozen facts, scopes,
multi-turn context, evidence lookup, stale/partial trust, unanswerable questions,
timeouts/rate limits/provider failures, and public sanitization. Invalid member,
customer, subscription, and mixed-scope requests must fail before source access.

Any defect returns to source: add a focused regression, rerun Gates 1–4, and obtain
fresh source approval. No local fixture result substitutes for this gate.

## Stop 2 — source commit and package authorization

Present the exact source diff, all gate counts, offline/live classification, sensitive
evidence location, visual findings, and remaining risk. Obtain explicit authorization
before stage/commit/push and separately before packaging. Require clean upstream
`main`; never force-push. Record the exact approved source commit SHA.

## Gate 5 — package pending Build 116 from the exact source commit

```bash
export ADOPTIQ_RELEASE_GATE=1
export ADOPTIQ_BAKE_CORPUS=1
export ADOPTIQ_CSONE_CORPUS_DIR='/approved/external/AdoptIQ_CSOne_Reports'
export ADOPTIQ_BAKE_FIXTURE_DIR="$ADOPTIQ_CSONE_CORPUS_DIR"
export ADOPTIQ_FASTEMBED_CACHE='/approved/persistent/fastembed-model-cache'
export ADOPTIQ_EXPECTED_COMMIT="$(git rev-parse HEAD)"
bash build_mac_dmg.sh
```

The build must remain stage-only. It may refresh the ignored local
`OUTBOX/latest.json` staging metadata, but that is not a consumer release manifest and
must remain untracked and unpublished. Do not install, publish, update the consumer or
OneDrive `latest.json`, tag, or deploy. A source change during/after packaging
invalidates the candidate and requires a build bump plus a complete restart.

Create candidate evidence only after the exact DMG and generated `build_info.txt`
exist. Never hand-copy hashes from chat or a prior build:

```bash
set -euo pipefail
CANDIDATE_DIR='release_candidates/macos-build116'
if [[ -e "$CANDIDATE_DIR" || -L "$CANDIDATE_DIR" ]]; then
  echo 'STOP: Build 116 candidate directory already exists.' >&2
  exit 1
fi
mkdir -- "$CANDIDATE_DIR"
```

**STOP here.** Author `$CANDIDATE_DIR/README.md` through the reviewed editor. Do
not paste the next block until that new file exists as a regular, non-symlink file.
The second block is independently fail-fast and refuses every generated target that
already exists, including a broken symlink:

```bash
set -euo pipefail
CANDIDATE_DIR='release_candidates/macos-build116'
for TARGET in \
  "$CANDIDATE_DIR/build_info.txt" \
  "$CANDIDATE_DIR/candidate.json" \
  "$CANDIDATE_DIR/manual-review.template.json"
do
  if [[ -e "$TARGET" || -L "$TARGET" ]]; then
    echo "STOP: candidate evidence target already exists: $TARGET" >&2
    exit 1
  fi
done
if [[ ! -f "$CANDIDATE_DIR/README.md" || -L "$CANDIDATE_DIR/README.md" ]]; then
  echo 'STOP: author a regular candidate-specific README.md first.' >&2
  exit 1
fi
if [[ ! -f OUTBOX/build_info.txt || -L OUTBOX/build_info.txt ]]; then
  echo 'STOP: generated OUTBOX/build_info.txt is missing or unsafe.' >&2
  exit 1
fi
cp -n -- OUTBOX/build_info.txt "$CANDIDATE_DIR/build_info.txt"
if [[ ! -f "$CANDIDATE_DIR/build_info.txt" || -L "$CANDIDATE_DIR/build_info.txt" ]]; then
  echo 'STOP: build_info.txt was not copied as a regular file.' >&2
  exit 1
fi
cmp -s OUTBOX/build_info.txt "$CANDIDATE_DIR/build_info.txt"
BUILT_AT_UTC="$(sed -n 's/^Built: //p' OUTBOX/build_info.txt)"
.venv/bin/python scripts/create_release_candidate.py \
  --artifact OUTBOX/AdoptIQ-v1.0.4-build116.dmg \
  --source-commit-sha "$(git rev-parse HEAD)" \
  --built-at-utc "$BUILT_AT_UTC" \
  --output "$CANDIDATE_DIR/candidate.json" \
  --platform macos --version 1.0.4 --build 116 \
  --sidecar "$CANDIDATE_DIR/build_info.txt" \
  --sidecar "$CANDIDATE_DIR/README.md"
.venv/bin/python scripts/create_manual_review_template.py \
  --candidate-manifest "$CANDIDATE_DIR/candidate.json" \
  --output "$CANDIDATE_DIR/manual-review.template.json"
```

Use the repository's normal reviewed edit workflow to create the directory and exact
sidecars; the shell excerpt is identity guidance, not permission to bypass it. The
candidate creator computes and verifies artifact/sidecar digests and size. The review
creator exclusively publishes a canonical, candidate-bound NO-GO template; it never
overwrites evidence or records an approval. Candidate evidence may be committed
separately only after review and explicit authorization.

## Gate 6 — exact frozen candidate and live acceptance

Keep evidence outside Git:

```bash
EVIDENCE='/approved/external/evidence/build116'
DMG="$PWD/OUTBOX/AdoptIQ-v1.0.4-build116.dmg"
.venv/bin/python scripts/smoke_frozen_candidate.py \
  --candidate "$DMG" \
  --expected-version 1.0.4 --expected-build 116 \
  --require-release-corpus \
  --summary "$EVIDENCE/frozen-smoke.json"

.venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir "$EVIDENCE/live-summary" \
  --retain-sensitive-dir "$EVIDENCE/live-artifacts" work-machine \
  --candidate-dmg "$DMG" \
  --candidate-manifest release_candidates/macos-build116/candidate.json \
  --manager '<authorized manager>' \
  --member-email '<authorized member>' \
  --customer-name '<unambiguous customer>' \
  --customer-member-email '<authorized owner>' \
  --ambiguous-customer-name '<known ambiguous customer>' \
  --subscription-id '<authorized subscription>' \
  --technology 'All Contact Center' --days 90 \
  --as-of '<current precise UTC timestamp>' \
  --csone-file '<approved current CSOne xlsx>'
```

Do not prestart an unrelated app; the runner must verify, mount, and launch the exact
eligible candidate. Require candidate/runtime identity, no skipped detailed gate, all
two-pass report/AI/workspace/replay/source checks, and fail-closed negative scopes.

Copy `manual-review.template.json` outside Git, bind it to the exact live-summary
SHA-256 and candidate identity, and attest only checks personally reconciled. Keep
customer samples and generated artifacts in the approved external evidence location.

## Stop 3 — GO/NO-GO and promotion

Present source and candidate SHA/hash/size/time, command counts, offline versus live
evidence, manual/visual results, failures, and remaining risks. A green automated run
does not authorize merge, tag, install, promotion, publication, or deployment.

Only after separate explicit promotion approval may
`scripts/promote_mac_release.py` consume the exact candidate manifest, fresh frozen
smoke, complete live summary, and bound manual review. It must preserve the PC slot or
its absence, reject downgrade/same-build-different-bytes, copy exact bytes first, and
expose `latest.json` last. Never call `write_release_manifest.py` directly.

## Immediate stop conditions

- dirty/unexpected source, advanced upstream, ancestry mismatch, or altered remote;
- Build 115 presented as eligible or any attempt to reuse/recreate it;
- missing/invalid Build 116 candidate identity or changed source after package;
- credentials/customer rows/generated artifacts entering Git;
- incomplete/red source, simulation, live, smoke, report, AI, manual, or visual gate;
- candidate/runtime/source mismatch, unsafe link, scope leak, false zero, or unsupported
  production-accuracy claim;
- request to publish/install/promote/deploy without specific approval.
