#!/usr/bin/env python3
"""Verify a credential-free, corpus-free developer-only Mac candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


FORBIDDEN_RESOURCE_NAMES = {
    "_bundled_secrets.py",
    "_bundled_secrets.pyc",
    "corpus.db.enc",
    "corpus.db.salt",
    "sentinel.json",
    "secrets.env",
    ".env",
}
REQUIRED_MARKER = "DEVELOPER_ONLY_BUILD.txt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: Sequence[str]) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "output_sha256": hashlib.sha256(
            (completed.stdout + completed.stderr).encode("utf-8", "replace")
        ).hexdigest(),
    }


def verify_app(app_path: Path, python_executable: str) -> dict[str, Any]:
    app = app_path.expanduser().resolve()
    contents = app / "Contents"
    info_path = contents / "Info.plist"
    executable = contents / "MacOS" / "AdoptIQ.bin"
    marker = contents / "Resources" / REQUIRED_MARKER
    errors: list[str] = []

    if not info_path.is_file():
        errors.append("Info.plist is missing")
        metadata: dict[str, Any] = {}
    else:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
        metadata = {
            "bundle_identifier": str(info.get("CFBundleIdentifier") or ""),
            "version": str(info.get("CFBundleShortVersionString") or ""),
            "build": str(info.get("CFBundleVersion") or ""),
        }
    if not executable.is_file() or not os.access(executable, os.X_OK):
        errors.append("AdoptIQ.bin is missing or not executable")
    if not marker.is_file() or "Not production-ready" not in marker.read_text(
        encoding="utf-8", errors="replace"
    ):
        errors.append("developer-only marker is missing or invalid")

    forbidden_paths = sorted(
        str(path.relative_to(app))
        for path in app.rglob("*")
        if path.is_file() and path.name in FORBIDDEN_RESOURCE_NAMES
    )
    if forbidden_paths:
        errors.append("forbidden credential or corpus resources are present")

    archive_check: dict[str, Any] = {"ok": False, "return_code": None, "output_sha256": ""}
    archive_forbidden: list[str] = []
    if executable.is_file():
        completed = subprocess.run(  # noqa: S603
            [
                python_executable,
                "-m",
                "PyInstaller.utils.cliutils.archive_viewer",
                "-r",
                "-b",
                str(executable),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        archive_text = completed.stdout + completed.stderr
        archive_check = {
            "ok": completed.returncode == 0,
            "return_code": completed.returncode,
            "output_sha256": hashlib.sha256(
                archive_text.encode("utf-8", "replace")
            ).hexdigest(),
        }
        archive_forbidden = [
            name
            for name in ("_bundled_secrets", "corpus.db.enc", "corpus.db.salt")
            if name.casefold() in archive_text.casefold()
        ]
        if not archive_check["ok"]:
            errors.append("PyInstaller archive inventory could not be read")
        if archive_forbidden:
            errors.append("forbidden modules or corpus artifacts are present in the archive")

    signature = _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    if not signature["ok"]:
        errors.append("app signature verification failed")
    return {
        "ok": not errors,
        "app_path_sha256": hashlib.sha256(str(app).encode()).hexdigest(),
        "metadata": metadata,
        "marker_present": marker.is_file(),
        "forbidden_path_count": len(forbidden_paths),
        "forbidden_archive_entries": archive_forbidden,
        "archive_inventory": archive_check,
        "signature": signature,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("dist/AdoptIQ.app"))
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--python", default=sys.executable)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app_result = verify_app(args.app, args.python)
    dmg_result: dict[str, Any] | None = None
    if args.dmg is not None:
        dmg = args.dmg.expanduser().resolve()
        if dmg.is_file():
            verify = _run(["hdiutil", "verify", str(dmg)])
            dmg_result = {
                "ok": verify["ok"],
                "sha256": _sha256(dmg),
                "bytes": dmg.stat().st_size,
                "integrity": verify,
            }
        else:
            dmg_result = {"ok": False, "error": "DMG is missing"}
    result = {
        "schema_version": "developer-candidate-verification/v1",
        "developer_only": True,
        "production_ready": False,
        "app": app_result,
        "dmg": dmg_result,
        "ok": bool(app_result["ok"] and (dmg_result is None or dmg_result.get("ok"))),
    }
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.summary:
        summary = args.summary.expanduser().resolve()
        summary.parent.mkdir(parents=True, exist_ok=True)
        temporary = summary.with_name(f".{summary.name}.{os.getpid()}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, summary)
    print(serialized, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
