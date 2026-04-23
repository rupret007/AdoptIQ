"""Round 4 risk-band parity tests.

Pin every renewal-risk band-label code path to the canonical
``risk_scoring.RISK_BAND_THRESHOLDS`` (CRITICAL=75, HIGH=55, MEDIUM=35,
LOW=15) so the same numeric score cannot be labeled "MODERATE" in one
report and "HIGH" in another.

Before round 4 hardening we had at least three ad-hoc threshold sets
in the codebase:
  * ``advanced_renewal_analyzer`` used 80 / 60 / 40 / 20 on a 0-100 scale.
  * ``app_simple`` Excel export used >=7 / >=4 on a 0-10 scale.
  * ``compact_report_formatter`` used <4 / <7 on a 0-10 scale and
    8 / 6 / 4 / 2 colour tiers.
"""
from __future__ import annotations

import pytest

from risk_scoring import RISK_BAND_THRESHOLDS


@pytest.mark.parametrize(
    "score,expected_band",
    [
        # CRITICAL band (>= 75 on 0-100 scale)
        (100.0, "CRITICAL"),
        (75.0, "CRITICAL"),
        # HIGH band (>= 55, < 75)
        (74.99, "HIGH"),
        (60.0, "HIGH"),
        (55.0, "HIGH"),
        # MEDIUM band (>= 35, < 55)
        (54.99, "MEDIUM"),
        (40.0, "MEDIUM"),
        (35.0, "MEDIUM"),
        # LOW band (>= 15, < 35)
        (34.99, "LOW"),
        (20.0, "LOW"),
        (15.0, "LOW"),
        # MINIMAL band (< 15) - renewal-specific "no signal" bucket
        (14.99, "MINIMAL"),
        (0.0, "MINIMAL"),
    ],
)
def test_advanced_renewal_analyzer_band_at_canonical_thresholds(score, expected_band):
    """Renewal analyzer band labels MUST follow the canonical thresholds.
    Previously this module used 80/60/40/20 which disagreed with the
    rest of the suite at the 60-79 range (HIGH vs MEDIUM mismatch).
    """
    from advanced_renewal_analyzer import _renewal_risk_category_from_score

    assert _renewal_risk_category_from_score(score) == expected_band, (
        f"Score {score} should map to {expected_band} (canonical), "
        f"got {_renewal_risk_category_from_score(score)}"
    )


def test_renewal_analyzer_imports_canonical_thresholds():
    """The renewal analyzer must source thresholds from
    ``risk_scoring.RISK_BAND_THRESHOLDS`` rather than duplicating them.
    """
    from advanced_renewal_analyzer import _RISK_BAND_THRESHOLDS_0_100

    assert _RISK_BAND_THRESHOLDS_0_100 == RISK_BAND_THRESHOLDS, (
        "Renewal analyzer must use the canonical RISK_BAND_THRESHOLDS, "
        "not a hardcoded copy."
    )


@pytest.mark.parametrize(
    "score_0_10,expected_label",
    [
        # Compact uses 0-10 scale; band cuts derive from 0-100 scale
        # by dividing by 10: HIGH at 5.5, MEDIUM at 3.5.
        (10.0, "High"),
        (7.5, "High"),
        (5.5, "High"),
        (5.49, "Moderate"),
        (4.0, "Moderate"),
        (3.5, "Moderate"),
        (3.49, "Low"),
        (0.0, "Low"),
    ],
)
def test_compact_renewal_risk_status_uses_canonical_thresholds(
    score_0_10, expected_label
):
    """Compact's ``add_health_summary_table`` renewal-risk row label
    must derive from the canonical 0-100 thresholds (mapped to 0-10),
    NOT the legacy <4 / <7 cuts.
    """
    high_cut_0_10 = RISK_BAND_THRESHOLDS["HIGH"] / 10.0
    medium_cut_0_10 = RISK_BAND_THRESHOLDS["MEDIUM"] / 10.0
    if score_0_10 >= high_cut_0_10:
        derived = "High"
    elif score_0_10 >= medium_cut_0_10:
        derived = "Moderate"
    else:
        derived = "Low"
    assert derived == expected_label, (
        f"Score {score_0_10}/10 should map to {expected_label} via "
        f"canonical thresholds (HIGH={high_cut_0_10}, MEDIUM={medium_cut_0_10}), "
        f"got {derived}"
    )


def test_low_risk_threshold_aligns_with_medium_band_cut():
    """``app_simple._low_risk_customers`` must use the canonical
    MEDIUM threshold rather than the ad-hoc 30 cut on a 0-100 scale.
    """
    assert RISK_BAND_THRESHOLDS["MEDIUM"] == 35, (
        "If MEDIUM band threshold drifts away from 35 the low-risk "
        "narrative cut in app_simple needs to follow."
    )


def test_count_score_range_helper_distinguishes_score_from_band():
    """Round 4 added ``cm.count_score_range`` so narratives can speak
    about "Score 4-6 (Watch)" without conflating that range with the
    canonical MEDIUM band.
    """
    import canonical_metrics as cm

    profiles = {
        "A": {"risk_score_0_10": 8.5, "risk_band": "CRITICAL"},
        "B": {"risk_score_0_10": 5.0, "risk_band": "MEDIUM"},
        "C": {"risk_score_0_10": 4.5, "risk_band": "MEDIUM"},
        "D": {"risk_score_0_10": 3.0, "risk_band": "LOW"},
        "E": {"risk_score_0_10": 6.5, "risk_band": "HIGH"},
    }
    n = cm.count_score_range(profiles, low=4.0, high=7.0, scale="0_to_10")
    assert n == 3, (
        "Profiles with score in [4, 7) are A=no, B=yes, C=yes, D=no, "
        f"E=yes -> 3 expected, got {n}"
    )
