"""Round 107 / Build 76: baked-corpus install is restored.

Build 76 ships a pre-baked corpus snapshot so first launch has Ask AI
corpus data without waiting for runtime indexing or OneDrive setup.
"""

from __future__ import annotations

from pathlib import Path

import corpus_bootstrap


def test_baked_corpus_file_contract_has_three_artifacts() -> None:
    assert corpus_bootstrap._BAKED_CORPUS_FILES == (
        "corpus.db.enc",
        "sentinel.json",
        "corpus.db.salt",
    )


def test_baked_corpus_dir_uses_explicit_override(
    tmp_path, monkeypatch,
) -> None:
    bake_dir = tmp_path / "bake"
    bake_dir.mkdir()
    (bake_dir / "corpus.db.enc").write_bytes(b"old-local-bake")
    (bake_dir / "corpus.db.salt").write_bytes(b"old-local-salt")
    (bake_dir / "sentinel.json").write_bytes(b"build-sentinel")
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    assert corpus_bootstrap._baked_corpus_dir() == bake_dir


def test_install_baked_corpus_copies_files_on_first_launch(
    tmp_path, monkeypatch,
) -> None:
    user_dir = tmp_path / "user_corpus"
    bake_dir = tmp_path / "bake"
    bake_dir.mkdir()
    (bake_dir / "corpus.db.enc").write_bytes(b"old-local-bake")
    (bake_dir / "corpus.db.salt").write_bytes(b"old-local-salt")
    (bake_dir / "sentinel.json").write_bytes(b"build-sentinel")
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()

    try:
        result = corpus_bootstrap._install_baked_corpus_if_present()
        assert result == "baked"
        assert (user_dir / "corpus.db.enc").read_bytes() == b"old-local-bake"
        assert (user_dir / "corpus.db.salt").read_bytes() == b"old-local-salt"
        assert (user_dir / "sentinel.json").read_bytes() == b"build-sentinel"
        state = corpus_bootstrap.get_state()
        assert state.source == "baked"
    finally:
        corpus_bootstrap.reset_for_tests()


def test_runtime_bootstrap_installs_baked_corpus_in_source() -> None:
    body = Path(corpus_bootstrap.__file__).read_text(encoding="utf-8")
    run_index_body = body[
        body.find("def _run_index_pass") : body.find("def _warm_embedder_in_background")
    ]
    assert "Round 107 / Build 76" in body
    assert 'installed_source or "fresh"' in run_index_body
    assert "_install_baked_corpus_if_present()" in run_index_body
    assert "installed_source or" in run_index_body
