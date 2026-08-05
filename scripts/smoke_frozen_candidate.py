#!/usr/bin/env python3
"""Start a frozen AdoptIQ candidate and exercise its loopback health contract."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def _read_config_identity(root: Path) -> tuple[str, str]:
    body = (root / "config.py").read_text(encoding="utf-8")
    version = re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]+)"', body)
    build = re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]+)"', body)
    if version is None or build is None:
        raise RuntimeError("config.py is missing canonical version/build metadata")
    return version.group(1), build.group(1)


def validate_version_payload(
    payload: Any, *, expected_version: str, expected_build: str
) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["version endpoint did not return a JSON object"]
    if payload.get("ok") is not True:
        errors.append("version endpoint did not report ok=true")
    if payload.get("frozen") is not True:
        errors.append("version endpoint did not report frozen=true")
    if str(payload.get("version") or "") != expected_version:
        errors.append("running version does not match the expected version")
    if str(payload.get("build") or "") != expected_build:
        errors.append("running build does not match the expected build")
    return errors


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _get(url: str, *, timeout: float = 10.0) -> tuple[int, bytes, str]:
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        # The caller constructs every URL from a fixed http://127.0.0.1 base;
        # no user-controlled scheme or host reaches this helper.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            request, timeout=timeout
        ) as response:
            return int(response.status), response.read(), ""
    except (OSError, urllib.error.URLError) as exc:
        return 0, b"", type(exc).__name__


def _json_response(url: str) -> tuple[int, Any, str]:
    status, body, error = _get(url)
    if error:
        return status, None, error
    try:
        return status, json.loads(body.decode("utf-8")), ""
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None, "invalid_json"


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


@contextmanager
def _candidate_executable(candidate: Path) -> Iterator[Path]:
    path = candidate.expanduser().resolve()
    if path.suffix.casefold() != ".dmg":
        if path.suffix.casefold() == ".app" or path.name.endswith(".app"):
            yield path / "Contents" / "MacOS" / "AdoptIQ.bin"
        else:
            yield path
        return

    if sys.platform != "darwin":
        raise RuntimeError("DMG smoke testing requires macOS")
    with tempfile.TemporaryDirectory(prefix="adoptiq-dmg-smoke-") as mount_root:
        attached = subprocess.run(  # noqa: S603
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-plist",
                "-mountroot",
                mount_root,
                str(path),
            ],
            capture_output=True,
            check=False,
        )
        if attached.returncode != 0:
            raise RuntimeError("DMG could not be mounted read-only")
        try:
            plist = plistlib.loads(attached.stdout)
            mount_points = [
                Path(entity["mount-point"])
                for entity in plist.get("system-entities", [])
                if entity.get("mount-point")
            ]
        except (AttributeError, KeyError, plistlib.InvalidFileException, ValueError) as exc:
            raise RuntimeError("DMG mount metadata was invalid") from exc
        try:
            apps = sorted(
                app
                for mount_point in mount_points
                for app in mount_point.rglob("AdoptIQ.app")
                if app.is_dir() and not app.is_symlink()
            )
            if len(apps) != 1:
                raise RuntimeError("DMG must contain exactly one AdoptIQ.app")
            yield apps[0] / "Contents" / "MacOS" / "AdoptIQ.bin"
        finally:
            for mount_point in mount_points:
                subprocess.run(  # noqa: S603
                    ["hdiutil", "detach", str(mount_point)],
                    capture_output=True,
                    check=False,
                )


def smoke_candidate(
    candidate: Path,
    *,
    expected_version: str,
    expected_build: str,
    startup_timeout: float,
) -> dict[str, Any]:
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    result: dict[str, Any] = {
        "schema_version": "frozen-candidate-smoke/v1",
        "ok": False,
        "expected_version": expected_version,
        "expected_build": expected_build,
        "frozen_required": True,
        "startup_seconds": None,
        "checks": {},
        "errors": [],
    }

    try:
        with _candidate_executable(candidate) as executable:
            if not executable.is_file():
                result["errors"].append("candidate executable is missing")
                return result
            environment = os.environ.copy()
            environment.update(
                {
                    "ADOPTIQ_PORT": str(port),
                    "ADOPTIQ_BIND_HOST": "127.0.0.1",
                    "ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "1",
                    "ADOPTIQ_SECRET_KEY": "native-candidate-smoke-main-key-000000000000000000000000",
                    "ADOPTIQ_ADMIN_SECRET_KEY": "native-candidate-smoke-admin-key-0000000000000000000000",
                }
            )
            popen_kwargs: dict[str, Any] = {
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True
            started = time.monotonic()
            process = subprocess.Popen([str(executable)], **popen_kwargs)  # noqa: S603
            try:
                ready = False
                while time.monotonic() - started < startup_timeout:
                    if process.poll() is not None:
                        result["errors"].append(
                            f"candidate exited during startup with code {process.returncode}"
                        )
                        break
                    status, body, _ = _get(f"{base_url}/ping", timeout=2.0)
                    if status == 200 and body.strip() == b"OK":
                        ready = True
                        result["startup_seconds"] = round(time.monotonic() - started, 3)
                        break
                    time.sleep(1.0)
                if not ready and not result["errors"]:
                    result["errors"].append("candidate did not become ready before timeout")
                if not ready:
                    return result

                result["checks"]["ping"] = {"ok": True, "status": 200}
                home_status, _, home_error = _get(f"{base_url}/")
                result["checks"]["home"] = {
                    "ok": home_status == 200 and not home_error,
                    "status": home_status,
                    "error": home_error,
                }

                version_status, version_payload, version_error = _json_response(
                    f"{base_url}/api/version"
                )
                version_errors = validate_version_payload(
                    version_payload,
                    expected_version=expected_version,
                    expected_build=expected_build,
                )
                if version_status != 200 or version_error:
                    version_errors.append("version endpoint was unavailable or invalid")
                result["checks"]["version"] = {
                    "ok": not version_errors,
                    "status": version_status,
                    "errors": version_errors,
                }

                status_code, status_payload, status_error = _json_response(
                    f"{base_url}/api/status/all"
                )
                result["checks"]["status"] = {
                    "ok": status_code == 200
                    and isinstance(status_payload, dict)
                    and not status_error,
                    "status": status_code,
                    "error": status_error,
                }

                corpus_code, corpus_payload, corpus_error = _json_response(
                    f"{base_url}/api/corpus/status"
                )
                result["checks"]["corpus"] = {
                    "ok": corpus_code == 200
                    and isinstance(corpus_payload, dict)
                    and "boot" in corpus_payload
                    and not corpus_error,
                    "status": corpus_code,
                    "error": corpus_error,
                }
                failed = [
                    name for name, check in result["checks"].items() if not check["ok"]
                ]
                if failed:
                    result["errors"].append(
                        "runtime endpoint checks failed: " + ", ".join(sorted(failed))
                    )
                result["ok"] = not result["errors"]
                return result
            finally:
                _terminate_process_tree(process)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        result["errors"].append(f"candidate smoke setup failed: {type(exc).__name__}")
        return result


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    version, build = _read_config_identity(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--expected-version", default=version)
    parser.add_argument("--expected-build", default=build)
    parser.add_argument("--startup-timeout", type=float, default=180.0)
    parser.add_argument("--summary", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = smoke_candidate(
        args.candidate,
        expected_version=args.expected_version,
        expected_build=args.expected_build,
        startup_timeout=max(1.0, args.startup_timeout),
    )
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
