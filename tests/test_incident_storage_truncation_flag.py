"""Phase 1.4 regression test.

``incident_storage.get_all_external_intel._list_truncated`` was reading
``stats.get('count')`` but the stats dicts produced by
``get_*_statistics`` expose ``total``. The bug made the per-source
``list_truncated[<key>]`` flag always ``False``, so the external
intelligence UI silently lied whenever the 500-row cap was hit. This
test pins the contract: when stats report ``total > _LIST_FETCH_LIMIT``
and the rendered list is at the cap, the flag is True.
"""
from __future__ import annotations

import incident_storage


def test_list_truncated_uses_total_not_count(monkeypatch):
    fetch_limit = 500
    fake_rows = [{"id": i} for i in range(fetch_limit)]
    fake_stats = {"total": fetch_limit + 25, "active": 10, "resolved": 5}

    monkeypatch.setattr(
        incident_storage,
        "get_historical_incidents",
        lambda days_back, limit: fake_rows,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_historical_bugs",
        lambda days_back, limit: fake_rows,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_historical_maintenances",
        lambda days_back, limit: fake_rows,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_incident_statistics",
        lambda days_back=None: fake_stats,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_bug_statistics",
        lambda days_back=None: fake_stats,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_maintenance_statistics",
        lambda days_back=None: fake_stats,
    )

    payload = incident_storage.get_all_external_intel(days_back=90)

    assert payload["list_fetch_limit"] == fetch_limit
    truncated = payload["list_truncated"]
    assert truncated["incidents"] is True, (
        "list_truncated['incidents'] must be True when stats['total'] "
        "exceeds the fetch limit and the rendered list is at the cap."
    )
    assert truncated["bugs"] is True
    assert truncated["maintenances"] is True


def test_list_truncated_false_when_below_limit(monkeypatch):
    fetch_limit = 500
    rows = [{"id": i} for i in range(10)]
    stats = {"total": 10}

    monkeypatch.setattr(
        incident_storage,
        "get_historical_incidents",
        lambda days_back, limit: rows,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_historical_bugs",
        lambda days_back, limit: rows,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_historical_maintenances",
        lambda days_back, limit: rows,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_incident_statistics",
        lambda days_back=None: stats,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_bug_statistics",
        lambda days_back=None: stats,
    )
    monkeypatch.setattr(
        incident_storage,
        "get_maintenance_statistics",
        lambda days_back=None: stats,
    )

    payload = incident_storage.get_all_external_intel(days_back=90)
    truncated = payload["list_truncated"]
    assert truncated["incidents"] is False
    assert truncated["bugs"] is False
    assert truncated["maintenances"] is False
    assert payload["list_fetch_limit"] == fetch_limit
