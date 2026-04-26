"""Round 13 marker tests.

For every HIGH/MED/LOW phase fix in the Round 13 reporting accuracy
audit this module asserts that the source-marker comment landed in
the expected file.  Behavioural tests live in
``tests/test_round13_behavioral.py``.

Pattern mirrors Rounds 5-12: a marker test guarantees the fix is not
silently reverted; a behavioural test guarantees the fix is correct.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding="utf-8")


def _has_marker(name: str, phase: str) -> bool:
    return f"Round 13 / Phase {phase}" in _read(name)


# ---------------------------------------------------------------------------
# Phase 1 - ARR / financial / multi-currency
# ---------------------------------------------------------------------------

def test_marker_phase_1_1_mixed_currency_total_arr_contract() -> None:
    assert _has_marker("enhanced_snowflake_insights.py", "1.1")


def test_marker_phase_1_2_renewal_currency_symbol() -> None:
    assert _has_marker("advanced_renewal_analyzer.py", "1.2")


def test_marker_phase_1_3_recommendations_multi_currency_gate() -> None:
    assert _has_marker("advanced_renewal_analyzer.py", "1.3")


def test_marker_phase_1_4_feature_request_arr_dedupe() -> None:
    assert _has_marker("app_simple.py", "1.4")


def test_marker_phase_1_5_null_currency_non_comparable() -> None:
    assert _has_marker("adoptiq_backend.py", "1.5")


def test_marker_phase_1_6_barrier_aging_arr_dedupe() -> None:
    assert _has_marker("adoptiq_backend.py", "1.6")


def test_marker_phase_1_7_renewal_arr_thresholds_currency() -> None:
    assert _has_marker("risk_scoring.py", "1.7")


def test_marker_phase_1_8_arr_trend_comparable() -> None:
    assert _has_marker("adoptiq_backend.py", "1.8")


def test_marker_phase_1_9_ei_arr_data_wired() -> None:
    assert _has_marker("executive_intelligence_formatter.py", "1.9")


# ---------------------------------------------------------------------------
# Phase 2 - UTC / clock / date predicate
# ---------------------------------------------------------------------------

def test_marker_phase_2_1_leader_csone_utc() -> None:
    assert _has_marker("leader_report_generator.py", "2.1")


def test_marker_phase_2_2_risk_open_age_utc() -> None:
    assert _has_marker("risk_scoring.py", "2.2")


def test_marker_phase_2_3_risk_recent_cases_utc() -> None:
    assert _has_marker("risk_scoring.py", "2.3")


def test_marker_phase_2_4_incident_correlation_utc() -> None:
    assert _has_marker("adoptiq_backend.py", "2.4")


def test_marker_phase_2_5_briefing_month_bucket_utc() -> None:
    assert _has_marker("adoptiq_backend.py", "2.5")


def test_marker_phase_2_6_format_date_utc() -> None:
    assert _has_marker("report_utils.py", "2.6")


def test_marker_phase_2_7_bst_last_indexed_real_utc() -> None:
    assert _has_marker("cisco_internal_integrations.py", "2.7")


def test_marker_phase_2_8_cisco_diagnostics_utc() -> None:
    assert _has_marker("cisco_internal_integrations.py", "2.8")


def test_marker_phase_2_9_lifecycle_strings_utc() -> None:
    assert _has_marker("app_simple.py", "2.9")


def test_marker_phase_2_10_default_excel_filename_utc() -> None:
    assert _has_marker("adoptiq_backend.py", "2.10")


def test_marker_phase_2_11_leader_table_date_utc() -> None:
    assert _has_marker("leader_report_generator.py", "2.11")


def test_marker_phase_2_12_leader_validation_utc() -> None:
    src = _read("leader_report_generator.py")
    assert "Round 13 / Phase 2.12" in src or "Round 13 / Phase 2.11 + 2.12" in src


# ---------------------------------------------------------------------------
# Phase 3 - Customer name normalization
# ---------------------------------------------------------------------------

def test_marker_phase_3_1_briefing_p1p2_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.1")


def test_marker_phase_3_2_portfolio_list_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.2")


def test_marker_phase_3_3_briefing_customer_barriers_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.3")


def test_marker_phase_3_4_barrier_details_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.4")


def test_marker_phase_3_5_exec_briefing_bems_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.5")


def test_marker_phase_3_6_exec_briefing_csone_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.6")


def test_marker_phase_3_7_exec_briefing_barriers_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.7")


def test_marker_phase_3_8_minimal_briefing_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.8")


def test_marker_phase_3_9_cust_arr_fallback_normalize() -> None:
    assert _has_marker("adoptiq_backend.py", "3.9")


def test_marker_phase_3_10_engagement_merge_normalize() -> None:
    assert _has_marker("app_simple.py", "3.10")


def test_marker_phase_3_11_top_at_risk_normalize() -> None:
    assert _has_marker("app_simple.py", "3.11")


def test_marker_phase_3_12_stalled_ap_normalize() -> None:
    assert _has_marker("leader_report_generator.py", "3.12")


def test_marker_phase_3_13_voice_of_customer_normalize() -> None:
    assert _has_marker("compact_report_formatter.py", "3.13")


def test_marker_phase_3_14_ask_ai_fallback_normalize() -> None:
    assert _has_marker("ask_ai_grounded.py", "3.14")


def test_marker_phase_3_15_normalize_nfkc() -> None:
    src = _read("data_normalization.py")
    assert "Round 13 / Phase 3.15" in src
    assert "NFKC" in src


# ---------------------------------------------------------------------------
# Phase 4 - Web / HTML / API
# ---------------------------------------------------------------------------

def test_marker_phase_4_1_shell_html_no_store() -> None:
    assert _has_marker("app_simple.py", "4.1")


def test_marker_phase_4_2_fetch_error_escape() -> None:
    assert _has_marker("templates/external_intelligence.html", "4.2")


def test_marker_phase_4_3_csrf_error_ok() -> None:
    assert _has_marker("app_simple.py", "4.3")


def test_marker_phase_4_4_download_result_status_keys() -> None:
    assert _has_marker("app_simple.py", "4.4")


def test_marker_phase_4_5_partial_warnings_redact() -> None:
    assert _has_marker("app_simple.py", "4.5")


def test_marker_phase_4_6_history_analysis_id_digest() -> None:
    assert _has_marker("templates/history.html", "4.6")


def test_marker_phase_4_7_ask_ai_disclaimer() -> None:
    assert _has_marker("templates/ask_ai.html", "4.7")


def test_marker_phase_4_8_subscription_search_debug_flag() -> None:
    assert _has_marker("static/js/subscription-search.js", "4.8")


def test_marker_phase_4_9_error_only_shim() -> None:
    assert _has_marker("app_simple.py", "4.9")


# ---------------------------------------------------------------------------
# Phase 5 - Risk color palette consistency
# ---------------------------------------------------------------------------

def test_marker_phase_5_1_bems_heading_canonical() -> None:
    assert _has_marker("leader_report_generator.py", "5.1")


def test_marker_phase_5_2_health_color_canonical() -> None:
    assert _has_marker("leader_report_generator.py", "5.2")


def test_marker_phase_5_3_sentiment_bems_shading_canonical() -> None:
    assert _has_marker("leader_report_generator.py", "5.3")


def test_marker_phase_5_4_ei_traffic_light_aliases() -> None:
    assert _has_marker("executive_intelligence_formatter.py", "5.4")


def test_marker_phase_5_5_compact_crimson_canonical() -> None:
    assert _has_marker("compact_report_formatter.py", "5.5")


def test_marker_phase_5_6_excel_band_fills_aligned() -> None:
    assert _has_marker("app_simple.py", "5.6")


def test_marker_phase_5_7_health_grade_canonical() -> None:
    assert _has_marker("adoptiq_backend.py", "5.7")


def test_marker_phase_5_8_css_risk_vars() -> None:
    assert _has_marker("static/css/style.css", "5.8")


def test_marker_phase_5_9_base_html_tokens() -> None:
    assert _has_marker("templates/base.html", "5.9")


# ---------------------------------------------------------------------------
# Phase 6 - Truncation / sampling honesty
# ---------------------------------------------------------------------------

def test_marker_phase_6_1_top_at_risk_footer() -> None:
    assert _has_marker("app_simple.py", "6.1")


def test_marker_phase_6_2_top15_footer() -> None:
    assert _has_marker("app_simple.py", "6.2")


def test_marker_phase_6_3_sorted_by_risk_tiebreak_footer() -> None:
    assert _has_marker("app_simple.py", "6.3")


def test_marker_phase_6_4_ask_ai_cust_cat_disclosure() -> None:
    assert _has_marker("app_simple.py", "6.4")


def test_marker_phase_6_5_ask_ai_cases_sample_prefix() -> None:
    assert _has_marker("app_simple.py", "6.5")


def test_marker_phase_6_6_ask_ai_barriers_sort() -> None:
    assert _has_marker("app_simple.py", "6.6")


def test_marker_phase_6_7_compact_red_tiebreak() -> None:
    assert _has_marker("compact_report_formatter.py", "6.7")


def test_marker_phase_6_8_evidence_cap_audit() -> None:
    assert _has_marker("ask_ai_grounded.py", "6.8")


# ---------------------------------------------------------------------------
# Phase 7 - SQL determinism
# ---------------------------------------------------------------------------

def test_marker_phase_7_1_renewal_cases_tiebreak() -> None:
    assert _has_marker("adoptiq_backend.py", "7.1")


def test_marker_phase_7_2_admin_last_seen_tiebreak() -> None:
    assert _has_marker("enhanced_admin_dashboard_v2.py", "7.2")


def test_marker_phase_7_3_admin_security_events_tiebreak() -> None:
    assert _has_marker("enhanced_admin_dashboard_v2.py", "7.3")


def test_marker_phase_7_4_admin_error_log_tiebreak() -> None:
    assert _has_marker("enhanced_admin_dashboard_v2.py", "7.4")


def test_marker_phase_7_5_csone_discovery_order_by() -> None:
    assert _has_marker("snowflake_csone_discovery.py", "7.5")


def test_marker_phase_7_6_schema_probe_document() -> None:
    assert _has_marker("adoptiq_backend.py", "7.6")


def test_marker_phase_7_7_python_top10_stable() -> None:
    src = _read("app_simple.py")
    assert "Round 13 / Phase 7.7" in src or "Round 13 / Phase 6.3 + 7.7" in src


# ---------------------------------------------------------------------------
# Phase 8 - Charts / matplotlib
# ---------------------------------------------------------------------------

def test_marker_phase_8_1_pie_set_aspect() -> None:
    assert _has_marker("app_simple.py", "8.1") or _has_marker(
        "adoptiq_backend.py", "8.1"
    )


def test_marker_phase_8_2_tac_bar_legend_cb() -> None:
    assert _has_marker("adoptiq_backend.py", "8.2")


def test_marker_phase_8_3_bems_by_cust_neutral() -> None:
    assert _has_marker("app_simple.py", "8.3")


def test_marker_phase_8_4_weekly_bars_non_risk() -> None:
    assert _has_marker("app_simple.py", "8.4")


def test_marker_phase_8_5_plt_close_fig() -> None:
    assert _has_marker("adoptiq_backend.py", "8.5") or _has_marker(
        "app_simple.py", "8.5"
    )


def test_marker_phase_8_6_chart_label_truncation() -> None:
    assert _has_marker("app_simple.py", "8.6") or _has_marker(
        "adoptiq_backend.py", "8.6"
    )


# ---------------------------------------------------------------------------
# Phase 9 - Word / Excel parity / formatting
# ---------------------------------------------------------------------------

def test_marker_phase_9_1_app_word_table_safe() -> None:
    assert _has_marker("app_simple.py", "9.1")


def test_marker_phase_9_2_feature_requests_safe() -> None:
    assert _has_marker("app_simple.py", "9.2")


def test_marker_phase_9_3_ei_bems_safe_tiebreak() -> None:
    assert _has_marker("executive_intelligence_formatter.py", "9.3")


def test_marker_phase_9_4_snowflake_insights_arr_format() -> None:
    assert _has_marker("enhanced_snowflake_insights.py", "9.4")


def test_marker_phase_9_5_renewal_word_format() -> None:
    assert _has_marker("advanced_renewal_analyzer.py", "9.5")


def test_marker_phase_9_6_renewal_percent_format() -> None:
    assert _has_marker("advanced_renewal_analyzer.py", "9.6")


def test_marker_phase_9_7_leader_cssm_safe() -> None:
    assert _has_marker("leader_report_generator.py", "9.7")


def test_marker_phase_9_8_excel_fallback_styling() -> None:
    assert _has_marker("adoptiq_backend.py", "9.8")


def test_marker_phase_9_9_renewal_excel_bands() -> None:
    assert _has_marker("app_simple.py", "9.9")


def test_marker_phase_9_10_chart_alt_text() -> None:
    assert _has_marker("executive_intelligence_formatter.py", "9.10")


# ---------------------------------------------------------------------------
# Phase 10 - Admin / progress / freshness / observability
# ---------------------------------------------------------------------------

def test_marker_phase_10_1_connectivity_utc_stamp() -> None:
    src = _read("connectivity_diagnostics.py")
    assert "Round 13" in src and "diagnostics_at_utc" in src


def test_marker_phase_10_2_status_all_envelope() -> None:
    src = _read("app_simple.py")
    assert "Round 13 / Phase 10.2" in src
    assert "generated_at_utc" in src


def test_marker_phase_10_3_status_iso_z() -> None:
    assert _has_marker("app_simple.py", "10.3")


def test_marker_phase_10_4_audit_report_log_skip() -> None:
    assert _has_marker("enhanced_admin_dashboard_v2.py", "10.4")


def test_marker_phase_10_5_connectivity_proxy_redact() -> None:
    assert _has_marker("enhanced_admin_dashboard_v2.py", "10.5")


def test_marker_phase_10_6_filter_log_redact() -> None:
    assert _has_marker("app_simple.py", "10.6")


def test_marker_phase_10_7_structured_request_id() -> None:
    src = _read("structured_logging.py")
    assert "Round 13 / Phase 10.7" in src
    assert "bind_request_id" in src


def test_marker_phase_10_8_previous_reports_sort() -> None:
    assert _has_marker("app_simple.py", "10.8")


# ---------------------------------------------------------------------------
# Phase 11 - Polish / determinism / silent exceptions
# ---------------------------------------------------------------------------

def test_marker_phase_11_1_circuit_backoff_test_mode() -> None:
    assert _has_marker("adoptiq_backend.py", "11.1")


def test_marker_phase_11_2_bems_refs_sorted_set() -> None:
    assert _has_marker("app_simple.py", "11.2")


def test_marker_phase_11_3_introspection_failures_cap() -> None:
    assert _has_marker("adoptiq_backend.py", "11.3")


def test_marker_phase_11_4_analysis_status_ttl() -> None:
    assert _has_marker("app_simple.py", "11.4")


def test_marker_phase_11_5_status_except_debug_log() -> None:
    assert _has_marker("app_simple.py", "11.5")


def test_marker_phase_11_6_content_filter_warn() -> None:
    assert _has_marker("adoptiq_backend.py", "11.6")


def test_marker_phase_11_7_upload_name_deterministic() -> None:
    assert _has_marker("app_simple.py", "11.7")


def test_marker_phase_11_8_previous_reports_stable() -> None:
    src = _read("app_simple.py")
    assert "Round 13 / Phase 11.8" in src or "Round 13 / Phase 10.8 + 11.8" in src
