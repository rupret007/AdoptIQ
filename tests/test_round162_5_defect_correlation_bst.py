"""Round 162.5: exact CSC correlation and official BST credential wiring."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from cisco_internal_integrations import CiscoInternalIntegrations, DefectInfo
from defect_correlation import (
    build_defect_correlation_bundle,
    correlate_scoped_defects,
    extract_csc_ids,
    normalize_csc_id,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_csc_extraction_is_exact_normalized_and_never_treats_bems_as_a_defect() -> None:
    value = (
        "Mixed cScWa12345 and CSCxy00001; BEMS01916938; "
        "BEMSCSCzz99999; CSCab1234; CSCab123456"
    )

    assert extract_csc_ids(value) == ("CSCWA12345", "CSCXY00001")
    assert extract_csc_ids(["bems00112233", {"ref": "CsCdr72939"}]) == ("CSCDR72939",)
    assert normalize_csc_id(" cScWa12345 ") == "CSCWA12345"
    assert normalize_csc_id("BEMS01916938") is None
    assert normalize_csc_id("prefix CSCwa12345") is None


def test_exact_scoped_correlation_carries_both_parent_sources_and_verified_bug_metadata() -> None:
    tac = pd.DataFrame(
        [
            {
                "ACCOUNT_ID": "001-A",
                "Customer Name": "Acme Corporation",
                "SR Number": "TAC-100",
                "Title": "Intermittent crash cScWa12345",
                "bemscsc_refs": "BEMS01916938, CSCwa12345",
            },
            {
                "ACCOUNT_ID": "001-A",
                "Customer Name": "Acme Corporation",
                "SR Number": "TAC-BEMS-ONLY",
                "Title": "BEMS01999999 engineering escalation",
                "bemscsc_refs": "BEMS01999999",
            },
        ]
    )
    barriers = pd.DataFrame(
        [
            {
                "customer_name": "  Acme Corporation ",
                "ID": "AB-9",
                "SUBJECT_C": "Upgrade blocked by CSCWA12345",
            }
        ]
    )
    external = [
        {
            "bug_id": "cscwA12345",
            "headline": "Crash during reconnect",
            "Status": "Open",
            "SEVERITY": "2",
            "known_fixed_releases": "44.3.1",
            "source": "Cisco Bug Search Tool",
        },
        {
            "bug_id": "CSCzz99999",
            "headline": "Public bug with no scoped customer evidence",
            "status": "Fixed",
        },
    ]

    bundle = build_defect_correlation_bundle(tac, barriers, external)

    assert len(bundle["records"]) == 1
    record = bundle["records"][0]
    assert record["identity_key"] == "account:001-a"
    assert record["account_id"] == "001-A"
    assert record["customer_name"] == "Acme Corporation"
    assert record["csc_id"] == "CSCWA12345"
    assert record["parent_source_sheets"] == ["Adoption_Barriers", "TAC_Cases"]
    assert record["parent_record_count"] == 2
    assert {(item["source_sheet"], item["source_position"], item["stable_id"]) for item in record["parent_records"]} == {
        ("TAC_Cases", 1, "TAC-100"),
        ("Adoption_Barriers", 1, "AB-9"),
    }
    assert record["verified_external_match"] is True
    assert record["verified_external_bug_id"] == "CSCWA12345"
    assert record["verified_external_status"] == "Open"
    assert record["verified_external_severity"] == "2"
    assert record["verified_external_version"] == "44.3.1"
    assert "verified CSCWA12345" in record["action_context"]
    assert all("BEMS" not in str(value) for value in record.values())
    assert "numeric_risk_weight" not in record

    assert bundle["unmatched_external_bugs"] == [
        {
            "csc_id": "CSCZZ99999",
            "status": "Fixed",
            "severity": "",
            "version": "",
            "title": "Public bug with no scoped customer evidence",
            "source": "External_Bugs",
            "source_positions": [2],
            "context_only": True,
            "reason": (
                "No exact CSC reference exists in the criteria-scoped TAC or Adoption Barrier rows; "
                "do not infer customer impact."
            ),
        }
    ]
    assert bundle["coverage"]["sources"]["TAC_Cases"]["state"] == "available"
    assert bundle["coverage"]["sources"]["Adoption_Barriers"]["state"] == "available"
    assert bundle["coverage"]["sources"]["External_Bugs"]["state"] == "available"


def test_customer_association_is_id_first_and_does_not_merge_ambiguous_duplicate_names() -> None:
    tac = pd.DataFrame(
        [
            {"ACCOUNT_ID": "A-1", "Customer Name": "Shared Name", "SR Number": "T-1", "Title": "CSCaa11111"},
            {"ACCOUNT_ID": "A-2", "Customer Name": "Shared Name", "SR Number": "T-2", "Title": "CSCaa11111"},
        ]
    )
    barriers = pd.DataFrame(
        [{"customer_name": "Shared Name", "ID": "AB-1", "description": "CSCaa11111"}]
    )

    records = correlate_scoped_defects(tac, barriers, [])

    assert [record["identity_key"] for record in records] == [
        "account:a-1",
        "account:a-2",
        "name:shared name",
    ]
    assert all(record["csc_id"] == "CSCAA11111" for record in records)


def test_caller_supplied_identity_resolver_can_join_sources_on_a_canonical_account() -> None:
    tac = [{"Customer Name": "Acme, Inc.", "SR Number": "T-1", "Title": "CSCaa11111"}]
    barriers = [{"customer_name": "ACME INC", "ID": "AB-1", "description": "cscAA11111"}]

    def resolver(_row, _source_sheet):
        return {
            "identity_key": "canonical-account-42",
            "account_id": "42",
            "customer_name": "Acme Inc.",
        }

    records = correlate_scoped_defects(tac, barriers, [], identity_resolver=resolver)

    assert len(records) == 1
    assert records[0]["identity_key"] == "caller:canonical-account-42"
    assert records[0]["association_method"] == "caller_supplied"
    assert records[0]["parent_record_count"] == 2


def test_coverage_distinguishes_unavailable_failed_partial_and_real_zero() -> None:
    failed = pd.DataFrame()
    failed.attrs["fetch_error"] = "TAC query failed"
    partial = pd.DataFrame([{"ID": "AB-1", "Title": "CSCaa11111"}])
    partial.attrs["fetch_error"] = "second page timed out"
    partial.attrs["fetch_error_partial"] = True

    bundle = build_defect_correlation_bundle(failed, partial, None)
    coverage = bundle["coverage"]["sources"]

    assert coverage["TAC_Cases"] == {
        "state": "failed",
        "row_count": 0,
        "detail": "TAC query failed",
    }
    assert coverage["Adoption_Barriers"]["state"] == "partial"
    assert coverage["Adoption_Barriers"]["row_count"] == 1
    assert coverage["External_Bugs"] == {
        "state": "unavailable",
        "row_count": None,
        "detail": "source not supplied",
    }

    zero = build_defect_correlation_bundle([], [], [])
    assert {state["state"] for state in zero["coverage"]["sources"].values()} == {"zero"}


def test_output_bounds_and_order_are_deterministic() -> None:
    tac = [
        {"SR Number": "T-2", "Customer Name": "Zulu", "Title": "CSCzz99999"},
        {"SR Number": "T-1", "Customer Name": "Alpha", "Title": "CSCaa11111"},
        {"SR Number": "T-3", "Customer Name": "Alpha", "Title": "CSCaa11111"},
    ]
    external = [
        {"bug_id": "CSCzz99999"},
        {"bug_id": "CSCaa11111"},
        {"bug_id": "CSCbb22222"},
        {"bug_id": "CSCcc33333"},
    ]

    first = build_defect_correlation_bundle(
        tac,
        [],
        external,
        max_correlations=1,
        max_parent_records=1,
        max_context_bugs=1,
    )
    second = build_defect_correlation_bundle(
        tac,
        [],
        external,
        max_correlations=1,
        max_parent_records=1,
        max_context_bugs=1,
    )

    assert first == second
    assert [record["csc_id"] for record in first["records"]] == ["CSCAA11111"]
    assert first["records"][0]["parent_records_truncated"] is True
    assert first["coverage"]["correlations_truncated"] is True
    assert first["coverage"]["parent_records_truncated"] is True
    assert first["coverage"]["unmatched_external_bugs_truncated"] is True
    # CSCZZ99999 has scoped internal evidence beyond the presentation cap, so
    # it must not be mislabeled as an unmatched public bug.
    assert [row["csc_id"] for row in first["unmatched_external_bugs"]] == ["CSCBB22222"]


def test_bst_key_and_secret_activate_official_path(monkeypatch) -> None:
    client = CiscoInternalIntegrations(bst_api_key="client-id", bst_client_secret="client-secret")
    sentinel = DefectInfo(
        defect_id="CSCWA12345",
        title="Known defect",
        status="Open",
        severity="Severity 2",
        product="Webex",
        component="Calling",
        description="",
        resolution=None,
        created_date="",
        modified_date="",
        assignee="",
        classification=client._classify_data("bst", "Known defect"),
        source="Cisco Bug API (Official)",
        verification_method="BST API Bug ID: CSCWA12345",
    )
    calls = []

    def official(search_terms, product_filter, days_back):
        calls.append((search_terms, product_filter, days_back))
        return [sentinel]

    monkeypatch.setattr(client, "_search_defects_bst_official_api", official)
    monkeypatch.delenv("BST_ENABLE_WEB_SCRAPING", raising=False)

    assert client.search_defects_bst(["calling"], "Webex", 30) == [sentinel]
    assert calls == [(["calling"], "Webex", 30)]


def test_bst_missing_secret_stays_fail_soft_and_never_calls_official_path(monkeypatch) -> None:
    client = CiscoInternalIntegrations(bst_api_key="client-id")
    monkeypatch.setattr(
        client,
        "_search_defects_bst_official_api",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("official path must not run")),
    )
    monkeypatch.delenv("BST_ENABLE_WEB_SCRAPING", raising=False)

    assert client.search_defects_bst(["calling"], "Webex", 30) == []
    assert client._get_bst_oauth_token() is None


def test_bst_token_failure_never_logs_credentials(monkeypatch, caplog) -> None:
    secret = "do-not-log-this-client-secret"
    client_id = "do-not-log-this-client-id"
    client = CiscoInternalIntegrations(bst_api_key=client_id, bst_client_secret=secret)

    class Response:
        status_code = 401
        text = f"invalid client_id={client_id} client_secret={secret}"

    monkeypatch.setattr("cisco_internal_integrations.requests.post", lambda *_args, **_kwargs: Response())
    with caplog.at_level(logging.ERROR):
        assert client._get_bst_oauth_token() is None

    assert client_id not in caplog.text
    assert secret not in caplog.text
    assert "body_digest=" in caplog.text


def test_all_four_app_integration_constructors_wire_bst_client_secret() -> None:
    source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    constructor_count = source.count("CiscoInternalIntegrations(")
    wiring_count = source.count('bst_client_secret=os.environ.get("BST_CLIENT_SECRET")')

    assert constructor_count == 4
    assert wiring_count == constructor_count
