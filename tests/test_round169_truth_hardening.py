"""Round 169 regressions for date and external-boolean truth contracts."""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

import canonical_metrics as metrics
import decision_report_delivery as delivery
import enhanced_admin_dashboard_v2 as admin
from scripts import run_local_acceptance_http as local_http


_HOSTILE_TRUE_VALUES = ("true", "false", "1", "0", 1, 0, None, [], {})


def _mixed_action_plans() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID": ["AP-001", "AP-002", "AP-003"],
            "SUBJECT_C": ["Missing date", "ISO date", "Display date"],
            "STATUS_C": ["Open", "Open", "Open"],
            "DUE_DATE_C": ["N/A", "2026-08-12T12:00:00Z", "Aug 13, 2026"],
            "CREATED_DATE": ["N/A", "2026-07-02T12:00:00Z", "Jul 3, 2026"],
        }
    )


def test_mixed_date_parser_stays_tz_aware_and_warning_free() -> None:
    source = pd.Series(["N/A", "2026-07-02T12:00:00Z", "Jul 3, 2026"])

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        parsed = metrics._r158_parse_dates_utc(source)  # noqa: SLF001

    assert str(parsed.dtype) == "datetime64[ns, UTC]"
    assert parsed.notna().tolist() == [False, True, True]
    assert captured == []


def test_activity_trend_handles_sentinel_first_mixed_dates() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result = metrics.build_activity_trend(
            action_plans_df=_mixed_action_plans(),
            ab_df=None,
            customer_pulse_df=None,
            tac_df=None,
            as_of="2026-08-14T00:00:00Z",
            days=90,
        )

    action_plan_coverage = result["coverage"].loc[
        result["coverage"]["Source"].eq("Action Plans")
    ].iloc[0]
    assert result["has_data"] is True
    assert int(result["series"]["Value"].sum()) == 2
    assert int(action_plan_coverage["Dateable_Records"]) == 2
    assert int(action_plan_coverage["Excluded_Invalid_or_Missing_Date"]) == 1
    assert captured == []


def test_action_plan_lifecycle_mixed_dates_are_warning_free() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        lifecycle = metrics.build_action_plan_lifecycle(
            _mixed_action_plans(),
            as_of="2026-08-14T00:00:00Z",
        )

    assert lifecycle["total"] == 3
    assert lifecycle["unknown_age"] == 1
    assert captured == []


def test_conflicting_duplicate_action_plan_is_quarantined_order_independently() -> None:
    rows = [
        {
            "ID": "AP-CONFLICT",
            "SUBJECT_C": "Conflicted plan",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-10",
            "CREATED_DATE_C": "2026-07-01",
        },
        {
            "ID": "AP-CONFLICT",
            "SUBJECT_C": "Conflicted plan",
            "STATUS_C": "Completed",
            "DUE_DATE_C": "2026-08-10",
            "CREATED_DATE_C": "2026-07-01",
        },
        {
            "ID": "AP-RETAINED",
            "SUBJECT_C": "Retained plan",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-20",
            "CREATED_DATE_C": "2026-07-15",
        },
    ]

    forward = metrics.build_action_plan_lifecycle(
        pd.DataFrame(rows),
        as_of="2026-08-14T00:00:00Z",
    )
    reverse = metrics.build_action_plan_lifecycle(
        pd.DataFrame(list(reversed(rows))),
        as_of="2026-08-14T00:00:00Z",
    )
    shuffled = metrics.build_action_plan_lifecycle(
        pd.DataFrame(rows).sample(frac=1, random_state=169).reset_index(drop=True),
        as_of="2026-08-14T00:00:00Z",
    )

    projected_keys = (
        "total",
        "open",
        "completed",
        "overdue",
        "due_soon",
        "conflicting_stable_id_count",
        "quarantined_conflicting_observation_count",
        "conflict_field_counts",
        "source_state",
        "source_state_detail",
    )
    assert {key: forward[key] for key in projected_keys} == {
        key: reverse[key] for key in projected_keys
    }
    assert {key: forward[key] for key in projected_keys} == {
        key: shuffled[key] for key in projected_keys
    }
    pd.testing.assert_frame_equal(forward["records"], reverse["records"])
    pd.testing.assert_frame_equal(forward["records"], shuffled["records"])
    assert forward["total"] == 1
    assert forward["conflicting_stable_id_count"] == 1
    assert forward["quarantined_conflicting_observation_count"] == 2
    assert forward["conflict_field_counts"] == {
        "status": 1,
        "due_date": 0,
        "created_date": 0,
    }
    assert forward["source_state"] == "partial"
    assert "AP-CONFLICT" not in forward["records"].to_string()


def test_compatible_duplicate_action_plan_selection_is_deterministic() -> None:
    rows = [
        {
            "ID": "AP-001",
            "SUBJECT_C": "Zeta fan-out row",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-10",
            "CREATED_DATE_C": "2026-07-01",
        },
        {
            "ID": "AP-001",
            "SUBJECT_C": "Alpha fan-out row",
            "STATUS_C": "In Progress",
            "DUE_DATE_C": "Aug 10, 2026",
            "CREATED_DATE_C": "2026-07-01T00:00:00Z",
        },
    ]

    forward = metrics.build_action_plan_lifecycle(
        pd.DataFrame(rows),
        as_of="2026-08-14T00:00:00Z",
    )
    reverse = metrics.build_action_plan_lifecycle(
        pd.DataFrame(list(reversed(rows))),
        as_of="2026-08-14T00:00:00Z",
    )

    pd.testing.assert_frame_equal(forward["records"], reverse["records"])
    assert forward["total"] == 1
    assert forward["conflicting_stable_id_count"] == 0
    assert forward["deduplicated_compatible_observation_count"] == 1


def test_complementary_action_plan_evidence_is_coalesced_before_publication() -> None:
    rows = [
        {
            "ID": "AP-COMPLEMENTARY",
            "ACCOUNT_ID_C": "ACC-001",
            "BU_NAME": "Acme Corporation",
            "SUBJECT_C": "",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-20",
            "CREATED_DATE_C": "2026-07-15",
            "NEXT_ACTION_OWNER_C": "",
            "DESCRIPTION_C": "",
        },
        {
            "ID": "AP-COMPLEMENTARY",
            "ACCOUNT_ID_C": "ACC-001",
            "BU_NAME": "Acme Corporation",
            "SUBJECT_C": "Populated plan title",
            "STATUS_C": "Open",
            "DUE_DATE_C": "Aug 20, 2026",
            "CREATED_DATE_C": "2026-07-15T00:00:00Z",
            "NEXT_ACTION_OWNER_C": "Named owner",
            "DESCRIPTION_C": "Populated source evidence",
        },
    ]

    forward = metrics.build_action_plan_lifecycle(
        pd.DataFrame(rows),
        as_of="2026-08-14T00:00:00Z",
    )
    reverse = metrics.build_action_plan_lifecycle(
        pd.DataFrame(list(reversed(rows))),
        as_of="2026-08-14T00:00:00Z",
    )

    pd.testing.assert_frame_equal(forward["records"], reverse["records"])
    assert forward["total"] == 1
    assert forward["display_conflicting_stable_id_count"] == 0
    retained = forward["records"].iloc[0]
    assert retained["AdoptIQ_Title"] == "Populated plan title"
    assert retained["NEXT_ACTION_OWNER_C"] == "Named owner"
    assert retained["DESCRIPTION_C"] == "Populated source evidence"

    facts = _build_conflict_facts(rows)
    sheets = delivery.build_source_data_sheets(facts)
    published = sheets["Action_Plans"].iloc[0]
    assert published["AdoptIQ_Title"] == "Populated plan title"
    assert published["NEXT_ACTION_OWNER_C"] == "Named owner"
    assert published["DESCRIPTION_C"] == "Populated source evidence"
    assert delivery.validate_cross_artifact_contract(facts, sheets)["ok"] is True


def test_non_lifecycle_action_plan_conflict_is_disclosed_without_raw_id() -> None:
    rows = [
        {
            "ID": "AP-DISPLAY-CONFLICT",
            "SUBJECT_C": "Alpha title",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-20",
            "CREATED_DATE_C": "2026-07-15",
        },
        {
            "ID": "AP-DISPLAY-CONFLICT",
            "SUBJECT_C": "Zeta title",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-20",
            "CREATED_DATE_C": "2026-07-15",
        },
    ]

    lifecycle = metrics.build_action_plan_lifecycle(
        pd.DataFrame(rows),
        as_of="2026-08-14T00:00:00Z",
    )

    assert lifecycle["total"] == 1
    assert lifecycle["source_state"] == "available"
    assert lifecycle["display_conflicting_stable_id_count"] == 1
    assert lifecycle["display_conflict_field_counts"] == {"SUBJECT_C": 1}
    assert "conflicting non-lifecycle display values" in lifecycle[
        "source_state_detail"
    ]
    assert "AP-DISPLAY-CONFLICT" not in lifecycle["source_state_detail"]

    facts = _build_conflict_facts(rows)
    coverage = facts["source_coverage"].loc[
        facts["source_coverage"]["Source_Sheet"].eq("Action_Plans")
    ].iloc[0]
    assert coverage["Source_State"] == "available"
    assert "conflicting non-lifecycle display values" in coverage["Detail"]
    sheets = delivery.build_source_data_sheets(facts)
    report_info = sheets["Report_Info"].loc[
        sheets["Report_Info"]["Item"].eq("Source_State:Action_Plans")
    ].iloc[0]
    assert "conflicting non-lifecycle display values" in report_info["Detail"]
    assert "AP-DISPLAY-CONFLICT" not in report_info["Detail"]


def _report_bundle(action_plan_rows: list[dict[str, object]]) -> dict[str, pd.DataFrame]:
    return {
        "subscriptions": pd.DataFrame(
            [
                {
                    "SUBSCRIPTION_ID": "SUB-001",
                    "ACCOUNT_ID_C": "ACC-001",
                    "BU_NAME": "Acme Corporation",
                }
            ]
        ),
        "action_plans": pd.DataFrame(action_plan_rows),
        "adoption_barriers": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "tac_cases": pd.DataFrame(),
        "success_priorities": pd.DataFrame(),
    }


def _build_conflict_facts(rows: list[dict[str, object]]) -> dict[str, object]:
    return delivery.build_report_facts(
        {"Alex Rivera": _report_bundle(rows)},
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-14T00:00:00Z",
        data_as_of_utc="2026-08-14T00:00:00Z",
        data_as_of_state="available",
        external_incidents=[],
        external_bugs=[],
    )


def test_report_facts_quarantine_conflicting_action_plan_before_publication() -> None:
    rows = [
        {
            "ID": "AP-CONFLICT",
            "ACCOUNT_ID_C": "ACC-001",
            "BU_NAME": "Acme Corporation",
            "SUBJECT_C": "Conflicted plan",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-10",
            "CREATED_DATE_C": "2026-07-01",
        },
        {
            "ID": "AP-CONFLICT",
            "ACCOUNT_ID_C": "ACC-001",
            "BU_NAME": "Acme Corporation",
            "SUBJECT_C": "Conflicted plan",
            "STATUS_C": "Completed",
            "DUE_DATE_C": "2026-08-10",
            "CREATED_DATE_C": "2026-07-01",
        },
        {
            "ID": "AP-RETAINED",
            "ACCOUNT_ID_C": "ACC-001",
            "BU_NAME": "Acme Corporation",
            "SUBJECT_C": "Retained plan",
            "STATUS_C": "Open",
            "DUE_DATE_C": "2026-08-20",
            "CREATED_DATE_C": "2026-07-15",
        },
    ]

    forward = _build_conflict_facts(rows)
    reverse = _build_conflict_facts(list(reversed(rows)))
    forward_sheets = delivery.build_source_data_sheets(forward)
    reverse_sheets = delivery.build_source_data_sheets(reverse)

    lifecycle = forward["action_plan_lifecycle"]
    assert lifecycle["total"] == 1
    assert lifecycle["conflicting_stable_id_count"] == 1
    assert lifecycle["quarantined_conflicting_observation_count"] == 2
    assert lifecycle["source_state"] == "partial"
    assert lifecycle["conflict_field_counts"] == {
        "status": 1,
        "due_date": 0,
        "created_date": 0,
    }
    assert forward["kpis"]["action_plans_total"] == 1
    assert set(forward_sheets["Action_Plans"]["Record_ID"]) == {"AP-RETAINED"}
    assert "AP-CONFLICT" not in forward_sheets["Action_Plans"].to_string()

    action_coverage = forward["source_coverage"].loc[
        forward["source_coverage"]["Source_Sheet"].eq("Action_Plans")
    ].iloc[0]
    assert action_coverage["Source_State"] == "partial"
    assert int(action_coverage["Record_Count"]) == 1
    assert "1 conflicting stable-ID Action Plan record(s)" in action_coverage["Detail"]
    assert "2 source observation(s)" in action_coverage["Detail"]
    assert "AP-CONFLICT" not in action_coverage["Detail"]
    report_info_state = forward_sheets["Report_Info"].loc[
        forward_sheets["Report_Info"]["Item"].eq("Source_State:Action_Plans")
    ].iloc[0]
    assert report_info_state["Value"] == "partial"
    assert "1 conflicting stable-ID Action Plan record(s)" in report_info_state[
        "Detail"
    ]
    assert "2 source observation(s)" in report_info_state["Detail"]
    assert all(
        "AP-CONFLICT" not in frame.to_string()
        for frame in forward_sheets.values()
    )

    forward_projection = {
        key: lifecycle[key]
        for key in (
            "total",
            "open",
            "completed",
            "conflicting_stable_id_count",
            "quarantined_conflicting_observation_count",
            "conflict_field_counts",
            "source_state",
            "source_state_detail",
        )
    }
    reverse_projection = {
        key: reverse["action_plan_lifecycle"][key]
        for key in forward_projection
    }
    assert forward_projection == reverse_projection
    pd.testing.assert_frame_equal(
        forward_sheets["Action_Plans"],
        reverse_sheets["Action_Plans"],
    )
    pd.testing.assert_frame_equal(
        forward_sheets["Report_Info"],
        reverse_sheets["Report_Info"],
    )
    assert delivery.validate_cross_artifact_contract(
        forward, forward_sheets
    )["ok"] is True
    assert delivery.validate_cross_artifact_contract(
        reverse, reverse_sheets
    )["ok"] is True


def test_report_facts_preserve_shared_attribution_for_compatible_fanout() -> None:
    shared_plan = {
        "ID": "AP-SHARED",
        "ACCOUNT_ID_C": "ACC-SHARED",
        "BU_NAME": "Shared Customer",
        "SUBJECT_C": "Shared plan",
        "STATUS_C": "Open",
        "DUE_DATE_C": "2026-08-20",
        "CREATED_DATE_C": "2026-07-15",
    }

    def member_bundle(name: str, email: str) -> dict[str, pd.DataFrame]:
        bundle = _report_bundle([dict(shared_plan)])
        bundle["subscriptions"] = pd.DataFrame(
            [
                {
                    "SUBSCRIPTION_ID": f"SUB-{name}",
                    "ACCOUNT_ID_C": "ACC-SHARED",
                    "BU_NAME": "Shared Customer",
                    "CSSM_NAME": name,
                    "CSSM_EMAIL": email,
                }
            ]
        )
        return bundle

    facts = delivery.build_report_facts(
        {
            "Alex Rivera": member_bundle("Alex Rivera", "alex@example.test"),
            "Morgan Lee": member_bundle("Morgan Lee", "morgan@example.test"),
        },
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-14T00:00:00Z",
        data_as_of_utc="2026-08-14T00:00:00Z",
        data_as_of_state="available",
        external_incidents=[],
        external_bugs=[],
    )
    sheets = delivery.build_source_data_sheets(facts)

    assert facts["action_plan_lifecycle"]["total"] == 1
    assert facts["action_plan_lifecycle"]["conflicting_stable_id_count"] == 0
    assert facts["action_plan_lifecycle"][
        "deduplicated_compatible_observation_count"
    ] >= 1
    assert len(sheets["Action_Plans"]) == 1
    assert sheets["Action_Plans"].iloc[0]["Attributed_Team_Members"] == (
        "Alex Rivera; Morgan Lee"
    )
    contract = delivery.validate_cross_artifact_contract(facts, sheets)
    assert contract["ok"], contract["errors"]


@pytest.mark.parametrize("value", _HOSTILE_TRUE_VALUES)
def test_admin_verbose_debug_requires_literal_true(value: object) -> None:
    verbose, count, failed = admin._admin_debug_metrics(  # noqa: SLF001
        {"verbose_debug": value, "snowflake_query_count": 0}
    )
    assert verbose is False
    assert count == 0
    assert failed is False


def test_admin_verbose_debug_accepts_literal_true() -> None:
    assert admin._admin_debug_metrics(  # noqa: SLF001
        {"verbose_debug": True, "snowflake_query_count": 0}
    ) == (True, 0, False)


@pytest.mark.parametrize("value", _HOSTILE_TRUE_VALUES)
def test_local_http_success_flag_requires_literal_true(value: object) -> None:
    assert local_http._literal_true(value) is False  # noqa: SLF001
    assert local_http.validate_provider_response("available", 200, {"ok": value})


def test_local_http_success_flag_accepts_literal_true() -> None:
    assert local_http._literal_true(True) is True  # noqa: SLF001
    assert local_http.validate_provider_response("available", 200, {"ok": True}) == []


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _WorkspaceClient:
    def get(self, _path: str, *, params: dict[str, object]) -> _Response:
        if params.get("scope_type") == "member" and "scope_value" not in params:
            return _Response(400, {"ok": False})
        return _Response(
            200,
            {
                "ok": "false",
                "preview": {
                    "schema": local_http.WORKSPACE_SCHEMA,
                    "report_type": params["report_type"],
                    "scope_type": params["scope_type"],
                    "live_validation_performed": False,
                    "source_mode": "local_fixture",
                    "expected_sources": [],
                    "limitations": [],
                },
            },
        )


def test_workspace_probe_rejects_truthy_string_success() -> None:
    projection, errors = local_http._run_workspace_preview_probe(  # noqa: SLF001
        _WorkspaceClient()
    )

    assert projection["ok"] is False
    assert all(case["ok"] is False for case in projection["cases"].values())
    assert len(errors) == len(local_http.WORKSPACE_PREVIEW_CASES)


class _ReportStartClient:
    def post_json(self, _path: str, _payload: dict[str, object]) -> _Response:
        return _Response(200, {"success": "false", "analysis_id": "analysis-1"})


def test_report_probe_rejects_truthy_string_start_success() -> None:
    projection, errors = local_http._run_report_probe(  # noqa: SLF001
        _ReportStartClient(),
        1,
        provider_state="available",
        expected_as_of_utc="2026-08-14T00:00:00Z",
        expected_action_plan_rows=0,
    )

    assert projection["completed"] is False
    assert errors == ["compact report did not start"]
