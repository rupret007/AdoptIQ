"""Round 162.3: every applicable source must affect decision detail.

The cross-source layer is deliberately deterministic.  Context-only sources
such as Success Priorities and external bugs must produce visible, traceable
signals and actions without silently changing the validated numeric risk
formula.  An unavailable source must produce a no-conclusion state, never a
zero claim.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd

import decision_report_delivery as delivery


AS_OF = pd.Timestamp("2026-08-11T12:00:00Z")
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _source_only_facts() -> dict:
    empty = pd.DataFrame()
    team_data = {
        "Portfolio": {
            "subscriptions": empty.copy(),
            "action_plans": empty.copy(),
            "adoption_barriers": empty.copy(),
            "customer_pulse": empty.copy(),
            "tac_cases": empty.copy(),
            "success_priorities": pd.DataFrame(
                [
                    {
                        "ID": "SP-ONLY-1",
                        "ACCOUNT_ID_C": "ACC-SP-1",
                        "RELATED_CUSTOMER__C": "Priority Only Customer",
                        "SUCCESS_PRIORITY_TITLE__C": "Launch executive adoption workshop",
                        "STATUS_C": "Active",
                        "CREATED_DATE_C": "2026-08-04",
                    }
                ]
            ),
        }
    }
    return delivery.build_report_facts(
        team_data,
        report_type="Comprehensive",
        scope_type="customer",
        scope_value="Priority Only Customer",
        manager_name="Dana Manager",
        technology="Webex Calling",
        days=90,
        as_of=AS_OF,
        external_incidents=[],
        external_bugs=[
            {
                "bug_id": "BUG-ONLY-1",
                "title": "Known calling policy display issue",
                "status": "Open",
                "discovered_at": "2026-08-07T09:00:00Z",
            }
        ],
    )


def _table_rows(doc, header: tuple[str, ...]) -> list[list[str]]:
    for table in doc.tables:
        actual = tuple(cell.text.strip() for cell in table.rows[0].cells)
        if actual == header:
            return [
                [cell.text.strip() for cell in row.cells]
                for row in table.rows[1:]
            ]
    raise AssertionError(f"missing Word table {header!r}")


def test_context_only_sources_create_visible_actions_and_exact_evidence(monkeypatch) -> None:
    facts = _source_only_facts()

    assert facts["kpis"]["customers"] == 1
    assert facts["kpis"]["success_priorities"] == 1
    assert facts["kpis"]["external_bugs"] == 1

    signals = {row["source_sheet"]: row for row in facts["decision_signals"]}
    assert set(signals) == {
        "Subscriptions",
        "Action_Plans",
        "Adoption_Barriers",
        "Customer_Pulse",
        "TAC_Cases",
        "Success_Priorities",
        "External_Incidents",
        "External_Bugs",
    }
    priority_signal = signals["Success_Priorities"]
    assert priority_signal["account"] == "Priority Only Customer"
    assert "SP-ONLY-1" in priority_signal["signal"]
    assert "Launch executive adoption workshop" in priority_signal["signal"]
    assert "success-plan action" in priority_signal["decision_implication"]
    bug_signal = signals["External_Bugs"]
    assert "BUG-ONLY-1" in bug_signal["signal"]
    assert "Known calling policy display issue" in bug_signal["signal"]
    assert "do not assume impact" in bug_signal["decision_implication"]

    # Success Priorities create identity/action context, but do not invent a
    # new risk component or score weight.
    profile = facts["risk_profiles"]["Priority Only Customer"]
    assert "success_priorities" not in set(profile.get("components") or {})

    def fake_chart(_chart_id: str, _rows: pd.DataFrame, target: Path) -> bool:
        target.write_bytes(_TINY_PNG)
        return True

    monkeypatch.setattr(delivery, "_render_chart_image", fake_chart)
    doc = delivery.build_concise_word_document(facts)
    rows = _table_rows(
        doc,
        (
            "Source",
            "State",
            "Scope / signal",
            "Decision implication",
        ),
    )
    serialized_rows = "\n".join(" | ".join(row) for row in rows)
    assert "Launch executive adoption workshop" in serialized_rows
    assert "Known calling policy display issue" in serialized_rows

    sheets = delivery.build_source_data_sheets(facts)
    links = sheets["Evidence_Links"]
    for source_sheet, record_id in (
        ("Success_Priorities", "SP-ONLY-1"),
        ("External_Bugs", "BUG-ONLY-1"),
    ):
        signal = signals[source_sheet]
        linked = links.loc[links["Evidence_Key"].eq(signal["evidence_key"])]
        assert len(linked) == 1
        assert linked.iloc[0]["Source_Sheet"] == source_sheet
        assert linked.iloc[0]["Record_ID"] == record_id
    contract = delivery.validate_cross_artifact_contract(facts, sheets, doc)
    assert contract["ok"], contract["errors"]


def test_unavailable_source_is_an_explicit_no_conclusion_not_zero() -> None:
    facts = _source_only_facts()
    unavailable = pd.DataFrame()
    unavailable.attrs.update(
        {
            "source_unavailable": True,
            "source_unavailable_detail": "criteria-scoped source could not be retrieved",
        }
    )
    facts = delivery.build_report_facts(
        {
            "Portfolio": {
                "subscriptions": pd.DataFrame(),
                "action_plans": pd.DataFrame(),
                "adoption_barriers": pd.DataFrame(),
                "customer_pulse": pd.DataFrame(),
                "tac_cases": pd.DataFrame(),
                "success_priorities": unavailable,
            }
        },
        report_type="Leader",
        scope_type="team",
        scope_value="Dana Manager team",
        manager_name="Dana Manager",
        days=90,
        as_of=AS_OF,
        external_incidents=[],
        external_bugs=[],
    )

    signal = next(
        row
        for row in facts["decision_signals"]
        if row["source_sheet"] == "Success_Priorities"
    )
    assert signal["source_state"] == "unavailable"
    assert "No complete source conclusion" in signal["signal"]
    assert "before relying on it" in signal["decision_implication"]
    coverage = facts["source_coverage"].set_index("Source_Sheet")
    assert coverage.loc["Success_Priorities", "Source_State"] == "unavailable"
    assert pd.isna(coverage.loc["Success_Priorities", "Record_Count"])

    zero_signal = next(
        row
        for row in facts["decision_signals"]
        if row["source_sheet"] == "External_Incidents"
    )
    assert zero_signal["source_state"] == "zero"
    assert zero_signal["signal"] == "No scoped records returned."

    sheets = delivery.build_source_data_sheets(facts)
    links = sheets["Evidence_Links"]
    unavailable_link = links.loc[links["Evidence_Key"].eq(signal["evidence_key"])]
    assert len(unavailable_link) == 1
    assert unavailable_link.iloc[0]["Source_State"] == "unavailable"
    assert unavailable_link.iloc[0]["Evidence_Role"] == "unavailable_state"
    assert pd.isna(unavailable_link.iloc[0]["Source_Row_Number"])
    zero_link = links.loc[links["Evidence_Key"].eq(zero_signal["evidence_key"])]
    assert len(zero_link) == 1
    assert zero_link.iloc[0]["Source_State"] == "zero"
    assert zero_link.iloc[0]["Evidence_Role"] == "zero_state"
    assert pd.isna(zero_link.iloc[0]["Source_Row_Number"])
    evidence_contract = delivery.validate_evidence_links(sheets)
    assert evidence_contract["ok"], evidence_contract["errors"]
