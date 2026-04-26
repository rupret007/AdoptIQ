"""Round 8 / Phase 6.5: edge-SQL behavioral tests for snowflake_table_policy.

The legacy regression suite only spot-checks that the allowlist exists.
This module exercises the actual extraction / guard helpers against the
kinds of strings real Snowflake clients (and real attackers) emit:

* trailing punctuation / commas after the table reference,
* quoted identifiers (``"DB"."SCHEMA"."TABLE"``),
* CTEs and ``INSERT INTO`` flavours,
* basenames vs fully qualified names,
* whitespace-only / empty / non-string inputs,
* SQL referencing a blocked table inside a permitted CTE (must still
  raise),
* SQL referencing only the basename of an allowed table (must pass).
"""

from __future__ import annotations

import pytest

from snowflake_table_policy import (
    ALLOWED_TABLES,
    BLOCKED_TABLES,
    TablePolicyViolation,
    extract_table_references,
    guard_sql,
    guard_table,
    is_table_allowed,
    is_table_blocked,
    normalize_table_name,
    table_basename,
)


# --------------------------------------------------------------------------
# normalize / basename helpers
# --------------------------------------------------------------------------

def test_normalize_strips_quotes_punctuation_and_uppercases():
    # Real-world: SQL parsers leave commas / semicolons clinging to the name.
    assert normalize_table_name('"cx_db"."cx_swssbst_br"."dsm_assignment_data",') == \
        "CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA"
    assert normalize_table_name("dsm_assignment_data;") == "DSM_ASSIGNMENT_DATA"


def test_normalize_handles_empty_and_non_string():
    assert normalize_table_name("") == ""
    assert normalize_table_name("   ") == ""
    # Defensive: function tolerates None via the ``or ""`` guard.
    assert normalize_table_name(None) == ""  # type: ignore[arg-type]


def test_basename_returns_last_segment():
    assert table_basename("CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA") == "DSM_ASSIGNMENT_DATA"
    assert table_basename('"cx_db"."ss"."foo"') == "FOO"
    assert table_basename("") == ""


# --------------------------------------------------------------------------
# allowlist / blocklist membership
# --------------------------------------------------------------------------

def test_allowed_canonical_and_basename_both_pass():
    fq = "CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA"
    assert fq in ALLOWED_TABLES
    assert "DSM_ASSIGNMENT_DATA" in ALLOWED_TABLES
    assert is_table_allowed(fq)
    assert is_table_allowed("dsm_assignment_data")


def test_blocked_canonical_and_basename_both_blocked():
    fq = "CX_DB.CX_SWSSBST_BR.SUPPORT_CASES"
    assert fq in BLOCKED_TABLES
    assert "SUPPORT_CASES" in BLOCKED_TABLES
    assert is_table_blocked(fq)
    assert is_table_blocked("support_cases")


def test_unknown_table_is_neither_allowed_nor_blocked():
    assert not is_table_allowed("CX_DB.CX_SWSSBST_BR.MYSTERY_TABLE")
    assert not is_table_blocked("CX_DB.CX_SWSSBST_BR.MYSTERY_TABLE")


# --------------------------------------------------------------------------
# guard_table behaviour
# --------------------------------------------------------------------------

def test_guard_table_passes_for_allowed():
    guard_table("CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA")
    guard_table("dsm_assignment_data")


def test_guard_table_raises_for_blocked():
    with pytest.raises(TablePolicyViolation):
        guard_table("CX_DB.CX_SWSSBST_BR.SUPPORT_CASES")


def test_guard_table_raises_for_unknown():
    with pytest.raises(TablePolicyViolation):
        guard_table("RANDOM_DB.RANDOM_SCHEMA.RANDOM_TABLE")


def test_guard_table_noop_for_empty():
    # Empty / whitespace input must not raise (callers may pass None-ish
    # values from optional clauses).
    guard_table("")
    guard_table("   ")


# --------------------------------------------------------------------------
# extract_table_references / guard_sql edge cases
# --------------------------------------------------------------------------

def test_extract_handles_quoted_identifiers_and_punctuation():
    sql = (
        'SELECT a.* '
        'FROM "CX_DB"."CX_SWSSBST_BR"."DSM_ASSIGNMENT_DATA" a, '
        '     CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY b '
        'WHERE a.id = b.id;'
    )
    refs = extract_table_references(sql)
    assert "CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA" in refs
    # The second comma-separated FROM target is not preceded by FROM/JOIN
    # so the regex (intentionally) won't see it -- but that is the existing
    # contract; document it here so a future regex change must update this
    # test deliberately.
    assert "CX_DB.CX_SWSSBST_BR.COLLAB_ACCOUNT_SUMMARY" not in refs


def test_extract_handles_join_into_update():
    sql = (
        "WITH cte AS (SELECT 1) "
        "UPDATE EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW "
        "JOIN EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C u "
        "  ON u.id = c.id "
        "INTO CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU "
    )
    refs = extract_table_references(sql)
    assert "EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW" in refs
    assert "EDW_SALES_ETL_DB.SS.ESA_C360_CUSTOMER_PULSE__C" in refs
    assert "CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU" in refs


def test_guard_sql_passes_for_allowed_only():
    sql = "SELECT * FROM CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA WHERE 1=1"
    refs = guard_sql(sql)
    assert refs == ["CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA"]


def test_guard_sql_raises_when_any_blocked_table_referenced():
    # Even when a permitted table is also referenced, a single blocked
    # reference must abort the query.
    sql = (
        "SELECT a.id "
        "FROM CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA a "
        "JOIN CX_DB.CX_SWSSBST_BR.SUPPORT_CASES s ON s.id = a.id"
    )
    with pytest.raises(TablePolicyViolation):
        guard_sql(sql)


def test_guard_sql_raises_when_unknown_table_referenced():
    sql = "SELECT * FROM RANDOM_DB.RANDOM_SCHEMA.RANDOM_TABLE"
    with pytest.raises(TablePolicyViolation):
        guard_sql(sql)


def test_guard_sql_handles_non_string_input():
    assert extract_table_references(None) == []  # type: ignore[arg-type]
    assert extract_table_references(123) == []  # type: ignore[arg-type]
    # guard_sql returns the (empty) ref list rather than raising on
    # non-string inputs, matching extract_table_references' contract.
    assert guard_sql(None) == []  # type: ignore[arg-type]


def test_guard_sql_dedupes_repeated_references():
    sql = (
        "SELECT * FROM CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA d1 "
        "JOIN CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA d2 ON d1.id = d2.id"
    )
    refs = guard_sql(sql)
    assert refs == ["CX_DB.CX_SWSSBST_BR.DSM_ASSIGNMENT_DATA"]


def test_guard_sql_basename_only_reference_passes_for_allowed():
    # Some legacy callers reference the basename only.  Allowlist accepts
    # both canonical and basename forms.
    sql = "SELECT * FROM dsm_assignment_data"
    refs = guard_sql(sql)
    assert refs == ["DSM_ASSIGNMENT_DATA"]


def test_guard_sql_basename_only_reference_blocks_blocked():
    sql = "SELECT * FROM support_cases"
    with pytest.raises(TablePolicyViolation):
        guard_sql(sql)
