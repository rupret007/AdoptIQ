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
    # Round 26 - review (R26-002): index() routes the flag through
    # ``Config.ADOPTIQ_INTEL_UPLOAD_ENABLED`` (same source-of-truth as
    # the upload endpoint).  Pin the flag explicitly off so a stray
    # developer env or a previous test that flipped Config does not
    # leak in.
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", False)

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="adoptiq-intel-upload-form"' not in body


def test_upload_dropzone_shown_when_flag_on(client, monkeypatch):
    # R26-002: only Config.* is load-bearing now; monkeypatch.setenv on
    # the same name is harmless legacy belt-and-suspenders.
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", True)

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="adoptiq-intel-upload-form"' in body
    assert 'accept=".xlsx,.xls,.csv,.docx,.pdf"' in body


def test_upload_dropzone_uses_config_flag_not_env(client, monkeypatch):
    """R26-002: the index() template flag must agree with the
    ``api_intel_upload`` route's gate (both read
    ``Config.ADOPTIQ_INTEL_UPLOAD_ENABLED``).  Setting the env var
    AFTER Config initialisation must NOT flip the rendered flag --
    if it does, the template and the route can disagree (UI shows the
    drop-zone while the endpoint returns 403, or vice versa).
    """
    # Force Config OFF; set env ON.  The page must reflect Config (off).
    monkeypatch.setattr(Config, "ADOPTIQ_INTEL_UPLOAD_ENABLED", False)
    monkeypatch.setenv("ADOPTIQ_INTEL_UPLOAD_ENABLED", "true")
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="adoptiq-intel-upload-form"' not in body, (
        "R26-002 regression: env-var override leaked into the rendered "
        "template; index() must read Config.ADOPTIQ_INTEL_UPLOAD_ENABLED "
        "exclusively so it cannot disagree with api_intel_upload's gate."
    )
