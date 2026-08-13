# OBSOLETE — DO NOT RUN §0

This file preserves a historical Round 160 handoff only. Its merge commands,
commit labels, build labels, and 16-sheet references are obsolete. Start from
the current checked-out branch/commit and use `NEXT_MACHINE_PROMPT.md` plus the
latest Round 167 entry in `QUALITY_AUDIT.md`; never execute the commands below.

# AdoptIQ — Cursor Handoff (historical work-machine record)

**Paste the prompt in §1 into Cursor after completing §0.** This document is the single up-to-date handoff; it supersedes `ROUND159_LIVE_VALIDATION_HANDOFF.md` (whose checklist is preserved below as the post-merge validation plan — the owner elected on 2026-08-11 to merge to `main` first and validate live immediately after, which is safe because every round since 152 is additive with the default report path proven byte-identical; the genuinely behavior-changing options remain opt-in flags).

---

## §0 — One-time: sync, merge to main, push (run in Terminal on the work Mac)

The bundle with everything through Round 160 is already at `.tmp/round153/` in your checkout:

```bash
cd ~/Documents/AdoptIQ
git fetch .tmp/round153/adoptiq_round153_branch.bundle claude/round153-leader-decision-value
git checkout claude/round153-leader-decision-value && git merge --ff-only FETCH_HEAD
git push origin claude/round153-leader-decision-value

# merge to main (owner-approved 2026-08-11)
git checkout main && git pull origin main
git merge --no-ff claude/round153-leader-decision-value -m "Merge rounds 152-160: product excellence, decision value, predictive intelligence"
git push origin main
```

If `git pull origin main` brings in commits beyond `9907db1`, resolve normally — the branch has never touched `main`-only files.

---

## §1 — The Cursor prompt (paste verbatim)

> You are working on AdoptIQ, a Cisco Customer Success desktop app (Flask + pandas/numpy, Word/Excel report generation, grounded Ask-AI). Repo root: `~/Documents/AdoptIQ`, branch `main` (just merged rounds 152–160). Python 3.11 venv: `python3.11 -m venv .venv311 && .venv311/bin/pip install -r requirements.txt`, or `./run_dev.sh`. Tests: `.venv311/bin/python -m pytest -q -m 'not eval'` (floor ~7,140; zero failures expected), plus `pytest tests/ask_ai_eval/ -m eval` (14). Lint/security: `ruff check .`, `bandit -c bandit.yaml -r . -ll`.
>
> **Non-negotiable invariants — never weaken, never bypass:**
> 1. Counts come from `canonical_metrics.py`; risk scores from `risk_scoring.py` (SSoT). Never recompute inline; never let an LLM decide a number.
> 2. Historical note: the current paired workbook has an exact 17-sheet contract including `Evidence_Links` and `Defect_Correlations`; consult `NEXT_MACHINE_PROMPT.md`. `validate_cross_artifact_contract` / `validate_word_semantics` must stay green.
> 3. Offline acceptance: `tests/test_round142_offline_acceptance_artifacts.py` regenerates 4 scopes at 23/23 parity against pinned oracles. Any change that moves an oracle value must be a conscious re-pin: add a failing fixture case first, regenerate, justify EVERY moved value in `QUALITY_AUDIT.md`. The default fixture composites are additionally pinned by name in `tests/test_round156_deep_verification.py::test_default_fixture_bands_pinned_as_characterization` (Acme 55.8 HIGH / Beta 35.2 MEDIUM / Gamma 55.8 HIGH).
> 4. Never weaken a test to make it pass. Characterization tests pin known legacy flaws deliberately (e.g. legacy scoring non-monotonicity, the AP status substring regex) — changing them is a decision, not a fix.
> 5. Ask AI: answers are grounded via citation whitelists + claim entailment; `CANONICAL_HEADLINE` and `DECISION_CONTEXT` blocks are authoritative. The eval replay corpus (75 questions, cassettes) must stay 75/75 — if you change evidence-context construction, re-record via `tests/ask_ai_eval/_generate_fixtures.py`, never hand-edit cassettes.
> 6. Two opt-in flags exist and default OFF — do not flip defaults without the validation steps below: `ADOPTIQ_RISK_SCORING_PROFILE=magnitude_first` (fixes ranking inversions; see `RISK_LOGIC_EVALUATION.md`) and predictive calibration artifacts (see `PREDICTIVE_INTELLIGENCE.md`; provenance-gated to `live_cisco_sources`).
> 7. Evidence labels are honest: `offline_fixture` vs `live_cisco_sources`. Never commit credentials, customer data, generated reports, or venvs.
>
> **Your first task is the live validation pass (§2 of CURSOR_HANDOFF.md).** Work through it in order; record every result with live labels in `QUALITY_AUDIT.md` as a "Round 161 — live validation" entry. Read `PREDICTIVE_INTELLIGENCE.md`, `RISK_LOGIC_EVALUATION.md`, `ROUND156_ASSESSMENT.md`, and the Round 152–160 entries in `QUALITY_AUDIT.md` before changing anything.

---

## §2 — Live validation checklist (first work on the machine, in order)

1. **Full gate on live tooling.** `make verify` equivalent: full suite (record the exact count — floor 7,001 at `17871ea` plus ~140 tests added in rounds 155–160), ruff 0, bandit HIGH/MED 0, pip-audit clean.
2. **The human acceptance test.** Render the real Brian Frazier ACC 90-day Leader report and read it as a CSM: risk drivers ring true? next-best-actions are what you'd actually do first? Support themes match known account pain? Momentum matches your gut? Predictive outlook names the right accounts (it will say "uncalibrated prior — relative ranking only" — that's correct until step 4)? Record per-section verdicts.
3. **Ask AI live truth check.** Ask "Who should I call first and why?", "Is it getting better?", "What are the support themes?", "Who's likely to escalate this month?". Verify answers quote DECISION_CONTEXT / CANONICAL_HEADLINE numbers verbatim, the named top-risk customer matches the Word report (R158 lock), zero R95 numeric corrections, citations resolve.
4. **Calibrate the predictive engine on real history.**
   `python scripts/backtest_escalation_forecast.py --fixture <90-365d export JSON> --data-end <export timestamp ISO> --output-dir .tmp/backtest`
   Read the audit block first (exclusions, data_end source, approximations). Then: does the scorecard beat all three baselines (base rate, trailing-90d heuristic, existing risk score) on lift@top-20%? What claim level did the event count earn? Wire the resulting artifact into report generation so calibrated language activates. If the scorecard loses to a baseline, the report must keep saying so — that's the design.
5. **The magnitude_first promotion decision.** `export ADOPTIQ_RISK_SCORING_PROFILE=magnitude_first`, render the same scope both ways, and use step 4's backtest baselines (both profiles are computed automatically) plus known outcomes: does magnitude_first rank the customers who actually escalated/churned higher? Promote to Config default ONLY if yes, with the conscious oracle re-pin per invariant 3. Otherwise record why and leave the flag available.
6. **Security & release items.** Set the real Cisco Apple Team ID in `expected_team_id.txt` (R153 signing pin); run the R152 route-gating sweep against a running app; verify live prefetch freshness stamps real timestamps (R153 fails-closed).
7. **Builds.** macOS ARM + x86 DMGs (signed) and Windows EXE on this machine; smoke-test auto-update; tag the release with the `QUALITY_AUDIT.md` rounds 152–161 entries as verification notes.
8. **Optional next feature round (B3 completion):** the age-band series under the AP chart (`action_plan_age_band_series` in canonical_metrics, second series under the AP `Chart_ID`) — this is the one remaining item that REQUIRES an oracle re-pin; do it with the invariant-3 discipline.

## §3 — What merged (rounds 152–160, one line each)

| Round | Delivered |
|---|---|
| 152 | Security default-deny endpoints, worker-failure tracking, a11y/canonical badge, product-excellence audit |
| 153 | Top-risk-driver column, full roster, freshness fails closed, alias-registry identity, update signing pin |
| 155 | Compound-risk correlation (barrier+case same tech) + deterministic per-customer next-best-action |
| 156 | Opt-in magnitude_first scoring (fixes ranking inversions) + 27-test deep-verification battery; default proven byte-identical (A/B 400 adversarial cases) |
| 157 | Support-themes line, tech-concentration drivers, Ask-AI per-customer slicing fix + DECISION_CONTEXT |
| 158 | Momentum ("is it getting better?" — Brian item 6) in report + Ask AI; report↔Ask-AI ranking consistency lock |
| 159 | (docs) validation handoff |
| 160 | Predictive escalation engine (30-day scorecard) + time-travel backtest with honesty ladder; built by 4-agent research fleet, hardened by 3-agent adversarial attack (15 findings fixed with regression tests) |

**Key docs:** `PREDICTIVE_INTELLIGENCE.md` · `RISK_LOGIC_EVALUATION.md` · `ROUND156_ASSESSMENT.md` · `QUALITY_AUDIT.md` (the full audit trail) · `WORK_MACHINE_TEST_ONRAMP.md` / `run_dev.sh` (setup).

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
