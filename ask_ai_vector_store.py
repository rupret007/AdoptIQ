"""Round 108 / Corpus Smoothness: runtime chunk-vector maintenance.

Bake-time corpus creation must still fail loud when dense embeddings are
unavailable, but runtime refreshes should keep lexical indexing useful and
only degrade dense retrieval as an operator-visible quality signal.

Round 109 / Fix Indexing Hang: runtime calls are bounded by a
``max_chunks`` budget so a large existing corpus (e.g. 500k+ chunks)
cannot keep ``corpus_bootstrap._run_index_pass`` synchronously busy and
leave the boot panel stuck on ``Indexing``. Bake passes opt out of the
cap by passing ``max_chunks=None`` so release builds still ship with
fully populated dense vectors.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Sequence

from _logging_helpers import safe_log_warning

logger = logging.getLogger(__name__)


# Round 109 / Fix Indexing Hang: bounded runtime upsert. Runtime refresh
# passes touch only this many chunks per call so the corpus boot path
# can finalize quickly. Bake-time callers explicitly pass
# ``max_chunks=None`` to opt out and embed every chunk (release builds
# still ship complete dense vectors).
_DEFAULT_RUNTIME_MAX_CHUNKS = 2000


def _runtime_max_chunks_default() -> int:
    """Round 109: env-overridable runtime cap so an operator can grow
    the per-pass budget without rebuilding the .app. Falls back to the
    module default when the env var is unset, blank, or unparseable."""

    raw = os.environ.get("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS")
    if raw is None:
        return _DEFAULT_RUNTIME_MAX_CHUNKS
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_RUNTIME_MAX_CHUNKS
    if parsed <= 0:
        return _DEFAULT_RUNTIME_MAX_CHUNKS
    return parsed


@dataclass(frozen=True)
class ChunkVectorUpsertResult:
    rows_written: int
    model_id: str
    model_dim: int
    rows_considered: int
    status: str
    error: str | None = None
    # Round 109 / Fix Indexing Hang: dense backlog after this pass.
    # ``rows_remaining`` > 0 with ``status="partial"`` means the
    # bounded runtime upsert wrote ``rows_written`` chunks and left
    # ``rows_remaining`` for a future refresh tick. Bake-time and
    # fully-caught-up runtime calls always report 0.
    rows_remaining: int = 0


def _embedding_config() -> tuple[str, int]:
    try:
        from config import Config  # type: ignore

        model_id = str(getattr(Config, "ASK_AI_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
        model_dim = int(getattr(Config, "ASK_AI_EMBEDDING_DIM", 384))
    except Exception:  # noqa: BLE001 - import-safe fallback
        model_id = "BAAI/bge-small-en-v1.5"
        model_dim = 384
    return model_id, model_dim


def _rows_missing_vectors(
    conn: Any,
    model_id: str,
    *,
    limit: int | None = None,
) -> list[tuple[int, str]]:
    """Round 109: ``LIMIT`` is applied at the SQL layer so the gap
    scan does not load 500k chunk rows into Python just to slice the
    first ``limit`` of them. ``None`` returns the full backlog (used by
    bake-time callers that opt out of the runtime cap)."""

    cur = conn.cursor()
    if limit is None or int(limit) <= 0:
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
    return list(
        cur.execute(
            'SELECT pc."id", pc."text" '
            'FROM "playbook_chunks" pc '
            'LEFT JOIN "chunk_vectors" cv ON cv."chunk_id" = pc."id" '
            'WHERE cv."chunk_id" IS NULL OR cv."model_id" != ? '
            'ORDER BY pc."id" ASC '
            'LIMIT ?;',
            (model_id, int(limit)),
        ).fetchall()
    )


def _count_rows_missing_vectors(conn: Any, model_id: str) -> int:
    """Round 109: cheap ``COUNT(*)`` for the dense-vector backlog so
    the diagnostic surface can advertise ``rows_remaining`` without
    re-running the row-fetching gap query."""

    try:
        cur = conn.cursor()
        row = cur.execute(
            'SELECT COUNT(*) '
            'FROM "playbook_chunks" pc '
            'LEFT JOIN "chunk_vectors" cv ON cv."chunk_id" = pc."id" '
            'WHERE cv."chunk_id" IS NULL OR cv."model_id" != ?;',
            (model_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - diagnostic-only
        return 0
    if not row:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):
        return 0


def upsert_chunk_vectors(
    conn: Any,
    *,
    chunk_rows: Sequence[tuple[int, str]] | None = None,
    strict: bool = False,
    batch_size: int = 64,
    max_chunks: int | None = None,
) -> ChunkVectorUpsertResult:
    """Fill ``chunk_vectors`` for missing or stale ``playbook_chunks``.

    ``strict=True`` is the release-bake contract: missing fastembed or a
    malformed batch raises and prevents a lexical-only DMG. ``strict=False``
    is the runtime refresh contract: lexical rows stay committed and dense
    status is surfaced as ``stale_or_lexical``.

    ``max_chunks`` (Round 109 / Fix Indexing Hang) bounds runtime calls
    so the corpus boot pass can finalize even on a 500k-chunk corpus.
    The default for runtime calls is
    :func:`_runtime_max_chunks_default` (2000 by default,
    ``ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS`` env override). Pass
    ``max_chunks=None`` for fully unbounded behaviour (bake-time
    contract). Strict callers also default to unbounded so existing
    bake tests do not regress.
    """

    from ask_ai_embeddings import embed_texts, encode_vector, get_embedder

    model_id, model_dim = _embedding_config()

    if max_chunks is None and not strict and chunk_rows is None:
        max_chunks = _runtime_max_chunks_default()

    try:
        if chunk_rows is not None:
            rows = list(chunk_rows)
            if max_chunks is not None and max_chunks > 0 and len(rows) > max_chunks:
                rows = rows[:max_chunks]
        else:
            rows = _rows_missing_vectors(conn, model_id, limit=max_chunks)
    except Exception as err:  # noqa: BLE001
        message = f"chunk vector gap query failed: {type(err).__name__}: {err}"
        if strict:
            raise RuntimeError(message) from err
        safe_log_warning(logger, "Round 108 / vector_store: %s", message)
        return ChunkVectorUpsertResult(
            0,
            model_id,
            model_dim,
            0,
            "stale_or_lexical",
            message,
            rows_remaining=0,
        )

    rows_considered = len(rows)
    backlog_after_cap = 0
    if (
        not strict
        and chunk_rows is None
        and max_chunks is not None
        and max_chunks > 0
        and rows_considered >= max_chunks
    ):
        # Round 109: only do the COUNT scan when the cap looks
        # saturated so a small backlog never pays the extra round-trip.
        total_missing = _count_rows_missing_vectors(conn, model_id)
        if total_missing > rows_considered:
            backlog_after_cap = max(0, total_missing - rows_considered)

    if not rows and not strict:
        return ChunkVectorUpsertResult(
            0,
            model_id,
            model_dim,
            0,
            "ready",
            None,
            rows_remaining=0,
        )

    if get_embedder() is None:
        message = "fastembed embedder unavailable"
        if strict:
            raise RuntimeError(f"{message} on bake host")
        safe_log_warning(
            logger,
            "Round 108 / vector_store: %s; lexical corpus rows remain available",
            message,
        )
        return ChunkVectorUpsertResult(
            0,
            model_id,
            model_dim,
            rows_considered,
            "stale_or_lexical",
            message,
            rows_remaining=rows_considered + backlog_after_cap,
        )

    if not rows:
        return ChunkVectorUpsertResult(
            0,
            model_id,
            model_dim,
            0,
            "ready",
            None,
            rows_remaining=0,
        )

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
        return ChunkVectorUpsertResult(
            written,
            model_id,
            model_dim,
            rows_considered,
            "stale_or_lexical",
            message,
            rows_remaining=max(0, rows_considered - written) + backlog_after_cap,
        )

    if backlog_after_cap > 0:
        # Round 109: lexical rows committed and the bounded dense pass
        # made progress; remaining backlog is logged so future refresh
        # ticks chip away without ever blocking the panel finalize.
        safe_log_warning(
            logger,
            "Round 109 / vector_store: bounded runtime upsert wrote %d "
            "chunks; %d still pending (next refresh will continue).",
            written,
            backlog_after_cap,
        )
        return ChunkVectorUpsertResult(
            written,
            model_id,
            model_dim,
            rows_considered,
            "partial",
            None,
            rows_remaining=backlog_after_cap,
        )

    return ChunkVectorUpsertResult(
        written,
        model_id,
        model_dim,
        rows_considered,
        "ready",
        None,
        rows_remaining=0,
    )
