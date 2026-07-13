"""Round 135 — WxCC Health Check as fifth report type (jobs panel + download TXT)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestAnalyzeTemplateShape:
    def test_fifth_report_type_radio_present(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert 'id="wxcc_health"' in html
        assert 'value="wxcc_health"' in html
        assert "WxCC Health Check" in html
        assert 'id="wxcc-card"' in html

    def test_standalone_export_button_removed(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert "data-wxcc-export-btn" not in html

    def test_report_type_grid_shows_five_cards_on_wide_layout(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert 'id="report-type-hint"' in html
        assert "row-cols-xl-5" in html
        assert html.count('id="wxcc-card"') == 1

    def test_legacy_health_input_label_removed_from_analyze(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert "WxCC Health Input" not in html
        js = _read(PROJECT_ROOT / "static/js/report_jobs_dashboard.js")
        assert "WxCC Health Input" not in js

    def test_sync_and_submit_wired_for_wxcc_health(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert "reportType === 'wxcc_health'" in html
        assert "/start_wxcc_health_export" in html
        assert "wxcc_health" in html  # syncReportTypeRequirements branch
        assert "WXCC_HEALTH_CHECK_TECHNOLOGY" in html
        assert "wxccFormData.append('technology', WXCC_HEALTH_CHECK_TECHNOLOGY)" in html

    def test_wxcc_hint_requires_locked_technology(self):
        html = _read(PROJECT_ROOT / "templates" / "analyze.html")
        assert "locked while this report type is selected" in html
        assert "change Technology Focus above if needed" not in html


class TestAppSimpleSourceShape:
    def test_start_route_and_worker_present(self):
        src = _read(PROJECT_ROOT / "app_simple.py")
        assert "def start_wxcc_health_export" in src
        assert "def run_wxcc_health_export" in src
        assert "'report_type': 'wxcc_health'" in src
        assert "txt_available" in src
        assert "validate_wxcc_health_check_technology" in src

    def test_download_whitelists_txt(self):
        src = _read(PROJECT_ROOT / "app_simple.py")
        assert re.search(r"file_type not in \('docx', 'xlsx', 'txt'\)", src)

    def test_sensitive_endpoint_registered(self):
        from app_simple import _SENSITIVE_ENDPOINTS

        assert "start_wxcc_health_export" in _SENSITIVE_ENDPOINTS


class TestJobsDashboardShape:
    def test_txt_download_button_when_txt_available(self):
        js = _read(PROJECT_ROOT / "static/js/report_jobs_dashboard.js")
        assert "job.txt_available" in js
        assert "/txt" in js
        assert "WxCC Health Check" in js


class _SyncThread:
    """Run background worker inline for deterministic pytest."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()

    def start(self):
        if self._target:
            self._target(*self._args)


@pytest.mark.flask
class TestWxccHealthExportJobFlow:
    def test_start_returns_analysis_id_and_completes_with_txt(
        self, client, monkeypatch, tmp_path
    ):
        import app_simple as app_mod
        from wxcc_health_input_exporter import ExportResult

        outputs_root = tmp_path / "outputs"
        outputs_root.mkdir(parents=True)
        out_file = outputs_root / "Customer" / "acme_wxcc_health_input.txt"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("WxCC snapshot line 1\n", encoding="utf-8")

        def _fake_export(**kwargs):
            path = kwargs.get("output_path")
            if path is not None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text("WxCC snapshot line 1\n", encoding="utf-8")
            return ExportResult(
                text="WxCC snapshot line 1\n",
                canonical_customer_name="ACME CORP",
                output_path=Path(path) if path else out_file,
                partial_data_warnings=[],
                decision_intelligence_metadata={
                    "decision_intelligence_v2_status": "canonical",
                    "analysis_schema_version": "2.0.0",
                    "analysis_fingerprint": "analysis:test-wxcc",
                    "analysis_request_fingerprint": "request:test-wxcc",
                    "analysis_comparison_scope_fingerprint": "scope:test-wxcc",
                    "analysis_snapshot_path": "/synthetic/wxcc-snapshot.json",
                },
            )

        monkeypatch.setattr(app_mod.threading, "Thread", _SyncThread)
        monkeypatch.setattr(
            "wxcc_health_input_exporter.export_wxcc_health_input",
            _fake_export,
        )
        monkeypatch.setattr(
            app_mod,
            "_r81_resolve_report_output_dir",
            lambda manager, report_type, customer=None: outputs_root / "Customer",
        )
        monkeypatch.setattr(
            app_mod,
            "get_latest_csone_from_folder_diag",
            lambda: (None, "not_synced", 0),
        )
        monkeypatch.setattr(app_mod, "_resolve_csone_path_safe", lambda p: None)

        original_support = app_mod._APP_SUPPORT
        original_frozen = app_mod._frozen
        original_status = {}
        try:
            app_mod._APP_SUPPORT = tmp_path
            app_mod._frozen = True
            with app_mod.analysis_status_lock:
                original_status = dict(app_mod.analysis_status)

            rv = client.post(
                "/start_wxcc_health_export",
                data={
                    "manager": "Single Customer",
                    "technology": "Webex Contact Center",
                    "days": "90",
                    "customer_name": "ACME CORP",
                    "subscription_id": "",
                },
            )
            assert rv.status_code == 200
            payload = rv.get_json()
            assert payload.get("success") is True
            analysis_id = payload.get("analysis_id")
            assert analysis_id
            assert analysis_id.startswith("WxCC_Health_")

            status_rv = client.get(f"/status/{analysis_id}")
            assert status_rv.status_code == 200
            status = status_rv.get_json()
            assert status.get("status") == "completed"
            assert status.get("txt_available") is True
            assert status.get("report_type") == "wxcc_health"
            assert status.get("word_available") is False
            assert status.get("excel_available") is False
            assert status.get("decision_intelligence_v2_status") == "canonical"
            assert status.get("analysis_fingerprint") == "analysis:test-wxcc"
            assert status.get("analysis_request_fingerprint") == "request:test-wxcc"
            assert status.get("analysis_comparison_scope_fingerprint") == (
                "scope:test-wxcc"
            )
            assert status.get("analysis_snapshot_path") == (
                "/synthetic/wxcc-snapshot.json"
            )

            with app_mod.analysis_status_lock:
                stored = app_mod.analysis_status[analysis_id]
                text_path = stored.get("wxcc_report") or stored.get("text_report")
            assert text_path

            dl = client.get(f"/download/{analysis_id}/txt")
            assert dl.status_code == 200
            assert b"WxCC snapshot" in dl.data
        finally:
            app_mod._APP_SUPPORT = original_support
            app_mod._frozen = original_frozen
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original_status)

    def test_start_requires_customer_or_subscription(self, client):
        rv = client.post(
            "/start_wxcc_health_export",
            data={
                "manager": "Brian Frazier",
                "technology": "Webex Contact Center",
                "days": "90",
                "customer_name": "",
                "subscription_id": "",
            },
        )
        assert rv.status_code == 400
        assert "required" in rv.get_json().get("error", "").lower()

    def test_start_rejects_non_wxcc_technology(self, client):
        rv = client.post(
            "/start_wxcc_health_export",
            data={
                "manager": "Single Customer",
                "technology": "Webex Calling",
                "days": "90",
                "customer_name": "ACME CORP",
                "subscription_id": "",
            },
        )
        assert rv.status_code == 400
        assert "Webex Contact Center" in rv.get_json().get("error", "")

    def test_api_rejects_non_wxcc_technology(self, client):
        resp = client.post(
            "/api/export/wxcc-health-input",
            json={
                "customer_name": "ACME CORP",
                "technology": "Webex Calling",
                "days": 90,
            },
        )
        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload.get("error_kind") == "validation"
        assert "Webex Contact Center" in payload.get("error", "")

    @pytest.mark.flask
    def test_download_rejects_unknown_file_type(self, client):
        with mock.patch.dict(
            "app_simple.analysis_status",
            {
                "WxCC_Health_test_90d_1": {
                    "status": "completed",
                    "wxcc_report": "/tmp/fake.txt",
                    "txt_available": True,
                }
            },
            clear=False,
        ):
            rv = client.get("/download/WxCC_Health_test_90d_1/pdf")
        assert rv.status_code == 404
        assert "Invalid file type" in rv.get_json().get("error", "")
