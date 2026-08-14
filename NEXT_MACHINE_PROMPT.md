# AdoptIQ — authoritative next-machine handoff (Round 167.5 / Build 115)

This is the only executable release runbook. Earlier Round 166/167 instructions in
Git history and audit journals are evidence, not current commands. The copy-ready
prompt (kept below 4,000 characters) is `WORK_MACHINE_BUILD115_PROMPT.md`.

## Current truth

- Product: AdoptIQ v1.0.4, source build 115.
- External source of truth: `https://github.com/rupret007/AdoptIQ`, branch `main`.
- Cisco destination remains the existing internal remote/config on the work Mac; do
  not alter or expose it.
- Build 114 DMG (`4cc485da…d6235`) is **invalidated**. It predates the final explicit
  Compact source window and Leader fail-closed CSOne boundary. It may be retained as
  historical evidence but must not be installed, staged, approved, or promoted.
- Build 115 must be built from the exact source commit recorded in
  `release_candidates/macos-build115/candidate.json`. That manifest also pins the
  artifact name, byte count, SHA-256, build timestamp, status, and sidecars.
- Every paired Source Data workbook must have exactly 17 sheets in canonical order:
  `Report_Info`, `Metric_Lineage`, `Chart_Data`, `Evidence_Links`, `Action_Plans`,
  `Adoption_Barriers`, `Customer_Pulse`, `TAC_Cases`, `BEMS`, `Subscriptions`,
  `Success_Priorities`, `External_Incidents`, `External_Bugs`,
  `Defect_Correlations`, `Risk_Components`, `Member_Summary`, `Account_Summary`.
- Local/synthetic evidence is not live Cisco validation. `production_accuracy_claimed`,
  `release_ready`, and publication remain false until the live and manual gates pass.
- OneDrive’s last observed Mac slot was Build 113. Re-read it at promotion time. The
  PC slot may be present or absent; either state must be preserved semantically.

## Why Round 167.5 exists

The earlier broad matrix could report green while equivalent report routes used
different source windows, managers, customers, source states, or freshness clocks.
Round 167.5 now:

1. passes explicit authorized manager/customer scopes through the entire A–G matrix;
2. derives KPI requirements from report family rather than scenario-key strings;
3. requires the exact expected/completed scenario inventory with no duplicates;
4. fails closed if any R114 artifact audit crashes, omits its terminal marker, or is
   missing a DOCX/XLSX pair;
5. compares privacy-safe source IDs, attribution, source states, and freshness across
   equivalent Compact, Comprehensive, Renewal, and Leader scopes;
6. runs Compact with an explicit bounded CSOne window and Leader through the shared
   strict CSOne scope/provenance boundary;
7. verifies and launches the exact candidate DMG inside live acceptance instead of
   trusting whichever app happens to be listening on port 5151;
8. binds promotion to an immutable eligible-candidate manifest, exact smoke evidence,
   complete live acceptance, and a separately hashed manual-review record;
9. rejects invalidated candidates, downgrades, same-build/different-byte writes,
   unsafe cloud paths, incomplete manifests, and direct manifest-writer bypasses.

## Safety and source preservation

Before mutation:

```bash
set -euo pipefail
cd /path/to/AdoptIQ
git status --short --branch
git fetch rupret007 main
git checkout main
git pull --ff-only rupret007 main
git status --short --branch
```

Require a clean tree and the expected Round 167.5 files. Preserve any internal Cisco
remote, Snowflake/Keeper/CircuIT/OneDrive configuration, ignored runtime state, and
generated reports. Never stash, reset, clean, force-push, merge unrelated history,
print secrets, copy customer data into Git, or replace integration files wholesale.

Use Python 3.12 for release evidence. Install with the tracked
`constraints-build113.txt`; that filename is a validated dependency-lock lineage,
not the current product build number. Do not invent `constraints-build114.txt`.

## Gate 1 — source verification

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints-build113.txt
make verify PY=.venv/bin/python
```

Require Ruff clean, Bandit zero HIGH/MEDIUM, strict dependency audit clean, full
pytest green with only documented environment skips, and deterministic Ask AI eval
green. Record exact counts. Never edit tests merely to erase red.

## Gate 2 — extensive production-like simulation

Point to an approved external directory of current CSOne exports; never copy it into
the repository.

```bash
make production-simulation \
  PY=.venv/bin/python \
  CSONE_CORPUS_DIR='/approved/external/AdoptIQ_CSOne_Reports' \
  OUTPUT_DIR='.adoptiq-acceptance/prebuild-production-simulation'
```

This must exercise all configured A–G report selections, multiple named managers,
All Managers, Leader Team/Member/Customer, portfolio/customer Compact and
Comprehensive, portfolio/customer Renewal, Subscription, degraded source states,
cross-manager isolation, evidence links, manager workspace, Ask AI sync/stream, and
the replay corpus. Require:

- exact scenario keys/counts and no missing, duplicate, malformed, or extra result;
- one successful R114 audit for every generated DOCX/XLSX pair;
- at least one meaningful cross-family source comparison;
- zero source-ID, attribution, source-state, or freshness mismatches;
- deterministic local freshness equality (live sequential runs use only the explicit
  four-hour maximum skew, with state equality still exact);
- no unexplained `Unknown`, literal `undefined`/`null`, fake URL, misleading zero,
  orphan page, missing chart, or cross-scope record;
- `live_validation_performed=false`, `production_accuracy_claimed=false`, and
  `release_ready=false`, because this remains guarded fixture evidence.

## Gate 3 — metadata-only Snowflake opportunity inventory

Run only while VPN and the existing authorized connection are available:

```bash
.venv/bin/python scripts/profile_snowflake_capabilities.py \
  --live-metadata \
  --confirm-authorized-live-metadata \
  --summary /approved/external/evidence/snowflake-capabilities.json
```

The profiler may describe allow-listed schemas but must query no customer rows. An
unreachable connection must return a sanitized `connection_unavailable` summary,
`row_values_queried=false`, and nonzero exit without raw provider text. Missing
permission means `blocked`, never zero. Do not widen table policy.

For an accessible field, document authoritative meaning, business owner, stable join
key/cardinality, scope authorization, null/duplicate/late-arrival semantics, source
event clock versus load clock, decision value, canonical target, and required tests.
Prefer stable IDs/source clocks, renewal timing/exposure, Action Plan execution, and
Pulse explanation. Keep currency groups separate without governed conversion data.
Keep source-provided renewal outlook separate from AdoptIQ escalation risk.

## Gate 4 — exact Build 115 candidate

Do not reuse Build 114. A release-gated build must start from clean upstream `main`
with the approved corpus, secrets file, native architecture, and model cache:

```bash
export ADOPTIQ_RELEASE_GATE=1
export ADOPTIQ_BAKE_CORPUS=1
export ADOPTIQ_CSONE_CORPUS_DIR='/approved/external/AdoptIQ_CSOne_Reports'
export ADOPTIQ_BAKE_FIXTURE_DIR="$ADOPTIQ_CSONE_CORPUS_DIR"
export ADOPTIQ_FASTEMBED_CACHE='/approved/persistent/fastembed-model-cache'
export ADOPTIQ_EXPECTED_COMMIT="$(git rev-parse HEAD)"
bash build_mac_dmg.sh
```

After packaging, create/verify the tracked candidate contract. Never hand-edit a hash
without hashing the exact bytes. The source commit may be an ancestor of later
tooling/docs commits, but any product/runtime change invalidates the candidate.

For a new candidate directory, author a concise candidate-specific `README.md`,
reproduce the exact generated `build_info.txt`, prove byte parity, and let the
fail-closed creator compute every size/digest and self-verify the result. Do not copy
the general installer `OUTBOX/README.md` into the immutable candidate contract:

```bash
CANDIDATE_DIR='release_candidates/macos-build115'
mkdir "$CANDIDATE_DIR"
# Create the candidate-specific README and exact build_info using the repository's
# normal reviewed file-edit workflow, then require exact build-info byte parity.
cmp -s OUTBOX/build_info.txt "$CANDIDATE_DIR/build_info.txt"
BUILT_AT_UTC="$(sed -n 's/^Built: //p' OUTBOX/build_info.txt)"
.venv/bin/python scripts/create_release_candidate.py \
  --artifact OUTBOX/AdoptIQ-v1.0.4-build115.dmg \
  --sidecar "$CANDIDATE_DIR/README.md" \
  --sidecar "$CANDIDATE_DIR/build_info.txt" \
  --output "$CANDIDATE_DIR/candidate.json" \
  --platform macos --version 1.0.4 --build 115 \
  --source-commit-sha "$ADOPTIQ_EXPECTED_COMMIT" \
  --built-at-utc "$BUILT_AT_UTC"
```

The creator refuses symlinks, nonregular/missing files, unsafe sidecar locations,
invalid identity, and any existing `candidate.json`; never delete or overwrite an
existing contract just to rerun it.

The DMG is intentionally not stored in Git. Transfer the exact manifest-pinned bytes
to the authorized work Mac only through an approved encrypted Cisco channel and
verify them before use. If those bytes cannot be obtained, do not manufacture another
Build 115: bump the build, package a new candidate, and restart every candidate gate.

Run a clean-room frozen smoke from the exact DMG path that will be used later:

```bash
EVIDENCE='/approved/external/evidence/build115'
DMG="$PWD/OUTBOX/AdoptIQ-v1.0.4-build115.dmg"
mkdir -p "$EVIDENCE"
.venv/bin/python scripts/smoke_frozen_candidate.py \
  --candidate "$DMG" \
  --expected-version 1.0.4 \
  --expected-build 115 \
  --require-release-corpus \
  --summary "$EVIDENCE/frozen-smoke.json"
```

Require DMG hash/size parity with the candidate manifest, integrity and signature
verification, frozen runtime identity, complete bundled corpus parse/vector counts,
hybrid retrieval, ready embedder/reranker, zero dense backlog, and no inherited user
corpus/model cache. This proves packaging—not Cisco truth.

## Gate 5 — controlled live Cisco acceptance

Prerequisites: VPN/DNS, existing authorized Keeper/Snowflake/CircuIT/OneDrive config,
current CSOne workbook, one authorized manager/member/customer/subscription, one known
ambiguous customer, and external evidence directories. Do not start another AdoptIQ
process. The runner verifies, mounts, and launches the exact eligible DMG itself and
rejects an occupied port.

```bash
NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
.venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir "$EVIDENCE/live-summary" \
  --retain-sensitive-dir "$EVIDENCE/live-artifacts" \
  work-machine \
  --candidate-dmg "$DMG" \
  --candidate-manifest release_candidates/macos-build115/candidate.json \
  --manager '<authorized manager>' \
  --member-email '<authorized roster member>' \
  --customer-name '<unambiguous authorized customer>' \
  --customer-member-email '<authorized customer owner>' \
  --ambiguous-customer-name '<known ambiguous customer>' \
  --subscription-id '<authorized subscription>' \
  --technology 'All Contact Center' \
  --days 90 \
  --as-of "$NOW_UTC" \
  --csone-file '/approved/external/current-CSOne-export.xlsx'
```

No skip flags are release-acceptable. Require candidate/runtime identity, four
decision scopes twice, exact A–G inventory, R114 per pair, cross-report source
consistency, AI two-pass repeatability, manager workspace parity, and Ask AI 75/75
plus 25/25 canonical replay. Run a second authorized manager/team and All Managers
as documented/manual checks; out-of-roster, ambiguous, mixed-scope, and unauthorized
subscription requests must fail before connection/filter work.

## Gate 6 — manual truth and visual reconciliation

Copy `release_candidates/macos-build115/manual-review.template.json` outside Git.
Do not change any false value until evidence supports it. Bind
`acceptance_summary_sha256` to the exact sanitized live-summary bytes.

Review Leader Team/Member/Customer, Comprehensive portfolio/customer, Compact
portfolio/customer, Renewal portfolio/customer, and Subscription. For each:

- reconcile every visible KPI and four chart series to `Chart_Data`;
- reconcile each claim to `Metric_Lineage` and the exact source row;
- reconcile each evidence reference to `Evidence_Links`, stable Record ID, source
  sheet, and authorized live record;
- reconcile defect summaries and linked incident/bug pairs to
  `Defect_Correlations` without double counting;
- open at least one Action Plan, Adoption Barrier, Customer Pulse, and Success
  Priority link; a login redirect or plausible URL is not proof;
- verify Action Plan raw status, lifecycle, owner, due date, and unresolved age;
- verify TAC/BEMS distinct IDs, timestamps, ownership, and no shared-attribution
  inflation;
- verify renewal timing/exposure/outlook separately from deterministic AdoptIQ risk;
- inspect every DOCX page and workbook sheet for clipping, blank/orphan pages,
  missing charts, broken links, raw placeholders, misleading zeroes, and unexplained
  unknowns;
- require honest unavailable/partial/stale disclosure. Never guess a value.

Keep sensitive artifacts and reconciliation worksheets outside Git. Commit only
sanitized counts, hashes, states, and conclusions.

## Gate 7 — approval and promotion

Stop and present exact source/candidate identity, all automated counts, live/manual
results, visual review, remaining risks, and GO/NO-GO. No push to internal GitHub,
merge, tag, install, OneDrive copy, `latest.json` update, or deployment is implied by
test approval. Publication needs separate explicit approval.

After approval only:

```bash
.venv/bin/python scripts/promote_mac_release.py \
  --dmg "$DMG" \
  --candidate-manifest release_candidates/macos-build115/candidate.json \
  --smoke-summary "$EVIDENCE/frozen-smoke.json" \
  --acceptance-summary "$EVIDENCE/live-summary/round146_acceptance_summary.json" \
  --manual-review-summary "$EVIDENCE/manual-review.json" \
  --approve-publish \
  --summary "$EVIDENCE/promotion.json"
```

This is the only allowed manifest publication path. Never invoke
`write_release_manifest.py` directly. The promoter re-verifies eligible status,
candidate bytes/sidecars, candidate-source ancestry, clean upstream `main`, smoke,
complete live gates, manual-review hash, DMG/app payload/signatures, managed OneDrive
paths, existing artifact integrity, monotonic build transition, and manifest
concurrency. It copies bytes first and atomically publishes `latest.json` last.

Preserve `OUTBOX/latest.before-mac-build115.json` as rollback evidence. Re-read and
hash the resulting Mac artifact/manifest. Preserve any PC slot exactly, or preserve
its absence. Do not start a Windows build in this Mac handoff.

## Stop conditions

Stop rather than rationalize any of these:

- candidate status/hash/size/source mismatch or a post-build runtime edit;
- dirty/divergent/unrelated Git history;
- lower/missing/duplicate scenario inventory;
- test, R114, source-consistency, freshness, scope-isolation, AI, or replay failure;
- unexplained zero/unknown, missing expected record link, or source mismatch;
- live credentials/VPN/source unavailable for a production claim;
- malformed, newer, or concurrently changed release manifest;
- manual review not tied to the exact live summary and candidate;
- requirement to weaken authorization, query policy, canonical metrics, or tests.

The correct result can be a precise NO-GO. Accuracy is more important than shipping.
