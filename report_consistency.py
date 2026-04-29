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

# Round 43 / Phase 6: module-level logger for the structured PM-drift log
# lines added inside ``validate_report_consistency``.  Pre-fix the validator
# only ``errors.append(...)``-ed the opaque mismatch message; operators had
# no way to see ``portfolio=72 canonical=68`` without running the full
# debugger or scrolling through the per-PID adoptiq.<pid>.log.
logger = logging.getLogger(__name__)


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
    extra_frames: Optional[list] = None,
    account_to_customer: Optional[Dict[str, str]] = None,
    pulse_df: Optional[pd.DataFrame] = None,
) -> ConsistencyResultContract:
    """
    Validate cross-report consistency and produce actionable diagnostics.
    This does not mutate inputs; callers can use returned canonical metrics.
    """
    errors = []
    warnings = []
    metrics: Dict[str, Any] = {}

    # Round 42 / Phase 1: hoist the canonical_metrics import (was lazy at
    # the body site below) so it is available for the ``ab_count``
    # derivation directly below.  Round 3 originally introduced the lazy
    # import to avoid a module-level cycle; the same lazy-inside-the-
    # function pattern still holds, just earlier in the body.
    import canonical_metrics as _cm  # local import to avoid cycle

    # Round 42 / Phase 1: total_barriers must use
    # ``canonical_metrics.count_total_barriers`` so the validator agrees
    # with ``build_portfolio_metrics``.  Round 25 / Phase F.1 changed the
    # canonical helper to dedupe by ``ID`` (Snowflake AB extract fans
    # out per-assignee), but the validator was still using
    # ``_safe_count`` (raw rowcount).  Result: every report path that
    # built ``portfolio_metrics`` via ``build_portfolio_metrics``
    # (compact / EI / leader / renewal) raised
    # ``Portfolio metric mismatch: total_barriers ...`` whenever any
    # barrier had multiple assignees.  Same class as R22-001 for
    # ``total_customers``; this fix is the equivalent for
    # ``total_barriers``.  ``total_cases`` and ``bems_count`` already
    # agree with their canonical helpers so no change there.
    ab_count = _cm.count_total_barriers(ab_df)
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
    # Round 42 / Phase 1: ``_cm`` is already imported above; the duplicate
    # import that used to live here was removed so the module-level cycle
    # avoidance still holds via the single hoisted import.

    # Round 25 / Phase A: ``metrics["total_customers"]`` now derives
    # from the SAME narrow displayed-sheets universe used by the
    # Excel ``Summary`` row and the Compact Word headline tile --
    # ``count_customers(ab_df=, csone_df=, pulse_df=)``.  Pre-Round 25
    # this branch threaded ``extra_frames`` (team subs, action plans,
    # success priorities, csconsole adoption barriers), which inflated
    # the validator's universe above what readers can manually
    # reconcile by counting unique customers across the three detail
    # sheets the report actually displays.  ``extra_frames`` is still
    # accepted (and used downstream for defect-customer linkage) but
    # no longer enters this headline count.  The wider count is
    # surfaced as ``metrics["total_customers_with_extras"]`` for
    # diagnostic parity with pre-Round 25 dashboards.
    _pulse_for_count = pulse_df if pulse_df is not None else customer_pulse_df
    if customer_universe is not None:
        if isinstance(customer_universe, pd.DataFrame):
            metrics["total_customers"] = _cm.count_customers(
                ab_df=customer_universe,
                csone_df=None,
                pulse_df=_pulse_for_count,
            )
        else:
            # Round 6 / Phase 5.14: previously this branch did its own
            # ad-hoc set construction (normalize -> drop "Unknown" ->
            # len()) which silently disagreed with ``cm.count_customers``
            # on edge cases (NaN handling, "" vs "Unknown" treatment,
            # how account_to_customer overrides apply).  Build a
            # one-column synthetic DataFrame and route through
            # ``cm.count_customers`` so the validator's customer
            # universe ALWAYS uses the same counting rule that the
            # reports themselves use.
            try:
                _iter_values = list(customer_universe) if not isinstance(customer_universe, str) else [customer_universe]
            except TypeError:
                _iter_values = [customer_universe]
            try:
                _synthetic_universe = pd.DataFrame({"customer_name": _iter_values})
                metrics["total_customers"] = _cm.count_customers(
                    ab_df=_synthetic_universe,
                    csone_df=None,
                    pulse_df=_pulse_for_count,
                )
            except Exception as _univ_err:
                # Defensive fallback: if the synthetic universe path
                # fails (e.g. unhashable values), fall back to the
                # legacy normalize-set count and warn so ops can see
                # which customer_universe shape caused it.
                warnings.append(
                    f"customer_universe canonical count failed; using legacy fallback "
                    f"({_univ_err.__class__.__name__})."
                )
                customer_set: set = set()
                for value in _iter_values:
                    customer_set.add(normalize_customer_name(value))
                customer_set = {c for c in customer_set if c and c != "Unknown"}
                metrics["total_customers"] = len(customer_set)
    else:
        metrics["total_customers"] = _cm.count_customers(
            ab_df=ab_df,
            csone_df=csone_df,
            pulse_df=_pulse_for_count,
        )

    # Round 25 / Phase A: surface the wider universe (with extras +
    # account_to_customer backfill) as a diagnostic so existing
    # callers that need it for defect-customer linkage / per-section
    # coverage can still see it.  This is INFORMATIONAL only; the
    # PM-vs-validator headline parity check below uses the narrow
    # ``metrics["total_customers"]`` value.
    try:
        metrics["total_customers_with_extras"] = _cm.count_customers(
            ab_df=ab_df,
            csone_df=csone_df,
            pulse_df=_pulse_for_count,
            extra_frames=extra_frames,
            account_to_customer=account_to_customer,
        )
    except Exception:
        metrics["total_customers_with_extras"] = metrics["total_customers"]

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

        # Round 4 / Phase 3.3: open / closed TAC counts must come from
        # the canonical helpers (`cm.count_open_tac` / `count_closed_tac`)
        # rather than a status-set-only path.  The previous implementation
        # only looked at ``status_norm`` membership and missed rows whose
        # status was ``Unknown`` but whose ``closed_date`` made them
        # closed (``add_case_lifecycle_fields`` flips ``is_closed=True``
        # for that case).  Driving the validator from the same lifecycle
        # fields the report builders use eliminates spurious validator
        # failures and makes drift detectable.
        try:
            metrics["count_open_tac"] = _cm.count_open_tac(csone_df)
            metrics["count_closed_tac"] = _cm.count_closed_tac(csone_df)
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
        # Round 43 / Phase 6: emit a structured drift log line BEFORE each
        # ``errors.append(...)`` so the next failure is one
        # ``grep '[CONSISTENCY] PM drift'`` away from the failing key +
        # both sides of the comparison.  Pre-fix the operator only had the
        # opaque ``errors.append("Portfolio metric mismatch: total_barriers
        # does not match ...")`` (the build-19 demo error) and had to do
        # log archaeology to discover ``portfolio=72 canonical=68``.
        _pm_total_barriers = int(portfolio_metrics.get("total_barriers", 0))
        if _pm_total_barriers != ab_count:
            logger.error(
                "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                "total_barriers", _pm_total_barriers, ab_count,
            )
            errors.append("Portfolio metric mismatch: total_barriers does not match normalized adoption barriers.")
        _pm_total_cases = int(portfolio_metrics.get("total_cases", 0))
        if _pm_total_cases != cs_count:
            logger.error(
                "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                "total_cases", _pm_total_cases, cs_count,
            )
            errors.append("Portfolio metric mismatch: total_cases does not match normalized TAC cases.")
        _pm_bems_count = int(portfolio_metrics.get("bems_count", 0))
        if _pm_bems_count != bems_count:
            logger.error(
                "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                "bems_count", _pm_bems_count, bems_count,
            )
            errors.append("Portfolio metric mismatch: bems_count does not match canonical BEMS detection.")
        # Round 25 / Phase A: cross-format parity gate.  After this
        # round, ``portfolio_metrics["total_customers"]`` (Word
        # headline) and ``metrics["total_customers"]`` (validator's
        # narrow displayed-sheets count) are both derived from
        # ``count_customers(ab_df=, csone_df=, pulse_df=)``.  Any drift
        # here means the Word path is widening the universe in a way
        # the Excel ``Summary`` sheet does NOT, so a reader manually
        # counting customers across the three detail sheets will
        # disagree with the headline tile.  Block the build.
        if "total_customers" in portfolio_metrics:
            pm_total = int(portfolio_metrics.get("total_customers", 0) or 0)
            canon_total = int(metrics["total_customers"])
            if pm_total != canon_total:
                # Round 43 / Phase 6: structured drift log -- see the
                # earlier total_barriers block for rationale.
                logger.error(
                    "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                    "total_customers", pm_total, canon_total,
                )
                errors.append(
                    "Portfolio metric mismatch: total_customers="
                    f"{pm_total} (Word headline) != "
                    f"{canon_total} (canonical AB ∪ CSOne ∪ Pulse universe). "
                    "The Word headline must mirror the Excel Summary row -- both "
                    "derive from count_customers(ab_df=, csone_df=, pulse_df=). "
                    "If the Word path is using extra_frames / account_to_customer "
                    "to widen the count, drop those args from the headline call site."
                )
        reported_p1 = portfolio_metrics.get("critical_p1", portfolio_metrics.get("p1_cases", None))
        if reported_p1 is not None and int(reported_p1) != metrics["critical_p1"]:
            # Round 43 / Phase 6: structured drift log.
            logger.error(
                "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                "critical_p1", int(reported_p1), metrics["critical_p1"],
            )
            errors.append("Portfolio metric mismatch: critical_p1 does not match canonical severity counting.")
        reported_p2 = portfolio_metrics.get("high_p2", portfolio_metrics.get("p2_cases", None))
        if reported_p2 is not None and int(reported_p2) != metrics["high_p2"]:
            # Round 43 / Phase 6: structured drift log.
            logger.error(
                "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                "high_p2", int(reported_p2), metrics["high_p2"],
            )
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
                # Round 43 / Phase 6: structured drift log.
                logger.error(
                    "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                    key, int(reported), int(expected_metric),
                )
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
                # Round 43 / Phase 6: structured drift log (warning-class
                # check; logged at INFO so it doesn't drown the operator
                # while still being grep-able).
                logger.info(
                    "[CONSISTENCY] PM drift (warning-class) key=%s portfolio=%s canonical=%s",
                    key, int(reported), int(expected_metric),
                )
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
                # Round 43 / Phase 6: structured drift log.
                logger.error(
                    "[CONSISTENCY] PM drift key=%s portfolio=%s canonical=%s",
                    key, int(reported), int(expected_metric),
                )
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
        # Round 5 / Phase 5.8: previously we only sourced known
        # customer names from ``ab_df`` and ``csone_df``.  That caused
        # subscription-only customers (no AB rows, no TAC cases) to
        # be flagged as "unknown defect customers" even though the
        # leader path passes ``team_subs_df_unfiltered`` /
        # ``customer_pulse`` etc. via ``extra_frames``.  Walk every
        # extra frame so the validator's universe matches the
        # report's universe.
        _frames_to_scan = [ab_df, csone_df, customer_pulse_df]
        if extra_frames:
            try:
                _frames_to_scan.extend(list(extra_frames))
            except Exception:
                pass
        for frame in _frames_to_scan:
            if frame is None or getattr(frame, "empty", True):
                continue
            for col in ("customer_name", "BU_NAME", "Customer Name", "Account Name", "ACCOUNT_NAME"):
                if col in frame.columns:
                    known_customers.update(
                        normalize_customer_name(v)
                        for v in frame[col].dropna().astype(str).tolist()
                    )
        # Round 5: include the account_to_customer mapping values
        # so subscription-only customers (resolved via account ID,
        # not by a named column) are still in the known set.
        if account_to_customer:
            try:
                known_customers.update(
                    normalize_customer_name(v)
                    for v in account_to_customer.values()
                    if v
                )
            except Exception:
                pass
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

    # Round 5 / Phase 5.10: in strict_mode, an empty / missing
    # ``factual_claims`` payload is itself an error when there is
    # actual portfolio activity to narrate.  Without this check, a
    # report that fails to extract any claim text at all would
    # silently pass strict consistency (because there are no claims
    # to be missing inline sources for) and a downstream auditor
    # would have no signal that the LLM never grounded the report.
    if strict_mode and not factual_claims:
        _has_activity = (
            (ab_df is not None and not getattr(ab_df, "empty", True))
            or (csone_df is not None and not getattr(csone_df, "empty", True))
            or (customer_pulse_df is not None and not getattr(customer_pulse_df, "empty", True))
        )
        if _has_activity:
            errors.append(
                "strict_mode: factual_claims is empty/None but the input "
                "frames contain narratable activity; the report appears "
                "to be ungrounded."
            )
            metrics["strict_mode_factual_claims_missing"] = True

    # Round 7 / Phase 3.7: assert that the reported high-risk count
    # equals the canonical compute_high_risk_count value.  Previously
    # the consistency check counted bands but never required parity
    # with the canonical helper, so a portfolio_high_risk_fallback
    # could ship without anyone noticing.
    if portfolio_metrics is not None and risk_data is not None:
        try:
            from canonical_metrics import (
                compute_high_risk_count as _cm_count_high,
                RISK_SCALE_0_TO_100 as _CM_RISK_0_100,
            )
            canonical_high = int(_cm_count_high(risk_data, scale=_CM_RISK_0_100))
        except Exception as _cm_err:
            warnings.append(
                f"Round 7 / Phase 3.7: could not compute canonical high-risk count "
                f"({type(_cm_err).__name__}: {_cm_err}); invariant check skipped."
            )
            canonical_high = None
        if canonical_high is not None and "high_risk_customers" in portfolio_metrics:
            try:
                reported_high = int(portfolio_metrics.get("high_risk_customers", 0))
            except (TypeError, ValueError):
                reported_high = -1
            if reported_high != canonical_high:
                errors.append(
                    f"Round 7 / Phase 3.7: high_risk_customers invariant violated -- "
                    f"portfolio_metrics.high_risk_customers={reported_high} but "
                    f"canonical_metrics.compute_high_risk_count={canonical_high}."
                )
            metrics["canonical_high_risk_count"] = canonical_high
        if portfolio_metrics.get("portfolio_high_risk_fallback"):
            warnings.append(
                "Round 7 / Phase 3.4: portfolio summary used the band-tally "
                "fallback for high_risk_customers; canonical_metrics was "
                "unavailable. Reason: "
                + str(portfolio_metrics.get("portfolio_high_risk_fallback_reason", "unknown"))
            )

    # Round 7 / Phase 3.8: deterministic ordering of errors and
    # warnings so dashboards/snapshots diff cleanly across runs.  The
    # original lists preserved insertion order, which depended on
    # dict iteration order from the upstream metrics dict.
    errors = sorted(errors)
    warnings = sorted(warnings)

    result = {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }
    if strict_mode and errors:
        raise ValueError("; ".join(errors))
    return result


# ---------------------------------------------------------------------------
# Round 25 / Phase B: post-render Word numeric drift validator.
#
# The Compact Word "Executive Summary: What's Really Happening" block is
# filled by the LLM from ``PROMPT_PORTFOLIO_TEMPLATE`` in
# ``adoptiq_backend.py``.  Phase B injects the canonical totals into the
# prompt body so the LLM has authoritative values; this validator runs
# *after* the doc text is assembled and asserts every numeric rendering
# of those totals matches the canonical pipeline.  Drift either means
# the LLM ignored the constraint or a downstream formatter mutated the
# value -- either way, block the build before the artifact reaches the
# user's Downloads folder.
# ---------------------------------------------------------------------------


# Each entry maps a canonical-metric key (the key on the canonical
# ``portfolio_metrics`` dict / kwargs to ``PROMPT_PORTFOLIO_TEMPLATE``)
# to:
#   - ``label``: human-readable name for error messages
#   - ``patterns``: regex patterns matched against the rendered Word
#     paragraphs.  The number is captured in group(1).  Patterns are
#     case-insensitive and tolerate ``**bold**`` / leading bullets.
#
# Patterns are intentionally narrow -- they match ONLY the
# Phase B "Portfolio Snapshot" lines.  They will not pick up
# free-floating numeric mentions elsewhere in the narrative
# (e.g. "we closed 14 cases this quarter") so the validator is
# safe to enable on existing reports.
_R25B_VALIDATED_TOTALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "total_customers",
        "Total Customers",
        (
            r"total\s+customers?\s*:?\s*\**\s*(\d+)\b",
        ),
    ),
    (
        "total_barriers",
        "Active Adoption Barriers",
        (
            r"active\s+adoption\s+barriers?\s*:?\s*\**\s*(\d+)\b",
        ),
    ),
    (
        "total_cases",
        "TAC Cases",
        (
            r"tac\s+cases?\s*:?\s*\**\s*(\d+)\b",
        ),
    ),
    (
        "bems_count",
        "BEMS Escalations",
        (
            r"bems\s+escalations?\s*:?\s*\**\s*(\d+)\b",
        ),
    ),
)


def _r25b_extract_paragraphs(rendered: Any) -> list[str]:
    """Return paragraph-level text from a rendered Word doc / iterable / string.

    Accepts:
    - a ``docx.Document``-like object exposing ``.paragraphs`` with ``.text``
    - an iterable of strings (one per paragraph)
    - a single string (split on newlines)
    """

    if rendered is None:
        return []
    if hasattr(rendered, "paragraphs"):
        try:
            return [str(getattr(p, "text", "") or "") for p in rendered.paragraphs]
        except Exception:
            return []
    if isinstance(rendered, str):
        return rendered.splitlines()
    try:
        return [str(item or "") for item in rendered]
    except TypeError:
        return [str(rendered)]


def validate_word_numeric_drift(
    rendered: Any,
    canonical_totals: Dict[str, Any],
    *,
    raise_on_drift: bool = False,
) -> ConsistencyResultContract:
    """Round 25 / Phase B post-render validator.

    Scans the rendered Compact Word document for the Portfolio Snapshot
    numeric claims and verifies each matches the canonical pipeline
    value supplied in ``canonical_totals``.

    Parameters
    ----------
    rendered:
        A ``docx.Document`` instance, an iterable of paragraph strings,
        or a single multi-line string.  The validator extracts
        paragraph-level text and runs the Round 25 narrow regex set
        against each paragraph.
    canonical_totals:
        Mapping of canonical metric keys (``"total_customers"``,
        ``"total_barriers"``, ``"total_cases"``, ``"bems_count"``) to
        their authoritative integer values.  Keys absent from this dict
        are skipped (the validator only checks what it has truth for).
    raise_on_drift:
        When ``True`` and any drift is detected, raise ``ValueError``
        with the aggregated error list.  When ``False`` (default), the
        caller can read the returned ``errors`` list and decide how to
        surface it.  The Compact Word build path uses
        ``raise_on_drift=True`` to block report delivery.

    Returns
    -------
    A ``ConsistencyResultContract`` dict with ``is_valid``, ``errors``,
    ``warnings``, and a ``metrics`` block carrying the parsed numbers
    keyed by canonical metric name (e.g. ``metrics["total_customers"]``
    is a list of every integer found in the rendered narrative for that
    label, in order).
    """

    paragraphs = _r25b_extract_paragraphs(rendered)
    parsed: Dict[str, list[int]] = {key: [] for key, _, _ in _R25B_VALIDATED_TOTALS}
    errors: list[str] = []
    warnings: list[str] = []

    for paragraph in paragraphs:
        if not paragraph:
            continue
        for key, _label, patterns in _R25B_VALIDATED_TOTALS:
            for pattern in patterns:
                for match in re.finditer(pattern, paragraph, flags=re.IGNORECASE):
                    try:
                        parsed[key].append(int(match.group(1)))
                    except (TypeError, ValueError):
                        continue

    for key, label, _patterns in _R25B_VALIDATED_TOTALS:
        if key not in canonical_totals:
            continue
        try:
            canonical_value = int(canonical_totals[key])
        except (TypeError, ValueError):
            warnings.append(
                f"Round 25 / Phase B: canonical_totals[{key!r}] is not an int "
                f"({canonical_totals.get(key)!r}); validator skipped this metric."
            )
            continue
        narrated_values = parsed[key]
        if not narrated_values:
            warnings.append(
                f"Round 25 / Phase B: no narrated value found for {label} "
                f"(canonical={canonical_value}). The Portfolio Snapshot block "
                "may have been omitted or reformatted by the LLM."
            )
            continue
        bad = [v for v in narrated_values if v != canonical_value]
        if bad:
            errors.append(
                f"Round 25 / Phase B: {label} drift detected. "
                f"Canonical={canonical_value}, narrated={bad}. "
                "The LLM rendered a value that disagrees with the canonical "
                "pipeline; either the prompt substitutions did not reach the "
                "model or the model overrode them. Block the build."
            )

    metrics: Dict[str, Any] = {f"narrated_{k}": v for k, v in parsed.items()}
    metrics["canonical_totals_checked"] = sorted(
        k for k in canonical_totals.keys() if k in parsed
    )

    errors = sorted(errors)
    warnings = sorted(warnings)
    result: ConsistencyResultContract = {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }
    if raise_on_drift and errors:
        raise ValueError("; ".join(errors))
    return result


# ---------------------------------------------------------------------------
# Round 25 / Phase C: risk-band narrative drift validator.
#
# The "All Customers in Trouble" block in ``PROMPT_PORTFOLIO_TEMPLATE``
# emits one ``Risk Level: <BAND>`` line per troubled customer.  The
# canonical pipeline reports ``portfolio_metrics["high_risk_customers"]``
# = number of customers in CRITICAL or HIGH bands.  Pre-Round 25 the
# LLM was free to:
#   - invent compound labels ("HIGH/CRITICAL", "MEDIUM/LOW")
#   - promote/demote bands relative to the canonical assignment
#   - list more or fewer CRITICAL+HIGH customers than the dashboard tile
#
# This validator catches all three cases by:
#   1. Asserting every ``Risk Level: <X>`` matches one of the five
#      canonical labels (no compound, no synonyms like "MODERATE").
#   2. Counting narrated CRITICAL+HIGH lines and comparing to the
#      canonical ``high_risk_customers`` count.
#
# It returns a separate ConsistencyResultContract so callers can
# combine it with ``validate_word_numeric_drift`` results without
# having to teach the latter about a fundamentally different
# matching strategy.
# ---------------------------------------------------------------------------


_R25C_CANONICAL_BANDS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY")
# Round 25 / Phase C: ``Risk Level:`` followed by a single canonical
# band token.  ``[\*\s\[]*`` lets the regex tolerate the prompt's
# bold-and-bracket formatting (e.g. ``Risk Level: **[HIGH]**``).
_R25C_RISK_LEVEL_RE = re.compile(
    r"risk\s+level\s*:\s*[\*\s\[]*([A-Za-z][A-Za-z/_-]*)",
    flags=re.IGNORECASE,
)


def validate_word_risk_band_claims(
    rendered: Any,
    canonical_high_risk_customers: int,
    *,
    raise_on_drift: bool = False,
) -> ConsistencyResultContract:
    """Round 25 / Phase C post-render validator for risk-band narratives.

    Scans the rendered Word doc for ``Risk Level: <BAND>`` lines and
    asserts:

    - Each band is one of the five canonical labels (CRITICAL, HIGH,
      MEDIUM, LOW, HEALTHY).  Compound labels (``HIGH/CRITICAL``,
      ``MEDIUM/LOW``) and synonyms (``MODERATE``) trip an error.
    - The number of narrated CRITICAL+HIGH lines equals
      ``canonical_high_risk_customers`` (the canonical
      ``portfolio_metrics["high_risk_customers"]`` count).

    Parameters
    ----------
    rendered:
        A ``docx.Document`` instance, an iterable of paragraph
        strings, or a single multi-line string.
    canonical_high_risk_customers:
        The authoritative count from
        ``portfolio_metrics["high_risk_customers"]``.
    raise_on_drift:
        When ``True`` and any drift is detected, raise ``ValueError``.

    Returns
    -------
    A ``ConsistencyResultContract`` dict with parsed risk-band
    statistics under ``metrics``:
    - ``narrated_risk_levels``: list of every band token found, in
      order
    - ``narrated_critical_high_count``: count of CRITICAL+HIGH lines
    - ``invalid_band_labels``: list of any non-canonical labels found
    - ``canonical_high_risk_customers``: echo of the input value
    """

    paragraphs = _r25b_extract_paragraphs(rendered)

    narrated_levels: list[str] = []
    invalid_labels: list[str] = []

    for paragraph in paragraphs:
        if not paragraph:
            continue
        for match in _R25C_RISK_LEVEL_RE.finditer(paragraph):
            raw = match.group(1) or ""
            token = raw.strip().upper().rstrip(".:,;]*")
            if not token:
                continue
            # Round 25 / Phase C: explicitly reject compound bands
            # that contain a separator AFTER the regex captured a
            # single word -- the regex doesn't allow ``/`` so we
            # must look back into the surrounding paragraph for the
            # full token.  Pull a wider match to detect compounds.
            full_pattern = re.compile(
                r"risk\s+level\s*:\s*[\*\s\[]*([A-Za-z][A-Za-z/_\-\s]*?)(?:[\*\]\.,;\s]|$)",
                flags=re.IGNORECASE,
            )
            wide = full_pattern.search(paragraph[match.start():])
            if wide:
                wide_token = wide.group(1).strip().upper()
                if "/" in wide_token or "-" in wide_token or " " in wide_token.strip():
                    invalid_labels.append(wide_token)
                    continue
                token = wide_token
            if token not in _R25C_CANONICAL_BANDS:
                invalid_labels.append(token)
                continue
            narrated_levels.append(token)

    critical_high = sum(1 for b in narrated_levels if b in {"CRITICAL", "HIGH"})

    errors: list[str] = []
    warnings: list[str] = []

    if invalid_labels:
        errors.append(
            "Round 25 / Phase C: non-canonical risk band label(s) detected in "
            f"narrative: {sorted(set(invalid_labels))}. "
            "The PROMPT_PORTFOLIO_TEMPLATE 'All Customers in Trouble' block "
            "binds the LLM to the five canonical bands "
            "(CRITICAL, HIGH, MEDIUM, LOW, HEALTHY) -- compound labels and "
            "synonyms are forbidden."
        )

    try:
        canon_high = int(canonical_high_risk_customers)
    except (TypeError, ValueError):
        warnings.append(
            "Round 25 / Phase C: canonical_high_risk_customers is not an "
            f"int ({canonical_high_risk_customers!r}); CRITICAL+HIGH "
            "parity check skipped."
        )
        canon_high = None  # type: ignore[assignment]

    if canon_high is not None:
        if not narrated_levels and not invalid_labels:
            warnings.append(
                "Round 25 / Phase C: no Risk Level lines found in the "
                "rendered narrative. The 'All Customers in Trouble' block "
                "may have been omitted by the LLM."
            )
        elif critical_high != canon_high:
            errors.append(
                "Round 25 / Phase C: CRITICAL+HIGH narrative drift. "
                f"Canonical portfolio_metrics['high_risk_customers']={canon_high}, "
                f"narrated CRITICAL+HIGH lines={critical_high}. "
                "Block the build -- the dashboard tile and the trouble-spot "
                "list must agree."
            )

    metrics: Dict[str, Any] = {
        "narrated_risk_levels": narrated_levels,
        "narrated_critical_high_count": critical_high,
        "invalid_band_labels": sorted(set(invalid_labels)),
        "canonical_high_risk_customers": canonical_high_risk_customers,
    }

    errors = sorted(errors)
    warnings = sorted(warnings)
    result: ConsistencyResultContract = {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }
    if raise_on_drift and errors:
        raise ValueError("; ".join(errors))
    return result
