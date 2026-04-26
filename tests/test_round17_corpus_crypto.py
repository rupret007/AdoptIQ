"""Round 17 / Phase F.1 -- corpus_crypto contracts.

Pins the at-rest crypto pipeline:

- AES-256-GCM round-trip (encrypt then decrypt yields the original
  bytes; tampered ciphertext raises ``CorpusCryptoError``).
- Wrong / different keys cannot decrypt -- authentication tag must
  reject the open.
- Each call to ``encrypt_bytes`` uses a fresh nonce (we observe two
  ciphertexts of identical plaintext differ).
- HKDF KDF is deterministic for the same ``(sentinel, salt)``.
- Sentinel handling: missing / empty / oversized / path-traversal
  filenames raise ``CorpusCryptoError``.
- Salt file is created with mode ``0600``.
- Encrypted corpus blob is *not* the SQLite plaintext (no plain
  ``"SQLite format 3"`` header on disk).
- ``EncryptedCorpusHandle.close()`` removes the plaintext temp
  file.
- ``open_corpus_for_user`` raises ``CorpusCryptoError`` when the
  OneDrive folder is not configured.
"""

from __future__ import annotations

import os
import secrets
import stat
import sqlite3
from pathlib import Path

import pytest

import corpus_crypto
from corpus_crypto import (
    CorpusCryptoError,
    CorpusKey,
    DEFAULT_SENTINEL_NAME,
    EncryptedCorpusHandle,
    decrypt_bytes,
    derive_key,
    encrypt_bytes,
    get_or_create_salt,
    open_corpus_for_user,
    open_encrypted_corpus,
    read_sentinel_bytes,
    resolve_sentinel_path,
)


_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "round17"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sentinel_bytes() -> bytes:
    return (_FIXTURES_ROOT / "sentinel.json").read_bytes()


def _make_key() -> CorpusKey:
    return derive_key(_sentinel_bytes(), salt=b"\x01" * 32)


# ---------------------------------------------------------------------------
# CorpusKey contract
# ---------------------------------------------------------------------------


def test_corpus_key_rejects_short_bytes():
    with pytest.raises(CorpusCryptoError):
        CorpusKey(key=b"\x00" * 8)


def test_corpus_key_repr_redacts_raw_key():
    k = _make_key()
    rep = repr(k)
    # Should not embed the raw 32-byte key as hex.
    assert k.key.hex() not in rep
    assert "sha256_prefix" in rep


def test_corpus_key_equality_uses_constant_time_compare():
    a = _make_key()
    b = _make_key()
    # Same sentinel + same salt -> same key bytes -> equal.
    assert a == b
    # Different keys are not equal.
    other = derive_key(_sentinel_bytes(), salt=b"\x02" * 32)
    assert a != other


# ---------------------------------------------------------------------------
# KDF contracts
# ---------------------------------------------------------------------------


def test_derive_key_is_deterministic():
    salt = b"\x33" * 32
    sentinel = _sentinel_bytes()
    a = derive_key(sentinel, salt)
    b = derive_key(sentinel, salt)
    # Same inputs -> same key.
    assert a.key == b.key


def test_derive_key_changes_with_salt():
    sentinel = _sentinel_bytes()
    a = derive_key(sentinel, salt=b"\x33" * 32)
    b = derive_key(sentinel, salt=b"\x44" * 32)
    assert a.key != b.key


def test_derive_key_changes_with_sentinel():
    salt = b"\x33" * 32
    a = derive_key(_sentinel_bytes(), salt)
    b = derive_key(_sentinel_bytes() + b"\x00mutation", salt)
    assert a.key != b.key


def test_derive_key_rejects_empty_sentinel():
    with pytest.raises(CorpusCryptoError):
        derive_key(b"", salt=b"\x00" * 32)


def test_derive_key_rejects_wrong_salt_length():
    with pytest.raises(CorpusCryptoError):
        derive_key(_sentinel_bytes(), salt=b"\x00" * 8)


# ---------------------------------------------------------------------------
# Sentinel resolution / read
# ---------------------------------------------------------------------------


def test_resolve_sentinel_path_returns_none_when_root_missing():
    assert resolve_sentinel_path(None) is None


def test_resolve_sentinel_path_uses_default_filename(tmp_path: Path):
    out = resolve_sentinel_path(tmp_path)
    assert out == tmp_path / DEFAULT_SENTINEL_NAME


def test_resolve_sentinel_path_rejects_traversal_in_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ADOPTIQ_CORPUS_SENTINEL", "../escape.json")
    with pytest.raises(CorpusCryptoError):
        resolve_sentinel_path(tmp_path)


def test_resolve_sentinel_path_rejects_separators(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ADOPTIQ_CORPUS_SENTINEL", "subdir/file.json")
    with pytest.raises(CorpusCryptoError):
        resolve_sentinel_path(tmp_path)


def test_read_sentinel_bytes_missing(tmp_path: Path):
    with pytest.raises(CorpusCryptoError):
        read_sentinel_bytes(tmp_path / "nope.json")


def test_read_sentinel_bytes_empty(tmp_path: Path):
    p = tmp_path / "empty.json"
    p.write_bytes(b"")
    with pytest.raises(CorpusCryptoError):
        read_sentinel_bytes(p)


def test_read_sentinel_bytes_returns_bytes_for_valid_file():
    sentinel = _FIXTURES_ROOT / "sentinel.json"
    out = read_sentinel_bytes(sentinel)
    assert isinstance(out, bytes)
    assert b"round17-test-sentinel" in out


def test_read_sentinel_bytes_with_none_raises():
    with pytest.raises(CorpusCryptoError):
        read_sentinel_bytes(None)


# ---------------------------------------------------------------------------
# Salt management
# ---------------------------------------------------------------------------


def test_get_or_create_salt_persists_with_mode_0600(tmp_path: Path):
    db = tmp_path / "knowledge" / "corpus.db.enc"
    salt = get_or_create_salt(db)
    assert isinstance(salt, bytes)
    assert len(salt) == 32

    salt_file = db.with_suffix(".salt")
    assert salt_file.exists()
    mode = stat.S_IMODE(salt_file.stat().st_mode)
    # File must be readable / writable only by owner.
    # macOS / Linux honour the chmod; Windows runners may not, so
    # only assert on POSIX.
    if os.name == "posix":
        assert mode == 0o600


def test_get_or_create_salt_is_idempotent(tmp_path: Path):
    db = tmp_path / "knowledge" / "corpus.db.enc"
    a = get_or_create_salt(db)
    b = get_or_create_salt(db)
    assert a == b


def test_get_or_create_salt_regenerates_when_corrupted(tmp_path: Path):
    db = tmp_path / "knowledge" / "corpus.db.enc"
    a = get_or_create_salt(db)
    # Truncate the salt file -- next call should rotate to a fresh
    # 32-byte salt rather than crash or accept the short value.
    salt_file = db.with_suffix(".salt")
    salt_file.write_bytes(b"\x00" * 4)
    b = get_or_create_salt(db)
    assert len(b) == 32
    assert b != a


# ---------------------------------------------------------------------------
# Encryption / decryption round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip():
    key = _make_key()
    plaintext = b"Round 17 corpus test payload -- not a secret."
    blob = encrypt_bytes(key, plaintext)
    assert blob != plaintext
    assert decrypt_bytes(key, blob) == plaintext


def test_encrypt_uses_fresh_nonce_per_call():
    key = _make_key()
    plaintext = b"identical input"
    a = encrypt_bytes(key, plaintext)
    b = encrypt_bytes(key, plaintext)
    # GCM nonce is fresh per write -> two ciphertexts must differ.
    assert a != b
    # Both must still decrypt to the same plaintext.
    assert decrypt_bytes(key, a) == plaintext
    assert decrypt_bytes(key, b) == plaintext


def test_decrypt_with_wrong_key_raises():
    key1 = _make_key()
    key2 = derive_key(_sentinel_bytes(), salt=b"\x99" * 32)
    blob = encrypt_bytes(key1, b"hello")
    with pytest.raises(CorpusCryptoError):
        decrypt_bytes(key2, blob)


def test_decrypt_tampered_ciphertext_raises():
    key = _make_key()
    blob = bytearray(encrypt_bytes(key, b"hello world"))
    # Flip a bit somewhere in the tag region.
    blob[-1] ^= 0xFF
    with pytest.raises(CorpusCryptoError):
        decrypt_bytes(key, bytes(blob))


def test_decrypt_short_blob_raises():
    key = _make_key()
    with pytest.raises(CorpusCryptoError):
        decrypt_bytes(key, b"short")


def test_encrypt_rejects_non_bytes():
    key = _make_key()
    with pytest.raises(CorpusCryptoError):
        encrypt_bytes(key, "not-bytes")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Encrypted SQLite round-trip
# ---------------------------------------------------------------------------


def test_open_encrypted_corpus_round_trip(tmp_path: Path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    key = _make_key()

    with open_encrypted_corpus(enc, key) as handle:
        assert isinstance(handle, EncryptedCorpusHandle)
        cur = handle.conn.cursor()
        cur.execute('CREATE TABLE notes ("id" INTEGER PRIMARY KEY, "body" TEXT NOT NULL);')
        cur.execute('INSERT INTO notes ("body") VALUES (?);', ("hello round 17",))
        handle.conn.commit()

    # The encrypted file must not contain the SQLite header in
    # plaintext; if it did, the at-rest crypto would be a no-op.
    assert enc.exists()
    raw = enc.read_bytes()
    assert b"SQLite format 3" not in raw
    assert b"hello round 17" not in raw

    # And the plaintext temp file must have been scrubbed.
    plaintext_path = handle.plaintext_path
    assert not plaintext_path.exists()

    # Re-open and verify the row is still there.
    with open_encrypted_corpus(enc, key) as handle2:
        rows = handle2.conn.execute('SELECT "body" FROM notes;').fetchall()
        assert len(rows) == 1
        assert rows[0]["body"] == "hello round 17"


def test_open_encrypted_corpus_wrong_key_raises(tmp_path: Path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    key = _make_key()
    other = derive_key(_sentinel_bytes(), salt=b"\x77" * 32)

    with open_encrypted_corpus(enc, key) as handle:
        handle.conn.execute("CREATE TABLE x (id INTEGER);")

    with pytest.raises(CorpusCryptoError):
        open_encrypted_corpus(enc, other)


def test_open_encrypted_corpus_missing_without_create_raises(tmp_path: Path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    key = _make_key()
    with pytest.raises(CorpusCryptoError):
        open_encrypted_corpus(enc, key, create_if_missing=False)


# ---------------------------------------------------------------------------
# open_corpus_for_user -- end-to-end gating
# ---------------------------------------------------------------------------


def test_open_corpus_for_user_missing_onedrive_root_raises(tmp_path: Path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    with pytest.raises(CorpusCryptoError):
        open_corpus_for_user(onedrive_root=None, encrypted_path=enc)


def test_open_corpus_for_user_missing_sentinel_raises(tmp_path: Path):
    onedrive = tmp_path / "OneDrive_AdoptIQ_CSOne_Reports"
    onedrive.mkdir()
    # No sentinel inside -> CorpusUnavailable-style error.
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    with pytest.raises(CorpusCryptoError):
        open_corpus_for_user(onedrive_root=onedrive, encrypted_path=enc)


def test_open_corpus_for_user_round_trip(tmp_path: Path, monkeypatch):
    onedrive = tmp_path / "OneDrive_AdoptIQ_CSOne_Reports"
    onedrive.mkdir()
    sentinel = onedrive / DEFAULT_SENTINEL_NAME
    sentinel.write_bytes(_sentinel_bytes())

    enc = tmp_path / "knowledge" / "corpus.db.enc"
    with open_corpus_for_user(onedrive_root=onedrive, encrypted_path=enc) as handle:
        handle.conn.execute(
            'CREATE TABLE notes ("id" INTEGER PRIMARY KEY, "body" TEXT);'
        )
        handle.conn.execute('INSERT INTO notes ("body") VALUES (?)', ("synthetic",))
        handle.conn.commit()

    # File on disk is encrypted.
    raw = enc.read_bytes()
    assert b"SQLite format 3" not in raw
    assert b"synthetic" not in raw

    # And we can re-open with the same sentinel/salt pair.
    with open_corpus_for_user(onedrive_root=onedrive, encrypted_path=enc) as handle2:
        rows = handle2.conn.execute('SELECT "body" FROM notes;').fetchall()
        assert [r["body"] for r in rows] == ["synthetic"]
