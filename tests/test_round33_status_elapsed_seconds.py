"""Round 33 / Build8: ``GET /status/<id>`` must emit
``elapsed_seconds`` so the progress page does not depend on
client-side ``Date.now()`` to display the elapsed timer.

Background -- the user reported that the progress-page elapsed
counter stayed at "0s" even while reports made server-side progress.
The legacy implementation computed ``Date.now() - START_TS``
client-side; when the redirect was blocked or the
browser throttled hidden-tab setInterval, the display froze.
Round 33 makes the timer authoritative server-side so the worst
case is "the JS poll completes, the watchdog re-arms the interval,
the next paint shows the correct value".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _seed_status(client, *, analysis_id, start_offset_s, completion_offset_s=None):
    """Inject a status entry directly into ``analysis_status`` so the
    /status route can serialize it without running a full report."""
    import app_simple

    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=start_offset_s)
    entry = {
        "status": "running",
        "progress": 50,
        "message": "halfway",
        "current_step": "step",
        "start_time": start,
    }
    if completion_offset_s is not None:
        entry["completion_time"] = start + timedelta(seconds=completion_offset_s)
        entry["status"] = "completed"
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[analysis_id] = entry
    return start


def test_running_report_emits_elapsed_seconds(client, monkeypatch):
    aid = "test-elapsed-running"
    _seed_status(client, analysis_id=aid, start_offset_s=120)
    resp = client.get(f"/status/{aid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "elapsed_seconds" in body
    assert isinstance(body["elapsed_seconds"], int)
    assert body["elapsed_seconds"] >= 120
    # Sanity: even on slow CI, less than a minute past the synthetic
    # 120s offset.
    assert body["elapsed_seconds"] < 240


def test_completed_report_uses_completion_time_for_elapsed(client):
    aid = "test-elapsed-completed"
    _seed_status(
        client,
        analysis_id=aid,
        start_offset_s=600,
        completion_offset_s=300,
    )
    resp = client.get(f"/status/{aid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "elapsed_seconds" in body
    # completion_time = start + 300s, so elapsed must be exactly 300
    # regardless of how much wall-clock time passed since seeding.
    assert body["elapsed_seconds"] == 300


def test_elapsed_seconds_never_negative(client):
    """Pathological case: completion_time before start_time.  Must
    clamp to 0 rather than emitting a negative integer that the
    progress page would render as ``Elapsed: -1s``."""
    import app_simple

    aid = "test-elapsed-negative"
    now = datetime.now(timezone.utc)
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[aid] = {
            "status": "completed",
            "progress": 100,
            "start_time": now,
            "completion_time": now - timedelta(seconds=10),
        }
    resp = client.get(f"/status/{aid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["elapsed_seconds"] == 0


def test_iso_string_start_time_is_supported(client):
    """When ``start_time`` was loaded from the on-disk JSON snapshot
    it is an ISO-8601 string, not a datetime.  The elapsed compute
    must accept both."""
    import app_simple

    aid = "test-elapsed-iso-string"
    iso = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[aid] = {
            "status": "running",
            "progress": 25,
            "start_time": iso,
        }
    resp = client.get(f"/status/{aid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "elapsed_seconds" in body
    assert body["elapsed_seconds"] >= 60


def test_missing_start_time_no_elapsed_seconds_emitted(client):
    """If ``start_time`` is missing, the route must not invent a
    value -- it should simply omit ``elapsed_seconds`` so the client
    can fall back to its own clock."""
    import app_simple

    aid = "test-elapsed-missing-start"
    with app_simple.analysis_status_lock:
        app_simple.analysis_status[aid] = {
            "status": "running",
            "progress": 5,
            "message": "no start time",
        }
    resp = client.get(f"/status/{aid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "elapsed_seconds" not in body
