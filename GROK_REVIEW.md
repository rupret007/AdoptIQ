# Grok quality + security review (Round 169.5)
<!-- Round 169.5 -->

Review of AdoptIQ `main` (Round 169 `7be5d2e`) plus this branch
(`cursor/offline-bob-sim-1748`, PR #2). Offline / fixture only.

Focus: **logic**, **reporting**, and **architecture** of the offline
sim — not cosmetic churn.

Honesty stamps that stay false here:

- `live_validation_performed=false`
- `production_accuracy_claimed=false`
- `release_ready=false`
- `ready_for_live_cisco=false`

**Stay PRIVATE.** Do not change visibility. Do not make the repo public.
Do not blame GitHub billing. Hosted ~2s empty-step jobs with
`runner_id=0` are runner assignment failures. **Local/fixture proof is
the gate.** Sim ≠ live Cisco accuracy.

No secrets, customer rows, or raw CSOne appear in this review.

## Review Findings — Logic

| ID | Area | Verdict | Notes |
| --- | --- | --- | --- |
| L1 / H2 | Subscription `live_validation_performed` default | **FAIL → fixed** | Missing key defaulted to True. Helper `_r169_5_explicit_live_validation_performed` is fail-closed. Snowflake rows are not Jeff's live checklist. |
| L2 | Blocked corpus skip-pass | **FAIL → fixed** | `blocked_in_repo_corpus` and `blocked_missing_external` used the `skipped_no_corpus` exit-0 path. A Cloud run could look green while `CSONE_CORPUS_DIR` pointed at an in-repo or missing workbook dir. Scorecard SSoT now **FAIL**s those kinds. Only true `skipped_no_corpus` skips. |
| L3 | Source-contracts empty-zero vs failure | **PASS** | Fixture DB-API contracts already distinguish failure-state-not-zero and policy-blocked-not-zero. Wired into local proof via pipeline smoke. |
| L4 | Jeff-only stubs | **PASS** | Live decision-report mode and work-machine DMG profile fail closed when Cisco/DMG inputs are absent. |
| L5 | Compact/Renewal Live Validation Yes | **FAIL** (left alone) | Compact and Renewal stamp Yes whenever `LOCAL_ACCEPTANCE_MODE` is off. Application-path, not authorized live reconciliation. Live presentation stays Jeff-only. |
| L6 | Fixture coverage gap | **WARN → deepened** | Local proof now runs pipeline smoke (contracts + Team/Member/Customer/Comprehensive × 2 + 17-sheet inventory + manager UX). Still SKIPPED on purpose: verify, official R169 metamorphic, 23-scenario lab, A–G production-simulation, live Cisco. |

## Review Findings — Reporting

| ID | Area | Verdict | Notes |
| --- | --- | --- | --- |
| R1 | Offline honesty stamps | **PASS** | Bob sim, resolve-only, local proof, lab, source-contracts, decision-report `--mode live` preflight-fail, and jeff-only stubs keep the three stamps false. |
| R2 | Scorecard used skip as pass | **FAIL → fixed** | Bash gate `exit_code=0` on skips made the summary look uniformly green. `scripts/offline_sim_scorecard.py` now emits PASS / FAIL / SKIPPED / UNKNOWN. Skips are not passes. |
| R3 | `ready_for_karen` vs live | **WARN → clarified** | Local green still means the work-Mac handoff is readable (`ready_for_karen`). It does **not** mean live Cisco. New `ready_for_live_cisco=false` is always stamped. Handoff now says do not assume verify ran. |
| R4 | QUALITY_AUDIT 169.2 billing story | **FAIL** (superseded) | Historical 169.2 text still says billing-blocked. 169.3+ is the product diagnosis. One-line supersede added; history not rewritten. |
| R5 | README historical "100%" changelog | **WARN** | Old changelog language. Do not treat as a current accuracy claim. Not rewritten. |
| R6 | Hosted Actions | **PASS** (documented) | Latest empirical `build.yml` PR run `32625073807` job `97159040140` (commit `508308a`) is `hosted_runner_not_assigned` / `job_never_started`: `runner_id=0`, empty steps, ~2s. Classifier: `is_billing_diagnosis=false`, `do_not_blame_billing=true`. YAML cannot force a hosted runner. Local green is the gate. |

## Review Findings — Architecture

| ID | Area | Verdict | Notes |
| --- | --- | --- | --- |
| A1 | Parallel sim entrypoints | **WARN → clarified** | `offline-sim-local` / `offline-sim-pr` / `offline-sim` are one honesty contract at three depths — not three simulators. Playbook "One entry" now names local-proof as the Cloud/Bob gate. |
| A2 | Local proof skipped report generation | **FAIL → fixed** | Local previously probed UX and fixture KPIs but did not build Word/XLSX. Pipeline smoke is now a required local gate so Cloud can catch 17-sheet / decision-report breaks before the work Mac. |
| A3 | Corpus decision duplicated in bash | **FAIL → fixed** | `offline_sim_scorecard.corpus_action` is the SSoT. Bash asks `--corpus-action` then run/skip/fail. Unknown kinds fail closed. |
| A4 | Workflows secret-free | **PASS** | `offline-sim.yml` forbids the word `secrets`. `build.yml` PR job does not use `secrets.` context. |
| A5 | In-repo corpus blocker + CSOne path | **PASS** | Resolver refuses non-synthetic in-repo dirs. `_resolve_csone_path_safe` uses normalized prefix + separator. |
| A6 | Roster / alias SSoT | **PASS** (do not strip) | `team_config.json` and `customer_aliases.defaults.json` carry real Cisco roster/alias strings by design. Do not copy them into new fixtures or PR text. |
| S1 | Secrets in fixtures / PR | **PASS** | No Keeper material, no Snowflake passwords, no raw CSOne in new fixtures or this review. Synthetic CSOne is Acme / Beta / Gamma + `@example.invalid` only. |
| F6 | `make csone-corpus-replay` empty dir | **FAIL → fixed** | Empty `CSONE_CORPUS_DIR` fails with an explicit required-for-replay message. |
| F7 | R75 test email | **FAIL → fixed** | Mock owner email is `fixture.owner@example.invalid`. Row-count contract unchanged. |

## What this branch changed after the review

1. Fail-closed Subscription live-validation helper
   (`_r169_5_explicit_live_validation_performed`). Missing key → No.
2. `fetch_subscription_data` stamps `live_validation_performed=False` on
   not-found, success, and error returns.
3. `make csone-corpus-replay` requires a non-empty corpus dir.
4. R75 mock email sanitized to `@example.invalid`.
5. `scripts/offline_sim_scorecard.py` — PASS/FAIL/SKIPPED/UNKNOWN +
   corpus fail-closed SSoT.
6. `make offline-sim-local` runs pipeline smoke and prints a scorecard.
   Verify / official R169 / lab / A–G are SKIPPED, live Cisco UNKNOWN.
7. Bash Bob sim **FAIL**s blocked corpus kinds instead of skip-passing.
8. Playbook + work-Mac handoff: one local-proof entry; do not assume
   verify ran; roster/alias SSoT warning.

## Left alone (Jeff-only)

- Live Snowflake / Keeper / CSConsole / CircuIT / OneDrive
- Packaging, install, promote, `latest.json`
- Compact/Renewal "Yes unless fixture mode" presentation
- Repo visibility
- `ADOPTIQ_BUILD` bump
- Official R169 metamorphic and `make verify` inside the local-proof
  runner (they stay on `offline-sim-pr` / `offline-sim`)

## How to prove locally

```bash
make offline-sim-local
python3 -m pytest tests/test_round169_5_grok_review_honesty.py tests/test_round169_2_hosted_actions_block.py tests/test_round168_offline_bob_sim.py -q
make verify
```

Local proof on this revision: `make offline-sim-local` PASS with
`ready_for_live_cisco=false`. `make verify` PASS — ruff 0, bandit 0
HIGH/MED, pip-audit clean, pytest 8752 passed / 9 skipped / 14
deselected, eval-ask-ai 14 passed.

Hosted Actions may remain red with `runner_id=0`. That is not a sim
regression and not a billing story. Stay draft until Jeff/Karen undraft.
