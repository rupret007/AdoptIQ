# AdoptIQ — Round 159 Handoff: Live Validation & Promotion (the gate before main)

**Status:** branch `claude/round153-leader-decision-value` at **`cb3d92c`**, NOT merged to `main` — deliberately. Every round since 152 was built and verified on offline fixtures with zero oracle churn, honestly labeled `offline_fixture`. This round runs the whole stack against **live Cisco data on the work machine**, makes the two promotion decisions, and only then merges to main and rebuilds installers. This document supersedes `ROUND154_HANDOFF` and the remaining items of `ROUND155_HANDOFF`.

---

## 0. First: sync the branch (5 minutes, work Mac)

The bundle with Rounds 156–158 is already at `.tmp/round153/adoptiq_round153_branch.bundle` in your checkout:

```bash
cd ~/Documents/AdoptIQ
git fetch .tmp/round153/adoptiq_round153_branch.bundle claude/round153-leader-decision-value
git checkout claude/round153-leader-decision-value
git merge --ff-only FETCH_HEAD        # advances to cb3d92c
git push origin claude/round153-leader-decision-value
```

## What shipped since the last live sync (`0ae84ff` → `cb3d92c`)

| Round | What | Look for live |
|---|---|---|
| 155 | Deterministic driver-specific **next-best-action** (kills band boilerplate) | Risk table "Recommended action" names the customer's actual acute signal with real counts |
| 156 | **Opt-in `magnitude_first` scoring profile** — fixes the ratio inversions (1-of-1 ≠ riskier than 3-of-20); missing pulse excluded not scored-healthy; canonical AP status buckets ("Unresolved" no longer counts as resolved) | Default reports byte-identical (proven, A/B 400 cases); flag flips the fixes on |
| 156 | **Deep-verification battery** (27 permanent tests): monotonicity, bounds, determinism, band/display parity, verbatim-or-refuse risk rows | `ROUND156_ASSESSMENT.md` — what's machine-proven vs what needs live outcomes |
| 157 | **Support themes** paragraph + **concentration drivers**; Ask AI **per-customer slicing fix** + **DECISION_CONTEXT** block | Themes line under KPI table; Ask AI answers "who first & why" with engine ranking |
| 158 | **Momentum** ("is it getting better?" — Brian item 6) in report + Ask AI; **cross-surface consistency lock** | "Momentum within this window:" paragraph; Ask AI momentum keys; report and Ask AI can never disagree on top risk |

All 23/23 offline parity ×4 scopes unchanged throughout; ask-AI eval replay 75/75 intact; no test weakened anywhere.

---

## The Round 159 prompt (paste into Cowork on the work machine, Fable, high reasoning)

> AdoptIQ live validation round on `claude/round153-leader-decision-value` (tip `cb3d92c`). Snowflake/CSOne/CSConsole are reachable. Do NOT weaken tests, do NOT blind-re-pin oracles, preserve canonical_metrics/risk_scoring SSoT, parameterized SQL, authorization, the 16-sheet workbook contract. Label every result `live_cisco_sources`. Work through the checklist in `ROUND159_LIVE_VALIDATION_HANDOFF.md` sections 1–6 in order; STOP before section 7 (merge) and report findings for a human go/no-go.

### 1. Full gate on live tooling
`make verify` (Py 3.11). Floor: 7,001 (confirmed at `17871ea`) **plus** the ~89 tests added in rounds 155–158 (`test_round155_compound_risk` 18, `test_round156_magnitude_first_scoring` 15, `test_round156_deep_verification` 27, `test_round157_reporting_ask_ai` 18, `test_round158_momentum_insights` 11) — record the exact count in `QUALITY_AUDIT.md`. Zero failures, ruff 0, bandit HIGH/MED 0, pip-audit clean.

### 2. The human acceptance test (the one no suite can run)
Render the real **Brian Frazier ACC 90-day** Leader report and **read it as a CSM**:
- Does the **Top risk drivers** column read as the true "why" for each customer?
- Does **next-best-action** name the right first move (not boilerplate)? Would you actually do it first?
- Do **Support themes** match what you know about those accounts' pain?
- Does **Momentum** match your gut on whether the team's quarter is improving?
- Compound-risk lines: is the named technology overlap real when you drill into the workbook?
Record verdicts per section in `QUALITY_AUDIT.md` (live labels). Any section that reads wrong live is a bug — fix before proceeding.

### 3. Ask AI live truth check
Same scope, ask: "Who should I call first and why?", "Is it getting better?", "What are the support themes?". Verify: answers quote DECISION_CONTEXT / CANONICAL_HEADLINE numbers verbatim; the named top-risk customer equals the Word report's top row (the R158 lock guarantees this offline — confirm live); no fabricated numbers (R95 cross-check should show zero corrections); citations resolve.

### 4. The scoring promotion decision (magnitude_first)
```bash
export ADOPTIQ_RISK_SCORING_PROFILE=magnitude_first
```
Render the same scope both ways. Compare band distributions against known outcomes (which customers actually escalated / churned / renewed / went BEMS in the trailing period). Decision rule: promote ONLY if magnitude_first ranks the known-bad accounts higher than legacy does. If promoted: make it the Config default, re-pin the four offline oracles **failing-test-first, every moved value justified** in `QUALITY_AUDIT.md` (the fixture pins are named in `test_round156_deep_verification.py::test_default_fixture_bands_pinned_as_characterization` — update them consciously). If not promoted: record why; the flag stays available.

### 5. Round 152/153 live items that still stand
- Route-gating live sweep (R152 default-deny endpoint audit against a running app).
- Live prefetch freshness clock (R153 T3 fails closed — verify a real fetch stamps real freshness and the report shows it).
- Alias registry on a real cross-variant org (R153 T4): merged identity count equals `count_customers`, merged org's risk reflects combined evidence.
- macOS auto-update: set the real Cisco Team ID in `expected_team_id.txt` (R153 security pin) and verify `_verify_macos_signing_identity` against the shipped app.

### 6. B3 completion (the one remaining report feature)
The momentum paragraph answers Brian item 6 textually; the full B3 is the **age-band series under the AP chart** (`action_plan_age_band_series` beside `action_plan_chart_series` in canonical_metrics, second series under the AP `Chart_ID`). This CHANGES `Chart_Data` digests → do it in this live round with the honest re-pin discipline (failing fixture case first, regenerate 4 scopes, justify every moved value). Optional if time-boxed; do not rush it.

### 7. Only after 1–6 are green and recorded: merge & build
- Merge the branch to `main` (no squash — history carries the audit trail).
- Build installers **on the work machine** (the cloud sandbox cannot codesign or build DMG/EXE): macOS ARM + x86 DMGs (signed, Team ID pinned), Windows EXE. Smoke-test auto-update path.
- Tag the release; attach `QUALITY_AUDIT.md` round entries 152–159 as the release notes' verification section.

---

## Standing truths (unchanged)
- Counts: `canonical_metrics.py`. Scores: `risk_scoring.py`. Never inline, never LLM-decided. New signals must be deterministic canonical helpers; the LLM only phrases from structured evidence.
- 16-sheet workbook contract; `validate_cross_artifact_contract` / `validate_word_semantics` gates; `_expected_visible_word_tables` moves in lockstep with the renderer (display_count defined twice — renderer ~4109, contract ~4617).
- Evidence labels are honest: `offline_fixture` vs `live_cisco_sources`.
- No credentials, customer data, reports, or venvs in git. `.venv311/` is now gitignored.

**Reference docs in the branch:** `ROUND156_ASSESSMENT.md` (what's proven vs what needs live outcomes), `RISK_LOGIC_EVALUATION.md` (scoring math + promotion rationale), `QUALITY_AUDIT.md` rounds 152–158, `WORK_MACHINE_TEST_ONRAMP.md` + `run_dev.sh` (setup), `UX_REVIEW_2026.md`.

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
