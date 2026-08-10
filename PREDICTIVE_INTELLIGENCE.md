# AdoptIQ — Predictive Customer Intelligence (Round 160)

**The goal, stated honestly:** tell a CSM what a customer is likely to do *before they do it* — specifically, **"will this customer open a new P1/P2 TAC case or BEMS escalation in the next 30 days?"** — with claims that are earned from the customer's own history, never asserted by a model's confidence. This document is the method, the evidence base, the leakage discipline, and the calibration lifecycle.

## Why this design (and not an ML black box)

A four-agent research sweep + foreground review converged on the classical, auditable architecture — the same one regulated industries use when scores must survive scrutiny:

- **Escalation history is the #1 validated predictor.** The IBM/UVic field study (Montgomery & Damian; 2.5M tickets, 10K escalations, 87% recall) found customer-level features dominate: open-case load, historical escalation ratio, recency. Production systems (SupportLogic) threshold exactly these ("4+ escalations in last 90 days").
- **Trend beats level.** ServiceNow's escalation model "only became truly predictive" after adding trend features; practitioner consensus puts rising 30-day case velocity vs the customer's own baseline at 30–60 days of lead time.
- **Severity dynamics beat severity level** (new P1/P2 in 14d; P1/P2 share rising vs the customer's own baseline).
- **Sentiment trajectory leads outcomes by ~4–9 weeks** (two consecutive pulse declines ≈ 29-day lead in a 10K-account study); staleness is neutral, never healthy.
- **A 30-day horizon matches the evidence window** — nearly all quantified lead times land in 18–63 days.
- **Explainability wins ties**: the 2025 Journal of Marketing Analytics survival-analysis study deliberately rejected black boxes for model-intrinsic explanations; a small portfolio (20–500 customers) cannot honestly fit a free multivariable model anyway (van Smeden et al. 2019; Riley pmsampsize) — so we pre-register ~7 constrained feature families instead of pretending to train.

**Architecture:** discrete-time hazard framing (person-period snapshots) expressed as a transparent additive **points scorecard** (credit-scoring machinery: PDO scaling, banded calibration), pure pandas/numpy, zero new dependencies, every point traceable to a named signal and a dated record.

## The engine (`predictive_signals.py`)

**Feature families** (pre-registered; ~134 max points; all computed strictly as-of T):

| Family | Signals | Research basis |
|---|---|---|
| Escalation recency & frequency | days since last P1/P2∪BEMS event (deduped stream); count in 90d | IBM/UVic #1 predictor; SupportLogic 4+/90d |
| Case velocity & acceleration | 30d opens vs own 90d baseline; acceleration flag | ServiceNow trend features; 30–60d lead |
| Severity dynamics | new P1/P2 in 14d; P1/P2 share shift vs baseline | IBM severity-transition features |
| Open backlog & aging | severity-weighted open cases (date-reconstructed); oldest open P1/P2 age | SupportLogic productized aging |
| Pulse trajectory | latest level; consecutive declines; staleness→neutral | ~29–63d sentiment lead studies |
| Barrier events | barriers opened in 90d; critical/high opened | dated events only (see leakage rules) |
| Compound tech | dated barrier + ≥2 dated cases on same technology in-window | R155 correlation; SupportLogic product concentration |

**Tiers:** LOW / MODERATE (≥22) / ELEVATED (≥45) / CRITICAL_WATCH (≥70). Uncalibrated tiers are a **relative ranking**; the UI says so in the same sentence.

**The leakage discipline (non-negotiable, adversarially hardened):** features may read only fields that carry their own dates. Case open/close timestamps, pulse dates+scores, barrier open dates — yes. Barrier *status*, action-plan *status*, renewal-risk *category*, and the lifetime-max **"Highest Priority"** field — **no**: a current export cannot say what a mutable field held at a historical cutoff. Openness at T is reconstructed from open/close dates, never from a status column. One shared convention: **features ≤ T; labels strictly (T, T+30]** — enforced by two functions and proven by tests that inject future records and assert bit-identical features.

Two subtleties a three-agent adversarial review surfaced (both fixed, both regression-tested):

- **BEMS designation is mutable and undated** (Transaction ID / refs / free text), so it is **excluded from historical labels and features** — backtest labels are *new P1/P2 case opens* only. Production scoring at T=now keeps BEMS, where current state is genuinely current.
- **Severity-at-open is an approximation the data cannot avoid**: the export stores each case's *current* severity, so a case opened P3 and upgraded to P1 is backdated to its open date in historical cutoffs. This is disclosed in the audit block of every backtest run; the live round should quantify the upgrade share from CSOne case history if available.

**Cold start:** < 60 days of dated history → the engine returns *nothing* ("insufficient history"), never a confident LOW.

## The backtest (`scripts/backtest_escalation_forecast.py`)

Time-travel evaluation on real history — the receipts machine:

- Weekly cutoff grid per customer; **headline metrics on the non-overlapping subset** (spacing derived from the actual stride so it always exceeds the 30-day horizon; overlapping windows fake precision).
- **Pass the export timestamp as `--data-end`** — it anchors censoring. Without it the max record date is used, which one future-dated typo could poison; the audit block discloses which source anchored the run.
- Exclusions with reason codes, disclosed in the audit block: `censored_window` (within 30d of data end — labels unknowable), `already_escalated_at_T` (open P1/P2 at T, date-reconstructed — predicting an ongoing fire is not early warning), `insufficient_history`.
- **Two-level metrics, never mixed:** event-level recall at TWO honest moments — the last cutoff before the event (most signal) and the first warning cutoff (~30d out, the harder criterion) — vs snapshot-level precision (Wilson intervals attached).
- **Lift@top-20%** (fight-churn calc_lift logic, MIT attribution) + rank AUC as secondary only (ROC flatters rare events; the PR-side numbers are the honest view).
- **Mandatory baselines** — the scorecard must beat: (a) base rate, (b) "any escalation in trailing 90d" heuristic, (c) the existing deterministic 0-100 risk score as a ranker, in BOTH scoring profiles (this doubles as the Round 156 magnitude_first live comparison). If it can't beat them, the report says so.
- **Calibration table:** per-tier observed rates with Jeffreys smoothing ((k+0.5)/(n+1)), Wilson 95% intervals (Brown, Cai & DasGupta 2001 — never Wald), PAVA monotonicity (Barlow 1972), and the Pluto & Tasche most-prudent **upper bound** for zero-event bands (a band that never escalated reports "could be up to X%", never "0%").

**The honesty ladder** (Van Calster et al. 2019 calibration hierarchy, adapted):

| Unique escalation events in history | What the product may claim |
|---|---|
| < 10 | Directional ranking only; all numeric claims suppressed |
| 10–29 | Banded rates WITH intervals + low-confidence badge |
| ≥ 30 | Banded rates with intervals ("X of N historical customer-periods like this escalated within 30 days") |

Continuous calibration curves and isotonic fits are **disqualified** below ~1000 calibration points (Niculescu-Mizil & Caruana 2005) — banded rates are the ceiling of honest complexity at this scale.

## Surfaces

- **Report:** "Predictive outlook (next 30 days, deterministic scorecard)" paragraph — top ELEVATED/CRITICAL_WATCH customers with named contributors and the calibration state in the same sentence. Gated on trustworthy TAC source state (offline fixtures disclose partial → renders nothing → zero oracle churn, proven by the parity suite).
- **Ask AI:** per-customer `outlook_30d` lines inside DECISION_CONTEXT (with the "not a probability" disclosure when uncalibrated), governed by the existing authoritative-block rules.

## Calibration lifecycle

1. **Shipped (now):** uncalibrated priors — research-derived point weights; relative ranking language only.
2. **Live pass (work machine):** `python scripts/backtest_escalation_forecast.py --fixture <export> --output-dir .tmp/backtest` on 90–365 days of real history → audit block + baselines + calibration artifact (JSON).
3. **Calibrated:** feed the artifact to `escalation_outlook(calibration=...)` → the report starts quoting observed rates with intervals, at whatever claim level the event count earns. **Provenance gate:** only artifacts with `derived_from: live_cisco_sources` calibrate — a fixture-smoke artifact is refused by construction, so degenerate offline numbers can never masquerade as historical evidence.
4. **Quarterly:** rerun the backtest on fresh exports; PSI > 0.25 across tier bands = recalibrate warning.

## Known limits (stated, not hidden)

- **Survivorship:** a current-customers export cannot see accounts that already churned; measured rates are scoped to surviving accounts.
- **Escalation ≠ churn:** quiet customers are low *escalation* risk but possibly high *churn* risk — the two layers are deliberately separate; churn prediction needs renewal-outcome labels the app should start accumulating (report_history + knowledge base already persist the raw material).
- **The offline fixture cannot calibrate anything** (~1 month of history; the harness refuses it by design).

## Attribution

Formulas re-implemented from published work; no code imported: scorecardpy (MIT, © Shichen Xie) — WoE/points/PSI/KS formulas; fight-churn (MIT, © 2020 Carl Gold) — observation-grid construction and lift logic; Barlow et al. 1972 / scikit-learn isotonic.py (BSD-3) — PAVA reference; Pluto & Tasche — low-default upper bounds; Brown, Cai & DasGupta 2001 — interval choice; Van Calster et al. 2019 — calibration claim hierarchy; Montgomery & Damian (IBM/UVic) — escalation feature evidence.

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
