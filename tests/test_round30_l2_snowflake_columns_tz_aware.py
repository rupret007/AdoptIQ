"""Round 30 / L2 — Snowflake datetime columns must be asserted
tz-aware at ingest.

Round 30 introduces ``_assert_datetime_columns_tz_aware`` in
``snowflake_prefetch`` and an ``ALTER SESSION SET TIMEZONE='UTC'``
on every connection in ``adoptiq_backend``.  Together they catch
``TIMESTAMP_NTZ`` columns at fetch time so reports never compare
naive Snowflake datetimes against ``datetime.now(timezone.utc)``.
"""

from __future__ import annotations

import inspect
import logging
import os

import pandas as pd
import pytest

import adoptiq_backend
import snowflake_prefetch


def test_round30_l2_assert_helper_exists() -> None:
    """The helper must be a public-name symbol so prefetch wrappers
    can import it and CI tests can pin its contract."""
    assert hasattr(snowflake_prefetch, '_assert_datetime_columns_tz_aware'), (
        "Round 30 / L2: snowflake_prefetch must export "
        "_assert_datetime_columns_tz_aware."
    )


def test_round30_l2_naive_datetime_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A frame with a tz-naive timestamp column whose name matches the
    canonical hints must trigger a structured WARN with the
    ``snowflake.tz_naive_datetime_columns`` event key."""
    df = pd.DataFrame({
        'CASE_ID': ['c-1', 'c-2'],
        'OPENED_AT': pd.to_datetime(
            ['2026-01-01', '2026-01-02'], utc=False,
        ),
    })
    prev = os.environ.pop('ADOPTIQ_STRICT_MODE', None)
    try:
        with caplog.at_level(logging.WARNING, logger='snowflake_prefetch'):
            snowflake_prefetch._assert_datetime_columns_tz_aware(
                df, dataset_name='unit-test',
            )
    finally:
        if prev is not None:
            os.environ['ADOPTIQ_STRICT_MODE'] = prev
    matched = [
        r for r in caplog.records
        if 'snowflake.tz_naive_datetime_columns' in r.getMessage()
    ]
    assert matched, (
        "Round 30 / L2: a tz-naive datetime column whose name matches "
        "_DATETIME_COLUMN_HINTS must produce a structured WARN."
    )


def test_round30_l2_aware_datetime_passes() -> None:
    """A tz-aware datetime column must pass even under strict mode."""
    df = pd.DataFrame({
        'CASE_ID': ['c-1', 'c-2'],
        'OPENED_AT': pd.to_datetime(
            ['2026-01-01', '2026-01-02'], utc=True,
        ),
    })
    snowflake_prefetch._assert_datetime_columns_tz_aware(
        df, dataset_name='unit-test', strict=True,
    )


def test_round30_l2_strict_mode_raises_value_error() -> None:
    """Strict mode raises ``ValueError`` so CI gates can pin the
    contract."""
    df = pd.DataFrame({
        'CASE_ID': ['c-1'],
        'CREATED_DATE': pd.to_datetime(['2026-01-01'], utc=False),
    })
    with pytest.raises(ValueError, match="tz-naive datetime"):
        snowflake_prefetch._assert_datetime_columns_tz_aware(
            df, dataset_name='unit-test', strict=True,
        )


def test_round30_l2_non_timestamp_columns_ignored() -> None:
    """Columns whose dtype is datetime64 but whose name does NOT match
    the canonical hints must be ignored.  This keeps the helper
    robust against test fixtures that accidentally type
    non-timestamp columns as datetime64."""
    df = pd.DataFrame({
        'ACCOUNT_ID_C': pd.to_datetime(
            ['2026-01-01', '2026-01-02'], utc=False,
        ),
    })
    # No raise even under strict mode -- the column name does not
    # match _DATETIME_COLUMN_HINTS.
    snowflake_prefetch._assert_datetime_columns_tz_aware(
        df, dataset_name='unit-test', strict=True,
    )


def test_round30_l2_alter_session_utc_issued_on_connect() -> None:
    """Source pin: ``adoptiq_backend`` must issue ``ALTER SESSION SET
    TIMEZONE='UTC'`` immediately after opening the Snowflake
    connection so the prefetch query never sees a session-local
    clock."""
    src = inspect.getsource(adoptiq_backend)
    assert "ALTER SESSION SET TIMEZONE = 'UTC'" in src, (
        "Round 30 / L2: adoptiq_backend must execute "
        "\"ALTER SESSION SET TIMEZONE = 'UTC'\" on connection setup "
        "so TIMESTAMP_NTZ columns are interpreted as UTC."
    )
