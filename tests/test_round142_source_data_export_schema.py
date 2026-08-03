"""Round 142: canonical Source Data activity-sheet export contract."""

from __future__ import annotations

import pandas as pd
import pytest

import report_export_schema as schema


# Round 142: canonical workbook names must reuse the established source
# projections rather than falling through to denylist-only raw exports.
def test_canonical_source_data_sheet_names_are_curated() -> None:
    assert schema.CURATED_COLUMNS["TAC_Cases"] is schema._CURATED_CSONE_DETAIL_ALL
    assert (
        schema.CURATED_COLUMNS["Customer_Pulse"]
        is schema._CURATED_CSCONSOLE_CUSTOMER_PULSE
    )
    assert (
        schema.CURATED_COLUMNS["BEMS_Escalations"]
        is schema._CURATED_BEMS_ESCALATIONS
    )
    assert schema.CURATED_COLUMNS["BEMS"] is schema._CURATED_BEMS_ESCALATIONS


@pytest.mark.parametrize(
    ("sheet_name", "source_id_column"),
    [
        ("Action_Plans", "ID"),
        ("Adoption_Barriers", "ID"),
        ("Customer_Pulse", "ID"),
        ("TAC_Cases", "SR Number"),
        ("BEMS_Escalations", "Transaction ID"),
    ],
)
# Round 142: each activity record remains independently traceable while raw
# ETL, Salesforce, and AdoptIQ calculation flags remain private.
def test_source_data_keeps_public_lineage_and_denies_internal_metadata(
    sheet_name: str,
    source_id_column: str,
) -> None:
    row = {
        "Record_ID": f"{sheet_name}-canonical-1",
        "Record_ID_Data_Quality": "OK",
        source_id_column: f"{sheet_name}-source-1",
        "CSSM": "Alex CSSM",
        "Scope_Type": "Manager / technology / lookback",
        "Scope_Value": "Manager A | Webex | 90 days",
        "Source_System": "CSConsole" if sheet_name != "TAC_Cases" else "CSOne",
        "Attributed_Team_Members": "Alex CSSM; Jordan CSSM",
        "ETL_ID": "warehouse-only",
        "IS_DELETED": False,
        "RECORD_TYPE_ID": "salesforce-only",
        "EDWSF_FUTURE_FIELD": "warehouse-only",
        "_ATTRIBUTED_BY_ACCOUNT": True,
    }

    out = schema.apply_export_schema(pd.DataFrame([row]), sheet_name)

    assert [c for c in schema._R142_SOURCE_CONTEXT_COLUMNS if c in out.columns] == [
        "Record_ID",
        "Record_ID_Data_Quality",
        "CSSM",
        "Scope_Type",
        "Scope_Value",
        "Source_System",
        "Attributed_Team_Members",
    ]
    assert out.loc[0, "Record_ID"] == f"{sheet_name}-canonical-1"
    assert out.loc[0, "Record_ID_Data_Quality"] == "OK"
    assert out.loc[0, source_id_column] == f"{sheet_name}-source-1"
    for internal in (
        "ETL_ID",
        "IS_DELETED",
        "RECORD_TYPE_ID",
        "EDWSF_FUTURE_FIELD",
        "_ATTRIBUTED_BY_ACCOUNT",
    ):
        assert internal not in out.columns


# Round 142: lifecycle rows power the AP chart and must remain reconstructable
# in the standalone Source Data workbook, including explicit quality flags.
def test_action_plan_projection_keeps_lifecycle_and_data_quality_fields() -> None:
    lifecycle_fields = {
        "AdoptIQ_Record_ID": "AP-001",
        "AdoptIQ_Title": "Title unavailable",
        "AdoptIQ_Status_Bucket": "Overdue",
        "AdoptIQ_Due_Date": "2026-08-01",
        "AdoptIQ_Age_Days": 42,
        "AdoptIQ_Due_Days": -2,
        "AdoptIQ_Data_Quality": "Missing title",
    }
    df = pd.DataFrame(
        [
            {
                "Record_ID": "AP-001",
                "ID": "00T-source-001",
                "CSSM": "Alex CSSM",
                "Scope_Type": "Manager / technology / lookback",
                "Scope_Value": "Manager A | All | 90 days",
                "Source_System": "CSConsole",
                "Attributed_Team_Members": "Alex CSSM",
                **lifecycle_fields,
                "DELETE_FLAG": "N",
                "CREATEDDATE": "2026-01-01T00:00:00Z",
            }
        ]
    )

    out = schema.apply_export_schema(df, "Action_Plans")

    for field, expected in lifecycle_fields.items():
        assert field in out.columns
        assert out.loc[0, field] == expected
    assert "DELETE_FLAG" not in out.columns
    assert "CREATEDDATE" not in out.columns


# Round 142: pulse records retain the actual rating/score/date evidence used
# by report summaries, not just the customer label.
def test_customer_pulse_projection_keeps_metric_evidence() -> None:
    df = pd.DataFrame(
        [
            {
                "Record_ID": "CP-001",
                "ID": "pulse-source-001",
                "CUSTOMER_PULSE__C": "Red",
                "PULSE_RATING__C": "Poor",
                "SCORE__C": 1,
                "PULSE_DATE_C": "2026-07-31",
                "COMMENTS__C": "Follow up",
                "CREATEDDATE": "2026-07-31T12:00:00Z",
            }
        ]
    )

    out = schema.apply_export_schema(df, "Customer_Pulse")

    assert out.loc[0, "Record_ID"] == "CP-001"
    assert out.loc[0, "Customer Pulse"] == "Red"
    assert out.loc[0, "Pulse Rating"] == "Poor"
    assert out.loc[0, "Pulse Score"] == 1
    assert out.loc[0, "Pulse Date"] == "2026-07-31"
    assert "CREATEDDATE" not in out.columns


# Round 142: BEMS rows are a focused CSOne subset.  Escalation and case IDs
# remain available for deep dive, while unrelated case/warehouse fields drop.
def test_bems_projection_is_focused_and_traceable() -> None:
    df = pd.DataFrame(
        [
            {
                "Record_ID": "TAC-001",
                "ACCOUNT_ID_C": "ACC-001",
                "SR Number": "SR-001",
                "Transaction ID": "BEMS01999999",
                "bemscsc_refs": "BEMS01999999",
                "Title": "Escalated calling defect",
                "Case Status": "Open",
                "Date/Time Opened": "2026-07-01",
                "Service Tier": "Premium",
                "EDWSF_BATCH_ID": "internal",
            }
        ]
    )

    out = schema.apply_export_schema(df, "BEMS_Escalations")

    assert out.loc[0, "Record_ID"] == "TAC-001"
    assert out.loc[0, "Account ID"] == "ACC-001"
    assert out.loc[0, "SR Number"] == "SR-001"
    assert out.loc[0, "Transaction ID"] == "BEMS01999999"
    assert "Service Tier" not in out.columns
    assert "EDWSF_BATCH_ID" not in out.columns


def test_tac_projection_retains_stable_account_association() -> None:
    """Round 143: exported TAC rows remain auditable to an account ID."""

    out = schema.apply_export_schema(
        pd.DataFrame(
            [
                {
                    "Record_ID": "TAC-002",
                    "ACCOUNT_ID_C": "ACC-002",
                    "Customer": "Example Customer",
                    "SR Number": "SR-002",
                }
            ]
        ),
        "TAC_Cases",
    )

    assert out.loc[0, "Record_ID"] == "TAC-002"
    assert out.loc[0, "Account ID"] == "ACC-002"
