"""Slice 1: practice setting precedence and validation."""

from __future__ import annotations

import adoptiq_settings
import practice_config as pc


class TestPracticePrecedence:
    def test_default_is_collaboration_without_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        assert pc.get_active_practice() == pc.PRACTICE_COLLABORATION

    def test_settings_json_over_env(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.setenv("ADOPTIQ_PRACTICE", "security")
        adoptiq_settings.save_settings({"practice": "collaboration"})
        assert pc.get_active_practice() == pc.PRACTICE_COLLABORATION

    def test_env_over_hard_default(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.setenv("ADOPTIQ_PRACTICE", "security")
        assert pc.get_active_practice() == pc.PRACTICE_SECURITY

    def test_invalid_practice_in_settings_is_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        adoptiq_settings.save_settings({"practice": "not-a-practice"})
        assert pc.get_active_practice() == pc.PRACTICE_COLLABORATION

    def test_invalid_practice_rejected_on_save(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        adoptiq_settings.save_settings({"practice": "webex"})
        loaded = adoptiq_settings.load_settings()
        assert "practice" not in loaded

    def test_is_valid_practice_cases(self):
        assert adoptiq_settings.is_valid_practice("collaboration")
        assert adoptiq_settings.is_valid_practice("Collaboration")
        assert adoptiq_settings.is_valid_practice("security")
        assert not adoptiq_settings.is_valid_practice("")
        assert not adoptiq_settings.is_valid_practice("security-sku")
        assert not adoptiq_settings.is_valid_practice(None)


class TestReportDefaultsApiPracticeField:
    def test_get_report_defaults_includes_practice(self, client):
        resp = client.get("/api/settings/report-defaults")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["practice"] == "collaboration"
        assert isinstance(data["technologies"], list)
        assert len(data["technologies"]) >= 5
