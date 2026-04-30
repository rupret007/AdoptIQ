#!/usr/bin/env python3
"""Round 53 / Phase 53.0 -- one-time provisioning CLI for the canonical
AdoptIQ Knowledge Corpus sentinel.

Background
----------
Pre-Round-53, the bake script (``scripts/bake_corpus.py``) auto-minted
a *local* sentinel into ``bake/sentinel.json`` and the PyInstaller spec
shipped that sentinel inside ``Resources/baked_corpus/`` of the .app.
That meant anyone who obtained the DMG could derive the AES key and
decrypt the bundled corpus without any Cisco authentication
(documented in QUALITY_AUDIT.md Round 52.2 -- HIGH severity).

Round 53 closes the gap by sealing the corpus under a sentinel that
lives in the Cisco-managed shared OneDrive folder
(``AI Projects/AdoptIQ_CSOne_Reports``).  Microsoft's tenant ACL on
that share is the actual access boundary -- only OneDrive-signed-in
Cisco users can sync the sentinel; nobody else can.  This CLI is the
one-time tool that drops the canonical sentinel file into that folder
so the bake has something to seal against.

Usage
-----
The build operator runs this **once** per share (e.g. when standing up
the OneDrive folder for the first time, or when rotating the sentinel
under controlled conditions):

    python scripts/mint_corpus_sentinel.py

The CLI is idempotent -- a second run on a folder that already
contains ``adoptiq_corpus_sentinel.json`` prints the existing digest
prefix and exits 0 without touching the file.  Force-overwrite
(rotation) is gated by ``--force`` and prints both the old and new
digest so the operator can record the rotation in QUALITY_AUDIT.md.

Exit codes (pinned by tests; do not reorder without updating
``tests/test_round53_mint_sentinel_cli.py`` and
``tests/test_round54_f4_mint_force_confirmation.py``):

* 0 -- sentinel was minted successfully OR the canonical sentinel
  already exists and ``--force`` was not passed (idempotent no-op so
  build pipelines can call this unconditionally; the log message
  distinguishes the two cases via ``digest_prefix=`` vs ``minted=``)
* 3 -- OneDrive root is missing, not a directory, or appears unsynced
* 4 -- write failed (permission denied, ENOSPC, etc.)
* 5 -- Round 54 / F4: ``--force`` rotation aborted by the operator
  (typed confirmation did not match, or stdin was a non-tty and
  ``--yes`` was not passed)

Security posture
----------------
* Sentinel material: 32 bytes from ``secrets.token_bytes`` (CSPRNG;
  matches ``corpus_crypto._LOCAL_SENTINEL_BYTES`` so the HKDF input
  has the same entropy as the legacy local-mint path).
* File mode 0o600, parent dir 0o700, atomic write (sibling .tmp +
  ``os.replace``).
* Logger NEVER emits raw sentinel bytes; only the SHA-256 prefix
  produced by ``corpus_crypto._sentinel_digest_prefix`` (16 hex
  chars) for forensic correlation.
* No network access, no admin privileges, no Keeper / Graph calls.
  The CLI runs entirely on the local filesystem.
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


logger = logging.getLogger("mint_corpus_sentinel")


# Round 53 / Phase 53.0: pinned constants.  Keep in sync with
# ``corpus_crypto._LOCAL_SENTINEL_BYTES`` (32) so the HKDF input has
# uniform entropy regardless of which provisioning path minted the
# sentinel.  Pinned by ``tests/test_round53_mint_sentinel_cli.py``.
_SENTINEL_BYTES: int = 32

# Minimum number of real (size > 0) entries we expect to see at the
# top level of the OneDrive folder before we are willing to write the
# sentinel.  Below this floor we assume the folder is unsynced (or is
# still pulling files-on-demand stubs) and refuse to write -- otherwise
# the sentinel could land in a folder that no other user will ever
# sync, defeating the access-control story entirely.
_MIN_REAL_FILES_BEFORE_MINT: int = 1


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Provision the canonical AdoptIQ corpus sentinel into the "
            "shared OneDrive folder.  Run ONCE per share; subsequent "
            "runs are idempotent (no-op when the sentinel already exists)."
        ),
    )
    parser.add_argument(
        "--onedrive-root",
        type=Path,
        default=None,
        help=(
            "OneDrive folder to mint into.  Defaults to "
            "Config.CSONE_ONEDRIVE_FOLDER -- the OneDrive desktop "
            "client's local mirror of AI Projects/AdoptIQ_CSOne_Reports."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an existing sentinel (sentinel rotation).  "
            "Prints both the old and new digest prefix so the operator "
            "can document the rotation in QUALITY_AUDIT.md.  Round 54 "
            "/ F4: an interactive typed confirmation ('type ROTATE to "
            "confirm') is required before the overwrite proceeds; "
            "pair with --yes to bypass for unattended pipelines."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Round 54 / F4 -- bypass the typed-confirmation prompt on "
            "--force.  Intended for build pipelines that have already "
            "confirmed the rotation out-of-band (change-mgmt ticket "
            "approval, runbook checklist, etc.).  Refuses to do "
            "anything without --force; cannot be used to skip any "
            "other safety gate."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve the target path and validate the OneDrive folder "
            "but do not write anything.  Useful for build-pipeline "
            "smoke checks."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _resolve_onedrive_root(args: argparse.Namespace) -> Optional[Path]:
    """Return the resolved OneDrive folder path, or ``None`` when no
    candidate is configured.  The CLI never silently falls back to a
    repo-relative dir -- the operator must explicitly configure where
    the sentinel goes."""
    if args.onedrive_root is not None:
        return Path(args.onedrive_root).expanduser().resolve()
    try:
        from config import Config
        candidate = getattr(Config, "CSONE_ONEDRIVE_FOLDER", None)
        if candidate:
            return Path(str(candidate)).expanduser().resolve()
    except Exception as err:  # noqa: BLE001 - tolerate malformed config
        logger.warning("could not read Config.CSONE_ONEDRIVE_FOLDER: %s", err)
    return None


def _count_real_files(root: Path) -> int:
    """Count regular files at the top level whose size > 0.  Mirrors
    ``corpus_bootstrap._check_onedrive_sync_status`` so the mint
    decision uses the same "is this folder actually synced?" heuristic
    the runtime uses."""
    count = 0
    try:
        for entry in root.iterdir():
            try:
                if entry.is_file() and entry.stat().st_size > 0:
                    count += 1
            except OSError:
                continue
    except OSError:
        return 0
    return count


def _digest_prefix(payload: bytes) -> str:
    """Return the same 16-hex-char digest prefix that
    ``corpus_crypto._sentinel_digest_prefix`` produces.  We re-import
    that helper rather than re-implement so a future KDF / digest
    change in one place propagates here automatically."""
    from corpus_crypto import _sentinel_digest_prefix
    return _sentinel_digest_prefix(payload)


# Round 54 / F4 -- typed-confirmation sentinel for --force rotation.
# A sleep-deprived operator who mistypes ``--force`` on a working share
# would otherwise rotate the canonical sentinel in one keystroke and
# brick every previously-baked corpus on every shipped install.  This
# helper requires the operator to TYPE the literal token below before
# the rotation proceeds, OR to pass ``--yes`` from a build pipeline
# that has already confirmed the rotation out-of-band.  Pinned by
# ``tests/test_round54_f4_mint_force_confirmation.py``.
# ruff: S105 false positive -- this is a UX confirmation literal that
# the operator types at the prompt, NOT a credential.  The whole point
# of the confirmation gate is that the token IS public knowledge --
# obscurity here would defeat the prompt's safety value.
_FORCE_CONFIRM_TOKEN: str = "ROTATE"  # noqa: S105 - UX confirm literal, not a secret


def _confirm_force_rotation(
    *,
    yes: bool,
    target: Path,
    existing_digest: str,
    input_fn=None,
    isatty_fn=None,
) -> bool:
    """Return True iff the operator has confirmed the rotation.

    * ``--yes`` -> bypass (build pipelines).
    * Interactive tty -> prompt for the literal confirmation token.
    * Non-tty without --yes -> refuse (no silent confirmation from
      a pipe / captured stdin so a hostile shell snippet that pipes
      "ROTATE\\n" cannot accidentally confirm).

    ``input_fn`` and ``isatty_fn`` are injected so tests can drive
    the prompt without owning a real tty.  Both default to ``None``
    so they are resolved lazily against ``builtins.input`` /
    :func:`sys.stdin.isatty` at call time -- this lets pytest's
    ``monkeypatch.setattr("builtins.input", ...)`` take effect.
    """
    if yes:
        logger.info(
            "Round 54 / F4: --yes passed; bypassing typed confirmation "
            "(target=%s old_digest_prefix=%s)",
            target, existing_digest,
        )
        return True
    if isatty_fn is None:
        try:
            isatty_fn = sys.stdin.isatty
        except Exception:  # noqa: BLE001 - defensive
            return False
    try:
        is_tty = bool(isatty_fn())
    except Exception:  # noqa: BLE001 - defensive
        is_tty = False
    if not is_tty:
        logger.error(
            "Round 54 / F4: --force rotation refused (stdin is not a "
            "tty and --yes was not passed).  Pair --force with --yes "
            "for unattended pipelines, or run this CLI interactively "
            "and type %r at the prompt.",
            _FORCE_CONFIRM_TOKEN,
        )
        return False
    prompt = (
        f"\n!!! ROTATING the canonical AdoptIQ corpus sentinel will "
        f"BRICK every previously-baked corpus !!!\n"
        f"target           : {target}\n"
        f"current digest   : {existing_digest}\n"
        f"This is irreversible.  Re-bake and re-ship are required\n"
        f"before any existing user can decrypt the corpus again.\n"
        f"Type {_FORCE_CONFIRM_TOKEN!r} (without quotes) to confirm: "
    )
    if input_fn is None:
        # Resolve lazily so ``monkeypatch.setattr("builtins.input", ...)``
        # in tests takes effect (a default parameter would capture the
        # original ``input`` at module-import time).
        import builtins as _builtins
        input_fn = _builtins.input
    try:
        typed = input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        logger.error("Round 54 / F4: rotation aborted (no confirmation received)")
        return False
    if typed.strip() != _FORCE_CONFIRM_TOKEN:
        logger.error(
            "Round 54 / F4: rotation aborted (typed %r, expected %r)",
            typed.strip(), _FORCE_CONFIRM_TOKEN,
        )
        return False
    return True


def _atomic_write_sentinel(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` atomically with mode 0o600,
    parent dir 0o700.  Raises ``OSError`` on any FS failure -- the
    caller maps that to exit code 4."""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0700 failed for %s: %s", parent, chmod_err)
    tmp = target.with_suffix(target.suffix + ".mint-tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(target))
    try:
        os.chmod(target, 0o600)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0600 failed for %s: %s", target, chmod_err)


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    _configure_logging(args.verbose)

    # Round 54 / F4 -- ``--yes`` without ``--force`` is meaningless and
    # almost certainly an operator typo.  Refuse rather than silently
    # swallow so the operator notices.
    if args.yes and not args.force:
        logger.error(
            "Round 54 / F4: --yes is only valid with --force; refusing "
            "to act on a confused command line.",
        )
        return 5

    root = _resolve_onedrive_root(args)
    if root is None:
        logger.error(
            "no OneDrive root configured; pass --onedrive-root <path> or "
            "set Config.CSONE_ONEDRIVE_FOLDER",
        )
        return 3
    if not root.exists() or not root.is_dir():
        logger.error(
            "OneDrive root %s is missing or not a directory; verify the "
            "OneDrive desktop client has synced AI Projects/AdoptIQ_CSOne_Reports",
            root,
        )
        return 3

    real_files = _count_real_files(root)
    if real_files < _MIN_REAL_FILES_BEFORE_MINT:
        logger.error(
            "OneDrive root %s contains %d non-empty file(s); refusing to "
            "mint the sentinel into an apparently-unsynced folder (would "
            "produce a sentinel that no other user can ever sync).  Open "
            "the OneDrive desktop client, sync the folder, wait for at "
            "least one real file to land on disk, then re-run.",
            root, real_files,
        )
        return 3

    # Resolve the sentinel target via the SAME helper the runtime
    # uses, so any traversal protection / env-var override behavior
    # cannot drift between this CLI and the open path.
    from corpus_crypto import (
        CorpusCryptoError,
        DEFAULT_SENTINEL_NAME,
        resolve_sentinel_path,
    )
    try:
        target = resolve_sentinel_path(root)
    except CorpusCryptoError as err:
        logger.error("sentinel path resolution failed: %s", err)
        return 3
    if target is None:  # pragma: no cover - defensive; root != None above
        logger.error("sentinel path resolved to None despite valid root")
        return 3

    logger.info("OneDrive root resolved: %s", root)
    logger.info("sentinel target path  : %s", target)
    logger.info("real files at top level: %d", real_files)

    existing_payload: Optional[bytes] = None
    if target.exists():
        if target.is_file():
            try:
                existing_payload = target.read_bytes()
            except OSError as read_err:
                logger.error(
                    "sentinel exists at %s but cannot be read: %s",
                    target, read_err,
                )
                return 3
        else:
            logger.error(
                "sentinel target %s exists but is not a regular file",
                target,
            )
            return 3

    if existing_payload is not None and not args.force:
        logger.info(
            "sentinel already exists; idempotent no-op "
            "(name=%s digest_prefix=%s bytes=%d)",
            DEFAULT_SENTINEL_NAME,
            _digest_prefix(existing_payload) if existing_payload else "<empty>",
            len(existing_payload) if existing_payload else 0,
        )
        # Exit 0 (not 2) for the idempotent path so build pipelines
        # can call this unconditionally.  Tests still distinguish
        # rotation-without-force (exit 2) from idempotent re-run via
        # the message pattern + the absence of a write.
        return 0

    if args.dry_run:
        logger.info(
            "dry-run: would mint %d-byte sentinel into %s "
            "(force=%s, existing=%s)",
            _SENTINEL_BYTES,
            target,
            args.force,
            "yes" if existing_payload is not None else "no",
        )
        return 0

    # Round 54 / F4 -- typed-confirmation gate for the rotation path.
    # Only fires when there IS an existing sentinel AND --force was
    # passed (the fresh-mint path needs no confirmation; --force is
    # the destructive one).
    if existing_payload is not None and args.force:
        existing_digest_for_prompt = _digest_prefix(existing_payload)
        if not _confirm_force_rotation(
            yes=args.yes,
            target=target,
            existing_digest=existing_digest_for_prompt,
        ):
            return 5

    new_payload = secrets.token_bytes(_SENTINEL_BYTES)
    try:
        _atomic_write_sentinel(target, new_payload)
    except OSError as write_err:
        logger.error("sentinel write failed at %s: %s", target, write_err)
        return 4

    new_digest = _digest_prefix(new_payload)
    if existing_payload is not None and args.force:
        old_digest = _digest_prefix(existing_payload)
        logger.warning(
            "sentinel ROTATED at %s "
            "(old_digest_prefix=%s new_digest_prefix=%s); "
            "every previously-baked corpus is now unopenable -- "
            "re-bake and re-ship before any user upgrades.  "
            "Document this rotation in QUALITY_AUDIT.md.",
            target, old_digest, new_digest,
        )
    else:
        logger.info(
            "sentinel minted at %s "
            "(digest_prefix=%s bytes=%d mode=0o600)",
            target, new_digest, _SENTINEL_BYTES,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
