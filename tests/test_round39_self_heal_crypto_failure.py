"""Round 107 / Build 76: bundled self-heal is restored.

Build 76 can reinstall a bundled baked snapshot after a crypto failure
so the user gets an immediately usable corpus without OneDrive setup.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

from pathlib import Path

import corpus_bootstrap
from corpus_crypto import CorpusCryptoError
from corpus_indexer import IndexStats


_LEGACY_FILES = (
    "corpus.db.enc",
    "corpus.db.salt",
    "sentinel.json",
    "corpus.sentinel.lock.json",
)


def test_install_baked_helper_uses_local_bake_override(
    tmp_path, monkeypatch,
) -> None:
    bake_dir = tmp_path / "bake"
    user_dir = tmp_path / "user"
    bake_dir.mkdir()
    for name in ("corpus.db.enc", "corpus.db.salt", "sentinel.json"):
        (bake_dir / name).write_bytes(f"stale-{name}".encode("utf-8"))

    monkeypatch.setenv("ADOPTIQ_BAKED_CORPUS_DIR", str(bake_dir))
    monkeypatch.setattr(corpus_bootstrap, "_user_corpus_dir", lambda: user_dir)
    corpus_bootstrap.reset_for_tests()
    try:
        assert corpus_bootstrap._install_baked_corpus_if_present() == "baked"
        assert (user_dir / "corpus.db.enc").exists()
        assert (user_dir / "corpus.db.salt").exists()
        assert (user_dir / "sentinel.json").exists()
        assert corpus_bootstrap.get_state().source == "baked"
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


def test_self_healed_baked_emitter_is_restored() -> None:
    src = Path(corpus_bootstrap.__file__).read_text(encoding="utf-8")
    assert_in_source(src, "self_healed_baked", label='src')
    assert_in_source(src, "prebaked corpus", label='src')


def test_round99_runtime_crypto_failure_preserves_and_retries(
    tmp_path, monkeypatch,
) -> None:
    user_dir = tmp_path / "knowledge"
    user_dir.mkdir()
    onedrive = tmp_path / "OneDrive-Cisco" / "AI Projects" / "AdoptIQ_CSOne_Reports"
    onedrive.mkdir(parents=True)
    (onedrive / "adoptiq_corpus_sentinel.json").write_text("sentinel", encoding="utf-8")
    (onedrive / "report.csv").write_text("Customer,Value\nAcme,1\n", encoding="utf-8")
    for name in _LEGACY_FILES:
        (user_dir / name).write_bytes(f"stale-{name}".encode("utf-8"))

    class FakeHandle:
        conn = object()

        def __init__(self) -> None:
            self.committed = False
            self.closed = False

        def commit_to_disk(self) -> None:
            self.committed = True

        def close(self, persist: bool = True) -> None:
            self.closed = True

    handle = FakeHandle()
    open_calls = []
    configured = []

    def fake_open_corpus_for_user(**kwargs):
        open_calls.append(kwargs)
        if len(open_calls) == 1:
            raise CorpusCryptoError("authentication tag mismatch")
        return handle

    monkeypatch.setattr(corpus_bootstrap, "default_db_path", lambda: user_dir / "corpus.db")
    monkeypatch.setattr(corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive), raising=False)
    monkeypatch.setattr(corpus_bootstrap, "_check_onedrive_sync_status", lambda: ("synced", 1, str(onedrive)))
    monkeypatch.setattr(corpus_bootstrap, "_r83_onedrive_signed_in_proxy", lambda: "signed_in_cisco")
    monkeypatch.setattr(
        corpus_bootstrap,
        "_resolve_index_sources",
        lambda: [{"label": "onedrive", "dir": str(onedrive), "filter": "all_supported"}],
    )
    monkeypatch.setattr(corpus_bootstrap, "open_corpus_for_user", fake_open_corpus_for_user)
    monkeypatch.setattr(corpus_bootstrap, "configure_connection", lambda conn: configured.append(conn))
    monkeypatch.setattr(
        corpus_bootstrap,
        "index_folder",
        lambda conn, src_dir, signal, rebuild: IndexStats(files_seen=1, files_parsed=1, chunks_added=2),
    )

    corpus_bootstrap.reset_for_tests()
    try:
        corpus_bootstrap._run_index_pass(rebuild=False)
        state = corpus_bootstrap.get_state()

        assert len(open_calls) == 2
        assert open_calls[0]["onedrive_root"] is None
        assert open_calls[1]["onedrive_root"] is None
        assert open_calls[0]["allow_local_sentinel"] is True
        assert open_calls[1]["allow_local_sentinel"] is True
        assert handle.committed is True
        assert state.completed is True
        assert state.last_error_kind is None
        assert state.source == "fresh"
        assert configured and configured[-1] is handle.conn
        for name in _LEGACY_FILES:
            assert not (user_dir / name).exists()
        assert list(user_dir.glob("corpus.db.enc.broken-*"))
        assert list(user_dir.glob("corpus.db.salt.broken-*"))
        assert list(user_dir.glob("corpus.sentinel.lock.json.broken-*"))
    finally:
        corpus_bootstrap.reset_for_tests()


def test_round99_runtime_crypto_retry_failure_stays_loud(
    tmp_path, monkeypatch,
) -> None:
    user_dir = tmp_path / "knowledge"
    user_dir.mkdir()
    onedrive = tmp_path / "OneDrive-Cisco" / "AI Projects" / "AdoptIQ_CSOne_Reports"
    onedrive.mkdir(parents=True)
    (onedrive / "adoptiq_corpus_sentinel.json").write_text("sentinel", encoding="utf-8")
    for name in _LEGACY_FILES:
        (user_dir / name).write_bytes(f"stale-{name}".encode("utf-8"))

    open_calls = []
    configured = []

    def fake_open_corpus_for_user(**kwargs):
        open_calls.append(kwargs)
        raise CorpusCryptoError("authentication tag mismatch")

    monkeypatch.setattr(corpus_bootstrap, "default_db_path", lambda: user_dir / "corpus.db")
    monkeypatch.setattr(corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive), raising=False)
    monkeypatch.setattr(corpus_bootstrap, "_check_onedrive_sync_status", lambda: ("synced", 1, str(onedrive)))
    monkeypatch.setattr(corpus_bootstrap, "_r83_onedrive_signed_in_proxy", lambda: "signed_in_cisco")
    monkeypatch.setattr(corpus_bootstrap, "_resolve_index_sources", lambda: [])
    monkeypatch.setattr(corpus_bootstrap, "open_corpus_for_user", fake_open_corpus_for_user)
    monkeypatch.setattr(corpus_bootstrap, "configure_connection", lambda conn: configured.append(conn))

    corpus_bootstrap.reset_for_tests()
    try:
        corpus_bootstrap._run_index_pass(rebuild=False)
        state = corpus_bootstrap.get_state()

        assert len(open_calls) == 2
        assert open_calls[0]["onedrive_root"] is None
        assert open_calls[1]["onedrive_root"] is None
        assert state.completed is False
        assert state.last_error_kind == "crypto"
        assert "after stale-artifact preserve" in (state.last_error or "")
        assert configured and configured[-1] is None
        assert list(user_dir.glob("corpus.db.enc.broken-*"))
    finally:
        corpus_bootstrap.reset_for_tests()
