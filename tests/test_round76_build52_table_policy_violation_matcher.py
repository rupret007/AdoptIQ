"""Round 76 / Build 52 tests -- matcher recognises ``_PolicyEnforcingCursor``-shape errors.

Build 51 acceptance audit found 317 ``unavailable`` tokens in the leader
DOCX -- down from the Build 49 floor of 423 (a 25% drop, confirming the
R76-B central filter was working) but still above the <50 acceptance
threshold.  Cluster analysis showed 105 customer warning blocks each
listing BOTH ``booking`` and ``risk`` as failed sections.

Root cause: the booking/risk insights ARE failing globally (both tables
are off the ``snowflake_table_policy.ALLOWED_TABLES`` allow-list in this
deployment) but the error string flowing out of
``_PolicyEnforcingCursor.execute`` is::

    ``"Round 7 / Phase 2.5: enhanced_snowflake_insights refused by table policy"``

NOT the upstream ``snowflake_table_policy.guard_sql`` shape::

    ``"Snowflake table not in allowlist policy: <table>"``

The Build 51 ``_is_globally_unavailable_error`` matcher only knew the
upstream shape (``"not in allowlist policy"``) so the wrapped
cursor-shape error fell through to the generic ``section_errors`` path
and reproduced the per-customer warning 105+105=210 times.

Build 52 extends the matcher to recognise BOTH shapes raised by
``snowflake_table_policy.guard_sql`` (``"blocked by policy"`` for
explicitly-blocked tables AND ``"not in allowlist policy"`` for unknown
tables) AND the ``_PolicyEnforcingCursor`` wrapped shape
(``"refused by table policy"``).  This file pins the matcher AND a
defense-in-depth integration test that drives the central aggregator
through a fake ``_get_booking_insights`` return value carrying the
exact wrapped error string.
"""
from __future__ import annotations

from unittest.mock import patch

from enhanced_snowflake_insights import EnhancedSnowflakeInsights


# ---------------------------------------------------------------- Matcher tests

def test_matcher_recognises_policy_enforcing_cursor_wrapped_error() -> None:
    """The exact error string captured from the live Build 51 stderr.

    ``_PolicyEnforcingCursor.execute`` raises::

        TablePolicyViolation(f"Round 7 / Phase 2.5: {self._context} refused by table policy")

    where ``self._context`` is ``"enhanced_snowflake_insights"``.
    """
    err = "Round 7 / Phase 2.5: enhanced_snowflake_insights refused by table policy"
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(err)


def test_matcher_recognises_policy_enforcing_cursor_short_form() -> None:
    """Defense in depth: any error that contains the substring
    ``"refused by table policy"`` is a global-config error regardless
    of the surrounding context tag.
    """
    err = "wrapper refused by table policy: foo"
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(err)


def test_matcher_recognises_guard_sql_blocked_form() -> None:
    """``snowflake_table_policy.guard_sql`` raises the second of its two
    shapes (``"blocked by policy"``) when the SQL references an
    explicitly-blocked table such as legacy ``SUPPORT_CASES``.
    """
    err = "Snowflake table blocked by policy: CX_DB.CX_SWSSBST_BR.SUPPORT_CASES"
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(err)


def test_matcher_case_insensitive_for_new_patterns() -> None:
    """Case folding must work for the new patterns too -- a future
    log refactoring that capitalises the message must not silently
    reintroduce the per-customer noise."""
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "REFUSED BY TABLE POLICY"
    )
    assert EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Snowflake Table BLOCKED BY POLICY: foo"
    )


def test_matcher_still_rejects_transient_table_errors() -> None:
    """Negative control: a transient error that happens to mention
    'table' or 'policy' but is NOT a global-config failure must NOT
    be misclassified.
    """
    assert not EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Snowflake table lock timeout (300s)"
    )
    assert not EnhancedSnowflakeInsights._is_globally_unavailable_error(
        "Connection reset by peer"
    )


def test_matcher_still_recognises_build51_patterns_intact() -> None:
    """Build 51 patterns must continue to match -- this test pins the
    backward compatibility contract so future expansions can't silently
    drop one of the existing recognized strings.
    """
    for pattern in [
        "Object 'X' does not exist or not authorized.",
        "User not authorized to read table",
        "SQL compilation error: invalid identifier 'AMOUNT'",
        "column_missing: T missing required column(s) ['X'].",
        "missing required column ARR_AMOUNT",
        "Snowflake table not in allowlist policy: CX_DB.X.Y",
    ]:
        assert EnhancedSnowflakeInsights._is_globally_unavailable_error(pattern), (
            f"Build 51 pattern regressed: {pattern!r}"
        )


# ------------------------------------------------------ Aggregator integration

def _build_instance_with_mock_ctx() -> EnhancedSnowflakeInsights:
    """Construct an EnhancedSnowflakeInsights with a mock ctx so we
    can drive the aggregator without Snowflake (mirrors the helper
    in test_round76_build51_global_unavailable_central_filter.py).
    """
    inst = EnhancedSnowflakeInsights.__new__(EnhancedSnowflakeInsights)
    inst.ctx = None
    inst.source_attribution = {}
    inst._skip_warned = set()
    inst._globally_unavailable_sections = set()
    inst._cached_table_columns = {}
    return inst


def test_aggregator_drops_booking_with_policy_enforcing_cursor_wrapped_error() -> None:
    """Defense-in-depth: a section that returns the EXACT live Build 51
    error string MUST be filtered out by the central aggregator AND
    routed into the globally-unavailable set so the banner fires once
    instead of 105 times.
    """
    inst = _build_instance_with_mock_ctx()

    # Replicates the Build 51 live-acceptance shape:
    # ``insights['error'] = f'Booking insights unavailable: {err_str}'``
    # where ``err_str`` is ``"Round 7 / Phase 2.5: enhanced_snowflake_insights refused by table policy"``.
    booking_err = {
        "error": (
            "Booking insights unavailable: "
            "Round 7 / Phase 2.5: enhanced_snowflake_insights refused by table policy"
        ),
    }
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=other), \
         patch.object(inst, "_get_engagement_insights", return_value=other), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=booking_err), \
         patch.object(inst, "_get_risk_insights", return_value=other), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        out = inst.get_comprehensive_customer_insights("Acme Corp", days=90)

    section_errors = out.get('section_errors', {})
    assert 'booking' not in section_errors, (
        "booking leaked into section_errors despite carrying the "
        "_PolicyEnforcingCursor 'refused by table policy' shape: "
        f"{section_errors}"
    )
    assert 'booking' in inst._globally_unavailable_sections


def test_aggregator_drops_risk_with_policy_enforcing_cursor_wrapped_error() -> None:
    """Same as above for the risk section -- the live Build 51 audit
    showed BOTH booking AND risk failing the same way, 105 times each.
    """
    inst = _build_instance_with_mock_ctx()

    risk_err = {
        "error": (
            "Risk insights unavailable: "
            "Round 7 / Phase 2.5: enhanced_snowflake_insights refused by table policy"
        ),
    }
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=other), \
         patch.object(inst, "_get_engagement_insights", return_value=other), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=other), \
         patch.object(inst, "_get_risk_insights", return_value=risk_err), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        out = inst.get_comprehensive_customer_insights("Acme Corp", days=90)

    section_errors = out.get('section_errors', {})
    assert 'risk' not in section_errors
    assert 'risk' in inst._globally_unavailable_sections


def test_aggregator_drops_both_booking_and_risk_in_one_pass() -> None:
    """The Build 51 live-audit reproducer: BOTH booking AND risk are
    off-allowlist in this deployment, so a single per-customer call
    must collapse BOTH into the globally-unavailable set and emit
    EXACTLY ZERO entries in section_errors.
    """
    inst = _build_instance_with_mock_ctx()

    booking_err = {
        "error": (
            "Booking insights unavailable: Round 7 / Phase 2.5: "
            "enhanced_snowflake_insights refused by table policy"
        ),
    }
    risk_err = {
        "error": (
            "Risk insights unavailable: Round 7 / Phase 2.5: "
            "enhanced_snowflake_insights refused by table policy"
        ),
    }
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=other), \
         patch.object(inst, "_get_engagement_insights", return_value=other), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=booking_err), \
         patch.object(inst, "_get_risk_insights", return_value=risk_err), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        out = inst.get_comprehensive_customer_insights("Acme Corp", days=90)

    section_errors = out.get('section_errors', {})
    assert section_errors == {}, (
        "BOTH booking AND risk should be filtered to the globally-unavailable "
        f"set, leaving section_errors empty.  Got: {section_errors}"
    )
    assert inst._globally_unavailable_sections == {'booking', 'risk'}


def test_aggregator_53_customer_reproducer_collapses_to_single_banner() -> None:
    """Replicates the 53-customer Build 51 live audit run: each call
    sees the same global-config errors; the set never grows beyond
    {'booking', 'risk'} and per-call section_errors stays empty so
    the per-customer warning paragraph never renders.
    """
    inst = _build_instance_with_mock_ctx()

    booking_err = {
        "error": (
            "Booking insights unavailable: Round 7 / Phase 2.5: "
            "enhanced_snowflake_insights refused by table policy"
        ),
    }
    risk_err = {
        "error": (
            "Risk insights unavailable: Round 7 / Phase 2.5: "
            "enhanced_snowflake_insights refused by table policy"
        ),
    }
    other = {"sources": []}

    with patch.object(inst, "_get_account_insights", return_value=other), \
         patch.object(inst, "_get_contract_insights", return_value=other), \
         patch.object(inst, "_get_engagement_insights", return_value=other), \
         patch.object(inst, "_get_usage_insights", return_value=other), \
         patch.object(inst, "_get_support_insights", return_value=other), \
         patch.object(inst, "_get_booking_insights", return_value=booking_err), \
         patch.object(inst, "_get_risk_insights", return_value=risk_err), \
         patch.object(inst, "_get_product_insights", return_value=other), \
         patch.object(inst, "_compile_source_attribution", return_value={}):
        for n in range(53):
            out = inst.get_comprehensive_customer_insights(f"Customer{n}", days=90)
            assert out.get('section_errors', {}) == {}, (
                f"per-customer section_errors leaked at iteration {n}: "
                f"{out.get('section_errors')}"
            )

    # Set must contain ONLY booking and risk, not all 53 distinct
    # customer-section pairs.
    assert inst._globally_unavailable_sections == {'booking', 'risk'}
