"""
Tests for cancel and error handling paths in app_simple.py.
Covers cancel_analysis route (Round 3 Fix 3 -- save_analysis_status persistence)
and error status handling.
"""

import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import app_simple as app_mod


class TestCancelAnalysis:
    @pytest.mark.flask
    def test_cancel_invalid_analysis_id_returns_400(self, client):
        rv = client.post("/cancel/bad%20id")
        assert rv.status_code == 400
        data = rv.get_json()
        assert "invalid analysis id" in data["error"].lower()

    @pytest.mark.flask
    def test_cancel_nonexistent_returns_404(self, client):
        rv = client.post("/cancel/nonexistent-id")
        assert rv.status_code == 404
        data = rv.get_json()
        assert "error" in data

    @pytest.mark.flask
    def test_cancel_completed_returns_400(self, client):
        with app_mod.analysis_status_lock:
            original = dict(app_mod.analysis_status)
        try:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status["completed-id"] = {
                    "status": "completed",
                    "progress": 100,
                }
            rv = client.post("/cancel/completed-id")
            assert rv.status_code == 400
            data = rv.get_json()
            assert "not running" in data["error"].lower()
        finally:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original)

    @pytest.mark.flask
    def test_cancel_running_sets_cancelling(self, client):
        """Round 3 Fix 3 regression: cancel should update status and persist."""
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status["cancel-test"] = {
                        "status": "running",
                        "progress": 50,
                        "message": "Processing...",
                    }
                rv = client.post("/cancel/cancel-test")
                assert rv.status_code == 200
                data = rv.get_json()
                assert data["success"] is True

                with app_mod.analysis_status_lock:
                    assert app_mod.analysis_status["cancel-test"]["status"] == "cancelling"

                status_file = Path(tmp) / app_mod.STATUS_FILE
                assert status_file.exists(), "save_analysis_status() should persist after cancel"
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original)

    @pytest.mark.flask
    def test_cancel_starting_also_works(self, client):
        with app_mod.analysis_status_lock:
            original = dict(app_mod.analysis_status)
        try:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status["starting-id"] = {
                    "status": "starting",
                    "progress": 0,
                }
            rv = client.post("/cancel/starting-id")
            assert rv.status_code == 200
        finally:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original)


class TestErrorStatusPersistence:
    def test_error_status_persists(self):
        """Verify that error status is saved (Round 3 Fix 3)."""
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status["err-test"] = {
                        "status": "error",
                        "message": "Analysis failed: test error",
                        "error": "test error",
                    }
                app_mod.save_analysis_status()

                status_file = Path(tmp) / app_mod.STATUS_FILE
                assert status_file.exists()

                with open(status_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                assert saved["err-test"]["status"] == "error"
                assert "test error" in saved["err-test"]["message"]
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original)

    def test_cancelling_status_persists(self):
        """Verify cancelling status round-trips through save/load."""
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status["cancel-persist"] = {
                        "status": "cancelling",
                        "message": "Cancellation requested...",
                        "progress": 0,
                    }
                app_mod.save_analysis_status()

                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()

                app_mod.load_analysis_status()

                with app_mod.analysis_status_lock:
                    loaded = app_mod.analysis_status.get("cancel-persist", {})
                    assert loaded["status"] == "cancelling"
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original)
