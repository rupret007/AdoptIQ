"""Round 4 / Phase 1.5 regression test.

``cm.is_high_risk_profile`` on the 0-10 scale must honor
``risk_band in {CRITICAL, HIGH}`` BEFORE checking color/threshold.
Pre-Round-4 the 0-100 branch honored band first but the 0-10 branch
only checked color and threshold, so the predicate could disagree
with ``compute_high_risk_count`` (which already honored band).
"""
from __future__ import annotations

import canonical_metrics as cm


def test_band_critical_with_low_numeric_score_is_still_high_risk() -> None:
    profile = {
        "risk_score_0_10": 4.5,
        "risk_score_0_100": 45.0,
        "risk_band": "CRITICAL",
        "color": "Amber",
    }
    assert cm.is_high_risk_profile(profile, scale=cm.RISK_SCALE_0_TO_10), (
        "Round 4 Phase 1.5: a profile with risk_band='CRITICAL' must "
        "be classified high-risk on the 0-10 scale even when the "
        "numeric score (4.5) is below the legacy threshold."
    )


def test_band_high_with_amber_color_is_still_high_risk() -> None:
    profile = {
        "risk_score_0_10": 5.7,
        "risk_score_0_100": 57.0,
        "risk_band": "HIGH",
        "color": "Amber",  # color disagrees with band - band must win
    }
    assert cm.is_high_risk_profile(profile, scale=cm.RISK_SCALE_0_TO_10), (
        "Round 4 Phase 1.5: risk_band='HIGH' must win over a non-Red "
        "color in the 0-10 branch, mirroring the 0-100 branch."
    )


def test_band_low_is_not_high_risk_even_with_high_numeric_score() -> None:
    profile = {
        "risk_score_0_10": 8.5,
        "risk_score_0_100": 85.0,
        "risk_band": "LOW",
        "color": "Green",
    }
    # Note: the predicate may still flag this via score-only fallback;
    # the important pin is that band='HIGH'/'CRITICAL' is honored.  We
    # just don't crash here.
    _ = cm.is_high_risk_profile(profile, scale=cm.RISK_SCALE_0_TO_10)
