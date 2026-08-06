"""Round 54 / F3, updated Round 108 -- admin corpus refresh/reset routes.

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

Round 108 demotes OneDrive state to optional refresh context.  The
helper remains for back-compat, but no longer blocks proxying because a
prebaked/local corpus can refresh from generated reports and uploads.

These tests pin the new server-side gate behavior:

* The legacy helper returns False even for old blocked source labels.
* Both routes proxy through normally in those states.
* When the status probe itself fails (main app unreachable, malformed
  JSON, etc.), both routes do NOT gate -- the operator can still
  reset / refresh as a corrective action against an unreachable main
  app (``_r54_corpus_is_blocked_no_onedrive`` returns False on probe
  failure so the gate is fail-OPEN here, by design -- failing closed
  on a probe error would lock operators out of the escape hatch).
"""

# Round 54

from __future__ import annotations
from source_shape_utils import assert_in_source

import os
from unittest.mock import MagicMock

import pytest

import enhanced_admin_dashboard_v2 as adm


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_admin_source() -> str:
    src_path = os.path.join(_REPO_ROOT, "enhanced_admin_dashboard_v2.py")
    with open(src_path, "r", encoding="utf-8") as fh:
        return fh.read()


def _make_status_payload(blocked: bool, source: str | None = None) -> dict:
    boot_source = source or ("blocked_no_onedrive" if blocked else "fresh")
    return {
        "ok": True, "enabled": True, "available": not blocked,
        "boot": {
            "completed": True,
            "in_progress": False,
            "source": boot_source,
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


def test_probe_returns_false_when_status_payload_says_blocked(monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=True)
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


def test_probe_returns_false_when_status_payload_says_signed_in_no_corpus(monkeypatch):
    """Round 108: signed-in/no-corpus is optional refresh context."""
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(
        blocked=True,
        source="signed_in_no_corpus",
    )
    monkeypatch.setattr(adm.requests, "get", fake_get)
    assert adm._r54_corpus_is_blocked_no_onedrive() is False


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


def test_refresh_proxies_when_legacy_blocked_source_seen(admin_client, monkeypatch):
    """Round 108: legacy blocked labels must not block local refresh."""
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=True)
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    fake_post.return_value.status_code = 200
    fake_post.return_value.json.return_value = {"refresh_started": True}
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_refresh", data={})
    assert resp.status_code == 302, "must redirect to dashboard"
    fake_post.assert_called_once()


def test_refresh_proxies_when_signed_in_no_corpus(admin_client, monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(
        blocked=True,
        source="signed_in_no_corpus",
    )
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    fake_post.return_value.status_code = 200
    fake_post.return_value.json.return_value = {"refresh_started": True}
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_refresh", data={})
    assert resp.status_code == 302
    fake_post.assert_called_once()


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


def test_reset_proxies_when_legacy_blocked_source_seen(admin_client, monkeypatch):
    """Round 108: reset remains available for real corpus recovery."""
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(blocked=True)
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    fake_post.return_value.status_code = 200
    fake_post.return_value.json.return_value = {"ok": True, "refresh_started": True}
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_reset", data={})
    assert resp.status_code == 302
    fake_post.assert_called_once()


def test_reset_proxies_when_signed_in_no_corpus(admin_client, monkeypatch):
    fake_get = MagicMock()
    fake_get.return_value.status_code = 200
    fake_get.return_value.json.return_value = _make_status_payload(
        blocked=True,
        source="signed_in_no_corpus",
    )
    monkeypatch.setattr(adm.requests, "get", fake_get)

    fake_post = MagicMock()
    fake_post.return_value.status_code = 200
    fake_post.return_value.json.return_value = {"ok": True, "refresh_started": True}
    monkeypatch.setattr(adm.requests, "post", fake_post)

    resp = admin_client.post("/corpus_reset", data={})
    assert resp.status_code == 302
    fake_post.assert_called_once()


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


def test_route_source_keeps_legacy_probe_noop_for_backcompat():
    """The helper name remains, but Round 108 makes it a no-op."""
    src = _read_admin_source()
    assert "_r54_corpus_is_blocked_no_onedrive" in src, (
        "Round 54 / F3 helper missing from admin module"
    )
    body_start = src.find("def _r54_corpus_is_blocked_no_onedrive")
    body = src[body_start:body_start + 500]
    assert_in_source(body, "return False", label='body')
    assert_in_source(body, "optional refresh context", label='body')


def test_route_source_no_longer_has_onedrive_blocked_redirects():
    """Round 108 removes OneDrive hard-block redirect messages."""
    src = _read_admin_source()
    refresh_idx = src.find("def corpus_refresh_route")
    reset_idx = src.find("def corpus_reset_route")
    refresh_body = src[refresh_idx:refresh_idx + 2500]
    reset_body = src[reset_idx:reset_idx + 2500]
    for label, body in (("refresh", refresh_body), ("reset", reset_body)):
        assert "sign in to onedrive" not in body.lower(), label
        assert "add/sync the AdoptIQ corpus share first" not in body, label
