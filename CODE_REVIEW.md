# AdoptIQ Code and Logic Review

**Date:** February 2026  
**Scope:** CSOne/TAC case handling across all report types, plus general logic consistency

---

## 1. Report Types and CSOne Handling Summary

| Report Type | Function | CSOne Scope Filtering | Progress Message | Status |
|-------------|----------|----------------------|------------------|--------|
| **Comprehensive** | `run_comprehensive_analysis` | ✅ `_prepare_csone` + `_apply_scope_filter_csone` (tech, days, sub_ids, team_customer_names) | "X cases found" (filtered) | ✅ Correct |
| **Compact (Executive Summary)** | `run_compact_analysis` | ✅ Same as above + fallback to `_apply_scope_filter_csone_inclusive` if 0 cases | No explicit case count in UI | ✅ Correct |
| **Customer Renewal** (Single) | `run_customer_renewal_analysis` | ✅ `_prepare_csone` + `_apply_scope_filter_csone` then filter to `customer_name` | No explicit case count | ✅ Correct |
| **Portfolio Renewal** | `run_customer_renewal_analysis` | ✅ Same scope filter, uses all team cases | No explicit case count | ✅ Correct |
| **Leader Report** | `run_leader_report_generation` | ✅ **Fixed** – now uses `_prepare_csone` + `_apply_scope_filter_csone` (was raw before) | "X TAC cases in scope for team" | ✅ Fixed |
| **Subscription Analysis** | `run_subscription_analysis` | N/A – uses Snowflake `fetch_subscription_data`, no CSOne upload | N/A | N/A |

---

## 2. CSOne Filtering Logic (All Reports That Use CSOne)

**Scope filter parameters:**
- **Technology** – filters by product/tech focus (or "All" for Leader)
- **Days** – last N days
- **Subscription IDs** – team's subscription IDs (when available)
- **Team customer names** – customers from Snowflake team subscriptions

**Flow:**
1. `load_csone_excel()` – raw load
2. `_prepare_csone()` – standardize columns, merge with team data for customer names
3. `_apply_scope_filter_csone()` – filter by team customers, sub IDs, date, technology

**Compact fallback:** If strict filter returns 0 cases, uses `_apply_scope_filter_csone_inclusive()` (tech + date only) to avoid empty reports.

**Leader fallback:** If no team subscriptions, uses `_apply_scope_filter_csone_inclusive()` (date only).

---

## 3. Consistency Findings

### ✅ All reports now use scope filtering
- **Comprehensive, Compact, Renewal** – were already correct
- **Leader** – was showing raw count (e.g. 1475) vs. scoped (e.g. 161); now aligned

### ✅ Team data ordering
- **Leader** – fetches team subscriptions *before* loading CSOne (required for scope filter)
- **Comprehensive, Compact, Renewal** – already fetch team data first

### ⚠️ Minor: Compact does not set `csone_import_message`
- Comprehensive sets `csone_import_status` and `csone_import_message` for the progress UI
- Compact does not – progress page may show generic "Processing CSOne data..." 
- **Impact:** Low – Compact still uses correct filtered data; only the displayed message differs
- **Recommendation:** Optional – add `update_analysis_status` with `csone_import_message` after Compact's CSOne processing for consistency

### ⚠️ Minor: Renewal does not set `csone_import_message`
- Same as Compact – no explicit "X cases found" in progress UI
- **Impact:** Low
- **Recommendation:** Optional – add for consistency if desired

---

## 4. Logic Flow Verification

### Comprehensive
- Team subs → AB fetch → CSConsole fetch → CSOne load + scope filter → validation → report
- Uses filtered `team_subs_df` for `team_customer_names` when user applies customer/subscription filter
- Output file detection: skips CSOne if filename looks like AdoptIQ output (e.g. `AdoptIQ_Portfolio_...`)

### Compact
- Same flow as Comprehensive for data fetching
- Uses `team_subs_df_unfiltered` for `team_customer_names` in CSOne filter (ensures all team customers included)
- Fallback to inclusive filter if strict returns 0

### Renewal (Single Customer)
- Team subs (or single-customer lookup) → AB fetch → CSOne load + scope filter → filter to `customer_name` → report
- Portfolio: uses all scoped cases (no extra customer filter)

### Leader
- Connect → fetch team subs → load CSOne → scope filter → external intel → report
- Tech = "All" (no technology filter for Leader)

### Subscription
- Uses `fetch_subscription_data()` – no CSOne; data comes from Snowflake

---

## 5. Data Quality Safeguards

| Safeguard | Location | Purpose |
|-----------|----------|---------|
| Output file detection | Comprehensive, Compact (implicit) | Avoid using AdoptIQ output as input |
| Empty team fallback | Leader | Date-only filter if no team subs |
| Inclusive filter fallback | Compact | Avoid 0 cases when strict filter too narrow |
| Validation before report | All | `raise_validation_error_if_invalid` before generation |

---

## 6. Recommendations

1. **Done:** Leader report scope filtering – implemented
2. **Optional:** Add `csone_import_message` to Compact and Renewal for UI consistency
3. **Optional:** Consider adding output-file detection to Leader (currently does not check)
4. **No changes needed:** Comprehensive, Renewal, Subscription logic is correct

---

## 7. Additional Fix: CSOne Path Resolution (Renewal + Leader)

**Issue:** Renewal report stores `csone_file` as filename only (line 8101); `os.path.exists(filename)` fails when cwd is not uploads.

**Fix:** Added path resolution for both Renewal and Leader reports:
- If direct path exists, use it
- Else try `UPLOAD_FOLDER + filename`
- Same pattern as Compact/Comprehensive `process_csone_data`

---

## 8. Files Touched in This Review

- `app_simple.py` – Leader report CSOne scope filtering; Renewal + Leader CSOne path resolution
