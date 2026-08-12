"""Round 165 canonical reporting-window parity regressions."""

from __future__ import annotations

import pandas as pd

import canonical_metrics as cm
import decision_report_delivery as delivery


AS_OF = pd.Timestamp("2026-08-11T12:00:00Z")


def _boundary_action_plans() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": "BEFORE",
                "BU_NAME": "Acme",
                "SUBJECT_C": "Before the 90-day calendar window",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-05-13T12:00:00Z",
            },
            {
                "ID": "START",
                "BU_NAME": "Acme",
                "SUBJECT_C": "At the canonical window start",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-05-14T00:00:00Z",
            },
            {
                "ID": "END",
                "BU_NAME": "Acme",
                "SUBJECT_C": "Before the exact evaluation clock",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-08-11T11:59:59Z",
            },
            {
                "ID": "FUTURE",
                "BU_NAME": "Acme",
                "SUBJECT_C": "Later on the evaluation date",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-08-11T13:00:00Z",
            },
        ]
    )


def test_momentum_and_activity_chart_share_exact_window_boundaries() -> None:
    plans = _boundary_action_plans()

    start, end, days = cm.reporting_window_bounds(as_of=AS_OF, days=90)
    assert start == pd.Timestamp("2026-05-14T00:00:00Z")
    assert end == AS_OF
    assert days == 90

    momentum = cm.window_momentum(
        plans,
        date_columns=("CREATED_DATE_C",),
        as_of=AS_OF,
        days=90,
    )
    assert momentum is not None
    assert momentum["first_half"] == 1
    assert momentum["second_half"] == 1
    assert momentum["direction"] == "steady"

    trend = cm.build_activity_trend(
        action_plans_df=plans,
        ab_df=pd.DataFrame(),
        customer_pulse_df=pd.DataFrame(),
        tac_df=pd.DataFrame(),
        as_of=AS_OF,
        days=90,
    )
    action_series = trend["series"].loc[
        trend["series"]["Source"] == "Action Plans"
    ]
    assert int(action_series["Value"].sum()) == 2
    coverage = trend["coverage"].set_index("Source").loc["Action Plans"]
    assert int(coverage["Dateable_Records"]) == 2
    assert int(coverage["Excluded_Outside_Window"]) == 2
    assert trend["window_start_utc"] == start.isoformat()
    assert trend["as_of_utc"] == end.isoformat()


def test_word_momentum_and_chart_evidence_use_the_same_two_rows() -> None:
    facts = delivery.build_report_facts(
        {
            "Alex Rivera": {
                "subscriptions": pd.DataFrame(
                    [{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme"}]
                ),
                "action_plans": _boundary_action_plans(),
                "adoption_barriers": pd.DataFrame(),
                "customer_pulse": pd.DataFrame(),
                "tac_cases": pd.DataFrame(),
                "success_priorities": pd.DataFrame(),
            }
        },
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera team",
        manager_name="Alex Rivera",
        days=90,
        as_of=AS_OF,
        data_as_of_utc=AS_OF,
        data_as_of_state="available",
    )

    momentum = facts["decision_insights"]["window_momentum"]
    component = next(
        item
        for item in momentum["components"]
        if item["source_sheet"] == "Action_Plans"
    )
    assert component["first_half"] == 1
    assert component["second_half"] == 1
    selected_positions = momentum["source_positions"]["Action_Plans"]
    selected_ids = set(
        facts["frames"]["action_plans"].iloc[selected_positions]["AdoptIQ_Record_ID"]
    )
    assert selected_ids == {"START", "END"}

    trend_total = int(
        facts["activity_trend"]["series"]
        .loc[lambda frame: frame["Source"] == "Action Plans", "Value"]
        .sum()
    )
    assert trend_total == 2

    document = delivery.build_concise_word_document(facts)
    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)
    assert contract["ok"], contract["errors"]

    evidence = sheets["Evidence_Links"]
    momentum_rows = evidence.loc[
        (evidence["Evidence_Key"] == momentum["metric_key"])
        & (evidence["Source_Sheet"] == "Action_Plans")
    ]
    assert len(momentum_rows) == 2


def test_one_day_criteria_never_widens_momentum_to_a_hidden_second_day() -> None:
    plans = pd.DataFrame(
        [
            {
                "ID": "YESTERDAY",
                "BU_NAME": "Acme",
                "SUBJECT_C": "Outside the one-day calendar window",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-08-10T18:00:00Z",
            },
            {
                "ID": "MORNING",
                "BU_NAME": "Acme",
                "SUBJECT_C": "First half of the selected day",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-08-11T09:00:00Z",
            },
            {
                "ID": "AFTERNOON",
                "BU_NAME": "Acme",
                "SUBJECT_C": "Second half of the selected day",
                "STATUS_C": "Open",
                "CREATED_DATE_C": "2026-08-11T15:00:00Z",
            },
        ]
    )
    as_of = pd.Timestamp("2026-08-11T18:00:00Z")

    momentum = cm.window_momentum(
        plans,
        date_columns=("CREATED_DATE_C",),
        as_of=as_of,
        days=1,
    )
    assert momentum is not None
    assert momentum["window_days"] == 1
    assert momentum["half_days"] == 0.5
    assert momentum["first_half"] == momentum["second_half"] == 1

    trend = cm.build_activity_trend(
        action_plans_df=plans,
        ab_df=pd.DataFrame(),
        customer_pulse_df=pd.DataFrame(),
        tac_df=pd.DataFrame(),
        as_of=as_of,
        days=1,
    )
    assert trend["window_days"] == 1
    assert int(
        trend["series"]
        .loc[lambda frame: frame["Source"] == "Action Plans", "Value"]
        .sum()
    ) == 2

    facts = delivery.build_report_facts(
        {
            "Alex Rivera": {
                "subscriptions": pd.DataFrame(
                    [{"SUBSCRIPTION_ID": "S1", "BU_NAME": "Acme"}]
                ),
                "action_plans": plans,
                "adoption_barriers": pd.DataFrame(),
                "customer_pulse": pd.DataFrame(),
                "tac_cases": pd.DataFrame(),
                "success_priorities": pd.DataFrame(),
            }
        },
        report_type="Leader",
        scope_type="team",
        scope_value="Alex Rivera team",
        manager_name="Alex Rivera",
        days=1,
        as_of=as_of,
        data_as_of_utc=as_of,
        data_as_of_state="available",
    )
    insight = facts["decision_insights"]["window_momentum"]
    component = next(
        item
        for item in insight["components"]
        if item["source_sheet"] == "Action_Plans"
    )
    assert component["window_days"] == 1
    assert component["first_half"] == component["second_half"] == 1
    selected_ids = set(
        facts["frames"]["action_plans"]
        .iloc[insight["source_positions"]["Action_Plans"]]["AdoptIQ_Record_ID"]
    )
    assert selected_ids == {"MORNING", "AFTERNOON"}
