"""Round 125 / Build 94 (B1+B4) -- composite customer-key normalization.

Pins ``data_normalization.normalize_composite_customer_key`` -- the SSoT
that both ``app_simple`` and ``executive_intelligence_formatter`` now share
so the compact high-risk DOCX table + ``High_Risk_Customers`` sheet no
longer leak the raw Snowflake join key ``ELEVANCE_ELEVANCE HEALTH_US``.
"""

import data_normalization as dn


def test_elevance_composite_collapses_to_human_name():
    assert dn.normalize_composite_customer_key("ELEVANCE_ELEVANCE HEALTH_US") == "ELEVANCE HEALTH"


def test_double_underscore_keeps_first_segment():
    assert dn.normalize_composite_customer_key("ACME__SUBSIDIARY__US") == "ACME"
    assert dn.normalize_composite_customer_key("ACME__SUBSIDIARY") == "ACME"


def test_single_underscore_country_tail_stripped():
    assert dn.normalize_composite_customer_key("FOO_BAR_US") == "FOO_BAR"


def test_plain_name_unchanged():
    assert dn.normalize_composite_customer_key("Farmers Insurance") == "Farmers Insurance"


def test_none_and_empty():
    assert dn.normalize_composite_customer_key(None) == ""
    assert dn.normalize_composite_customer_key("") == ""
    assert dn.normalize_composite_customer_key("   ") == ""


def test_idempotent_on_own_output():
    once = dn.normalize_composite_customer_key("ELEVANCE_ELEVANCE HEALTH_US")
    twice = dn.normalize_composite_customer_key(once)
    assert once == twice == "ELEVANCE HEALTH"
