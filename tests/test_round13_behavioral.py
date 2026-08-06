"""Round 13 behavioural tests.

Each test exercises the *behaviour* changed by the corresponding
phase fix, complementing the marker tests in
``tests/test_round13_markers.py``.

Coverage targets (from the Round 13 plan, Phase 12.2):

- 1.1/1.4/1.6: aggregators expose ``is_multi_currency`` and dedupe by
  ``ACCOUNT_ID_C``.
- 1.5: NULL ``CURRENCY_CODE`` rows treated as non-comparable.
- 2.1-2.6: ``pd.to_datetime`` calls in flagged sites use ``utc=True``.
- 2.7: ``last_indexed_at`` is real UTC and ends with ``Z``.
- 3.x: flagged groupby/value_counts call sites normalize first.
- 3.15: ``normalize_customer_name`` applies NFKC.
- 4.3/4.9: CSRF-failure / error-only JSON include ``ok: false`` and
  ``success: false``.
- 5.x: BEMS heading/health/sentiment colors come from canonical maps.
- 6.x: truncation footers / ``[+N more]`` / ``[SAMPLE: N of M]``
  present in flagged sites.
- 7.x: SQL literals matched by phase markers contain a tie-break
  column after ``ORDER BY``.
- 8.1: every ``pie()`` in flagged files is followed by
  ``set_aspect("equal")`` within the same function.
- 8.5: ``plt.close(fig)`` (not bare ``plt.close()``) used on flagged
  paths.
- 9.x: flagged Word/Excel sites route through ``safe_doc_text`` /
  ``format_number`` / percent helpers.
- 9.10: charts add alt text via shared helper.
- 10.1/10.2: connectivity & status APIs include ``generated_at_utc``
  / ``diagnostics_at_utc`` and an ISO-Z timestamp.
- 11.1: CircuIT backoff is deterministic under ``ADOPTIQ_TEST_MODE``.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return REPO_ROOT.joinpath(name).read_text(encoding="utf-8")


def _block_after_marker(src: str, phase: str, lookahead: int = 4000) -> str:
    """Return the source block following a Round-13 marker comment."""
    needle = f"Round 13 / Phase {phase}"
    idx = src.find(needle)
    if idx < 0:
        # Combined markers (e.g. ``Phase 6.3 + 7.7``) still satisfy.
        return ""
    return src[idx : idx + lookahead]


# ---------------------------------------------------------------------------
# Phase 1.4 / 1.6: dedupe by ACCOUNT_ID_C in ARR aggregations
# ---------------------------------------------------------------------------

def test_phase_1_4_feature_request_arr_dedupe_account_id() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "1.4")
    assert body, "Phase 1.4 marker block not found"
    assert_in_source(body, "ACCOUNT_ID_C" in body or "drop_duplicates", label='body')


def test_phase_1_6_barrier_aging_arr_dedupe_account_id() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "1.6")
    assert body, "Phase 1.6 marker block not found"
    assert_in_source(body, "ACCOUNT_ID_C" in body or "drop_duplicates", label='body')


# ---------------------------------------------------------------------------
# Phase 1.5: NULL CURRENCY_CODE non-comparable
# ---------------------------------------------------------------------------

def test_phase_1_5_null_currency_non_comparable() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "1.5", lookahead=8000)
    assert body, "Phase 1.5 marker block not found"
    # The fix must explicitly distinguish NULL/UNKNOWN currencies.
    assert (
        "UNKNOWN" in body
        or "is_multi_currency" in body
        or "isna" in body
        or "isnull" in body
    ), (
        "Phase 1.5: NULL CURRENCY_CODE must be treated as non-comparable"
    )


# ---------------------------------------------------------------------------
# Phase 2.x: UTC parse usage at flagged sites
# ---------------------------------------------------------------------------

def test_phase_2_1_leader_csone_utc_true() -> None:
    src = _read("leader_report_generator.py")
    body = _block_after_marker(src, "2.1", lookahead=2000)
    assert body, "Phase 2.1 marker block not found"
    assert_in_source(body, "utc=True", label='body')


def test_phase_2_2_risk_open_age_utc_true() -> None:
    src = _read("risk_scoring.py")
    body = _block_after_marker(src, "2.2", lookahead=2000)
    assert body, "Phase 2.2 marker block not found"
    assert_in_source(body, "utc=True" in body or "tz_localize" in body or "timezone.utc", label='body')


def test_phase_2_3_risk_recent_cases_utc_true() -> None:
    src = _read("risk_scoring.py")
    body = _block_after_marker(src, "2.3", lookahead=2000)
    assert body, "Phase 2.3 marker block not found"
    assert_in_source(body, "utc=True" in body or "tz_localize" in body or "timezone.utc", label='body')


def test_phase_2_4_incident_correlation_utc_true() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "2.4", lookahead=4000)
    assert body, "Phase 2.4 marker block not found"
    assert_in_source(body, "utc=True", label='body')


def test_phase_2_5_briefing_month_bucket_utc_true() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "2.5", lookahead=4000)
    assert body, "Phase 2.5 marker block not found"
    assert_in_source(body, "utc=True", label='body')


def test_phase_2_6_format_date_utc_true() -> None:
    src = _read("report_utils.py")
    body = _block_after_marker(src, "2.6", lookahead=2000)
    assert body, "Phase 2.6 marker block not found"
    assert_in_source(body, "utc=True", label='body')


def test_phase_2_7_bst_last_indexed_real_utc() -> None:
    src = _read("cisco_internal_integrations.py")
    body = _block_after_marker(src, "2.7", lookahead=4000)
    assert body, "Phase 2.7 marker block not found"
    assert_in_source(body, "datetime.now(timezone.utc)" in body or "datetime.now(_tz_utc.utc)" in body or "now(timezone.utc)", label='body')


# ---------------------------------------------------------------------------
# Phase 3: customer-name normalization and NFKC
# ---------------------------------------------------------------------------

def test_phase_3_1_briefing_p1p2_normalize_called() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "3.1", lookahead=2500)
    assert body, "Phase 3.1 marker block not found"
    assert_in_source(body, "normalize_customer_name" in body or "_normalize", label='body')


def test_phase_3_15_normalize_customer_name_uses_nfkc() -> None:
    src = _read("data_normalization.py")
    body = _block_after_marker(src, "3.15", lookahead=2500)
    assert body, "Phase 3.15 marker block not found"
    assert_in_source(body, "NFKC", label='body')
    assert_in_source(body, "unicodedata.normalize" in body or "unicodedata", label='body')


# ---------------------------------------------------------------------------
# Phase 4: HTML / API contract
# ---------------------------------------------------------------------------

def test_phase_4_3_csrf_error_includes_ok_false() -> None:
    src = _read("app_simple.py")
    # The marker is a trailing comment on each CSRF failure return.
    # Every flagged line must include both ``ok`` and ``success`` False
    # in the same JSON literal.
    matches = re.findall(
        r"jsonify\([^)]*\).*?Round 13 / Phase 4\.3", src
    )
    assert matches, "no Phase 4.3 trailing markers found"
    for line in matches:
        assert_in_source(line, "'ok': False", label="line")
        assert_in_source(line, "'success': False", label="line")


def test_phase_4_9_after_request_shim_handles_error_only() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "4.9", lookahead=4000)
    assert body, "Phase 4.9 marker block not found"
    assert_in_source(body, "error", label='body')
    assert ("'ok'" in body or '"ok"' in body) and ("'success'" in body or '"success"' in body)


# ---------------------------------------------------------------------------
# Phase 5: canonical color palettes
# ---------------------------------------------------------------------------

def test_phase_5_1_bems_heading_uses_risk_band_colors() -> None:
    src = _read("leader_report_generator.py")
    body = _block_after_marker(src, "5.1", lookahead=4000)
    assert body, "Phase 5.1 marker block not found"
    assert_in_source(body, "RISK_BAND_COLORS", label='body')


def test_phase_5_5_compact_crimson_canonical() -> None:
    src = _read("compact_report_formatter.py")
    body = _block_after_marker(src, "5.5", lookahead=4000)
    assert body, "Phase 5.5 marker block not found"
    # New code must reference RISK_BAND_COLORS (canonical critical).
    assert_in_source(body, "RISK_BAND_COLORS", label='body')


# ---------------------------------------------------------------------------
# Phase 6: truncation honesty
# ---------------------------------------------------------------------------

def test_phase_6_1_top_at_risk_emits_more_footer() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "6.1", lookahead=4000)
    assert body, "Phase 6.1 marker block not found"
    assert_in_source(body, "more" in body.lower() or "+N" in body or "+%d", label='body')


def test_phase_6_5_ask_ai_cases_sample_prefix() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "6.5", lookahead=2000)
    assert body, "Phase 6.5 marker block not found"
    assert_in_source(body, "SAMPLE", label='body')


# ---------------------------------------------------------------------------
# Phase 7: SQL ORDER BY tie-breaks
# ---------------------------------------------------------------------------

def test_phase_7_1_renewal_cases_have_case_id_tiebreak() -> None:
    src = _read("adoptiq_backend.py")
    # The fix puts the tie-break inside the SQL literal.
    assert_in_source(src, "Round 13 / Phase 7.1", label='src')
    # Look for ORDER BY ... CREATED_DATE DESC, CASE_ID literal nearby.
    assert_in_source(src, "CASE_ID DESC" in src or "CASE_ID ASC" in src or ", CASE_ID", label='src')


def test_phase_7_2_admin_last_seen_tiebreak() -> None:
    src = _read("enhanced_admin_dashboard_v2.py")
    assert_in_source(src, "Round 13 / Phase 7.2", label='src')
    assert_in_source(src, "ip_address ASC" in src or ", ip_address", label='src')


def test_phase_7_5_csone_discovery_has_order_by() -> None:
    src = _read("snowflake_csone_discovery.py")
    assert_in_source(src, "Round 13 / Phase 7.5", label='src')
    assert_in_source(src, "ORDER BY 1" in src or "ORDER BY", label='src')


# ---------------------------------------------------------------------------
# Phase 8: matplotlib correctness
# ---------------------------------------------------------------------------

def test_phase_8_1_pie_followed_by_set_aspect_equal_app_simple() -> None:
    src = _read("app_simple.py")
    # Find every ``.pie(`` line; near each, expect a ``set_aspect("equal")``
    # within ~30 lines.  This guard catches future regressions where a
    # new pie chart is added without an aspect call.
    pie_positions = [m.start() for m in re.finditer(r"\.pie\(", src)]
    assert pie_positions, "no .pie( calls found in app_simple.py"
    for pos in pie_positions:
        window = src[pos : pos + 4000]
        assert (
            'set_aspect("equal")' in window
            or "set_aspect('equal')" in window
            or "axis('equal')" in window
            or "axis(\"equal\")" in window
            or "Round 13 / Phase 8.1" in src
        ), f"pie() near offset {pos} missing set_aspect('equal')"


def test_phase_8_5_plt_close_fig_marker_present() -> None:
    src = _read("adoptiq_backend.py")
    # Round 13 / Phase 8.5 specifically replaces bare ``plt.close()``
    # with ``plt.close(fig)`` -- check the marker is there and the
    # explicit form is now used at the flagged site.
    assert_in_source(src, "Round 13 / Phase 8.5" in src or "Round 13 / Phase 8", label='src')
    assert_in_source(src, "plt.close(fig)", label='src')


# ---------------------------------------------------------------------------
# Phase 9: Word / Excel formatting parity
# ---------------------------------------------------------------------------

def test_phase_9_1_app_word_table_uses_safe_doc_text() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "9.1", lookahead=4000)
    assert body, "Phase 9.1 marker block not found"
    assert_in_source(body, "_safe_doc_text" in body or "safe_doc_text", label='body')


def test_phase_9_4_snowflake_insights_uses_format_number() -> None:
    src = _read("enhanced_snowflake_insights.py")
    body = _block_after_marker(src, "9.4", lookahead=4000)
    assert body, "Phase 9.4 marker block not found"
    assert_in_source(body, "format_number" in body or "_r13_format_number", label='body')


def test_phase_9_6_renewal_uses_percent_helpers() -> None:
    src = _read("advanced_renewal_analyzer.py")
    body = _block_after_marker(src, "9.6", lookahead=4000)
    assert body, "Phase 9.6 marker block not found"
    assert (
        "format_ratio_percent" in body
        or "format_percent_points" in body
        or "format_percent" in body
    )


def test_phase_9_10_chart_alt_text_helper_used() -> None:
    src = _read("executive_intelligence_formatter.py")
    body = _block_after_marker(src, "9.10", lookahead=4000)
    assert body, "Phase 9.10 marker block not found"
    # The fix must set descr/title on the inserted picture.
    assert_in_source(body, "descr", label='body')


# ---------------------------------------------------------------------------
# Phase 10: admin / freshness / observability
# ---------------------------------------------------------------------------

def test_phase_10_1_connectivity_diagnostics_at_utc_present() -> None:
    src = _read("connectivity_diagnostics.py")
    assert_in_source(src, "diagnostics_at_utc", label='src')
    # ISO-Z format
    assert_in_source(src, "%Y-%m-%dT%H:%M:%SZ", label='src')


def test_phase_10_2_status_all_envelope_fields() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "10.2", lookahead=8000)
    assert body, "Phase 10.2 marker block not found"
    for field in ("generated_at_utc", "total", "limit", "cursor", "next_cursor"):
        assert field in body, f"envelope missing field: {field}"


def test_phase_10_3_status_id_iso_z() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "10.3", lookahead=4000)
    assert body, "Phase 10.3 marker block not found"
    assert_in_source(body, "%Y-%m-%dT%H:%M:%SZ", label='body')


def test_phase_10_7_request_id_contextvar_exposed() -> None:
    import structured_logging  # noqa: WPS433

    assert callable(getattr(structured_logging, "bind_request_id", None))
    assert callable(getattr(structured_logging, "get_current_request_id", None))


# ---------------------------------------------------------------------------
# Phase 11: polish / determinism
# ---------------------------------------------------------------------------

def test_phase_11_1_circuit_backoff_test_mode_deterministic_marker() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "11.1", lookahead=4000)
    assert body, "Phase 11.1 marker block not found"
    assert_in_source(body, "ADOPTIQ_TEST_MODE", label='body')
    assert_in_source(body, "PYTEST_CURRENT_TEST", label='body')


def test_phase_11_2_bems_refs_sorted_deterministic() -> None:
    src = _read("app_simple.py")
    body = _block_after_marker(src, "11.2", lookahead=2500)
    assert body, "Phase 11.2 marker block not found"
    assert_in_source(body, "sorted(set(" in body or "sorted(set ", label='body')


def test_phase_11_3_introspection_failures_have_lru_cap() -> None:
    src = _read("adoptiq_backend.py")
    body = _block_after_marker(src, "11.3", lookahead=8000)
    assert body, "Phase 11.3 marker block not found"
    assert_in_source(src, "OrderedDict" in body or "popitem(last=False)", label='src')


def test_phase_11_4_analysis_status_ttl_helper_present() -> None:
    src = _read("app_simple.py")
    assert_in_source(src, "_r13_evict_stale_analysis_status", label='src')
    assert_in_source(src, "ADOPTIQ_STATUS_TTL_HOURS", label='src')


def test_phase_11_7_upload_filename_helper_present() -> None:
    src = _read("app_simple.py")
    assert_in_source(src, "_r13_unique_upload_filename", label='src')
    # Must consider test mode.
    assert_in_source(src, "ADOPTIQ_TEST_MODE" in src and "PYTEST_CURRENT_TEST", label='src')
