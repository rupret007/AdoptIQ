"""Round 142 canonical decision-report metric contracts."""

from __future__ import annotations

import pandas as pd

import canonical_metrics as cm


AS_OF = "2026-08-03T12:00:00Z"


def _action_plans() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ID": "AP-001",
                "SUBJECT_C": "Resolve onboarding gap",
                "STATUS_C": "Open",
                "DUE_DATE_C": "2026-08-01",
                "CREATED_DATE_C": "2026-07-01",
            },
            # Duplicate source ID must not inflate any bucket.
            {
                "ID": "AP-001",
                "SUBJECT_C": "Duplicate fan-out row",
                "STATUS_C": "Open",
                "DUE_DATE_C": "2026-08-01",
                "CREATED_DATE_C": "2026-07-01",
            },
            {
                "ID": "AP-002",
                "SUBJECT_C": "",
                "STATUS_C": "On Hold",
                "DUE_DATE_C": "2026-08-06",
                "CREATED_DATE_C": "2026-07-20",
            },
            {
                "ID": "AP-003",
                "SUBJECT_C": "Confirm adoption owner",
                "STATUS_C": "Completed - Successful",
                "DUE_DATE_C": "2026-07-15",
                "CREATED_DATE_C": "2026-07-02",
            },
            {
                "ID": "AP-004",
                "SUBJECT_C": "Schedule workshop",
                "STATUS_C": "In Progress",
                "DUE_DATE_C": "2026-08-10",
                "CREATED_DATE_C": "2026-07-30",
            },
            {
                "ID": "AP-005",
                "SUBJECT_C": "Investigate unlabeled state",
                "STATUS_C": "custom state",
                "CREATED_DATE_C": "2026-07-31",
            },
        ]
    )


def test_action_plan_lifecycle_is_distinct_partitioned_and_honest() -> None:
    lifecycle = cm.build_action_plan_lifecycle(_action_plans(), as_of=AS_OF, due_soon_days=14)

    assert lifecycle["total"] == 5
    assert lifecycle["open"] == 2
    assert lifecycle["overdue"] == 1
    assert lifecycle["due_soon"] == 1
    assert lifecycle["open_other"] == 0
    assert lifecycle["completed"] == 1
    assert lifecycle["blocked_on_hold"] == 1
    assert lifecycle["unknown"] == 1
    assert lifecycle["missing_title"] == 1
    assert lifecycle["field_selection"]["id"] == "ID"
    assert lifecycle["deduplication_rule"].startswith("distinct ID")

    partition = sum(lifecycle["bucket_counts"].values())
    assert partition == lifecycle["total"]
    missing_title = lifecycle["records"].loc[
        lifecycle["records"]["AdoptIQ_Record_ID"] == "AP-002"
    ].iloc[0]
    assert missing_title["AdoptIQ_Title"] == "Title unavailable"
    assert missing_title["AdoptIQ_Data_Quality"] == "Missing title"


def test_action_plan_lifecycle_requires_explicit_valid_clock() -> None:
    try:
        cm.build_action_plan_lifecycle(_action_plans(), as_of="not-a-date")
    except ValueError as exc:
        assert "explicit as_of" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("invalid implicit aging clock was accepted")


def test_activity_mix_does_not_count_bems_twice_or_turn_failure_into_zero() -> None:
    failed_tac = pd.DataFrame()
    failed_tac.attrs["fetch_error"] = "fixture connection unavailable"
    mix = cm.build_activity_mix(
        action_plans_df=_action_plans(),
        ab_df=pd.DataFrame([{"ID": "AB-1"}]),
        customer_pulse_df=pd.DataFrame([{"ID": "CP-1"}]),
        tac_df=failed_tac,
    )

    series = mix["series"].set_index("Category")
    assert series.loc["Action Plans", "Value"] == 5
    assert series.loc["TAC Cases", "Source_State"] == "failed"
    assert pd.isna(series.loc["TAC Cases", "Value"])
    assert mix["known_total"] == 7
    assert mix["total_state"] == "partial"
    assert "BEMS is a TAC subset" in mix["definition"]
    assert "BEMS" not in set(series.index)


def test_activity_trend_uses_deduped_ids_and_reports_date_coverage() -> None:
    trend = cm.build_activity_trend(
        action_plans_df=_action_plans(),
        ab_df=pd.DataFrame(),
        customer_pulse_df=pd.DataFrame(),
        tac_df=pd.DataFrame(),
        as_of=AS_OF,
        days=90,
    )

    ap_series = trend["series"].loc[trend["series"]["Source"] == "Action Plans"]
    # Five distinct records exist, but AP-005 has no invalid/missing date.
    assert int(ap_series["Value"].sum()) == 5
    coverage = trend["coverage"].set_index("Source")
    assert coverage.loc["Action Plans", "Records"] == 5
    assert coverage.loc["Action Plans", "Dateable_Records"] == 5
    assert coverage.loc["Adoption Barriers", "Trend_State"] == "zero"


def test_rows_without_stable_ids_are_retained_and_flagged() -> None:
    plans = pd.DataFrame(
        [
            {"SUBJECT_C": "First", "STATUS_C": "Open"},
            {"SUBJECT_C": "Second", "STATUS_C": "Open"},
        ]
    )
    lifecycle = cm.build_action_plan_lifecycle(plans, as_of=AS_OF)

    assert lifecycle["total"] == 2
    assert lifecycle["missing_record_id"] == 2
    assert lifecycle["field_selection"]["id"] is None
    assert set(lifecycle["records"]["AdoptIQ_Data_Quality"]) == {"Missing stable source ID"}


def test_mixed_missing_ids_reconcile_lifecycle_counter_and_activity_mix() -> None:
    plans = pd.DataFrame(
        [
            {"ID": "AP-1", "STATUS_C": "Open"},
            {"ID": "AP-1", "STATUS_C": "Open"},
            {"ID": None, "STATUS_C": "Open"},
            {"ID": "", "STATUS_C": "On Hold"},
        ]
    )
    lifecycle = cm.build_action_plan_lifecycle(plans, as_of=AS_OF)
    mix = cm.build_activity_mix(
        action_plans_df=lifecycle["records"],
        ab_df=pd.DataFrame(),
        customer_pulse_df=pd.DataFrame(),
        tac_df=pd.DataFrame(),
    )
    action_plan_value = mix["series"].set_index("Category").loc["Action Plans", "Value"]

    assert lifecycle["total"] == 3
    assert lifecycle["missing_record_id"] == 2
    assert cm.count_total_action_plans(plans) == 3
    assert action_plan_value == lifecycle["total"]
