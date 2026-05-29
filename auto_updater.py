"""Round 119 / Build 88 -- cross-platform auto-update engine (Tier C).

Full silent self-replace + relaunch on both macOS and Windows, fed by a
machine-readable ``latest.json`` manifest in a synced OneDrive Releases
folder (``AI Projects/OUTBOX``).  The design is deliberately *fail-safe*:

  * verification (sha256 + macOS codesign) is MANDATORY before any swap;
  * the apply step is gated on an idle app (no running analysis);
  * ANY failure degrades to a notify banner -- never a broken install;
  * the swapper runs detached from the bundle and keeps ``<name>.old``
    for one boot so a bad build can be rolled back by hand.

This module imports ONLY the standard library + ``config`` so it stays
free of the ``app_simple`` import cycle and is fully unit-testable
offline.  The idle-gate check, the clean-SIGTERM shutdown trigger, and
the subprocess runner are all INJECTED by the caller (``app_simple``)
with safe defaults, so pytest can exercise every branch without ever
spawning a real swapper or replacing a real bundle.

Safety contract (mirrored in CLAUDE.md):
  - Never swap an unverified artifact.
  - Never auto-update mid-analysis.
  - Any exception in the auto path -> notify fallback.
  - Swapper detached + keeps ``.old`` for one boot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import subprocess  # noqa: S404 - used for detached swapper + codesign verify only
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_PLATFORMS = ("mac", "pc")
# How many bytes to read per chunk when hashing a (potentially large) DMG/EXE.
_HASH_CHUNK = 1024 * 1024


class UpdateError(Exception):
    """Internal signal that the auto path must degrade to notify.

    Carries an ``error_kind`` string so the status endpoint can surface a
    non-PII, operator-meaningful reason without leaking paths/secrets.
    """

    def __init__(self, message: str, *, error_kind: str = "update_error") -> None:
        super().__init__(message)
        self.error_kind = error_kind


# ---------------------------------------------------------------------------
# Platform + install-root resolution
# ---------------------------------------------------------------------------
def platform_key() -> Optional[str]:
    """Return ``'mac'`` / ``'pc'`` for the current OS, else ``None``."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform == "win32":
        return "pc"
    return None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_install_root() -> Optional[Path]:
    """Resolve the running bundle/exe that a swapper would replace.

    Frozen-only (returns ``None`` in dev so a swapper is never written
    against a source checkout).  macOS: ``sys.executable`` is
    ``.../AdoptIQ.app/Contents/MacOS/AdoptIQ`` so ``parents[2]`` is the
    ``.app`` bundle.  Windows: the ``AdoptIQ.exe`` path itself.
    """
    if not is_frozen():
        return None
    try:
        exe = Path(sys.executable).resolve()
    except Exception:  # noqa: BLE001 - never raise from a resolver
        return None
    key = platform_key()
    try:
        if key == "mac":
            # .../AdoptIQ.app/Contents/MacOS/AdoptIQ -> .../AdoptIQ.app
            for parent in exe.parents:
                if parent.suffix == ".app":
                    return parent
            # Fallback to the documented depth if the suffix walk misses.
            if len(exe.parents) >= 3:
                return exe.parents[2]
            return None
        if key == "pc":
            return exe
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------------------------------------------------------------------------
# Manifest read + validate
# ---------------------------------------------------------------------------
def _releases_folder(releases_folder: Optional[str] = None) -> Optional[Path]:
    if releases_folder:
        return Path(releases_folder)
    try:
        from config import Config  # noqa: PLC0415
        folder = getattr(Config, "ADOPTIQ_RELEASES_FOLDER", None)
        return Path(folder) if folder else None
    except Exception:  # noqa: BLE001
        return None


def validate_manifest(data: Any) -> Dict[str, Any]:
    """Schema-validate the parsed manifest; raise ``UpdateError`` on junk.

    Tolerant of the OTHER platform's slot being absent/broken -- only the
    fields the updater actually consumes are enforced.
    """
    if not isinstance(data, dict):
        raise UpdateError("manifest is not a JSON object", error_kind="bad_manifest")
    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        raise UpdateError(f"unsupported manifest schema {schema!r}", error_kind="bad_schema")
    if "build" not in data:
        raise UpdateError("manifest missing top-level build", error_kind="bad_manifest")
    return data


def read_latest_manifest(releases_folder: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Locate + parse + validate ``latest.json``; ``None`` when unavailable.

    Returns ``None`` (not a raise) when the Releases folder or manifest is
    simply missing/unsynced -- that is the common "nothing published yet"
    case and must not look like an error.  A *corrupt* manifest raises
    ``UpdateError`` so the caller can record the reason.
    """
    folder = _releases_folder(releases_folder)
    if folder is None:
        return None
    manifest_path = folder / "latest.json"
    try:
        if not manifest_path.is_file():
            return None
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise UpdateError(f"latest.json unreadable: {type(exc).__name__}", error_kind="bad_manifest") from exc
    return validate_manifest(data)


def _slot_build(manifest: Dict[str, Any], key: str) -> Optional[int]:
    """Per-platform build (authoritative), falling back to top-level build."""
    slot = manifest.get(key)
    if isinstance(slot, dict) and "build" in slot:
        try:
            return int(slot["build"])
        except (TypeError, ValueError):
            return None
    try:
        return int(manifest.get("build"))
    except (TypeError, ValueError):
        return None


def is_update_available(
    manifest: Optional[Dict[str, Any]],
    *,
    current_build: Optional[int] = None,
    key: Optional[str] = None,
    channel: str = "stable",
) -> bool:
    """``True`` when the manifest advertises a newer build for this platform.

    Compares the per-platform slot build (authoritative) against the
    running ``ADOPTIQ_BUILD``.  Conservative: any malformed input,
    channel mismatch, missing slot, or non-numeric build returns
    ``False`` so we never trigger an update we can't reason about.
    """
    if not isinstance(manifest, dict):
        return False
    if str(manifest.get("channel", "stable")) != channel:
        return False
    key = key or platform_key()
    if key not in _PLATFORMS:
        return False
    # The slot must actually exist + carry an artifact for this platform.
    slot = manifest.get(key)
    if not isinstance(slot, dict) or not str(slot.get("artifact") or "").strip():
        return False
    latest = _slot_build(manifest, key)
    if latest is None:
        return False
    if current_build is None:
        try:
            from config import ADOPTIQ_BUILD  # noqa: PLC0415
            current_build = int(ADOPTIQ_BUILD)
        except Exception:  # noqa: BLE001
            return False
    return latest > current_build


# ---------------------------------------------------------------------------
# Staging + verification
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _updates_dir(app_support_dir: Optional[str]) -> Path:
    base = Path(app_support_dir) if app_support_dir else Path(tempfile.gettempdir())
    updates = base / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    return updates


def _artifact_source(manifest: Dict[str, Any], key: str, releases_folder: Optional[str]) -> Path:
    folder = _releases_folder(releases_folder)
    if folder is None:
        raise UpdateError("releases folder not found", error_kind="no_releases_folder")
    slot = manifest.get(key)
    if not isinstance(slot, dict):
        raise UpdateError(f"manifest has no {key} slot", error_kind="bad_manifest")
    rel = str(slot.get("artifact") or "").strip()
    if not rel:
        raise UpdateError(f"{key} slot has no artifact", error_kind="bad_manifest")
    # Guard against absolute / traversal artifact paths in the manifest.
    if os.path.isabs(rel) or ".." in Path(rel).parts:
        raise UpdateError("artifact path is not a safe relative path", error_kind="bad_artifact_path")
    return folder / rel


def stage_and_verify(
    manifest: Dict[str, Any],
    *,
    releases_folder: Optional[str] = None,
    app_support_dir: Optional[str] = None,
    key: Optional[str] = None,
    runner: Callable[..., Any] = subprocess.run,
) -> Path:
    """Copy the artifact into the updates dir and verify it.

    Returns the path the swapper should install FROM:
      * macOS: the extracted ``AdoptIQ.app`` (mounted from the verified DMG).
      * Windows: the verified ``AdoptIQ.exe``.

    Raises ``UpdateError`` on any verification failure so the caller falls
    back to notify.  ``runner`` is injected so tests can assert the
    codesign / hdiutil / xattr invocations without touching the disk.
    """
    key = key or platform_key()
    if key not in _PLATFORMS:
        raise UpdateError(f"unsupported platform {key!r}", error_kind="unsupported_platform")
    slot = manifest.get(key)
    if not isinstance(slot, dict):
        raise UpdateError(f"manifest has no {key} slot", error_kind="bad_manifest")

    src = _artifact_source(manifest, key, releases_folder)
    if not src.is_file():
        raise UpdateError("artifact missing in releases folder", error_kind="artifact_missing")

    updates = _updates_dir(app_support_dir)
    staged_artifact = updates / src.name
    try:
        import shutil  # noqa: PLC0415
        shutil.copy2(src, staged_artifact)
    except OSError as exc:
        raise UpdateError(f"artifact copy failed: {type(exc).__name__}", error_kind="copy_failed") from exc

    expected = str(slot.get("sha256") or "").strip().lower()
    if not expected:
        raise UpdateError("manifest slot missing sha256", error_kind="bad_manifest")
    actual = _sha256_file(staged_artifact)
    if actual != expected:
        # Remove the bad download so a later run re-fetches cleanly.
        try:
            staged_artifact.unlink()
        except OSError:
            pass
        raise UpdateError("sha256 mismatch on staged artifact", error_kind="sha256_mismatch")

    if key == "mac":
        return _stage_mac_app(staged_artifact, updates, runner=runner)
    return _stage_pc_exe(staged_artifact, runner=runner)


def _strip_quarantine(path: Path, runner: Callable[..., Any]) -> None:
    """Best-effort ``xattr -cr`` so Gatekeeper does not block the relaunch."""
    try:
        runner(["xattr", "-cr", str(path)], check=False, capture_output=True)
    except Exception:  # noqa: BLE001 - quarantine strip is best-effort
        logger.debug("auto_updater: quarantine strip skipped for %s", path)


def _stage_mac_app(dmg_path: Path, updates: Path, *, runner: Callable[..., Any]) -> Path:
    """Mount the verified DMG, extract AdoptIQ.app, codesign-verify it."""
    mountpoint = Path(tempfile.mkdtemp(prefix="adoptiq_update_mnt."))
    staged_app = updates / "AdoptIQ.app"
    try:
        attach = runner(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mountpoint), str(dmg_path)],
            check=False, capture_output=True,
        )
        if getattr(attach, "returncode", 1) != 0:
            raise UpdateError("hdiutil attach failed", error_kind="dmg_mount_failed")
        src_app = mountpoint / "AdoptIQ.app"
        if not src_app.exists():
            raise UpdateError("AdoptIQ.app not found in DMG", error_kind="dmg_no_app")
        import shutil  # noqa: PLC0415
        if staged_app.exists():
            shutil.rmtree(staged_app, ignore_errors=True)
        ditto = runner(["ditto", str(src_app), str(staged_app)], check=False, capture_output=True)
        if getattr(ditto, "returncode", 1) != 0:
            raise UpdateError("ditto from DMG failed", error_kind="dmg_copy_failed")
    finally:
        try:
            runner(["hdiutil", "detach", str(mountpoint), "-force"], check=False, capture_output=True)
        except Exception:  # noqa: BLE001
            logger.debug("auto_updater: hdiutil detach skipped")
        try:
            os.rmdir(mountpoint)
        except OSError:
            pass

    _strip_quarantine(staged_app, runner)
    verify = runner(
        ["codesign", "--verify", "--deep", "--strict", str(staged_app)],
        check=False, capture_output=True,
    )
    if getattr(verify, "returncode", 1) != 0:
        raise UpdateError("codesign --verify failed on staged app", error_kind="codesign_failed")
    return staged_app


def _stage_pc_exe(exe_path: Path, *, runner: Callable[..., Any]) -> Path:
    """Windows: optional Authenticode soft-check, then return the EXE.

    The EXE is currently unsigned (see CLAUDE.md out-of-scope note), so a
    failed/absent Authenticode signature is logged but does NOT block the
    update.  When the EXE becomes signed, flip ``_PC_REQUIRE_SIGNATURE``.
    """
    try:
        result = runner(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-AuthenticodeSignature '{exe_path}').Status"],
            check=False, capture_output=True, text=True,
        )
        status = (getattr(result, "stdout", "") or "").strip()
        if status and status != "Valid":
            logger.warning("auto_updater: Authenticode status %r (soft-check; unsigned EXE expected)", status)
    except Exception:  # noqa: BLE001 - soft check only
        logger.debug("auto_updater: Authenticode check skipped")
    return exe_path


# ---------------------------------------------------------------------------
# Swapper script generation
# ---------------------------------------------------------------------------
def _swapper_dir() -> Path:
    """Swapper lives OUTSIDE the bundle (TMPDIR) so it survives the swap."""
    d = Path(tempfile.gettempdir()) / "adoptiq_swapper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_swapper_macos(*, pid: int, staged_app: Path, target_app: Path,
                        swapper_dir: Optional[Path] = None) -> Path:
    """Write the detached macOS swapper shell script. Returns its path."""
    swapper_dir = swapper_dir or _swapper_dir()
    script = swapper_dir / "adoptiq_swap.sh"
    body = f"""#!/bin/bash
# Round 119 / Build 88 -- AdoptIQ macOS auto-update swapper (detached).
# Waits for the running app PID to exit, moves the old bundle aside to
# <name>.old (rollback), installs the verified staged bundle, relaunches,
# then self-deletes. Runs OUTSIDE the bundle so the swap cannot delete it.
set -u
PID="{pid}"
STAGED="{staged_app}"
TARGET="{target_app}"
OLD="${{TARGET}}.old"

# 1. Wait for the current AdoptIQ process to exit (poll, ~30s timeout).
for i in $(seq 1 60); do
  if ! kill -0 "$PID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

# 2. Move the old bundle aside (keep exactly one .old for rollback).
if [ -d "$TARGET" ]; then
  rm -rf "$OLD" 2>/dev/null || true
  mv "$TARGET" "$OLD" 2>/dev/null || true
fi

# 3. Install the verified staged bundle.
ditto "$STAGED" "$TARGET"
xattr -cr "$TARGET" 2>/dev/null || true

# 4. Relaunch the new build.
open "$TARGET"

# 5. Clean up the staged copy and self-delete.
rm -rf "$STAGED" 2>/dev/null || true
rm -f "$0" 2>/dev/null || true
exit 0
"""
    script.write_text(body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return script


def write_swapper_windows(*, pid: int, staged_exe: Path, target_exe: Path,
                          swapper_dir: Optional[Path] = None) -> Path:
    """Write the detached Windows swapper batch script. Returns its path."""
    swapper_dir = swapper_dir or _swapper_dir()
    script = swapper_dir / "adoptiq_swap.bat"
    body = f"""@echo off
REM Round 119 / Build 88 -- AdoptIQ Windows auto-update swapper (detached).
REM Waits for the running PID to exit, moves the old EXE aside to .old
REM (rollback), copies the verified staged EXE into place, relaunches,
REM then self-deletes. Runs OUTSIDE the install dir.
set PID={pid}
set STAGED={staged_exe}
set TARGET={target_exe}
set OLD=%TARGET%.old

REM 1. Wait for the current process to exit (poll, ~30s timeout).
set /a tries=0
:waitloop
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto exited
set /a tries+=1
if %tries% geq 60 goto exited
ping -n 2 127.0.0.1 >nul
goto waitloop
:exited

REM 2. Move the old EXE aside (keep one .old for rollback).
if exist "%TARGET%" (
  if exist "%OLD%" del /F /Q "%OLD%" >nul 2>nul
  move /Y "%TARGET%" "%OLD%" >nul 2>nul
)

REM 3. Install the verified staged EXE.
copy /Y "%STAGED%" "%TARGET%" >nul 2>nul

REM 4. Relaunch the new build.
start "" "%TARGET%"

REM 5. Clean up staged copy and self-delete.
del /F /Q "%STAGED%" >nul 2>nul
(goto) 2>nul & del /F /Q "%~f0"
"""
    script.write_text(body, encoding="utf-8")
    return script


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _spawn_detached(argv) -> None:
    """Launch the swapper fully detached from this process group."""
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, close_fds=True, **kwargs)  # noqa: S603 - argv is our generated swapper


def apply_update(
    manifest: Optional[Dict[str, Any]] = None,
    *,
    releases_folder: Optional[str] = None,
    app_support_dir: Optional[str] = None,
    current_build: Optional[int] = None,
    key: Optional[str] = None,
    is_busy: Optional[Callable[[], bool]] = None,
    trigger_shutdown: Optional[Callable[[], None]] = None,
    runner: Callable[..., Any] = subprocess.run,
    spawn: Callable[[Any], None] = _spawn_detached,
    testing: bool = False,
) -> Dict[str, Any]:
    """Orchestrate a verified self-replace; never raises, never half-installs.

    Returns a result dict the caller can echo to the status endpoint:
      * ``{ok: True, state: 'applying', ...}`` -- swapper spawned, shutdown triggered.
      * ``{ok: True, state: 'would_update', ...}`` -- TESTING short-circuit.
      * ``{ok: False, state: 'busy', ...}`` -- a job is running; defer to notify.
      * ``{ok: False, state: 'notify', error_kind: ...}`` -- any failure; notify banner.

    The idle-gate (`is_busy`), the clean-SIGTERM shutdown (`trigger_shutdown`),
    the subprocess runner, and the detached spawn are all injected so this
    function is fully testable offline.  The TESTING short-circuit
    guarantees pytest can never trigger a real swap.
    """
    key = key or platform_key()
    try:
        if key not in _PLATFORMS:
            raise UpdateError(f"unsupported platform {key!r}", error_kind="unsupported_platform")
        if not is_frozen() and not testing:
            # Dev mode: there is no bundle to replace. Never swap a checkout.
            raise UpdateError("not a frozen build", error_kind="not_frozen")

        if manifest is None:
            manifest = read_latest_manifest(releases_folder)
        if not is_update_available(manifest, current_build=current_build, key=key):
            return {"ok": False, "state": "notify", "error_kind": "no_update",
                    "reason": "no newer build available"}

        # Idle gate -- never auto-update mid-analysis.
        if is_busy is not None:
            try:
                if bool(is_busy()):
                    return {"ok": False, "state": "busy", "error_kind": "busy",
                            "reason": "analysis running; deferring update"}
            except Exception:  # noqa: BLE001 - a broken busy-check must not force an unsafe swap
                return {"ok": False, "state": "busy", "error_kind": "busy_check_failed",
                        "reason": "idle check failed; deferring update"}

        # TESTING short-circuit: validate the path WITHOUT staging/spawning.
        if testing:
            slot_build = _slot_build(manifest, key) if manifest else None
            return {"ok": True, "state": "would_update", "latest_build": slot_build}

        target = current_install_root()
        if target is None:
            raise UpdateError("could not resolve install root", error_kind="no_install_root")

        staged = stage_and_verify(
            manifest, releases_folder=releases_folder,
            app_support_dir=app_support_dir, key=key, runner=runner,
        )

        pid = os.getpid()
        if key == "mac":
            swapper = write_swapper_macos(pid=pid, staged_app=staged, target_app=target)
            argv = ["/bin/bash", str(swapper)]
        else:
            swapper = write_swapper_windows(pid=pid, staged_exe=staged, target_exe=target)
            argv = ["cmd", "/c", str(swapper)]

        spawn(argv)

        # Trigger the existing clean SIGTERM shutdown so atexit handlers
        # fire (status save + corpus scrub) before the swapper takes over.
        if trigger_shutdown is not None:
            try:
                trigger_shutdown()
            except Exception:  # noqa: BLE001 - swapper already spawned; log only
                logger.warning("auto_updater: trigger_shutdown raised; swapper will wait on PID timeout")

        return {"ok": True, "state": "applying", "swapper": str(swapper),
                "staged": str(staged), "latest_build": _slot_build(manifest, key)}
    except UpdateError as exc:
        logger.warning("auto_updater: degrading to notify (%s)", exc.error_kind)
        return {"ok": False, "state": "notify", "error_kind": exc.error_kind, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - any failure MUST fall back to notify
        logger.warning("auto_updater: unexpected failure (%s); degrading to notify", type(exc).__name__)
        return {"ok": False, "state": "notify", "error_kind": "unexpected", "reason": type(exc).__name__}
