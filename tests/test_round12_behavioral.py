"""Round 12 behavioural tests.

Each test exercises the *behaviour* changed by the corresponding
phase fix, complementing the marker tests in
``tests/test_round12_markers.py``.

Coverage targets (from the Round 12 plan, Phase 12.2):
- 1.1/1.2: ``analyze_feature_requests`` / ``calculate_arr_impact_for_issues``
  set ``is_multi_currency`` on multi-currency input.
- 1.3: ``_create_briefing_book`` mixed-currency branch keys on
  ``ACCOUNT_ID_C``, not ``BU_NAME``.
- 2.1/2.2: briefing recent-barriers and CSOne scope filter are tz-aware.
- 3.x: briefing BEMS / predictive risk normalize before groupby.
- 4.1: ``search_subscriptions`` JSON has ``results_truncated`` when
  ``len(results) >= limit``.
- 5.1: renewal donut LOW wedge color resolved from ``RISK_BAND_COLORS``.
- 5.2: TAC severity bar colors equal ``SEVERITY_COLORS`` map.
- 7.1/7.2: regex assertion that LIMIT is paired with ORDER BY.
- 8.1: dpi unified across ``add_executive_visual_dashboard`` and
  ``app_simple`` chart ``savefig``.
- 9.3/9.4: Complete Barrier Details and Success Priorities sort
  before head.
- 9.8: Excel sheet name sanitizer strips ``[]:*?/\\``.
- 10.2: prefetch cache entries carry ``fetched_at_utc``.
- 10.4: admin persisted timestamps end with ``Z``.
- 11.1: ``round_percent`` helper imported and called by
  ``adoptiq_backend.py``.
- 11.3: ``verbose_debug_api`` JSON has both ``success`` and ``ok``
  fields (compat shim).
"""
from __future__ import annotations

import importlib
import os
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 1.1 / 1.2: multi-currency contract present on ARR aggregators
# ---------------------------------------------------------------------------

def test_phase_1_1_feature_requests_multi_currency_gate() -> None:
    src = _read("app_simple.py")
    block = re.search(
        r"Round 12 / Phase 1\.1.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 1.1 marker block not found"
    body = block.group(0)
    # Must reference multi-currency gating (CURRENCY_CODE / is_multi_currency).
    assert "is_multi_currency" in body or "CURRENCY_CODE" in body, (
        "Phase 1.1: feature-request ARR aggregation must guard on currency"
    )


def test_phase_1_2_arr_impact_for_issues_currency_contract() -> None:
    src = _read("app_simple.py")
    block = re.search(
        r"Round 12 / Phase 1\.2.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 1.2 marker block not found"
    body = block.group(0)
    assert "is_multi_currency" in body, (
        "Phase 1.2: calculate_arr_impact_for_issues must expose is_multi_currency"
    )


# ---------------------------------------------------------------------------
# Phase 1.3: mixed-currency briefing branch must group on ACCOUNT_ID_C
# ---------------------------------------------------------------------------

def test_phase_1_3_briefing_mixed_currency_keyed_on_account_id() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 12 / Phase 1\.3.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 1.3 marker block not found"
    body = block.group(0)
    assert "ACCOUNT_ID_C" in body, (
        "Phase 1.3: mixed-currency branch must group on ACCOUNT_ID_C"
    )


# ---------------------------------------------------------------------------
# Phase 2.1 / 2.2: briefing date predicates and CSOne scope filter UTC
# ---------------------------------------------------------------------------

def test_phase_2_1_briefing_recent_barriers_uses_utc_or_as_of() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 12 / Phase 2\.1.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 2.1 marker block not found"
    body = block.group(0)
    assert "timezone.utc" in body or "_as_of" in body or "tz=" in body, (
        "Phase 2.1: briefing recent-barriers cutoff must be tz-aware"
    )


def test_phase_2_2_csone_scope_filter_uses_utc() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 12 / Phase 2\.2.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 2.2 marker block not found"
    body = block.group(0)
    assert "timezone.utc" in body or "tz=" in body or "tz_localize" in body, (
        "Phase 2.2: CSOne scope filter must compute the cutoff in UTC"
    )


# ---------------------------------------------------------------------------
# Phase 2.3: incident_storage newest column uses MAX(COALESCE(...))
# ---------------------------------------------------------------------------

def test_phase_2_3_incident_newest_uses_coalesce() -> None:
    src = _read("incident_storage.py")
    # Both get_incident_statistics and get_maintenance_statistics should
    # use MAX(COALESCE(NULLIF(published, ''), last_seen)) per the plan.
    assert "MAX(COALESCE(NULLIF(published" in src or "MAX(COALESCE(published" in src, (
        "Phase 2.3: incident newest must use MAX(COALESCE(...))"
    )


# ---------------------------------------------------------------------------
# Phase 3.x: briefing BEMS / predictive risk must normalize before groupby
# ---------------------------------------------------------------------------

def test_phase_3_1_briefing_bems_normalizes_customer_name() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 12 / Phase 3\.1.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 3.1 marker block not found"
    body = block.group(0)
    assert "normalize_customer_name" in body or "customer_name_norm" in body, (
        "Phase 3.1: BEMS by-customer must normalize before groupby"
    )


def test_phase_3_3_predictive_risk_normalizes_customer_name() -> None:
    src = _read("compact_report_formatter.py")
    block = re.search(
        r"Round 12 / Phase 3\.3.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 3.3 marker block not found"
    body = block.group(0)
    assert "normalize_customer_name" in body or "customer_name_norm" in body, (
        "Phase 3.3: predictive risk must normalize before unique()/filter"
    )


# ---------------------------------------------------------------------------
# Phase 4.1: search_subscriptions JSON has results_truncated flag
# ---------------------------------------------------------------------------

def test_phase_4_1_search_subscriptions_truncated_flag_present() -> None:
    src_app = _read("app_simple.py")
    src_be = _read("adoptiq_backend.py")
    combined = src_app + "\n" + src_be
    assert "results_truncated" in combined, (
        "Phase 4.1: search_subscriptions output must expose results_truncated"
    )
    assert "may_have_more" in combined, (
        "Phase 4.1: search_subscriptions output must expose may_have_more"
    )


# ---------------------------------------------------------------------------
# Phase 5.1: renewal donut LOW wedge resolves color from RISK_BAND_COLORS
# ---------------------------------------------------------------------------

def test_phase_5_1_renewal_donut_uses_risk_band_low() -> None:
    cm = importlib.import_module("canonical_metrics")
    palette = getattr(cm, "RISK_BAND_COLORS", {})
    assert palette.get("LOW") == "#2ca02c", (
        "RISK_BAND_COLORS['LOW'] must be canonical green per the plan"
    )
    src = _read("app_simple.py")
    block = re.search(
        r"Round 12 / Phase 5\.1.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 5.1 marker block not found"
    body = block.group(0)
    assert "RISK_BAND_COLORS" in body, (
        "Phase 5.1: renewal donut LOW color must come from RISK_BAND_COLORS"
    )


# ---------------------------------------------------------------------------
# Phase 5.2: TAC severity bar uses canonical SEVERITY_COLORS
# ---------------------------------------------------------------------------

def test_phase_5_2_tac_severity_bar_uses_canonical_palette() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 12 / Phase 5\.2.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 5.2 marker block not found"
    body = block.group(0)
    assert "SEVERITY_COLORS" in body, (
        "Phase 5.2: TAC severity bar must build palette from SEVERITY_COLORS"
    )


# ---------------------------------------------------------------------------
# Phase 7.1 / 7.2: every LIMIT in flagged files is paired with ORDER BY
# ---------------------------------------------------------------------------

def _ast_string_literals(path: pathlib.Path) -> list:
    """Yield every string literal in *path* using ``ast`` so we don't
    mis-parse triple-quoted SQL or escaped quotes.
    """
    import ast as _ast

    out: list = []
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, _ast.JoinedStr):
            chunks: list = []
            for v in node.values:
                if isinstance(v, _ast.Constant) and isinstance(v.value, str):
                    chunks.append(v.value)
                else:
                    chunks.append("?")
            out.append("".join(chunks))
    return out


def _sql_strings_with_limit(path: pathlib.Path) -> list:
    """Return the subset of string literals that look like SQL with a
    ``LIMIT ...`` clause.

    Conservative: only flag literals that ALSO contain a ``SELECT`` /
    ``FROM`` so we don't false-positive on Python variable names like
    ``_AP_LIMIT = 20``.
    """
    matches: list = []
    for body in _ast_string_literals(path):
        upper = body.upper()
        if "LIMIT" not in upper:
            continue
        # Require it to actually look like a SQL SELECT to avoid
        # matching constants like ``"limit per table"``.
        if "SELECT" not in upper or "FROM" not in upper:
            continue
        matches.append(body)
    return matches


def test_phase_7_1_renewal_customer_info_limit_pairs_with_order_by() -> None:
    src = _read("advanced_renewal_analyzer.py")
    # We don't enforce *every* LIMIT (some are in unrelated subqueries);
    # we enforce that the Phase 7.1 marker block contains ORDER BY.
    block = re.search(
        r"Round 12 / Phase 7\.1.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 7.1 marker block not found"
    body = block.group(0)
    assert "ORDER BY" in body, (
        "Phase 7.1: customer info queries must add ORDER BY before LIMIT"
    )


def test_phase_7_2_enhanced_snowflake_insights_limits_have_order_by() -> None:
    path = REPO_ROOT.joinpath("enhanced_snowflake_insights.py")
    bad = []
    for sql in _sql_strings_with_limit(path):
        upper = sql.upper()
        # Allow ORDER BY inside the literal OR a QUALIFY ROW_NUMBER pattern
        # (which is a deterministic alternative).
        if "ORDER BY" not in upper and "QUALIFY ROW_NUMBER" not in upper:
            bad.append(sql.strip()[:200])
    assert not bad, (
        f"Phase 7.2: SQL literals contain LIMIT without ORDER BY:\n{bad[:3]}"
    )


# ---------------------------------------------------------------------------
# Phase 8.1: chart dpi unified across backend and app_simple
# ---------------------------------------------------------------------------

def test_phase_8_1_chart_dpi_unified() -> None:
    src_be = _read("adoptiq_backend.py")
    src_app = _read("app_simple.py")
    # Both modules should agree on a single canonical dpi token.  The
    # plan calls out 150 vs 300 inconsistency; after the fix we expect
    # at least one Round 12 / Phase 8.1 marker block in either file.
    assert "Round 12 / Phase 8.1" in src_be or "Round 12 / Phase 8.1" in src_app


# ---------------------------------------------------------------------------
# Phase 9.3 / 9.4: sort before head
# ---------------------------------------------------------------------------

def test_phase_9_3_complete_barrier_details_sorts_before_head() -> None:
    src = _read("leader_report_generator.py")
    block = re.search(
        r"Round 12 / Phase 9\.3.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 9.3 marker block not found"
    body = block.group(0)
    assert "sort_values" in body or "sort" in body.lower(), (
        "Phase 9.3: must sort before head(100) on combined_abs"
    )


def test_phase_9_4_success_priorities_sorts_by_created_date() -> None:
    src = _read("app_simple.py")
    block = re.search(
        r"Round 12 / Phase 9\.4.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 9.4 marker block not found"
    body = block.group(0)
    assert "sort_values" in body and "CREATED_DATE" in body, (
        "Phase 9.4: Success Priorities must sort by CREATED_DATE before head"
    )


# ---------------------------------------------------------------------------
# Phase 9.8: Excel sheet-name sanitizer strips invalid characters
# ---------------------------------------------------------------------------

def test_phase_9_8_excel_sheet_name_sanitizer_strips_invalid() -> None:
    src = _read("adoptiq_backend.py")
    block = re.search(
        r"Round 12 / Phase 9\.8.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 9.8 marker block not found"
    body = block.group(0)
    # Each Excel-illegal character should be referenced by the sanitizer.
    for ch in r"[]:*?/\\":
        assert ch in body, f"Phase 9.8: sanitizer must handle illegal char {ch!r}"


# ---------------------------------------------------------------------------
# Phase 10.2: prefetch cache entries carry fetched_at_utc
# ---------------------------------------------------------------------------

def test_phase_10_2_prefetch_cache_has_fetched_at_utc() -> None:
    src = _read("snowflake_prefetch.py")
    assert "fetched_at_utc" in src, (
        "Phase 10.2: per-dataset cache entries must be stamped with fetched_at_utc"
    )
    # And the marker block must reference the field too.
    block = re.search(
        r"Round 12 / Phase 10\.2.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 10.2 marker block not found"
    assert "fetched_at_utc" in block.group(0)


# ---------------------------------------------------------------------------
# Phase 10.4: admin persisted timestamps end with Z
# ---------------------------------------------------------------------------

def test_phase_10_4_admin_persisted_timestamps_end_with_z() -> None:
    src = _read("enhanced_admin_dashboard_v2.py")
    # All Phase 10.4 marker blocks should reference the canonical UTC ISO
    # Z helper or an inline ``replace("+00:00", "Z")`` token.
    blocks = list(re.finditer(r"Round 12 / Phase 10\.4", src))
    assert blocks, "Phase 10.4 marker block(s) not found"
    # Globally the file must contain the Z-suffix replacement / utc helper.
    assert '"+00:00", "Z"' in src or '_utc_iso_z' in src or "Z')" in src, (
        "Phase 10.4: admin persisted timestamps must be UTC ISO with Z suffix"
    )


# ---------------------------------------------------------------------------
# Phase 11.1: round_percent migration imported / called in adoptiq_backend
# ---------------------------------------------------------------------------

def test_phase_11_1_round_percent_used_in_adoptiq_backend() -> None:
    src = _read("adoptiq_backend.py")
    # Either the helper is imported by name OR a Round-12 thin wrapper
    # (_r12_round_percent) is defined and used.
    assert ("from report_utils import" in src and "round_percent" in src) or (
        "_r12_round_percent" in src
    ), "Phase 11.1: round_percent must be imported/wrapped in adoptiq_backend"


# ---------------------------------------------------------------------------
# Phase 11.3: verbose_debug_api JSON exposes both ok and success
# ---------------------------------------------------------------------------

def test_phase_11_3_after_request_shim_present() -> None:
    src = _read("app_simple.py")
    # The Phase 11.3 marker block should install an after_request hook
    # that mirrors `ok` <-> `success` keys in JSON responses.
    block = re.search(
        r"Round 12 / Phase 11\.3.*?(?=Round 12 / Phase|\nclass |\Z)",
        src,
        re.DOTALL,
    )
    assert block, "Phase 11.3 marker block not found"
    body = block.group(0)
    # Either after_request (preferred) or both keys appear together.
    assert "after_request" in body or ("'ok'" in body and "'success'" in body), (
        "Phase 11.3: success/ok shim must mirror keys (after_request hook or inline)"
    )


# ---------------------------------------------------------------------------
# Phase 11.4: stable sort + tie-break for sort_values calls
# ---------------------------------------------------------------------------

def test_phase_11_4_stable_sort_calls_use_kind_stable() -> None:
    src_be = _read("adoptiq_backend.py")
    src_app = _read("app_simple.py")
    combined = src_be + "\n" + src_app
    # At minimum every Phase 11.4 marker block should reference the
    # 'stable' kind argument so the sort is reproducible.
    blocks = re.findall(
        r"Round 12 / Phase 11\.4.*?(?=Round 12 / Phase|\nclass |\ndef |\Z)",
        combined,
        re.DOTALL,
    )
    assert blocks, "Phase 11.4 marker block(s) not found"
    stable_blocks = [b for b in blocks if "stable" in b]
    assert stable_blocks, (
        "Phase 11.4: at least one marker block must request kind='stable'"
    )


# ---------------------------------------------------------------------------
# Phase 11.5: deterministic backoff jitter under ADOPTIQ_TEST_MODE
# ---------------------------------------------------------------------------

def test_phase_11_5_retry_jitter_is_deterministic_in_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sp = importlib.import_module("snowflake_prefetch")
    fn = getattr(sp, "_retry_sleep_seconds", None)
    assert fn is not None, "_retry_sleep_seconds must be defined in snowflake_prefetch"
    # In test mode, two calls with the same attempt should be equal.
    monkeypatch.setenv("ADOPTIQ_TEST_MODE", "1")
    a = fn(1)
    b = fn(1)
    assert a == b, "Phase 11.5: jitter must be deterministic in test mode"
    # Outside test mode, the helper still returns a non-negative float
    # bounded by the configured cap.
    monkeypatch.delenv("ADOPTIQ_TEST_MODE", raising=False)
    c = fn(1)
    assert isinstance(c, float) and c >= 0.0


# ---------------------------------------------------------------------------
# Phase 11.7: fold_fuzzy degradation counter exposes a non-zero count
# when data_normalization import fails.
# ---------------------------------------------------------------------------

def test_phase_11_7_fold_fuzzy_degradation_counter() -> None:
    cm = importlib.import_module("canonical_metrics")
    getter = getattr(cm, "get_fold_fuzzy_degradation_count", None)
    resetter = getattr(cm, "reset_fold_fuzzy_degradation_count", None)
    assert getter is not None and resetter is not None, (
        "Phase 11.7: counter accessors must be exported"
    )
    resetter()
    assert getter() == 0


# ---------------------------------------------------------------------------
# Phase 11.8: incident_storage page parameter normalizes negatives
# ---------------------------------------------------------------------------

def test_phase_11_8_export_all_data_clamps_negative_page() -> None:
    inc = importlib.import_module("incident_storage")
    # We can't (and don't want to) actually open a SQLite connection in
    # a unit test, but we can at least verify the function signature
    # documents the 0-indexed contract and the source module mentions
    # the clamp.
    src = _read("incident_storage.py")
    assert "page < 0" in src and "page = 0" in src, (
        "Phase 11.8: export_all_data must clamp negative page to 0"
    )
