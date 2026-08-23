# Work-Mac Cursor handoff (Round 169.3)

This is the copy-ready prompt for **Jeff’s work-Mac Cursor** after Cloud/Bob
finishes the offline sim. It does **not** authorize live accuracy claims,
packaging, or promotion by itself.

Honesty stamps that stay false until the live gates below pass:

- `live_validation_performed=false`
- `production_accuracy_claimed=false`
- `release_ready=false`

Source line: **v1.0.4 / Build 116 pending**. Build 114 was never promoted.
**Build 115 is invalidated / NO-GO** — do not install, stage, or publish it.
No Build 116 candidate identity, hash, smoke, or release approval exists yet.
Do not bump `ADOPTIQ_BUILD` for tooling-only merges. Sim ≠ production ready.
Never imply Build 116 or `release_ready=true` from Cloud/Bob alone.

Authoritative packaging runbook remains `NEXT_MACHINE_PROMPT.md`.
Copy-ready work-Mac prompt: `WORK_MACHINE_BUILD116_PROMPT.md`.
Cloud command card remains `OFFLINE_SIM_PLAYBOOK.md`.

## What Cloud/Bob already proved (offline / fixture)

After `make offline-sim` (or `make offline-sim-pr` on the PR):

1. `make verify` — ruff, bandit HIGH/MED, pip-audit, full pytest, Ask AI cassettes.
2. Round 145 lab — 23 guarded fixture scenarios, canonical-count reconcile.
3. Round 169 metamorphic SSoT — artifact invariance, duplicate quarantine,
   invalid-ID block, identity quarantine, freshness, scope isolation, Ask AI
   origin transport (`scripts/run_round169_metamorphic_acceptance.py`).
4. Complementary Round 168 fixture KPI metamorphic — row-order / rebuild /
   NYU alias / join-key honesty.
5. Round 169 pipeline smoke:
   - Snowflake **fetchers** against the fixture DB-API simulator (no Keeper).
   - Offline decision reports: Team / Member / Customer / Comprehensive × 2.
   - Exact 17-sheet Source Data inventory.
   - Manager UX pages (`/`, `/help`, `/preferences`, `/ask-ai`, history, leader form, `/api/version`).
6. Synthetic CSOne replay from `testdata/synthetic_csone/` (`@example.invalid`,
   Acme / Beta / Gamma). Not live CSOne.
7. Jeff-only stubs — live decision-report mode and the work-machine DMG profile
   fail closed when Cisco/DMG are absent.

`full` profile also runs `make production-simulation` (A–G matrix, Ask AI
replay, degraded HTTP) against the synthetic corpus. Still
`live_validation_performed=false`.

Sim **≠** production ready. Do not imply Build 116 or `release_ready=true`.

## Still Jeff-only (work Mac / VPN / Cisco)

| Gap | Why Cloud cannot close it |
| --- | --- |
| Live Snowflake / Keeper / CSConsole | Needs the authorized work-Mac secret store |
| Real CSOne folder | External `CSONE_CORPUS_DIR` only; never commit workbooks |
| Live CircuIT | Cassettes are the offline floor |
| Packaged `.app` / EXE smoke | No Build 116 candidate yet; follow `WORK_MACHINE_BUILD116_PROMPT.md` |
| Visual review + promote + OneDrive | Manual + `latest.json` |
| Build 114 / Build 115 | Stay invalidated |

## Exact next commands on the work Mac

Preserve the existing Cisco remote, Keeper/Snowflake/CircuIT/OneDrive config,
and ignored runtime state. Do not stash, reset, or print secrets.

```bash
set -euo pipefail
cd /path/to/AdoptIQ
git fetch origin main
git checkout main
git pull --ff-only origin main
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints-build113.txt
make verify PY=.venv/bin/python
```

Optional: re-run the Cloud loop on this Mac (still not live accuracy):

```bash
make offline-sim PY=.venv/bin/python
```

Then the **live** gates from `NEXT_MACHINE_PROMPT.md` and
`WORK_MACHINE_BUILD116_PROMPT.md`:

```bash
# External real CSOne folder — never copy into Git
make production-simulation \
  PY=.venv/bin/python \
  CSONE_CORPUS_DIR='/approved/external/AdoptIQ_CSOne_Reports' \
  OUTPUT_DIR='.adoptiq-acceptance/prebuild-production-simulation'

# VPN + existing Keeper connection
.venv/bin/python scripts/profile_snowflake_capabilities.py

# Live decision reports against the running app (fail closed; no fixture fallback)
.venv/bin/python scripts/run_decision_report_acceptance.py \
  --mode live \
  --manager '<authorized manager>' \
  --days 90 \
  --as-of '<prefetch clock UTC>' \
  --output-dir '.adoptiq-acceptance/live-decision-reports'
```

Package and promote **only** after a fresh Build 116 candidate contract exists.
Do not invent a hash from chat. Do not promote Build 114 or invalidated Build 115.

## Hosted Actions note

**Stay draft.** Keep the PR **draft** until hosted `make offline-sim-pr`
actually starts (non-zero `runner_id`, real steps).

PR CI for `make offline-sim-pr` lives in `.github/workflows/build.yml`
(the last workflow that received a GitHub-hosted runner: Quality Checks,
2026-08-04). `offline-sim.yml` is dispatch-only so it cannot create
empty pull_request checks. Packaging jobs stay `workflow_dispatch` only.

If a hosted job finishes in ~2s with `runner_id=0` and empty steps, it
never started. That is **not** a missing `make offline-sim-pr` target.
Diagnose workflow/runner/config. Do not blame GitHub billing. Jeff has
no spend-limit account type. Classify with `make hosted-actions-classify`.
Cloud/Bob proof is `make offline-sim-local` and `make verify`.

**Stay PRIVATE.** **AdoptIQ stays PRIVATE (Cisco — do not change visibility).**
Do not run `gh repo edit --visibility public`.

## What to tell Karen / reviewers

Ready for Karen to review as a **draft**. Do not undraft until hosted
`make offline-sim-pr` runs.

- Cloud PR is fixture-only. Summaries under `.adoptiq-acceptance/` are gitignored.
- No tokens, customer rows, or raw CSOne in the PR.
- A green offline sim is regression evidence, not Cisco production proof.
- Hosted empty-step failures mean the job never started — not a missing
  target and not a billing story.
