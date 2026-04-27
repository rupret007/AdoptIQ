"""Round 19 / Round 21 — Report Accuracy Golden Fixture.

Mission (per QUALITY_AUDIT.md L1299-1416, "Round 19 — Report Accuracy
Golden Fixture"): a known synthetic input + hand-computed expected
values for every KPI in the registry, so every report formatter can be
diffed programmatically against a single source of truth.

Phase 2 (this module): build deterministic synthetic frames.
Phase 3 (this module): hand-compute the EXPECTED_KPIS dict.

The fixture is anchored to the canonical metric helpers in
``canonical_metrics`` (count_*, build_portfolio_metrics, pulse_sentiment,
bems_rate). Every value below is computed by inspection against the
literal frames defined in this file, not by calling the helpers, so the
diff harness is independent of the SoT it validates.

Reconciliation invariants (registry L1344-1348, L1358):
  1. p1 + p2 + p3 + p4 + unknown_priority == total_cases
  2. critical + high_only + medium + low + healthy == len(risk_profiles)
  3. high_risk_customers == critical_risk + high_only_risk
  4. count_open_tac + count_closed_tac <= count_total_tac

All four are exercised by the Phase-2 frames + Phase-3 expected dict
in ``tests/test_round21_canonical_kpi_golden_fixture.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

import pandas as pd


# ---------------------------------------------------------------------------
# CSOne / TAC frame
# ---------------------------------------------------------------------------
#
# 20 rows. Distribution chosen to give clean, distinct buckets:
#
#   Priority  : 4 P1, 4 P2, 4 P3, 4 P4, 4 Unknown      (sum = 20)
#   Lifecycle : 12 Open, 6 Closed, 2 Unknown            (sum = 20, < and =)
#   Case type : 3 break_fix, 2 provisioning, 15 unknown
#   BEMS      : 4 rows carry BEMS-NNNNN in Transaction ID
#   Customers : 2 unique normalized names (AcmeCorp, BetaInc)
#
# Lifecycle / priority / case-type columns are pre-populated with the
# normalized values that ``add_case_lifecycle_fields`` would produce, so
# the canonical helpers exercise the fast path and the fixture is robust
# to any future tweak in the normalization regex tables. Raw "Status" /
# "Severity" / "Title" columns are kept alongside so the frame still
# resembles a real CSOne export.

_CSONE_ROWS: List[Dict[str, Any]] = [
    # --- 4 x P1 (3 Open, 1 Closed) -- 1 BEMS, 1 break_fix
    {"customer_name": "AcmeCorp", "SR Number": "TAC-001",
     "Title": "Outage on production cluster",
     "Severity": "P1", "case_priority_norm": "P1", "severity_norm": "Critical",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "BEMS-10001",
     "case_type_class": "break_fix_technical"},
    {"customer_name": "AcmeCorp", "SR Number": "TAC-002",
     "Title": "Critical system unavailable",
     "Severity": "P1", "case_priority_norm": "P1", "severity_norm": "Critical",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "AcmeCorp", "SR Number": "TAC-003",
     "Title": "Replication lag P1",
     "Severity": "P1", "case_priority_norm": "P1", "severity_norm": "Critical",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-004",
     "Title": "Login outage post-upgrade",
     "Severity": "P1", "case_priority_norm": "P1", "severity_norm": "Critical",
     "Status": "Closed", "case_status_norm": "Closed", "is_open": False, "is_closed": True,
     "Transaction ID": "BEMS-10002",
     "case_type_class": "break_fix_technical"},
    # --- 4 x P2 (3 Open, 1 Closed)
    {"customer_name": "AcmeCorp", "SR Number": "TAC-005",
     "Title": "API latency degradation",
     "Severity": "P2", "case_priority_norm": "P2", "severity_norm": "High",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "AcmeCorp", "SR Number": "TAC-006",
     "Title": "Provisioning license entitlement",
     "Severity": "P2", "case_priority_norm": "P2", "severity_norm": "High",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "BEMS-10003",
     "case_type_class": "provisioning_request"},
    {"customer_name": "BetaInc", "SR Number": "TAC-007",
     "Title": "Slow query response",
     "Severity": "P2", "case_priority_norm": "P2", "severity_norm": "High",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-008",
     "Title": "Connection drops intermittently",
     "Severity": "P2", "case_priority_norm": "P2", "severity_norm": "High",
     "Status": "Closed", "case_status_norm": "Closed", "is_open": False, "is_closed": True,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    # --- 4 x P3 (3 Open, 1 Closed) -- 1 break_fix, 1 BEMS
    {"customer_name": "AcmeCorp", "SR Number": "TAC-009",
     "Title": "Database error blocking writes",
     "Severity": "P3", "case_priority_norm": "P3", "severity_norm": "Medium",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "BEMS-10004",
     "case_type_class": "break_fix_technical"},
    {"customer_name": "AcmeCorp", "SR Number": "TAC-010",
     "Title": "Configuration mismatch",
     "Severity": "P3", "case_priority_norm": "P3", "severity_norm": "Medium",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-011",
     "Title": "Reporting question",
     "Severity": "P3", "case_priority_norm": "P3", "severity_norm": "Medium",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-012",
     "Title": "Documentation request",
     "Severity": "P3", "case_priority_norm": "P3", "severity_norm": "Medium",
     "Status": "Closed", "case_status_norm": "Closed", "is_open": False, "is_closed": True,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    # --- 4 x P4 (1 Open, 2 Closed, 1 Unknown lifecycle) -- 1 provisioning
    {"customer_name": "AcmeCorp", "SR Number": "TAC-013",
     "Title": "Feature request enable new module",
     "Severity": "P4", "case_priority_norm": "P4", "severity_norm": "Low",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "provisioning_request"},
    {"customer_name": "AcmeCorp", "SR Number": "TAC-014",
     "Title": "How-to question",
     "Severity": "P4", "case_priority_norm": "P4", "severity_norm": "Low",
     "Status": "Closed", "case_status_norm": "Closed", "is_open": False, "is_closed": True,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-015",
     "Title": "Cosmetic UI tweak",
     "Severity": "P4", "case_priority_norm": "P4", "severity_norm": "Low",
     "Status": "Closed", "case_status_norm": "Closed", "is_open": False, "is_closed": True,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-016",
     "Title": "Misc inquiry",
     "Severity": "P4", "case_priority_norm": "P4", "severity_norm": "Low",
     "Status": "Foo", "case_status_norm": "Unknown", "is_open": False, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    # --- 4 x Unknown priority (2 Open, 1 Closed, 1 Unknown lifecycle)
    {"customer_name": "AcmeCorp", "SR Number": "TAC-017",
     "Title": "Unclassified request",
     "Severity": "", "case_priority_norm": "Unknown", "severity_norm": "Unknown",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "AcmeCorp", "SR Number": "TAC-018",
     "Title": "Unclassified request 2",
     "Severity": "", "case_priority_norm": "Unknown", "severity_norm": "Unknown",
     "Status": "Open", "case_status_norm": "Open", "is_open": True, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-019",
     "Title": "Unclassified inquiry",
     "Severity": "", "case_priority_norm": "Unknown", "severity_norm": "Unknown",
     "Status": "Closed", "case_status_norm": "Closed", "is_open": False, "is_closed": True,
     "Transaction ID": "",
     "case_type_class": "unknown"},
    {"customer_name": "BetaInc", "SR Number": "TAC-020",
     "Title": "Unclassified misc",
     "Severity": "", "case_priority_norm": "Unknown", "severity_norm": "Unknown",
     "Status": "Foo", "case_status_norm": "Unknown", "is_open": False, "is_closed": False,
     "Transaction ID": "",
     "case_type_class": "unknown"},
]


def make_csone_df() -> pd.DataFrame:
    """Synthetic CSOne / TAC frame (20 rows)."""
    return pd.DataFrame(_CSONE_ROWS)


# ---------------------------------------------------------------------------
# Adoption Barrier frame
# ---------------------------------------------------------------------------
#
# 10 rows. Severity 2/2/2/2/2 across Critical/High/Medium/Low/Unknown.
# Status 6 Open, 3 Closed, 1 Unknown.
# Customers: AcmeCorp (4), BetaInc (3), GammaLLC (3) = 3 unique.

_AB_ROWS: List[Dict[str, Any]] = [
    {"customer_name": "AcmeCorp",  "SUBJECT_C": "AB issue 1",  "SEVERITY_C": "Critical",
     "severity_norm": "Critical",  "AB_STATUS_C": "Open",      "case_status_norm": "Open",      "ID": "AB001"},
    {"customer_name": "AcmeCorp",  "SUBJECT_C": "AB issue 2",  "SEVERITY_C": "Critical",
     "severity_norm": "Critical",  "AB_STATUS_C": "Open",      "case_status_norm": "Open",      "ID": "AB002"},
    {"customer_name": "AcmeCorp",  "SUBJECT_C": "AB issue 3",  "SEVERITY_C": "High",
     "severity_norm": "High",      "AB_STATUS_C": "Open",      "case_status_norm": "Open",      "ID": "AB003"},
    {"customer_name": "AcmeCorp",  "SUBJECT_C": "AB issue 4",  "SEVERITY_C": "High",
     "severity_norm": "High",      "AB_STATUS_C": "Closed",    "case_status_norm": "Closed",    "ID": "AB004"},
    {"customer_name": "BetaInc",   "SUBJECT_C": "AB issue 5",  "SEVERITY_C": "Medium",
     "severity_norm": "Medium",    "AB_STATUS_C": "Open",      "case_status_norm": "Open",      "ID": "AB005"},
    {"customer_name": "BetaInc",   "SUBJECT_C": "AB issue 6",  "SEVERITY_C": "Medium",
     "severity_norm": "Medium",    "AB_STATUS_C": "Open",      "case_status_norm": "Open",      "ID": "AB006"},
    {"customer_name": "BetaInc",   "SUBJECT_C": "AB issue 7",  "SEVERITY_C": "Low",
     "severity_norm": "Low",       "AB_STATUS_C": "Closed",    "case_status_norm": "Closed",    "ID": "AB007"},
    {"customer_name": "GammaLLC",  "SUBJECT_C": "AB issue 8",  "SEVERITY_C": "Low",
     "severity_norm": "Low",       "AB_STATUS_C": "Open",      "case_status_norm": "Open",      "ID": "AB008"},
    {"customer_name": "GammaLLC",  "SUBJECT_C": "AB issue 9",  "SEVERITY_C": "",
     "severity_norm": "Unknown",   "AB_STATUS_C": "Closed",    "case_status_norm": "Closed",    "ID": "AB009"},
    {"customer_name": "GammaLLC",  "SUBJECT_C": "AB issue 10", "SEVERITY_C": "",
     "severity_norm": "Unknown",   "AB_STATUS_C": "Foo",       "case_status_norm": "Unknown",   "ID": "AB010"},
]


def make_ab_df() -> pd.DataFrame:
    """Synthetic Adoption Barrier frame (10 rows)."""
    return pd.DataFrame(_AB_ROWS)


# ---------------------------------------------------------------------------
# Pulse frame (8 rows, 0-10 scale)
# ---------------------------------------------------------------------------
#
# Scores: 7.5, 8.0, 9.0 (positive >= 7.5)
#         5.5, 6.0, 7.0 (neutral, strictly between 5.0 and 7.5)
#         4.0, 5.0      (negative <= 5.0)
#
# Sum = 7.5 + 8.0 + 9.0 + 5.5 + 6.0 + 7.0 + 4.0 + 5.0 = 52.0
# Mean = 52.0 / 8 = 6.5 (rounds to 6.5, label "Neutral")

_PULSE_ROWS: List[Dict[str, Any]] = [
    {"RELATED_CUSTOMER__C": "AcmeCorp", "SCORE__C": 9.0},
    {"RELATED_CUSTOMER__C": "AcmeCorp", "SCORE__C": 8.0},
    {"RELATED_CUSTOMER__C": "BetaInc",  "SCORE__C": 7.5},
    {"RELATED_CUSTOMER__C": "BetaInc",  "SCORE__C": 7.0},
    {"RELATED_CUSTOMER__C": "GammaLLC", "SCORE__C": 6.0},
    {"RELATED_CUSTOMER__C": "GammaLLC", "SCORE__C": 5.5},
    {"RELATED_CUSTOMER__C": "DeltaCo",  "SCORE__C": 5.0},
    {"RELATED_CUSTOMER__C": "DeltaCo",  "SCORE__C": 4.0},
]


def make_pulse_df() -> pd.DataFrame:
    """Synthetic Pulse frame (8 rows, 0-10 scale)."""
    return pd.DataFrame(_PULSE_ROWS)


# ---------------------------------------------------------------------------
# Risk profiles (10 customers, 0-100 scale)
# ---------------------------------------------------------------------------
#
# Bands (from risk_scoring.RISK_BAND_THRESHOLDS):
#   CRITICAL >= 75, HIGH 55-75, MEDIUM 35-55, LOW 15-35, HEALTHY < 15
# Distribution: 2 each across all five bands.

_RISK_PROFILES: Dict[str, Dict[str, Any]] = {
    "RiskCust01": {"risk_band": "CRITICAL", "risk_score_0_100": 90.0},
    "RiskCust02": {"risk_band": "CRITICAL", "risk_score_0_100": 80.0},
    "RiskCust03": {"risk_band": "HIGH",     "risk_score_0_100": 65.0},
    "RiskCust04": {"risk_band": "HIGH",     "risk_score_0_100": 60.0},
    "RiskCust05": {"risk_band": "MEDIUM",   "risk_score_0_100": 45.0},
    "RiskCust06": {"risk_band": "MEDIUM",   "risk_score_0_100": 40.0},
    "RiskCust07": {"risk_band": "LOW",      "risk_score_0_100": 25.0},
    "RiskCust08": {"risk_band": "LOW",      "risk_score_0_100": 20.0},
    "RiskCust09": {"risk_band": "HEALTHY",  "risk_score_0_100": 10.0},
    "RiskCust10": {"risk_band": "HEALTHY",  "risk_score_0_100": 5.0},
}


def make_risk_profiles() -> Dict[str, Dict[str, Any]]:
    """Synthetic risk profiles dict (10 customers, 0-100 scale).

    Returns a fresh dict copy so callers cannot mutate the module-level
    canonical fixture.
    """
    return {name: dict(profile) for name, profile in _RISK_PROFILES.items()}


# ---------------------------------------------------------------------------
# Extra customer frames (CSConsole-style)
# ---------------------------------------------------------------------------
#
# These exercise the multi-source customer expansion in count_customers.
# Adds DeltaCo + EpsilonInc to the customer universe (neither appears
# in csone_df or ab_df). Total customer universe becomes:
#   {AcmeCorp, BetaInc, GammaLLC, DeltaCo, EpsilonInc} = 5
#
# Round 25 / Phase A note: ``csconsole_customer_pulse`` is the frame
# the production formatters thread into ``count_customers(...,
# pulse_df=...)`` for the headline ``Total Customers`` tile.  After
# Round 25 the headline narrows to the (AB ∪ CSOne ∪ Pulse) universe
# only, so this frame must contribute BOTH "extra" customers
# (DeltaCo + EpsilonInc) to keep the fixture's
# ``EXPECTED_KPIS["total_customers"] == 5`` invariant intact.  Pre-
# Round 25 the headline also pulled customers from action_plans /
# success_priorities / adoption_barriers, which let those frames
# carry the unique names; under Round 25 the headline ignores
# extras entirely and only ``customer_pulse`` is part of the
# displayed universe.

def make_extra_frames() -> List[pd.DataFrame]:
    """Minimal CSConsole-style frames that contribute new customer names."""
    csconsole_action_plans = pd.DataFrame([
        {"RELATED_CUSTOMER__C": "DeltaCo", "ID": "AP001"},
        {"RELATED_CUSTOMER__C": "AcmeCorp", "ID": "AP002"},  # already known; dedup
    ])
    # Round 25 / Phase A: customer_pulse must carry both DeltaCo and
    # EpsilonInc so the narrow ``count_customers(ab_df, csone_df,
    # pulse_df=customer_pulse)`` headline shape used by both Word and
    # Excel still produces the expected 5-customer universe.  Pre-
    # Round 25 it was acceptable for DeltaCo to live only in
    # ``action_plans`` because the headline also widened via extras;
    # that is no longer true.
    csconsole_customer_pulse = pd.DataFrame([
        {"RELATED_CUSTOMER__C": "EpsilonInc", "ID": "CP001"},
        {"RELATED_CUSTOMER__C": "DeltaCo", "ID": "CP002"},
    ])
    csconsole_success_priorities = pd.DataFrame([
        {"RELATED_CUSTOMER__C": "DeltaCo", "ID": "SP001"},  # already added by AP frame
    ])
    csconsole_adoption_barriers = pd.DataFrame([
        {"RELATED_CUSTOMER__C": "EpsilonInc", "ID": "CAB001"},  # already added by CP frame
    ])
    return [
        csconsole_action_plans,
        csconsole_customer_pulse,
        csconsole_success_priorities,
        csconsole_adoption_barriers,
    ]


def make_all() -> Dict[str, Any]:
    """Bundle all frames + profiles into one dict for convenience."""
    return {
        "ab_df": make_ab_df(),
        "csone_df": make_csone_df(),
        "pulse_df": make_pulse_df(),
        "risk_profiles": make_risk_profiles(),
        "extra_frames": make_extra_frames(),
    }


# ---------------------------------------------------------------------------
# Phase 3 — Hand-computed EXPECTED_KPIS dict
# ---------------------------------------------------------------------------
#
# Every value below is derived by inspection against the literal
# _CSONE_ROWS / _AB_ROWS / _PULSE_ROWS / _RISK_PROFILES tables above,
# NOT by calling the canonical helpers. This preserves the diff-harness
# contract: if the helper drifts from the registry definition, the test
# fails because the harness's expected values were computed independently.
#
# Reconciliation invariants:
#   1. p1+p2+p3+p4+unknown = 4+4+4+4+4 = 20 = total_cases  (registry L1344)
#   2. crit+high_only+med+low+healthy = 2+2+2+2+2 = 10 = len(risk_profiles)
#                                                       (registry L1346)
#   3. high_risk_customers = 4 = critical_risk(2) + high_only_risk(2)
#                                                       (registry L1348)
#   4. count_open_tac + count_closed_tac = 12 + 6 = 18 <= 20 = total_cases
#                                                       (registry L1358)

EXPECTED_KPIS: Dict[str, Any] = {
    # Core portfolio
    "total_customers": 5,           # AcmeCorp, BetaInc, GammaLLC, DeltaCo, EpsilonInc
    "total_barriers": 10,
    "total_cases": 20,
    "bems_count": 4,                # canonical mode: rows in csone_df with BEMS-* token
    "p1_cases": 4, "p2_cases": 4, "p3_cases": 4, "p4_cases": 4,
    "unknown_priority_cases": 4,
    "critical_p1": 4,               # alias of p1_cases in build_portfolio_metrics
    "high_p2": 4,                   # alias of p2_cases in build_portfolio_metrics
    "break_fix_cases": 3,
    "provisioning_cases": 2,

    # Risk bands
    "high_risk_customers": 4,        # CRITICAL(2) + HIGH(2)
    "critical_risk_customers": 2,
    "high_only_risk_customers": 2,
    "medium_risk_customers": 2,
    "low_risk_customers": 2,
    "healthy_customers": 2,
    "risk_scale": "0_to_100",

    # TAC lifecycle
    "count_open_tac": 12,
    "count_closed_tac": 6,
    "count_escalated": 8,            # P1(4) + P2(4)

    # BEMS rate (canonical: round(bems / total * 100, 2))
    "bems_rate": 20.0,               # round(4 / 20 * 100, 2)

    # Adoption Barriers (default mode: critical_or_high)
    "count_critical_barriers": 4,    # 2 Critical + 2 High
    "count_open_barriers": 6,        # 4 AcmeCorp Open + 2 BetaInc Open

    # Pulse sentiment (canonical, 0-10 scale)
    "pulse": {
        "count": 8,
        "mean_0_to_10": 6.5,         # round(52.0 / 8, 2)
        "positive": 3,               # 9.0, 8.0, 7.5  (>= 7.5)
        "neutral": 3,                # 7.0, 6.0, 5.5  (5.0 < x < 7.5)
        "negative": 2,               # 5.0, 4.0       (<= 5.0)
        "sentiment": "Neutral",      # mean 6.5 in (5.0, 7.5)
        "has_backfill_flag": False,
    },

    # Excel summary sheet labels (Round 15 / report_export_styling.build_summary_rows).
    # Order matters; the test asserts exact sequence after the optional
    # generated_at_utc_iso_z header (which we do not pass, so it is absent).
    "excel_summary_label_order": (
        "Manager scope",
        "Technology scope",
        "Window (days)",
        "Customers in portfolio",
        "Adoption barriers (total)",
        "Adoption barriers (critical)",
        "Adoption barriers (open)",
        "TAC cases (total)",
        "TAC cases (P1)",
        "TAC cases (open)",
        "Escalations",
        "BEMS / break-fix",
        "External bugs (rows)",
        "External incidents (rows)",
    ),
}


# Public re-exports for the test harness.
__all__: Sequence[str] = (
    "EXPECTED_KPIS",
    "make_ab_df",
    "make_csone_df",
    "make_pulse_df",
    "make_risk_profiles",
    "make_extra_frames",
    "make_all",
)
