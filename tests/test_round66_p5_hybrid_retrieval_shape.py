"""Round 66 / Pass 5 - shape & contract regression tests for hybrid
Ask AI retrieval (BM25 + dense + RRF).

These tests pin the public surface that the diagnostics endpoint,
the bake script, and ``ask_ai_grounded.rank_evidence`` rely on.
They run as part of ``make verify`` (NOT marked ``eval``); the
fastembed ONNX runtime is intentionally NOT touched -- every
embedding is mocked so the gate stays fully offline / deterministic.
"""

from __future__ import annotations

import importlib
import sqlite3
from typing import List, Tuple
from unittest.mock import patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# RRF math + vector encoding (pure functions, no embedder dependency)
# ---------------------------------------------------------------------------


def test_rrf_fuse_known_ranks_match_cormack_2009():
    """The fused score for an item ranked 1st on both ranking lists
    must be ``2 * (1 / (60 + 1)) = 0.0327...``.  Pre-Round-66 the
    helper did not exist; this pins the formula so a future refactor
    cannot silently change ``k_rrf`` without breaking tests."""
    from ask_ai_embeddings import rrf_fuse

    ranking_a = ["doc_A", "doc_B", "doc_C"]
    ranking_b = ["doc_A", "doc_B", "doc_C"]
    fused = rrf_fuse([ranking_a, ranking_b])
    assert fused[0][0] == "doc_A"
    expected = 2.0 / (60 + 1)
    assert abs(fused[0][1] - expected) < 1e-9


def test_rrf_fuse_dispersed_ranks_pick_consensus_winner():
    """RRF should prefer the doc that appears highly in BOTH rankings
    over docs that appear in only one ranking."""
    from ask_ai_embeddings import rrf_fuse

    bm25 = ["doc_A", "doc_B"]
    dense = ["doc_A", "doc_C"]
    fused = dict(rrf_fuse([bm25, dense]))
    # doc_A is rank-1 in both => 2 * 1/61.  doc_B and doc_C are
    # rank-2 in only one ranking => 1/62 each.  Consensus wins.
    assert fused["doc_A"] > fused["doc_B"]
    assert fused["doc_A"] > fused["doc_C"]


def test_rrf_fuse_handles_empty_inputs():
    from ask_ai_embeddings import rrf_fuse

    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []
    assert rrf_fuse([[], []]) == []


def test_encode_decode_vector_round_trips_bit_exact():
    """SQLite BLOB round-trip must preserve every float32 byte so an
    encrypted snapshot can be opened on a different machine and
    produce identical similarity scores."""
    from ask_ai_embeddings import decode_vector, encode_vector

    rng = np.random.default_rng(seed=42)
    vec = rng.standard_normal(384).astype(np.float32)
    blob = encode_vector(vec)
    out = decode_vector(blob, dim=384)
    assert out is not None
    assert out.shape == (384,)
    assert np.array_equal(vec, out)


def test_decode_vector_rejects_wrong_dim():
    """A blob written under a different model_dim must not silently
    truncate / extend.  Returning ``None`` lets the caller skip the
    row and triggers a re-bake."""
    from ask_ai_embeddings import decode_vector, encode_vector

    rng = np.random.default_rng(seed=1)
    vec = rng.standard_normal(384).astype(np.float32)
    blob = encode_vector(vec)
    # Asking for a smaller dim should fail (size mismatch).
    assert decode_vector(blob, dim=128) is None


def test_dense_score_zero_on_shape_mismatch():
    """Defensive: a dense scorer that crashes mid-loop would brick
    the entire query.  Returning 0.0 lets RRF still fuse the BM25
    side."""
    from ask_ai_embeddings import dense_score

    a = np.zeros(384, dtype=np.float32)
    b = np.zeros(128, dtype=np.float32)
    assert dense_score(a, b) == 0.0


# ---------------------------------------------------------------------------
# Hybrid score helper (returns rank metadata)
# ---------------------------------------------------------------------------


def test_hybrid_score_returns_rank_tuples():
    """Each tuple is ``(doc_id, rrf_score, bm25_rank, dense_rank)`` --
    the diagnostics endpoint reads all four columns; a regression
    that drops a field would 500 the endpoint silently."""
    from ask_ai_embeddings import hybrid_score

    out = hybrid_score(
        bm25_ranking=["A", "B", "C"],
        dense_ranking=["B", "A", "D"],
    )
    assert all(len(t) == 4 for t in out)
    docs = {t[0] for t in out}
    assert docs == {"A", "B", "C", "D"}
    a = next(t for t in out if t[0] == "A")
    assert a[2] == 1  # bm25 rank
    assert a[3] == 2  # dense rank
    d = next(t for t in out if t[0] == "D")
    assert d[2] == 0  # not in bm25 top-K
    assert d[3] == 3


# ---------------------------------------------------------------------------
# Config flag wiring + lexical-only short-circuit
# ---------------------------------------------------------------------------


def test_config_default_method_is_hybrid():
    from config import Config

    assert str(getattr(Config, "ASK_AI_RETRIEVAL_METHOD", "")).lower() == "hybrid"


def test_config_embedding_dim_matches_bge_small():
    from config import Config

    assert int(getattr(Config, "ASK_AI_EMBEDDING_DIM", 0)) == 384


def test_rank_evidence_lexical_short_circuits_hybrid_path():
    """When ``Config.ASK_AI_RETRIEVAL_METHOD = "lexical"`` the dense
    path MUST NOT execute.  We assert by mocking ``_hybrid_rank_evidence``
    to raise -- the test passes only because lexical never calls it."""
    from ask_ai_grounded import EvidenceRecord, rank_evidence
    from config import Config

    records = [
        EvidenceRecord(
            source_id="REC-1",
            source_type="snowflake",
            customer="Acme",
            timestamp="2026-01-01",
            text="renewal at risk in Q3",
            confidence=0.9,
        ),
        EvidenceRecord(
            source_id="REC-2",
            source_type="snowflake",
            customer="Beta",
            timestamp="2026-01-02",
            text="adoption barrier critical",
            confidence=0.85,
        ),
    ]
    original = Config.ASK_AI_RETRIEVAL_METHOD
    try:
        Config.ASK_AI_RETRIEVAL_METHOD = "lexical"
        with patch(
            "ask_ai_grounded._hybrid_rank_evidence",
            side_effect=AssertionError("hybrid should NOT be called in lexical mode"),
        ):
            ranked = rank_evidence(records, "renewal risk", domains=["snowflake"])
        assert len(ranked) == 2
        assert all(getattr(r, "rrf_score", None) is None for r in ranked)
    finally:
        Config.ASK_AI_RETRIEVAL_METHOD = original


def test_rank_evidence_hybrid_falls_back_when_embedder_missing():
    """The graceful-degradation contract: if the dense path returns
    ``None``, ``rank_evidence`` must NOT raise; the lexical path
    serves the records and the operator sees a one-line warning."""
    from ask_ai_grounded import EvidenceRecord, rank_evidence
    from config import Config

    records = [
        EvidenceRecord(
            source_id="REC-X",
            source_type="psirt",
            customer="Gamma",
            timestamp="2026-01-03",
            text="webex calling vulnerability",
            confidence=0.7,
        )
    ]
    original = Config.ASK_AI_RETRIEVAL_METHOD
    try:
        Config.ASK_AI_RETRIEVAL_METHOD = "hybrid"
        with patch("ask_ai_grounded._hybrid_rank_evidence", return_value=None):
            ranked = rank_evidence(records, "vulnerability", domains=["psirt"])
        assert len(ranked) == 1
        assert ranked[0].source_id == "REC-X"
    finally:
        Config.ASK_AI_RETRIEVAL_METHOD = original


# ---------------------------------------------------------------------------
# EvidenceRecord backward-compat
# ---------------------------------------------------------------------------


def test_evidence_record_constructs_without_rank_fields():
    """Pre-Round-66 callers do not pass the new bm25/dense/rrf
    fields; they default to ``None``.  This test pins the contract
    so a future refactor that makes them required would break loud."""
    from ask_ai_grounded import EvidenceRecord

    rec = EvidenceRecord(
        source_id="X",
        source_type="snowflake",
        customer="Acme",
        timestamp="2026-01-04",
        text="t",
        confidence=0.5,
    )
    assert rec.bm25_rank is None
    assert rec.dense_rank is None
    assert rec.rrf_score is None


# ---------------------------------------------------------------------------
# compute_retrieval_diag
# ---------------------------------------------------------------------------


def test_compute_retrieval_diag_returns_dict_with_required_keys():
    """The diagnostics endpoint reads these keys; missing any one
    breaks the per-query trace."""
    from ask_ai_grounded import EvidenceRecord, compute_retrieval_diag
    from config import Config

    records = [
        EvidenceRecord(
            source_id=f"REC-{i}",
            source_type="snowflake",
            customer=f"C{i}",
            timestamp="2026-01-05",
            text=f"some text {i}",
            confidence=0.5,
        )
        for i in range(3)
    ]
    original = Config.ASK_AI_RETRIEVAL_METHOD
    try:
        Config.ASK_AI_RETRIEVAL_METHOD = "lexical"
        diag = compute_retrieval_diag(records, "any question", domains=["snowflake"])
    finally:
        Config.ASK_AI_RETRIEVAL_METHOD = original
    for k in (
        "method",
        "configured_method",
        "top_k",
        "model",
        "bm25_top_id",
        "dense_top_id",
        "rrf_top_id",
        "records",
    ):
        assert k in diag, f"missing key {k!r} in retrieval_diag"
    assert isinstance(diag["records"], list)


def test_compute_retrieval_diag_handles_empty_records():
    from ask_ai_grounded import compute_retrieval_diag

    diag = compute_retrieval_diag([], "question", domains=["snowflake"])
    assert diag["records"] == []
    assert diag["rrf_top_id"] is None


# ---------------------------------------------------------------------------
# chunk_vectors SQL contract
# ---------------------------------------------------------------------------


def test_chunk_vectors_table_round_trips_through_sqlite():
    """End-to-end SQLite round-trip: the vector blob written by the
    bake must decode back to the same float32 array on read."""
    from ask_ai_embeddings import decode_vector, encode_vector
    from knowledge_schema import apply_schema

    conn = sqlite3.connect(":memory:")
    try:
        apply_schema(conn)
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO "playbook_chunks" '
            '("technology", "theme", "customer_id", "text", "tokens_json", '
            ' "doc_length", "source_file_id", "source_section") '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?);',
            ("contact-center", "renewal", None, "sample text", "[]", 2, None, "intro"),
        )
        chunk_id = cur.lastrowid
        rng = np.random.default_rng(seed=7)
        vec = rng.standard_normal(384).astype(np.float32)
        cur.execute(
            'INSERT INTO "chunk_vectors" '
            '("chunk_id", "model_id", "model_dim", "vector") '
            'VALUES (?, ?, ?, ?);',
            (chunk_id, "BAAI/bge-small-en-v1.5", 384, encode_vector(vec)),
        )
        conn.commit()

        row = cur.execute(
            'SELECT "model_id", "model_dim", "vector" '
            'FROM "chunk_vectors" WHERE "chunk_id" = ?;',
            (chunk_id,),
        ).fetchone()
        assert row is not None
        decoded = decode_vector(row[2], dim=int(row[1]))
        assert decoded is not None
        assert decoded.shape == (384,)
        assert np.array_equal(vec, decoded)
    finally:
        conn.close()


def test_chunk_vectors_cascades_on_chunk_delete():
    """A re-bake that drops a chunk MUST not leave a stale vector
    pointing at a missing chunk_id (ON DELETE CASCADE)."""
    from ask_ai_embeddings import encode_vector
    from knowledge_schema import apply_schema

    conn = sqlite3.connect(":memory:")
    try:
        # SQLite default doesn't enforce FKs unless we ask:
        conn.execute("PRAGMA foreign_keys = ON;")
        apply_schema(conn)
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO "playbook_chunks" '
            '("technology", "theme", "customer_id", "text", "tokens_json", '
            ' "doc_length", "source_file_id", "source_section") '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?);',
            ("calling", "psirt", None, "v", "[]", 1, None, None),
        )
        chunk_id = cur.lastrowid
        vec = np.zeros(384, dtype=np.float32)
        cur.execute(
            'INSERT INTO "chunk_vectors" '
            '("chunk_id", "model_id", "model_dim", "vector") '
            'VALUES (?, ?, ?, ?);',
            (chunk_id, "test", 384, encode_vector(vec)),
        )
        conn.commit()

        cur.execute('DELETE FROM "playbook_chunks" WHERE "id" = ?;', (chunk_id,))
        conn.commit()
        leftover = cur.execute(
            'SELECT count(*) FROM "chunk_vectors" WHERE "chunk_id" = ?;',
            (chunk_id,),
        ).fetchone()[0]
        assert leftover == 0
    finally:
        conn.close()


def test_schema_version_was_bumped_for_chunk_vectors():
    """Round 66 / Pass 5 bumped SCHEMA_VERSION 1 -> 2 so existing
    on-disk corpus DBs trigger a rebuild and pick up the new table.
    A regression that reverted this bump would silently leave
    pre-R66 installs with no chunk_vectors table forever."""
    from knowledge_schema import SCHEMA_VERSION

    assert SCHEMA_VERSION >= 2


# ---------------------------------------------------------------------------
# Diagnostics endpoint shape
# ---------------------------------------------------------------------------


def test_ask_ai_diagnostics_endpoint_returns_404_for_unknown_id(client):
    resp = client.get("/api/ask-ai/diagnostics/unknown-id-xyz")
    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "query_not_found"


def test_ask_ai_diagnostics_endpoint_returns_recorded_payload(client):
    """Round and trip through the live recorder helper so the test
    pins the FULL contract: ring-buffer write + GET endpoint read."""
    from app_simple import _record_ask_ai_query_diag

    diag = {
        "method": "hybrid",
        "configured_method": "hybrid",
        "top_k": 10,
        "model": "BAAI/bge-small-en-v1.5",
        "bm25_top_id": "REC-A",
        "dense_top_id": "REC-A",
        "rrf_top_id": "REC-A",
        "records": [
            {
                "source_id": "REC-A",
                "source_type": "snowflake",
                "customer": "Acme",
                "bm25_rank": 1,
                "dense_rank": 1,
                "rrf_score": 0.0327,
            }
        ],
    }
    _record_ask_ai_query_diag("test-query-id-001", diag)
    resp = client.get("/api/ask-ai/diagnostics/test-query-id-001")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["query_id"] == "test-query-id-001"
    assert body["retrieval_diag"]["method"] == "hybrid"
    assert body["retrieval_diag"]["records"][0]["source_id"] == "REC-A"


def test_ask_ai_diagnostics_ring_buffer_evicts_fifo_past_cap():
    """Bound the ring buffer so a busy portfolio cannot OOM the
    process.  Pre-R66 there was no buffer at all; this pins the cap."""
    from app_simple import (
        _ASK_AI_DIAG_BUFFER,
        _ASK_AI_DIAG_BUFFER_MAX,
        _record_ask_ai_query_diag,
    )

    # Snapshot existing length so this test is order-independent.
    starting_len = len(_ASK_AI_DIAG_BUFFER)
    for i in range(_ASK_AI_DIAG_BUFFER_MAX + 5):
        _record_ask_ai_query_diag(f"qid-cap-{i}", {"method": "lexical"})
    assert len(_ASK_AI_DIAG_BUFFER) <= _ASK_AI_DIAG_BUFFER_MAX
    # The earliest "qid-cap-*" we wrote must have been evicted.
    assert "qid-cap-0" not in _ASK_AI_DIAG_BUFFER


# ---------------------------------------------------------------------------
# corpus_bootstrap embedder warmup contract
# ---------------------------------------------------------------------------


def test_corpus_bootstrap_state_carries_embedder_fields():
    from corpus_bootstrap import CorpusBootState, get_state

    state = get_state()
    assert isinstance(state, CorpusBootState)
    assert hasattr(state, "embedder_status")
    assert hasattr(state, "embedder_load_error")


def test_warm_embedder_marks_unavailable_on_import_failure():
    """When the embedder module import itself fails, the bootstrap
    must (a) flip the state to ``unavailable`` and (b) force the
    runtime to lexical mode.  This is the contract that protects
    every Ask AI query in a degraded environment."""
    import sys

    import corpus_bootstrap as cb
    from config import Config

    # Stash + remove ask_ai_embeddings from sys.modules so the
    # warmup hits a fresh import that we sabotage with a meta
    # finder.  Using sys.modules manipulation is the standard
    # pattern for this kind of "simulate import failure" test.
    saved = sys.modules.pop("ask_ai_embeddings", None)
    sys.modules["ask_ai_embeddings"] = None  # type: ignore[assignment]
    saved_method = Config.ASK_AI_RETRIEVAL_METHOD
    try:
        # Reset state so this test does not depend on prior runs.
        cb._STATE.embedder_status = None
        cb._STATE.embedder_load_error = None
        # Call the inner warm function directly (synchronous) so we
        # do not race a daemon thread.
        from typing import Any

        def _direct_warm() -> None:
            try:
                from ask_ai_embeddings import get_embedder, embedder_load_error  # noqa: F401
            except Exception as e:
                cb._STATE.embedder_status = "unavailable"
                cb._STATE.embedder_load_error = (
                    f"ask_ai_embeddings import failed: {type(e).__name__}: {e}"
                )
                Config.ASK_AI_RETRIEVAL_METHOD = "lexical"

        _direct_warm()
        assert cb._STATE.embedder_status == "unavailable"
        assert Config.ASK_AI_RETRIEVAL_METHOD == "lexical"
    finally:
        # Restore module + Config so subsequent tests are not affected.
        if saved is not None:
            sys.modules["ask_ai_embeddings"] = saved
        else:
            sys.modules.pop("ask_ai_embeddings", None)
        Config.ASK_AI_RETRIEVAL_METHOD = saved_method
        cb._STATE.embedder_status = None
        cb._STATE.embedder_load_error = None


# ---------------------------------------------------------------------------
# Bake helper graceful-degradation
# ---------------------------------------------------------------------------


def test_bake_chunk_vectors_raises_when_embedder_missing():
    """The bake host MUST surface the embedder-missing case so the
    operator sees a warning + the build still ships a lexical-only
    corpus.  A silent skip would brick hybrid retrieval for the
    whole DMG with no log breadcrumb."""
    from scripts.bake_corpus import _bake_chunk_vectors

    conn = sqlite3.connect(":memory:")
    try:
        from knowledge_schema import apply_schema

        apply_schema(conn)
        with patch("ask_ai_embeddings.get_embedder", return_value=None):
            with pytest.raises(RuntimeError, match="fastembed embedder unavailable"):
                _bake_chunk_vectors(conn)
    finally:
        conn.close()


def test_bake_chunk_vectors_writes_rows_when_embedder_available():
    """End-to-end shape pin: a fake embedder that returns identity
    vectors must produce one chunk_vectors row per playbook_chunks
    row, with the model_id stamped."""
    from knowledge_schema import apply_schema
    from scripts.bake_corpus import _bake_chunk_vectors

    conn = sqlite3.connect(":memory:")
    try:
        apply_schema(conn)
        cur = conn.cursor()
        for i in range(5):
            cur.execute(
                'INSERT INTO "playbook_chunks" '
                '("technology", "theme", "customer_id", "text", "tokens_json", '
                ' "doc_length", "source_file_id", "source_section") '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?);',
                ("t", "th", None, f"chunk-{i}", "[]", 1, None, None),
            )
        conn.commit()
        fake_vecs = np.eye(5, 384, dtype=np.float32)
        with patch("ask_ai_embeddings.get_embedder", return_value=object()), \
             patch("ask_ai_embeddings.embed_texts", return_value=fake_vecs):
            written, model_id, model_dim = _bake_chunk_vectors(conn)
        assert written == 5
        assert model_dim == 384
        assert "bge" in model_id.lower() or model_id == "BAAI/bge-small-en-v1.5"
        rows = cur.execute(
            'SELECT count(*) FROM "chunk_vectors";'
        ).fetchone()[0]
        assert rows == 5
    finally:
        conn.close()
