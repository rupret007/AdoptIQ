"""Round 30 / H3 — compact recent-window comparison must be tz-aware
on BOTH sides so the recent-30-day filter is invariant under the
timezone encoding of the upstream Snowflake column.

Round 8 / Phase 3.4 introduced a "naive UTC on both sides" contract
that silently dropped offsets for tz-aware inputs (e.g. a row stamped
``2026-04-25T22:00:00-05:00`` was coerced to a naive
``2026-04-25T22:00:00``, placing it five hours later on the timeline
than its real UTC instant).  The Round 30 / H3 fix passes
``utc=True`` to ``pd.to_datetime`` so tz-aware inputs are normalized
to UTC and naive inputs are interpreted as UTC, then compares
against ``datetime.now(timezone.utc)`` (tz-aware).
"""

from __future__ import annotations

import inspect

import compact_report_formatter


def test_round30_h3_compact_uses_utc_true_on_to_datetime() -> None:
    """Source pin: the recent-30-day window in
    ``compact_report_formatter`` MUST call ``pd.to_datetime`` with
    ``utc=True`` so the comparison is tz-aware."""
    src = inspect.getsource(compact_report_formatter)
    # The post-fix call site is anchored on the ``date_opened``
    # assignment inside the ``recent_30`` filter.
    assert "pd.to_datetime(" in src, (
        "Round 30 / H3: compact must call pd.to_datetime to coerce "
        "the date column."
    )
    # Pin the tz-aware contract.
    assert "utc=True" in src, (
        "Round 30 / H3: pd.to_datetime in the compact recent-30-day "
        "filter must pass utc=True so tz-aware inputs are normalized "
        "to UTC and naive inputs are interpreted as UTC."
    )


def test_round30_h3_compact_does_not_strip_tzinfo() -> None:
    """Negative source pin: the post-fix code must NOT call
    ``.replace(tzinfo=None)`` on the comparison clock, because that
    re-introduces the tz-stripping bug."""
    src = inspect.getsource(compact_report_formatter)
    # Round 30 / H3 fix removed the ``.replace(tzinfo=None)`` from the
    # ``_now_naive_utc`` line.  Pin its absence.
    bad_pattern = "datetime.now(timezone.utc).replace(tzinfo=None)"
    assert bad_pattern not in src, (
        "Round 30 / H3: compact must NOT strip tzinfo from "
        "datetime.now(timezone.utc) -- the comparison must stay "
        "tz-aware on both sides."
    )


def test_round30_h3_comment_reflects_new_contract() -> None:
    """The Round 8 / Phase 3.4 inline comment described the old
    'both sides naive UTC' contract.  Round 30 / H3 updates the
    comment to describe the new 'both sides tz-aware UTC' contract.
    Pin the new contract phrase so a future edit cannot silently
    flip the documented intent."""
    src = inspect.getsource(compact_report_formatter)
    assert "tz-AWARE" in src or "tz-aware UTC" in src, (
        "Round 30 / H3: the inline comment for the recent-30-day "
        "filter must describe the new 'both sides tz-aware UTC' "
        "contract so a future reader understands why utc=True is "
        "required."
    )


def test_round30_h3_uses_tz_aware_now() -> None:
    """Pin that the comparison clock is ``datetime.now(timezone.utc)``
    without a tzinfo strip."""
    src = inspect.getsource(compact_report_formatter)
    # The post-fix code uses ``_now_utc_aware`` (or similar) bound to
    # ``datetime.now(timezone.utc)`` without a strip.
    assert "datetime.now(timezone.utc)" in src, (
        "Round 30 / H3: comparison clock must be "
        "datetime.now(timezone.utc) (tz-aware)."
    )
