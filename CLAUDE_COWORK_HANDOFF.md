# AdoptIQ — Claude Cowork Live-Validation Handoff (Round 151)

Paste the prompt below into Claude Cowork. Use **Fable 5** with **Very High reasoning**. Start in its planning/read-only mode if it has one, review the repository and the plan, then execute the work in the same task after the plan is sound. Do not use an auto model for this round: the work spans security-sensitive Cisco integrations, report accuracy, generated Office artifacts, and a large existing codebase.

---

You are taking over AdoptIQ, Cisco Customer Success's renewal-risk and adoption-intelligence desktop app. Work from the current `main` checkout and treat that commit as the only baseline. First run `git fetch origin main`, confirm a clean worktree, and record `git rev-parse HEAD` plus `origin/main`. Do not reset, clean ignored files, alter remotes, expose credentials, copy customer data into Git, or work from an older handoff branch. If `origin/main` advanced, stop and re-plan from its new tip.

## Mission

Close the remaining gap between strong local fixture evidence and trustworthy Cisco live-source evidence. The product must be useful to managers and accurate enough to make decisions from:

- Every report must be detailed but not overwhelming. The **Leader report** was the manager's main concern: it needs a concise decision document for Team, Member, and Customer scope, with clear prioritized metrics, trends, risks, charts, evidence references, and tracked Action Plans. Raw activities/records belong in the paired Source Data workbook, not in pages of repetitive Word prose.
- Comprehensive, Compact, Renewal (portfolio and individual), and Subscription reports must be evaluated separately—not cloned from Leader—and must agree on any shared canonical KPI, risk score, count, source state, and reporting window.
- Every visible claim must be traceable to canonical facts and to exact source/evidence rows. AI can summarize supported facts; it must never create or override canonical values.
- The paired decision-report contract is **one concise Word document plus a 16-sheet Source Data workbook**: `Report_Info`, `Metric_Lineage`, `Chart_Data`, `Evidence_Links`, `Action_Plans`, `Adoption_Barriers`, `Customer_Pulse`, `TAC_Cases`, `BEMS`, `Subscriptions`, `Success_Priorities`, `External_Incidents`, `External_Bugs`, `Risk_Components`, `Member_Summary`, `Account_Summary`. Do not regress `Evidence_Links` to the obsolete 15-sheet variant.

## Read first

1. `CLAUDE.md` — non-negotiable project/security/reporting invariants.
2. `QUALITY_AUDIT.md`, especially Round 149 through Round 150 — exact test history, known limitations, and local artifact locations.
3. `CODEX_HANDOFF_PROMPT.md` and `HANDOFF_PROMPT.md` for architecture and historical context. Their old commit/build labels are historical; current Git state wins.
4. `canonical_metrics.py`, `risk_scoring.py`, `decision_report_delivery.py`, `canonical_report_adapter.py`, `leader_scope.py`, `ask_ai_grounded.py`, and their focused tests before changing behavior.

## What Round 150 added and what it did NOT prove

Round 150 made guarded local-acceptance and several harness/core paths Python 3.9-compatible. It passed the local fixture lab (21 reconciled source states), local HTTP acceptance (21/21), offline decision-report acceptance twice (Team/Member/Customer/Comprehensive, repeatability green), local Ask AI acceptance, and a strict four-scenario report-matrix sanity check. It also passed the focused compatibility test, Round 149 tests (8 passed, 1 skipped), Ruff, and Bandit HIGH/MED. The full local adapter suite can stall after 15/21 tests in the Python 3.9 sandbox; do not claim it green unless it actually finishes.

This is **not** live validation. No Round 150 run proved live Snowflake, Keeper, CSConsole, CSOne/OneDrive, CircuIT, current roster ownership, source freshness, customer scoping, or live AI quality. `pip-audit` also has a Python 3.9 resolver limitation for `truststore>=0.9.0`; distinguish that environment condition from a real dependency vulnerability.

## Mandatory guardrails

- Preserve existing Keeper/Snowflake connection chains, parameterized SQL, table allowlists, cursor cleanup, aliases, manager/technology/customer authorization, DSM secondary attribution, and fail-closed handling for empty or ambiguous scopes. Never substitute synthetic data in normal runtime or loosen scope to make a report pass.
- Keep loopback defaults, CSRF, HttpOnly/SameSite cookie protections, secure public-bind behavior, redacted provider errors, and corpus/OneDrive guards. Do not log raw customer rows, prompts, secrets, tokens, query text containing sensitive values, or provider diagnostics.
- Counts come from `canonical_metrics.py`; risk scores from `risk_scoring.py`; workbook/export schema from the existing schema modules. Do not recompute them inline in a formatter or ask an LLM to decide a number.
- Keep Action Plan lifecycle, TAC/BEMS distinct-ID collapsing, source availability/partial/zero semantics, source warnings, and metric lineage intact. A populated chart series that cannot render should fail publication, not silently become an empty chart.
- Do not weaken tests, add source-marker-only compatibility code, bulk reformat large modules, regenerate source data, publish artifacts, alter `latest.json`, tag, release, or deploy over the installed production app without explicit approval.

## Execution plan

1. **Baseline and offline gate.** Capture the clean baseline. Run `make verify` with the host-supported Python and record exact results. Run the previously incomplete `scripts/run_round146_acceptance.py local --output-dir <ignored/redacted path>` to a final summary. If a suite fails or stalls, classify each result as product defect, stale test, environment/dependency issue, or live prerequisite—never hide it. Re-run the narrowest relevant test after every repair.

2. **Live connection preflight (authorized work machine only).** With existing approved Cisco configuration and VPN, start the normal application—not the local-fixture process. Verify `/ping`, `/api/version`, `/api/status/all`, `/api/diag/connectivity`, corpus/intelligence status, DSM diagnostics, and source warnings. Confirm a configured manager, 90-day window, roster-authorized member, unambiguous customer, authorized subscription, and reviewed CSOne workbook. Keep output redacted. If VPN/DNS/Keeper/CircuIT/Snowflake is unavailable, record that as a blocker and continue only offline; do not fabricate a pass.

3. **Live report truth matrix, two passes.** Generate and inspect twice: Leader Team, Leader Member, Leader Customer, Comprehensive, Compact, Renewal portfolio, Renewal individual customer, and Subscription. Exercise the existing A–G report-option families where configured. For every paired output, reconcile Word KPIs and all four chart series—Activity Mix, Action Plan Status/Aging, Risk Distribution, Activity Trend—against `Chart_Data`; reconcile each claim to `Metric_Lineage`, `Evidence_Links`, and the precise source row. Check source date/window, source state, stable-ID deduplication, Action Plan open/completed/blocked/due-soon/overdue/unknown counts, TAC/BEMS collapse, Member/Account summaries, and no shared-attribution inflation.

4. **Manager-product review.** Review DOCX/XLSX visually, not just programmatically: pages, headers/footers, chart labels and legends, clipping, blank pages, readable Word hierarchy, data-source references, table accessibility, raw-record separation, and exact 16-sheet inventory. The Leader report must be concise but drillable; the Source Data workbook must make every summary auditable. Review Comprehensive for depth without overwhelming raw-record repetition. Run `python3 scripts/r114_audit_reports.py --auto` and retain only sanitized results.

5. **Ask AI and all AI surfaces.** Test sync and SSE streaming parity, multi-turn history, Team/Member/Customer/subscription scope binding, report-bound frozen facts, citations/evidence drill-down, freshness/partial/stale disclosures, unanswerable questions, Ask Intel/corpus search, and provider timeout/rate-limit/content failures. Verify that both success and failure envelopes are safe and that all factual answers are entailed by retrieved evidence. Run the existing AI acceptance runner twice and compare deterministic facts/evidence; label results `live_cisco_sources`, never `offline_fixture`.

6. **Repair only real findings.** Use a new `claude/round151-live-accuracy` branch. Make small focused commits with regression tests. Before each commit scan only the changed range for secrets/customer data/generated artifacts and keep a redacted summary. Do not merge or push to `main` automatically.

7. **Closeout.** Append a Round 151 section to `QUALITY_AUDIT.md` with the baseline SHA, exact commands, pass/fail/skipped counts, live-vs-offline labels, every reconciliation result, artifact-audit result, remaining risks, and any deferred Windows packaging. Update this handoff only if facts changed. Present the full diff and a concise go/no-go recommendation. Stop before production deployment, release-manifest modification, publishing, or mainline merge; request approval for those separate actions.

## Success definition

The work is complete only when there are zero newly introduced failures; all targeted reporting, scope, evidence, AI, and security tests pass; the live matrix proves report-to-workbook-to-source reconciliation for authorized scopes; all reports are visually usable and not overwhelming; Ask AI is grounded and safe; and any inherited or blocked failure has an exact, honest classification. Local fixture success alone is never sufficient to call the production app accurate.

At the end, provide: (1) exact commit/range, (2) a per-report verification table, (3) a live/offline evidence table, (4) fixes made and regression tests, (5) unresolved blockers and risk, and (6) the single next approval needed. Keep all sensitive artifacts outside Git.

---

**End of Claude Cowork prompt.**
