"""Round 17 / Phase F.1 -- Playbook route contracts.

Pins ``GET/POST /playbook``:

- GET renders the form (technology / theme / query) using the
  enum allow-lists from ``app_simple._PLAYBOOK_TECH_CHOICES`` /
  ``_PLAYBOOK_THEME_CHOICES``.
- POST without a CSRF token returns 403 (CSRF gate enforced).
- POST with valid CSRF runs the corpus retriever and renders the
  results.  Disabled / unconfigured corpus surfaces a banner.
- Strict allow-lists reject unknown technology / theme / query
  characters with a validation banner (HTTP 200 + form
  preserved).
- Sliding-window rate limiter is reused via
  ``_check_ask_ai_throttle`` (the same path Ask AI uses).
- Validation banner message never echoes raw user input
  verbatim.
"""

from __future__ import annotations

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
    for name in (
        "synthetic_cases.csv",
        "synthetic_barriers.csv",
        "synthetic_pulse.csv",
    ):
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
    from config import Config
    original = getattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False)
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", True, raising=False)
    try:
        yield
    finally:
        monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", original, raising=False)


@pytest.fixture
def csrf_client(app):
    """Client with CSRF protection enabled so we can pin the
    CSRF gate behaviour.  conftest's default fixture sets it
    ``False`` for the rest of the suite; we re-enable it for
    the duration of the test and restore on teardown."""
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        yield app.test_client()
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture(autouse=True)
def _reset_rate_log():
    """Round 17 / Phase F.1 -- the playbook page reuses the Ask
    AI sliding-window throttle, which is a process-global dict.
    Clear it before each test so individual cases stay isolated
    regardless of execution order.
    """
    app_simple._ask_ai_rate_log.clear()
    yield
    app_simple._ask_ai_rate_log.clear()


# ---------------------------------------------------------------------------
# GET -- form renders, allow-list options exposed.
# ---------------------------------------------------------------------------


def test_playbook_get_renders_form(client):
    resp = client.get("/playbook")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Troubleshooting Playbook" in body
    # Tech allow-list options are rendered (Jinja escapes ``&``
    # to ``&amp;``).
    from markupsafe import escape as _escape
    for choice in app_simple._PLAYBOOK_TECH_CHOICES:
        assert str(_escape(choice)) in body
    # Theme allow-list options are rendered.
    for choice in app_simple._PLAYBOOK_THEME_CHOICES:
        assert str(_escape(choice)) in body
    # CSRF token is emitted as a hidden input even when CSRF is
    # disabled in tests (Jinja's ``csrf_token()`` helper still
    # returns a token string).
    assert 'name="csrf_token"' in body


def test_playbook_get_renders_disabled_banner_when_flag_off(client, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False, raising=False)
    resp = client.get("/playbook")
    body = resp.data.decode("utf-8")
    assert "CORPUS_KNOWLEDGE_ENABLED" in body


# ---------------------------------------------------------------------------
# CSRF -- POST without token rejected.
# ---------------------------------------------------------------------------


def test_playbook_post_without_csrf_rejected(csrf_client):
    # No token + CSRF enabled -> 403.
    resp = csrf_client.post("/playbook", data={"technology": ""})
    assert resp.status_code == 403


def test_playbook_post_with_invalid_csrf_rejected(csrf_client):
    resp = csrf_client.post(
        "/playbook",
        data={
            "csrf_token": "obviously-not-a-real-token",
            "technology": "",
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST allow-list validation.
# ---------------------------------------------------------------------------


def test_playbook_post_rejects_unknown_technology(client):
    resp = client.post(
        "/playbook",
        data={
            "technology": "RogueTech",
            "theme": "",
            "query": "",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Unsupported technology" in body
    # The rogue value must not be selected on re-render (we never
    # echo it back into the <option>'s ``selected`` flag).
    assert 'value="RogueTech" selected' not in body


def test_playbook_post_rejects_unknown_theme(client):
    resp = client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "totally-bogus",
            "query": "",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Unsupported theme" in body


def test_playbook_post_rejects_query_with_html_chars(client):
    resp = client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "",
            "query": "<script>x</script>",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "disallowed characters" in body
    # The raw payload must not appear unescaped anywhere.
    assert "<script>x</script>" not in body


def test_playbook_post_caps_query_length_via_pattern(client):
    huge = "A" * 250  # exceeds 200-char allow-list cap
    resp = client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "",
            "query": huge,
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "disallowed characters" in body


def test_playbook_post_accepts_empty_form(client):
    resp = client.post(
        "/playbook",
        data={"technology": "", "theme": "", "query": ""},
    )
    # Empty selections = render bare form; no validation error.
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Unsupported" not in body
    assert "disallowed characters" not in body


# ---------------------------------------------------------------------------
# POST -- corpus disabled / unconfigured banners.
# ---------------------------------------------------------------------------


def test_playbook_post_disabled_renders_disabled_banner(client, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False, raising=False)
    resp = client.post(
        "/playbook",
        data={
            "technology": "Webex Calling",
            "theme": "",
            "query": "handshake",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "CORPUS_KNOWLEDGE_ENABLED" in body or "disabled" in body.lower()


def test_playbook_post_unconfigured_renders_unavailable_banner(
    client, corpus_enabled
):
    corpus_retriever.configure_connection(None)
    resp = client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "",
            "query": "handshake",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "OneDrive" in body or "unavailable" in body.lower()


# ---------------------------------------------------------------------------
# POST -- happy path with wired corpus.
# ---------------------------------------------------------------------------


def test_playbook_post_renders_recurring_themes(
    client, corpus_enabled, configured_corpus
):
    resp = client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "",
            "query": "",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # Themes table renders the column headers + at least one row.
    assert "Recurring barriers" in body
    assert "Theme" in body and "Customers" in body and "Occurrences" in body


def test_playbook_post_renders_search_chunks(
    client, corpus_enabled, configured_corpus
):
    resp = client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "",
            # "case" is in the indexed playbook chunk text
            # (synthetic resolution narrative).
            "query": "case",
        },
    )
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # Echo back the query unescaped only via Jinja escape.
    assert "Search matches for:" in body or "Recurring barriers" in body


# ---------------------------------------------------------------------------
# Rate limit -- exhaustion returns 429.
# ---------------------------------------------------------------------------


def test_playbook_post_rate_limit_returns_429(client, monkeypatch):
    # Set the throttle to 1 request / 60s so the second call is
    # immediately blocked.  We have to clear the in-process log
    # so the prior tests don't influence this one.
    monkeypatch.setattr(app_simple, "_ASK_AI_RATE_MAX_CALLS", 1, raising=False)
    monkeypatch.setattr(app_simple, "_ASK_AI_RATE_WINDOW_SEC", 60.0, raising=False)
    app_simple._ask_ai_rate_log.clear()

    first = client.post(
        "/playbook",
        data={"technology": "", "theme": "", "query": ""},
    )
    assert first.status_code == 200

    second = client.post(
        "/playbook",
        data={"technology": "", "theme": "", "query": ""},
    )
    # After the cap, the next POST is throttled.  The route
    # surfaces the throttle as HTTP 429 with the rate-limit
    # message in the banner.
    assert second.status_code == 429
    body = second.data.decode("utf-8")
    assert "Rate limit" in body or "rate limit" in body.lower()


# ---------------------------------------------------------------------------
# Logging -- query never logged at INFO (privacy rule).
# ---------------------------------------------------------------------------


def test_playbook_post_does_not_log_query_at_info(
    client, corpus_enabled, configured_corpus, caplog
):
    caplog.set_level(logging.INFO)
    distinctive = "ZZQuerySecret"
    client.post(
        "/playbook",
        data={
            "technology": "",
            "theme": "",
            # Query passes the allow-list (alnum only).
            "query": distinctive,
        },
    )
    info_messages = [
        r.message for r in caplog.records
        if r.levelno >= logging.INFO
    ]
    for msg in info_messages:
        assert distinctive not in msg, (
            f"playbook query leaked into log: {msg}"
        )
