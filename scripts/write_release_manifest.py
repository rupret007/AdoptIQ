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
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

SCHEMA_VERSION = 1
PLATFORMS = ("mac", "pc")


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
    args = parser.parse_args(argv)

    existing = load_existing(args.manifest)
    manifest = merge_manifest(
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
    write_atomic(args.manifest, manifest)
    sys.stderr.write(
        f"write_release_manifest: {args.platform} build {manifest[args.platform]['build']} "
        f"-> {args.manifest} (top-level build {manifest['build']})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
