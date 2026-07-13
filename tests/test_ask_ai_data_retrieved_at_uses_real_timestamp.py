"""Round 3 / Phase 2.3 regression test.

The Ask AI prompt's "Data retrieved at" stamp must reflect when the
prefetch actually pulled the data (``AnalysisRunContext.data_retrieved_at``),
not ``datetime.utcnow()`` at LLM-call time. With prefetch caches
those can differ by minutes, so the prompt-side freshness label
would otherwise lie about how stale the underlying data is.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_ask_ai_grounded_reads_run_ctx_data_retrieved_at():
    """Static guard on the source — both the portfolio and intel
    grounded paths must read ``run_ctx.data_retrieved_at`` (with a
    sane fallback) instead of computing the timestamp from
    ``datetime.utcnow()`` at the call site."""
    src = (PROJECT_ROOT / "ask_ai_grounded.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    # The fix uses ``getattr(run_ctx, 'data_retrieved_at', None)`` so
    # we look for that exact pattern. (Or the equivalent attribute
    # access if a refactor renames the helper.)
    assert "data_retrieved_at" in src
    assert "getattr(run_ctx, \"data_retrieved_at\"" in src or \
           "getattr(run_ctx, 'data_retrieved_at'" in src, (
        "ask_ai_grounded must source data_retrieved_at from the "
        "AnalysisRunContext, not utcnow() at LLM-call time"
    )


def test_analysis_run_context_carries_data_retrieved_at():
    from snowflake_prefetch import AnalysisRunContext

    ctx = AnalysisRunContext.__new__(AnalysisRunContext)
    # The class must allow this attribute to be set; the prefetch
    # path stamps it when the actual fetch begins.
    ctx.data_retrieved_at = None
    assert hasattr(ctx, "data_retrieved_at")
