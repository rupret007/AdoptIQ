# AdoptIQ — next-machine handoff (Round 163 all-source intelligence)

Use this prompt after pulling `main`. Round 163 is a large accuracy, predictive-depth,
Ask AI, UI/UX, and stability integration on top of the last packaged release,
`v1.0.4` Build 112.

> Important release distinction: the Round 163 source tree passed the complete local
> acceptance suite, but it has not been packaged as a new installer or reconciled
> against live Cisco production data. Build 112 remains the last Mac installer that
> received a live smoke. Do not call Round 163 “100% production accurate” until the
> live work-machine reconciliation and temporal backtest below are complete.

---

## Start here

```bash
git checkout main
git pull --ff-only origin main
git status --short --branch
git log -3 --oneline
```

Read, in this order:

1. `NEXT_MACHINE_PROMPT.md` — this file
2. `QUALITY_AUDIT.md` — `## Round 163 — all-source intelligence and predictive depth`
3. `CLAUDE.md` — repository rules and source-of-truth constraints
4. `decision_report_delivery.py`, `risk_scoring.py`, and `predictive_signals.py`
5. `ask_ai_grounded.py` and `defect_correlation.py`

Never commit `secrets.env`, Cisco credentials, live customer exports, local acceptance
artifacts, or generated reports.

## Mission and governing contract

AdoptIQ’s advantage is the combined depth of its sources. A report or Ask AI answer
must not merely mention those sources; every applicable source must do one of the
following after the requested manager/member/customer/subscription/technology/time
criteria are applied:

1. contribute to canonical identity, metrics, risk, forecast, narrative, or action;
2. appear as exact, citable decision context; or
3. disclose why it could not support a conclusion.

The non-negotiable invariants are:

- scope first, analyze second;
- stable customer/account/subscription IDs win over display names;
- merge both physical Adoption Barrier feeds with stable-ID deduplication;
- unavailable, failed, stale, partial, and genuine zero are different states;
- never widen a named-technology or selected-customer report to make it look fuller;
- never turn an unavailable integration into an exact zero;
- never smear an untagged portfolio incident or defect across every customer;
- one canonical fact/lineage contract drives Word, Excel, report preview, and Ask AI;
- unsupported or contradictory factual prose blocks publication;
- predictive claims must state coverage and calibration status;
- LLM prose is downstream of deterministic facts and addressable evidence.

## Round 163 outcome

### 1. Criteria-scoped, all-source reporting

- Customer identity/counting is ID-first and spans scoped subscriptions, Action
  Plans, merged Adoption Barriers, Customer Pulse, TAC/CSOne, and Success Priorities.
- Registered name aliases can join an ID-backed identity only when the match is
  unambiguous; distinct stable IDs remain distinct even when names collide.
- Compact, Comprehensive, Renewal, Subscription, Executive Intelligence, and Leader
  now use criteria-scoped inputs through their final Word/XLSX contract.
- Compact and Renewal source workbooks contain real scoped `Subscriptions` sheets;
  `Risk_Summary` and `Renewal_Summary` are no longer misrepresented as subscription
  evidence.
- Named-technology CSConsole data without authoritative technology evidence fails
  closed. Action Plans with ambiguous technology are retained only when their
  customer/account identity is already in the scoped subscription cohort.
- Subscription reports defensively rescope every source to the selected
  subscription/account/customer before validation, scoring, AI, or artifact writing.
- Comprehensive degrades honestly when AB and TAC are empty but scoped subscription
  or CSConsole evidence remains; actual fetch/schema/integrity failures still abort.

### 2. Cross-source decision depth

The canonical report renders a deterministic `Cross-Source Decision Signals` layer.
Each applicable source produces a prioritized signal/action with exact evidence, or
an explicit no-conclusion state:

- Snowflake subscriptions and renewal data;
- Snowflake and CSConsole Action Plans;
- merged Snowflake and CSConsole Adoption Barriers;
- Customer Pulse;
- TAC/CSOne cases and BEMS escalation references;
- Success Priorities;
- Webex status incidents and maintenances;
- external bugs/BST and exact CSC correlations.

Success Priorities, unlinked external bugs, maintenances, PSIRT, and Circuit/status
context are visible and actionable but are not assigned an invented risk weight.

### 3. Predictive intelligence without false certainty

There are two deliberately separate outcomes:

- **Escalation Outlook** estimates near-term escalation watch level from canonical
  TAC, BEMS, Adoption Barrier, and Pulse signals. Its evaluation clock is independent
  of source-freshness timestamps.
- **Renewal Outlook** reports account-attributed Snowflake renewal date/status/
  probability as a source-provided estimate. It is not folded into the escalation
  score and is not described as AdoptIQ-calibrated.

Prediction coverage now distinguishes complete, partial, stale, unavailable, failed,
and verified-zero feeds. Failed/unavailable TAC blocks the escalation forecast;
missing AB/Pulse produces an explicit lower-bound/partial result. Calibration loads
only from an explicit, non-symlinked, bounded JSON artifact whose provenance is
`derived_from=live_cisco_sources`. Missing or invalid calibration fails safely to an
uncalibrated disclosure.

External incidents affect a customer only when account/customer attribution is
present. Untagged status incidents remain portfolio context and contribute no
per-customer weight. Tagged incidents produce a precise driver and incident-specific
next-best action. Failed incident feeds are missing components, not zeros.

### 4. BST/CSC correlation

`defect_correlation.py` is the shared exact-correlation layer:

```python
build_defect_correlation_bundle(
    scoped_tac,
    scoped_ab,
    external_bugs,
    identity_resolver=...,
)
```

It returns bounded deterministic `records`, `unmatched_external_bugs`, and `coverage`.
Only exact normalized CSC references correlate; BEMS references are never treated as
software defects. Records retain canonical customer identity, parent TAC/AB rows and
stable IDs, verified BST status/severity/version, provenance, and action context.
Correlations are exported in `Defect_Correlations`, mapped through Metric Lineage and
Evidence Links, rendered as visible decision signals, and exposed to Ask AI as
tamper-evident evidence. They intentionally have no numeric weight until a live,
labeled backtest supports one.

`CiscoInternalIntegrations` now accepts `bst_client_secret`; all application
construction paths pass `BST_CLIENT_SECRET`. Official BST lookup requires both key
and secret, while absent credentials retain the manual-link/fail-soft path. Logs never
include credentials or response bodies.

### 5. Ask AI

- Ask AI reuses the report’s canonical ID-first risk-profile and predictive seams.
- Its universe includes subscriptions, AP, merged AB, Pulse, TAC, Success Priorities,
  customer-tagged incidents, renewal outlooks, maintenances, and exact BST evidence.
- Deterministic `DecisionMetric`, `RenewalOutlook`, `BSTReference`, and maintenance
  records are citable; tampered or non-rendered IDs are rejected.
- “Who should I call first?” and “who is likely to escalate?” survive final grounding
  with resolvable evidence rather than being stripped as unsupported prose.
- The current user turn controls report-bound intent; conflicting history cannot turn
  a “completed” question into an “overdue” query.
- The browser client owns one answer target per turn, reuses it on retry/fallback,
  delegates live citations from the chat container, records the real sync answer,
  blocks overlapping sends, and distinguishes manual cancellation from timeout.

### 6. UI/UX and progress truth

- Navigation renders a single Run Analysis entry with responsive mobile behavior.
- Report cards retain aligned badge/radio regions and an accessible named radiogroup.
- Progress warnings render the producer’s real dataset/kind/error fields, use warning
  styling, remain visible for terminal CSOne states, and announce updates through an
  ARIA live region.
- Browser checks covered 390 px, 1050 px, and 1440 px: controls remained accessible,
  mobile navigation toggled at the intended widths, desktop showed one Run Analysis
  link, and the loading overlay did not remain stuck.

### 7. Stability and source handling

- CSOne discovery excludes prior AdoptIQ outputs case-insensitively, rejects
  unsupported `.xls`, skips unreadable/zero-byte candidates, and survives per-file
  OneDrive hydration/mtime races by trying the next candidate.
- Comprehensive preserves honest CSOne unavailable/failure state rather than
  overwriting it as a successful scoped-empty load.
- Corpus bootstrap now uses the `_user_corpus_dir()` seam consistently. Tests no
  longer write the real Application Support corpus or scan real user output folders.
- Publication validators remain fail-closed for legacy/canonical risk contradictions,
  factual claims, insight paragraphs, citations, lineage, and source-sheet parity.

## Final verification on the integrated tree

Repository gate:

```text
7,281 collected
14 deselected
7,267 selected
7,259 passed
0 failed
8 skipped
2,458 warnings
523.93s (8:43)
```

Preserved log on the source machine:
`/tmp/adoptiq-round163-final-full-pytest.log`

Complete clean-room acceptance:

```text
7/7 required gates passed; 0 skipped
fixture manifest: 21 scenarios
degraded HTTP: 21/21
decision reports: 2 passes, 4/4 scopes, repeatable
report matrix: 36/36, all A–G blocks
AI feature acceptance: 2/2, repeatable
manager workspace: 14 reports, 8/8 previews, 36 history rows, 0 errors
Ask AI replay: 75/75 questions, 25/25 canonical checks
```

Summary on the source machine:
`/private/tmp/adoptiq-round163-acceptance-final-20260812/round146_acceptance_summary.json`

Additional final gates:

- `git diff --check` — passed
- `.venv/bin/ruff check .` — passed
- production `compileall` — passed
- `node --check static/js/ask_ai.js` — passed
- `.venv/bin/pip check` — passed
- `make eval-ask-ai` — 14/14 passed, including 75/75 replay and 25 checks
- `make security` — passed; no medium/high Bandit findings
- `.venv` `pip-audit --local --strict` — no known vulnerabilities

The acceptance corpus is sanitized and deterministic. Its summary correctly records
`live_validation_performed=false`, `production_accuracy_claimed=false`, and
`release_ready=false`.

## Highest-priority next work

### P0 — live all-source reconciliation on the Cisco work machine

With VPN and approved credentials, run the same manager/technology/time scope through
every report family and reconcile:

- canonical customer IDs and aliases;
- subscription/customer totals;
- TAC/BEMS case totals and deduplication;
- AP, AB, Pulse, and Success Priority counts;
- external incident attribution;
- renewal dates/probabilities;
- exact CSC/BST correlations;
- risk components, bands, Word/XLSX/preview parity, and Ask AI answers.

Do not weaken a validator to make a live mismatch pass. Resolve the first divergent
source, scope, identity, or as-of input.

### P0 — temporal predictive backtest and calibration

Export only approved, de-identified historical snapshots with labels defined before
the forecast window. Run `scripts/backtest_escalation_forecast.py` with strict leakage
controls. Measure calibration, precision/recall, false-negative rate, stability by
source-coverage state, and performance by technology/customer cohort. Only publish a
calibration artifact if its provenance is `derived_from=live_cisco_sources`; only add
new numeric weights when the labeled evidence supports them.

### P0 — package and live-smoke the next build

Round 163 did not bump `ADOPTIQ_BUILD` and did not produce a new DMG/EXE. After live
reconciliation:

1. choose the next build number;
2. rebake the corpus from a clean approved fixture;
3. run release-gated Mac packaging and smoke;
4. run the live report iteration harness plus R114 audit and soak;
5. build/smoke Windows on the PC host;
6. update the merge-aware release manifest without losing either platform slot.

Build 112’s Mac DMG still contains the restored Build 111 corpus snapshot; treat a
fresh corpus bake as required for the next package.

### P1 — predictive evaluation expansion

- Add decision/predictive questions to the committed Ask AI replay corpus.
- Add labeled BST-status/version and renewal-outcome studies before considering new
  numeric weights.
- Test calibration drift and abstention behavior under source outages/staleness.
- Add automated visual viewport/screenshot checks at 390, 992/1050, and 1440 px.

## Useful focused commands

```bash
MPLCONFIGDIR=/tmp/adoptiq-mpl .venv/bin/python -m pytest tests -q --disable-warnings
make eval-ask-ai
make security
.venv/bin/ruff check .
.venv/bin/pip check
.venv/bin/python -m pip_audit --local --strict --progress-spinner off
```

For the complete sanitized acceptance, inspect the current CLI first:

```bash
.venv/bin/python scripts/run_round146_acceptance.py --help
```

Use a fresh output directory and the guarded fixture runtime. Never point the local
acceptance harness at live data or claim that its result validates production.

## Copy/paste kickoff for the next coding agent

```text
Continue AdoptIQ from Round 163 on main. Read NEXT_MACHINE_PROMPT.md and the Round 163
entry in QUALITY_AUDIT.md completely before editing.

The governing rule is criteria-scoped all-source intelligence: every applicable source
must contribute to identity/metrics/risk/forecast/narrative/action or disclose why it
cannot. Missing is never zero; unscopable is never widened; untagged portfolio events
are never smeared across customers. Word, Excel, preview, and Ask AI must reconcile to
the same canonical facts, lineage, evidence, as-of clock, and source coverage.

First run read-only checks and confirm main/remote ancestry. Then choose one bounded
objective: (1) live Cisco all-source reconciliation, (2) leakage-safe temporal
predictive backtest/calibration, or (3) next-build packaging/live smoke. Do not weaken
fail-closed validators, invent weights, commit secrets/live exports, or call sanitized
fixtures proof of 100% production accuracy.
```

**End of Round 163 next-machine handoff.**
