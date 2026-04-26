# AdoptIQ Desktop (macOS and Windows)

**Version 1.0.3** — Version and build are shown in the app footer (e.g. v1.0.3 build 1).

AdoptIQ generates renewal reports (Word, Excel) from CSOne adoption barriers, support cases, and related data. No Python or development tools are required for end users.

### What's New since v1.0.3 (Quality & Hardening)

These improvements are on the active development branch and will land in the next user release. The packaged build is still **v1.0.3 build 1**; round-by-round audit detail lives in `QUALITY_AUDIT.md`.

#### CSOne Knowledge Corpus (Round 17 / 17.1 / 17.2, opt-in)
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
- **Test suite:** **2106 passed / 2 skipped** (up from 1949 after Round 16) — Round 17 added 157 new tests covering corpus indexer idempotency, schema-version skip, malformed-file resilience, AES-256-GCM crypto round-trip, file-mode `0600`, retriever facade contracts, Ask AI corpus grounding, report pre-fill historical context (Word + Excel), Customer 360 input validation and HTML escaping, Playbook CSRF + rate limiting, and the Admin corpus tile.
- **`make verify`:** end-to-end gate wraps `pytest`, `ruff check`, `bandit -ll`, and `pip-audit -r requirements.txt`. All gates clean and back-to-back idempotent.
- **Pipeline-boundary logging.** All three formatters log entry/exit at INFO with manager-digest only — never raw PII. Round 17 logs corpus indexing at INFO with file-id / sha256-prefix only; customer names never appear above DEBUG.
- **Audit log.** Running findings, fixes, and residual risks are tracked in `QUALITY_AUDIT.md` (Round 14 → Round 17 sections). Older `CODE_REVIEW_*.md` files are frozen point-in-time snapshots; consult `QUALITY_AUDIT.md` for current state.

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
1. Open the **AdoptIQ** DMG (e.g. `AdoptIQ-v1.0.3-build1.dmg`).
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
3. Your browser will open automatically to **http://localhost:5001** (or open it manually if needed).
4. Use AdoptIQ as needed, keeping the server window open during use.

### Verbose Debug Mode (optional)

- Enable detailed runtime diagnostics with environment variable `ADOPTIQ_VERBOSE_DEBUG=1`.
- You can also toggle verbose mode at runtime from the admin dashboard (`http://localhost:5002`) using the **Debug Controls** section.
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

### CSOne Knowledge Corpus (optional, off by default)

- The corpus integration mines the SharePoint share `AI Projects/AdoptIQ_CSOne_Reports` (Round 17.2, no local sync needed), with your synced OneDrive copy and your AdoptIQ-named files in `~/Downloads` as fallbacks (top level only, allow-listed by filename). It surfaces persistent customer / troubleshooting context across Ask AI, the Customer 360 page (`/customer/<name>`), the Troubleshooting Playbook page (`/playbook`), and the Historical Context section of Executive / Leader reports.
- Enable with `CORPUS_KNOWLEDGE_ENABLED=true`. The local index lives at `~/Library/Application Support/AdoptIQ/knowledge/` (macOS) and is encrypted with AES-256-GCM. The encryption key is derived from a sentinel file delivered inside the share, so the SharePoint ACL gates access whether the share arrives via Microsoft Graph or OneDrive sync — no customer PII ever ships in the installer.
- **Sign in once to SharePoint.** When `ADOPTIQ_SHAREPOINT_ENABLED=true` (default), the Admin Corpus tile shows a "Sign in to SharePoint" button on first launch. The flow uses Microsoft Graph device-code OAuth: AdoptIQ shows you a code, you open https://microsoft.com/devicelogin in any browser, paste the code, and AdoptIQ caches the refresh token in the macOS Keychain (with a 0600-permission JSON fallback) so future launches are silent. Press "Refresh SharePoint corpus" any time to re-pull.
- Initial indexing runs in a background thread on first launch; the rest of the app remains responsive. The Admin dashboard's Corpus tile shows status (per-source breakdown for SharePoint cache, OneDrive sync, Downloads), the signed-in UPN + token expiry, a sign-in CTA when needed, and a CSRF-protected "Refresh SharePoint corpus" action.
- Configure source visibility:
  - `ADOPTIQ_SHAREPOINT_ENABLED` — enable / disable the Microsoft Graph pull (default `true`).
  - `ADOPTIQ_SHAREPOINT_FOLDER_URL` — the SharePoint share URL (default `https://cisco-my.sharepoint.com/:f:/r/personal/jestory_cisco_com/Documents/AI%20Projects/AdoptIQ_CSOne_Reports`).
  - `ADOPTIQ_SHAREPOINT_CLIENT_ID`, `ADOPTIQ_SHAREPOINT_AUTHORITY` — overrides for advanced setups; defaults to the public Microsoft Graph PowerShell client + the common authority.
  - `ADOPTIQ_SHAREPOINT_CACHE_DIR` — override the local SharePoint cache directory (default `~/.adoptiq/cache/sharepoint_csone`).
  - `ADOPTIQ_SHAREPOINT_MAX_FILE_BYTES` — per-file download cap in bytes (default `52428800` / 50 MiB).
  - `CSONE_ONEDRIVE_FOLDER` — override the OneDrive fallback path (auto-discovered from the standard `~/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports` and `~/OneDrive - Cisco/AI Projects/AdoptIQ_CSOne_Reports` candidates).
  - `CSONE_INCLUDE_USER_DOWNLOADS` — set to `false` to skip indexing Downloads entirely (default `true`).
  - `CSONE_USER_DOWNLOADS_DIR` — override the Downloads path when reports land elsewhere (default `~/Downloads`).
- When SharePoint sign-in has not completed and OneDrive is not synced, when the feature flag is off, or when the sentinel cannot be read, all surfaces degrade gracefully with a clear "corpus unavailable — sign in to SharePoint or sync OneDrive" banner. Every existing flow continues to work.
- Every corpus chunk is filtered through the Round 16 narrative validator before reaching the LLM or any rendered surface.

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

- **Port 5001 in use:** If you launch AdoptIQ while another instance is running, the app will show a dialog asking whether to quit the other instance and start, or cancel. From Terminal you can also run `lsof -i :5001` to find and stop the process.
- **App closes immediately / bounces in the Dock and exits (macOS):** This is almost always macOS Gatekeeper quarantine on an adhoc-signed build. Run the **Unblock AdoptIQ.command** from the DMG window, or in Terminal run `xattr -dr com.apple.quarantine /Applications/AdoptIQ.app` then `open /Applications/AdoptIQ.app`. See "If macOS blocks the app" above.
- **App closes immediately (other):** Launch from Terminal to see startup errors. On macOS, also check `~/Library/Application Support/AdoptIQ/startup_error.txt` for any uncaught Python exception.
- **Windows SmartScreen warning:** Click **More info** and then **Run anyway** if you trust the packaged build source.
- **Connection errors:** You may need to be on **corporate VPN**.
- **Snowflake/credential errors:** The app has embedded credentials. If you see credential errors, the build may have been created without a complete `secrets.env`. Contact your administrator for a properly configured build.
