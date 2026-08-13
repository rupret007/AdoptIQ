# Round 144 work-machine live-acceptance and deployment runbook

This runbook starts from the exact Round 143 decision-report implementation,
validates it as a separate work-machine candidate, and deploys it without
overwriting the currently installed application until every release gate is
green. It does not authorize merging `main`, force-pushing, uploading reports,
or committing customer data.

## Acceptance scope and priorities

The manager's comments were primarily about the **Leader report** shown in the
original screenshots. Treat Leader as the principal usability target, but do
not exempt any other report or AI feature from this release:

- Evaluate Leader at team, individual member, and individual customer scope.
- Evaluate Comprehensive, Compact, Renewal portfolio, Renewal single-customer,
  and subscription analysis where configured.
- Keep the Leader report detailed but not overwhelming: decision summary,
  metrics, charts, account exceptions, risks, and tracked Action Plans in Word;
  complete supporting records in the paired Source Data workbook.
- Apply the same hierarchy and raw-record separation to Comprehensive only
  where it improves that report's broader analytical purpose. Do not force all
  report products into one identical shape.
- Evaluate the entire AI-assisted surface: portfolio Ask AI (synchronous and
  streaming), multi-turn conversation, suggestions, citations/evidence
  drill-through, support-case search, Ask Intel, Customer 360, Playbook, corpus
  retrieval/status, report narratives and recommendations, BE-priority LLM
  classification, model preferences/provider connectivity, diagnostics, and
  honest degraded/failure behavior.

"All reports" means every product family and supported scope above. "All AI
features" means every user-visible generative or corpus-assisted feature above,
not merely a provider ping or one successful prompt.

## Release decision

Do not replace the deployed app unless all of these are true:

1. The exact handoff branch and commit are checked out on a new local branch.
2. The offline four-scope acceptance command passes twice with byte-identical
   output, and the Ask AI replay evaluation passes.
3. The live four-scope acceptance command passes twice with stable semantic
   sheet hashes and no unexplained degraded required source.
4. Every generated Word page and all 17 sheets in each representative workbook
   have been rendered and reviewed. The exact inventory includes
   `Evidence_Links` and `Defect_Correlations`; use
   `decision_report_delivery.SOURCE_DATA_SHEET_NAMES` as the SSoT.
5. Every report product and AI surface in the acceptance inventory is exercised
   against the candidate, with quantitative and record-level claims reconciled.
6. Accessibility, formula, fail-closed, build, and isolated smoke checks pass.
7. The old deployed build has a tested, documented rollback path.

The valid pre-live claim is rollout readiness and deterministic fixture
consistency. A production-accuracy claim requires the live steps below.

## 1. Preserve the current work machine

Close any report generation in progress. Record the deployed version, build,
installation path, local app port, and current Git commit if the deployed copy
came from a repository.

From the existing AdoptIQ checkout:

```powershell
git status --short --branch
git rev-parse HEAD
git diff --binary > ..\AdoptIQ-before-round144-working.patch
git diff --binary --staged > ..\AdoptIQ-before-round144-staged.patch
git ls-files --others --exclude-standard > ..\AdoptIQ-before-round144-untracked.txt
```

Review the two patch files and the untracked-file list before continuing. Do
not put reports, customer exports, logs, databases, `.env` files, credentials,
or CSOne workbooks into a Git commit, patch shared outside the work machine, or
stash. Copy any non-Git runtime data needed for rollback to an access-controlled
backup directory instead. Preserve legitimate source edits with a local commit
on the existing branch or a separately reviewed code-only patch.

If `git status --short` is not clean after that preservation step, stop. Do not
reset, clean, or checkout over the changes.

## 2. Fetch and verify the exact release candidate

Use the configured credential helper or authenticated GitHub CLI. Never paste
a personal access token into the command, a remote URL, a file, or a prompt.

Fetch the published Round 144 preparation branch once, resolve its tip to an
immutable local SHA before running anything, and prove that it descends from
the exact Round 143 source commit. Record the resolved SHA in the acceptance
handoff; all validation must stay on that SHA even if the remote branch moves:

```powershell
$branch = "codex/round-144-work-machine-live-acceptance"
$sourceSha = "515d6d5e5739f477dd7acb35472108d41c586a5d"
git fetch origin --prune
$candidateSha = (git rev-parse "origin/$branch").Trim()
Write-Host "Pinned Round 144 candidate: $candidateSha"
git merge-base --is-ancestor $sourceSha $candidateSha
if ($LASTEXITCODE -ne 0) { throw "Round 143 source is not an ancestor" }
git switch -c "round144-work-machine-validation" $candidateSha
if ((git rev-parse HEAD).Trim() -ne $candidateSha) { throw "Checkout SHA mismatch" }
git status --short --branch
```

Never force-push, rewrite the branch, merge `main`, or reuse the deployed app's
working directory for an unreviewed pull.

## 3. Install dependencies in an isolated environment

Use the work machine's approved Python version and a new virtual environment.
Do not upgrade the currently deployed app's environment in place.

```powershell
py -3.12 -m venv .venv-round144
.\.venv-round144\Scripts\python.exe -m pip install --upgrade pip
.\.venv-round144\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-round144\Scripts\python.exe -m pip_audit -r requirements.txt --strict
```

Run the local quality gate before contacting live systems:

```powershell
$env:PY = ".\.venv-round144\Scripts\python.exe"
make verify
```

If `make` is unavailable, run its four commands directly from `Makefile` with
the isolated Python interpreter. Do not skip lint, Bandit, dependency audit, or
the full non-eval pytest suite.

## 4. Run deterministic offline acceptance first

The command requires an explicit manager, analysis window, and acceptance
clock. Offline artifacts are sanitized fixtures relabeled with this operator
context and remain clearly marked `partial`; they are not the manager's live
portfolio.

```powershell
$manager = "Brian Frazier"
$days = 90
$asOf = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$acceptanceRoot = Join-Path $env:TEMP "adoptiq-round144-acceptance"
.\.venv-round144\Scripts\python.exe scripts\run_decision_report_acceptance.py `
  --mode offline `
  --manager $manager `
  --days $days `
  --as-of $asOf `
  --output-dir $acceptanceRoot
```

Open `decision_report_acceptance_summary.json`. Require `all_passed: true`, two
passes, four passing scopes per pass, and `repeatability.ok: true`. Each offline
scope must report byte-identical Word and workbook hashes, matching fact and
per-sheet fingerprints, exactly 17 sheets, four accessible charts, zero
formulas, and honest partial source states.

## 5. Run portable AI acceptance

The default suite deliberately excludes the replay evaluation. Run it
explicitly before any live provider or Snowflake work:

```powershell
$env:PY = ".\.venv-round144\Scripts\python.exe"
make eval-ask-ai
```

Require all six runner gates and all 75 replayed questions to pass. The replay
suite proves deterministic citation, grounding, and answer-shape behavior; it
does not prove live Snowflake retrieval, current corpus recall, or provider
quality. Those remain live gates below.

## 6. Start the candidate app separately

Leave the currently deployed app installed and stopped or running on its
normal port. Start the source candidate or newly built candidate on a different
loopback port and a separate runtime/output directory. Do not point acceptance
at the production port until the process identity and `/api/version` response
prove it is the Round 144 candidate built from the required Round 143 commit.

Example development launch:

```powershell
$env:PORT = "5153"
.\.venv-round144\Scripts\python.exe app_simple.py
```

In a second terminal:

```powershell
Invoke-WebRequest http://127.0.0.1:5153/ping
Invoke-RestMethod http://127.0.0.1:5153/api/version | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:5153/api/diag/connectivity | ConvertTo-Json -Depth 12
```

The connectivity endpoint must succeed on VPN. The acceptance runner then
checks CSConsole and CSOne through the canonical source states in every
generated workbook; it never treats their absence as a true zero.

## 7. Run live four-scope acceptance

Choose a current roster member and an unambiguous authorized customer. Supply
the latest reviewed CSOne `.xlsx` export explicitly unless the candidate's
configured OneDrive folder is known to be fully synced. The runner uploads the
same file independently to each report job, generates Team, Member, Customer,
and Comprehensive twice, and downloads every pair.

```powershell
$asOf = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
.\.venv-round144\Scripts\python.exe scripts\run_decision_report_acceptance.py `
  --mode live `
  --base-url http://127.0.0.1:5153 `
  --manager $manager `
  --member-email "<CURRENT_MEMBER_EMAIL>" `
  --customer-name "<UNAMBIGUOUS_CUSTOMER_NAME>" `
  --days $days `
  --as-of $asOf `
  --csone-file "<PATH_TO_REVIEWED_CSONE_XLSX>" `
  --output-dir $acceptanceRoot
```

If a known live customer shares an account identifier with another customer,
add `--ambiguous-customer-name "<KNOWN_AMBIGUOUS_NAME>"`; the request must fail
closed. An outside-manager member probe always runs. Do not invent an ambiguous
name merely to satisfy the option—record the deterministic local ambiguity
probe and the absence of a known live candidate.

Require:

- `mode_executed: live` and `live_validation_performed: true`;
- all four scopes pass in both passes;
- server `delivery_contract` and `source_data_contract` pass;
- `Report_Info` manager, scope, window, actual source snapshot time, fact hash,
  per-sheet hashes, states, and counts reconcile to status and Word;
- required sources are `available`, `zero`, or deliberately `filtered`, never
  `partial`, `stale`, `failed`, or `unavailable` without a release stop;
- Action Plan buckets are mutually exclusive and reconcile to rows, charts,
  and lineage;
- assigned TAC rows retain stable account/subscription association, unmatched
  rows are explicitly quarantined, and BEMS IDs are a subset of TAC IDs;
- both live passes have identical per-sheet semantic hashes and normalized
  metrics when source records did not change. Investigate any drift; do not
  dismiss it as formatting noise.

The explicit command clock is the acceptance-run reference. Each live report
uses and records its real prefetch `Data_As_Of_UTC`; status and workbook must
match exactly. This avoids pretending a historical input clock controlled a
live Snowflake query when it did not.

## 8. Render, inspect, and audit the artifacts

Use the work agent's document and spreadsheet rendering workflows. Render
every page of the four representative Word files and every sheet of the four
representative Source Data workbooks (60 sheets). Inspect at 100% zoom for
clipping, split rows, missing headings, illegible chart labels, blank charts,
bad page breaks, truncated hashes/JSON, missing filters, and inaccessible chart
descriptions.

For each pair, run the fail-closed audit:

```powershell
.\.venv-round144\Scripts\python.exe scripts\r114_audit_reports.py `
  --target "run=<WORD_PATH_WITHOUT_DOCX_SUFFIX>"
```

Require no critical finding. The acceptance summary must also show zero
formula cells/errors, frozen headers, filters, readable dimensions, and chart
alternative text. Record the render directories and reviewer sign-off outside
Git if they contain live customer information.

## 9. Evaluate every report product and option family

The four-scope runner proves Leader team/member/customer and Comprehensive. It
does not by itself evaluate Compact, Renewal, every manager/technology option,
or edge cases. Run the complete live matrix against the same candidate and
window; do not reduce this to only Compact/Renewal blocks:

```powershell
.\.venv-round144\Scripts\python.exe scripts\run_report_option_matrix.py `
  --base-url http://127.0.0.1:5153 `
  --downloads-dir "$acceptanceRoot\all-report-products" `
  --days $days `
  --blocks A,B,C,D,E,F,G `
  --strict `
  --baseline-mode off `
  --stop-on-failure
```

Block G inputs that require a subscription, single customer, or reviewed CSOne
file must be supplied when the work-machine data provides an authorized
candidate. Record an explicit not-applicable reason only when the option truly
cannot exist for the validated portfolio. Do not proceed if any report's
canonical KPI, risk, artifact, source-state, or fail-closed audit checks regress.

For every product, separately assess whether the Word output is detailed but
not overwhelming. Record page count, visible word count, repeated/raw-record
sections, chart count, decision/action content, and the paired source-record
location. Leader is the primary redesign target, but the evidence table must
contain a row for every report product.

## 10. Validate every AI-assisted feature live

First verify current corpus, intelligence, model, and provider state without
changing the operator's saved choices:

- `/api/corpus/status` reports whether the encrypted corpus is active, warming,
  degraded, or unavailable, including retrieval method and embedding/reranker
  state.
- `/api/intel/status` is the user-facing alias for the same encrypted-corpus
  status; do not mislabel it as external incident/bug state.
- `/external-intelligence` and grounded Ask Intel exercise the stored external
  incident, maintenance, bug, and portfolio-correlation surface.
- The Ask AI and report model-setting GET endpoints resolve the intended saved
  or configured models independently.
- `/api/llm/ping` succeeds for each active model. A ping alone is not feature
  acceptance.

Run the two-pass live harness. It writes a hashed/redacted summary plus a
permission-restricted sensitive evidence file outside Git. Both artifacts are
marked `do_not_commit`; the summary is suitable for an access-controlled
release decision but is not a repository artifact:

```powershell
$env:PY = ".\.venv-round144\Scripts\python.exe"
$env:MANAGER = $manager
$env:CUSTOMER_NAME = "<UNAMBIGUOUS_CUSTOMER_NAME>"
$env:TECHNOLOGY = "All"
$env:DAYS = $days
$env:BASE_URL = "http://127.0.0.1:5153"
$env:OUTPUT_DIR = "$acceptanceRoot\ai-features"
make ai-feature-acceptance
```

If `make` is unavailable, run `scripts\run_ai_feature_acceptance.py` directly
with the corresponding command-line arguments. Require
`all_automated_checks_passed: true` and `repeatability.ok: true`; never treat
the runner's automated result as source-record sign-off. The sensitive
evidence file is required for the claim-by-claim review below. Neither output
may be committed; the sensitive file must also never be uploaded or copied off
the work machine.

The harness exercises and retains evidence for all of the following:

1. Portfolio Ask AI through both synchronous and streaming paths.
2. A follow-up question with conversation history enabled.
3. Personalized suggestion chips and follow-up suggestions.
4. Evidence lookup from at least one returned citation to its exact source row.
5. Customer-name drill-through from an answer to Customer 360.
6. A support-case narrative/ID search that requires case-body retrieval.
7. An Action Plan question requiring owner, status, date, and stable ID.
8. A customer-risk question and a cross-customer comparison.
9. A deliberately unanswerable question, which must disclose missing evidence
   instead of guessing.
10. Ask Intel with incident/maintenance/bug evidence and an insufficient-data
    case.
11. Customer 360 history and the corpus-backed Playbook search.
12. Corpus status/diagnostics and a safe refresh only if the current status says
    refresh is required; never reset a healthy corpus for acceptance.
13. AI narrative/recommendation content in Comprehensive, Compact, and Renewal.
14. BE-priority LLM classification in Leader and Comprehensive when enabled;
    deterministic priority ranking must remain authoritative if classification
    is disabled or unavailable.

For each AI answer or report narrative:

- reconcile every number, customer, ID, date, owner, status, and risk claim to
  the cited Snowflake, corpus, intelligence, or Source Data record;
- verify scope and date filters, actual retrieval timestamp, source coverage,
  sampling/truncation disclosure, and absence of cross-scope leakage;
- open citations/evidence and confirm the source ID was actually available to
  the model;
- reject fabricated IDs, unsupported percentages, silent legacy/ungrounded
  fallback, raw provider errors, credentials, prompt-injection obedience, and
  conclusions built from unavailable data;
- confirm provider failure or partial retrieval produces an honest degraded
  state and does not convert missing data to zero or a positive conclusion.

Repeat the same fixed question set twice against an unchanged source snapshot.
Equivalent facts, IDs, and conclusions are required; prose need not be
byte-identical. Any unsupported material claim is a release blocker.

## 11. Build and smoke separately

Build only after offline and live acceptance pass. Follow the platform's
existing release-gated build procedure; do not overwrite the deployed binary.
Place the candidate in a versioned staging directory, verify its version/build
metadata and signature, then start it on a separate port/runtime directory.

Smoke at minimum:

- `/ping`, `/api/version`, `/api/status/all`, `/api/corpus/status`, and
  `/api/intel/status`;
- Team/Member/Customer selectors and one paired artifact download;
- every report product can be started and downloaded;
- Ask AI sync/stream, evidence drawer, Ask Intel, Customer 360, and Playbook;
- Previous Reports grouping of `AdoptIQ_Report_*` with
  `AdoptIQ_Source_Data_*`;
- no write outside the candidate output/runtime directories;
- the same live acceptance summary remains green against the built candidate.

## 12. Backup, deploy, and rollback

Before replacement, copy the deployed application bundle/executable, its
version metadata, configuration, and required runtime data to a dated,
access-controlled backup location. Do not copy credentials into Git. Record the
old and new hashes and installation paths.

Stop the old process, install the accepted candidate, start it on the normal
port, and repeat the smoke endpoints. If startup, connectivity, report
generation, download, or artifact validation fails:

1. Stop the candidate.
2. Restore the prior application from the dated backup.
3. Restore only the prior compatible runtime/config files; do not overwrite
   newer customer exports without explicit operator review.
4. Restart the prior version and verify `/api/version` plus `/ping`.
5. Preserve the failed candidate logs and redacted summary outside Git for
   diagnosis.

## 13. Final repository hygiene

Before any code-only follow-up commit:

```powershell
git status --short --branch
git diff --check
git diff --name-only
git ls-files --others --exclude-standard
```

Confirm there are no generated DOCX/XLSX/PDF/PNG files, acceptance summaries,
customer names/IDs, CSOne exports, credentials, tokens, databases, logs, build
artifacts, or unrelated edits in the staged set. Stage exact reviewed source
paths only; never use `git add -A` or `git add .` for a live-acceptance tree.

## 14. Record the decision and push the validated branch

The final handoff must contain redacted, requirement-by-requirement evidence:

- starting SHA, ending SHA, branch, candidate version/build, and deployed path;
- the manager-feedback disposition for Leader and Comprehensive;
- one acceptance row for every report product and supported scope;
- one acceptance row for every AI feature listed in section 10;
- two-pass report reconciliation and two-pass AI question-set results;
- automated, accessibility, visual, R114, build, and deployed-smoke results;
- known data limitations and honest not-applicable cases;
- prior deployment backup path/hash, new deployment hash, and exact tested
  rollback steps.

Keep customer-level evidence, downloaded artifacts, screenshots, and source
records in the approved work-machine evidence location, not Git. The committed
handoff may contain only redacted counts, pass/fail status, stable test names,
build identifiers, and non-sensitive paths or hashes.

After reviewing the exact staged diff, commit legitimate work-machine fixes,
tests, redacted acceptance results, and non-sensitive deployment guidance.
Push the validation branch for review; do not rewrite the published preparation
commit:

```powershell
git diff --cached --check
git diff --cached --stat
git commit -m "validate Round 144 work-machine reports and AI features"
git push -u origin round144-work-machine-validation
```

Do not push if live Snowflake reconciliation, all-report evaluation, full AI
acceptance, deployment verification, or rollback proof remains incomplete.
