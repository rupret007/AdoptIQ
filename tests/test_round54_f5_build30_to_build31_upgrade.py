"""Round 54 / F5 -- end-to-end Build30 (4-file) -> Build31 (2-file)
upgrade regression test using REAL crypto (no probe mocks).

Background
----------
Round 39 introduced ``_install_baked_corpus_if_present`` self-heal
(probe + preserve + reinstall) and Round 35 added the bake/install
pipeline.  Round 53 / Phase 53.3 then changed the bake bundle from
4 files to 2 files (the sentinel + lock are no longer shipped) and
flipped the runtime open path to ``allow_local_sentinel=False`` so
the corpus is sealed against the canonical OneDrive sentinel.

The Round 54 review noted that ``_LEGACY_BAKED_CORPUS_FILES`` was
exercised INDIRECTLY by ``test_round39_self_heal_crypto_failure.py``
(via mocked probes), but no test pinned the FULL Build30 -> Build31
upgrade path with REAL encryption / decryption end-to-end.  That
path is the most operationally sensitive one in the lifecycle:
every shipping user transitions through it exactly once and a
silent regression would brick installs in the field.

This test does the full real-crypto round-trip:

1. Mint a canonical "Build31" OneDrive sentinel into a fake synced
   OneDrive root.
2. Build a Build31 bake_dir (2 files) by encrypting a real plaintext
   corpus against the OneDrive sentinel, then scrub the auto-written
   sentinel + lock sidecars (mirroring the post-bake scrub in
   ``scripts/bake_corpus.py``).
3. Build a Build30 user_dir (4 files) by encrypting a DIFFERENT
   plaintext corpus against a DIFFERENT local sentinel (mirroring
   what a Build30 install actually has on disk: a local
   ``sentinel.json`` + lock that pins it).
4. Run ``_install_baked_corpus_if_present()`` against the configured
   OneDrive root.
5. Assert:
   * The Build30 4-file user corpus is preserved as
     ``*.broken-<utc_iso>`` sidecars (all 4 legacy artifacts rotated,
     not just the .enc).
   * The Build31 2-file corpus is installed in user_dir.
   * No ``sentinel.json`` or ``corpus.sentinel.lock.json`` was
     copied INTO user_dir from the bake bundle (the bake doesn't
     ship them; this proves the install loop honors that).
   * Subsequent ``open_corpus_for_user(allow_local_sentinel=False,
     onedrive_root=<canonical>)`` against the freshly-installed
     user corpus succeeds AND reads back the Build31 plaintext
     payload (proves the OneDrive sentinel actually decrypts the
     installed corpus).
   * The Build30 broken sidecars survive the install so a support
     engineer can still recover them for forensics.
"""

# Round 54

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

import corpus_bootstrap
import corpus_crypto
from corpus_crypto import (
    DEFAULT_SENTINEL_NAME,
    open_corpus_for_user,
)


_BUILD30_PLAINTEXT_MARKER = b"build30-user-corpus-payload-marker"
_BUILD31_PLAINTEXT_MARKER = b"build31-baked-corpus-payload-marker"


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Sandbox the module-level state: redirect ``_user_corpus_dir``
    + ``_baked_corpus_dir`` at the test tmp tree so we never touch
    the real user's knowledge dir or the real bake bundle."""
    fake_user_dir = tmp_path / "user_dir"
    fake_user_dir.mkdir()

    monkeypatch.setattr(
        corpus_bootstrap, "_user_corpus_dir", lambda: fake_user_dir,
    )

    fake_module_path = tmp_path / "corpus_bootstrap_isolated.py"
    fake_module_path.write_text("# isolated for test", encoding="utf-8")
    monkeypatch.setattr(
        corpus_bootstrap, "__file__", str(fake_module_path),
    )

    # Disable any sys._MEIPASS bake the host might have so
    # ADOPTIQ_BAKED_CORPUS_DIR (set per-test) is the sole source.
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    monkeypatch.delenv("ADOPTIQ_BAKED_CORPUS_DIR", raising=False)

    corpus_bootstrap.reset_for_tests()
    yield (tmp_path, fake_user_dir)
    corpus_bootstrap.reset_for_tests()


def _mint_sentinel_into(root: Path) -> bytes:
    """Mint a 32-byte sentinel into ``root`` and return its bytes
    (so the caller can compare digests)."""
    root.mkdir(parents=True, exist_ok=True)
    # Seed a non-empty file so the sync-status heuristic counts the
    # folder as "synced".
    (root / "synced_doc.docx").write_bytes(b"x" * 32)
    sentinel_bytes = secrets.token_bytes(32)
    (root / DEFAULT_SENTINEL_NAME).write_bytes(sentinel_bytes)
    return sentinel_bytes


def _write_payload(handle, marker: bytes) -> None:
    """Write a known marker into the encrypted corpus so a later
    open can confirm decryption recovered the right bytes."""
    handle.conn.execute(
        "CREATE TABLE IF NOT EXISTS upgrade_marker (k TEXT PRIMARY KEY, v BLOB);"
    )
    handle.conn.execute(
        "INSERT OR REPLACE INTO upgrade_marker (k, v) VALUES (?, ?);",
        ("payload", marker),
    )
    handle.conn.commit()


def _read_payload(handle) -> bytes:
    row = handle.conn.execute(
        "SELECT v FROM upgrade_marker WHERE k = 'payload';"
    ).fetchone()
    return bytes(row[0]) if row else b""


def _build_build31_bake_dir(bake_dir: Path, onedrive_root: Path) -> None:
    """Mirror ``scripts/bake_corpus.py``'s contract: encrypt a
    corpus against the OneDrive sentinel, commit to disk, then
    scrub the auto-written sentinel.json + lock so the bake_dir
    contains exactly 2 files (corpus.db.enc + corpus.db.salt)."""
    bake_dir.mkdir(parents=True, exist_ok=True)
    encrypted = bake_dir / "corpus.db.enc"
    handle = open_corpus_for_user(
        onedrive_root=onedrive_root,
        encrypted_path=encrypted,
        create_if_missing=True,
        allow_local_sentinel=False,
    )
    try:
        _write_payload(handle, _BUILD31_PLAINTEXT_MARKER)
    finally:
        handle.close(persist=True)
    # Post-commit scrub -- the bake script does this exact thing.
    for fname in ("sentinel.json", "corpus.sentinel.lock.json"):
        (bake_dir / fname).unlink(missing_ok=True)
    # Sanity: only the 2 ship-set artifacts remain.
    leftover = sorted(p.name for p in bake_dir.iterdir())
    assert leftover == ["corpus.db.enc", "corpus.db.salt"], (
        f"bake fixture must contain only the 2 Build31 artifacts; "
        f"got {leftover!r}"
    )


def _build_build30_user_dir(user_dir: Path) -> bytes:
    """Mirror a pre-Round-53 install: encrypt a corpus against a
    locally-minted sentinel.  After the open, the user_dir contains
    all 4 legacy artifacts (corpus.db.enc, sentinel.json,
    corpus.db.salt, corpus.sentinel.lock.json).  Returns the local
    sentinel bytes so the caller can confirm the upgrade really
    rotated to a different sentinel."""
    user_dir.mkdir(parents=True, exist_ok=True)
    encrypted = user_dir / "corpus.db.enc"
    handle = open_corpus_for_user(
        onedrive_root=None,
        encrypted_path=encrypted,
        create_if_missing=True,
        allow_local_sentinel=True,
    )
    try:
        _write_payload(handle, _BUILD30_PLAINTEXT_MARKER)
    finally:
        handle.close(persist=True)
    sentinel_path = user_dir / "sentinel.json"
    assert sentinel_path.exists(), (
        "pre-condition: Build30 install must have a local sentinel.json"
    )
    leftover = sorted(p.name for p in user_dir.iterdir())
    expected = sorted([
        "corpus.db.enc", "corpus.db.salt",
        "sentinel.json", "corpus.sentinel.lock.json",
    ])
    assert leftover == expected, (
        f"Build30 fixture must contain all 4 legacy artifacts; got {leftover!r}"
    )
    return sentinel_path.read_bytes()


# ---------------------------------------------------------------------------
# F5 -- end-to-end upgrade with real crypto
# ---------------------------------------------------------------------------


def test_build30_to_build31_full_upgrade_with_real_crypto(
    _isolate_state, monkeypatch,
):
    """Build30 install + Build31 bake + canonical OneDrive sentinel
    -> the install path probes (fails, by design), preserves the
    Build30 4-file corpus aside as ``.broken-<utc>``, installs the
    Build31 2-file corpus in place, AND a subsequent
    ``allow_local_sentinel=False`` open against the OneDrive sentinel
    successfully decrypts the Build31 payload."""
    tmp_path, user_dir = _isolate_state

    onedrive_root = tmp_path / "onedrive_root"
    onedrive_sentinel_bytes = _mint_sentinel_into(onedrive_root)

    # Wire the Config in both the live module and the bootstrap module
    # so probe and gate both see the OneDrive root.
    import config as live_config
    monkeypatch.setattr(
        live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )

    # Build31 bake fixture -- 2 files, sealed against OneDrive sentinel.
    bake_dir = tmp_path / "bake_dir"
    _build_build31_bake_dir(bake_dir, onedrive_root)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    # Build30 user fixture -- 4 files, sealed against a different
    # local sentinel.  The sentinel value MUST differ from the
    # OneDrive one or the probe would falsely succeed.
    build30_local_sentinel_bytes = _build_build30_user_dir(user_dir)
    assert build30_local_sentinel_bytes != onedrive_sentinel_bytes, (
        "test pre-condition broken: local and OneDrive sentinels must differ"
    )

    # Capture the Build30 .enc bytes so we can later confirm the
    # broken sidecar preserved them verbatim.
    build30_enc_bytes = (user_dir / "corpus.db.enc").read_bytes()

    # ACT -- run the install.  Must trigger probe -> preserve -> install.
    indexed_at = corpus_bootstrap._install_baked_corpus_if_present()
    assert indexed_at is not None, (
        "self-heal install must complete and return a bake mtime"
    )

    # ASSERT 1 -- the 4 Build30 artifacts were preserved as broken sidecars.
    children = sorted(p.name for p in user_dir.iterdir())
    broken_enc_names = [n for n in children if n.startswith("corpus.db.enc.broken-")]
    broken_sentinel_names = [n for n in children if n.startswith("sentinel.json.broken-")]
    broken_salt_names = [n for n in children if n.startswith("corpus.db.salt.broken-")]
    broken_lock_names = [n for n in children if n.startswith("corpus.sentinel.lock.json.broken-")]
    assert len(broken_enc_names) == 1, (
        f"Build30 .enc must rotate to exactly one .broken-<utc> sidecar; "
        f"got {broken_enc_names!r}"
    )
    assert len(broken_sentinel_names) == 1, (
        f"Build30 sentinel.json must also rotate (so the broken-set is "
        f"internally consistent for forensics); got {broken_sentinel_names!r}"
    )
    assert len(broken_salt_names) == 1
    assert len(broken_lock_names) == 1
    # All four broken sidecars must share one timestamp suffix.
    suffix = broken_enc_names[0][len("corpus.db.enc.broken-"):]
    assert broken_sentinel_names[0].endswith(suffix)
    assert broken_salt_names[0].endswith(suffix)
    assert broken_lock_names[0].endswith(suffix)

    # ASSERT 2 -- the Build30 .enc bytes were preserved verbatim
    # (this is the forensic recovery contract).
    assert (user_dir / broken_enc_names[0]).read_bytes() == build30_enc_bytes

    # ASSERT 3 -- the freshly-installed user_dir has the 2 Build31
    # artifacts AND no sentinel.json / lock from the bake bundle.
    fresh_files = [
        n for n in children
        if not (
            n.startswith("corpus.db.enc.broken-")
            or n.startswith("sentinel.json.broken-")
            or n.startswith("corpus.db.salt.broken-")
            or n.startswith("corpus.sentinel.lock.json.broken-")
        )
    ]
    assert sorted(fresh_files) == ["corpus.db.enc", "corpus.db.salt"], (
        f"post-install user_dir must contain ONLY the 2 Build31 artifacts "
        f"(no sentinel.json or lock copied from the bake); got {fresh_files!r}"
    )

    # ASSERT 4 -- the Build31 .enc bytes were copied byte-identically
    # from the bake_dir.
    assert (
        (user_dir / "corpus.db.enc").read_bytes()
        == (bake_dir / "corpus.db.enc").read_bytes()
    ), "install must preserve byte-identical copy of the bake .enc"

    # ASSERT 5 -- end-to-end decrypt.  Open the freshly-installed
    # corpus with the OneDrive sentinel + allow_local_sentinel=False,
    # read back the Build31 payload, confirm it's the marker we wrote
    # at bake time.
    handle = open_corpus_for_user(
        onedrive_root=onedrive_root,
        encrypted_path=user_dir / "corpus.db.enc",
        create_if_missing=False,
        allow_local_sentinel=False,
    )
    try:
        recovered = _read_payload(handle)
    finally:
        handle.close(persist=False)
    assert recovered == _BUILD31_PLAINTEXT_MARKER, (
        f"freshly-installed corpus must decrypt against the OneDrive "
        f"sentinel and return the BUILD31 payload; got {recovered!r}"
    )


# ---------------------------------------------------------------------------
# F5 -- contract regressions: install must NOT bring the bake
# sentinel/lock into user_dir even if a stale build accidentally
# bundles them.
# ---------------------------------------------------------------------------


def test_install_loop_only_copies_2_baked_files_even_if_4_present(
    _isolate_state, monkeypatch,
):
    """Defense in depth: a stale build that accidentally bundles the
    legacy 4 files into ``baked_corpus/`` must NOT result in the
    install loop carrying a local sentinel + lock into user_dir.
    The install loop iterates ``_BAKED_CORPUS_FILES`` (2 entries),
    so any extras in the bake_dir are silently ignored.

    Pinned because ``_baked_corpus_dir`` returns a single path; if
    a future refactor changed the iteration set, the offline-decrypt
    HIGH-severity finding would re-open."""
    tmp_path, user_dir = _isolate_state

    onedrive_root = tmp_path / "onedrive_root"
    _mint_sentinel_into(onedrive_root)
    import config as live_config
    monkeypatch.setattr(
        live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )

    bake_dir = tmp_path / "bake_dir"
    _build_build31_bake_dir(bake_dir, onedrive_root)
    # Plant the legacy sidecars in the bake dir to simulate a
    # supply-chain regression where the post-bake scrub did not run.
    (bake_dir / "sentinel.json").write_bytes(b"hostile-sentinel-payload")
    (bake_dir / "corpus.sentinel.lock.json").write_bytes(b'{"hostile": true}')
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    indexed_at = corpus_bootstrap._install_baked_corpus_if_present()
    assert indexed_at is not None

    children = sorted(p.name for p in user_dir.iterdir())
    # The hostile sentinel + lock from the bake must NOT have been
    # copied into the user dir.  The lock will be re-minted when the
    # corpus is opened against the OneDrive sentinel; the sentinel
    # must NEVER appear in user_dir.
    assert "sentinel.json" not in children, (
        "install loop MUST NOT copy a bake sentinel.json into user_dir "
        "(would re-open the offline-decrypt HIGH-severity finding)"
    )
    # The lock SHOULD also not be copied from the bake -- we want
    # the runtime open to mint a fresh lock against the live
    # OneDrive sentinel, not trust whatever the bake shipped.
    assert "corpus.sentinel.lock.json" not in children, (
        "install loop MUST NOT copy a bake corpus.sentinel.lock.json "
        "into user_dir; the lock is minted by open_corpus_for_user at "
        "runtime against the live OneDrive sentinel"
    )


# ---------------------------------------------------------------------------
# F5 -- the bake_dir constant pinning (regressions caught at import time)
# ---------------------------------------------------------------------------


def test_baked_corpus_files_pins_two_artifacts():
    """Pin the Round 53 contract: ``_BAKED_CORPUS_FILES`` MUST list
    exactly the 2 ship-set artifacts.  A regression here is the
    HIGH-severity offline-decrypt finding."""
    assert corpus_bootstrap._BAKED_CORPUS_FILES == (
        "corpus.db.enc", "corpus.db.salt",
    )


def test_legacy_baked_corpus_files_pins_four_artifacts():
    """``_LEGACY_BAKED_CORPUS_FILES`` MUST retain the 4-tuple so the
    Round 39 self-heal preserve loop continues rotating Build30
    sentinels + locks into broken sidecars on upgrade."""
    assert corpus_bootstrap._LEGACY_BAKED_CORPUS_FILES == (
        "corpus.db.enc", "sentinel.json",
        "corpus.db.salt", "corpus.sentinel.lock.json",
    )
