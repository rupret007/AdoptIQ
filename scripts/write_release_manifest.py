#!/usr/bin/env python3
"""Round 119 / Build 88 -- merge-aware ``latest.json`` release-manifest writer.

Shared by ``build_mac_dmg.sh`` (``--platform mac``) and ``build_pc.bat``
(``--platform pc``) so the cross-platform auto-updater has a single
machine-readable manifest at the OneDrive ``AI Projects/OUTBOX`` root.

The two build hosts run independently (Mac DMG on macOS, Windows EXE on
Windows), each syncing the SAME SharePoint folder via the OneDrive
desktop client.  This writer therefore MUST be merge-aware: it reads the
existing manifest (which may already carry the OTHER platform's slot),
updates only the slot for the platform being built, recomputes the
informational top-level ``build``, and writes the result atomically
(``os.replace``) so a crash mid-write can never leave a truncated JSON
document that would wedge every consumer's update check.

Schema (v1)::

    {
      "schema": 1,
      "version": "1.0.4",
      "build": 88,                 # max across platform slots (informational)
      "channel": "stable",
      "released_at_utc": "2026-05-29T19:00:00Z",
      "mac": { "build": 88, "version": "1.0.4",
               "artifact": "AdoptIQ/AdoptIQ-v1.0.4-build88.dmg",
               "sha256": "<hex>", "size_bytes": 0,
               "released_at_utc": "..." },
      "pc":  { "build": 87, "version": "1.0.4",
               "artifact": "AdoptIQ_PC/AdoptIQ-v1.0.4-build87.exe",
               "sha256": "<hex>", "size_bytes": 0,
               "released_at_utc": "..." },
      "notes": ""
    }

Per-platform ``build`` (inside each slot) is the authoritative value the
updater compares against ``ADOPTIQ_BUILD`` -- the top-level ``build`` is
only a convenience max so a human reading the file sees the newest build
at a glance.  Carrying the build inside each slot prevents a Mac-only
bump from making a Windows install think a (nonexistent) newer EXE is
available, and vice versa.

``artifact`` is the path RELATIVE to the OUTBOX root (e.g.
``AdoptIQ/AdoptIQ-v1.0.4-build88.dmg``) so the updater can join it onto
whatever local OneDrive mount the consumer has without the build host
needing to know the consumer's home directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from release_manifest_lock import release_manifest_lock

SCHEMA_VERSION = 1
PLATFORMS = ("mac", "pc")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _utc_now_iso() -> str:
    """UTC timestamp in the same ``...Z`` shape the build_info.txt uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_existing(path: str) -> dict:
    """Return the existing manifest dict, or ``{}`` when absent/corrupt.

    A corrupt or partially-synced manifest must NOT abort the build --
    we fall back to an empty base and rebuild the current platform's
    slot.  The other platform's slot is only preserved when the existing
    file parses cleanly; that is the correct trade-off because a corrupt
    file has no trustworthy slots to preserve anyway.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_existing_publication(path: Path) -> dict:
    """Read consumer state strictly; corrupt state must never be replaced."""

    if path.is_symlink():
        raise ValueError("consumer release manifest must not be a symlink")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError("consumer release manifest must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("consumer release manifest must contain valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise ValueError("consumer release manifest has an unsupported schema")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_publication_request(
    existing: dict,
    *,
    root: Path,
    platform: str,
    version: str,
    build: str,
    artifact: str,
    sha256: str,
    size: str,
    verify_artifact: bool = True,
) -> bool:
    """Validate exact bytes and monotonic slot transition; return idempotence."""

    try:
        requested_build = int(str(build).strip())
        requested_size = int(str(size).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("published build and size must be integers") from exc
    digest = str(sha256 or "").strip().casefold()
    if requested_build <= 0 or requested_size <= 0 or not _SHA256_RE.fullmatch(digest):
        raise ValueError("published build/size/digest identity is invalid")
    relative = Path(str(artifact).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
        raise ValueError("published artifact path is unsafe")
    unresolved_artifact = root / relative
    artifact_path = unresolved_artifact.resolve()
    if (
        unresolved_artifact.is_symlink()
        or (root / relative.parts[0]).is_symlink()
        or artifact_path.parent == root
        or root not in artifact_path.parents
    ):
        raise ValueError("published artifact path is unsafe")
    if verify_artifact:
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise ValueError("published artifact is missing or unsafe")
        if (
            artifact_path.stat().st_size != requested_size
            or _sha256(artifact_path) != digest
        ):
            raise ValueError("published artifact bytes do not match the requested identity")

    slot = existing.get(platform)
    if slot is None:
        return False
    if not isinstance(slot, dict):
        raise ValueError(f"existing {platform} release slot is malformed")
    try:
        existing_build = int(slot.get("build"))
        existing_size = int(slot.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"existing {platform} release slot is malformed") from exc
    if existing_build > requested_build:
        raise ValueError("refusing to replace a newer release with a stale build")
    if existing_build < requested_build:
        return False
    exact = bool(
        str(slot.get("version")) == str(version)
        and str(slot.get("artifact")) == str(artifact).replace("\\", "/")
        and str(slot.get("sha256") or "").casefold() == digest
        and existing_size == requested_size
    )
    if not exact:
        raise ValueError("same build number already identifies different bytes")
    return True


def copy_publication_artifact(
    source: Path,
    *,
    root: Path,
    artifact: str,
    expected_sha256: str,
    expected_size: int,
) -> Path:
    """Copy exact candidate bytes atomically while the publication lock is held."""

    expanded_source = source.expanduser()
    if expanded_source.is_symlink():
        raise ValueError("publication source artifact must not be a symlink")
    source_path = expanded_source.resolve()
    if not source_path.is_file():
        raise ValueError("publication source artifact must be a regular file")
    digest = str(expected_sha256).strip().casefold()
    if source_path.stat().st_size != expected_size or _sha256(source_path) != digest:
        raise ValueError("publication source bytes do not match the requested identity")

    relative = Path(str(artifact).replace("\\", "/"))
    destination = root / relative
    if destination.is_symlink() or destination.parent.is_symlink():
        raise ValueError("publication destination must not be a symlink")
    if not destination.parent.is_dir():
        raise ValueError("publication artifact directory must already exist")
    resolved_destination = destination.resolve()
    if root not in resolved_destination.parents:
        raise ValueError("publication destination escaped the approved root")
    if source_path == resolved_destination:
        return resolved_destination

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source_path, temporary)
        if temporary.stat().st_size != expected_size or _sha256(temporary) != digest:
            raise ValueError("publication copy failed exact-byte verification")
        if source_path.stat().st_size != expected_size or _sha256(source_path) != digest:
            raise ValueError("publication source changed during copy")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if destination.stat().st_size != expected_size or _sha256(destination) != digest:
        raise ValueError("published artifact failed post-copy verification")
    return destination.resolve()


def merge_manifest(
    existing: dict,
    *,
    platform: str,
    version: str,
    build,
    artifact: str,
    sha256: str,
    size_bytes,
    channel: str = "stable",
    released_at: str | None = None,
    notes: str | None = None,
) -> dict:
    """Merge the current platform's slot into ``existing`` and return it.

    Raises ``ValueError`` for an unknown platform or a non-integer build
    so a typo in the build script fails loud instead of writing a
    manifest the updater cannot compare.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}; expected one of {PLATFORMS}")
    try:
        build_int = int(str(build).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"build must be an integer, got {build!r}") from exc
    try:
        size_int = int(size_bytes) if size_bytes not in (None, "") else 0
    except (TypeError, ValueError):
        size_int = 0

    released = released_at or _utc_now_iso()
    manifest: dict = dict(existing) if isinstance(existing, dict) else {}
    manifest["schema"] = SCHEMA_VERSION
    manifest["version"] = version
    manifest["channel"] = channel
    manifest["released_at_utc"] = released
    manifest[platform] = {
        "build": build_int,
        "version": version,
        "artifact": artifact,
        "sha256": (sha256 or "").strip().lower(),
        "size_bytes": size_int,
        "released_at_utc": released,
    }

    # Top-level build = max across known platform slots (informational only).
    builds = []
    for plat in PLATFORMS:
        slot = manifest.get(plat)
        if isinstance(slot, dict):
            try:
                builds.append(int(slot.get("build")))
            except (TypeError, ValueError):
                continue
    manifest["build"] = max(builds) if builds else build_int

    if notes is not None:
        manifest["notes"] = notes
    elif "notes" not in manifest:
        manifest["notes"] = ""
    return manifest


def write_atomic(path: str, manifest: dict) -> None:
    """Write ``manifest`` to ``path`` atomically (temp + ``os.replace``)."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".latest.", suffix=".json.tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge-aware latest.json release-manifest writer (Round 119)."
    )
    parser.add_argument("--manifest", required=True, help="Path to latest.json (read+merged+written in place).")
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--artifact", required=True, help="Artifact path RELATIVE to the OUTBOX root.")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--size", default=0)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--released-at", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--publication-root",
        default=None,
        help=(
            "Consumer OUTBOX root. When set, the shared cross-platform lock is "
            "mandatory and --publication-approved must also be supplied."
        ),
    )
    parser.add_argument(
        "--publication-approved",
        action="store_true",
        help="Acknowledge separately serialized consumer publication approval.",
    )
    parser.add_argument(
        "--source-artifact",
        default=None,
        help=(
            "Exact staged candidate bytes to copy under the shared publication "
            "lock; required with --publication-root."
        ),
    )
    args = parser.parse_args(argv)

    def merge_and_write(*, publication_root: Path | None = None) -> dict:
        existing = (
            load_existing_publication(Path(args.manifest).expanduser().resolve())
            if publication_root is not None
            else load_existing(args.manifest)
        )
        idempotent = False
        if publication_root is not None:
            idempotent = validate_publication_request(
                existing,
                root=publication_root,
                platform=args.platform,
                version=args.version,
                build=args.build,
                artifact=args.artifact,
                sha256=args.sha256,
                size=args.size,
            )
            if idempotent:
                return existing
        other_platform = "pc" if args.platform == "mac" else "mac"
        other_slot = json.loads(json.dumps(existing.get(other_platform), sort_keys=True))
        merged = merge_manifest(
            existing,
            platform=args.platform,
            version=args.version,
            build=args.build,
            artifact=args.artifact,
            sha256=args.sha256,
            size_bytes=args.size,
            channel=args.channel,
            released_at=args.released_at,
            notes=args.notes,
        )
        if publication_root is not None and json.loads(
            json.dumps(merged.get(other_platform), sort_keys=True)
        ) != other_slot:
            raise ValueError("release merge would alter the other platform slot")
        write_atomic(args.manifest, merged)
        if publication_root is not None and load_existing_publication(
            Path(args.manifest).expanduser().resolve()
        ) != merged:
            raise ValueError("published consumer manifest failed verification")
        return merged

    if args.publication_root is not None:
        if not args.publication_approved:
            parser.error("consumer publication requires --publication-approved")
        publication_root = Path(args.publication_root).expanduser().resolve()
        manifest_path = Path(args.manifest).expanduser().resolve()
        if manifest_path != publication_root / "latest.json":
            parser.error("published manifest must be <publication-root>/latest.json")
        if args.source_artifact is None:
            parser.error("consumer publication requires --source-artifact")
        with release_manifest_lock(
            publication_root,
            publisher=f"write_release_manifest:{args.platform}",
        ):
            existing = load_existing_publication(manifest_path)
            validate_publication_request(
                existing,
                root=publication_root,
                platform=args.platform,
                version=args.version,
                build=args.build,
                artifact=args.artifact,
                sha256=args.sha256,
                size=args.size,
                verify_artifact=False,
            )
            copy_publication_artifact(
                Path(args.source_artifact),
                root=publication_root,
                artifact=args.artifact,
                expected_sha256=str(args.sha256).strip().casefold(),
                expected_size=int(str(args.size).strip()),
            )
            manifest = merge_and_write(publication_root=publication_root)
    elif args.publication_approved or args.source_artifact is not None:
        parser.error(
            "--publication-approved/--source-artifact require --publication-root"
        )
    else:
        manifest = merge_and_write()
    sys.stderr.write(
        f"write_release_manifest: {args.platform} build {manifest[args.platform]['build']} "
        f"-> {args.manifest} (top-level build {manifest['build']})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
