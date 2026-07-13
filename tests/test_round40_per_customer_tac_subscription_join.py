"""Round 40 / Phase 1 -- per-customer TAC drilldown uses SUBSCRIPTION_ID join.

Pre-Round-40 ``LeaderReportGenerator._add_customer_summary_with_sources``
filtered ``data['tac_cases']`` by exact-normalized customer name against
the CSOne ``Customer Name: Customer Name`` column, but the team roster
(Snowflake ``BU_NAME``) and CSOne consistently disagree on suffixes --
``FARMERS INSURANCE GROUP US`` (roster) vs ``FARMERS INSURANCE GROUP``
(TAC), ``WINTRUST FINANCIAL CORPORATION US`` vs ``WINTRUST FINANCIAL``,
``NATIONAL GRID PLC US`` vs ``NATIONAL GRID``, etc.  Verified
production-incident fallout in ``Brian_Frazier_90d_1777421123``: 46 of
61 per-customer summary tables collapsed to ``TAC Cases: 0`` even
though the CSSM-level total (correctly attributed by Phase B's
``add_tac_cases_from_csone``) was nonzero.

Round 40 / Phase 1 promotes the per-customer drilldown to the same
three-tier authoritative join Phase B uses at the team level:

  1. SUBSCRIPTION_ID -- look up this customer's subscriptions from
                       ``data['subscriptions']`` then filter
                       ``data['tac_cases']`` by SUBSCRIPTION_ID.
  2. ACCOUNT_ID_C    -- account-level fallback.
  3. exact normalized customer name -- preserved third-tier so
                       customers whose names DO match (e.g.
                       ``ZURICH NORTH AMERICA``) still resolve.

The tests pin:

  * ``test_per_customer_tac_resolves_via_subscription_id_when_names_drift``
    -- the named FARMERS INSURANCE GROUP collision.
  * ``test_per_customer_tac_does_not_leak_unrelated_subscriptions``
    -- a sibling subscription on the same team must not bleed in.
  * ``test_per_customer_tac_account_id_fallback_when_subscription_blank``
    -- Priority 2 covers rows missing SUBSCRIPTION_ID.
  * ``test_per_customer_tac_name_fallback_for_matching_customers``
    -- Priority 3 still works for customers whose names match.
  * ``test_per_customer_tac_handles_csone_float_id_strings``
    -- mirrors Phase B's ``.0`` suffix strip (line ~1688).
  * ``test_per_customer_summary_with_sources_uses_three_tier_join_in_source``
    -- source-level pin for the Round 40 / Phase 1 marker so a future
       refactor cannot silently revert to the customer-name-only filter.
"""
from __future__ import annotations

import inspect
from unittest import mock

import pandas as pd
import pytest


@pytest.fixture
def generator():
    from leader_report_generator import LeaderReportGenerator

    return LeaderReportGenerator(
        mock.MagicMock(),
        [("Brian Frazier", "Jeffrey Story", "jeffrey@example.com")],
    )


def _customer_data(*, subscriptions: pd.DataFrame, tac_cases: pd.DataFrame) -> dict:
    """Build a per-CSSM ``data`` dict in the shape
    ``_add_customer_summary_with_sources`` expects."""
    return {
        "subscriptions": subscriptions,
        "tac_cases": tac_cases,
        "action_plans": pd.DataFrame(),
        "adoption_barriers": pd.DataFrame(),
        "customer_pulse": pd.DataFrame(),
        "customers": [],
    }


def _account_summary_tac_value(generator) -> int:
    """Walk ``generator.doc`` for the most recently rendered Account
    Summary table and return the integer value in the 'TAC Cases' row."""
    for table in reversed(generator.doc.tables):
        rows = list(table.rows)
        if not rows:
            continue
        # Account Summary tables have headers ['Metric', 'Value'].
        header_cells = [c.text.strip() for c in rows[0].cells]
        if header_cells[:2] != ["Metric", "Value"]:
            continue
        for row in rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if cells[0] == "TAC Cases":
                return int(cells[1])
        return 0
    raise AssertionError(
        "No Account Summary table found in rendered doc; "
        "_add_customer_summary_with_sources may have failed early."
    )


def _account_summary_metric(generator, metric_label: str) -> int:
    for table in reversed(generator.doc.tables):
        rows = list(table.rows)
        if not rows:
            continue
        header_cells = [c.text.strip() for c in rows[0].cells]
        if header_cells[:2] != ["Metric", "Value"]:
            continue
        for row in rows[1:]:
            cells = [c.text.strip() for c in row.cells]
            if cells[0] == metric_label:
                return int(cells[1])
        return 0
    raise AssertionError(
        "No Account Summary table found in rendered doc."
    )


# ---------------------------------------------------------------------------
# Test 1 -- the production trigger: FARMERS INSURANCE GROUP US (roster)
# vs FARMERS INSURANCE GROUP (TAC) suffix drift.
# ---------------------------------------------------------------------------


def test_per_customer_tac_resolves_via_subscription_id_when_names_drift(generator):
    """Roster spells the customer ``FARMERS INSURANCE GROUP US`` but the
    CSOne TAC sheet spells it ``FARMERS INSURANCE GROUP`` (no ``US``).
    Pre-Round-40 the per-customer "TAC Cases" cell collapsed to 0
    because the exact-normalized name compare returned no rows.
    Post-Round-40 the SUBSCRIPTION_ID tier resolves the join."""
    customer = "FARMERS INSURANCE GROUP US"
    subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "Sub1153461", "ACCOUNT_ID_C": "ACC-FARM",
         "BU_NAME": "FARMERS INSURANCE GROUP US"},
    ])
    tac = pd.DataFrame([
        # Two rows for FARMERS via SUBSCRIPTION_ID; CSOne uses the
        # short name (no "US" suffix).
        {"Customer Name: Customer Name": "FARMERS INSURANCE GROUP",
         "SUBSCRIPTION_ID": "Sub1153461", "Case #": "700001",
         "Title": "Voice issue A", "Status": "Open", "Severity": "3"},
        {"Customer Name: Customer Name": "FARMERS INSURANCE GROUP",
         "SUBSCRIPTION_ID": "Sub1153461", "Case #": "700002",
         "Title": "Voice issue B", "Status": "Closed", "Severity": "4"},
    ])
    generator._add_customer_summary_with_sources(customer, _customer_data(
        subscriptions=subs, tac_cases=tac,
    ))
    assert _account_summary_tac_value(generator) == 2, (
        "Round 40 / Phase 1: per-customer TAC cell must resolve via "
        "SUBSCRIPTION_ID when CSOne and Snowflake disagree on the "
        "customer-name suffix.  Got 0 -- the suffix-drift bug regressed."
    )
    # Total Activities = AP+AB+CP+TAC = 0+0+0+2.
    assert _account_summary_metric(generator, "Total Activities") == 2


# ---------------------------------------------------------------------------
# Test 2 -- a sibling subscription on the same team must not leak in.
# ---------------------------------------------------------------------------


def test_per_customer_tac_does_not_leak_unrelated_subscriptions(generator):
    customer = "FARMERS INSURANCE GROUP US"
    subs = pd.DataFrame([
        # FARMERS owns one subscription...
        {"SUBSCRIPTION_ID": "Sub1153461", "ACCOUNT_ID_C": "ACC-FARM",
         "BU_NAME": "FARMERS INSURANCE GROUP US"},
        # ...and the same CSSM owns a second subscription for an
        # unrelated customer.  The second sub must NOT contribute to
        # FARMERS' per-customer TAC drilldown.
        {"SUBSCRIPTION_ID": "Sub9999", "ACCOUNT_ID_C": "ACC-OTHER",
         "BU_NAME": "OTHER CUSTOMER INC"},
    ])
    tac = pd.DataFrame([
        {"Customer Name: Customer Name": "FARMERS INSURANCE GROUP",
         "SUBSCRIPTION_ID": "Sub1153461", "Case #": "700001",
         "Title": "Real FARMERS case", "Status": "Open", "Severity": "3"},
        {"Customer Name: Customer Name": "OTHER CUSTOMER INC",
         "SUBSCRIPTION_ID": "Sub9999", "Case #": "700009",
         "Title": "Sibling sub case", "Status": "Open", "Severity": "3"},
    ])
    generator._add_customer_summary_with_sources(customer, _customer_data(
        subscriptions=subs, tac_cases=tac,
    ))
    assert _account_summary_tac_value(generator) == 1, (
        "Round 40 / Phase 1: per-customer TAC cell must include ONLY "
        "subscriptions whose BU_NAME normalizes to the requested "
        "customer; sibling subs on the same CSSM must not leak in."
    )


# ---------------------------------------------------------------------------
# Test 3 -- ACCOUNT_ID_C fallback when SUBSCRIPTION_ID is blank/missing.
# ---------------------------------------------------------------------------


def test_per_customer_tac_account_id_fallback_when_subscription_blank(generator):
    customer = "FARMERS INSURANCE GROUP US"
    subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "Sub1153461", "ACCOUNT_ID_C": "ACC-FARM",
         "BU_NAME": "FARMERS INSURANCE GROUP US"},
    ])
    tac = pd.DataFrame([
        # Row missing SUBSCRIPTION_ID but has ACCOUNT_ID_C.
        {"Customer Name: Customer Name": "FARMERS INSURANCE GROUP",
         "SUBSCRIPTION_ID": None, "ACCOUNT_ID_C": "ACC-FARM",
         "Case #": "700003", "Title": "No sub id", "Status": "Open",
         "Severity": "3"},
    ])
    generator._add_customer_summary_with_sources(customer, _customer_data(
        subscriptions=subs, tac_cases=tac,
    ))
    assert _account_summary_tac_value(generator) == 1, (
        "Round 40 / Phase 1: ACCOUNT_ID_C tier 2 must catch rows that "
        "lack SUBSCRIPTION_ID (mirrors Phase B's team-level fallback)."
    )


# ---------------------------------------------------------------------------
# Test 4 -- name fallback (tier 3) still works for matching customers.
# ---------------------------------------------------------------------------


def test_per_customer_tac_name_fallback_for_matching_customers(generator):
    """Some customers (e.g. ZURICH NORTH AMERICA) have IDENTICAL names
    in CSOne and Snowflake; the third-tier exact-name fallback must
    still attribute their cases when subscriptions/account IDs are
    missing entirely from the TAC sheet."""
    customer = "ZURICH NORTH AMERICA"
    subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "Sub2222", "ACCOUNT_ID_C": "ACC-ZNA",
         "BU_NAME": "ZURICH NORTH AMERICA"},
    ])
    tac = pd.DataFrame([
        # No SUBSCRIPTION_ID, no ACCOUNT_ID_C, but the customer name
        # matches exactly.
        {"Customer Name: Customer Name": "ZURICH NORTH AMERICA",
         "Case #": "700004", "Title": "Name-match only", "Status": "Open",
         "Severity": "3"},
    ])
    generator._add_customer_summary_with_sources(customer, _customer_data(
        subscriptions=subs, tac_cases=tac,
    ))
    assert _account_summary_tac_value(generator) == 1, (
        "Round 40 / Phase 1: tier-3 exact-name fallback must remain in "
        "place for customers whose names match across sources."
    )


# ---------------------------------------------------------------------------
# Test 5 -- ".0" float-id suffix strip (mirrors Phase B line ~1688).
# ---------------------------------------------------------------------------


def test_per_customer_tac_handles_csone_float_id_strings(generator):
    """CSOne sometimes exports IDs as floats (``Sub1153461.0``).  Phase B
    strips the trailing ``.0`` before comparing to the integer-string
    form on the Snowflake side; the per-customer join must do the same."""
    customer = "FARMERS INSURANCE GROUP US"
    subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "Sub1153461", "ACCOUNT_ID_C": "ACC-FARM",
         "BU_NAME": "FARMERS INSURANCE GROUP US"},
    ])
    tac = pd.DataFrame([
        {"Customer Name: Customer Name": "FARMERS INSURANCE GROUP",
         # Note the trailing ``.0`` -- pre-strip this would not match.
         "SUBSCRIPTION_ID": "Sub1153461.0",
         "Case #": "700005", "Title": "Float id", "Status": "Open",
         "Severity": "3"},
    ])
    generator._add_customer_summary_with_sources(customer, _customer_data(
        subscriptions=subs, tac_cases=tac,
    ))
    assert _account_summary_tac_value(generator) == 1, (
        "Round 40 / Phase 1: trailing '.0' on CSOne id strings must be "
        "stripped before SUBSCRIPTION_ID comparison."
    )


# ---------------------------------------------------------------------------
# Test 6 -- source-level pin so a future refactor cannot revert.
# ---------------------------------------------------------------------------


def test_per_customer_summary_with_sources_uses_three_tier_join_in_source():
    """Source-level pin: the Round 40 / Phase 1 marker and the three
    join-key column references must be present in
    ``_add_customer_summary_with_sources``.  This guards against a
    future refactor silently reverting to a customer-name-only filter."""
    import leader_report_generator as lrg

    src = inspect.getsource(
        lrg.LeaderReportGenerator._add_customer_summary_with_sources
    )
    assert "Round 40 / Phase 1" in src, (
        "Round 40 / Phase 1 source marker is missing from "
        "_add_customer_summary_with_sources; if the marker was removed "
        "or renamed, update this test, but do NOT silently delete the "
        "three-tier authoritative TAC join."
    )
    assert "SUBSCRIPTION_ID" in src, (
        "Per-customer TAC join must reference SUBSCRIPTION_ID -- the "
        "exact-name-only filter is the regression Round 40 fixed."
    )
    assert "ACCOUNT_ID_C" in src, (
        "Per-customer TAC join must reference ACCOUNT_ID_C as the "
        "second-priority fallback (mirrors Phase B's team-level join)."
    )
