"""Round 139 / Build 109 — AP scope, TAC collapse, health grades, Excel headers."""

import pandas as pd

import canonical_metrics as cm
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
