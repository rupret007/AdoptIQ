"""Round 167 build entrypoints require the extensive local simulation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import canonical_report_adapter as adapter
import decision_report_delivery as delivery
from app_simple import (
    _r65_filter_customer_tagged_incidents,
    _r167_renewal_commercial_fact_frame,
    analyze_feature_requests,
)
from leader_report_generator import LeaderReportGenerator
from local_acceptance_lab import build_scenario_bundle
from local_acceptance_runtime import (
    _subscription_payload,
    _with_fixture_member_attribution,
)


ROOT = Path(__file__).resolve().parents[1]


def test_every_mac_candidate_runs_simulation_before_corpus_bake_and_build() -> None:
    source = (ROOT / "build_mac_dmg.sh").read_text(encoding="utf-8")

    simulation = source.index('scripts/run_round146_acceptance.py "${PREBUILD_ARGS[@]}"')
    bake = source.index("scripts/bake_corpus.py")
    native_build = source.index("\n./build_mac.sh")

    assert simulation < bake < native_build
    assert "every direct macOS candidate" in source
    assert "PREBUILD_SOURCE_STATE" in source
    assert "ADOPTIQ_CSONE_CORPUS_DIR" in source
    assert "ADOPTIQ_CSONE_REPLAY_MAX_ROWS" in source
    assert "ADOPTIQ_RELEASE_GATE=1 requires ADOPTIQ_CSONE_CORPUS_DIR" in source


def test_windows_build_runs_simulation_before_credentials_version_and_pyinstaller() -> None:
    source = (ROOT / "build_pc.bat").read_text(encoding="utf-8")

    simulation = source.index("scripts\\run_round146_acceptance.py")
    credentials = source.index('"%PYTHON%" embed_credentials.py')
    version = source.index('"%PYTHON%" update_version_pc.py')
    pyinstaller = source.index('"%PYTHON%" -m PyInstaller')

    assert simulation < credentials < version < pyinstaller
    assert "ADOPTIQ_CSONE_CORPUS_DIR" in source
    assert "production Windows builds require ADOPTIQ_CSONE_CORPUS_DIR" in source
    assert "mandatory pre-build production simulation failed" in source


def test_ci_quality_job_gates_both_native_build_jobs() -> None:
    source = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    simulation = source.index("Run extensive pre-build production simulation")
    mac_job = source.index("build-mac:")
    windows_job = source.index("build-windows:")

    assert simulation < mac_job
    assert simulation < windows_job
    assert source.count("needs: quality-checks") >= 2


def test_all_managers_leader_generator_uses_the_entire_deduplicated_roster() -> None:
    generator = LeaderReportGenerator.__new__(LeaderReportGenerator)
    generator.team_roster = [
        ("Manager One", "Member One", "one@example.invalid"),
        ("Manager Two", "Member Two", "two@example.invalid"),
        ("Manager Two", "Duplicate Owner", "ONE@example.invalid"),
    ]

    assert generator._get_direct_reports("All Managers") == [
        {"name": "Member One", "email": "one@example.invalid"},
        {"name": "Member Two", "email": "two@example.invalid"},
    ]
    assert generator._get_direct_reports("Manager Two") == [
        {"name": "Member Two", "email": "two@example.invalid"},
        {"name": "Duplicate Owner", "email": "ONE@example.invalid"},
    ]


def test_healthy_subscription_replay_populates_subtechnology_from_product() -> None:
    payload = _subscription_payload(build_scenario_bundle("healthy"), "SUB-002", 90)

    assert payload["found"] is True
    assert payload["technology"] == "Webex Meetings"
    assert payload["sub_technology"] == "Webex Meetings"


def test_name_only_compact_risk_is_ambiguous_across_stable_account_ids() -> None:
    subscriptions = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "ACC-001", "BU_NAME": "Acme Corporation"},
            {"ACCOUNT_ID_C": "ACC-004", "BU_NAME": "ACME CORPORATION"},
            {"ACCOUNT_ID_C": "ACC-002", "BU_NAME": "Beta Industries"},
        ]
    )

    assert adapter._ambiguous_subscription_customer_labels(  # noqa: SLF001
        subscriptions
    ) == {
        "acmecorporation": ("ACME CORPORATION", "Acme Corporation"),
    }


def test_canonical_team_summary_resolves_roster_email_to_display_name() -> None:
    source = pd.DataFrame([{"CSSM_EMAIL": "OWNER@EXAMPLE.INVALID", "Record_ID": "AP-001"}])

    team_data = adapter._build_attributed_team_data(  # noqa: SLF001
        {"action_plans": source},
        member_display_names_by_email={
            "owner@example.invalid": "Alex Rivera",
        },
    )

    assert list(team_data) == ["Alex Rivera"]
    assert team_data["Alex Rivera"]["action_plans"].iloc[0]["CSSM_EMAIL"] == ("OWNER@EXAMPLE.INVALID")


def test_fixture_member_attribution_exercises_real_team_rollups() -> None:
    source = pd.DataFrame(
        [
            {"Record_ID": "AP-001", "FIXTURE_MEMBER": "Alex Rivera"},
            {
                "Record_ID": "AP-002",
                "FIXTURE_MEMBER": "Morgan Lee",
                "CSSM_EMAIL": "preserved@example.invalid",
            },
            {"Record_ID": "AP-003", "FIXTURE_MEMBER": "Roster Gap"},
        ]
    )
    source.attrs["source_state"] = "available"

    projected = _with_fixture_member_attribution(
        source,
        {
            "Alex Rivera": "fixture.owner1@example.invalid",
            "Morgan Lee": "fixture.owner2@example.invalid",
        },
    )

    assert projected["CSSM_EMAIL"].tolist() == [
        "fixture.owner1@example.invalid",
        "preserved@example.invalid",
        "",
    ]
    assert projected.attrs["source_state"] == "available"
    assert projected.attrs["fixture_attribution_unmapped_rows"] == 1

    team_data = adapter._build_attributed_team_data(  # noqa: SLF001
        {"action_plans": projected},
        member_display_names_by_email={
            "fixture.owner1@example.invalid": "Alex Rivera",
            "preserved@example.invalid": "Morgan Lee",
        },
    )
    assert set(team_data) == {
        "Alex Rivera",
        "Morgan Lee",
        adapter._UNASSIGNED_BUNDLE,  # noqa: SLF001 - honest gap contract
    }


def test_adapter_projects_scoped_account_ownership_into_member_sources() -> None:
    subscriptions = pd.DataFrame(
        [
            {
                "ACCOUNT_ID_C": "ACC-001",
                "CSSM_EMAIL": "fixture.owner2@example.invalid",
            }
        ]
    )
    action_plans = pd.DataFrame([{"Record_ID": "AP-001", "ACCOUNT_ID_C": "ACC-001"}])
    tac_cases = pd.DataFrame([{"Record_ID": "SR-001", "ACCOUNT_ID_C": "ACC-001"}])
    priorities = pd.DataFrame(
        [
            {
                "Record_ID": "SP-001",
                "ACCOUNT_ID_C": "ACC-001",
                "CSSM": "Alex Rivera",
            }
        ]
    )

    projected, counts = adapter._project_scoped_account_attribution(  # noqa: SLF001
        {
            "subscriptions": subscriptions,
            "action_plans": action_plans,
            "tac_cases": tac_cases,
            "success_priorities": priorities,
        },
        member_display_names_by_email={
            "fixture.owner2@example.invalid": "Morgan Lee",
        },
    )

    assert counts == {
        "action_plans": 1,
        "success_priorities": 1,
        "tac_cases": 1,
    }
    for source_key in ("action_plans", "tac_cases", "success_priorities"):
        assert projected[source_key].loc[0, "Attributed_Team_Members"] == ("Morgan Lee")
        assert projected[source_key].loc[0, "Attribution_Basis"] == ("Scoped subscription account ownership")
    assert projected["success_priorities"].loc[0, "Source_Reported_Attribution"] == "Alex Rivera"

    team_data = adapter._build_attributed_team_data(  # noqa: SLF001
        projected,
        member_display_names_by_email={
            "fixture.owner2@example.invalid": "Morgan Lee",
        },
    )
    assert list(team_data) == ["Morgan Lee"]
    assert len(team_data["Morgan Lee"]["action_plans"]) == 1
    assert len(team_data["Morgan Lee"]["tac_cases"]) == 1


def test_shared_missing_id_row_keeps_union_without_duplicate_public_rows() -> None:
    frames, _counts = adapter._project_scoped_account_attribution(  # noqa: SLF001
        {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "ACCOUNT_ID_C": "ACC-SHARED",
                        "CSSM": "Alex Rivera",
                    },
                    {
                        "ACCOUNT_ID_C": "ACC-SHARED",
                        "CSSM": "Morgan Lee",
                    },
                ]
            ),
            "action_plans": pd.DataFrame(
                [
                    {
                        "ID": "",
                        "ACCOUNT_ID_C": "ACC-SHARED",
                        "SUBJECT_C": "Retained malformed source row",
                    }
                ]
            ),
        }
    )
    team_data = adapter._build_attributed_team_data(frames)  # noqa: SLF001

    aggregated = delivery.aggregate_team_frames(
        team_data,
        scope_type="team",
        scope_value="All Managers team",
    )

    action_plans = aggregated["action_plans"]
    assert len(action_plans) == 1
    assert action_plans.loc[0, "Attributed_Team_Members"] == ("Alex Rivera; Morgan Lee")
    assert "_AdoptIQ_Partition_Row_Key" not in action_plans.columns


def test_report_specific_fact_surface_is_decision_first_and_manager_readable() -> None:
    rows = []
    facts_by_field = (
        ("Analysis_Date", "2026-08-13T01:45:41.482280Z"),
        ("Next_Review_Date", "2026-09-12T01:45:41.482284+00:00"),
        ("Overall_Risk_Score", 5.4),
        ("Recommendation", "Confirm the renewal decision owner"),
    )
    for position, (field, value) in enumerate(facts_by_field, 1):
        rows.append(
            {
                "Legacy_Record_Type": "Family-specific reported fact",
                "Metric_Key": f"legacy.family.renewal.summary.r2.{field}.{position}",
                "Legacy_Fact_Value": value,
                "Legacy_Fact_Label": field,
                "Legacy_Source_Field": field,
                "Legacy_Source_Sheet": "Renewal_Summary",
                "Legacy_Source_Row_Number": 2,
                "BU_NAME": "Acme Corporation",
            }
        )
    facts = {
        "report_type": "Renewal",
        "legacy_adapter": {"report_family": "renewal"},
        "frames": {"subscriptions": pd.DataFrame(rows)},
        "risk_summary": {"source_state": "available"},
    }

    bundle = delivery._report_specific_decision_fact_bundle(facts)  # noqa: SLF001
    visible = bundle["rows"]
    visible_labels = [str(row["fact"]) for row in visible]
    visible_values = [str(row["reported_value"]) for row in visible]

    assert visible[0]["category"] == "risk"
    assert visible[1]["category"] == "recommendation"
    assert sum(row["category"] == "analysis_metadata" for row in visible) == 1
    assert all("_" not in label for label in visible_labels)
    assert any("overall risk score (0–10)" in label for label in visible_labels)
    assert not any("T01:45" in value for value in visible_values)
    assert "2026-09-12" in visible_values


def test_renewal_commercial_word_facts_keep_status_currency_probability_and_contract_identity() -> None:
    fields = (
        ("Contract Number", "CON-44"),
        ("Service End Date", "2026-10-15"),
        ("ARR Amount", 250000.0),
        ("Currency", "EUR"),
        ("Service Tier", "Tier 2"),
        ("Renewal Status", "In Review"),
        ("Source-Reported Renewal Probability (%)", 74.0),
    )
    rows = [
        {
            "Legacy_Record_Type": "Family-specific reported fact",
            "Metric_Key": f"legacy.family.renewal.commercial.r2.{field}.{position}",
            "Legacy_Fact_Value": value,
            "Legacy_Fact_Label": field,
            "Legacy_Source_Field": field,
            "Legacy_Source_Sheet": "Renewal_Commercial_Facts",
            "Legacy_Source_Row_Number": 2,
            "Customer Name": "Acme Corporation",
        }
        for position, (field, value) in enumerate(fields, 1)
    ]
    facts = {
        "report_type": "Renewal",
        "legacy_adapter": {"report_family": "renewal"},
        "frames": {"subscriptions": pd.DataFrame(rows)},
        "risk_summary": {"source_state": "available"},
    }

    bundle = delivery._report_specific_decision_fact_bundle(facts)  # noqa: SLF001
    by_category = {row["category"]: row for row in bundle["rows"]}

    assert by_category["subscription_identity"]["reported_value"] == "CON-44"
    assert by_category["renewal_date"]["reported_value"] == "2026-10-15"
    assert by_category["arr"]["fact"].endswith("ARR (source currency)")
    assert by_category["arr"]["reported_value"] == "EUR 250,000"
    assert by_category["renewal_probability"]["reported_value"] == "74%"
    assert by_category["status"]["reported_value"] == "In Review"


def test_report_families_have_distinct_word_surface_policies() -> None:
    base = delivery.build_report_facts(
        {
            "Alex Rivera": {
                "subscriptions": pd.DataFrame(
                    [{"SUBSCRIPTION_ID": "SUB-1", "ACCOUNT_ID_C": "ACC-1", "BU_NAME": "Acme"}]
                ),
                "action_plans": pd.DataFrame(),
                "adoption_barriers": pd.DataFrame(),
                "customer_pulse": pd.DataFrame(),
                "tac_cases": pd.DataFrame(),
                "success_priorities": pd.DataFrame(),
            }
        },
        report_type="Leader",
        scope_type="team",
        scope_value="Manager team",
        manager_name="Manager",
        days=90,
        as_of="2026-08-03T12:00:00Z",
    )
    policies = {}
    for report_type in ("Leader", "Compact", "Comprehensive"):
        candidate = dict(base)
        candidate["report_type"] = report_type
        policies[report_type] = delivery._report_surface_policy(candidate)  # noqa: SLF001

    assert policies["Compact"]["kpi_heading"] == "Decision Snapshot"
    assert policies["Compact"]["show_source_coverage"] is False
    assert policies["Compact"]["show_detailed_action_rollup"] is False
    assert policies["Compact"]["insight_limit"] == 2
    assert policies["Compact"]["detailed_action_limit"] == 0
    assert policies["Leader"]["kpi_heading"] == "Management Snapshot"
    assert policies["Leader"]["show_decision_signals"] is False
    assert policies["Leader"]["insight_limit"] == 3
    assert policies["Leader"]["detailed_action_limit"] == 3
    assert policies["Comprehensive"]["show_decision_signals"] is True
    assert policies["Comprehensive"]["insight_limit"] is None
    assert policies["Comprehensive"]["detailed_action_limit"] == 3


def test_manager_charts_add_detail_only_when_one_source_hides_the_rest() -> None:
    assert delivery._chart_needs_detail_panel([461, 7, 3, 3]) is True  # noqa: SLF001
    assert delivery._chart_needs_detail_panel([16, 12, 7, 3]) is False  # noqa: SLF001
    assert delivery._chart_needs_detail_panel([0, 0, 461]) is False  # noqa: SLF001


def test_activity_trend_detail_chart_keeps_a_zero_baseline() -> None:
    source = Path(delivery.__file__).read_text(encoding="utf-8")

    assert 'panel_axis.set_ylim(bottom=0)' in source


def test_source_coverage_inventory_uses_compact_fixed_geometry() -> None:
    source = Path(delivery.__file__).read_text(encoding="utf-8")

    assert "coverage_table = add_banded_top_n_table(" in source
    assert "cell_margin_twips=45" in source


def test_action_plan_rollup_is_bounded_and_uses_compact_geometry() -> None:
    source = Path(delivery.__file__).read_text(encoding="utf-8")

    assert "def _detailed_action_rollup_rows(" in source
    assert "This Word view intentionally limits detailed rows" in source
    assert '_artifact_reference_text("Metric_Lineage", "kpi.action_plans_total")' in source
    assert "action_rollup_table," in source


def test_leader_intervention_and_comprehensive_review_focus_are_lineage_backed() -> None:
    from tests.test_round142_decision_report_delivery import _team_fixture

    leader = delivery.build_report_facts(
        _team_fixture(),
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-03T12:00:00Z",
    )
    leader_rows = delivery._leader_intervention_rows(leader)  # noqa: SLF001
    assert leader_rows
    assert any("owner, recovery date" in str(row[6]) for row in leader_rows)
    leader_lineage = leader["metric_lineage"]
    intervention_lineage = leader_lineage.loc[
        leader_lineage["Metric_Key"].astype(str).str.endswith(".manager_intervention")
    ]
    assert len(intervention_lineage) == len(leader_rows)
    assert set(intervention_lineage["Metric_Value"].astype(str)) == {
        str(row[6]) for row in leader_rows
    }

    comprehensive = delivery.build_report_facts(
        _team_fixture(),
        report_type="Comprehensive",
        scope_type="customer",
        scope_value="Acme Corporation",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-03T12:00:00Z",
    )
    review_rows = delivery._comprehensive_account_evidence_rows(comprehensive)  # noqa: SLF001
    assert review_rows and review_rows[0][6]
    review_lineage = comprehensive["metric_lineage"].loc[
        comprehensive["metric_lineage"]["Metric_Key"].astype(str).str.endswith(".review_focus")
    ]
    assert set(review_lineage["Metric_Value"].astype(str)) == {
        str(row[6]) for row in review_rows
    }


def test_renewal_commercial_projection_keeps_account_currency_and_source_probability() -> None:
    subscriptions = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "ACC-001", "BU_NAME": "Acme Corporation"},
            {"ACCOUNT_ID_C": "ACC-002", "BU_NAME": "Beta Industries"},
        ]
    )
    enhanced = {
        "contracts": {
            "details": [
                {
                    "contract": "CON-2",
                    "account_id": "ACC-002",
                    "end_date": "2026-10-15",
                    "service_tier": "Tier 2",
                    "arr": 250000.0,
                    "currency": "EUR",
                },
                {
                    "contract": "CON-1",
                    "account_id": "ACC-001",
                    "end_date": "2026-09-01",
                    "service_tier": "Tier 1",
                    "arr": 500000.0,
                    "currency": "USD",
                },
                {
                    "contract": "OUTSIDE",
                    "account_id": "ACC-999",
                    "end_date": "2026-08-30",
                    "arr": 999999.0,
                    "currency": "USD",
                },
            ]
        },
        "renewals": {
            "details": [
                {
                    "contract": "CON-1",
                    "account_id": "ACC-001",
                    "status": "At Risk",
                    "probability": 58.0,
                },
                {
                    "contract": "CON-2",
                    "account_id": "ACC-002",
                    "status": "In Review",
                    "probability": 74.0,
                },
            ]
        },
    }

    projected = _r167_renewal_commercial_fact_frame(enhanced, subscriptions)

    assert projected["Contract Number"].tolist() == ["CON-1", "CON-2"]
    assert projected["Customer Name"].tolist() == ["Acme Corporation", "Beta Industries"]
    assert projected["Currency"].tolist() == ["USD", "EUR"]
    assert projected["ARR Amount"].tolist() == [500000.0, 250000.0]
    assert projected["Source-Reported Renewal Probability (%)"].tolist() == [58.0, 74.0]


def test_customer_search_expands_authoritative_dsm_owner_slots() -> None:
    import adoptiq_backend as backend

    rows = [
        {
            "SUBSCRIPTION_ID": "SUB-001",
            "ACCOUNT_ID_C": "ACC-001",
            "BU_NAME": "Acme Corporation",
            "PRIMARY_DSM_EMAIL": "primary@example.invalid",
            "DSM_EMAIL1": "secondary@example.invalid",
            "NEXT_ACTION_OWNER_EMAIL": "not-an-account-owner@example.invalid",
        }
    ]

    columns = backend._dsm_attribution_email_columns(  # noqa: SLF001
        {
            "PRIMARY_DSM_EMAIL",
            "DSM_EMAIL1",
            "NEXT_ACTION_OWNER_EMAIL",
        }
    )
    projected = backend._expand_dsm_attribution_rows(  # noqa: SLF001
        rows,
        attribution_columns=columns,
    )

    assert columns == ("PRIMARY_DSM_EMAIL", "DSM_EMAIL1")
    assert [row["CSSM_EMAIL"] for row in projected] == [
        "primary@example.invalid",
        "secondary@example.invalid",
    ]
    assert all("NEXT_ACTION_OWNER_EMAIL" in row for row in projected)


def test_comprehensive_gets_evidence_deep_dive_without_unsupported_risk_ranking() -> None:
    facts = delivery.build_report_facts(
        {
            "Alex Rivera": {
                "subscriptions": pd.DataFrame(
                    [{"ACCOUNT_ID_C": "ACC-001", "BU_NAME": "Acme Corporation"}]
                ),
                "action_plans": pd.DataFrame(
                    [
                        {
                            "ID": "AP-001",
                            "ACCOUNT_ID_C": "ACC-001",
                            "BU_NAME": "Acme Corporation",
                            "STATUS_C": "Open",
                        }
                    ]
                ),
                "adoption_barriers": pd.DataFrame(),
                "customer_pulse": pd.DataFrame(),
                "tac_cases": pd.DataFrame(),
                "success_priorities": pd.DataFrame(),
            }
        },
        report_type="Comprehensive",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-03T12:00:00Z",
    )

    rows = delivery._comprehensive_account_evidence_rows(facts)  # noqa: SLF001

    assert rows
    assert rows[0][0] == "Acme Corporation"
    assert "lower bound" in str(rows[0][2]).casefold() or rows[0][2] == 1
    compact = dict(facts)
    compact["report_type"] = "Compact"
    assert delivery._comprehensive_account_evidence_rows(compact) == []  # noqa: SLF001


def test_report_specific_fact_surface_omits_legacy_summary_counts_from_word() -> None:
    rows = []
    facts_by_field = (
        ("Key_Metrics", "Key_Findings", 2),
        ("Key_Metrics", "Recommendations", 4),
        ("Renewal_Summary", "Next_Review_Date", "2026-09-12T00:00:00Z"),
        ("Recommendations", "Recommendation", "Confirm the executive renewal owner"),
    )
    for position, (sheet, field, value) in enumerate(facts_by_field, 1):
        rows.append(
            {
                "Legacy_Record_Type": "Family-specific reported fact",
                "Metric_Key": f"legacy.family.renewal.{sheet}.{field}.{position}",
                "Legacy_Fact_Value": value,
                "Legacy_Fact_Label": field,
                "Legacy_Source_Field": field,
                "Legacy_Source_Sheet": sheet,
                "Legacy_Source_Row_Number": 2,
                "BU_NAME": "Acme Corporation",
            }
        )
    facts = {
        "report_type": "Renewal",
        "legacy_adapter": {"report_family": "renewal"},
        "frames": {"subscriptions": pd.DataFrame(rows)},
        "risk_summary": {"source_state": "available"},
    }

    bundle = delivery._report_specific_decision_fact_bundle(facts)  # noqa: SLF001
    visible = " ".join(str(row["fact"]) for row in bundle["rows"])

    assert bundle["total_available"] == 4
    assert bundle["manager_surface_eligible"] == 2
    assert bundle["manager_surface_omitted"] == 2
    assert "Key Findings" not in visible
    assert "Key Recommendations" not in visible
    assert "next review date" in visible.casefold()
    assert "Recommendation" in visible


def test_partial_renewal_suppresses_legacy_risk_and_risk_derived_recommendation() -> None:
    rows = []
    for position, (sheet, field, value) in enumerate(
        (
            ("Renewal_Summary", "Risk_Band", "MEDIUM"),
            ("Recommendations", "Recommendation", "Schedule a risk review"),
            ("Recommendations", "Priority", 1),
            ("Renewal_Summary", "Next_Review_Date", "2026-09-12T00:00:00Z"),
        ),
        1,
    ):
        rows.append(
            {
                "Legacy_Record_Type": "Family-specific reported fact",
                "Metric_Key": f"legacy.family.renewal.{sheet}.{field}.{position}",
                "Legacy_Fact_Value": value,
                "Legacy_Fact_Label": field,
                "Legacy_Source_Field": field,
                "Legacy_Source_Sheet": sheet,
                "Legacy_Source_Row_Number": position + 1,
                "Customer Name": "Acme Corporation",
            }
        )
    facts = {
        "report_type": "Renewal",
        "legacy_adapter": {"report_family": "renewal"},
        "frames": {"subscriptions": pd.DataFrame(rows)},
        "risk_summary": {"source_state": "partial"},
    }

    bundle = delivery._report_specific_decision_fact_bundle(facts)  # noqa: SLF001
    visible = " ".join(
        f"{row['fact']} {row['reported_value']}" for row in bundle["rows"]
    )

    assert "MEDIUM" not in visible
    assert "Schedule a risk review" not in visible
    assert "Priority" not in visible
    assert "Suggested next review date (legacy analysis)" in visible
    assert "2026-09-12" in visible


def test_warning_copy_distinguishes_unavailable_technology_scope() -> None:
    source, state, effect = delivery._public_warning_copy(  # noqa: SLF001
        {
            "dataset": "csconsole_customer_pulse",
            "kind": "technology_scope_unavailable",
            "effect": "technology columns missing",
        }
    )

    assert source == "CSConsole Customer Pulse"
    assert state == "Unavailable"
    assert "not counted as zero" in effect


def test_report_facts_disclose_guarded_fixture_provenance() -> None:
    frame = pd.DataFrame(
        [{"SUBSCRIPTION_ID": "SUB-1", "ACCOUNT_ID_C": "ACC-1", "BU_NAME": "Acme"}]
    )
    frame.attrs.update(
        {
            "source_mode": "local_acceptance_fixture",
            "source_state": "available",
        }
    )
    team_data = {
        "Alex Rivera": {
            "subscriptions": frame,
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
            "success_priorities": pd.DataFrame(),
        }
    }

    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="team",
        scope_value="Acme Team",
        manager_name="Dana Manager",
        days=90,
        as_of="2026-08-03T21:00:00Z",
        data_as_of_utc="2026-08-03T21:00:00Z",
        data_as_of_state="available",
    )

    fixture_warnings = [
        warning
        for warning in facts["partial_data_warnings"]
        if "fixture" in str(warning.get("kind") or "").casefold()
    ]
    assert len(fixture_warnings) == 1
    assert "not queried or validated" in fixture_warnings[0]["effect"]
    assert facts["frames"]["subscriptions"].attrs["source_mode"] == (
        "local_acceptance_fixture"
    )
    assert facts["data_mode"] == "Guarded offline fixture"
    assert facts["live_validation_performed"] is False
    report_info = delivery.build_source_data_sheets(facts)["Report_Info"]
    info = dict(zip(report_info["Item"], report_info["Value"]))
    assert info["Data_Mode"] == "Guarded offline fixture"
    assert info["Live_Source_Validation"] == "No"


def test_tac_case_type_coverage_is_explicit_and_survives_public_projection() -> None:
    from report_export_schema import apply_export_schema

    tac = pd.DataFrame(
        [
            {"SR Number": "SR-1", "Title": "Provision license enablement"},
            {"SR Number": "SR-2", "Title": "Service outage and failure"},
            {"SR Number": "SR-3", "Title": "General usage question"},
        ]
    )
    decorated, coverage = delivery._decorate_tac_case_type_quality(tac)  # noqa: SLF001

    assert coverage == {
        "total": 3,
        "classified": 2,
        "not_derivable": 1,
        "coverage_percent": 66.7,
        "break_fix": 1,
        "provisioning": 1,
    }
    unresolved = decorated.loc[decorated["case_type_class"].eq("unknown")].iloc[0]
    assert "Not derivable" in unresolved["case_type_data_quality"]

    public = apply_export_schema(decorated, sheet_name="TAC_Cases")
    assert "Case Type Data Quality" in public.columns
    assert public["Case Type"].tolist() == [
        "provisioning_request",
        "break_fix_technical",
        "Unclassified",
    ]


def test_active_handoffs_match_exact_seventeen_sheet_contract() -> None:
    assert len(delivery.SOURCE_DATA_SHEET_NAMES) == 17
    assert "Evidence_Links" in delivery.SOURCE_DATA_SHEET_NAMES
    assert "Defect_Correlations" in delivery.SOURCE_DATA_SHEET_NAMES

    active_docs = (
        "CLAUDE.md",
        "HANDOFF_PROMPT.md",
        "WORK_MACHINE_ROLLOUT.md",
        "CLAUDE_COWORK_HANDOFF.md",
    )
    for relative in active_docs:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "exactly 16" not in text
        assert "all 16 sheets" not in text
        assert "exact 16-sheet" not in text
        assert "17" in text and "Defect_Correlations" in text

    cursor = (ROOT / "CURSOR_HANDOFF.md").read_text(encoding="utf-8")
    assert cursor.startswith("# OBSOLETE — DO NOT RUN §0")
    assert "use `NEXT_MACHINE_PROMPT.md`" in cursor


def test_admin_audit_logs_use_digest_and_dynamic_max_score() -> None:
    app_source = (ROOT / "app_simple.py").read_text(encoding="utf-8")
    admin_source = (ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")

    assert "score=%s/%s" in app_source
    assert "score=%s/100" not in app_source
    assert "Starting audit for analysis:" not in admin_source
    assert "Audit completed for %s" not in admin_source
    assert "Error auditing report {analysis_id}" not in admin_source
    assert "_audit_id_digest" in admin_source


def test_feature_request_analysis_does_not_mutate_public_tac_source_rows() -> None:
    source = pd.DataFrame(
        [
            {
                "Case Number": "CASE-001",
                "Title": "Feature request for reporting",
                "Problem Description": None,
            }
        ]
    )
    original = source.copy(deep=True)

    result = analyze_feature_requests(source)

    pd.testing.assert_frame_equal(source, original)
    assert result["total_requests"] == 1
    assert "Title_Lower" not in source.columns
    assert "Desc_Lower" not in source.columns


def test_untagged_status_incidents_do_not_change_customer_renewal_risk() -> None:
    incidents = [
        {
            "id": "INC-PORTFOLIO",
            "status": "investigating",
            "impact_level": "high",
        }
    ]

    assert (
        _r65_filter_customer_tagged_incidents(
            incidents,
            "Acme Corporation",
        )
        == []
    )
