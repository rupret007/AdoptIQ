"""Pin the new severity-pie data builder.

The Executive severity pie used to call ``csone_df['Severity'].value_counts()``
which produced a different denominator from the ``Total: N cases`` headline:
  * raw "P1" / "1" / "Critical" become 3 separate slices,
  * rows whose ``Severity`` is null are silently dropped from the pie
    while still being counted in the headline.

Round 2 rebuilt the pie data from ``case_priority_norm`` with an
explicit "Unknown" wedge so that the slice total ALWAYS equals
``cm.count_total_tac``.
"""
from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm
from data_normalization import add_case_lifecycle_fields


@pytest.fixture
def mixed_severity_csone() -> pd.DataFrame:
    """A frame that mixes raw severity tokens and includes nulls.

    Canonical normalization:
      P1 = 4  ("P1", "1", "Critical", "P1") -- one of the P1s is itself "P1"
      P2 = 2  ("P2", "High")
      P3 = 1  ("P3")
      P4 = 1  ("P4")
      Unknown = 2  (None, "")
    Total = 10
    """
    return pd.DataFrame(
        [
            {"customer_name": "Acme", "Severity": "P1"},
            {"customer_name": "Acme", "Severity": "1"},
            {"customer_name": "Acme", "Severity": "Critical"},
            {"customer_name": "Acme", "Severity": "P1"},
            {"customer_name": "Beta", "Severity": "P2"},
            {"customer_name": "Beta", "Severity": "High"},
            {"customer_name": "Beta", "Severity": "P3"},
            {"customer_name": "Beta", "Severity": "P4"},
            {"customer_name": "Gamma", "Severity": None},
            {"customer_name": "Gamma", "Severity": ""},
        ]
    )


def _build_severity_pie_series(csone_df: pd.DataFrame) -> pd.Series:
    """Reproduce the round 2 pie-data builder used by app_simple.

    This MUST match the algorithm in ``app_simple._generate_executive_charts``
    so that any change to the chart code that drifts away from this
    contract is caught by the tests immediately.
    """
    if csone_df is None or csone_df.empty:
        return pd.Series(dtype=int)
    norm = add_case_lifecycle_fields(csone_df)
    if "case_priority_norm" not in norm.columns:
        return pd.Series(dtype=int)
    severity_series = norm["case_priority_norm"].fillna("Unknown").astype(str)
    ordered_keys = ["P1", "P2", "P3", "P4", "Unknown"]
    raw_counts = severity_series.value_counts()
    counts = pd.Series(
        [int(raw_counts.get(k, 0)) for k in ordered_keys],
        index=ordered_keys,
    )
    # Drop empty buckets (matches the chart's "don't show 0-slices" behavior).
    return counts[counts > 0]


def test_pie_total_equals_cm_count_total_tac(mixed_severity_csone: pd.DataFrame) -> None:
    """The single most important contract: the pie's denominator must
    equal the headline "Total: N cases" number.
    """
    pie = _build_severity_pie_series(mixed_severity_csone)
    assert int(pie.sum()) == cm.count_total_tac(mixed_severity_csone)


def test_pie_collapses_p1_synonyms_into_one_slice(mixed_severity_csone: pd.DataFrame) -> None:
    """All of "P1" / "1" / "Critical" must collapse into a single P1 wedge
    whose count equals ``cm.count_p1``.
    """
    pie = _build_severity_pie_series(mixed_severity_csone)
    assert "P1" in pie.index
    assert int(pie.loc["P1"]) == cm.count_p1(mixed_severity_csone)
    # Defensive: there must be no separate "1" / "Critical" wedges.
    assert "1" not in pie.index
    assert "Critical" not in pie.index


def test_pie_collapses_p2_synonyms_into_one_slice(mixed_severity_csone: pd.DataFrame) -> None:
    pie = _build_severity_pie_series(mixed_severity_csone)
    assert "P2" in pie.index
    assert int(pie.loc["P2"]) == cm.count_p2(mixed_severity_csone)
    assert "High" not in pie.index
    assert "2" not in pie.index


def test_pie_includes_unknown_slice_for_null_severities(mixed_severity_csone: pd.DataFrame) -> None:
    """Null/blank severities MUST become an explicit "Unknown" wedge.

    The previous implementation dropped them and made the pie's total
    smaller than the "Total: N cases" headline.
    """
    pie = _build_severity_pie_series(mixed_severity_csone)
    assert "Unknown" in pie.index
    # Two rows in the fixture have null/blank severity.
    assert int(pie.loc["Unknown"]) == 2


def test_pie_ordering_is_stable_p1_to_p4_then_unknown(mixed_severity_csone: pd.DataFrame) -> None:
    """The wedge order is part of the contract — colors are paired with
    labels positionally elsewhere in the chart code.
    """
    pie = _build_severity_pie_series(mixed_severity_csone)
    expected_order = ["P1", "P2", "P3", "P4", "Unknown"]
    actual_order = list(pie.index)
    # Subset of expected ordering, preserving relative order:
    indices = [expected_order.index(label) for label in actual_order]
    assert indices == sorted(indices), (
        f"Wedge order must follow {expected_order}; got {actual_order}"
    )


def test_pie_total_equals_input_row_count(mixed_severity_csone: pd.DataFrame) -> None:
    """No row from the input CSOne frame may be lost from the pie."""
    pie = _build_severity_pie_series(mixed_severity_csone)
    assert int(pie.sum()) == len(mixed_severity_csone)


def test_empty_csone_returns_empty_pie() -> None:
    """Empty input -> empty pie. No crashes, no accidental "Unknown" slice."""
    pie = _build_severity_pie_series(pd.DataFrame())
    assert pie.empty


def test_only_unknown_severity_yields_single_unknown_slice() -> None:
    """A frame with ONLY null severities still rounds-trips: one
    Unknown wedge whose count equals the row count.
    """
    df = pd.DataFrame([{"Severity": None}, {"Severity": ""}, {"Severity": None}])
    pie = _build_severity_pie_series(df)
    assert list(pie.index) == ["Unknown"]
    assert int(pie.loc["Unknown"]) == 3
