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

# Round 9 / Phase 6.1: capture the trailing "FROM/JOIN <t1>, <t2>, <t3>"
# tail so comma-separated joins are walked too.  The original pattern
# only grabbed the first identifier after ``FROM`` / ``JOIN``, so a
# query of the form ``FROM allowed.t1, blocked.t2`` got past
# ``guard_sql`` because ``blocked.t2`` was never inspected.  This
# secondary pattern grabs the rest of the comma list (everything up to
# the next SQL keyword that terminates the FROM clause) and we split it
# into individual identifiers below.
_FROM_TAIL_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+[A-Za-z0-9_.$\"]+\s*((?:,\s*[A-Za-z0-9_.$\"]+\s*)+)",
    re.IGNORECASE,
)

# Round 9 / Phase 6.1: SQL keywords that legitimately follow a FROM /
# JOIN list.  Any identifier captured by ``_FROM_TAIL_PATTERN`` whose
# normalised form matches one of these is dropped -- defensive against
# the regex over-greedily reaching into the next clause.
_SQL_CLAUSE_KEYWORDS = {
    "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "JOIN", "ON",
    "UNION", "INTERSECT", "EXCEPT", "QUALIFY", "WINDOW",
    "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "LATERAL",
    "AS", "USING", "WITH", "SELECT",
}


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
    """Round 9 / Phase 6.1: extract every table referenced by the SQL,
    including comma-joined siblings of a ``FROM`` / ``JOIN`` clause.

    The original implementation only captured the first identifier
    after ``FROM`` / ``JOIN``, which let a query of the form
    ``FROM allowed.t1, blocked.t2`` slip past ``guard_sql`` because
    ``blocked.t2`` was never offered to ``guard_table``.  We now walk
    the comma list with ``_FROM_TAIL_PATTERN``, drop SQL keywords that
    the over-greedy regex might pick up, and dedupe on the way out.
    Subqueries (``FROM (SELECT ...)``) are still skipped via the
    original ``startswith("(")`` guard.
    """
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
    # Round 9 / Phase 6.1: sweep comma-separated tail identifiers too.
    for tail_match in _FROM_TAIL_PATTERN.finditer(sql):
        tail_text = tail_match.group(1) or ""
        for piece in tail_text.split(","):
            candidate = normalize_table_name(piece)
            if not candidate:
                continue
            if candidate.startswith("(") or candidate in _SQL_CLAUSE_KEYWORDS:
                continue
            refs.append(candidate)
    # Preserve order, remove duplicates.
    return list(dict.fromkeys(refs))


def guard_sql(sql: str) -> List[str]:
    refs = extract_table_references(sql)
    for table in refs:
        guard_table(table)
    return refs

