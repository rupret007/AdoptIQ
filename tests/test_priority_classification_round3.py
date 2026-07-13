"""Round 3 regression: pin canonical priority classification on a
CSOne frame whose only severity column is ``SEVERITY`` (all caps), no
``Severity`` / ``Case_Priority`` lowercase variants.

Before round 3 hardening, several call sites in ``app_simple.py`` gated
on ``severity_col in ['Severity','severity','Priority','priority',
'Case_Priority']`` and silently returned 0 P1/P2 for frames that only
exposed the all-caps ``SEVERITY`` column, even though the canonical
``cm.count_p1`` returned the correct count for the same frame.
"""
from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm
from data_normalization import add_case_lifecycle_fields, normalize_priority_label


@pytest.fixture
def all_caps_severity_only_csone() -> pd.DataFrame:
    """A TAC frame whose only severity column is `SEVERITY` (uppercase).
    Includes 2 P1, 1 P2, 1 P3, 1 unknown.
    """
    return pd.DataFrame({
        "Customer Name": ["Acme", "Acme", "Beta", "Gamma", "Delta"],
        "SEVERITY":      ["P1",   "1",    "P2",   "P3",    "Unknown"],
    })


def test_count_p1_works_on_all_caps_severity(all_caps_severity_only_csone):
    """`cm.count_p1` MUST recognize "P1" and "1" via canonical
    normalization regardless of whether the column is named ``Severity``
    (mixed case) or ``SEVERITY`` (all caps)."""
    n = cm.count_p1(all_caps_severity_only_csone)
    assert n == 2, f"Expected 2 P1 cases, got {n}"


def test_count_p2_works_on_all_caps_severity(all_caps_severity_only_csone):
    n = cm.count_p2(all_caps_severity_only_csone)
    assert n == 1, f"Expected 1 P2 case, got {n}"


def test_priority_breakdown_buckets(all_caps_severity_only_csone):
    """P3 + Unknown buckets must be populated correctly when only the
    uppercase column exists.
    """
    buckets = cm.count_priority_breakdown(all_caps_severity_only_csone)
    assert buckets["P1"] == 2
    assert buckets["P2"] == 1
    assert buckets["P3"] == 1
    assert buckets["Unknown"] == 1


def test_legacy_lowercase_only_gate_would_have_returned_zero(
    all_caps_severity_only_csone,
):
    """Documents the bug the round 3 fix removed: the previous
    ``severity_col in ['Severity','severity','Priority','priority',
    'Case_Priority']`` gate did NOT include ``SEVERITY`` (all caps), so
    p1_count silently returned 0 for the same frame where canonical
    ``cm.count_p1`` returns 2.
    """
    df = all_caps_severity_only_csone
    legacy_severity_cols = ["Severity", "severity", "Priority", "priority", "Case_Priority"]
    legacy_match = next((c for c in legacy_severity_cols if c in df.columns), None)
    assert legacy_match is None, (
        "Test fixture would not exercise the bug if the legacy gate "
        "matched any column."
    )
    assert cm.count_p1(df) > 0, (
        "Canonical count_p1 must return >0 on this fixture, otherwise "
        "the regression test does not actually pin the fix."
    )


def test_canonical_round_trip_via_lifecycle_fields(all_caps_severity_only_csone):
    """`add_case_lifecycle_fields` must surface ``case_priority_norm``
    that round-trips with `normalize_priority_label` so downstream
    builders (Word severity table, renewal Panel 3 pie, fallback
    insights) see consistent values.
    """
    enriched = add_case_lifecycle_fields(all_caps_severity_only_csone)
    assert "case_priority_norm" in enriched.columns
    expected = all_caps_severity_only_csone["SEVERITY"].map(normalize_priority_label)
    pd.testing.assert_series_equal(
        enriched["case_priority_norm"].reset_index(drop=True),
        expected.rename("case_priority_norm").reset_index(drop=True),
        check_names=True,
    )
