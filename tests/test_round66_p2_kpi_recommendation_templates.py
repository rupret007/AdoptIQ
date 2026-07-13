"""Round 66 / Pass 3 (B12) — KPI-specific recommendation templates.

Pre-R66 ``advanced_renewal_analyzer._generate_renewal_recommendations``
emitted generic strings ("Assign dedicated CSM", "Provide additional
training") with no link back to the actual KPI value that triggered
each recommendation. R66/B12 introduces ``report_utils.kpi_recommendation_*``
helpers that embed the triggering KPI in the recommendation string so
the operator can act on the line without back-deriving WHY it was
emitted.

These tests pin both:
1. The new helper functions in ``report_utils`` (signatures, output
   shapes, edge-case handling).
2. The wiring into ``advanced_renewal_analyzer._generate_renewal_recommendations``.
"""
from __future__ import annotations

import pytest

from report_utils import (
    _r66_b12_format_categories,
    _r66_b12_safe_int,
    kpi_recommendation_csm_engagement,
    kpi_recommendation_engagement_cadence,
    kpi_recommendation_high_severity_barriers,
    kpi_recommendation_premium_support,
    kpi_recommendation_training,
    kpi_recommendation_upsell,
)


# ---------------------------------------------------------------------------
# Helper internals
# ---------------------------------------------------------------------------


def test_safe_int_coerces_floats() -> None:
    assert _r66_b12_safe_int(7.9) == 7
    assert _r66_b12_safe_int("12") == 12


def test_safe_int_handles_bad_input() -> None:
    assert _r66_b12_safe_int(None) == 0
    assert _r66_b12_safe_int("not-a-number") == 0
    assert _r66_b12_safe_int(float("nan")) == 0
    assert _r66_b12_safe_int(-5) == 0  # default floor is 0


def test_format_categories_handles_empty() -> None:
    assert _r66_b12_format_categories(None) == "the top categories"
    assert _r66_b12_format_categories([]) == "the top categories"
    assert _r66_b12_format_categories(["", "  "]) == "the top categories"


def test_format_categories_handles_single_double_triple() -> None:
    assert _r66_b12_format_categories(["technical"]) == "technical"
    assert _r66_b12_format_categories(["technical", "adoption"]) == "technical and adoption"
    assert _r66_b12_format_categories(["a", "b", "c"]) == "a, b, and c"


# ---------------------------------------------------------------------------
# CSM engagement template
# ---------------------------------------------------------------------------


def test_csm_engagement_includes_open_ab_count() -> None:
    text = kpi_recommendation_csm_engagement(
        open_ab_count=7,
        top_categories=["technical", "process"],
        cssm_name="Jane Doe",
    )
    assert "7 open" in text
    assert "Jane Doe" in text
    assert "technical and process" in text


def test_csm_engagement_handles_zero_open_abs() -> None:
    text = kpi_recommendation_csm_engagement(open_ab_count=0)
    assert "weekly" in text.lower()
    # Zero-AB path must not call out "0 open adoption barriers"
    # because that reads as conflicting; use a positive framing.
    assert "no open adoption barriers" in text.lower() or "engagement frequency" in text


def test_csm_engagement_falls_back_when_no_cssm() -> None:
    text = kpi_recommendation_csm_engagement(open_ab_count=5)
    assert "the assigned CSM" in text


# ---------------------------------------------------------------------------
# Training template
# ---------------------------------------------------------------------------


def test_training_renders_completion_rate_as_percentage() -> None:
    text = kpi_recommendation_training(completion_rate=0.42)
    assert "42%" in text
    assert "below the 50%" in text


def test_training_handles_already_percentage() -> None:
    """Caller may pass either 0.42 or 42.0; helper handles both."""
    text = kpi_recommendation_training(completion_rate=42.0)
    assert "42%" in text


def test_training_falls_back_when_no_feature_area() -> None:
    text = kpi_recommendation_training(completion_rate=0.3)
    assert "lowest-completion product modules" in text


def test_training_handles_bad_completion_rate() -> None:
    text = kpi_recommendation_training(completion_rate="not a rate")
    assert "0%" in text


# ---------------------------------------------------------------------------
# Engagement cadence template
# ---------------------------------------------------------------------------


def test_engagement_cadence_includes_score_and_days() -> None:
    text = kpi_recommendation_engagement_cadence(
        engagement_score=15,
        days_since_last_touch=42,
    )
    assert "15/100" in text
    assert "42 days" in text
    assert "below the 30 floor" in text


def test_engagement_cadence_handles_no_days() -> None:
    text = kpi_recommendation_engagement_cadence(engagement_score=20)
    assert "20/100" in text
    assert "next 7 days" in text


# ---------------------------------------------------------------------------
# High-severity barriers template
# ---------------------------------------------------------------------------


def test_high_severity_includes_count() -> None:
    text = kpi_recommendation_high_severity_barriers(high_severity_count=5)
    assert "5 high-severity" in text
    assert "this sprint" in text


def test_high_severity_includes_breakdown_when_present() -> None:
    text = kpi_recommendation_high_severity_barriers(
        high_severity_count=8,
        severity_breakdown={"Critical": 2, "High": 6},
    )
    assert "2 Critical" in text
    assert "6 High" in text


def test_high_severity_handles_zero() -> None:
    text = kpi_recommendation_high_severity_barriers(high_severity_count=0)
    assert "No high-severity" in text
    assert "monitoring" in text


# ---------------------------------------------------------------------------
# Premium support template
# ---------------------------------------------------------------------------


def test_premium_support_includes_arr() -> None:
    text = kpi_recommendation_premium_support(total_arr=250_000)
    assert "$250,000" in text or "250" in text
    assert "premium-support" in text


def test_premium_support_includes_bems_when_present() -> None:
    text = kpi_recommendation_premium_support(total_arr=500_000, bems_count=3)
    assert "3 BEMS" in text


def test_premium_support_handles_zero_arr() -> None:
    text = kpi_recommendation_premium_support(total_arr=0)
    assert "Confirm ARR" in text
    assert "zero" in text


# ---------------------------------------------------------------------------
# Upsell template
# ---------------------------------------------------------------------------


def test_upsell_includes_arr() -> None:
    text = kpi_recommendation_upsell(total_arr=5_000)
    assert "$5,000" in text or "5,000" in text


def test_upsell_includes_feature_gaps_when_present() -> None:
    text = kpi_recommendation_upsell(
        total_arr=8_000,
        feature_gaps=["analytics", "automation"],
    )
    assert "analytics and automation" in text
    assert "22% expansion-rate" in text


# ---------------------------------------------------------------------------
# Wiring into advanced_renewal_analyzer
# ---------------------------------------------------------------------------


def test_renewal_analyzer_imports_kpi_helpers() -> None:
    """Source-shape pin: the renewal analyzer's recommendation method
    MUST import the KPI helpers (lazy import is fine -- pinned by
    text presence)."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    assert "kpi_recommendation_csm_engagement" in text
    assert "kpi_recommendation_training" in text
    assert "kpi_recommendation_high_severity_barriers" in text
    assert "kpi_recommendation_premium_support" in text
    assert "kpi_recommendation_upsell" in text
    assert "kpi_recommendation_engagement_cadence" in text


def test_renewal_analyzer_recommendation_method_carries_r66_b12_marker() -> None:
    """Source marker MUST be present so a future refactor flags here."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    assert "Round 66 / Pass 3 (B12)" in text


def test_recommendation_method_falls_back_gracefully_when_helpers_missing() -> None:
    """When the lazy import fails, the legacy generic strings MUST
    still flow through (fail-soft contract)."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    # The fallback sentinel: legacy generic phrasing is preserved.
    assert "Assign dedicated Customer Success Manager" in text
    assert "Provide additional training and onboarding support" in text
    assert "Schedule regular check-ins to increase engagement" in text


def test_helpers_compose_into_actionable_sentences() -> None:
    """End-to-end: the templates produce sentences that are both
    actionable AND carry the triggering KPI value, satisfying the
    R66/B12 acceptance bar."""
    samples = [
        kpi_recommendation_csm_engagement(open_ab_count=12, cssm_name="Alex"),
        kpi_recommendation_training(completion_rate=0.35, feature_area="Analytics"),
        kpi_recommendation_engagement_cadence(engagement_score=18, days_since_last_touch=21),
        kpi_recommendation_high_severity_barriers(
            high_severity_count=3, severity_breakdown={"Critical": 1, "High": 2}
        ),
        kpi_recommendation_premium_support(total_arr=350_000, bems_count=5),
        kpi_recommendation_upsell(total_arr=8_500, feature_gaps=["analytics"]),
    ]
    for text in samples:
        # Each must be at least 30 chars long (rules out "TODO" / "TBD"
        # placeholders).
        assert len(text) > 30, f"Recommendation too short: {text!r}"
        # Must NOT carry the pre-R66 generic phrasings.
        assert "Engage CSM" not in text
        # Must contain at least one digit (the triggering KPI value).
        assert any(c.isdigit() for c in text), (
            f"Recommendation missing KPI digit: {text!r}"
        )
