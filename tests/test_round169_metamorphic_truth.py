"""Round 169 deterministic, aggregate-only metamorphic truth gate."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import adoptiq_backend as backend
import canonical_metrics as metrics
from local_acceptance_lab import build_scenario_bundle
from local_acceptance_runtime import _frame
from scripts import run_round169_metamorphic_acceptance as acceptance


@pytest.fixture(scope="module")
def acceptance_summary() -> dict[str, object]:
    return acceptance.run_acceptance(max_seconds=180)


def test_round169_metamorphic_gate_is_exactly_green(
    acceptance_summary: dict[str, object],
) -> None:
    assert acceptance.validate_summary(acceptance_summary) == []
    assert acceptance_summary["all_passed"] is True
    assert acceptance_summary["passed_count"] == len(acceptance.CHECK_NAMES)
    assert set(acceptance_summary["checks"]) == set(acceptance.CHECK_NAMES)


def test_round169_summary_is_aggregate_only_and_path_free(
    acceptance_summary: dict[str, object],
) -> None:
    serialized = json.dumps(acceptance_summary, sort_keys=True).casefold()
    assert acceptance_summary["sanitized"] is True
    assert acceptance_summary["aggregate_only"] is True
    assert acceptance_summary["live_validation_performed"] is False
    assert acceptance_summary["production_accuracy_claimed"] is False
    for forbidden in (
        "record_id",
        "customer_name",
        "manager_name",
        "source_row",
        "/users/",
        ".xlsx",
        ".docx",
        "ap-001",
        "sr-001",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("hostile", (True, 0, 901, 1.5, "180"))
def test_round169_runtime_rejects_unbounded_or_nonexact_time_budget(
    hostile: object,
) -> None:
    with pytest.raises(ValueError, match="max_seconds"):
        acceptance.run_acceptance(max_seconds=hostile)  # type: ignore[arg-type]


def test_round169_summary_writer_is_bounded_exclusive_and_symlink_safe(
    tmp_path: Path,
) -> None:
    target = tmp_path / "summary.json"
    acceptance._write_summary_exclusive(target, '{"safe":true}')  # noqa: SLF001
    assert target.read_text(encoding="utf-8") == '{"safe":true}\n'
    with pytest.raises(FileExistsError):
        acceptance._write_summary_exclusive(target, "{}")  # noqa: SLF001

    symlink = tmp_path / "summary-link.json"
    symlink.symlink_to(target)
    with pytest.raises(FileExistsError):
        acceptance._write_summary_exclusive(symlink, "{}")  # noqa: SLF001
    with pytest.raises(ValueError, match="bounded output"):
        acceptance._write_summary_exclusive(  # noqa: SLF001
            tmp_path / "too-large.json",
            "x" * acceptance.MAX_SUMMARY_BYTES,
        )


@pytest.mark.parametrize("hostile", ("true", "false", 1, 0, None, [], {}))
def test_summary_projector_rejects_nonliteral_success_booleans(
    acceptance_summary: dict[str, object],
    hostile: object,
) -> None:
    mutated = json.loads(json.dumps(acceptance_summary))
    mutated["checks"][acceptance.CHECK_NAMES[0]]["passed"] = hostile
    assert acceptance.validate_summary(mutated)


def test_summary_projector_rejects_missing_or_extra_checks(
    acceptance_summary: dict[str, object],
) -> None:
    missing = json.loads(json.dumps(acceptance_summary))
    missing["checks"].pop(acceptance.CHECK_NAMES[-1])
    assert "check_inventory" in acceptance.validate_summary(missing)

    extra = json.loads(json.dumps(acceptance_summary))
    extra["checks"]["unexpected"] = {
        "passed": True,
        "cases": 1,
        "digest": "0" * 64,
    }
    assert "check_inventory" in acceptance.validate_summary(extra)

    extra_top = json.loads(json.dumps(acceptance_summary))
    extra_top["unexpected"] = True
    assert "summary_inventory" in acceptance.validate_summary(extra_top)


@pytest.mark.parametrize("hostile", (True, 8.0, "8", None))
def test_summary_projector_rejects_nonexact_top_level_counts(
    acceptance_summary: dict[str, object],
    hostile: object,
) -> None:
    for key in ("check_count", "passed_count"):
        mutated = json.loads(json.dumps(acceptance_summary))
        mutated[key] = hostile
        assert key in acceptance.validate_summary(mutated)


def test_summary_projector_rejects_extra_check_fields(
    acceptance_summary: dict[str, object],
) -> None:
    mutated = json.loads(json.dumps(acceptance_summary))
    check_name = acceptance.CHECK_NAMES[0]
    mutated["checks"][check_name]["unexpected"] = True
    assert f"check_keys:{check_name}" in acceptance.validate_summary(mutated)

    failure_code = json.loads(json.dumps(acceptance_summary))
    failure_code["checks"][check_name]["failure_code"] = "should_not_exist"
    assert f"check_keys:{check_name}" in acceptance.validate_summary(failure_code)


@pytest.mark.parametrize("hostile", (True, 1.0, "1", None))
def test_summary_projector_rejects_noninteger_case_counts(
    acceptance_summary: dict[str, object],
    hostile: object,
) -> None:
    mutated = json.loads(json.dumps(acceptance_summary))
    check_name = acceptance.CHECK_NAMES[0]
    mutated["checks"][check_name]["cases"] = hostile
    assert f"cases:{check_name}" in acceptance.validate_summary(mutated)


@pytest.mark.parametrize("delta", (-1, 1))
def test_summary_projector_rejects_partial_or_extra_case_inventory(
    acceptance_summary: dict[str, object],
    delta: int,
) -> None:
    mutated = json.loads(json.dumps(acceptance_summary))
    check_name = acceptance.CHECK_NAMES[0]
    mutated["checks"][check_name]["cases"] = (
        acceptance.EXPECTED_CASE_COUNTS[check_name] + delta
    )
    assert f"cases:{check_name}" in acceptance.validate_summary(mutated)


@pytest.mark.parametrize("hostile", ("true", 1, 0, None))
def test_summary_projector_rejects_top_level_boolean_truthiness(
    acceptance_summary: dict[str, object],
    hostile: object,
) -> None:
    for key in (
        "sanitized",
        "aggregate_only",
        "do_not_commit_artifacts",
        "companion_http_negative_control_required",
        "all_passed",
    ):
        mutated = json.loads(json.dumps(acceptance_summary))
        mutated[key] = hostile
        assert acceptance.validate_summary(mutated)


def test_stable_id_reconciliation_ignores_transport_but_not_source_facts() -> None:
    compatible = pd.DataFrame(
        [
            {
                "Record_ID": "SAFE-RECORD",
                "ACCOUNT_ID_C": "ACCOUNT-1",
                "STATUS_C": "Open",
                "DESCRIPTION_C": "",
                "CSSM": "Member A",
                "Attributed_Team_Members": "Member A",
                "QUERY_ID": "query-a",
                "QUERY_MEMBER_EMAIL": "member-a@example.invalid",
                "RETRIEVED_AT": "2026-08-03T20:00:00Z",
                "SOURCE_ROW_NUMBER": 2,
                "Scope_Value": "member A query",
                "Record_ID_Data_Quality": "OK",
                "Source_Record_URL": "https://example.invalid/first",
            },
            {
                "Record_ID": "SAFE-RECORD",
                "ACCOUNT_ID_C": "ACCOUNT-1",
                "STATUS_C": "Open",
                "DESCRIPTION_C": "Complementary evidence",
                "CSSM": "Member B",
                "Attributed_Team_Members": "Member B",
                "QUERY_ID": "query-b",
                "QUERY_MEMBER_EMAIL": "member-b@example.invalid",
                "RETRIEVED_AT": "2026-08-03T20:01:00Z",
                "SOURCE_ROW_NUMBER": 9,
                "Scope_Value": "member B query",
                "Record_ID_Data_Quality": "Verified",
                "Source_Record_URL": "https://example.invalid/second",
            },
        ]
    )
    forward, forward_coverage = metrics.reconcile_stable_id_observations(
        compatible,
        source_label="privacy-safe fixture",
    )
    reverse, reverse_coverage = metrics.reconcile_stable_id_observations(
        compatible.iloc[::-1].reset_index(drop=True),
        source_label="privacy-safe fixture",
    )

    pd.testing.assert_frame_equal(forward, reverse)
    assert forward_coverage == reverse_coverage
    assert len(forward) == 1
    assert forward.iloc[0]["DESCRIPTION_C"] == "Complementary evidence"
    assert forward.iloc[0]["Attributed_Team_Members"] == "Member A; Member B"
    assert forward_coverage["conflicting_stable_id_count"] == 0

    conflicted = compatible.copy()
    conflicted.loc[1, "STATUS_C"] = "Closed"
    quarantined, coverage = metrics.reconcile_stable_id_observations(
        conflicted,
        source_label="privacy-safe fixture",
    )
    assert quarantined.empty
    assert coverage["conflicting_stable_id_count"] == 1
    assert coverage["quarantined_observation_count"] == 2
    assert "SAFE-RECORD" not in json.dumps(coverage, sort_keys=True)


def test_adoption_barrier_source_conflict_is_quarantined_without_raw_id() -> None:
    first = pd.DataFrame(
        [{"ID": "BARRIER-PRIVATE", "STATUS_C": "Open", "DESCRIPTION_C": "Evidence"}]
    )
    second = pd.DataFrame(
        [{"ID": "BARRIER-PRIVATE", "STATUS_C": "Closed", "DESCRIPTION_C": "Evidence"}]
    )

    merged = metrics.merge_adoption_barrier_sources(
        [first, second],
        source_labels=["Source A", "Source B"],
    )

    assert merged.empty
    assert merged.attrs["partial"] is True
    assert merged.attrs["stable_id_conflicting_record_count"] == 1
    assert merged.attrs["stable_id_quarantined_observation_count"] == 2
    assert "BARRIER-PRIVATE" not in json.dumps(merged.attrs, sort_keys=True)


def test_adoption_barrier_fixture_transport_fanout_coalesces_order_invariant() -> None:
    observations = _frame(build_scenario_bundle("healthy"), "adoption_barriers")

    forward = metrics.merge_adoption_barrier_sources(
        [observations],
        source_labels=["Fixture source"],
    )
    reversed_rows = metrics.merge_adoption_barrier_sources(
        [observations.iloc[::-1].reset_index(drop=True)],
        source_labels=["Fixture source"],
    )

    pd.testing.assert_frame_equal(forward, reversed_rows)
    assert metrics.count_total_barriers(forward) == 3
    assert forward.attrs.get("stable_id_conflicting_record_count", 0) == 0
    shared = forward.loc[forward["ID"].eq("AB-001")].iloc[0]
    assert shared["DESCRIPTION_C"] == (
        "A sanitized dependency blocks the final migration wave."
    )


def test_merge_suffixed_member_attribution_is_not_a_substantive_conflict() -> None:
    observations = pd.DataFrame(
        [
            {
                "ID": "AB-SAFE",
                "STATUS_C": "Open",
                "DESCRIPTION_C": "",
                "CSSM_EMAIL_x": "member-a@example.invalid",
                "LOCAL_ACCEPTANCE_RECORD_ID": "observation-a",
            },
            {
                "ID": "AB-SAFE",
                "STATUS_C": "Open",
                "DESCRIPTION_C": "Complementary evidence",
                "CSSM_EMAIL_x": "member-b@example.invalid",
                "LOCAL_ACCEPTANCE_RECORD_ID": "observation-b",
            },
        ]
    )

    reconciled, coverage = metrics.reconcile_stable_id_observations(
        observations,
        id_candidates=("ID",),
        source_label="Adoption Barrier",
    )
    merged = metrics.merge_adoption_barrier_sources(
        [observations],
        source_labels=["Snowflake C360 Adoption Barriers"],
    )

    assert len(reconciled) == 1
    assert reconciled.iloc[0]["DESCRIPTION_C"] == "Complementary evidence"
    assert coverage["state"] == "available"
    assert coverage["identified_observation_count"] == 2
    assert len(merged) == 1
    assert merged.attrs.get("stable_id_conflicting_record_count", 0) == 0


def test_effective_observation_count_uses_local_record_identity_before_fanout() -> None:
    observations = pd.DataFrame(
        [
            {
                "ID": "CASE-SAFE",
                "STATUS": status,
                "LOCAL_ACCEPTANCE_RECORD_ID": f"raw-{index}",
                "CSSM_EMAIL": member,
            }
            for index, status in enumerate(("Open", "Closed"), start=1)
            for member in ("member-a@example.invalid", "member-b@example.invalid")
        ]
    )

    reconciled, coverage = metrics.reconcile_stable_id_observations(
        observations,
        id_candidates=("ID",),
        source_label="TAC Case",
    )

    assert reconciled.empty
    assert coverage["conflicting_stable_id_count"] == 1
    assert coverage["quarantined_observation_count"] == 2


def test_csconsole_query_union_drops_only_exact_transport_overlap() -> None:
    observations = pd.DataFrame(
        [
            {"ID": "AP-1", "STATUS_C": "Open", "DESCRIPTION_C": "Evidence"},
            {"ID": "AP-1", "STATUS_C": "Open", "DESCRIPTION_C": "Evidence"},
            {"ID": "AP-1", "STATUS_C": "Closed", "DESCRIPTION_C": "Evidence"},
        ]
    )

    result = backend._deduplicate_csconsole_query_union(observations)  # noqa: SLF001

    assert len(result) == 2
    assert set(result["STATUS_C"]) == {"Open", "Closed"}


def test_local_runtime_defers_report_source_reconciliation() -> None:
    bundle = build_scenario_bundle("healthy")

    action_plans = _frame(bundle, "action_plans")
    barriers = _frame(bundle, "adoption_barriers")

    assert len(action_plans) == len(bundle.frame("action_plans")) == 8
    assert len(barriers) == len(bundle.frame("adoption_barriers")) == 4
    for frame in (action_plans, barriers):
        assert frame.attrs["stable_id_reconciliation_deferred"] is True
        assert frame.attrs["duplicate_rows_removed"] == 0
        assert frame.attrs["duplicate_observations_deferred"] > 0


def test_adoption_barrier_merge_unions_route_diagnostics_without_paths() -> None:
    snowflake = pd.DataFrame(
        [{"ID": "AB-1", "STATUS_C": "Open", "DESCRIPTION_C": "Evidence"}]
    )
    snowflake.attrs["source_observation_routes"] = ["snowflake-query-a"]
    snowflake.attrs["source_mode"] = "live"
    csconsole = pd.DataFrame(
        [{"ID": "AB-1", "STATUS_C": "Open", "DESCRIPTION_C": "Evidence"}]
    )
    csconsole.attrs["source_observation_routes"] = ["csconsole-query-b"]
    csconsole.attrs["source_mode"] = "guarded"

    merged = metrics.merge_adoption_barrier_sources(
        [snowflake, csconsole],
        source_labels=["Snowflake", "CSConsole"],
    )

    assert merged.attrs["source_observation_routes"] == [
        "csconsole-query-b",
        "snowflake-query-a",
    ]
    assert merged.attrs["source_mode"] == "mixed"
    assert merged.attrs["source_modes"] == ["guarded", "live"]
