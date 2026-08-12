"""Round 165: BST API integration removed — PSIRT-only Cisco security API."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_embed_credentials_env_keys_has_no_bst_credentials() -> None:
    import embed_credentials

    env_keys = set(embed_credentials.ENV_KEYS)
    for banned in ("BST_API_KEY", "BST_CLIENT_SECRET", "BST_ENABLE_WEB_SCRAPING"):
        assert banned not in env_keys, f"Round 165: {banned} must not ship in ENV_KEYS"


def test_cisco_internal_integrations_has_no_bst_api_surface() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert "def search_defects_bst" not in src
    assert "def search_and_summarize_defect" not in src
    assert "def defect_portal_url" in src


def test_app_simple_psirt_routes_only(client) -> None:
    resp = client.get("/bst_psirt_search")
    assert resp.status_code == 301
    assert "/psirt_search" in (resp.headers.get("Location") or "")

    removed = client.post(
        "/search_bst_defect",
        json={"defect_id": "CSCab12345"},
    )
    assert removed.status_code == 404


def test_psirt_search_page_renders(client) -> None:
    resp = client.get("/psirt_search")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "PSIRT Advisory Search" in body
    assert "bst.cloudapps.cisco.com/bugsearch" in body
    assert "/search_bst_defect" not in body
