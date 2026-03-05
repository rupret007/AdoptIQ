# AdoptIQ Desktop (macOS and Windows)

**Version 1.0.3** — Version and build are shown in the app footer (e.g. v1.0.3 build 1).

AdoptIQ generates renewal reports (Word, Excel) from CSOne adoption barriers, support cases, and related data. No Python or development tools are required for end users.

### What's New in v1.0.3

#### New Features
- **Ask AI (Enhanced):** Ask natural-language questions about your portfolio with dramatically richer context. The AI now receives 16 data sections including live Snowflake data, period-over-period trend analysis, barrier creation/resolution velocity, ARR at risk calculations, historical report baselines, portfolio intelligence, barrier aging analysis, pulse-revenue correlation, and active service incidents — producing insights that combine financial, operational, and external data in ways never seen before.
- **External Intelligence:** Live incidents, bugs, and maintenances from status.webex.com and help.webex.com are fetched, stored historically in a local database, and displayed with search, export, and import capabilities. Ask the AI questions about any tracked intelligence.
- **Graceful shutdown:** Analysis status is automatically saved when the app exits, preventing data loss.

#### AI Intelligence Engine
- **Trend Analysis:** Compares current period metrics (barriers, pulse, action plans) against the previous period to show whether things are improving or worsening.
- **Barrier Velocity:** Tracks weekly barrier creation vs resolution rates to show if the team is keeping up with new issues.
- **ARR at Risk:** Calculates exactly how much revenue is tied to accounts with active barriers or support cases, broken down by severity tier.
- **Historical Baselines:** Scans past report Excel files to establish historical context, so the AI can identify significant changes from previous baselines.
- **External Intelligence Fusion:** Active service incidents and known bugs are automatically included in Ask AI context, enabling cross-domain insights (e.g., "Which customers are affected by the current outage?").
- **Enhanced Snowflake Intelligence:** Ask AI now queries additional Snowflake tables (COLLAB_ACCOUNT_SUMMARY, COLLAB_ARR_CON_SKU, ACCOUNTS_EXPIRED_LAST_MONTH) for renewal risk categories, contract expirations, customer tiers, and recently expired accounts.
- **Portfolio Intelligence Engine:** Computes derived analytics including customer concentration risk (HHI index), CSSM workload distribution, technology risk density (barriers per $1M ARR), and repeat offender identification (accounts with both barriers AND cases).
- **Cross-Report Trend Analysis:** Compares metrics across multiple past reports to surface trends in data volume, customer scope, and ARR over time.
- **Barrier Aging Analysis:** Computes how long each open barrier has been active, groups into aging buckets (0-30d, 30-60d, 60-90d, 90-180d, 180+d), and identifies the longest-standing stale barriers with their ARR exposure.
- **Pulse-Revenue Correlation (Silent Risk):** Identifies high-ARR accounts with critically low pulse scores — "silent risk" customers that may churn without obvious warning signs.
- **Renewal Probability Intelligence:** Queries RENEWAL_DATA for contract renewal probabilities and flags at-risk renewals below 70% confidence.
- **Severity Evolution Tracking:** Cross-report trends now track how severity distributions change over time (e.g., are P1 barriers increasing?).
- **Customer Recurrence Detection:** Identifies customers appearing in every historical report — chronic problem accounts requiring intervention.
- **Deeper Historical Mining:** Reports now extract individual barrier subjects, customer-level ARR breakdowns, and category distributions from past Excel files.
- **Enhanced System Prompt:** The AI uses a chain-of-thought analytical framework with cross-domain correlation, hidden pattern detection, and revenue-based prioritization.
- **Software Defect & BEMS Extraction:** Ask AI automatically scans support cases and adoption barriers for CSC defect IDs and BEMS escalation IDs, groups them by customer, and surfaces them to the AI for correlation analysis.
- **Feature Request Detection:** Barrier categories and subjects are scanned for feature request patterns, surfacing product feedback themes across the portfolio.
- **Portfolio-Aware External Intelligence:** When asking about incidents or bugs, the AI receives portfolio context (customer names, technologies, active cases) so it can correlate external disruptions with internal customer impact.
- **Citation-Ready Data:** All briefing data now includes record identifiers (AB-IDs, SP-IDs, AP-IDs, Case IDs, incident IDs, CSC IDs, BEMS IDs) so the AI can cite specific sources in its analysis, making reports verifiable and actionable.
- **Smart Briefing Truncation:** When briefing data exceeds the LLM context window, a priority-based truncation system preserves the most critical sections (financial data, BEMS escalations, software defects) rather than blindly cutting from the end.
- **Few-Shot Analytical Examples:** Prompt templates include example analyses demonstrating the expected depth of cross-correlation (revenue-risk, incident-impact, pattern-detection).

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

**Recommended:** Install from the **DMG** for the best experience. No additional development tools are required for end users.

### Install

1. Open the **AdoptIQ** DMG (e.g. `AdoptIQ-v1.0.3-build1.dmg`).
2. Drag **AdoptIQ.app** to **Applications**.
3. Eject the DMG. Launch **AdoptIQ** from Applications (or Spotlight).

### Run

1. Double-click **AdoptIQ.app** (macOS) or **AdoptIQ.exe** (Windows) to launch.
2. A terminal window should not appear in standard packaged builds.
3. Your browser will open automatically to **http://localhost:5001** (or open it manually if needed).
4. Use AdoptIQ as needed, keeping the server window open during use.

### Verbose Debug Mode (optional)

- Enable detailed runtime diagnostics with environment variable `ADOPTIQ_VERBOSE_DEBUG=1`.
- You can also toggle verbose mode at runtime from the admin dashboard (`http://localhost:5002`) using the **Debug Controls** section.
- The same admin section shows a Snowflake query counter and includes a reset button to baseline each report flow.
- Verbose mode is intended for troubleshooting and query optimization validation; disable it for normal use.
- Detailed rollout notes are in `SNOWFLAKE_OPTIMIZATION.md`.

**To quit:** Use **AdoptIQ → Quit AdoptIQ** from the menu bar (or ⌘Q). Closing only the terminal window may leave the app running in the background.

### If macOS blocks the app ("cannot be opened because it is from an unidentified developer")

- **Right-click** (or Control-click) **AdoptIQ.app** → **Open** → **Open** in the dialog. You only need to do this once.
- Or: **System Settings** → **Privacy & Security** → scroll to the blocked app → click **Open Anyway**.

**Alternative:** You can run **AdoptIQ.app** directly from the DMG mount without installing. If you see sync or startup issues, copy the app to a local folder (e.g. `~/Applications`) and run it from there.

---

### Navigation

- **Dashboard / Run Analysis** — Start here; run new analyses from the dashboard buttons.
- **History** — Lists previous portfolio analyses. Use **View** to open the progress page for an analysis and download Word/Excel reports when completed.
- **Intel** — View tracked incidents, bugs, and maintenances from Webex status and help pages. Search, export/import historical data, and ask AI questions about intelligence.
- **Ask AI** — Ask natural-language questions powered by 16 live data sections: portfolio overview, ARR and financials, adoption barriers, support cases, customer pulse, success priorities, action plans, trend analysis, barrier velocity, ARR at risk, external intelligence, historical context, account insights, portfolio intelligence, barrier aging, and pulse-revenue correlation.
- **Help** — Links and usage notes.

---

## Requirements

- **macOS or Windows** system
- **Internet access** (corporate VPN may be needed for Snowflake and CSOne)
- **Cisco CSOne** access to export reports

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
- **App closes immediately:** Launch from Terminal to see startup errors.
- **Connection errors:** You may need to be on **corporate VPN**.
- **Snowflake/credential errors:** The app has embedded credentials. If you see credential errors, the build may have been created without a complete `secrets.env`. Contact your administrator for a properly configured build.
