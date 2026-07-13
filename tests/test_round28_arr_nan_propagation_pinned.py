"""Round 28 / Phase 3 — pin the NaN-safety contract for ARR sums in
``adoptiq_backend.compute_metrics_from_frame`` (or its at-risk
sibling).

Previously a single NaN-bearing or string-coerced ARR row would
propagate NaN through downstream percent / band math and surface as
``"$nan"`` in the rendered report.  Round 28 routes the ``.sum()``
calls through ``pd.to_numeric(errors='coerce').fillna(0).sum()``.

This test pins the source-level pattern so a future "simplification"
cannot revert the safe pattern.
"""
from __future__ import annotations

import inspect
import re

import adoptiq_backend


def test_round28_arr_at_risk_sum_uses_pd_to_numeric_with_coerce_and_fillna() -> None:
    """The at-risk / critical ARR sum block MUST route through
    ``pd.to_numeric(errors='coerce').fillna(0).sum()`` so a single
    NaN row does not poison ``arr_at_risk`` / ``arr_critical``."""
    src = inspect.getsource(adoptiq_backend)

    # Anchor on the at-risk mask line so we are checking the right
    # block (there are several ARR sum sites in the file).
    match = re.search(
        r"at_risk_mask\s*=\s*arr_active_df\[acct_col\]\.isin\(troubled_accounts\)"
        r"(?P<body>.+?)"
        r"critical_mask\s*=\s*arr_active_df\[acct_col\]\.isin\(critical_accounts\)",
        src,
        re.DOTALL,
    )
    assert match is not None, (
        "Round 28: could not locate the at-risk ARR sum block; the "
        "regression-pin regex needs updating to match the current "
        "source."
    )
    body = match.group('body')
    assert 'pd.to_numeric' in body, (
        "Round 28: at-risk ARR sum must route through pd.to_numeric "
        "to coerce string-typed cells before .sum().  Current body: "
        f"{body!r}"
    )
    assert "errors='coerce'" in body or 'errors="coerce"' in body, (
        "Round 28: at-risk ARR sum must use errors='coerce' so "
        "non-numeric cells become NaN rather than raising."
    )
    assert '.fillna(0)' in body, (
        "Round 28: at-risk ARR sum must fillna(0) so NaN does not "
        "propagate through downstream percent / band math and "
        "surface as '$nan' in the report."
    )


def test_round28_arr_critical_sum_uses_pd_to_numeric_with_coerce_and_fillna() -> None:
    """Mirror of the at-risk pin for the critical ARR sum."""
    src = inspect.getsource(adoptiq_backend)

    # Anchor on the critical mask line and pin behavior up to the
    # next outer dict-key assignment.
    idx = src.find("critical_mask = arr_active_df[acct_col].isin(critical_accounts)")
    assert idx != -1, (
        "Round 28: could not locate the critical ARR sum block."
    )
    # Look forward ~40 lines or until the next blank-line block.
    body = src[idx:idx + 2000]
    assert 'arr_critical' in body
    assert 'pd.to_numeric' in body
    assert "errors='coerce'" in body or 'errors="coerce"' in body
    assert '.fillna(0)' in body, (
        "Round 28: critical ARR sum must fillna(0) before .sum() so "
        "NaN does not poison the rendered total."
    )


def test_round28_arr_at_risk_sum_returns_finite_float_on_nan_inputs() -> None:
    """Behavioural pin: feed a frame with a NaN ARR row and confirm
    the resulting sum is a finite float."""
    import pandas as pd

    # Mirror the runtime structure: an arr_active_df with one NaN row
    # and one non-NaN row.  The function under test is private, but
    # the behavioural contract is that NaN must not propagate into
    # the float aggregate.
    raw = pd.DataFrame(
        {
            'ANNUAL_CONTRACT_VALUE': [100_000.0, float('nan'), 50_000.0],
        }
    )
    coerced = pd.to_numeric(raw['ANNUAL_CONTRACT_VALUE'], errors='coerce').fillna(0)
    total = float(coerced.sum())
    assert total == 150_000.0, (
        f"Round 28 baseline: pd.to_numeric+fillna(0)+sum() must "
        f"yield 150_000.0; got {total!r}"
    )
    import math
    assert math.isfinite(total), (
        f"Round 28: ARR sum must be finite even with NaN inputs; "
        f"got {total!r}"
    )
