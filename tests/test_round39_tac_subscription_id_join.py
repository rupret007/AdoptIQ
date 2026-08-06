"""Round 39 / Phase 1.1 — TAC SUBSCRIPTION_ID join, no fuzzy name match.

Pre-Round-39 ``add_tac_cases_from_csone`` used a 2-word-overlap fuzzy
matcher (``matches_customer`` closure) that returned True whenever a
CSSM's customer name and a CSOne TAC row's customer name shared 2 words
(or 1 word if either side was short). Verified production-incident
fallout from the ``Brian_Frazier_90d_1777414305`` audit:

  * **ERIE INSURANCE GROUP** (CSSM A) word-overlapped with **FARMERS
    INSURANCE GROUP** (CSSM B) on ``{INSURANCE, GROUP}`` → all 31 TAC
    rows on FARMERS got attributed to BOTH CSSMs.
  * Same pattern for WINTRUST FINANCIAL — 16 cases double-counted.
  * Effect: one CSSM's reported "63 TAC cases" was actually ~12; the
    team total was over-reported by ~51; coaching priorities were
    inverted.

Round 39 / Phase 1.1 replaces the heuristic with a three-tier
authoritative join:

  1. SUBSCRIPTION_ID → CSSM (from each CSSM's Subscription DSM rows)
  2. ACCOUNT_ID_C    → CSSM (account-level fallback)
  3. exact normalized customer name (case-folded, NFKC, suffix-collapsed)

If a TAC row matches none of the three, it is logged as a
``partial_data_warning`` (kind=tac_unmatched_after_subscription_join)
rather than being silently dropped or fuzzy-attributed.

This file pins the fix with the exact production scenario plus three
regression scenarios that the pre-fix code would have failed:

  * ``test_erie_farmers_no_cross_attribution`` — the named INSURANCE
    GROUP collision; assert each TAC row lands on exactly ONE CSSM.
  * ``test_subscription_id_takes_priority_over_name`` — when
    SUBSCRIPTION_ID and customer-name disagree, SUBSCRIPTION_ID wins.
  * ``test_account_id_fallback_when_subscription_missing`` — Priority 2.
  * ``test_unmatched_rows_recorded_in_summary`` — the summary dict
    surfaces unmatched rows so the wrapper can fold them into
    partial_data_warnings.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source

import pandas as pd
import pytest
from unittest import mock


@pytest.fixture
def generator():
    from leader_report_generator import LeaderReportGenerator
    return LeaderReportGenerator(mock.MagicMock(), [
        ("Brian Frazier", "Angelica Rivera", "angelica@example.com"),
        ("Brian Frazier", "Jeffrey Smith", "jeffrey@example.com"),
    ])


def _team_data(*, angelica_subs=None, jeffrey_subs=None,
               angelica_customers=None, jeffrey_customers=None):
    return {
        "Angelica Rivera": {
            "subscriptions": angelica_subs if angelica_subs is not None else pd.DataFrame(),
            "customers": list(angelica_customers or []),
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
        },
        "Jeffrey Smith": {
            "subscriptions": jeffrey_subs if jeffrey_subs is not None else pd.DataFrame(),
            "customers": list(jeffrey_customers or []),
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
            "tac_cases": pd.DataFrame(),
        },
    }


def _csone_row(*, sub_id=None, account_id=None, customer="Unknown",
               opened_days_ago=10):
    return {
        "Subscription Reference Id": sub_id,
        "ACCOUNT_ID_C": account_id,
        "Customer Name": customer,
        "Date/Time Opened": (
            pd.Timestamp.utcnow() - pd.Timedelta(days=opened_days_ago)
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Test 1 — the production trigger: ERIE / FARMERS INSURANCE GROUP collision.
# ---------------------------------------------------------------------------


def test_erie_farmers_no_cross_attribution(generator):
    """ERIE INSURANCE GROUP (Angelica) and FARMERS INSURANCE GROUP
    (Jeffrey) word-overlap on {INSURANCE, GROUP}.  Pre-Round-39 the
    fuzzy matcher attributed every FARMERS TAC row to BOTH CSSMs.
    Post-Round-39 each row lands on exactly one CSSM via SUBSCRIPTION_ID."""
    angelica_subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-ERIE-1", "ACCOUNT_ID_C": "ACC-ERIE", "BU_NAME": "ERIE INSURANCE GROUP"},
    ])
    jeffrey_subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-FARM-1", "ACCOUNT_ID_C": "ACC-FARM", "BU_NAME": "FARMERS INSURANCE GROUP"},
    ])
    team = _team_data(
        angelica_subs=angelica_subs,
        jeffrey_subs=jeffrey_subs,
        angelica_customers=["ERIE INSURANCE GROUP"],
        jeffrey_customers=["FARMERS INSURANCE GROUP"],
    )

    farmers_rows = [
        _csone_row(sub_id="SUB-FARM-1", customer="FARMERS INSURANCE GROUP", opened_days_ago=i)
        for i in range(1, 32)
    ]
    csone_df = pd.DataFrame(farmers_rows)
    generator.add_tac_cases_from_csone(team, csone_df, days=90)

    angelica_n = len(team["Angelica Rivera"]["tac_cases"])
    jeffrey_n = len(team["Jeffrey Smith"]["tac_cases"])
    assert angelica_n == 0, (
        f"Angelica must NOT receive FARMERS TAC rows via word overlap; "
        f"got {angelica_n} -- the Round 39 fuzzy-match removal regressed."
    )
    assert jeffrey_n == 31, (
        f"Jeffrey must receive ALL 31 FARMERS rows via SUBSCRIPTION_ID; "
        f"got {jeffrey_n}."
    )
    summary = getattr(generator, "_tac_match_summary", {})
    assert summary.get("matched_by_subscription", 0) == 31
    assert summary.get("unmatched", 0) == 0


# ---------------------------------------------------------------------------
# Test 2 — SUBSCRIPTION_ID wins over customer-name when they disagree.
# ---------------------------------------------------------------------------


def test_subscription_id_takes_priority_over_name(generator):
    """When a CSOne row's SUBSCRIPTION_ID belongs to CSSM A but the
    customer-name string would resolve to CSSM B, SUBSCRIPTION_ID wins
    (it is the authoritative join key)."""
    angelica_subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-A", "ACCOUNT_ID_C": "ACC-A", "BU_NAME": "Acme Corp"},
    ])
    jeffrey_subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-B", "ACCOUNT_ID_C": "ACC-B", "BU_NAME": "Beta LLC"},
    ])
    team = _team_data(
        angelica_subs=angelica_subs,
        jeffrey_subs=jeffrey_subs,
        angelica_customers=["Acme Corp"],
        jeffrey_customers=["Beta LLC"],
    )
    csone_df = pd.DataFrame([
        _csone_row(sub_id="SUB-A", customer="Beta LLC", opened_days_ago=5),
    ])
    generator.add_tac_cases_from_csone(team, csone_df, days=90)
    assert len(team["Angelica Rivera"]["tac_cases"]) == 1
    assert len(team["Jeffrey Smith"]["tac_cases"]) == 0


# ---------------------------------------------------------------------------
# Test 3 — ACCOUNT_ID_C fallback when SUBSCRIPTION_ID is blank.
# ---------------------------------------------------------------------------


def test_account_id_fallback_when_subscription_missing(generator):
    angelica_subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-A", "ACCOUNT_ID_C": "ACC-A", "BU_NAME": "Acme Corp"},
    ])
    team = _team_data(
        angelica_subs=angelica_subs,
        angelica_customers=["Acme Corp"],
    )
    csone_df = pd.DataFrame([
        _csone_row(sub_id=None, account_id="ACC-A", customer="Acme Corp", opened_days_ago=10),
    ])
    generator.add_tac_cases_from_csone(team, csone_df, days=90)
    assert len(team["Angelica Rivera"]["tac_cases"]) == 1
    summary = getattr(generator, "_tac_match_summary", {})
    assert summary.get("matched_by_account", 0) == 1


# ---------------------------------------------------------------------------
# Test 4 — unmatched rows recorded for partial_data_warnings handoff.
# ---------------------------------------------------------------------------


def test_unmatched_rows_recorded_in_summary(generator):
    angelica_subs = pd.DataFrame([
        {"SUBSCRIPTION_ID": "SUB-A", "ACCOUNT_ID_C": "ACC-A", "BU_NAME": "Acme Corp"},
    ])
    team = _team_data(angelica_subs=angelica_subs, angelica_customers=["Acme Corp"])
    csone_df = pd.DataFrame([
        # Genuinely unmatchable: no sub id, no account id, name not in team.
        _csone_row(sub_id="UNKNOWN-SUB", account_id="UNKNOWN-ACC",
                   customer="UNRELATED CUSTOMER", opened_days_ago=10),
    ])
    generator.add_tac_cases_from_csone(team, csone_df, days=90)
    summary = getattr(generator, "_tac_match_summary", {})
    assert summary.get("unmatched", 0) == 1
    assert len(team["Angelica Rivera"]["tac_cases"]) == 0


# ---------------------------------------------------------------------------
# Test 5 — fuzzy 2-word-overlap heuristic is GONE from the source.
# ---------------------------------------------------------------------------


def test_fuzzy_two_word_overlap_matcher_removed():
    """Pre-Round-39 the matching logic relied on a closure named
    ``matches_customer`` that hand-rolled a 2-word-overlap test.  Round
    39 / Phase 1.1 deleted that closure entirely.  Pin its absence so
    a future refactor cannot reintroduce it without explicit acknowledgment."""
    import inspect
    import leader_report_generator as lrg
    src = inspect.getsource(lrg.LeaderReportGenerator.add_tac_cases_from_csone)
    assert "def matches_customer" not in src, (
        "Round 39 / Phase 1.1 removed the fuzzy ``matches_customer`` "
        "closure.  Re-introducing it brings back the ERIE/FARMERS "
        "double-attribution bug -- use the three-tier authoritative "
        "join (SUBSCRIPTION_ID -> ACCOUNT_ID_C -> exact-name) instead."
    )
    # Also pin that the SUBSCRIPTION_ID priority comment is in the source
    # so the next reader knows the matching strategy without spelunking.
    assert_in_source(src, "Round 39 / Phase 1.1", label='src')
    assert_in_source(src, "SUBSCRIPTION_ID", label='src')
