"""Cross-report consistency checks and failsafes for AdoptIQ outputs."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

import pandas as pd

from data_normalization import (
    ACCOUNT_COLUMN_CANDIDATES,
    detect_bems_mask,
    normalize_customer_name,
    normalize_priority_label,
)


def _safe_count(df: Optional[pd.DataFrame]) -> int:
    return 0 if df is None or df.empty else len(df)


def _missing_inline_source_claims(factual_claims: Optional[list]) -> list:
    if not factual_claims:
        return []
    missing = []
    for claim in factual_claims:
        text = str(claim or "").strip()
        if not text:
            continue
        if not re.search(r"\[\s*source\s*:", text, flags=re.IGNORECASE):
            missing.append(text)
    return missing


def _normalize_account_id_token(value: Any) -> str:
    token = str(value or "").strip().upper()
    if not token or token in {"NONE", "NAN", "NULL"}:
        return ""
    return token


def _account_token_sets(values: Optional[list]) -> tuple[set[str], set[str]]:
    exact: set[str] = set()
    sf15: set[str] = set()
    for value in values or []:
        token = _normalize_account_id_token(value)
        if not token:
            continue
        exact.add(token)
        if len(token) >= 15:
            sf15.add(token[:15])
    return exact, sf15


def validate_report_consistency(
    ab_df: Optional[pd.DataFrame],
    csone_df: Optional[pd.DataFrame],
    portfolio_metrics: Optional[Dict[str, Any]] = None,
    risk_data: Optional[Dict[str, Dict[str, Any]]] = None,
    defects: Optional[Dict[str, Any]] = None,
    factual_claims: Optional[list] = None,
    customer_universe: Optional[Any] = None,
    max_other_unknown_ratio: float = 0.60,
    customer_pulse_df: Optional[pd.DataFrame] = None,
    expected_account_ids: Optional[list] = None,
    pulse_coverage_warn_threshold: float = 0.50,
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
    metrics["total_customers"] = 0
    metrics["critical_p1"] = 0
    metrics["high_p2"] = 0
    metrics["customer_pulse_records"] = _safe_count(customer_pulse_df)

    # Canonical dashboard metrics (customer/case-severity parity checks)
    customer_set = set()
    if customer_universe is not None:
        if isinstance(customer_universe, pd.DataFrame):
            for col in ("customer_name", "BU_NAME", "Customer Name"):
                if col in customer_universe.columns:
                    customer_set.update(
                        normalize_customer_name(v) for v in customer_universe[col].dropna().astype(str).tolist()
                    )
        else:
            try:
                for value in customer_universe:
                    customer_set.add(normalize_customer_name(value))
            except TypeError:
                customer_set.add(normalize_customer_name(customer_universe))
    else:
        for frame in (ab_df, csone_df):
            if frame is None or frame.empty:
                continue
            for col in ("customer_name", "BU_NAME", "Customer Name"):
                if col in frame.columns:
                    customer_set.update(
                        normalize_customer_name(v) for v in frame[col].dropna().astype(str).tolist()
                    )
    customer_set = {c for c in customer_set if c and c != "Unknown"}
    metrics["total_customers"] = len(customer_set)

    if csone_df is not None and not csone_df.empty:
        if "case_priority_norm" in csone_df.columns:
            sev_series = csone_df["case_priority_norm"].fillna("").astype(str)
        else:
            sev_col = next((c for c in ("Severity", "Highest Priority", "Priority") if c in csone_df.columns), None)
            sev_series = (
                csone_df[sev_col].fillna("").astype(str).apply(normalize_priority_label)
                if sev_col
                else pd.Series(dtype=str)
            )
        metrics["critical_p1"] = int((sev_series == "P1").sum())
        metrics["high_p2"] = int((sev_series == "P2").sum())

    # Adoption-barrier categorization leakage
    if ab_df is not None and not ab_df.empty and "sub_technology" in ab_df.columns:
        subtech = ab_df["sub_technology"].fillna("").astype(str).str.strip()
        unknown_count = int(subtech.str.contains(r"other/unknown|unknown", case=False, regex=True).sum())
        unknown_ratio = unknown_count / max(len(subtech), 1)
        metrics["other_unknown_count"] = unknown_count
        metrics["other_unknown_ratio"] = round(unknown_ratio, 4)
        if unknown_ratio > max_other_unknown_ratio:
            tech_signal_cols = [
                col
                for col in (
                    "SUB_TECHNOLOGY_C",
                    "TECHNOLOGY_C",
                    "CSS_PRE_UNLINK_TECHNOLOGY_NAME_C",
                    "PRODUCT_NAME_C",
                    "PRODUCT_C",
                )
                if col in ab_df.columns
            ]
            if tech_signal_cols:
                signal_mask = pd.Series(False, index=ab_df.index)
                for col in tech_signal_cols:
                    signal_mask = signal_mask | ab_df[col].fillna("").astype(str).str.strip().ne("")
                signal_ratio = float(signal_mask.mean()) if len(signal_mask) else 0.0
                metrics["technology_signal_ratio"] = round(signal_ratio, 4)
                # When source technology columns are mostly empty, this warning is noisy and non-actionable.
                if signal_ratio >= 0.25:
                    warnings.append(
                        f"Adoption barrier Other/Unknown ratio is high ({unknown_ratio:.1%}); update technology mapping."
                    )
            else:
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
        if "total_customers" in portfolio_metrics and int(portfolio_metrics.get("total_customers", 0)) != metrics["total_customers"]:
            errors.append("Portfolio metric mismatch: total_customers does not match normalized customer universe.")
        if "critical_p1" in portfolio_metrics and int(portfolio_metrics.get("critical_p1", 0)) != metrics["critical_p1"]:
            errors.append("Portfolio metric mismatch: critical_p1 does not match canonical severity counting.")
        if "high_p2" in portfolio_metrics and int(portfolio_metrics.get("high_p2", 0)) != metrics["high_p2"]:
            errors.append("Portfolio metric mismatch: high_p2 does not match canonical severity counting.")

    # Risk data/customer totals coherence
    if risk_data is not None:
        metrics["risk_customers"] = len(risk_data)
        if len(risk_data) == 0 and (ab_count > 0 or cs_count > 0):
            warnings.append("Risk data is empty while source records exist; verify risk pipeline wiring.")

    # Customer pulse account coverage diagnostics (warning-only guardrail).
    if expected_account_ids is not None:
        expected_exact, expected_sf15 = _account_token_sets(expected_account_ids)
        metrics["pulse_expected_accounts"] = len(expected_exact)
        if customer_pulse_df is not None and not customer_pulse_df.empty and expected_exact:
            pulse_acct_col = next(
                (c for c in ACCOUNT_COLUMN_CANDIDATES if c in customer_pulse_df.columns),
                None,
            )
            if pulse_acct_col:
                backfill_mask = pd.Series([False] * len(customer_pulse_df), index=customer_pulse_df.index)
                if "PULSE_BACKFILL" in customer_pulse_df.columns:
                    backfill_mask = (
                        customer_pulse_df["PULSE_BACKFILL"]
                        .fillna("")
                        .astype(str)
                        .str.strip()
                        .str.lower()
                        .isin({"1", "true", "yes", "on"})
                    )
                in_window_df = customer_pulse_df[~backfill_mask]
                backfill_df = customer_pulse_df[backfill_mask]

                observed_exact, observed_sf15 = _account_token_sets(
                    customer_pulse_df[pulse_acct_col].dropna().astype(str).tolist()
                )
                observed_in_window_exact, observed_in_window_sf15 = _account_token_sets(
                    in_window_df[pulse_acct_col].dropna().astype(str).tolist()
                )
                observed_backfill_exact, observed_backfill_sf15 = _account_token_sets(
                    backfill_df[pulse_acct_col].dropna().astype(str).tolist()
                )

                matched_in_window_exact = expected_exact & observed_in_window_exact
                matched_in_window_sf15 = {
                    token for token in (expected_exact - matched_in_window_exact)
                    if len(token) >= 15 and token[:15] in observed_in_window_sf15
                }
                matched_in_window_total = matched_in_window_exact | matched_in_window_sf15

                remaining_after_in_window = expected_exact - matched_in_window_total
                matched_backfill_exact = remaining_after_in_window & observed_backfill_exact
                matched_backfill_sf15 = {
                    token for token in (remaining_after_in_window - matched_backfill_exact)
                    if len(token) >= 15 and token[:15] in observed_backfill_sf15
                }
                matched_backfill_total = matched_backfill_exact | matched_backfill_sf15
                matched_total = matched_in_window_total | matched_backfill_total

                in_window_coverage = len(matched_in_window_total) / max(len(expected_exact), 1)
                total_coverage = len(matched_total) / max(len(expected_exact), 1)
                metrics["pulse_observed_accounts"] = len(observed_exact)
                metrics["pulse_matched_accounts"] = len(matched_total)
                metrics["pulse_coverage"] = round(total_coverage, 4)
                metrics["pulse_in_window_accounts_matched"] = len(matched_in_window_total)
                metrics["pulse_backfill_accounts_matched"] = len(matched_backfill_total)
                metrics["pulse_total_coverage"] = round(total_coverage, 4)
                metrics["pulse_in_window_coverage"] = round(in_window_coverage, 4)
                if total_coverage < pulse_coverage_warn_threshold:
                    warnings.append(
                        f"Customer Pulse account coverage is low ({total_coverage:.1%}); validate account mapping and scope filters."
                    )
                elif in_window_coverage < pulse_coverage_warn_threshold and len(matched_backfill_total) > 0:
                    warnings.append(
                        f"Customer Pulse in-window coverage is low ({in_window_coverage:.1%}) but latest-known backfill raised total coverage to {total_coverage:.1%}; review data freshness."
                    )
            else:
                warnings.append("Customer Pulse data missing account ID column for coverage diagnostics.")

    # Defect linkage consistency
    if defects:
        defect_ids = set(str(x).strip().upper() for x in (defects.get("csc_ids", []) or []) if str(x).strip())
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
        linked_ids = set()
        for values in defect_by_customer.values():
            for defect_id in values or []:
                if str(defect_id).strip():
                    linked_ids.add(str(defect_id).strip().upper())
        unlinked_defects = sorted(defect_ids - linked_ids)
        metrics["unlinked_defects"] = unlinked_defects
        if unlinked_defects:
            warnings.append(
                f"{len(unlinked_defects)} defect ID(s) are missing customer linkage."
            )

    # Inline source attribution coverage
    missing_sources = _missing_inline_source_claims(factual_claims)
    metrics["missing_inline_sources_count"] = len(missing_sources)
    if missing_sources:
        errors.append(
            f"{len(missing_sources)} factual claim(s) missing inline source attribution."
        )
        metrics["missing_inline_sources_samples"] = missing_sources[:5]

    return {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }

