# Round 169 — Report Audit (Build 116 / Aug-14 session)

**Audit date:** 2026-08-15  
**Auditor:** Cursor (read-only)  
**Internal source SHA:** `49660737d1251dac838c1f16da6032c6a40372a4`  
**App version / build:** 1.0.4 / 116  
**External product base (Codex):** `4350b39` on `codex/round168-evidence-led-improvements`

## Executive summary

This audit reviewed **24 paired decision-report artifacts** generated during the **2026-08-14** Round 168 integration/debug session. All pairs are **fixture-backed simulation outputs** under the local acceptance tree (`Local_Fixture_Manager` scope, Manager A alias). **`live_validation_performed=false`** — no VPN/Snowflake/CSConsole live acceptance was executed for this corpus.

**Headline:** The Build 116 fixture matrix is largely healthy: all 24 pairs honor the **17-sheet Source Data contract**, pass the **Word content budget** gate, carry **build 116 footers**, and show **zero mid-string citation injections**. Four actionable themes remain: **(1)** missing embedded Word charts on technology-filtered Comprehensive runs, **(2)** CSOne upload shape issues for Customer A/B inputs, **(3)** cross-folder row-count drift among debug reruns (documented limitation), and **(4)** **no live AdoptIQ outputs** were found for Customer A/B customer-scoped runs.

| Severity | Count |
|----------|------:|
| critical | 0 |
| high | 0 |
| medium | 4 |
| low | 0 |
| info | 1 |

**Do not claim production accuracy** from this audit. Fixture `partial` / `unavailable` / `zero` source states were observed and are treated as honest disclosure unless contradicted by narrative claims.

---

## Audit corpus disclaimer

| Corpus | Role | Count | Notes |
|--------|------|------:|-------|
| AdoptIQ DOCX + Source Data pairs | Primary | 24 | Aug-14 mtime; `debug-b-comp-all` + `debug-matrix2` buckets |
| CSOne session uploads (Customer A/B) | Source-quality only | 2 | Single-sheet exports; not AdoptIQ workbooks |
| Live customer AdoptIQ outputs | Expected but **missing** | 0 | No customer-scoped pairs under Documents/App Support for Aug-14 |

Pairing method: `decision_report_delivery.source_data_path_for_word` + `Report_Info` key confirmation (`Report_Type`, `Scope_Type`, `Scope_Value`, `Technology`, `Days`, `Data_As_Of_UTC`).

Private evidence (not in Git): `.adoptiq-audit-evidence/round169/` (`inventory.json`, `audit_summary.json`, `pair_metrics.json`).

---

## Inventory (24 pairs)

| Pair ID | Bucket | Report family | Scope | Technology alias | Days | DOCX charts |
|---------|--------|---------------|-------|------------------|-----:|------------:|
| R169-PAIR-01 | debug-b-comp-all | Comprehensive | team / Manager A team | All | 90 | 4 |
| R169-PAIR-02 | debug-b-comp-all | Comprehensive | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-03 | debug-b-comp-all | Comprehensive | team / Manager A team | Cisco UCCE | 90 | 0 |
| R169-PAIR-04 | debug-b-comp-all | Comprehensive | team / Manager A team | Cisco UCCX | 90 | 0 |
| R169-PAIR-05 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Calling | 90 | 0 |
| R169-PAIR-06 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Contact Center | 90 | 0 |
| R169-PAIR-07 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Contact Center Enterprise | 90 | 0 |
| R169-PAIR-08 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Meetings & Messaging | 90 | 0 |
| R169-PAIR-09 | debug-b-comp-all | Comprehensive | team / Manager A team | All | 90 | 4 |
| R169-PAIR-10 | debug-b-comp-all | Comprehensive | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-11 | debug-b-comp-all | Comprehensive | team / Manager A team | Cisco UCCE | 90 | 0 |
| R169-PAIR-12 | debug-b-comp-all | Comprehensive | team / Manager A team | Cisco UCCX | 90 | 0 |
| R169-PAIR-13 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Calling | 90 | 0 |
| R169-PAIR-14 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Contact Center | 90 | 0 |
| R169-PAIR-15 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Contact Center Enterprise | 90 | 0 |
| R169-PAIR-16 | debug-b-comp-all | Comprehensive | team / Manager A team | Webex Meetings & Messaging | 90 | 0 |
| R169-PAIR-17 | debug-matrix2 | Compact | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-18 | debug-matrix2 | Leader | team / Entire team | All | 90 | 0 |
| R169-PAIR-19 | debug-matrix2 | Comprehensive | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-20 | debug-matrix2 | Renewal Portfolio | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-21 | debug-matrix2 | Compact | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-22 | debug-matrix2 | Comprehensive | team / Manager A team | All Contact Center | 90 | 0 |
| R169-PAIR-23 | debug-matrix2 | Leader | team / Entire team | All | 90 | 0 |
| R169-PAIR-24 | debug-matrix2 | Renewal Portfolio | team / Manager A team | All Contact Center | 90 | 0 |

Report families: Comprehensive (18), Compact (2), Leader (2), Renewal Portfolio (2).

---

## Defect register

### R169-001 — Missing embedded Word charts on technology-filtered runs

| Field | Value |
|-------|-------|
| Severity | medium |
| Category | chart_embedding |
| Classification | Confirmed product defect |
| Affected pairs | 20 of 24 (all where Technology ≠ `All` on Comprehensive matrix; Compact/Leader/Renewal pairs also 0 charts) |
| Evidence | `Chart_Data` rows present (26–28 series rows per filtered workbook) but DOCX `inline_shapes` = 0 for 20/24 pairs; only Technology=`All` Comprehensive copies embed 4 charts |
| Repro | Generate Comprehensive fixture pair with Technology=`All Contact Center`; open DOCX chart count vs Technology=`All` baseline |
| Recommended fix order | 1 |
| Notes | Round 168 CSOne chart path may still gate embedding on unfiltered activity-mix data; verify `decision_report_delivery` chart render branch for filtered scopes |

### R169-002 — CSOne export malformed headers (Customer A)

| Field | Value |
|-------|-------|
| Severity | medium |
| Category | source_input_quality |
| Classification | Source-data quality |
| Affected | Customer A CSOne upload (Aug-14 session export) |
| Evidence | Single-sheet export; pandas reads `Unnamed: 0` / `Unnamed: 1` header columns; 37 columns / 34 rows |
| Repro | Upload latest Customer A CSOne export; inspect column headers before `load_csone_excel` |
| Recommended fix order | 3 |
| Notes | Not an AdoptIQ 17-sheet workbook; impacts downstream column mapping if uploaded raw |

### R169-003 — CSOne export malformed headers (Customer B)

| Field | Value |
|-------|-------|
| Severity | medium |
| Category | source_input_quality |
| Classification | Source-data quality |
| Affected | Customer B CSOne upload (Aug-14 session export) |
| Evidence | Single-sheet export; `Unnamed` header columns; 36 columns / 27 rows |
| Repro | Same as R169-002 for Customer B export |
| Recommended fix order | 3 |

### R169-004 — Cross-folder row-count drift among equivalent scopes

| Field | Value |
|-------|-------|
| Severity | medium |
| Category | cross_scope_drift |
| Classification | Environment/live limitation |
| Affected | debug-b-comp-all vs debug-matrix2 reruns with matching Report_Info fingerprints |
| Evidence | 20 scope/sheet groups show differing Action_Plans / Adoption_Barriers / TAC row counts across folders despite identical scope metadata |
| Repro | Compare sheet row counts for PAIR-02 vs PAIR-19 (both ACC Comprehensive) |
| Recommended fix order | 5 |
| Notes | Treat as fixture rerun noise until live acceptance reproduces on single output root |

### R169-005 — No live AdoptIQ outputs for Customer A/B session

| Field | Value |
|-------|-------|
| Severity | info |
| Category | environment_limitation |
| Classification | Environment/live limitation |
| Affected | Customer A/B customer-scoped report audit (blocked) |
| Evidence | Zero shipped decision-report Word/Source Data pairs under Documents/App Support or analysis status for Aug-14 customer runs |
| Repro | Search output roots after customer-scoped analysis; none materialized |
| Recommended fix order | 99 |
| Notes | CSOne inputs exist; AdoptIQ report generation outputs do not — live customer audit deferred |

---

## Cross-report patterns

1. **Chart embedding gap:** Technology=`All` is the only scope that consistently embeds four Word charts; filtered technologies retain `Chart_Data` rows but omit visuals — likely the highest-impact product fix.
2. **Honest partial states:** `Report_Info` Source_State rows show `available`, `partial`, `unavailable`, and `zero` counts consistent with fixture mode (no false “all green” claims detected).
3. **Citation hygiene:** Zero mid-string `[Source: …]` injections across all 24 DOCX files (r114 audit pass).
4. **Build traceability:** 24/24 DOCX footers reference build **116** via the R68 build-label contract.
5. **Workbook contract:** 24/24 Source Data workbooks match the canonical 17-sheet inventory; zero Excel error cells detected.

## Confirmed-good behaviors

- All pairs pass `decision_report_delivery.validate_word_content` (content budget + no forbidden raw-appendix headings).
- All pairs pass `report_completeness_audit.audit_source_data_frames` without blocking errors.
- No HTML leakage, stub bullets, duplicate ID explosions, or placeholder identity failures flagged in automated scans.
- Round 146 offline acceptance metadata (`build116-prebuild-v2/round146_acceptance_summary.json`) reports **36/36 matrix + 13/13 gates green** with `production_accuracy_claimed=false` — consistent with this audit’s fixture-only scope.

## Limitations and blocked validation

| Item | Status |
|------|--------|
| Live Snowflake / CSConsole | Not executed |
| CSConsole URL HTTP probes | Skipped (`not_probed_fixture_mode`) |
| PDF page-count walk | `textutil` conversion did not yield page metadata in this environment |
| Customer A/B AdoptIQ pairs | **Missing** — audit blocked for live customer report quality |
| Production accuracy claim | **Explicitly not supported** by this audit |

## Recommended fix order (Codex)

1. R169-001 — Restore four embedded charts (or honest omission text) for technology-filtered decision reports when `Chart_Data` is populated.
2. R169-002 / R169-003 — Harden CSOne header-row detection for Customer A/B export shapes (or document required export steps).
3. R169-004 — Document or eliminate debug-folder rerun drift; require single output root for acceptance.
4. R169-005 — Run VPN-gated live customer-scoped regeneration; re-audit when outputs exist.

---

**Trailer:** Made-with: Cursor
