"""Round 106 / Build 75: pin local corpus bootstrap without OneDrive.

Background
----------
Round 36 introduced ``CorpusBootState.onedrive_status`` so the
analyze panel could distinguish a synced OneDrive sync from an
unsynced one.  Round 53 adds a new fail-closed contract on top:

* The bake bundle no longer ships ``sentinel.json`` /
  ``corpus.sentinel.lock.json``.  The encrypted corpus is keyed
  against the canonical sentinel that lives in
  ``AI Projects/AdoptIQ_CSOne_Reports`` -- only OneDrive-signed-in
  Cisco users can sync it.
* The runtime open path now passes ``allow_local_sentinel=False``,
  so an install without OneDrive sync cannot derive the AES key
  and cannot decrypt the corpus.
* Surfacing that state cleanly is the responsibility of the
  pre-flight gate in ``_run_index_pass``: when
  ``onedrive_status != "synced"`` OR the canonical sentinel is
  not present, the index pass short-circuits and writes
  ``_STATE.source = "blocked_no_onedrive"`` along with a
  remediation ``last_error`` and ``last_error_kind="no_onedrive_sentinel"``.

Build 75 temporarily removes the OneDrive/sentinel prerequisite so the
local corpus can index generated reports and Intelligence uploads before
the OneDrive rollout is reintroduced.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

import corpus_bootstrap


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Point the bootstrap at tmp dirs and reset module state between
    tests so we never touch the real user's knowledge dir."""
    fake_user_dir = tmp_path / "user_corpus"
    monkeypatch.setattr(
        corpus_bootstrap, "_user_corpus_dir", lambda: fake_user_dir,
    )
    monkeypatch.setattr(corpus_bootstrap, "_resolve_index_sources", lambda: [])

    def _unexpected_default_db_path():
        pytest.fail(
            "_run_index_pass must derive its encrypted path from "
            "_user_corpus_dir, not the real default_db_path"
        )

    monkeypatch.setattr(
        corpus_bootstrap,
        "default_db_path",
        _unexpected_default_db_path,
    )
    # Default: no baked corpus on the test host so the tests exercise
    # the post-install pre-flight gate, not the install path itself.
    monkeypatch.delenv("ADOPTIQ_BAKED_CORPUS_DIR", raising=False)
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    fake_module_path = tmp_path / "corpus_bootstrap_isolated.py"
    fake_module_path.write_text("# isolated for test", encoding="utf-8")
    monkeypatch.setattr(
        corpus_bootstrap, "__file__", str(fake_module_path),
    )
    # Round 83 / Build 59: pin the OneDrive sign-in proxy to
    # ``"not_signed_in"`` for the R53 legacy-blocked test suite so
    # they exercise the legacy ``blocked_no_onedrive`` branch (NOT
    # the new R83 ``signed_in_no_corpus`` branch which fires
    # automatically on dev hosts that DO have CloudStorage/
    # OneDrive-Cisco populated).  Tests that want to exercise the
    # new R83 path live in ``test_round83_onedrive_signed_in_proxy.py``
    # and ``test_round83_signed_in_no_corpus_panel.py``; this fixture
    # preserves the R53 contract intact.
    monkeypatch.setattr(
        corpus_bootstrap,
        "_r83_onedrive_signed_in_proxy",
        lambda: "not_signed_in",
    )
    corpus_bootstrap.reset_for_tests()
    yield fake_user_dir
    corpus_bootstrap.reset_for_tests()


def _seed_synced_onedrive(root: Path, *, with_sentinel: bool) -> None:
    """Drop a non-empty file at the top level so the sync-status
    heuristic counts as synced.  Optionally drop the canonical
    sentinel."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "synced_doc.docx").write_bytes(b"x" * 16)
    if with_sentinel:
        from corpus_crypto import DEFAULT_SENTINEL_NAME
        (root / DEFAULT_SENTINEL_NAME).write_bytes(secrets.token_bytes(32))


# ---------------------------------------------------------------------------
# Gate fires: not_synced or sentinel-absent path
# ---------------------------------------------------------------------------


def test_local_corpus_runs_when_csone_folder_unconfigured(
    tmp_path, monkeypatch, _isolate_state,
):
    """No ``CSONE_ONEDRIVE_FOLDER`` configured at all still creates the
    local encrypted corpus."""
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source == "fresh"
    assert state.last_error_kind is None
    assert state.in_progress is False
    assert state.completed is True
    assert Path(state.encrypted_path) == _isolate_state / "corpus.db.enc"


def test_local_corpus_runs_when_folder_missing_on_disk(
    tmp_path, monkeypatch,
):
    """A missing configured OneDrive folder no longer blocks indexing."""
    import config as _live_config
    missing_root = tmp_path / "no_such_onedrive"
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(missing_root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(missing_root),
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source == "fresh"
    assert state.onedrive_status == "not_synced"
    assert state.last_error_kind is None


def test_local_corpus_runs_when_folder_synced_but_sentinel_absent(
    tmp_path, monkeypatch,
):
    """A synced OneDrive folder without the canonical sentinel is still
    indexed using the local per-user corpus key."""
    import config as _live_config
    onedrive_root = tmp_path / "onedrive_no_sentinel"
    _seed_synced_onedrive(onedrive_root, with_sentinel=False)
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source == "fresh"
    assert state.onedrive_status == "synced"
    assert state.last_error_kind is None


def test_local_corpus_runs_when_folder_only_zero_byte_stubs(
    tmp_path, monkeypatch,
):
    """OneDrive Files-On-Demand placeholders are 0-byte stubs.  The
    sync-status heuristic must NOT count them as 'synced', but that
    diagnostic no longer blocks the local corpus."""
    import config as _live_config
    onedrive_root = tmp_path / "onedrive_stubs"
    onedrive_root.mkdir(parents=True)
    (onedrive_root / "stub_a.docx").write_bytes(b"")
    (onedrive_root / "stub_b.xlsx").write_bytes(b"")
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source == "fresh"
    assert state.onedrive_status == "not_synced"


def test_local_corpus_without_onedrive_records_no_remediation_error(
    tmp_path, monkeypatch,
):
    """Missing OneDrive should not emit a user-action-required error."""
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.last_error is None
    assert state.last_error_kind is None


# ---------------------------------------------------------------------------
# Gate does NOT fire: synced + sentinel present -> normal path
# ---------------------------------------------------------------------------


def test_gate_passes_when_onedrive_synced_and_sentinel_present(
    tmp_path, monkeypatch,
):
    """The happy path: synced OneDrive + sentinel present.  The gate
    must NOT fire; the source ends up either ``"fresh"`` (no baked
    corpus) or whatever the indexer leaves it at."""
    import config as _live_config
    onedrive_root = tmp_path / "onedrive_ok"
    _seed_synced_onedrive(onedrive_root, with_sentinel=True)
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(onedrive_root),
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source != "blocked_no_onedrive", (
        f"happy-path source must NOT be blocked_no_onedrive; "
        f"got {state.source!r}"
    )
    # ``last_error_kind`` must NOT be "no_onedrive_sentinel" on the
    # happy path -- if it is, the gate fired incorrectly.
    assert state.last_error_kind != "no_onedrive_sentinel", (
        f"gate fired falsely on happy path; last_error_kind="
        f"{state.last_error_kind!r}"
    )


# ---------------------------------------------------------------------------
# Defensive contracts
# ---------------------------------------------------------------------------


def test_local_corpus_without_onedrive_opens_handle(tmp_path, monkeypatch):
    """The local sentinel path should configure a corpus handle even
    when OneDrive is unavailable."""
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    assert corpus_bootstrap._HANDLE is not None


def test_local_corpus_without_onedrive_clears_in_progress_flag(tmp_path, monkeypatch):
    """The local corpus path must clear ``in_progress`` after indexing."""
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None,
        raising=False,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.in_progress is False, (
        "local indexing must clear in_progress so the panel does not "
        "show a perpetual 'Indexing...' spinner."
    )
    assert state.last_finished_at is not None, (
        "local indexing must stamp last_finished_at so the panel can "
        "render the timestamp of the attempt."
    )
