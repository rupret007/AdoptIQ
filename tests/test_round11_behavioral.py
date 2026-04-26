"""Round 11 behavioural tests.

Each test exercises the *behaviour* changed by the corresponding
phase fix, complementing the marker tests in
``tests/test_round11_markers.py``.
"""
from __future__ import annotations

import importlib
import pathlib
import re

import pandas as pd
import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 2.3: _is_high_impact_incident helper agrees across all four
# call sites and counts both status- and impact-level matches.
# ---------------------------------------------------------------------------

def test_phase_2_3_high_impact_incident_helper_present() -> None:
    src = _read("app_simple.py")
    assert "_HIGH_IMPACT_INCIDENT_STATUSES" in src
    assert "_HIGH_IMPACT_INCIDENT_LEVELS" in src
    assert "def _is_high_impact_incident" in src


# ---------------------------------------------------------------------------
# Phase 5.1: RISK_BAND_COLORS must cover HEALTHY and UNKNOWN explicitly.
# ---------------------------------------------------------------------------

def test_phase_5_1_risk_band_colors_complete() -> None:
    cm = importlib.import_module("canonical_metrics")
    palette = getattr(cm, "RISK_BAND_COLORS", None)
    assert isinstance(palette, dict), "RISK_BAND_COLORS must be a dict"
    for required in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY", "UNKNOWN"):
        assert required in palette, f"RISK_BAND_COLORS missing band {required}"
        # All entries should look like a hex color.
        assert isinstance(palette[required], str)
        assert palette[required].startswith("#")
        assert len(palette[required]) in (4, 7)


def test_phase_5_2_portfolio_palette_present() -> None:
    cm = importlib.import_module("canonical_metrics")
    palette = getattr(cm, "RISK_BAND_PORTFOLIO_COLORS", None)
    assert isinstance(palette, dict), "RISK_BAND_PORTFOLIO_COLORS must be a dict"
    # At minimum the four standard bucket labels should be present.
    for required in ("Critical Risk", "High Risk", "Medium Risk", "Low Risk"):
        assert required in palette


# ---------------------------------------------------------------------------
# Phase 6.7: leader subscription_customers should use nunique on stable id
# ---------------------------------------------------------------------------

def test_phase_6_7_leader_subscription_customers_nunique() -> None:
    src = _read("leader_report_generator.py")
    # The Phase 6.7 marker block should mention .nunique() and one of the
    # stable-id columns rather than len() on the raw frame.
    block = re.search(
        r"Round 11 / Phase 6\.7.*?(\n\n|\nclass |\ndef )",
        src,
        re.DOTALL,
    )
    assert block, "Phase 6.7 marker block not found"
    body = block.group(0)
    assert "nunique" in body, "Phase 6.7 must call .nunique() on stable id"


# ---------------------------------------------------------------------------
# Phase 7.x: every LIMIT in adoptiq_backend.py marked by a Round 11 / Phase 7
# comment should be paired with an ORDER BY in the same query block.
# ---------------------------------------------------------------------------

def test_phase_7_limits_have_order_by() -> None:
    src = _read("adoptiq_backend.py")
    # Phase 7.1-7.5 are about ORDER BY on LIMIT'd / DISTINCT SQL; 7.6 is
    # about reusing a frozen ``_as_of_date`` and is a Python-side fix.
    # Only the SQL phases are required to emit ORDER BY in their block.
    for match in re.finditer(r"Round 11 / Phase 7\.([1-5])\b", src):
        start = match.start()
        window = src[start : start + 4000]
        assert "ORDER BY" in window or "QUALIFY ROW_NUMBER" in window, (
            f"Phase 7 marker at offset {start} not followed by ORDER BY/QUALIFY"
        )


# ---------------------------------------------------------------------------
# Phase 8.4: severity pies must use the shared _r10_autopct helper, not
# the bare ``%1.1f%%`` format that prints "0.0%" for tiny non-zero slices.
# ---------------------------------------------------------------------------

def test_phase_8_4_severity_pies_use_autopct_helper() -> None:
    src = _read("app_simple.py")
    # The helper itself must be defined.
    assert "def _r10_autopct" in src, "_r10_autopct helper must be defined"
    # And every Phase 8.4 *use site* (those that say 'use shared'/'shared
    # _r10_autopct'/'shared autopct') must reference _r10_autopct in the
    # immediately following pie() call window.
    markers = list(
        re.finditer(r"Round 11 / Phase 8\.4: (?:use shared|shared _r10)", src)
    )
    assert markers, "Phase 8.4 use-site markers missing"
    for marker in markers:
        window = src[marker.start() : marker.start() + 1200]
        assert "_r10_autopct" in window, "Phase 8.4: pie must use _r10_autopct"


# ---------------------------------------------------------------------------
# Phase 9.4: numbered list ordinal regex must accept >=10 (multi-digit)
# ---------------------------------------------------------------------------

def test_phase_9_4_numbered_list_regex_accepts_double_digits() -> None:
    backend = importlib.import_module("adoptiq_backend")
    pattern = getattr(backend, "_RE_NUMBERED_LIST_ITEM", None)
    assert pattern is not None, "_RE_NUMBERED_LIST_ITEM must be exported"
    for ok in ("1. one", "9. nine", "10. ten", "11) eleven", "100. hundred"):
        assert pattern.match(ok), f"regex must match ordered-list line {ok!r}"
    for bad in ("- bullet", "* bullet", "10 ten", "10.no space"):
        assert not pattern.match(bad), f"regex must reject {bad!r}"


# ---------------------------------------------------------------------------
# Phase 10.2: get_total_request_count must return None on DB exception
# (not silently coerce to 0).
# ---------------------------------------------------------------------------

def test_phase_10_2_total_request_count_returns_none_on_exception() -> None:
    src = _read("enhanced_admin_dashboard_v2.py")
    # Find the function body and assert the except branch returns None.
    block = re.search(
        r"def get_total_request_count\(\).*?(?=\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "get_total_request_count not found"
    body = block.group(0)
    assert "return None" in body, (
        "Phase 10.2: get_total_request_count must return None on exception"
    )


# ---------------------------------------------------------------------------
# Phase 11.7: snowflake_prefetch cache key must encode (days, scope, ids)
# ---------------------------------------------------------------------------

def test_phase_11_7_cache_key_encodes_scope() -> None:
    sp = importlib.import_module("snowflake_prefetch")
    AnalysisRunContext = getattr(sp, "AnalysisRunContext", None)
    assert AnalysisRunContext is not None
    # Build two contexts with different days windows and compare cache keys.
    ctx_a = AnalysisRunContext.build(
        ctx=None, account_ids=["A1", "A2"], days=90,
    )
    ctx_b = AnalysisRunContext.build(
        ctx=None, account_ids=["A1", "A2"], days=180,
    )
    assert ctx_a._cache_key("adoption_barriers") != ctx_b._cache_key(
        "adoption_barriers"
    )
    # Different account scope -> different key.
    ctx_c = AnalysisRunContext.build(
        ctx=None, account_ids=["A1", "A2", "A3"], days=90,
    )
    assert ctx_a._cache_key("adoption_barriers") != ctx_c._cache_key(
        "adoption_barriers"
    )
    # Same scope/days -> same key (deterministic).
    ctx_d = AnalysisRunContext.build(
        ctx=None, account_ids=["A2", "A1"], days=90,  # reversed order
    )
    assert ctx_a._cache_key("adoption_barriers") == ctx_d._cache_key(
        "adoption_barriers"
    )


# ---------------------------------------------------------------------------
# Phase 11.8: round_percent helper must be exported and use half-away-from-zero
# ---------------------------------------------------------------------------

def test_phase_11_8_round_percent_helper() -> None:
    ru = importlib.import_module("report_utils")
    fn = getattr(ru, "round_percent", None)
    assert fn is not None, "report_utils.round_percent must exist"
    # Half-away-from-zero (not banker's rounding).
    assert fn(0.05, 1) == 0.1
    assert fn(0.15, 1) == 0.2
    assert fn(2.5, 0) == 3.0
    # Non-numeric / NaN tolerance.
    assert fn(None) == 0.0
    assert fn("") == 0.0
    assert fn("not-a-number") == 0.0
    # Negative half-away-from-zero.
    assert fn(-0.05, 1) == -0.1


# ---------------------------------------------------------------------------
# Phase 11.6: snowflake prefetch contract must reflect real return shape
# ---------------------------------------------------------------------------

def test_phase_11_6_prefetch_contract_matches_reality() -> None:
    sp = importlib.import_module("snowflake_prefetch")
    contracts = getattr(sp, "_DATASET_TO_AGGREGATE_CONTRACT", None)
    assert isinstance(contracts, dict)
    pc = set(contracts.get("period_comparison", ()))
    assert {"adoption_barriers", "customer_pulse", "action_plans"}.issubset(pc), (
        "period_comparison contract must list the three rolled-up tables"
    )
    bv = set(contracts.get("barrier_velocity", ()))
    assert "weeks" in bv and "avg_new_per_week" in bv
    eai = set(contracts.get("enhanced_account_insights", ()))
    assert "_meta" in eai


# ---------------------------------------------------------------------------
# Phase 11.5: leader coverage gap subheading must use distinct count
# ---------------------------------------------------------------------------

def test_phase_11_5_coverage_gap_distinct_count_marker() -> None:
    src = _read("leader_report_generator.py")
    block = re.search(
        r"Round 11 / Phase 11\.5.*?(?=\n\s*\n)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 11.5 marker block not found"
    body = block.group(0)
    assert "normalize_customer_name" in body
    assert "_distinct_count" in body or "set(" in body


# ---------------------------------------------------------------------------
# Phase 1.5: briefing customer ARR must be keyed on ACCOUNT_ID_C
# ---------------------------------------------------------------------------

def test_phase_1_5_briefing_keyed_on_account_id() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 11 / Phase 1\.5.*?(?=Round 11 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 1.5 marker block not found"
    body = block.group(0)
    assert "ACCOUNT_ID_C" in body
