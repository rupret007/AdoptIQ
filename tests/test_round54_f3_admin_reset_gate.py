"""Round 54 / F3 -- pin the server-side ``blocked_no_onedrive`` gate
on the admin ``/corpus_refresh`` and ``/corpus_reset`` proxy routes.

Background
----------
Round 53 / Phase 53.4 added template-level disabling of the Re-index /
Rebuild / Reset buttons in the admin Intelligence tile when the corpus
sits in ``blocked_no_onedrive``.  That covers the dashboard click
path -- but a curl / devtools / hostile-tab POST that carries a valid
admin CSRF token still hit the proxy, which still proxied through to
the main app, which started a refresh that immediately re-blocked on
the same Round 53 gate inside ``corpus_bootstrap._run_index_pass``.
The user-visible result was a confusing "started" / "reset+refresh
started" status banner over a corpus that never actually moved out of
the blocked state.

Round 54 / F3 short-circuits BOTH proxy routes BEFORE the proxy call
when ``corpus_status.boot.source == "blocked_no_onedrive"``.  Defense
in depth on top of the template gate; surfaces a clear "Sign in to
OneDrive first" status instead of the misleading success banner.

These tests pin the new server-side gate behavior:

* Both routes redirect with a "blocked" status message when the
  corpus is in ``blocked_no_onedrive``.
* Both routes do NOT proxy through to the main app in that state
  (verified via a spy on ``requests.post``).
* When NOT in the blocked state, both routes proxy through normally
  (the gate must not break the happy path).
* When the status probe itself fails (main app unreachable, malformed
  JSON, etc.), both routes do NOT gate -- the operator can still
  reset / refresh as a corrective action against an unreachable main
  app (``_r54_corpus_is_blocked_no_onedrive`` returns False on probe
  failure so the gate is fail-OPEN here, by design -- failing closed
  on a probe error would lock operators out of the escape hatch).
"""

# Round 54

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

import enhanced_admin_dashboard_v2 as adm


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_admin_source() -> str:
    src_path = os.path.join(_REPO_ROOT, "enhanced_admin_dashboard_v2.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        return fh.read()


def _make_status_payload(blocked: bool) -> dict:
    return {
        "ok": True, "enabled": True, "available": not blocked,
        "boot": {
            "completed": True,
            "in_progress": False,
            "source": "blocked_no_onedrive" if blocked else "fresh",
            "onedrive_status": "not_synced" if blocked else "synced",
            "onedrive_file_count": 0 if blocked else 47,
        },
        "corpus": {
            "files_total": 0 if blocked else 47,
            "files_parsed": 0 if blocked else 47,
        },
    }


@pytest.fixture
def admin_client(monkeypatch):
    """Bypass admin CSRF for these focused tests so we can exercise
    the gate logic directly.  Live admin CSRF is pinned by
    ``tests/test_round5_admin_destructive_endpoints_csrf.py``."""
    monkeypatch.setattr(adm, "_require_admin_csrf", lambda: None)
    return adm.admin_app.test_client()


# ---------------------------------------------------------------------------
# Probe helper -- _r54_corpus_is_blocked_no_onedrive
# ---------------------------------------------------------------------------


def test_probe_returns_true_when_status_payload_says_blocked(monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=True)
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is True


def test_probe_returns_false_when_status_payload_says_synced(monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=False)
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


def test_probe_fail_open_when_main_app_unreachable(monkeypatch):
    """``_r54_corpus_is_blocked_no_onedrive`` is intentionally fail-OPEN
    on probe error.  Failing closed on probe failure would lock
    operators out of the reset escape hatch when the main app is
    sick -- exactly the case where they need it most."""
    fake_get = MagicMock(side_effect=ConnectionError("main app down"))
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


def test_probe_fail_open_on_malformed_json(monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.side_effect = ValueError("not json")
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


def test_probe_fail_open_on_non_200_status(monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 500
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


def test_probe_fail_open_when_boot_missing(monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = {"ok": True}
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


# ---------------------------------------------------------------------------
# /corpus_refresh -- gated when blocked
# ---------------------------------------------------------------------------


def test_refresh_short_circuits_when_blocked(admin_client, monkeypatch):
    """When the status probe reports ``blocked_no_onedrive``, the
    refresh route must short-circuit and NOT proxy the request to
    the main app."""
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=True)
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_refresh", data={})
    assert resp.status_code == 302, "must redirect to dashboard"
    location = resp.headers.get("Location") or ""
    assert "blocked" in location.lower(), (
        f"redirect must carry the 'blocked' status message; got: {location}"
    )
    fake_post.assert_not_called()


def test_refresh_proxies_through_when_not_blocked(admin_client, monkeypatch):
    """When NOT in blocked_no_onedrive, the refresh route must proxy
    through to the main app's ``/api/corpus/refresh`` endpoint -- the
    happy path must not regress."""
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=False)
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    fake_post.return_value.status_code = 200
    fake_post.return_value.json.return_value = {"refresh_started": True}
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_refresh", data={})
    assert resp.status_code == 302
    fake_post.assert_called_once()
    posted_url = fake_post.call_args.args[0]
    assert posted_url.endswith("/api/corpus/refresh")


# ---------------------------------------------------------------------------
# /corpus_reset -- gated when blocked
# ---------------------------------------------------------------------------


def test_reset_short_circuits_when_blocked(admin_client, monkeypatch):
    """When the status probe reports ``blocked_no_onedrive``, the
    reset route must short-circuit -- otherwise the user wastes a
    click: reset preserves the bundled snapshot, triggers a refresh,
    and the refresh immediately re-blocks on the same gate."""
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=True)
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_reset", data={})
    assert resp.status_code == 302
    location = resp.headers.get("Location") or ""
    assert "blocked" in location.lower()
    assert "OneDrive" in location or "onedrive" in location.lower()
    fake_post.assert_not_called()


def test_reset_proxies_through_when_not_blocked(admin_client, monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=False)
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    fake_post.return_value.status_code = 200
    fake_post.return_value.json.return_value = {"ok": True, "refresh_started": True}
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_reset", data={})
    assert resp.status_code == 302
    fake_post.assert_called_once()
    posted_url = fake_post.call_args.args[0]
    assert posted_url.endswith("/api/corpus/reset")


# ---------------------------------------------------------------------------
# Source-shape contracts -- pin the gate is on the route
# ---------------------------------------------------------------------------


def test_route_source_calls_blocked_probe_before_proxy():
    """Pin the call ordering by source-text inspection: both proxy
    routes must invoke ``_r54_corpus_is_blocked_no_onedrive`` BEFORE
    issuing the ``requests.post`` proxy call.  Otherwise the gate
    would leak the proxy request through on a future refactor."""
    src = _read_admin_source()
    assert "_r54_corpus_is_blocked_no_onedrive" in src, (
        "Round 54 / F3 helper missing from admin module"
    )

    # Walk the source: for each route handler, the first occurrence
    # of either symbol must be the probe, not the post.
    for route_name in ("def corpus_refresh_route", "def corpus_reset_route"):
        idx = src.find(route_name)
        assert idx != -1, f"route {route_name!r} missing"
        body = src[idx:idx + 4000]
        probe_pos = body.find("_r54_corpus_is_blocked_no_onedrive")
        post_pos = body.find("requests.post")
        assert probe_pos != -1, (
            f"route {route_name!r} must call the probe before the proxy"
        )
        if post_pos != -1:
            assert probe_pos < post_pos, (
                f"route {route_name!r} probe must come BEFORE the proxy "
                f"call so the gate can short-circuit; "
                f"probe@{probe_pos} post@{post_pos}"
            )


def test_route_short_circuit_uses_warning_message_type():
    """The short-circuit redirect must carry ``message_type='warning'``
    so the dashboard renders a yellow banner, not a green success
    banner -- otherwise the operator might assume the action
    succeeded."""
    src = _read_admin_source()
    # Both routes must include the warning + Sign-in OneDrive copy.
    refresh_idx = src.find("def corpus_refresh_route")
    reset_idx = src.find("def corpus_reset_route")
    refresh_body = src[refresh_idx:refresh_idx + 2500]
    reset_body = src[reset_idx:reset_idx + 2500]
    for label, body in (("refresh", refresh_body), ("reset", reset_body)):
        assert "blocked" in body.lower(), (
            f"{label} short-circuit message must mention 'blocked'"
        )
        assert "OneDrive" in body, (
            f"{label} short-circuit must mention OneDrive in remediation"
        )
        assert "warning" in body, (
            f"{label} short-circuit must use message_type='warning'"
        )
