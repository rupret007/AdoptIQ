"""Round 147 progress-page provenance stays scope- and source-accurate."""

from __future__ import annotations

import pytest


@pytest.fixture
def _completed_progress_status(app):
    import app_simple as app_mod

    analysis_id = "round147-progress-trust"
    with app_mod.analysis_status_lock:
        previous = dict(app_mod.analysis_status)
        app_mod.analysis_status[analysis_id] = {
            "status": "completed",
            "progress": 100,
            "message": "Leader report generated successfully!",
            "current_step": "Complete",
            "manager": "Local Fixture Manager",
            "technology": "",
            "days": 90,
            "start_time": "2026-08-04T23:38:23Z",
            "completion_time": "2026-08-04T23:38:27Z",
            "data_retrieved_at": "2026-08-03T21:00:00Z",
            "report_type": "leader",
            "scope_type": "member",
            "scope_value": "fixture.owner1@example.invalid",
            "scope_display": "Alex Rivera (fixture.owner1@example.invalid)",
        }
    try:
        yield analysis_id
    finally:
        with app_mod.analysis_status_lock:
            app_mod.analysis_status.clear()
            app_mod.analysis_status.update(previous)


def test_fixture_progress_is_honest_and_complete(
    client,
    _completed_progress_status,
    monkeypatch,
):
    monkeypatch.setenv("ADOPTIQ_LOCAL_ACCEPTANCE_ACTIVE", "1")
    response = client.get(f"/progress/{_completed_progress_status}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Controlled local fixture" in body
    assert "no live Snowflake or enterprise-system validation was performed" in body
    assert "Alex Rivera (fixture.owner1@example.invalid)" in body
    assert "<strong>Technology:</strong> All" in body
    assert "2026-08-04T23:38:27" in body
    assert "Figures are derived from Snowflake CSOne" not in body


def test_nonfixture_progress_names_canonical_source_workbook(
    client,
    _completed_progress_status,
    monkeypatch,
):
    monkeypatch.delenv("ADOPTIQ_LOCAL_ACCEPTANCE_ACTIVE", raising=False)
    response = client.get(f"/progress/{_completed_progress_status}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "Controlled local fixture only" not in body
    assert "source systems named in the canonical Source Data workbook" in body
    assert "Figures are derived from Snowflake CSOne" not in body
