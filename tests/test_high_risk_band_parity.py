"""Phase 5 / Phase 1.5 regression test.

A risk profile carrying ``risk_band="HIGH"`` must be flagged as
high-risk by ``cm.is_high_risk_profile`` and ``cm.compute_high_risk_count``
even when its raw 0-10 score (e.g. 5.7) sits below the legacy
``score >= 6`` ad-hoc cutoff.  Before Phase 1.5 the EI table and the
``high_risk_customers`` count disagreed for exactly this kind of
profile, leaving the same report claiming N high-risk in one section
and N-1 in another.
"""
from __future__ import annotations

import canonical_metrics as cm


def test_high_band_below_legacy_six_is_still_high_risk() -> None:
    profile = {
        "risk_score_0_10": 5.7,
        "risk_score_0_100": 57.0,
        "risk_band": "HIGH",
        "color": "Red",
    }
    assert cm.is_high_risk_profile(profile, scale=cm.RISK_SCALE_0_TO_10), (
        "A profile flagged HIGH band must register as high-risk even "
        "if its 0-10 score (5.7) is below the legacy >=6 cutoff."
    )
    assert cm.is_high_risk_profile(profile, scale=cm.RISK_SCALE_0_TO_100), (
        "Same profile must register as high-risk on the canonical 0-100 scale."
    )


def test_compute_high_risk_count_band_parity_across_scales() -> None:
    profiles = {
        "AcmeCorp": {
            "risk_score_0_10": 5.7,
            "risk_score_0_100": 57.0,
            "risk_band": "HIGH",
            "color": "Red",
        },
        "BetaCo": {
            "risk_score_0_10": 8.2,
            "risk_score_0_100": 82.0,
            "risk_band": "CRITICAL",
            "color": "Red",
        },
        "GammaInc": {
            "risk_score_0_10": 3.5,
            "risk_score_0_100": 35.0,
            "risk_band": "MEDIUM",
            "color": "Amber",
        },
    }
    n100 = cm.compute_high_risk_count(profiles, scale=cm.RISK_SCALE_0_TO_100)
    n010 = cm.compute_high_risk_count(profiles, scale=cm.RISK_SCALE_0_TO_10)
    assert n100 == n010 == 2, (
        "Both scales must report the same high-risk count for the "
        "same portfolio (Acme HIGH + Beta CRITICAL = 2). "
        f"Got 0-100={n100}, 0-10={n010}."
    )


def test_is_high_risk_profile_in_public_api() -> None:
    """Phase 1.5: ``is_high_risk_profile`` must be in __all__ so other
    modules can import it without depending on internals.
    """
    assert "is_high_risk_profile" in cm.__all__, (
        "is_high_risk_profile must be exported in canonical_metrics.__all__."
    )


def test_compute_high_risk_default_threshold_is_band_aligned() -> None:
    """Phase 1.5 aligned the 0-10 default threshold with the HIGH band
    cutoff (5.5).  If this drifts the legacy compact path will once
    again disagree with the EI table.
    """
    score_5_5 = {"risk_score_0_10": 5.5}
    profiles = {"X": score_5_5}
    n = cm.compute_high_risk_count(profiles, scale=cm.RISK_SCALE_0_TO_10)
    assert n == 1, (
        "A profile at exactly 5.5/10 must count as high-risk under the "
        "default threshold (which is aligned to the HIGH band cutoff)."
    )
