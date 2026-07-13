"""Round 98 runtime/operator stability regression coverage."""

from __future__ import annotations

from pathlib import Path

import app_simple


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_round98_admin_status_all_parses_envelope_and_uses_live_url() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")

    assert "all_reports_payload = response.json()" in src
    assert "all_reports_payload.get('statuses')" in src
    assert "_live_main_url()}/api/corpus/status" in src
    assert "_live_main_url()}/api/shutdown" in src


def test_round98_startup_splash_waits_for_real_ping_ok() -> None:
    html = app_simple._startup_splash_html(5151)

    assert 'mode: "no-cors"' not in html
    assert "if (!response.ok)" in html
    assert "await response.text()" in html
    assert 'body.trim() !== "OK"' in html


def test_round98_ping_allows_file_splash_to_read_body(client) -> None:
    response = client.get("/ping")

    assert response.status_code == 200
    assert response.data == b"OK"
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_round98_build_mac_launcher_splash_uses_real_ping_check() -> None:
    src = (REPO_ROOT / "build_mac.sh").read_text(encoding="utf-8")

    assert 'mode: "no-cors"' not in src
    assert "if (!response.ok)" in src
    assert "await response.text()" in src
    assert 'body.trim() !== "OK"' in src


def test_round98_jobs_dashboard_surfaces_poll_failures() -> None:
    src = (REPO_ROOT / "static" / "js" / "report_jobs_dashboard.js").read_text(encoding="utf-8")

    assert "data-report-jobs-error" in src
    assert "Unable to refresh report jobs" in src
    assert "setPanelPollError('')" in src


def test_round98_download_filename_prefers_analysis_status_path(monkeypatch, tmp_path) -> None:
    root = tmp_path / "outputs"
    status_dir = root / "ManagerA" / "Compact"
    collision_dir = root / "ManagerB" / "Compact"
    status_dir.mkdir(parents=True)
    collision_dir.mkdir(parents=True)
    status_file = status_dir / "AdoptIQ_Report.docx"
    collision_file = collision_dir / "AdoptIQ_Report.docx"
    status_file.write_text("status file", encoding="utf-8")
    collision_file.write_text("collision file", encoding="utf-8")

    monkeypatch.setattr(app_simple, "_r92_candidate_output_roots", lambda create_current=False: [root])
    with app_simple.analysis_status_lock:
        old_status = dict(app_simple.analysis_status)
        app_simple.analysis_status.clear()
        app_simple.analysis_status["round98"] = {"word_report": str(status_file)}
    try:
        resolved = app_simple._r98_resolve_download_filename("AdoptIQ_Report.docx")
        assert resolved == str(status_file.resolve())
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()
            app_simple.analysis_status.update(old_status)
