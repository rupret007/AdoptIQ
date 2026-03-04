"""
Tests for status management: _json_default, save_analysis_status,
load_analysis_status, and the get_status route.
Covers Round 3 Fix 1 (datetime serialization) and Fix 7 (numpy/pandas types).
"""

import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

import app_simple as app_mod
from app_simple import _json_default


# ── _json_default (Fix 7 regression) ─────────────────────────────────────

class TestJsonDefault:
    def test_datetime(self):
        dt = datetime(2026, 3, 1, 12, 30, 0)
        result = _json_default(dt)
        assert result == "2026-03-01T12:30:00"

    def test_numpy_int64(self):
        val = np.int64(42)
        result = _json_default(val)
        assert result == 42
        assert isinstance(result, int)

    def test_numpy_float64(self):
        val = np.float64(3.14)
        result = _json_default(val)
        assert abs(result - 3.14) < 1e-10
        assert isinstance(result, float)

    def test_set(self):
        result = _json_default({1, 2, 3})
        assert isinstance(result, list)
        assert sorted(result) == [1, 2, 3]

    def test_frozenset(self):
        result = _json_default(frozenset(["a", "b"]))
        assert isinstance(result, list)
        assert sorted(result) == ["a", "b"]

    def test_unknown_type_fallback(self):
        class Custom:
            def __str__(self):
                return "custom_obj"
        result = _json_default(Custom())
        assert result == "custom_obj"

    def test_numpy_bool(self):
        val = np.bool_(True)
        result = _json_default(val)
        assert result is True

    def test_json_dump_uses_default(self):
        data = {
            "dt": datetime(2026, 1, 1),
            "np_int": np.int64(99),
            "tags": {"a", "b"},
        }
        output = json.dumps(data, default=_json_default)
        parsed = json.loads(output)
        assert parsed["dt"] == "2026-01-01T00:00:00"
        assert parsed["np_int"] == 99
        assert sorted(parsed["tags"]) == ["a", "b"]


# ── save_analysis_status / load_analysis_status round-trip ───────────────

class TestStatusPersistence:
    def test_round_trip_with_datetime(self):
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original_status = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status["test-rt"] = {
                        "status": "completed",
                        "start_time": datetime(2026, 1, 1, 10, 0, 0),
                        "progress": 100,
                    }

                app_mod.save_analysis_status()
                status_file = Path(tmp) / app_mod.STATUS_FILE
                assert status_file.exists()

                with open(status_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                assert saved["test-rt"]["start_time"] == "2026-01-01T10:00:00"
                assert saved["test-rt"]["progress"] == 100
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original_status)

    def test_round_trip_with_numpy_types(self):
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original_status = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status["test-np"] = {
                        "status": "completed",
                        "progress": np.int64(100),
                        "score": np.float64(85.5),
                    }

                app_mod.save_analysis_status()
                status_file = Path(tmp) / app_mod.STATUS_FILE
                assert status_file.exists()

                with open(status_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                assert saved["test-np"]["progress"] == 100
                assert abs(saved["test-np"]["score"] - 85.5) < 1e-10
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original_status)

    def test_load_missing_file(self):
        original_app_support = app_mod._APP_SUPPORT
        with app_mod.analysis_status_lock:
            original_status = dict(app_mod.analysis_status)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                app_mod._APP_SUPPORT = Path(tmp)
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                app_mod.load_analysis_status()
                with app_mod.analysis_status_lock:
                    assert len(app_mod.analysis_status) == 0
            finally:
                app_mod._APP_SUPPORT = original_app_support
                with app_mod.analysis_status_lock:
                    app_mod.analysis_status.clear()
                    app_mod.analysis_status.update(original_status)


# ── get_status route (Fix 1 regression) ──────────────────────────────────

class TestGetStatusRoute:
    @pytest.mark.flask
    def test_status_with_datetime_values(self, client):
        with app_mod.analysis_status_lock:
            original_status = dict(app_mod.analysis_status)
        try:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status["test-datetime-route"] = {
                    "status": "running",
                    "progress": 50,
                    "message": "Processing...",
                    "step_start_time": datetime(2026, 3, 1, 12, 0, 0),
                }
            rv = client.get("/status/test-datetime-route")
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["status"] == "running"
            assert data["step_start_time"] == "2026-03-01T12:00:00"
        finally:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original_status)

    @pytest.mark.flask
    def test_status_not_found(self, client):
        rv = client.get("/status/nonexistent-id")
        assert rv.status_code == 404
        data = rv.get_json()
        assert "error" in data

    @pytest.mark.flask
    def test_status_valid(self, client):
        with app_mod.analysis_status_lock:
            original_status = dict(app_mod.analysis_status)
        try:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status["test-valid"] = {
                    "status": "completed",
                    "progress": 100,
                    "message": "Done",
                }
            rv = client.get("/status/test-valid")
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["status"] == "completed"
            assert data["progress"] == 100
        finally:
            with app_mod.analysis_status_lock:
                app_mod.analysis_status.clear()
                app_mod.analysis_status.update(original_status)
