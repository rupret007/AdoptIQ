"""Round 30 / M5 — enforce the ARR-frame ``attrs`` contract.

Every DataFrame that reaches an ARR-consuming function must have
been routed through ``_normalize_arr_df`` so the following keys are
stamped on ``DataFrame.attrs``:

- ``is_multi_currency`` (bool)
- ``currencies_present`` (sorted list of currency codes)

Without the contract, multi-currency disclosures silently degrade to
single-currency rendering whenever an upstream caller forgets the
normalization step.
"""

from __future__ import annotations

import logging
import os

import pandas as pd
import pytest

import adoptiq_backend


def test_round30_m5_assert_arr_attrs_helper_exists() -> None:
    """The ``_assert_arr_attrs`` helper must exist as a public-name
    backend symbol so callers can import it."""
    assert hasattr(adoptiq_backend, '_assert_arr_attrs'), (
        "Round 30 / M5: adoptiq_backend must export _assert_arr_attrs "
        "for ARR consumers to enforce the attrs contract."
    )


def test_round30_m5_empty_or_none_frame_is_a_noop() -> None:
    """Empty / None frames are tolerated.  Legacy placeholder frames
    built via ``pd.DataFrame()`` outside the normalizer must keep
    working unchanged."""
    # None
    adoptiq_backend._assert_arr_attrs(
        None, function_name='test', strict=True,
    )
    # Explicitly empty
    adoptiq_backend._assert_arr_attrs(
        pd.DataFrame(), function_name='test', strict=True,
    )


def test_round30_m5_missing_attrs_warns_by_default(caplog: pytest.LogCaptureFixture) -> None:
    """Default behaviour is warn-by-default so the contract can be
    observed for one verification cycle before flipping to strict.
    The warning MUST carry the ``arr.attrs.missing`` event key so log
    aggregators can detect drift."""
    df = pd.DataFrame({
        'ACCOUNT_ID_C': ['acct-1'],
        'ANNUAL_CONTRACT_VALUE': [100.0],
    })
    # Round 30 / M5 contract: empty attrs => warn, do not raise.
    # Make sure the env var is unset so default is warn.
    prev = os.environ.pop('ADOPTIQ_STRICT_MODE', None)
    try:
        with caplog.at_level(logging.WARNING, logger='adoptiq_backend'):
            adoptiq_backend._assert_arr_attrs(
                df, function_name='test_round30_m5',
            )
    finally:
        if prev is not None:
            os.environ['ADOPTIQ_STRICT_MODE'] = prev
    matched = [
        r for r in caplog.records
        if 'arr.attrs.missing' in r.getMessage()
    ]
    assert matched, (
        "Round 30 / M5: missing attrs must emit a structured WARNING "
        "with the literal event key 'arr.attrs.missing'."
    )


def test_round30_m5_strict_mode_raises_value_error() -> None:
    """When ``strict=True`` the helper raises ``ValueError`` so CI
    gates can pin the contract."""
    df = pd.DataFrame({
        'ACCOUNT_ID_C': ['acct-1'],
        'ANNUAL_CONTRACT_VALUE': [100.0],
    })
    with pytest.raises(ValueError, match="missing attrs"):
        adoptiq_backend._assert_arr_attrs(
            df, function_name='test_round30_m5_strict', strict=True,
        )


def test_round30_m5_attrs_stamped_frame_passes() -> None:
    """A frame whose ``attrs`` carry the contract keys must satisfy
    the strict-mode check.  ``_normalize_arr_df`` is a closure inside
    ``fetch_subscription_data`` (the helper that calls into Snowflake),
    so we synthesise the post-normalization shape here -- the contract
    we're pinning is the ``attrs`` shape, not the closure itself."""
    df = pd.DataFrame({
        'ACCOUNT_ID_C': ['acct-1', 'acct-2'],
        'ANNUAL_CONTRACT_VALUE': [100.0, 200.0],
        'CURRENCY_CODE': ['USD', 'USD'],
    })
    df.attrs['is_multi_currency'] = False
    df.attrs['currencies_present'] = ['USD']
    # Strict mode must accept a properly-stamped frame.
    adoptiq_backend._assert_arr_attrs(
        df, function_name='test', strict=True,
    )


def test_round30_m5_normalize_arr_df_docstring_documents_contract() -> None:
    """Source pin: ``_normalize_arr_df`` (the closure that stamps the
    attrs) MUST document the post-normalization contract so callers
    know what keys to expect on ``attrs``."""
    import inspect
    src = inspect.getsource(adoptiq_backend)
    # Locate the closure and grab a reasonable slice of its body.
    idx = src.find('def _normalize_arr_df(df: pd.DataFrame) -> pd.DataFrame:')
    assert idx != -1, (
        "Round 30 / M5: _normalize_arr_df closure must exist in "
        "adoptiq_backend (used by fetch_subscription_data to stamp the "
        "attrs contract)."
    )
    # Look at the next ~2500 chars (covers the docstring + body).
    snippet = src[idx:idx + 2500]
    assert "is_multi_currency" in snippet, (
        "Round 30 / M5: _normalize_arr_df docstring/body must document "
        "the is_multi_currency contract."
    )
    assert "currencies_present" in snippet, (
        "Round 30 / M5: _normalize_arr_df docstring/body must document "
        "the currencies_present contract."
    )
    assert "_assert_arr_attrs" in snippet, (
        "Round 30 / M5: _normalize_arr_df docstring must reference "
        "_assert_arr_attrs as the enforcement helper."
    )
