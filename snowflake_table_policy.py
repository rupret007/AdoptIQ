"""
Hardcoded Snowflake table policy for AdoptIQ.

This module enforces a strict policy:
- Block explicitly disallowed tables.
- Permit only explicitly allowed tables when a table is referenced in SQL.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Set


class TablePolicyViolation(ValueError):
    """Raised when a SQL table reference violates policy."""


_ALLOWED_CANONICAL: Set[str] = {
    "CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA",
    "CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY",
    "CX_DB.CX_SWSSBST_BR.ACCOUNTS_EXPIRED_LAST_MONTH",
    "CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU",
    "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW",
    "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C",
}

_BLOCKED_CANONICAL: Set[str] = {
    "CX_DB.CX_SWSSBST_BR.SUPPORT_CASES",
    "CX_DB.CX_SWSSBST_BR.USER_DATA",
    "CX_DB.CX_SWSSBST_BR.PRODUCT_USAGE",
    "EDW_SALES_ETL_DB.SS.ESA_C360_SUCCESS_PRIORITY__C",
    "EDW_SALES_ETL_DB.SS.ESA_C360_CS_TASK__C",
}

_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+([A-Za-z0-9_.$\"]+)",
    re.IGNORECASE,
)


def normalize_table_name(table_name: str) -> str:
    cleaned = (table_name or "").strip().strip(",;")
    cleaned = cleaned.replace('"', "")
    return cleaned.upper()


def table_basename(table_name: str) -> str:
    normalized = normalize_table_name(table_name)
    return normalized.split(".")[-1] if normalized else normalized


def _with_basenames(canonical_names: Iterable[str]) -> Set[str]:
    names = set(canonical_names)
    for name in list(canonical_names):
        names.add(table_basename(name))
    return names


ALLOWED_TABLES: Set[str] = _with_basenames(_ALLOWED_CANONICAL)
BLOCKED_TABLES: Set[str] = _with_basenames(_BLOCKED_CANONICAL)


def is_table_blocked(table_name: str) -> bool:
    normalized = normalize_table_name(table_name)
    if not normalized:
        return False
    return normalized in BLOCKED_TABLES or table_basename(normalized) in BLOCKED_TABLES


def is_table_allowed(table_name: str) -> bool:
    normalized = normalize_table_name(table_name)
    if not normalized:
        return False
    return normalized in ALLOWED_TABLES or table_basename(normalized) in ALLOWED_TABLES


def guard_table(table_name: str) -> None:
    normalized = normalize_table_name(table_name)
    if not normalized:
        return
    if is_table_blocked(normalized):
        raise TablePolicyViolation(f"Snowflake table blocked by policy: {normalized}")
    if not is_table_allowed(normalized):
        raise TablePolicyViolation(
            f"Snowflake table not in allowlist policy: {normalized}"
        )


def extract_table_references(sql: str) -> List[str]:
    if not isinstance(sql, str):
        return []
    refs: List[str] = []
    for match in _TABLE_PATTERN.finditer(sql):
        candidate = normalize_table_name(match.group(1))
        if not candidate:
            continue
        # Skip subquery markers, if ever captured.
        if candidate.startswith("(") or candidate in {"SELECT"}:
            continue
        refs.append(candidate)
    # Preserve order, remove duplicates.
    return list(dict.fromkeys(refs))


def guard_sql(sql: str) -> List[str]:
    refs = extract_table_references(sql)
    for table in refs:
        guard_table(table)
    return refs

