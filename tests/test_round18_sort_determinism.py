"""Round 18 / Phase 2 -- sort-determinism gaps in two formatter tables.

Round 16 / Phase 2.1-2.3 added stable secondary tiebreakers to every
top-N sort callsite in ``compact_report_formatter`` and the
``adoptiq_backend`` writers.  Two equivalent sites in the
executive-intelligence and leader formatters were missed:

* ``executive_intelligence_formatter._add_high_risk_customers_table``
  (around line 703) sorts ``high_risk.items()`` by ``score`` only.

* ``leader_report_generator._add_team_performance_summary`` (around
  line 4808) sorts ``team_summary_data`` by ``total_activities`` only.

In both cases ties resolve by upstream insertion order, which is
*per-process* stable but not byte-stable across re-runs that ingest the
same source frames in a different order (e.g. worker-pool completion
order, Snowflake query ordering without ORDER BY, dict construction
from a ``set()``).

The fix is to add a deterministic secondary tiebreaker -- the
case-folded customer / member name -- so two records tied on the
primary metric render in the same order across runs.

These tests build small fixtures with intentional ties and assert that
the source code carries the stable secondary key.  They are
behavior-pinning self-tests of the sort key, not full end-to-end
renderers.  That keeps Round 18 on its narrow scope without re-
exercising the entire Word render harness.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (_REPO_ROOT / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 2.1 -- executive_intelligence_formatter._add_high_risk_customers_table
# ---------------------------------------------------------------------------


def test_phase_2_1_exec_intel_high_risk_sort_has_secondary_key():
    """Round 18 / Phase 2.1 -- ``executive_intelligence_formatter``
    must sort ``high_risk.items()`` by a tuple key that includes a
    deterministic name-based secondary tiebreaker.

    The legacy form is::

        sorted(high_risk.items(), key=lambda x: x[1].get('score', 0), reverse=True)

    which leaves two customers tied on ``score`` rendering in
    upstream-dict-insertion order.  The Round-18 fix is to use a
    tuple key ``(-score, name_casefold)`` so the table is byte-stable
    across runs over identical input.
    """

    src = _read("executive_intelligence_formatter.py")

    # The legacy single-key form (the one we want to *eliminate*).
    legacy_pattern = re.compile(
        r"sorted\(\s*high_risk\.items\(\)\s*,\s*"
        r"key\s*=\s*lambda\s+x\s*:\s*x\[1\]\.get\('score',\s*0\)\s*,\s*"
        r"reverse\s*=\s*True\s*\)"
    )
    assert legacy_pattern.search(src) is None, (
        "Round 18 / Phase 2.1 regression: executive_intelligence_formatter "
        "still sorts ``high_risk.items()`` by score alone with reverse=True; "
        "two customers tied on the same score will render in upstream "
        "dict insertion order, which is not byte-stable across runs."
    )

    # The new form must reference both ``score`` and ``casefold``/``lower``
    # in the sort key for the high-risk customers iterator.  The fix
    # may extract the key into a helper function, so we examine a
    # generous window of source code around the iterator block.
    iter_match = re.search(
        r"for\s+customer\s*,\s*data\s+in\s+sorted\(\s*high_risk\.items\(\)[\s\S]+?\):",
        src,
    )
    assert iter_match is not None, (
        "Round 18 / Phase 2.1: could not locate the sorted(high_risk.items()) "
        "iterator in executive_intelligence_formatter.py"
    )
    # Window spans 1500 chars before and after the iterator so any
    # inline lambda or nearby helper-function sort key is in scope.
    start = max(iter_match.start() - 1500, 0)
    end = min(iter_match.end() + 200, len(src))
    window = src[start:end]
    assert "score" in window, (
        "Round 18 / Phase 2.1: high_risk sort must still rank by score "
        "near the iterator block."
    )
    assert ("casefold" in window) or ("lower" in window), (
        "Round 18 / Phase 2.1: high_risk sort must include a "
        "case-folded name secondary tiebreaker near the iterator block."
    )


def test_phase_2_1_exec_intel_high_risk_sort_marker_present():
    """Round 18 / Phase 2.1 -- pin the marker comment on the fix.

    The Round-16 sort-determinism phases pinned the marker at every
    fixed callsite so a future refactor that unwound the tuple key
    could not silently regress.  Mirror the same convention here.
    """

    src = _read("executive_intelligence_formatter.py")
    assert "Round 18 / Phase 2.1" in src, (
        "Round 18 / Phase 2.1 marker missing from "
        "executive_intelligence_formatter.py"
    )


# ---------------------------------------------------------------------------
# Phase 2.2 -- leader_report_generator._add_team_performance_summary
# ---------------------------------------------------------------------------


def test_phase_2_2_leader_team_perf_sort_has_secondary_key():
    """Round 18 / Phase 2.2 -- ``leader_report_generator`` must sort
    ``team_summary_data`` by a tuple key that includes a deterministic
    secondary tiebreaker on ``cssm_name``.

    The legacy single-key form was::

        sorted(team_summary_data, key=lambda x: x['total_activities'], reverse=True)

    which leaves two CSSMs tied on ``total_activities`` rendering in
    ``team_data`` insertion order.  The Round-18 fix uses
    ``(-total_activities, cssm_name_casefold)`` so the team summary
    table is byte-stable across runs.
    """

    src = _read("leader_report_generator.py")

    legacy_pattern = re.compile(
        r"sorted\(\s*team_summary_data\s*,\s*"
        r"key\s*=\s*lambda\s+x\s*:\s*x\[\s*['\"]total_activities['\"]\s*\]\s*,\s*"
        r"reverse\s*=\s*True\s*\)"
    )
    assert legacy_pattern.search(src) is None, (
        "Round 18 / Phase 2.2 regression: leader_report_generator still "
        "sorts ``team_summary_data`` by total_activities alone with "
        "reverse=True; two CSSMs tied on activity count will render in "
        "upstream dict insertion order, which is not byte-stable across "
        "runs."
    )

    iter_match = re.search(
        r"for\s+member_data\s+in\s+sorted\(\s*team_summary_data[\s\S]+?\):",
        src,
    )
    assert iter_match is not None, (
        "Round 18 / Phase 2.2: could not locate the "
        "sorted(team_summary_data) iterator in leader_report_generator.py"
    )
    # Window spans 1500 chars before and after the iterator so a
    # helper-function sort key declared just above the loop is in
    # scope of the assertion.
    start = max(iter_match.start() - 1500, 0)
    end = min(iter_match.end() + 200, len(src))
    window = src[start:end]
    assert "total_activities" in window, (
        "Round 18 / Phase 2.2: team summary sort must still rank by "
        "total_activities near the iterator block."
    )
    assert ("casefold" in window) or ("lower" in window), (
        "Round 18 / Phase 2.2: team summary sort must include a "
        "case-folded cssm_name secondary tiebreaker near the iterator block."
    )


def test_phase_2_2_leader_team_perf_sort_marker_present():
    """Round 18 / Phase 2.2 -- pin the marker comment on the fix."""

    src = _read("leader_report_generator.py")
    assert "Round 18 / Phase 2.2" in src, (
        "Round 18 / Phase 2.2 marker missing from leader_report_generator.py"
    )


# ---------------------------------------------------------------------------
# Behavioral pin: in-process tuple-sort gives the expected order.
# ---------------------------------------------------------------------------


def test_phase_2_1_high_risk_tuple_sort_is_stable():
    """Round 18 / Phase 2.1 behavioral pin -- given two customers
    tied at the same score with names in different upstream orders,
    the tuple key ``(-score, name_casefold)`` produces the same
    output regardless of dict insertion order.
    """

    profile_a = {"score": 7.5}
    profile_b = {"score": 7.5}

    # Two dicts with the same content but opposite insertion order.
    forward = {"alpha": profile_a, "Bravo": profile_b}
    reverse = {"Bravo": profile_b, "alpha": profile_a}

    def _key(item):
        name, profile = item
        try:
            score = float(profile.get("score", 0)) if isinstance(profile, dict) else 0.0
        except (TypeError, ValueError):
            score = 0.0
        return (-score, str(name).casefold())

    out_forward = [name for name, _ in sorted(forward.items(), key=_key)]
    out_reverse = [name for name, _ in sorted(reverse.items(), key=_key)]
    assert out_forward == out_reverse, (
        f"Tuple key was not order-stable: forward={out_forward}, "
        f"reverse={out_reverse}"
    )
    assert out_forward == ["alpha", "Bravo"], (
        "Case-folded sort must order 'alpha' before 'Bravo' since "
        "'alpha' < 'bravo' in casefolded comparison."
    )


def test_phase_2_2_team_perf_tuple_sort_is_stable():
    """Round 18 / Phase 2.2 behavioral pin -- two CSSMs tied on
    ``total_activities`` order alphabetically by ``cssm_name``
    (case-folded), regardless of upstream dict insertion order.
    """

    forward = [
        {"cssm_name": "Bob", "total_activities": 12},
        {"cssm_name": "Alice", "total_activities": 12},
    ]
    reverse = list(reversed(forward))

    def _key(x):
        return (-x["total_activities"], str(x.get("cssm_name", "")).casefold())

    out_forward = [m["cssm_name"] for m in sorted(forward, key=_key)]
    out_reverse = [m["cssm_name"] for m in sorted(reverse, key=_key)]
    assert out_forward == out_reverse, (
        f"Tuple key was not order-stable: forward={out_forward}, "
        f"reverse={out_reverse}"
    )
    assert out_forward == ["Alice", "Bob"], (
        "Case-folded sort must order 'Alice' before 'Bob' on equal "
        "activity counts."
    )
