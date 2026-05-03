"""Round 71 / Phase 2 (#13) -- Missing salt + existing DB fails loud.

Pre-R71 ``get_or_create_salt`` silently minted a new salt when the
salt file was missing but the encrypted corpus DB already existed.
HKDF over the new salt produced a different AES key, so the existing
``corpus.db.enc`` decrypted to gibberish (``InvalidTag``) and the
install was bricked.  Round 39's self-heal handled the post-mortem,
but the salt-mint itself was the failure source.

Round 71 / Phase 2 (#13) raises ``CorpusCryptoError`` when the salt
is missing but the DB is present, so the operator sees the failure
immediately and Round 39's self-heal path can repair from the bake.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import corpus_crypto


def test_round71_missing_salt_with_existing_db_raises_loud(tmp_path: Path) -> None:
    """When the encrypted DB exists but the salt file is missing,
    ``get_or_create_salt`` MUST raise ``CorpusCryptoError`` (do NOT
    silently mint a new salt that bricks the install)."""
    db_path = tmp_path / "corpus.db.enc"
    db_path.write_bytes(b"\x00" * 1024)  # placeholder encrypted bytes
    # Salt file is intentionally absent -- this is the bug condition.

    with pytest.raises(corpus_crypto.CorpusCryptoError, match="missing"):
        corpus_crypto.get_or_create_salt(str(db_path))


def test_round71_missing_salt_no_db_silently_mints_new_salt(tmp_path: Path) -> None:
    """When NEITHER the DB nor the salt exists, the helper should
    silently mint a new salt (this is the fresh-install path; nothing
    to brick)."""
    db_path = tmp_path / "corpus.db.enc"
    # No DB, no salt.
    salt = corpus_crypto.get_or_create_salt(str(db_path))
    assert isinstance(salt, bytes)
    assert len(salt) == corpus_crypto._SALT_BYTES, (
        f"Fresh-install path must mint a {corpus_crypto._SALT_BYTES}-byte "
        f"salt; got {len(salt)} bytes."
    )


def test_round71_missing_salt_branch_carries_round71_marker() -> None:
    """The new branch MUST carry a ``Round 71 / Phase 2 (#13)`` marker
    so the audit grep finds it."""
    src = (Path(__file__).resolve().parent.parent / "corpus_crypto.py").read_text(encoding="utf-8")
    assert "Round 71 / Phase 2 (#13)" in src, (
        "corpus_crypto.get_or_create_salt must carry a "
        "``Round 71 / Phase 2 (#13)`` marker comment near the missing-salt "
        "fail-loud branch."
    )
