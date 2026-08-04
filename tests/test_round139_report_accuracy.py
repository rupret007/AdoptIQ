"""Round 139 / Build 109 — AP scope, TAC collapse, health grades, Excel headers."""

import warnings

import pandas as pd

import canonical_metrics as cm
from report_consistency import validate_report_consistency
from adoptiq_backend import (
    _scope_action_plans_for_report,
    _r139_normalize_grade_token,
    stamp_customer_health_grade,
)
from data_normalization import collapse_tac_cases, resolve_tac_case_id
from report_export_schema import apply_export_schema, ensure_unique_excel_headers


def test_scope_action_plans_retains_rows_without_authoritative_tech():
    df = pd.DataFrame(
        [
            {
                "ID": "ap-1",
                "SUBJECT_C": "Training gap",
                "Account.Id": "001",
                "BU_NAME": "ACME",
            },
            {
                "ID": "ap-2",
                "SUBJECT_C": "Routing issue",
                "Account.Id": "002",
                "BU_NAME": "BETA",
            },
        ]
    )
    scoped = _scope_action_plans_for_report(
        df,
        "Webex Contact Center",
        customer_names=["ACME", "BETA"],
        account_ids=["001", "002"],
    )
    assert len(scoped) == 2


def test_scope_action_plans_excludes_explicit_non_matching_tech():
    df = pd.DataFrame(
        [
            {
                "ID": "ap-3",
                "SUBJECT_C": "Enterprise only",
                "Account.Id": "003",
                "BU_NAME": "GAMMA",
                "SUB_TECHNOLOGY_C": "Webex Contact Center Enterprise",
                "TECHNOLOGY_C": "Contact Center Software",
            }
        ]
    )
    scoped = _scope_action_plans_for_report(
        df,
        "Webex Contact Center",
        customer_names=["GAMMA"],
        account_ids=["003"],
    )
    assert scoped.empty
    assert scoped.attrs.get("tech_filter_empty_after_scope") is True


def test_tac_collapse_preserves_bems_union():
    raw = pd.DataFrame(
        {
            "Case #": ["700840277", "700840277", "700822701"],
            "customer_name": ["WINTRUST", "WINTRUST", "OTHER"],
            "Transaction ID": ["BEMS01943186", "BEMS01999999", ""],
            "Severity": ["P2", "P2", "P3"],
        }
    )
    collapsed = collapse_tac_cases(raw)
    assert len(collapsed) == 2
    assert cm.count_total_tac(collapsed) == 2
    assert cm.count_bems(collapsed) == 1
    wintrust = collapsed[collapsed["Case #"].astype(str) == "700840277"].iloc[0]
    assert "BEMS01943186" in str(wintrust["Transaction ID"])
    assert "BEMS01999999" in str(wintrust["Transaction ID"])


def test_tac_collapse_emits_no_groupby_apply_future_warning():
    raw = pd.DataFrame(
        [
            {"Case #": "700840277", "Transaction ID": "BEMS01943186"},
            {"Case #": "700840277", "Transaction ID": "BEMS01999999"},
        ]
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", FutureWarning)
        collapsed = collapse_tac_cases(raw)

    assert collapsed is not None
    assert len(collapsed) == 1
    assert not [item for item in captured if issubclass(item.category, FutureWarning)]


def test_resolve_tac_case_id_reads_sr_number():
    row = {"SR Number": "  SR-12345 ", "Case #": ""}
    assert resolve_tac_case_id(row) == "SR-12345"


def test_health_grade_band_words_normalize_to_letters():
    assert _r139_normalize_grade_token("LOW") == "B"
    assert _r139_normalize_grade_token("MODERATE") == "C"
    assert _r139_normalize_grade_token("CRITICAL") == "F"


def test_stamp_customer_health_grade_replaces_band_word():
    narrative = "Customer Health Score: LOW (watchlist)."
    stamped = stamp_customer_health_grade(narrative, "B")
    assert "Customer Health Score: B" in stamped
    assert "LOW" not in stamped.split("Customer Health Score:")[1][:8]


def test_ensure_unique_excel_headers_case_insensitive():
    headers = ensure_unique_excel_headers(["Risk", "risk", "RISK", "Customer"])
    assert headers == ["Risk", "risk_2", "RISK_3", "Customer"]


def test_apply_export_schema_uniques_duplicate_headers():
    df = pd.DataFrame({"A": [1], "a": [2], "B": [3]})
    out = apply_export_schema(df, sheet_name=None)
    lowered = [c.lower() for c in out.columns]
    assert len(lowered) == len(set(lowered))


def test_round139_leader_portfolio_uses_collapsed_tac_not_raw_rowcount():
    """R139: leader Team Performance Metrics must use collapsed TAC/BEMS."""
    import pathlib

    src = pathlib.Path("leader_report_generator.py").read_text(encoding="utf-8")
    assert "total_tac_cases = cm.count_total_tac(_r139_tac_union)" in src
    assert "total_bems_tac_only = cm.count_bems(_r139_tac_union)" in src
    assert "num_tac = cm.count_total_tac(data.get('tac_cases'" in src
    assert "('BEMS Escalations', str(total_bems_tac_only)" in src
    assert "_r139_total_bems_kpi = cm.count_bems(_r139_tac_union)" in src
    assert "totals_cells[5].text = str(_r139_total_bems_kpi)" in src


def test_round139_leader_tac_sheet_collapsed_before_xlsx_write():
    """R139: leader TAC_Cases XLSX must collapse cross-CSSM dup case ids."""
    import pathlib

    src = pathlib.Path("app_simple.py").read_text(encoding="utf-8")
    assert "Round 139 / Build 109: leader TAC_Cases sheet" in src
    assert "collapse_tac_cases as _r139_collapse_leader_tac" in src


def test_round139_comprehensive_csone_sheet_collapsed_before_xlsx_write():
    """R139: comprehensive CSOne_Detail_All must collapse dup case ids."""
    import pathlib

    src = pathlib.Path("app_simple.py").read_text(encoding="utf-8")
    assert "_r139_csone_sheet" in src
    assert "collapse_tac_cases as _r139_collapse_csone_sheet" in src


def test_round139_renewal_portfolio_uses_collapsed_tac_not_raw_rowcount():
    """R139: portfolio renewal DOCX KPIs must not use len(csone) or summed bems."""
    import pathlib

    src = pathlib.Path("app_simple.py").read_text(encoding="utf-8")
    assert "tot_cases = cm.count_total_tac(customer_csone)" in src
    assert "tot_bems = cm.count_bems(customer_csone)" in src
    assert "tot_cases = len(customer_csone)" not in src
    assert "sum(a.get('bems_escalations_count', 0) for a in portfolio_renewal_analyses.values())" not in src
    assert "'support_cases_count': cm.count_total_tac(customer_csone)" in src
    assert "'bems_escalations_count': cm.count_bems(customer_csone)" in src


def test_round139_validator_tac_bems_use_collapsed_canonical_counts():
    """R139: portfolio_metrics and validator must agree after TAC collapse."""
    csone = pd.DataFrame(
        {
            "Case #": ["700840277", "700840277", "700822701"],
            "customer_name": ["WINTRUST", "WINTRUST", "OTHER"],
            "Transaction ID": ["BEMS01943186", "BEMS01999999", ""],
            "Severity": ["P2", "P2", "P3"],
        }
    )
    portfolio_metrics = {
        "total_barriers": 0,
        "total_cases": cm.count_total_tac(csone),
        "bems_count": cm.count_bems(csone),
        "total_customers": 2,
    }
    result = validate_report_consistency(
        pd.DataFrame(),
        csone,
        portfolio_metrics=portfolio_metrics,
        strict_mode=False,
    )
    assert result["is_valid"] is True
    assert not any("total_cases" in e for e in result.get("errors", []))
    assert not any("bems_count" in e for e in result.get("errors", []))
