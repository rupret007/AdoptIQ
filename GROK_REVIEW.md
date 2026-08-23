# Grok quality + security review (Round 169.5)
<!-- Round 169.5 -->

Review of AdoptIQ `main` (Round 169 `7be5d2e`) plus this branch
(`cursor/offline-bob-sim-1748`, PR #2). Offline / fixture only.

Honesty stamps that stay false here:

- `live_validation_performed=false`
- `production_accuracy_claimed=false`
- `release_ready=false`

**Stay PRIVATE.** Do not change visibility. Do not make the repo public.
Do not blame GitHub billing. Hosted ~2s empty-step jobs with
`runner_id=0` are runner assignment failures. **Local/fixture proof is
the gate.** Sim ≠ live Cisco accuracy.

No secrets, customer rows, or raw CSOne appear in this review.

## Review Findings

| ID | Area | Verdict | Notes |
| --- | --- | --- | --- |
| H1 | Offline sim honesty stamps | **PASS** | Bob sim, resolve-only, local proof, lab, source-contracts, decision-report `--mode live` preflight-fail, and jeff-only stubs keep all three stamps false. |
| H2 | Subscription `live_validation_performed` default | **FAIL → fixed** | `app_simple.py` defaulted a missing key to `True`, so Subscription Word/XLSX could stamp Yes when `fetch_subscription_data` never set the key. Default is now fail-closed. Snowflake rows are not Jeff's live checklist. |
| H3 | Compact/Renewal Live Validation Yes | **FAIL** (left alone) | Compact and Renewal stamp Yes whenever `LOCAL_ACCEPTANCE_MODE` is off. That is "application source path", not authorized live reconciliation. Left unchanged — live Cisco / packaging / promote stay Jeff-only. |
| H4 | QUALITY_AUDIT 169.2 billing story | **FAIL** (superseded) | Historical 169.2 text still says billing-blocked / stay-draft-because-hosted-red. 169.3+ is the product diagnosis. One-line supersede added; history not rewritten. |
| H5 | README historical "100%" changelog | **WARN** | Old changelog language. Do not treat as a current accuracy claim. Not rewritten this round. |
| F1 | Synthetic CSOne fixtures | **PASS** | `testdata/synthetic_csone` is Acme / Beta / Gamma + `@example.invalid` only. |
| F2 | Workflows secret-free | **PASS** | `offline-sim.yml` forbids the word `secrets`. `build.yml` PR job does not use `secrets.` context. |
| F3 | Jeff-only stubs fail-closed | **PASS** | Live decision-report mode and work-machine DMG profile refuse when Cisco/DMG inputs are absent. |
| F4 | In-repo corpus blocker | **PASS** | `blocked_in_repo_corpus` refuses non-synthetic in-repo `CSONE_CORPUS_DIR`. |
| F5 | CSOne path traversal | **PASS** | `_resolve_csone_path_safe` uses normalized prefix + separator. |
| F6 | `make csone-corpus-replay` empty dir | **FAIL → fixed** | Empty `CSONE_CORPUS_DIR` now fails with an explicit required-for-replay message, matching production-simulation. |
| F7 | R75 test email | **FAIL → fixed** | Mock owner email was a cisco.com string. Now `fixture.owner@example.invalid`. Row-count contract unchanged. |
| F8 | Roster / alias SSoT | **PASS** (do not strip) | `team_config.json` and `customer_aliases.defaults.json` carry real Cisco roster/alias strings by design. Do not copy them into new fixtures or PR text. |
| C1 | Offline-sim coverage gap | **WARN → deepened** | Local/PR sim now runs fixture Snowflake `source-contracts`. Still skipped on purpose: standalone 23× HTTP, A–G `production-simulation` (PR profile), live Cisco, packaging, promote. |
| C2 | Hosted Actions | **PASS** (documented) | Latest `build.yml` PR run still `runner_id=0`, empty steps. YAML cannot force a hosted runner. Do not blame billing. Do not make the repo public. Local green is the gate. |
| S1 | Secrets in fixtures / PR | **PASS** | No Keeper material, no Snowflake passwords, no raw CSOne in new fixtures or this review. |

## What this branch changed after the review

1. Fail-closed Subscription live-validation helper
   (`_r169_5_explicit_live_validation_performed`). Missing key → No.
2. `fetch_subscription_data` stamps `live_validation_performed=False` on
   not-found, success, and error returns.
3. `make csone-corpus-replay` requires a non-empty corpus dir.
4. R75 mock email sanitized to `@example.invalid`.
5. `make offline-sim-local` and `make offline-sim-pr` run fixture
   `source-contracts`.
6. Playbook + work-Mac handoff: local/fixture proof is the undraft gate;
   hosted may stay red; roster/alias SSoT warning.

## Left alone (Jeff-only)

- Live Snowflake / Keeper / CSConsole / CircuIT / OneDrive
- Packaging, install, promote, `latest.json`
- Compact/Renewal "Yes unless fixture mode" presentation
- Repo visibility
- `ADOPTIQ_BUILD` bump

## How to prove locally

```bash
make offline-sim-local
python3 -m pytest tests/test_round169_5_grok_review_honesty.py tests/test_round169_2_hosted_actions_block.py tests/test_round168_offline_bob_sim.py -q
```

Hosted Actions may remain red with `runner_id=0`. That is not a sim
regression and not a billing story.
