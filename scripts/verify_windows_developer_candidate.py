#!/usr/bin/env python3
"""Verify a credential-free, corpus-free Windows developer candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from developer_candidate_security import (
    archive_inventory_contract,
    scan_file,
    scan_pyinstaller_carchive,
    sha256_file,
    validate_developer_payload,
)


def _read_config_identity(root: Path) -> tuple[str, str]:
    body = (root / "config.py").read_text(encoding="utf-8")
    version = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]+)"', body)
    build = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]+)"', body)
    if version is None or build is None:
        raise RuntimeError("config.py is missing canonical version/build metadata")
    return version.group(1), build.group(1)


def verify_candidate(
    exe_path: Path,
    python_executable: str,
    *,
    expected_version: str = "",
    expected_build: str = "",
    expected_commit: str = "",
) -> dict[str, Any]:
    exe = exe_path.expanduser().resolve()
    errors: list[str] = []
    inventory_text = ""
    inventory_return_code: int | None = None
    size_bytes = 0
    exe_sha256 = ""
    pe_header_ok = False

    if not exe.is_file():
        errors.append("Windows executable is missing")
    else:
        size_bytes = exe.stat().st_size
        exe_sha256 = sha256_file(exe)
        with exe.open("rb") as handle:
            pe_header_ok = handle.read(2) == b"MZ"
        if not pe_header_ok:
            errors.append("Windows executable does not have an MZ header")

        completed = subprocess.run(  # noqa: S603
            [
                python_executable,
                "-m",
                "PyInstaller.utils.cliutils.archive_viewer",
                "-r",
                "-b",
                str(exe),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        inventory_return_code = completed.returncode
        inventory_text = completed.stdout + completed.stderr
        if completed.returncode != 0:
            errors.append("PyInstaller archive inventory failed")

    archive_contract = archive_inventory_contract(inventory_text)
    if not archive_contract["ok"]:
        errors.append("PyInstaller archive module/security contract failed")

    raw_content_scan = scan_file(exe, display_path="AdoptIQ.exe")
    if not raw_content_scan["ok"]:
        errors.append("executable content or credential scan failed")

    archive_content_scan, resources = scan_pyinstaller_carchive(
        exe,
        require_developer_config_overlay=True,
    )
    if not archive_content_scan["ok"]:
        errors.append("archive content or credential scan failed")

    developer_payload = validate_developer_payload(
        resources,
        expected_platform="windows",
        expected_version=expected_version,
        expected_build=expected_build,
        expected_commit=expected_commit,
    )
    if not developer_payload["ok"]:
        errors.append("sanitized developer payload verification failed")

    return {
        "schema_version": "windows-developer-candidate-verification/v2",
        "ok": not errors,
        "developer_only": True,
        "production_ready": False,
        "expected_identity": {
            "version": expected_version,
            "build": expected_build,
            "source_commit_sha": expected_commit,
        },
        "exe": {
            "bytes": size_bytes,
            "sha256": exe_sha256,
            "pe_header_ok": pe_header_ok,
            "archive_inventory": {
                "ok": inventory_return_code == 0,
                "return_code": inventory_return_code,
                "output_sha256": hashlib.sha256(
                    inventory_text.encode("utf-8", "replace")
                ).hexdigest(),
            },
            "archive_contract": archive_contract,
            "raw_content_scan": raw_content_scan,
            "archive_content_scan": archive_content_scan,
            "developer_payload": developer_payload,
            "errors": errors,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    version, build = _read_config_identity(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--expected-version", default=version)
    parser.add_argument("--expected-build", default=build)
    parser.add_argument(
        "--expected-commit",
        default=os.environ.get("GITHUB_SHA")
        or os.environ.get("ADOPTIQ_SOURCE_COMMIT")
        or "local-uncommitted",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = verify_candidate(
        args.exe,
        args.python,
        expected_version=args.expected_version,
        expected_build=args.expected_build,
        expected_commit=args.expected_commit,
    )
    summary_path = args.summary.expanduser().resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = summary_path.with_name(f".{summary_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
