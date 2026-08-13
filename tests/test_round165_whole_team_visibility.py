"""Round 165 regression: a Leader Team brief renders the complete roster."""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd

import decision_report_delivery as delivery


_AS_OF = "2026-08-12T12:00:00Z"
_MEMBER_HEADERS = [
    "Team member",
    "Customers",
    "Open AP",
    "Overdue AP",
    "Barriers",
    "TAC",
]
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _team_fixture(member_count: int = 18) -> dict[str, dict[str, pd.DataFrame]]:
    team: dict[str, dict[str, pd.DataFrame]] = {}
    for number in range(1, member_count + 1):
        member = f"Roster Member {number:02d}"
        team[member] = {
            "subscriptions": pd.DataFrame(
                [
                    {
                        "SUBSCRIPTION_ID": f"SUB-{number:03d}",
                        "ACCOUNT_ID_C": f"ACC-{number:03d}",
                        "BU_NAME": f"Customer {number:02d}",
                        "CSSM_NAME": member,
                        "CSSM_EMAIL": f"member{number:02d}@example.test",
                    }
                ]
            ),
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
            "success_priorities": pd.DataFrame(),
        }
    return team


def _fake_chart_renderer(_chart_id: str, _rows: pd.DataFrame, target: Path) -> bool:
    target.write_bytes(_TINY_PNG)
    return True


def test_leader_word_and_contract_include_every_member_beyond_old_15_row_cap(
    monkeypatch,
) -> None:
    """All 18 roster members survive facts, Word, Source Data, and validation."""

    team_data = _team_fixture()
    expected_members = set(team_data)
    facts = delivery.build_report_facts(
        team_data,
        report_type="Leader",
        scope_type="team",
        scope_value="Large configured team",
        manager_name="Roster Manager",
        days=90,
        as_of=_AS_OF,
    )

    assert delivery.MEMBER_ITEM_LIMIT_DEFAULT >= len(expected_members)
    assert facts["member_summary_omitted"] == 0
    assert {row[0] for row in facts["member_summary"]} == expected_members
    assert facts["member_summary"] == facts["member_summary_all"]

    monkeypatch.setattr(delivery, "_render_chart_image", _fake_chart_renderer)
    document = delivery.build_concise_word_document(facts)
    sheets = delivery.build_source_data_sheets(facts)
    contract = delivery.validate_cross_artifact_contract(facts, sheets, document)

    member_table = next(
        table
        for table in document.tables
        if [cell.text for cell in table.rows[0].cells][: len(_MEMBER_HEADERS)]
        == _MEMBER_HEADERS
    )
    assert [cell.text for cell in member_table.rows[0].cells] == [
        *_MEMBER_HEADERS,
        "Manager intervention",
    ]
    rendered_members = {row.cells[0].text for row in member_table.rows[1:]}
    source_members = set(sheets["Member_Summary"]["Team_Member"].astype(str))
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert rendered_members == expected_members
    assert len(member_table.rows) - 1 == len(expected_members)
    assert source_members == expected_members
    assert "additional team-member row(s)" not in paragraph_text
    assert contract["ok"], contract["errors"]
    assert contract["word"]["word_count"] <= delivery.WORD_BUDGET_DEFAULT
