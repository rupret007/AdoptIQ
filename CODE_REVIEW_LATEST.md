# AdoptIQ Code & Logic Review

**Date:** February 4, 2026  
**Scope:** Full application – code quality, logic correctness, security, and robustness

---

## 1. Executive Summary

The AdoptIQ codebase is well-structured with clear data flow, validation, and error handling. Previous audits fixed DataFrame/string handling issues. This review identifies one path bug, documents the browser-launch addition, and summarizes strengths and recommendations.

---

## 2. Architecture & Data Flow

| Report Type | Entry Point | Data Sources | Validation |
|-------------|-------------|--------------|------------|
| Compact/Executive | `create_executive_intelligence_report` / `_create_enhanced_compact_report` | Snowflake, team_subs, AB, CSOne | `raise_validation_error_if_invalid('compact')` |
| Renewal (Single/Portfolio) | `_create_simple_renewal_report` | Snowflake, team_subs (AB/CSOne optional) | `raise_validation_error_if_invalid('renewal')` |
| Comprehensive | `ExecutiveReportBuilder` + `create_enhanced_word_report` | Snowflake, team_subs, AB, CSOne | `raise_validation_error_if_invalid('comprehensive')` |
| Leader | `generate_leader_report` | Snowflake, team_subs | `raise_validation_error_if_invalid('leader')` |
| Advanced Renewal | `generate_renewal_report` | Snowflake (CX_DB) | None (mock fallback) |
| Enhanced Snowflake | `generate_enhanced_insights_report` | Snowflake (89+ tables) | None |

---

## 3. Bug: Status File Path When Frozen

**Location:** `app_simple.py` – `save_analysis_status()`, `load_analysis_status()`, and status-load logic in `/analysis/<id>` route

**Issue:** The status file path uses `os.path.join(os.path.dirname(__file__), STATUS_FILE)`. When running as a PyInstaller-frozen executable:
- `__file__` may point to a temp extraction folder (e.g. `_MEIxxxxx`)
- That folder can be read-only or deleted after exit
- The comment says "cwd is _APP_SUPPORT so analysis_status.json works" but the code does **not** use cwd—it uses `dirname(__file__)`

**Fix applied:** Use `_APP_SUPPORT` for the status file so it is always written to the writable app support directory:

```python
status_file_path = str(_APP_SUPPORT / STATUS_FILE) if not os.path.isabs(STATUS_FILE) else STATUS_FILE
```

This works for both frozen (e.g. `%APPDATA%\AdoptIQ\analysis_status.json`) and development (project folder when `_APP_SUPPORT = _BASE_PATH`).

---

## 4. Recent Addition: Browser Launch on Startup

**Location:** `app_simple.py` – main block, before `app.run()`

**Implementation:**
- Daemon thread sleeps 1.5 seconds, then calls `webbrowser.open('http://localhost:5001/')`
- Wrapped in try/except to avoid crashes if no browser is available

**Assessment:** Logic is correct. The delay allows the server to bind before the browser opens.

---

## 5. Security

### 5.1 Input Validation
- **Manager, Technology:** Length limits, dangerous-character checks (`'`, `"`, `;`, `--`, `/*`, `*/`, `xp_`, `sp_`)
- **Customer name:** Optional, length limit, SQL/script injection checks
- **Days:** 1–365 range
- **File uploads:** `secure_filename`, size limit (50MB), extension validation

### 5.2 CSRF
- CSRF enabled with `WTF_CSRF_ENABLED = True`
- AJAX requests identified by `X-Requested-With: XMLHttpRequest` bypass CSRF and use manual validation
- **Note:** For a local-only app (localhost), risk is low. If exposed to a network, consider stricter CSRF handling.

### 5.3 Secrets
- Credentials from environment variables and Keeper
- No hardcoded secrets in config
- `_bundled_secrets` used when frozen for embedded config

---

## 6. Error Handling & Robustness

| Area | Status |
|------|--------|
| DataFrame None/empty | Guards in `calculate_arr_impact_for_issues`, formatters |
| `.str.contains` on mixed types | `.astype(str)` applied consistently |
| `DataFrame.get` misuse | Replaced with column-existence checks |
| Executive formatter fallback | Uses `_create_enhanced_compact_report` (no missing module) |
| Data source validation | `raise_validation_error_if_invalid` before report generation |

---

## 7. Logic Correctness

- **Renewal CSOne upload:** Renewal requests use FormData to `/start_analysis` so `csone_file` is included
- **Risk scores:** `risk_scores.values()` treated as dicts with `score` key
- **Canonical data sources:** All reports use `report_utils` for citations
- **estimate_arr:** Uses column check and `df['Severity'].astype(str)` instead of `DataFrame.get`

---

## 8. Recommendations

### High Priority
1. **Fix status file path** – Use `_APP_SUPPORT / STATUS_FILE` instead of `dirname(__file__)` so the frozen app writes to the correct writable directory.

### Medium Priority
2. **Add `end_time` to datetime parsing** – In `load_analysis_status`, the `key in ['step_start_time', ...]` check does not include `end_time`; add it if that key is used.
3. **Consider port check before browser launch** – If the port is in use and the app exits, the browser may open to a different app. Low impact for normal runs.

### Low Priority
4. **Extract magic numbers** – e.g. 1.5s browser delay, 50MB upload limit – into config for easier tuning.
5. **Add integration tests** – For full report flows with mocked Snowflake/CSOne.

---

## 9. Test Coverage

**`tests/test_reports_extensive.py`** covers:
- report_utils
- _calculate_simple_renewal_risk
- _create_simple_renewal_report (single, portfolio, no-data)
- create_compact_executive_report (with data, empty)
- calculate_renewal_risk_scores edge cases
- AdvancedRenewalAnalyzer (mock)
- LeaderReportGenerator structure
- ExecutiveIntelligenceFormatter

**Recommendation:** Run `python tests/test_reports_extensive.py` after changes.

---

## 10. Summary

| Category | Rating | Notes |
|----------|--------|-------|
| Architecture | Good | Clear separation, validation before reports |
| Security | Good | Input validation, CSRF, no hardcoded secrets |
| Error handling | Good | Defensive checks, fallbacks |
| Data flow | Good | Documented, canonical sources |
| Path handling | Fix needed | Status file path when frozen |
