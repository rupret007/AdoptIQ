"""Round 17 / Phase B.4 -- App-side glue that wires the encrypted
corpus into Flask startup.

Responsibilities:

* Honor the ``CORPUS_KNOWLEDGE_ENABLED`` feature flag.
* On the first call, spawn a background thread that:
  - opens the encrypted corpus (auto-creating the empty DB on first
    run);
  - runs ``corpus_indexer.index_folder`` against
    ``Config.CSONE_ONEDRIVE_FOLDER``;
  - configures the retriever connection so HTTP handlers can answer.
* Surface state to the admin dashboard (``last_index_stats``,
  ``last_error``, etc.) without forcing every caller to know about
  threads.
* Provide a CSRF-protected ``request_refresh`` entry point so the
  admin tile / a cron can re-index on demand.

The module must never raise on import.  Callers in app code that may
run before bootstrap has completed (e.g. health checks) should rely on
the retriever's ``CorpusUnavailable`` fallback rather than introspect
this module's internals.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from _logging_helpers import exit_log_streams_open as _shared_exit_log_streams_open
from _logging_helpers import safe_log_exception as _shared_safe_log_exception
from _logging_helpers import safe_log_info as _shared_safe_log_info
from _logging_helpers import safe_log_warning as _shared_safe_log_warning
from config import Config
from corpus_crypto import (
    CorpusCryptoError,
    EncryptedCorpusHandle,
    open_corpus_for_user,
)
from corpus_indexer import (
    IndexSignal,
    IndexStats,
    default_db_path,
    enumerate_user_report_files,
    index_folder,
)
from corpus_retriever import configure_connection

logger = logging.getLogger(__name__)


_BOOT_LOCK = threading.RLock()

# Round 35 / native-corpus + Round 96 / runtime-only corpus: 24h
# refresh cadence for the daily worker that pulls updates from the
# authorized local OneDrive mirror. Hour-resolution ticks keep us
# responsive to user sign-in events without hammering the filesystem.
_DAILY_REFRESH_INTERVAL_S = 86400.0
_DAILY_REFRESH_TICK_S = 3600.0
_DAILY_REFRESH_RETRY_S = 3600.0
_DAILY_REFRESH_THREAD_NAME = "adoptiq-corpus-daily-refresh"

# Round 53 / Phase 53.4.2 -- accelerated tick while the corpus is in
# the ``blocked_no_onedrive`` state.  We poll every 30 s so the
# moment the user signs in to OneDrive (and the OneDrive desktop
# client mirrors the sentinel into the synced folder) the panel
# transitions out of "Sign in to OneDrive" into "Active" without
# the user having to wait up to an hour for the next standard tick.
# Bounded by ``_DAILY_REFRESH_BLOCKED_MAX_TICKS`` so a user who
# never signs in does not get a 30-second polling loop that runs
# forever -- after the cap we fall back to the hourly tick (the
# loop still re-probes; it just sleeps longer between checks).
_DAILY_REFRESH_TICK_BLOCKED_S = 30.0
_DAILY_REFRESH_BLOCKED_MAX_TICKS = 240  # 240 * 30 s = 2 h before fallback


@dataclass
class CorpusBootState:
    """Lightweight snapshot of the bootstrap thread's progress.  All
    timestamps are ISO-8601 UTC strings; ``last_error`` is a short
    human-readable string that never includes raw exception
    tracebacks."""

    enabled: bool = False
    started: bool = False
    in_progress: bool = False
    completed: bool = False
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_stats: Optional[dict[str, object]] = None
    last_error: Optional[str] = None
    last_error_kind: Optional[str] = None
    encrypted_path: Optional[str] = None
    onedrive_root: Optional[str] = None
    # Round 17.1: per-source rollup of the most recent index pass so
    # the admin tile can break out OneDrive vs Downloads counts.
    # Each entry is ``{label, dir, files_seen, files_parsed,
    # files_skipped, files_failed, chunks_added}``.  ``None`` until
    # the first pass completes.
    last_sources: Optional[list[dict[str, object]]] = None
    # Round 17.2: SharePoint pull state for the admin tile.  ``None``
    # when SharePoint is disabled / the feature has never been
    # invoked.  Otherwise carries the latest :class:`RefreshStats`
    # dict plus a few user-displayable fields the tile needs to render
    # sign-in / refresh CTAs without reaching into the SharePoint
    # client.  Always serializable so the JSON payload is stable.
    sharepoint: Optional[dict[str, object]] = None
    # Round 35 / native-corpus: how the corpus was first materialized
    # on this install.
    #
    #   * ``"fresh"``  -- runtime-only path: no corpus data ships in
    #                     the .app, so the indexer creates or refreshes
    #                     the encrypted local DB from the user's
    #                     authorized OneDrive mirror after sync.
    #
    # ``last_successful_refresh_ts`` and ``last_refresh_attempt_ts``
    # back the daily-refresh worker's ``_should_refresh()`` math; both
    # are float epoch seconds (None means "never").
    source: Optional[str] = None
    indexed_at: Optional[str] = None
    last_successful_refresh_ts: Optional[float] = None
    last_refresh_attempt_ts: Optional[float] = None
    last_refresh_error: Optional[str] = None
    # Round 36 / onedrive-sync-auth: snapshot of whether the OneDrive
    # sync client has the canonical AdoptIQ folder mirrored to disk.
    # Replaces the MSAL/Graph "is the user signed into SharePoint?"
    # check -- we trust that the OneDrive desktop client handled
    # auth/MFA/admin-consent and just verify the result by stat()ing
    # the folder.
    #
    #   * ``"synced"``     -- ``Config.CSONE_ONEDRIVE_FOLDER`` exists,
    #                         is a directory, and contains at least one
    #                         non-empty file.  Daily refresh enabled.
    #   * ``"not_synced"`` -- folder is missing, empty, or unreadable.
    #                         Daily refresh paused; corpus remains
    #                         unavailable until OneDrive sync completes.
    #   * ``None``         -- check has not run yet (first boot, before
    #                         the bootstrap thread populates state).
    #
    # ``onedrive_file_count`` is the number of real files seen at the
    # last check (0 when not_synced).
    onedrive_status: Optional[str] = None
    onedrive_file_count: Optional[int] = None
    # Round 83 / Build 59: OneDrive sign-in proxy. Independent of
    # ``onedrive_status`` (which is folder-specific). Detects whether
    # the OneDrive desktop client is set up at all on this host so the
    # corpus panel can distinguish "not signed into OneDrive at all"
    # from "signed into OneDrive but the AdoptIQ corpus folder isn't
    # synced yet -- click here to add the share." Cross-platform via
    # ``_r83_onedrive_signed_in_proxy``: macOS reads
    # ``~/Library/CloudStorage/OneDrive-*`` glob; Windows reads
    # ``HKCU\\Software\\Microsoft\\OneDrive\\Accounts\\Business1``
    # registry key existence (no value reads, no PII).
    #
    #   * ``"signed_in_cisco"`` -- a Cisco-tenant OneDrive sync root is
    #                              present (the canonical match for
    #                              the AdoptIQ workflow).
    #   * ``"signed_in_other"`` -- some OneDrive sync root is present
    #                              but not the Cisco-tenant one
    #                              (personal OneDrive, different
    #                              tenant). Bootstrap button is NOT
    #                              shown -- the user needs to sign
    #                              into Cisco OneDrive first.
    #   * ``"not_signed_in"``   -- no OneDrive sync root detected.
    #   * ``"unknown"``         -- detection has not run yet OR the
    #                              probe raised (defensive fallback).
    signed_in_proxy: Optional[str] = None
    # Round 66 / Pass 5 - hybrid retrieval bootstrap state.
    #   * ``embedder_status``: "ready" | "unavailable" | None (untried).
    #   * ``embedder_load_error``: short human-readable error from the
    #     fastembed load attempt; None when ``ready`` or untried.
    # When ``unavailable``, ``Config.ASK_AI_RETRIEVAL_METHOD`` is
    # forced to ``"lexical"`` for this process so the rank_evidence
    # fallback path engages without per-query churn.
    embedder_status: Optional[str] = None
    embedder_load_error: Optional[str] = None


_STATE: CorpusBootState = CorpusBootState()
_HANDLE: Optional[EncryptedCorpusHandle] = None
_SIGNAL: Optional[IndexSignal] = None
_THREAD: Optional[threading.Thread] = None
_DAILY_REFRESH_THREAD: Optional[threading.Thread] = None
_DAILY_REFRESH_STOP: threading.Event = threading.Event()


def get_state() -> CorpusBootState:
    """Return a snapshot of the current bootstrap state.  Safe to call
    at any point; never raises."""
    with _BOOT_LOCK:
        # Return a shallow copy so callers cannot mutate our singleton.
        return CorpusBootState(
            enabled=_STATE.enabled,
            started=_STATE.started,
            in_progress=_STATE.in_progress,
            completed=_STATE.completed,
            last_started_at=_STATE.last_started_at,
            last_finished_at=_STATE.last_finished_at,
            last_stats=dict(_STATE.last_stats) if _STATE.last_stats else None,
            last_error=_STATE.last_error,
            last_error_kind=_STATE.last_error_kind,
            encrypted_path=_STATE.encrypted_path,
            onedrive_root=_STATE.onedrive_root,
            last_sources=(
                [dict(item) for item in _STATE.last_sources]
                if _STATE.last_sources is not None
                else None
            ),
            sharepoint=(
                dict(_STATE.sharepoint) if _STATE.sharepoint is not None else None
            ),
            source=_STATE.source,
            indexed_at=_STATE.indexed_at,
            last_successful_refresh_ts=_STATE.last_successful_refresh_ts,
            last_refresh_attempt_ts=_STATE.last_refresh_attempt_ts,
            last_refresh_error=_STATE.last_refresh_error,
            onedrive_status=_STATE.onedrive_status,
            onedrive_file_count=_STATE.onedrive_file_count,
            # Round 83
            signed_in_proxy=_STATE.signed_in_proxy,
        )


def is_enabled() -> bool:
    """Return True when the feature flag is on."""
    return bool(getattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Round 36 / onedrive-sync-auth: presence check
# ---------------------------------------------------------------------------


def _check_onedrive_sync_status() -> tuple[str, int, Optional[str]]:
    """Round 36: probe whether the OneDrive desktop client has the
    canonical AdoptIQ corpus folder synced to disk.  Replaces the
    legacy MSAL/Graph ``_is_sharepoint_signed_in`` check -- we trust
    that the OneDrive client handled auth/MFA/admin-consent and just
    verify the result by ``Path.is_dir()`` + counting non-empty
    files.

    Returns a 3-tuple ``(status, file_count, path)``:

    * ``status``     -- ``"synced"``     when the folder exists, is a
                        directory, and contains at least one
                        non-empty file.
                        ``"not_synced"`` when the folder is missing,
                        unreadable, empty, or all entries are
                        zero-byte placeholder stubs (OneDrive
                        on-demand files that have not been pulled).
                        ``"unknown"``    when ``Config.CSONE_ONEDRIVE_FOLDER``
                        is not configured.
    * ``file_count`` -- number of real (size > 0) files seen at the
                        top level of the OneDrive folder (no
                        recursion into immediate children -- a
                        recursive walk would wedge boot on large
                        sync mirrors).  Bounded scan -- we stop
                        after the first real file when only
                        deciding ``synced`` vs ``not_synced``,
                        so the returned count is always either
                        ``0`` (not_synced) or ``1`` (synced; not
                        a true total).  The full count is
                        computed by ``corpus_indexer`` on the
                        next refresh pass.  Round 68 / Build 42
                        (B4): docstring previously claimed
                        "top level + immediate children" but the
                        implementation only walks ``root.iterdir()``
                        (top level only) -- updated to match.
    * ``path``       -- the configured folder path (str), or ``None``
                        when not configured.

    Never raises -- any ``OSError`` collapses to ``("not_synced", 0,
    path)``.
    """
    onedrive_root = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
    if not onedrive_root:
        return "unknown", 0, None
    path_str = str(onedrive_root)
    try:
        root = Path(path_str)
        if not root.is_dir():
            return "not_synced", 0, path_str
        real_files = 0
        for entry in root.iterdir():
            try:
                if entry.is_file() and entry.stat().st_size > 0:
                    real_files += 1
                    if real_files >= 1:
                        # Short-circuit: we only need to know whether
                        # the folder is synced, not the exact count.
                        # The full count is computed by the indexer's
                        # walker on the next refresh pass.
                        break
            except OSError:
                continue
        if real_files >= 1:
            return "synced", real_files, path_str
        return "not_synced", 0, path_str
    except OSError:
        return "not_synced", 0, path_str


# ---------------------------------------------------------------------------
# Round 83 / Build 59: OneDrive sign-in proxy
# ---------------------------------------------------------------------------


# Glob patterns for macOS OneDrive sync roots. The OneDrive desktop
# client materializes one folder per signed-in account under
# ``~/Library/CloudStorage/`` named ``OneDrive-<tenant>`` for work
# accounts and ``OneDrive-Personal`` for consumer accounts; legacy
# pre-Big-Sur installs use ``~/OneDrive - <tenant>`` directly under
# the home directory.
_R83_MACOS_CISCO_GLOBS = (
    "Library/CloudStorage/OneDrive-Cisco*",
    "OneDrive - Cisco*",
)
_R83_MACOS_ANY_GLOBS = (
    "Library/CloudStorage/OneDrive-*",
    "OneDrive - *",
)


def _r83_onedrive_signed_in_proxy() -> str:
    """Round 83 / Build 59: probe whether ANY OneDrive account is
    signed in on this host -- independent of whether the canonical
    AdoptIQ corpus folder is synced.

    Replaces the implicit ``_check_onedrive_sync_status`` overload
    where "folder missing" was conflated with "not signed into
    OneDrive at all" -- two distinct UX states that need different
    panel copy and different bootstrap buttons.

    Returns one of:

      * ``"signed_in_cisco"`` -- a Cisco-tenant OneDrive sync root is
                                 present on disk (the canonical
                                 match for the AdoptIQ workflow).
      * ``"signed_in_other"`` -- some OneDrive sync root is present
                                 but not the Cisco-tenant one. The
                                 user is signed into OneDrive but
                                 with the wrong account; the
                                 corpus panel surfaces a "switch
                                 accounts" hint instead of the
                                 share-URL bootstrap button.
      * ``"not_signed_in"``   -- no OneDrive sync root detected.
      * ``"unknown"``         -- defensive fallback when probes
                                 raised. UI treats it as
                                 ``"not_signed_in"`` for behavior
                                 but logs it separately.

    Detection is filesystem / registry existence-only -- no plist
    parsing, no MSAL, no Graph calls, no shell-out to ``ps``.
    Round 36's "trust the OneDrive client; verify locally"
    architecture is preserved. No PII enters logs or
    ``analysis_status.json`` -- we never read account email values
    out of the registry / plist; existence of the slot is the
    signal.

    Pinned by ``tests/test_round83_onedrive_signed_in_proxy.py``.
    """
    # Round 83
    try:
        if sys.platform == "darwin":
            return _r83_macos_signed_in_proxy()
        if sys.platform.startswith("win"):
            return _r83_windows_signed_in_proxy()
        # Other unix platforms: check the Linux-style fallback paths
        # the OneDrive client occasionally creates. None of these are
        # supported AdoptIQ targets but a sensible default keeps the
        # contract honest.
        return _r83_macos_signed_in_proxy()
    except Exception:  # noqa: BLE001 - defensive boot-path
        return "unknown"


def _r83_macos_signed_in_proxy() -> str:
    """macOS arm of :func:`_r83_onedrive_signed_in_proxy`. Walks the
    home directory for OneDrive sync-root names. ``OSError`` collapses
    to ``"not_signed_in"`` so a permission error never raises out of
    the boot path."""
    # Round 83
    home_str = os.path.expanduser("~")
    try:
        home = Path(home_str)
        if not home.is_dir():
            return "not_signed_in"
    except OSError:
        return "not_signed_in"
    cisco_present = False
    other_present = False
    for pattern in _R83_MACOS_CISCO_GLOBS:
        try:
            for match in home.glob(pattern):
                try:
                    if match.is_dir():
                        cisco_present = True
                        break
                except OSError:
                    continue
        except OSError:
            continue
        if cisco_present:
            break
    if cisco_present:
        return "signed_in_cisco"
    for pattern in _R83_MACOS_ANY_GLOBS:
        try:
            for match in home.glob(pattern):
                try:
                    if match.is_dir():
                        other_present = True
                        break
                except OSError:
                    continue
        except OSError:
            continue
        if other_present:
            break
    if other_present:
        return "signed_in_other"
    return "not_signed_in"


def _r83_windows_signed_in_proxy() -> str:
    """Windows arm of :func:`_r83_onedrive_signed_in_proxy`. Uses
    ``winreg`` to probe for the OneDrive Accounts registry slots
    (existence-only -- no value reads, no PII). Falls back to
    ``%LOCALAPPDATA%\\Microsoft\\OneDrive\\settings\\Business1`` and
    ``%USERPROFILE%\\OneDrive - Cisco`` directories when the registry
    probe is unavailable (e.g. on a shimmed test environment)."""
    # Round 83
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        winreg = None  # type: ignore[assignment]
    if winreg is not None:
        try:
            # Cisco-tenant slot is conventionally ``Business1`` for
            # the user's primary work account.
            with winreg.OpenKey(  # type: ignore[attr-defined]
                winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
                r"Software\Microsoft\OneDrive\Accounts\Business1",
            ):
                return "signed_in_cisco"
        except OSError:
            pass
        try:
            with winreg.OpenKey(  # type: ignore[attr-defined]
                winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
                r"Software\Microsoft\OneDrive\Accounts\Personal",
            ):
                return "signed_in_other"
        except OSError:
            pass
    # Filesystem fallback: same shape as the macOS probe.
    home_str = os.path.expanduser("~")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    candidates_cisco = [
        os.path.join(home_str, "OneDrive - Cisco"),
    ]
    if localappdata:
        candidates_cisco.append(
            os.path.join(localappdata, "Microsoft", "OneDrive",
                         "settings", "Business1"),
        )
    for candidate in candidates_cisco:
        try:
            if os.path.isdir(candidate):
                return "signed_in_cisco"
        except OSError:
            continue
    candidates_other = []
    if localappdata:
        candidates_other.append(
            os.path.join(localappdata, "Microsoft", "OneDrive",
                         "settings", "Personal"),
        )
    for candidate in candidates_other:
        try:
            if os.path.isdir(candidate):
                return "signed_in_other"
        except OSError:
            continue
    return "not_signed_in"


def _r68_onedrive_sentinel_present() -> bool:
    """Round 68 / Build 42 (B5): probe whether the canonical OneDrive
    sentinel file (``adoptiq_corpus_sentinel.json`` by default; env
    override ``ADOPTIQ_CORPUS_SENTINEL``) is present and non-empty
    in the configured ``CSONE_ONEDRIVE_FOLDER``.

    Used by the daily refresh worker to defer the
    blocked-to-synced transition refresh trigger until the sentinel
    has actually landed on disk.  Without this gate, the worker
    fires ``request_refresh()`` as soon as the folder reports
    ``synced`` (any non-empty file), which can happen several ticks
    before OneDrive finishes pulling the sentinel itself, producing
    a fail-loud ``CorpusCryptoError`` that the operator sees as
    ``Last refresh failed``.

    Returns ``False`` (rather than raising) on:

    * ``CSONE_ONEDRIVE_FOLDER`` unset.
    * Folder missing / not a directory.
    * Sentinel filename traversal block (env override smuggled "..").
    * Sentinel missing or zero-byte (Files-On-Demand stub).
    * Any ``OSError`` during stat.

    Returns ``True`` only when the sentinel exists, is a regular
    file, AND has size > 0 bytes.
    """
    onedrive_root = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
    if not onedrive_root:
        return False
    try:
        from corpus_crypto import resolve_sentinel_path  # local import: avoid bootstrap import order issues
        sentinel = resolve_sentinel_path(Path(str(onedrive_root)))
    except Exception:  # noqa: BLE001 - defensive
        return False
    if sentinel is None:
        return False
    try:
        if not sentinel.exists() or not sentinel.is_file():
            return False
        return sentinel.stat().st_size > 0
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Round 96 / runtime-only corpus: app-bundled corpus install retired
# ---------------------------------------------------------------------------


# Round 96: shipping builds copy no corpus artifacts from the app
# bundle.  The tuple remains as an explicit empty contract so tests can
# pin that app-bundled corpus data is retired.  ``corpus.db.salt`` still
# follows ``corpus_crypto._salt_path_for`` when a runtime corpus is
# created locally, but the app bundle must not provide either artifact.
#
# ``_LEGACY_BAKED_CORPUS_FILES`` retains the historical 4-tuple so
# reset/preserve loops can rotate broken legacy artifacts that already
# exist in a user's App Support directory.  Do NOT use it for
# install-time copying or app-resource discovery.
_BAKED_CORPUS_FILES: tuple = ()
_LEGACY_BAKED_CORPUS_FILES: tuple = (
    "corpus.db.enc",
    "sentinel.json",
    "corpus.db.salt",
    "corpus.sentinel.lock.json",
)


def _baked_corpus_dir() -> Optional[Path]:
    """Round 96: baked corpus lookup is retired.

    Keep the helper as a compatibility stub for older tests/tools, but
    always return ``None`` so no local development bake directory can be
    accidentally treated as app-shipped data.
    """
    return None


def _user_corpus_dir() -> Path:
    """Return the writable directory where the runtime corpus lives."""
    return default_db_path().parent


# ---------------------------------------------------------------------------
# Round 39 / corpus crypto self-heal: probe-and-recover helpers
# ---------------------------------------------------------------------------


def _probe_existing_corpus_decrypts(user_db: Path) -> bool:
    """Round 39: probe whether the user's existing ``corpus.db.enc``
    can actually be decrypted with the sentinel/lock/salt sitting next
    to it.  Returns ``True`` when the open + close round-trips
    cleanly; ``False`` when ``open_corpus_for_user`` raises
    :class:`CorpusCryptoError` (the InvalidTag class of failures the
    self-heal path was built for).

    Any non-crypto exception is left to bubble -- those represent
    real OS-level failures (permission denied, ENOSPC, etc.) that
    the caller must surface, not silently overwrite a healthy user
    corpus over.

    The probe opens with ``create_if_missing=False`` so a missing DB
    cannot accidentally be auto-minted as part of the diagnostic; it
    must be a real prior install.
    """
    onedrive_root = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
    handle: Optional[EncryptedCorpusHandle] = None
    try:
        handle = open_corpus_for_user(
            onedrive_root=onedrive_root,
            encrypted_path=user_db,
            create_if_missing=False,
            # Round 53 / Phase 53.3 -- fail-closed.  The probe must
            # exercise the same hardening contract the runtime open
            # uses; otherwise an upgrade from a pre-Round-53 install
            # whose user_dir still has a local sentinel on disk would
            # decrypt cleanly via the legacy path and the self-heal
            # branch would never run.
            allow_local_sentinel=False,
        )
    except CorpusCryptoError:
        return False
    finally:
        if handle is not None:
            try:
                handle.close(persist=False)
            except Exception:  # noqa: BLE001 - probe path
                logger.debug(
                    "Round 39 / corpus_bootstrap: probe handle close failed",
                    exc_info=True,
                )
    return True


def _read_lock_minted_at(user_dir: Path) -> Optional[str]:
    """Round 39: best-effort read of ``corpus.sentinel.lock.json``'s
    ``minted_at`` field for forensic logging.  Returns ``None`` when
    the lock is missing, malformed, or unreadable -- never raises."""
    import json
    try:
        lock_path = user_dir / "corpus.sentinel.lock.json"
        if not lock_path.is_file():
            return None
        with lock_path.open("rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
        if not isinstance(data, dict):
            return None
        minted = data.get("minted_at")
        return str(minted) if minted else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _preserve_broken_corpus(user_dir: Path) -> Optional[str]:
    """Round 39: rotate the four current corpus artifacts to
    ``<name>.broken-<utc_iso>`` so we keep one rolling backup of the
    last broken state for forensics, then bound disk usage by deleting
    any *prior* ``*.broken-*`` files first.  Returns the timestamp
    suffix on success, ``None`` when the user_dir has no corpus.db.enc
    to preserve (idempotent no-op).

    Renames are atomic (``os.replace``); a partial failure logs and
    returns ``None`` but does not raise (the install path then falls
    back to the existing-file branch and the user keeps their broken
    corpus rather than ending up in a half-renamed state).

    Cap at one rolling backup: a stuck-bake-loop must not accumulate
    280-MB sidecars on every boot.
    """
    user_db = user_dir / "corpus.db.enc"
    if not user_db.exists():
        return None

    # First, prune any prior .broken-* sidecars (cap=1).  Done before
    # the rename so a crash here cannot leave us with two backup sets.
    # Scan against the LEGACY 4-tuple so a prior Round 39 rotation
    # of the old ``sentinel.json`` / ``corpus.sentinel.lock.json``
    # sidecars also gets cleaned up.
    try:
        for child in user_dir.iterdir():
            try:
                name = child.name
                if any(
                    name.startswith(f + ".broken-")
                    for f in _LEGACY_BAKED_CORPUS_FILES
                ):
                    child.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError as prune_err:
        logger.warning(
            "Round 39 / corpus_bootstrap: prior broken-backup prune "
            "failed: %s",
            type(prune_err).__name__,
        )

    # Compute the suffix once so the files share one timestamp
    # (makes correlating them in support trivial).  We rotate the
    # LEGACY 4-tuple here -- pre-Round-53 installs may still have
    # the local sentinel + lock on disk, and rotating them too
    # ensures the broken-corpus sidecar is internally consistent
    # (no orphan sentinel pointing at a renamed .enc).
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    renamed: list[Path] = []
    for fname in _LEGACY_BAKED_CORPUS_FILES:
        src = user_dir / fname
        if not src.exists():
            # Some artifacts may be missing already (e.g., legacy
            # install with no lock).  Skip silently.
            continue
        dest = user_dir / f"{fname}.broken-{suffix}"
        try:
            os.replace(src, dest)
            renamed.append(dest)
        except OSError as rename_err:
            logger.warning(
                "Round 39 / corpus_bootstrap: broken-corpus preserve "
                "failed name=%s err=%s",
                fname, type(rename_err).__name__,
            )
            # Roll back any successful renames so we do not leave a
            # half-renamed state (that would confuse subsequent boots
            # into thinking the corpus is gone when it is just
            # mid-rename).
            for moved in renamed:
                try:
                    original = user_dir / moved.name.split(".broken-", 1)[0]
                    os.replace(moved, original)
                except OSError:
                    pass
            return None
    return suffix if renamed else None


def _install_baked_corpus_if_present() -> Optional[str]:
    """Round 96: legacy no-op.

    AdoptIQ no longer ships corpus data inside the app bundle.  The
    runtime corpus is created only after the user's authorized OneDrive
    sync exposes the corpus folder and sentinel, then the indexer walks
    that local source.  Keep this symbol temporarily so older tests or
    support tools that import it fail harmlessly instead of reinstalling
    stale data from a local development bake directory.
    """
    return None


# ---------------------------------------------------------------------------
# Round 35 / native-corpus: daily-refresh worker
# ---------------------------------------------------------------------------


def _should_refresh(
    *,
    last_refresh_ts: Optional[float],
    now: Optional[float] = None,
    interval_s: float = _DAILY_REFRESH_INTERVAL_S,
) -> bool:
    """Return True when the corpus is due for its periodic refresh.

    ``last_refresh_ts`` is float epoch seconds (None ≙ never
    refreshed).  ``now`` defaults to ``time.time()``; tests can pin
    it to exercise edge cases without sleep.
    """
    if interval_s <= 0:
        return True
    if now is None:
        now = time.time()
    if last_refresh_ts is None:
        return True
    return (now - float(last_refresh_ts)) >= float(interval_s)


def _next_refresh_tick_s(blocked_streak: int) -> float:
    """Round 53 / Phase 53.4.2 (extended Round 83 / Build 59):
    compute how long the daily-refresh loop should sleep before its
    next iteration.

    * When the corpus is in EITHER ``blocked_no_onedrive`` (not
      signed in to OneDrive) OR ``signed_in_no_corpus`` (signed in
      but corpus share missing) AND we have not yet hit the bounded
      retry cap, return the accelerated 30-second tick so we
      transition out of the blocked state promptly once the user
      either signs in to OneDrive (``blocked_no_onedrive`` ->
      ``synced``) or adds the corpus share to their OneDrive tree
      (``signed_in_no_corpus`` -> ``synced``).
    * Otherwise return the standard hourly tick.

    ``blocked_streak`` is the number of consecutive ticks the loop
    has spent observing either blocked state.  Resets to 0 the
    moment the state clears.  Pinned by
    ``tests/test_round53_ux_helpers.py`` and
    ``tests/test_round83_daily_worker_bootstrap_trigger.py``.
    """
    with _BOOT_LOCK:
        source = _STATE.source
    blocked = (
        source == "blocked_no_onedrive" or source == "signed_in_no_corpus"
    )
    if blocked and blocked_streak < _DAILY_REFRESH_BLOCKED_MAX_TICKS:
        return _DAILY_REFRESH_TICK_BLOCKED_S
    return _DAILY_REFRESH_TICK_S


def _daily_refresh_loop() -> None:
    """Body of the daily-refresh daemon.

    Wakes once per ``_DAILY_REFRESH_TICK_S`` (1h) by default; while
    the corpus is in the ``blocked_no_onedrive`` state the tick
    accelerates to ``_DAILY_REFRESH_TICK_BLOCKED_S`` (30 s) so the
    panel transitions out of "Sign in to OneDrive" promptly once the
    user signs in.  After ``_DAILY_REFRESH_BLOCKED_MAX_TICKS`` (240
    ticks = 2 h) the tick reverts to hourly so an unsynced user does
    not get a perpetual 30-second polling loop.

    Triggers an incremental refresh on each tick when:

      * the feature flag is on,
      * a successful runtime index or prior refresh has populated
        ``_STATE.last_successful_refresh_ts``, AND
      * ``_should_refresh()`` says we're past the 24h window, AND
      * the OneDrive desktop client has the canonical AdoptIQ folder
        synced to disk (``_check_onedrive_sync_status() == "synced"``).

    Round 53 also kicks an immediate refresh whenever the loop
    observes a blocked-to-synced transition -- without this the user
    who just signed in would have to wait the full 24 h window
    before the corpus actually unlocks.

    Round 36 / onedrive-sync-auth: the legacy MSAL refresh-token gate
    has been removed -- we trust the OneDrive desktop client to keep
    the local mirror current, and just verify by stat()ing the folder.

    Refreshes piggy-back on ``request_refresh()`` -> the existing
    ``_run_index_pass()`` machinery, which already writes the
    encrypted DB atomically (sibling-tmp + ``os.replace`` inside
    ``EncryptedCorpusHandle.commit_to_disk``) so a mid-refresh crash
    leaves the prior corpus intact (Phase 4c atomic-swap semantics).
    """
    # Round 61 / Phase 2.E: gate the start log on the same closed-stream
    # check as the exit log below.  Both fire at lifecycle boundaries
    # which can coincide with pytest teardown; both must skip the
    # logger call when reachable streams are closed to avoid the
    # ``Handler.handleError()`` -> stderr traceback path.
    # Round 62 / A1: replaced the inline gate+try/except wrap with a
    # call through the ``_safe_log_info`` helper that now centralizes
    # the closed-stream defense for every ``logger.info`` site in this
    # module.  Behavior is unchanged.
    _safe_log_info(
        "Round 53 / corpus_bootstrap: daily refresh worker started "
        "(interval=%.0fs tick=%.0fs blocked_tick=%.0fs blocked_cap=%d)",
        _DAILY_REFRESH_INTERVAL_S,
        _DAILY_REFRESH_TICK_S,
        _DAILY_REFRESH_TICK_BLOCKED_S,
        _DAILY_REFRESH_BLOCKED_MAX_TICKS,
    )
    blocked_streak = 0
    while not _DAILY_REFRESH_STOP.is_set():
        # Sleep with .wait() so stop() can interrupt the worker
        # quickly during process shutdown.  Round 53: tick interval
        # depends on the current blocked state.
        tick = _next_refresh_tick_s(blocked_streak)
        if _DAILY_REFRESH_STOP.wait(tick):
            break
        try:
            if not is_enabled():
                blocked_streak = 0
                continue
            with _BOOT_LOCK:
                last_ts = _STATE.last_successful_refresh_ts
                in_progress = _STATE.in_progress
                source_at_tick_start = _STATE.source
            if in_progress:
                continue
            # Round 53 / Phase 53.4.2: re-probe OneDrive on every
            # tick so a blocked-to-synced transition is detected
            # within the accelerated window.
            od_status, od_count, _ = _check_onedrive_sync_status()
            with _BOOT_LOCK:
                _STATE.onedrive_status = od_status
                _STATE.onedrive_file_count = od_count
            # Round 83 / Build 59: ``signed_in_no_corpus`` is the new
            # blocked state added when the OneDrive desktop client is
            # signed in to a Cisco account but the corpus share is
            # not in the user's tree yet. Same transition semantics
            # as ``blocked_no_onedrive`` -- the user takes action
            # (adds the share via the panel button), the OneDrive
            # client mirrors it locally, the next tick observes
            # ``onedrive_status == 'synced'`` and ``sentinel_present``,
            # and the worker fires an immediate refresh.
            blocked_now = (
                source_at_tick_start == "blocked_no_onedrive"
                or source_at_tick_start == "signed_in_no_corpus"
            )
            # Round 53: blocked-to-synced transition detection.  When
            # the loop sees the user just synced, kick an immediate
            # refresh regardless of the 24h window so the corpus
            # unlocks promptly.  ``_should_refresh()`` would otherwise
            # gate this for fresh installs whose
            # ``last_successful_refresh_ts`` is still None.
            #
            # Round 68 / Build 42 (B5): the OneDrive folder can flip
            # to ``synced`` (folder exists + >=1 non-zero file) several
            # ticks BEFORE the canonical sentinel finishes downloading
            # to disk.  Pre-R68 we'd happily fire ``request_refresh``
            # at that window, ``open_corpus_for_user`` would raise
            # ``CorpusCryptoError("corpus sentinel not found ...")``,
            # and the operator would see ``Last refresh failed`` for
            # ~24h until the daily worker's window-based refresh
            # naturally re-fired.  Mitigation: when the OneDrive folder
            # is synced but the sentinel has not landed yet, treat the
            # tick as still-blocked so we keep the accelerated 5s
            # cadence and re-check on the next tick.
            sentinel_present = _r68_onedrive_sentinel_present()
            transition_unblocked = (
                blocked_now and od_status == "synced" and sentinel_present
            )
            if blocked_now and od_status == "synced" and not sentinel_present:
                blocked_streak += 1
                logger.debug(
                    "Round 68 / Build 42 (B5): OneDrive folder is synced "
                    "but sentinel has not landed yet -- keeping the "
                    "accelerated tick cadence (blocked_streak=%d "
                    "source=%s)",
                    blocked_streak, source_at_tick_start,
                )
                continue
            if blocked_now and not transition_unblocked:
                blocked_streak += 1
                # While still blocked we only need to keep ticking;
                # there is nothing the indexer can do until the user
                # finishes either the OneDrive sign-in (legacy
                # ``blocked_no_onedrive``) or the share-shortcut add
                # (Round 83 ``signed_in_no_corpus``).
                continue
            if not transition_unblocked:
                if not _should_refresh(last_refresh_ts=last_ts):
                    blocked_streak = 0
                    continue
                if od_status != "synced":
                    logger.debug(
                        "Round 36 / corpus_bootstrap: daily refresh skipped "
                        "(onedrive_status=%s)",
                        od_status,
                    )
                    blocked_streak = 0
                    continue
            blocked_streak = 0
            _safe_log_info(
                "Round 53 / corpus_bootstrap: triggering refresh "
                "(last_successful=%s onedrive_files=%s "
                "transition_unblocked=%s sentinel_present=%s)",
                last_ts, od_count, transition_unblocked, sentinel_present,
            )
            with _BOOT_LOCK:
                _STATE.last_refresh_attempt_ts = time.time()
                _STATE.last_refresh_error = None
            try:
                request_refresh(rebuild=False)
            except Exception as refresh_err:  # noqa: BLE001 - never bubble
                with _BOOT_LOCK:
                    _STATE.last_refresh_error = type(refresh_err).__name__
                logger.warning(
                    "Round 36 / corpus_bootstrap: daily refresh raised: %s",
                    type(refresh_err).__name__,
                )
        except Exception as loop_err:  # noqa: BLE001 - never bubble
            blocked_streak = 0
            logger.warning(
                "Round 36 / corpus_bootstrap: daily refresh loop iteration "
                "failed: %s",
                type(loop_err).__name__,
            )
    # Round 61 / Phase 2.E: pytest teardown closes the logging stream
    # (sys.stderr / sys.stdout) BEFORE this daemon thread wakes from
    # .wait() and falls through to the exit log.  The resulting
    # "I/O operation on closed file." ValueError is caught INSIDE
    # ``logging.StreamHandler.emit()`` (logging/__init__.py:1113) and
    # then surfaced via ``Handler.handleError()`` which prints the
    # traceback to stderr regardless of any try/except wrapped around
    # the ``logger.info()`` call.  The ONLY way to suppress the noise
    # is to detect the closed-stream condition BEFORE calling
    # ``logger.info()``.  Walk our logger AND its propagation chain;
    # if any reachable StreamHandler is wired to a closed stream,
    # skip the exit log entirely.  Real runs (where streams are
    # alive) still emit the log line exactly once.
    # Round 62 / A1: replaced the inline gate+try/except wrap with a
    # call through the ``_safe_log_info`` helper.  Same defense, less
    # duplication; the helper docstring carries the long explanation
    # of WHY we cannot just rely on Python's logging exception path.
    _safe_log_info("Round 36 / corpus_bootstrap: daily refresh worker exiting")


def _safe_log_info(msg: str, *args: object) -> None:
    """Round 63: thin wrapper around ``_logging_helpers.safe_log_info``.

    The implementation moved out of this module in R63 to deduplicate
    the helper that ``corpus_indexer.py`` had been carrying as a
    near-byte-equivalent copy (R62 / A1 had to duplicate it because
    ``corpus_bootstrap`` already imports from ``corpus_indexer`` and
    a direct import in the other direction would create a cycle).
    The wrapper name is preserved so the R61 + R62 tests that
    reference ``corpus_bootstrap._safe_log_info`` keep passing
    unchanged.  See ``_logging_helpers.safe_log_info`` for the full
    explanation of why a try/except wrap alone is insufficient.
    Pinned by ``tests/test_round63_logging_helpers.py``.
    """
    _shared_safe_log_info(logger, msg, *args)


def _safe_log_warning(msg: str, *args: object) -> None:
    """Round 98: warning-level closed-stream sibling for pytest/app teardown."""

    _shared_safe_log_warning(logger, msg, *args)


def _safe_log_exception(msg: str, *args: object) -> None:
    """Round 101: exception-level closed-stream sibling for pytest/app teardown."""

    _shared_safe_log_exception(logger, msg, *args)


def _exit_log_streams_open() -> bool:
    """Round 63: thin wrapper around
    ``_logging_helpers.exit_log_streams_open``.  Wrapper name preserved
    for back-compat with R61 + R62 tests."""
    return _shared_exit_log_streams_open(logger)


def start_daily_refresh_worker() -> bool:
    """Spawn the daily-refresh daemon if it is not already running.
    Returns ``True`` when a new thread was started, ``False`` when one
    is already alive.
    """
    global _DAILY_REFRESH_THREAD
    with _BOOT_LOCK:
        if _DAILY_REFRESH_THREAD is not None and _DAILY_REFRESH_THREAD.is_alive():
            return False
        _DAILY_REFRESH_STOP.clear()
        thread = threading.Thread(
            target=_daily_refresh_loop,
            name=_DAILY_REFRESH_THREAD_NAME,
            daemon=True,
        )
        _DAILY_REFRESH_THREAD = thread
        thread.start()
        return True


def _index_stats_to_dict(stats: IndexStats) -> dict[str, object]:
    """Marshal :class:`IndexStats` into a dict the admin tile / JSON
    endpoint can render.

    Round 26 - review (R26-OPEN-001): the original Round 17 design
    emitted only ``len(stats.errors)`` because filenames could leak
    customer names.  AdoptIQ is internal-only and the original
    Phase E goal of "operators diagnose a degraded run from the
    dashboard without tailing logs" requires actual filenames in the
    payload.  We now expose:

    * ``errors``      -- a list of ``{"file": str, "reason": str}``
      dicts (first 5 entries), parsed from the
      ``"<filename>: <ExceptionClass>"`` strings the indexer
      appends to ``IndexStats.errors``.
    * ``errors_total`` -- the full count, so the tile can render
      "5 of 12 shown" when truncated.

    The ``conn=None`` edge case (no filename to split) is rendered
    as ``{"file": "?", "reason": "<original string>"}`` so the
    template branch in
    :file:`enhanced_admin_dashboard_v2.py` doesn't have to handle
    it specially.
    """
    error_rows: list[dict[str, str]] = []
    for raw in list(stats.errors)[:5]:
        s = str(raw)
        if ":" in s:
            file_part, _, reason_part = s.partition(":")
            error_rows.append(
                {
                    "file": file_part.strip() or "?",
                    "reason": reason_part.strip() or "error",
                }
            )
        else:
            error_rows.append({"file": "?", "reason": s.strip() or "error"})

    return {
        "files_seen": int(stats.files_seen),
        "files_parsed": int(stats.files_parsed),
        "files_skipped": int(stats.files_skipped),
        "files_failed": int(stats.files_failed),
        "files_oversized": int(stats.files_oversized),
        "chunks_added": int(stats.chunks_added),
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
        # Round 26 - review (R26-OPEN-001): list of dicts, capped at 5.
        "errors": error_rows,
        # Round 26 - review (R26-OPEN-001): full count so the tile
        # can render "N of M shown" when the list is truncated.
        "errors_total": int(len(stats.errors)),
    }


def _accumulate_index_stats(target: IndexStats, source: IndexStats) -> None:
    """Round 17.1: roll a per-source ``IndexStats`` into the aggregate
    one used by the admin tile.  Mirrors :func:`_index_stats_to_dict`
    but mutates ``target`` in place.  Errors list is intentionally
    capped at 50 entries to bound memory."""
    target.files_seen += int(source.files_seen)
    target.files_parsed += int(source.files_parsed)
    target.files_skipped += int(source.files_skipped)
    target.files_failed += int(source.files_failed)
    target.files_oversized += int(source.files_oversized)
    target.chunks_added += int(source.chunks_added)
    if source.errors:
        space = max(0, 50 - len(target.errors))
        if space:
            target.errors.extend(list(source.errors[:space]))
    if not target.started_at and source.started_at:
        target.started_at = source.started_at
    if source.finished_at:
        target.finished_at = source.finished_at


# Round 80: locate AdoptIQ's writable outputs directory so the
# corpus indexer can ingest reports the running app produces.  This
# is the same path ``app_simple.py`` writes to (mac:
# ``~/Library/Application Support/AdoptIQ/outputs/``, win:
# ``%APPDATA%\AdoptIQ\outputs\``, else ``~/.adoptiq/outputs/``).
# Returns ``None`` on resolution failure or when the directory does
# not exist so the bootstrap stays robust.  Honours the
# ``ADOPTIQ_OUTPUTS_DIR`` env override unconditionally so tests and
# power users can pin any path.  Pinned by
# ``tests/test_round80_local_outputs_corpus_source.py``.
def _r80_resolve_app_support_outputs_dir() -> Optional[Path]:
    """Round 80: return the absolute path to AdoptIQ's writable
    ``outputs/`` directory if it exists, else ``None``."""
    override = os.environ.get('ADOPTIQ_OUTPUTS_DIR')
    if override:
        try:
            override_path = Path(str(override))
            return override_path if override_path.is_dir() else None
        except Exception:
            return None
    # Round 92: use the same Documents/default/settings/env resolver as
    # the report writers so generated reports do not split away from
    # corpus indexing when the operator changes the report folder.
    try:
        from report_output_paths import get_report_outputs_root  # noqa: PLC0415

        resolved = Path(get_report_outputs_root(create=False))
        return resolved if resolved.is_dir() else None
    except Exception:
        pass
    # Round 80 fallback
    try:
        if sys.platform == 'darwin':
            base = Path.home() / 'Library' / 'Application Support' / 'AdoptIQ'
        elif sys.platform == 'win32':
            base = Path(os.environ.get('APPDATA', str(Path.home()))) / 'AdoptIQ'
        else:
            base = Path.home() / '.adoptiq'
        outputs = base / 'outputs'
        return outputs if outputs.is_dir() else None
    except Exception:
        return None


def _resolve_index_sources() -> list[dict[str, object]]:
    """Round 17.1 + 26 + 36 + 80: enumerate the corpus index sources
    for the current bootstrap pass.  Order matters because
    ``index_folder`` rebuilds only on the first source -- subsequent
    sources do incremental upserts on top of the rows the first
    source wrote.

    Priority (Round 102 -- local_outputs retained, user_downloads
    retired):

    1. ``onedrive`` -- the synced OneDrive folder
       (``Config.CSONE_ONEDRIVE_FOLDER``); appended only when the
       resolved directory actually exists so we don't waste a walker
       pass logging a "missing" warning on every refresh.  Round 80
       narrows discovery to the SharePoint-shortcut leaf only -- see
       ``config._csone_onedrive_candidates``.
    2. ``local_outputs`` (Round 80, NEW) -- ``_APP_SUPPORT/outputs/``
       filtered to AdoptIQ report names, so reports the running app
       generates are picked up by the next refresh tick without
       requiring the user to manually drop them into another folder.
       Always present (the .app creates the directory at startup) and
       permission-clean (no macOS Files-and-Folders prompt).
    3. ``intel_uploads`` (Round 26) -- per-user drop folder
       populated by ``/api/intel/upload``.  Walked when the
       directory exists.  Pre-create is gated on
       ``ADOPTIQ_INTEL_UPLOAD_ENABLED`` (Round 26 review /
       R26-OPEN-002), so disabled installs leave the source
       absent unless an admin pre-seeds the directory by hand --
       at which point the walker still picks it up.

    Round 36 / onedrive-sync-auth: the legacy ``sharepoint_csone``
    source has been removed.  MSAL/Graph runtime auth was blocked
    by Cisco tenant admin-consent on the default Microsoft Graph
    PowerShell client ID; the OneDrive desktop client already
    handles that auth flow and produces the same files on disk
    under ``Config.CSONE_ONEDRIVE_FOLDER``.

    Each entry is a self-describing dict so the admin tile can render
    labels without having to hard-code the order.
    """
    sources: list[dict[str, object]] = []

    # 1) OneDrive sync (Round 36: now the primary runtime source).
    onedrive_root = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
    if onedrive_root:
        try:
            onedrive_exists = Path(str(onedrive_root)).is_dir()
        except Exception:
            onedrive_exists = False
        if onedrive_exists:
            sources.append(
                {
                    "label": "onedrive",
                    "dir": str(onedrive_root),
                    "filter": "all_supported",
                    "quality_gate": "strict_generated_report",  # Round 94
                }
            )
        else:
            _safe_log_info(
                "Round 36 / corpus_bootstrap: onedrive source skipped "
                "(no synced copy at %s)",
                onedrive_root,
            )

    # 2) Round 80: AdoptIQ's own _APP_SUPPORT/outputs/ folder.  This
    # is where every generated report lands so it's always present
    # and always permission-clean.  Slotted after OneDrive so the
    # OneDrive source remains the primary signal for the corpus walker's
    # rebuild decision.
    outputs_dir = _r80_resolve_app_support_outputs_dir()
    if outputs_dir is not None:
        sources.append(
            {
                "label": "local_outputs",
                "dir": str(outputs_dir),
                "filter": "adoptiq_named",
                "quality_gate": "strict_generated_report",  # Round 92
            }
        )

    # 3) Round 26: per-user uploaded CSOne reports.  We walk this
    # source unconditionally when the directory exists -- not gated
    # on ``ADOPTIQ_INTEL_UPLOAD_ENABLED`` -- so admin-pre-seeded
    # files are still ingested even when the live upload endpoint
    # is turned off.  The walker uses ``all_supported`` so any
    # CSOne export shape (xlsx / csv / docx / pdf) the operator
    # drops in is picked up.  Filename hygiene is enforced at
    # upload time (``secure_filename`` + uuid prefix); files
    # placed by hand are trusted to come from the operator.
    intel_uploads_dir = getattr(Config, "CSONE_INTEL_UPLOADS_FOLDER", None)
    if intel_uploads_dir:
        try:
            intel_uploads_path = Path(str(intel_uploads_dir))
            intel_exists = intel_uploads_path.is_dir()
        except Exception:
            intel_exists = False
        if intel_exists:
            sources.append(
                {
                    "label": "intel_uploads",
                    "dir": str(intel_uploads_path),
                    "filter": "all_supported",
                    "quality_gate": "strict_generated_report",  # Round 94
                }
            )
    return sources


def _run_index_pass(*, rebuild: bool) -> None:
    """Body of the bootstrap thread.  Round 17.1: walks every entry in
    :func:`_resolve_index_sources` (OneDrive + the runtime user's
    Downloads, when enabled).  Stats are accumulated across sources
    and the per-source breakdown is recorded on the singleton state
    for the admin tile.  Must not raise; on error it records
    ``last_error`` so the admin tile shows the failure."""
    global _HANDLE, _SIGNAL

    onedrive_root = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
    encrypted_path = default_db_path().with_suffix(".db.enc")

    # Round 96 / runtime-only corpus: do not install a corpus snapshot
    # from the app bundle.  The encrypted DB is created or refreshed
    # only after OneDrive + sentinel checks pass below, using the
    # authorized local OneDrive mirror as the source of truth.

    # Round 36 / onedrive-sync-auth: probe sync status before the
    # index pass so the panel can render "synced" / "not_synced"
    # immediately (the slow indexer walk no longer gates the UI's
    # ability to tell the user whether OneDrive is talking to disk).
    od_status, od_count, _ = _check_onedrive_sync_status()
    # Round 83 / Build 59: probe sign-in proxy independently so the
    # panel can distinguish "not signed in to OneDrive" from "signed
    # in but the AdoptIQ corpus folder is not synced yet" -- two
    # separate UX states with different bootstrap CTAs.
    signed_in_proxy = _r83_onedrive_signed_in_proxy()

    with _BOOT_LOCK:
        _STATE.in_progress = True
        _STATE.last_started_at = _utc_now_iso()
        _STATE.encrypted_path = str(encrypted_path)
        _STATE.onedrive_root = str(onedrive_root) if onedrive_root else None
        _STATE.onedrive_status = od_status
        _STATE.onedrive_file_count = od_count
        _STATE.signed_in_proxy = signed_in_proxy
        _STATE.last_error = None
        _STATE.last_error_kind = None
        _STATE.last_sources = None
        if _STATE.source is None:
            _STATE.source = "fresh"

    # Round 36 / onedrive-sync-auth: the legacy SharePoint cache pull
    # has been removed -- the OneDrive desktop client mirrors the
    # canonical folder under ``Config.CSONE_ONEDRIVE_FOLDER`` and
    # the indexer walks it directly.  Clear any stale state from
    # earlier rounds so the panel does not show a phantom "signed in"
    # tile after upgrade.
    with _BOOT_LOCK:
        _STATE.sharepoint = None

    sources = _resolve_index_sources()

    # Round 53 / Phase 53.3 -- fail-closed pre-flight gate.  If the
    # OneDrive desktop client has not synced the canonical AdoptIQ
    # folder OR the canonical sentinel is not present inside that
    # folder, we cannot derive the AES key (allow_local_sentinel=False
    # at runtime).  Surface a dedicated ``blocked_no_onedrive`` source
    # so the analyze panel can render a clear "Sign in to OneDrive"
    # CTA instead of a generic crypto error.
    sentinel_present = False
    if onedrive_root:
        try:
            from corpus_crypto import resolve_sentinel_path
            sentinel_path = resolve_sentinel_path(onedrive_root)
            sentinel_present = bool(
                sentinel_path is not None
                and sentinel_path.exists()
                and sentinel_path.is_file()
            )
        except CorpusCryptoError:
            sentinel_present = False
        except Exception:  # noqa: BLE001 - defensive; fail-closed
            sentinel_present = False
    if od_status != "synced" or not sentinel_present:
        with _BOOT_LOCK:
            # Round 83 / Build 59: split the legacy
            # ``blocked_no_onedrive`` state in two so the panel can
            # render different copy + a different bootstrap button:
            #
            #   * ``signed_in_no_corpus`` -- the OneDrive client is
            #     signed in with a Cisco account but the canonical
            #     AdoptIQ share is not in the user's tree yet. The
            #     panel surfaces a "Add the AdoptIQ share to your
            #     OneDrive" button that opens the SharePoint URL
            #     in the user's browser; SSO completes via Cisco
            #     IdP, the share lands as a shortcut, the OneDrive
            #     client mirrors it locally, and the next daily
            #     refresh tick picks it up.
            #   * ``blocked_no_onedrive`` -- the OneDrive client is
            #     not signed in at all (or signed in only with a
            #     non-Cisco account). The user must complete the
            #     OneDrive desktop client sign-in BEFORE the
            #     bootstrap shortcut button is meaningful, so the
            #     panel keeps the legacy "Sign in to OneDrive"
            #     copy.
            if signed_in_proxy == "signed_in_cisco":
                _STATE.source = "signed_in_no_corpus"
                _STATE.last_error = (
                    "OneDrive is signed in but the AdoptIQ corpus "
                    "share is not in your OneDrive tree yet.  Click "
                    "\"Add corpus share to my OneDrive\" to open the "
                    "share in your browser; OneDrive will mirror it "
                    "locally and AdoptIQ will pick it up on the next "
                    "refresh."
                )
                _STATE.last_error_kind = "no_corpus_share"
            else:
                _STATE.source = "blocked_no_onedrive"
                _STATE.last_error = (
                    "OneDrive sync of AI Projects/AdoptIQ_CSOne_Reports "
                    "is required to unlock the corpus.  Open the OneDrive "
                    "desktop client, sign in with your Cisco account, and "
                    "sync the folder."
                )
                _STATE.last_error_kind = "no_onedrive_sentinel"
            _STATE.in_progress = False
            _STATE.last_finished_at = _utc_now_iso()
            _STATE.completed = False
        _safe_log_info(
            "Round 53 / corpus_bootstrap: corpus open blocked "
            "(onedrive_status=%s sentinel_present=%s signed_in_proxy=%s "
            "source=%s)",
            od_status, sentinel_present, signed_in_proxy,
            "signed_in_no_corpus" if signed_in_proxy == "signed_in_cisco"
            else "blocked_no_onedrive",
        )
        configure_connection(None)
        return

    # Round 36 / onedrive-sync-auth: ``sharepoint_root`` is no longer
    # passed to ``open_corpus_for_user`` -- the SharePoint cache dir
    # has been retired.  Round 53: ``allow_local_sentinel=False`` so
    # we fail-closed when the OneDrive sentinel is unavailable
    # (defense in depth on top of the gate above; covers the race
    # where the sentinel disappears between the gate and the open).
    handle: Optional[EncryptedCorpusHandle] = None
    try:
        try:
            handle = open_corpus_for_user(
                onedrive_root=onedrive_root,
                encrypted_path=encrypted_path,
                create_if_missing=True,
                allow_local_sentinel=False,
            )
        except CorpusCryptoError as crypto_err:
            # Round 54 / F1 -- TOCTOU race UX fix.  If the OneDrive
            # sentinel disappeared between the Round 53 pre-flight
            # gate above and this open (the OneDrive desktop client
            # evicted the file, the user signed out, the share was
            # un-shared, etc.), the open fails-closed with a generic
            # CorpusCryptoError.  Pre-Round-54 the user-facing UI
            # then surfaced the legacy ``crypto`` path (Reset Corpus
            # button, no actionable remediation), even though the
            # actual root cause is "OneDrive is no longer providing
            # the sentinel".  Re-probe the gate; if it now fails,
            # re-emit as ``blocked_no_onedrive`` so the user sees
            # the same Sign-in CTA + clickable deep link they would
            # have seen if the gate had caught it on the first pass.
            # Security is unaffected (open already fails-closed); this
            # is purely UX clarity.
            post_status, post_count, _ = _check_onedrive_sync_status()
            post_sentinel_present = False
            if onedrive_root:
                try:
                    from corpus_crypto import resolve_sentinel_path
                    post_sentinel_path = resolve_sentinel_path(onedrive_root)
                    post_sentinel_present = bool(
                        post_sentinel_path is not None
                        and post_sentinel_path.exists()
                        and post_sentinel_path.is_file()
                    )
                except CorpusCryptoError:
                    post_sentinel_present = False
                except Exception:  # noqa: BLE001 - defensive; fail-closed
                    post_sentinel_present = False
            if post_status != "synced" or not post_sentinel_present:
                with _BOOT_LOCK:
                    _STATE.source = "blocked_no_onedrive"
                    _STATE.last_error = (
                        "OneDrive sync of AI Projects/AdoptIQ_CSOne_Reports "
                        "is required to unlock the corpus.  Open the OneDrive "
                        "desktop client, sign in with your Cisco account, and "
                        "sync the folder."
                    )
                    _STATE.last_error_kind = "no_onedrive_sentinel"
                    _STATE.onedrive_status = post_status
                    _STATE.onedrive_file_count = post_count
                    _STATE.in_progress = False
                    _STATE.last_finished_at = _utc_now_iso()
                    _STATE.completed = False
                _safe_log_info(
                    "Round 54 / F1 corpus_bootstrap: TOCTOU race -- "
                    "open raised CorpusCryptoError and re-probe shows "
                    "(onedrive_status=%s sentinel_present=%s); "
                    "surfacing blocked_no_onedrive instead of generic crypto",
                    post_status, post_sentinel_present,
                )
                configure_connection(None)
                return
            # Round 99: runtime-only corpus recovery.  A synced OneDrive
            # folder plus present sentinel means this is not the Round 54
            # "sentinel disappeared" race.  The common upgrade failure is a
            # stale local encrypted corpus sealed under an older sentinel.  Keep
            # a bounded forensic sidecar set, then retry the open so the
            # runtime indexer can rebuild from the authorized OneDrive mirror.
            preserved_suffix = _preserve_broken_corpus(encrypted_path.parent)
            if preserved_suffix:
                _safe_log_warning(
                    "Round 99 / corpus_bootstrap: preserved stale runtime "
                    "corpus artifacts as .broken-%s after crypto mismatch; "
                    "retrying clean runtime index",
                    preserved_suffix,
                )
                try:
                    handle = open_corpus_for_user(
                        onedrive_root=onedrive_root,
                        encrypted_path=encrypted_path,
                        create_if_missing=True,
                        allow_local_sentinel=False,
                    )
                except CorpusCryptoError as retry_err:
                    with _BOOT_LOCK:
                        _STATE.last_error = (
                            "corpus decrypt failed after stale-artifact "
                            f"preserve: {retry_err}"
                        )
                        _STATE.last_error_kind = "crypto"
                        _STATE.in_progress = False
                        _STATE.last_finished_at = _utc_now_iso()
                        _STATE.completed = False
                    _safe_log_warning(
                        "Round 99 / corpus_bootstrap: runtime corpus "
                        "crypto retry failed (%s)",
                        _STATE.last_error_kind,
                    )
                    configure_connection(None)
                    return
            else:
                _safe_log_warning(
                    "Round 99 / corpus_bootstrap: crypto mismatch had no "
                    "local runtime corpus artifacts to preserve; surfacing "
                    "crypto failure"
                )
                with _BOOT_LOCK:
                    _STATE.last_error = str(crypto_err)
                    _STATE.last_error_kind = "crypto"
                    _STATE.in_progress = False
                    _STATE.last_finished_at = _utc_now_iso()
                    _STATE.completed = False
                _safe_log_warning(
                    "Round 99 / corpus_bootstrap: corpus unavailable (%s)",
                    _STATE.last_error_kind,
                )
                configure_connection(None)
                return

        signal = IndexSignal()
        with _BOOT_LOCK:
            _HANDLE = handle
            _SIGNAL = signal

        configure_connection(handle.conn)

        aggregate = IndexStats()
        per_source: list[dict[str, object]] = []
        # First source uses the caller's ``rebuild`` flag; subsequent
        # sources never rebuild (they'd wipe the rows that the
        # earlier source just wrote).
        first = True
        for source in sources:
            label = str(source.get("label") or "unknown")
            src_dir = source.get("dir")
            filter_kind = source.get("filter") or "all_supported"
            if not src_dir:
                _safe_log_info(
                    "Round 17 / corpus_bootstrap: source=%s skipped (no dir configured)",
                    label,
                )
                continue
            try:
                # Build the file list with the source-specific filter,
                # then hand the resulting CorpusFile objects to
                # ``index_folder``.  ``index_folder`` accepts either a
                # path or a list, so we just call it with the path and
                # let it use its own walker for the OneDrive case (the
                # filter there is "all supported extensions").  For
                # the Downloads case we pass a pre-filtered list via
                # the dedicated user-report walker.
                #
                # Round 81 / Build 57: ``local_outputs`` (the
                # AdoptIQ-managed ``<APP_SUPPORT>/outputs/`` tree) is
                # walked recursively so the new ``<Manager>/<Type>/``
                # nested layout is fully picked up. Round 102 retires
                # the old Downloads source entirely, so no user home
                # folder is walked as part of corpus bootstrap.
                if filter_kind == "adoptiq_named":
                    _r81_recursive = label == "local_outputs"
                    try:
                        files = enumerate_user_report_files(
                            src_dir,
                            signal=signal,
                            recursive=_r81_recursive,
                            require_adoptiq_quality_gate=(label == "local_outputs"),  # Round 92
                        )
                    except TypeError:
                        # Older monkeypatched walker without the R81
                        # ``recursive`` kwarg -- fall back to legacy
                        # call shape so test fakes do not break.
                        files = enumerate_user_report_files(
                            src_dir, signal=signal
                        )
                    src_stats = index_folder(
                        handle.conn,
                        src_dir,
                        signal=signal,
                        rebuild=False,
                        files=files,
                    )
                else:
                    src_stats = index_folder(
                        handle.conn,
                        src_dir,
                        signal=signal,
                        rebuild=bool(rebuild) if first else False,
                    )
            except TypeError:
                # Older index_folder signature without ``files`` arg --
                # fall back to the default walker.  Only reachable in
                # tests that monkey-patch the indexer.
                src_stats = index_folder(
                    handle.conn,
                    src_dir,
                    signal=signal,
                    rebuild=bool(rebuild) if first else False,
                )
            except Exception as index_err:  # noqa: BLE001 - defensive; never bubble
                # Round 71 / Phase 2 (#12): pre-R71 the indexer error
                # branch updated state + returned, leaving:
                #   - the encrypted handle open on a partially-written
                #     plaintext temp file (next refresh would inherit
                #     its sqlite cursor state).
                #   - ``configure_connection`` still pointing at the
                #     same partial handle, so Ask AI queries would
                #     read from a corpus that the indexer had given
                #     up on mid-batch.
                #   - the module-level ``_HANDLE`` still bound, so the
                #     next refresh would re-use the partial DB instead
                #     of bootstrapping a clean encrypted snapshot.
                # Tear all three down here so the next refresh starts
                # from the prior good encrypted DB on disk.  ``_HANDLE``
                # is already declared ``global`` at the top of
                # ``_run_index_pass``; reassigning it inside this
                # except branch is intentional.
                try:
                    configure_connection(None)
                except Exception as cleanup_err:  # noqa: BLE001
                    logger.debug(
                        "Round 71 / corpus_bootstrap: configure_connection(None) "
                        "during indexer error cleanup raised: %s",
                        type(cleanup_err).__name__,
                    )
                try:
                    handle.close(persist=False)
                except Exception as cleanup_err:  # noqa: BLE001
                    logger.debug(
                        "Round 71 / corpus_bootstrap: handle.close(persist=False) "
                        "during indexer error cleanup raised: %s",
                        type(cleanup_err).__name__,
                    )
                with _BOOT_LOCK:
                    _STATE.last_error = type(index_err).__name__
                    _STATE.last_error_kind = "indexer"
                    _STATE.in_progress = False
                    _STATE.last_finished_at = _utc_now_iso()
                    _HANDLE = None
                _safe_log_exception(
                    "Round 17 / corpus_bootstrap: indexer raised on source=%s",
                    label,
                )
                return

            first = False
            per_source.append(
                {
                    "label": label,
                    "dir": str(src_dir),
                    "filter": filter_kind,
                    "files_seen": int(src_stats.files_seen),
                    "files_parsed": int(src_stats.files_parsed),
                    "files_skipped": int(src_stats.files_skipped),
                    "files_failed": int(src_stats.files_failed),
                    "chunks_added": int(src_stats.chunks_added),
                }
            )
            _safe_log_info(
                "Round 17 / corpus_bootstrap: corpus source=%s dir=%s "
                "files_seen=%d files_parsed=%d files_skipped=%d files_failed=%d "
                "chunks_added=%d",
                label,
                src_dir,
                int(src_stats.files_seen),
                int(src_stats.files_parsed),
                int(src_stats.files_skipped),
                int(src_stats.files_failed),
                int(src_stats.chunks_added),
            )
            _accumulate_index_stats(aggregate, src_stats)

        try:
            handle.commit_to_disk()
        except CorpusCryptoError as commit_err:
            with _BOOT_LOCK:
                _STATE.last_error = str(commit_err)
                _STATE.last_error_kind = "commit"
                _STATE.in_progress = False
                _STATE.last_finished_at = _utc_now_iso()
            logger.warning(
                "Round 17 / corpus_bootstrap: commit_to_disk failed (%s)",
                _STATE.last_error_kind,
            )
            return

        # Round 36 / onedrive-sync-auth: re-probe after the indexer
        # finishes so a folder that became available mid-pass (or a
        # folder that emptied) is reflected in the next status poll
        # without waiting for the bootstrap to re-run.
        post_status, post_count, _ = _check_onedrive_sync_status()

        with _BOOT_LOCK:
            _STATE.in_progress = False
            _STATE.completed = True
            _STATE.last_finished_at = _utc_now_iso()
            _STATE.last_stats = _index_stats_to_dict(aggregate)
            _STATE.last_sources = per_source if per_source else None
            # Round 35 / native-corpus: record success so the daily-
            # refresh worker's ``_should_refresh()`` math can advance
            # past the 24h window and so the UI can show "last
            # refreshed N hours ago".
            _STATE.last_successful_refresh_ts = time.time()
            _STATE.last_refresh_error = None
            _STATE.onedrive_status = post_status
            _STATE.onedrive_file_count = post_count
            # Round 96.1: a successful runtime index supersedes any
            # prior blocked source label from the same process.  The
            # JS panel maps ``fresh`` + synced + completed to
            # ``runtime_synced``.
            _STATE.source = "fresh"
            # Round 96: this timestamp now represents the runtime
            # local-index pass, not a build-time bake.
            _STATE.indexed_at = _STATE.last_finished_at
        _safe_log_info(
            "Round 17.1 / corpus_bootstrap: indexed files_parsed=%d chunks=%d sources=%d",
            int(aggregate.files_parsed),
            int(aggregate.chunks_added),
            len(per_source),
        )
    except Exception as boot_err:  # noqa: BLE001 - defensive
        with _BOOT_LOCK:
            _STATE.last_error = type(boot_err).__name__
            _STATE.last_error_kind = "bootstrap"
            _STATE.in_progress = False
            _STATE.last_finished_at = _utc_now_iso()
        _safe_log_exception("Round 17 / corpus_bootstrap: bootstrap raised")


def _warm_embedder_in_background() -> None:
    """Round 66 / Pass 5 - eagerly load the fastembed model on a daemon
    thread so the FIRST Ask AI query is not the one that pays the
    1-3s cold start. Idempotent (singleton inside ask_ai_embeddings)
    and graceful: any failure flips the runtime to lexical mode and
    records the error on ``_STATE.embedder_load_error`` for the
    diagnostics endpoint to surface.
    """

    def _warm() -> None:
        try:
            from ask_ai_embeddings import get_embedder, embedder_load_error
        except Exception as e:  # noqa: BLE001
            with _BOOT_LOCK:
                _STATE.embedder_status = "unavailable"
                _STATE.embedder_load_error = (
                    f"ask_ai_embeddings import failed: {type(e).__name__}: {e}"
                )
            try:
                from config import Config  # type: ignore
                Config.ASK_AI_RETRIEVAL_METHOD = "lexical"
            except Exception:  # noqa: BLE001
                pass
            return
        embedder = get_embedder()
        with _BOOT_LOCK:
            if embedder is None:
                _STATE.embedder_status = "unavailable"
                _STATE.embedder_load_error = embedder_load_error()
                try:
                    from config import Config  # type: ignore
                    Config.ASK_AI_RETRIEVAL_METHOD = "lexical"
                except Exception:  # noqa: BLE001
                    pass
            else:
                _STATE.embedder_status = "ready"
                _STATE.embedder_load_error = None

    threading.Thread(
        target=_warm,
        name="adoptiq-embedder-warmup",
        daemon=True,
    ).start()


def start_background(*, rebuild: bool = False, allow_refresh: bool = True) -> bool:
    """Spawn the bootstrap thread if it is not already running.
    Returns ``True`` when a new thread was started, ``False`` when the
    feature flag is off or an existing pass is in progress.

    Round 71 / Phase 2 (#9): pre-R71 the function unconditionally
    short-circuited when ``_STATE.completed=True and not rebuild``,
    which permanently silenced the daily refresh worker -- the worker
    calls ``request_refresh(rebuild=False)`` once per 24h to pick up
    new content, but after the very first successful index pass that
    request was a no-op for the rest of the process lifetime.  The
    operator-flippable corpus would then go stale (and the
    onedrive_status pill would happily say "synced" because the
    OneDrive folder DOES contain new files; only the in-process
    encrypted DB stayed at the first-pass snapshot).

    The new gate accepts ``allow_refresh=True`` (the default) which
    permits a re-index pass even when ``_STATE.completed=True``,
    provided no other pass is currently in flight.  Callers that
    explicitly want the legacy "only run once per process lifetime"
    behavior (e.g. an unhappy-path retry that would otherwise
    thrash) can pass ``allow_refresh=False``.
    """
    global _THREAD
    with _BOOT_LOCK:
        _STATE.enabled = is_enabled()
        if not _STATE.enabled:
            configure_connection(None)
            return False
        if _STATE.in_progress:
            return False
        if _STATE.completed and not rebuild and not allow_refresh:
            return False
        _STATE.started = True
        # Round 71 / Phase 2 (#10): set ``in_progress=True`` BEFORE
        # spawning the thread.  Pre-R71 the flag was set inside
        # ``_run_index_pass`` after the thread started, so two
        # concurrent ``request_refresh`` calls (e.g. a click-storm on
        # the admin "Re-index now" button) could both pass the
        # ``if _STATE.in_progress: return False`` gate above and
        # spawn two threads racing to write the same encrypted DB.
        # Setting the flag inside the same lock that guards the
        # spawn closes the race.
        _STATE.in_progress = True
        thread = threading.Thread(
            target=_run_index_pass,
            kwargs={"rebuild": bool(rebuild)},
            name="adoptiq-corpus-bootstrap",
            daemon=True,
        )
        _THREAD = thread
        thread.start()
    # Round 66 / Pass 5 - kick off the embedder warmup in parallel so
    # the FIRST Ask AI query does not pay the cold start. Spawned
    # outside the corpus lock; failures here never block corpus boot.
    try:
        _warm_embedder_in_background()
    except Exception as warm_err:  # noqa: BLE001
        logger.warning(
            "Round 66 / Pass 5: embedder warmup failed to start: %s",
            type(warm_err).__name__,
        )
    # Round 35 / native-corpus: also kick off the daily-refresh
    # worker.  Idempotent -- ``start_daily_refresh_worker`` no-ops
    # when the daemon is already alive.  Spawned outside the lock so
    # an unexpectedly-slow ``threading.Thread.start`` cannot deadlock
    # callers of ``get_state()``.
    try:
        start_daily_refresh_worker()
    except Exception as worker_err:  # noqa: BLE001 - never bubble
        logger.warning(
            "Round 35 / corpus_bootstrap: daily refresh worker failed "
            "to start: %s",
            type(worker_err).__name__,
        )
    return True


def request_refresh(*, rebuild: bool = False) -> bool:
    """Public entry point for the admin "Refresh now" action.  Returns
    ``True`` when a refresh thread was spawned, ``False`` otherwise.
    A refresh while one is already in progress is a no-op."""
    return start_background(rebuild=rebuild)


def reset_user_corpus() -> tuple[Optional[str], int]:
    """Round 39: public entry point for the "Reset corpus" UI button.

    Preserves the user's current four corpus artifacts as
    ``<name>.broken-<utc_iso>`` (single rolling backup) and returns
    ``(suffix, count_preserved)``.  When no corpus is present on disk
    returns ``(None, 0)``.  The caller is expected to follow up with
    ``request_refresh(rebuild=True)`` so the bootstrap path rebuilds
    the local encrypted corpus from the authorized OneDrive source.

    Closes the in-process handle (if any) before renaming so the
    sqlite plaintext temp file does not race against the rename.
    """
    global _HANDLE, _SIGNAL
    user_dir = _user_corpus_dir()
    # Close any live handle so the rename does not race against an
    # active sqlite connection (Windows would refuse the rename
    # outright; macOS would leave the temp plaintext orphaned).
    with _BOOT_LOCK:
        if _SIGNAL is not None:
            try:
                _SIGNAL.stop_requested = True
            except Exception:  # noqa: BLE001 - best effort
                pass
        handle = _HANDLE
        _HANDLE = None
        _SIGNAL = None
    if handle is not None:
        try:
            handle.close(persist=True)
        except Exception:  # noqa: BLE001 - best effort
            logger.debug(
                "Round 39 / corpus_bootstrap: handle close on reset failed",
                exc_info=True,
            )
    configure_connection(None)

    suffix = _preserve_broken_corpus(user_dir)
    if suffix is None:
        return None, 0
    # Count is whatever the four-file rotation actually moved -- we
    # re-walk the directory so the answer reflects disk truth, not a
    # buffered guess.
    preserved = 0
    try:
        for entry in user_dir.iterdir():
            if entry.name.endswith(f".broken-{suffix}"):
                preserved += 1
    except OSError:
        pass
    logger.warning(
        "Round 39 / corpus_bootstrap: event=corpus_reset_via_api ts=%s "
        "preserved_count=%d source=user_initiated",
        _utc_now_iso(), preserved,
    )
    return suffix, preserved


def stop() -> None:
    """Best-effort shutdown.  Sets the indexer's stop flag and closes
    the encrypted handle.  Idempotent; safe to call from
    ``atexit``."""
    global _HANDLE, _SIGNAL, _THREAD, _DAILY_REFRESH_THREAD
    # Round 35 / native-corpus: signal the daily-refresh worker to
    # exit before we close the handle so a tick already in flight does
    # not race against the shutdown.  ``Event.set`` is safe outside
    # the lock; the worker reads via ``Event.wait``.
    _DAILY_REFRESH_STOP.set()
    refresh_thread = _DAILY_REFRESH_THREAD
    if refresh_thread is not None and refresh_thread.is_alive():
        try:
            refresh_thread.join(timeout=2.0)
        except Exception:  # noqa: BLE001 - shutdown path
            pass
    with _BOOT_LOCK:
        if _SIGNAL is not None:
            try:
                _SIGNAL.stop_requested = True
            except Exception:  # noqa: BLE001 - shutdown path
                pass
        handle = _HANDLE
        _HANDLE = None
        _SIGNAL = None
        _DAILY_REFRESH_THREAD = None
    if handle is not None:
        try:
            handle.close(persist=True)
        except Exception:  # noqa: BLE001 - shutdown path
            logger.debug("corpus_bootstrap: handle close failed", exc_info=True)
    configure_connection(None)
    with _BOOT_LOCK:
        _STATE.in_progress = False
        _STATE.completed = False
        _STATE.encrypted_path = None
        _THREAD = None


def reset_for_tests() -> None:
    """Wipe the singleton state.  Used by the tests in
    ``tests/test_round17_*.py``; not part of the public API."""
    global _HANDLE, _SIGNAL, _THREAD, _STATE, _DAILY_REFRESH_THREAD
    # Round 35: tear down the daily-refresh worker so a test that
    # mutates _STATE in-place does not race against an active loop.
    _DAILY_REFRESH_STOP.set()
    refresh_thread = _DAILY_REFRESH_THREAD
    if refresh_thread is not None and refresh_thread.is_alive():
        try:
            refresh_thread.join(timeout=1.0)
        except Exception:  # noqa: BLE001 - test path
            pass
    with _BOOT_LOCK:
        if _HANDLE is not None:
            try:
                _HANDLE.close(persist=False)
            except Exception:  # noqa: BLE001 - test path
                pass
        _HANDLE = None
        _SIGNAL = None
        _THREAD = None
        _DAILY_REFRESH_THREAD = None
        _STATE = CorpusBootState()
    _DAILY_REFRESH_STOP.clear()
    configure_connection(None)


__all__ = [
    "CorpusBootState",
    "get_state",
    "is_enabled",
    "request_refresh",
    "reset_for_tests",
    "reset_user_corpus",
    "start_background",
    "stop",
]
