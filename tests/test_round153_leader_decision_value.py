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


# ---------------------------------------------------------------------------
# Tier 2 -- the full team appears in a Leader Team report
# ---------------------------------------------------------------------------

from tests.test_round142_decision_report_delivery import (  # noqa: E402
    AS_OF as _T2_AS_OF,
    _team_fixture as _t2_team_fixture,
)


def _t2_facts():
    return delivery.build_report_facts(
        _t2_team_fixture(),
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=_T2_AS_OF,
    )


def test_round153_member_limit_is_higher_than_account_limit() -> None:
    assert delivery.MEMBER_ITEM_LIMIT_DEFAULT >= 12
    assert delivery.MEMBER_ITEM_LIMIT_DEFAULT > delivery.TOP_ITEM_LIMIT_DEFAULT


def test_round153_member_table_uses_member_limit_not_top_limit() -> None:
    """Source-shape: members slice on MEMBER_ITEM_LIMIT, accounts on TOP_ITEM."""
    with open(delivery.__file__, encoding="utf-8") as handle:
        body = handle.read()
    assert "member_summary_all[: max(int(MEMBER_ITEM_LIMIT_DEFAULT), 1)]" in body
    assert "account_summary_all[: max(int(top_item_limit), 1)]" in body


def test_round153_small_team_shows_every_member_no_omission() -> None:
    facts = _t2_facts()
    assert facts["member_summary"], "fixture should produce member rows"
    # The fixture's whole roster fits under the new limit, so nothing is hidden.
    assert facts["member_summary_omitted"] == 0
    assert len(facts["member_summary"]) == len(facts["member_summary_all"])


def test_round153_accounts_still_capped_at_top_limit() -> None:
    """Tier 2 must not widen the account cap."""
    facts = _t2_facts()
    assert len(facts["account_summary"]) <= delivery.TOP_ITEM_LIMIT_DEFAULT


# ---------------------------------------------------------------------------
# Tier 3 -- freshness fails closed when no retrieval clock is supplied
# ---------------------------------------------------------------------------


def test_round153_missing_retrieval_clock_is_not_available() -> None:
    """Omitting data_as_of_utc must NOT yield a verified 'available' state."""
    facts = delivery.build_report_facts(
        _t2_team_fixture(),
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=_T2_AS_OF,
        # data_as_of_utc / data_as_of_state deliberately omitted.
    )
    assert facts["data_as_of_state"] != "available"
    assert not facts["as_of_utc"], "public as-of must be blank when no clock was recorded"


def test_round153_missing_clock_renders_honest_unavailable_subtitle() -> None:
    facts = delivery.build_report_facts(
        _t2_team_fixture(),
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=_T2_AS_OF,
    )
    document = delivery.build_concise_word_document(facts)
    subtitle = document.paragraphs[2].text
    assert subtitle.startswith("Data as of unavailable")


def test_round153_explicit_clock_still_renders_verified_subtitle() -> None:
    """A caller that DOES record a retrieval clock is unaffected."""
    facts = delivery.build_report_facts(
        _t2_team_fixture(),
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=_T2_AS_OF,
        data_as_of_utc=_T2_AS_OF,
        data_as_of_state="available",
    )
    assert facts["data_as_of_state"] == "available"
    subtitle = delivery.build_concise_word_document(facts).paragraphs[2].text
    assert subtitle.startswith("Data as of 2026-08-03")


def test_round153_no_evaluation_clock_impersonates_freshness() -> None:
    """Source-shape: the removed fallback must not come back."""
    with open(delivery.__file__, encoding="utf-8") as handle:
        body = handle.read()
    # Match the executable assignment (8-space indent, own line), not the
    # explanatory comment that quotes the removed code.
    assert "\n        public_as_of_utc = as_of_ts.isoformat()\n" not in body, (
        "Round 153 / Tier 3: a missing retrieval clock must yield a blank "
        "public as-of, never the evaluation clock stamped as freshness."
    )
