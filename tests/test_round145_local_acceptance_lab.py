"""Round 145 guarded local acceptance data-lab contracts."""

from __future__ import annotations

import json

import pytest

import local_acceptance_lab as lab
from scripts import run_local_acceptance_lab as cli


def test_manifest_declares_every_required_scenario_and_data_family() -> None:
    manifest = lab.load_manifest()

    assert lab.REQUIRED_SCENARIOS.issubset(manifest["scenarios"])
    assert set(manifest["datasets"]) == {
        "subscriptions",
        "customers",
        "ownership",
        "action_plans",
        "adoption_barriers",
        "customer_pulse",
        "tac_cases",
        "bems_cases",
        "success_priorities",
        "renewals",
        "activities",
        "support_cases",
        "external_incidents",
        "external_bugs",
        "external_maintenances",
        "corpus_documents",
        "corpus_chunks",
        "customer_history",
        "playbook_entries",
    }
    assert manifest["schema_fingerprint"] == lab.compute_schema_fingerprint(
        manifest["datasets"]
    )


def test_every_scenario_materializes_and_reconciles() -> None:
    summaries = lab.validate_all_scenarios()

    assert set(summaries) == set(lab.load_manifest()["scenarios"])
    assert all(item["live_validation_performed"] is False for item in summaries.values())
    assert all(item["sanitized"] is True for item in summaries.values())


@pytest.mark.parametrize(
    ("scenario", "dataset", "expected_count", "expected_state"),
    [
        ("healthy", "action_plans", 8, "available"),
        ("true_zero", "activities", 0, "zero"),
        ("partial", "support_cases", 1, "partial"),
        ("stale", "external_incidents", 2, "stale"),
        ("failed", "action_plans", 0, "failed"),
        ("unavailable", "customer_pulse", 0, "unavailable"),
        ("truncated", "activities", 2, "truncated"),
        ("duplicate_records", "action_plans", 9, "available"),
        ("ambiguous_customer", "subscriptions", 8, "available"),
        ("multi_manager", "ownership", 2, "available"),
        ("roster_gap", "subscriptions", 2, "partial"),
        ("timezone_boundary", "activities", 5, "available"),
        ("large_volume", "activities", 250, "available"),
        ("prompt_injection", "corpus_chunks", 5, "available"),
    ],
)
def test_scenario_oracles_and_frame_provenance(
    scenario: str,
    dataset: str,
    expected_count: int,
    expected_state: str,
) -> None:
    bundle = lab.build_scenario_bundle(scenario)
    frame = bundle.frame(dataset)

    assert len(frame) == expected_count
    assert frame.attrs["sanitized"] is True
    assert frame.attrs["source_mode"] == lab.SOURCE_MODE
    assert frame.attrs["source_state"] == expected_state
    assert frame.attrs["live_validation_performed"] is False


def test_edge_mutations_are_real_not_label_only() -> None:
    ambiguous = lab.build_scenario_bundle("ambiguous_customer")
    long_text = lab.build_scenario_bundle("long_text")
    malformed = lab.build_scenario_bundle("malformed_value")
    injection = lab.build_scenario_bundle("prompt_injection")
    multi_manager = lab.build_scenario_bundle("multi_manager")
    roster_gap = lab.build_scenario_bundle("roster_gap")

    normalized = (
        ambiguous.frame("customers")["BU_NAME"]
        .str.replace(r"[^A-Za-z0-9]", "", regex=True)
        .str.casefold()
    )
    assert normalized.duplicated(keep=False).any()
    assert long_text.frame("action_plans")["SUBJECT_C"].str.len().max() > 4_000
    assert "not-a-number" in set(malformed.frame("customer_pulse")["SCORE__C"])
    assert injection.frame("corpus_chunks")["TEXT"].str.contains(
        "ignore all previous instructions",
        case=False,
    ).any()
    assert set(multi_manager.frame("ownership")["MANAGER_NAME"]) == {
        "Local Fixture Manager",
        "Second Fixture Manager",
    }
    assert set(roster_gap.frame("subscriptions")["FIXTURE_MEMBER"]) == {
        "Alex Rivera"
    }
    assert not roster_gap.frame("action_plans").loc[
        lambda frame: frame["FIXTURE_MEMBER"].eq("Morgan Lee")
    ].empty


def test_provider_failure_scenarios_are_declared_separately_from_data_state() -> None:
    assert lab.build_scenario_bundle("provider_timeout").provider_state == "timeout"
    assert lab.build_scenario_bundle("provider_rate_limit").provider_state == "rate_limited"
    assert lab.build_scenario_bundle("provider_unavailable").provider_state == "unavailable"
    assert lab.build_scenario_bundle("provider_malformed").provider_state == "malformed"


def test_frame_returns_an_independent_copy() -> None:
    bundle = lab.build_scenario_bundle("healthy")
    first = bundle.frame("subscriptions")
    first.loc[0, "BU_NAME"] = "mutated"

    assert bundle.frame("subscriptions").loc[0, "BU_NAME"] != "mutated"


def test_every_fixture_row_has_a_unique_stable_lineage_id() -> None:
    bundle = lab.build_scenario_bundle("healthy")

    for dataset in bundle.frames:
        frame = bundle.frame(dataset)
        assert "LOCAL_ACCEPTANCE_RECORD_ID" in frame.columns
        assert frame["LOCAL_ACCEPTANCE_RECORD_ID"].notna().all()
        assert frame["LOCAL_ACCEPTANCE_RECORD_ID"].is_unique

    assert bundle.frame("action_plans")["ID"].fillna("").ne("").all()

    missing_id_row = lab.build_scenario_bundle("missing_id").frame("action_plans").loc[
        lambda frame: frame["ID"].fillna("").eq("")
    ].iloc[0]
    assert ":missing:" in missing_id_row["LOCAL_ACCEPTANCE_RECORD_ID"]


def test_every_dataset_declares_and_reconciles_canonical_metrics_warnings_lineage() -> None:
    manifest = lab.load_manifest()
    bundle = lab.build_scenario_bundle("healthy")

    assert bundle.expected_canonical_counts["subscriptions"] == 6
    assert bundle.expected_canonical_counts["action_plans"] == 7
    assert bundle.warning_codes["action_plans"] == ("duplicate_source_id",)
    for dataset, spec in manifest["datasets"].items():
        assert spec["canonical_metric"]
        assert isinstance(spec["expected_canonical_count"], int)
        assert isinstance(spec["expected_warning_codes"], list)
        assert spec["lineage"] == {
            "origin": spec["origin"],
            "stable_source_id": spec["primary_key"],
            "fixture_record_id": "LOCAL_ACCEPTANCE_RECORD_ID",
        }


def test_scenarios_declare_canonical_warning_and_provider_oracles() -> None:
    manifest = lab.load_manifest()
    for scenario in manifest["scenarios"].values():
        assert "expected_canonical_count_overrides" in scenario
        assert "expected_warning_overrides" in scenario
        assert "provider_warning_code" in scenario

    assert lab.build_scenario_bundle("missing_id").expected_canonical_counts[
        "action_plans"
    ] == 7
    assert lab.build_scenario_bundle("missing_id").warning_codes["action_plans"] == (
        "duplicate_source_id",
        "missing_source_id",
    )
    assert (
        lab.build_scenario_bundle("provider_timeout").provider_warning_code
        == "provider_timeout"
    )
    assert (
        lab.build_scenario_bundle("healthy").report_publication_expectation
        == lab.REPORT_PUBLICATION_COMPLETED
    )
    assert (
        lab.build_scenario_bundle("missing_id").report_publication_expectation
        == lab.REPORT_PUBLICATION_BLOCKED_MISSING_STABLE_ID
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"explicit": False, "host": "127.0.0.1", "frozen": False, "environ": {}},
        {"explicit": True, "host": "0.0.0.0", "frozen": False, "environ": {}},
        {"explicit": True, "host": "127.0.0.1", "frozen": True, "environ": {}},
        {
            "explicit": True,
            "host": "127.0.0.1",
            "frozen": False,
            "environ": {"ADOPTIQ_BIND_PUBLIC": "1"},
        },
        {
            "explicit": True,
            "host": "127.0.0.1",
            "frozen": False,
            "environ": {"ADOPTIQ_ENV": "production"},
        },
    ],
)
def test_fixture_activation_fails_closed(kwargs: dict) -> None:
    with pytest.raises(lab.LocalAcceptanceSafetyError):
        lab.assert_safe_activation(**kwargs)


def test_fixture_activation_allows_explicit_source_loopback() -> None:
    lab.assert_safe_activation(
        explicit=True,
        host="localhost",
        frozen=False,
        environ={},
    )


def test_redacted_summary_contains_no_fixture_records() -> None:
    bundle = lab.build_scenario_bundle("healthy")
    serialized = json.dumps(bundle.redacted_summary(), sort_keys=True)

    assert "Acme Corporation" not in serialized
    assert "AP-001" not in serialized
    assert "fixture.owner" not in serialized
    assert bundle.redacted_summary()["live_validation_performed"] is False
    assert (
        bundle.redacted_summary()["report_publication_expectation"]
        == lab.REPORT_PUBLICATION_COMPLETED
    )


def test_cli_requires_explicit_activation(capsys) -> None:
    assert cli.main(["--scenario", "healthy"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error_kind"] == "LocalAcceptanceSafetyError"


def test_cli_validates_all_scenarios_without_live_claim(capsys) -> None:
    exit_code = cli.main(["--enable-local-fixtures", "--scenario", "all"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["all_reconciled"] is True
    assert payload["scenario_count"] >= len(lab.REQUIRED_SCENARIOS)
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False


def test_normal_runtime_and_build_specs_do_not_import_fixture_lab() -> None:
    root = lab.REPO_ROOT
    for path in (
        root / "app_simple.py",
        root / "adoptiq_mac.spec",
        root / "adoptiq_pc.spec",
        root / "build_mac.sh",
        root / "build_mac_dmg.sh",
    ):
        assert "local_acceptance_lab" not in path.read_text(encoding="utf-8")
