# Round 146 portable acceptance

`scripts/run_round146_acceptance.py` is the no-Codex entry point for the
Manager Decision Workspace acceptance round. It composes the guarded local
lab, real Flask route probes, two-pass decision reports, the complete report
matrix, two-pass AI feature acceptance, workspace history/comparison checks,
and the fixed 75-question Ask AI replay.

The command has two profiles that cannot be silently interchanged:

- `local` explicitly starts the synthetic Round 145 fixture runtime on a
  random loopback port. Every result remains `fixture_validation_performed`;
  `live_validation_performed` is always false.
- `work-machine` requires an already-running loopback candidate and fails when
  its connectivity diagnostic is unavailable or identifies the fixture
  runtime. It never falls back to local fixtures.

Neither profile grants production accuracy or release readiness. The summary
always keeps `production_accuracy_claimed`, `release_ready`, visual review,
and manual source reconciliation false. Live claim-by-claim reconciliation,
render review, packaged-candidate verification, deployment, and rollback
remain separate operator gates.

## Environment

Use the repository's approved Python 3.11+ environment. Install dependencies
from `requirements.txt` in a new virtual environment; do not change the
currently deployed application's environment. The runner accepts no
credential, password, token, or GitHub-token argument. The application must
obtain its own approved credentials through its normal work-machine setup.

The only retained output by default is:

```text
<output-dir>/round146_acceptance_summary.json
```

That file contains allow-listed hashes, counts, source/mode states, and
pass/fail findings. Raw child output, downloaded acceptance copies, and AI
evidence are held in a temporary directory and removed after projection.
Each guarded fixture process also receives a disposable application-state
directory for status JSON, SQLite history, uploads, and logs; synthetic runs
therefore do not enter the checkout's or operator's normal report history.
The running application still writes each generated report to its configured
report-output folder as part of normal product behavior; the runner does not
delete or move those originals. On the guarded source runtime it requests a
temporary fixture-output folder. On a live or frozen candidate, handle the
configured report-output folder under the work machine's retention policy.
Repository-local summaries are allowed only under `.adoptiq-acceptance/`,
which is Git-ignored. Every summary is also marked `do_not_commit`.

## One-command local acceptance

From the repository root:

```bash
python3 scripts/run_round146_acceptance.py \
  --output-dir .adoptiq-acceptance/round146-local \
  local
```

This is intentionally a substantial run. It validates all 21 degraded and
edge fixture scenarios through real loopback HTTP, generates the deterministic
four-scope decision reports twice, runs matrix blocks A-G, runs the two-pass AI
surface, probes all Manager Decision Workspace report families and scopes, and
runs all 75 replay questions plus 25 canonical metric checks.

## Manager-feedback Leader gates

The Leader report is the primary manager-feedback gate. The controlled fixture
baseline is a four-page report after selecting the top five Action Plans;
render review must reconfirm the page count because the portable command keeps
`visual_review_complete` false. Automated acceptance requires:

- four populated, source-backed charts with accessible titles;
- no raw repeated activity or `No subject` dump in Word;
- a decision-ready top-five Action Plan table in Word with record ID, account,
  owner, status, due date and age, priority, and next action;
- all selected-scope activities, cases, Action Plans, and source rows only in
  the paired Source Data File, with Word limited to metrics and decisions;
- Team, Member, and Customer Leader deep dives with exact scope preservation;
- truthful available, zero, partial, stale, failed, and unavailable source
  states, without turning missing evidence into a zero or a conclusion.

Comprehensive, Compact, portfolio renewal, individual renewal, Subscription,
and every Ask AI/AI surface are required secondary breadth gates. They may not
weaken, widen, or replace the Leader-centered manager-feedback contract.

The `--skip-*` switches are diagnostic conveniences only. If any required gate
is skipped, `acceptance_complete` and `all_passed` remain false. Skipping never
turns a partial run into acceptance evidence.

## One-command work-machine acceptance

First start the candidate on a separate loopback port and verify that VPN and
Snowflake connectivity are available. Select a current authorized manager,
member, unambiguous customer, and subscription. Use the same reviewed CSOne
workbook when one is required by the portfolio.

PowerShell:

```powershell
$asOf = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$evidence = Join-Path $env:TEMP "adoptiq-round146-sanitized"
.\.venv-round146\Scripts\python.exe scripts\run_round146_acceptance.py `
  --output-dir $evidence `
  work-machine `
  --base-url http://127.0.0.1:5153 `
  --manager "<AUTHORIZED_MANAGER>" `
  --member-email "<AUTHORIZED_MEMBER_EMAIL>" `
  --customer-name "<UNAMBIGUOUS_CUSTOMER>" `
  --subscription-id "<AUTHORIZED_SUBSCRIPTION_ID>" `
  --technology "All" `
  --days 90 `
  --as-of $asOf `
  --csone-file "<REVIEWED_CSONE_XLSX>"
```

macOS/Linux shell:

```bash
python3 scripts/run_round146_acceptance.py \
  --output-dir "${TMPDIR:-/tmp}/adoptiq-round146-sanitized" \
  work-machine \
  --base-url http://127.0.0.1:5153 \
  --manager "<AUTHORIZED_MANAGER>" \
  --member-email "<AUTHORIZED_MEMBER_EMAIL>" \
  --customer-name "<UNAMBIGUOUS_CUSTOMER>" \
  --subscription-id "<AUTHORIZED_SUBSCRIPTION_ID>" \
  --technology "All" \
  --days 90 \
  --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --csone-file "<REVIEWED_CSONE_XLSX>"
```

If a known customer is ambiguous, add
`--ambiguous-customer-name "<KNOWN_AMBIGUOUS_CUSTOMER>"`; do not invent one.
If customer scope must be constrained through a particular authorized member,
add `--customer-member-email "<AUTHORIZED_MEMBER_EMAIL>"`.

The live profile runs, in order:

1. two-pass Team, Member, Customer, and Comprehensive report acceptance;
2. the complete A-G report-option matrix, including individual renewal and
   subscription analysis;
3. two-pass AI feature acceptance, including sync/stream delivery, follow-up
   context, evidence lookup, Ask Intel, Customer 360, and Playbook;
4. workspace previews for every report family and Leader scope, filterable
   history, a post-generation report projection, and comparison of two
   canonical snapshots. Older compatibility-projected workbooks remain
   viewable but are never accepted as comparison-grade evidence;
5. the offline 75-question replay and 25 canonical checks.

The runner targets only `127.0.0.1`, `localhost`, or `::1`. It rejects remote
URLs, URLs containing credentials, and URLs with paths, queries, or fragments.
An explicit live run fails closed if the connectivity preflight fails or if
the candidate identifies itself as the fixture runtime.

## Retaining sensitive evidence for manual review

The automated summary is not enough for live claim-by-claim review. When an
approved access-controlled directory is available outside the repository, add
the global option before the profile name:

```powershell
--retain-sensitive-dir "D:\Approved\AdoptIQ\Round146\Evidence"
```

This retains the acceptance runner's downloaded Word reports, Source Data
workbooks, matrix artifacts, and permission-restricted AI evidence file in a
new run-specific child directory. It does not relocate the originals written
by the candidate application. The retention root must resolve outside the
repository. Never point it at Git, OneDrive locations not approved for
customer data, a shared chat attachment directory, or a source checkout. The
sanitized summary stores only a digest of the run-specific directory path.

Review every retained Word page and workbook sheet, reconcile material claims
to the approved live records, and record the review outside Git. Remove or
archive the directory under the applicable data-retention policy when review
is complete.

## Result interpretation

Require all of the following before calling the automated run green:

- `acceptance_complete: true` and `all_passed: true`;
- no `skipped_gates`;
- all required gates have `ok: true`;
- local runs say `fixture_validation_performed: true` and
  `fixture_validation_passed: true`, with `live_validation_performed: false`;
- work-machine runs say `live_validation_performed: true` only after the live
  report, AI, and workspace gates all completed, and
  `live_validation_passed: true` only when every required gate passed;
- replay reports exactly 75/75 questions and 25/25 canonical checks;
- the report and AI gates each report two passing, repeatable passes;
- matrix blocks A-G completed without an aborted scenario.

Even then, `release_ready` remains false until visual, accessibility, source
reconciliation, frozen-build, installation, and rollback evidence are
independently completed.
