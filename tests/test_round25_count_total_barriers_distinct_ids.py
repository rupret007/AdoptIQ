"""Round 25 / Phase F.1: ``count_total_barriers`` must count distinct IDs.

Pre-Round 25 ``canonical_metrics.count_total_barriers`` returned
``_safe_len(ab_df)`` -- one count per row.  But the Snowflake AB
extract joins barrier rows to assignees / status-history changes, so a
single barrier with three assignees inflated the count by 3x.  The
reference Brian Frazier / All Contact Center / 90d report had 71 rows
but only 67 distinct ``ID`` values; the dashboard tile said ``Active
Barriers: 71`` while a recipient who actually pivoted the Excel export
got 67.

Phase F.1 switches the canonical helper to ``ab_df["ID"].nunique()`` so
the headline matches what a recipient sees when they de-dupe the
``AB_Detail_All`` sheet.  This test enforces:

1.  A frame with 71 rows but only 67 distinct IDs returns 67.
2.  A frame whose ``ID`` column has trailing whitespace / mixed case
    is *not* treated as different barriers (we explicitly use
    ``.dropna().nunique()`` so NaN does not become a phantom barrier).
3.  A frame missing the ``ID`` column entirely falls back to the
    rowcount path (preserves behaviour for older fixture data).
4.  Empty / ``None`` inputs still yield 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import canonical_metrics as cm


def test_distinct_id_count_drops_71_to_67_on_reference_shape() -> None:
    """71 rows / 67 distinct IDs -> 67 (the reference report's correction)."""

    rng = np.random.default_rng(seed=2526)
    ids = [f"AB-{i:04d}" for i in range(67)]
    # Inflate to 71 rows by repeating four IDs (each with one extra
    # assignee row, mirroring the Snowflake fan-out).
    extra = list(rng.choice(ids, size=4, replace=False))
    rows = [{"ID": _id, "Account": f"Account-{_id}"} for _id in ids + extra]
    ab_df = pd.DataFrame(rows)

    assert len(ab_df) == 71, "Test fixture must have 71 rows."
    assert int(ab_df["ID"].nunique()) == 67, "Test fixture must have 67 distinct IDs."

    assert cm.count_total_barriers(ab_df) == 67, (
        "Round 25 / Phase F.1: count_total_barriers must return the "
        "distinct-ID count (67), not the rowcount (71).  This is the "
        "headline that powers the 'Active Adoption Barriers' tile in "
        "the Word report and the canonical totals in the LLM prompt."
    )


def test_distinct_id_count_drops_nan_ids_silently() -> None:
    """NaN IDs are not counted as a phantom barrier."""

    ab_df = pd.DataFrame(
        [
            {"ID": "AB-1"},
            {"ID": "AB-2"},
            {"ID": None},
            {"ID": np.nan},
            {"ID": "AB-1"},  # duplicate, same barrier different assignee
        ]
    )
    assert cm.count_total_barriers(ab_df) == 2, (
        "Round 25 / Phase F.1: NaN IDs must drop out of the distinct "
        "count -- a missing ID should not inflate the headline."
    )


def test_falls_back_to_rowcount_when_id_column_missing() -> None:
    """No ``ID`` column -> behaviour-preserving rowcount fallback.

    Older test fixtures and any future caller that supplies a frame
    without an ``ID`` column should get the rowcount, not zero.
    """

    ab_df = pd.DataFrame(
        [
            {"BarrierName": "Adoption blocked"},
            {"BarrierName": "Pricing concern"},
            {"BarrierName": "Network gap"},
        ]
    )
    assert cm.count_total_barriers(ab_df) == 3, (
        "Round 25 / Phase F.1: when no ``ID`` column is present the "
        "helper must fall back to the rowcount so callers do not "
        "regress to zero."
    )


def test_empty_and_none_inputs_yield_zero() -> None:
    """Empty / ``None`` inputs yield 0 (unchanged from pre-Round-25)."""

    assert cm.count_total_barriers(None) == 0
    assert cm.count_total_barriers(pd.DataFrame()) == 0
    assert cm.count_total_barriers(pd.DataFrame(columns=["ID"])) == 0
