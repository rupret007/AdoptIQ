#!/usr/bin/env python3
"""Stage, checksum, and independently re-verify native release candidates."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from developer_candidate_security import resource_path_findings, scan_file, sha256_file


CHECKSUM_FILE = "CHECKSUMS.sha256"
PROVENANCE_FILE = "candidate_provenance.json"
SCHEMA_VERSION = "native-candidate-provenance/v1"
_SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([^/\\]+)$")


def _safe_clean_stage(stage_dir: Path) -> Path:
    target = stage_dir.expanduser().resolve()
    forbidden = {Path("/").resolve(), Path.cwd().resolve(), Path.home().resolve()}
    if target in forbidden or len(target.parts) < 3:
        raise ValueError("refusing to clean an unsafe staging directory")
    if stage_dir.is_symlink():
        raise ValueError("staging directory must not be a symlink")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def _tool_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def stage_candidate(
    stage_dir: Path,
    source_files: Sequence[Path],
    *,
    target_platform: str,
    version: str,
    build: str,
    source_commit_sha: str,
) -> dict[str, Any]:
    target = _safe_clean_stage(stage_dir)
    if target_platform not in {"macos", "windows"}:
        raise ValueError("target_platform must be macos or windows")
    if not version or not build or not source_commit_sha:
        raise ValueError("version, build, and source commit are required")

    copied: list[Path] = []
    names: set[str] = set()
    for raw_source in source_files:
        source = raw_source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"candidate staging input is missing: {source.name}")
        if source.name in names or source.name in {CHECKSUM_FILE, PROVENANCE_FILE}:
            raise ValueError(f"duplicate or reserved staged file name: {source.name}")
        names.add(source.name)
        destination = target / source.name
        shutil.copy2(source, destination)
        copied.append(destination)

    if not copied:
        raise ValueError("at least one candidate file must be staged")
    records = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(copied, key=lambda item: item.name.casefold())
    ]
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "target_platform": target_platform,
        "version": version,
        "build": build,
        "source_commit_sha": source_commit_sha,
        "developer_only": True,
        "production_ready": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "github": {
            "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
            "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        },
        "runner": {
            "name": os.environ.get("RUNNER_NAME", platform.node() or "unknown"),
            "os": os.environ.get("RUNNER_OS", platform.system()),
            "arch": os.environ.get("RUNNER_ARCH", platform.machine()),
        },
        "tools": {
            "python": platform.python_version(),
            "pyinstaller": _tool_version("pyinstaller"),
        },
        "files": records,
    }
    _atomic_write(
        target / PROVENANCE_FILE,
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
    )

    checksum_targets = sorted(
        [*copied, target / PROVENANCE_FILE], key=lambda item: item.name.casefold()
    )
    checksum_text = "".join(
        f"{sha256_file(path)}  {path.name}\n" for path in checksum_targets
    )
    _atomic_write(target / CHECKSUM_FILE, checksum_text)
    verified = verify_staged_candidate(
        target,
        expected_platform=target_platform,
        expected_version=version,
        expected_build=build,
        expected_commit=source_commit_sha,
    )
    if not verified["ok"]:
        raise RuntimeError("newly staged candidate failed self-verification")
    return verified


def _read_checksum_manifest(path: Path) -> tuple[dict[str, str], list[str]]:
    entries: dict[str, str] = {}
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}, ["checksum manifest is missing"]
    for line in lines:
        match = _SHA_LINE.fullmatch(line)
        if match is None:
            errors.append("checksum manifest contains an invalid line")
            continue
        digest, name = match.groups()
        if name in entries or name == CHECKSUM_FILE:
            errors.append("checksum manifest contains a duplicate or reserved name")
            continue
        entries[name] = digest
    if not entries:
        errors.append("checksum manifest contains no files")
    return entries, errors


def _scan_outer_payload_files(stage_dir: Path) -> dict[str, Any]:
    """Content-scan every flat artifact payload, including integrity files."""

    target = stage_dir.expanduser().resolve()
    findings: list[dict[str, str]] = []
    file_results: list[dict[str, Any]] = []
    bytes_scanned = 0
    if not target.is_dir():
        return {
            "ok": False,
            "files_scanned": 0,
            "bytes_scanned": 0,
            "files": [],
            "findings": [
                {"rule": "outer_payload_root_missing", "path": str(target)}
            ],
        }

    for candidate in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
        display_path = f"outer/{candidate.name}"
        candidate_findings = resource_path_findings(display_path)
        scanned_bytes = 0
        content_scanned = False
        if candidate.is_symlink():
            candidate_findings.append(
                {"rule": "outer_payload_symlink", "path": display_path}
            )
        elif not candidate.is_file():
            candidate_findings.append(
                {"rule": "outer_payload_not_regular_file", "path": display_path}
            )
        else:
            content_scan = scan_file(candidate, display_path=display_path)
            content_scanned = True
            scanned_bytes = int(content_scan.get("bytes_scanned") or 0)
            candidate_findings.extend(content_scan.get("findings") or [])
        bytes_scanned += scanned_bytes
        findings.extend(candidate_findings)
        file_results.append(
            {
                "name": candidate.name,
                "content_scanned": content_scanned,
                "bytes_scanned": scanned_bytes,
                "ok": not candidate_findings,
                "findings": candidate_findings,
            }
        )

    return {
        "ok": not findings,
        "files_scanned": sum(1 for item in file_results if item["content_scanned"]),
        "bytes_scanned": bytes_scanned,
        "files": file_results,
        "findings": findings,
    }


def verify_staged_candidate(
    stage_dir: Path,
    *,
    expected_platform: str,
    expected_version: str,
    expected_build: str,
    expected_commit: str,
) -> dict[str, Any]:
    target = stage_dir.expanduser().resolve()
    errors: list[str] = []
    checksums, manifest_errors = _read_checksum_manifest(target / CHECKSUM_FILE)
    errors.extend(manifest_errors)
    actual_files = sorted(path.name for path in target.iterdir()) if target.is_dir() else []
    expected_files = sorted([*checksums, CHECKSUM_FILE])
    if actual_files != expected_files:
        errors.append("staged candidate inventory does not match the checksum manifest")

    outer_payload_scan = _scan_outer_payload_files(target)
    if not outer_payload_scan["ok"]:
        errors.append("staged candidate outer payload security scan failed")

    file_results: list[dict[str, Any]] = []
    for name, expected_sha in sorted(checksums.items()):
        candidate = target / name
        actual_sha = (
            sha256_file(candidate)
            if candidate.is_file() and not candidate.is_symlink()
            else ""
        )
        ok = actual_sha == expected_sha
        file_results.append({"name": name, "sha256": actual_sha, "ok": ok})
        if not ok:
            errors.append(f"checksum mismatch: {name}")

    provenance: dict[str, Any] = {}
    try:
        loaded = json.loads((target / PROVENANCE_FILE).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            provenance = loaded
        else:
            errors.append("candidate provenance is not a JSON object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("candidate provenance is missing or invalid")

    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "target_platform": expected_platform,
        "version": expected_version,
        "build": expected_build,
        "source_commit_sha": expected_commit,
        "developer_only": True,
        "production_ready": False,
    }
    for key, expected in expected_identity.items():
        if provenance.get(key) != expected:
            errors.append(f"candidate provenance mismatch: {key}")

    provenance_records = provenance.get("files")
    expected_payload_names = sorted(
        name for name in checksums if name != PROVENANCE_FILE
    )
    recorded_names = sorted(
        str(record.get("name") or "")
        for record in provenance_records
        if isinstance(record, dict)
    ) if isinstance(provenance_records, list) else []
    if recorded_names != expected_payload_names:
        errors.append("candidate provenance payload inventory is incomplete")
    by_name = {
        str(record.get("name") or ""): record
        for record in provenance_records
        if isinstance(record, dict)
    } if isinstance(provenance_records, list) else {}
    for name in expected_payload_names:
        candidate = target / name
        record = by_name.get(name, {})
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or record.get("sha256") != sha256_file(candidate)
            or record.get("bytes") != candidate.stat().st_size
        ):
            errors.append(f"candidate provenance file record mismatch: {name}")

    return {
        "schema_version": "native-candidate-stage-verification/v1",
        "ok": not errors,
        "stage_dir": str(target),
        "file_count": len(file_results),
        "files": file_results,
        "outer_payload_scan": outer_payload_scan,
        "identity": expected_identity,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("stage", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--stage-dir", required=True, type=Path)
        child.add_argument("--platform", required=True, choices=("macos", "windows"))
        child.add_argument("--version", required=True)
        child.add_argument("--build", required=True)
        child.add_argument("--commit", required=True)
        if command == "stage":
            child.add_argument("--file", action="append", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stage":
        result = stage_candidate(
            args.stage_dir,
            args.file,
            target_platform=args.platform,
            version=args.version,
            build=args.build,
            source_commit_sha=args.commit,
        )
    else:
        result = verify_staged_candidate(
            args.stage_dir,
            expected_platform=args.platform,
            expected_version=args.version,
            expected_build=args.build,
            expected_commit=args.commit,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
