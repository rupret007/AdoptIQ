"""Round 95 / Phase A - Ask AI reranker contract tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


def _records():
    from ask_ai_grounded import EvidenceRecord

    return [
        EvidenceRecord(
            source_id="REC-A",
            source_type="AdoptionBarrier",
            customer="Alpha",
            timestamp="2026-05-01",
            text="alpha adoption barrier",
        ),
        EvidenceRecord(
            source_id="REC-B",
            source_type="SupportCase",
            customer="Beta",
            timestamp="2026-05-02",
            text="beta escalation case",
        ),
        EvidenceRecord(
            source_id="REC-C",
            source_type="ActionPlan",
            customer="Gamma",
            timestamp="2026-05-03",
            text="gamma action plan",
        ),
    ]


def test_round95_reranker_singleton_failure_logs_structured_warning(caplog):
    import ask_ai_reranker as reranker

    reranker.reset_reranker_for_tests()
    with patch("ask_ai_reranker._load_cross_encoder_class", return_value=(None, None)):
        assert reranker.get_reranker() is None
    assert "Round 95" in caplog.text
    assert "cross-encoder rerank class unavailable" in caplog.text


def test_round95_hybrid_rank_reorders_top_candidates_when_reranker_scores():
    from ask_ai_grounded import _hybrid_rank_evidence
    from config import Config

    original_enabled = Config.ASK_AI_RERANK_ENABLED
    original_k = Config.ASK_AI_RERANK_CANDIDATE_K
    try:
        Config.ASK_AI_RERANK_ENABLED = False
        with patch("ask_ai_embeddings.embed_query", return_value=np.ones(4, dtype=np.float32)), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((3, 4), dtype=np.float32)), \
             patch("ask_ai_embeddings.dense_score", side_effect=[0.3, 0.2, 0.1]):
            base = _hybrid_rank_evidence(_records(), "adoption barrier", ["core"])
        assert base is not None and len(base) == 3

        Config.ASK_AI_RERANK_ENABLED = True
        Config.ASK_AI_RERANK_CANDIDATE_K = 3
        # Make the second RRF candidate the rerank winner.
        with patch("ask_ai_embeddings.embed_query", return_value=np.ones(4, dtype=np.float32)), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((3, 4), dtype=np.float32)), \
             patch("ask_ai_embeddings.dense_score", side_effect=[0.3, 0.2, 0.1]), \
             patch("ask_ai_reranker.rerank_scores", return_value=[0.1, 9.0, 0.2]):
            reranked = _hybrid_rank_evidence(_records(), "adoption barrier", ["core"])
        assert reranked is not None
        assert reranked[0].source_id == base[1].source_id
        assert reranked[0].rerank_rank == 1
        assert reranked[0].rerank_score == pytest.approx(9.0)
    finally:
        Config.ASK_AI_RERANK_ENABLED = original_enabled
        Config.ASK_AI_RERANK_CANDIDATE_K = original_k


def test_round95_hybrid_rank_preserves_rrf_order_when_reranker_unavailable():
    from ask_ai_grounded import _hybrid_rank_evidence
    from config import Config

    original_enabled = Config.ASK_AI_RERANK_ENABLED
    try:
        Config.ASK_AI_RERANK_ENABLED = False
        with patch("ask_ai_embeddings.embed_query", return_value=np.ones(4, dtype=np.float32)), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((3, 4), dtype=np.float32)), \
             patch("ask_ai_embeddings.dense_score", side_effect=[0.3, 0.2, 0.1]):
            base = _hybrid_rank_evidence(_records(), "adoption barrier", ["core"])
        Config.ASK_AI_RERANK_ENABLED = True
        with patch("ask_ai_embeddings.embed_query", return_value=np.ones(4, dtype=np.float32)), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((3, 4), dtype=np.float32)), \
             patch("ask_ai_embeddings.dense_score", side_effect=[0.3, 0.2, 0.1]), \
             patch("ask_ai_reranker.rerank_scores", return_value=None):
            fallback = _hybrid_rank_evidence(_records(), "adoption barrier", ["core"])
        assert [r.source_id for r in fallback or []] == [r.source_id for r in base or []]
        assert all(r.rerank_score is None for r in fallback or [])
    finally:
        Config.ASK_AI_RERANK_ENABLED = original_enabled


def test_round95_retrieval_diag_always_carries_rerank_field():
    from ask_ai_grounded import compute_retrieval_diag
    from config import Config

    original = Config.ASK_AI_RETRIEVAL_METHOD
    try:
        Config.ASK_AI_RETRIEVAL_METHOD = "lexical"
        diag = compute_retrieval_diag(_records(), "question", ["core"])
    finally:
        Config.ASK_AI_RETRIEVAL_METHOD = original
    assert "rerank" in diag
    assert diag["rerank"] in {"not_applicable", "not_applied", "hybrid", "fallback"}


def test_round95_bake_reranker_self_test_fails_loud_on_missing_reranker():
    from scripts.bake_corpus import _bake_reranker_self_test

    with patch("ask_ai_reranker.bake_self_test", return_value=(False, "missing model")):
        ok, message = _bake_reranker_self_test()
    assert ok is False
    assert "missing model" in message


def test_round98_mac_spec_pins_existing_fastembed_reranker_module() -> None:
    spec_text = Path("adoptiq_mac.spec").read_text(encoding="utf-8")

    assert "'fastembed.rerank.cross_encoder'" in spec_text
    assert "'fastembed.rerank.text_cross_encoder'" not in spec_text
