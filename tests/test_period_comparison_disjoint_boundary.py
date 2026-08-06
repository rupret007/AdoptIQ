"""Round 3 / Phase 4.1 regression test.

The current and previous period SQL windows in
``fetch_period_comparison`` must be strictly disjoint so the boundary
day (``today - days``) is not counted in both buckets. The previous
form used:

  current:  D >= today - days
  previous: D BETWEEN today - 2*days AND today - days

…where ``BETWEEN`` is inclusive on both ends, double-counting the day
at exactly ``today - days``.

The fix uses ``D >= today - 2*days AND D < today - days`` for the
previous bucket.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _backend_text() -> str:
    return (PROJECT_ROOT / "adoptiq_backend.py").read_text(
        encoding="utf-8", errors="ignore"
    )


def test_period_comparison_uses_strict_less_than_for_previous():
    """The previous-period CASE clause must use ``<`` against the
    current-window boundary, not ``BETWEEN ... AND ...`` which would
    double-count the boundary day.

    Round 8 / Phase 2.3 replaced the in-SQL ``DATEADD(day, -%s,
    CURRENT_DATE())`` window edges with Python-computed UTC ISO
    strings bound as ``%s`` parameters, so the SQL no longer mentions
    ``DATEADD`` at all -- but the disjoint ``< %s`` invariant still
    holds.
    """
    src = _backend_text()
    idx = src.find("def fetch_period_comparison")
    assert idx >= 0
    body = src[idx : idx + 6000]
    # Strict-less-than against the current-window boundary parameter
    # must be present (Round 8 form: ``< %s`` with the ``_curr_start``
    # value bound from Python).
    assert "<  %s" in body or "< %s" in body, (
        "previous-period upper bound must be strict less-than the "
        "current-window start (now bound as %s from Python)"
    )
    # The disjoint two-bound previous-period filter must remain.
    assert_in_source(body, "AND DATE(", label='body')
    # The legacy double-counting BETWEEN form must not return.
    assert "BETWEEN DATEADD(day, -%s, CURRENT_DATE())" not in body, (
        "fetch_period_comparison still uses overlapping BETWEEN windows; "
        "boundary day will be double-counted"
    )
    # Round 8 / Phase 2.3: window edges must be computed via the
    # UTC helper, not a session-TZ DATEADD() expression.
    assert "_utc_window_start_iso" in body, (
        "Round 8 / Phase 2.3 requires Python-computed UTC window edges"
    )


def test_period_comparison_documents_disjoint_intent():
    """A comment in the source should say the windows are disjoint /
    explain the boundary-day fix so a future refactor can't silently
    reintroduce the overlap."""
    src = _backend_text()
    assert (
        "disjoint" in src.lower()
        or "boundary day" in src.lower()
        or "double-counted" in src.lower()
    )
