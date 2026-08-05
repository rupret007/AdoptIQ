#!/usr/bin/env python3
"""Verify a credential-free, corpus-free developer-only macOS candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from developer_candidate_security import (
    archive_inventory_contract,
    scan_pyinstaller_carchive,
    scan_tree,
    selected_resource_bytes,
    sha256_file,
    sha256_tree,
    validate_developer_payload,
)


REQUIRED_RESOURCES = (
    "DEVELOPER_ONLY_BUILD.txt",
    "DEVELOPER_BUILD_METADATA.json",
    "customer_aliases.defaults.json",
    "team_config.json",
)


def _run(command: Sequence[str]) -> tuple[dict[str, Any], str, str]:
    completed = subprocess.run(  # noqa: S603
        list(command),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    result = {
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "output_sha256": hashlib.sha256(
            combined.encode("utf-8", "replace")
        ).hexdigest(),
    }
    return result, completed.stdout, completed.stderr


def _read_config_identity(root: Path) -> tuple[str, str]:
    body = (root / "config.py").read_text(encoding="utf-8")
    version = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]+)"', body)
    build = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]+)"', body)
    if version is None or build is None:
        raise RuntimeError("config.py is missing canonical version/build metadata")
    return version.group(1), build.group(1)


def _resource_payloads(resources_dir: Path) -> dict[str, bytes]:
    entries: list[tuple[str, bytes]] = []
    for name in REQUIRED_RESOURCES:
        path = resources_dir / name
        if path.is_file():
            entries.append((name, path.read_bytes()))
    return selected_resource_bytes(entries)


def verify_app(
    app_path: Path,
    python_executable: str,
    *,
    expected_version: str = "",
    expected_build: str = "",
    expected_commit: str = "",
) -> dict[str, Any]:
    app = app_path.expanduser().resolve()
    contents = app / "Contents"
    resources_dir = contents / "Resources"
    info_path = contents / "Info.plist"
    executable = contents / "MacOS" / "AdoptIQ.bin"
    errors: list[str] = []

    metadata: dict[str, Any] = {}
    if not info_path.is_file():
        errors.append("Info.plist is missing")
    else:
        try:
            with info_path.open("rb") as handle:
                info = plistlib.load(handle)
            metadata = {
                "bundle_identifier": str(info.get("CFBundleIdentifier") or ""),
                "version": str(info.get("CFBundleShortVersionString") or ""),
                "build": str(info.get("CFBundleVersion") or ""),
            }
        except (OSError, plistlib.InvalidFileException):
            errors.append("Info.plist could not be read")
    if expected_version and metadata.get("version") != expected_version:
        errors.append("Info.plist version does not match the expected version")
    if expected_build and metadata.get("build") != expected_build:
        errors.append("Info.plist build does not match the expected build")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        errors.append("AdoptIQ.bin is missing or not executable")

    resources = _resource_payloads(resources_dir)
    developer_payload = validate_developer_payload(
        resources,
        expected_platform="macos",
        expected_version=expected_version,
        expected_build=expected_build,
        expected_commit=expected_commit,
    )
    if not developer_payload["ok"]:
        errors.append("sanitized developer payload verification failed")

    content_scan = scan_tree(app)
    if not content_scan["ok"]:
        errors.append("bundle content or credential scan failed")

    inventory_text = ""
    archive_check: dict[str, Any] = {
        "ok": False,
        "return_code": None,
        "output_sha256": "",
    }
    if executable.is_file():
        archive_check, stdout, stderr = _run(
            [
                python_executable,
                "-m",
                "PyInstaller.utils.cliutils.archive_viewer",
                "-r",
                "-b",
                str(executable),
            ]
        )
        inventory_text = stdout + stderr
        if not archive_check["ok"]:
            errors.append("PyInstaller archive inventory could not be read")
    archive_contract = archive_inventory_contract(inventory_text)
    if not archive_contract["ok"]:
        errors.append("PyInstaller archive module/security contract failed")

    archive_content_scan, _ = scan_pyinstaller_carchive(
        executable,
        require_developer_config_overlay=True,
    )
    if not archive_content_scan["ok"]:
        errors.append("PyInstaller archive content or credential scan failed")

    signature, _, _ = _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    if not signature["ok"]:
        errors.append("app signature verification failed")

    return {
        "ok": not errors,
        "app_bundle_sha256": sha256_tree(app),
        "metadata": metadata,
        "developer_payload": developer_payload,
        "content_scan": content_scan,
        "archive_inventory": archive_check,
        "archive_contract": archive_contract,
        "archive_content_scan": archive_content_scan,
        "signature": signature,
        "errors": errors,
    }


def _mounted_dmg_app(
    dmg: Path,
    python_executable: str,
    *,
    expected_version: str,
    expected_build: str,
    expected_commit: str,
) -> dict[str, Any]:
    integrity, _, _ = _run(["hdiutil", "verify", str(dmg)])
    result: dict[str, Any] = {
        "ok": False,
        "sha256": sha256_file(dmg),
        "bytes": dmg.stat().st_size,
        "integrity": integrity,
        "mount": None,
        "volume_content_scans": [],
        "embedded_app": None,
        "detach": None,
        "errors": [],
    }
    if not integrity["ok"]:
        result["errors"].append("DMG integrity verification failed")
        return result

    with tempfile.TemporaryDirectory(prefix="adoptiq-dmg-verify-") as mount_root:
        mount_check, stdout, _ = _run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-plist",
                "-mountroot",
                mount_root,
                str(dmg),
            ]
        )
        result["mount"] = mount_check
        mount_points: list[Path] = []
        try:
            attached = plistlib.loads(stdout.encode("utf-8"))
            for entity in attached.get("system-entities", []):
                mount_point = entity.get("mount-point")
                if mount_point:
                    mount_points.append(Path(mount_point))
        except (AttributeError, plistlib.InvalidFileException, ValueError):
            mount_points = []

        if not mount_check["ok"] or not mount_points:
            result["errors"].append("DMG could not be mounted read-only")
            return result

        try:
            volume_scans = [scan_tree(point) for point in mount_points]
            result["volume_content_scans"] = volume_scans
            if not all(scan["ok"] for scan in volume_scans):
                result["errors"].append(
                    "mounted DMG outer payload content or credential scan failed"
                )

            candidates = sorted(
                app
                for mount_point in mount_points
                for app in mount_point.rglob("AdoptIQ.app")
                if app.is_dir() and not app.is_symlink()
            )
            if len(candidates) != 1:
                result["errors"].append(
                    "DMG must contain exactly one non-symlink AdoptIQ.app"
                )
            else:
                embedded = verify_app(
                    candidates[0],
                    python_executable,
                    expected_version=expected_version,
                    expected_build=expected_build,
                    expected_commit=expected_commit,
                )
                result["embedded_app"] = embedded
                if not embedded["ok"]:
                    result["errors"].append("mounted DMG app verification failed")
        finally:
            detach_results = [_run(["hdiutil", "detach", str(point)])[0] for point in mount_points]
            result["detach"] = {
                "ok": all(item["ok"] for item in detach_results),
                "volumes": len(detach_results),
                "result_hashes": [item["output_sha256"] for item in detach_results],
            }
            if not result["detach"]["ok"]:
                result["errors"].append("DMG volume did not detach cleanly")

    result["ok"] = not result["errors"]
    return result


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    version, build = _read_config_identity(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=Path("dist/AdoptIQ.app"))
    parser.add_argument("--dmg", type=Path)
    parser.add_argument(
        "--skip-loose-app",
        action="store_true",
        help="Verify only the app mounted from --dmg (post-upload recheck).",
    )
    parser.add_argument("--summary", type=Path)
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
    app_result: dict[str, Any] | None = None
    if not args.skip_loose_app:
        app_result = verify_app(
            args.app,
            args.python,
            expected_version=args.expected_version,
            expected_build=args.expected_build,
            expected_commit=args.expected_commit,
        )
    dmg_result: dict[str, Any] | None = None
    if args.dmg is not None:
        dmg = args.dmg.expanduser().resolve()
        if dmg.is_file():
            dmg_result = _mounted_dmg_app(
                dmg,
                args.python,
                expected_version=args.expected_version,
                expected_build=args.expected_build,
                expected_commit=args.expected_commit,
            )
        else:
            dmg_result = {"ok": False, "error": "DMG is missing"}
    result = {
        "schema_version": "developer-candidate-verification/v2",
        "developer_only": True,
        "production_ready": False,
        "expected_identity": {
            "version": args.expected_version,
            "build": args.expected_build,
            "source_commit_sha": args.expected_commit,
        },
        "app": app_result,
        "dmg": dmg_result,
        "ok": bool(
            (app_result is None or app_result["ok"])
            and (dmg_result is None or dmg_result.get("ok"))
            and not (app_result is None and dmg_result is None)
        ),
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
