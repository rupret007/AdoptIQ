# Offline / Cloud / Bob simulation playbook (Round 169.1)

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

```bash
python3 -m pip install -r requirements.txt -c constraints-build113.txt
python3 -m pip install ruff bandit pip-audit pytest
make offline-sim            # full Cloud/Bob loop (any Mac or Cloud Agent)
make offline-sim-pr         # PR/CI subset (same honesty; skips A-G matrix)
```

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

`scripts/bake_corpus.py` and `scripts/mint_corpus_sentinel.py` seal the Ask AI
knowledge corpus. They cannot mint CSOne workbooks. Do not use them for this.

## How to mark a corpus blocker

`scripts/run_offline_bob_sim.sh` resolves a corpus in this order:

1. `CSONE_CORPUS_DIR` **outside** the repo, with `*.xlsx` → use it (`external_operator_dir`).
2. `CSONE_CORPUS_DIR` pointing at `testdata/synthetic_csone` → use it (`synthetic_checked_in`).
3. `CSONE_CORPUS_DIR` **inside** the repo at any other path → **refuse**
   (`blocked_in_repo_corpus`) so real CSOne cannot be committed.
4. Else checked-in `testdata/synthetic_csone/*.xlsx` → use it.
5. Else skip replay and production-simulation CSOne with a clear message
   (`skipped_no_corpus`). Exit 0 for the skip; do not invent a live pass.

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

`.github/workflows/offline-sim.yml` is valid YAML, lives on this branch, and
runs `make offline-sim-pr` after `scripts/check_offline_sim_ci_surface.py`.
The Makefile recipes `offline-sim` / `offline-sim-pr` exist. A missing
target is **not** the 2026-08-23 3-second failure mode.

That check run finished in ~3s with **empty steps** and **no `runner_name`**.
GitHub’s check-run annotation on `.github` was:

> The job was not started because recent account payments have failed or
> your spending limit needs to be increased.

That is a **hosted runner assignment** failure (the job never started).
The same empty-step 3s pattern hit another PR’s Quality Gate the same day.
The last hosted job on this repo that actually ran steps was the
`workflow_dispatch` Build on 2026-08-05.

Cloud/Bob proof is `make offline-sim-pr` / `make offline-sim-ci-surface` in
this environment. Re-run the Actions check after the account can assign an
`ubuntu-latest` runner. Do not treat the empty-step annotation as a sim
regression and do not invent `release_ready=true`.

## Safety

- Private repo. No secrets, tokens, customer rows, or raw CSOne in Git or PRs.
- Do not overwrite Cisco connection code wholesale.
- Reuse Round 145 fixtures, Ask AI cassettes, Round 143 offline decision
  reports, the Round 169 metamorphic SSoT, and this playbook. Do not invent
  a parallel stack.
- If a gate needs a real corpus and none is present, skip with
  `skipped_no_corpus` / `blocked_*` — fail closed and stay honest.
