"""Round 96 / Build 69: legacy bundle-upgrade reinstall path retired.

Round 54 pinned the Build30 -> Build31 recovery path where a broken
local corpus could be replaced from a bundled bake. With no corpus data
in the app bundle, the safe upgrade behavior is different: preserve
legacy local artifacts only on explicit reset, and never overwrite them
from a local baked snapshot.
"""

from __future__ import annotations

from pathlib import Path

import corpus_bootstrap


def test_build30_style_local_artifacts_are_not_replaced_from_bake(
    tmp_path, monkeypatch,
) -> None:
    user_dir = tmp_path / "user"
    bake_dir = tmp_path / "bake"
    user_dir.mkdir()
    bake_dir.mkdir()

    (user_dir / "corpus.db.enc").write_bytes(b"build30-user-db")
    (user_dir / "corpus.db.salt").write_bytes(b"build30-user-salt")
    (user_dir / "sentinel.json").write_bytes(b"build30-user-sentinel")
    (user_dir / "corpus.sentinel.lock.json").write_bytes(b"build30-user-lock")
    (bake_dir / "corpus.db.enc").write_bytes(b"new-bundled-db")
    (bake_dir / "corpus.db.salt").write_bytes(b"new-bundled-salt")

    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()
    try:
        assert corpus_bootstrap._install_baked_corpus_if_present() is None
        assert (user_dir / "corpus.db.enc").read_bytes() == b"build30-user-db"
        assert (user_dir / "corpus.db.salt").read_bytes() == b"build30-user-salt"
        assert (user_dir / "sentinel.json").read_bytes() == b"build30-user-sentinel"
        assert corpus_bootstrap.get_state().source is None
    finally:
        corpus_bootstrap.reset_for_tests()


def test_runtime_only_upgrade_contract_is_documented() -> None:
    src = Path(corpus_bootstrap.__file__).read_text(encoding="utf-8")
    assert "Round 96 / runtime-only corpus" in src
    assert "authorized local OneDrive mirror" in src
