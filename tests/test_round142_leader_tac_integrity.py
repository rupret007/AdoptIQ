"""Round 142 TAC attribution and fixed-clock delivery regressions."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import decision_report_delivery as delivery
from leader_report_generator import LeaderReportGenerator


def _generator() -> LeaderReportGenerator:
    generator = LeaderReportGenerator.__new__(LeaderReportGenerator)
    generator.data_retrieved_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    return generator


def _team() -> dict:
    empty = pd.DataFrame()
    return {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "SUBSCRIPTION_ID": "SUB-1",
                        "ACCOUNT_ID_C": "ACC-1",
                        "BU_NAME": "Acme Inc",
                    }
                ]
            ),
            "customers": ["Acme Inc"],
            "action_plans": empty.copy(),
            "adoption_barriers": empty.copy(),
            "customer_pulse": empty.copy(),
            "success_priorities": empty.copy(),
            "tac_cases": empty.copy(),
        }
    }


def test_tac_window_uses_explicit_report_as_of_not_wall_clock() -> None:
    team = _team()
    cases = pd.DataFrame(
        [
            {
                "SR Number": "IN-BOUNDARY",
                "Subscription Reference Id": "SUB-1",
                "Date/Time Opened": "2026-05-05T12:00:00Z",
            },
            {
                "SR Number": "TOO-OLD",
                "Subscription Reference Id": "SUB-1",
                "Date/Time Opened": "2026-05-05T11:59:59Z",
            },
        ]
    )

    _generator().add_tac_cases_from_csone(team, cases, days=90)

    assert team["Alex Rivera"]["tac_cases"]["SR Number"].tolist() == ["IN-BOUNDARY"]


def test_stable_id_matching_works_without_customer_name_column() -> None:
    team = _team()
    cases = pd.DataFrame(
        [
            {
                "SR Number": "TAC-1",
                "Subscription Reference Id": "SUB-1",
                "Date/Time Opened": "2026-08-01T00:00:00Z",
            }
        ]
    )

    _generator().add_tac_cases_from_csone(team, cases, days=90)

    assert team["Alex Rivera"]["tac_cases"]["SR Number"].tolist() == ["TAC-1"]


def test_scope_validated_unmatched_tac_is_retained_as_unassigned_source_row() -> None:
    team = _team()
    cases = pd.DataFrame(
        [
            {
                "SR Number": "TAC-UNASSIGNED",
                "Subscription Reference Id": "UNKNOWN",
                "ACCOUNT_ID_C": "UNKNOWN",
                "Customer Name": "Scoped but unmapped customer",
                "Date/Time Opened": "2026-08-01T00:00:00Z",
            }
        ]
    )
    cases.attrs["scope_validated"] = True
    generator = _generator()

    generator.add_tac_cases_from_csone(team, cases, days=90)

    summary = generator._tac_match_summary
    assert summary["unmatched"] == 1
    assert summary["retained_unassigned"] == 1
    assert summary["excluded_unvalidated_scope"] == 0
    assert team["__Unassigned_Portfolio__"]["_adoptiq_unassigned_bundle"] is True
    facts = delivery.build_report_facts(
        team,
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=generator.data_retrieved_at,
    )
    assert facts["kpis"]["team_members"] == 1
    assert facts["kpis"]["tac_cases"] == 1
    assert facts["frames"]["tac_cases"].loc[0, "CSSM"] == "Unassigned / Portfolio"


def test_unvalidated_unmatched_tac_fails_closed() -> None:
    team = _team()
    cases = pd.DataFrame(
        [
            {
                "SR Number": "OUTSIDE-UNKNOWN-SCOPE",
                "Customer Name": "Unrelated",
                "Date/Time Opened": "2026-08-01T00:00:00Z",
            }
        ]
    )
    generator = _generator()

    generator.add_tac_cases_from_csone(team, cases, days=90)

    assert "__Unassigned_Portfolio__" not in team
    assert generator._tac_match_summary["excluded_unvalidated_scope"] == 1


def test_shared_account_tac_is_attributed_to_each_owner_without_portfolio_inflation() -> None:
    team = _team()
    team["Morgan Lee"] = {
        "subscriptions": pd.DataFrame(
            [
                {
                    "SUBSCRIPTION_ID": "SUB-2",
                    "ACCOUNT_ID_C": "ACC-1",
                    "BU_NAME": "Acme Inc",
                }
            ]
        ),
        "customers": ["Acme Inc"],
        "action_plans": pd.DataFrame(),
        "adoption_barriers": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "success_priorities": pd.DataFrame(),
        "tac_cases": pd.DataFrame(),
    }
    cases = pd.DataFrame(
        [
            {
                "SR Number": "TAC-SHARED",
                "ACCOUNT_ID_C": "ACC-1",
                "Customer Name": "Acme Inc",
                "Date/Time Opened": "2026-08-01T00:00:00Z",
            }
        ]
    )
    generator = _generator()

    generator.add_tac_cases_from_csone(team, cases, days=90)

    assert team["Alex Rivera"]["tac_cases"]["SR Number"].tolist() == ["TAC-SHARED"]
    assert team["Morgan Lee"]["tac_cases"]["SR Number"].tolist() == ["TAC-SHARED"]
    assert generator._tac_match_summary["shared_attribution_rows"] == 1
    assert generator._tac_match_summary["retained_total"] == 1
    assert generator._tac_match_summary["member_row_assignments"] == 2

    facts = delivery.build_report_facts(
        team,
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=generator.data_retrieved_at,
    )
    assert facts["kpis"]["tac_cases"] == 1
    assert facts["frames"]["tac_cases"].loc[0, "Attributed_Team_Members"] == "Alex Rivera; Morgan Lee"
    member_tac = {row[0]: row[5] for row in facts["member_summary_all"]}
    assert member_tac == {"Alex Rivera": 1, "Morgan Lee": 1}
