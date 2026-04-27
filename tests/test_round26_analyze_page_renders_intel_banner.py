"""
Round 26 / Phase C: regression tests for the AdoptIQ Intelligence banner on the
analysis page, shared base template hooks (navbar badge, CSRF meta, intel_status.js),
and the optional intel upload drop-zone behind ADOPTIQ_INTEL_UPLOAD_ENABLED.
"""

import pytest

from config import Config


def test_analyze_page_renders_intel_banner_section(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="adoptiq-intel-banner"' in body
    assert "data-intel-banner" in body
    assert "AdoptIQ Intelligence" in body


def test_analyze_page_renders_run_now_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data-intel-run-now" in body
    assert "Run now" in body


def test_analyze_page_renders_navbar_intel_badge(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data-intel-badge" in body
    assert "data-intel-badge-text" in body


def test_analyze_page_loads_intel_status_js_site_wide(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body_home = resp.get_data(as_text=True)
    assert "js/intel_status.js" in body_home

    resp_help = client.get("/help")
    assert resp_help.status_code == 200
    body_help = resp_help.get_data(as_text=True)
    assert "js/intel_status.js" in body_help


def test_analyze_page_includes_csrf_meta_in_base_template(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="csrf-token"' in body

    resp_help = client.get("/help")
    assert resp_help.status_code == 200
    body_help = resp_help.get_data(as_text=True)
    assert 'name="csrf-token"' in body_help


def test_upload_dropzone_hidden_when_flag_off(client, monkeypatch):
    # index() reads os.environ directly; keep the env explicitly off so the
    # test is independent of developer shell defaults.
    monkeypatch.setenv("ADOPTIQ_INTEL_UPLOAD_ENABLED", "false")

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="adoptiq-intel-upload-form"' not in body


def test_upload_dropzone_shown_when_flag_on(client, monkeypatch):
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)
    monkeypatch.setenv("ADOPTIQ_INTEL_UPLOAD_ENABLED", "true")

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="adoptiq-intel-upload-form"' in body
    assert 'accept=".xlsx,.xls,.csv,.docx,.pdf"' in body
