"""Round 2 / Phase 1.5 high-risk definition parity test.

Both the ``High_Risk_Customers`` Excel sheet and the executive
``high_risk_count`` tile must agree because they both go through
``cm.is_high_risk_profile``.

This test feeds a synthetic profile collection that historically
caused drift (a band=HIGH profile with a 0-10 score below 6) and
asserts both the predicate and the canonical counter classify it as
high-risk.
"""
from __future__ import annotations

import canonical_metrics as cm


def test_band_high_below_legacy_six_is_high_risk_excel_predicate() -> None:
    profile = {
        "risk_score_0_10": 5.7,
        "risk_score_0_100": 57.0,
        "risk_band": "HIGH",
        "color": "Red",
    }
    assert cm.is_high_risk_profile(profile, scale=cm.RISK_SCALE_0_TO_10), (
        "Excel High_Risk_Customers sheet rows must include band=HIGH "
        "profiles even when their 0-10 score (5.7) is below the "
        "legacy >= 6 ad-hoc cutoff."
    )


def test_dashboard_tile_count_matches_excel_row_count() -> None:
    profiles = {
        "Acme":  {"risk_score_0_10": 5.7, "risk_score_0_100": 57.0, "risk_band": "HIGH",     "color": "Red"},
        "Beta":  {"risk_score_0_10": 8.1, "risk_score_0_100": 81.0, "risk_band": "CRITICAL", "color": "Red"},
        "Gamma": {"risk_score_0_10": 4.0, "risk_score_0_100": 40.0, "risk_band": "MEDIUM",   "color": "Amber"},
        "Delta": {"risk_score_0_10": 1.0, "risk_score_0_100": 10.0, "risk_band": "LOW",      "color": "Green"},
    }
    excel_rows = [
        name for name, prof in profiles.items()
        if cm.is_high_risk_profile(prof, scale=cm.RISK_SCALE_0_TO_10)
    ]
    tile = cm.compute_high_risk_count(profiles, scale=cm.RISK_SCALE_0_TO_10)
    assert len(excel_rows) == tile == 2, (
        "Executive dashboard high_risk_count tile must equal the row "
        f"count of the High_Risk_Customers sheet. Got Excel={len(excel_rows)} "
        f"({excel_rows}), tile={tile}."
    )
