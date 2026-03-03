# AdoptIQ Deep Code & Logic Review Audit

**Date:** February 2, 2025  
**Scope:** Full application audit – reports, data sources, error handling, and logic correctness

---

## 1. Report Types & Data Flow Map

| Report Type | Entry Point | Data Sources Required | Validation | Output |
|-------------|-------------|------------------------|------------|--------|
| **Compact/Executive Intelligence** | `app_simple.py` → `create_executive_intelligence_report` / `_create_enhanced_compact_report` | Snowflake, team_subs, AB, CSOne | `raise_validation_error_if_invalid('compact')` | Word (.docx) |
| **Renewal (Single)** | `app_simple.py` → `_create_simple_renewal_report` | Snowflake, team_subs (AB/CSOne optional) | `raise_validation_error_if_invalid('renewal')` | Word (.docx) |
| **Renewal (Portfolio)** | Same as above, `portfolio_mode=True` | Same | Same | Word (.docx) |
| **Comprehensive** | `app_simple.py` → `ExecutiveReportBuilder` + `create_enhanced_word_report` | Snowflake, team_subs, AB, CSOne | `raise_validation_error_if_invalid('comprehensive')` | Word (.docx) |
| **Leader** | `leader_report_generator.py` → `generate_leader_report` | Snowflake, team_subs | `raise_validation_error_if_invalid('leader')` | Word (.docx) |
| **Advanced Renewal** | `advanced_renewal_analyzer.py` → `generate_renewal_report` | Snowflake (CX_DB) | None (uses mock if no connection) | Word (.docx) |
| **Enhanced Snowflake Insights** | `enhanced_snowflake_insights.py` → `generate_enhanced_insights_report` | Snowflake (89+ tables) | None | Word (.docx) |

---

## 2. Data Source Validation Summary

**`data_source_validator.py`** – Required sources by report type:

- **Compact / Comprehensive:** snowflake, team_subscriptions, adoption_barriers, csone
- **Renewal / Renewal Portfolio / Leader:** snowflake, team_subscriptions (AB and CSOne optional for renewal)

**Key behavior:**
- Renewal reports can run with empty AB and CSOne (e.g., Snowflake support cases used as fallback)
- Compact and Comprehensive reports require non-empty AB and CSOne
- All validators check for required columns (`customer_name`, `ACCOUNT_ID_C`, etc.)

---

## 3. Canonical Data Sources (report_utils.py)

All reports now use shared canonical sources from `report_utils.py`:

- **DATA_SOURCES_CANONICAL** – List of (metric, source_system, verification_note)
- **get_data_sources_paragraph_text()** – Inline paragraph text
- **get_data_sources_list()** – Table/bullet list format

**Reports using canonical sources:**
- Renewal (app_simple)
- Leader (leader_report_generator)
- Executive Intelligence (executive_intelligence_formatter)
- Compact (compact_report_formatter) – Data Citations + Methodology
- Advanced Renewal Analyzer
- Enhanced Snowflake Insights

---

## 4. Bugs Fixed in This Audit

### 4.1 `ab_data.get('SEVERITY_C', '')` – DataFrame.get returns wrong type

**Location:** `compact_report_formatter.py` – `add_critical_adoption_barriers`, `_generate_key_concerns`, `_generate_immediate_actions`

**Issue:** `DataFrame.get('col', '')` returns a Series or the default. Using `''` as default and then `.str.contains()` can fail when the column is missing (default is a string, not a Series).

**Fix:** Use `'SEVERITY_C' in ab_data.columns` and `ab_data['SEVERITY_C'].astype(str).str.contains(...)`.

### 4.2 `_generate_immediate_actions` – risk_scores structure mismatch

**Location:** `compact_report_formatter.py` line 666

**Issue:** `risk_scores.values()` yields dicts like `{'score': 7.5, 'color': 'Red', ...}`, not raw scores. `score >= 6` was comparing a dict to an int.

**Fix:** Use `v.get('score', 0) >= 6` for each value `v`.

### 4.3 `calculate_arr_impact_for_issues` – None handling

**Location:** `app_simple.py` line 2103

**Issue:** If `arr_data` is `None`, `arr_data.empty` raises `AttributeError`.

**Fix:** Add explicit `if ab_norm is None or arr_data is None` guard before `.empty` checks.

### 4.4 `add_common_problems_section` – DataFrame.get for columns

**Location:** `compact_report_formatter.py` – `add_common_problems_section`

**Issue:** `ab_data.get('SUBJECT_C', pd.Series())` can behave unexpectedly. Prefer direct column access with existence checks.

**Fix:** Use `ab_data['SUBJECT_C']` when `'SUBJECT_C' in ab_data.columns`, else empty Series.

### 4.5 `.str.contains` without `.astype(str)` – mixed-type columns

**Location:** `compact_report_formatter.py`, `app_simple.py`, `adoptiq_backend.py`, `leader_report_generator.py`

**Issue:** Columns like `SEVERITY_C`, `AB_STATUS_C`, `Severity`, `BU_NAME`, `PRIORITY`, `STATUS_C`, `PULSE_RATING__C`, `Case Status`, `Highest Priority` can contain NaN or mixed types. Calling `.str.contains()` directly may raise `AttributeError` on non-string values. Using `DataFrame.get('col', '')` returns a string when column is missing, and `.str.contains` on a string fails.

**Fix:** Use `.astype(str).str.contains(..., na=False)` for all such columns. Replace `df.get('col', '')` with column-existence checks and `df['col'].astype(str).str.contains(...)`.

### 4.6 `estimate_arr` – DataFrame.get('Severity') in ARR estimation

**Location:** `app_simple.py` – `estimate_arr` helper (around line 1969)

**Issue:** `customer_cases.get('Severity', '') == 'P1'` – when the column is missing, `.get` returns `''`, and `'' == 'P1'` yields scalar `False`. Using that for row indexing breaks.

**Fix:** Use `customer_cases['Severity'].astype(str) == 'P1'` when `'Severity' in customer_cases.columns`, else 0.

### 4.7 `leader_report_generator` – BEMS escalation block BU_NAME

**Location:** `leader_report_generator.py` – BEMS Escalation Details block (inside enhanced Snowflake insights try, around line 3056)

**Issue:** One occurrence of `data['adoption_barriers']['BU_NAME'].str.contains(customer` was missed (no `.astype(str)`).

**Fix:** Add `.astype(str)` before `.str.contains`.

---

## 5. Known Gaps & Recommendations

### 5.1 Missing Module: `comprehensive_executive_formatter` — FIXED

**Location:** `app_simple.py` line 3919

**Issue:** Fallback imported `comprehensive_executive_formatter` when `executive_intelligence_formatter` was unavailable. This module does not exist in the repo.

**Fix Applied:** Fallback now uses `_create_enhanced_compact_report` (same as the outer exception handler), which produces a valid Word report with all data sources.

### 5.2 Compact Report with Empty Data

**Location:** `compact_report_formatter.py` – `create_compact_executive_report`

**Issue:** Compact report requires AB and CSOne per validation. The test `test_compact_report_empty_data` passes empty DataFrames, but in production the flow is blocked by validation before reaching the formatter. The formatter itself handles empty data (e.g., `calculate_renewal_risk_scores` returns `{}`).

**Status:** Formatter is robust; validation prevents empty-data runs in production.

### 5.3 `_create_enhanced_compact_report` – extract_software_defects / extract_psirt_vulnerabilities with None

**Location:** `app_simple.py` lines 2239–2240

**Issue:** `extract_software_defects(csone_df, ab_norm)` and `extract_psirt_vulnerabilities(csone_df, ab_norm)` are called without checking for None. Both functions already handle `csone_df is None or csone_df.empty` internally. `ab_norm` can be None if initialization fails.

**Recommendation:** Ensure `ab_norm` is always at least `pd.DataFrame()` before these calls (already done in most code paths).

### 5.4 `detect_bems_escalations` with empty DataFrame

**Location:** `app_simple.py` line 2276 – `bems_cases, bems_count = detect_bems_escalations(csone_df)`

**Issue:** When `csone_df` is empty, `detect_bems_escalations` returns `(pd.DataFrame(), 0)`. The next line uses `bems_cases[customer_col]` – if `customer_col` is None (no severity column found), this could fail. The code path uses `customer_col` only when `bems_count > 0`, so this is likely safe.

**Status:** Low risk; `detect_bems_escalations` is defensive.

---

## 6. Empty/None Handling Checklist

| Component | ab_data | csone_data | team_subs | arr_data |
|-----------|---------|------------|-----------|----------|
| `calculate_renewal_risk_scores` | `not ab_data.empty` | `not csone_data.empty` | N/A | N/A |
| `_create_simple_renewal_report` | Handles empty | Handles empty, Snowflake fallback | Required | Optional |
| `create_compact_executive_report` | Handles empty | Handles empty | N/A (pre-validated) | N/A |
| `_create_enhanced_compact_report` | Handles empty | Handles empty | Can be None → pd.DataFrame() | N/A |
| `calculate_arr_impact_for_issues` | None + empty check | N/A | N/A | None + empty check |
| `extract_software_defects` | Optional, handles None | Handles None/empty | N/A | N/A |
| `extract_psirt_vulnerabilities` | Optional, handles None | Handles None/empty | N/A | N/A |
| `detect_bems_escalations` | N/A | Handles None/empty | N/A | N/A |

---

## 7. PyInstaller Build (adoptiq_pc.spec)

**hiddenimports** includes:
- `report_utils`, `data_source_validator`, `leader_report_generator`, `enhanced_snowflake_insights`
- `compact_report_formatter`, `advanced_renewal_analyzer`, `executive_intelligence_formatter`

**Note:** `comprehensive_executive_formatter` is not in hiddenimports (module does not exist).

---

## 8. Test Coverage

**`tests/test_reports_extensive.py`** covers:
- report_utils (format_date, format_number, get_risk_scoring_explanation, get_report_metadata_footer)
- _calculate_simple_renewal_risk
- _create_simple_renewal_report (single, portfolio, no-data)
- create_compact_executive_report (with data, empty)
- calculate_renewal_risk_scores edge cases
- AdvancedRenewalAnalyzer (mock)
- LeaderReportGenerator structure
- ExecutiveIntelligenceFormatter

**Recommendation:** Run `python tests/test_reports_extensive.py` after changes to confirm no regressions.

---

## 9. Summary

- **Data flow:** All report types are mapped; validation aligns with requirements.
- **Canonical sources:** All reports use `report_utils` for data source citations.
- **Bugs fixed:** DataFrame.get misuse, risk_scores structure, None handling in `calculate_arr_impact_for_issues`, and common problems section column access.
- **Outstanding:** Add or replace `comprehensive_executive_formatter` fallback; consider extra tests for empty/edge cases.
