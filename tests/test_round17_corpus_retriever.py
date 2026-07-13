"""Round 17 / Phase F.1 -- corpus_retriever facade contracts.

Pins the read-side facade:

- All public methods raise :class:`CorpusUnavailable` when no
  connection is configured.
- ``configure_connection(None)`` clears prior wiring.
- ``get_status`` never raises -- on failure it returns
  ``available=False`` with a non-empty ``reason``.
- ``get_customer_history`` returns a typed :class:`CustomerHistory`
  with cases, barriers, sentiment trend, and top resolutions.
- Input validation (length, allow-list, traversal) rejects hostile
  strings.
- ``search_playbook`` returns BM25-ranked chunks and respects
  optional technology/theme filters.
- ``list_customers`` honours ``prefix`` and ``limit``.

Tests use a freshly-indexed in-memory SQLite database backed by the
synthetic CSV fixtures so the facade is exercised end-to-end without
the encryption layer (the encryption layer has its own tests).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import corpus_retriever
from corpus_indexer import index_folder, open_corpus_db
from corpus_retriever import (
    BarrierRecord,
    CaseRecord,
    Chunk,
    CorpusSnapshot,
    CorpusUnavailable,
    CustomerHistory,
    ResolutionRecord,
    SentimentSnapshot,
    Theme,
    configure_connection,
    get_customer_history,
    get_recurring_themes,
    get_resolutions_for,
    get_status,
    is_configured,
    list_customers,
    search_playbook,
)


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
    """Build a fresh in-memory-style corpus DB on disk, populate it
    from the synthetic fixtures, and wire it into the retriever.

    Yields the open connection so tests can introspect rows
    directly when needed.  ``configure_connection(None)`` is called
    in teardown so subsequent tests start clean."""
    root = _seed_corpus(tmp_path)
    db_path = tmp_path / "corpus.db"
    conn = open_corpus_db(db_path)
    try:
        index_folder(conn, root)
        configure_connection(conn)
        yield conn
    finally:
        configure_connection(None)
        try:
            conn.close()
        except sqlite3.DatabaseError:
            pass


# ---------------------------------------------------------------------------
# Connection wiring
# ---------------------------------------------------------------------------


def test_unconfigured_retriever_raises_corpus_unavailable():
    configure_connection(None)
    assert is_configured() is False
    with pytest.raises(CorpusUnavailable):
        get_customer_history("Synthetic Alpha")
    with pytest.raises(CorpusUnavailable):
        get_recurring_themes("security")
    with pytest.raises(CorpusUnavailable):
        get_resolutions_for("authentication")
    with pytest.raises(CorpusUnavailable):
        search_playbook("auth")
    with pytest.raises(CorpusUnavailable):
        list_customers()


def test_get_status_when_unconfigured_returns_unavailable():
    configure_connection(None)
    snap = get_status()
    assert isinstance(snap, CorpusSnapshot)
    assert snap.available is False
    assert snap.reason
    # Sanity defaults.
    assert snap.files_total == 0
    assert snap.cases == 0


def test_configure_connection_sets_row_factory():
    configure_connection(None)
    conn = sqlite3.connect(":memory:")
    try:
        configure_connection(conn)
        assert conn.row_factory is sqlite3.Row
    finally:
        configure_connection(None)
        conn.close()


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


def test_get_status_after_indexing(configured_corpus):
    snap = get_status()
    assert isinstance(snap, CorpusSnapshot)
    assert snap.available is True
    assert snap.files_total == 3
    assert snap.files_parsed == 3
    assert snap.customers >= 3
    assert snap.cases >= 5
    assert snap.chunks > 0
    assert snap.schema_version >= 1
    assert snap.reason is None


# ---------------------------------------------------------------------------
# get_customer_history
# ---------------------------------------------------------------------------


def test_get_customer_history_returns_typed_dataclass(configured_corpus):
    history = get_customer_history("Synthetic Alpha")
    assert isinstance(history, CustomerHistory)
    assert history.name.lower().startswith("synthetic alpha")
    assert isinstance(history.cases, tuple)
    assert all(isinstance(c, CaseRecord) for c in history.cases)
    assert all(isinstance(b, BarrierRecord) for b in history.barriers)
    assert all(isinstance(s, SentimentSnapshot) for s in history.sentiment_trend)
    assert all(isinstance(r, ResolutionRecord) for r in history.top_resolutions)
    assert len(history.cases) >= 1
    assert any(c.case_number == "TAC9001" for c in history.cases)


def test_get_customer_history_normalizes_case_and_whitespace(configured_corpus):
    canonical = get_customer_history("Synthetic Alpha")
    variant = get_customer_history("  synthetic   ALPHA  ")
    assert canonical.name == variant.name
    assert len(canonical.cases) == len(variant.cases)


def test_get_customer_history_rejects_unknown_customer(configured_corpus):
    with pytest.raises(CorpusUnavailable):
        get_customer_history("No Such Customer")


def test_get_customer_history_rejects_traversal_strings(configured_corpus):
    with pytest.raises(CorpusUnavailable):
        get_customer_history("../../etc/passwd")


def test_get_customer_history_rejects_empty_input(configured_corpus):
    with pytest.raises(CorpusUnavailable):
        get_customer_history("")
    with pytest.raises(CorpusUnavailable):
        get_customer_history("   ")
    with pytest.raises(CorpusUnavailable):
        get_customer_history(None)


def test_get_customer_history_truncates_oversized_input(configured_corpus):
    # Length cap: 500 chars max; a 1000-char string should be
    # truncated and matched by allow-list, but no real customer has
    # this name -> CorpusUnavailable.
    huge = "Synthetic Alpha " + ("X" * 2000)
    with pytest.raises(CorpusUnavailable):
        get_customer_history(huge)


def test_get_customer_history_includes_sentiment_for_alpha(configured_corpus):
    history = get_customer_history("Synthetic Alpha")
    assert len(history.sentiment_trend) >= 1
    # Sentiment scores must be numeric or None.
    for snap in history.sentiment_trend:
        assert snap.score is None or isinstance(snap.score, float)


# ---------------------------------------------------------------------------
# get_recurring_themes
# ---------------------------------------------------------------------------


def test_get_recurring_themes_returns_typed_results(configured_corpus):
    themes = get_recurring_themes("security", top_k=5)
    assert isinstance(themes, list)
    assert all(isinstance(t, Theme) for t in themes)
    # At least one security-themed barrier from the synthetic
    # fixtures.
    assert any(t.theme for t in themes)


def test_get_recurring_themes_with_no_filter_returns_results(configured_corpus):
    themes = get_recurring_themes(None, top_k=20)
    assert len(themes) >= 1


def test_get_recurring_themes_sorted_by_occurrences_desc(configured_corpus):
    themes = get_recurring_themes(None, top_k=20)
    occurrences = [t.occurrences for t in themes]
    assert occurrences == sorted(occurrences, reverse=True)


def test_get_recurring_themes_top_k_capped(configured_corpus):
    themes = get_recurring_themes(None, top_k=2)
    assert len(themes) <= 2


def test_get_recurring_themes_top_k_zero_clamped_to_one(configured_corpus):
    themes = get_recurring_themes(None, top_k=0)
    # The retriever clamps top_k to >=1.
    assert len(themes) <= 1


# ---------------------------------------------------------------------------
# get_resolutions_for
# ---------------------------------------------------------------------------


def test_get_resolutions_for_returns_resolution_records(configured_corpus):
    out = get_resolutions_for("authentication", "security", limit=5)
    assert isinstance(out, list)
    assert all(isinstance(r, ResolutionRecord) for r in out)


def test_get_resolutions_for_rejects_disallowed_chars(configured_corpus):
    # Control characters / NULs are outside the allow-list and must
    # be rejected by the input-validation guard.
    with pytest.raises(CorpusUnavailable):
        get_resolutions_for("auth\x00ication")


def test_get_resolutions_for_unknown_theme_returns_empty(configured_corpus):
    out = get_resolutions_for("unobserved_theme_xyz", "security")
    assert out == []


# ---------------------------------------------------------------------------
# search_playbook
# ---------------------------------------------------------------------------


def test_search_playbook_returns_typed_chunks(configured_corpus):
    out = search_playbook("authentication SSO token", top_k=5)
    assert isinstance(out, list)
    assert all(isinstance(c, Chunk) for c in out)
    # All scores strictly positive (zero-score chunks are filtered).
    assert all(c.score > 0.0 for c in out)


def test_search_playbook_returns_empty_for_query_with_only_stop_words(configured_corpus):
    # "the and or" are all stop-words -> tokenized to an empty list
    # -> no results.
    assert search_playbook("the and or") == []


def test_search_playbook_filters_by_technology(configured_corpus):
    out = search_playbook("token", technology="security", top_k=5)
    for chunk in out:
        if chunk.technology:
            assert chunk.technology.lower() == "security"


def test_search_playbook_orders_by_score_desc(configured_corpus):
    out = search_playbook("authentication token webhook", top_k=10)
    if len(out) >= 2:
        scores = [c.score for c in out]
        assert scores == sorted(scores, reverse=True)


def test_search_playbook_respects_top_k(configured_corpus):
    out = search_playbook("token", top_k=1)
    assert len(out) <= 1


def test_search_playbook_rejects_disallowed_query(configured_corpus):
    with pytest.raises(CorpusUnavailable):
        search_playbook("bad query \x00 with control chars")


# ---------------------------------------------------------------------------
# list_customers
# ---------------------------------------------------------------------------


def test_list_customers_returns_strings(configured_corpus):
    names = list_customers()
    assert isinstance(names, list)
    assert all(isinstance(n, str) and n for n in names)
    # Synthetic fixture has at least three customers.
    assert len(names) >= 3


def test_list_customers_respects_prefix(configured_corpus):
    names = list_customers(prefix="Synthetic Alpha")
    for n in names:
        assert n.lower().startswith("synthetic alpha")


def test_list_customers_respects_limit(configured_corpus):
    names = list_customers(limit=1)
    assert len(names) == 1


def test_list_customers_rejects_disallowed_prefix(configured_corpus):
    with pytest.raises(CorpusUnavailable):
        list_customers(prefix="bad\x00prefix")
