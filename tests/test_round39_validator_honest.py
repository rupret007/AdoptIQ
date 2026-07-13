"""Round 39 / Phase 2.3 — validator reflects what the renderer saw.

Pre-Round-39 the validator was self-contradicting:

  * ``Report_Info.Partial_Data_Warning_Count = 0`` and document text
    said "Validation Status: PASSED, Data Quality Score: 100/100"
    EVEN THOUGH the body had 102 "section unavailable" notices for
    missing Snowflake columns.
  * ``CSOne Data: WARN: Not Available`` in the same Data Sources
    block while the TAC_Cases sheet held 434 rows clearly sourced
    from CSOne.

Round 39 / Phase 2.3 fixes both:

  1. ``_verify_data_sources`` sets ``csone_data_loaded = True``
     whenever ANY CSSM has a non-empty ``tac_cases`` DataFrame
     (TAC cases come exclusively from ``add_tac_cases_from_csone``).
  2. The generator carries a ``self._section_error_count`` accumulator
     that the body renderers increment.  ``_apply_late_quality_penalties``
     (called from ``_add_validation_section``) folds those signals
     into the data quality score and demotes status to "DEGRADED".
"""
from __future__ import annotations

import pandas as pd
import pytest
from unittest import mock


@pytest.fixture
def generator():
    from leader_report_generator import LeaderReportGenerator
    return LeaderReportGenerator(mock.MagicMock(), [
        ("manager@example.com", "CSSM A", "Manager"),
    ])


# ---------------------------------------------------------------------------
# csone_data_loaded honesty.
# ---------------------------------------------------------------------------


def test_csone_data_loaded_true_when_tac_cases_present(generator):
    team_data = {
        "CSSM A": {
            "subscriptions": pd.DataFrame(),
            "customers": ["Acme"],
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame({"x": range(5)}),
        },
    }
    out = generator._verify_data_sources(team_data)
    assert out["csone_data_loaded"] is True, (
        "Round 39 / Phase 2.3: TAC cases come exclusively from CSOne; "
        "their presence proves the CSOne file loaded.  Pre-Round-39 "
        "this flag was hard-coded False."
    )


def test_csone_data_loaded_false_when_no_tac_anywhere(generator):
    team_data = {
        "CSSM A": {
            "subscriptions": pd.DataFrame(),
            "customers": ["Acme"],
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
        },
    }
    out = generator._verify_data_sources(team_data)
    assert out["csone_data_loaded"] is False


def test_team_roster_loaded_reflects_actual_roster(generator):
    team_data = {
        "CSSM A": {
            "subscriptions": pd.DataFrame(),
            "customers": [],
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
        },
    }
    out = generator._verify_data_sources(team_data)
    assert out["team_roster_loaded"] is True


# ---------------------------------------------------------------------------
# Late quality penalty.
# ---------------------------------------------------------------------------


def test_late_quality_penalty_drops_score_when_section_errors_present(generator):
    """Manually inflate ``_section_error_count`` (as the renderer
    would) and assert ``_apply_late_quality_penalties`` drops the
    score and adds a warning."""
    generator._section_error_count = 12
    generator._section_error_kinds = {"account", "contract", "engagement"}
    summary = {
        "overall_status": "PASSED",
        "critical_issues": [],
        "warnings": [],
        "data_quality_score": 100,
        "recommendations": [],
    }
    out = generator._apply_late_quality_penalties(summary)
    assert out["data_quality_score"] < 100, (
        "Round 39 / Phase 2.3: with 12 section_errors across 3 kinds "
        "the score must drop below 100.  Got "
        f"{out['data_quality_score']}/100."
    )
    assert out["overall_status"] == "DEGRADED"
    # The warning text should at least mention section count and kinds.
    joined = " ".join(out.get("warnings", []))
    assert "12" in joined or "sub-section" in joined.lower()


def test_late_quality_penalty_no_op_when_clean(generator):
    """When no section_errors were observed, the score must stay 100."""
    generator._section_error_count = 0
    generator._section_error_kinds = set()
    summary = {
        "overall_status": "PASSED",
        "critical_issues": [],
        "warnings": [],
        "data_quality_score": 100,
        "recommendations": [],
    }
    out = generator._apply_late_quality_penalties(summary)
    assert out["data_quality_score"] == 100
    assert out["overall_status"] == "PASSED"


def test_validation_section_renderer_calls_late_penalty():
    """Pin that ``_add_validation_section`` calls
    ``_apply_late_quality_penalties`` BEFORE rendering the score
    line, so the value the reader sees reflects body-time signals."""
    import inspect
    import leader_report_generator as lrg
    src = inspect.getsource(lrg.LeaderReportGenerator._add_validation_section)
    assert "_apply_late_quality_penalties" in src, (
        "Round 39 / Phase 2.3: _add_validation_section must call "
        "_apply_late_quality_penalties so the rendered Data Quality "
        "Score reflects body-time section_error signals."
    )


def test_section_error_counter_initialized():
    """``LeaderReportGenerator.__init__`` must initialize the
    section_error accumulators so the renderers can safely +=."""
    from leader_report_generator import LeaderReportGenerator
    gen = LeaderReportGenerator(mock.MagicMock(), [("m", "c", "C")])
    assert hasattr(gen, "_section_error_count")
    assert gen._section_error_count == 0
    assert hasattr(gen, "_section_error_kinds")
    assert gen._section_error_kinds == set()
