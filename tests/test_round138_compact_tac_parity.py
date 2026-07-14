"""Round 138 regression pins for Compact Word/XLSX TAC parity."""

from pathlib import Path

import pandas as pd
import openpyxl

import canonical_metrics as cm
import wxcc_health_input_exporter as wxcc
from executive_intelligence_formatter import _r118_dedup_tac_cases
from report_iteration_loop import (
    _extract_team_summary_sheet,
    compare_kpi_parity,
    extract_xlsx_kpis,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_compact_excel_writer_reuses_word_tac_dedup_universe():
    source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")

    assert "_r138_compact_excel_csone_df = _r138_dedup_tac_cases(csone_df)" in source
    assert '"All_Support_Cases": _r138_compact_excel_csone_df' in source
    assert "csone_df=_r138_compact_excel_csone_df" in source
    assert "cm.count_escalated(_r138_compact_excel_csone_df)" in source
    assert '"All_Support_Cases": csone_df' not in source


def test_deduped_compact_detail_sheet_passes_support_and_bems_parity(tmp_path):
    raw = pd.DataFrame(
        {
            "Case #": ["700840277", "700840277", "700822701"],
            "customer_name": ["WINTRUST", "WINTRUST", "OTHER"],
            "Severity": ["P2", "P2", "P3"],
            "Transaction ID": ["BEMS01943186", "BEMS01943186", ""],
        }
    )
    deduped = _r118_dedup_tac_cases(raw)

    assert cm.count_total_tac(deduped) == 2
    assert cm.count_bems(deduped) == 1

    workbook_path = tmp_path / "compact.xlsx"
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        deduped.to_excel(writer, sheet_name="All_Support_Cases", index=False)

    xlsx_kpis = extract_xlsx_kpis(workbook_path)
    docx_kpis = {
        "values": {
            "support_cases": str(cm.count_total_tac(deduped)),
            "bems": str(cm.count_bems(deduped)),
        }
    }
    parity = compare_kpi_parity(docx_kpis, xlsx_kpis, strict=True)

    assert xlsx_kpis["values"]["support_cases"] == "2"
    assert xlsx_kpis["values"]["bems"] == "1"
    assert parity.passed is True
    assert parity.details["mismatches"] == {}


def test_leader_team_summary_uses_deduped_total_without_counting_it_as_member(tmp_path):
    workbook_path = tmp_path / "leader.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Team_Summary"
    sheet.append(
        [
            "Team_Member",
            "Num_Customers",
            "Num_Action_Plans",
            "Num_Adoption_Barriers",
            "Num_Customer_Pulse",
            "Num_TAC_Cases",
        ]
    )
    sheet.append(["One", 5, 130, 20, 7, 200])
    sheet.append(["Two", 4, 135, 18, 6, 229])
    sheet.append(["TOTAL (deduped)", 8, 251, 32, 12, 429])
    workbook.save(workbook_path)
    workbook.close()

    reopened = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    values: dict[str, str] = {}
    _extract_team_summary_sheet(reopened["Team_Summary"], values)
    reopened.close()

    assert values["team_members"] == "2"
    assert values["total_customers"] == "9"
    assert values["action_plans"] == "251"
    assert values["adoption_barriers"] == "32"
    assert values["customer_pulse"] == "12"
    assert values["support_cases"] == "429"


def test_wxcc_csone_scope_threads_required_subscription_and_customer_roster(monkeypatch):
    team_subs = pd.DataFrame(
        {
            "SUBSCRIPTION_ID": ["SUB-001"],
            "BU_NAME": ["WINTRUST"],
            "CSSM_EMAIL": ["owner@example.test"],
            "ACCOUNT_ID_C": ["A-001"],
        }
    )
    scope = wxcc.CustomerScope(
        canonical_name="WINTRUST",
        customer_query="wintrust",
        subscription_id="SUB-001",
        days=90,
        team_subs_df=team_subs,
        account_ids=["A-001"],
    )
    csone = pd.DataFrame(
        {
            "Subscription ID": ["SUB-001"],
            "customer_name": ["WINTRUST"],
            "Case #": ["700840277"],
        }
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(wxcc, "_connect_with_keeper", lambda: object())
    monkeypatch.setattr(wxcc, "prefetch_ask_ai_grounded", lambda _ctx: {})
    monkeypatch.setattr(wxcc, "fetch_adoption_barriers", lambda *_args: pd.DataFrame())
    monkeypatch.setattr(wxcc, "_apply_scope_filter_ab", lambda frame, *_args: frame)
    monkeypatch.setattr(wxcc, "load_csone_excel", lambda _path: csone.copy())
    monkeypatch.setattr(wxcc, "_prepare_csone", lambda frame, _subs: frame)
    monkeypatch.setattr(wxcc, "fetch_status_incidents", lambda **_kwargs: [])
    monkeypatch.setattr(wxcc, "fetch_help_webex_bugs", lambda: [])
    monkeypatch.setattr(wxcc, "add_case_lifecycle_fields", lambda frame: frame)
    monkeypatch.setattr(wxcc, "compute_customer_risk_profile", lambda **_kwargs: {})

    def _capture_scope(frame, tech, days, sub_ids, customer_names):
        captured.update(
            {
                "tech": tech,
                "days": days,
                "sub_ids": sub_ids,
                "customer_names": customer_names,
            }
        )
        return frame

    monkeypatch.setattr(wxcc, "_apply_scope_filter_csone", _capture_scope)

    result = wxcc.fetch_customer_datasets(
        scope,
        "Webex Contact Center",
        csone_path="/tmp/csone.xlsx",
        csone_sync_status="synced",
    )

    assert captured == {
        "tech": "Webex Contact Center",
        "days": 90,
        "sub_ids": ["SUB-001"],
        "customer_names": ["WINTRUST"],
    }
    assert result.csone_df["Case #"].tolist() == ["700840277"]
    assert not any(warning["kind"] == "csone_load_failed" for warning in result.partial_data_warnings)
