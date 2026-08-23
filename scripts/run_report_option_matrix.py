#!/usr/bin/env python3
"""Round 133: live exhaustive report-option matrix runner.

Exercises every report type, manager, and technology at a fixed scope
(default 90 days) against a running AdoptIQ instance.  Writes a
``matrix_summary.json`` rollup to the downloads directory.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from report_source_parity import (  # noqa: E402
    PARITY_PROJECTED_FIELDS,
    PARITY_SHEET_NAMES,
    ParityContractError,
    build_workbook_parity_signature,
    validate_ooxml_artifact,
)

_CROSS_REPORT_SOURCE_SHEETS = PARITY_SHEET_NAMES
_CROSS_REPORT_REQUIRED_FAMILIES = (
    "compact",
    "comprehensive",
    "leader",
    "renewal",
)
_CROSS_REPORT_PROJECTED_FIELDS = PARITY_PROJECTED_FIELDS
_R114_RESULT_LINE_RE = re.compile(
    r"^CRITICAL_ISSUES_FOUND=(True|False)[ \t\r]*$"
)
_R114_TIMEOUT_SECONDS = 300.0
_R114_OUTPUT_LIMIT_BYTES = 1024 * 1024
_R114_MARKER_LINE_LIMIT_CHARS = 128
_KNOWN_REPORT_FAMILIES = {
    "compact": "compact",
    "comprehensive": "comprehensive",
    "leader": "leader",
    "renewal": "renewal",
    "renewal_portfolio": "renewal",
    "subscription": "subscription",
    "subscription_analysis": "subscription",
}


def _ensure_repo_root_on_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def _probe_connectivity(base_url: str) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    url = f"{base_url.rstrip('/')}/api/diag/connectivity"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310  # nosec B310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"ok": False, "error_kind": "connectivity_probe_failed", "error": str(exc)}
    if not isinstance(payload, dict):
        return {"ok": False, "error_kind": "connectivity_invalid_payload"}
    return payload


def _probe_running_reports(base_url: str) -> list[dict[str, Any]]:
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from run_report_soak import probe_running_reports  # noqa: WPS433

    return probe_running_reports(base_url)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Best-effort bounded cleanup for an R114 process and its descendants."""

    if os.name == "nt":  # pragma: no cover - exercised on the Windows lane
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
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # The direct child can exit while a descendant keeps running or holds
        # an inherited output pipe.  Kill the private process group even when
        # the direct child has already been reaped.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class _BoundedPipeDigest:
    """Drain one child pipe while retaining no raw child output."""

    def __init__(
        self,
        stream: Any,
        *,
        limit_bytes: int,
        terminate: Any,
        parse_status_marker: bool = False,
    ) -> None:
        self.stream = stream
        self.limit_bytes = int(limit_bytes)
        self.terminate = terminate
        self.byte_count = 0
        self._digest = hashlib.sha256()
        self.truncated = False
        self.complete = False
        self.utf8_valid = True
        self.marker: str | None = None
        self.marker_count = 0
        self._parse_status_marker = bool(parse_status_marker)
        self._decoder = (
            codecs.getincrementaldecoder("utf-8")("strict")
            if self._parse_status_marker
            else None
        )
        self._line_fragment = ""
        self._discard_line = False
        self.thread = threading.Thread(
            target=self._drain,
            name="r114-bounded-output",
            daemon=True,
        )

    def _finish_status_line(self) -> None:
        if not self._discard_line:
            match = _R114_RESULT_LINE_RE.fullmatch(self._line_fragment)
            if match:
                self.marker_count += 1
                if self.marker_count == 1:
                    self.marker = match.group(1)
        self._line_fragment = ""
        self._discard_line = False

    def _consume_status_text(self, text: str) -> None:
        for index, fragment in enumerate(text.split("\n")):
            if index:
                self._finish_status_line()
            if self._discard_line:
                continue
            if (
                len(self._line_fragment) + len(fragment)
                > _R114_MARKER_LINE_LIMIT_CHARS
            ):
                self._line_fragment = ""
                self._discard_line = True
            else:
                self._line_fragment += fragment

    def _decode_status_block(self, block: bytes, *, final: bool = False) -> None:
        if self._decoder is None or not self.utf8_valid:
            return
        try:
            decoded = self._decoder.decode(block, final=final)
        except UnicodeDecodeError:
            self.utf8_valid = False
            self._line_fragment = ""
            self._discard_line = True
            return
        self._consume_status_text(decoded)
        if final:
            self._finish_status_line()

    def _drain(self) -> None:
        try:
            while True:
                remaining = self.limit_bytes - self.byte_count
                block = self.stream.read(min(64 * 1024, max(1, remaining + 1)))
                if not block:
                    self._decode_status_block(b"", final=True)
                    self.complete = True
                    return
                if len(block) > remaining:
                    self.byte_count = self.limit_bytes + 1
                    self.truncated = True
                    self.terminate()
                    return
                self.byte_count += len(block)
                self._digest.update(block)
                self._decode_status_block(block)
        except (OSError, ValueError):
            self.truncated = True
            self.terminate()
        finally:
            try:
                self.stream.close()
            except OSError:
                pass

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest() if self.complete and not self.truncated else ""

@dataclass(frozen=True)
class _BoundedProcessResult:
    returncode: int
    stdout_marker: str | None
    stdout_marker_count: int
    stdout_utf8_valid: bool
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str
    timed_out: bool
    output_truncated: bool


def _run_bounded_process(
    command: list[str],
    *,
    timeout_seconds: float = _R114_TIMEOUT_SECONDS,
    output_limit_bytes: int = _R114_OUTPUT_LIMIT_BYTES,
) -> _BoundedProcessResult:
    """Run one subprocess with hard retained-output and elapsed-time bounds."""

    if not 1.0 <= float(timeout_seconds) <= 3600.0:
        raise ValueError("bounded process timeout is invalid")
    if not 1024 <= int(output_limit_bytes) <= 16 * 1024 * 1024:
        raise ValueError("bounded process output limit is invalid")
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=str(REPO_ROOT),
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
    if process.stdout is None or process.stderr is None:
        _terminate_process_tree(process)
        raise OSError("bounded process pipes were not created")

    terminate_lock = threading.Lock()

    def terminate() -> None:
        with terminate_lock:
            _terminate_process_tree(process)

    stdout = _BoundedPipeDigest(
        process.stdout,
        limit_bytes=output_limit_bytes,
        terminate=terminate,
        parse_status_marker=True,
    )
    stderr = _BoundedPipeDigest(
        process.stderr,
        limit_bytes=output_limit_bytes,
        terminate=terminate,
    )
    stdout.thread.start()
    stderr.thread.start()
    timed_out = False
    deadline = time.monotonic() + float(timeout_seconds)
    while process.poll() is None:
        if time.monotonic() >= deadline:
            timed_out = True
            terminate()
            break
        if stdout.truncated or stderr.truncated:
            terminate()
            break
        time.sleep(0.02)
    try:
        returncode = int(process.wait(timeout=5))
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate()
        returncode = int(process.returncode) if process.returncode is not None else -1
    terminate()
    stdout.thread.join(timeout=5)
    stderr.thread.join(timeout=5)
    output_truncated = bool(
        stdout.truncated
        or stderr.truncated
        or stdout.thread.is_alive()
        or stderr.thread.is_alive()
        or not stdout.complete
        or not stderr.complete
    )
    return _BoundedProcessResult(
        returncode=returncode,
        stdout_marker=stdout.marker,
        stdout_marker_count=stdout.marker_count,
        stdout_utf8_valid=stdout.utf8_valid,
        stdout_bytes=stdout.byte_count,
        stdout_sha256=stdout.sha256,
        stderr_bytes=stderr.byte_count,
        stderr_sha256=stderr.sha256,
        timed_out=timed_out,
        output_truncated=output_truncated,
    )


def _run_r114_audit(
    docx_path: Path | None,
    xlsx_path: Path | None,
    *,
    allowed_root: Path | str | None = None,
    timeout_seconds: float = _R114_TIMEOUT_SECONDS,
    output_limit_bytes: int = _R114_OUTPUT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Run R114 on exactly one DOCX/XLSX pair and fail closed.

    A zero process status is not sufficient evidence: the audit must emit one
    recognized terminal marker and that marker must explicitly say ``False``.
    """

    missing_artifacts = [
        file_type
        for file_type, path in (("docx", docx_path), ("xlsx", xlsx_path))
        if path is None or not path.is_file()
    ]
    if missing_artifacts:
        return {
            "ok": False,
            "critical": True,
            "reason": "artifact_pair_incomplete",
            "missing_artifact_types": missing_artifacts,
            "returncode": None,
            "marker": None,
        }
    assert docx_path is not None
    assert xlsx_path is not None
    if allowed_root is not None:
        try:
            validate_ooxml_artifact(docx_path, allowed_root=allowed_root)
            validate_ooxml_artifact(xlsx_path, allowed_root=allowed_root)
        except ParityContractError as exc:
            return {
                "ok": False,
                "critical": True,
                "reason": "artifact_contract_failed",
                "error_kind": exc.kind,
                "returncode": None,
                "marker": None,
                "timed_out": False,
                "output_truncated": False,
            }
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "r114_audit_reports.py"),
        "--name",
        "run",
        "--docx",
        str(docx_path),
        "--xlsx",
        str(xlsx_path),
    ]
    try:
        completed = _run_bounded_process(
            command,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed on audit execution
        return {
            "ok": False,
            "critical": True,
            "reason": "audit_process_failed",
            "exception_type": type(exc).__name__,
            "returncode": None,
            "marker": None,
            "timed_out": False,
            "output_truncated": False,
        }
    if completed.timed_out:
        reason = "audit_timeout"
    elif completed.output_truncated:
        reason = "audit_output_limit"
    elif not completed.stdout_utf8_valid:
        reason = "audit_output_malformed"
    else:
        reason = ""
    marker = (
        completed.stdout_marker
        if not reason and completed.stdout_marker_count == 1
        else None
    )
    if not reason and completed.stdout_marker_count != 1:
        reason = (
            "audit_marker_missing"
            if completed.stdout_marker_count == 0
            else "audit_marker_malformed"
        )
    elif not reason and completed.returncode != 0:
        reason = "audit_nonzero_exit"
    elif not reason and marker != "False":
        reason = "audit_critical_findings"
    elif not reason:
        reason = "audit_passed"
    ok = bool(
        not completed.timed_out
        and not completed.output_truncated
        and completed.returncode == 0
        and marker == "False"
        and completed.stdout_marker_count == 1
    )
    return {
        "ok": ok,
        "critical": not ok,
        "reason": reason,
        "returncode": completed.returncode,
        "marker": marker,
        "stdout_bytes": completed.stdout_bytes,
        "stdout_sha256": completed.stdout_sha256,
        "stderr_bytes": completed.stderr_bytes,
        "stderr_sha256": completed.stderr_sha256,
        "timed_out": completed.timed_out,
        "output_truncated": completed.output_truncated,
    }


def _canonical_report_family(raw: Any) -> str:
    token = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(raw or "").strip().casefold(),
    ).strip("_")
    return _KNOWN_REPORT_FAMILIES.get(token, "")


def _scenario_report_family(scenario: Any) -> str:
    endpoint = str(getattr(scenario, "endpoint", "") or "")
    payload = getattr(scenario, "payload", {})
    if not isinstance(payload, dict):
        return ""
    if endpoint == "/start_compact_analysis":
        return "compact"
    if endpoint == "/start_leader_report":
        return "leader"
    if endpoint == "/start_analysis":
        return _canonical_report_family(payload.get("report_type"))
    return ""


def _declared_source_parity_cohorts(
    matrix: dict[str, Any],
    scenario_keys: list[str],
) -> dict[str, dict[str, str]]:
    """Project immutable scenario cohort declarations for the parity gate."""

    declarations: dict[str, dict[str, str]] = {}
    for scenario_key in scenario_keys:
        scenario = matrix.get(scenario_key)
        cohort = str(getattr(scenario, "source_parity_cohort", "") or "").strip()
        if not cohort:
            continue
        declarations[scenario_key] = {
            "cohort": cohort,
            "family": _scenario_report_family(scenario),
        }
    return declarations


def _attach_scenario_inventory(
    summary: dict[str, Any],
    expected_keys: list[str],
) -> dict[str, Any]:
    """Attach an exact, machine-checkable expected/completed inventory."""

    raw_results = list(summary.get("results") or [])
    malformed_result_count = sum(
        not isinstance(result, dict) for result in raw_results
    )
    completed_keys = [
        str(result.get("scenario") or "")
        for result in raw_results
        if isinstance(result, dict)
    ]
    missing = [key for key in expected_keys if key not in completed_keys]
    unexpected = [key for key in completed_keys if key not in expected_keys]
    duplicates = sorted(
        {key for key in completed_keys if completed_keys.count(key) > 1}
    )
    exact = (
        malformed_result_count == 0
        and completed_keys == expected_keys
        and not duplicates
    )
    summary.update(
        {
            "scenario_keys_expected": list(expected_keys),
            "scenario_count_expected": len(expected_keys),
            "scenario_keys_completed": completed_keys,
            "scenario_count_completed": len(completed_keys),
            "scenario_keys_missing": missing,
            "scenario_keys_unexpected": unexpected,
            "scenario_keys_completed_duplicate": duplicates,
            "scenario_results_malformed_count": malformed_result_count,
            "scenario_inventory_exact": exact,
        }
    )
    if not exact:
        summary["all_passed"] = False
        summary["aborted"] = True
    return summary


def _cross_report_source_consistency(
    summary: dict[str, Any],
    *,
    max_freshness_skew_seconds: int = 0,
    declared_cohorts: dict[str, dict[str, str]] | None = None,
    artifacts_root: Path | str | None = None,
) -> dict[str, Any]:
    """Compare all canonical workbook facts across explicit exact quartets.

    The declaration comes from immutable scenario definitions, not from
    coincidentally matching labels in generated artifacts.  This lets a named
    technology triplet remain legitimate while a missing or duplicated member
    of a declared quartet fails closed.  Only counts and digests of source
    content leave this function.
    """

    import pandas as pd  # noqa: PLC0415

    if (
        isinstance(max_freshness_skew_seconds, bool)
        or not isinstance(max_freshness_skew_seconds, int)
        or max_freshness_skew_seconds < 0
    ):
        raise ValueError("max_freshness_skew_seconds must be a non-negative integer")

    def safe_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]

    required_family_set = set(_CROSS_REPORT_REQUIRED_FAMILIES)
    declarations = declared_cohorts if isinstance(declared_cohorts, dict) else {}
    declaration_errors: list[dict[str, Any]] = []
    declared_by_cohort: dict[str, list[dict[str, str]]] = {}
    for scenario, raw in declarations.items():
        if not isinstance(scenario, str) or not scenario or not isinstance(raw, dict):
            declaration_errors.append({"kind": "malformed_declaration"})
            continue
        cohort = str(raw.get("cohort") or "").strip()
        family = str(raw.get("family") or "").strip().casefold()
        if not cohort or family not in required_family_set:
            declaration_errors.append(
                {
                    "kind": "malformed_declaration",
                    "scenario_digest": safe_digest(scenario),
                }
            )
            continue
        declared_by_cohort.setdefault(cohort, []).append(
            {"scenario": scenario, "family": family}
        )
    if not declared_by_cohort:
        declaration_errors.append({"kind": "no_declared_parity_cohort"})

    duplicate_family_groups: list[dict[str, Any]] = []
    incomplete_scope_groups: list[dict[str, Any]] = []
    valid_cohorts: set[str] = set()
    for cohort, members in sorted(declared_by_cohort.items()):
        family_counts = {
            family: sum(member["family"] == family for member in members)
            for family in _CROSS_REPORT_REQUIRED_FAMILIES
        }
        duplicate_families = sorted(
            family for family, count in family_counts.items() if count > 1
        )
        missing_families = sorted(
            family for family, count in family_counts.items() if count == 0
        )
        cohort_digest = safe_digest(cohort)
        if duplicate_families:
            duplicate_family_groups.append(
                {"group_digest": cohort_digest, "families": duplicate_families}
            )
        if missing_families or len(members) != len(required_family_set):
            incomplete_scope_groups.append(
                {
                    "group_digest": cohort_digest,
                    "families_present": sorted(
                        family for family, count in family_counts.items() if count
                    ),
                    "families_missing": missing_families,
                }
            )
        if not duplicate_families and not missing_families and len(members) == len(required_family_set):
            valid_cohorts.add(cohort)

    raw_results = summary.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    results_by_scenario: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        scenario = result.get("scenario")
        if isinstance(scenario, str) and scenario:
            results_by_scenario.setdefault(scenario, []).append(result)

    read_errors: list[dict[str, str]] = []
    membership_errors: list[dict[str, str]] = []
    identity_quality_errors: list[dict[str, Any]] = []
    entries_by_cohort: dict[str, list[dict[str, Any]]] = {}
    declared_scenarios = set(declarations)
    ignored_non_parity_scenario_count = sum(
        isinstance(result, dict)
        and result.get("all_passed") is True
        and (
            not isinstance(result.get("scenario"), str)
            or result.get("scenario") not in declared_scenarios
        )
        for result in results
    )
    for cohort, members in sorted(declared_by_cohort.items()):
        cohort_digest = safe_digest(cohort)
        if cohort not in valid_cohorts:
            continue
        for member in members:
            scenario = member["scenario"]
            scenario_digest = safe_digest(scenario)
            matching = results_by_scenario.get(scenario, [])
            if len(matching) != 1:
                membership_errors.append(
                    {
                        "group_digest": cohort_digest,
                        "scenario_digest": scenario_digest,
                        "kind": "scenario_result_missing" if not matching else "scenario_result_duplicate",
                    }
                )
                continue
            result = matching[0]
            if result.get("all_passed") is not True:
                membership_errors.append(
                    {
                        "group_digest": cohort_digest,
                        "scenario_digest": scenario_digest,
                        "kind": "scenario_not_passed",
                    }
                )
                continue
            xlsx_artifacts = [
                artifact
                for artifact in result.get("artifacts") or []
                if isinstance(artifact, dict) and artifact.get("file_type") == "xlsx"
            ]
            if len(xlsx_artifacts) != 1:
                read_errors.append(
                    {
                        "scenario_digest": scenario_digest,
                        "kind": "xlsx_artifact_missing" if not xlsx_artifacts else "xlsx_artifact_duplicate",
                    }
                )
                continue
            xlsx_path = Path(str(xlsx_artifacts[0].get("debug_path") or ""))
            try:
                workbook = build_workbook_parity_signature(
                    xlsx_path,
                    allowed_root=artifacts_root,
                )
            except ParityContractError as exc:
                read_errors.append({"scenario_digest": scenario_digest, "kind": exc.kind})
                continue
            actual_family = str(
                workbook["metadata"].get("report_family") or ""
            )
            if actual_family != member["family"]:
                membership_errors.append(
                    {
                        "group_digest": cohort_digest,
                        "scenario_digest": scenario_digest,
                        "kind": "report_family_mismatch",
                    }
                )
                continue
            for sheet_name, signature in workbook["signatures"].items():
                missing_count = int(signature["missing_identity_count"])
                duplicate_count = int(signature["duplicate_identity_count"])
                if missing_count or duplicate_count:
                    identity_quality_errors.append(
                        {
                            "group_digest": cohort_digest,
                            "scenario_digest": scenario_digest,
                            "report_family": actual_family,
                            "source_sheet": sheet_name,
                            "missing_identity_count": missing_count,
                            "duplicate_identity_count": duplicate_count,
                        }
                    )
            metadata = workbook["metadata"]
            entries_by_cohort.setdefault(cohort, []).append(
                {
                    "report_family": actual_family,
                    "signatures": workbook["signatures"],
                    "group_key": (
                        metadata["scope_contract_sha256"],
                        metadata["scope_type"],
                        metadata["days"],
                        metadata["data_mode"],
                        metadata["live_source_validation"],
                    ),
                    "freshness": {
                        "data_as_of_utc": metadata["data_as_of_utc"],
                        "data_as_of_state": metadata["data_as_of_state"],
                        "retrieval_attempted_at_utc": metadata["retrieval_attempted_at_utc"],
                        "evaluation_as_of_utc": metadata["evaluation_as_of_utc"],
                    },
                }
            )

    mismatches: list[dict[str, Any]] = []
    freshness_mismatches: list[dict[str, Any]] = []
    scope_mismatches: list[dict[str, Any]] = []
    groups_evaluated = 0
    comparisons = 0
    report_family_sets: set[tuple[str, ...]] = set()
    for cohort in sorted(valid_cohorts):
        cohort_digest = safe_digest(cohort)
        entries = entries_by_cohort.get(cohort, [])
        family_counts = {
            family: sum(entry["report_family"] == family for entry in entries)
            for family in _CROSS_REPORT_REQUIRED_FAMILIES
        }
        if len(entries) != len(required_family_set) or any(count != 1 for count in family_counts.values()):
            incomplete_scope_groups.append(
                {
                    "group_digest": cohort_digest,
                    "families_present": sorted(family for family, count in family_counts.items() if count),
                    "families_missing": sorted(family for family, count in family_counts.items() if not count),
                }
            )
            continue
        group_keys = {entry["group_key"] for entry in entries}
        if len(group_keys) != 1:
            scope_mismatches.append(
                {
                    "group_digest": cohort_digest,
                    "metadata_sha256": sorted(
                        hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()
                        for key in group_keys
                    ),
                }
            )
            continue
        groups_evaluated += 1
        families = tuple(sorted(family_counts))
        report_family_sets.add(families)

        freshness_observed = {
            entry["report_family"]: entry["freshness"] for entry in entries
        }
        freshness_values = list(freshness_observed.values())
        states = {value["data_as_of_state"] for value in freshness_values}
        clocks_within_bound = True
        for clock_key in (
            "data_as_of_utc",
            "retrieval_attempted_at_utc",
            "evaluation_as_of_utc",
        ):
            values = [value[clock_key] for value in freshness_values]
            if any(values) != all(values):
                clocks_within_bound = False
                continue
            if not values or not values[0]:
                continue
            timestamps = [pd.Timestamp(value) for value in values]
            if (max(timestamps) - min(timestamps)).total_seconds() > max_freshness_skew_seconds:
                clocks_within_bound = False
        if len(states) > 1 or not clocks_within_bound:
            freshness_mismatches.append(
                {"group_digest": cohort_digest, "observed": freshness_observed}
            )

        for sheet_name in _CROSS_REPORT_SOURCE_SHEETS:
            comparisons += 1
            observed = {
                entry["report_family"]: entry["signatures"][sheet_name]
                for entry in entries
            }
            unique = {
                tuple(signature[field] for field in _CROSS_REPORT_PROJECTED_FIELDS)
                for signature in observed.values()
            }
            if len(unique) > 1:
                mismatches.append(
                    {
                        "group_digest": cohort_digest,
                        "source_sheet": sheet_name,
                        "observed": observed,
                    }
                )

    cohorts_expected = len(declared_by_cohort)
    expected_comparisons = cohorts_expected * len(_CROSS_REPORT_SOURCE_SHEETS)
    comparison_requirement_met = bool(
        cohorts_expected > 0
        and groups_evaluated == cohorts_expected
        and comparisons == expected_comparisons
        and report_family_sets == {tuple(sorted(required_family_set))}
        and not declaration_errors
        and not duplicate_family_groups
        and not incomplete_scope_groups
        and not membership_errors
        and not scope_mismatches
    )
    return {
        "ok": (
            comparison_requirement_met
            and not read_errors
            and not identity_quality_errors
            and not mismatches
            and not freshness_mismatches
        ),
        "comparison_requirement_met": comparison_requirement_met,
        "groups_discovered": cohorts_expected,
        "groups_evaluated": groups_evaluated,
        "comparisons": comparisons,
        "comparisons_expected": expected_comparisons,
        "required_report_families": list(_CROSS_REPORT_REQUIRED_FAMILIES),
        "required_sheets": list(_CROSS_REPORT_SOURCE_SHEETS),
        "projected_fields": list(_CROSS_REPORT_PROJECTED_FIELDS),
        "required_family_set_group_count": groups_evaluated,
        "required_family_set_group_count_expected": cohorts_expected,
        "report_family_sets_compared": [
            list(item) for item in sorted(report_family_sets)
        ],
        "cohort_declaration_errors": declaration_errors,
        "cohort_membership_errors": membership_errors,
        "incomplete_equivalent_scope_groups": incomplete_scope_groups,
        "duplicate_family_groups": duplicate_family_groups,
        "scope_mismatches": scope_mismatches,
        "identity_quality_errors": identity_quality_errors,
        "ignored_non_parity_scenario_count": ignored_non_parity_scenario_count,
        "mismatches": mismatches,
        "freshness_mismatches": freshness_mismatches,
        "read_errors": read_errors,
        "max_freshness_skew_seconds": max_freshness_skew_seconds,
        "privacy": "counts_and_sha256_only_no_source_values",
    }


def build_matrix_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Round 133 exhaustive AdoptIQ report option matrix.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:5151", help="Live AdoptIQ base URL")
    parser.add_argument("--downloads-dir", default="~/Downloads", help="Directory for debug artifacts")
    parser.add_argument("--days", type=int, default=90, help="Analysis window (1-365)")
    parser.add_argument(
        "--blocks",
        default="all",
        help="Comma-separated matrix blocks: E,F,A,B,C,D,G or 'all' (cheap-first default order)",
    )
    parser.add_argument(
        "--resume-from",
        default="",
        help="Scenario key to resume from (inclusive)",
    )
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between /status polls")
    parser.add_argument("--timeout", type=int, default=1800, help="Max seconds per scenario")
    parser.add_argument("--request-timeout", type=int, default=120, help="HTTP request timeout")
    parser.add_argument("--download-timeout", type=int, default=300, help="Artifact download timeout")
    parser.add_argument("--run-id", default="", help="Optional run identifier")
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop on first failing scenario")
    parser.add_argument("--strict", action="store_true", help="Enable strict quality gates")
    parser.add_argument(
        "--baseline-mode",
        choices=["off", "latest", "manifest"],
        default="off",
        help="Baseline comparison mode (default off for live matrix)",
    )
    parser.add_argument("--baseline-manifest", default="", help="Baseline manifest path when mode=manifest")
    parser.add_argument(
        "--manager",
        default=os.environ.get("ADOPTIQ_MATRIX_MANAGER", ""),
        help=(
            "Authorized manager scope for canonical and Block G live runs; "
            "required outside --local-acceptance"
        ),
    )
    parser.add_argument(
        "--customer-name",
        default=os.environ.get("ADOPTIQ_MATRIX_CUSTOMER", ""),
        help=(
            "Authorized customer scope for Block G live runs; required outside "
            "--local-acceptance"
        ),
    )
    parser.add_argument(
        "--subscription-id",
        default=os.environ.get("ADOPTIQ_MATRIX_SUBSCRIPTION_ID", ""),
        help="Block G subscription analysis ID (skip when empty)",
    )
    parser.add_argument(
        "--csone-upload-path",
        default=os.environ.get("ADOPTIQ_MATRIX_CSONE_FILE", ""),
        help="Block G explicit CSOne upload path (skip upload edges when empty)",
    )
    parser.add_argument(
        "--allow-running",
        action="store_true",
        help="Allow matrix start even when other analyses are running",
    )
    parser.add_argument(
        "--skip-r114",
        action="store_true",
        help="Skip per-run R114 artifact audit (faster; not recommended)",
    )
    parser.add_argument("--min-docx-similarity", type=float, default=0.35)
    parser.add_argument("--min-sheet-overlap", type=float, default=0.5)
    parser.add_argument("--min-header-similarity", type=float, default=0.3)
    parser.add_argument("--min-docx-chars", type=int, default=200)
    parser.add_argument("--min-docx-numeric-similarity", type=float, default=0.8)
    parser.add_argument("--min-docx-table-numeric-similarity", type=float, default=0.95)
    parser.add_argument("--max-xlsx-row-delta-ratio", type=float, default=0.2)
    parser.add_argument("--max-xlsx-row-delta-abs", type=int, default=25)
    parser.add_argument(
        "--local-acceptance",
        action="store_true",
        help=(
            "Require the explicitly started sanitized local-acceptance runtime "
            "and use its production-contract report matrix"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_root_on_path()
    from report_iteration_loop import (
        EdgeMatrixConfig,
        build_exhaustive_option_matrix,
        build_local_acceptance_multi_manager_matrix,
        build_local_acceptance_option_matrix,
        build_runner_config,
        parse_matrix_blocks,
        run_option_matrix,
        select_matrix_scenario_keys,
    )

    parser = build_matrix_arg_parser()
    args = parser.parse_args(argv)

    parsed = urlparse(args.base_url)
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
        print("[matrix] base-url must target the local AdoptIQ app", file=sys.stderr)
        return 2

    if not args.allow_running:
        try:
            running = _probe_running_reports(args.base_url)
        except RuntimeError as exc:
            print(f"[matrix] preflight failed: {exc}", file=sys.stderr)
            return 2
        if running:
            print(f"[matrix] refusing to start: {len(running)} analysis(es) still running", file=sys.stderr)
            for item in running[:5]:
                print(f"  - {item}", file=sys.stderr)
            return 3

    connectivity = _probe_connectivity(args.base_url)
    if args.local_acceptance:
        if (
            connectivity.get("ok") is not True
            or connectivity.get("mode") != "local_acceptance_fixture"
            or connectivity.get("live_validation_performed") is not False
        ):
            print(
                "[matrix] local-acceptance mode requires the explicit sanitized "
                "loopback fixture runtime",
                file=sys.stderr,
            )
            print(json.dumps(connectivity, indent=2, sort_keys=True), file=sys.stderr)
            return 5
    elif connectivity.get("ok") is not True:
        print(
            "[matrix] Snowflake/connectivity preflight failed — live matrix requires VPN + credentials",
            file=sys.stderr,
        )
        print(json.dumps(connectivity, indent=2, sort_keys=True), file=sys.stderr)
        return 5

    if args.local_acceptance:
        local_customer = args.customer_name.strip() or "Acme Corporation"
        if str(connectivity.get("scenario") or "") == "multi_manager":
            matrix = build_local_acceptance_multi_manager_matrix(
                days=max(min(int(args.days), 365), 1),
            )
        else:
            matrix = build_local_acceptance_option_matrix(
                days=max(min(int(args.days), 365), 1),
                customer_name=local_customer,
                subscription_id=args.subscription_id or "SUB-001",
            )
    else:
        selected_manager = args.manager.strip()
        selected_customer = args.customer_name.strip()
        if not selected_manager or not selected_customer:
            print(
                "[matrix] live matrix requires explicit --manager and "
                "--customer-name scopes",
                file=sys.stderr,
            )
            return 2
        edge = EdgeMatrixConfig(
            manager_name=selected_manager,
            customer_name=selected_customer,
            compact_customer_name=selected_customer,
            subscription_id=args.subscription_id,
            csone_upload_path=args.csone_upload_path,
        )
        matrix = build_exhaustive_option_matrix(
            days=max(min(int(args.days), 365), 1), edge=edge
        )
    blocks = parse_matrix_blocks(args.blocks)
    scenario_keys = select_matrix_scenario_keys(matrix, blocks, resume_from=args.resume_from)
    if not scenario_keys:
        print("[matrix] no scenarios selected", file=sys.stderr)
        return 4

    runner_args = argparse.Namespace(
        base_url=args.base_url,
        downloads_dir=args.downloads_dir,
        iterations=1,
        scenarios="all",
        poll_interval=args.poll_interval,
        timeout=args.timeout,
        request_timeout=args.request_timeout,
        download_timeout=args.download_timeout,
        run_id=args.run_id,
        stop_on_failure=args.stop_on_failure,
        baseline_mode=args.baseline_mode,
        baseline_manifest=args.baseline_manifest,
        init_baseline=False,
        init_baseline_dir="",
        init_baseline_label="",
        strict=args.strict,
        min_docx_similarity=args.min_docx_similarity,
        min_sheet_overlap=args.min_sheet_overlap,
        min_header_similarity=args.min_header_similarity,
        min_docx_chars=args.min_docx_chars,
        min_docx_numeric_similarity=args.min_docx_numeric_similarity,
        min_docx_table_numeric_similarity=args.min_docx_table_numeric_similarity,
        max_xlsx_row_delta_ratio=args.max_xlsx_row_delta_ratio,
        max_xlsx_row_delta_abs=args.max_xlsx_row_delta_abs,
    )
    config = build_runner_config(runner_args)
    config.scenario_keys = scenario_keys

    print(
        f"[matrix] starting {len(scenario_keys)} scenario(s) blocks={','.join(blocks)} days={args.days}"
    )
    summary = run_option_matrix(config, matrix, scenario_keys)
    _attach_scenario_inventory(summary, scenario_keys)
    if args.local_acceptance:
        summary["local_acceptance_scenario"] = str(
            connectivity.get("scenario") or ""
        )

    source_consistency = _cross_report_source_consistency(
        summary,
        max_freshness_skew_seconds=(0 if args.local_acceptance else 4 * 60 * 60),
        declared_cohorts=_declared_source_parity_cohorts(matrix, scenario_keys),
        artifacts_root=Path(args.downloads_dir).expanduser().resolve(),
    )
    summary["cross_report_source_consistency"] = source_consistency
    if source_consistency.get("ok") is not True:
        summary["all_passed"] = False
        summary["aborted"] = True

    if not args.skip_r114:
        # Emit the inverse gate explicitly. The parent acceptance runner rejects
        # a missing or non-boolean value rather than inferring that an omitted
        # ``skipped`` marker means the audit ran.
        summary["r114_audit_skipped"] = False
        audit_results: dict[str, Any] = {}
        successful_results = [
            result
            for result in summary.get("results", [])
            if isinstance(result, dict) and result.get("all_passed") is True
        ]
        audit_expected_keys = [
            str(result.get("scenario") or "") for result in successful_results
        ]
        for result in successful_results:
            scenario_key = str(result.get("scenario") or "")
            docx_paths: list[Path] = []
            xlsx_paths: list[Path] = []
            for artifact in result.get("artifacts") or []:
                if not isinstance(artifact, dict):
                    continue
                if artifact.get("file_type") == "docx":
                    docx_paths.append(
                        Path(str(artifact.get("debug_path") or ""))
                    )
                elif artifact.get("file_type") == "xlsx":
                    xlsx_paths.append(
                        Path(str(artifact.get("debug_path") or ""))
                    )
            if scenario_key in audit_results or not scenario_key:
                duplicate_key = scenario_key or "<missing-scenario-key>"
                audit_results[duplicate_key] = {
                    "ok": False,
                    "critical": True,
                    "reason": "audit_scenario_key_missing_or_duplicate",
                    "returncode": None,
                    "marker": None,
                }
                continue
            if len(docx_paths) != 1 or len(xlsx_paths) != 1:
                audit_results[scenario_key] = {
                    "ok": False,
                    "critical": True,
                    "reason": "artifact_pair_cardinality_invalid",
                    "docx_artifact_count": len(docx_paths),
                    "xlsx_artifact_count": len(xlsx_paths),
                    "returncode": None,
                    "marker": None,
                }
                continue
            audit_results[scenario_key] = _run_r114_audit(
                docx_paths[0],
                xlsx_paths[0],
                allowed_root=Path(args.downloads_dir).expanduser().resolve(),
            )
        summary["r114_audit"] = audit_results
        audit_completed_keys = list(audit_results)
        audit_inventory_exact = (
            audit_completed_keys == audit_expected_keys
            and len(audit_results) == len(successful_results)
        )
        summary["r114_audit_scenario_keys_expected"] = audit_expected_keys
        summary["r114_audit_scenario_count_expected"] = len(audit_expected_keys)
        summary["r114_audit_scenario_keys_completed"] = audit_completed_keys
        summary["r114_audit_scenario_count_completed"] = len(audit_completed_keys)
        summary["r114_audit_inventory_exact"] = audit_inventory_exact
        critical_hits = [
            key
            for key, result in audit_results.items()
            if result.get("ok") is not True
        ]
        summary["r114_critical_scenarios"] = critical_hits
        if critical_hits or not audit_inventory_exact:
            summary["all_passed"] = False
            summary["aborted"] = True
    else:
        summary["r114_audit_skipped"] = True

    summary_path = sorted(
        Path(config.downloads_dir).glob("AdoptIQ_ReportOptionMatrixSummary__*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if summary_path:
        summary_path[0].write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(f"[matrix] all_passed={summary.get('all_passed')} completed={summary.get('scenarios_completed')}")
    return 0 if summary.get("all_passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
