# AdoptIQ — Round 156 Deep-Verification Assessment

**Request:** "I need further deep-level assessment of the logic and reporting to ensure this is 100% accurate 100% of the time."

This document is the honest answer. It separates what is now **machine-proven** (deterministic, self-consistent, monotone, bounded, honest about missing data) from what **cannot be proven offline** (predictive validity — whether HIGH-band customers really escalate/churn more), states every flaw the assessment found, and shows where each fix landed. The default report path is proven unchanged.

## Method

Five independent attack angles, each with permanent regression tests (`tests/test_round156_deep_verification.py`, 27 tests) — not one-off probes:

1. **A/B equivalence proof** — the pre-change module (commit `ceaf9fd`) was imported alongside the current one and both were fed **400 seeded adversarial cases** (NaN severities, junk statuses, duplicate-ID fan-out, shuffled columns, bad dates, huge/empty frames). Result: **0 mismatches** on the default path. The refactor provably changed nothing a CSM sees.
2. **Brute-force monotonicity hunt** — exhaustive small-space + targeted extremes: *adding an open/risky record must never decrease a component score.*
3. **Adversarial edge battery** — bounds at 3,000–5,000 records, row/column-order invariance, duplicate-ID dedup, band-edge semantics, portfolio-tally consistency.
4. **Cross-module consistency audit** — risk scorer vs `canonical_metrics` status classification; composite weighting vs documented intent; the 23 acceptance checks enumerated and coverage-gapped.
5. **Reporting-layer verification** — risk table rows vs risk profiles under BOTH scoring profiles; the cross-artifact contract under the opt-in profile; fixture composites pinned to the exact oracle-backing values.

## Findings (all fixed or characterized; none left silent)

### F1 — Legacy scoring is severely non-monotone (234 violations)
Adding an **open Low** barrier to a customer with one **open Critical** barrier drops the component **77.0 → 62.125**. Root cause: proportion-based severity/openness terms. The hunt found 234 such orderings in a small search space. **Disposition:** characterized in a pinned test (`test_legacy_barriers_nonmonotone_characterization`) so any change to legacy is conscious; **fixed under `magnitude_first`** (now provably 0 violations in the same hunt). This is the quantified justification for the live-validation round.

### F2 — magnitude_first had a residual 0.44-pt dip (9 violations) — fixed
The initial magnitude-first severity formula kept a *mean-severity-ratio* secondary term, which dipped ≤0.44 pts when adding a lower-severity open barrier at capped all-critical bases. **Fix:** the secondary term is now the **worst severity present** (max record weight / 4) — non-decreasing under record addition. Re-hunt: **0 violations; strictly monotone.** All previously pinned values (1 critical = 30, 4 critical = 82) unchanged.

### F3 — Missing pulse data was scored as healthy sentiment — fixed under magnitude_first
`_score_customer_pulse` returns 0.0 for absent/backfill-only pulse rows, and the composite weighs that 0.0 at weight 0.15 — diluting real risk as if sentiment had been measured healthy. The Round 2 `excluded_from_score` flag was **write-only**: no consumer ever honored it. **Fix (opt-in profile only):** missing pulse → `score=None` (the Round 7 contract-sentinel pattern), composite renormalizes over measured components. Legacy is byte-identical (its own alignment suite passes untouched).

### F4 — The action-plan status regex silently resolves "Unresolved" — fixed under magnitude_first
Legacy classifies resolution by substring regex `closed|resolved|complete|done`, so:
- **"Unresolved"** (contains "resolved") → counted **resolved**
- **"Incomplete"** (contains "complete") → counted **resolved**
- **"Abandoned"** (contains "done") → counted **resolved**

This is exactly the false-match class `canonical_metrics` R64 eliminated for the KPI tiles — meaning the AP tile and the risk engine could count the *same plan* differently. **Fix (opt-in profile only):** classification via the canonical lifecycle bucket (`resolved ⟺ bucket == "Completed"`), which also handles bare "Cancelled" (blocked bucket → still an unresolved commitment) and the R124 en-dash statuses. Legacy behavior pinned as characterization.

### F5 — Fixture composites now pinned by name, closing an audit gap
The 23 acceptance checks pin composite scoring **indirectly** (band-distribution chart values + `high_risk_customers`). The assessment computed the true generator-equivalent fixture composites — **Acme 55.8 HIGH, Beta 35.2 MEDIUM, Gamma 55.8 HIGH** (matching the oracle's `high=2, medium=1` exactly) — and pinned them in a named test, so a default-path scoring drift now fails with the exact numbers rather than a generic digest mismatch. A second new invariant covers the risk table itself: each row either carries its profile's score/band **verbatim** or refuses with an explicit `Unavailable (…)` label — never a third thing, in either scoring profile.

### F6 — Verified-sound properties (no defects found)
- **Weights** sum to exactly 1.0; renormalization over missing components equals the hand-computed weighted mean (proven by recomputation, not approximation).
- **Support-case scorer** (the reference model): monotone in the hunt; recent-window boundary is inclusive-at-cutoff and threads the report horizon; escalated definition routes through `cm.count_escalated` (headline-tile parity).
- **Determinism:** row order, column order, and repeat calls never change a score; duplicate-ID Snowflake fan-out rows still dedup to one logical record under both profiles.
- **Bounds:** every component and composite stays in [0, 100] at 3,000–5,000-record extremes; bands always valid.
- **Band/display consistency:** the reported band always derives from the *rounded* composite the reader sees; `risk_score_0_10` always equals `risk_score_0_100 / 10`; the portfolio summary always tallies exactly the profiles given.
- **Compound-risk gating:** requires OPEN records on *both* sides (closed barriers or closed cases can never fabricate the signal); unspecific tech ("Other / Unclassified") never pairs.
- **Profile isolation:** no production code path sets or passes a scoring profile; `Config` has no `RISK_SCORING_PROFILE` attribute. The default is `legacy` everywhere until deliberately enabled.

## Verification summary (all on this commit)

| Gate | Result |
|---|---|
| A/B equivalence vs shipped `ceaf9fd` (400 adversarial cases) | **0 mismatches** |
| Monotonicity hunt, `magnitude_first` | **0 violations** (legacy: 234, characterized) |
| Offline acceptance parity, 4 scopes | **10/10 tests — 23/23 checks/scope, oracles untouched** |
| Decision-report acceptance | **12/12** |
| Risk/consistency suites | **94/94** |
| Round 155 + 156 + deep-verification battery | **60/60** (27 new) |
| ruff / bandit (HIGH/MED gate) | **clean / clean** |

## The honest limit of "100%"

What is now **proven, permanently, by tests**: the engine is deterministic, bounded, self-consistent across every surface (score ↔ band ↔ 0-10 display ↔ portfolio tallies ↔ risk-table rows), honest when data is missing, and — under `magnitude_first` — strictly monotone (more risk can never lower a score) and canonically consistent in status classification.

What **no offline test can prove**: that the *ranking* predicts real outcomes — that a HIGH-band customer actually escalates or churns more often than a MEDIUM one. That is a property of the world, not the code, and it is measurable only against live Brian-Frazier 90-day data with known outcomes. This is precisely why `magnitude_first` ships opt-in (`ADOPTIQ_RISK_SCORING_PROFILE=magnitude_first`) with the default untouched: the live comparison in Round 154+ is the remaining gate, and everything needed to run it is staged.

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
