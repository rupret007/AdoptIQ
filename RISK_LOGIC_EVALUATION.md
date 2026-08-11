# AdoptIQ — Risk Scoring Logic: Evaluation & Improvement Plan

A domain evaluation of `risk_scoring.py` — is the analytical logic genuinely insightful, or just plausible-looking? Honest verdict, the real flaws with numeric evidence, the principled fixes, and what must be validated on live data before shipping any score change.

## Verdict in one line

The **insight layer** is now strong (compound-risk correlation + deterministic driver-specific actions — shipped Round 155). The **scoring math** is sound on its best component and had a **real structural flaw on three others**: it mixed *magnitude* and *proportion* inconsistently, and where it used proportion it produced orderings that are, for risk, backwards. **Round 156 fixes this** — but as an **opt-in profile** (default unchanged), because the correction moves every customer's band and that must be proven against live outcomes, not formula aesthetics, before it becomes the default.

## What shipped in Round 156 (and what didn't)

- **Shipped:** a `magnitude_first` scoring profile in `risk_scoring.py` that makes the three inverted components magnitude-primary (details below). It is **opt-in** — resolved by `resolve_risk_scoring_profile()` with precedence *explicit arg > `ADOPTIQ_RISK_SCORING_PROFILE` env > `Config.RISK_SCORING_PROFILE` > `legacy`*. The **default is `legacy`**, so every shipped report, oracle, and test is **byte-identical** (verified: offline 4-scope parity 23/23 unchanged, decision-report acceptance green, full risk suite green).
- **Did NOT ship:** any change to the *default* scoring, i.e. no change to what a Cisco CSM sees today. Promoting `magnitude_first` to the default is a deliberate, live-validated step (see sequencing below) — not something to flip blind in a sandbox on synthetic fixtures.
- **Why a flag and not a straight fix:** the fixture customers sit within ~1 point of a band edge (Acme/Gamma 54.2, HIGH edge 55; Beta 33.6, MEDIUM edge 35). A straight change would move their bands and force an oracle re-pin whose "correct" new values can't be validated on 1-barrier/3-plan toy data. Industry practice (Gainsight; churn back-testing) is explicit that a weighting change is validated against real churn/escalation cohorts before production. The flag lets the improved logic be real, tested, and adoptable now — and promoted after one live comparison.

## The scoring model

A weighted 0–100 composite over seven components (`RiskWeights`):

| Component | Weight | Scoring style | Assessment |
|---|---|---|---|
| Adoption barriers | 0.28 | **mixed** — severity & open are *ratios*, aging & volume are magnitude | flawed on its two biggest sub-signals |
| Support cases (TAC) | 0.27 | **absolute magnitude**, capped | **sound** — the reference design |
| Customer pulse | 0.15 | numeric score / poor-count | reasonable |
| Action plans | 0.10 | **pure ratio** (`unresolved_ratio × 100`) | **clearly backwards** |
| Incidents | 0.08 | count/impact | reasonable |
| Contract | 0.08 | **ratio** (`high_risk/count × 70 + …`) | flawed |
| Engagement | 0.04 | activity volume | minor |

## The flaw, with numbers

**Support cases (the good model).** `volume×2.5 (cap 25) + escalated×9 (cap 35) + bems×12 (cap 30) + recent×2.5 (cap 15)`. Every term is an *absolute count* with a cap. More escalations → higher risk, until saturation. Momentum is captured. This is how a risk signal should behave.

**Action plans (the clearest error).** `score = unresolved_ratio × 100`.

- Customer A: **1** open plan, 0 closed → ratio 1.0 → **score 100** (maximum AP risk)
- Customer B: **3** open plans, 17 closed → ratio 0.15 → **score 15**

Customer A, with one open plan, is scored **6.7× riskier** on this dimension than Customer B with three times the open work. The formula rewards customers who complete nothing (small denominator) and penalizes customers doing real remediation (large denominator). Magnitude of open commitments is the risk-relevant quantity; the model discards it.

**Adoption barriers (highest-weighted component, 0.28).** Two of its four sub-scores are ratios:

- `severity_points = Σ(per-record severity) / (count×4) × 45`. A customer with **1** critical barrier and one with **4** critical barriers get the **identical** severity score (both ratio = 1.0 → 11.25 pts). Four critical adoption blockers is a materially worse situation than one; the model can't tell.
- `open_points = (open/total) × 30`. **1 open of 1** = 30 pts; **10 open of 20** = 15 pts. A customer with ten open barriers scores *lower* on openness than one with a single open barrier.

`volume_points` (count×2, cap 15) partially compensates for magnitude, but weakly — it's capped at 15 of the component's ~110 raw points, so the ratio terms dominate.

**Contract (0.08).** `(high_risk_subs/count)×70 + (inactive_subs/count)×40` — same proportion-not-magnitude issue: one at-risk sub of one total maxes the term.

## Why it matters for customer success

Risk scoring exists to answer one question: *which customer do I call first?* Proportion-based components corrupt that ranking. A customer **drowning** in open barriers, open plans, and at-risk subscriptions — but who also has a lot of *total* history — can be ranked **below** a smaller customer whose handful of items happen to all be open. The tool can point the CSM at the wrong account. That is the opposite of "truly understand the customer."

## The principled fix (per component) — implemented in the `magnitude_first` profile

The correction is not a tuning guess — it's making magnitude primary and proportion secondary, bounded to the same 0–100 scale so the change is contained and defensible. All four are live in the `magnitude_first` profile and covered by `tests/test_round156_magnitude_first_scoring.py` (verified numbers below):

- **Action plans:** `min(unresolved×12, 60) + unresolved_ratio×40`. → A(1-of-1)=**52**, B(3-of-20)=**42**, C(5-of-5)=**100** (legacy: A=100, B=15 — a 6.7× inversion, now collapsed to 1.24×). One open item no longer maxes the risk; real remediation load is no longer scored near-zero. Round 156 deep-verification also switched *resolution classification* to the canonical lifecycle bucket under this profile: the legacy substring regex counts plans named "Unresolved"/"Incomplete"/"Abandoned" as **resolved** (substring false-matches); the canonical bucket cannot.
- **Barrier severity:** `min(severity_mass×3, 40) + worst_severity_share×5`, so 4 critical (**82**) ≫ 1 critical (**30**) — legacy could barely tell them apart (83 vs 77, a 6-pt spread) because both maxed the ratio. (Deep-verification note: the secondary term is the **worst severity present**, not the mean ratio — a mean ratio dips when a lower-severity barrier is added, which briefly made "add an open Low barrier" *lower* the score by ≤0.44 pts; the max-share term is strictly monotone.)
- **Barrier openness:** `min(open_count×6, 25) + (open/total)×5` — magnitude-led, so 10 open barriers outrank 1 (legacy inverted this).
- **Contract:** `min(high_risk×18, 55) + min(inactive×10, 30) + at_risk_ratio×15` — so 3 at-risk subs of 20 (**56**) outrank 1 at-risk of 1 (**33**); legacy inverted this (10.5 vs 70).
- **Customer pulse (missing-data honesty):** absent/backfill-only pulse rows are **excluded and renormalized** (`score=None`, the Round 7 contract-sentinel pattern) instead of scoring 0.0 — legacy weighs missing sentiment into the composite as if it were measured *healthy* at weight 0.15, diluting real risk. The measured-pulse formula itself is unchanged: pulse is intentionally a proportion (a satisfaction *rate*, like CSAT/NPS).

Support cases (the sound reference) are unchanged in both profiles. See `ROUND156_ASSESSMENT.md` for the full deep-verification: A/B equivalence proof (400 adversarial cases, 0 default-path mismatches), monotonicity hunt (legacy 234 violations, magnitude_first 0), and the permanent 27-test battery.

## How to enable it for the live validation pass

Set the environment variable before generating a report, or set `Config.RISK_SCORING_PROFILE`:

```bash
export ADOPTIQ_RISK_SCORING_PROFILE=magnitude_first
```

or pass `scoring_profile="magnitude_first"` directly to `compute_customer_risk_profile(...)`. Nothing else changes; the flag flows through every report path.

## Why the default is unchanged until validated on **live** data

Turning `magnitude_first` on shifts **every customer's score and band**. Two consequences govern why it ships opt-in rather than as the default:

1. **Oracle re-pin** — the offline fixtures encode the *current* (legacy) orderings, so every fixture score moves under the new profile. That re-pin is mechanical and expected, but must be done with the Round 153/155 discipline (failing-test-first, justify every moved value) — and it should be done *together with* the live comparison, not ahead of it, so the pinned "expected" bands reflect a validated model rather than formula reasoning on toy data.
2. **The correctness claim needs ground truth.** "Magnitude-first is more accurate" is a strong, defensible hypothesis — but *accuracy* is measured against real outcomes: which Brian-Frazier-class customers actually escalated, churned, or renewed. The comparison to run on live 90-day data: does the new band distribution correlate **better** with actual escalations/BEMS/renewal outcomes than the old one? Promoting a score-math change blind — without confirming it ranks real at-risk customers higher — risks making the tool *less* accurate even while the formula looks more principled. **That is why the default stays `legacy` and the new model ships behind a flag.**

## Recommended sequencing

1. **Shipped Round 155 (safe):** the insight layer — compound-risk correlation and deterministic driver-specific next actions. Zero change to scores/bands, so no calibration needed.
2. **Shipped Round 156 (safe, opt-in):** the magnitude-first component fixes above, behind the `magnitude_first` profile. Default stays `legacy` → zero oracle churn, zero test change. The improved logic is now real, tested, and one flag away.
3. **Next, on the work machine with live data (a dedicated round):** turn on `ADOPTIQ_RISK_SCORING_PROFILE=magnitude_first`, render the Brian-Frazier 90-day report both ways, and **compare band distribution against real escalation/renewal outcomes** — does the magnitude-first ranking put the customers who actually escalated/churned higher? If yes, regenerate the offline oracles with full per-value justification and **promote `magnitude_first` to the default**. Only then does it change what a CSM sees.
4. **Consider capturing outcomes** (did this customer escalate/churn/renew?) so weight tuning is calibrated against ground truth rather than judgment — the single biggest thing that would make the scoring *provably* insightful rather than plausible. This is the back-testing standard the industry uses ("if a health score doesn't flag red 60+ days before a churn event, it's not ready for production").

## Bottom line

The engine was not broken, but it was not yet *amazing* at its core job of ranking. The proportion-based components were a genuine, evidence-backed flaw that could misorder the call list. That fix is now **built and tested** (the `magnitude_first` profile) — and gated behind a flag precisely because it moves every customer's band and must be proven against live outcomes, not shipped on formula aesthetics. Default behavior is untouched; one live comparison on Brian-Frazier data promotes it. The insight layer that could be made amazing with **zero** risk — the compound-risk correlation and the specific, per-customer next action — is already the default. Net: the logic is meaningfully more insightful today, and the one change that needs real-world proof is staged and one flag from done.

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
