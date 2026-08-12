"""Round 166 Compact adapter reconciles zero Source_State with real rows."""

from __future__ import annotations

import pandas as pd

import canonical_report_adapter as adapter


def test_strip_placeholder_rows_reconciles_zero_state_with_usable_rows() -> None:
    frame = pd.DataFrame(
        [
            {"Customer": "Acme Corp", "Overall_Risk_Score": 4.2, "Risk_Band": "LOW"},
        ]
    )
    result = adapter._strip_placeholder_rows(
        frame,
        declared_state="zero",
        declared_detail="successful source returned zero records",
    )
    assert not result.empty
    assert result.attrs.get("partial") is True
    assert "Round 166" in str(result.attrs.get("source_mode_detail") or "")


def test_strip_placeholder_rows_keeps_true_zero_when_empty() -> None:
    frame = pd.DataFrame(columns=["Customer", "Overall_Risk_Score"])
    result = adapter._strip_placeholder_rows(
        frame,
        declared_state="zero",
        declared_detail="successful source returned zero records",
    )
    assert result.empty
    assert result.attrs.get("partial") is not True


def test_zero_declared_with_real_row_does_not_raise() -> None:
    frame = pd.DataFrame(
        [{"Customer": "Real Customer", "Overall_Risk_Score": 7.1, "Risk_Band": "HIGH"}]
    )
    result = adapter._strip_placeholder_rows(
        frame,
        declared_state="zero",
        declared_detail="Legacy Report_Info: Source_State:Subscriptions=Zero",
    )
    assert not result.empty
    assert result.attrs.get("partial") is True
