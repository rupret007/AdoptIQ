"""Round 170 — _json_safe must not use ambiguous pd.isna on ndarray/Series."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

import decision_report_delivery as delivery


def test_json_safe_ndarray_and_series_are_warning_free() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        ndarray_result = delivery._json_safe(np.array([1.0, np.nan]))  # noqa: SLF001
        series_result = delivery._json_safe(pd.Series([2, None]))  # noqa: SLF001
        index_result = delivery._json_safe(pd.Index([3, None]))  # noqa: SLF001
        empty_list = delivery._json_safe([])  # noqa: SLF001

    assert ndarray_result == [1.0, None]
    assert series_result == [2, None]
    assert index_result == [3, None]
    assert empty_list == []
    assert not any(issubclass(w.category, DeprecationWarning) for w in captured)
