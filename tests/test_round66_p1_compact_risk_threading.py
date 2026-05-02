"""Round 66 / Pass 2 (B8) — Compact / Excel risk score threading.

Pre-R66 the Compact + Excel paths in ``app_simple.py`` passed
``extra_frames`` (subscriptions, pulse, action plans) to
``calculate_renewal_risk_scores`` for customer-universe expansion
ONLY -- the per-customer scoring then ran against ``pd.DataFrame()``
for pulse / AP / subs, dampening the composite ~30% relative to the
Renewal pipeline (which feeds all components real data).

R66/B8 fixes this by:
1. Adding ``_r66_b8_classify_extra_frames`` to inspect each frame's
   columns and return ``(pulse_df, action_plans_df, subs_df)``.
2. Per-customer slicing those three frames inside the scoring loop
   and threading them into ``compute_customer_risk_profile``.

These tests pin the classifier's column markers and verify the
end-to-end score threading reduces the Compact / Renewal gap.
"""
from __future__ import annotations

import pandas as pd
import pytest


def test_classify_extra_frames_handles_none() -> None:
    """``None`` extra_frames returns three Nones."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse, aps, subs = _r66_b8_classify_extra_frames(None)
    assert pulse is None and aps is None and subs is None


def test_classify_extra_frames_handles_empty_list() -> None:
    """Empty list returns three Nones."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse, aps, subs = _r66_b8_classify_extra_frames([])
    assert pulse is None and aps is None and subs is None


def test_classify_extra_frames_skips_empty_dataframes() -> None:
    """Empty / non-DataFrame entries are skipped."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse, aps, subs = _r66_b8_classify_extra_frames([
        pd.DataFrame(),
        None,
        "not a dataframe",
        pd.DataFrame(columns=["UNKNOWN_COL"]),
    ])
    assert pulse is None and aps is None and subs is None


def test_classify_extra_frames_detects_pulse_by_PULSE_RATING__C() -> None:
    """Pulse frames carry ``PULSE_RATING__C`` (CSConsole canonical)."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse_frame = pd.DataFrame([{
        "BU_NAME": "Acme",
        "PULSE_RATING__C": "Poor",
    }])
    pulse, aps, subs = _r66_b8_classify_extra_frames([pulse_frame])
    assert pulse is pulse_frame
    assert aps is None and subs is None


def test_classify_extra_frames_detects_pulse_by_SCORE__C() -> None:
    """Pulse frames may also carry numeric ``SCORE__C``."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse_frame = pd.DataFrame([{
        "BU_NAME": "Acme",
        "SCORE__C": 4,
    }])
    pulse, aps, subs = _r66_b8_classify_extra_frames([pulse_frame])
    assert pulse is pulse_frame


def test_classify_extra_frames_detects_subs_by_RENEWAL_RISK_CATEGORY() -> None:
    """Subscriptions carry ``RENEWAL_RISK_CATEGORY`` (the most diagnostic field)."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    subs_frame = pd.DataFrame([{
        "BU_NAME": "Acme",
        "RENEWAL_RISK_CATEGORY": "High",
        "STATUS_C": "Active",
    }])
    pulse, aps, subs = _r66_b8_classify_extra_frames([subs_frame])
    assert subs is subs_frame
    assert pulse is None and aps is None


def test_classify_extra_frames_detects_aps_by_STATUS_C_alone() -> None:
    """Action plans carry ``STATUS_C`` without pulse / subs / AB markers."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    ap_frame = pd.DataFrame([{
        "BU_NAME": "Acme",
        "NAME": "Onboarding plan",
        "STATUS_C": "Open",
    }])
    pulse, aps, subs = _r66_b8_classify_extra_frames([ap_frame])
    assert aps is ap_frame
    assert pulse is None and subs is None


def test_classify_extra_frames_does_not_classify_AB_as_action_plans() -> None:
    """AB frames (``SEVERITY_C`` / ``AB_STATUS_C``) MUST NOT be classified as APs."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    ab_frame = pd.DataFrame([{
        "BU_NAME": "Acme",
        "SEVERITY_C": "High",
        "AB_STATUS_C": "Open",
    }])
    pulse, aps, subs = _r66_b8_classify_extra_frames([ab_frame])
    assert aps is None, "AB frame must not be misclassified as action plans"
    assert pulse is None and subs is None


def test_classify_extra_frames_handles_three_frames_together() -> None:
    """All three categories detected from a typical app_simple call site."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    pulse_frame = pd.DataFrame([{"BU_NAME": "Acme", "PULSE_RATING__C": "Good"}])
    subs_frame = pd.DataFrame([{"BU_NAME": "Acme", "RENEWAL_RISK_CATEGORY": "Low"}])
    ap_frame = pd.DataFrame([{"BU_NAME": "Acme", "STATUS_C": "Open"}])
    extras = [pulse_frame, subs_frame, ap_frame]
    pulse, aps, subs = _r66_b8_classify_extra_frames(extras)
    assert pulse is pulse_frame
    assert subs is subs_frame
    assert aps is ap_frame


def test_compact_score_lifts_when_extra_frames_provided() -> None:
    """End-to-end: compact composite score MUST go UP when pulse/AP/subs are threaded.

    Pre-R66 the score below would be ~30% lower because pulse / AP /
    subs scored as 0.0 (NOT excluded). Post-R66 the same Compact path
    feeds those frames into compute_customer_risk_profile and the
    composite is closer to what the Renewal path produces for the
    same customer.
    """
    from compact_report_formatter import calculate_renewal_risk_scores

    customer_name = "Acme"
    ab = pd.DataFrame([{
        "ID": "AB-1",
        "customer_name": customer_name,
        "AB_STATUS_C": "Open",
        "SEVERITY_C": "High",
    }])
    csone = pd.DataFrame([{
        "customer_name": customer_name,
        "Severity": "P1",
        "Status": "Open",
        "Case #": "12345",
        "Date/Time Opened": "2026-04-01T00:00:00Z",
    }])

    # Baseline: no extra_frames threaded.
    baseline = calculate_renewal_risk_scores(ab, csone, recent_window_days=90)
    baseline_score = baseline.get(customer_name, {}).get("score", 0.0)

    # With extra_frames carrying poor pulse + open APs + high-risk subs.
    pulse_frame = pd.DataFrame([{
        "BU_NAME": customer_name,
        "PULSE_RATING__C": "Poor",
    }])
    ap_frame = pd.DataFrame([{
        "BU_NAME": customer_name,
        "STATUS_C": "Open",
    }])
    subs_frame = pd.DataFrame([{
        "BU_NAME": customer_name,
        "RENEWAL_RISK_CATEGORY": "High",
        "STATUS_C": "Active",
    }])
    threaded = calculate_renewal_risk_scores(
        ab,
        csone,
        extra_frames=[pulse_frame, ap_frame, subs_frame],
        recent_window_days=90,
    )
    threaded_score = threaded.get(customer_name, {}).get("score", 0.0)

    assert threaded_score > baseline_score, (
        f"R66/B8 lift failed: baseline={baseline_score} threaded={threaded_score} "
        "-- extra_frames did not flow into the per-customer scoring"
    )


def test_compact_baseline_unchanged_when_no_extra_frames() -> None:
    """Negative regression: not passing extra_frames keeps pre-R66 behavior."""
    from compact_report_formatter import calculate_renewal_risk_scores

    customer_name = "Acme"
    ab = pd.DataFrame([{
        "ID": "AB-1",
        "customer_name": customer_name,
        "AB_STATUS_C": "Open",
        "SEVERITY_C": "High",
    }])
    csone = pd.DataFrame([{
        "customer_name": customer_name,
        "Severity": "P3",
        "Status": "Closed",
        "Case #": "12345",
    }])
    result = calculate_renewal_risk_scores(ab, csone, recent_window_days=90)
    assert customer_name in result
    assert "score" in result[customer_name]
    assert isinstance(result[customer_name]["score"], (int, float))


def test_compact_score_uses_BU_NAME_when_pulse_lacks_customer_name() -> None:
    """Pulse / AP / subs frames typically use BU_NAME, not ``customer_name``.

    The R66/B8 classifier must support BU_NAME slicing because that's
    what the live CSConsole feed uses. Without this, a pulse frame
    keyed on BU_NAME would silently fail to match.
    """
    from compact_report_formatter import calculate_renewal_risk_scores

    ab = pd.DataFrame([{
        "ID": "AB-1",
        "customer_name": "Acme",
        "AB_STATUS_C": "Open",
        "SEVERITY_C": "High",
    }])
    csone = pd.DataFrame([{
        "customer_name": "Acme",
        "Severity": "P1",
        "Status": "Open",
        "Case #": "12345",
    }])
    pulse_frame = pd.DataFrame([{"BU_NAME": "Acme", "PULSE_RATING__C": "Poor"}])
    result = calculate_renewal_risk_scores(
        ab,
        csone,
        extra_frames=[pulse_frame],
        recent_window_days=90,
    )
    # Result keys retain the original (un-normalized) customer name casing
    # from the AB / CSone columns.
    assert any(k for k in result.keys() if "acme" in str(k).lower()), (
        f"Expected an Acme-bearing key in result, got: {list(result.keys())}"
    )
    cust_key = next(k for k in result.keys() if "acme" in str(k).lower())
    # Lift assertion: with poor pulse threaded, score should NOT match
    # the no-pulse baseline byte-for-byte.
    baseline = calculate_renewal_risk_scores(ab, csone, recent_window_days=90)
    baseline_cust_key = next(k for k in baseline.keys() if "acme" in str(k).lower())
    assert result[cust_key]["score"] != baseline[baseline_cust_key]["score"], (
        "BU_NAME-keyed pulse slice did not affect score -- _r66_b8_slice "
        "did not iterate the BU_NAME column"
    )


def test_classifier_returns_first_match_for_each_category() -> None:
    """When multiple frames could classify as pulse, the first wins (deterministic)."""
    from compact_report_formatter import _r66_b8_classify_extra_frames

    first_pulse = pd.DataFrame([{"BU_NAME": "A", "PULSE_RATING__C": "X"}])
    second_pulse = pd.DataFrame([{"BU_NAME": "B", "PULSE_RATING__C": "Y"}])
    pulse, aps, subs = _r66_b8_classify_extra_frames([first_pulse, second_pulse])
    assert pulse is first_pulse, "Classifier MUST be deterministic on first match"
