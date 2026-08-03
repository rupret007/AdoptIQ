"""Round 144 External Intelligence cold-page responsiveness contracts."""

from __future__ import annotations

import time

import adoptiq_backend
import app_simple
import incident_storage
import pytest


@pytest.fixture(autouse=True)
def _reset_refresh_singleflight() -> None:
    with app_simple._R144_EXTERNAL_INTEL_REFRESH_LOCK:  # noqa: SLF001
        app_simple._R144_EXTERNAL_INTEL_REFRESH_ACTIVE = False  # noqa: SLF001
    yield
    with app_simple._R144_EXTERNAL_INTEL_REFRESH_LOCK:  # noqa: SLF001
        app_simple._R144_EXTERNAL_INTEL_REFRESH_ACTIVE = False  # noqa: SLF001


def test_external_intelligence_cold_page_queues_refresh_without_fetching_inline(
    monkeypatch,
) -> None:
    incident_stats = {
        "total": 0,
        "active": 0,
        "resolved": 0,
        "newest": "",
    }
    maintenance_stats = {"total": 0, "newest": ""}
    stored = {
        "incidents": [],
        "bugs": [],
        "maintenances": [],
        "incident_stats": incident_stats,
        "bug_stats": {"total": 0},
        "maintenance_stats": maintenance_stats,
        "fetch_errors": {},
        "not_configured": {},
        "list_truncated": {},
        "list_fetch_limit": None,
    }
    monkeypatch.setattr(incident_storage, "get_incident_statistics", lambda: incident_stats)
    monkeypatch.setattr(incident_storage, "get_maintenance_statistics", lambda: maintenance_stats)
    monkeypatch.setattr(incident_storage, "get_all_external_intel", lambda **_kwargs: stored)
    monkeypatch.setattr(app_simple, "_r144_start_external_intel_refresh", lambda: "started")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("external feed fetch ran inline on the page request")

    monkeypatch.setattr(adoptiq_backend, "fetch_status_incidents", fail_if_called)
    monkeypatch.setattr(adoptiq_backend, "fetch_help_webex_bugs", fail_if_called)
    monkeypatch.setattr(adoptiq_backend, "fetch_status_maintenances", fail_if_called)

    app_simple.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    started = time.monotonic()
    response = app_simple.app.test_client().get("/external-intelligence")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 1.0
    assert b"refreshing in the background" in response.data
    assert b"may be stale or incomplete" in response.data


def test_external_intelligence_refresh_is_singleflight(monkeypatch) -> None:
    workers = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            workers.append({"target": target, "daemon": daemon, "name": name})

        def start(self) -> None:
            return None

    monkeypatch.setattr(app_simple.threading, "Thread", FakeThread)

    assert app_simple._r144_start_external_intel_refresh() == "started"  # noqa: SLF001
    assert app_simple._r144_start_external_intel_refresh() == "in_progress"  # noqa: SLF001
    assert len(workers) == 1
    assert workers[0]["daemon"] is True
    assert workers[0]["name"] == "adoptiq-external-intel-refresh"


def test_external_intelligence_worker_fetches_every_feed_and_clears_state(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        adoptiq_backend,
        "fetch_status_incidents",
        lambda *, timeout: calls.append(("incidents", timeout)),
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "fetch_help_webex_bugs",
        lambda *, timeout: calls.append(("bugs", timeout)),
    )
    monkeypatch.setattr(
        adoptiq_backend,
        "fetch_status_maintenances",
        lambda *, timeout: calls.append(("maintenances", timeout)),
    )
    with app_simple._R144_EXTERNAL_INTEL_REFRESH_LOCK:  # noqa: SLF001
        app_simple._R144_EXTERNAL_INTEL_REFRESH_ACTIVE = True  # noqa: SLF001

    app_simple._r144_external_intel_refresh_worker()  # noqa: SLF001

    assert calls == [
        ("incidents", 20),
        ("bugs", 20),
        ("maintenances", 20),
    ]
    assert app_simple._R144_EXTERNAL_INTEL_REFRESH_ACTIVE is False  # noqa: SLF001


def test_external_intelligence_worker_clears_state_after_failure(monkeypatch) -> None:
    def fail(*, timeout):
        raise RuntimeError(f"fixture failure after {timeout}")

    monkeypatch.setattr(adoptiq_backend, "fetch_status_incidents", fail)
    with app_simple._R144_EXTERNAL_INTEL_REFRESH_LOCK:  # noqa: SLF001
        app_simple._R144_EXTERNAL_INTEL_REFRESH_ACTIVE = True  # noqa: SLF001

    app_simple._r144_external_intel_refresh_worker()  # noqa: SLF001

    assert app_simple._R144_EXTERNAL_INTEL_REFRESH_ACTIVE is False  # noqa: SLF001
