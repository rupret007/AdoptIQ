"""Round 103 UX cleanup regression pins."""

from __future__ import annotations

import importlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GEMINI = "gemini-3.1-flash-lite"
STALE = "gpt-5-nano"


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def test_round103_migrates_stale_gpt_settings_once(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({"ask_ai_model_name": STALE, "report_model_name": STALE})

    assert _s.migrate_round103_model_defaults() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["ask_ai_model_name"] == GEMINI
    assert on_disk["report_model_name"] == GEMINI
    assert on_disk["r103_model_default_migrated"] is True
    assert _s.migrate_round103_model_defaults() is False


def test_round103_resolver_maps_stale_env_default_to_gemini(monkeypatch, tmp_path):
    monkeypatch.setenv("CIRCUIT_MODEL_NAME", STALE)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_ASK_AI", raising=False)
    monkeypatch.delenv("CIRCUIT_MODEL_NAME_REPORT", raising=False)

    import adoptiq_settings as _s
    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == GEMINI
    assert _mr.get_active_report_model() == GEMINI


def test_round103_post_migration_gpt_back_toggle_still_works(monkeypatch, tmp_path):
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    # Round 115 / Build 84: a deliberate nano choice is one made AFTER the
    # R115 re-migration, so the r115 marker must be present for the choice
    # to survive (otherwise R115 re-stomps the stale value to Gemini).
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "r115_model_default_migrated": True,
        "ask_ai_model_name": STALE,
        "report_model_name": STALE,
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == STALE
    assert _mr.get_active_report_model() == STALE


def test_round108_migrates_stale_nano_even_when_r103_marker_is_set(monkeypatch, tmp_path):
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "ask_ai_model_name": STALE,
        "report_model_name": STALE,
    })

    assert _s.ensure_model_defaults_migrated() is True
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["ask_ai_model_name"] == GEMINI
    assert on_disk["report_model_name"] == GEMINI
    assert on_disk["r108_model_default_migrated"] is True


def test_round108_post_migration_nano_choice_still_works(monkeypatch, tmp_path):
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    # Round 115 / Build 84: deliberate nano survives only when the r115
    # marker is also present (post-R115 selection).
    _s.save_settings({
        "r103_model_default_migrated": True,
        "r108_model_default_migrated": True,
        "r115_model_default_migrated": True,
        "ask_ai_model_name": STALE,
        "report_model_name": STALE,
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == STALE
    assert _mr.get_active_report_model() == STALE


def test_round108_resolver_runs_all_model_migrations(monkeypatch, tmp_path):
    monkeypatch.delenv("CIRCUIT_MODEL_NAME", raising=False)
    import adoptiq_settings as _s

    monkeypatch.setattr(_s, "_app_support_dir", lambda: tmp_path)
    _s.save_settings({
        "r103_model_default_migrated": True,
        "ask_ai_model_name": STALE,
        "report_model_name": STALE,
    })

    import model_resolver as _mr

    importlib.reload(_mr)
    assert _mr.get_active_ask_ai_model() == GEMINI
    assert _mr.get_active_report_model() == GEMINI
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["r108_model_default_migrated"] is True


def test_round108_app_boot_calls_model_migration() -> None:
    src = _read("app_simple.py")
    assert "ensure_model_defaults_migrated()" in src
    assert "Round 108 / Corpus Smoothness" in src


def test_round103_jobs_dashboard_splits_current_and_history():
    js = _read("static/js/report_jobs_dashboard.js")
    assert "sortJobsForDisplay" in js
    assert "data-report-jobs-current-only" in js
    assert "data-report-history-panel" in js
    assert "historyJobs.slice(0, 6)" in js

    for template in ("templates/analyze.html", "templates/leader_report_form.html"):
        src = _read(template)
        assert "Current Running Reports" in src
        assert "data-report-jobs-current-only" in src
        assert "Historical Reports" in src
        assert "data-report-history-panel" in src


def test_round103_jobs_dashboard_uses_theme_tokens_not_bootstrap_gray_rows():
    js = _read("static/js/report_jobs_dashboard.js")
    base = _read("templates/base.html")

    assert "table-primary" not in js
    assert "adoptiq-report-job-active" in js
    assert "[data-report-jobs-panel] .table" in base
    assert "[data-report-history-panel] .table" in base
    for token in (
        "var(--bg-surface)",
        "var(--bg-surface-raised)",
        "var(--border-subtle)",
        "var(--text-primary)",
        "var(--text-muted)",
        "var(--accent-primary)",
    ):
        assert token in base


def test_round103_leader_success_path_uses_non_blocking_notification():
    src = _read("templates/leader_report_form.html")
    assert "alert(" not in src
    assert "showLeaderNotification" in src
    assert "Leader report started successfully" in src
