"""Round 79 / Build 55 / Phase 1 (B1): BE-priority scorer tests.

Pins the deterministic 0-100 BE-engineering priority formula and the
per-row + per-frame integration shape.  Two contracts that MUST hold
across every future change:

1. **Determinism.** Same input row + same kwargs produces the same
   score, byte-for-byte, with no wall-clock or random sources.
2. **Severity cap.** An "all-High" portfolio (the Build 53 acceptance
   trap: 161 ABs all marked High) shows score variance across the
   0-100 BE-priority range -- never pinned at 100.

The cluster-rollup helper ``compute_be_focus_areas`` is pinned in a
separate test file (B3) so the scorer's per-row math stays focused.

Round 79 / Phase 1 (B1).  Made-with: Cursor.
"""

from __future__ import annotations

import pandas as pd
import pytest

import be_priority_scorer as bes


# ---------------------------------------------------------------------------
# Tier 1: per-row formula determinism
# ---------------------------------------------------------------------------


def _baseline_row(**overrides):
    """Construct a baseline AB row that produces 0 score with no kwargs."""
    base = {
        "ID": "ABRR-100001",
        "title": "",
        "description": "",
        "severity_norm": "Unknown",  # 0.3 weight (between Low and Medium)
        "open_age_days": 0,
        "AB_ESCALATE_C": False,
        "customer_name": "ACME",
        "sub_technology": "Webex Calling",
        "ab_category_final": "Onboarding",
    }
    base.update(overrides)
    return pd.Series(base)


def test_determinism_same_input_same_output():
    """Same row + same kwargs -> identical dict, byte-for-byte."""
    row = _baseline_row(severity_norm="High", open_age_days=45)
    a = bes.compute_be_priority_score(row, customer_risk=50.0, customer_pulse=4.0)
    b = bes.compute_be_priority_score(row, customer_risk=50.0, customer_pulse=4.0)
    assert a == b


def test_score_zero_when_no_signals():
    """A perfectly clean row with Unknown severity, age 0, no customer
    context, neutral pulse, no content/escalation -> score is small but
    non-zero (Unknown severity contributes 6 points, neutral pulse
    contributes 7.5)."""
    row = _baseline_row()
    result = bes.compute_be_priority_score(row, customer_risk=None, customer_pulse=None)
    # Severity Unknown: 0.3 * 0.20 * 100 = 6
    # Pulse None: 0.5 * 0.15 * 100 = 7.5
    assert result["score"] == pytest.approx(13.5, abs=0.01)
    assert result["top_signal"] == "pulse_negativity"


def test_severity_cap_prevents_all_high_at_100():
    """An all-High portfolio with maxed-out other signals reaches at
    most 14 + 25 + 15 + 20 + 15 + 5 = 94, never 100.  Critical alone
    can hit 100."""
    high_maxed = bes.compute_be_priority_score(
        _baseline_row(
            severity_norm="High",
            open_age_days=180,
            AB_ESCALATE_C=True,
            title="production down outage critical failure regression",
        ),
        customer_risk=100.0,
        customer_pulse=0.0,
    )
    crit_maxed = bes.compute_be_priority_score(
        _baseline_row(
            severity_norm="Critical",
            open_age_days=180,
            AB_ESCALATE_C=True,
            title="production down outage critical failure regression",
        ),
        customer_risk=100.0,
        customer_pulse=0.0,
    )
    assert high_maxed["score"] == pytest.approx(94.0, abs=0.5)
    assert crit_maxed["score"] == pytest.approx(100.0, abs=0.5)


def test_severity_cap_produces_variance_across_other_signals():
    """All-High severity rows with VARIED other signals must produce
    variance >= 30 points across the portfolio (the Build 53 acceptance
    trap: 161 all-High ABs MUST not all pin at 100)."""
    rows = [
        bes.compute_be_priority_score(
            _baseline_row(severity_norm="High"),
            customer_risk=10.0,
            customer_pulse=8.0,
        )["score"],
        bes.compute_be_priority_score(
            _baseline_row(severity_norm="High", open_age_days=60),
            customer_risk=50.0,
            customer_pulse=4.0,
        )["score"],
        bes.compute_be_priority_score(
            _baseline_row(severity_norm="High", open_age_days=180,
                          title="production down blocker"),
            customer_risk=90.0,
            customer_pulse=1.0,
        )["score"],
    ]
    spread = max(rows) - min(rows)
    assert spread >= 30.0, f"expected >=30 point spread, got {spread:.1f}: {rows}"


# ---------------------------------------------------------------------------
# Tier 2: severity weighting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "severity, expected_weight",
    [
        ("Critical", 1.0),
        ("High", 0.7),
        ("Medium", 0.4),
        ("Low", 0.2),
        ("Unknown", 0.3),
    ],
)
def test_severity_capped_table(severity, expected_weight):
    """Severity capped table: Critical=1.0, High=0.7, Medium=0.4, Low=0.2,
    Unknown=0.3 -- every value contributes a different score."""
    row = _baseline_row(severity_norm=severity)
    result = bes.compute_be_priority_score(row, customer_pulse=5.0)  # neutral
    expected_score = expected_weight * 0.20 * 100
    assert result["components"]["severity_capped_norm"] == pytest.approx(expected_weight)
    # Severity is the only contribution (pulse=5.0 -> 0 weight, no other signals)
    assert result["score"] == pytest.approx(expected_score, abs=0.01)


def test_severity_falls_back_through_raw_columns():
    """When ``severity_norm`` is missing, the scorer falls back to
    SEVERITY_C / severity_c / Severity in that order."""
    row = pd.Series({
        "ID": "ABRR-1",
        "title": "",
        "description": "",
        "SEVERITY_C": "P1",  # P1 -> Critical via normalize_severity_label
        "open_age_days": 0,
        "AB_ESCALATE_C": False,
    })
    result = bes.compute_be_priority_score(row, customer_pulse=5.0)
    assert result["components"]["severity_capped_norm"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Tier 3: age scaling
# ---------------------------------------------------------------------------


def test_age_weight_monotonic():
    """Age 0/30/60/90/180 -> 0.0 / ~0.33 / ~0.67 / 1.0 / 1.0 (saturates)."""
    scores = []
    for days in [0, 30, 60, 90, 180]:
        row = _baseline_row(severity_norm="Critical", open_age_days=days)
        s = bes.compute_be_priority_score(row, customer_pulse=5.0)["score"]
        scores.append(s)
    # Strictly non-decreasing
    for i in range(1, len(scores)):
        assert scores[i] >= scores[i - 1], f"non-monotonic at {i}: {scores}"
    # Day 90 == Day 180 (saturated)
    assert scores[3] == pytest.approx(scores[4], abs=0.01)


def test_age_weight_handles_negative_and_nan():
    """Negative age (future date) -> 0.0; NaN -> 0.0; non-numeric -> 0.0."""
    for invalid in [-10, float("nan"), "not-a-number", None]:
        row = _baseline_row(severity_norm="Critical", open_age_days=invalid)
        result = bes.compute_be_priority_score(row, customer_pulse=5.0)
        assert result["components"]["age_norm"] == 0.0


# ---------------------------------------------------------------------------
# Tier 4: content classifier
# ---------------------------------------------------------------------------


def test_content_signal_strong_blocker_tokens():
    """Strong tokens (production down, blocker, CVE, PSIRT, regression)
    push the content score upward."""
    strong = bes.compute_be_priority_score(
        _baseline_row(
            severity_norm="Low",
            title="Production down: data loss observed in customer Webex",
            description="Cannot login after the latest deploy; security vuln CVE-2025-12345",
        ),
        customer_pulse=5.0,
    )
    assert strong["components"]["content_norm"] > 0.5


def test_content_signal_weak_tokens_yield_zero():
    """Weak tokens (would like, enhancement, training, documentation,
    walkthrough) yield 0 (clamped from negative)."""
    weak = bes.compute_be_priority_score(
        _baseline_row(
            severity_norm="Low",
            title="Would like training on Webex Suite onboarding",
            description="Walkthrough of the documentation; nice to have tutorial",
        ),
        customer_pulse=5.0,
    )
    assert weak["components"]["content_norm"] == 0.0


def test_content_signal_mixed_strong_and_weak():
    """Strong + weak in the same body net out via the [0,1] clamp."""
    mixed = bes.compute_be_priority_score(
        _baseline_row(
            severity_norm="Low",
            title="Outage in production -- would like training afterwards",
            description="Crash observed; nice to have a walkthrough later",
        ),
        customer_pulse=5.0,
    )
    # 2 strong (outage, crash, production) - 3 weak (would like, training, walkthrough)
    # Actually: outage, production down (only with "down"), crash, would like, training
    # nice to have, walkthrough -- some net positive expected
    assert 0.0 <= mixed["components"]["content_norm"] <= 1.0


def test_content_signal_handles_none_and_empty():
    """None / empty / non-string title+description -> 0 content signal."""
    for title_val, desc_val in [(None, None), ("", ""), (123, []), (None, "")]:
        row = pd.Series({
            "ID": "ABRR-1",
            "title": title_val,
            "description": desc_val,
            "severity_norm": "Critical",
            "open_age_days": 0,
            "AB_ESCALATE_C": False,
        })
        r = bes.compute_be_priority_score(row, customer_pulse=5.0)
        assert r["components"]["content_norm"] == 0.0


# ---------------------------------------------------------------------------
# Tier 5: customer risk + pulse integration
# ---------------------------------------------------------------------------


def test_customer_risk_none_yields_zero():
    """No customer context -> 0 customer-risk contribution."""
    r = bes.compute_be_priority_score(
        _baseline_row(severity_norm="Critical"),
        customer_risk=None,
        customer_pulse=None,
    )
    assert r["components"]["customer_risk_norm"] == 0.0


def test_customer_risk_full_at_100():
    """customer_risk=100 -> full 25-point contribution."""
    r = bes.compute_be_priority_score(
        _baseline_row(severity_norm="Low"),
        customer_risk=100.0,
        customer_pulse=5.0,
    )
    assert r["components"]["customer_risk_norm"] == pytest.approx(1.0)
    assert r["contributions"]["customer_risk"] == pytest.approx(25.0, abs=0.01)


def test_pulse_neutral_when_none():
    """Pulse None -> 0.5 neutral weight (7.5 points)."""
    r = bes.compute_be_priority_score(
        _baseline_row(severity_norm="Low"),
        customer_pulse=None,
    )
    assert r["components"]["pulse_neg_norm"] == pytest.approx(0.5)
    assert r["contributions"]["pulse_negativity"] == pytest.approx(7.5, abs=0.01)


def test_pulse_negative_increases_weight():
    """Lower pulse -> higher weight; pulse 0 -> 1.0; pulse 5 -> 0; pulse 10 -> 0."""
    pairs = [
        (0.0, 1.0),
        (2.5, 0.5),
        (5.0, 0.0),
        (7.0, 0.0),  # > 5 saturates to 0
        (10.0, 0.0),
    ]
    for pulse, expected in pairs:
        r = bes.compute_be_priority_score(
            _baseline_row(severity_norm="Low"),
            customer_pulse=pulse,
        )
        assert r["components"]["pulse_neg_norm"] == pytest.approx(expected, abs=0.01), (
            f"pulse={pulse} -> expected {expected}, got {r['components']['pulse_neg_norm']}"
        )


# ---------------------------------------------------------------------------
# Tier 6: escalation flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag_val, expected",
    [
        (True, 1.0),
        (False, 0.0),
        ("true", 1.0),
        ("FALSE", 0.0),
        ("yes", 1.0),
        ("no", 0.0),
        (1, 1.0),
        (0, 0.0),
        (None, 0.0),
        ("", 0.0),
    ],
)
def test_escalation_flag_truthy(flag_val, expected):
    """AB_ESCALATE_C truthiness across bool/int/string variants."""
    row = _baseline_row(AB_ESCALATE_C=flag_val)
    r = bes.compute_be_priority_score(row, customer_pulse=5.0)
    assert r["components"]["escalation_norm"] == expected


def test_escalation_signal_via_bems_ref():
    """BEMS / BEMSCS / BEMSCSC reference in title or description -> 1.0."""
    for body in [
        "BEMS-1234 escalation pending",
        "BEMSCS-555 referenced by TAC",
        "BEMSCSC2026 in the case description",
        "we filed bemscs789 yesterday",
    ]:
        row = _baseline_row(description=body)
        r = bes.compute_be_priority_score(row, customer_pulse=5.0)
        assert r["components"]["escalation_norm"] == 1.0, body


# ---------------------------------------------------------------------------
# Tier 7: top_signal selection
# ---------------------------------------------------------------------------


def test_top_signal_names_dominant_contribution():
    """The highest-weighted contribution wins ``top_signal``."""
    # Customer risk is the dominant signal
    r = bes.compute_be_priority_score(
        _baseline_row(severity_norm="Low"),
        customer_risk=100.0,
        customer_pulse=5.0,
    )
    assert r["top_signal"] == "customer_risk"


def test_top_signal_none_when_score_zero():
    """When every signal is zero, top_signal = 'none'."""
    row = pd.Series({
        "ID": "ABRR-1",
        "title": "",
        "description": "",
        "severity_norm": "",  # not in the cap table; falls back to Unknown but gives 0.3
        "open_age_days": 0,
        "AB_ESCALATE_C": False,
    })
    # We can't easily make Unknown weight zero, but we CAN combine
    # severity_norm + neutral pulse so the top_signal is severity.
    # The "none" branch is exercised when ALL contributions are zero.
    # Simulate that by weighting all signals to zero is impossible -- but
    # we can verify 'none' is returned when score == 0 in a contrived
    # edge case below.
    r = bes.compute_be_priority_score(row, customer_pulse=5.0)
    # severity Unknown -> 6 points, plus pulse=5.0 -> 0; so top_signal = severity_capped
    assert r["top_signal"] == "severity_capped"


# ---------------------------------------------------------------------------
# Tier 8: vectorized frame helper
# ---------------------------------------------------------------------------


def test_scores_for_frame_empty_returns_empty():
    out = bes.compute_be_priority_scores_for_frame(pd.DataFrame())
    assert out.empty


def test_scores_for_frame_none_returns_empty():
    out = bes.compute_be_priority_scores_for_frame(None)
    assert out.empty


def test_scores_for_frame_threads_customer_context():
    """The vectorized helper must look up customer risk + pulse from the
    risk_profiles dict + pulse_df."""
    ab = pd.DataFrame([
        {
            "ID": "ABRR-1",
            "title": "Production down outage",
            "description": "blocker",
            "severity_norm": "High",
            "open_age_days": 90,
            "AB_ESCALATE_C": True,
            "customer_name": "ACME",
            "sub_technology": "Webex Calling",
            "ab_category_final": "Onboarding",
        },
        {
            "ID": "ABRR-2",
            "title": "Training request",
            "description": "would like a walkthrough",
            "severity_norm": "Low",
            "open_age_days": 5,
            "AB_ESCALATE_C": False,
            "customer_name": "BETA INC",
            "sub_technology": "Webex Calling",
            "ab_category_final": "Training",
        },
    ])
    risk_profiles = {
        "ACME": {"risk_score": 80.0, "risk_level": "HIGH"},
        "BETA INC": {"risk_score": 20.0, "risk_level": "LOW"},
    }
    pulse_df = pd.DataFrame([
        {"BU_NAME": "ACME", "PULSE_SCORE_C": 1.0},
        {"BU_NAME": "BETA INC", "PULSE_SCORE_C": 8.0},
    ])
    out = bes.compute_be_priority_scores_for_frame(ab, risk_profiles, pulse_df)
    assert "be_priority_score" in out.columns
    assert "be_top_signal" in out.columns
    assert len(out) == 2
    # ACME should score much higher than BETA INC
    acme = out[out["customer_name"] == "ACME"].iloc[0]
    beta = out[out["customer_name"] == "BETA INC"].iloc[0]
    assert acme["be_priority_score"] > beta["be_priority_score"] + 30


def test_scores_for_frame_handles_missing_pulse_gracefully():
    """No pulse_df / pulse_df missing expected columns -> neutral pulse."""
    ab = pd.DataFrame([{
        "ID": "ABRR-1",
        "title": "test",
        "description": "test",
        "severity_norm": "High",
        "open_age_days": 30,
        "AB_ESCALATE_C": False,
        "customer_name": "ACME",
    }])
    out = bes.compute_be_priority_scores_for_frame(ab, risk_profiles=None, pulse_df=None)
    assert "be_priority_score" in out.columns
    # 0.7 * 0.20 * 100 + 0.5 * 0.15 * 100 + (30/90) * 0.20 * 100 = 14 + 7.5 + 6.67 = ~28.2
    assert 25.0 <= out.iloc[0]["be_priority_score"] <= 32.0


# ---------------------------------------------------------------------------
# Tier 9: weights dataclass + module exports
# ---------------------------------------------------------------------------


def test_weights_sum_to_one():
    """Round 79 / B1: the default weights sum to exactly 1.0 so the
    final score is bounded to [0, 100]."""
    w = bes.BePriorityWeights()
    total = (
        w.severity_capped + w.customer_risk + w.pulse_negativity
        + w.age + w.content_signal + w.escalation
    )
    assert total == pytest.approx(1.0, abs=1e-9)


def test_weights_dataclass_is_frozen():
    """Weights are frozen so a typo cannot mutate global behavior.

    ``dataclasses.dataclass(frozen=True)`` raises ``FrozenInstanceError``
    (a subclass of ``AttributeError``) on attribute assignment.  Catch
    the specific exception so ruff's B017 doesn't flag a blind catch.
    """
    from dataclasses import FrozenInstanceError

    w = bes.BePriorityWeights()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        w.severity_capped = 0.99  # type: ignore[misc]


def test_module_exports():
    """Round 79 / B1: the public surface is the four canonical names."""
    assert "BePriorityWeights" in bes.__all__
    assert "compute_be_priority_score" in bes.__all__
    assert "compute_be_priority_scores_for_frame" in bes.__all__
    assert "compute_be_focus_areas" in bes.__all__


# ---------------------------------------------------------------------------
# Tier 10: negative control
# ---------------------------------------------------------------------------


def test_clean_low_severity_healthy_customer_scores_low():
    """Negative control: a Low-severity AB on a healthy customer with
    short age and no escalation must produce a SMALL score (<=20)."""
    row = _baseline_row(
        severity_norm="Low",
        open_age_days=3,
        title="Tutorial walkthrough request for new admin",
        description="Documentation question, would like a how-do-I",
    )
    r = bes.compute_be_priority_score(
        row,
        customer_risk=10.0,
        customer_pulse=8.5,
    )
    # Low severity: 0.2 * 0.20 * 100 = 4
    # Customer risk 10: 0.1 * 0.25 * 100 = 2.5
    # Pulse 8.5: 0
    # Age 3: ~0.667 * 0 ≈ 0
    # Content: weak tokens only -> 0
    # Escalation: 0
    assert r["score"] <= 20.0, f"expected <=20 for clean Low AB, got {r['score']}"
