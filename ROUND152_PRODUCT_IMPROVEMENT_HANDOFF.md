# AdoptIQ — Round 152 Product Improvement Handoff

**Branch:** `claude/round152-product-excellence`
**Starting SHA:** `0a50e59adc20179048d851703482ffded2ff7a26` (Round 151 docs-only branch, which contains `main` @ `9907db1389fba05d87bcd5ca3d91227a3b33928f`)
**Environment:** Anthropic Cowork cloud sandbox, Linux, **Python 3.11.15**, fresh venv from `requirements.txt`
**Evidence label:** everything below is **`offline_fixture` / sandbox** evidence. There is **no `live_cisco_sources` evidence in this round** — this environment has no VPN, Snowflake, Keeper, CSConsole, CSOne/OneDrive, or CircuIT access.

---

## 1. Why this round exists

Round 151 ran the existing gates, found them green, and correctly changed no code. That is the right outcome for a *verification* round — but it means the gates themselves were the only thing looking at the product. Round 152 audited AdoptIQ the way five different people would: a report analyst, a product designer, an accessibility engineer, an SRE, and a security reviewer, reading the actual source and the actual rendered artifacts rather than re-running the suite.

That audit found **23 substantiated defects**. Every one had a concrete failure scenario and a `file:line` citation. Twenty-three of them are fixed on this branch, with 103 new focused tests.

The distinguishing pattern across almost all of them: **a defense that was applied to one path and not to its sibling.** The Round 148 citation whitelist was hardened on the portfolio path but not the intel path. Round 9's exception scrubbing was applied to the comprehensive worker but not the other four. The Round 71 endpoint sweep was pinned as an allowlist, so every route added afterwards silently escaped it. `_r127_cell_text` normalised the evidence *text* column but not the three header fields rendered on the same line. This is what a mature codebase's remaining bug surface looks like, and it is why a fresh audit still found P0-class issues after 151 rounds.

---

## 2. What changed, and why it improves the product

### 2.1 Security and data protection

**A1 — 14 data-bearing routes were bypassing the localhost gate.** `_SENSITIVE_ENDPOINTS` gives three things nothing else gives: the loopback-peer check, the `Host` allow-list (the anti-DNS-rebinding layer), and the `no-store`/`nosniff`/`DENY` headers. A default-deny audit of `app.url_map` found 14 registered routes in none of those layers — including `POST /api/ask-ai-portfolio/stream` (whose own docstring promises "same request schema, same throttle, same CSRF" as the gated sync sibling), `POST /api/update/apply` (triggers a verified self-replace of the installed application), `POST /api/settings/customer-aliases` (writes the alias registry that feeds canonical customer collapsing, so it changes reported counts), `GET /api/leader_scope_options` (roster member emails), and `GET /api/decision-workspace/report/<id>/evidence` (Source Data rows — its four siblings were listed, this one was missed).

The root cause was the shape of the guard test: `tests/test_round71_sensitive_endpoints_complete.py` pins a hardcoded tuple, which proves the R71 sweep has not been *undone* but says nothing about routes added *since*. Round 152 adds `_INTENTIONALLY_PUBLIC_ENDPOINTS` and a completeness test that enumerates `app.url_map` and fails on anything unclassified. **The next route added without a decision now breaks the build instead of silently opening a hole.**

**A2 — attribute-context XSS in the BST/PSIRT page.** `_safeHref` escaped its URL with a text-node round-trip, which the HTML spec defines as escaping only `&`, `<`, `>` and NBSP — double quotes survive — and then interpolated the result into `href="${...}"` at four call sites. A URL containing a quote broke out of the attribute. The wired route's own validators currently prevent a quote reaching it, but `app_simple.py` builds the same `direct_link` field *unquoted* from upstream BST/PSIRT API data in two other places; one reuse of that payload would have made this live. Fixed with a real attribute escaper, percent-encoding on both `direct_link` builders, and the same hardening on `subscription-search.js`'s similarly-named helper.

**A3 — whole customer rows were written to the log at INFO.** Three sites emitted `df.head(n).to_dict('records')` — CSConsole barrier records and CSOne support-case problem details, complete with customer names, account IDs and assignee emails — into `~/.adoptiq/adoptiq.<pid>.log`, the file the UI repeatedly asks operators to send to support. `structured_logging`'s redactor only scrubs the `extra_kv` prefix, never an f-string message body, so nothing downstream was catching them. Now logs shape (row/column counts); column names were already logged separately and are not PII.

**A4 — two workers wrote raw exception bodies into the admin console.** `run_compact_analysis` and `run_customer_renewal_analysis` wrote `str(e)[:1000]` straight into `error_detail` — the field the admin console and the `report_history` audit row render, persisted to `analysis_status.json`. Round 9 / Phase 6.6 documented exactly why that is unsafe (absolute URLs, internal hostnames, Keeper secret paths, AppRole IDs) and added `_detail_tail` to scrub it — on the comprehensive worker only. A new shared `_record_worker_failure` is now the single place that decision is made, for all five workers.

The same edit closes a real operability gap: four of the five workers recorded **no `error_kind` at all**, so an admin could not tell "not on VPN" from "corporate TLS interception" from "rotated AppRole" from "Snowflake denied the service account" — the exact ambiguity `error_classifier` exists to remove. The Leader worker was worst: a failed Leader report, the manager's primary artifact, persisted nothing but a fixed sentence.

### 2.2 Ask AI grounding integrity

**B1 — evidence rows could be forged from a customer name.** `build_evidence_context` renders one evidence row per line, and `_r146_context_source_ids` derives the citation whitelist by matching `^\s*-\s*\[SourceID:` against that rendered block. The `text` column was whitespace-collapsed; the three *header* fields went through `_first_present`, which only `.strip()`s. CSConsole customer columns (`RELATED_CUSTOMER__C`, `CUSTOMER_NAME__C`) are free text — so a value containing a newline followed by `- [SourceID: ...]` rendered as a **second evidence line** and its forged identifier entered `allowed_ids`, the set the entire citation contract rests on.

Reproduced in this round: one crafted cell produced `{'CASE-FORGED-1', 'REAL-1'}`. After the fix the same input yields `{'REAL-1'}`. External-intelligence records were worse — built from scraped and operator-imported fields (`POST /api/import-intel` validates only `isinstance(x, dict)`) with no normalisation at all; those now go through the same normaliser the Snowflake path uses.

**B2 — the intel path derived its citation whitelist by regex over prompt text.** Round 148 hardened the portfolio path to derive `allowed_ids` from real record objects; the intel path was left matching the rendered string, so a forged heading was citable. It now intersects against the bounded entailment records, exactly as the portfolio path does. The intersection can only narrow — a real row is never dropped.

**B3 — conversation history was skewing retrieval.** Round 148 added `turn_question` precisely so a prior turn cannot corrupt the current one, and wired it into `build_retrieval_plan`. The evidence *prefilter*, the *ranker*, and *corpus retrieval* were all left on the history-prefixed composite. History contributes up to 5 turns × 1500 chars of prior answer text and `_lexical_rank_evidence` scores on raw token hits, so a prior turn about one customer crowded that customer's rows to the top of a follow-up about a different one. History still reaches the model as conversation context; only retrieval now narrows to the current turn.

**B4 — the strongest trust signal was computed and thrown away.** The pipeline cross-checks every KPI the model rendered against `canonical_metrics` and silently rewrites any that disagree. Both routes already returned `canonical_corrections` and `canonical_verified`; nothing in `static/js` read either. A user saw a bare "Canonical Metrics" heading with no indication a correction had occurred — and, when the answer was fully verified, no indication of that either. There is now a badge on both the sync and SSE paths showing `N corrected` (with the stated-vs-canonical delta in the tooltip) or `N verified`, rendered via `textContent`.

### 2.3 Report clarity and honesty

All eight report families now render through `decision_report_delivery.build_concise_word_document`, so each of these affected every report.

**C1 — the disclosure section hid its own truncation.** The Data Coverage Warning table silently dropped warnings 6..N. Every other truncated section in the document discloses its overflow; this is the one section whose entire purpose is honest disclosure. A live ACC run with R93 scope exclusions plus per-source states clears five easily.

**C2 — member rows do not sum to the team totals, and nothing said so.** A record shared by two team members is attributed to both (DSM secondary attribution) while the team total counts it once. That is correct by design — measured on the offline fixture as team 4/2 vs member sum 5/3 — but it was undisclosed in both the Word report and `Metric_Lineage`, so a manager who added the column found it disagreeing with the headline and had no way to tell a design choice from a bug. Now disclosed in prose *and* as a `Caveat` on every `summary.member.*` lineage row, so the workbook is auditable without reading the Word prose. The Round 147 evidence-adjacency contract is preserved: the lineage reference is still the element immediately following the table.

**C4 — an empty account summary emitted a header-only grid** under a "Ranked by canonical risk score descending" claim, while the adjacent section printed the correct empty-state sentence. The team branch had always had an `else` for this; the account branch had none. `_expected_visible_word_tables` was updated in lockstep so the contract validator agrees.

**C5 — withheld sentinels were substituted into noun phrases**, producing prose a manager cannot parse: *"The selected scope covers Unavailable (Partial) customers and 2 team members"* and *"Known activity total: Unavailable (Incomplete coverage) distinct records"*. The withholding is correct and is preserved unchanged; only the grammar moved, so a withheld value now gets its own clause.

### 2.4 Workflow, reliability and accessibility

**A5 — a status race could silently lose the job-status file.** `analysis_status_lock` guards the outer map, but the five workers hold a direct reference to the inner dict and mutate it — including adding new keys — without the lock. Iterating that dict while another thread adds a key raises `RuntimeError: dictionary changed size during iteration`; in `save_analysis_status` the broad `except` swallowed it and the status file was silently not written for that cycle, and in `get_all_status` it returned a 500 that flashed an error on the admin tile while a job was simply making progress. The codebase had already fixed this exact class one level up for the outer map; the inner dicts were missed. Both readers now snapshot before iterating.

**A6 — "clear stuck analyses" relabelled without stopping.** The route never set `cancellation_flags`, and because the worker holds a direct reference to the same status dict it overwrote the label back to `running` on its next progress update. From the operator's seat the job "un-cancelled" itself and kept burning Snowflake and LLM budget. The 600 s threshold was also below the app's own runtime estimate (a healthy 33-customer comprehensive run is ~16 min, and the Compact worker's timeout is 1800 s), so healthy jobs were being declared stuck. Now raises the cancellation flag, defaults to 3600 s, is tunable via `ADOPTIQ_STUCK_ANALYSIS_SECONDS`, and is floored at 1800 s so it cannot be tuned into killing healthy work. `base.html` also stopped promising a navbar control that has never existed.

**E1 — the progress page was silent to assistive technology.** Status, activity, elapsed time, the progress bar and the error strip are all mutated every 2 s and carried no live region, no `role="progressbar"` and no accessible name. A screen-reader user started a report, heard the page once, and was never told it had failed. The Report Jobs panel already did this correctly; `progress.html` was the gap.

**E2 — error toasts self-destructed after 5 s.** The toast is the only channel reporting why a report failed to start. A manager who glanced away came back to a page that looked like nothing had happened — no job in Report Jobs, no error on screen, form still populated — and clicked Generate again into the same wall. Failures now persist until dismissed; success stays transient. The Leader form's toasts were never announced at all and now carry a role.

**E3 — the customer lookup failed completely silently.** With VPN or Snowflake down, a manager typed a real customer name, saw an empty dropdown, concluded the customer did not exist, and either abandoned the report or typed a free-text name that would not match CSOne — silently producing a wrongly-scoped report. In-flight, genuinely-empty and unreachable are now three distinguishable messages in a live region.

**E4** replaced developer strings shown to managers (`Server returned non-JSON content-type: text/html`, `Unexpected token < in JSON at position 0`) with plain language, keeping the verbatim message in `console.error` for support. **E5** gated the "Test JavaScript" debug button that shipped directly beneath the primary call to action. **E6** cleared `aria-busy` on the two renewal branches that leaked it. **E7/E8/E9** added a skip link and `aria-current`, `scope=` on 14 table headers with `aria-hidden` on their decorative icons, and raised the focus ring above the WCAG 1.4.11 3:1 floor with a transparent outline so it survives forced-colors mode.

---

## 3. Verification performed (all offline)

| Gate | Result |
|---|---|
| `ruff check .` | pass, 0 findings |
| `bandit -c bandit.yaml -r . -ll` | pass, 0 HIGH/MED |
| `pip-audit -r requirements.txt --strict` | clean, no known vulnerabilities |
| Full pytest (`-m 'not eval'`) | see QUALITY_AUDIT.md Round 152 for exact counts |
| Ask AI deterministic eval (`-m eval`) | see QUALITY_AUDIT.md |
| Offline four-scope artifact generation | team / member / customer / comprehensive — **23/23 parity checks each**, all exit 0 |
| 16-sheet Source Data contract | **exact, in canonical order, all four scopes**, `Evidence_Links` present |
| `scripts/run_local_acceptance_lab.py --enable-local-fixtures --scenario all` | `all_reconciled: true`, 21 scenarios, `live_validation_performed: false` |
| `scripts/r114_audit_reports.py --auto` | 0 per-cell citation clutter, 0 mid-string citations, 0 markdown chrome, 0 stub bullets, 0 global-config tokens, 0 HTML leakage, 0 nan-ish cells, no duplicate IDs, no risk saturation |
| Rendered DOCX inspection | paragraph-by-paragraph and table-by-table review of Leader Team / Member / Customer and Comprehensive |

Exploit reproductions, before and after the fix, are recorded in `QUALITY_AUDIT.md`.

---

## 4. What still requires live Cisco validation

Nothing in this round proves anything about live data. Specifically, the following must be re-established on the authorized work machine:

1. `git fetch origin main` and confirm `origin/main == 9907db1` (or re-plan from the new tip) before live work.
2. **A1 regression check.** The 14 newly-gated routes must be exercised from the packaged app to confirm no in-app `fetch()` sends a `Host` header outside the allow-list. This is the single highest-risk change in the round for a false negative offline: the gate is correct, but a client that was relying on being un-gated would now get a 403. Load every page, run one report of each type, and open the Ask AI, evidence-drawer, BST/PSIRT, playbook, customer-360 and preferences surfaces.
3. **A4 classification quality.** Force each real failure class (VPN off, TLS interception, rotated AppRole, revoked Snowflake grant) against each of the five workers and confirm the `error_kind` an admin sees is the one that actually happened, and that `error_detail` carries no internal hostname or secret path.
4. **B1/B2 against live CSConsole text.** Confirm no legitimate live customer name is mangled by header flattening (the 160-char cap and `[SourceID:` neutralisation are the two behaviours to eyeball), and that live Ask AI citation counts did not drop.
5. **B3 retrieval quality.** Multi-turn Ask AI on live data: confirm follow-ups still resolve pronouns correctly now that *retrieval* keys off the current turn while the prompt still carries history.
6. Full live report truth matrix, two passes, all eight families, with Word ↔ `Chart_Data` ↔ `Metric_Lineage` ↔ `Evidence_Links` ↔ source-row reconciliation; live `r114_audit_reports.py`; live Ask AI sync/SSE parity and provider-degradation cases. Label results `live_cisco_sources`.

---

## 5. Backlog carried forward (audited, evidenced, deliberately not fixed)

These are real findings with `file:line` evidence that were **not** implemented because each needs either live data, product sign-off, or a fixture re-pin that would be dishonest to do blind. Full detail in `QUALITY_AUDIT.md`.

**P0 candidates (need product sign-off or live evidence):**

- **Report generation time is published as verified source freshness** on Leader, Renewal and Subscription. `decision_report_delivery` documents that "attempt, evaluation, or generation time must never impersonate source freshness", but `data_as_of_utc` defaults to *now* and `data_as_of_state` defaults to `"available"`, and three call sites fall through to `_now_utc_iso_z()`. Compact and Comprehensive get this right and hard-fail instead. **Fix requires the Leader worker to record a real prefetch clock first**, so it cannot be validated offline — but this is the highest-value remaining item and should lead Round 153.
- **The decision report's customer universe bypasses the Round 132 alias registry.** `_canonical_customer_identities` joins on exact normalized label or account ID and never consults `customer_aliases`, so one organisation with cross-source name variants is counted twice, its evidence split across two partial slices (understating *both* risk scores), and duplicated in `Account_Summary`. `canonical_metrics.count_customers` *does* collapse aliases, so the two disagree. Reproduced against the shipped `customer_aliases.defaults.json`. Fixing it changes `Fact_Contract_SHA256` and the byte-identical offline oracles, which must be re-pinned deliberately.

**P1:**

- **A single `partial` source blanks every KPI while the same page prints exact counts and full detail rows** — the document says "Action Plan lifecycle rollup is withheld because the source is incomplete" directly above a table of five fully-specified plans. Needs a product decision: publish the count with the state label attached, or suppress the detail too. Either is defensible; the contradiction is not.
- **One partial contributing source blanks the entire chart, not just its series.** A live run where TAC coverage is partial loses Activity Mix, Risk Distribution *and* Trend even though 3 of 4 series are exact.
- **"Evidence-backed next action" is band-level boilerplate.** `risk_scoring` already computes genuinely customer-specific `risk_factors` ("2 critical/high adoption barriers", "1 escalated TAC case"); `grep risk_factors decision_report_delivery.py` returns nothing. The evidence is computed and discarded, and two customers in the same band get byte-identical rows.
- **A Leader Team report shows only the top 5 direct reports.** `TOP_ITEM_LIMIT_DEFAULT = 5` is never overridden per section; the roster carries 42 direct reports across 6 managers. Measured word budget usage is 597–841 of 1500, so there is ample room. The single question a Leader Team report exists to answer currently requires opening Excel.
- **The AP chart is titled "Status and Aging" but has no aging dimension**, and `leader_report_generator`'s stalled-AP and period-over-period analysis (`_add_aging_section`, `_add_period_delta_section`, `_add_customer_health_section`) is orphaned — `leader_report_components` is imported by nothing.
- **Cancellation is a no-op on Renewal and Subscription** (zero `check_cancellation` calls) and effectively on Comprehensive (its per-customer checkpoint sits inside a loop over a list that is emptied at `app_simple.py:20884` and restored after the loop). `decision_report_delivery` — which now does the long work — has zero cancellation awareness.
- **Corpus retrieval is not manager-filtered.** Team-scope Ask AI queries the corpus with no manager predicate although the schema carries one, so a chunk from another manager's report is citable with no attribution.
- **Provider failure taxonomy is collapsed and the actionable branch is dead code.** `_r147_public_ai_error_message` has a well-written `provider_timeout` / `rate_limit` / `unavailable` / `malformed` cascade that no production path can reach, because `generate_llm_json_response` maps every failure to one opaque reason and `_grounded_failure` passes `.get("error")` rather than `.get("reason")`.
- **The admin console stores per-report source state and never renders it.** `partial_data_warnings_json` and `data_as_of_utc` are both persisted and projected into the history row dict but appear nowhere in the template, so a report that shipped with a Snowflake fetch failure looks identical to a fully-sourced one.
- **CSOne provenance is invisible.** The field is labelled "Optional" and the server silently substitutes the newest `.xlsx` from the OneDrive mirror. A manager who leaves it blank expecting "no CSOne data" can get a colleague's three-week-old export for a different technology, and nothing in the UI ever names the file.

**P2:** `_r65_filter_customer_tagged_incidents` duplicated verbatim across `app_simple` and `compact_report_formatter` with no parity test (both feed the Incidents risk component, whose Compact↔Renewal parity is an explicit R67/B1 contract); ~590 lines of dead per-customer narrative code inside the 3,972-line `run_comprehensive_analysis`; `save_analysis_status` holds the global lock across `json.dump` + `os.replace`; no cap on concurrent report threads; `MEDIUM` vs `MODERATE` band vocabulary split reintroduced at the display layer by the R142 rewrite; ~12% of the Word budget spent on machine identifiers; documented 5,000-word budget vs enforced 1,500.

---

## 6. Best next-round objective

**Round 153 should be the source-freshness truth round, run on the authorized work machine.**

Take the two P0 candidates above — `data_as_of` impersonation and the alias-registry bypass — and close them together, because they are the only two remaining findings that can make a *correct-looking* report *wrong*. Everything else on the backlog makes the product less useful, harder to operate, or less pleasant; these two make it quietly inaccurate, and both are invisible to every gate the project currently runs. Both need live data to fix safely: the freshness fix needs the Leader worker to record a real prefetch clock, and the alias fix needs a deliberate re-pin of `Fact_Contract_SHA256` and the offline oracles against known-good live output.

Do the A1 live regression check (Section 4, item 2) first, in the same session, since it gates shipping this branch at all.

---

**Trailer:** Made-with: Claude Opus 5 (Cowork cloud sandbox)
