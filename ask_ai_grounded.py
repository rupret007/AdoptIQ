#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Grounded Ask AI pipeline.

This module provides a retrieval-first pipeline for Ask AI responses:
1) Plan retrieval scope from question intent.
2) Fetch only relevant datasets (query-cost control).
3) Normalize records into evidence units with verifiable IDs.
4) Build bounded context for the model.
5) Parse structured JSON answer and strictly validate citations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

from data_normalization import extract_bems_ids_from_text
from snowflake_prefetch import AnalysisRunContext, prefetch_ask_ai_grounded

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it",
    "of", "on", "or", "that", "the", "to", "was", "what", "when", "where", "which", "who", "with",
}

_CLAIM_ID_RE = re.compile(
    r"\b(?:CSC[A-Z0-9]{6,10}|BEMS[A-Z0-9-]{4,}|INC[-A-Z0-9]+|SP[-_A-Z0-9:]+|AP[-_A-Z0-9:]+|CASE[-_A-Z0-9:]+|AB[-_A-Z0-9:]+)\b",
    flags=re.IGNORECASE,
)

_QUESTION_DOMAIN_RULES: Dict[str, Tuple[str, ...]] = {
    "contracts": ("renewal", "contract", "churn", "risk", "at risk"),
    "barriers": ("barrier", "adoption", "severity", "customer pulse", "friction"),
    "cases": ("case", "tac", "sr", "p1", "p2", "escalation", "bems"),
    "trends": ("trend", "velocity", "week", "change", "compare", "historical"),
    "intel": ("incident", "maintenance", "bug", "defect", "status.webex", "help.webex", "csc"),
    "execution": ("action plan", "success priority", "next step", "recommendation"),
}

_DATASETS_BY_DOMAIN: Dict[str, Set[str]] = {
    "core": {
        "support_cases_snowflake",
        "csconsole_customer_pulse",
        "csconsole_success_priorities",
        "csconsole_action_plans",
        # Owner-aware barrier fetcher so collaborator-authored ABs on
        # non-primary accounts are captured (parity with manager report).
        "csconsole_adoption_barriers",
    },
    "contracts": {"enhanced_account_insights"},
    "trends": {"period_comparison", "barrier_velocity"},
}


@dataclass(frozen=True)
class AskAIRequest:
    question: str
    manager: str
    technology: str
    days: int


@dataclass(frozen=True)
class EvidenceRecord:
    source_type: str
    source_id: str
    customer: str
    timestamp: str
    text: str
    confidence: float = 0.8


def is_grounded_ask_ai_enabled() -> bool:
    """Enable the grounded Ask AI path by default with env rollback support."""
    return str(os.environ.get("ADOPTIQ_ASK_AI_V2", "1")).strip().lower() in {"1", "true", "yes", "on"}


def _question_terms(question: str) -> Set[str]:
    raw = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", (question or "").lower())
    return {tok for tok in raw if len(tok) >= 3 and tok not in _STOP_WORDS}


def build_retrieval_plan(question: str) -> Dict[str, Any]:
    text = (question or "").lower()
    domains: Set[str] = {"core"}
    for domain, keywords in _QUESTION_DOMAIN_RULES.items():
        if any(keyword in text for keyword in keywords):
            domains.add(domain)
    datasets: Set[str] = set()
    for domain in domains:
        datasets.update(_DATASETS_BY_DOMAIN.get(domain, set()))
    return {
        "domains": sorted(domains),
        "datasets": sorted(datasets),
        "terms": sorted(_question_terms(question)),
    }


def _first_present(row: pd.Series, columns: Sequence[str], default: str = "") -> str:
    for col in columns:
        if col in row.index:
            value = row.get(col)
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() != "nan":
                return text
    return default


def _normalize_claim_id(value: str) -> str:
    clean = re.sub(r"\s+", "", str(value or "").upper())
    clean = clean.replace("ID:", "").replace("CASE#", "CASE")
    return clean


def _extract_ids_from_text(text: str) -> Set[str]:
    values = {_normalize_claim_id(m.group(0)) for m in _CLAIM_ID_RE.finditer(str(text or ""))}
    for bems_id in extract_bems_ids_from_text(str(text or "")):
        values.add(_normalize_claim_id(bems_id))
    return {v for v in values if v}


def _records_from_dataframe(
    df: Optional[pd.DataFrame],
    source_type: str,
    id_columns: Sequence[str],
    text_columns: Sequence[str],
    customer_columns: Sequence[str],
    timestamp_columns: Sequence[str],
    max_rows: int = 120,
    id_prefix: str = "",
) -> Tuple[List[EvidenceRecord], Set[str]]:
    if df is None or df.empty:
        return [], set()
    # Round 4: present human-readable column names to the LLM rather
    # than the raw Snowflake/CSConsole schema names.  This prevents
    # quoted evidence lines from carrying confusing identifiers like
    # ``SUBJECT_C`` or ``RELATED_CUSTOMER__C`` which the model has been
    # observed to echo verbatim into its narrative.
    _SCHEMA_LABELS: Dict[str, str] = {
        "SUBJECT_C": "Subject",
        "SUBJECT": "Subject",
        "DESCRIPTION_C": "Description",
        "DESCRIPTION": "Description",
        "RELATED_CUSTOMER__C": "Customer",
        "CUSTOMER_BU_NAME__C": "Customer",
        "BU_NAME": "Customer",
        "ACCOUNT_NAME": "Account",
        "ACCOUNT_ID_C": "Account ID",
        "PRIORITY_C": "Priority",
        "PRIORITY": "Priority",
        "SEVERITY_C": "Severity",
        "STATUS_C": "Status",
        "STATUS": "Status",
        "CASE_NUMBER": "Case Number",
        "BARRIER_TYPE_C": "Barrier Type",
        "ROOT_CAUSE_C": "Root Cause",
        "RESOLUTION_C": "Resolution",
        "OWNER_NAME_C": "Owner",
        "OWNER_C": "Owner",
        "CREATED_DATE": "Created",
        "CLOSED_DATE": "Closed",
        "LAST_MODIFIED_DATE": "Last Modified",
    }
    records: List[EvidenceRecord] = []
    citation_ids: Set[str] = set()
    for _, row in df.head(max_rows).iterrows():
        source_id = _first_present(row, id_columns, default="")
        if source_id and id_prefix and not source_id.upper().startswith(id_prefix.upper()):
            source_id = f"{id_prefix}{source_id}"
        customer = _first_present(row, customer_columns, default="Unknown")
        timestamp = _first_present(row, timestamp_columns, default="")
        detail_parts: List[str] = []
        for col in text_columns:
            if col in row.index:
                value = row.get(col)
                if value is None:
                    continue
                clean = str(value).strip()
                if clean and clean.lower() != "nan":
                    label = _SCHEMA_LABELS.get(str(col).upper(), str(col))
                    detail_parts.append(f"{label}: {clean}")
        text = " | ".join(detail_parts) if detail_parts else f"{source_type} record"
        records.append(
            EvidenceRecord(
                source_type=source_type,
                source_id=source_id or f"{source_type}-UNSPECIFIED",
                customer=customer,
                timestamp=timestamp,
                text=text,
            )
        )
        if source_id:
            citation_ids.add(_normalize_claim_id(source_id))
        citation_ids.update(_extract_ids_from_text(text))
    return records, citation_ids


def rank_evidence(records: Sequence[EvidenceRecord], question: str, domains: Sequence[str]) -> List[EvidenceRecord]:
    terms = _question_terms(question)
    domain_text = " ".join(domains).lower()

    def _score(record: EvidenceRecord) -> float:
        source_blob = f"{record.source_type} {record.source_id} {record.customer} {record.text}".lower()
        term_hits = sum(1 for t in terms if t in source_blob)
        domain_bonus = 2.0 if record.source_type.lower() in domain_text else 0.0
        id_bonus = 1.25 if record.source_id and "UNSPECIFIED" not in record.source_id else 0.0
        customer_bonus = 0.5 if record.customer and record.customer != "Unknown" else 0.0
        return (term_hits * 1.5) + domain_bonus + id_bonus + customer_bonus + max(min(record.confidence, 1.0), 0.0)

    return sorted(records, key=_score, reverse=True)


def build_evidence_context(
    records: Sequence[EvidenceRecord],
    question: str,
    domains: Sequence[str],
    char_budget: int = 42000,
    max_records: int = 220,
) -> Tuple[str, Set[str], int]:
    ranked = rank_evidence(records, question, domains)
    kept: List[str] = []
    allowed_ids: Set[str] = set()
    used_records = 0
    current_len = 0
    total_candidates = len(ranked)
    considered = ranked[:max_records]
    budget_dropped = 0
    for record in considered:
        line = (
            f"- [SourceID: {record.source_id}] [{record.source_type}] "
            f"Customer: {record.customer} | Time: {record.timestamp or 'N/A'} | {record.text}"
        )
        if current_len + len(line) + 1 > char_budget:
            budget_dropped += 1
            continue
        kept.append(line)
        current_len += len(line) + 1
        used_records += 1
        allowed_ids.add(_normalize_claim_id(record.source_id))
        allowed_ids.update(_extract_ids_from_text(record.text))
    if not kept:
        return "No evidence records were available for this question.", set(), 0
    # Round 4: when ``max_records`` or ``char_budget`` clip the evidence,
    # append an explicit truncation marker so the LLM knows it is seeing
    # a sample and cannot describe partial coverage as exhaustive.
    rank_dropped = max(total_candidates - len(considered), 0)
    if rank_dropped or budget_dropped:
        kept.append(
            f"[Evidence truncated: included {used_records} of {total_candidates} ranked records "
            f"due to context budget (rank-cap dropped {rank_dropped}, char-budget dropped {budget_dropped}).]"
        )
    return "\n".join(kept), allowed_ids, used_records


def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_claim_citations(claims: Iterable[Dict[str, Any]], allowed_ids: Set[str]) -> Tuple[List[Dict[str, Any]], List[str], int]:
    valid_claims: List[Dict[str, Any]] = []
    unknowns: List[str] = []
    rejected = 0
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        statement = str(claim.get("statement") or "").strip()
        if not statement:
            continue
        citations = claim.get("citations") or []
        normalized = [_normalize_claim_id(c) for c in citations if str(c).strip()]
        accepted = sorted({c for c in normalized if c in allowed_ids})
        if accepted:
            valid_claims.append({"statement": statement, "citations": accepted})
        else:
            rejected += 1
            unknowns.append(statement)
    return valid_claims, unknowns, rejected


def compose_grounded_answer(payload: Dict[str, Any], allowed_ids: Set[str]) -> Tuple[str, int]:
    summary = str(payload.get("executive_summary") or "").strip()
    actions = [str(a).strip() for a in (payload.get("actions") or []) if str(a).strip()]
    model_unknowns = [str(u).strip() for u in (payload.get("unknowns") or []) if str(u).strip()]
    claims, rejected_unknowns, rejected = _validate_claim_citations(payload.get("claims") or [], allowed_ids)
    unknowns = model_unknowns + rejected_unknowns

    lines: List[str] = []
    if summary:
        lines.append(summary)
        lines.append("")
    if claims:
        lines.append("### Supported Findings")
        for claim in claims:
            lines.append(f"- {claim['statement']} [Sources: {', '.join(claim['citations'])}]")
        lines.append("")
    if actions:
        lines.append("### Recommended Actions")
        for action in actions:
            lines.append(f"- {action}")
        lines.append("")
    if unknowns:
        lines.append("### Evidence Gaps")
        for item in unknowns[:8]:
            lines.append(f"- {item}")

    answer = "\n".join(lines).strip()
    if not answer:
        answer = "Insufficient grounded evidence was available to answer this question confidently."
    return answer, rejected


def _portfolio_records_from_payload(payload: Dict[str, Any]) -> Tuple[List[EvidenceRecord], Set[str]]:
    records: List[EvidenceRecord] = []
    ids: Set[str] = set()

    map_config = (
        ("AdoptionBarrier", payload.get("adoption_barriers"), ("ID",), ("SUBJECT_C", "AB_CATEGORY_C", "SEVERITY_C", "STATUS_C"), ("BU_NAME", "ACCOUNT_NAME_C"), ("OPEN_DATE_C", "CREATED_DATE")),
        ("SupportCase", payload.get("support_cases_snowflake"), ("CASE_ID", "ID"), ("SUBJECT", "SEVERITY", "STATUS"), ("ACCOUNT_ID", "BU_NAME"), ("OPEN_DATE", "CREATED_DATE")),
        ("CustomerPulse", payload.get("csconsole_customer_pulse"), ("ID",), ("SCORE__C", "SCORE_C", "PULSE_RATING__C", "COMMENTS__C"), ("CUSTOMER_NAME__C", "BU_NAME"), ("LAST_MODIFIED_DATE", "CREATED_DATE")),
        ("SuccessPriority", payload.get("csconsole_success_priorities"), ("ID", "SP_ID"), ("SUBJECT_C", "STATUS_C", "SEVERITY_C"), ("RELATED_CUSTOMER__C", "CUSTOMER_BU_NAME__C"), ("OPEN_DATE_C", "CREATED_DATE")),
        ("ActionPlan", payload.get("csconsole_action_plans"), ("ID", "AP_ID"), ("SUBJECT_C", "STATUS_C", "ACTION_TYPE_C"), ("CUSTOMER_BU_NAME__C", "RELATED_CUSTOMER__C"), ("OPEN_DATE_C", "CREATED_DATE")),
    )
    for source_type, df, id_cols, text_cols, customer_cols, ts_cols in map_config:
        prefix = "SP-" if source_type == "SuccessPriority" else ("AP-" if source_type == "ActionPlan" else "")
        subset, subset_ids = _records_from_dataframe(
            df=df,
            source_type=source_type,
            id_columns=id_cols,
            text_columns=text_cols,
            customer_columns=customer_cols,
            timestamp_columns=ts_cols,
            id_prefix=prefix,
        )
        records.extend(subset)
        ids.update(subset_ids)

    for incident in (payload.get("incidents") or [])[:60]:
        incident_id = str(incident.get("id") or "").strip() or "INC-UNSPECIFIED"
        records.append(
            EvidenceRecord(
                source_type="Incident",
                source_id=incident_id,
                customer="Portfolio",
                timestamp=str(incident.get("published") or "")[:19],
                text=f"[{incident.get('status', '')}] {incident.get('title', '')} | Impact: {incident.get('impact_level', '')}",
                confidence=0.85,
            )
        )
        ids.add(_normalize_claim_id(incident_id))
    for bug in (payload.get("bugs") or [])[:80]:
        bug_id = str(bug.get("bug_id") or "").strip()
        if not bug_id:
            continue
        records.append(
            EvidenceRecord(
                source_type="Bug",
                source_id=bug_id,
                customer="Portfolio",
                timestamp=str(bug.get("discovered_at") or "")[:19],
                text=str(bug.get("title") or "Known bug"),
                confidence=0.8,
            )
        )
        ids.add(_normalize_claim_id(bug_id))

    return records, ids


def run_portfolio_grounded_ask_ai(req: AskAIRequest) -> Dict[str, Any]:
    """
    Execute grounded Ask AI for portfolio questions.
    Returns a route-ready payload:
      - {'ok': True, 'answer': ..., 'context_summary': ...}
      - {'ok': False, 'error': ..., 'status_code': ...}
      - {'ok': False, 'fallback_to_legacy': True, ...}
    """
    from adoptiq_backend import (
        TEAM_ROSTER,
        _connect_with_keeper,
        build_cross_report_trends,
        compute_barrier_aging,
        generate_llm_json_response,
        get_subscriptions_for_team,
        scan_historical_reports,
    )
    from incident_storage import get_all_external_intel

    retrieval_plan = build_retrieval_plan(req.question)
    cssm_emails = [email for mgr, _, email in TEAM_ROSTER if mgr == req.manager or req.manager == "All Managers"]
    if not cssm_emails:
        return {"ok": False, "error": f"No team members found for manager: {req.manager}", "status_code": 400}

    ctx = _connect_with_keeper()
    if ctx is None:
        return {
            "ok": False,
            "error": "Database connection failed. Please connect to Cisco VPN and try again.",
            "status_code": 503,
        }

    try:
        team_subs_df = get_subscriptions_for_team(ctx, cssm_emails)
        if team_subs_df is None or team_subs_df.empty:
            return {"ok": True, "answer": "No subscription data found for the selected scope.", "context_summary": "Data: no subscriptions"}

        if req.technology and req.technology != "All" and "TECHNOLOGY_C" in team_subs_df.columns:
            team_subs_df = team_subs_df[
                team_subs_df["TECHNOLOGY_C"].astype(str).str.contains(req.technology, case=False, na=False)
            ]

        account_ids = team_subs_df["ACCOUNT_ID_C"].dropna().astype(str).unique().tolist() if "ACCOUNT_ID_C" in team_subs_df.columns else []
        if not account_ids:
            return {"ok": True, "answer": "No account IDs found for detailed analysis in this scope.", "context_summary": "Data: no account IDs"}

        # Round 4: unify the legacy and grounded Ask-AI account batch
        # caps to the same default (100) so two sections of the same
        # model context cannot disagree on how many accounts were
        # actually inspected.  Override via ``ADOPTIQ_ASK_AI_MAX_ACCOUNTS``.
        _account_batch_limit = int(os.environ.get("ADOPTIQ_ASK_AI_MAX_ACCOUNTS", "100"))
        account_batch = account_ids[:_account_batch_limit]
        _account_batch_truncated = len(account_ids) > _account_batch_limit
        customer_batch_names = (
            team_subs_df[team_subs_df["ACCOUNT_ID_C"].isin(account_batch)]["BU_NAME"].dropna().astype(str).unique().tolist()
            if {"ACCOUNT_ID_C", "BU_NAME"}.issubset(set(team_subs_df.columns))
            else []
        )

        ask_owner_emails = (
            team_subs_df["CSSM_EMAIL"].dropna().astype(str).str.strip().str.lower().unique().tolist()
            if "CSSM_EMAIL" in team_subs_df.columns else []
        )
        run_ctx = AnalysisRunContext.build(
            ctx,
            account_batch,
            req.days,
            customer_names=customer_batch_names,
            owner_emails=ask_owner_emails,
        )
        bundle = prefetch_ask_ai_grounded(run_ctx, include_datasets=retrieval_plan["datasets"])
        bundle["support_cases_snowflake"] = bundle.get("support_cases_snowflake", pd.DataFrame())
        bundle["csconsole_adoption_barriers"] = bundle.get("csconsole_adoption_barriers", pd.DataFrame())
        # Backward-compatible alias: downstream evidence builders key off
        # ``adoption_barriers``; point it at the owner-aware frame.
        bundle["adoption_barriers"] = bundle["csconsole_adoption_barriers"]
        bundle["csconsole_customer_pulse"] = bundle.get("csconsole_customer_pulse", pd.DataFrame())
        bundle["csconsole_success_priorities"] = bundle.get("csconsole_success_priorities", pd.DataFrame())
        bundle["csconsole_action_plans"] = bundle.get("csconsole_action_plans", pd.DataFrame())

        bundle["barrier_aging"] = compute_barrier_aging(bundle.get("adoption_barriers"), pd.DataFrame())

        # Round 3: thread the request's analysis window into external
        # intel so the LLM sees the same window as the rest of the
        # report. Clamped to a documented max of 365 days to keep the
        # context payload bounded.
        try:
            _intel_days = int(getattr(req, "days", 90) or 90)
        except (TypeError, ValueError):
            _intel_days = 90
        _intel_days = max(1, min(_intel_days, 365))
        intel = get_all_external_intel(days_back=_intel_days)
        bundle["incidents"] = intel.get("incidents", [])
        bundle["bugs"] = intel.get("bugs", [])
        hist = scan_historical_reports(str(Path.cwd() / "outputs"), manager=req.manager, technology=req.technology, limit=4)
        bundle["cross_report_trends"] = build_cross_report_trends(hist) if hist else {}

        records, cited_ids = _portfolio_records_from_payload(bundle)
        context_text, allowed_ids, used_records = build_evidence_context(
            records=records,
            question=req.question,
            domains=retrieval_plan["domains"],
            char_budget=int(os.environ.get("ADOPTIQ_ASK_AI_CHAR_BUDGET", "42000")),
        )
        allowed_ids.update(cited_ids)

        if not allowed_ids:
            return {"ok": False, "fallback_to_legacy": True, "reason": "No verifiable source IDs found in retrieval payload"}

        system_prompt = (
            "You are AdoptIQ's grounded portfolio analyst. "
            "Return STRICT JSON only with keys: executive_summary, claims, actions, unknowns. "
            "claims must be a list of objects with fields: statement (string) and citations (string array). "
            "Only cite SourceID values present in the provided evidence."
        )
        # Round 4: explicitly state the analysis window and the data
        # retrieval timestamp so the LLM grounds its temporal claims on
        # the same horizon as the underlying fetch.  Previously the
        # ``days`` value was buried inside the scope line which the LLM
        # frequently ignored when summarizing "recent" trends.
        from datetime import datetime as _dt
        _retrieved_at = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        _account_batch_disclosure = (
            f"[NOTE] Account-level evidence covers the first "
            f"{len(account_batch)} of {len(account_ids)} accounts in this scope (sample only).\n"
            if _account_batch_truncated else ""
        )
        user_prompt = (
            f"Analysis window: last {req.days} days\n"
            f"Data retrieved at: {_retrieved_at} (UTC)\n"
            f"{_account_batch_disclosure}"
            f"Question: {req.question}\n"
            f"Scope: manager={req.manager}, technology={req.technology}, days={req.days}\n"
            f"Retrieval domains: {', '.join(retrieval_plan['domains'])}\n"
            f"Citation whitelist (must use exactly): {', '.join(sorted(list(allowed_ids))[:400])}\n\n"
            f"Evidence:\n{context_text}\n"
        )
        schema = {
            "type": "object",
            "required": ["executive_summary", "claims", "actions", "unknowns"],
            "properties": {
                "executive_summary": {"type": "string"},
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["statement", "citations"],
                        "properties": {
                            "statement": {"type": "string"},
                            "citations": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "actions": {"type": "array", "items": {"type": "string"}},
                "unknowns": {"type": "array", "items": {"type": "string"}},
            },
        }
        llm_result = generate_llm_json_response(system_prompt, user_prompt, schema)
        if not llm_result.get("ok"):
            return {"ok": False, "fallback_to_legacy": True, "reason": llm_result.get("error", "LLM JSON mode failed")}
        payload = llm_result.get("data") or {}
        answer, rejected = compose_grounded_answer(payload, allowed_ids)

        summary = (
            f"Data: {len(team_subs_df)} subs, "
            f"{team_subs_df['BU_NAME'].nunique() if 'BU_NAME' in team_subs_df.columns else 0} customers | "
            f"evidence_records={used_records} | citations={len(allowed_ids)} | "
            f"citation_rejections={rejected} | queries={sum(v for k, v in run_ctx.metrics.items() if k.endswith('_queries'))}"
        )
        return {"ok": True, "answer": answer, "context_summary": summary}
    except Exception as exc:
        logger.error("Grounded Ask AI portfolio pipeline failed: %s", exc, exc_info=True)
        return {"ok": False, "fallback_to_legacy": True, "reason": "Pipeline exception"}
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def run_intel_grounded_ask_ai(question: str, days: int = 365) -> Dict[str, Any]:
    """Grounded Ask AI path for external intelligence questions.

    Round 3: ``days`` is now a parameter (default 365 to preserve prior
    behavior for callers that do not pass it). Clamped to [1, 365].
    """
    from adoptiq_backend import generate_llm_json_response
    from incident_storage import get_all_external_intel

    try:
        _intel_days = int(days or 365)
    except (TypeError, ValueError):
        _intel_days = 365
    _intel_days = max(1, min(_intel_days, 365))
    intel = get_all_external_intel(days_back=_intel_days)
    records: List[EvidenceRecord] = []
    ids: Set[str] = set()

    for incident in (intel.get("incidents") or [])[:120]:
        incident_id = str(incident.get("id") or "").strip()
        if not incident_id:
            continue
        records.append(
            EvidenceRecord(
                source_type="Incident",
                source_id=incident_id,
                customer="Portfolio",
                timestamp=str(incident.get("published") or "")[:19],
                text=f"[{incident.get('status', '')}] {incident.get('title', '')} | Impact: {incident.get('impact_level', '')} | Description: {(incident.get('description') or '')[:180]}",
                confidence=0.9,
            )
        )
        ids.add(_normalize_claim_id(incident_id))

    for maint in (intel.get("maintenances") or [])[:120]:
        maintenance_id = str(maint.get("id") or "").strip()
        if not maintenance_id:
            continue
        records.append(
            EvidenceRecord(
                source_type="Maintenance",
                source_id=maintenance_id,
                customer="Portfolio",
                timestamp=str(maint.get("published") or "")[:19],
                text=f"[{maint.get('status', '')}] {maint.get('title', '')}",
                confidence=0.85,
            )
        )
        ids.add(_normalize_claim_id(maintenance_id))

    for bug in (intel.get("bugs") or [])[:120]:
        bug_id = str(bug.get("bug_id") or "").strip()
        if not bug_id:
            continue
        records.append(
            EvidenceRecord(
                source_type="Bug",
                source_id=bug_id,
                customer="Portfolio",
                timestamp=str(bug.get("discovered_at") or "")[:19],
                text=f"{bug.get('title', '')} | Source: {bug.get('source', '')}",
                confidence=0.85,
            )
        )
        ids.add(_normalize_claim_id(bug_id))

    context, allowed_ids, used_records = build_evidence_context(records, question, domains=["intel"], char_budget=32000)
    allowed_ids.update(ids)
    if not allowed_ids:
        return {"ok": False, "fallback_to_legacy": True, "reason": "No intelligence IDs available"}

    system_prompt = (
        "You are AdoptIQ's external intelligence analyst. "
        "Return STRICT JSON only with keys: executive_summary, claims, actions, unknowns. "
        "Each claim must include citations that exactly match SourceID values from evidence."
    )
    # Round 4: inject the analysis window and the data-retrieval
    # timestamp into the user prompt so the LLM cannot describe the
    # evidence as "recent" without anchoring to a concrete window.
    # This closes the long-standing fidelity gap where a 7-day request
    # could surface 365-day-old incidents narrated as "recent".
    from datetime import datetime as _dt
    _retrieved_at = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    user_prompt = (
        f"Analysis window: last {_intel_days} days\n"
        f"Data retrieved at: {_retrieved_at} (UTC)\n"
        f"Question: {question}\n"
        f"Citation whitelist: {', '.join(sorted(list(allowed_ids))[:400])}\n"
        f"Evidence:\n{context}\n"
    )
    schema = {
        "type": "object",
        "required": ["executive_summary", "claims", "actions", "unknowns"],
        "properties": {
            "executive_summary": {"type": "string"},
            "claims": {"type": "array"},
            "actions": {"type": "array"},
            "unknowns": {"type": "array"},
        },
    }
    llm_result = generate_llm_json_response(system_prompt, user_prompt, schema)
    if not llm_result.get("ok"):
        return {"ok": False, "fallback_to_legacy": True, "reason": llm_result.get("error", "LLM JSON mode failed")}
    payload = llm_result.get("data") or {}
    answer, rejected = compose_grounded_answer(payload, allowed_ids)
    return {
        "ok": True,
        "answer": answer,
        "context_summary": f"intel_records={used_records} | citations={len(allowed_ids)} | citation_rejections={rejected}",
    }
