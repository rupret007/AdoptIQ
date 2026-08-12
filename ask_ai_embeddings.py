"""Round 66 / Pass 5 - Dense-embedding helpers for hybrid Ask AI retrieval.

Architecture
------------
- The fastembed TextEmbedding model is loaded ONCE per process via the
  ``get_embedder()`` singleton; subsequent calls return the cached
  instance. Cold start is ~1-2s on M-series Mac, ~3s on Intel; warm
  calls are <100ms per query.
- All public functions return ``None`` on degraded paths (model
  missing, fastembed not installed, model load failure) rather than
  raising. Callers MUST honor that contract or the production-grade
  graceful-degradation guarantee in the plan is silently broken.
- Vectors are 384-dim float32 (BAAI/bge-small-en-v1.5). Persisted as
  SQLite BLOBs via ``encode_vector`` / ``decode_vector`` round-trip
  bit-exact.
- RRF fusion uses Cormack et al. 2009: ``rrf(d) = sum(1/(k + rank_i))``
  with ``k=60`` per literature default.

This module is INTENTIONALLY decoupled from corpus_retriever and
ask_ai_grounded; both import from here so the embedding lifecycle is
a single source of truth.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from _logging_helpers import safe_log_warning


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Singleton + tunables
# ---------------------------------------------------------------------------

_EMBED_LOCK = threading.RLock()
_EMBED: Optional[Any] = None  # fastembed.TextEmbedding instance or None
_EMBED_LOAD_ATTEMPTED: bool = False
_EMBED_LOAD_ERROR: Optional[str] = None


def _config_value(key: str, default: Any) -> Any:
    """Soft import of Config so this module is import-safe even if
    config is being reloaded mid-test."""
    try:
        from config import Config  # type: ignore
        return getattr(Config, key, default)
    except Exception:  # noqa: BLE001 - module-load-time defensive
        return default


def _model_name() -> str:
    return str(_config_value("ASK_AI_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")).strip()


def _expected_dim() -> int:
    try:
        return int(_config_value("ASK_AI_EMBEDDING_DIM", 384))
    except (TypeError, ValueError):
        return 384


def _rrf_k() -> int:
    try:
        return int(_config_value("ASK_AI_RRF_K", 60))
    except (TypeError, ValueError):
        return 60


# ---------------------------------------------------------------------------
# Bundle path resolution (PyInstaller-aware)
# ---------------------------------------------------------------------------


def _bundled_model_dir() -> Optional[Path]:
    """Return the bundled ``Resources/embeddings/<model>`` path when
    running from a frozen .app, else None. The build pipeline writes
    the fastembed cache structure under this prefix so the model loads
    without a HuggingFace fetch."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    base = Path(meipass) / "Resources" / "embeddings"
    if not base.is_dir():
        return None
    # The fastembed cache layout under HF is models--<org>--<model>.
    name = _model_name().replace("/", "--")
    candidates = [
        base / name,
        base / "release_fastembed_cache",
        base / "fastembed_cache",
        base,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _dev_model_cache_dir() -> Optional[Path]:
    """Round 161: repo-local fastembed cache so dev runs can load hybrid without HF fetch."""
    env = (
        os.environ.get("ADOPTIQ_FASTEMBED_CACHE")
        or os.environ.get("FASTEMBED_CACHE_PATH")
        or ""
    ).strip()
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_dir():
            return candidate
    root = Path(__file__).resolve().parent
    for candidate in (
        root / "embeddings" / "fastembed_cache",
        root / "fastembed_cache",
    ):
        if candidate.is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Lazy embedder load
# ---------------------------------------------------------------------------


def reset_embedder_for_tests() -> None:
    """Test hook: forget the cached embedder so the next ``get_embedder``
    re-attempts the import. Production code must NOT call this."""
    global _EMBED, _EMBED_LOAD_ATTEMPTED, _EMBED_LOAD_ERROR
    with _EMBED_LOCK:
        _EMBED = None
        _EMBED_LOAD_ATTEMPTED = False
        _EMBED_LOAD_ERROR = None


def embedder_load_error() -> Optional[str]:
    """Return the most recent load error message, or None when the
    embedder is healthy or has not been attempted yet."""
    return _EMBED_LOAD_ERROR


def get_embedder() -> Optional[Any]:
    """Return a fastembed TextEmbedding singleton, or None when the
    model cannot be loaded. Idempotent and thread-safe.

    Failure modes (all return None):
      - fastembed package not installed
      - Model files cannot be located AND HuggingFace fetch fails
      - Model file is corrupt
    """
    global _EMBED, _EMBED_LOAD_ATTEMPTED, _EMBED_LOAD_ERROR
    with _EMBED_LOCK:
        if _EMBED is not None:
            return _EMBED
        if _EMBED_LOAD_ATTEMPTED:
            # We tried once and failed; don't keep retrying on every
            # query. Operator must restart the process after fixing
            # the install / model.
            return None
        _EMBED_LOAD_ATTEMPTED = True
        try:
            from fastembed import TextEmbedding  # type: ignore
        except Exception as e:  # noqa: BLE001 - intentional broad catch
            _EMBED_LOAD_ERROR = f"fastembed import failed: {type(e).__name__}: {e}"
            safe_log_warning(
                logger,
                "Round 66 / Pass 5: %s; falling back to lexical retrieval",
                _EMBED_LOAD_ERROR,
            )
            return None
        kwargs = {"model_name": _model_name()}
        bundled = _bundled_model_dir()
        if bundled is not None:
            kwargs["cache_dir"] = str(bundled)
            kwargs["local_files_only"] = True
        else:
            dev_cache = _dev_model_cache_dir()
            if dev_cache is not None:
                kwargs["cache_dir"] = str(dev_cache)
        # Prefer truststore TLS so corporate MITM does not block the
        # one-time HuggingFace fetch when the model is not bundled.
        try:
            import truststore  # type: ignore
            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001 - truststore is optional
            pass
        try:
            _EMBED = TextEmbedding(**kwargs)
        except Exception as e:  # noqa: BLE001
            _EMBED_LOAD_ERROR = f"fastembed model load failed: {type(e).__name__}: {e}"
            safe_log_warning(
                logger,
                "Round 66 / Pass 5: %s; falling back to lexical retrieval",
                _EMBED_LOAD_ERROR,
            )
            _EMBED = None
            return None
        return _EMBED


# ---------------------------------------------------------------------------
# Embedding functions
# ---------------------------------------------------------------------------


def embed_query(text: str) -> Optional[np.ndarray]:
    """Embed a single query; return shape ``(384,)`` float32 or None."""
    if not text or not isinstance(text, str):
        return None
    embedder = get_embedder()
    if embedder is None:
        return None
    try:
        vec = next(iter(embedder.embed([text])))
    except Exception as e:  # noqa: BLE001
        safe_log_warning(logger, "Round 66 / Pass 5: embed_query failed: %s", e)
        return None
    return _normalize_vector(np.asarray(vec, dtype=np.float32))


def embed_texts(texts: Sequence[str]) -> Optional[np.ndarray]:
    """Embed a batch; return shape ``(N, 384)`` float32 or None on
    failure (single failure poisons the whole batch - callers should
    handle by falling back to lexical, not by retrying per-item)."""
    if not texts:
        return None
    embedder = get_embedder()
    if embedder is None:
        return None
    try:
        vecs = list(embedder.embed(list(texts)))
    except Exception as e:  # noqa: BLE001
        safe_log_warning(logger, "Round 66 / Pass 5: embed_texts failed: %s", e)
        return None
    if not vecs:
        return None
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim != 2:
        return None
    # Normalize so cosine == dot product downstream.
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms


def _normalize_vector(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return v
    return (v / n).astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# SQLite BLOB encoding
# ---------------------------------------------------------------------------


def encode_vector(v: np.ndarray) -> bytes:
    """Encode a 1D float32 vector as bytes for SQLite BLOB storage.
    The dtype + length is implicit in the model_dim recorded in
    ``sentinel.json`` - decoding side reshapes accordingly."""
    arr = np.asarray(v, dtype=np.float32).reshape(-1)
    return arr.tobytes()


def decode_vector(blob: bytes, dim: Optional[int] = None) -> Optional[np.ndarray]:
    """Decode a previously-encoded vector. ``dim`` defaults to the
    Config-pinned ``ASK_AI_EMBEDDING_DIM``."""
    if blob is None:
        return None
    expected = dim if dim is not None else _expected_dim()
    try:
        arr = np.frombuffer(blob, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if arr.size != expected:
        return None
    return arr.copy()  # detach from the buffer so callers can mutate safely


# ---------------------------------------------------------------------------
# Similarity + RRF
# ---------------------------------------------------------------------------


def dense_score(qvec: np.ndarray, dvec: np.ndarray) -> float:
    """Cosine similarity. Both vectors are assumed pre-normalized
    (embed_* functions normalize on the way out); this is a plain dot
    product. Returns 0.0 on shape mismatch."""
    if qvec is None or dvec is None:
        return 0.0
    if qvec.shape != dvec.shape:
        return 0.0
    return float(np.dot(qvec, dvec))


def rrf_fuse(
    rankings: Sequence[Sequence[Any]],
    *,
    k: Optional[int] = None,
) -> List[Tuple[Any, float]]:
    """Reciprocal Rank Fusion (Cormack, Clarke, Buettcher 2009).

    ``rankings`` is a sequence of ranked lists; each inner list holds
    document identifiers ordered best-to-worst. Returns a single
    fused ranking ``[(doc_id, rrf_score), ...]`` sorted by score desc
    with the doc_id used as deterministic tiebreaker.

    Identifiers can be any hashable; this function never assumes the
    underlying type.
    """
    k_eff = int(k if k is not None else _rrf_k())
    if k_eff < 1:
        k_eff = 60
    scores: dict[Any, float] = {}
    for ranking in rankings or ():
        for rank_zero_indexed, doc_id in enumerate(ranking or ()):
            rank = rank_zero_indexed + 1  # RRF uses 1-indexed rank
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k_eff + rank)
    # Sort by score desc; tiebreak by stable repr(doc_id) so output is
    # reproducible across runs.
    return sorted(
        scores.items(),
        key=lambda kv: (-kv[1], repr(kv[0])),
    )


def hybrid_score(
    *,
    bm25_ranking: Sequence[Any],
    dense_ranking: Sequence[Any],
    k: Optional[int] = None,
) -> List[Tuple[Any, float, int, int]]:
    """High-level hybrid score combining a BM25 ranking and a dense
    ranking. Returns ``[(doc_id, rrf_score, bm25_rank, dense_rank), ...]``
    where ranks are 1-indexed (0 for "not in this ranking's top-K").
    """
    bm25_index = {doc_id: i + 1 for i, doc_id in enumerate(bm25_ranking or ())}
    dense_index = {doc_id: i + 1 for i, doc_id in enumerate(dense_ranking or ())}
    fused = rrf_fuse([list(bm25_ranking or ()), list(dense_ranking or ())], k=k)
    return [
        (doc_id, score, bm25_index.get(doc_id, 0), dense_index.get(doc_id, 0))
        for doc_id, score in fused
    ]


__all__ = [
    "decode_vector",
    "dense_score",
    "embed_query",
    "embed_texts",
    "embedder_load_error",
    "encode_vector",
    "get_embedder",
    "hybrid_score",
    "reset_embedder_for_tests",
    "rrf_fuse",
]
