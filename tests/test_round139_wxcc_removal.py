"""Round 139 / Build 109 — WxCC Health Check feature fully retired."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_wxcc_exporter_module_removed():
    assert not (REPO / "wxcc_health_input_exporter.py").exists()


def test_wxcc_cli_and_js_removed():
    assert not (REPO / "scripts" / "export_wxcc_health_input.py").exists()
    assert not (REPO / "static" / "js" / "wxcc_health_export.js").exists()


def test_wxcc_dedicated_tests_removed():
    assert not (REPO / "tests" / "test_round134_wxcc_health_input_exporter.py").exists()
    assert not (REPO / "tests" / "test_round135_wxcc_report_type.py").exists()


def test_app_simple_has_no_wxcc_health_routes():
    source = (REPO / "app_simple.py").read_text(encoding="utf-8")
    for needle in (
        "start_wxcc_health_export",
        "run_wxcc_health_export",
        "api/export/wxcc-health-input",
        "wxcc_health_input_exporter",
    ):
        assert needle not in source


def test_analyze_template_has_no_wxcc_health_card():
    html = (REPO / "templates" / "analyze.html").read_text(encoding="utf-8")
    assert "wxcc_health" not in html
    assert "WxCC Health Check" not in html
    assert "wxcc_health_export.js" not in html


def test_pyinstaller_specs_no_wxcc_hiddenimport():
    for spec in ("adoptiq_mac.spec", "adoptiq_pc.spec"):
        text = (REPO / spec).read_text(encoding="utf-8")
        assert "wxcc_health_input_exporter" not in text
