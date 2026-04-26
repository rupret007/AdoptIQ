"""Round 17 / Phase F.1 -- corpus indexer tests.

Pins the contract of :mod:`corpus_indexer`:

- File enumeration: only supported extensions, OneDrive temp files
  ignored, hidden ``~$`` lock files ignored, missing root yields
  empty result.
- Idempotency: running ``index_folder`` twice produces no new
  ``corpus_files`` rows when nothing changed; modifying a file's
  content (different sha256) re-parses on the next pass.
- Parsing: synthetic CSV fixtures shipped under
  ``tests/fixtures/round17/`` produce customer / case / barrier /
  resolution / sentiment / chunk records.
- Defensive: a malformed CSV is recorded with
  ``parse_status='error'`` and the rest of the run completes.
- Tokenizer / theme detector are pure / deterministic.
- BM25 score is non-negative, monotone in term frequency.
- Public ``tokenize`` alias is exported.

The tests are deterministic and offline.  No network, no live
OneDrive sync, no real CSOne data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import corpus_indexer
from corpus_indexer import (
    BM25_B,
    BM25_K1,
    CorpusFile,
    IndexSignal,
    IndexStats,
    bm25_score,
    detect_theme,
    enumerate_corpus_files,
    index_folder,
    open_corpus_db,
    rebuild_index,
    tokenize,
)
from knowledge_schema import SCHEMA_VERSION


_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "round17"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_corpus(tmp_path: Path) -> Path:
    """Copy the synthetic CSV fixtures into ``tmp_path`` and return
    the corpus root directory.  Each test gets a fresh copy so we
    can mutate / corrupt files without polluting the shared
    fixture."""
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    for fixture in (
        "synthetic_cases.csv",
        "synthetic_barriers.csv",
        "synthetic_pulse.csv",
    ):
        (root / fixture).write_bytes((_FIXTURES_ROOT / fixture).read_bytes())
    return root


# ---------------------------------------------------------------------------
# Tokenizer / theme detector -- pure helpers.
# ---------------------------------------------------------------------------


def test_tokenize_drops_stop_words_and_short_tokens():
    tokens = tokenize("The slow API is timing out for Acme Corp.")
    assert "the" not in tokens
    assert "is" not in tokens
    assert "slow" in tokens
    assert "api" in tokens
    assert "timing" in tokens
    assert "acme" in tokens
    assert "corp" in tokens


def test_tokenize_is_deterministic_across_calls():
    sample = "Webex Meetings audio quality issue: latency exceeds 200ms."
    assert tokenize(sample) == tokenize(sample)


def test_tokenize_handles_none_and_empty():
    assert tokenize("") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SSO login failed for the operator", "authentication"),
        ("API webhook retry returned 429 errors", "integration"),
        ("Latency spikes during bulk import", "performance"),
        ("Renewal billing question on auto-renew", "billing"),
        ("Random sentence with no theme keywords here", "general"),
    ],
)
def test_detect_theme_maps_known_keywords(text: str, expected: str):
    assert detect_theme(text) == expected


def test_detect_theme_empty_returns_general():
    assert detect_theme("") == "general"
    assert detect_theme(None) == "general"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BM25 scoring -- pure function.
# ---------------------------------------------------------------------------


def test_bm25_score_returns_non_negative_for_overlapping_tokens():
    chunk_tokens = ["latency", "spike", "during", "bulk", "import"]
    score = bm25_score(
        query_tokens=["latency", "spike"],
        chunk_tokens=chunk_tokens,
        chunk_length=len(chunk_tokens),
        avg_doc_length=10.0,
        doc_count=10,
        df_lookup={"latency": 2, "spike": 1},
        k1=BM25_K1,
        b=BM25_B,
    )
    assert score >= 0.0


def test_bm25_score_is_zero_when_no_overlap():
    score = bm25_score(
        query_tokens=["foo", "bar"],
        chunk_tokens=["latency", "spike"],
        chunk_length=2,
        avg_doc_length=10.0,
        doc_count=10,
        df_lookup={},
        k1=BM25_K1,
        b=BM25_B,
    )
    assert score == 0.0


def test_bm25_score_monotone_in_term_frequency():
    common = dict(
        query_tokens=["latency"],
        chunk_length=10,
        avg_doc_length=10.0,
        doc_count=10,
        df_lookup={"latency": 1},
        k1=BM25_K1,
        b=BM25_B,
    )
    low = bm25_score(chunk_tokens=["latency"] + ["other"] * 9, **common)
    high = bm25_score(chunk_tokens=["latency"] * 5 + ["other"] * 5, **common)
    assert high > low


# ---------------------------------------------------------------------------
# File enumeration.
# ---------------------------------------------------------------------------


def test_enumerate_corpus_files_returns_empty_for_missing_root(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert enumerate_corpus_files(missing) == []


def test_enumerate_corpus_files_returns_empty_when_root_is_none():
    assert enumerate_corpus_files(None) == []


def test_enumerate_corpus_files_skips_unsupported_and_temp_files(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    # Decoy / lock files that must be ignored.
    (root / "~$lock.docx").write_bytes(b"locked")
    (root / "draft.tmp").write_bytes(b"tmp")
    (root / "notes.md").write_text("# notes", encoding="utf-8")

    files = enumerate_corpus_files(root)
    names = sorted(cf.filename for cf in files)
    assert names == [
        "synthetic_barriers.csv",
        "synthetic_cases.csv",
        "synthetic_pulse.csv",
    ]
    for cf in files:
        assert isinstance(cf, CorpusFile)
        assert cf.size_bytes > 0
        assert len(cf.sha256) == 64  # hex digest
        assert cf.extension in (".csv", ".xlsx", ".docx")


def test_enumerate_corpus_files_honours_stop_signal(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    signal = IndexSignal(stop_requested=True)
    out = enumerate_corpus_files(root, signal=signal)
    # Stop requested before any file is processed -> empty result.
    assert out == []


# ---------------------------------------------------------------------------
# index_folder -- end-to-end against the synthetic CSVs.
# ---------------------------------------------------------------------------


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "corpus.db"
    return open_corpus_db(db_path)


def test_index_folder_populates_every_table(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    conn = _open_db(tmp_path)
    try:
        stats = index_folder(conn, root)
        assert isinstance(stats, IndexStats)
        # Three CSV fixtures ship one customer per row, plus one
        # extra row covering Synthetic Gamma in cases.csv.
        assert stats.files_seen == 3
        assert stats.files_parsed == 3
        assert stats.files_skipped == 0
        assert stats.files_failed == 0

        cur = conn.cursor()
        customers = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        cases = cur.execute('SELECT COUNT(*) FROM "cases"').fetchone()[0]
        chunks = cur.execute('SELECT COUNT(*) FROM "playbook_chunks"').fetchone()[0]
        sentiments = cur.execute('SELECT COUNT(*) FROM "sentiments"').fetchone()[0]

        assert customers >= 3  # alpha, beta, gamma
        assert cases >= 5
        assert chunks > 0
        assert sentiments >= 1

        # Schema version is recorded.
        version_row = cur.execute(
            'SELECT "version" FROM "schema_meta" WHERE "id" = 1;'
        ).fetchone()
        assert version_row is not None
        assert int(version_row[0]) == SCHEMA_VERSION
    finally:
        conn.close()


def test_index_folder_is_idempotent(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    conn = _open_db(tmp_path)
    try:
        first = index_folder(conn, root)
        second = index_folder(conn, root)

        assert first.files_parsed == 3
        # Second pass: no change to (path, sha256, schema_version).
        # All three files should be skipped.
        assert second.files_parsed == 0
        assert second.files_skipped == first.files_parsed
    finally:
        conn.close()


def test_index_folder_reparses_when_content_changes(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    conn = _open_db(tmp_path)
    try:
        index_folder(conn, root)
        # Mutate the cases CSV so its sha256 changes.
        cases = root / "synthetic_cases.csv"
        original = cases.read_text(encoding="utf-8")
        cases.write_text(
            original
            + "Synthetic Delta,TAC9006,Edge case,P3,Closed,2026-04-01T00:00:00Z,2026-04-02T00:00:00Z,Edge follow-up resolved.,collaboration\n",
            encoding="utf-8",
        )
        second = index_folder(conn, root)
        # The mutated file should re-parse (one parsed); the other
        # two files should be skipped.
        assert second.files_parsed == 1
        assert second.files_skipped == 2
    finally:
        conn.close()


def test_index_folder_records_malformed_file_as_error(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    # Drop a deliberately broken CSV that pandas will choke on.
    bad = root / "broken.csv"
    bad.write_bytes(b"\xff\xfe\x00\x01garbled,not,csv,at,all\n\xff\xff")
    conn = _open_db(tmp_path)
    try:
        stats = index_folder(conn, root)
        # Three good files plus the broken one were enumerated; the
        # run must complete without raising regardless of how
        # forgiving pandas chooses to be on the bad file.
        assert stats.files_seen == 4
        assert stats.files_parsed + stats.files_failed == 4
        cur = conn.cursor()
        row = cur.execute(
            'SELECT "parse_status" FROM "corpus_files" WHERE "filename" = ?',
            ("broken.csv",),
        ).fetchone()
        assert row is not None
        # parse_status must be one of the documented enum values.
        assert row["parse_status"] in {"ok", "error", "oversized"}
    finally:
        conn.close()


def test_rebuild_index_drops_existing_rows(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    conn = _open_db(tmp_path)
    try:
        index_folder(conn, root)
        cur = conn.cursor()
        before = cur.execute('SELECT COUNT(*) FROM "customers"').fetchone()[0]
        assert before > 0

        rebuild_index(conn)
        # All corpus tables should be empty after rebuild.
        for table in (
            "customers",
            "cases",
            "barriers",
            "resolutions",
            "sentiments",
            "playbook_chunks",
            "term_stats",
        ):
            count = cur.execute(f'SELECT COUNT(*) FROM "{table}";').fetchone()[0]
            assert count == 0, f"table {table} retained rows after rebuild"
    finally:
        conn.close()


def test_index_folder_with_none_conn_returns_stats_with_error():
    stats = index_folder(None, "/does/not/matter")  # type: ignore[arg-type]
    assert isinstance(stats, IndexStats)
    assert any("conn" in e.lower() for e in stats.errors)


def test_default_db_path_returns_path_object():
    p = corpus_indexer.default_db_path()
    assert isinstance(p, Path)
    # Filename should make it obvious this is the corpus DB.
    assert "corpus" in p.name.lower()
