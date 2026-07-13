from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm
from data_normalization import (
    CustomerAliasRegistry,
    build_customer_lookup,
    customer_identity_key,
    partition_customer_frame,
    quarantine_cross_customer_record_ids,
)
from risk_scoring import RiskWeights, compute_customer_risk_profile


@pytest.mark.parametrize(
    "id_column",
    [
        "SR_NUMBER",
        "sr_number",
        "CaseNumber",
        "casenumber",
        "CUSTOMER_PULSE_ID_C",
        "customer_pulse_id_c",
        "SUBSCRIPTION_ID",
        "subscription_id__c",
    ],
)
def test_generic_id_aliases_are_case_insensitive(id_column: str) -> None:
    source = pd.DataFrame(
        {
            id_column: ["shared-1", "shared-1"],
            "customer_name": ["Alpha", "Beta"],
        }
    )

    quarantined = quarantine_cross_customer_record_ids(source)

    assert quarantined.empty
    diagnostics = quarantined.attrs["cross_customer_id_conflicts"]
    assert diagnostics["conflicting_ids"] == ["SHARED-1"]
    assert diagnostics["quarantined_rows"] == 2
    assert diagnostics["record_id_columns_used"] == [id_column]


@pytest.mark.parametrize(
    "missing_id",
    [None, pd.NA, "", "N/A", "NA", "<NA>", "null", "Unknown", "undefined", "missing", "-", "--"],
)
def test_textual_missing_ids_remain_independent(missing_id: object) -> None:
    source = pd.DataFrame(
        {
            "CaseNumber": [missing_id, missing_id],
            "customer_name": ["Alpha", "Beta"],
        }
    )

    quarantined = quarantine_cross_customer_record_ids(source)

    assert len(quarantined) == 2
    assert quarantined.attrs["cross_customer_id_conflicts"]["conflicting_ids"] == []


def test_duplicate_dataframe_indexes_do_not_break_quarantine_or_partition() -> None:
    source = pd.DataFrame(
        {
            "Case #": ["shared", "shared"],
            "customer_name": ["Alpha", "Beta"],
        },
        index=[7, 7],
    )

    quarantined = quarantine_cross_customer_record_ids(source)

    assert quarantined.empty
    assert partition_customer_frame(source) == {}


def test_unknown_name_is_not_an_owner_and_account_ids_are_fallback_evidence() -> None:
    conflicting_accounts = pd.DataFrame(
        {
            "CaseNumber": ["same", "same"],
            "customer_name": ["Unknown", "unknown"],
            "account_id_c": ["A-1", "A-2"],
        }
    )
    same_account = conflicting_accounts.assign(account_id_c=["A-1", "A-1"])

    assert customer_identity_key("Unknown") == ""
    assert quarantine_cross_customer_record_ids(conflicting_accounts).empty
    assert len(quarantine_cross_customer_record_ids(same_account)) == 2


def test_owner_keys_honor_explicit_aliases_without_fuzzy_entity_merges() -> None:
    registry = CustomerAliasRegistry(
        group_id_to_aliases={
            "ibm": ["IBM", "International Business Machines"],
        },
        name_key_to_group_id={
            "ibm": "ibm",
            "international business machines": "ibm",
        },
    )
    aliases = pd.DataFrame(
        {
            "Case #": ["C-1", "C-1"],
            "customer_name": ["IBM", "International Business Machines"],
        }
    )
    distinct_legal_entities = pd.DataFrame(
        {
            "Case #": ["C-2", "C-2"],
            "customer_name": ["Acme Inc", "Acme LLC"],
        }
    )

    alias_result = quarantine_cross_customer_record_ids(
        aliases, registry=registry
    )
    distinct_result = quarantine_cross_customer_record_ids(
        distinct_legal_entities, registry=registry
    )

    assert len(alias_result) == 2
    assert alias_result.attrs["cross_customer_id_conflicts"]["conflicting_ids"] == []
    assert distinct_result.empty
    assert distinct_result.attrs["cross_customer_id_conflicts"]["conflicting_ids"] == [
        "C-2"
    ]


def test_lookup_collision_does_not_merge_distinct_legal_entities() -> None:
    subscriptions = pd.DataFrame(
        {
            "BU_NAME": ["Acme Inc", "Acme LLC"],
            "ACCOUNT_ID_C": ["A-INC", "A-LLC"],
        }
    )
    source_without_account_ids = pd.DataFrame(
        {
            "Case #": ["C-SHARED", "C-SHARED"],
            "customer_name": ["Acme Inc", "Acme LLC"],
        }
    )

    lookup = build_customer_lookup(subscriptions)
    result = quarantine_cross_customer_record_ids(
        source_without_account_ids,
        customer_lookup=lookup,
    )

    assert "acme" in lookup["ambiguous_customer_keys"]
    assert result.empty
    assert result.attrs["cross_customer_id_conflicts"]["conflicting_ids"] == [
        "C-SHARED"
    ]


def test_repeated_quarantine_preserves_conflict_caveat_on_surviving_partitions() -> None:
    source = pd.DataFrame(
        [
            {"Case #": "conflict", "customer_name": "Alpha"},
            {"Case #": "conflict", "customer_name": "Beta"},
            {"Case #": "alpha-only", "customer_name": "Alpha"},
            {"Case #": "beta-only", "customer_name": "Beta"},
        ]
    )

    pre_quarantined = quarantine_cross_customer_record_ids(source)
    partitions = partition_customer_frame(pre_quarantined)

    assert set(partitions) == {"alpha", "beta"}
    for partition in partitions.values():
        diagnostics = partition.attrs["cross_customer_id_conflicts"]
        assert diagnostics["conflicting_ids"] == ["CONFLICT"]
        assert diagnostics["quarantined_rows"] == 2

    logical_alpha = cm.deduplicate_tac_cases(partitions["alpha"])
    assert logical_alpha.attrs["cross_customer_id_conflicts"]["conflicting_ids"] == [
        "CONFLICT"
    ]


def test_all_blank_tac_ids_still_emit_dedup_diagnostics() -> None:
    source = pd.DataFrame(
        {
            "case #": ["N/A", "unknown", None],
            "Status": ["Open", "Closed", "Open"],
        }
    )

    logical = cm.deduplicate_tac_cases(source)

    assert len(logical) == 3
    assert logical.attrs["tac_dedup"] == {
        "id_column": "case #",
        "id_columns_used": ["case #"],
        "raw_rows": 3,
        "logical_cases": 3,
        "duplicates_removed": 0,
        "conflicting_case_ids": [],
        "cross_customer_conflict_ids": [],
        "cross_customer_rows_quarantined": 0,
    }


def test_internal_conflict_attrs_reach_risk_and_block_healthy_at_half_coverage() -> None:
    support = pd.DataFrame(
        {
            "sr_number": ["shared", "shared"],
            "customer_name": ["Alpha", "Beta"],
            "Status": ["Closed", "Closed"],
            "Severity": ["P3", "P3"],
        }
    )
    half_and_half = RiskWeights(
        adoption_barriers=0.5,
        support_cases=0.5,
        customer_pulse=0.0,
        action_plans=0.0,
        incidents=0.0,
        contract=0.0,
        engagement=0.0,
    )

    profile = compute_customer_risk_profile(
        "Alpha",
        customer_ab=pd.DataFrame(),
        customer_csone=support,
        weights=half_and_half,
    )

    assert profile["risk_score_0_100"] is None
    assert profile["risk_band"] == "UNKNOWN"
    assert profile["risk_assessment_state"] == "INSUFFICIENT_EVIDENCE"
    assert profile["evidence_quality"]["coverage_ratio"] == 0.5
    assert profile["evidence_quality"]["ownership_conflict_boundary_blocked"] is True
    assert profile["evidence_quality"]["ownership_conflicts"]["support_cases"][
        "quarantined_rows"
    ] == 2
    assert profile["next_best_actions"][0]["action"].startswith(
        "Validate the missing evidence sources"
    )
