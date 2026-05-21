"""Round 96 / Build 69: bundled self-heal is retired safely.

Round 39 could reinstall a bundled baked snapshot after a crypto
failure. Round 96 removes the bundle data, so the old install helper is
a no-op and recovery is reset + runtime re-index from OneDrive.
"""

from __future__ import annotations

from pathlib import Path

import corpus_bootstrap


_LEGACY_FILES = (
    "corpus.db.enc",
    "corpus.db.salt",
    "sentinel.json",
    "corpus.sentinel.lock.json",
)


def test_install_baked_helper_is_noop_with_local_bake_override(
    tmp_path, monkeypatch,
) -> None:
    bake_dir = tmp_path / "bake"
    user_dir = tmp_path / "user"
    bake_dir.mkdir()
    for name in ("corpus.db.enc", "corpus.db.salt"):
        (bake_dir / name).write_bytes(f"stale-{name}".encode("utf-8"))

    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()
    try:
        assert corpus_bootstrap._install_baked_corpus_if_present() is None
        assert not user_dir.exists(), (
            "legacy baked install helper must not create or copy runtime corpus data"
        )
        assert corpus_bootstrap.get_state().source is None
    finally:
        corpus_bootstrap.reset_for_tests()


def test_reset_still_preserves_legacy_local_artifacts(tmp_path, monkeypatch) -> None:
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    for name in _LEGACY_FILES:
        (user_dir / name).write_bytes(f"legacy-{name}".encode("utf-8"))

    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()
    try:
        suffix, count = corpus_bootstrap.reset_user_corpus()
        assert suffix is not None
        assert count == len(_LEGACY_FILES)
        for name in _LEGACY_FILES:
            assert not (user_dir / name).exists()
            assert (user_dir / f"{name}.broken-{suffix}").exists()
    finally:
        corpus_bootstrap.reset_for_tests()


def test_no_self_healed_baked_emitter_remains() -> None:
    src = Path(corpus_bootstrap.__file__).read_text(encoding="utf-8")
    assert '_STATE.source = "self_healed_baked"' not in src
    assert "self-healed baked corpus" not in src
