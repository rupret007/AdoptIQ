"""Round 107 / Build 76: bundled corpus can self-heal old local artifacts.

Build 76 restores the bundled bake as the first-launch source of truth.
If a legacy local corpus cannot decrypt, the install helper preserves it
and replaces it with the prebaked snapshot.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import corpus_bootstrap


def test_build30_style_local_artifacts_are_preserved_then_replaced_from_bake(
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
    (bake_dir / "sentinel.json").write_bytes(b"new-bundled-sentinel")

    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()
    try:
        assert corpus_bootstrap._install_baked_corpus_if_present() == "self_healed_baked"
        assert (user_dir / "corpus.db.enc").read_bytes() == b"new-bundled-db"
        assert (user_dir / "corpus.db.salt").read_bytes() == b"new-bundled-salt"
        assert (user_dir / "sentinel.json").read_bytes() == b"new-bundled-sentinel"
        assert list(user_dir.glob("corpus.db.enc.broken-*"))
        assert corpus_bootstrap.get_state().source == "self_healed_baked"
    finally:
        corpus_bootstrap.reset_for_tests()


def test_runtime_only_upgrade_contract_is_documented() -> None:
    src = Path(corpus_bootstrap.__file__).read_text(encoding="utf-8")
    assert_in_source(src, "Round 107 / Build 76", label='src')
    assert_in_source(src, "prebaked corpus", label='src')
