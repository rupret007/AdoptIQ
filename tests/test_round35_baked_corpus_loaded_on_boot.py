"""Round 35 / native-corpus: pin the boot-time baked-corpus install.

When AdoptIQ ships, the .app bundle carries an encrypted corpus +
sentinel + salt + lock under ``Resources/baked_corpus/``.  On the
first launch the ``corpus_bootstrap`` module must:

* Detect the bundled snapshot via ``_baked_corpus_dir()`` (PyInstaller
  ``sys._MEIPASS`` path or the dev ``ADOPTIQ_BAKED_CORPUS_DIR``
  override the test suite uses to avoid monkey-patching ``_MEIPASS``).
* Copy all four artifacts atomically into the user's writable
  knowledge dir with mode ``0600``.
* Set ``CorpusBootState.source = "baked"`` and
  ``CorpusBootState.indexed_at`` to the bake mtime so the analyze-
  page panel can render "Indexed at build time".
* Be idempotent on subsequent launches (existing user_db is left
  alone -- the daily refresh worker keeps it current).
* Roll back cleanly when the bake set is incomplete (missing any
  of the four files), so a half-shipped snapshot cannot corrupt
  the install.

These tests use ``ADOPTIQ_BAKED_CORPUS_DIR`` + a tmp ``CORPUS_DIR``
override so the install never touches the real user's
``~/Library/Application Support/AdoptIQ/knowledge/``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import corpus_bootstrap


_FAKE_BAKE_FILES = (
    "corpus.db.enc",
    "sentinel.json",
    "corpus.db.salt",
    "corpus.sentinel.lock.json",
)


def _seed_bake_dir(bake_dir: Path) -> None:
    """Drop placeholder bytes for each of the four artifacts.  The
    install path uses ``shutil.copyfile`` -- it never reads the
    contents -- so cheap fake bytes are sufficient to exercise the
    detect-and-copy logic."""
    bake_dir.mkdir(parents=True, exist_ok=True)
    for fname in _FAKE_BAKE_FILES:
        (bake_dir / fname).write_bytes(
            f"adoptiq-bake-fixture-{fname}".encode("utf-8")
        )


@pytest.fixture(autouse=True)
def _isolate_corpus_dirs(tmp_path, monkeypatch):
    """Point the bootstrap module at tmp dirs so we never touch the
    real user's knowledge/corpus directories.  Cleared between tests."""
    fake_user_dir = tmp_path / "user_corpus"
    monkeypatch.setattr(
        corpus_bootstrap, "_user_corpus_dir", lambda: fake_user_dir,
    )
    # Clean any pollution from prior test invocations.
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


def test_baked_corpus_dir_resolves_via_env_override(tmp_path, monkeypatch):
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    resolved = corpus_bootstrap._baked_corpus_dir()
    assert resolved == bake_dir, (
        f"ADOPTIQ_BAKED_CORPUS_DIR should win: got {resolved!r}"
    )


def test_baked_corpus_dir_returns_none_when_no_bake_present(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("ADOPTIQ_BAKED_CORPUS_DIR", raising=False)
    # Override repo bake dir to a non-existent path.
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    # Point at an empty dir via the env override path (which only
    # accepts dirs whose corpus.db.enc exists).
    empty = tmp_path / "empty_baked"
    empty.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(empty))
    assert corpus_bootstrap._baked_corpus_dir() is None


def test_baked_corpus_dir_ignores_env_dir_without_corpus_db_enc(
    tmp_path, monkeypatch,
):
    bake_dir = tmp_path / "incomplete"
    bake_dir.mkdir(parents=True)
    (bake_dir / "sentinel.json").write_bytes(b"x")
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    # No corpus.db.enc -> resolution should fall through to None
    # (assuming no real meipass / repo bake on the test host).
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    # Defense: if a stray bake dir lives in the repo, we still want
    # the assertion to be meaningful.  Allow the result to be the
    # repo dir, but never the incomplete env override.
    resolved = corpus_bootstrap._baked_corpus_dir()
    assert resolved != bake_dir


def test_install_baked_corpus_copies_all_four_files(tmp_path, monkeypatch):
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    indexed_at = corpus_bootstrap._install_baked_corpus_if_present()
    assert indexed_at is not None, (
        "install must report a bake timestamp on success"
    )

    user_dir = corpus_bootstrap._user_corpus_dir()
    for fname in _FAKE_BAKE_FILES:
        target = user_dir / fname
        assert target.exists(), f"{fname} not copied into user dir"
        # Round 35 contract: each artifact lands at 0o600 mode.
        import stat as _stat
        mode = _stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, (
            f"{fname} mode {oct(mode)} != 0o600 -- multi-user "
            "macOS hosts would leak corpus material to other accounts."
        )


def test_install_baked_corpus_is_idempotent(tmp_path, monkeypatch):
    bake_dir = tmp_path / "baked"
    _seed_bake_dir(bake_dir)
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    first = corpus_bootstrap._install_baked_corpus_if_present()
    assert first is not None

    user_dir = corpus_bootstrap._user_corpus_dir()
    # User edits / refreshes their own corpus -> we MUST NOT clobber
    # it on the second launch.
    user_db = user_dir / "corpus.db.enc"
    user_db.write_bytes(b"user-refreshed-content")

    second = corpus_bootstrap._install_baked_corpus_if_present()
    assert second is None, (
        "install must NO-OP when the user already has a corpus.db.enc; "
        "the daily refresh worker is responsible for keeping it current."
    )
    assert user_db.read_bytes() == b"user-refreshed-content", (
        "second install must not overwrite the user's existing corpus"
    )


def test_install_baked_corpus_rolls_back_on_incomplete_bake(
    tmp_path, monkeypatch,
):
    """If any of the four artifacts is missing from the bake dir,
    the install must abort and roll back any partially-copied files
    so the legacy fresh-mint path can take over cleanly."""
    bake_dir = tmp_path / "baked"
    bake_dir.mkdir()
    # Only stage two of the four artifacts.
    (bake_dir / "corpus.db.enc").write_bytes(b"db")
    (bake_dir / "sentinel.json").write_bytes(b"sent")
    # salt.bin and lock are missing.
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    result = corpus_bootstrap._install_baked_corpus_if_present()
    assert result is None, (
        "incomplete bake must yield None so the bootstrap falls back "
        "to fresh-mint"
    )
    user_dir = corpus_bootstrap._user_corpus_dir()
    # Roll-back: no artifact should remain in the user dir.
    for fname in _FAKE_BAKE_FILES:
        assert not (user_dir / fname).exists(), (
            f"incomplete bake left a stale {fname} in the user dir"
        )


def test_install_returns_none_when_no_baked_corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("ADOPTIQ_BAKED_CORPUS_DIR", raising=False)
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    # Point env at an empty dir so the resolver hits a no-bake path
    # (env override gate requires corpus.db.enc to exist).
    empty = tmp_path / "no_baked"
    empty.mkdir()
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(empty))

    result = corpus_bootstrap._install_baked_corpus_if_present()
    assert result is None
