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
import threading
from dataclasses import dataclass, field
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


_STATE: CorpusBootState = CorpusBootState()
_HANDLE: Optional[EncryptedCorpusHandle] = None
_SIGNAL: Optional[IndexSignal] = None
_THREAD: Optional[threading.Thread] = None


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
        )


def is_enabled() -> bool:
    """Return True when the feature flag is on."""
    return bool(getattr(Config, "CORPUS_KNOWLEDGE_ENABLED", False))


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _index_stats_to_dict(stats: IndexStats) -> dict[str, object]:
    """Marshal :class:`IndexStats` into a dict the admin tile / JSON
    endpoint can render.  Errors list is truncated for privacy."""
    return {
        "files_seen": int(stats.files_seen),
        "files_parsed": int(stats.files_parsed),
        "files_skipped": int(stats.files_skipped),
        "files_failed": int(stats.files_failed),
        "files_oversized": int(stats.files_oversized),
        "chunks_added": int(stats.chunks_added),
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
        # Only the count of errors -- error strings can leak filenames.
        "errors": int(len(stats.errors)),
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
    """Round 17.1 + 17.2 + 26: enumerate the corpus index sources for
    the current bootstrap pass.  Order matters because ``index_folder``
    rebuilds only on the first source -- subsequent sources do
    incremental upserts on top of the rows the first source wrote.

    Priority:

    1. ``sharepoint_csone`` -- the local cache populated by
       :func:`sharepoint_corpus_source.refresh_local_cache` when
       ``Config.ADOPTIQ_SHAREPOINT_ENABLED`` is on.  Skipped silently
       (``dir=None``) when SharePoint is disabled, no folder URL is
       configured, or the user has not yet completed the device-code
       sign-in (``RefreshStats.error_kind == 'auth_required'``).
    2. ``onedrive`` -- the synced OneDrive folder
       (``Config.CSONE_ONEDRIVE_FOLDER``); appended only when the
       resolved directory actually exists so we don't waste a walker
       pass logging a "missing" warning on every refresh.
    3. ``user_downloads`` -- ``~/Downloads`` filtered to AdoptIQ
       report names, when ``CSONE_INCLUDE_USER_DOWNLOADS`` is
       truthy.
    4. ``intel_uploads`` (Round 26) -- per-user drop folder
       populated by ``/api/intel/upload``.  Always walked when the
       directory exists so admin-pre-seeded files are picked up
       even when the user-facing upload route itself is disabled.

    Each entry is a self-describing dict so the admin tile can render
    labels without having to hard-code the order.
    """
    sources: list[dict[str, object]] = []

    # 1) SharePoint local cache (primary source; refreshed before the
    # walker runs in :func:`_run_index_pass`).
    sharepoint_enabled = bool(
        getattr(Config, "ADOPTIQ_SHAREPOINT_ENABLED", False)
    )
    sharepoint_url = (
        getattr(Config, "ADOPTIQ_SHAREPOINT_FOLDER_URL", None) or ""
    ).strip()
    sharepoint_cache = (
        getattr(Config, "ADOPTIQ_SHAREPOINT_CACHE_DIR", None) or ""
    ).strip()
    if sharepoint_enabled and sharepoint_url and sharepoint_cache:
        cache_path = Path(sharepoint_cache)
        # We always include the source -- even if the cache directory
        # does not yet exist -- because :func:`_run_index_pass` will
        # create it when the SharePoint refresh succeeds.  The walker
        # handles a missing dir gracefully (zero files seen).
        sources.append(
            {
                "label": "sharepoint_csone",
                "dir": str(cache_path),
                "filter": "all_supported",
            }
        )

    # 2) OneDrive sync.
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
                "Round 17.2 / corpus_bootstrap: onedrive source skipped "
                "(no synced copy at %s)",
                onedrive_root,
            )

    # 3) Downloads.
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

    # 4) Round 26: per-user uploaded CSOne reports.  We walk this
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


def _refresh_sharepoint_cache_for_bootstrap() -> Optional[dict[str, object]]:
    """Round 17.2: pull the SharePoint share into its local cache so
    the indexer can walk it.  Returns a dict the admin tile can
    render (always serializable), or ``None`` when SharePoint is
    disabled / not configured.  Auth failures are surfaced as a
    structured field, never raised.
    """
    if not bool(getattr(Config, "ADOPTIQ_SHAREPOINT_ENABLED", False)):
        return None
    folder_url = (
        getattr(Config, "ADOPTIQ_SHAREPOINT_FOLDER_URL", None) or ""
    ).strip()
    cache_dir = (
        getattr(Config, "ADOPTIQ_SHAREPOINT_CACHE_DIR", None) or ""
    ).strip()
    if not folder_url or not cache_dir:
        return None

    try:
        import sharepoint_corpus_source as _sp
    except Exception as imp_err:  # noqa: BLE001 - optional dep / install issue
        logger.warning(
            "Round 17.2 / corpus_bootstrap: sharepoint module import failed (%s)",
            type(imp_err).__name__,
        )
        return {
            "enabled": True,
            "configured": True,
            "folder_url": folder_url,
            "cache_dir": cache_dir,
            "signed_in": False,
            "error_kind": "import_failed",
            "error_detail": type(imp_err).__name__,
            "stats": None,
        }

    try:
        client = _sp.build_default_client()
    except Exception as build_err:  # noqa: BLE001 - never bubble
        logger.warning(
            "Round 17.2 / corpus_bootstrap: sharepoint client build failed (%s)",
            type(build_err).__name__,
        )
        return {
            "enabled": True,
            "configured": True,
            "folder_url": folder_url,
            "cache_dir": cache_dir,
            "signed_in": False,
            "error_kind": "client_init_failed",
            "error_detail": type(build_err).__name__,
            "stats": None,
        }

    max_bytes = int(
        getattr(client, "max_file_bytes", _sp.DEFAULT_MAX_FILE_BYTES)
    )
    cache_path = Path(cache_dir)
    logger.info(
        "Round 17.2 / corpus_bootstrap: refreshing SharePoint cache_dir=%s",
        cache_path,
    )
    stats = _sp.refresh_local_cache(
        share_url=folder_url,
        cache_dir=cache_path,
        client=client,
        max_file_bytes=max_bytes,
    )
    token_info = client.get_token_info()
    return {
        "enabled": True,
        "configured": True,
        "folder_url": folder_url,
        "cache_dir": str(cache_path),
        "signed_in": bool(token_info.upn),
        "upn": token_info.upn,
        "name": token_info.name,
        "expires_at": token_info.expires_at,
        "expires_in_s": token_info.expires_in_s,
        "error_kind": stats.error_kind,
        "error_detail": stats.error_detail,
        "stats": stats.to_dict(),
    }


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

    with _BOOT_LOCK:
        _STATE.in_progress = True
        _STATE.last_started_at = _utc_now_iso()
        _STATE.encrypted_path = str(encrypted_path)
        _STATE.onedrive_root = str(onedrive_root) if onedrive_root else None
        _STATE.last_error = None
        _STATE.last_error_kind = None
        _STATE.last_sources = None

    # Round 17.2: refresh the SharePoint cache before resolving the
    # source list, so the cache directory is populated by the time the
    # indexer walks it.  Auth failures are non-fatal -- the bootstrap
    # falls through to OneDrive / Downloads.
    sharepoint_state = _refresh_sharepoint_cache_for_bootstrap()
    with _BOOT_LOCK:
        _STATE.sharepoint = sharepoint_state

    sources = _resolve_index_sources()

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

        with _BOOT_LOCK:
            _STATE.in_progress = False
            _STATE.completed = True
            _STATE.last_finished_at = _utc_now_iso()
            _STATE.last_stats = _index_stats_to_dict(aggregate)
            _STATE.last_sources = per_source if per_source else None
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
    global _HANDLE, _SIGNAL, _THREAD
    with _BOOT_LOCK:
        if _SIGNAL is not None:
            try:
                _SIGNAL.stop_requested = True
            except Exception:  # noqa: BLE001 - shutdown path
                pass
        handle = _HANDLE
        _HANDLE = None
        _SIGNAL = None
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
    global _HANDLE, _SIGNAL, _THREAD, _STATE
    with _BOOT_LOCK:
        if _HANDLE is not None:
            try:
                _HANDLE.close(persist=False)
            except Exception:  # noqa: BLE001 - test path
                pass
        _HANDLE = None
        _SIGNAL = None
        _THREAD = None
        _STATE = CorpusBootState()
    configure_connection(None)


def begin_sharepoint_signin() -> dict[str, object]:
    """Round 17.2: kick off a Microsoft Graph device-code sign-in for
    the SharePoint corpus source.  Returns a dict with the
    user-displayable code/uri so the admin tile can render the
    prompt.  Spawns a worker thread that completes the flow and (on
    success) triggers a corpus refresh.  Never raises -- network /
    config failures are surfaced via the ``error`` key.
    """
    if not bool(getattr(Config, "ADOPTIQ_SHAREPOINT_ENABLED", False)):
        return {"ok": False, "error": "SharePoint feature disabled"}
    try:
        import sharepoint_corpus_source as _sp
    except Exception as imp_err:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"sharepoint module import failed: {type(imp_err).__name__}",
        }
    try:
        client = _sp.build_default_client()
    except Exception as build_err:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"client init failed: {type(build_err).__name__}",
        }
    try:
        flow = client.start_device_code_flow()
    except _sp.SharePointAuthRequired as auth_err:
        return {"ok": False, "error": f"device flow init failed: {auth_err}"}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "error": type(err).__name__}

    def _completion_worker() -> None:
        try:
            info = client.await_device_code_completion()
        except Exception as werr:  # noqa: BLE001 - worker must never raise
            logger.warning(
                "Round 17.2 / sharepoint signin worker raised: %s",
                type(werr).__name__,
            )
            return
        if info is None:
            logger.info("Round 17.2 / sharepoint signin worker: flow not completed")
            return
        # Trigger an incremental refresh now that the user is signed in.
        try:
            request_refresh(rebuild=False)
        except Exception as rerr:  # noqa: BLE001
            logger.warning(
                "Round 17.2 / sharepoint post-signin refresh failed: %s",
                type(rerr).__name__,
            )

    thread = threading.Thread(
        target=_completion_worker,
        name="adoptiq-sharepoint-signin",
        daemon=True,
    )
    thread.start()

    return {
        "ok": True,
        "user_code": flow.user_code,
        "verification_uri": flow.verification_uri,
        "message": flow.message,
        "expires_in": int(flow.expires_in or 0),
        "interval": int(flow.interval or 5),
    }


def request_sharepoint_refresh() -> bool:
    """Round 17.2: convenience wrapper that triggers a corpus refresh
    after the SharePoint cache is updated.  Returns True when a
    refresh thread was spawned."""
    return start_background(rebuild=False)


__all__ = [
    "CorpusBootState",
    "begin_sharepoint_signin",
    "get_state",
    "is_enabled",
    "request_refresh",
    "request_sharepoint_refresh",
    "reset_for_tests",
    "start_background",
    "stop",
]
