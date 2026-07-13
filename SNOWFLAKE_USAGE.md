# AdoptIQ: Snowflake Usage Summary

**Purpose:** Technical summary of how Snowflake is used in the AdoptIQ application for sharing with colleagues.

**Last updated:** April 2026 (current as of Round 16 audit; tables and policy below match `snowflake_table_policy.py` HEAD).

---

## 1. Overview

AdoptIQ is a Flask-based executive analytics application that generates renewal reports (Word, Excel) for Cisco collaboration customers. Snowflake is the primary database for:

- **Team/customer scope** — Determining which subscriptions and accounts belong to the selected manager's team
- **CSConsole data** — Adoption barriers, action plans, customer pulse, success priorities (synced from Salesforce/CSConsole into Snowflake)
- **Support cases** — Disabled by enforced table policy (see section 1.1)
- **ARR/financial data** — Contract value, MRR, TCV for prioritization (when columns exist)
- **Enhanced insights** — Leader reports use additional CX_DB tables for account, contract, booking, risk, and product data

### 1.1 Enforced Table Policy (Hardcoded)

AdoptIQ now enforces a strict Snowflake table policy in `snowflake_table_policy.py`.

**Allowed tables**
- `CX_DB.CX_SWSSBST_BR.dsm_assignment_data`
- `CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY`
- `CX_DB.CX_SWSSBST_BR.ACCOUNTS_EXPIRED_LAST_MONTH`
- `CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU`
- `EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW`
- `EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C`

**Blocked tables**
- `CX_DB.CX_SWSSBST_BR.SUPPORT_CASES`
- `CX_DB.CX_SWSSBST_BR.USER_DATA`
- `CX_DB.CX_SWSSBST_BR.PRODUCT_USAGE`
- `EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C`
- `EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C`

**Behavior**
- Blocked-table queries are skipped in key report paths and return empty outputs for those sections.
- Query execution is centrally guarded so blocked (or non-allowlisted) table references are rejected by policy before Snowflake execution.
- This is intentional and reduces noisy runtime SQL errors for disallowed sources.

---

## 2. Connection & Authentication

| Setting | Source | Notes |
|---------|--------|------|
| **Connection** | `snowflake.connector.connect()` | Python connector |
| **Auth** | `SNOWFLAKE_USER`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_PASSWORD` | Runtime process environment or the current user's AdoptIQ `.env`; never stored in a release artifact |
| **Alternative** | Keeper (HashiCorp Vault) | `KEEPER_ROLE_ID`, `KEEPER_SECRET_ID` for private-key auth when password not set |
| **Role/Warehouse** | `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE` | Optional; from env |
| **Timeout** | 30 seconds | Connection uses `ThreadPoolExecutor` with 30s timeout |

**Code location:** `adoptiq_backend.py` — `_connect_snowflake_direct()`, `_connect_with_keeper()`

Packaged `.app` and `.exe` files contain no service credentials. The per-user
runtime file is `~/Library/Application Support/AdoptIQ/.env` on macOS,
`%APPDATA%\AdoptIQ\.env` on Windows, or `~/.adoptiq/.env` on Linux. Use
`secrets.env.template` only as a supported-key reference and never place a
populated credential file in source, OUTBOX, or an application bundle.

---

## 3. Databases & Schemas Used

| Database | Schema | Usage |
|----------|--------|-------|
| **CX_DB** | **CX_SWSSBST_BR** | Primary: DSM assignments, support cases, account/contract/booking/renewal data |
| **EDW_SALES_ETL_DB** | **SS** | CSConsole-synced data: adoption barriers, action plans, customer pulse, success priorities |

---

## 4. Tables Queried

### 4.1 Core Tables (All Report Types)

| Full Table Name | Purpose | Key Columns Used |
|-----------------|---------|------------------|
| **CX_DB.CX_SWSSBST_BR.dsm_assignment_data** | Team roster, subscription-to-account mapping, CSSM assignments | `SUBSCRIPTION_ID`, `SUBSCRIPTION_ID_C`, `ACCOUNT_ID_C`, `BU_NAME`, `PRIMARY_DSM_EMAIL`, `TECHNOLOGY_C`, `SUB_TECHNOLOGY_C`, `STATUS_C`, `CSSM_EMAIL`, `CSSM_NAME`, `CSSM_MANAGER`, `CSSM_MANAGER_EMAIL`, `ANNUAL_CONTRACT_VALUE_C`, `MONTHLY_RECURRING_REVENUE_C`, `TOTAL_CONTRACT_VALUE_C`, `LICENSE_COUNT_C` |
| **EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW** | Adoption barriers and action plans (CSConsole) | `ACCOUNT_ID_C`, `CREATED_DATE`, `OPEN_DATE_C`, `record_type_id` (`0122T000000GJfTQAW` = Adoption Barrier, `0122T000000QHBGQA4` = Action Plan) |
| **EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C** | Customer pulse ratings (CSConsole) | `ACCOUNT__C`, `CREATEDDATE` |
| **EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C** | Success priorities (CSConsole) | **Blocked by policy** |

### 4.2 Support Cases (Renewal Reports Only)

| Full Table Name | Purpose | When Used |
|-----------------|---------|-----------|
| **CX_DB.CX_SWSSBST_BR.SUPPORT_CASES** | TAC/support case counts | **Blocked by policy** |

**Note:** The historical fallback query logic remains in code paths, but runtime policy blocks execution of `SUPPORT_CASES`.

### 4.3 Enhanced Insights (Leader Reports Only)

Used by `EnhancedSnowflakeInsights` in `enhanced_snowflake_insights.py` for Leader Reports. These tables are **optional** — if they do not exist or the role lacks access, the app skips them and continues.

| Full Table Name | Purpose | Key Columns |
|-----------------|---------|-------------|
| **CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY** | Account summary, renewal risk category | `ACCOUNT_ID_C`, `BU_ACCOUNT_NAME`, `RENEWAL_RISK_CATEGORY`, `CONTRACT_STATUS`, `CISCO_TIER_RANKING__C`, `ABC_CATEGORY__C` |
| **CX_DB.CX_SWSSBST_BR.ACCOUNTS_EXPIRED_LAST_MONTH** | Recently expired accounts | `ID`, `NAME`, `EXPIRED_DATE`, `RENEWAL_ACCOUNT` |
| **CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU** | Contract and ARR data | `CONTRACT_NUMBER`, `SERVICE_END_DATE`, `ARR_AMOUNT`, `ACCOUNT_ID_C`, `C_360_SERVICE_TIER_C` |
| **CX_DB.CX_SWSSBST_BR.RENEWAL_DATA** | Renewal status and probability | `CONTRACT_NUMBER`, `RENEWAL_DATE`, `RENEWAL_STATUS`, `RENEWAL_PROBABILITY` |
| **CX_DB.CX_SWSSBST_BR.BOOKINGS_TABLE_FOR_ACCOUNT_CHECK** | Booking/transaction data | `SUBSCRIPTION_REFERENCE_ID`, `DATE_BOOKED`, `ORDER_STATUS`, `END_CUSTOMER_NAME`, `AMOUNT` |
| **CX_DB.CX_SWSSBST_BR.TSS_BOOKINGS_COLLAB_UPSELL_WITH_SUBS_REFERENCE_ID** | Upsell data | `SUBSCRIPTION_REFERENCE_ID`, `UPSELL_AMOUNT`, `PRODUCT`, `DATE_CREATED` |
| **EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C** | Alternate source for action plans/adoption barriers (engagement) | **Blocked by policy** |
| **CX_DB.CX_SWSSBST_BR.USER_DATA** | User/login data | **Blocked by policy** |
| **CX_DB.CX_SWSSBST_BR.RISK_ASSESSMENT** | Risk scores and factors | `ACCOUNT_ID`, `RISK_SCORE`, `RISK_CATEGORY`, `LAST_ASSESSED_DATE`, `RISK_FACTORS` |
| **CX_DB.CX_SWSSBST_BR.PRODUCT_USAGE** | Product adoption/usage | **Blocked by policy** |

### 4.4 Advanced Renewal Analyzer (Optional)

Used by `AdvancedRenewalAnalyzer` for portfolio renewal analysis when that path is enabled:

| Full Table Name | Purpose |
|-----------------|---------|
| **CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY** | Account lookup by customer name |
| **CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU** | Contract/renewal info by account |
| **EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW** | Adoption barriers, action plans |
| **EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C** | Customer pulse |
| **EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C** | **Blocked by policy** |

---

## 5. Data Flow by Report Type

### 5.1 Comprehensive Report

1. **Team scope:** `get_subscriptions_for_team(ctx, emails)` → `dsm_assignment_data` by `PRIMARY_DSM_EMAIL`
2. **Account IDs** from subscriptions
3. **ARR data:** `fetch_arr_data(ctx, account_ids)` → `dsm_assignment_data` (with ARR columns if present)
4. **Adoption barriers:** `fetch_adoption_barriers(ctx, account_ids, days)` → `C360_CS_TASK_C_VW` (record_type_id = Adoption Barrier)
5. **Action plans, customer pulse:** `fetch_csconsole_*` → `C360_CS_TASK_C_VW`, `ESA_C360_CUSTOMER_PULSE__C` (`ESA_C360_SUCCESS_PRIORITY__C` is blocked by policy)
6. **CSOne Excel** (uploaded) merged with Snowflake data
7. **Output:** Word + Excel reports with adoption barriers, cases, ARR, executive briefing

### 5.2 Compact Report

Same Snowflake flow as Comprehensive: team scope → adoption barriers → ARR → CSConsole data. Focused on renewal risk and early warning.

### 5.3 Renewal Report (Single Customer or Portfolio)

1. **Team scope** from `dsm_assignment_data`
2. **Adoption barriers** from `C360_CS_TASK_C_VW`
3. **Support cases:** From CSOne Excel if uploaded; Snowflake `SUPPORT_CASES` fallback is blocked by policy
4. **Output:** Renewal risk score, key findings, customer-level breakdown

### 5.4 Leader Report

1. **Team scope** from `dsm_assignment_data`
2. **Adoption barriers, action plans, customer pulse** from EDW/SS tables (`ESA_C360_SUCCESS_PRIORITY__C` is blocked by policy)
3. **Enhanced insights** from `EnhancedSnowflakeInsights` → COLLAB_ACCOUNT_SUMMARY, COLLAB_ARR_CON_SKU, RENEWAL_DATA, BOOKINGS_*, USER_DATA, SUPPORT_CASES, RISK_ASSESSMENT, PRODUCT_USAGE, etc.
4. **Output:** Manager-focused report with per-customer deep dives and enhanced Snowflake insights

### 5.5 Subscription Search / Single-Subscription Analysis

1. **Lookup by subscription ID:** `fetch_subscription_data()`, `search_subscriptions_by_customer()` → `dsm_assignment_data` (uses `SUBSCRIPTION_ID` or `SUBSCRIPTION_ID_C` depending on query)
2. **By account ID:** Adoption barriers, action plans, customer pulse from EDW/SS (`ESA_C360_SUCCESS_PRIORITY__C` is blocked by policy)
3. **Team info:** `dsm_assignment_data` → CSSM_EMAIL, CSSM_NAME, CSSM_MANAGER, CSSM_MANAGER_EMAIL
4. **Renewal risk:** `get_subscription_renewal_risk()` calls `fetch_subscription_data()` and computes risk from adoption barriers, customer pulse, action plans

### 5.6 Ask AI (Legacy + Grounded)

1. **Run-scoped prefetch context:** `AnalysisRunContext` captures account scope and day window once.
2. **Legacy Ask AI:** Uses `prefetch_ask_ai(...)` for a shared support/pulse/success-priority/action-plan bundle.
3. **Grounded Ask AI:** Uses `prefetch_ask_ai_grounded(...)` with intent-based dataset gating to fetch only datasets needed for the user question.

---

## 6. Query Patterns

- **Parameterized queries:** All user/input-derived values use `%s` placeholders; no string concatenation of user input into SQL.
- **Date filtering:** `DATEADD(day, -%s, CURRENT_DATE())` for lookback window (e.g., 90 days).
- **Record type filtering:** `record_type_id = '0122T000000GJfTQAW'` (Adoption Barrier) or `'0122T000000QHBGQA4'` (Action Plan).
- **Status filtering:** `STATUS_C = 'ACTIVE'` for DSM/ARR queries where applicable.
- **Graceful fallback:** ARR query tries financial columns first; if missing, falls back to basic customer columns. Enhanced insights tables fail quietly if unavailable.
- **Run-scoped prefetch/caching:** Report and Ask AI flows use `snowflake_prefetch.py` to reduce repeated Snowflake queries within a single analysis run.

---

## 7. What Is Done With the Data

| Data | Use in Application |
|------|--------------------|
| **Team subscriptions** | Define which customers/subscriptions are in scope for the selected manager |
| **Adoption barriers** | Count, list, and summarize barriers; feed into renewal risk scoring; include in Word/Excel reports |
| **Action plans** | Count and list; include in reports |
| **Customer pulse** | Include in reports; factor into customer health view. Backfilled pulse rows improve account coverage visibility but are excluded from scoring/trend semantics. |
| **Success priorities** | Include in reports |
| **Support cases** | Case counts and lists in renewal reports when CSOne not provided |
| **ARR/MRR/TCV** | Prioritize customers by revenue; show in reports; enrich briefing |
| **Enhanced insights** | Leader report: account summary, contracts, renewals, bookings, risk, product usage |

---

## 8. No Writes to Snowflake

AdoptIQ performs **read-only** queries. No `INSERT`, `UPDATE`, or `DELETE` statements are executed against Snowflake.

---

## 9. Code References

| Module | Functions / Classes |
|--------|---------------------|
| `adoptiq_backend.py` | `_connect_snowflake_direct`, `_connect_with_keeper`, `fetch_subscription_data`, `search_subscriptions_by_customer`, `get_subscription_renewal_risk`, `get_subscriptions_for_team`, `fetch_arr_data`, `fetch_support_cases_snowflake`, `fetch_adoption_barriers`, `load_and_merge_data_for_subscription`, `fetch_csconsole_action_plans`, `fetch_csconsole_customer_pulse`, `fetch_csconsole_success_priorities`, `fetch_csconsole_adoption_barriers` |
| `snowflake_prefetch.py` | `AnalysisRunContext`, `prefetch_comprehensive`, `prefetch_ask_ai`, `prefetch_ask_ai_grounded` |
| `risk_scoring.py` | `_exclude_backfill_pulse_rows`, `compute_customer_risk_profile` (coverage-only backfill handling) |
| `leader_report_generator.py` | `_get_subscriptions_for_cssm`, `_fetch_action_plans`, `_fetch_adoption_barriers`, `_fetch_customer_pulse`, `_fetch_success_priorities`; uses `EnhancedSnowflakeInsights` |
| `enhanced_snowflake_insights.py` | `EnhancedSnowflakeInsights` — `_get_account_insights`, `_get_contract_insights`, `_get_booking_insights`, `_get_engagement_insights`, `_get_usage_insights`, `_get_support_insights`, `_get_risk_insights`, `_get_product_insights` |
| `advanced_renewal_analyzer.py` | `_get_customer_account_info`, `_get_contract_renewal_info`, `_get_usage_adoption_metrics`, `_get_support_engagement_metrics`, `_get_adoption_success_metrics`; queries COLLAB_ACCOUNT_SUMMARY, COLLAB_ARR_CON_SKU, C360_CS_TASK_C_VW, ESA_C360_* |
| `config.py` | `DSM_TABLE`, `AB_TABLE` constants |

---

## 10. Summary Table

| Database.Schema | Tables | Report Types |
|-----------------|--------|--------------|
| CX_DB.CX_SWSSBST_BR | dsm_assignment_data | All |
| CX_DB.CX_SWSSBST_BR | SUPPORT_CASES | Renewal (fallback when no CSOne) |
| CX_DB.CX_SWSSBST_BR | COLLAB_ACCOUNT_SUMMARY, COLLAB_ARR_CON_SKU, RENEWAL_DATA, BOOKINGS_*, USER_DATA, RISK_ASSESSMENT, PRODUCT_USAGE, ACCOUNTS_EXPIRED_LAST_MONTH, TSS_BOOKINGS_COLLAB_UPSELL_* | Leader (Enhanced Insights) |
| EDW_SALES_ETL_DB.SS | C360_CS_TASK_C_VW | All (adoption barriers, action plans) |
| EDW_SALES_ETL_DB.SS | ESA_C360_CUSTOMER_PULSE__C, ESA_C360_SUCCESS_PRIORITY__C | All |
| EDW_SALES_ETL_DB.SS | ESA_C360_CS_TASK__C | Leader (Enhanced Insights, alternate engagement source) |
