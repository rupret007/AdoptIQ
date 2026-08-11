# AdoptIQ — Round 153 Result: Leader Decision Value (offline)

**Branch:** `claude/round153-leader-decision-value`
**Starting SHA:** `4f2830c660023ca9f49ca76a11d9ccf3496fa166` (`claude/round152-product-excellence` tip)
**Environment:** Cowork cloud sandbox, Python 3.11.15. Evidence label: **`offline_fixture` / sandbox** throughout. No live Cisco sources.

## Tiers completed vs skipped

| Tier | Status | Commit |
|---|---|---|
| 1 — customer-specific risk drivers | ✅ done | `fd1b92b` |
| 2 — full team roster in Leader Team | ✅ done | `a64f316` |
| 3 — freshness fails closed | ✅ done | `8dcd69c` |
| 4 — alias-registry fix (zero oracle churn) | ✅ done | `1a14d21` |

Tier 4 was completed **without any oracle re-pin**, which is what made it safe on this budget. The shipped acceptance fixture's customers are Acme / Beta / Gamma, none of which are in the Round 132 alias registry (its only group is NYU). The fix therefore causes zero movement in any shipped oracle — proven by regenerating all four scopes at 23/23 after the change — and is exercised instead by a synthetic NYU test. The blind-re-pin failure mode the prompt warned about simply does not arise here. What remains for Round 154 is the *live* validation: confirm on a real portfolio with cross-source name variants that the merged customer count equals canonical_metrics and that the merged org's risk score rises (evidence no longer split).

## What changed and why it improves the product

**Tier 1 — the decision column now contains evidence.** `_visible_risk_decision_rows` rendered `recommendations[0]`, a three-branch band-level constant in `risk_scoring.py`, so two customers in the same risk band produced byte-identical rows in the one table that tells a manager what to do. Meanwhile `risk_scoring` already computed per-customer `risk_factors` ("2 critical/high adoption barriers", "1 escalated TAC cases (P1/P2)") and returned them — `grep -c risk_factors decision_report_delivery.py` returned 0. Added a "Top risk drivers" column sourced from `profile['risk_factors'][:2]`, with the `[Source: …]` provenance chrome stripped (that belongs in `Metric_Lineage`). The column is built inside the shared row builder, so the renderer and `_expected_visible_word_tables` stay in sync automatically; only the two header tuples moved, in lockstep. Inserted before the action, so existing state/score/band cell indices are unchanged.

**Tier 2 — a Leader Team report shows the whole team.** `TOP_ITEM_LIMIT_DEFAULT = 5` was one knob applied to action plans, members and accounts alike. Top-5 is right for accounts (large portfolios) and wrong for members — the single question a Leader Team report exists to answer, "how is each of my people doing?", required opening Excel. New `MEMBER_ITEM_LIMIT_DEFAULT = 15` applies to `member_summary` only; accounts and action plans keep 5. The `member_summary_omitted` overflow disclosure still fires for a pathological scope. Measured word usage is 597–841 of a 1500 budget, so there is ample room.

**Tier 3 — no report claims freshness it cannot substantiate.** `decision_report_delivery.py` documents that "attempt, evaluation, or generation time must never impersonate source freshness", but the defaults violated it: `data_as_of_state` defaulted to `"available"` and a missing `data_as_of_utc` fell through to the evaluation clock, so any caller omitting the retrieval clock published a verified "Data as of <now>" subtitle. Changed the default to `"unknown"` and made a missing clock yield a blank public as-of, forcing the honest "Data as of unavailable" branch.

**Tier 4 — the decision report honours the customer alias registry.** `_canonical_customer_identities` built the customer universe for KPIs, risk profiles, `Account_Summary` and the risk chart but never consulted the Round 132 alias registry, so an organisation with cross-source name variants (the NYU case R132 shipped for) was counted twice, its evidence split across two partial slices — understating **both** risk scores — and duplicated in `Account_Summary`. The ID-less path now keys identities by their alias group when the registry defines one (`alias_join_keys_for_name` returns the whole group for a registered name, a singleton self-fold otherwise), so NYU variants collapse to one identity while Acme/Beta/Gamma and all suffix-sensitive behaviour are untouched. `validate_cross_artifact_contract` now blocks publication if any alias group resolves to two identities — the durable half, converting the recurring drift into a hard error, the same shape as Round 152's default-deny endpoint check.

## The Tier 3 side effect Round 154 must know about

**This is intended, not a regression.** With Tier 3, the Leader / Renewal / Subscription workers currently render **"Data as of unavailable"** offline, because they do not yet thread a real prefetch clock (Comprehensive and Compact already do). An honest "unavailable" is strictly better than a false verified claim. Round 154's P0-A completes the other half: wire the real `snowflake_prefetch` retrieval timestamp into those three workers and validate it against live sources. Do not mistake the offline "unavailable" subtitle for a defect.

The offline acceptance generator was updated to pass the pinned acceptance clock explicitly as the retrieval timestamp — truthful, because the synthetic fixture's data *is* as-of that clock — so the `artifact_as_of_is_explicit` / `as_of_matches_acceptance_clock` checks keep validating a real declared retrieval time. **No acceptance check was weakened.** This also means the shipped offline artifacts now stop stamping the evaluation clock as verified freshness, which was itself the impersonation Tier 3 removes.

## Verification (offline)

- **Ruff:** 0 findings. **Bandit HIGH/MED:** 0.
- **Four-scope offline artifacts** (team/member/customer/comprehensive): **23/23 parity each**, exact 16 sheets in canonical order including `Evidence_Links`.
- **`run_decision_report_acceptance.py --mode offline`:** 4 scopes × 2 passes green, repeatability ok (rerun after the generator change).
- **`r114_audit_reports.py --auto`:** clean on every canonical type present. `CRITICAL_ISSUES_FOUND=True` is solely `MISSING_CANONICAL_TYPES=['Compact','Renewal']` — the offline generator emits only Leader and Comprehensive scopes; a coverage condition of the sandbox, not a defect (identical to Round 152).
- **New tests:** `tests/test_round153_leader_decision_value.py`, 20 tests, all passing — covering distinct same-band drivers, chrome stripping, header lockstep, member-limit vs account-limit, small-team no-omission, missing-clock fail-closed, explicit-clock verified, and the source-shape pin against the removed evaluation-clock fallback.
- **One pre-existing test updated, no assertion weakened:** the R147 evidence-contract test that pinned the old 5-column risk-table header now expects the 6-column set; the cell-index assertions it makes are preserved because the new column is inserted before the action.
- **`make verify` (full suite):** see the Round 153 entry in `QUALITY_AUDIT.md` for the exact final count.
- **Visual:** Leader Team rendered and read — subtitle honest, member table complete (all roster rows), drivers column populated and readable, no blank pages, no raw-record leakage.

## Files changed

`decision_report_delivery.py`, `scripts/generate_offline_acceptance_artifacts.py`, `tests/test_round147_evidence_contract.py`, `tests/test_round153_leader_decision_value.py` (new), plus `QUALITY_AUDIT.md` and this file.

**SSoT modules touched:** none. `canonical_metrics.py` and `risk_scoring.py` unchanged.

## Next action

Round 154, on the work machine with VPN: (1) the Round 152 route-gating live regression sweep, (2) wire the real prefetch clock into the Leader/Renewal/Subscription workers so their live freshness stamp returns to verified,, and (3) live validation of the Tier 4 alias fix against a real portfolio with cross-source name variants (the code fix shipped in Round 153; only its live confirmation remains).

**Trailer:** Made-with: Claude Fable 5 (Cowork cloud sandbox)
