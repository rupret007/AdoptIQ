"""Canonical report regressions for exact CSC defect correlations."""

from __future__ import annotations

from collections import OrderedDict

import pandas as pd

import decision_report_delivery as delivery


AS_OF = pd.Timestamp("2026-08-03T12:00:00Z")


def _team() -> dict:
    return {
        "Jordan": {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "ACCOUNT_ID_C": "ACC-1",
                        "SUBSCRIPTION_ID": "SUB-1",
                        "BU_NAME": "Acme",
                    }
                ]
            ),
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(
                [
                    {
                        "ACCOUNT_ID_C": "ACC-1",
                        "BU_NAME": "Acme",
                        "ID": "AB-1",
                        "SUBJECT_C": "Upgrade blocked by CSCWA12345",
                        "OPEN_DATE_C": "2026-07-01",
                        "SEVERITY_C": "High",
                    }
                ]
            ),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(
                [
                    {
                        "ACCOUNT_ID_C": "ACC-1",
                        "BU_NAME": "Acme",
                        "SR Number": "TAC-1",
                        "Title": "Reconnect crash cScWa12345",
                        "Date/Time Opened": "2026-07-20",
                        "Severity": "P3",
                    },
                    {
                        "ACCOUNT_ID_C": "ACC-1",
                        "BU_NAME": "Acme",
                        "SR Number": "TAC-BEMS",
                        "Title": "BEMS01916938 escalation",
                        "Date/Time Opened": "2026-07-21",
                        "Severity": "P3",
                    },
                ]
            ),
            "success_priorities": pd.DataFrame(),
        }
    }


def _facts(external_bugs) -> dict:
    return delivery.build_report_facts(
        _team(),
        report_type="Leader",
        scope_type="team",
        scope_value="Jordan's Team",
        manager_name="Jordan",
        days=90,
        as_of=AS_OF,
        external_incidents=[],
        external_bugs=external_bugs,
    )


def test_exact_csc_is_visible_auditable_and_has_no_independent_risk_weight() -> None:
    external_bug = {
        "bug_id": "cscwa12345",
        "headline": "Reconnect crash",
        "status": "Open",
        "severity": "2",
        "known_fixed_releases": "44.3.1",
    }
    facts = _facts([external_bug])
    baseline = _facts([])

    # External/BST context explains the already-observed TAC/barrier evidence;
    # it is not a new numeric component and cannot silently inflate risk.
    assert facts["risk_profiles"]["Acme"]["risk_score_0_100"] == baseline[
        "risk_profiles"
    ]["Acme"]["risk_score_0_100"]

    records = facts["defect_correlation_bundle"]["records"]
    assert len(records) == 1
    assert records[0]["csc_id"] == "CSCWA12345"
    assert records[0]["verified_external_match"] is True
    assert records[0]["parent_record_count"] == 2
    assert "BEMS" not in facts["defect_correlations"].to_string()

    correlation_signals = [
        signal
        for signal in facts["decision_signals"]
        if signal["source_key"] == "defect_correlations"
    ]
    assert len(correlation_signals) == 1
    signal = correlation_signals[0]
    assert "Exact CSCWA12345 correlation" in signal["signal"]
    assert "status Open" in signal["signal"]
    assert "verified CSCWA12345" in signal["decision_implication"]

    sheets = delivery.build_source_data_sheets(facts)
    correlation_sheet = sheets["Defect_Correlations"]
    assert len(correlation_sheet) == 1
    assert {
        "Record_ID",
        "Scope_Type",
        "Scope_Value",
        "Source_System",
        "Attributed_Team_Members",
        "CSSM",
    }.issubset(correlation_sheet.columns)
    row = correlation_sheet.iloc[0]
    assert row["Record_ID"] == "CSCWA12345"
    assert row["Scope_Type"] == "team"
    assert row["Scope_Value"] == "Jordan's Team"
    assert row["Source_System"] == "AdoptIQ exact CSC correlation"
    assert row["Attributed_Team_Members"] == "Jordan"
    assert row["CSSM"] == "Jordan"

    evidence_key = signal["evidence_key"]
    lineage = sheets["Metric_Lineage"]
    assert evidence_key in set(lineage["Metric_Key"].astype(str))
    linked = sheets["Evidence_Links"].loc[
        sheets["Evidence_Links"]["Evidence_Key"].astype(str) == evidence_key
    ]
    assert {
        "prioritized_cross_source_signal",
        "exact_csc_parent_record",
        "exact_csc_external_bug_record",
    }.issubset(set(linked["Evidence_Role"].astype(str)))

    doc = delivery.build_concise_word_document(facts)
    assert any(
        "Exact CSCWA12345 correlation" in cell.text
        for table in doc.tables
        for row in table.rows
        for cell in row.cells
    )
    contract = delivery.validate_cross_artifact_contract(facts, sheets, doc)
    assert contract["ok"] is True, contract["errors"]

    tampered = OrderedDict((name, frame.copy()) for name, frame in sheets.items())
    tampered["Defect_Correlations"] = correlation_sheet.iloc[0:0].copy()
    tamper_result = delivery.validate_cross_artifact_contract(facts, tampered, doc)
    assert tamper_result["ok"] is False
    assert any(
        "Defect_Correlations keys differ" in error
        for error in tamper_result["errors"]
    )


def test_empty_defect_sheet_still_exposes_public_provenance_contract() -> None:
    team = _team()
    team["Jordan"]["tac_cases"]["Title"] = "No linked defect reference"
    team["Jordan"]["adoption_barriers"]["SUBJECT_C"] = "Upgrade planning"
    facts = delivery.build_report_facts(
        team,
        report_type="Leader",
        scope_type="team",
        scope_value="Jordan's Team",
        manager_name="Jordan",
        days=90,
        as_of=AS_OF,
        external_incidents=[],
        external_bugs=[],
    )
    sheets = delivery.build_source_data_sheets(facts)

    assert sheets["Defect_Correlations"].empty
    assert {
        "Record_ID",
        "Scope_Type",
        "Scope_Value",
        "Source_System",
        "Attributed_Team_Members",
        "CSSM",
    }.issubset(sheets["Defect_Correlations"].columns)
    result = delivery.validate_cross_artifact_contract(facts, sheets)
    assert result["ok"] is True, result["errors"]


def test_noncanonical_customer_defect_is_withheld_with_report_warning() -> None:
    team = _team()
    # Two authoritative accounts intentionally share one display name. The
    # CSC-bearing TAC row has only that name, so the canonical resolver must
    # decline instead of guessing which account owns the signal.
    team["Jordan"]["subscriptions"] = pd.DataFrame(
        [
            {
                "ACCOUNT_ID_C": "ACC-GHOST-1",
                "SUBSCRIPTION_ID": "SUB-GHOST-1",
                "BU_NAME": "Ghost Customer",
            },
            {
                "ACCOUNT_ID_C": "ACC-GHOST-2",
                "SUBSCRIPTION_ID": "SUB-GHOST-2",
                "BU_NAME": "Ghost Customer",
            },
        ]
    )
    team["Jordan"]["adoption_barriers"] = pd.DataFrame()
    team["Jordan"]["tac_cases"] = pd.DataFrame(
        [
            {
                "BU_NAME": "Ghost Customer",
                "SR Number": "TAC-GHOST-1",
                "Title": "Reconnect crash CSCGH12345",
                "Date/Time Opened": "2026-07-20",
                "Severity": "P2",
            }
        ]
    )

    facts = delivery.build_report_facts(
        team,
        report_type="Leader",
        scope_type="team",
        scope_value="Jordan's Team",
        manager_name="Jordan",
        days=90,
        as_of=AS_OF,
        external_incidents=[],
        external_bugs=[{"bug_id": "CSCGH12345", "status": "Open"}],
    )

    assert facts["defect_correlations"].empty
    assert facts["defect_correlation_bundle"]["records"] == []
    assert facts["defect_correlation_bundle"]["unmatched_external_bugs"] == []
    assert not any(
        signal["source_key"] == "defect_correlations"
        for signal in facts["decision_signals"]
    )
    coverage = facts["defect_correlation_bundle"]["coverage"]["identity_resolution"]
    assert coverage["state"] == "partial"
    assert coverage["quarantined_observation_count"] == 1
    warning = next(
        item
        for item in facts["partial_data_warnings"]
        if item.get("kind") == "identity_resolution_partial"
    )
    assert warning == {
        "dataset": "Defect correlations",
        "kind": "identity_resolution_partial",
        "effect": (
            "1 CSC-bearing source row(s) were retained in their source sheets but "
            "withheld from account-level defect correlations because no canonical "
            "customer identity was available."
        ),
    }
    source_row = facts["source_coverage"].loc[
        facts["source_coverage"]["Source_Sheet"].eq("Defect_Correlations")
    ].iloc[0]
    assert source_row["Source_State"] == "partial"
    assert "withheld" in source_row["Detail"]
    # Raw, unverified identity text cannot escape through report-visible or
    # frozen Ask-AI facts; only aggregate coverage is published.
    public_correlation_payload = {
        "records": facts["defect_correlation_bundle"]["records"],
        "signals": [
            signal
            for signal in facts["decision_signals"]
            if signal["source_key"] == "defect_correlations"
        ],
        "warnings": facts["partial_data_warnings"],
    }
    assert "Ghost Customer" not in repr(public_correlation_payload)
    assert "'Unknown'" not in repr(public_correlation_payload)
