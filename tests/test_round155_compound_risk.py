"""Round 155 / B2 -- deterministic compound-risk correlation.

The single most differentiating customer-success signal: when a customer has
both an open adoption barrier AND an open TAC case in the *same* technology
area, one focused action can clear the connected cluster. The app's AI prompt
already asked for this (adoptiq_backend.py CORRELATION MANDATE); Round 155
makes it a deterministic, auditable ``risk_factors`` entry so it flows into the
concise report's Round 153 "Top risk drivers" column without the model
inventing it. It leads the driver list so it survives the top-2 truncation.

Zero oracle churn: the shipped offline fixtures are partial (drivers render
"Unavailable"), so this only enriches healthy renders.
"""

from __future__ import annotations

import pandas as pd

from decision_report_delivery import _r153_top_risk_drivers
from risk_scoring import (
    _r155_compound_risk_factor,
    compute_customer_risk_profile,
)


def _ab(*techs):
    return pd.DataFrame(
        [{"sub_technology": t, "SEVERITY_C": "Critical", "AB_STATUS_C": "Open"} for t in techs]
    )


def _tac(*techs, status="Open"):
    return pd.DataFrame(
        [{"sub_technology": t, "Severity": "P1", "Status": status} for t in techs]
    )


def test_round155_detects_same_tech_overlap() -> None:
    factor = _r155_compound_risk_factor(_ab("Webex Calling", "Control Hub"), _tac("Webex Calling", "Webex Calling"))
    assert factor is not None
    assert "Webex Calling" in factor
    assert "1 open barrier(s)" in factor
    assert "2 open TAC case(s)" in factor


def test_round155_no_overlap_returns_none() -> None:
    assert _r155_compound_risk_factor(_ab("Webex Calling"), _tac("Meetings")) is None


def test_round155_closed_cases_do_not_count() -> None:
    assert _r155_compound_risk_factor(_ab("Webex Calling"), _tac("Webex Calling", status="Resolved")) is None


def test_round155_unspecific_tech_is_ignored() -> None:
    """'Other / Unclassified' overlap must not fabricate a compound signal."""
    assert _r155_compound_risk_factor(_ab("Other / Unclassified"), _tac("Other / Unclassified")) is None


def test_round155_case_insensitive_match() -> None:
    factor = _r155_compound_risk_factor(_ab("Webex Calling"), _tac("webex calling"))
    assert factor is not None and "Webex Calling" in factor


def test_round155_picks_highest_combined_overlap() -> None:
    ab = _ab("Webex Calling", "Control Hub", "Control Hub")
    tac = _tac("Webex Calling", "Control Hub", "Control Hub", "Control Hub")
    factor = _r155_compound_risk_factor(ab, tac)
    assert "Control Hub" in factor  # 2 barriers + 3 cases beats 1 + 1


def test_round155_missing_tech_column_is_safe() -> None:
    ab = pd.DataFrame([{"SEVERITY_C": "Critical", "AB_STATUS_C": "Open"}])
    tac = pd.DataFrame([{"Severity": "P1", "Status": "Open"}])
    assert _r155_compound_risk_factor(ab, tac) is None


def test_round155_compound_leads_the_risk_factors() -> None:
    prof = compute_customer_risk_profile(
        "Acme",
        customer_ab=_ab("Webex Calling", "Webex Calling"),
        customer_csone=_tac("Webex Calling"),
    )
    assert prof["risk_factors"], "expected risk factors"
    assert prof["risk_factors"][0].startswith("Compound risk in Webex Calling")


def test_round155_no_compound_when_single_source() -> None:
    prof = compute_customer_risk_profile("Beta", customer_ab=_ab("Webex Calling"), customer_csone=None)
    assert not any("Compound risk" in f for f in prof["risk_factors"])


def test_round155_flows_into_driver_column_chrome_stripped() -> None:
    prof = compute_customer_risk_profile(
        "Acme",
        customer_ab=_ab("Webex Calling"),
        customer_csone=_tac("Webex Calling"),
    )
    drivers = _r153_top_risk_drivers(prof)
    assert "Compound risk in Webex Calling" in drivers
    assert "[Source:" not in drivers


# ---------------------------------------------------------------------------
# Round 155 -- deterministic driver-specific next-best-action
# ---------------------------------------------------------------------------

from risk_scoring import _r155_next_best_action  # noqa: E402


def _nba(**over):
    base = dict(
        compound=None, ab={}, support={}, pulse={}, actions={}, contract={},
        risk_band="MEDIUM", recommendations=["BAND BOILERPLATE"],
    )
    base.update(over)
    return _r155_next_best_action(**base)


def test_round155_action_priority_compound_wins() -> None:
    a = _nba(
        compound="Compound risk in Webex Calling: 1 open barrier(s) + 2 open TAC case(s)",
        support={"bems_count": 3, "escalated_count": 5},
    )
    assert "Webex Calling" in a and "single root-cause" in a


def test_round155_action_bems_before_escalated() -> None:
    a = _nba(support={"bems_count": 2, "escalated_count": 4})
    assert "BEMS" in a and "2" in a


def test_round155_action_escalated_specific() -> None:
    a = _nba(support={"escalated_count": 3})
    assert "3 escalated P1/P2" in a


def test_round155_action_aging_barriers() -> None:
    a = _nba(ab={"aging_open_count": 4, "critical_high_count": 9})
    assert "over 60 days" in a and "4" in a


def test_round155_action_falls_back_to_band() -> None:
    assert _nba() == "BAND BOILERPLATE"


def test_round155_same_band_customers_get_different_actions() -> None:
    """The whole point: same band, different acute signal, different action."""
    a = _nba(support={"escalated_count": 2})
    b = _nba(ab={"critical_high_count": 3})
    assert a != b


def test_round155_profile_exposes_next_best_action() -> None:
    prof = compute_customer_risk_profile(
        "Acme",
        customer_ab=_ab("Webex Calling"),
        customer_csone=_tac("Webex Calling"),
    )
    assert prof["next_best_action"]
    assert "Webex Calling" in prof["next_best_action"]


def test_round155_decision_row_uses_specific_action() -> None:
    """The concise report's action column must prefer next_best_action."""
    from decision_report_delivery import _visible_risk_decision_rows

    facts = {
        "risk_summary": {"source_state": "available"},
        "risk_profiles": {
            "Alpha": {
                "risk_band": "HIGH", "risk_score_0_100": 72.0,
                "risk_factors": ["2 critical/high adoption barriers [Source: a]"],
                "recommendations": ["BAND BOILERPLATE"],
                "next_best_action": "Run a weekly review to resolve the 3 escalated P1/P2 TAC case(s).",
            },
        },
    }
    rows = _visible_risk_decision_rows(facts)
    action = rows[0][5]
    assert "weekly review" in action
    assert "BAND BOILERPLATE" not in action
