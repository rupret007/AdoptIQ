#!/usr/bin/env python3
"""Load and verify immutable AdoptIQ native release-candidate manifests.

The manifest is an identity contract, not a release approval.  Validation is
deliberately strict so malformed, ambiguous, or partially copied evidence fails
closed before a candidate is smoked, accepted, staged, or promoted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "adoptiq-release-candidate/v1"
_SUPPORTED_PLATFORMS = {"macos": ".dmg", "windows": ".exe"}
_SUPPORTED_RELEASE_STATUSES = {"eligible", "invalidated"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_UTC_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SAFE_FLAT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_MANIFEST_MAX_BYTES = 64 * 1024
_FILE_READ_CHUNK_BYTES = 1024 * 1024


class ReleaseCandidateContractError(ValueError):
    """Raised when candidate identity or bytes do not satisfy the contract."""


@dataclass(frozen=True)
class ArtifactIdentity:
    name: str
    sha256: str
    size_bytes: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SidecarIdentity:
    path: str
    sha256: str

    def to_public_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class ReleaseCandidateManifest:
    schema_version: str
    release_status: str
    platform: str
    version: str
    build: int
    source_commit_sha: str
    artifact: ArtifactIdentity
    built_at_utc: str
    sidecars: tuple[SidecarIdentity, ...]

    @property
    def identity(self) -> dict[str, Any]:
        """Return the immutable candidate identity used to bind later evidence."""

        return {
            "release_status": self.release_status,
            "platform": self.platform,
            "version": self.version,
            "build": self.build,
            "source_commit_sha": self.source_commit_sha,
            "artifact_name": self.artifact.name,
            "artifact_sha256": self.artifact.sha256,
            "artifact_size_bytes": self.artifact.size_bytes,
            "built_at_utc": self.built_at_utc,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-safe representation; it contains no secrets."""

        return {
            "schema_version": self.schema_version,
            "release_status": self.release_status,
            "platform": self.platform,
            "version": self.version,
            "build": self.build,
            "source_commit_sha": self.source_commit_sha,
            "artifact": self.artifact.to_public_dict(),
            "built_at_utc": self.built_at_utc,
            "sidecars": [sidecar.to_public_dict() for sidecar in self.sidecars],
        }


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ReleaseCandidateContractError(f"{label} fields do not match the required schema")


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseCandidateContractError(f"{label} must be a non-empty canonical string")
    return value


def _require_sha256(value: Any, label: str) -> str:
    digest = _require_string(value, label)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ReleaseCandidateContractError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _require_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReleaseCandidateContractError(f"{label} must be a positive integer")
    return value


def _require_safe_flat_name(value: Any, label: str) -> str:
    name = _require_string(value, label)
    if (
        _SAFE_FLAT_NAME_RE.fullmatch(name) is None
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
    ):
        raise ReleaseCandidateContractError(f"{label} must be a safe flat file name")
    return name


def _validate_built_at_utc(value: Any) -> str:
    timestamp = _require_string(value, "built_at_utc")
    if _UTC_TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ReleaseCandidateContractError("built_at_utc must use canonical UTC YYYY-MM-DDTHH:MM:SSZ form")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ReleaseCandidateContractError("built_at_utc is not a valid UTC timestamp") from exc
    return timestamp


def validate_release_candidate_manifest(payload: Any) -> ReleaseCandidateManifest:
    """Validate a decoded manifest and return its immutable typed representation."""

    if not isinstance(payload, Mapping):
        raise ReleaseCandidateContractError("release candidate manifest must be a JSON object")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "release_status",
            "platform",
            "version",
            "build",
            "source_commit_sha",
            "artifact",
            "built_at_utc",
            "sidecars",
        },
        "release candidate manifest",
    )

    schema_version = _require_string(payload["schema_version"], "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ReleaseCandidateContractError("release candidate manifest schema is unsupported")

    release_status = _require_string(payload["release_status"], "release_status")
    if release_status not in _SUPPORTED_RELEASE_STATUSES:
        raise ReleaseCandidateContractError("release_status is unsupported")

    platform_name = _require_string(payload["platform"], "platform")
    if platform_name not in _SUPPORTED_PLATFORMS:
        raise ReleaseCandidateContractError("platform is unsupported")

    version = _require_string(payload["version"], "version")
    if len(version) > 64 or _VERSION_RE.fullmatch(version) is None:
        raise ReleaseCandidateContractError("version is not a supported release version")
    build = _require_positive_int(payload["build"], "build")

    source_commit_sha = _require_string(payload["source_commit_sha"], "source_commit_sha")
    if _SOURCE_SHA_RE.fullmatch(source_commit_sha) is None:
        raise ReleaseCandidateContractError("source_commit_sha must be a full lowercase 40-hex Git SHA")

    artifact_payload = payload["artifact"]
    if not isinstance(artifact_payload, Mapping):
        raise ReleaseCandidateContractError("artifact must be a JSON object")
    _require_exact_keys(artifact_payload, {"name", "sha256", "size_bytes"}, "artifact")
    artifact_name = _require_safe_flat_name(artifact_payload["name"], "artifact.name")
    expected_artifact_name = f"AdoptIQ-v{version}-build{build}{_SUPPORTED_PLATFORMS[platform_name]}"
    if artifact_name != expected_artifact_name:
        raise ReleaseCandidateContractError("artifact.name does not match platform/version/build identity")
    artifact = ArtifactIdentity(
        name=artifact_name,
        sha256=_require_sha256(artifact_payload["sha256"], "artifact.sha256"),
        size_bytes=_require_positive_int(artifact_payload["size_bytes"], "artifact.size_bytes"),
    )

    sidecars_payload = payload["sidecars"]
    if not isinstance(sidecars_payload, list) or not sidecars_payload:
        raise ReleaseCandidateContractError("sidecars must be a non-empty JSON array")
    if len(sidecars_payload) > 32:
        raise ReleaseCandidateContractError("sidecars exceeds the supported inventory limit")
    sidecars: list[SidecarIdentity] = []
    seen_names: set[str] = {artifact.name.casefold()}
    for index, raw_sidecar in enumerate(sidecars_payload):
        if not isinstance(raw_sidecar, Mapping):
            raise ReleaseCandidateContractError(f"sidecars[{index}] must be a JSON object")
        _require_exact_keys(raw_sidecar, {"path", "sha256"}, f"sidecars[{index}]")
        path = _require_safe_flat_name(raw_sidecar["path"], f"sidecars[{index}].path")
        folded = path.casefold()
        if folded in seen_names:
            raise ReleaseCandidateContractError("sidecar paths must be unique and distinct from the artifact")
        seen_names.add(folded)
        sidecars.append(
            SidecarIdentity(
                path=path,
                sha256=_require_sha256(raw_sidecar["sha256"], f"sidecars[{index}].sha256"),
            )
        )

    return ReleaseCandidateManifest(
        schema_version=schema_version,
        release_status=release_status,
        platform=platform_name,
        version=version,
        build=build,
        source_commit_sha=source_commit_sha,
        artifact=artifact,
        built_at_utc=_validate_built_at_utc(payload["built_at_utc"]),
        sidecars=tuple(sidecars),
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseCandidateContractError("release candidate manifest contains duplicate JSON fields")
        result[key] = value
    return result


def _read_regular_bytes(path: Path, *, label: str, maximum_bytes: int | None = None) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} is missing or unreadable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseCandidateContractError(f"{label} must be a regular non-symlink file")
    if maximum_bytes is not None and before.st_size > maximum_bytes:
        raise ReleaseCandidateContractError(f"{label} exceeds the supported size limit")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ReleaseCandidateContractError(f"{label} changed while it was opened")
        if maximum_bytes is not None and opened.st_size > maximum_bytes:
            raise ReleaseCandidateContractError(f"{label} exceeds the supported size limit")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            body = handle.read(maximum_bytes + 1) if maximum_bytes is not None else handle.read()
            if maximum_bytes is not None and len(body) > maximum_bytes:
                raise ReleaseCandidateContractError(f"{label} exceeds the supported size limit")
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} could not be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        after = path.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} changed while it was read") from exc
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or len(body) != before.st_size
    ):
        raise ReleaseCandidateContractError(f"{label} changed while it was read")
    return body


def load_release_candidate_manifest(path: Path | str) -> ReleaseCandidateManifest:
    """Read strict JSON from a regular file and validate the full manifest schema."""

    manifest_path = Path(path).expanduser()
    raw = _read_regular_bytes(manifest_path, label="release candidate manifest", maximum_bytes=_MANIFEST_MAX_BYTES)
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except ReleaseCandidateContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateContractError("release candidate manifest is not valid UTF-8 JSON") from exc
    return validate_release_candidate_manifest(payload)


def _hash_regular_file(path: Path, *, label: str, expected_size: int | None = None) -> tuple[int, str]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} is missing or unreadable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseCandidateContractError(f"{label} must be a regular non-symlink file")
    if expected_size is not None and before.st_size != expected_size:
        raise ReleaseCandidateContractError(f"{label} size does not match the manifest")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} could not be opened safely") from exc
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (expected_size is not None and opened.st_size != expected_size)
        ):
            raise ReleaseCandidateContractError(f"{label} changed while it was opened")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            for chunk in iter(lambda: handle.read(_FILE_READ_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} could not be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        after = path.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} changed while it was read") from exc
    if (
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise ReleaseCandidateContractError(f"{label} changed while it was read")
    return after.st_size, digest.hexdigest()


def _coerce_manifest_and_root(
    manifest: ReleaseCandidateManifest | Path | str,
    sidecar_root: Path | str | None,
) -> tuple[ReleaseCandidateManifest, Path]:
    if isinstance(manifest, ReleaseCandidateManifest):
        if sidecar_root is None:
            raise ReleaseCandidateContractError("sidecar_root is required when the manifest was already loaded")
        loaded = manifest
        root = Path(sidecar_root).expanduser()
    else:
        manifest_path = Path(manifest).expanduser()
        loaded = load_release_candidate_manifest(manifest_path)
        root = Path(sidecar_root).expanduser() if sidecar_root is not None else manifest_path.parent
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError("sidecar root is missing or unreadable") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ReleaseCandidateContractError("sidecar root must be a real non-symlink directory")
    return loaded, root


def verify_release_candidate_sidecars(
    manifest: ReleaseCandidateManifest | Path | str,
    *,
    sidecar_root: Path | str | None = None,
) -> ReleaseCandidateManifest:
    """Verify every flat sidecar against the exact digest in the manifest."""

    loaded, root = _coerce_manifest_and_root(manifest, sidecar_root)
    for sidecar in loaded.sidecars:
        _, actual_digest = _hash_regular_file(root / sidecar.path, label=f"candidate sidecar {sidecar.path}")
        if actual_digest != sidecar.sha256:
            raise ReleaseCandidateContractError(f"candidate sidecar {sidecar.path} hash does not match the manifest")
    return loaded


def verify_release_candidate(
    manifest: ReleaseCandidateManifest | Path | str,
    candidate_path: Path | str,
    *,
    sidecar_root: Path | str | None = None,
) -> ReleaseCandidateManifest:
    """Verify exact artifact name/size/hash and all manifest-bound sidecars."""

    loaded, root = _coerce_manifest_and_root(manifest, sidecar_root)
    if loaded.release_status != "eligible":
        raise ReleaseCandidateContractError("release candidate has been invalidated")
    candidate = Path(candidate_path).expanduser()
    if candidate.name != loaded.artifact.name:
        raise ReleaseCandidateContractError("candidate artifact name does not match the manifest")
    _, actual_digest = _hash_regular_file(
        candidate,
        label="candidate artifact",
        expected_size=loaded.artifact.size_bytes,
    )
    if actual_digest != loaded.artifact.sha256:
        raise ReleaseCandidateContractError("candidate artifact hash does not match the manifest")
    return verify_release_candidate_sidecars(loaded, sidecar_root=root)
