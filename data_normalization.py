"""Shared normalization helpers for AdoptIQ report consistency."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Canonical status buckets
# Round 4 / Phase 3.4: extend with the in-progress / waiting phrases
# that appear in real CSOne exports.  Without these patterns the
# corresponding rows fall through to ``Unknown`` and
# ``cm.count_open_tac`` undercounts the open backlog (open TAC and
# aging look healthier than reality).
OPEN_STATUS_PATTERNS = (
    r"\bopen\b",
    r"\bnew\b",
    r"\bin\s*progress\b",
    r"\bworking\b",
    r"\breopened?\b",
    r"\bpending\b",
    r"\bwaiting\s+on\s+customer\b",
    r"\bawaiting\s+customer\b",
    r"\bwaiting\s+on\s+(engineering|support|3rd|third)[^\b]*\b",
    r"\bawaiting\s+(engineering|support|3rd|third|response|info|information)\b",
    r"\bcustomer\s+(action|response|update|input)\b",
    r"\binvestigat(?:e|ing|ion)\b",
    r"\bmonitor(?:ing)?\b",
    r"\bidentified\b",
    r"\bassigned\b",
    r"\bactive\b",
    r"\bon\s*hold\b",
    r"\bdeferred\b",
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
    text = _clean_text(name)
    if not text:
        return ""
    # Round 13 / Phase 3.15: collapse compatibility variants (NFKC) and
    # case-fold (not just lowercase) before further canonicalization.
    # Without this, the join key for "ACME co." and "AcmE\u00A0co" --
    # which include a non-breaking space and a German "ß" -- mismatched
    # the corresponding "Acme Co" rows in another source, producing
    # double-counted customers in cross-source merges and BEMS lookups.
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass
    lowered = text.casefold()
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
    # Round 13 / Phase 3.15: collapse compatibility variants (NFKC) so
    # display strings like "Acme\u00A0Co" (non-breaking space) and
    # "Acme Co" present identically in the report and in groupby /
    # nunique cardinality counts.  Note: we deliberately do NOT lower
    # or case-fold here; that's reserved for the join key
    # (``_clean_name_for_key``) so display preserves the original
    # capitalization the customer recognizes.
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_for_display(value: Any) -> str:
    """Round 39 / Phase 4.4 -- prepare a customer or account name for
    rendering in customer-facing reports.

    The Snowflake ``BU_ACCOUNT_NAME`` field sometimes carries
    multi-segment account labels separated with ``__`` (a CSOne /
    Salesforce convention used to encode parent/child or jurisdiction
    boundaries).  When those raw strings flowed into the leader and
    executive reports they printed as e.g.
    ``"TRIBUNAL...__GOBIERNO...__MX"`` -- a reader couldn't tell
    whether the underscores were significant or noise.

    This helper:

    1. Runs the value through :func:`normalize_customer_name` so it
       inherits the same NFKC + whitespace collapse policy.
    2. Replaces any run of ``_`` characters of length >= 2 with
       ``", "`` so the segments read as a list a human can parse.
    3. Collapses any leading / trailing comma noise that might appear
       if the upstream string started or ended with the separator.

    Single underscores (e.g. ``"Acme_Subsidiary"``) are preserved on
    purpose -- those occur in real customer names and the Round 39
    audit only flagged the ``__`` (double-underscore) case.
    """
    text = normalize_customer_name(value)
    if not text or text == "Unknown":
        return text
    text = re.sub(r"_{2,}", ", ", text)
    text = re.sub(r"\s*,\s*,\s*", ", ", text)
    text = text.strip().strip(",").strip()
    return text or "Unknown"


def build_customer_lookup(team_subs_df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """Build account-id and fuzzy customer-name lookups from subscription data.

    Round 2 / Phase 4.1: detect collisions deterministically.

    - ``account_to_customer`` was previously last-write-wins.  Two
      different ``BU_NAME`` values for the same ``ACCOUNT_ID_C`` would
      silently overwrite the prior mapping, so the same account would
      route to different customers depending on row order.
    - ``key_to_customer`` was first-write-wins (opposite policy), so
      the same dataset could yield inconsistent canonical names.

    The rebuilt logic:

    1. Tally every ``account_id -> customer`` observed (with row order
       preserved as a stable tiebreaker).
    2. For accounts with multiple distinct customer names, deterministic
       rule: pick the alphabetically first ``BU_NAME`` (stable across
       runs, independent of row order).
    3. Log every collision with the chosen winner and losers and surface
       them via the returned ``warnings`` / ``collisions`` lists so
       callers can route them to ``partial_data_warnings``.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)

    account_to_customer: Dict[str, str] = {}
    key_to_customer: Dict[str, str] = {}
    collisions: List[Dict[str, Any]] = []
    warnings: List[str] = []
    empty_result = {
        "account_to_customer": account_to_customer,
        "key_to_customer": key_to_customer,
        "collisions": collisions,
        "warnings": warnings,
    }
    if team_subs_df is None or team_subs_df.empty:
        return empty_result

    safe = team_subs_df.copy()
    if "BU_NAME" not in safe.columns:
        safe["BU_NAME"] = ""
    safe["BU_NAME"] = safe["BU_NAME"].apply(normalize_customer_name)

    account_observations: Dict[str, List[str]] = {}
    key_observations: Dict[str, List[str]] = {}

    for _, row in safe.iterrows():
        customer = normalize_customer_name(row.get("BU_NAME"))
        if customer == "Unknown":
            continue
        key = _clean_name_for_key(customer)
        if key:
            key_observations.setdefault(key, []).append(customer)
        for account_col in LIKELY_ACCOUNT_ID_COLS:
            if account_col in safe.columns:
                account_id = _clean_text(row.get(account_col))
                if account_id:
                    account_observations.setdefault(account_id, []).append(customer)

    for account_id, names in account_observations.items():
        distinct = sorted(set(names))
        winner = distinct[0]
        account_to_customer[account_id] = winner
        if len(distinct) > 1:
            collisions.append({
                "kind": "account_to_customer",
                "key": account_id,
                "chosen": winner,
                "alternatives": distinct[1:],
            })
            warning_msg = (
                f"account_to_customer collision for ACCOUNT_ID={account_id!r}: "
                f"chose {winner!r} (alphabetical) over {distinct[1:]!r}"
            )
            warnings.append(warning_msg)
            _logger.warning(warning_msg)

    for key, names in key_observations.items():
        distinct = sorted(set(names))
        winner = distinct[0]
        key_to_customer[key] = winner
        if len(distinct) > 1:
            collisions.append({
                "kind": "key_to_customer",
                "key": key,
                "chosen": winner,
                "alternatives": distinct[1:],
            })
            warning_msg = (
                f"key_to_customer collision for normalized_key={key!r}: "
                f"chose {winner!r} (alphabetical) over {distinct[1:]!r}"
            )
            warnings.append(warning_msg)
            _logger.warning(warning_msg)

    return {
        "account_to_customer": account_to_customer,
        "key_to_customer": key_to_customer,
        "collisions": collisions,
        "warnings": warnings,
    }


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
    """Round 9 / Phase 6.2: predictable, all-NaT fallback on hard parse failure.

    The previous form's nested ``except Exception: pass`` swallowed the
    failure silently and returned ``parsed`` in whatever ambiguous,
    half-converted state pandas had landed it in (sometimes
    tz-aware, sometimes tz-naive, sometimes with a mix of dtypes
    after a partial coercion).  Downstream barrier-aging and ARR
    window code then compared tz-aware to tz-naive Timestamps and
    raised an opaque ``TypeError`` *inside the report writer* --
    losing the rest of the report section.

    The new contract is:

    * On the happy path the return value is always tz-naive (UTC
      anchored) so downstream comparisons against ``pd.Timestamp.now('UTC')
      .tz_localize(None)`` (Round 8 / Phase 2.10) are well-defined.
    * On total parse failure we return an all-``NaT`` Series of the
      same length / index and stamp ``parsed.attrs['partial_data_warning']``
      so the caller (``arr_df`` / ``incidents_df`` builders, already
      wired to surface ``attrs`` via Round 7) can flag the section as
      partial in the report rather than silently degrade.
    """
    def _all_nat(reason: str) -> pd.Series:
        try:
            empty = pd.Series([pd.NaT] * len(series), index=getattr(series, 'index', None), dtype="datetime64[ns]")
        except Exception:
            empty = pd.Series([pd.NaT], dtype="datetime64[ns]")
        try:
            empty.attrs['partial_data_warning'] = reason
        except Exception:
            pass
        return empty

    try:
        parsed = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
    except TypeError:
        try:
            parsed = pd.to_datetime(series, errors="coerce", utc=True)
        except Exception as fallback_err:
            return _all_nat(f"parse_datetime_series: pd.to_datetime failed ({fallback_err.__class__.__name__})")
    except Exception as parse_err:
        return _all_nat(f"parse_datetime_series: pd.to_datetime failed ({parse_err.__class__.__name__})")
    # Round 9 / Phase 6.2: convert to tz-naive UTC for downstream
    # arithmetic.  ``tz_convert`` fails on already-naive series;
    # ``tz_localize(None)`` fails on already-tz-aware series.  Try
    # both and only fall back to ``_all_nat`` when neither path
    # leaves us with a usable datetime64 dtype.
    try:
        parsed = parsed.dt.tz_convert(None)
    except Exception:
        try:
            parsed = parsed.dt.tz_localize(None)
        except Exception:
            try:
                if not pd.api.types.is_datetime64_any_dtype(parsed):
                    return _all_nat("parse_datetime_series: tz normalisation failed and result is non-datetime")
            except Exception:
                return _all_nat("parse_datetime_series: tz normalisation failed unexpectedly")
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

    # Round 6 / Phase 4.15: capture a single tz-aware UTC clock so
    # the open/closed age computations below cannot drift by the
    # host's local UTC offset and so both ages share the exact same
    # reference instant (no clock skew between the two subtractions).
    now = pd.Timestamp(datetime.now(timezone.utc))
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

    # Round 10 / Phase 9.1: ``parse_datetime_series`` stamps a
    # ``partial_data_warning`` on the returned ``Series.attrs`` when
    # parsing falls back to all-NaT.  However ``Series.attrs`` does
    # NOT propagate through ``df[col] = series`` assignment, so any
    # downstream caller that reads ``df['open_date'].attrs`` will get
    # an empty dict and silently miss the warning.  Capture the warn
    # texts off the source ``Series.attrs`` *before* assignment and
    # surface them on the returned DataFrame's ``attrs['partial_data_warnings']``
    # list so report assembly can append them to the operator-facing
    # ``partial_data_warnings`` shown on the progress page.
    _lifecycle_warnings = []
    if open_col:
        _open_series = parse_datetime_series(use[open_col])
        try:
            _w = _open_series.attrs.get('partial_data_warning')
            if _w:
                _lifecycle_warnings.append({
                    'source': 'add_case_lifecycle_fields.open_date',
                    'column': str(open_col),
                    'reason': str(_w),
                })
        except Exception:
            pass
        use["open_date"] = _open_series
    else:
        use["open_date"] = pd.NaT
    if close_col:
        _close_series = parse_datetime_series(use[close_col])
        try:
            _w = _close_series.attrs.get('partial_data_warning')
            if _w:
                _lifecycle_warnings.append({
                    'source': 'add_case_lifecycle_fields.closed_date',
                    'column': str(close_col),
                    'reason': str(_w),
                })
        except Exception:
            pass
        use["closed_date"] = _close_series
    else:
        use["closed_date"] = pd.NaT
    if _lifecycle_warnings:
        try:
            existing = list(use.attrs.get('partial_data_warnings') or [])
            existing.extend(_lifecycle_warnings)
            use.attrs['partial_data_warnings'] = existing
        except Exception:
            pass

    use["is_open"] = use["case_status_norm"].eq("Open")
    use["is_closed"] = use["case_status_norm"].eq("Closed")
    # If status is unknown but close date exists, treat as closed.
    use.loc[use["closed_date"].notna() & use["case_status_norm"].eq("Unknown"), "is_closed"] = True
    use.loc[use["closed_date"].notna() & use["case_status_norm"].eq("Unknown"), "is_open"] = False

    # Round 6 / Phase 4.15: align both date columns onto the same
    # tz-aware UTC basis as ``now`` before subtraction so pandas
    # does not raise / coerce when one side is tz-naive and the
    # other tz-aware.
    def _to_utc(s: pd.Series) -> pd.Series:
        try:
            if getattr(s.dt, 'tz', None) is None:
                return s.dt.tz_localize('UTC')
            return s.dt.tz_convert('UTC')
        except Exception:
            return s

    _open_utc = _to_utc(use["open_date"])
    _closed_utc = _to_utc(use["closed_date"])
    use["open_age_days"] = (
        (now - _open_utc).dt.days.where(_open_utc.notna() & use["is_open"], other=pd.NA)
    )
    use["closed_age_days"] = (
        (now - _closed_utc).dt.days.where(_closed_utc.notna() & use["is_closed"], other=pd.NA)
    )

    use["case_type_class"] = use.apply(classify_case_type, axis=1)
    bems_mask = detect_bems_mask(use)
    use["is_bems"] = bems_mask.astype(bool)
    use["case_classification"] = use["is_bems"].map(
        lambda flagged: "bems_escalation" if bool(flagged) else "tac_case"
    )

    return use


# ---------------------------------------------------------------------------
# Round 25 / Phase F: HTML strip helper for Excel object columns
# ---------------------------------------------------------------------------
#
# Pre-Round 25 the Excel writer emitted whatever string lived in object
# columns -- including raw HTML markup leaking out of Snowflake views
# that store rich-text descriptions.  The reference Brian Frazier
# report's ``AB_Detail_All`` and ``CSConsole_Customer_Pulse`` sheets had
# cells like::
#
#     <a href="/" target="_blank"> </a>
#     <img src="/lightning/r/Account/0014..." />
#     <p>Customer asked about <strong>renewal</strong> options.</p>
#
# Excel renders ``<`` literally, so the recipient saw raw markup
# instead of the intended text content.  This helper strips well-formed
# HTML tags and unescapes named/numeric entities, while leaving plain
# text untouched.  Cells that don't contain ``<`` skip the regex pass
# entirely so this is cheap on clean exports.

import html as _html_module

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_from_string(value: Any) -> Any:
    """Strip HTML tags and unescape entities from a single cell value.

    Non-string values are returned unchanged.  Strings that don't
    contain ``<`` are returned unchanged (so we don't rewrite their
    storage in the underlying frame).  Strings with ``<`` are passed
    through ``re.sub`` to drop tags and ``html.unescape`` to convert
    ``&amp;`` -> ``&``, ``&nbsp;`` -> non-breaking space, etc.

    Round 25 / Phase F.2: applied to Excel object columns immediately
    before ``df.to_excel(...)`` so generated workbooks no longer leak
    Snowflake rich-text markup into ``AB_Detail_All`` and
    ``CSConsole_Customer_Pulse``.
    """

    if not isinstance(value, str):
        return value
    if "<" not in value:
        return value
    try:
        stripped = _HTML_TAG_RE.sub("", value)
        return _html_module.unescape(stripped)
    except Exception:
        # Defensive: a regex / unescape failure should not lose data.
        return value


def strip_html_from_dataframe(df: "pd.DataFrame") -> "pd.DataFrame":
    """Return a shallow copy of ``df`` with HTML stripped from object columns.

    Only object-dtype columns whose values look like they might contain
    HTML are rewritten -- numeric / datetime columns and pure-text
    columns without any ``<`` characters are left alone.  We make a
    shallow copy so callers can pass us the DataFrame they were about
    to write to Excel without worrying about us mutating their working
    state.

    Returns ``df`` unchanged when:
    - ``df`` is None,
    - ``df`` is not a DataFrame, or
    - ``df`` is empty.

    Round 25 / Phase F.2.
    """

    if df is None:
        return df
    if not hasattr(df, "columns") or not hasattr(df, "copy"):
        return df
    try:
        if len(df) == 0:
            return df
    except Exception:
        return df

    try:
        out = df.copy()
    except Exception:
        return df

    try:
        obj_cols = list(out.select_dtypes(include=["object"]).columns)
    except Exception:
        obj_cols = []
    for col in obj_cols:
        try:
            series = out[col]
        except Exception:
            continue
        # Cheap fast-path: skip the column entirely if no cell has a
        # ``<`` character -- avoids the regex apply on big text columns.
        try:
            has_tag = series.astype(str).str.contains("<", regex=False, na=False).any()
        except Exception:
            has_tag = True
        if not has_tag:
            continue
        try:
            out[col] = series.map(strip_html_from_string)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Round 52 / partial-data-warning fix #3: dtype-safe customer merge for the
# CSConsole adoption-barriers re-annotation path.
# ---------------------------------------------------------------------------
#
# ``app_simple.py`` runs a left-merge between the CSConsole adoption-barriers
# frame (raw ``ACCOUNT_ID_C`` from ``C360_CS_TASK_C_VW``) and ``team_subs_df``
# (``ACCOUNT_ID_C`` + ``BU_NAME``) so the contract's ``customer`` slot can
# resolve.  Round 50 wired that merge into both compact and comprehensive
# paths but the recurring "schema_drift: missing slot(s) customer" warning
# kept appearing in production runs because the join key dtypes drift between
# Snowflake fetches: one frame would carry ``ACCOUNT_ID_C`` as ``object`` and
# the other as ``int64`` / ``Int64`` / ``float64`` (NULLs in DSM).  Pandas
# ``merge(on=...)`` returns no rows in that case (or all-NaN BU_NAME),
# leaving the ``customer`` slot unresolved and the prefetch-time
# ``fetch_error`` stamp untouched.
#
# ``merge_customer_join_keys_dtype_safe`` normalises both join sides to a
# common ``string`` form (NaN-safe, whitespace-trimmed, ``<NA>``-aware) BEFORE
# the merge so the join always succeeds when the underlying account IDs match
# regardless of upstream dtype drift.  When either input is empty / missing
# the helper short-circuits and returns the left frame unchanged.


def merge_customer_join_keys_dtype_safe(
    left: Optional[pd.DataFrame],
    right: Optional[pd.DataFrame],
    *,
    join_key: str = "ACCOUNT_ID_C",
    right_columns: Optional[Sequence[str]] = None,
    how: str = "left",
) -> Optional[pd.DataFrame]:
    """Left-merge ``left`` onto ``right`` after coercing the join key to a
    consistent string dtype on both sides.

    Returns ``left`` unchanged when either input is empty / missing the
    join key. Never raises; on any unexpected error returns ``left`` and
    leaves the caller to surface the failure via the existing
    ``fetch_error`` channel.
    """
    if left is None or not isinstance(left, pd.DataFrame) or left.empty:
        return left
    if right is None or not isinstance(right, pd.DataFrame) or right.empty:
        return left
    if join_key not in left.columns or join_key not in right.columns:
        return left

    try:
        right_subset_cols = list(right_columns) if right_columns else list(right.columns)
        if join_key not in right_subset_cols:
            right_subset_cols = [join_key] + [c for c in right_subset_cols if c != join_key]
        right_subset = right[[c for c in right_subset_cols if c in right.columns]].copy()

        left_copy = left.copy()
        left_copy[join_key] = (
            left_copy[join_key]
            .astype("string")
            .str.strip()
            .replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA, "": pd.NA})
        )
        right_subset[join_key] = (
            right_subset[join_key]
            .astype("string")
            .str.strip()
            .replace({"<NA>": pd.NA, "None": pd.NA, "nan": pd.NA, "": pd.NA})
        )
        right_subset = right_subset.drop_duplicates(subset=[join_key])

        return left_copy.merge(right_subset, on=join_key, how=how)
    except Exception:
        return left
