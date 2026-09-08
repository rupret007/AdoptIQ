"""Round 169 / P3: identical offline inputs yield identical sheet row counts."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import decision_report_delivery as delivery
from tests.test_round142_decision_report_delivery import AS_OF, _team_fixture


def _comprehensive_sheet_counts(facts: dict) -> dict[str, int]:
    sheets = delivery.build_source_data_sheets(facts)
    return {name: int(len(frame)) for name, frame in sheets.items()}


def test_comprehensive_acc_scope_is_deterministic_for_identical_inputs() -> None:
    fixture = _team_fixture()
    kwargs = {
        "team_data": fixture,
        "report_type": "Comprehensive",
        "scope_type": "team",
        "scope_value": "Dana Manager team",
        "manager_name": "Dana Manager",
        "technology": "All Contact Center",
        "days": 90,
        "as_of": AS_OF,
        "data_as_of_utc": AS_OF,
        "data_as_of_state": "available",
    }

    first = delivery.build_report_facts(**kwargs)
    second = delivery.build_report_facts(**kwargs)

    first_counts = _comprehensive_sheet_counts(first)
    second_counts = _comprehensive_sheet_counts(second)

    assert first_counts == second_counts
    assert first_counts["Action_Plans"] == second_counts["Action_Plans"]
    assert first_counts["Chart_Data"] == second_counts["Chart_Data"]


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("matplotlib") is None,
    reason="offline acceptance harness imports chart rendering",
)
def test_offline_acceptance_repeat_pass_matches_sheet_row_counts(tmp_path: Path) -> None:
    from scripts.generate_offline_acceptance_artifacts import DEFAULT_FIXTURE_PATH

    if not DEFAULT_FIXTURE_PATH.exists():
        pytest.skip("sanitized local acceptance fixture is not present")

    common = {
        "scope": "team",
        "as_of": AS_OF,
        "manager_name": "Local Fixture Manager",
        "days": 90,
        "fixture_path": DEFAULT_FIXTURE_PATH,
    }
    from scripts import generate_offline_acceptance_artifacts as harness

    first = harness.generate_acceptance_artifacts(
        **common, output_dir=tmp_path / "pass-1"
    )
    second = harness.generate_acceptance_artifacts(
        **common, output_dir=tmp_path / "pass-2"
    )

    with pd.ExcelFile(first["source_data_path"]) as first_workbook, pd.ExcelFile(
        second["source_data_path"]
    ) as second_workbook:
        assert first_workbook.sheet_names == second_workbook.sheet_names
        for sheet_name in first_workbook.sheet_names:
            first_rows = pd.read_excel(first_workbook, sheet_name=sheet_name)
            second_rows = pd.read_excel(second_workbook, sheet_name=sheet_name)
            assert len(first_rows) == len(second_rows), sheet_name
