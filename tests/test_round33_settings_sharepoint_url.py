"""Round 33 / Build8: ``adoptiq_settings`` sharepoint_folder_url
allow-list validation, schema round-trip, and Config bridging.

Why this test exists
--------------------
Build7 hardcoded a personal Cisco SharePoint URL into ``config.py``,
which leaked operator identity and -- because nobody else had access
to that folder -- caused Intelligence indexing to silently fail with
``auth_required`` / 404 on every fresh install.  Build8 replaces the
default with an empty string and lets the user persist a per-user
URL via ``settings.json``.  This test pins the validator and the
load/save round-trip so a future regression that broadens the
allow-list (e.g. accepts ``http://`` or arbitrary hosts) is caught
in CI.

The validator must:
* accept empty strings (= "not configured" sentinel);
* accept ``https://<tenant>.sharepoint.com/<path>`` URLs;
* reject ``http://`` (TLS required);
* reject hosts other than ``*.sharepoint.com``;
* reject overlong inputs (DoS guardrail);
* reject non-string inputs;
* reject the bare host (path component is required).
"""
from __future__ import annotations

import json
import sys

import pytest

import adoptiq_settings as s


@pytest.fixture(autouse=True)
def _isolate_settings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    yield


def test_schema_includes_sharepoint_folder_url():
    keys = s.schema_keys()
    assert "sharepoint_folder_url" in keys
    assert "corpus_knowledge_enabled" in keys


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "https://contoso.sharepoint.com/sites/team/Shared%20Documents/Reports",
        "https://cisco-my.sharepoint.com/personal/foo/Documents/Reports?csf=1",
        "https://a-b.sharepoint.com/x",
    ],
)
def test_validator_accepts_allowed_values(value):
    assert s.is_valid_sharepoint_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "http://contoso.sharepoint.com/sites/team",  # plain HTTP
        "https://contoso.sharepoint.com",            # bare host -- no path
        "https://evil.example.com/path",             # wrong host
        "https://contoso.evil.com/sharepoint.com",   # path-injected host
        "javascript:alert(1)",                       # script scheme
        "ftp://contoso.sharepoint.com/x",            # non-http(s) scheme
        "https://" + "a" * 64 + ".sharepoint.com/x", # label > 63 chars
        "https://contoso.sharepoint.com/" + "a" * 4096,  # > 2048-char cap
    ],
)
def test_validator_rejects_disallowed_values(value):
    assert s.is_valid_sharepoint_url(value) is False


@pytest.mark.parametrize("value", [123, 1.0, True, [], {}, object()])
def test_validator_rejects_non_string_inputs(value):
    assert s.is_valid_sharepoint_url(value) is False


def test_save_drops_invalid_url_silently_then_returns_empty_load():
    """A hand-edited settings.json containing an arbitrary URL must
    be dropped on both save and load -- no exception, no persisted
    poison value."""
    bad = "http://attacker.example.com/sharepoint.com/foo"
    s.save_settings({"sharepoint_folder_url": bad})
    on_disk = s._settings_path()
    if on_disk.exists():
        text = on_disk.read_text(encoding="utf-8")
        # Either the file isn't created (preferred) or the bad value
        # was dropped -- never persisted verbatim.
        if text.strip():
            payload = json.loads(text)
            assert "sharepoint_folder_url" not in payload
    loaded = s.load_settings()
    assert "sharepoint_folder_url" not in loaded


def test_save_load_round_trip_preserves_valid_url():
    url = "https://contoso.sharepoint.com/sites/team/Documents/Reports"
    s.save_settings({"sharepoint_folder_url": url})
    loaded = s.load_settings()
    assert loaded["sharepoint_folder_url"] == url


def test_save_empty_string_is_persisted_as_unset():
    """Empty string is the documented "not configured" marker; it
    must be persisted as-is so the startup hook can fall through to
    the env var without colliding with a stale value."""
    s.save_settings({"sharepoint_folder_url": ""})
    loaded = s.load_settings()
    assert loaded.get("sharepoint_folder_url") == ""


def test_load_drops_invalid_url_from_disk(tmp_path, monkeypatch):
    """If a hand-edited or downgraded settings.json carries an invalid
    URL, ``load_settings`` must drop it silently.  This is the
    defense-in-depth twin of ``save_settings`` -- both paths refuse
    to surface a non-allow-listed URL into ``Config``."""
    target = s._settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({
            "corpus_knowledge_enabled": True,
            "sharepoint_folder_url": "javascript:alert(1)",
        }),
        encoding="utf-8",
    )
    loaded = s.load_settings()
    assert loaded.get("corpus_knowledge_enabled") is True
    assert "sharepoint_folder_url" not in loaded
