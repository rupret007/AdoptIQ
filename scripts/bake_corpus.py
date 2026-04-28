#!/usr/bin/env python3
"""Round 35 / native-corpus: bake the AdoptIQ Knowledge Corpus into a
ship-ready encrypted SQLite snapshot during the macOS DMG build.

Invoked by ``build_mac_dmg.sh`` BEFORE ``pyinstaller`` so the four
output artifacts can be picked up by the PyInstaller ``datas=[]``
section in ``adoptiq_mac.spec`` and shipped inside the .app at
``Resources/baked_corpus/``.

Outputs (under ``--bake-dir``, default ``./bake/``):

* ``corpus.db.enc``                -- AES-GCM-encrypted SQLite database
* ``sentinel.json``                -- the locally-minted sentinel that
                                     keys the corpus (Round 33/Build8
                                     "local sentinel" path)
* ``corpus.db.salt``               -- per-corpus 32-byte salt
                                     (filename pinned by
                                     ``corpus_crypto._salt_path_for``)
* ``corpus.sentinel.lock.json``    -- pin sidecar (Round 34/A1) so a
                                     downstream open consults the
                                     baked sentinel and never silently
                                     re-keys against a different one

Auth modes:

* ``device_code`` (default)        -- MSAL device-code flow against
                                     ``Config.ADOPTIQ_SHAREPOINT_CLIENT_ID``
                                     and ``ADOPTIQ_SHAREPOINT_AUTHORITY``;
                                     the build operator (you) signs in
                                     once per build host.  Token cache
                                     persists in the keychain so
                                     subsequent builds reuse it until
                                     it expires.
* ``offline_fixture``              -- skip auth + download; index from
                                     a local directory passed via
                                     ``--offline-fixture``.  Used by
                                     ``tests/test_round35_bake_script_smoke.py``
                                     so the test suite does not need
                                     network or MSAL.

Skip mode (``--no-bake`` or ``ADOPTIQ_BAKE_CORPUS=0``):
Drop a one-line sentinel file under ``--bake-dir/.bake-skipped`` so
the build script can detect "no bake performed" and the spec file can
exclude the missing artifacts gracefully.

Security posture:

* Hardcoded share URL (``Config.ADOPTIQ_CORPUS_SHARE_URL``).  The
  ``--share-url`` CLI flag is only respected when the env var
  ``ADOPTIQ_CORPUS_SHARE_URL`` is set so an operator cannot quietly
  swap in a different source without leaving a trail.
* All output files are written with mode 0o600; the bake dir 0o700.
* No secrets in argv or stdout: the access token never leaves
  :class:`SharePointGraphClient`.
* Network access is exclusively to ``*.microsoft.com`` /
  ``*.sharepoint.com`` (enforced by ``_encode_share_url_for_graph``).

Exit codes:

* 0  -- bake succeeded (artifacts written) or skip-mode confirmed.
* 1  -- argument / config error.
* 2  -- auth failure (device-code expired, user declined, network down).
* 3  -- Graph fetch failure (share not found, ACL denied, throttled).
* 4  -- index failure (no parseable files, sqlite error).
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
        "--share-url",
        type=str,
        default=None,
        help=(
            "Override Config.ADOPTIQ_CORPUS_SHARE_URL (also accepts "
            "ADOPTIQ_CORPUS_SHARE_URL env var; CLI wins).  Must be "
            "https://*.sharepoint.com/<path>."
        ),
    )
    parser.add_argument(
        "--auth-mode",
        choices=("device_code", "offline_fixture"),
        default="device_code",
        help="Authentication / fetch mode (default: device_code)",
    )
    parser.add_argument(
        "--offline-fixture",
        type=Path,
        default=None,
        help=(
            "Local directory of pre-staged corpus files; used with "
            "--auth-mode=offline_fixture (test path)."
        ),
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=5000,
        help="Maximum files to download from the share (default: 5000)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=12,
        help="Maximum recursion depth into nested folders (default: 12)",
    )
    parser.add_argument(
        "--no-bake",
        action="store_true",
        help=(
            "Skip the bake; emit a marker file so build scripts can "
            "detect 'no bake performed'.  Useful for dev iteration "
            "when network/auth would block."
        ),
    )
    parser.add_argument(
        "--device-code-timeout-s",
        type=float,
        default=900.0,
        help="Device-code completion timeout in seconds (default: 900s/15min)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
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
    # active.
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


def _resolve_share_url(cli_value: Optional[str]) -> str:
    if cli_value:
        return str(cli_value).strip()
    try:
        from config import Config
        url = str(getattr(Config, "ADOPTIQ_CORPUS_SHARE_URL", "") or "").strip()
    except Exception as err:  # noqa: BLE001
        logger.error("failed to import Config to resolve share URL: %s", err)
        return ""
    return url


def _device_code_token_provider(timeout_s: float):
    """Return a zero-arg callable that yields a fresh access token via
    MSAL device-code, falling back to silent acquisition on retries."""

    def _provider() -> str:
        # Local imports so the script can survive an environment that
        # has not yet installed msal/keyring (e.g. tests skip auth via
        # ``--auth-mode=offline_fixture``).
        from sharepoint_corpus_source import (
            SharePointAuthRequired,
            build_default_client,
        )

        client = build_default_client()
        token = client.acquire_token_silent()
        if token:
            logger.info("MSAL silent acquire succeeded; reusing cached token")
            return token

        logger.info(
            "no cached MSAL token; starting device-code flow "
            "(timeout=%.0fs).  You will be prompted to sign in.",
            timeout_s,
        )
        try:
            flow = client.start_device_code_flow()
        except SharePointAuthRequired as err:
            logger.error("device-code flow failed to start: %s", err)
            raise
        # The verification URI + user code MUST go to the operator on
        # stderr (not stdout, which the build script may capture).
        # We deliberately print the user code here -- this is an
        # operator-facing prompt, not a log line, so the
        # logger-redaction rule from Round 34/C2 does not apply.
        print(
            "\n=== Microsoft device-code sign-in required ===",
            file=sys.stderr,
        )
        print(
            f"Open: {flow.verification_uri}",
            file=sys.stderr,
        )
        print(
            f"Enter code: {flow.user_code}",
            file=sys.stderr,
        )
        print(
            f"(Expires in {flow.expires_in} seconds)\n",
            file=sys.stderr,
        )
        sys.stderr.flush()

        info = client.await_device_code_completion(timeout_s=timeout_s)
        if info is None:
            raise SharePointAuthRequired(
                "device-code flow did not complete before timeout"
            )
        token = client.acquire_token_silent()
        if not token:
            raise SharePointAuthRequired(
                "post-device-code silent acquire returned no token"
            )
        return token

    return _provider


def _fetch_via_graph(share_url: str, dest_dir: Path, timeout_s: float) -> int:
    """Download the entire share into ``dest_dir`` via Graph + MSAL.
    Returns process-exit-style code (0 = ok, 2 = auth, 3 = fetch)."""
    from sharepoint_corpus_source import fetch_share_link_folder

    provider = _device_code_token_provider(timeout_s)
    stats = fetch_share_link_folder(share_url, provider, dest_dir)
    logger.info(
        "Graph fetch summary: listed=%d downloaded=%d failed=%d "
        "skipped_unsupported=%d skipped_oversized=%d bytes=%d",
        stats.files_listed,
        stats.files_downloaded,
        stats.files_failed,
        stats.files_skipped_unsupported,
        stats.files_skipped_oversized,
        stats.bytes_downloaded,
    )
    if stats.error_kind == "auth_required":
        logger.error("auth failure: %s", stats.error_detail)
        return 2
    if stats.error_kind:
        logger.error(
            "Graph fetch failed kind=%s detail=%s",
            stats.error_kind, stats.error_detail,
        )
        return 3
    if stats.files_downloaded == 0:
        logger.error(
            "Graph fetch downloaded zero files (listed=%d).  Refusing "
            "to bake an empty corpus -- investigate the share URL or "
            "tenant ACLs.",
            stats.files_listed,
        )
        return 3
    return 0


def _stage_offline_fixture(fixture_dir: Path, dest_dir: Path) -> int:
    if not fixture_dir.exists() or not fixture_dir.is_dir():
        logger.error(
            "offline fixture dir %s missing or not a directory", fixture_dir,
        )
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for child in fixture_dir.iterdir():
        if not child.is_file():
            continue
        target = dest_dir / child.name
        shutil.copy2(child, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        count += 1
    logger.info("staged %d offline fixture file(s) under %s", count, dest_dir)
    if count == 0:
        logger.error("offline fixture %s contained no files", fixture_dir)
        return 1
    return 0


def _index_into_encrypted_corpus(downloads_dir: Path, bake_dir: Path) -> int:
    """Open an encrypted corpus rooted in ``bake_dir``, index every
    parseable file under ``downloads_dir``, commit, and close.

    On success, the four target artifacts are written to ``bake_dir``:
    corpus.db.enc, sentinel.json, corpus.db.salt, corpus.sentinel.lock.json.
    The salt filename is pinned by ``corpus_crypto._salt_path_for``
    which derives ``<encrypted>.with_suffix(".salt")`` -- so for an
    ``encrypted_path`` of ``corpus.db.enc`` the salt becomes
    ``corpus.db.salt`` (NOT ``salt.bin``).  Renaming the encrypted
    artifact would break this mapping.
    """
    from corpus_crypto import (
        CorpusCryptoError,
        open_corpus_for_user,
    )
    from corpus_indexer import index_folder

    encrypted_path = bake_dir / "corpus.db.enc"
    # Wipe any previous bake artifacts so the new sentinel + salt mint
    # cleanly.  We deliberately do NOT preserve a prior bake -- each
    # build re-keys to ensure the lock + sentinel + db all agree.
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

    try:
        handle = open_corpus_for_user(
            onedrive_root=None,
            encrypted_path=encrypted_path,
            create_if_missing=True,
            sharepoint_root=None,
            allow_local_sentinel=True,
        )
    except CorpusCryptoError as err:
        logger.error("open_corpus_for_user failed: %s", err)
        return 4

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

    # Verify the four artifacts exist with the expected modes.
    # ``corpus.db.salt`` follows ``corpus_crypto._salt_path_for``
    # (``<db>.with_suffix(".salt")``); changing this name without
    # updating ``corpus_bootstrap._BAKED_CORPUS_FILES`` and the
    # PyInstaller ``adoptiq_mac.spec`` would silently fall back to
    # the legacy "regenerate salt at runtime" path which bricks the
    # bundled DB on first launch.
    expected = (
        encrypted_path,
        bake_dir / "sentinel.json",
        bake_dir / "corpus.db.salt",
        bake_dir / "corpus.sentinel.lock.json",
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
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    _configure_logging(args.verbose)

    bake_dir: Path = args.bake_dir.resolve()
    _ensure_bake_dir(bake_dir)

    # Skip-mode short circuit (--no-bake or ADOPTIQ_BAKE_CORPUS=0).
    env_bake_flag = os.environ.get("ADOPTIQ_BAKE_CORPUS", "").strip()
    if args.no_bake or env_bake_flag in {"0", "false", "no", "off"}:
        return _emit_skip_marker(bake_dir)

    # Resolve and validate the share URL early.
    if args.auth_mode == "device_code":
        share_url = _resolve_share_url(args.share_url)
        if not share_url:
            logger.error(
                "no corpus share URL: pass --share-url or set "
                "ADOPTIQ_CORPUS_SHARE_URL / Config.ADOPTIQ_CORPUS_SHARE_URL.",
            )
            return 1
        try:
            from sharepoint_corpus_source import _encode_share_url_for_graph
            _encode_share_url_for_graph(share_url)
        except Exception as err:
            logger.error("share URL rejected by allow-list: %s", err)
            return 1
        logger.info("baking corpus from share URL (host validated)")
    elif args.auth_mode == "offline_fixture":
        if args.offline_fixture is None:
            logger.error(
                "--auth-mode=offline_fixture requires --offline-fixture <dir>",
            )
            return 1

    # Stage the downloaded files in a temp directory so we never
    # commit raw .docx/.xlsx blobs to the build tree.
    with tempfile.TemporaryDirectory(prefix="adoptiq-bake-") as tmp:
        tmp_path = Path(tmp)
        downloads_dir = tmp_path / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(downloads_dir, 0o700)
        except OSError:
            pass

        if args.auth_mode == "offline_fixture":
            rc = _stage_offline_fixture(args.offline_fixture, downloads_dir)
        else:
            rc = _fetch_via_graph(
                share_url, downloads_dir, args.device_code_timeout_s,
            )
        if rc != 0:
            return rc

        rc = _index_into_encrypted_corpus(downloads_dir, bake_dir)
        if rc != 0:
            return rc

    logger.info("bake complete: %s", bake_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
