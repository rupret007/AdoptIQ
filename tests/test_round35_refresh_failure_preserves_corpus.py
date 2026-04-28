"""Round 35 / native-corpus: refresh failure must preserve prior corpus.

Phase 4c contract: the daily refresh worker writes through the
existing ``EncryptedCorpusHandle.commit_to_disk`` path, which seals
the encrypted bytes into a sibling ``corpus.db.enc.tmp`` file and
then ``os.replace``-swaps it onto the real path.  If the seal+write
sequence fails *before* the ``os.replace`` call, the prior encrypted
DB MUST be left bit-identical.  Mid-flight indexer failures must
likewise NEVER blow away the existing corpus.

These tests exercise the seam by:

1. Sealing a real prior corpus, capturing its bytes.
2. Failing in three places:
   * inside ``commit_to_disk`` BEFORE ``os.replace`` (write failure)
   * inside ``commit_to_disk`` after the ``.tmp`` is written but
     ``os.replace`` raises (swap failure)
   * inside the indexer itself (refresh never even reaches commit)
3. Asserting the on-disk ``corpus.db.enc`` bytes are unchanged
   after each failure.

If any of these regress, a network blip mid-refresh would corrupt
every install's corpus -- the worst-case data-loss outcome.
"""

from __future__ import annotations

import builtins
import os
from pathlib import Path
from unittest import mock

import pytest

from corpus_crypto import (
    CorpusCryptoError,
    EncryptedCorpusHandle,
    open_corpus_for_user,
)


def _open_fresh_corpus(tmp_path: Path) -> EncryptedCorpusHandle:
    encrypted_path = tmp_path / "knowledge" / "corpus.db.enc"
    encrypted_path.parent.mkdir(parents=True, exist_ok=True)
    return open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=encrypted_path,
        create_if_missing=True,
        sharepoint_root=None,
        allow_local_sentinel=True,
    )


def _seed_committed_corpus(tmp_path: Path) -> bytes:
    """Open a fresh corpus, write some data, commit.  Returns the
    on-disk bytes so the test can assert byte-for-byte preservation
    after a subsequent failed refresh."""
    handle = _open_fresh_corpus(tmp_path)
    try:
        handle.conn.execute(
            "CREATE TABLE IF NOT EXISTS r35_test ("
            "id INTEGER PRIMARY KEY, label TEXT NOT NULL)"
        )
        handle.conn.execute(
            "INSERT INTO r35_test (label) VALUES (?)",
            ("baked-snapshot",),
        )
        handle.commit_to_disk()
    finally:
        handle.close(persist=False)
    return (tmp_path / "knowledge" / "corpus.db.enc").read_bytes()


def test_atomic_swap_uses_sibling_tmp(tmp_path):
    """Pin the contract: ``commit_to_disk`` writes to a sibling
    ``.tmp`` and then renames.  If the suffix changes, the rest of
    the rollback semantics this test pinned shift too."""
    handle = _open_fresh_corpus(tmp_path)
    try:
        handle.conn.execute(
            "CREATE TABLE IF NOT EXISTS r35_test ("
            "id INTEGER PRIMARY KEY)"
        )
        handle.conn.execute(
            "INSERT INTO r35_test DEFAULT VALUES"
        )
        # Patch os.replace so we can intercept the rename and inspect
        # the staging path.  We allow the rename through.
        observed: list[str] = []
        real_replace = os.replace

        def _spy_replace(src, dst):
            observed.append(str(src))
            return real_replace(src, dst)

        with mock.patch("corpus_crypto.os.replace", _spy_replace):
            handle.commit_to_disk()

        assert observed, "commit_to_disk did not call os.replace"
        # The src path must be a sibling tmp suffix of the encrypted
        # path -- this is the lynchpin of the atomic-swap contract.
        assert observed[0].endswith(".enc.tmp"), (
            f"expected .enc.tmp staging, got {observed[0]!r}"
        )
    finally:
        handle.close(persist=False)


def test_failed_commit_preserves_prior_corpus_bytes(tmp_path):
    """Simulate a failure inside ``commit_to_disk`` AFTER the .tmp
    is opened but BEFORE the ``os.replace`` swap.  The on-disk
    ``corpus.db.enc`` must be byte-identical afterwards."""
    prior_bytes = _seed_committed_corpus(tmp_path)
    encrypted_path = tmp_path / "knowledge" / "corpus.db.enc"

    # Re-open and queue some new state.
    handle = _open_fresh_corpus(tmp_path)
    try:
        handle.conn.execute(
            "INSERT INTO r35_test (label) VALUES (?)",
            ("would-be-new-row",),
        )

        # Fail the os.replace -> emulates a "tmp written but rename
        # crashed" race.  The prior file MUST be untouched because we
        # never replaced it.
        with mock.patch(
            "corpus_crypto.os.replace",
            side_effect=OSError("simulated rename failure"),
        ):
            with pytest.raises(OSError, match="simulated rename failure"):
                handle.commit_to_disk()
    finally:
        try:
            handle.close(persist=False)
        except Exception:
            pass

    after_bytes = encrypted_path.read_bytes()
    assert after_bytes == prior_bytes, (
        "Phase 4c violation: a failed os.replace during commit "
        "rewrote the encrypted DB; prior corpus would be lost."
    )


def test_failed_seal_preserves_prior_corpus_bytes(tmp_path):
    """Simulate a failure inside ``commit_to_disk`` BEFORE the .tmp
    is even opened (e.g. AESGCM seal raises).  The on-disk
    ``corpus.db.enc`` must be byte-identical afterwards."""
    prior_bytes = _seed_committed_corpus(tmp_path)
    encrypted_path = tmp_path / "knowledge" / "corpus.db.enc"

    handle = _open_fresh_corpus(tmp_path)
    try:
        handle.conn.execute(
            "INSERT INTO r35_test (label) VALUES (?)",
            ("would-be-new-row",),
        )
        # Patch the encrypt step itself to simulate an AES-GCM
        # failure (e.g. OSError reading plaintext, MemoryError on
        # seal, etc.).
        with mock.patch(
            "corpus_crypto.encrypt_bytes",
            side_effect=RuntimeError("simulated seal failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated seal failure"):
                handle.commit_to_disk()
    finally:
        try:
            handle.close(persist=False)
        except Exception:
            pass

    after_bytes = encrypted_path.read_bytes()
    assert after_bytes == prior_bytes


def test_indexer_crash_before_commit_preserves_prior_corpus_bytes(tmp_path):
    """The most common failure mode: the daily-refresh indexer
    raises mid-pass before ``commit_to_disk`` is even called.  The
    encrypted DB on disk must be unchanged."""
    prior_bytes = _seed_committed_corpus(tmp_path)
    encrypted_path = tmp_path / "knowledge" / "corpus.db.enc"

    handle = _open_fresh_corpus(tmp_path)
    try:
        # Simulate mid-pass mutation that then crashes -- the
        # in-memory plaintext is dirtied but never committed.
        handle.conn.execute(
            "INSERT INTO r35_test (label) VALUES (?)",
            ("partial-refresh-row",),
        )
        # The "indexer crash" is just abandoning the handle without
        # commit_to_disk.  We close with persist=False to mirror the
        # daily-refresh worker's exception path
        # (``corpus_bootstrap._daily_refresh_loop`` swallows the
        # error and lets the bootstrap state record it).
    finally:
        handle.close(persist=False)

    after_bytes = encrypted_path.read_bytes()
    assert after_bytes == prior_bytes, (
        "Indexer crash-before-commit altered the on-disk corpus; "
        "Phase 4c atomic-swap semantics violated."
    )


def test_failed_refresh_does_not_leave_orphaned_tmp_visible(tmp_path):
    """After a failed os.replace, a sibling ``.tmp`` file MAY exist
    (the kernel does not roll it back), but it must NOT shadow the
    encrypted_path.  Future refreshes overwrite the tmp by re-opening
    O_CREAT|O_TRUNC, so this is not a leak we need to clean up
    here -- this test just documents the expected post-state so a
    future engineer reading it understands the design."""
    _seed_committed_corpus(tmp_path)
    encrypted_path = tmp_path / "knowledge" / "corpus.db.enc"
    tmp_sibling = encrypted_path.with_suffix(encrypted_path.suffix + ".tmp")

    handle = _open_fresh_corpus(tmp_path)
    try:
        handle.conn.execute(
            "INSERT INTO r35_test (label) VALUES (?)",
            ("attempt",),
        )
        with mock.patch(
            "corpus_crypto.os.replace",
            side_effect=OSError("simulated"),
        ):
            with pytest.raises(OSError):
                handle.commit_to_disk()
    finally:
        try:
            handle.close(persist=False)
        except Exception:
            pass

    # The encrypted_path itself remains intact + readable.
    assert encrypted_path.exists()
    assert encrypted_path.stat().st_size > 0
    # The tmp sibling MAY exist but does not shadow the real file.
    if tmp_sibling.exists():
        # The next successful commit will O_TRUNC it; nothing to assert.
        pass
