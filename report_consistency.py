"""Cross-report consistency checks and failsafes for AdoptIQ outputs."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from data_normalization import detect_bems_mask, normalize_customer_name


def _safe_count(df: Optional[pd.DataFrame]) -> int:
    return 0 if df is None or df.empty else len(df)


def validate_report_consistency(
    ab_df: Optional[pd.DataFrame],
    csone_df: Optional[pd.DataFrame],
    portfolio_metrics: Optional[Dict[str, Any]] = None,
    risk_data: Optional[Dict[str, Dict[str, Any]]] = None,
    defects: Optional[Dict[str, Any]] = None,
    max_other_unknown_ratio: float = 0.60,
) -> Dict[str, Any]:
    """
    Validate cross-report consistency and produce actionable diagnostics.
    This does not mutate inputs; callers can use returned canonical metrics.
    """
    errors = []
    warnings = []
    metrics: Dict[str, Any] = {}

    ab_count = _safe_count(ab_df)
    cs_count = _safe_count(csone_df)
    bems_count = int(detect_bems_mask(csone_df).sum()) if cs_count else 0

    metrics["total_barriers"] = ab_count
    metrics["total_cases"] = cs_count
    metrics["bems_count"] = bems_count

    # Adoption-barrier categorization leakage
    if ab_df is not None and not ab_df.empty and "sub_technology" in ab_df.columns:
        subtech = ab_df["sub_technology"].fillna("").astype(str).str.strip()
        unknown_count = int(subtech.str.contains(r"other/unknown|unknown", case=False, regex=True).sum())
        unknown_ratio = unknown_count / max(len(subtech), 1)
        metrics["other_unknown_count"] = unknown_count
        metrics["other_unknown_ratio"] = round(unknown_ratio, 4)
        if unknown_ratio > max_other_unknown_ratio:
            warnings.append(
                f"Adoption barrier Other/Unknown ratio is high ({unknown_ratio:.1%}); update technology mapping."
            )

    # Portfolio metric mismatch
    if portfolio_metrics:
        if int(portfolio_metrics.get("total_barriers", 0)) != ab_count:
            errors.append("Portfolio metric mismatch: total_barriers does not match normalized adoption barriers.")
        if int(portfolio_metrics.get("total_cases", 0)) != cs_count:
            errors.append("Portfolio metric mismatch: total_cases does not match normalized TAC cases.")
        if int(portfolio_metrics.get("bems_count", 0)) != bems_count:
            errors.append("Portfolio metric mismatch: bems_count does not match canonical BEMS detection.")

    # Risk data/customer totals coherence
    if risk_data is not None:
        metrics["risk_customers"] = len(risk_data)
        if len(risk_data) == 0 and (ab_count > 0 or cs_count > 0):
            warnings.append("Risk data is empty while source records exist; verify risk pipeline wiring.")

    # Defect linkage consistency
    if defects:
        defect_by_customer = defects.get("defect_by_customer", {}) or {}
        known_customers = set()
        for frame in (ab_df, csone_df):
            if frame is None or frame.empty:
                continue
            for col in ("customer_name", "BU_NAME", "Customer Name"):
                if col in frame.columns:
                    known_customers.update(
                        normalize_customer_name(v)
                        for v in frame[col].dropna().astype(str).tolist()
                    )
        unknown_defect_customers = [
            normalize_customer_name(name)
            for name in defect_by_customer.keys()
            if normalize_customer_name(name) not in known_customers
        ]
        if unknown_defect_customers:
            warnings.append(
                f"{len(unknown_defect_customers)} defect-customer entries are not in normalized customer set."
            )
        metrics["unknown_defect_customers"] = sorted(set(unknown_defect_customers))

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }

