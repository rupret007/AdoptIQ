"""Round 66 / Pass 2 (B10) — SUPPORT_CASES empty-for-scope warning + diag.

Pre-R66 ``fetch_support_cases_snowflake`` emitted a quiet ``logger.info``
when the SUPPORT_CASES Try 1 query returned 0 rows for the requested
manager+technology scope, then silently returned an empty DataFrame
with no ``fetch_error`` marker. Downstream readers (renewal narrative
gate, partial_data_warnings surface, Ask AI grounding briefing) had
no way to distinguish "0 cases happened in this window" from
"SUPPORT_CASES is empty for this manager+tech scope -- you have a
data plumbing degradation".

R66/B10 fixes this by:
1. Promoting the empty-result log line from ``info`` to ``warning``
   and including the account count + days window in the message.
2. Attaching ``fetch_error_kind = 'empty_for_scope'`` and
   ``fetch_error = '...'`` markers on the returned DataFrame's
   ``.attrs`` so consumers can render an honest "data unavailable"
   tristate instead of a misleading "0".

These tests pin the source-shape of the writer block and exercise
the simulated path through ``fetch_support_cases_snowflake``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def support_cases_block() -> str:
    """Load the SUPPORT_CASES Try 1 block from adoptiq_backend.py."""
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "adoptiq_backend.py").read_text(encoding="utf-8")
    start_marker = "# Try 1: SUPPORT_CASES with ACCOUNT_ID IN (...)"
    end_marker = "# Try 2: SUPPORT_CASES with ACCOUNT_ID_C"
    start_idx = text.index(start_marker)
    end_idx = text.index(end_marker, start_idx)
    return text[start_idx:end_idx]


def test_block_has_r66_b10_marker(support_cases_block: str) -> None:
    """R66/B10 source marker MUST be present so a future refactor flags here."""
    assert "Round 66 / Pass 2 (B10)" in support_cases_block, (
        "R66/B10 source marker missing from SUPPORT_CASES Try 1 block"
    )


def test_block_promotes_empty_result_log_to_warning(support_cases_block: str) -> None:
    """Empty-result log line MUST be ``logger.warning``, not ``logger.info``."""
    assert "logger.warning(" in support_cases_block, (
        "Empty-result log must be promoted to warning"
    )
    # Spot-check the warning message names accounts and days so the
    # operator can correlate with the requested scope.
    assert "accounts=%d" in support_cases_block
    assert "days=%d" in support_cases_block


def test_block_no_longer_uses_quiet_info_for_empty_result(support_cases_block: str) -> None:
    """The pre-R66 quiet ``info`` line MUST be gone."""
    pre_r66_line = 'logger.info(f"[[RENEWAL]] Snowflake SUPPORT_CASES returned 0 rows for scope")'
    assert pre_r66_line not in support_cases_block, (
        "Pre-R66 quiet info log line still present"
    )


def test_block_attaches_empty_for_scope_fetch_error_kind(support_cases_block: str) -> None:
    """The empty DataFrame MUST be tagged with ``fetch_error_kind = 'empty_for_scope'``."""
    assert "'empty_for_scope'" in support_cases_block, (
        "fetch_error_kind = 'empty_for_scope' marker missing"
    )
    assert "fetch_error_kind" in support_cases_block, (
        "fetch_error_kind attr not set"
    )


def test_block_attaches_scope_context_attrs(support_cases_block: str) -> None:
    """Scope context (account count, days) MUST be on the empty DataFrame's attrs."""
    assert "scope_account_count" in support_cases_block, (
        "scope_account_count attr missing"
    )
    assert "scope_window_days" in support_cases_block, (
        "scope_window_days attr missing"
    )


def test_block_still_returns_via_normalize_cases_df(support_cases_block: str) -> None:
    """The empty DataFrame MUST still go through ``_normalize_cases_df`` so it
    carries the canonical column schema and the ``was_truncated`` flag."""
    assert "_normalize_cases_df(pd.DataFrame())" in support_cases_block, (
        "Empty path must go through _normalize_cases_df for canonical schema"
    )


def test_simulated_empty_scope_attaches_diag(caplog: pytest.LogCaptureFixture) -> None:
    """End-to-end simulation: empty Try 1 result attaches diag attrs."""
    import adoptiq_backend
    from adoptiq_backend import fetch_support_cases_snowflake

    # Build a mock cursor that returns 0 rows for Try 1.
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_cursor.description = [
        ("CASE_ID",), ("ACCOUNT_ID",), ("SUBJECT",), ("STATUS",),
        ("CREATED_DATE",), ("CLOSED_DATE",), ("SEVERITY",),
    ]

    mock_ctx = MagicMock()
    mock_ctx.cursor.return_value = mock_cursor

    # Force the table-policy gate False so we exercise the empty-rows
    # branch (which is the path B10 is about).
    with patch.object(adoptiq_backend, 'is_table_blocked', return_value=False), \
         patch.object(adoptiq_backend, '_get_table_columns', return_value=set()), \
         caplog.at_level(logging.WARNING, logger=adoptiq_backend.logger.name):
        result = fetch_support_cases_snowflake(
            mock_ctx,
            account_ids=["ACC001", "ACC002", "ACC003"],
            days=90,
        )

    # The returned frame MUST carry the empty_for_scope diag.
    assert isinstance(result, pd.DataFrame)
    assert result.empty, "Result frame must be empty"
    assert result.attrs.get('fetch_error_kind') == 'empty_for_scope', (
        f"Expected empty_for_scope marker, got: {result.attrs.get('fetch_error_kind')!r}"
    )
    assert 'fetch_error' in result.attrs
    assert result.attrs.get('scope_account_count') == 3
    assert result.attrs.get('scope_window_days') == 90

    # The warning MUST have fired.
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "0 rows for scope" in msg and "accounts=3" in msg and "days=90" in msg
        for msg in warning_messages
    ), f"Expected R66/B10 warning, got: {warning_messages}"


def test_simulated_non_empty_path_does_not_attach_empty_for_scope_diag() -> None:
    """When SUPPORT_CASES returns rows, the empty-for-scope diag MUST NOT be set."""
    import adoptiq_backend
    from adoptiq_backend import fetch_support_cases_snowflake

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        ("C001", "ACC001", "Subject 1", "Open", "2026-01-01", None, "P3"),
    ]
    mock_cursor.description = [
        ("CASE_ID",), ("ACCOUNT_ID",), ("SUBJECT",), ("STATUS",),
        ("CREATED_DATE",), ("CLOSED_DATE",), ("SEVERITY",),
    ]

    mock_ctx = MagicMock()
    mock_ctx.cursor.return_value = mock_cursor

    with patch.object(adoptiq_backend, 'is_table_blocked', return_value=False), \
         patch.object(adoptiq_backend, '_get_table_columns', return_value=set()):
        result = fetch_support_cases_snowflake(
            mock_ctx,
            account_ids=["ACC001"],
            days=90,
        )

    assert isinstance(result, pd.DataFrame)
    assert not result.empty, "Result frame must NOT be empty"
    # Non-empty path must NOT set the empty-for-scope marker.
    assert result.attrs.get('fetch_error_kind') != 'empty_for_scope', (
        "Non-empty result must not carry empty_for_scope diag"
    )


def test_simulated_empty_scope_warning_log_message_format(caplog: pytest.LogCaptureFixture) -> None:
    """The R66/B10 warning message MUST follow the documented format."""
    import adoptiq_backend
    from adoptiq_backend import fetch_support_cases_snowflake

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_cursor.description = [("CASE_ID",), ("ACCOUNT_ID",)]
    mock_ctx = MagicMock()
    mock_ctx.cursor.return_value = mock_cursor

    with patch.object(adoptiq_backend, 'is_table_blocked', return_value=False), \
         patch.object(adoptiq_backend, '_get_table_columns', return_value=set()), \
         caplog.at_level(logging.WARNING, logger=adoptiq_backend.logger.name):
        _ = fetch_support_cases_snowflake(
            mock_ctx,
            account_ids=["ACC001"] * 17,  # 17 dupes → after dedupe = 1.
            days=30,
        )

    # Note: list of duplicates dedupes to 1; we want to verify the
    # warning's account count reflects POST-dedupe count.
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "accounts=1" in msg and "days=30" in msg
        for msg in warning_messages
    ), f"Warning must reflect post-dedupe count, got: {warning_messages}"


def test_simulated_empty_scope_with_unique_accounts(caplog: pytest.LogCaptureFixture) -> None:
    """Verify the warning reports the correct account count for unique IDs."""
    import adoptiq_backend
    from adoptiq_backend import fetch_support_cases_snowflake

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_cursor.description = [("CASE_ID",), ("ACCOUNT_ID",)]
    mock_ctx = MagicMock()
    mock_ctx.cursor.return_value = mock_cursor

    with patch.object(adoptiq_backend, 'is_table_blocked', return_value=False), \
         patch.object(adoptiq_backend, '_get_table_columns', return_value=set()), \
         caplog.at_level(logging.WARNING, logger=adoptiq_backend.logger.name):
        _ = fetch_support_cases_snowflake(
            mock_ctx,
            account_ids=["ACC001", "ACC002", "ACC003", "ACC004", "ACC005"],
            days=180,
        )

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "accounts=5" in msg and "days=180" in msg
        for msg in warning_messages
    ), f"Warning must report 5 accounts / 180 days, got: {warning_messages}"
