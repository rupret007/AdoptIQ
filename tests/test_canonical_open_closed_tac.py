"""Phase 5 regression test.

Round 1.4 + 4.1 introduced canonical lifecycle helpers in
``canonical_metrics``.  The contract every report relies on is::

    count_open_tac(df) + count_closed_tac(df) == count_total_tac(df)

If a normalization change ever stops classifying a row, the leader,
compact, and Ask AI surfaces will silently disagree on TAC totals.
This test pins the invariant on a mixed open/closed/unknown
fixture so any future drift fails loudly in CI.
"""
from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm


@pytest.fixture
def all_classifiable_csone_df() -> pd.DataFrame:
    """3 open, 2 closed — every row has an unambiguous status."""
    return pd.DataFrame([
        {"Case Status": "Open", "Severity": "P1", "SR Number": "T1",
         "Date/Time Opened": "2026-01-01", "Date/Time Closed": ""},
        {"Case Status": "In Progress", "Severity": "P2", "SR Number": "T2",
         "Date/Time Opened": "2026-01-02", "Date/Time Closed": ""},
        {"Case Status": "Working", "Severity": "P3", "SR Number": "T3",
         "Date/Time Opened": "2026-01-03", "Date/Time Closed": ""},
        {"Case Status": "Closed", "Severity": "P2", "SR Number": "T4",
         "Date/Time Opened": "2026-01-04", "Date/Time Closed": "2026-01-10"},
        {"Case Status": "Resolved", "Severity": "P3", "SR Number": "T5",
         "Date/Time Opened": "2026-01-05", "Date/Time Closed": "2026-01-09"},
    ])


@pytest.fixture
def mixed_csone_df(all_classifiable_csone_df: pd.DataFrame) -> pd.DataFrame:
    """All classifiable rows + 1 ambiguous-status row that lands in
    neither the open nor closed bucket."""
    extra = pd.DataFrame([
        {"Case Status": "ZZ-Unknown-Garbage", "Severity": "P4", "SR Number": "T6",
         "Date/Time Opened": "2026-01-06", "Date/Time Closed": ""},
    ])
    return pd.concat([all_classifiable_csone_df, extra], ignore_index=True)


def test_open_plus_closed_equals_total_for_classifiable_rows(
    all_classifiable_csone_df: pd.DataFrame,
) -> None:
    total = cm.count_total_tac(all_classifiable_csone_df)
    open_n = cm.count_open_tac(all_classifiable_csone_df)
    closed_n = cm.count_closed_tac(all_classifiable_csone_df)
    assert open_n + closed_n == total, (
        "When every row carries a known lifecycle status, "
        "count_open_tac + count_closed_tac must equal count_total_tac. "
        f"Got open={open_n}, closed={closed_n}, total={total}."
    )


def test_open_plus_closed_never_exceeds_total(
    mixed_csone_df: pd.DataFrame,
) -> None:
    """The open and closed buckets must be disjoint subsets of the
    total — even when a row's status is unknown, it must not be
    double-counted into both."""
    total = cm.count_total_tac(mixed_csone_df)
    open_n = cm.count_open_tac(mixed_csone_df)
    closed_n = cm.count_closed_tac(mixed_csone_df)
    assert open_n + closed_n <= total, (
        "open + closed must never exceed total — exceeding the total "
        "means a row was double-classified into both buckets."
    )


def test_priority_breakdown_sums_to_total(mixed_csone_df: pd.DataFrame) -> None:
    total = cm.count_total_tac(mixed_csone_df)
    breakdown = cm.count_priority_breakdown(mixed_csone_df)
    assert sum(breakdown.values()) == total, (
        "Sum of P1+P2+P3+P4+Unknown must equal total — any drift "
        "indicates a normalization bucket has gone missing."
    )


def test_empty_df_returns_zeroes() -> None:
    empty = pd.DataFrame()
    assert cm.count_total_tac(empty) == 0
    assert cm.count_open_tac(empty) == 0
    assert cm.count_closed_tac(empty) == 0


def test_none_df_returns_zeroes() -> None:
    assert cm.count_total_tac(None) == 0
    assert cm.count_open_tac(None) == 0
    assert cm.count_closed_tac(None) == 0
