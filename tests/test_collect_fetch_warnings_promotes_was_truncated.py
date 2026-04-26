"""Round 3 / Phase 2.8 regression test.

A DataFrame whose ``attrs['was_truncated']`` is set must produce a
warning entry from ``collect_fetch_warnings`` with kind=``truncation``.
Previously only ``fetch_error`` was promoted, so a query that
silently hit its LIMIT showed no banner and downstream totals
under-reported reality without disclosure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from snowflake_prefetch import collect_fetch_warnings


def test_truncated_dataframe_generates_warning():
    df = pd.DataFrame({"a": [1, 2, 3]})
    df.attrs["was_truncated"] = True
    df.attrs["fetch_limit"] = 100000
    df.attrs["rows_returned"] = 100000

    warnings = collect_fetch_warnings({"csone_cases": df})
    truncation_warnings = [w for w in warnings if w.get("kind") == "truncation"]
    assert truncation_warnings, "expected a truncation warning entry"
    w = truncation_warnings[0]
    assert "csone_cases" == w.get("dataset") or w.get("dataset") in {
        "csone_cases", df.attrs.get("fetch_error_dataset")
    }
    assert "truncated" in w.get("error", "").lower()
    assert "100000" in w.get("error", "")


def test_truncation_warning_for_dict_shaped_results():
    bundle = {
        "period_compare": {
            "was_truncated": True,
            "fetch_limit": 50000,
            "rows_returned": 50000,
        }
    }
    warnings = collect_fetch_warnings(bundle)
    assert any(
        w.get("kind") == "truncation" and w.get("dataset") == "period_compare"
        for w in warnings
    )


def test_no_truncation_warning_when_complete_result():
    df = pd.DataFrame({"a": [1, 2]})
    df.attrs["was_truncated"] = False
    warnings = collect_fetch_warnings({"foo": df})
    assert not any(w.get("kind") == "truncation" for w in warnings)
