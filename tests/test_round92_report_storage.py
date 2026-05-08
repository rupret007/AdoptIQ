"""Round 92: TACTrack-style visible report storage + strict corpus gate."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch


def test_round92_settings_schema_includes_report_outputs_folder() -> None:
    import adoptiq_settings

    assert "report_outputs_folder" in adoptiq_settings.schema_keys()
    assert adoptiq_settings.is_valid_report_outputs_folder("~/Documents/AdoptIQ Reports")
    assert not adoptiq_settings.is_valid_report_outputs_folder("relative/path")


def test_round92_resolver_prefers_settings_over_env(monkeypatch, tmp_path) -> None:
    report_output_paths = importlib.import_module("report_output_paths")
    settings_dir = tmp_path / "settings"
    env_dir = tmp_path / "env"
    settings_dir.mkdir()
    env_dir.mkdir()
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(env_dir))
    with patch("adoptiq_settings.get", return_value=str(settings_dir)):
        root, source = report_output_paths.get_report_outputs_root_with_source(
            create=True,
            frozen=True,
        )
    assert root == settings_dir
    assert source == "settings.json"


def test_round92_frozen_default_is_documents_reports_folder(monkeypatch, tmp_path) -> None:
    report_output_paths = importlib.import_module("report_output_paths")
    monkeypatch.delenv("ADOPTIQ_OUTPUTS_DIR", raising=False)
    with patch("adoptiq_settings.get", return_value=""), patch("pathlib.Path.home", return_value=tmp_path):
        root, source = report_output_paths.get_report_outputs_root_with_source(
            create=True,
            frozen=True,
        )
    assert root == tmp_path / "Documents" / "AdoptIQ Reports"
    assert source == "default"


def test_round92_app_r81_outputs_root_uses_shared_resolver(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(tmp_path))
    app_simple = importlib.import_module("app_simple")
    assert app_simple._r81_outputs_root() == tmp_path


def test_round92_report_outputs_endpoint_persists_after_write_probe(client, monkeypatch, tmp_path) -> None:
    import adoptiq_settings

    settings_home = tmp_path / "settings-home"
    target = tmp_path / "visible-reports"
    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: settings_home)
    resp = client.post(
        "/api/settings/report-outputs-folder",
        json={"folder_path": str(target)},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["persisted_value"] == str(target)
    assert target.is_dir()
    saved = json.loads((settings_home / "settings.json").read_text(encoding="utf-8"))
    assert saved["report_outputs_folder"] == str(target)


def test_round92_report_outputs_endpoint_rejects_relative_path(client, monkeypatch, tmp_path) -> None:
    import adoptiq_settings

    monkeypatch.setattr(adoptiq_settings, "_app_support_dir", lambda: tmp_path)
    resp = client.post(
        "/api/settings/report-outputs-folder",
        json={"folder_path": "relative/path"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_folder_path"


def test_round92_open_report_artifact_uses_server_side_status_path(client, monkeypatch, tmp_path) -> None:
    app_simple = importlib.import_module("app_simple")
    root = tmp_path / "outputs"
    artifact = root / "Brian_Frazier" / "Compact" / "AdoptIQ_Report_Compact_Test.docx"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("docx placeholder", encoding="utf-8")
    monkeypatch.setenv("ADOPTIQ_OUTPUTS_DIR", str(root))
    opened: list[str] = []
    monkeypatch.setattr(app_simple, "_r92_open_path_in_default_app", lambda path: opened.append(path))
    with app_simple.analysis_status_lock:
        app_simple.analysis_status["Round92_Open_Test"] = {
            "status": "completed",
            "word_report": str(artifact),
            "excel_report": None,
        }
    try:
        resp = client.post("/open-report/Round92_Open_Test/docx")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert opened == [str(artifact.resolve())]
    finally:
        with app_simple.analysis_status_lock:
            app_simple.analysis_status.pop("Round92_Open_Test", None)


def test_round92_corpus_indexer_requires_eligible_sidecar(tmp_path) -> None:
    from corpus_indexer import enumerate_user_report_files

    approved = tmp_path / "AdoptIQ_Report_Approved.docx"
    rejected = tmp_path / "AdoptIQ_Report_Rejected.docx"
    missing = tmp_path / "AdoptIQ_Report_Missing.docx"
    for path in (approved, rejected, missing):
        path.write_text("placeholder", encoding="utf-8")
    approved.with_name(approved.name + ".adoptiq_corpus.json").write_text(
        json.dumps({"corpus_eligible": True}),
        encoding="utf-8",
    )
    rejected.with_name(rejected.name + ".adoptiq_corpus.json").write_text(
        json.dumps({"corpus_eligible": False, "reasons": ["grounding_rejections:1"]}),
        encoding="utf-8",
    )

    files = enumerate_user_report_files(
        tmp_path,
        recursive=True,
        require_adoptiq_quality_gate=True,
    )
    assert [f.filename for f in files] == [approved.name]


def test_round92_strict_corpus_admission_blocks_quality_failures() -> None:
    app_simple = importlib.import_module("app_simple")
    status = {
        "status": "completed",
        "grounding_diagnostics": {"rejection_summary": {"rejected": 1, "total": 5}},
        "per_customer_llm_diag": {"fallback_summary": {"fallback": 2, "total": 5}},
        "portfolio_llm_diag": {"drift_attempts": [{"field": "Total Customers"}]},
        "partial_data_warnings": [{"kind": "required_source_missing"}],
    }
    admission = app_simple._r92_corpus_admission_for_status(status)
    assert admission["corpus_eligible"] is False
    reasons = " ".join(admission["reasons"])
    assert "grounding_rejections:1" in reasons
    assert "per_customer_llm_fallbacks:2" in reasons
    assert "portfolio_drift_attempts:1" in reasons
    assert "fatal_partial_data_warnings:1" in reasons


def test_round92_strict_corpus_admission_allows_clean_completed_status() -> None:
    app_simple = importlib.import_module("app_simple")
    admission = app_simple._r92_corpus_admission_for_status({
        "status": "completed",
        "grounding_diagnostics": {"rejection_summary": {"rejected": 0, "total": 5}},
        "per_customer_llm_diag": {"fallback_summary": {"fallback": 0, "total": 5}},
        "portfolio_llm_diag": {"final_outcome": "ok", "drift_attempts": []},
        "partial_data_warnings": [{"kind": "empty_for_scope"}],
    })
    assert admission["corpus_eligible"] is True


def test_round92_preferences_ui_uses_safe_report_folder_module() -> None:
    repo = Path(__file__).resolve().parent.parent
    template = (repo / "templates" / "preferences.html").read_text(encoding="utf-8")
    script = (repo / "static" / "js" / "r92_report_outputs_folder.js").read_text(encoding="utf-8")
    assert "data-report-outputs-folder-card" in template
    assert "r92_report_outputs_folder.js" in template
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "/api/settings/report-outputs-folder" in script
