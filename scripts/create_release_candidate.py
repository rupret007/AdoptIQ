#!/usr/bin/env python3
"""Create and self-verify one immutable AdoptIQ release-candidate manifest.

The command hashes bytes only.  It does not mount, inspect, scan, copy, or
otherwise interpret the native artifact or its sidecars.  The output is
created once with exclusive semantics and is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from release_candidate_contract import (  # noqa: E402
    SCHEMA_VERSION,
    ReleaseCandidateContractError,
    _hash_regular_file,
    load_release_candidate_manifest,
    validate_release_candidate_manifest,
    verify_release_candidate,
)


def _require_real_directory(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} is missing or unreadable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseCandidateContractError(f"{label} must be a real non-symlink directory")
    return path


def _direct_child(path: Path, *, output_root: Path, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.absolute()
    if candidate.parent != output_root:
        raise ReleaseCandidateContractError(f"{label} must be a direct child of the manifest directory")
    return candidate


def _sidecar_path(raw_path: Path, *, output_root: Path) -> Path:
    expanded = raw_path.expanduser()
    if not expanded.is_absolute() and expanded.parent == Path("."):
        expanded = output_root / expanded
    return _direct_child(expanded, output_root=output_root, label="candidate sidecar")


def _canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _remove_created_output(path: Path, *, device: int, inode: int) -> None:
    """Remove only the exact regular output inode this process just created."""

    try:
        metadata = path.lstat()
    except OSError:
        return
    if stat.S_ISREG(metadata.st_mode) and (metadata.st_dev, metadata.st_ino) == (device, inode):
        try:
            path.unlink()
        except OSError:
            pass


def create_release_candidate_manifest(
    *,
    artifact_path: Path,
    sidecar_paths: Sequence[Path],
    output_path: Path,
    platform_name: str,
    version: str,
    build: int,
    source_commit_sha: str,
    built_at_utc: str,
) -> Path:
    """Create a canonical eligible-candidate manifest and verify exact bytes."""

    output = output_path.expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    output = output.absolute()
    output_root = _require_real_directory(output.parent, label="manifest directory")
    if output.name != "candidate.json":
        raise ReleaseCandidateContractError("manifest output name must be candidate.json")
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ReleaseCandidateContractError("manifest output could not be checked safely") from exc
    else:
        raise ReleaseCandidateContractError("manifest output already exists; refusing to overwrite it")

    artifact = artifact_path.expanduser()
    if not artifact.is_absolute():
        artifact = Path.cwd() / artifact
    artifact = artifact.absolute()
    artifact_size, artifact_sha256 = _hash_regular_file(artifact, label="candidate artifact")

    if not sidecar_paths:
        raise ReleaseCandidateContractError("at least one candidate sidecar is required")
    sidecar_records: list[dict[str, str]] = []
    for raw_path in sidecar_paths:
        sidecar = _sidecar_path(raw_path, output_root=output_root)
        _, sidecar_sha256 = _hash_regular_file(sidecar, label=f"candidate sidecar {sidecar.name}")
        sidecar_records.append({"path": sidecar.name, "sha256": sidecar_sha256})
    sidecar_records.sort(key=lambda value: value["path"].casefold())

    manifest = validate_release_candidate_manifest(
        {
            "schema_version": SCHEMA_VERSION,
            "release_status": "eligible",
            "platform": platform_name,
            "version": version,
            "build": build,
            "source_commit_sha": source_commit_sha,
            "artifact": {
                "name": artifact.name,
                "sha256": artifact_sha256,
                "size_bytes": artifact_size,
            },
            "built_at_utc": built_at_utc,
            "sidecars": sidecar_records,
        }
    )
    body = _canonical_json_bytes(manifest.to_public_dict())

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o644)
    except OSError as exc:
        raise ReleaseCandidateContractError("manifest output could not be created exclusively") from exc

    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())

        loaded = load_release_candidate_manifest(output)
        if loaded.to_public_dict() != manifest.to_public_dict():
            raise ReleaseCandidateContractError("created manifest did not reload with its exact canonical identity")
        verify_release_candidate(output, artifact)
    except BaseException:
        _remove_created_output(output, device=created.st_dev, inode=created.st_ino)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path, help="Exact native artifact to hash")
    parser.add_argument(
        "--sidecar",
        action="append",
        required=True,
        type=Path,
        help="Regular sidecar in the manifest directory; repeat for each sidecar",
    )
    parser.add_argument("--output", required=True, type=Path, help="New candidate.json path")
    parser.add_argument("--platform", required=True, choices=("macos", "windows"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True, type=int)
    parser.add_argument("--source-commit-sha", required=True)
    parser.add_argument("--built-at-utc", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = create_release_candidate_manifest(
            artifact_path=args.artifact,
            sidecar_paths=args.sidecar,
            output_path=args.output,
            platform_name=args.platform,
            version=args.version,
            build=args.build,
            source_commit_sha=args.source_commit_sha,
            built_at_utc=args.built_at_utc,
        )
    except (OSError, ReleaseCandidateContractError, ValueError) as exc:
        print(f"Release candidate manifest was not created: {exc}", file=sys.stderr)
        return 2
    print(f"Created and verified eligible release candidate manifest: {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
