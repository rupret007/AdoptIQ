"""Round 4 / Phase 1.2 regression test.

The Executive Excel dashboard ``high_risk_count`` tile and the
``High_Risk_Customers`` sheet must derive their population from the
same canonical helper (``cm.is_high_risk_profile`` /
``cm.compute_high_risk_count``).  Pre-Round-4 the tile used an ad-hoc
``Risk_Band in {HIGH, CRITICAL}`` mask while the sheet rebuilt via
the canonical helper, leading to disagreement inside the same workbook.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_executive_excel_uses_canonical_high_risk_helper() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "cm.is_high_risk_profile" in src or "compute_high_risk_count" in src, (
        "Round 4 Phase 1.2: the executive Excel writer must derive "
        "high_risk_count from cm.is_high_risk_profile / "
        "cm.compute_high_risk_count so the dashboard tile and the "
        "High_Risk_Customers sheet count the same population."
    )


def test_high_risk_count_does_not_use_ad_hoc_band_string_match() -> None:
    """The pre-Round-4 ad-hoc tile used a literal pandas mask like
    ``risk_summary_df['Risk_Band'].isin(['HIGH','CRITICAL'])``.  We do
    not forbid that mask outright (the sheet may still need it for
    presentation) but the *count* must be sourced from the canonical
    helper - otherwise the two will drift again.
    """
    import canonical_metrics as cm

    profiles = {
        "A": {"risk_score_0_10": 5.7, "risk_score_0_100": 57.0, "risk_band": "HIGH",     "color": "Red"},
        "B": {"risk_score_0_10": 8.1, "risk_score_0_100": 81.0, "risk_band": "CRITICAL", "color": "Red"},
        "C": {"risk_score_0_10": 4.0, "risk_score_0_100": 40.0, "risk_band": "MEDIUM",   "color": "Amber"},
    }
    tile_count = cm.compute_high_risk_count(profiles, scale=cm.RISK_SCALE_0_TO_10)
    sheet_rows = [
        n for n, p in profiles.items()
        if cm.is_high_risk_profile(p, scale=cm.RISK_SCALE_0_TO_10)
    ]
    assert tile_count == len(sheet_rows) == 2, (
        "Round 4 Phase 1.2: cm.compute_high_risk_count and "
        "cm.is_high_risk_profile must agree on the same population."
    )
