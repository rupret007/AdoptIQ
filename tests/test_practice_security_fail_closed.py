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

    @pytest.mark.parametrize(
        "path,kwargs",
        [
            (
                "/start_leader_report",
                {"data": {"manager": "Brian Frazier", "days": "90", "scope_type": "team"}},
            ),
            (
                "/start_compact_analysis",
                {
                    "headers": {"X-Requested-With": "XMLHttpRequest"},
                    "json": {"manager": "Brian Frazier", "technology": "All"},
                },
            ),
            (
                "/start_customer_renewal_analysis",
                {
                    "headers": {"X-Requested-With": "XMLHttpRequest"},
                    "json": {
                        "manager": "Brian Frazier",
                        "technology": "All",
                        "renewal_type": "renewal_portfolio",
                    },
                },
            ),
            (
                "/start_subscription_analysis",
                {"data": {"subscription_id": "SUB-1", "days": "90"}},
            ),
        ],
    )
    def test_other_start_routes_return_409_when_security_practice(
        self, client, security_practice, monkeypatch, path, kwargs
    ):
        import threading

        import app_simple

        started = []

        class _NoWorker:
            def __init__(self, *args, **kwargs):
                started.append("init")

            def start(self):
                started.append("start")

        monkeypatch.setattr(threading, "Thread", _NoWorker)
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()

        resp = client.post(path, **kwargs)
        assert resp.status_code == 409, path
        data = resp.get_json()
        assert data["error"] == pc.ERROR_COLLAB_TECH_UNAVAILABLE
        with app_simple.analysis_status_lock:
            assert app_simple.analysis_status == {}
        assert started == []

    def test_start_routes_share_one_practice_gate(self):
        import inspect

        import app_simple

        helper = "_slice1_reject_if_collaboration_technology_unavailable"
        assert helper in inspect.getsource(app_simple._slice1_reject_if_collaboration_technology_unavailable)
        for fn in (
            app_simple.start_analysis,
            app_simple.start_leader_report,
            app_simple.start_compact_analysis,
            app_simple.start_customer_renewal_analysis,
            app_simple.start_subscription_analysis,
        ):
            src = inspect.getsource(fn)
            assert helper in src, f"{fn.__name__} must call the shared practice gate"


class TestPracticeFilterRoundTripWithoutRestart:
    """P1: Security → Collaboration restores TECH_FILTERS without reboot."""

    def test_backend_and_config_filters_follow_practice_switch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)

        import adoptiq_backend as ab
        from adoptiq_backend import _filter_tech_text, _filter_tech_text_enhanced
        from config import Config
        from practices import collaboration as collab

        adoptiq_settings.save_settings({"practice": "security"})
        assert pc.get_active_practice() == pc.PRACTICE_SECURITY
        assert dict(ab.TECH_FILTERS) == {}
        assert list(ab.TECH_CHOICES) == []
        assert Config.TECH_FILTERS == {}
        assert Config.TECH_CHOICES == []
        assert ab.TECH_FILTERS.get("Webex Calling", []) == []
        with pytest.raises(KeyError):
            _filter_tech_text("Webex Calling", "Webex Calling")

        adoptiq_settings.save_settings({"practice": "collaboration"})
        assert pc.get_active_practice() == pc.PRACTICE_COLLABORATION
        assert sorted(ab.TECH_FILTERS.keys()) == sorted(collab.BACKEND_TECH_FILTERS.keys())
        assert tuple(ab.TECH_CHOICES) == collab.BACKEND_TECH_CHOICES
        assert Config.TECH_FILTERS == collab.CONFIG_TECH_FILTERS
        assert Config.TECH_CHOICES == list(collab.CONFIG_TECH_CHOICES)
        assert _filter_tech_text("Webex Calling", "Webex Calling") is True
        assert _filter_tech_text("Cisco Unified CCX", "Webex Calling") is False
        assert _filter_tech_text_enhanced("Webex Calling", "", "Webex Calling") is True
        assert _filter_tech_text_enhanced("Contact Center Software", "UCCX", "Webex Calling") is False

        adoptiq_settings.save_settings({"practice": "security"})
        assert dict(ab.TECH_FILTERS) == {}
        assert list(ab.TECH_CHOICES) == []
        assert Config.TECH_FILTERS == {}
        with pytest.raises(KeyError):
            _filter_tech_text("Webex Calling", "Webex Calling")

    def test_no_import_time_tech_snapshots_remain(self):
        from pathlib import Path

        backend = (Path(__file__).resolve().parent.parent / "adoptiq_backend.py").read_text(
            encoding="utf-8"
        )
        config = (Path(__file__).resolve().parent.parent / "config.py").read_text(encoding="utf-8")
        assert "LivePracticeMapping(get_backend_tech_filters)" in backend
        assert "TECH_FILTERS = {}" not in backend
        assert "_slice1_config_tech_filters_at_import" not in config
        assert "_LivePracticeAttr(get_config_tech_filters" in config


class TestReportDefaultsCombinedPracticeSave:
    def test_security_to_collaboration_with_technology_succeeds(self, client, security_practice):
        resp = client.post(
            "/api/settings/report-defaults",
            json={
                "practice": "collaboration",
                "default_technology": "Webex Calling",
                "default_days": 90,
                "default_manager": "",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["practice"] == "collaboration"
        assert data["collaboration_technology_available"] is True
        assert "Webex Calling" in data["technologies"]
        assert data["default_technology"] == "Webex Calling"


def _calling_uccx_ab_fixture():
    """Two-row Calling/UCCX fixture from the Round 189 Codex repro."""
    import pandas as pd

    now = pd.Timestamp.now(tz="UTC")
    return pd.DataFrame(
        [
            {
                "PRODUCT_C": "Webex Calling",
                "SUB_TECHNOLOGY_C": "Webex Calling",
                "SUBJECT_C": "Calling outage",
                "OPEN_DATE_C": now,
            },
            {
                "PRODUCT_C": "Contact Center Software",
                "SUB_TECHNOLOGY_C": "UCCX",
                "SUBJECT_C": "UCCX issue",
                "OPEN_DATE_C": now,
            },
        ]
    )


def _compact_ab_filter_tail(ab_raw, technology, days):
    """Mirrors ``run_compact_analysis`` AB filter try/except (app_simple ~11185/11208)."""
    import pandas as pd
    from adoptiq_backend import _apply_scope_filter_ab

    try:
        ab_scoped = _apply_scope_filter_ab(ab_raw, technology, days)
        return ab_scoped, None
    except Exception as exc:  # noqa: BLE001 — compact swallows this into empty evidence
        return pd.DataFrame(), exc


class TestAdmittedPracticeSnapshotStableAcrossSwitch:
    """P1 Round 189: in-flight jobs keep the admission-time filter pack."""

    def test_compact_keeps_calling_scope_after_switch_to_security(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        adoptiq_settings.save_settings({"practice": "collaboration"})
        snapshot = pc.capture_practice_filter_snapshot()
        assert snapshot.pack_available is True
        assert "Webex Calling" in snapshot.backend_tech_filters

        adoptiq_settings.save_settings({"practice": "security"})
        assert pc.get_active_practice() == pc.PRACTICE_SECURITY

        import adoptiq_backend as ab

        assert dict(ab.TECH_FILTERS) == {}
        fixture = _calling_uccx_ab_fixture()
        dropped, unbound_err = _compact_ab_filter_tail(fixture, "Webex Calling", 90)
        assert isinstance(unbound_err, KeyError)
        assert "Webex Calling" in str(unbound_err)
        assert dropped.empty
        assert not dict(getattr(dropped, "attrs", {}) or {})

        with pc.bound_job_practice_snapshot(snapshot):
            scoped, err = _compact_ab_filter_tail(fixture, "Webex Calling", 90)
        assert err is None
        assert list(scoped["PRODUCT_C"]) == ["Webex Calling"]
        assert len(scoped) == 1

    def test_renewal_direct_filter_does_not_fail_job_after_switch(self, monkeypatch, tmp_path):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        adoptiq_settings.save_settings({"practice": "collaboration"})
        snapshot = pc.capture_practice_filter_snapshot()
        adoptiq_settings.save_settings({"practice": "security"})

        from adoptiq_backend import _apply_scope_filter_ab

        fixture = _calling_uccx_ab_fixture()
        with pytest.raises(KeyError):
            _apply_scope_filter_ab(fixture, "Webex Calling", 90)

        with pc.bound_job_practice_snapshot(snapshot):
            scoped = _apply_scope_filter_ab(fixture, "Webex Calling", 90)
        assert list(scoped["PRODUCT_C"]) == ["Webex Calling"]
        assert len(scoped) == 1

    def test_reverse_security_to_collaboration_unbound_restores_live_filters(
        self, monkeypatch, tmp_path
    ):
        """New requests still follow live practice after a bound job unbinds."""
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        adoptiq_settings.save_settings({"practice": "collaboration"})
        snapshot = pc.capture_practice_filter_snapshot()
        adoptiq_settings.save_settings({"practice": "security"})

        import adoptiq_backend as ab
        from adoptiq_backend import _filter_tech_text

        with pc.bound_job_practice_snapshot(snapshot):
            assert "Webex Calling" in ab.TECH_FILTERS
            assert _filter_tech_text("Webex Calling", "Webex Calling") is True

        assert dict(ab.TECH_FILTERS) == {}
        with pytest.raises(KeyError):
            _filter_tech_text("Webex Calling", "Webex Calling")

        adoptiq_settings.save_settings({"practice": "collaboration"})
        assert "Webex Calling" in ab.TECH_FILTERS
        assert _filter_tech_text("Webex Calling", "Webex Calling") is True

    def test_new_start_still_409s_while_admitted_snapshot_is_bound(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        adoptiq_settings.save_settings({"practice": "collaboration"})
        snapshot = pc.capture_practice_filter_snapshot()
        adoptiq_settings.save_settings({"practice": "security"})

        import threading

        import app_simple

        started = []

        class _NoWorker:
            def __init__(self, *args, **kwargs):
                started.append("init")

            def start(self):
                started.append("start")

        monkeypatch.setattr(threading, "Thread", _NoWorker)
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()

        with pc.bound_job_practice_snapshot(snapshot):
            from adoptiq_backend import _apply_scope_filter_ab

            scoped = _apply_scope_filter_ab(_calling_uccx_ab_fixture(), "Webex Calling", 90)
            assert list(scoped["PRODUCT_C"]) == ["Webex Calling"]
            resp = client.post(
                "/start_compact_analysis",
                headers={"X-Requested-With": "XMLHttpRequest"},
                json={"manager": "Brian Frazier", "technology": "Webex Calling"},
            )
        assert resp.status_code == 409
        assert resp.get_json()["error"] == pc.ERROR_COLLAB_TECH_UNAVAILABLE
        assert started == []

    def test_compact_start_persists_snapshot_used_after_switch(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
        monkeypatch.delenv("ADOPTIQ_PRACTICE", raising=False)
        adoptiq_settings.save_settings({"practice": "collaboration"})

        import threading

        import app_simple

        class _NoWorker:
            def __init__(self, *args, **kwargs):
                self.daemon = False

            def start(self):
                return None

        monkeypatch.setattr(threading, "Thread", _NoWorker)
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.clear()

        resp = client.post(
            "/start_compact_analysis",
            headers={"X-Requested-With": "XMLHttpRequest"},
            json={"manager": "Brian Frazier", "technology": "Webex Calling", "days": 90},
        )
        assert resp.status_code == 200
        with app_simple.analysis_status_lock:
            status = next(iter(app_simple.analysis_status.values()))
            raw = status.get("practice_filter_snapshot")
        snapshot = pc.practice_filter_snapshot_from_mapping(raw)
        assert snapshot is not None
        assert snapshot.practice == pc.PRACTICE_COLLABORATION
        assert snapshot.pack_available is True

        adoptiq_settings.save_settings({"practice": "security"})
        with pc.bound_job_practice_snapshot(snapshot):
            scoped, err = _compact_ab_filter_tail(
                _calling_uccx_ab_fixture(), "Webex Calling", 90
            )
        assert err is None
        assert list(scoped["PRODUCT_C"]) == ["Webex Calling"]

    def test_start_routes_and_workers_bind_admitted_snapshot(self):
        import inspect

        import app_simple

        persist = "practice_filter_snapshot"
        bind = "_slice1_bind_admitted_practice_snapshot"
        reset = "_slice1_reset_admitted_practice_snapshot"
        for fn in (
            app_simple.start_analysis,
            app_simple.start_compact_analysis,
            app_simple.start_customer_renewal_analysis,
            app_simple.start_subscription_analysis,
            app_simple.start_leader_report,
        ):
            src = inspect.getsource(fn)
            assert persist in src, f"{fn.__name__} must persist the admission snapshot"

        for fn in (
            app_simple.run_compact_analysis,
            app_simple.run_customer_renewal_analysis,
            app_simple.run_comprehensive_analysis,
            app_simple.run_subscription_analysis,
            app_simple.run_leader_report_generation,
        ):
            src = inspect.getsource(fn)
            assert bind in src, f"{fn.__name__} must bind the admitted snapshot"
            assert reset in src, f"{fn.__name__} must reset the admitted snapshot"
