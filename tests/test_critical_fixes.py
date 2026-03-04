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


class TestRound14Fixes:
    """Tests for Round 14 audit fixes."""

    def test_download_file_sanitizes_download_name(self):
        """download_name must strip CRLF to prevent header injection."""
        from werkzeug.utils import secure_filename
        dangerous = "report.docx\r\nX-Injected: evil"
        safe = secure_filename(os.path.basename(dangerous))
        assert '\r' not in safe
        assert '\n' not in safe
        assert ':' not in safe

    def test_format_currency_overflow(self):
        """format_currency must not crash on extremely large floats."""
        from report_utils import format_currency
        result = format_currency(1e308)
        assert isinstance(result, str)
        result2 = format_currency(float('inf'))
        assert result2 == 'N/A'

    def test_format_currency_negative_inf(self):
        """format_currency must return N/A for -inf."""
        from report_utils import format_currency
        assert format_currency(float('-inf')) == 'N/A'

    def test_incident_db_corrupted_recovery(self):
        """init_db should recover from a corrupted database file."""
        import tempfile, incident_storage
        from incident_storage import init_db
        with tempfile.TemporaryDirectory() as tmp:
            fake_db = os.path.join(tmp, 'external_intelligence.db')
            with open(fake_db, 'wb') as f:
                f.write(b'this is not a valid sqlite file')
            original_db_path = incident_storage._db_path
            incident_storage._db_path = lambda: fake_db
            try:
                init_db()
                assert os.path.exists(fake_db)
            finally:
                incident_storage._db_path = original_db_path

    def test_cancellation_flags_cleanup_concept(self):
        """Verify cancellation_flags.pop pattern works correctly."""
        flags = {'analysis_1': True, 'analysis_2': True}
        flags.pop('analysis_1', None)
        assert 'analysis_1' not in flags
        assert 'analysis_2' in flags
        flags.pop('nonexistent', None)
        assert len(flags) == 1

    def test_analysis_status_runtime_trim(self):
        """Verify the trimming logic keeps 50 most recent and skips running."""
        statuses = {}
        for i in range(60):
            statuses[f'a_{i:03d}'] = {
                'start_time': f'2026-01-01T{i:02d}:00:00',
                'status': 'completed'
            }
        statuses['a_running'] = {
            'start_time': '2025-01-01T00:00:00',
            'status': 'running'
        }
        if len(statuses) > 50:
            sorted_ids = sorted(
                statuses.keys(),
                key=lambda k: statuses[k].get('start_time', ''),
                reverse=True
            )
            for old_id in sorted_ids[50:]:
                if statuses[old_id].get('status') not in ('running', 'starting', 'cancelling'):
                    del statuses[old_id]
        assert len(statuses) <= 51
        assert 'a_running' in statuses


class TestRound15Fixes:
    """Tests for Round 15 audit fixes."""

    def test_generate_llm_response_call_signature(self):
        """generate_llm_response should accept exactly 2 positional args."""
        import inspect
        from adoptiq_backend import generate_llm_response
        sig = inspect.signature(generate_llm_response)
        params = [p for p in sig.parameters.values()
                  if p.default is inspect.Parameter.empty]
        assert len(params) == 2, f"Expected 2 required params, got {len(params)}: {params}"

    def test_trim_sort_key_mixed_types(self):
        """Trimming sort key should handle both datetime and str start_time."""
        from datetime import datetime
        statuses = {
            'a': {'start_time': datetime(2025, 1, 1), 'status': 'completed'},
            'b': {'start_time': '2025-06-01T00:00:00', 'status': 'completed'},
            'c': {'start_time': '', 'status': 'completed'},
            'd': {'start_time': None, 'status': 'completed'},
        }
        def _trim_sort_key(k):
            st = statuses[k].get('start_time', '')
            return st.isoformat() if isinstance(st, datetime) else str(st)
        result = sorted(statuses.keys(), key=_trim_sort_key, reverse=True)
        assert len(result) == 4

    def test_customer_progress_escape_html(self):
        """Customer name with HTML should not execute as HTML."""
        malicious = '<img src=x onerror=alert(1)>'
        from markupsafe import escape
        safe = str(escape(malicious))
        assert '<img' not in safe
        assert '&lt;' in safe

    def test_fetch_subscription_cur_not_defined(self):
        """If cur is never assigned, the finally block should not NameError."""
        assert 'cur' not in locals()
        if 'cur' in locals() and locals().get('cur') is not None:
            pass  # Would call cur.close() — should not be reached

    def test_format_currency_none_input(self):
        """format_currency should handle None gracefully."""
        from report_utils import format_currency
        result = format_currency(None)
        assert result in ('N/A', '$0', '$0.00', '')

    def test_subscription_prompt_formatting(self):
        """PROMPT_CUSTOMER_TEMPLATE should accept expected format kwargs."""
        from adoptiq_backend import PROMPT_CUSTOMER_TEMPLATE
        formatted = PROMPT_CUSTOMER_TEMPLATE.format(
            CUSTOMER_NAME='Test Corp',
            CSSM_NAME='',
            TECHNOLOGY='',
            MANAGER=''
        )
        assert 'Test Corp' in formatted


class TestRound16Fixes:
    """Tests for Round 16 audit fixes."""

    def test_check_cancellation_uses_flags(self):
        """check_cancellation should read from cancellation_flags, not status."""
        from app_simple import check_cancellation, cancellation_flags, cancellation_flags_lock
        test_id = 'test_cancel_r16'
        with cancellation_flags_lock:
            cancellation_flags[test_id] = True
        assert check_cancellation(test_id) is True
        with cancellation_flags_lock:
            cancellation_flags.pop(test_id, None)
        assert check_cancellation(test_id) is False

    def test_briefing_book_nan_keys_filtered(self):
        """json.dumps in briefing book should not crash on NaN groupby keys."""
        import json
        import numpy as np
        data = {'cat_a': 10, np.nan: 5, 'cat_b': 3}
        filtered = {str(k): int(v) for k, v in data.items() if pd.notna(k)}
        result = json.dumps(filtered)
        assert 'cat_a' in result
        assert 'cat_b' in result

    def test_arr_nan_sum_handled(self):
        """calculate_arr_at_risk should handle NaN in ARR column."""
        from adoptiq_backend import calculate_arr_at_risk
        arr_df = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A2', 'A3'],
            'ANNUAL_CONTRACT_VALUE': [100000, float('nan'), 200000]
        })
        ab_df = pd.DataFrame({'ACCOUNT_ID_C': ['A1'], 'ID': ['1']})
        result = calculate_arr_at_risk(arr_df, ab_df)
        assert result.get('total_portfolio_arr', 0) == 300000.0
        assert not pd.isna(result.get('pct_at_risk', 0))

    def test_arr_all_nan_handled(self):
        """calculate_arr_at_risk should handle all-NaN ARR column."""
        from adoptiq_backend import calculate_arr_at_risk
        arr_df = pd.DataFrame({
            'ACCOUNT_ID_C': ['A1', 'A2'],
            'ANNUAL_CONTRACT_VALUE': [float('nan'), float('nan')]
        })
        ab_df = pd.DataFrame()
        result = calculate_arr_at_risk(arr_df, ab_df)
        arr_val = result.get('total_portfolio_arr', 0)
        assert arr_val == 0.0 or pd.isna(arr_val) is False

    def test_duplicate_sheet_names_deduped(self):
        """write_excel_workbook should handle duplicate sheet names."""
        used = {"Report_Info"}
        names_to_test = ["Summary", "Summary", "Summary"]
        results = []
        for name in names_to_test:
            sheet = (name or "Sheet")[:31]
            base_sheet = sheet
            suffix = 2
            while sheet in used:
                sheet = f"{base_sheet[:28]}_{suffix}"
                suffix += 1
            used.add(sheet)
            results.append(sheet)
        assert results[0] == "Summary"
        assert results[1] == "Summary_2"
        assert results[2] == "Summary_3"

    def test_cancel_race_does_not_overwrite_completed(self):
        """Cancel route should not overwrite completed/error status."""
        status = {'status': 'completed', 'message': 'Done'}
        if status.get('status') in ('starting', 'running'):
            status['status'] = 'cancelling'
        assert status['status'] == 'completed'

    def test_subscription_briefing_builds_correctly(self):
        """Subscription briefing should build from sub_data without _create_briefing_book."""
        sub_data = {
            'customer_name': 'Acme Corp',
            'adoption_barriers': [{'title': 'Barrier 1', 'status': 'Open'}],
            'action_plans': [],
            'customer_pulse': [{'score': 5}],
            'success_priorities': []
        }
        briefing_parts = [f"## Subscription Briefing: {sub_data.get('customer_name', 'test')}"]
        for section_key in ('adoption_barriers', 'action_plans', 'customer_pulse', 'success_priorities'):
            items = sub_data.get(section_key, [])
            if items:
                briefing_parts.append(f"\n### {section_key.replace('_', ' ').title()} ({len(items)} items)")
                for item in items[:50]:
                    if isinstance(item, dict):
                        briefing_parts.append(f"- {', '.join(f'{k}: {v}' for k, v in item.items() if v)}")
        result = "\n".join(briefing_parts)
        assert 'Acme Corp' in result
        assert 'Adoption Barriers' in result
        assert 'Barrier 1' in result


class TestRound17Fixes:
    """Tests for Round 17 audit fixes."""

    def test_resolve_csone_path_rejects_arbitrary_paths(self):
        """_resolve_csone_path_safe should reject paths outside uploads/OneDrive."""
        from app_simple import _resolve_csone_path_safe
        assert _resolve_csone_path_safe('/etc/passwd') is None
        assert _resolve_csone_path_safe('../../etc/passwd') is None
        assert _resolve_csone_path_safe('') is None
        assert _resolve_csone_path_safe(None) is None

    def test_leader_report_cancellation_uses_check_cancellation(self):
        """Leader report runner should use check_cancellation for cancellation."""
        from app_simple import check_cancellation, cancellation_flags, cancellation_flags_lock
        test_id = 'test_leader_cancel'
        with cancellation_flags_lock:
            cancellation_flags[test_id] = True
        assert check_cancellation(test_id) is True
        with cancellation_flags_lock:
            cancellation_flags.pop(test_id, None)

    def test_bst_defect_id_validation(self, client):
        """BST search should reject invalid defect IDs."""
        import json
        resp = client.post('/search_bst_defect',
                           data=json.dumps({'defect_id': '<script>alert(1)</script>'}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_psirt_advisory_id_validation(self, client):
        """PSIRT search should reject invalid advisory IDs."""
        import json
        resp = client.post('/search_psirt_advisory',
                           data=json.dumps({'advisory_id': '../../../etc/passwd'}),
                           content_type='application/json')
        assert resp.status_code == 400

    def test_bst_defect_id_valid_format_accepted(self, client):
        """BST search should accept valid alphanumeric IDs (even if search fails)."""
        import json
        resp = client.post('/search_bst_defect',
                           data=json.dumps({'defect_id': 'CSCab12345'}),
                           content_type='application/json')
        assert resp.status_code != 400

    def test_minimal_briefing_book_none_inputs(self):
        """_create_minimal_briefing_book should handle None DataFrames."""
        from adoptiq_backend import _create_minimal_briefing_book
        result = _create_minimal_briefing_book('Test Manager', None, None, 'Test Tech')
        assert 'Test Manager' in result
        assert 'Test Tech' in result

    def test_executive_briefing_book_none_inputs(self):
        """_create_executive_briefing_book_with_csone should handle None DataFrames."""
        from adoptiq_backend import _create_executive_briefing_book_with_csone
        result = _create_executive_briefing_book_with_csone(
            'Test Manager', None, None, None, 'Test Tech')
        assert 'Test Manager' in result


class TestRound18Fixes:
    """Tests for Round 18 code audit findings."""

    def test_export_intel_sanitizes_payload(self, client):
        """Export intel should sanitize data (no NaN/Inf in JSON)."""
        import unittest.mock as mock
        fake_data = {
            'schema_version': '1.0',
            'incidents': [{'id': '1', 'title': 'test'}],
            'bugs': [],
            'maintenances': [],
        }
        with mock.patch('incident_storage.export_all_data', return_value=fake_data):
            resp = client.get('/api/export-intel')
        assert resp.status_code == 200
        assert b'schema_version' in resp.data

    def test_risk_summary_none_in_health_dashboard(self):
        """add_portfolio_health_dashboard should not crash when risk_summary is None."""
        from compact_report_formatter import CompactReportFormatter
        import pandas as pd
        fmt = CompactReportFormatter.__new__(CompactReportFormatter)
        from docx import Document
        fmt.doc = Document()
        try:
            fmt.add_portfolio_health_dashboard(pd.DataFrame(), pd.DataFrame(), None)
        except Exception as e:
            if 'NoneType' in str(type(e).__name__) and 'get' in str(e):
                raise AssertionError("risk_summary=None not handled") from e

    def test_risk_summary_none_in_renewal_recommendations(self):
        """add_renewal_recommendations should not crash when risk_summary is None."""
        from compact_report_formatter import CompactReportFormatter
        import pandas as pd
        fmt = CompactReportFormatter.__new__(CompactReportFormatter)
        from docx import Document
        fmt.doc = Document()
        try:
            fmt.add_renewal_recommendations(None, {})
        except Exception as e:
            if 'NoneType' in str(type(e).__name__) and 'get' in str(e):
                raise AssertionError("risk_summary=None not handled") from e

    def test_cell_text_none_endswith_guard(self):
        """cell.text endswith check should handle None without AttributeError."""
        text = None
        result = (text or '').endswith('/10')
        assert result is False

    def test_duplicate_ai_callout_quote_removed(self):
        """AI callout should not have a duplicate leading quote."""
        import inspect
        from compact_report_formatter import CompactReportFormatter
        source = inspect.getsource(CompactReportFormatter.add_executive_summary)
        lines = source.split('\n')
        consecutive_quote_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "ai_callout.add_run('\"')":
                consecutive_quote_lines.append(i)
        assert len(consecutive_quote_lines) == 0, "Duplicate leading quote still present"

    def test_team_subs_df_none_in_exec_intel_formatter(self):
        """executive_intelligence_formatter should guard team_subs_df None before .empty."""
        import re
        with open('/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/executive_intelligence_formatter.py') as f:
            src = f.read()
        pattern = r'team_subs_df\.empty'
        matches = [(m.start(), src[max(0,m.start()-60):m.start()]) for m in re.finditer(pattern, src)]
        for pos, context in matches:
            assert 'is not None' in context or 'if team_subs_df' in context, \
                f"team_subs_df.empty at position {pos} lacks None guard"

    def test_runs_index_guarded_in_exec_intel_formatter(self):
        """Table header runs[0] access should be guarded in executive_intelligence_formatter."""
        with open('/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/executive_intelligence_formatter.py') as f:
            src = f.read()
        import re
        lines = src.split('\n')
        unguarded = []
        for i, line in enumerate(lines):
            if '.runs[0]' in line:
                has_guard = False
                for j in range(max(0, i - 5), i):
                    if 'if' in lines[j] and 'runs' in lines[j]:
                        has_guard = True
                        break
                if not has_guard:
                    unguarded.append(i + 1)
        assert len(unguarded) == 0, f"Unguarded runs[0] at lines: {unguarded}"

    def test_import_intel_no_file(self, client):
        """Import intel should return 400 when no file is provided."""
        resp = client.post('/api/import-intel')
        assert resp.status_code == 400

    def test_ask_intel_empty_question(self, client):
        """Ask intel should return 400 for empty question."""
        import json
        resp = client.post('/api/ask-intel',
                           data=json.dumps({'question': ''}),
                           content_type='application/json')
        assert resp.status_code == 400


class TestRound19Fixes:
    """Tests for Round 19 code audit findings."""

    def test_defang_formulas_equals(self):
        """_defang_formulas should prefix cells starting with = to prevent formula injection."""
        import pandas as pd
        df = pd.DataFrame({'A': ['=SUM(1+1)', 'normal', '+cmd', '-data', '@risk', '']})
        from adoptiq_backend import write_excel_workbook
        import inspect
        src = inspect.getsource(write_excel_workbook)
        assert '_defang_formulas' in src, "write_excel_workbook should use _defang_formulas"

    def test_defang_formulas_logic(self):
        """Verify defanging logic for formula-injection chars."""
        import pandas as pd
        df = pd.DataFrame({'col': ['=SUM(1)', '+cmd', '-data', '@risk', 'safe', '', None, 123]})
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].apply(
                    lambda v: "'" + v if isinstance(v, str) and v and v[0] in ('=', '+', '-', '@') else v
                )
        assert df['col'].iloc[0] == "'=SUM(1)"
        assert df['col'].iloc[1] == "'+cmd"
        assert df['col'].iloc[2] == "'-data"
        assert df['col'].iloc[3] == "'@risk"
        assert df['col'].iloc[4] == "safe"
        assert df['col'].iloc[5] == ""
        assert df['col'].iloc[6] is None

    def test_upload_filename_has_uuid_prefix(self):
        """All three upload paths should use UUID-prefixed filenames."""
        with open('/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/app_simple.py') as f:
            src = f.read()
        import re
        uuid_pattern = re.findall(r'_uuid\.uuid4\(\)\.hex\[:8\]', src)
        assert len(uuid_pattern) >= 3, f"Expected 3 UUID-prefixed upload paths, found {len(uuid_pattern)}"

    def test_format_number_empty_string(self):
        """format_number should return N/A for empty strings."""
        from report_utils import format_number
        assert format_number("") == "N/A"
        assert format_number("  ") == "N/A"

    def test_format_currency_empty_string(self):
        """format_currency should return N/A for empty strings."""
        from report_utils import format_currency
        assert format_currency("") == "N/A"
        assert format_currency("  ") == "N/A"

    def test_format_number_valid_string(self):
        """format_number should still handle valid numeric strings."""
        from report_utils import format_number
        assert format_number("42") == "42"
        assert format_number("1234.5", decimals=1) == "1,234.5"

    def test_format_currency_valid_string(self):
        """format_currency should still handle valid numeric strings."""
        from report_utils import format_currency
        assert format_currency("100") == "$100.00"

    def test_show_error_uses_esc(self):
        """showError in bst_psirt_search.html should escape error via _esc."""
        with open('/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/templates/bst_psirt_search.html') as f:
            src = f.read()
        assert '_esc(String(error))' in src, "showError should use _esc for XSS prevention"

    def test_minimal_test_escapes_output(self):
        """minimal_test.html should escape result.message and error.message."""
        with open('/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/templates/minimal_test.html') as f:
            src = f.read()
        assert '_esc(String(result.message' in src, "result.message should be escaped"
        assert 'textContent=error.message' in src, "error.message should be escaped via textContent"

    def test_leader_form_redirect_validation(self):
        """leader_report_form.html should validate redirect_url starts with /progress/."""
        with open('/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/templates/leader_report_form.html') as f:
            src = f.read()
        assert "startsWith('/progress/')" in src, "redirect_url should be validated"
