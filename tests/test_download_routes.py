"""
Tests for download routes in app_simple.py.
Covers /download-file/<filename> and /download/<analysis_id>/<file_type>.
Regression tests for Round 1 Fix 2 (secure_filename fallback) and
Round 2 Fix 1 (JSON fallback after restart).
"""

import sys
import os
import json
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import app_simple as app_mod


# ── /download-file/<filename> ────────────────────────────────────────────

class TestDownloadFileRoute:
    @pytest.mark.flask
    def test_missing_file_returns_404(self, client):
        rv = client.get("/download-file/nonexistent_report.docx")
        assert rv.status_code == 404

    @pytest.mark.flask
    def test_path_traversal_blocked(self, client):
        rv = client.get("/download-file/..%2F..%2Fetc%2Fpasswd.docx")
        assert rv.status_code in (400, 404)

    @pytest.mark.flask
    def test_invalid_extension_blocked(self, client):
        rv = client.get("/download-file/script.py")
        assert rv.status_code == 400

    @pytest.mark.flask
    def test_empty_filename_blocked(self, client):
        rv = client.get("/download-file/ ")
        assert rv.status_code in (400, 404)

    @pytest.mark.flask
    def test_valid_file_download(self, client, tmp_path):
        """Ensure a valid .docx file in the outputs dir can be downloaded."""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()
        test_file = outputs_dir / "test_report.docx"
        test_file.write_bytes(b"fake docx content")

        original_app_support = app_mod._APP_SUPPORT
        original_frozen = app_mod._frozen
        try:
            app_mod._APP_SUPPORT = tmp_path
            app_mod._frozen = True
            rv = client.get("/download-file/test_report.docx")
            assert rv.status_code == 200
            assert rv.data == b"fake docx content"
        finally:
            app_mod._APP_SUPPORT = original_app_support
            app_mod._frozen = original_frozen

    @pytest.mark.flask
    def test_secure_filename_fallback(self, client, tmp_path):
        """Round 1 Fix 2 regression: files with spaces should still be downloadable
        via the fallback that tries the original filename."""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()
        file_with_spaces = outputs_dir / "Report_Brian Frazier_90d.docx"
        file_with_spaces.write_bytes(b"spaced name content")

        original_app_support = app_mod._APP_SUPPORT
        original_frozen = app_mod._frozen
        try:
            app_mod._APP_SUPPORT = tmp_path
            app_mod._frozen = True
            rv = client.get("/download-file/Report_Brian Frazier_90d.docx")
            assert rv.status_code == 200
            assert rv.data == b"spaced name content"
        finally:
            app_mod._APP_SUPPORT = original_app_support
            app_mod._frozen = original_frozen


# ── /download/<analysis_id>/<file_type> ──────────────────────────────────

class TestDownloadResultRoute:
    @pytest.mark.flask
    def test_invalid_analysis_id(self, client):
        rv = client.get("/download/bad%20id/docx")
        assert rv.status_code == 400
        data = rv.get_json()
        assert "invalid analysis id" in data["error"].lower()

    @pytest.mark.flask
    def test_invalid_file_type(self, client):
        rv = client.get("/download/test-id/pdf")
        assert rv.status_code == 404
        data = rv.get_json()
        assert "Invalid file type" in data["error"]

    @pytest.mark.flask
    def test_analysis_not_found(self, client):
        rv = client.get("/download/nonexistent-id/docx")
        assert rv.status_code == 404
        data = rv.get_json()
        assert "not found" in data["error"].lower() or "not completed" in data["error"].lower()

    @pytest.mark.flask
    def test_analysis_not_completed(self, client):
        with app_mod.analysis_status_lock:
            original = dict(app_mod.analysis_status)
        try:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status["running-id"] = {
                    "status": "running",
                    "progress": 50,
                }
            rv = client.get("/download/running-id/docx")
            assert rv.status_code in (200, 400, 404)
            data = rv.get_json()
            if rv.status_code != 200:
                err_msg = data.get("error", "").lower()
                assert "not completed" in err_msg or "not found" in err_msg
        finally:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original)

    @pytest.mark.flask
    def test_json_fallback_after_restart(self, client):
        """Round 2 Fix 1 regression: download_result should load status from JSON
        file when not in memory."""
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original_status = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                test_report = Path(tmp) / "outputs" / "test_report.docx"
                test_report.parent.mkdir(parents=True, exist_ok=True)
                test_report.write_text("test content")

                status_data = {
                    "json-fallback-test": {
                        "status": "completed",
                        "word_report": str(test_report),
                    }
                }
                status_file = Path(tmp) / app_mod.STATUS_FILE
                with open(status_file, "w", encoding="utf-8") as f:
                    json.dump(status_data, f)

                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()

                rv = client.get("/download/json-fallback-test/docx")
                assert rv.status_code in (200, 404)
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original_status)

    @pytest.mark.flask
    def test_valid_file_types(self, client):
        rv_docx = client.get("/download/any-id/docx")
        rv_xlsx = client.get("/download/any-id/xlsx")
        for rv in (rv_docx, rv_xlsx):
            data = rv.get_json()
            assert "Invalid file type" not in data.get("error", "")
