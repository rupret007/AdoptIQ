"""Round 42 / Phase 1 + Phase 2: validator total_barriers parity.

Pre-Round-42, ``report_consistency.validate_report_consistency`` derived
``ab_count`` from ``_safe_count(ab_df) -> len(ab_df)`` while
``canonical_metrics.build_portfolio_metrics`` set
``portfolio_metrics["total_barriers"] = count_total_barriers(ab_df)``,
which Round 25 / Phase F.1 had switched to ``ab_df["ID"].nunique()``.
The two values disagree whenever the Snowflake AB extract fans out per
assignee (or per status-history change).  Result: every report path that
built ``portfolio_metrics`` (compact / EI / leader / renewal) raised
``Portfolio metric mismatch: total_barriers does not match normalized
adoption barriers.`` on every realistic dataset -- and the user hit it
on a build-18 Compact run for "All Managers / All Contact Center / 90d"
on 2026-04-28.

This test pins both halves of the fix:

1.  **Reference shape (71 rows / 67 distinct IDs)**: validator must
    accept ``portfolio_metrics`` built via ``build_portfolio_metrics``
    without raising the ``total_barriers`` error, because both sides
    now agree on ``count_total_barriers(ab_df)``.

2.  **Drift still detectable**: a hand-built ``portfolio_metrics``
    with a deliberately wrong ``total_barriers`` value still trips
    the validator's error path.  This guards against the lazy "just
    delete the comparison" cleanup that would silently swallow real
    PM drift.

The 71/67 fixture mirrors the canonical reference shape pinned by
``tests/test_round25_count_total_barriers_distinct_ids.py`` (the
"Brian Frazier / All Contact Center / 90d" report's actual values).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import canonical_metrics as cm
from report_consistency import validate_report_consistency


def _build_71_67_ab_fixture() -> pd.DataFrame:
    """Return a 71-row AB frame with exactly 67 distinct ``ID`` values.

    Mirrors ``tests/test_round25_count_total_barriers_distinct_ids.py``'s
    reference shape: 67 unique barrier IDs, 4 of which appear a second
    time (mimicking the Snowflake assignee fan-out).  Identifies the
    Round 25 / Phase F.1 acceptance signature.
    """

    rng = np.random.default_rng(seed=2526)
    ids = [f"AB-{i:04d}" for i in range(67)]
    extra = list(rng.choice(ids, size=4, replace=False))
    rows = [{"ID": _id, "Account": f"Account-{_id}"} for _id in ids + extra]
    ab = pd.DataFrame(rows)
    assert len(ab) == 71, "fixture must have 71 rows"
    assert int(ab["ID"].nunique()) == 67, "fixture must have 67 distinct IDs"
    return ab


def test_reference_shape_passes_after_round_42_fix() -> None:
    """71/67 AB shape: validator agrees with build_portfolio_metrics."""

    ab = _build_71_67_ab_fixture()
    csone = pd.DataFrame()  # cs_count = 0 keeps the comparison focused on AB
    pm = cm.build_portfolio_metrics(
        ab_df=ab,
        csone_df=csone,
        risk_scale=cm.RISK_SCALE_0_TO_10,
    )
    assert pm["total_barriers"] == 67, (
        "build_portfolio_metrics must use count_total_barriers (67), "
        "not raw rowcount (71).  If this regresses, the validator parity "
        "check below will hide the underlying canonical-helper drift."
    )

    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=pm,
    )
    barrier_errors = [
        err for err in result.get("errors", []) if "total_barriers" in err
    ]
    assert not barrier_errors, (
        "Round 42 / Phase 1: validator must NOT raise "
        "'Portfolio metric mismatch: total_barriers ...' on the 71/67 "
        "reference shape now that ab_count routes through "
        "canonical_metrics.count_total_barriers.  Observed errors: "
        f"{barrier_errors}.  Full result: {result}"
    )
    # Defensive: the validator's own metrics dict should also expose 67,
    # since that downstream value is consumed by other consistency-check
    # branches (and a future leader / renewal path may key off it).
    assert int(result["metrics"]["total_barriers"]) == 67, (
        "Round 42 / Phase 1: validator metrics['total_barriers'] must "
        "expose the canonical (distinct-ID) count, not the rowcount."
    )


def test_drift_in_portfolio_metrics_still_raises() -> None:
    """Hand-built PM with a wrong total_barriers still trips the validator.

    Round 42's fix aligns the validator's *source of truth* with
    ``count_total_barriers``.  It does NOT remove the comparison itself.
    A future PM that disagrees with the canonical helper (e.g. someone
    accidentally wires in a stale rowcount) must still surface as a
    consistency error.
    """

    ab = _build_71_67_ab_fixture()
    csone = pd.DataFrame()
    bad_pm = {
        "total_barriers": 99,  # deliberate drift from the canonical 67
        "total_cases": 0,
        "bems_count": 0,
    }
    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=bad_pm,
    )
    barrier_errors = [
        err for err in result.get("errors", []) if "total_barriers" in err
    ]
    assert barrier_errors, (
        "Round 42 / Phase 2: validator must STILL raise the "
        "'total_barriers' mismatch when portfolio_metrics drift away "
        "from canonical_metrics.count_total_barriers.  Without this, "
        "the Phase 1 alignment would silently mask real PM drift."
    )
