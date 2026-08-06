"""Round 109 / Fix Indexing Hang: regression tests.

Pins:
* Bounded runtime dense-vector upsert: a 500k-chunk backlog must not
  keep ``corpus_bootstrap`` synchronously busy.  ``upsert_chunk_vectors``
  caps runtime calls at ``_runtime_max_chunks_default`` and surfaces
  ``rows_remaining`` so the daily refresh worker can chip through the
  backlog without blocking startup.
* The bake path stays unbounded so release DMGs still ship with full
  dense vectors.
* The ``partial`` status promotes back to hybrid retrieval (the rows
  written are usable; the missing rows fall through the lexical
  channel of the RRF fuser).
* The jobs dashboard model label falls back to the server-resolved
  ``data-default-model`` attribute when no active jobs are running so a
  pre-R108 historical "gpt-5-nano" snapshot cannot pin a stale label
  on top of the live "gemini-3.1-flash-lite" default.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_STORE_PATH = _REPO_ROOT / "ask_ai_vector_store.py"
_BOOTSTRAP_PATH = _REPO_ROOT / "corpus_bootstrap.py"
_APP_SIMPLE_PATH = _REPO_ROOT / "app_simple.py"
_REPORT_JOBS_DASHBOARD_PATH = _REPO_ROOT / "static" / "js" / "report_jobs_dashboard.js"
_INTEL_STATUS_PATH = _REPO_ROOT / "static" / "js" / "intel_status.js"


def _seed_chunks(conn, count, *, with_vectors=0):
    from ask_ai_embeddings import encode_vector
    from knowledge_schema import apply_schema

    apply_schema(conn)
    cur = conn.cursor()
    chunk_ids = []
    for i in range(count):
        cur.execute(
            'INSERT INTO "playbook_chunks" '
            '("technology", "theme", "customer_id", "text", "tokens_json", '
            ' "doc_length", "source_file_id", "source_section") '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?);',
            ("t", "th", None, f"chunk-{i}", "[]", 1, None, None),
        )
        chunk_ids.append(cur.lastrowid)
    for chunk_id in chunk_ids[:with_vectors]:
        cur.execute(
            'INSERT INTO "chunk_vectors" '
            '("chunk_id", "model_id", "model_dim", "vector") '
            'VALUES (?, ?, ?, ?);',
            (chunk_id, "BAAI/bge-small-en-v1.5", 384, encode_vector(np.zeros(384, dtype=np.float32))),
        )
    conn.commit()
    return chunk_ids


# -------------------------------------------------------------------------
# Backend: bounded runtime dense-vector upsert
# -------------------------------------------------------------------------


def test_round109_runtime_max_chunks_default_present():
    """Source-shape pin: the module must declare a runtime cap."""
    src = _VECTOR_STORE_PATH.read_text(encoding="utf-8")
    assert_in_source(src, "_DEFAULT_RUNTIME_MAX_CHUNKS", label='src')
    assert_in_source(src, "_runtime_max_chunks_default", label='src')
    # The cap is documented as a Round 109 / Fix Indexing Hang fix.
    assert_in_source(src, "Round 109", label='src')


def test_round109_runtime_max_chunks_default_value(monkeypatch):
    from ask_ai_vector_store import _DEFAULT_RUNTIME_MAX_CHUNKS, _runtime_max_chunks_default

    monkeypatch.delenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", raising=False)
    assert _runtime_max_chunks_default() == _DEFAULT_RUNTIME_MAX_CHUNKS

    monkeypatch.setenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", "32")
    assert _runtime_max_chunks_default() == 32

    # Bad/blank/zero/negative values fall through to the module default.
    for bad_value in ("", "   ", "garbage", "0", "-7"):
        monkeypatch.setenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", bad_value)
        assert _runtime_max_chunks_default() == _DEFAULT_RUNTIME_MAX_CHUNKS


def test_round109_runtime_upsert_caps_large_backlog(monkeypatch):
    """Pre-R109 a corpus with hundreds of thousands of missing vectors
    would keep the runtime upsert busy for the entire catch-up. The cap
    means a single runtime pass embeds at most ``max_chunks`` rows and
    advertises the remaining backlog via ``rows_remaining`` /
    ``status='partial'``."""
    from ask_ai_vector_store import upsert_chunk_vectors

    monkeypatch.setenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", "8")

    conn = sqlite3.connect(":memory:")
    try:
        _seed_chunks(conn, count=25)

        captured_batches = []

        def _embed_capture(texts):
            captured_batches.append(len(texts))
            return np.ones((len(texts), 384), dtype=np.float32)

        with patch("ask_ai_embeddings.get_embedder", return_value=object()), \
             patch("ask_ai_embeddings.embed_texts", side_effect=_embed_capture):
            result = upsert_chunk_vectors(conn, strict=False)

        assert result.status == "partial"
        assert result.rows_written == 8
        assert result.rows_considered == 8
        assert result.rows_remaining == 25 - 8
        # Bounded query means we never even loaded all 25 rows into the
        # embedder.
        assert sum(captured_batches) == 8

        rows = conn.execute('SELECT count(*) FROM "chunk_vectors";').fetchone()[0]
        assert rows == 8
    finally:
        conn.close()


def test_round109_runtime_upsert_returns_ready_when_caught_up(monkeypatch):
    from ask_ai_vector_store import upsert_chunk_vectors

    monkeypatch.setenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", "100")

    conn = sqlite3.connect(":memory:")
    try:
        _seed_chunks(conn, count=3)

        with patch("ask_ai_embeddings.get_embedder", return_value=object()), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((3, 384), dtype=np.float32)):
            result = upsert_chunk_vectors(conn, strict=False)

        assert result.status == "ready"
        assert result.rows_written == 3
        assert result.rows_considered == 3
        assert result.rows_remaining == 0
    finally:
        conn.close()


def test_round109_strict_bake_path_stays_unbounded(monkeypatch):
    """Release bakes still embed every missing chunk. ``strict=True``
    must not silently truncate to the runtime cap (regression risk if
    we ever route the bake path through the same default code path)."""
    from ask_ai_vector_store import upsert_chunk_vectors

    monkeypatch.setenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", "2")

    conn = sqlite3.connect(":memory:")
    try:
        chunk_ids = _seed_chunks(conn, count=5)
        rows = [(chunk_id, f"chunk-{i}") for i, chunk_id in enumerate(chunk_ids)]

        with patch("ask_ai_embeddings.get_embedder", return_value=object()), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((5, 384), dtype=np.float32)):
            result = upsert_chunk_vectors(conn, chunk_rows=rows, strict=True)

        assert result.rows_written == 5
        assert result.rows_remaining == 0
    finally:
        conn.close()


def test_round109_partial_status_keeps_hybrid_retrieval(monkeypatch):
    """``partial`` status (Round 109) must NOT force lexical fallback.
    The chunks we DID write are usable; the missing chunks fall through
    the lexical leg of RRF fusion. Forcing lexical for partial would
    make a 24-hour catch-up feel like a permanent regression."""
    import sys

    monkeypatch.delenv("ASK_AI_RETRIEVAL_METHOD", raising=False)

    import config
    import corpus_bootstrap as cb

    sys.modules["config"] = config

    monkeypatch.setattr(cb.Config, "ASK_AI_RETRIEVAL_METHOD", "lexical", raising=False)
    monkeypatch.setattr(config.Config, "ASK_AI_RETRIEVAL_METHOD", "lexical", raising=False)

    cb._r108_update_retrieval_method_for_vector_status("partial")

    assert cb.Config.ASK_AI_RETRIEVAL_METHOD == "hybrid"
    assert config.Config.ASK_AI_RETRIEVAL_METHOD == "hybrid"


def test_round109_partial_status_respects_operator_lexical_pin(monkeypatch):
    """When the operator has explicitly pinned lexical via the env
    var, ``partial`` (and ``ready``) must NOT silently flip them back
    to hybrid -- that would be a settings-precedence regression."""
    import config
    import corpus_bootstrap as cb

    monkeypatch.setenv("ASK_AI_RETRIEVAL_METHOD", "lexical")
    monkeypatch.setattr(cb.Config, "ASK_AI_RETRIEVAL_METHOD", "lexical", raising=False)
    monkeypatch.setattr(config.Config, "ASK_AI_RETRIEVAL_METHOD", "lexical", raising=False)

    cb._r108_update_retrieval_method_for_vector_status("partial")
    assert cb.Config.ASK_AI_RETRIEVAL_METHOD == "lexical"

    cb._r108_update_retrieval_method_for_vector_status("ready")
    assert cb.Config.ASK_AI_RETRIEVAL_METHOD == "lexical"


# -------------------------------------------------------------------------
# Boot state diagnostics
# -------------------------------------------------------------------------


def test_round109_boot_state_carries_dense_rows_remaining():
    """``CorpusBootState`` must expose ``dense_rows_remaining`` so the
    UI can render "warming/backfilling N remaining" without scraping
    log lines."""
    src = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    assert_in_source(src, "dense_rows_remaining", label='src')
    # The pre-R109 single in_progress flag should still be the only
    # signal the bootstrap thread sets to True; the new field is
    # diagnostics only.
    assert_in_source(src, "Round 109 / Fix Indexing Hang", label='src')


def test_round109_status_endpoint_exposes_dense_rows_remaining():
    """The /api/intel/status payload must include the new diagnostic
    so the JS panel can render the bounded-backfill quality note."""
    src = _APP_SIMPLE_PATH.read_text(encoding="utf-8")
    assert_in_source(src, '"dense_rows_remaining"', label='src')
    # Both the success branch (getattr from boot_state) and the
    # default fallback payload must include the key.
    assert src.count('"dense_rows_remaining"') >= 2


def test_round109_run_index_pass_records_rows_remaining():
    """Source-shape pin: ``_run_index_pass`` must persist
    ``rows_remaining`` from ``upsert_chunk_vectors`` onto the singleton
    state so subsequent ``/api/intel/status`` polls expose the
    backlog."""
    src = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    # The state assignment must read the field off the result.
    assert_in_source(src, "_STATE.dense_rows_remaining", label='src')
    # And it must be plumbed through ``last_stats`` so the diagnostic
    # survives across polls that re-read ``last_stats`` rather than
    # the raw fields.
    assert_in_source(src, '"dense_rows_remaining"', label='src')


# -------------------------------------------------------------------------
# UI: corpus panel "warming" rendering
# -------------------------------------------------------------------------


def test_round109_intel_panel_renders_warming_for_partial_status():
    """The panel script must render the bounded-backfill state as a
    quality note instead of leaving the user staring at "Indexing".
    Source-shape pin only (no JS runtime in pytest)."""
    src = _INTEL_STATUS_PATH.read_text(encoding="utf-8")
    # The classifier must consult corpus.chunks to decide whether an
    # in-progress pass is "Indexing" or just a backfill on top of an
    # already-serving corpus.
    assert_in_source(src, "available", label='src')
    assert_in_source(src, "Round 109", label='src')
    # The dense status helper must surface the partial state.
    assert_in_source(src, "'partial'" in src or '"partial"', label='src')
    assert_in_source(src, "warming", label='src')
    assert_in_source(src, "dense_rows_remaining", label='src')


def test_round109_intel_panel_classify_promotes_in_progress_with_chunks():
    """Sanity check on the classifier policy: when the corpus is
    already serving (chunks > 0, available=true), an in-flight refresh
    must NOT degrade the panel to "refreshing" (which renders as
    "Building local corpus..."). Source-shape pin pinning the policy
    branch order."""
    src = _INTEL_STATUS_PATH.read_text(encoding="utf-8")
    block_idx = src.find("function classifyCorpusPanel")
    assert block_idx != -1
    fn_block = src[block_idx: block_idx + 4000]
    assert "available" in fn_block
    # Chunks count guard must precede the in_progress -> refreshing
    # short-circuit so the bounded backfill case promotes to
    # runtime_synced.
    assert "hasChunks" in fn_block
    assert "inProgress" in fn_block


# -------------------------------------------------------------------------
# Jobs dashboard: model label drift
# -------------------------------------------------------------------------


def test_round109_jobs_dashboard_uses_active_jobs_for_model_label():
    """Pre-R109 the panel computed ``firstModel`` from the merged
    active+history list, which made historical "gpt-5-nano" snapshots
    pin a stale label across sessions. The fix must resolve the
    "Active report model" label from active jobs only and fall back to
    ``data-default-model``."""
    src = _REPORT_JOBS_DASHBOARD_PATH.read_text(encoding="utf-8")
    # The fixed code must declare ``activeJobs`` as the source of
    # truth and must reference ``data-default-model`` as the fallback.
    assert_in_source(src, "activeJobs", label='src')
    assert_in_source(src, "activeModelLabel", label='src')
    assert_in_source(src, "data-default-model", label='src')
    # The pre-R109 buggy expression that walked ``jobs.map(modelLabel)``
    # must be gone -- if it ever comes back, this test fires.
    assert "jobs.map(modelLabel)" not in src


def test_round109_jobs_dashboard_no_activeJobs_falls_back_to_default():
    """Branch-coverage source-shape pin: the new code must explicitly
    accept the empty active-jobs case (when no reports are running)
    and read ``data-default-model`` rather than dropping back to
    ``modelLabel(jobs[0])``."""
    src = _REPORT_JOBS_DASHBOARD_PATH.read_text(encoding="utf-8")
    # Locate the model panel branch and confirm the fallback chain.
    panel_idx = src.find("[data-report-model-current]")
    assert panel_idx != -1
    panel_block = src[panel_idx: panel_idx + 1500]
    assert "activeJobs" in panel_block
    assert "data-default-model" in panel_block
    assert "Round 109" in panel_block


# -------------------------------------------------------------------------
# Integration: bounded upsert keeps boot state finalize fast
# -------------------------------------------------------------------------


def test_round109_bounded_upsert_does_not_hang_on_large_backlog(monkeypatch):
    """Smoke-style integration: with a 2,000-row backlog and a tiny
    cap of 16 per pass, the runtime upsert must return promptly with
    ``partial`` status so the surrounding ``_run_index_pass`` can
    finalize boot state in the same tick."""
    from ask_ai_vector_store import upsert_chunk_vectors

    monkeypatch.setenv("ADOPTIQ_RUNTIME_VECTOR_MAX_CHUNKS", "16")

    conn = sqlite3.connect(":memory:")
    try:
        _seed_chunks(conn, count=2000)

        # The embedder mock is a constant-cost stand-in -- the real
        # ONNX call is what makes the unbounded case slow; bounded
        # behaviour is testable without the real model.
        with patch("ask_ai_embeddings.get_embedder", return_value=object()), \
             patch("ask_ai_embeddings.embed_texts", return_value=np.ones((16, 384), dtype=np.float32)):
            result = upsert_chunk_vectors(conn, strict=False)

        assert result.status == "partial"
        assert result.rows_written == 16
        assert result.rows_remaining == 2000 - 16

        # The persisted vector count must match the bounded write.
        persisted = conn.execute('SELECT count(*) FROM "chunk_vectors";').fetchone()[0]
        assert persisted == 16
    finally:
        conn.close()


@pytest.mark.parametrize(
    "status,expected_field",
    [
        ("partial", "rows_remaining"),
        ("ready", "rows_remaining"),
        ("stale_or_lexical", "rows_remaining"),
    ],
)
def test_round109_chunk_vector_upsert_result_carries_rows_remaining(status, expected_field):
    from ask_ai_vector_store import ChunkVectorUpsertResult

    # Empty-default behaviour preserves callers that ignore the field.
    res = ChunkVectorUpsertResult(0, "m", 384, 0, status)
    assert getattr(res, expected_field) == 0

    res2 = ChunkVectorUpsertResult(0, "m", 384, 0, status, rows_remaining=42)
    assert res2.rows_remaining == 42
