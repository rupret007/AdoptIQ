"""Round 33 / Build8: ``POST /api/settings/sharepoint_url`` endpoint.

The route must:
* require auth (CSRF token or ``X-AdoptIQ-Internal``);
* return 400 on missing field, non-string value, or invalid URL;
* persist a valid URL to ``settings.json`` (allow-listed key only);
* mutate ``Config.ADOPTIQ_SHAREPOINT_FOLDER_URL`` in-process;
* accept the empty string as "clear the persisted URL".
"""
from __future__ import annotations

import json
import sys

import pytest

from config import Config


@pytest.fixture(autouse=True)
def _isolate_settings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    yield


def _settings_file_path():
    import adoptiq_settings as s
    return s._settings_path()


def test_csrf_required_when_enabled(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "WTF_CSRF_ENABLED", True)
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": "https://contoso.sharepoint.com/x"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_internal_token_bypasses_csrf(client, monkeypatch):
    monkeypatch.setenv("ADOPTIQ_INTERNAL_TOKEN", "shared-secret-token")
    monkeypatch.setitem(client.application.config, "WTF_CSRF_ENABLED", True)
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": "https://contoso.sharepoint.com/sites/team/Reports"}),
        content_type="application/json",
        headers={"X-AdoptIQ-Internal": "shared-secret-token"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body and body.get("ok") is True


def test_missing_url_field_returns_400(client):
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_non_string_url_returns_400(client):
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": 12345}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_invalid_url_returns_400_without_persisting(client):
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": "javascript:alert(1)"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body and body.get("ok") is False
    assert "SharePoint" in (body.get("error") or "")
    assert not _settings_file_path().exists() or "sharepoint_folder_url" not in (
        json.loads(_settings_file_path().read_text(encoding="utf-8"))
    )


def test_valid_url_persists_and_mutates_config(client, monkeypatch):
    monkeypatch.setattr(Config, "ADOPTIQ_SHAREPOINT_FOLDER_URL", "", raising=False)
    url = "https://contoso.sharepoint.com/sites/customer-success/Documents/Reports"
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": url}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("url") == url
    assert body.get("configured") is True
    on_disk = json.loads(_settings_file_path().read_text(encoding="utf-8"))
    assert on_disk.get("sharepoint_folder_url") == url
    assert Config.ADOPTIQ_SHAREPOINT_FOLDER_URL == url


def test_empty_url_clears_persisted_value(client, monkeypatch):
    monkeypatch.setattr(
        Config,
        "ADOPTIQ_SHAREPOINT_FOLDER_URL",
        "https://old.sharepoint.com/sites/x",
        raising=False,
    )
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": ""}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("url") == ""
    assert body.get("configured") is False
    assert Config.ADOPTIQ_SHAREPOINT_FOLDER_URL == ""


def test_url_strip_whitespace(client):
    """Leading/trailing whitespace must be stripped before validation
    -- otherwise users pasting from email/Slack hit a confusing 400
    on what looks like a valid URL."""
    url = "  https://contoso.sharepoint.com/x  "
    resp = client.post(
        "/api/settings/sharepoint_url",
        data=json.dumps({"url": url}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("ok") is True
    assert body.get("url") == url.strip()
