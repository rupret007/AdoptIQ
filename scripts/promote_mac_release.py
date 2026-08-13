#!/usr/bin/env python3
"""Promote an immutable, live-validated Mac candidate without rebuilding it.

Publication is separate from packaging.  The operator checkout may contain newer
release tooling, but the candidate bytes, packaged source commit, smoke evidence,
controlled live run, and manual review must all bind to one tracked candidate
manifest.  Consumer visibility changes only after every check succeeds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from developer_candidate_security import scan_file
from release_manifest_lock import release_manifest_lock
from release_candidate_contract import (
    ReleaseCandidateManifest,
    verify_release_candidate,
)


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _run(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} is missing, not regular, or is a symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _git(root: Path, *args: str) -> str:
    result = _run(("git", *args), cwd=root)
    if result.returncode != 0:
        raise ValueError("Git source identity could not be resolved")
    return result.stdout.strip()


def _require_clean_operator_source(root: Path, candidate_commit: str) -> str:
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    upstream = _git(root, "rev-parse", "@{upstream}")
    status = _run(("git", "status", "--porcelain=v1", "--untracked-files=all"), cwd=root)
    if branch != "main":
        raise ValueError(f"Promotion requires main; found {branch or 'detached HEAD'}")
    if head != upstream:
        raise ValueError("promotion tooling HEAD must equal its configured upstream")
    if status.returncode != 0 or status.stdout.strip():
        raise ValueError("Promotion requires a clean source checkout")
    if _git(root, "cat-file", "-t", candidate_commit) != "commit":
        raise ValueError("candidate source identity is not a full Git commit")
    ancestry = _run(
        ("git", "merge-base", "--is-ancestor", candidate_commit, head),
        cwd=root,
    )
    if ancestry.returncode != 0:
        raise ValueError("candidate source commit is not an ancestor of trusted upstream main")
    return head


def _read_config_identity(root: Path) -> tuple[str, str]:
    try:
        body = (root / "config.py").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("config.py could not be read") from exc
    version = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]+)"', body)
    build = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]+)"', body)
    if version is None or build is None:
        raise ValueError("config.py is missing canonical version/build metadata")
    return version.group(1), build.group(1)


def _read_candidate_config_identity(root: Path, commit: str) -> tuple[str, str]:
    body = _git(root, "show", f"{commit}:config.py")
    version = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]+)"', body)
    build = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]+)"', body)
    if version is None or build is None:
        raise ValueError("candidate config.py is missing canonical version/build metadata")
    return version.group(1), build.group(1)


def _managed_path(
    path: Path,
    *,
    expected_tail: tuple[str, ...],
    label: str,
    approved_cloud_root: Path | None = None,
) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = expanded.resolve()
    cloud_root = (
        approved_cloud_root.expanduser().resolve()
        if approved_cloud_root is not None
        else (
            Path.home()
            / "Library"
            / "CloudStorage"
            / "OneDrive-Cisco"
        ).resolve()
    )
    forbidden = {Path("/").resolve(), Path.home().resolve(), cloud_root}
    if (
        resolved in forbidden
        or tuple(resolved.parts[-len(expected_tail) :]) != expected_tail
        or cloud_root not in resolved.parents
    ):
        raise ValueError(f"{label} is not the managed {'/'.join(expected_tail)} path")
    return resolved


def _validate_smoke(
    path: Path,
    *,
    version: str,
    build: str,
    candidate: Path,
    candidate_sha256: str,
    candidate_size_bytes: int,
) -> dict[str, Any]:
    payload = _read_json(path, label="smoke summary")
    if (
        payload.get("schema_version") != "frozen-candidate-smoke/v1"
        or payload.get("ok") is not True
    ):
        raise ValueError("frozen-candidate smoke did not pass")
    if (
        str(payload.get("expected_version")) != version
        or str(payload.get("expected_build")) != build
    ):
        raise ValueError("smoke summary version/build does not match the candidate")
    expected_candidate = candidate.expanduser().resolve()
    candidate_checks = (
        payload.get("candidate_path") == str(expected_candidate),
        payload.get("candidate_kind") == "file",
        payload.get("candidate_sha256") == candidate_sha256,
        not isinstance(payload.get("candidate_size_bytes"), bool),
        isinstance(payload.get("candidate_size_bytes"), int),
        payload.get("candidate_size_bytes") == candidate_size_bytes,
    )
    checks = payload.get("checks")
    version_check = checks.get("version") if isinstance(checks, dict) else None
    corpus_check = checks.get("corpus") if isinstance(checks, dict) else None
    integrity_check = (
        checks.get("candidate_integrity") if isinstance(checks, dict) else None
    )
    actual = version_check.get("actual") if isinstance(version_check, dict) else None
    corpus_evidence = (
        corpus_check.get("evidence") if isinstance(corpus_check, dict) else None
    )
    corpus_inventory_valid = isinstance(corpus_evidence, dict) and all(
        _positive_int(corpus_evidence.get(field))
        for field in ("files_total", "files_parsed", "customers", "chunks")
    )
    runtime_checks = (
        isinstance(version_check, dict) and version_check.get("ok") is True,
        isinstance(actual, dict),
        isinstance(actual, dict) and actual.get("version") == version,
        isinstance(actual, dict) and actual.get("build") == build,
        isinstance(actual, dict) and actual.get("frozen") is True,
        isinstance(actual, dict) and actual.get("restart_required") is False,
        payload.get("release_corpus_required") is True,
        payload.get("offline_model_probe") is True,
        isinstance(corpus_check, dict) and corpus_check.get("ok") is True,
        isinstance(corpus_check, dict)
        and corpus_check.get("release_requirements_enforced") is True,
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("available") is True,
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("boot_completed") is True,
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("boot_in_progress") is False,
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("embedder_status") == "ready",
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("reranker_status") == "ready",
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("dense_retrieval_status") == "ready",
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("dense_rows_remaining") == 0,
        isinstance(corpus_evidence, dict)
        and not isinstance(corpus_evidence.get("dense_rows_remaining"), bool),
        isinstance(corpus_evidence, dict)
        and corpus_evidence.get("ask_ai_retrieval_method") == "hybrid",
        corpus_inventory_valid,
        isinstance(integrity_check, dict) and integrity_check.get("ok") is True,
        isinstance(integrity_check, dict)
        and integrity_check.get("identity_unchanged") is True,
    )
    if not all(candidate_checks):
        raise ValueError("smoke summary is not bound to the exact candidate DMG")
    if not all(runtime_checks):
        raise ValueError(
            "smoke summary lacks frozen runtime or release-corpus evidence"
        )
    return payload


def _validate_live_acceptance(
    path: Path,
    *,
    candidate: ReleaseCandidateManifest,
) -> dict[str, Any]:
    payload = _read_json(path, label="live acceptance summary")
    gates = payload.get("gates")
    runtime = gates.get("runtime_identity") if isinstance(gates, dict) else None
    candidate_gate = gates.get("candidate_identity") if isinstance(gates, dict) else None
    expected_required = {
        "candidate_identity",
        "runtime_identity",
        "decision_reports",
        "report_matrix",
        "ai_features",
        "manager_workspace",
        "ask_ai_replay",
    }
    required = {
        str(item)
        for item in payload.get("required_gates") or []
        if isinstance(item, str)
    }
    gate_contract = all(
        isinstance(gates.get(name), dict)
        and gates[name].get("ok") is True
        and gates[name].get("status") == "passed"
        for name in expected_required
    ) if isinstance(gates, dict) else False
    identity_contract = bool(
        isinstance(candidate_gate, dict)
        and candidate_gate.get("release_status") == "eligible"
        and candidate_gate.get("source_commit_sha") == candidate.source_commit_sha
        and candidate_gate.get("artifact_name") == candidate.artifact.name
        and candidate_gate.get("artifact_sha256") == candidate.artifact.sha256
        and candidate_gate.get("artifact_size_bytes") == candidate.artifact.size_bytes
        and str(candidate_gate.get("version")) == candidate.version
        and str(candidate_gate.get("build")) == str(candidate.build)
        and candidate_gate.get("launch_controlled") is True
        and candidate_gate.get("candidate_environment_sanitized") is True
        and candidate_gate.get("external_baked_corpus_override_allowed") is False
    )
    decision = gates.get("decision_reports") if isinstance(gates, dict) else None
    matrix = gates.get("report_matrix") if isinstance(gates, dict) else None
    ai = gates.get("ai_features") if isinstance(gates, dict) else None
    workspace = gates.get("manager_workspace") if isinstance(gates, dict) else None
    replay = gates.get("ask_ai_replay") if isinstance(gates, dict) else None
    detailed_contract = (
        isinstance(decision, dict)
        and decision.get("live_validation_performed") is True
        and decision.get("pass_count") == 2
        and decision.get("scope_count") == 4
        and decision.get("passing_scope_count") == 4
        and decision.get("repeatability_ok") is True
        and decision.get("failure_count") == 0
        and isinstance(matrix, dict)
        and matrix.get("live_validation_performed") is True
        and matrix.get("scenario_inventory_complete") is True
        and _positive_int(matrix.get("expected_count"))
        and matrix.get("scenario_count") == matrix.get("expected_count")
        and matrix.get("completed_count") == matrix.get("expected_count")
        and matrix.get("passed_count") == matrix.get("expected_count")
        and matrix.get("failed_count") == 0
        and matrix.get("all_report_blocks_requested") is True
        and matrix.get("source_consistency_ok") is True
        and _positive_int(matrix.get("source_consistency_comparison_count"))
        and matrix.get("source_consistency_comparison_count")
        == matrix.get("source_consistency_expected_comparison_count")
        and matrix.get("source_consistency_required_report_families")
        == ["compact", "comprehensive", "leader", "renewal"]
        and matrix.get("source_consistency_projected_fields")
        == [
            "count",
            "identity_sha256",
            "attribution_sha256",
            "attributed_record_count",
            "source_state",
        ]
        and _positive_int(
            matrix.get("source_consistency_required_family_set_group_count")
        )
        and matrix.get("source_consistency_report_family_sets_compared")
        and all(
            family_set == ["compact", "comprehensive", "leader", "renewal"]
            for family_set in matrix.get(
                "source_consistency_report_family_sets_compared"
            )
        )
        and matrix.get("source_consistency_mismatch_count") == 0
        and matrix.get("source_freshness_mismatch_count") == 0
        and matrix.get("source_consistency_read_error_count") == 0
        and matrix.get("r114_audit_ok") is True
        and matrix.get("r114_audit_completed_count") == matrix.get("expected_count")
        and isinstance(ai, dict)
        and ai.get("live_validation_performed") is True
        and ai.get("pass_count") == 2
        and ai.get("repeatability_ok") is True
        and ai.get("failure_count") == 0
        and isinstance(workspace, dict)
        and workspace.get("live_validation_performed") is True
        and workspace.get("preview_coverage_complete") is True
        and workspace.get("comparison_ok") is True
        and workspace.get("canonical_ai_sync_ok") is True
        and workspace.get("canonical_ai_stream_ok") is True
        and isinstance(replay, dict)
        and replay.get("question_count") == 75
        and replay.get("passed_count") == 75
        and replay.get("canonical_check_count") == 25
        and replay.get("canonical_passed_count") == 25
    )
    checks = (
        payload.get("schema_version") == "round146-portable-acceptance/v1",
        payload.get("sanitized") is True,
        payload.get("do_not_commit") is True,
        payload.get("profile") == "work-machine",
        payload.get("all_passed") is True,
        payload.get("acceptance_complete") is True,
        payload.get("live_validation_performed") is True,
        payload.get("live_validation_passed") is True,
        payload.get("skipped_gates") == [],
        required == expected_required,
        gate_contract,
        identity_contract,
        detailed_contract,
        isinstance(runtime, dict),
        isinstance(runtime, dict) and runtime.get("ok") is True,
        isinstance(runtime, dict) and runtime.get("status") == "passed",
        isinstance(runtime, dict) and runtime.get("version") == candidate.version,
        isinstance(runtime, dict) and str(runtime.get("build")) == str(candidate.build),
        isinstance(runtime, dict) and runtime.get("frozen") is True,
        isinstance(runtime, dict) and runtime.get("restart_required") is False,
        isinstance(runtime, dict)
        and runtime.get("live_validation_performed") is True,
        isinstance(runtime, dict) and runtime.get("status_code") == 200,
    )
    if not all(checks):
        raise ValueError(
            "work-machine live acceptance is incomplete, skipped, failed, not bound "
            "to the exact candidate, or missing a required report/AI gate"
        )
    return payload


def _verify_dmg(dmg: Path, *, root: Path) -> None:
    if platform.system() != "Darwin":
        raise ValueError("Mac promotion must run on macOS")
    for argv, label in (
        (("hdiutil", "verify", str(dmg)), "DMG integrity"),
        (("codesign", "--verify", "--strict", str(dmg)), "DMG signature"),
    ):
        result = _run(argv, cwd=root)
        if result.returncode != 0:
            raise ValueError(f"{label} verification failed")
    with tempfile.TemporaryDirectory(prefix="adoptiq-promote-mount-") as mount:
        mount_path = Path(mount) / "AdoptIQ"
        mount_path.mkdir()
        attach = _run(
            ("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount_path), str(dmg)),
            cwd=root,
        )
        if attach.returncode != 0:
            raise ValueError("DMG could not be mounted for app verification")
        try:
            app = mount_path / "AdoptIQ.app"
            executable = app / "Contents" / "MacOS" / "AdoptIQ.bin"
            if not executable.is_file():
                raise ValueError("DMG does not contain the expected AdoptIQ.app executable")
            outer = {item.name for item in mount_path.iterdir()}
            expected_outer = {
                "AdoptIQ.app",
                "Applications",
                "README.md",
                "READ_ME_FIRST.txt",
                "Unblock AdoptIQ.command",
            }
            if outer != expected_outer:
                raise ValueError("DMG outer payload does not match the release allowlist")
            applications = mount_path / "Applications"
            if not applications.is_symlink() or os.readlink(applications) != "/Applications":
                raise ValueError("DMG Applications link is missing or unsafe")
            verify = _run(("codesign", "--verify", "--deep", "--strict", str(app)), cwd=root)
            if verify.returncode != 0:
                raise ValueError("AdoptIQ.app deep signature verification failed")
        finally:
            _run(("hdiutil", "detach", str(mount_path)), cwd=root)


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    rendered = value.strip()
    try:
        parsed = datetime.fromisoformat(
            rendered[:-1] + "+00:00" if rendered.endswith("Z") else rendered
        )
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


_REQUIRED_REVIEW_SCOPES = {
    "leader_team",
    "leader_member",
    "leader_customer",
    "comprehensive_portfolio",
    "comprehensive_customer",
    "compact_portfolio",
    "compact_customer",
    "renewal_portfolio",
    "renewal_customer",
    "subscription",
}
_REQUIRED_LINK_TYPES = {
    "action_plan",
    "adoption_barrier",
    "customer_pulse",
    "success_priority",
}


def _validate_manual_review(
    path: Path,
    *,
    candidate: ReleaseCandidateManifest,
    acceptance_summary: Path,
) -> dict[str, Any]:
    payload = _read_json(path, label="manual review summary")
    exact_fields = {
        "schema_version",
        "sanitized",
        "do_not_commit",
        "candidate",
        "acceptance_summary_sha256",
        "reviewer",
        "reviewed_at_utc",
        "manual_source_reconciliation_complete",
        "visual_review_complete",
        "second_manager_validated",
        "all_managers_validated",
        "report_scopes_reviewed",
        "link_types_opened",
        "claim_count",
        "mismatch_count",
        "unexplained_unknown_count",
        "missing_expected_link_count",
        "release_recommendation",
    }
    if set(payload) != exact_fields:
        raise ValueError("manual review summary fields do not match the required schema")
    observed_candidate = payload.get("candidate")
    expected_candidate = candidate.identity
    review_scopes = {
        str(item) for item in payload.get("report_scopes_reviewed") or []
    }
    link_types = {str(item) for item in payload.get("link_types_opened") or []}
    reviewer = payload.get("reviewer")
    checks = (
        payload.get("schema_version") == "adoptiq-live-manual-review/v1",
        payload.get("sanitized") is True,
        payload.get("do_not_commit") is True,
        observed_candidate == expected_candidate,
        payload.get("acceptance_summary_sha256") == _sha256(acceptance_summary),
        isinstance(reviewer, str) and 1 <= len(reviewer.strip()) <= 200,
        _valid_utc_timestamp(payload.get("reviewed_at_utc")),
        payload.get("manual_source_reconciliation_complete") is True,
        payload.get("visual_review_complete") is True,
        payload.get("second_manager_validated") is True,
        payload.get("all_managers_validated") is True,
        review_scopes == _REQUIRED_REVIEW_SCOPES,
        link_types == _REQUIRED_LINK_TYPES,
        _positive_int(payload.get("claim_count")),
        payload.get("mismatch_count") == 0,
        payload.get("unexplained_unknown_count") == 0,
        payload.get("missing_expected_link_count") == 0,
        payload.get("release_recommendation") == "go",
    )
    if not all(checks):
        raise ValueError(
            "manual review is incomplete, mismatched, or not bound to the exact candidate and acceptance run"
        )
    return payload


def _strict_pc_slot(pc: Any) -> dict[str, Any]:
    if not isinstance(pc, dict):
        raise ValueError("consumer release manifest has no trustworthy PC slot to preserve")
    build = pc.get("build")
    version = pc.get("version")
    artifact = pc.get("artifact")
    digest = pc.get("sha256")
    size = pc.get("size_bytes")
    released = pc.get("released_at_utc")
    if not _positive_int(build) or not isinstance(version, str) or not version.strip():
        raise ValueError("consumer release manifest PC slot has invalid version/build")
    if not isinstance(artifact, str) or "\\" in artifact:
        raise ValueError("consumer release manifest PC artifact path is unsafe")
    relative = PurePosixPath(artifact)
    expected_name = f"AdoptIQ-v{version}-build{build}.exe"
    if (
        relative.is_absolute()
        or relative.parts != ("AdoptIQ_PC", expected_name)
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("consumer release manifest PC artifact path is unsafe")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("consumer release manifest PC sha256 is invalid")
    if not _positive_int(size):
        raise ValueError("consumer release manifest PC size_bytes is invalid")
    if not _valid_utc_timestamp(released):
        raise ValueError("consumer release manifest PC release timestamp is invalid")
    return pc


def _strict_existing_manifest(path: Path) -> dict[str, Any]:
    payload = _read_json(path, label="consumer release manifest")
    if payload.get("schema") != 1:
        raise ValueError("consumer release manifest has an unsupported schema")
    if "pc" in payload:
        _strict_pc_slot(payload.get("pc"))
    return payload


def _verify_pc_artifact(releases: Path, pc: dict[str, Any] | None) -> None:
    if pc is None:
        return
    relative = PurePosixPath(str(pc["artifact"]))
    pc_directory = releases / "AdoptIQ_PC"
    if pc_directory.is_symlink() or not pc_directory.is_dir():
        raise ValueError("consumer PC artifact directory is missing or unsafe")
    artifact = pc_directory / relative.name
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError("consumer PC artifact is missing, not regular, or is a symlink")
    if artifact.resolve().parent != pc_directory.resolve():
        raise ValueError("consumer PC artifact resolves outside its managed directory")
    if artifact.stat().st_size != pc["size_bytes"] or _sha256(artifact) != pc["sha256"]:
        raise ValueError("consumer PC artifact does not match its preserved manifest slot")


def _ensure_rollback_backup(path: Path, payload: dict[str, Any]) -> bool:
    """Create a complete rollback file once; accept only an identical retry."""
    if path.exists() or path.is_symlink():
        if _read_json(path, label="rollback manifest") != payload:
            raise ValueError("rollback manifest already exists for different release state")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read_json(path, label="rollback manifest") != payload:
                raise ValueError(
                    "rollback manifest already exists for different release state"
                )
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _mac_slot_matches(
    slot: Any,
    *,
    version: str,
    build: str,
    artifact: str,
    sha256: str,
    size_bytes: int,
) -> bool:
    return isinstance(slot, dict) and (
        slot.get("version") == version
        and slot.get("build") == int(build)
        and slot.get("artifact") == artifact
        and slot.get("sha256") == sha256
        and slot.get("size_bytes") == size_bytes
    )


def _validate_mac_transition(
    slot: Any,
    *,
    version: str,
    build: str,
    artifact: str,
    sha256: str,
    size_bytes: int,
) -> bool:
    """Return idempotence; reject stale or same-number/different-byte writes."""

    if slot is None:
        return False
    if not isinstance(slot, dict):
        raise ValueError("existing Mac manifest slot is malformed")
    existing_build = slot.get("build")
    if not _positive_int(existing_build):
        raise ValueError("existing Mac manifest slot has invalid build")
    requested_build = int(build)
    if existing_build > requested_build:
        raise ValueError("refusing to replace a newer Mac release with a stale candidate")
    if existing_build == requested_build:
        if not _mac_slot_matches(
            slot,
            version=version,
            build=build,
            artifact=artifact,
            sha256=sha256,
            size_bytes=size_bytes,
        ):
            raise ValueError("same Mac build number already identifies different bytes")
        return True
    return False


def _atomic_copy(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source_digest = _sha256(source)
        if expected_sha256 is not None and source_digest != expected_sha256:
            raise ValueError(f"source changed before copying {destination.name}")
        shutil.copy2(source, temporary)
        copied_digest = _sha256(temporary)
        current_source_digest = _sha256(source)
        if (
            copied_digest != current_source_digest
            or (expected_sha256 is not None and copied_digest != expected_sha256)
        ):
            raise ValueError(f"copy verification failed for {destination.name}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def promote(
    *,
    root: Path,
    dmg: Path,
    candidate_manifest: Path,
    smoke_summary: Path,
    acceptance_summary: Path,
    manual_review_summary: Path,
    staging_dir: Path,
    releases_root: Path,
    approved: bool,
) -> dict[str, Any]:
    if not approved:
        raise ValueError("promotion requires separate explicit publication approval")
    root = root.resolve()
    manifest_path = candidate_manifest.expanduser().resolve()
    candidate = verify_release_candidate(
        manifest_path,
        dmg,
        sidecar_root=manifest_path.parent,
    )
    version = candidate.version
    build = str(candidate.build)
    expected_commit = candidate.source_commit_sha
    operator_commit = _require_clean_operator_source(root, expected_commit)
    if _read_candidate_config_identity(root, expected_commit) != (version, build):
        raise ValueError("candidate manifest does not match its source config.py")
    expected_name = f"AdoptIQ-v{version}-build{build}.dmg"
    expanded_dmg = dmg.expanduser()
    if expanded_dmg.is_symlink():
        raise ValueError(f"expected regular candidate {expected_name}")
    dmg = expanded_dmg.resolve()
    if dmg.name != expected_name or not dmg.is_file():
        raise ValueError(f"expected regular candidate {expected_name}")
    source_outbox = root / "OUTBOX"
    if (
        source_outbox.is_symlink()
        or not source_outbox.is_dir()
        or source_outbox.resolve().parent != root
    ):
        raise ValueError("repository OUTBOX is missing or unsafe")
    sidecars = {item.path: manifest_path.parent / item.path for item in candidate.sidecars}
    build_info = sidecars.get("build_info.txt")
    readme = sidecars.get("README.md")
    if build_info is None or readme is None:
        raise ValueError("candidate manifest must bind README.md and build_info.txt")
    build_info_text = build_info.read_text(encoding="utf-8", errors="replace")
    if f"AdoptIQ v{version} build {build}" not in build_info_text or f"Source commit: {expected_commit}" not in build_info_text:
        raise ValueError("build_info.txt does not match the pinned version/build/commit")
    for sidecar in (readme, build_info):
        findings = scan_file(sidecar, display_path=f"candidate-sidecar/{sidecar.name}")
        if findings.get("findings"):
            raise ValueError(f"candidate sidecar failed security scan: {sidecar.name}")

    digest = _sha256(dmg)
    size = dmg.stat().st_size
    if digest != candidate.artifact.sha256 or size != candidate.artifact.size_bytes:
        raise ValueError("candidate bytes no longer match the release manifest")
    _validate_smoke(
        smoke_summary,
        version=version,
        build=build,
        candidate=dmg,
        candidate_sha256=digest,
        candidate_size_bytes=size,
    )
    _validate_live_acceptance(
        acceptance_summary,
        candidate=candidate,
    )
    _validate_manual_review(
        manual_review_summary,
        candidate=candidate,
        acceptance_summary=acceptance_summary,
    )
    _verify_dmg(dmg, root=root)
    if dmg.stat().st_size != size or _sha256(dmg) != digest:
        raise ValueError("candidate DMG changed after smoke/signature verification")

    staging = _managed_path(
        staging_dir,
        expected_tail=("AI Projects", "Staging", "AdoptIQ_MAC", "OUTBOX"),
        label="Mac staging directory",
    )
    releases = _managed_path(
        releases_root,
        expected_tail=("AI Projects", "OUTBOX"),
        label="consumer releases root",
    )
    if not staging.is_dir() or not releases.is_dir():
        raise ValueError("managed staging and releases directories must already exist and be hydrated")
    mac_dir = releases / "AdoptIQ"
    if mac_dir.is_symlink():
        raise ValueError("consumer Mac release directory must not be a symlink")
    mac_dir.mkdir(exist_ok=True)

    consumer_manifest_path = releases / "latest.json"
    artifact = f"AdoptIQ/{dmg.name}"
    backup = source_outbox / f"latest.before-mac-build{build}.json"
    with release_manifest_lock(releases, publisher="promote_mac_release"):
        existing = _strict_existing_manifest(consumer_manifest_path)
        manifest_digest_before = _sha256(consumer_manifest_path)
        pc_before = json.loads(json.dumps(existing.get("pc"), sort_keys=True))
        _verify_pc_artifact(releases, existing.get("pc"))
        already_promoted = _validate_mac_transition(
            existing.get("mac"),
            version=version,
            build=build,
            artifact=artifact,
            sha256=digest,
            size_bytes=size,
        )
        rollback_created = False
        if already_promoted:
            rollback_payload = _strict_existing_manifest(backup)
            rollback_pc = json.loads(json.dumps(rollback_payload.get("pc"), sort_keys=True))
            if rollback_pc != pc_before or _mac_slot_matches(
                rollback_payload.get("mac"),
                version=version,
                build=build,
                artifact=artifact,
                sha256=digest,
                size_bytes=size,
            ):
                raise ValueError("existing rollback manifest cannot restore the prior Mac slot")
        else:
            rollback_created = _ensure_rollback_backup(backup, existing)

        # Copy bytes first. Consumers see the new build only after the manifest
        # is atomically updated last while the shared writer lock is held.
        _atomic_copy(dmg, staging / dmg.name, expected_sha256=digest)
        _atomic_copy(readme, staging / "README.md")
        _atomic_copy(build_info, staging / "build_info.txt")
        _atomic_copy(dmg, mac_dir / dmg.name, expected_sha256=digest)
        _atomic_copy(readme, mac_dir / "README.md")

        if already_promoted:
            if _sha256(consumer_manifest_path) != manifest_digest_before:
                raise ValueError(
                    "consumer release manifest changed during promotion; retry from a fresh review"
                )
            return {
                "schema_version": "adoptiq-mac-promotion/v1",
                "ok": True,
                "version": version,
                "build": build,
                "source_commit": expected_commit,
                "dmg_sha256": digest,
                "dmg_size_bytes": size,
                "operator_commit": operator_commit,
                "manifest": str(consumer_manifest_path),
                "rollback_manifest": str(backup),
                "published_at_utc": datetime.now(timezone.utc).isoformat(),
                "pc_slot_preserved": True,
                "already_promoted": True,
                "rollback_created": False,
            }

        from write_release_manifest import merge_manifest, write_atomic

        merged = merge_manifest(
            existing,
            platform="mac",
            version=version,
            build=build,
            artifact=artifact,
            sha256=digest,
            size_bytes=size,
            notes=f"Mac Build {build} promoted from source commit {expected_commit}",
        )
        if json.loads(json.dumps(merged.get("pc"), sort_keys=True)) != pc_before:
            raise ValueError("promotion would alter the existing PC manifest slot")
        if _sha256(consumer_manifest_path) != manifest_digest_before:
            raise ValueError("consumer release manifest changed during promotion; retry from a fresh review")
        write_atomic(str(consumer_manifest_path), merged)
        published = _read_json(
            consumer_manifest_path,
            label="published consumer release manifest",
        )
        if published != merged or json.loads(
            json.dumps(published.get("pc"), sort_keys=True)
        ) != pc_before:
            raise ValueError("published consumer release manifest failed verification")

    return {
        "schema_version": "adoptiq-mac-promotion/v1",
        "ok": True,
        "version": version,
        "build": build,
        "source_commit": expected_commit,
        "dmg_sha256": digest,
        "dmg_size_bytes": size,
        "operator_commit": operator_commit,
        "manifest": str(consumer_manifest_path),
        "rollback_manifest": str(backup),
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "pc_slot_preserved": "pc" not in existing or published.get("pc") == existing.get("pc"),
        "already_promoted": False,
        "rollback_created": rollback_created,
    }


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=root)
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--smoke-summary", type=Path, required=True)
    parser.add_argument("--acceptance-summary", type=Path, required=True)
    parser.add_argument("--manual-review-summary", type=Path, required=True)
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=Path.home() / "Library/CloudStorage/OneDrive-Cisco/AI Projects/Staging/AdoptIQ_MAC/OUTBOX",
    )
    parser.add_argument(
        "--releases-root",
        type=Path,
        default=Path.home() / "Library/CloudStorage/OneDrive-Cisco/AI Projects/OUTBOX",
    )
    parser.add_argument("--approve-publish", action="store_true")
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path(tempfile.gettempdir()) / "adoptiq-mac-promotion.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = promote(
            root=args.repo_root,
            dmg=args.dmg,
            candidate_manifest=args.candidate_manifest,
            smoke_summary=args.smoke_summary,
            acceptance_summary=args.acceptance_summary,
            manual_review_summary=args.manual_review_summary,
            staging_dir=args.staging_dir,
            releases_root=args.releases_root,
            approved=args.approve_publish,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        return 1
    try:
        summary = args.summary.expanduser().resolve()
        summary.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{summary.name}.", suffix=".tmp", dir=summary.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, summary)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        print(json.dumps(result, indent=2, sort_keys=True))
        print(
            "Promotion succeeded, but its summary could not be written; "
            "do not repeat publication. Preserve this stdout evidence and repair "
            f"the summary destination separately ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 0
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
