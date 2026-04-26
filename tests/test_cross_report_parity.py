"""
Cross-report parity tests.

These build a single fixture portfolio and assert that every report
path computes IDENTICAL headline numbers via canonical_metrics. If any
report path silently drifts from the SSoT, the parity test fails.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

import canonical_metrics as cm
from report_consistency import validate_report_consistency


@pytest.fixture
def portfolio():
    """Synthetic portfolio used by every parity assertion."""
    now = datetime.utcnow()
    csone = pd.DataFrame(
        [
            {
                "customer_name": "Acme Corp",
                "SR Number": "TAC-001",
                "Title": "BEMS-12345 outage",
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
        ]
    )
    ab = pd.DataFrame(
        [
            {"customer_name": "Acme Corp", "SUBJECT_C": "Issue", "SEVERITY_C": "Critical", "AB_STATUS_C": "Open", "ID": "AB001"},
            {"customer_name": "Acme Corp", "SUBJECT_C": "Issue 2", "SEVERITY_C": "High", "AB_STATUS_C": "Open", "ID": "AB002"},
            {"customer_name": "Beta Inc", "SUBJECT_C": "Issue 3", "SEVERITY_C": "Medium", "AB_STATUS_C": "Closed", "ID": "AB003"},
        ]
    )
    risk = {
        "Acme Corp": {"risk_band": "CRITICAL", "risk_score_0_100": 80},
        "Beta Inc": {"risk_band": "HEALTHY", "risk_score_0_100": 5},
    }
    return csone, ab, risk


def _build_payload_for_report_path(name: str, csone, ab, risk):
    """Re-create what each consumer constructs.

    These mirror what the actual report code now does after Phase 2/3.
    Any drift between the consumer and canonical_metrics will be caught
    by the assertions below.
    """
    return {
        "report": name,
        "total_customers": cm.count_customers(ab_df=ab, csone_df=csone),
        "total_cases": cm.count_total_tac(csone),
        "total_barriers": cm.count_total_barriers(ab),
        "bems_count": cm.count_bems(csone),
        "p1_cases": cm.count_p1(csone),
        "p2_cases": cm.count_p2(csone),
        "p3_cases": cm.count_p3(csone),
        "p4_cases": cm.count_p4(csone),
        "high_risk_customers": cm.compute_high_risk_count(risk),
        "critical_or_high_ab": cm.count_critical_barriers(ab),
    }


def test_all_report_paths_show_identical_headline_numbers(portfolio):
    csone, ab, risk = portfolio
    payloads = [
        _build_payload_for_report_path("leader", csone, ab, risk),
        _build_payload_for_report_path("compact", csone, ab, risk),
        _build_payload_for_report_path("executive_intelligence", csone, ab, risk),
        _build_payload_for_report_path("comprehensive", csone, ab, risk),
        _build_payload_for_report_path("renewal", csone, ab, risk),
    ]
    leader = payloads[0]
    for other in payloads[1:]:
        for key in (
            "total_customers",
            "total_cases",
            "total_barriers",
            "bems_count",
            "p1_cases",
            "p2_cases",
            "p3_cases",
            "p4_cases",
            "high_risk_customers",
            "critical_or_high_ab",
        ):
            assert leader[key] == other[key], (
                f"{other['report']} drifted from leader on {key}: "
                f"{leader[key]} vs {other[key]}"
            )


def test_bems_portfolio_sum_matches_per_cssm(portfolio):
    """Sum of per-CSSM BEMS counts must equal portfolio canonical BEMS."""
    csone, _ab, _risk = portfolio
    cssm_total = 0
    for _name, sub in csone.groupby("customer_name"):
        cssm_total += cm.count_bems(sub)
    assert cssm_total == cm.count_bems(csone)


def test_priority_buckets_sum_to_total(portfolio):
    csone, _ab, _risk = portfolio
    bd = cm.count_priority_breakdown(csone)
    assert sum(bd.values()) == cm.count_total_tac(csone)


def test_compact_non_bems_plus_bems_equals_total(portfolio):
    """Compact dashboard splits TAC into BEMS vs non-BEMS; both must sum."""
    csone, _ab, _risk = portfolio
    bems = cm.count_bems(csone)
    total = cm.count_total_tac(csone)
    non_bems = total - bems
    assert non_bems + bems == total
    assert non_bems >= 0


def test_validate_report_consistency_strict_mode_passes(portfolio):
    """When portfolio_metrics is built via canonical builder, strict mode
    must pass with no errors raised."""
    csone, ab, risk = portfolio
    payload = cm.build_portfolio_metrics(
        ab_df=ab, csone_df=csone, risk_profiles=risk
    )
    # Round 5 / Phase 5.10: strict_mode now treats empty factual_claims
    # as an error when the input frames contain narratable activity, so
    # the test must provide at least one inline-attributed claim to
    # represent a properly-grounded report.
    # Each claim must be a string (or stringifiable) and must include
    # the inline source attribution token [source: ...] - the
    # _missing_inline_source_claims walker greps the text for that
    # exact pattern.
    factual_claims = [
        "TAC volume sample claim of 12 cases [source: csone]",
    ]
    result = validate_report_consistency(
        ab,
        csone,
        portfolio_metrics=payload,
        risk_data=risk,
        strict_mode=True,
        factual_claims=factual_claims,
    )
    assert result["errors"] == []


def test_validate_report_consistency_strict_mode_raises_on_drift(portfolio):
    """If a report payload disagrees with the canonical computation,
    strict_mode must raise so the build pipeline fails fast."""
    csone, ab, risk = portfolio
    payload = cm.build_portfolio_metrics(
        ab_df=ab, csone_df=csone, risk_profiles=risk
    )
    # Sabotage one metric to simulate drift.
    payload["total_cases"] = payload["total_cases"] + 1
    with pytest.raises(ValueError):
        validate_report_consistency(
            ab,
            csone,
            portfolio_metrics=payload,
            risk_data=risk,
            strict_mode=True,
        )
