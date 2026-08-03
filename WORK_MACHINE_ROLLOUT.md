# Round 143 work-machine rollout runbook

This runbook moves the Round 142 decision-report redesign from a source branch
to a separately tested work-machine build without overwriting the currently
deployed application. It does not authorize merging `main`, force-pushing,
uploading reports, or committing customer data.

## Release decision

Do not replace the deployed app unless all of these are true:

1. The exact handoff branch and commit are checked out on a new local branch.
2. The offline four-scope acceptance command passes twice with byte-identical
   output.
3. The live four-scope acceptance command passes twice with stable semantic
   sheet hashes and no unexplained degraded required source.
4. Every generated Word page and all 15 sheets in each representative workbook
   have been rendered and reviewed.
5. Accessibility, formula, fail-closed, Compact, Renewal, build, and isolated
   smoke checks pass.
6. The old deployed build has a tested, documented rollback path.

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
git diff --binary > ..\AdoptIQ-before-round143-working.patch
git diff --binary --staged > ..\AdoptIQ-before-round143-staged.patch
git ls-files --others --exclude-standard > ..\AdoptIQ-before-round143-untracked.txt
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

Set `EXPECTED_SHA` to the commit in the final Round 143 handoff:

```powershell
$branch = "codex/round-143-decision-report-rollout"
$expectedSha = "<EXACT_SHA_FROM_HANDOFF>"
git fetch origin --prune
$remoteSha = (git rev-parse "origin/$branch").Trim()
if ($remoteSha -ne $expectedSha) { throw "Remote SHA mismatch: $remoteSha" }
git switch -c "work-machine/round-143-$($expectedSha.Substring(0,12))" "origin/$branch"
if ((git rev-parse HEAD).Trim() -ne $expectedSha) { throw "Checkout SHA mismatch" }
git status --short --branch
```

Never force-push, rewrite the branch, merge `main`, or reuse the deployed app's
working directory for an unreviewed pull.

## 3. Install dependencies in an isolated environment

Use the work machine's approved Python version and a new virtual environment.
Do not upgrade the currently deployed app's environment in place.

```powershell
py -3.12 -m venv .venv-round143
.\.venv-round143\Scripts\python.exe -m pip install --upgrade pip
.\.venv-round143\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-round143\Scripts\python.exe -m pip_audit -r requirements.txt --strict
```

Run the local quality gate before contacting live systems:

```powershell
$env:PY = ".\.venv-round143\Scripts\python.exe"
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
$acceptanceRoot = Join-Path $env:TEMP "adoptiq-round143-acceptance"
.\.venv-round143\Scripts\python.exe scripts\run_decision_report_acceptance.py `
  --mode offline `
  --manager $manager `
  --days $days `
  --as-of $asOf `
  --output-dir $acceptanceRoot
```

Open `decision_report_acceptance_summary.json`. Require `all_passed: true`, two
passes, four passing scopes per pass, and `repeatability.ok: true`. Each offline
scope must report byte-identical Word and workbook hashes, matching fact and
per-sheet fingerprints, exactly 15 sheets, four accessible charts, zero
formulas, and honest partial source states.

## 5. Start the candidate app separately

Leave the currently deployed app installed and stopped or running on its
normal port. Start the source candidate or newly built candidate on a different
loopback port and a separate runtime/output directory. Do not point acceptance
at the production port until the process identity and `/api/version` response
prove it is the Round 143 candidate.

Example development launch:

```powershell
$env:PORT = "5153"
.\.venv-round143\Scripts\python.exe app_simple.py
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

## 6. Run live four-scope acceptance

Choose a current roster member and an unambiguous authorized customer. Supply
the latest reviewed CSOne `.xlsx` export explicitly unless the candidate's
configured OneDrive folder is known to be fully synced. The runner uploads the
same file independently to each report job, generates Team, Member, Customer,
and Comprehensive twice, and downloads every pair.

```powershell
$asOf = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
.\.venv-round143\Scripts\python.exe scripts\run_decision_report_acceptance.py `
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

## 7. Render, inspect, and audit the artifacts

Use the work agent's document and spreadsheet rendering workflows. Render
every page of the four representative Word files and every sheet of the four
representative Source Data workbooks (60 sheets). Inspect at 100% zoom for
clipping, split rows, missing headings, illegible chart labels, blank charts,
bad page breaks, truncated hashes/JSON, missing filters, and inaccessible chart
descriptions.

For each pair, run the fail-closed audit:

```powershell
.\.venv-round143\Scripts\python.exe scripts\r114_audit_reports.py `
  --target "run=<WORD_PATH_WITHOUT_DOCX_SUFFIX>"
```

Require no critical finding. The acceptance summary must also show zero
formula cells/errors, frozen headers, filters, readable dimensions, and chart
alternative text. Record the render directories and reviewer sign-off outside
Git if they contain live customer information.

## 8. Run Compact and Renewal regressions

The decision-report change must not silently break the established Compact or
Renewal paths. Against the same candidate app and manager/window, run the
relevant live option-matrix blocks and R114 audits:

```powershell
.\.venv-round143\Scripts\python.exe scripts\run_report_option_matrix.py `
  --base-url http://127.0.0.1:5153 `
  --downloads-dir "$acceptanceRoot\legacy-regression" `
  --days $days `
  --blocks E,F `
  --strict `
  --baseline-mode off `
  --stop-on-failure
```

Do not proceed if Compact/Renewal canonical KPI, risk, artifact, source-state,
or fail-closed audit checks regress.

## 9. Build and smoke separately

Build only after offline and live acceptance pass. Follow the platform's
existing release-gated build procedure; do not overwrite the deployed binary.
Place the candidate in a versioned staging directory, verify its version/build
metadata and signature, then start it on a separate port/runtime directory.

Smoke at minimum:

- `/ping`, `/api/version`, `/api/status/all`, `/api/corpus/status`, and
  `/api/intel/status`;
- Team/Member/Customer selectors and one paired artifact download;
- Previous Reports grouping of `AdoptIQ_Report_*` with
  `AdoptIQ_Source_Data_*`;
- no write outside the candidate output/runtime directories;
- the same live acceptance summary remains green against the built candidate.

## 10. Backup, deploy, and rollback

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

## 11. Final repository hygiene

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
