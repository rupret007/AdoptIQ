"""Round 169 public source-sheet evidence-schema restoration."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

import decision_report_delivery as delivery
import report_export_schema as export_schema


_ROUTE_ALIAS_EXCLUSIONS = {
    "Adoption_Barriers": {
        "Customer Name_2",
        "description_2",
        "open_date",
        "closed_date",
        "Open Age (Days)",
    },
    "Customer_Pulse": {"Account ID_2"},
    "TAC_Cases": set(),
    "BEMS": set(),
}


@pytest.mark.parametrize("sheet_name", tuple(_ROUTE_ALIAS_EXCLUSIONS))
def test_fixed_source_schema_is_complete_curated_semantic_projection(
    sheet_name: str,
) -> None:
    """Keep every curated field except explicit duplicate/derived aliases."""

    curated = export_schema.CURATED_COLUMNS[sheet_name]
    friendly = export_schema.apply_export_schema(
        pd.DataFrame([{column: column for column in curated}]),
        sheet_name,
    )
    exclusions = _ROUTE_ALIAS_EXCLUSIONS[sheet_name]
    expected = tuple(column for column in friendly.columns if column not in exclusions)

    assert delivery._CANONICAL_SOURCE_EXPORT_COLUMNS[sheet_name] == expected  # noqa: SLF001


def test_adoption_barrier_restored_evidence_is_exact_and_type_stable() -> None:
    common = {
        "ID": "AB-1",
        "BU_NAME": "",
        "customer_name": "Acme Corporation",
        "DESCRIPTION_C": "",
        "description": "Customer-visible dependency evidence.",
        "ACTION_PLAN_TITLE_C": "Remove the dependency",
        "NEXT_ACTION_OWNER_C": "Owner One",
        "TAC_CASE_NUMBER_LINK_C": "SR-100",
        "COMMENTS_C": "Customer-visible comment",
        "FIXTURE_MEMBER": "must not ship",
        "_AdoptIQ_Internal": "must not ship",
    }
    typed = delivery._prepare_export_frame(  # noqa: SLF001
        pd.DataFrame(
            [
                {
                    **common,
                    "OPEN_DATE_C": pd.Timestamp("2026-08-01T00:00:00Z"),
                    "NEXT_ACTION_DUE_DATE_C": pd.Timestamp("2026-08-20"),
                    "AOV_C": Decimal("123456.789012345678"),
                    "PRODUCT_ARR_C": Decimal("50000.125"),
                    "AGE_C": Decimal("17"),
                    "COUNT_OF_LINKED_CASES_C": 2,
                    "AB_ESCALATE_C": True,
                }
            ]
        ),
        "Adoption_Barriers",
    )
    text = delivery._prepare_export_frame(  # noqa: SLF001
        pd.DataFrame(
            [
                {
                    **common,
                    "OPEN_DATE_C": "2026-08-01",
                    "NEXT_ACTION_DUE_DATE_C": "2026-08-20T00:00:00Z",
                    "AOV_C": "123456.789012345678",
                    "PRODUCT_ARR_C": "50000.125",
                    "AGE_C": "17.0",
                    "COUNT_OF_LINKED_CASES_C": "2.0",
                    "AB_ESCALATE_C": "yes",
                }
            ]
        ),
        "Adoption_Barriers",
    )

    pd.testing.assert_frame_equal(typed, text)
    row = typed.iloc[0]
    assert row["Customer Name"] == "Acme Corporation"
    assert row["Description"] == "Customer-visible dependency evidence."
    assert row["Annual Order Value"] == "123456.789012345678"
    assert row["Product ARR"] == "50000.125"
    assert row["Age (Days)"] == 17
    assert row["Linked Cases (Count)"] == 2
    assert bool(row["Escalated"]) is True
    assert row["Action Plan Title"] == "Remove the dependency"
    assert row["Next Action Owner"] == "Owner One"
    assert row["TAC Case Number"] == "SR-100"
    assert row["Comments"] == "Customer-visible comment"
    assert not {
        "Customer Name_2",
        "description_2",
        "open_date",
        "closed_date",
        "Open Age (Days)",
        "FIXTURE_MEMBER",
        "_AdoptIQ_Internal",
    } & set(typed.columns)


@pytest.mark.parametrize("sheet_name", ("TAC_Cases", "BEMS"))
def test_support_restored_evidence_is_retained_and_type_stable(
    sheet_name: str,
) -> None:
    common = {
        "Record_ID": "SR-1",
        "Source_Record_URL": "https://example.invalid/source/SR-1",
        "Customer": "Acme Corporation",
        "ACCOUNT_ID_C": "A1",
        "Subscription Reference Id": "SUB-REF-1",
        "SUBSCRIPTION_ID": "SUB-1",
        "SR Number": "SR-1",
        "Case Number": "CASE-1",
        "Transaction ID": "BEMS-100",
        "BEMS_ID": "BEMS-100",
        "BEMS_REF": "CSCaa123456",
        "Case Owner": "Case Owner One",
        "Current Contact Email": "contact@example.invalid",
        "Problem Description": "Problem description evidence",
        "Problem Details": "Problem detail evidence",
        "CSE Action Plan": "Validate the workaround",
        "Last Cisco Update": "Workaround validated",
        "Resolution Summary": "Resolved with configuration change",
        "Customer Activity": "Customer confirmed recovery",
        "LOCAL_ACCEPTANCE_RECORD_ID": "must not ship",
        "_route_debug": "must not ship",
    }
    typed = delivery._prepare_export_frame(  # noqa: SLF001
        pd.DataFrame(
            [
                {
                    **common,
                    "Date/Time Opened": pd.Timestamp("2026-08-01T12:30:00Z"),
                    "is_open": True,
                    "is_closed": False,
                    "is_bems": True,
                    "open_age_days": Decimal("12"),
                    "closed_age_days": Decimal("0"),
                    "Highest Priority": True,
                    "# of Case Owner Changes": Decimal("3"),
                }
            ]
        ),
        sheet_name,
    )
    text = delivery._prepare_export_frame(  # noqa: SLF001
        pd.DataFrame(
            [
                {
                    **common,
                    "Date/Time Opened": "2026-08-01T12:30:00Z",
                    "is_open": "1",
                    "is_closed": "0",
                    "is_bems": "yes",
                    "open_age_days": "12.0",
                    "closed_age_days": "0",
                    "Highest Priority": "true",
                    "# of Case Owner Changes": "3.0",
                }
            ]
        ),
        sheet_name,
    )

    pd.testing.assert_frame_equal(typed, text)
    row = typed.iloc[0]
    assert row["Subscription Reference Id"] == "SUB-REF-1"
    assert row["Subscription ID"] == "SUB-1"
    assert row["Case Number"] == "CASE-1"
    assert row["Case Owner"] == "Case Owner One"
    assert row["Problem Details"] == "Problem detail evidence"
    assert row["CSE Action Plan"] == "Validate the workaround"
    assert row["Resolution Summary"] == "Resolved with configuration change"
    if sheet_name == "TAC_Cases":
        assert row["# of Case Owner Changes"] == 3
    else:
        assert "# of Case Owner Changes" not in typed.columns
    assert not {"LOCAL_ACCEPTANCE_RECORD_ID", "_route_debug"} & set(typed.columns)


def test_customer_pulse_restored_evidence_is_retained_and_type_stable() -> None:
    common = {
        "ID": "CP-1",
        "PULSE_ID": "PULSE-1",
        "NAME": "Pulse observation",
        "BU_NAME": "Acme Corporation",
        "ACCOUNT__C": "",
        "ACCOUNT_ID_C": "A1",
        "CUSTOMER_PULSE__C": "Red",
        "CUSTOMER_PULSE_COLOR_IMAGE__C": "Red",
        "PRODUCT__C": "Webex Calling",
        "COMMENTS__C": "Recovery plan requested",
        "OWNERID": "owner-1",
        "RECORD_SOURCE": "Customer Pulse",
        "FIXTURE_MEMBER": "must not ship",
    }
    typed = delivery._prepare_export_frame(  # noqa: SLF001
        pd.DataFrame(
            [
                {
                    **common,
                    "SCORE__C": Decimal("1.5"),
                    "PULSE_DATE_C": pd.Timestamp("2026-08-01"),
                    "AS_OF_DATE": pd.Timestamp("2026-08-02T06:30:00Z"),
                }
            ]
        ),
        "Customer_Pulse",
    )
    text = delivery._prepare_export_frame(  # noqa: SLF001
        pd.DataFrame(
            [
                {
                    **common,
                    "SCORE__C": "1.50",
                    "PULSE_DATE_C": "2026-08-01T00:00:00Z",
                    "AS_OF_DATE": "2026-08-02T06:30:00Z",
                }
            ]
        ),
        "Customer_Pulse",
    )

    pd.testing.assert_frame_equal(typed, text)
    row = typed.iloc[0]
    assert row["PULSE_ID"] == "PULSE-1"
    assert row["Account ID"] == "A1"
    assert row["Customer Pulse Color"] == "Red"
    assert row["Pulse Score"] == "1.5"
    assert row["As Of Date"] == "2026-08-02T06:30:00Z"
    assert row["Product"] == "Webex Calling"
    assert row["Owner"] == "owner-1"
    assert row["RECORD_SOURCE"] == "Customer Pulse"
    assert "Account ID_2" not in typed.columns
    assert "FIXTURE_MEMBER" not in typed.columns
