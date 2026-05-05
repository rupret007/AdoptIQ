"""Round 79 / Build 55 (B2/B3 wiring): BE-engineering priority pipeline.

Orchestrator that ties together the deterministic scorer
(:mod:`be_priority_scorer`) and the LLM classifier
(:mod:`be_priority_llm_classifier`) into the two canonical XLSX sheets
the Comprehensive and Leader writers need:

* ``BE_Priority_Barriers`` -- per-AB rank with deterministic priority +
  optional LLM classification.
* ``BE_Focus_Areas`` -- portfolio rollup grouped by sub-technology /
  category cluster.

This module deliberately stays I/O-free: the LLM callable is injected so
the same orchestrator runs in production (real CircuIT) and in pytest
(stub callable returning canned JSON).  Both ``run_comprehensive_analysis``
and ``run_leader_report_generation`` import the same helpers so the SSoT
for the BE-priority output is a single function.

Tests: ``tests/test_round79_b4_be_xlsx_sheets.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

import be_priority_llm_classifier as bpl
import be_priority_scorer as bes

logger = logging.getLogger(__name__)


# Round 79 / B2: curated column order for the ``BE_Priority_Barriers``
# XLSX sheet.  Must match the operator-facing schema documented in the
# Round 79 plan; new columns MUST be appended AT THE END so old XLSX
# readers (``pd.read_excel``) keep working.
_BARRIERS_COLUMNS: Tuple[str, ...] = (
    "Rank",
    "Customer",
    "Technology",
    "Sub_Technology",
    "AB_ID",
    "CSConsole_Severity",
    "Independent_Priority_Score",
    "BE_Class",
    "BE_Reason",
    "BE_Confidence",
    "Days_Open",
    "Customer_Risk_Score",
    "Customer_Pulse",
    "Top_Signal",
    "Title",
    "Description",
    "Action_Plan_Status",
    "Account_ID",
)


def _resolve_pulse_for_customer(
    customer: Any, pulse_by_customer: Dict[str, float]
) -> Optional[float]:
    """Find the pulse score for ``customer`` (case/whitespace insensitive)."""

    if pulse_by_customer is None or not isinstance(pulse_by_customer, dict):
        return None
    if customer is None:
        return None
    try:
        key = str(customer).strip().lower()
    except Exception:
        return None
    if not key:
        return None
    return pulse_by_customer.get(key)


def _safe_str(value: Any, *, max_len: int = 480) -> str:
    """Render ``value`` as a bounded string for XLSX rows."""

    if value is None:
        return ""
    try:
        if isinstance(value, float) and pd.isna(value):
            return ""
    except Exception:
        pass
    rendered = str(value)
    if len(rendered) > max_len:
        return rendered[: max_len - 3] + "..."
    return rendered


def _column_pick(row: pd.Series, *candidates: str) -> Any:
    """Return the first non-blank value among the given column names."""

    for cand in candidates:
        if cand in row.index:
            value = row[cand]
            try:
                if value is None:
                    continue
                if isinstance(value, float) and pd.isna(value):
                    continue
                rendered = str(value).strip()
                if not rendered:
                    continue
                return value
            except Exception:
                continue
    return None


def _build_barriers_dataframe(
    scored: pd.DataFrame,
    classified_lookup: Dict[str, Dict[str, Any]],
) -> pd.DataFrame:
    """Project the scored frame into the curated XLSX schema."""

    if scored is None or scored.empty:
        return _provenance_barriers_df(
            "No adoption barriers to score for this scope. The scorer "
            "received an empty AB frame -- confirm CSConsole / Snowflake "
            "returned at least one AB row in the analysed window."
        )

    # Sort by deterministic priority desc, AB_ID asc for a stable rank.
    sort_cols = ["be_priority_score", "ID"]
    available_sort = [c for c in sort_cols if c in scored.columns]
    if available_sort:
        sorted_frame = scored.sort_values(
            by=available_sort,
            ascending=[False, True][: len(available_sort)],
            kind="mergesort",
        ).reset_index(drop=True)
    else:
        sorted_frame = scored.reset_index(drop=True)

    rows: List[Dict[str, Any]] = []
    for idx, row in sorted_frame.iterrows():
        ab_id_raw = _column_pick(row, "ID", "AB_ID", "Id", "id")
        ab_id = _safe_str(ab_id_raw, max_len=64) if ab_id_raw is not None else ""
        llm_record = classified_lookup.get(ab_id) if ab_id else None
        be_class = ""
        be_reason = ""
        be_confidence = ""
        if isinstance(llm_record, dict):
            be_class_raw = _safe_str(llm_record.get("be_class"), max_len=64)
            # Round 79 / B2: ``UNCLASSIFIED`` is the classifier's neutral
            # default (LLM disabled / dropped / not in response). Project
            # it to empty string in the curated XLSX so the operator does
            # not see "UNCLASSIFIED" tags everywhere on a kill-switched
            # run -- empty signals "not classified" cleanly.
            be_class = "" if be_class_raw.upper() == "UNCLASSIFIED" else be_class_raw
            be_reason = _safe_str(llm_record.get("be_reason"), max_len=480)
            confidence_raw = _safe_str(llm_record.get("be_confidence"), max_len=32)
            be_confidence = "" if confidence_raw.lower() == "unknown" else confidence_raw

        score_raw = row.get("be_priority_score")
        try:
            score_value = round(float(score_raw), 2) if score_raw is not None else None
        except (TypeError, ValueError):
            score_value = None

        days_raw = row.get("open_age_days")
        try:
            days_value = int(round(float(days_raw))) if days_raw is not None and not (
                isinstance(days_raw, float) and pd.isna(days_raw)
            ) else None
        except (TypeError, ValueError):
            days_value = None

        cust_risk_raw = row.get("be_customer_risk")
        try:
            cust_risk_value = (
                round(float(cust_risk_raw), 2)
                if cust_risk_raw is not None
                and not (isinstance(cust_risk_raw, float) and pd.isna(cust_risk_raw))
                else None
            )
        except (TypeError, ValueError):
            cust_risk_value = None

        pulse_raw = row.get("be_customer_pulse")
        try:
            pulse_value = (
                round(float(pulse_raw), 2)
                if pulse_raw is not None
                and not (isinstance(pulse_raw, float) and pd.isna(pulse_raw))
                else None
            )
        except (TypeError, ValueError):
            pulse_value = None

        rows.append(
            {
                "Rank": int(idx) + 1,
                "Customer": _safe_str(
                    _column_pick(row, "customer_name", "Customer", "BU_NAME"),
                    max_len=200,
                ),
                "Technology": _safe_str(
                    _column_pick(row, "technology", "Technology"), max_len=120
                ),
                "Sub_Technology": _safe_str(
                    _column_pick(row, "sub_technology", "Sub_Technology"),
                    max_len=120,
                ),
                "AB_ID": ab_id,
                "CSConsole_Severity": _safe_str(
                    _column_pick(row, "severity_norm", "Severity"), max_len=32
                ),
                "Independent_Priority_Score": score_value,
                "BE_Class": be_class,
                "BE_Reason": be_reason,
                "BE_Confidence": be_confidence,
                "Days_Open": days_value,
                "Customer_Risk_Score": cust_risk_value,
                "Customer_Pulse": pulse_value,
                "Top_Signal": _safe_str(
                    row.get("be_top_signal"), max_len=64
                ),
                "Title": _safe_str(
                    _column_pick(row, "Title", "title", "Subject"), max_len=480
                ),
                "Description": _safe_str(
                    _column_pick(
                        row,
                        "Problem Description",
                        "problem_description",
                        "Description",
                    ),
                    max_len=2000,
                ),
                "Action_Plan_Status": _safe_str(
                    _column_pick(
                        row,
                        "Action Plan Status",
                        "ap_status",
                        "Action_Plan_Status",
                    ),
                    max_len=64,
                ),
                "Account_ID": _safe_str(
                    _column_pick(
                        row,
                        "Account ID",
                        "account_id",
                        "Account_ID",
                        "AccountId",
                    ),
                    max_len=64,
                ),
            }
        )

    df = pd.DataFrame(rows, columns=list(_BARRIERS_COLUMNS))
    return df


def _provenance_barriers_df(message: str) -> pd.DataFrame:
    """Render a single-row provenance frame for the barriers sheet."""

    return pd.DataFrame(
        [
            {
                "_adoptiq_provenance_row": True,
                "AdoptIQ_Status": "EMPTY",
                "AdoptIQ_Source": "be_priority_pipeline.build_be_priority_outputs",
                "AdoptIQ_Message": message,
            }
        ]
    )


def _provenance_focus_df(message: str) -> pd.DataFrame:
    """Render a single-row provenance frame for the focus-areas sheet."""

    return pd.DataFrame(
        [
            {
                "_adoptiq_provenance_row": True,
                "AdoptIQ_Status": "EMPTY",
                "AdoptIQ_Source": "be_priority_scorer.compute_be_focus_areas",
                "AdoptIQ_Message": message,
            }
        ]
    )


def build_be_priority_outputs(
    ab_norm: Optional[pd.DataFrame],
    *,
    risk_profiles: Optional[Dict[str, Any]] = None,
    pulse_df: Optional[pd.DataFrame] = None,
    llm_top_n: int = 50,
    llm_callable: Optional[Callable[[str, str], str]] = None,
    use_llm: bool = True,
    max_per_tech: int = 10,
    min_cluster_score: float = 30.0,
    correlation_id: Optional[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Run the full BE-priority pipeline and return canonical sheets.

    Pipeline contract:

    1. Score ``ab_norm`` deterministically via
       :func:`be_priority_scorer.compute_be_priority_scores_for_frame`.
    2. Take the top-N rows by ``be_priority_score`` and pass them
       through :func:`be_priority_llm_classifier.classify_top_n_be_barriers`
       (the LLM tags inform the narrative but never alter the score).
    3. Project the merged frame into the curated
       ``BE_Priority_Barriers`` schema.
    4. Roll the scored frame up into ``BE_Focus_Areas`` via
       :func:`be_priority_scorer.compute_be_focus_areas`.

    Returns ``(barriers_sheet, focus_areas_sheet, diag)`` where ``diag``
    captures the LLM classifier diagnostic dict plus a small pipeline
    summary (rows scored, top-N selected, classification counts).  The
    diag dict is PII-safe (AB IDs are SHA256 digested) so it can be
    persisted directly into ``analysis_status``.
    """

    diag: Dict[str, Any] = {
        "rows_scored": 0,
        "top_n_selected": 0,
        "llm_diag": {},
    }

    if ab_norm is None or ab_norm.empty:
        msg = (
            "BE-priority pipeline received an empty adoption-barrier frame. "
            "The deterministic scorer was skipped; both sheets are rendered "
            "with provenance rows so the operator sees honest provenance "
            "instead of missing sheets."
        )
        return (
            _provenance_barriers_df(msg),
            _provenance_focus_df(msg),
            diag,
        )

    try:
        scored = bes.compute_be_priority_scores_for_frame(
            ab_norm, risk_profiles=risk_profiles, pulse_df=pulse_df
        )
    except Exception as scorer_err:  # noqa: BLE001
        # Round 79 / B2: hoist sheet assignment OUT of the broad
        # try/except so a scorer failure still ships explicit provenance
        # rows (matching the R67/B2 contract for Risk_Components).
        logger.warning(
            "Round 79 / B2: BE-priority scorer raised %s -- emitting "
            "provenance rows instead of skipping the sheets.",
            scorer_err,
        )
        msg = (
            "BE-priority deterministic scorer failed: "
            f"{str(scorer_err)[:200]}"
        )
        return (
            _provenance_barriers_df(msg),
            _provenance_focus_df(msg),
            diag,
        )

    diag["rows_scored"] = int(len(scored))

    # Step 2: classify the top-N rows via LLM (or stub when use_llm=False).
    if scored.empty:
        msg = (
            "BE-priority deterministic scorer returned no rows for this "
            "scope. The two sheets are rendered with provenance rows."
        )
        return (
            _provenance_barriers_df(msg),
            _provenance_focus_df(msg),
            diag,
        )

    if "be_priority_score" in scored.columns:
        try:
            top_n_df = scored.nlargest(
                int(max(1, llm_top_n)),
                "be_priority_score",
                keep="first",
            )
        except Exception as nlargest_err:  # noqa: BLE001
            logger.debug(
                "Round 79 / B2: nlargest fallback (%s); using head().",
                nlargest_err,
            )
            top_n_df = scored.head(int(max(1, llm_top_n)))
    else:
        top_n_df = scored.head(int(max(1, llm_top_n)))

    diag["top_n_selected"] = int(len(top_n_df))

    classified, llm_diag = bpl.classify_top_n_be_barriers(
        top_n_df,
        n=llm_top_n,
        use_llm=use_llm,
        llm_callable=llm_callable,
        correlation_id=correlation_id,
    )
    diag["llm_diag"] = llm_diag

    # Build a lookup keyed on AB_ID -> {be_class, be_reason, be_confidence}.
    classified_lookup: Dict[str, Dict[str, Any]] = {}
    if isinstance(classified, pd.DataFrame) and not classified.empty:
        id_col = None
        for cand in ("ID", "AB_ID", "Id", "id"):
            if cand in classified.columns:
                id_col = cand
                break
        if id_col:
            for _, crow in classified.iterrows():
                key = crow.get(id_col)
                if key is None:
                    continue
                try:
                    if isinstance(key, float) and pd.isna(key):
                        continue
                except Exception:
                    pass
                key_str = str(key).strip()
                if not key_str:
                    continue
                # Round 79 / B2: ``classify_top_n_be_barriers`` writes
                # the LLM verdict to ``be_llm_class`` / ``be_llm_reason``
                # / ``be_llm_confidence``. Mirror those names here so
                # downstream consumers find the data via the curated
                # ``BE_Class`` / ``BE_Reason`` / ``BE_Confidence`` columns.
                classified_lookup[key_str] = {
                    "be_class": crow.get("be_llm_class") or crow.get("be_class"),
                    "be_reason": crow.get("be_llm_reason") or crow.get("be_reason"),
                    "be_confidence": (
                        crow.get("be_llm_confidence") or crow.get("be_confidence")
                    ),
                }

    barriers_sheet = _build_barriers_dataframe(scored, classified_lookup)

    try:
        focus_sheet = bes.compute_be_focus_areas(
            scored,
            max_per_tech=max_per_tech,
            min_cluster_score=min_cluster_score,
        )
    except Exception as focus_err:  # noqa: BLE001
        logger.warning(
            "Round 79 / B3: BE focus-areas rollup raised %s -- emitting "
            "provenance row instead.",
            focus_err,
        )
        focus_sheet = _provenance_focus_df(
            f"BE focus-areas rollup failed: {str(focus_err)[:200]}"
        )

    return barriers_sheet, focus_sheet, diag


__all__ = [
    "build_be_priority_outputs",
]
