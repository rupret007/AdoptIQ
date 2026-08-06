"""Round 132 / Build 102 — customer alias Preferences API + UI pins."""

from __future__ import annotations
from source_shape_utils import assert_in_source

import json
from pathlib import Path

import pytest

from data_normalization import (
    CUSTOMER_ALIASES_USER_FILENAME,
    invalidate_customer_alias_registry_cache,
    load_customer_alias_registry,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

_CUSTOMER_ALIASES_MARKERS = (
    "data-customer-aliases-card",
    "data-customer-aliases-bundled",
    "data-customer-aliases-path",
    "data-customer-aliases-input",
    "data-customer-aliases-save",
    "data-customer-aliases-clear",
    "data-customer-aliases-feedback",
)


@pytest.fixture(autouse=True)
def _fresh_alias_registry():
    invalidate_customer_alias_registry_cache()
    yield
    invalidate_customer_alias_registry_cache()


@pytest.fixture
def customer_aliases_user_path(tmp_path, monkeypatch):
    import adoptiq_settings as settings_mod

    support = tmp_path / "AdoptIQ"
    support.mkdir(parents=True)
    monkeypatch.setattr(settings_mod, "_app_support_dir", lambda: support)
    return support / CUSTOMER_ALIASES_USER_FILENAME


def test_r132_get_customer_aliases_contract(client):
    rv = client.get("/api/settings/customer-aliases")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["schema_version"] == 1
    assert isinstance(data["bundled_groups"], list)
    assert any(g.get("group_id") == "nyu_langone" for g in data["bundled_groups"])
    assert isinstance(data["operator_groups"], list)
    assert isinstance(data["effective_group_count"], int)
    assert data["effective_group_count"] >= 1
    assert "user_file_path" in data
    assert "has_operator_override" in data


def test_r132_post_customer_aliases_persists_and_reloads(client, customer_aliases_user_path):
    body = {
        "groups": [
            {
                "group_id": "test_org",
                "aliases": ["ACME CORP", "ACME CORPORATION"],
            }
        ]
    }
    rv = client.post(
        "/api/settings/customer-aliases",
        json=body,
        headers={"Content-Type": "application/json"},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["has_operator_override"] is True
    assert any(g.get("group_id") == "test_org" for g in data["operator_groups"])
    assert customer_aliases_user_path.is_file()

    reg = load_customer_alias_registry(force_reload=True)
    assert "test_org" in reg.group_id_to_aliases
    assert reg.canonical_customer_name("ACME CORP") == "ACME CORP"


def test_r132_post_clear_removes_operator_file(client, customer_aliases_user_path):
    customer_aliases_user_path.write_text(
        json.dumps({"schema_version": 1, "groups": []}),
        encoding="utf-8",
    )
    rv = client.post(
        "/api/settings/customer-aliases",
        json={"clear": True},
        headers={"Content-Type": "application/json"},
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["ok"] is True
    assert data["has_operator_override"] is False
    assert not customer_aliases_user_path.exists()


def test_r132_post_invalid_group_id_returns_400(client):
    rv = client.post(
        "/api/settings/customer-aliases",
        json={"groups": [{"group_id": "bad id!", "aliases": ["X"]}]},
        headers={"Content-Type": "application/json"},
    )
    assert rv.status_code == 400
    assert rv.get_json()["error"] == "invalid_group_id"


def test_r132_post_invalid_groups_list_returns_400(client):
    rv = client.post(
        "/api/settings/customer-aliases",
        json={"groups": "not-a-list"},
        headers={"Content-Type": "application/json"},
    )
    assert rv.status_code == 400
    assert rv.get_json()["error"] == "groups_must_be_list"


def test_r132_preferences_page_has_customer_aliases_markers(client):
    rv = client.get("/preferences")
    assert rv.status_code == 200
    html = rv.get_data(as_text=True)
    for marker in _CUSTOMER_ALIASES_MARKERS:
        assert marker in html, f"missing preferences marker {marker!r}"


def test_r132_customer_aliases_js_source_shape():
    text = (_REPO_ROOT / "static/js/r132_customer_aliases.js").read_text(encoding="utf-8")
    assert_in_source(text, "textContent", label='text')
    assert "innerHTML" not in text
    assert_in_source(text, "/api/settings/customer-aliases", label='text')
    assert_in_source(text, "data-customer-aliases-card", label='text')


def test_r132_app_simple_route_marker():
    text = (_REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(text, "@app.route('/api/settings/customer-aliases'", label='text')
    assert_in_source(text, "Round 132", label='text')


def test_r132_is_valid_customer_alias_group_id():
    from data_normalization import is_valid_customer_alias_group_id

    assert is_valid_customer_alias_group_id("nyu_langone")
    assert is_valid_customer_alias_group_id("org_2")
    assert not is_valid_customer_alias_group_id("")
    assert not is_valid_customer_alias_group_id("bad space")
    assert not is_valid_customer_alias_group_id("bad!")
