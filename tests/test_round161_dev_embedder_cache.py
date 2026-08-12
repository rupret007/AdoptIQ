"""Round 161 — dev fastembed cache resolution for hybrid Ask AI."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_round161_dev_model_cache_prefers_repo_embeddings_dir(monkeypatch) -> None:
    """Repo-local cache wins when no ADOPTIQ_FASTEMBED_CACHE is set (dev default)."""
    from ask_ai_embeddings import _dev_model_cache_dir

    root = Path(__file__).resolve().parents[1]
    expected = root / "embeddings" / "fastembed_cache"
    if not expected.is_dir():
        pytest.skip("repo embeddings cache not present")

    monkeypatch.delenv("ADOPTIQ_FASTEMBED_CACHE", raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    assert _dev_model_cache_dir() == expected


def test_round161_dev_model_cache_honors_adoptiq_fastembed_cache_env(monkeypatch, tmp_path) -> None:
    """Build 113 runbook sets ADOPTIQ_FASTEMBED_CACHE; env path takes precedence over repo."""
    from ask_ai_embeddings import _dev_model_cache_dir

    cache_dir = tmp_path / "fastembed"
    cache_dir.mkdir()
    monkeypatch.setenv("ADOPTIQ_FASTEMBED_CACHE", str(cache_dir))
    assert _dev_model_cache_dir() == cache_dir
