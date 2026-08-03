#!/usr/bin/env python3
"""Round 144 live acceptance for AdoptIQ AI-assisted features.

The runner targets only a loopback AdoptIQ instance. It exercises the grounded
portfolio Ask AI sync and SSE paths, conversation history, suggestions,
diagnostics, evidence lookup, support-case search, Ask Intel, Customer 360,
Playbook, corpus status, model resolution, and provider connectivity twice.

Two artifacts are written:

* ``ai_feature_acceptance_summary.json`` is redacted and safe for a release
  decision. It contains hashes, counts, states, and validation findings.
* ``ai_feature_acceptance_sensitive_evidence.json`` contains answers and source
  records needed for claim-by-claim review. It is permission-restricted where
  supported and must never be committed or copied off the work machine.

Automated checks do not replace source-record review. The summary deliberately
keeps ``release_ready`` false until the required manual/work-agent review is
recorded outside this runner.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from report_iteration_loop import extract_csrf_token  # noqa: E402
from scripts.run_decision_report_acceptance import (  # noqa: E402
    _ensure_safe_output_dir,
    _utc_now,
    _write_json,
)


SUMMARY_SCHEMA = "ai-feature-acceptance/v1"
QUERY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
CITATION_RE = re.compile(r"\[Sources?:\s*([^\]]+)\]", re.IGNORECASE)
FACT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[$€£])?-?\d[\d,]*(?:\.\d+)?%?",
)
EVIDENCE_GAP_RE = re.compile(
    r"\b(insufficient|not provided|unavailable|cannot determine|"
    r"absent|not (?:in|present in) (?:the )?(?:data|evidence)|"
    r"does not contain|no (?:grounded |supporting )?evidence|missing evidence)\b",
    re.IGNORECASE,
)
@dataclass(frozen=True)
class QuestionCase:
    """One fixed live AI question and delivery path."""

    key: str
    route: str
    question: str
    require_citations: bool = True
    require_evidence_gap: bool = False
    use_conversation_history: bool = False


def build_question_cases(customer_name: str) -> tuple[QuestionCase, ...]:
    """Return the fixed question set used in both live passes."""

    customer = str(customer_name or "the selected customer").strip()
    return (
        QuestionCase(
            key="portfolio_headline_sync",
            route="portfolio_sync",
            question=(
                "State the exact customer, subscription, open Action Plan, open adoption "
                "barrier, and TAC case counts for this scope. Cite every material claim "
                "and disclose any unavailable or truncated source."
            ),
        ),
        QuestionCase(
            key="action_plan_details_sync",
            route="portfolio_sync",
            question=(
                "List the highest-priority open or overdue Action Plans with stable ID, "
                "customer, owner, status, and due date. Do not infer absent fields."
            ),
        ),
        QuestionCase(
            key="support_case_search_sync",
            route="portfolio_sync",
            question=(
                "Find support cases whose narratives mention registration, authentication, "
                "compliance, security, or outage. Return the case ID, customer, status, "
                "severity, and opened date, and state when the evidence is incomplete."
            ),
        ),
        QuestionCase(
            key="customer_risk_sync",
            route="portfolio_sync",
            question=(
                f"For {customer}, explain the current evidence-backed risk, the top open "
                "issues, the responsible owners, and the next action. Cite every claim."
            ),
        ),
        QuestionCase(
            key="portfolio_risk_stream",
            route="portfolio_stream",
            question=(
                "Summarize the highest-risk accounts and the evidence-backed action that "
                "should be taken first for each. Cite every account-level claim."
            ),
        ),
        QuestionCase(
            key="conversation_follow_up_sync",
            route="portfolio_sync",
            question=(
                "Using the prior answer only as conversational context, identify the one "
                "decision that should be made first and support it with current source IDs."
            ),
            use_conversation_history=True,
        ),
        QuestionCase(
            key="unanswerable_sync",
            route="portfolio_sync",
            question=(
                "Provide an exact customer-satisfaction score for a source and period that "
                "are not present in the retrieved evidence. Do not estimate or substitute."
            ),
            require_citations=False,
            require_evidence_gap=True,
        ),
        QuestionCase(
            key="external_intelligence",
            route="ask_intel",
            question=(
                "Which recent incidents, maintenances, or known bugs have evidence of impact "
                "on this portfolio? Cite every incident, bug, or case ID and disclose gaps."
            ),
        ),
    )


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_id(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def extract_citations(answer: str) -> list[str]:
    """Extract the unique source IDs rendered in supported findings."""

    found: set[str] = set()
    for match in CITATION_RE.finditer(str(answer or "")):
        for item in match.group(1).split(","):
            cleaned = str(item or "").strip()
            if cleaned:
                found.add(cleaned)
    return sorted(found, key=str.casefold)


def _evidence_ids(payload: Mapping[str, Any]) -> list[str]:
    found: set[str] = set()
    for key in ("evidence_index", "evidence_records"):
        rows = payload.get(key) or []
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            source_id = row.get("source_id") or row.get("citation_id") or row.get("id")
            if str(source_id or "").strip():
                found.add(str(source_id).strip())
    return sorted(found, key=str.casefold)


def _fact_tokens(answer: str) -> list[str]:
    without_citations = CITATION_RE.sub("", str(answer or ""))
    return sorted(set(FACT_TOKEN_RE.findall(without_citations)))


def _warning_kinds(value: Any) -> list[str]:
    kinds: set[str] = set()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    for item in value:
        if isinstance(item, Mapping):
            for key in ("kind", "state", "error_kind", "dataset"):
                text = str(item.get(key) or "").strip()
                if text:
                    kinds.add(text[:120])
    return sorted(kinds, key=str.casefold)


def _evidence_type_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    counts: collections.Counter[str] = collections.Counter()
    rows = payload.get("evidence_records") or payload.get("evidence_index") or []
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
        for row in rows:
            if isinstance(row, Mapping):
                source_type = str(row.get("source_type") or "unknown").strip() or "unknown"
                counts[source_type] += 1
    return dict(sorted(counts.items(), key=lambda item: item[0].casefold()))


def _numeric_projection(value: Any) -> Any:
    """Preserve numeric/boolean structure while excluding live strings."""

    if isinstance(value, Mapping):
        return {
            str(key): projected
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if (projected := _numeric_projection(item)) is not None
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        projected = [_numeric_projection(item) for item in value]
        return [item for item in projected if item is not None]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return None


def validate_portfolio_payload(
    payload: Mapping[str, Any],
    *,
    require_citations: bool,
    require_evidence_gap: bool,
) -> list[str]:
    """Validate one grounded portfolio response without trusting prose."""

    errors: list[str] = []
    answer = str(payload.get("answer") or "").strip()
    if not payload.get("ok"):
        errors.append("response ok flag is false")
    if str(payload.get("mode") or "") != "grounded":
        errors.append("response mode is not grounded")
    if not answer:
        errors.append("answer is empty")
    if answer.startswith("ERROR:"):
        errors.append("raw provider error reached the answer")
    query_id = str(payload.get("query_id") or "")
    if not QUERY_ID_RE.fullmatch(query_id):
        errors.append("query_id is missing or invalid")
    retrieval_method = str(payload.get("retrieval_method") or "").casefold()
    if retrieval_method in {"", "unknown", "unavailable"}:
        errors.append("retrieval method is unavailable")
    if not str(payload.get("model_name") or "").strip():
        errors.append("active model name is missing")

    citations = extract_citations(answer)
    allowed = _evidence_ids(payload)
    allowed_normalized = {_normalize_id(item) for item in allowed}
    unsupported = [
        citation
        for citation in citations
        if _normalize_id(citation) not in allowed_normalized
    ]
    if require_citations and not citations:
        errors.append("answer contains no source citations")
    if unsupported:
        errors.append(f"answer contains {len(unsupported)} unsupported citation(s)")
    if require_citations and not allowed:
        errors.append("response contains no evidence index or records")
    if require_evidence_gap and not EVIDENCE_GAP_RE.search(answer):
        errors.append("unanswerable question did not disclose an evidence gap")
    if payload.get("evidence_truncated"):
        errors.append("evidence was truncated")
    if payload.get("account_batch_truncated"):
        errors.append("account batch was truncated")
    return errors


def validate_ask_intel_payload(payload: Mapping[str, Any]) -> list[str]:
    """Validate grounded Ask Intel response shape and visible citations."""

    errors: list[str] = []
    answer = str(payload.get("answer") or "").strip()
    if not payload.get("ok"):
        errors.append("Ask Intel ok flag is false")
    if str(payload.get("mode") or "") != "grounded":
        errors.append("Ask Intel mode is not grounded")
    if not answer:
        errors.append("Ask Intel answer is empty")
    if answer.startswith("ERROR:"):
        errors.append("raw provider error reached Ask Intel")
    if not extract_citations(answer):
        errors.append("Ask Intel answer contains no source citations")
    return errors


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse the app's bounded JSON SSE response."""

    events: list[tuple[str, dict[str, Any]]] = []
    normalized = str(text or "").replace("\r\n", "\n")
    for block in re.split(r"\n\n+", normalized):
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid SSE JSON for event {event_name}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"SSE event {event_name} payload must be an object")
        events.append((event_name, payload))
    return events


def _stream_payload(events: Sequence[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    errors = [payload for name, payload in events if name == "error"]
    meta = next((dict(payload) for name, payload in events if name == "meta"), {})
    done = next((dict(payload) for name, payload in events if name == "done"), {})
    chunks = [str(payload.get("chunk") or "") for name, payload in events if name == "data"]
    out: dict[str, Any] = {
        "ok": bool(meta) and not errors and bool(done),
        "mode": "grounded",
        "answer": "".join(chunks),
        **meta,
        "follow_up_suggestions": done.get("follow_up_suggestions") or [],
    }
    if errors:
        out["stream_errors"] = [str(item.get("error") or "stream error") for item in errors]
    return out


class AiFeatureClient:
    """Loopback-only HTTP client for live AI feature acceptance."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout: int,
        pace_seconds: float,
        max_rate_retries: int,
    ) -> None:
        parsed = urlparse(base_url)
        if (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost"}:
            raise ValueError("--base-url must target the local AdoptIQ app")
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("--base-url must use http or https")
        self.base_url = base_url.rstrip("/")
        self.request_timeout = max(int(request_timeout), 10)
        self.pace_seconds = max(float(pace_seconds), 0.0)
        self.max_rate_retries = max(int(max_rate_retries), 0)
        self.session = requests.Session()
        self.csrf_token = ""
        self.session_cookie = ""

    def bootstrap(self) -> None:
        response = self.session.get(f"{self.base_url}/", timeout=self.request_timeout)
        response.raise_for_status()
        self.csrf_token = extract_csrf_token(response.text)
        self.session_cookie = self.session.cookies.get("session", "") or ""

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": self.csrf_token,
        }
        if self.session_cookie:
            headers["Cookie"] = f"session={self.session_cookie}"
        return headers

    def _sleep_after_expensive_call(self) -> None:
        if self.pace_seconds > 0:
            time.sleep(self.pace_seconds)

    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=dict(params or {}),
            headers=self._headers(),
            timeout=self.request_timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"ok": False, "error": "invalid_json_response"}
        return response.status_code, payload if isinstance(payload, dict) else {}

    def get_page(self, path: str) -> tuple[int, str, str]:
        response = self.session.get(
            f"{self.base_url}{path}",
            headers=self._headers(accept="text/html"),
            timeout=self.request_timeout,
        )
        return response.status_code, str(response.headers.get("Content-Type") or ""), response.text

    def post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        expensive: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        for attempt in range(self.max_rate_retries + 1):
            response = self.session.post(
                f"{self.base_url}{path}",
                json=dict(payload),
                headers=self._headers(),
                timeout=self.request_timeout,
            )
            try:
                body = response.json()
            except ValueError:
                body = {"ok": False, "error": "invalid_json_response"}
            if response.status_code != 429 or attempt >= self.max_rate_retries:
                if expensive:
                    self._sleep_after_expensive_call()
                return response.status_code, body if isinstance(body, dict) else {}
            retry_after = body.get("retry_after_seconds") or response.headers.get("Retry-After") or 1
            try:
                wait_seconds = max(1, min(int(retry_after), 60))
            except (TypeError, ValueError):
                wait_seconds = 1
            time.sleep(wait_seconds)
        raise AssertionError("unreachable rate-retry loop")

    def post_form_page(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        expensive: bool = False,
    ) -> tuple[int, str, str]:
        response = self.session.post(
            f"{self.base_url}{path}",
            data=dict(payload),
            headers=self._headers(accept="text/html"),
            timeout=self.request_timeout,
        )
        if expensive:
            self._sleep_after_expensive_call()
        return response.status_code, str(response.headers.get("Content-Type") or ""), response.text

    def post_stream(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> tuple[int, str, list[tuple[str, dict[str, Any]]]]:
        response = self.session.post(
            f"{self.base_url}{path}",
            json=dict(payload),
            headers=self._headers(accept="text/event-stream"),
            timeout=self.request_timeout,
        )
        content_type = str(response.headers.get("Content-Type") or "")
        if response.status_code == 429:
            self._sleep_after_expensive_call()
            return response.status_code, content_type, []
        events = parse_sse(response.text) if "text/event-stream" in content_type else []
        self._sleep_after_expensive_call()
        return response.status_code, content_type, events


def _scenario_redaction(
    *,
    case: QuestionCase,
    payload: Mapping[str, Any],
    errors: Sequence[str],
    duration_ms: int,
    evidence_lookup_ok: bool | None,
    diagnostics_ok: bool | None,
) -> dict[str, Any]:
    answer = str(payload.get("answer") or "")
    citations = extract_citations(answer)
    evidence_ids = _evidence_ids(payload)
    fact_tokens = _fact_tokens(answer)
    canonical_headline = payload.get("canonical_headline") or {}
    warnings = _warning_kinds(payload.get("partial_data_warnings"))
    return {
        "scenario": case.key,
        "route": case.route,
        "ok": not errors,
        "errors": list(errors),
        "duration_ms": duration_ms,
        "mode": str(payload.get("mode") or ""),
        "model_name": str(payload.get("model_name") or ""),
        "retrieval_method": str(payload.get("retrieval_method") or ""),
        "answer_characters": len(answer),
        "answer_sha256": _digest(answer),
        "fact_token_count": len(fact_tokens),
        "fact_token_hashes": sorted(_digest(item) for item in fact_tokens),
        "citation_count": len(citations),
        "citation_id_hashes": sorted(_digest(item) for item in citations),
        "evidence_id_count": len(evidence_ids),
        "evidence_id_hashes": sorted(_digest(item) for item in evidence_ids),
        "evidence_type_counts": _evidence_type_counts(payload),
        "canonical_headline_sha256": _digest(canonical_headline),
        "canonical_correction_count": len(payload.get("canonical_corrections") or []),
        "canonical_verified_count": len(payload.get("canonical_verified") or []),
        "partial_warning_kinds": warnings,
        "evidence_truncated": bool(payload.get("evidence_truncated")),
        "account_batch_truncated": bool(payload.get("account_batch_truncated")),
        "evidence_lookup_ok": evidence_lookup_ok,
        "diagnostics_ok": diagnostics_ok,
        "follow_up_suggestion_count": len(payload.get("follow_up_suggestions") or []),
    }


def compare_passes(passes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require stable facts and source IDs for every fixed scenario."""

    if len(passes) != 2:
        return {"ok": False, "errors": ["exactly two AI acceptance passes are required"]}
    first = passes[0].get("scenarios") or {}
    second = passes[1].get("scenarios") or {}
    errors: list[str] = []
    scenario_results: dict[str, Any] = {}
    for key in sorted(set(first) | set(second)):
        left = first.get(key)
        right = second.get(key)
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            errors.append(f"{key}: missing from one pass")
            scenario_results[key] = {"ok": False, "missing": True}
            continue
        fields = (
            "fact_token_hashes",
            "citation_id_hashes",
            "canonical_headline_sha256",
        )
        drift = [field for field in fields if left.get(field) != right.get(field)]
        ok = not drift
        scenario_results[key] = {"ok": ok, "drift_fields": drift}
        if drift:
            errors.append(f"{key}: semantic drift in {', '.join(drift)}")
    return {"ok": not errors, "errors": errors, "scenarios": scenario_results}


def _safe_page_result(status: int, content_type: str, body: str) -> dict[str, Any]:
    return {
        "ok": status == 200 and "text/html" in content_type.casefold() and len(body) > 200,
        "status_code": status,
        "content_type": content_type.split(";", 1)[0],
        "body_characters": len(body),
        "body_sha256": _digest(body),
    }


def _corpus_feature_page_result(
    status: int,
    content_type: str,
    body: str,
    *,
    required_markers: Sequence[str],
) -> dict[str, Any]:
    """Validate that a corpus page rendered usable evidence, not just HTML.

    Customer 360 and Playbook deliberately return a fully rendered page when
    the corpus is disabled, unavailable, or has no matching records.  An HTTP
    200 therefore proves only that Flask and Jinja worked.  Live acceptance
    must also reject warning/error/info banners and require the result-only
    markers that are emitted when corpus-backed content is present.
    """

    result = _safe_page_result(status, content_type, body)
    try:
        soup = BeautifulSoup(str(body or ""), "html.parser")
        page_text = " ".join(soup.stripped_strings)
        normalized_text = page_text.casefold()
        alert_nodes = soup.select(".alert-danger, .alert-warning, .alert-info")
        alert_texts = [" ".join(node.stripped_strings) for node in alert_nodes]
        marker_presence = [
            marker.casefold() in normalized_text
            for marker in required_markers
        ]
        missing_marker_hashes = sorted(
            _digest(marker)
            for marker, present in zip(required_markers, marker_presence, strict=True)
            if not present
        )
        result.update(
            {
                "alert_count": len(alert_texts),
                "alert_sha256s": sorted(_digest(item) for item in alert_texts),
                "required_marker_count": len(marker_presence),
                "required_markers_found": sum(marker_presence),
                "missing_marker_sha256s": missing_marker_hashes,
            }
        )
        result["ok"] = bool(
            result["ok"]
            and not alert_texts
            and marker_presence
            and all(marker_presence)
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on malformed HTML
        result.update(
            {
                "ok": False,
                "alert_count": None,
                "alert_sha256s": [],
                "required_marker_count": len(required_markers),
                "required_markers_found": None,
                "missing_marker_sha256s": [],
                "parse_error": type(exc).__name__,
            }
        )
    return result


def _run_preflight(
    client: AiFeatureClient,
    *,
    manager: str,
    technology: str,
    days: int,
    customer_name: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Exercise read-only feature state and model/provider connectivity."""

    errors: list[str] = []
    redacted: dict[str, Any] = {}
    sensitive: dict[str, Any] = {}
    for name, path in (
        ("version", "/api/version"),
        ("connectivity", "/api/diag/connectivity"),
        ("corpus_status", "/api/corpus/status"),
        ("intelligence_status_alias", "/api/intel/status"),
        ("ask_ai_model", "/api/settings/ask-ai-model"),
        ("report_model", "/api/settings/report-model"),
    ):
        status, payload = client.get_json(path)
        sensitive[name] = payload
        redacted[name] = {
            "status_code": status,
            "ok": status == 200 and bool(payload.get("ok", True)),
            "payload_sha256": _digest(payload),
        }
        if status != 200 or not bool(payload.get("ok", True)):
            errors.append(f"{name} preflight failed")

    corpus_payload = sensitive.get("corpus_status") or {}
    alias_payload = sensitive.get("intelligence_status_alias") or {}
    if not corpus_payload.get("enabled"):
        errors.append("encrypted corpus is not enabled")
    if not corpus_payload.get("available"):
        errors.append("encrypted corpus is not available")
    if corpus_payload != alias_payload:
        errors.append("intelligence status alias does not match corpus status")
    if bool((corpus_payload.get("boot") or {}).get("in_progress")):
        errors.append("encrypted corpus indexing is still in progress")
    corpus_counts = corpus_payload.get("corpus") or {}
    if int(corpus_counts.get("customers") or 0) < 1:
        errors.append("encrypted corpus contains no customers")
    if int(corpus_counts.get("chunks") or 0) < 1:
        errors.append("encrypted corpus contains no searchable chunks")
    redacted["corpus_status"].update(
        {
            "enabled": bool(corpus_payload.get("enabled")),
            "available": bool(corpus_payload.get("available")),
            "boot_in_progress": bool((corpus_payload.get("boot") or {}).get("in_progress")),
            "retrieval_method": str(
                (corpus_payload.get("boot") or {}).get("ask_ai_retrieval_method") or ""
            ),
            "contains_customers": int(corpus_counts.get("customers") or 0) > 0,
            "contains_cases": int(corpus_counts.get("cases") or 0) > 0,
            "contains_searchable_chunks": int(corpus_counts.get("chunks") or 0) > 0,
            "corpus_counts_sha256": _digest(_numeric_projection(corpus_counts)),
            "alias_payload_matches": corpus_payload == alias_payload,
        }
    )

    status, suggestions = client.get_json(
        "/api/ask-ai/suggestions",
        params={"manager": manager, "technology": technology, "days": days},
    )
    sensitive["suggestions"] = suggestions
    suggestion_rows = suggestions.get("suggestions") or []
    redacted["suggestions"] = {
        "status_code": status,
        "ok": status == 200 and bool(suggestions.get("ok")) and len(suggestion_rows) >= 4,
        "count": len(suggestion_rows),
        "payload_sha256": _digest(suggestions),
    }
    if not redacted["suggestions"]["ok"]:
        errors.append("personalized suggestion chips failed")

    for setting_name in ("ask_ai_model", "report_model"):
        model_payload = sensitive.get(setting_name) or {}
        model_name = str(model_payload.get("active_value") or "").strip()
        if not model_name:
            errors.append(f"{setting_name} has no active model")
            continue
        status, ping = client.post_json(
            "/api/llm/ping",
            {"model_name": model_name},
            expensive=False,
        )
        sensitive[f"{setting_name}_ping"] = ping
        redacted[f"{setting_name}_ping"] = {
            "status_code": status,
            "ok": status == 200 and bool(ping.get("ok")),
            "model_name": model_name,
            "latency_ms": ping.get("latency_ms"),
        }
        if not redacted[f"{setting_name}_ping"]["ok"]:
            errors.append(f"{setting_name} provider ping failed")

    customer_status, customer_type, customer_page = client.get_page(
        f"/customer/{quote(customer_name, safe='')}",
    )
    redacted["customer_360"] = _corpus_feature_page_result(
        customer_status,
        customer_type,
        customer_page,
        required_markers=("Customer 360", customer_name, "Cases timeline"),
    )
    sensitive["customer_360"] = {
        "status_code": customer_status,
        "content_type": customer_type,
        "body": customer_page,
    }
    if not redacted["customer_360"]["ok"]:
        errors.append("Customer 360 page failed")

    ext_status, ext_type, ext_page = client.get_page("/external-intelligence")
    redacted["external_intelligence_page"] = _safe_page_result(ext_status, ext_type, ext_page)
    sensitive["external_intelligence_page"] = {
        "status_code": ext_status,
        "content_type": ext_type,
        "body": ext_page,
    }
    if not redacted["external_intelligence_page"]["ok"]:
        errors.append("External Intelligence page failed")

    export_status, intel_export = client.get_json(
        "/api/export-intel",
        params={"page": 0, "page_size": 1},
    )
    intel_totals = intel_export.get("totals") or {}
    intel_truncated = intel_export.get("truncated") or {}
    redacted["external_intelligence_export"] = {
        "status_code": export_status,
        "ok": export_status == 200 and bool(intel_export.get("schema_version")),
        "totals": _numeric_projection(intel_totals),
        "truncated": _numeric_projection(intel_truncated),
        "payload_sha256": _digest(intel_export),
    }
    sensitive["external_intelligence_export"] = intel_export
    if not redacted["external_intelligence_export"]["ok"]:
        errors.append("External Intelligence export probe failed")

    playbook_status, playbook_type, playbook_page = client.post_form_page(
        "/playbook",
        {
            "technology": "",
            "theme": "",
            "query": "registration authentication outage",
            "csrf_token": client.csrf_token,
        },
        expensive=True,
    )
    redacted["playbook"] = _corpus_feature_page_result(
        playbook_status,
        playbook_type,
        playbook_page,
        required_markers=(
            "Troubleshooting Playbook",
            "Search matches for:",
            "registration authentication outage",
        ),
    )
    sensitive["playbook"] = {
        "status_code": playbook_status,
        "content_type": playbook_type,
        "body": playbook_page,
    }
    if not redacted["playbook"]["ok"]:
        errors.append("corpus Playbook search failed")
    return redacted, sensitive, errors


def _lookup_evidence_and_diagnostics(
    client: AiFeatureClient,
    payload: Mapping[str, Any],
) -> tuple[bool | None, bool | None, dict[str, Any]]:
    query_id = str(payload.get("query_id") or "")
    citations = extract_citations(str(payload.get("answer") or ""))
    evidence_lookup_ok: bool | None = None
    diagnostics_ok: bool | None = None
    evidence: dict[str, Any] = {}
    if QUERY_ID_RE.fullmatch(query_id):
        status, diag = client.get_json(f"/api/ask-ai/diagnostics/{quote(query_id)}")
        diagnostics_ok = status == 200 and bool(diag.get("ok"))
        evidence["diagnostics"] = diag
        if citations:
            source_id = citations[0]
            status, row = client.get_json(
                f"/api/ask-ai/evidence/{quote(query_id)}/{quote(source_id, safe='')}",
            )
            record = row.get("record") if isinstance(row, Mapping) else None
            returned_id = ""
            if isinstance(record, Mapping):
                returned_id = str(
                    record.get("source_id") or record.get("citation_id") or record.get("id") or ""
                )
            evidence_lookup_ok = (
                status == 200
                and bool(row.get("ok"))
                and _normalize_id(returned_id) == _normalize_id(source_id)
            )
            evidence["lookup"] = row
    return evidence_lookup_ok, diagnostics_ok, evidence


def _run_pass(
    client: AiFeatureClient,
    *,
    pass_number: int,
    cases: Sequence[QuestionCase],
    manager: str,
    technology: str,
    days: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    redacted: dict[str, Any] = {"pass_number": pass_number, "scenarios": {}}
    sensitive: dict[str, Any] = {"pass_number": pass_number, "scenarios": {}}
    conversation_seed: tuple[str, str] | None = None
    for case in cases:
        request_payload: dict[str, Any] = {
            "question": case.question,
            "manager": manager,
            "technology": technology,
            "days": days,
            "allow_legacy_fallback": False,
        }
        if case.use_conversation_history and conversation_seed is not None:
            request_payload["conversation_history"] = [
                {"q": conversation_seed[0], "a": conversation_seed[1]},
            ]
        started = time.monotonic()
        status_code = 0
        content_type = "application/json"
        if case.route == "portfolio_sync":
            status_code, payload = client.post_json(
                "/api/ask-ai-portfolio",
                request_payload,
                expensive=True,
            )
            errors = validate_portfolio_payload(
                payload,
                require_citations=case.require_citations,
                require_evidence_gap=case.require_evidence_gap,
            )
        elif case.route == "portfolio_stream":
            status_code, content_type, events = client.post_stream(
                "/api/ask-ai-portfolio/stream",
                request_payload,
            )
            payload = _stream_payload(events)
            errors = validate_portfolio_payload(
                payload,
                require_citations=case.require_citations,
                require_evidence_gap=case.require_evidence_gap,
            )
            if "text/event-stream" not in content_type:
                errors.append("stream response content type is not text/event-stream")
        elif case.route == "ask_intel":
            status_code, payload = client.post_json(
                "/api/ask-intel",
                {
                    "question": case.question,
                    "days": days,
                    "allow_legacy_fallback": False,
                },
                expensive=True,
            )
            errors = validate_ask_intel_payload(payload)
        else:
            raise ValueError(f"unsupported case route: {case.route}")
        if status_code != 200:
            errors.append(f"HTTP status {status_code}")
        duration_ms = int((time.monotonic() - started) * 1000)

        evidence_lookup_ok: bool | None = None
        diagnostics_ok: bool | None = None
        supporting_evidence: dict[str, Any] = {}
        if case.route.startswith("portfolio") and payload.get("ok"):
            evidence_lookup_ok, diagnostics_ok, supporting_evidence = (
                _lookup_evidence_and_diagnostics(client, payload)
            )
            if case.require_citations and evidence_lookup_ok is not True:
                errors.append("citation evidence lookup failed")
            if diagnostics_ok is not True:
                errors.append("query diagnostics lookup failed")

        redacted["scenarios"][case.key] = _scenario_redaction(
            case=case,
            payload=payload,
            errors=errors,
            duration_ms=duration_ms,
            evidence_lookup_ok=evidence_lookup_ok,
            diagnostics_ok=diagnostics_ok,
        )
        sensitive["scenarios"][case.key] = {
            "request": request_payload,
            "status_code": status_code,
            "content_type": content_type,
            "response": payload,
            "supporting_evidence": supporting_evidence,
            "errors": errors,
        }
        if conversation_seed is None and case.route.startswith("portfolio"):
            answer = str(payload.get("answer") or "").strip()
            if answer:
                conversation_seed = (case.question, answer)
    redacted["ok"] = all(
        bool(item.get("ok"))
        for item in redacted["scenarios"].values()
    )
    return redacted, sensitive


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run two-pass live acceptance for AdoptIQ AI-assisted features.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:5151")
    parser.add_argument("--manager", required=True)
    parser.add_argument("--technology", default="All")
    parser.add_argument("--customer-name", required=True)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--request-timeout", type=int, default=300)
    parser.add_argument(
        "--pace-seconds",
        type=float,
        default=7.0,
        help="Delay after expensive calls to respect the app's default 10/minute throttle",
    )
    parser.add_argument("--max-rate-retries", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = str(args.manager or "").strip()
    technology = str(args.technology or "").strip() or "All"
    customer_name = str(args.customer_name or "").strip()
    if not manager:
        parser.error("--manager cannot be blank")
    if not customer_name:
        parser.error("--customer-name cannot be blank")
    if not 1 <= int(args.days) <= 365:
        parser.error("--days must be between 1 and 365")
    try:
        output_dir = _ensure_safe_output_dir(args.output_dir)
        client = AiFeatureClient(
            base_url=args.base_url,
            request_timeout=args.request_timeout,
            pace_seconds=args.pace_seconds,
            max_rate_retries=args.max_rate_retries,
        )
    except ValueError as exc:
        parser.error(str(exc))

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "ai_feature_acceptance_summary.json"
    evidence_path = output_dir / "ai_feature_acceptance_sensitive_evidence.json"
    started_at = _utc_now()
    scope_digest = _digest(
        {"manager": manager, "technology": technology, "customer_name": customer_name},
    )
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA,
        "sensitive": False,
        "do_not_commit": True,
        "started_at_utc": started_at,
        "live_validation_attempted": False,
        "live_validation_performed": False,
        "live_validation_passed": False,
        "base_url": args.base_url,
        "scope_sha256": scope_digest,
        "days": int(args.days),
        "preflight": {},
        "passes": [],
        "repeatability": {"ok": False, "errors": ["not run"]},
        "all_automated_checks_passed": False,
        "manual_review_complete": False,
        "release_ready": False,
        "manual_review_required": [
            "Reconcile every answer and report-narrative claim to the sensitive evidence and live source records.",
            "Review Customer 360 and Playbook content for scope, completeness, and prompt-injection safety.",
            "Review Comprehensive, Compact, and Renewal AI narratives and recommendations.",
            "Review Leader and Comprehensive BE-priority classification when enabled.",
            "Verify honest provider/source degraded behavior without exposing credentials or raw errors.",
        ],
        "sensitive_evidence_path": str(evidence_path),
        "failures_requiring_review": [],
    }
    sensitive: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA,
        "sensitive": True,
        "do_not_commit": True,
        "started_at_utc": started_at,
        "scope": {
            "manager": manager,
            "technology": technology,
            "customer_name": customer_name,
            "days": int(args.days),
        },
        "preflight": {},
        "passes": [],
    }

    try:
        client.bootstrap()
        summary["live_validation_attempted"] = True
        preflight, preflight_sensitive, preflight_errors = _run_preflight(
            client,
            manager=manager,
            technology=technology,
            days=int(args.days),
            customer_name=customer_name,
        )
        summary["preflight"] = preflight
        sensitive["preflight"] = preflight_sensitive
        summary["failures_requiring_review"].extend(preflight_errors)
        cases = build_question_cases(customer_name)
        for pass_number in (1, 2):
            redacted_pass, sensitive_pass = _run_pass(
                client,
                pass_number=pass_number,
                cases=cases,
                manager=manager,
                technology=technology,
                days=int(args.days),
            )
            summary["passes"].append(redacted_pass)
            sensitive["passes"].append(sensitive_pass)
        summary["live_validation_performed"] = len(summary["passes"]) == 2
        summary["repeatability"] = compare_passes(summary["passes"])
    except Exception as exc:  # noqa: BLE001
        error_type = type(exc).__name__
        error_message = str(exc)[:2000]
        summary["failures_requiring_review"].append(
            f"acceptance runner failed: {error_type} ({_digest(error_message)[:16]})",
        )
        sensitive["runner_error"] = {
            "type": error_type,
            "message": error_message,
        }

    automated_ok = (
        not summary["failures_requiring_review"]
        and all(bool(item.get("ok")) for item in summary["passes"])
        and bool(summary["repeatability"].get("ok"))
    )
    summary["all_automated_checks_passed"] = automated_ok
    summary["live_validation_passed"] = automated_ok
    summary["completed_at_utc"] = _utc_now()
    sensitive["completed_at_utc"] = summary["completed_at_utc"]
    sensitive["summary_sha256"] = _digest(summary)
    _write_json(evidence_path, sensitive)
    try:
        os.chmod(evidence_path, 0o600)
    except OSError:
        pass
    _write_json(summary_path, summary)
    print(
        json.dumps(
            {
                "all_automated_checks_passed": automated_ok,
                "release_ready": False,
                "summary_path": str(summary_path),
                "sensitive_evidence_path": str(evidence_path),
            },
            sort_keys=True,
        )
    )
    return 0 if automated_ok else 5


if __name__ == "__main__":
    raise SystemExit(main())
