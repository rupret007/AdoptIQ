"""Round 161 — R95 must not parse KPI numbers from METRIC-* citation slugs."""

from __future__ import annotations


_LIVE_FAILURE_ANSWER = (
    "Open adoption barriers: 25 [Source: METRIC-TOTAL-BARRIERS-D9084B46]. "
    "Momentum is easing across the portfolio."
)


def test_round161_r95_strips_metric_source_ids_before_extraction() -> None:
    from ask_ai_grounded import _r95_extract_answer_value, _r95_text_for_kpi_extraction

    cleaned = _r95_text_for_kpi_extraction(_LIVE_FAILURE_ANSWER)
    assert "METRIC-TOTAL-BARRIERS" not in cleaned.upper()
    assert "D9084" not in cleaned
    assert _r95_extract_answer_value(_LIVE_FAILURE_ANSWER, ("barriers", "adoption barriers")) == 25.0


def test_round161_r95_no_false_correction_on_live_failure_shape() -> None:
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    result = _r95_cross_check_answer_against_canonical(
        _LIVE_FAILURE_ANSWER,
        {"manager": "Brian Frazier"},
        {"total_barriers": 25, "open_adoption_barriers": 25},
    )
    assert result.corrections == []
    assert "total_barriers" in result.verified or not result.verified


def test_round161_r95_still_flags_real_barrier_drift() -> None:
    from ask_ai_grounded import _r95_cross_check_answer_against_canonical

    result = _r95_cross_check_answer_against_canonical(
        "Total barriers: 47 in scope.",
        {"manager": "Brian Frazier"},
        {"total_barriers": 25},
    )
    assert len(result.corrections) == 1
    assert result.corrections[0]["kpi"] == "total_barriers"
    assert result.corrections[0]["llm_value"] == 47
