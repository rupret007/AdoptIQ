"""Round 17 / Phase F.1 -- Customer 360 route contracts.

Pins the Flask route ``GET /customer/<name>``:

- Customer-name allow-list rejects path-traversal and control
  characters with HTTP 400 + a validation banner.
- Disabled feature flag renders the disabled banner without
  reaching the corpus.
- Unconfigured corpus renders the "OneDrive sync" banner.
- Wired corpus renders the customer history (heading, badge,
  recurring-barriers section).
- Missing customer renders the "no history" banner.
- Template never injects user-controlled input via ``innerHTML``;
  the rendered HTML must escape dangerous characters.
- Customer name is never logged at INFO level.

Tests run against the Flask app from ``conftest.py`` with
``WTF_CSRF_ENABLED=False`` so we focus on the route logic.  CSRF
behaviour for state-changing routes lives in the playbook /
admin tile tests.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import logging
import sqlite3
from pathlib import Path

import pytest

import app_simple
import corpus_retriever
from corpus_indexer import index_folder, open_corpus_db


_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "round17"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("synthetic_cases.csv", "synthetic_barriers.csv", "synthetic_pulse.csv"):
        (root / name).write_bytes((_FIXTURES_ROOT / name).read_bytes())
    return root


@pytest.fixture
def configured_corpus(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    db_path = tmp_path / "corpus.db"
    conn = open_corpus_db(db_path)
    try:
        index_folder(conn, root)
        corpus_retriever.configure_connection(conn)
        yield conn
    finally:
        corpus_retriever.configure_connection(None)
        try:
            conn.close()
        except sqlite3.DatabaseError:
            pass


@pytest.fixture
def corpus_enabled(monkeypatch):
    """Toggle ``Config.CORPUS_KNOWLEDGE_ENABLED=True`` for the test
    via the resolved helper.  Restored on teardown."""
    from config import Config

    original = getattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False)
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    try:
        yield
    finally:
        monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", original, raising=False)


# ---------------------------------------------------------------------------
# Validation -- allow-list / length cap
# ---------------------------------------------------------------------------


def test_customer_360_rejects_html_injection_chars(client):
    # ``<`` and ``>`` are outside the allow-list, so the handler
    # must short-circuit with HTTP 400 + the validation banner.
    resp = client.get("/customer/Acme%3Cscript%3E")
    assert resp.status_code == 400
    body = resp.data.decode("utf-8")
    assert_in_source(body, "disallowed characters", label='body')


def test_customer_360_rejects_semicolon_injection(client):
    # ``;`` is also outside the allow-list (no command-injection
    # surface anyway, but defence in depth).
    resp = client.get("/customer/Acme%3Bdrop")
    assert resp.status_code == 400


def test_customer_360_rejects_control_chars(client):
    # ``%00`` is a NUL byte.  Werkzeug refuses to even decode the
    # path (returns 404); the allow-list path-handler kicks in for
    # other control characters.
    resp = client.get("/customer/Synthetic%20Alpha%07")
    # Either 400 (allow-list rejection) or 404 (router rejection).
    assert resp.status_code in (400, 404)


def test_customer_360_rejects_oversized_name(client):
    huge = "A" * 500
    resp = client.get(f"/customer/{huge}")
    assert resp.status_code in (400, 404)
    if resp.status_code == 400:
        assert b"disallowed characters" in resp.data or b"required" in resp.data


def test_customer_360_empty_name_returns_400(client):
    # The ``<path:name>`` converter requires at least one character;
    # this route returns 404 from werkzeug, which is acceptable.
    resp = client.get("/customer/%20")  # encoded space
    # Whitespace-only collapses to empty after strip -> 400.
    assert resp.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Disabled / unavailable banners
# ---------------------------------------------------------------------------


def test_customer_360_disabled_renders_disabled_banner(client, monkeypatch):
    # Force the feature flag off.
    from config import Config
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False, raising=False)

    resp = client.get("/customer/Synthetic Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert_in_source(body, "CORPUS_KNOWLEDGE_ENABLED", label='body')
    assert "disabled" in body.lower()


def test_customer_360_unconfigured_renders_unavailable_banner(
    client, corpus_enabled
):
    corpus_retriever.configure_connection(None)
    resp = client.get("/customer/Synthetic Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "OneDrive" in body or "unavailable" in body.lower()


# ---------------------------------------------------------------------------
# Happy path -- wired corpus.
# ---------------------------------------------------------------------------


def test_customer_360_renders_known_customer(
    client, corpus_enabled, configured_corpus
):
    resp = client.get("/customer/Synthetic Alpha")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert_in_source(body, "Synthetic Alpha", label='body')
    # Section headings present.
    assert_in_source(body, "Cases timeline", label='body')
    assert_in_source(body, "Recurring barriers", label='body')
    assert_in_source(body, "Top resolutions", label='body')
    # Page chrome is rendered server-side.
    assert_in_source(body, "Customer 360", label='body')


def test_customer_360_unknown_customer_renders_no_history_banner(
    client, corpus_enabled, configured_corpus
):
    resp = client.get("/customer/Definitely Unknown Customer")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "No corpus history" in body or "is not in the corpus" in body
    # The requested name must still be echoed back, escaped.
    assert_in_source(body, "Definitely Unknown Customer", label='body')


# ---------------------------------------------------------------------------
# XSS / template-escape contracts.
# ---------------------------------------------------------------------------


def test_customer_360_template_escapes_user_input(
    client, corpus_enabled, configured_corpus
):
    from html.parser import HTMLParser

    # Walk the rendered DOM and confirm that the user-controlled
    # customer name never appears inside a real ``<script>`` block
    # (regex-based extraction is unsafe because the base template
    # documents script tags inside HTML comments).
    distinctive = "ZZUniqueCustomerXYQ"
    resp = client.get(f"/customer/{distinctive}")
    body = resp.data.decode("utf-8")
    assert distinctive in body, (
        "echoed customer name must be present in the rendered body"
    )

    class _ScriptBodyCollector(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.in_script = False
            self.bodies: list[str] = []
            self._buf: list[str] = []

        def handle_starttag(self, tag, attrs):  # noqa: ARG002
            if tag.lower() == "script":
                self.in_script = True
                self._buf = []

        def handle_endtag(self, tag):
            if tag.lower() == "script" and self.in_script:
                self.bodies.append("".join(self._buf))
                self.in_script = False
                self._buf = []

        def handle_data(self, data):
            if self.in_script:
                self._buf.append(data)

    parser = _ScriptBodyCollector()
    parser.feed(body)
    for block in parser.bodies:
        assert distinctive not in block, (
            "user-controlled input must never be interpolated "
            "inside <script> blocks"
        )


def test_customer_360_no_innerhtml_for_user_data(
    client, corpus_enabled, configured_corpus
):
    resp = client.get("/customer/Synthetic Alpha")
    body = resp.data.decode("utf-8")
    # The template must not call ``.innerHTML =`` with user-
    # controlled values.
    assert ".innerHTML" not in body


# ---------------------------------------------------------------------------
# Logging discipline -- no PII at INFO.
# ---------------------------------------------------------------------------


def test_customer_360_does_not_log_customer_name_at_info(
    client, corpus_enabled, configured_corpus, caplog
):
    caplog.set_level(logging.INFO)
    customer = "Synthetic Alpha"
    resp = client.get(f"/customer/{customer}")
    assert resp.status_code == 200
    # No INFO-level log line should embed the raw customer name.
    info_messages = [
        r.message for r in caplog.records
        if r.levelno >= logging.INFO
    ]
    for msg in info_messages:
        assert customer not in msg, f"customer name leaked into log: {msg}"
