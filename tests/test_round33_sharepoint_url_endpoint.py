"""Round 33 / Build8 (retired in Round 35): the
``POST /api/settings/sharepoint_url`` endpoint is gone.

Round 35 / native-corpus replaced the user-configurable URL with a
hardcoded ``Config.ADOPTIQ_CORPUS_SHARE_URL`` (env-overridable for
ops only).  The Build8 route added an attack surface (a writable,
process-mutating Flask endpoint) that no longer earns its keep, so
it is removed.

This module remains as a regression pin: if someone re-introduces
the route in a future round without the original Build8 hardening,
CI catches it via ``test_route_no_longer_registered``.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_settings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
    yield


def test_route_no_longer_registered(client):
    """The Build8 route is gone -- a POST should resolve to 404 (or
    405 if some future round mounts a different verb on the path)."""
    resp = client.post(
        "/api/settings/sharepoint_url",
        json={"url": "https://contoso.sharepoint.com/sites/team/Reports"},
    )
    assert resp.status_code in (404, 405), (
        f"Round 35 retired POST /api/settings/sharepoint_url; "
        f"expected 404/405, got {resp.status_code}"
    )


def test_route_not_in_url_map(client):
    """Belt-and-braces: the Flask URL map must not carry the route at
    all.  A 404 alone could mean a method mismatch -- this assertion
    proves the rule never registered."""
    rules = [r.rule for r in client.application.url_map.iter_rules()]
    assert "/api/settings/sharepoint_url" not in rules, (
        "Round 35 retired the URL endpoint; a future round must not "
        "re-register it without the original Build8 hardening "
        "(CSRF, host gate, sensitive-endpoint set)."
    )
