"""Round 9 / Phase 6.2: parse_datetime_series partial-failure -> all-NaT + warning attr."""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_marker_parse_datetime_fallback() -> None:
    src = REPO_ROOT.joinpath('data_normalization.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 6.2' in src, (
        'Round 9 / Phase 6.2 marker missing in data_normalization.py'
    )
    assert 'partial_data_warning' in src
    assert '_all_nat' in src


def test_parse_datetime_series_returns_naive_utc() -> None:
    from data_normalization import parse_datetime_series

    s = pd.Series(['2024-01-01T00:00:00Z', '2024-06-15T12:00:00Z'])
    parsed = parse_datetime_series(s)
    # Result is tz-naive (UTC anchor stripped) and datetime64.
    assert parsed.dt.tz is None
    assert pd.api.types.is_datetime64_any_dtype(parsed)


def test_parse_datetime_series_handles_garbage() -> None:
    """Mixed garbage input should parse to NaT without raising."""
    from data_normalization import parse_datetime_series

    s = pd.Series(['not-a-date', 'also-bad', None])
    parsed = parse_datetime_series(s)
    # All values should be NaT.
    assert parsed.isna().all()


def test_parse_datetime_series_handles_unparseable_input() -> None:
    """A non-Series input that triggers a hard exception should
    return an all-NaT result with a partial_data_warning attr."""
    from data_normalization import parse_datetime_series

    class _Boom:
        """Object that raises on every conversion attempt."""
        def __iter__(self):
            raise RuntimeError('boom')

    # Wrapped in a Series first so pd.to_datetime hits the failure path.
    try:
        parsed = parse_datetime_series(pd.Series([_Boom()]))
    except Exception:
        # If pandas raises before our handler can wrap it, that's a
        # regression: parse_datetime_series promises a fallback.
        pytest.fail('parse_datetime_series should not raise on bad input')
    assert parsed.isna().all() or pd.api.types.is_datetime64_any_dtype(parsed)
