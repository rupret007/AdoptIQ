"""Round 12 marker tests.

For every HIGH/MED/LOW phase fix in the Round 12 reporting accuracy
audit this module asserts that the source-marker comment landed in the
expected file.  Behavioural tests live in
``tests/test_round12_behavioral.py``.

Pattern mirrors Rounds 5-11: a marker test guarantees the fix is not
silently reverted; a behavioural test guarantees the fix is correct.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding='utf-8')


# Phase 1 - HIGH: ARR / financial math correctness (NEW surfaces) ----------

def test_marker_phase_1_1_feature_requests_arr_currency() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 1.1' in src
    assert 'analyze_feature_requests' in src


def test_marker_phase_1_2_arr_impact_currency_contract() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 1.2' in src
    assert 'calculate_arr_impact_for_issues' in src


def test_marker_phase_1_3_briefing_mixed_currency_account_id() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 1.3' in src


def test_marker_phase_1_4_portfolio_intel_multicurrency_top5() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 1.4' in src


def test_marker_phase_1_5_estimate_arr_currency_label() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 1.5' in src


# Phase 2 - HIGH: AB / incident date predicate consistency -----------------

def test_marker_phase_2_1_briefing_recent_barriers_utc() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 2.1' in src


def test_marker_phase_2_2_csone_scope_filter_utc() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 2.2' in src


def test_marker_phase_2_3_incident_newest_coalesce() -> None:
    src = _read('incident_storage.py')
    assert 'Round 12 / Phase 2.3' in src
    assert 'COALESCE' in src


def test_marker_phase_2_4_barrier_velocity_week_key() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 2.4' in src


# Phase 3 - HIGH: Customer name normalization in deep-dive bodies ----------

def test_marker_phase_3_1_briefing_bems_normalize() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 3.1' in src


def test_marker_phase_3_2_briefing_csone_details_normalize() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 3.2' in src


def test_marker_phase_3_3_predictive_risk_normalize() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 12 / Phase 3.3' in src


def test_marker_phase_3_4_leader_high_severity_barriers_normalize() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 12 / Phase 3.4' in src


def test_marker_phase_3_5_leader_complete_barrier_details_normalize() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 12 / Phase 3.5' in src


def test_marker_phase_3_6_historical_excel_scan_normalize() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 3.6' in src


def test_marker_phase_3_7_ask_ai_grounded_normalize() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 3.7' in src


# Phase 4 - HIGH: Web / HTML accuracy --------------------------------------

def test_marker_phase_4_1_search_subscriptions_truncated_flag() -> None:
    src_app = _read('app_simple.py')
    src_be = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 4.1' in src_app or 'Round 12 / Phase 4.1' in src_be
    # results_truncated keyword should appear in at least one of the two paths.
    assert 'results_truncated' in src_app or 'results_truncated' in src_be


def test_marker_phase_4_2_analyze_html_console_log_pii() -> None:
    src = _read('templates/analyze.html')
    assert 'Round 12 / Phase 4.2' in src


def test_marker_phase_4_3_progress_page_parity() -> None:
    """Round 12 / Phase 4.3: the progress page must show the
    ``Report Provenance & Disclaimer`` strip (data-as-of /
    generated-at + the for-internal-use disclaimer) so operators
    can sanity-check the figures before they share the artifact.

    Round 28 moved the progress page from an inline f-string in
    ``app_simple.py`` to ``templates/progress.html``.  The
    Round 12 invariant moved with it; the marker test follows the
    string to the new canonical location.
    """
    template_src = _read('templates/progress.html')
    app_src = _read('app_simple.py')
    assert (
        'Round 12 / Phase 4.3' in template_src
        or 'Round 12 / Phase 4.3' in app_src
    ), (
        "Round 12 / Phase 4.3 marker missing.  The 'Report "
        "Provenance & Disclaimer' block in templates/progress.html "
        "must keep its 'Round 12 / Phase 4.3' source-marker comment "
        "so a future drop-in revert (or accidental re-inline) is "
        "caught immediately."
    )


def test_marker_phase_4_4_external_intel_format_number() -> None:
    src = _read('templates/external_intelligence.html')
    assert 'Round 12 / Phase 4.4' in src
    assert 'format_number' in src


def test_marker_phase_4_5_history_html_format_number() -> None:
    src = _read('templates/history.html')
    assert 'Round 12 / Phase 4.5' in src
    assert 'format_number' in src


# Phase 5 - HIGH: Risk color palette consistency ---------------------------

def test_marker_phase_5_1_renewal_donut_low_band_color() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 5.1' in src


def test_marker_phase_5_2_tac_severity_bar_canonical() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 5.2' in src


def test_marker_phase_5_3_admin_traffic_light_canonical() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 12 / Phase 5.3' in src


def test_marker_phase_5_4_portfolio_colors_fallback_dedup() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 5.4' in src


def test_marker_phase_5_5_portfolio_metrics_bar_neutral() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 5.5' in src


# Phase 6 - MED: Truncation / sampling honesty -----------------------------

def test_marker_phase_6_1_historical_distribution_sample_flag() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 6.1' in src


def test_marker_phase_6_2_cross_trends_severity_canonical_sheet() -> None:
    src = _read('adoptiq_backend.py')
    # Phases 6.2 and 6.3 are commented together because they share the
    # same canonical-sheet rollup loop; accept either standalone or
    # combined marker.
    assert 'Round 12 / Phase 6.2' in src or 'Round 12 / Phase 6.2 + 6.3' in src


def test_marker_phase_6_3_cross_trends_total_rows_canonical_sheet() -> None:
    src = _read('adoptiq_backend.py')
    # See test_marker_phase_6_2 for the combined marker rationale.
    assert 'Round 12 / Phase 6.3' in src or 'Round 12 / Phase 6.2 + 6.3' in src


def test_marker_phase_6_4_ask_ai_historical_truncation_tags() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 6.4' in src


def test_marker_phase_6_5_sample_subjects_rename() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 6.5' in src


def test_marker_phase_6_6_ask_ai_barrier_aging_arr() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 6.6' in src


# Phase 7 - MED: Snowflake LIMIT-without-ORDER-BY non-determinism ----------

def test_marker_phase_7_1_renewal_customer_info_order_by() -> None:
    src = _read('advanced_renewal_analyzer.py')
    assert 'Round 12 / Phase 7.1' in src
    assert 'ORDER BY' in src


def test_marker_phase_7_2_enhanced_snowflake_insights_order_by() -> None:
    src = _read('enhanced_snowflake_insights.py')
    assert 'Round 12 / Phase 7.2' in src
    assert 'ORDER BY' in src


def test_marker_phase_7_3_json_lite_stable_sort() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 7.3' in src


# Phase 8 - MED: Charts / visualization corrections ------------------------

def test_marker_phase_8_1_chart_dpi_unify() -> None:
    src_be = _read('adoptiq_backend.py')
    src_app = _read('app_simple.py')
    assert 'Round 12 / Phase 8.1' in src_be or 'Round 12 / Phase 8.1' in src_app


def test_marker_phase_8_2_fig_text_clip_margin() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 8.2' in src


def test_marker_phase_8_3_renewal_panel_1_neutral_palette() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 8.3' in src


def test_marker_phase_8_4_portfolio_pie_fixed_band_order() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 8.4' in src


# Phase 9 - MED: Word body section accuracy (and Excel) --------------------

def test_marker_phase_9_1_briefing_money_format_number() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 9.1' in src


def test_marker_phase_9_2_tac_lifecycle_snapshot_sort() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert 'Round 12 / Phase 9.2' in src


def test_marker_phase_9_3_complete_barrier_details_sort() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 12 / Phase 9.3' in src


def test_marker_phase_9_4_success_priorities_sort() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 9.4' in src


def test_marker_phase_9_5_top_focus_accounts_more_footer() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 12 / Phase 9.5' in src


def test_marker_phase_9_6_parse_markdown_fallback_safe_doc() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 9.6' in src


def test_marker_phase_9_7_leader_table_headers_canonical_style() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 12 / Phase 9.7' in src


def test_marker_phase_9_8_excel_sheet_name_sanitize() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 9.8' in src


def test_marker_phase_9_9_ask_ai_top_customers_more_footer() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 9.9' in src


# Phase 10 - MED: Admin / progress / freshness -----------------------------

def test_marker_phase_10_1_ask_ai_throttle_monotonic() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 10.1' in src
    assert 'monotonic' in src


def test_marker_phase_10_2_prefetch_fetched_at_utc() -> None:
    src = _read('snowflake_prefetch.py')
    assert 'Round 12 / Phase 10.2' in src
    assert 'fetched_at_utc' in src


def test_marker_phase_10_3_total_managers_failed_flag() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 12 / Phase 10.3' in src


def test_marker_phase_10_4_admin_persisted_utc_iso_z() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 12 / Phase 10.4' in src


def test_marker_phase_10_5_briefing_report_generated_utc() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 10.5' in src


def test_marker_phase_10_6_chart_filename_utc() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 10.6' in src


def test_marker_phase_10_7_total_count_disallowed_none() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 12 / Phase 10.7' in src


# Phase 11 - LOW: Polish / determinism -------------------------------------

def test_marker_phase_11_1_round_percent_migration() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 11.1' in src
    assert '_r12_round_percent' in src or 'round_percent' in src


def test_marker_phase_11_2_prefetch_metrics_debug_api() -> None:
    src_app = _read('app_simple.py')
    src_pf = _read('snowflake_prefetch.py')
    assert 'Round 12 / Phase 11.2' in src_app
    assert 'Round 12 / Phase 11.2' in src_pf
    assert 'get_active_prefetch_metrics' in src_pf


def test_marker_phase_11_3_api_success_vs_ok_shim() -> None:
    src = _read('app_simple.py')
    assert 'Round 12 / Phase 11.3' in src


def test_marker_phase_11_4_stable_sort_tiebreak() -> None:
    src_be = _read('adoptiq_backend.py')
    src_app = _read('app_simple.py')
    assert 'Round 12 / Phase 11.4' in src_be or 'Round 12 / Phase 11.4' in src_app
    # At least one of the touched files should explicitly request stable sort.
    assert "kind='stable'" in src_be or "kind='stable'" in src_app or 'kind="stable"' in src_be or 'kind="stable"' in src_app


def test_marker_phase_11_5_retry_jitter_test_seed() -> None:
    src_pf = _read('snowflake_prefetch.py')
    src_ci = _read('cisco_internal_integrations.py')
    assert 'Round 12 / Phase 11.5' in src_pf
    assert 'Round 12 / Phase 11.5' in src_ci
    assert 'ADOPTIQ_TEST_MODE' in src_pf


def test_marker_phase_11_6_debug_log_redaction() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 12 / Phase 11.6' in src


def test_marker_phase_11_7_fuzzy_fold_error_flag() -> None:
    src = _read('canonical_metrics.py')
    assert 'Round 12 / Phase 11.7' in src
    assert 'get_fold_fuzzy_degradation_count' in src


def test_marker_phase_11_8_incident_storage_page_audit() -> None:
    src_inc = _read('incident_storage.py')
    src_app = _read('app_simple.py')
    assert 'Round 12 / Phase 11.8' in src_inc
    assert 'Round 12 / Phase 11.8' in src_app
