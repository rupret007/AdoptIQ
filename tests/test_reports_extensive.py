"""
AdoptIQ Report Extensive Testing
Tests all report methods, variables, and edge cases for each report type.
Migrated to pytest -- all tests are auto-discovered via test_* naming.
"""

import os
import sys
import tempfile
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import pytest
from datetime import datetime, timedelta
from docx import Document


def _make_ab_df(rows=None):
    """Create adoption barrier DataFrame with standard columns."""
    if rows is None:
        rows = [
            {"customer_name": "Acme Corp", "SUBJECT_C": "Integration issue", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open", "ID": "AB001"},
            {"customer_name": "Acme Corp", "SUBJECT_C": "Login problem", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "ID": "AB002"},
            {"customer_name": "Beta Inc", "SUBJECT_C": "API timeout", "SEVERITY_C": "Medium", "AB_STATUS_C": "Closed", "ID": "AB003"},
        ]
    return pd.DataFrame(rows)


def _make_csone_df(rows=None):
    """Create CSOne/TAC case DataFrame with standard columns."""
    if rows is None:
        rows = [
            {"customer_name": "Acme Corp", "SR Number": "TAC001", "Title": "Login fails", "Severity": "P1", "Transaction ID": "BEMS01916938", "Date/Time Opened": (datetime.now() - timedelta(days=5)).isoformat()},
            {"customer_name": "Acme Corp", "SR Number": "TAC002", "Title": "API slow", "Severity": "P2", "Transaction ID": "", "Date/Time Opened": (datetime.now() - timedelta(days=10)).isoformat()},
            {"customer_name": "Beta Inc", "SR Number": "TAC003", "Title": "Config issue", "Severity": "P3", "Transaction ID": "", "Date/Time Opened": (datetime.now() - timedelta(days=20)).isoformat()},
        ]
    return pd.DataFrame(rows)


def _make_team_subs_df():
    """Create team subscriptions DataFrame."""
    return pd.DataFrame({
        "BU_NAME": ["Acme Corp", "Beta Inc"],
        "ACCOUNT_ID_C": ["ACC001", "ACC002"],
        "SUBSCRIPTION_ID": ["SUB001", "SUB002"],
        "RENEWAL_RISK_CATEGORY": ["High", "Low"],
    })


def test_report_utils():
    from report_utils import format_date, format_number, format_currency, get_risk_scoring_explanation, get_report_metadata_footer
    d = format_date(datetime(2025, 2, 2), "long")
    assert "February" in d and "2025" in d
    assert format_date(None) == "N/A"
    assert format_number(1234) == "1,234"
    assert format_number(12.5, decimals=1) == "12.5"
    assert format_number(0.5, as_percent=True) == "0.5%"
    assert format_currency(1000.5) == "$1,000.50"
    expl = get_risk_scoring_explanation()
    assert "Adoption Barriers" in expl and "BEMS" in expl
    footer = get_report_metadata_footer(report_type="Test", manager="Jane", days=90)
    assert "Test" in footer and "Jane" in footer and "90" in footer


def test_simple_renewal_risk_calculation():
    from app_simple import _calculate_simple_renewal_risk
    team_subs = _make_team_subs_df()
    ab = _make_ab_df()
    csone = _make_csone_df()
    ab_acme = ab[ab["customer_name"] == "Acme Corp"] if not ab.empty else ab
    csone_acme = csone[csone["customer_name"] == "Acme Corp"] if not csone.empty else csone
    result = _calculate_simple_renewal_risk(
        customer_name="Acme Corp",
        customer_ab=ab_acme,
        customer_csone=csone_acme,
        team_subs_df=team_subs,
        days=90,
    )
    assert "renewal_risk_score" in result
    assert "renewal_risk_category" in result
    assert "key_findings" in result
    assert "recommendations" in result
    assert "risk_factors" in result
    assert result["adoption_barriers_count"] == len(ab_acme)
    assert result["support_cases_count"] == len(csone_acme)
    assert result["bems_escalations_count"] >= 1

    empty_result = _calculate_simple_renewal_risk(
        customer_name="Empty Corp",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=team_subs,
        days=90,
    )
    assert empty_result["renewal_risk_score"] >= 0
    assert any("adoption" in str(f).lower() for f in empty_result["key_findings"])


def test_subtech_scope_fallback_relabels_unknown_contact_center_rows():
    from app_simple import _apply_subtech_scope_fallback

    df = pd.DataFrame(
        [
            {"sub_technology": "Other/Unknown"},
            {"sub_technology": "Unknown"},
            {"sub_technology": "Cisco UCCX"},
        ]
    )
    out = _apply_subtech_scope_fallback(df, "Contact Center")
    assert out.loc[0, "sub_technology"] == "All Contact Center"
    assert out.loc[1, "sub_technology"] == "All Contact Center"
    assert out.loc[2, "sub_technology"] == "Cisco UCCX"


@pytest.mark.slow
def test_renewal_report_single_customer():
    from app_simple import _create_simple_renewal_report, _calculate_simple_renewal_risk
    team_subs = _make_team_subs_df()
    renewal_analysis = _calculate_simple_renewal_risk(
        customer_name="Acme Corp",
        customer_ab=_make_ab_df(),
        customer_csone=_make_csone_df(),
        team_subs_df=team_subs,
        days=90,
    )
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "test_renewal_single")
        path = _create_simple_renewal_report(
            base_path=base,
            customer_name="Acme Corp",
            technology="Webex",
            days=90,
            renewal_analysis=renewal_analysis,
            customer_ab=_make_ab_df(),
            customer_csone=_make_csone_df(),
            portfolio_mode=False,
        )
        assert os.path.exists(path)
        # Round 88 / F3: ``base_path`` already carries the ``Renewal``
        # token (in production it is ``AdoptIQ_Report_Renewal_<tag>``),
        # so the writer no longer appends a redundant
        # ``_Renewal_Report`` suffix.  Aligned with Compact +
        # Comprehensive which save as ``{base}.docx``.  The synthetic
        # base in this test is ``test_renewal_single`` so the file is
        # ``test_renewal_single.docx``.
        assert path.endswith(".docx")
        assert not path.endswith("_Renewal_Report.docx")
        assert os.path.getsize(path) > 1000


@pytest.mark.slow
def test_renewal_report_portfolio():
    from app_simple import _create_simple_renewal_report
    ab = _make_ab_df()
    csone = _make_csone_df()
    renewal_analysis = {
        "portfolio_mode": True,
        "total_customers": 2,
        "customer_analyses": {
            "Acme Corp": {"renewal_risk_score": 65, "renewal_risk_category": "HIGH", "key_findings": [], "risk_factors": [], "recommendations": []},
            "Beta Inc": {"renewal_risk_score": 25, "renewal_risk_category": "LOW", "key_findings": [], "risk_factors": [], "recommendations": []},
        },
        "renewal_risk_score": 45,
        "renewal_risk_category": "MEDIUM",
        "key_findings": ["Portfolio total: 3 adoption barriers"],
        "recommendations": ["Prioritize Acme Corp"],
        "high_risk_customers": ["Acme Corp"],
        "medium_risk_customers": ["Beta Inc"],
        "low_risk_customers": [],
        "adoption_barriers_count": 3,
        "support_cases_count": 3,
        "bems_escalations_count": 1,
        "support_cases_from_snowflake": False,
    }
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "test_renewal_portfolio")
        path = _create_simple_renewal_report(
            base_path=base,
            customer_name="Portfolio",
            technology="Webex",
            days=90,
            renewal_analysis=renewal_analysis,
            customer_ab=ab,
            customer_csone=csone,
            portfolio_mode=True,
            all_customers=["Acme Corp", "Beta Inc"],
        )
        assert os.path.exists(path)
        assert os.path.getsize(path) > 1000


@pytest.mark.slow
def test_renewal_report_no_data():
    from app_simple import _create_simple_renewal_report, _calculate_simple_renewal_risk
    team_subs = _make_team_subs_df()
    renewal_analysis = _calculate_simple_renewal_risk(
        customer_name="NoData Corp",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=team_subs,
        days=90,
    )
    renewal_analysis["support_cases_from_snowflake"] = False
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "test_renewal_nodata")
        path = _create_simple_renewal_report(
            base_path=base,
            customer_name="NoData Corp",
            technology="Webex",
            days=90,
            renewal_analysis=renewal_analysis,
            customer_ab=pd.DataFrame(),
            customer_csone=pd.DataFrame(),
            portfolio_mode=False,
        )
        assert os.path.exists(path)
        assert os.path.getsize(path) > 500


@pytest.mark.slow
def test_renewal_report_includes_case_type_split_and_defect_linkage_table():
    from app_simple import _create_simple_renewal_report

    renewal_analysis = {
        "renewal_risk_score": 55,
        "renewal_risk_category": "HIGH",
        "adoption_barriers_count": 1,
        "support_cases_count": 2,
        "break_fix_cases_count": 1,
        "provisioning_cases_count": 1,
        "bems_escalations_count": 1,
        "support_cases_from_snowflake": False,
        "key_findings": [],
        "recommendations": [],
        "risk_factors": [],
    }
    csone = pd.DataFrame(
        [
            {
                "customer_name": "Acme Corp",
                "SR Number": "TAC001",
                "Title": "Break fix issue",
                "Severity": "P1",
                "Status": "Open",
                "Transaction ID": "BEMS01916938",
                "Date/Time Opened": (datetime.now() - timedelta(days=4)).isoformat(),
            },
            {
                "customer_name": "Acme Corp",
                "SR Number": "TAC002",
                "Title": "Provisioning request",
                "Severity": "P3",
                "Status": "Closed",
                "Transaction ID": "",
                "Date/Time Opened": (datetime.now() - timedelta(days=8)).isoformat(),
            },
        ]
    )
    software_defects = {
        "total_defects": 2,
        "total_cases_with_defects": 2,
        "defect_by_customer": {"Acme Corp": ["CSCaa11111", "CSCbb22222"]},
    }
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "renewal_case_type_defect_linkage")
        path = _create_simple_renewal_report(
            base_path=base,
            customer_name="Acme Corp",
            technology="All Contact Center",
            days=90,
            renewal_analysis=renewal_analysis,
            customer_ab=_make_ab_df([{"customer_name": "Acme Corp", "SUBJECT_C": "Barrier", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "ID": "AB01"}]),
            customer_csone=csone,
            software_defects=software_defects,
            portfolio_mode=False,
        )
        doc = Document(path)
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "TAC Case Type Breakdown" in text
        assert "Defect-to-Customer Linkage" in text
        table_headers = {
            tuple(cell.text for cell in table.rows[0].cells)
            for table in doc.tables
            if table.rows
        }
        assert ("Case Type", "Count") in table_headers
        assert ("Customer Name", "Defect Count", "Defect IDs") in table_headers


@pytest.mark.slow
def test_compact_report_formatter():
    from compact_report_formatter import create_compact_executive_report, calculate_renewal_risk_scores
    ab = _make_ab_df()
    csone = _make_csone_df()
    risk_data = calculate_renewal_risk_scores(ab, csone)
    assert len(risk_data) >= 2
    for cust, info in risk_data.items():
        assert "score" in info
        assert "color" in info
        assert "category" in info
        assert "risk_factors" in info
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_compact.docx")
        path = create_compact_executive_report(
            analysis_id="test-123",
            manager="Jane Doe",
            technology="Webex",
            days=90,
            ab_data=ab,
            csone_data=csone,
            ai_insights={"executive_summary": "Test AI summary"},
            output_path=out,
        )
        assert os.path.exists(path)
        assert os.path.getsize(path) > 2000


@pytest.mark.slow
def test_compact_report_total_customers_consistent_between_dashboard_and_summary():
    from compact_report_formatter import create_compact_executive_report

    ab = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SUBJECT_C": "Barrier 1", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "ID": "AB001"},
        ]
    )
    csone = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SR Number": "TAC001", "Title": "Issue 1", "Severity": "P1", "Transaction ID": ""},
            {"customer_name": "Beta Inc", "SR Number": "TAC002", "Title": "Issue 2", "Severity": "P2", "Transaction ID": ""},
            {"customer_name": "Beta Inc", "SR Number": "TAC003", "Title": "Issue 3", "Severity": "P3", "Transaction ID": ""},
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "compact_customer_parity.docx")
        path = create_compact_executive_report(
            analysis_id="test-parity",
            manager="Jane Doe",
            technology="All Contact Center",
            days=90,
            ab_data=ab,
            csone_data=csone,
            ai_insights={"executive_summary": "Portfolio summary"},
            output_path=out,
        )
        doc = Document(path)
        dashboard_table = next(
            (
                table
                for table in doc.tables
                if table.rows
                and any("Total Customers" in cell.text for cell in table.rows[0].cells)
            ),
            None,
        )
        assert dashboard_table is not None
        dashboard_values = [dashboard_table.rows[1].cells[i].text for i in range(5)]
        assert dashboard_values[0] == "2"
        full_text = "\n".join(p.text for p in doc.paragraphs)
        assert "Total customers analyzed: 2" in full_text


@pytest.mark.slow
def test_compact_report_empty_data():
    from compact_report_formatter import create_compact_executive_report
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_compact_empty.docx")
        path = create_compact_executive_report(
            analysis_id="test-empty",
            manager="Jane",
            technology="Webex",
            days=90,
            ab_data=pd.DataFrame(),
            csone_data=pd.DataFrame(),
            ai_insights={},
            output_path=out,
        )
        assert os.path.exists(path)


def test_risk_scores_edge_cases():
    from compact_report_formatter import calculate_renewal_risk_scores
    r = calculate_renewal_risk_scores(pd.DataFrame(), pd.DataFrame())
    assert r == {}
    ab = pd.DataFrame([{"customer_name": "X", "SUBJECT_C": "Issue", "SEVERITY_C": "Low", "AB_STATUS_C": "Open"}])
    r = calculate_renewal_risk_scores(ab, pd.DataFrame())
    assert "X" in r
    assert r["X"]["score"] >= 0
    assert "risk_factors" in r["X"]
    csone = pd.DataFrame([{"customer_name": "Y", "Transaction ID": "BEMS123", "Severity": "P1", "Date/Time Opened": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")}])
    r = calculate_renewal_risk_scores(pd.DataFrame(), csone)
    assert "Y" in r
    assert r["Y"]["score"] >= 0
    assert "risk_factors" in r["Y"]
    old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    ab_aging = pd.DataFrame([{"customer_name": "AgingCust", "SUBJECT_C": "Old issue", "SEVERITY_C": "Medium", "AB_STATUS_C": "Open", "CREATED_DATE": old_date}])
    r_aging = calculate_renewal_risk_scores(ab_aging, pd.DataFrame())
    assert "AgingCust" in r_aging
    factors = r_aging["AgingCust"]["risk_factors"]
    assert any("60+ days" in f for f in factors), f"Expected aging factor, got: {factors}"


def test_advanced_renewal_analyzer_mock():
    try:
        from advanced_renewal_analyzer import AdvancedRenewalAnalyzer
        analyzer = AdvancedRenewalAnalyzer("mock_connection")
        result = analyzer.analyze_customer_renewal_risk("Test Customer", 90)
        assert "renewal_risk_category" in result
        assert "key_findings" in result
        assert result.get("renewal_risk_category") == "HIGH"
    except ImportError:
        pytest.skip("AdvancedRenewalAnalyzer not importable")


def test_leader_report_generator_structure():
    try:
        from leader_report_generator import LeaderReportGenerator
        assert hasattr(LeaderReportGenerator, "generate_leader_report")
        assert hasattr(LeaderReportGenerator, "_create_title_page")
    except ImportError:
        pytest.skip("LeaderReportGenerator not importable")


def test_executive_intelligence_formatter():
    try:
        from executive_intelligence_formatter import ExecutiveIntelligenceFormatter
        from docx import Document
        formatter = ExecutiveIntelligenceFormatter()
        formatter.doc = Document()
        formatter.add_executive_summary({"executive_summary": "Test summary"})
        assert len(formatter.doc.paragraphs) > 0
    except ImportError:
        pytest.skip("ExecutiveIntelligenceFormatter not importable")


@pytest.mark.flask
def test_history_route_and_analyses_mapping(client):
    rv = client.get("/history")
    assert rv.status_code == 200
    html = rv.data.decode("utf-8")
    assert "Analysis History" in html
    assert "Previous Analyses" in html or "No Analysis History Found" in html or "Run Your First Analysis" in html

    raw_one = [
        {
            "request_id": "Compact_Webex_30d_20250205_120000",
            "report_type": "compact",
            "manager": "Jane",
            "technology": "Webex",
            "customer_name": "",
            "status": "completed",
            "start_time": "2025-02-05T12:00:00",
            "end_time": "2025-02-05T12:05:00",
            "created_at": "2025-02-05T12:05:00",
        }
    ]

    def map_raw_to_analyses(raw):
        if raw is None or not isinstance(raw, list):
            raw = []
        return [
            {
                "id": r.get("request_id", ""),
                "start_time": r.get("start_time") or r.get("created_at") or "Unknown",
                "manager": r.get("manager") or "\u2014",
                "technology": r.get("technology") or "\u2014",
                "days": r.get("days"),
                "report_type": r.get("report_type", ""),
                "customer_name": r.get("customer_name", ""),
                "status": r.get("status", ""),
            }
            for r in raw
        ]

    assert map_raw_to_analyses(None) == []
    assert map_raw_to_analyses([]) == []
    analyses_one = map_raw_to_analyses(raw_one)
    assert len(analyses_one) == 1
    assert analyses_one[0]["id"] == "Compact_Webex_30d_20250205_120000"
    assert analyses_one[0]["manager"] == "Jane"
    analyses_missing = map_raw_to_analyses([{}])
    assert len(analyses_missing) == 1
    assert analyses_missing[0]["manager"] == "\u2014"
    assert analyses_missing[0]["start_time"] == "Unknown"


def test_learned_insights_store_and_retrieve():
    import enhanced_admin_dashboard_v2 as admin
    original_db_path = admin.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test_insights.db")
        try:
            admin.DB_PATH = db_path
            admin.init_database()
            admin.store_report_insights(
                "req1", "comprehensive", "Alice", "Webex", "",
                {"customer_count": 10, "barrier_count": 5, "summary_line": "First run"}
            )
            admin.store_report_insights(
                "req2", "compact", "Alice", "Webex", "",
                {"summary_line": "Second run"}
            )
            out = admin.get_learned_insights("Alice", "Webex", limit=5)
            assert "Learned from past analyses" in out
            assert "10 customers" in out
            assert "5 adoption barriers" in out
            assert "First run" in out or "Second run" in out
            assert "comprehensive" in out or "compact" in out
            empty_out = admin.get_learned_insights("Nobody", "Nothing", limit=5)
            assert empty_out == ""
        finally:
            admin.DB_PATH = original_db_path


def test_status_persistence_datetime_fields():
    import app_simple as app_mod
    original_app_support = app_mod._APP_SUPPORT
    with app_mod.analysis_status_lock:
        original_status = dict(app_mod.analysis_status)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            app_mod._APP_SUPPORT = Path(tmp)
            status_path = Path(tmp) / app_mod.STATUS_FILE
            payload = {
                "test-analysis-id": {
                    "status": "completed",
                    "start_time": "2026-02-01T10:00:00",
                    "completion_time": "2026-02-01T10:05:00",
                    "end_time": "2026-02-01T10:05:00",
                    "step_start_time": "2026-02-01T10:04:00",
                }
            }
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
            app_mod.load_analysis_status()
            with app_mod.analysis_status_lock:
                loaded = app_mod.analysis_status.get("test-analysis-id", {})
                assert isinstance(loaded.get("start_time"), datetime)
                assert isinstance(loaded.get("completion_time"), datetime)
                assert isinstance(loaded.get("end_time"), datetime)
            app_mod.save_analysis_status()
            assert status_path.exists()
        finally:
            app_mod._APP_SUPPORT = original_app_support
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original_status)


def test_build_insights_payload_helper():
    from app_simple import _build_insights_payload, INSIGHT_SUMMARY_MAX_CHARS
    status = {"message": "A" * (INSIGHT_SUMMARY_MAX_CHARS + 25)}
    payload = _build_insights_payload(status, "fallback")
    assert "summary_line" in payload
    assert len(payload["summary_line"]) == INSIGHT_SUMMARY_MAX_CHARS

    payload2 = _build_insights_payload({}, "fallback", {"risk_theme": "HIGH"})
    assert payload2["summary_line"] == "fallback"
    assert payload2["risk_theme"] == "HIGH"


def test_wxcc_excludes_wxcce_signatures():
    from adoptiq_backend import _filter_tech_text_enhanced
    assert _filter_tech_text_enhanced(
        "Contact Center Software",
        "Webex CCE / Webex Contact Center Enterprise",
        "Webex Contact Center",
    ) is False
    assert _filter_tech_text_enhanced(
        "Contact Center Software",
        "Webex CCE / Webex Contact Center Enterprise",
        "Webex Contact Center Enterprise",
    ) is True
    assert _filter_tech_text_enhanced(
        "Webex Contact Center",
        "Webex Contact Center",
        "Webex Contact Center",
    ) is True


def test_csconsole_filter_separates_wxcc_and_wxcce():
    from adoptiq_backend import _filter_csconsole_data_by_technology
    df = pd.DataFrame([
        {"BU_NAME": "CustA", "SUB_TECHNOLOGY_C": "Webex Contact Center", "TECHNOLOGY_C": "Contact Center Software", "SUBJECT_C": "wxcc onboarding"},
        {"BU_NAME": "CustA", "SUB_TECHNOLOGY_C": "Webex CCE / Webex Contact Center Enterprise", "TECHNOLOGY_C": "Contact Center Software", "SUBJECT_C": "enterprise queue issue"},
        {"BU_NAME": "CustB", "SUB_TECHNOLOGY_C": "Webex Contact Center Enterprise", "TECHNOLOGY_C": "Contact Center Software", "SUBJECT_C": "wxcce routing issue"},
    ])
    wxcc = _filter_csconsole_data_by_technology(df, "Webex Contact Center")
    assert len(wxcc) == 1
    assert str(wxcc.iloc[0]["SUB_TECHNOLOGY_C"]) == "Webex Contact Center"
    wxcce = _filter_csconsole_data_by_technology(df, "Webex Contact Center Enterprise")
    assert len(wxcce) == 2


def test_csconsole_filter_customer_names_supports_success_priority_columns():
    from adoptiq_backend import _filter_csconsole_data_by_technology
    df = pd.DataFrame(
        [
            {"CUSTOMER_BU_NAME__C": "Acme Corp", "TECHNOLOGY_C": "Webex Contact Center", "SUBJECT_C": "priority alignment"},
            {"CUSTOMER_BU_NAME__C": "Beta Inc", "TECHNOLOGY_C": "Webex Contact Center", "SUBJECT_C": "other customer"},
        ]
    )
    filtered = _filter_csconsole_data_by_technology(df, "All", customer_names=["Acme  Corp"])
    assert len(filtered) == 1
    assert filtered.iloc[0]["CUSTOMER_BU_NAME__C"] == "Acme Corp"


def test_csconsole_filter_customer_names_supports_related_customer_column():
    from adoptiq_backend import _filter_csconsole_data_by_technology
    df = pd.DataFrame(
        [
            {"RELATED_CUSTOMER__C": "Acme Corp", "TECHNOLOGY_C": "Webex Contact Center", "SUBJECT_C": "success priority"},
            {"RELATED_CUSTOMER__C": "Gamma LLC", "TECHNOLOGY_C": "Webex Contact Center", "SUBJECT_C": "unrelated"},
        ]
    )
    filtered = _filter_csconsole_data_by_technology(df, "All", customer_names=["Acme Corp"])
    assert len(filtered) == 1
    assert filtered.iloc[0]["RELATED_CUSTOMER__C"] == "Acme Corp"


def test_csconsole_filter_customer_pulse_account_scope_fallback_uses_sf15():
    from adoptiq_backend import _filter_csconsole_data_by_technology
    df = pd.DataFrame(
        [
            {"ACCOUNT__C": "001ABCDEF123456", "BU_NAME": "", "TECHNOLOGY_C": "Webex Contact Center"},
            {"ACCOUNT__C": "001ZZZDEF123456", "BU_NAME": "", "TECHNOLOGY_C": "Webex Contact Center"},
        ]
    )
    filtered = _filter_csconsole_data_by_technology(
        df,
        "All Contact Center",
        customer_names=["Does Not Match"],
        account_ids=["001ABCDEF123456AAA"],
    )
    assert len(filtered) == 1
    assert filtered.iloc[0]["ACCOUNT__C"] == "001ABCDEF123456"


def test_csconsole_filter_tech_falls_back_to_account_scope_when_text_missing():
    from adoptiq_backend import _filter_csconsole_data_by_technology
    df = pd.DataFrame(
        [
            {"ACCOUNT__C": "001ABCDEF123456", "SUBJECT_C": "General customer pulse update"},
            {"ACCOUNT__C": "001ZZZDEF123456", "SUBJECT_C": "General customer pulse update"},
        ]
    )
    filtered = _filter_csconsole_data_by_technology(
        df,
        "Webex Contact Center",
        account_ids=["001ABCDEF123456AAA"],
    )
    assert len(filtered) == 1
    assert filtered.iloc[0]["ACCOUNT__C"] == "001ABCDEF123456"


def test_csconsole_filter_account_scope_supports_account_column_variant():
    from adoptiq_backend import _filter_csconsole_data_by_technology

    df = pd.DataFrame(
        [
            {"ACCOUNT": "001ABCDEF123456", "SUBJECT_C": "General customer pulse update"},
            {"ACCOUNT": "001ZZZDEF123456", "SUBJECT_C": "General customer pulse update"},
        ]
    )
    filtered = _filter_csconsole_data_by_technology(
        df,
        "Webex Contact Center",
        account_ids=["001ABCDEF123456AAA"],
    )
    assert len(filtered) == 1
    assert filtered.iloc[0]["ACCOUNT"] == "001ABCDEF123456"


def test_customer_activity_includes_csconsole_only_data():
    from app_simple import _has_customer_activity_for_deep_dive
    empty = pd.DataFrame()
    csconsole_only = pd.DataFrame([{"ACCOUNT_ID_C": "001234", "Status": "Open"}])
    assert _has_customer_activity_for_deep_dive(empty, empty, empty, empty, empty, csconsole_only) is True
    assert _has_customer_activity_for_deep_dive(empty, empty, empty, empty, empty, empty) is False


def test_csconsole_ab_fallback_fields_render_in_briefing():
    from adoptiq_backend import _create_briefing_book
    csconsole_data = {
        "action_plans": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "success_priorities": pd.DataFrame(),
        "adoption_barriers": pd.DataFrame([
            {
                "Record ID": "AB-ALT-001",
                "Barrier Title": "Adapter parity gap",
                "Description": "Legacy adapter behavior diverges from WxCC flow.",
                "Status": "Open",
                "Severity": "High",
                "Customer Name": "Example Customer",
                "Owner Email": "owner@example.com",
            }
        ]),
    }
    briefing = _create_briefing_book(
        "Example Customer",
        pd.DataFrame(),
        pd.DataFrame(),
        [],
        [],
        [],
        pd.DataFrame(),
        None,
        None,
        csconsole_data,
    )
    assert "Barrier [AB-ALT-001]" in briefing
    assert "Adapter parity gap" in briefing
    assert "Legacy adapter behavior diverges from WxCC flow." in briefing
    assert "Status: Open | Severity: High" in briefing
    assert "(Example Customer, CSSM: owner@example.com)" in briefing
