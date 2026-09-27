"""Slice 1.1: fail-closed Collaboration tech accessors when practice=security."""

from __future__ import annotations

import adoptiq_settings
import practice_config as pc
import pytest


@pytest.fixture
def security_practice(monkeypatch, tmp_path):
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
    adoptiq_settings.save_settings({"practice": "security"})
    assert pc.get_active_practice() == pc.PRACTICE_SECURITY


class TestSecurityPracticeTechFailClosed:
    def test_collaboration_pack_available_only_for_collaboration(self, security_practice):
        assert pc.collaboration_technology_pack_available() is False

    @pytest.mark.parametrize(
        "getter",
        [
            pc.get_config_tech_choices,
            pc.get_config_tech_filters,
            pc.get_backend_tech_choices,
            pc.get_backend_tech_filters,
            pc.get_matrix_technology_choices,
            pc.get_analysis_form_technology_choices,
            pc.get_report_defaults_technology_choices,
        ],
    )
    def test_accessors_raise_not_collab_rows(self, security_practice, getter):
        with pytest.raises(pc.PracticeTechnologyUnavailableError) as exc:
            getter()
        assert exc.value.error_kind == pc.ERROR_COLLAB_TECH_UNAVAILABLE
        assert exc.value.practice == pc.PRACTICE_SECURITY

    def test_unavailable_payload_shape(self, security_practice):
        body = pc.technology_unavailable_payload()
        assert body["ok"] is False
        assert body["error"] == pc.ERROR_COLLAB_TECH_UNAVAILABLE
        assert body["practice"] == "security"
        assert body["collaboration_technology_available"] is False


class TestReportDefaultsApiSecurityPractice:
    def test_get_report_defaults_empty_technologies_honest_flag(self, client, security_practice):
        resp = client.get("/api/settings/report-defaults")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["practice"] == "security"
        assert data["technologies"] == []
        assert data["collaboration_technology_available"] is False
        assert data.get("technology_unavailable_detail")

    def test_collaboration_default_still_lists_technologies(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        resp = client.get("/api/settings/report-defaults")
        data = resp.get_json()
        assert data["collaboration_technology_available"] is True
        assert len(data["technologies"]) >= 5

    def test_start_analysis_returns_409_when_security_practice(self, client, security_practice):
        resp = client.post(
            "/start_analysis",
            headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"},
            json={"manager": "Brian Frazier", "technology": "All", "report_type": "comprehensive"},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["error"] == pc.ERROR_COLLAB_TECH_UNAVAILABLE
