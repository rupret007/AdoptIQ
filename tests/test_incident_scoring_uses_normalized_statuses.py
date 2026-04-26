"""Round 2 / Phase 1.8 regression test.

``risk_scoring._score_incidents`` previously used a status allow-list
of ``investigating/identified/monitoring/major_outage`` that no
longer matched ``fetch_status_incidents`` output (which normalizes
status to ``active`` / ``resolved`` and impact_level to
``Low/Medium/High/Critical``).  The dead allow-list silently zeroed
``high_impact_count`` so the impact-weighted score component vanished.

This test pins the new behavior end-to-end:

  * an ``active`` row contributes to ``active_count``
  * a ``high`` impact row contributes to ``high_impact_count``
  * a ``critical`` impact row contributes to BOTH ``critical_impact_count``
    and (for legacy callers) ``high_impact_count``, and is scored
    strictly higher than a single ``high`` event.
"""
from __future__ import annotations

import risk_scoring


def test_active_high_incident_increments_high_impact_count() -> None:
    incidents = [
        {"status": "active", "impact_level": "High"},
    ]
    out = risk_scoring._score_incidents(incidents)
    details = out["details"]
    assert details["count"] == 1
    assert details["active_count"] == 1, (
        "Round 2 Phase 1.8: an active-status incident must register "
        "as active_count=1 under the normalized vocabulary."
    )
    assert details["high_impact_count"] == 1, (
        "Round 2 Phase 1.8: a High-impact incident must register as "
        "high_impact_count=1 under the normalized impact_level "
        "vocabulary (the prior allow-list missed every normalized row)."
    )
    assert out["score"] > 0.0


def test_critical_incident_outscores_high_only_incident() -> None:
    """Round 2 Phase 5.5: ``critical`` is now a distinct band and
    carries an extra weight on top of the high-impact contribution,
    so a single critical event must score strictly higher than a
    single high event.
    """
    high_only = risk_scoring._score_incidents([
        {"status": "active", "impact_level": "High"},
    ])
    critical_only = risk_scoring._score_incidents([
        {"status": "active", "impact_level": "Critical"},
    ])
    assert critical_only["details"]["critical_impact_count"] == 1
    assert critical_only["score"] > high_only["score"], (
        "Round 2 Phase 5.5: a single Critical-impact incident must "
        f"score strictly higher than a High-only incident. "
        f"Got high={high_only['score']}, critical={critical_only['score']}."
    )
