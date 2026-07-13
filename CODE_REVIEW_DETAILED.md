# AdoptIQ – Detailed Code and Logic Review

> **Status: historical snapshot (Feb 2026).** Findings here have all been applied. The codebase has since gone through Round 14, 15, and 16 audits. **For current state see `CODE_REVIEW_LATEST.md` and `QUALITY_AUDIT.md`.**

**Date:** February 2026
**Scope:** All report types, CSOne handling, build, runtime, and logic flows

---

## 1. Verification Status

| Check | Result |
|-------|--------|
| Python syntax (app_simple, adoptiq_backend, leader_report_generator, config) | ✅ Pass |
| Module imports | ✅ Pass |
| Linter errors | ✅ None |

---

## 2. Summary of Recent Changes

| Change | File | Lines | Purpose |
|--------|------|-------|---------|
| **Leader report scope filtering** | app_simple.py | 9131–9180 | Scope CSOne to team portfolio (was raw count) |
| **Leader report path resolution** | app_simple.py | 9149–9155 | Resolve CSOne path when stored as filename |
| **Renewal report path resolution** | app_simple.py | 5979–5986 | Resolve CSOne path when stored as filename |
| **Standalone exe build** | build_pc.bat, README.md | – | Removed installer; output only AdoptIQ.exe |

---

## 3. Build and Deployment

### 3.1 Build Script (`build_pc.bat`)

```
1. Use venv if present
2. pip install -r requirements.txt, pyinstaller
3. embed_credentials.py (from secrets.env or stub)
4. update_version_pc.py (sets ADOPTIQ_VERSION, ADOPTIQ_BUILD in config.py)
5. PyInstaller adoptiq_pc.spec → dist/AdoptIQ.exe
6. copy dist/AdoptIQ.exe, README.md → OUTBOX/
7. Unblock-File for SmartScreen
```

**Output:** `OUTBOX/AdoptIQ.exe`, `OUTBOX/README.md`

### 3.2 PyInstaller Spec (`adoptiq_pc.spec`)

- **Entry:** app_simple.py
- **Data:** team_config.json, templates/, static/, certifi CA bundle
- **Excludes:** matplotlib, PIL, tkinter
- **Hidden imports:** flask, pandas, snowflake.connector, adoptiq_backend, leader_report_generator, etc.

### 3.3 Frozen Runtime (app_simple.py L33–78)

- `_BASE_PATH` = sys._MEIPASS (extracted bundle)
- `_APP_SUPPORT` = %APPDATA%\AdoptIQ (Windows)
- `os.chdir(_APP_SUPPORT)` so relative paths resolve correctly
- SSL_CERT_FILE, REQUESTS_CA_BUNDLE set for Snowflake
- _bundled_secrets loaded for credentials

---

## 4. Report Entry Points and CSOne Storage

### 4.1 Entry Points

| Report | Route | Handler | Request Type |
|--------|-------|---------|--------------|
| Comprehensive | `/start_analysis` | start_analysis | Form (files) |
| Compact | `/start_analysis` or `/start_compact_analysis` | start_analysis / start_compact_analysis | Form or JSON |
| Renewal | `/start_analysis` or `/start_customer_renewal_analysis` | start_analysis / start_customer_renewal_analysis | Form or JSON |
| Leader | `/start_leader_report` | start_leader_report | Form (files) |
| Subscription | `/start_subscription_analysis` | start_subscription_analysis | JSON |

### 4.2 What Gets Stored in `status['csone_file']`

| Entry Point | Line | Stored Value |
|-------------|------|--------------|
| start_analysis | 537–539 | `filepath` = os.path.join(UPLOAD_FOLDER, filename) → full path |
| start_compact_analysis (form) | 8099–8101 | `filename` only |
| start_compact_analysis (JSON) | 8074 | data.get('csone_file', '') |
| start_customer_renewal_analysis | 8196 | data.get('csone_file', '') from JSON |
| start_leader_report | 9037–9039 | `filepath` = full path |

### 4.3 Path Resolution

| Report | Location | Logic |
|--------|----------|-------|
| Compact | process_csone_data L3486–3571 | If path missing → UPLOAD_FOLDER + path; fallback to first .xlsx |
| Comprehensive | L6708–6709 | Uses path as-is (always full path) |
| Renewal | L5979–5986 | If path missing → UPLOAD_FOLDER + path |
| Leader | L9149–9155 | Same as Renewal |

---

## 5. Report-by-Report Logic Flow

### 5.1 Comprehensive (`run_comprehensive_analysis`, L6518)

1. Connect Snowflake
2. get_subscriptions_for_team(ctx, cssm_emails)
3. Filter team_subs_df by customer/subscription if provided
4. Fetch adoption barriers, CSConsole (AP, CP, SP, AB)
5. Load CSOne: reject if filename like AdoptIQ_Portfolio_/Compact_/Renewal_
6. _prepare_csone, _apply_scope_filter_csone(tech, days, sub_ids, team_customer_names)
7. csone_import_message: "X cases found"
8. Validation, report generation

**CSOne scope:** Technology, days, subscription IDs, team customer names.

---

### 5.2 Compact (`run_compact_analysis`, L3129)

1. Connect Snowflake
2. get_subscriptions_for_team, filter by customer/subscription
3. Fetch adoption barriers, ARR, CSConsole
4. process_csone_data(csone_file): path resolution, load, _prepare_csone, _apply_scope_filter_csone
5. If 0 cases: _apply_scope_filter_csone_inclusive(technology, days)
6. Enrich with ARR, feature requests, charts
7. Report generation

**CSOne scope:** Same as Comprehensive; inclusive fallback if strict returns 0.

---

### 5.3 Customer Renewal (`run_customer_renewal_analysis`, L5662)

**Modes:** Single customer (`renewal`/`renewal_single`), Portfolio (`renewal_portfolio`).

1. Connect Snowflake
2. team_subs_df: single without manager → fetch_subscription_data or search_subscriptions_by_customer; else get_subscriptions_for_team
3. Fetch adoption barriers, CSConsole for customer(s)
4. CSOne (L5976–5999): path resolution, load, _prepare_csone, _apply_scope_filter_csone(technology, days, [], team_customer_names)
5. Portfolio: use all csone_df; Single: filter to customer_name
6. If no CSOne: fetch_support_cases_snowflake
7. Report generation

**CSOne scope:** Technology, days, team customer names (sub_ids=[]).

---

### 5.4 Leader (`run_leader_report_generation`, L9078)

1. Connect Snowflake
2. get_subscriptions_for_team for manager's direct reports (L9134–9140)
3. Path resolution (L9149–9155)
4. load_csone_excel, _prepare_csone
5. If team_subs_df not empty: _apply_scope_filter_csone("All", days, sub_ids, team_customer_names)
   Else: _apply_scope_filter_csone_inclusive("All", days)
6. status['message'] = "Loaded X TAC cases in scope for team..."
7. External intel, defects, PSIRT
8. generate_leader_report, Excel with add_tac_cases_from_csone

**CSOne scope:** Team customers, sub IDs, days; tech="All". Fallback: date only if no team subs.

---

### 5.5 Subscription (`run_subscription_analysis`, L8413)

1. fetch_subscription_data(subscription_id, days)
2. No CSOne; data from Snowflake
3. Report generation

---

## 6. CSOne Filtering (adoptiq_backend.py)

### _prepare_csone(df, team_subs_df) – L3970

- Standardizes customer column to `customer_name`
- Merges with team subscriptions for BU_NAME
- Adds `bemscsc_refs` for BEMS detection

### _apply_scope_filter_csone(df, tech, days, sub_ids, team_customer_names) – L3655

1. Filter by subscription IDs or customer names
2. Date filter: cases >= (now - days)
3. Technology filter (if tech != "All")

### _apply_scope_filter_csone_inclusive(df, technology, days) – L3769

- Technology and date only; no team filter

---

## 7. Configuration and Paths

### config.py

- ADOPTIQ_VERSION = "1.0", ADOPTIQ_BUILD = "1"
- UPLOAD_FOLDER = './uploads', OUTPUT_FOLDER = './outputs'
- When frozen + os.chdir(_APP_SUPPORT): ./uploads → %APPDATA%\AdoptIQ\uploads

### Key Paths When Frozen

| Purpose | Path |
|---------|------|
| Uploads | %APPDATA%\AdoptIQ\uploads |
| Outputs | %APPDATA%\AdoptIQ\outputs |
| Status file | %APPDATA%\AdoptIQ\analysis_status.json |
| Admin DB | %APPDATA%\AdoptIQ\admin_monitoring_v2.db |

---

## 8. Edge Cases and Recommendations

| Issue | Location | Recommendation |
|-------|----------|----------------|
| Renewal JSON API may have empty csone_file | start_customer_renewal_analysis | Document; path resolution helps when filename is sent |
| Compact fallback to first .xlsx | process_csone_data L3518–3523 | Consider removing or restricting |
| Leader no output-file detection | run_leader_report_generation | Add same check as Comprehensive |
| Compact/Renewal no csone_import_message | – | Optional: add for UI consistency |

---

## 9. File Summary

| File | Role |
|------|------|
| app_simple.py | Flask app, routes, report logic, CSOne path resolution |
| adoptiq_backend.py | Snowflake, CSOne load/prepare/filter, adoption barriers |
| leader_report_generator.py | Leader Word/Excel, add_tac_cases_from_csone |
| config.py | Version, Flask, Snowflake/Keeper/CircuIT config |
| build_pc.bat | Build AdoptIQ.exe, copy to OUTBOX |
| adoptiq_pc.spec | PyInstaller spec, certifi, hidden imports |
| enhanced_admin_dashboard_v2.py | Audit, report_history, init_database |

---

## 10. Quick Reference: CSOne Usage by Report

| Report | Load | Prepare | Scope Filter | Fallback |
|--------|------|---------|--------------|----------|
| Comprehensive | L6709 | L6717 | L6718 | – |
| Compact | L3533 | L3534 | L3552, L3558 | Inclusive if 0 |
| Renewal | L5986 | L5987 | L5989 | Snowflake SUPPORT_CASES |
| Leader | L9157 | L9161 | L9166, L9171 | Inclusive if no team |
