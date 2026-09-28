"""Ratio conversion must not introduce non-finite report percentages."""
import sys

import pytest

from report_utils import format_ratio_percent


@pytest.mark.parametrize("value", [sys.float_info.max, -sys.float_info.max, "1e308", "-1e308"])
def test_ratio_scaling_overflow_returns_na(value):
    assert format_ratio_percent(value) == "N/A"


@pytest.mark.parametrize(
    "value, decimals, expected",
    [(0, 1, "0.0%"), (0.42, 1, "42.0%"), (-0.125, 2, "-12.50%"),
     (1.5, 0, "150%"), ("0.125", 2, "12.50%"),
     (None, 1, "N/A"), (float("nan"), 1, "N/A"),
     (float("inf"), 1, "N/A"), (-float("inf"), 1, "N/A")],
)
def test_ratio_formatting_preserves_existing_behavior(value, decimals, expected):
    assert format_ratio_percent(value, decimals=decimals) == expected
