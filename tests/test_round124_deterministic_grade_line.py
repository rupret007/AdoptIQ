"""Round 124 / I1 regression tests.

When a per-customer Comprehensive narrative is withheld (R27 grounding
rejection), fails (LLM ERROR), or raises, the deterministic Customer
Health Score line must still be emitted from the canonical profile so
every profiled customer keeps a grounded grade in the doc.  Build 92
audit caught the single C-grade customer (T-MOBILE) and UPMC vanishing
from the grade roll-up because the placeholder substitution dropped the
stamped grade with the body.
"""

from source_shape_utils import assert_in_source
import app_simple


def test_grade_line_renders_band_and_score_for_high_profile():
    profile = {"risk_band": "HIGH", "risk_score_0_10": 7.4, "risk_score_0_100": 74.0}
    line = app_simple._r124_deterministic_grade_line(profile)
    assert line is not None
    assert line.startswith("Customer Health Score:")
    assert "HIGH" in line
    assert "7.4/10" in line


def test_grade_line_uses_canonical_letter_for_band():
    # HIGH band -> grade D under the canonical band->grade map.
    profile = {"risk_band": "HIGH", "risk_score_0_10": 7.4, "risk_score_0_100": 74.0}
    line = app_simple._r124_deterministic_grade_line(profile)
    canonical = app_simple._r123_health_grade_for_profile(profile)
    assert f"Customer Health Score: {canonical}" in line


def test_grade_line_healthy_profile():
    profile = {"risk_band": "HEALTHY", "risk_score_0_10": 1.1, "risk_score_0_100": 11.0}
    line = app_simple._r124_deterministic_grade_line(profile)
    assert "HEALTHY" in line
    assert "1.1/10" in line


def test_grade_line_none_for_empty_or_nondict():
    assert app_simple._r124_deterministic_grade_line(None) is None
    assert app_simple._r124_deterministic_grade_line({}) is None
    assert app_simple._r124_deterministic_grade_line("not a dict") is None
    assert app_simple._r124_deterministic_grade_line([1, 2, 3]) is None


def test_grade_line_tolerates_missing_score():
    profile = {"risk_band": "MEDIUM"}
    line = app_simple._r124_deterministic_grade_line(profile)
    assert line is not None
    assert "MEDIUM" in line
    assert "0.0/10" in line


def test_grade_line_tolerates_garbage_score():
    profile = {"risk_band": "LOW", "risk_score_0_10": "not-a-number"}
    line = app_simple._r124_deterministic_grade_line(profile)
    assert line is not None
    assert "0.0/10" in line


def test_i1_source_marker_present():
    import inspect

    src = inspect.getsource(app_simple._r124_deterministic_grade_line)
    assert_in_source(src, "Round 124 / I1", label='src')
