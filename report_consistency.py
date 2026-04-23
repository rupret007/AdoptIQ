"""Cross-report consistency checks and failsafes for AdoptIQ outputs."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

import pandas as pd

from data_contracts import ConsistencyResultContract, DefectsContract, PortfolioMetricsContract
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
    portfolio_metrics: Optional[PortfolioMetricsContract] = None,
    risk_data: Optional[Dict[str, Dict[str, Any]]] = None,
    defects: Optional[DefectsContract] = None,
    factual_claims: Optional[list] = None,
    customer_universe: Optional[Any] = None,
    max_other_unknown_ratio: float = 0.60,
    customer_pulse_df: Optional[pd.DataFrame] = None,
    expected_account_ids: Optional[list] = None,
    pulse_coverage_warn_threshold: float = 0.50,
    strict_mode: bool = False,
) -> ConsistencyResultContract:
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
    # Round 3: route through canonical_metrics so the validator and the
    # reports it validates use IDENTICAL counting rules. Previously the
    # validator scanned only ("customer_name","BU_NAME","Customer Name")
    # and added ad-hoc "" / "UNKNOWN" buckets to unknown-priority,
    # producing spurious mismatches against the canonical helpers.
    import canonical_metrics as _cm  # local import to avoid cycle

    if customer_universe is not None:
        if isinstance(customer_universe, pd.DataFrame):
            metrics["total_customers"] = _cm.count_customers(
                ab_df=customer_universe, csone_df=None
            )
        else:
            customer_set: set = set()
            try:
                for value in customer_universe:
                    customer_set.add(normalize_customer_name(value))
            except TypeError:
                customer_set.add(normalize_customer_name(customer_universe))
            customer_set = {c for c in customer_set if c and c != "Unknown"}
            metrics["total_customers"] = len(customer_set)
    else:
        metrics["total_customers"] = _cm.count_customers(
            ab_df=ab_df, csone_df=csone_df
        )

    if csone_df is not None and not csone_df.empty:
        # Single source of truth for P1/P2/P3/P4/Unknown counts so the
        # validator cannot disagree with the same numbers rendered into
        # report tables / dashboards.
        metrics["critical_p1"] = _cm.count_p1(csone_df)
        metrics["high_p2"] = _cm.count_p2(csone_df)
        # Round 4: also surface the canonical escalated total so the
        # contract can pin (and detect drift in) the P1+P2 figure used
        # across report headlines, AI prompts, and Excel summaries.
        try:
            metrics["escalated_cases"] = _cm.count_escalated(csone_df)
        except Exception:
            metrics["escalated_cases"] = metrics["critical_p1"] + metrics["high_p2"]
        priority_buckets = _cm.count_priority_breakdown(csone_df)
        metrics["p3_cases"] = priority_buckets["P3"]
        metrics["p4_cases"] = priority_buckets["P4"]
        metrics["unknown_priority_cases"] = priority_buckets["Unknown"]

        # Round 4: open / closed TAC counts, derived via canonical
        # status normalization so the validator and report templates
        # cannot disagree on what "open" or "closed" means.
        try:
            from data_normalization import (
                add_case_lifecycle_fields as _enrich,
                normalize_status_label as _norm_status,
            )
            _status_enriched = _enrich(csone_df) if "status_norm" not in csone_df.columns else csone_df
            if "status_norm" in _status_enriched.columns:
                _status_norm = _status_enriched["status_norm"].astype(str)
            else:
                _status_col = next(
                    (c for c in ("STATUS_C", "STATUS", "Status") if c in _status_enriched.columns),
                    None,
                )
                _status_norm = (
                    _status_enriched[_status_col].apply(_norm_status)
                    if _status_col else pd.Series([], dtype=str)
                )
            _open_states = {"Open", "New", "InProgress", "WaitingOnCustomer", "Pending"}
            _closed_states = {"Closed", "Resolved", "Cancelled"}
            metrics["count_open_tac"] = int(_status_norm.isin(_open_states).sum())
            metrics["count_closed_tac"] = int(_status_norm.isin(_closed_states).sum())
        except Exception as _open_close_err:
            metrics["count_open_tac"] = 0
            metrics["count_closed_tac"] = 0
            warnings.append(
                f"Open/closed TAC derivation failed: {str(_open_close_err).strip() or _open_close_err.__class__.__name__}"
            )

        # Case type classification (break/fix vs provisioning) - canonical.
        # If the caller did not pre-enrich the DataFrame, we enrich on the
        # fly via the same helper that canonical_metrics uses, so the
        # validator agrees with the metrics that report builders compute.
        if "case_type_class" not in csone_df.columns:
            try:
                from data_normalization import add_case_lifecycle_fields as _enrich
                _enriched = _enrich(csone_df)
            except Exception:
                _enriched = csone_df
        else:
            _enriched = csone_df
        if "case_type_class" in _enriched.columns:
            ctc = _enriched["case_type_class"].astype(str)
            metrics["break_fix_cases"] = int((ctc == "break_fix_technical").sum())
            metrics["provisioning_cases"] = int((ctc == "provisioning_request").sum())
        else:
            metrics["break_fix_cases"] = 0
            metrics["provisioning_cases"] = 0
    else:
        metrics["p3_cases"] = 0
        metrics["p4_cases"] = 0
        metrics["unknown_priority_cases"] = 0
        metrics["break_fix_cases"] = 0
        metrics["provisioning_cases"] = 0

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
        reported_p1 = portfolio_metrics.get("critical_p1", portfolio_metrics.get("p1_cases", None))
        if reported_p1 is not None and int(reported_p1) != metrics["critical_p1"]:
            errors.append("Portfolio metric mismatch: critical_p1 does not match canonical severity counting.")
        reported_p2 = portfolio_metrics.get("high_p2", portfolio_metrics.get("p2_cases", None))
        if reported_p2 is not None and int(reported_p2) != metrics["high_p2"]:
            errors.append("Portfolio metric mismatch: high_p2 does not match canonical severity counting.")
        # Extended priority parity (P3, P4, Unknown, escalated).
        for key, expected_metric in (
            ("p3_cases", metrics["p3_cases"]),
            ("p4_cases", metrics["p4_cases"]),
            ("unknown_priority_cases", metrics["unknown_priority_cases"]),
            ("escalated_cases", metrics.get("escalated_cases", 0)),
        ):
            reported = portfolio_metrics.get(key)
            if reported is not None and int(reported) != int(expected_metric):
                errors.append(
                    f"Portfolio metric mismatch: {key} ({int(reported)}) does not match canonical severity counting ({int(expected_metric)})."
                )

        # Round 4: open / closed TAC parity.  Reported as warnings
        # because some report variants (e.g. archived snapshots) carry
        # historical totals that intentionally differ from the live
        # canonical view.
        for key, expected_metric in (
            ("count_open_tac", metrics.get("count_open_tac", 0)),
            ("count_closed_tac", metrics.get("count_closed_tac", 0)),
        ):
            reported = portfolio_metrics.get(key)
            if reported is not None and int(reported) != int(expected_metric):
                warnings.append(
                    f"Portfolio metric drift: {key} ({int(reported)}) differs from canonical normalized status counting ({int(expected_metric)})."
                )

        # Round 4: dual BEMS check.  Some surfaces (Leader mode) report
        # ``bems_combined`` (BEMS detected across BOTH AB and CSOne),
        # while the canonical ``bems_count`` is TAC-only.  Track both
        # explicitly so the Leader variance is surfaced as a tracked
        # warning rather than masked silently.
        if "bems_tac_only" in portfolio_metrics:
            try:
                if int(portfolio_metrics.get("bems_tac_only", 0)) != bems_count:
                    warnings.append(
                        f"Portfolio metric drift: bems_tac_only ({int(portfolio_metrics['bems_tac_only'])}) differs from canonical TAC BEMS detection ({bems_count})."
                    )
            except (TypeError, ValueError):
                pass
        if "bems_combined" in portfolio_metrics and ab_df is not None and not ab_df.empty:
            try:
                ab_bems = int(detect_bems_mask(ab_df).sum())
            except Exception:
                ab_bems = 0
            metrics["bems_combined_observed"] = ab_bems + bems_count
            if int(portfolio_metrics["bems_combined"]) != metrics["bems_combined_observed"]:
                warnings.append(
                    f"Portfolio metric drift: bems_combined ({int(portfolio_metrics['bems_combined'])}) differs from observed AB+TAC BEMS detection ({metrics['bems_combined_observed']})."
                )
        # Case-type parity (break/fix vs provisioning).
        for key, expected_metric in (
            ("break_fix_cases", metrics["break_fix_cases"]),
            ("provisioning_cases", metrics["provisioning_cases"]),
        ):
            reported = portfolio_metrics.get(key)
            if reported is not None and int(reported) != int(expected_metric):
                errors.append(
                    f"Portfolio metric mismatch: {key} ({int(reported)}) does not match canonical case-type counting ({int(expected_metric)})."
                )

        # Risk-band parity (critical/high/medium/low/healthy). These
        # are checked as warnings rather than errors because risk_band
        # depends on the selected scoring scale and is recomputed per
        # profile.
        # Round 4: split CRITICAL and HIGH into distinct buckets so a
        # report that swaps the two categories no longer shows as
        # "matching" against the merged ``high_risk_customers`` total.
        # Maintain ``high_risk_customers`` as the legacy combined sum
        # (CRITICAL+HIGH) for back-compat with existing report keys
        # while also surfacing ``critical_risk_customers`` and
        # ``high_only_risk_customers`` as Round 3 introduced them.
        if risk_data is not None:
            band_observed = {
                "critical": 0,
                "high": 0,
                "high_only": 0,
                "medium": 0,
                "low": 0,
                "healthy": 0,
            }
            for profile in risk_data.values():
                if not isinstance(profile, dict):
                    continue
                band = str(profile.get("risk_band", "")).strip().upper()
                if band == "CRITICAL":
                    band_observed["critical"] += 1
                    band_observed["high"] += 1  # legacy combined bucket
                elif band == "HIGH":
                    band_observed["high_only"] += 1
                    band_observed["high"] += 1
                elif band == "MEDIUM":
                    band_observed["medium"] += 1
                elif band == "LOW":
                    band_observed["low"] += 1
                elif band == "HEALTHY":
                    band_observed["healthy"] += 1
            metrics["risk_band_observed"] = band_observed
            for key, observed in (
                ("critical_risk_customers", band_observed["critical"]),
                ("high_only_risk_customers", band_observed["high_only"]),
                ("high_risk_customers", band_observed["high"]),
                ("medium_risk_customers", band_observed["medium"]),
                ("low_risk_customers", band_observed["low"]),
                ("healthy_customers", band_observed["healthy"]),
            ):
                reported = portfolio_metrics.get(key)
                if reported is not None and int(reported) != int(observed):
                    warnings.append(
                        f"Portfolio metric drift: {key} ({int(reported)}) differs from observed risk_band tally ({int(observed)})."
                    )

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
        csc_ids = defects.get("csc_ids", []) or []
        bems_ids = defects.get("bems_ids", []) or []
        combined_defect_ids = list(csc_ids) + list(bems_ids)
        defect_ids = set(
            str(x).strip().upper()
            for x in combined_defect_ids
            if str(x).strip()
        )
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

    result = {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }
    if strict_mode and errors:
        raise ValueError("; ".join(errors))
    return result

