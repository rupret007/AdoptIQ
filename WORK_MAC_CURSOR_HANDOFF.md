# Work-Mac Cursor handoff (Round 169)

This is the copy-ready prompt for **Jeff’s work-Mac Cursor** after Cloud/Bob
finishes the offline sim. It does **not** authorize live accuracy claims,
packaging, or promotion by itself.

Honesty stamps that stay false until the live gates below pass:

- `live_validation_performed=false`
- `production_accuracy_claimed=false`
- `release_ready=false`

Product line: **v1.0.4 / Build 115**. Build 114 was never promoted — do not
install, stage, or publish it. Do not bump `ADOPTIQ_BUILD` for tooling-only
merges.

Authoritative packaging runbook remains `NEXT_MACHINE_PROMPT.md`.
Cloud command card remains `OFFLINE_SIM_PLAYBOOK.md`.

## What Cloud/Bob already proved (offline / fixture)

After `make offline-sim` (or `make offline-sim-pr` on the PR):

1. `make verify` — ruff, bandit HIGH/MED, pip-audit, full pytest, Ask AI cassettes.
2. Round 145 lab — 23 guarded fixture scenarios, canonical-count reconcile.
3. Round 168 metamorphic — row-order / rebuild / NYU alias / join-key honesty.
4. Round 169 pipeline smoke:
   - Snowflake **fetchers** against the fixture DB-API simulator (no Keeper).
   - Offline decision reports: Team / Member / Customer / Comprehensive × 2.
   - Exact 17-sheet Source Data inventory.
   - Manager UX pages (`/`, `/help`, `/preferences`, `/ask-ai`, history, leader form, `/api/version`).
5. Synthetic CSOne replay from `testdata/synthetic_csone/` (`@example.invalid`,
   Acme / Beta / Gamma). Not live CSOne.
6. Jeff-only stubs — live decision-report mode and the work-machine DMG profile
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
| Packaged `.app` / EXE smoke | `release_candidates/macos-build115/candidate.json` |
| Visual review + promote + OneDrive | Manual + `latest.json` |
| Build 114 | Stay invalidated |

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

Then the **live** gates from `NEXT_MACHINE_PROMPT.md`:

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

Package and promote **only** from the Build 115 candidate manifest. Do not
invent a hash from chat. Do not promote Build 114.

## What to tell Karen / reviewers

- Cloud PR is fixture-only. Summaries under `.adoptiq-acceptance/` are gitignored.
- No secrets, tokens, customer rows, or raw CSOne in the PR.
- A green offline sim is regression evidence, not Cisco production proof.
