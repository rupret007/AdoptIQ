"""Round 53 / Phase 53.0: pin the ``scripts/mint_corpus_sentinel.py``
one-time provisioning CLI.

Background
----------
Pre-Round-53, the bake auto-minted a local sentinel into the .app
bundle.  Anyone who obtained the DMG could derive the AES key and
decrypt the bundled corpus offline (see QUALITY_AUDIT.md Round 52.2
HIGH severity).

Round 53 closes that gap by sealing the corpus under a sentinel that
lives only in the Cisco-managed shared OneDrive folder
(``AI Projects/AdoptIQ_CSOne_Reports``).  The mint CLI is the
one-time tool that drops the canonical sentinel file there so the
bake has something to seal against.

These tests pin:

* Exit code 0 + a sentinel file appears when the OneDrive root is
  synced and the file does not yet exist.
* Exit code 0 + idempotent no-op when the sentinel already exists
  (build pipelines can call this unconditionally).
* Exit code 3 when the OneDrive root is missing / unsynced
  (refuses to mint into a folder that no other user can sync).
* Exit code 4 mapping path for OS-level write failures.
* The minted sentinel is 32 bytes (matches
  ``corpus_crypto._LOCAL_SENTINEL_BYTES``) and mode 0o600.
* ``--force`` rotates an existing sentinel and logs both the old
  and the new digest prefix for audit trail.
* Logger NEVER emits raw sentinel bytes -- only the
  ``_sentinel_digest_prefix`` 16-hex-char tag.
* No network access during the entire mint flow.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import stat
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import mint_corpus_sentinel as mint_module  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_synced_onedrive(root: Path) -> None:
    """Drop a single non-empty file at the top level so the mint
    CLI's sync-status heuristic counts it as 'synced'."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "synced_doc.docx").write_bytes(b"x" * 16)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_mint_writes_canonical_sentinel_into_synced_root(tmp_path):
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)

    rc = mint_module.main(["--onedrive-root", str(onedrive_root)])

    from corpus_crypto import DEFAULT_SENTINEL_NAME
    sentinel_path = onedrive_root / DEFAULT_SENTINEL_NAME
    assert rc == 0, f"happy path must exit 0, got {rc}"
    assert sentinel_path.exists(), (
        f"sentinel not created at {sentinel_path}"
    )
    payload = sentinel_path.read_bytes()
    assert len(payload) == 32, (
        f"minted sentinel must be 32 bytes (matches "
        f"corpus_crypto._LOCAL_SENTINEL_BYTES); got {len(payload)}"
    )


def test_mint_sets_0600_permissions(tmp_path):
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)

    rc = mint_module.main(["--onedrive-root", str(onedrive_root)])

    from corpus_crypto import DEFAULT_SENTINEL_NAME
    sentinel_path = onedrive_root / DEFAULT_SENTINEL_NAME
    assert rc == 0
    mode = stat.S_IMODE(sentinel_path.stat().st_mode)
    assert mode == 0o600, (
        f"sentinel mode {oct(mode)} != 0o600 -- a multi-user host "
        "would otherwise leak keying material to other accounts."
    )


# ---------------------------------------------------------------------------
# Idempotency / rotation
# ---------------------------------------------------------------------------


def test_mint_idempotent_no_op_when_sentinel_already_exists(tmp_path, caplog):
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)
    # First mint creates the file.
    rc1 = mint_module.main(["--onedrive-root", str(onedrive_root)])
    assert rc1 == 0
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    sentinel_path = onedrive_root / DEFAULT_SENTINEL_NAME
    first_bytes = sentinel_path.read_bytes()

    # Second mint is a no-op (build pipelines call this unconditionally).
    with caplog.at_level(logging.INFO, logger="mint_corpus_sentinel"):
        rc2 = mint_module.main(["--onedrive-root", str(onedrive_root)])
    assert rc2 == 0, (
        f"idempotent re-run must exit 0 so build pipelines can call "
        f"mint unconditionally; got {rc2}"
    )
    assert sentinel_path.read_bytes() == first_bytes, (
        "idempotent re-run must NOT mutate existing sentinel bytes"
    )
    assert any(
        "already exists" in rec.getMessage() for rec in caplog.records
    ), "idempotent path must log a clear 'already exists' message"


def test_mint_force_rotates_and_logs_digests(tmp_path, caplog):
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)
    rc1 = mint_module.main(["--onedrive-root", str(onedrive_root)])
    assert rc1 == 0
    from corpus_crypto import DEFAULT_SENTINEL_NAME, _sentinel_digest_prefix
    sentinel_path = onedrive_root / DEFAULT_SENTINEL_NAME
    old_payload = sentinel_path.read_bytes()
    old_digest = _sentinel_digest_prefix(old_payload)

    with caplog.at_level(logging.WARNING, logger="mint_corpus_sentinel"):
        # Round 54 / F4: ``--force`` now requires either an interactive
        # typed confirmation OR ``--yes``.  This test exercises the
        # rotation behavior, not the confirmation gate, so we pass
        # ``--yes`` to bypass.  Confirmation gate is pinned by
        # ``tests/test_round54_f4_mint_force_confirmation.py``.
        rc2 = mint_module.main([
            "--onedrive-root", str(onedrive_root), "--force", "--yes",
        ])
    assert rc2 == 0
    new_payload = sentinel_path.read_bytes()
    new_digest = _sentinel_digest_prefix(new_payload)

    assert new_payload != old_payload, (
        "--force must mint a fresh 32-byte sentinel"
    )
    rotation_lines = [
        rec.getMessage() for rec in caplog.records
        if "ROTATED" in rec.getMessage()
    ]
    assert len(rotation_lines) == 1, (
        f"--force must emit exactly one ROTATED warning; got "
        f"{len(rotation_lines)}: {rotation_lines}"
    )
    rotation_msg = rotation_lines[0]
    assert old_digest in rotation_msg, (
        "rotation log must include the OLD digest prefix for audit"
    )
    assert new_digest in rotation_msg, (
        "rotation log must include the NEW digest prefix for audit"
    )


# ---------------------------------------------------------------------------
# Fail-closed paths
# ---------------------------------------------------------------------------


def test_mint_fails_3_when_root_missing(tmp_path):
    rc = mint_module.main([
        "--onedrive-root", str(tmp_path / "no_such_dir"),
    ])
    assert rc == 3, f"missing root must exit 3, got {rc}"


def test_mint_fails_3_when_root_not_a_directory(tmp_path):
    not_a_dir = tmp_path / "i_am_a_file.txt"
    not_a_dir.write_text("hello")
    rc = mint_module.main(["--onedrive-root", str(not_a_dir)])
    assert rc == 3, f"non-dir root must exit 3, got {rc}"


def test_mint_fails_3_when_root_is_unsynced_empty(tmp_path):
    """An empty OneDrive root means the desktop client has not yet
    pulled any files.  Minting into it would produce a sentinel that
    no other user can ever sync, defeating the point.  Exit 3."""
    onedrive_root = tmp_path / "onedrive_empty"
    onedrive_root.mkdir(parents=True)

    rc = mint_module.main(["--onedrive-root", str(onedrive_root)])
    assert rc == 3, f"empty/unsynced root must exit 3, got {rc}"
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    assert not (onedrive_root / DEFAULT_SENTINEL_NAME).exists(), (
        "fail-closed mint must NOT leave a sentinel behind"
    )


def test_mint_fails_3_when_only_zero_byte_files_present(tmp_path):
    """OneDrive's Files-On-Demand placeholders are 0-byte stubs.
    The sync-status heuristic must NOT count those as 'synced'."""
    onedrive_root = tmp_path / "onedrive_stubs_only"
    onedrive_root.mkdir(parents=True)
    (onedrive_root / "stub_a.docx").write_bytes(b"")
    (onedrive_root / "stub_b.xlsx").write_bytes(b"")

    rc = mint_module.main(["--onedrive-root", str(onedrive_root)])
    assert rc == 3, (
        f"all-zero-byte (Files-On-Demand stubs) root must exit 3, "
        f"got {rc}"
    )


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_mint_dry_run_does_not_write(tmp_path, caplog):
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)

    with caplog.at_level(logging.INFO, logger="mint_corpus_sentinel"):
        rc = mint_module.main([
            "--onedrive-root", str(onedrive_root),
            "--dry-run",
        ])
    assert rc == 0
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    assert not (onedrive_root / DEFAULT_SENTINEL_NAME).exists(), (
        "--dry-run must not create the sentinel file"
    )
    assert any(
        "dry-run" in rec.getMessage().lower() for rec in caplog.records
    ), "dry-run path must log its own marker"


def test_mint_resolves_root_from_config_default(tmp_path, monkeypatch):
    """When ``--onedrive-root`` is omitted, the CLI falls back to
    ``Config.CSONE_ONEDRIVE_FOLDER``."""
    onedrive_root = tmp_path / "onedrive_from_config"
    _seed_synced_onedrive(onedrive_root)

    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )

    rc = mint_module.main([])
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    assert rc == 0, f"config fallback must succeed, got {rc}"
    assert (onedrive_root / DEFAULT_SENTINEL_NAME).exists()


def test_mint_logs_only_digest_prefix_never_raw_bytes(tmp_path, caplog):
    """Security-critical: the CLI must NEVER log raw sentinel bytes.
    Only the 16-hex-char digest prefix from
    ``corpus_crypto._sentinel_digest_prefix`` is acceptable."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)

    with caplog.at_level(logging.DEBUG, logger="mint_corpus_sentinel"):
        rc = mint_module.main([
            "--onedrive-root", str(onedrive_root),
            "--verbose",
        ])
    assert rc == 0

    from corpus_crypto import DEFAULT_SENTINEL_NAME
    sentinel_bytes = (onedrive_root / DEFAULT_SENTINEL_NAME).read_bytes()
    sentinel_hex = sentinel_bytes.hex()
    # Defense-in-depth: scan every log message for the FULL hex of
    # the sentinel.  16 hex chars is the digest-prefix safe form;
    # 64 hex chars (32 bytes) would be the raw payload.
    for rec in caplog.records:
        msg = rec.getMessage()
        assert sentinel_hex not in msg, (
            "log message contained raw sentinel bytes -- security "
            "regression.  Only the digest prefix is safe to log."
        )


def test_mint_no_network_calls_during_provisioning(tmp_path, monkeypatch):
    """Round 53 contract: the mint CLI is filesystem-only.  Block
    socket.socket so any accidental network attempt raises an error
    we can catch (the mint must STILL exit 0)."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)

    real_socket = socket.socket

    def _no_network(*args, **kwargs):
        raise AssertionError(
            "Round 53 contract violated: mint CLI attempted to open "
            "a socket; the entire flow must be filesystem-only."
        )

    monkeypatch.setattr(socket, "socket", _no_network)
    try:
        rc = mint_module.main(["--onedrive-root", str(onedrive_root)])
    finally:
        monkeypatch.setattr(socket, "socket", real_socket)
    assert rc == 0, f"mint must succeed without network, got {rc}"


def test_mint_handles_write_failure_with_exit_4(tmp_path, monkeypatch):
    """If the FS write fails (permission denied, ENOSPC, etc.) the
    CLI must exit 4 and log the OSError message."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive(onedrive_root)

    def _fail(*args, **kwargs):
        raise OSError("simulated write failure (no space left on device)")

    monkeypatch.setattr(mint_module.os, "open", _fail)
    rc = mint_module.main(["--onedrive-root", str(onedrive_root)])
    assert rc == 4, f"FS write failure must exit 4, got {rc}"
