#!/usr/bin/env python3
"""Round 35 + Round 36 + Round 53 / native-corpus bake helper.

Round 107 / Build 76 restores this script as a production DMG input:
shipping builds pre-bake a corpus snapshot so Ask AI has data on first
launch. Runtime indexing remains available for generated reports and
operator uploads, but it is no longer required for the initial corpus.

Round 107 / Build 76 -- ship three artifacts:

* ``corpus.db.enc``                -- AES-GCM-encrypted SQLite database
* ``corpus.db.salt``               -- per-corpus 32-byte salt
                                     (filename pinned by
                                     ``corpus_crypto._salt_path_for``)
* ``sentinel.json``                -- local build sentinel required to
                                     decrypt the bundled corpus without
                                     OneDrive at first launch

Round 107 intentionally re-bundles ``sentinel.json`` with the encrypted
snapshot. That makes the shipped corpus immediately openable by the app
without OneDrive setup. ``corpus.sentinel.lock.json`` remains runtime
local and is not bundled.

Source resolution (Round 36 -- MSAL/Graph removed):

The bake reads files from a *local directory*, never the network.
Resolution order:

1. ``--source <path>`` CLI flag (highest priority; explicit operator
   override).
2. ``ADOPTIQ_BAKE_FIXTURE_DIR`` env var (set by ``build_mac_dmg.sh``).
3. ``Config.CSONE_ONEDRIVE_FOLDER`` (the OneDrive desktop client's
   sync mirror of the canonical AdoptIQ corpus folder).

The legacy ``--auth-mode`` / ``--share-url`` / ``--offline-fixture``
flags from Round 35 remain accepted as aliases for
backward-compat with shell scripts but no longer perform any MSAL /
Graph download -- ``--share-url`` is silently ignored, and
``--offline-fixture`` is treated as ``--source``.

Skip mode (``--no-bake`` or ``ADOPTIQ_BAKE_CORPUS=0``):
Drop a one-line sentinel file under ``--bake-dir/.bake-skipped`` so
the build script can detect "no bake performed" and the spec file can
exclude the missing artifacts gracefully.

Security posture:

* Source paths are validated to exist + be a directory before any
  copy.  No symlink traversal -- copies are file-by-file with
  ``shutil.copy2``.
* All output files are written with mode 0o600; the bake dir 0o700.
* No network access whatsoever.

Exit codes:

* 0  -- bake succeeded (artifacts written) or skip-mode confirmed.
* 1  -- argument / config error (no source dir, source missing).
* 3  -- reserved for legacy OneDrive-sentinel bake failures.
* 4  -- index failure (no parseable files, sqlite error).
* 5  -- decrypt round-trip self-test failed (Round 39); artifacts
        deleted so a malformed bake cannot be bundled.
* 6  -- reserved for the retired Round 53 negative self-test.
* 8  -- Round 95 reranker self-test failed; the bake host cannot load
        or score with the configured Ask AI reranker.

(Exit code 2 is reserved for the legacy auth/Graph path and no
longer emitted; ``main`` never returns that value.)
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Bootstrap path so the script runs from any cwd (build script invokes
# it via ``python3 scripts/bake_corpus.py``).  Insert repo root at
# index 0 so our project modules win over any installed copies.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


logger = logging.getLogger("bake_corpus")


def _log_phase_timing(phase: str, started_at: float) -> float:
    """Emit one stable, non-PII timing line and return elapsed seconds."""
    elapsed = max(0.0, time.perf_counter() - started_at)
    logger.info("bake timing: phase=%s seconds=%.3f", phase, elapsed)
    return elapsed


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bake the AdoptIQ Knowledge Corpus into an encrypted "
            "SQLite snapshot for shipping inside the .app bundle."
        ),
    )
    parser.add_argument(
        "--bake-dir",
        type=Path,
        default=Path("bake"),
        help="Output directory for corpus.db.enc + sentinel/salt/lock (default: ./bake/)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help=(
            "Local directory of pre-staged corpus files.  When omitted "
            "the script falls back to ADOPTIQ_BAKE_FIXTURE_DIR env var, "
            "then Config.CSONE_ONEDRIVE_FOLDER."
        ),
    )
    parser.add_argument(
        "--onedrive-sentinel-root",
        type=Path,
        default=None,
        help=(
            "Legacy/diagnostic OneDrive root hint. Build 76 seals the "
            "bundle with a local bake sentinel, so this path is no longer "
            "required for first-launch corpus readiness."
        ),
    )
    parser.add_argument(
        "--no-bake",
        action="store_true",
        help=(
            "Skip the bake; emit a marker file so build scripts can "
            "detect 'no bake performed'.  Useful for dev iteration."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    # Round 36 / back-compat: the Round 35 build script passes these
    # flags.  We accept and ignore them (or re-map ``offline-fixture``
    # to ``--source``) so existing shells keep working through the
    # transition window.  They will be removed in a later round once
    # the build pipeline has been bumped.
    parser.add_argument(
        "--auth-mode",
        choices=("device_code", "offline_fixture"),
        default=None,
        help="DEPRECATED (Round 36): ignored; bake reads local files only.",
    )
    parser.add_argument(
        "--offline-fixture",
        type=Path,
        default=None,
        help="DEPRECATED (Round 36): alias for --source.",
    )
    parser.add_argument(
        "--share-url",
        type=str,
        default=None,
        help="DEPRECATED (Round 36): ignored; no Graph download is performed.",
    )
    parser.add_argument(
        "--device-code-timeout-s",
        type=float,
        default=None,
        help="DEPRECATED (Round 36): ignored; no MSAL device-code flow runs.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=5000,
        help="DEPRECATED (Round 36): ignored; the indexer enforces its own caps.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=12,
        help="DEPRECATED (Round 36): ignored; recursion depth is fixed.",
    )
    return parser


# ---------------------------------------------------------------------------
# Main bake orchestration
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _ensure_bake_dir(bake_dir: Path) -> None:
    bake_dir.mkdir(parents=True, exist_ok=True)
    persist_on_close = True
    try:
        os.chmod(bake_dir, 0o700)
    except OSError as err:
        logger.warning("chmod 0700 on %s failed: %s", bake_dir, err)


def _emit_skip_marker(bake_dir: Path) -> int:
    _ensure_bake_dir(bake_dir)
    marker = bake_dir / ".bake-skipped"
    payload = {
        "skipped_at": datetime.now(timezone.utc).isoformat(),
        "reason": "ADOPTIQ_BAKE_CORPUS=0 or --no-bake passed",
    }
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    logger.info("bake skipped; wrote marker %s", marker)
    # Round 96: remove any prior bake artifacts so the runtime-only
    # shipping path cannot accidentally carry local corpus data into
    # packaging.  The list deliberately includes the legacy Round 33/34
    # files (sentinel.json + corpus.sentinel.lock.json) so old dev
    # artifacts are scrubbed too.
    for stale in (
        "corpus.db.enc",
        "sentinel.json",
        "corpus.db.salt",
        "corpus.sentinel.lock.json",
    ):
        try:
            (bake_dir / stale).unlink(missing_ok=True)
        except OSError as err:  # pragma: no cover - exotic FS
            logger.warning("failed to remove stale %s: %s", stale, err)
    return 0


def _resolve_source_dir(args: argparse.Namespace) -> Optional[Path]:
    """Round 36 (extended Round 83 / Build 59): resolve the local
    source directory in priority order. Returns ``None`` when no
    source is configured (caller emits a descriptive error).

    Round 83 widens the auto-detect step: when ``Config.CSONE_ONEDRIVE_FOLDER``
    points at a non-existent path (the first-existing-wins fallback
    in ``_resolve_csone_onedrive_folder`` returns the modern macOS
    candidate even when nothing is synced), the resolver walks the
    full ``_csone_onedrive_candidates()`` list and returns the first
    candidate that exists AND is a directory. This rescues the
    corpus owner's machine where the canonical R80 leaf is not
    present but the R83 owner-style path is, without forcing a
    manual ``ADOPTIQ_BAKE_FIXTURE_DIR`` env override.
    """
    if args.source:
        return Path(args.source).expanduser().resolve()
    # Back-compat: --offline-fixture from Round 35.
    if args.offline_fixture:
        return Path(args.offline_fixture).expanduser().resolve()
    env_dir = os.environ.get("ADOPTIQ_BAKE_FIXTURE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    # Round 83 / Build 59: explicitly walk the candidate list so the
    # bake script picks up an owner-style path when the canonical
    # R80 leaf isn't present. This is a defense-in-depth layer on
    # top of Config.CSONE_ONEDRIVE_FOLDER (which itself walks the
    # same list at config-import time but returns the first
    # candidate when NONE exist, so we re-walk here to surface a
    # ``None`` rather than a non-existent default).
    try:
        from config import _csone_onedrive_candidates
        for candidate in _csone_onedrive_candidates():
            try:
                resolved = Path(candidate).expanduser().resolve()
                if resolved.exists() and resolved.is_dir():
                    logger.info(
                        "Round 83 / bake: auto-detected source dir %s",
                        resolved,
                    )
                    return resolved
            except OSError:
                continue
    except Exception as err:  # noqa: BLE001 - defensive
        logger.warning(
            "could not enumerate OneDrive candidates: %s", err,
        )
    # Final fallback: trust Config.CSONE_ONEDRIVE_FOLDER even when it
    # doesn't exist on disk so the caller's error message points at a
    # known-canonical path rather than ``None``.
    try:
        from config import Config
        path = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
        if path:
            return Path(str(path)).expanduser().resolve()
    except Exception as err:  # noqa: BLE001
        logger.warning("could not read Config.CSONE_ONEDRIVE_FOLDER: %s", err)
    return None


def _stage_source_files(source_dir: Path, dest_dir: Path) -> int:
    """Stage exactly the file types the indexer can consume.

    Unsupported files cannot contribute to the corpus, so copying them only
    adds I/O. Supported inputs are staged recursively with their relative paths
    intact. Any symlink entry, or a zero-byte/unreadable supported input,
    fails closed: silently skipping one could thin or redirect a release corpus.
    """
    if not source_dir.exists() or not source_dir.is_dir():
        logger.error(
            "source dir %s missing or not a directory", source_dir,
        )
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    entries = sorted(
        source_dir.rglob("*"),
        key=lambda item: str(item.relative_to(source_dir)).casefold(),
    )
    symlink_entries = [
        str(child.relative_to(source_dir)) for child in entries if child.is_symlink()
    ]
    if symlink_entries:
        logger.error(
            "refusing partial corpus: %d symlink entry/entries found; "
            "hydrate/copy the approved snapshot locally",
            len(symlink_entries),
        )
        return 2

    count = 0
    skipped_unsupported = 0
    unsafe_supported: list[str] = []
    for child in entries:
        relative = child.relative_to(source_dir)
        if not child.is_file():
            continue
        if child.suffix.lower() not in {".csv", ".docx", ".xlsx"}:
            skipped_unsupported += 1
            continue
        try:
            if child.stat().st_size <= 0:
                unsafe_supported.append(str(relative))
                continue
        except OSError:
            unsafe_supported.append(str(relative))
            continue
        target = dest_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        count += 1
    logger.info("staged %d source file(s) under %s", count, dest_dir)
    logger.info(
        "stage exclusions: unsupported=%d unsafe_supported=%d",
        skipped_unsupported,
        len(unsafe_supported),
    )
    if unsafe_supported:
        logger.error(
            "refusing partial corpus: %d supported input(s) are zero-byte "
            "or unreadable",
            len(unsafe_supported),
        )
        return 2
    if count == 0:
        logger.error("source dir %s contained no usable supported files", source_dir)
        return 1
    return 0


def _resolve_onedrive_sentinel_root(args: argparse.Namespace) -> Optional[Path]:
    """Round 53 / Phase 53.1 (extended Round 83 / Build 59): locate
    the OneDrive folder that hosts the canonical AdoptIQ corpus
    sentinel.  Resolution priority (highest first):

    1. ``--onedrive-sentinel-root <path>`` CLI flag.
    2. ``ADOPTIQ_BAKE_SENTINEL_ROOT`` env var.
    3. Walk ``config._csone_onedrive_candidates()`` and pick the
       first candidate that exists AND is a directory (Round 83 -- so
       the corpus owner's owner-style path resolves automatically
       without a manual env override).
    4. Fall back to ``Config.CSONE_ONEDRIVE_FOLDER`` (the OneDrive
       desktop client's local mirror).

    When the resolved candidate path exists and is a directory it is
    returned.  Otherwise ``None`` is returned and the caller emits a
    descriptive fail-closed error.

    Returning ``None`` when the candidate exists but is not yet
    populated by the OneDrive desktop client would be surprising --
    the absence is detected later by ``read_sentinel_bytes`` which
    raises a clear "corpus sentinel not found" error pointing the
    operator at the right OneDrive folder.
    """
    onedrive_root: Optional[Path] = None
    explicit = getattr(args, "onedrive_sentinel_root", None)
    if explicit is not None:
        onedrive_root = Path(explicit).expanduser().resolve()
    else:
        env_root = os.environ.get("ADOPTIQ_BAKE_SENTINEL_ROOT", "").strip()
        if env_root:
            onedrive_root = Path(env_root).expanduser().resolve()
    if onedrive_root is None:
        # Round 83 / Build 59: walk the explicit candidate list FIRST
        # so we pick the owner-style path on the corpus owner's
        # machine even though it isn't ``Config.CSONE_ONEDRIVE_FOLDER``.
        try:
            from config import _csone_onedrive_candidates
            for candidate in _csone_onedrive_candidates():
                try:
                    resolved = Path(candidate).expanduser().resolve()
                    if resolved.exists() and resolved.is_dir():
                        onedrive_root = resolved
                        logger.info(
                            "Round 83 / bake: auto-detected sentinel "
                            "root %s",
                            resolved,
                        )
                        break
                except OSError:
                    continue
        except Exception as err:  # noqa: BLE001 - defensive
            logger.warning(
                "could not enumerate OneDrive candidates: %s", err,
            )
    if onedrive_root is None:
        try:
            from config import Config
            candidate = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
            if candidate:
                onedrive_root = Path(str(candidate)).expanduser().resolve()
        except Exception as err:  # noqa: BLE001 - tolerate malformed config
            logger.warning(
                "could not read Config.CSONE_ONEDRIVE_FOLDER: %s", err,
            )
    if onedrive_root is None:
        return None
    if not onedrive_root.exists() or not onedrive_root.is_dir():
        return None
    return onedrive_root


def _bake_chunk_vectors(conn) -> tuple[int, str, int]:
    """Round 66 / Pass 5 - compute one dense embedding per row in
    ``playbook_chunks`` and persist into ``chunk_vectors``.

    Returns ``(rows_written, model_id, model_dim)``. Raises on hard
    failures so the caller can downgrade to a lexical-only bake; the
    caller catches and logs.

    Notes
    -----
    * The bake host is the only machine that needs ``fastembed``
      installed; runtime installs ship the precomputed vectors in
      the encrypted snapshot, so the .app does not require fastembed
      to read the vectors back.
    * Batched 64 chunks at a time so a 10K-chunk corpus does not
      build a single 384KB-dense matrix in one allocation.
    * Idempotent: ``INSERT OR REPLACE`` so re-running the bake does
      not fail on existing rows.
    """
    # Round 108 / Corpus Smoothness: bake delegates to the shared vector
    # store helper with strict=True so release builds still fail loud if
    # dense vectors cannot be produced.
    from ask_ai_vector_store import upsert_chunk_vectors

    rows = conn.cursor().execute(
        'SELECT "id", "text" FROM "playbook_chunks" ORDER BY "id" ASC;'
    ).fetchall()
    result = upsert_chunk_vectors(conn, chunk_rows=rows, strict=True)
    return (result.rows_written, result.model_id, result.model_dim)


def _bake_reranker_self_test() -> tuple[bool, str]:
    """Round 95 - fail release bakes when the Ask AI reranker is absent."""
    try:
        from ask_ai_reranker import bake_self_test
    except Exception as err:  # noqa: BLE001
        return (False, f"ask_ai_reranker import failed: {type(err).__name__}: {err}")
    try:
        ok, message = bake_self_test()
    except Exception as err:  # noqa: BLE001
        return (False, f"reranker self-test raised: {type(err).__name__}: {err}")
    return (bool(ok), str(message or "unknown"))


def _index_into_encrypted_corpus(
    downloads_dir: Path,
    bake_dir: Path,
    onedrive_root: Path | None,
) -> int:
    """Open an encrypted corpus rooted in ``bake_dir``, index every
    parseable file under ``downloads_dir``, commit, and close.

    Round 107 / Build 76 -- the bake opens with the local sentinel
    fallback enabled and intentionally writes ``sentinel.json`` beside
    ``corpus.db.enc`` / ``corpus.db.salt``.  Those three files are the
    app-shipped bundle so first launch can decrypt the corpus without
    OneDrive.

    The salt filename is pinned by ``corpus_crypto._salt_path_for``
    which derives ``<encrypted>.with_suffix(".salt")`` -- so for an
    ``encrypted_path`` of ``corpus.db.enc`` the salt becomes
    ``corpus.db.salt`` (NOT ``salt.bin``).  Renaming the encrypted
    artifact would break this mapping.
    """
    from corpus_crypto import CorpusCryptoError, open_corpus_for_user
    from corpus_indexer import index_folder

    encrypted_path = bake_dir / "corpus.db.enc"
    # Wipe any previous bake artifacts so the new salt mints cleanly.
    # We deliberately do NOT preserve a prior bake -- each build
    # re-keys to ensure salt + db agree.  The 4-element list keeps
    # legacy Round 33/34 artifacts from leaking into the new bundle.
    #
    # Round 87 / Phase 1: also clear ``.bake-skipped`` because we are
    # about to produce REAL artifacts.  Without this, a prior dev
    # iteration that ran ``ADOPTIQ_BAKE_CORPUS=0`` would leave the
    # marker on disk and the new ``ADOPTIQ_RELEASE_GATE=1`` block in
    # ``build_mac_dmg.sh`` would trip on it after a successful bake
    # (the gate is the only consumer of ``.bake-skipped``; the
    # script never reads it on the real-bake path).
    for stale in (
        encrypted_path,
        bake_dir / "sentinel.json",
        bake_dir / "corpus.db.salt",
        bake_dir / "corpus.sentinel.lock.json",
        bake_dir / ".bake-skipped",
    ):
        try:
            stale.unlink(missing_ok=True)
        except OSError as err:
            logger.warning("failed to remove stale %s: %s", stale, err)

    persist_on_close = True
    try:
        handle = open_corpus_for_user(
            # Round 107: seal the bundled corpus with the local build
            # sentinel, independent of any OneDrive sentinel that may
            # exist on the build host.
            onedrive_root=None,
            encrypted_path=encrypted_path,
            create_if_missing=True,
            allow_local_sentinel=True,
        )
    except CorpusCryptoError as err:
        logger.error(
            "Round 107: open_corpus_for_user failed (local baked sentinel): %s",
            err,
        )
        return 3

    try:
        index_started = time.perf_counter()
        stats = index_folder(handle.conn, downloads_dir)
        _log_phase_timing("parse_and_lexical_index", index_started)
        logger.info(
            "indexer summary: seen=%d skipped=%d oversized=%d errors=%d",
            getattr(stats, "files_seen", 0),
            getattr(stats, "files_skipped", 0),
            getattr(stats, "files_oversized", 0),
            len(getattr(stats, "errors", []) or []),
        )
        if getattr(stats, "files_seen", 0) == 0:
            logger.error(
                "indexer saw zero files under %s -- refusing to bake "
                "an empty corpus.",
                downloads_dir,
            )
            handle.close(persist=False)
            return 4
        failed_sources = int(getattr(stats, "files_failed", 0) or 0)
        empty_sources = int(getattr(stats, "files_empty", 0) or 0)
        oversized_sources = int(getattr(stats, "files_oversized", 0) or 0)
        source_errors = list(getattr(stats, "errors", []) or [])
        if failed_sources or empty_sources or oversized_sources or source_errors:
            persist_on_close = False
            logger.error(
                "release corpus source integrity failed: failed=%d empty=%d "
                "oversized=%d errors=%d; refusing to bake a partial corpus",
                failed_sources,
                empty_sources,
                oversized_sources,
                len(source_errors),
            )
            return 6
        # Round 66 / Pass 5 - compute dense embeddings per chunk and
        # store them in the chunk_vectors table BEFORE
        # commit_to_disk so the WAL checkpoint sweeps the vector
        # pages along with the BM25 pages into a single
        # internally-consistent .enc snapshot.  Round 94 restored the
        # hard-fail contract for this release bake path: a bake
        # that cannot write dense vectors fails here. Runtime still
        # degrades to lexical when the user's machine cannot load the
        # embedder.
        try:
            vectors_started = time.perf_counter()
            vectors_added, model_id, model_dim = _bake_chunk_vectors(handle.conn)
            _log_phase_timing("dense_vectors", vectors_started)
            logger.info(
                "Round 66 / Pass 5: chunk_vectors written: rows=%d model=%s dim=%d",
                vectors_added, model_id, model_dim,
            )
            chunk_count = int(
                handle.conn.execute(
                    'SELECT COUNT(*) FROM "playbook_chunks"'
                ).fetchone()[0]
            )
            vector_count = int(
                handle.conn.execute(
                    'SELECT COUNT(*) FROM "chunk_vectors" '
                    'WHERE "model_id" = ? AND "model_dim" = ?',
                    (model_id, model_dim),
                ).fetchone()[0]
            )
            if chunk_count <= 0 or vectors_added != chunk_count or vector_count != chunk_count:
                raise RuntimeError(
                    "dense-vector completeness mismatch "
                    f"(chunks={chunk_count}, written={vectors_added}, stored={vector_count})"
                )
        except Exception as vec_err:  # noqa: BLE001 - fail-loud release gate
            persist_on_close = False
            logger.error(
                "Round 94: chunk-vector bake failed (%s); refusing to "
                "produce a lexical-only validation corpus.",
                vec_err,
            )
            return 7
        reranker_started = time.perf_counter()
        rerank_ok, rerank_message = _bake_reranker_self_test()
        _log_phase_timing("reranker_self_test", reranker_started)
        if not rerank_ok:
            persist_on_close = False
            logger.error(
                "Round 95: reranker bake self-test failed (%s); refusing "
                "to ship a build without rerank support.",
                rerank_message,
            )
            return 8
        logger.info("Round 95: reranker bake self-test ok: %s", rerank_message)
        commit_started = time.perf_counter()
        # ``commit_to_disk`` is the one authoritative WAL checkpoint + AES-GCM
        # seal. Closing with ``persist=True`` would seal the identical finished
        # database a second time, doubling this I/O-heavy phase without changing
        # a row or validation result.
        persist_on_close = False
        handle.commit_to_disk()
        _log_phase_timing("encrypt_and_commit", commit_started)
    finally:
        try:
            handle.close(persist=persist_on_close)
        except Exception as close_err:  # noqa: BLE001 - never bubble
            logger.warning("handle.close failed: %s", close_err)

    # The lock is runtime-local and can be re-minted after install.
    # The sentinel itself is now an intentional Build 76 shipping
    # artifact because first-launch decryption must not require
    # OneDrive.
    for sidecar in (bake_dir / "corpus.sentinel.lock.json",):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as err:  # pragma: no cover - exotic FS
            logger.warning("failed to scrub %s: %s", sidecar, err)

    # Verify the THREE ship-able artifacts exist with the expected
    # modes.  ``corpus.db.salt`` follows ``corpus_crypto._salt_path_for``
    # (``<db>.with_suffix(".salt")``); changing this name without
    # updating ``corpus_bootstrap._BAKED_CORPUS_FILES`` and the
    # PyInstaller ``adoptiq_mac.spec`` would silently fall back to
    # the legacy "regenerate salt at runtime" path which bricks the
    # bundled DB on first launch.
    expected = (
        encrypted_path,
        bake_dir / "corpus.db.salt",
        bake_dir / "sentinel.json",
    )
    missing = [p for p in expected if not p.exists()]
    if missing:
        logger.error(
            "expected bake artifact(s) missing after commit: %s",
            ", ".join(str(p) for p in missing),
        )
        return 4
    for p in expected:
        try:
            os.chmod(p, 0o600)
        except OSError as err:  # pragma: no cover - exotic FS
            logger.warning("chmod 0600 on %s failed: %s", p, err)
    logger.info("bake artifacts written: %s", ", ".join(p.name for p in expected))

    # Round 39 + Round 107 / corpus crypto self-heal -- positive
    # decrypt round-trip self-test.  The structural verify above
    # only confirms the two files exist; it does not prove the
    # .enc actually decrypts with the bundled local sentinel plus
    # the bundled salt. A bake regression that ships an
    # internally inconsistent pair would silently brick every user
    # install.
    selftest_handle = None
    selftest_started = time.perf_counter()
    try:
        selftest_handle = open_corpus_for_user(
            onedrive_root=None,
            encrypted_path=encrypted_path,
            create_if_missing=False,
            allow_local_sentinel=True,
        )
        cur = selftest_handle.conn.cursor()
        cur.execute("SELECT count(*) FROM sqlite_master")
        _ = cur.fetchone()
        # Round 66 / Pass 5 - verify the chunk_vectors table is
        # readable and (when populated) that a sample vector decodes
        # to the expected dimension.  An unreadable table is
        # tolerable (lexical fallback engages at runtime); a malformed
        # vector blob is fail-loud because it would silently degrade
        # every Ask AI query for the lifetime of the install.
        try:
            vec_row = cur.execute(
                'SELECT "model_id", "model_dim", "vector" '
                'FROM "chunk_vectors" LIMIT 1;'
            ).fetchone()
        except Exception as table_err:  # noqa: BLE001
            logger.info(
                "Round 66 / Pass 5: chunk_vectors not present in self-test "
                "(this is OK when the bake host has no fastembed): %s",
                table_err,
            )
            vec_row = None
        if vec_row is not None:
            try:
                from ask_ai_embeddings import decode_vector
                decoded = decode_vector(vec_row[2], dim=int(vec_row[1]))
            except Exception as dec_err:  # noqa: BLE001
                raise RuntimeError(
                    f"chunk_vectors blob failed to decode: {dec_err}"
                ) from dec_err
            if decoded is None or decoded.shape[0] != int(vec_row[1]):
                raise RuntimeError(
                    "chunk_vectors blob decoded to wrong shape "
                    f"(expected dim={vec_row[1]}, got "
                    f"{None if decoded is None else decoded.shape})"
                )
            logger.info(
                "Round 66 / Pass 5: chunk_vectors self-test ok "
                "(model=%s dim=%d, sample decoded to shape %s)",
                vec_row[0], vec_row[1], decoded.shape,
            )
    except Exception as selftest_err:  # noqa: BLE001 - we want fail-loud here
        logger.error(
            "Round 107 / bake positive decrypt self-test failed "
            "(bundled local sentinel): %s -- "
            "deleting bake artifacts so a malformed bake cannot be "
            "bundled into the .app",
            selftest_err,
        )
        if selftest_handle is not None:
            try:
                selftest_handle.close(persist=False)
            except Exception:  # noqa: BLE001 - cleanup path
                pass
        for p in expected:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return 5
    else:
        try:
            selftest_handle.close(persist=False)
        except Exception:  # noqa: BLE001 - cleanup path
            pass
        logger.info(
            "Round 107 / bake positive decrypt self-test ok (sqlite_master "
            "readable; bundle is internally consistent under the "
            "bundled local sentinel)"
        )
    _log_phase_timing("decrypt_round_trip", selftest_started)

    # Round 53 / Phase 53.1 -- final scrub.  The positive self-test
    # above calls ``open_corpus_for_user`` which RE-MINTS the
    # ``corpus.sentinel.lock.json`` sidecar (the lock is part of
    # the runtime contract -- ``open_corpus_for_user`` creates one
    # whenever it is missing).  Without this final scrub the bake
    # dir would carry the lock again after the self-test runs,
    # which would (a) confuse subsequent test runs that check the
    # bake-dir shape, and (b) risk leaking the lock if a future
    # spec change ever bundles the entire ``bake/`` directory
    # wholesale instead of allow-listing the two files explicitly.
    for sidecar in (bake_dir / "corpus.sentinel.lock.json",):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as err:  # pragma: no cover - exotic FS
            logger.warning("failed to scrub post-self-test %s: %s", sidecar, err)
    return 0


def main(argv: Optional[list] = None) -> int:
    total_started = time.perf_counter()
    args = _build_arg_parser().parse_args(argv)
    _configure_logging(args.verbose)

    # Round 36: warn loudly if any deprecated flag was passed so the
    # operator notices the build pipeline is still on the old shape.
    for stale_arg, value in (
        ("--auth-mode", args.auth_mode),
        ("--share-url", args.share_url),
        ("--device-code-timeout-s", args.device_code_timeout_s),
    ):
        if value is not None:
            logger.warning(
                "Round 36: %s is deprecated and ignored; bake reads local files only.",
                stale_arg,
            )

    bake_dir: Path = args.bake_dir.resolve()
    _ensure_bake_dir(bake_dir)

    # Skip-mode short circuit (--no-bake or ADOPTIQ_BAKE_CORPUS=0).
    env_bake_flag = os.environ.get("ADOPTIQ_BAKE_CORPUS", "").strip()
    if args.no_bake or env_bake_flag in {"0", "false", "no", "off"}:
        return _emit_skip_marker(bake_dir)

    source_dir = _resolve_source_dir(args)
    if source_dir is None:
        logger.error(
            "no source directory: pass --source <dir>, set "
            "ADOPTIQ_BAKE_FIXTURE_DIR, or ensure Config.CSONE_ONEDRIVE_FOLDER "
            "is configured.",
        )
        return 1
    logger.info("baking corpus from local source %s", source_dir)

    # Round 107: the source directory may still be the local OneDrive
    # mirror, but the encryption key is the build-local sentinel.  Keep
    # resolving the old sentinel root only as diagnostic context for
    # operators who still use the canonical OneDrive folder as input.
    onedrive_root = _resolve_onedrive_sentinel_root(args)
    if onedrive_root is None:
        logger.info(
            "Round 107: no OneDrive sentinel root configured; baking with "
            "local bundled sentinel only."
        )
    else:
        logger.info("Round 107: source-side OneDrive root resolved: %s", onedrive_root)

    # Stage the source files in a temp directory so we never commit
    # raw .docx/.xlsx blobs to the build tree.
    with tempfile.TemporaryDirectory(prefix="adoptiq-bake-") as tmp:
        tmp_path = Path(tmp)
        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(downloads_dir, 0o700)
        except OSError:
            pass

        stage_started = time.perf_counter()
        rc = _stage_source_files(source_dir, downloads_dir)
        _log_phase_timing("stage_inputs", stage_started)
        if rc != 0:
            _log_phase_timing("total", total_started)
            return rc

        rc = _index_into_encrypted_corpus(downloads_dir, bake_dir, onedrive_root)
        if rc != 0:
            _log_phase_timing("total", total_started)
            return rc

    logger.info("bake complete: %s", bake_dir)
    _log_phase_timing("total", total_started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
