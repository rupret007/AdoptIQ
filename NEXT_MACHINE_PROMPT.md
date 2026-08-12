# AdoptIQ — next-machine handoff (Round 164 Mac packaging)

Use this prompt after pulling `main`. Round 163 is the large accuracy,
predictive-depth, Ask AI, UI/UX, and stability integration. Round 164 stamps that
source as `v1.0.4` Build 113 and prepares the work-machine Mac release path.

> Important release distinction: Build 113 is prepared but not yet packaged. It still
> requires a fresh approved corpus bake, release-gated DMG build, install/smoke, and
> live Cisco production reconciliation on the work machine. Build 112 remains the last
> Mac installer that received a live Cisco smoke.

---

## Start here

```bash
set -euo pipefail
git checkout main
git pull --ff-only rupret007 main
git status --short --branch
git log -3 --oneline
```

Read, in this order:

1. `NEXT_MACHINE_PROMPT.md` — this file
2. `QUALITY_AUDIT.md` — `## Round 163 — all-source intelligence and predictive depth`
3. `CLAUDE.md` — repository rules and source-of-truth constraints
4. `decision_report_delivery.py`, `risk_scoring.py`, and `predictive_signals.py`
5. `ask_ai_grounded.py` and `defect_correlation.py`

Never commit `secrets.env`, Cisco credentials, live customer exports, local acceptance
artifacts, or generated reports.

## Mission and governing contract

AdoptIQ’s advantage is the combined depth of its sources. A report or Ask AI answer
must not merely mention those sources; every applicable source must do one of the
following after the requested manager/member/customer/subscription/technology/time
criteria are applied:

1. contribute to canonical identity, metrics, risk, forecast, narrative, or action;
2. appear as exact, citable decision context; or
3. disclose why it could not support a conclusion.

The non-negotiable invariants are:

- scope first, analyze second;
- stable customer/account/subscription IDs win over display names;
- merge both physical Adoption Barrier feeds with stable-ID deduplication;
- unavailable, failed, stale, partial, and genuine zero are different states;
- never widen a named-technology or selected-customer report to make it look fuller;
- never turn an unavailable integration into an exact zero;
- never smear an untagged portfolio incident or defect across every customer;
- one canonical fact/lineage contract drives Word, Excel, report preview, and Ask AI;
- unsupported or contradictory factual prose blocks publication;
- predictive claims must state coverage and calibration status;
- LLM prose is downstream of deterministic facts and addressable evidence.

## Round 163 outcome

### 1. Criteria-scoped, all-source reporting

- Customer identity/counting is ID-first and spans scoped subscriptions, Action
  Plans, merged Adoption Barriers, Customer Pulse, TAC/CSOne, and Success Priorities.
- Registered name aliases can join an ID-backed identity only when the match is
  unambiguous; distinct stable IDs remain distinct even when names collide.
- Compact, Comprehensive, Renewal, Subscription, Executive Intelligence, and Leader
  now use criteria-scoped inputs through their final Word/XLSX contract.
- Compact and Renewal source workbooks contain real scoped `Subscriptions` sheets;
  `Risk_Summary` and `Renewal_Summary` are no longer misrepresented as subscription
  evidence.
- Named-technology CSConsole data without authoritative technology evidence fails
  closed. Action Plans with ambiguous technology are retained only when their
  customer/account identity is already in the scoped subscription cohort.
- Subscription reports defensively rescope every source to the selected
  subscription/account/customer before validation, scoring, AI, or artifact writing.
- Comprehensive degrades honestly when AB and TAC are empty but scoped subscription
  or CSConsole evidence remains; actual fetch/schema/integrity failures still abort.

### 2. Cross-source decision depth

The canonical report renders a deterministic `Cross-Source Decision Signals` layer.
Each applicable source produces a prioritized signal/action with exact evidence, or
an explicit no-conclusion state:

- Snowflake subscriptions and renewal data;
- Snowflake and CSConsole Action Plans;
- merged Snowflake and CSConsole Adoption Barriers;
- Customer Pulse;
- TAC/CSOne cases and BEMS escalation references;
- Success Priorities;
- Webex status incidents and maintenances;
- external bugs/BST and exact CSC correlations.

Success Priorities, unlinked external bugs, maintenances, PSIRT, and Circuit/status
context are visible and actionable but are not assigned an invented risk weight.

### 3. Predictive intelligence without false certainty

There are two deliberately separate outcomes:

- **Escalation Outlook** estimates near-term escalation watch level from canonical
  TAC, BEMS, Adoption Barrier, and Pulse signals. Its evaluation clock is independent
  of source-freshness timestamps.
- **Renewal Outlook** reports account-attributed Snowflake renewal date/status/
  probability as a source-provided estimate. It is not folded into the escalation
  score and is not described as AdoptIQ-calibrated.

Prediction coverage now distinguishes complete, partial, stale, unavailable, failed,
and verified-zero feeds. Failed/unavailable TAC blocks the escalation forecast;
missing AB/Pulse produces an explicit lower-bound/partial result. Calibration loads
only from an explicit, non-symlinked, bounded JSON artifact whose provenance is
`derived_from=live_cisco_sources`. Missing or invalid calibration fails safely to an
uncalibrated disclosure.

External incidents affect a customer only when account/customer attribution is
present. Untagged status incidents remain portfolio context and contribute no
per-customer weight. Tagged incidents produce a precise driver and incident-specific
next-best action. Failed incident feeds are missing components, not zeros.

### 4. BST/CSC correlation

`defect_correlation.py` is the shared exact-correlation layer:

```python
build_defect_correlation_bundle(
    scoped_tac,
    scoped_ab,
    external_bugs,
    identity_resolver=...,
)
```

It returns bounded deterministic `records`, `unmatched_external_bugs`, and `coverage`.
Only exact normalized CSC references correlate; BEMS references are never treated as
software defects. Records retain canonical customer identity, parent TAC/AB rows and
stable IDs, verified BST status/severity/version, provenance, and action context.
Correlations are exported in `Defect_Correlations`, mapped through Metric Lineage and
Evidence Links, rendered as visible decision signals, and exposed to Ask AI as
tamper-evident evidence. They intentionally have no numeric weight until a live,
labeled backtest supports one.

`CiscoInternalIntegrations` now accepts `bst_client_secret`; all application
construction paths pass `BST_CLIENT_SECRET`. Official BST lookup requires both key
and secret, while absent credentials retain the manual-link/fail-soft path. Logs never
include credentials or response bodies.

### 5. Ask AI

- Ask AI reuses the report’s canonical ID-first risk-profile and predictive seams.
- Its universe includes subscriptions, AP, merged AB, Pulse, TAC, Success Priorities,
  customer-tagged incidents, renewal outlooks, maintenances, and exact BST evidence.
- Deterministic `DecisionMetric`, `RenewalOutlook`, `BSTReference`, and maintenance
  records are citable; tampered or non-rendered IDs are rejected.
- “Who should I call first?” and “who is likely to escalate?” survive final grounding
  with resolvable evidence rather than being stripped as unsupported prose.
- The current user turn controls report-bound intent; conflicting history cannot turn
  a “completed” question into an “overdue” query.
- The browser client owns one answer target per turn, reuses it on retry/fallback,
  delegates live citations from the chat container, records the real sync answer,
  blocks overlapping sends, and distinguishes manual cancellation from timeout.

### 6. UI/UX and progress truth

- Navigation renders a single Run Analysis entry with responsive mobile behavior.
- Report cards retain aligned badge/radio regions and an accessible named radiogroup.
- Progress warnings render the producer’s real dataset/kind/error fields, use warning
  styling, remain visible for terminal CSOne states, and announce updates through an
  ARIA live region.
- Browser checks covered 390 px, 1050 px, and 1440 px: controls remained accessible,
  mobile navigation toggled at the intended widths, desktop showed one Run Analysis
  link, and the loading overlay did not remain stuck.

### 7. Stability and source handling

- CSOne discovery excludes prior AdoptIQ outputs case-insensitively, rejects
  unsupported `.xls`, skips unreadable/zero-byte candidates, and survives per-file
  OneDrive hydration/mtime races by trying the next candidate.
- Comprehensive preserves honest CSOne unavailable/failure state rather than
  overwriting it as a successful scoped-empty load.
- Corpus bootstrap now uses the `_user_corpus_dir()` seam consistently. Tests no
  longer write the real Application Support corpus or scan real user output folders.
- Publication validators remain fail-closed for legacy/canonical risk contradictions,
  factual claims, insight paragraphs, citations, lineage, and source-sheet parity.

## Final verification on the integrated tree

Repository gate:

```text
7,335 collected
14 deselected
7,321 selected
7,313 passed
0 failed
8 skipped
2,458 warnings
470.44s (7:50)
```

Preserved log on the source machine:
`/tmp/adoptiq-round164-final3-tests.log`

Complete clean-room acceptance:

```text
7/7 required gates passed; 0 skipped
fixture manifest: 21 scenarios
degraded HTTP: 21/21
decision reports: 2 passes, 4/4 scopes, repeatable
report matrix: 36/36, all A–G blocks
AI feature acceptance: 2/2, repeatable
manager workspace: 14 reports, 8/8 previews, 36 history rows, 0 errors
Ask AI replay: 75/75 questions, 25/25 canonical checks
```

Summary on the source machine:
`/private/tmp/adoptiq-round164-acceptance-final-authorized-20260812/round146_acceptance_summary.json`

The Round 164 clean-room run started at `2026-08-12T07:38:42Z`, completed at
`2026-08-12T07:49:28Z`, and passed all required local gates. The first sandboxed
attempt was discarded because macOS denied loopback binds; only the authorized
loopback result above is release evidence.

Additional final gates:

- `git diff --check` — passed
- `.venv/bin/ruff check .` — passed
- production `compileall` — passed
- `node --check static/js/ask_ai.js` — passed
- `.venv/bin/pip check` — passed
- `make eval-ask-ai` — 14/14 passed, including 75/75 replay and 25 checks
- `make security` — passed; no medium/high Bandit findings
- `.venv` `pip-audit --local --strict` — no known vulnerabilities

The acceptance corpus is sanitized and deterministic. Its summary correctly records
`live_validation_performed=false`, `production_accuracy_claimed=false`, and
`release_ready=false`.

## Highest-priority next work

## Build 113 Mac work-machine runbook

Use the same immutable source commit for Mac first and Windows second. The build
number is part of the source contract: if any application, report, Ask AI, corpus, or
packaging code changes after the Mac DMG is created, do not build the PC artifact as
Build 113. Land the fix, increment `ADOPTIQ_BUILD`, and rebuild both platforms.

### 1. Pull and pin the source

Start from a clean work-machine checkout. Preserve any local work before this block;
do not discard it with a reset or checkout command.

```bash
set -euo pipefail
git checkout main
git fetch rupret007 main
git pull --ff-only rupret007 main
git status --short --branch
test -z "$(git status --porcelain=v1 --untracked-files=all)"
BUILD_SHA="$(git rev-parse HEAD)"
test "$BUILD_SHA" = "$(git rev-parse @{upstream})"
test "$(python3 -c 'from config import ADOPTIQ_VERSION; print(ADOPTIQ_VERSION)')" = "1.0.4"
test "$(python3 -c 'from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)')" = "113"
echo "Pinned Build 113 source: $BUILD_SHA"
```

The later Windows build must use this exact commit. Record `BUILD_SHA` in the Mac
acceptance notes and on the Windows work machine verify `git rev-parse HEAD` matches
it before running `build_pc.bat`.

### 2. Prepare the private build inputs

`secrets.env` must sit at the repository root, remain Git-ignored/untracked, and be
owner-only. Never paste its values into a prompt, console transcript, commit, or test
summary.

```bash
test -f secrets.env
chmod 600 secrets.env
git check-ignore -q secrets.env
! git ls-files --error-unmatch secrets.env >/dev/null 2>&1
```

Choose the exact approved, fully hydrated local folder for this build. Do not rely on
auto-discovery for a production bake. It must contain the intended `.csv`, `.docx`,
and/or `.xlsx` source files; nested folders are preserved. Any supported zero-byte,
unreadable, symlinked, corrupt, or semantically empty file makes the release bake fail
rather than silently thinning the corpus. Replace the example path below:

```bash
export ADOPTIQ_BAKE_FIXTURE_DIR="/ABSOLUTE/PATH/TO/APPROVED/AdoptIQ_CSOne_Reports"
test -d "$ADOPTIQ_BAKE_FIXTURE_DIR"
```

Set the expected native architecture explicitly. Use `arm64` on an Apple Silicon Mac
or `x86_64` on an Intel Mac. Do not run an Apple Silicon package through Rosetta.

```bash
export ADOPTIQ_EXPECTED_ARCH="$(uname -m)"
case "$ADOPTIQ_EXPECTED_ARCH" in arm64|x86_64) ;; *) exit 1 ;; esac
export ADOPTIQ_FASTEMBED_CACHE="$HOME/Library/Caches/AdoptIQ/fastembed"
mkdir -p "$ADOPTIQ_FASTEMBED_CACHE"
```

The persistent model cache is a quality-neutral speedup: it reuses identical
embedding/reranker bytes and does not change corpus rows, chunks, vectors, scores, or
validation. Build 113 packages the validated cache with deterministic SHA-256
evidence, so a Finder-launched app stays hybrid-ready without a first-run download.
The bake also removes a duplicate database seal. Runtime hybrid retrieval reuses baked
candidate vectors only when the complete model/dimension/blob/finite-value contract
validates; otherwise it embeds the same candidates live. It still parses every
supported source, requires meaningful chunks, writes and counts every dense vector,
runs semantic embedding and reranker probes, encrypts the fresh corpus once, and
performs the decrypt round trip. Do not use `ADOPTIQ_BAKE_CORPUS=0`, `--no-bake`, a
partial-vector limit, or an old baked snapshot for a shipping candidate.

### 3. Recreate the approved environment and run every source gate

Prefer a fresh `.venv` on the native host. Dependency/model downloads may require the
Cisco trust chain/VPN. The release preflight is read-only and never prints values or
creates `_bundled_secrets.py`.

```bash
set -euo pipefail
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -c constraints-build113.txt
.venv/bin/python -m pip install -c constraints-build113.txt ruff bandit pip-audit
.venv/bin/python -m pip check

ADOPTIQ_EXPECTED_COMMIT="$BUILD_SHA" \
  .venv/bin/python scripts/preflight_mac_release.py \
  --expected-version 1.0.4 \
  --expected-build 113 \
  --expected-commit "$BUILD_SHA" \
  --expected-arch "$ADOPTIQ_EXPECTED_ARCH" \
  --corpus-source "$ADOPTIQ_BAKE_FIXTURE_DIR" \
  --summary /tmp/adoptiq-build113-mac-preflight.json

MPLCONFIGDIR=/tmp/adoptiq-build113-mpl \
  .venv/bin/python -m pytest tests -q --disable-warnings \
  2>&1 | tee /tmp/adoptiq-build113-full-pytest.log
make eval-ask-ai
make security
.venv/bin/ruff check .
.venv/bin/pip check
.venv/bin/python -m pip_audit --local --strict --progress-spinner off
git diff --check
node --check static/js/ask_ai.js
PYTHONPYCACHEPREFIX=/tmp/adoptiq-build113-pyc \
  .venv/bin/python -m compileall -q -f \
  -x '(^|/)(tests|\.venv|build|dist|OUTBOX)(/|$)' .
git status --short --branch
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

The expected repository floor from the source machine is 7,313 passed, 8 skipped,
14 deselected, and zero failures. A higher collected/passed count is acceptable when
this release-prep round adds tests; any failure is not. Do not build around a failed
gate.

Run the complete sanitized clean-room acceptance again from this exact commit:

```bash
set -euo pipefail
ACCEPT_DIR="/tmp/adoptiq-build113-cleanroom-$(date -u +%Y%m%dT%H%M%SZ)"
MPLCONFIGDIR=/tmp/adoptiq-build113-mpl \
  .venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir "$ACCEPT_DIR" local
.venv/bin/python - <<'PY' "$ACCEPT_DIR/round146_acceptance_summary.json"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
assert p["all_passed"] is True
assert p["acceptance_complete"] is True
assert not p["skipped_gates"]
assert p["gates"]["report_matrix"]["passed_count"] == 36
assert p["gates"]["ask_ai_replay"]["passed_count"] == 75
print("clean-room acceptance passed")
PY
```

### 4. Build a local candidate; do not publish yet

`build_mac_dmg.sh` automatically repeats the release preflight before the expensive
bake and scrubs the generated reversible `_bundled_secrets.py` on every exit. The
default is stage-only: it writes `OUTBOX/AdoptIQ-v1.0.4-build113.dmg` and a local
manifest, but it does not update OneDrive or `latest.json` for consumers.

```bash
set -euo pipefail
export ADOPTIQ_VERSION="1.0.4"
export ADOPTIQ_BUILD="113"
export ADOPTIQ_EXPECTED_COMMIT="$BUILD_SHA"
export ADOPTIQ_RELEASE_GATE="1"
export ADOPTIQ_BAKE_CORPUS="1"
export ADOPTIQ_PUBLISH_RELEASE="0"
time bash build_mac_dmg.sh 2>&1 | tee /tmp/adoptiq-build113-mac-build.log

test -f OUTBOX/AdoptIQ-v1.0.4-build113.dmg
test ! -e _bundled_secrets.py
hdiutil verify OUTBOX/AdoptIQ-v1.0.4-build113.dmg
codesign --verify --strict OUTBOX/AdoptIQ-v1.0.4-build113.dmg
grep 'bake timing:' /tmp/adoptiq-build113-mac-build.log
grep -F "Source commit: $BUILD_SHA" OUTBOX/build_info.txt
```

The timing lines identify `stage_inputs`, `parse_and_lexical_index`, `dense_vectors`,
`reranker_self_test`, `encrypt_and_commit`, `decrypt_round_trip`, and `total`. Keep
the log so the next optimization targets measured work. Do not infer that a slow phase
can be skipped; preserve its output contract.

### 5. Verify the packaged bytes and inspect the UI

```bash
.venv/bin/python scripts/smoke_frozen_candidate.py \
  --candidate OUTBOX/AdoptIQ-v1.0.4-build113.dmg \
  --expected-version 1.0.4 \
  --expected-build 113 \
  --require-release-corpus \
  --summary /tmp/adoptiq-build113-mac-smoke.json
```

Then mount/install the DMG and verify, at minimum:

- Gatekeeper/unblock flow and first-launch startup;
- version footer shows v1.0.4 build 113;
- Corpus panel reports the fresh baked snapshot and dense retrieval readiness;
- Analyze at 390 px, 1050 px, and 1440 px with no overlap/stuck overlay;
- one Compact, Comprehensive, Renewal, Subscription, Executive Intelligence, and
  Leader artifact can be opened; Word, XLSX, preview, scope, lineage, and citations
  reconcile;
- Ask AI supports decision, prediction, renewal, TAC/BEMS, and BST questions with
  clickable evidence;
- no credentials, generated live reports, or raw corpus source files appear in the
  source checkout or user-visible outer DMG payload.

### 6. Run live work-machine acceptance against the packaged candidate

Launch the installed Build 113 candidate on loopback port 5153, then replace every
placeholder with an approved live scope. Retain sensitive artifacts only outside Git
and only when the operator has explicitly approved that directory.

```bash
set -euo pipefail
LIVE_DIR="/tmp/adoptiq-build113-live-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir "$LIVE_DIR" \
  work-machine \
  --base-url http://127.0.0.1:5153 \
  --manager "APPROVED MANAGER" \
  --member-email "APPROVED MEMBER EMAIL" \
  --customer-name "APPROVED CUSTOMER" \
  --customer-member-email "APPROVED CUSTOMER MEMBER EMAIL" \
  --subscription-id "APPROVED SUBSCRIPTION ID" \
  --technology "APPROVED TECHNOLOGY OR All" \
  --days 90 \
  --as-of "YYYY-MM-DD" \
  --csone-file "/ABSOLUTE/PATH/TO/APPROVED/CSOne.xlsx"
```

Require `all_passed=true`, `acceptance_complete=true`,
`live_validation_performed=true`, `live_validation_passed=true`, no skipped gates,
and a Git SHA equal to `BUILD_SHA`. Also require `git.branch=main`, `git.dirty=false`,
and `gates.runtime_identity` to report `status=passed`, `version=1.0.4`, `build=113`,
`frozen=true`, `restart_required=false`, and `live_validation_performed=true`.
Separately reconcile exact source rows/counts, risk components/bands, predictive
coverage, renewal values, CSC/BST links, and
Word/XLSX/preview/Ask AI claims. The harness intentionally does not set
`production_accuracy_claimed`, `manual_source_reconciliation_complete`,
`visual_review_complete`, or `release_ready`; those require human evidence.

```bash
.venv/bin/python - <<'PY' "$LIVE_DIR/round146_acceptance_summary.json" "$BUILD_SHA"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
r = p["gates"]["runtime_identity"]
assert p["all_passed"] and p["acceptance_complete"]
assert p["live_validation_performed"] and p["live_validation_passed"]
assert not p["skipped_gates"]
assert p["git"]["sha"] == sys.argv[2]
assert p["git"]["branch"] == "main" and p["git"]["dirty"] is False
assert r["ok"] and r["status"] == "passed" and r["live_validation_performed"]
assert (r["version"], r["build"], r["frozen"], r["restart_required"]) == ("1.0.4", "113", True, False)
print("live installed-candidate identity and acceptance passed")
PY
```

### 7. Promote only after all checks pass

This is the only publication step. It validates DMG/app signatures and integrity,
smoke identity, same-commit live acceptance, the two explicit human attestations,
safe managed OneDrive paths, and a valid existing PC manifest slot. It copies bytes
first and atomically updates `latest.json` last; it refuses a corrupt/PC-less manifest
instead of silently erasing the Windows slot.

```bash
set -euo pipefail
.venv/bin/python scripts/promote_mac_release.py \
  --dmg OUTBOX/AdoptIQ-v1.0.4-build113.dmg \
  --smoke-summary /tmp/adoptiq-build113-mac-smoke.json \
  --acceptance-summary "$LIVE_DIR/round146_acceptance_summary.json" \
  --expected-version 1.0.4 \
  --expected-build 113 \
  --expected-commit "$BUILD_SHA" \
  --manual-source-reconciliation-complete \
  --visual-review-complete \
  --approve-publish \
  --summary /tmp/adoptiq-build113-mac-promotion.json
```

Preserve `OUTBOX/latest.before-mac-build113.json` as the rollback manifest. Verify the
consumer `latest.json` Mac slot is Build 113 and its PC slot is byte-for-byte
unchanged. If any post-promotion issue appears, restore the saved manifest atomically
and remove only the Build 113 Mac artifact after resolving its exact path.

### 8. PC follow-on from the same code base

Move/pull this exact `BUILD_SHA` to the approved Windows build machine along with the
private `secrets.env` through the approved secure channel. Before building, require:

```text
git rev-parse HEAD == BUILD_SHA
config.py == v1.0.4 build 113
working tree clean
dependencies installed through constraints-build113.txt
embedding and reranker cache staged with scripts/stage_release_models.py
full Windows-focused verification green
```

Then build/smoke the PC executable. Do not change shared application code or version
metadata between the Mac and PC builds. Platform-native PyInstaller/spec and signing
steps may differ; application/report/Ask AI/corpus logic must not. If a Windows-only
fix requires a source commit, increment to Build 114 and rebuild Mac plus PC so the
same build number never identifies different code.

The current Mac scripts use ad-hoc signing for internal distribution; they do not use
a configured Developer ID, hardened runtime, notarization, or stapling. Do not
represent this candidate as publicly notarized. If Cisco distribution policy requires
those controls, stop before promotion and add/verify the real signing workflow first.

### P0 — live all-source reconciliation on the Cisco work machine

With VPN and approved credentials, run the same manager/technology/time scope through
every report family and reconcile:

- canonical customer IDs and aliases;
- subscription/customer totals;
- TAC/BEMS case totals and deduplication;
- AP, AB, Pulse, and Success Priority counts;
- external incident attribution;
- renewal dates/probabilities;
- exact CSC/BST correlations;
- risk components, bands, Word/XLSX/preview parity, and Ask AI answers.

Do not weaken a validator to make a live mismatch pass. Resolve the first divergent
source, scope, identity, or as-of input.

### P0 — temporal predictive backtest and calibration

Export only approved, de-identified historical snapshots with labels defined before
the forecast window. Run `scripts/backtest_escalation_forecast.py` with strict leakage
controls. Measure calibration, precision/recall, false-negative rate, stability by
source-coverage state, and performance by technology/customer cohort. Only publish a
calibration artifact if its provenance is `derived_from=live_cisco_sources`; only add
new numeric weights when the labeled evidence supports them.

### P0 — promote Build 113 after live smoke

Round 164 bumps `ADOPTIQ_BUILD` to 113 and adds a fail-closed Mac release preflight.
Before calling Build 113 a production release:

1. rebake the corpus from a clean approved fixture;
2. run release-gated Mac packaging and smoke;
3. run the live report iteration harness plus R114 audit and soak;
4. verify the Windows package later on an approved Windows host;
5. obtain Apple notarization and Windows Authenticode signatures if required;
6. update the merge-aware release manifest without losing either platform slot.

Build 112’s Mac DMG still contains the restored Build 111 corpus snapshot; treat a
fresh corpus bake as required for the next package.

### P1 — predictive evaluation expansion

- Add decision/predictive questions to the committed Ask AI replay corpus.
- Add labeled BST-status/version and renewal-outcome studies before considering new
  numeric weights.
- Test calibration drift and abstention behavior under source outages/staleness.
- Add automated visual viewport/screenshot checks at 390, 992/1050, and 1440 px.

## Useful focused commands

```bash
MPLCONFIGDIR=/tmp/adoptiq-mpl .venv/bin/python -m pytest tests -q --disable-warnings
make eval-ask-ai
make security
.venv/bin/ruff check .
.venv/bin/pip check
.venv/bin/python -m pip_audit --local --strict --progress-spinner off
```

For the complete sanitized acceptance, inspect the current CLI first:

```bash
.venv/bin/python scripts/run_round146_acceptance.py --help
```

Use a fresh output directory and the guarded fixture runtime. Never point the local
acceptance harness at live data or claim that its result validates production.

## Copy/paste kickoff for the next coding agent

```text
Continue AdoptIQ from Round 164 on main. Read NEXT_MACHINE_PROMPT.md and the Round 164
entry in QUALITY_AUDIT.md completely before editing.

The governing rule is criteria-scoped all-source intelligence: every applicable source
must contribute to identity/metrics/risk/forecast/narrative/action or disclose why it
cannot. Missing is never zero; unscopable is never widened; untagged portfolio events
are never smeared across customers. Word, Excel, preview, and Ask AI must reconcile to
the same canonical facts, lineage, evidence, as-of clock, and source coverage.

First run read-only checks and confirm main/remote ancestry. Then choose one bounded
objective: (1) live Cisco all-source reconciliation, (2) leakage-safe temporal
predictive backtest/calibration, or (3) next-build packaging/live smoke. Do not weaken
fail-closed validators, invent weights, commit secrets/live exports, or call sanitized
fixtures proof of 100% production accuracy.
```

**End of Round 164 next-machine handoff.**
