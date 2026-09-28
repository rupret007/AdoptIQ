"""Round 170: report jobs dashboard visibility under concurrent load."""

from __future__ import annotations

from pathlib import Path

from source_shape_utils import assert_in_source

ROOT = Path(__file__).resolve().parents[1]


def test_round170_status_poll_requests_two_hundred_jobs() -> None:
    js = (ROOT / "static/js/report_jobs_dashboard.js").read_text(encoding="utf-8")
    assert_in_source(js, "STATUS_FETCH_LIMIT = 200", label="js")
    assert_in_source(js, "/api/status/all?limit=' + STATUS_FETCH_LIMIT", label="js")


def test_round170_current_panel_shows_up_to_fifty_active_rows() -> None:
    js = (ROOT / "static/js/report_jobs_dashboard.js").read_text(encoding="utf-8")
    assert_in_source(js, "MAX_VISIBLE_ACTIVE_JOBS = 50", label="js")
    assert_in_source(js, "activeJobs.slice(0, MAX_VISIBLE_ACTIVE_JOBS)", label="js")
    assert "activeJobs.slice(0, 6)" not in js


def test_round170_overflow_hint_and_pageshow_refresh() -> None:
    js = (ROOT / "static/js/report_jobs_dashboard.js").read_text(encoding="utf-8")
    assert_in_source(js, "data-report-jobs-overflow", label="js")
    assert_in_source(js, "ev.persisted", label="js")
    assert_in_source(js, "pageshow", label="js")
