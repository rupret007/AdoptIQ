# Offline / Cloud / Bob simulation playbook (Round 169.5)
<!-- Round 169.5 -->

AdoptIQ can be improved from Cursor Cloud or any machine that is **not**
Jeff Story’s work Mac. This page is the Cloud/Bob command card.
Work-Mac Cursor reads `WORK_MAC_CURSOR_HANDOFF.md` after this loop is green.

It does **not** authorize live Cisco accuracy claims.

Honesty stamps that must stay false here:

- `live_validation_performed=false`
- `production_accuracy_claimed=false`
- `release_ready=false`

Build 114 was never promoted. **Build 115 is invalidated / NO-GO.**
Source line is **v1.0.4 / Build 116 pending** — no candidate, hash, smoke,
or release approval exists. Do not bump `ADOPTIQ_BUILD` for tooling-only
work. Sim ≠ live accuracy. Never imply Build 116 or `release_ready=true`
from this loop.

## One entry

Cloud/Bob has **one local-proof command**. The other two are larger
profiles of the same honesty contract — not a second simulator.

```bash
python3 -m pip install -r requirements.txt -c constraints-build113.txt
python3 -m pip install ruff bandit pip-audit pytest
make offline-sim-local      # Cloud/Bob local-proof gate (no hosted runner)
make offline-sim-pr         # local-proof plus verify + lab + official R169
make offline-sim            # PR profile plus A-G production-simulation
```

`make offline-sim-local` prints a PASS / FAIL / SKIPPED / UNKNOWN
scorecard. Skipped gates (verify, official R169, lab, A–G) are **not**
passes. `ready_for_live_cisco` stays false. Sim ≠ live.

The scorecard also rejects malformed JSON evidence. Gate names must be
non-empty; `ran` and `ok` must be booleans, `exit_code` must be an integer,
and `status` must be a non-empty string when present. A non-zero exit remains
FAIL even if another field incorrectly says `ok=true`.

**Stay PRIVATE.** **AdoptIQ stays PRIVATE (Cisco — do not change visibility).**
**Stay draft.** Leave the PR **draft** for Bob/Karen even after
local/fixture proof is green. Local green is the Cloud/Bob completeness
gate, not an undraft signal. Do not undraft from local proof.
**Local/fixture proof is the gate.** Hosted Actions may stay red with
`runner_id=0` and empty steps — the job never started. That is runner
assignment, not a missing make target. PR CI lives on `build.yml`; `offline-sim.yml` is
dispatch-only. Diagnose workflow/runner/config. Do not blame GitHub
billing. Jeff has no spend-limit account type.

`make offline-sim` is the end-to-end command. It runs:

1. `make verify`
2. `make local-acceptance-lab` (23 fixture scenarios)
3. `make metamorphic-acceptance` — official Round 169 SSoT
   (`scripts/run_round169_metamorphic_acceptance.py`)
4. Fixture KPI metamorphic — complementary Round 168 row-order / alias checks
5. `make offline-pipeline-smoke` — ingest → canonical truth → Word/XLSX → UX
6. `make jeff-only-stubs` — live/DMG paths fail closed
7. Synthetic CSOne replay (`testdata/synthetic_csone/` or external dir)
8. `make production-simulation` when a corpus path exists (full profile)

## What Cloud/Bob can run (no work Mac)

```bash
make verify                 # lint + security + audit + pytest + eval-ask-ai
make local-acceptance-lab   # 23 guarded fixture scenarios
make metamorphic-acceptance # Round 169 SSoT (run_round169_metamorphic_acceptance.py)
make source-contracts       # real fetchers vs fixture Snowflake DB-API
make offline-pipeline-smoke # decision reports + 17-sheet XLSX + manager UX
make jeff-only-stubs        # fail-closed live / work-machine stubs
make eval-ask-ai            # already inside make verify
make offline-sim-local      # local/fixture proof when a hosted job never starts
make offline-sim-pr
make offline-sim
```

Optional diagnostics:

```bash
bash scripts/run_offline_bob_sim.sh --resolve-only
make synthetic-csone        # regenerate testdata/synthetic_csone from Round 145 fixtures
make local-acceptance-http  # 23 Flask boots; also inside production-simulation
```

## Pipeline coverage (fixtures only)

| Layer | Gate | Live Cisco? |
| --- | --- | --- |
| Ingest / CSOne loader | synthetic replay + `load_csone_excel` | no |
| Snowflake fetchers | `make source-contracts` | no (fixture DB-API) |
| Canonical counts | lab + metamorphic + `canonical_metrics` | no |
| Word + 17-sheet XLSX | offline decision-report acceptance | no |
| Manager UX pages | pipeline smoke Flask client | no |
| Ask AI | `make eval-ask-ai` cassettes | no |
| A–G report matrix | `make production-simulation` (full) | no |
| Keeper / live Snowflake | **needs work Mac** | yes |
| Real CSOne folder | **needs work Mac** (`CSONE_CORPUS_DIR` external) | yes |
| Live CircuIT | **needs work Mac** | yes |
| Package / promote / OneDrive | **needs work Mac** | yes |

## Fixture-only vs corpus

| Gate | Needs | Corpus? |
| --- | --- | --- |
| `make verify` / `eval-ask-ai` | committed tests + Ask AI cassettes | no |
| `make local-acceptance-lab` / `http` | Round 145 fixtures | no |
| `make metamorphic-acceptance` | Round 145 fixtures | no |
| `make source-contracts` / pipeline smoke | Round 145 + Round 142 fixtures | no |
| `make csone-corpus-replay` | `*.xlsx` via `CSONE_CORPUS_DIR` or checked-in synthetic | yes |
| `make production-simulation` | fixtures always; CSOne replay only if a corpus path exists | optional |

**`CSONE_CORPUS_DIR`** is an **external** directory of real CSOne exports on
Jeff’s Mac. Never copy those workbooks into Git or a PR body.

**`testdata/synthetic_csone/`** is the only in-repo corpus. It is projected
from Round 145 sanitized TAC rows (Acme / Beta / Gamma,
`@example.invalid`). It exercises `load_csone_excel` + privacy-preserving
replay. It is **not** live CSOne.

`team_config.json` and `customer_aliases.defaults.json` carry real Cisco
roster and alias strings **by design** (SSoT). Do not copy those strings
into new fixtures, synthetic CSOne, or PR text. New fixtures stay on
Acme / Beta / Gamma and `@example.invalid`.

`scripts/bake_corpus.py` and `scripts/mint_corpus_sentinel.py` seal the Ask AI
knowledge corpus. They cannot mint CSOne workbooks. Do not use them for this.

## How to mark a corpus blocker

`scripts/run_offline_bob_sim.sh` resolves a corpus in this order:

1. `CSONE_CORPUS_DIR` **outside** the repo, with `*.xlsx` → use it (`external_operator_dir`).
2. `CSONE_CORPUS_DIR` pointing at `testdata/synthetic_csone` → use it (`synthetic_checked_in`).
3. `CSONE_CORPUS_DIR` **inside** the repo at any other path → **FAIL**
   (`blocked_in_repo_corpus`) so real CSOne cannot be committed. This is
   fail-closed, not a skip-pass.
4. `CSONE_CORPUS_DIR` set but not a directory of `*.xlsx` → **FAIL**
   (`blocked_missing_external`).
5. Else checked-in `testdata/synthetic_csone/*.xlsx` → use it.
6. Else skip replay and production-simulation CSOne with a clear message
   (`skipped_no_corpus`). Exit 0 for that skip only; do not invent a live
   pass. Blocked kinds never use this skip.

Summaries stay under `.adoptiq-acceptance/` (gitignored).

## Jeff-only (work Mac / Cisco)

Do not pretend Cloud can close these. See `WORK_MAC_CURSOR_HANDOFF.md`.

1. Live Snowflake / Keeper / CircuIT / CSOne / OneDrive reconciliation.
2. Packaged Build 116 smoke, visual review, promote, `latest.json`
   (no candidate exists yet — see `WORK_MACHINE_BUILD116_PROMPT.md`).
3. OneDrive publication of a DMG/EXE.
4. Any claim of production accuracy or `release_ready=true`.
5. Never promote Build 114 or invalidated Build 115.

`make jeff-only-stubs` proves the live decision-report mode and the
work-machine DMG profile fail closed when those inputs are absent.

## Ranked improvement backlog

**P0 — this PR (offline, Cloud-safe)**

- One Cloud entrypoint (`make offline-sim` / `offline-sim-pr`).
- Honest corpus blocker; no real CSOne in Git.
- Synthetic CSOne projected from Round 145 fixtures.
- Fixture metamorphic gate.
- PR CI (PRs previously had no workflow).
- Fixture pipeline smoke (ingest → reports/workbook → manager UX).
- Fail-closed Jeff-only stubs + work-Mac Cursor handoff.

**P0 — Jeff / live only**

- Live Cisco reconciliation against authorized scopes.
- Build 116 package / smoke / promote / OneDrive (work-Mac only).
- Keep Build 114 and invalidated Build 115 unpublished.

**P1 — this PR (Cloud-safe)**

- Round 51 `select_latest_baseline` ignored only `__data-loop-<id>__`.
  Short dumps named `__data-loop-current.docx` could win on coarse-mtime
  filesystems. Ignore `__data-loop-<id>` plus a name tie-break.

**P1 — later, not this PR**

- Period-over-period / trend (orphaned helper surface in `leader_report_components.py`).
- Worker refactor only after a live baseline.
- Leader / Renewal / Subscription live freshness clocks.
- Live NYU / customer-alias confirmation on real DSM rows.

**P2**

- Cosmetic ruff `UP` modernizations.
- `locals()` dead-code sweep.
- Live CircuIT Ask AI eval (cassettes stay the offline floor).

## Hosted GitHub Actions vs local proof

**Stay draft.** Leave the PR **draft** for Bob/Karen even after
local/fixture proof is green. Do not undraft from local proof.
**Local/fixture proof is the gate.** Hosted `make offline-sim-pr` on
`build.yml` may stay red with `runner_id=0` / empty steps — the job
never started. That does not block Cloud/Bob. PR CI lives in `.github/workflows/build.yml` (last
runner-assigned workflow: Quality Checks, 2026-08-04). `offline-sim.yml`
is dispatch-only. Packaging jobs stay `workflow_dispatch` only.

The Makefile recipes exist. A missing target is **not** the 2026-08-23
~2s failure mode.

Latest empirical check: Actions run `32625073807` job `97159040140`
(commit `508308a`, 2026-08-23). **`runner_id=0`**, **empty steps**,
**no `runner_name`**, ~2s, conclusion failure. Classifier:
`kind=hosted_runner_not_assigned`, `reason=job_never_started`,
`is_billing_diagnosis=false`. Packaging/quality jobs skipped as designed.
The sibling PR Quality Gate showed the same never-started pattern the
same day. Diagnose workflow/runner/config. Do not blame GitHub billing.
Jeff has no spend-limit account type.

**Stay PRIVATE.** **AdoptIQ stays PRIVATE (Cisco — do not change visibility).**
Do not run `gh repo edit --visibility public`.

`scripts/classify_hosted_actions_failure.py` maps empty-runner / `runner_id=0`
to `hosted_runner_not_assigned` / `job_never_started` and never to
`missing_make_target`. `is_billing_diagnosis` stays false. Fixture:
`testdata/hosted_actions/empty_runner_billing.json`.

Cloud/Bob proof is **local/fixture**:

```bash
make offline-sim-local      # fast in-repo proof, no hosted runner
make hosted-actions-classify
make synthetic-csone-metrics
make offline-sim-ci-surface
make verify
```

Do not treat a never-started hosted job as a sim regression and do not invent
`release_ready=true`.

## Safety

- Private repo. No secrets, tokens, customer rows, or raw CSOne in Git or PRs.
- Do not overwrite Cisco connection code wholesale.
- Reuse Round 145 fixtures, Ask AI cassettes, Round 143 offline decision
  reports, the Round 169 metamorphic SSoT, and this playbook. Do not invent
  a parallel stack.
- If a gate needs a real corpus and none is present, skip with
  `skipped_no_corpus` / `blocked_*` — fail closed and stay honest.
