"""Round 66 / Pass 4 - One-shot fixture generator for the Ask AI eval.

This module is intentionally NOT collected by pytest (no ``test_*``
prefix on functions, leading underscore on the file name). It runs
deterministically and emits:

- ``fixtures/portfolios/<id>/{ab,csone,pulse,sp,ap}.csv`` x 5 portfolios
- ``questions/<id>.yaml`` x 50 (10 per portfolio)
- ``cassettes/<id>.json`` x 50 (synthetic LLM responses)

Re-run after editing this file; outputs overwrite atomically. The
generator's hash-of-inputs is stamped into each output's frontmatter
so a stale generator + committed output drift can be caught in code
review.

Usage::

    python -m tests.ask_ai_eval._generate_fixtures

The cassettes simulate a well-behaved LLM that cites real IDs from the
fixture data. Pass 4 baseline pass rate is whatever lexical retrieval
manages with these cassettes; Pass 5 hybrid retrieval should beat it
by surfacing more semantically-relevant records into the allowed_ids
set so cassette citations survive validation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


_HERE = Path(__file__).resolve().parent
_FIXTURES = _HERE / "fixtures" / "portfolios"
_QUESTIONS = _HERE / "questions"
_CASSETTES = _HERE / "cassettes"

# Round 66 / Pass 4 - small, deterministic golden set. Customer / ID
# strings are intentionally distinctive ("EVAL" infix) so they cannot
# collide with any real customer data that may appear in unrelated
# tests.

# ---------- p01: renewal risk ----------------------------------------------

_P01_CUSTOMERS = [
    "AcmeEvalCorp", "BetaEvalInc", "GammaEvalCo", "DeltaEvalLLC",
    "EpsilonEvalGroup", "ZetaEvalPartners", "EtaEvalCorp", "ThetaEvalIndustries",
]

_P01_AB = [
    # ID, BU_NAME, SUBJECT_C, SEVERITY_C, STATUS_C, OPEN_DATE_C
    ("AB-EVAL-001", "AcmeEvalCorp", "Contract renewal at risk - low engagement",
     "High", "Open", "2026-01-15"),
    ("AB-EVAL-002", "AcmeEvalCorp", "Webex Meetings adoption stalled",
     "Medium", "Open", "2026-02-01"),
    ("AB-EVAL-003", "BetaEvalInc", "Renewal cycle blocked by procurement",
     "Critical", "Open", "2026-01-22"),
    ("AB-EVAL-004", "GammaEvalCo", "Customer pulse trending negative",
     "High", "Open", "2026-02-10"),
    ("AB-EVAL-005", "DeltaEvalLLC", "Active renewal negotiation",
     "Medium", "InProgress", "2025-12-05"),
    ("AB-EVAL-006", "EpsilonEvalGroup", "Churn risk - competitor evaluation",
     "Critical", "Open", "2026-01-30"),
    ("AB-EVAL-007", "ZetaEvalPartners", "Adoption metrics below threshold",
     "Medium", "Open", "2026-02-14"),
    ("AB-EVAL-008", "EtaEvalCorp", "Renewal pricing dispute",
     "High", "Open", "2026-02-18"),
    ("AB-EVAL-009", "ThetaEvalIndustries", "Champion changed roles",
     "Medium", "Open", "2026-02-20"),
    ("AB-EVAL-010", "AcmeEvalCorp", "Executive sponsor disengaged",
     "High", "Open", "2026-02-22"),
    ("AB-EVAL-011", "BetaEvalInc", "Webex Calling rollout delayed",
     "Medium", "Open", "2026-02-25"),
    ("AB-EVAL-012", "GammaEvalCo", "Renewal forecast revised down",
     "High", "Open", "2026-03-01"),
]

_P01_CASES = [
    # CASE_ID, BU_NAME, SUBJECT, SEVERITY, STATUS, OPEN_DATE
    ("CASE-EVAL-101", "AcmeEvalCorp", "Webex Meetings audio quality complaint",
     "P2", "Open", "2026-02-15"),
    ("CASE-EVAL-102", "BetaEvalInc", "Conference room device failure",
     "P1", "Open", "2026-02-20"),
    ("CASE-EVAL-103", "GammaEvalCo", "License provisioning delay",
     "P2", "Resolved", "2026-01-28"),
    ("CASE-EVAL-104", "DeltaEvalLLC", "Webex Calling outbound dial issue",
     "P2", "Open", "2026-02-22"),
    ("CASE-EVAL-105", "EpsilonEvalGroup", "SSO integration failure",
     "P1", "Open", "2026-02-18"),
    ("CASE-EVAL-106", "ZetaEvalPartners", "Recording playback intermittent",
     "P3", "Open", "2026-02-25"),
    ("CASE-EVAL-107", "EtaEvalCorp", "Webex Board firmware crash",
     "P2", "Open", "2026-02-26"),
    ("CASE-EVAL-108", "ThetaEvalIndustries", "Network latency complaint",
     "P3", "Open", "2026-02-28"),
]

_P01_PULSE = [
    # ID, CUSTOMER_NAME__C, SCORE__C, COMMENTS__C, LAST_MODIFIED_DATE
    ("PULSE-EVAL-001", "AcmeEvalCorp", 1.5,
     "Executive sponsor disengaged; renewal at risk", "2026-02-25"),
    ("PULSE-EVAL-002", "BetaEvalInc", 2.0,
     "Procurement delays affecting renewal timeline", "2026-02-22"),
    ("PULSE-EVAL-003", "GammaEvalCo", 2.5,
     "Mixed signals from champion", "2026-02-20"),
    ("PULSE-EVAL-004", "DeltaEvalLLC", 3.0, "Stable but cautious", "2026-02-18"),
    ("PULSE-EVAL-005", "EpsilonEvalGroup", 1.0,
     "Active churn risk - competitor in flight", "2026-02-26"),
    ("PULSE-EVAL-006", "ZetaEvalPartners", 3.5, "Above expected", "2026-02-15"),
    ("PULSE-EVAL-007", "EtaEvalCorp", 2.0,
     "Pricing concerns dominating", "2026-02-21"),
    ("PULSE-EVAL-008", "ThetaEvalIndustries", 2.5,
     "New champion needs onboarding", "2026-02-23"),
]

# ---------- p02: heavy PSIRT ------------------------------------------------

_P02_CUSTOMERS = [
    "SecureEvalCorp", "VaultEvalLLC", "ShieldEvalInc",
    "LockEvalSystems", "GuardEvalTech", "DefenseEvalLogix",
]

_P02_AB = [
    ("AB-EVAL-201", "SecureEvalCorp", "PSIRT remediation overdue",
     "Critical", "Open", "2026-01-10"),
    ("AB-EVAL-202", "VaultEvalLLC", "Webex Calling vulnerability patch pending",
     "High", "Open", "2026-01-25"),
    ("AB-EVAL-203", "ShieldEvalInc", "Security review blocking adoption",
     "Medium", "Open", "2026-02-05"),
    ("AB-EVAL-204", "LockEvalSystems", "PSIRT scan flagged 3 advisories",
     "High", "Open", "2026-02-12"),
    ("AB-EVAL-205", "GuardEvalTech", "Compliance deadline approaching",
     "Critical", "Open", "2026-02-15"),
    ("AB-EVAL-206", "DefenseEvalLogix", "Encryption upgrade in progress",
     "Medium", "InProgress", "2026-02-01"),
]

_P02_CASES = [
    ("CASE-EVAL-201", "SecureEvalCorp",
     "CSCwk12345 patch verification", "P1", "Open", "2026-02-10"),
    ("CASE-EVAL-202", "VaultEvalLLC",
     "CSCwk67890 mitigation guidance", "P2", "Open", "2026-02-12"),
    ("CASE-EVAL-203", "ShieldEvalInc",
     "Webex Calling SIP TLS issue", "P2", "Open", "2026-02-15"),
    ("CASE-EVAL-204", "LockEvalSystems",
     "PSIRT advisory cisco-sa-2026-001", "P1", "Open", "2026-02-18"),
    ("CASE-EVAL-205", "GuardEvalTech",
     "Webex Calling firmware vulnerability", "P1", "Open", "2026-02-20"),
    ("CASE-EVAL-206", "DefenseEvalLogix",
     "TLS 1.3 enforcement question", "P3", "Open", "2026-02-22"),
    ("CASE-EVAL-207", "SecureEvalCorp",
     "CSCwk24680 hotfix request", "P2", "Open", "2026-02-23"),
    ("CASE-EVAL-208", "VaultEvalLLC",
     "Audit trail export issue", "P3", "Open", "2026-02-24"),
    ("CASE-EVAL-209", "ShieldEvalInc",
     "Encryption key rotation question", "P2", "Open", "2026-02-25"),
    ("CASE-EVAL-210", "LockEvalSystems",
     "Webex Meetings DLP integration", "P3", "Open", "2026-02-26"),
    ("CASE-EVAL-211", "GuardEvalTech",
     "PSIRT response SLA inquiry", "P2", "Open", "2026-02-27"),
    ("CASE-EVAL-212", "DefenseEvalLogix",
     "FIPS validation status", "P3", "Open", "2026-02-28"),
    ("CASE-EVAL-213", "SecureEvalCorp",
     "Recording encryption verification", "P3", "Open", "2026-03-01"),
    ("CASE-EVAL-214", "VaultEvalLLC",
     "PSIRT remediation tracking", "P2", "Open", "2026-03-02"),
    ("CASE-EVAL-215", "ShieldEvalInc",
     "Compliance certificate renewal", "P3", "Open", "2026-03-03"),
]

_P02_PULSE = [
    ("PULSE-EVAL-201", "SecureEvalCorp", 2.0,
     "Multiple PSIRT items overdue", "2026-02-27"),
    ("PULSE-EVAL-202", "VaultEvalLLC", 2.5,
     "Patch backlog growing", "2026-02-25"),
    ("PULSE-EVAL-203", "ShieldEvalInc", 3.0,
     "Security review on track", "2026-02-23"),
    ("PULSE-EVAL-204", "LockEvalSystems", 2.0,
     "3 active CSC advisories", "2026-02-26"),
    ("PULSE-EVAL-205", "GuardEvalTech", 1.5,
     "Compliance deadline pressure", "2026-02-28"),
    ("PULSE-EVAL-206", "DefenseEvalLogix", 3.0,
     "Encryption upgrade progressing", "2026-02-22"),
]

# ---------- p03: adoption barriers, high volume -----------------------------

_P03_CUSTOMERS = [f"AdoptEvalCust{i:02d}" for i in range(1, 11)]

_P03_AB: List[Tuple[str, str, str, str, str, str]] = []
_AB_TOPICS = [
    "Meeting room device adoption", "Webex Calling pilot stalled",
    "User training backlog", "Champion turnover", "Mobile app rollout pending",
    "License utilization low", "Integration with Teams blocked",
    "Recording compliance gap", "SSO migration in flight",
    "Webex Board procurement delay",
]
for i, cust in enumerate(_P03_CUSTOMERS):
    for j in range(3):  # 3 ABs per customer = 30 total
        idx = i * 3 + j + 1
        topic = _AB_TOPICS[(i + j) % len(_AB_TOPICS)]
        sev = ["Medium", "High", "Critical"][j % 3]
        status = "Open" if j != 1 else "InProgress"
        date = f"2026-{(i % 3) + 1:02d}-{(j + 5):02d}"
        _P03_AB.append((f"AB-EVAL-3{idx:02d}", cust, topic, sev, status, date))

_P03_CASES = [
    ("CASE-EVAL-301", "AdoptEvalCust01",
     "Provisioning question", "P3", "Open", "2026-02-10"),
    ("CASE-EVAL-302", "AdoptEvalCust03",
     "License audit", "P3", "Open", "2026-02-12"),
    ("CASE-EVAL-303", "AdoptEvalCust05",
     "Training escalation", "P2", "Open", "2026-02-14"),
    ("CASE-EVAL-304", "AdoptEvalCust07",
     "Webex Calling registration", "P2", "Open", "2026-02-16"),
    ("CASE-EVAL-305", "AdoptEvalCust09",
     "Adoption dashboard request", "P3", "Open", "2026-02-18"),
]

_P03_PULSE = [
    (f"PULSE-EVAL-3{i:02d}", c, [3.0, 2.5, 3.5, 2.0, 2.5, 3.0, 2.0, 3.5, 2.5, 3.0][i - 1],
     f"Adoption velocity moderate for {c}", f"2026-02-{(i + 10):02d}")
    for i, c in enumerate(_P03_CUSTOMERS, start=1)
]

# ---------- p04: quiet portfolio (negative control) -------------------------

_P04_CUSTOMERS = ["SilentEvalCorp", "NullEvalInc", "VoidEvalLLC"]

_P04_AB = [
    ("AB-EVAL-401", "SilentEvalCorp", "Routine quarterly review",
     "Low", "Open", "2026-02-15"),
]
_P04_CASES: List[Tuple[str, str, str, str, str, str]] = []
_P04_PULSE: List[Tuple[str, str, float, str, str]] = []

# ---------- p05: cross-compare (4 directly comparable) ----------------------

_P05_CUSTOMERS = ["CmpEvalAlpha", "CmpEvalBeta", "CmpEvalGamma", "CmpEvalDelta"]

_P05_AB = [
    ("AB-EVAL-501", "CmpEvalAlpha", "Webex Calling rollout phase 2",
     "Medium", "InProgress", "2026-01-20"),
    ("AB-EVAL-502", "CmpEvalAlpha", "Mobile app adoption gap",
     "Medium", "Open", "2026-02-05"),
    ("AB-EVAL-503", "CmpEvalAlpha", "Champion identification needed",
     "Low", "Open", "2026-02-10"),
    ("AB-EVAL-504", "CmpEvalAlpha", "Reporting dashboard request",
     "Low", "Open", "2026-02-15"),
    ("AB-EVAL-505", "CmpEvalBeta", "Webex Calling SSO question",
     "Medium", "Open", "2026-01-25"),
    ("AB-EVAL-506", "CmpEvalBeta", "Recording retention policy",
     "Low", "Open", "2026-02-08"),
    ("AB-EVAL-507", "CmpEvalBeta", "Mobile rollout blocked by MDM",
     "High", "Open", "2026-02-12"),
    ("AB-EVAL-508", "CmpEvalBeta", "Champion change",
     "Medium", "Open", "2026-02-18"),
    ("AB-EVAL-509", "CmpEvalGamma", "Webex Calling phase 1 complete",
     "Low", "Closed", "2026-01-15"),
    ("AB-EVAL-510", "CmpEvalGamma", "Phase 2 planning underway",
     "Low", "Open", "2026-02-01"),
    ("AB-EVAL-511", "CmpEvalGamma", "Training session scheduled",
     "Low", "Open", "2026-02-10"),
    ("AB-EVAL-512", "CmpEvalGamma", "Adoption metrics on track",
     "Low", "Open", "2026-02-20"),
    ("AB-EVAL-513", "CmpEvalDelta", "Webex Calling deployment delayed",
     "Critical", "Open", "2026-01-18"),
    ("AB-EVAL-514", "CmpEvalDelta", "Procurement issues escalated",
     "High", "Open", "2026-02-02"),
    ("AB-EVAL-515", "CmpEvalDelta", "Champion availability concern",
     "Medium", "Open", "2026-02-14"),
    ("AB-EVAL-516", "CmpEvalDelta", "Renewal at risk",
     "High", "Open", "2026-02-22"),
]

_P05_CASES = [
    ("CASE-EVAL-501", "CmpEvalAlpha", "Audio quality", "P3", "Open", "2026-02-10"),
    ("CASE-EVAL-502", "CmpEvalAlpha", "License question", "P3", "Open", "2026-02-15"),
    ("CASE-EVAL-503", "CmpEvalAlpha", "Mobile setup", "P3", "Open", "2026-02-20"),
    ("CASE-EVAL-504", "CmpEvalBeta", "SSO config", "P2", "Open", "2026-02-08"),
    ("CASE-EVAL-505", "CmpEvalBeta", "Recording retention", "P3", "Open", "2026-02-15"),
    ("CASE-EVAL-506", "CmpEvalBeta", "MDM integration", "P2", "Open", "2026-02-22"),
    ("CASE-EVAL-507", "CmpEvalGamma", "Phase 2 planning question",
     "P3", "Open", "2026-02-12"),
    ("CASE-EVAL-508", "CmpEvalGamma", "Training material request",
     "P3", "Open", "2026-02-18"),
    ("CASE-EVAL-509", "CmpEvalGamma", "Adoption report",
     "P3", "Open", "2026-02-25"),
    ("CASE-EVAL-510", "CmpEvalDelta", "Deployment escalation",
     "P1", "Open", "2026-02-05"),
    ("CASE-EVAL-511", "CmpEvalDelta", "Procurement followup",
     "P2", "Open", "2026-02-15"),
    ("CASE-EVAL-512", "CmpEvalDelta", "Renewal pricing inquiry",
     "P2", "Open", "2026-02-22"),
]

_P05_PULSE = [
    ("PULSE-EVAL-501", "CmpEvalAlpha", 3.5, "On track", "2026-02-22"),
    ("PULSE-EVAL-502", "CmpEvalBeta", 3.0, "Stable with concerns", "2026-02-21"),
    ("PULSE-EVAL-503", "CmpEvalGamma", 4.0, "Best in cohort", "2026-02-23"),
    ("PULSE-EVAL-504", "CmpEvalDelta", 2.0, "Renewal risk", "2026-02-25"),
]


# ---------------------------------------------------------------------------
# Question authoring (10 per portfolio across 8 categories)
# ---------------------------------------------------------------------------


def _q(qid: str, portfolio: str, category: str, question: str,
       predicates: List[Dict[str, Any]],
       expected_evidence_ids: List[str]) -> Dict[str, Any]:
    return {
        "id": qid,
        "portfolio": portfolio,
        "category": category,
        "question": question,
        "predicates": predicates,
        "expected_evidence_ids": expected_evidence_ids,
    }


_QUESTIONS_DATA: List[Dict[str, Any]] = [
    # ---------- p01 (renewal risk) -----------------------------------------
    _q("p01_q01", "p01_high_renewal_risk", "kpi_extraction",
       "How many adoption barriers are open in this portfolio?",
       [{"type": "must_render_number_within_tolerance", "value": 12, "tolerance_pct": 10},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-001", "AB-EVAL-003"]),
    _q("p01_q02", "p01_high_renewal_risk", "customer_lookup",
       "Which customer has the lowest pulse score?",
       [{"type": "must_contain_phrase", "phrase": "EpsilonEvalGroup"},
        {"type": "must_cite_source_id", "expected_id": "PULSE-EVAL-005"}],
       ["PULSE-EVAL-005"]),
    _q("p01_q03", "p01_high_renewal_risk", "citation_correctness",
       "What is the AcmeEvalCorp executive sponsor barrier ID?",
       [{"type": "must_cite_source_id", "expected_id": "AB-EVAL-010"},
        {"type": "must_contain_phrase", "phrase": "AB-EVAL-010"}],
       ["AB-EVAL-010"]),
    _q("p01_q04", "p01_high_renewal_risk", "kpi_extraction",
       "How many P1 cases are open in this portfolio?",
       [{"type": "must_render_number_within_tolerance", "value": 2, "tolerance_pct": 50},
        {"type": "must_not_render_pii", "pii_patterns": ["@cisco.com"]}],
       ["CASE-EVAL-102", "CASE-EVAL-105"]),
    _q("p01_q05", "p01_high_renewal_risk", "cross_compare",
       "Compare BetaEvalInc and GammaEvalCo by AB count.",
       [{"type": "must_contain_phrase", "phrase": "BetaEvalInc"},
        {"type": "must_contain_phrase", "phrase": "GammaEvalCo"},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-003", "AB-EVAL-004", "AB-EVAL-011", "AB-EVAL-012"]),
    _q("p01_q06", "p01_high_renewal_risk", "multi_step",
       "What is the average pulse score across all customers?",
       [{"type": "must_render_number_within_tolerance", "value": 2.25, "tolerance_pct": 25}],
       ["PULSE-EVAL-001", "PULSE-EVAL-005"]),
    _q("p01_q07", "p01_high_renewal_risk", "negative_control",
       "Were there any incidents reported in the last 7 days?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p01_q08", "p01_high_renewal_risk", "time_bounded",
       "Which barriers were opened in February 2026?",
       [{"type": "must_cite_source_id", "expected_id": "any"},
        {"type": "must_contain_phrase", "phrase": "AB-EVAL"}],
       ["AB-EVAL-002", "AB-EVAL-004", "AB-EVAL-006", "AB-EVAL-007"]),
    _q("p01_q09", "p01_high_renewal_risk", "customer_lookup",
       "Which case is escalated to P1 for SSO?",
       [{"type": "must_cite_source_id", "expected_id": "CASE-EVAL-105"}],
       ["CASE-EVAL-105"]),
    _q("p01_q10", "p01_high_renewal_risk", "psirt_exposure",
       "Are there any PSIRT-related barriers in this portfolio?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),

    # ---------- p02 (heavy PSIRT) ------------------------------------------
    _q("p02_q01", "p02_heavy_psirt", "psirt_exposure",
       "How many PSIRT-related cases are open?",
       [{"type": "must_render_number_within_tolerance", "value": 4, "tolerance_pct": 30},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["CASE-EVAL-201", "CASE-EVAL-202", "CASE-EVAL-204"]),
    _q("p02_q02", "p02_heavy_psirt", "citation_correctness",
       "What is the case ID for the SecureEvalCorp CSCwk12345 patch verification?",
       [{"type": "must_cite_source_id", "expected_id": "CASE-EVAL-201"},
        {"type": "must_contain_phrase", "phrase": "CASE-EVAL-201"}],
       ["CASE-EVAL-201"]),
    _q("p02_q03", "p02_heavy_psirt", "customer_lookup",
       "Which customer has the lowest pulse score in the PSIRT portfolio?",
       [{"type": "must_contain_phrase", "phrase": "GuardEvalTech"}],
       ["PULSE-EVAL-205"]),
    _q("p02_q04", "p02_heavy_psirt", "kpi_extraction",
       "How many adoption barriers are open in this portfolio?",
       [{"type": "must_render_number_within_tolerance", "value": 6, "tolerance_pct": 20}],
       ["AB-EVAL-201"]),
    _q("p02_q05", "p02_heavy_psirt", "kpi_extraction",
       "How many P1 cases are open?",
       [{"type": "must_render_number_within_tolerance", "value": 3, "tolerance_pct": 30},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["CASE-EVAL-201", "CASE-EVAL-204", "CASE-EVAL-205"]),
    _q("p02_q06", "p02_heavy_psirt", "multi_step",
       "Identify customers with both an Open AB and an Open P1 case.",
       [{"type": "must_contain_phrase", "phrase": "SecureEvalCorp"},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-201", "CASE-EVAL-201"]),
    _q("p02_q07", "p02_heavy_psirt", "time_bounded",
       "Which cases opened in late February 2026?",
       [{"type": "must_cite_source_id", "expected_id": "any"}],
       ["CASE-EVAL-207", "CASE-EVAL-208"]),
    _q("p02_q08", "p02_heavy_psirt", "negative_control",
       "What is the renewal forecast for VaultEvalLLC?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p02_q09", "p02_heavy_psirt", "customer_lookup",
       "Which customer's case mentions cisco-sa-2026-001?",
       [{"type": "must_contain_phrase", "phrase": "LockEvalSystems"},
        {"type": "must_cite_source_id", "expected_id": "CASE-EVAL-204"}],
       ["CASE-EVAL-204"]),
    _q("p02_q10", "p02_heavy_psirt", "cross_compare",
       "Compare SecureEvalCorp vs ShieldEvalInc by case volume.",
       [{"type": "must_contain_phrase", "phrase": "SecureEvalCorp"},
        {"type": "must_contain_phrase", "phrase": "ShieldEvalInc"}],
       ["CASE-EVAL-201", "CASE-EVAL-203"]),

    # ---------- p03 (adoption barriers, high volume) -----------------------
    _q("p03_q01", "p03_adoption_barriers", "kpi_extraction",
       "How many adoption barriers are open in this portfolio?",
       [{"type": "must_render_number_within_tolerance", "value": 30, "tolerance_pct": 20},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-301"]),
    _q("p03_q02", "p03_adoption_barriers", "customer_lookup",
       "Which customer has a Webex Calling pilot stalled?",
       [{"type": "must_contain_phrase", "phrase": "AdoptEvalCust"},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-302"]),
    _q("p03_q03", "p03_adoption_barriers", "kpi_extraction",
       "How many critical-severity barriers are open?",
       [{"type": "must_render_number_within_tolerance", "value": 10, "tolerance_pct": 30}],
       ["AB-EVAL-303"]),
    _q("p03_q04", "p03_adoption_barriers", "citation_correctness",
       "Which barrier ID covers the SSO migration in flight?",
       [{"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-309"]),
    _q("p03_q05", "p03_adoption_barriers", "cross_compare",
       "Compare AdoptEvalCust01 and AdoptEvalCust05 by AB count.",
       [{"type": "must_contain_phrase", "phrase": "AdoptEvalCust01"},
        {"type": "must_contain_phrase", "phrase": "AdoptEvalCust05"}],
       ["AB-EVAL-301", "AB-EVAL-313"]),
    _q("p03_q06", "p03_adoption_barriers", "multi_step",
       "Which customer has both a Critical AB and a Pulse score below 3?",
       [{"type": "must_contain_phrase", "phrase": "AdoptEvalCust"}],
       ["AB-EVAL-303", "PULSE-EVAL-304"]),
    _q("p03_q07", "p03_adoption_barriers", "time_bounded",
       "Which ABs were opened in March 2026?",
       [{"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-330"]),
    _q("p03_q08", "p03_adoption_barriers", "negative_control",
       "How many P1 cases are open in this portfolio?",
       [{"type": "must_render_number_within_tolerance", "value": 0, "tolerance_pct": 100}],
       []),
    _q("p03_q09", "p03_adoption_barriers", "psirt_exposure",
       "Are there any PSIRT-related barriers in this portfolio?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p03_q10", "p03_adoption_barriers", "kpi_extraction",
       "What is the average pulse score across all customers?",
       [{"type": "must_render_number_within_tolerance", "value": 2.75, "tolerance_pct": 25}],
       ["PULSE-EVAL-301"]),

    # ---------- p04 (quiet portfolio - negative controls) ------------------
    _q("p04_q01", "p04_quiet_portfolio", "kpi_extraction",
       "How many adoption barriers are open in this portfolio?",
       [{"type": "must_render_number_within_tolerance", "value": 1, "tolerance_pct": 50},
        {"type": "must_cite_source_id", "expected_id": "AB-EVAL-401"}],
       ["AB-EVAL-401"]),
    _q("p04_q02", "p04_quiet_portfolio", "negative_control",
       "How many support cases are open?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p04_q03", "p04_quiet_portfolio", "negative_control",
       "What is the average pulse score?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p04_q04", "p04_quiet_portfolio", "customer_lookup",
       "Which customer has the only open barrier?",
       [{"type": "must_contain_phrase", "phrase": "SilentEvalCorp"}],
       ["AB-EVAL-401"]),
    _q("p04_q05", "p04_quiet_portfolio", "negative_control",
       "Are there any PSIRT cases in this portfolio?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p04_q06", "p04_quiet_portfolio", "negative_control",
       "Which customer churned this quarter?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p04_q07", "p04_quiet_portfolio", "citation_correctness",
       "What is the source ID for the SilentEvalCorp barrier?",
       [{"type": "must_cite_source_id", "expected_id": "AB-EVAL-401"}],
       ["AB-EVAL-401"]),
    _q("p04_q08", "p04_quiet_portfolio", "negative_control",
       "What is the renewal pipeline value for this portfolio?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p04_q09", "p04_quiet_portfolio", "negative_control",
       "Identify any customers with multiple open barriers.",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
    _q("p04_q10", "p04_quiet_portfolio", "kpi_extraction",
       "What is the AB severity for SilentEvalCorp?",
       [{"type": "must_contain_phrase", "phrase": "Low"},
        {"type": "must_cite_source_id", "expected_id": "AB-EVAL-401"}],
       ["AB-EVAL-401"]),

    # ---------- p05 (cross-compare) ----------------------------------------
    _q("p05_q01", "p05_cross_compare", "cross_compare",
       "Compare CmpEvalAlpha and CmpEvalBeta by AB count.",
       [{"type": "must_contain_phrase", "phrase": "CmpEvalAlpha"},
        {"type": "must_contain_phrase", "phrase": "CmpEvalBeta"},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-501", "AB-EVAL-505"]),
    _q("p05_q02", "p05_cross_compare", "kpi_extraction",
       "How many adoption barriers are open across the comparable cohort?",
       [{"type": "must_render_number_within_tolerance", "value": 16, "tolerance_pct": 20},
        {"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-501"]),
    _q("p05_q03", "p05_cross_compare", "customer_lookup",
       "Which customer has the highest AB count?",
       [{"type": "must_contain_phrase", "phrase": "CmpEval"}],
       ["AB-EVAL-501", "AB-EVAL-505", "AB-EVAL-509", "AB-EVAL-513"]),
    _q("p05_q04", "p05_cross_compare", "customer_lookup",
       "Which customer has the highest pulse score?",
       [{"type": "must_contain_phrase", "phrase": "CmpEvalGamma"},
        {"type": "must_cite_source_id", "expected_id": "PULSE-EVAL-503"}],
       ["PULSE-EVAL-503"]),
    _q("p05_q05", "p05_cross_compare", "customer_lookup",
       "Which customer has the lowest pulse score?",
       [{"type": "must_contain_phrase", "phrase": "CmpEvalDelta"},
        {"type": "must_cite_source_id", "expected_id": "PULSE-EVAL-504"}],
       ["PULSE-EVAL-504"]),
    _q("p05_q06", "p05_cross_compare", "kpi_extraction",
       "How many P1 cases are open in the cross-compare cohort?",
       [{"type": "must_render_number_within_tolerance", "value": 1, "tolerance_pct": 50}],
       ["CASE-EVAL-510"]),
    _q("p05_q07", "p05_cross_compare", "citation_correctness",
       "What is the source ID for the CmpEvalDelta deployment escalation?",
       [{"type": "must_cite_source_id", "expected_id": "CASE-EVAL-510"},
        {"type": "must_contain_phrase", "phrase": "CASE-EVAL-510"}],
       ["CASE-EVAL-510"]),
    _q("p05_q08", "p05_cross_compare", "multi_step",
       "Which customer has Webex Calling phase 2 in progress and a pulse score above 3?",
       [{"type": "must_contain_phrase", "phrase": "CmpEvalAlpha"}],
       ["AB-EVAL-501", "PULSE-EVAL-501"]),
    _q("p05_q09", "p05_cross_compare", "time_bounded",
       "Which barriers were opened in late February 2026?",
       [{"type": "must_cite_source_id", "expected_id": "any"}],
       ["AB-EVAL-512", "AB-EVAL-515", "AB-EVAL-516"]),
    _q("p05_q10", "p05_cross_compare", "psirt_exposure",
       "Are there any PSIRT-tagged cases in this cohort?",
       [{"type": "must_contain_phrase", "phrase": "Insufficient"}],
       []),
]


# ---------------------------------------------------------------------------
# Cassette synthesis (synthetic LLM responses)
# ---------------------------------------------------------------------------


def _build_cassette(question: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize an LLM-shaped JSON response for the question.

    The cassette references the question's expected_evidence_ids so a
    well-functioning retrieval surfaces those IDs and the citations
    survive ``compose_grounded_answer``'s validation. Missing IDs are
    suppressed by the production composer (rejected count goes up,
    must_cite_source_id predicates fail).

    Negative-control questions get an empty cassette so the composer
    falls back to the "Insufficient grounded evidence" prose, which
    is exactly what the predicate expects.
    """
    cat = question.get("category", "")
    expected_ids = list(question.get("expected_evidence_ids") or [])
    qtext = question.get("question", "")
    predicates = list(question.get("predicates") or [])
    # Round 66 / Pass 4 - "Insufficient" is the composer's fallback
    # prose when no claims survive validation. Cassettes for these
    # questions deliberately ship empty so the composer's fallback path
    # is the one that satisfies the must_contain_phrase predicate.
    expects_insufficient = any(
        p.get("type") == "must_contain_phrase" and "Insufficient" in str(p.get("phrase", ""))
        for p in predicates
    )
    if expects_insufficient:
        return {"executive_summary": "", "claims": [], "actions": [], "unknowns": []}
    # Build claims using the first expected id; the composer requires
    # at least one citation per claim.
    claims: List[Dict[str, Any]] = []
    if expected_ids:
        # First claim: cite up to 3 expected IDs to maximize must_cite_source_id matches
        claims.append({
            "statement": _summary_for(question, expected_ids),
            "citations": expected_ids[:3],
        })
    # Numeric value claims for KPI extraction
    for p in predicates:
        if p.get("type") == "must_render_number_within_tolerance":
            value = p.get("value")
            try:
                value_int = int(round(float(value)))
            except (TypeError, ValueError):
                continue
            citations = expected_ids[:1] or ["AB-EVAL-001"]
            claims.append({
                "statement": f"Total of {value_int} relevant records identified.",
                "citations": citations,
            })
            break
    summary = _summary_for(question, expected_ids)
    return {
        "executive_summary": summary,
        "claims": claims,
        "actions": [],
        "unknowns": [],
    }


def _phrases_required(question: Dict[str, Any]) -> List[str]:
    """Collect every must_contain_phrase string (excluding "Insufficient",
    which is the negative-control sentinel used by the composer's
    fallback prose)."""
    out: List[str] = []
    for p in question.get("predicates") or []:
        if p.get("type") != "must_contain_phrase":
            continue
        phrase = str(p.get("phrase", "")).strip()
        if phrase and phrase != "Insufficient":
            out.append(phrase)
    return out


def _summary_for(question: Dict[str, Any], expected_ids: List[str]) -> str:
    qtext = question.get("question", "")
    cat = question.get("category", "")
    phrases = _phrases_required(question)
    # KPI numeric value (single, used to seed the summary number for
    # kpi_extraction / negative-control-with-zero questions).
    numeric_value: int | None = None
    for p in question.get("predicates") or []:
        if p.get("type") == "must_render_number_within_tolerance":
            try:
                numeric_value = int(round(float(p.get("value"))))
            except (TypeError, ValueError):
                numeric_value = None
            break
    # Round 66 / Pass 4 - every category branch that has a phrase
    # requirement weaves the phrase into the summary so the synthetic
    # baseline is a clean 100%. Real-LLM cassettes (operator-recorded)
    # will produce a more variable baseline; that's the meaningful
    # "lexical vs hybrid" comparison surface.
    if cat == "customer_lookup" and phrases:
        return (
            f"Based on the available evidence, {phrases[0]} is the customer "
            f"matching the query: {qtext}"
        )
    if cat == "cross_compare" and phrases:
        return (
            f"Comparison across {', '.join(phrases)} based on the retrieved evidence."
        )
    if cat == "multi_step" and phrases:
        return (
            f"Multi-step analysis identifies {phrases[0]} as the matching customer "
            f"based on the cross-referenced evidence."
        )
    if cat == "kpi_extraction":
        # Honor must_contain_phrase first (e.g. "Low" for severity), then
        # numeric. Both branches below run if both predicates exist.
        parts: List[str] = []
        if phrases:
            parts.append(f"Severity: {phrases[0]}.")
        if numeric_value is not None:
            parts.append(f"Identified {numeric_value} relevant records based on the evidence.")
        if parts:
            return " ".join(parts)
    if cat == "negative_control" and numeric_value is not None:
        # e.g. "How many P1 cases?" expecting 0 in a portfolio with no
        # P1 cases. Render the number explicitly so the predicate sees it.
        return f"There are {numeric_value} matching records in this portfolio."
    if cat == "citation_correctness" and expected_ids:
        return f"The relevant source ID is {expected_ids[0]}."
    if cat == "time_bounded":
        return "Records matching the requested time window are listed below."
    if cat == "psirt_exposure":
        return "PSIRT-related records identified in the retrieved evidence."
    return "Analysis based on the retrieved evidence."


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, header: List[str], rows: List[Tuple[Any, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Tiny YAML writer matching the runner's reader."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for key in ("id", "portfolio", "category", "question"):
        val = data.get(key, "")
        lines.append(f"{key}: {_yaml_scalar(val)}")
    if data.get("predicates"):
        lines.append("predicates:")
        for p in data["predicates"]:
            first = True
            for k, v in p.items():
                prefix = "  - " if first else "    "
                lines.append(f"{prefix}{k}: {_yaml_scalar(v)}")
                first = False
    if data.get("expected_evidence_ids"):
        lines.append("expected_evidence_ids:")
        for eid in data["expected_evidence_ids"]:
            lines.append(f"  - {_yaml_scalar(eid)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]"
    s = str(v)
    if any(ch in s for ch in (":", "#", '"', "[", "]", "{", "}")):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if s and (s[0].isdigit() or s in {"true", "false", "null"}):
        return f'"{s}"'
    return s


def _hash_inputs() -> str:
    """Hash the source code of this generator; stamped into outputs."""
    src = Path(__file__).read_text(encoding="utf-8")
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]


_AB_HEADER = ["ID", "BU_NAME", "SUBJECT_C", "SEVERITY_C", "STATUS_C", "OPEN_DATE_C"]
_CASE_HEADER = ["CASE_ID", "BU_NAME", "SUBJECT", "SEVERITY", "STATUS", "OPEN_DATE"]
_PULSE_HEADER = ["ID", "CUSTOMER_NAME__C", "SCORE__C", "COMMENTS__C", "LAST_MODIFIED_DATE"]


def write_all() -> Dict[str, int]:
    """Write all fixtures + questions + cassettes. Returns counts."""
    portfolios = [
        ("p01_high_renewal_risk", _P01_AB, _P01_CASES, _P01_PULSE),
        ("p02_heavy_psirt", _P02_AB, _P02_CASES, _P02_PULSE),
        ("p03_adoption_barriers", _P03_AB, _P03_CASES, _P03_PULSE),
        ("p04_quiet_portfolio", _P04_AB, _P04_CASES, _P04_PULSE),
        ("p05_cross_compare", _P05_AB, _P05_CASES, _P05_PULSE),
    ]
    counts: Dict[str, int] = {"portfolios": 0, "questions": 0, "cassettes": 0}
    for pid, ab_rows, case_rows, pulse_rows in portfolios:
        base = _FIXTURES / pid
        _write_csv(base / "ab.csv", _AB_HEADER, ab_rows)
        _write_csv(base / "csone.csv", _CASE_HEADER, case_rows)
        _write_csv(base / "pulse.csv", _PULSE_HEADER, pulse_rows)
        # Empty SP / AP fixtures so the runner does not error reading them.
        _write_csv(base / "sp.csv", ["ID", "RELATED_CUSTOMER__C", "SUBJECT_C",
                                     "STATUS_C", "OPEN_DATE_C"], [])
        _write_csv(base / "ap.csv", ["ID", "CUSTOMER_BU_NAME__C", "SUBJECT_C",
                                     "STATUS_C", "OPEN_DATE_C"], [])
        counts["portfolios"] += 1
    _CASSETTES.mkdir(parents=True, exist_ok=True)
    for q in _QUESTIONS_DATA:
        qid = q["id"]
        _write_yaml(_QUESTIONS / f"{qid}.yaml", q)
        cassette = {
            "question_id": qid,
            "recorded_at": "synthetic-2026-05-02",
            "prompt_hash": "",  # empty hash disables strict-hash check (synthetic baseline)
            "response": _build_cassette(q),
        }
        path = _CASSETTES / f"{qid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cassette, indent=2, sort_keys=True), encoding="utf-8")
        counts["questions"] += 1
        counts["cassettes"] += 1
    # Marker file so a stale generator re-run is visible in `git status`.
    marker = _HERE / "_generated_at.json"
    marker.write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "generator_hash": _hash_inputs(),
                "counts": counts,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return counts


if __name__ == "__main__":
    counts = write_all()
    print(json.dumps(counts, indent=2))
