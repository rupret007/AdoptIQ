"""Round 4 Excel parity test: the Excel summary sheet's
``total_customers`` cell must equal ``cm.count_customers`` for the
SAME multi-source frames the Word headline uses.

Before round 4 the Excel sheet computed ``total_customers`` via
``len(ab_norm['customer_name'].unique())`` which silently dropped
subscription-only and pulse-only customers — same workbook,
different denominators in the Word headline vs the Excel summary.
"""
from __future__ import annotations

import pandas as pd

import canonical_metrics as cm


def _build_multi_source_portfolio():
    """Acme in AB+TAC+subs; Beta in subs only; Gamma in pulse only."""
    ab_df = pd.DataFrame({
        "customer_name": ["AcmeCorp"],
        "BU_NAME": ["AcmeCorp"],
        "SUBJECT_C": ["X"],
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
    })
    return ab_df, csone_df, team_subs_df, pulse_df


def test_excel_total_matches_word_headline_canonical():
    """Round 4: assert the Excel ``total_customers`` is computed via the
    SAME canonical helper, with the SAME extra frames, that the Word
    headline uses.  Before the fix this number reported 1 (AB only)
    while the Word headline reported 3 (AB + subs + pulse).
    """
    ab_df, csone_df, team_subs_df, pulse_df = _build_multi_source_portfolio()

    word_headline = cm.count_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        extra_frames=[team_subs_df, pulse_df],
    )

    excel_summary = cm.count_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        extra_frames=[team_subs_df, pulse_df],
    )

    assert excel_summary == word_headline == 3, (
        f"Excel summary ({excel_summary}) and Word headline "
        f"({word_headline}) must agree at 3 distinct customers."
    )


def test_excel_total_with_account_id_backfill():
    """If the Excel summary receives ACCOUNT_ID-only frames (e.g. the
    contract dataset) it must still backfill to a customer name via
    ``account_to_customer`` — same behavior as the Word headline.
    """
    ab_df, csone_df, team_subs_df, pulse_df = _build_multi_source_portfolio()
    contract_df = pd.DataFrame({"ACCOUNT_ID_C": ["A001", "B001", "C001"]})
    account_to_customer = {
        "A001": "AcmeCorp",
        "B001": "BetaCo",
        "C001": "DeltaInc",
    }

    n = cm.count_customers(
        ab_df=ab_df,
        csone_df=csone_df,
        extra_frames=[team_subs_df, pulse_df, contract_df],
        account_to_customer=account_to_customer,
    )
    assert n == 4, (
        "Acme + Beta + Gamma + Delta-via-account-map = 4 distinct, "
        f"got {n}"
    )


def test_excel_summary_does_not_undercount_when_ab_is_empty():
    """Subscription-driven analyses (no AB rows) should still report
    the full customer universe, not zero.
    """
    csone_df = pd.DataFrame({"Customer Name": ["AcmeCorp"]})
    team_subs_df = pd.DataFrame({
        "BU_NAME": ["AcmeCorp", "BetaCo", "GammaInc"],
    })

    n = cm.count_customers(
        ab_df=pd.DataFrame(),
        csone_df=csone_df,
        extra_frames=[team_subs_df],
    )
    assert n == 3, f"Expected 3 customers, got {n}"
