# AdoptIQ — Round 155 Handoff: Actionable Customer Intelligence

**Theme:** the reports are now concise and honest (Rounds 152–153). This round makes them *deep* — surface the rich CSOne + TAC troubleshooting intelligence the app already computes but the concise decision report throws away, so a CSM opens one page and truly understands the customer.

Paste the prompt below into Cowork. Use **Fable 5, very high reasoning**. This is a report-structure round: it touches `Chart_Data`/oracle digests and the visible-table contract, so it needs the discipline Round 153 used (failing-test-first, per-value-justified oracle re-pin, renderer + `_expected_visible_word_tables` in lockstep). Ideally run alongside or after the Round 154 live pass so the new sections can be validated on real Brian-Frazier-class data.

---

## The grounded finding (why this round exists)

The concise decision report (`decision_report_delivery.build_concise_word_document`, the single renderer for all eight families since R142) surfaces support/troubleshooting data as **counts only**:

- `kpi.tac_cases` and `kpi.bems` — that's it. `grep -ciE "theme|cluster|correlat|be_priority|focus_area|recurring" decision_report_delivery.py` returns **2**, and both are the generic chart "category" grouping, not case analysis.

Meanwhile the app *already computes* far richer intelligence — it just lives in the legacy path the R142 rewrite bypassed:

- **TAC category / product breakdown** — `leader_report_generator.py:5956-6083` builds `tac_category_counts` and renders a "TAC Category Breakdown" paragraph; `:2822` classifies by product (WxCC/CCaaS/etc.).
- **The AB↔TAC compound-risk correlation** — `adoptiq_backend.py:12311` is an explicit AI **CORRELATION MANDATE**: *"connect adoption barriers with TAC case themes and external bugs… if a customer has both barriers and cases about the same technology area, that is a compound risk signal. Identify the single most impactful action that would address the largest cluster of connected issues."* This is exactly "truly understand the customer" — and it never reaches the concise report.
- **BE-priority focus areas** — `be_priority_scorer.compute_be_focus_areas` clusters barriers by `(sub_technology, ab_category)` with a content-signal regex (blocker/outage/cannot/urgent) and emits the 5–10 most important themes per technology. Shipped in the *legacy* Leader/Comprehensive, not the concise decision report.
- **Rich per-case fields** — `data_normalization.py:1724` maps `Problem Details`; severity/lifecycle normalization at `:1168-1194`; product/sub-technology at `adoptiq_backend.py:946-986`. The signal to say *"3 cases, all Webex Calling call-quality, same area as the top barrier"* is in the data.

So a manager reading today's Leader report sees **"TAC Cases: 3"** when the app could tell them **"3 open TAC cases — all Webex Calling call-quality, the same technology as this customer's top adoption barrier; this is a compound Contact Center risk. One action — escalate the call-quality root cause — clears the largest connected cluster."** That is the difference between a status report and customer success no one else can deliver.

This is the same orphaned-capability pattern Round 153 found with trends: the intelligence exists; the concise renderer doesn't surface it.

---

## Part A — Verify Brian's concerns are 100% addressed (do this first, it's cheap)

Brian Frazier's ACC 90-day report is the canonical benchmark throughout the codebase. His documented concern (Round 152 prompt, `CLAUDE.md`): the Leader report must be a **concise decision document** — prioritized metrics, risks, trends, charts, evidence, tracked Action Plans — with **raw activities/records in the paired Source Data workbook, not pages of Word prose**.

Confirm each is now true on a real Brian-Frazier-class render and record it explicitly:

1. **Concise, decision-oriented** — ✅ shipped R142/R152; verify the Word body stays within budget and reads as decisions, not a dump.
2. **Prioritized** — ✅ R153 Tier 1 added the "Top risk drivers" column (per-customer *why*). Verify it reads well live.
3. **Whole team visible** — ✅ R153 Tier 2 (member roster uncapped to 15). Verify a real team shows every member.
4. **Honest source state** — ✅ R152/R153 (freshness fails closed, coverage warnings disclosed).
5. **Accurate customer identity** — ✅ R153 Tier 4 (alias registry). Verify on a real cross-variant org.
6. **Trends / "is it getting better?"** — ❌ **still open.** Brian's ask included *trends*; the concise report has none. `leader_report_generator.py`'s `_add_period_delta_section` / `_add_aging_section` exist but are orphaned (`leader_report_components` imported by nothing). **This round should close it** (Part B item 3).
7. **Raw records separated** — ✅ 16-sheet Source Data workbook.

If any of 1–5 fails live, fix it before adding new sections.

---

## Part B — Surface the actionable intelligence (the core of the round)

Add to the concise decision report, in priority order. Each must come from the **canonical** analysis (never recompute a count/score inline; never let the LLM invent a number), render concisely (this is still a decision doc, not the workbook), and stay evidence-linked.

### B1 — Support/TAC themes, not just a count (highest value)
Under the KPI snapshot, add a short "Support & Troubleshooting" block: for the scope's open TAC cases, the top 2–3 **themes by product / sub-technology** and severity mix — e.g. "Webex Calling — call quality (2 P1)", "Control Hub — provisioning (1 P2)". Source the clustering from the existing legacy logic (`leader_report_generator.py:5956+`) or lift it into `canonical_metrics` as a canonical helper so both paths share it. Cap at the top themes; the full case list stays in `TAC_Cases`.

### B2 — Compound-risk correlation (the "unlike anyone else" feature)
For each top-risk customer, when they have **both** an adoption barrier and a TAC case in the same technology area, surface one line: *"Compound risk: {tech} — {N} barriers + {M} cases; recommended single action: {…}"*. The AB↔TAC correlation mandate already exists as an AI instruction (`adoptiq_backend.py:12311`); make it a **deterministic** structured signal (grouped by `sub_technology`) so it's canonical and auditable, and let the AI only phrase the recommended action from that structured evidence. This is the single most differentiating addition.

### B3 — Trends / period-over-period (closes Brian item 6)
Revive the orphaned `_add_period_delta_section` / `_add_aging_section` / `_add_customer_health_section` as canonical series in the concise report: "Open APs 7 (up from 4)", "3 stalled >30 days", an age-band on the AP chart. Add `action_plan_age_band_series` to `canonical_metrics` beside `action_plan_chart_series`; emit it as a second series under the AP chart's `Chart_ID`.

### B4 — Use the richer CSOne fields
Where `Problem Details` / product / software-version / contract-type are available, let the per-customer drivers name the *specific* problem area, not just "N cases". Keep it one clause; the detail stays in the workbook.

---

## Guardrails (same as Round 153)

- Counts from `canonical_metrics.py`, scores from `risk_scoring.py` — never inline, never LLM-decided. New themes/correlation must be **deterministic canonical helpers**; the LLM may only phrase actions from structured evidence.
- Preserve the exact 16-sheet workbook contract, source-state semantics, evidence links, and the `validate_cross_artifact_contract` / `validate_word_semantics` gates. New Word tables → update `_expected_visible_word_tables` **in lockstep** (`display_count` is defined twice — renderer ~4109 and contract ~4617 — both move together).
- New chart series change `Chart_Data` digests → **honest oracle re-pin**: add a failing fixture case first, regenerate the four scopes, and justify **every** moved value in `QUALITY_AUDIT.md`. Do not blind-bump. (The shipped fixture is Acme/Beta/Gamma; a themes/correlation feature likely needs a richer fixture — add one that exercises a compound Webex/Contact-Center case + barrier.)
- Do not weaken any test or acceptance check. If one encodes old behavior, say so and justify.
- Keep the report **concise** — this round adds *signal*, not length. Budget is 1,500 words; each new block is a few lines. Raw records stay in the workbook.

## Validation

Per section: focused tests, ruff, bandit. End of round: `make verify` (Py 3.11, floor 7,001+), four-scope 23/23, decision-report acceptance 4×2, `r114_audit_reports.py --auto`, and — critically — **render a real Brian-Frazier-class report and read it as a CSM**: does the Support & Troubleshooting block, the compound-risk line, and the trend actually tell you what to do? That human read is the acceptance test for "actionable."

## Deliverables

Round 155 entry in `QUALITY_AUDIT.md` (Part A verification table + Part B changes + every moved oracle value justified + live-vs-offline labels) and `ROUND155_RESULT.md`. Commit to `claude/round155-actionable-intelligence`; bundle + push. Do not merge to `main` before the Round 154 live pass.

---

**Why this is the right next round:** Rounds 152–153 made the report trustworthy and concise — necessary, but they made it *safe to read*, not *worth reading twice*. The customer intelligence that makes AdoptIQ singular is already computed and sitting in the legacy path. Surfacing it — themes, compound risk, trends — is what turns "TAC Cases: 3" into customer success no one else can deliver. It's grounded, it's mostly wiring-existing-capability, and it directly answers both of Brian's concerns and the "use all this data to truly understand the customer" mandate.

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
