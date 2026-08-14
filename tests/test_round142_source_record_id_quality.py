"""Round 142 public record-ID data-quality and publication contract.

Missing source identifiers are evidence-quality defects, not permission to
drop the underlying activity.  The standalone Source Data workbook therefore
keeps every such row and exposes an explicit public quality flag, while the
publication validator fails closed until supported CSConsole records have IDs.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import decision_report_delivery as delivery


AS_OF = "2026-08-03T12:00:00Z"
ACTIVITY_SHEETS = (
    "Action_Plans",
    "Adoption_Barriers",
    "Customer_Pulse",
    "TAC_Cases",
)


def _facts_with_one_missing_id_per_activity_source() -> dict:
    team_data = {
        "Alex Rivera": {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "SUBSCRIPTION_ID": "SUB-001",
                        "ACCOUNT_ID_C": "ACC-001",
                        "BU_NAME": "Acme Corporation",
                    }
                ]
            ),
            "action_plans": pd.DataFrame(
                [
                    {
                        "ID": "AP-001",
                        "BU_NAME": "Acme Corporation",
                        "SUBJECT_C": "Stable-ID action plan",
                        "STATUS_C": "Open",
                        "CREATED_DATE_C": "2026-07-01",
                        "DUE_DATE_C": "2026-08-10",
                    },
                    {
                        "ID": None,
                        "BU_NAME": "Acme Corporation",
                        "SUBJECT_C": "Missing-ID action plan",
                        "STATUS_C": "On Hold",
                        "CREATED_DATE_C": "2026-07-02",
                        "DUE_DATE_C": "2026-08-11",
                    },
                ]
            ),
            "adoption_barriers": pd.DataFrame(
                [
                    {
                        "ID": "AB-001",
                        "BU_NAME": "Acme Corporation",
                        "SUBJECT_C": "Stable-ID barrier",
                        "STATUS_C": "Open",
                        "SEVERITY_C": "High",
                        "OPEN_DATE_C": "2026-07-03",
                    },
                    {
                        "ID": "",
                        "BU_NAME": "Acme Corporation",
                        "SUBJECT_C": "Missing-ID barrier",
                        "STATUS_C": "Open",
                        "SEVERITY_C": "Medium",
                        "OPEN_DATE_C": "2026-07-04",
                    },
                ]
            ),
            "customer_pulse": pd.DataFrame(
                [
                    {
                        "ID": "CP-001",
                        "BU_NAME": "Acme Corporation",
                        "PULSE_RATING__C": "Good",
                        "COMMENTS__C": "Stable-ID pulse",
                        "PULSE_DATE_C": "2026-07-05",
                    },
                    {
                        "ID": pd.NA,
                        "BU_NAME": "Acme Corporation",
                        "PULSE_RATING__C": "Poor",
                        "COMMENTS__C": "Missing-ID pulse",
                        "PULSE_DATE_C": "2026-07-06",
                    },
                ]
            ),
            "tac_cases": pd.DataFrame(
                [
                    {
                        "SR Number": "700000001",
                        "Customer": "Acme Corporation",
                        "Title": "Stable-ID TAC case",
                        "Severity": "2",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-07",
                    },
                    {
                        "SR Number": None,
                        "Customer": "Acme Corporation",
                        "Title": "Missing-ID TAC case",
                        "Severity": "3",
                        "Case Status": "Open",
                        "Date/Time Opened": "2026-07-08",
                    },
                ]
            ),
            "success_priorities": pd.DataFrame(),
        }
    }
    return delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="member",
        scope_value="Alex Rivera",
        manager_name="Dana Manager",
        days=90,
        as_of=AS_OF,
        external_incidents=[],
        external_bugs=[],
    )


def _assert_public_id_quality_contract(frame: pd.DataFrame, sheet_name: str) -> None:
    assert frame.columns[:2].tolist() == ["Record_ID", "Record_ID_Data_Quality"], sheet_name
    assert len(frame) == 2, sheet_name

    ids = frame["Record_ID"].fillna("").astype(str).str.strip()
    missing = ids.eq("")
    assert int(missing.sum()) == 1, sheet_name
    assert frame.loc[missing, "Record_ID_Data_Quality"].tolist() == [
        "Missing stable source ID"
    ], sheet_name
    assert frame.loc[~missing, "Record_ID_Data_Quality"].tolist() == ["OK"], sheet_name


def test_missing_record_ids_remain_visible_through_xlsx_round_trip(tmp_path: Path) -> None:
    facts = _facts_with_one_missing_id_per_activity_source()
    sheets = delivery.build_source_data_sheets(facts)

    for sheet_name in ACTIVITY_SHEETS:
        prepared = delivery._prepare_export_frame(  # noqa: SLF001 - public XLSX projection contract
            sheets[sheet_name],
            sheet_name,
        )
        _assert_public_id_quality_contract(prepared, sheet_name)

    source_path = tmp_path / "AdoptIQ_Source_Data_missing_record_ids.xlsx"
    delivery.write_source_data_workbook(source_path, sheets)

    written_contract = delivery.validate_written_source_workbook(source_path, facts)
    assert written_contract["ok"] is False
    assert written_contract["errors"] == [
        "Source Data has 3 CSConsole link coverage error(s) (missing_stable_id=3)",
        "Evidence_Links has 16 CSConsole link coverage error(s) (missing_stable_id=16)",
    ]
    completeness = written_contract["completeness"]
    assert completeness["source_record_link_errors"] == 3
    assert completeness["source_record_link_missing_id_rows"] == 3
    assert completeness["source_record_link_invalid_id_rows"] == 0
    assert completeness["source_record_link_missing_url_rows"] == 0
    assert completeness["source_record_link_error_reasons"] == {
        "Action_Plans": {"missing_stable_id": 1},
        "Adoption_Barriers": {"missing_stable_id": 1},
        "Customer_Pulse": {"missing_stable_id": 1},
    }
    assert completeness["evidence_link_errors"] == 16
    assert completeness["evidence_link_error_reasons"] == {
        "Action_Plans": {"missing_stable_id": 8},
        "Adoption_Barriers": {"missing_stable_id": 4},
        "Customer_Pulse": {"missing_stable_id": 4},
    }
    for sheet_name in ACTIVITY_SHEETS:
        reopened = pd.read_excel(source_path, sheet_name=sheet_name, dtype=object)
        _assert_public_id_quality_contract(reopened, sheet_name)
