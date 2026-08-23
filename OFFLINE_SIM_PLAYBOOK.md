# Offline / Cloud / Bob simulation playbook (Round 168)

AdoptIQ can be improved from Cursor Cloud or any machine that is **not**
Jeff Story’s work Mac. This page is the only Cloud/Bob command card.
It does **not** authorize live Cisco accuracy claims.

Honesty stamps that must stay false here:

- `live_validation_performed=false`
- `production_accuracy_claimed=false`
- `release_ready=false`

Build 114 was never promoted. Product line is **v1.0.4 / Build 115**.
Do not bump `ADOPTIQ_BUILD` for tooling-only work.

## What Cloud/Bob can run (no work Mac)

```bash
python3 -m pip install -r requirements.txt -c constraints-build113.txt
python3 -m pip install ruff bandit pip-audit pytest
make verify                 # lint + security + audit + pytest + eval-ask-ai
make local-acceptance-lab   # 21 guarded fixture scenarios
make metamorphic-acceptance # fixture KPI invariance (Round 168)
make eval-ask-ai            # already inside make verify
make offline-sim-pr         # PR/Cloud default: verify + lab + metamorphic + synthetic replay
make offline-sim            # full Bob loop; adds HTTP + production-simulation when a corpus exists
```

Optional diagnostics:

```bash
bash scripts/run_offline_bob_sim.sh --resolve-only
make synthetic-csone        # regenerate testdata/synthetic_csone from Round 145 fixtures
```

`make local-acceptance-http` starts Flask per scenario (21/21). Use it in
`--profile full`, not as the PR default.

## Fixture-only vs corpus

| Gate | Needs | Corpus? |
| --- | --- | --- |
| `make verify` / `eval-ask-ai` | committed tests + Ask AI cassettes | no |
| `make local-acceptance-lab` / `http` | Round 145 fixtures | no |
| `make metamorphic-acceptance` | Round 145 fixtures | no |
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

Do not pretend Cloud can close these:

1. Live Snowflake / Keeper / CircuIT / CSOne / OneDrive reconciliation.
2. Packaged Build 115 smoke, visual review, promote, `latest.json`.
3. OneDrive publication of a DMG/EXE.
4. Any claim of production accuracy or `release_ready=true`.
5. Never promote Build 114.

## Ranked improvement backlog

**P0 — this PR (offline, Cloud-safe)**

- One Cloud entrypoint (`make offline-sim` / `offline-sim-pr`).
- Honest corpus blocker; no real CSOne in Git.
- Synthetic CSOne projected from Round 145 fixtures.
- Fixture metamorphic gate.
- PR CI (PRs previously had no workflow).

**P0 — Jeff / live only**

- Live Cisco reconciliation against authorized scopes.
- Build 115 package / smoke / promote / OneDrive.
- Invalidate any attempt to install or publish Build 114.

**P1 — later, not this PR**

- Period-over-period / trend (orphaned helper surface in `leader_report_components.py`).
- Worker refactor only after a live baseline.
- Leader / Renewal / Subscription live freshness clocks.
- Live NYU / customer-alias confirmation on real DSM rows.

**P2**

- Cosmetic ruff `UP` modernizations.
- `locals()` dead-code sweep.
- Live CircuIT Ask AI eval (cassettes stay the offline floor).

## Safety

- Private repo. No secrets, tokens, customer rows, or raw CSOne in Git or PRs.
- Do not overwrite Cisco connection code wholesale.
- Reuse Round 145 fixtures, Ask AI cassettes, and this metamorphic gate.
- If a gate needs a real corpus and none is present, skip with
  `skipped_no_corpus` / `blocked_*` — fail closed and stay honest.
