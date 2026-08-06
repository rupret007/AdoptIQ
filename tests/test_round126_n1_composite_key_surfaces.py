"""Round 126 / Build 95 (N1) regression tests.

N1 extends the R125 composite-key collapse (compact High_Risk surfaces) to
the comprehensive per-customer narratives so the raw Snowflake join key
(``ELEVANCE_ELEVANCE HEALTH_US`` / ``X__Y__US``) can never reach ANY
customer-facing surface: the per-customer LLM prompt, the section headings,
the "Customer:" lines, and the withheld/fallback/error mini-sections.

The raw ``customer_name`` MUST remain the join key everywhere it filters a
DataFrame or looks up a profile/cssm, so these tests pin the source shape of
the comprehensive loop: a single ``_disp_customer`` display variable is
derived from ``_normalize_composite_customer_key`` and routed to the surfaces
while the join sites keep the raw key.
"""

from source_shape_utils import assert_in_source
import inspect

import app_simple


def _comprehensive_loop_src() -> str:
    src = inspect.getsource(app_simple)
    idx = src.index("for i, customer_name in enumerate(all_customers, 1):")
    # The per-customer loop body runs ~700 lines; grab a generous window.
    return src[idx : idx + 90000]


def test_disp_customer_derived_from_composite_normalizer():
    block = _comprehensive_loop_src()
    assert_in_source(block, "_disp_customer = _normalize_composite_customer_key", label='block')


def test_prompt_uses_display_name_not_raw_key():
    block = _comprehensive_loop_src()
    # The per-customer prompt must format CUSTOMER_NAME from the display var.
    assert_in_source(block, "CUSTOMER_NAME=_disp_customer", label='block')
    # And the raw-key form must be gone from the prompt format call.
    assert "CUSTOMER_NAME=customer_name" not in block


def test_disp_customer_added_to_r27_allowed_entities():
    block = _comprehensive_loop_src()
    # So the validator does not flag the collapsed name as an invented entity.
    assert_in_source(block, "_disp_customer,  # Round 126 / Build 95 (N1)", label='block')


def test_join_sites_still_use_raw_customer_name():
    block = _comprehensive_loop_src()
    # The DataFrame filters and lookups MUST keep the raw join key -- collapsing
    # it would silently break the joins (the SSoT docstring's warning).
    assert_in_source(block, "ab_norm['customer_name'] == customer_name", label='block')
    assert_in_source(block, "risk_profiles.get(customer_name)", label='block')
    assert_in_source(block, "cssm_lookup.get(customer_name", label='block')


def test_fallback_and_withheld_headings_use_display_name():
    block = _comprehensive_loop_src()
    # Withheld + LLM-unavailable mini-sections render the display name.
    assert_in_source(block, '_safe_doc_text(f"{_disp_customer} Analysis")', label='block')
    # Exception path re-resolves defensively.
    assert_in_source(block, "_disp_customer_exc = _normalize_composite_customer_key", label='block')
    assert_in_source(block, '_safe_doc_text(f"{_disp_customer_exc} Analysis")', label='block')


def test_a1_activity_empty_section_uses_display_name():
    block = _comprehensive_loop_src()
    assert_in_source(block, 'f"AdoptIQ Executive Analysis: {_disp_customer}"', label='block')


def test_n1_marker_present():
    block = _comprehensive_loop_src()
    assert_in_source(block, "Round 126 / Build 95 (N1)", label='block')
