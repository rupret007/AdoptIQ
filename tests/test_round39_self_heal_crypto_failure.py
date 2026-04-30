"""Round 39 / corpus crypto self-heal -- pin the probe-and-recover
contract added to ``_install_baked_corpus_if_present``.

Background
----------

Before Round 39, the install path was strictly one-shot: if the user's
``corpus.db.enc`` already existed on disk we returned ``None`` and the
bootstrap then tried to ``open_corpus_for_user`` against it.  That
worked for the happy upgrade path (same sentinel/salt across builds)
but bricked every install where a previous build's bake-time sentinel
no longer matched the current build (every build mints a fresh
sentinel when no stable one is found).  The user-visible symptom was
"Last run failed (crypto) -- authentication tag mismatch" with no UI
escape hatch.

Round 39 changes the contract: when the user already has a
``corpus.db.enc`` we PROBE-DECRYPT it first.  A healthy corpus is
left alone (existing happy path); a broken corpus is preserved
aside as ``<name>.broken-<utc>`` (single rolling backup so disk
usage is bounded) and the bake snapshot is reinstalled in place so
the user sees a working install on the next status poll.

These tests pin every branch of the new probe-and-recover flow:

* probe succeeds          -> install no-op (healthy idempotency)
* probe fails AND bake    -> self-heal (preserve + reinstall)
* probe fails AND no bake -> no-op, leave the broken corpus in
                              place so the UI can surface the error
                              and the user can reach for the new
                              Reset Corpus button
* repeated self-heal      -> single rolling backup (cap=1 to prevent
                              280-MB sidecars accumulating on every
                              boot)
* partial preserve fails  -> roll back so we never end up in a
                              half-renamed state
* non-crypto exception    -> bubble (we MUST NOT silently overwrite
                              a user's healthy corpus on a
                              transient OS error)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import corpus_bootstrap
from corpus_crypto import CorpusCryptoError


# Round 53 / Phase 53.3: the BAKE bundle ships 2 files (encrypted DB
# + salt; the sentinel + lock are resolved at runtime against the
# OneDrive sync).  The USER DIR may still carry the legacy 4-tuple
# from a pre-Round-53 install -- that's the "upgrade-handoff" path
# the Round 39 self-heal was designed for, and Round 53 keeps that
# preserve-and-rotate logic intact via ``_LEGACY_BAKED_CORPUS_FILES``.
_FAKE_BAKE_FILES = (
    "corpus.db.enc",
    "corpus.db.salt",
)
_FAKE_USER_FILES = (
    "corpus.db.enc",
    "sentinel.json",
    "corpus.db.salt",
    "corpus.sentinel.lock.json",
)


def _seed_bake_dir(bake_dir: Path) -> None:
    """Populate the bake_dir with the Round 53 ship-set (encrypted DB
    + salt).  We only need cheap fake bytes -- the install path uses
    copyfile and the probe is monkeypatched so the real crypto stack
    is not exercised here."""
    bake_dir.mkdir(parents=True, exist_ok=True)
    for fname in _FAKE_BAKE_FILES:
        (bake_dir / fname).write_bytes(
            f"adoptiq-bake-fixture-{fname}".encode("utf-8")
        )


def _seed_user_dir(user_dir: Path) -> None:
    """Drop the legacy 4-tuple in the user dir so the install path's
    ``user_db.exists()`` branch fires and probe-decrypt is consulted.
    Mirrors a pre-Round-53 install being upgraded by a Round 53 build."""
    user_dir.mkdir(parents=True, exist_ok=True)
    for fname in _FAKE_USER_FILES:
        (user_dir / fname).write_bytes(
            f"adoptiq-user-fixture-{fname}".encode("utf-8")
        )


@pytest.fixture(autouse=True)
def _isolate_corpus_dirs(tmp_path, monkeypatch):
    """Point the bootstrap module at tmp dirs so we never touch the
    real user's knowledge directory."""
    fake_user_dir = tmp_path / "user_corpus"
    monkeypatch.setattr(
        corpus_bootstrap, "_user_corpus_dir", lambda: fake_user_dir,
    )
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


# ---------------------------------------------------------------------------
# Happy path: probe succeeds -> install no-op
# ---------------------------------------------------------------------------


def test_install_baked_skips_when_existing_corpus_decrypts(
    tmp_path, monkeypatch,
):
    """Round 35 idempotency contract preserved -- a HEALTHY existing
    corpus is left untouched even when a fresh bake is bundled."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    user_db = user_dir / "corpus.db.enc"
    user_bytes_before = user_db.read_bytes()
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: True,
    )

    result = corpus_bootstrap._install_baked_corpus_if_present()

    assert result is None, "healthy probe must short-circuit install"
    assert user_db.read_bytes() == user_bytes_before, (
        "healthy user corpus must NOT be overwritten by a fresh bake"
    )


# ---------------------------------------------------------------------------
# Self-heal path: probe fails + bake present -> preserve + reinstall
# ---------------------------------------------------------------------------


def test_install_baked_self_heals_when_existing_corpus_invalid_tag(
    tmp_path, monkeypatch,
):
    """The canonical Round 39 fix: a broken existing corpus is moved
    aside as ``<name>.broken-<ts>`` and the bake is reinstalled."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    result = corpus_bootstrap._install_baked_corpus_if_present()

    assert result is not None, (
        "self-heal must report a bake timestamp on success"
    )

    broken_files = sorted(
        p.name for p in user_dir.iterdir() if ".broken-" in p.name
    )
    # Round 53: the preserve loop iterates _LEGACY_BAKED_CORPUS_FILES
    # (the legacy 4-tuple) so all four pre-Round-53 user artifacts
    # (db.enc + sentinel + salt + lock) are rotated together -- this
    # is the upgrade-handoff path the self-heal was built for.
    assert len(broken_files) == 4, (
        "all four legacy user artifacts must be preserved as .broken-<ts>"
    )
    suffix = broken_files[0].split(".broken-", 1)[1]
    expected_brokens = {f"{f}.broken-{suffix}" for f in _FAKE_USER_FILES}
    assert set(broken_files) == expected_brokens, (
        "all four .broken-<ts> sidecars must share the same suffix"
    )

    # The reinstall loop only copies the Round 53 ship-set
    # (_BAKED_CORPUS_FILES = 2 files); the runtime re-mints
    # sentinel.json / corpus.sentinel.lock.json against the user's
    # OneDrive on the next open.
    for fname in _FAKE_BAKE_FILES:
        bake_bytes = (bake_dir / fname).read_bytes()
        user_bytes = (user_dir / fname).read_bytes()
        assert user_bytes == bake_bytes, (
            f"{fname} must reflect the bake bytes after self-heal"
        )
    # Defense: the legacy sidecars must NOT be re-installed from
    # the bake (Round 53 contract -- they are not in the bundle).
    for legacy in ("sentinel.json", "corpus.sentinel.lock.json"):
        if (bake_dir / legacy).exists():
            continue  # Round 39 fixture compatibility -- skip
        # The user dir's legacy sidecars were rotated to .broken-*
        # and not re-installed.  Their existence in user_dir would
        # imply a contract drift.
        assert not (user_dir / legacy).exists(), (
            f"Round 53 contract violated: {legacy} reappeared in "
            f"user_dir after self-heal -- the bake must not ship it."
        )


def test_install_baked_no_bake_dir_does_not_touch_broken_corpus(
    tmp_path, monkeypatch,
):
    """Self-heal can only run when a bake is bundled.  Without one we
    must leave the broken corpus in place so the existing
    CorpusCryptoError surfaces in the UI; the user will then click
    the new Reset Corpus button to fall back manually."""
    monkeypatch.delenv("ADOPTIQ_BAKED_CORPUS_DIR", raising=False)
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    fake_module_path = tmp_path / "corpus_bootstrap_isolated.py"
    fake_module_path.write_text("# isolated for test", encoding="utf-8")
    monkeypatch.setattr(corpus_bootstrap, "__file__", str(fake_module_path))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    user_db = user_dir / "corpus.db.enc"
    bytes_before = user_db.read_bytes()
    # Should not even be called -- bake_dir is None so we return early.
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    result = corpus_bootstrap._install_baked_corpus_if_present()

    assert result is None, (
        "no bake -> install must report None even with a broken corpus"
    )
    assert user_db.read_bytes() == bytes_before, (
        "no bake -> broken corpus must remain untouched on disk"
    )
    broken_files = [p for p in user_dir.iterdir() if ".broken-" in p.name]
    assert broken_files == [], (
        "no bake -> we must not preserve aside (we cannot recover from "
        "the broken state, so destroying the user's only copy of the "
        "encrypted material would be worse than leaving it alone)"
    )


def test_install_baked_self_heal_caps_broken_backups_at_one(
    tmp_path, monkeypatch,
):
    """Repeated self-heal cycles must NOT accumulate ``.broken-*``
    sidecars; each new cycle prunes the previous one before writing
    its own (single rolling backup, ~280 MB cap)."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    first = corpus_bootstrap._install_baked_corpus_if_present()
    assert first is not None
    first_brokens = sorted(
        p.name for p in user_dir.iterdir() if ".broken-" in p.name
    )
    first_suffix = first_brokens[0].split(".broken-", 1)[1]

    # Force a second self-heal by re-seeding the probe to return False
    # (the install just happened so the user_db exists; the second
    # call hits the probe again).  The bake dir is shared so the
    # bake content is identical to what we just wrote -- so we
    # mutate the user_db to ensure the 2nd probe-fail branch fires
    # and the new .broken-<ts> uses a different suffix.
    (user_dir / "corpus.db.enc").write_bytes(b"second-cycle-broken")

    # Sleep just enough so the second timestamp suffix differs at
    # second resolution from the first (the suffix is %Y%m%dT%H%M%SZ).
    import time as _time
    _time.sleep(1.1)

    second = corpus_bootstrap._install_baked_corpus_if_present()
    assert second is not None

    second_brokens = sorted(
        p.name for p in user_dir.iterdir() if ".broken-" in p.name
    )
    second_suffix = second_brokens[0].split(".broken-", 1)[1]
    assert second_suffix != first_suffix, (
        "a second self-heal cycle must use a different timestamp"
    )
    # Round 53: the SECOND self-heal cycle starts from a user_dir
    # that has the bake's 2-file install (db.enc + salt) PLUS the
    # runtime-minted sentinel.json + corpus.sentinel.lock.json that
    # ``open_corpus_for_user`` writes after the first install -- so
    # the legacy 4-tuple's worth of files exists when the second
    # preserve runs.  In our fixture the runtime open never ran, so
    # the second cycle preserves only the 2 bake files (no sentinel
    # nor lock to rotate).  This is fine -- the cap=1 contract is
    # about NOT accumulating multiple suffix sets, not the file count.
    # ``second_brokens`` size is therefore 2 in the test fixture
    # (real install would be 4 because the runtime mints the sidecars).
    expected_second_count = 2
    assert len(second_brokens) == expected_second_count, (
        f"only ONE rolling .broken-<ts> set must exist on disk; "
        f"expected {expected_second_count} files (Round 53 second-cycle "
        f"shape -- the first cycle's preserve already rotated the "
        f"legacy sidecars; the second cycle only sees the 2-file bake "
        f"install), got {len(second_brokens)} files: {second_brokens}"
    )
    for fname in second_brokens:
        assert fname.endswith(f".broken-{second_suffix}"), (
            "all .broken-<ts> files must be from the latest cycle "
            "(prior cycle was supposed to be pruned)"
        )


def test_install_baked_self_heal_logs_round39_event(
    tmp_path, monkeypatch, caplog,
):
    """Forensic logging contract: the self-heal must emit a single
    structured warning line with the keys ``event``,
    ``broken_suffix``, and ``bake_dir`` so support runbooks can grep
    for the event class."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    with caplog.at_level(logging.WARNING, logger="corpus_bootstrap"):
        corpus_bootstrap._install_baked_corpus_if_present()

    self_heal_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "event=corpus_self_heal_invalidtag" in rec.getMessage()
    ]
    assert len(self_heal_lines) == 1, (
        f"expected exactly one self-heal log line, got {self_heal_lines}"
    )
    line = self_heal_lines[0]
    assert "broken_suffix=" in line, "log must include broken_suffix"
    assert "bake_dir=" in line, "log must include bake_dir"


def test_install_baked_healthy_existing_corpus_marks_state_baked(
    tmp_path, monkeypatch,
):
    """Round 39 UX: when the existing user corpus probe-decrypts
    cleanly, ``_STATE.source`` MUST be ``"baked"`` (not falling
    through to ``"fresh"``).  Without this fix the analyze panel
    would label a healthy upgraded install as
    "Indexing OneDrive..." forever -- the fresh-source label is for
    install paths that have never seen a baked corpus, and a healthy
    user corpus did originally come from SOME bake (just an earlier
    build's)."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    # Seed a lock with a minted_at value so the test also asserts
    # indexed_at is plumbed through.
    (user_dir / "corpus.sentinel.lock.json").write_bytes(
        b'{"minted_at": "2026-04-01T00:00:00Z", "version": 1}'
    )
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: True,
    )

    result = corpus_bootstrap._install_baked_corpus_if_present()

    assert result is None, (
        "healthy probe must short-circuit install (no bake copy)"
    )
    state = corpus_bootstrap.get_state()
    assert state.source == "baked", (
        f"healthy existing corpus must be labeled source='baked'; "
        f"got {state.source!r} (a 'fresh' label here would mislabel "
        f"every upgrade install as 'Indexing OneDrive...')"
    )
    assert state.indexed_at == "2026-04-01T00:00:00Z", (
        "indexed_at must be populated from the user's lock minted_at "
        "so the panel can show the bake provenance"
    )


def test_install_baked_self_heal_state_source_is_self_healed_baked(
    tmp_path, monkeypatch,
):
    """After self-heal, ``CorpusBootState.source`` must read
    ``"self_healed_baked"`` so the analyze-page panel can label the
    state distinctly from a clean baked install."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    indexed_at = corpus_bootstrap._install_baked_corpus_if_present()

    assert indexed_at is not None
    state = corpus_bootstrap.get_state()
    assert state.source == "self_healed_baked", (
        f"self-heal must flag source as self_healed_baked; got "
        f"{state.source!r}"
    )
    assert state.indexed_at == indexed_at, (
        "self-heal must also stamp indexed_at on the state singleton"
    )


def test_install_baked_self_heal_atomic_partial_failure(
    tmp_path, monkeypatch,
):
    """If a rename mid-preserve fails (e.g., another process holding a
    file open on Windows; permission denied transient on macOS), we
    must roll back the prior renames so we never end up in a
    half-renamed state.  A half-renamed state would confuse the next
    boot into thinking the corpus is gone, when it is just split
    between original and .broken-<ts> filenames."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    # Wrap os.replace so the third call fails.  Renames within
    # _preserve_broken_corpus iterate _LEGACY_BAKED_CORPUS_FILES;
    # failing the third (corpus.db.salt) means corpus.db.enc and
    # sentinel.json are already moved aside, lock has not yet been
    # touched.
    import os as _os
    real_replace = _os.replace
    counter = {"calls": 0}

    def fake_replace(src, dest):
        counter["calls"] += 1
        if counter["calls"] == 3:
            raise OSError("simulated rename failure")
        return real_replace(src, dest)

    monkeypatch.setattr(corpus_bootstrap.os, "replace", fake_replace)

    result = corpus_bootstrap._install_baked_corpus_if_present()

    # The preserve step bailed -> install must short-circuit (we
    # cannot safely overwrite a broken-but-not-preserved corpus).
    assert result is None, (
        "preserve failure must short-circuit install rather than "
        "overwriting a corpus that was not preserved aside"
    )

    # Roll-back contract: the four legacy original files must still
    # be in their original positions (no half-renamed state).
    for fname in _FAKE_USER_FILES:
        assert (user_dir / fname).exists(), (
            f"{fname} missing from user_dir after rollback -- preserve "
            "left a half-renamed state"
        )
    broken_files = [p for p in user_dir.iterdir() if ".broken-" in p.name]
    assert broken_files == [], (
        "preserve rollback must remove any successful renames so we "
        "do not leave .broken-* files paired with originals"
    )


def test_install_baked_skips_for_legitimate_user_delta(
    tmp_path, monkeypatch,
):
    """Round 35 contract regression: a healthy user corpus with newer
    content than the bake must NEVER be silently overwritten -- the
    user's daily-refresh delta is more valuable than a stale bake
    snapshot."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    user_db = user_dir / "corpus.db.enc"
    user_db.write_bytes(b"healthy-user-corpus-with-newer-delta")
    bytes_before = user_db.read_bytes()
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: True,
    )

    result = corpus_bootstrap._install_baked_corpus_if_present()

    assert result is None
    assert user_db.read_bytes() == bytes_before, (
        "healthy probe must keep user bytes intact even if bake is fresh"
    )


def test_install_baked_uncaught_exception_propagates(
    tmp_path, monkeypatch,
):
    """We only swallow CorpusCryptoError in the probe.  Any other
    exception (PermissionError, MemoryError, KeyboardInterrupt, ...)
    must bubble so we never silently overwrite a user's corpus on a
    transient OS-level failure."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)

    def boom(_user_db):
        raise PermissionError("EACCES on knowledge dir")

    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts", boom,
    )

    with pytest.raises(PermissionError):
        corpus_bootstrap._install_baked_corpus_if_present()


def test_install_baked_self_heal_handles_missing_lock(
    tmp_path, monkeypatch,
):
    """A broken install where the lock file is also missing/malformed
    must still self-heal cleanly -- the preserve step must not
    require the lock to exist (it would already be missing in some
    legacy installs predating Round 34)."""
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    user_dir = corpus_bootstrap._user_corpus_dir()
    _seed_user_dir(user_dir)
    # Delete the lock file from the user dir so the preserve step
    # is exercised against a 3-of-4 install state.
    (user_dir / "corpus.sentinel.lock.json").unlink()
    monkeypatch.setattr(
        corpus_bootstrap, "_probe_existing_corpus_decrypts",
        lambda _user_db: False,
    )

    result = corpus_bootstrap._install_baked_corpus_if_present()

    assert result is not None, (
        "self-heal must succeed even when the prior lock is missing"
    )
    # Three .broken-<ts> sidecars (no lock to preserve) is the
    # expected count.
    broken_files = [p for p in user_dir.iterdir() if ".broken-" in p.name]
    assert len(broken_files) == 3, (
        f"expected 3 .broken-<ts> files (no prior lock to preserve); "
        f"got {len(broken_files)}: {[p.name for p in broken_files]}"
    )
    # All four bake files must now exist in the user dir.
    for fname in _FAKE_BAKE_FILES:
        assert (user_dir / fname).exists(), (
            f"{fname} missing from user_dir after self-heal"
        )


# ---------------------------------------------------------------------------
# End-to-end probe-decrypt: real crypto round-trip
# ---------------------------------------------------------------------------


def _seed_canonical_onedrive_sentinel(root: Path) -> None:
    """Round 53 helper: drop the canonical sentinel into a tmp
    OneDrive root so probe-decrypt can succeed under
    ``allow_local_sentinel=False``."""
    import secrets as _secrets
    from corpus_crypto import DEFAULT_SENTINEL_NAME
    root.mkdir(parents=True, exist_ok=True)
    (root / DEFAULT_SENTINEL_NAME).write_bytes(_secrets.token_bytes(32))


def test_probe_existing_corpus_decrypts_returns_true_for_healthy_corpus(
    tmp_path, monkeypatch,
):
    """End-to-end sanity check: against a real encrypted corpus that
    we just wrote with ``open_corpus_for_user``, the probe MUST
    return True.  A regression that returns False here would
    silently self-heal every healthy install on every cold boot.

    Round 53: the probe now passes ``allow_local_sentinel=False``,
    so the corpus must be created against a real OneDrive sentinel
    too -- otherwise the probe finds no matching candidate and
    returns False, which would mask the contract.
    """
    import config as _live_config
    onedrive_root = tmp_path / "onedrive_root"
    _seed_canonical_onedrive_sentinel(onedrive_root)
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    from corpus_crypto import open_corpus_for_user as _open
    with _open(
        onedrive_root=onedrive_root,
        encrypted_path=enc,
        create_if_missing=True,
        allow_local_sentinel=False,
    ) as handle:
        handle.conn.execute(
            'CREATE TABLE notes ("id" INTEGER PRIMARY KEY, "body" TEXT);'
        )
        handle.conn.execute(
            'INSERT INTO notes ("body") VALUES (?)', ("synthetic",),
        )
        handle.conn.commit()

    assert corpus_bootstrap._probe_existing_corpus_decrypts(enc) is True


def test_probe_existing_corpus_decrypts_returns_false_for_corrupt_blob(
    tmp_path, monkeypatch,
):
    """End-to-end sanity check: against a real encrypted corpus whose
    .enc has been mutated by a single byte (auth-tag invalidation),
    the probe MUST return False.  A regression that returns True
    here would mask the canonical upgrade-handoff bug Round 39 was
    built to fix."""
    import config as _live_config
    onedrive_root = tmp_path / "onedrive_root"
    _seed_canonical_onedrive_sentinel(onedrive_root)
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    from corpus_crypto import open_corpus_for_user as _open
    with _open(
        onedrive_root=onedrive_root,
        encrypted_path=enc,
        create_if_missing=True,
        allow_local_sentinel=False,
    ) as handle:
        handle.conn.execute(
            'CREATE TABLE notes ("id" INTEGER PRIMARY KEY, "body" TEXT);'
        )
        handle.conn.commit()

    # Flip the last byte to corrupt the GCM auth tag.
    raw = bytearray(enc.read_bytes())
    raw[-1] ^= 0xFF
    enc.write_bytes(bytes(raw))

    assert corpus_bootstrap._probe_existing_corpus_decrypts(enc) is False


def test_probe_returns_false_when_onedrive_sentinel_absent(
    tmp_path, monkeypatch,
):
    """Round 53 / Phase 53.3: a corpus created under a OneDrive
    sentinel and later probed with that OneDrive root NOT configured
    must return False -- this is the canonical "user upgraded build
    but their OneDrive is offline" path that the new
    ``blocked_no_onedrive`` UI state surfaces."""
    import config as _live_config
    onedrive_root = tmp_path / "onedrive_root"
    _seed_canonical_onedrive_sentinel(onedrive_root)
    enc = tmp_path / "knowledge" / "corpus.db.enc"
    from corpus_crypto import open_corpus_for_user as _open
    with _open(
        onedrive_root=onedrive_root,
        encrypted_path=enc,
        create_if_missing=True,
        allow_local_sentinel=False,
    ) as handle:
        handle.conn.execute(
            'CREATE TABLE notes ("id" INTEGER PRIMARY KEY, "body" TEXT);'
        )
        handle.conn.commit()
    # Now wipe the OneDrive sentinel root from Config so the probe
    # has no way to find the sentinel.
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    # Round 53 contract: with no OneDrive sentinel reachable AND
    # allow_local_sentinel=False, the probe MUST return False so
    # the bootstrap surfaces blocked_no_onedrive.
    assert corpus_bootstrap._probe_existing_corpus_decrypts(enc) is False


def test_probe_handles_completely_missing_user_db(
    tmp_path, monkeypatch,
):
    """``create_if_missing=False`` ensures the probe never accidentally
    auto-mints a fresh corpus when the user dir is empty.  This is a
    safety property: the install path's own ``user_db.exists()``
    branch already filters out missing files, but if a future
    refactor accidentally calls the probe on a missing path we must
    surface that as 'broken' rather than 'auto-create then claim
    healthy'."""
    import config as _live_config
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    missing = tmp_path / "knowledge" / "no_corpus_here.db.enc"
    # Probe must return False (CorpusCryptoError "not found") rather
    # than minting a fresh empty corpus.
    assert corpus_bootstrap._probe_existing_corpus_decrypts(missing) is False
    assert not missing.exists(), (
        "probe must NOT create the missing corpus as a side effect"
    )


# ---------------------------------------------------------------------------
# CorpusCryptoError import sanity (defends against an accidental rename)
# ---------------------------------------------------------------------------


def test_corpus_crypto_error_is_imported_in_bootstrap():
    """The probe relies on catching ``CorpusCryptoError``.  If that
    import disappears or gets aliased the probe collapses to
    bare-except + silent self-heal on every transient OS error,
    which would silently overwrite healthy corpora."""
    import corpus_bootstrap as _cb
    assert getattr(_cb, "CorpusCryptoError", None) is CorpusCryptoError
