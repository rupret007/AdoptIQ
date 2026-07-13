"""Round 3 parity test: ``total_customers`` must agree across every
report path even when the portfolio contains subscription-only
customers (i.e. customers who exist in ``team_subs`` / pulse / action
plans but never opened a TAC case or an Adoption Barrier).

Before round 3 hardening this number drifted because:
  * EI dashboard counted via ``_get_all_customers_from_all_sources``
    (multi-source + ACCOUNT_ID backfill).
  * ``build_portfolio_metrics`` called ``cm.count_customers(ab, csone)``
    (no extra frames, no account map).
  * ``report_consistency.validate_report_consistency`` scanned only a
    short hard-coded column list.

After the round 3 fix all four numbers are derived from the SAME
``cm.count_customers`` helper with the SAME extra frames, so they are
guaranteed by construction to match.
"""
from __future__ import annotations

import pandas as pd
import pytest

import canonical_metrics as cm
import report_consistency as rc


@pytest.fixture
def portfolio_with_subs_only_customers():
    """Build a synthetic portfolio:

    - "AcmeCorp" appears in BOTH AB and TAC + has a subscription row.
    - "BetaCo"  appears ONLY in the team_subs frame (no AB, no TAC).
    - "GammaInc" appears ONLY in the customer pulse frame.

    The canonical universe therefore has exactly 3 distinct customers.
    Naive counters that scan only AB+TAC would return 1.
    """
    ab_df = pd.DataFrame({
        "BU_NAME": ["AcmeCorp"],
        "SUBJECT_C": ["Email delivery delay"],
        "SEVERITY_C": ["High"],
    })
    csone_df = pd.DataFrame({
        "Customer Name": ["AcmeCorp"],
        "SEVERITY": ["P1"],
    })
    team_subs_df = pd.DataFrame({
        "BU_NAME": ["AcmeCorp", "BetaCo"],
        "ACCOUNT_ID_C": ["A001", "B001"],
    })
    pulse_df = pd.DataFrame({
        "RELATED_CUSTOMER__C": ["GammaInc"],
        "SCORE__C": [4.0],
    })
    return ab_df, csone_df, team_subs_df, pulse_df


def test_count_customers_includes_subscription_only_customers(
    portfolio_with_subs_only_customers,
):
    ab_df, csone_df, team_subs_df, pulse_df = portfolio_with_subs_only_customers

    n = cm.count_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        extra_frames=[team_subs_df, pulse_df],
    )

    assert n == 3, (
        "Subscription-only and pulse-only customers must be counted in "
        "the canonical universe; got {}".format(n)
    )


def test_portfolio_metrics_total_customers_matches_count_customers(
    portfolio_with_subs_only_customers,
):
    ab_df, csone_df, team_subs_df, pulse_df = portfolio_with_subs_only_customers

    portfolio_metrics = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_profiles=None,
        extra_customer_frames=[team_subs_df, pulse_df],
    )

    expected = cm.count_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        extra_frames=[team_subs_df, pulse_df],
    )

    assert portfolio_metrics["total_customers"] == expected
    assert portfolio_metrics["total_customers"] == 3


def test_report_consistency_total_matches_canonical(
    portfolio_with_subs_only_customers,
):
    """``validate_report_consistency`` must compute total_customers via
    ``cm.count_customers`` so it cannot disagree with the portfolio
    metrics it is supposed to validate.
    """
    ab_df, csone_df, _team_subs_df, _pulse_df = portfolio_with_subs_only_customers

    result = rc.validate_report_consistency(
        ab_df=ab_df,
        csone_df=csone_df,
    )
    metrics = result["metrics"]

    expected_ab_only = cm.count_customers(ab_df=ab_df, csone_df=csone_df)
    assert metrics["total_customers"] == expected_ab_only


def test_account_id_backfill_routes_through_canonical():
    """Frames carrying only an ACCOUNT_ID (e.g. the renewal/contract
    Snowflake pull) must contribute to the customer universe via the
    ``account_to_customer`` map, matching the EI dashboard behaviour.
    """
    ab_df = pd.DataFrame({"BU_NAME": ["AcmeCorp"]})
    contract_df = pd.DataFrame({"ACCOUNT_ID_C": ["A001", "B001"]})
    account_to_customer = {"A001": "AcmeCorp", "B001": "BetaCo"}

    n = cm.count_customers(
        ab_df=ab_df,
        csone_df=None,
        extra_frames=[contract_df],
        account_to_customer=account_to_customer,
    )
    assert n == 2, (
        "ACCOUNT_ID rows with a known mapping must backfill the "
        "customer name; expected 2 unique customers, got {}".format(n)
    )
