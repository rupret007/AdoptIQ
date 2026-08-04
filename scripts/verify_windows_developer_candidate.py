#!/usr/bin/env python3
"""Verify a credential-free, corpus-free Windows developer candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_MARKER = "DEVELOPER_ONLY_BUILD.txt"
FORBIDDEN_ARCHIVE_NAMES = (
    "_bundled_secrets",
    "secrets.env",
    "corpus.db.enc",
    "corpus.db.salt",
    "sentinel.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_candidate(exe_path: Path, python_executable: str) -> dict[str, Any]:
    exe = exe_path.expanduser().resolve()
    errors: list[str] = []
    inventory_text = ""
    inventory_return_code: int | None = None

    if not exe.is_file():
        errors.append("Windows executable is missing")
        size_bytes = 0
        exe_sha256 = ""
        pe_header_ok = False
    else:
        size_bytes = exe.stat().st_size
        exe_sha256 = _sha256(exe)
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

    inventory_folded = inventory_text.casefold()
    marker_present = REQUIRED_MARKER.casefold() in inventory_folded
    if not marker_present:
        errors.append("developer-only marker is missing from the archive")

    marker_source = (
        Path(__file__).resolve().parents[1]
        / "build"
        / "developer-only-marker"
        / REQUIRED_MARKER
    )
    marker_valid = bool(
        marker_source.is_file()
        and "Not production-ready" in marker_source.read_text(
            encoding="utf-8", errors="replace"
        )
    )
    if not marker_valid:
        errors.append("developer-only marker source is missing or invalid")

    forbidden_entries = sorted(
        name for name in FORBIDDEN_ARCHIVE_NAMES if name.casefold() in inventory_folded
    )
    if forbidden_entries:
        errors.append("forbidden credential or corpus resources are present")

    return {
        "schema_version": "windows-developer-candidate-verification/v1",
        "ok": not errors,
        "developer_only": True,
        "production_ready": False,
        "exe": {
            "bytes": size_bytes,
            "sha256": exe_sha256,
            "pe_header_ok": pe_header_ok,
            "marker_present": marker_present,
            "marker_valid": marker_valid,
            "forbidden_archive_entries": forbidden_entries,
            "archive_inventory": {
                "ok": inventory_return_code == 0,
                "return_code": inventory_return_code,
                "output_sha256": hashlib.sha256(
                    inventory_text.encode("utf-8", "replace")
                ).hexdigest(),
            },
            "errors": errors,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a developer-only AdoptIQ Windows executable."
    )
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    summary = verify_candidate(args.exe, args.python)
    summary_path = args.summary.expanduser().resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
