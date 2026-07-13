from __future__ import annotations

import os

import pandas as pd
import pytest

import canonical_metrics as cm
from data_normalization import account_id_matches_scope, detect_bems_mask
from decision_intelligence import (
    AnalysisBundle,
    AnalysisRequest,
    AnalysisSnapshotStore,
    AnalysisSources,
    build_analysis_bundle,
)


AS_OF = "2026-07-13T12:00:00Z"
OBSERVED = "2026-07-12T12:00:00Z"


class _ScopeStringSpoof:
    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


def _request(*customers: str, allowed_source_types=None) -> AnalysisRequest:
    kwargs = {}
    if allowed_source_types is not None:
        kwargs["allowed_source_types"] = tuple(allowed_source_types)
    return AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        customer_scope=tuple(customers),
        as_of_time=AS_OF,
        **kwargs,
    )


def _subscriptions(*customers: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": f"SUB-{index}",
                "ACCOUNT_ID_C": f"ACC-{index}",
                "BU_NAME": customer,
                "TECHNOLOGY": "Collaboration",
                "TEAM_NAME": "Synthetic Team",
                "RENEWAL_RISK_LEVEL": "Low",
                "SUBSCRIPTION_STATUS": "Active",
                "RENEWAL_DATE": "2026-12-31T00:00:00Z",
                "LAST_MODIFIED_DATE": OBSERVED,
            }
            for index, customer in enumerate(customers, 1)
        ]
    )


def _complete_sources(*customers: str) -> AnalysisSources:
    return AnalysisSources(
        subscriptions=_subscriptions(*customers),
        adoption_barriers=pd.DataFrame(
            columns=["ID", "BU_NAME", "SEVERITY_C", "AB_STATUS_C"]
        ),
        support_cases=pd.DataFrame(
            columns=["SR_NUMBER", "BU_NAME", "PRIORITY", "STATUS"]
        ),
        customer_pulse=pd.DataFrame(
            [
                {
                    "CUSTOMER_PULSE_ID_C": f"P-{index}",
                    "BU_NAME": customer,
                    "PULSE_RATING__C": "Green",
                    "LAST_MODIFIED_DATE": OBSERVED,
                }
                for index, customer in enumerate(customers, 1)
            ]
        ),
        action_plans=pd.DataFrame(
            columns=["ACTION_PLAN_ID", "BU_NAME", "STATUS_C"]
        ),
        success_priorities=pd.DataFrame(
            columns=["SUCCESS_PRIORITY_ID", "BU_NAME", "STATUS_C"]
        ),
        external_incidents=(),
        metadata={"ingestion_timestamp": OBSERVED},
    )


def test_request_requires_an_explicit_scope() -> None:
    with pytest.raises(ValueError, match="explicit"):
        AnalysisRequest(as_of_time=AS_OF)


@pytest.mark.parametrize(
    "scope_name",
    ("tenant_scope", "organization_scope", "portfolio_scope"),
)
@pytest.mark.parametrize(
    "invalid_value",
    ({}, [], ("synthetic",), 123),
)
def test_scalar_scope_boundaries_reject_non_strings(
    scope_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(TypeError, match=scope_name):
        AnalysisRequest(
            **{
                scope_name: invalid_value,
                "as_of_time": AS_OF,
            }
        )


def test_explicit_bems_case_type_is_detected_without_free_text_guessing() -> None:
    frame = pd.DataFrame(
        [
            {"CASE_TYPE": "BEMS", "TITLE": "Generic escalation"},
            {"CASE_TYPE": "TAC", "TITLE": "Generic support case"},
        ]
    )
    assert detect_bems_mask(frame).tolist() == [True, False]


def test_identical_facts_have_one_fingerprint_regardless_of_generation_time() -> None:
    request = _request("Acme Inc")
    sources = _complete_sources("Acme Inc")
    first = build_analysis_bundle(
        request, sources, generated_time="2026-07-13T12:01:00Z"
    )
    second = build_analysis_bundle(
        request, sources, generated_time="2026-07-13T12:59:00Z"
    )
    assert first.analysis_fingerprint == second.analysis_fingerprint
    assert first.canonical_payload() == second.canonical_payload()
    assert first.context.generated_time != second.context.generated_time


def test_cross_customer_logical_id_is_quarantined_before_counting() -> None:
    customers = ("Acme Inc", "Beta LLC")
    sources = _complete_sources(*customers)
    sources.support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-SHARED",
                "BU_NAME": customer,
                "PRIORITY": "P1",
                "STATUS": "Open",
                "LAST_MODIFIED_DATE": OBSERVED,
            }
            for customer in customers
        ]
    )
    bundle = build_analysis_bundle(_request(*customers), sources)
    assert [customer.metric("total_cases") for customer in bundle.customers] == [0, 0]
    assert all("support_cases" in customer.unresolved_conflicts for customer in bundle.customers)
    assert bundle.diagnostics.quarantined_records


def test_missing_and_observed_empty_remain_distinct() -> None:
    request = _request("Acme Inc")
    missing = _complete_sources("Acme Inc")
    missing.external_incidents = None
    empty = _complete_sources("Acme Inc")
    empty.external_incidents = ()
    missing_bundle = build_analysis_bundle(request, missing)
    empty_bundle = build_analysis_bundle(request, empty)
    assert missing_bundle.context.source_availability["external_incidents"] == "missing"
    assert empty_bundle.context.source_availability["external_incidents"] == "observed_empty"
    assert missing_bundle.analysis_fingerprint != empty_bundle.analysis_fingerprint


def test_disallowed_source_cannot_affect_metrics_or_evidence() -> None:
    request = _request("Acme Inc", allowed_source_types=("subscriptions",))
    sources = _complete_sources("Acme Inc")
    sources.support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-1",
                "BU_NAME": "Acme Inc",
                "PRIORITY": "P1",
                "STATUS": "Open",
                "LAST_MODIFIED_DATE": OBSERVED,
            }
        ]
    )
    bundle = build_analysis_bundle(request, sources)
    customer = bundle.customers[0]
    assert customer.metric("total_cases") == 0
    assert bundle.context.source_availability["support_cases"] == "excluded_by_request"
    assert {item.source_type for item in bundle.evidence} == {"subscriptions"}


def test_future_renewal_does_not_make_stale_subscription_source_current() -> None:
    subscriptions = _subscriptions("Acme Inc")
    subscriptions["LAST_MODIFIED_DATE"] = "2025-01-01T00:00:00Z"
    subscriptions["RENEWAL_DATE"] = "2026-12-31T00:00:00Z"
    bundle = build_analysis_bundle(
        _request("Acme Inc", allowed_source_types=("subscriptions",)),
        AnalysisSources(subscriptions=subscriptions),
    )
    assert bundle.context.source_freshness["subscriptions"] == "stale"
    assert bundle.customers[0].data_quality.source_freshness["subscriptions"] == "stale"


def test_explicit_renewal_subscription_scope_is_conjunctive() -> None:
    subscriptions = _subscriptions("Acme Inc", "Beta LLC")
    request = AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        renewal_scope=("SUB-2",),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    bundle = build_analysis_bundle(
        request, AnalysisSources(subscriptions=subscriptions)
    )
    assert bundle.portfolio.customer_count == 1
    assert bundle.customers[0].customer_name == "Beta LLC"
    assert bundle.customers[0].subscriptions == ("SUB-2",)

    allowed_18 = "001ABCDEFGHIJKLY55"
    assert account_id_matches_scope("001ABCDEFGHIJKL", (allowed_18,))
    assert account_id_matches_scope("001abcdefghijkly55", (allowed_18,))
    assert not account_id_matches_scope(
        "001abcdefghijklAAA",
        (allowed_18,),
    )
    assert not account_id_matches_scope(
        "001ABCDEFGHIJKLBBB",
        (allowed_18,),
    )
    assert not account_id_matches_scope(
        "001ABCDEF123456Z5B",
        ("001ABCDEF123456YPA",),
    )

    scoped = _subscriptions("Acme Inc")
    scoped["ACCOUNT_ID_C"] = ["001abcdefghijklAAA"]
    strict_request = AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        account_scope=(allowed_18,),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    strict_bundle = build_analysis_bundle(
        strict_request,
        AnalysisSources(subscriptions=scoped),
    )
    assert strict_bundle.portfolio.customer_count == 0

    scoped["ACCOUNT_ID_C"] = ["001ABCDEFGHIJKL"]
    scoped["ACCOUNT__C"] = ["001abcdefghijklAAA"]
    conflicted_bundle = build_analysis_bundle(
        strict_request,
        AnalysisSources(subscriptions=scoped),
    )
    assert conflicted_bundle.portfolio.customer_count == 0

    duplicate_labels = _subscriptions("Acme Inc")
    duplicate_labels["ACCOUNT_ID_C"] = ["001ABCDEFGHIJKL"]
    duplicate_labels = pd.concat(
        [
            duplicate_labels,
            pd.DataFrame({"ACCOUNT_ID_C": ["001abcdefghijklAAA"]}),
        ],
        axis=1,
    )
    duplicate_conflict_bundle = build_analysis_bundle(
        strict_request,
        AnalysisSources(subscriptions=duplicate_labels),
    )
    assert duplicate_conflict_bundle.portfolio.customer_count == 0

    wx_cce = _subscriptions("Acme Inc").drop(columns=["TECHNOLOGY"])
    wx_cce["TECHNOLOGY_C"] = ["Webex Contact Center Enterprise"]
    wrong_product_request = AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        technology_scope=("Cisco UCCE",),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    assert build_analysis_bundle(
        wrong_product_request,
        AnalysisSources(subscriptions=wx_cce),
    ).portfolio.customer_count == 0

    wx_cce["SUB_TECHNOLOGY_C"] = ["Cisco UCCE"]
    conflicting_technology_request = AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        technology_scope=("Webex Contact Center Enterprise",),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    assert build_analysis_bundle(
        conflicting_technology_request,
        AnalysisSources(subscriptions=wx_cce),
    ).portfolio.customer_count == 0
    assert build_analysis_bundle(
        conflicting_technology_request,
        AnalysisSources(
            subscriptions=wx_cce.drop(
                columns=["TECHNOLOGY_C", "SUB_TECHNOLOGY_C"]
            )
        ),
    ).portfolio.customer_count == 0

    benign_duplicates = _subscriptions("Acme Inc").drop(columns=["TECHNOLOGY"])
    benign_duplicates = pd.concat(
        [
            benign_duplicates,
            pd.DataFrame(
                {
                    "SUBSCRIPTION_ID": ["SUB-1"],
                    "TECHNOLOGY_C": ["Webex Contact Center"],
                    "RENEWAL_DATE": ["2026-12-31T00:00:00Z"],
                }
            ),
            pd.DataFrame(
                {"TECHNOLOGY_C": ["Webex Contact Center"]}
            ),
        ],
        axis=1,
    )
    benign_request = AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        subscription_scope=("SUB-1",),
        technology_scope=("Webex Contact Center",),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    benign_bundle = build_analysis_bundle(
        benign_request,
        AnalysisSources(subscriptions=benign_duplicates),
    )
    assert benign_bundle.portfolio.customer_count == 1
    assert benign_bundle.customers[0].subscriptions == ("SUB-1",)
    assert benign_bundle.customers[0].technologies == (
        "Webex Contact Center",
    )

    conflicting_duplicate_technology = benign_duplicates.copy()
    conflicting_duplicate_technology.iloc[
        0,
        list(conflicting_duplicate_technology.columns).index("TECHNOLOGY_C"),
    ] = "Cisco UCCE"
    assert build_analysis_bundle(
        benign_request,
        AnalysisSources(subscriptions=conflicting_duplicate_technology),
    ).portfolio.customer_count == 0

    compound_technology = _subscriptions("Acme Inc").drop(
        columns=["TECHNOLOGY"]
    )
    compound_technology["TECHNOLOGY_C"] = [
        "Webex Contact Center Enterprise / Cisco UCCE"
    ]
    assert build_analysis_bundle(
        conflicting_technology_request,
        AnalysisSources(subscriptions=compound_technology),
    ).portfolio.customer_count == 0

    for malformed_value in (
        {"product": "Webex Contact Center Enterprise"},
        ["Webex Contact Center Enterprise"],
    ):
        malformed_technology = _subscriptions("Acme Inc").drop(
            columns=["TECHNOLOGY"]
        )
        malformed_technology["TECHNOLOGY_C"] = [malformed_value]
        assert build_analysis_bundle(
            conflicting_technology_request,
            AnalysisSources(subscriptions=malformed_technology),
        ).portfolio.customer_count == 0

    other_source_only = AnalysisSources(
        subscriptions=None,
        adoption_barriers=pd.DataFrame(
            [
                {
                    "ID": "AB-OTHER-TECH",
                    "BU_NAME": "Acme Inc",
                    "TECHNOLOGY_C": "Cisco UCCE",
                }
            ]
        ),
    )
    assert build_analysis_bundle(
        conflicting_technology_request,
        other_source_only,
    ).portfolio.customer_count == 0

    duplicate_schema_sources = AnalysisSources(
        subscriptions=pd.concat(
            [
                _subscriptions("Acme Inc"),
                pd.DataFrame({"BU_NAME": ["Acme Inc"]}),
            ],
            axis=1,
        ),
        adoption_barriers=pd.DataFrame(
            [
                [
                    "AB-1",
                    "AB-1",
                    "Acme Inc",
                    "High",
                    "High",
                    "Open",
                    "Open",
                ]
            ],
            columns=[
                "ID",
                "ID",
                "BU_NAME",
                "SEVERITY_C",
                "SEVERITY_C",
                "STATUS_C",
                "STATUS_C",
            ],
        ),
        support_cases=pd.DataFrame(
            [
                [
                    "SR-1",
                    "Acme Inc",
                    "P2",
                    "P2",
                    "TX-1",
                    "TX-1",
                    OBSERVED,
                    OBSERVED,
                    "Open",
                ]
            ],
            columns=[
                "SR_NUMBER",
                "BU_NAME",
                "PRIORITY",
                "PRIORITY",
                "Transaction ID",
                "Transaction ID",
                "LAST_MODIFIED_DATE",
                "LAST_MODIFIED_DATE",
                "STATUS",
            ],
        ),
        customer_pulse=pd.DataFrame(
            [
                [
                    "P-1",
                    "Acme Inc",
                    8,
                    8,
                    OBSERVED,
                    OBSERVED,
                ]
            ],
            columns=[
                "CUSTOMER_PULSE_ID_C",
                "BU_NAME",
                "SCORE",
                "SCORE",
                "UPDATED_AT",
                "UPDATED_AT",
            ],
        ),
        action_plans=pd.DataFrame(
            [
                [
                    "AP-1",
                    "Acme Inc",
                    "Open",
                    "Open",
                    OBSERVED,
                    OBSERVED,
                ]
            ],
            columns=[
                "ACTION_PLAN_ID",
                "BU_NAME",
                "STATUS",
                "STATUS",
                "UPDATED_AT",
                "UPDATED_AT",
            ],
        ),
    )
    duplicate_schema_bundle = build_analysis_bundle(
        _request("Acme Inc"),
        duplicate_schema_sources,
    )
    assert duplicate_schema_bundle.portfolio.customer_count == 1
    assert duplicate_schema_bundle.customers[0].subscriptions == ("SUB-1",)
    assert all(
        duplicate_schema_bundle.context.source_availability[source]
        == "available"
        for source in (
            "subscriptions",
            "adoption_barriers",
            "support_cases",
            "customer_pulse",
            "action_plans",
        )
    )

    conflicting_bu = pd.concat(
        [
            _subscriptions("Acme Inc"),
            pd.DataFrame({"BU_NAME": ["Different Customer"]}),
        ],
        axis=1,
    )
    conflicting_bu_bundle = build_analysis_bundle(
        _request("Acme Inc", allowed_source_types=("subscriptions",)),
        AnalysisSources(subscriptions=conflicting_bu),
    )
    assert conflicting_bu_bundle.context.source_availability[
        "subscriptions"
    ] == "conflict_only"
    assert conflicting_bu_bundle.customers[0].subscriptions == ()


def test_idless_row_signature_ignores_benign_schema_format_and_duplicates() -> None:
    clean = pd.DataFrame(
        [
            {
                "BU_NAME": "Acme Inc",
                "Severity C": "High",
                "Status C": "Open",
                "Updated At": OBSERVED,
            }
        ]
    )
    agreeing_duplicates = pd.DataFrame(
        [
            [
                "Acme Inc",
                "High",
                "High",
                "Open",
                "Open",
                OBSERVED,
                OBSERVED,
            ]
        ],
        columns=[
            "BU_NAME",
            "SEVERITY_C",
            "SEVERITY_C",
            "STATUS_C",
            "STATUS_C",
            "UPDATED_AT",
            "UPDATED_AT",
        ],
    )
    request = _request(
        "Acme Inc",
        allowed_source_types=("adoption_barriers",),
    )
    clean_bundle = build_analysis_bundle(
        request,
        AnalysisSources(adoption_barriers=clean),
    )
    duplicate_bundle = build_analysis_bundle(
        request,
        AnalysisSources(adoption_barriers=agreeing_duplicates),
    )

    def _row_records(bundle: AnalysisBundle):
        return [
            evidence
            for evidence in bundle.evidence
            if evidence.source_type == "adoption_barriers"
            and evidence.source_record != "source-state"
        ]

    clean_records = _row_records(clean_bundle)
    duplicate_records = _row_records(duplicate_bundle)
    assert len(clean_records) == len(duplicate_records) == 1
    assert (
        clean_records[0].stable_source_identifier
        == duplicate_records[0].stable_source_identifier
    )
    assert clean_records[0].evidence_id == duplicate_records[0].evidence_id

    conflicting_duplicates = agreeing_duplicates.copy()
    conflicting_duplicates.iloc[0, 2] = "Low"
    conflict_bundle = build_analysis_bundle(
        request,
        AnalysisSources(adoption_barriers=conflicting_duplicates),
    )
    assert _row_records(conflict_bundle) == []
    assert any(
        warning.startswith("adoption_barriers:")
        and "duplicate schema" in warning
        for warning in conflict_bundle.diagnostics.quarantined_records
    )


@pytest.mark.parametrize(
    ("scope_name", "column_name", "authorized_value"),
    (
        ("account_scope", "ACCOUNT_ID_C", "ACC-1"),
        ("subscription_scope", "SUBSCRIPTION_ID", "SUB-1"),
        ("team_scope", "CSSM_EMAIL", "alice@cisco.com"),
        ("leader_scope", "CSSM_MANAGER", "Synthetic Leader"),
        ("renewal_scope", "SUBSCRIPTION_ID", "SUB-1"),
    ),
)
def test_object_stringification_cannot_satisfy_source_identity_scope(
    scope_name: str,
    column_name: str,
    authorized_value: str,
) -> None:
    subscriptions = _subscriptions("Acme Inc")
    subscriptions[column_name] = [_ScopeStringSpoof(authorized_value)]
    request = AnalysisRequest(
        **{
            scope_name: (authorized_value,),
            "as_of_time": AS_OF,
            "allowed_source_types": ("subscriptions",),
        }
    )
    bundle = build_analysis_bundle(
        request,
        AnalysisSources(subscriptions=subscriptions),
    )
    assert bundle.portfolio.customer_count == 0


@pytest.mark.parametrize(
    ("scope_name", "column_name"),
    (
        ("account_scope", "ACCOUNT_ID_C"),
        ("subscription_scope", "SUBSCRIPTION_ID"),
        ("team_scope", "TEAM_NAME"),
        ("leader_scope", "CSSM_MANAGER"),
    ),
)
def test_numeric_cells_cannot_satisfy_source_identity_scope(
    scope_name: str,
    column_name: str,
) -> None:
    subscriptions = _subscriptions("Acme Inc")
    subscriptions[column_name] = [123]
    request = AnalysisRequest(
        **{
            scope_name: ("123",),
            "as_of_time": AS_OF,
            "allowed_source_types": ("subscriptions",),
        }
    )
    bundle = build_analysis_bundle(
        request,
        AnalysisSources(subscriptions=subscriptions),
    )
    assert bundle.portfolio.customer_count == 0


@pytest.mark.parametrize(
    ("source_value", "requested_customer"),
    (
        (_ScopeStringSpoof("Acme Inc"), "Acme Inc"),
        (123, "123"),
    ),
)
def test_non_string_customer_cells_cannot_partition_source_evidence(
    source_value: object,
    requested_customer: str,
) -> None:
    subscriptions = _subscriptions("Acme Inc")
    subscriptions["BU_NAME"] = [source_value]
    request = AnalysisRequest(
        customer_scope=(requested_customer,),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    bundle = build_analysis_bundle(
        request,
        AnalysisSources(subscriptions=subscriptions),
    )
    # Explicit customer requests retain an empty analysis shell, but the
    # malformed source row must not attach as customer evidence.
    assert bundle.portfolio.customer_count == 1
    assert bundle.customers[0].subscriptions == ()
    assert all(
        evidence.source_record == "source-state"
        for evidence in bundle.evidence
    )


def test_missing_identity_aliases_preserve_valid_string_scope_evidence() -> None:
    subscriptions = _subscriptions("Acme Inc")
    subscriptions["ACCOUNT__C"] = [pd.NA]
    subscriptions["SUBSCRIPTION_ID_C"] = [None]
    subscriptions["CSSM_EMAIL"] = ["alice@cisco.com"]
    subscriptions["OWNER_EMAIL"] = [pd.NA]
    subscriptions["CSSM_MANAGER"] = ["Synthetic Leader"]
    subscriptions["MANAGER_NAME"] = [None]
    request = AnalysisRequest(
        account_scope=("ACC-1",),
        subscription_scope=("SUB-1",),
        team_scope=("alice@cisco.com",),
        leader_scope=("Synthetic Leader",),
        renewal_scope=("SUB-1",),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    bundle = build_analysis_bundle(
        request,
        AnalysisSources(subscriptions=subscriptions),
    )
    assert bundle.portfolio.customer_count == 1
    assert bundle.customers[0].subscriptions == ("SUB-1",)


@pytest.mark.parametrize("requested_customer", ("Acme Inc", "Beta LLC"))
def test_conflicting_populated_customer_aliases_are_quarantined(
    requested_customer: str,
) -> None:
    subscriptions = _subscriptions("Acme Inc")
    subscriptions["CUSTOMER_NAME"] = ["Beta LLC"]
    request = AnalysisRequest(
        customer_scope=(requested_customer,),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    bundle = build_analysis_bundle(
        request,
        AnalysisSources(subscriptions=subscriptions),
    )
    assert bundle.portfolio.customer_count == 1
    assert bundle.customers[0].subscriptions == ()
    assert all(
        evidence.source_record == "source-state"
        for evidence in bundle.evidence
    )
    assert any(
        "conflicting customer/account identity aliases" in warning
        for warning in bundle.diagnostics.quarantined_records
    )


def _identity_scope_subscriptions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-A",
                "ACCOUNT_ID_C": "ACC-A",
                "BU_NAME": "Acme Inc",
                "TECHNOLOGY": "Collaboration",
            },
            {
                "SUBSCRIPTION_ID": "SUB-B",
                "ACCOUNT_ID_C": "ACC-B",
                "BU_NAME": "Beta LLC",
                "TECHNOLOGY": "Collaboration",
            },
        ]
    )


def test_conflicting_account_aliases_without_customer_label_are_quarantined() -> None:
    support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-CONFLICT",
                "ACCOUNT_ID_C": "ACC-A",
                "ACCOUNT__C": "ACC-B",
                "PRIORITY": "P1",
                "STATUS": "Open",
            }
        ]
    )
    bundle = build_analysis_bundle(
        _request(
            "Acme Inc",
            allowed_source_types=("subscriptions", "support_cases"),
        ),
        AnalysisSources(
            subscriptions=_identity_scope_subscriptions(),
            support_cases=support_cases,
        ),
    )
    assert bundle.customers[0].metric("total_cases") == 0
    assert not any(
        evidence.source_type == "support_cases"
        and evidence.source_record != "source-state"
        for evidence in bundle.evidence
    )
    assert any(
        warning.startswith("support_cases:")
        and "conflicting customer/account identity aliases" in warning
        for warning in bundle.diagnostics.quarantined_records
    )


def test_equivalent_account_aliases_without_customer_label_still_resolve() -> None:
    support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-AGREE",
                "ACCOUNT_ID_C": "ACC-A",
                "ACCOUNT__C": "acc-a",
                "PRIORITY": "P2",
                "STATUS": "Open",
            }
        ]
    )
    bundle = build_analysis_bundle(
        _request(
            "Acme Inc",
            allowed_source_types=("subscriptions", "support_cases"),
        ),
        AnalysisSources(
            subscriptions=_identity_scope_subscriptions(),
            support_cases=support_cases,
        ),
    )
    assert bundle.customers[0].metric("total_cases") == 1
    assert any(
        evidence.source_type == "support_cases"
        and evidence.stable_source_identifier == "SR-AGREE"
        for evidence in bundle.evidence
    )


def test_equivalent_salesforce_15_and_18_account_aliases_still_resolve() -> None:
    account_15 = "001ABCDEFGHIJKL"
    account_18 = "001ABCDEFGHIJKLY55"
    subscriptions = _identity_scope_subscriptions().iloc[[0]].copy()
    subscriptions["ACCOUNT_ID_C"] = [account_18]
    support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-SALESFORCE-EQUIVALENT",
                "ACCOUNT_ID_C": account_15,
                "PRIORITY": "P2",
                "STATUS": "Open",
            }
        ]
    )
    bundle = build_analysis_bundle(
        _request(
            "Acme Inc",
            allowed_source_types=("subscriptions", "support_cases"),
        ),
        AnalysisSources(
            subscriptions=subscriptions,
            support_cases=support_cases,
        ),
    )
    assert bundle.customers[0].metric("total_cases") == 1


@pytest.mark.parametrize(
    ("first_account", "second_account", "requested_account"),
    (
        ("ACC-A", "acc-a", "ACC-A"),
        (
            "001ABCDEFGHIJKL",
            "001ABCDEFGHIJKLY55",
            "001ABCDEFGHIJKL",
        ),
    ),
)
def test_equivalent_account_ids_with_conflicting_owners_fail_closed(
    first_account: str,
    second_account: str,
    requested_account: str,
) -> None:
    subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-A",
                "ACCOUNT_ID_C": first_account,
                "BU_NAME": "Acme Inc",
                "TECHNOLOGY": "Collaboration",
            },
            {
                "SUBSCRIPTION_ID": "SUB-B",
                "ACCOUNT_ID_C": second_account,
                "BU_NAME": "Beta LLC",
                "TECHNOLOGY": "Collaboration",
            },
        ]
    )
    request = AnalysisRequest(
        account_scope=(requested_account,),
        as_of_time=AS_OF,
        allowed_source_types=("subscriptions",),
    )
    bundle = build_analysis_bundle(
        request,
        AnalysisSources(subscriptions=subscriptions),
    )
    assert bundle.portfolio.customer_count == 0
    assert any(
        "equivalent account identifier" in warning
        and "fails closed" in warning
        for warning in bundle.context.warnings
    )


@pytest.mark.parametrize(
    ("subscription_account", "case_account"),
    (
        ("ACC-A", "acc-a"),
        ("001ABCDEFGHIJKLY55", "001ABCDEFGHIJKL"),
    ),
)
def test_equivalent_same_owner_account_id_resolves_across_sources(
    subscription_account: str,
    case_account: str,
) -> None:
    subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-A",
                "ACCOUNT_ID_C": subscription_account,
                "BU_NAME": "Acme Inc",
                "TECHNOLOGY": "Collaboration",
            }
        ]
    )
    support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-EQUIVALENT-ACCOUNT",
                "ACCOUNT_ID_C": case_account,
                "PRIORITY": "P2",
                "STATUS": "Open",
            }
        ]
    )
    bundle = build_analysis_bundle(
        _request(
            "Acme Inc",
            allowed_source_types=("subscriptions", "support_cases"),
        ),
        AnalysisSources(
            subscriptions=subscriptions,
            support_cases=support_cases,
        ),
    )
    assert bundle.customers[0].metric("total_cases") == 1
    assert any(
        evidence.source_type == "support_cases"
        and evidence.stable_source_identifier == "SR-EQUIVALENT-ACCOUNT"
        for evidence in bundle.evidence
    )


def test_customer_label_and_account_owner_mismatch_is_quarantined() -> None:
    support_cases = pd.DataFrame(
        [
            {
                "SR_NUMBER": "SR-MISMATCH",
                "BU_NAME": "Acme Inc",
                "ACCOUNT_ID_C": "ACC-B",
                "PRIORITY": "P1",
                "STATUS": "Open",
            }
        ]
    )
    bundle = build_analysis_bundle(
        _request(
            "Acme Inc",
            "Beta LLC",
            allowed_source_types=("subscriptions", "support_cases"),
        ),
        AnalysisSources(
            subscriptions=_identity_scope_subscriptions(),
            support_cases=support_cases,
        ),
    )
    assert all(customer.metric("total_cases") == 0 for customer in bundle.customers)
    assert not any(
        evidence.source_type == "support_cases"
        and evidence.source_record != "source-state"
        for evidence in bundle.evidence
    )
    assert any(
        warning.startswith("support_cases:")
        and "conflicting customer/account identity aliases" in warning
        for warning in bundle.diagnostics.quarantined_records
    )


def test_mixed_incident_feed_keeps_global_context_without_cross_customer_leak() -> None:
    customers = ("Acme Inc", "Beta LLC")
    sources = _complete_sources(*customers)
    sources.external_incidents = (
        {
            "id": "INC-GLOBAL",
            "title": "Global service maintenance",
            "status": "resolved",
            "updated_at": OBSERVED,
        },
        {
            "id": "INC-ACME",
            "title": "Acme-specific incident",
            "status": "resolved",
            "customer_name": "Acme Inc",
            "updated_at": OBSERVED,
        },
    )
    bundle = build_analysis_bundle(_request(*customers), sources)
    incident_ids_by_customer = {
        customer.customer_name: {
            evidence.stable_source_identifier
            for evidence in bundle.evidence
            if evidence.customer_identity == customer.customer_id
            and evidence.source_type == "external_incidents"
            and evidence.source_record != "source-state"
        }
        for customer in bundle.customers
    }
    assert incident_ids_by_customer["Acme Inc"] == {"INC-GLOBAL", "INC-ACME"}
    assert incident_ids_by_customer["Beta LLC"] == {"INC-GLOBAL"}


def test_portfolio_totals_are_exact_customer_derived_sums() -> None:
    customers = ("Acme Inc", "Beta LLC")
    sources = _complete_sources(*customers)
    sources.adoption_barriers = pd.DataFrame(
        [
            {
                "ID": f"AB-{index}",
                "BU_NAME": customer,
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "LAST_MODIFIED_DATE": OBSERVED,
            }
            for index, customer in enumerate(customers, 1)
        ]
    )
    bundle = build_analysis_bundle(_request(*customers), sources)
    expected = sum(
        customer.metric("critical_high_barriers", 0) for customer in bundle.customers
    )
    assert expected == 2
    assert bundle.portfolio.metric("critical_high_barriers") == expected
    assert bundle.portfolio.customer_count == len(bundle.customers)
    assert bundle.reconciliation_errors() == []


def test_snapshot_store_is_atomic_private_and_scope_bound(tmp_path) -> None:
    request = _request("Acme Inc")
    bundle = build_analysis_bundle(request, _complete_sources("Acme Inc"))
    store = AnalysisSnapshotStore(tmp_path / "snapshots")
    path = store.persist(bundle)
    assert path.is_file()
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    assert store.load_latest(request).to_dict() == bundle.to_dict()
    assert store.load_latest(_request("Other Corp")) is None
    assert not list(path.parent.glob("*.tmp"))


def test_incompatible_snapshot_is_explicitly_not_comparable() -> None:
    prior_request = AnalysisRequest(
        tenant_scope="synthetic-test-tenant",
        customer_scope=("Acme Inc",),
        as_of_time="2026-06-13T12:00:00Z",
    )
    prior = build_analysis_bundle(prior_request, _complete_sources("Acme Inc"))
    payload = prior.to_dict()
    payload["schema_version"] = "1.9.0"
    incompatible = AnalysisBundle.from_dict(payload)
    current = build_analysis_bundle(
        _request("Acme Inc"),
        _complete_sources("Acme Inc"),
        prior_bundle=incompatible,
    )
    classifications = {
        change.classification
        for customer in current.customers
        for change in customer.temporal_changes
    }
    assert classifications == {"not_comparable"}
    assert any("schema" in warning.casefold() for warning in current.context.warnings)


@pytest.mark.parametrize(
    ("deduplicator_name", "frame"),
    (
        (
            "deduplicate_tac_cases",
            pd.DataFrame(
                [
                    {"SR_NUMBER": "SR-1", "BU_NAME": "Acme Inc", "STATUS": "Open"},
                    {"SR_NUMBER": "SR-1", "BU_NAME": "Acme Inc", "STATUS": "Open"},
                ]
            ),
        ),
        (
            "deduplicate_action_plans",
            pd.DataFrame(
                [
                    {"ID": "AP-1", "BU_NAME": "Acme Inc", "STATUS_C": "Open"},
                    {"ID": "AP-1", "BU_NAME": "Acme Inc", "STATUS_C": "Open"},
                ]
            ),
        ),
        (
            "deduplicate_customer_pulse",
            pd.DataFrame(
                [
                    {"ID": "CP-1", "BU_NAME": "Acme Inc", "SCORE__C": 7},
                    {"ID": "CP-1", "BU_NAME": "Acme Inc", "SCORE__C": 7},
                ]
            ),
        ),
    ),
)
def test_canonical_logical_frames_skip_repeat_work_without_aliasing(
    monkeypatch,
    deduplicator_name: str,
    frame: pd.DataFrame,
) -> None:
    deduplicator = getattr(cm, deduplicator_name)
    first = deduplicator(frame)
    assert len(first) == 1

    monkeypatch.setattr(
        cm,
        "quarantine_cross_customer_record_ids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("canonical frame was reprocessed")
        ),
    )
    second = deduplicator(first)
    pd.testing.assert_frame_equal(second, first)
    assert second is not first
    second.iloc[0, 0] = "changed"
    assert first.iloc[0, 0] != "changed"


@pytest.mark.parametrize(
    ("deduplicator_name", "id_column"),
    (
        ("deduplicate_tac_cases", "SR_NUMBER"),
        ("deduplicate_action_plans", "ID"),
        ("deduplicate_customer_pulse", "ID"),
    ),
)
def test_canonical_cache_rejects_mutation_and_subset_concat_duplicates(
    deduplicator_name: str,
    id_column: str,
) -> None:
    deduplicator = getattr(cm, deduplicator_name)
    original = pd.DataFrame(
        [
            {id_column: "A", "BU_NAME": "Acme Inc", "STATUS": "Open"},
            {id_column: "B", "BU_NAME": "Acme Inc", "STATUS": "Open"},
        ]
    )
    canonical = deduplicator(original)
    assert len(canonical) == 2

    mutated = canonical.copy()
    mutated.loc[mutated.index[1], id_column] = "A"
    assert len(deduplicator(mutated)) == 1

    first_row = canonical.iloc[[0]].copy()
    forged_concat = pd.concat([first_row, first_row], ignore_index=True)
    # Explicitly mirror integration code that aggregates source attrs.
    forged_concat.attrs.update(canonical.attrs)
    collapsed = deduplicator(forged_concat)
    assert len(collapsed) == 1
    assert collapsed.iloc[0][id_column] == "A"


@pytest.mark.parametrize(
    ("deduplicator_name", "id_column"),
    (
        ("deduplicate_tac_cases", "SR_NUMBER"),
        ("deduplicate_action_plans", "ID"),
        ("deduplicate_customer_pulse", "ID"),
    ),
)
def test_canonical_cache_accepts_a_genuine_row_subset(
    monkeypatch,
    deduplicator_name: str,
    id_column: str,
) -> None:
    deduplicator = getattr(cm, deduplicator_name)
    canonical = deduplicator(
        pd.DataFrame(
            [
                {id_column: "A", "BU_NAME": "Acme Inc"},
                {id_column: "B", "BU_NAME": "Acme Inc"},
            ]
        )
    )
    subset = canonical.iloc[[0]].copy()
    monkeypatch.setattr(
        cm,
        "quarantine_cross_customer_record_ids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("genuine canonical subset was reprocessed")
        ),
    )
    result = deduplicator(subset)
    assert result[id_column].tolist() == ["A"]


def test_source_specific_canonicalization_failure_never_claims_canonical(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cm,
        "deduplicate_tac_cases",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic canonical dedupe failure")
        ),
    )
    with pytest.raises(RuntimeError, match="synthetic canonical dedupe failure"):
        build_analysis_bundle(_request("Acme Inc"), _complete_sources("Acme Inc"))
