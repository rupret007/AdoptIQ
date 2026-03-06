"""Shared data contracts for cross-report consistency payloads."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class PortfolioMetricsContract(TypedDict, total=False):
    total_customers: int
    total_barriers: int
    total_cases: int
    bems_count: int
    critical_p1: int
    high_p2: int
    p1_cases: int
    p2_cases: int
    p3_cases: int
    p4_cases: int
    unknown_priority_cases: int
    break_fix_cases: int
    provisioning_cases: int
    high_risk_customers: int
    medium_risk_customers: int
    low_risk_customers: int
    healthy_customers: int
    health_score: str
    trend_direction: str


class DefectsContract(TypedDict, total=False):
    csc_ids: List[str]
    bems_ids: List[str]
    defect_by_customer: Dict[str, List[str]]
    total_defects: int
    total_cases_with_defects: int


class ConsistencyResultContract(TypedDict):
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    metrics: Dict[str, Any]
