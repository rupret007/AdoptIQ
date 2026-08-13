"""Round 167 explicit-clock CSOne window integrity regressions."""

from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm
import decision_report_delivery as delivery
from adoptiq_backend import _apply_scope_filter_csone
from data_normalization import add_case_lifecycle_fields
from tests.test_round142_decision_report_delivery import _team_fixture


def test_strict_csone_window_uses_explicit_as_of_and_rejects_future_rows() -> None:
    as_of = pd.Timestamp("2026-08-03T21:00:00Z")
    frame = pd.DataFrame(
        [
            {
                "Subscription ID": "Sub1001",
                "customer_name": "Acme Corp",
                "Date/Time Opened": as_of - pd.Timedelta(90, unit="D"),
                "SR Number": "AT-LOWER-BOUNDARY",
            },
            {
                "Subscription ID": "Sub1001",
                "customer_name": "Acme Corp",
                "Date/Time Opened": as_of,
                "SR Number": "AT-UPPER-BOUNDARY",
            },
            {
                "Subscription ID": "Sub1001",
                "customer_name": "Acme Corp",
                "Date/Time Opened": as_of - pd.Timedelta(90 * 86_400 + 1, unit="s"),
                "SR Number": "TOO-OLD",
            },
            {
                "Subscription ID": "Sub1001",
                "customer_name": "Acme Corp",
                "Date/Time Opened": as_of + pd.Timedelta(1, unit="s"),
                "SR Number": "FUTURE",
            },
            {
                "Subscription ID": "Sub1001",
                "customer_name": "Acme Corp",
                "Date/Time Opened": None,
                "SR Number": "MISSING-DATE",
            },
        ]
    )

    scoped = _apply_scope_filter_csone(
        frame,
        "All",
        90,
        ["Sub1001"],
        ["Acme Corp"],
        include_all_cases=False,
        as_of=as_of,
    )

    assert scoped["SR Number"].tolist() == [
        "AT-LOWER-BOUNDARY",
        "AT-UPPER-BOUNDARY",
    ]
    assert scoped.attrs["time_window_days"] == 90
    assert scoped.attrs["time_window_excluded_before"] == 1
    assert scoped.attrs["time_window_excluded_after"] == 1
    assert scoped.attrs["time_window_excluded_invalid_date"] == 1


def test_tac_lifecycle_ages_use_the_explicit_report_clock() -> None:
    frame = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-24T21:00:00Z",
            },
            {
                "BU_NAME": "Acme",
                "Case Status": "Closed",
                "Date/Time Opened": "2026-07-01T21:00:00Z",
                "Date/Time Closed": "2026-08-01T21:00:00Z",
            },
        ]
    )

    first = add_case_lifecycle_fields(frame, as_of="2026-08-03T21:00:00Z")
    later_host_run = add_case_lifecycle_fields(frame, as_of="2026-08-03T21:00:00Z")

    assert first["open_age_days"].tolist()[0] == 10
    assert first["closed_age_days"].tolist()[1] == 2
    pd.testing.assert_series_equal(
        first["open_age_days"], later_host_run["open_age_days"]
    )
    pd.testing.assert_series_equal(
        first["closed_age_days"], later_host_run["closed_age_days"]
    )


def test_tac_lifecycle_rejects_an_invalid_explicit_clock() -> None:
    frame = pd.DataFrame(
        [{"BU_NAME": "Acme", "Case Status": "Open", "Date/Time Opened": "2026-08-01"}]
    )

    with pytest.raises(ValueError, match="valid explicit as_of"):
        add_case_lifecycle_fields(frame, as_of="not-a-clock")


def test_canonical_source_data_recomputes_tac_ages_from_report_clock() -> None:
    team_data = _team_fixture()
    tac = team_data["Alex Rivera"]["tac_cases"].copy()
    tac["open_age_days"] = 9999
    tac["closed_age_days"] = 9999
    team_data["Alex Rivera"]["tac_cases"] = tac

    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-03T12:00:00Z",
    )

    public_tac = facts["frames"]["tac_cases"]
    assert 9999 not in set(
        pd.to_numeric(public_tac["open_age_days"], errors="coerce").dropna()
    )
    assert 9999 not in set(
        pd.to_numeric(public_tac["closed_age_days"], errors="coerce").dropna()
    )


def test_tac_operating_health_uses_honest_independent_denominators() -> None:
    tac = pd.DataFrame(
        [
            {
                "SR Number": "1",
                "Date/Time Opened": "2026-07-01T00:00:00Z",
                "Date/Time Closed": "2026-07-02T00:00:00Z",
                "# of Case Owner Changes": 0,
            },
            {
                "SR Number": "2",
                "Date/Time Opened": "2026-06-01T00:00:00Z",
                "Date/Time Closed": "2026-06-11T00:00:00Z",
                "# of Case Owner Changes": 2,
            },
            {
                "SR Number": "3",
                "Date/Time Opened": "2026-05-01T00:00:00Z",
                "Date/Time Closed": "2026-05-21T00:00:00Z",
                "# of Case Owner Changes": "",
            },
            {
                "SR Number": "4",
                "Date/Time Opened": "2026-08-01T00:00:00Z",
                "Date/Time Closed": "2026-08-04T00:00:00Z",
                "# of Case Owner Changes": -1,
            },
        ]
    )

    health = cm.tac_operating_health(tac, as_of="2026-08-03T12:00:00Z")

    assert health is not None
    assert health["closed_case_count"] == 3
    assert health["close_time_median_days"] == 10.0
    assert health["close_time_p90_days"] == 18.0
    assert health["ownership_observed_count"] == 2
    assert health["ownership_churn_count"] == 1
    assert health["ownership_churn_rate_percent"] == 50.0
    assert health["closure_positions"] == [0, 1, 2]
    assert health["ownership_positions"] == [0, 1]
