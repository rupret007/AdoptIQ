# AdoptIQ — Round 153 Handoff: Leader Decision Value (offline)

Paste the prompt below into Claude Cowork. Use **Fable 5** with **very high reasoning**.

**Reasoning level is not a judgment call here.** This round touches the canonical fact contract and the offline oracles — the exact place where a weaker pass "fixes" a failing test by updating the expected value and calls it green. It also adds a column to a contract-validated Word table, which requires two separate code paths to move in lockstep or the document is rejected at publication.

**This round runs fine in a Cowork cloud sandbox.** Everything in it is offline-implementable and offline-verifiable by rendering and inspecting real artifacts. The live work is Round 154 (`ROUND154_HANDOFF.md`) and must wait for the work machine and VPN.

**This is a budget-constrained pass.** The work is organised as a priority ladder, ordered by value per token. **Commit after each tier.** If budget runs low, stop at a tier boundary with the repo green — do not start a tier you cannot finish. Tiers 1–3 are the target; tier 4 is a bonus; tier 5 is explicitly out of scope unless everything else landed early.

---

You are taking over AdoptIQ, Cisco Customer Success's renewal-risk and adoption-intelligence desktop app.

## Baseline

- Approved mainline: `main` @ `9907db1389fba05d87bcd5ca3d91227a3b33928f`.
- **Start from `claude/round152-product-excellence` @ `5db45f0898f1d6cdbccbbb9d806a55789d499463`** (4 commits ahead of `main`). It carries 23 defect fixes and 116 tests from Round 152, none of which have had live validation.
- Create and work on `claude/round153-leader-decision-value`. Do not modify or merge into `main`, reset, clean ignored files, expose credentials, commit generated reports, or alter remotes.

**Getting the repo into the sandbox.** GitHub is unreachable from a Cowork cloud session — the device bridge has no network egress (`403 from proxy after CONNECT`) and the container has no credentials for a private repo. Transfer by git bundle instead, as Rounds 151 and 152 did:

```bash
# operator, on the Mac:
git bundle create .tmp/round153/base.bundle claude/round152-product-excellence
# then stage that file into the sandbox and:
git clone base.bundle AdoptIQ
```

Return work the same way: `git bundle create out.bundle <branch> --not 9907db1…`, deliver the file, and the operator fetches it into the Mac checkout. Keep every generated artifact outside Git.

**Environment.** Use **Python 3.11**. The Round 150 `pip-audit` failure was a Python 3.9 resolver limitation on `truststore>=0.9.0`, not a dependency vulnerability. The current floor is **6,974 passed / 8 skipped / 14 deselected**.

## Read first

1. `ROUND152_PRODUCT_IMPROVEMENT_HANDOFF.md` — what Round 152 changed, and §5, which is the evidenced backlog this round draws from.
2. `QUALITY_AUDIT.md`, Round 152 entry.
3. `CLAUDE.md` — non-negotiable invariants.
4. `decision_report_delivery.py`, `risk_scoring.py`, `canonical_metrics.py`, `data_normalization.py` and their focused tests before changing behaviour.

## Why this round exists

Round 152 made the Leader report **honest**. It did not make it **useful**. The manager's original complaint — that the Leader report should be a concise decision document with prioritized metrics, risks, trends and tracked Action Plans — is still substantially open, and two accuracy defects were deliberately left for a round with more care budget.

`decision_report_delivery.build_concise_word_document` is now the single Word renderer for **all eight report families**, so every change below improves all of them at once.

---

## Tier 1 — the decision column actually contains evidence

**Cheapest change with the highest manager-visible value. Do this first.**

The column headed "Evidence-backed next action" contains no evidence. `decision_report_delivery.py:3869` renders `str(recommendations[0])`, and those come from a three-branch band-level constant in `risk_scoring.py:996 / :1004 / :1011` — so two customers in the same risk band get **byte-identical rows**.

Meanwhile `risk_scoring.py:948-970` already builds genuinely customer-specific `risk_factors` — `"2 critical/high adoption barriers"`, `"1 escalated TAC cases (P1/P2)"`, `"3 unresolved action plans"` — and returns them at `:1038`. Confirm the waste yourself: `grep -c risk_factors decision_report_delivery.py` returns **0**. The evidence is computed and thrown away.

**Fix.** In `_visible_risk_decision_rows` (`decision_report_delivery.py:3832`), add a "Top risk drivers" column sourced from `profile.get("risk_factors")[:2]`, keeping `recommendations[0]` as the action. Strip the `[Source: …]` chrome those strings carry before they enter a Word cell.

**The trap.** `display_count` and the visible-table contract are defined **twice** — once in the renderer (`decision_report_delivery.py:4109`) and once in `_expected_visible_word_tables` (`:4617`). Adding a column means updating `_expected_visible_word_tables` and `validate_word_semantics` in lockstep, or the contract validator rejects the document at publication. Change both or neither.

**Acceptance.** Two customers with different component profiles but the same band must produce different decision rows. `validate_word_semantics` must still return `ok`.

---

## Tier 2 — a Leader Team report shows the whole team

`TOP_ITEM_LIMIT_DEFAULT = 5` (`decision_report_delivery.py:50`) is applied as one knob to three different things at `:2077-2084`: `top_action_plans`, `member_summary`, and `account_summary`. **No caller ever overrides it.**

Top-5 is right for accounts, where a portfolio is genuinely large. It is wrong for team members: the roster carries 42 direct reports across 6 managers, so the single question a Leader Team report exists to answer — "how is each of my people doing?" — currently requires opening Excel. Measured word usage on the offline fixture is **597–841 of a 1500 budget**, and each member row costs about 7 words, so there is ample room.

**Fix.** Make the limit per-section rather than global: uncapped (or a generous ceiling such as 15, with a hard safety cap) for `member_summary`; keep 5 for `top_action_plans` and `account_summary`. Preserve the existing `member_summary_omitted` overflow disclosure for the pathological case.

**Acceptance.** A 12-member fixture renders 12 data rows with `member_summary_omitted == 0`, `validate_word_content` still passes the word budget, and account/action-plan sections are unchanged at 5.

---

## Tier 3 — no report may claim freshness it cannot substantiate

This is the offline half of Round 154's P0-A. It is cheap, it has no oracle churn, and it removes a false trust claim immediately.

`decision_report_delivery.py:2054` states the contract in the code itself:

> `as_of_utc` is only the public source-retrieval clock and may be blank; attempt, evaluation, or generation time must never impersonate source freshness.

The defaults violate it. At `:1770-1771`, `data_as_of_utc` defaults to `None` and `data_as_of_state` defaults to `"available"`; at `:1785` a missing clock silently falls back to the evaluation timestamp. The Word subtitle then renders the *verified* branch at `:4060` — `f"Data as of {…}"` — so the two honest branches at `:4072` and `:4076` are unreachable for any caller that omits the argument.

**Fix — this tier only.** Change the `data_as_of_state` default to `"unknown"` and stop substituting the evaluation clock for a missing `data_as_of_utc`. A caller supplying no retrieval clock must land on the honest "Data as of unavailable" branch.

**Do not** rewire the Leader / Renewal / Subscription workers to record a real prefetch clock in this round — that needs live sources to validate and belongs to Round 154. Expect this change to make those three families render the honest "unavailable" subtitle offline. **That is the correct outcome, not a regression**: an honest "unavailable" is strictly better than a false verified claim, and Round 154 restores the real timestamp.

Record this explicitly in the audit entry so Round 154 does not mistake it for a defect.

**Acceptance.** Facts built with `data_as_of_utc` omitted must yield `data_as_of_state != "available"` and a subtitle that does not begin `"Data as of "` followed by a timestamp. Add a source-shape pin that no `data_as_of` expression in `decision_report_delivery.py` derives from an evaluation or generation clock.

---

## Tier 4 — the decision report must honour the customer alias registry

**The most valuable remaining accuracy fix, and the most token-expensive. Start it only with real budget left.**

`_canonical_customer_identities` (`decision_report_delivery.py:814`) builds the customer universe for KPIs, risk profiles, `Account_Summary`, `Member_Summary` and the risk chart. It joins on exact normalized label (`_customer_match_key`, `:321`) or shared account ID, and **never consults the Round 132 alias registry**. The module imports `customer_names_match` at `:31` but uses it only for incident matching at `:1104`. The report then publishes `"customers": len(risk_profiles)` (`:1899`) instead of the canonical count.

`canonical_metrics.count_customers` (`:422`) *does* collapse aliases via `_r132_collapse_alias_customer_names` (`:407`). The helpers exist: `data_normalization.alias_join_keys_for_name` (`:597`), `canonical_customer_name` (`:603`).

**Consequence.** One organisation with cross-source name variants — the exact NYU case Round 132 shipped for — is counted twice, its evidence split across two partial slices so **both** risk scores are understated, and it appears twice in `Account_Summary`.

**Fix.** Seed the union-find with `alias_join_keys_for_name(display)` alongside the account-ID union, and pick the display label via `canonical_customer_name(...)` anchored on the scoped `BU_NAME`. Do **not** add fuzzy matching — the registry is an explicit operator allow-list.

**Then add the assertion that makes it permanent.** In `validate_cross_artifact_contract`, assert `len(risk_profiles) == cm.count_customers(...)` for the same frames. This is the durable half of the fix: it converts a recurring drift class into an impossible one, the same way Round 152's default-deny endpoint check did.

### The oracle re-pin — read this before touching a fixture

This fix moves `Fact_Contract_SHA256` and the offline oracles. The oracle lives at `tests/fixtures/report_acceptance/v1/sanitized_portfolio.json` under `expected_by_scope`; team currently pins `"customers": 3` and `chart_totals.risk_distribution: 3`.

**Re-pinning is expected. Doing it blind is the failure mode this whole round is guarding against.** Procedure:

1. First add a fixture case that exercises the defect — two records for one organisation under different names, no shared account ID, with a matching alias-registry entry — and confirm it fails **before** the fix.
2. Apply the fix, then regenerate all four scopes with `scripts/generate_offline_acceptance_artifacts.py --scope {team,member,customer,comprehensive} --as-of 2026-08-03T12:00:00Z`.
3. For **every** oracle value that moved, state in `QUALITY_AUDIT.md` what it was, what it became, and why the new value is correct. An oracle change with no stated reason is a red flag, not a green test.
4. Confirm `decision_report_delivery` and `canonical_metrics` now agree on the same frames.

If you cannot complete steps 1–4 within budget, **do not start this tier** — leave it for Round 154, which has live data to validate against.

---

## Tier 5 — out of scope unless everything above landed early

Listed so you can recognise and skip them, not so you can attempt them:

- **One partial source blanks all four charts.** `decision_report_delivery.py:1737-1751` nulls every row sharing a `Chart_ID` when any one contributing source is not `available`/`zero`. A live run with partial TAC loses Activity Mix, Risk Distribution *and* Trend even though three of four series are exact. Changing this moves chart digests and needs its own oracle pass.
- **The self-contradicting page.** `_derived_value` (`:1212`) and `display_count` (`:4109`) withhold the aggregate while the coverage table and top-N detail table still print exact counts and fully-specified rows — so the document says "Action Plan lifecycle rollup is withheld because the source is incomplete" directly above a table of five complete plans. Needs a product decision before code.
- **"Status and Aging" has no aging.** `canonical_metrics` computes `AdoptIQ_Age_Days` at `:1616` but `action_plan_chart_series` (`:1686`) never aggregates it. The stalled-AP and period-over-period analysis exists at `leader_report_generator.py:4197 / :4420 / :4503 / :4661` and is orphaned — `leader_report_components` is imported by nothing outside its own docstring.

---

## Guardrails

- Counts come from `canonical_metrics.py`; risk scores from `risk_scoring.py`. Never recompute inline in a formatter.
- Preserve the exact 16-sheet workbook contract including `Evidence_Links`, Action Plan lifecycle semantics, TAC/BEMS distinct-ID collapsing, source-state semantics, metric lineage, manager/member/customer/subscription authorization, DSM secondary attribution, and fail-closed ambiguous scopes.
- Preserve loopback defaults, CSRF, cookie protections, redacted provider errors, and safe error sanitization. Round 152 removed the last three raw-customer-row log sites; do not reintroduce them.
- **Do not weaken any test or acceptance criterion to make a change green.** Round 152 hit three pre-existing tests and fixed all three without weakening an assertion — by moving new code into its own module, by moving a paired declaration in lockstep, and by making a test threshold-agnostic instead of pinning a constant that had deliberately changed. Hold that bar. If a test genuinely encodes old wrong behaviour, say so explicitly and justify it in `QUALITY_AUDIT.md`.
- Do not add dead source markers or compatibility code solely to satisfy string tests.

## Validation per tier

After each tier: focused tests, then `ruff`, `bandit -ll`, and the affected suites. At the end of the round:

- `make verify` (Python 3.11) — at or above 6,974 passed / 8 skipped / 14 deselected.
- `scripts/generate_offline_acceptance_artifacts.py` for all four scopes — 23/23 parity each, exact 16 sheets in canonical order.
- `scripts/run_decision_report_acceptance.py --mode offline` — 4 scopes × 2 passes, repeatability ok.
- `scripts/run_local_acceptance_lab.py --enable-local-fixtures --scenario all` — 21 scenarios reconciled.
- `scripts/r114_audit_reports.py --auto` on the retained artifacts. Note that `MISSING_CANONICAL_TYPES=['Compact','Renewal']` is a coverage condition of the offline generator, **not** a defect finding.
- **Render and read the actual DOCX** — paragraph by paragraph and table by table, at minimum Leader Team and Leader Customer. Confirm the new drivers column reads well, the member table is complete, no blank pages or clipping, and no raw-record leakage into the concise report.

## Deliverables

Append a Round 153 entry to `QUALITY_AUDIT.md`: starting and ending SHAs, which tiers were completed and which were skipped and why, exact commands and counts, offline-vs-live evidence labels, every oracle value that moved with its justification, visual review results, remaining backlog, and a go/no-go. Create `ROUND153_RESULT.md` explaining what changed, why it improves the product, and what Round 154 must now validate live — including the deliberate Tier 3 side effect (Leader/Renewal/Subscription rendering "Data as of unavailable" offline until Round 154 wires the real prefetch clock).

Commit to `claude/round153-leader-decision-value` and return a git bundle. Keep credentials, customer data, virtual environments, reports, logs and acceptance artifacts out of the commit.

Finish with: (1) the exact commit range, (2) tiers completed vs skipped, (3) a per-report verification table, (4) fixes made and their regression tests, (5) every oracle value that moved and why, (6) remaining risks, and (7) the single next action.

---

**End of Claude Cowork prompt.**
