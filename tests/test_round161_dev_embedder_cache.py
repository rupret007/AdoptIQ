"""Round 161 — dev fastembed cache resolution for hybrid Ask AI."""

from __future__ import annotations

from pathlib import Path


def test_round161_dev_model_cache_prefers_repo_embeddings_dir() -> None:
    from ask_ai_embeddings import _dev_model_cache_dir

    cache = _dev_model_cache_dir()
    root = Path(__file__).resolve().parents[1]
    expected = root / "embeddings" / "fastembed_cache"
    if expected.is_dir():
        assert cache == expected
    else:
        assert cache is None or cache.is_dir()
