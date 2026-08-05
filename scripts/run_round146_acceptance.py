#!/usr/bin/env python3
"""Portable Round 146 acceptance orchestrator.

The command has two deliberately separate profiles:

* ``local`` runs the guarded synthetic lab and labels every result as fixture
  validation.  It cannot produce a live-validation claim.
* ``work-machine`` targets an already-running loopback candidate and runs the
  live report, AI, report-matrix, workspace, and offline replay gates.

Only a redacted, allow-listed summary is retained by default.  Downloaded
acceptance copies and AI evidence live in a temporary directory that is
removed after their pass/fail and hash evidence is projected.  The candidate
application's normal report-output originals are not deleted.  Operators may
explicitly retain the acceptance copies outside the repository for manual
review.  The runner never accepts credentials or GitHub tokens as arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_acceptance_lab import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    SOURCE_MODE,
    load_manifest,
)
from scripts.run_ai_feature_acceptance import (  # noqa: E402
    _stream_payload,
    extract_citations,
    parse_sse,
)
SUMMARY_SCHEMA = "round146-portable-acceptance/v1"
WORKSPACE_SCHEMA = "manager-decision-workspace/v1"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
REQUIRED_MATRIX_BLOCKS = "A,B,C,D,E,F,G"
REQUIRED_MATRIX_BLOCK_SET = frozenset(REQUIRED_MATRIX_BLOCKS.split(","))
REQUIRED_DECISION_SCOPES = frozenset(
    {"team", "member", "customer", "comprehensive"}
)
EXPECTED_REPLAY_QUESTIONS = 75
EXPECTED_REPLAY_CANONICAL_CHECKS = 25
CSRF_META_RE = re.compile(
    r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bytes_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _safe_output_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError("acceptance output must use a dedicated directory")
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != ".adoptiq-acceptance":
        raise ValueError(
            "in-repository acceptance output must stay under .adoptiq-acceptance"
        )
    return resolved


def _safe_sensitive_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError("retained sensitive evidence must use a dedicated directory")
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("retained sensitive evidence must stay outside the repository")
    return resolved


def _loopback_base_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or hostname not in LOOPBACK_HOSTS:
        raise ValueError("base URL must target the loopback AdoptIQ candidate")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain credentials")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("base URL must not contain a path, query, or fragment")
    return str(value).rstrip("/")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status = run("status", "--porcelain")
    return {
        "sha": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(status),
        "changed_path_count": len(status.splitlines()) if status else 0,
    }


def _gate_result(
    *,
    ok: bool,
    status: str = "passed",
    **evidence: Any,
) -> dict[str, Any]:
    return {"ok": bool(ok), "status": status if ok else "failed", **evidence}


def _skipped_gate(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "skipped",
        "reason": reason,
        "acceptance_evidence": False,
    }


def _run_command(
    command: Sequence[str],
    *,
    summary_path: Path | None = None,
    projector: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one gate while retaining no command line or raw child output."""

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603
            list(command),
            cwd=REPO_ROOT,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return _gate_result(
            ok=False,
            error_kind=type(exc).__name__,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    raw_summary = _read_json(summary_path) if summary_path is not None else {}
    projected = projector(raw_summary) if projector is not None else {}
    summary_present = summary_path is None or bool(raw_summary)
    projected_ok = projected.pop("projected_ok", True)
    ok = completed.returncode == 0 and summary_present and bool(projected_ok)
    return _gate_result(
        ok=ok,
        return_code=completed.returncode,
        elapsed_seconds=round(time.monotonic() - started, 3),
        stdout_bytes=len(completed.stdout.encode("utf-8", "replace")),
        stdout_sha256=_bytes_digest(completed.stdout),
        stderr_bytes=len(completed.stderr.encode("utf-8", "replace")),
        stderr_sha256=_bytes_digest(completed.stderr),
        summary_present=summary_present,
        **projected,
    )


def _project_lab(payload: Mapping[str, Any]) -> dict[str, Any]:
    passed = bool(payload.get("all_reconciled"))
    return {
        "projected_ok": passed,
        "schema_version": str(payload.get("schema_version") or ""),
        "sanitized": payload.get("sanitized") is True,
        "scenario_count": int(payload.get("scenario_count") or 0),
        "manifest_schema_fingerprint": str(
            payload.get("manifest_schema_fingerprint") or ""
        ),
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_local_http(payload: Mapping[str, Any]) -> dict[str, Any]:
    passed = bool(
        payload.get("all_passed")
        and payload.get("source_mode") == SOURCE_MODE
        and payload.get("live_validation_performed") is False
        and payload.get("scenario_inventory_complete")
        and payload.get("report_probes_enabled")
    )
    workspace_passed = all(
        bool(
            (item.get("route_checks") or {})
            .get("manager_decision_workspace_preview", {})
            .get("ok")
        )
        for item in payload.get("results") or []
        if isinstance(item, Mapping)
    )
    return {
        "projected_ok": passed and workspace_passed,
        "schema_version": str(payload.get("schema_version") or ""),
        "sanitized": payload.get("sanitized") is True,
        "scenario_count": int(payload.get("scenario_count") or 0),
        "scenario_inventory_complete": bool(
            payload.get("scenario_inventory_complete")
        ),
        "report_probes_enabled": bool(payload.get("report_probes_enabled")),
        "workspace_previews_passed": workspace_passed,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_decision_reports(payload: Mapping[str, Any]) -> dict[str, Any]:
    scopes: set[str] = set()
    passing_scopes: set[str] = set()
    for pass_result in payload.get("passes") or []:
        if not isinstance(pass_result, Mapping):
            continue
        for name, result in (pass_result.get("scopes") or {}).items():
            scopes.add(str(name))
            if isinstance(result, Mapping) and result.get("ok"):
                passing_scopes.add(str(name))
    mode = str(payload.get("mode_executed") or "")
    passed = bool(
        payload.get("all_passed")
        and (payload.get("repeatability") or {}).get("ok")
        and len(payload.get("passes") or []) == 2
        and scopes == REQUIRED_DECISION_SCOPES
        and passing_scopes == REQUIRED_DECISION_SCOPES
    )
    return {
        "projected_ok": passed,
        "mode_executed": mode,
        "pass_count": len(payload.get("passes") or []),
        "scope_count": len(scopes),
        "passing_scope_count": len(passing_scopes),
        "repeatability_ok": bool((payload.get("repeatability") or {}).get("ok")),
        "failure_count": len(payload.get("failures_requiring_review") or []),
        "live_validation_performed": bool(
            mode == "live" and payload.get("live_validation_performed")
        ),
        "fixture_validation_performed": mode == "offline",
        "production_accuracy_claimed": False,
    }


def _project_ai(payload: Mapping[str, Any]) -> dict[str, Any]:
    passed = bool(
        payload.get("all_automated_checks_passed")
        and (payload.get("repeatability") or {}).get("ok")
        and len(payload.get("passes") or []) == 2
    )
    validation_mode = str(payload.get("validation_mode") or "")
    return {
        "projected_ok": passed,
        "validation_mode": validation_mode,
        "pass_count": len(payload.get("passes") or []),
        "repeatability_ok": bool((payload.get("repeatability") or {}).get("ok")),
        "failure_count": len(payload.get("failures_requiring_review") or []),
        "live_validation_performed": bool(
            validation_mode == "live" and payload.get("live_validation_performed")
        ),
        "fixture_validation_performed": bool(
            validation_mode == "local_acceptance"
            and payload.get("local_validation_performed")
        ),
        "manual_review_complete": False,
        "release_ready": False,
        "production_accuracy_claimed": False,
    }


def _project_matrix(payload: Mapping[str, Any]) -> dict[str, Any]:
    completed = int(payload.get("scenarios_completed") or 0)
    requested_keys = [
        str(item)
        for item in payload.get("scenario_keys_requested") or []
        if str(item)
    ]
    requested = len(requested_keys)
    requested_blocks = {
        item.split("_", 1)[0].upper()
        for item in requested_keys
        if "_" in item
    }
    all_blocks_requested = REQUIRED_MATRIX_BLOCK_SET <= requested_blocks
    passed = sum(
        1
        for item in payload.get("results") or []
        if isinstance(item, Mapping) and item.get("all_passed")
    )
    return {
        "projected_ok": bool(
            payload.get("all_passed")
            and completed > 0
            and completed == requested
            and all_blocks_requested
        ),
        "scenario_count": requested or completed,
        "completed_count": completed,
        "passed_count": passed,
        "failed_count": max(completed - passed, 0),
        "all_report_blocks_requested": all_blocks_requested,
        "production_accuracy_claimed": False,
    }


def _find_matrix_summary(path: Path) -> Path | None:
    candidates = sorted(
        path.glob("AdoptIQ_ReportOptionMatrixSummary__*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_csrf_token(html: str) -> str:
    match = CSRF_META_RE.search(html or "")
    return unescape(match.group(1)) if match else ""


def _workspace_preview_cases(
    *,
    manager: str,
    technology: str,
    days: int,
    member_email: str,
    customer_name: str,
    subscription_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    common = {
        "manager": manager,
        "technology": technology,
        "days": days,
    }
    cases: list[tuple[str, dict[str, Any]]] = [
        ("leader_team", {**common, "report_type": "leader", "scope_type": "team"}),
        (
            "comprehensive_team",
            {**common, "report_type": "comprehensive", "scope_type": "team"},
        ),
        ("compact_team", {**common, "report_type": "compact", "scope_type": "team"}),
        (
            "renewal_portfolio",
            {**common, "report_type": "renewal_portfolio", "scope_type": "team"},
        ),
    ]
    if member_email:
        cases.append(
            (
                "leader_member",
                {
                    **common,
                    "report_type": "leader",
                    "scope_type": "member",
                    "scope_value": member_email,
                },
            )
        )
    if customer_name:
        cases.extend(
            [
                (
                    "leader_customer",
                    {
                        **common,
                        "report_type": "leader",
                        "scope_type": "customer",
                        "scope_value": customer_name,
                        "member_email": member_email,
                    },
                ),
                (
                    "renewal_customer",
                    {
                        **common,
                        "report_type": "renewal",
                        "scope_type": "customer",
                        "scope_value": customer_name,
                    },
                ),
            ]
        )
    if subscription_id:
        cases.append(
            (
                "subscription",
                {
                    **common,
                    "report_type": "subscription",
                    "scope_type": "subscription",
                    "subscription_id": subscription_id,
                },
            )
        )
    return cases


def _workspace_comparison_key(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the production comparison endpoint's like-for-like scope key."""

    # Round 148: history can interleave Team, Member, Customer, and report
    # families. Select a real comparable pair instead of blindly taking the
    # two newest canonical workbooks and expecting production to widen scope.
    binding = (
        report.get("ask_ai_binding")
        if isinstance(report.get("ask_ai_binding"), Mapping)
        else {}
    )

    def value(field: str) -> str:
        candidate = report.get(field)
        if candidate in (None, ""):
            candidate = binding.get(field)
        return str(candidate or "").strip().casefold()

    return (
        value("report_type"),
        value("manager"),
        value("technology"),
        value("scope_type"),
        value("scope_value"),
        value("days"),
    )


def probe_manager_workspace(
    *,
    base_url: str,
    expect_fixture: bool,
    manager: str,
    technology: str,
    days: int,
    member_email: str,
    customer_name: str,
    subscription_id: str,
    timeout: float = 30.0,
    require_history: bool = True,
) -> dict[str, Any]:
    """Probe Round 146 routes and retain only counts, booleans, and hashes."""

    base_url = _loopback_base_url(base_url)
    session = requests.Session()
    errors: list[str] = []
    try:
        home = session.get(base_url + "/", timeout=timeout)
        csrf_token = _extract_csrf_token(home.text) if home.status_code == 200 else ""
    except (requests.RequestException, ValueError):
        csrf_token = ""
        errors.append("candidate bootstrap failed")

    try:
        connectivity_response = session.get(
            base_url + "/api/diag/connectivity", timeout=timeout
        )
        connectivity = _response_json(connectivity_response)
    except requests.RequestException:
        connectivity_response = None
        connectivity = {}
    connectivity_mode = str(connectivity.get("mode") or "").casefold()
    fixture_runtime = bool(
        connectivity_mode == SOURCE_MODE
        or "fixture" in connectivity_mode
        or (
            connectivity.get("live_validation_performed") is False
            and connectivity_mode.startswith("local")
        )
    )
    connectivity_ok = bool(
        connectivity_response is not None
        and connectivity_response.status_code == 200
        and connectivity.get("ok")
    )
    mode_ok = fixture_runtime if expect_fixture else connectivity_ok and not fixture_runtime
    if not connectivity_ok or not mode_ok:
        errors.append("candidate connectivity mode did not match the requested profile")

    preview_results: dict[str, Any] = {}
    cases = _workspace_preview_cases(
        manager=manager,
        technology=technology,
        days=days,
        member_email=member_email,
        customer_name=customer_name,
        subscription_id=subscription_id,
    )
    for label, params in cases:
        try:
            response = session.get(
                base_url + "/api/decision-workspace/scope-preview",
                params=params,
                timeout=timeout,
            )
            payload = _response_json(response)
        except requests.RequestException:
            response = None
            payload = {}
        preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
        source_mode = str(preview.get("source_mode") or "").casefold()
        fixture_claim_ok = (
            preview.get("live_validation_performed") is False
            and "fixture" in source_mode
        )
        live_claim_ok = "fixture" not in source_mode and source_mode == "live"
        ok = bool(
            response is not None
            and response.status_code == 200
            and payload.get("ok")
            and preview.get("schema") == WORKSPACE_SCHEMA
            and preview.get("report_type") == params["report_type"]
            and preview.get("scope_type") == params["scope_type"]
            and (fixture_claim_ok if expect_fixture else live_claim_ok)
        )
        preview_results[label] = {
            "ok": ok,
            "status_code": response.status_code if response is not None else 0,
            "source_count": len(preview.get("expected_sources") or []),
            "limitation_count": len(preview.get("limitations") or []),
            "payload_sha256": _digest(payload),
        }
        if not ok:
            errors.append(f"{label} preview failed")

    try:
        history_response = session.get(
            base_url + "/api/decision-workspace/history", timeout=timeout
        )
        history_payload = _response_json(history_response)
    except requests.RequestException:
        history_response = None
        history_payload = {}
    reports = history_payload.get("reports") or []
    history_ok = bool(
        history_response is not None
        and history_response.status_code == 200
        and history_payload.get("ok")
        and isinstance(reports, list)
    )
    if not history_ok or (require_history and not reports):
        errors.append("filterable report history was unavailable or empty")

    scoped_history: dict[str, Any] = {}
    if require_history:
        for scope_type in ("member", "customer"):
            try:
                scoped_response = session.get(
                    base_url + "/api/decision-workspace/history",
                    params={
                        "manager": manager,
                        "report_type": "leader",
                        "scope_type": scope_type,
                    },
                    timeout=timeout,
                )
                scoped_payload = _response_json(scoped_response)
            except requests.RequestException:
                scoped_response = None
                scoped_payload = {}
            scoped_reports = scoped_payload.get("reports") or []
            scoped_ok = bool(
                scoped_response is not None
                and scoped_response.status_code == 200
                and scoped_payload.get("ok")
                and isinstance(scoped_reports, list)
                and scoped_reports
                and all(
                    isinstance(item, Mapping)
                    and item.get("report_type") == "leader"
                    and item.get("scope_type") == scope_type
                    and item.get("manager") == manager
                    for item in scoped_reports
                )
            )
            scoped_history[scope_type] = {
                "ok": scoped_ok,
                "status_code": (
                    scoped_response.status_code
                    if scoped_response is not None
                    else 0
                ),
                "count": len(scoped_reports),
                "payload_sha256": _digest(scoped_payload),
            }
            if not scoped_ok:
                errors.append(
                    f"durable Leader {scope_type} history filter failed"
                )

    report_view_ok: bool | None = None
    report_id = ""
    canonical_workbook_ids: list[str] = []
    canonical_reports: dict[str, dict[str, Any]] = {}
    canonical_ids_by_scope: dict[tuple[str, ...], list[str]] = {}
    comparison_ids: list[str] = []
    inspected_report_count = 0
    history_candidates = [
        item
        for item in reports
        if isinstance(item, Mapping)
        and item.get("analysis_id")
        and item.get("excel_available")
    ]
    # Canonical Leader/Comprehensive outputs are the intended comparison
    # contract. Prefer them without assuming every older history row carries a
    # trustworthy workbook-shape flag, then inspect the remaining candidates.
    history_candidates.sort(
        key=lambda item: str(item.get("report_type") or "").casefold()
        not in {"leader", "comprehensive"}
    )
    for item in history_candidates[:100]:
        candidate_id = str(item.get("analysis_id") or "")
        if not candidate_id:
            continue
        try:
            report_response = session.get(
                base_url
                + "/api/decision-workspace/report/"
                + quote(candidate_id, safe=""),
                timeout=timeout,
            )
            report_payload = _response_json(report_response)
        except requests.RequestException:
            report_response = None
            report_payload = {}
        report = report_payload.get("report") if isinstance(report_payload.get("report"), dict) else {}
        inspected_report_count += 1
        candidate_view_ok = bool(
            report_response is not None
            and report_response.status_code == 200
            and report_payload.get("ok")
            and report.get("schema") == WORKSPACE_SCHEMA
            and report.get("workbook_loaded") is True
            and bool(report.get("decision_metrics"))
            and isinstance(report.get("ask_ai_binding"), dict)
            and report["ask_ai_binding"].get("fact_fingerprint")
            == report.get("fact_fingerprint")
            and not any(
                key in report
                for key in (
                    "excel_report",
                    "excel_path",
                    "word_report",
                    "word_path",
                    "report_path",
                )
            )
        )
        if candidate_view_ok and report_view_ok is not True:
            report_view_ok = True
            report_id = candidate_id
        if (
            candidate_view_ok
            and report.get("canonical_snapshot") is True
            and candidate_id not in canonical_workbook_ids
        ):
            canonical_workbook_ids.append(candidate_id)
            canonical_reports[candidate_id] = report
            comparison_key = _workspace_comparison_key(report)
            scope_ids = canonical_ids_by_scope.setdefault(comparison_key, [])
            scope_ids.append(candidate_id)
            if len(scope_ids) >= 2 and not comparison_ids:
                comparison_ids = scope_ids[:2]
        if report_view_ok is True and comparison_ids:
            break
    if history_candidates and report_view_ok is not True:
        report_view_ok = False
        errors.append("post-generation decision view failed")

    comparison_performed = len(comparison_ids) >= 2
    before_id, after_id = (
        (comparison_ids[1], comparison_ids[0])
        if comparison_performed
        else (report_id or "Round146Probe_A", report_id or "Round146Probe_A")
    )
    headers = {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    try:
        compare_response = session.post(
            base_url + "/api/decision-workspace/compare",
            json={
                "before_analysis_id": before_id,
                "after_analysis_id": after_id,
            },
            headers=headers,
            timeout=timeout,
        )
        compare_payload = _response_json(compare_response)
    except requests.RequestException:
        compare_response = None
        compare_payload = {}
    if comparison_performed:
        comparison_ok = bool(
            compare_response is not None
            and compare_response.status_code == 200
            and compare_payload.get("ok")
            and isinstance(compare_payload.get("comparison"), dict)
        )
    elif not require_history:
        comparison_ok = bool(
            compare_response is not None
            and compare_response.status_code == 400
            and compare_payload.get("ok") is False
        )
    else:
        comparison_ok = False
    if not comparison_ok:
        errors.append("report comparison contract failed")

    canonical_ai_attempted = False
    canonical_ai_sync_ok = False
    canonical_ai_stream_ok = False
    canonical_ai_sync_status = 0
    canonical_ai_stream_status = 0
    canonical_ai_citation_count = 0
    canonical_ai_answers_match = False
    canonical_ai_payload_sha256 = ""
    if canonical_workbook_ids:
        canonical_ai_attempted = True
        canonical_id = canonical_workbook_ids[0]
        canonical_report = canonical_reports.get(canonical_id, {})
        expected_binding = canonical_report.get("ask_ai_binding") or {}
        binding_keys = (
            "manager",
            "technology",
            "days",
            "scope_type",
            "scope_value",
            "report_analysis_id",
            "report_type",
            "data_as_of_utc",
            "fact_fingerprint",
        )

        def binding_matches(observed: object) -> bool:
            if not isinstance(observed, Mapping):
                return False
            expected = dict(expected_binding)
            expected["report_analysis_id"] = expected.pop(
                "analysis_id", canonical_id
            )
            return all(
                str(observed.get(key) if observed.get(key) is not None else "")
                == str(expected.get(key) if expected.get(key) is not None else "")
                for key in binding_keys
            )

        ai_request = {
            "question": (
                "State the first evidence-backed decision and cite its exact "
                "source record. Keep the selected report scope."
            ),
            "report_analysis_id": canonical_id,
            "manager": "All Managers",
            "technology": "Webex Meetings",
            "days": 1,
            "scope_type": "customer",
            "scope_value": "Out-of-scope customer",
            "allow_legacy_fallback": True,
        }
        try:
            sync_response = session.post(
                base_url + "/api/ask-ai-portfolio",
                json=ai_request,
                headers=headers,
                timeout=timeout,
            )
            sync_payload = _response_json(sync_response)
        except requests.RequestException:
            sync_response = None
            sync_payload = {}
        sync_answer = str(sync_payload.get("answer") or "")
        canonical_ai_sync_status = (
            sync_response.status_code if sync_response is not None else 0
        )
        canonical_ai_citation_count = len(extract_citations(sync_answer))
        canonical_ai_sync_ok = bool(
            sync_response is not None
            and sync_response.status_code == 200
            and sync_payload.get("ok")
            and sync_payload.get("mode") == "grounded"
            and binding_matches(sync_payload.get("scope_context"))
            and canonical_ai_citation_count
            and not sync_payload.get("fallback_available")
        )
        try:
            stream_response = session.post(
                base_url + "/api/ask-ai-portfolio/stream",
                json=ai_request,
                headers={**headers, "Accept": "text/event-stream"},
                timeout=timeout,
            )
            stream_events = (
                parse_sse(stream_response.text)
                if "text/event-stream"
                in str(stream_response.headers.get("Content-Type") or "")
                else []
            )
            stream_payload = _stream_payload(stream_events)
        except requests.RequestException:
            stream_response = None
            stream_payload = {}
        stream_answer = str(stream_payload.get("answer") or "")
        canonical_ai_stream_status = (
            stream_response.status_code if stream_response is not None else 0
        )
        canonical_ai_answers_match = bool(
            stream_answer and stream_answer == sync_answer
        )
        canonical_ai_stream_ok = bool(
            stream_response is not None
            and stream_response.status_code == 200
            and stream_payload.get("ok")
            and binding_matches(stream_payload.get("scope_context"))
            and extract_citations(stream_answer)
            and canonical_ai_answers_match
        )
        canonical_ai_payload_sha256 = _digest(
            {"sync": sync_payload, "stream": stream_payload}
        )
        if not canonical_ai_sync_ok:
            errors.append("canonical report-bound Ask AI sync probe failed")
        if not canonical_ai_stream_ok:
            errors.append("canonical report-bound Ask AI stream probe failed")
    elif require_history:
        errors.append("canonical report-bound Ask AI probe had no eligible report")

    required_previews = {
        "leader_team",
        "leader_member",
        "leader_customer",
        "comprehensive_team",
        "compact_team",
        "renewal_portfolio",
        "renewal_customer",
        "subscription",
    }
    preview_coverage_complete = required_previews <= set(preview_results)
    if not preview_coverage_complete:
        errors.append("one or more required report/scope previews were not configured")
    live_performed = bool(not expect_fixture and connectivity_ok and not fixture_runtime)
    return _gate_result(
        ok=not errors,
        schema_version=WORKSPACE_SCHEMA,
        validation_mode="local_acceptance" if expect_fixture else "live",
        fixture_runtime_confirmed=expect_fixture and fixture_runtime,
        live_validation_attempted=not expect_fixture,
        live_validation_performed=live_performed,
        production_accuracy_claimed=False,
        connectivity_ok=connectivity_ok,
        connectivity_sha256=_digest(connectivity),
        preview_count=len(preview_results),
        preview_coverage_complete=preview_coverage_complete,
        previews=preview_results,
        history_ok=history_ok,
        history_count=len(reports),
        history_sha256=_digest(history_payload),
        scoped_history=scoped_history,
        report_view_ok=report_view_ok,
        inspected_report_count=inspected_report_count,
        canonical_report_count=len(canonical_workbook_ids),
        comparison_performed=comparison_performed,
        comparison_ok=comparison_ok,
        comparison_sha256=_digest(compare_payload),
        canonical_ai_attempted=canonical_ai_attempted,
        canonical_ai_sync_ok=canonical_ai_sync_ok,
        canonical_ai_stream_ok=canonical_ai_stream_ok,
        canonical_ai_sync_status=canonical_ai_sync_status,
        canonical_ai_stream_status=canonical_ai_stream_status,
        canonical_ai_citation_count=canonical_ai_citation_count,
        canonical_ai_answers_match=canonical_ai_answers_match,
        canonical_ai_payload_sha256=canonical_ai_payload_sha256,
        error_count=len(errors),
        error_kinds=sorted({_digest(item)[:16] for item in errors}),
    )


def run_replay_gate() -> dict[str, Any]:
    """Run the fixed offline replay and return a body-free score projection."""

    started = time.monotonic()
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    previous_logging_disable = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            from tests.ask_ai_eval.runner import run_all  # noqa: PLC0415

            results = run_all(mode="replay")
    except Exception as exc:  # noqa: BLE001 - gate must emit sanitized failure
        return _gate_result(
            ok=False,
            elapsed_seconds=round(time.monotonic() - started, 3),
            error_kind=type(exc).__name__,
            error_sha256=_digest(str(exc)),
            production_accuracy_claimed=False,
        )
    finally:
        logging.disable(previous_logging_disable)
    passed = sum(1 for item in results if item.passed)
    canonical_checks = [
        predicate
        for item in results
        for predicate in item.predicate_results
        if predicate.get("type") == "must_match_canonical_metric"
    ]
    canonical_passed = sum(
        1 for predicate in canonical_checks if predicate.get("passed")
    )
    ok = bool(
        len(results) == EXPECTED_REPLAY_QUESTIONS
        and passed == EXPECTED_REPLAY_QUESTIONS
        and len(canonical_checks) == EXPECTED_REPLAY_CANONICAL_CHECKS
        and canonical_passed == EXPECTED_REPLAY_CANONICAL_CHECKS
    )
    return _gate_result(
        ok=ok,
        validation_mode="offline_replay",
        question_count=len(results),
        passed_count=passed,
        canonical_check_count=len(canonical_checks),
        canonical_passed_count=canonical_passed,
        result_sha256=_digest(
            [
                {
                    "question_id": item.question_id,
                    "passed": item.passed,
                    "predicate_passes": [
                        bool(predicate.get("passed"))
                        for predicate in item.predicate_results
                    ],
                }
                for item in results
            ]
        ),
        elapsed_seconds=round(time.monotonic() - started, 3),
        stdout_bytes=len(stdout_capture.getvalue().encode("utf-8", "replace")),
        stdout_sha256=_bytes_digest(stdout_capture.getvalue()),
        stderr_bytes=len(stderr_capture.getvalue().encode("utf-8", "replace")),
        stderr_sha256=_bytes_digest(stderr_capture.getvalue()),
        live_validation_performed=False,
        production_accuracy_claimed=False,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _wait_for_fixture_runtime(
    base_url: str,
    process: subprocess.Popen[Any],
    *,
    timeout: float = 60.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("guarded local runtime exited before readiness")
        try:
            response = requests.get(
                base_url + "/api/diag/connectivity", timeout=2
            )
            payload = _response_json(response)
            if (
                response.status_code == 200
                and payload.get("ok")
                and payload.get("mode") == SOURCE_MODE
                and payload.get("live_validation_performed") is False
            ):
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise TimeoutError("guarded local runtime did not become ready")


@contextmanager
def _fixture_runtime(
    scratch: Path,
    *,
    scenario: str = "healthy",
) -> Iterator[tuple[str, Path]]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = scratch / "fixture-runtime.log"
    state_dir = Path(
        tempfile.mkdtemp(prefix="adoptiq-round146-runtime-state-")
    ).resolve()
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_local_acceptance_app.py"),
        "--enable-local-fixtures",
        "--scenario",
        scenario,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    env = dict(os.environ)
    env.update(
        {
            "ADOPTIQ_BIND_PUBLIC": "0",
            "ADOPTIQ_ASK_AI_ALLOW_LEGACY_FALLBACK": "0",
            "ADOPTIQ_OUTPUTS_DIR": str(scratch / "fixture-report-outputs"),
            "ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR": str(state_dir),
            "PYTHONUNBUFFERED": "1",
        }
    )
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=REPO_ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_fixture_runtime(base_url, process)
            yield base_url, log_path
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            shutil.rmtree(state_dir, ignore_errors=True)


def _acceptance_summary(
    *,
    profile: str,
    started_at: str,
    gates: Mapping[str, Mapping[str, Any]],
    sensitive_artifacts_retained: bool,
    sensitive_dir: Path | None,
) -> dict[str, Any]:
    required = (
        {
            "fixture_manifest",
            "degraded_http",
            "decision_reports",
            "report_matrix",
            "ai_features",
            "manager_workspace",
            "ask_ai_replay",
        }
        if profile == "local"
        else {
            "decision_reports",
            "report_matrix",
            "ai_features",
            "manager_workspace",
            "ask_ai_replay",
        }
    )
    skipped = sorted(
        name for name, result in gates.items() if result.get("status") == "skipped"
    )
    all_required_present = required <= set(gates)
    acceptance_complete = bool(
        all_required_present
        and not skipped
        and all(bool(gates[name].get("ok")) for name in required)
    )
    fixture_mode = profile == "local"
    live_performed = bool(
        profile == "work-machine"
        and acceptance_complete
        and gates.get("decision_reports", {}).get("live_validation_performed")
        and gates.get("ai_features", {}).get("live_validation_performed")
        and gates.get("manager_workspace", {}).get("live_validation_performed")
    )
    return {
        "schema_version": SUMMARY_SCHEMA,
        "sanitized": True,
        "do_not_commit": True,
        "profile": profile,
        "started_at_utc": started_at,
        "completed_at_utc": _utc_now(),
        "git": _git_metadata(),
        "fixture_validation_performed": fixture_mode,
        "fixture_validation_passed": fixture_mode and acceptance_complete,
        "live_validation_attempted": profile == "work-machine",
        "live_validation_performed": False if fixture_mode else live_performed,
        "live_validation_passed": (
            profile == "work-machine" and acceptance_complete
        ),
        "production_accuracy_claimed": False,
        "manual_source_reconciliation_complete": False,
        "visual_review_complete": False,
        "release_ready": False,
        "acceptance_complete": acceptance_complete,
        "all_passed": acceptance_complete,
        "required_gates": sorted(required),
        "skipped_gates": skipped,
        "sensitive_artifacts_retained": sensitive_artifacts_retained,
        "sensitive_directory_sha256": (
            _digest(str(sensitive_dir)) if sensitive_dir is not None else ""
        ),
        "gates": dict(gates),
        "limitations": [
            (
                "Controlled synthetic fixtures only; no live source validation was performed."
                if fixture_mode
                else "Automated live checks do not replace claim-by-claim source, visual, packaged-build, deployment, or rollback review."
            ),
            "The summary contains hashes, counts, states, and pass/fail evidence only.",
            "Release readiness and production accuracy are never granted by this command.",
        ],
    }


def _local_profile(args: argparse.Namespace, scratch: Path) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    manifest = load_manifest(Path(args.manifest))
    lab_dir = scratch / "fixture-manifest"
    lab_summary = lab_dir / "summary.json"
    gates["fixture_manifest"] = _run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_local_acceptance_lab.py"),
            "--enable-local-fixtures",
            "--scenario",
            "all",
            "--manifest",
            str(args.manifest),
            "--summary-path",
            str(lab_summary),
        ],
        summary_path=lab_summary,
        projector=_project_lab,
    )

    if args.skip_degraded_http:
        gates["degraded_http"] = _skipped_gate("operator requested skip")
    else:
        http_dir = scratch / "degraded-http"
        http_summary = http_dir / "local_acceptance_http_summary.json"
        command = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_local_acceptance_http.py"),
            "--manifest",
            str(args.manifest),
            "--output-dir",
            str(http_dir),
            "--scenarios",
            str(args.scenarios),
        ]
        if args.skip_degraded_reports:
            command.append("--skip-reports")
        gates["degraded_http"] = _run_command(
            command,
            summary_path=http_summary,
            projector=_project_local_http,
        )

    decision_dir = scratch / "decision-reports"
    decision_summary = decision_dir / "decision_report_acceptance_summary.json"
    gates["decision_reports"] = _run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_decision_report_acceptance.py"),
            "--mode",
            "offline",
            "--manager",
            "Local Fixture Manager",
            "--days",
            str(args.days),
            "--as-of",
            str(manifest["deterministic_clock_utc"]),
            "--output-dir",
            str(decision_dir),
        ],
        summary_path=decision_summary,
        projector=_project_decision_reports,
    )

    if args.skip_matrix and args.skip_ai:
        gates["report_matrix"] = _skipped_gate("operator requested skip")
        gates["ai_features"] = _skipped_gate("operator requested skip")
        gates["manager_workspace"] = _skipped_gate(
            "workspace needs the persistent guarded runtime"
        )
    else:
        try:
            with _fixture_runtime(scratch) as (base_url, log_path):
                if args.skip_matrix:
                    gates["report_matrix"] = _skipped_gate("operator requested skip")
                else:
                    matrix_dir = scratch / "report-matrix"
                    matrix_dir.mkdir(parents=True, exist_ok=True)
                    matrix_gate = _run_command(
                        [
                            sys.executable,
                            str(REPO_ROOT / "scripts" / "run_report_option_matrix.py"),
                            "--base-url",
                            base_url,
                            "--downloads-dir",
                            str(matrix_dir),
                            "--days",
                            str(args.days),
                            "--blocks",
                            REQUIRED_MATRIX_BLOCKS,
                            "--strict",
                            "--baseline-mode",
                            "off",
                            "--stop-on-failure",
                            "--local-acceptance",
                            "--customer-name",
                            "Acme Corporation",
                            "--subscription-id",
                            "SUB-001",
                        ]
                    )
                    matrix_summary = _find_matrix_summary(matrix_dir)
                    projection = _project_matrix(
                        _read_json(matrix_summary) if matrix_summary else {}
                    )
                    projection_ok = projection.pop("projected_ok", False)
                    matrix_gate.update(projection)
                    matrix_gate["summary_present"] = matrix_summary is not None
                    matrix_gate["ok"] = bool(matrix_gate.get("ok") and projection_ok)
                    matrix_gate["status"] = "passed" if matrix_gate["ok"] else "failed"
                    gates["report_matrix"] = matrix_gate

                if args.skip_ai:
                    gates["ai_features"] = _skipped_gate("operator requested skip")
                else:
                    ai_dir = scratch / "ai-features"
                    ai_summary = ai_dir / "ai_feature_acceptance_summary.json"
                    gates["ai_features"] = _run_command(
                        [
                            sys.executable,
                            str(REPO_ROOT / "scripts" / "run_ai_feature_acceptance.py"),
                            "--base-url",
                            base_url,
                            "--manager",
                            "Local Fixture Manager",
                            "--technology",
                            "All",
                            "--customer-name",
                            "Acme Corporation",
                            "--days",
                            str(args.days),
                            "--output-dir",
                            str(ai_dir),
                            "--pace-seconds",
                            "0",
                            "--local-acceptance",
                        ],
                        summary_path=ai_summary,
                        projector=_project_ai,
                    )

                gates["manager_workspace"] = probe_manager_workspace(
                    base_url=base_url,
                    expect_fixture=True,
                    manager="Local Fixture Manager",
                    technology="All",
                    days=args.days,
                    member_email="fixture.owner1@example.invalid",
                    customer_name="Acme Corporation",
                    subscription_id="SUB-001",
                    timeout=args.request_timeout,
                    require_history=not args.skip_matrix,
                )
            gates["fixture_runtime_log"] = _gate_result(
                ok=True,
                log_bytes=log_path.stat().st_size,
                log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest(),
                retained=False,
                live_validation_performed=False,
                production_accuracy_claimed=False,
            )
        except Exception as exc:  # noqa: BLE001 - sanitized orchestration failure
            failure = _gate_result(
                ok=False,
                error_kind=type(exc).__name__,
                error_sha256=_digest(str(exc)),
                production_accuracy_claimed=False,
            )
            gates.setdefault("report_matrix", failure)
            gates.setdefault("ai_features", failure)
            gates.setdefault("manager_workspace", failure)

    gates["ask_ai_replay"] = (
        _skipped_gate("operator requested skip")
        if args.skip_replay
        else run_replay_gate()
    )
    return gates


def _work_machine_profile(args: argparse.Namespace, scratch: Path) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    base_url = _loopback_base_url(args.base_url)
    decision_dir = scratch / "decision-reports"
    decision_summary = decision_dir / "decision_report_acceptance_summary.json"
    decision_command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_decision_report_acceptance.py"),
        "--mode",
        "live",
        "--base-url",
        base_url,
        "--manager",
        args.manager,
        "--member-email",
        args.member_email,
        "--customer-name",
        args.customer_name,
        "--days",
        str(args.days),
        "--as-of",
        args.as_of,
        "--output-dir",
        str(decision_dir),
    ]
    if args.customer_member_email:
        decision_command.extend(
            ["--customer-member-email", args.customer_member_email]
        )
    if args.ambiguous_customer_name:
        decision_command.extend(
            ["--ambiguous-customer-name", args.ambiguous_customer_name]
        )
    if args.csone_file:
        decision_command.extend(["--csone-file", str(args.csone_file)])
    gates["decision_reports"] = _run_command(
        decision_command,
        summary_path=decision_summary,
        projector=_project_decision_reports,
    )

    if args.skip_matrix:
        gates["report_matrix"] = _skipped_gate("operator requested skip")
    else:
        matrix_dir = scratch / "report-matrix"
        matrix_dir.mkdir(parents=True, exist_ok=True)
        matrix_command = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_report_option_matrix.py"),
            "--base-url",
            base_url,
            "--downloads-dir",
            str(matrix_dir),
            "--days",
            str(args.days),
            "--blocks",
            REQUIRED_MATRIX_BLOCKS,
            "--strict",
            "--baseline-mode",
            "off",
            "--stop-on-failure",
            "--customer-name",
            args.customer_name,
            "--subscription-id",
            args.subscription_id,
        ]
        if args.csone_file:
            matrix_command.extend(["--csone-upload-path", str(args.csone_file)])
        matrix_gate = _run_command(matrix_command)
        matrix_summary = _find_matrix_summary(matrix_dir)
        projection = _project_matrix(
            _read_json(matrix_summary) if matrix_summary else {}
        )
        projection_ok = projection.pop("projected_ok", False)
        matrix_gate.update(projection)
        matrix_gate["summary_present"] = matrix_summary is not None
        matrix_gate["ok"] = bool(matrix_gate.get("ok") and projection_ok)
        matrix_gate["status"] = "passed" if matrix_gate["ok"] else "failed"
        gates["report_matrix"] = matrix_gate

    if args.skip_ai:
        gates["ai_features"] = _skipped_gate("operator requested skip")
    else:
        ai_dir = scratch / "ai-features"
        ai_summary = ai_dir / "ai_feature_acceptance_summary.json"
        gates["ai_features"] = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_ai_feature_acceptance.py"),
                "--base-url",
                base_url,
                "--manager",
                args.manager,
                "--technology",
                args.technology,
                "--customer-name",
                args.customer_name,
                "--days",
                str(args.days),
                "--output-dir",
                str(ai_dir),
                "--pace-seconds",
                str(args.pace_seconds),
            ],
            summary_path=ai_summary,
            projector=_project_ai,
        )

    gates["manager_workspace"] = probe_manager_workspace(
        base_url=base_url,
        expect_fixture=False,
        manager=args.manager,
        technology=args.technology,
        days=args.days,
        member_email=args.member_email,
        customer_name=args.customer_name,
        subscription_id=args.subscription_id,
        timeout=args.request_timeout,
        require_history=True,
    )
    gates["ask_ai_replay"] = (
        _skipped_gate("operator requested skip")
        if args.skip_replay
        else run_replay_gate()
    )
    return gates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Round 146 acceptance without Codex and write a sanitized summary."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".adoptiq-acceptance" / "round146",
    )
    parser.add_argument(
        "--retain-sensitive-dir",
        type=Path,
        help=(
            "Explicit external directory for generated reports/evidence; omit to "
            "remove all sensitive child artifacts after summary projection."
        ),
    )
    subparsers = parser.add_subparsers(dest="profile", required=True)

    local = subparsers.add_parser(
        "local", help="Run guarded synthetic acceptance; never live validation."
    )
    local.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    local.add_argument("--scenarios", default="all")
    local.add_argument("--days", type=int, default=90)
    local.add_argument("--request-timeout", type=float, default=120.0)
    local.add_argument("--skip-degraded-http", action="store_true")
    local.add_argument("--skip-degraded-reports", action="store_true")
    local.add_argument("--skip-matrix", action="store_true")
    local.add_argument("--skip-ai", action="store_true")
    local.add_argument("--skip-replay", action="store_true")

    work = subparsers.add_parser(
        "work-machine",
        help="Run live acceptance against an already-running loopback candidate.",
    )
    work.add_argument("--base-url", default="http://127.0.0.1:5153")
    work.add_argument("--manager", required=True)
    work.add_argument("--member-email", required=True)
    work.add_argument("--customer-name", required=True)
    work.add_argument("--customer-member-email", default="")
    work.add_argument("--ambiguous-customer-name", default="")
    work.add_argument("--subscription-id", required=True)
    work.add_argument("--technology", default="All")
    work.add_argument("--days", type=int, default=90)
    work.add_argument("--as-of", required=True)
    work.add_argument("--csone-file", type=Path)
    work.add_argument("--pace-seconds", type=float, default=7.0)
    work.add_argument("--request-timeout", type=float, default=300.0)
    work.add_argument("--skip-matrix", action="store_true")
    work.add_argument("--skip-ai", action="store_true")
    work.add_argument("--skip-replay", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= int(args.days) <= 365:
        parser.error("--days must be between 1 and 365")
    try:
        output_dir = _safe_output_dir(args.output_dir)
        retained = (
            _safe_sensitive_dir(args.retain_sensitive_dir)
            if args.retain_sensitive_dir
            else None
        )
        if args.profile == "work-machine":
            _loopback_base_url(args.base_url)
            if args.csone_file:
                args.csone_file = args.csone_file.expanduser().resolve()
                if (
                    not args.csone_file.is_file()
                    or args.csone_file.suffix.casefold() != ".xlsx"
                ):
                    raise ValueError("--csone-file must be a readable .xlsx file")
    except ValueError as exc:
        parser.error(str(exc))

    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    if retained is not None:
        retained = retained / (
            "round146-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + f"-{os.getpid()}"
        )
        retained.mkdir(parents=True, exist_ok=False)
        gates = (
            _local_profile(args, retained)
            if args.profile == "local"
            else _work_machine_profile(args, retained)
        )
    else:
        with tempfile.TemporaryDirectory(prefix="adoptiq-round146-") as temporary:
            scratch = Path(temporary)
            gates = (
                _local_profile(args, scratch)
                if args.profile == "local"
                else _work_machine_profile(args, scratch)
            )

    summary = _acceptance_summary(
        profile=args.profile,
        started_at=started_at,
        gates=gates,
        sensitive_artifacts_retained=retained is not None,
        sensitive_dir=retained,
    )
    summary_path = output_dir / "round146_acceptance_summary.json"
    _write_json(summary_path, summary)
    print(
        json.dumps(
            {
                "all_passed": summary["all_passed"],
                "profile": args.profile,
                "live_validation_performed": summary["live_validation_performed"],
                "release_ready": False,
                "summary_path": str(summary_path),
            },
            sort_keys=True,
        )
    )
    return 0 if summary["all_passed"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
