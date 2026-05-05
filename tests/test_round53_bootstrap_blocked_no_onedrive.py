"""Round 53 / Phase 53.3: pin the runtime fail-closed gate that
surfaces the new ``blocked_no_onedrive`` boot state.

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

These tests pin every branch of that gate.
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
    yield
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


def test_blocked_no_onedrive_when_csone_folder_unconfigured(
    tmp_path, monkeypatch,
):
    """No ``CSONE_ONEDRIVE_FOLDER`` configured at all -> the pre-flight
    gate fires and we surface ``blocked_no_onedrive``."""
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
    assert state.source == "blocked_no_onedrive", (
        f"unconfigured OneDrive must yield blocked_no_onedrive; "
        f"got {state.source!r}"
    )
    assert state.last_error_kind == "no_onedrive_sentinel"
    assert state.in_progress is False
    assert state.completed is False


def test_blocked_no_onedrive_when_folder_missing_on_disk(
    tmp_path, monkeypatch,
):
    """``CSONE_ONEDRIVE_FOLDER`` configured but the directory does
    not exist on disk -> ``onedrive_status="not_synced"`` -> gate
    fires."""
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
    assert state.source == "blocked_no_onedrive"
    assert state.onedrive_status == "not_synced"
    assert state.last_error_kind == "no_onedrive_sentinel"


def test_blocked_no_onedrive_when_folder_synced_but_sentinel_absent(
    tmp_path, monkeypatch,
):
    """OneDrive folder is synced (has real files) but the canonical
    sentinel was never minted into it -> the gate still fires
    because we cannot derive the AES key without the sentinel."""
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
    assert state.source == "blocked_no_onedrive", (
        "synced OneDrive WITHOUT a sentinel must still fail-closed -- "
        "the sentinel is the actual access boundary."
    )
    assert state.onedrive_status == "synced"
    assert state.last_error_kind == "no_onedrive_sentinel"


def test_blocked_no_onedrive_when_folder_only_zero_byte_stubs(
    tmp_path, monkeypatch,
):
    """OneDrive Files-On-Demand placeholders are 0-byte stubs.  The
    sync-status heuristic must NOT count them as 'synced' -- if it
    did the gate would let through a folder where the sentinel is a
    placeholder we cannot read."""
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
    assert state.source == "blocked_no_onedrive"
    assert state.onedrive_status == "not_synced"


def test_blocked_no_onedrive_records_remediation_message(
    tmp_path, monkeypatch,
):
    """The user-visible ``last_error`` must point the user at the
    OneDrive desktop client -- not show a raw crypto error."""
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
    msg = state.last_error or ""
    # Sanity-check the remediation language without coupling to exact
    # wording (tests should not break on a small copy edit).
    assert "OneDrive" in msg, (
        f"remediation message must mention OneDrive; got: {msg!r}"
    )
    assert "AI Projects/AdoptIQ_CSOne_Reports" in msg, (
        f"remediation message must name the canonical share folder; "
        f"got: {msg!r}"
    )


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


def test_gate_failure_does_not_open_handle(tmp_path, monkeypatch):
    """When the gate fires, ``_HANDLE`` must remain None -- otherwise
    a stale handle from a prior pass could leak the prior key into
    the indexer."""
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

    # The module-level handle must NOT be set when the gate fires.
    assert corpus_bootstrap._HANDLE is None, (
        "gate firing must leave _HANDLE == None"
    )


def test_gate_failure_clears_in_progress_flag(tmp_path, monkeypatch):
    """If the gate fires, ``_STATE.in_progress`` must be False so the
    UI does not show a perpetual spinner."""
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
        "gate firing must clear in_progress so the panel does not "
        "show a perpetual 'Indexing...' spinner."
    )
    assert state.last_finished_at is not None, (
        "gate firing must stamp last_finished_at so the panel can "
        "render the timestamp of the failed attempt."
    )
