#!/usr/bin/env python3
"""Portable Round 146 acceptance orchestrator.

The command has two deliberately separate profiles:

* ``local`` runs the guarded synthetic lab and labels every result as fixture
  validation.  It cannot produce a live-validation claim.
* ``work-machine`` verifies, mounts, and launches one exact Mac candidate DMG,
  then runs the live report, AI, report-matrix, workspace, and replay gates.

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
from scripts.release_candidate_contract import (  # noqa: E402
    ReleaseCandidateManifest,
    verify_release_candidate,
)
from scripts.smoke_frozen_candidate import (  # noqa: E402
    _candidate_executable,
    _terminate_process_tree,
)
SUMMARY_SCHEMA = "round146-portable-acceptance/v1"
WORKSPACE_SCHEMA = "manager-decision-workspace/v1"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
REQUIRED_MATRIX_BLOCKS = "A,B,C,D,E,F,G"
REQUIRED_MATRIX_BLOCK_SET = frozenset(REQUIRED_MATRIX_BLOCKS.split(","))
REQUIRED_SOURCE_PARITY_FAMILIES = (
    "compact",
    "comprehensive",
    "leader",
    "renewal",
)
REQUIRED_SOURCE_PARITY_FIELDS = (
    "count",
    "identity_sha256",
    "attribution_sha256",
    "attributed_record_count",
    "source_state",
)
REQUIRED_DECISION_SCOPES = frozenset(
    {"team", "member", "customer", "comprehensive"}
)
EXPECTED_REPLAY_QUESTIONS = 75
EXPECTED_REPLAY_CANONICAL_CHECKS = 25
REQUIRED_WORK_MACHINE_GATES = frozenset(
    {
        "candidate_identity",
        "runtime_identity",
        "decision_reports",
        "report_matrix",
        "ai_features",
        "manager_workspace",
        "ask_ai_replay",
    }
)
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


def _candidate_identity_gate(
    manifest: ReleaseCandidateManifest,
    *,
    launch_controlled: bool,
) -> dict[str, Any]:
    """Project only immutable, non-sensitive candidate identity evidence."""

    return _gate_result(
        ok=launch_controlled,
        schema_version=manifest.schema_version,
        release_status=manifest.release_status,
        platform=manifest.platform,
        version=manifest.version,
        build=str(manifest.build),
        source_commit_sha=manifest.source_commit_sha,
        artifact_name=manifest.artifact.name,
        artifact_sha256=manifest.artifact.sha256,
        artifact_size_bytes=manifest.artifact.size_bytes,
        built_at_utc=manifest.built_at_utc,
        launch_controlled=launch_controlled,
        candidate_environment_sanitized=launch_controlled,
        external_baked_corpus_override_allowed=False,
        live_validation_performed=launch_controlled,
        production_accuracy_claimed=False,
    )


def _gate_result(
    *,
    ok: bool,
    status: str = "passed",
    **evidence: Any,
) -> dict[str, Any]:
    return {"ok": bool(ok), "status": status if ok else "failed", **evidence}


def _runtime_identity_gate(
    payload: Mapping[str, Any],
    *,
    status_code: int,
    expected_version: str,
    expected_build: str,
) -> dict[str, Any]:
    """Bind work-machine acceptance to the installed frozen candidate."""
    version = str(payload.get("version") or "")
    build = str(payload.get("build") or "")
    frozen = payload.get("frozen") is True
    restart_required = payload.get("restart_required")
    ok = bool(
        status_code == 200
        and payload.get("ok") is True
        and version == expected_version
        and build == expected_build
        and frozen
        and restart_required is False
    )
    return _gate_result(
        ok=ok,
        version=version,
        build=build,
        frozen=frozen,
        restart_required=restart_required,
        live_validation_performed=True,
        status_code=int(status_code),
    )


def probe_runtime_identity(
    *,
    base_url: str,
    timeout: float,
    expected_version: str,
    expected_build: str,
) -> dict[str, Any]:
    base_url = _loopback_base_url(base_url)
    try:
        response = requests.get(base_url + "/api/version", timeout=timeout)
        payload = _response_json(response)
        status_code = int(response.status_code)
    except requests.RequestException:
        payload = {}
        status_code = 0
    return _runtime_identity_gate(
        payload,
        status_code=status_code,
        expected_version=str(expected_version),
        expected_build=str(expected_build),
    )


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
    expected_keys = [
        str(item)
        for item in payload.get("scenario_keys_expected") or []
        if str(item)
    ]
    completed_keys = [
        str(item)
        for item in payload.get("scenario_keys_completed") or []
        if str(item)
    ]
    expected = int(payload.get("scenario_count_expected") or 0)
    completed = int(payload.get("scenario_count_completed") or 0)
    requested_blocks = {
        item.split("_", 1)[0].upper()
        for item in expected_keys
        if "_" in item
    }
    all_blocks_requested = REQUIRED_MATRIX_BLOCK_SET <= requested_blocks
    passed = sum(
        1
        for item in payload.get("results") or []
        if isinstance(item, Mapping) and item.get("all_passed")
    )
    consistency = payload.get("cross_report_source_consistency")
    consistency_family_sets = (
        consistency.get("report_family_sets_compared") or []
        if isinstance(consistency, Mapping)
        else []
    )
    consistency_comparisons = (
        int(consistency.get("comparisons") or 0)
        if isinstance(consistency, Mapping)
        else 0
    )
    consistency_comparisons_expected = (
        int(consistency.get("comparisons_expected") or 0)
        if isinstance(consistency, Mapping)
        else 0
    )
    exact_family_set = list(REQUIRED_SOURCE_PARITY_FAMILIES)
    exact_projected_fields = list(REQUIRED_SOURCE_PARITY_FIELDS)
    consistency_ok = bool(
        isinstance(consistency, Mapping)
        and consistency.get("ok") is True
        and consistency.get("comparison_requirement_met") is True
        and consistency.get("required_report_families") == exact_family_set
        and consistency.get("projected_fields") == exact_projected_fields
        and int(consistency.get("required_family_set_group_count") or 0) > 0
        and consistency_comparisons > 0
        and consistency_comparisons == consistency_comparisons_expected
        and consistency_family_sets
        and all(item == exact_family_set for item in consistency_family_sets)
        and not consistency.get("mismatches")
        and not consistency.get("freshness_mismatches")
        and not consistency.get("read_errors")
    )
    r114_expected = int(payload.get("r114_audit_scenario_count_expected") or 0)
    r114_completed = int(payload.get("r114_audit_scenario_count_completed") or 0)
    r114_ok = bool(
        payload.get("r114_audit_inventory_exact") is True
        and r114_expected == expected
        and r114_completed == expected
        and not payload.get("r114_critical_scenarios")
        and payload.get("r114_audit_skipped") is not True
    )
    inventory_ok = bool(
        payload.get("scenario_inventory_exact") is True
        and expected > 0
        and expected == len(expected_keys)
        and completed == expected
        and completed_keys == expected_keys
        and not payload.get("scenario_keys_missing")
        and not payload.get("scenario_keys_unexpected")
        and not payload.get("scenario_keys_completed_duplicate")
    )
    return {
        "projected_ok": bool(
            payload.get("all_passed")
            and inventory_ok
            and all_blocks_requested
            and passed == expected
            and consistency_ok
            and r114_ok
        ),
        "scenario_count": expected,
        "expected_count": expected,
        "completed_count": completed,
        "passed_count": passed,
        "failed_count": max(completed - passed, 0),
        "scenario_inventory_complete": inventory_ok,
        "all_report_blocks_requested": all_blocks_requested,
        "source_consistency_ok": consistency_ok,
        "source_consistency_comparison_count": (
            consistency_comparisons
        ),
        "source_consistency_expected_comparison_count": (
            consistency_comparisons_expected
        ),
        "source_consistency_required_report_families": exact_family_set,
        "source_consistency_projected_fields": exact_projected_fields,
        "source_consistency_required_family_set_group_count": (
            int(consistency.get("required_family_set_group_count") or 0)
            if isinstance(consistency, Mapping)
            else 0
        ),
        "source_consistency_report_family_sets_compared": consistency_family_sets,
        "source_consistency_mismatch_count": (
            len(consistency.get("mismatches") or [])
            if isinstance(consistency, Mapping)
            else 0
        ),
        "source_freshness_mismatch_count": (
            len(consistency.get("freshness_mismatches") or [])
            if isinstance(consistency, Mapping)
            else 0
        ),
        "source_consistency_read_error_count": (
            len(consistency.get("read_errors") or [])
            if isinstance(consistency, Mapping)
            else 0
        ),
        "r114_audit_ok": r114_ok,
        "r114_audit_completed_count": r114_completed,
        "production_accuracy_claimed": False,
    }


def _project_multi_manager_matrix(payload: Mapping[str, Any]) -> dict[str, Any]:
    from report_iteration_loop import build_local_acceptance_multi_manager_matrix

    expected = set(build_local_acceptance_multi_manager_matrix())
    requested = {
        str(item)
        for item in payload.get("scenario_keys_requested") or []
        if str(item)
    }
    completed = int(payload.get("scenarios_completed") or 0)
    passed = sum(
        1
        for item in payload.get("results") or []
        if isinstance(item, Mapping) and item.get("all_passed")
    )
    return {
        "projected_ok": bool(
            payload.get("all_passed")
            and requested == expected
            and completed == len(expected)
            and passed == len(expected)
        ),
        "scenario_count": len(requested),
        "completed_count": completed,
        "passed_count": passed,
        "expected_count": len(expected),
        "both_named_managers_exercised": all(
            any(token in key for key in requested)
            for token in ("primary_manager", "secondary_manager")
        ),
        "aggregate_manager_exercised": any(
            "all_managers" in key for key in requested
        ),
        "production_accuracy_claimed": False,
    }


def _project_source_contracts(payload: Mapping[str, Any]) -> dict[str, Any]:
    checks = payload.get("checks") if isinstance(payload.get("checks"), Mapping) else {}
    passed = bool(
        payload.get("all_passed")
        and payload.get("source_mode") == SOURCE_MODE
        and payload.get("live_validation_performed") is False
        and checks
        and all(bool(value) for value in checks.values())
    )
    return {
        "projected_ok": passed,
        "scenario": str(payload.get("scenario") or ""),
        "check_count": len(checks),
        "query_count": int((payload.get("query_trace") or {}).get("query_count") or 0),
        "parameter_binding_ok": bool(
            checks.get("parameter_binding_and_family_coverage")
        ),
        "secondary_attribution_ok": bool(checks.get("secondary_attribution")),
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_snowflake_capabilities(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = int(payload.get("allowed_table_count") or 0)
    accessible = int(payload.get("accessible_table_count") or 0)
    blocked = payload.get("policy_blocked_tables") or []
    passed = bool(
        payload.get("all_passed")
        and payload.get("mode") == "local"
        and payload.get("row_values_queried") is False
        and payload.get("live_validation_performed") is False
        and allowed > 0
        and accessible == allowed
        and all(
            isinstance(item, Mapping)
            and item.get("probe_attempted") is False
            and item.get("state") == "blocked_by_policy"
            for item in blocked
        )
    )
    return {
        "projected_ok": passed,
        "allowed_table_count": allowed,
        "accessible_table_count": accessible,
        "row_values_queried": payload.get("row_values_queried") is True,
        "blocked_table_count": len(blocked),
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_csone_corpus(payload: Mapping[str, Any]) -> dict[str, Any]:
    workbook_count = int(payload.get("workbook_count") or 0)
    row_count = int(payload.get("total_profiled_rows") or 0)
    passed = bool(
        payload.get("sanitized") is True
        and payload.get("source_rows_exported") is False
        and payload.get("source_values_exported") is False
        and payload.get("live_snowflake_validation_performed") is False
        and workbook_count > 0
        and row_count > 0
        and int(payload.get("distinct_schema_count") or 0) > 0
    )
    return {
        "projected_ok": passed,
        "workbook_count": workbook_count,
        "profiled_row_count": row_count,
        "distinct_schema_count": int(payload.get("distinct_schema_count") or 0),
        "dominant_schema_workbook_count": int(
            payload.get("dominant_schema_workbook_count") or 0
        ),
        "source_rows_exported": payload.get("source_rows_exported") is True,
        "source_values_exported": payload.get("source_values_exported") is True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_csone_replay(payload: Mapping[str, Any]) -> dict[str, Any]:
    loader = payload.get("loader_contract") or {}
    replay = payload.get("replay") or {}
    passed = bool(
        payload.get("all_passed")
        and payload.get("sanitized") is True
        and payload.get("source_rows_exported") is False
        and payload.get("source_values_exported") is False
        and payload.get("raw_values_retained") is False
        and loader.get("all_nonempty")
        and loader.get("no_footer_rows_remaining")
        and loader.get("consistent_schema")
        and replay.get("pseudonym_contract_ok")
        and int(replay.get("row_count") or 0) > 0
    )
    return {
        "projected_ok": passed,
        "representative_workbook_count": int(
            loader.get("representative_workbook_count") or 0
        ),
        "replay_row_count": int(replay.get("row_count") or 0),
        "source_row_count": int(replay.get("source_row_count") or 0),
        "excluded_non_record_rows": int(
            replay.get("excluded_non_record_rows") or 0
        ),
        "pseudonym_contract_ok": bool(replay.get("pseudonym_contract_ok")),
        "live_validation_performed": False,
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


def _require_free_loopback_port(port: int) -> None:
    """Fail before launch when another local service owns the candidate port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            handle.bind(("127.0.0.1", int(port)))
        except OSError as exc:
            raise ValueError("candidate port is already in use") from exc


def _live_candidate_environment(*, port: int, admin_port: int) -> dict[str, str]:
    """Build a production-only child environment for live acceptance.

    The runner is commonly invoked from pytest, a fixture shell, or a prior local
    acceptance session.  None of those test switches may leak into the frozen
    candidate and suppress real corpus/source behavior while the summary is labeled
    live.  Authorized connection variables are otherwise preserved unchanged.
    """

    environment = os.environ.copy()
    for test_variable in (
        "ADOPTIQ_ALLOW_TEST_CORPUS_REFRESH",
        "ADOPTIQ_ALLOW_TEST_RUNTIME_VECTORS",
        "ADOPTIQ_BAKED_CORPUS_DIR",
        "ADOPTIQ_LOCAL_ACCEPTANCE_ACTIVE",
        "ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR",
        "ADOPTIQ_TESTING",
        "ADOPTIQ_TEST_MODE",
        "PYTEST_CURRENT_TEST",
        "TESTING",
    ):
        environment.pop(test_variable, None)
    environment.update(
        {
            "ADOPTIQ_PORT": str(int(port)),
            "ADOPTIQ_ADMIN_PORT": str(int(admin_port)),
            "ADOPTIQ_BIND_HOST": "127.0.0.1",
            "ADOPTIQ_MAIN_URL": f"http://127.0.0.1:{int(port)}",
            "ADOPTIQ_AUTO_UPDATE_MODE": "off",
            "ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "1",
            "ADOPTIQ_PRODUCTION_READY": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


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


def _wait_for_live_candidate_runtime(
    base_url: str,
    process: subprocess.Popen[Any],
    *,
    version: str,
    build: str,
    timeout: float,
) -> None:
    """Wait until the exact mounted candidate reports its frozen identity."""

    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("live candidate exited before readiness")
        try:
            response = requests.get(base_url + "/api/version", timeout=2)
            payload = _response_json(response)
            if (
                response.status_code == 200
                and payload.get("ok") is True
                and str(payload.get("version") or "") == version
                and str(payload.get("build") or "") == build
                and payload.get("frozen") is True
                and payload.get("restart_required") is False
            ):
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise TimeoutError("exact live candidate did not become ready")


@contextmanager
def _live_candidate_runtime(
    *,
    candidate_dmg: Path,
    candidate_manifest: Path,
    port: int,
    startup_timeout: float,
) -> Iterator[tuple[str, ReleaseCandidateManifest]]:
    """Verify, mount, and launch the exact release DMG under acceptance control.

    The operator checkout may contain newer acceptance tooling than the immutable
    candidate.  Binding the process to the verified DMG bytes avoids confusing the
    runner's Git SHA with the packaged app's source identity.
    """

    manifest = verify_release_candidate(
        candidate_manifest,
        candidate_dmg,
        sidecar_root=candidate_manifest.expanduser().resolve().parent,
    )
    if manifest.platform != "macos":
        raise ValueError("work-machine candidate must target macOS")
    if not 1 <= int(port) <= 65535:
        raise ValueError("candidate port must be between 1 and 65535")
    _require_free_loopback_port(int(port))
    admin_port = _free_port()
    base_url = f"http://127.0.0.1:{int(port)}"
    environment = _live_candidate_environment(port=int(port), admin_port=admin_port)
    with _candidate_executable(candidate_dmg) as executable:
        if not executable.is_file():
            raise RuntimeError("verified DMG has no launchable AdoptIQ binary")
        popen_kwargs: dict[str, Any] = {
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        process = subprocess.Popen([str(executable)], **popen_kwargs)  # noqa: S603
        try:
            _wait_for_live_candidate_runtime(
                base_url,
                process,
                version=manifest.version,
                build=str(manifest.build),
                timeout=startup_timeout,
            )
            yield base_url, manifest
        finally:
            _terminate_process_tree(process)


@contextmanager
def _fixture_runtime(
    scratch: Path,
    *,
    scenario: str = "healthy",
    csone_corpus_dir: Path | None = None,
    csone_replay_max_rows: int = 600,
) -> Iterator[tuple[str, Path]]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = scratch / f"fixture-runtime-{scenario}.log"
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
    if csone_corpus_dir is not None:
        command.extend(
            [
                "--csone-corpus-dir",
                str(csone_corpus_dir),
                "--csone-replay-max-rows",
                str(int(csone_replay_max_rows)),
            ]
        )
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
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    required = (
        {
            "fixture_manifest",
            "source_contracts",
            "snowflake_capabilities",
            "degraded_http",
            "decision_reports",
            "report_matrix",
            "multi_manager_reports",
            "multi_manager_isolation",
            "ai_features",
            "manager_workspace",
            "ask_ai_replay",
        }
        if profile == "local"
        else set(REQUIRED_WORK_MACHINE_GATES)
    )
    if profile == "local" and "real_csone_corpus" in gates:
        required.add("real_csone_corpus")
        required.add("real_csone_replay")
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
        and gates.get("candidate_identity", {}).get("live_validation_performed")
        and gates.get("runtime_identity", {}).get("live_validation_performed")
        and gates.get("decision_reports", {}).get("live_validation_performed")
        and gates.get("report_matrix", {}).get("live_validation_performed")
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
        "candidate": dict(candidate or {}),
        "fixture_validation_performed": fixture_mode,
        "fixture_validation_passed": fixture_mode and acceptance_complete,
        "live_validation_attempted": profile == "work-machine",
        "live_validation_performed": False if fixture_mode else live_performed,
        "live_validation_passed": (
            profile == "work-machine" and acceptance_complete and live_performed
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


def probe_multi_manager_isolation(
    *,
    base_url: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Prove cross-team Leader selections fail closed in the real routes."""

    base_url = _loopback_base_url(base_url)
    session = requests.Session()
    errors: list[str] = []
    try:
        home = session.get(base_url + "/", timeout=timeout)
        token = _extract_csrf_token(home.text) if home.status_code == 200 else ""
    except requests.RequestException:
        token = ""
    if not token:
        return _gate_result(
            ok=False,
            error_kind="csrf_bootstrap_failed",
            live_validation_performed=False,
            production_accuracy_claimed=False,
        )
    headers = {
        "X-CSRFToken": token,
        "X-Requested-With": "XMLHttpRequest",
    }

    immediate_cases = (
        {
            "manager": "Local Fixture Manager",
            "scope_value": "fixture.owner2@example.invalid",
        },
        {
            "manager": "Second Fixture Manager",
            "scope_value": "fixture.owner1@example.invalid",
        },
    )
    immediate_results: list[dict[str, Any]] = []
    for case in immediate_cases:
        try:
            response = session.post(
                base_url + "/start_leader_report",
                data={
                    "manager": case["manager"],
                    "days": "90",
                    "scope_type": "member",
                    "scope_value": case["scope_value"],
                },
                headers=headers,
                timeout=timeout,
            )
            payload = _response_json(response)
        except requests.RequestException:
            response = None
            payload = {}
        rejected = bool(
            response is not None
            and response.status_code == 400
            and payload.get("success") is False
            and not payload.get("analysis_id")
        )
        immediate_results.append(
            {
                "rejected_before_worker": rejected,
                "status_code": response.status_code if response is not None else 0,
            }
        )
        if not rejected:
            errors.append("cross-manager member request was not rejected before worker start")

    # Customer ownership can only be verified against the manager-authorized
    # subscription frame. Exercise both directions and require the background
    # worker to terminate in an error state before report publication.
    ownership_cases = (
        {
            "manager": "Local Fixture Manager",
            "member": "fixture.owner1@example.invalid",
            "customer": "Gamma Public Sector",
        },
        {
            "manager": "Second Fixture Manager",
            "member": "fixture.owner2@example.invalid",
            "customer": "Beta Industries",
        },
    )
    ownership_results: list[dict[str, Any]] = []
    for case in ownership_cases:
        try:
            response = session.post(
                base_url + "/start_leader_report",
                data={
                    "manager": case["manager"],
                    "days": "90",
                    "scope_type": "customer",
                    "scope_value": case["customer"],
                    "scope_member": case["member"],
                },
                headers=headers,
                timeout=timeout,
            )
            payload = _response_json(response)
        except requests.RequestException:
            response = None
            payload = {}
        analysis_id = str(payload.get("analysis_id") or "")
        terminal: dict[str, Any] = {}
        if response is not None and response.status_code == 200 and analysis_id:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    status_response = session.get(
                        base_url + "/status/" + quote(analysis_id, safe=""),
                        timeout=min(timeout, 10.0),
                    )
                    terminal = _response_json(status_response)
                except requests.RequestException:
                    terminal = {}
                    break
                if terminal.get("status") in {
                    "completed",
                    "error",
                    "failed",
                    "cancelled",
                }:
                    break
                time.sleep(0.1)
        output_fields = (
            "file_path",
            "filepath",
            "excel_path",
            "docx_path",
            "xlsx_path",
            "output_path",
        )
        blocked = bool(
            analysis_id
            and terminal.get("status") in {"error", "failed"}
            and not any(terminal.get(field) for field in output_fields)
        )
        ownership_results.append(
            {
                "blocked_before_publication": blocked,
                "start_status_code": response.status_code if response is not None else 0,
                "terminal_state": str(terminal.get("status") or "unavailable"),
                "published_output_fields": sum(
                    bool(terminal.get(field)) for field in output_fields
                ),
            }
        )
        if not blocked:
            errors.append("cross-manager customer request was not blocked before publication")

    options_results: list[dict[str, Any]] = []
    for manager, expected_member_count, expected_customer_count in (
        ("Local Fixture Manager", 1, 2),
        ("Second Fixture Manager", 1, 2),
    ):
        try:
            response = session.get(
                base_url + "/api/leader_scope_options",
                params={"manager": manager, "include_customers": "true"},
                timeout=timeout,
            )
            payload = _response_json(response)
        except requests.RequestException:
            response = None
            payload = {}
        members = payload.get("members") or []
        customers = payload.get("customers") or []
        isolated = bool(
            response is not None
            and response.status_code == 200
            and payload.get("success") is True
            and len(members) == expected_member_count
            and len(customers) == expected_customer_count
        )
        options_results.append(
            {
                "isolated": isolated,
                "member_count": len(members),
                "customer_count": len(customers),
                "status_code": response.status_code if response is not None else 0,
            }
        )
        if not isolated:
            errors.append("Leader scope options did not remain team-isolated")

    return _gate_result(
        ok=not errors,
        immediate_member_cases=immediate_results,
        customer_ownership_cases=ownership_results,
        scope_option_cases=options_results,
        error_count=len(errors),
        error_sha256=_digest(errors),
        live_validation_performed=False,
        production_accuracy_claimed=False,
    )


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
    source_contract_dir = scratch / "source-contracts"
    source_contract_summary = source_contract_dir / "summary.json"
    gates["source_contracts"] = _run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_local_source_contracts.py"),
            "--enable-local-fixtures",
            "--scenario",
            "multi_manager",
            "--manifest",
            str(args.manifest),
            "--summary",
            str(source_contract_summary),
        ],
        summary_path=source_contract_summary,
        projector=_project_source_contracts,
    )
    capability_summary = scratch / "snowflake-capabilities" / "summary.json"
    gates["snowflake_capabilities"] = _run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "profile_snowflake_capabilities.py"),
            "--enable-local-fixtures",
            "--scenario",
            "multi_manager",
            "--manifest",
            str(args.manifest),
            "--summary",
            str(capability_summary),
        ],
        summary_path=capability_summary,
        projector=_project_snowflake_capabilities,
    )
    if args.csone_corpus_dir is not None:
        corpus_summary = scratch / "real-csone-corpus" / "summary.json"
        gates["real_csone_corpus"] = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "profile_csone_corpus.py"),
                "--input-dir",
                str(args.csone_corpus_dir),
                "--summary",
                str(corpus_summary),
            ],
            summary_path=corpus_summary,
            projector=_project_csone_corpus,
        )
        replay_summary = scratch / "real-csone-replay" / "summary.json"
        gates["real_csone_replay"] = _run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_csone_corpus_replay.py"),
                "--input-dir",
                str(args.csone_corpus_dir),
                "--max-rows",
                str(int(args.csone_replay_max_rows)),
                "--summary",
                str(replay_summary),
            ],
            summary_path=replay_summary,
            projector=_project_csone_replay,
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
            with _fixture_runtime(
                scratch,
                csone_corpus_dir=args.csone_corpus_dir,
                csone_replay_max_rows=args.csone_replay_max_rows,
            ) as (base_url, log_path):
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
                    matrix_gate["live_validation_performed"] = False
                    matrix_gate["fixture_validation_performed"] = True
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

    if args.skip_matrix:
        gates["multi_manager_reports"] = _skipped_gate(
            "operator requested skip"
        )
    else:
        try:
            with _fixture_runtime(
                scratch,
                scenario="multi_manager",
                csone_corpus_dir=args.csone_corpus_dir,
                csone_replay_max_rows=args.csone_replay_max_rows,
            ) as (multi_base_url, multi_log_path):
                gates["multi_manager_isolation"] = probe_multi_manager_isolation(
                    base_url=multi_base_url,
                    timeout=args.request_timeout,
                )
                multi_matrix_dir = scratch / "multi-manager-report-matrix"
                multi_matrix_dir.mkdir(parents=True, exist_ok=True)
                multi_gate = _run_command(
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "run_report_option_matrix.py"),
                        "--base-url",
                        multi_base_url,
                        "--downloads-dir",
                        str(multi_matrix_dir),
                        "--days",
                        str(args.days),
                        "--blocks",
                        REQUIRED_MATRIX_BLOCKS,
                        "--strict",
                        "--baseline-mode",
                        "off",
                        "--stop-on-failure",
                        "--local-acceptance",
                    ]
                )
                multi_summary = _find_matrix_summary(multi_matrix_dir)
                projection = _project_multi_manager_matrix(
                    _read_json(multi_summary) if multi_summary else {}
                )
                projection_ok = projection.pop("projected_ok", False)
                multi_gate.update(projection)
                multi_gate["summary_present"] = multi_summary is not None
                multi_gate["ok"] = bool(
                    multi_gate.get("ok") and projection_ok
                )
                multi_gate["status"] = (
                    "passed" if multi_gate["ok"] else "failed"
                )
                gates["multi_manager_reports"] = multi_gate
            gates["multi_manager_runtime_log"] = _gate_result(
                ok=True,
                log_bytes=multi_log_path.stat().st_size,
                log_sha256=hashlib.sha256(multi_log_path.read_bytes()).hexdigest(),
                retained=False,
                live_validation_performed=False,
                production_accuracy_claimed=False,
            )
        except Exception as exc:  # noqa: BLE001
            failure = _gate_result(
                ok=False,
                error_kind=type(exc).__name__,
                error_sha256=_digest(str(exc)),
                production_accuracy_claimed=False,
            )
            gates["multi_manager_reports"] = failure
            gates.setdefault("multi_manager_isolation", failure)

    gates["ask_ai_replay"] = (
        _skipped_gate("operator requested skip")
        if args.skip_replay
        else run_replay_gate()
    )
    return gates


def _work_machine_profile(
    args: argparse.Namespace,
    scratch: Path,
    candidate: ReleaseCandidateManifest,
) -> dict[str, Any]:
    base_url = _loopback_base_url(args.base_url)
    gates: dict[str, Any] = {
        "runtime_identity": probe_runtime_identity(
            base_url=base_url,
            timeout=args.request_timeout,
            expected_version=candidate.version,
            expected_build=str(candidate.build),
        )
    }
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
            "--manager",
            args.manager,
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
        matrix_gate["live_validation_performed"] = True
        matrix_gate["fixture_validation_performed"] = False
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
    local.add_argument(
        "--csone-corpus-dir",
        type=Path,
        default=(
            Path(os.environ["ADOPTIQ_CSONE_CORPUS_DIR"])
            if os.environ.get("ADOPTIQ_CSONE_CORPUS_DIR")
            else None
        ),
        help=(
            "Optional external directory of real CSOne xlsx exports. A metadata "
            "profile is retained and an in-memory pseudonymous replay feeds the "
            "healthy and multi-manager report matrices."
        ),
    )
    local.add_argument("--csone-replay-max-rows", type=int, default=600)
    local.add_argument("--skip-degraded-http", action="store_true")
    local.add_argument("--skip-degraded-reports", action="store_true")
    local.add_argument("--skip-matrix", action="store_true")
    local.add_argument("--skip-ai", action="store_true")
    local.add_argument("--skip-replay", action="store_true")

    work = subparsers.add_parser(
        "work-machine",
        help="Verify, launch, and run live acceptance against an exact Mac DMG.",
    )
    work.add_argument("--candidate-dmg", type=Path, required=True)
    work.add_argument("--candidate-manifest", type=Path, required=True)
    work.add_argument("--port", type=int, default=5153)
    work.add_argument("--candidate-startup-timeout", type=float, default=180.0)
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
            args.candidate_dmg = args.candidate_dmg.expanduser().resolve()
            args.candidate_manifest = args.candidate_manifest.expanduser().resolve()
            if not args.candidate_dmg.is_file() or args.candidate_dmg.is_symlink():
                raise ValueError("--candidate-dmg must be a regular non-symlink file")
            if (
                not args.candidate_manifest.is_file()
                or args.candidate_manifest.is_symlink()
            ):
                raise ValueError("--candidate-manifest must be a regular non-symlink file")
            if not 1 <= int(args.port) <= 65535:
                raise ValueError("--port must be between 1 and 65535")
            if args.csone_file:
                args.csone_file = args.csone_file.expanduser().resolve()
                if (
                    not args.csone_file.is_file()
                    or args.csone_file.suffix.casefold() != ".xlsx"
                ):
                    raise ValueError("--csone-file must be a readable .xlsx file")
        elif args.csone_corpus_dir is not None:
            args.csone_corpus_dir = args.csone_corpus_dir.expanduser().resolve()
            if not args.csone_corpus_dir.is_dir():
                raise ValueError("--csone-corpus-dir must be a readable directory")
            if not 1 <= int(args.csone_replay_max_rows) <= 10000:
                raise ValueError("--csone-replay-max-rows must be between 1 and 10000")
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
        if args.profile == "local":
            gates = _local_profile(args, retained)
            candidate_evidence: dict[str, Any] = {}
        else:
            with _live_candidate_runtime(
                candidate_dmg=args.candidate_dmg,
                candidate_manifest=args.candidate_manifest,
                port=args.port,
                startup_timeout=args.candidate_startup_timeout,
            ) as (base_url, candidate_manifest):
                args.base_url = base_url
                gates = _work_machine_profile(args, retained, candidate_manifest)
                gates["candidate_identity"] = _candidate_identity_gate(
                    candidate_manifest,
                    launch_controlled=True,
                )
                candidate_evidence = dict(gates["candidate_identity"])
    else:
        with tempfile.TemporaryDirectory(prefix="adoptiq-round146-") as temporary:
            scratch = Path(temporary)
            if args.profile == "local":
                gates = _local_profile(args, scratch)
                candidate_evidence = {}
            else:
                with _live_candidate_runtime(
                    candidate_dmg=args.candidate_dmg,
                    candidate_manifest=args.candidate_manifest,
                    port=args.port,
                    startup_timeout=args.candidate_startup_timeout,
                ) as (base_url, candidate_manifest):
                    args.base_url = base_url
                    gates = _work_machine_profile(args, scratch, candidate_manifest)
                    gates["candidate_identity"] = _candidate_identity_gate(
                        candidate_manifest,
                        launch_controlled=True,
                    )
                    candidate_evidence = dict(gates["candidate_identity"])

    summary = _acceptance_summary(
        profile=args.profile,
        started_at=started_at,
        gates=gates,
        sensitive_artifacts_retained=retained is not None,
        sensitive_dir=retained,
        candidate=candidate_evidence,
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
