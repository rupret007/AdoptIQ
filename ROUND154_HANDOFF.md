# AdoptIQ — Round 154 Handoff: the Source-Freshness Truth Round

Paste the prompt below into Claude Cowork. Use a **frontier model with very high reasoning** — this round touches the canonical fact contract, the offline oracles, and live Cisco sources, and a mistake here makes reports quietly wrong rather than loudly broken. Start in planning/read-only mode, review the repository and the plan, then execute in the same task once the plan is sound.

**Run this round on the authorized Cisco work machine, with VPN.** Unlike Rounds 151 and 152, the core work cannot be completed in a cloud sandbox: both P0 fixes need live source data to validate, and the round opens with a live regression sweep that gates shipping Round 152 at all.

---

You are taking over AdoptIQ, Cisco Customer Success's renewal-risk and adoption-intelligence desktop app.

## Baseline

- Approved mainline: `main` @ `9907db1389fba05d87bcd5ca3d91227a3b33928f`.
- **Start from the Round 153 branch** (`claude/round153-leader-decision-value`) if it exists, otherwise `claude/round152-product-excellence`. Round 152 carries 23 defect fixes and 116 tests; Round 153 adds the offline half of the accuracy work (the alias-registry fix and the fail-closed freshness defaults) plus the Leader usefulness batch. Neither has had live validation.
- First run `git fetch origin main`, confirm a clean worktree, and record `git rev-parse HEAD` plus `origin/main`. If `origin/main` advanced past `9907db1`, stop and re-plan from its new tip.
- Create and work on `claude/round154-source-freshness-truth`. Do not modify or merge directly into `main`. Do not reset, clean ignored files, expose credentials, commit generated reports, or alter remotes.

## Mission

Round 152 closed six P0-class defects but deliberately left two alone, because both need live data to fix safely. **They are the only remaining findings that can make a correct-*looking* report wrong**, and both are invisible to every gate the project currently runs. Close them, and validate Round 152 live.

The round has three obligations, in order:

1. **Gate Round 152.** One Round 152 change has a failure mode that cannot be observed offline. Prove it live before anything else.
2. **P0-A — stop publishing generation time as verified source freshness.**
3. **P0-B — stop the decision report from bypassing the customer alias registry.**

Anything beyond these three is optional and must not compromise them.

## Read first

1. `CLAUDE.md` — non-negotiable project/security/reporting invariants.
2. `QUALITY_AUDIT.md`, **Round 152 entry first**, then Rounds 149–151 — exact test history, evidence labels, known limitations.
3. `ROUND152_PRODUCT_IMPROVEMENT_HANDOFF.md` — what Round 152 changed, why, and its full remaining backlog with evidence.
4. `decision_report_delivery.py`, `canonical_metrics.py`, `data_normalization.py`, `risk_scoring.py`, `canonical_report_adapter.py`, `leader_scope.py`, and their focused tests before changing behavior.

---

## Phase 0 — Gate Round 152 live (do this first)

Round 152 moved 14 data-bearing and state-mutating routes behind `restrict_sensitive_routes_to_localhost`, which enforces a loopback-peer check, a `Host` allow-list, and `no-store`/`nosniff`/`DENY` headers. The gate is correct, but **if any in-app `fetch()` sends a `Host` header outside the allow-list, a legitimate client now receives `403 {"error": "Forbidden: unexpected Host header"}` instead of data** — and nothing offline can detect that.

Newly gated: `ask_ai_portfolio_stream`, `ask_ai_evidence_lookup`, `get_ask_ai_suggestions`, `leader_scope_options`, `decision_workspace_report_evidence`, `customer_360_page`, `playbook_page`, `api_diag_dsm_columns`, `api_llm_ping`, `api_update_status`, `api_update_apply`, `api_settings_auto_update_mode`, `api_settings_report_defaults`, `api_settings_customer_aliases`.

Start the normal application (not the local-fixture process) and exercise every one:

- Load Analyze, History, Previous Reports, External Intelligence, Preferences, Help, Leader Report Form, BST/PSIRT search, Playbook, and a Customer 360 page.
- Run one report of each type (Leader Team, Leader Member, Leader Customer, Comprehensive, Compact, Renewal portfolio, Renewal customer, Subscription).
- Open Ask AI in both live and report-bound modes; use SSE streaming; open the evidence drawer; click through to an evidence row.
- Open the Report Jobs dashboard and the decision workspace (scope preview, report, history, compare, **evidence**).
- Check the update-status banner and the model/report-defaults/customer-alias settings surfaces.

Watch the browser network panel and the app log for any `403`. **Any 403 on a legitimate in-app request is a Round 152 regression and must be fixed in this round before proceeding.** Record the sweep as a table (route → exercised how → result).

Also in Phase 0, force each failure class against each of the five report workers — VPN off, corporate TLS interception, rotated AppRole, revoked Snowflake grant — and confirm that the `error_kind` an admin sees matches what actually happened, and that `error_detail` contains no internal hostname, absolute path, or secret fragment. This is new behavior on four of the five workers (`_record_worker_failure` in `app_simple.py`) and has only fixture evidence.

Then run `make verify` on the host-supported Python and record exact results. **Use Python 3.11**: the Round 150 `pip-audit` failure was a Python 3.9 resolver limitation on `truststore>=0.9.0`, not a dependency vulnerability. The Round 152 floor is **6,974 passed / 8 skipped / 14 deselected**.

---

## Phase 1 — P0-A: source freshness must never be impersonated

### The defect

`decision_report_delivery.py:2054` states the contract in the code itself:

> `as_of_utc` is only the public source-retrieval clock and may be blank; attempt, evaluation, or generation time must never impersonate source freshness.

The defaults violate it. At `decision_report_delivery.py:1770-1771`:

```python
data_as_of_utc: Any = None,
data_as_of_state: str = "available",
```

and at `:1785`, when `data_as_of_utc` is `None`, `public_as_of_utc` falls back to the evaluation timestamp. The Word subtitle then renders the *verified* branch at `:4060` — `f"Data as of {public_as_of.strftime('%Y-%m-%d %H:%M UTC')}"` — with state `available`. The two honest branches at `:4072` and `:4076` ("Data as of unavailable …") become unreachable for any caller that omits the argument.

Three call sites omit it:

- **Leader** — `app_simple.py:35024` sets `_r142_leader_as_of = datetime.now(timezone.utc)` when no prefetch clock parsed, writes it to `status["data_retrieved_at"]` at `:35027`, and threads it as `as_of` at `:35843`.
- **Renewal** — `app_simple.py:18378`: `status.get("data_as_of_utc") or status.get("data_retrieved_at") or _now_utc_iso_z()`.
- **Subscription** — worse than a fallback: `app_simple.py:32438` *unconditionally* writes `status["data_as_of_utc"] = _now_utc_iso_z()` immediately after the subscription lookup, before the data fetch completes, and `:33920` reads it back through the same `or _now_utc_iso_z()` chain.

Comprehensive gets this right and hard-fails instead — `app_simple.py:21819` raises `ValueError("Round 142 comprehensive report requires the explicit prefetch data_retrieved_at clock")`.

**Consequence.** A Leader, Renewal, or Subscription report always claims verified data freshness, stamped with the moment the report started. A manager reading a report generated from a stale or partially-failed prefetch cannot tell. This is the highest-trust claim on the title page and it is currently unfalsifiable.

### The fix

**If Round 153 ran, step 1 is already done** — verify it rather than redoing it, and start at step 2.

1. Make omission fail closed. Change the `data_as_of_state` default at `decision_report_delivery.py:1771` from `"available"` to `"unknown"`, and stop substituting the evaluation clock for a missing `data_as_of_utc` at `:1785`. A caller that supplies no retrieval clock must land on the honest "Data as of unavailable" branch, not the verified one.
2. Make the three workers supply a **real** clock. Each must record the actual `snowflake_prefetch` retrieval timestamp — the same one Comprehensive already demands — onto `status["data_as_of_utc"]`, together with `data_as_of_state`, `data_as_of_detail`, and `retrieval_attempted_at_utc`. Delete every `or _now_utc_iso_z()` fallback on this field, and delete the unconditional stamp at `app_simple.py:32438`.
3. Mirror the Comprehensive fail-closed guard once the clock is genuinely available, so a worker that loses its prefetch timestamp fails loudly rather than fabricating one.

**Do this in that order.** Step 1 alone will make Leader/Renewal/Subscription reports render the honest "unavailable" subtitle, which is *already an improvement* over a false claim — but ship it together with step 2 so the normal path still shows real freshness.

### Live validation for P0-A

Generate all eight report families twice against live sources and confirm the rendered `Data as of` on each title page matches the actual Snowflake retrieval time — not the report start time. Deliberately degrade one source (or run just after a known-stale prefetch) and confirm the report says so. Confirm `Report_Info` `Data_As_Of_UTC` / `Data_As_Of_State` agree with the Word subtitle on every pair.

### Regression tests to add

- Build facts with `data_as_of_utc` omitted; assert `facts["data_as_of_state"] != "available"` and that the Word subtitle does **not** start with `"Data as of "` followed by a timestamp.
- Source-shape pin: no `data_as_of` expression in `app_simple.py` may contain `_now_utc_iso_z()` or `datetime.now(`.
- Per-worker: a recorded prefetch clock round-trips to the rendered subtitle unchanged.

---

## Phase 2 — P0-B: the decision report must honour the customer alias registry

### The defect

`decision_report_delivery._canonical_customer_identities` (`:814`) builds the customer universe for KPIs, risk profiles, `Account_Summary`, `Member_Summary`, and the risk-distribution chart. It joins on exact normalized label (`_customer_match_key`, `:321`) or shared account ID — and **never consults the Round 132 alias registry**. The module imports `customer_names_match` at `:31` but uses it only for incident matching at `:1104`.

`canonical_metrics` *does* collapse aliases: `count_customers` (`canonical_metrics.py:422`) routes through `_r132_collapse_alias_customer_names` (`:407`). The helpers exist in `data_normalization.py` — `alias_join_keys_for_name` (`:597`), `canonical_customer_name` (`:603`), `customer_names_match` (`:616`).

So the two disagree, and the decision report publishes `"customers": len(risk_profiles)` (`decision_report_delivery.py:1899`) rather than the canonical count.

**Consequence.** One organisation with cross-source name variants — the exact NYU case Round 132 shipped for — is counted twice. Its evidence is split across two partial slices, so **both** risk scores are understated. It appears as two rows in `Account_Summary` and twice in the Risks/Decisions table. This violates the project's own SSoT rule: counts come from `canonical_metrics.py`.

### The fix

1. Seed the union-find in `_canonical_customer_identities` with `data_normalization.alias_join_keys_for_name(display)` alongside the existing account-ID union, and choose the display label via `canonical_customer_name(...)` anchored on the scoped `BU_NAME`, per the Round 132 contract.
2. Add an assertion to `validate_cross_artifact_contract` that `len(risk_profiles) == cm.count_customers(...)` for the same frames, so the two can never silently diverge again. **This assertion is the durable part of the fix** — it converts a recurring drift class into an impossible one, the same way Round 152's default-deny endpoint check did.
3. Over-merging is bounded: the registry is an explicit operator allow-list, not fuzzy matching. Do not add fuzzy matching in this round.

### The re-pin, done honestly

This fix changes `Fact_Contract_SHA256` and the byte-identical offline oracles. **Re-pinning is expected; doing it blind is not.**

The oracle lives at `tests/fixtures/report_acceptance/v1/sanitized_portfolio.json` under `expected_by_scope`. The team scope currently pins `"customers": 3` and `chart_totals.risk_distribution: 3`. Procedure:

1. Add a fixture case that exercises the defect — two records for one organisation under different names with no shared account ID, plus a matching entry in the alias registry — and assert the *pre-fix* behaviour fails.
2. Apply the fix. Regenerate the four scopes with `scripts/generate_offline_acceptance_artifacts.py`.
3. For **every** oracle value that moved, state in `QUALITY_AUDIT.md` what it was, what it became, and why the new value is the correct one. An oracle change with no stated reason is a red flag, not a green test.
4. Confirm `decision_report_delivery` and `canonical_metrics` now agree on the same frames.

### Live validation for P0-B

On live data, pick a manager whose portfolio has known cross-source name variants. Confirm the decision report's customer count equals `canonical_metrics.count_customers` for the same scope, that `Account_Summary` has one row per organisation, and that the merged organisation's risk score is *higher* than either of the two pre-fix partial scores (because its evidence is no longer split). Reconcile against the source rows in `Evidence_Links`.

---

## Phase 3 — Full live acceptance

Only after Phases 0–2 are green:

- Live report truth matrix, **two passes**, all eight families plus the configured A–G option blocks. For every paired output, reconcile Word KPIs and all four chart series against `Chart_Data`; reconcile each claim to `Metric_Lineage`, `Evidence_Links`, and the precise source row. Check source date/window, source state, stable-ID deduplication, Action Plan lifecycle counts, TAC/BEMS collapse, Member/Account summaries, and no shared-attribution inflation.
- Visual review of every DOCX and XLSX: pagination, headers/footers, chart labels and legends, clipping, blank pages, readable hierarchy, table accessibility, raw-record separation, exact 16-sheet inventory including `Evidence_Links`.
- `python3 scripts/r114_audit_reports.py --auto` on live output — **including Compact and Renewal**, which the offline generator cannot produce and which therefore have never been artifact-audited in Rounds 151 or 152.
- Ask AI live: sync/SSE parity, multi-turn history, scope binding, report-bound frozen facts, citations and evidence drill-down, freshness/partial/stale disclosures, unanswerable questions, provider timeout/rate-limit/content failures. Verify the Round 152 changes specifically: that live CSConsole customer names are not mangled by header flattening (`_r152_flatten_header_field`, 160-char cap and `[SourceID:` neutralisation), that live citation counts did not drop after the intel whitelist narrowing, and that follow-ups still resolve correctly now that retrieval keys off `turn_question`.
- Label all of this `live_cisco_sources`. Never describe fixture results as live validation.

---

## Guardrails

- Counts come from `canonical_metrics.py`; risk scores from `risk_scoring.py`. Do not recompute them inline in a formatter or ask an LLM to decide a number.
- Preserve parameterized Snowflake queries, table allowlists, cursor cleanup, manager/member/customer/subscription authorization, DSM secondary attribution, and fail-closed handling for empty or ambiguous scopes. Never substitute synthetic data at runtime or loosen scope to make a report pass.
- Preserve loopback defaults, CSRF, HttpOnly/SameSite cookies, secure public-bind behavior, redacted provider errors, and corpus/OneDrive guards. Do not log raw customer rows, prompts, secrets, tokens, or query text containing sensitive values — Round 152 removed the last three such sites; do not reintroduce them.
- Preserve the exact 16-sheet workbook contract including `Evidence_Links`, Action Plan lifecycle semantics, TAC/BEMS distinct-ID collapsing, source-state semantics, and metric lineage.
- **Do not weaken any test or acceptance criterion to make a change green.** Round 152 hit three pre-existing tests and fixed all three without weakening an assertion — by moving new code into its own module, by moving a paired declaration in lockstep, and by making a test threshold-agnostic instead of pinning a constant that deliberately changed. Hold that standard. If a test genuinely encodes the old wrong behaviour, say so explicitly and justify the change in `QUALITY_AUDIT.md`.
- Do not add dead source markers or compatibility code solely to satisfy string tests.
- Do not publish, tag, release, alter `latest.json`, or deploy over the installed production app without explicit approval.

---

## Optional, only if Phases 0–3 are complete

From the Round 152 backlog, in value order. Each has evidence in `ROUND152_PRODUCT_IMPROVEMENT_HANDOFF.md` §5.

1. **The self-contradicting document.** A single `partial` source blanks every KPI while the same page prints exact counts and full detail rows — the report says "Action Plan lifecycle rollup is withheld because the source is incomplete" directly above a table of five fully-specified plans. Needs a product decision: publish the count with the state attached (`7 (partial coverage)`), or suppress the detail too. Either is defensible; the contradiction is not.
2. **One partial series blanks the whole chart.** A live run with partial TAC coverage loses Activity Mix, Risk Distribution *and* Trend even though three of four series are exact.
3. **"Evidence-backed next action" contains no evidence.** `risk_scoring` already computes per-customer `risk_factors`; `grep risk_factors decision_report_delivery.py` returns nothing.
4. **Leader Team shows only 5 of a manager's direct reports.** The roster carries 42 across 6 managers, and measured word usage is 597–841 of a 1500 budget.
5. **Cancellation is a no-op on Renewal and Subscription**, and effectively on Comprehensive (its per-customer checkpoint sits inside a loop over a list emptied at `app_simple.py:20884`).

---

## Success definition

Complete only when:

- The Phase 0 live sweep shows zero 403s on legitimate in-app requests, and per-worker failure classification is confirmed correct on live failures.
- No report family can publish a freshness claim it cannot substantiate, and the honest-unavailable branch is reachable and exercised.
- The decision report and `canonical_metrics` agree on customer identity, with a contract assertion preventing future drift, and every moved oracle value is individually justified.
- The live matrix proves report-to-workbook-to-source reconciliation for authorized scopes across all eight families, twice.
- Zero newly introduced failures; `make verify` at or above the 6,974 floor.
- Every result is labelled `live_cisco_sources` or `offline_fixture`, honestly.

## Deliverables

Append a Round 154 entry to `QUALITY_AUDIT.md` with starting/ending SHAs, exact commands, pass/fail/skip counts, live-vs-offline labels, the Phase 0 sweep table, every reconciliation result, each oracle value that moved and why, artifact-audit results, remaining risks, and a go/no-go recommendation. Create `ROUND154_HANDOFF_RESULT.md` explaining what changed, why it improves the product, what still requires live validation, and the best next-round objective.

Commit to `claude/round154-source-freshness-truth`. Keep credentials, customer data, virtual environments, reports, logs, and generated acceptance artifacts out of the commit.

Finish with: (1) the exact commit range, (2) a per-report verification table, (3) a live/offline evidence table, (4) fixes made and their regression tests, (5) unresolved blockers and risk, (6) whether Round 152 is now safe to merge to `main`, and (7) the single next approval needed.

---

**End of Claude Cowork prompt.**
