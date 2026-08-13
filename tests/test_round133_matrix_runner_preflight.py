"""Round 133 matrix runner preflight tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts import run_report_option_matrix as matrix_runner


def test_round133_matrix_runner_blocks_when_connectivity_preflight_fails():
    with (
        patch.object(matrix_runner, "_probe_running_reports", return_value=[]),
        patch.object(
            matrix_runner,
            "_probe_connectivity",
            return_value={"ok": False, "error_kind": "snowflake_credentials_missing"},
        ),
    ):
        code = matrix_runner.main(["--blocks", "A"])
    assert code == 5


def test_round133_matrix_runner_connectivity_probe_parses_json():
    class _Resp:
        def read(self):
            return json.dumps({"ok": True, "stages": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Resp()):
        payload = matrix_runner._probe_connectivity("http://127.0.0.1:5151")
    assert payload.get("ok") is True


def _matrix_source_workbook(
    path: Path,
    *,
    tac_ids: list[str],
    tac_attribution: str = "Alex Rivera",
    data_as_of_utc: str = "2026-08-03T21:00:00Z",
) -> None:
    info = {
        "Manager": "Fixture Manager",
        "Technology": "All",
        "Scope_Type": "team",
        "Scope_Value": "Fixture Manager team",
        "Days": 90,
        "Data_As_Of_UTC": data_as_of_utc,
        "Data_As_Of_State": "available",
        "Evaluation_As_Of_UTC": "2026-08-03T21:00:00Z",
    }
    for sheet_name in matrix_runner._CROSS_REPORT_SOURCE_SHEETS:
        info[f"Source_State:{sheet_name}"] = "available"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(
            [{"Item": key, "Value": value} for key, value in info.items()]
        ).to_excel(writer, sheet_name="Report_Info", index=False)
        for sheet_name in matrix_runner._CROSS_REPORT_SOURCE_SHEETS:
            ids = tac_ids if sheet_name == "TAC_Cases" else [f"{sheet_name}-1"]
            attribution = (
                [tac_attribution] * len(ids)
                if sheet_name == "TAC_Cases"
                else ["Alex Rivera"] * len(ids)
            )
            pd.DataFrame(
                {
                    "Record_ID": ids,
                    "Attributed_Team_Members": attribution,
                }
            ).to_excel(
                writer,
                sheet_name=sheet_name,
                index=False,
            )


def test_cross_report_source_gate_detects_route_specific_tac_population(
    tmp_path: Path,
) -> None:
    first = tmp_path / "compact.xlsx"
    second = tmp_path / "comprehensive.xlsx"
    _matrix_source_workbook(first, tac_ids=["TAC-1", "TAC-2"])
    _matrix_source_workbook(second, tac_ids=["TAC-1", "TAC-3"])
    summary = {
        "results": [
            {
                "scenario": "compact",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(first)}],
            },
            {
                "scenario": "comprehensive",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(second)}],
            },
        ]
    }

    audit = matrix_runner._cross_report_source_consistency(summary)

    assert audit["ok"] is False
    assert audit["groups_evaluated"] == 1
    assert [item["source_sheet"] for item in audit["mismatches"]] == [
        "TAC_Cases"
    ]
    assert audit["privacy"] == "counts_and_sha256_only"


def test_cross_report_source_gate_detects_attribution_drift_with_same_ids(
    tmp_path: Path,
) -> None:
    first = tmp_path / "leader.xlsx"
    second = tmp_path / "comprehensive.xlsx"
    _matrix_source_workbook(
        first,
        tac_ids=["TAC-1"],
        tac_attribution="Alex Rivera; Morgan Lee",
    )
    _matrix_source_workbook(
        second,
        tac_ids=["TAC-1"],
        tac_attribution="Alex Rivera",
    )
    summary = {
        "results": [
            {
                "scenario": "leader",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(first)}],
            },
            {
                "scenario": "comprehensive",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(second)}],
            },
        ]
    }

    audit = matrix_runner._cross_report_source_consistency(summary)

    assert audit["ok"] is False
    assert [item["source_sheet"] for item in audit["mismatches"]] == [
        "TAC_Cases"
    ]
    observed = audit["mismatches"][0]["observed"]
    assert {value["count"] for value in observed.values()} == {1}
    assert len({value["attribution_sha256"] for value in observed.values()}) == 2


def test_cross_report_source_gate_detects_freshness_drift(
    tmp_path: Path,
) -> None:
    first = tmp_path / "leader.xlsx"
    second = tmp_path / "comprehensive.xlsx"
    _matrix_source_workbook(
        first,
        tac_ids=["TAC-1"],
        data_as_of_utc="2026-08-13T04:20:00Z",
    )
    _matrix_source_workbook(
        second,
        tac_ids=["TAC-1"],
        data_as_of_utc="2026-08-03T21:00:00Z",
    )
    summary = {
        "results": [
            {
                "scenario": "leader",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(first)}],
            },
            {
                "scenario": "comprehensive",
                "all_passed": True,
                "artifacts": [{"file_type": "xlsx", "debug_path": str(second)}],
            },
        ]
    }

    audit = matrix_runner._cross_report_source_consistency(summary)

    assert audit["ok"] is False
    assert audit["mismatches"] == []
    assert len(audit["freshness_mismatches"]) == 1
