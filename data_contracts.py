"""Shared data contracts for cross-report consistency payloads.

Phase 4.1 expansion: in addition to the rolled-up portfolio /
defects / consistency contracts that already lived here, every
upstream **row-level** dataset that feeds a report now declares the
columns it MUST carry. Loaders should call
``validate_row_contract`` immediately after fetching, so schema drift
(renamed columns, dropped fields) is surfaced loudly instead of
quietly producing a zero count downstream.

The contracts intentionally stay minimal — only the columns the
report logic actually depends on — so harmless additions to the
source schema do not break the build.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, TypedDict

import pandas as pd

logger = logging.getLogger(__name__)


# Round 32 / Phase 1.A: Snowflake custom-object namespacing prefixes
# we strip during column-name normalization so a real column like
# ``AB_C__BU_NAME`` can satisfy the ``customer`` slot whose alias list
# only contains ``BU_NAME``.  These prefixes were observed silently
# wiping the adoption_barriers / customer_pulse contracts in build6
# (see ~/.adoptiq/adoptiq.46198.log lines 174 / 172).  Trailing ``_C``
# / ``__C`` is the Salesforce custom-field marker that some Snowflake
# views strip and others leave in place.
_NORMALIZE_PREFIXES: Tuple[str, ...] = (
    "AB_C__", "AB__", "BARRIER_C__", "BARRIER__",
    "PULSE_C__", "PULSE__", "CASE_C__", "CASE__",
    "ACCOUNT_C__", "ACCOUNT__", "CSONE_C__", "CSONE__",
)
_NORMALIZE_SUFFIXES: Tuple[str, ...] = ("__C", "_C")
_BOM_OR_WS_RE = re.compile(r"[\s\ufeff\u200b]+")


def _normalize_column_name(name: Any) -> str:
    """Return a normalized form of ``name`` for resilient alias matching.

    Round 32 / Phase 1.A: strips whitespace + BOM/zero-width chars,
    case-folds, strips common Snowflake namespacing prefixes, and
    strips Salesforce ``__C`` / ``_C`` suffixes.  This is *only* used
    by ``validate_row_contract``'s alias resolver — the actual column
    in the DataFrame is left untouched, so callers that read the raw
    upstream name continue to work.
    """
    if name is None:
        return ""
    s = _BOM_OR_WS_RE.sub("", str(name)).lower()
    if not s:
        return ""
    for prefix in _NORMALIZE_PREFIXES:
        p = prefix.lower()
        if s.startswith(p):
            s = s[len(p):]
            break
    for suffix in _NORMALIZE_SUFFIXES:
        sfx = suffix.lower()
        if s.endswith(sfx) and len(s) > len(sfx):
            s = s[: -len(sfx)]
            break
    return s


# ---------------------------------------------------------------------------
# Roll-up payload contracts (existing)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Row-level dataset contracts (Phase 4.1)
# ---------------------------------------------------------------------------


# Each contract enumerates the columns the loader MUST produce. Any
# acceptable alias for a required column belongs in
# ``ROW_CONTRACT_ALIASES`` below so legacy upstream names continue to
# satisfy the contract without renaming.
ROW_CONTRACTS: Dict[str, Dict[str, Sequence[str]]] = {
    "subscriptions": {
        # Customer / account identity
        "customer": ("BU_NAME", "customer_name", "ACCOUNT_NAME", "ACCOUNT_ID_C"),
        # Subscription identity & state are required so renewal,
        # leader, and ARR analytics can group rows.
        "subscription": ("SUBSCRIPTION_NUMBER", "SUB_REF_ID", "SUBSCRIPTION_ID"),
        # ARR / value figure used by every dollarized aggregation.
        "arr": ("ANNUAL_RECURRING_REVENUE", "TOTAL_VALUE", "ARR", "ARR_USD"),
    },
    "adoption_barriers": {
        "id": ("ID", "RECORD_ID", "ADOPTION_BARRIER_ID"),
        # Round 48 / F-DV-PULSE-CONTRACT-DRIFT: when the
        # ``C360_CS_TASK_C_VW`` view is loaded without a JOIN to
        # ``dsm_assignment_data``, the only customer-bearing column
        # is ``ACCOUNT_ID_C``.  Annotators / loaders that resolve
        # the customer name post-fetch stamp it as ``Customer`` /
        # ``Customer Name`` (friendly) or ``customer`` (canonical
        # already populated).  Accept all three variants so the
        # contract passes once the customer name is materialized
        # rather than escalating an unrelated schema_drift.
        "customer": (
            "BU_NAME", "customer_name", "ACCOUNT_NAME",
            "Customer", "Customer Name", "BU_ACCOUNT_NAME",
            "customer", "CUSTOMER",
        ),
        "subject": ("SUBJECT_C", "subject", "TITLE"),
        "status": ("AB_STATUS_C", "STATUS_C", "STATUS"),
        "severity": ("SEVERITY_C", "severity_c", "Severity", "PRIORITY", "severity_norm"),
    },
    "tac_cases": {
        "case_id": ("Case #", "CASE_NUMBER", "CASE_ID", "Case Number", "SR Number"),
        # Round 48 / F-DV-PULSE-CONTRACT-DRIFT: parity with
        # adoption_barriers -- accept friendly and canonical
        # variants resolved post-fetch.
        "customer": (
            "customer_name", "BU_NAME", "Account Name", "ACCOUNT_NAME",
            "Customer", "Customer Name", "BU_ACCOUNT_NAME",
            "customer", "CUSTOMER",
        ),
        "severity": ("Severity", "SEVERITY_C", "PRIORITY", "Priority"),
        "status": ("Status", "STATUS_C", "STATUS", "Case Status"),
    },
    "customer_pulse": {
        # Round 48 / F-DV-PULSE-CONTRACT-DRIFT: ``ESA_C360_CUSTOMER_PULSE__C``
        # joins to ``dsm_assignment_data`` to deliver ``BU_NAME``,
        # but some upstream paths (``CUSTOMER_NAME__C``,
        # legacy ad-hoc JOINs that drop the dsm side, or post-fetch
        # canonical-slot remap) deliver the customer name under a
        # different label.  Accept the documented friendly forms.
        "customer": (
            "BU_NAME", "customer_name", "ACCOUNT_NAME",
            "CUSTOMER_NAME__C", "Customer", "Customer Name",
            "customer", "CUSTOMER",
        ),
        # Round 48 / F-DV-PULSE-CONTRACT-DRIFT: ``ask_ai_grounded.py``
        # documents ``SCORE__C``, ``SCORE_C``, and ``PULSE_RATING__C``
        # as the historic rating columns.  The Excel friendly label
        # is ``Pulse Rating`` (see ``report_export_schema.py``
        # ~L222).  Accept all of them so the contract passes
        # whenever any of these arrive on the frame.
        "rating": (
            "PULSE_RATING__C", "PULSE_RATING", "Rating", "RATING",
            "SCORE__C", "SCORE_C", "Score", "SCORE",
            "Pulse Rating", "pulse_rating", "rating",
            "OVERALL_RATING__C", "OVERALL_RATING",
        ),
    },
    "bems_rows": {
        # BEMS escalations live inside the TAC case data and are
        # detected by the presence of these reference columns.
        "case_id": ("Case #", "CASE_NUMBER", "CASE_ID"),
        "bems_ref": ("Transaction ID", "bemscsc_refs", "BEMS_REFS"),
    },
}


# Optional aliases let a loader satisfy a contract slot using any of
# multiple historical column names. The validator passes when AT LEAST
# ONE of the listed aliases is present for each contract slot.
ROW_CONTRACT_ALIASES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    name: {slot: tuple(aliases) for slot, aliases in slots.items()}
    for name, slots in ROW_CONTRACTS.items()
}


def list_row_contracts() -> List[str]:
    """Return the list of dataset names that have row-level contracts."""
    return sorted(ROW_CONTRACTS.keys())


def required_columns(dataset: str) -> List[str]:
    """Return the contract slot names (canonical, not aliases) for ``dataset``."""
    return list(ROW_CONTRACTS.get(dataset, {}).keys())


def validate_row_contract(
    df: Optional[pd.DataFrame],
    *,
    dataset: str,
    raise_on_missing: bool = False,
) -> Dict[str, Any]:
    """Validate that ``df`` carries at least one acceptable column for
    every contract slot of ``dataset``.

    Returns a dict ``{is_valid, missing_slots, present_columns,
    error_message}``. When ``raise_on_missing`` is True and the
    contract is violated, raises :class:`ValueError`.

    A missing/empty DataFrame is treated as a soft-pass when the
    DataFrame already carries a ``fetch_error`` ``.attrs`` flag
    (Phase 1.3a) — the failure is owned by the fetcher, not the
    schema. This avoids double-reporting the same incident.
    """
    if dataset not in ROW_CONTRACTS:
        return {
            "is_valid": False,
            "missing_slots": [],
            "present_columns": [],
            "error_message": f"Unknown dataset contract: {dataset!r}",
        }

    if df is None:
        # Treat as soft pass; the loader contract will surface the missing data.
        return {
            "is_valid": False,
            "missing_slots": list(required_columns(dataset)),
            "present_columns": [],
            "error_message": f"{dataset}: DataFrame is None",
        }

    attrs = getattr(df, "attrs", {}) or {}
    if attrs.get("fetch_error"):
        return {
            "is_valid": True,
            "missing_slots": [],
            "present_columns": list(df.columns),
            "error_message": f"{dataset}: fetch_error already flagged; skipping schema check",
        }

    if df.empty:
        # Empty frame can still be schema-conformant; verify columns exist.
        pass

    # Round 7 / Phase 2.7: alias resolution is now case-insensitive.
    # Round 32 / Phase 1.A: alias resolution also strips Snowflake
    # namespacing prefixes (``AB_C__``, ``PULSE_C__``, ...) and
    # Salesforce ``__C`` / ``_C`` suffixes via ``_normalize_column_name``,
    # because build6 silently lost adoption_barriers / customer_pulse
    # / tac_cases / bems_rows in production runs when upstream views
    # delivered prefix-namespaced columns.  The first matching alias
    # for each slot is recorded in ``matched_columns`` so the
    # annotator can copy the value into a canonical-name column for
    # downstream consumers that read ``df["customer"]`` directly.
    columns_raw: List[str] = [str(c) for c in df.columns]
    columns_set: Set[str] = set(columns_raw)
    columns_ci_map: Dict[str, str] = {c.lower(): c for c in columns_raw}
    columns_norm_map: Dict[str, str] = {}
    for c in columns_raw:
        norm = _normalize_column_name(c)
        if norm and norm not in columns_norm_map:
            columns_norm_map[norm] = c

    missing_slots: List[str] = []
    matched_columns: Dict[str, str] = {}
    for slot, aliases in ROW_CONTRACT_ALIASES[dataset].items():
        matched_col: Optional[str] = None
        for alias in aliases:
            alias_str = str(alias)
            if alias_str in columns_set:
                matched_col = alias_str
                break
            ci_hit = columns_ci_map.get(alias_str.lower())
            if ci_hit is not None:
                matched_col = ci_hit
                logger.debug(
                    "[[CONTRACT]] Round 7 / Phase 2.7: %s slot %r matched "
                    "alias %r via case-insensitive lookup (actual column %r).",
                    dataset, slot, alias_str, ci_hit,
                )
                break
            norm_hit = columns_norm_map.get(_normalize_column_name(alias_str))
            if norm_hit is not None:
                matched_col = norm_hit
                logger.debug(
                    "[[CONTRACT]] Round 32 / Phase 1.A: %s slot %r matched "
                    "alias %r via normalized lookup (actual column %r).",
                    dataset, slot, alias_str, norm_hit,
                )
                break
        if matched_col is None:
            missing_slots.append(slot)
        else:
            matched_columns[slot] = matched_col

    is_valid = not missing_slots
    error_message = ""
    if not is_valid:
        details = []
        for slot in missing_slots:
            aliases = ", ".join(ROW_CONTRACT_ALIASES[dataset][slot])
            details.append(f"slot {slot!r} (one of: {aliases})")
        error_message = (
            f"Row contract violation for {dataset}: missing required slot(s) — "
            + "; ".join(details)
        )
        logger.warning("[[CONTRACT]] %s", error_message)
        if raise_on_missing:
            raise ValueError(error_message)

    # Round 14 / Phase 2.1: previously this returned ``sorted(columns)``,
    # but ``columns`` was never bound in this function -- the actual
    # column list lives in ``columns_raw``.  Every caller that hit the
    # success-return path (i.e. all required slots present) raised
    # ``NameError``; the broad ``except`` in callers like
    # ``annotate_with_contract`` turned it into a debug log and the
    # contract drift signal was silently dropped.  Use ``columns_raw``.
    return {
        "is_valid": is_valid,
        "missing_slots": missing_slots,
        "present_columns": sorted(columns_raw),
        "matched_columns": matched_columns,
        "row_count": int(len(df.index)),
        "error_message": error_message,
    }


def annotate_with_contract(
    df: pd.DataFrame,
    *,
    dataset: str,
    raise_on_missing: bool = False,
) -> pd.DataFrame:
    """Run the row contract and stash the result onto ``df.attrs`` so
    downstream callers can detect schema drift without re-running the
    check. Returns ``df`` for chaining.

    Round 32 / Phase 1.A behavior:

    * For every slot that resolved to an actual column, copy that
      column's values into a canonical-name column (e.g. ``customer``
      from ``BU_NAME``) iff the canonical name is not already present.
      Downstream code that dereferences ``df["customer"]`` then
      stops silently emitting empty sections when upstream uses a
      legacy or namespaced name.

    * If a non-empty frame fails the contract AND it does not already
      carry a ``fetch_error`` annotation (the fetcher already owns
      that signal for fetch failures), stamp
      ``df.attrs['fetch_error']`` + ``fetch_error_kind='schema_drift'``
      so the existing report banners surface the issue instead of
      silently producing an empty section.

    * Empty frames with missing slots are *not* escalated to
      ``fetch_error``: zero rows is a legitimate query result and the
      fetcher would have set ``fetch_error`` itself if the upstream
      call truly failed.
    """
    if df is None:
        return df
    result = validate_row_contract(df, dataset=dataset, raise_on_missing=raise_on_missing)

    matched_cols = result.get("matched_columns") or {}
    if isinstance(df, pd.DataFrame) and matched_cols:
        for slot, src_col in matched_cols.items():
            try:
                if not slot or slot in df.columns or src_col not in df.columns:
                    continue
                df[slot] = df[src_col]
            except Exception as _copy_err:  # noqa: BLE001 - never break callers
                logger.debug(
                    "[[CONTRACT]] Round 32 / Phase 1.A: failed to copy "
                    "%s -> canonical %r on %s: %s",
                    src_col, slot, dataset, _copy_err,
                )

    soft_pass = (not result["is_valid"]) and (result.get("row_count", 0) == 0)

    contract_blob = {
        "dataset": dataset,
        "is_valid": result["is_valid"],
        "missing_slots": result["missing_slots"],
        "matched_columns": dict(matched_cols),
        "row_count": int(result.get("row_count", 0) or 0),
        "soft_pass": bool(soft_pass),
        "error_message": result["error_message"],
    }
    # Round 2 / Phase 4.4: a single frame can satisfy more than one
    # contract (e.g. CSOne load is both ``tac_cases`` and
    # ``bems_rows``).  Keep the legacy ``row_contract`` slot pointing
    # at the most recently stamped dataset for back-compat, but also
    # accumulate every stamp in ``row_contracts`` so consistency
    # checks can iterate without losing the earlier annotation.
    df.attrs["row_contract"] = contract_blob
    existing = df.attrs.get("row_contracts")
    if not isinstance(existing, list):
        existing = []
    existing = [c for c in existing if c.get("dataset") != dataset]
    existing.append(contract_blob)
    df.attrs["row_contracts"] = existing

    if (not result["is_valid"]) and not soft_pass and not df.attrs.get("fetch_error"):
        missing = ",".join(result["missing_slots"]) or "unknown"
        df.attrs["fetch_error"] = (
            f"schema_drift:{dataset}: missing slot(s) {missing} on a "
            f"non-empty result ({contract_blob['row_count']} row(s)); "
            "upstream column names changed - report banner will surface this."
        )
        df.attrs["fetch_error_kind"] = "schema_drift"
        df.attrs.setdefault("fetch_error_dataset", dataset)
        logger.warning(
            "[[CONTRACT]] Round 32 / Phase 1.A: escalated schema_drift to "
            "fetch_error on %s (%d row(s), missing slots: %s)",
            dataset, contract_blob['row_count'], missing,
        )

    return df


__all__ = [
    "PortfolioMetricsContract",
    "DefectsContract",
    "ConsistencyResultContract",
    "ROW_CONTRACTS",
    "ROW_CONTRACT_ALIASES",
    "annotate_with_contract",
    "list_row_contracts",
    "required_columns",
    "validate_row_contract",
]
