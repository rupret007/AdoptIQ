"""
Tests for incident_storage module.
Validates CRUD operations for incidents, bugs, and maintenances using an isolated temp DB.
"""
import os
import sys
import sqlite3
import pytest
from unittest import mock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect incident_storage to a temporary database for test isolation."""
    db_file = str(tmp_path / 'test_intel.db')
    import incident_storage
    monkeypatch.setattr(incident_storage, '_db_path', lambda: db_file)
    incident_storage.init_db()
    return db_file


class TestIncidentStorage:
    """CRUD for incidents."""

    def test_store_and_retrieve(self):
        from incident_storage import store_historical_incidents, get_historical_incidents
        incidents = [
            {'id': 'INC-001', 'title': 'Outage', 'status': 'active', 'published': '2026-02-01', 'source': 'status.webex.com'},
            {'id': 'INC-002', 'title': 'Degraded', 'status': 'resolved', 'published': '2026-01-15', 'source': 'status.webex.com'},
        ]
        count = store_historical_incidents(incidents)
        assert count == 2

        result = get_historical_incidents(days_back=365)
        ids = [r['id'] for r in result]
        assert 'INC-001' in ids
        assert 'INC-002' in ids

    def test_upsert_updates_existing(self):
        from incident_storage import store_historical_incidents, get_historical_incidents
        store_historical_incidents([{'id': 'INC-U1', 'title': 'Original', 'status': 'active'}])
        store_historical_incidents([{'id': 'INC-U1', 'title': 'Updated', 'status': 'resolved'}])
        result = get_historical_incidents(days_back=365)
        row = [r for r in result if r['id'] == 'INC-U1'][0]
        assert row['title'] == 'Updated'
        assert row['status'] == 'resolved'

    def test_empty_input(self):
        from incident_storage import store_historical_incidents
        assert store_historical_incidents([]) == 0
        assert store_historical_incidents(None) == 0

    def test_skip_missing_id(self):
        from incident_storage import store_historical_incidents
        count = store_historical_incidents([{'title': 'no id'}])
        assert count == 0

    def test_statistics(self):
        from incident_storage import store_historical_incidents, get_incident_statistics
        store_historical_incidents([
            {'id': 'S1', 'status': 'active', 'source': 'webex'},
            {'id': 'S2', 'status': 'resolved', 'source': 'webex'},
            {'id': 'S3', 'status': 'active', 'source': 'other'},
        ])
        stats = get_incident_statistics()
        assert stats['total'] == 3
        assert stats['active'] == 2
        assert stats['resolved'] == 1
        assert 'webex' in stats['sources']


class TestBugStorage:
    """CRUD for bugs."""

    def test_store_and_retrieve(self):
        from incident_storage import store_historical_bugs, get_historical_bugs
        bugs = [
            {'bug_id': 'BUG-001', 'title': 'UI crash', 'source': 'help.webex.com'},
            {'bug_id': 'BUG-002', 'title': 'Audio drop', 'source': 'help.webex.com'},
        ]
        count = store_historical_bugs(bugs)
        assert count == 2
        result = get_historical_bugs(days_back=365)
        assert len(result) == 2

    def test_empty_and_missing_id(self):
        from incident_storage import store_historical_bugs
        assert store_historical_bugs([]) == 0
        assert store_historical_bugs([{'title': 'no id'}]) == 0

    def test_bug_statistics(self):
        from incident_storage import store_historical_bugs, get_bug_statistics
        store_historical_bugs([{'bug_id': 'BS1', 'source': 'webex'}])
        stats = get_bug_statistics()
        assert stats['total'] == 1
        assert 'webex' in stats['sources']


class TestMaintenanceStorage:
    """CRUD for maintenances."""

    def test_store_and_retrieve(self):
        from incident_storage import store_historical_maintenances, get_historical_maintenances
        maints = [{'id': 'MNT-001', 'title': 'Planned', 'status': 'scheduled'}]
        assert store_historical_maintenances(maints) == 1
        result = get_historical_maintenances(days_back=365)
        assert len(result) == 1
        assert result[0]['status'] == 'scheduled'

    def test_maintenance_statistics(self):
        from incident_storage import store_historical_maintenances, get_maintenance_statistics
        store_historical_maintenances([
            {'id': 'MS1', 'status': 'scheduled'},
            {'id': 'MS2', 'status': 'completed'},
        ])
        stats = get_maintenance_statistics()
        assert stats['total'] == 2
        assert stats['scheduled'] == 1
        assert stats['completed'] == 1


class TestExportImport:
    """Export and import full data sets."""

    def test_export_structure(self):
        from incident_storage import store_historical_incidents, export_all_data
        store_historical_incidents([{'id': 'EX1', 'title': 'test'}])
        data = export_all_data()
        assert 'schema_version' in data
        assert 'incidents' in data
        assert 'bugs' in data
        assert 'maintenances' in data
        assert data['schema_version'] == 1

    def test_import_merges_data(self):
        from incident_storage import import_all_data, get_historical_incidents
        payload = {
            'schema_version': 1,
            'incidents': [{'id': 'IMP1', 'title': 'imported'}],
            'bugs': [],
            'maintenances': [],
        }
        counts = import_all_data(payload)
        assert counts['incidents'] == 1
        result = get_historical_incidents(days_back=365)
        assert any(r['id'] == 'IMP1' for r in result)

    def test_import_invalid_data(self):
        from incident_storage import import_all_data
        counts = import_all_data(None)
        assert counts == {'incidents': 0, 'bugs': 0, 'maintenances': 0}
        counts = import_all_data({})
        assert counts == {'incidents': 0, 'bugs': 0, 'maintenances': 0}


class TestGetAllExternalIntel:
    """Test combined view."""

    def test_returns_all_sections(self):
        from incident_storage import get_all_external_intel
        data = get_all_external_intel(days_back=365)
        assert 'incidents' in data
        assert 'bugs' in data
        assert 'maintenances' in data
        assert 'incident_stats' in data
        assert 'bug_stats' in data
        assert 'maintenance_stats' in data
