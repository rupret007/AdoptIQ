"""Focused contracts for persisted-vector playbook reranking."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

import ask_ai_embeddings
import config
import corpus_retriever as cr
from corpus_retriever import Chunk


def _chunks() -> list[Chunk]:
    return [
        Chunk("first", None, None, None, 3.0, chunk_id=1),
        Chunk("second", None, None, None, 2.0, chunk_id=2),
        Chunk("third", None, None, None, 1.0, chunk_id=3),
    ]


@pytest.fixture
def vector_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        'CREATE TABLE "chunk_vectors" ('
        '"chunk_id" INTEGER PRIMARY KEY, "model_id" TEXT NOT NULL, '
        '"model_dim" INTEGER NOT NULL, "vector" BLOB NOT NULL)'
    )
    cr.configure_connection(conn)
    try:
        yield conn
    finally:
        cr.configure_connection(None)
        conn.close()


def _hybrid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.Config, "ASK_AI_RETRIEVAL_METHOD", "hybrid")
    monkeypatch.setattr(config.Config, "ASK_AI_EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(config.Config, "ASK_AI_EMBEDDING_DIM", 2)
    monkeypatch.setattr(config.Config, "ASK_AI_RRF_K", 60)


def _patch_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "search_playbook", lambda *args, **kwargs: _chunks())


def _insert_vector(
    conn: sqlite3.Connection,
    chunk_id: int,
    vector: np.ndarray,
    *,
    model_id: str = "test-model",
    model_dim: int = 2,
) -> None:
    conn.execute(
        'INSERT INTO "chunk_vectors" VALUES (?, ?, ?, ?)',
        (chunk_id, model_id, model_dim, ask_ai_embeddings.encode_vector(vector)),
    )
    conn.commit()


def test_valid_persisted_vectors_avoid_live_candidate_embedding(
    monkeypatch: pytest.MonkeyPatch, vector_conn: sqlite3.Connection
) -> None:
    _hybrid_config(monkeypatch)
    _patch_candidates(monkeypatch)
    for chunk_id, vector in enumerate(
        (
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.5, 0.5], dtype=np.float32),
        ),
        start=1,
    ):
        _insert_vector(vector_conn, chunk_id, vector)

    query_calls: list[str] = []
    rankings: list[list[list[int]]] = []
    monkeypatch.setattr(ask_ai_embeddings, "get_embedder", lambda: object())

    def embed_query(text: str) -> np.ndarray:
        query_calls.append(text)
        return np.array([1.0, 0.0], dtype=np.float32)

    def no_live_embedding(_texts: list[str]) -> np.ndarray:
        raise AssertionError("valid persisted vectors must avoid live candidate embedding")

    def fuse(items, *, k):
        rankings.append([list(ranking) for ranking in items])
        return [(doc_id, 1.0) for doc_id in items[1]]

    monkeypatch.setattr(ask_ai_embeddings, "embed_query", embed_query)
    monkeypatch.setattr(ask_ai_embeddings, "embed_texts", no_live_embedding)
    monkeypatch.setattr(ask_ai_embeddings, "rrf_fuse", fuse)

    out = cr.search_playbook_hybrid("alpha", top_k=2)

    assert query_calls == ["alpha"]  # one-argument public API
    assert rankings == [[[0, 1, 2], [1, 2, 0]]]
    assert [chunk.chunk_id for chunk in out] == [2, 3]


@pytest.mark.parametrize(
    "invalid", ["missing", "model", "dim", "blob", "nonfinite", "zero"]
)
def test_invalid_or_missing_persisted_vector_uses_live_candidates(
    monkeypatch: pytest.MonkeyPatch,
    vector_conn: sqlite3.Connection,
    invalid: str,
) -> None:
    _hybrid_config(monkeypatch)
    _patch_candidates(monkeypatch)
    for chunk_id in (1, 2, 3):
        if invalid == "missing" and chunk_id == 3:
            continue
        model_id = "wrong-model" if invalid == "model" and chunk_id == 3 else "test-model"
        model_dim = 3 if invalid == "dim" and chunk_id == 3 else 2
        vector = np.array([1.0, 0.0], dtype=np.float32)
        if invalid == "nonfinite" and chunk_id == 3:
            vector[0] = np.nan
        if invalid == "zero" and chunk_id == 3:
            vector[:] = 0.0
        _insert_vector(
            vector_conn,
            chunk_id,
            vector if model_dim == 2 else np.array([1.0, 0.0, 0.0], dtype=np.float32),
            model_id=model_id,
            model_dim=model_dim,
        )
    if invalid == "blob":
        vector_conn.execute(
            'UPDATE "chunk_vectors" SET "vector" = ? WHERE "chunk_id" = 3',
            (b"bad",),
        )
        vector_conn.commit()

    live_calls: list[list[str]] = []
    monkeypatch.setattr(ask_ai_embeddings, "get_embedder", lambda: object())
    monkeypatch.setattr(
        ask_ai_embeddings,
        "embed_query",
        lambda text: np.array([1.0, 0.0], dtype=np.float32),
    )

    def embed_texts(texts: list[str]) -> np.ndarray:
        live_calls.append(texts)
        return np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]], dtype=np.float32)

    monkeypatch.setattr(ask_ai_embeddings, "embed_texts", embed_texts)
    monkeypatch.setattr(
        ask_ai_embeddings,
        "rrf_fuse",
        lambda rankings, *, k: [(doc_id, 1.0) for doc_id in rankings[1]],
    )

    out = cr.search_playbook_hybrid("alpha", top_k=1)

    assert live_calls == [["first", "second", "third"]]
    assert out[0].chunk_id == 2


@pytest.mark.parametrize(
    "live_vectors",
    [
        np.zeros((3, 3), dtype=np.float32),
        np.array([[np.inf, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        np.zeros((3, 2), dtype=np.float32),
    ],
)
def test_invalid_live_vectors_return_lexical_order(
    monkeypatch: pytest.MonkeyPatch,
    vector_conn: sqlite3.Connection,
    live_vectors: np.ndarray,
) -> None:
    _hybrid_config(monkeypatch)
    _patch_candidates(monkeypatch)
    monkeypatch.setattr(ask_ai_embeddings, "get_embedder", lambda: object())
    monkeypatch.setattr(
        ask_ai_embeddings,
        "embed_query",
        lambda text: np.array([1.0, 0.0], dtype=np.float32),
    )
    monkeypatch.setattr(ask_ai_embeddings, "embed_texts", lambda texts: live_vectors)

    assert cr.search_playbook_hybrid("alpha", top_k=2) == _chunks()[:2]


@pytest.mark.parametrize("mode", ["lexical", "unavailable"])
def test_lexical_or_unavailable_embedder_returns_lexical_candidates(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _patch_candidates(monkeypatch)
    monkeypatch.setattr(config.Config, "ASK_AI_RETRIEVAL_METHOD", mode)
    if mode == "lexical":
        monkeypatch.setattr(
            ask_ai_embeddings,
            "get_embedder",
            lambda: (_ for _ in ()).throw(AssertionError("lexical mode must not load model")),
        )
    else:
        monkeypatch.setattr(config.Config, "ASK_AI_RETRIEVAL_METHOD", "hybrid")
        monkeypatch.setattr(ask_ai_embeddings, "get_embedder", lambda: None)
    monkeypatch.setattr(
        ask_ai_embeddings,
        "embed_query",
        lambda text: (_ for _ in ()).throw(AssertionError("query embedding must not run")),
    )

    assert cr.search_playbook_hybrid("alpha", top_k=2) == _chunks()[:2]


def test_chunk_construction_remains_backward_compatible() -> None:
    chunk = Chunk("text", "tech", "theme", "customer", 1.0, "file.csv", "summary")

    assert chunk.chunk_id is None
    assert chunk.source_section == "summary"
