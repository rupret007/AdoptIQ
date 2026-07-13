# AdoptIQ Logic Improvement Report

Date: 2026-07-13 (America/Chicago)
Release target: local `main`, prepared from `codex/adoptiq-decision-intelligence-v2`
Protected source: `/Users/jeffstory/Downloads/AdoptIQ_source_backup_20260713T043647Z`
Working copy: `/Users/jeffstory/Downloads/AdoptIQ_logic_improvement_working_20260713T043647Z`

## Executive outcome

AdoptIQ already had unusually broad customer-success coverage: deterministic canonical metrics, multiple report formats, grounded Ask AI, narrative validators, source-aware report consistency checks, encrypted/local corpus support, and a large regression suite. The strongest existing design choice was that canonical calculations—not an LLM—were intended to be authoritative.

The main weakness was not a lack of features. It was that several paths could lose record identity, lifecycle state, source availability, or customer scope before reaching those canonical calculations. That could turn duplicated rows into extra risk, old closed escalations into current urgency, missing evidence into a healthy score, one shared logical ID into risk for two customers, or a plausible but unsupported AI sentence into an accepted claim.

This run hardened the shared normalization, canonicalization, scoring, report, and AI boundaries. Severe observed evidence now rises conservatively; missing, malformed, or ownership-conflicted evidence fails closed; duplicate fan-out no longer inflates risk; supported report paths quarantine ownership conflicts before per-customer slicing; recommendations name the triggering evidence, likely owner, urgency, dependency, and expected outcome; and AI claims face numeric, citation, scope, relationship, and canonical-metric checks.

This is a deterministic evidence-quality improvement, not a claim of predictive calibration or autonomous online learning.

## Source protection and operating scope

- No supplied source file was edited or imported. Five transient generated pytest/cache artifacts found in the protected copy were removed, restoring the exact all-files manifest recorded before implementation.
- A sibling working copy was created; the initial logic-hardening work used `codex/adoptiq-logic-improvement`, and the V2 continuation used `codex/adoptiq-decision-intelligence-v2` before local-main release.
- Initial supplied-source baseline: `12c54ee0943bf8a623e9024ba61ca9435ab04be1` (`chore: preserve supplied AdoptIQ baseline`). Decision Intelligence V2 comparison base: `842d5b9192f7773d1760fb7f92741fb776162fc2`.
- Final read-only verification of the protected source found 1,191 files and SHA-256 tree-manifest digest `7227f8b2ae33416aab410238db51a9a7dbc92354ed834c2aad0ca4e979c2b5dc`, exactly matching the pre-change values.
- The protected and working directories have distinct real paths/inodes and zero shared regular-file inodes; the working copy is not a hard-linked mutation of the backup.
- No customer data, production credentials, Cisco private systems, Snowflake production environment, external upload, or network-backed connector was used. Tests used repository fixtures, synthetic records, and in-memory connector stubs with credential variables cleared.

## Architecture discovered from the source

The representative execution path is:

1. `app_simple.py` orchestrates Flask/desktop requests, source loading, scoping, progress, report modes, and Word/Excel output.
2. `adoptiq_backend.py`, Snowflake helpers, and CSConsole/CSOne adapters retrieve or load source frames and attach freshness/fetch diagnostics.
3. `data_normalization.py` resolves customer/account aliases, normalizes status/severity/priority/time fields, and partitions source frames.
4. `canonical_metrics.py` defines logical records and shared counts for TAC, Action Plans, Customer Pulse, Adoption Barriers, customers, and portfolio rollups.
5. `risk_scoring.py` turns canonical components into a deterministic profile with evidence coverage, confidence, guardrails, risk contributors, and next-best actions.
6. `compact_report_formatter.py`, `leader_report_generator.py`, `executive_intelligence_formatter.py`, `app_simple.py`, and `wxcc_health_input_exporter.py` render the shared values into Compact, Comprehensive, Renewal, Leader, dashboard, Word, and Excel surfaces.
7. `ask_ai_grounded.py`, `ask_ai_corpus.py`, `numeric_grounding.py`, and `ai_narrative_validator.py` retrieve bounded evidence, isolate untrusted source text, validate claims/citations/numbers/relations, and reconcile selected KPIs to canonical values.
8. Local report history, encrypted corpus/storage, diagnostics, logging, packaging specs, and macOS/Windows build scripts complete the desktop application boundary.

The important architectural correction in this run is the ordering of identity work:

`complete source frame -> normalize identity -> quarantine cross-owner logical IDs -> partition once by canonical customer -> canonicalize logical records -> score -> render/ground narrative`

Previously, some report paths sliced by customer before the ownership disagreement was visible.

## Reproducible baseline

The repository targets Python 3.11 (`pyproject.toml` and CI). The available local runtime was Python 3.12.13.

Baseline results before logic changes:

| Baseline area | Result | Observation |
|---|---:|---|
| Canonical/risk core selection | 87 passed | 19 warnings |
| Identity/parity/determinism selection | 57 passed, 5 failed, 1 collection error | Failures/collection were caused by unavailable local `hvac` dependency |
| Grounding/citation selection | 139 passed, 4 failed, 6 errors | Failures/errors were caused by the same unavailable optional dependency path |
| Full collection | Could not complete | Optional local packages such as `hvac`, Snowflake libraries, `truststore`, `fastembed`, and `feedparser` were absent |

The README's historical 6,251-test figure was not treated as a reproduced result. Connector-dependent final suites were instead run with harmless in-memory stubs and cleared credentials.

Baseline synthetic observations exposed material logic errors:

- One active P1 case with a BEMS reference scored 7.6/100 (`HEALTHY`) and received generic monitoring text.
- Repeating the same logical case ten times raised the score to 27.6/100 and saturated the support component.
- All sources absent scored 1.3/100 (`HEALTHY`) with no explicit confidence state.
- One incomplete Action Plan scored 0.6/100, recorded no unresolved plan, and produced no evidence-specific action.
- An old closed P1/BEMS case still contributed 6.9/100 and 23.5 support-risk points.

## Highest-risk findings and prioritization

| Priority | Finding | Why it ranked highly | Resolution |
|---|---|---|---|
| P0 | Missing/unusable evidence could publish `HEALTHY` | A false reassurance is more dangerous than an explicit unknown | Added source-state propagation, minimum evidence coverage, record-completeness checks, `UNKNOWN`/N/A rendering, and confidence caveats |
| P0 | Duplicate/fan-out rows changed counts and scores | A common join artifact affected every report and risk output | Added deterministic logical-record dedup for TAC, Pulse, Action Plans, and barriers with stable tie-breaking |
| P0 | The same logical ID could be assigned to two customers | It could make both customers appear urgent and defeated per-customer isolation | Added full-source, alias-aware ownership quarantine before partitioning across Ask, Compact, Comprehensive, Renewal, and Leader paths |
| P0 | Active P1/BEMS evidence could remain healthy | Genuine severe conditions were under-detected | Added evidence guardrail floors and active-only urgency semantics |
| P0 | AI validation checked tokens more strongly than relationships | A model could cite real values while assigning them to the wrong actor/field or inventing causality | Added typed quantity and structured/extractive relationship validation plus canonical KPI correction |
| P0 | Build design supported reversible embedded credentials | A packaged secret is recoverable from the binary | Removed bundled-secret generation/collection and made credential handling runtime-only |
| P1 | Report paths recomputed/sliced the same concepts differently | Word, Excel, dashboard, Renewal, Compact, Leader, and Ask could drift | Routed shared counts/risk through canonical helpers and added cross-report parity tests |
| P1 | Repeated per-customer scans were quadratic in portfolio size | It increased latency and repeated identity work | Added one-pass customer partition indexes |

## Changes implemented

### Canonical identity and logical records

- Customer identity now fails closed for ambiguous account-only rows and honors configured alias groups.
- Case and punctuation variants collapse consistently, while unconfigured legal entities such as `Acme Inc` and `Acme LLC` remain distinct.
- Logical-ID schema matching is case- and punctuation-insensitive (`SR_NUMBER`, `CaseNumber`, lowercase headers, Pulse/AP/subscription variants).
- Null-like text IDs (`N/A`, `nan`, `None`, `NULL`, and related sentinels) are treated as missing IDs, not one shared record.
- Duplicate DataFrame indexes use positional processing and no longer crash or select ambiguous Series values.
- A logical ID observed under two resolved owners is quarantined for all owners. Diagnostics survive repeated normalization, empty fallbacks, and per-customer partitions.
- TAC, Customer Pulse, Action Plan, and barrier canonicalizers select one current row deterministically by timestamp, lifecycle, severity, signal completeness, and stable payload tie-breakers. Blank IDs remain independent.

### Risk, evidence quality, and actions

- Risk remains deterministic on a 0–100 canonical scale with documented half-open bands: `HEALTHY <15`, `LOW 15–<35`, `MEDIUM 35–<55`, `HIGH 55–<75`, `CRITICAL >=75`.
- A benign health conclusion requires at least 50% usable configured evidence. This is a conservative operating threshold, not an empirically calibrated probability.
- Missing feeds, fetch failures, unusable schemas, malformed record majorities, and ownership-conflict-only sources are excluded from the denominator and surfaced in evidence quality.
- Active P1, active BEMS, critical barriers, high-risk renewal evidence, and customer-attributed severe incidents apply conservative risk floors. Historical closed records do not create current urgency.
- Adding positive incident evidence cannot lower an already higher non-incident score.
- Untagged portfolio incidents remain bounded context and cannot trigger customer-specific guardrails/actions; tagged incidents can.
- Recommendations now carry triggering sources, reason, likely owner, urgency, effort, dependencies, expected outcome, and evidence confidence. Cross-source recovery actions connect a negative Pulse with active escalated support evidence.
- Portfolio averages and band counts exclude `UNKNOWN` rather than coercing missing scores to zero.

### Report and source-path consistency

- Comprehensive, Renewal, Compact, Ask, and Leader now quarantine complete source frames before per-customer slicing.
- Per-source frames are partitioned once by canonical customer key and preserve `None` versus observed-empty versus fetch-failed versus conflict-only states.
- Compact/Leader Pulse, TAC, and AP paths no longer allow a shared ID to influence two customer or CSSM slices.
- Canonical TAC counts and lifecycle state flow through Compact, Comprehensive, Leader, Executive Intelligence, and export paths.
- Renewal uses the intended last-modified/close-date semantics, and Compact/Renewal share the same 0–10 display of the canonical 0–100 score.
- Word/Excel/dashboard render `UNKNOWN`/N/A explicitly, and portfolio summaries do not average unavailable scores.

### Grounded AI and narrative validation

- Ask AI builds per-customer canonical frames from the same full-source quarantine/partition boundary used by reports.
- Evidence is scoped to the requested customer; mixed-customer input cannot leak into another customer's facts.
- Citation validation rejects a statement if any cited ID is outside the allowed evidence set; a valid citation cannot mask an invalid one.
- Numeric validation is typed: currency and currency code, percentages, signed quantities, durations, years, scientific notation, suffix magnitudes, ranges, and parenthetical KPI context are distinguished.
- A relationship gate rejects customer metadata recast as an actor, field/value swaps, unsupported causal joins, and actions contradicted by record lifecycle. Direct extractive facts and status-compatible actions remain allowed.
- Untrusted evidence is fenced as data, prompt-injection text is rejected/escaped, and factual/canonical corrections are deterministic.
- Validator failure is fail-closed: raw unvalidated model prose is not accepted as authoritative output.

### Credential and packaging safety

- PyInstaller specs no longer collect `_bundled_secrets`.
- CI and local build scripts no longer generate or package credential modules or root `.env` files.
- `embed_credentials.py` is lint-only and refuses secret generation.
- Packaged execution reads operator/runtime credentials only; a random per-user session key replaces a shared bundled key.
- Windows/macOS build documentation and `secrets.env.template` describe runtime provisioning without embedding values.

## Major files and functions changed

| Area | Major files/functions |
|---|---|
| Identity/normalization | `data_normalization.py`: `resolve_customer_name`, `customer_identity_key`, `customer_ownership_key`, `quarantine_cross_customer_record_ids`, `partition_customer_frame`, row-wise coalescing/schema matching |
| Canonical metrics | `canonical_metrics.py`: `deduplicate_tac_cases`, `deduplicate_action_plans`, `deduplicate_customer_pulse`, customer/portfolio counts |
| Risk | `risk_scoring.py`: component scorers, `compute_customer_risk_profile`, incident attribution/monotonic uplift, evidence quality, guardrails, next-best actions |
| Orchestration | `app_simple.py`: shared report partitions, Comprehensive/Renewal profiles and storyboards, UNKNOWN rollups, canonical customer labels |
| Compact/Leader | `compact_report_formatter.py`, `leader_report_generator.py`: full-source partitions, logical TAC/Pulse/AP counts, UNKNOWN rendering |
| Other report/export paths | `adoptiq_backend.py`, `executive_intelligence_formatter.py`, `wxcc_health_input_exporter.py`, `leader_report_generator.py` |
| Grounding | `ask_ai_grounded.py`, `ask_ai_corpus.py`, new `numeric_grounding.py`, `ai_narrative_validator.py` |
| Packaging/security | `.github/workflows/build.yml`, `adoptiq_mac.spec`, `adoptiq_pc.spec`, `embed_credentials.py`, build scripts, build/security documentation |

## Synthetic before-and-after behavior

All rows below are synthetic and identified as such.

| Scenario | Baseline | Final behavior |
|---|---|---|
| One active P1 + BEMS | 7.6, `HEALTHY`, generic monitor | 55.0, `HIGH`; P1 owner/daily checkpoint and BEMS engineering owner/ETA actions |
| Same logical P1/BEMS repeated 10x | 27.6, `LOW`; support component inflated | 55.0, `HIGH`; one logical case, nine duplicates removed, identical result to one row |
| Explicitly observed empty sources + active subscription | 1.2, `HEALTHY` | 0.0, `HEALTHY`, high evidence confidence; observed zero remains distinct from missing |
| All sources missing | 1.3, `HEALTHY` | score N/A, `UNKNOWN`, low confidence; validate-source action |
| One incomplete Action Plan | 0.6; unresolved count zero | 13.8, `HEALTHY`; one unresolved plan and concrete owner/due-date action |
| Old closed P1/BEMS | 6.9 with historical support risk | 0.8, `HEALTHY`; no P1/BEMS guardrail or urgent action |
| Malformed support record | Could dilute/appear benign | score N/A, `UNKNOWN`, low confidence |
| Untagged global critical incident | Could fan out a customer guardrail in the initial implementation | 3.2, `HEALTHY`; no customer guardrail/action |
| Customer-tagged critical incident | Not reliably isolated | 55.0, `HIGH`; customer-attributed incident guardrail/action |
| Same `SR_NUMBER` under Alpha and Beta | Could be selected for one or counted for both | both rows quarantined; no customer partition; direct score is N/A/`UNKNOWN` |

## Tests added or updated

New focused suites:

- `tests/test_logic_improvement_engine.py`
- `tests/test_ownership_quarantine_hardening.py`
- `tests/test_compact_partition_ownership.py`
- `tests/test_app_report_ownership_partitions.py`
- `tests/test_packaged_build_credentials.py`
- `tests/test_tac_logical_report_parity.py`
- `tests/test_unknown_risk_propagation.py`

Existing tests were extended for Ask AI relationship/citation/numeric grounding, narrative validation, canonical score/band parity, lifecycle handling, report reconciliation, source failure propagation, credentials, and package behavior. Assertions that encoded the unsafe missing-as-zero or raw-row-count behavior were updated to the new explicit contract; useful checks were not removed to manufacture a pass.

## Pre-V2 logic-hardening validation

Final offline regression matrix after all identity/alias fixes:

| Area | Result |
|---|---:|
| Canonical, dedup, ownership, aliases | 355 passed |
| Risk and renewal | 184 passed, 1 skipped |
| Report parity and source partitions | 314 passed |
| Ask AI and grounding | 322 passed, 2 skipped |
| Credential packaging | 63 passed |
| **Aggregate** | **1,238 passed, 3 skipped, 0 failed (1,241 collected)** |

The 27 warnings were existing `datetime.utcnow()` deprecations. Connector-dependent tests used cleared credentials and in-memory offline stubs; no connection attempt reached an external system.

Additional validation:

- 99/99 supported adversarial assertions passed after the self-review loop, plus one extra invariant confirming punctuation/case variants collapse while `Inc` and `LLC` remain distinct.
- Two deliberately unsupported probes manually discarded the other owner's row before calling a private Leader sentiment helper. Conflict detection is informationally impossible after that data loss; the supported Leader ingestion boundary quarantines the full Pulse source first.
- Active severe, duplicated, stale, missing, malformed, contradictory-owner, global/tagged incident, casing, alias, sentinel-ID, lowercase-header, duplicate-index, account-only, reorder, and deterministic tie cases were exercised synthetically.
- All 50 changed or new Python files compiled and passed Ruff; `git diff --check` passed.
- Credential CI lint and workflow YAML parsing passed. No packaged GUI binary or live connector smoke test was attempted in this dependency-limited, offline application-validation run.

## Performance and compatibility observations

- Customer partitioning is now one pass per source (`O(rows)`) instead of a scan per customer (`O(customers × rows)`). On a synthetic 10,000-row/100-customer frame, observed three-sample medians for the one-pass path ranged from 0.48–1.25 seconds under varying concurrent test load. The legacy repeated-slice comparison measured 33.16 seconds, at least 26× slower than the slow end of the observed one-pass range.
- Canonicalizing a synthetic 10,000-row TAC frame into 1,000 logical cases took 1.86–2.84 seconds across local three-sample runs (approximately 3,500–5,400 input rows/second). Correct ownership checks are now included in that cost. Repeated canonicalization of the same frame remains a performance target.
- Public report labels, expected sheet/report names, score scales, and existing report modes were retained.
- Python 3.11 remains the supported build target; logic validation ran on Python 3.12.13.
- Missing optional local packages prevented a real PyInstaller build and live Snowflake/Keeper/CSOne execution. The code, specs, workflows, and offline package contract were validated instead.

## Unresolved risks and assumptions

1. **No outcome calibration:** weights, guardrail floors, and the 50% health-publication threshold are conservative deterministic policy, not a measured churn/renewal probability. Authorized historical outcome data is required for calibration.
2. **Temporal/renewal model remains split:** specialized renewal analysis contains milestone logic, but the general customer-risk profile does not yet expose a unified renewal-proximity/trend model.
3. **Private pre-slice bypass:** callers that manually discard other-owner rows before invoking a private Leader sentiment helper cannot recover the lost conflict. Supported report ingestion is protected; future APIs should require a quarantined-source provenance marker or accept the full frame.
4. **Untagged incidents:** an untagged portfolio incident contributes a small bounded context score to each customer (3.2/100 in the synthetic critical example) but cannot create a customer-specific guardrail/action. Service-to-customer mapping would be needed for stronger attribution.
5. **Bounded semantic gate:** the AI relationship validator covers tested structured/extractive, actor, lifecycle, field-swap, and causal shapes. It is not a general natural-language entailment proof system.
6. **Connector/package compatibility:** actual enterprise TLS, Keeper, Snowflake, CSOne, frozen macOS/Windows binaries, and large real schemas require authorized environment testing.
7. **Performance:** repeated calls to canonicalize the same large DataFrame can still duplicate work. A request-scoped immutable canonical analysis object would reduce this safely.

## Next highest-value improvement

The next step should be a request-scoped canonical `CustomerAnalysis`/`PortfolioAnalysis` object built once from authorized, de-identified source extracts. It should carry logical records, observation/freshness timestamps, scope, evidence references, coverage/conflict diagnostics, trends, risk components, and report-ready metrics. Every renderer and Ask AI would consume that object rather than recanonicalizing frames.

With product-owner-approved historical outcomes, the deterministic thresholds and temporal features could then be calibrated and back-tested by cohort, horizon, and data-coverage band. Evaluation should measure ranking quality, false urgency, dangerous under-detection, calibration error, and action usefulness. Until that evidence exists, AdoptIQ should continue presenting forecasts as conditional scenarios rather than predictive certainty.

---

## Decision Intelligence V2 continuation — 2026-07-13

The next-step architecture described above is now implemented as Decision Intelligence V2. The detailed design and evaluation ledger is in [`ADOPTIQ_DECISION_INTELLIGENCE_V2_REPORT.md`](ADOPTIQ_DECISION_INTELLIGENCE_V2_REPORT.md); this section records the additional work in the same logic-improvement audit trail.

### Source protection and comparison floor

- Work was performed only in `/Users/jeffstory/Downloads/AdoptIQ_logic_improvement_working_20260713T043647Z` from baseline commit `842d5b9192f7773d1760fb7f92741fb776162fc2`.
- The protected source copy finished with 1,191 regular files and SHA-256 tree-manifest digest `7227f8b2ae33416aab410238db51a9a7dbc92354ed834c2aad0ca4e979c2b5dc`, exactly matching the recorded pre-change digest. Five runtime-only pytest/cache artifacts found during final verification were removed; no source file in the protected copy changed.
- The reproducible pre-V2 selected matrix remained 1,238 passed, 3 skipped, 0 failed across 1,241 collected tests.
- No customer data, production credential, private Cisco service, Snowflake production environment, live connector, or new package installation was used. Optional connector imports were inert in-memory stubs with credential variables cleared.

### Highest-risk V2 findings and their disposition

| Priority | Finding | Disposition |
|---|---|---|
| P0 | Report, renewal, dashboard, history, and Ask paths could independently rebuild the same truth. | Added one immutable, request-scoped `AnalysisBundle` and thin projections; active Compact, Renewal, Subscription, Leader, Comprehensive, WxCC, and Ask boundaries build once or enter a visible legacy fallback. |
| P0 | Missing, failed, observed-empty, excluded, and conflicted sources could collapse into the same empty shape. | Source-state evidence is explicit. Incident, technology-filter, account-batch, and Comprehensive Action Plan failures remain `None`/`fetch_failed`; successful empty results remain `observed_empty`. |
| P0 | Comprehensive Snowflake Action Plan retrieval could be widened by `owner_emails` beyond the selected account/technology scope. | Owner email is now retrieval-only: every nonblank account alias must resolve inside the selected roster, conflicting aliases are excluded, 15/18-character Salesforce IDs reconcile safely, explicit technology aliases must agree and match, and unsupported or unverifiable scopes fail closed. |
| P0 | A nominally canonical source helper could fail and silently fall back to generic dedupe. | TAC, Pulse, and Action Plan canonicalization failures now fail bundle construction so the caller cannot claim canonical status. |
| P0 | Duplicate physical column labels could make pandas return a DataFrame where mature readers expected a Series, causing crashes or order-dependent scope/metric decisions. | The shared ingress boundary now coalesces blank-plus-value and semantically agreeing duplicates, chooses a deterministic canonical label, and quarantines contradictory rows with customer/account-attributed `duplicate_schema_conflicts` diagnostics before identity, scope, concatenation, or scoring. Identifier fields deliberately preserve meaningful differences such as leading zeros. |
| P0 | Source identity and account ownership could diverge across aliases: non-string values could stringify into scope, conflicting customer/account aliases could select by column order, and generic case or valid Salesforce 15/18 variants could be matched more broadly than they were grouped. | Scope now accepts only genuine scalar strings; populated customer/account aliases are conjunctive; customer labels must agree with account ownership; ID-less signatures use canonical schema keys; and one equivalence-aware ownership index governs lookup, ambiguity, partitioning, and request authorization. Equivalent IDs with different owners fail account-only scope closed, while same-owner variants resolve across sources. |
| P0 | Pandas could propagate a canonical cache marker through mutation or subset concatenation. | The fast path now re-coalesces current logical IDs and accepts only a still-unique frame. Same-length mutation, marked subset concat, and WxCC cross-source overlap reproductions all recanonicalize. |
| P0 | Ask could fetch only the first 100 accounts but score the full roster as if unfetched customers had observed zeros. | Canonical row sources are fetched across bounded account batches; a failed/missing batch carries an explicit fetch error. |
| P0 | Deterministic Ask action/finding text could cite projection IDs omitted from the actual prompt/index. | Canonical citations are intersected with the actual retrieval whitelist; an empty index yields no deterministic citations. |
| P1 | Snapshots/history could not distinguish incompatible scope/schema from no change. | Added comparison-scope fingerprints, schema-major compatibility, atomic snapshots, temporal `not_comparable` semantics, and additive history fields. |
| P1 | Legacy actions lacked a stable, evidence-linked prioritization contract. | Added stable IDs, finding/evidence chains, weighted factors, guardrails, role ownership, dependencies, expected outcomes, and measurable success signals. |

### Architecture and analytical changes

`decision_intelligence.py` now defines frozen request, evidence, metric, finding, temporal-change, action, customer, portfolio, diagnostic, context, and bundle contracts. The build order is:

`authorized scoped sources → strict ownership quarantine → logical-record canonicalization → deterministic metrics/risk → findings/change detection → ranked actions → portfolio roll-up → immutable fingerprinted bundle → snapshot/adapters`

Material behavior includes:

- conjunctive customer/account/subscription/team/leader/technology/renewal/time scoping;
- conservative aliases with distinct unconfigured legal entities;
- duplicate physical-schema normalization at source ingress: agreeing or blank-plus-value columns coalesce, while contradictory rows are quarantined and surfaced as conflicted evidence instead of crashing or being selected by column order;
- strict identity normalization at request, authorized-roster, and source-row boundaries: malformed containers and non-string cells cannot manufacture scope; conflicting aliases quarantine; account-equivalent owner conflicts fail closed; and exact prebuilt Ask bundle validation covers customers, accounts, subscriptions, technology, and leadership;
- record-level evidence plus source-state evidence and freshness;
- explicit relationship language that does not claim causation;
- conditional scenarios with `probability: null`, not fabricated forecasts;
- compatible prior-snapshot comparison for worsening, improving, persistent, recovering, resolved, new, uncertain, and not-comparable states;
- exact portfolio totals and known-score averages that exclude unknown values;
- deterministic action ranks with confidence/effort/urgency/renewal/persistence factors and severe-evidence/data-establishment guardrails;
- canonical serialization, lossless round-trip, reconciliation, request/comparison/analysis fingerprints, and atomic mode-restricted local snapshots.

`decision_intelligence_adapters.py` supplies backward-compatible risk and portfolio shapes, Decision Brief Word/Excel frames, bounded Ask facts, stamped manifests, tamper digests, and synthetic five-surface reconciliation. `enhanced_admin_dashboard_v2.py` stores schema, request, comparison-scope, analysis, and snapshot identity without copying source records into history; it does not yet migrate every live dashboard fact.

Active-path changes include:

- Compact, Renewal, Comprehensive, and Subscription call a shared application boundary once before local risk/portfolio work.
- Subscription reuses its authorized fetch and avoids the historical second fetch on canonical success.
- Leader builds once before direct-report slicing and hands Word, Excel, risk, portfolio, and metadata projections back to the worker.
- Comprehensive finalizes scoped CSConsole and Snowflake Action Plans once; technology input is allowlisted, owner-expanded Snowflake retrieval requires every account alias to remain inside the selected roster, contradictory explicit technology aliases are excluded, unverifiable scope fails closed, the hard-coded secondary health grade is removed, and bundle facts are reused.
- WxCC combines CSOne/Snowflake TAC evidence, deduplicates cross-source cases, reuses compatible snapshots, and carries V2 identity through the export result and job status.
- Ask builds once after full bounded-batch acquisition or validates an exactly scoped supplied bundle; the bounded adapter projection is its sole authority for facts/findings/actions.
- Compact, Renewal, Leader, Comprehensive, Subscription, and WxCC completion/status surfaces carry V2 identity; legacy history rows remain readable as `legacy_unversioned`.

### Synthetic evaluation and before/after guarantees

The fixed `decision_intelligence_eval.py` catalogue covers 20 healthy, severe, temporal, renewal, action, ownership, stale, contradictory, prompt-injection, and AI-unavailable scenarios. Each scenario runs nine common invariants plus one scenario-specific assertion: 200/200 passed with deterministic evaluation fingerprint `evaluation:b3e010b4cf84e45216e3efedbd971fc32fe23b223d900aa16640d4201509d3c1`.

Representative guarantees after V2:

| Synthetic case | Final guarantee |
|---|---|
| No compatible prior | Current facts remain usable; temporal state is explicitly `not_comparable`. |
| Same scope, later observation | Comparison ignores report mode and advancing endpoints but rejects customer/technology/team/schema drift. |
| Same logical ID under two customers | All conflicting rows are quarantined before partition/scoring. |
| Missing incident/AP/account batch | Source is missing or fetch-failed, never a healthy zero. |
| Explicit successful empty fetch | Source is `observed_empty`, distinct from failure. |
| Owner-expanded Snowflake Action Plan fetch | Only rows whose account aliases all resolve inside the selected roster survive; conflicting account/technology aliases are excluded, unsupported technology is rejected, and unverifiable identity fails closed. |
| Repeated physical headers across subscriptions and evidence sources | Identical, blank-plus-value, numeric-equivalent, and datetime-equivalent values coalesce deterministically; contradictory identity, technology, severity, priority, score, status, or renewal values quarantine the affected row and propagate conflict diagnostics. Leading-zero identifier differences remain conflicts. |
| Negative Pulse plus escalated TAC | Emits a relationship/recovery finding that explicitly disclaims causation. |
| Severe evidence with low confidence | Action guardrails preserve urgency while confidence cannot exceed evidence quality. |
| AI synthesis unavailable | Deterministic canonical metrics/findings/actions remain; model failure cannot change facts. |
| Marked frame mutated or concatenated to duplicate an ID | Cache validation fails and canonical dedupe collapses the duplicate. |

### Final validation record

| Validation | Result |
|---|---:|
| Complete new V2 test glob | 127 passed |
| Focused duplicate physical-schema regression | Passed; covered core source families, Comprehensive Action Plan merging, public Mapping/incident ingestion, report scope filtering, and WxCC-style source concatenation |
| Mapped integration/source-guard matrix before the final account-scope delta | 242 passed |
| Final Comprehensive Action Plan delta matrix | 77 passed |
| Expanded Action Plan/Comprehensive regression matrix | 118 passed |
| Offline Ask evaluation marker | 6 passed |
| Fixed synthetic invariant ledger | 200 passed, 0 failed |
| Exact WxCC compatibility matrix | 49 passed |
| V2 app integration | 16 passed |
| Source preservation | 9 passed |
| Static validation | `py_compile`, Ruff, and `git diff --check` clean |

The final independently snapshotted offline repository diagnostic collected 6,575 tests in 104.55 seconds: 6,514 passed, 11 skipped, 6 deselected, 21 failed, and 23 errored. Triage was explicit:

- all 20 legacy non-artifact failures reproduced at clean baseline `842d5b9`;
- seven earlier failures were hard-coded `/Users/jestory/...` paths and are now portable;
- one obsolete legacy Subscription source-shape assertion now pins the stricter V2 build-once/fetch-once/guarded-fallback seam;
- the grounded-answer API preserves its established four positional-or-keyword parameters, including optional structured evidence, with the strengthened signature contract passing;
- the remaining failure and all 23 fixture errors also reproduce at baseline and require absent Round 56/57 historical artifacts.

After the actionable fixes and final source-state, scope, physical-schema, and account-equivalence hardening, the complete V2 glob, exact AP delta, and expanded AP/Comprehensive matrix were green. The 242-test mapped aggregate predates the final AP scope-only deltas; its directly affected modules are included in the final 77- and 118-test runs. The repository-wide result is reported directly rather than inferred from focused results.

### Performance and compatibility

The final correctness-hardened deterministic builder benchmark used Python 3.12.13, pandas 2.2.3, fixed timestamps, one warm-up, and exact per-customer synthetic source shapes:

| Customers | Median | Range |
|---:|---:|---:|
| 1 | 0.160247 s | 0.098705–0.230352 s |
| 10 | 1.464267 s | 1.114794–1.609665 s |
| 100 | 14.127626 s | 11.743249–14.337296 s |

The hardened 100-customer median is 45.1% below the earlier 25.746506-second cache-disabled simulation, though it remains above the non-equivalent 11.054-second pre-V2 Compact observation. All benchmark repetitions had exact customer counts, one stable analysis/request/comparison fingerprint per size, zero reconciliation errors, and unchanged input frames/attributes. Word projection remains the largest measured adapter cost (7.442612 of 8.709731 seconds in the earlier projection benchmark).

Public report modes, established output names/sheets, legacy score scales, synchronous/SSE Ask response fields, and legacy history rows remain compatible. Canonical construction failure is visible and is the only route to legacy calculation on migrated paths.

### Remaining risks and next step

1. Thresholds and action weights are deterministic policy, not outcome-calibrated probabilities.
2. Authorized enterprise data, TLS/Keeper/Snowflake/CSOne behavior, frozen macOS/Windows builds, and representative artifact visual acceptance remain untested here.
3. The temporal engine compares one compatible prior; it is not a multi-period time-series model. Ask-built bundles do not yet load a prior snapshot unless a validated bundle is supplied.
4. Snapshot files are bounded and permission-restricted but not application-level encrypted.
5. Bugs, maintenance, help content, and corpus material remain outside the canonical source contract.
6. The repository still has 21 baseline-reproducible failures and absent Round 56/57 artifact fixtures; green focused matrices are not a claim of a fully green repository.
7. Named-technology Snowflake Action Plan rows without explicit technology attribution now fail closed. Restoring that excluded coverage requires an authoritative AP-to-subscription relationship or enriched source fields.

The next highest-value work is authorized calibration and artifact acceptance: reconcile representative outputs in the target environment, compare bands/change labels/action ranks with de-identified outcomes and structured reviewer judgment, complete live-dashboard and Ask-prior-snapshot integration, establish a committed performance gate, and restore/disposition the historical broad-suite debt without weakening assertions.
