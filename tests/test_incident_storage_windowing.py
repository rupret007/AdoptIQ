"""
Tests for the windowed-statistics fix in `incident_storage`.

Phase 2 fix: ``get_all_external_intel(days_back=N)`` must scope the
statistics to the same N-day window as the surfaced lists, so the
admin/external-intel page never shows "Total: 421" above a list of
just the most recent 90 days.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def isolated_intel_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "external_intelligence_test.db")
    import incident_storage

    monkeypatch.setattr(incident_storage, "_db_path", lambda: db_file)
    incident_storage.init_db()
    return db_file


def _seed_incident_rows():
    """Seed 1 fresh + 2 ancient incidents directly via SQL so we can
    control ``last_seen`` precisely (the helper function always stamps
    ``now``)."""
    import incident_storage

    now = datetime.utcnow()
    fresh = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    ancient = (now - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        ("INC-FRESH", "Fresh outage", None, fresh, "active", "high", "webex", "", fresh, fresh),
        ("INC-OLD-1", "Old issue", None, ancient, "resolved", "medium", "webex", "", ancient, ancient),
        ("INC-OLD-2", "Older issue", None, ancient, "resolved", "low", "webex", "", ancient, ancient),
    ]
    with sqlite3.connect(incident_storage._db_path()) as conn:
        conn.executemany(
            """
            INSERT INTO incidents (id, title, link, published, status, impact_level, source,
                                   description, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def test_windowed_stats_only_count_recent_rows():
    """``days_back=30`` must report 1 incident, not 3."""
    from incident_storage import get_incident_statistics

    _seed_incident_rows()
    stats = get_incident_statistics(days_back=30)
    assert stats["total"] == 1
    assert stats["active"] == 1
    assert stats["resolved"] == 0


def test_global_stats_count_everything():
    from incident_storage import get_incident_statistics

    _seed_incident_rows()
    stats = get_incident_statistics()
    assert stats["total"] == 3
    assert stats["active"] == 1
    assert stats["resolved"] == 2


def test_get_all_external_intel_includes_global_keys():
    """Admin page should still have access to the global totals via
    ``*_stats_global`` even when the visible list is windowed."""
    from incident_storage import get_all_external_intel

    _seed_incident_rows()
    payload = get_all_external_intel(days_back=30)
    assert "incident_stats" in payload
    assert "incident_stats_global" in payload
    assert payload["incident_stats"]["total"] == 1
    assert payload["incident_stats_global"]["total"] == 3
    # Lists themselves are also windowed.
    assert all(row["id"] == "INC-FRESH" for row in payload["incidents"])
