# AdoptIQ v1.0.1 Code Review – Logic and Error Audit

> **Status: historical snapshot (v1.0.1 release, Feb 5, 2026).** All fixes documented here have been applied. The codebase has since gone through Round 14, 15, and 16 audits. **For current state see `CODE_REVIEW_LATEST.md` and `QUALITY_AUDIT.md`.**

**Date:** February 5, 2026
**Scope:** Full codebase logic and error review for v1.0.1 release

---

## Critical Fixes Applied

### 1. **Optional type hint collision (app_simple.py)**
- **Issue:** `Optional` from `wtforms.validators` shadowed `typing.Optional`, causing `type 'Optional' is not subscriptable` when using `Optional[str]` in type hints.
- **Fix:** Renamed wtforms import to `OptionalValidator` and updated form validators to use `OptionalValidator()`.
- **Impact:** 4 tests were failing; all now pass.

### 2. **CSOne Excel column flexibility (adoptiq_backend.py)**
- **Issue:** `load_csone_excel`, `_prepare_csone`, and `_calc_rates` only checked for `"Title"` and `"Problem Description"`. CSOne exports can use `TITLE`, `SUBJECT`, `DESCRIPTION`, etc.
- **Fix:** Use `LIKELY_TITLE_COLS` and `LIKELY_DESC_COLS` to resolve the correct columns.
- **Impact:** More robust handling of different CSOne export formats.

### 3. **Typing import (app_simple.py)**
- **Issue:** `Optional` was not in scope for `get_latest_csone_from_folder() -> Optional[str]` because only `TypingOptional` was imported.
- **Fix:** Added `Optional` to the typing import.

---

## Logic and Architecture Notes

### Thread safety
- `analysis_status` and `cancellation_flags` use locks (`analysis_status_lock`, `cancellation_flags_lock`).
- `save_analysis_status()` is always called from within `with analysis_status_lock:` blocks.
- Background analysis threads are daemon threads.

### Input validation
- Manager, technology, days, customer name, and file uploads are validated.
- Dangerous characters (SQL-like patterns) are rejected in manager/technology/customer fields.
- File uploads limited to 50MB, `.xlsx`/`.xls` only.

### Path handling
- Frozen app uses `_APP_SUPPORT` (%APPDATA%\AdoptIQ on Windows).
- OneDrive CSOne folder uses `os.path.expandvars('%USERPROFILE%')` for default path.
- `get_latest_csone_from_folder()` handles missing folder and permission errors.

### Data source validation
- `data_source_validator.py` enforces required sources per report type.
- Renewal reports require only Snowflake and team subscriptions; CSOne is optional.

---

## Test Results

```
=== Results: 11 passed, 0 failed ===
```

All tests in `tests/test_reports_extensive.py` pass.

---

## Recommendations (Non-blocking)

1. **Config vs team_config.json:** `config.py` has `MANAGERS` and `TEAM_ROSTER`, but `adoptiq_backend` loads from `team_config.json`. Consider a single source of truth.
2. **save_analysis_status lock:** The docstring says "assumes lock is already held." Consider asserting the lock or documenting call sites.
3. **create_executive_charts:** Uses `outputs/` relative path; when frozen, cwd is `_APP_SUPPORT`, so this should resolve correctly.
4. **Windows %USERPROFILE%:** `os.path.expandvars('%USERPROFILE%')` works on Windows; on Mac/Linux use `$HOME` or `~` if this config is ever shared.

---

## Additional Fixes (Round 2)

### 4. **customer_col never set in _create_enhanced_compact_report**
- **Issue:** `customer_col` was used at line 2289 (`bems_cases[customer_col].nunique()`) but never set, causing potential KeyError/AttributeError when BEMS section runs.
- **Fix:** Added customer column detection (Customer Name, customer_name, etc.) alongside severity_col.

### 5. **extract_bems_refs return bug**
- **Issue:** The function only returned when Transaction ID was non-null; when it was empty, the function implicitly returned None. Also, bemscsc_refs was only checked when Transaction ID existed.
- **Fix:** Moved bemscsc_refs check outside the Transaction ID block; function always returns a string.

### 6. **analyze_feature_requests column names**
- **Issue:** Hardcoded 'Title' and 'Problem Description'; CSOne exports may use TITLE, SUBJECT, DESCRIPTION.
- **Fix:** Use LIKELY_TITLE_COLS and LIKELY_DESC_COLS from adoptiq_backend.

### 7. **enrich_csone_with_arr column names**
- **Issue:** Only checked 'Customer Name'; _prepare_csone outputs customer_name (lowercase).
- **Fix:** Use flexible customer column lookup (Customer Name, customer_name, BU_NAME, Customer).

---

## Files Modified in This Review

- `app_simple.py` – Optional collision fix, typing import, customer_col, extract_bems_refs, analyze_feature_requests, enrich_csone_with_arr
- `adoptiq_backend.py` – CSOne column flexibility (load_csone_excel, _prepare_csone, _calc_rates)
