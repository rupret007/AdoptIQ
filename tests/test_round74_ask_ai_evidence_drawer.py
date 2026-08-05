"""Round 74 / Build 48 / Phase 4 (P4): Ask AI evidence drawer tests.

Pin the new ``GET /api/ask-ai/evidence/<query_id>/<source_id>``
lookup endpoint, the synchronous endpoint's new ``evidence_records``
field, and the source-shape of the Bootstrap Offcanvas drawer +
its click + keyboard wiring in ``static/js/ask_ai.js``.
"""
from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"
_ASK_AI_JS = _REPO_ROOT / "static" / "js" / "ask_ai.js"
_ASK_AI_HTML = _REPO_ROOT / "templates" / "ask_ai.html"


# ---------------------------------------------------------------------------
# Backend tests
# ---------------------------------------------------------------------------


def test_evidence_lookup_route_exists():
    """``GET /api/ask-ai/evidence/<query_id>/<source_id>`` must be
    registered with both query_id and source_id path params.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    pattern = (
        r"@app\.route\(\s*['\"]/api/ask-ai/evidence/<query_id>/<source_id>['\"]"
        r"[\s\S]*?methods\s*=\s*\[\s*['\"]GET['\"]\s*\]"
    )
    assert re.search(pattern, src), (
        "Round 74 / P4: evidence lookup route must be registered as "
        "GET /api/ask-ai/evidence/<query_id>/<source_id> "
        "(the drawer's late-binding fallback URL)."
    )


def test_evidence_lookup_endpoint_uses_diag_buffer():
    """The lookup endpoint must read records from
    ``_get_ask_ai_query_diag`` (the same store the diag endpoint uses).
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_evidence_lookup\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m, "could not locate ask_ai_evidence_lookup body"
    body = m.group(0)
    assert '_get_ask_ai_query_diag' in body, (
        "Round 74 / P4: evidence lookup must use _get_ask_ai_query_diag "
        "so it transparently falls back to the SQLite sidecar (R68/C2)."
    )
    assert '_r74_evidence_records' in body, (
        "Round 74 / P4: evidence lookup must read the new "
        "_r74_evidence_records field from the diag payload."
    )


def test_evidence_lookup_returns_404_for_unknown_query_id():
    """Unknown query_id must return HTTP 404 with an explicit error."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_evidence_lookup\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m
    body = m.group(0)
    assert "'unknown query_id'" in body or '"unknown query_id"' in body, (
        "Round 74 / P4: missing query id must surface as a clear error string"
    )
    assert ", 404" in body, (
        "Round 74 / P4: unknown query_id must return HTTP 404"
    )


def test_evidence_lookup_returns_404_for_unknown_source_id():
    """Unknown source_id within a known query must return HTTP 404."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_evidence_lookup\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m
    body = m.group(0)
    assert "'unknown source_id'" in body or '"unknown source_id"' in body, (
        "Round 74 / P4: missing source id must surface as a clear error string"
    )


def test_evidence_lookup_rate_limited_via_r71():
    """The lookup endpoint must inherit the R71 per-IP rate limit so
    a runaway page can't spin the SQLite store.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    m = re.search(
        r"def ask_ai_evidence_lookup\([^)]*\):[\s\S]*?(?=\n@app\.route|\ndef\s)",
        src,
    )
    assert m
    body = m.group(0)
    assert '_r71_diag_rate_limit_check' in body, (
        "Round 74 / P4: evidence lookup must use the R71 rate limiter"
    )


def test_sync_endpoint_response_carries_evidence_records():
    """The synchronous ``/api/ask-ai-portfolio`` JSON response must
    carry an ``evidence_records`` field for the drawer.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # The grounded sync payload must include both projected values.
    # Match either quote style and arbitrary formatter line wrapping.
    pattern = (
        r"[\"']evidence_index[\"']\s*:\s*_public_evidence_index"
        r"[\s\S]*?"
        r"[\"']evidence_records[\"']\s*:\s*_public_evidence_records"
    )
    assert re.search(pattern, src), (
        "Round 74 / P4: synchronous ask-ai response must include "
        "projected evidence_index and evidence_records so the drawer "
        "doesn't need a round-trip for the common case."
    )


def test_diag_persistence_includes_r74_evidence_records():
    """Both sync + streaming paths must persist
    ``_r74_evidence_records`` on the diag payload so the lookup
    endpoint can resolve them later.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Count BOTH quoting styles -- the sync path uses single quotes
    # while the streaming path uses double quotes (PEP-8-clean both).
    single_q = src.count("'_r74_evidence_records'")
    double_q = src.count('"_r74_evidence_records"')
    bare = src.count('_r74_evidence_records')
    # Bare-token count should be >= 3 (sync persistence + streaming
    # persistence + lookup endpoint reader, plus any docstring
    # mentions).  Quoted occurrences should be >= 2 (one in each
    # write site).
    assert (single_q + double_q) >= 2, (
        f"Round 74 / P4: '_r74_evidence_records' should appear as a "
        f"key in BOTH the sync and streaming diag persistence sites "
        f"(saw {single_q} single-quoted + {double_q} double-quoted)."
    )
    assert bare >= 3, (
        f"Round 74 / P4: bare-token count for _r74_evidence_records "
        f"should be at least 3 (sync persistence + streaming "
        f"persistence + lookup endpoint reader); saw {bare}."
    )


def test_evidence_record_payload_size_capped():
    """The diag persistence must cap individual record bodies so a
    runaway corpus snippet cannot blow up the SQLite blob.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    # Look for the 32_000-char cap applied to ``content`` /
    # ``full_record`` / ``snippet`` (the three common large fields).
    assert '32_000' in src, (
        "Round 74 / P4: evidence record bodies must be capped at 32 KB "
        "before persisting to the diag store."
    )


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------


def test_offcanvas_drawer_in_template():
    """The Bootstrap Offcanvas drawer must be present in ask_ai.html."""
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'id="r74EvidenceDrawer"' in html, (
        "Round 74 / P4: ask_ai.html must declare #r74EvidenceDrawer"
    )
    assert 'class="offcanvas offcanvas-end"' in html, (
        "Round 74 / P4: drawer must use Bootstrap's offcanvas-end variant"
    )
    assert 'data-bs-dismiss="offcanvas"' in html, (
        "Round 74 / P4: drawer must include Bootstrap's dismiss control"
    )


def test_offcanvas_drawer_has_aria_attributes():
    """The drawer must be screen-reader accessible."""
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'aria-labelledby="r74EvidenceDrawerLabel"' in html, (
        "Round 74 / P4: drawer must declare aria-labelledby for a11y"
    )
    assert 'role="dialog"' in html, (
        "Round 74 / P4: drawer must declare role='dialog'"
    )


def test_drawer_body_and_status_elements_present():
    """The drawer body + status alert containers must exist so the
    JS can populate them on click.
    """
    html = _ASK_AI_HTML.read_text(encoding="utf-8")
    assert 'id="r74EvidenceDrawerBody"' in html
    assert 'id="r74EvidenceDrawerStatus"' in html
    assert 'id="r74EvidenceDrawerLabel"' in html


# ---------------------------------------------------------------------------
# Client-side tests
# ---------------------------------------------------------------------------


def test_ask_ai_js_defines_r74_set_evidence_records():
    """``_r74SetEvidenceRecords`` must exist + populate the in-memory
    index keyed by source_id.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'function _r74SetEvidenceRecords(records, queryId)' in src, (
        "Round 74 / P4: client must define _r74SetEvidenceRecords"
    )
    assert '_r74CurrentQueryId' in src, (
        "Round 74 / P4: client must track the current query id for "
        "the evidence-lookup fallback"
    )


def test_ask_ai_js_drawer_open_handler_wired():
    """The badge click + keydown handlers must be wired via delegated
    listeners on ``answerContent``.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "answerContent.addEventListener('click'" in src, (
        "Round 74 / P4: drawer must use a delegated click listener "
        "on #answerContent (badges are dynamically rendered)"
    )
    assert "answerContent.addEventListener('keydown'" in src, (
        "Round 74 / P4: drawer must support Enter / Space keyboard "
        "activation for accessibility"
    )
    assert 'r74-source-badge' in src, (
        "Round 74 / P4: handler must filter on .r74-source-badge"
    )


def test_ask_ai_js_drawer_uses_bootstrap_offcanvas():
    """The drawer must use Bootstrap's Offcanvas API for show/hide."""
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert 'window.bootstrap.Offcanvas' in src, (
        "Round 74 / P4: drawer must use the Bootstrap Offcanvas API"
    )


def test_ask_ai_js_drawer_falls_back_to_lookup_endpoint():
    """When the in-memory index has no record, the drawer must call
    the new ``GET /api/ask-ai/evidence/<query_id>/<source_id>`` endpoint.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert "'/api/ask-ai/evidence/'" in src, (
        "Round 74 / P4: drawer must fall back to the lookup endpoint "
        "on an in-memory index miss (e.g. after a page reload)."
    )
    assert 'encodeURIComponent(_r74CurrentQueryId)' in src, (
        "Round 74 / P4: drawer URL must encode the query id"
    )


def test_ask_ai_js_drawer_handles_malformed_lookup_response():
    """A failed lookup must surface a clear status message instead
    of crashing or showing a stale record.
    """
    src = _ASK_AI_JS.read_text(encoding="utf-8")
    assert '_r74SetDrawerStatus' in src, (
        "Round 74 / P4: drawer must have a status setter for errors"
    )
    assert "_r74SetDrawerStatus(" in src and "'error'" in src, (
        "Round 74 / P4: drawer must surface lookup failures as "
        "alert-danger status messages, not silent failures"
    )
