"""Round 145 regression coverage for single-customer renewal score scales."""

from __future__ import annotations

import pytest

from app_simple import (
    _r145_project_renewal_scores,
    _r145_reconcile_key_metric_scores,
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"renewal_risk_score": 30.8, "renewal_risk_score_10": 3.1},
            (3.08, 30.8),
        ),
        ({"renewal_risk_score_10": 7.2}, (7.2, 72.0)),
        ({"overall_risk_score": 30.8}, (3.08, 30.8)),
        ({"overall_risk_score": 7.2}, (7.2, 72.0)),
        ({"renewal_risk_score": "not-a-number"}, (0.0, 0.0)),
    ],
)
def test_renewal_score_projection_reconciles_named_scales(
    payload: dict,
    expected: tuple[float, float],
) -> None:
    assert _r145_project_renewal_scores(payload) == expected


def test_renewal_score_projection_prefers_canonical_0_100_value() -> None:
    assert _r145_project_renewal_scores(
        {
            "renewal_risk_score": 30.8,
            "renewal_risk_score_10": 9.9,
            "overall_risk_score": 8.8,
        }
    ) == (3.08, 30.8)


@pytest.mark.parametrize(
    ("metrics", "default_10", "default_100", "expected"),
    [
        ({}, 3.7, 37.0, (3.7, 37.0)),
        ({"Risk_Score": 54.24}, 0.0, 0.0, (5.4, 54.2)),
        (
            {"Risk_Score_0_10": 9.9, "Risk_Score_0_100": 30.8},
            0.0,
            0.0,
            (3.1, 30.8),
        ),
    ],
)
def test_key_metrics_always_publish_reconciled_paired_scales(
    metrics: dict,
    default_10: float,
    default_100: float,
    expected: tuple[float, float],
) -> None:
    out = _r145_reconcile_key_metric_scores(
        metrics,
        default_score_10=default_10,
        default_score_100=default_100,
    )
    assert (out["Risk_Score_0_10"], out["Risk_Score_0_100"]) == expected
