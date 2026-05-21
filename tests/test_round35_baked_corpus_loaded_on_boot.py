"""Round 96 / Build 69: baked-corpus install is retired.

The historical Round 35 behavior copied encrypted corpus artifacts from
the app bundle into the user's knowledge directory on first launch.
Round 96 externalizes the corpus: no corpus data ships in the app, and
runtime indexing is authoritative after OneDrive sync/auth exposes the
authorized folder and sentinel.
"""

from __future__ import annotations

from pathlib import Path

import corpus_bootstrap


def test_baked_corpus_file_contract_is_empty() -> None:
    assert corpus_bootstrap._BAKED_CORPUS_FILES == (), (
        "Round 96 requires the app-bundled corpus artifact list to stay empty"
    )


def test_baked_corpus_dir_always_returns_none_even_with_override(
    tmp_path, monkeypatch,
) -> None:
    bake_dir = tmp_path / "bake"
    bake_dir.mkdir()
    (bake_dir / "corpus.db.enc").write_bytes(b"old-local-bake")
    (bake_dir / "corpus.db.salt").write_bytes(b"old-local-salt")
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))

    assert corpus_bootstrap._baked_corpus_dir() is None, (
        "runtime-only builds must ignore ADOPTIQ_BAKED_CORPUS_DIR so a "
        "developer's local bake directory is never treated as shipped app data"
    )


def test_install_baked_corpus_noop_does_not_copy_files(
    tmp_path, monkeypatch,
) -> None:
    user_dir = tmp_path / "user_corpus"
    bake_dir = tmp_path / "bake"
    bake_dir.mkdir()
    (bake_dir / "corpus.db.enc").write_bytes(b"old-local-bake")
    (bake_dir / "corpus.db.salt").write_bytes(b"old-local-salt")
    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()

    try:
        result = corpus_bootstrap._install_baked_corpus_if_present()
        assert result is None
        assert not (user_dir / "corpus.db.enc").exists()
        assert not (user_dir / "corpus.db.salt").exists()
        state = corpus_bootstrap.get_state()
        assert state.source is None
    finally:
        corpus_bootstrap.reset_for_tests()


def test_runtime_bootstrap_source_defaults_to_fresh_in_source() -> None:
    body = Path(corpus_bootstrap.__file__).read_text(encoding="utf-8")
    run_index_body = body[
        body.find("def _run_index_pass") : body.find("def _warm_embedder_in_background")
    ]
    assert "Round 96 / runtime-only corpus" in body
    assert '_STATE.source = "fresh"' in run_index_body
    assert "_install_baked_corpus_if_present()" not in run_index_body
