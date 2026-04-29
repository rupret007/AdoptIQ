"""Round 37 / Phase 4: pin the admin Intelligence tile renders the
"Re-index now" button (POST /corpus_refresh, CSRF-protected) AND
disables it while ``boot.in_progress`` is true.

Round 26 / Phase E added two ``/corpus_refresh`` POST forms (Run
incremental + Rebuild).  Round 37 renames "Run incremental" -> "Re-
index now" (matches the analyze-page panel labelling) and disables
both buttons during an active index pass so the operator cannot
stack refresh requests.

Round 52.1 / Build28 adds the visible Reset corpus button to the same
Admin Console tile.  The route already existed since Round 39; this
file pins that the UI now exposes it with CSRF, confirmation, and the
same busy-state disable contract.
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


@pytest.fixture
def admin_client(monkeypatch):
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

    def _make_client(corpus_payload):
        def _fake_get(url, *_a, **_kw):
            class _Resp:
                status_code = 200
                def __init__(self, payload):
                    self._p = payload
                def json(self):
                    return self._p
            if url.endswith("/api/corpus/status"):
                return _Resp(corpus_payload)
            if url.endswith("/api/status/all"):
                return _Resp([])
            if url.endswith("/api/debug/verbose"):
                return _Resp({"verbose_debug": False, "snowflake_query_count": 0})
            return _Resp({})
        monkeypatch.setattr("enhanced_admin_dashboard_v2.requests.get", _fake_get)
        return adm.admin_app.test_client()

    return _make_client


def _baseline_payload(in_progress: bool):
    return {
        "ok": True, "enabled": True, "available": True, "reason": None,
        "boot": {
            "completed": True, "in_progress": in_progress,
            "last_started_at": "2026-04-28T12:50:00Z",
            "last_finished_at": "2026-04-28T12:51:00Z",
            "onedrive_status": "synced", "onedrive_file_count": 47,
            "encrypted_path": "/tmp/corpus.db.enc",
            "sharepoint": None,
        },
        "corpus": {
            "files_total": 47, "files_parsed": 47, "customers": 10,
            "cases": 100, "chunks": 5000, "schema_version": 6,
            "last_parsed_at": "2026-04-28T12:51:00Z",
            "indexed_at": "2026-04-28T12:51:00Z",
        },
    }


def test_intelligence_tile_renders_reindex_now_button(admin_client):
    """The button label MUST be "Re-index now" (Round 37 rename) and
    its form MUST POST to /corpus_refresh with the admin CSRF token."""
    client = admin_client(_baseline_payload(in_progress=False))
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "Re-index now" in body, (
        "Round 37 rename missing: 'Run incremental' should be 'Re-index now'."
    )
    assert 'action="/corpus_refresh"' in body, (
        "Re-index now button must POST to /corpus_refresh"
    )
    assert 'name="_admin_csrf"' in body, (
        "Re-index now form must carry the admin CSRF input"
    )


def test_reindex_button_disabled_when_in_progress(admin_client):
    """While ``boot.in_progress`` is true, the Re-index button MUST
    have the disabled attribute so the operator cannot stack refresh
    requests on an active index pass."""
    client = admin_client(_baseline_payload(in_progress=True))
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Find the Re-index now button and the surrounding <button> tag.
    # The flag must be the literal HTML5 ``disabled`` attribute (no
    # value or empty value -- both render the same).
    btn_match = re.search(
        r'<button[^>]*?>\s*Re-index now\s*</button>',
        body,
        re.DOTALL,
    )
    assert btn_match, "Re-index now button not found in rendered HTML"
    btn_tag = btn_match.group(0)
    assert "disabled" in btn_tag, (
        f"Re-index now button is NOT disabled while boot.in_progress=True. "
        f"Got: {btn_tag!r}"
    )


def test_reindex_button_enabled_when_idle(admin_client):
    """Inverse pin: when no index pass is running, the button MUST
    NOT carry the disabled attribute (otherwise the operator cannot
    refresh)."""
    client = admin_client(_baseline_payload(in_progress=False))
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    btn_match = re.search(
        r'<button[^>]*?>\s*Re-index now\s*</button>',
        body,
        re.DOTALL,
    )
    assert btn_match
    btn_tag = btn_match.group(0)
    # disabled must NOT appear on this enabled-state render.
    assert "disabled" not in btn_tag, (
        f"Re-index now button is INCORRECTLY disabled while idle: {btn_tag!r}"
    )


def test_rebuild_button_also_disabled_when_in_progress(admin_client):
    """The Rebuild button (separate form on the same tile) must also
    respect ``boot.in_progress``.  Otherwise an operator could
    interrupt an in-flight index pass with a destructive rebuild."""
    client = admin_client(_baseline_payload(in_progress=True))
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    btn_match = re.search(
        r'<button[^>]*?>\s*Rebuild\s*</button>',
        body,
        re.DOTALL,
    )
    assert btn_match, "Rebuild button not found in rendered HTML"
    btn_tag = btn_match.group(0)
    assert "disabled" in btn_tag, (
        f"Rebuild button must also be disabled during an active "
        f"index pass; got: {btn_tag!r}"
    )


def test_reset_corpus_button_visible_and_confirm_gated(admin_client):
    """Round 52.1 / Build28: the Admin Console must expose the existing
    /corpus_reset proxy so operators can recover a broken encrypted
    corpus without asking users to find the hidden analyze-page button."""
    client = admin_client(_baseline_payload(in_progress=False))
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "Reset corpus" in body, (
        "Admin Intelligence tile must render a visible Reset corpus button."
    )
    assert 'action="/corpus_reset"' in body, (
        "Reset corpus button must POST to the existing /corpus_reset proxy."
    )
    assert 'name="_admin_csrf"' in body, (
        "Reset corpus form must carry the admin CSRF input."
    )
    assert "Reset the local encrypted corpus cache" in body, (
        "Reset corpus form must confirm before replacing the encrypted cache."
    )


def test_reset_corpus_button_disabled_when_in_progress(admin_client):
    """Reset is destructive enough that it must not be available while
    an index pass is already running."""
    client = admin_client(_baseline_payload(in_progress=True))
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    btn_match = re.search(
        r'<button[^>]*?>\s*Reset corpus\s*</button>',
        body,
        re.DOTALL,
    )
    assert btn_match, "Reset corpus button not found in rendered HTML"
    btn_tag = btn_match.group(0)
    assert "disabled" in btn_tag, (
        f"Reset corpus button must be disabled during an active index pass; "
        f"got: {btn_tag!r}"
    )


def test_reset_corpus_button_enabled_when_idle(admin_client):
    client = admin_client(_baseline_payload(in_progress=False))
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    btn_match = re.search(
        r'<button[^>]*?>\s*Reset corpus\s*</button>',
        body,
        re.DOTALL,
    )
    assert btn_match
    btn_tag = btn_match.group(0)
    assert "disabled" not in btn_tag, (
        f"Reset corpus button is incorrectly disabled while idle: {btn_tag!r}"
    )


def test_corpus_refresh_route_still_registered():
    """Sanity: ``/corpus_refresh`` itself MUST still be a registered
    POST route -- Round 37 only added the disabled attribute on the
    button, it did NOT remove the underlying endpoint."""
    text = _read_admin_source()
    pattern = re.compile(
        r"@admin_app\.route\(\s*['\"]/corpus_refresh['\"]\s*,\s*methods\s*=\s*\[\s*['\"]POST['\"]",
        re.MULTILINE,
    )
    assert pattern.search(text), (
        "/corpus_refresh POST route is missing; Round 37 should NOT "
        "have removed it -- it is the canonical refresh entry point."
    )


def test_old_run_incremental_label_is_gone():
    """The pre-Round-37 button label "Run incremental" must NOT
    appear anywhere in the rendered tile.  Catches a botched rename
    that left both labels."""
    text = _read_admin_source()
    # Match the literal label inside a button tag (not a comment).
    pattern = re.compile(
        r"<button[^>]*>\s*Run incremental\s*</button>",
        re.DOTALL,
    )
    assert not pattern.search(text), (
        "Old 'Run incremental' button label is still in the template; "
        "Round 37 / Phase 4 renames it to 'Re-index now'."
    )
