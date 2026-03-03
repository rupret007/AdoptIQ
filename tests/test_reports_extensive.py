#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AdoptIQ Report Extensive Testing
Tests all report methods, variables, and edge cases for each report type.
"""

import os
import sys
import tempfile
import shutil
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from datetime import datetime, timedelta


def _make_ab_df(rows=None):
    """Create adoption barrier DataFrame with standard columns."""
    if rows is None:
        rows = [
            {'customer_name': 'Acme Corp', 'SUBJECT_C': 'Integration issue', 'SEVERITY_C': 'Critical', 'AB_STATUS_C': 'Open', 'ID': 'AB001'},
            {'customer_name': 'Acme Corp', 'SUBJECT_C': 'Login problem', 'SEVERITY_C': 'High', 'AB_STATUS_C': 'Open', 'ID': 'AB002'},
            {'customer_name': 'Beta Inc', 'SUBJECT_C': 'API timeout', 'SEVERITY_C': 'Medium', 'AB_STATUS_C': 'Closed', 'ID': 'AB003'},
        ]
    return pd.DataFrame(rows)


def _make_csone_df(rows=None):
    """Create CSOne/TAC case DataFrame with standard columns."""
    if rows is None:
        rows = [
            {'customer_name': 'Acme Corp', 'SR Number': 'TAC001', 'Title': 'Login fails', 'Severity': 'P1', 'Transaction ID': 'BEMS01916938', 'Date/Time Opened': (datetime.now() - timedelta(days=5)).isoformat()},
            {'customer_name': 'Acme Corp', 'SR Number': 'TAC002', 'Title': 'API slow', 'Severity': 'P2', 'Transaction ID': '', 'Date/Time Opened': (datetime.now() - timedelta(days=10)).isoformat()},
            {'customer_name': 'Beta Inc', 'SR Number': 'TAC003', 'Title': 'Config issue', 'Severity': 'P3', 'Transaction ID': '', 'Date/Time Opened': (datetime.now() - timedelta(days=20)).isoformat()},
        ]
    return pd.DataFrame(rows)


def _make_team_subs_df():
    """Create team subscriptions DataFrame."""
    return pd.DataFrame({
        'BU_NAME': ['Acme Corp', 'Beta Inc'],
        'ACCOUNT_ID_C': ['ACC001', 'ACC002'],
        'SUBSCRIPTION_ID': ['SUB001', 'SUB002'],
        'RENEWAL_RISK_CATEGORY': ['High', 'Low'],
    })


def test_report_utils():
    """Test report_utils module."""
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
    print("  [OK] report_utils")


def test_simple_renewal_risk_calculation():
    """Test _calculate_simple_renewal_risk with various inputs."""
    from app_simple import _calculate_simple_renewal_risk
    team_subs = _make_team_subs_df()
    # With data (filter to single customer for renewal risk)
    ab = _make_ab_df()
    csone = _make_csone_df()
    ab_acme = ab[ab['customer_name'] == 'Acme Corp'] if not ab.empty else ab
    csone_acme = csone[csone['customer_name'] == 'Acme Corp'] if not csone.empty else csone
    result = _calculate_simple_renewal_risk(
        customer_name="Acme Corp",
        customer_ab=ab_acme,
        customer_csone=csone_acme,
        team_subs_df=team_subs,
        days=90,
    )
    assert 'renewal_risk_score' in result
    assert 'renewal_risk_category' in result
    assert 'key_findings' in result
    assert 'recommendations' in result
    assert 'risk_factors' in result
    assert result['adoption_barriers_count'] == len(ab_acme)
    assert result['support_cases_count'] == len(csone_acme)
    assert result['bems_escalations_count'] >= 1
    # Empty data
    empty_result = _calculate_simple_renewal_risk(
        customer_name="Empty Corp",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=team_subs,
        days=90,
    )
    assert empty_result['renewal_risk_score'] >= 0
    assert any("adoption" in str(f).lower() for f in empty_result['key_findings'])
    print("  [OK] _calculate_simple_renewal_risk")


def test_renewal_report_single_customer():
    """Test _create_simple_renewal_report for single customer."""
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
        assert path.endswith("_Renewal_Report.docx")
        # Verify file is valid (has content)
        assert os.path.getsize(path) > 1000
    print("  [OK] _create_simple_renewal_report (single)")


def test_renewal_report_portfolio():
    """Test _create_simple_renewal_report for portfolio mode."""
    from app_simple import _create_simple_renewal_report
    ab = _make_ab_df()
    csone = _make_csone_df()
    renewal_analysis = {
        'portfolio_mode': True,
        'total_customers': 2,
        'customer_analyses': {
            'Acme Corp': {'renewal_risk_score': 65, 'renewal_risk_category': 'HIGH', 'key_findings': [], 'risk_factors': [], 'recommendations': []},
            'Beta Inc': {'renewal_risk_score': 25, 'renewal_risk_category': 'LOW', 'key_findings': [], 'risk_factors': [], 'recommendations': []},
        },
        'renewal_risk_score': 45,
        'renewal_risk_category': 'MEDIUM',
        'key_findings': ['Portfolio total: 3 adoption barriers'],
        'recommendations': ['Prioritize Acme Corp'],
        'high_risk_customers': ['Acme Corp'],
        'medium_risk_customers': ['Beta Inc'],
        'low_risk_customers': [],
        'adoption_barriers_count': 3,
        'support_cases_count': 3,
        'bems_escalations_count': 1,
        'support_cases_from_snowflake': False,
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
            all_customers=['Acme Corp', 'Beta Inc'],
        )
        assert os.path.exists(path)
        assert os.path.getsize(path) > 1000
    print("  [OK] _create_simple_renewal_report (portfolio)")


def test_renewal_report_no_data():
    """Test renewal report with empty CSOne and adoption barriers."""
    from app_simple import _create_simple_renewal_report, _calculate_simple_renewal_risk
    team_subs = _make_team_subs_df()
    renewal_analysis = _calculate_simple_renewal_risk(
        customer_name="NoData Corp",
        customer_ab=pd.DataFrame(),
        customer_csone=pd.DataFrame(),
        team_subs_df=team_subs,
        days=90,
    )
    renewal_analysis['support_cases_from_snowflake'] = False
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
        # Docx is zip format; we verify file exists and has content
        assert os.path.getsize(path) > 500
    print("  [OK] _create_simple_renewal_report (no data)")


def test_compact_report_formatter():
    """Test compact report formatter and create_compact_executive_report."""
    from compact_report_formatter import create_compact_executive_report, calculate_renewal_risk_scores
    ab = _make_ab_df()
    csone = _make_csone_df()
    risk_data = calculate_renewal_risk_scores(ab, csone)
    assert len(risk_data) >= 2
    for cust, info in risk_data.items():
        assert 'score' in info
        assert 'color' in info
        assert 'category' in info
        assert 'risk_factors' in info
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test_compact.docx")
        path = create_compact_executive_report(
            analysis_id="test-123",
            manager="Jane Doe",
            technology="Webex",
            days=90,
            ab_data=ab,
            csone_data=csone,
            ai_insights={'executive_summary': 'Test AI summary'},
            output_path=out,
        )
        assert os.path.exists(path)
        assert os.path.getsize(path) > 2000
    print("  [OK] create_compact_executive_report")


def test_compact_report_empty_data():
    """Test compact report with empty data."""
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
    print("  [OK] create_compact_executive_report (empty)")


def test_risk_scores_edge_cases():
    """Test risk score calculation edge cases."""
    from compact_report_formatter import calculate_renewal_risk_scores
    # Empty
    r = calculate_renewal_risk_scores(pd.DataFrame(), pd.DataFrame())
    assert r == {}
    # Only AB
    ab = pd.DataFrame([{'customer_name': 'X', 'SUBJECT_C': 'Issue', 'SEVERITY_C': 'Low', 'AB_STATUS_C': 'Open'}])
    r = calculate_renewal_risk_scores(ab, pd.DataFrame())
    assert 'X' in r
    assert r['X']['score'] >= 0
    assert 'risk_factors' in r['X']
    # CSOne with case (compact formatter uses 0-10 scale, different from renewal 0-100)
    csone = pd.DataFrame([{'customer_name': 'Y', 'Transaction ID': 'BEMS123', 'Severity': 'P1', 'Date/Time Opened': (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')}])
    r = calculate_renewal_risk_scores(pd.DataFrame(), csone)
    assert 'Y' in r
    assert r['Y']['score'] >= 0
    assert 'risk_factors' in r['Y']
    # Aging barrier (open 60+ days) adds penalty
    old_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    ab_aging = pd.DataFrame([{'customer_name': 'AgingCust', 'SUBJECT_C': 'Old issue', 'SEVERITY_C': 'Medium', 'AB_STATUS_C': 'Open', 'CREATED_DATE': old_date}])
    r_aging = calculate_renewal_risk_scores(ab_aging, pd.DataFrame())
    assert 'AgingCust' in r_aging
    factors = r_aging['AgingCust']['risk_factors']
    assert any('60+ days' in f for f in factors), f"Expected aging factor, got: {factors}"
    print("  [OK] risk scores edge cases")


def test_advanced_renewal_analyzer_mock():
    """Test AdvancedRenewalAnalyzer with mock connection."""
    try:
        from advanced_renewal_analyzer import AdvancedRenewalAnalyzer
        analyzer = AdvancedRenewalAnalyzer('mock_connection')
        result = analyzer.analyze_customer_renewal_risk("Test Customer", 90)
        assert 'renewal_risk_category' in result
        assert 'key_findings' in result
        assert result.get('renewal_risk_category') == 'HIGH'  # Customer not found
    except ImportError as e:
        print(f"  [SKIP] AdvancedRenewalAnalyzer: {e}")
    else:
        print("  [OK] AdvancedRenewalAnalyzer (mock)")


def test_leader_report_generator_structure():
    """Test LeaderReportGenerator structure (without full Snowflake)."""
    try:
        from leader_report_generator import LeaderReportGenerator
        # Just verify class exists and has expected methods
        assert hasattr(LeaderReportGenerator, 'generate_leader_report')
        assert hasattr(LeaderReportGenerator, '_create_title_page')
    except ImportError as e:
        print(f"  [SKIP] LeaderReportGenerator: {e}")
    else:
        print("  [OK] LeaderReportGenerator structure")


def test_executive_intelligence_formatter():
    """Test executive intelligence formatter."""
    try:
        from executive_intelligence_formatter import ExecutiveIntelligenceFormatter
        from docx import Document
        formatter = ExecutiveIntelligenceFormatter()
        formatter.doc = Document()
        formatter.add_executive_summary({'executive_summary': 'Test summary'})
        assert len(formatter.doc.paragraphs) > 0
    except ImportError as e:
        print(f"  [SKIP] ExecutiveIntelligenceFormatter: {e}")
    else:
        print("  [OK] ExecutiveIntelligenceFormatter")


def test_filter_subscriptions_by_criteria():
    """Test filter_subscriptions_by_criteria: subscription_id, customer_name, empty, invalid format."""
    from app_simple import filter_subscriptions_by_criteria
    team = pd.DataFrame({
        'BU_NAME': ['Acme Corp', 'Acme Corp UK', 'Beta Inc'],
        'ACCOUNT_ID_C': ['A1', 'A2', 'B1'],
        'SUBSCRIPTION_ID': ['Sub123', 'Sub456', 'Sub789'],
    })
    # No filter: returns copy, no error
    out, err = filter_subscriptions_by_criteria(team, None, None)
    assert err is None
    assert len(out) == 3
    # Filter by customer name (partial, case-insensitive)
    out, err = filter_subscriptions_by_criteria(team, 'acme', None)
    assert err is None
    assert len(out) == 2
    assert set(out['BU_NAME']) == {'Acme Corp', 'Acme Corp UK'}
    # Filter by subscription ID (exact)
    out, err = filter_subscriptions_by_criteria(team, None, 'Sub456')
    assert err is None
    assert len(out) == 1
    assert out['SUBSCRIPTION_ID'].iloc[0] == 'Sub456'
    # Customer name no match
    out, err = filter_subscriptions_by_criteria(team, 'NoMatch', None)
    assert err is not None
    assert 'No subscriptions found' in err
    assert out.empty
    # Subscription ID no match
    out, err = filter_subscriptions_by_criteria(team, None, 'Sub999')
    assert err is not None
    assert out.empty
    # Invalid subscription ID format (e.g. semicolon)
    out, err = filter_subscriptions_by_criteria(team, None, 'Sub123;DROP')
    assert err is not None
    assert 'Invalid subscription ID format' in err
    assert out.empty
    # Empty team_subs_df
    out, err = filter_subscriptions_by_criteria(pd.DataFrame(), None, None)
    assert err is not None
    assert 'No subscriptions available' in err
    # customer_name takes precedence when both provided (implementation uses if/elif)
    out, err = filter_subscriptions_by_criteria(team, 'Beta', 'Sub123')
    assert err is None
    assert len(out) == 1
    assert out['BU_NAME'].iloc[0] == 'Beta Inc'
    print("  [OK] filter_subscriptions_by_criteria")


def test_validate_customer_name_input():
    """Test validate_customer_name_input: valid, empty, too long, dangerous chars, Dropbox allowed."""
    from app_simple import validate_customer_name_input
    # Valid
    ok, msg = validate_customer_name_input('Acme Corp')
    assert ok is True
    assert msg == 'Valid'
    # Empty is allowed (optional field)
    ok, msg = validate_customer_name_input('')
    assert ok is True
    ok, msg = validate_customer_name_input(None)
    assert ok is True
    # Too long
    ok, msg = validate_customer_name_input('A' * 201)
    assert ok is False
    assert 'long' in msg.lower()
    # Not a string
    ok, msg = validate_customer_name_input(123)
    assert ok is False
    assert 'text' in msg.lower()
    # Dangerous chars
    ok, msg = validate_customer_name_input("Acme'; DROP TABLE--")
    assert ok is False
    assert 'Invalid' in msg or 'character' in msg.lower()
    ok, msg = validate_customer_name_input('Test<script>alert(1)')
    assert ok is False
    # Allowed: names that used to be blocked (no substring DROP/SELECT)
    ok, msg = validate_customer_name_input('Dropbox')
    assert ok is True
    ok, msg = validate_customer_name_input('Select Inc')
    assert ok is True
    print("  [OK] validate_customer_name_input")


def test_history_route_and_analyses_mapping():
    """Test /history route returns 200 and analyses mapping (raw -> template list) is correct."""
    from app_simple import app

    with app.test_client() as client:
        # Empty history: GET /history should return 200 and empty state text
        rv = client.get("/history")
        assert rv.status_code == 200, f"Expected 200, got {rv.status_code}"
        html = rv.data.decode("utf-8")
        assert "Analysis History" in html
        # With no analyses we may see empty state or table
        assert "Previous Analyses" in html or "No Analysis History Found" in html or "Run Your First Analysis" in html

    # Test analyses mapping logic (same as history() view)
    raw_none = None
    raw_empty = []
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
    raw_missing_keys = [{}]

    def map_raw_to_analyses(raw):
        if raw is None or not isinstance(raw, list):
            raw = []
        return [
            {
                "id": r.get("request_id", ""),
                "start_time": r.get("start_time") or r.get("created_at") or "Unknown",
                "manager": r.get("manager") or "—",
                "technology": r.get("technology") or "—",
                "days": r.get("days"),
                "report_type": r.get("report_type", ""),
                "customer_name": r.get("customer_name", ""),
                "status": r.get("status", ""),
            }
            for r in raw
        ]

    analyses_none = map_raw_to_analyses(raw_none)
    assert analyses_none == []
    analyses_empty = map_raw_to_analyses(raw_empty)
    assert analyses_empty == []
    analyses_one = map_raw_to_analyses(raw_one)
    assert len(analyses_one) == 1
    assert analyses_one[0]["id"] == "Compact_Webex_30d_20250205_120000"
    assert analyses_one[0]["manager"] == "Jane"
    assert analyses_one[0]["technology"] == "Webex"
    assert analyses_one[0]["start_time"] == "2025-02-05T12:00:00"
    analyses_missing = map_raw_to_analyses(raw_missing_keys)
    assert len(analyses_missing) == 1
    assert analyses_missing[0]["manager"] == "—"
    assert analyses_missing[0]["start_time"] == "Unknown"
    print("  [OK] history route and analyses mapping")


def test_learned_insights_store_and_retrieve():
    """Test store_report_insights and get_learned_insights with a temp DB."""
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
    print("  [OK] learned insights store and retrieve")


def test_status_persistence_datetime_fields():
    """Test status persistence parses end_time and save works without external lock contract."""
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

            # Ensure save works when called directly and writes expected file.
            app_mod.save_analysis_status()
            assert status_path.exists()
        finally:
            app_mod._APP_SUPPORT = original_app_support
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original_status)

    print("  [OK] status persistence datetime fields")


def test_build_insights_payload_helper():
    """Test insights payload helper trims, defaults, and merges extras."""
    from app_simple import _build_insights_payload, INSIGHT_SUMMARY_MAX_CHARS

    status = {'message': 'A' * (INSIGHT_SUMMARY_MAX_CHARS + 25)}
    payload = _build_insights_payload(status, 'fallback')
    assert 'summary_line' in payload
    assert len(payload['summary_line']) == INSIGHT_SUMMARY_MAX_CHARS
    assert payload['summary_line'] == 'A' * INSIGHT_SUMMARY_MAX_CHARS

    payload2 = _build_insights_payload({}, 'fallback', {'risk_theme': 'HIGH'})
    assert payload2['summary_line'] == 'fallback'
    assert payload2['risk_theme'] == 'HIGH'
    print("  [OK] build insights payload helper")


def test_wxcc_excludes_wxcce_signatures():
    """WxCC must not match enterprise-tagged rows/signatures."""
    from adoptiq_backend import _filter_tech_text_enhanced

    # Reported production signature from CSOne.
    assert _filter_tech_text_enhanced(
        "Contact Center Software",
        "Webex CCE / Webex Contact Center Enterprise",
        "Webex Contact Center",
    ) is False

    # Enterprise filter should still match the same row.
    assert _filter_tech_text_enhanced(
        "Contact Center Software",
        "Webex CCE / Webex Contact Center Enterprise",
        "Webex Contact Center Enterprise",
    ) is True

    # True WxCC rows must remain included.
    assert _filter_tech_text_enhanced(
        "Webex Contact Center",
        "Webex Contact Center",
        "Webex Contact Center",
    ) is True
    print("  [OK] wxcc excludes wxcce signatures")


def test_csconsole_filter_separates_wxcc_and_wxcce():
    """CSConsole technology filter should keep only rows for selected technology."""
    from adoptiq_backend import _filter_csconsole_data_by_technology

    df = pd.DataFrame([
        {
            "BU_NAME": "CustA",
            "SUB_TECHNOLOGY_C": "Webex Contact Center",
            "TECHNOLOGY_C": "Contact Center Software",
            "SUBJECT_C": "wxcc onboarding",
        },
        {
            "BU_NAME": "CustA",
            "SUB_TECHNOLOGY_C": "Webex CCE / Webex Contact Center Enterprise",
            "TECHNOLOGY_C": "Contact Center Software",
            "SUBJECT_C": "enterprise queue issue",
        },
        {
            "BU_NAME": "CustB",
            "SUB_TECHNOLOGY_C": "Webex Contact Center Enterprise",
            "TECHNOLOGY_C": "Contact Center Software",
            "SUBJECT_C": "wxcce routing issue",
        },
    ])

    wxcc = _filter_csconsole_data_by_technology(df, "Webex Contact Center")
    assert len(wxcc) == 1
    assert str(wxcc.iloc[0]["SUB_TECHNOLOGY_C"]) == "Webex Contact Center"

    wxcce = _filter_csconsole_data_by_technology(df, "Webex Contact Center Enterprise")
    assert len(wxcce) == 2
    print("  [OK] csconsole separates wxcc and wxcce")


def test_customer_activity_includes_csconsole_only_data():
    """Customer should be analyzed when only CSConsole data exists."""
    from app_simple import _has_customer_activity_for_deep_dive

    empty = pd.DataFrame()
    csconsole_only = pd.DataFrame([{"ACCOUNT_ID_C": "001234", "Status": "Open"}])

    assert _has_customer_activity_for_deep_dive(
        empty,
        empty,
        empty,
        empty,
        empty,
        csconsole_only,
    ) is True

    assert _has_customer_activity_for_deep_dive(
        empty,
        empty,
        empty,
        empty,
        empty,
        empty,
    ) is False
    print("  [OK] customer activity includes csconsole-only data")


def test_csconsole_ab_fallback_fields_render_in_briefing():
    """Briefing should render CSConsole ABs when alternate column names are used."""
    from adoptiq_backend import _create_briefing_book

    csconsole_data = {
        "action_plans": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "success_priorities": pd.DataFrame(),
        "adoption_barriers": pd.DataFrame(
            [
                {
                    "Record ID": "AB-ALT-001",
                    "Barrier Title": "Adapter parity gap",
                    "Description": "Legacy adapter behavior diverges from WxCC flow.",
                    "Status": "Open",
                    "Severity": "High",
                    "Customer Name": "Example Customer",
                    "Owner Email": "owner@example.com",
                }
            ]
        ),
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
    print("  [OK] csconsole AB fallback fields render in briefing")


def run_all():
    """Run all tests."""
    print("\n=== AdoptIQ Report Extensive Testing ===\n")
    tests = [
        test_report_utils,
        test_simple_renewal_risk_calculation,
        test_renewal_report_single_customer,
        test_renewal_report_portfolio,
        test_renewal_report_no_data,
        test_compact_report_formatter,
        test_compact_report_empty_data,
        test_risk_scores_edge_cases,
        test_filter_subscriptions_by_criteria,
        test_validate_customer_name_input,
        test_advanced_renewal_analyzer_mock,
        test_leader_report_generator_structure,
        test_executive_intelligence_formatter,
        test_history_route_and_analyses_mapping,
        test_learned_insights_store_and_retrieve,
        test_status_persistence_datetime_fields,
        test_build_insights_payload_helper,
        test_wxcc_excludes_wxcce_signatures,
        test_csconsole_filter_separates_wxcc_and_wxcce,
        test_customer_activity_includes_csconsole_only_data,
        test_csconsole_ab_fallback_fields_render_in_briefing,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
    print(f"\n=== Results: {passed} passed, {failed} failed ===\n")
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
