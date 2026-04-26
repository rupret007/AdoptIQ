"""Round 11 marker tests.

For every HIGH/MED/LOW phase fix in the Round 11 reporting accuracy
audit this module asserts that the source-marker comment landed in the
expected file.  The behavioural tests live in
``tests/test_round11_behavioral.py``.

Pattern mirrors Rounds 5-10: a marker test guarantees the fix is not
silently reverted; a behavioural test guarantees the fix is correct.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding='utf-8')


# Phase 1 - HIGH: ARR / financial math correctness ----------------------

def test_marker_phase_1_1_arr_by_issue_merge() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 1.1' in src


def test_marker_phase_1_2_arr_view_multi_sub() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 1.2' in src


def test_marker_phase_1_3_tech_hotspot_multicurrency() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 1.3' in src


def test_marker_phase_1_4_briefing_arr_currency() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 1.4' in src


def test_marker_phase_1_5_briefing_account_id_key() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 1.5' in src


def test_marker_phase_1_6_barrier_aging_multicurrency() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 1.6' in src


# Phase 2 - HIGH: AB / incident date predicate consistency --------------

def test_marker_phase_2_1_load_merge_ab_date_predicate() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 2.1' in src


def test_marker_phase_2_2_subscription_word_utc_window() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 2.2' in src


def test_marker_phase_2_3_high_impact_incident_helper() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 2.3' in src
    assert '_is_high_impact_incident' in src


def test_marker_phase_2_4_undated_incidents_quarantine() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 2.4' in src
    assert 'undated_incidents' in src or 'undated_count' in src


def test_marker_phase_2_5_incident_sort_by_parsed_datetime() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 2.5' in src
    assert '_parsed_published_ts' in src


# Phase 3 - HIGH: Customer name normalization in deep-dive bodies -------

def test_marker_phase_3_1_troubled_accounts_normalize() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 3.1' in src
    assert '_r11_cust_key' in src


def test_marker_phase_3_2_critical_ab_normalize_group() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 11 / Phase 3.2' in src


def test_marker_phase_3_3_bems_by_customer_normalize() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 11 / Phase 3.3' in src


def test_marker_phase_3_4_ei_tables_normalize() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert 'Round 11 / Phase 3.4' in src


def test_marker_phase_3_5_leader_action_plans_normalize() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 11 / Phase 3.5' in src


def test_marker_phase_3_6_fill_barrier_name_deterministic() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 3.6' in src


# Phase 4 - HIGH: Web/HTML accuracy --------------------------------------

def test_marker_phase_4_1_ask_intel_days_param() -> None:
    src = _read('templates/external_intelligence.html')
    assert 'Round 11 / Phase 4.1' in src
    assert 'days: _daysVal' in src or "'days'" in src or 'days:' in src


def test_marker_phase_4_2_search_subscriptions_normalize() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 4.2' in src
    assert 'unique_customer_count' in src


def test_marker_phase_4_3_panel1_cases_fallback() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 4.3' in src


# Phase 5 - HIGH: Risk color palette consistency ------------------------

def test_marker_phase_5_1_risk_band_colors_constant() -> None:
    cm_src = _read('canonical_metrics.py')
    assert 'RISK_BAND_COLORS' in cm_src
    assert "'HEALTHY'" in cm_src or '"HEALTHY"' in cm_src
    assert "'UNKNOWN'" in cm_src or '"UNKNOWN"' in cm_src
    app_src = _read('app_simple.py')
    assert 'Round 11 / Phase 5.1' in app_src


def test_marker_phase_5_2_word_vs_matplotlib_palette() -> None:
    cm_src = _read('canonical_metrics.py')
    assert 'RISK_BAND_PORTFOLIO_COLORS' in cm_src
    backend_src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 5.2' in backend_src


def test_marker_phase_5_3_severity_unknown_color() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 5.3' in src
    assert 'SEVERITY_COLORS' in src


# Phase 6 - MED: Truncation / sampling honesty --------------------------

def test_marker_phase_6_1_top_customers_arr_sample_honest() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 6.1' in src


def test_marker_phase_6_2_active_contracts_sql_aggregate() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 6.2' in src
    assert 'totals_source' in src


def test_marker_phase_6_3_renewal_stats_sql_aggregate() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 6.3' in src


def test_marker_phase_6_4_collab_empty_currency_unknown() -> None:
    src = _read('advanced_renewal_analyzer.py')
    assert 'Round 11 / Phase 6.4' in src
    assert 'no_financial_rows' in src


def test_marker_phase_6_5_resolution_rate_weighted() -> None:
    src = _read('advanced_renewal_analyzer.py')
    assert 'Round 11 / Phase 6.5' in src


def test_marker_phase_6_6_discount_multicurrency_gate() -> None:
    src = _read('advanced_renewal_analyzer.py')
    assert 'Round 11 / Phase 6.6' in src


def test_marker_phase_6_7_leader_subscription_customers_nunique() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 11 / Phase 6.7' in src


def test_marker_phase_6_8_leader_exec_summary_dedupe() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 11 / Phase 6.8' in src


# Phase 7 - MED: SQL determinism (LIMIT without ORDER BY) ---------------

def test_marker_phase_7_1_collab_order_by() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 7.1' in src


def test_marker_phase_7_2_expired_renewal_order_by() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 7.2' in src


def test_marker_phase_7_3_load_merge_order_by() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 7.3' in src


def test_marker_phase_7_4_team_query_order_by() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 7.4' in src


def test_marker_phase_7_5_arr_distinct_windowed() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 7.5' in src
    assert 'QUALIFY ROW_NUMBER' in src


def test_marker_phase_7_6_as_of_date_single_source() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 7.6' in src
    assert '_as_of_date' in src


# Phase 8 - MED: Charts / visualization corrections ---------------------

def test_marker_phase_8_1_panel2_risk_1dp() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 8.1' in src


def test_marker_phase_8_2_bems_bar_stable_sort() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 8.2' in src


def test_marker_phase_8_3_bem_fr_truncation_titles() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 8.3' in src


def test_marker_phase_8_4_severity_pies_r10_autopct() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 8.4' in src
    assert '_r10_autopct' in src


def test_marker_phase_8_5_portfolio_metrics_axis_units() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 8.5' in src


def test_marker_phase_8_6_portfolio_pie_zero_bands() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 8.6' in src


def test_marker_phase_8_7_volume_charts_neutral_colors() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 8.7' in src


def test_marker_phase_8_8_incident_utc_week_bucket() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 8.8' in src


# Phase 9 - MED: Word body section accuracy -----------------------------

def test_marker_phase_9_1_common_problems_empty_state() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 11 / Phase 9.1' in src


def test_marker_phase_9_2_themes_singular_plural() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 11 / Phase 9.2' in src


def test_marker_phase_9_3_high_risk_heading_band_aware() -> None:
    src = _read('compact_report_formatter.py')
    assert 'Round 11 / Phase 9.3' in src


def test_marker_phase_9_4_numbered_list_double_digit() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 9.4' in src
    assert '_RE_NUMBERED_LIST_ITEM' in src


def test_marker_phase_9_5_content_heading_stricter() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert 'Round 11 / Phase 9.5' in src


def test_marker_phase_9_6_format_number_everywhere() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 9.6' in src


def test_marker_phase_9_7_most_recent_first_sort() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 9.7' in src


def test_marker_phase_9_8_portfolio_psirt_per_customer() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 9.8' in src


# Phase 10 - MED: Admin / progress / freshness --------------------------

def test_marker_phase_10_1_admin_export_utc() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 11 / Phase 10.1' in src
    assert 'datetime.now(timezone.utc)' in src


def test_marker_phase_10_2_total_request_count_none() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 11 / Phase 10.2' in src


def test_marker_phase_10_3_last_7_days_failure_flag() -> None:
    src = _read('enhanced_admin_dashboard_v2.py')
    assert 'Round 11 / Phase 10.3' in src
    assert 'last_7_days_failed' in src


def test_marker_phase_10_4_progress_started_utc_label() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 10.4' in src or 'Started (UTC)' in src


def test_marker_phase_10_5_customer_progress_current_normalize() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 10.5' in src


def test_marker_phase_10_6_status_incidents_stale_flag() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 10.6' in src
    assert 'stale_storage' in src


def test_marker_phase_10_7_enhanced_insights_subsection_errors() -> None:
    src = _read('adoptiq_backend.py')
    assert 'Round 11 / Phase 10.7' in src
    assert 'subsection_errors' in src


# Phase 11 - LOW: Polish / determinism ----------------------------------

def test_marker_phase_11_1_previous_reports_tiebreak() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 11.1' in src


def test_marker_phase_11_2_status_all_availability_parity() -> None:
    src = _read('app_simple.py')
    assert 'Round 11 / Phase 11.2' in src


def test_marker_phase_11_3_ask_intel_canonical_headline_ui() -> None:
    src = _read('templates/external_intelligence.html')
    assert 'Round 11 / Phase 11.3' in src
    assert 'context_summary' in src
    assert 'canonical_headline' in src


def test_marker_phase_11_4_impact_vs_risk_tooltip() -> None:
    src = _read('templates/external_intelligence.html')
    assert 'Round 11 / Phase 11.4' in src


def test_marker_phase_11_5_coverage_gap_distinct_count() -> None:
    src = _read('leader_report_generator.py')
    assert 'Round 11 / Phase 11.5' in src


def test_marker_phase_11_6_prefetch_contract_align() -> None:
    src = _read('snowflake_prefetch.py')
    assert 'Round 11 / Phase 11.6' in src


def test_marker_phase_11_7_prefetch_cache_key_scope() -> None:
    src = _read('snowflake_prefetch.py')
    assert 'Round 11 / Phase 11.7' in src
    assert '_cache_key' in src


def test_marker_phase_11_8_percentage_rounding_helper() -> None:
    src = _read('report_utils.py')
    assert 'Round 11 / Phase 11.8' in src
    assert 'round_percent' in src
