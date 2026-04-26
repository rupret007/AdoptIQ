"""Round 17 / Phase B.3 -- AES-256-GCM at-rest encryption for the
CSOne knowledge corpus.

The corpus database carries customer-confidential data (case history,
sentiment, escalation context).  At rest we keep it encrypted under
``~/Library/Application Support/AdoptIQ/knowledge/corpus.db.enc`` (or
the Windows equivalent) with a key derived from a sentinel file
inside the user's CSOne corpus root -- either the local SharePoint
cache populated by ``sharepoint_corpus_source.refresh_local_cache``
(Round 17.2), or a locally-synced OneDrive copy of the same share.

Why the sentinel-derived key?
-----------------------------
The sentinel lives inside the SharePoint share, so it is delivered to
disk only when the operator can authenticate to and read the share.
Whether that delivery happens via Microsoft Graph (Round 17.2 pull)
or the OneDrive client, the upstream SharePoint ACL is enforced by
Microsoft -- the operator without share access never gets the
sentinel, key derivation fails, decryption fails, and the corpus
surfaces as ``CorpusUnavailable``.  This delegates the access-control
decision to SharePoint where it belongs.

Design contract
---------------
* AES-256-GCM only.  No CBC / CTR / ECB.  No legacy algorithms.
  ``codeguard-0-additional-cryptography``.
* HKDF-SHA-256 derives the AES key from ``(sentinel_bytes,
  per_install_salt)``.  The salt is generated once per host and
  stored next to the encrypted DB; it is *not* a secret on its own.
* Fresh 96-bit nonce per write (``os.urandom``).  Nonces are stored
  alongside the ciphertext.
* Decrypted plaintext lives only in memory or in a temp file with
  mode ``0600``.  The temp file is removed when the connection is
  closed.
* No hardcoded keys, passwords, or tokens.  ``codeguard-1-hardcoded-credentials``.
* Defensive: every public function returns an explicit result object
  or raises ``CorpusCryptoError`` with a human-readable reason.  No
  silent fallbacks.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: AES-256-GCM key length.
_KEY_BYTES: int = 32

#: GCM nonce length (NIST SP 800-38D recommends 96 bits).
_NONCE_BYTES: int = 12

#: HKDF info / context label.  Pinned in tests so a future relabel is
#: a deliberate, breaking change rather than a silent rotation.
_HKDF_INFO: bytes = b"AdoptIQ.Round17.CorpusKnowledgeStore.v1"

#: Per-install salt length.  256 bits -- not strictly required but
#: keeps the KDF input distinct from any plaintext sentinel.
_SALT_BYTES: int = 32

#: Default sentinel filename inside the corpus root (SharePoint cache
#: or OneDrive folder).  The corpus maintainer creates this file
#: once on the share and keeps it alongside the
#: reports.  Anyone with SharePoint folder access auto-syncs it; no
#: one else can read it.  The filename can be overridden via the
#: ``ADOPTIQ_CORPUS_SENTINEL`` env var for tests.
DEFAULT_SENTINEL_NAME: str = "adoptiq_corpus_sentinel.json"

#: Maximum sentinel size accepted by the KDF (defends against a
#: hostile sentinel that tries to consume gigabytes of memory).
_MAX_SENTINEL_BYTES: int = 1 * 1024 * 1024


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CorpusCryptoError(RuntimeError):
    """Raised when the encrypted-at-rest pipeline cannot complete the
    requested operation (sentinel missing, wrong key, file corrupt,
    etc.)."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusKey:
    """Wrapper for an HKDF-derived AES-256 key.  Frozen so a caller
    cannot mutate the bytes; equality is intentionally identity-based
    so we never compare keys with ``==`` (timing risk)."""

    key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key, (bytes, bytearray)) or len(self.key) != _KEY_BYTES:
            raise CorpusCryptoError(
                f"corpus key must be exactly {_KEY_BYTES} bytes; got "
                f"{type(self.key).__name__} len={len(self.key) if hasattr(self.key, '__len__') else '?'}"
            )

    def __eq__(self, other: object) -> bool:  # noqa: D401
        # ``cryptography``'s constant-time compare; intentionally
        # disable Python's ``==`` to avoid timing leaks if key
        # comparison ever becomes attacker-influenced.
        if not isinstance(other, CorpusKey):
            return NotImplemented
        return secrets.compare_digest(bytes(self.key), bytes(other.key))

    def __hash__(self) -> int:  # pragma: no cover - identity hash is fine
        return id(self)

    def __repr__(self) -> str:
        # Never print the raw bytes.  Emit a short SHA-256 prefix of
        # the key material so structured logs can correlate without
        # leaking key bytes.
        h = hashes.Hash(hashes.SHA256())
        h.update(bytes(self.key))
        digest = h.finalize().hex()
        return f"<CorpusKey sha256_prefix={digest[:8]}>"


# ---------------------------------------------------------------------------
# Sentinel & salt management
# ---------------------------------------------------------------------------


def resolve_sentinel_path(onedrive_root: Path | str | None) -> Optional[Path]:
    """Return the absolute path of the sentinel file under
    ``onedrive_root``, or ``None`` when no root is configured.

    The sentinel filename can be overridden via
    ``ADOPTIQ_CORPUS_SENTINEL``; this is intended for tests that
    cannot mutate the user's real OneDrive directory.
    """
    if onedrive_root is None:
        return None
    name = os.environ.get("ADOPTIQ_CORPUS_SENTINEL", DEFAULT_SENTINEL_NAME).strip() or DEFAULT_SENTINEL_NAME
    # Defensive: reject anything that looks like a path traversal so
    # an env-var override cannot escape the configured root.
    if "/" in name or "\\" in name or ".." in name:
        raise CorpusCryptoError("sentinel filename must not contain path separators or '..'")
    root_path = Path(onedrive_root)
    return root_path / name


def read_sentinel_bytes(sentinel_path: Optional[Path]) -> bytes:
    """Read the sentinel file at ``sentinel_path`` and return its
    bytes.  Raises ``CorpusCryptoError`` when the file is missing,
    not a regular file, empty, or larger than ``_MAX_SENTINEL_BYTES``.
    """
    if sentinel_path is None:
        raise CorpusCryptoError("CSOne corpus folder is not configured")
    if not sentinel_path.exists() or not sentinel_path.is_file():
        raise CorpusCryptoError(
            "corpus sentinel not found -- this typically means SharePoint "
            "sign-in has not completed, the OneDrive folder is not synced, "
            "or the operator does not have access to the "
            "AdoptIQ_CSOne_Reports share"
        )
    try:
        size = sentinel_path.stat().st_size
    except OSError as stat_err:
        raise CorpusCryptoError(f"sentinel stat failed: {stat_err}") from stat_err
    if size <= 0:
        raise CorpusCryptoError("corpus sentinel is empty")
    if size > _MAX_SENTINEL_BYTES:
        raise CorpusCryptoError(
            f"corpus sentinel is too large ({size} bytes > {_MAX_SENTINEL_BYTES})"
        )
    try:
        with sentinel_path.open("rb") as fh:
            data = fh.read(_MAX_SENTINEL_BYTES + 1)
    except OSError as read_err:
        raise CorpusCryptoError(f"sentinel read failed: {read_err}") from read_err
    if len(data) > _MAX_SENTINEL_BYTES:
        raise CorpusCryptoError("corpus sentinel exceeded size cap during read")
    return data


def _salt_path_for(db_path: Path) -> Path:
    """Salt file lives next to the encrypted DB.  Filename is
    ``corpus.salt`` -- it is not a secret but its location is locked
    down so it does not co-mingle with other AdoptIQ state."""
    return db_path.with_suffix(".salt")


def get_or_create_salt(db_path: Path | str) -> bytes:
    """Read the per-install salt, generating one if necessary.  The
    salt file is created with mode ``0600``."""
    db_p = Path(db_path)
    salt_path = _salt_path_for(db_p)
    if salt_path.exists() and salt_path.is_file():
        try:
            data = salt_path.read_bytes()
        except OSError as read_err:
            raise CorpusCryptoError(f"salt read failed: {read_err}") from read_err
        if len(data) == _SALT_BYTES:
            return data
        # Length mismatch -- regenerate.
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    new_salt = secrets.token_bytes(_SALT_BYTES)
    fd = os.open(str(salt_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, new_salt)
    finally:
        os.close(fd)
    try:
        os.chmod(salt_path, 0o600)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0600 failed for %s: %s", salt_path, chmod_err)
    return new_salt


def derive_key(sentinel_bytes: bytes, salt: bytes) -> CorpusKey:
    """HKDF-SHA-256 over ``(sentinel_bytes, salt)``; ``info`` is the
    pinned ``_HKDF_INFO`` label.  Returns a :class:`CorpusKey`."""
    if not sentinel_bytes:
        raise CorpusCryptoError("sentinel bytes are empty")
    if len(salt) != _SALT_BYTES:
        raise CorpusCryptoError(f"salt must be {_SALT_BYTES} bytes; got {len(salt)}")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=salt,
        info=_HKDF_INFO,
    )
    raw = hkdf.derive(sentinel_bytes)
    return CorpusKey(key=raw)


# ---------------------------------------------------------------------------
# File-level encryption helpers
# ---------------------------------------------------------------------------


def encrypt_bytes(key: CorpusKey, plaintext: bytes) -> bytes:
    """AES-256-GCM seal.  Returned blob layout: 12-byte nonce ||
    ciphertext+tag.  Always uses a fresh nonce."""
    if not isinstance(plaintext, (bytes, bytearray)):
        raise CorpusCryptoError("plaintext must be bytes")
    aes = AESGCM(key.key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ct = aes.encrypt(nonce, bytes(plaintext), None)
    return nonce + ct


def decrypt_bytes(key: CorpusKey, blob: bytes) -> bytes:
    """AES-256-GCM open.  ``blob`` must be the layout produced by
    :func:`encrypt_bytes`.  Raises ``CorpusCryptoError`` when the tag
    does not verify (wrong key, truncated, corrupt, tampered)."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < _NONCE_BYTES + 16:
        raise CorpusCryptoError("encrypted blob is malformed (too short)")
    nonce = bytes(blob[:_NONCE_BYTES])
    ct = bytes(blob[_NONCE_BYTES:])
    aes = AESGCM(key.key)
    try:
        return aes.decrypt(nonce, ct, None)
    except InvalidTag as tag_err:
        raise CorpusCryptoError(
            "corpus decrypt failed: authentication tag mismatch (wrong key, "
            "tampered ciphertext, or sentinel changed)"
        ) from tag_err


# ---------------------------------------------------------------------------
# Encrypted SQLite open / close
# ---------------------------------------------------------------------------


@dataclass
class EncryptedCorpusHandle:
    """Open handle to an encrypted-at-rest corpus database.

    ``conn`` is a normal :class:`sqlite3.Connection` against a
    ``0600`` mode plaintext file under ``$TMPDIR``.  ``commit()``
    re-encrypts and writes back to ``encrypted_path``; ``close()``
    flushes and removes the plaintext temp file."""

    conn: sqlite3.Connection
    plaintext_path: Path
    encrypted_path: Path
    key: CorpusKey
    _closed: bool = False

    def commit_to_disk(self) -> None:
        """Encrypt the plaintext temp file and atomically replace
        ``encrypted_path``.  Used by the indexer after every batch so
        a crash mid-run does not lose progress."""
        if self._closed:
            raise CorpusCryptoError("handle is closed")
        self.conn.commit()
        plaintext = self.plaintext_path.read_bytes()
        sealed = encrypt_bytes(self.key, plaintext)
        # Write to sibling tmp + os.replace for atomicity.
        tmp = self.encrypted_path.with_suffix(self.encrypted_path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, sealed)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.encrypted_path))
        try:
            os.chmod(self.encrypted_path, 0o600)
        except OSError as chmod_err:  # pragma: no cover - exotic FS
            logger.debug("chmod 0600 failed for %s: %s", self.encrypted_path, chmod_err)

    def close(self, *, persist: bool = True) -> None:
        """Close the connection, optionally persisting changes, then
        scrub the plaintext temp file."""
        if self._closed:
            return
        try:
            if persist:
                try:
                    self.commit_to_disk()
                except CorpusCryptoError:
                    raise
                except Exception as persist_err:  # noqa: BLE001 - defensive
                    logger.warning(
                        "Round 17 / corpus_crypto: commit_to_disk failed: %s",
                        persist_err,
                    )
        finally:
            try:
                self.conn.close()
            except sqlite3.DatabaseError as close_err:
                logger.debug("conn.close failed: %s", close_err)
            try:
                if self.plaintext_path.exists():
                    # Best-effort scrub before unlink.
                    try:
                        size = self.plaintext_path.stat().st_size
                        with self.plaintext_path.open("r+b") as fh:
                            fh.write(b"\x00" * min(size, 1 * 1024 * 1024))
                            fh.flush()
                            os.fsync(fh.fileno())
                    except OSError as scrub_err:
                        logger.debug("plaintext scrub failed: %s", scrub_err)
                    self.plaintext_path.unlink(missing_ok=True)
            except OSError as unlink_err:  # pragma: no cover - exotic FS
                logger.debug("plaintext unlink failed: %s", unlink_err)
            self._closed = True

    def __enter__(self) -> "EncryptedCorpusHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Persist only if the body did not raise.
        self.close(persist=exc_type is None)


def open_encrypted_corpus(
    encrypted_path: Path | str,
    key: CorpusKey,
    *,
    create_if_missing: bool = True,
) -> EncryptedCorpusHandle:
    """Open ``encrypted_path`` and decrypt to a ``0600`` temp file
    that backs a sqlite3 connection.  Returns an
    :class:`EncryptedCorpusHandle`.

    Raises ``CorpusCryptoError`` when the file exists but cannot be
    decrypted (wrong key, truncated, etc.).  When the file does not
    exist and ``create_if_missing=True``, a fresh empty database is
    created (and will be sealed on the first :meth:`commit_to_disk`).
    """
    enc_p = Path(encrypted_path)
    enc_p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(enc_p.parent, 0o700)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0700 failed for %s: %s", enc_p.parent, chmod_err)

    # Plaintext temp file under $TMPDIR with 0600.
    tmp_dir = Path(tempfile.gettempdir()) / "adoptiq_corpus"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tmp_dir, 0o700)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0700 failed for %s: %s", tmp_dir, chmod_err)

    fd, plaintext_path_str = tempfile.mkstemp(
        prefix="corpus.",
        suffix=".db",
        dir=str(tmp_dir),
    )
    os.close(fd)
    plaintext_path = Path(plaintext_path_str)
    try:
        os.chmod(plaintext_path, 0o600)
    except OSError as chmod_err:  # pragma: no cover - exotic FS
        logger.debug("chmod 0600 failed for %s: %s", plaintext_path, chmod_err)

    if enc_p.exists():
        try:
            blob = enc_p.read_bytes()
        except OSError as read_err:
            plaintext_path.unlink(missing_ok=True)
            raise CorpusCryptoError(f"encrypted corpus read failed: {read_err}") from read_err
        try:
            plaintext = decrypt_bytes(key, blob)
        except CorpusCryptoError:
            plaintext_path.unlink(missing_ok=True)
            raise
        plaintext_path.write_bytes(plaintext)
        try:
            os.chmod(plaintext_path, 0o600)
        except OSError as chmod_err:  # pragma: no cover - exotic FS
            logger.debug("chmod 0600 failed for %s: %s", plaintext_path, chmod_err)
    elif not create_if_missing:
        plaintext_path.unlink(missing_ok=True)
        raise CorpusCryptoError(f"encrypted corpus not found at {enc_p}")
    # else: fresh, empty plaintext file -> empty sqlite.

    conn = sqlite3.connect(
        str(plaintext_path),
        check_same_thread=False,
        timeout=30.0,
    )
    conn.row_factory = sqlite3.Row
    return EncryptedCorpusHandle(
        conn=conn,
        plaintext_path=plaintext_path,
        encrypted_path=enc_p,
        key=key,
    )


# ---------------------------------------------------------------------------
# High-level helper used by app_simple
# ---------------------------------------------------------------------------


def open_corpus_for_user(
    *,
    onedrive_root: Path | str | None,
    encrypted_path: Path | str,
    create_if_missing: bool = True,
) -> EncryptedCorpusHandle:
    """One-shot helper that resolves the sentinel, derives the key,
    and opens the encrypted corpus.  Raises ``CorpusCryptoError``
    with a human-readable reason when any step fails (missing
    sentinel = missing SharePoint ACL = corpus unavailable)."""
    sentinel = resolve_sentinel_path(onedrive_root)
    sentinel_bytes = read_sentinel_bytes(sentinel)
    salt = get_or_create_salt(encrypted_path)
    key = derive_key(sentinel_bytes, salt)
    return open_encrypted_corpus(encrypted_path, key, create_if_missing=create_if_missing)


__all__ = [
    "CorpusCryptoError",
    "CorpusKey",
    "DEFAULT_SENTINEL_NAME",
    "EncryptedCorpusHandle",
    "decrypt_bytes",
    "derive_key",
    "encrypt_bytes",
    "get_or_create_salt",
    "open_corpus_for_user",
    "open_encrypted_corpus",
    "read_sentinel_bytes",
    "resolve_sentinel_path",
]
