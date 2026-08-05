"""Explicit runtime adapters for the deterministic local acceptance lab.

The production application never imports this module.  The guarded local
runner imports it only after :func:`local_acceptance_lab.assert_safe_activation`
has accepted an explicit loopback/source-mode request.  Adapters are reversible
so tests cannot leak fixture behavior into later test cases.
"""

from __future__ import annotations

import copy
import importlib
import json
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from local_acceptance_lab import LocalAcceptanceBundle, SOURCE_MODE


_CANONICAL_PRIMARY_KEYS: Mapping[str, str] = {
    "action_plans": "ID",
    "adoption_barriers": "ID",
    "customer_pulse": "ID",
    "tac_cases": "SR Number",
    "bems_cases": "Transaction ID",
    "success_priorities": "ID",
    "renewals": "RENEWAL_ID",
    "activities": "ACTIVITY_ID",
    "support_cases": "CASE_ID",
    "external_incidents": "id",
    "external_bugs": "bug_id",
    "external_maintenances": "id",
    "corpus_documents": "DOCUMENT_ID",
    "corpus_chunks": "CHUNK_ID",
    "customer_history": "HISTORY_ID",
    "playbook_entries": "PLAYBOOK_ID",
}


class UnexpectedLiveDependency(RuntimeError):
    """A supposedly adapted path attempted to use a live-only dependency."""


class _LocalConnection:
    """Small connection sentinel which exposes missing adapters immediately."""

    closed = False

    def close(self) -> None:
        self.closed = True

    def cursor(self, *_args: Any, **_kwargs: Any) -> Any:
        raise UnexpectedLiveDependency(
            "local acceptance reached an unadapted Snowflake cursor path"
        )


class RuntimeInstallation:
    """Own and restore every module attribute changed by an installation."""

    def __init__(self, bundle: LocalAcceptanceBundle) -> None:
        self.bundle = bundle
        self._originals: list[tuple[object, str, Any, bool]] = []

    def patch(self, target: object, name: str, value: Any) -> None:
        existed = hasattr(target, name)
        original = getattr(target, name, None)
        self._originals.append((target, name, original, existed))
        setattr(target, name, value)

    def patch_mapping(self, target: dict[str, Any], name: str, value: Any) -> None:
        existed = name in target
        original = target.get(name)
        self._originals.append((target, name, original, existed))
        target[name] = value

    def restore(self) -> None:
        while self._originals:
            target, name, original, existed = self._originals.pop()
            if isinstance(target, dict):
                if existed:
                    target[name] = original
                else:
                    target.pop(name, None)
            elif existed:
                setattr(target, name, original)
            else:
                delattr(target, name)

    def __enter__(self) -> "RuntimeInstallation":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.restore()


def _frame(bundle: LocalAcceptanceBundle, dataset: str) -> pd.DataFrame:
    frame = bundle.frame(dataset)
    primary_key = _CANONICAL_PRIMARY_KEYS.get(dataset)
    if not primary_key or primary_key not in frame.columns or frame.empty:
        return frame
    key_values = frame[primary_key].fillna("").astype(str).str.strip()
    identified = frame.loc[key_values.ne("")]
    unidentified = frame.loc[key_values.eq("")]
    result = pd.concat(
        [identified.drop_duplicates(subset=[primary_key], keep="first"), unidentified]
    ).sort_index()
    result.attrs.update(frame.attrs)
    result.attrs["raw_fixture_rows"] = len(frame)
    result.attrs["canonical_rows"] = len(result)
    result.attrs["duplicate_rows_removed"] = len(frame) - len(result)
    return result


def _values(values: Iterable[Any] | None) -> set[str]:
    return {str(value).strip().casefold() for value in values or [] if str(value).strip()}


def _filter_frame(
    frame: pd.DataFrame,
    values: Iterable[Any] | None,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    wanted = _values(values)
    if not wanted:
        return frame.copy(deep=True)
    mask = pd.Series(False, index=frame.index)
    for column in columns:
        if column in frame.columns:
            mask |= frame[column].fillna("").astype(str).str.strip().str.casefold().isin(wanted)
    result = frame.loc[mask].copy(deep=True)
    result.attrs.update(frame.attrs)
    return result


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def _email_roster(bundle: LocalAcceptanceBundle) -> list[tuple[str, str, str]]:
    rows = []
    for record in bundle.records("ownership"):
        rows.append(
            (
                str(record.get("MANAGER_NAME") or "Local Fixture Manager"),
                str(record.get("OWNER_NAME") or "Fixture Member"),
                str(record.get("OWNER_EMAIL") or "fixture@example.invalid"),
            )
        )
    return rows


def _member_for_email(bundle: LocalAcceptanceBundle) -> dict[str, str]:
    return {
        str(row.get("OWNER_EMAIL") or "").strip().casefold(): str(
            row.get("OWNER_NAME") or ""
        ).strip()
        for row in bundle.records("ownership")
    }


def _source_warning(bundle: LocalAcceptanceBundle, dataset: str) -> list[str]:
    state = bundle.source_states.get(dataset, "available")
    if state in {"available", "zero"}:
        return []
    return [f"{dataset.replace('_', ' ').title()} source state: {state} (local fixture)."]


def _provider_failure(state: str) -> dict[str, Any]:
    status_by_state = {
        "timeout": 504,
        "rate_limited": 429,
        "unavailable": 503,
        "malformed": 502,
    }
    message_by_state = {
        "timeout": "The AI provider timed out. Retry the request.",
        "rate_limited": "The AI provider is temporarily rate limited. Retry later.",
        "unavailable": "The AI provider is temporarily unavailable.",
        "malformed": "The AI provider returned an unusable response.",
    }
    return {
        "ok": False,
        "status_code": status_by_state.get(state, 503),
        "error": message_by_state.get(state, "The AI provider is unavailable."),
        "reason": f"provider_{state}",
        "fallback_to_legacy": False,
    }


def _safe_excerpt(value: object, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _portfolio_ai_result(bundle: LocalAcceptanceBundle, request: object) -> dict[str, Any]:
    if bundle.provider_state != "available":
        return _provider_failure(bundle.provider_state)

    question = str(getattr(request, "question", "") or "").strip()
    action_plans = _frame(bundle, "action_plans")
    cases = _frame(bundle, "support_cases")
    barriers = _frame(bundle, "adoption_barriers")
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in _records(action_plans.head(8)):
        record_id = str(
            row.get("ID") or row.get("LOCAL_ACCEPTANCE_RECORD_ID") or "AP-MISSING-ID"
        )
        if record_id in seen_ids:
            continue
        seen_ids.add(record_id)
        records.append(
            {
                "id": record_id,
                "record_id": record_id,
                "source_id": record_id,
                "source_type": "action_plan",
                "customer": row.get("BU_NAME") or "Unknown customer",
                "title": row.get("SUBJECT_C") or "Untitled action plan",
                "snippet": _safe_excerpt(row.get("NEXT_ACTION_C") or row.get("SUBJECT_C")),
                "status": row.get("STATUS_C") or "Unknown",
            }
        )
    for row in _records(cases.head(5)):
        record_id = str(
            row.get("CASE_ID")
            or row.get("LOCAL_ACCEPTANCE_RECORD_ID")
            or "CASE-MISSING-ID"
        )
        if record_id in seen_ids:
            continue
        seen_ids.add(record_id)
        records.append(
            {
                "id": record_id,
                "record_id": record_id,
                "source_id": record_id,
                "source_type": "support_case",
                "customer": row.get("BU_NAME") or "Unknown customer",
                "title": row.get("SUBJECT") or "Untitled support case",
                "snippet": _safe_excerpt(row.get("DESCRIPTION") or row.get("SUBJECT")),
                "status": row.get("STATUS") or "Unknown",
            }
        )
    evidence_index = [
        {
            "id": item["id"],
            "source_id": item["source_id"],
            "source_type": item["source_type"],
            "title": item["title"],
        }
        for item in records
    ]

    lower_question = question.casefold()
    unanswerable = any(
        token in lower_question
        for token in ("weather", "stock price", "private password", "credential")
    )
    if unanswerable:
        answer = (
            "The available AdoptIQ evidence does not support that request. "
            "An external fact or credential was not inferred from the local dataset."
        )
    elif records:
        first_id = records[0]["id"]
        case_id = next(
            (item["id"] for item in records if item["source_type"] == "support_case"),
            first_id,
        )
        answer = (
            f"Prioritize the overdue and blocked Action Plans first [{first_id}]. "
            f"Then validate customer impact against the highest-severity support case "
            f"[{case_id}]. This recommendation is limited to the cited fixture evidence."
        )
    else:
        answer = "No in-scope evidence records are available; no recommendation can be made."

    warnings = []
    for dataset in ("action_plans", "support_cases", "adoption_barriers"):
        warnings.extend(_source_warning(bundle, dataset))
    return {
        "ok": True,
        "answer": answer,
        "context_summary": (
            f"Local acceptance evidence: {len(action_plans)} Action Plans, "
            f"{len(barriers)} barriers, {len(cases)} support cases."
        ),
        "evidence_records": records,
        "evidence_index": evidence_index,
        "evidence_records_used": len(records),
        "evidence_records_total": len(records),
        "evidence_truncated": False,
        "account_batch_truncated": False,
        "account_batch_size": len(_frame(bundle, "customers")),
        "account_total": len(_frame(bundle, "customers")),
        "partial_data_warnings": warnings,
        "canonical_headline": {
            "action_plans": len(action_plans),
            "adoption_barriers": len(barriers),
            "support_cases": len(cases),
            "customers": len(_frame(bundle, "customers")),
        },
        "canonical_corrections": [],
        "canonical_verified": ["action_plans", "adoption_barriers", "support_cases"],
        "retrieval_diag": {
            "method": "deterministic_local_acceptance",
            "source_mode": SOURCE_MODE,
            "scenario": bundle.scenario,
            "schema_fingerprint": bundle.schema_fingerprint,
            "live_validation_performed": False,
        },
        "corpus": {
            "available": True,
            "chunks": len(_frame(bundle, "corpus_chunks")),
            "source_mode": SOURCE_MODE,
        },
    }


def _intel_ai_result(bundle: LocalAcceptanceBundle, question: str, days: int = 365) -> dict[str, Any]:
    if bundle.provider_state != "available":
        return _provider_failure(bundle.provider_state)
    incidents = bundle.records("external_incidents")
    bugs = bundle.records("external_bugs")
    maint = bundle.records("external_maintenances")
    citations = [str(item.get("id") or "") for item in incidents if item.get("id")]
    if not citations:
        return {
            "ok": True,
            "answer": "No in-window external-intelligence records are available.",
            "context_summary": f"0 records in the requested {days}-day window.",
        }
    return {
        "ok": True,
        "answer": (
            f"The fixture contains {len(incidents)} incidents, {len(maint)} maintenance "
            f"records, and {len(bugs)} known bugs. Review [Source: {citations[0]}] first; "
            "no customer impact is inferred beyond the stored records."
        ),
        "context_summary": f"Local deterministic external intelligence ({days} days).",
    }


def _deterministic_json_response(
    bundle: LocalAcceptanceBundle,
    _system_prompt: object,
    user_prompt: object,
    _schema: object,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Deterministic provider boundary for the real grounded AI pipeline.

    Retrieval, ranking, canonical metrics, citation validation, evidence
    persistence, and answer composition remain production code.  Only the
    external model call is replaced.  The response cites an ID from the
    prompt's exact whitelist and explicitly declines questions whose requested
    fact is absent, allowing local acceptance to exercise orchestration without
    credentials or a network provider.
    """

    if bundle.provider_state != "available":
        failure = _provider_failure(bundle.provider_state)
        return {"ok": False, "error": failure["reason"]}

    prompt = str(user_prompt or "")
    whitelist_match = re.search(
        r"^Citation whitelist \(must use exactly\):\s*(.+)$",
        prompt,
        flags=re.MULTILINE,
    )
    allowed_ids: list[str] = []
    if whitelist_match:
        for item in whitelist_match.group(1).split(","):
            candidate = item.strip()
            if candidate and not candidate.startswith("..."):
                allowed_ids.append(candidate)

    question_match = re.search(
        r"=== BEGIN USER_QUESTION ===\s*(.*?)\s*=== END USER_QUESTION ===",
        prompt,
        flags=re.DOTALL,
    )
    question = (question_match.group(1) if question_match else "").casefold()
    evidence_gap = any(
        phrase in question
        for phrase in (
            "customer-satisfaction score",
            "not present in the retrieved evidence",
            "stock price",
            "weather",
            "private password",
            "credential",
        )
    )

    headline_values: list[str] = []
    for key, value in re.findall(
        r"^\s*-\s+([A-Za-z0-9_]+):\s+(-?\d+(?:\.\d+)?)\s*$",
        prompt,
        flags=re.MULTILINE,
    ):
        headline_values.append(f"{key}={value}")
    summary = "Based on the available evidence"
    if headline_values:
        summary += ", the canonical headline is " + ", ".join(headline_values[:8])
    summary += "."

    claims: list[dict[str, Any]] = []
    if allowed_ids:
        # Mirror the production entailment contract: the deterministic local
        # provider must claim only text present in the exact cited prompt row.
        # The former generic "first item to review" sentence had a valid ID but
        # was not supported by the row itself, so the hardened composer correctly
        # suppressed it.
        first_id = allowed_ids[0]
        evidence_match = re.search(
            rf"^-\s*\[SourceID:\s*{re.escape(first_id)}\s*\]\s*(.+)$",
            prompt,
            flags=re.MULTILINE,
        )
        supported_statement = evidence_match.group(1).strip() if evidence_match else ""
        # Production adds ``Customer`` and ``Time`` presentation labels around
        # the underlying EvidenceRecord.  Those labels are not row facts, so use
        # the exact record text after the second separator as the claim.
        prompt_parts = supported_statement.split(" | ", 2)
        if len(prompt_parts) == 3:
            supported_statement = prompt_parts[2].strip()
    else:
        first_id = ""
        supported_statement = ""
    if first_id and supported_statement:
        claims.append(
            {
                "statement": supported_statement,
                "citations": [first_id],
            }
        )
    unknowns = []
    if evidence_gap:
        unknowns.append(
            "Insufficient evidence is present to determine the requested value; no estimate was made."
        )
    return {
        "ok": True,
        "data": {
            "executive_summary": summary,
            "claims": claims,
            "actions": [],
            "unknowns": unknowns,
        },
        "model_name": "local-acceptance-deterministic",
    }


def _external_intel(bundle: LocalAcceptanceBundle, days_back: int = 365) -> dict[str, Any]:
    incidents = bundle.records("external_incidents")
    bugs = bundle.records("external_bugs")
    maint = bundle.records("external_maintenances")

    def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        timestamps = [
            str(row.get("published") or row.get("discovered_at") or "") for row in rows
        ]
        timestamps = [value for value in timestamps if value]
        return {
            "total": len(rows),
            "count": len(rows),
            "newest": max(timestamps, default=""),
            "oldest": min(timestamps, default=""),
            "days_back": days_back,
        }

    incident_stats = stats(incidents)
    bug_stats = stats(bugs)
    maintenance_stats = stats(maint)
    incident_stats.update(
        {
            "active": sum(
                str(row.get("status") or "").casefold() not in {"resolved", "closed"}
                for row in incidents
            ),
            "resolved": sum(
                str(row.get("status") or "").casefold() in {"resolved", "closed"}
                for row in incidents
            ),
        }
    )
    maintenance_stats.update(
        {
            "scheduled": sum(
                str(row.get("status") or "").casefold() == "scheduled" for row in maint
            ),
            "completed": sum(
                str(row.get("status") or "").casefold() == "completed" for row in maint
            ),
        }
    )
    return {
        "incidents": incidents,
        "bugs": bugs,
        "maintenances": maint,
        "incident_stats": incident_stats,
        "bug_stats": bug_stats,
        "maintenance_stats": maintenance_stats,
        "incident_stats_global": dict(incident_stats),
        "bug_stats_global": dict(bug_stats),
        "maintenance_stats_global": dict(maintenance_stats),
        "days_back": days_back,
        "list_fetch_limit": 500,
        "list_truncated": {"incidents": False, "bugs": False, "maintenances": False},
        "fetch_errors": {},
        "not_configured": {},
        # Optional source-state metadata keeps stale/unavailable fixture
        # feeds distinguishable from a verified zero through AI/UI routes.
        "source_states": {
            "incidents": bundle.source_states.get(
                "external_incidents", "available"
            ),
            "bugs": bundle.source_states.get("external_bugs", "available"),
            "maintenances": bundle.source_states.get(
                "external_maintenances", "available"
            ),
        },
        "source_mode": SOURCE_MODE,
        "live_validation_performed": False,
    }


def _subscription_payload(bundle: LocalAcceptanceBundle, subscription_id: str, days: int) -> dict[str, Any]:
    subscriptions = _frame(bundle, "subscriptions")
    match = _filter_frame(subscriptions, [subscription_id], ("SUBSCRIPTION_ID",))
    if match.empty:
        return {
            "subscription_id": subscription_id,
            "found": False,
            "error": "Subscription not found in the local acceptance snapshot.",
            "failure_kind": "not_found",
        }
    row = match.iloc[0].to_dict()
    account = row.get("ACCOUNT_ID_C")
    member = str(row.get("FIXTURE_MEMBER") or "")
    owner_map = {
        str(item.get("OWNER_NAME") or ""): str(item.get("OWNER_EMAIL") or "")
        for item in bundle.records("ownership")
    }
    scoped = {
        "adoption_barriers": _filter_frame(
            _frame(bundle, "adoption_barriers"), [account], ("ACCOUNT_ID_C",)
        ),
        "action_plans": _filter_frame(
            _frame(bundle, "action_plans"), [account], ("ACCOUNT_ID_C",)
        ),
        "customer_pulse": _filter_frame(
            _frame(bundle, "customer_pulse"), [account], ("ACCOUNT__C", "ACCOUNT_ID_C")
        ),
        "success_priorities": _filter_frame(
            _frame(bundle, "success_priorities"), [account, row.get("BU_NAME")],
            ("ACCOUNT_ID_C", "RELATED_CUSTOMER__C"),
        ),
    }
    counts = {f"{name}_count": len(frame) for name, frame in scoped.items()}
    result = {
        "subscription_id": subscription_id,
        "customer_name": row.get("BU_NAME") or "Unknown",
        "account_id": account,
        "cssm_email": owner_map.get(member),
        "technology": row.get("TECHNOLOGY_C") or row.get("PRODUCT_NAME") or "Unknown",
        "sub_technology": row.get("SUB_TECHNOLOGY_C") or "Unknown",
        "status": row.get("STATUS_C") or "Active",
        "renewal_risk_category": row.get("RENEWAL_RISK_CATEGORY") or "Medium",
        "found": True,
        "analysis_period_days": days,
        "team_data": [row],
        "total_records": sum(len(frame) for frame in scoped.values()),
        "summary": counts,
        "source_mode": SOURCE_MODE,
        "live_validation_performed": False,
    }
    result.update({name: _records(frame) for name, frame in scoped.items()})
    return result


def _patch_corpus(installation: RuntimeInstallation, bundle: LocalAcceptanceBundle) -> None:
    retriever = importlib.import_module("corpus_retriever")
    bootstrap = importlib.import_module("corpus_bootstrap")

    def status() -> Any:
        return retriever.CorpusSnapshot(
            available=True,
            files_total=len(_frame(bundle, "corpus_documents")),
            files_parsed=len(_frame(bundle, "corpus_documents")),
            last_parsed_at=bundle.as_of_utc,
            indexed_at=bundle.as_of_utc,
            customers=len(_frame(bundle, "customers")),
            cases=len(_frame(bundle, "customer_history")),
            chunks=len(_frame(bundle, "corpus_chunks")),
            schema_version=1,
        )

    def customer_history(name: object, **_kwargs: Any) -> Any:
        wanted = str(name or "").strip().casefold()
        histories = [
            row for row in bundle.records("customer_history")
            if str(row.get("CUSTOMER_NAME") or "").strip().casefold() == wanted
        ]
        if not histories:
            raise retriever.CorpusUnavailable("customer is not in the local fixture corpus")
        cases = tuple(
            retriever.CaseRecord(
                customer_name=str(row.get("CUSTOMER_NAME") or ""),
                case_number=str(row.get("CASE_ID") or row.get("HISTORY_ID") or ""),
                severity="Unknown",
                status="Historical",
                is_open=False,
                opened_at=str(row.get("EVENT_DATE") or ""),
                closed_at=str(row.get("EVENT_DATE") or ""),
                summary=str(row.get("SUMMARY") or ""),
                source_filename="sanitized-local-fixture",
            )
            for row in histories
        )
        resolutions = tuple(
            retriever.ResolutionRecord(
                method_text=str(row.get("RESOLUTION") or ""),
                technology="Webex",
                theme="fixture history",
                source_filename="sanitized-local-fixture",
                source_section="History",
            )
            for row in histories if row.get("RESOLUTION")
        )
        return retriever.CustomerHistory(
            name=str(histories[0].get("CUSTOMER_NAME") or name),
            manager="Local Fixture Manager",
            technology="Webex",
            first_seen=min(str(row.get("EVENT_DATE") or "") for row in histories),
            last_seen=max(str(row.get("EVENT_DATE") or "") for row in histories),
            occurrences=len(histories),
            cases=cases,
            barriers=(),
            sentiment_trend=(),
            top_resolutions=resolutions,
        )

    def themes(technology: object, *, top_k: int = 10) -> list[Any]:
        chunks = _frame(bundle, "corpus_chunks")
        if technology and str(technology).casefold() not in {"all", "all technologies"}:
            chunks = _filter_frame(chunks, [technology], ("TECHNOLOGY",))
        grouped = chunks.groupby(["TECHNOLOGY", "THEME"], dropna=False).size()
        return [
            retriever.Theme(str(tech or "Unknown"), str(theme or "Unknown"), 1, int(count))
            for (tech, theme), count in grouped.head(top_k).items()
        ]

    def resolutions(theme: object, technology: object = None, *, limit: int = 10) -> list[Any]:
        rows = bundle.records("playbook_entries")
        wanted_theme = str(theme or "").casefold()
        wanted_tech = str(technology or "").casefold()
        matches = []
        for row in rows:
            if wanted_theme and wanted_theme not in str(row.get("THEME") or "").casefold():
                continue
            if wanted_tech and wanted_tech not in str(row.get("TECHNOLOGY") or "").casefold():
                continue
            matches.append(
                retriever.ResolutionRecord(
                    method_text=str(row.get("METHOD_TEXT") or ""),
                    technology=str(row.get("TECHNOLOGY") or ""),
                    theme=str(row.get("THEME") or ""),
                    source_filename=str(row.get("SOURCE_FILENAME") or ""),
                    source_section="Playbook",
                )
            )
        return matches[:limit]

    def search(query: object, technology: object = None, theme: object = None, *, limit: int = 10, **_kwargs: Any) -> list[Any]:
        tokens = set(re.findall(r"[a-z0-9]+", str(query or "").casefold()))
        unsafe = {"ignore", "instructions", "credentials", "password", "secret"}
        results = []
        for row in bundle.records("corpus_chunks"):
            text = str(row.get("TEXT") or "")
            lowered = text.casefold()
            if unsafe.intersection(set(re.findall(r"[a-z0-9]+", lowered))):
                continue
            if technology and str(technology).casefold() not in str(row.get("TECHNOLOGY") or "").casefold():
                continue
            if theme and str(theme).casefold() not in str(row.get("THEME") or "").casefold():
                continue
            row_tokens = set(re.findall(r"[a-z0-9]+", lowered))
            score = len(tokens.intersection(row_tokens)) / max(len(tokens), 1)
            if score or not tokens:
                results.append(
                    retriever.Chunk(
                        text=text,
                        technology=str(row.get("TECHNOLOGY") or ""),
                        theme=str(row.get("THEME") or ""),
                        customer_name=str(row.get("CUSTOMER_NAME") or ""),
                        score=float(score),
                        source_filename="sanitized-local-fixture",
                        source_section="Fixture corpus",
                    )
                )
        return sorted(results, key=lambda item: (-item.score, item.text))[:limit]

    boot_state = SimpleNamespace(
        enabled=True,
        started=True,
        in_progress=False,
        completed=True,
        last_started_at=bundle.as_of_utc,
        last_finished_at=bundle.as_of_utc,
        last_error=None,
        last_error_kind=None,
        last_stats={"files_parsed": len(_frame(bundle, "corpus_documents"))},
        last_sources=[{"kind": SOURCE_MODE, "sanitized": True}],
        encrypted_path=None,
        onedrive_root=None,
        sharepoint=None,
        source=SOURCE_MODE,
        indexed_at=bundle.as_of_utc,
        last_successful_refresh_ts=None,
        last_refresh_attempt_ts=None,
        last_refresh_error=None,
        onedrive_status="local_fixture",
        onedrive_file_count=len(_frame(bundle, "corpus_documents")),
        signed_in_proxy="local_fixture",
        embedder_status="ready",
        embedder_load_error=None,
        dense_retrieval_status="fixture",
        dense_vectors_upserted=len(_frame(bundle, "corpus_chunks")),
        dense_vectors_considered=len(_frame(bundle, "corpus_chunks")),
        dense_vector_error=None,
        dense_rows_remaining=0,
    )
    for name, value in {
        "get_status": status,
        "get_customer_history": customer_history,
        "get_recurring_themes": themes,
        "get_resolutions_for": resolutions,
        "search_playbook": search,
        "search_playbook_hybrid": search,
        "is_configured": lambda: True,
    }.items():
        installation.patch(retriever, name, value)
    installation.patch(bootstrap, "is_enabled", lambda: True)
    installation.patch(bootstrap, "get_state", lambda: copy.deepcopy(boot_state))
    installation.patch(bootstrap, "request_refresh", lambda rebuild=False: False)


def install_runtime_adapters(
    bundle: LocalAcceptanceBundle,
    app_module: ModuleType | None = None,
) -> RuntimeInstallation:
    """Install reversible adapters into the real app/report orchestration.

    The caller must run the safety guard before importing the app and calling
    this function.  No environment-variable auto activation exists.
    """

    bundle.assert_reconciled()
    app_module = app_module or importlib.import_module("app_simple")
    backend = importlib.import_module("adoptiq_backend")
    prefetch = importlib.import_module("snowflake_prefetch")
    leader = importlib.import_module("leader_report_generator")
    incident_storage = importlib.import_module("incident_storage")
    diagnostics = importlib.import_module("connectivity_diagnostics")
    model_resolver = importlib.import_module("model_resolver")
    installation = RuntimeInstallation(bundle)
    roster = _email_roster(bundle)
    email_members = _member_for_email(bundle)
    fixture_pointer = str(
        Path(__file__).resolve().parent
        / "tests"
        / "fixtures"
        / "local_acceptance"
        / "v1"
        / "manifest.json"
    )

    def connect() -> _LocalConnection:
        return _LocalConnection()

    def subscriptions_for_team(_ctx: object, emails: list[str]) -> pd.DataFrame:
        frame = _frame(bundle, "subscriptions")
        selected_members = {
            email_members[email]
            for email in _values(emails)
            if email in email_members
        }
        if selected_members and "FIXTURE_MEMBER" in frame.columns:
            frame = _filter_frame(frame, selected_members, ("FIXTURE_MEMBER",))
        owner_email = {member: email for _, member, email in roster}
        frame["CSSM_EMAIL"] = frame.get("FIXTURE_MEMBER", pd.Series(index=frame.index)).map(
            owner_email
        )
        frame["TECHNOLOGY_C"] = frame.get("TECHNOLOGY_C", frame.get("PRODUCT_NAME", "Unknown"))
        frame.attrs["_r82_team_subs_diag"] = {
            "primary_email_column_used": "LOCAL_FIXTURE_OWNER_EMAIL",
            "secondary_email_columns_used": [],
            "primary_rows": len(frame),
            "secondary_rows": 0,
            "merged_rows": len(frame),
            "duplicate_rows_dropped": 0,
            "introspected_at": bundle.as_of_utc,
            "source_mode": SOURCE_MODE,
        }
        return frame

    def by_accounts(dataset: str, columns: tuple[str, ...]) -> Callable[..., pd.DataFrame]:
        def fetch(_ctx: object, account_ids: list[str], _days: int, *args: Any, **kwargs: Any) -> pd.DataFrame:
            del args, kwargs
            frame = _filter_frame(_frame(bundle, dataset), account_ids, columns)
            if dataset == "adoption_barriers" and "BU_NAME" in frame.columns:
                # Compact validates its row contract before the later
                # normalization step. Preserve the canonical source field and
                # add the expected semantic alias so a subsequent subscription
                # merge cannot turn BU_NAME into only BU_NAME_x/BU_NAME_y.
                frame["customer_name"] = frame["BU_NAME"]
            return frame
        return fetch

    def action_plans(
        _ctx: object,
        account_ids: list[str],
        _days: int,
        owner_emails: Iterable[Any] | None = None,
        *_args: Any,
        **_kwargs: Any,
    ) -> pd.DataFrame:
        frame = _filter_frame(_frame(bundle, "action_plans"), account_ids, ("ACCOUNT_ID_C",))
        selected_members = {
            email_members[email]
            for email in _values(owner_emails)
            if email in email_members
        }
        if not account_ids and selected_members:
            frame = _filter_frame(_frame(bundle, "action_plans"), selected_members, ("FIXTURE_MEMBER",))
        return frame

    def csone_loader(_path: object = None) -> pd.DataFrame:
        frame = _frame(bundle, "tac_cases")
        subscriptions = _frame(bundle, "subscriptions")
        technology_by_account: dict[str, str] = {}
        for row in _records(subscriptions):
            account_id = str(row.get("ACCOUNT_ID_C") or "").strip()
            technology = str(
                row.get("TECHNOLOGY_C") or row.get("PRODUCT_NAME") or ""
            ).strip()
            if account_id and technology:
                # Keep the first source-order mapping. The sanitized base
                # fixture declares the primary product before any optional
                # multi-product coverage rows, making this deterministic.
                technology_by_account.setdefault(account_id, technology)
        account_ids = frame.get(
            "ACCOUNT_ID_C", pd.Series("", index=frame.index, dtype="object")
        ).fillna("").astype(str)
        inferred_technology = account_ids.map(technology_by_account).fillna("Unknown")
        existing_technology = frame.get(
            "Technology", pd.Series("", index=frame.index, dtype="object")
        ).fillna("").astype(str).str.strip()
        technology = existing_technology.where(
            existing_technology.ne(""), inferred_technology
        )
        existing_sub_technology = frame.get(
            "Sub Technology", pd.Series("", index=frame.index, dtype="object")
        ).fillna("").astype(str).str.strip()
        # Compact's real enhanced scope filter requires both fields. Supplying
        # them here mirrors the production CSOne schema and avoids widening a
        # strict Contact Center request when a generic case title lacks a
        # product token.
        frame["Technology"] = technology
        frame["Sub Technology"] = existing_sub_technology.where(
            existing_sub_technology.ne(""), technology
        )
        return frame

    def external_fetch(dataset: str) -> Callable[..., list[dict[str, Any]]]:
        def fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return bundle.records(dataset)
        return fetch

    def search_subscriptions(customer_name: str, limit: int = 10) -> list[dict[str, Any]]:
        frame = _frame(bundle, "subscriptions")
        needle = str(customer_name or "").strip().casefold()
        if needle:
            mask = frame["BU_NAME"].fillna("").astype(str).str.casefold().str.contains(
                re.escape(needle), regex=True
            )
            frame = frame.loc[mask].copy()
        return _records(frame.head(max(1, int(limit))))

    def renewal_risk(subscription_id: str, days: int = 90) -> dict[str, Any]:
        payload = _subscription_payload(bundle, subscription_id, days)
        if not payload.get("found"):
            return {"found": False, "error": payload.get("error")}
        return {
            "found": True,
            "subscription_id": subscription_id,
            "customer_name": payload["customer_name"],
            "risk_level": payload.get("renewal_risk_category") or "Medium",
            "risk_score": 50,
            "analysis_period_days": days,
            "source_mode": SOURCE_MODE,
        }

    def narrative(*args: Any, **kwargs: Any) -> str:
        if bundle.provider_state != "available":
            failure = _provider_failure(bundle.provider_state)
            return f"ERROR: {failure['reason']}: {failure['error']}"
        system_prompt = str(args[0] if args else kwargs.get("system_prompt") or "")
        user_prompt = str(args[1] if len(args) > 1 else kwargs.get("user_prompt") or "")
        if "backend-engineering triage analyst" in system_prompt.casefold():
            barrier_ids = re.findall(
                r"^\[([^\]\n]+?)\s+-\s+score\s+[-+0-9.]+\]",
                user_prompt,
                flags=re.MULTILINE,
            )
            return json.dumps(
                [
                    {
                        "id": barrier_id.strip(),
                        "class": "TRUE_BLOCKER",
                        "reason": "Fixture describes an engineering dependency.",
                        "confidence": "high",
                    }
                    for barrier_id in barrier_ids
                ],
                sort_keys=True,
            )
        return (
            "## Decision Summary\n"
            "Focus on the cited Action Plan owners, due dates, and next actions. "
            "Validate each exception against the paired Source Data workbook before "
            "taking action.\n\n"
            "## Risks and Exceptions\n"
            "Source gaps and degraded states must remain visible; no missing source is "
            "treated as a true zero.\n\n"
            "## Recommended Actions\n"
            "Use the stable record identifiers in the workbook to assign ownership, "
            "confirm the due date, and record the next decision."
        )

    def support_cases(_ctx: object, account_ids: list[str], _days: int, limit: int = 50000) -> pd.DataFrame:
        frame = _filter_frame(
            _frame(bundle, "support_cases"), account_ids, ("ACCOUNT_ID_C", "ACCOUNT_ID")
        ).head(limit)
        frame["ACCOUNT_ID"] = frame.get("ACCOUNT_ID", frame.get("ACCOUNT_ID_C"))
        frame["CREATED_DATE"] = frame.get("CREATED_DATE", frame.get("DATE_OPENED"))
        frame["DESCRIPTION_C"] = frame.get("DESCRIPTION_C", frame.get("DESCRIPTION"))
        frame.attrs["was_truncated"] = bool(len(frame) >= limit)
        frame.attrs["fetch_limit"] = limit
        return frame

    functions: Mapping[str, Callable[..., Any]] = {
        "_connect_with_keeper": connect,
        "get_subscriptions_for_team": subscriptions_for_team,
        "fetch_adoption_barriers": by_accounts("adoption_barriers", ("ACCOUNT_ID_C",)),
        "load_csone_excel": csone_loader,
        "fetch_help_webex_bugs": external_fetch("external_bugs"),
        "fetch_status_incidents": external_fetch("external_incidents"),
        "fetch_status_maintenances": external_fetch("external_maintenances"),
        "fetch_csconsole_action_plans": action_plans,
        "fetch_csconsole_customer_pulse": by_accounts(
            "customer_pulse", ("ACCOUNT__C", "ACCOUNT_ID_C")
        ),
        "fetch_support_cases_snowflake": support_cases,
        "fetch_csconsole_success_priorities": by_accounts(
            "success_priorities", ("ACCOUNT_ID_C", "RELATED_CUSTOMER__C")
        ),
        "fetch_csconsole_adoption_barriers": by_accounts(
            "adoption_barriers", ("ACCOUNT_ID_C",)
        ),
        "fetch_subscription_data": lambda subscription_id, days=90: _subscription_payload(
            bundle, subscription_id, days
        ),
        "search_subscriptions_by_customer": search_subscriptions,
        "get_subscription_renewal_risk": renewal_risk,
        "generate_llm_response": narrative,
        "generate_llm_json_response": lambda system_prompt, user_prompt, schema, **kwargs: (
            _deterministic_json_response(
                bundle,
                system_prompt,
                user_prompt,
                schema,
                **kwargs,
            )
        ),
    }
    for name, value in functions.items():
        if hasattr(backend, name):
            installation.patch(backend, name, value)
        if hasattr(app_module, name):
            installation.patch(app_module, name, value)

    installation.patch(backend, "TEAM_ROSTER", roster)
    installation.patch(backend, "MANAGERS", sorted({row[0] for row in roster}))
    installation.patch(app_module, "TEAM_ROSTER", roster)
    installation.patch(app_module, "MANAGERS", sorted({row[0] for row in roster}))
    for name, value in {
        "fetch_period_comparison": lambda *_args, **_kwargs: {},
        "fetch_barrier_velocity": lambda *_args, **_kwargs: {},
        "fetch_enhanced_account_insights": lambda *_args, **_kwargs: {},
        "scan_historical_reports": lambda *_args, **_kwargs: [],
        "build_cross_report_trends": lambda *_args, **_kwargs: {},
    }.items():
        if hasattr(backend, name):
            installation.patch(backend, name, value)
    installation.patch(leader, "fetch_action_plans_snowflake", action_plans)
    installation.patch(
        leader.LeaderReportGenerator,
        "_fetch_adoption_barriers",
        lambda _self, account_ids, _days, owner_emails=None: _filter_frame(
            _frame(bundle, "adoption_barriers"), account_ids, ("ACCOUNT_ID_C",)
        ),
    )
    installation.patch(
        leader.LeaderReportGenerator,
        "_fetch_customer_pulse",
        lambda _self, account_ids, _days, owner_emails=None: _filter_frame(
            _frame(bundle, "customer_pulse"),
            account_ids,
            ("ACCOUNT__C", "ACCOUNT_ID_C"),
        ),
    )
    installation.patch(
        leader.LeaderReportGenerator,
        "_fetch_success_priorities",
        lambda _self, customer_names, _days: _filter_frame(
            _frame(bundle, "success_priorities"),
            customer_names,
            ("RELATED_CUSTOMER__C", "BU_NAME"),
        ),
    )
    if hasattr(app_module, "_r65_fetch_aps_snowflake"):
        def empty_secondary_action_plans(*_args: Any, **_kwargs: Any) -> pd.DataFrame:
            frame = _frame(bundle, "action_plans").iloc[0:0].copy()
            frame.attrs.update(
                {
                    "source_mode": SOURCE_MODE,
                    "source_state": "available",
                    "secondary_source_role": "no_distinct_fixture_rows",
                }
            )
            return frame

        installation.patch(
            app_module,
            "_r65_fetch_aps_snowflake",
            empty_secondary_action_plans,
        )

    prefetch_fetchers = dict(prefetch._FETCHERS)
    prefetch_fetchers.update(
        {
            "adoption_barriers": functions["fetch_adoption_barriers"],
            "csconsole_action_plans": action_plans,
            "csconsole_customer_pulse": functions["fetch_csconsole_customer_pulse"],
            "csconsole_success_priorities": functions[
                "fetch_csconsole_success_priorities"
            ],
            "csconsole_adoption_barriers": functions[
                "fetch_csconsole_adoption_barriers"
            ],
            "support_cases_snowflake": support_cases,
            "period_comparison": lambda *_args, **_kwargs: {},
            "barrier_velocity": lambda *_args, **_kwargs: {},
            "enhanced_account_insights": lambda *_args, **_kwargs: {},
        }
    )
    installation.patch(prefetch, "_FETCHERS", prefetch_fetchers)

    all_intel = lambda days_back=365: _external_intel(bundle, days_back)
    installation.patch(incident_storage, "get_all_external_intel", all_intel)
    installation.patch(
        incident_storage,
        "get_incident_statistics",
        lambda days_back=None: _external_intel(bundle, days_back or 365)["incident_stats"],
    )
    installation.patch(
        incident_storage,
        "get_bug_statistics",
        lambda days_back=None: _external_intel(bundle, days_back or 365)["bug_stats"],
    )
    installation.patch(
        incident_storage,
        "get_maintenance_statistics",
        lambda days_back=None: _external_intel(bundle, days_back or 365)[
            "maintenance_stats"
        ],
    )
    installation.patch(
        incident_storage,
        "export_all_data",
        lambda page=0, page_size=None: {
            **_external_intel(bundle, 365),
            "page": int(page),
            "page_size": page_size,
            "exported_at": bundle.as_of_utc,
            "sanitized": True,
        },
    )
    installation.patch(
        diagnostics,
        "run_connectivity_diagnostics",
        lambda _secrets=None: {
            "ok": True,
            "mode": SOURCE_MODE,
            "scenario": bundle.scenario,
            "schema_fingerprint": bundle.schema_fingerprint,
            "live_validation_performed": False,
            "canonical_counts": dict(sorted(bundle.expected_canonical_counts.items())),
            "source_states": dict(sorted(bundle.source_states.items())),
            "warning_codes": {
                name: list(codes)
                for name, codes in sorted(bundle.warning_codes.items())
            },
            "checks": [
                {
                    "name": "local_acceptance_adapter",
                    "status": "ok",
                    "ms": 0.0,
                    "detail": "Explicit sanitized loopback fixture adapter active",
                }
            ],
        },
    )

    # Healthy local acceptance keeps the real retrieval-first orchestration
    # active and replaces only the external model boundary above.  Explicit
    # provider-failure scenarios use bounded route-ready failures so HTTP
    # status and redaction behavior can be exercised without a real provider.
    if bundle.provider_state != "available":
        portfolio_ai = lambda request: _portfolio_ai_result(bundle, request)
        intel_ai = lambda question, days=365: _intel_ai_result(bundle, question, days)
        installation.patch(app_module, "run_portfolio_grounded_ask_ai", portfolio_ai)
        installation.patch(app_module, "run_intel_grounded_ask_ai", intel_ai)
    installation.patch(app_module, "is_grounded_ask_ai_enabled", lambda: True)
    installation.patch(app_module, "_check_ask_ai_throttle", lambda: None)
    installation.patch(app_module, "_r71_diag_rate_limit_check", lambda _ip: (True, 0))
    if hasattr(app_module, "get_latest_csone_from_folder_diag"):
        installation.patch(
            app_module,
            "get_latest_csone_from_folder_diag",
            lambda: (fixture_pointer, "synced", 1),
        )
        if hasattr(app_module, "get_latest_csone_from_folder"):
            installation.patch(
                app_module,
                "get_latest_csone_from_folder",
                lambda: fixture_pointer,
            )
    if hasattr(app_module, "_resolve_csone_path_safe"):
        original_csone_resolver = app_module._resolve_csone_path_safe

        def resolve_fixture_csone_path(value: object) -> str | None:
            try:
                if Path(str(value)).resolve() == Path(fixture_pointer).resolve():
                    return fixture_pointer
            except (OSError, RuntimeError, ValueError):
                pass
            return original_csone_resolver(value)

        installation.patch(
            app_module,
            "_resolve_csone_path_safe",
            resolve_fixture_csone_path,
        )
    installation.patch(
        app_module,
        "_r74_generate_follow_up_suggestions",
        lambda **_kwargs: [
            "Which overdue Action Plans need an owner decision?",
            "Which cited support cases require escalation?",
        ],
    )
    if hasattr(app_module, "_r144_start_external_intel_refresh"):
        installation.patch(app_module, "_r144_start_external_intel_refresh", lambda: "fixture")
    installation.patch(
        model_resolver,
        "get_active_ask_ai_model",
        lambda: "local-acceptance-deterministic",
    )
    installation.patch(
        model_resolver,
        "get_active_report_model",
        lambda: "local-acceptance-deterministic",
    )

    class _LocalCircuitClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def complete(self, _system_prompt: object, _user_prompt: object) -> str:
            if bundle.provider_state == "available":
                return "OK"
            return f"ERROR: provider_{bundle.provider_state}"

    local_circuit_config = dict(getattr(backend, "CIRCUIT_CONFIG", {}) or {})
    local_circuit_config.update(
        {
            "client_id": "sanitized-local-acceptance",
            "client_secret": "sanitized-local-acceptance",
            "app_key": "sanitized-local-acceptance",
            "model_name": "local-acceptance-deterministic",
            "model_name_ask_ai": "local-acceptance-deterministic",
            "model_name_report": "local-acceptance-deterministic",
        }
    )
    installation.patch(backend, "CIRCUIT_CONFIG", local_circuit_config)
    installation.patch(backend, "CircuitChatClient", _LocalCircuitClient)

    _patch_corpus(installation, bundle)
    if hasattr(app_module, "Config"):
        installation.patch(app_module.Config, "CORPUS_KNOWLEDGE_ENABLED", True)
    for key, value in {
        "LOCAL_ACCEPTANCE_MODE": True,
        "LOCAL_ACCEPTANCE_SCENARIO": bundle.scenario,
        "LOCAL_ACCEPTANCE_SCHEMA_FINGERPRINT": bundle.schema_fingerprint,
        "LOCAL_ACCEPTANCE_LIVE_VALIDATION": False,
        "LOCAL_ACCEPTANCE_AS_OF_UTC": bundle.as_of_utc,
    }.items():
        installation.patch_mapping(app_module.app.config, key, value)
    return installation


def installation_summary(installation: RuntimeInstallation) -> dict[str, Any]:
    """Return a redacted, record-free description for startup output."""

    payload = installation.bundle.redacted_summary()
    payload.update(
        {
            "runtime_adapters_installed": True,
            "production_accuracy_claimed": False,
        }
    )
    return payload
