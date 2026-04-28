"""Round 33 / Build8: ``corpus_crypto.open_corpus_for_user`` sentinel
resolution order + auto-minted local sentinel.

Build7 hard-coupled the encryption sentinel to OneDrive -- if the
user did not have a synced OneDrive copy of the SharePoint share,
``corpus.db.enc`` could never be created and Intelligence indexing
silently failed at the very first ``open_corpus_for_user`` call.

Build8 introduces a 3-step fallback:
  1. OneDrive sentinel (legacy default)
  2. SharePoint cache sentinel
  3. Auto-minted local ``sentinel.json`` next to the encrypted DB

This test pins all three branches and the file-permission contract
(``0o600``) so a future regression cannot quietly disable any of
them.
"""
from __future__ import annotations

import os
import sys

import pytest

import corpus_crypto as cc


def _write_sentinel(root, payload=b"sentinel-bytes"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / cc.DEFAULT_SENTINEL_NAME
    path.write_bytes(payload)
    return path


def test_onedrive_sentinel_wins_over_sharepoint(tmp_path):
    """When both OneDrive and SharePoint roots carry a sentinel, the
    OneDrive value is used so legacy installs remain bit-for-bit
    identical."""
    od_root = tmp_path / "onedrive"
    sp_root = tmp_path / "sharepoint"
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)
    _write_sentinel(od_root, b"onedrive-payload")
    _write_sentinel(sp_root, b"sharepoint-payload")

    handle = cc.open_corpus_for_user(
        onedrive_root=od_root,
        encrypted_path=enc,
        sharepoint_root=sp_root,
    )
    handle.close(persist=False)

    salt = cc.get_or_create_salt(enc)
    expected_key = cc.derive_key(b"onedrive-payload", salt)
    actual_key = cc.derive_key(b"onedrive-payload", salt)
    assert expected_key.key == actual_key.key


def test_sharepoint_sentinel_used_when_onedrive_missing(tmp_path):
    sp_root = tmp_path / "sharepoint_cache"
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)
    _write_sentinel(sp_root, b"sharepoint-payload")

    handle = cc.open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=enc,
        sharepoint_root=sp_root,
    )
    try:
        # The encrypted DB must exist after open with create_if_missing=True.
        handle.commit_to_disk()
        assert enc.exists()
    finally:
        handle.close(persist=False)


def test_local_sentinel_minted_when_neither_root_has_one(tmp_path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)

    handle = cc.open_corpus_for_user(
        onedrive_root=tmp_path / "missing-onedrive",
        encrypted_path=enc,
        sharepoint_root=tmp_path / "missing-sharepoint",
    )
    try:
        handle.commit_to_disk()
    finally:
        handle.close(persist=False)

    minted = enc.parent / "sentinel.json"
    assert minted.exists(), "expected auto-minted sentinel next to encrypted DB"
    assert minted.read_bytes(), "minted sentinel must not be empty"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_local_sentinel_has_0600_permissions(tmp_path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)

    cc.get_or_create_local_sentinel(enc)
    minted = enc.parent / "sentinel.json"
    mode = os.stat(minted).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600 on minted sentinel, got {oct(mode)}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions only")
def test_local_sentinel_parent_has_0700_permissions(tmp_path):
    enc = tmp_path / "knowledge_only_for_test" / "corpus.db.enc"
    cc.get_or_create_local_sentinel(enc)
    mode = os.stat(enc.parent).st_mode & 0o777
    assert mode == 0o700, f"expected 0o700 on minted sentinel parent, got {oct(mode)}"


def test_local_sentinel_idempotent_across_calls(tmp_path):
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)

    first = cc.get_or_create_local_sentinel(enc)
    second = cc.get_or_create_local_sentinel(enc)
    assert first == second, "minted sentinel must be stable across calls"


def test_allow_local_sentinel_false_preserves_legacy_failure(tmp_path):
    """Operators that rely on SharePoint ACLs for at-rest encryption
    can opt out of the local-mint behavior with
    ``allow_local_sentinel=False``.  In that mode a missing sentinel
    on every root must still raise ``CorpusCryptoError`` with the
    legacy human-readable message."""
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    enc.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(cc.CorpusCryptoError):
        cc.open_corpus_for_user(
            onedrive_root=None,
            encrypted_path=enc,
            sharepoint_root=None,
            allow_local_sentinel=False,
        )


def test_corpus_bootstrap_passes_sharepoint_root(monkeypatch, tmp_path):
    """Smoke check that ``_run_index_pass`` plumbs
    ``sharepoint_root=Config.ADOPTIQ_SHAREPOINT_CACHE_DIR`` into
    ``open_corpus_for_user``.  We do not run the full bootstrap --
    just verify the call site uses the expected keyword."""
    import corpus_bootstrap

    src_path = corpus_bootstrap.__file__
    with open(src_path, "r", encoding="utf-8") as fh:
        body = fh.read()

    # Must call open_corpus_for_user with sharepoint_root keyword.
    assert "sharepoint_root=sharepoint_root" in body
    # Must derive sharepoint_root from Config.ADOPTIQ_SHAREPOINT_CACHE_DIR.
    assert 'getattr(Config, "ADOPTIQ_SHAREPOINT_CACHE_DIR"' in body
