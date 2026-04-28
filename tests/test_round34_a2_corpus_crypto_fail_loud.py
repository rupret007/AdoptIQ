"""Round 34 / A2 -- corpus_crypto fail-loud on corrupt salt / sentinel.

Pre-Round-34 behavior: when ``corpus.salt`` had wrong length
(truncated, partial-write leftover) ``get_or_create_salt`` silently
regenerated it.  HKDF over the new salt produces a different AES
key, the existing ``corpus.db.enc`` decrypts to an InvalidTag,
corpus inaccessible.  Same shape on the sentinel side:
``_try_resolve_sentinel`` swallowed any
``CorpusCryptoError`` from ``read_sentinel_bytes`` (empty,
oversized, OS read failure), so a corrupt OneDrive sentinel
silently fell through to SharePoint or local-mint.  The operator
never learned their primary sentinel was broken.

Round 34 / A2 contract:

* ``get_or_create_salt`` -- if salt has wrong length AND an
  encrypted corpus exists, raise ``CorpusCryptoError`` instead of
  silently regenerating.
* ``_try_resolve_sentinel`` -- if the sentinel path is present but
  read / parse fails, propagate ``CorpusCryptoError`` instead of
  returning ``None``.

These tests are intentionally narrow: they pin the bug shape so a
future "let's just make it more forgiving" refactor cannot quietly
restore the bricking regression.
"""
from __future__ import annotations

import pytest

import corpus_crypto as cc


def _enc_path(tmp_path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)
    return enc


# ---------------------------------------------------------------------------
# Salt corruption
# ---------------------------------------------------------------------------


def test_truncated_salt_with_existing_corpus_fails_loud(tmp_path):
    """Salt file has wrong size AND corpus.db.enc exists: must raise.
    The legacy silent regeneration would brick the corpus."""
    od_root = tmp_path / "onedrive"
    od_root.mkdir()
    (od_root / cc.DEFAULT_SENTINEL_NAME).write_bytes(b"sentinel-payload")
    enc = _enc_path(tmp_path)

    # Seed a real install with a real salt + encrypted corpus.
    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    salt_path = enc.with_suffix(".salt")
    assert salt_path.exists()
    assert enc.exists()

    # Truncate the salt to a non-zero invalid length.
    salt_path.write_bytes(b"\x00" * 7)

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.get_or_create_salt(enc)
    msg = str(excinfo.value)
    assert "salt" in msg.lower()
    assert "corrupt" in msg.lower() or "got 7" in msg
    # Operator guidance must reference the recovery path.
    assert (
        "delete" in msg.lower() or "restore" in msg.lower() or "backup" in msg.lower()
    )


def test_truncated_salt_without_corpus_silently_regenerates(tmp_path):
    """No corpus to brick: regeneration is safe and remains the
    behavior so a half-finished install can recover."""
    enc = _enc_path(tmp_path)
    salt_path = enc.with_suffix(".salt")
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(b"\x00" * 7)
    # No enc file, so no corpus exists.
    assert not enc.exists()

    salt = cc.get_or_create_salt(enc)
    assert len(salt) == 32
    # Regenerated -- not the truncated bytes.
    assert salt != b"\x00" * 32


def test_oversized_salt_with_existing_corpus_fails_loud(tmp_path):
    """Wrong-length is wrong-length in either direction."""
    od_root = tmp_path / "onedrive"
    od_root.mkdir()
    (od_root / cc.DEFAULT_SENTINEL_NAME).write_bytes(b"sentinel-payload")
    enc = _enc_path(tmp_path)

    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    salt_path = enc.with_suffix(".salt")
    salt_path.write_bytes(b"\x00" * 64)  # 64 != 32

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.get_or_create_salt(enc)
    assert "got 64" in str(excinfo.value) or "corrupt" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Sentinel corruption
# ---------------------------------------------------------------------------


def test_empty_onedrive_sentinel_fails_loud(tmp_path):
    """Sentinel file present (zero bytes) at the OneDrive root must
    raise rather than silently fall through to SharePoint / local-mint."""
    od_root = tmp_path / "onedrive"
    od_root.mkdir()
    (od_root / cc.DEFAULT_SENTINEL_NAME).write_bytes(b"")  # empty

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc._try_resolve_sentinel(od_root)
    assert "empty" in str(excinfo.value).lower()


def test_oversized_onedrive_sentinel_fails_loud(tmp_path):
    """Sentinel above the 1 MiB cap is fail-loud (denial-of-memory
    defense + signals corruption)."""
    od_root = tmp_path / "onedrive"
    od_root.mkdir()
    # Just over the cap.
    (od_root / cc.DEFAULT_SENTINEL_NAME).write_bytes(b"\x00" * (1024 * 1024 + 1))

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc._try_resolve_sentinel(od_root)
    assert "too large" in str(excinfo.value).lower() or "size" in str(excinfo.value).lower()


def test_missing_root_returns_none(tmp_path):
    """Backward compat: a root that simply doesn't have the sentinel
    file is still a legitimate fall-through case (returns None).
    Don't confuse 'absent' with 'corrupt'."""
    od_root = tmp_path / "onedrive-without-sentinel"
    od_root.mkdir()
    # No sentinel file written.
    result = cc._try_resolve_sentinel(od_root)
    assert result is None


def test_none_root_returns_none():
    """Passing ``None`` as root is the explicit "not configured"
    signal -- still returns None."""
    assert cc._try_resolve_sentinel(None) is None


def test_corrupt_onedrive_sentinel_does_not_silently_fall_through_to_sharepoint(tmp_path):
    """End-to-end: a corrupt OneDrive sentinel must surface as an
    error from open_corpus_for_user, NOT silently use the SharePoint
    sentinel.  The operator needs to know their primary sentinel is
    broken."""
    od_root = tmp_path / "onedrive"
    od_root.mkdir()
    (od_root / cc.DEFAULT_SENTINEL_NAME).write_bytes(b"")  # corrupt: empty

    sp_root = tmp_path / "sharepoint"
    sp_root.mkdir()
    (sp_root / cc.DEFAULT_SENTINEL_NAME).write_bytes(b"valid-sharepoint-payload")

    enc = _enc_path(tmp_path)
    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.open_corpus_for_user(
            onedrive_root=od_root,
            encrypted_path=enc,
            sharepoint_root=sp_root,
        )
    # Error must mention sentinel / empty so it's actionable.
    assert "sentinel" in str(excinfo.value).lower()


def test_corrupt_local_sentinel_fails_loud_during_pin_walk(tmp_path):
    """If a previously-minted local sentinel is corrupted on disk
    (zero bytes), the lock-resolution walk must raise rather than
    silently treat it as 'absent' and re-mint a different one
    (which would brick the corpus)."""
    enc = _enc_path(tmp_path)
    # Seed the install -- mints local sentinel + lock + corpus.
    handle = cc.open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    local_sentinel = enc.parent / "sentinel.json"
    assert local_sentinel.exists()

    # Truncate the local sentinel to zero bytes (simulates partial
    # write or overzealous "scrub").
    local_sentinel.write_bytes(b"")

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.open_corpus_for_user(
            onedrive_root=None,
            encrypted_path=enc,
        )
    msg = str(excinfo.value)
    # Either the local-sentinel read raises empty (read path) or
    # the lock walk reports "no candidate matches" (because empty
    # bytes are filtered).  Either is fail-loud; pin both shapes.
    assert (
        "sentinel" in msg.lower()
        or "lock" in msg.lower()
    )
