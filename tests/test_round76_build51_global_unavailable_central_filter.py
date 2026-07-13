"""Round 76 / Build 51 tests -- centralised global-config filter.

Build 50 acceptance found that the per-section ``globally_unavailable_section``
tagging (Round 76 / R76-B Build 50 implementation) only caught
server-side error strings (``"does not exist"`` / ``"not authorized"``
/ ``"invalid identifier"``) but missed two production-common error
classes:

  1. ``"column_missing: ..."`` -- raised by ``_resolve_columns``
     short-circuit when a critical column is absent (e.g. the
     ``CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU`` ``ARR_AMOUNT`` column
     missing in the contract section).
  2. ``"not in allowlist policy"`` -- raised by
     ``_PolicyEnforcingCursor`` BEFORE the SQL even reaches Snowflake
     when the table is not in
     ``snowflake_table_policy.ALLOWED_TABLES``.

Build 51 adds:

  * A static helper ``_is_globally_unavailable_error(err_str)`` that
    recognises all four global-config error classes.
  * Defense-in-depth detection in
    ``get_comprehensive_customer_insights`` so a section that emits a
    global-config error WITHOUT tagging itself
    (``insights['globally_unavailable_section'] = True``) is still
    routed into ``_globally_unavailable_sections`` and dropped from
    ``section_errors``.

This file pins both pieces.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import enhanced_snowflake_insights
from enhanced_snowflake_insights import EnhancedSnowflakeInsights


def test_is_globally_unavailable_error_recognises_does_not_exist() -> None:
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Object 'CX_DB.X.RISK_ASSESSMENT' does not exist or not authorized."
    )


def test_is_globally_unavailable_error_recognises_not_authorized() -> None:
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "User not authorized to read table"
    )


def test_is_globally_unavailable_error_recognises_invalid_identifier() -> None:
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "SQL compilation error: invalid identifier 'AMOUNT'"
    )


def test_is_globally_unavailable_error_recognises_column_missing_short_form() -> None:
    """Build 50 missed this -- ``column_missing:`` is the
    `_resolve_columns` short-circuit shape.
    """
    err = (
        "column_missing: CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU "
        "missing required column(s) ['ACCOUNT_ID_C', 'ARR_AMOUNT']."
    )
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(err)


def test_is_globally_unavailable_error_recognises_missing_required_column_long_form() -> None:
    """The same column-missing condition phrased in a different way."""
    err = "Section skipped: missing required column ARR_AMOUNT in COLLAB_ARR_CON_SKU"
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(err)


def test_is_globally_unavailable_error_recognises_table_policy_violation() -> None:
    """Build 50 missed this too -- ``_PolicyEnforcingCursor`` raises
    ``TablePolicyViolation`` BEFORE the SQL reaches Snowflake.  This
    is the ROOT cause of the 421-token Build 50 leakage.
    """
    err = "Snowflake table not in allowlist policy: CX_DB.CX_SWSSBST_BR.RISK_ASSESSMENT"
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(err)


def test_is_globally_unavailable_error_rejects_transient_error() -> None:
    """A genuinely transient error (network timeout, throttle) is
    NOT a global-config issue and should NOT collapse.
    """
    assert not EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Connection reset by peer"
    )
    assert not EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Snowflake query timeout (300s)"
    )


def test_is_globally_unavailable_error_rejects_empty_or_none() -> None:
    assert not EnhancedSnowflakeInsights._is_globally_unavailable_error("")
    assert not EnhancedSnowflakeInsights._is_globally_unavailable_error(None)  # type: ignore


def test_is_globally_unavailable_error_case_insensitive() -> None:
    """Case folding is required so error variations don't sneak past."""
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Snowflake TABLE NOT IN ALLOWLIST POLICY: CX_DB.X.Y"
    )


def _build_instance_with_mock_ctx() -> EnhancedSnowflakeInsights:
    """Construct an EnhancedSnowflakeInsights with a mock ctx so we
    can drive the aggregator without Snowflake.
    """
    inst = EnhancedSnowflakeInsights.__new__(EnhancedSnowflakeInsights)
    inst.ctx = None
    inst.source_attribution = {}
    inst._skip_warned = set()
    inst._globally_unavailable_sections = set()
    inst._cached_table_columns = {}
    return inst


def test_aggregator_drops_section_with_column_missing_error() -> None:
    """A section that returns ``insights['error'] = 'column_missing: ...'``
    WITHOUT setting ``globally_unavailable_section`` must STILL be
    filtered out by the central aggregator AND registered in the
    instance set.
    """
    inst = _build_instance_with_mock_ctx()

    fake_account = {"sources": [], "account_summary": {}}
    fake_contract = {
        "error": (
            "column_missing: CX_DB.CX_SWSSBST_BR.COLLAB_ARR_CON_SKU "
            "missing required column(s) ['ACCOUNT_ID_C', 'ARR_AMOUNT']."
        ),
    }
    fake_engagement = {"sources": []}
    fake_support = {"sources": []}
    fake_usage = {"sources": []}
    fake_booking = {"sources": []}
    fake_risk = {"sources": []}
    fake_product = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=fake_account), \
         patch.object(inst, "_get_contract_insights", return_value=fake_contract), \
         patch.object(inst, "_get_engagement_insights", return_value=fake_engagement), \
         patch.object(inst, "_get_usage_insights", return_value=fake_usage), \
         patch.object(inst, "_get_support_insights", return_value=fake_support), \
         patch.object(inst, "_get_booking_insights", return_value=fake_booking), \
         patch.object(inst, "_get_risk_insights", return_value=fake_risk), \
         patch.object(inst, "_get_product_insights", return_value=fake_product), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        out = inst.get_comprehensive_customer_insights("Acme Corp", days=90)

    section_errors = out.get('section_errors', {})
    assert 'contract' not in section_errors, (
        f"contract leaked into section_errors despite being a column_missing global-config error: "
        f"{section_errors}"
    )
    assert 'contract' in inst._globally_unavailable_sections, (
        f"contract was not registered in _globally_unavailable_sections: "
        f"{inst._globally_unavailable_sections}"
    )


def test_aggregator_drops_section_with_table_policy_error() -> None:
    """Same as above but for the table-policy-violation error class."""
    inst = _build_instance_with_mock_ctx()

    fake_section = {
        "error": "Snowflake table not in allowlist policy: CX_DB.X.RISK_ASSESSMENT",
    }
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=other), \
         patch.object(inst, "_get_engagement_insights", return_value=other), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=other), \
         patch.object(inst, "_get_risk_insights", return_value=fake_section), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        out = inst.get_comprehensive_customer_insights("Acme Corp", days=90)

    section_errors = out.get('section_errors', {})
    assert 'risk' not in section_errors, (
        f"risk leaked into section_errors despite being a table-policy global-config error: "
        f"{section_errors}"
    )
    assert 'risk' in inst._globally_unavailable_sections


def test_aggregator_keeps_genuinely_transient_error() -> None:
    """A transient error MUST still flow through ``section_errors``
    so the per-customer report can flag the hiccup.
    """
    inst = _build_instance_with_mock_ctx()

    transient = {"error": "Snowflake query timeout (300s)"}
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=other), \
         patch.object(inst, "_get_engagement_insights", return_value=transient), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=other), \
         patch.object(inst, "_get_risk_insights", return_value=other), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        out = inst.get_comprehensive_customer_insights("Acme Corp", days=90)

    section_errors = out.get('section_errors', {})
    assert 'engagement' in section_errors, (
        f"transient engagement error was incorrectly suppressed: "
        f"section_errors={section_errors}"
    )
    assert 'engagement' not in inst._globally_unavailable_sections


def test_globally_unavailable_set_persists_across_customers() -> None:
    """The instance-level set MUST accumulate across multiple
    per-customer calls so the banner is emitted once even when only
    the first call encountered the error.
    """
    inst = _build_instance_with_mock_ctx()

    config_err = {"error": "column_missing: T missing required column(s) ['X']."}
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=config_err), \
         patch.object(inst, "_get_engagement_insights", return_value=other), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=other), \
         patch.object(inst, "_get_risk_insights", return_value=other), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        # First customer: triggers the error
        inst.get_comprehensive_customer_insights("Acme Corp", days=90)
        assert 'contract' in inst._globally_unavailable_sections

        # Second customer: same global-config issue should still be tracked
        inst.get_comprehensive_customer_insights("Beta Corp", days=90)
        assert 'contract' in inst._globally_unavailable_sections
        assert len(inst._globally_unavailable_sections) == 1, (
            f"expected exactly one globally_unavailable section, got "
            f"{inst._globally_unavailable_sections}"
        )


def test_get_globally_unavailable_sections_returns_sorted_list() -> None:
    inst = _build_instance_with_mock_ctx()
    inst._globally_unavailable_sections.update({"risk", "booking", "contract"})
    out = inst.get_globally_unavailable_sections()
    assert out == ["booking", "contract", "risk"], f"sort broken: {out}"


def test_no_banner_when_set_is_empty() -> None:
    """Clean Snowflake state: the accessor returns ``[]``."""
    inst = _build_instance_with_mock_ctx()
    assert inst.get_globally_unavailable_sections() == []
