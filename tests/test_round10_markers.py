"""Round 10 marker tests.

For every HIGH/MED phase fix in the Round 10 reporting accuracy audit
this module asserts that the source-marker comment landed in the
expected file.  The behavioural tests live in
``tests/test_round10_behavioral.py``.

Pattern mirrors Rounds 5-9: a marker test guarantees the fix is not
silently reverted; a behavioural test guarantees the fix is correct.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding='utf-8')


# Phase 1 ---------------------------------------------------------------

def test_marker_phase_1_1_renewal_excel_keys() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 1.1' in src or "renewal_risk_score', cust_analysis.get('overall_risk_score'", label='src')


def test_marker_phase_1_2_port_category_healthy() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 1.2' in src or 'HEALTHY', label='src')


def test_marker_phase_1_3_renewal_exact_match_ambiguity() -> None:
    src = _read('advanced_renewal_analyzer.py')
    assert_in_source(src, 'Round 10 / Phase 1.3', label='src')
    assert_in_source(src, "'ambiguous'", label='src')


def test_marker_phase_1_4_engagement_format_not_percent() -> None:
    src = _read('advanced_renewal_analyzer.py')
    assert_in_source(src, 'Round 10 / Phase 1.4', label='src')


def test_marker_phase_1_5_renewal_gauge_1dp() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 1.5' in src or "f'{float(risk_score):.1f}/100'" in src or '{risk_score:.1f}/100', label='src')


def test_marker_phase_1_6_correlated_incidents_utc() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 1.6', label='src')


# Phase 2 ---------------------------------------------------------------

def test_marker_phase_2_1_key_concerns_escalated() -> None:
    src = _read('compact_report_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 2.1', label='src')
    assert_in_source(src, 'escalated_cases', label='src')


def test_marker_phase_2_2_band_helper_shared() -> None:
    src = _read('compact_report_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 2.2', label='src')
    assert_in_source(src, '_compact_portfolio_band', label='src')


def test_marker_phase_2_3_high_risk_customers_canonical() -> None:
    src = _read('compact_report_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 2.3', label='src')
    assert_in_source(src, 'is_high_risk_profile', label='src')


def test_marker_phase_2_4_top10_tie_stable() -> None:
    src = _read('compact_report_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 2.4', label='src')


# Phase 3 ---------------------------------------------------------------

def test_marker_phase_3_1_top5_bu_collision() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.1', label='src')


def test_marker_phase_3_2_historical_scan() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.2', label='src')


def test_marker_phase_3_3_cross_report_trends() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.3', label='src')


def test_marker_phase_3_4_repeat_offenders_multicurrency() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.4', label='src')


def test_marker_phase_3_5_tech_hotspot_dedupe() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.5', label='src')


def test_marker_phase_3_6_concentration_without_bu_name() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.6', label='src')


def test_marker_phase_3_7_comprehensive_portfolio_metrics_bands() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 3.7', label='src')


def test_marker_phase_3_8_portfolio_pie_1dp_autopct() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.8', label='src')


def test_marker_phase_3_9_title_page_zero_metrics() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 3.9', label='src')


# Phase 4 ---------------------------------------------------------------

def test_marker_phase_4_1_ei_high_risk_canonical() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 4.1', label='src')
    assert_in_source(src, 'is_high_risk_profile', label='src')


def test_marker_phase_4_2_bems_case_type_scope() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 4.2', label='src')
    assert_in_source(src, 'BEMS Case Type', label='src')


def test_marker_phase_4_3_psirt_clarity() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 4.3', label='src')
    assert 'distinct' in src.lower()


def test_marker_phase_4_4_data_citation_units() -> None:
    src = _read('executive_intelligence_formatter.py')
    assert_in_source(src, 'Round 10 / Phase 4.4', label='src')


# Phase 5 ---------------------------------------------------------------

def test_marker_phase_5_1_ab_date_predicate_unify() -> None:
    src_l = _read('leader_report_generator.py')
    src_r = _read('advanced_renewal_analyzer.py')
    # Both files were updated to use the COALESCE date_expr.
    assert 'COALESCE(' in src_l
    assert 'COALESCE(' in src_r


def test_marker_phase_5_2_leader_renewal_utc_window() -> None:
    src_l = _read('leader_report_generator.py')
    src_r = _read('advanced_renewal_analyzer.py')
    assert '_utc_window_start_iso' in src_l
    assert '_utc_window_start_iso' in src_r


def test_marker_phase_5_3_status_active_case_fold() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 5.3', label='src')
    assert_in_source(src, "UPPER(TRIM(STATUS_C))", label='src')


def test_marker_phase_5_4_collab_account_dedupe() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 5.4' in src or '_seen_acct_ids', label='src')


def test_marker_phase_5_5_incident_window_canonical_field() -> None:
    src = _read('incident_storage.py')
    assert_in_source(src, 'Round 10 / Phase 5.5' in src or "COALESCE(NULLIF(published, '')", label='src')


# Phase 6 ---------------------------------------------------------------

def test_marker_phase_6_1_grounded_safe_opener_strict() -> None:
    src = _read('ask_ai_grounded.py')
    assert_in_source(src, 'Round 10 / Phase 6.1' in src or '_qualitative_sentence_is_cited', label='src')


def test_marker_phase_6_2_intel_canonical_numbers_cap() -> None:
    src = _read('ask_ai_grounded.py')
    assert_in_source(src, 'Round 10 / Phase 6.2' in src or 'min(len(_items), 120)', label='src')


def test_marker_phase_6_3_compact_prompt_extrapolation() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 6.3', label='src')


def test_marker_phase_6_4_jsonschema_strict_in_prod() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 6.4', label='src')


# Phase 7 ---------------------------------------------------------------

def test_marker_phase_7_1_compact_excel_action_plans() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 7.1', label='src')
    tree = ast.parse(src)
    action_plans_is_in_compact_extra_frames = any(
        isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == '_candidate_df'
        and isinstance(node.iter, (ast.Tuple, ast.List))
        and any(
            isinstance(item, ast.Name) and item.id == 'csconsole_action_plans'
            for item in node.iter.elts
        )
        for node in ast.walk(tree)
    )
    assert action_plans_is_in_compact_extra_frames


def test_marker_phase_7_2_overall_risk_single_mean() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 7.2', label='src')


def test_marker_phase_7_3_leader_num_customers_column() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 7.3', label='src')
    tree = ast.parse(src)
    num_customer_values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == 'Num_Customers':
                num_customer_values.append(value)
    assert len(num_customer_values) >= 2
    assert all(
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == 'len'
        and len(value.args) == 1
        for value in num_customer_values
    )


# Phase 8 ---------------------------------------------------------------

def test_marker_phase_8_1_renewal_dashboard_key_metrics() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 8.1', label='src')


def test_marker_phase_8_2_feature_requests_stable_sort() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 8.2', label='src')


def test_marker_phase_8_3_top10_customers_stable_sort() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 8.3', label='src')


# Phase 9 ---------------------------------------------------------------

def test_marker_phase_9_1_partial_data_warning_promote() -> None:
    src = _read('data_normalization.py')
    assert_in_source(src, 'Round 10 / Phase 9.1', label='src')
    assert_in_source(src, "use.attrs['partial_data_warnings']", label='src')


def test_marker_phase_9_2_deep_dive_normalize_keys() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 9.2', label='src')


def test_marker_phase_9_3_arr_schema_degraded_warning() -> None:
    src = _read('adoptiq_backend.py')
    assert_in_source(src, 'Round 10 / Phase 9.3', label='src')
    assert_in_source(src, "arr_schema_degraded", label='src')


def test_marker_phase_9_4_leader_data_retrieved_at_required() -> None:
    src = _read('leader_report_generator.py')
    assert_in_source(src, 'Round 10 / Phase 9.4', label='src')
    assert_in_source(src, '_data_retrieved_at_is_render_time', label='src')


def test_marker_phase_9_5_renewal_cover_utc_data_as_of() -> None:
    src = _read('app_simple.py')
    assert_in_source(src, 'Round 10 / Phase 9.5', label='src')
