#!/usr/bin/env python3
"""Provision runtime-only credentials into the app's owner-protected .env.

``embed_credentials.RUNTIME_ONLY_ENV_KEYS`` are deliberately excluded from
``_bundled_secrets.py`` so no authentication material is recoverable from a
shipped ``.app``, ``.dmg`` or ``.exe``.  The frozen app loads them from the
per-user Application Support ``.env`` instead, which means every machine that
runs a packaged build needs this one-time step.

Secret values are never printed, logged, or echoed: the report is limited to key
names, presence, and truncated SHA-256 digests used to confirm a match.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from embed_credentials import (  # noqa: E402
    REQUIRED_RUNTIME_CREDENTIAL_KEYS,
    RUNTIME_ONLY_ENV_KEYS,
    _parse_env_file,
)


def runtime_env_path() -> Path:
    """Return the Application Support ``.env`` path for the current platform."""

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AdoptIQ" / ".env"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "AdoptIQ" / ".env"
    return Path.home() / ".adoptiq" / ".env"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else "<empty>"


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=value`` file, preserving every key it contains."""

    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def render_env_file(values: dict[str, str]) -> str:
    header = (
        "# AdoptIQ runtime credentials - owner-only (mode 0600); never commit.\n"
        "# Written by scripts/provision_runtime_credentials.py.\n"
        "# These values are intentionally NOT embedded in the packaged app.\n"
    )
    body = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    return header + body


def provision(source: Path, target: Path, *, apply: bool) -> int:
    if not source.exists():
        print(f"ERROR: source secrets file not found: {source}")
        return 1

    configured = _parse_env_file(source, include_runtime_only=True)
    available = {
        key: configured[key]
        for key in sorted(RUNTIME_ONLY_ENV_KEYS)
        if configured.get(key)
    }
    missing = sorted(RUNTIME_ONLY_ENV_KEYS - available.keys())

    existing = read_env_file(target)
    # Preserve unrelated keys an operator may have added by hand.
    merged = dict(existing)
    merged.update(available)

    print(f"source:  {source}")
    print(f"target:  {target}")
    print(f"runtime-only keys required: {sorted(RUNTIME_ONLY_ENV_KEYS)}")
    for key in sorted(RUNTIME_ONLY_ENV_KEYS):
        state = "present" if key in available else "absent"
        changed = existing.get(key, "") != merged.get(key, "")
        print(
            f"  {key}: source={state} digest={_digest(available.get(key, ''))} "
            f"changed={changed}"
        )
    preserved = sorted(set(existing) - set(available))
    if preserved:
        print(f"preserved existing keys: {preserved}")

    missing_required = sorted(
        key for key in REQUIRED_RUNTIME_CREDENTIAL_KEYS if not configured.get(key)
    )
    if missing_required:
        print(
            "ERROR: required runtime credential(s) missing from source: "
            + ", ".join(missing_required)
        )
        return 1

    if not available:
        print(
            "ERROR: no runtime-only credential had a value in the source file; "
            "refusing to write an unusable .env."
        )
        return 1

    if not apply:
        print("\nDry run only. Re-run with --apply to write the file.")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    # Create with owner-only permissions before any secret bytes are written so
    # the value is never briefly world-readable.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(render_env_file(merged))
    os.chmod(target, 0o600)

    written = read_env_file(target)
    for key, value in available.items():
        if written.get(key) != value:
            print(f"ERROR: verification failed for {key}")
            return 1

    mode = oct(target.stat().st_mode & 0o777)
    print(f"\nWrote {target} (mode {mode}) with {len(merged)} key(s).")
    if missing:
        print(f"NOTE: not configured in source, left unset: {missing}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO_ROOT / "secrets.env",
        help="secrets.env holding the runtime-only credentials",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="destination .env (defaults to the platform Application Support path)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the file; without this flag the script only reports",
    )
    args = parser.parse_args(argv)
    return provision(
        args.source,
        args.target or runtime_env_path(),
        apply=args.apply,
    )


if __name__ == "__main__":
    raise SystemExit(main())
