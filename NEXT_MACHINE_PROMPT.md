# AdoptIQ — next-machine handoff (Round 167 / Mac Build 114)

Use this prompt after the verified Round 167 branch has been reviewed, committed, and
pulled from `rupret007/main` on the approved Cisco work Mac. Round 163 is the large
all-source accuracy, predictive-depth, Ask AI, UI/UX, and stability integration;
Round 164 prepared the fail-closed release path; Round 165 made manager-facing reports
action-first; Round 166 fixed Build 113 live acceptance failures; and **Round 167 adds
the production-like local simulation and completeness/link quality gates required
before Build 114 can package**.

> Important release distinction: `OUTBOX/AdoptIQ-v1.0.4-build113.dmg` was packaged from
> pre-R166 source. It does **not** include the Aug 12 acceptance fixes. A stage-only
> **Build 114** candidate now exists from the exact Round 167 source below, but it has
> not received live Cisco truth validation and has not been published or promoted.
>
> **Round 167 candidate source commit:**
> `6a956e203b88a3586251d7f63eae49b319a653c0` (2026-08-13), based on
> `138ac43`. It includes the simulation work, provenance handoff, and direct-preflight
> import fix. At packaging time, `BUILD_SHA` is always the current clean
> `rupret007/main` HEAD returned by `git rev-parse HEAD`; derive and verify it at
> runtime rather than copying this informational implementation SHA into a command.
> Never package an uncommitted, dirty, or upstream-divergent tree.
>
> **Stage-only Build 114 candidate:** `AdoptIQ-v1.0.4-build114.dmg`,
> 1,708,039,038 bytes, SHA-256
> `4cc485daf1bc695ec1376dd6161ce47eb10ca3dae0c67be11c8f91a4a17d6235`.
> Preflight passed with zero warnings; DMG/signature verification and frozen smoke
> passed. The frozen app reported 358/358 files, 488,079 chunks, hybrid retrieval,
> zero dense backlog, and ready embedder/reranker. This is offline packaging evidence,
> not a production-accuracy claim. Use `WORK_MACHINE_BUILD114_PROMPT.md` for the
> copy/paste live handoff.
>
> **OneDrive OUTBOX (2026-08-12):** Build **113** package mirrored to
> `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ`; parent
> `latest.json` mac slot **113** (`sha256=c0195428519ac473a28cfd77bac61319778bdd4e6fa47c35ea91c390996324c6`).
> README banner + handoff docs copied into that folder. **Risk:** auto-update may offer
> Build 113 (pre-R166). Do **not** skip Build 114 packaging + live acceptance before
> re-promoting `latest.json` to build 114.
>
> This handoff is Mac-only. Do not start, stage, or publish a PC build in this run.
> Preserve Build 114's final `BUILD_SHA` for a later, separately approved Windows
> handoff.

---

## Start here

```bash
set -euo pipefail
git checkout main
git pull --ff-only rupret007 main
git status --short --branch
git log -3 --oneline
```

Require the pulled tree to contain `local_snowflake_simulator.py`,
`csone_corpus_replay.py`, `report_completeness_audit.py`,
`source_record_links.py`, and `scripts/run_local_source_contracts.py`. If any are
missing, stop: the machine is on the pre-Round-167 baseline.

## Round 167 mandatory production-simulation gate

Run this before credentials, corpus baking, PyInstaller, OUTBOX writes, manifest
changes, or installation over an existing app:

```bash
make production-simulation \
  CSONE_CORPUS_DIR=/Users/jeffstory/Documents/AdoptIQ_CSOne_Reports \
  OUTPUT_DIR=.adoptiq-acceptance/prebuild-production-simulation
```

If that personal-Mac path does not exist on the work Mac, point
`CSONE_CORPUS_DIR` at an approved external directory containing the current CSOne
exports. Do not copy source workbooks into Git. The gate must be fully green and
must report `production_accuracy_claimed=false` even when it passes.

The gate exercises:

- 23 production fetcher queries through the fail-closed DB-API Snowflake simulator,
  including parameter binding, table policy, retries, account/owner union dedup, and
  secondary attribution;
- all report/technology choices (36 scenarios), then 24 multi-manager scenarios over
  two independent teams plus aggregate-manager scope;
- Compact and Comprehensive portfolio/customer paths, Renewal portfolio/customer,
  Leader Team/Member/Customer, and Subscription reports;
- cross-manager member/customer rejection before publication;
- 23 healthy/degraded/failed/partial/zero source-state combinations;
- deterministic decision reports twice, Ask AI sync/stream and 75-question replay,
  report history, previews, evidence, source links, and canonical Word/XLSX parity;
- a bounded allow-list-pseudonymized replay of representative real CSOne rows, with
  non-record export footers excluded.

### Evidence carried forward from the personal-Mac audit

The final Round 167 personal-Mac run completed on 2026-08-13 and is the comparison
floor, not a substitute for this machine's run:

- `make verify`: Ruff clean, Bandit HIGH/MED clean, dependency audit clean,
  **7,486 passed / 9 skipped / 14 deselected**, Ask AI eval **14/14**;
- production simulation: all gates green, but correctly
  `live_validation_performed=false`, `production_accuracy_claimed=false`, and
  `release_ready=false`;
- external CSOne profile: **358 workbooks / 602,944 rows / one dominant schema**,
  with no source row or value exported;
- representative production-loader replay: three workbooks, 1,776 source rows,
  600 pseudonymous replay rows, six non-record footer rows excluded;
- real fetcher/local Snowflake contract: 23 queries / seven checks, parameter binding
  and secondary attribution green;
- full A-G report matrix: **36/36**; two-manager-plus-aggregate matrix: **24/24**;
- decision reports: four scopes × two passes; Ask AI replay: **75/75** plus
  **25/25 canonical checks**; AI-feature acceptance: two repeatable passes;
- manager workspace: 36 history records, 14 canonical reports, eight previews, and
  Ask AI sync/stream parity;
- separate human rendering review: Leader six pages, Comprehensive five, Renewal
  four, Subscription five; a single-member Compact orphan-disclosure defect was
  fixed and the final strict real-route Compact render is two complete pages.

If the work-machine offline gate is weaker than this floor, do not explain the delta
away as “environment” until the Python version, dependency lock, corpus selection,
fixture clock, source-state scenario, and exact Round 167 commit have been compared.
Any new failure is a stop. Any lower count or missing scenario requires a written,
evidence-backed classification.

Every direct macOS/Windows build script and native CI lane invokes this gate. On a
workstation build, set `ADOPTIQ_CSONE_CORPUS_DIR` so the build-script invocation also
uses real exports:

```bash
export ADOPTIQ_CSONE_CORPUS_DIR=/approved/external/AdoptIQ_CSOne_Reports
```

### Metadata-only Snowflake capability inventory

Before changing SQL or deciding that a useful field is unavailable, inventory only
the allow-listed table schemas using the authorized connection. This command issues
metadata descriptions, not customer-row queries, and writes a sanitized capability
summary outside Git:

```bash
.venv/bin/python scripts/profile_snowflake_capabilities.py \
  --live-metadata \
  --confirm-authorized-live-metadata \
  --summary /approved/external/temporary/snowflake-capabilities.json
```

Do not widen the table allow-list or add row queries merely to make the profile look
complete. Treat missing permission as `blocked`, not as a zero-valued source.

Review the sanitized profile by decision value, not simply by column count. For each
accessible candidate field, record:

1. table/view and column name;
2. business owner and authoritative meaning;
3. stable join key and expected cardinality;
4. manager/member/customer/subscription/technology authorization behavior;
5. null, late-arriving, and duplicate semantics;
6. source event clock versus ETL/load clock;
7. manager decision enabled (prioritization, exposure, ownership, timing, health, or
   traceability);
8. canonical target field and evidence/source-sheet destination;
9. whether the value may influence risk or is context-only;
10. test and live-reconciliation evidence required before release.

Prioritize stable identifiers and source clocks first, then renewal timing and
commercial exposure, then Action Plan execution and Pulse explanation fields.
Product/technology fields are useful only when they make scope stricter or more
explainable; never use a descriptive field to widen an otherwise empty named-
technology result. ARR must remain grouped by currency unless a separately governed
conversion source and as-of rate exist. Source-provided renewal probability must
remain labeled as source-provided and separate from AdoptIQ escalation risk.

### Live Cisco truth run (still mandatory)

Start the normal application with the existing authorized work-machine configuration,
confirm `/ping`, `/api/version`, and `/api/diag/connectivity`, then run:

```bash
.venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir /approved/external/temporary/round167-live \
  --retain-sensitive-dir /approved/external/temporary/round167-live-artifacts \
  work-machine \
  --base-url http://127.0.0.1:5151 \
  --manager '<authorized manager>' \
  --member-email '<roster-authorized member>' \
  --customer-name '<unambiguous authorized customer>' \
  --customer-member-email '<authorized customer owner>' \
  --subscription-id '<authorized subscription>' \
  --technology 'All Contact Center' \
  --days 90 \
  --as-of '<YYYY-MM-DDTHH:MM:SSZ>' \
  --csone-file '/approved/external/current-CSOne-export.xlsx'
```

Reconcile every visible KPI and all four chart series to `Chart_Data`, every claim to
`Metric_Lineage`, and every linked record to `Evidence_Links` and the source-system
row. Specifically inspect unresolved Action Plans, unknown owner/status/due values,
TAC/BEMS distinct IDs, source freshness/partial-state disclosures, and CSConsole
Lightning links. A safe link requires a stable record ID; a missing ID must be stated
as a data-quality limitation rather than rendered as `undefined`, `null`, or a fake
URL. Only this live run plus manual visual/source reconciliation can support a
production-accuracy decision.

Use the following live truth matrix instead of spot-checking only one successful
portfolio report:

| Family | Required scopes | What must be uniquely useful |
|---|---|---|
| Leader | Team, Member, Customer; two passes each | concise manager decisions, whole-team visibility, exact AP moves, shared attribution only where real |
| Comprehensive | authorized manager and aggregate manager; portfolio plus customer where supported | deeper account/source explanation without raw-row duplication |
| Compact | portfolio and customer | short call sheet; no orphan pages; highest-priority action and support change only |
| Renewal | portfolio and individual customer | timing, exposure, source-provided outlook, freshness, and explicit separation from escalation risk |
| Subscription | one authorized subscription | no cross-subscription leakage; selected subscription/account/customer reconciliation |
| Ask AI | sync and stream over Team, Member, Customer, Subscription and report-bound context | identical canonical answer, valid citations, freshness/partial disclosure, safe unanswerable behavior |

For every required report, capture a sanitized reconciliation worksheet outside Git
with: visible claim, displayed value, lineage key, workbook sheet/record ID, live
source query or UI record, match result, reviewer, and timestamp. Review at least one
known missing-ID record and one unavailable/partial source state as well as the happy
path. The correct outcome for missing evidence is an honest limitation, not a filled
number.

The CSConsole link check is not complete when a URL merely looks valid. For at least
one Action Plan, Adoption Barrier, Customer Pulse, and Success Priority with a stable
ID, open the `Evidence_Links` hyperlink in the authorized browser session and confirm
that the object type, record, customer/account, and displayed report claim agree. A
login redirect is not proof of record correctness. Never paste the destination page,
customer content, or credentials into Git or a public handoff.

Read, in this order:

1. `NEXT_MACHINE_PROMPT.md` — this file
2. `QUALITY_AUDIT.md` — the Round 163, Round 164, Round 165, and **Round 166** entries
3. `CLAUDE.md` — repository rules and source-of-truth constraints
4. `decision_report_delivery.py`, `canonical_metrics.py`, `risk_scoring.py`, and
   `predictive_signals.py`
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
- external bugs, PSIRT advisories, and exact CSC correlations (manual CSC lookup via
  https://bst.cloudapps.cisco.com/bugsearch — web portal only, no API).

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

### 4. CSC / PSIRT correlation

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
stable IDs, CSC reference text, provenance, and action context. Manual deep links to
individual CSC IDs use `defect_portal_url()` (static URL only — no HTTP to Bug Search
Tool). PSIRT openVuln is the only Cisco security API (`PSIRT_API_KEY` /
`PSIRT_CLIENT_SECRET` in `secrets.env`; required by release preflight). Bug Search Tool
has no API — operators use https://bst.cloudapps.cisco.com/bugsearch in a browser.
Correlations are exported in `Defect_Correlations`, mapped through Metric Lineage and
Evidence Links, rendered as visible decision signals, and exposed to Ask AI as
tamper-evident evidence. They intentionally have no numeric weight until a live,
labeled backtest supports one. Logs never include credentials or response bodies.

### 5. Ask AI

- Ask AI reuses the report’s canonical ID-first risk-profile and predictive seams.
- Its universe includes subscriptions, AP, merged AB, Pulse, TAC, Success Priorities,
  customer-tagged incidents, renewal outlooks, maintenances, PSIRT advisories, and
  exact CSC correlation evidence.
- Deterministic `DecisionMetric`, `RenewalOutlook`, `BSTReference` (CSC ID citations,
  not API-backed), and maintenance
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

## Round 165 outcome

### 1. Action-first, report-family-aware decision briefs

- The canonical Word report now places a family-specific `Decision Brief` directly
  after the frozen Executive Summary: Leader Interventions, Comprehensive Portfolio
  Priorities, Compact Immediate Customer Calls, Renewal Decisions, or Subscription
  Decisions.
- Complete evidence renders a readable four-column risk table (`Account`, `Risk`,
  `Why`, `First move`) and a bounded top Action Plans table (`Action Plan`,
  `Account / owner`, `Urgency`, `First move`). Decision insights sit with those first
  actions under `What Is Changing`, instead of being buried later in the report.
- Renewal and Subscription briefs expose their defining commercial facts as exact
  decision facts while retaining adjacent evidence and workbook lineage. Incomplete
  risk or Action Plan feeds render one precise gap statement instead of a misleading
  empty table.
- Cross-source signals use four manager-readable columns while exact evidence keys
  remain adjacent and resolvable. If all four charts are withheld, the report emits
  one consolidated explanation rather than repeating the same coverage warning four
  times.

### 2. Accuracy, completeness, and fail-closed delivery

- The reporting window now has one canonical calendar-day contract: start at midnight
  `(days - 1)` days before the evaluation date and end at the exact evaluation
  timestamp. Momentum, activity trend, decision insights, evidence windows, and
  fingerprints share those same bounds.
- Executive Summary title, summary, citation, and purpose paragraphs are frozen and
  semantically validated as one exact adjacent block. Family-specific decision briefs
  and their tables are likewise validated before publication.
- Unresolved Action Plans now carry canonical age bands (`0–14`, `15–30`, `31–60`,
  `61–90`, `>90`, `Unknown`). Completed rows are excluded from aging. The existing
  Action Plan visual has separate lifecycle and unresolved-age panels; `Chart_Data`,
  Metric Lineage, Evidence Links, Word, and the `Action_Plans.AdoptIQ_Age_Band` export
  are reconciled to the same facts.
- Whole-team output no longer silently stops at 15 members. The ordinary roster ceiling
  is 64, with an end-to-end 18-member regression across facts, Word, XLSX, artifact
  contract, and word budget.
- Direct report downloads verify the persisted authoritative SHA-256 before serving
  current canonical DOCX/XLSX bytes. Missing, malformed, unreadable, or mismatched
  current artifacts fail with HTTP 409. Older hashless history is explicitly marked
  `legacy-unverified`; it is never mislabeled as verified.

### 3. Canonical workbook and visual QA

The Source Data workbook remains the intentionally stable 17-sheet forensic contract:
`Report_Info`, `Metric_Lineage`, `Chart_Data`, `Evidence_Links`, `Action_Plans`,
`Adoption_Barriers`, `Customer_Pulse`, `TAC_Cases`, `BEMS`, `Subscriptions`,
`Success_Priorities`, `External_Incidents`, `External_Bugs`, `Defect_Correlations`,
`Risk_Components`, `Member_Summary`, and `Account_Summary`. Do not add, rename, or
drop sheets during the build handoff.

Source-machine visual QA rendered and inspected every page of both degraded and
complete Word fixtures and every sheet of the complete workbook:

- degraded Leader fixture: 3 pages, honest risk/AP gaps, one chart-withholding note,
  no clipping, overlap, or broken tables;
- complete Leader fixture: 6 pages, action-first brief, four readable visuals
  including the two-panel Action Plan lifecycle/age chart, no clipping or overlap;
- complete 17-sheet XLSX: every sheet rendered, formulas/error scan clean, expected
  filters/freeze panes/native tables present.

A workbook dashboard/navigation sheet remains a deliberate follow-on. The build must
preserve the 17-sheet canonical contract rather than introducing a rushed schema
change.

### 4. Brian Frazier acceptance state

The code/test/fixture closure now covers Brian's requested concise decision report,
prioritization, whole-team visibility, source honesty, ID-first identity, trend/age
visibility, and separation of decision narrative from raw records. It does **not**
constitute a current live Cisco sign-off. On the work Mac, Brian Frazier / All Contact
Center / 90d must be regenerated as both Leader and Comprehensive and reconciled
against the current 17-sheet workbook and source systems.

The live run must specifically resolve or document:

- exact account/customer/subscription identities, aliases, totals, and all secondary
  owner/member rows;
- a nonzero, readable, correctly scoped CSOne/TAC input, or an explicit honest
  unavailable/failed resolution—never an inferred zero;
- the raw Action Plan status domain, including any terminal variants not represented
  in the sanitized fixture, with unknowns disclosed rather than silently classified;
- decision-brief ordering, drivers, first moves, and family-specific facts as useful
  to Brian, not merely structurally present;
- report/preview/XLSX/Ask AI parity for the same scope and evaluation clock.

## Round 164 verification baseline

Repository gate:

```text
7,335 collected
14 deselected
7,321 selected
7,313 passed
0 failed
8 skipped
2,458 warnings
470.44s (7:50)
```

Preserved log on the source machine:
`/tmp/adoptiq-round164-final3-tests.log`

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
`/private/tmp/adoptiq-round164-acceptance-final-authorized-20260812/round146_acceptance_summary.json`

The Round 164 clean-room run started at `2026-08-12T07:38:42Z`, completed at
`2026-08-12T07:49:28Z`, and passed all required local gates. The first sandboxed
attempt was discarded because macOS denied loopback binds; only the authorized
loopback result above is release evidence.

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

## Round 166 outcome (Build 113 live acceptance fixes)

Aug 12 Build 113 operator runs surfaced four defects; Round 166 closes them in source:

| Run | Fix |
|-----|-----|
| All Managers **Compact** `error` | `canonical_report_adapter._strip_placeholder_rows` reconciles `Source_State=zero` when substantive rows remain; Compact Report_Info declares `partial` when risk universe has customers but subscription roster is empty for scope |
| **Comprehensive** degraded | `_r166_comprehensive_prefetch_freshness` threads source clocks into `_r142_build_facts`; member partition uses authorized roster (`_comprehensive_fetch_subs_df`), not tech-scoped subs slice |
| All Managers **Leader** `error` | Leader worker CSSM roster includes `manager == "All Managers"` branch before Pass 1 |
| UX | Optimistic `Queued…` job row before start POST; Leader card no permanent orange ring |

**Live regen gate (mandatory before promotion):** regenerate Brian Frazier + All Managers /
All Contact Center / 90d **Comprehensive**, **Compact**, and **Leader** on VPN; run
`python3 scripts/r114_audit_reports.py --auto`. Comprehensive must show real **Data as of**
when prefetch succeeded; Compact must complete; Leader All Managers must pass Pass 1.

Regression tests: `tests/test_round166_*.py` (14 tests). Verify floor: **7375 passed** /
7 skipped.

## Round 165 source-machine verification

The handoff is the final source commit by design, so its own SHA cannot be embedded in
its contents without creating another commit. The work Mac must resolve the final
clean `rupret007/main` HEAD and pin it as `BUILD_SHA` in Step 1; that exact value is
the Build 114 source identity and the only commit eligible for the later PC build.

Final source-machine evidence from this tree:

```text
Source identity: final clean rupret007/main HEAD, resolved and pinned as BUILD_SHA
Full pytest: 7,355 passed, 8 skipped, 14 deselected, 0 failed (7,377 collected; 7,363 selected; 574.71s)
Focused Round 165 + download routes: 55 passed, 0 failed (47.05s)
Static gates: repository Ruff, production compileall, Node syntax, pip check, and git diff --check passed
Security gates: Ask AI eval 14/14 (replay 75/75; canonical 25/25), Bandit passed with no medium/high findings, pip-audit found no known vulnerabilities
Clean-room acceptance: 7/7 gates, report matrix 36/36, Ask AI replay 75/75, canonical checks 25/25
```

The clean-room summary is
`/private/tmp/adoptiq-r165-cleanroom-authoritative/round146_acceptance_summary.json`.
It correctly records `live_validation_performed=false`,
`production_accuracy_claimed=false`, and `release_ready=false`; live Brian/source
reconciliation remains a work-Mac release gate.

## Required next action: Build 114 on the approved work Mac

Follow this runbook in order and stop at the first failed gate. Build 114 is set in
`config.py`. Round 166 landed after the Build 113 DMG was baked; **do not promote
Build 113** — package Build 114 from this commit. If source changes after the candidate
DMG is created, land the fix as a later build number and restart the Mac gates from the
beginning. Do not build Windows in this handoff.

### 1. Pull and pin the source

Start from a clean work-machine checkout. Preserve any local work before this block;
do not discard it with a reset or checkout command.

```bash
set -euo pipefail
git checkout main
git fetch rupret007 main
git pull --ff-only rupret007 main
git branch --set-upstream-to=rupret007/main main
git status --short --branch
test -z "$(git status --porcelain=v1 --untracked-files=all)"
BUILD_SHA="$(git rev-parse HEAD)"
test "$(git rev-parse --abbrev-ref --symbolic-full-name @{upstream})" = "rupret007/main"
test "$BUILD_SHA" = "$(git rev-parse rupret007/main)"
test "$(python3 -c 'from config import ADOPTIQ_VERSION; print(ADOPTIQ_VERSION)')" = "1.0.4"
test "$(python3 -c 'from config import ADOPTIQ_BUILD; print(ADOPTIQ_BUILD)')" = "114"
echo "Pinned Build 114 source: $BUILD_SHA"
```

Record `BUILD_SHA` in the Mac build, smoke, acceptance, and promotion evidence. It is
also the only source commit eligible for a later separately approved PC handoff.
The later Windows build must use this exact commit, but do not run it in this handoff.

### 2. Prepare the private build inputs

`secrets.env` must sit at the repository root, remain Git-ignored/untracked, and be
owner-only. Never paste its values into a prompt, console transcript, commit, or test
summary.

```bash
test -f secrets.env
chmod 600 secrets.env
git check-ignore -q secrets.env
! git ls-files --error-unmatch secrets.env >/dev/null 2>&1
```

Round 165.1 removed all `BST_*` keys. Release preflight requires the **PSIRT** pair
(`PSIRT_API_KEY`, `PSIRT_CLIENT_SECRET`) only — not Bug Search Tool credentials.

Choose the exact approved, fully hydrated local folder for this build. Do not rely on
auto-discovery for a production bake. It must contain the intended `.csv`, `.docx`,
and/or `.xlsx` source files; nested folders are preserved. Any supported zero-byte,
unreadable, symlinked, corrupt, or semantically empty file makes the release bake fail
rather than silently thinning the corpus. Replace the example path below:

```bash
export ADOPTIQ_BAKE_FIXTURE_DIR="/ABSOLUTE/PATH/TO/APPROVED/AdoptIQ_CSOne_Reports"
test -d "$ADOPTIQ_BAKE_FIXTURE_DIR"
```

Set the expected native architecture explicitly. Use `arm64` on an Apple Silicon Mac
or `x86_64` on an Intel Mac. Do not run an Apple Silicon package through Rosetta.

```bash
export ADOPTIQ_EXPECTED_ARCH="$(uname -m)"
case "$ADOPTIQ_EXPECTED_ARCH" in arm64|x86_64) ;; *) exit 1 ;; esac
export ADOPTIQ_FASTEMBED_CACHE="$HOME/Library/Caches/AdoptIQ/fastembed"
mkdir -p "$ADOPTIQ_FASTEMBED_CACHE"
```

The persistent model cache is a quality-neutral speedup: it reuses identical
embedding/reranker bytes and does not change corpus rows, chunks, vectors, scores, or
validation. Build 114 packages the validated cache with deterministic SHA-256
evidence, so a Finder-launched app stays hybrid-ready without a first-run download.
The bake also removes a duplicate database seal. Runtime hybrid retrieval reuses baked
candidate vectors only when the complete model/dimension/blob/finite-value contract
validates; otherwise it embeds the same candidates live. It still parses every
supported source, requires meaningful chunks, writes and counts every dense vector,
runs semantic embedding and reranker probes, encrypts the fresh corpus once, and
performs the decrypt round trip. Do not use `ADOPTIQ_BAKE_CORPUS=0`, `--no-bake`, a
partial-vector limit, or an old baked snapshot for a shipping candidate.

### 3. Recreate the approved environment and run every source gate

Prefer a fresh `.venv` on the native host. Dependency/model downloads may require the
Cisco trust chain/VPN. The release preflight is read-only and never prints values or
creates `_bundled_secrets.py`.

```bash
set -euo pipefail
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -c constraints-build114.txt
.venv/bin/python -m pip install -c constraints-build114.txt ruff bandit pip-audit
.venv/bin/python -m pip check

ADOPTIQ_EXPECTED_COMMIT="$BUILD_SHA" \
  .venv/bin/python scripts/preflight_mac_release.py \
  --expected-version 1.0.4 \
  --expected-build 114 \
  --expected-commit "$BUILD_SHA" \
  --expected-arch "$ADOPTIQ_EXPECTED_ARCH" \
  --corpus-source "$ADOPTIQ_BAKE_FIXTURE_DIR" \
  --summary /tmp/adoptiq-build114-mac-preflight.json

MPLCONFIGDIR=/tmp/adoptiq-build114-mpl \
  .venv/bin/python -m pytest tests -q --disable-warnings \
  2>&1 | tee /tmp/adoptiq-build114-full-pytest.log
make eval-ask-ai
make security
.venv/bin/ruff check .
.venv/bin/pip check
.venv/bin/python -m pip_audit --local --strict --progress-spinner off
git diff --check
node --check static/js/ask_ai.js
PYTHONPYCACHEPREFIX=/tmp/adoptiq-build114-pyc \
  .venv/bin/python -m compileall -q -f \
  -x '(^|/)(tests|\.venv|build|dist|OUTBOX)(/|$)' .
git status --short --branch
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

The Round 166 verify floor is 7,375 passed, 7 skipped, 14 deselected,
and zero failures. The work Mac must collect at
least that suite and produce zero failures; do not build around a failed or
unexpectedly smaller gate.

Run the complete sanitized clean-room acceptance again from this exact commit:

```bash
set -euo pipefail
ACCEPT_DIR="/tmp/adoptiq-build114-cleanroom-$(date -u +%Y%m%dT%H%M%SZ)"
MPLCONFIGDIR=/tmp/adoptiq-build114-mpl \
  .venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir "$ACCEPT_DIR" local
.venv/bin/python - <<'PY' "$ACCEPT_DIR/round146_acceptance_summary.json"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
assert p["all_passed"] is True
assert p["acceptance_complete"] is True
assert p["profile"] == "local" and p["fixture_validation_passed"] is True
assert not p["skipped_gates"]
assert set(p["required_gates"]) == {
    "fixture_manifest", "degraded_http", "decision_reports", "report_matrix",
    "ai_features", "manager_workspace", "ask_ai_replay",
}
matrix = p["gates"]["report_matrix"]
assert matrix["scenario_count"] == matrix["completed_count"] == matrix["passed_count"] == 36
assert matrix["failed_count"] == 0 and matrix["all_report_blocks_requested"] is True
replay = p["gates"]["ask_ai_replay"]
assert replay["question_count"] == replay["passed_count"] == 75
assert replay["canonical_check_count"] == replay["canonical_passed_count"] == 25
assert p["live_validation_performed"] is False
assert p["production_accuracy_claimed"] is False and p["release_ready"] is False
print("clean-room acceptance passed")
PY
```

### 4. Build a local candidate; do not publish yet

`build_mac_dmg.sh` automatically repeats the release preflight, snapshots the two
validated pinned models with `scripts/stage_release_models.py`, verifies the model
manifest, and only then starts the expensive fresh corpus bake. It scrubs the
generated reversible `_bundled_secrets.py` on every exit. The default is stage-only:
it writes `OUTBOX/AdoptIQ-v1.0.4-build114.dmg` and a local manifest, but it does not
update OneDrive or `latest.json` for consumers.

```bash
set -euo pipefail
export ADOPTIQ_VERSION="1.0.4"
export ADOPTIQ_BUILD="114"
export ADOPTIQ_EXPECTED_COMMIT="$BUILD_SHA"
export ADOPTIQ_RELEASE_GATE="1"
export ADOPTIQ_BAKE_CORPUS="1"
export ADOPTIQ_PUBLISH_RELEASE="0"
time bash build_mac_dmg.sh 2>&1 | tee /tmp/adoptiq-build114-mac-build.log

test -f OUTBOX/AdoptIQ-v1.0.4-build114.dmg
test -f embeddings/release_fastembed_cache/adoptiq_model_manifest.json
test ! -e _bundled_secrets.py
hdiutil verify OUTBOX/AdoptIQ-v1.0.4-build114.dmg
codesign --verify --strict OUTBOX/AdoptIQ-v1.0.4-build114.dmg
grep 'bake timing:' /tmp/adoptiq-build114-mac-build.log
grep -F "Source commit: $BUILD_SHA" OUTBOX/build_info.txt
```

The timing lines identify `stage_inputs`, `parse_and_lexical_index`, `dense_vectors`,
`reranker_self_test`, `encrypt_and_commit`, `decrypt_round_trip`, and `total`. Keep
the log so the next optimization targets measured work. Do not infer that a slow phase
can be skipped; preserve its output contract.

### 5. Verify the packaged bytes and inspect the UI

```bash
.venv/bin/python scripts/smoke_frozen_candidate.py \
  --candidate OUTBOX/AdoptIQ-v1.0.4-build114.dmg \
  --expected-version 1.0.4 \
  --expected-build 114 \
  --require-release-corpus \
  --summary /tmp/adoptiq-build114-mac-smoke.json
```

Then mount/install the DMG and verify, at minimum:

- Gatekeeper/unblock flow and first-launch startup;
- version footer shows v1.0.4 build 114;
- Corpus panel reports the fresh baked snapshot and dense retrieval readiness;
- Analyze at 390 px, 1050 px, and 1440 px with no overlap/stuck overlay;
- one Compact, Comprehensive, Renewal, Subscription, Executive Intelligence, and
  Leader artifact can be opened; Word, XLSX, preview, scope, lineage, and citations
  reconcile;
- every current canonical download returns
  `X-AdoptIQ-Artifact-Integrity: sha256-verified`; changing or removing a persisted
  artifact in a disposable test copy produces HTTP 409 rather than serving bytes;
- each Word artifact begins with the exact Executive Summary block followed by the
  correct family-specific Decision Brief; risk and Action Plan first moves remain
  readable, and unavailable inputs render the exact gap state;
- the Source Data workbook has exactly the 17 canonical sheets listed above,
  `Action_Plans.AdoptIQ_Age_Band` reconciles to the lifecycle/age visual, completed
  plans have no age band, and every visible evidence key resolves;
- Ask AI supports decision, prediction, renewal, TAC/BEMS, PSIRT, and CSC-correlation
  questions with clickable evidence;
- no credentials, generated live reports, or raw corpus source files appear in the
  source checkout or user-visible outer DMG payload.

### 6. Run live work-machine acceptance against the packaged candidate

Launch the installed Build 114 candidate on loopback port 5153. Use Brian Frazier /
All Contact Center / 90d as the mandatory manager scope and replace the member,
customer, and subscription placeholders with approved identities from that scope.
Retain sensitive artifacts only outside Git and only when the operator has explicitly
approved that directory.

```bash
set -euo pipefail
LIVE_DIR="/tmp/adoptiq-build114-live-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python scripts/run_round146_acceptance.py \
  --output-dir "$LIVE_DIR" \
  work-machine \
  --base-url http://127.0.0.1:5153 \
  --manager "Brian Frazier" \
  --member-email "APPROVED MEMBER EMAIL" \
  --customer-name "APPROVED CUSTOMER" \
  --customer-member-email "APPROVED CUSTOMER MEMBER EMAIL" \
  --subscription-id "APPROVED SUBSCRIPTION ID" \
  --technology "All Contact Center" \
  --days 90 \
  --as-of "YYYY-MM-DD" \
  --csone-file "/ABSOLUTE/PATH/TO/APPROVED/CSOne.xlsx"
```

Require `all_passed=true`, `acceptance_complete=true`,
`live_validation_performed=true`, `live_validation_passed=true`, no skipped gates,
and a Git SHA equal to `BUILD_SHA`. Also require `git.branch=main`, `git.dirty=false`,
and `gates.runtime_identity` to report `status=passed`, `version=1.0.4`, `build=114`,
`frozen=true`, `restart_required=false`, and `live_validation_performed=true`.
Separately reconcile exact source rows/counts, risk components/bands, predictive
coverage, renewal values, CSC portal links, PSIRT advisories, and
Word/XLSX/preview/Ask AI claims. The harness intentionally does not set
`production_accuracy_claimed`, `manual_source_reconciliation_complete`,
`visual_review_complete`, or `release_ready`; those require human evidence.

```bash
.venv/bin/python - <<'PY' "$LIVE_DIR/round146_acceptance_summary.json" "$BUILD_SHA"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
r = p["gates"]["runtime_identity"]
assert p["all_passed"] and p["acceptance_complete"]
assert p["live_validation_performed"] and p["live_validation_passed"]
assert not p["skipped_gates"]
assert p["git"]["sha"] == sys.argv[2]
assert p["git"]["branch"] == "main" and p["git"]["dirty"] is False
assert r["ok"] and r["status"] == "passed" and r["live_validation_performed"]
assert (r["version"], r["build"], r["frozen"], r["restart_required"]) == ("1.0.4", "114", True, False)
print("live installed-candidate identity and acceptance passed")
PY
```

After the automated work-machine profile, generate fresh **Leader** and
**Comprehensive** Brian Frazier / All Contact Center / 90d artifacts from the
installed app and complete this human reconciliation before setting either promotion
attestation:

1. Confirm both workbooks contain exactly the 17 canonical sheets and that every
   count, stable ID, alias, secondary owner/member row, raw row, visible claim,
   Evidence Link, and Metric Lineage entry agrees with the approved source extracts.
2. Require a readable CSOne workbook and inspect raw/scoped TAC counts. A true nonzero
   scoped population must remain nonzero in TAC/BEMS/report/Ask AI; an unavailable,
   failed, or genuinely out-of-scope source must retain that exact state. Stop on an
   unexplained zero.
3. Inventory the distinct raw Action Plan statuses from the live scope. Confirm each
   maps to the intended lifecycle bucket, unresolved age band, or explicit `Unknown`;
   completed rows must not be aged. Record any unrecognized terminal variant rather
   than changing the classifier during the build.
4. Ask Brian (or the approved reviewer) to confirm that the first three risks/actions,
   `Why`, `First move`, `What Is Changing`, and Renewal/Subscription decision facts
   are the right decision order and are specific enough to act on.
5. Ask the matching decision, prediction, renewal, TAC/BEMS, PSIRT, and CSC-correlation
   questions in Ask AI. Reconcile answers and citations to the same report facts, scope,
   source states,
   identity, and evaluation window.

If any mismatch needs code, stop. Do not patch the built app or reuse Build 114;
create the follow-on source/build change and restart from source verification.

### 7. Promote only after all checks pass

This is the only publication step. It validates DMG/app signatures and integrity,
smoke identity, same-commit live acceptance, the two explicit human attestations,
safe managed OneDrive paths, and a valid existing PC manifest slot. It copies bytes
first and atomically updates `latest.json` last; it refuses a corrupt/PC-less manifest
instead of silently erasing the Windows slot.

```bash
set -euo pipefail
.venv/bin/python scripts/promote_mac_release.py \
  --dmg OUTBOX/AdoptIQ-v1.0.4-build114.dmg \
  --smoke-summary /tmp/adoptiq-build114-mac-smoke.json \
  --acceptance-summary "$LIVE_DIR/round146_acceptance_summary.json" \
  --expected-version 1.0.4 \
  --expected-build 114 \
  --expected-commit "$BUILD_SHA" \
  --manual-source-reconciliation-complete \
  --visual-review-complete \
  --approve-publish \
  --summary /tmp/adoptiq-build114-mac-promotion.json
```

Preserve `OUTBOX/latest.before-mac-build114.json` as the rollback manifest. Verify the
consumer `latest.json` Mac slot is Build 114 and its PC slot is byte-for-byte
unchanged. If any post-promotion issue appears, restore the saved manifest atomically
and remove only the Build 114 Mac artifact after resolving its exact path.

### 8. Stop after the Mac run

Do not move `secrets.env` to a PC, run `build_pc.bat`, stage Windows artifacts, or
modify the PC release-manifest slot in this handoff. Preserve `BUILD_SHA`, the Mac
build/smoke/acceptance logs, corpus/model manifests, artifact hashes, and promotion or
rollback evidence for the later Windows task.

The current Mac scripts use ad-hoc signing for internal distribution; they do not use
a configured Developer ID, hardened runtime, notarization, or stapling. Do not
represent this candidate as publicly notarized. If Cisco distribution policy requires
those controls, stop before promotion and add/verify the real signing workflow first.

### P0 — live Brian all-source reconciliation on the Cisco work machine

With VPN and approved credentials, run Brian Frazier / All Contact Center / 90d
through Leader and Comprehensive first, then the remaining applicable report families,
and reconcile:

- canonical customer IDs and aliases;
- subscription/customer totals;
- TAC/BEMS case totals and deduplication;
- AP, AB, Pulse, and Success Priority counts;
- external incident attribution;
- renewal dates/probabilities;
- exact CSC correlations and PSIRT advisories;
- Action Plan raw-status classification and unresolved age bands;
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

### P0 — promote Build 114 after live smoke

Round 166 sets `ADOPTIQ_BUILD=114`. Before calling Build 114 a production release:

1. rebake the corpus from a clean approved fixture (Build 113 bake is stale for promotion);
2. run release-gated Mac packaging and smoke on **Build 114** source;
3. run clean-room acceptance, the live Brian + All Managers ACC report acceptance,
   download-integrity checks, source reconciliation, visual review, and soak;
4. obtain Apple notarization if required;
5. update the merge-aware release manifest without changing the existing PC slot.

Do **not** promote `OUTBOX/AdoptIQ-v1.0.4-build113.dmg` — it predates Round 166 fixes.

**Current OneDrive state (pre–Build 114):** Consumers on synced OneDrive already see
mac build **113** in `latest.json`. That is intentional interim distribution of the last
packaged artifact only. After Build 114 passes live acceptance, promotion must replace the
mac slot (preserve any existing `pc` slot byte-for-byte) via `promote_mac_release.py` or
the release-gated mirror block — never hand-edit `latest.json` on Windows from the Mac run.

### OneDrive sync reference (Build 113 baseline — 2026-08-12)

| Item | Value |
|------|--------|
| Consumer folder | `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/AdoptIQ` |
| Manifest | `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/latest.json` |
| Staging | `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/OUTBOX` |
| DMG sha256 | `c0195428519ac473a28cfd77bac61319778bdd4e6fa47c35ea91c390996324c6` |
| Verified | `hdiutil verify` VALID; mirrored `.app` codesign OK |

Re-publish manifest after Build 114 only:

```bash
python3 scripts/write_release_manifest.py \
  --manifest "$HOME/Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX/latest.json" \
  --platform mac --version 1.0.4 --build 114 \
  --artifact "AdoptIQ/AdoptIQ-v1.0.4-build114.dmg" \
  --sha256 "<computed>" --size "<bytes>"
```

Prefer `scripts/promote_mac_release.py` when all acceptance gates pass — it enforces
signatures, human attestations, and PC-slot preservation.

- Add decision/predictive questions to the committed Ask AI replay corpus.
- Add labeled CSC-status/version and renewal-outcome studies before considering new
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

Copy the complete contents of `WORK_MACHINE_BUILD114_PROMPT.md`. It pins the Build
114 candidate source/hash and requires the live source, report, record-link, AI,
multi-manager scope, visual-review, and promotion stop gates while this file remains
the detailed evidence and command runbook.

**End of Round 167 next-machine handoff.**
