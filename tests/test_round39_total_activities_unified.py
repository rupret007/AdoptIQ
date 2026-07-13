"""Round 39 / Phase 1.2 — single 'Total Activities' definition.

Pre-Round-39 the Leader Report exposed THREE different "Total Activities"
columns whose formulas drifted:

  1. Team Activity Summary table -> ``cm.count_total_activities()`` =
     AP + AB + CP + BEMS  (excluded TAC)
  2. Per-member Activity Summary sub-table ->  AP + AB + CP only
     (excluded BEMS and TAC)
  3. Activity Counts Cross-Check table -> AP + AB + CP only

A director comparing the three columns saw three different numbers for
the same person.

Round 39 / Phase 1.2 unifies all three on the canonical
``cm.ACTIVITIES_MODE_FULL`` formula (AP + AB + CP + TAC + BEMS) and
extends the cross-check table with explicit BEMS / TAC columns so the
math is reproducible without spelunking.

This file pins the unified formula by reading it directly from the
canonical_metrics module and asserting all three writers reach for
``ACTIVITIES_MODE_FULL`` (or compute the same sum manually).
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

import canonical_metrics as cm
import leader_report_generator as lrg


def test_canonical_full_mode_includes_all_five():
    """Pin that ACTIVITIES_MODE_FULL counts AP + AB + CP + TAC + BEMS."""
    assert hasattr(cm, "ACTIVITIES_MODE_FULL"), (
        "ACTIVITIES_MODE_FULL is the canonical 'count everything' mode "
        "Round 39 / Phase 1.2 unifies the three writers around.  Removing "
        "it requires updating leader_report_generator.py to a new mode."
    )
    full = cm.count_total_activities(
        action_plans_df=pd.DataFrame({"x": range(3)}),
        ab_df=pd.DataFrame({"x": range(2)}),
        customer_pulse_df=pd.DataFrame({"x": range(4)}),
        tac_df=pd.DataFrame({"x": range(5)}),
        bems_count=6,
        mode=cm.ACTIVITIES_MODE_FULL,
    )
    assert full == 3 + 2 + 4 + 5 + 6, (
        f"Round 39 / Phase 1.2: ACTIVITIES_MODE_FULL must sum AP+AB+CP+TAC+BEMS, "
        f"got {full}; if the canonical formula changed, every writer below "
        f"needs an audit."
    )


@pytest.mark.parametrize("method_name", [
    "_create_summary_table",
    "_add_team_member_activity_table",
    "_cross_check_activity_counts",
])
def test_writer_uses_canonical_full_mode(method_name):
    """All three writer call sites must reference ACTIVITIES_MODE_FULL.

    Without this, a future refactor could quietly switch one writer
    back to AP+AB+CP-only and re-introduce the cross-table
    contradiction the audit caught.
    """
    method = getattr(lrg.LeaderReportGenerator, method_name, None)
    assert method is not None, (
        f"Round 39 / Phase 1.2: expected method "
        f"LeaderReportGenerator.{method_name} to exist; if it was "
        f"renamed, update this test."
    )
    src = inspect.getsource(method)
    assert (
        "ACTIVITIES_MODE_FULL" in src
        or "AP + AB + CP + TAC + BEMS" in src
        or ("tac" in src.lower() and "bems" in src.lower())
    ), (
        f"Round 39 / Phase 1.2: method {method_name} must compute Total "
        f"Activities using the canonical AP+AB+CP+TAC+BEMS formula "
        f"(typically via ``cm.ACTIVITIES_MODE_FULL``).  The pre-Round-39 "
        f"AP+AB+CP-only path produced contradictory column values across "
        f"the three Team Activity tables in the same Word document."
    )


def test_cross_check_team_total_uses_full_mode():
    """End-to-end: ``_cross_check_activity_counts`` must report
    grand_total = AP + AB + CP + TAC + BEMS."""
    from unittest import mock
    gen = lrg.LeaderReportGenerator(mock.MagicMock(), [
        ("manager@example.com", "CSSM A", "Manager"),
    ])
    team_data = {
        "CSSM A": {
            "subscriptions": pd.DataFrame([{"SUBSCRIPTION_ID": "S1"}]),
            "customers": ["Acme"],
            "action_plans": pd.DataFrame({"x": range(3)}),
            "adoption_barriers": pd.DataFrame({"x": range(2)}),
            "customer_pulse": pd.DataFrame({"x": range(4)}),
            "tac_cases": pd.DataFrame({"x": range(5)}),
        },
    }
    # Stub out BEMS counter to a known value -- the real one inspects
    # adoption_barrier IDs which we don't supply.
    with mock.patch.object(gen, "_count_bems_escalations", return_value=6):
        cross = gen._cross_check_activity_counts(team_data)
    totals = cross["team_totals"]
    assert totals["grand_total"] == 3 + 2 + 4 + 5 + 6, (
        f"Round 39 / Phase 1.2: cross-check grand_total must equal "
        f"AP+AB+CP+TAC+BEMS = 20, got {totals['grand_total']}; "
        f"individual: {cross['individual_totals']}"
    )
    assert totals["bems"] == 6
    assert totals["tac_cases"] == 5
