"""Round 34 / A1 -- corpus_crypto sentinel pinning anti-bricking pin.

Closes the bricking regression where a user who started on the
auto-minted local sentinel got locked out of their corpus once
SharePoint sync later delivered a different sentinel.  Without
pinning, ``open_corpus_for_user`` walks
OneDrive > SharePoint > local-mint priority on every call -- so
once SharePoint shows up, ``derive_key`` produces a different AES
key and ``decrypt_bytes`` fails the GCM tag check on the existing
``corpus.db.enc``.  CorpusCryptoError raised, corpus inaccessible.

Round 34 / A1 introduces ``corpus.sentinel.lock.json`` next to the
encrypted DB.  It pins the SHA-256 digest prefix of whichever
sentinel actually sealed the corpus.  Subsequent opens consult the
lock and re-derive the same key regardless of which sentinel roots
become available later.

These tests pin:

1. **Lock minted on fresh open** -- first ``open_corpus_for_user``
   creates the sidecar with the correct digest + source.
2. **Lock honored on re-open** -- second open under a *different*
   higher-priority sentinel still uses the pinned one.
3. **Sentinel rotation fails LOUD, not silent** -- if the pinned
   sentinel is gone but a different sentinel is available, raise
   a clear ``CorpusCryptoError`` instead of silently re-pinning
   (the operator decides whether to migrate).
4. **Lock file mode is 0o600**.
5. **Backward compat** -- legacy installs without a lock still
   open and gain a lock on first open.
6. **Operator-deleted lock recovers** -- removing the lock and
   re-opening re-pins to the highest-priority candidate.

Tests are deterministic and offline.  No network.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

import corpus_crypto as cc


def _write_sentinel(root, payload):
    root.mkdir(parents=True, exist_ok=True)
    path = root / cc.DEFAULT_SENTINEL_NAME
    path.write_bytes(payload)
    return path


def _enc_path(tmp_path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)
    return enc


def test_lock_minted_on_fresh_open_carries_digest_and_source(tmp_path):
    """First open against a real sentinel must mint the lock so the
    next open can re-pin without re-walking priority."""
    od_root = tmp_path / "onedrive"
    enc = _enc_path(tmp_path)
    sentinel_payload = b"onedrive-payload-for-pinning-test"
    _write_sentinel(od_root, sentinel_payload)

    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.close(persist=False)

    lock_path = enc.parent / "corpus.sentinel.lock.json"
    assert lock_path.exists(), "expected sidecar lock after fresh open"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["source"] == "onedrive"
    expected_prefix = cc._sentinel_digest_prefix(sentinel_payload)
    assert payload["sentinel_sha256_prefix"] == expected_prefix
    assert "minted_at" in payload


def test_pinned_sentinel_wins_over_higher_priority_new_sentinel(tmp_path):
    """The whole point of pinning: once a sentinel sealed the corpus,
    opening must re-pin on THAT sentinel even if a higher-priority
    sentinel becomes available later.  This is the bricking scenario
    the prompt called out: user starts on local-mint, SharePoint sync
    later delivers a different sentinel, corpus must NOT brick."""
    enc = _enc_path(tmp_path)

    # Pass 1: no real sentinels -> mints local sentinel + commits.
    handle = cc.open_corpus_for_user(
        onedrive_root=tmp_path / "missing-od",
        encrypted_path=enc,
        sharepoint_root=tmp_path / "missing-sp",
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    minted = enc.parent / "sentinel.json"
    assert minted.exists(), "auto-minted local sentinel missing"
    minted_bytes = minted.read_bytes()
    lock_path = enc.parent / "corpus.sentinel.lock.json"
    assert lock_path.exists()
    pinned_digest = json.loads(lock_path.read_text(encoding="utf-8"))[
        "sentinel_sha256_prefix"
    ]
    assert pinned_digest == cc._sentinel_digest_prefix(minted_bytes)

    # Pass 2: SharePoint sync now delivers a DIFFERENT sentinel under
    # the OneDrive root (simulates the post-bootstrap rotation).  The
    # pre-A1 code would derive a different key here and InvalidTag
    # the existing corpus.  With the lock, we must re-pin on local.
    od_root = tmp_path / "onedrive"
    _write_sentinel(od_root, b"newly-delivered-onedrive-sentinel")

    handle2 = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    # If the bug were back, this would raise CorpusCryptoError.
    handle2.close(persist=False)

    # Lock unchanged -- still pinned to local.
    after = json.loads(lock_path.read_text(encoding="utf-8"))
    assert after["sentinel_sha256_prefix"] == pinned_digest, (
        "lock digest must not change after a benign re-open"
    )
    assert after["source"] == "local"


def test_pin_violation_raises_loud_error_when_no_candidate_matches(tmp_path):
    """If every available sentinel produces a different digest than
    the lock pins, fail loud with operator guidance.  Silently
    re-pinning would let attacker-supplied or accidentally-rotated
    sentinels swap the encryption key without anyone noticing."""
    enc = _enc_path(tmp_path)

    # Seed an encrypted corpus + lock under one sentinel.
    od_root = tmp_path / "onedrive"
    _write_sentinel(od_root, b"original-sentinel")
    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    # Replace the OneDrive sentinel with a DIFFERENT payload.
    od_root_path = od_root / cc.DEFAULT_SENTINEL_NAME
    od_root_path.write_bytes(b"replaced-sentinel-payload")

    # Open must now fail loud.
    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.open_corpus_for_user(
            onedrive_root=od_root,
            encrypted_path=enc,
        )
    msg = str(excinfo.value)
    assert "sentinel lock" in msg
    assert "digest_prefix" in msg
    # Operator guidance must reference the migration path.
    assert "remove" in msg.lower() or "migrate" in msg.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_lock_file_mode_is_0600(tmp_path):
    od_root = tmp_path / "onedrive"
    enc = _enc_path(tmp_path)
    _write_sentinel(od_root, b"payload")

    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.close(persist=False)

    lock_path = enc.parent / "corpus.sentinel.lock.json"
    mode = os.stat(lock_path).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600 on sentinel lock, got {oct(mode)}"


def test_legacy_install_without_lock_opens_then_mints_lock(tmp_path):
    """Backward compat: an install that was opened pre-A1 has an
    encrypted corpus + salt but no lock.  First open under the new
    code must succeed (using priority order) and mint the lock."""
    od_root = tmp_path / "onedrive"
    enc = _enc_path(tmp_path)
    _write_sentinel(od_root, b"legacy-install-payload")

    # Simulate the pre-A1 state: open + commit + delete the lock.
    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)
    lock_path = enc.parent / "corpus.sentinel.lock.json"
    lock_path.unlink()
    assert not lock_path.exists()

    # Re-open should succeed AND re-mint the lock.
    handle2 = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle2.close(persist=False)
    assert lock_path.exists(), "expected lock re-minted on legacy open"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["source"] == "onedrive"


def test_operator_deleted_lock_recovers_and_re_pins(tmp_path):
    """The intentional migration path: operator deletes the lock to
    re-seal under a different sentinel.  Next open must succeed and
    pin to whichever sentinel is now highest-priority."""
    enc = _enc_path(tmp_path)

    # Pass 1: seal under local sentinel.
    handle = cc.open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    # Operator deletes both the corpus AND the lock to migrate.
    enc.unlink()
    (enc.parent / "corpus.sentinel.lock.json").unlink()
    (enc.parent / "sentinel.json").unlink()
    salt_path = enc.with_suffix(".salt")
    if salt_path.exists():
        salt_path.unlink()

    # Pass 2: now SharePoint is configured.
    sp_root = tmp_path / "sharepoint_cache"
    _write_sentinel(sp_root, b"sharepoint-now-configured")
    handle2 = cc.open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=enc,
        sharepoint_root=sp_root,
    )
    handle2.commit_to_disk()
    handle2.close(persist=False)

    lock_path = enc.parent / "corpus.sentinel.lock.json"
    assert lock_path.exists()
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["source"] == "sharepoint"


def test_corrupt_lock_json_fails_loud(tmp_path):
    """If the lock file is present but unparseable, open must raise
    rather than silently re-pin -- a corrupt lock might be the result
    of a partial write or an attacker swap and deserves human
    investigation."""
    od_root = tmp_path / "onedrive"
    enc = _enc_path(tmp_path)
    _write_sentinel(od_root, b"payload")

    # Seed a real install.
    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
    )
    handle.commit_to_disk()
    handle.close(persist=False)

    # Corrupt the lock.
    lock_path = enc.parent / "corpus.sentinel.lock.json"
    lock_path.write_text("not-json{", encoding="utf-8")

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.open_corpus_for_user(
            onedrive_root=od_root,
            encrypted_path=enc,
        )
    assert "JSON" in str(excinfo.value) or "json" in str(excinfo.value).lower()


def test_lock_with_unknown_schema_fails_loud(tmp_path):
    """Forward-compat probe: a future schema bump must NOT silently
    fall back to the old logic.  Make this an explicit operator
    decision."""
    enc = _enc_path(tmp_path)
    enc.parent.mkdir(parents=True, exist_ok=True)
    lock_path = enc.parent / "corpus.sentinel.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "schema": 999,
                "sentinel_sha256_prefix": "0" * 16,
                "source": "future",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(cc.CorpusCryptoError) as excinfo:
        cc.open_corpus_for_user(
            onedrive_root=None,
            encrypted_path=enc,
        )
    assert "schema" in str(excinfo.value).lower()
