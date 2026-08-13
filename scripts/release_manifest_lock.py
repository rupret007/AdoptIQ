#!/usr/bin/env python3
"""Portable fail-closed lock for consumer release-manifest publication.

Both platform publishers must hold the same adjacent lock while reading,
merging, and atomically replacing ``latest.json``.  ``mkdir`` is the common
exclusive primitive available on macOS and Windows.  A leftover lock is never
silently stolen: an operator must first establish that no publisher is active.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

LOCK_NAME = ".adoptiq-release-manifest.lock"


@contextlib.contextmanager
def release_manifest_lock(root: str | Path, *, publisher: str) -> Iterator[Path]:
    """Exclusively lock one hydrated release root until the caller finishes."""

    releases = Path(root).expanduser()
    if releases.is_symlink() or not releases.is_dir():
        raise ValueError("release-manifest lock root must be an existing regular directory")
    releases = releases.resolve()
    lock_dir = releases / LOCK_NAME
    token = secrets.token_hex(16)
    try:
        lock_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError(
            "release-manifest publication is already locked; do not publish "
            "concurrently or remove the lock without operator verification"
        ) from exc
    except OSError as exc:
        raise ValueError("release-manifest publication lock could not be acquired") from exc

    marker = lock_dir / "owner.json"
    identity = {
        "schema": "adoptiq-release-manifest-lock/v1",
        "publisher": str(publisher).strip() or "unknown",
        "pid": os.getpid(),
        "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "token": token,
    }
    try:
        marker.write_text(
            json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        yield lock_dir
    finally:
        # Remove only the lock created by this context.  If its marker was
        # replaced or altered, leave the directory behind so publication fails
        # closed instead of deleting another publisher's coordination state.
        try:
            observed = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            observed = None
        if isinstance(observed, dict) and observed.get("token") == token:
            try:
                marker.unlink()
                lock_dir.rmdir()
            except OSError:
                pass
