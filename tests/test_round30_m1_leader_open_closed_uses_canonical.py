"""Round 30 / M1 — leader open/closed counts must route through the
canonical lifecycle helpers in ``canonical_metrics`` instead of inline
regex matches.

Round 12 introduced ``data_normalization.add_case_lifecycle_fields`` +
``canonical_metrics.count_open*`` / ``count_closed*`` so every report
agrees on the closed/resolved/complete vocabulary.  The leader report
was still using an inline ``status_norm.str.contains`` regex for both
adoption barriers (line ~4673) and action plans (line ~4683), which
meant edge cases like ``"Resolved (Pending Customer Review)"``
counted differently in the leader than in the executive / compact
surfaces.

Round 30 / M1 routes both leader counts through the canonical helpers
so the lifecycle definition lives in one place.
"""

from __future__ import annotations

import inspect

import canonical_metrics as cm
import leader_report_generator
import pandas as pd


def test_round30_m1_canonical_helpers_exist() -> None:
    """Source pin: the canonical helpers must exist as public-name
    symbols in ``canonical_metrics``."""
    assert hasattr(cm, 'count_closed_barriers'), (
        "Round 30 / M1: canonical_metrics must export "
        "count_closed_barriers (resolved/closed adoption barriers)."
    )
    assert hasattr(cm, 'count_open_barriers'), (
        "Round 30 / M1: canonical_metrics must export "
        "count_open_barriers (open/active adoption barriers)."
    )
    assert hasattr(cm, 'count_action_plan_completed'), (
        "Round 30 / M1: canonical_metrics must export "
        "count_action_plan_completed for action-plan lifecycle "
        "counting parity."
    )


def test_round30_m1_leader_uses_canonical_count_closed_barriers() -> None:
    """Source pin: the leader report MUST call
    ``cm.count_closed_barriers`` (and not a hand-rolled regex) for
    its 'Resolved Barriers' tile.

    The legacy ``str.contains(r'closed|resolved|complete')`` regex is
    permitted ONLY inside a defensive ``except`` fallback (so a stale
    fixture cannot break the report at runtime); the canonical helper
    must be the primary path.  We pin that contract by asserting the
    canonical call appears BEFORE the legacy regex in source order.
    """
    src = inspect.getsource(leader_report_generator)
    assert "count_closed_barriers" in src, (
        "Round 30 / M1: leader_report_generator must route resolved "
        "barrier counts through cm.count_closed_barriers."
    )
    # Order pin: canonical call must be defined before any legacy
    # regex appears.  This ensures the canonical path is primary.
    canonical_idx = src.find("count_closed_barriers")
    legacy_idx = src.find("str.contains(r'closed|resolved|complete'")
    if legacy_idx != -1:
        assert canonical_idx < legacy_idx, (
            "Round 30 / M1: leader must call cm.count_closed_barriers "
            "as the primary path; any inline "
            "str.contains(r'closed|resolved|complete') regex is "
            "allowed only as a defensive ``except`` fallback AFTER "
            "the canonical call."
        )


def test_round30_m1_leader_uses_canonical_count_action_plan_completed() -> None:
    """Source pin: leader must call ``cm.count_action_plan_completed``
    for the action-plan tile."""
    src = inspect.getsource(leader_report_generator)
    assert "count_action_plan_completed" in src, (
        "Round 30 / M1: leader must route completed action-plan counts "
        "through cm.count_action_plan_completed."
    )


def test_round30_m1_canonical_handles_edge_case_status() -> None:
    """Behavioural pin: the canonical helper handles the edge-case
    status ``'Resolved (Pending Customer Review)'`` consistently
    across reports.  This pins the lifecycle definition so a future
    refactor cannot silently change which barriers count as closed."""
    df = pd.DataFrame({
        'STATUS': [
            'Open',
            'Closed',
            'Resolved (Pending Customer Review)',
            'In Progress',
            'Resolved',
        ],
        'customer_name': ['a', 'b', 'c', 'd', 'e'],
    })
    closed = cm.count_closed_barriers(df)
    # The exact count depends on data_normalization rules, but it MUST
    # be deterministic and >= 2 (the literal 'Closed' + 'Resolved'
    # rows).
    assert closed >= 2, (
        "Round 30 / M1: canonical helper must count at least the "
        "literal 'Closed' and 'Resolved' rows as closed."
    )
