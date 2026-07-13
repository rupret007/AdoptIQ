"""Round 39 / Phase 2.1 — sanitize raw Snowflake errors before render.

Pre-Round-39 the per-customer Enhanced Snowflake Insights renderer
wrote ``section_errors`` verbatim into the Word document.  In the
Brian Frazier 90d audit the rendered document carried:

  * ``CISCO_TIER_RANKING__C`` SQL error 102 times
  * ``Round 7 / Phase 2.5`` dev-phase marker 204 times
  * Trace IDs like ``01c40515-0619-a2d6-0042-210ce1c31bbf``
  * Internal column names ``ARR_AMOUNT``, ``SUBJECT_C``

A director reading the report sees raw SQL compilation errors next to
"Validation Status: PASSED" -- the report contradicts itself.

Round 39 / Phase 2.1 introduces ``_sanitize_snowflake_error()`` which
strips error codes, trace IDs, ``Round X / Phase Y.Z`` markers,
internal ``__C`` column names, and full SQL after "SQL compilation
error".  The renderer also dedupes by sanitized message so the same
failure text doesn't appear dozens of times.

This file pins the sanitizer with sample raw-error strings drawn from
the audit transcript.
"""
from __future__ import annotations

import pytest


def test_sanitizer_strips_snowflake_error_codes():
    from leader_report_generator import _sanitize_snowflake_error
    raw = (
        "000904 (42000): SQL compilation error: error line 5 at "
        "position 8 invalid identifier 'CISCO_TIER_RANKING__C'"
    )
    out = _sanitize_snowflake_error(raw)
    assert "000904" not in out
    assert "CISCO_TIER_RANKING__C" not in out
    assert "SQL compilation error" not in out


def test_sanitizer_strips_dev_phase_markers():
    from leader_report_generator import _sanitize_snowflake_error
    raw = "Round 7 / Phase 2.5: account_insights query returned no rows"
    out = _sanitize_snowflake_error(raw)
    # The user-facing line should NOT contain the internal dev marker.
    assert "Round 7 / Phase 2.5" not in out


def test_sanitizer_strips_trace_ids():
    from leader_report_generator import _sanitize_snowflake_error
    raw = "Snowflake request 01c40515-0619-a2d6-0042-210ce1c31bbf failed"
    out = _sanitize_snowflake_error(raw)
    assert "01c40515-0619-a2d6-0042-210ce1c31bbf" not in out


@pytest.mark.parametrize("raw_col", [
    "ARR_AMOUNT", "SUBJECT_C", "CISCO_TIER_RANKING__C",
])
def test_sanitizer_strips_internal_column_names(raw_col):
    from leader_report_generator import _sanitize_snowflake_error
    raw = f"invalid identifier '{raw_col}' in SELECT clause"
    out = _sanitize_snowflake_error(raw)
    assert raw_col not in out, (
        f"Round 39 / Phase 2.1: sanitizer must strip internal Snowflake "
        f"column name {raw_col!r}; rendered output {out!r}."
    )


def test_sanitizer_returns_user_facing_fallback_on_empty():
    from leader_report_generator import _sanitize_snowflake_error
    out = _sanitize_snowflake_error("000904 (42000): SQL compilation error")
    # After scrubbing nearly everything we should still produce a
    # non-empty user-facing line so the renderer has something to show.
    assert out and len(out.strip()) > 0
    assert "SQL compilation error" not in out


def test_user_facing_message_constant_is_safe():
    """The shared user-facing fallback constant must read like
    customer-friendly prose, not internal jargon."""
    from leader_report_generator import _SNOWFLAKE_USER_FACING_MSG
    txt = _SNOWFLAKE_USER_FACING_MSG.lower()
    forbidden = [
        "sql", "round 7", "round 39", "trace id", "__c",
        "select", "where", "from edw_", "snowflake_",
    ]
    for tok in forbidden:
        assert tok not in txt, (
            f"Round 39 / Phase 2.1: user-facing fallback must not "
            f"contain {tok!r}.  Current message: {_SNOWFLAKE_USER_FACING_MSG!r}"
        )


def test_renderer_uses_sanitizer_at_known_call_sites():
    """Pin that the two leader_report_generator render call sites for
    section_errors route through ``_sanitize_snowflake_error`` instead
    of writing the raw error string directly."""
    import inspect
    import leader_report_generator as lrg

    customer_detail_src = inspect.getsource(
        lrg.LeaderReportGenerator._add_customer_enhanced_insights
    )
    enhanced_insights_src = inspect.getsource(
        lrg.LeaderReportGenerator._add_enhanced_snowflake_insights
    )
    for src, name in (
        (customer_detail_src, "_add_customer_enhanced_insights"),
        (enhanced_insights_src, "_add_enhanced_snowflake_insights"),
    ):
        assert "_sanitize_snowflake_error" in src, (
            f"Round 39 / Phase 2.1: {name} must route section_errors "
            f"through ``_sanitize_snowflake_error`` before adding any "
            f"paragraph -- otherwise raw SQL leaks into the docx."
        )
