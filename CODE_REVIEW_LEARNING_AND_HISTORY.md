# Code and Logic Review: History, Admin, and Learning

> **Status: historical snapshot (Feb 2025).** Earliest review of the History/Admin/Learning surfaces. Findings here have all been applied. The codebase has since gone through Round 14, 15, and 16 audits in 2026. **For current state see `CODE_REVIEW_LATEST.md` and `QUALITY_AUDIT.md`.**

**Date:** 2025-02
**Scope:** History page, Admin link, Admin console "Back to AdoptIQ", Learn-from-past-analyses (report_insights), and related flows.

---

## 1. History Page

### Route (`app_simple.py` – `/history`)
- **Data source:** `get_report_history()` when callable; otherwise `[]`. Guard added: if return is not a list (e.g. `None`), treat as `[]`.
- **Mapping:** Each `r` is a dict from audit DB; view uses `.get(..., '')` / `or '—'` / `or 'Unknown'` so missing keys are safe.
- **Template:** Receives `analyses` (always a list). Uses `{% set st = analysis.start_time or 'Unknown' %}` so date/time slicing never sees `None`. Period column uses `analysis.days is not none and analysis.days != ''` else "—".

### Template (`templates/history.html`)
- **View/Download:** Both use `/progress/${encodeURIComponent(analysisId)}` (no `/results/`). Progress page shows status and download links when the run is in memory or in `analysis_status.json`.
- **Stats:** “Last 7 Days” label corrected to “Last 7 Reports” (value is `analyses[:7]|length` – most recent 7 reports, not calendar-based).
- **Active Managers:** Uses `analyses|map(attribute='manager')|unique|list|length` (Jinja2 2.10+ `unique`).
- **Delete:** Client-only (row styling + “Analysis deleted”); no API or DB delete. Refresh restores list.

### Status
- Logic and edge cases covered; one label fix and one robustness fix (raw not a list) applied.

---

## 2. Admin Link (Main App)

### Context (`app_simple.py`)
- `inject_version()` provides `admin_console_url` from `os.environ.get('ADOPTIQ_ADMIN_URL', 'http://localhost:5151')`. `os` is imported at top level.

### Template (`templates/base.html`)
- Admin nav item wrapped in `{% if admin_console_url %}` so it is hidden when URL is empty.
- Link uses `target="_blank"` and `rel="noopener noreferrer"`.

### Status
- Correct and consistent.

---

## 3. Admin Console

### “Back to AdoptIQ”
- `main_app_url=MAIN_APP_URL` passed into dashboard template; link in header with `{% if main_app_url %}`; opens in new tab with `rel="noopener noreferrer"`.

### Defensive Template
- **user_agent:** `(connection.user_agent or '')[:50]`; ellipsis only when length > 50.
- **error.message:** `(error.message or '')[:100]`; ellipsis only when length > 100.
- **risk_level:** `(connection.risk_level or 'unknown')|lower` for CSS class; display uses `connection.risk_level or 'N/A'`.
- **request_count:** In `get_ip_connections()`, `request_count` set to `row[3] if row[3] is not None else 0` so `ip_connections|sum(attribute='request_count')` never sees `None`.

### Status
- All defensive fixes in place.

---

## 4. Learn from Past Analyses

### Storage (`enhanced_admin_dashboard_v2.py`)
- **Table:** `report_insights` (request_id, report_type, manager, technology, customer_name, insights_json, created_at) created in `init_database()`.
- **store_report_insights:** Writes JSON dict; handles `None`/empty; used from app_simple after compact, renewal, comprehensive, subscription, and leader completion.
- **Payloads:** Comprehensive stores customer_count, barrier_count, case_count, summary_line; renewal adds risk_theme when `risk_level` is non-empty; others store at least summary_line.

### Retrieval (`get_learned_insights`)
- **Query:** Filters by manager/technology (empty string = “any”); `ORDER BY created_at DESC`, `LIMIT ?`.
- **Bullets:** Built from customer_count, barrier_count, summary_line (truncated to 80 chars), risk_theme (only if non-empty). `summary_line` and `risk_theme` normalized with `str()` and `.strip()` where used.
- **Return:** Empty string on error or no data; otherwise a short “Learned from past analyses…” block.

### Injection (`adoptiq_backend.py`)
- **Import:** `get_learned_insights` from `enhanced_admin_dashboard_v2` with try/except; fallback returns `""`.
- **Usage:** Before portfolio LLM call, `learned = get_learned_insights(manager, tech, limit=5)`; if non-empty, prepended to `portfolio_briefing` with `"\n\n---\n\n"`.
- **Prompt:** `PROMPT_PORTFOLIO_TEMPLATE.format(MANAGER=manager, TECHNOLOGY=tech)` so both placeholders are set.

### App_simple Integration
- **Import:** `store_report_insights` and `get_learned_insights` with no-op fallbacks when audit module is missing.
- **Call sites:** Compact, customer renewal, comprehensive, subscription, leader – each call `store_report_insights` with an appropriate payload after `record_report_completion`.

### Status
- End-to-end flow is consistent; safeguards for None/empty and type handling are in place.

---

## 5. Cross-Cutting

- **Circular imports:** `enhanced_admin_dashboard_v2` does not import `adoptiq_backend` or `app_simple`; adoptiq_backend can import get_learned_insights from it.
- **Linting:** No linter errors reported for the modified files.
- **History “Delete”:** Purely UI; no backend or DB change. Documented in review; optional future work to add a real delete API.

---

## 6. Changes Made During This Review

1. **history.html:** “Last 7 Days” → “Last 7 Reports” (value is last 7 reports, not date-filtered).
2. **app_simple.py (history):** If `get_report_history()` returns non-list (e.g. `None`), coerce to `[]` before building `analyses`.

All other behavior was already correct or had been fixed in earlier passes.
