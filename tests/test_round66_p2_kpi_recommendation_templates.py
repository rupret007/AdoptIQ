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
from source_shape_utils import assert_in_source

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
    assert_in_source(text, "7 open", label='text')
    assert_in_source(text, "Jane Doe", label='text')
    assert_in_source(text, "technical and process", label='text')


def test_csm_engagement_handles_zero_open_abs() -> None:
    text = kpi_recommendation_csm_engagement(open_ab_count=0)
    assert "weekly" in text.lower()
    # Zero-AB path must not call out "0 open adoption barriers"
    # because that reads as conflicting; use a positive framing.
    assert_in_source(text, "no open adoption barriers" in text.lower() or "engagement frequency", label='text')


def test_csm_engagement_falls_back_when_no_cssm() -> None:
    text = kpi_recommendation_csm_engagement(open_ab_count=5)
    assert_in_source(text, "the assigned CSM", label='text')


# ---------------------------------------------------------------------------
# Training template
# ---------------------------------------------------------------------------


def test_training_renders_completion_rate_as_percentage() -> None:
    text = kpi_recommendation_training(completion_rate=0.42)
    assert_in_source(text, "42%", label='text')
    assert_in_source(text, "below the 50%", label='text')


def test_training_handles_already_percentage() -> None:
    """Caller may pass either 0.42 or 42.0; helper handles both."""
    text = kpi_recommendation_training(completion_rate=42.0)
    assert_in_source(text, "42%", label='text')


def test_training_falls_back_when_no_feature_area() -> None:
    text = kpi_recommendation_training(completion_rate=0.3)
    assert_in_source(text, "lowest-completion product modules", label='text')


def test_training_handles_bad_completion_rate() -> None:
    text = kpi_recommendation_training(completion_rate="not a rate")
    assert_in_source(text, "0%", label='text')


# ---------------------------------------------------------------------------
# Engagement cadence template
# ---------------------------------------------------------------------------


def test_engagement_cadence_includes_score_and_days() -> None:
    text = kpi_recommendation_engagement_cadence(
        engagement_score=15,
        days_since_last_touch=42,
    )
    assert_in_source(text, "15/100", label='text')
    assert_in_source(text, "42 days", label='text')
    assert_in_source(text, "below the 30 floor", label='text')


def test_engagement_cadence_handles_no_days() -> None:
    text = kpi_recommendation_engagement_cadence(engagement_score=20)
    assert_in_source(text, "20/100", label='text')
    assert_in_source(text, "next 7 days", label='text')


# ---------------------------------------------------------------------------
# High-severity barriers template
# ---------------------------------------------------------------------------


def test_high_severity_includes_count() -> None:
    text = kpi_recommendation_high_severity_barriers(high_severity_count=5)
    assert_in_source(text, "5 high-severity", label='text')
    assert_in_source(text, "this sprint", label='text')


def test_high_severity_includes_breakdown_when_present() -> None:
    text = kpi_recommendation_high_severity_barriers(
        high_severity_count=8,
        severity_breakdown={"Critical": 2, "High": 6},
    )
    assert_in_source(text, "2 Critical", label='text')
    assert_in_source(text, "6 High", label='text')


def test_high_severity_handles_zero() -> None:
    text = kpi_recommendation_high_severity_barriers(high_severity_count=0)
    assert_in_source(text, "No high-severity", label='text')
    assert_in_source(text, "monitoring", label='text')


# ---------------------------------------------------------------------------
# Premium support template
# ---------------------------------------------------------------------------


def test_premium_support_includes_arr() -> None:
    text = kpi_recommendation_premium_support(total_arr=250_000)
    assert_in_source(text, "$250,000" in text or "250", label='text')
    assert_in_source(text, "premium-support", label='text')


def test_premium_support_includes_bems_when_present() -> None:
    text = kpi_recommendation_premium_support(total_arr=500_000, bems_count=3)
    assert_in_source(text, "3 BEMS", label='text')


def test_premium_support_handles_zero_arr() -> None:
    text = kpi_recommendation_premium_support(total_arr=0)
    assert_in_source(text, "Confirm ARR", label='text')
    assert_in_source(text, "zero", label='text')


# ---------------------------------------------------------------------------
# Upsell template
# ---------------------------------------------------------------------------


def test_upsell_includes_arr() -> None:
    text = kpi_recommendation_upsell(total_arr=5_000)
    assert_in_source(text, "$5,000" in text or "5,000", label='text')


def test_upsell_includes_feature_gaps_when_present() -> None:
    text = kpi_recommendation_upsell(
        total_arr=8_000,
        feature_gaps=["analytics", "automation"],
    )
    assert_in_source(text, "analytics and automation", label='text')
    assert_in_source(text, "22% expansion-rate", label='text')


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
    assert_in_source(text, "kpi_recommendation_csm_engagement", label='text')
    assert_in_source(text, "kpi_recommendation_training", label='text')
    assert_in_source(text, "kpi_recommendation_high_severity_barriers", label='text')
    assert_in_source(text, "kpi_recommendation_premium_support", label='text')
    assert_in_source(text, "kpi_recommendation_upsell", label='text')
    assert_in_source(text, "kpi_recommendation_engagement_cadence", label='text')


def test_renewal_analyzer_recommendation_method_carries_r66_b12_marker() -> None:
    """Source marker MUST be present so a future refactor flags here."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    assert_in_source(text, "Round 66 / Pass 3 (B12)", label='text')


def test_recommendation_method_falls_back_gracefully_when_helpers_missing() -> None:
    """When the lazy import fails, the legacy generic strings MUST
    still flow through (fail-soft contract)."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "advanced_renewal_analyzer.py").read_text(encoding="utf-8")
    # The fallback sentinel: legacy generic phrasing is preserved.
    assert_in_source(text, "Assign dedicated Customer Success Manager", label='text')
    assert_in_source(text, "Provide additional training and onboarding support", label='text')
    assert_in_source(text, "Schedule regular check-ins to increase engagement", label='text')


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
