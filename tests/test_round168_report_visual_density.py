from __future__ import annotations

import copy

import decision_report_delivery as delivery
from tests.test_round142_decision_report_delivery import AS_OF, _team_fixture


def _facts(report_type: str) -> dict:
    return delivery.build_report_facts(
        _team_fixture(),
        report_type=report_type,
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=AS_OF,
    )


def test_dense_table_count_copy_is_compact_without_losing_state() -> None:
    display = delivery._coverage_aware_compact_display  # noqa: SLF001

    assert display(4, "available") == 4
    assert display(4, "partial") == "At least 4"
    assert display(4, "stale") == "4 (stale)"
    assert display(4, "unavailable") == "Unavailable"
    assert display(0, "partial") == "At least 0"


def test_partial_composite_score_is_not_misrepresented_as_a_lower_bound() -> None:
    display = delivery._coverage_aware_scalar_display  # noqa: SLF001

    assert display(54.2, "available") == 54.2
    assert display(54.2, "partial") == "54.2 (partial inputs)"
    assert display(54.2, "stale") == "54.2 (stale inputs)"
    assert display("", "partial") == "Unavailable (Partial)"
    assert "At least" not in str(display(54.2, "partial"))


def test_leader_blank_technology_scope_is_explicitly_all() -> None:
    subtitle = delivery._word_scope_subtitle(  # noqa: SLF001
        {
            "manager_name": "Dana Manager",
            "scope_type": "team",
            "scope_value": "Dana Manager team",
            "report_type": "Leader",
            "technology": "",
            "days": 90,
        }
    )

    assert "Technology: All" in subtitle
    assert "Not specified" not in subtitle


def test_source_data_blank_technology_scope_is_explicitly_all() -> None:
    sheets = delivery.build_source_data_sheets(
        _facts("Leader"),
        _skip_contract_fingerprint=True,
    )
    report_info = sheets["Report_Info"]

    technology = report_info.loc[report_info["Item"] == "Technology", "Value"]
    assert technology.tolist() == ["All"]


def test_leader_and_comprehensive_dense_rows_do_not_repeat_long_lower_bound_copy() -> None:
    leader = copy.deepcopy(_facts("Leader"))
    leader["member_summary"] = [list(row) for row in leader["member_summary"]]
    for row in leader["member_summary"]:
        row[6] = "partial"
        row[7] = "partial"
        row[9] = "partial"
        row[10] = "partial"
    leader_rows = delivery._leader_intervention_rows(leader)  # noqa: SLF001

    comprehensive = copy.deepcopy(_facts("Comprehensive"))
    comprehensive["account_summary"] = [
        list(row) for row in comprehensive["account_summary"]
    ]
    for row in comprehensive["account_summary"]:
        row[9] = "partial"
        row[11] = "partial"
        row[12] = "partial"
    comprehensive_rows = delivery._comprehensive_account_evidence_rows(  # noqa: SLF001
        comprehensive
    )

    assert leader_rows and comprehensive_rows
    for row in leader_rows:
        assert all(str(value).startswith("At least ") for value in row[1:6])
    for row in comprehensive_rows:
        assert all(str(value).startswith("At least ") for value in row[2:6])
    assert "Known retained:" not in repr(leader_rows + comprehensive_rows)
