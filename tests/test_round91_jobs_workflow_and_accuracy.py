"""Round 91: single-window jobs workflow and artifact-quality regressions."""

from __future__ import annotations
from source_shape_utils import assert_in_source

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import app_simple
import be_priority_llm_classifier as bpl
import report_utils


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_report_jobs_dashboard_module_uses_existing_status_cancel_download_apis():
    js = _read("static/js/report_jobs_dashboard.js")
    assert_in_source(js, "/api/status/all?limit=50", label='js')
    assert_in_source(js, "/cancel/", label='js')
    assert_in_source(js, "/download/", label='js')
    assert_in_source(js, "recordStartedJob", label='js')
    assert_in_source(js, "textContent", label='js')
    assert ".innerHTML" not in js


def test_analyze_page_renders_jobs_panel_and_no_longer_auto_redirects():
    html = _read("templates/analyze.html")
    assert "js/report_jobs_dashboard.js" in html
    assert_in_source(html, "data-report-jobs-panel", label='html')
    assert_in_source(html, "data-report-model-current", label='html')
    assert "window.location.href = finalRedirectUrl" not in html
    assert "Redirecting to progress page" not in html
    assert_in_source(html, "AdoptIQReportJobs.recordStartedJob", label='html')


def test_leader_form_renders_shared_jobs_panel_and_no_longer_auto_redirects():
    html = _read("templates/leader_report_form.html")
    assert "js/report_jobs_dashboard.js" in html
    assert_in_source(html, "data-report-jobs-panel", label='html')
    assert_in_source(html, "AdoptIQReportJobs.recordStartedJob", label='html')
    assert "window.location.href = url" not in html


def test_bulk_status_projects_report_model_phase_timings_and_elapsed(client):
    aid = "Round91_Test_90d_1778163000"
    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[aid] = {
            "status": "running",
            "progress": 42,
            "message": "Working",
            "manager": "Brian Frazier",
            "technology": "All Contact Center",
            "report_type": "comprehensive",
            "start_time": datetime.now(timezone.utc),
            "current_step": "Per-Customer Narratives",
            "step_start_time": datetime.now(timezone.utc),
            "active_report_model": "gemini-3.1-flash-lite",
            "report_model_name": "gemini-3.1-flash-lite",
            "phase_timings": {
                "Snowflake/Data Prep": {
                    "started_at": "2026-05-07T10:00:00Z",
                    "completed_at": "2026-05-07T10:01:00Z",
                    "duration_seconds": 60.0,
                }
            },
        }
    try:
        resp = client.get("/api/status/all?limit=5")
        assert resp.status_code == 200
        body = resp.get_json()
        rows = body["statuses"] if isinstance(body, dict) else body
        row = next(r for r in rows if r["analysis_id"] == aid)
        assert row["active_report_model"] == "gemini-3.1-flash-lite"
        assert isinstance(row["phase_timings"], dict)
        assert "elapsed_seconds" in row
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()


def test_update_progress_records_phase_timings_on_step_change():
    status = {
        "start_time": "2026-05-07T10:00:00Z",
        "step_start_time": "2026-05-07T10:00:00Z",
        "current_step": "Snowflake/Data Prep",
        "completed_steps": [],
    }
    app_simple._update_progress(status, 20, "Portfolio prompt", "Portfolio LLM", save=False)
    assert "phase_timings" in status
    assert "Snowflake/Data Prep" in status["phase_timings"]
    assert status["current_step"] == "Portfolio LLM"
    assert status["completed_steps"] == ["Snowflake/Data Prep"]


def test_comprehensive_portfolio_llm_calls_thread_report_model():
    src = _read("app_simple.py")
    assert_in_source(src, "_r91_portfolio_model", label='src')
    assert_in_source(src, "model_name=_r91_portfolio_model,  # Round 91", label='src')
    assert_in_source(src, "get_active_ask_ai_model as _r69_get_ask_ai_model", label='src')


def test_renewal_risk_methodology_uses_plain_text_bullets_not_markdown_asterisks():
    rendered = report_utils._render_risk_scoring_explanation()
    bullet_lines = [line for line in rendered.splitlines() if line.strip().startswith("- ")]
    assert len(bullet_lines) >= 5
    assert "\n* " not in rendered


def test_leader_high_severity_label_is_not_user_facing_error():
    src = _read("leader_report_generator.py")
    assert "ERROR: High Severity Issues" not in src
    assert_in_source(src, "High Severity Issues:", label='src')


def test_be_priority_classifier_retries_once_after_json_parse_error():
    df = pd.DataFrame(
        [
            {
                "ID": "ABRR-9001",
                "title": "Login outage",
                "description": "Users cannot log in",
                "severity_norm": "High",
                "open_age_days": 12,
                "customer_name": "ACME",
                "be_priority_score": 80.0,
            }
        ]
    )
    calls: list[tuple[str, str]] = []

    def fake_llm(system_prompt: str, user_prompt: str) -> str:
        calls.append((system_prompt, user_prompt))
        if len(calls) == 1:
            return "This is not JSON."
        return json.dumps(
            [
                {
                    "id": "ABRR-9001",
                    "class": "TRUE_BLOCKER",
                    "reason": "Login failure blocks production users.",
                    "confidence": "high",
                }
            ]
        )

    out, diag = bpl.classify_top_n_be_barriers(df, llm_callable=fake_llm)
    assert len(calls) == 2
    assert "previous response was not parseable JSON" in calls[1][0]
    assert diag["classified_count"] == 1
    assert diag["llm_attempts"] == 2
    assert diag["llm_recovered_after_json_retry"] is True
    assert out["be_llm_class"].iloc[0] == "TRUE_BLOCKER"

