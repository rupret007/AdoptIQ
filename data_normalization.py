"""Shared normalization helpers for AdoptIQ report consistency."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Canonical status buckets
OPEN_STATUS_PATTERNS = (
    r"\bopen\b",
    r"\bnew\b",
    r"\bin\s*progress\b",
    r"\bworking\b",
    r"\breopened?\b",
    r"\bpending\b",
)
CLOSED_STATUS_PATTERNS = (
    r"\bclosed?\b",
    r"\bresolved?\b",
    r"\bcomplete(d)?\b",
    r"\bdone\b",
    r"\bcancelled?\b",
)

# Case type classification heuristics
PROVISIONING_PATTERNS = (
    r"\bprovision(ing|ed)?\b",
    r"\benable(ment|d)?\b",
    r"\bturn\s*on\b",
    r"\bfeature\s*request\b",
    r"\blicense\b",
    r"\bentitlement\b",
    r"\bonboard(ing)?\b",
    r"\bsetup\b",
    r"\bconfig(uration|ure)?\b",
)
BREAK_FIX_PATTERNS = (
    r"\bbreak[\s-]*fix\b",
    r"\bincident\b",
    r"\boutage\b",
    r"\berror\b",
    r"\bfail(ure|ed|ing)?\b",
    r"\bdefect\b",
    r"\bbug\b",
    r"\bcrash\b",
    r"\bdegrad(ed|ation)?\b",
    r"\bescalat(e|ion)\b",
    r"\btechnical\b",
    r"\bsev[ -]?[1-2]\b",
)

BEMS_ID_PATTERN = re.compile(r"\bBEMS[- ]?\d+\b|\bBEMS\b", re.IGNORECASE)

LIKELY_OPEN_DATE_COLS = (
    "Date/Time Opened",
    "Created",
    "Created Date",
    "OPEN_DATE",
    "OPEN_DATE_C",
    "CREATED_DATE",
    "CREATED_DATE_C",
    "CREATEDDATE",
)
LIKELY_CLOSED_DATE_COLS = (
    "Date/Time Closed",
    "Closed",
    "Closed Date",
    "CLOSED_DATE",
    "CLOSED_DATE_C",
    "RESOLVED_DATE",
    "RESOLVED_DATE_C",
    "LAST_MODIFIED_DATE",
    "LASTMODIFIEDDATE",
)
LIKELY_STATUS_COLS = (
    "Case Status",
    "Status",
    "STATUS",
    "STATUS_C",
    "AB_STATUS_C",
    "state",
)
LIKELY_PRIORITY_COLS = (
    "Severity",
    "SEVERITY",
    "SEVERITY_C",
    "Highest Priority",
    "Priority",
    "PRIORITY",
    "PRIORITY_C",
)
LIKELY_CASE_TYPE_COLS = (
    "Case Type",
    "CASE_TYPE",
    "CASE_TYPE_C",
    "Request Type",
    "REQUEST_TYPE",
    "REQUEST_TYPE_C",
    "Category",
    "CATEGORY",
    "CATEGORY_C",
)
LIKELY_CUSTOMER_COLS = (
    "customer_name",
    "Customer Name",
    "BU_NAME",
    "CUSTOMER_NAME",
    "Customer",
    "ACCOUNT_NAME",
)
LIKELY_ACCOUNT_ID_COLS = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "ACCOUNT_ID",
)

# Shared account-column candidates for cross-source parity/filtering.
ACCOUNT_COLUMN_CANDIDATES = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "DSM_ACCOUNT_ID_C",
    "ACCOUNT_ID",
    "ACCOUNT",
    "ACCOUNTID",
    "AccountId",
    "Account ID",
    "Account Id",
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in {"", "none", "nan", "null"}:
        return ""
    return text


def _clean_name_for_key(name: str) -> str:
    lowered = _clean_text(name).lower()
    if not lowered:
        return ""
    lowered = re.sub(r"\s+", " ", lowered).strip()
    lowered = re.sub(r"[,.\-_/]+", " ", lowered)
    lowered = re.sub(
        r"\b(inc|incorporated|corp|corporation|llc|ltd|limited|co|company|plc)\b",
        "",
        lowered,
    )
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def normalize_customer_name(value: Any) -> str:
    """Normalize customer name for display and joins."""
    text = _clean_text(value)
    if not text:
        return "Unknown"
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_customer_lookup(team_subs_df: Optional[pd.DataFrame]) -> Dict[str, Dict[str, str]]:
    """Build account-id and fuzzy customer-name lookups from subscription data."""
    account_to_customer: Dict[str, str] = {}
    key_to_customer: Dict[str, str] = {}
    if team_subs_df is None or team_subs_df.empty:
        return {"account_to_customer": account_to_customer, "key_to_customer": key_to_customer}

    safe = team_subs_df.copy()
    if "BU_NAME" not in safe.columns:
        safe["BU_NAME"] = ""
    safe["BU_NAME"] = safe["BU_NAME"].apply(normalize_customer_name)
    for _, row in safe.iterrows():
        customer = normalize_customer_name(row.get("BU_NAME"))
        if customer == "Unknown":
            continue
        key = _clean_name_for_key(customer)
        if key and key not in key_to_customer:
            key_to_customer[key] = customer
        for account_col in LIKELY_ACCOUNT_ID_COLS:
            if account_col in safe.columns:
                account_id = _clean_text(row.get(account_col))
                if account_id:
                    account_to_customer[account_id] = customer
    return {"account_to_customer": account_to_customer, "key_to_customer": key_to_customer}


def resolve_customer_name(
    row: pd.Series,
    customer_lookup: Optional[Dict[str, Dict[str, str]]] = None,
    customer_columns: Sequence[str] = LIKELY_CUSTOMER_COLS,
    account_columns: Sequence[str] = LIKELY_ACCOUNT_ID_COLS,
) -> str:
    """Resolve canonical customer name from row using account ID and fuzzy lookup."""
    lookup = customer_lookup or {"account_to_customer": {}, "key_to_customer": {}}
    account_to_customer = lookup.get("account_to_customer", {})
    key_to_customer = lookup.get("key_to_customer", {})

    for col in account_columns:
        if col in row.index:
            account_id = _clean_text(row.get(col))
            if account_id and account_id in account_to_customer:
                return account_to_customer[account_id]

    for col in customer_columns:
        if col in row.index:
            raw = normalize_customer_name(row.get(col))
            if raw != "Unknown":
                key = _clean_name_for_key(raw)
                if key and key in key_to_customer:
                    return key_to_customer[key]
                return raw
    return "Unknown"


def normalize_status_label(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return "Unknown"
    if any(re.search(pat, text) for pat in CLOSED_STATUS_PATTERNS):
        return "Closed"
    if any(re.search(pat, text) for pat in OPEN_STATUS_PATTERNS):
        return "Open"
    return "Unknown"


def normalize_priority_label(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return "Unknown"

    # Common numeric-only encodings from CSOne exports.
    if text in {"1", "2", "3", "4"}:
        return {"1": "P1", "2": "P2", "3": "P3", "4": "P4"}[text]

    if re.search(r"\bp[\s\-_:]*1\b|\bsev(?:erity)?[\s\-_:]*1\b|\bpriority[\s\-_:]*1\b|\bcritical\b", text):
        return "P1"
    if re.search(r"\bp[\s\-_:]*2\b|\bsev(?:erity)?[\s\-_:]*2\b|\bpriority[\s\-_:]*2\b|\bhigh\b", text):
        return "P2"
    if re.search(r"\bp[\s\-_:]*3\b|\bsev(?:erity)?[\s\-_:]*3\b|\bpriority[\s\-_:]*3\b|\bmedium\b|\bmoderate\b", text):
        return "P3"
    if re.search(r"\bp[\s\-_:]*4\b|\bsev(?:erity)?[\s\-_:]*4\b|\bpriority[\s\-_:]*4\b|\blow\b", text):
        return "P4"
    return "Unknown"


def normalize_severity_label(value: Any) -> str:
    priority = normalize_priority_label(value)
    return {
        "P1": "Critical",
        "P2": "High",
        "P3": "Medium",
        "P4": "Low",
    }.get(priority, "Unknown")


def parse_datetime_series(series: pd.Series) -> pd.Series:
    try:
        parsed = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
    except TypeError:
        parsed = pd.to_datetime(series, errors="coerce", utc=True)
    try:
        parsed = parsed.dt.tz_convert(None)
    except Exception:
        try:
            parsed = parsed.dt.tz_localize(None)
        except Exception:
            pass
    return parsed


def first_existing_column(columns: Iterable[str], candidates: Sequence[str]) -> Optional[str]:
    available = set(columns)
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def detect_bems_mask(df: Optional[pd.DataFrame]) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=bool)

    mask = pd.Series([False] * len(df), index=df.index)
    if "Transaction ID" in df.columns:
        mask |= df["Transaction ID"].fillna("").astype(str).str.contains(r"\bBEMS\b|BEMS[- ]?\d+", case=False, regex=True)
    if "bemscsc_refs" in df.columns:
        refs = df["bemscsc_refs"].fillna("").astype(str)
        mask |= refs.str.contains(r"\bBEMS\b|BEMS[- ]?\d+", case=False, regex=True)

    for col in ("BEMS_REF", "bems_ref", "Escalation_Ref", "Engineering_Ref", "BEMS", "bems"):
        if col in df.columns:
            mask |= df[col].fillna("").astype(str).str.contains(r"\bBEMS\b|BEMS[- ]?\d+", case=False, regex=True)

    text_cols: List[str] = []
    for col in df.columns:
        lowered = str(col).lower()
        if any(token in lowered for token in ("title", "problem", "description", "subject", "summary", "detail")):
            text_cols.append(col)
    if text_cols:
        text_blob = pd.Series([""] * len(df), index=df.index)
        for col in text_cols:
            text_blob += " " + df[col].fillna("").astype(str)
        mask |= text_blob.str.contains(
            r"\bBEMS\b|BEMS[- ]?\d+|backend\s+escalation|engineering\s+escalation",
            case=False,
            regex=True,
        )

    return mask.fillna(False)


def extract_bems_ids_from_text(text: Any) -> List[str]:
    """Extract canonical BEMS references from free text."""
    candidate = _clean_text(text)
    if not candidate:
        return []
    found = BEMS_ID_PATTERN.findall(candidate)
    normalized = []
    for token in found:
        t = _clean_text(token).upper()
        if t:
            normalized.append(t)
    if any(re.search(r"\d", token) for token in normalized):
        normalized = [token for token in normalized if re.search(r"\d", token)]
    return sorted(set(normalized))


def extract_bems_ids_from_row(
    row: pd.Series,
    columns: Sequence[str] = ("Transaction ID", "bemscsc_refs", "Title", "Problem Description", "SUBJECT", "SUBJECT_C"),
) -> List[str]:
    """Extract canonical BEMS references from selected row columns."""
    refs: List[str] = []
    for col in columns:
        if col in row.index:
            refs.extend(extract_bems_ids_from_text(row.get(col)))
    return sorted(set(refs))


def _classify_case_type_text(text: str) -> str:
    if not text:
        return "unknown"
    provisioning_hits = sum(1 for pat in PROVISIONING_PATTERNS if re.search(pat, text, flags=re.IGNORECASE))
    break_fix_hits = sum(1 for pat in BREAK_FIX_PATTERNS if re.search(pat, text, flags=re.IGNORECASE))
    if provisioning_hits > break_fix_hits and provisioning_hits > 0:
        return "provisioning_request"
    if break_fix_hits > provisioning_hits and break_fix_hits > 0:
        return "break_fix_technical"
    return "unknown"


def classify_case_type(row: pd.Series) -> str:
    for col in LIKELY_CASE_TYPE_COLS:
        if col in row.index:
            ctype = _classify_case_type_text(_clean_text(row.get(col)))
            if ctype != "unknown":
                return ctype

    text_parts: List[str] = []
    for col in ("Title", "title", "Problem Description", "DESCRIPTION_C", "SUBJECT", "SUBJECT_C", "Case Status", "Status"):
        if col in row.index:
            text_parts.append(_clean_text(row.get(col)))
    return _classify_case_type_text(" ".join(text_parts))


def add_case_lifecycle_fields(
    df: Optional[pd.DataFrame],
    customer_lookup: Optional[Dict[str, Dict[str, str]]] = None,
) -> pd.DataFrame:
    """Add normalized TAC lifecycle, severity, and case-type fields."""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df

    now = datetime.utcnow()
    use = df.copy()
    lookup = customer_lookup or {"account_to_customer": {}, "key_to_customer": {}}

    use["customer_name"] = use.apply(lambda row: resolve_customer_name(row, lookup), axis=1)
    use["customer_name_norm"] = use["customer_name"].apply(normalize_customer_name)

    status_col = first_existing_column(use.columns, LIKELY_STATUS_COLS)
    priority_col = first_existing_column(use.columns, LIKELY_PRIORITY_COLS)
    open_col = first_existing_column(use.columns, LIKELY_OPEN_DATE_COLS)
    close_col = first_existing_column(use.columns, LIKELY_CLOSED_DATE_COLS)

    if status_col:
        use["case_status_norm"] = use[status_col].apply(normalize_status_label)
    else:
        use["case_status_norm"] = "Unknown"
    if priority_col:
        use["case_priority_norm"] = use[priority_col].apply(normalize_priority_label)
        use["severity_norm"] = use[priority_col].apply(normalize_severity_label)
    else:
        use["case_priority_norm"] = "Unknown"
        use["severity_norm"] = "Unknown"

    if open_col:
        use["open_date"] = parse_datetime_series(use[open_col])
    else:
        use["open_date"] = pd.NaT
    if close_col:
        use["closed_date"] = parse_datetime_series(use[close_col])
    else:
        use["closed_date"] = pd.NaT

    use["is_open"] = use["case_status_norm"].eq("Open")
    use["is_closed"] = use["case_status_norm"].eq("Closed")
    # If status is unknown but close date exists, treat as closed.
    use.loc[use["closed_date"].notna() & use["case_status_norm"].eq("Unknown"), "is_closed"] = True
    use.loc[use["closed_date"].notna() & use["case_status_norm"].eq("Unknown"), "is_open"] = False

    use["open_age_days"] = (
        (pd.Timestamp(now) - use["open_date"]).dt.days.where(use["open_date"].notna() & use["is_open"], other=pd.NA)
    )
    use["closed_age_days"] = (
        (pd.Timestamp(now) - use["closed_date"]).dt.days.where(use["closed_date"].notna() & use["is_closed"], other=pd.NA)
    )

    use["case_type_class"] = use.apply(classify_case_type, axis=1)
    bems_mask = detect_bems_mask(use)
    use["is_bems"] = bems_mask.astype(bool)

    return use

