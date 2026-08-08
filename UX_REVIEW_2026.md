# AdoptIQ — UI/UX Review

Focused experience review (Fable 5). Round 152 already did the deep accessibility/security audit and its fixes shipped (skip link, live regions, focus rings, `th scope`, persistent error toasts, plain-language errors, typeahead feedback). This pass is about the *experience quality* on top of that — what a non-technical Customer Success manager feels using it.

## What's genuinely good (keep it)

- **Report-type selection is clear.** Each of the five cards has a title *and* a one-line purpose — "Decisions, exceptions, and accountable next actions for a team, member, or customer" (Leader), "A fast executive briefing focused on the exceptions that need attention" (Compact). A manager can pick without guessing. This is better than most enterprise tools.
- **The scope preview (R146)** tells the user what they'll get — report, scope, window, sources, deliverables — before they click Run. That closes the biggest "what will this do?" anxiety.
- **Empty states exist and read well** — "No reports are running right now. Start a report and it will appear here." Report Jobs and History both have them.
- **Consistent design system** — Cisco tokens, one card style, dark/light with early-paint (no flash). After R152 the focus ring, skip link, and `aria-current` are correct.
- **The Round 153 "Top risk drivers" column** now makes the Leader report's decision table *say why*, not just *what* — that's the single biggest experience upgrade to the actual deliverable.

## Prioritized opportunities

### P1 — First-run guidance
A first-time manager lands on a dense page: an Intelligence banner, a Report Jobs panel, then a long form. Nothing says "start here." The report-type cards are the natural entry point but sit below the fold of attention.
**Fix (small):** a one-time dismissible hint anchored to the report-type section — "New here? Pick a report type, choose your scope, and click Generate." Store dismissal in a cookie (not localStorage — artifacts constraint doesn't apply to the app, but the app already avoids it). ~15 lines, no new deps.

### P1 — The primary action's visual weight
"Generate Report" is the one thing every visit exists to reach, but it competes visually with the Intel banner, the jobs panel, and the form chrome. On a first read the eye isn't pulled to the path → CTA.
**Fix:** make the Generate button the clear visual anchor (size/color/position sticky at the bottom of the form on scroll), and de-emphasize the Intelligence banner to a collapsed strip once configured. Low risk, high perceived-polish.

### P2 — Progressive disclosure on the form
The form shows manager → technology → scope → (member/customer/subscription) → CSOne → run. The customer/subscription sub-sections already hide/show, which is good. But the whole form is visible at once, which reads as "a lot to fill in" to a non-technical user.
**Fix:** consider a light stepper feel — dim/disable later sections until the prior one is set (manager gates technology gates scope). The data dependencies already exist ("Select a manager first" options prove it); surfacing them as visual gating turns a long form into a guided path.

### P2 — Report History discoverability
The improved Leader report is the payoff, but a returning user's path back to a prior report (History / Previous Reports / Report Jobs) is spread across three surfaces with overlapping purpose.
**Fix:** unify into one "Your reports" surface with running + completed in one list, or make the naming/relationship explicit so the user isn't guessing which of the three to open.

### P2 — The partial-data experience
When sources are partial, the report honestly says "unavailable" (good, and R153 made freshness honest too). But in the UI, a manager who gets a partial report has no *in-app* explanation of *why* it was partial or *what to do* — they'd have to open the workbook or the admin page. R152 added the freshness/trust vocabulary; the gap is surfacing it at report-completion time in plain language ("2 of 6 sources were incomplete — TAC and Pulse. The report shows what's verified.").

### P3 — Micro-polish
- The Tier 1 driver text can read "1 escalated TAC **cases**" (plural with count 1) — a small grammar nit inherited from `risk_scoring`'s factor strings. Worth a singularization pass if that module is ever touched (don't touch SSoT just for this).
- "Generate Report" spinner + disabled state is handled; consider an inline time estimate ("Comprehensive reports take ~15 min for a large portfolio") so a manager doesn't wonder if it stalled.

## What I did NOT re-flag
The accessibility and DOM-safety items from Round 152 (F-01..F-20) are fixed and shipped; this review does not repeat them. Responsive layout was clean at 1280/1440 in the R152 audit and I saw nothing new.

## Honest scope note
This is a focused review against the templates and the R152 audit, not a live click-through of the running app (no browser session this pass). The P1/P2 items are design-level and low-risk; none are defects, they're the difference between "works well" and "feels effortless." If you want, a future Fable round can implement the two P1s (first-run hint + primary-action weight) — together they're ~1–2 hours and the most visible polish per token.

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
