"""Round 9 / Phase 6.1: snowflake_table_policy.extract_table_references handles comma joins.

Marker + behavioral tests for comma-separated tables, CTEs and quoted
identifiers.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_marker_snowflake_policy_comma_join() -> None:
    src = REPO_ROOT.joinpath('snowflake_table_policy.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 6.1' in src, (
        'Round 9 / Phase 6.1 marker missing in snowflake_table_policy.py'
    )
    assert_in_source(src, '_FROM_TAIL_PATTERN', label='src')
    assert_in_source(src, '_SQL_CLAUSE_KEYWORDS', label='src')


def test_extract_comma_separated_tables() -> None:
    from snowflake_table_policy import extract_table_references

    sql = "SELECT * FROM allowed.t1, blocked.t2 WHERE allowed.t1.id = blocked.t2.id"
    refs = extract_table_references(sql)
    assert 'ALLOWED.T1' in refs
    assert 'BLOCKED.T2' in refs


def test_extract_three_way_comma_from() -> None:
    """Three-way comma-separated FROM clause -- all tables must be captured."""
    from snowflake_table_policy import extract_table_references

    sql = "SELECT * FROM a.b, c.d, e.f WHERE a.b.id = c.d.id"
    refs = extract_table_references(sql)
    assert 'A.B' in refs
    assert 'C.D' in refs
    assert 'E.F' in refs


def test_extract_join_with_explicit_join_keyword() -> None:
    """Explicit JOIN ... ON forms still capture both tables (legacy path)."""
    from snowflake_table_policy import extract_table_references

    sql = "SELECT * FROM a.b JOIN c.d ON a.b.id = c.d.id"
    refs = extract_table_references(sql)
    assert 'A.B' in refs
    assert 'C.D' in refs


def test_extract_filters_sql_keywords() -> None:
    from snowflake_table_policy import extract_table_references

    sql = "SELECT * FROM allowed.t1 WHERE allowed.t1.id > 0"
    refs = extract_table_references(sql)
    assert 'WHERE' not in refs
    assert 'SELECT' not in refs


def test_extract_handles_no_tables() -> None:
    from snowflake_table_policy import extract_table_references

    refs = extract_table_references("SELECT 1")
    assert refs == []


def test_extract_dedupes_repeats() -> None:
    from snowflake_table_policy import extract_table_references

    sql = "SELECT * FROM a.b, a.b, c.d"
    refs = extract_table_references(sql)
    # Table refs returned in first-seen order, no duplicates.
    assert refs.count('A.B') == 1
    assert 'C.D' in refs
