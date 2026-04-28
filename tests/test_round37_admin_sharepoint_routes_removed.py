"""Round 37 / Phase 3: pin the admin module no longer registers the
``/sharepoint_signin`` and ``/sharepoint_refresh`` proxy routes, and
the legacy SharePoint sub-block is gone from the rendered HTML.

Round 36 deleted the upstream ``/api/corpus/sharepoint/*`` routes on
the main app, so the admin proxy routes that called into them would
just produce a 404 from MAIN_APP_URL.  Round 37 / Phase 3 deletes the
proxy routes AND the dead UI block that rendered Sign-in / Refresh
buttons against them.
"""

# ruff: noqa: E501

from __future__ import annotations

import os
import re

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_admin_source() -> str:
    src_path = os.path.join(_REPO_ROOT, "enhanced_admin_dashboard_v2.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        return fh.read()


def _admin_route_decorators(text: str) -> list[str]:
    """Extract ``@admin_app.route('...')`` URL strings from the admin
    module so we can assert the negative without false-positives from
    docstrings or comments."""
    pattern = re.compile(
        r"@admin_app\.route\(\s*['\"]([^'\"]+)['\"]",
        re.MULTILINE,
    )
    return pattern.findall(text)


@pytest.mark.parametrize(
    "url",
    [
        "/sharepoint_signin",
        "/sharepoint_refresh",
    ],
)
def test_admin_no_longer_registers_sharepoint_route(url):
    """The two legacy SharePoint admin proxy routes must not appear as
    ``@admin_app.route`` decorators."""
    text = _read_admin_source()
    routes = _admin_route_decorators(text)
    assert url not in routes, (
        f"enhanced_admin_dashboard_v2.py still registers admin route "
        f"{url!r}; Round 36 removed the upstream main-app endpoint, so "
        f"this proxy would just 404. Remove the route. Found these "
        f"sharepoint routes: {[r for r in routes if 'sharepoint' in r]!r}"
    )


def test_admin_no_sharepoint_view_functions():
    """The view function definitions themselves must be gone."""
    text = _read_admin_source()
    forbidden = (
        "def sharepoint_signin_route",
        "def sharepoint_refresh_route",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"admin module still defines view function {needle!r}; "
            "Round 37 / Phase 3 removed the SharePoint admin proxies."
        )


@pytest.mark.parametrize(
    "url",
    [
        "/sharepoint_signin",
        "/sharepoint_refresh",
    ],
)
def test_admin_app_returns_404_for_legacy_sharepoint_url(url):
    """End-to-end: the Flask test client MUST return 404 (route not
    registered) for each retired URL."""
    import enhanced_admin_dashboard_v2 as adm  # noqa: PLC0415

    client = adm.admin_app.test_client()
    resp = client.post(url)
    assert resp.status_code == 404, (
        f"POST {url} returned HTTP {resp.status_code}; expected 404 "
        "after Round 37 retired the SharePoint admin proxy routes."
    )


def test_admin_template_drops_sharepoint_signin_button():
    """The rendered HTML must NOT contain the action="/sharepoint_signin"
    or action="/sharepoint_refresh" form attributes any more.  We
    grep the source so the test runs without rendering the dashboard
    (faster + does not depend on a live main app)."""
    text = _read_admin_source()
    forbidden = (
        'action="/sharepoint_signin"',
        'action="/sharepoint_refresh"',
        ">Sign in to SharePoint<",
        ">Refresh SharePoint corpus<",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"admin template still contains {needle!r}; Round 37 / "
            "Phase 3 removed the legacy SharePoint UI."
        )


def test_admin_template_drops_sharepoint_status_block():
    """The legacy SharePoint pull-state block (``<strong>SharePoint
    pull:</strong>`` etc.) must be gone from the template -- it
    rendered for ``corpus_status.boot.sharepoint`` which is always
    None after Round 36."""
    text = _read_admin_source()
    forbidden = (
        "<strong>SharePoint pull:</strong>",
        "<strong>SharePoint account:</strong>",
        "<strong>SharePoint last refresh:</strong>",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"admin template still renders {needle!r}; the SharePoint "
            "status block was retired in Round 37 / Phase 3 because "
            "the underlying boot.sharepoint payload was nulled in "
            "Round 36."
        )


def test_admin_dashboard_renders_without_sharepoint_block(monkeypatch):
    """End-to-end render check: the dashboard must produce a 200 OK
    HTML response WITHOUT the SharePoint sub-block, even when the
    upstream payload's ``boot.sharepoint`` field is None (the only
    state Round 36 ever produces)."""
    import enhanced_admin_dashboard_v2 as adm  # noqa: PLC0415

    monkeypatch.setattr(
        adm,
        "get_server_status",
        lambda: {
            "running": True, "pid": 1, "host": "127.0.0.1", "port": 15152,
            "port_open": True, "data_path_ok": True, "data_path_detail": None,
            "last_check": "2026-04-28T13:00:00Z",
        },
    )

    def _fake_get(url, *_a, **_kw):
        class _Resp:
            status_code = 200
            def __init__(self, payload):
                self._p = payload
            def json(self):
                return self._p
        if url.endswith("/api/corpus/status"):
            return _Resp({
                "ok": True, "enabled": True, "available": True, "reason": None,
                "boot": {
                    "completed": True, "in_progress": False,
                    "onedrive_status": "synced", "onedrive_file_count": 5,
                    "sharepoint": None,
                },
                "corpus": {
                    "files_total": 5, "files_parsed": 5, "customers": 1,
                    "cases": 1, "chunks": 1, "schema_version": 6,
                    "last_parsed_at": None, "indexed_at": None,
                },
            })
        if url.endswith("/api/status/all"):
            return _Resp([])
        if url.endswith("/api/debug/verbose"):
            return _Resp({"verbose_debug": False, "snowflake_query_count": 0})
        return _Resp({})

    monkeypatch.setattr("enhanced_admin_dashboard_v2.requests.get", _fake_get)

    client = adm.admin_app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200, f"dashboard 500'd: {resp.get_data(as_text=True)[:500]}"
    body = resp.get_data(as_text=True)
    assert "SharePoint pull" not in body
    assert "Sign in to SharePoint" not in body
