"""Round 26 / Phase A + E -- Admin AdoptIQ Intelligence tile regression tests.

Pins the renamed corpus tile in ``enhanced_admin_dashboard_v2`` (heading,
in-progress status line, per-source table, capped error list, button labels)
by rendering ``GET /`` on the admin app with ``requests.get`` stubbed to
return a synthetic ``/api/corpus/status`` payload.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

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
    assert_in_source(html, "<h3>AdoptIQ Intelligence</h3>", label='html')
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
    assert_in_source(html, "indexing in progress", label='html')
    assert_in_source(html, "2026-04-25T12:00:00Z", label='html')


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
    assert_in_source(html, "onedrive", label='html')
    assert_in_source(html, "intel_uploads", label='html')
    assert_in_source(html, "/path/intel_uploads", label='html')
    assert_in_source(html, "Per-source breakdown", label='html')


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
    assert_in_source(html, "Recent index errors", label='html')
    assert_in_source(html, "broken.xlsx", label='html')
    assert_in_source(html, "Corrupt zip", label='html')
    assert_in_source(html, "too_big.xlsx", label='html')


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
        assert_in_source(html, f"error_{i}.xlsx", label='html')
    for i in range(5, 10):
        assert f"error_{i}.xlsx" not in html


@pytest.mark.flask
def test_admin_tile_buttons_renamed(monkeypatch):
    # Round 37 / Phase 4: the Round 26 "Run incremental" label was
    # renamed to "Re-index now" so it matches the analyze-page
    # corpus panel labelling.  The button still POSTs to
    # /corpus_refresh -- only the user-visible label changed.  The
    # multi-line ``<button ...>\n   Rebuild\n</button>`` formatting
    # (also Round 37) means we use ``re.search`` here instead of
    # the original strict ``>Rebuild<`` substring.
    import re
    html = _get_dashboard_html(monkeypatch, _corpus_status_payload())
    assert_in_source(html, "Re-index now", label='html')
    assert re.search(r">\s*Rebuild\s*</button>", html), "Rebuild button missing"
    assert ">Run incremental<" not in html  # Round 37 retired this label
    assert ">Refresh Corpus<" not in html
    assert ">Force Rebuild<" not in html


@pytest.mark.flask
def test_admin_tile_legacy_int_error_count_still_renders(monkeypatch):
    """Round 26 - review (R26-OPEN-001): the production payload now
    emits a list of ``{file, reason}`` dicts plus ``errors_total``,
    but a stale subprocess / downgraded build can still emit the
    legacy integer count.  The dual-shape template branch keeps the
    int-shape path working as a defensive fallback so a downgrade
    doesn't silently swallow failures.

    This test pins the legacy-int branch.  It also asserts the old
    "Filenames suppressed to avoid PII leakage" copy is gone --
    AdoptIQ is internal-only and the operator's question is now
    "which files failed?", not "which customer".
    """
    boot = _base_boot(last_stats={"errors": 3})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert_in_source(html, "Recent index errors", label='html')
    assert_in_source(html, "3 files failed", label='html')
    # R26-OPEN-001: PII-suppression copy removed for internal deployment.
    assert "Filenames suppressed to avoid PII" not in html
    # Legacy fallback should hint at log-tailing instead of pretending
    # the suppression is intentional.
    assert_in_source(html, "Legacy payload shape", label='html')


@pytest.mark.flask
def test_admin_tile_singular_error_count_uses_singular_label(monkeypatch):
    """R26-OPEN-001: 1 error => "1 file failed" (singular), not "1 files"."""
    boot = _base_boot(last_stats={"errors": 1})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert_in_source(html, "1 file failed", label='html')
    assert "1 files failed" not in html


@pytest.mark.flask
def test_admin_tile_does_not_render_errors_section_when_zero(monkeypatch):
    """R26-OPEN-001: ``errors=0`` is the steady-state happy path.  The
    Recent index errors block must NOT appear (no false alarm in the
    operator's eye-line)."""
    boot = _base_boot(last_stats={"errors": 0})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert "Recent index errors" not in html


@pytest.mark.flask
def test_admin_tile_renders_n_of_m_when_errors_truncated(monkeypatch):
    """R26-OPEN-001: when ``errors_total`` exceeds the rendered list
    length, the tile must surface "N of M shown" so operators know
    log-tailing is required for the rest.

    The marshaller caps the list at 5 and exposes the full count via
    ``errors_total``.  Here we feed 5 dicts + ``errors_total=12`` and
    assert the truncation summary appears.
    """
    errors = [
        {"file": f"err_{i}.xlsx", "reason": f"r{i}"} for i in range(5)
    ]
    boot = _base_boot(last_stats={"errors": errors, "errors_total": 12})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert_in_source(html, "Recent index errors", label='html')
    assert_in_source(html, "5 of 12 shown", label='html')


@pytest.mark.flask
def test_admin_tile_omits_n_of_m_when_no_truncation(monkeypatch):
    """R26-OPEN-001: when the full list fits (``errors_total`` ==
    ``len(errors)``) the truncation summary must NOT appear -- the
    operator already sees every error inline."""
    errors = [
        {"file": "err_a.xlsx", "reason": "a"},
        {"file": "err_b.xlsx", "reason": "b"},
    ]
    boot = _base_boot(last_stats={"errors": errors, "errors_total": 2})
    html = _get_dashboard_html(
        monkeypatch,
        _corpus_status_payload(boot=boot),
    )
    assert_in_source(html, "Recent index errors", label='html')
    assert "of 2 shown" not in html
    # The "(last bootstrap pass)" subhead should appear instead.
    assert_in_source(html, "last bootstrap pass", label='html')


@pytest.mark.flask
def test_admin_tile_no_pii_suppression_copy_anywhere(monkeypatch):
    """R26-OPEN-001: AdoptIQ is internal-only.  The original Round 26
    Phase E PII-suppression subtext must not appear in any error-shape
    branch (int, list-of-dicts, or zero).  Pin the copy removal so a
    later refactor doesn't accidentally restore it."""
    cases = [
        {"errors": 3},
        {"errors": [{"file": "x.xlsx", "reason": "boom"}]},
        {"errors": [], "errors_total": 0},
    ]
    for last_stats in cases:
        boot = _base_boot(last_stats=last_stats)
        html = _get_dashboard_html(
            monkeypatch,
            _corpus_status_payload(boot=boot),
        )
        assert "Filenames suppressed to avoid PII" not in html
        assert "PII leakage" not in html
