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
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

# Round 35 / native-corpus: 24h refresh cadence for the daily worker
# that pulls updates from ``Config.ADOPTIQ_CORPUS_SHARE_URL`` on top
# of the baked snapshot.  Hour-resolution ticks keep us responsive to
# user sign-in events without hammering Graph.
_DAILY_REFRESH_INTERVAL_S = 86400.0
_DAILY_REFRESH_TICK_S = 3600.0
_DAILY_REFRESH_RETRY_S = 3600.0
_DAILY_REFRESH_THREAD_NAME = "adoptiq-corpus-daily-refresh"


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
    #   * ``"baked"``  -- copied from ``<sys._MEIPASS>/baked_corpus/``
    #                     on the first launch of a freshly-installed
    #                     .app.  ``indexed_at`` reflects the build-time
    #                     bake timestamp so the panel can show
    #                     "Indexed (last bake YYYY-MM-DD)".
    #   * ``"fresh"``  -- legacy / dev path: no baked corpus shipped,
    #                     so the indexer creates an empty DB and waits
    #                     for the user to sign in / refresh.
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
    #                         Daily refresh paused; baked snapshot is
    #                         the only source of truth.
    #   * ``None``         -- check has not run yet (first boot, before
    #                         the bootstrap thread populates state).
    #
    # ``onedrive_file_count`` is the number of real files seen at the
    # last check (0 when not_synced).
    onedrive_status: Optional[str] = None
    onedrive_file_count: Optional[int] = None


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
                        top level + immediate children.  Bounded
                        scan -- we stop after seeing 1 real file
                        when only deciding ``synced`` vs
                        ``not_synced`` so a huge folder does not
                        wedge boot.
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
# Round 35 / native-corpus: baked-corpus discovery + install
# ---------------------------------------------------------------------------


# Names mirror the four artifacts ``scripts/bake_corpus.py`` writes
# (and ``adoptiq_mac.spec`` ships under ``Resources/baked_corpus/``).
# ``corpus.db.salt`` follows ``corpus_crypto._salt_path_for`` which
# derives ``<encrypted_path>.with_suffix(".salt")`` -- changing this
# tuple without updating both the bake script and the spec would
# silently break the install path.
_BAKED_CORPUS_FILES: tuple = (
    "corpus.db.enc",
    "sentinel.json",
    "corpus.db.salt",
    "corpus.sentinel.lock.json",
)


def _baked_corpus_dir() -> Optional[Path]:
    """Return the directory containing the baked corpus, or ``None``
    when the running build did not bundle one.

    Resolution order:

    1. ``ADOPTIQ_BAKED_CORPUS_DIR`` env override (test path; lets the
       test suite point at a fixture without monkey-patching
       ``sys._MEIPASS``).
    2. ``<sys._MEIPASS>/baked_corpus/`` -- the PyInstaller-mounted
       resource location used by the shipping .app.
    3. ``<repo_root>/bake/`` -- handy when running from a dev checkout
       after a local ``scripts/bake_corpus.py`` invocation.

    Returns ``None`` (rather than raising) when no candidate exists,
    so callers can fall through to the legacy fresh-bootstrap path
    cleanly.
    """
    override = os.environ.get("ADOPTIQ_BAKED_CORPUS_DIR")
    if override:
        candidate = Path(override)
        if candidate.is_dir() and (candidate / "corpus.db.enc").exists():
            return candidate

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "baked_corpus"
        if candidate.is_dir() and (candidate / "corpus.db.enc").exists():
            return candidate

    # Dev / source-tree path: only honored when a corpus.db.enc lives
    # there (we never want to silently treat an empty ``bake/`` skip
    # marker as a valid baked corpus).
    repo_bake = Path(__file__).resolve().parent / "bake"
    if repo_bake.is_dir() and (repo_bake / "corpus.db.enc").exists():
        return repo_bake

    return None


def _user_corpus_dir() -> Path:
    """Return the writable directory where the runtime corpus lives."""
    return default_db_path().parent


def _install_baked_corpus_if_present() -> Optional[str]:
    """If a baked corpus is bundled and the user has no corpus yet,
    copy the four artifacts into the writable user dir.  Returns the
    bake timestamp (ISO-8601) on success, ``None`` when no install
    happened (no bake bundled, or user already has a corpus).

    The copy is one-shot per install: if the user's
    ``corpus.db.enc`` already exists we leave it alone -- the daily
    refresh will keep it current and the user's prior delta is more
    valuable than the build-time snapshot.

    Each copy is atomic (sibling tmp + ``os.replace``) and the
    destination files are chmod 0600 so a multi-user host cannot read
    another account's encrypted DB or sentinel material.
    """
    bake_dir = _baked_corpus_dir()
    if bake_dir is None:
        return None
    user_dir = _user_corpus_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(user_dir, 0o700)
    except OSError:
        pass

    user_db = user_dir / "corpus.db.enc"
    if user_db.exists():
        # Already installed (or user has a refresh-built DB).  Leave it alone.
        return None

    indexed_at: Optional[str] = None
    for fname in _BAKED_CORPUS_FILES:
        src = bake_dir / fname
        if not src.exists():
            # The four artifacts ship together; missing any one means
            # the bake is incomplete and the bake-source is unsafe to
            # trust.  Roll back and let the legacy path mint a fresh
            # local sentinel.
            logger.warning(
                "Round 35 / corpus_bootstrap: baked corpus incomplete "
                "(missing %s); falling back to fresh-mint path",
                fname,
            )
            for cleanup in _BAKED_CORPUS_FILES:
                try:
                    (user_dir / cleanup).unlink(missing_ok=True)
                except OSError:
                    pass
            return None
        dest = user_dir / fname
        tmp = dest.with_suffix(dest.suffix + ".install-tmp")
        try:
            shutil.copyfile(src, tmp)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, dest)
        except OSError as copy_err:
            logger.warning(
                "Round 35 / corpus_bootstrap: baked corpus copy failed "
                "name=%s err=%s",
                fname, type(copy_err).__name__,
            )
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        # Capture the build-time timestamp from the bake mtime so the
        # UI can show "Last bake YYYY-MM-DD".  Use the earliest mtime
        # across the four files (they are all written within seconds
        # of one another at bake time).
        try:
            file_iso = datetime.fromtimestamp(
                src.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            if indexed_at is None or file_iso < indexed_at:
                indexed_at = file_iso
        except OSError:
            pass
    logger.info(
        "Round 35 / corpus_bootstrap: installed baked corpus into %s "
        "(bake_dir=%s indexed_at=%s)",
        user_dir, bake_dir, indexed_at,
    )
    return indexed_at


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


def _daily_refresh_loop() -> None:
    """Body of the daily-refresh daemon.

    Wakes once per ``_DAILY_REFRESH_TICK_S`` (1h) and triggers an
    incremental refresh when:

      * the feature flag is on,
      * a baked corpus install or prior refresh has populated
        ``_STATE.last_successful_refresh_ts``, AND
      * ``_should_refresh()`` says we're past the 24h window, AND
      * the OneDrive desktop client has the canonical AdoptIQ folder
        synced to disk (``_check_onedrive_sync_status() == "synced"``).

    Round 36 / onedrive-sync-auth: the legacy MSAL refresh-token gate
    has been removed -- we trust the OneDrive desktop client to keep
    the local mirror current, and just verify by stat()ing the folder.

    Refreshes piggy-back on ``request_refresh()`` -> the existing
    ``_run_index_pass()`` machinery, which already writes the
    encrypted DB atomically (sibling-tmp + ``os.replace`` inside
    ``EncryptedCorpusHandle.commit_to_disk``) so a mid-refresh crash
    leaves the prior corpus intact (Phase 4c atomic-swap semantics).
    """
    logger.info(
        "Round 36 / corpus_bootstrap: daily refresh worker started "
        "(interval=%.0fs tick=%.0fs)",
        _DAILY_REFRESH_INTERVAL_S, _DAILY_REFRESH_TICK_S,
    )
    while not _DAILY_REFRESH_STOP.is_set():
        # Sleep with .wait() so stop() can interrupt the worker
        # quickly during process shutdown.
        if _DAILY_REFRESH_STOP.wait(_DAILY_REFRESH_TICK_S):
            break
        try:
            if not is_enabled():
                continue
            with _BOOT_LOCK:
                last_ts = _STATE.last_successful_refresh_ts
                in_progress = _STATE.in_progress
            if in_progress:
                continue
            if not _should_refresh(last_refresh_ts=last_ts):
                continue
            # Round 36: probe the OneDrive sync mirror.  If the user
            # has not signed into OneDrive (or the folder was deleted
            # locally) we skip silently; the panel surfaces
            # ``onedrive_status="not_synced"`` so the user knows
            # daily refresh is paused.
            od_status, od_count, _ = _check_onedrive_sync_status()
            with _BOOT_LOCK:
                _STATE.onedrive_status = od_status
                _STATE.onedrive_file_count = od_count
            if od_status != "synced":
                logger.debug(
                    "Round 36 / corpus_bootstrap: daily refresh skipped "
                    "(onedrive_status=%s)",
                    od_status,
                )
                continue
            logger.info(
                "Round 36 / corpus_bootstrap: triggering daily refresh "
                "(last_successful=%s onedrive_files=%s)",
                last_ts, od_count,
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
            logger.warning(
                "Round 36 / corpus_bootstrap: daily refresh loop iteration "
                "failed: %s",
                type(loop_err).__name__,
            )
    logger.info("Round 36 / corpus_bootstrap: daily refresh worker exiting")


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


def _resolve_index_sources() -> list[dict[str, object]]:
    """Round 17.1 + 26 + 36: enumerate the corpus index sources for
    the current bootstrap pass.  Order matters because ``index_folder``
    rebuilds only on the first source -- subsequent sources do
    incremental upserts on top of the rows the first source wrote.

    Priority (Round 36 - MSAL/Graph removed):

    1. ``onedrive`` -- the synced OneDrive folder
       (``Config.CSONE_ONEDRIVE_FOLDER``); appended only when the
       resolved directory actually exists so we don't waste a walker
       pass logging a "missing" warning on every refresh.  Now the
       primary source -- the OneDrive desktop client handles auth /
       MFA / admin-consent and we trust the on-disk mirror.
    2. ``user_downloads`` -- ``~/Downloads`` filtered to AdoptIQ
       report names, when ``CSONE_INCLUDE_USER_DOWNLOADS`` is
       truthy.
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
                }
            )
        else:
            logger.info(
                "Round 36 / corpus_bootstrap: onedrive source skipped "
                "(no synced copy at %s)",
                onedrive_root,
            )

    # 2) Downloads.
    include_downloads = bool(getattr(Config, "CSONE_INCLUDE_USER_DOWNLOADS", False))
    downloads_dir = getattr(Config, "CSONE_USER_DOWNLOADS_DIR", None)
    if include_downloads and downloads_dir:
        downloads_path = Path(downloads_dir)
        if downloads_path.exists() and downloads_path.is_dir():
            sources.append(
                {
                    "label": "user_downloads",
                    "dir": str(downloads_path),
                    "filter": "adoptiq_named",
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

    # Round 35 / native-corpus: on first launch of a freshly-installed
    # .app, copy the baked corpus into the writable user dir so the
    # rest of this function opens an already-populated DB instead of
    # starting from empty.  Idempotent: subsequent launches see an
    # existing user_db and short-circuit.
    try:
        baked_indexed_at = _install_baked_corpus_if_present()
    except Exception as install_err:  # noqa: BLE001 - never bubble
        baked_indexed_at = None
        logger.warning(
            "Round 35 / corpus_bootstrap: baked corpus install failed: %s",
            type(install_err).__name__,
        )

    # Round 36 / onedrive-sync-auth: probe sync status before the
    # index pass so the panel can render "synced" / "not_synced"
    # immediately (the slow indexer walk no longer gates the UI's
    # ability to tell the user whether OneDrive is talking to disk).
    od_status, od_count, _ = _check_onedrive_sync_status()

    with _BOOT_LOCK:
        _STATE.in_progress = True
        _STATE.last_started_at = _utc_now_iso()
        _STATE.encrypted_path = str(encrypted_path)
        _STATE.onedrive_root = str(onedrive_root) if onedrive_root else None
        _STATE.onedrive_status = od_status
        _STATE.onedrive_file_count = od_count
        _STATE.last_error = None
        _STATE.last_error_kind = None
        _STATE.last_sources = None
        if baked_indexed_at is not None and _STATE.source is None:
            _STATE.source = "baked"
            _STATE.indexed_at = baked_indexed_at
        elif _STATE.source is None:
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

    # Round 36 / onedrive-sync-auth: ``sharepoint_root`` is no longer
    # passed to ``open_corpus_for_user`` -- the SharePoint cache dir
    # has been retired.  ``open_corpus_for_user`` falls through to
    # ``CSONE_ONEDRIVE_FOLDER`` first and then the auto-minted local
    # sentinel under ``~/Library/Application Support/AdoptIQ/knowledge/sentinel.json``,
    # so OneDrive-less installs still encrypt cleanly.
    handle: Optional[EncryptedCorpusHandle] = None
    try:
        try:
            handle = open_corpus_for_user(
                onedrive_root=onedrive_root,
                encrypted_path=encrypted_path,
                create_if_missing=True,
            )
        except CorpusCryptoError as crypto_err:
            with _BOOT_LOCK:
                _STATE.last_error = str(crypto_err)
                _STATE.last_error_kind = "crypto"
                _STATE.in_progress = False
                _STATE.last_finished_at = _utc_now_iso()
            logger.warning(
                "Round 17 / corpus_bootstrap: corpus unavailable (%s)",
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
                logger.info(
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
                if filter_kind == "adoptiq_named":
                    files = enumerate_user_report_files(src_dir, signal=signal)
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
                with _BOOT_LOCK:
                    _STATE.last_error = type(index_err).__name__
                    _STATE.last_error_kind = "indexer"
                    _STATE.in_progress = False
                    _STATE.last_finished_at = _utc_now_iso()
                logger.exception(
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
            logger.info(
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
        logger.info(
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
        logger.exception("Round 17 / corpus_bootstrap: bootstrap raised")


def start_background(*, rebuild: bool = False) -> bool:
    """Spawn the bootstrap thread if it is not already running.
    Returns ``True`` when a new thread was started, ``False`` when the
    feature flag is off, an existing pass is in progress, or another
    thread already finished successfully and ``rebuild=False``."""
    global _THREAD
    with _BOOT_LOCK:
        _STATE.enabled = is_enabled()
        if not _STATE.enabled:
            configure_connection(None)
            return False
        if _STATE.in_progress:
            return False
        if _STATE.completed and not rebuild:
            return False
        _STATE.started = True
        thread = threading.Thread(
            target=_run_index_pass,
            kwargs={"rebuild": bool(rebuild)},
            name="adoptiq-corpus-bootstrap",
            daemon=True,
        )
        _THREAD = thread
        thread.start()
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
    "start_background",
    "stop",
]
