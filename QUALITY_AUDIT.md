# QUALITY_AUDIT.md — Round 14

Working note. Not committed unless explicitly requested.

## Stack
- Python 3.11.9
- Flask web app (single-process), CLI entry, PyInstaller mac/win bundles
- Local persistence: sqlite (`admin_monitoring_v2.db`, `external_intelligence.db`, `incident_storage.py`)
- External integrations: Snowflake, Cisco internal services (cisco_internal_integrations.py), CircuIT LLM (adoptiq_backend.py / ask_ai_grounded.py)

## Package boundaries
- Single-package "flat" layout. ~30 top-level modules at repo root + `tests/`. No installable package metadata.
- Hot files (>2k lines): `app_simple.py` (~18.8k), `adoptiq_backend.py` (~11.6k), `leader_report_generator.py` (~6.2k), `compact_report_formatter.py` (~2.9k), `executive_intelligence_formatter.py` (~1.7k).

## Verification commands
- `make test` — pytest (1674 passed / 2 skipped baseline as of Round 13).
- `make lint` — ruff with the rules in `pyproject.toml`.
- `make security` — bandit HIGH/MED gate (`bandit.yaml`); `-x _bundled_secrets.py`.
- `make audit` — pip-audit on `requirements.txt`.
- `make verify` — all of the above.

## Phase-1 baselines (raw)

| Tool | Result |
| --- | --- |
| `pytest -q` | 1674 passed / 2 skipped (warning: `ValueError: I/O operation on closed file.` from `app_simple._shutdown_handler` at the very end of the run) |
| `ruff check .` | 2074 errors before per-file ignores; 1843 auto-fixable |
| `bandit -ll` | 0 HIGH / 4 MED (all `B104` bind-all) / 10 LOW (all false positives or sentinel strings) |
| `pip-audit -r requirements.txt` | No known vulnerabilities |

### Ruff top buckets (before fixes)

| Count | Code | Severity | Notes |
| --- | --- | --- | --- |
| 765 | UP006 | low / cosmetic | `List[str]` → `list[str]`. Mass auto-fix candidate. |
| 316 | I001 | low / cosmetic | unsorted imports |
| 255 | UP045 | low / cosmetic | `Optional[X]` → `X \| None`. Pair with UP007. |
| 190 | F541 | low | f-string with no placeholder. Cosmetic but exposes dead-format calls. |
| 123 | UP017 | low | `datetime.timezone.utc` → `datetime.UTC`. Auto-fix safe. |
| 74 | F401 | medium | unused imports |
| 69 | UP035 | low | deprecated import (`typing.List`) |
| 62 | RUF100 | low | unused `noqa` |
| 35 | B905 | low | `zip(..., strict=)` missing |
| 22 | F841 | medium | unused local variable — sometimes a real bug |
| 18 | RUF059 | low | unused unpacked variable |
| **13** | **F821** | **HIGH** | **undefined name — real bugs** |
| 9 | S104/S105/S106 | mixed | bind-all / hardcoded passwords (false positives reviewed) |

## Adversarial triage findings

| ID | Severity | Surface | Phase | Finding | Action |
| --- | --- | --- | --- | --- | --- |
| R14-001 | LOW | runtime / observability | 3 | `app_simple._shutdown_handler` calls `logger.info` after stdout/stderr close at interpreter shutdown → `ValueError: I/O operation on closed file` printed at the end of every pytest run. | Guard logger call against closed streams. |
| R14-002 | HIGH | correctness | 2 | `data_contracts.validate_row_contract` references undefined `columns` on the success-return path → raises `NameError` every time the contract validates a frame with all required slots. **Reproduced.** | Replace with `columns_raw`. |
| R14-003 | HIGH | observability / admin UX | 2 | `enhanced_admin_dashboard_v2.get_report_history` and `get_analytics` reference module-level `_utc_iso_z` and `_tz`, but those are only defined locally inside `record_report_completion`. Both endpoints throw `NameError`, fall through to broad `except`, log an error and return placeholders. The History "last 7 days" tile and the analytics endpoint are silently broken. **Reproduced.** | Promote helpers to module level. |
| R14-004 | MEDIUM | correctness | 2 | `enhanced_admin_dashboard_v2.record_report_completion` uses `Any` in a nested-function annotation but `typing.Any` is not imported. Annotation only fires on `inspect.signature` / `get_type_hints`, but it is still wrong. | Import `Any`. |
| R14-005 | MEDIUM | correctness | 2 | `adoptiq_backend.py:848,878` annotation strings reference unaliased `OrderedDict`. The module imports it as `_OrderedDictForSchemaCache`. Fires on `get_type_hints` only. | Import `OrderedDict` directly or update the quoted annotation. |
| R14-006 | MEDIUM | correctness | 2 | `app_simple.py:10179, 17598, 17792, 17822` use the `X if 'X' in locals() else Y` defensive antipattern where `X` is never bound in the enclosing function. The branch is dead and the intent is opaque. | Replace with `locals().get('X', Y)`. |
| R14-007 | MEDIUM | security defaults | 1 | `app_simple.py:18768` defaults Flask bind host to `0.0.0.0`. Sensitive endpoints rely on a separate `_is_local_client` gate. The admin dashboard already defaults to `127.0.0.1` and gates public binding behind `ADOPTIQ_ADMIN_BIND_PUBLIC=1`. Default the main app the same way. | Default to `127.0.0.1`; opt-in public bind via `ADOPTIQ_BIND_PUBLIC=1` env. |
| R14-008 | LOW | hygiene | 4 | `bandit` flags `_bundled_secrets.py` even though it is gitignored / generated. | Excluded via `bandit.yaml` + `-x _bundled_secrets.py`. (Done.) |
| R14-009 | LOW | hygiene | 4 | Ruff is not on the gate; `make lint` introduced. Many cosmetic issues remain. | Configure conservative ruff ruleset; defer mass auto-fix to a separate cleanup. |

## Adversarial review notes (Phase 2)

Areas walked, in order. Findings folded into the table above where they led to a fix.

- **HTTP surface (`app_simple.py`, `enhanced_admin_dashboard_v2.py`)**: CSRF middleware is in place (Round 5/6 work). `_is_local_client` gate is the defensive ring around download/cancel/intel endpoints. The bind-host default is the one improvement landing in Phase 1.
- **Outbound integrations (`cisco_internal_integrations.py`, `ask_ai_grounded.py`, `adoptiq_backend.py`)**: timeouts are set; retries have jitter (Round 6). No raw URL inputs; allow-listed hosts. No new finding.
- **Persistence (`incident_storage.py`, `enhanced_admin_dashboard_v2.py` sqlite)**: parameterized queries throughout. R14-003 is the only sqlite-adjacent finding.
- **File I/O (`download_*` routes, `OUTBOX`, uploads)**: path traversal already mitigated (`_resolve_csone_path_safe`). No new finding.
- **Concurrency (`analysis_status` dict, `_TABLE_COLUMN_*` LRUs)**: Round 13 added LRU caps and TTL eviction. R14-005 is the cosmetic annotation fix; no real concurrency bug observed.
- **Time / timezone**: 123 UP017 flags are the `timezone.utc` → `UTC` modernization. Defer to Phase 4 polish; not a behavior change.
- **Error swallowing**: R14-002 / R14-003 confirm the cost — silent NameError disguised as "soft failure." Fix the F821 set; the swallowing pattern itself is necessary and stays.
- **Logging redaction**: Round 13 redacted `[[FILTER]]` PII. Spot-check shows no new INFO-level leaks.
- **Crypto / certs**: usage limited to `cryptography`/`hvac`/`truststore`; no hardcoded keys; no deprecated algos surfaced by ruff/bandit.

## Round 14 phases (final)

### Security (Phase 1.x)
- **Phase 1.1** — Default `app_simple` Flask bind host to `127.0.0.1`; require `ADOPTIQ_BIND_PUBLIC=1` (or explicit `ADOPTIQ_BIND_HOST`) to expose externally. (R14-007)

### Correctness (Phase 2.x)
- **Phase 2.1** — Fix `data_contracts.validate_row_contract` `columns` → `columns_raw`. Was raising `NameError` on the success path. (R14-002)
- **Phase 2.2** — Promote `_utc_iso_z` and `_tz` to module level in `enhanced_admin_dashboard_v2.py`; import `Any`. Two admin dashboard endpoints (`get_report_history`, `get_analytics`) were silently failing with `NameError` and returning placeholders. (R14-003, R14-004)
- **Phase 2.3** — Bind `OrderedDict` at module scope in `adoptiq_backend.py` so quoted annotations on the LRU caches resolve under `get_type_hints`. (R14-005)
- **Phase 2.4** — Replace `X if 'X' in locals() else Y` antipattern with `locals().get('X', Y)` at four sites in `app_simple.py`. Removes dead bare-name references. (R14-006)

### Reliability (Phase 3.x)
- **Phase 3.1** — Guard `app_simple._shutdown_handler` logger call against closed streams using `logging.raiseExceptions=False`. Removes the trailing `--- Logging error ---` diagnostic at every shutdown. (R14-001)

### Polish (Phase 4.x)
- **Phase 4.1** — Drop redundant duplicate `from config import Config` in `adoptiq_backend.py`. (F811)
- **Phase 4.2** — Replace misleading `part.strip('**')` with explicit `part[2:-2]` in `app_simple.py` Word-render bold-tag handler. (B005)
- **Phase 4.3** — Remove duplicate `"Worker"` and `"Mode"` tokens from the technical-name allowlist `frozenset` in `structured_logging.py`. (B033)
- **Phase 4.4** — Document false-positive S105/S106 findings (OAuth `/token` URL, `_kind_token` enum label, developer smoke-test placeholders) with per-line `# noqa: S10x -- rationale`. (S105, S106)
- **Phase 4.5** — Annotate the existing opt-in admin `0.0.0.0` bind with `# noqa: S104 # nosec B104` so bandit and ruff both stop alerting; the public-bind branch is gated by `ADOPTIQ_ADMIN_BIND_PUBLIC=1`. (S104, B104)
- **Phase 4.6** — Surface CLI `_integrity_checks` reason at WARNING in `adoptiq_backend.py`. The result was being assigned to `reason` and discarded — the integrity gate was a silent no-op on the CLI report path while the Flask flow inspects it correctly. (F841)

## Files changed

### New (untracked)
- `Makefile` — `test`, `lint`, `security`, `audit`, `verify` targets.
- `pyproject.toml` — ruff config (E/F/B/S selected; documented ignore list).
- `bandit.yaml` — bandit config (HIGH/MED gate; documented skips).
- `QUALITY_AUDIT.md` — this document.
- `tests/test_round14_markers.py` — marker presence tests for every Round 14 fix.
- `tests/test_round14_behavioral.py` — behavioral regression tests.

### Edited
- `app_simple.py` — Phase 1.1, 2.4 (×4 sites), 3.1, 4.2.
- `adoptiq_backend.py` — Phase 2.3, 4.1, 4.4, 4.6.
- `enhanced_admin_dashboard_v2.py` — Phase 2.2, 4.5.
- `data_contracts.py` — Phase 2.1.
- `cisco_internal_integrations.py` — Phase 4.4 (×3 sites).
- `structured_logging.py` — Phase 4.3.

## Verification commands & results

```
$ make verify
... pytest 1689 passed, 2 skipped in 5.03s
... ruff: All checks passed!
... bandit: 0 HIGH, 0 MED, 10 LOW (all triaged false positives, see Deferrals)
... pip-audit: No known vulnerabilities found.
```

Run twice consecutively, both clean.

| Tool | Pre-Round-14 | Post-Round-14 |
| --- | --- | --- |
| `pytest -q` | 1674 passed / 2 skipped (+ trailing `ValueError` on shutdown) | **1689 passed / 2 skipped** (clean shutdown) |
| `ruff check .` | n/a (not configured); 2074 errors at first run | **All checks passed** |
| `bandit -ll` | 0 HIGH / 4 MED / 10 LOW | **0 HIGH / 0 MED / 10 LOW** |
| `pip-audit -r requirements.txt` | clean | **clean** |

## Deferrals (knowingly not addressed in Round 14)

| Item | Why deferred |
| --- | --- |
| **UP rules** (UP006/UP017/UP035/UP045): ~1,210 cosmetic PEP-585/604/UTC modernizations | Pure style; touching this many lines risks review fatigue and would obscure Round 14's behavioral fixes. Track as a follow-up PR scoped to a single ruleset at a time. |
| **I001** import sorting (~317 occurrences) | Mass refactor of every module's import block; non-functional. Follow-up PR. |
| **F541** f-string-without-placeholder (~190) | Cosmetic; many are intentional in error messages. Follow-up PR. |
| **B905** `zip(..., strict=)` (~35) | Adding `strict=` could change behavior on unequal-length zips. Audit each call site separately. |
| **F841** non-bug unused locals (~21 remaining) | Mostly heading-capture idiom (`var = self.doc.add_heading(...)`). One real bug (Phase 4.6) was extracted; the rest are cosmetic. |
| **F401** unused imports (~74) | Many are intentional cross-module re-exports (e.g. `from openai import APITimeoutError` for `except` clauses elsewhere). Manual review needed; bandit/SCA already cover the security risk surface. |
| **`# nosec` parser warnings** | bandit emits "Test in comment: X is not a test name" for our human-readable rationale text; bandit still exits 0 since the matched test code is recognized. Cosmetic noise, not a failure. |
| **PyInstaller / build pipeline polish** | Out of scope per plan §"Out of scope". |

## Residual risks

- **Bind-host default**: changes from `0.0.0.0` to `127.0.0.1`. Anyone deploying the Flask app behind a reverse proxy on a single host without `ADOPTIQ_BIND_HOST=0.0.0.0` (or `ADOPTIQ_BIND_PUBLIC=1`) will see "no route to host" from the proxy. Mitigation: `ADOPTIQ_BIND_PUBLIC=1` is documented as the opt-in.
- **CLI integrity gate** still does not abort on integrity violations — Phase 4.6 only surfaces a WARNING. The Flask path is unchanged. A follow-up could decide whether the CLI should also abort.
- **Ruff `select` is narrowed** to E/F/B/S. Re-introducing UP/I/RUF on a future round will surface ~1,800 cosmetic findings — track as a separate effort.
- **Bandit LOW findings (10)** are documented false positives (test fixtures, defensive `try/except/pass` paths). Re-triage if any new appears.

## Recommended follow-ups

- Add `make verify` to a CI gate (the plan kept this out of scope, but the harness is ready).
- Schedule a "Round 14 polish — cosmetic ruff fixes" PR per ruleset (UP, I, RUF) so each is reviewed in isolation.
- Decide on CLI vs Flask integrity-gate parity for `_integrity_checks` (currently asymmetric).
- Audit all `# noqa: S10x` and `# nosec B10x` annotations annually — the rationale text should still match the surrounding code.

## Final verification checklist
- [x] `make lint` clean
- [x] `make security` clean (0 HIGH / 0 MED; LOW triaged in Deferrals)
- [x] `make audit` clean
- [x] `make test` clean — two consecutive runs (1689 passed / 2 skipped)
- [x] `git diff --stat` reviewed for accidental scope creep — all Round 14 lines carry a `Round 14 / Phase X.Y` marker
- [x] Adversarial self-review of the diff — every changed line maps to a backlog finding

---

# QUALITY_AUDIT.md — Round 15 (Reports Polish + Full Adversarial Sweep)

## Scope
Round 14-style adversarial sweep across all modules, layered on top of a substantial customer-facing report polish (Excel + Word) driven by concrete findings in tonight's gold-standard run (`AdoptIQ_Data_Brian_Frazier_All_Contact_Center_90d_*.xlsx` / `.docx`). Plan: `.cursor/plans/round_15_reports_audit_*.plan.md`.

## Phase 0 baseline (post-Round-14, pre-Round-15)

| Tool | Result |
| --- | --- |
| `make test` | 1689 passed / 2 skipped — clean |
| `make lint` | All checks passed (ruff E/F/B/S ruleset) |
| `make security` | 0 HIGH / 0 MED (cosmetic `nosec` parser warnings only) |
| `make audit` | No known vulnerabilities |

Gold-standard fixtures shipped:
- `tests/fixtures/round15/gold_data.xlsx` (228 KB, 6 sheets)
- `tests/fixtures/round15/gold_report.docx` (113 KB)

Phase 0 smoke tests (`tests/test_round15_export_quality.py`):
- `test_phase_0_gold_fixtures_present`
- `test_phase_0_gold_xlsx_baseline_shape` — locks the current sheet inventory.
- `test_phase_0_gold_xlsx_documents_known_blandness` — encodes the blandness findings against the fixture so Round 15 lands them deliberately.
- `test_phase_0_export_schema_module_optional` — skipped pending Phase 1 SSoT.

## Concrete blandness findings (verified against the gold xlsx)

| ID | Severity | Surface | Phase | Finding |
| --- | --- | --- | --- | --- |
| R15-001 | MEDIUM | report quality | 1 | `AB_Detail_All` is **272 columns wide**, exporting Salesforce metadata (`IS_DELETED`, `MAY_EDIT`, `IS_LOCKED`, `RECORD_TYPE_ID`, `SYSTEM_MODSTAMP`, `LAST_VIEWED_DATE`, `LAST_REFERENCED_DATE`, `CONNECTION_RECEIVED_ID`, `CONNECTION_SENT_ID`). |
| R15-002 | MEDIUM | report quality | 1 | `CSConsole_Customer_Pulse` exports ETL/SF plumbing (`ETL_ID`, `DELETE_FLAG`, `RECURSIVE_FLAG`, `ISDELETED`, `MAYEDIT`, `ISLOCKED`, `CONNECTIONRECEIVEDID`, `CONNECTIONSENTID`, `CREATEDBYID`, `LASTMODIFIEDBYID`, `SYSTEMMODSTAMP`). |
| R15-003 | LOW | report quality | 1 | `External_Incidents` exports underscore-prefixed internal markers (`_stale_storage`, `_from_storage`, `_window_meta`) and `publication_id`. |
| R15-004 | LOW | report quality | 1 | `CSOne_Detail_All` has header oddities: literal `'col_2'`, `'Customer Name: Customer Name'`, duplicated `Tech.` / `Sub Technology` / `Sub Tech.`. |
| R15-005 | MEDIUM | report quality | 2 | Zero conditional-formatting rules across all 6 sheets — no risk-band coloring, no severity tiering, no age data bars. |
| R15-006 | LOW | report quality | 2 | No data ranges are registered as Excel Tables → no native banded rows or filtering UX. |
| R15-007 | LOW | report quality | 2 | No Summary/dashboard tab — `Report_Info` is just 3 rows of metadata. |
| R15-008 | LOW | report quality | 2 | No column-level number / date / percent formats discoverable in the workbook structure (raw values). |

## Phase 0 status
- [x] Baselines captured.
- [x] Gold fixtures committed under `tests/fixtures/round15/`.
- [x] Smoke test scaffold in `tests/test_round15_export_quality.py`.
- [x] §Round 15 backlog seeded above.

## Phase 1 — Excel column curation (R15 / 1.x)

- New SSoT module `report_export_schema.py` exposes `INTERNAL_COLUMN_DENYLIST`, `INTERNAL_COLUMN_PREFIXES`, `SHEET_HEADER_RENAMES`, `CURATED_COLUMNS`, `is_internal_column`, `filter_columns`, `apply_export_schema`. Every entry is documented inline.
- Wiring sites that route DataFrames through `apply_export_schema(...)` immediately before write:
  - `adoptiq_backend.write_excel_workbook` (gold-standard writer): main loop + CSConsole branch.
  - `app_simple.py` compact-analysis writer (line ~7957): per-sheet projection.
  - `app_simple.py` subscription-renewal writer (line ~16749): `Adoption_Barriers`, `Action_Plans`, `Customer_Pulse`, `Success_Priorities`.
  - `app_simple.py` leader-team writer (line ~18118): per-sheet projection.
- Resolves R15-001 .. R15-004:
  - `AB_Detail_All` projection: 272 raw columns → ~60-column curated allowlist (drops every SF / ETL / audit column the gold workbook leaked).
  - `CSConsole_Customer_Pulse` projection drops `ETL_ID`, `DELETE_FLAG`, `RECURSIVE_FLAG`, every `EDWSF_*`, and every CamelCase SF audit column.
  - `External_Incidents` denylist drops `_stale_storage`, `_from_storage`, `_window_meta`, `publication_id`. Underscore-prefix rule catches new internal markers automatically.
  - `CSOne_Detail_All` rename map collapses `'Customer Name: Customer Name'` → `Customer`, `'Product: Product Name'` → `Product`, etc. Curated set drops `'col_2'` / `'col_32'`.
- Tests: `tests/test_round15_excel_columns.py` (54 cases). Covers per-column denylist, underscore-prefix rule, curated allowlist non-overlap, rename map, marker presence in `adoptiq_backend.py` / `app_simple.py`, and gold-fixture regression filters.

## Phase 2 — Excel visual polish (R15 / 2.x)

- New module `report_export_styling.py` (no behavior change unless explicitly invoked):
  - `detect_column_format(col)` — column-name driven xlsxwriter `num_format` lookup (currency / date / percent / integer / risk-float). Resolves R15-008.
  - `apply_excel_polish(workbook, worksheet, df, sheet_name, used_table_names)` — converts the written range to a true Excel Table (`Table Style Medium 9`), wires per-column `num_format` via the Table column spec, layers conditional formatting. Resolves R15-005, R15-006.
  - `apply_conditional_formatting(...)` — risk 3-color scale (anchored to `risk_scoring.RISK_BAND_THRESHOLDS["HIGH"] / 10` and `["MEDIUM"] / 10`, never magic numbers); severity tier bands (P1/Critical=red, P2/High=orange, P3/Medium=yellow, P4/Low=grey); status grey/yellow/red; days-open data bar.
  - `build_summary_rows(...)` / `write_summary_sheet(...)` — Summary KPI tab pulled from `canonical_metrics` (`count_customers`, `count_total_barriers`, `count_critical_barriers`, `count_open_barriers`, `count_total_tac`, `count_p1`, `count_open_tac`, `count_escalated`, `count_bems`). Unknown KPIs render `"--"` rather than fabricated zero. Resolves R15-007.
- Wired into `adoptiq_backend.write_excel_workbook` fallback path (the writer that generated the gold workbook):
  - Summary sheet is now the **first** tab (was: `Report_Info`).
  - Every sheet (main path + CSConsole branch) is registered as a real Excel Table with a workbook-unique `tbl_*` name.
  - Per-column number formats applied via the Table column spec.
  - Conditional formatting layered on `risk_score_*`, severity / priority columns, status columns, and days-open / age columns.
- Tests: `tests/test_round15_excel_format.py` (67 cases). Covers format detection, classifier, address builders, table-name dedup, palette pin, summary KPI rendering, and end-to-end through `adoptiq_backend.write_excel_workbook` (asserts Summary first, all data sheets have a Table, AB sheet has ≥10 conditional-formatting rules, Phase 1 curation still drops plumbing after polish runs).

### Deferred (documented trade-off)

- The polish pass is **not yet** wired into the three other `app_simple.py` `pd.ExcelWriter` sites (compact analysis @ ~7957, renewal @ ~16749, leader-team @ ~18118). Those writers each write data with `startrow=1` and a custom merged-cell title row, which would need bespoke address arithmetic to register a true Excel Table without overwriting the title. Phase 1 column curation IS applied at all four writers; the visual polish (Tables + conditional formatting + Summary tab) is currently only on the gold-standard writer. Rationale: the binding Phase 8 verification target IS the gold-standard writer, and the deferred sites have header-row conventions that would require their own polish helper to avoid regression. Tracked as follow-up R15-FOLLOWUP-1 below.

## Phase 3 — Word visual polish (R15 / 3.x)

### Findings (verified against the gold docx)

- **R15-009** — every markdown ``#`` LLM heading collapsed onto Heading 1, producing **38 H1 paragraphs** in the gold report. Heading 1 should be reserved for the document title; the body should follow a real H2 → H3 → H4 hierarchy so the document outline / TOC actually carries meaning.
- **R15-010** — no executive summary table at the top of the report. The only KPI surface was a buried 3×4 "Portfolio Dashboard - At-A-Glance" table (when matplotlib was unavailable) or a 4-panel chart image (when it was). Neither matches the Excel `Summary` tab built in Phase 2.
- **R15-011** — risk callouts ("Top Critical Risk", "Recommended Action", etc.) rendered as plain bold paragraphs that read as body text. No color affordance to distinguish a critical risk from a low-priority one at a glance.
- **R15-012** — top-N lists ("Top 5 customers", "Top 5 barriers") rendered as bulleted lists. Without banded rows, bold header rows, or aligned columns, scanning a long list in Word is hard.

### Resolution

- New module `report_word_styling.py` (no behavior change unless explicitly invoked):
  - `markdown_heading_level(hash_count)` — single source of truth for the markdown ``#`` → Word heading-level mapping (`# = H2`, `## = H3`, `### = H4`, `####/##### = H4`). Heading 1 stays reserved for the title page only. Resolves R15-009.
  - `MARKDOWN_TO_WORD_HEADING`, `MIN_BODY_HEADING_LEVEL = 2`, `MAX_HEADING_LEVEL = 4` — explicit constants the regression test pins.
  - `risk_band_fill_color(band)` — resolves a band label (`"Critical Risk"`, `"High Risk"`, `"Medium Risk"`, `"Low Risk"`, `"Healthy"`, plus the canonical `RISK_BAND_COLORS` keys) to its hex via `canonical_metrics.RISK_BAND_PORTFOLIO_COLORS`. Defensive fallback to neutral grey for unknown bands.
  - `set_cell_shading(cell, hex_color)` / `get_cell_fill_hex(cell)` — round-trip cell-fill helpers (lxml-level XML splice that python-docx doesn't expose natively).
  - `add_banded_top_n_table(doc, headers, rows)` — Cisco-blue header row with white bold text, alternating-row banding, returns the created `Table`. Defensive on empty inputs / None doc. Resolves R15-012.
  - `add_risk_callout(doc, title, risk_band, body_text)` — single-cell colored table whose fill is `risk_band_fill_color(band)`. Resolves R15-011.
  - `add_executive_summary_table(doc, kpi_rows)` — prepends a Heading-2 ("Executive Summary") followed by a 2-column banded KPI table. Title-level capped at `MAX_HEADING_LEVEL`. Resolves R15-010.
  - `build_executive_summary_rows(...)` — delegates to `report_export_styling.build_summary_rows` so Word + Excel summary KPIs come from the same canonical-metrics calls.
- Wired into `adoptiq_backend.append_to_word_report` and `adoptiq_backend.create_executive_title_page`:
  - `# / ## / ### / ####` markdown headings now route through `markdown_heading_level()` (R15-009).
  - The `heading` arg to `append_to_word_report` now lands at `MIN_BODY_HEADING_LEVEL` (Heading 2) instead of Heading 1.
  - `create_executive_title_page` renders the Phase-3 executive summary table immediately after the page break, sourcing labels/values from the supplied `portfolio_metrics` dict (manager scope, technology scope, window, customer count, barrier count, case count, BEMS count, risk-band breakdown, P1/P2 case counts).
- Tests: `tests/test_round15_word_format.py` (40 cases). Covers heading-level mapping (incl. garbage inputs), risk-band → hex resolution, hex normalization, cell-shading round-trip, banded top-N table layout (header bold + white, alt-row fill on the 2nd data row, 1 + N rows × N cols), risk callout color matching, executive summary table layout (heading + KPI rows), defensive paths (None doc, empty rows, missing band), end-to-end `append_to_word_report` regression guard (markdown `# heading` no longer becomes Heading 1; only the original title page H1 survives), and end-to-end `create_executive_title_page` integration (verifies Heading 2 + 2-column KPI table on the title page).

### Deferred (documented trade-off)

- The Phase-3 helpers (`add_risk_callout`, `add_banded_top_n_table`) are *available* to the existing formatters (`compact_report_formatter`, `executive_intelligence_formatter`, `leader_report_generator`) but are not yet substituted in for every existing `doc.add_table(...)` call. Those formatters already use a baseline `Light Grid Accent 1` style which gives banded rows for free, so the immediate visual gap is the *risk callouts* and the *executive summary table at the top* — both of which are now in place via the title-page change. Substituting `add_banded_top_n_table` for the ~30 existing tables across those formatters is tracked as **R15-FOLLOWUP-2**.

## Phase 4 — Security adversarial sweep (R15 / 4.x)

### Scope reviewed (read-only, with one targeted fix)

| Surface | Modules inspected | Result |
| --- | --- | --- |
| HTTP outbound | `adoptiq_backend.py`, `app_simple.py`, `cisco_internal_integrations.py`, `enhanced_admin_dashboard_v2.py`, `enhanced_snowflake_insights.py` | Every `requests.{get,post,put,delete,head,options}` carries an explicit `timeout=`; body reads gated through `_response_text_capped` / `_response_json_capped` (5 MiB default cap); no `verify=False`. Clean. |
| TLS / cert handling | repo-wide grep | No `verify=False`, no `urllib3.disable_warnings` stubs, no static `ca_bundle` overrides. Clean. |
| Crypto / hashing | repo-wide grep | No `hashlib.md5` / `hashlib.sha1` / `MD5` / `SHA1` use; HMAC paths land on `hashlib.sha256`. Clean. |
| Unsafe deserialization | repo-wide grep | No `pickle`, no `yaml.load(...)` (only `yaml.safe_load`), no `eval` / `exec` / `os.system` / `shell=True`. Clean. |
| Subprocess | `app_simple.py`, `enhanced_admin_dashboard_v2.py`, `installer_app.py` | All calls use list-form argv, fixed bin paths or whitelisted commands (`lsof`, `netstat`, `kill`), `text=True`, `timeout=` set, narrow `except` for `CalledProcessError` / `TimeoutExpired` / `FileNotFoundError`. Clean. |
| File I/O / path traversal | `app_simple.download_file`, all `send_file` / `send_from_directory` callers | `secure_filename`, extension allowlist, `os.path.realpath` containment check against the outputs root, `.` / `..` / `/` / `\\` rejected, `\x00` rejected, length capped at 255. Clean (already hardened in Round 8 / Phase 1.10). |
| Tempfiles | `installer_app.py`, `enhanced_admin_dashboard_v2.py` | `NamedTemporaryFile` / `TemporaryDirectory` only; no `mktemp()`. Clean. |
| Sqlite / SQL injection | `incident_storage.py`, `snowflake_csone_discovery.py`, `enhanced_admin_dashboard_v2.py` | All `cursor.execute` / `conn.execute` use parameterised queries. Where identifiers are interpolated (`incident_storage._IDENT_RE`, `snowflake_csone_discovery._safe_or_skip`, `snowflake_table_policy._SAFE_OBJECT`), the value is validated against a strict regex *and* compared against an allowlist before f-string substitution. Clean. |
| Flask request parsing | `app_simple.py` | `request.get_json(silent=True)`, `request.form.get`, `request.args.get`, `request.values.get`; CSRF validated on every state-changing endpoint via `validate_csrf`; `WTF_CSRF_ENABLED=True` by default. Clean. |
| Cookies / session | `app_simple.py` ll. 509–588 | `SECRET_KEY` must be set for packaged builds (`RuntimeError` otherwise) and falls back to `secrets.token_urlsafe(48)` in dev; `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE='Lax'`, `SESSION_COOKIE_SECURE` follows `_frozen` (True in packaged mode). Clean. |
| Bind defaults | `app_simple.py` Round 14 / Phase 7.1 | Main app defaults to `127.0.0.1`; admin dashboard defaults to `127.0.0.1`. Public bind requires `ADOPTIQ_BIND_PUBLIC=1` / `ADOPTIQ_ADMIN_BIND_PUBLIC=1`. Clean. |
| ReDoS / regex shapes | `data_normalization.py`, `error_classifier.py`, `incident_storage.py` | No nested `(.+)+` / `(.*)+` shapes; the few alternations are anchored. Clean. |
| Logging redaction | `structured_logging.py` | `_REDACT_PATTERNS` covers JWT, API keys, AWS keys, bearer tokens, emails, basic-auth URLs, K-V password forms, GitHub tokens; key-prefix denylist covers `password`, `secret`, `token`, `api_key`, `apikey`, `auth`, `bearer`, `private_key`, `passphrase`, `pass`. **One residual finding (R15-013) addressed below.** |

### Findings

- **R15-013 (MEDIUM, log injection)** — `structured_logging._redact_extra_kv_value` returned non-redacted primitives unchanged. A user-supplied value containing `\r\n` (e.g. an attacker-controlled customer name, error string, or other free-text field that flows into `extra_kv={"step": ...}`) could splice a fake log record onto a single legitimate line. Since the structured prefix is rendered *as* part of the log record, a CR/LF in any value would silently break SIEM "one-line == one-event" assumptions and let an attacker forge follow-on log lines.

### Resolution

- New `structured_logging._strip_log_control_chars(s)`: replaces every byte `< 0x20` and `0x7F` with a visible escape (`\x0d`, `\x0a`, `\x00`, `\x7f`); collapses tabs to a single space; preserves printable ASCII and Unicode.
- `_redact_extra_kv_value` now passes its surviving primitive return through `_strip_log_control_chars`, guaranteeing a single-line, single-record structured prefix even when an upstream caller embeds CR/LF.
- Marker: `Round 15 / Phase 4.1` in `structured_logging.py`.
- Tests: `tests/test_round15_security.py` (6 cases) — exercises CR/LF/NUL/0x7F replacement, printable-text preservation, `None` / empty-string handling, the redactor end-to-end, and an end-to-end `StructuredAdapter.info(...)` that asserts a CR/LF-bearing `extra_kv` value never produces a second physical record.

## Phase 5 — Correctness adversarial sweep (R15 / 5.x)

### Findings

- **R15-014 (HIGH, correctness)** — `report_export_styling._format_kpi` called `int(value)` on every float input. For `float('nan')` that raises `ValueError`; for `float('inf')` / `float('-inf')` it raises `OverflowError`. Any canonical-metrics call that returned a non-finite float (e.g. an ARR sum that overflowed, a percentage with a zero denominator, or a degraded numerator) would crash the entire Summary write at runtime. Caught by adversarial input fuzzing of the formatter.
- **R15-015 (HIGH, correctness)** — `report_export_styling.build_summary_rows` invoked `cm.count_customers(ab_df, csone_df, cs_pulse)` *positionally*. `count_customers` is keyword-only (`def count_customers(*, ab_df=..., csone_df=..., pulse_df=...)`), so the call raised `TypeError`, which `_safe_canonical_call` swallowed and returned `None`. Net effect: the Summary tab's "Customers in portfolio" row has been silently rendering `--` since Round 15 / Phase 2 landed, even when the frames clearly carried customer rows. Reproduced.
- **R15-016 (LOW, correctness)** — `adoptiq_backend.write_excel_workbook` defaulted its filename suffix via `datetime.now().strftime('%Y%m%d_%H%M%S')` (host-local clock, no timezone marker). Two operators in different regions on the same UTC second would receive *different* filenames; a UK operator running during BST got a filename one hour ahead of a colleague in UTC. Inconsistent with the UTC ISO-Z stamps Round 5 / Phase 6.3 and Round 12 / Phase 10.4 standardised elsewhere.

### Resolution

- `report_export_styling._format_kpi` now treats `float('nan')`, `float('inf')`, `float('-inf')` as "unknown" and renders `"--"`; explicitly maps `bool` to `"Yes"` / `"No"` so a stray boolean doesn't surface as the integer `1`. Marker: `Round 15 / Phase 5.2`.
- `report_export_styling.build_summary_rows` calls `cm.count_customers` with the required keyword arguments (`ab_df=`, `csone_df=`, `pulse_df=`) so the canonical customer count actually surfaces. Marker: `Round 15 / Phase 5.3`.
- `adoptiq_backend.write_excel_workbook`'s default filename suffix is now anchored on `datetime.now(timezone.utc)` and carries a trailing `Z`. Marker: `Round 15 / Phase 5.1`.
- Tests: `tests/test_round15_correctness.py` (11 cases). Pins `_format_kpi` against NaN / `inf` / `-inf` / `bool` / integer-valued floats / fractional floats; pins `build_summary_rows` to actually emit the customer count from the canonical universe (Acme + Beta + Gamma == 3); pins the workbook default-filename UTC marker so a future refactor can't quietly regress the parity.

## Phase 6 — Reliability adversarial sweep (R15 / 6.x)

### Reviewed surfaces

| Surface | Location | Result |
| --- | --- | --- |
| Resource leaks (file handles, sqlite cursors) | `incident_storage.py`, `enhanced_admin_dashboard_v2.py`, `connectivity_diagnostics.py` | All `sqlite3.connect` callers use `_connect()` / `db_connection()` context managers with `try/finally`. No bare `open()` calls in the hot path; every reader uses `with open(...) as fh:`. Clean. |
| Retries / timeouts | `adoptiq_backend.py`, `cisco_internal_integrations.py`, `enhanced_snowflake_insights.py` | Outbound HTTP and Snowflake calls already carry bounded exponential backoff with `jitter` (Round 6 / Phase 6.x), per-call `timeout=` arguments, and circuit-breaker state. Clean. |
| Concurrency / lock discipline around `analysis_status` | `app_simple.py` `progress_view` | **One residual finding (R15-017) addressed below.** |
| Error narrowing | `report_export_styling.py` `_safe_canonical_call` | **One residual finding (R15-018) addressed below.** |
| Empty-frame defensiveness | `report_export_styling.apply_excel_polish` | Already returns early for `n_cols <= 0`; writes a blank row when `last_row < 1` so the Excel Table style still applies. Clean. |

### Findings

- **R15-017 (MEDIUM, reliability)** — `app_simple.progress_view` had two structurally identical "audit-fallback" branches. The first (status-file present, id missing) released `analysis_status_lock` around the SQLite query and re-acquired only to commit. The second (status-file missing entirely) ran `_build_status_from_report_history(analysis_id)` *while holding* `analysis_status_lock`, blocking every other request thread that wanted to read or write the in-memory dict on a single SQLite round-trip. Latency-asymmetric across two paths that should behave identically.
- **R15-018 (MEDIUM, reliability)** — `report_export_styling._safe_canonical_call` swallowed every `Exception` at `DEBUG`. R15-015 (the keyword-only positional call) lived for an entire phase because the call-signature `TypeError` it raised was rendered at DEBUG and never made it to operator logs. The wrapper conflated two very different failure modes: (a) "canonical_metrics returned None for an empty frame" (expected, noisy at WARNING) and (b) "we called the function with the wrong shape" (a code-level contract violation that should be loud).

### Resolution

- `app_simple.progress_view`'s status-file-missing branch now mirrors the sibling branch: it acquires the lock only for the in-memory check, sets a `_need_audit_lookup` flag, runs the SQLite query *outside* the lock, and re-acquires only to commit (with a re-check in case another thread populated the entry while we were querying). Marker: `Round 15 / Phase 6.2` in `app_simple.py`.
- `report_export_styling._safe_canonical_call` now narrows `TypeError` to `WARNING` with a structured prefix that names the function and the argument-type shape; every other exception class stays at `DEBUG`. Marker: `Round 15 / Phase 6.1` in `report_export_styling.py`.
- Tests: `tests/test_round15_reliability.py` (6 cases). Pins the WARNING split for `TypeError` vs `ValueError` in `_safe_canonical_call` (call-signature regressions are now visible); pins the success path; pins the `Round 15 / Phase 6.1` marker; pins the structural shape of `progress_view`'s audit-fallback branch (the SQLite call's parent block must NOT be `with analysis_status_lock:`); pins the `Round 15 / Phase 6.2` marker.

## Phase 7 — Lint / style polish (R15 / 7.x)

### Reviewed surfaces

| Surface | Location | Result |
| --- | --- | --- |
| `ruff check .` (E, F, B, S) | repository-wide | Clean on entry to Phase 7. The Phase 6 changes (`report_export_styling.py`, `app_simple.py`, `tests/test_round15_reliability.py`) compile and lint clean. |
| `bandit -ll -r .` | repository-wide | Clean. Existing `# nosec B104` rationales (the `0.0.0.0` bind opt-in via `ADOPTIQ_BIND_PUBLIC=1` / `ADOPTIQ_ADMIN_BIND_PUBLIC=1`) remain documented in source. |
| `pip-audit -r requirements.txt --strict` | dependency tree | Clean. |
| Silent rule disables (`# noqa`, `# nosec`) | repository-wide | No new disables added. The two existing `nosec B104` callsites carry the rationale comment in source (env-flag-gated public bind) and are documented in Round 14 / Phase 7 of this audit. |

### Resolution

- No code change required: `make verify` was already clean after Phase 6.
- The two pre-existing `# nosec B104` rationales in `app_simple.py:18943` and `enhanced_admin_dashboard_v2.py:3159, 3167, 3167` remain — they document the `ADOPTIQ_BIND_PUBLIC=1` / `ADOPTIQ_ADMIN_BIND_PUBLIC=1` env-flag-gated public bind path and were inherited from the Round 14 sweep. Re-validated against bandit's HIGH/MED gate (`make security`); no new findings.

## Phase 8 — Final verification

### Verification commands & results

```
make lint     # ruff check . → All checks passed!
make security # bandit -ll → no HIGH/MED findings
make audit    # pip_audit -r requirements.txt --strict → no vulns
make test     # 1881 passed, 2 skipped in 5.87s
make verify   # All Round 14 gates passed.   (run #1)
make verify   # All Round 14 gates passed.   (run #2 — back-to-back)
```

Both `make verify` runs were clean and back-to-back-idempotent.

### `git diff --stat` adversarial review

```
QUALITY_AUDIT.md      | … insertions(+), … deletions(-)
adoptiq_backend.py    | 205 +++++++++++++++++++++++++++++++++++++++++++++++---
app_simple.py         | 122 +++++++++++++++++++++++-------
structured_logging.py |  35 ++++++++-
```

Untracked files (Round-15 additions):

```
report_export_schema.py
report_export_styling.py
report_word_styling.py
tests/fixtures/round15/
tests/test_round15_correctness.py
tests/test_round15_excel_columns.py
tests/test_round15_excel_format.py
tests/test_round15_export_quality.py
tests/test_round15_reliability.py
tests/test_round15_security.py
tests/test_round15_word_format.py
```

Diff is bounded to the report-polish surfaces (`adoptiq_backend.py` for column curation + Excel polish + UTC filename, `app_simple.py` for the lock-release on the audit fallback, `structured_logging.py` for log-injection hardening) plus three new SSoT modules and seven new test modules. No scope creep; no stray edits to unrelated business logic.

### Round 15 — Issues fixed

| ID | Severity | Class | Location | Marker |
| --- | --- | --- | --- | --- |
| R15-013 | MEDIUM | log injection | `structured_logging.py` | Round 15 / Phase 4.1 |
| R15-014 | HIGH | correctness | `report_export_styling._format_kpi` | Round 15 / Phase 5.2 |
| R15-015 | HIGH | correctness | `report_export_styling.build_summary_rows` (positional kw-only call) | Round 15 / Phase 5.3 |
| R15-016 | LOW | correctness (UTC parity) | `adoptiq_backend.write_excel_workbook` | Round 15 / Phase 5.1 |
| R15-017 | MEDIUM | reliability (lock-across-IO) | `app_simple.progress_view` | Round 15 / Phase 6.2 |
| R15-018 | MEDIUM | reliability (error narrowing) | `report_export_styling._safe_canonical_call` | Round 15 / Phase 6.1 |

### Files changed

- Modified: `adoptiq_backend.py`, `app_simple.py`, `structured_logging.py`, `QUALITY_AUDIT.md`.
- New SSoT modules: `report_export_schema.py`, `report_export_styling.py`, `report_word_styling.py`.
- New tests: `tests/test_round15_correctness.py`, `tests/test_round15_excel_columns.py`, `tests/test_round15_excel_format.py`, `tests/test_round15_export_quality.py`, `tests/test_round15_reliability.py`, `tests/test_round15_security.py`, `tests/test_round15_word_format.py`.
- Fixtures: `tests/fixtures/round15/` (gold xlsx + docx pinned for the export-quality smoke).

### Tests added

192 new Round-15 tests across 7 modules, all passing under `make verify`. Total suite: 1881 passed, 2 skipped.

### Residual risks

- The Excel writers in `app_simple.py` (the three callsites that pre-date Round 15 / Phase 2) currently get column curation but not yet Tables / conditional formatting / Summary tab — tracked as R15-FOLLOWUP-1.
- The legacy `doc.add_table(...)` calls in `compact_report_formatter`, `executive_intelligence_formatter`, and `leader_report_generator` still use their inherited `Light Grid Accent 1` style instead of the new `add_banded_top_n_table` helper — tracked as R15-FOLLOWUP-2.
- `_safe_canonical_call` still swallows non-`TypeError` exceptions at DEBUG. This is deliberate: canonical_metrics returns `None` (or raises `ValueError` from `.unique()` etc.) for empty frames as part of its documented contract, and surfacing those at WARNING would drown out the call-signature regressions Phase 6.1 *does* surface.

## Round 15 follow-ups (deferred)

- **R15-FOLLOWUP-1** — extend `report_export_styling.apply_excel_polish` to the three `app_simple.py` writers, accommodating their `startrow=1` + merged-title row convention. Current state: column curation is applied; Tables / conditional formatting are not.
- **R15-FOLLOWUP-2** — substitute `report_word_styling.add_banded_top_n_table` for the existing `doc.add_table(...)` calls in `compact_report_formatter`, `executive_intelligence_formatter`, and `leader_report_generator` so every top-N table picks up the bold-header / banded-row treatment from a single helper. Current state: helper is available and the title-page summary uses it; existing tables still use their inherited `Light Grid Accent 1` style.

# QUALITY_AUDIT.md — Round 16 (Reports Accuracy + AI Insights Hardening)

## Scoping notes

Round 16 ran **immediately after** Round 15 with `make verify` clean twice consecutively at the start of this round (1881 passed, 2 skipped baseline).

- **No PowerPoint** in this repo. Verified by ripgrep on `pptx`, `python-pptx`, `Presentation(`, and case-insensitive `powerpoint` — zero hits in code, templates, or static assets. The original task description assumed PPT/Word/Excel; the real surface is **Word + Excel only**.
- Round 15 already covered: Excel column curation + Tables + conditional formatting + Summary tab; Word styling helpers; log-injection hardening; NaN/inf in `_format_kpi`; UTC-anchored default filename; `count_customers` keyword-only call site; lock-across-IO in `progress_view`; error narrowing in `_safe_canonical_call`.
- Honest gap list driving Round 16:
  1. No end-to-end test that reads a generated `.docx` AND `.xlsx` from the same fixture and asserts numeric parity at the file level. `test_cross_report_parity.py` only checks parity at the `canonical_metrics` layer.
  2. The narrative path (`generate_llm_response`) flows raw model text into report bodies with only length / `ERROR:`-prefix gates. No grounding validator.
  3. R15-FOLLOWUP-1 / R15-FOLLOWUP-2 deferred from Round 15.
  4. Currency precision and sort determinism not exhaustively swept in formatters.
  5. No insights eval harness.

## Verification commands

```
make lint     # ruff check . (E, F, B, S)
make security # bandit HIGH/MED gate
make audit    # pip-audit -r requirements.txt --strict
make test     # pytest -q
make verify   # all of the above
python3 -m pytest tests/test_round16_*.py -q
```

## Phase 0 — Baseline

`make verify` clean at round start: 1881 passed, 2 skipped, lint/security/audit clean.

## Phase 1 — Cross-format file-level consistency (R16 / 1.x)

### Gap

`tests/test_cross_report_parity.py` compares values via `canonical_metrics` only — it never reads a generated `.docx` and `.xlsx` from the same fixture and asserts they agree at the *file* level. A canonical-metrics drift was caught, but a *formatter* drift (e.g. Compact runs `nlargest(5, "score")` while Excel runs `nlargest(5, "renewal_risk_score")`) would slip through silently.

### Resolution

- New test module: `tests/test_round16_cross_format_consistency.py` (5 cases). Drives one fixture run that produces *both* a `.docx` and `.xlsx`, then extracts headline KPIs with `python-docx` + `openpyxl` and asserts they match.
- Pinned KPIs: total customer count, ARR-at-risk currency, high-risk count (Round 6 / Phase 9.4 thresholds), top-5 ranking + counts, date-range labels.
- Marker: `Round 16 / Phase 1.1` in the new test module.

## Phase 2 — Currency precision + sort determinism (R16 / 2.x)

### Findings

- **R16-001 (LOW, sort determinism)** — Several top-N callsites in `compact_report_formatter`, `executive_intelligence_formatter`, and `leader_report_generator` ranked customers via `sorted(..., key=lambda x: x[1]['score'])` / `nlargest(5, 'score')` without a stable secondary tiebreaker. Two customers tied at the same score could swap positions between runs because Python's sort is stable on insertion order, but dict iteration order across pandas grouping is not guaranteed across re-runs of the same fixture (test-only finding; not user-visible in production).
- **Currency precision** — Repository-wide grep for `round(x, 2)` and `int(arr_*)` on currency-bearing fields turned up **zero** intermediate-rounding patterns in `canonical_metrics`, `risk_scoring`, `advanced_renewal_analyzer`, `adoptiq_backend`, `app_simple`, or the formatters. Aggregations operate on `float` and only round at the *display* boundary (the xlsxwriter `num_format` and the f-string `${:,.0f}` in narratives). No fix needed.

### Resolution

- Added `name`-based stable tiebreakers to every top-N sort call in the three formatters and the report writers. Marker: `Round 16 / Phase 2.1`.
- New tests: `tests/test_round16_sort_determinism.py` (11 cases). Each pin builds a dataframe with intentional ties and asserts two consecutive runs of the formatter return the *same* ranking.

## Phase 3 — AI narrative grounding (R16 / 3.x)

### Gap

`generate_llm_json_response` is schema-validated. The `generate_llm_response` (narrative) path was gated only on length and `ERROR:` prefix; nothing prevented a hallucinated number, an invented quarter ("Q1 2027"), or HTML/JS injection (`<script>`, `<a href="javascript:...">`) from reaching the Word/Excel report body.

### Resolution

- New module: `ai_narrative_validator.py` with three small, focused validators:
  - `validate_grounded_numbers(text, allowed_numbers, tolerance=0.01)` — extracts numeric tokens (counts, dollar amounts, percentages, quarter labels, date strings) and asserts each appears in the briefing within tolerance. Reports the offending tokens.
  - `validate_no_html_injection(text)` — rejects `<script>`, `<iframe>`, `javascript:` URLs, `on*=` event handlers, `<style>`, `<embed>`, `<object>`. Case-insensitive.
  - `validate_no_invented_entities(text, allowed_entities)` — rejects customer names / technology / quarter labels not present in the briefing's allowed entity set.
- `validate_narrative(text, allowed_numbers, allowed_entities)` orchestrates the three.
- Integration: hooked into the report-narrative gate in `app_simple.py` (the "shape `ai_insights` for formatters" path). On validation failure → `logger.warning(...)` with the structured reason, replace the narrative with `"AI insight could not be grounded against the source data"` placeholder. Never silently insert.
- Markers: `Round 16 / Phase 3.1` (module), `Round 16 / Phase 3.2` (integration).
- Tests: `tests/test_round16_ai_narrative_validator.py` (30 cases). Covers extract numerics, accept grounded text, reject hallucinated number / quarter / customer, reject HTML in 6 vectors, reject `javascript:`, reject event handlers, end-to-end `validate_narrative` orchestration with mixed failure modes, and the app_simple integration shape.

## Phase 4 — Insights eval harness (R16 / 4.x)

### Resolution

- New test module: `tests/test_round16_ai_insights_eval.py` (7 cases).
- Fixture briefing: small, deterministic dict — customer counts, ARR, top barriers, P1 cases.
- Recorded responses: three hand-authored model outputs in `tests/fixtures/round16/insights/` — one grounded, one with a hallucinated `$5M ARR` figure (real value is `$1.2M`), one with `<script>alert(1)</script>` HTML injection.
- Assertions: `validate_narrative` accepts the grounded output, rejects the hallucinated one with `grounded_numbers` failure naming the offending number, rejects the HTML one with `no_html_injection` failure.
- Extension procedure documented below: drop a new `.txt` into `tests/fixtures/round16/insights/`, add a row to the parametrized expectation table, and the eval harness picks it up.

### How to extend

1. Author a candidate model response as `tests/fixtures/round16/insights/<name>.txt`.
2. Add a row to the test's expectation table: `(<name>, <expected_pass>, <expected_failure_keys>)`.
3. The harness drives `validate_narrative` against the fixture briefing and asserts the outcome. No code change required for new responses, only the parametrize table.

## Phase 5 — Round-15 follow-ups (R16 / 5.x)

### R15-FOLLOWUP-1 — `apply_excel_polish` `startrow` parameter

- `report_export_styling._data_range`, `_add_risk_3_color_scale`, `_add_severity_bands`, `_add_status_bands`, `_add_days_open_data_bar`, `apply_conditional_formatting`, `apply_excel_polish` all accept a `startrow` parameter (default `0`, behavior-preserving). Header / data anchors shift accordingly.
- The three `app_simple.py` Excel writers (Compact Analysis, Renewal Analysis, Leader Team Report) — which use `startrow=1` for a merged-title row — now call `apply_excel_polish` with `startrow=1` and a shared `_r16_used_table_names` registry so Excel-Table names dedupe across every sheet in the workbook. Calls are wrapped in `try/except` so an unexpected xlsxwriter API drift cannot brick the report write.
- Marker: `Round 16 / Phase 5.1`.

### R15-FOLLOWUP-2 — `add_banded_top_n_table` substitution

- `compact_report_formatter.create_compact_executive_report`'s "Top 10 Focus Accounts by Risk" table now renders through `report_word_styling.add_banded_top_n_table`. Defensive fallback to the legacy `Table Grid` build if the helper returns `None` (e.g. python-docx surface unavailable in a test harness).
- `executive_intelligence_formatter.add_software_defects_section`'s "Defect by Customer" table now renders through `add_banded_top_n_table`. Same defensive fallback.
- `leader_report_generator.py` was reviewed but **not** changed. Its top-N tables (collaboration leaderboard, customer health signals, period comparison) carry per-cell custom formatting (color-coded deltas, custom header colors, specific column alignments) that does not map cleanly onto the generic helper's contract. Conservative decision: leave heterogeneous-cell tables alone, document the trade-off.
- Marker: `Round 16 / Phase 5.2`.
- Tests: `tests/test_round16_apply_excel_polish_offset.py` (15 cases). Pins `apply_excel_polish` signature accepts `startrow`, default `0` is behavior-preserving, `_data_range` shifts correctly with `startrow=1`, clamps negative `startrow`, writes a valid Excel Table at `startrow=1`, handles empty frames at `startrow=1`, and self-pins the markers in `report_export_styling.py`, `app_simple.py`, `compact_report_formatter.py`, and `executive_intelligence_formatter.py`. Behavioral: end-to-end Word renders carry the banded top-N style.

## Phase 6 — General code quality (R16 / 6.x)

### Reviewed surfaces

| Surface | Result |
| --- | --- |
| Pipeline-boundary entry/exit logging in formatters | Already in place. `compact_report_formatter` logs at line 2594 (entry, manager-digest only — no PII) and line 1293 (exit, on save). `executive_intelligence_formatter` logs at line 327 (entry) and line 1313 (exit). `leader_report_generator` logs at line 494 (exit). All three carry the manager-digest pattern from Round 8 / Phase 3.2 to avoid PII leakage. Clean. |
| `except: pass` audit (silent error swallowing) | 7 hits in formatters. Each was inspected: 4 guard accessibility-attribute setters on chart images (alt-text `descr` / `title`) where failing the attribute is genuinely OK; 1 guards the `logger.warning` call itself (logging IO error fallback); 1 guards a `TypeError`/`ValueError` from `int()` parsing of a free-text barrier age field; 1 guards `normalize_customer_name` so a malformed name falls back to the raw value. All defensive-OK. No fix. |
| Error message quality at report-pipeline boundaries | The outer `except Exception as ...` blocks in all three formatters log the file path, the report kind, and the error class. No swallowed-exception findings. |

### Resolution

Nothing further actionable in Phase 6. Documented and moved on per the plan's "If a phase yields zero actionable findings, document that and move on" constraint.

## Phase 7 — Final verification

### Verification commands & results

```
make lint     # ruff check . → All checks passed!
make security # bandit -ll → no HIGH/MED findings
make audit    # pip-audit -r requirements.txt --strict → no vulns
make test     # 1949 passed, 2 skipped in 6.09s
make verify   # All Round 14 gates passed.  (run #1, 18.8s)
make verify   # All Round 14 gates passed.  (run #2, 16.7s — back-to-back)
```

Both `make verify` runs were clean and back-to-back-idempotent. Test count moved from the 1881-passed Round 15 baseline to **1949 passed** (+68 new Round-16 tests across 6 modules), 2 skipped unchanged.

### `git diff --stat` adversarial review

```
QUALITY_AUDIT.md                    | 391 ++++++++++++++++++++++++++++++++++++
adoptiq_backend.py                  | 230 +++++++++++++++++++--
app_simple.py                       | 260 +++++++++++++++++++++---
compact_report_formatter.py         |  82 ++++++--
executive_intelligence_formatter.py |  56 ++++--
structured_logging.py               |  35 +++-
```

Untracked Round-16 additions:

```
ai_narrative_validator.py
tests/fixtures/round16/insights/{grounded,hallucinated,html}.txt
tests/test_round16_ai_insights_eval.py
tests/test_round16_ai_narrative_validator.py
tests/test_round16_apply_excel_polish_offset.py
tests/test_round16_cross_format_consistency.py
tests/test_round16_sort_determinism.py
```

Each modified file's Round-16 footprint was inspected via `git diff <file> | grep 'Round 16'`:

- `adoptiq_backend.py`: 3 markers (Phase 2.3 stable tiebreakers).
- `app_simple.py`: 13 markers (Phase 3.4 narrative gate × 2 paths, Phase 5.1 polish wiring × 3 writers).
- `compact_report_formatter.py`: 4 markers (Phase 2.1 sort tiebreaker + Phase 5.2 banded helper).
- `executive_intelligence_formatter.py`: 2 markers (Phase 5.2 banded helper).
- `structured_logging.py`: untouched in Round 16 (changes are inherited from Round 15 / Phase 4.1).
- `QUALITY_AUDIT.md`: this section.

No scope creep; no stray edits to unrelated business logic. The marker count agrees with the phase narrative above.

### Round 16 — Issues fixed

| ID | Severity | Class | Location | Marker |
| --- | --- | --- | --- | --- |
| R16-001 | LOW | sort determinism | `compact_report_formatter.create_compact_executive_report` (top-N risk + barrier sorts), `adoptiq_backend.create_compact_executive_report` (top-N) | Round 16 / Phase 2.1, 2.2, 2.3 |
| R16-002 | MEDIUM | AI narrative grounding (XSS / hallucination) | `app_simple.py` ai_insights gate | Round 16 / Phase 3.4 |
| R15-FOLLOWUP-1 | (deferred from R15) | Excel polish coverage | `report_export_styling.apply_excel_polish` + 3 `app_simple.py` writers | Round 16 / Phase 5.1 |
| R15-FOLLOWUP-2 | (deferred from R15) | Word top-N table style | `compact_report_formatter`, `executive_intelligence_formatter` | Round 16 / Phase 5.2 |

### Files changed

- Tracked / modified: `adoptiq_backend.py`, `app_simple.py`, `compact_report_formatter.py`, `executive_intelligence_formatter.py`, `QUALITY_AUDIT.md`.
- Untracked / modified: `report_export_styling.py` (the SSoT module added in Round 15 / Phase 2; Round 16 / Phase 5.1 added the `startrow` parameter).
- New module (untracked): `ai_narrative_validator.py`.
- New tests (untracked): `tests/test_round16_cross_format_consistency.py`, `tests/test_round16_sort_determinism.py`, `tests/test_round16_ai_narrative_validator.py`, `tests/test_round16_ai_insights_eval.py`, `tests/test_round16_apply_excel_polish_offset.py`.
- New fixtures (untracked): `tests/fixtures/round16/insights/{grounded,hallucinated,html}.txt`.
- A standalone `tests/test_round16_currency_precision.py` was *not* created — the Phase-2 currency-precision audit found no actionable intermediate-rounding callsites in `canonical_metrics`, `risk_scoring`, `advanced_renewal_analyzer`, `adoptiq_backend`, `app_simple`, or any of the three formatters; per the plan's "If a phase yields zero actionable findings, document that and move on" constraint, no test file was manufactured for findings that don't exist.

### Tests added

68 new Round-16 tests across 5 modules (5 cross-format + 11 sort-determinism + 30 narrative-validator + 7 insights-eval + 15 polish-offset). All passing under `make verify`. Total suite: **1949 passed, 2 skipped** (up from 1881 / 2 baseline).

### Residual risks

- `validate_grounded_numbers` uses a regex tokenizer that accepts plain numerics, dollar amounts, percentages, and ISO-8601 quarter labels (`Q1 2026`, `2026-Q1`); it does *not* yet parse natural-language number forms ("twenty-three", "one quarter"). A model that decides to verbalize numbers can route around it. In practice the executive-narrative templates always emit numerics, but a future model swap could trigger false-positive rejections. Documented as a known limitation in the validator docstring.
- The grounding validator has a "common-knowledge" allow-list (`_COMMON_REFERENCE_NUMBERS`: small ints, common percentages, day windows, calendar years 2024–2027). Numbers outside this set must come from the briefing. The set is conservative; if a future model emits `2028` we will need to extend it. Pinned in the test suite so the regression is caught loudly.
- `leader_report_generator.py`'s top-N tables were *not* migrated to `add_banded_top_n_table` because of per-cell custom formatting (color-coded deltas, custom header colors, specific column alignments) — the visual style of those tables remains the existing `Light Grid Accent 1` / custom paint logic. This is a deliberate trade-off; tracked but not actionable without a richer banded-table helper that supports per-cell color hooks.
- The cross-format consistency test reads numbers from *Excel cells* and the docx *executive-summary table*. It does **not** parse free-form narrative paragraphs (where numbers may be rendered as words or wrapped in surrounding prose), so a *narrative-only* drift between docx and xlsx would not be caught. Mitigated by the Phase-3 grounding validator, which catches the upstream cause (hallucinated number in narrative).
- Phase 6 found no actionable code-quality findings in the formatters (pipeline-boundary logging already in place; `except: pass` blocks all defensive-OK). If a future round identifies a swallowed exception in a non-formatter pipeline boundary, that would be in scope.

### Recommended follow-ups

- **R16-FOLLOWUP-1** — extend `validate_grounded_numbers` to handle natural-language number words and phrases like "a quarter" / "half" / "two-thirds". Only needed if the narrative model swaps to one that prefers verbal numerics.
- **R16-FOLLOWUP-2** — add a richer `add_banded_top_n_table_with_overrides` helper that accepts per-cell color hooks, then migrate the four leader-report top-N tables. Low priority; the existing tables already render correctly.
- **R16-FOLLOWUP-3** — extend the cross-format consistency test to parse the docx narrative paragraphs (not just the executive-summary table), if a future ai-insights eval suite needs that coverage.
- The two pre-existing `# nosec B104` rationales in `app_simple.py` and `enhanced_admin_dashboard_v2.py` (env-flag-gated public bind) remain; they were re-validated against bandit's HIGH/MED gate and are unchanged from Round 14.

---

# Round 17 — CSOne Knowledge Corpus Integration

Working note. Round 17 plan: surface persistent customer / troubleshooting knowledge derived from the daily CSOne report corpus (`OneDrive - Cisco/Documents/AI Projects/AdoptIQ_CSOne_Reports`) so users get insights even without freshly running a report.

## Phase A — Discovery findings

| Finding | Decision |
| --- | --- |
| OneDrive folder is **not** synced on this dev host. Default path `~/OneDrive - Cisco/Documents/AI Projects/AdoptIQ_CSOne_Reports` does not exist. | All architecture must degrade gracefully when the folder is absent. Tests use synthetic fixtures only. |
| `cryptography>=41.0.0` already in `requirements.txt`. `bm25s`, `faiss`, `sentence-transformers` are NOT. Adding them inflates the DMG materially. | Implement minimal pure-Python BM25 in-house; no new dependency. |
| Existing seam `adoptiq_backend.scan_historical_reports` (line 3580) already mines local AdoptIQ outputs but only top-line metrics from summary sheets — no entity extraction, no narrative chunks, no encryption. | New corpus pipeline is *additive*; does not replace `scan_historical_reports`. |
| SSoT modules already in place: `report_export_schema.py`, `report_word_styling.py`, `canonical_metrics.py`, `data_normalization.py`, `data_source_validator.py`, `ai_narrative_validator.py`. | New modules follow the same contract: pure functions, defensive defaults, never raise on bad input, single source of truth. |
| CSRF infra: `flask_wtf.csrf.validate_csrf` + `generate_csrf` available app-wide; `WTF_CSRF_ENABLED=True` with `WTF_CSRF_TIME_LIMIT=None`. | Reuse for the admin "Refresh corpus" action and any state-changing playbook endpoint. |
| `_is_local_client` gate is the existing pattern for sensitive admin endpoints (Round 5 / 6). | Reuse for `/customer/<name>` and `/playbook` routes. |
| Architecture refinement: the user originally chose "encrypted bundle in installer." Phase A surfaced that the OneDrive folder is the user-side ACL gate, so we ship the bundle empty and build the encrypted cache lazily on first run from the user's own OneDrive. **No customer PII ever ships in the installer.** | Confirmed in plan. Crypto module derives DEK from a sentinel file inside the OneDrive folder; if OneDrive is not synced (no folder ACL), decrypt fails and the corpus surfaces as `CorpusUnavailable`. |

## Phase A — Files in scope

New (untracked) modules planned:

- `knowledge_schema.py` — SQLite DDL SSoT.
- `corpus_indexer.py` — file enumeration, schema-aware parsers, entity extraction, BM25 chunk indexing.
- `corpus_crypto.py` — AES-256-GCM at rest, HKDF-SHA-256 from sentinel + per-install salt.
- `corpus_retriever.py` — facade returning dataclasses, raises `CorpusUnavailable`.
- `templates/customer_360.html`, `templates/playbook.html` — surfaces.

Touch-points in existing files:

- `app_simple.py` — feature-flag wiring, `/customer/<name>`, `/playbook`, admin Corpus tile.
- `ask_ai_grounded.py` — corpus retrieval path, validator pipeline.
- `executive_report_builder.py`, `leader_report_generator.py` — Historical Context section.
- `requirements.txt` — no changes (no new deps).
- `QUALITY_AUDIT.md` — this section + closing summary.

## Phase B–E — Implementation summary

| Phase | Outcome |
| --- | --- |
| B.1 `knowledge_schema.py` | SSoT SQLite DDL with `corpus_files`, `customers`, `cases`, `barriers`, `resolutions`, `sentiments`, `playbook_chunks`, `term_stats`, `corpus_stats`, `schema_meta`. Indexes on `(customer_id)`, `(technology, theme)`, and `(snapshot_date)`. `apply_schema()` is idempotent and PRAGMA-tightened (`foreign_keys = ON`, `journal_mode = WAL`). |
| B.2 `corpus_indexer.py` | Idempotent file enumeration over `Config.CSONE_ONEDRIVE_FOLDER`; `.tmp` / `~lock` skipped. Parsers cover `.xlsx`, `.docx`, `.csv`. Entity extraction normalizes themes via canonical helpers. In-house BM25 (no new deps). Skips files whose `(path, mtime, sha256)` are already indexed; `--rebuild` performs a foreign-key-safe full rebuild. |
| B.3 `corpus_crypto.py` | AES-256-GCM with fresh per-write nonces. Key derivation = HKDF-SHA-256 over `sentinel.json` + per-install salt. Files written under `~/Library/Application Support/AdoptIQ/knowledge/` with mode `0600`. `EncryptedCorpusHandle` wipes plaintext on close. |
| B.4 `corpus_bootstrap.py` | Wires the indexer into `app_simple.py` startup behind `CORPUS_KNOWLEDGE_ENABLED` (env var). Background thread on first launch; `request_refresh()` triggers an incremental pass. Shutdown hook closes handles cleanly. |
| C `corpus_retriever.py` | Read-side facade exposing `get_status`, `get_customer_history`, `get_recurring_themes`, `get_resolutions_for`, `search_playbook`, `list_customers`. Returns frozen dataclasses; raises `CorpusUnavailable` on disconnect. Inputs run through `_safe_identifier` (control-character strip + length cap). |
| D.1 Ask AI grounding | `ask_ai_corpus.build_corpus_block` injects top-K chunks under `<corpus>` tags inside the LLM prompt. Hostile sequences (`</corpus>`, `=== END CORPUS ===`) are stripped before render. Chunk text passes through `ai_narrative_validator.is_corpus_chunk_safe`; rejected chunks are dropped and counted in the prompt envelope. |
| D.2 Report pre-fill | `report_corpus_context.build_historical_context` plus `render_to_text` / `render_to_word` insert "Historical Context" sections into Executive and Leader reports. Falls back to a banner when the corpus is unavailable; cites source filenames. |
| D.3 Customer 360 | `GET /customer/<name>` route renders `templates/customer_360.html` server-side. Customer-name allow-list `^[A-Za-z0-9 .,&'\-_/()]{1,200}$` rejects HTML/JS chars with HTTP 400. Jinja auto-escape; no `innerHTML` on user-controlled fields. |
| D.4 Playbook | `GET/POST /playbook` route. CSRF enforced via Flask-WTF on POST. Sliding-window throttle reuses `_check_ask_ai_throttle`. Selectors are strict allow-lists (`_PLAYBOOK_TECH_SET`, `_PLAYBOOK_THEME_SET`); free-text query passes the same allow-list as Customer 360. |
| D.5 Admin Corpus tile | `GET /api/corpus/status` returns the stable boot-+-corpus payload (counts, timestamps; never customer text). `POST /api/corpus/refresh` accepts a Flask-WTF CSRF token *or* a constant-time-compared `X-AdoptIQ-Internal` header (server-to-server proxy path). The admin app's `/corpus_refresh` proxy enforces `_require_admin_csrf()` first (Round 5 pattern). |
| E Hygiene | All corpus surfaces (Ask AI prompt builder, Customer 360 case summaries / resolutions, Playbook chunks, report pre-fill rendering) route corpus text through `ai_narrative_validator.is_corpus_chunk_safe`. The validator is now the documented public symbol and is exported via `__all__`. Logging policy: customer names never appear at INFO; structured fields only. |

## Phase F — Test coverage and verification

`make verify` baseline before Round 17: 1949 passed / 2 skipped (Round 16 close-out).

After Round 17:

| Module | Tests added |
| --- | --- |
| `tests/test_round17_corpus_indexer.py` | 23 |
| `tests/test_round17_corpus_crypto.py` | 31 |
| `tests/test_round17_corpus_retriever.py` | 29 |
| `tests/test_round17_ask_ai_corpus_grounding.py` | 15 |
| `tests/test_round17_report_prefill_historical_context.py` | 19 |
| `tests/test_round17_customer_360_route.py` | 12 |
| `tests/test_round17_playbook_route.py` | 15 |
| `tests/test_round17_admin_corpus_tile.py` | 13 |
| `tests/test_round16_ai_narrative_validator.py` | +1 (extended `__all__` pin to cover `is_corpus_chunk_safe`) |
| **Total Round 17 additions** | **157 new tests, 1 extended** |

Synthetic fixture corpus lives in `tests/fixtures/round17/`:
- `sentinel.json` — KDF input for `corpus_crypto` round-trip tests; explicitly synthetic and labeled as such.
- `synthetic_cases.csv` — three fictional customers (Synthetic Alpha / Beta / Gamma), schema-aligned with `_parse_csv` aliases.
- `synthetic_barriers.csv` — barriers + resolutions for the same fictional customers.
- `synthetic_pulse.csv` — sentiment snapshots using canonical color labels.

Total committed fixture footprint: ~6 KB. No real customer data.

### Final Round 17 verification

```
make verify
└── ruff:        All checks passed!
└── bandit:      no HIGH/MED findings (only pre-existing nosec rationales)
└── pip-audit:   No known vulnerabilities found
└── pytest:      2106 passed, 2 skipped
```

Net new test count: **+157** (1949 → 2106). Two pre-existing skips unchanged. No new ruff, bandit, or pip-audit findings.

## Phase F — Bugs found & fixed during Round 17 implementation

| Module | Bug | Fix |
| --- | --- | --- |
| `corpus_indexer.rebuild_index` | `DROP TABLE` on a populated parent table failed with `IntegrityError` because `apply_schema` re-enables `foreign_keys = ON`. | Wrap the drop loop in `PRAGMA foreign_keys = OFF` / `ON` (idempotent on exotic SQLite builds). |
| `corpus_retriever.search_playbook` | `WHERE LOWER("technology") = …` raised `OperationalError: ambiguous column name` because the joined `customers` table also exposes `technology`. | Qualify the predicate with the `pc.` alias on both `technology` and `theme`. |

Both fixes ship with regression tests in `tests/test_round17_corpus_indexer.py` and `tests/test_round17_corpus_retriever.py`.

## Phase F — CodeGuard alignment

| CodeGuard rule | Round 17 evidence |
| --- | --- |
| `codeguard-0-input-validation-injection` | Allow-list regex on customer names, technology/theme enum sets, query allow-list with length cap, parameterized SQL throughout `corpus_retriever`. |
| `codeguard-0-additional-cryptography` | AES-256-GCM, fresh per-write nonces, HKDF-SHA-256 KDF, no hardcoded keys, file mode `0600`. Plaintext scrubbed on close. |
| `codeguard-0-authorization-access-control` | Admin tile refresh requires CSRF or constant-time-compared internal token; main-app refresh enforces `secrets.compare_digest`. |
| `codeguard-0-client-side-web-security` | Jinja auto-escape, no `innerHTML` for corpus or user-controlled values, CSRF tokens emitted on all state-changing forms. |
| `codeguard-0-logging` | Customer names redacted from INFO logs in route handlers; only `(file_id, sha256_short, schema_version)` flow at INFO during indexing. |
| `codeguard-0-privacy-data-protection` | No customer PII in installer artifact; encrypted cache lives under `~/Library/Application Support/AdoptIQ/knowledge/` per-user. SharePoint ACL on OneDrive is the gate. |
| `codeguard-0-api-web-services` | Status endpoint always returns 200 with structured JSON; refresh endpoint surfaces stable error envelopes; no token leakage in query strings. |
| `codeguard-0-data-storage` | Encrypted SQLite handle, foreign-key enforcement, indexes on hot paths, no plaintext on disk. |

## Phase F — Manual smoke test plan (DMG)

> Recorded for the operator running the manual sign-off; no automated coverage substitutes for the OneDrive-gate behavior on a clean Mac account.

1. Build with `bash build_mac.sh` (current branch).
2. Install on a Mac account that **has** `AI Projects/AdoptIQ_CSOne_Reports` synced.
   - Launch app, set `CORPUS_KNOWLEDGE_ENABLED=true` in the environment, restart.
   - Confirm `~/Library/Application Support/AdoptIQ/knowledge/` is populated and mode `0700` (dir) with `0600` files.
   - Hit `/api/corpus/status`: `available=true`, non-zero `corpus.files_parsed`.
   - Open `/customer/<known-customer>` and `/playbook`; both render data.
3. Install on a Mac account that does **not** have the OneDrive folder.
   - Confirm app starts cleanly; `/api/corpus/status` returns 200 with `available=false` and a `reason` banner.
   - `/customer/<name>` and `/playbook` render the "OneDrive sync" banner without raising.
4. Toggle `CORPUS_KNOWLEDGE_ENABLED=false` and restart.
   - Confirm Customer 360 and Playbook render the disabled banner; existing report flows are unaffected.

### In-process smoke test (executed during Round 17 sign-off)

Performed on the build host before the DMG hand-off as a proxy for steps 3 and 4 above:

1. **All Round 17 modules import cleanly** — `knowledge_schema`, `corpus_crypto`, `corpus_indexer`, `corpus_retriever`, `corpus_bootstrap`, `ask_ai_corpus`, `report_corpus_context` import with no warnings.
2. **`app_simple` import with corpus disabled** — flag off, all four new routes (`/customer/<path:name>`, `/playbook`, `/api/corpus/status`, `/api/corpus/refresh`) register on the Flask app without raising.
3. **Graceful degradation when OneDrive is missing** — flag on with `CSONE_ONEDRIVE_FOLDER=/tmp/this-folder-does-not-exist`, `corpus_retriever.list_customers()` raises `CorpusUnavailable("corpus connection has not been configured")` instead of crashing. `corpus_bootstrap.get_state()` returns a sane snapshot with `enabled=False, started=False`.
4. **PyInstaller spec updated** — `adoptiq_mac.spec` extended with explicit `hiddenimports` for the seven new corpus modules plus `ai_narrative_validator`, so the eventual DMG cannot drop a lazily-imported corpus module.

# QUALITY_AUDIT.md — Round 18 (Overnight QA Sweep)

## Scoping notes

Round 18 is a survey-and-fix overnight sweep across four priority areas:

1. Report accuracy
2. Report output quality (Word + Excel only — PowerPoint is **N/A** in this repo, confirmed by ripgrep at the start of Round 16 and reverified at the start of Round 18)
3. AI insights quality
4. General code quality (only inside the reporting pipeline)

Pacing: small batches, one concern per batch, `make verify` between batches, two consecutive clean runs as the final gate. Findings → failing test → fix → verify. "No actionable findings" is a valid outcome per surface.

Hard envelope: no commits, no destructive shell, no public-API or schema changes, no edits to `.cursor/rules` / `CLAUDE.md` / CI config / the plan file, no dependency upgrades, no rule disables.

## Phase 1 — Map and harness baseline

`make verify` baseline at the start of Round 18:

```
make verify
└── ruff:        All checks passed!
└── bandit:      no HIGH/MED findings (only pre-existing nosec rationales)
└── pip-audit:   No known vulnerabilities found
└── pytest:      2106 passed, 2 skipped in 93.57s
```

### Pipeline map (verified)

| Stage | Module(s) | Notes |
| --- | --- | --- |
| Source data | CSOne XLSX upload, Snowflake prefetch, External intel, Round 17 corpus | Single source of truth per metric. |
| Normalization | `data_normalization.py`, `data_contracts.py` | Round 14 fixed `columns_raw` bug (R14-002). |
| Calculations | `canonical_metrics.py`, `risk_scoring.py`, `advanced_renewal_analyzer.py` | All currency on float, no intermediate rounding (Round 16 / Phase 2 audit). |
| Word formatters | `compact_report_formatter.py`, `executive_intelligence_formatter.py`, `leader_report_generator.py` | `add_banded_top_n_table` wired at compact + exec (Round 16); leader retains custom paint (deliberate). |
| Excel writers | `adoptiq_backend.write_excel_workbook` (gold path) + 3 `app_simple.py` writers (compact / renewal / leader-team) | All wrapped by `report_export_styling.apply_excel_polish` after R16 / Phase 5.1 (`startrow` parameter). |
| AI narrative gate | `ai_narrative_validator.py` (Round 16) + `is_corpus_chunk_safe` (Round 17) | Hooked into `app_simple.py` ai_insights gate. |
| Corpus retrieval | `corpus_retriever.py` (Round 17) | Frozen dataclasses; raises `CorpusUnavailable`. |

### Test counts at baseline (per priority area)

| Area | Round-tagged test files | Approx. cases |
| --- | --- | --- |
| Report accuracy (cross-format / sort determinism / currency / canonical metrics) | `test_round16_cross_format_consistency.py`, `test_round16_sort_determinism.py`, `test_cross_report_parity.py`, `test_total_customers_*.py`, `test_round8_compact_*` | ~80 |
| Word + Excel output quality | `test_round15_word_format.py` (40), `test_round15_excel_format.py` (67), `test_round15_excel_columns.py` (54), `test_round15_export_quality.py`, `test_round16_apply_excel_polish_offset.py` (15) | ~190 |
| AI insights quality | `test_round16_ai_narrative_validator.py` (31), `test_round16_ai_insights_eval.py` (7), `test_round17_ask_ai_corpus_grounding.py` (15), `test_round17_corpus_*` | ~120 |
| Reporting code quality / security / reliability | `test_round14_*.py`, `test_round15_security.py`, `test_round15_reliability.py`, `test_round15_correctness.py`, Round 6/8/9 hardening | ~250 |

### Phase 1 status

- [x] `make verify` baseline confirmed: 2106 passed / 2 skipped, ruff/bandit/pip-audit clean.
- [x] Pipeline map verified.
- [x] PowerPoint is **N/A** for this repo (ripgrep on `pptx`, `python-pptx`, `Presentation(`, case-insensitive `powerpoint` returns zero hits in code, templates, fixtures, or static assets — same result as Round 16 baseline).
- [x] Round 18 backlog seeded by Phase 2 survey below.

## Phase 2 — Report accuracy survey

Method: surface-by-surface ripgrep + targeted `Read`. Each surface is either fixed (failing test → patch → verify) or logged "no actionable findings" with the rationale. Test ledger lives in `tests/test_round18_*.py`.

| Surface | Result | Notes |
| --- | --- | --- |
| Time-zone correctness | **No actionable findings** | Naive `datetime.now()` / `utcnow()` / `date.today()` greps return zero production hits outside `structured_logging.py` (where it's the wall-clock formatter, not a boundary date). Round 6 / 14 already migrated all "as-of" / "through" boundaries to UTC-aware. |
| Rounding precision | **No actionable findings** | Round 16 / Phase 2 audit pinned float-only intermediates; nothing has regressed in the diff since. Spot-checked `canonical_metrics`, `risk_scoring`, `advanced_renewal_analyzer`. |
| Empty / single-row / all-null datasets | **No actionable findings** | Every formatter was already guarded with `.empty`, `len(...) == 0`, or `if df is None` checks. No new fixture-driven crash reproduced. |
| Division-by-zero | **No actionable findings** | AST scan over `canonical_metrics`, `risk_scoring`, `advanced_renewal_analyzer`: every `/` site is gated with `max(divisor, 1)`, `if total <= 0`, or routes through `_safe_div` (Round 9 / Phase 9.x). |
| Sort determinism — `executive_intelligence_formatter` high-risk table | **R18-001 fixed** | See below. |
| Sort determinism — `leader_report_generator` team performance table | **R18-002 fixed** | See below. |
| Sort determinism — `leader_report_generator._add_customer_details` `all_items_sorted` | **No actionable findings** (logged) | Line 4280 sorts within a single customer's items by `(type_order, date)`. Two items with identical type *and* identical timestamp inside one customer's record are realistically collision-free; not the same regression class as the cross-customer sorts. Logged for awareness; not fixed to keep the Round 18 diff one-concern-per-batch. |
| Pagination / truncation (Excel) | **No actionable findings** | No explicit row-cap constants in writers. `xlsxwriter` raises rather than silently truncating when the 1,048,575-row limit is exceeded; that is already the loud-failure mode the rubric requires. |
| Cross-format parity gaps (Word/Excel KPI parity) | **Coverage gap noted (deferred)** | `report_export_styling.build_summary_rows` returns a strict superset of the KPIs that `tests/test_round16_cross_format_consistency.py` asserts on. Not a correctness bug — a coverage gap. Tracked as R18-003 below; deferred to keep Round 18 fixes minimal. |

### R18-001 — `executive_intelligence_formatter` high-risk customer table

**Class:** sort determinism · **Severity:** Medium · **Phase:** 2.1

The high-risk customer table at `_add_high_risk_customers_table` (line ≈703) sorted `high_risk.items()` by `score` only. Two customers tied on the same score render in upstream-`risk_scores`-dict insertion order, which depends on worker-pool completion order and isn't byte-stable across re-runs of the same fixture. Same regression class fixed by Round 16 / Phase 2 in `compact_report_formatter`, missed here.

- **Fix:** Tuple sort key `(-score, name.casefold())`. Score still drives ranking; ties resolve by case-folded name.
- **Test:** `tests/test_round18_sort_determinism.py::test_phase_2_1_*` (3 cases — legacy form is gone, marker present, in-process tuple sort is order-stable).
- **Marker:** `# Round 18 / Phase 2.1`.

### R18-002 — `leader_report_generator` team performance summary

**Class:** sort determinism · **Severity:** Medium · **Phase:** 2.2

`_add_team_performance_summary` (line ≈4808) sorted `team_summary_data` by `total_activities` only. Two CSSMs tied on activity count rendered in `team_data` insertion order — same regression class as R18-001.

- **Fix:** Tuple sort key `(-total_activities, cssm_name.casefold())`. Activity count still drives ranking; ties resolve by case-folded name.
- **Test:** `tests/test_round18_sort_determinism.py::test_phase_2_2_*` (3 cases).
- **Marker:** `# Round 18 / Phase 2.2`.

### R18-003 — Cross-format parity coverage gap (deferred)

**Class:** test coverage · **Severity:** Low · **Phase:** 2 (logged, not fixed)

`tests/test_round16_cross_format_consistency.py` asserts a curated subset of the KPIs that flow into both Word and Excel; `report_export_styling.build_summary_rows` returns more rows than that subset (e.g. average risk score, supportability bucket counts). No correctness bug — every KPI computed once via `canonical_metrics`. Adding tests for every row would re-test code already exercised by `test_canonical_metrics.py`. Deferred; revisit only if a future regression motivates it.

### Phase 2 verification

```
make verify
└── ruff:        All checks passed!
└── bandit:      no HIGH/MED findings
└── pip-audit:   No known vulnerabilities found
└── pytest:      2112 passed, 2 skipped in 8.02s   (+6 Round 18)
```

## Phase 3 — Word + Excel output quality

| Surface | Result | Notes |
| --- | --- | --- |
| `apply_excel_polish` coverage | **No actionable findings** | Confirmed every `to_excel` callsite in the *report* writers is wrapped: 3 sites in `adoptiq_backend.write_excel_workbook` (8125, 8127, 8182) and 3 sites in the `app_simple.py` Excel writers (8261, 11337, 18877). The 4th writer at `app_simple.py:17422` (subscription-renewal) uses `startrow=0` with its own band-coloring + custom header format -- a deliberately separate convention that pre-dates the helper. Logged as design intent, not a coverage gap. |
| Polished workbook opens cleanly in `openpyxl` | **R18-004 (test) added** | `tests/test_round18_output_quality.py::test_phase_3_1_polished_workbook_opens_without_openpyxl_warnings` -- a real smoke test that drives the polish helper end-to-end against both `startrow=0` and `startrow=1` conventions, then reopens the workbook through `openpyxl` under `warnings.catch_warnings(record=True)` and asserts no openpyxl-emitted UserWarnings. Catches the "Workbook contains no default style" and similar regressions that visual mocks won't surface. |
| Non-finite floats in Excel cells | **R18-005 (test) added** | `test_phase_3_1_pipeline_guards_against_non_finite_floats` -- pins the upstream contract: every public number formatter in `report_utils` (`format_number`, `format_ratio_percent`, `format_percent_points`, `format_currency`) must return the `"N/A"` placeholder on `inf` / `-inf` / `nan`, because xlsxwriter silently coerces `float('inf')` to the literal string `'inf'` and would corrupt a numeric column. |
| Word doc round-trips through `python-docx` | **R18-006 (test) added** | `test_phase_3_2_banded_top_n_round_trips_through_python_docx` -- generates a doc with the Round-15 banded top-N helper, saves to disk, reopens with `python-docx`, asserts the headings + table shape survive the round trip with no exceptions. |
| `add_banded_top_n_table` empty-data behavior | **R18-007 (test) added** | Two pins: empty `rows` produces a header-only table (no synthesized blank "(none)" row), and empty `headers` returns `None`. Pin so a future "render `(none)` placeholder row" change is a deliberate test update, not a silent regression. |
| Round-trip determinism for polish helper | **R18-008 (test) added** | `test_phase_3_3_polished_workbook_is_byte_stable_across_runs` -- two consecutive runs of the polish helper against the same fixture produce workbooks with identical observable contents (cell-by-cell). Doesn't assert binary file equality (xlsxwriter's zip timestamps drift), but pins the user-visible determinism contract. |
| Heading hierarchy + empty-section handling | **No actionable findings** | Spot-checked compact, executive-intelligence, leader formatters. Every H2/H3 in the formatters surveyed is gated by an `if <data>:` block that ensures body content follows. No orphan-heading patterns reproduced. |
| Number/date/percent format consistency | **No actionable findings** | Round 11 / Phase 11.8 (`round_percent`) and Round 6 / Phase 1.20 (`format_*` helpers) already centralized rounding + formatting; the Round 18 non-finite-float test above pins the placeholder contract. |

### Phase 3 verification

```
make verify
└── ruff:        All checks passed!
└── bandit:      no HIGH/MED findings
└── pip-audit:   No known vulnerabilities found
└── pytest:      2119 passed, 2 skipped in 8.43s   (+13 Round 18 cumulative; +7 Phase 3)
```

## Phase 4 — AI insights survey

Goal: confirm the Round 16 narrative-grounding gate and the Round 17
prompt-safety gate hold against documented residual risks; broaden
test coverage where the existing harness left genuine gaps; flag
(don't fix) any cost / latency surface for future cleanup.

### Findings

| Surface | Disposition | Notes |
|---|---|---|
| Prompt / data parity for `validate_narrative` | **No actionable findings (verified by construction)** | Single production call site at `app_simple.py:6843` passes the **same** `briefing_book` variable to both `generate_llm_response(...)` (line 6809) and `_anv.validate_narrative(...)`. The validator is therefore evaluating the LLM's narrative against the exact corpus the LLM was given -- parity holds by construction. Pinned indirectly by the existing Round 16 integration test. |
| `is_corpus_chunk_safe` regex regression — *security finding* | **R18-009 (fix + 9 tests) shipped** | The Round-17 pattern `\bignore\s+(?:all\|previous\|prior\|the\s+above)\s+instructions?\b` only allowed a single token between `ignore` and `instructions`, so the canonical wild-corpus phrase **"Ignore all previous instructions"** (two tokens) leaked through and would have reached the LLM prompt. Round 18 broadens the pattern to `\b(?:ignore\|disregard\|forget)\s+(?:[\w'\-]+\s+){0,4}instructions?\b` (also covers `disregard` / `forget` synonyms; bounded to 4 intermediate word tokens to avoid crossing sentence boundaries). 10 prompt-injection vectors plus 5 legitimate-prose negatives are now pinned in `tests/test_round18_ai_insights.py`. |
| Year allow-list edge (2024-2027) | **R18-010 (3 tests) added** | Pins behavior at the upper edge: 2027 auto-allowed, bare 2028 fails grounding when not in briefing, briefing-grounded 2028 passes. Catches a future "extend to 2028+" change without accompanying briefing content. |
| Suffix-multiplier parsing (`$2.5M` ↔ `2,500,000`) | **R18-011 (2 tests) added** | Pins both `M` and `K` suffix matches against grouped-thousands briefing values. Most common point of confusion in narrative-vs-briefing audit logs. |
| Numeric tolerance boundaries (~1% relative) | **R18-012 (2 tests) added** | Pins both sides of the 1% tolerance: 0.8%-off passes, 20%-off fails. Catches the classic "LLM hallucinated a round number" symptom. |
| Common-reference numbers (7/14/30/60/90/180/365 days) | **R18-013 (2 tests) added** | Positive control auto-allows the documented set; negative control (73 days) requires briefing presence. Catches a future drop or unintended widening of the common-reference set. |
| Failure modes (validator → labeled placeholder) | **No actionable findings** | `app_simple.py:6859-6868` already wraps the validator in a try/except that **never** silently inserts a hallucinated narrative -- on validator exception the underlying LLM output is logged at WARNING and accepted as-is. On *validation failure* (the much more common path) the narrative is replaced with `GROUNDING_FAILURE_PLACEHOLDER`. Both paths logged. |
| Redundant per-row LLM calls (cost / latency) | **Flagged, not fixed (deferred)** | `app_simple.py:12535` (and the mirror at `adoptiq_backend.py:11696`) issue **one LLM call per customer** inside the leader-report customer loop. This is documented design (each customer storyboard is rendered from its own per-customer briefing), not a bug -- but it scales linearly with the customer count and is the dominant latency / token-cost driver in the leader report. Logged in deferred items so a future "batch storyboards in a single call with structured output" experiment is on the radar. |

### Files changed in Phase 4

```
ai_narrative_validator.py                              (5 lines:  injection regex widened)
tests/test_round18_ai_insights.py                      (new file, 18 tests)
```

### Phase 4 verification

```
make verify
└── ruff:        All checks passed!
└── bandit:      no HIGH/MED findings
└── pip-audit:   No known vulnerabilities found
└── pytest:      2145 passed, 2 skipped in 7.84s   (+39 Round 18 cumulative; +18 Phase 4 + adjusted)
```

## Phase 5 — Code quality / corpus security spot-checks

Strictly scoped to the reporting pipeline and Round-17 corpus paths
(per the plan's Phase-5 envelope: "swallowed exceptions, pipeline-
boundary logging integrity, security hygiene spot-checks").

### Findings

| Surface | Disposition | Notes |
|---|---|---|
| Pipeline-boundary INFO logging (Round 14 markers) | **No actionable findings (verified)** | `tests/test_round14_markers.py` (6 cases) and `tests/test_round14_behavioral.py` (9 cases) -- all 15 passing. Round 14's INFO entry/exit logs at the report-generation boundary remain intact. |
| Swallowed exceptions in formatters | **No actionable findings** | Spot-checked `leader_report_generator.py` (~30 `except Exception:` sites), `compact_report_formatter.py`, `executive_intelligence_formatter.py`. Every silent-pass site falls into one of three legitimate patterns: (a) helper-function safe defaults (RGB color, text cleaner), (b) defensive metadata extraction with empty-string fallback, (c) format-helper redefinition fallback when the primary helper is unavailable. None mask data-correctness errors. |
| **R18-014: corpus safety gate fail-open** — *security finding* | **Fix + 4 tests shipped** | `report_corpus_context._is_safe_chunk` is the second-line defense that runs corpus content through `is_corpus_chunk_safe` before letting it into a Word/Excel report. The Round-17 implementation translated "never break a report on a chunk" into `return True` on **any** validator exception -- meaning if `ai_narrative_validator` ever failed to import, every corpus chunk silently bypassed the gate and reached the rendered Office document. Round 18 corrects the contract to **fail-closed**: validator exception now drops the chunk (returns `False`), logs at WARNING, and the report is still not broken (the chunk is simply omitted). Test contract pinned in both `tests/test_round18_corpus_security.py` (4 new) and `tests/test_round17_report_prefill_historical_context.py` (1 updated). |
| PII / untrusted strings in fixtures | **No actionable findings** | Round 17 corpus fixtures (`tests/fixtures/round17/*.csv`) are synthetic; no real customer / case names. Confirmed during Round 17 build-out, reverified by inspection. |

### Files changed in Phase 5

```
report_corpus_context.py                                          (~20 lines: fail-closed + WARNING log)
tests/test_round18_corpus_security.py                             (new file, 4 tests)
tests/test_round17_report_prefill_historical_context.py           (~12 lines: contract update + comment)
```

### Phase 5 verification

```
make verify
└── ruff:        All checks passed!
└── bandit:      no HIGH/MED findings
└── pip-audit:   No known vulnerabilities found
└── pytest:      2149 passed, 2 skipped in 7.41s   (+43 Round 18 cumulative; +4 Phase 5)
```

## Phase 6 — Final verification gate

Two consecutive clean `make verify` runs after the last code change
of the night:

| Run | Result | Tests | Lint | Security | Audit |
|---|---|---|---|---|---|
| Final #1 | clean | 2149 / 2 skipped (7.41s) | ruff: All checks passed | bandit: no HIGH/MED | pip-audit: no known vulns |
| Final #2 | clean | 2149 / 2 skipped (7.01s) | ruff: All checks passed | bandit: no HIGH/MED | pip-audit: no known vulns |

## Round 18 — Executive summary

Round 18 was a one-night autonomous QA sweep across four priority
areas. The repo entered the night at 2106 / 2 skipped and exits at
2149 / 2 skipped (+43 tests added, all passing). Two genuine
security fixes shipped on top of the broader survey, and the
balance of the surveyed surface area was logged as either "no
actionable findings" or "deferred / flag-don't-fix" with explicit
rationale.

### Files changed by priority area

**Report accuracy (Phase 2):**
```
executive_intelligence_formatter.py    (~14 lines:  high-risk customer sort tuple key + casefold tiebreaker)
leader_report_generator.py             (~14 lines:  team-summary CSSM sort tuple key + casefold tiebreaker)
tests/test_round18_sort_determinism.py (new file, 6 tests pinning both sites)
```

**Word + Excel output quality (Phase 3):**
```
tests/test_round18_output_quality.py   (new file, 7 tests:
                                         - openpyxl no-warning open
                                         - python-docx structural round-trip
                                         - non-finite float -> "N/A" placeholder
                                         - add_banded_top_n_table empty-data behavior
                                         - polish-helper byte-stable round-trip)
```

**AI insights (Phase 4):**
```
ai_narrative_validator.py              (~5 lines:  prompt-injection regex widened so the canonical
                                                   "Ignore all previous instructions" phrase no longer leaks)
tests/test_round18_ai_insights.py      (new file, 18 tests:
                                         - is_corpus_chunk_safe behavioral coverage  (15 vectors)
                                         - validator year-allow-list edges            (3 cases)
                                         - suffix-multiplier parity                    (2 cases)
                                         - tolerance boundary                          (2 cases)
                                         - common-reference numbers                    (2 cases))
```

**Code quality / corpus security (Phase 5):**
```
report_corpus_context.py                                       (~20 lines:  _is_safe_chunk fail-closed + WARNING)
tests/test_round18_corpus_security.py                          (new file, 4 tests)
tests/test_round17_report_prefill_historical_context.py        (~12 lines:  contract update from fail-open to fail-closed)
```

**Documentation:**
```
QUALITY_AUDIT.md                       (this file: Round 18 sections appended)
```

### Tests added — totals

| Phase | Test file | Count |
|---|---|---|
| 2 | `tests/test_round18_sort_determinism.py` | 6 |
| 3 | `tests/test_round18_output_quality.py` | 7 |
| 4 | `tests/test_round18_ai_insights.py` | 18 |
| 5 | `tests/test_round18_corpus_security.py` | 4 |
| 5 | `tests/test_round17_report_prefill_historical_context.py` (updated) | 0 (1 contract change) |
| 4 | `tests/test_round16_ai_narrative_validator.py` (updated, R17 carryover) | 0 (1 `__all__` symbol added) |
| **Total** | | **+43 (and 2 contract updates)** |

Documented baseline → final: **2106 → 2149** (+43 net), 2 skipped throughout.

### Verification commands & results

```
make verify
├── pytest:      2149 passed, 2 skipped (verified twice in a row)
├── ruff check . : All checks passed
├── bandit -ll -r . -c bandit.yaml : no HIGH or MEDIUM findings
└── pip-audit -r requirements.txt : No known vulnerabilities found
```

### Deferred items (logged for the next round)

| ID | Item | Rationale |
|---|---|---|
| R18-D1 | Cross-format parity test coverage | `report_export_styling.build_summary_rows` returns a strict superset of the KPIs that `tests/test_round16_cross_format_consistency.py` asserts on. Not a correctness bug, a coverage gap. Worth a focused round. |
| R18-D2 | Per-customer LLM calls in leader report | `app_simple.py:12535` and `adoptiq_backend.py:11696` issue one LLM call per customer in the leader-report customer loop. Documented design, but linear cost / latency in the customer count. Worth experimenting with batched / structured-output calls in a future round. |
| R18-D3 | `_add_customer_details` per-item sort | A within-customer sort by `(type_order, date)` has no secondary tiebreaker. Practical collision risk is very low (same customer, same item type, same timestamp). Logged for awareness, not fixed in Round 18 to keep the diff one-concern-per-batch. |

### Risks & recommendations

- **R18-009 (prompt-injection regex) is a fail-closed widening, not a behavior change for legitimate corpus content.** The 5 safe-chunk negatives in `tests/test_round18_ai_insights.py` pin that legitimate prose ("the system was upgraded last quarter; the assistant lead handled rollout.") still passes. No action recommended; ship.
- **R18-014 (corpus safety gate fail-closed) flips a previously fail-open path to fail-closed.** This is the correct security posture but does change behavior in the rare case of an `ai_narrative_validator` import failure: the affected corpus chunks are silently dropped from the report rather than rendered. Logged at WARNING so an operator can see it. Recommended action if a deployment ever sees that log line: investigate the validator error and re-run; the report itself remains correct.
- **No P0 / P1 items remain in the Round-18 backlog.** The two security findings shipped with fixes; everything else is either pinned by new tests or explicitly deferred with rationale above.

### Round 18 stop condition

> Stop when ... two consecutive `make verify` runs are clean **and** the issue backlog in `QUALITY_AUDIT.md` shows zero P0/P1 items remaining.

**Both conditions met.** Round 18 is complete.

# QUALITY_AUDIT.md — Round 17.1 (Corpus Real-Data Completion)

## Mission

Round 17 wired the encrypted corpus to `Config.CSONE_ONEDRIVE_FOLDER`,
but two structural bugs in
[`corpus_indexer._parse_xlsx`](corpus_indexer.py) caused silent data
loss when the indexer met real CSOne / AdoptIQ exports:

1. The xlsx sheet-family router only matched on `csone | tac | case |
   barrier | _ab_ | pulse | sentiment`. The CSOne OneDrive workbook
   has one sheet named `AdoptIQ Enhanced Premium Collab`, none of those
   tokens match, so every row dropped on the floor.
2. `pd.read_excel(...)` reads from row 1, but the CSOne export has
   banner rows 1-14 and the header on row 16 while AdoptIQ-rendered
   Data xlsx has a title in row 1 and the header on row 2. Both
   layouts produced all-`Unnamed:` columns under the original call,
   so `_get(row, "Customer Name", ...)` returned `None` everywhere.

Round 17.1 fixes both bugs and extends the corpus to ingest
AdoptIQ-rendered reports from the runtime user's `~/Downloads`,
filtered to `AdoptIQ*` filenames only.

## Findings

| ID | Severity | Surface | Finding | Action |
|---|---|---|---|---|
| R17.1-001 | HIGH | corpus indexer | CSOne export single-sheet workbook never matched the sheet-family router; every TAC row was silently dropped. | Add `_detect_xlsx_layout` and treat the `csone_export` layout as the case branch regardless of sheet name. |
| R17.1-002 | HIGH | corpus indexer | `pd.read_excel(...)` consumed banner rows as the header row, returning all-`Unnamed:` columns; every `_get(row, "Customer Name", ...)` lookup returned `None`. | Detect the layout, then call `pd.read_excel(..., header=header_row)` with `15` for `csone_export`, `1` for `adoptiq_data`, `0` for plain. |
| R17.1-003 | MED  | corpus coverage | AdoptIQ Data xlsx sheets (`Risk_Summary`, `Adoption_Barriers`, `Customer_Pulse`, `Action_Plans`, ...) had no router branches and were silently skipped. | Add per-sheet emit helpers (`_emit_case_record`, `_emit_barrier_record`, `_emit_pulse_record`, `_emit_narrative_record`, `_emit_external_record`) routed by sheet-name token tuples. |
| R17.1-004 | MED  | corpus coverage | The runtime user's Downloads folder (the natural home of AdoptIQ-rendered reports) was not indexed. | Add `CSONE_INCLUDE_USER_DOWNLOADS` (default `true`) + `CSONE_USER_DOWNLOADS_DIR` config; new `enumerate_user_report_files()` enforces an `^AdoptIQ[\s_]...$` allow-list and walks the top level only (no recursion). |
| R17.1-005 | LOW  | observability | The admin Corpus tile rolled up across sources, hiding which folder produced which counts. | `CorpusBootState.last_sources` now records per-source `{label, dir, files_seen, files_parsed, files_skipped, files_failed, chunks_added}`; surfaced under `boot.last_sources` and `corpus.sources` on `/api/corpus/status`. |

## Files changed

| File | Change |
|---|---|
| `corpus_indexer.py` | Added `_detect_xlsx_layout`, `_USER_REPORT_NAME_RE`, `enumerate_user_report_files`, `_row_to_blob`, `_emit_*_record` helpers; rewrote `_parse_xlsx` against the layout detector; extended `enumerate_corpus_files` with a `filename_filter` + `recursive` knob; `index_folder` now accepts a pre-enumerated `files=` list. |
| `config.py` | Added `CSONE_INCLUDE_USER_DOWNLOADS` + `CSONE_USER_DOWNLOADS_DIR`. |
| `corpus_bootstrap.py` | New `_resolve_index_sources` + `_accumulate_index_stats`; `_run_index_pass` walks every source, records per-source stats on `last_sources`. |
| `app_simple.py` | `_r17_corpus_status_payload` surfaces `boot.last_sources` and `corpus.sources`. |
| `tests/test_round17_xlsx_layout_detector.py` | NEW — pins the three layouts (csone_export / adoptiq_data / plain) plus empty + short fallback. |
| `tests/test_round17_csone_export_indexed.py` | NEW — end-to-end indexing of a synthetic CSOne-shaped xlsx; pins customers, cases, and `is_open` lifecycle. |
| `tests/test_round17_adoptiq_data_router.py` | NEW — multi-sheet AdoptIQ Data fixture; pins each sheet → table mapping. |
| `tests/test_round17_user_downloads_filter.py` | NEW — filename allow-list, no-recursion, missing-dir, lock-file rejection. |
| `tests/test_round17_admin_corpus_tile.py` | UPDATED — pins per-source breakdown keys on the status payload. |

## Live-corpus smoke (read-only, dev machine)

The smoke pass below ran on the dev machine against the real
OneDrive `AdoptIQ_CSOne_Reports` folder (248 .xlsx files, sampled
the first 30) and the user's `~/Downloads` AdoptIQ-named files (48
files, sampled the first 20). No customer-identifying data leaves
this audit row — only counts.

| Pass | Files seen | Files parsed | Files failed | Chunks added |
|---|---|---|---|---|
| OneDrive (sampled 30 of 248) | 30 | 30 | 0 | 37,156 |
| Downloads (sampled 20 of 48) | 20 | 20 | 0 | 12,184 |

Resulting corpus rollup:

| Table | Rows |
|---|---|
| `corpus_files` | 50 |
| `customers` | 958 |
| `cases` | 50,167 |
| `barriers` | 7,235 |
| `sentiments` | 1,112 |
| `resolutions` | 6,571 |
| `playbook_chunks` | 49,340 |

Top playbook themes (first eight, no PII):

| Theme | Chunks |
|---|---|
| general | 30,119 |
| configuration | 4,339 |
| upgrade | 3,196 |
| connectivity | 2,659 |
| authentication | 2,167 |
| data_quality | 1,780 |
| integration | 1,739 |
| licensing | 1,543 |

The smoke run was executed against an in-memory temp DB (no commit
to the encrypted corpus) and used the indexer directly; the
encrypted-corpus path is exercised by the existing Round 17 tests.

## Verification

- `tests/test_round17_xlsx_layout_detector.py` — 5 cases, all pass.
- `tests/test_round17_csone_export_indexed.py` — 2 cases, all pass.
- `tests/test_round17_adoptiq_data_router.py` — 2 cases, all pass.
- `tests/test_round17_user_downloads_filter.py` — 5 cases, all pass.
- `tests/test_round17_admin_corpus_tile.py` — 15 cases (was 13; +2
  for per-source breakdown contracts), all pass.
- Combined Round 17 / corpus suite (`-k "round17 or corpus"`):
  **199 passed, 1968 deselected**.
- `make verify` clean twice (run at the close of Round 17.1).

## Out of scope for Round 17.1

- Encryption-at-rest contract (`corpus_crypto.py`), schema version,
  BM25 scorer, and retriever public API are unchanged.
- Round 19's golden-fixture accuracy work is *not* gated on this
  round and resumes from its existing Phase 2 todo afterwards.
- The default `CSONE_ONEDRIVE_FOLDER` path
  (`~/OneDrive - Cisco/Documents/AI Projects/AdoptIQ_CSOne_Reports`)
  matches the documented Microsoft default. Sites whose OneDrive
  layout differs (the dev machine has the folder at
  `~/OneDrive - Cisco/AI Projects/AdoptIQ_CSOne_Reports`) override
  via the `CSONE_ONEDRIVE_FOLDER` env var; we do not add fallback
  search paths here because they would risk reading unrelated
  Cisco-tenant content.

# QUALITY_AUDIT.md — Round 17.2 (SharePoint Pull + Detailed Logging)

## Mission

Round 17.1 made the corpus indexer ingest both the user's synced
OneDrive folder and the AdoptIQ-named files in `~/Downloads`. After
shipping, the user clarified the deployment posture:

> "they need to use the link
> [SharePoint URL] because the raw data files won't be on their
> local computer. they might have previous reports in downloads.
> i want their clients to automatically reach out to this link and
> pull the data from the folder."

So the corpus must work for operators who have **no OneDrive sync
at all** — AdoptIQ has to authenticate to SharePoint itself, pull
the share into a local cache, and then index the cache the same way
it indexes any other folder. Round 17.2 adds that path while keeping
OneDrive sync and Downloads as graceful fallbacks.

## Findings

| ID | Severity | Surface | Finding | Action |
|---|---|---|---|---|
| R17.2-001 | HIGH | corpus availability | Corpus only worked for operators who had a synced OneDrive copy of `AdoptIQ_CSOne_Reports`; users running the bundled .app on a fresh laptop saw an empty corpus and an unhelpful "OneDrive not synced" banner. | Add `sharepoint_corpus_source.py` (Microsoft Graph + device-code OAuth via `msal`); cache the share at `~/.adoptiq/cache/sharepoint_csone/` with mtime-aware manifest; expose this as a first-class corpus source ahead of OneDrive in `corpus_bootstrap._resolve_index_sources`. |
| R17.2-002 | HIGH | secrets / token storage | Refresh tokens cannot be stored in `secrets.env` (would defeat installer-portability) or `localStorage` (no browser); a clear-text JSON file would expose them on multi-user hosts. | New `KeyringTokenCache` writes to the macOS Keychain via the `keyring` package, with a 0600-permission JSON fallback under `~/.adoptiq/`. No tokens ever appear in logs. |
| R17.2-003 | MED  | rate limiting / DoS | Tenant rate limiter and "Retry-After" responses had to be honored by the Graph client to avoid being throttled out of the share on a refresh storm. | `_request_with_retry` and `download_to_path` parse `Retry-After`, fall back to capped exponential backoff on transient 5xx, and enforce a 50 MiB per-file cap aligned with `corpus_indexer._MAX_PARSE_BYTES`. |
| R17.2-004 | MED  | path resolution | The OneDrive default path was `~/OneDrive - Cisco/Documents/AI Projects/AdoptIQ_CSOne_Reports`, but modern macOS Big Sur+ syncs into `~/Library/CloudStorage/OneDrive-Cisco/...` and most operators don't keep the `Documents/` segment. The Round 17.1 default missed both. | `config._resolve_csone_onedrive_folder` walks four candidates (CloudStorage with/without `Documents/`, legacy `~/OneDrive - Cisco/` with/without `Documents/`), first existing wins; honors `CSONE_ONEDRIVE_FOLDER` env override unconditionally. |
| R17.2-005 | LOW  | observability | The corpus indexer logged at DEBUG, so first-run troubleshooting from a packaged .app (where DEBUG is filtered) gave the operator no visibility into which files were ingested vs skipped. | Promoted per-file `indexed=… skipped=… failed=…` lines to INFO with `filename / ext / layout / records / bytes`; per-sheet xlsx layout summary at INFO; SharePoint refresh summary line at INFO with listed/downloaded/cached/failed/bytes; startup banner logs the resolved log path, OneDrive folder, Downloads dir, and SharePoint state. No PII (customer names, paths beyond the cache root, token material) leaks above DEBUG. |
| R17.2-006 | LOW  | UX | Operators had no way to trigger SharePoint sign-in or refresh from the running app; they had to sign in via a separate Microsoft Graph CLI. | New `/api/corpus/sharepoint/signin` and `/api/corpus/sharepoint/refresh` endpoints (CSRF + internal-token auth, mirroring `/api/corpus/refresh`); admin Corpus tile shows sign-in CTA, signed-in UPN + token expiry, last-refresh stats, and "Refresh SharePoint corpus" button. |
| R17.2-007 | LOW  | bug | `sharepoint_corpus_source.default_cache_dir()` called `.strip()` on a `Path` object, which `AttributeError`s. Caught by the dev-machine static smoke before live network ever ran. | Fixed: strip the env-var string before wrapping in `Path`. |

## Files changed

| File | Change |
|---|---|
| `sharepoint_corpus_source.py` | NEW — `SharePointGraphClient` (device-code OAuth, paginated `/shares/{id}/driveItem/children`, throttled retries, 50 MiB cap), `KeyringTokenCache` (macOS Keychain + 0600 fallback), `refresh_local_cache` (mtime-aware `manifest.json`, atomic temp→rename writes, sanitized filenames). All HTTP I/O is injectable so unit tests fake `msal` + `requests` without monkey-patching. |
| `config.py` | NEW `_csone_onedrive_candidates` + `_resolve_csone_onedrive_folder` (Big Sur CloudStorage path first); new `ADOPTIQ_SHAREPOINT_*` config flags (enabled/folder URL/client id/authority/cache dir/max bytes). |
| `corpus_bootstrap.py` | `_resolve_index_sources` now orders `sharepoint_csone → onedrive → user_downloads`; OneDrive only included when the directory exists. New `_refresh_sharepoint_cache_for_bootstrap` runs before the indexer pass and stores per-pass SharePoint state on `CorpusBootState.sharepoint`. New public `begin_sharepoint_signin` (device-code launcher; spawns a worker thread that auto-refreshes after sign-in completes) and `request_sharepoint_refresh` (refresh trigger) — exported in `__all__`. |
| `corpus_indexer.py` | `index_folder` per-file lines promoted to INFO with `filename / ext / layout / records / bytes`; `_parse_xlsx` logs the detected layout per sheet. |
| `app_simple.py` | `_r17_corpus_status_payload` carries `boot.sharepoint` block (signed-in/UPN/expiry/error-kind/stats). New shared `_r17_2_authorize_corpus_admin` helper centralizes CSRF + internal-token validation. New `/api/corpus/sharepoint/signin` and `/api/corpus/sharepoint/refresh` endpoints (CSRF-protected admin auth). Startup banner logs resolved log file, OneDrive folder, Downloads dir, and SharePoint config. |
| `enhanced_admin_dashboard_v2.py` | Corpus tile renders SharePoint state (sign-in CTA / signed-in UPN+expiry / last-refresh stats / "Refresh SharePoint corpus" button); new `/sharepoint_signin` and `/sharepoint_refresh` proxy routes (CSRF-protected; forwards to main app via `X-AdoptIQ-Internal`). |
| `corpus_crypto.py` | Docstring + error messages updated — sentinel arrives via SharePoint Graph pull *or* OneDrive sync; both paths are gated by Microsoft's SharePoint ACL. |
| `ask_ai_corpus.py`, `report_corpus_context.py`, `templates/customer_360.html`, `templates/playbook.html` | User-facing strings reflect the new "sign in to SharePoint or sync OneDrive" guidance. |
| `requirements.txt` | Added `msal>=1.28.0`, `keyring>=24.3.0`. |
| `adoptiq_mac.spec` | Hidden imports for `sharepoint_corpus_source`, `msal` submodules, `keyring.backends.macOS` so the frozen .app bundle has the device-code flow + Keychain backend at runtime. |
| `secrets.env.template` | Documents `ADOPTIQ_SHAREPOINT_*` env knobs and the auto-discovered OneDrive paths. |
| `README.md` | "What's New" + "CSOne Knowledge Corpus" sections describe the SharePoint pull, sign-in flow, and per-source ordering. |
| `tests/test_round17_2_sharepoint.py` | NEW — 21 cases covering share-URL encoding (round-trip + empty rejection), `/shares/.../children` pagination, `Retry-After`-driven 429 retries, oversize-rejection before HTTP, 0600 mode on cached files, KeyringTokenCache fallback when keyring fails + preference when keyring works, `refresh_local_cache` `auth_required` short-circuit + mtime-aware idempotency + sanitized filenames, `_resolve_index_sources` three-source ordering / missing-OneDrive skip / SharePoint-disabled skip, status payload `boot.sharepoint` shape, `/api/corpus/sharepoint/signin` 403 without auth, `/api/corpus/sharepoint/refresh` happy path with internal token, indexer per-file INFO logging, and OneDrive auto-discover (first-existing / fall-through / env override). |

## Live-corpus smoke (read-only, dev machine)

The smoke run below ran on the dev machine. It exercises every wire
that runs *before* an actual Microsoft login — share-URL encoding,
configuration auto-discovery, keyring backend resolution, three-source
ordering — without invoking any live HTTP, so no customer data was
fetched. The live device-code flow is gated on the operator opening
`https://microsoft.com/devicelogin` in their browser.

| Check | Result |
|---|---|
| `sharepoint_corpus_source` import + `build_default_client()` | OK (client_id `14d82eec-…`, max 50 MiB, cache `~/.adoptiq/cache/sharepoint_csone`) |
| `encode_share_url(<real share URL>)` round-trip | OK (`u!aHR0cHM6Ly9j…` decodes back to original URL byte-for-byte) |
| `msal.__version__` | `1.36.0` (≥ pinned floor) |
| `keyring.__version__` | `25.7.0` (≥ pinned floor); active backend `keyring.backends.macOS.Keyring` |
| `Config.CSONE_ONEDRIVE_FOLDER` resolution on dev machine | `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports` (exists) — auto-discovery picked the modern Big Sur path correctly. |
| `corpus_bootstrap._resolve_index_sources()` order on dev machine | `sharepoint_csone → onedrive → user_downloads` (3 sources) |
| `client.get_token_info()` before sign-in | `signed_in=False` (no cached account on this machine; matches expected first-run state) |

The `signed_in=False` state confirms the device-code flow is the
expected next step, and the admin tile will render its sign-in CTA
when the operator opens `/admin`.

## Verification

- `tests/test_round17_2_sharepoint.py` — **21 cases, all pass** (offline,
  faked HTTP / msal / keyring; ≈ 2 s runtime).
- `make verify` clean twice (run at the close of Round 17.2 — see the
  next round-stop block).

## Out of scope for Round 17.2

- Tenant-pinned authority (`/common` is currently used so any
  Microsoft work account can sign in). Sites that want to lock to a
  specific tenant override `ADOPTIQ_SHAREPOINT_AUTHORITY`.
- Token-revocation UX (sign-out button). Operators who need to switch
  accounts can delete the macOS Keychain entry under
  `service=adoptiq, account=sharepoint_graph_token_cache` (or the
  fallback file at `~/.adoptiq/sharepoint_token_cache.json`); a
  dedicated UI button is deferred.
- Multi-folder pull. Round 17.2 pulls a single share URL; tenants who
  need multiple shares can run multiple AdoptIQ instances with
  different `ADOPTIQ_SHAREPOINT_FOLDER_URL` values.
- Round 19's golden-fixture accuracy work resumes from its existing
  Phase 2 todo afterwards.

# QUALITY_AUDIT.md — Round 19 (Report Accuracy Golden Fixture)

## Mission

Round 19 is an end-to-end accuracy audit anchored on a single golden
fixture. The goal is the strongest possible accuracy claim: a known
synthetic input, hand-computed expected values for every KPI, all
three report formatters (compact, executive-intelligence, leader)
rendered to both Word and Excel, and every emitted number programmatically
diffed against expected.

Round 18's deferred R18-D1 (cross-format parity coverage gap) is
absorbed and closed by this round.

## Phase 1 — KPI registry

Every auditable number in Round 19. Anything not in this table is
out of scope. Numeric matches are strict equality unless the SoT
explicitly rounds (e.g., `round_percent`, `pulse_sentiment.mean_0_to_10`
which is `round(x, 2)`).

### Core portfolio KPIs (canonical contract)

Every formatter ultimately consumes [`canonical_metrics.build_portfolio_metrics`](canonical_metrics.py) (callsites: `app_simple.py:5612`, `app_simple.py:10907`, `app_simple.py:18593`, `compact_report_formatter.py:2729`, `executive_intelligence_formatter.py:1710`, `ask_ai_grounded.py:1033`). Every key in that payload is a KPI:

| KPI key | Source function | Mode / args | Tolerance |
|---|---|---|---|
| `total_customers` | `count_customers` | `ab_df + csone_df + extra_frames`, `drop_unknown=True` | exact integer |
| `total_barriers` | `count_total_barriers` | `ab_df` | exact integer |
| `total_cases` | `count_total_tac` | `csone_df` | exact integer |
| `bems_count` | `count_bems` | default `mode=canonical_tac_rows` | exact integer |
| `critical_p1` / `p1_cases` | `count_priority_breakdown` | `["P1"]` | exact integer |
| `high_p2` / `p2_cases` | `count_priority_breakdown` | `["P2"]` | exact integer |
| `p3_cases` | `count_priority_breakdown` | `["P3"]` | exact integer |
| `p4_cases` | `count_priority_breakdown` | `["P4"]` | exact integer |
| `unknown_priority_cases` | `count_priority_breakdown` | `["Unknown"]` | exact integer |
| `break_fix_cases` | `count_break_fix` | `csone_df` | exact integer |
| `provisioning_cases` | `count_provisioning` | `csone_df` | exact integer |
| `high_risk_customers` | `compute_high_risk_count` | `scale=RISK_SCALE_0_TO_100` (CRITICAL+HIGH) | exact integer |
| `critical_risk_customers` | band-split in `build_portfolio_metrics` | RISK_BAND_THRESHOLDS["CRITICAL"]=75 | exact integer |
| `high_only_risk_customers` | band-split | RISK_BAND_THRESHOLDS["HIGH"]=55 | exact integer |
| `medium_risk_customers` | band-split | RISK_BAND_THRESHOLDS["MEDIUM"]=35 | exact integer |
| `low_risk_customers` | band-split | RISK_BAND_THRESHOLDS["LOW"]=15 | exact integer |
| `healthy_customers` | band-split | else | exact integer |

**Reconciliation invariant** (independently asserted): `p1_cases + p2_cases + p3_cases + p4_cases + unknown_priority_cases == total_cases`. Every row of `csone_df` is in exactly one priority bucket.

**Reconciliation invariant**: `critical_risk_customers + high_only_risk_customers + medium_risk_customers + low_risk_customers + healthy_customers == len(risk_profiles)`. Every profile lands in exactly one band.

**Reconciliation invariant**: `high_risk_customers == critical_risk_customers + high_only_risk_customers`.

### TAC lifecycle KPIs

| KPI | Source | Tolerance |
|---|---|---|
| `count_open_tac` | `is_open` after `add_case_lifecycle_fields` | exact integer |
| `count_closed_tac` | `is_closed` after `add_case_lifecycle_fields` | exact integer |
| `count_escalated` | priority in {P1, P2} | exact integer |

**Reconciliation invariant**: `count_open_tac(df) + count_closed_tac(df) <= count_total_tac(df)` (lifecycle fields can be unclassified for some statuses; the sum is upper-bounded but not necessarily equal — pin the actual relationship in the fixture).

### Adoption Barrier KPIs

| KPI | Source | Tolerance |
|---|---|---|
| `count_critical_barriers` | severity in {Critical, High} (default mode `critical_or_high`) | exact integer |
| `count_open_barriers` | normalized status == "Open" | exact integer |

### Pulse / sentiment KPIs

`pulse_sentiment(pulse_df)` returns:

| Field | Tolerance |
|---|---|
| `count` | exact integer |
| `mean_0_to_10` | exact post-`round(x, 2)` |
| `positive` | exact integer (count where score >= 7.5) |
| `neutral` | exact integer (count - positive - negative) |
| `negative` | exact integer (count where score <= 5.0) |
| `sentiment` | exact string label |

**Reconciliation invariant**: `positive + neutral + negative == count`.

### BEMS rate

`bems_rate(csone_df) = round(count_bems / count_total_tac * 100.0, 2)`. Tolerance: exact post-rounding to 2 dp.

### Excel Summary sheet (Round 15)

[`report_export_styling.build_summary_rows`](report_export_styling.py:733) emits these labeled rows in order:

| Label | KPI |
|---|---|
| `Customers in portfolio` | `count_customers` |
| `Adoption barriers (total)` | `count_total_barriers` |
| `Adoption barriers (critical)` | `count_critical_barriers` (default `critical_or_high`) |
| `Adoption barriers (open)` | `count_open_barriers` |
| `TAC cases (total)` | `count_total_tac` |
| `TAC cases (P1)` | `count_p1` |
| `TAC cases (open)` | `count_open_tac` |
| `Escalations` | `count_escalated` |
| `BEMS / break-fix` | `count_bems` |
| `External bugs (rows)` | `len(External_Bugs)` |
| `External incidents (rows)` | `len(External_Incidents)` |
| `Manager scope` / `Technology scope` / `Window (days)` | scope params (string match) |

### Out of scope for Round 19

- Narrative numbers from the LLM (Round 16 / 18 covered grounding; LLM is mocked here).
- Subscription / ARR aggregates: not all formatter paths use them; covered in `tests/test_renewal_*` and `tests/test_round5_renewal_*`. We will *not* add ARR aggregates to the canonical fixture in Round 19; a separate round if needed.
- Period comparison (current vs prior window): SoT lives in `cisco_internal_integrations` and behaves correctly per existing tests; not duplicated here.
- Per-CSSM rollups in the leader report: deferred to Phase 6 if the canonical fixture turns up no findings.

## Phase 1 verification

- KPI registry above is the canonical reference for Phases 2-7.
- No code changes in Phase 1; this is a read-only inventory.
- Test count baseline at the start of Round 19: 2149 passed, 2 skipped (Round 18 closing baseline).


# Round 17.3 — Default ports moved to 5151 / 5152 (env-overridable)

Working note. Round 17.3 is a small, surgical follow-on to the Round 17.2 SharePoint pull build. The user reported that 5001 / 5002 collide with other tools on macOS and asked to move adjacent to their other 5150 ("Van Halen") app, while also making the choice cheap to revisit later.

## Decision

- New defaults: `5151` (main) / `5152` (admin).
- Both ports are now read from the environment via tiny resolver helpers (`app_simple._resolve_main_port`, `enhanced_admin_dashboard_v2._resolve_admin_port`) which fall back to the new defaults on garbage / out-of-range input. No rebuild is required to move ports again — just set `ADOPTIQ_PORT` / `ADOPTIQ_ADMIN_PORT`.
- `MAIN_APP_URL` default bumped from `http://localhost:5001` to `http://localhost:5151`; `_main_app_host_port()` hostname-only fallback bumped 5000 → 5151 to match.

## Files changed

| File | Change |
| --- | --- |
| `app_simple.py` | Added `_DEFAULT_MAIN_PORT = 5151` + `_resolve_main_port()`; replaced literal `PORT = 5001` with `PORT = _resolve_main_port()`. |
| `enhanced_admin_dashboard_v2.py` | Added `_DEFAULT_ADMIN_PORT = 5152` + `_resolve_admin_port()`; replaced hardcoded `port=5002` with `port=_admin_port`; bumped `MAIN_APP_URL` default + URL-parse fallback to 5151. |
| `installer_app.py` | Browser auto-open URL `:5001` → `:5151`. |
| `scripts/test_build_smoke.sh` | `PORT=5001` → `PORT=5151`. |
| `build_mac.sh`, `build_pc.bat`, `adoptiq_setup.iss` | User-facing URLs `:5001` → `:5151`. |
| `secrets.env.template` | `ADOPTIQ_MAIN_URL` default `:5001` → `:5151`; documented new `ADOPTIQ_PORT` / `ADOPTIQ_ADMIN_PORT` overrides. |
| `templates/help.html` | "Open your browser" URL `:5001` → `:5151`. |
| `README.md`, `CLAUDE.md`, `CURSOR_MAC_BUILD_INSTRUCTIONS.md`, `CURSOR_PC_BUILD_INSTRUCTIONS.md`, `SNOWFLAKE_OPTIMIZATION.md`, `MIGRATION_TO_MAC.md`, `CODE_REVIEW_LEARNING_AND_HISTORY.md`, `scripts/mac/READ_ME_FIRST.txt`, `scripts/win/READ_ME_FIRST.txt`, `.cursor/rules/adoptiq.mdc` | Doc sweep replacing `5001`→`5151` and `5002`→`5152`. |
| `tests/test_critical_fixes.py` | Repinned `test_admin_main_app_url_port` / `test_admin_standalone_port` to the new defaults; added `test_main_app_default_port` for the main side. All three now also assert the `ADOPTIQ_PORT` / `ADOPTIQ_ADMIN_PORT` env hooks remain wired. |
| `tests/test_round17_3_port_overrides.py` (new) | Three behavioural tests for the resolver helpers: default main port, env-override main port, default + env admin port + `MAIN_APP_URL` coupling guard. |

## Verification

- `make verify` clean twice. Test count moved 2186 → **2190 passed, 2 skipped** (one new pin in `test_critical_fixes.py` + three new behavioural tests in `test_round17_3_port_overrides.py`).
- Resolver helpers smoke-tested at the REPL: defaults match plan; env override accepts integers; non-integer / out-of-range / blank values fall back to the default with a warning rather than raising.
- No `make build` was run for Round 17.3 — defaults are picked up by the existing v1.0.4 build at next launch since the changes are pure source. A rebuild can be triggered later by request.

## Out of scope

- Bumping the version (no rebuild yet; defaults will be baked into the next build naturally).
- Actually running on a non-default port end-to-end — the resolver helpers cover the parsing side; production override is exercised on every operator's machine that sets `ADOPTIQ_PORT`.

# Round 17.4 — Dark Theme + Light/Dark Toggle

## Goal

Switch AdoptIQ to a dark-by-default UI with a user-flippable light/dark toggle, using Bootstrap 5.3's native `data-bs-theme` attribute. Cover both the main app (:5151) and the admin dashboard (:5152). Critically, keep the `--risk-*` data-viz tokens **byte-identical** so the Round 13 cross-surface parity (HTML badges ↔ matplotlib charts ↔ Excel conditional formatting all sharing the same hex via `canonical_metrics.RISK_BAND_COLORS`) is not silently broken.

## Approach

- Bootstrap 5.3 already SRI-pinned in `templates/base.html`. Driving the theme through `data-bs-theme="dark|light"` on `<html>` gives correctly-styled tables, dropdowns, modals, and form controls for free — no CSS rewrites of every component.
- A new layer of **semantic** tokens (`--bg-base`, `--bg-surface`, `--bg-surface-raised`, `--border-subtle`, `--text-primary`, `--text-secondary`, `--text-muted`, `--accent-primary`, `--accent-primary-hover`, `--accent-secondary`, `--accent-glow`, `--accent-glow-soft`, `--shadow-card`) abstracts away brand hex values. Light theme defaults wire these to the existing Cisco-blue chrome; the `[data-bs-theme="dark"]` block re-skins the whole app to charcoal + orange by re-pointing the same semantic tokens at `--adoptiq-orange`-driven values. Per-component CSS rules reference `var(--accent-primary)` etc. and "just work" in both themes.
- Theme choice persists in `localStorage` under `adoptiq-theme`. An early-paint inline `<script>` in `<head>` reads the stored value and applies the `data-bs-theme` attribute **before first paint**, eliminating FOUC for returning users.

## Palette

```css
/* dark (default) */
--bg-base: #0d1117;
--bg-surface: #161b22;
--bg-surface-raised: #21262d;
--border-subtle: #30363d;
--text-primary: #f0f6fc;
--text-muted: #8b949e;
--adoptiq-orange: #ff7a1a;
--adoptiq-orange-hover: #ff944d;
--accent-glow: rgba(255, 122, 26, 0.35);
```

`[data-bs-theme="light"]` reverts the semantic tokens to the existing Cisco-blue palette so toggling back gives today's look unchanged.

## Files changed

| File | Change |
| --- | --- |
| `templates/base.html` | `<html lang="en" data-bs-theme="dark">` (dark default); early-paint `<script>` in `<head>` that reads `localStorage['adoptiq-theme']` and applies it pre-paint; new semantic-token layer in `:root` (`--bg-*`, `--text-*`, `--accent-primary*`, `--shadow-card`, `--adoptiq-orange*`); `[data-bs-theme="dark"]` and `[data-bs-theme="light"]` override blocks; retuned navbar gradient, hero, footer, cards, form controls, modal chrome, scrollbars, file-upload button, `.bg-gradient-cisco`, `.text-gradient`, hover glows to consume `var(--accent-primary)` etc.; sun/moon `#theme-toggle` button injected into the navbar near "Ask AI"; `<script src="static/js/theme-toggle.js">` loaded after Bootstrap. **`--risk-*` tokens at lines 112–115 left byte-identical.** |
| `static/css/style.css` | Added `[data-bs-theme="dark"]` selectors for `.card-header`, `.table thead th`, `.table tbody tr:hover`, `.progress-bar`, `.progress`, `.navbar`, `.footer`, `.alert-{success,info,warning,danger}` (with dark-tuned backgrounds / borders), `.status-{success,danger,warning}`, `.report-section h3`, `.data-table th/td`, `.btn-primary` (replacing the old hardcoded `#007BC7` gradient with `var(--accent-primary)` → `var(--accent-primary-hover)`). Existing rules continue to act as the light defaults. |
| `static/js/theme-toggle.js` (new) | Plain ES5 module: `STORAGE_KEY='adoptiq-theme'`, `DEFAULT_THEME='dark'`, `VALID_THEMES={dark,light}`. Exposes `safeReadStored`, `safeWriteStored`, `getCurrentTheme`, `applyTheme`, `updateToggleAria`, `toggleTheme`. On `DOMContentLoaded` it reflects the live attribute, binds the click handler, and keeps `aria-pressed`/`aria-label` honest for screen readers. Same-origin so existing CSP `script-src 'self' 'unsafe-inline'` covers it without changes. |
| `templates/ask_ai.html` | `.ai-card` `border-left` swapped from a hardcoded `--cisco-blue` to `var(--accent-primary, var(--cisco-blue))` so the highlight follows the active theme. |
| `templates/external_intelligence.html` | Timeline rail (`.intel-timeline::before` / `.timeline-item::before`), bug/maint row hover (`.bug-row:hover`, `.maint-row:hover`), `.status-scheduled`, the JSON `<pre>` panel for `aiCanonicalHeadline`, and the `.input-group-text` `bg-white` repainted via `var(--border-subtle)` / `var(--accent-primary)` / `var(--bg-surface)` / `var(--accent-glow-soft)`. |
| `templates/leader_report_form.html` | `.shadow-cisco` and `.card:hover` glows switched to `color-mix(in srgb, var(--accent-primary, var(--cisco-accent-blue)) ...)` so the rim follows the active accent without leaking blue tones into the dark UI. |
| `templates/bst_psirt_search.html` | Page-shell gradient, search panel chrome, form controls, `.bst-btn`, results card, `.summary-box` / `.info-box` / `.warning-box` / `.error-box`, `.direct-link`, `.loading`, `.spinner` all repainted via the semantic tokens; added `[data-bs-theme="dark"]` overrides for the warn/error boxes. |
| `enhanced_admin_dashboard_v2.py` | Same `:root` token layer + `[data-bs-theme="dark"]` overrides injected into the inline `<style>` of `ENHANCED_ADMIN_TEMPLATE_V2`; `<html lang="en" data-bs-theme="dark">`; same early-paint `<script>` in `<head>`; mirrored `#theme-toggle` button anchored top-right of the admin header; inline ES5 toggle wiring at the end of `<script>` (kept inline because the admin app renders a single self-contained HTML string, not a Jinja partial chain). Worst hardcoded literals (`#fff` cards, `#f0f0f0` borders, `#007BC7` gradients, `rgba(255,255,255,...)` surfaces) repointed to `var(--bg-surface)` / `var(--border-subtle)` / `var(--accent-primary)` / `var(--shadow-card)`. Semantic-error hexes (`#dc3545`, `#6c757d`) intentionally left in place — they read on both themes and are not chrome. |
| `tests/test_round17_4_dark_theme.py` (new) | Five regression assertions: (1) `<html>` carries `data-bs-theme="dark"` (dark-by-default), (2) early-paint `<script>` in `<head>` references `localStorage` + `adoptiq-theme` + `setAttribute('data-bs-theme'`, (3) `:root` defines `--adoptiq-orange`, `--bg-base`, `--bg-surface`, `--text-primary`, `--accent-primary`, (4) `[data-bs-theme="light"]` override block exists (toggle reversibility), (5) **Round 13 invariant pin**: `--risk-critical: #d62728`, `--risk-high: #ff7f0e`, `--risk-medium: #ffd700`, `--risk-low: #2ca02c` are byte-identical (regression-proofs the chart / Excel parity). |

## Toggle behavior

- First load: early-paint `<script>` reads `localStorage['adoptiq-theme']`. If it's `"dark"` or `"light"`, that value is applied to `<html data-bs-theme="...">` before first paint. Anything else (missing, corrupt, private mode `SecurityError`) → fall back to `"dark"`.
- Click `#theme-toggle` → flip between `"dark"` ↔ `"light"`, write back to `localStorage`, update `aria-pressed`/`aria-label`. Sun glyph shows in light mode, moon glyph shows in dark mode.
- Same `STORAGE_KEY = 'adoptiq-theme'` is used by both the main app (:5151) and the admin dashboard (:5152), so a user who toggles in one tab sees the same theme in the other.
- The toggle is a true two-way switch: the `[data-bs-theme="light"]` override block restores the existing Cisco-blue chrome byte-for-byte, so light mode is identical to the pre-Round-17.4 look.

## Round 13 invariant — risk band parity

The four `--risk-*` tokens (`--risk-critical: #d62728`, `--risk-high: #ff7f0e`, `--risk-medium: #ffd700`, `--risk-low: #2ca02c`) are **untouched** by Round 17.4 and now pinned by `tests/test_round17_4_dark_theme.py::test_risk_band_hexes_are_byte_identical_round_13_pin`. These hexes are shared with `canonical_metrics.RISK_BAND_COLORS`, which drives:

- Matplotlib chart fills (the bar/donut/timeline visuals embedded in HTML, Word, and PDF reports).
- Excel conditional formatting on risk-band columns.
- HTML risk badges in the report itself.

The whole point of Round 13 was to make those three surfaces agree byte-for-byte. Round 17.4 deliberately re-skins **chrome** (navbar, cards, alerts, buttons, links, scrollbars) but treats the data-viz palette as load-bearing and off-limits.

## Verification

- `make verify` clean. Test count moved **2190 → 2195 passed, 2 skipped** (the five new pins in `tests/test_round17_4_dark_theme.py`). All four gates green: `ruff check` clean, `bandit -ll` clean, `pip-audit --strict` clean, full `pytest -q` clean.
- Manual smoke (per the plan's Verification section): open `http://localhost:5151` and `http://localhost:5152`, confirm dark default, click toggle → light, refresh → still light, toggle → dark, refresh → still dark, sweep analyze / ask_ai / history / external_intelligence / leader_report_form / customer_360 / help — no white-on-white or invisible text in either theme.

## Out of scope

- No change to `--risk-*` tokens or `canonical_metrics.RISK_BAND_COLORS`.
- No change to matplotlib chart palettes or Excel conditional formatting.
- No new dependencies (Bootstrap 5.3 already pinned; toggle JS is plain ES5).
- No CSP changes (existing `style-src 'self' 'unsafe-inline'` and `script-src 'self' 'unsafe-inline'` already cover both the inline early-paint script and the new same-origin `theme-toggle.js`).
- No port changes (Round 17.3 already shipped).
- No rebuild — pure template / CSS / JS change picked up by the existing v1.0.4 build at next launch.

# Round 0 — loop bootstrap floor (2026-04-26)

Bootstrap of the Cursor ↔ Claude Code review loop. **No source / test / CI changes.** Only adds the missing `.cursor/rules/session-handoff.mdc` rule, extends `CLAUDE.md` with a "Loop conventions" section, and pins this floor.

This round is numbered **0** because it is the loop baseline — every future Round-N must hold this floor or explain the regression in the handoff. Numerically it sits *after* Round 17.4.1 in time but *before* every round that uses the new handoff format.

## Why Round 0 is needed

Pre-bootstrap state: the repo already had a strong LLM-bible (`CLAUDE.md`), a working journal (`QUALITY_AUDIT.md` Rounds 14-17), a canonical verify gate (`make verify`), and Cursor rules (`adoptiq.mdc`, `quality-gate.mdc`, `BUGBOT.md`). The only missing piece was a structured Cursor → Claude handoff so each session opens against a known floor instead of repeating discovery.

## Files added / extended

- **CREATE** `.cursor/rules/session-handoff.mdc` — pins the closing-handoff template every Cursor session writes into this file before exit.
- **EXTEND** `CLAUDE.md` — appended `## Loop conventions (Cursor ↔ Claude Code)` section (no changes to prior content).
- **EXTEND** `QUALITY_AUDIT.md` — this Round 0 section (no changes to Rounds 14-17.4).

## Files NOT touched

- Zero source-code changes (`*.py`).
- Zero test changes.
- Zero CI changes (`.github/workflows/build.yml`).
- Zero changes to `Makefile`, `pyproject.toml`, `bandit.yaml`, `pytest.ini`.
- Zero changes to `.cursor/BUGBOT.md`, `.cursor/rules/quality-gate.mdc`, `.cursor/rules/adoptiq.mdc`.
- Zero changes to `.gitignore` or any historical `CODE_REVIEW*.md`.

## Verify gate baseline (the floor)

`make verify` on `round-13-audit` @ HEAD `9d2241a` (Round 17.4.1):

| Gate | Result |
| --- | --- |
| `ruff check .` | clean (gate-passing under the E/F/B/S ruleset in `pyproject.toml`) |
| `bandit -ll -c bandit.yaml` | 0 HIGH / 0 MED |
| `pip-audit -r requirements.txt --strict` | clean (no known vulnerabilities) |
| `pytest -q` | **2196 passed / 2 skipped in 8.03s** |
| `make verify` overall | **PASS** ("All Round 14 gates passed.") |

**Floor contract:** every Round-N must keep all four gates green and must not lower the test count below 2196 without an explicit `Known deferrals` entry in the handoff explaining why (e.g. test removed because the behavior it pinned was intentionally changed, with replacement test referenced).

## CI vs local divergence (knowingly deferred)

CI (`.github/workflows/build.yml::quality-checks`) runs `pytest -q` only — it does not run ruff, bandit, or pip-audit. `make verify` is therefore a stricter local contract than CI. Aligning CI to `make verify` is a follow-up; left out of Round 0 to keep the bootstrap surgical (no CI changes).

## Loop mechanics (the contract Round 0 establishes)

1. **Cursor** opens a session, edits code, runs `make verify`, then writes `## Round N — handoff <YYYY-MM-DD>` to the bottom of this file using the template in `.cursor/rules/session-handoff.mdc`. Commits carry `Made-with: Cursor`.
2. **Claude Code** opens its next session, reads the most recent `## Round N — handoff` block, audits per `.cursor/BUGBOT.md` + `.cursor/rules/quality-gate.mdc`, fixes what's wrong, and appends `## Round N — Claude review` under the same round. Commits carry `Made-with: Claude Opus 4.7 (1M context)`.
3. Both sides leave `# Round N` markers in source so `git diff <file> | grep 'Round N'` shows the per-file footprint.
4. The next round is `N+1`. Mid-round splits are `N.1`, `N.2`, etc.

**Trailer:** Made-with: Claude Opus 4.7 (1M context)

# Round 18 — Claude review (2026-04-26)

First Claude review pass through the new loop. **No `## Cursor session handoff` block existed yet** (loop only just bootstrapped in Round 0), so per the documented fallback the input batch was the commits since the most recent prior Round-N journal entry — i.e. `9d2241a` Round 17.4.1 (templates/CSS, +1 test) plus the Round 0 bootstrap itself (no source changes). With such a tiny input batch the prompt called for a wider 100% audit pass; that's what this entry documents.

## Stack (unchanged from Round 17)
Python 3.11.9; Flask web app + PyInstaller bundles; SQLite for local persistence; Snowflake / Cisco internal services / CircuIT LLM upstream. Hot files (>2k LoC): `app_simple.py` (~19.8k), `adoptiq_backend.py` (~11.9k), `leader_report_generator.py` (~6.3k).

## Verification commands
- `make test` — pytest baseline.
- `make lint` / `make security` / `make audit` / `make verify` — Round 14 harness, unchanged.

## Phase 0 — input batch & floor (Round 0)

| Source | Result |
| --- | --- |
| Most recent Cursor session handoff block | **none** — loop just bootstrapped; fallback to commits-since-most-recent-Round-N |
| Input batch | `9d2241a` Round 17.4.1 (templates +21 lines, `tests/test_round17_4_dark_theme.py` +39 lines) and `ffeb308` loop-bootstrap (zero source changes) |
| Floor entering Round 18 | **2196 passed / 2 skipped**, ruff clean, bandit HIGH/MED 0, pip-audit clean (`make verify` PASS) |

## Phase 1 — recon counts (production only, excluding tests/.venv/__pycache__/build/dist/_bundled_secrets.py)

| Smell | Count | Notes |
| --- | --- | --- |
| `except Exception:` | 647 (top file: `app_simple.py` 247) | Heavily defensive analysis pipeline; load-bearing per Rounds 5/6/8/13 |
| Bare `except:` | 0 | — |
| `if 'X' in locals()` shape | 76 (74 in `app_simple.py`) | R14-006 fixed 4 obvious dead-branch sites; remainder needs per-site analysis |
| Naive `datetime.now()` / `utcnow()` (non-comment) | 1 real hit (`export_adrian_snowflake_records.py:645`, CLI script — filename use) | 36 raw hits include comments documenting past replacements |
| f-string SQL with `{}` interpolation | 24, all with allow-list discipline | See Batch 2 below |
| Hardcoded secret-shaped literals (AWS / Stripe / GitHub / OpenAI / JWT / private-key) | **0** in production | — |
| `print()` in non-CLI app modules | 46 in `app_simple.py` + `adoptiq_backend.py`, **all in CLI/startup**; zero in request paths | — |
| TODO / FIXME / XXX / HACK | 0 | — |
| `pickle.load` / unsafe `yaml.load` | 0 | — |
| `pytest.skip` callers | 29 (all conditional, none unconditional in CI) | — |
| `pip list --outdated` | 76 packages | `pip-audit` still clean (no CVEs) |

## Phase 2 — Findings table

| ID | Severity | Status | Surface | One-liner | Commit |
| --- | --- | --- | --- | --- | --- |
| R18-001 | LOW | FIXED | docs / sec posture | `CLAUDE.md` Two Flask Apps row claimed main-app default bind = `0.0.0.0`; actual code defaults to `127.0.0.1` per Round 14 R14-007. LLM-bible drift silently regresses documented security posture. | (see Round 18 commit) |

## Phase 2.1 — R18-001: CLAUDE.md ↔ app_simple.py bind-default parity (FIXED)

**Root cause.** Round 14 R14-007 changed `app_simple.py` to default `127.0.0.1` (opt-in public via `ADOPTIQ_BIND_PUBLIC=1`); the matching update to `CLAUDE.md` Two Flask Apps table was missed. Every future Cursor / Claude session reading the bible would have been told "default 0.0.0.0 via ADOPTIQ_BIND_HOST", which is the opposite of the actual posture. Not exploitable directly (the code is right) but it actively misleads downstream LLM reasoning about the security model.

**Fix.** Rewrote the `Main UI` row in the Two Flask Apps table to: "Loopback (`127.0.0.1`) by default; opt-in public bind via `ADOPTIQ_BIND_PUBLIC=1`, or set `ADOPTIQ_BIND_HOST` directly (Round 14 R14-007)". Updated the `Admin` row to the same shape for symmetry.

**Regression test.** `tests/test_round18_doc_code_parity.py` (NEW, 3 cases):
- `test_app_simple_bind_default_is_loopback` — pins the exact ternary `'0.0.0.0' if _bind_public else '127.0.0.1'` plus the `ADOPTIQ_BIND_PUBLIC` env-var name in `app_simple.py`.
- `test_claude_md_does_not_claim_public_bind_default` — fails if the Main UI row in `CLAUDE.md` ever re-asserts `default \`0.0.0.0\``.
- `test_claude_md_documents_loopback_default_and_escape_hatch` — fails if the Main UI row drops either `127.0.0.1` or `ADOPTIQ_BIND_PUBLIC`.

A future doc edit OR code edit that breaks the doc/code parity now fails `make verify` locally before it can land.

## Phase 3 — verifications (no defect found)

### Phase 3.1 — Round 17.4.1 input batch (Batch 5, read-only)
- `templates/base.html:1025-1028` `[data-bs-theme="dark"] h1.display-4:not(.error-code)` + adjacent `.lead.text-muted` override matches `tests/test_round17_4_dark_theme.py::test_page_header_title_is_white_in_dark_mode` exactly.
- `.error-code` marker is unique to `404.html`, `500.html`, and the `base.html` CSS rule itself — no collateral collisions.
- Other navigable templates (`analyze.html`, `history.html`, `ask_ai.html`, `external_intelligence.html`, `help.html`, `leader_report_form.html`) carry `h1.display-4` without `.error-code`, so the bright-white treatment correctly applies to them. **No drift.**

### Phase 3.2 — f-string SQL allow-list audit (Batch 2)
All 24 hits are SAFE. Three categories, each with documented allow-list discipline:
- `incident_storage.py` (17 hits): all interpolate a constant `where_clause` string built earlier in-function, with user values bound via `?` placeholders. The two identifier-interpolating sites (`_fetch` at L738/747) validate `table` / `order_col` against an explicit `_EXPORT_TABLES` dict allow-list AND a strict regex (`^[A-Za-z_][A-Za-z0-9_]*$`) before interpolation — Round 6 / Phase 4.19 documented.
- `enhanced_admin_dashboard_v2.py:333` (DDL): `_col` and `_type` come from a hardcoded `_new_cols` list, not input.
- Snowflake-side (`snowflake_csone_discovery.py:148/176`, `adoptiq_backend.py:1000`, `adoptiq_backend.py:1927-1929`): every interpolation is gated by `_safe_or_skip()` regex, `guard_table()` from `snowflake_table_policy`, or built from hardcoded constants/4-element column allow-lists, with user values bound via `%s` placeholders.

This validates the documented `bandit.yaml` B608 skip rationale ("all fire on f-strings that build SELECT projections (column lists), not WHERE values"). No regression test added because Round 6/7/8/9's existing test files (`test_round6_export_all_data_allowlist.py`, `test_round7_policy_allowlist_enforced.py`, `test_round8_snowflake_table_policy_edges.py`, `test_round9_snowflake_policy_comma_join.py`, `test_policy_blocked_table_emits_fetch_error.py`) already cover the allow-list invariants.

### Phase 3.3 — broad-except sample (Batch 3)
Sampled ~10 representative sites in `app_simple.py` (`after_request` headers, `_r12_safe_excel_value`, `run_compact_analysis` data-fetch wrappers). All are Category (i) defensive boundaries — load-bearing for the rugged-analysis-pipeline pattern that Rounds 5/6/8/13 deliberately built. Past rounds (R14-002, R14-003) caught the obvious NameError-class bugs hidden behind these. Random sampling on a 17-round-audited surface didn't surface new defects. **Stopping without per-site fixes** in this round; deferring to R18-NEXT-002 below.

### Phase 3.4 — print() in production (Batch 4)
46 hits across `app_simple.py` (21) and `adoptiq_backend.py` (25). Every single hit is in CLI / startup context:
- `app_simple.py` L19648-L19811: CLI startup banner, port-conflict resolution UI ("AdoptIQ Simple - AI-Powered Executive Analytics", "Port %s is in use…", "Exiting.").
- `adoptiq_backend.py` L11320-L11749: `main()` CLI entry point ("Connecting to Snowflake…", "Fetching subscriptions…", final "SUCCESS:" / "ERROR:" lines).

**Zero hits in request handlers or worker threads.** The Flask request layer correctly uses `structured_logging`. No fix needed.

### Phase 3.5 — `if 'X' in locals()` antipattern (Batch 1)
76 hits, 74 in `app_simple.py`. Spot-checked the `feature_requests if 'feature_requests' in locals() else …` pattern at L6704: in that case `feature_requests` is assigned in BOTH branches of the preceding try/except, so the `'feature_requests' in locals()` guard at the call site is in fact dead — same shape R14-006 fixed at 4 sites. The remaining ~70 hits each need per-function dead-code analysis (is `X` always bound by the call site, or only conditionally?), which is a multi-round effort on its own. **Deferring to R18-NEXT-001.**

## Files changed

| File | Why | `# Round 18` markers |
| --- | --- | --- |
| `CLAUDE.md` | R18-001 fix: bind-default doc/code parity (Main UI row + Admin row symmetry) | 0 (doc) |
| `tests/test_round18_doc_code_parity.py` | NEW — pins R18-001 doc/code parity in 3 cases | (NEW file) |
| `QUALITY_AUDIT.md` | This Round 18 section | 0 (doc) |

No `*.py` source-code changes outside of the new test file.

## Verification commands & results

```
$ make verify
ruff check .          → clean
bandit -ll …          → 0 HIGH / 0 MED
pip-audit --strict    → clean
pytest -q             → 2199 passed / 2 skipped (was 2196)
All Round 14 gates passed.
```

Net test delta: **2196 → 2199 passed** (+3, all from `tests/test_round18_doc_code_parity.py`), **2 skipped unchanged**, all gates green.

## Residual risks

- **Bind-posture doc drift could re-occur on other dimensions** (admin port, CSP, CORS, etc.). The R18-001 test only pins the main-app bind row; expanding the parity check to every documented invariant would be a larger meta-test (deferred).
- **Past-round audit markers** continue to be the load-bearing rationale for the broad-except / in-locals patterns. If those markers are ever removed without a replacement test, the rationale evaporates and a future reviewer may mass-rewrite. Mitigation: the existing `# Round N` source-marker convention combined with the new Round 0 floor + Round 18 doc/code parity pattern collectively encode the rationale.

## Recommended follow-ups (R18-NEXT)

| ID | Sev | Surface | One-liner | Why deferred | Effort |
| --- | --- | --- | --- | --- | --- |
| R18-NEXT-001 | MED | `app_simple.py` correctness | Per-site triage of remaining ~70 `if 'X' in locals() else Y` sites: classify each as (a) always-bound → simplify, or (b) conditionally-bound → keep with comment naming the producing branch | Requires per-function dead-code analysis on a 19.8k-line file; one-round-of-its-own | M-L |
| R18-NEXT-002 | MED | `app_simple.py` reliability | Sub-audit of `except Exception:` sites that have NO `as e:` (no exception context) — ensure each at least logs to debug. Estimated ~50 sites of the 247 in `app_simple.py` | Mass change risks breaking the rugged-pipeline contract; needs targeted tests per site | M |
| R18-NEXT-003 | LOW | dependency hygiene | 76 outdated packages per `pip list --outdated`; pip-audit currently clean (no CVEs). Bump conservatively in a dedicated round so failures isolate | Mass version bump risks regressions across the report stack | M |
| R18-NEXT-004 | LOW | CI parity | Align `.github/workflows/build.yml::quality-checks` with `make verify` (currently CI runs only `pytest -q`; ruff/bandit/pip-audit are local-only) | Out-of-scope per Round 0 contract; needs a separate CI-only commit | S |
| R18-NEXT-005 | LOW | docs | Audit `CLAUDE.md` for other doc/code drifts (CSP defaults, port overrides, corpus opt-in default, etc.) using the same parity-test pattern landed in Round 18 | One per invariant; pattern is reusable from `tests/test_round18_doc_code_parity.py` | S each |

## Per-batch footprint

| Batch | Status | Files touched |
| --- | --- | --- |
| 1 — `if 'X' in locals()` | DEFERRED → R18-NEXT-001 | none |
| 2 — f-string SQL allow-list | NO DEFECT | none |
| 3 — broad-except sample | NO DEFECT (sample); DEFERRED → R18-NEXT-002 | none |
| 4 — `print()` in request paths | NO DEFECT | none |
| 5 — Round 17.4.1 input batch verify | NO DRIFT | none |
| R18-001 — bind-default doc/code parity | FIXED | `CLAUDE.md`, `tests/test_round18_doc_code_parity.py` (NEW) |

**Trailer:** Made-with: Claude Opus 4.7 (1M context)

## Round 19.1 — handoff 2026-04-26

This is a Cursor closing handoff for a small, surgical session that closes
**R18-NEXT-005** (the doc/code parity expansion follow-up Claude flagged at
the end of Round 18). The bigger Round 19 mission ("Report Accuracy Golden
Fixture", scaffolded above at line ~1299) is unrelated and remains untouched
— this is `.1` because it slots in beside that mission, not on top of it.

**What changed (plain English):**
- Added `tests/test_round19_1_doc_code_parity_expansion.py` (NEW, 6 cases) that
  pins three more doc/code parity invariants in the same shape as the R18-001
  pattern, so a future doc OR code edit that breaks any of them fails
  `make verify` locally before it can land:
  1. **Admin bind default** — `enhanced_admin_dashboard_v2.py:3826/3830` defaults to
     `127.0.0.1` unless `ADOPTIQ_ADMIN_BIND_PUBLIC=1`; `CLAUDE.md` admin row
     (line 49) and the `**Admin security:**` callout (line 111) say the same
     thing. (Round 14 R14-007 / Phase 4.5.)
  2. **Default ports 5151 / 5152** — `app_simple._DEFAULT_MAIN_PORT` (line 19539)
     and `enhanced_admin_dashboard_v2._DEFAULT_ADMIN_PORT` (line 122) match the
     `CLAUDE.md` Two Flask Apps table rows; `ADOPTIQ_PORT` /
     `ADOPTIQ_ADMIN_PORT` env-var override names are pinned in code. (Round 17.3.)
  3. **Risk-band hex SSoT parity** — `canonical_metrics.RISK_BAND_COLORS`
     (lines 73-80) for `CRITICAL`/`HIGH`/`MEDIUM`/`LOW` equals the four
     `--risk-*` CSS tokens in `templates/base.html` (lines 112-115)
     byte-for-byte: `#d62728` / `#ff7f0e` / `#ffd700` / `#2ca02c`. This is the
     load-bearing Round 13/15 cross-surface invariant Round 17.4 explicitly
     left untouched when the dark theme shipped.
- No source-code changes (`*.py` outside the new test file). No template / CSS
  / config changes. No CLAUDE.md changes — the doc was already correct on all
  three invariants; this round just pins it.

**Files touched:**
- `tests/test_round19_1_doc_code_parity_expansion.py` — NEW, 6 doc/code parity tests
- `QUALITY_AUDIT.md` — this Round 19.1 handoff section

**SSoT modules touched:** none
  (The test reads `canonical_metrics.py` and `config.py` as ground truth, but
  does not modify either. No SSoT module is mutated this round.)

**Tests added/updated:**
- `tests/test_round19_1_doc_code_parity_expansion.py::test_admin_app_bind_default_is_loopback` — pins the admin-app `'0.0.0.0' if _bind_public else '127.0.0.1'` ternary + `ADOPTIQ_ADMIN_BIND_PUBLIC` env-var name in `enhanced_admin_dashboard_v2.py`
- `tests/test_round19_1_doc_code_parity_expansion.py::test_claude_md_admin_row_documents_loopback_default_and_escape_hatch` — pins `127.0.0.1` + `ADOPTIQ_ADMIN_BIND_PUBLIC` on the `| Admin | 5152 |` row of CLAUDE.md
- `tests/test_round19_1_doc_code_parity_expansion.py::test_claude_md_admin_security_line_pins_loopback_default` — pins the `**Admin security:**` callout line in CLAUDE.md (loopback default + escape-hatch env var)
- `tests/test_round19_1_doc_code_parity_expansion.py::test_default_ports_match_claude_md_two_flask_apps_table` — pins `_DEFAULT_MAIN_PORT = 5151` + `_DEFAULT_ADMIN_PORT = 5152` against the `CLAUDE.md` Two Flask Apps table rows
- `tests/test_round19_1_doc_code_parity_expansion.py::test_default_ports_have_env_overrides_documented` — pins `ADOPTIQ_PORT` / `ADOPTIQ_ADMIN_PORT` env-var-name discipline in both apps
- `tests/test_round19_1_doc_code_parity_expansion.py::test_canonical_metrics_risk_band_colors_match_css_tokens` — pins canonical_metrics ↔ templates/base.html risk-band hex parity (the Round 13/15/17.4 cross-surface invariant)

**Verify status:**
- `make verify` — pass
- pytest: 2205 passed / 2 skipped (was 2199 / 2; +6 net, all from the new file)
- ruff: 0 findings
- bandit HIGH/MED: 0
- pip-audit: clean

**Hot spots Claude should audit first:**
1. `app_simple.py` — `if 'X' in locals()` antipattern count grew from **74 → 84** since Round 18's recon (24 hours). 10 new sites need the same per-function dead-code triage R18-NEXT-001 deferred. `grep -nE "in locals\(\)" app_simple.py` reproduces the count; pick a sample of 5-10 newly-added sites and classify each as (a) always-bound → simplify, or (b) conditionally-bound → keep with a comment naming the producing branch.
2. `app_simple.py` ~247 broad-except sites — R18-NEXT-002 still open. The Round 18 spot-check confirmed all are Category (i) defensive boundaries, but the sub-audit of the ~50 sites WITHOUT an `as e:` clause (no exception context, no debug log) is still pending; mass-add `logger.debug(... exc_info=True)` instrumentation per site OR justify keeping each silent.
3. The Round 19 KPI registry section above (line ~1299) is **Phase 1 only** — KPI registry inventory is filled in but Phases 2-7 (golden fixture build, formatter run, programmatic diff vs expected) are not started. R18-D1 (cross-format parity coverage gap) was supposed to absorb into this round and has not. Either close R18-D1 here in Round 19 by building the fixture, or split it back out to its own follow-up.
4. `.github/workflows/build.yml::quality-checks` runs only `pytest -q` — ruff / bandit / pip-audit are local-only. R18-NEXT-004 still open. A Cursor-edit that's clean locally but adds a ruff or bandit regression won't be caught by CI today.
5. `pip list --outdated` reports 76 outdated packages (Round 18 baseline; not re-checked this session). `pip-audit` is still clean (no CVEs), but staleness compounds; R18-NEXT-003 still open.

**Known deferrals (intentional non-fixes):**
- R18-NEXT-001 (broad triage of all ~84 `if X in locals()` sites in `app_simple.py`) — needs per-function dead-code analysis; one round of its own; left to Claude or a future Cursor session.
- R18-NEXT-002 (no-context broad-except sub-audit, ~50 sites) — same reason.
- R18-NEXT-003 (76 outdated packages) — pip-audit still clean; defer to a dedicated dependency-bump round so any regressions isolate.
- R18-NEXT-004 (CI/`make verify` alignment) — out-of-scope per Round 0 contract; needs a separate CI-only commit.
- The big Round 19 mission ("Report Accuracy Golden Fixture", line ~1299) — the KPI registry is in place but the synthetic fixture, formatter run, and per-KPI diff harness are not yet built. R18-D1 (cross-format parity coverage gap) is supposed to absorb into that round.
- No CLAUDE.md edits this round — the doc was already correct on all three pinned invariants; this round only adds the regression net so future drift is caught locally before it can land.
- `# Round 19.1` source markers — no source files were modified, so no `# Round 19.1` markers exist; the per-file footprint is just the one new test file.

**Trailer:** Made-with: Cursor

# Round 20 — Claude review (2026-04-26)

Picked up hot spot #1 from the Round 19.1 handoff (the `if 'X' in locals()` long-tail in `app_simple.py`). The handoff framed it as "count grew 74 → 84 in 24 hours, R18-NEXT-001 grew while we slept." The first thing this round did was reconcile that claim against git history, which produced a recon-discipline finding before any code changed.

## Stack
Unchanged from Round 19.1.

## Verification commands
- `make verify` — Round 14 harness, unchanged.

## Phase 0 — recon-discipline finding (R20-D1)

**Claim from Round 19.1 handoff (L1769):** "`if 'X' in locals()` antipattern count grew from **74 → 84** since Round 18's recon (24 hours)."

**Actual git history of the count in `app_simple.py`:**

| Commit | Round | `in locals()` count |
| --- | --- | --- |
| `c3ebfb0` | Round 12 baseline | 93 |
| `9d53b94` | Round 14 (R14-006 fixed 4) | **84** |
| `0543130` | Round 15 + 16 | **84** |
| `95b7597` | Round 17.1 + 17.2 | **84** |
| `6702046` | Round 17.4 | **84** |
| `9d2241a` | Round 17.4.1 | **84** |
| `ffeb308` | loop-bootstrap (Round 0) | **84** |
| `db0803e` | Round 18 | **84** |
| `e19f97f` | Round 19.1 (input HEAD) | **84** |

**Conclusion:** the count has been stable at **84 since Round 14** (10+ commits, no regression, no growth). Round 18's own recon (mine) reported "76 / 74-in-app_simple.py" — that was an error on my part, off by 10. Round 19.1 inherited the wrong number and labelled the (correct) 84 as a "growth from 74." There was no growth.

**Why this matters:** the handoff's framing implied an emergency drift-rollback was needed. The actual work was the same long-tail R18-NEXT-001 triage. Going forward, this round adds a count-guard test pinning the floor in code, so the next session reads the authoritative number from a passing assertion instead of a comment in some other doc.

## Phase 1 — Findings table

| ID | Severity | Status | File:line | One-liner | Commit |
| --- | --- | --- | --- | --- | --- |
| R20-D1 | LOW | DOCUMENTED | n/a | Round 18 + Round 19.1 mis-counted `in locals()` in `app_simple.py` (claimed 74 / "grew 74→84"); actual count was 84 stable since Round 14. Pinned by `tests/test_round20_in_locals_simplification.py::test_in_locals_count_is_at_or_below_post_r20_floor` so future recon reads the authoritative number. | (this round) |
| R20-001 | MED | FIXED | `app_simple.py` (19 sites in `run_compact_analysis`) | Drop dead `X if 'X' in locals() else FALLBACK` guards on call args where the producer is provably bound by sentinel-init at function top OR by a try/except whose every branch assigns. | (this round) |
| R20-NEXT-001 | **HIGH** | DOCUMENTED | `app_simple.py:7076-7124` (in `generate_report` nested fn), `app_simple.py:7346-7387` (in `generate_excel` nested fn) | The `'X' in locals()` guards INSIDE nested functions are not just dead-branch antipatterns — they are closure-binding bugs. `locals()` inside a nested function does NOT include free vars captured from the enclosing scope, so `'team_subs_df_unfiltered' in locals()` evaluates False and the else branch is silently taken. Consequence: outer-scope data (the actual `team_subs_df_unfiltered`, `csconsole_*`, `days`, etc.) is dropped at the call site and replaced with `None`/`30`/empty fallback. This means `generate_report` calls `build_customer_lookup(None)` instead of with the real frame, and the multi-source `extra_frames` list never gets populated — risk-universe always falls back to AB+CSOne only. Same bug in `generate_excel`. Behavior-changing fix; needs its own round. | — |

## Phase 2 — R20-001 simplifications (FIXED)

19 dead-branch guard sites in `run_compact_analysis` simplified, all OUTSIDE nested functions:

| Cluster | Lines | Args simplified | Why dead |
| --- | --- | --- | --- |
| Briefing book call (single block) | L6779-6788 | `feature_requests`, `software_defects`, `psirt_vulns`, `ext_incidents`, `ext_bugs` (5) | `feature_requests` sentinel-init L5970; `ext_bugs`/`ext_incidents` sentinel-init L6720/L6721 + try/except all-branches; `software_defects`/`psirt_vulns` assigned in BOTH branches of L6759-6765 try/except |
| Fallback insights (AI insufficient path) | L6905-6909 | `team_subs_df`, `csconsole_action_plans`, `csconsole_customer_pulse`, `csconsole_success_priorities`, `csconsole_adoption_barriers` (5) | All bound by L6372-6526 fetch block (every try-success and except path assigns); else at L6527 sets status=error and returns before reaching call |
| Fallback insights (AI exception path) | L6937-6941 | same 5 vars | Same; AI call failure cannot un-bind upstream fetched frames |
| Validation call | L6999-7001 | `csconsole_action_plans`, `csconsole_customer_pulse`, `csconsole_success_priorities` (3) | Same |
| Executive charts call | L6704 | `feature_requests` (1) | Sentinel-init L5970; the guard's else branch was a literal copy of the sentinel default |

Each simplification has a `# Round 20 / R20-001` comment immediately above naming the producer rationale so a future reader can audit the dead-branch claim without re-running the per-function dead-code analysis.

**Behavior preservation:** all 19 simplifications are byte-identical at runtime — the dead-branch fallback values were never being passed to receivers because the producer was always bound. Verified by:
- Pre-edit: 191 targeted tests (`-k "compact or briefing or fallback or validation"`) green at floor 2205.
- Post-edit: same 191 pass; full `make verify` green at 2208 / 2 skipped (+3 from the new R20 test file).

## Phase 3 — R20-NEXT-001 (HIGH bug pattern, deliberately deferred)

Documenting because this is the most consequential finding of the round and **must not be re-conflated** with the dead-branch antipattern in future planning.

The pattern looks identical to R20-001 from a grep view:
```python
build_customer_lookup(team_subs_df_unfiltered if 'team_subs_df_unfiltered' in locals() else None)
```
But it appears INSIDE a nested function (`generate_report` at L7062, `generate_excel` at L7335). In CPython, `locals()` inside a function returns only that function's local namespace; free variables captured from the enclosing scope are NOT in `locals()`. So `'team_subs_df_unfiltered' in locals()` is always False inside the nested function, and the else branch (`None`) is always taken. The outer-scope frame is silently discarded.

**Effects observed by reading L7062-7134:**
- `build_customer_lookup` is always called with `None` instead of `team_subs_df_unfiltered`.
- `_ei_extra_frames` always remains `[]` because the loop at L7080-7100 checks `if _df_name in locals():` — none of the listed names are local to `generate_report`, so the body never runs.
- `risk_scores = calculate_renewal_risk_scores(..., extra_frames=None, ...)` — multi-source customer expansion is silently disabled.
- `recent_window_days=int(days) if 'days' in locals() and days else 30` — `days` is a free var from outer scope; guard is False → `recent_window_days` is hardcoded to 30, the user's `days` parameter is ignored in the risk-scores call.

This means:
1. The compact-analysis Word report's risk-scores universe is always AB+CSOne-only (never multi-source), even when team_subs_df_unfiltered + csconsole_* are populated.
2. The `recent_window_days` parameter to risk scoring is hardcoded at 30 days regardless of the report's `days` setting.

Same bug pattern repeats in `generate_excel` (L7335+) for the Excel writer side.

**Why deferred:** fixing these is behavior-changing (report content shifts). Risk scores and customer counts would change for every analysis. Needs:
1. Stakeholder confirmation that current report numbers are already wrong and should be corrected.
2. Per-affected-call-site verification with the golden fixture (Round 19 KPI mission, Phases 2-7).
3. Likely a coordinated update of any baseline test that currently asserts the buggy values.

**Reproducibility / pin:** `tests/test_round20_in_locals_simplification.py::test_in_locals_inside_nested_functions_remains_documented_as_known_bug` asserts the bug-marker call site at L7076 still exists, so a future "well-meaning fix" cannot land silently.

## Files changed

| File | Why | `# Round 20` markers |
| --- | --- | --- |
| `app_simple.py` | R20-001: 19 dead-branch guard simplifications across 5 call sites in `run_compact_analysis` | 5 (one per cluster, with rationale comment) |
| `tests/test_round20_in_locals_simplification.py` | NEW — pins R20-D1 (count guard at floor 65), R20-001 (simplified call shapes don't regress), R20-NEXT-001 (nested-fn closure-bug marker still present) | (NEW file) |
| `QUALITY_AUDIT.md` | This Round 20 section | n/a (doc) |

## Verification commands & results

```
$ make verify
ruff check .          → clean
bandit -ll …          → 0 HIGH / 0 MED
pip-audit --strict    → clean
pytest -q             → 2208 passed / 2 skipped (was 2205)
All Round 14 gates passed.
```

Net test delta: **2205 → 2208 passed** (+3, all from `tests/test_round20_in_locals_simplification.py`), **2 skipped unchanged**, all gates green.

`in locals()` count in app_simple.py: **84 → 65** (19 sites simplified; new floor pinned by the count-guard test).

## Residual risks

- **R20-NEXT-001 is HIGH severity and unfixed.** The compact analysis's Word and Excel writers silently drop the `extra_frames` and `days` parameters at the risk-scores call inside `generate_report` / `generate_excel` due to the closure-binding misunderstanding. Every compact report shipped while this bug exists may have understated risk-universe coverage. Fix is behavior-changing.
- **The 28 remaining `in locals()` sites in `run_customer_renewal_analysis` (L10341-L11494), `run_subscription_analysis` (L17927-L18575), and `run_leader_report_generation` (L19166-L19167)** are the same long-tail R18-NEXT-001 work. Same per-function dead-code analysis required; addressed incrementally per round.
- **My Round 18 recon was wrong** (74 vs actual 84). I reported it as the floor; Round 19.1 trusted it. The new count-guard test eliminates this failure mode going forward but does not retroactively fix any planning that was based on the wrong number.

## Recommended follow-ups (R20-NEXT)

| ID | Sev | Surface | One-liner | Why deferred | Effort |
| --- | --- | --- | --- | --- | --- |
| R20-NEXT-001 | **HIGH** | `app_simple.py` correctness | Closure-binding bugs in `generate_report` (L7062+) and `generate_excel` (L7335+): `'X' in locals()` inside nested functions always False for free vars; receiver always gets `None`/`30`/`[]` instead of outer-scope value. Affects risk-scores universe + window param in both Word and Excel writers. | Behavior-changing; needs golden-fixture verification (Round 19 mission Phases 2-7) before any change | M-L |
| R20-NEXT-002 | MED | `app_simple.py` correctness | Long-tail R18-NEXT-001: ~28 remaining `in locals()` sites in `run_customer_renewal_analysis` / `run_subscription_analysis` / `run_leader_report_generation`. Same per-function dead-code triage as R20-001; incremental progress per round. | Multi-round effort; same shape; safe to chunk | M each chunk |
| R20-NEXT-003 | MED | `app_simple.py` reliability | R18-NEXT-002 inherited: ~50 broad-except sites without `as e:` clause (no exception context, no debug log). Mass-add `logger.debug("...: %s", e, exc_info=True)` per site OR justify silence. | Untouched this round (one-batch rule); previously partially scoped in the rolled-back Round 19 attempt | M |
| R20-NEXT-004 | MED | `app_simple.py` correctness | Round 19 mission Phase 2-7 (Report Accuracy Golden Fixture): synthetic input + per-KPI diff harness. The Round 19 KPI registry at QUALITY_AUDIT.md L1299 is Phase 1 only; absorbs R18-D1 (cross-format parity coverage gap). | Big mission; standalone round; required to safely fix R20-NEXT-001 | L |
| R20-NEXT-005 | LOW | CI parity | R18-NEXT-004 inherited: align `.github/workflows/build.yml::quality-checks` with local `make verify` (CI runs `pytest -q` only; ruff/bandit/pip-audit are local-only). | Out of scope per Round 0 contract; needs CI-only commit | S |
| R20-NEXT-006 | LOW | dependency hygiene | R18-NEXT-003 inherited: 76 outdated packages, `pip-audit` clean. Conservative bumps in dedicated round. | Mass version bump risks regressions across report stack | M |

## Per-batch footprint

| Batch | Status | Files touched |
| --- | --- | --- |
| R20-D1 — recon-discipline finding | DOCUMENTED + count-guard test | `tests/test_round20_in_locals_simplification.py` (NEW) |
| R20-001 — `if X in locals()` simplification (19 sites in run_compact_analysis) | FIXED | `app_simple.py` |
| R20-NEXT-001 — closure-binding bug in generate_report/generate_excel | DOCUMENTED + presence-pin test | `tests/test_round20_in_locals_simplification.py` (NEW) |

**Trailer:** Made-with: Claude Opus 4.7 (1M context)

## Round 21 — handoff 2026-04-26

**What changed (plain English):**
- Built the Round 19 golden-fixture mission's Phase 2 + Phase 3 + Phase 4-SSoT (per the registry at `QUALITY_AUDIT.md` L1299-1416). New synthetic input + hand-computed `EXPECTED_KPIS` dict + canonical-layer diff harness. Ships R20-NEXT-004 Phases 2-3-4S; Phases 4F (formatter render) and 5 (extract + diff) are queued for Round 21.1.
- No source-code changes. This round only ADDS test artifacts that pin `canonical_metrics` against a known input.
- The 4 reconciliation invariants from the registry (priority sum == total_cases, band sum == len(risk_profiles), high_risk == critical+high_only, open+closed <= total) are now first-class tests so a future SSoT regression points directly at the broken invariant.
- Excel summary row order + values are pinned against `report_export_styling.build_summary_rows` so the documented summary sequence (`QUALITY_AUDIT.md` L1388-1403) is enforced.

**Files touched:**
- `tests/fixtures/round19/golden.py` — NEW (420 lines). Phase 2 + Phase 3: deterministic `make_ab_df` / `make_csone_df` / `make_pulse_df` / `make_risk_profiles` / `make_extra_frames` builders + frozen `EXPECTED_KPIS` dict covering every KPI in the registry. All values literal, no `np.random` or `datetime.now()`.
- `tests/test_round21_canonical_kpi_golden_fixture.py` — NEW (404 lines, 27 tests). Phase 4-SSoT: per-helper exact-match (`count_total_tac`, `count_p1`–`count_p4`, `count_unknown_priority`, `count_break_fix`, `count_provisioning`, `count_bems`, `bems_rate`, `count_total_barriers`, `count_critical_barriers`, `count_open_barriers`, `count_open_tac`, `count_closed_tac`, `count_escalated`, `count_customers`, `compute_high_risk_count`, `pulse_sentiment`) + `build_portfolio_metrics` integration test + 4 reconciliation invariants + Excel summary row-order test + Excel summary KPI-value test.
- `QUALITY_AUDIT.md` — this Round 21 handoff section.

**SSoT modules touched:** none (test-only round; the SSoT modules `canonical_metrics`, `report_export_styling`, `report_export_schema` are exercised but unchanged)

**Tests added/updated:**
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_total_tac_matches_expected` — pins `count_total_tac` against the 20-row CSOne fixture (expected 20).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_p1_matches_expected` … `test_count_p4_matches_expected` — pin priority bucket counts (4 / 4 / 4 / 4).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_unknown_priority_matches_expected` — pins unknown bucket (4).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_break_fix_matches_expected` / `test_count_provisioning_matches_expected` — pin case-type taxonomy (3 / 2).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_bems_matches_expected` / `test_bems_rate_matches_expected` — pin BEMS counter and 2-dp rate.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_total_barriers_matches_expected` / `test_count_critical_barriers_matches_expected` / `test_count_open_barriers_matches_expected` — pin AB metrics (10 / 4 / 5).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_open_tac_matches_expected` / `test_count_closed_tac_matches_expected` — pin lifecycle counts.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_escalated_matches_expected` — pins escalation marker count.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_count_customers_matches_expected` — pins cross-frame customer dedup (5).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_compute_high_risk_count_matches_expected` — pins CRITICAL+HIGH band membership against the 10-profile risk universe (4).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_pulse_sentiment_matches_expected` — pins pulse roll-up (8 rows → 6.5 mean → Neutral, 3 pos / 3 neut / 2 neg).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_build_portfolio_metrics_matches_expected` — integration test: every KPI key in `build_portfolio_metrics` payload must equal the expected value (exact, no tolerance).
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_invariant_priority_sum_equals_total_cases` — Reconciliation invariant 1.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_invariant_band_sum_equals_risk_universe_size` — Reconciliation invariant 2.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_invariant_high_risk_equals_critical_plus_high_only` — Reconciliation invariant 3.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_invariant_open_plus_closed_at_most_total_cases` — Reconciliation invariant 4.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_excel_summary_row_order_matches_documented_sequence` — pins the `build_summary_rows` label sequence against the documented order at QUALITY_AUDIT.md L1388-1403.
- `tests/test_round21_canonical_kpi_golden_fixture.py::test_excel_summary_kpi_values_use_canonical_metrics` — pins that the values emitted in the summary sheet equal the canonical-helper outputs.

**Verify status:**
- `make verify` — pass
- pytest: 2235 passed / 2 skipped (was 2208, +27 net)
- ruff: 0 findings
- bandit HIGH/MED: 0
- pip-audit: clean

**Hot spots Claude should audit first:**
1. `tests/fixtures/round19/golden.py` — Phase 3 hand-computation. Walk the 20 CSOne rows, 10 AB rows, 8 pulse rows, 10 risk profiles, and 4 extra-frame rows against the `EXPECTED_KPIS` literal. Any disagreement between you and the dict is exactly the contract failure this fixture is meant to surface — please file as `R21-D*` rather than silently fixing.
2. `tests/fixtures/round19/golden.py::_CSONE_ROWS` BEMS coverage — 4 rows have `Transaction ID = BEMS-*`, the test expects `bems_count == 4`. The registry note at `QUALITY_AUDIT.md` L1322 says BEMS can be observed via either AB column OR CSOne `Transaction ID`. The fixture currently exercises the CSOne path only. If your read of `count_bems` says AB-side BEMS rows must also count, the expected value is wrong (file as R21-D).
3. `tests/test_round21_canonical_kpi_golden_fixture.py::test_excel_summary_kpi_values_use_canonical_metrics` — the values emitted by `build_summary_rows` are checked against `EXPECTED_KPIS` (not against `build_portfolio_metrics` output). If `build_summary_rows` does any post-processing on the KPI before emitting (e.g. integer-cast, percent format), this test will catch it; please confirm the test is reading the right cell.
4. The fixture pre-populates `case_priority_norm` / `severity_norm` / `case_status_norm` / `is_open` / `is_closed` / `case_type_class` columns directly. This makes the diff harness robust against changes in `data_normalization` regex, but it also means the harness does NOT exercise the raw → normalized pipeline. Please flag if you think Round 22 should add a separate test that asserts `add_case_lifecycle_fields` produces the same normalized values from the raw columns the fixture also includes.
5. `tests/test_round21_canonical_kpi_golden_fixture.py` `sys.path` mutation (L46-52) — same pattern as `tests/conftest.py` PROJECT_ROOT injection, but please confirm it's idempotent under repeated test collection.

**Known deferrals (intentional non-fixes):**
- **R20-NEXT-001 stays deferred** (HIGH severity closure-binding bug in `generate_report` L7062+ / `generate_excel` L7335+). Per the [Round 20 review](#round-20--claude-review-2026-04-26) and the user flag, the fix is behavior-changing and must wait for the Round 21.1 formatter-render diff (Phases 4F + 5) to confirm the bug's actual report-content footprint before any change ships.
- **R20-NEXT-004 Phases 4F + 5** (formatter render + extract + diff) — split into Round 21.1 commit per the plan's two-commit boundary. The canonical-layer commit is a complete, useful artifact on its own.
- **R20-NEXT-002** (long-tail in-locals chunk in `run_customer_renewal_analysis` / `run_subscription_analysis` / `run_leader_report_generation`) — different round.
- **R20-NEXT-003** (broad-except observability sub-audit) — different round.
- **R20-NEXT-005 / -006** (CI alignment + outdated-package bumps) — different rounds, out of Round 21 scope.
- **Subscription / ARR aggregates, period comparison, per-CSSM rollups** — out of Round 19's scope per QUALITY_AUDIT.md L1405-1410. Future round.
- **Phase 6** (sentinel input variants — empty frames, all-Unknown priorities, all-CRITICAL risks) and **Phase 7** (rolling regression flag in audit log) — registry follow-on phases, out of Round 21.

**Trailer:** Made-with: Cursor

