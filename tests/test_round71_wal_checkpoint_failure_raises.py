"""Round 71 / Phase 2 (#11) -- WAL checkpoint failure surfaces loudly.

Pre-R71 ``commit_to_disk`` swallowed any ``sqlite3.DatabaseError``
raised by ``PRAGMA wal_checkpoint(TRUNCATE);`` and continued reading
the plaintext file.  The result was an encrypted artifact containing
only the 4096-byte SQLite header (because committed pages still lived
in the ``-wal`` sibling file), with the operator seeing "corpus refresh
succeeded".

Round 71 / Phase 2 (#11) re-raises the failure as ``CorpusCryptoError``
so the caller can preserve the prior good corpus instead of writing a
bad one.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import corpus_crypto


def test_round71_wal_checkpoint_failure_raises_crypto_error(tmp_path: Path) -> None:
    """When ``PRAGMA wal_checkpoint(TRUNCATE);`` raises
    ``sqlite3.DatabaseError``, ``commit_to_disk`` MUST re-raise as
    ``CorpusCryptoError`` (not silently fall through)."""
    plaintext = tmp_path / "plaintext.db"
    encrypted = tmp_path / "corpus.db.enc"
    plaintext.write_bytes(b"\x00" * 100)  # placeholder bytes

    fake_conn = MagicMock(spec=sqlite3.Connection)
    fake_conn.commit.return_value = None
    # Simulate a flaky filesystem: checkpoint raises.
    fake_conn.execute.side_effect = sqlite3.DatabaseError("disk I/O error")

    handle = corpus_crypto.EncryptedCorpusHandle(
        conn=fake_conn,
        plaintext_path=plaintext,
        encrypted_path=encrypted,
        key=corpus_crypto.CorpusKey(b"\x00" * 32),
    )

    with pytest.raises(corpus_crypto.CorpusCryptoError, match="wal_checkpoint"):
        handle.commit_to_disk()


def test_round71_wal_checkpoint_failure_does_not_write_encrypted_file(
    tmp_path: Path,
) -> None:
    """When checkpoint fails, the encrypted file MUST NOT be written.
    Otherwise the operator would see a stale or empty file presented
    as the latest corpus."""
    plaintext = tmp_path / "plaintext.db"
    encrypted = tmp_path / "corpus.db.enc"
    plaintext.write_bytes(b"\x00" * 100)

    fake_conn = MagicMock(spec=sqlite3.Connection)
    fake_conn.commit.return_value = None
    fake_conn.execute.side_effect = sqlite3.DatabaseError("checkpoint failure")

    handle = corpus_crypto.EncryptedCorpusHandle(
        conn=fake_conn,
        plaintext_path=plaintext,
        encrypted_path=encrypted,
        key=corpus_crypto.CorpusKey(b"\x00" * 32),
    )

    try:
        handle.commit_to_disk()
    except corpus_crypto.CorpusCryptoError:
        pass

    assert not encrypted.exists(), (
        "Round 71 / Phase 2 (#11): encrypted file must NOT be written "
        "when wal_checkpoint fails -- otherwise we ship a potentially "
        "empty / stale snapshot."
    )


def test_round71_wal_checkpoint_failure_source_message_carries_round_marker() -> None:
    """The source must include a Round 71 marker on the checkpoint
    error path so a future cleanup cannot quietly downgrade the
    error to a debug log without tripping the audit grep."""
    src = (Path(__file__).resolve().parent.parent / "corpus_crypto.py").read_text(encoding="utf-8")
    assert "Round 71 / Phase 2 (#11)" in src, (
        "corpus_crypto.commit_to_disk must carry a ``Round 71 / Phase 2 "
        "(#11)`` marker comment near the wal_checkpoint error path so "
        "the audit grep finds it."
    )
    assert "raise CorpusCryptoError" in src, (
        "corpus_crypto.commit_to_disk must explicitly raise "
        "CorpusCryptoError on wal_checkpoint failure (not log+continue)."
    )
