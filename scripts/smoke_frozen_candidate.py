#!/usr/bin/env python3
"""Start a frozen AdoptIQ candidate and exercise its loopback health contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import signal
import socket
import stat
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
    payload: Any,
    *,
    expected_version: str,
    expected_build: str,
    require_restart_ready: bool = False,
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
    if require_restart_ready and payload.get("restart_required") is not False:
        errors.append("running candidate did not report restart_required=false")
    return errors


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_identity(path: Path) -> tuple[str, int]:
    """Digest a candidate directory's paths, types, modes, links, and bytes."""
    digest = hashlib.sha256()
    total_size = 0
    for entry in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        relative = entry.relative_to(path).as_posix().encode("utf-8", "surrogateescape")
        metadata = os.lstat(entry)
        if stat.S_ISLNK(metadata.st_mode):
            kind = b"L"
        elif stat.S_ISDIR(metadata.st_mode):
            kind = b"D"
        elif stat.S_ISREG(metadata.st_mode):
            kind = b"F"
        else:
            kind = b"O"
        digest.update(kind)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.S_IMODE(metadata.st_mode).to_bytes(4, "big"))
        if kind == b"L":
            target = os.readlink(entry).encode("utf-8", "surrogateescape")
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
        elif kind == b"F":
            total_size += int(metadata.st_size)
            digest.update(metadata.st_size.to_bytes(8, "big"))
            with entry.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest(), total_size


def candidate_identity(candidate: Path) -> dict[str, Any]:
    """Return the exact path/hash/size identity of a regular candidate."""
    expanded = candidate.expanduser()
    if expanded.is_symlink():
        raise RuntimeError("candidate must not be a symlink")
    resolved = expanded.resolve()
    if resolved.is_file():
        digest = _sha256_file(resolved)
        size = int(resolved.stat().st_size)
        kind = "file"
    elif resolved.is_dir():
        digest, size = _directory_identity(resolved)
        kind = "directory"
    else:
        raise RuntimeError("candidate is missing or is not a regular file/directory")
    return {
        "candidate_path": str(resolved),
        "candidate_kind": kind,
        "candidate_sha256": digest,
        "candidate_size_bytes": size,
    }


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _zero_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value == 0


def validate_release_corpus_payload(payload: Any) -> list[str]:
    """Validate the fail-closed packaged corpus + hybrid retrieval contract."""
    if not isinstance(payload, dict):
        return ["corpus endpoint did not return a JSON object"]
    errors: list[str] = []
    boot = payload.get("boot")
    corpus = payload.get("corpus")
    if payload.get("ok") is not True:
        errors.append("corpus endpoint did not report ok=true")
    if payload.get("enabled") is not True:
        errors.append("packaged corpus is not enabled")
    if payload.get("available") is not True:
        errors.append("packaged corpus is not available")
    if not isinstance(boot, dict):
        errors.append("corpus boot evidence is missing")
        boot = {}
    if boot.get("completed") is not True or boot.get("in_progress") is not False:
        errors.append("corpus bootstrap is not complete")
    if boot.get("last_error") not in (None, ""):
        errors.append("corpus bootstrap reports an error")
    if str(boot.get("source") or "").casefold() != "baked":
        errors.append("corpus was not installed from this candidate's bundled snapshot")
    if str(boot.get("embedder_status") or "").casefold() != "ready":
        errors.append("embedding model is not ready")
    if str(boot.get("reranker_status") or "").casefold() != "ready":
        errors.append("reranker model is not ready")
    if str(boot.get("dense_retrieval_status") or "").casefold() != "ready":
        errors.append("dense retrieval is not ready")
    if not _zero_int(boot.get("dense_rows_remaining")):
        errors.append("dense vector backlog is not zero")
    if str(boot.get("ask_ai_retrieval_method") or "").casefold() != "hybrid":
        errors.append("Ask AI retrieval method is not hybrid")
    if not isinstance(corpus, dict):
        errors.append("corpus inventory evidence is missing")
        corpus = {}
    for field in ("files_total", "files_parsed", "customers", "chunks"):
        if not _positive_int(corpus.get(field)):
            errors.append(f"corpus {field} is not positive")
    return errors


def _corpus_evidence(payload: Any) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    boot = body.get("boot") if isinstance(body.get("boot"), dict) else {}
    corpus = body.get("corpus") if isinstance(body.get("corpus"), dict) else {}
    return {
        "available": body.get("available") is True,
        "boot_completed": boot.get("completed") is True,
        "boot_in_progress": boot.get("in_progress") is True,
        "boot_source": str(boot.get("source") or "").casefold(),
        "embedder_status": str(boot.get("embedder_status") or "").casefold(),
        "reranker_status": str(boot.get("reranker_status") or "").casefold(),
        "dense_retrieval_status": str(
            boot.get("dense_retrieval_status") or ""
        ).casefold(),
        "dense_rows_remaining": boot.get("dense_rows_remaining"),
        "ask_ai_retrieval_method": str(
            boot.get("ask_ai_retrieval_method") or ""
        ).casefold(),
        "files_total": corpus.get("files_total"),
        "files_parsed": corpus.get("files_parsed"),
        "customers": corpus.get("customers"),
        "chunks": corpus.get("chunks"),
    }


def _isolated_candidate_environment(
    *,
    port: int,
    runtime_root: Path,
) -> dict[str, str]:
    """Return a clean-room environment for packaged-candidate evidence.

    A frozen smoke must prove the candidate's bundled corpus and model bytes. It
    must never inherit a decryptable corpus, report-output corpus, persisted
    OneDrive setting, or model cache from the operator account running the test.
    """
    root = runtime_root.expanduser().resolve()
    home = root / "home"
    appdata = root / "appdata"
    local_appdata = root / "local-appdata"
    knowledge = root / "knowledge"
    outputs = root / "outputs"
    model_cache = root / "model-cache"
    unavailable_onedrive = root / "unavailable-onedrive"
    for directory in (home, appdata, local_appdata, knowledge, outputs, model_cache):
        directory.mkdir(parents=True, mode=0o700)

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "ADOPTIQ_KNOWLEDGE_DIR": str(knowledge),
            "ADOPTIQ_OUTPUTS_DIR": str(outputs),
            "CSONE_ONEDRIVE_FOLDER": str(unavailable_onedrive),
            "ADOPTIQ_PORT": str(port),
            "ADOPTIQ_BIND_HOST": "127.0.0.1",
            "ADOPTIQ_LAUNCHER_SPLASH_SHOWN": "1",
            "ADOPTIQ_SECRET_KEY": "native-candidate-smoke-main-key-000000000000000000000000",
            "ADOPTIQ_ADMIN_SECRET_KEY": "native-candidate-smoke-admin-key-0000000000000000000000",
            # Prove the packaged model payload is sufficient. Inherited
            # work-machine caches could otherwise make a model-less DMG
            # appear healthy during smoke.
            "ADOPTIQ_FASTEMBED_CACHE": str(model_cache),
            "FASTEMBED_CACHE_PATH": str(model_cache),
            "HF_HOME": str(root / "huggingface"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    return environment


def _poll_release_corpus(
    base_url: str,
    *,
    timeout: float,
    process: subprocess.Popen[bytes],
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, timeout)
    last_payload: Any = None
    last_status = 0
    last_error = ""
    while True:
        last_status, last_payload, last_error = _json_response(
            f"{base_url}/api/corpus/status"
        )
        boot = (
            last_payload.get("boot")
            if isinstance(last_payload, dict) and isinstance(last_payload.get("boot"), dict)
            else {}
        )
        terminal = boot.get("completed") is True and boot.get("in_progress") is False
        terminal_error = bool(boot.get("last_error")) and boot.get("in_progress") is False
        if last_status == 200 and not last_error and (terminal or terminal_error):
            errors = validate_release_corpus_payload(last_payload)
            pending_model_errors: set[str] = set()
            if not str(boot.get("embedder_status") or "").strip():
                pending_model_errors.add("embedding model is not ready")
            if not str(boot.get("reranker_status") or "").strip():
                pending_model_errors.add("reranker model is not ready")
            waiting_for_async_models = bool(pending_model_errors) and set(errors).issubset(
                pending_model_errors
            )
            if not waiting_for_async_models:
                return {
                    "ok": not errors,
                    "status": last_status,
                    "error": last_error,
                    "errors": errors,
                    "release_requirements_enforced": True,
                    "evidence": _corpus_evidence(last_payload),
                }
        if process.poll() is not None:
            return {
                "ok": False,
                "status": last_status,
                "error": "candidate_exited",
                "errors": ["candidate exited before corpus bootstrap completed"],
                "release_requirements_enforced": True,
                "evidence": _corpus_evidence(last_payload),
            }
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "status": last_status,
                "error": last_error or "bootstrap_timeout",
                "errors": ["corpus bootstrap did not complete before timeout"],
                "release_requirements_enforced": True,
                "evidence": _corpus_evidence(last_payload),
            }
        time.sleep(1.0)


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
    require_release_corpus: bool = False,
    corpus_timeout: float = 300.0,
) -> dict[str, Any]:
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    result: dict[str, Any] = {
        "schema_version": "frozen-candidate-smoke/v1",
        "ok": False,
        "expected_version": expected_version,
        "expected_build": expected_build,
        "frozen_required": True,
        "release_corpus_required": bool(require_release_corpus),
        "startup_seconds": None,
        "checks": {},
        "errors": [],
    }

    try:
        initial_identity = candidate_identity(candidate)
        result.update(initial_identity)
        with _candidate_executable(candidate) as executable, tempfile.TemporaryDirectory(
            prefix="adoptiq-frozen-smoke-"
        ) as isolated_runtime_root:
            if not executable.is_file():
                result["errors"].append("candidate executable is missing")
                return result
            environment = _isolated_candidate_environment(
                port=port,
                runtime_root=Path(isolated_runtime_root),
            )
            result["offline_model_probe"] = True
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
                    require_restart_ready=True,
                )
                if version_status != 200 or version_error:
                    version_errors.append("version endpoint was unavailable or invalid")
                result["checks"]["version"] = {
                    "ok": not version_errors,
                    "status": version_status,
                    "errors": version_errors,
                    "actual": {
                        "version": (
                            str(version_payload.get("version") or "")
                            if isinstance(version_payload, dict)
                            else ""
                        ),
                        "build": (
                            str(version_payload.get("build") or "")
                            if isinstance(version_payload, dict)
                            else ""
                        ),
                        "frozen": (
                            version_payload.get("frozen") is True
                            if isinstance(version_payload, dict)
                            else False
                        ),
                        "restart_required": (
                            version_payload.get("restart_required")
                            if isinstance(version_payload, dict)
                            else None
                        ),
                    },
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

                if require_release_corpus:
                    result["checks"]["corpus"] = _poll_release_corpus(
                        base_url,
                        timeout=corpus_timeout,
                        process=process,
                    )
                else:
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
                        "release_requirements_enforced": False,
                    }

                final_identity = candidate_identity(candidate)
                identity_matches = final_identity == initial_identity
                result["checks"]["candidate_integrity"] = {
                    "ok": identity_matches,
                    "identity_unchanged": identity_matches,
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
    parser.add_argument(
        "--require-release-corpus",
        action="store_true",
        help="Wait for and require a non-empty hybrid-ready packaged corpus.",
    )
    parser.add_argument("--corpus-timeout", type=float, default=300.0)
    parser.add_argument("--summary", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = smoke_candidate(
        args.candidate,
        expected_version=args.expected_version,
        expected_build=args.expected_build,
        startup_timeout=max(1.0, args.startup_timeout),
        require_release_corpus=args.require_release_corpus,
        corpus_timeout=max(1.0, args.corpus_timeout),
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
