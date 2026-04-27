"""Round 26 / Phase A + E -- Admin AdoptIQ Intelligence tile regression tests.

Pins the renamed corpus tile in ``enhanced_admin_dashboard_v2`` (heading,
in-progress status line, per-source table, capped error list, button labels)
by rendering ``GET /`` on the admin app with ``requests.get`` stubbed to
return a synthetic ``/api/corpus/status`` payload.
"""

from __future__ import annotations

import pytest

import enhanced_admin_dashboard_v2 as admin_mod


class _FakeGetResp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _base_boot(**overrides):
    return {
        "completed": True,
        "in_progress": False,
        "last_started_at": None,
        "last_finished_at": "2026-04-25T00:00:01Z",
        "last_error": None,
        "last_error_kind": None,
        "last_stats": None,
        "encrypted_path": None,
        "onedrive_root": "/tmp/onedrive_root",
        "last_sources": None,
        "sharepoint": None,
        **overrides,
    }


def _base_corpus():
    return {
        "files_total": 10,
        "files_parsed": 10,
        "customers": 3,
        "cases": 100,
        "chunks": 500,
        "schema_version": 1,
        "last_parsed_at": "2026-04-25T00:00:00Z",
        "indexed_at": "2026-04-25T00:00:01Z",
    }


def _corpus_status_payload(*, boot: dict | None = None, corpus: dict | None = None):
    return {
        "ok": True,
        "enabled": True,
        "available": True,
        "reason": "",
        "boot": boot if boot is not None else _base_boot(),
        "corpus": corpus if corpus is not None else _base_corpus(),
    }


def _fake_requests_get_factory(corpus_json: dict):
    """Return a ``requests.get`` stand-in that satisfies all main-app polls
    from ``enhanced_admin_dashboard`` and returns ``corpus_json`` for
    ``/api/corpus/status``.
    """

    def fake_get(url, timeout=2):  # noqa: ARG001
        u = str(url)
        if "/api/corpus/status" in u:
            return _FakeGetResp(200, corpus_json)
        if "/api/status/all" in u:
            return _FakeGetResp(200, [])
        if "/api/debug/verbose" in u:
            return _FakeGetResp(
                200,
                {"verbose_debug": False, "snowflake_query_count": 0},
            )
        return _FakeGetResp(404, {})

    return fake_get


def _get_dashboard_html(monkeypatch, corpus_json: dict) -> str:
    monkeypatch.setattr(
        admin_mod.requests,
        "get",
        _fake_requests_get_factory(corpus_json),
    )
    client = admin_mod.admin_app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


@pytest.mark.flask
def test_admin_tile_uses_intelligence_heading(monkeypatch):
    html = _get_dashboard_html(monkeypatch, _corpus_status_payload())
    assert "<h3>AdoptIQ Intelligence</h3>" in html
    assert "<h3>CSOne Knowledge Corpus</h3>" not in html


@pytest.mark.flask
def test_admin_tile_shows_in_progress_status(monkeypatch):
    boot = _base_boot(
        in_progress=True,
        last_started_at="2026-04-25T12:00:00Z",
    )
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "indexing in progress" in html
    assert "2026-04-25T12:00:00Z" in html


@pytest.mark.flask
def test_admin_tile_renders_per_source_breakdown(monkeypatch):
    sources = [
        {
            "label": "onedrive",
            "dir": "/path/onedrive",
            "files_seen": 5,
            "files_parsed": 5,
            "files_skipped": 0,
            "files_failed": 0,
            "chunks_added": 25,
        },
        {
            "label": "intel_uploads",
            "dir": "/path/intel_uploads",
            "files_seen": 2,
            "files_parsed": 2,
            "files_skipped": 0,
            "files_failed": 0,
            "chunks_added": 8,
        },
    ]
    boot = _base_boot(last_sources=sources)
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "onedrive" in html
    assert "intel_uploads" in html
    assert "/path/intel_uploads" in html
    assert "Per-source breakdown" in html


@pytest.mark.flask
def test_admin_tile_renders_recent_errors_when_present(monkeypatch):
    boot = _base_boot(
        last_stats={
            "errors": [
                {"file": "broken.xlsx", "reason": "Corrupt zip"},
                {"file": "too_big.xlsx", "reason": "Exceeds 10 MB cap"},
            ],
        },
    )
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "Recent index errors" in html
    assert "broken.xlsx" in html
    assert "Corrupt zip" in html
    assert "too_big.xlsx" in html


@pytest.mark.flask
def test_admin_tile_caps_recent_errors_to_five(monkeypatch):
    errors = [
        {"file": f"error_{i}.xlsx", "reason": f"reason {i}"}
        for i in range(10)
    ]
    boot = _base_boot(last_stats={"errors": errors})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    for i in range(5):
        assert f"error_{i}.xlsx" in html
    for i in range(5, 10):
        assert f"error_{i}.xlsx" not in html


@pytest.mark.flask
def test_admin_tile_buttons_renamed(monkeypatch):
    html = _get_dashboard_html(monkeypatch, _corpus_status_payload())
    assert ">Run incremental<" in html
    assert ">Rebuild<" in html
    assert ">Refresh Corpus<" not in html
    assert ">Force Rebuild<" not in html


@pytest.mark.flask
def test_admin_tile_renders_error_count_when_errors_is_int(monkeypatch):
    """Round 26 - review (R26-001): the production payload from
    ``corpus_bootstrap._index_stats_to_dict`` exposes
    ``last_stats.errors`` as an INTEGER COUNT (filenames suppressed
    on purpose to avoid PII leakage).  The original Round 26 Phase E
    template assumed a list-of-dicts shape and silently broke (slicing
    an int raises TypeError); the existing list-shape tests passed
    only because they faked the dict shape.

    This test pins the int-shape branch of the dual-shape rendering
    so a regression that re-removes the count branch (or a refactor
    that changes the production shape without updating the template)
    is caught immediately.
    """
    boot = _base_boot(last_stats={"errors": 3})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "Recent index errors" in html
    assert "3 files failed" in html
    # PII-safety: no individual filenames / paths should appear in the
    # rendered tile when the count-only shape is in play.
    assert "Filenames suppressed" in html


@pytest.mark.flask
def test_admin_tile_singular_error_count_uses_singular_label(monkeypatch):
    """R26-001: 1 error => "1 file failed" (singular), not "1 files"."""
    boot = _base_boot(last_stats={"errors": 1})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "1 file failed" in html
    assert "1 files failed" not in html


@pytest.mark.flask
def test_admin_tile_does_not_render_errors_section_when_zero(monkeypatch):
    """R26-001: ``errors=0`` is the steady-state happy path.  The
    Recent index errors block must NOT appear (no false alarm in the
    operator's eye-line)."""
    boot = _base_boot(last_stats={"errors": 0})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "Recent index errors" not in html
