# AdoptIQ for macOS

**Version 1.0.2** — Version and build are shown in the app footer (e.g. v1.0.2 build 1).

AdoptIQ generates renewal reports (Word, Excel) from CSOne adoption barriers, support cases, and related data. No Python or development tools required. Credentials (Snowflake, CircuIT, PSIRT) are embedded in the app—no .env file needed.

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
- **Admin dashboard:** Secret key is set at build time (embedded via `ADOPTIQ_ADMIN_SECRET_KEY` in secrets.env); packaged builds require it for the Admin Console.

---

## Quick Start

**Recommended:** Install from the **DMG** for the best experience. No additional development tools are required for end users.

### Install

1. Open the **AdoptIQ** DMG (e.g. `AdoptIQ-v1.0.2-build1.dmg`).
2. Drag **AdoptIQ.app** to **Applications**.
3. Eject the DMG. Launch **AdoptIQ** from Applications (or Spotlight).

### Run

1. Double-click **AdoptIQ.app** to launch.
2. A terminal window may open if the app is packaged with console output.
3. Your browser will open automatically to **http://localhost:5001** (or open it manually if needed).
4. Use AdoptIQ as needed, keeping the server window open during use.

**To quit:** Use **AdoptIQ → Quit AdoptIQ** from the menu bar (or ⌘Q). Closing only the terminal window may leave the app running in the background.

### If macOS blocks the app ("cannot be opened because it is from an unidentified developer")

- **Right-click** (or Control-click) **AdoptIQ.app** → **Open** → **Open** in the dialog. You only need to do this once.
- Or: **System Settings** → **Privacy & Security** → scroll to the blocked app → click **Open Anyway**.

**Alternative:** You can run **AdoptIQ.app** directly from the DMG mount without installing. If you see sync or startup issues, copy the app to a local folder (e.g. `~/Applications`) and run it from there.

---

### Navigation

- **Dashboard** — Start here; run new analyses from **Run Analysis** or the dashboard buttons.
- **History** — Lists previous portfolio analyses. Use **View** to open the progress page for an analysis and download Word/Excel reports when completed.
- **Help** — Links and usage notes.
- **Admin** — Opens the optional Admin Console (monitoring, report history, logs) in a new tab. The console runs as a separate process; if you use it, set `ADOPTIQ_ADMIN_URL` (e.g. `http://localhost:5002`) when it runs on a different port.

---

## Requirements

- **macOS** system
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

| Purpose | Location |
|--------|----------|
| Uploads (CSOne Excel files) | `~/Library/Application Support/AdoptIQ/uploads/` |
| Generated reports (Word, Excel) | `~/Library/Application Support/AdoptIQ/outputs/` |
| Analysis status (progress tracking) | `~/Library/Application Support/AdoptIQ/analysis_status.json` |

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
