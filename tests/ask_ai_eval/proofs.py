"""Deterministic, evidence-complete proofs for the offline Ask AI replay set.

The replay cassettes are synthetic model outputs.  They must therefore be
derived from the fixture data, never from the values written in the question
predicates.  This module is the verifier-owned boundary:

* row-level statements cite the exact fixture rows that contain the facts;
* aggregates and comparisons are recomputed from the fixture DataFrames and
  exposed as unique ``METRIC-EVAL-*`` evidence records;
* unsupported questions produce an explicit insufficiency statement.

Production's strict claim-entailment filter still makes the final decision.
The records here merely give a correct synthetic answer the same auditable
evidence shape that a production canonical metric receives.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import pandas as pd

import ask_ai_grounded as _grounded


_FIXTURE_TIMESTAMP = "2026-08-04T00:00:00Z"
_CLOSED_STATUS_TERMS = (
    "cancelled",
    "canceled",
    "closed",
    "complete",
    "completed",
    "resolved",
    "successful",
    "unsuccessful",
)
_NEGATIVE_CONTROL_IDS = frozenset({
    "p01_q07",
    "p01_q10",
    "p02_q08",
    "p03_q08",
    "p03_q09",
    "p04_q02",
    "p04_q03",
    "p04_q05",
    "p04_q06",
    "p04_q08",
    "p04_q09",
    "p05_q10",
})
_NEGATIVE_CONTROL_REASONS = {
    "p01_q07": "the controlled fixture has no incident source or completeness declaration",
    "p01_q10": "the fixture has no PSIRT-tagged barrier record or source completeness declaration",
    "p02_q08": "the fixture has no renewal-forecast field or renewal source",
    "p03_q08": "the bounded case rows have no completeness declaration, so absent P1 rows cannot be asserted as zero",
    "p03_q09": "the fixture has no PSIRT-tagged barrier record or source completeness declaration",
    "p04_q02": "the fixture has no support-case rows or source completeness declaration",
    "p04_q03": "the fixture has no pulse rows or source completeness declaration",
    "p04_q05": "the fixture has no PSIRT case rows or source completeness declaration",
    "p04_q06": "the fixture has no churn-outcome source",
    "p04_q08": "the fixture has no renewal-pipeline source",
    "p04_q09": "the barrier fixture has no completeness declaration, so one row cannot prove there are no additional barriers",
    "p05_q10": "the fixture has no PSIRT-tagged case record or source completeness declaration",
}


@dataclass(frozen=True)
class EvalQuestionProof:
    """One replay response plus verifier-owned derived evidence records."""

    response: Dict[str, Any]
    derived_records: Tuple[_grounded.EvidenceRecord, ...] = ()


def metric_source_id(question_id: str, suffix: str = "") -> str:
    """Return a stable exact SourceID for one eval-derived metric."""

    qid = re.sub(r"[^A-Z0-9]+", "-", str(question_id).upper()).strip("-")
    tail = re.sub(r"[^A-Z0-9]+", "-", str(suffix).upper()).strip("-")
    return f"METRIC-EVAL-{qid}{('-' + tail) if tail else ''}"


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def _format_number(value: float | int) -> str:
    number = float(value)
    if math.isfinite(number) and number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _series(df: pd.DataFrame, column: str) -> pd.Series:
    if not isinstance(df, pd.DataFrame) or df.empty or column not in df.columns:
        return pd.Series(dtype="object")
    return df[column].fillna("").astype(str).str.strip()


def _open_mask(df: pd.DataFrame, column: str) -> pd.Series:
    values = _series(df, column).str.casefold()
    if values.empty:
        return pd.Series(False, index=getattr(df, "index", []), dtype=bool)
    return ~values.apply(
        lambda status: any(term in status for term in _CLOSED_STATUS_TERMS)
    )


def _ids(df: pd.DataFrame, *, mask: pd.Series | None = None, id_column: str = "ID") -> List[str]:
    if not isinstance(df, pd.DataFrame) or df.empty or id_column not in df.columns:
        return []
    frame = df
    if mask is not None:
        frame = df.loc[mask.reindex(df.index, fill_value=False).astype(bool)]
    return sorted({_clean(value) for value in frame[id_column] if _clean(value)})


def _record_lookup(
    records: Sequence[_grounded.EvidenceRecord],
) -> Dict[str, _grounded.EvidenceRecord]:
    out: Dict[str, _grounded.EvidenceRecord] = {}
    collisions: set[str] = set()
    for record in records:
        source_id = _grounded._normalize_claim_id(record.source_id)
        if not source_id:
            continue
        if source_id in out:
            collisions.add(source_id)
        else:
            out[source_id] = record
    for source_id in collisions:
        out.pop(source_id, None)
    return out


def _valid_citations(
    lookup: Mapping[str, _grounded.EvidenceRecord],
    source_ids: Iterable[str],
) -> List[str]:
    out: List[str] = []
    for source_id in source_ids:
        clean_id = _clean(source_id)
        if clean_id and _grounded._normalize_claim_id(clean_id) in lookup:
            out.append(clean_id)
    return out


def _metric_record(
    question_id: str,
    statement: str,
    *,
    calculation: str,
    lineage_ids: Sequence[str] = (),
    suffix: str = "",
    customer: str = "Portfolio",
) -> _grounded.EvidenceRecord:
    lineage = ", ".join(lineage_ids) if lineage_ids else "no matching fixture rows"
    return _grounded.EvidenceRecord(
        source_type="EvalDerivedMetric",
        source_id=metric_source_id(question_id, suffix),
        customer=customer,
        timestamp=_FIXTURE_TIMESTAMP,
        text=(
            f"{statement} Calculation: {calculation}. "
            f"Exact fixture SourceIDs: {lineage}."
        ),
        confidence=1.0,
    )


def _claim(statement: str, citations: Iterable[str]) -> Dict[str, Any]:
    return {
        "statement": str(statement).strip(),
        "citations": [str(value).strip() for value in citations if str(value).strip()],
    }


def _response(
    claims: Sequence[Dict[str, Any]],
    *,
    unknowns: Sequence[str] = (),
) -> Dict[str, Any]:
    return {
        "executive_summary": "",
        "claims": list(claims),
        "actions": [],
        "unknowns": list(unknowns),
    }


def _insufficient(question_id: str) -> EvalQuestionProof:
    reason = _NEGATIVE_CONTROL_REASONS.get(
        question_id,
        "the controlled fixture does not contain the required source",
    )
    return EvalQuestionProof(
        response=_response(
            [],
            unknowns=[
                f"Insufficient grounded evidence: {reason}."
            ],
        )
    )


def _metric_proof(
    question_id: str,
    label: str,
    value: float | int,
    *,
    calculation: str,
    lineage_ids: Sequence[str],
    suffix: str = "",
    customer: str = "Portfolio",
    extra_citations: Sequence[str] = (),
) -> EvalQuestionProof:
    statement = f"{label}: {_format_number(value)}."
    record = _metric_record(
        question_id,
        statement,
        calculation=calculation,
        lineage_ids=lineage_ids,
        suffix=suffix,
        customer=customer,
    )
    citations = [record.source_id, *extra_citations]
    return EvalQuestionProof(_response([_claim(statement, citations)]), (record,))


def _open_barrier_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "adoption_barriers", pd.DataFrame())
    mask = _open_mask(df, "STATUS_C")
    row_ids = _ids(df, mask=mask)
    return _metric_proof(
        question_id,
        "Open adoption barriers",
        len(row_ids),
        calculation="distinct nonblank barrier IDs whose fixture status is not closed",
        lineage_ids=row_ids,
        extra_citations=row_ids if question_id == "p04_q01" else (),
    )


def _total_barrier_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "adoption_barriers", pd.DataFrame())
    row_ids = _ids(df)
    return _metric_proof(
        question_id,
        "Total barriers",
        len(row_ids) if row_ids else int(len(df)),
        calculation="distinct nonblank barrier IDs, with row-count fallback",
        lineage_ids=row_ids,
    )


def _p1_case_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "support_cases", pd.DataFrame())
    severity = _series(df, "SEVERITY").str.upper()
    mask = severity.eq("P1") & _open_mask(df, "STATUS")
    row_ids = _ids(df, mask=mask, id_column="CASE_ID")
    return _metric_proof(
        question_id,
        "Open P1 support cases",
        len(row_ids),
        calculation="distinct P1 case IDs whose fixture status is not closed",
        lineage_ids=row_ids,
    )


def _high_severity_case_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "support_cases", pd.DataFrame())
    mask = _series(df, "SEVERITY").str.upper().isin({"P1", "P2"})
    row_ids = _ids(df, mask=mask, id_column="CASE_ID")
    return _metric_proof(
        question_id,
        "High severity P1/P2 support cases",
        len(row_ids),
        calculation="distinct fixture case IDs classified P1 or P2",
        lineage_ids=row_ids,
    )


def _critical_barrier_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "adoption_barriers", pd.DataFrame())
    mask = _series(df, "SEVERITY_C").str.casefold().eq("critical") & _open_mask(
        df, "STATUS_C"
    )
    row_ids = _ids(df, mask=mask)
    return _metric_proof(
        question_id,
        "Open critical severity barriers",
        len(row_ids),
        calculation="distinct Critical barrier IDs whose fixture status is not closed",
        lineage_ids=row_ids,
    )


def _average_pulse_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "customer_pulse", pd.DataFrame())
    values = pd.to_numeric(
        df.get("SCORE__C", pd.Series(dtype="float64")), errors="coerce"
    ).dropna()
    if values.empty:
        return _insufficient(question_id)
    row_ids = _ids(df)
    return _metric_proof(
        question_id,
        "Average pulse score",
        float(values.mean()),
        calculation="arithmetic mean of all nonnull fixture pulse scores",
        lineage_ids=row_ids,
    )


def _customer_names(bundle: Any) -> List[str]:
    names: set[str] = set()
    frames_and_columns = (
        (_value(bundle, "adoption_barriers", pd.DataFrame()), ("BU_NAME",)),
        (_value(bundle, "support_cases", pd.DataFrame()), ("BU_NAME",)),
        (_value(bundle, "customer_pulse", pd.DataFrame()), ("CUSTOMER_NAME__C",)),
        (_value(bundle, "success_priorities", pd.DataFrame()), ("RELATED_CUSTOMER__C",)),
        (_value(bundle, "action_plans", pd.DataFrame()), ("CUSTOMER_BU_NAME__C",)),
    )
    for frame, columns in frames_and_columns:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for column in columns:
            if column not in frame.columns:
                continue
            names.update(_clean(value) for value in frame[column] if _clean(value))
    return sorted(
        name for name in names if name.casefold() not in {"unknown", "unassigned"}
    )


def _total_customer_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    customers = _customer_names(bundle)
    all_ids = sorted(
        set(_ids(_value(bundle, "adoption_barriers", pd.DataFrame())))
        | set(_ids(_value(bundle, "customer_pulse", pd.DataFrame())))
        | set(
            _ids(
                _value(bundle, "support_cases", pd.DataFrame()),
                id_column="CASE_ID",
            )
        )
    )
    return _metric_proof(
        question_id,
        "Total customers",
        len(customers),
        calculation="unique nonblank customer names across all five fixture sources",
        lineage_ids=all_ids,
    )


def _open_action_plan_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "action_plans", pd.DataFrame())
    mask = _open_mask(df, "STATUS_C")
    row_ids = _ids(df, mask=mask)
    return _metric_proof(
        question_id,
        "Open action plans",
        len(row_ids),
        calculation="distinct action-plan IDs whose fixture status is not closed",
        lineage_ids=row_ids,
    )


def _pulse_extreme_proof(
    question_id: str,
    bundle: Any,
    lookup: Mapping[str, _grounded.EvidenceRecord],
    *,
    mode: str,
) -> EvalQuestionProof:
    df = _value(bundle, "customer_pulse", pd.DataFrame()).copy()
    if df.empty or "SCORE__C" not in df.columns:
        return _insufficient(question_id)
    df["_score"] = pd.to_numeric(df["SCORE__C"], errors="coerce")
    df = df.dropna(subset=["_score"])
    if df.empty:
        return _insufficient(question_id)
    target = float(df["_score"].min() if mode == "minimum" else df["_score"].max())
    winners = df.loc[df["_score"].eq(target)].sort_values(
        by=["CUSTOMER_NAME__C", "ID"], kind="mergesort"
    )
    row_ids = [_clean(value) for value in winners["ID"] if _clean(value)]
    citations = _valid_citations(lookup, row_ids)
    customers = [_clean(value) for value in winners["CUSTOMER_NAME__C"] if _clean(value)]
    if not customers or len(citations) != len(row_ids):
        return _insufficient(question_id)
    customer_text = ", ".join(customers)
    statement = (
        f"{customer_text} has the {mode} pulse score of {_format_number(target)}."
        if len(customers) == 1
        else f"{customer_text} tie for the {mode} pulse score of {_format_number(target)}."
    )
    record = _metric_record(
        question_id,
        statement,
        calculation=(
            f"{mode} across all nonnull fixture pulse scores; "
            + "; ".join(f"Customer: {customer}" for customer in customers)
        ),
        lineage_ids=row_ids,
        customer=customers[0] if len(customers) == 1 else "Portfolio",
    )
    return EvalQuestionProof(
        _response([_claim(statement, [record.source_id, *citations])]),
        (record,),
    )


def _comparison_proof(
    question_id: str,
    df: pd.DataFrame,
    lookup: Mapping[str, _grounded.EvidenceRecord],
    *,
    customers: Sequence[str],
    customer_column: str,
    id_column: str,
    label: str,
) -> EvalQuestionProof:
    claims: List[Dict[str, Any]] = []
    derived: List[_grounded.EvidenceRecord] = []
    for customer in customers:
        mask = _series(df, customer_column).eq(customer)
        row_ids = _ids(df, mask=mask, id_column=id_column)
        direct_ids = _valid_citations(lookup, row_ids)
        statement = f"{customer} {label}: {len(row_ids)}."
        record = _metric_record(
            question_id,
            statement,
            calculation=f"distinct {id_column} values for Customer: {customer}",
            lineage_ids=row_ids,
            suffix=customer,
            customer=customer,
        )
        derived.append(record)
        claims.append(_claim(statement, [record.source_id, *direct_ids]))
    return EvalQuestionProof(_response(claims), tuple(derived))


def _direct_claims(
    question_id: str,
    lookup: Mapping[str, _grounded.EvidenceRecord],
    statements: Sequence[Tuple[str, Sequence[str]]],
) -> EvalQuestionProof:
    claims: List[Dict[str, Any]] = []
    for statement, requested_ids in statements:
        citations = _valid_citations(lookup, requested_ids)
        if len(citations) != len(requested_ids):
            return _insufficient(question_id)
        claims.append(_claim(statement, citations))
    return EvalQuestionProof(_response(claims))


def _time_window_claims(
    question_id: str,
    df: pd.DataFrame,
    lookup: Mapping[str, _grounded.EvidenceRecord],
    *,
    id_column: str,
    customer_column: str,
    subject_column: str,
    date_column: str,
    year: int,
    month: int,
    minimum_day: int = 1,
) -> EvalQuestionProof:
    if df.empty:
        return _insufficient(question_id)
    dates = pd.to_datetime(df.get(date_column), errors="coerce")
    mask = dates.dt.year.eq(year) & dates.dt.month.eq(month) & dates.dt.day.ge(minimum_day)
    rows = df.loc[mask].copy()
    rows["_date"] = dates.loc[mask]
    rows = rows.sort_values(by=["_date", id_column], kind="mergesort")
    statements: List[Tuple[str, Sequence[str]]] = []
    for _, row in rows.iterrows():
        source_id = _clean(row.get(id_column))
        subject = _clean(row.get(subject_column))
        customer = _clean(row.get(customer_column))
        opened = row.get("_date")
        if not source_id or pd.isna(opened):
            continue
        statements.append(
            (
                f"{source_id} {subject} for {customer} on {opened:%Y-%m-%d}.",
                [source_id],
            )
        )
    return _direct_claims(question_id, lookup, statements) if statements else _insufficient(question_id)


def _psirt_case_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    df = _value(bundle, "support_cases", pd.DataFrame())
    subjects = _series(df, "SUBJECT")
    mask = subjects.str.contains(r"(?:psirt|cscwk|cisco-sa)", case=False, regex=True) & _open_mask(
        df, "STATUS"
    )
    row_ids = _ids(df, mask=mask, id_column="CASE_ID")
    return _metric_proof(
        question_id,
        "Open PSIRT-related cases",
        len(row_ids),
        calculation="distinct open case IDs whose subject contains PSIRT, CSCwk, or cisco-sa",
        lineage_ids=row_ids,
    )


def _canonical_proof(question_id: str, bundle: Any) -> EvalQuestionProof:
    suffix = question_id.rsplit("_q", 1)[-1]
    if suffix == "11":
        return _open_barrier_proof(question_id, bundle)
    if suffix == "12":
        return _total_customer_proof(question_id, bundle)
    if suffix == "13":
        return _open_action_plan_proof(question_id, bundle)
    if suffix == "14":
        return _high_severity_case_proof(question_id, bundle)
    if suffix == "15":
        return _total_barrier_proof(question_id, bundle)
    return _insufficient(question_id)


def build_question_proof(
    question: Any,
    bundle: Any,
    records: Sequence[_grounded.EvidenceRecord],
) -> EvalQuestionProof:
    """Compute the exact replay answer from fixture rows, not predicates."""

    question_id = str(_value(question, "id", "")).strip()
    lookup = _record_lookup(records)
    if not question_id:
        return _insufficient("unknown-question")
    if question_id in _NEGATIVE_CONTROL_IDS:
        return _insufficient(question_id)
    if question_id.endswith(tuple(f"_q{number}" for number in range(11, 16))):
        return _canonical_proof(question_id, bundle)

    # Aggregate KPIs recomputed directly from fixture rows.
    if question_id in {"p01_q01", "p02_q04", "p03_q01", "p04_q01", "p05_q02"}:
        return _open_barrier_proof(question_id, bundle)
    if question_id in {"p01_q04", "p02_q05", "p03_q08", "p05_q06"}:
        return _p1_case_proof(question_id, bundle)
    if question_id == "p03_q03":
        return _critical_barrier_proof(question_id, bundle)
    if question_id in {"p01_q06", "p03_q10"}:
        return _average_pulse_proof(question_id, bundle)
    if question_id == "p02_q01":
        return _psirt_case_proof(question_id, bundle)

    # Min/max questions combine an independently computed metric row with the
    # exact winning fixture row(s).
    if question_id in {"p01_q02", "p02_q03", "p05_q05"}:
        return _pulse_extreme_proof(question_id, bundle, lookup, mode="minimum")
    if question_id == "p05_q04":
        return _pulse_extreme_proof(question_id, bundle, lookup, mode="maximum")

    ab_df = _value(bundle, "adoption_barriers", pd.DataFrame())
    case_df = _value(bundle, "support_cases", pd.DataFrame())
    pulse_df = _value(bundle, "customer_pulse", pd.DataFrame())

    # Row-level source lookup / citation checks.
    direct: Dict[str, Sequence[Tuple[str, Sequence[str]]]] = {
        "p01_q03": [("AB-EVAL-010 executive sponsor disengaged for AcmeEvalCorp.", ["AB-EVAL-010"])],
        "p01_q09": [("CASE-EVAL-105 SSO integration failure for EpsilonEvalGroup with severity P1.", ["CASE-EVAL-105"])],
        "p02_q02": [("CASE-EVAL-201 CSCwk12345 patch verification for SecureEvalCorp.", ["CASE-EVAL-201"])],
        "p02_q09": [("CASE-EVAL-204 PSIRT advisory cisco-sa-2026-001 for LockEvalSystems.", ["CASE-EVAL-204"])],
        "p04_q04": [("SilentEvalCorp barrier status Open: Routine quarterly review.", ["AB-EVAL-401"])],
        "p04_q07": [("AB-EVAL-401 Routine quarterly review for SilentEvalCorp.", ["AB-EVAL-401"])],
        "p04_q10": [("SilentEvalCorp barrier severity Low.", ["AB-EVAL-401"])],
        "p05_q07": [("CASE-EVAL-510 deployment escalation for CmpEvalDelta.", ["CASE-EVAL-510"])],
        "p05_q08": [(
            "CmpEvalAlpha Webex Calling rollout phase 2 status InProgress with score 3.5.",
            ["AB-EVAL-501", "PULSE-EVAL-501"],
        )],
    }
    if question_id in direct:
        return _direct_claims(question_id, lookup, direct[question_id])

    # Pairwise comparisons are derived counts with exact row lineage.
    if question_id == "p01_q05":
        return _comparison_proof(
            question_id,
            ab_df,
            lookup,
            customers=("BetaEvalInc", "GammaEvalCo"),
            customer_column="BU_NAME",
            id_column="ID",
            label="adoption barrier count",
        )
    if question_id == "p02_q10":
        return _comparison_proof(
            question_id,
            case_df,
            lookup,
            customers=("SecureEvalCorp", "ShieldEvalInc"),
            customer_column="BU_NAME",
            id_column="CASE_ID",
            label="support case count",
        )
    if question_id == "p03_q05":
        return _comparison_proof(
            question_id,
            ab_df,
            lookup,
            customers=("AdoptEvalCust01", "AdoptEvalCust05"),
            customer_column="BU_NAME",
            id_column="ID",
            label="adoption barrier count",
        )
    if question_id == "p05_q01":
        return _comparison_proof(
            question_id,
            ab_df,
            lookup,
            customers=("CmpEvalAlpha", "CmpEvalBeta"),
            customer_column="BU_NAME",
            id_column="ID",
            label="adoption barrier count",
        )

    if question_id == "p05_q03":
        counts = (
            ab_df.groupby("BU_NAME")["ID"].nunique().sort_index()
            if not ab_df.empty else pd.Series(dtype="int64")
        )
        if counts.empty:
            return _insufficient(question_id)
        maximum = int(counts.max())
        winners = sorted(str(name) for name, value in counts.items() if int(value) == maximum)
        representative_ids: List[str] = []
        for customer in winners:
            mask = _series(ab_df, "BU_NAME").eq(customer)
            customer_ids = _ids(ab_df, mask=mask)
            if customer_ids:
                representative_ids.append(customer_ids[0])
        statement = (
            f"{', '.join(winners)} tie for maximum adoption barrier count: {maximum}."
        )
        record = _metric_record(
            question_id,
            statement,
            calculation=(
                "maximum distinct barrier IDs per customer; "
                + "; ".join(f"Customer: {customer}" for customer in winners)
            ),
            lineage_ids=_ids(ab_df),
        )
        return EvalQuestionProof(
            _response([
                _claim(
                    statement,
                    [record.source_id, *_valid_citations(lookup, representative_ids)],
                )
            ]),
            (record,),
        )

    # Cross-source intersections retain the exact contributing rows.
    if question_id == "p02_q06":
        open_ab = ab_df.loc[_open_mask(ab_df, "STATUS_C")]
        open_p1 = case_df.loc[
            _series(case_df, "SEVERITY").str.upper().eq("P1")
            & _open_mask(case_df, "STATUS")
        ]
        customers = sorted(
            set(_series(open_ab, "BU_NAME")) & set(_series(open_p1, "BU_NAME"))
        )
        statements: List[Tuple[str, Sequence[str]]] = []
        for customer in customers:
            ab_id = _ids(open_ab, mask=_series(open_ab, "BU_NAME").eq(customer))[0]
            case_id = _ids(
                open_p1,
                mask=_series(open_p1, "BU_NAME").eq(customer),
                id_column="CASE_ID",
            )[0]
            statements.append(
                (f"{customer} has an Open barrier and an Open P1 case.", [ab_id, case_id])
            )
        return _direct_claims(question_id, lookup, statements)

    if question_id == "p03_q06":
        critical = ab_df.loc[_series(ab_df, "SEVERITY_C").str.casefold().eq("critical")]
        scored = pulse_df.copy()
        scored["_score"] = pd.to_numeric(scored.get("SCORE__C"), errors="coerce")
        scored = scored.loc[scored["_score"].lt(3)]
        customers = sorted(
            set(_series(critical, "BU_NAME"))
            & set(_series(scored, "CUSTOMER_NAME__C"))
        )
        statements = []
        for customer in customers:
            ab_id = _ids(critical, mask=_series(critical, "BU_NAME").eq(customer))[0]
            pulse_rows = scored.loc[_series(scored, "CUSTOMER_NAME__C").eq(customer)]
            pulse_id = _ids(pulse_rows)[0]
            score = float(pulse_rows.iloc[0]["_score"])
            statements.append(
                (f"{customer} has a Critical barrier and a score of {_format_number(score)}.", [ab_id, pulse_id])
            )
        return _direct_claims(question_id, lookup, statements)

    # Complete row enumerations for the requested time windows / subjects.
    if question_id == "p01_q08":
        return _time_window_claims(
            question_id,
            ab_df,
            lookup,
            id_column="ID",
            customer_column="BU_NAME",
            subject_column="SUBJECT_C",
            date_column="OPEN_DATE_C",
            year=2026,
            month=2,
        )
    if question_id == "p02_q07":
        return _time_window_claims(
            question_id,
            case_df,
            lookup,
            id_column="CASE_ID",
            customer_column="BU_NAME",
            subject_column="SUBJECT",
            date_column="OPEN_DATE",
            year=2026,
            month=2,
            minimum_day=20,
        )
    if question_id == "p03_q07":
        return _time_window_claims(
            question_id,
            ab_df,
            lookup,
            id_column="ID",
            customer_column="BU_NAME",
            subject_column="SUBJECT_C",
            date_column="OPEN_DATE_C",
            year=2026,
            month=3,
        )
    if question_id == "p05_q09":
        return _time_window_claims(
            question_id,
            ab_df,
            lookup,
            id_column="ID",
            customer_column="BU_NAME",
            subject_column="SUBJECT_C",
            date_column="OPEN_DATE_C",
            year=2026,
            month=2,
            minimum_day=20,
        )

    if question_id == "p03_q02":
        rows = ab_df.loc[_series(ab_df, "SUBJECT_C").str.casefold().eq(
            "webex calling pilot stalled"
        )]
        statements = [
            (
                f"{_clean(row['BU_NAME'])}: Webex Calling pilot stalled ({_clean(row['ID'])}).",
                [_clean(row["ID"])],
            )
            for _, row in rows.sort_values("ID", kind="mergesort").iterrows()
        ]
        return _direct_claims(question_id, lookup, statements)

    if question_id == "p03_q04":
        rows = ab_df.loc[_series(ab_df, "SUBJECT_C").str.casefold().eq(
            "sso migration in flight"
        )]
        statements = [
            (
                f"{_clean(row['ID'])} SSO migration in flight for {_clean(row['BU_NAME'])}.",
                [_clean(row["ID"])],
            )
            for _, row in rows.sort_values("ID", kind="mergesort").iterrows()
        ]
        return _direct_claims(question_id, lookup, statements)

    return _insufficient(question_id)


__all__ = [
    "EvalQuestionProof",
    "build_question_proof",
    "metric_source_id",
]
