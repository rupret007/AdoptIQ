# AdoptIQ: Sync Windows PC Changes to Mac Version

**Purpose:** Apply all changes from the Windows PC restore point (commit d6cdd77) to the Mac version so both apps are identical in behavior.

**Use this document:** Paste it into a Cursor session on the Mac AdoptIQ project and ask the AI to implement all changes.

**Last updated:** Includes fixes from multiple audit passes: `.astype(str)` before `.str.contains()`, `DataFrame.get()` replacements, and `estimate_arr` Severity fix in `compact_report_formatter.py`, `app_simple.py`, `adoptiq_backend.py`, and `leader_report_generator.py` (sections 4.8–4.13).

---

## 1. NEW FILE: `report_utils.py`

Create a new file `report_utils.py` in the project root with this exact content:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Report Utilities
Shared formatting, risk scoring explanation, metadata, and canonical data sources for all report types.
"""

from datetime import datetime
from typing import Optional, Union, Any, List, Tuple
import re


# --- Canonical Data Sources (used by all reports) ---
# Format: (metric_name, source_system, verification_note)
DATA_SOURCES_CANONICAL: List[Tuple[str, str, str]] = [
    ('Adoption Barriers', 'CSConsole / Snowflake C360_CS_TASK_C_VW', 'Query by Record ID in CSConsole or Snowflake'),
    ('Support Cases (TAC)', 'CSOne (TAC case data)', 'Query by Case Number / SR Number in CSOne'),
    ('BEMS Escalations', 'CSOne (Transaction ID, bemscsc_refs)', 'Engineering-level escalations; BEMS IDs verifiable in CSOne'),
    ('Service Incidents', 'status.webex.com', 'Public RSS feed; incident IDs verifiable'),
    ('Software Defects', 'help.webex.com, CSC/BST refs in CSOne and Adoption Barriers', 'Defect IDs verifiable in help.webex.com and Bug Search Tool'),
    ('Customer Pulse', 'CSConsole', 'Pulse ratings by customer in CSConsole'),
    ('Action Plans', 'CSConsole', 'Action plans by customer in CSConsole'),
    ('Success Priorities', 'CSConsole', 'Success priorities by customer in CSConsole'),
    ('ARR / Financial', 'Snowflake CX_DB (when available)', 'Contract and ARR data from CX_DB'),
]


def get_data_sources_paragraph_text() -> str:
    """Return the standard Report Data Sources paragraph text for inline use."""
    parts = [
        'All metrics and references in this report cite their origin. ',
        'Adoption Barriers: CSConsole / Snowflake C360_CS_TASK_C_VW. ',
        'Support Cases: CSOne (TAC case data). ',
        'BEMS Escalations: CSOne (Transaction ID, bemscsc_refs — engineering-level escalations). ',
        'Service Incidents: status.webex.com. ',
        'Software Defects: help.webex.com, plus CSC/BST references in CSOne and Adoption Barriers. ',
        'Customer Pulse, Action Plans, Success Priorities: CSConsole.',
    ]
    return ''.join(parts)


def get_data_sources_list() -> List[Tuple[str, str, str]]:
    """Return the canonical data sources list for table or bullet formatting."""
    return list(DATA_SOURCES_CANONICAL)


# --- Standardized Date Formatting ---
DATE_FORMAT_LONG = "%B %d, %Y"           # February 2, 2025
DATE_FORMAT_SHORT = "%Y-%m-%d"          # 2025-02-02
DATE_FORMAT_DATETIME = "%B %d, %Y at %H:%M"  # February 2, 2025 at 14:30
DATE_FORMAT_ISO = "%Y-%m-%d %H:%M:%S"   # 2025-02-02 14:30:00


def format_date(value: Any, style: str = "long") -> str:
    """Format date/datetime for consistent display in reports."""
    if value is None:
        return "N/A"
    try:
        if isinstance(value, str):
            from pandas import to_datetime
            value = to_datetime(value, errors="coerce")
        if hasattr(value, "strftime"):
            fmt = {
                "long": DATE_FORMAT_LONG,
                "short": DATE_FORMAT_SHORT,
                "datetime": DATE_FORMAT_DATETIME,
                "iso": DATE_FORMAT_ISO,
            }.get(style, DATE_FORMAT_LONG)
            return value.strftime(fmt)
    except Exception:
        pass
    return str(value) if value is not None else "N/A"


def format_number(value: Union[int, float, None], decimals: int = 0, as_percent: bool = False) -> str:
    """Format number for consistent display in reports."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        if as_percent:
            return f"{v:.1f}%"
        if decimals == 0:
            return f"{int(v):,}"
        return f"{v:,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value) if value is not None else "N/A"


def format_currency(value: Union[int, float, None], decimals: int = 2) -> str:
    """Format currency for reports."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
        return f"${v:,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value) if value is not None else "N/A"


# --- Risk Scoring Explanation (transparent methodology) ---
RISK_SCORING_EXPLANATION = """
Risk Score Methodology (0–100 scale):
• Adoption Barriers: Critical/High severity (+5 each, max 15), open barriers (+2 each, max 10), base count (+1 each, max 5). Total max: 30 points.
• Support Cases: Volume-based (5–15 points for 1–10+ cases), P1/Critical (+5 each, max 10), P2/High (+2 each, max 5). Total max: 30 points.
• BEMS Escalations: +10 per escalation, max 20 points. BEMS indicates engineering-level issues that often drive churn.
• Contract/Subscription: High-risk or inactive subscriptions add 5–15 points. No subscription data: +10. Total max: 20 points.
• Service Incidents: High-impact (investigating/identified/monitoring) add +3 each, max 15 points. Source: status.webex.com.
• Engagement: No issues may indicate disengagement (+10); high issue volume indicates churn risk (+15). Total max: 20 points.

Categories: CRITICAL (≥70), HIGH (50–69), MEDIUM (30–49), LOW (<30).
"""


def get_risk_scoring_explanation() -> str:
    """Return the risk scoring explanation text for report inclusion."""
    return RISK_SCORING_EXPLANATION.strip()


# --- Report Metadata Footer ---
def get_report_metadata_footer(
    report_type: str,
    analysis_id: str = "",
    manager: str = "",
    customer_name: str = "",
    technology: str = "",
    days: int = 0,
) -> str:
    """Generate standardized report metadata footer text."""
    parts = [
        f"Report Type: {report_type}",
        f"Generated: {format_date(datetime.now(), 'datetime')}",
    ]
    if analysis_id:
        parts.append(f"Analysis ID: {analysis_id}")
    if manager:
        parts.append(f"Manager: {manager}")
    if customer_name:
        parts.append(f"Customer: {customer_name}")
    if technology:
        parts.append(f"Technology: {technology}")
    if days:
        parts.append(f"Analysis Period: {days} days")
    parts.append("AdoptIQ | Data sources: CSConsole, Snowflake, CSOne (TAC/BEMS), status.webex.com")
    return " | ".join(parts)
```

---

## 2. FRONTEND FIX: `templates/analyze.html` – Renewal CSOne File Upload

**Problem:** Renewal reports were always showing 0 support cases because the frontend sent JSON to `/start_customer_renewal_analysis` with `csone_file: ''`, never including the uploaded file.

**Fix:** Change renewal requests to use FormData and POST to `/start_analysis` so the CSOne file is included.

**Find** the renewal block (around the `reportType === 'renewal'` or `reportType === 'renewal_portfolio'` branch):

- Change `endpoint` from `/start_customer_renewal_analysis` to `/start_analysis`
- Replace the JSON payload with FormData that includes the CSOne file when uploaded

**Before (conceptual):**
```javascript
endpoint = '/start_customer_renewal_analysis';
const jsonData = {
    manager: managerValue,
    technology: technologyValue,
    days: parseInt(formData.get('days') || '90'),
    subscription_id: subscriptionId,
    customer_name: customerName,
    report_type: reportType,
    renewal_type: reportType,
    csone_file: ''   // <-- BUG: always empty, file never sent
};
requestBody = JSON.stringify(jsonData);
contentType = 'application/json';
```

**After:**
```javascript
endpoint = '/start_analysis';
const renewalFormData = new FormData();
renewalFormData.append('report_type', reportType);
renewalFormData.append('renewal_type', reportType);
renewalFormData.append('manager', managerValue);
renewalFormData.append('technology', technologyValue);
renewalFormData.append('days', formData.get('days') || '90');
renewalFormData.append('subscription_id', subscriptionId);
renewalFormData.append('customer_name', customerName);
const csoneFile = formData.get('csone_file');
if (csoneFile && csoneFile.size > 0) {
    renewalFormData.append('csone_file', csoneFile);
}
requestBody = renewalFormData;
contentType = 'multipart/form-data';
```

**Note:** The Mac app may use `/start_analysis` or a different route. Ensure the backend endpoint that handles renewal accepts FormData and reads `request.files['csone_file']`, and that the Mac equivalent of `start_analysis` stores `csone_file` in the analysis status for the report generator.

---

## 3. `app_simple.py` (or main Flask app) – Backend Fixes

### 3.1 `calculate_arr_impact_for_issues` – None guard

Add explicit None check at the start of the function:

```python
def calculate_arr_impact_for_issues(ab_norm: pd.DataFrame, arr_data: pd.DataFrame) -> Dict[str, Any]:
    """Calculate combined ARR for customers experiencing specific issues"""
    if ab_norm is None or arr_data is None:
        return {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
    if ab_norm.empty or arr_data.empty:
        return {"total_arr": 0, "issue_breakdown": {}, "top_issues": [], "customer_count": 0, "total_issues": 0}
    # ... rest of function
```

### 3.2 Executive report fallback – use `_create_enhanced_compact_report`

If there is any fallback that imports or uses `comprehensive_executive_formatter` when the executive formatter fails, replace it with `_create_enhanced_compact_report`. The module `comprehensive_executive_formatter` does not exist; the correct fallback is the enhanced compact report.

---

## 4. `compact_report_formatter.py` – Changes

### 4.1 `add_critical_adoption_barriers` – Safe column access

Replace:
```python
critical_ab = ab_data[
    ab_data.get('SEVERITY_C', '').str.contains('Critical|High', case=False, na=False)
]
```

With:
```python
if 'SEVERITY_C' not in ab_data.columns:
    critical_ab = pd.DataFrame()
else:
    critical_ab = ab_data[
        ab_data['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)
    ]
```

### 4.2 `add_renewal_recommendations_detailed` – Handle both Dict[str, float] and Dict[str, Dict]

`risk_scores` can be `Dict[str, float]` or `Dict[str, Dict]` with a `'score'` key. Add helper and use it:

```python
def _score(v):
    return v.get('score', v) if isinstance(v, dict) else v
high_risk_customers = {k: v for k, v in risk_scores.items() if _score(v) >= 6}
# ...
moderate_risk_customers = {k: v for k, v in risk_scores.items() if 4 <= _score(v) < 6}
```

### 4.3 `_generate_key_concerns` and `_generate_immediate_actions`

- Replace `ab_data.get('SEVERITY_C', '')` and `csone_data.get('Severity', '')` with column existence checks and `ab_data['SEVERITY_C'].astype(str).str.contains(...)` / `csone_data['Severity'].astype(str).str.contains(...)`.
- In `_generate_immediate_actions`, `risk_scores.values()` yields dicts like `{'score': 7.5, 'color': 'Red', ...}`. Change:
  ```python
  high_risk_count = len([score for score in risk_scores.values() if score >= 6])
  ```
  To:
  ```python
  high_risk_count = len([v for v in risk_scores.values() if isinstance(v, dict) and v.get('score', 0) >= 6])
  ```

### 4.4 `add_common_problems_section`

Replace:
```python
subjects = ab_data.get('SUBJECT_C', pd.Series()).dropna().str.lower()
descriptions = ab_data.get('DESCRIPTION_C', pd.Series()).dropna().str.lower()
all_text = pd.concat([subjects, descriptions])
```

With:
```python
subjects = ab_data['SUBJECT_C'].dropna().astype(str).str.lower() if 'SUBJECT_C' in ab_data.columns else pd.Series(dtype=object)
descriptions = ab_data['DESCRIPTION_C'].dropna().astype(str).str.lower() if 'DESCRIPTION_C' in ab_data.columns else pd.Series(dtype=object)
all_text = pd.concat([subjects, descriptions]) if not subjects.empty or not descriptions.empty else pd.Series(dtype=object)
```

### 4.5 `add_data_citations_section` and `add_methodology_section`

- Update intro to: "Every metric in this report is traceable to its source. Same canonical data sources used across all AdoptIQ reports."
- Import `get_data_sources_paragraph_text` and `get_data_sources_list` from `report_utils` (with ImportError fallback).
- Add the canonical data sources paragraph, then a run-specific metrics table: use canonical (source, verification) for each metric row; values come from this analysis (e.g. Total Support Cases = len(csone_data), P1/Critical = Severity contains '1|Critical', etc.).
- In methodology, replace hardcoded data source bullets with a loop over `get_data_sources_list()`.
- In the Risk Scoring Methodology subsection, add: `risk_p.add_run('• BEMS escalations: +10 points each (max 20)\n')` (after the "Recent cases" line).

### 4.6 BEMS section – No-data message

When `csone_data.empty`, change the message to:
```
Data availability: No CSOne (TAC) case data was provided for this analysis. BEMS escalations are identified from CSOne (Transaction ID, bemscsc_refs). Upload a CSOne export to include TAC cases and BEMS analysis.
```

### 4.7 `.astype(str)` before `.str.contains()`

For any `SEVERITY_C`, `AB_STATUS_C`, or `Severity` column used with `.str.contains()`, add `.astype(str)` first to avoid errors on NaN or mixed types:

```python
ab_data['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)
customer_ab['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)
customer_ab['AB_STATUS_C'].astype(str).str.contains('Open|New', case=False, na=False)
customer_csone['Severity'].astype(str).str.contains('P1|P2|Critical', case=False, na=False)
```

Apply this pattern everywhere these columns are used with `.str.contains()`.

### 4.8 `create_compact_executive_report` – risk_summary dict

In `create_compact_executive_report`, the `risk_summary` dict must use `.astype(str)` for `critical_adoption_barriers` and `escalated_cases`:

**Find:**
```python
'critical_adoption_barriers': len(ab_data[
    ab_data['SEVERITY_C'].str.contains('Critical|High', case=False, na=False)
]) if not ab_data.empty and 'SEVERITY_C' in ab_data.columns else 0,
'escalated_cases': len(csone_data[
    csone_data['Severity'].str.contains('P1|P2|Critical', case=False, na=False)
]) if not csone_data.empty and 'Severity' in csone_data.columns else 0,
```

**Replace with:**
```python
'critical_adoption_barriers': len(ab_data[
    ab_data['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)
]) if not ab_data.empty and 'SEVERITY_C' in ab_data.columns else 0,
'escalated_cases': len(csone_data[
    csone_data['Severity'].astype(str).str.contains('P1|P2|Critical', case=False, na=False)
]) if not csone_data.empty and 'Severity' in csone_data.columns else 0,
```

---

## 4.9 ADDITIONAL FIXES: `app_simple.py` – `.astype(str)` and `.get()` replacements

Apply these search-and-replace changes in `app_simple.py`:

- **BU_NAME filter:** `filtered_df['BU_NAME'].str.contains(` → `filtered_df['BU_NAME'].astype(str).str.contains(`
- **Portfolio insights severity_col:** `severity_col = ab_norm['SEVERITY_C']` → `severity_col = ab_norm['SEVERITY_C'].astype(str)`
- **Portfolio insights severity_col (csone):** `severity_col = csone_df['Severity']` → `severity_col = csone_df['Severity'].astype(str)`
- **Portfolio insights status_col:** `status_col = csone_df['Status']` → `status_col = csone_df['Status'].astype(str)`
- **Executive risk_summary:** `ab_norm['SEVERITY_C'].str.contains(` → `ab_norm['SEVERITY_C'].astype(str).str.contains(` (and same for `csone_df['Severity']`)
- **Dashboard critical_abs:** `ab_norm['SEVERITY_C'].str.contains('Critical|High'` → `ab_norm['SEVERITY_C'].astype(str).str.contains('Critical|High'`
- **Dashboard escalated_cases:** `csone_df['Severity'].str.contains('P1|P2|Critical'` → `csone_df['Severity'].astype(str).str.contains('P1|P2|Critical'`
- **Renewal risk (customer_ab):** `customer_ab['SEVERITY_C'].str.contains(` → `customer_ab['SEVERITY_C'].astype(str).str.contains(`; same for `AB_STATUS_C`, `Severity`
- **Renewal risk (customer_subs):** `customer_subs['RENEWAL_RISK_CATEGORY'].str.contains(` → add `.astype(str)`; same for `STATUS_C`
- **Portfolio metrics:** Replace `csone_df.get('Case Status', '').str.contains` with `csone_df['Case Status'].astype(str).str.contains` (keep column guard). Same for `Highest Priority` (4 occurrences).
- **Subscription report:** Replace `ab_df.get('SEVERITY_C', '').str.contains` with: `critical_ab = ab_df[ab_df['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)] if 'SEVERITY_C' in ab_df.columns else ab_df.iloc[0:0]`

---

## 4.10 ADDITIONAL FIXES: `adoptiq_backend.py` – Replace `DataFrame.get()` with column checks

In the risk components calculation (around lines 752–786), replace:

**Before:**
```python
critical_barriers = len(ab_df[ab_df.get('SEVERITY_C', '').str.contains('Critical|High', case=False, na=False)])
open_barriers = len(ab_df[ab_df.get('AB_STATUS_C', '').str.contains('Open|New', case=False, na=False)])
# ...
poor_pulse = len(cp_df[cp_df.get('PULSE_RATING__C', '').str.contains('Poor|Bad', case=False, na=False)])
# ...
unresolved_plans = len(ap_df[ap_df.get('STATUS_C', '').str.contains('Open|New|In Progress', case=False, na=False)])
```

**After:**
```python
critical_barriers = len(ab_df[ab_df['SEVERITY_C'].astype(str).str.contains('Critical|High', case=False, na=False)]) if 'SEVERITY_C' in ab_df.columns else 0
open_barriers = len(ab_df[ab_df['AB_STATUS_C'].astype(str).str.contains('Open|New', case=False, na=False)]) if 'AB_STATUS_C' in ab_df.columns else 0
# ...
poor_pulse = len(cp_df[cp_df['PULSE_RATING__C'].astype(str).str.contains('Poor|Bad', case=False, na=False)]) if 'PULSE_RATING__C' in cp_df.columns else 0
# ...
unresolved_plans = len(ap_df[ap_df['STATUS_C'].astype(str).str.contains('Open|New|In Progress', case=False, na=False)]) if 'STATUS_C' in ap_df.columns else 0
```

---

## 4.11 ADDITIONAL FIXES: `leader_report_generator.py` – `.astype(str)` before `.str.contains()`

Add `.astype(str)` before every `.str.contains()` on DataFrame columns. For each pattern below, insert `.astype(str)` before `.str.contains`:

- `data['tac_cases'][col].str.contains(customer` → `data['tac_cases'][col].astype(str).str.contains(customer`
- `adoption_barriers['PRIORITY'].str.contains(` → `adoption_barriers['PRIORITY'].astype(str).str.contains(`
- `data['adoption_barriers']['SEVERITY_C'].str.contains(` → `data['adoption_barriers']['SEVERITY_C'].astype(str).str.contains(`
- `data['action_plans']['BU_NAME'].str.contains(customer` → `data['action_plans']['BU_NAME'].astype(str).str.contains(customer`
- `data['adoption_barriers']['BU_NAME'].str.contains(customer` → `data['adoption_barriers']['BU_NAME'].astype(str).str.contains(customer` (multiple occurrences: `_collect_all_items_for_customer`, BEMS escalation details, defect analysis)
- `data['customer_pulse']['BU_NAME'].str.contains(customer` → `data['customer_pulse']['BU_NAME'].astype(str).str.contains(customer`
- `data['tac_cases']['Customer Name: Customer Name'].str.contains(customer` → `data['tac_cases']['Customer Name: Customer Name'].astype(str).str.contains(customer`
- `data['tac_cases'][customer_col].str.contains(customer` → `data['tac_cases'][customer_col].astype(str).str.contains(customer`

**Note:** Fix **all** occurrences of `data['adoption_barriers']['BU_NAME'].str.contains(customer`, including the BEMS Escalation Details block inside the enhanced Snowflake insights try block (easy to miss).

---

## 4.13 ADDITIONAL FIX: `app_simple.py` – `estimate_arr` DataFrame.get('Severity')

In the `estimate_arr` helper (used when no ARR data is available, around line 1967), replace:

**Before:**
```python
p1_count = len(customer_cases[customer_cases.get('Severity', '') == 'P1'])
p2_count = len(customer_cases[customer_cases.get('Severity', '') == 'P2'])
```

**After:**
```python
p1_count = len(customer_cases[customer_cases['Severity'].astype(str) == 'P1']) if 'Severity' in customer_cases.columns else 0
p2_count = len(customer_cases[customer_cases['Severity'].astype(str) == 'P2']) if 'Severity' in customer_cases.columns else 0
```

**Reason:** `DataFrame.get('Severity', '')` returns a string when the column is missing; `'' == 'P1'` yields a scalar `False`, which breaks row indexing.

---

### 4.12 `create_compact_executive_report` – structure (original 4.8)

- Add "Top 10 Focus Accounts by Risk" table (sorted by score, top 10).
- Add Report Metadata section using `get_report_metadata_footer` from `report_utils`.
- Add page break before long sections.
- In `risk_summary`, ensure `critical_adoption_barriers` and `escalated_cases` and `key_concerns` use `.astype(str).str.contains()` for SEVERITY_C and Severity.

---

## 5. `advanced_renewal_analyzer.py`

In `_create_renewal_data_sources`:

- Change heading from "Data Sources & Verification" to "Report Data Sources".
- Import `get_data_sources_paragraph_text` and `get_data_sources_list` from `report_utils` (with ImportError fallback).
- Add canonical data sources paragraph.
- Replace the old 4-column table with a 3-column table: Metric, Source System, Verification Method. Populate from `get_data_sources_list()`.
- Add subsection heading "Report-Specific Snowflake Tables (this analysis)".
- **Report-specific sources** (account_info, support_metrics, adoption_metrics, etc.): Render as **bullet paragraphs** instead of table rows. Format: `p.add_run(f'• {source_name}: ').bold = True` then `p.add_run(f'{system} – {verification}')`. Do NOT add these as table rows.

---

## 6. `enhanced_snowflake_insights.py`

- Change "Data Sources Overview" to **"Report Data Sources"** (level 1).
- Import `get_data_sources_paragraph_text` from `report_utils` and add the canonical paragraph.
- Add subsection **"Enhanced Snowflake Tables (this report)"** (level 2) with text: "This report **additionally** leverages 89+ Snowflake tables across 8 major categories:" (note: "additionally").
- In `_add_comprehensive_source_attribution`: Change "Comprehensive Source Attribution" to level 2 (was level 1). Add "Report Data Sources" (level 1) with canonical paragraph first, then "Comprehensive Source Attribution" (level 2).

---

## 7. `executive_intelligence_formatter.py`

In `add_data_citations_section`:

- Change heading from "Data Citations & Source Attribution" to **"Report Data Sources"**.
- Update intro to: "All data in this report is traceable. Same canonical sources used across all AdoptIQ reports."
- Import `get_data_sources_paragraph_text` and `get_data_sources_list` from `report_utils` (with ImportError fallback).
- Add canonical data sources paragraph.
- Add "Record counts in this run:" (bold) before the citations list.
- Simplify citations: "records from Excel upload" → "records"; "records from CSConsole (Snowflake)" → "records"; "Extracted from CSOne Transaction ID column (BEMS pattern)" → "Extracted from CSOne Transaction ID / bemscsc_refs"; remove "External Defects" line; shorten "Service Incidents" and "AI Insights" to match canonical style.

---

## 8. `leader_report_generator.py`

Add new method `_add_report_data_sources()` that:

- Imports `get_data_sources_paragraph_text` and `get_data_sources_list` from `report_utils` (with ImportError fallback).
- Adds "Report Data Sources" heading.
- Adds canonical paragraph and table (Metric, Source System, Verification).

Call `_add_report_data_sources()` at the end of the report generation flow (before final page break).

---

## 9. `app_simple.py` – Renewal report and related fixes

### 9.1 Renewal report content (`_create_simple_renewal_report`)

- **Report Data Sources** section: heading "Report Data Sources", then `get_data_sources_paragraph_text()` with ImportError fallback (replace hardcoded bold source list).
- **Executive Summary** (new section at top, before Customer Health Dashboard): 2–3 sentence overview with risk score, category, ab_count, case_count, bems_count. Use `format_number` for counts. For portfolio: "This portfolio of N customers has overall renewal risk score X/100 (CATEGORY). N customer(s) require immediate attention. Key metrics: ..." For single: "Customer has renewal risk score X/100 (CATEGORY). Key metrics: ... See Key Findings and Recommendations."
- **Top 10 Focus Accounts by Risk** (portfolio mode only): After Executive Summary, add page break, heading "Top 10 Focus Accounts by Risk", table with columns Rank, Customer, Risk Score, Category. Sort `customer_analyses` by `renewal_risk_score` (or `overall_risk_score`), take top 10. Score format: "X.X/100".
- **Risk Scoring Methodology** section: use `get_risk_scoring_explanation()` from report_utils (with fallback) and render each line.
- **Report metadata footer**: use `get_report_metadata_footer()` with report_type, manager, customer_name, technology, days.
- **Format helpers**: use `format_date`, `format_number` from report_utils where dates/numbers are displayed.
- **When Support Cases and BEMS are both 0**: Update dash_note to start with "Data availability: " (bold), then explain sources (CSOne TAC primary, Snowflake when no file). If `support_cases_from_snowflake`: "This report used Snowflake; no support cases matched the selected scope." Else: "No CSOne file was provided and no Snowflake support cases were returned for this scope, so counts show 0." End with: "To get complete TAC cases and BEMS escalations, upload a CSOne export when starting the analysis." Source in italic.
- **When BEMS = 0** (no_bems section in BEMS Escalation Analysis): Start with "Data availability: " (bold). Two branches: (a) If case_count==0 (both zero): "BEMS and support cases come from CSOne (TAC) or Snowflake SUPPORT_CASES. No CSOne file was provided and no support cases were returned from Snowflake for this scope, so both show as zero. To get full TAC cases and BEMS escalations, upload a CSOne export when starting the analysis." Source in italic. (b) If case_count>0 (cases exist, BEMS=0): "No BEMS escalations detected. " + (if from_snowflake: "Support cases came from Snowflake; BEMS are only available from CSOne (TAC). Upload a CSOne export for BEMS analysis. " else: "The absence of BEMS in the provided CSOne data is a positive indicator. ") + "Source: CSOne (Transaction ID, bemscsc_refs)."

### 9.2 Portfolio renewal recommendations (was missing)

In `run_customer_renewal_analysis`, when building `renewal_analysis` for portfolio mode, add a `recommendations` key. Build `portfolio_recs` from: high-risk customers (≥70), medium-risk (30–69), adoption barriers, BEMS, and category. If empty, use default recs like "Continue regular portfolio engagement...". Previously the "Recommendations" heading had no content.

In the Recommendations section of the report: if `recommendations` is empty, add fallback paragraph (italic): "No specific recommendations at this time. Continue monitoring adoption barriers, customer pulse, and support case trends."

### 9.3 Single customer CSOne filter – case-insensitive match

When filtering CSOne for a single customer, try exact match first. If no match, try case-insensitive match on `customer_name`. Use `customer_name.astype(str).str.strip()` and `.str.lower()` for comparison. Log when case-insensitive match is used.

### 9.4 CSOne scope filter fallback

When the strict scope filter returns 0 cases but `csone_df_prepared` has data, fall back to `_apply_scope_filter_csone_inclusive` (technology + date only). Log a warning that team names may not match Excel.

### 9.7 Leader report – CSOne loading (`run_leader_report_generation`)

- Fetch team subscriptions first (needed to scope CSOne to manager's portfolio).
- Resolve `csone_path`: if `csone_file` is filename only, try `UPLOAD_FOLDER` + filename.
- Load CSOne with `load_csone_excel`, then `_prepare_csone` with team_subs_df.
- If team_subs_df not empty: use `_apply_scope_filter_csone("All", days, sub_ids, team_customer_names)` to scope to team portfolio.
- If team_subs_df empty: use `_apply_scope_filter_csone_inclusive("All", days)` (date filter only).
- Ensure `csone_df` is never None (use `pd.DataFrame()` as default).
- Update status message from "Loaded X TAC cases from CSOne..." to **"Loaded X TAC cases in scope for team..."**.

---

## 10. PyInstaller / Build spec (Mac equivalent)

Add `report_utils` to `hiddenimports` in the Mac build spec (e.g. `adoptiq_mac.spec` or equivalent):

```python
hidden_imports = [
    # ... existing ...
    'report_utils',
    # ...
]
```

If the Windows spec excludes `playwright`, consider adding it to Mac excludes too (reduces bundle size).

---

## 11. README.md and OUTBOX/README.md

**Add** to the CSOne Report section (after the Excel export line):

```
**Renewal reports:** Upload the CSOne file when generating renewal reports (single or portfolio) to include support case data. Without it, the report will show 0 cases unless Snowflake support case data is available.
```

**Remove** the entire "Known Limitations (Non-Critical)" section if it exists (the 4 bullet points: charts not in reports, Playwright/RSS, ARR/TECHNOLOGY_C warnings, SUPPORT_CASES table). The README should end at the Troubleshooting section.

---

## 12. NEW: `tests/test_reports_extensive.py`

Create `tests/test_reports_extensive.py` with tests for:

- `report_utils`: format_date, format_number, format_currency, get_risk_scoring_explanation, get_report_metadata_footer
- `_calculate_simple_renewal_risk` (with data and empty data)
- `_create_simple_renewal_report` (single, portfolio, no data)
- `create_compact_executive_report` (with data, empty)
- `calculate_renewal_risk_scores` edge cases (empty, AB only, CSOne only)
- `AdvancedRenewalAnalyzer` (mock)
- `LeaderReportGenerator` structure (include `_add_report_data_sources` in structure check)
- `ExecutiveIntelligenceFormatter`

**Easiest:** Copy the full file from the Windows project (`tests/test_reports_extensive.py`). Run with: `python tests/test_reports_extensive.py`

---

## 13. Mac-specific notes

- **Build script:** Windows uses `build_pc.bat`; Mac likely uses `build_mac.sh` or similar. Ensure the Mac build script includes `report_utils` in the spec and runs the same PyInstaller flow.
- **Port:** Windows uses 5001; confirm Mac uses the same or update README.
- **Paths:** `%APPDATA%\AdoptIQ` becomes `~/Library/Application Support/AdoptIQ` or similar on Mac. Update README paths for Mac.
- **SmartScreen:** Mac doesn't have SmartScreen; that section can be Mac-specific or omitted.
- **Code signing (adhoc only):** Builds use adhoc signing — there is no Apple Developer ID. To keep the DMG drag-to-install flow working on Apple Silicon:
  - `build_mac.sh` and `build_mac_dmg.sh` MUST use `ditto` (not `cp -R`) to copy `dist/AdoptIQ.app`, then `xattr -cr` the result and re-sign adhoc with `codesign --force --deep --sign - --timestamp=none`, then verify with `codesign --verify --deep --strict`.
  - `build_mac_dmg.sh` MUST include `scripts/mac/Unblock_AdoptIQ.command` (renamed to `Unblock AdoptIQ.command` inside the DMG, chmod +x) and `scripts/mac/READ_ME_FIRST.txt` alongside `AdoptIQ.app` and the `Applications` symlink.
  - Without these steps, the installed app silently bounces in the Dock and dies on first launch because Gatekeeper / AMFI rejects a quarantined adhoc-signed bundle.
  - End-user docs (README.md macOS install section) MUST tell users to double-click `Unblock AdoptIQ.command` once after dragging the app to `/Applications` (or run `xattr -dr com.apple.quarantine /Applications/AdoptIQ.app` manually).

## 14. Optional (not required for app parity)

- **CODE_REVIEW.md, CODE_REVIEW_AUDIT.md, CODE_REVIEW_DETAILED.md** – Documentation of the audit. Can copy for reference but not needed for identical behavior.

---

## Summary checklist

- [ ] Create `report_utils.py`
- [ ] Fix `templates/analyze.html` – renewal FormData + CSOne file
- [ ] Fix `calculate_arr_impact_for_issues` – None guard
- [ ] Fix executive fallback – use `_create_enhanced_compact_report` (no `comprehensive_executive_formatter`)
- [ ] Update `compact_report_formatter.py` – all DataFrame/risk_scores/str.contains fixes (4.1–4.8), BEMS methodology bullet
- [ ] Update `app_simple.py` – `.astype(str)` and `.get()` fixes (sections 4.9, 4.13), renewal (all 9.1–9.7)
- [ ] Update `adoptiq_backend.py` – replace `DataFrame.get()` with column checks (section 4.10)
- [ ] Update `leader_report_generator.py` – `.astype(str)` before `.str.contains()` (section 4.11), add `_add_report_data_sources`
- [ ] Update `advanced_renewal_analyzer.py` – canonical data sources, report-specific as bullet paragraphs
- [ ] Update `enhanced_snowflake_insights.py` – canonical data sources
- [ ] Update `executive_intelligence_formatter.py` – canonical data sources, heading, citations simplification
- [ ] Add `report_utils` to build spec hiddenimports, add `playwright` to excludes
- [ ] Update README – add CSOne renewal note, remove Known Limitations section
- [ ] Create/copy `tests/test_reports_extensive.py` and run tests
- [ ] **Post-sync: Excel export parity** – In `write_excel_workbook` fallback: write **Report_Info** sheet first (Export type: Standard (fallback); Note explaining all data present); then main sheets; then **CSConsole** sheets (CSConsole_Action_Plans, CSConsole_Customer_Pulse, CSConsole_Success_Priorities, CSConsole_Adoption_Barriers) when `csconsole_data` is provided.
