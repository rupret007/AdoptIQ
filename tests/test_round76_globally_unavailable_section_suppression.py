"""Round 76 / R76-B — globally-unavailable Snowflake section suppression.

Build 49 acceptance audit (Phase 6 sweep) found 423 repetitions of
``"⚠️ Some Snowflake sub-sections were unavailable for this customer:
• booking: Some enhanced Snowflake insights are temporarily
unavailable; ..."`` across the leader DOCX.  The repetition stemmed
from `BOOKINGS_TABLE_FOR_ACCOUNT_CHECK` being missing the ``AMOUNT``
column (Snowflake error: ``invalid identifier 'AMOUNT'``) -- the
same global-config error fires for every customer in the run.

Pre-R76 the error already passed through the
``_get_booking_insights`` global-config branch (which logs ONCE at
INFO via ``self._skip_warned``) BUT the per-customer
``insights['error']`` was still set, which propagated to
``section_errors``, which propagated to the per-customer
``⚠️ ...`` warning paragraph in `leader_report_generator.py:7251`
(and again at line 6130).

R76-B fix:
  1. ``EnhancedSnowflakeInsights.__init__`` gains an instance-level
     ``_globally_unavailable_sections: set[str]`` accumulator.
  2. The ``_get_booking_insights`` and ``_get_risk_insights``
     global-config branches now also call ``self._globally_unavailable_sections.add(name)``
     and mark the per-customer payload with
     ``insights['globally_unavailable_section'] = True``.
  3. ``get_comprehensive_customer_insights`` ``section_errors``
     aggregator drops sections that are flagged globally
     unavailable; surfaces the cumulative list at top level via
     ``insights['globally_unavailable_sections']``.
  4. New public accessor ``get_globally_unavailable_sections()``
     returns the sorted list so writers can render a single banner
     without holding a reference to the per-customer payload.
  5. ``LeaderReportGenerator._add_validation_section`` emits ONE
     banner near the validation summary when the accessor returns
     non-empty.

This file pins:
  * the new accumulator + per-customer-payload markers,
  * the section_errors filter,
  * the public accessor,
  * the leader writer banner,
  * negative controls (clean Snowflake state, transient errors).

Round 76.  Made-with: Cursor.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from enhanced_snowflake_insights import EnhancedSnowflakeInsights


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSnowflakeError(Exception):
    """Mimic the Snowflake driver exception class shape."""


def _make_insights_instance() -> EnhancedSnowflakeInsights:
    """Construct an EnhancedSnowflakeInsights with a mock connection.

    The mock cursor is configured per test via direct attribute writes
    so each branch of ``_get_booking_insights`` / ``_get_risk_insights``
    can be exercised independently.
    """
    mock_ctx = MagicMock()
    mock_ctx.cursor.return_value.execute.return_value = None
    return EnhancedSnowflakeInsights(mock_ctx)


@contextmanager
def _bypass_table_policy_with_error(error: Exception):
    """Bypass the Round 7 / Phase 2.5 table-allowlist policy AND inject
    the global-config error directly into the underlying cursor.

    ``_PolicyEnforcingCursor`` raises ``TablePolicyViolation`` when the
    SQL references an off-allowlist table; the booking/risk tables ARE
    off-allowlist by default so the policy fires before the cursor
    sees the SQL.  This context manager:

    * Patches ``_enforce_policy_or_skip`` to always return True so the
      policy proxy delegates to the real cursor.
    * Returns a setup hook the test calls to attach the error as the
      cursor's ``execute.side_effect``.
    """
    with patch(
        "enhanced_snowflake_insights._enforce_policy_or_skip",
        return_value=True,
    ):
        yield error


def _minimal_validation_results() -> dict:
    """Minimum-shape ``validation_results`` for ``_add_validation_section``.

    Includes ``cross_checks.activity_counts.individual_totals`` so the
    Activity Counts Cross-Check table render doesn't ``KeyError``.
    """
    return {
        "summary": {
            "overall_status": "PASSED",
            "data_quality_score": 100,
            "critical_issues": [],
            "warnings": [],
            "recommendations": [],
        },
        "timestamp": "2026-05-04T00:00:00Z",
        "validation_checks": {
            "data_sources": {"snowflake_connection": True, "csone_data_loaded": True}
        },
        "cross_checks": {
            "activity_counts": {
                "individual_totals": {},
            },
        },
    }


# ---------------------------------------------------------------------------
# Init / accessor
# ---------------------------------------------------------------------------


def test_init_creates_empty_globally_unavailable_set() -> None:
    """Fresh instance has an empty globally-unavailable set."""
    insights = _make_insights_instance()
    assert insights._globally_unavailable_sections == set()
    assert insights.get_globally_unavailable_sections() == []


def test_accessor_returns_sorted_list() -> None:
    """``get_globally_unavailable_sections()`` returns a sorted list."""
    insights = _make_insights_instance()
    insights._globally_unavailable_sections.add("risk")
    insights._globally_unavailable_sections.add("booking")
    insights._globally_unavailable_sections.add("usage")

    out = insights.get_globally_unavailable_sections()
    assert out == ["booking", "risk", "usage"]
    # Defensive: the accessor returns a NEW list (not a live ref).
    out.append("MUTATION_TEST")
    assert "MUTATION_TEST" not in insights._globally_unavailable_sections


def test_accessor_is_idempotent_on_repeat_adds() -> None:
    """Adding the same section name twice doesn't duplicate."""
    insights = _make_insights_instance()
    insights._globally_unavailable_sections.add("booking")
    insights._globally_unavailable_sections.add("booking")
    insights._globally_unavailable_sections.add("booking")
    assert insights.get_globally_unavailable_sections() == ["booking"]


# ---------------------------------------------------------------------------
# Booking section: global-config branch registration
# ---------------------------------------------------------------------------


def test_booking_invalid_identifier_registers_globally_unavailable() -> None:
    """``invalid identifier 'AMOUNT'`` triggers global-config branch + registration."""
    insights = _make_insights_instance()

    error = _FakeSnowflakeError(
        "000904 (42000): SQL compilation error: error line 6 at position 16\n"
        "invalid identifier 'AMOUNT'"
    )
    insights.ctx.cursor.return_value.execute.side_effect = error

    with _bypass_table_policy_with_error(error):
        result = insights._get_booking_insights("Test Customer", days=90)

    # Section is registered as globally unavailable.
    assert "booking" in insights._globally_unavailable_sections, (
        f"Expected 'booking' in globally-unavailable set; got {insights._globally_unavailable_sections}"
    )
    # Per-customer payload carries the marker.
    assert result.get("globally_unavailable_section") is True
    # The user-facing error message stays the same (back-compat).
    assert "not configured" in result.get("error", "").lower()


def test_booking_does_not_exist_registers_globally_unavailable() -> None:
    """``does not exist or not authorized`` also triggers registration."""
    insights = _make_insights_instance()

    error = _FakeSnowflakeError(
        "002003 (42S02): Object 'CX_DB.CX_SWSSBST_BR.BOOKINGS_TABLE_FOR_ACCOUNT_CHECK' does not exist or not authorized."
    )
    insights.ctx.cursor.return_value.execute.side_effect = error

    with _bypass_table_policy_with_error(error):
        result = insights._get_booking_insights("Test Customer", days=90)

    assert "booking" in insights._globally_unavailable_sections
    assert result.get("globally_unavailable_section") is True


def test_booking_transient_error_NOT_registered_as_globally_unavailable() -> None:
    """Negative control: a transient (non-config) error does NOT register."""
    insights = _make_insights_instance()

    error = _FakeSnowflakeError(
        "250002 (08003): None: Connection is closed"
    )
    insights.ctx.cursor.return_value.execute.side_effect = error

    with _bypass_table_policy_with_error(error):
        result = insights._get_booking_insights("Test Customer", days=90)

    # Critical: NOT registered (this is a transient connection issue,
    # not a global-config issue).
    assert "booking" not in insights._globally_unavailable_sections
    # Per-customer payload does NOT carry the marker.
    assert result.get("globally_unavailable_section") is None
    # An error is still set, just not the globally-unavailable variant.
    assert "error" in result


# ---------------------------------------------------------------------------
# Risk section: same pattern
# ---------------------------------------------------------------------------


def test_risk_does_not_exist_registers_globally_unavailable() -> None:
    """``RISK_ASSESSMENT does not exist`` triggers registration."""
    insights = _make_insights_instance()

    error = _FakeSnowflakeError(
        "002003 (42S02): Object 'CX_DB.CX_SWSSBST_BR.RISK_ASSESSMENT' does not exist or not authorized."
    )
    insights.ctx.cursor.return_value.execute.side_effect = error

    with _bypass_table_policy_with_error(error):
        result = insights._get_risk_insights("Test Customer", days=90)

    assert "risk" in insights._globally_unavailable_sections
    assert result.get("globally_unavailable_section") is True


def test_risk_transient_error_NOT_registered_as_globally_unavailable() -> None:
    """Negative control for risk section."""
    insights = _make_insights_instance()

    error = _FakeSnowflakeError(
        "250002 (08003): None: Connection is closed"
    )
    insights.ctx.cursor.return_value.execute.side_effect = error

    with _bypass_table_policy_with_error(error):
        result = insights._get_risk_insights("Test Customer", days=90)

    assert "risk" not in insights._globally_unavailable_sections
    assert result.get("globally_unavailable_section") is None


# ---------------------------------------------------------------------------
# section_errors aggregation: filtering globally-unavailable sections
# ---------------------------------------------------------------------------


def test_section_errors_drops_globally_unavailable_section_marker() -> None:
    """The aggregator skips sections marked ``globally_unavailable_section``.

    Direct synthetic test of the loop body in
    ``get_comprehensive_customer_insights``.  We construct a payload
    with two error-bearing sections -- one marked globally unavailable
    and one transient -- and verify only the transient error makes
    it into ``section_errors``.
    """
    payload_insights = {
        "booking": {
            "error": "Booking insights not configured (table/column unavailable).",
            "globally_unavailable_section": True,
        },
        "engagement": {
            "error": "Booking insights unavailable: Connection is closed",
        },
    }
    section_errors = {}
    for section_name, section_payload in payload_insights.items():
        if isinstance(section_payload, dict):
            if section_payload.get("globally_unavailable_section"):
                continue
            err = section_payload.get("error")
            if err:
                section_errors[section_name] = str(err)

    assert "booking" not in section_errors
    assert "engagement" in section_errors


def test_section_errors_keeps_transient_errors_when_no_marker() -> None:
    """Without the marker, the transient error path stays unchanged."""
    payload_insights = {
        "booking": {
            "error": "Booking insights unavailable: SQL execution timeout",
        },
        "support": {
            "error": "Support insights unavailable: Connection is closed",
        },
    }
    section_errors = {}
    for section_name, section_payload in payload_insights.items():
        if isinstance(section_payload, dict):
            if section_payload.get("globally_unavailable_section"):
                continue
            err = section_payload.get("error")
            if err:
                section_errors[section_name] = str(err)

    # Both transient errors propagate (back-compat with pre-R76).
    assert section_errors.keys() == {"booking", "support"}


# ---------------------------------------------------------------------------
# Top-level surfacing: insights['globally_unavailable_sections']
# ---------------------------------------------------------------------------


def test_top_level_exposes_globally_unavailable_sections_when_set_nonempty() -> None:
    """A populated set surfaces on the per-customer payload."""
    insights_obj = _make_insights_instance()
    insights_obj._globally_unavailable_sections.add("booking")
    insights_obj._globally_unavailable_sections.add("risk")

    # Simulate the top-level surfacing block from
    # ``get_comprehensive_customer_insights``.
    out_insights = {"insights": {}, "source_attribution": {}}
    if insights_obj._globally_unavailable_sections:
        out_insights["globally_unavailable_sections"] = sorted(
            insights_obj._globally_unavailable_sections
        )

    assert out_insights["globally_unavailable_sections"] == ["booking", "risk"]


def test_top_level_omits_globally_unavailable_sections_when_set_empty() -> None:
    """Empty set --> the key is NOT added (clean Snowflake state)."""
    insights_obj = _make_insights_instance()
    # _globally_unavailable_sections is empty on init.

    out_insights = {"insights": {}, "source_attribution": {}}
    if insights_obj._globally_unavailable_sections:
        out_insights["globally_unavailable_sections"] = sorted(
            insights_obj._globally_unavailable_sections
        )

    assert "globally_unavailable_sections" not in out_insights


# ---------------------------------------------------------------------------
# Multi-customer call sequence: state persistence across calls
# ---------------------------------------------------------------------------


def test_globally_unavailable_persists_across_multiple_customer_calls() -> None:
    """The set persists across calls so customer 2..N see the cumulative list."""
    insights = _make_insights_instance()

    booking_error = _FakeSnowflakeError(
        "SQL compilation error: invalid identifier 'AMOUNT'"
    )
    insights.ctx.cursor.return_value.execute.side_effect = booking_error

    with _bypass_table_policy_with_error(booking_error):
        # Customer 1 -- first time we see the booking error.
        result1 = insights._get_booking_insights("Customer One", days=90)
        assert "booking" in insights._globally_unavailable_sections
        assert result1.get("globally_unavailable_section") is True

        # Customer 2 -- still sees the same global-config branch (instance state preserved).
        result2 = insights._get_booking_insights("Customer Two", days=90)
        assert insights._globally_unavailable_sections == {"booking"}  # not duplicated
        assert result2.get("globally_unavailable_section") is True

    # Accessor returns one entry, not two.
    assert insights.get_globally_unavailable_sections() == ["booking"]


# ---------------------------------------------------------------------------
# Leader writer banner: indirect verification via shape
# ---------------------------------------------------------------------------


def _make_leader_gen_for_validation_test() -> "object":
    """Build a minimal LeaderReportGenerator skeleton for validation-section tests.

    Bypasses the Snowflake-bearing ``__init__`` so we can test the
    ``_add_validation_section`` block in isolation with a mocked doc
    + enhanced_insights.
    """
    from leader_report_generator import LeaderReportGenerator

    with patch("leader_report_generator.LeaderReportGenerator.__init__", return_value=None):
        gen = LeaderReportGenerator.__new__(LeaderReportGenerator)
        gen.doc = MagicMock()
        # The cell shapes used by the activity-counts table -- we don't
        # exercise this path in our tests but the renderer reads them.
        gen.enhanced_insights = MagicMock()
        gen._apply_late_quality_penalties = lambda summary: summary
    return gen


def test_leader_writer_banner_renders_when_accessor_returns_nonempty() -> None:
    """Verify the new banner block in ``_add_validation_section`` fires.

    Patches the method's collaborators so we can assert the banner
    heading + paragraph were added when the accessor returns non-empty.
    """
    gen = _make_leader_gen_for_validation_test()
    gen.enhanced_insights.get_globally_unavailable_sections.return_value = ["booking", "risk"]

    gen._add_validation_section(_minimal_validation_results())

    # The banner heading uses the exact phrase 'Snowflake Enrichment
    # Sections Unavailable' so the audit harness can grep for it.
    add_heading_calls = [
        call.args for call in gen.doc.add_heading.call_args_list
    ]
    heading_texts = [args[0] for args in add_heading_calls if args]
    assert "Snowflake Enrichment Sections Unavailable" in heading_texts, (
        f"Expected R76-B banner heading; got headings: {heading_texts}"
    )


def test_leader_writer_banner_skips_when_accessor_returns_empty() -> None:
    """Negative control: clean Snowflake state -> no banner heading."""
    gen = _make_leader_gen_for_validation_test()
    gen.enhanced_insights.get_globally_unavailable_sections.return_value = []

    gen._add_validation_section(_minimal_validation_results())

    add_heading_calls = [
        call.args for call in gen.doc.add_heading.call_args_list
    ]
    heading_texts = [args[0] for args in add_heading_calls if args]
    assert "Snowflake Enrichment Sections Unavailable" not in heading_texts, (
        f"Banner should NOT render with empty set; got headings: {heading_texts}"
    )


def test_leader_writer_banner_resilient_to_accessor_exception() -> None:
    """Defensive: an exception from the accessor must not break the validator."""
    gen = _make_leader_gen_for_validation_test()
    gen.enhanced_insights.get_globally_unavailable_sections.side_effect = RuntimeError("boom")

    # Must not raise.
    gen._add_validation_section(_minimal_validation_results())

    # And the banner heading must NOT be in the output (graceful skip).
    add_heading_calls = [
        call.args for call in gen.doc.add_heading.call_args_list
    ]
    heading_texts = [args[0] for args in add_heading_calls if args]
    assert "Snowflake Enrichment Sections Unavailable" not in heading_texts


# ---------------------------------------------------------------------------
# Build 49 reproducer: 423-warning collapse acceptance
# ---------------------------------------------------------------------------


def test_53_customer_loop_with_global_booking_error_yields_zero_per_customer_warnings() -> None:
    """End-to-end-ish: 53 customer calls + booking missing column = ZERO per-customer ``booking`` errors.

    Pre-R76: each of the 53 customers would carry
    ``section_errors['booking'] = '...'`` so the leader writer
    rendered the per-customer ⚠️ paragraph 53 times for booking
    alone.  Post-R76: the marker drops booking from
    ``section_errors`` so the per-customer warning never fires for
    this section -- the operator sees the single banner instead.
    """
    insights_obj = _make_insights_instance()

    # Prime the global-config branch by making the booking query fail
    # with the exact error class observed in the Build 49 run.
    error = _FakeSnowflakeError(
        "000904 (42000): SQL compilation error: error line 6 at position 16\n"
        "invalid identifier 'AMOUNT'"
    )
    insights_obj.ctx.cursor.return_value.execute.side_effect = error

    per_customer_payloads = []
    with _bypass_table_policy_with_error(error):
        for i in range(53):
            result = insights_obj._get_booking_insights(f"Customer{i:03d}", days=90)
            per_customer_payloads.append(result)

    # Every per-customer payload carries the marker (so downstream
    # aggregator drops them all consistently).
    assert all(
        r.get("globally_unavailable_section") is True
        for r in per_customer_payloads
    ), "Not every per-customer payload carries the globally-unavailable marker"

    # Single registration in the instance-level set (despite 53 calls).
    assert insights_obj._globally_unavailable_sections == {"booking"}
    assert insights_obj.get_globally_unavailable_sections() == ["booking"]

    # Simulate the section_errors aggregator on each payload -- every
    # call should yield an EMPTY section_errors dict for the booking
    # section because of the marker.
    for r in per_customer_payloads:
        section_errors = {}
        if r.get("globally_unavailable_section"):
            continue  # filtered out
        err = r.get("error")
        if err:
            section_errors["booking"] = err
        assert "booking" not in section_errors, (
            "Booking error leaked through aggregator despite globally_unavailable marker"
        )
