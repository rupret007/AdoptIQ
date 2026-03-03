# AdoptIQ Deep Code Review

**Date:** February 5, 2026  
**Scope:** Full application – security, architecture, error handling, data flow, edge cases

---

## 1. Executive Summary

The AdoptIQ codebase is production-ready with solid validation, parameterized SQL, and path traversal protection. This deep review identifies one security edge case, documents the SUPPORT_CASES fallback behavior, and provides actionable recommendations for hardening and maintainability.

---

## 2. Architecture Overview

| Component | Responsibility |
|-----------|----------------|
| `app_simple.py` | Flask routes, form validation, analysis orchestration (~10k lines) |
| `adoptiq_backend.py` | Snowflake, CSOne, CSConsole, report generation (~4.6k lines) |
| `compact_report_formatter.py` | Compact/executive report formatting |
| `leader_report_generator.py` | Leader reports by manager |
| `data_source_validator.py` | Pre-report data validation |
| `config.py` | Env-based config, no hardcoded secrets |

**Data flow:** Form → validation → Snowflake/CSOne/CSConsole → formatters → Word/Excel output

---

## 3. Security Findings

### 3.1 Path Traversal – Edge Case (Medium)

**Location:** `app_simple.py` – `_resolve_csone_path_safe()`

**Issue:** `resolved.startswith(uploads_dir)` can allow sibling directories when `uploads_dir` has no trailing separator.

Example: If `uploads_dir = "C:\uploads"`, then `"C:\uploads2\file.xlsx"` passes because `"C:\uploads2".startswith("C:\uploads")` is `True`.

**Recommended fix:**
```python
# Use normalized path with explicit subpath check
resolved_norm = os.path.normpath(resolved)
base_norm = os.path.normpath(uploads_dir)
if resolved_norm != base_norm and not resolved_norm.startswith(base_norm + os.sep):
    return None
# Same for onedrive_dir
```

### 3.2 Input Validation – Good

- **Manager/Technology:** Length limits (100 chars), blocklist for `'`, `"`, `;`, `--`, `/*`, `*/`, `xp_`, `sp_`
- **Customer name:** Optional, 200 chars, SQL/script injection checks
- **Days:** 1–365 via `validate_days_input()` and inline checks
- **File uploads:** `secure_filename`, 50MB limit, `.xlsx`/`.xls` only

### 3.3 SQL Injection – Good

- All Snowflake queries use parameterized placeholders (`%s`) with `cur.execute(sql, params)`
- Table/column names come from constants (`DSM_TABLE`, `AB_TABLE`) or hardcoded strings
- `search_subscriptions_by_customer` uses `(f'%{customer_name}%', limit)` as a single parameter – safe

### 3.4 CSRF

- CSRF enabled for form submissions
- AJAX requests (`X-Requested-With: XMLHttpRequest`) bypass CSRF and rely on manual validation
- **Note:** Header can be forged; acceptable for localhost-only use. If exposed to a network, consider origin checks or token-based CSRF for AJAX.

### 3.5 Secrets

- Credentials from env vars and Keeper
- No hardcoded secrets in config
- `_bundled_secrets` used when frozen for embedded config

---

## 4. Snowflake & Data

### 4.1 SUPPORT_CASES Table – Documented Limitation

**Location:** `adoptiq_backend.py` – `fetch_support_cases_snowflake()`

**Finding:** `CX_DB.CX_SWSSBST_BR.SUPPORT_CASES` does not exist (or is not accessible). The function tries three query variants and always returns an empty DataFrame. Renewal reports that rely on Snowflake support cases when no CSOne file is uploaded will show 0 cases.

**Recommendation:** Document this in user-facing help or SNOWFLAKE_USAGE.md. Consider removing the fallback or adding a clear “Support cases: N/A (table not available)” message in reports.

### 4.2 Connection & Cursor Management

- `fetch_adoption_barriers`, `fetch_arr_data`, `fetch_support_cases_snowflake` use `try/finally` with `cur.close()`
- `load_and_merge_data_for_subscription` closes `ctx` in `finally` but does not close `cur` – Snowflake connector typically closes cursors when connection closes; low risk

### 4.3 Days Validation in Backend

- `fetch_adoption_barriers` raises `ValueError` for `days < 1 or days > 365` – good
- Other fetch functions assume valid `days`; app-level validation is the primary gate

---

## 5. Error Handling

### 5.1 Broad Exception Handling

- Many `except Exception as e` blocks log and return fallbacks (e.g. empty DataFrame)
- Appropriate for resilience; consider logging `exc_info=True` in critical paths for debugging

### 5.2 Silent Swallowing

**Location:** `app_simple.py` – lines 4945, 4964, 4983, 5007

```python
except Exception: pass
```

Used in `_na()`, `_is_empty()`, `_first_avail()` for `pd.isna()` checks. Low risk (defensive checks) but could log at debug level.

### 5.3 Data Source Validation

- `raise_validation_error_if_invalid()` used before report generation
- `DataSourceValidationError` provides clear messages for missing sources

---

## 6. Code Quality

### 6.1 Duplication

- **TECH_FILTERS / OFFICIAL_CATEGORIES:** Defined in both `config.py` and `adoptiq_backend.py` with slight differences. Consider single source of truth (e.g. config only).
- **Manager list:** `config.py` has legacy `MANAGERS`; app uses `adoptiq_backend.MANAGERS` from `team_config.json`. Config.py MANAGERS is unused.

### 6.2 File Size

- `app_simple.py` ~10k lines – consider splitting routes (e.g. `routes/analysis.py`, `routes/renewal.py`, `routes/admin.py`)
- `adoptiq_backend.py` ~4.6k lines – consider extracting Snowflake, CSOne, and report modules

### 6.3 Logging

- Mix of `logging` and `logger`; `adoptiq_backend` uses `logger` consistently
- `logging.info` vs `logger.info` – minor inconsistency

---

## 7. Edge Cases & Robustness

### 7.1 Frozen / PyInstaller

- `_BASE_PATH`, `_APP_SUPPORT` correctly handle frozen vs dev
- Status file uses `_APP_SUPPORT / STATUS_FILE` (fixed in prior audit)
- CA bundle from certifi for Snowflake/requests when frozen

### 7.2 Team Config

- `team_config.json` has `"Shams"`; `_get_default_team_config()` has `"Shams"`; `config.py` has `"Josh Horowitz"` – config.py is legacy, not used
- If `team_config.json` is missing, fallback works; managers from JSON are authoritative

### 7.3 Optional Modules

- `enhanced_adoptiq_backend`, `executive_intelligence_formatter`, `enhanced_admin_dashboard_v2` – graceful ImportError handling
- `EnhancedDefectAnalyzer`, `BEMSEscalationAnalyzer`, `ARRSentimentAnalyzer` – optional in leader_report_generator

---

## 8. Recommendations

### High Priority

1. **Fix path traversal edge case** – Use `os.path.normpath` and `base + os.sep` check in `_resolve_csone_path_safe()`.
2. **Document SUPPORT_CASES** – Add note in SNOWFLAKE_USAGE.md and optionally in renewal report UI when support cases are empty due to missing table.

### Medium Priority

3. **Unify TECH_FILTERS / OFFICIAL_CATEGORIES** – Single source in `config.py`; backend imports from config.
4. **Add `end_time` to datetime parsing** – In `load_analysis_status`, include `end_time` in the list of keys parsed from ISO strings if used.
5. **Split app_simple.py** – Extract route blueprints to improve maintainability.

### Low Priority

6. **Extract magic numbers** – 50MB, 1.5s browser delay, 365 days – into `config.py` or env.
7. **Replace `except Exception: pass`** – With `except Exception: logger.debug(...)` in normalization helpers.
8. **Integration tests** – Mock Snowflake/CSOne for full report flows.

---

## 9. Test Coverage

- `tests/test_reports_extensive.py` – report_utils, renewal risk, compact reports
- Data source validation and formatters covered
- Consider adding tests for `_resolve_csone_path_safe` edge cases and validation functions

---

## 10. Summary Table

| Area | Status | Notes |
|------|--------|-------|
| SQL injection | ✅ | Parameterized queries throughout |
| Path traversal | ⚠️ | One edge case (sibling dir) |
| Input validation | ✅ | Manager, tech, days, file |
| CSRF | ✅ | Enabled; AJAX bypass for local use |
| Secrets | ✅ | Env/Keeper, no hardcoding |
| Error handling | ✅ | Broad but logged |
| Snowflake SUPPORT_CASES | ⚠️ | Table missing; fallback returns empty |
| Code structure | ⚠️ | Large files; consider splitting |
