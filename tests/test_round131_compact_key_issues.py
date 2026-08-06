"""Round 131 / Build 100 — Compact high-risk Key Issues column (F1)."""

from __future__ import annotations
from source_shape_utils import assert_in_source

import inspect

import pandas as pd

from compact_report_formatter import calculate_renewal_risk_scores
from executive_intelligence_formatter import (
    ExecutiveIntelligenceFormatter,
    _r131_format_high_risk_key_issues,
)


def test_r131_helper_lists_activity_counts_when_present() -> None:
    text = _r131_format_high_risk_key_issues(
        {
            "ab_count": 2,
            "case_count": 3,
            "pulse_count": 1,
            "ap_count": 4,
        }
    )
    assert text == "2 barriers, 3 cases, 1 pulse, 4 action plans"


def test_r131_helper_uses_risk_factor_when_counts_zero() -> None:
    text = _r131_format_high_risk_key_issues(
        {
            "ab_count": 0,
            "case_count": 0,
            "risk_factors": [
                "3 active service incident(s) in scope [Source: AdoptIQ external_intelligence]"
            ],
            "risk_band": "HIGH",
        }
    )
    assert "incident" in text.lower()
    assert "[Source:" not in text


def test_r131_helper_band_fallback_when_no_factors() -> None:
    text = _r131_format_high_risk_key_issues(
        {"ab_count": 0, "case_count": 0, "risk_band": "HIGH"}
    )
    assert "elevated high band" in text.lower()
    assert text != "N/A"


def test_r131_calculate_renewal_risk_scores_emits_activity_counts() -> None:
    ab = pd.DataFrame(
        {
            "customer_name": ["Acme Corp", "Acme Corp"],
            "AB_STATUS_C": ["Open", "Open"],
        }
    )
    csone = pd.DataFrame({"customer_name": ["Acme Corp"], "case_id": ["C1"]})
    scores = calculate_renewal_risk_scores(ab, csone, recent_window_days=90)
    acme_key = next(k for k in scores if "acme" in k.lower())
    row = scores[acme_key]
    assert row["ab_count"] == 2
    assert row["case_count"] == 1
    assert "pulse_count" in row
    assert "ap_count" in row


def test_r131_add_risk_analysis_section_uses_helper_not_na() -> None:
    src = inspect.getsource(ExecutiveIntelligenceFormatter.add_risk_analysis_section)
    assert_in_source(src, "_r131_format_high_risk_key_issues", label='src')
    assert "else 'N/A'" not in src.split("_r131_format_high_risk_key_issues")[1][:200]


def test_r131_source_markers_present() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert "Round 131" in (root / "executive_intelligence_formatter.py").read_text(encoding="utf-8")
    assert "Round 131" in (root / "compact_report_formatter.py").read_text(encoding="utf-8")
