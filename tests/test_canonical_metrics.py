"""
Unit tests for canonical_metrics.py.

These pin the contract that *every* report path consumes. If any of
these break, the cross-report parity tests will follow because the
consumers (Leader / Compact / EI / Comprehensive / Renewal) all share
this single source of truth.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import canonical_metrics as cm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def csone_df():
    """Five canonical TAC rows: 1 P1, 1 P2, 1 P3, 1 P4, 1 Unknown.

    The P1 row references a BEMS id so BEMS counters can lock too.
    """
    now = datetime.utcnow()
    return pd.DataFrame(
        [
            {
                "customer_name": "Acme Corp",
                "SR Number": "TAC-001",
                "Title": "Outage BEMS-12345",
                "Severity": "P1",
                "Transaction ID": "BEMS-12345",
                "Date/Time Opened": (now - timedelta(days=3)).isoformat(),
                "Status": "Open",
            },
            {
                "customer_name": "Acme Corp",
                "SR Number": "TAC-002",
                "Title": "Login slow",
                "Severity": "P2",
                "Transaction ID": "",
                "Date/Time Opened": (now - timedelta(days=10)).isoformat(),
                "Status": "Open",
            },
            {
                "customer_name": "Beta Inc",
                "SR Number": "TAC-003",
                "Title": "Config issue",
                "Severity": "P3",
                "Transaction ID": "",
                "Date/Time Opened": (now - timedelta(days=20)).isoformat(),
                "Status": "Closed",
            },
            {
                "customer_name": "Beta Inc",
                "SR Number": "TAC-004",
                "Title": "Provision new user",
                "Severity": "P4",
                "Transaction ID": "",
                "Date/Time Opened": (now - timedelta(days=4)).isoformat(),
                "Status": "Closed",
            },
            {
                "customer_name": "Gamma LLC",
                "SR Number": "TAC-005",
                "Title": "How do I export?",
                "Severity": "",
                "Transaction ID": "",
                "Date/Time Opened": (now - timedelta(days=2)).isoformat(),
                "Status": "Open",
            },
        ]
    )


@pytest.fixture
def ab_df():
    """Three AB rows: 1 Critical, 1 High, 1 Medium."""
    return pd.DataFrame(
        [
            {
                "customer_name": "Acme Corp",
                "SUBJECT_C": "Adoption issue 1",
                "SEVERITY_C": "Critical",
                "AB_STATUS_C": "Open",
                "ID": "AB001",
            },
            {
                "customer_name": "Acme Corp",
                "SUBJECT_C": "Adoption issue 2 BEMS-99999",
                "SEVERITY_C": "High",
                "AB_STATUS_C": "Open",
                "ID": "AB002",
            },
            {
                "customer_name": "Beta Inc",
                "SUBJECT_C": "Adoption issue 3",
                "SEVERITY_C": "Medium",
                "AB_STATUS_C": "Closed",
                "ID": "AB003",
            },
        ]
    )


@pytest.fixture
def ap_df():
    return pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SUBJECT_C": "AP1"},
            {"customer_name": "Beta Inc", "SUBJECT_C": "AP2"},
        ]
    )


@pytest.fixture
def cp_df():
    return pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SCORE__C": 8.0},
            {"customer_name": "Beta Inc", "SCORE__C": 4.0},
            {"customer_name": "Gamma LLC", "SCORE__C": 6.0},
        ]
    )


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def test_count_customers_unique_union(ab_df, csone_df):
    assert cm.count_customers(ab_df=ab_df, csone_df=csone_df) == 3


def test_count_customers_drops_unknown():
    df = pd.DataFrame({"customer_name": ["Acme", "Unknown", "", None, "Beta"]})
    assert cm.count_customers(csone_df=df) == 2


def test_list_customers_is_sorted(ab_df, csone_df):
    names = cm.list_customers(ab_df=ab_df, csone_df=csone_df)
    assert names == sorted(names)
    assert "Acme Corp" in names


# ---------------------------------------------------------------------------
# Priority counts
# ---------------------------------------------------------------------------


def test_priority_breakdown_sums_to_total(csone_df):
    breakdown = cm.count_priority_breakdown(csone_df)
    assert sum(breakdown.values()) == cm.count_total_tac(csone_df) == 5


def test_individual_priority_counters(csone_df):
    assert cm.count_p1(csone_df) == 1
    assert cm.count_p2(csone_df) == 1
    assert cm.count_p3(csone_df) == 1
    assert cm.count_p4(csone_df) == 1
    assert cm.count_unknown_priority(csone_df) == 1


def test_count_escalated_is_p1_plus_p2(csone_df):
    assert cm.count_escalated(csone_df) == 2


def test_priority_count_handles_empty():
    assert cm.count_p1(None) == 0
    assert cm.count_p2(pd.DataFrame()) == 0
    assert cm.count_priority_breakdown(None) == {
        "P1": 0,
        "P2": 0,
        "P3": 0,
        "P4": 0,
        "Unknown": 0,
    }


# ---------------------------------------------------------------------------
# BEMS
# ---------------------------------------------------------------------------


def test_bems_canonical_tac_only(csone_df, ab_df):
    assert cm.count_bems(csone_df) == 1
    assert cm.count_bems(csone_df, mode=cm.BEMS_MODE_CANONICAL) == 1


def test_bems_combined_ab_tac_includes_both(csone_df, ab_df):
    combined = cm.count_bems(csone_df, ab_df=ab_df, mode=cm.BEMS_MODE_COMBINED_AB_TAC)
    assert combined == 2


def test_bems_unique_ids_dedupes(csone_df, ab_df):
    unique = cm.count_bems(csone_df, ab_df=ab_df, mode=cm.BEMS_MODE_UNIQUE_IDS)
    assert unique == 2  # BEMS-12345 + BEMS-99999


def test_bems_invalid_mode_raises(csone_df):
    with pytest.raises(ValueError):
        cm.count_bems(csone_df, mode="not_a_real_mode")


def test_bems_rate_calculation(csone_df):
    assert cm.bems_rate(csone_df) == pytest.approx(20.0, rel=1e-3)


def test_bems_rate_empty_safe():
    assert cm.bems_rate(pd.DataFrame()) == 0.0
    assert cm.bems_rate(None) == 0.0


# ---------------------------------------------------------------------------
# Critical adoption barriers
# ---------------------------------------------------------------------------


def test_critical_ab_modes_distinct(ab_df):
    crit_only = cm.count_critical_barriers(
        ab_df, mode=cm.CRITICAL_AB_MODE_CRITICAL_ONLY
    )
    crit_or_high = cm.count_critical_barriers(
        ab_df, mode=cm.CRITICAL_AB_MODE_CRITICAL_OR_HIGH
    )
    assert crit_only == 1
    assert crit_or_high == 2
    assert crit_or_high >= crit_only


def test_critical_ab_invalid_mode_raises(ab_df):
    with pytest.raises(ValueError):
        cm.count_critical_barriers(ab_df, mode="foo")


def test_count_total_barriers_and_open(ab_df):
    assert cm.count_total_barriers(ab_df) == 3
    assert cm.count_open_barriers(ab_df) == 2


# ---------------------------------------------------------------------------
# Total Activities (3 modes)
# ---------------------------------------------------------------------------


def test_total_activities_modes(ap_df, ab_df, cp_df, csone_df):
    bems = cm.count_bems(csone_df)
    leader = cm.count_total_activities(
        action_plans_df=ap_df,
        ab_df=ab_df,
        customer_pulse_df=cp_df,
        bems_count=bems,
        mode=cm.ACTIVITIES_MODE_LEADER_SUMMARY,
    )
    member = cm.count_total_activities(
        action_plans_df=ap_df,
        ab_df=ab_df,
        customer_pulse_df=cp_df,
        mode=cm.ACTIVITIES_MODE_MEMBER_TABLE,
    )
    overall = cm.count_total_activities(
        action_plans_df=ap_df,
        ab_df=ab_df,
        customer_pulse_df=cp_df,
        tac_df=csone_df,
        mode=cm.ACTIVITIES_MODE_OVERALL_SUMMARY,
    )
    full = cm.count_total_activities(
        action_plans_df=ap_df,
        ab_df=ab_df,
        customer_pulse_df=cp_df,
        tac_df=csone_df,
        bems_count=bems,
        mode=cm.ACTIVITIES_MODE_FULL,
    )
    # Three modes are intentionally distinct.
    assert leader == 2 + 3 + 3 + bems
    assert member == 2 + 3 + 3
    assert overall == 2 + 3 + 3 + 5
    assert full == 2 + 3 + 3 + 5 + bems
    # Sanity ordering.
    assert member <= leader <= full
    assert member <= overall <= full


def test_total_activities_invalid_mode():
    with pytest.raises(ValueError):
        cm.count_total_activities(mode="invalid")


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


def test_compute_high_risk_count_0_to_100():
    profiles = {
        "A": {"risk_band": "CRITICAL", "risk_score_0_100": 90},
        "B": {"risk_band": "HIGH", "risk_score_0_100": 70},
        "C": {"risk_band": "MEDIUM", "risk_score_0_100": 45},
        "D": {"risk_band": "LOW", "risk_score_0_100": 20},
    }
    assert cm.compute_high_risk_count(profiles) == 2


def test_compute_high_risk_count_0_to_10_legacy():
    profiles = {
        "A": {"risk_score_0_10": 9.0},
        "B": {"risk_score_0_10": 6.5},
        "C": {"risk_score_0_10": 5.0},
        "D": {"color": "red"},
    }
    assert cm.compute_high_risk_count(profiles, scale=cm.RISK_SCALE_0_TO_10) == 3


def test_high_risk_count_handles_missing_keys():
    profiles = {"A": {}, "B": {"risk_band": "n/a"}}
    assert cm.compute_high_risk_count(profiles) == 0


def test_high_risk_invalid_scale_raises():
    with pytest.raises(ValueError):
        cm.compute_high_risk_count({"A": {}}, scale="bogus")


# ---------------------------------------------------------------------------
# Pulse sentiment
# ---------------------------------------------------------------------------


def test_pulse_sentiment_thresholds(cp_df):
    summary = cm.pulse_sentiment(cp_df)
    assert summary["count"] == 3
    assert summary["positive"] == 1  # 8.0
    assert summary["negative"] == 1  # 4.0
    assert summary["neutral"] == 1  # 6.0
    assert summary["sentiment"] in {"Positive", "Neutral", "Negative"}


def test_pulse_sentiment_scale_conversion():
    # Scores on the legacy /5 scale must be doubled before thresholding.
    df = pd.DataFrame({"SCORE__C": [4.0, 4.0, 4.0]})  # /5 -> 8.0/10
    summary = cm.pulse_sentiment(df, scale=cm.PULSE_SCALE_0_TO_5)
    assert summary["positive"] == 3
    assert summary["sentiment"] == "Positive"


def test_pulse_sentiment_empty_safe():
    s = cm.pulse_sentiment(None)
    assert s["count"] == 0
    assert s["sentiment"] == "Unknown"


# ---------------------------------------------------------------------------
# Case-type classifiers
# ---------------------------------------------------------------------------


def test_break_fix_and_provisioning_safe_when_missing(csone_df):
    # Without the case_type_class column we must not raise; helper enriches.
    assert cm.count_break_fix(csone_df) >= 0
    assert cm.count_provisioning(csone_df) >= 0


# ---------------------------------------------------------------------------
# build_portfolio_metrics
# ---------------------------------------------------------------------------


def test_build_portfolio_metrics_reconciles_with_priority_breakdown(ab_df, csone_df):
    payload = cm.build_portfolio_metrics(ab_df=ab_df, csone_df=csone_df)
    bd = cm.count_priority_breakdown(csone_df)

    assert payload["total_customers"] == 3
    assert payload["total_barriers"] == 3
    assert payload["total_cases"] == 5
    assert payload["bems_count"] == 1
    # Priority parity: P1+P2+P3+P4+Unknown == total_cases.
    sum_priorities = (
        payload["p1_cases"]
        + payload["p2_cases"]
        + payload["p3_cases"]
        + payload["p4_cases"]
        + payload["unknown_priority_cases"]
    )
    assert sum_priorities == payload["total_cases"]
    # critical_p1/high_p2 must equal canonical counters.
    assert payload["critical_p1"] == bd["P1"]
    assert payload["high_p2"] == bd["P2"]


def test_build_portfolio_metrics_merges_defects(ab_df, csone_df):
    payload = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        defects={"total_defects": 7, "security_advisories_count": 2},
    )
    assert payload["defects_count"] == 7
    assert payload["security_advisories_count"] == 2


def test_build_portfolio_metrics_risk_bands(ab_df, csone_df):
    profiles = {
        "Acme Corp": {"risk_band": "CRITICAL", "risk_score_0_100": 90},
        "Beta Inc": {"risk_band": "MEDIUM", "risk_score_0_100": 45},
        "Gamma LLC": {"risk_band": "HEALTHY", "risk_score_0_100": 5},
    }
    payload = cm.build_portfolio_metrics(
        ab_df=ab_df,
        csone_df=csone_df,
        risk_profiles=profiles,
        risk_scale=cm.RISK_SCALE_0_TO_100,
    )
    assert payload["high_risk_customers"] == 1
    assert payload["medium_risk_customers"] == 1
    assert payload["healthy_customers"] == 1
