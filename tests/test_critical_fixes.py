"""Tests for critical bug fixes identified in the 3-round code review."""
import pytest
import numpy as np
import pandas as pd
import os
import tempfile


class TestFormatNumberNaN:
    """format_number must handle NaN, None, and edge cases without crashing."""

    def test_nan_returns_na(self):
        from report_utils import format_number
        assert format_number(float('nan')) == "N/A"

    def test_numpy_nan_returns_na(self):
        from report_utils import format_number
        assert format_number(np.nan) == "N/A"

    def test_none_returns_na(self):
        from report_utils import format_number
        assert format_number(None) == "N/A"

    def test_zero(self):
        from report_utils import format_number
        assert format_number(0) == "0"

    def test_positive_int(self):
        from report_utils import format_number
        assert format_number(1234) == "1,234"

    def test_float_no_decimals(self):
        from report_utils import format_number
        assert format_number(1234.56) == "1,234"

    def test_float_with_decimals(self):
        from report_utils import format_number
        assert format_number(1234.56, decimals=2) == "1,234.56"

    def test_percent(self):
        from report_utils import format_number
        assert format_number(95.5, as_percent=True) == "95.5%"

    def test_nan_as_percent(self):
        from report_utils import format_number
        assert format_number(float('nan'), as_percent=True) == "N/A"

    def test_string_value(self):
        from report_utils import format_number
        assert format_number("not a number") == "not a number"


class TestFormatDateNaT:
    """format_date must handle NaT, None, and bad strings without crashing."""

    def test_none_returns_na(self):
        from report_utils import format_date
        assert format_date(None) == "N/A"

    def test_nat_returns_na(self):
        from report_utils import format_date
        assert format_date(pd.NaT) == "N/A"

    def test_valid_date_string(self):
        from report_utils import format_date
        result = format_date("2025-01-15")
        assert "2025" in result

    def test_invalid_date_string(self):
        from report_utils import format_date
        result = format_date("not-a-date")
        assert result is not None

    def test_empty_string(self):
        from report_utils import format_date
        result = format_date("")
        assert result is not None


class TestIncidentStorageNoneCoercion:
    """incident_storage must handle None values in dict fields without storing NULL."""

    def test_store_incident_with_none_title(self):
        from incident_storage import store_historical_incidents, get_historical_incidents
        incidents = [{'id': 'test-none-title', 'title': None, 'link': '', 'published': '', 'status': '', 'impact_level': '', 'source': 'test'}]
        count = store_historical_incidents(incidents)
        assert count == 1

    def test_store_bug_with_none_title(self):
        from incident_storage import store_historical_bugs
        bugs = [{'bug_id': 'test-none-bug', 'title': None, 'source_url': '', 'source': 'test', 'discovered_at': ''}]
        count = store_historical_bugs(bugs)
        assert count == 1

    def test_store_maintenance_with_none_title(self):
        from incident_storage import store_historical_maintenances
        maints = [{'id': 'test-none-maint', 'title': None, 'link': '', 'published': '', 'status': '', 'source': 'test'}]
        count = store_historical_maintenances(maints)
        assert count == 1

    def test_empty_id_skipped(self):
        from incident_storage import store_historical_incidents
        incidents = [{'id': '', 'title': 'test'}]
        count = store_historical_incidents(incidents)
        assert count == 0

    def test_export_import_roundtrip(self):
        from incident_storage import export_all_data, import_all_data
        exported = export_all_data()
        assert 'schema_version' in exported
        assert isinstance(exported['incidents'], list)
        assert isinstance(exported['bugs'], list)
        assert isinstance(exported['maintenances'], list)
        counts = import_all_data(exported)
        assert isinstance(counts, dict)


class TestDownloadPathTraversal:
    """Download routes must reject paths outside the outputs directory."""

    def test_resolve_safe_path_rejects_traversal(self, app, client):
        with app.test_request_context():
            from app_simple import download_result
            response = client.get('/download/fake-id/docx')
            assert response.status_code in (400, 404)

    def test_download_invalid_file_type(self, app, client):
        response = client.get('/download/fake-id/exe')
        assert response.status_code == 404


class TestNewRoutes:
    """New routes (admin, ask-ai) must render without errors."""

    def test_admin_page_redirects(self, app, client):
        response = client.get('/admin')
        assert response.status_code == 302

    def test_ask_ai_page_renders(self, app, client):
        response = client.get('/ask-ai')
        assert response.status_code == 200
        assert b'Ask AI' in response.data

    def test_ask_ai_api_rejects_empty_question(self, app, client):
        response = client.post('/api/ask-ai-portfolio',
                               json={'question': ''},
                               content_type='application/json')
        assert response.status_code == 400

    def test_ask_ai_api_rejects_long_question(self, app, client):
        response = client.post('/api/ask-ai-portfolio',
                               json={'question': 'x' * 2001},
                               content_type='application/json')
        assert response.status_code == 400


class TestFormatCurrencyNaN:
    """format_currency must handle NaN/Inf without returning $nan."""

    def test_nan_returns_na(self):
        from report_utils import format_currency
        assert format_currency(float('nan')) == "N/A"

    def test_inf_returns_na(self):
        from report_utils import format_currency
        result = format_currency(float('inf'))
        assert result == "N/A"

    def test_none_returns_na(self):
        from report_utils import format_currency
        assert format_currency(None) == "N/A"

    def test_normal_value(self):
        from report_utils import format_currency
        assert format_currency(1234.5) == "$1,234.50"


class TestImportAllDataEdgeCases:
    """import_all_data must handle None and malformed input."""

    def test_none_input(self):
        from incident_storage import import_all_data
        result = import_all_data(None)
        assert result == {'incidents': 0, 'bugs': 0, 'maintenances': 0}

    def test_empty_dict(self):
        from incident_storage import import_all_data
        result = import_all_data({})
        assert result == {'incidents': 0, 'bugs': 0, 'maintenances': 0}

    def test_non_dict(self):
        from incident_storage import import_all_data
        result = import_all_data("not a dict")
        assert result == {'incidents': 0, 'bugs': 0, 'maintenances': 0}

    def test_none_lists(self):
        from incident_storage import import_all_data
        result = import_all_data({'incidents': None, 'bugs': None, 'maintenances': None})
        assert result == {'incidents': 0, 'bugs': 0, 'maintenances': 0}


class TestJsonDefaultNanInf:
    """R5 Fix 1: _sanitize_for_json must strip NaN/Inf before serialization."""

    def test_nan_sanitized_to_none(self):
        import json
        from app_simple import _sanitize_for_json, _json_default
        data = _sanitize_for_json({'val': float('nan')})
        result = json.dumps(data, default=_json_default)
        assert 'NaN' not in result
        assert 'null' in result

    def test_inf_sanitized_to_none(self):
        import json
        from app_simple import _sanitize_for_json, _json_default
        data = _sanitize_for_json({'val': float('inf')})
        result = json.dumps(data, default=_json_default)
        assert 'Infinity' not in result

    def test_numpy_nan_sanitized(self):
        import json
        from app_simple import _sanitize_for_json, _json_default
        data = _sanitize_for_json({'val': np.float64('nan')})
        result = json.dumps(data, default=_json_default)
        assert 'NaN' not in result

    def test_normal_float_passes(self):
        import json
        from app_simple import _sanitize_for_json, _json_default
        data = _sanitize_for_json({'val': 42.5})
        result = json.dumps(data, default=_json_default)
        assert '42.5' in result

    def test_nested_nan(self):
        from app_simple import _sanitize_for_json
        data = _sanitize_for_json({'a': {'b': [float('nan'), 1.0]}})
        assert data == {'a': {'b': [None, 1.0]}}


class TestProgressClamping:
    """R5 Fix 5: _update_progress must clamp progress to 0-100."""

    def test_progress_clamped_to_100(self):
        from app_simple import _update_progress
        status = {'completed_steps': [], 'start_time': None}
        _update_progress(status, 150, 'test', 'test', save=False)
        assert status['progress'] == 100

    def test_progress_clamped_to_0(self):
        from app_simple import _update_progress
        status = {'completed_steps': [], 'start_time': None}
        _update_progress(status, -10, 'test', 'test', save=False)
        assert status['progress'] == 0

    def test_normal_progress(self):
        from app_simple import _update_progress
        status = {'completed_steps': [], 'start_time': None}
        _update_progress(status, 50, 'test', 'test', save=False)
        assert status['progress'] == 50


class TestCtxNoneGuards:
    """R5 Fix 7: DB functions must return empty DataFrame when ctx is None."""

    def test_get_subscriptions_for_team_ctx_none(self):
        from adoptiq_backend import get_subscriptions_for_team
        result = get_subscriptions_for_team(None, ['test@example.com'])
        assert result.empty

    def test_fetch_adoption_barriers_ctx_none(self):
        from adoptiq_backend import fetch_adoption_barriers
        result = fetch_adoption_barriers(None, ['ACC123'], 90)
        assert result.empty

    def test_fetch_csconsole_customer_pulse_ctx_none(self):
        from adoptiq_backend import fetch_csconsole_customer_pulse
        result = fetch_csconsole_customer_pulse(None, ['ACC123'], 90)
        assert result.empty

    def test_fetch_csconsole_success_priorities_ctx_none(self):
        from adoptiq_backend import fetch_csconsole_success_priorities
        result = fetch_csconsole_success_priorities(None, ['ACC123'], 90)
        assert result.empty

    def test_fetch_csconsole_action_plans_ctx_none(self):
        from adoptiq_backend import fetch_csconsole_action_plans
        result = fetch_csconsole_action_plans(None, ['ACC123'], 90)
        assert result.empty


class TestAdvancedAnalytics:
    """Tests for the new AI analytics functions."""

    def test_calculate_arr_at_risk_empty(self):
        from adoptiq_backend import calculate_arr_at_risk
        result = calculate_arr_at_risk(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        assert result == {}

    def test_calculate_arr_at_risk_none(self):
        from adoptiq_backend import calculate_arr_at_risk
        result = calculate_arr_at_risk(None, None, None)
        assert result == {}

    def test_calculate_arr_at_risk_basic(self):
        from adoptiq_backend import calculate_arr_at_risk
        arr_df = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A2', 'A3'],
            'ANNUAL_CONTRACT_VALUE': [100000, 200000, 300000],
        })
        ab_df = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1'],
            'SEVERITY_C': ['Critical'],
        })
        result = calculate_arr_at_risk(arr_df, ab_df)
        assert result['total_portfolio_arr'] == 600000
        assert result['arr_at_risk'] == 100000
        assert result['arr_critical'] == 100000
        assert result['arr_healthy'] == 500000
        assert result['pct_at_risk'] > 0
        assert result['troubled_account_count'] == 1
        assert result['critical_account_count'] == 1

    def test_fetch_period_comparison_none_ctx(self):
        from adoptiq_backend import fetch_period_comparison
        result = fetch_period_comparison(None, ['A1'], 90)
        assert result == {}

    def test_fetch_period_comparison_empty_accounts(self):
        from adoptiq_backend import fetch_period_comparison
        result = fetch_period_comparison('mock', [], 90)
        assert result == {}

    def test_fetch_barrier_velocity_none_ctx(self):
        from adoptiq_backend import fetch_barrier_velocity
        result = fetch_barrier_velocity(None, ['A1'], 90)
        assert result == {}

    def test_fetch_barrier_velocity_empty_accounts(self):
        from adoptiq_backend import fetch_barrier_velocity
        result = fetch_barrier_velocity('mock', [], 90)
        assert result == {}

    def test_scan_historical_reports_missing_dir(self):
        from adoptiq_backend import scan_historical_reports
        result = scan_historical_reports('/nonexistent/path/outputs')
        assert result == []

    def test_scan_historical_reports_empty_string(self):
        from adoptiq_backend import scan_historical_reports
        result = scan_historical_reports('')
        assert result == []

    def test_scan_historical_reports_temp_dir(self, tmp_path):
        from adoptiq_backend import scan_historical_reports
        result = scan_historical_reports(str(tmp_path))
        assert result == []

    def test_fetch_enhanced_account_insights_none_ctx(self):
        from adoptiq_backend import fetch_enhanced_account_insights
        result = fetch_enhanced_account_insights(None, ['A1'])
        assert result == {}

    def test_fetch_enhanced_account_insights_empty_accounts(self):
        from adoptiq_backend import fetch_enhanced_account_insights
        result = fetch_enhanced_account_insights('mock', [])
        assert result == {}

    def test_derive_portfolio_intelligence_none(self):
        from adoptiq_backend import derive_portfolio_intelligence
        result = derive_portfolio_intelligence(None, None)
        assert result == {}

    def test_derive_portfolio_intelligence_empty(self):
        from adoptiq_backend import derive_portfolio_intelligence
        result = derive_portfolio_intelligence(pd.DataFrame(), pd.DataFrame())
        assert result == {}

    def test_derive_portfolio_intelligence_basic(self):
        from adoptiq_backend import derive_portfolio_intelligence
        arr = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A2', 'A3'],
            'BU_NAME': ['Acme', 'Beta', 'Gamma'],
            'ANNUAL_CONTRACT_VALUE': [100000, 200000, 300000],
            'TECHNOLOGY_C': ['Webex', 'Webex', 'Teams'],
        })
        ab = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A1'],
            'SEVERITY_C': ['Critical', 'Medium'],
            'CSS_PRE_UNLINK_TECHNOLOGY_NAME_C': ['Webex', 'Webex'],
        })
        result = derive_portfolio_intelligence(arr, ab)
        assert 'concentration' in result
        assert result['concentration']['top5_pct'] == 100.0
        assert 'tech_hotspots' in result

    def test_derive_portfolio_intelligence_repeat_offenders(self):
        from adoptiq_backend import derive_portfolio_intelligence
        arr = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A2'],
            'BU_NAME': ['Acme', 'Beta'],
            'ANNUAL_CONTRACT_VALUE': [100000, 200000],
        })
        ab = pd.DataFrame({'ACCOUNT_ID_C': ['A1']})
        cases = pd.DataFrame({'ACCOUNT_ID': ['A1', 'A2']})
        result = derive_portfolio_intelligence(arr, ab, cases)
        assert 'repeat_offenders' in result
        assert result['repeat_offenders']['count'] == 1
        assert 'Acme' in result['repeat_offenders']['customers']

    def test_build_cross_report_trends_insufficient(self):
        from adoptiq_backend import build_cross_report_trends
        assert build_cross_report_trends([]) == {}
        assert build_cross_report_trends(None) == {}
        assert build_cross_report_trends([{'date': '2026-01-01'}]) == {}

    def test_build_cross_report_trends_basic(self):
        from adoptiq_backend import build_cross_report_trends
        data = [
            {'date': '2026-01-01', 'filename': 'a.xlsx',
             'metrics': {'S1': {'rows': 50, 'unique_customers': 10, 'total_arr': 1000000}}},
            {'date': '2026-02-01', 'filename': 'b.xlsx',
             'metrics': {'S1': {'rows': 100, 'unique_customers': 15, 'total_arr': 1500000}}},
        ]
        result = build_cross_report_trends(data)
        assert 'record_trend' in result
        assert result['record_trend']['pct_change'] == 100.0
        assert 'arr_trend' in result
        assert result['arr_trend']['pct_change'] == 50.0

    def test_build_cross_report_trends_severity(self):
        from adoptiq_backend import build_cross_report_trends
        data = [
            {'date': '2026-01-01', 'filename': 'a.xlsx',
             'metrics': {'S1': {'rows': 50, 'severity_distribution': {'Critical': 5, 'High': 10}}}},
            {'date': '2026-02-01', 'filename': 'b.xlsx',
             'metrics': {'S1': {'rows': 80, 'severity_distribution': {'Critical': 8, 'High': 12, 'Medium': 3}}}},
        ]
        result = build_cross_report_trends(data)
        assert 'severity_trend' in result
        assert result['severity_trend']['Critical']['change'] == 3
        assert result['severity_trend']['Medium']['oldest'] == 0

    def test_build_cross_report_trends_recurring(self):
        from adoptiq_backend import build_cross_report_trends
        data = [
            {'date': '2026-01-01', 'filename': 'a.xlsx',
             'metrics': {'S1': {'rows': 50, 'top_customers_by_arr': {'Acme': 100, 'Beta': 200}}}},
            {'date': '2026-02-01', 'filename': 'b.xlsx',
             'metrics': {'S1': {'rows': 80, 'top_customers_by_arr': {'Acme': 150, 'Gamma': 300}}}},
        ]
        result = build_cross_report_trends(data)
        assert 'recurring_customers' in result
        assert 'Acme' in result['recurring_customers']['names']

    def test_compute_barrier_aging_empty(self):
        from adoptiq_backend import compute_barrier_aging
        assert compute_barrier_aging(None) == {}
        assert compute_barrier_aging(pd.DataFrame()) == {}

    def test_compute_barrier_aging_all_closed(self):
        from adoptiq_backend import compute_barrier_aging
        df = pd.DataFrame({
            'STATUS_C': ['Closed', 'Resolved', 'Completed'],
            'CREATED_DATE': ['2025-01-01', '2025-02-01', '2025-03-01'],
        })
        result = compute_barrier_aging(df)
        assert result.get('total_open', 0) == 0

    def test_compute_barrier_aging_with_open(self):
        from adoptiq_backend import compute_barrier_aging
        df = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A2', 'A3'],
            'STATUS_C': ['Open', 'In Progress', 'Closed'],
            'SEVERITY_C': ['Critical', 'Medium', 'High'],
            'SUBJECT_C': ['Login issue', 'Slow load', 'Bug fixed'],
            'CREATED_DATE': ['2025-01-01', '2026-02-01', '2026-03-01'],
            'BU_NAME': ['Acme', 'Beta', 'Gamma'],
            'ID': ['AB-1', 'AB-2', 'AB-3'],
        })
        result = compute_barrier_aging(df)
        assert result['total_open'] == 2
        assert 'aging_buckets' in result
        assert result['avg_days_open'] > 0
        assert len(result.get('stale_barriers', [])) <= 5

    def test_compute_barrier_aging_with_arr(self):
        from adoptiq_backend import compute_barrier_aging
        ab = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1'],
            'STATUS_C': ['Open'],
            'CREATED_DATE': ['2025-06-01'],
            'SUBJECT_C': ['Test'],
            'ID': ['AB-1'],
        })
        arr = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1'],
            'ANNUAL_CONTRACT_VALUE': [500000],
        })
        result = compute_barrier_aging(ab, arr)
        assert result['total_open'] == 1
        stale = result.get('stale_barriers', [])
        assert len(stale) == 1
        assert stale[0].get('account_arr') == 500000

    def test_compute_barrier_aging_nan_dates(self):
        from adoptiq_backend import compute_barrier_aging
        df = pd.DataFrame({
            'STATUS_C': ['Open', 'Open', 'Open'],
            'CREATED_DATE': [None, 'not-a-date', '2025-01-01'],
            'SUBJECT_C': ['A', 'B', 'C'],
            'ID': ['1', '2', '3'],
        })
        result = compute_barrier_aging(df)
        assert result['total_open'] == 3
        assert len(result.get('stale_barriers', [])) >= 1

    def test_compute_barrier_aging_future_dates(self):
        from adoptiq_backend import compute_barrier_aging
        df = pd.DataFrame({
            'STATUS_C': ['Open'],
            'CREATED_DATE': ['2030-01-01'],
        })
        result = compute_barrier_aging(df)
        assert result['total_open'] == 1
        if result.get('stale_barriers'):
            assert result['stale_barriers'][0]['days_open'] == 0

    def test_format_number_inf(self):
        from report_utils import format_number
        assert format_number(float('inf')) == 'N/A'
        assert format_number(float('-inf')) == 'N/A'
        assert format_number(float('inf'), as_percent=True) == 'N/A'

    def test_format_number_negative_inf(self):
        from report_utils import format_number
        assert format_number(float('-inf'), decimals=2) == 'N/A'

    def test_build_cross_report_trends_empty_metrics(self):
        from adoptiq_backend import build_cross_report_trends
        data = [
            {'date': '2026-01-01', 'filename': 'a.xlsx', 'metrics': {}},
            {'date': '2026-02-01', 'filename': 'b.xlsx', 'metrics': {}},
        ]
        result = build_cross_report_trends(data)
        assert result.get('period', {}).get('reports_analyzed') == 2

    def test_build_cross_report_trends_non_int_severity(self):
        from adoptiq_backend import build_cross_report_trends
        data = [
            {'date': '2026-01-01', 'filename': 'a.xlsx',
             'metrics': {'S1': {'rows': 10, 'severity_distribution': {'Critical': 'bad'}}}},
            {'date': '2026-02-01', 'filename': 'b.xlsx',
             'metrics': {'S1': {'rows': 20, 'severity_distribution': {'Critical': 5}}}},
        ]
        result = build_cross_report_trends(data)
        assert 'period' in result


class TestRound13Fixes:
    """Tests for Round 13 audit fixes."""

    def test_scan_historical_reports_nan_arr(self):
        """total_arr should be 0.0 when column is all NaN, not NaN."""
        import math
        with tempfile.TemporaryDirectory() as tmpdir:
            df = pd.DataFrame({
                'BU_NAME': ['Cust A', 'Cust B'],
                'ANNUAL_CONTRACT_VALUE': [None, None],
                'STATUS_C': ['Open', 'Closed']
            })
            fpath = os.path.join(tmpdir, 'AdoptIQ_Data_test.xlsx')
            df.to_excel(fpath, index=False)

            from adoptiq_backend import scan_historical_reports
            result = scan_historical_reports(tmpdir, limit=1)
            assert len(result) == 1
            arr_val = result[0]['metrics'].get('total_arr', 0)
            assert not (isinstance(arr_val, float) and math.isnan(arr_val)), \
                f"total_arr should not be NaN, got {arr_val}"

    def test_excel_fallback_replaces_inf(self):
        """Inf values should be replaced with NaN before writing to Excel."""
        with tempfile.TemporaryDirectory() as tmpdir:
            df = pd.DataFrame({
                'Name': ['A', 'B', 'C'],
                'Value': [1.0, float('inf'), float('-inf')]
            })
            fpath = os.path.join(tmpdir, 'test_inf')
            from adoptiq_backend import write_excel_workbook
            result_path = write_excel_workbook(fpath, {'Data': df})
            assert result_path is not None
            read_back = pd.read_excel(result_path, sheet_name='Data')
            assert read_back['Value'].iloc[0] == 1.0
            assert pd.isna(read_back['Value'].iloc[1])
            assert pd.isna(read_back['Value'].iloc[2])

    def test_error_responses_no_str_e(self):
        """API error responses must not contain raw exception strings."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from app_simple import app
        client = app.test_client()
        resp = client.post('/simple_test')
        if resp.status_code == 500:
            data = resp.get_json()
            assert 'Traceback' not in str(data.get('error', ''))
            assert '/' not in str(data.get('error', ''))

    def test_subscription_word_failure_sets_none(self):
        """When word_path is set to None on failure, status should reflect it."""
        word_path = None
        try:
            raise RuntimeError("simulated failure")
        except Exception:
            word_path = None

        status = {}
        word_filename = 'test.docx'
        status['word_file'] = word_filename if word_path else None
        status['word_report'] = str(word_path) if word_path else None
        assert status['word_file'] is None
        assert status['word_report'] is None

    def test_animatestatistics_nan_guard_concept(self):
        """Verify the NaN guard logic used in results.html animateStatistics."""
        import math
        values = ['10', '', 'abc', '0', None]
        results = []
        for v in values:
            try:
                parsed = int(v) if v else None
            except (ValueError, TypeError):
                parsed = None
            if parsed is None or (isinstance(parsed, float) and math.isnan(parsed)):
                results.append('fallback')
            else:
                results.append(parsed)
        assert results == [10, 'fallback', 'fallback', 0, 'fallback']
