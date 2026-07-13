"""Round 4 truncation surfacing tests.

Pin every fetch-cap and silent-fallback fix landed in Round 4 so they
cannot regress.  These tests focus on STRUCTURAL contracts (presence
of ``was_truncated`` / ``fetch_error`` / ``mock_payload`` keys) rather
than runtime Snowflake behavior, so they remain reliable in CI without
a live database connection.
"""
from __future__ import annotations

import pandas as pd
import pytest


# --- _get_customer_account_info LIMIT 10 ----------------------------


def test_get_customer_account_info_returns_meta_when_empty():
    """The empty-result path must still carry a ``_meta`` block with
    ``fetch_limit`` / ``was_truncated`` so consumers can disclose the
    cap consistently regardless of whether rows were returned.
    """
    from advanced_renewal_analyzer import AdvancedRenewalAnalyzer

    class _StubCursor:
        description = None

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return []

        def close(self):
            return None

    class _StubCtx:
        def cursor(self):
            return _StubCursor()

    analyzer = AdvancedRenewalAnalyzer.__new__(AdvancedRenewalAnalyzer)
    analyzer.ctx = _StubCtx()

    result = analyzer._get_customer_account_info("nonexistent-customer-xyz")
    assert "_meta" in result, (
        "_get_customer_account_info must include a ``_meta`` block "
        "even when no rows are returned, so callers can surface the "
        "fetch cap consistently."
    )
    meta = result["_meta"]
    # Round 2 Phase 4.1 raised the per-customer fetch cap from 10 to
    # 50 so that account-level collisions can be detected and logged
    # rather than silently masked by a too-tight LIMIT.  The meta
    # block must reflect whatever the implementation is actually
    # using, so consumers can disclose the real cap.
    from advanced_renewal_analyzer import AdvancedRenewalAnalyzer as _Cls
    import inspect, re
    src = inspect.getsource(_Cls._get_customer_account_info)
    m = re.search(r"_FETCH_LIMIT\s*=\s*(\d+)", src)
    assert m, "Could not locate _FETCH_LIMIT in _get_customer_account_info"
    expected_limit = int(m.group(1))
    assert meta["fetch_limit"] == expected_limit
    assert meta["was_truncated"] is False
    assert meta["rows_returned"] == 0


# --- fetch_barrier_velocity weeks_total -----------------------------


def test_fetch_barrier_velocity_surfaces_weeks_total():
    """``fetch_barrier_velocity`` must return ``weeks_total`` so
    consumers cannot misread ``len(weeks)`` (capped at 12 in the
    payload) as the actual number of weeks observed.
    """
    import inspect

    import adoptiq_backend

    src = inspect.getsource(adoptiq_backend.fetch_barrier_velocity)
    assert "'weeks_total'" in src or '"weeks_total"' in src, (
        "fetch_barrier_velocity must include a ``weeks_total`` key in "
        "the returned dict to disclose the true observation count."
    )
    assert "'weeks_truncated'" in src or '"weeks_truncated"' in src, (
        "fetch_barrier_velocity must include a ``weeks_truncated`` "
        "flag so consumers can detect whether the 12-week cap was hit."
    )


# --- get_all_external_intel list_truncated --------------------------


def test_get_all_external_intel_returns_list_truncated_block():
    """``incident_storage.get_all_external_intel`` must include a
    ``list_truncated`` mapping and a ``list_fetch_limit`` value so
    consumers can detect when ``incident_stats['count']`` (unbounded)
    diverges from the rendered list (capped at 500).
    """
    import inspect

    import incident_storage

    src = inspect.getsource(incident_storage.get_all_external_intel)
    assert "'list_truncated'" in src or '"list_truncated"' in src, (
        "get_all_external_intel must return a ``list_truncated`` map "
        "alongside the list payloads."
    )
    assert "'list_fetch_limit'" in src or '"list_fetch_limit"' in src, (
        "get_all_external_intel must return ``list_fetch_limit`` so "
        "consumers can label the displayed cap explicitly."
    )


# --- prefetch_datasets fetch_error attr -----------------------------


def test_prefetch_datasets_attaches_fetch_error_attr_on_exception():
    """``snowflake_prefetch.prefetch_datasets`` must tag the empty
    placeholder DataFrame with ``fetch_error`` so a query failure is
    distinguishable from a legitimate zero-row result.
    """
    import inspect

    import snowflake_prefetch

    src = inspect.getsource(snowflake_prefetch.prefetch_datasets)
    assert "fetch_error" in src, (
        "prefetch_datasets must set a ``fetch_error`` attribute on the "
        "empty placeholder DataFrame in its exception path."
    )


# --- enhanced_snowflake_insights silent mock --------------------------


def test_enhanced_insights_exception_returns_error_not_silent_mock(monkeypatch):
    """``enhanced_snowflake_insights.get_comprehensive_customer_insights``
    must return a structured error dict (not a populated mock payload)
    when the underlying queries fail.  Otherwise downstream reports
    silently treat sample data as measured.
    """
    from enhanced_snowflake_insights import EnhancedSnowflakeInsights

    class _DummyCtx:
        def cursor(self):
            raise RuntimeError("ctx not used; account fetch is patched")

    insights = EnhancedSnowflakeInsights(_DummyCtx())

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated snowflake query failure")

    monkeypatch.setattr(EnhancedSnowflakeInsights, "_get_account_insights", _boom)

    result = insights.get_comprehensive_customer_insights("AcmeCorp", days=30)
    assert isinstance(result, dict)
    assert result.get("ok") is False, (
        "Exception path must explicitly mark the result as not-ok so "
        "callers cannot mistake mock numbers for measured ones."
    )
    assert "error" in result and result["error"], (
        "Exception path must include a non-empty ``error`` message."
    )
    assert result.get("data_quality") == "mock", (
        "Exception path must label the data quality as ``mock`` for "
        "any UI that wants to opt-in to placeholder rendering."
    )


# --- build_evidence_context truncation marker ----------------------


def test_build_evidence_context_emits_truncation_marker():
    """When ``max_records`` is exceeded, ``build_evidence_context`` must
    emit a marker line so the LLM knows it received a sample.
    """
    from ask_ai_grounded import EvidenceRecord, build_evidence_context

    records = [
        EvidenceRecord(
            source_type="case",
            source_id=f"CASE-{i}",
            customer="AcmeCorp",
            timestamp="2026-01-01",
            text=f"sample text {i}",
        )
        for i in range(30)
    ]
    text, _allowed_ids, used = build_evidence_context(
        records,
        question="recent issues",
        domains=["case"],
        char_budget=200000,
        max_records=10,
    )
    assert "Evidence truncated" in text, (
        "build_evidence_context must append an explicit truncation "
        "marker when max_records clips the evidence."
    )
    assert used == 10
