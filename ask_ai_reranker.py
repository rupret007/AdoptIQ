"""Round 95 - optional Ask AI reranker helpers.

The retrieval pipeline already ranks evidence with BM25 + dense + RRF.
This module adds a second-stage reranker behind a small, import-safe
wrapper so runtime failures degrade to the RRF order while release bakes
can fail loud when the configured reranker cannot load.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

_RERANK_LOCK = threading.RLock()
_RERANKER: Optional[Any] = None
_RERANK_LOAD_ATTEMPTED = False
_RERANK_LOAD_ERROR: Optional[str] = None
_RERANK_BACKEND: Optional[str] = None


def _config_value(key: str, default: Any) -> Any:
    try:
        from config import Config  # type: ignore

        return getattr(Config, key, default)
    except Exception:  # noqa: BLE001 - import-safe defensive path
        return default


def _model_name() -> str:
    return str(_config_value("ASK_AI_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")).strip()


def _bundled_model_dir() -> Optional[Path]:
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    base = Path(meipass) / "Resources" / "embeddings"
    if not base.is_dir():
        return None
    name = _model_name().replace("/", "--")
    for candidate in (
        base / name,
        base / "release_fastembed_cache",
        base / "fastembed_cache",
        base,
    ):
        if candidate.is_dir():
            return candidate
    return None


def _dev_model_cache_dir() -> Optional[Path]:
    """Resolve the same persistent, operator-approved cache as embeddings.

    Reusing downloaded model bytes changes no scores; it only avoids fetching
    the identical reranker again on each clean release build.
    """
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
    for candidate in (root / "embeddings" / "fastembed_cache", root / "fastembed_cache"):
        if candidate.is_dir():
            return candidate
    return None


def reset_reranker_for_tests() -> None:
    """Forget the cached reranker so tests can exercise load branches."""
    global _RERANKER, _RERANK_LOAD_ATTEMPTED, _RERANK_LOAD_ERROR, _RERANK_BACKEND
    with _RERANK_LOCK:
        _RERANKER = None
        _RERANK_LOAD_ATTEMPTED = False
        _RERANK_LOAD_ERROR = None
        _RERANK_BACKEND = None


def reranker_load_error() -> Optional[str]:
    return _RERANK_LOAD_ERROR


def reranker_backend() -> Optional[str]:
    return _RERANK_BACKEND


def _load_cross_encoder_class() -> tuple[Optional[type], Optional[str]]:
    """Return the first supported fastembed cross-encoder class.

    The currently pinned fastembed line may expose this class under
    different module paths as the rerank surface matures, so keep this
    lookup narrow and explicit rather than binding to one import path.
    """
    candidates = (
        ("fastembed.rerank.text_cross_encoder", "TextCrossEncoder"),
        ("fastembed.rerank.cross_encoder", "TextCrossEncoder"),
        ("fastembed.rerank.cross_encoder", "CrossEncoder"),
    )
    for module_name, class_name in candidates:
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
            return cls, f"{module_name}.{class_name}"
        except Exception:  # noqa: BLE001 - try next supported path
            continue
    return None, None


def get_reranker() -> Optional[Any]:
    """Return a cached reranker instance or ``None`` on degraded paths."""
    global _RERANKER, _RERANK_LOAD_ATTEMPTED, _RERANK_LOAD_ERROR, _RERANK_BACKEND
    with _RERANK_LOCK:
        if _RERANKER is not None:
            return _RERANKER
        if _RERANK_LOAD_ATTEMPTED:
            return None
        _RERANK_LOAD_ATTEMPTED = True

        cls, backend = _load_cross_encoder_class()
        if cls is None:
            _RERANK_LOAD_ERROR = "fastembed cross-encoder rerank class unavailable"
            logger.warning("Round 95: %s; using RRF order without rerank", _RERANK_LOAD_ERROR)
            return None

        kwargs = {"model_name": _model_name()}
        bundled = _bundled_model_dir()
        if bundled is not None:
            kwargs["cache_dir"] = str(bundled)
            kwargs["local_files_only"] = True
        else:
            developer_cache = _dev_model_cache_dir()
            if developer_cache is not None:
                kwargs["cache_dir"] = str(developer_cache)
        # Round 95: the HF Xet range downloader has shown non-sequential byte
        # reconstruction errors on macOS bake hosts. The plain HTTP path is
        # slower but deterministic for the release self-test.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        try:
            import truststore  # type: ignore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001 - optional TLS helper
            pass
        try:
            _RERANKER = cls(**kwargs)
            _RERANK_BACKEND = backend
            _RERANK_LOAD_ERROR = None
            return _RERANKER
        except Exception as exc:  # noqa: BLE001
            _RERANK_LOAD_ERROR = f"fastembed reranker load failed: {type(exc).__name__}: {exc}"
            logger.warning("Round 95: %s; using RRF order without rerank", _RERANK_LOAD_ERROR)
            _RERANKER = None
            return None


def _score_with_supported_api(reranker: Any, question: str, texts: Sequence[str]) -> list[float]:
    # Round 95: fastembed's supported cross-encoder API is
    # ``rerank(query, documents)``.  Keep the pair-list fallbacks for
    # alternate implementations exposed by future fastembed builds.
    fn = getattr(reranker, "rerank", None)
    if callable(fn):
        raw = fn(question, list(texts))
        return [float(x) for x in list(raw)]
    pairs = [(question, text) for text in texts]
    for attr in ("rerank_pairs", "predict", "score"):
        fn = getattr(reranker, attr, None)
        if not callable(fn):
            continue
        raw = fn(pairs)
        return [float(x) for x in list(raw)]
    if callable(reranker):
        raw = reranker(pairs)
        return [float(x) for x in list(raw)]
    raise TypeError("reranker exposes no supported scoring API")


def rerank_scores(question: str, texts: Sequence[str]) -> Optional[list[float]]:
    """Return scores aligned to ``texts`` or ``None`` when unavailable."""
    if not question or not texts:
        return None
    reranker = get_reranker()
    if reranker is None:
        return None
    try:
        scores = _score_with_supported_api(reranker, str(question), list(texts))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Round 95: reranker scoring failed; using RRF order: %s", exc)
        return None
    if len(scores) != len(texts):
        logger.warning(
            "Round 95: reranker returned %d score(s) for %d text(s); using RRF order",
            len(scores),
            len(texts),
        )
        return None
    return scores


def bake_self_test() -> tuple[bool, str]:
    """Release-gate probe used by ``scripts/bake_corpus.py``."""
    # Round 95: force the release probe to observe the current bake host,
    # not a cached runtime/test failure from an earlier soft-fallback path.
    reset_reranker_for_tests()
    scores = rerank_scores(
        "Which customer has an adoption barrier?",
        [
            "Customer ACME has an adoption barrier blocking rollout.",
            "This unrelated record describes a maintenance notice.",
        ],
    )
    if scores is None:
        return False, reranker_load_error() or "reranker returned no scores"
    if len(scores) != 2:
        return False, f"expected 2 scores, got {len(scores)}"
    if not all(isinstance(score, float) for score in scores):
        return False, "reranker scores were not floats"
    if not all(math.isfinite(score) for score in scores):
        return False, "reranker scores were not finite"
    if scores[0] <= scores[1]:
        return False, "reranker did not rank the relevant control above the unrelated control"
    return True, f"reranker healthy via {reranker_backend() or 'unknown'}"


__all__ = [
    "bake_self_test",
    "get_reranker",
    "rerank_scores",
    "reranker_backend",
    "reranker_load_error",
    "reset_reranker_for_tests",
]
