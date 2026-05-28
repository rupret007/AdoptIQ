"""Round 108 / Corpus Smoothness: runtime chunk-vector maintenance.

Bake-time corpus creation must still fail loud when dense embeddings are
unavailable, but runtime refreshes should keep lexical indexing useful and
only degrade dense retrieval as an operator-visible quality signal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from _logging_helpers import safe_log_warning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkVectorUpsertResult:
    rows_written: int
    model_id: str
    model_dim: int
    rows_considered: int
    status: str
    error: str | None = None


def _embedding_config() -> tuple[str, int]:
    try:
        from config import Config  # type: ignore

        model_id = str(getattr(Config, "ASK_AI_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
        model_dim = int(getattr(Config, "ASK_AI_EMBEDDING_DIM", 384))
    except Exception:  # noqa: BLE001 - import-safe fallback
        model_id = "BAAI/bge-small-en-v1.5"
        model_dim = 384
    return model_id, model_dim


def _rows_missing_vectors(conn: Any, model_id: str) -> list[tuple[int, str]]:
    cur = conn.cursor()
    return list(
        cur.execute(
            'SELECT pc."id", pc."text" '
            'FROM "playbook_chunks" pc '
            'LEFT JOIN "chunk_vectors" cv ON cv."chunk_id" = pc."id" '
            'WHERE cv."chunk_id" IS NULL OR cv."model_id" != ? '
            'ORDER BY pc."id" ASC;',
            (model_id,),
        ).fetchall()
    )


def upsert_chunk_vectors(
    conn: Any,
    *,
    chunk_rows: Sequence[tuple[int, str]] | None = None,
    strict: bool = False,
    batch_size: int = 64,
) -> ChunkVectorUpsertResult:
    """Fill ``chunk_vectors`` for missing or stale ``playbook_chunks``.

    ``strict=True`` is the release-bake contract: missing fastembed or a
    malformed batch raises and prevents a lexical-only DMG. ``strict=False``
    is the runtime refresh contract: lexical rows stay committed and dense
    status is surfaced as ``stale_or_lexical``.
    """

    from ask_ai_embeddings import embed_texts, encode_vector, get_embedder

    model_id, model_dim = _embedding_config()

    try:
        rows = list(chunk_rows) if chunk_rows is not None else _rows_missing_vectors(conn, model_id)
    except Exception as err:  # noqa: BLE001
        message = f"chunk vector gap query failed: {type(err).__name__}: {err}"
        if strict:
            raise RuntimeError(message) from err
        safe_log_warning(logger, "Round 108 / vector_store: %s", message)
        return ChunkVectorUpsertResult(0, model_id, model_dim, 0, "stale_or_lexical", message)

    if not rows and not strict:
        return ChunkVectorUpsertResult(0, model_id, model_dim, 0, "ready", None)

    if get_embedder() is None:
        message = "fastembed embedder unavailable"
        if strict:
            raise RuntimeError(f"{message} on bake host")
        safe_log_warning(
            logger,
            "Round 108 / vector_store: %s; lexical corpus rows remain available",
            message,
        )
        return ChunkVectorUpsertResult(0, model_id, model_dim, len(rows), "stale_or_lexical", message)

    if not rows:
        return ChunkVectorUpsertResult(0, model_id, model_dim, 0, "ready", None)

    written = 0
    try:
        cur = conn.cursor()
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [str(row[1] or "") for row in batch]
            vecs = embed_texts(texts)
            if vecs is None or vecs.shape[0] != len(batch):
                raise RuntimeError(
                    f"embed_texts returned unexpected shape for batch starting at {start}"
                )
            for (chunk_id, _text), vec in zip(batch, vecs):
                blob = encode_vector(vec)
                cur.execute(
                    'INSERT OR REPLACE INTO "chunk_vectors" '
                    '("chunk_id", "model_id", "model_dim", "vector") '
                    'VALUES (?, ?, ?, ?);',
                    (int(chunk_id), model_id, model_dim, blob),
                )
                written += 1
        conn.commit()
    except Exception as err:  # noqa: BLE001
        message = f"chunk vector upsert failed: {type(err).__name__}: {err}"
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        if strict:
            raise RuntimeError(message) from err
        safe_log_warning(logger, "Round 108 / vector_store: %s", message)
        return ChunkVectorUpsertResult(written, model_id, model_dim, len(rows), "stale_or_lexical", message)

    return ChunkVectorUpsertResult(written, model_id, model_dim, len(rows), "ready", None)
