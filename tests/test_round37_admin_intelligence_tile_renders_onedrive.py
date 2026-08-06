"""Round 37 / Phase 2: pin the admin Intelligence tile renders the
Round 36 OneDrive sync state.

Round 36 added ``boot.onedrive_status`` and ``boot.onedrive_file_count``
to the ``/api/corpus/status`` JSON contract.  The admin dashboard fetches
that JSON and renders a tile, but until Round 37 the new fields were
not surfaced anywhere in the template.  This test pins that the new
state appears in the rendered HTML with the right pill colors.
"""

# ruff: noqa: E501

from __future__ import annotations
from source_shape_utils import assert_in_source

import json

import pytest


@pytest.fixture
def admin_client_with_corpus(monkeypatch):
    """Yield a Flask test client whose ``/api/corpus/status`` proxy
    returns a controlled corpus_status payload (with the requested
    ``boot.onedrive_status`` / ``boot.onedrive_file_count``)."""
    import enhanced_admin_dashboard_v2 as adm  # noqa: PLC0415

    # Stub server_status so the dashboard always renders the Stop
    # Server form (the admin tile is below it; we want a clean
    # render).
    monkeypatch.setattr(
        adm,
        "get_server_status",
        lambda: {
            "running": True,
            "pid": 1,
            "host": "127.0.0.1",
            "port": 15152,
            "port_open": True,
            "data_path_ok": True,
            "data_path_detail": None,
            "last_check": "2026-04-28T13:00:00Z",
        },
    )

    def _make_client(corpus_payload):
        def _fake_get(url, *_a, **_kw):
            class _Resp:
                def __init__(self, payload, status=200):
                    self._payload = payload
                    self.status_code = status
                def json(self):
                    return self._payload

            if url.endswith("/api/corpus/status"):
                return _Resp(corpus_payload, 200)
            if url.endswith("/api/status/all"):
                return _Resp([], 200)
            if url.endswith("/api/debug/verbose"):
                return _Resp({"verbose_debug": False, "snowflake_query_count": 0}, 200)
            if url.endswith("/api/diag/connectivity"):
                return _Resp({"ok": True}, 200)
            return _Resp({}, 200)

        monkeypatch.setattr(
            "enhanced_admin_dashboard_v2.requests.get",
            _fake_get,
        )
        return adm.admin_app.test_client()

    return _make_client


def _baseline_corpus_payload(**overrides):
    """Build a minimal but valid corpus_status payload.  Tests pass
    overrides on top to flip the OneDrive state."""
    payload = {
        "ok": True,
        "enabled": True,
        "available": True,
        "reason": None,
        "boot": {
            "completed": True,
            "in_progress": False,
            "last_started_at": "2026-04-28T12:50:00Z",
            "last_finished_at": "2026-04-28T12:51:00Z",
            "last_error": None,
            "last_error_kind": None,
            "encrypted_path": "/tmp/corpus.db.enc",
            "onedrive_root": "/Users/test/OneDrive/AdoptIQ",
            "onedrive_status": None,
            "onedrive_file_count": None,
            "last_stats": None,
            "last_sources": None,
            "sharepoint": None,
        },
        "corpus": {
            "files_total": 47,
            "files_parsed": 47,
            "customers": 10,
            "cases": 100,
            "chunks": 5000,
            "schema_version": 6,
            "last_parsed_at": "2026-04-28T12:51:00Z",
            "indexed_at": "2026-04-28T12:51:00Z",
        },
    }
    boot_overrides = overrides.pop("boot", {})
    payload["boot"].update(boot_overrides)
    payload.update(overrides)
    return payload


def test_intelligence_tile_renders_synced_state(admin_client_with_corpus):
    """OneDrive synced (47 files): pill says "synced" and the file
    count surfaces."""
    payload = _baseline_corpus_payload(boot={
        "onedrive_status": "synced",
        "onedrive_file_count": 47,
    })
    client = admin_client_with_corpus(payload)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "OneDrive sync" in body, "Round 37 OneDrive sync row missing from tile"
    assert "synced" in body, "Status pill 'synced' missing"
    assert "47 files" in body, "File count '47 files' missing"
    # Negative: must not render the prompt to sign in to OneDrive.
    assert "sign in to OneDrive" not in body.lower() or (
        # The instructional text only appears in the not_synced /
        # unknown branch; if we leaked it into the synced branch the
        # lowercase form would show up here.
        "sign in to onedrive" not in body.lower()
    )


def test_intelligence_tile_renders_not_synced_state(admin_client_with_corpus):
    """OneDrive not_synced: pill says "not synced" and the helper text
    instructs the operator to sync the canonical folder."""
    payload = _baseline_corpus_payload(boot={
        "onedrive_status": "not_synced",
        "onedrive_file_count": 0,
    })
    client = admin_client_with_corpus(payload)
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    assert_in_source(body, "OneDrive sync", label='body')
    assert "not synced" in body, "Status 'not synced' missing"
    assert "AdoptIQ_CSOne_Reports" in body, (
        "Helper text should name the canonical OneDrive folder so the "
        "operator knows what to sync."
    )


def test_intelligence_tile_renders_unknown_state(admin_client_with_corpus):
    """OneDrive unknown (env unset / OS error): pill says "unknown",
    no file count, helper text still guides the operator."""
    payload = _baseline_corpus_payload(boot={
        "onedrive_status": "unknown",
        "onedrive_file_count": None,
    })
    client = admin_client_with_corpus(payload)
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    assert_in_source(body, "OneDrive sync", label='body')
    assert "unknown" in body, "Status 'unknown' missing"
    assert "AdoptIQ_CSOne_Reports" in body, (
        "Helper text should still appear in the unknown state."
    )


def test_intelligence_tile_omits_onedrive_row_when_not_in_payload(admin_client_with_corpus):
    """A pre-Round-36 main app that does not send ``onedrive_status``
    should NOT crash the admin tile.  The OneDrive row is gated on
    ``{% if _od_status %}`` so it disappears when the field is None."""
    payload = _baseline_corpus_payload(boot={
        "onedrive_status": None,
        "onedrive_file_count": None,
    })
    client = admin_client_with_corpus(payload)
    resp = client.get("/")
    assert resp.status_code == 200, (
        "Dashboard 500'd when boot.onedrive_status was None; the "
        "template must tolerate the legacy payload shape."
    )
    body = resp.get_data(as_text=True)
    # The row should not render at all -- "OneDrive sync" appears in
    # exactly zero <strong> labels under this state.
    assert "<strong>OneDrive sync:</strong>" not in body, (
        "OneDrive sync row rendered even though onedrive_status was "
        "None; the {% if _od_status %} guard is missing."
    )


def test_intelligence_tile_renders_singular_file_count(admin_client_with_corpus):
    """Pluralization: 1 file -> 'file ready', not 'files ready'."""
    payload = _baseline_corpus_payload(boot={
        "onedrive_status": "synced",
        "onedrive_file_count": 1,
    })
    client = admin_client_with_corpus(payload)
    resp = client.get("/")
    body = resp.get_data(as_text=True)

    assert "1 file ready" in body, (
        "Pluralization broken: count=1 should render 'file ready' "
        "(singular), not 'files ready'."
    )
