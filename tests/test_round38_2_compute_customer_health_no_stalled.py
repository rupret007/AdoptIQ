"""Round 38.2 / Build14 - ``_compute_customer_health`` `_bu_disp` KeyError fix.

Production trace from ``/Users/jestory/.adoptiq/adoptiq.5587.log`` (15:28:13):

    KeyError: '_bu_disp'
      File "leader_report_generator.py", line 3457, in _compute_customer_health
      File "leader_report_generator.py", line 3564, in _add_customer_health_section
      File "leader_report_generator.py", line 3644, in _add_team_insights_section

This was a pre-Round-38 latent indentation bug:

    if not stalled.empty:                                       # 3453
        stalled['_bu_disp'] = (                                 # 3454 -- assigned INSIDE guard
            stalled['BU_NAME'].dropna().astype(str).apply(_r13_norm_cust_lr)
        )
    for name, count in stalled['_bu_disp'].dropna().value_counts().items():  # 3457 -- accessed OUTSIDE
        rows.setdefault(name, ...).update({"stalled_aps": int(count)})

The bug triggered for any CSSM whose open APs were ALL <=30 days old (no
stalled rows -> ``_bu_disp`` never created -> KeyError).  It was masked
pre-Round-38 because the leader report aborted at validation when CSOne
was empty/missing, so Document Generation rarely ran on real data.
Round 38's two-pass fix correctly let the report through to Document
Generation and surfaced the latent bug.

Round 38.2 fixes the indentation by moving the ``for`` loop INSIDE the
``if not stalled.empty:`` guard.  This file pins the fix with five
positive scenarios that all used to raise on the pre-fix code path.

It also includes one cross-cut audit test (``test_age_days_clean_*``)
that pins the AB block immediately below as a CLEAN reference example
of the same guard pattern done correctly -- so a future refactor of
that block can't silently introduce the same bug.
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest import mock


@pytest.fixture
def generator():
    """Round 38.2: instantiate a LeaderReportGenerator the same way the
    existing leader-report tests in ``tests/test_leader_report.py`` do
    -- a mock Snowflake ctx + an arbitrary roster.  We never invoke any
    Snowflake-touching method here; we exercise ``_compute_customer_health``
    directly with synthetic DataFrames."""
    from leader_report_generator import LeaderReportGenerator
    return LeaderReportGenerator(mock.MagicMock(), [
        ("manager@example.com", "Test User", "Test Manager"),
    ])


def _open_ap_row(*, customer, days_old, status='Open'):
    """Build one row that:

    * has ``BU_NAME`` set so the outer ``if 'BU_NAME' in ap.columns``
      guard at line 3446 of leader_report_generator.py is satisfied.
    * has ``STATUS_C`` set to a value that ``_is_status_open`` accepts
      so the row survives the ``open_ap`` filter at line 3450.
    * has ``LAST_MODIFIED_DATE`` set ``days_old`` days before now so the
      ``ages > 30`` filter at line 3452 keeps or drops it deterministically.
    """
    return {
        'BU_NAME': customer,
        'STATUS_C': status,
        'LAST_MODIFIED_DATE': (pd.Timestamp.utcnow() - pd.Timedelta(days=days_old)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Phase 3 / Round 38.2 - the five scenarios that all USED to raise
# ---------------------------------------------------------------------------


def test_no_stalled_aps_does_not_raise(generator):
    """The exact production trigger: open APs exist, but none are
    older than 30 days.  Pre-fix this raised ``KeyError: '_bu_disp'``;
    post-fix the function returns cleanly with no stalled-AP entries."""
    ap_df = pd.DataFrame([
        _open_ap_row(customer='Acme Corp', days_old=5),
        _open_ap_row(customer='Acme Corp', days_old=20),
        _open_ap_row(customer='Beta Inc',  days_old=29),
    ])
    rows = generator._compute_customer_health({
        'action_plans': ap_df,
        'adoption_barriers': pd.DataFrame(),
        'customer_pulse': pd.DataFrame(),
    })
    # No stalled APs -> no rows mention stalled_aps; function may return
    # an empty list or rows derived from AB/CP only (both empty here).
    assert isinstance(rows, list)
    for r in rows:
        assert 'stalled_aps' not in r, (
            f"row {r!r} carries a stalled_aps key but no AP in the "
            "fixture is >30 days old; the post-fix code path must not "
            "double-count or leak from a stale column"
        )


def test_some_stalled_some_fresh_aps(generator):
    """Mixed AP fixture: only the >30-day rows must be counted."""
    ap_df = pd.DataFrame([
        _open_ap_row(customer='Acme Corp', days_old=5),    # fresh
        _open_ap_row(customer='Acme Corp', days_old=45),   # stalled
        _open_ap_row(customer='Acme Corp', days_old=60),   # stalled
        _open_ap_row(customer='Beta Inc',  days_old=10),   # fresh
        _open_ap_row(customer='Beta Inc',  days_old=90),   # stalled
    ])
    rows = generator._compute_customer_health({
        'action_plans': ap_df,
        'adoption_barriers': pd.DataFrame(),
        'customer_pulse': pd.DataFrame(),
    })
    by_name = {r['customer']: r for r in rows}
    assert by_name.get('Acme Corp', {}).get('stalled_aps') == 2
    assert by_name.get('Beta Inc',  {}).get('stalled_aps') == 1


def test_all_aps_closed_status_does_not_raise(generator):
    """All APs have closed status -> ``open_ap`` is empty -> ``stalled``
    is empty.  Same pre-fix code path as the no-stalled case."""
    ap_df = pd.DataFrame([
        _open_ap_row(customer='Acme Corp', days_old=90, status='Closed'),
        _open_ap_row(customer='Beta Inc',  days_old=120, status='Completed'),
    ])
    rows = generator._compute_customer_health({
        'action_plans': ap_df,
        'adoption_barriers': pd.DataFrame(),
        'customer_pulse': pd.DataFrame(),
    })
    assert isinstance(rows, list)
    for r in rows:
        assert 'stalled_aps' not in r


def test_no_bu_name_column_short_circuits(generator):
    """AP DataFrame missing ``BU_NAME`` -> outer guard at line 3446
    short-circuits and the buggy block is never reached.  This is
    audit coverage that the outer guard is still the first line of
    defence after the inner-guard fix."""
    ap_df = pd.DataFrame([
        {'STATUS_C': 'Open', 'LAST_MODIFIED_DATE': pd.Timestamp.utcnow().isoformat()},
    ])
    rows = generator._compute_customer_health({
        'action_plans': ap_df,
        'adoption_barriers': pd.DataFrame(),
        'customer_pulse': pd.DataFrame(),
    })
    assert isinstance(rows, list)
    assert rows == []


def test_empty_action_plans_does_not_raise(generator):
    """Top-level ``ap.empty`` short-circuits at line 3446.  Same code
    path as a CSSM with zero APs in scope."""
    rows = generator._compute_customer_health({
        'action_plans': pd.DataFrame(),
        'adoption_barriers': pd.DataFrame(),
        'customer_pulse': pd.DataFrame(),
    })
    assert rows == []


# ---------------------------------------------------------------------------
# Cross-cut audit pin: the AB block immediately below uses the SAME
# anti-pattern shape (transient ``_age_days`` column assigned inside an
# ``if not open_ab.empty:`` guard) but does it CORRECTLY.  Pin that so a
# future refactor can't silently regress it into the same bug.
# ---------------------------------------------------------------------------


def test_age_days_clean_when_no_open_abs(generator):
    """All ABs closed -> ``open_ab`` is empty -> ``_age_days`` never
    created.  The reference-correct guard at line 3478 must keep this
    from raising the analogous ``KeyError: '_age_days'``."""
    ab_df = pd.DataFrame([
        {
            'BU_NAME': 'Acme Corp',
            'STATUS_C': 'Closed',
            'CREATED_DATE': pd.Timestamp.utcnow().isoformat(),
        },
    ])
    rows = generator._compute_customer_health({
        'action_plans': pd.DataFrame(),
        'adoption_barriers': ab_df,
        'customer_pulse': pd.DataFrame(),
    })
    assert isinstance(rows, list)
    for r in rows:
        assert 'oldest_open_ab_days' not in r


def test_age_days_clean_when_some_open_abs(generator):
    """Positive control: with at least one open AB, ``_age_days`` is
    assigned, the groupby runs, and ``oldest_open_ab_days`` lands on
    the customer row.  Pins the happy path so the cleanup test above
    can't be made vacuously true by a refactor that removes the
    happy-path output."""
    ab_df = pd.DataFrame([
        {
            'BU_NAME': 'Acme Corp',
            'STATUS_C': 'Open',
            'CREATED_DATE': (pd.Timestamp.utcnow() - pd.Timedelta(days=42)).isoformat(),
        },
    ])
    rows = generator._compute_customer_health({
        'action_plans': pd.DataFrame(),
        'adoption_barriers': ab_df,
        'customer_pulse': pd.DataFrame(),
    })
    by_name = {r['customer']: r for r in rows}
    assert 'Acme Corp' in by_name
    assert by_name['Acme Corp'].get('oldest_open_ab_days') == 42


# ---------------------------------------------------------------------------
# Source-shape pin: assert the ``for`` loop now lives INSIDE the
# ``if not stalled.empty:`` block, so a future refactor can't quietly
# revert the indentation.  Pairs with the behavioural tests above.
# ---------------------------------------------------------------------------


def test_for_loop_lives_inside_if_not_stalled_empty_guard():
    """Pin the indentation: line containing
    ``for name, count in stalled['_bu_disp'].dropna().value_counts()``
    must be at strictly deeper indentation than its enclosing
    ``if not stalled.empty:`` line."""
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(os.path.dirname(here), 'leader_report_generator.py')
    with open(src_path, 'r', encoding='utf-8') as fh:
        lines = fh.readlines()

    if_idx = None
    for i, line in enumerate(lines):
        if 'if not stalled.empty:' in line:
            if_idx = i
            break
    assert if_idx is not None, (
        "anchor 'if not stalled.empty:' not found in leader_report_generator.py"
    )
    if_indent = len(lines[if_idx]) - len(lines[if_idx].lstrip())

    for_idx = None
    for j in range(if_idx + 1, min(if_idx + 60, len(lines))):
        if "for name, count in stalled['_bu_disp']" in lines[j]:
            for_idx = j
            break
    assert for_idx is not None, (
        "anchor for-loop on stalled['_bu_disp'] not found within 60 lines "
        "of the if-not-stalled-empty guard; the Round 38.2 fix may have "
        "been moved or undone"
    )
    for_indent = len(lines[for_idx]) - len(lines[for_idx].lstrip())
    assert for_indent > if_indent, (
        f"Round 38.2 regression: for-loop on line {for_idx+1} (indent "
        f"{for_indent}) is at SAME OR SHALLOWER indentation than the "
        f"if-not-stalled-empty guard on line {if_idx+1} (indent "
        f"{if_indent}).  This re-introduces the KeyError: '_bu_disp' "
        f"bug for CSSMs whose open APs are all <=30 days old."
    )
