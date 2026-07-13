"""Round 4 / Phase 6.3 regression test.

``_records_from_dataframe`` must sort the source DataFrame
deterministically (by timestamp, then ID) BEFORE applying
``head(max_rows)``.  Pre-Round-4 the function sliced whatever order
Snowflake returned, so the same data could surface different
evidence subsets across runs.
"""
from __future__ import annotations

import pandas as pd

import ask_ai_grounded as aag


def test_records_are_sorted_by_timestamp_desc_then_id() -> None:
    df = pd.DataFrame([
        {"ID": "AB-001", "title": "x", "OPEN_DATE_C": "2026-01-01T00:00:00Z", "customer_name": "C1"},
        {"ID": "AB-002", "title": "y", "OPEN_DATE_C": "2026-04-01T00:00:00Z", "customer_name": "C2"},
        {"ID": "AB-003", "title": "z", "OPEN_DATE_C": "2026-02-15T00:00:00Z", "customer_name": "C3"},
    ])
    records, _ids = aag._records_from_dataframe(
        df,
        source_type="ab",
        id_columns=["ID"],
        text_columns=["title"],
        customer_columns=["customer_name"],
        timestamp_columns=["OPEN_DATE_C"],
        max_rows=2,
    )
    assert len(records) == 2, "head(max_rows) must cap at 2"
    # Most recent first.
    assert records[0].source_id.upper().endswith("AB-002"), (
        "Round 4 Phase 6.3: most recent record (AB-002, 2026-04-01) "
        f"must be first; got {records[0].source_id}."
    )
    assert records[1].source_id.upper().endswith("AB-003"), (
        "Round 4 Phase 6.3: second-most-recent (AB-003, 2026-02-15) "
        f"must be second; got {records[1].source_id}."
    )


def test_sort_is_deterministic_across_calls() -> None:
    """Same input must yield identical record orderings on repeat calls."""
    df = pd.DataFrame([
        {"ID": f"AB-{i:03d}", "title": f"t{i}", "OPEN_DATE_C": f"2026-04-{(i % 28) + 1:02d}T00:00:00Z", "customer_name": f"C{i}"}
        for i in range(10)
    ])
    a, _ = aag._records_from_dataframe(
        df, "ab", ["ID"], ["title"], ["customer_name"], ["OPEN_DATE_C"], max_rows=5
    )
    b, _ = aag._records_from_dataframe(
        df, "ab", ["ID"], ["title"], ["customer_name"], ["OPEN_DATE_C"], max_rows=5
    )
    assert [r.source_id for r in a] == [r.source_id for r in b], (
        "Round 4 Phase 6.3: repeated calls on the same DataFrame must "
        "produce the same record ordering."
    )
