"""Round 153 -- Leader decision-value regression pins.

Tier 1: the "Evidence-backed next action" column used to render a band-level
constant, so two customers in the same risk band produced byte-identical
rows.  ``risk_scoring`` already computes per-customer ``risk_factors``; the
report now surfaces them in a "Top risk drivers" column.

Tier 2: ``TOP_ITEM_LIMIT_DEFAULT = 5`` was applied as one knob to action
plans, members and accounts.  A Leader Team report therefore showed only 5 of
a manager's direct reports -- the one question the report exists to answer
required opening Excel.  The member table is now uncapped (with a safety
ceiling) while accounts and action plans stay at 5.

Tier 3: report generation/evaluation time was published as verified source
freshness.  Omitting the retrieval clock now fails closed to an honest
"unavailable" subtitle instead of stamping "Data as of <now>".
"""

from __future__ import annotations

import decision_report_delivery as delivery
from decision_report_delivery import (
    _r153_strip_source_chrome,
    _r153_top_risk_drivers,
    _visible_risk_decision_rows,
)


# ---------------------------------------------------------------------------
# Tier 1 -- customer-specific risk drivers
# ---------------------------------------------------------------------------


def test_round153_strip_source_chrome_removes_provenance_suffix() -> None:
    raw = (
        "2 critical/high adoption barriers [Source: CSConsole / Snowflake "
        "C360_CS_TASK_C_VW; Field(s): SEVERITY_C; Verification: Query by ID]"
    )
    assert _r153_strip_source_chrome(raw) == "2 critical/high adoption barriers"


def test_round153_top_risk_drivers_reads_risk_factors() -> None:
    profile = {
        "risk_factors": [
            "2 critical/high adoption barriers [Source: x]",
            "1 escalated TAC cases (P1/P2) [Source: y]",
            "3 unresolved action plans [Source: z]",
        ]
    }
    drivers = _r153_top_risk_drivers(profile, limit=2)
    assert "2 critical/high adoption barriers" in drivers
    assert "1 escalated TAC cases (P1/P2)" in drivers
    assert "[Source:" not in drivers


def test_round153_top_risk_drivers_falls_back_when_empty() -> None:
    assert "Risk_Components" in _r153_top_risk_drivers({"risk_factors": []})


def test_round153_decision_rows_have_a_drivers_column() -> None:
    facts = {
        "risk_summary": {"source_state": "available"},
        "risk_profiles": {
            "Alpha Corp": {
                "risk_band": "HIGH",
                "risk_score_0_100": 72.0,
                "risk_factors": ["2 critical/high adoption barriers [Source: a]"],
                "recommendations": ["Prioritize immediate executive review."],
            },
            "Beta Corp": {
                "risk_band": "HIGH",
                "risk_score_0_100": 70.0,
                "risk_factors": ["4 unresolved action plans [Source: b]"],
                "recommendations": ["Prioritize immediate executive review."],
            },
        },
    }
    rows = _visible_risk_decision_rows(facts)
    assert len(rows) == 2
    # 6 columns now: account, band, score, state, drivers, action.
    assert all(len(row) == 6 for row in rows)


def test_round153_same_band_customers_no_longer_identical() -> None:
    """The whole point of Tier 1: distinct drivers for same-band customers."""
    facts = {
        "risk_summary": {"source_state": "available"},
        "risk_profiles": {
            "Alpha Corp": {
                "risk_band": "HIGH",
                "risk_score_0_100": 72.0,
                "risk_factors": ["2 critical/high adoption barriers [Source: a]"],
                "recommendations": ["Prioritize immediate executive review."],
            },
            "Beta Corp": {
                "risk_band": "HIGH",
                "risk_score_0_100": 70.0,
                "risk_factors": ["4 unresolved action plans [Source: b]"],
                "recommendations": ["Prioritize immediate executive review."],
            },
        },
    }
    rows = _visible_risk_decision_rows(facts)
    drivers_by_account = {row[0]: row[4] for row in rows}
    assert drivers_by_account["Alpha Corp"] != drivers_by_account["Beta Corp"]
    assert "adoption barriers" in drivers_by_account["Alpha Corp"]
    assert "action plans" in drivers_by_account["Beta Corp"]


def test_round153_renderer_and_contract_headers_stay_in_lockstep() -> None:
    """Both the rendered table and its contract must carry the new column.

    They both call ``_visible_risk_decision_rows`` for the row data, so the
    only drift risk is the two hand-written header tuples.
    """
    source = delivery.__file__
    with open(source, encoding="utf-8") as handle:
        body = handle.read()
    assert body.count('"Top risk drivers",') == 2, (
        "the renderer header and the _expected_visible_word_tables header must "
        "both carry 'Top risk drivers'; a mismatch makes validate_word_semantics "
        "reject every document."
    )
