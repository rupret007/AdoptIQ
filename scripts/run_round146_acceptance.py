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
import signal
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
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
    REQUIRED_SCENARIOS,
    SOURCE_MODE,
    build_scenario_bundle,
    load_manifest,
)
import adoptiq_backend as backend  # noqa: E402
from csone_corpus_replay import (  # noqa: E402
    MAX_PREPARED_REPLAY_BYTES,
    PreparedCsoneReplay,
    prepared_replay_from_bytes,
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
from report_source_parity import (  # noqa: E402
    PARITY_PROJECTED_FIELDS,
    PARITY_SHEET_NAMES,
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
REQUIRED_SOURCE_PARITY_FIELDS = PARITY_PROJECTED_FIELDS
REQUIRED_SOURCE_PARITY_SHEETS = PARITY_SHEET_NAMES
REQUIRED_METAMORPHIC_CHECKS = (
    "artifact_invariance",
    "identical_duplicate_invariance",
    "conflicting_duplicate_quarantine",
    "invalid_id_publication_block",
    "identity_quarantine",
    "freshness_truth",
    "scope_isolation",
    "ask_ai_origin_transport",
)
REQUIRED_METAMORPHIC_CASE_COUNTS = {
    "artifact_invariance": 4,
    "identical_duplicate_invariance": 8,
    "conflicting_duplicate_quarantine": 6,
    "invalid_id_publication_block": 2,
    "identity_quarantine": 2,
    "freshness_truth": 4,
    "scope_isolation": 7,
    "ask_ai_origin_transport": 2,
}
METAMORPHIC_SUMMARY_SCHEMA = "round169-metamorphic/v1"
REQUIRED_METAMORPHIC_SUMMARY_KEYS = frozenset(
    {
        "schema",
        "sanitized",
        "aggregate_only",
        "do_not_commit_artifacts",
        "companion_http_negative_control_required",
        "live_validation_performed",
        "production_accuracy_claimed",
        "all_passed",
        "check_count",
        "passed_count",
        "checks",
    }
)
REQUIRED_SOURCE_PARITY_SUMMARY_KEYS = frozenset(
    {
        "ok",
        "comparison_requirement_met",
        "groups_discovered",
        "groups_evaluated",
        "comparisons",
        "comparisons_expected",
        "required_report_families",
        "required_sheets",
        "projected_fields",
        "required_family_set_group_count",
        "required_family_set_group_count_expected",
        "report_family_sets_compared",
        "cohort_declaration_errors",
        "cohort_membership_errors",
        "incomplete_equivalent_scope_groups",
        "duplicate_family_groups",
        "scope_mismatches",
        "identity_quality_errors",
        "ignored_non_parity_scenario_count",
        "mismatches",
        "freshness_mismatches",
        "read_errors",
        "max_freshness_skew_seconds",
        "privacy",
    }
)
REQUIRED_CSONE_REPLAY_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "sanitized",
        "do_not_commit",
        "source_mode",
        "live_snowflake_validation_performed",
        "source_rows_exported",
        "source_values_exported",
        "raw_values_retained",
        "production_accuracy_claimed",
        "all_passed",
        "loader_contract",
        "prepared_replay",
        "replay",
    }
)
REQUIRED_DECISION_SCOPES = frozenset(
    {"team", "member", "customer", "comprehensive"}
)
REQUIRED_AI_SCENARIOS = frozenset(
    {
        "portfolio_headline_sync",
        "action_plan_details_sync",
        "support_case_search_sync",
        "customer_risk_sync",
        "portfolio_risk_stream",
        "delivery_parity_sync",
        "delivery_parity_stream",
        "conversation_follow_up_sync",
        "unanswerable_sync",
        "prompt_injection_resistance_sync",
        "external_intelligence",
    }
)
REQUIRED_SOURCE_CONTRACT_CHECKS = frozenset(
    {
        "count_contract",
        "enhanced_account_contract",
        "secondary_attribution",
        "customer_search_attribution",
        "failure_state_not_zero",
        "policy_blocked_sources_not_zero",
        "unknown_query_rejected",
        "parameter_binding_and_family_coverage",
    }
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
_FIXTURE_STARTUP_BASE_SECONDS = 60.0
_FIXTURE_CORPUS_STARTUP_MAX_SECONDS = 900.0
_FIXTURE_CORPUS_SECONDS_PER_WORKBOOK = 2.0
_FIXTURE_CORPUS_BYTES_PER_SECOND = 64 * 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 3600.0
_COMMAND_OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024
_RUNTIME_LOG_LIMIT_BYTES = 16 * 1024 * 1024
_SUMMARY_JSON_LIMIT_BYTES = 8 * 1024 * 1024
_R114_AUDIT_OUTPUT_LIMIT_BYTES = 1024 * 1024
_R114_SUCCESS_MARKER_MIN_BYTES = len(b"CRITICAL_ISSUES_FOUND=False")
REQUIRED_R114_AUDIT_RESULT_KEYS = frozenset(
    {
        "ok",
        "critical",
        "reason",
        "returncode",
        "marker",
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "timed_out",
        "output_truncated",
    }
)


class _BoundedDigestWriter(io.TextIOBase):
    """Hash text without retaining it and abort when a child exceeds its budget."""

    def __init__(self, limit: int) -> None:
        self.limit = int(limit)
        self.byte_count = 0
        self._digest = hashlib.sha256()

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        raw = str(value).encode("utf-8", "replace")
        self.byte_count += len(raw)
        if self.byte_count > self.limit:
            raise RuntimeError("prepared replay diagnostic output exceeded its bound")
        self._digest.update(raw)
        return len(value)

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


class _BoundedPipeCapture:
    """Drain a child pipe without ever retaining or hashing past its cap."""

    def __init__(
        self,
        stream: Any,
        *,
        limit_bytes: int,
        retain: bool = False,
        sink: Any | None = None,
        on_exceeded: Callable[[], None] | None = None,
    ) -> None:
        self.stream = stream
        self.limit_bytes = int(limit_bytes)
        self.sink = sink
        self.on_exceeded = on_exceeded
        self.byte_count = 0
        self._digest = hashlib.sha256()
        self._retained = bytearray() if retain else None
        self.exceeded = threading.Event()
        self.complete = False
        self.error_kind = ""
        self.thread = threading.Thread(
            target=self._drain,
            name="bounded-child-output-drain",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float = 15.0) -> None:
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            self.error_kind = self.error_kind or "capture_join_timeout"
            self.exceeded.set()

    def _fail_bound(self, kind: str) -> None:
        self.error_kind = self.error_kind or kind
        self.exceeded.set()
        if self.on_exceeded is not None:
            # The callback is deliberately best-effort process cleanup.  An
            # operating-system denial must not escape the drain thread (or
            # recursively invoke this callback from ``_drain``'s OSError
            # handler); the supervising thread observes ``exceeded`` and
            # performs the same fail-closed cleanup path.
            try:
                self.on_exceeded()
            except (OSError, subprocess.SubprocessError):
                pass

    def _drain(self) -> None:
        try:
            while True:
                remaining = self.limit_bytes - self.byte_count
                block = self.stream.read(min(64 * 1024, max(1, remaining + 1)))
                if not block:
                    self.complete = True
                    break
                if len(block) > remaining:
                    self.byte_count = self.limit_bytes + 1
                    self._fail_bound("output_limit")
                    break
                self.byte_count += len(block)
                self._digest.update(block)
                if self._retained is not None:
                    self._retained.extend(block)
                if self.sink is not None:
                    self.sink.write(block)
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._fail_bound(type(exc).__name__)
        finally:
            try:
                self.stream.close()
            except OSError:
                pass
            if self.sink is not None:
                try:
                    self.sink.flush()
                except OSError:
                    self._fail_bound("sink_flush_error")

    @property
    def digest_complete(self) -> bool:
        return self.complete and not self.exceeded.is_set()

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest() if self.digest_complete else ""

    @property
    def payload(self) -> bytes:
        if self._retained is None or not self.digest_complete:
            return b""
        return bytes(self._retained)


def _path_file_identity(
    path: Path,
    *,
    max_bytes: int,
) -> tuple[int, str]:
    """Hash a regular non-symlink file only when its full size is in bounds."""

    before = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise ValueError("bounded identity requires a regular non-symlink file")
    if not 0 <= before.st_size <= int(max_bytes):
        raise ValueError("bounded identity file exceeds its byte limit")
    digest = hashlib.sha256()
    total = 0
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("bounded identity file changed before read")
        while total <= int(max_bytes):
            block = os.read(
                descriptor,
                min(64 * 1024, int(max_bytes) + 1 - total),
            )
            if not block:
                break
            total += len(block)
            if total > int(max_bytes):
                raise ValueError("bounded identity file exceeds its byte limit")
            digest.update(block)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = path.lstat()
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field) != getattr(after_fd, field)
        or getattr(before, field) != getattr(after_path, field)
        for field in identity_fields
    ):
        raise ValueError("bounded identity file changed during read")
    return total, digest.hexdigest()


def _bind_gate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Put a Windows gate in a kill-on-close Job Object; POSIX uses sessions."""

    if os.name != "nt":
        return
    try:  # pragma: no cover - exercised on the Windows build lane
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            handle,
            wintypes.HANDLE(process._handle),  # noqa: SLF001
        )
        if not assigned:
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "gate Job Object assignment failed")
        process._adoptiq_kill_job_handle = handle  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - fail closed at OS isolation boundary
        try:
            process.kill()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise OSError("Windows gate process-tree isolation unavailable") from exc


def _terminate_gate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Bounded best-effort termination of a gate and all descendants."""

    if os.name == "nt":  # pragma: no cover - exercised on the Windows build lane
        job_handle = getattr(process, "_adoptiq_kill_job_handle", None)
        if job_handle:
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.TerminateJobObject(job_handle, 1)
                kernel32.CloseHandle(job_handle)
            finally:
                process._adoptiq_kill_job_handle = None  # type: ignore[attr-defined]
        elif process.poll() is None:
            try:
                subprocess.run(  # noqa: S603
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
    else:
        # Every caller launches the gate as a new session leader, so its PID is
        # also the process-group ID.  Always follow TERM with a group-wide KILL:
        # the leader can exit promptly while a descendant ignores TERM.  While
        # any descendant remains, POSIX keeps the PGID allocated; if the group is
        # already empty killpg returns ESRCH instead of addressing a lone PID.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                process.terminate()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(0.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return


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


def _scratch_confidentiality_gate(
    root: Path,
    *,
    retained: bool,
) -> dict[str, Any]:
    """Harden and recursively attest one acceptance scratch tree.

    Real-corpus workbooks, runtime logs, and sidecars can contain workstation
    paths or source records.  The containing directory is made private first,
    then every regular file/directory is normalized and audited.  Evidence is
    counts and hashes only; paths and filenames never leave this function.
    """

    directory_count = 0
    file_count = 0
    symlink_count = 0
    special_file_count = 0
    broad_permission_count = 0
    ownership_mismatch_count = 0
    error_kind = ""
    error_sha256 = ""
    expected_uid = os.geteuid() if hasattr(os, "geteuid") else None
    try:
        root_stat = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError("acceptance scratch root must be a real directory")
        root.chmod(0o700)

        # Normalize only objects contained by the private, non-symlink root.
        # Symlinks and special files are rejected and never followed.
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            directory_path.chmod(0o700)
            for name in (*dirnames, *filenames):
                candidate = directory_path / name
                candidate_stat = candidate.lstat()
                if stat.S_ISLNK(candidate_stat.st_mode):
                    symlink_count += 1
                elif stat.S_ISDIR(candidate_stat.st_mode):
                    candidate.chmod(0o700)
                elif stat.S_ISREG(candidate_stat.st_mode):
                    candidate.chmod(0o600)
                else:
                    special_file_count += 1

        # Rewalk after normalization so a chmod failure, late-created entry,
        # or broad mode cannot be reported green.
        directory_count = 0
        file_count = 0
        symlink_count = 0
        special_file_count = 0
        broad_permission_count = 0
        ownership_mismatch_count = 0
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            directory_stat = directory_path.lstat()
            directory_count += 1
            if stat.S_IMODE(directory_stat.st_mode) != 0o700:
                broad_permission_count += 1
            if expected_uid is not None and directory_stat.st_uid != expected_uid:
                ownership_mismatch_count += 1
            for name in (*dirnames, *filenames):
                candidate_stat = (directory_path / name).lstat()
                if stat.S_ISLNK(candidate_stat.st_mode):
                    symlink_count += 1
                    continue
                if stat.S_ISDIR(candidate_stat.st_mode):
                    continue
                if stat.S_ISREG(candidate_stat.st_mode):
                    file_count += 1
                    if stat.S_IMODE(candidate_stat.st_mode) != 0o600:
                        broad_permission_count += 1
                    if expected_uid is not None and candidate_stat.st_uid != expected_uid:
                        ownership_mismatch_count += 1
                else:
                    special_file_count += 1
    except Exception as exc:  # noqa: BLE001 - emit only sanitized evidence
        error_kind = type(exc).__name__
        error_sha256 = _digest(str(exc))

    ok = bool(
        not error_kind
        and symlink_count == 0
        and special_file_count == 0
        and broad_permission_count == 0
        and ownership_mismatch_count == 0
        and directory_count >= 1
    )
    evidence = {
        "retained": bool(retained),
        "directory_count": directory_count,
        "file_count": file_count,
        "directory_mode": "0700",
        "file_mode": "0600",
        "symlink_count": symlink_count,
        "special_file_count": special_file_count,
        "broad_permission_count": broad_permission_count,
        "ownership_mismatch_count": ownership_mismatch_count,
        "recursive_assertion_complete": not error_kind,
        "private_mode_contract_sha256": _digest(
            {
                "retained": bool(retained),
                "directories": directory_count,
                "files": file_count,
                "directory_mode": "0700",
                "file_mode": "0600",
                "symlinks": symlink_count,
                "special": special_file_count,
                "broad": broad_permission_count,
                "ownership": ownership_mismatch_count,
            }
        ),
    }
    if error_kind:
        evidence.update(
            {
                "error_kind": error_kind,
                "error_sha256": error_sha256,
            }
        )
    return _gate_result(ok=ok, **evidence)


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


def _reject_duplicate_summary_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate summary JSON key")
        result[key] = value
    return result


def _read_json(
    path: Path,
    *,
    allowed_root: Path | None = None,
    max_bytes: int = _SUMMARY_JSON_LIMIT_BYTES,
) -> dict[str, Any]:
    """Read one bounded, stable, regular non-symlink child summary."""

    requested = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        root = (allowed_root or requested.parent).expanduser().resolve()
        parent = requested.parent.resolve()
        if parent != root and root not in parent.parents:
            return {}
        before = requested.lstat()
        if (
            requested.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= int(max_bytes)
        ):
            return {}
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(requested, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino)
                != (before.st_dev, before.st_ino)
            ):
                return {}
            raw = bytearray()
            while len(raw) <= int(max_bytes):
                block = os.read(
                    descriptor,
                    min(64 * 1024, int(max_bytes) + 1 - len(raw)),
                )
                if not block:
                    break
                raw.extend(block)
            after_fd = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = requested.lstat()
        identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
        if (
            len(raw) > int(max_bytes)
            or any(
                getattr(before, field) != getattr(after_fd, field)
                or getattr(before, field) != getattr(after_path, field)
                for field in identity_fields
            )
        ):
            return {}
        payload = json.loads(
            bytes(raw).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_summary_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _bounded_git_output(
    *args: str,
    timeout_seconds: float = 5.0,
    output_limit_bytes: int = 1024 * 1024,
) -> tuple[str, bool, str]:
    """Return a small Git result without unbounded pipe capture or child leakage."""

    try:
        if not 1.0 <= float(timeout_seconds) <= 60.0:
            return "", False, "invalid_timeout"
        if not 256 <= int(output_limit_bytes) <= 4 * 1024 * 1024:
            return "", False, "invalid_output_limit"
        process = subprocess.Popen(  # noqa: S603
            ["git", *args],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        _bind_gate_process_tree(process)
        if process.stdout is None:
            raise OSError("bounded Git pipe was not created")

        capture = _BoundedPipeCapture(
            process.stdout,
            limit_bytes=int(output_limit_bytes),
            retain=True,
            on_exceeded=lambda: _terminate_gate_process_tree(process),
        )
        capture.start()
        deadline = time.monotonic() + float(timeout_seconds)
        failure_kind = ""
        while process.poll() is None:
            if time.monotonic() >= deadline:
                failure_kind = "timeout"
                _terminate_gate_process_tree(process)
                break
            if capture.exceeded.is_set():
                failure_kind = "output_limit"
                _terminate_gate_process_tree(process)
                break
            time.sleep(0.02)
        try:
            return_code = int(process.wait(timeout=5))
        except subprocess.TimeoutExpired:
            failure_kind = failure_kind or "timeout"
            _terminate_gate_process_tree(process)
            return_code = (
                int(process.returncode) if process.returncode is not None else -1
            )
        _terminate_gate_process_tree(process)
        capture.join(timeout=5)
        if capture.exceeded.is_set():
            failure_kind = failure_kind or "output_limit"
        if failure_kind or return_code != 0 or not capture.digest_complete:
            return "", False, failure_kind or "incomplete_output"
        return capture.payload.decode("utf-8", errors="replace").strip(), True, ""
    except (OSError, TypeError, ValueError):
        return "", False, "os_error"


def _git_metadata() -> dict[str, Any]:
    status, status_ok, status_error = _bounded_git_output("status", "--porcelain")
    sha, sha_ok, sha_error = _bounded_git_output("rev-parse", "HEAD")
    branch, branch_ok, branch_error = _bounded_git_output(
        "branch", "--show-current"
    )
    sha_valid = bool(sha_ok and re.fullmatch(r"[0-9a-f]{40,64}", sha))
    branch_valid = bool(branch_ok and len(branch) <= 255 and "\n" not in branch)
    complete = bool(status_ok and sha_valid and branch_valid)
    failure_kinds = sorted(
        {
            error
            for error in (
                status_error if not status_ok else "",
                (sha_error or "invalid_sha") if not sha_valid else "",
                (branch_error or "invalid_branch") if not branch_valid else "",
            )
            if error
        }
    )
    return {
        "sha": sha if sha_valid else "",
        "branch": branch if branch_valid else "",
        "dirty": bool(status) if status_ok else True,
        "changed_path_count": len(status.splitlines()) if status_ok and status else 0,
        "metadata_complete": complete,
        "metadata_failure_count": len(failure_kinds),
        "metadata_failure_kinds": failure_kinds,
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
    timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS,
    output_limit_bytes: int = _COMMAND_OUTPUT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Run one gate while retaining no command line or raw child output."""

    started = time.monotonic()
    timed_out = False
    output_truncated = False
    return_code = -1
    stdout_bytes = 0
    stdout_sha256 = ""
    stdout_digest_complete = False
    stderr_bytes = 0
    stderr_sha256 = ""
    stderr_digest_complete = False
    try:
        if not 1.0 <= float(timeout_seconds) <= 7200.0:
            raise ValueError("gate timeout must be between 1 and 7200 seconds")
        if not 1024 <= int(output_limit_bytes) <= 64 * 1024 * 1024:
            raise ValueError("gate output limit must be between 1 KiB and 64 MiB")
        process = subprocess.Popen(  # noqa: S603
            list(command),
            cwd=REPO_ROOT,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        _bind_gate_process_tree(process)
        if process.stdout is None or process.stderr is None:
            raise OSError("bounded child pipes were not created")

        def stop_output_process() -> None:
            _terminate_gate_process_tree(process)

        stdout_capture = _BoundedPipeCapture(
            process.stdout,
            limit_bytes=int(output_limit_bytes),
            on_exceeded=stop_output_process,
        )
        stderr_capture = _BoundedPipeCapture(
            process.stderr,
            limit_bytes=int(output_limit_bytes),
            on_exceeded=stop_output_process,
        )
        stdout_capture.start()
        stderr_capture.start()
        deadline = time.monotonic() + float(timeout_seconds)
        while process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_gate_process_tree(process)
                break
            if stdout_capture.exceeded.is_set() or stderr_capture.exceeded.is_set():
                output_truncated = True
                _terminate_gate_process_tree(process)
                break
            time.sleep(0.05)
        try:
            return_code = int(process.wait(timeout=10))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_gate_process_tree(process)
            return_code = (
                int(process.returncode) if process.returncode is not None else -1
            )
        _terminate_gate_process_tree(process)
        stdout_capture.join()
        stderr_capture.join()
        output_truncated = bool(
            output_truncated
            or stdout_capture.exceeded.is_set()
            or stderr_capture.exceeded.is_set()
        )
        stdout_bytes = stdout_capture.byte_count
        stdout_sha256 = stdout_capture.sha256
        stdout_digest_complete = stdout_capture.digest_complete
        stderr_bytes = stderr_capture.byte_count
        stderr_sha256 = stderr_capture.sha256
        stderr_digest_complete = stderr_capture.digest_complete
    except OSError as exc:
        return _gate_result(
            ok=False,
            error_kind=type(exc).__name__,
            elapsed_seconds=round(time.monotonic() - started, 3),
            timed_out=False,
            output_truncated=False,
        )
    except (TypeError, ValueError) as exc:
        return _gate_result(
            ok=False,
            error_kind=type(exc).__name__,
            error_sha256=_digest(str(exc)),
            elapsed_seconds=round(time.monotonic() - started, 3),
            timed_out=False,
            output_truncated=False,
        )
    raw_summary = (
        _read_json(summary_path)
        if summary_path is not None and not timed_out and not output_truncated
        else {}
    )
    try:
        projected = projector(raw_summary) if projector is not None else {}
    except Exception as exc:  # noqa: BLE001 - fail-closed projector boundary
        projected = {
            "projected_ok": False,
            "projection_error_kind": type(exc).__name__,
            "projection_error_sha256": _digest(str(exc)),
        }
    summary_present = summary_path is None or bool(raw_summary)
    projected_ok = projected.pop("projected_ok", projector is None)
    ok = (
        return_code == 0
        and not timed_out
        and not output_truncated
        and summary_present
        and projected_ok is True
    )
    return _gate_result(
        ok=ok,
        return_code=return_code,
        elapsed_seconds=round(time.monotonic() - started, 3),
        stdout_bytes=stdout_bytes,
        stdout_sha256=stdout_sha256,
        stderr_bytes=stderr_bytes,
        stderr_sha256=stderr_sha256,
        stdout_digest_complete=stdout_digest_complete,
        stderr_digest_complete=stderr_digest_complete,
        timed_out=timed_out,
        output_truncated=output_truncated,
        summary_present=summary_present,
        **projected,
    )


def _run_prepared_replay_worker(
    command: Sequence[str],
    *,
    summary_path: Path,
    timeout_seconds: float,
) -> tuple[dict[str, Any], bytes]:
    """Run the sole N+S producer with bounded anonymous stdout transport."""

    started = time.monotonic()
    timed_out = False
    output_truncated = False
    return_code = -1
    stdout_bytes = 0
    stdout_sha256 = ""
    stdout_digest_complete = False
    stderr_bytes = 0
    stderr_sha256 = ""
    stderr_digest_complete = False
    payload_bytes = b""
    try:
        if not 1.0 <= float(timeout_seconds) <= 7200.0:
            raise ValueError("prepared replay timeout must be between 1 and 7200 seconds")
        process = subprocess.Popen(  # noqa: S603
            list(command),
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        _bind_gate_process_tree(process)
        if process.stdout is None or process.stderr is None:
            raise OSError("bounded prepared replay pipes were not created")

        def stop_output_process() -> None:
            _terminate_gate_process_tree(process)

        stdout_capture = _BoundedPipeCapture(
            process.stdout,
            limit_bytes=MAX_PREPARED_REPLAY_BYTES,
            retain=True,
            on_exceeded=stop_output_process,
        )
        stderr_capture = _BoundedPipeCapture(
            process.stderr,
            limit_bytes=_COMMAND_OUTPUT_LIMIT_BYTES,
            on_exceeded=stop_output_process,
        )
        stdout_capture.start()
        stderr_capture.start()
        deadline = time.monotonic() + float(timeout_seconds)
        while process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_gate_process_tree(process)
                break
            if stdout_capture.exceeded.is_set() or stderr_capture.exceeded.is_set():
                output_truncated = True
                _terminate_gate_process_tree(process)
                break
            time.sleep(0.05)
        try:
            return_code = int(process.wait(timeout=10))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_gate_process_tree(process)
            return_code = (
                int(process.returncode) if process.returncode is not None else -1
            )
        _terminate_gate_process_tree(process)
        stdout_capture.join()
        stderr_capture.join()
        output_truncated = bool(
            output_truncated
            or stdout_capture.exceeded.is_set()
            or stderr_capture.exceeded.is_set()
        )
        stdout_bytes = stdout_capture.byte_count
        stdout_sha256 = stdout_capture.sha256
        stdout_digest_complete = stdout_capture.digest_complete
        stderr_bytes = stderr_capture.byte_count
        stderr_sha256 = stderr_capture.sha256
        stderr_digest_complete = stderr_capture.digest_complete
        if not timed_out and not output_truncated and return_code == 0:
            payload_bytes = stdout_capture.payload
    except (OSError, TypeError, ValueError) as exc:
        return (
            _gate_result(
                ok=False,
                error_kind=type(exc).__name__,
                error_sha256=_digest(str(exc)),
                elapsed_seconds=round(time.monotonic() - started, 3),
                timed_out=False,
                output_truncated=False,
            ),
            b"",
        )

    raw_summary = (
        _read_json(summary_path)
        if not timed_out and not output_truncated and return_code == 0
        else {}
    )
    try:
        projected = _project_csone_replay(raw_summary)
    except Exception as exc:  # noqa: BLE001 - fail-closed projector boundary
        projected = {
            "projected_ok": False,
            "projection_error_kind": type(exc).__name__,
            "projection_error_sha256": _digest(str(exc)),
        }
    projected_ok = projected.pop("projected_ok", False)
    expected_length = projected.get("prepared_replay_byte_length")
    expected_sha = projected.get("prepared_replay_sha256")
    transport_identity_ok = bool(
        type(expected_length) is int
        and expected_length == stdout_bytes
        and expected_length == len(payload_bytes)
        and isinstance(expected_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_sha)
        and expected_sha == stdout_sha256
    )
    ok = bool(
        return_code == 0
        and not timed_out
        and not output_truncated
        and raw_summary
        and projected_ok is True
        and transport_identity_ok
    )
    gate = _gate_result(
        ok=ok,
        return_code=return_code,
        elapsed_seconds=round(time.monotonic() - started, 3),
        stdout_bytes=stdout_bytes,
        stdout_sha256=stdout_sha256,
        stdout_digest_complete=stdout_digest_complete,
        stderr_bytes=stderr_bytes,
        stderr_sha256=stderr_sha256,
        stderr_digest_complete=stderr_digest_complete,
        timed_out=timed_out,
        output_truncated=output_truncated,
        summary_present=bool(raw_summary),
        prepared_transport_identity_ok=transport_identity_ok,
        **projected,
    )
    return gate, payload_bytes if ok else b""


def _project_lab(payload: Mapping[str, Any]) -> dict[str, Any]:
    scenario_count_value = _exact_json_int(payload.get("scenario_count"), minimum=1)
    scenario_count = scenario_count_value or 0
    raw_scenarios = payload.get("scenarios")
    scenarios = raw_scenarios if isinstance(raw_scenarios, Mapping) else {}
    scenario_keys = {
        key for key in scenarios if isinstance(key, str) and key
    }
    scenario_inventory_exact = bool(
        scenario_count_value is not None
        and scenario_count == len(scenarios)
        and scenario_count == len(REQUIRED_SCENARIOS)
        and scenario_keys == REQUIRED_SCENARIOS
        and all(
            isinstance(value, Mapping)
            and value.get("scenario") == key
            and value.get("schema_version") == "local_acceptance/v1"
            and value.get("sanitized") is True
            and value.get("live_validation_performed") is False
            for key, value in scenarios.items()
        )
    )
    fingerprint = payload.get("manifest_schema_fingerprint")
    fingerprint_valid = bool(
        isinstance(fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    )
    passed = bool(
        payload.get("all_reconciled") is True
        and payload.get("sanitized") is True
        and payload.get("schema_version") == "local_acceptance/v1"
        and payload.get("live_validation_performed") is False
        and payload.get("production_accuracy_claimed") is False
        and scenario_inventory_exact
        and fingerprint_valid
    )
    return {
        "projected_ok": passed,
        "schema_version": (
            payload.get("schema_version")
            if isinstance(payload.get("schema_version"), str)
            else ""
        ),
        "sanitized": payload.get("sanitized") is True,
        "scenario_count": scenario_count,
        "scenario_inventory_exact": scenario_inventory_exact,
        "manifest_schema_fingerprint": fingerprint if fingerprint_valid else "",
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_local_http(payload: Mapping[str, Any]) -> dict[str, Any]:
    scenario_count_value = _exact_json_int(payload.get("scenario_count"), minimum=1)
    scenario_count = scenario_count_value or 0
    raw_results = payload.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    scenario_keys = [
        item.get("scenario")
        for item in results
        if isinstance(item, Mapping)
        and isinstance(item.get("scenario"), str)
        and item.get("scenario")
    ]
    result_inventory_exact = bool(
        scenario_count_value is not None
        and scenario_count == len(results)
        and scenario_count == len(REQUIRED_SCENARIOS)
        and len(scenario_keys) == len(results)
        and len(set(scenario_keys)) == len(scenario_keys)
        and set(scenario_keys) == REQUIRED_SCENARIOS
        and all(
            isinstance(item, Mapping) and item.get("ok") is True
            for item in results
        )
    )
    passed = bool(
        payload.get("all_passed") is True
        and payload.get("schema_version") == "local-acceptance-http/v1"
        and payload.get("source_mode") == SOURCE_MODE
        and payload.get("live_validation_performed") is False
        and payload.get("production_accuracy_claimed") is False
        and payload.get("scenario_inventory_complete") is True
        and payload.get("report_probes_enabled") is True
        and payload.get("sanitized") is True
        and result_inventory_exact
    )
    workspace_passed = bool(
        results
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("route_checks"), Mapping)
            and isinstance(
                item["route_checks"].get("manager_decision_workspace_preview"),
                Mapping,
            )
            and item["route_checks"]["manager_decision_workspace_preview"].get(
                "ok"
            )
            is True
            for item in results
        )
    )
    return {
        "projected_ok": passed and workspace_passed,
        "schema_version": (
            payload.get("schema_version")
            if isinstance(payload.get("schema_version"), str)
            else ""
        ),
        "sanitized": payload.get("sanitized") is True,
        "scenario_count": scenario_count,
        "scenario_inventory_complete": (
            payload.get("scenario_inventory_complete") is True
        ),
        "result_inventory_exact": result_inventory_exact,
        "report_probes_enabled": payload.get("report_probes_enabled") is True,
        "workspace_previews_passed": workspace_passed,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_decision_reports(payload: Mapping[str, Any]) -> dict[str, Any]:
    scopes: set[str] = set()
    passing_scopes: set[str] = set()
    pass_scope_counts: list[int] = []
    pass_passing_scope_counts: list[int] = []
    pass_inventories_exact: list[bool] = []
    raw_passes = payload.get("passes")
    passes = (
        raw_passes
        if isinstance(raw_passes, Sequence)
        and not isinstance(raw_passes, (str, bytes))
        else []
    )
    for pass_result in passes:
        if not isinstance(pass_result, Mapping):
            pass_scope_counts.append(0)
            pass_passing_scope_counts.append(0)
            pass_inventories_exact.append(False)
            continue
        raw_scopes = pass_result.get("scopes")
        scope_results = raw_scopes if isinstance(raw_scopes, Mapping) else {}
        current_scopes = {str(name) for name in scope_results}
        current_passing_scopes = {
            str(name)
            for name, result in scope_results.items()
            if isinstance(result, Mapping) and result.get("ok") is True
        }
        scopes.update(current_scopes)
        passing_scopes.update(current_passing_scopes)
        pass_scope_counts.append(len(current_scopes))
        pass_passing_scope_counts.append(len(current_passing_scopes))
        pass_inventories_exact.append(
            current_scopes == REQUIRED_DECISION_SCOPES
            and current_passing_scopes == REQUIRED_DECISION_SCOPES
        )
    raw_repeatability = payload.get("repeatability")
    repeatability = (
        raw_repeatability if isinstance(raw_repeatability, Mapping) else {}
    )
    mode = str(payload.get("mode_executed") or "")
    failures = payload.get("failures_requiring_review")
    failure_inventory_ok = _is_exact_empty_json_array(failures)
    passed = bool(
        payload.get("all_passed") is True
        and repeatability.get("ok") is True
        and failure_inventory_ok
        and len(passes) == 2
        and pass_inventories_exact == [True, True]
        and scopes == REQUIRED_DECISION_SCOPES
        and passing_scopes == REQUIRED_DECISION_SCOPES
    )
    return {
        "projected_ok": passed,
        "mode_executed": mode,
        "pass_count": len(passes),
        "scope_count": len(scopes),
        "passing_scope_count": len(passing_scopes),
        "scope_inventory_exact": pass_inventories_exact == [True, True],
        "scope_count_per_pass": pass_scope_counts,
        "passing_scope_count_per_pass": pass_passing_scope_counts,
        "repeatability_ok": repeatability.get("ok") is True,
        "failure_count": _json_array_count(failures),
        "failure_inventory_exact": failure_inventory_ok,
        "live_validation_performed": (
            mode == "live" and payload.get("live_validation_performed") is True
        ),
        "fixture_validation_performed": mode == "offline",
        "production_accuracy_claimed": False,
    }


def _project_ai(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_passes = payload.get("passes")
    passes = (
        raw_passes
        if isinstance(raw_passes, Sequence)
        and not isinstance(raw_passes, (str, bytes))
        else []
    )
    pass_scenario_counts: list[int] = []
    pass_passing_scenario_counts: list[int] = []
    pass_inventories_exact: list[bool] = []
    for pass_result in passes:
        if not isinstance(pass_result, Mapping):
            pass_scenario_counts.append(0)
            pass_passing_scenario_counts.append(0)
            pass_inventories_exact.append(False)
            continue
        raw_scenarios = pass_result.get("scenarios")
        scenarios = raw_scenarios if isinstance(raw_scenarios, Mapping) else {}
        scenario_keys = {str(name) for name in scenarios}
        passing_scenario_keys = {
            str(name)
            for name, result in scenarios.items()
            if isinstance(result, Mapping) and result.get("ok") is True
        }
        pass_scenario_counts.append(len(scenario_keys))
        pass_passing_scenario_counts.append(len(passing_scenario_keys))
        pass_inventories_exact.append(
            pass_result.get("ok") is True
            and scenario_keys == REQUIRED_AI_SCENARIOS
            and passing_scenario_keys == REQUIRED_AI_SCENARIOS
        )
    raw_repeatability = payload.get("repeatability")
    repeatability = (
        raw_repeatability if isinstance(raw_repeatability, Mapping) else {}
    )
    failures = payload.get("failures_requiring_review")
    failure_inventory_ok = _is_exact_empty_json_array(failures)
    passed = bool(
        payload.get("all_automated_checks_passed") is True
        and repeatability.get("ok") is True
        and failure_inventory_ok
        and len(passes) == 2
        and pass_inventories_exact == [True, True]
    )
    validation_mode = str(payload.get("validation_mode") or "")
    return {
        "projected_ok": passed,
        "validation_mode": validation_mode,
        "pass_count": len(passes),
        "expected_scenario_count": len(REQUIRED_AI_SCENARIOS),
        "scenario_inventory_exact": pass_inventories_exact == [True, True],
        "scenario_count_per_pass": pass_scenario_counts,
        "passing_scenario_count_per_pass": pass_passing_scenario_counts,
        "repeatability_ok": repeatability.get("ok") is True,
        "failure_count": _json_array_count(failures),
        "failure_inventory_exact": failure_inventory_ok,
        "live_validation_performed": (
            validation_mode == "live"
            and payload.get("live_validation_performed") is True
        ),
        "fixture_validation_performed": bool(
            validation_mode == "local_acceptance"
            and payload.get("local_validation_performed") is True
        ),
        "manual_review_complete": False,
        "release_ready": False,
        "production_accuracy_claimed": False,
    }


def _is_exact_empty_json_array(value: object) -> bool:
    """Return true only for the literal empty-list shape emitted in JSON."""

    return isinstance(value, list) and not value


def _json_array_count(value: object) -> int:
    """Return the length of a JSON array without coercing malformed shapes."""

    return len(value) if isinstance(value, list) else 0


def _exact_json_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = (1 << 63) - 1,
) -> int | None:
    """Accept only a bounded literal JSON integer, never bool/float/string."""

    if type(value) is not int or value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _is_exact_passing_r114_audit_result(value: object) -> bool:
    """Validate one bounded, aggregate-only R114 success record."""

    result = value if isinstance(value, Mapping) else {}
    stdout_bytes = _exact_json_int(
        result.get("stdout_bytes"),
        minimum=_R114_SUCCESS_MARKER_MIN_BYTES,
        maximum=_R114_AUDIT_OUTPUT_LIMIT_BYTES,
    )
    stderr_bytes = _exact_json_int(
        result.get("stderr_bytes"),
        maximum=_R114_AUDIT_OUTPUT_LIMIT_BYTES,
    )
    stdout_sha256 = result.get("stdout_sha256")
    stderr_sha256 = result.get("stderr_sha256")
    return bool(
        set(result) == REQUIRED_R114_AUDIT_RESULT_KEYS
        and result.get("ok") is True
        and result.get("critical") is False
        and _exact_json_int(result.get("returncode"), maximum=0) == 0
        and result.get("marker") == "False"
        and result.get("timed_out") is False
        and result.get("output_truncated") is False
        and result.get("reason") == "audit_passed"
        and stdout_bytes is not None
        and stderr_bytes is not None
        and isinstance(stdout_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", stdout_sha256) is not None
        and isinstance(stderr_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", stderr_sha256) is not None
    )


def _project_metamorphic(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project the Round 169 mutation gate without retaining report facts."""

    root_inventory_exact = set(payload) == REQUIRED_METAMORPHIC_SUMMARY_KEYS
    raw_checks = payload.get("checks")
    checks = raw_checks if isinstance(raw_checks, Mapping) else {}
    check_inventory_exact = set(checks) == set(REQUIRED_METAMORPHIC_CHECKS)
    projected_checks: list[dict[str, Any]] = []
    checks_exact = check_inventory_exact
    for name in REQUIRED_METAMORPHIC_CHECKS:
        raw_check = checks.get(name)
        check = raw_check if isinstance(raw_check, Mapping) else {}
        cases = _exact_json_int(check.get("cases"), minimum=1)
        digest = check.get("digest")
        digest_ok = bool(
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
        )
        check_ok = bool(
            set(check) == {"passed", "cases", "digest"}
            and check.get("passed") is True
            and cases == REQUIRED_METAMORPHIC_CASE_COUNTS[name]
            and digest_ok
        )
        checks_exact = checks_exact and check_ok
        projected_checks.append(
            {
                "name": name,
                "cases": cases if cases is not None else 0,
                "digest": digest if digest_ok else "",
                "passed": check_ok,
            }
        )

    expected_count = len(REQUIRED_METAMORPHIC_CHECKS)
    check_count = _exact_json_int(payload.get("check_count"))
    passed_count = _exact_json_int(payload.get("passed_count"))
    flags_exact = bool(
        payload.get("sanitized") is True
        and payload.get("aggregate_only") is True
        and payload.get("do_not_commit_artifacts") is True
        and payload.get("companion_http_negative_control_required") is True
        and payload.get("live_validation_performed") is False
        and payload.get("production_accuracy_claimed") is False
    )
    projected_ok = bool(
        root_inventory_exact
        and payload.get("schema") == METAMORPHIC_SUMMARY_SCHEMA
        and payload.get("all_passed") is True
        and flags_exact
        and checks_exact
        and check_count == expected_count
        and passed_count == expected_count
    )
    check_identity_sha256 = hashlib.sha256(
        json.dumps(projected_checks, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "projected_ok": projected_ok,
        "schema": (
            METAMORPHIC_SUMMARY_SCHEMA
            if payload.get("schema") == METAMORPHIC_SUMMARY_SCHEMA
            else "invalid"
        ),
        "root_inventory_exact": root_inventory_exact,
        "check_inventory_exact": check_inventory_exact,
        "check_contract_exact": checks_exact,
        "check_count": check_count if check_count is not None else 0,
        "passed_count": passed_count if passed_count is not None else 0,
        "expected_count": expected_count,
        "check_identity_sha256": check_identity_sha256,
        "companion_http_negative_control_required": (
            payload.get("companion_http_negative_control_required") is True
        ),
        "fixture_validation_performed": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _expected_report_matrix_contract(
    *,
    local_acceptance: bool,
    days: int,
    manager: str = "",
    customer_name: str,
    subscription_id: str,
) -> tuple[tuple[str, ...], int]:
    """Rebuild trusted scenario and parity-cohort inventories locally."""

    from report_iteration_loop import (
        EdgeMatrixConfig,
        build_exhaustive_option_matrix,
        build_local_acceptance_option_matrix,
        parse_matrix_blocks,
        select_matrix_scenario_keys,
    )

    bounded_days = max(min(int(days), 365), 1)
    if local_acceptance:
        matrix = build_local_acceptance_option_matrix(
            days=bounded_days,
            customer_name=customer_name,
            subscription_id=subscription_id,
        )
    else:
        edge = EdgeMatrixConfig(
            manager_name=manager,
            customer_name=customer_name,
            compact_customer_name=customer_name,
            subscription_id=subscription_id,
        )
        matrix = build_exhaustive_option_matrix(days=bounded_days, edge=edge)
    selected = tuple(
        select_matrix_scenario_keys(matrix, parse_matrix_blocks(REQUIRED_MATRIX_BLOCKS))
    )
    cohorts = {
        matrix[key].source_parity_cohort
        for key in selected
        if matrix[key].source_parity_cohort
    }
    return selected, len(cohorts)


def _project_matrix(
    payload: Mapping[str, Any],
    *,
    expected_scenario_keys: Sequence[str],
    expected_scenario_count: int,
    expected_source_parity_cohort_count: int,
    expected_max_freshness_skew_seconds: int = 0,
    require_all_blocks: bool = True,
) -> dict[str, Any]:
    trusted_keys = list(expected_scenario_keys)
    trusted_keys_ok = bool(
        type(expected_scenario_count) is int
        and expected_scenario_count > 0
        and len(trusted_keys) == expected_scenario_count
        and trusted_keys
        and all(isinstance(item, str) and item for item in trusted_keys)
        and len(set(trusted_keys)) == len(trusted_keys)
    )
    raw_requested_keys = payload.get("scenario_keys_requested")
    requested_keys = (
        list(raw_requested_keys) if isinstance(raw_requested_keys, list) else []
    )
    raw_expected_keys = payload.get("scenario_keys_expected")
    expected_keys = (
        list(raw_expected_keys) if isinstance(raw_expected_keys, list) else []
    )
    raw_completed_keys = payload.get("scenario_keys_completed")
    completed_keys = (
        list(raw_completed_keys) if isinstance(raw_completed_keys, list) else []
    )
    key_shapes_ok = bool(
        trusted_keys_ok
        and expected_keys
        and all(isinstance(item, str) and item for item in requested_keys)
        and all(isinstance(item, str) and item for item in expected_keys)
        and all(isinstance(item, str) and item for item in completed_keys)
        and len(set(requested_keys)) == len(requested_keys)
        and len(set(expected_keys)) == len(expected_keys)
        and len(set(completed_keys)) == len(completed_keys)
    )
    expected_value = _exact_json_int(payload.get("scenario_count_expected"), minimum=1)
    completed_value = _exact_json_int(payload.get("scenario_count_completed"))
    scenarios_completed_value = _exact_json_int(payload.get("scenarios_completed"))
    expected = expected_value if expected_value is not None else 0
    completed = completed_value if completed_value is not None else 0
    requested_blocks = {
        item.split("_", 1)[0].upper()
        for item in expected_keys
        if "_" in item
    }
    all_blocks_requested = REQUIRED_MATRIX_BLOCK_SET <= requested_blocks
    raw_results = payload.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    passed_results = [
        item
        for item in results
        if isinstance(item, Mapping) and item.get("all_passed") is True
    ]
    passed = len(passed_results)
    result_scenario_keys = [
        item.get("scenario") for item in passed_results
    ]
    result_inventory_ok = bool(
        len(results) == expected
        and len(passed_results) == expected
        and all(isinstance(item, str) and item for item in result_scenario_keys)
        and result_scenario_keys == trusted_keys
    )
    consistency = payload.get("cross_report_source_consistency")
    consistency_map = consistency if isinstance(consistency, Mapping) else {}
    consistency_family_sets = consistency_map.get("report_family_sets_compared")
    consistency_comparisons_value = (
        _exact_json_int(consistency_map.get("comparisons"), minimum=1)
        if consistency_map
        else None
    )
    consistency_comparisons_expected_value = (
        _exact_json_int(consistency_map.get("comparisons_expected"), minimum=1)
        if consistency_map
        else None
    )
    consistency_group_count_value = (
        _exact_json_int(
            consistency_map.get("required_family_set_group_count"),
            minimum=1,
        )
        if consistency_map
        else None
    )
    consistency_expected_group_count_value = (
        _exact_json_int(
            consistency_map.get("required_family_set_group_count_expected"),
            minimum=1,
        )
        if consistency_map
        else None
    )
    consistency_groups_discovered_value = (
        _exact_json_int(consistency_map.get("groups_discovered"), minimum=1)
        if consistency_map
        else None
    )
    consistency_groups_evaluated_value = (
        _exact_json_int(consistency_map.get("groups_evaluated"), minimum=1)
        if consistency_map
        else None
    )
    ignored_non_parity_value = (
        _exact_json_int(consistency_map.get("ignored_non_parity_scenario_count"))
        if consistency_map
        else None
    )
    max_freshness_skew_value = (
        _exact_json_int(consistency_map.get("max_freshness_skew_seconds"))
        if consistency_map
        else None
    )
    consistency_comparisons = consistency_comparisons_value or 0
    consistency_comparisons_expected = consistency_comparisons_expected_value or 0
    consistency_group_count = consistency_group_count_value or 0
    consistency_expected_group_count = consistency_expected_group_count_value or 0
    consistency_groups_discovered = consistency_groups_discovered_value or 0
    consistency_groups_evaluated = consistency_groups_evaluated_value or 0
    ignored_non_parity = ignored_non_parity_value or 0
    exact_family_set = list(REQUIRED_SOURCE_PARITY_FAMILIES)
    exact_required_sheets = list(REQUIRED_SOURCE_PARITY_SHEETS)
    exact_projected_fields = list(REQUIRED_SOURCE_PARITY_FIELDS)
    negative_inventory_fields = (
        "cohort_declaration_errors",
        "cohort_membership_errors",
        "incomplete_equivalent_scope_groups",
        "duplicate_family_groups",
        "scope_mismatches",
        "identity_quality_errors",
        "mismatches",
        "freshness_mismatches",
        "read_errors",
    )
    negative_inventories_ok = all(
        _is_exact_empty_json_array(consistency_map.get(field))
        for field in negative_inventory_fields
    )
    trusted_group_count_ok = bool(
        type(expected_source_parity_cohort_count) is int
        and expected_source_parity_cohort_count > 0
    )
    expected_ignored_non_parity = expected - expected_source_parity_cohort_count * len(
        REQUIRED_SOURCE_PARITY_FAMILIES
    )
    consistency_ok = bool(
        consistency_map
        and set(consistency_map) == REQUIRED_SOURCE_PARITY_SUMMARY_KEYS
        and len(REQUIRED_SOURCE_PARITY_SHEETS) == 17
        and len(REQUIRED_SOURCE_PARITY_FIELDS) == 10
        and consistency_map.get("ok") is True
        and consistency_map.get("comparison_requirement_met") is True
        and consistency_map.get("required_report_families") == exact_family_set
        and consistency_map.get("required_sheets") == exact_required_sheets
        and consistency_map.get("projected_fields") == exact_projected_fields
        and consistency_group_count_value is not None
        and consistency_expected_group_count_value is not None
        and consistency_groups_discovered_value is not None
        and consistency_groups_evaluated_value is not None
        and consistency_comparisons_value is not None
        and consistency_comparisons_expected_value is not None
        and ignored_non_parity_value is not None
        and max_freshness_skew_value is not None
        and trusted_group_count_ok
        and consistency_group_count == expected_source_parity_cohort_count
        and consistency_expected_group_count == expected_source_parity_cohort_count
        and consistency_groups_discovered == expected_source_parity_cohort_count
        and consistency_groups_evaluated == expected_source_parity_cohort_count
        and consistency_comparisons == consistency_comparisons_expected
        and consistency_comparisons
        == consistency_group_count * len(REQUIRED_SOURCE_PARITY_SHEETS)
        and expected_ignored_non_parity >= 0
        and ignored_non_parity == expected_ignored_non_parity
        and max_freshness_skew_value == expected_max_freshness_skew_seconds
        and isinstance(consistency_family_sets, list)
        and consistency_family_sets == [exact_family_set]
        and consistency_map.get("privacy")
        == "counts_and_sha256_only_no_source_values"
        and negative_inventories_ok
    )
    r114_expected_value = _exact_json_int(
        payload.get("r114_audit_scenario_count_expected"),
        minimum=1,
    )
    r114_completed_value = _exact_json_int(
        payload.get("r114_audit_scenario_count_completed"),
    )
    r114_expected = r114_expected_value or 0
    r114_completed = r114_completed_value if r114_completed_value is not None else 0
    raw_r114_expected_keys = payload.get("r114_audit_scenario_keys_expected")
    r114_expected_keys = (
        list(raw_r114_expected_keys)
        if isinstance(raw_r114_expected_keys, list)
        else []
    )
    raw_r114_completed_keys = payload.get("r114_audit_scenario_keys_completed")
    r114_completed_keys = (
        list(raw_r114_completed_keys)
        if isinstance(raw_r114_completed_keys, list)
        else []
    )
    r114_key_inventory_ok = bool(
        r114_expected_keys == trusted_keys
        and r114_completed_keys == trusted_keys
    )
    raw_r114_audit = payload.get("r114_audit")
    r114_audit = raw_r114_audit if isinstance(raw_r114_audit, Mapping) else {}
    r114_result_key_inventory_ok = bool(
        isinstance(raw_r114_audit, Mapping)
        and list(raw_r114_audit) == trusted_keys
    )
    r114_result_contract_ok = bool(
        r114_result_key_inventory_ok
        and all(
            _is_exact_passing_r114_audit_result(r114_audit.get(key))
            for key in trusted_keys
        )
    )
    r114_key_inventory_ok = bool(
        r114_key_inventory_ok and r114_result_key_inventory_ok
    )
    r114_ok = bool(
        payload.get("r114_audit_inventory_exact") is True
        and r114_expected_value is not None
        and r114_completed_value is not None
        and r114_expected == expected
        and r114_completed == expected
        and r114_key_inventory_ok
        and r114_result_contract_ok
        and _is_exact_empty_json_array(payload.get("r114_critical_scenarios"))
        and payload.get("r114_audit_skipped") is False
    )
    inventory_ok = bool(
        payload.get("scenario_inventory_exact") is True
        and expected_value is not None
        and completed_value is not None
        and scenarios_completed_value is not None
        and key_shapes_ok
        and requested_keys == trusted_keys
        and expected_keys == trusted_keys
        and expected == len(trusted_keys)
        and completed == expected
        and scenarios_completed_value == expected
        and completed_keys == trusted_keys
        and _is_exact_empty_json_array(payload.get("scenario_keys_missing"))
        and _is_exact_empty_json_array(payload.get("scenario_keys_unexpected"))
        and _is_exact_empty_json_array(
            payload.get("scenario_keys_completed_duplicate")
        )
    )
    return {
        "projected_ok": bool(
            payload.get("all_passed") is True
            and inventory_ok
            and (all_blocks_requested or require_all_blocks is False)
            and result_inventory_ok
            and consistency_ok
            and r114_ok
        ),
        "scenario_count": expected,
        "expected_count": expected,
        "completed_count": completed,
        "passed_count": passed,
        "failed_count": max(completed - passed, 0),
        "scenario_inventory_complete": inventory_ok,
        "trusted_scenario_inventory_complete": trusted_keys_ok and inventory_ok,
        "result_inventory_complete": result_inventory_ok,
        "all_report_blocks_requested": all_blocks_requested,
        "source_consistency_ok": consistency_ok,
        "source_consistency_comparison_count": (
            consistency_comparisons
        ),
        "source_consistency_expected_comparison_count": (
            consistency_comparisons_expected
        ),
        "source_consistency_required_report_families": exact_family_set,
        "source_consistency_required_sheets": exact_required_sheets,
        "source_consistency_projected_fields": exact_projected_fields,
        "source_consistency_required_family_set_group_count": (
            consistency_group_count
        ),
        "source_consistency_expected_family_set_group_count": (
            consistency_expected_group_count
        ),
        "source_consistency_groups_discovered": consistency_groups_discovered,
        "source_consistency_groups_evaluated": consistency_groups_evaluated,
        "source_consistency_ignored_non_parity_scenario_count": (
            ignored_non_parity
        ),
        "source_consistency_max_freshness_skew_seconds": (
            max_freshness_skew_value if max_freshness_skew_value is not None else -1
        ),
        "source_consistency_report_family_sets_compared": consistency_family_sets,
        "source_consistency_mismatch_count": (
            _json_array_count(consistency_map.get("mismatches"))
        ),
        "source_freshness_mismatch_count": (
            _json_array_count(consistency_map.get("freshness_mismatches"))
        ),
        "source_consistency_read_error_count": (
            _json_array_count(consistency_map.get("read_errors"))
        ),
        "source_consistency_contract_error_count": sum(
            _json_array_count(consistency_map.get(field))
            for field in negative_inventory_fields[:-3]
        ),
        "r114_audit_ok": r114_ok,
        "r114_audit_key_inventory_complete": r114_key_inventory_ok,
        "r114_audit_result_contract_complete": r114_result_contract_ok,
        "r114_audit_completed_count": r114_completed,
        "production_accuracy_claimed": False,
    }


def _project_multi_manager_matrix(payload: Mapping[str, Any]) -> dict[str, Any]:
    from report_iteration_loop import (
        build_local_acceptance_multi_manager_matrix,
        parse_matrix_blocks,
        select_matrix_scenario_keys,
    )

    matrix = build_local_acceptance_multi_manager_matrix()
    expected_keys = tuple(
        select_matrix_scenario_keys(
            matrix,
            parse_matrix_blocks(REQUIRED_MATRIX_BLOCKS),
        )
    )
    expected_cohorts = len(
        {
            matrix[key].source_parity_cohort
            for key in expected_keys
            if matrix[key].source_parity_cohort
        }
    )
    projection = _project_matrix(
        payload,
        expected_scenario_keys=expected_keys,
        expected_scenario_count=24,
        expected_source_parity_cohort_count=3,
        expected_max_freshness_skew_seconds=0,
        require_all_blocks=False,
    )
    both_named = all(
        any(token in key for key in expected_keys)
        for token in ("primary_manager", "secondary_manager")
    )
    aggregate = any("all_managers" in key for key in expected_keys)
    projection["projected_ok"] = bool(
        projection.get("projected_ok") is True
        and both_named
        and aggregate
        and expected_cohorts == 3
    )
    projection["both_named_managers_exercised"] = both_named
    projection["aggregate_manager_exercised"] = aggregate
    return projection


def _project_source_contracts(payload: Mapping[str, Any]) -> dict[str, Any]:
    checks = payload.get("checks") if isinstance(payload.get("checks"), Mapping) else {}
    check_inventory_exact = set(checks) == REQUIRED_SOURCE_CONTRACT_CHECKS
    raw_trace = payload.get("query_trace")
    query_trace = raw_trace if isinstance(raw_trace, Mapping) else {}
    query_count_value = _exact_json_int(query_trace.get("query_count"), minimum=10)
    raw_families = query_trace.get("families")
    families = raw_families if isinstance(raw_families, Mapping) else {}
    family_counts_valid = bool(
        families
        and all(
            isinstance(name, str)
            and name
            and _exact_json_int(count, minimum=1) is not None
            for name, count in families.items()
        )
    )
    raw_fingerprints = query_trace.get("query_fingerprints")
    fingerprints = raw_fingerprints if isinstance(raw_fingerprints, list) else []
    fingerprints_valid = bool(
        fingerprints
        and len(set(fingerprints)) == len(fingerprints)
        and all(
            isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in fingerprints
        )
    )
    query_trace_valid = bool(
        query_count_value is not None
        and query_trace.get("source_mode") == SOURCE_MODE
        and query_trace.get("live_validation_performed") is False
        and query_trace.get("all_data_queries_parameterized") is True
        and family_counts_valid
        and sum(families.values()) == query_count_value
        and fingerprints_valid
    )
    passed = bool(
        payload.get("all_passed") is True
        and payload.get("schema_version") == "local-snowflake-source-contracts/v1"
        and payload.get("sanitized") is True
        and payload.get("source_mode") == SOURCE_MODE
        and payload.get("scenario") == "multi_manager"
        and payload.get("live_validation_performed") is False
        and payload.get("production_accuracy_claimed") is False
        and check_inventory_exact
        and all(value is True for value in checks.values())
        and query_trace_valid
    )
    return {
        "projected_ok": passed,
        "scenario": str(payload.get("scenario") or ""),
        "check_count": len(checks),
        "check_inventory_exact": check_inventory_exact,
        "query_count": query_count_value or 0,
        "query_trace_valid": query_trace_valid,
        "parameter_binding_ok": (
            checks.get("parameter_binding_and_family_coverage") is True
        ),
        "secondary_attribution_ok": checks.get("secondary_attribution") is True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_snowflake_capabilities(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed_value = _exact_json_int(payload.get("allowed_table_count"), minimum=1)
    accessible_value = _exact_json_int(payload.get("accessible_table_count"))
    allowed = allowed_value or 0
    accessible = accessible_value if accessible_value is not None else 0
    raw_tables = payload.get("tables")
    tables = raw_tables if isinstance(raw_tables, list) else []
    raw_blocked = payload.get("policy_blocked_tables")
    blocked = raw_blocked if isinstance(raw_blocked, list) else []
    table_names = [
        str(item.get("table") or "")
        for item in tables
        if isinstance(item, Mapping)
    ]
    blocked_table_names = [
        str(item.get("table") or "")
        for item in blocked
        if isinstance(item, Mapping)
    ]
    table_inventory_ok = bool(
        allowed_value is not None
        and accessible_value is not None
        and len(tables) == allowed
        and accessible == allowed
        and len(table_names) == allowed
        and all(table_names)
        and len(set(table_names)) == allowed
        and all(
            isinstance(item, Mapping)
            and item.get("access_state") == "simulated_available"
            for item in tables
        )
    )
    blocked_inventory_ok = bool(
        blocked
        and len(blocked_table_names) == len(blocked)
        and all(blocked_table_names)
        and len(set(blocked_table_names)) == len(blocked_table_names)
        and all(
            isinstance(item, Mapping)
            and item.get("probe_attempted") is False
            and item.get("state") == "blocked_by_policy"
            for item in blocked
        )
    )
    passed = bool(
        payload.get("all_passed") is True
        and payload.get("mode") == "local"
        and payload.get("row_values_queried") is False
        and payload.get("live_validation_performed") is False
        and payload.get("all_allowed_tables_accessible") is True
        and table_inventory_ok
        and blocked_inventory_ok
    )
    return {
        "projected_ok": passed,
        "allowed_table_count": allowed,
        "accessible_table_count": accessible,
        "row_values_queried": payload.get("row_values_queried") is True,
        "blocked_table_count": len(blocked),
        "table_inventory_ok": table_inventory_ok,
        "blocked_table_inventory_ok": blocked_inventory_ok,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_csone_corpus(payload: Mapping[str, Any]) -> dict[str, Any]:
    workbook_count_value = _exact_json_int(payload.get("workbook_count"), minimum=1)
    row_count_value = _exact_json_int(payload.get("total_profiled_rows"), minimum=1)
    distinct_schema_count_value = _exact_json_int(
        payload.get("distinct_schema_count"),
        minimum=1,
    )
    dominant_schema_count_value = _exact_json_int(
        payload.get("dominant_schema_workbook_count"),
        minimum=1,
    )
    workbook_count = workbook_count_value or 0
    row_count = row_count_value or 0
    distinct_schema_count = distinct_schema_count_value or 0
    dominant_schema_count = dominant_schema_count_value or 0
    raw_schema_counts = payload.get("schema_fingerprint_counts")
    schema_counts = (
        raw_schema_counts if isinstance(raw_schema_counts, Mapping) else {}
    )
    schema_count_values = [
        _exact_json_int(value, minimum=1) for value in schema_counts.values()
    ]
    schema_inventory_ok = bool(
        workbook_count_value is not None
        and distinct_schema_count_value is not None
        and dominant_schema_count_value is not None
        and len(schema_counts) == distinct_schema_count
        and schema_count_values
        and all(value is not None for value in schema_count_values)
        and sum(value or 0 for value in schema_count_values) == workbook_count
        and max(value or 0 for value in schema_count_values)
        == dominant_schema_count
    )
    passed = bool(
        payload.get("sanitized") is True
        and payload.get("source_rows_exported") is False
        and payload.get("source_values_exported") is False
        and payload.get("live_snowflake_validation_performed") is False
        and row_count_value is not None
        and schema_inventory_ok
    )
    return {
        "projected_ok": passed,
        "workbook_count": workbook_count,
        "profiled_row_count": row_count,
        "distinct_schema_count": distinct_schema_count,
        "dominant_schema_workbook_count": dominant_schema_count,
        "schema_inventory_ok": schema_inventory_ok,
        "source_rows_exported": payload.get("source_rows_exported") is True,
        "source_values_exported": payload.get("source_values_exported") is True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }


def _project_csone_replay(payload: Mapping[str, Any]) -> dict[str, Any]:
    root_contract_ok = bool(
        set(payload) == REQUIRED_CSONE_REPLAY_SUMMARY_KEYS
        and payload.get("schema_version") == "csone-corpus-replay/v4"
        and payload.get("sanitized") is True
        and payload.get("do_not_commit") is True
        and payload.get("source_mode") == SOURCE_MODE
        and payload.get("live_snowflake_validation_performed") is False
        and payload.get("source_rows_exported") is False
        and payload.get("source_values_exported") is False
        and payload.get("raw_values_retained") is False
        and payload.get("production_accuracy_claimed") is False
    )
    raw_loader = payload.get("loader_contract")
    loader = raw_loader if isinstance(raw_loader, Mapping) else {}
    raw_replay = payload.get("replay")
    replay = raw_replay if isinstance(raw_replay, Mapping) else {}
    raw_coverage = replay.get("corpus_coverage")
    coverage = raw_coverage if isinstance(raw_coverage, Mapping) else {}
    representative_count_value = _exact_json_int(
        loader.get("representative_workbook_count"),
        minimum=1,
    )
    raw_loader_results = loader.get("results")
    loader_results = (
        raw_loader_results if isinstance(raw_loader_results, list) else []
    )
    loader_inventory_ok = bool(
        representative_count_value is not None
        and len(loader_results) == representative_count_value
        and _exact_json_int(
            loader.get("date_parse_failure_count"),
            minimum=0,
            maximum=0,
        )
        == 0
        and all(
            isinstance(item, Mapping)
            and _exact_json_int(item.get("row_count"), minimum=1) is not None
            and _exact_json_int(item.get("column_count"), minimum=1) is not None
            and _exact_json_int(item.get("excluded_non_record_rows")) is not None
            and _exact_json_int(item.get("footer_like_rows_remaining")) == 0
            and _exact_json_int(
                item.get("date_parse_failure_count"),
                minimum=0,
                maximum=0,
            )
            == 0
            for item in loader_results
        )
    )
    replay_row_count_value = _exact_json_int(replay.get("row_count"), minimum=1)
    source_row_count_value = _exact_json_int(
        replay.get("source_row_count"),
        minimum=1,
    )
    excluded_row_count_value = _exact_json_int(
        replay.get("excluded_non_record_rows"),
    )
    replay_counts_ok = bool(
        replay_row_count_value is not None
        and source_row_count_value is not None
        and excluded_row_count_value is not None
        and replay_row_count_value <= source_row_count_value
    )
    raw_status_coverage = replay.get("status_coverage")
    status_coverage = (
        raw_status_coverage
        if isinstance(raw_status_coverage, Mapping)
        else {}
    )
    exact_status_coverage_keys = {
        "schema_version",
        "row_count",
        "raw_missing_count",
        "raw_missing_ratio",
        "normalized_unknown_count",
        "normalized_unknown_ratio",
        "populated_unclassified_count",
        "classified_count",
        "source_state",
    }
    raw_missing_count = _exact_json_int(
        status_coverage.get("raw_missing_count")
    )
    normalized_unknown_count = _exact_json_int(
        status_coverage.get("normalized_unknown_count")
    )
    populated_unclassified_count = _exact_json_int(
        status_coverage.get("populated_unclassified_count")
    )
    classified_count = _exact_json_int(status_coverage.get("classified_count"))
    status_row_count = _exact_json_int(
        status_coverage.get("row_count"),
        minimum=1,
    )
    raw_missing_ratio = status_coverage.get("raw_missing_ratio")
    normalized_unknown_ratio = status_coverage.get("normalized_unknown_ratio")
    status_coverage_sha256 = replay.get("status_coverage_sha256")
    try:
        observed_status_coverage_sha256 = hashlib.sha256(
            json.dumps(
                status_coverage,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        observed_status_coverage_sha256 = ""
    status_coverage_contract_ok = bool(
        set(status_coverage) == exact_status_coverage_keys
        and status_coverage.get("schema_version") == "csone-status-coverage/v1"
        and status_row_count is not None
        and status_row_count == replay_row_count_value
        and raw_missing_count is not None
        and normalized_unknown_count is not None
        and populated_unclassified_count is not None
        and classified_count is not None
        and 0 <= raw_missing_count <= normalized_unknown_count <= status_row_count
        and populated_unclassified_count
        == normalized_unknown_count - raw_missing_count
        and classified_count == status_row_count - normalized_unknown_count
        and type(raw_missing_ratio) is float
        and raw_missing_ratio == round(raw_missing_count / status_row_count, 6)
        and type(normalized_unknown_ratio) is float
        and normalized_unknown_ratio
        == round(normalized_unknown_count / status_row_count, 6)
        and status_coverage.get("source_state")
        == ("partial" if normalized_unknown_count else "available")
        and _exact_json_int(replay.get("missing_status_rows"))
        == normalized_unknown_count
        and _exact_json_int(replay.get("raw_missing_status_rows"))
        == raw_missing_count
        and isinstance(status_coverage_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", status_coverage_sha256) is not None
        and observed_status_coverage_sha256 == status_coverage_sha256
    )
    raw_privacy_contract = replay.get("privacy_contract")
    privacy_contract = (
        raw_privacy_contract
        if isinstance(raw_privacy_contract, Mapping)
        else {}
    )
    privacy_row_count = _exact_json_int(
        privacy_contract.get("row_count"),
        minimum=1,
    )
    privacy_column_count = _exact_json_int(
        privacy_contract.get("column_count"),
        minimum=1,
    )
    privacy_counts_ok = bool(
        privacy_contract.get("validated") is True
        and privacy_contract.get("raw_values_retained") is False
        and privacy_row_count == replay_row_count_value
        and privacy_column_count is not None
    )
    raw_prepared = payload.get("prepared_replay")
    prepared = raw_prepared if isinstance(raw_prepared, Mapping) else {}
    exact_prepared_keys = {
        "payload_sha256",
        "frame_sha256",
        "coverage_sha256",
        "source_snapshot_sha256",
        "status_coverage_sha256",
        "byte_length",
        "instrumentation",
    }
    sha_fields_ok = all(
        isinstance(prepared.get(key), str)
        and re.fullmatch(r"[0-9a-f]{64}", prepared[key]) is not None
        for key in (
            "payload_sha256",
            "frame_sha256",
            "coverage_sha256",
            "source_snapshot_sha256",
            "status_coverage_sha256",
        )
    )
    byte_length = _exact_json_int(
        prepared.get("byte_length"), minimum=1, maximum=MAX_PREPARED_REPLAY_BYTES
    )
    raw_instrumentation = prepared.get("instrumentation")
    instrumentation = (
        raw_instrumentation if isinstance(raw_instrumentation, Mapping) else {}
    )
    exact_instrumentation_keys = {
        "preparation_count",
        "profile_loader_calls",
        "selected_reload_calls",
        "total_loader_calls",
    }
    preparation_count = _exact_json_int(
        instrumentation.get("preparation_count"), minimum=1, maximum=1
    )
    profile_calls = _exact_json_int(
        instrumentation.get("profile_loader_calls"), minimum=1
    )
    reload_calls = _exact_json_int(
        instrumentation.get("selected_reload_calls"), minimum=1
    )
    total_calls = _exact_json_int(
        instrumentation.get("total_loader_calls"), minimum=2
    )
    candidate_count = _exact_json_int(
        loader.get("candidate_workbook_count"), minimum=1
    )
    prepared_contract_ok = bool(
        set(prepared) == exact_prepared_keys
        and sha_fields_ok
        and byte_length is not None
        and set(instrumentation) == exact_instrumentation_keys
        and preparation_count == 1
        and profile_calls == candidate_count
        and reload_calls == representative_count_value
        and total_calls is not None
        and total_calls == (profile_calls or 0) + (reload_calls or 0)
        and prepared.get("frame_sha256") == replay.get("frame_sha256")
        and prepared.get("status_coverage_sha256")
        == replay.get("status_coverage_sha256")
    )
    passed = bool(
        root_contract_ok
        and payload.get("all_passed") is True
        and loader.get("all_nonempty") is True
        and loader.get("no_footer_rows_remaining") is True
        and loader.get("consistent_schema") is True
        and loader.get("breadth_ok") is True
        and loader_inventory_ok
        and coverage.get("breadth_ok") is True
        and replay.get("pseudonym_contract_ok") is True
        and replay_counts_ok
        and privacy_counts_ok
        and status_coverage_contract_ok
        and prepared_contract_ok
    )
    return {
        "projected_ok": passed,
        "root_contract_ok": root_contract_ok,
        "schema_version": (
            "csone-corpus-replay/v4"
            if payload.get("schema_version") == "csone-corpus-replay/v4"
            else "invalid"
        ),
        "representative_workbook_count": representative_count_value or 0,
        "replay_row_count": replay_row_count_value or 0,
        "source_row_count": source_row_count_value or 0,
        "excluded_non_record_rows": excluded_row_count_value or 0,
        "pseudonym_contract_ok": replay.get("pseudonym_contract_ok") is True,
        "loader_inventory_ok": loader_inventory_ok,
        "date_parse_failure_count": (
            loader.get("date_parse_failure_count")
            if _exact_json_int(
                loader.get("date_parse_failure_count"),
                minimum=0,
                maximum=0,
            )
            == 0
            else -1
        ),
        "replay_count_contract_ok": replay_counts_ok,
        "privacy_count_contract_ok": privacy_counts_ok,
        "status_coverage_contract_ok": status_coverage_contract_ok,
        "raw_missing_status_rows": raw_missing_count or 0,
        "normalized_unknown_status_rows": normalized_unknown_count or 0,
        "raw_missing_status_ratio": (
            raw_missing_ratio if type(raw_missing_ratio) is float else -1.0
        ),
        "normalized_unknown_status_ratio": (
            normalized_unknown_ratio
            if type(normalized_unknown_ratio) is float
            else -1.0
        ),
        "tac_source_state": str(status_coverage.get("source_state") or "invalid"),
        "loader_breadth_ok": loader.get("breadth_ok") is True,
        "replay_breadth_ok": coverage.get("breadth_ok") is True,
        "prepared_replay_contract_ok": prepared_contract_ok,
        "prepared_replay_byte_length": byte_length or 0,
        "prepared_replay_sha256": (
            prepared.get("payload_sha256") if sha_fields_ok else ""
        ),
        "prepared_frame_sha256": (
            prepared.get("frame_sha256") if sha_fields_ok else ""
        ),
        "prepared_coverage_sha256": (
            prepared.get("coverage_sha256") if sha_fields_ok else ""
        ),
        "prepared_source_snapshot_sha256": (
            prepared.get("source_snapshot_sha256") if sha_fields_ok else ""
        ),
        "prepared_status_coverage_sha256": (
            prepared.get("status_coverage_sha256") if sha_fields_ok else ""
        ),
        "profile_loader_calls": profile_calls or 0,
        "selected_reload_calls": reload_calls or 0,
        "total_loader_calls": total_calls or 0,
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
        and connectivity.get("ok") is True
    )
    live_marker_valid = bool(
        "live_validation_performed" not in connectivity
        or connectivity.get("live_validation_performed") is True
    )
    live_runtime = bool(
        connectivity_ok
        and not fixture_runtime
        and live_marker_valid
    )
    mode_ok = fixture_runtime if expect_fixture else live_runtime
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
            and payload.get("ok") is True
            and preview.get("schema") == WORKSPACE_SCHEMA
            and preview.get("report_type") == params["report_type"]
            and preview.get("scope_type") == params["scope_type"]
            and (
                fixture_claim_ok
                if expect_fixture
                else (
                    live_claim_ok
                    and preview.get("live_validation_performed") is True
                )
            )
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
        and history_payload.get("ok") is True
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
                and scoped_payload.get("ok") is True
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
        and item.get("excel_available") is True
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
            and report_payload.get("ok") is True
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
            and compare_payload.get("ok") is True
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
    canonical_ai_stream_done_ok = False
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
            and sync_payload.get("ok") is True
            and sync_payload.get("mode") == "grounded"
            and binding_matches(sync_payload.get("scope_context"))
            and canonical_ai_citation_count
            and sync_payload.get("fallback_available") is False
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
            done_events = [
                payload for name, payload in stream_events if name == "done"
            ]
            canonical_ai_stream_done_ok = bool(
                len(done_events) == 1
                and bool(done_events[0])
                and (
                    "ok" not in done_events[0]
                    or done_events[0].get("ok") is True
                )
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
            and canonical_ai_stream_done_ok
            and stream_payload.get("ok") is True
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
    live_performed = bool(not expect_fixture and mode_ok and live_runtime)
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
    stdout_capture = _BoundedDigestWriter(_COMMAND_OUTPUT_LIMIT_BYTES)
    stderr_capture = _BoundedDigestWriter(_COMMAND_OUTPUT_LIMIT_BYTES)
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
    passed = sum(1 for item in results if item.passed is True)
    canonical_checks = [
        predicate
        for item in results
        for predicate in item.predicate_results
        if predicate.get("type") == "must_match_canonical_metric"
    ]
    canonical_passed = sum(
        1 for predicate in canonical_checks if predicate.get("passed") is True
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
                    "passed": item.passed is True,
                    "predicate_passes": [
                        predicate.get("passed") is True
                        for predicate in item.predicate_results
                    ],
                }
                for item in results
            ]
        ),
        elapsed_seconds=round(time.monotonic() - started, 3),
        stdout_bytes=stdout_capture.byte_count,
        stdout_sha256=stdout_capture.sha256,
        stderr_bytes=stderr_capture.byte_count,
        stderr_sha256=stderr_capture.sha256,
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
                and payload.get("ok") is True
                and payload.get("mode") == SOURCE_MODE
                and payload.get("live_validation_performed") is False
            ):
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise TimeoutError("guarded local runtime did not become ready")


def _probe_prepared_runtime_identity(
    base_url: str,
    prepared: PreparedCsoneReplay,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Require aggregate identity emitted by the child after deserialize/apply."""

    expected = {
        "prepared_replay_sha256": prepared.payload_sha256,
        "prepared_frame_sha256": prepared.frame_sha256,
        "prepared_coverage_sha256": prepared.coverage_sha256,
        "prepared_source_snapshot_sha256": prepared.source_snapshot_sha256,
    }
    # This is a single loopback diagnostics request after readiness has already
    # succeeded. Keep a literal, scanner-verifiable network bound instead of
    # inheriting a potentially much larger end-to-end acceptance timeout.
    try:
        response = requests.get(
            base_url + "/api/diag/connectivity", timeout=10.0
        )
        payload = _response_json(response)
    except requests.RequestException:
        payload = {}
        response = None
    observed = {
        key: (
            payload.get(key)
            if isinstance(payload.get(key), str)
            and re.fullmatch(r"[0-9a-f]{64}", payload[key]) is not None
            else ""
        )
        for key in expected
    }
    exact_hashes = bool(
        response is not None
        and response.status_code == 200
        and payload.get("ok") is True
        and payload.get("mode") == SOURCE_MODE
        and payload.get("live_validation_performed") is False
        and all(
            observed[key] == value
            for key, value in expected.items()
        )
    )
    consumer_calls = payload.get("consumer_loader_calls")
    return _gate_result(
        ok=bool(
            exact_hashes
            and type(consumer_calls) is int
            and consumer_calls == 0
        ),
        **observed,
        expected_identity_sha256=_digest(expected),
        observed_identity_sha256=(
            _digest(observed) if all(observed.values()) else ""
        ),
        consumer_loader_calls=(consumer_calls if type(consumer_calls) is int else -1),
        exact_hashes=exact_hashes,
        live_validation_performed=False,
        production_accuracy_claimed=False,
    )


def _prepared_replay_identity_gate(
    producer: Mapping[str, Any],
    healthy: Mapping[str, Any],
    multi: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile one producer and both pre/post-validated consumers."""

    identity_fields = (
        "prepared_replay_sha256",
        "prepared_frame_sha256",
        "prepared_coverage_sha256",
        "prepared_source_snapshot_sha256",
    )
    hashes_match = all(
        isinstance(producer.get(field), str)
        and re.fullmatch(r"[0-9a-f]{64}", producer[field]) is not None
        and producer.get(field) == healthy.get(field) == multi.get(field)
        for field in identity_fields
    )
    healthy_calls = healthy.get("consumer_loader_calls")
    multi_calls = multi.get("consumer_loader_calls")
    healthy_probe_count = healthy.get("identity_probe_count")
    multi_probe_count = multi.get("identity_probe_count")
    healthy_probe_passed = healthy.get("identity_probe_passed_count")
    multi_probe_passed = multi.get("identity_probe_passed_count")
    probe_count = (
        healthy_probe_count + multi_probe_count
        if type(healthy_probe_count) is int and type(multi_probe_count) is int
        else -1
    )
    probe_passed_count = (
        healthy_probe_passed + multi_probe_passed
        if type(healthy_probe_passed) is int
        and type(multi_probe_passed) is int
        else -1
    )
    total_consumer_loader_calls = (
        healthy_calls + multi_calls
        if type(healthy_calls) is int and type(multi_calls) is int
        else -1
    )
    return _gate_result(
        ok=bool(
            hashes_match
            and healthy.get("ok") is True
            and multi.get("ok") is True
            and healthy.get("pre_report_identity_ok") is True
            and healthy.get("post_report_identity_ok") is True
            and multi.get("pre_report_identity_ok") is True
            and multi.get("post_report_identity_ok") is True
            and type(healthy_calls) is int
            and healthy_calls == 0
            and type(multi_calls) is int
            and multi_calls == 0
            and probe_count == 4
            and probe_passed_count == 4
        ),
        hashes_match=hashes_match,
        consumer_count=2,
        identity_probe_count=probe_count,
        identity_probe_passed_count=probe_passed_count,
        healthy_consumer_loader_calls=(
            healthy_calls if type(healthy_calls) is int else -1
        ),
        multi_manager_consumer_loader_calls=(
            multi_calls if type(multi_calls) is int else -1
        ),
        consumer_loader_calls=total_consumer_loader_calls,
        live_validation_performed=False,
        production_accuracy_claimed=False,
    )


def _fixture_startup_timeout(csone_corpus_dir: Path | None) -> float:
    """Bound fixture startup time to the metadata-only corpus workload.

    A corpus-backed fixture must profile every eligible workbook before Flask can
    answer its readiness probe.  The ordinary fixture remains fail-fast at 60
    seconds; only an explicitly supplied corpus earns a larger, bounded window.
    No workbook content or filename is read or retained here.
    """

    if csone_corpus_dir is None:
        return _FIXTURE_STARTUP_BASE_SECONDS
    try:
        candidates = [
            path
            for path in csone_corpus_dir.expanduser().resolve().iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() == ".xlsx"
            and not path.name.startswith("~$")
        ]
        total_bytes = sum(max(0, path.stat().st_size) for path in candidates)
    except OSError:
        # The child process owns corpus validation and will fail with the real
        # cause.  Do not turn a metadata sizing error into an unbounded wait.
        return _FIXTURE_STARTUP_BASE_SECONDS
    if not candidates:
        return _FIXTURE_STARTUP_BASE_SECONDS
    estimated = (
        _FIXTURE_STARTUP_BASE_SECONDS
        + len(candidates) * _FIXTURE_CORPUS_SECONDS_PER_WORKBOOK
        + total_bytes / _FIXTURE_CORPUS_BYTES_PER_SECOND
    )
    return min(_FIXTURE_CORPUS_STARTUP_MAX_SECONDS, max(120.0, estimated))


def _write_prepared_replay_stdin(
    process: subprocess.Popen[Any],
    payload: bytes,
    *,
    timeout_seconds: float = 15.0,
) -> None:
    """Bound a runtime's exact prepared-payload pipe consumption."""

    if process.stdin is None:
        raise RuntimeError("prepared replay stdin pipe was not created")
    failure_kinds: list[str] = []

    def writer() -> None:
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            failure_kinds.append(type(exc).__name__)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    thread = threading.Thread(
        target=writer,
        name="prepared-replay-stdin-writer",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=max(1.0, min(float(timeout_seconds), 60.0)))
    if thread.is_alive():
        _terminate_gate_process_tree(process)
        thread.join(timeout=2)
        raise TimeoutError("guarded runtime did not consume prepared replay stdin")
    if failure_kinds:
        _terminate_gate_process_tree(process)
        raise RuntimeError("guarded runtime rejected prepared replay stdin")


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
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    csone_corpus_dir: Path | None = None,
    csone_replay_max_rows: int = 600,
    prepared_replay: PreparedCsoneReplay | None = None,
) -> Iterator[tuple[str, Path]]:
    if csone_corpus_dir is not None and prepared_replay is not None:
        raise ValueError("fixture runtime accepts corpus or prepared replay, not both")
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
        "--manifest",
        str(manifest_path),
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
    if prepared_replay is not None:
        command.extend(
            [
                "--prepared-replay-stdin",
                "--prepared-replay-bytes",
                str(prepared_replay.byte_length),
                "--prepared-replay-sha256",
                prepared_replay.payload_sha256,
                "--prepared-replay-manifest-sha256",
                prepared_replay.manifest_sha256,
                "--csone-replay-max-rows",
                str(prepared_replay.max_rows),
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
    log_descriptor = os.open(
        log_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(log_descriptor, "wb") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=(subprocess.PIPE if prepared_replay is not None else subprocess.DEVNULL),
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        _bind_gate_process_tree(process)
        if process.stdout is None:
            _terminate_gate_process_tree(process)
            raise RuntimeError("guarded runtime log pipe was not created")
        log_capture = _BoundedPipeCapture(
            process.stdout,
            limit_bytes=_RUNTIME_LOG_LIMIT_BYTES,
            sink=log_handle,
            on_exceeded=lambda: _terminate_gate_process_tree(process),
        )
        log_capture.start()
        try:
            if prepared_replay is not None:
                _write_prepared_replay_stdin(
                    process,
                    prepared_replay.payload,
                )
            _wait_for_fixture_runtime(
                base_url,
                process,
                timeout=_fixture_startup_timeout(csone_corpus_dir),
            )
            if log_capture.exceeded.is_set():
                raise RuntimeError("guarded runtime log exceeded its byte limit")
            yield base_url, log_path
            if log_capture.exceeded.is_set():
                raise RuntimeError("guarded runtime log exceeded its byte limit")
        finally:
            _terminate_gate_process_tree(process)
            log_capture.join()
            shutil.rmtree(state_dir, ignore_errors=True)
        if log_capture.exceeded.is_set() or not log_capture.digest_complete:
            raise RuntimeError("guarded runtime log capture was incomplete")


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
            "metamorphic_truth",
            "degraded_http",
            "decision_reports",
            "report_matrix",
            "multi_manager_reports",
            "multi_manager_isolation",
            "fixture_runtime_log",
            "multi_manager_runtime_log",
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
        required.add("prepared_replay_identity")
    required.add("scratch_confidentiality")
    skipped = sorted(
        name for name, result in gates.items() if result.get("status") == "skipped"
    )
    all_required_present = required <= set(gates)
    acceptance_complete = bool(
        all_required_present
        and not skipped
        and all(gates[name].get("ok") is True for name in required)
    )
    fixture_mode = profile == "local"
    live_performed = bool(
        profile == "work-machine"
        and acceptance_complete
        and gates.get("candidate_identity", {}).get("live_validation_performed") is True
        and gates.get("runtime_identity", {}).get("live_validation_performed") is True
        and gates.get("decision_reports", {}).get("live_validation_performed") is True
        and gates.get("report_matrix", {}).get("live_validation_performed") is True
        and gates.get("ai_features", {}).get("live_validation_performed") is True
        and gates.get("manager_workspace", {}).get("live_validation_performed") is True
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
    manifest_sha256 = hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()
    prepared_replay: PreparedCsoneReplay | None = None
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
    metamorphic_summary = scratch / "metamorphic-truth" / "summary.json"
    gates["metamorphic_truth"] = _run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_round169_metamorphic_acceptance.py"),
            "--output",
            str(metamorphic_summary),
            "--max-seconds",
            "300",
        ],
        summary_path=metamorphic_summary,
        projector=_project_metamorphic,
        timeout_seconds=420,
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
        gates["real_csone_replay"], payload_bytes = _run_prepared_replay_worker(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_csone_corpus_replay.py"),
                "--input-dir",
                str(args.csone_corpus_dir),
                "--max-rows",
                str(args.csone_replay_max_rows),
                "--manifest",
                str(args.manifest),
                "--summary",
                str(replay_summary),
                "--prepared-stdout",
            ],
            summary_path=replay_summary,
            timeout_seconds=_fixture_startup_timeout(Path(args.csone_corpus_dir)),
        )
        if gates["real_csone_replay"].get("ok") is True:
            try:
                byte_length = gates["real_csone_replay"].get(
                    "prepared_replay_byte_length"
                )
                prepared_replay = prepared_replay_from_bytes(
                    payload_bytes,
                    expected_length=byte_length,
                    expected_sha256=str(
                        gates["real_csone_replay"].get(
                            "prepared_replay_sha256"
                        )
                        or ""
                    ),
                    expected_as_of_utc=str(manifest["deterministic_clock_utc"]),
                    expected_manifest_sha256=manifest_sha256,
                    expected_max_rows=int(args.csone_replay_max_rows),
                )
            except (OSError, TypeError, ValueError) as exc:
                prepared_replay = None
                gates["real_csone_replay"].update(
                    {
                        "ok": False,
                        "status": "failed",
                        "prepared_handoff_error_kind": type(exc).__name__,
                        "prepared_handoff_error_sha256": _digest(str(exc)),
                    }
                )

        if prepared_replay is None:
            failure = _gate_result(
                ok=False,
                reason="required prepared CSOne replay was unavailable",
                acceptance_evidence=False,
                live_validation_performed=False,
                production_accuracy_claimed=False,
            )
            for name in (
                "degraded_http",
                "decision_reports",
                "report_matrix",
                "ai_features",
                "manager_workspace",
                "fixture_runtime_log",
                "multi_manager_reports",
                "multi_manager_isolation",
                "multi_manager_runtime_log",
                "prepared_replay_identity",
            ):
                gates[name] = dict(failure)
            gates["ask_ai_replay"] = (
                _skipped_gate("operator requested skip")
                if args.skip_replay
                else run_replay_gate()
            )
            return gates

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
                manifest_path=Path(args.manifest),
                csone_corpus_dir=(
                    args.csone_corpus_dir if prepared_replay is None else None
                ),
                csone_replay_max_rows=args.csone_replay_max_rows,
                prepared_replay=prepared_replay,
            ) as (base_url, log_path):
                healthy_prepared_identity_before = (
                    _probe_prepared_runtime_identity(
                        base_url,
                        prepared_replay,
                        timeout=args.request_timeout,
                    )
                    if prepared_replay is not None
                    else {}
                )
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
                    trusted_matrix_keys, _trusted_matrix_cohorts = (
                        _expected_report_matrix_contract(
                            local_acceptance=True,
                            days=args.days,
                            customer_name="Acme Corporation",
                            subscription_id="SUB-001",
                        )
                    )
                    projection = _project_matrix(
                        _read_json(matrix_summary) if matrix_summary else {},
                        expected_scenario_keys=trusted_matrix_keys,
                        expected_scenario_count=36,
                        expected_source_parity_cohort_count=2,
                        expected_max_freshness_skew_seconds=0,
                    )
                    projection_ok = projection.pop("projected_ok", False)
                    matrix_gate.update(projection)
                    matrix_gate["summary_present"] = matrix_summary is not None
                    matrix_gate["ok"] = bool(
                        matrix_gate.get("ok") is True
                        and projection_ok is True
                    )
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
                healthy_prepared_identity_after = (
                    _probe_prepared_runtime_identity(
                        base_url,
                        prepared_replay,
                        timeout=args.request_timeout,
                    )
                    if prepared_replay is not None
                    else {}
                )
                healthy_prepared_identity = dict(healthy_prepared_identity_after)
                if prepared_replay is not None:
                    healthy_prepared_identity.update(
                        {
                            "ok": bool(
                                healthy_prepared_identity_before.get("ok") is True
                                and healthy_prepared_identity_after.get("ok") is True
                            ),
                            "status": (
                                "passed"
                                if healthy_prepared_identity_before.get("ok") is True
                                and healthy_prepared_identity_after.get("ok") is True
                                else "failed"
                            ),
                            "pre_report_identity_ok": (
                                healthy_prepared_identity_before.get("ok") is True
                            ),
                            "post_report_identity_ok": (
                                healthy_prepared_identity_after.get("ok") is True
                            ),
                            "identity_probe_count": 2,
                            "identity_probe_passed_count": int(
                                healthy_prepared_identity_before.get("ok") is True
                            )
                            + int(
                                healthy_prepared_identity_after.get("ok") is True
                            ),
                            "consumer_loader_calls_before": (
                                healthy_prepared_identity_before.get(
                                    "consumer_loader_calls"
                                )
                                if type(
                                    healthy_prepared_identity_before.get(
                                        "consumer_loader_calls"
                                    )
                                )
                                is int
                                else -1
                            ),
                            "consumer_loader_calls_after": (
                                healthy_prepared_identity_after.get(
                                    "consumer_loader_calls"
                                )
                                if type(
                                    healthy_prepared_identity_after.get(
                                        "consumer_loader_calls"
                                    )
                                )
                                is int
                                else -1
                            ),
                        }
                    )
            healthy_log_bytes, healthy_log_sha256 = _path_file_identity(
                log_path,
                max_bytes=_RUNTIME_LOG_LIMIT_BYTES,
            )
            gates["fixture_runtime_log"] = _gate_result(
                ok=(
                    healthy_prepared_identity.get("ok") is True
                    if prepared_replay is not None
                    else True
                ),
                log_bytes=healthy_log_bytes,
                log_sha256=healthy_log_sha256,
                retained=False,
                live_validation_performed=False,
                production_accuracy_claimed=False,
                **(
                    {
                        key: value
                        for key, value in healthy_prepared_identity.items()
                        if key
                        not in {
                            "ok",
                            "status",
                            "live_validation_performed",
                            "production_accuracy_claimed",
                        }
                    }
                    if prepared_replay is not None
                    else {}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - sanitized orchestration failure
            failure = _gate_result(
                ok=False,
                error_kind=type(exc).__name__,
                error_sha256=_digest(str(exc)),
                production_accuracy_claimed=False,
            )
            # A context-manager exit can fail after the report/AI bodies have
            # already emitted green evidence (for example, an over-limit or
            # incomplete runtime log drain).  That process-level failure
            # invalidates every result gathered from the runtime; never retain
            # earlier green projections with ``setdefault``.
            gates["report_matrix"] = dict(failure)
            gates["ai_features"] = dict(failure)
            gates["manager_workspace"] = dict(failure)
            gates["fixture_runtime_log"] = dict(failure)

    if args.skip_matrix:
        gates["multi_manager_reports"] = _skipped_gate(
            "operator requested skip"
        )
    else:
        try:
            with _fixture_runtime(
                scratch,
                scenario="multi_manager",
                manifest_path=Path(args.manifest),
                csone_corpus_dir=(
                    args.csone_corpus_dir if prepared_replay is None else None
                ),
                csone_replay_max_rows=args.csone_replay_max_rows,
                prepared_replay=prepared_replay,
            ) as (multi_base_url, multi_log_path):
                multi_prepared_identity_before = (
                    _probe_prepared_runtime_identity(
                        multi_base_url,
                        prepared_replay,
                        timeout=args.request_timeout,
                    )
                    if prepared_replay is not None
                    else {}
                )
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
                    multi_gate.get("ok") is True
                    and projection_ok is True
                )
                multi_gate["status"] = (
                    "passed" if multi_gate["ok"] else "failed"
                )
                gates["multi_manager_reports"] = multi_gate
                multi_prepared_identity_after = (
                    _probe_prepared_runtime_identity(
                        multi_base_url,
                        prepared_replay,
                        timeout=args.request_timeout,
                    )
                    if prepared_replay is not None
                    else {}
                )
                multi_prepared_identity = dict(multi_prepared_identity_after)
                if prepared_replay is not None:
                    multi_prepared_identity.update(
                        {
                            "ok": bool(
                                multi_prepared_identity_before.get("ok") is True
                                and multi_prepared_identity_after.get("ok") is True
                            ),
                            "status": (
                                "passed"
                                if multi_prepared_identity_before.get("ok") is True
                                and multi_prepared_identity_after.get("ok") is True
                                else "failed"
                            ),
                            "pre_report_identity_ok": (
                                multi_prepared_identity_before.get("ok") is True
                            ),
                            "post_report_identity_ok": (
                                multi_prepared_identity_after.get("ok") is True
                            ),
                            "identity_probe_count": 2,
                            "identity_probe_passed_count": int(
                                multi_prepared_identity_before.get("ok") is True
                            )
                            + int(multi_prepared_identity_after.get("ok") is True),
                            "consumer_loader_calls_before": (
                                multi_prepared_identity_before.get(
                                    "consumer_loader_calls"
                                )
                                if type(
                                    multi_prepared_identity_before.get(
                                        "consumer_loader_calls"
                                    )
                                )
                                is int
                                else -1
                            ),
                            "consumer_loader_calls_after": (
                                multi_prepared_identity_after.get(
                                    "consumer_loader_calls"
                                )
                                if type(
                                    multi_prepared_identity_after.get(
                                        "consumer_loader_calls"
                                    )
                                )
                                is int
                                else -1
                            ),
                        }
                    )
            multi_log_bytes, multi_log_sha256 = _path_file_identity(
                multi_log_path,
                max_bytes=_RUNTIME_LOG_LIMIT_BYTES,
            )
            gates["multi_manager_runtime_log"] = _gate_result(
                ok=(
                    multi_prepared_identity.get("ok") is True
                    if prepared_replay is not None
                    else True
                ),
                log_bytes=multi_log_bytes,
                log_sha256=multi_log_sha256,
                retained=False,
                live_validation_performed=False,
                production_accuracy_claimed=False,
                **(
                    {
                        key: value
                        for key, value in multi_prepared_identity.items()
                        if key
                        not in {
                            "ok",
                            "status",
                            "live_validation_performed",
                            "production_accuracy_claimed",
                        }
                    }
                    if prepared_replay is not None
                    else {}
                ),
            )
        except Exception as exc:  # noqa: BLE001
            failure = _gate_result(
                ok=False,
                error_kind=type(exc).__name__,
                error_sha256=_digest(str(exc)),
                production_accuracy_claimed=False,
            )
            gates["multi_manager_reports"] = dict(failure)
            gates["multi_manager_isolation"] = dict(failure)
            gates["multi_manager_runtime_log"] = dict(failure)

    if prepared_replay is not None:
        producer = gates.get("real_csone_replay") or {}
        healthy = gates.get("fixture_runtime_log") or {}
        multi = gates.get("multi_manager_runtime_log") or {}
        gates["prepared_replay_identity"] = _prepared_replay_identity_gate(
            producer,
            healthy,
            multi,
        )

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
        trusted_matrix_keys, _trusted_matrix_cohorts = (
            _expected_report_matrix_contract(
                local_acceptance=False,
                days=args.days,
                manager=args.manager,
                customer_name=args.customer_name,
                subscription_id=args.subscription_id,
            )
        )
        projection = _project_matrix(
            _read_json(matrix_summary) if matrix_summary else {},
            expected_scenario_keys=trusted_matrix_keys,
            expected_scenario_count=42,
            expected_source_parity_cohort_count=1,
            expected_max_freshness_skew_seconds=4 * 60 * 60,
        )
        projection_ok = projection.pop("projected_ok", False)
        matrix_gate.update(projection)
        matrix_gate["summary_present"] = matrix_summary is not None
        matrix_gate["ok"] = bool(
            matrix_gate.get("ok") is True
            and projection_ok is True
        )
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
    previous_umask = os.umask(0o077)
    try:
        if retained is not None:
            retained = retained / (
                "round146-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + f"-{os.getpid()}"
            )
            retained.mkdir(parents=True, mode=0o700, exist_ok=False)
            retained.chmod(0o700)
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
            gates["scratch_confidentiality"] = _scratch_confidentiality_gate(
                retained,
                retained=True,
            )
        else:
            with tempfile.TemporaryDirectory(prefix="adoptiq-round146-") as temporary:
                scratch = Path(temporary)
                scratch.chmod(0o700)
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
                gates["scratch_confidentiality"] = _scratch_confidentiality_gate(
                    scratch,
                    retained=False,
                )
    finally:
        os.umask(previous_umask)

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
