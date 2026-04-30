#!/usr/bin/env python3
"""Round 35 + Round 36 + Round 53 / native-corpus: bake the AdoptIQ
Knowledge Corpus into a ship-ready encrypted SQLite snapshot during
the macOS DMG build.

Invoked by ``build_mac_dmg.sh`` BEFORE ``pyinstaller`` so the two
output artifacts can be picked up by the PyInstaller ``datas=[]``
section in ``adoptiq_mac.spec`` and shipped inside the .app at
``Resources/baked_corpus/``.

Round 53 / Phase 53.1 -- shrink to 2 artifacts (was 4):

* ``corpus.db.enc``                -- AES-GCM-encrypted SQLite database
* ``corpus.db.salt``               -- per-corpus 32-byte salt
                                     (filename pinned by
                                     ``corpus_crypto._salt_path_for``)

The Round 33/Build8 ``sentinel.json`` (local-mint) and the
Round 34/A1 ``corpus.sentinel.lock.json`` pin sidecar are
DELIBERATELY NOT bundled.  Pre-Round-53 the .app shipped both, which
made the encrypted corpus offline-decryptable by anyone who obtained
the DMG (sentinel + salt + .enc -> derive key -> open; documented in
QUALITY_AUDIT.md Round 52.2 -- HIGH severity).

Round 53 closes that gap by requiring the build to seal under the
canonical sentinel that lives in the Cisco-managed shared OneDrive
folder ``AI Projects/AdoptIQ_CSOne_Reports`` (provisioned once via
``scripts/mint_corpus_sentinel.py``).  The bake now passes
``allow_local_sentinel=False`` so the open fails loudly if the
OneDrive sentinel is missing.  Microsoft's tenant ACL on that share
is the actual access boundary at runtime: only Cisco-signed-in users
can sync the sentinel, so only they can derive the AES key.

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
* 3  -- OneDrive sentinel missing (Round 53 / Phase 53.1 fail-closed
        gate; provision via scripts/mint_corpus_sentinel.py).
* 4  -- index failure (no parseable files, sqlite error).
* 5  -- decrypt round-trip self-test failed (Round 39); artifacts
        deleted so a malformed bake cannot be bundled.
* 6  -- Round 53 / Phase 53.1 negative self-test failed: the
        artifacts decrypted with allow_local_sentinel=True+root=None,
        which proves the bake is offline-decryptable.  Artifacts
        deleted so a leaky bake cannot ship.

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
            "OneDrive folder hosting the canonical AdoptIQ corpus "
            "sentinel (Round 53 / Phase 53.1).  When omitted the script "
            "falls back to ADOPTIQ_BAKE_SENTINEL_ROOT env var, then "
            "Config.CSONE_ONEDRIVE_FOLDER.  The sentinel under that "
            "folder must already exist (provision via "
            "scripts/mint_corpus_sentinel.py) -- the bake refuses to "
            "auto-mint a local sentinel because doing so makes the "
            "encrypted corpus offline-decryptable."
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
    # Remove any prior baked artifacts so PyInstaller does not pick
    # up a stale corpus from a previous bake.  The build script /
    # spec MUST tolerate the absence of these files when skip is
    # active.  The list deliberately includes the legacy Round 33/34
    # files (sentinel.json + corpus.sentinel.lock.json) so a dev
    # iteration coming from a pre-Round-53 bake cannot leak the
    # sentinel into the .app even if --no-bake is used.
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
    """Round 36: resolve the local source directory in priority order.
    Returns ``None`` when no source is configured (caller emits a
    descriptive error).
    """
    if args.source:
        return Path(args.source).expanduser().resolve()
    # Back-compat: --offline-fixture from Round 35.
    if args.offline_fixture:
        return Path(args.offline_fixture).expanduser().resolve()
    env_dir = os.environ.get("ADOPTIQ_BAKE_FIXTURE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    # Fall through to Config.CSONE_ONEDRIVE_FOLDER -- the OneDrive
    # desktop client's local mirror.
    try:
        from config import Config
        path = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
        if path:
            return Path(str(path)).expanduser().resolve()
    except Exception as err:  # noqa: BLE001
        logger.warning("could not read Config.CSONE_ONEDRIVE_FOLDER: %s", err)
    return None


def _stage_source_files(source_dir: Path, dest_dir: Path) -> int:
    """Copy every regular file from ``source_dir`` (top level only) to
    ``dest_dir``, chmod 0600.  Returns process-exit-style code
    (0 = ok, 1 = no files / not a directory)."""
    if not source_dir.exists() or not source_dir.is_dir():
        logger.error(
            "source dir %s missing or not a directory", source_dir,
        )
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for child in source_dir.iterdir():
        if not child.is_file():
            continue
        target = dest_dir / child.name
        shutil.copy2(child, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        count += 1
    logger.info("staged %d source file(s) under %s", count, dest_dir)
    if count == 0:
        logger.error("source dir %s contained no regular files", source_dir)
        return 1
    return 0


def _resolve_onedrive_sentinel_root(args: argparse.Namespace) -> Optional[Path]:
    """Round 53 / Phase 53.1: locate the OneDrive folder that hosts
    the canonical AdoptIQ corpus sentinel.  Resolution priority
    (highest first):

    1. ``--onedrive-sentinel-root <path>`` CLI flag.
    2. ``ADOPTIQ_BAKE_SENTINEL_ROOT`` env var.
    3. ``Config.CSONE_ONEDRIVE_FOLDER`` (the OneDrive desktop client's
       local mirror of ``AI Projects/AdoptIQ_CSOne_Reports``).

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


def _index_into_encrypted_corpus(
    downloads_dir: Path,
    bake_dir: Path,
    onedrive_root: Path,
) -> int:
    """Open an encrypted corpus rooted in ``bake_dir``, index every
    parseable file under ``downloads_dir``, commit, and close.

    Round 53 / Phase 53.1 -- the bake now opens with
    ``onedrive_root=<onedrive_root>`` and ``allow_local_sentinel=False``.
    The sentinel under that OneDrive folder MUST exist (provisioned
    once via ``scripts/mint_corpus_sentinel.py``); a missing or
    unreadable sentinel fails the bake with exit code 3 instead of
    silently auto-minting a local sentinel.

    On success, two target artifacts are written to ``bake_dir``:
    ``corpus.db.enc`` and ``corpus.db.salt``.  The auto-generated
    ``corpus.sentinel.lock.json`` sidecar is INTENTIONALLY scrubbed
    after commit so it never reaches the .app bundle (the lock is
    re-minted at user runtime against the user's own OneDrive
    sentinel; pinning it at bake time bricks every install if the
    sentinel rotates between bake and ship).  The local
    ``sentinel.json`` is also scrubbed defensively in case a prior
    dev iteration left one behind.

    The salt filename is pinned by ``corpus_crypto._salt_path_for``
    which derives ``<encrypted>.with_suffix(".salt")`` -- so for an
    ``encrypted_path`` of ``corpus.db.enc`` the salt becomes
    ``corpus.db.salt`` (NOT ``salt.bin``).  Renaming the encrypted
    artifact would break this mapping.
    """
    from corpus_crypto import (
        CorpusCryptoError,
        DEFAULT_SENTINEL_NAME,
        open_corpus_for_user,
        resolve_sentinel_path,
    )
    from corpus_indexer import index_folder

    encrypted_path = bake_dir / "corpus.db.enc"
    # Wipe any previous bake artifacts so the new salt mints cleanly.
    # We deliberately do NOT preserve a prior bake -- each build
    # re-keys to ensure salt + db agree.  The 4-element list keeps
    # legacy Round 33/34 artifacts from leaking into the new bundle.
    for stale in (
        encrypted_path,
        bake_dir / "sentinel.json",
        bake_dir / "corpus.db.salt",
        bake_dir / "corpus.sentinel.lock.json",
    ):
        try:
            stale.unlink(missing_ok=True)
        except OSError as err:
            logger.warning("failed to remove stale %s: %s", stale, err)

    # Round 53 / Phase 53.1 -- pre-flight check that the OneDrive
    # sentinel actually exists before we kick off the indexer.  This
    # avoids spending minutes indexing only to discover the sentinel
    # is missing at commit time.
    sentinel_path = resolve_sentinel_path(onedrive_root)
    if sentinel_path is None or not sentinel_path.exists():
        logger.error(
            "Round 53: canonical OneDrive sentinel %s not found under %s; "
            "provision it once via scripts/mint_corpus_sentinel.py before "
            "baking.  The bake refuses to fall back to a local sentinel "
            "because doing so makes the encrypted corpus offline-decryptable.",
            DEFAULT_SENTINEL_NAME, onedrive_root,
        )
        return 3

    try:
        handle = open_corpus_for_user(
            onedrive_root=onedrive_root,
            encrypted_path=encrypted_path,
            create_if_missing=True,
            allow_local_sentinel=False,
        )
    except CorpusCryptoError as err:
        logger.error(
            "Round 53: open_corpus_for_user failed (allow_local_sentinel=False): %s",
            err,
        )
        return 3

    try:
        stats = index_folder(handle.conn, downloads_dir)
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
        handle.commit_to_disk()
    finally:
        try:
            handle.close()
        except Exception as close_err:  # noqa: BLE001 - never bubble
            logger.warning("handle.close failed: %s", close_err)

    # Round 53 / Phase 53.1 -- the OneDrive-keyed bake creates a
    # ``corpus.sentinel.lock.json`` sidecar inside ``bake_dir`` (via
    # ``open_corpus_for_user``'s post-open mint).  Scrub it BEFORE
    # the structural verify so the "expected" set never lists it,
    # and so a bug that bundles the bake/ dir wholesale cannot leak
    # the lock either.  ``sentinel.json`` is also nuked defensively
    # in case any code path in the future re-introduces it.
    for sidecar in (
        bake_dir / "corpus.sentinel.lock.json",
        bake_dir / "sentinel.json",
    ):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as err:  # pragma: no cover - exotic FS
            logger.warning("failed to scrub %s: %s", sidecar, err)

    # Verify the TWO ship-able artifacts exist with the expected
    # modes.  ``corpus.db.salt`` follows ``corpus_crypto._salt_path_for``
    # (``<db>.with_suffix(".salt")``); changing this name without
    # updating ``corpus_bootstrap._BAKED_CORPUS_FILES`` and the
    # PyInstaller ``adoptiq_mac.spec`` would silently fall back to
    # the legacy "regenerate salt at runtime" path which bricks the
    # bundled DB on first launch.
    expected = (
        encrypted_path,
        bake_dir / "corpus.db.salt",
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

    # Round 39 + Round 53 / corpus crypto self-heal -- positive
    # decrypt round-trip self-test.  The structural verify above
    # only confirms the two files exist; it does not prove the
    # .enc actually decrypts with the OneDrive-supplied sentinel
    # plus the bundled salt.  A bake regression that ships an
    # internally inconsistent pair would silently brick every user
    # install (the runtime self-heal cannot save them because they
    # have no working snapshot to fall back to).  Round 53 also
    # passes ``allow_local_sentinel=False`` here so the self-test
    # exercises the SAME fail-closed path the runtime uses --
    # otherwise a regression that ships a leaky bundle could pass
    # the self-test by silently falling back to the local sentinel.
    selftest_handle = None
    try:
        selftest_handle = open_corpus_for_user(
            onedrive_root=onedrive_root,
            encrypted_path=encrypted_path,
            create_if_missing=False,
            allow_local_sentinel=False,
        )
        cur = selftest_handle.conn.cursor()
        cur.execute("SELECT count(*) FROM sqlite_master")
        _ = cur.fetchone()
    except Exception as selftest_err:  # noqa: BLE001 - we want fail-loud here
        logger.error(
            "Round 53 / bake positive decrypt self-test failed "
            "(onedrive_root=%s, allow_local_sentinel=False): %s -- "
            "deleting bake artifacts so a malformed bake cannot be "
            "bundled into the .app",
            onedrive_root, selftest_err,
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
            "Round 53 / bake positive decrypt self-test ok (sqlite_master "
            "readable; bundle is internally consistent under the "
            "OneDrive sentinel)"
        )

    # Round 53 / Phase 53.1 -- NEGATIVE self-test.  The whole point
    # of the round is "the bundled artifacts must NOT be openable
    # without OneDrive auth".  We assert that contract here by
    # opening with ``onedrive_root=None`` and
    # ``allow_local_sentinel=False`` -- which is exactly the
    # scenario a malicious party gets when they extract the .app
    # bundle on a non-Cisco machine.  This MUST raise
    # ``CorpusCryptoError`` ("CSOne corpus folder is not configured"
    # or similar).  An open that *succeeds* here is a HIGH-severity
    # regression and we delete the artifacts + exit 6 so the build
    # cannot proceed.
    leak_handle = None
    try:
        leak_handle = open_corpus_for_user(
            onedrive_root=None,
            encrypted_path=encrypted_path,
            create_if_missing=False,
            allow_local_sentinel=False,
        )
    except CorpusCryptoError:
        logger.info(
            "Round 53 / bake negative self-test ok (open without "
            "OneDrive root + allow_local_sentinel=False fails closed; "
            "the bundle is NOT offline-decryptable)"
        )
    except Exception as leak_err:  # noqa: BLE001 - any other exception is also "fail-closed"
        logger.info(
            "Round 53 / bake negative self-test ok (open without "
            "OneDrive root raised %s; bundle still not offline-decryptable)",
            type(leak_err).__name__,
        )
    else:
        try:
            leak_handle.close(persist=False)
        except Exception:  # noqa: BLE001 - cleanup path
            pass
        logger.error(
            "Round 53 / bake NEGATIVE self-test FAILED: corpus opened "
            "with onedrive_root=None and allow_local_sentinel=False.  "
            "This means the bundle is offline-decryptable -- the "
            "Round 53 hardening is not in effect.  Deleting bake "
            "artifacts and exiting non-zero so the build cannot ship.",
        )
        for p in expected:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return 6

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
    for sidecar in (
        bake_dir / "corpus.sentinel.lock.json",
        bake_dir / "sentinel.json",
    ):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as err:  # pragma: no cover - exotic FS
            logger.warning("failed to scrub post-self-test %s: %s", sidecar, err)
    return 0


def main(argv: Optional[list] = None) -> int:
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

    # Round 53 / Phase 53.1 -- resolve the OneDrive folder that hosts
    # the canonical sentinel BEFORE staging, so we fail fast when the
    # sentinel is missing rather than after a multi-minute index.
    onedrive_root = _resolve_onedrive_sentinel_root(args)
    if onedrive_root is None:
        logger.error(
            "Round 53: no OneDrive sentinel root configured (or the "
            "configured path does not exist).  Pass "
            "--onedrive-sentinel-root <path>, set "
            "ADOPTIQ_BAKE_SENTINEL_ROOT, or ensure "
            "Config.CSONE_ONEDRIVE_FOLDER points at a synced OneDrive "
            "folder containing adoptiq_corpus_sentinel.json.  Provision "
            "the sentinel once via scripts/mint_corpus_sentinel.py.",
        )
        return 3
    logger.info("Round 53: OneDrive sentinel root resolved: %s", onedrive_root)

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

        rc = _stage_source_files(source_dir, downloads_dir)
        if rc != 0:
            return rc

        rc = _index_into_encrypted_corpus(downloads_dir, bake_dir, onedrive_root)
        if rc != 0:
            return rc

    logger.info("bake complete: %s", bake_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
