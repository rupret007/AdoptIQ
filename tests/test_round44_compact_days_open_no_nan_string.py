"""Round 44 / Phase 1 regression test.

Pin the compact TAC-lifecycle table renderer so the "Days Open" cell
NEVER renders the literal string ``"nan"``.  Pre-fix the compact path
at ``executive_intelligence_formatter.py:913`` did
``str(row.get('open_age_days', 'N/A'))`` -- when ``open_age_days`` is
missing/NaN, ``str(float('nan')) == 'nan'`` and the cell rendered as
literal ``nan``.  Audited Build-20 compact output had 100% of the 20
sampled lifecycle rows render ``nan`` even when ``open_date`` and
``closed_date`` were both populated.

Round 44 / Phase 1 introduces a ``_format_open_age_days`` helper that
backfills from open/closed dates and never emits the literal string
``"nan"``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import executive_intelligence_formatter as eif


def _row(open_age_days, open_date=None, closed_date=None):
    return pd.Series(
        {
            "open_age_days": open_age_days,
            "open_date": open_date,
            "closed_date": closed_date,
        }
    )


def test_helper_exists() -> None:
    """The Round 44 / Phase 1 helper MUST be exposed at module level so
    other formatters can re-use the same NaN-safe semantic."""

    assert hasattr(eif, "_format_open_age_days"), (
        "executive_intelligence_formatter must expose _format_open_age_days "
        "(Round 44 / Phase 1 NaN-safe Days Open renderer)."
    )


def test_nan_open_age_with_valid_dates_backfills_from_delta() -> None:
    """When ``open_age_days`` is NaN BUT both dates parse, the helper
    returns the integer day delta -- never the literal string ``nan``."""

    opened = datetime(2026, 4, 18, 8, 25, tzinfo=timezone.utc)
    closed = datetime(2026, 4, 18, 10, 9, tzinfo=timezone.utc)
    out = eif._format_open_age_days(_row(float("nan"), opened, closed))
    assert out != "nan"
    assert out.lower() != "nan"
    # Same-day open/close -> 0 days open (clamped to >=0).
    assert out == "0"


def test_nan_open_age_with_only_open_date_falls_back_to_today_delta() -> None:
    """When only ``open_date`` is parseable, the helper renders the
    days-from-opened-to-now count.  Still must not be ``"nan"``."""

    opened = datetime.now(tz=timezone.utc) - timedelta(days=11, hours=3)
    out = eif._format_open_age_days(_row(float("nan"), opened, None))
    assert out != "nan"
    # 11.x days truncated to 11 (or possibly 10 due to clock skew); just
    # require a plausible non-negative integer.
    assert out.isdigit()
    assert int(out) >= 0


def test_nothing_parseable_returns_em_dash_not_nan() -> None:
    """When ``open_age_days`` is NaN AND neither date parses, the helper
    must return the em-dash placeholder -- NEVER the literal ``"nan"``."""

    out = eif._format_open_age_days(_row(float("nan"), None, None))
    assert out != "nan"
    assert out.lower() != "nan"
    assert out == "\u2014"


def test_valid_open_age_days_passes_through() -> None:
    """When ``open_age_days`` is a valid non-negative integer, the helper
    returns it verbatim (no chrome change for the happy path)."""

    out = eif._format_open_age_days(_row(7, None, None))
    assert out == "7"


def test_negative_open_age_clamps_to_zero_via_dates_path() -> None:
    """Negative deltas (closed before opened -- bad data) clamp to 0
    rather than rendering ``-N``, so the cell is always non-negative."""

    opened = datetime(2026, 4, 20, tzinfo=timezone.utc)
    closed = datetime(2026, 4, 10, tzinfo=timezone.utc)
    out = eif._format_open_age_days(_row(float("nan"), opened, closed))
    assert out == "0"


def test_string_iso_dates_parse_correctly() -> None:
    """The dates may arrive as ISO strings (CSOne export shape) -- the
    helper must coerce them via pandas, not require pre-parsed datetimes."""

    out = eif._format_open_age_days(
        _row(float("nan"), "2026-04-18T08:25:00", "2026-04-23T10:09:00")
    )
    assert out != "nan"
    assert out == "5"


def test_lifecycle_render_call_site_uses_helper() -> None:
    """The compact lifecycle table render block at ``cells[5].text`` must
    route through ``_format_open_age_days`` -- not the pre-fix
    ``str(row.get('open_age_days', 'N/A'))`` pattern that produced the
    literal ``"nan"`` rendering."""

    src = (
        __import__("pathlib").Path(eif.__file__).read_text(encoding="utf-8")
    )
    assert "_format_open_age_days(row)" in src, (
        "lifecycle table cell[5] must call _format_open_age_days(row) per "
        "Round 44 / Phase 1; the pre-fix str(row.get('open_age_days', "
        "'N/A')) pattern was the source of the 100% NaN rendering."
    )
    # Defense in depth: assert the pre-fix pattern is gone from cell[5]
    # (it can still legitimately appear in OTHER columns / helper
    # signatures, so we only require the cells[5] line to have changed).
    assert "cells[5].text = str(row.get('open_age_days'" not in src
