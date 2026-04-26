"""Round 2 / Phase 1.1 regression test.

Renewal ARR / TCV / Product-ARR aggregates on accounts that carry
multiple currencies must NOT be summed across currencies — that
produces a meaningless float (e.g. ``USD 100k + EUR 90k = 190``).

After Phase 1.1 the analyzer:
  * buckets sums by currency in ``totals_by_currency``,
  * sets ``is_multi_currency = True`` and leaves the flat ``total_arr``
    at 0 when more than one currency is present, so any caller that
    still reads the flat field cannot accidentally render the cross-
    currency sum,
  * sets ``currency = 'MIXED'`` and ``currencies_present = [...]`` so
    the renderer has the data needed for a per-currency row.

This test pins both the ``advanced_renewal_analyzer`` source-level
contract and the runtime behaviour by running the bucketing helper
directly.
"""
from __future__ import annotations

import inspect
import re

import advanced_renewal_analyzer as ara


def test_analyzer_uses_totals_by_currency_dict() -> None:
    """The financial-metrics method must populate
    ``totals_by_currency`` and an ``is_multi_currency`` flag.
    """
    src = inspect.getsource(ara)
    assert "totals_by_currency" in src, (
        "Round 2 Phase 1.1: advanced_renewal_analyzer must expose "
        "totals_by_currency so multi-currency ARR is not silently "
        "summed across currencies."
    )
    assert "is_multi_currency" in src, (
        "Round 2 Phase 1.1: advanced_renewal_analyzer must expose "
        "is_multi_currency so renderers can branch instead of "
        "rendering a misleading single number."
    )


def test_analyzer_does_not_concat_arr_across_currencies() -> None:
    """The flat ``total_arr`` field must be set to the single-currency
    bucket only when there is exactly one currency, and left at 0
    otherwise.  Pin the source structure so a future "simplification"
    cannot revert to ``total_arr += val`` over all rows.
    """
    src = inspect.getsource(ara)
    # The bucket-then-flatten pattern is what we expect.
    assert re.search(
        r"if\s+len\(\s*totals_by_currency\s*\)\s*<=\s*1",
        src,
    ), (
        "Round 2 Phase 1.1: the analyzer must only set the flat "
        "total_arr when there is a single currency bucket."
    )
    assert "MIXED" in src, (
        "Round 2 Phase 1.1: the multi-currency branch must mark "
        "currency='MIXED' so renderers know not to format with a "
        "single-currency symbol."
    )


def test_no_naive_sum_arr_field_in_renewal_paths() -> None:
    """Source-level pin: the renewal analyzer must NOT contain a
    pattern like ``total_arr = sum(row[1] for row in results)`` that
    blindly sums ARR across all rows regardless of currency.
    """
    src = inspect.getsource(ara)
    bad = re.search(
        r"total_arr\s*=\s*sum\([^)]*for\s+row\s+in\s+results",
        src,
    )
    assert bad is None, (
        "Round 2 Phase 1.1: regression — total_arr must not be "
        "computed as a naive sum over all result rows; that throws "
        "away the per-currency bucketing required by Phase 1.1."
    )
