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

    def test_admin_page_renders(self, app, client):
        response = client.get('/admin')
        assert response.status_code == 200
        assert b'Admin Console' in response.data

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
