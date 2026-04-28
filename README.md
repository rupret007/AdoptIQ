# AdoptIQ Desktop (macOS and Windows)

**Version 1.0.4** — Version and build are shown in the app footer (e.g. v1.0.4 build 1).

AdoptIQ generates renewal reports (Word, Excel) from CSOne adoption barriers, support cases, and related data. No Python or development tools are required for end users.

### What's New since v1.0.3 (Quality & Hardening)

These improvements are now packaged as **v1.0.4 build 1**; v1.0.3 was the previous user-facing release. Round-by-round audit detail lives in `QUALITY_AUDIT.md`.

#### CSOne Knowledge Corpus (Round 17 / 17.1 / 17.2 / 35 / 36, opt-in)
- **NEW (Round 36): OneDrive sync presence as auth signal — no MSAL, no Microsoft Graph at runtime.** Round 36 retired the MSAL/Graph runtime path entirely (the default Microsoft Graph client ID required Cisco tenant admin-consent which is not granted; the OneDrive desktop client already handles that auth flow and produces the same files on disk). To keep the corpus current you just sign in to your IT-managed OneDrive account and sync the `AI Projects/AdoptIQ_CSOne_Reports` folder under `~/Library/CloudStorage/OneDrive-Cisco/`. AdoptIQ probes that folder, surfaces the result on the analyze-page panel ("OneDrive synced — Daily refresh enabled"), and re-indexes from the local mirror every 24 hours. There is no Connect / Sign in / device-code modal anywhere in the UI.
- **NEW (Round 35): Native Knowledge Corpus — baked at build time, refreshed daily.** The `AI Projects/AdoptIQ_CSOne_Reports` share is hardcoded into the build (no per-user URL configuration) and indexed during `build_mac_dmg.sh` via `scripts/bake_corpus.py`. The four ship-ready artifacts (`corpus.db.enc`, `sentinel.json`, `corpus.db.salt`, `corpus.sentinel.lock.json`) ride inside the .app under `Resources/baked_corpus/`. On first launch `corpus_bootstrap` copies them into `~/Library/Application Support/AdoptIQ/knowledge/` (mode `0o600`); subsequent launches leave the user's existing corpus alone. A daemon thread wakes every hour and triggers a local re-index from the OneDrive sync mirror whenever the folder is synced AND 24 hours have elapsed since the last successful refresh. The encrypted DB is updated atomically (sibling `.tmp` + `os.replace`) so a mid-refresh crash leaves the prior corpus byte-identical. The analyze-page panel is labelled **"AdoptIQ Knowledge Corpus"** and is informational only (no buttons, no URL paste field).
- **NEW (Round 17.2): SharePoint pull, no OneDrive sync required.** AdoptIQ now fetches the `AI Projects/AdoptIQ_CSOne_Reports` share directly from SharePoint via Microsoft Graph using a one-time device-code sign-in. Refresh tokens are kept in the macOS Keychain (with a 0600-permission fallback) so the operator only signs in once per machine. The Admin Corpus tile shows a "Sign in to SharePoint" CTA when authentication is required, the signed-in UPN + token expiry once authenticated, and a "Refresh SharePoint corpus" button that re-pulls and re-indexes. OneDrive sync is now a **fallback** (still supported, auto-discovered across the standard `~/Library/CloudStorage/...` and `~/OneDrive - Cisco/...` paths), and the user's `~/Downloads` folder remains the third source.
- **Now ingests OneDrive *and* your Downloads folder (Round 17.1).** The corpus indexer used to silently drop CSOne TAC exports because the workbook layout has banner rows above the header and a sheet name (`AdoptIQ Enhanced Premium Collab`) that the old router didn't match. The Round 17.1 indexer auto-detects three layouts (`csone_export`, `adoptiq_data`, plain) and reads the header from the right row in each. It also walks the runtime user's `~/Downloads` (top level only, no recursion), filtered to an `AdoptIQ*` filename allow-list, so previously generated AdoptIQ Word and Excel reports become first-class corpus sources alongside the SharePoint / OneDrive feed. Skip Downloads with `CSONE_INCLUDE_USER_DOWNLOADS=false`; override the path with `CSONE_USER_DOWNLOADS_DIR`.
- **Per-source breakdown on the admin Corpus tile (Round 17.1 + 17.2).** The status payload records per-source counts (SharePoint cache, OneDrive sync, Downloads: files seen, parsed, skipped, failed, chunks added) so the admin UI shows exactly where the rows came from.
- **Persistent customer & troubleshooting knowledge.** AdoptIQ builds a local, encrypted index from the SharePoint share `AI Projects/AdoptIQ_CSOne_Reports` (with OneDrive + Downloads as fallbacks), so Ask AI, report pre-fill, the new Customer 360 page, and the new Troubleshooting Playbook page have rich historical context — even if you haven't just run a fresh report.
- **Customer 360 page (`/customer/<name>`).** Server-side rendered cases timeline, recurring barriers, top resolutions, and a sentiment direction badge for any indexed customer.
- **Troubleshooting Playbook page (`/playbook`).** Browse recurring barriers and historical resolutions across the corpus by technology, theme, or free-text query.
- **Historical Context in reports.** Executive and Leader reports get a "Historical Context" section listing prior occurrences of focused customers and their recurring themes, sourced from the corpus and cited by source filename.
- **Admin Corpus tile.** Live status (file counts, schema version, last-indexed timestamp), CSRF-protected refresh action, SharePoint sign-in CTA + signed-in UPN + token expiry, and a clear "sign in to SharePoint / OneDrive not synced" banner when the corpus is unavailable.
- **Encrypted-at-rest, SharePoint-ACL-gated.** The local cache lives under `~/Library/Application Support/AdoptIQ/knowledge/`, encrypted with AES-256-GCM. The encryption key is derived (HKDF-SHA-256) from a sentinel file delivered inside the share — Microsoft enforces the ACL, whether the file arrives via the Round 17.2 Graph pull or the OneDrive sync, so no customer PII ever ships in the installer. File mode `0600`; plaintext is scrubbed on close.
- **Feature flag.** Off by default. Set `CORPUS_KNOWLEDGE_ENABLED=true` to opt in. The corpus is built lazily on first launch in a background thread; first-run indexing of typical corpora completes in well under a minute. Degrades gracefully when SharePoint sign-in is pending, OneDrive is not synced, or the feature flag is off — every existing flow continues to work without the corpus.
- **Validator pipeline.** Every corpus chunk that reaches the LLM, the Customer 360 page, the Playbook page, or the report pre-fill helper is filtered through the Round 16 narrative validator (`ai_narrative_validator.is_corpus_chunk_safe`) so a hostile corpus entry cannot inject HTML/JS or prompt-injection payloads.

#### Reports — accuracy and polish
- **Cross-format consistency.** Word and Excel reports share single-source-of-truth modules (`canonical_metrics.py`, `report_export_schema.py`, `report_word_styling.py`) so headline KPIs — customer count, ARR at risk, high-risk count, P1 open, top barriers, date-range labels — match exactly across both formats. Regression tests pin the parity.
- **Excel polish.** Every report sheet now renders as a native Excel Table with banded rows, frozen header, autofilter, ISO date format, and consistent conditional formatting (3-color risk scale, severity highlights, status pills, days-open data bar) via `report_export_styling.py`. Title rows and offsets (`startrow`) are honored end-to-end.
- **Word styling polish.** Top-N tables (Focus Accounts, Defects by Customer, etc.) use a banded style with consistent header colors and reproducible formatting via `report_word_styling.add_banded_top_n_table`.
- **Sort determinism.** Top-N rankings (focus accounts, top barriers, customer risk lists) now use stable name-based tiebreakers, so identical input always produces byte-identical rankings across runs.
- **Currency precision.** Audited every ARR / financial path for intermediate-rounding bugs; confirmed precision is preserved until final display formatting.

#### Ask AI / AI Insights — grounding and hardening
- **AI narrative grounding validator.** New `ai_narrative_validator.py` automatically rejects model output that (a) emits numbers not present in the briefing data, (b) references customers, quarters, or other entities the model wasn't shown, or (c) tries to inject HTML/JS. On validation failure, the narrative is replaced with a placeholder rather than rendered as fact.
- **Lightweight evaluation harness.** Fixture-driven AI insights eval suite pins validator behavior against grounded, hallucinated-number, HTML-injection, and invented-entity model responses so regressions are caught loudly.

#### Quality harness
- **Test suite:** **2570 passed / 2 skipped** (up from 2106 after Round 17) — Rounds 18-30 added 464 new tests across report determinism, multi-currency arithmetic safety, AI grounding, AdoptIQ Intelligence indexing, admin CSRF & audit-mirror hardening, connectivity-diagnostics namespace cleanups, theme unification + branded error pages, the Round 29 token-polish + error-handler cleanup pass, and the Round 30 logic / accuracy / cross-report parity sweep.
- **`make verify`:** end-to-end gate wraps `pytest`, `ruff check`, `bandit -ll`, and `pip-audit -r requirements.txt`. All gates clean and back-to-back idempotent.
- **Pipeline-boundary logging.** All three formatters log entry/exit at INFO with manager-digest only — never raw PII. Round 17 logs corpus indexing at INFO with file-id / sha256-prefix only; customer names never appear above DEBUG.
- **Audit log.** Running findings, fixes, and residual risks are tracked in `QUALITY_AUDIT.md` (Round 14 → Round 30 sections). Older `CODE_REVIEW_*.md` files are frozen point-in-time snapshots; consult `QUALITY_AUDIT.md` for current state.

#### Quality & Hardening — Rounds 18-30

These are the round-by-round summaries of every quality, security, and reporting-honesty pass since Round 17. Full audit detail (file paths, line numbers, deferred items, and rationale) lives in `QUALITY_AUDIT.md`.

- **Round 18 — One-night autonomous QA sweep.** Sort-determinism tie-breakers in `executive_intelligence_formatter` and `leader_report_generator`; openpyxl no-warning open + python-docx structural round-trip pins; non-finite-float → "N/A" placeholder coverage; `is_corpus_chunk_safe` fail-closed + structured WARNING; prompt-injection regex widened to catch the canonical "ignore all previous instructions" phrase. **+43 tests** (2106 → 2149).
- **Round 19 — Renewal Excel risk-component canonicalization.** Renewal Excel risk components now match the Word/canonical view; synthetic-recommendation suppression; lifecycle helper used in renewal cases. **+10 tests** behavioural pins.
- **Round 20 — Briefing prompt customer-format SSoT.** Customer-format passes all keys through a single source of truth; prompt template no longer extrapolates quantification.
- **Round 21 — Currency-unknown no-USD-default contract.** When `CURRENCY_CODE` resolves to `UNKNOWN` the analyzer no longer silently defaults to USD; multi-currency disclosure is emitted instead. **+5 tests** including handoff R21.1.
- **Round 22 — Admin CSRF + audit-mirror hardening.** Admin destructive endpoints require CSRF; audit-mirror file rotation + size cap + mode `0600`; sensitive endpoints include status & diag.
- **Round 23 — Connectivity-diag namespace + closure-binding fix.** Explicit `ctx` dict to fix a closure-binding bug at the diag boundary; connectivity-diag error-kind namespace cleanup; leader/renewal handoff items closed (R22-NEXT-LEADER, R21-NEXT-LEADER, R22-NEXT-001/002/003).
- **Round 24 — Compact briefing external-intel inclusion.** Compact briefing now includes external-intel; Round-4 dedupe of BEMS join IDs; evidence sorted before `head`.
- **Round 25 — Reports numerically honest (R25A/B/C).** R25A: currency consistency (multi-currency disclosed on every export, USD default removed). R25B: risk-band drift validator (LLM rewrite that swaps "HIGH" → "CRITICAL" is rejected). R25C: ARR-NaN guard (a single NaN-bearing ARR row no longer surfaces as "$nan" in the rendered report).
- **Round 26 — AdoptIQ Intelligence (user-facing).** `corpus_indexer` extended and renamed UI-side to "AdoptIQ Intelligence". Indexes numerical KPIs and CSOne case data into structured SQLite rows; admin tile (full status card) + main-page banner (compact, polled), both wired to `/api/intel/status` and `/api/intel/refresh`. Default-off `/api/intel/upload` accepts xlsx, xls, csv, docx, pdf with size/extension/path-traversal guards and writes to `~/.adoptiq/intel_uploads/`. Operator-diagnosable error tile marshals up to 5 rows of (file, reason, exception class) plus a total count. JSON-shaped 413 response for `/api/intel/*` paths. **+50 tests** including end-to-end xlsx → BM25 retrieval pin.
- **Round 27 — AI grounding gates on customer storyboard + portfolio summary.** R27-AI-GATE-CUSTOMER and R27-AI-GATE-PORTFOLIO route the per-customer storyboard and portfolio summary LLM outputs through `ai_narrative_validator.validate_narrative` before they reach `report_builder.parse_ai_output_and_add(...)`. Both gates substitute `GROUNDING_FAILURE_PLACEHOLDER` on failure (never raise), wrap the validator in `try/except Exception` so a validator regression never breaks the report, and respect a single rollback flag `ADOPTIQ_R27_LEGACY_AI_GATE=1`. **+13 tests** pinning source-shape, behavioral catches, and the R27-vs-R25 asymmetry contract.
- **Round 28 — Multi-currency arithmetic safety + determinism floor (stream A) AND theme unification + branded error pages (stream B).** Round 28 ran as two concurrent streams that landed in the same shipping vehicle.
  - *Stream A — Multi-currency arithmetic safety + determinism floor.* Formal contract docstring on `risk_scoring.RENEWAL_ARR_THRESHOLDS` requiring consumers to skip USD-basis compares (and emit a disclosure factor) when `is_multi_currency=True` OR `currency != currency_basis`. `advanced_renewal_analyzer` sweep verified all four currency-bearing _RAT[...] reads are gated; multi-currency branch now also emits a disclosure factor (was previously silent). `adoptiq_backend.py`: NaN-safe ARR sums (`pd.to_numeric(errors='coerce').fillna(0).sum()` parity for `arr_at_risk` / `arr_critical`); explicit `'CURRENCY_CODE' in df.columns` guard with structured WARNING (was silent except-Exception); deterministic stale-cases ranking (`sort_values(by=['_days_open','ID'], kind='stable')` instead of `nlargest`); deterministic `value_counts().head(N)` for severity / status / category / customer-count distributions (`sort_index().sort_values(ascending=False, kind='stable')`). **+25 tests** including a Phase 3b byte-identical determinism pin that SHA-256 fingerprints the JSON payload of each Round 28-affected field across 10 randomly-shuffled runs.
  - *Stream B — Theme unification + branded error pages + new-report audit.* `templates/progress.html` and `templates/previous_reports.html` were rebuilt as Jinja children of `base.html` (was inline f-strings on a hardcoded light palette that ignored the toggle). `templates/404.html` and `templates/500.html` were wired through new `@app.errorhandler(404)` / `@app.errorhandler(500)` so unknown URLs and unhandled exceptions render the branded page (was Werkzeug's bare default). `static/js/theme-toggle.js::detectPreferredTheme()` now consults `window.matchMedia('(prefers-color-scheme: light)')` for first-time visitors (the comment claimed this for several rounds; the code never delivered). Orphan `static/css/style.css` (448-line parallel theme system that drifted from base.html and was never `link`-ed) was deleted; tombstone test pins it stays gone. New-report audit confirmed every report-generation surface still extends `base.html` and inherits the dark/light toggle.
- **Round 29 — Theme token polish + error-handler cleanup (build4).** Closes the 1 medium and 5 low findings Claude Code surfaced in its read-only review of Round 28. `base.html` `:root` and `[data-bs-theme="dark"]` blocks now declare `--success-bg` / `--success-fg` / `--warning-bg` / `--warning-fg` / `--danger-bg` / `--danger-fg` / `--info-card-border` (and a `--info-tint-bg`) so child templates token-reference status tints instead of hardcoding `rgba(...)` literals derived from brand hexes. `progress.html` and `previous_reports.html` migrated off the inline `rgba` literals onto those tokens. The early-paint inline `<script>` in `base.html` now consults `prefers-color-scheme` synchronously so a first-visit on a light-mode OS no longer flashes dark before `theme-toggle.js` flips it. `app_simple.progress()` swapped its three inline `<h1>Analysis not found</h1>` f-string returns for `abort(404)` so the branded handler renders, dropping the now-dead `import html as html_module`, `analysis_id_safe`, `analysis_id_json`, and `csrf_token_json`. `progress.html` switched its JS-context `analysis_id` and `csrf_token_value` interpolation from `|safe` against pre-jsonified strings to `|tojson` (Jinja-blessed pattern). The Round-28 orphan-CSS test was relaxed from `"static/css/style.css"` to `"css/style.css"` to also catch a `url_for('static', filename='css/style.css')` regression. Shipped as `AdoptIQ-v1.0.4-build4.dmg`. **+12 tests** across branded-404, `|tojson`, semantic-token-presence, prefers-color-scheme early-paint, and inline-status-rgba absence flanks.
- **Round 32 — Silent data-loss fixes + admin auto-start + Intelligence default-on (build7).** Diagnosis of a 32 KB / 40 KB "thin report" run (`~/.adoptiq/adoptiq.46198.log`) revealed four cascading silent failures. **Phase 1.A — Row-contract auto-remap.** `data_contracts.validate_row_contract` now case-folds, strips whitespace/BOM, and strips Snowflake namespacing prefixes (`AB_C__`, `PULSE_C__`, `CASE_C__`, `BARRIER_C__`, `ACCOUNT_C__`, `CSONE_C__`) and Salesforce `__C` / `_C` suffixes when matching aliases. `annotate_with_contract` then copies each matched column under its canonical slot name (`BU_NAME → customer`, `PULSE_RATING__C → rating`, …) so downstream code that reads `df["customer"]` stops silently emitting empty sections. Non-empty frames whose contract still fails are escalated to `df.attrs['fetch_error'] = "schema_drift:..."` (with `fetch_error_kind='schema_drift'`) so the existing partial-data banners fire — empty frames are soft-passed (zero rows is a legitimate query result). **Phase 1.B — matplotlib bundled.** Removed `matplotlib` and `PIL` from `adoptiq_mac.spec` `excludes` and added the full pyplot/Agg/PIL chain to `hidden_imports`; `app_simple.py` calls `matplotlib.use("Agg")` at module load so the headless backend wins before any worker thread imports `pyplot`. The "Matplotlib not available - charts will be skipped" log line is gone in the packaged .app. **Phase 1.C — CSConsole tech filter fuzzier.** `_filter_tech_text_enhanced` now falls back to a substring synonym map (`_ALL_BUCKET_FUZZY_TERMS`) for `All <X>` filters when none of the strict per-bucket regexes match; an INFO log fires on every fuzzy hit so the next thin-report incident has a clear breadcrumb. Specific buckets (e.g. `Webex Calling`) keep their strict semantics. **Phase 2.D — Admin Console auto-start.** New `_start_admin_server_in_thread()` in `app_simple.py` spawns `enhanced_admin_dashboard_v2.admin_app` on a daemon thread (default `127.0.0.1:5152`) so the packaged .app actually exposes the admin UI without a second process. Idempotent (`sys._adoptiq_admin_started`), short-circuits under pytest, and quietly downgrades on `OSError` (port already bound). `templates/base.html` got a header link so users can reach it from any page. **Phase 2.E — Persistent settings + Intelligence UI toggle.** New `adoptiq_settings.py` reads/writes `~/Library/Application Support/AdoptIQ/settings.json` with mode `0600`, parent dir `0700`, atomic `os.replace` writes, and an allow-list (currently `corpus_knowledge_enabled`). `app_simple.py` startup hook overrides `Config.CORPUS_KNOWLEDGE_ENABLED` from settings.json after the `Config` import. New `POST /api/settings/intelligence` (CSRF-protected via the same dual-path as `/api/corpus/refresh`) persists the toggle, mutates `Config` in-process, and triggers `corpus_bootstrap.request_refresh` on enable. `templates/analyze.html` Intelligence card grew a Bootstrap switch wired to that endpoint via `static/js/intel_status.js::bindEnableToggle`. Resolution order: **`settings.json` > env var > config default**. **Phase 2.F — Default-on.** `config.py` flipped `CORPUS_KNOWLEDGE_ENABLED` default from `'false'` to `'true'`; the existing four tests that gate Intelligence already monkeypatch the value explicitly, so they stayed green untouched. Shipped as `AdoptIQ-v1.0.4-build7.dmg`. **+52 tests** across the seven `tests/test_round32_*.py` files (contract auto-remap, matplotlib bundling, fuzzy tech filter, settings module round-trip + permissions, intelligence toggle endpoint, settings-overrides-Config precedence, admin auto-start safety).
- **Round 30 — Logic, accuracy & cross-report parity sweep (build5).** Closes all 14 findings from Claude Code's read-only logic / accuracy review of Round 29 (4 HIGH, 6 MEDIUM, 3 LOW, 1 INFO). Two findings (H4 portfolio-gate-fails-open-on-empty-allowlist, M6 validator-exception-accepts-LLM-output) were Round-27 regressions and shipped as one-line fixes. The remaining twelve closed pre-existing accuracy / parity gaps: H1 multi-currency disclosure parity (leader title-page advisory mirrors the executive ARR Exposure phrasing); H2 BEMS exception classification + degradation banner via `error_classifier.classify_exception` (was silently falling through to basic counts under the same heading); H3 compact recent-window now uses `pd.to_datetime(..., utc=True)` for tz-aware UTC compare (was stripping offsets); M1 leader resolved-AB and completed-AP counts route through new `canonical_metrics.count_closed_barriers` / `count_action_plan_completed` helpers (was inline `str.contains(r'closed|resolved|complete')`); M2 incident-storage truncation flags now reach the user-visible disclosure on every renderer; M3 compact "Customers with Barriers" uses canonical `count_customers_with_barriers` for byte-stable parity; M4 `data_source_validator.has_optional_fetch_errors` + Partial Data banner emitted from each report writer; M5 new `_assert_arr_attrs` guard enforces the multi-currency `attrs` contract at every ARR-consumer entry (warn-by-default, strict via `ADOPTIQ_STRICT_MODE=1`); L1 `datetime.now(UTC)` in `export_adrian_snowflake_records.py`; L2 `_assert_datetime_columns_tz_aware` in `snowflake_prefetch.py` + `ALTER SESSION SET TIMEZONE = 'UTC'` on connect; L3 11 inline `x/y if y > 0 else 0` ternaries in `adoptiq_backend.py` replaced with `_safe_div(num, den, default=...)` plus a regex regression scan; I1 (reframed) backend was already correct (the concentration "skipped" note was being populated with `not_comparable_across_currencies: True`) — the gap was renderer-side, so leader title-page advisory and executive ARR Exposure now surface the note via the new `concentration_note_text` helper. Shipped as `AdoptIQ-v1.0.4-build5.dmg`. **+75 tests** across the 14 finding-specific files plus the cross-report parity extension (multi-currency disclosure phrase, concentration-note surfacing, attrs contract enforcement, behavioural pin).

### What's New in v1.0.3

#### New Features
- **Ask AI (Enhanced):** Ask natural-language questions about your portfolio with dramatically richer context. The AI now receives live Snowflake data, period-over-period trend analysis, barrier creation/resolution velocity, historical report baselines, portfolio intelligence, barrier aging analysis, and active service incidents — producing insights that combine operational and external data in ways that are easier to ground and validate.
- **External Intelligence:** Live incidents, bugs, and maintenances from status.webex.com and help.webex.com are fetched, stored historically in a local database, and displayed with search, export, and import capabilities. Ask the AI questions about any tracked intelligence.
- **Graceful shutdown:** Analysis status is automatically saved when the app exits, preventing data loss.

#### AI Intelligence Engine
- **Trend Analysis:** Compares current period metrics (barriers, pulse, action plans) against the previous period to show whether things are improving or worsening.
- **Barrier Velocity:** Tracks weekly barrier creation vs resolution rates to show if the team is keeping up with new issues.
- **Historical Baselines:** Scans past report Excel files to establish historical context, so the AI can identify significant changes from previous baselines.
- **External Intelligence Fusion:** Active service incidents and known bugs are automatically included in Ask AI context, enabling cross-domain insights (e.g., "Which customers are affected by the current outage?").
- **Enhanced Snowflake Intelligence:** Ask AI uses additional Snowflake context and schema-aware retrieval to improve grounded customer, engagement, and activity analysis.
- **Portfolio Intelligence Engine:** Computes derived analytics including customer concentration risk (HHI index), CSSM workload distribution, and repeat offender identification (accounts with both barriers and cases).
- **Cross-Report Trend Analysis:** Compares metrics across multiple past reports to surface trends in data volume, customer scope, and issue patterns over time.
- **Barrier Aging Analysis:** Computes how long each open barrier has been active, groups issues into aging buckets (0-30d, 30-60d, 60-90d, 90-180d, 180+d), and identifies the longest-standing stale barriers.
- **Severity Evolution Tracking:** Cross-report trends now track how severity distributions change over time (e.g., are P1 barriers increasing?).
- **Customer Recurrence Detection:** Identifies customers appearing in every historical report — chronic problem accounts requiring intervention.
- **Deeper Historical Mining:** Reports now extract individual barrier subjects and category distributions from past Excel files.
- **Enhanced System Prompt:** The AI uses a chain-of-thought analytical framework with cross-domain correlation, hidden pattern detection, and operational prioritization.
- **Software Defect & BEMS Extraction:** Ask AI automatically scans support cases and adoption barriers for CSC defect IDs and BEMS escalation IDs, groups them by customer, and surfaces them to the AI for correlation analysis.
- **Feature Request Detection:** Barrier categories and subjects are scanned for feature request patterns, surfacing product feedback themes across the portfolio.
- **Portfolio-Aware External Intelligence:** When asking about incidents or bugs, the AI receives portfolio context (customer names, technologies, active cases) so it can correlate external disruptions with internal customer impact.
- **Citation-Ready Data:** All briefing data now includes record identifiers (AB-IDs, SP-IDs, AP-IDs, Case IDs, incident IDs, CSC IDs, BEMS IDs) so the AI can cite specific sources in its analysis, making reports verifiable and actionable.
- **Smart Briefing Truncation:** When briefing data exceeds the LLM context window, a priority-based truncation system preserves the most critical sections (BEMS escalations, software defects, and high-signal operational context) rather than blindly cutting from the end.
- **Few-Shot Analytical Examples:** Prompt templates include example analyses demonstrating the expected depth of cross-correlation (incident impact, pattern detection, and issue concentration).

#### Stability & Robustness
- **Leader Report Excel fix:** Leader report Excel downloads now work correctly (filename sanitization was causing 404s).
- **Download resilience:** Report downloads survive app restarts — status is loaded from disk when not in memory.
- **Excel export stability:** Fixed datetime timezone handling and NaN/NaT/Inf sanitization that could crash or corrupt Excel exports.
- **Leader report performance:** Eliminated redundant Snowflake queries during leader report generation (2 fewer round-trips).
- **Report error handling:** All report types now guarantee error status is recorded and persisted, even on unexpected failures.
- **Startup cleanup:** Analyses left in "running" state from a previous session are automatically marked as cancelled on restart.
- **Memory management:** Old analysis entries are automatically evicted (keeps most recent 50) to prevent unbounded growth.
- **Database safety:** All Snowflake query functions handle None connections gracefully; connection leaks fixed with proper finally blocks.
- **Path traversal hardening:** Download routes strictly verify file paths are within the outputs directory.
- **Error message sanitization:** Internal stack traces and file paths are never exposed to users.
- **Status API security:** Internal file paths are filtered from the status API response.

#### UI & Progress
- **Granular progress notifications:** All 5 report types show detailed, real-time status updates during generation — the leader report alone has ~20 sub-steps with per-team-member progress.
- **Step timeline UI:** The progress page shows a visual pipeline of completed steps (with checkmarks), the current step (with a spinner), and a live elapsed-time clock.
- **Robust polling:** Status polling handles network errors with retry limits, validates server responses, and has a 30-minute safety timeout.
- **XSS prevention:** All dynamic content in the progress page and subscription search is properly escaped.

#### Testing
- **Test suite:** Comprehensive pytest suite covering data processing, report formatting, security (XSS, CSRF, path traversal, input validation), error handling, NaN/None/Inf safety, API correctness, and edge-case defense.
- **Debug log cleanup:** Removed noisy DEBUG-prefixed log statements; downgraded to debug level for cleaner production logs.

### What's New in v1.0.2

- **Subscription ID or Customer Name (all report types):** One optional field for Comprehensive, Compact, and Customer Renewal. Enter a subscription ID (e.g. **Sub12345**) or type a customer name and select from the list. Subscription IDs start with *Sub* then numbers; anything else is treated as a customer name with typeahead so the report uses the exact Snowflake name.
- **Single-customer reports:** Run a report for one customer on any type: Comprehensive, Compact, or Customer Renewal. When you enter a subscription ID or customer name (and select from the list), the report is for that customer only—manager selection is not used. Technology still applies. For Customer Renewal you can omit manager when you provide customer name or subscription ID.
- **Customer renewal by name:** Single-customer renewal can be run by typing a customer name and selecting from a search list (typeahead). The report uses the exact Snowflake BU_NAME so it matches CSOne data.
- **Validation:** Customer name is validated (length, safe characters) on search and when starting analysis; names like "Dropbox" are no longer blocked.
- **Fixes:** Form submission for single-customer renewal now correctly uses the customer-name path (renewal_type normalization). Subscription lookup now includes CSSM email for team context.

### What's New in v1.0.1

- **Security:** Path traversal protection for CSOne file paths; download route validates filenames
- **Logging:** Replaced `print()` with structured logging in backend for better diagnostics
- **Validation:** Centralized days input validation (1–365) across all report types
- **Admin dashboard:** Secret key is set at build time (embedded via `ADOPTIQ_ADMIN_SECRET_KEY` in secrets.env).

---

## Quick Start

Use the packaged app for your platform. No Python or development tools are required for end users.

### Install

#### macOS
1. Open the **AdoptIQ** DMG (e.g. `AdoptIQ-v1.0.4-build1.dmg`).
2. Drag **AdoptIQ.app** onto the **Applications** shortcut in the DMG window.
3. In the same DMG window, double-click **Unblock AdoptIQ.command**.
   - This clears macOS Gatekeeper's quarantine flag on the installed app and launches AdoptIQ.
   - You only need to do this the first time after installing or updating.
   - If macOS asks "Allow Terminal to open this script?", click **Open**.
4. Eject the DMG. From now on, launch **AdoptIQ** from Applications or Spotlight.

> **Why the Unblock step?** AdoptIQ is signed adhoc (no Apple Developer ID), so macOS quarantines it when downloaded. On Apple Silicon Macs, quarantined adhoc-signed apps are silently killed at launch (the Dock icon bounces once and the app exits with no error window). The helper simply runs `xattr -dr com.apple.quarantine /Applications/AdoptIQ.app` and then `open /Applications/AdoptIQ.app`. You can run those two commands manually in Terminal instead if you prefer.

#### Windows
1. Open the folder containing `AdoptIQ.exe`, `README.md`, and `build_info.txt`.
2. Double-click **AdoptIQ.exe**.
3. If Windows SmartScreen appears, click **More info** and then **Run anyway**.

### Run

1. Double-click **AdoptIQ.app** (macOS) or **AdoptIQ.exe** (Windows) to launch.
2. A terminal window should not appear in standard packaged builds.
3. Your browser will open automatically to **http://localhost:5151** (or open it manually if needed).
4. Use AdoptIQ as needed, keeping the server window open during use.

### Verbose Debug Mode (optional)

- Enable detailed runtime diagnostics with environment variable `ADOPTIQ_VERBOSE_DEBUG=1`.
- You can also toggle verbose mode at runtime from the admin dashboard (`http://localhost:5152`) using the **Debug Controls** section.
- Runtime API is also available at `GET/POST /api/debug/verbose` (localhost app/admin surface) for scripted troubleshooting.
- The same admin section shows a Snowflake query counter and includes a reset button to baseline each report flow.
- Verbose mode is intended for troubleshooting and query optimization validation; disable it for normal use.
- Detailed rollout notes are in `SNOWFLAKE_OPTIMIZATION.md`.

### Ask AI Grounded Mode (default enabled)

- Ask AI now uses a retrieval-first grounded pipeline that builds a bounded evidence context and validates citations before returning claims.
- Source-backed claims include explicit source IDs (for example AB IDs, Case IDs, incident IDs, bug IDs, BEMS/CSC IDs).
- Claims without verifiable citations are moved to an "Evidence Gaps" section instead of being presented as facts.
- Retrieval is intent-aware: Ask AI fetches only relevant dataset bundles for the question to reduce unnecessary Snowflake queries.
- Grounded retrieval reuses run-scoped prefetch context to avoid repeating the same Snowflake fetches in a single analysis run.
- Rollback toggle: set `ADOPTIQ_ASK_AI_V2=0` to force legacy Ask AI behavior.

### CSOne Knowledge Corpus (Round 35 → Round 36: native; OneDrive sync as auth signal)

- Round 35 made the corpus a **native** part of every install: the `AI Projects/AdoptIQ_CSOne_Reports` share is indexed at build time and bundled inside the .app, so a freshly-installed AdoptIQ has the corpus loaded immediately on first launch.
- **Round 36 retired the MSAL/Graph runtime path entirely.** AdoptIQ no longer depends on `msal` / `keyring` / device-code OAuth at runtime; the OneDrive desktop client handles SSO/MFA/admin-consent and AdoptIQ verifies by `Path.is_dir()` + counting non-empty files at `Config.CSONE_ONEDRIVE_FOLDER`. The `/api/corpus/sharepoint/signin|signout|refresh` routes were removed; the analyze-page panel is informational-only.
- A daily refresh worker keeps the corpus current — every hour the worker wakes, checks the OneDrive sync presence, and re-indexes from the local mirror when the 24-hour window has elapsed AND the folder is synced. Refreshes use the same atomic write the bake script does, so a mid-refresh crash never corrupts the prior corpus.
- The local index lives at `~/Library/Application Support/AdoptIQ/knowledge/` (macOS) and is encrypted with AES-256-GCM. Build-time bake uses the auto-minted local sentinel; runtime refreshes use the same key (Round 34 / A1 sentinel pinning prevents accidental re-keying when the source sentinel changes).
- Build-time controls: `ADOPTIQ_BAKE_CORPUS=0` (or `--no-bake`) skips the bake; the build still produces a working .app and the runtime falls back to the legacy "first-launch refresh" path. The bake script accepts `--source <dir>` (preferred), `--offline-fixture <dir>` (back-compat alias), or `ADOPTIQ_BAKE_FIXTURE_DIR=<dir>` env (used by `build_mac_dmg.sh` to point at the build operator's local OneDrive sync mirror); falls through to `Config.CSONE_ONEDRIVE_FOLDER` when none of the above are set. The legacy `--auth-mode device_code` and `--share-url` flags are accepted for back-compat but are no-ops.
- **End-user setup:** sign in to your IT-managed OneDrive account in the OneDrive desktop client and sync the `AI Projects/AdoptIQ_CSOne_Reports` folder under `~/Library/CloudStorage/OneDrive-Cisco/`. AdoptIQ detects the folder automatically, shows **"Active • OneDrive synced"** on the analyze-page panel, and refreshes daily from the local mirror.
- Initial indexing runs in a background thread on first launch; the rest of the app remains responsive. The analyze-page Knowledge Corpus panel renders one of seven states (`baked_synced`, `baked_not_synced`, `fresh_indexing`, `fresh_not_synced`, `refreshing`, `refresh_failed`, `unknown`). The admin Corpus tile shows the per-source breakdown (OneDrive, Downloads, intel_uploads), schema version, and last-indexed timestamp.
- Configure source visibility:
  - `CSONE_ONEDRIVE_FOLDER` — override the OneDrive sync path (auto-discovered from the standard `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports` and `~/OneDrive - Cisco/AI Projects/AdoptIQ_CSOne_Reports` candidates).
  - `CSONE_INCLUDE_USER_DOWNLOADS` — set to `false` to skip indexing Downloads entirely (default `true`).
  - `CSONE_USER_DOWNLOADS_DIR` — override the Downloads path when reports land elsewhere (default `~/Downloads`).
  - `ADOPTIQ_INTEL_UPLOAD_ENABLED` — opt-in for the user-facing per-customer upload route (default `false`); admin-pre-seeded `~/.adoptiq/intel_uploads/` content is still indexed when the dir exists.
  - `ADOPTIQ_CORPUS_SHARE_URL` — preserved for documentation / bake logging; the runtime never opens this URL (it walks `CSONE_ONEDRIVE_FOLDER`). `ADOPTIQ_SHAREPOINT_FOLDER_URL` is a back-compat alias.
  - **Retired in Round 36 (no-op if set):** `ADOPTIQ_SHAREPOINT_ENABLED`, `ADOPTIQ_SHAREPOINT_CLIENT_ID`, `ADOPTIQ_SHAREPOINT_AUTHORITY`, `ADOPTIQ_SHAREPOINT_CACHE_DIR`, `ADOPTIQ_SHAREPOINT_MAX_FILE_BYTES` — the MSAL/Graph runtime path was removed; these settings have no effect.
- When the OneDrive folder is not synced, the feature flag is off, or the sentinel cannot be read, all surfaces degrade gracefully with a clear "OneDrive sync required" banner. Every existing flow continues to work; the baked snapshot is still served on read until a sync appears.
- Every corpus chunk is filtered through the Round 16 narrative validator before reaching the LLM or any rendered surface.

#### How do I keep the corpus fresh? (Round 36)

1. Sign in to the OneDrive desktop client with your Cisco-managed Microsoft account.
2. Sync the `AI Projects/AdoptIQ_CSOne_Reports` folder so it lands at `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports/`. AdoptIQ does not need any further action — the analyze-page panel will flip from "Active • baked snapshot" to **"Active • OneDrive synced"** within one poll cycle.
3. The daily refresh worker re-indexes from the local mirror every 24 hours. The encryption sentinel resolution order (Round 36) is: OneDrive sentinel (`<onedrive_root>/.adoptiq_corpus_sentinel.json`) → auto-minted local `sentinel.json` (mode `0o600`, parent `0o700`) under `~/Library/Application Support/AdoptIQ/knowledge/`.
4. There is no "Connect to Microsoft" / device-code prompt anywhere in AdoptIQ; the OneDrive client owns the auth flow.

**To quit:** Use **AdoptIQ → Quit AdoptIQ** from the menu bar (or ⌘Q). Closing only the terminal window may leave the app running in the background.

### If macOS blocks the app or it bounces in the Dock and exits

This happens because the build is adhoc-signed (no Apple Developer ID) and macOS has quarantined the installed app. Use the **Unblock AdoptIQ.command** helper from the DMG window — it removes the quarantine flag and launches the app.

If you no longer have the DMG mounted, run this once in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/AdoptIQ.app
open /Applications/AdoptIQ.app
```

If macOS shows "cannot be opened because Apple cannot check it for malicious software", click **Done**, then re-run the unblock command above.

> Older guidance suggested right-clicking **AdoptIQ.app** → **Open**. That workaround is unreliable for adhoc-signed apps on Apple Silicon — use the unblock command instead.

---

### Navigation

- **Dashboard / Run Analysis** — Start here; run new analyses from the dashboard buttons.
- **History** — Lists previous portfolio analyses. Use **View** to open the progress page for an analysis and download Word/Excel reports when completed.
- **Intel** — View tracked incidents, bugs, and maintenances from Webex status and help pages. Search, export/import historical data, and ask AI questions about intelligence.
- **Ask AI** — Ask natural-language questions powered by grounded retrieval and source-validated answers across portfolio, adoption barriers, support cases, pulse, priorities, action plans, trends, external intelligence, and historical context.
- **Customer 360** *(corpus opt-in)* — Visit `/customer/<name>` to see the historical case timeline, recurring barriers, top resolutions, and sentiment direction for any customer in the indexed corpus.
- **Playbook** *(corpus opt-in)* — Visit `/playbook` to browse recurring barriers and historical resolutions across the corpus by technology, theme, or free-text query.
- **Help** — Links and usage notes.

---

## Requirements

- **macOS or Windows** system
- **Internet access** (corporate VPN may be needed for Snowflake and CSOne)
- **Cisco CSOne** access to export reports

---

## Developer Branch Workflow (PC + Mac)

If you are actively coding on both machines, use the branch-first process in `BRANCH_WORKFLOW.md` to avoid collisions and keep `master` stable.

---

## CSOne Report

**Required report:** **AdoptIQ Enhanced/Premium Collab Summary** (Cases with Case Engagement)  
Link: https://csone.lightning.force.com/lightning/r/Report/00OfX000001Nnh2UAC/view?queryScope=userFolders  

**How to get the report:**
1. Click **Access latest AdoptIQ Export from CSOne** (link above the file upload) to open the shared folder in your browser
2. Download the most recent .xlsx file
3. Click **Browse** and select the downloaded file. Or run your own report in CSOne and browse to select it.

**Renewal reports:** CSOne data is required for support case counts. Without it (and without Snowflake), the report will show 0 cases.

---

## Where Files Are Stored

| Purpose | macOS Location | Windows Location |
|--------|------------------|------------------|
| Uploads (CSOne Excel files) | `~/Library/Application Support/AdoptIQ/uploads/` | `%APPDATA%\AdoptIQ\uploads\` |
| Generated reports (Word, Excel) | `~/Library/Application Support/AdoptIQ/outputs/` | `%APPDATA%\AdoptIQ\outputs\` |
| Analysis status (progress tracking) | `~/Library/Application Support/AdoptIQ/analysis_status.json` | `%APPDATA%\AdoptIQ\analysis_status.json` |
| External intelligence history | `~/Library/Application Support/AdoptIQ/external_intelligence.db` | `%APPDATA%\AdoptIQ\external_intelligence.db` |

In Finder, use **Go -> Go to Folder...** and enter `~/Library/Application Support/AdoptIQ/outputs` to open outputs.

---

## Found an Issue?

Please report it in the defect tracker so we can address it in future releases:

[App Defect Report Spreadsheet](https://cisco-my.sharepoint.com/:x:/r/personal/jestory_cisco_com/Documents/AI%20Projects/OUTBOX/_App_Defect_Report_Template.xlsx?d=wc71bd561722b491e9007cf6fe5ba92eb&csf=1&web=1&e=mD1Yga)

Thank you for your feedback and for helping us improve AdoptIQ!

---

## Troubleshooting

- **Port 5151 in use:** If you launch AdoptIQ while another instance is running, the app will show a dialog asking whether to quit the other instance and start, or cancel. From Terminal you can also run `lsof -i :5151` to find and stop the process.
- **App closes immediately / bounces in the Dock and exits (macOS):** This is almost always macOS Gatekeeper quarantine on an adhoc-signed build. Run the **Unblock AdoptIQ.command** from the DMG window, or in Terminal run `xattr -dr com.apple.quarantine /Applications/AdoptIQ.app` then `open /Applications/AdoptIQ.app`. See "If macOS blocks the app" above.
- **App closes immediately (other):** Launch from Terminal to see startup errors. On macOS, also check `~/Library/Application Support/AdoptIQ/startup_error.txt` for any uncaught Python exception.
- **Windows SmartScreen warning:** Click **More info** and then **Run anyway** if you trust the packaged build source.
- **Connection errors:** You may need to be on **corporate VPN**.
- **Snowflake/credential errors:** The app has embedded credentials. If you see credential errors, the build may have been created without a complete `secrets.env`. Contact your administrator for a properly configured build.
