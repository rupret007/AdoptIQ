"""Round 54 / F1 -- pin the TOCTOU race UX fix in
``corpus_bootstrap._run_index_pass``.

Background
----------
The Round 53 / Phase 53.3 pre-flight gate checks ``onedrive_status`` and
``sentinel_present`` before calling ``open_corpus_for_user``.  If the
OneDrive desktop client evicts the sentinel between the gate and the
open (Files-On-Demand reclaim, user signs out, share is un-shared,
etc.), the open raises ``CorpusCryptoError``.  Pre-Round-54 the catch
recorded ``last_error_kind = "crypto"`` and the analyze panel surfaced
the legacy crypto-error path -- "Reset corpus" CTA, no actionable
remediation -- even though the actual root cause is "OneDrive is no
longer providing the sentinel".

Round 54 / F1 re-probes the OneDrive gate inside the catch.  When the
re-probe shows the gate would now fail, we surface
``blocked_no_onedrive`` (the same state the user would have seen if
the gate had caught it on the first pass) so the panel renders the
clear "Sign in to OneDrive" CTA + clickable deep-link.

These tests pin every branch of the new TOCTOU recovery:

* sentinel disappears between gate and open -> blocked_no_onedrive
* whole OneDrive folder unmounts between gate and open -> blocked_no_onedrive
* CorpusCryptoError raised but OneDrive still synced + sentinel still
  present (i.e. the open really did fail for a key/digest reason, not
  a TOCTOU) -> classic ``crypto`` path is preserved.

Security is unaffected by this fix -- the open ALREADY fails-closed
because ``allow_local_sentinel=False`` is passed in.  This is purely
UX clarity for the race case.
"""

# Round 54

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

import corpus_bootstrap
from corpus_crypto import (
    CorpusCryptoError,
    DEFAULT_SENTINEL_NAME,
)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Mirror the isolation harness from
    ``tests/test_round53_bootstrap_blocked_no_onedrive.py`` so we
    never touch the real user's knowledge dir or the real bake
    bundle inside ``sys._MEIPASS``."""
    fake_user_dir = tmp_path / "user_corpus"
    monkeypatch.setattr(
        corpus_bootstrap, "_user_corpus_dir", lambda: fake_user_dir,
    )
    monkeypatch.delenv("ADOPTIQ_BAKED_CORPUS_DIR", raising=False)
    monkeypatch.delattr(__import__("sys"), "_MEIPASS", raising=False)
    fake_module_path = tmp_path / "corpus_bootstrap_isolated.py"
    fake_module_path.write_text("# isolated for test", encoding="utf-8")
    monkeypatch.setattr(
        corpus_bootstrap, "__file__", str(fake_module_path),
    )
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


def _seed_synced_onedrive_with_sentinel(root: Path) -> Path:
    """Seed a OneDrive root the gate considers synced AND containing
    the canonical sentinel.  Returns the sentinel path so callers can
    delete it mid-flight to simulate the race."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "synced_doc.docx").write_bytes(b"x" * 16)
    sentinel = root / DEFAULT_SENTINEL_NAME
    sentinel.write_bytes(secrets.token_bytes(32))
    return sentinel


def _wire_onedrive(monkeypatch, root: Path) -> None:
    """Point both copies of ``Config.CSONE_ONEDRIVE_FOLDER`` (the
    live module and the bootstrap-imported reference) at ``root``."""
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", str(root),
        raising=False,
    )
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", str(root),
        raising=False,
    )


# ---------------------------------------------------------------------------
# F1 happy paths -- TOCTOU recovery surfaces blocked_no_onedrive
# ---------------------------------------------------------------------------


def test_toctou_sentinel_evicted_between_gate_and_open_yields_blocked(
    tmp_path, monkeypatch,
):
    """Sentinel is present at gate time but disappears before the
    actual ``open_corpus_for_user`` call.  Round 54 / F1: the catch
    re-probes the gate, sees the sentinel is gone, and surfaces
    ``blocked_no_onedrive`` instead of the generic ``crypto`` path."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    def racey_open(**kwargs):
        # Race: delete the sentinel BEFORE the open completes.
        sentinel_path.unlink(missing_ok=True)
        raise CorpusCryptoError(
            "simulated TOCTOU: sentinel disappeared mid-open"
        )

    monkeypatch.setattr(
        corpus_bootstrap, "open_corpus_for_user", racey_open,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source == "blocked_no_onedrive", (
        f"TOCTOU race must re-emit blocked_no_onedrive; got source="
        f"{state.source!r}"
    )
    assert state.last_error_kind == "no_onedrive_sentinel", (
        f"TOCTOU race must use the no_onedrive_sentinel kind so the "
        f"UI matches the gate path; got {state.last_error_kind!r}"
    )
    # The user-visible message must point at OneDrive, not the
    # raw CorpusCryptoError text.
    msg = state.last_error or ""
    assert "OneDrive" in msg
    assert "AI Projects/AdoptIQ_CSOne_Reports" in msg


def test_toctou_whole_folder_unmounted_between_gate_and_open_yields_blocked(
    tmp_path, monkeypatch,
):
    """Whole OneDrive folder unmounts between gate and open (e.g. the
    OneDrive client signs out and removes the local mirror).  The
    re-probe sees ``onedrive_status="not_synced"`` and surfaces
    ``blocked_no_onedrive``."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    def racey_open(**kwargs):
        # Wipe the whole folder.  ``not_synced`` after re-probe.
        for entry in onedrive_root.iterdir():
            try:
                entry.unlink()
            except OSError:
                pass
        try:
            onedrive_root.rmdir()
        except OSError:
            pass
        raise CorpusCryptoError(
            "simulated TOCTOU: OneDrive folder unmounted"
        )

    monkeypatch.setattr(
        corpus_bootstrap, "open_corpus_for_user", racey_open,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.source == "blocked_no_onedrive"
    assert state.last_error_kind == "no_onedrive_sentinel"
    assert state.onedrive_status == "not_synced"


# ---------------------------------------------------------------------------
# F1 negative -- a real crypto failure still surfaces "crypto"
# ---------------------------------------------------------------------------


def test_real_crypto_failure_with_sentinel_still_present_keeps_crypto_kind(
    tmp_path, monkeypatch,
):
    """Open raises ``CorpusCryptoError`` but the OneDrive folder is
    STILL synced AND the sentinel is STILL present -- this is a real
    key/digest mismatch, not a TOCTOU race.  The classic ``crypto``
    path must be preserved so the user can see the Reset Corpus
    escape hatch (Round 39 / corpus self-heal)."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    def real_crypto_open(**kwargs):
        # Sentinel and folder remain intact; this is a digest mismatch.
        raise CorpusCryptoError(
            "corpus decrypt failed: authentication tag mismatch"
        )

    monkeypatch.setattr(
        corpus_bootstrap, "open_corpus_for_user", real_crypto_open,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.last_error_kind == "crypto", (
        f"genuine crypto failure must keep last_error_kind='crypto' "
        f"(Round 39 self-heal hooks off this kind); got "
        f"{state.last_error_kind!r}"
    )
    assert state.source != "blocked_no_onedrive", (
        f"genuine crypto failure must NOT mask itself as "
        f"blocked_no_onedrive; got source={state.source!r}"
    )
    # Raw crypto error text must be preserved for log forensics.
    assert "authentication tag mismatch" in (state.last_error or "")


# ---------------------------------------------------------------------------
# F1 defensive contracts
# ---------------------------------------------------------------------------


def test_toctou_recovery_does_not_open_handle(tmp_path, monkeypatch):
    """When the F1 recovery fires, the module-level ``_HANDLE`` must
    remain None so a stale handle from a prior pass cannot leak into
    the indexer."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    def racey_open(**kwargs):
        sentinel_path.unlink(missing_ok=True)
        raise CorpusCryptoError("simulated TOCTOU")

    monkeypatch.setattr(
        corpus_bootstrap, "open_corpus_for_user", racey_open,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    assert corpus_bootstrap._HANDLE is None, (
        "F1 recovery must leave _HANDLE == None"
    )


def test_toctou_recovery_clears_in_progress_and_completed_flags(
    tmp_path, monkeypatch,
):
    """``in_progress`` must clear and ``completed`` must be False so
    the analyze panel does not show a perpetual spinner OR a stale
    'completed' badge."""
    onedrive_root = tmp_path / "onedrive"
    sentinel_path = _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    def racey_open(**kwargs):
        sentinel_path.unlink(missing_ok=True)
        raise CorpusCryptoError("simulated TOCTOU")

    monkeypatch.setattr(
        corpus_bootstrap, "open_corpus_for_user", racey_open,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.in_progress is False
    assert state.completed is False
    assert state.last_finished_at is not None


def test_toctou_recovery_records_post_probe_onedrive_counts(
    tmp_path, monkeypatch,
):
    """The recovery path must update ``onedrive_status`` and
    ``onedrive_file_count`` from the POST-race re-probe -- otherwise
    the panel would show stale gate-time counts that no longer
    reflect reality."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    def racey_open(**kwargs):
        # Wipe everything -- post-probe count must be 0.
        for entry in list(onedrive_root.iterdir()):
            try:
                entry.unlink()
            except OSError:
                pass
        raise CorpusCryptoError("simulated TOCTOU")

    monkeypatch.setattr(
        corpus_bootstrap, "open_corpus_for_user", racey_open,
    )

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.onedrive_status == "synced" or state.onedrive_status == "not_synced", (
        f"onedrive_status must be set from post-probe; got {state.onedrive_status!r}"
    )
    # Post-race the folder is empty so file count is 0.
    assert state.onedrive_file_count == 0


def test_toctou_recovery_runs_only_when_open_raised(
    tmp_path, monkeypatch,
):
    """The F1 recovery branch lives INSIDE the
    ``except CorpusCryptoError`` arm.  A successful open must never
    enter the recovery code -- pin this by asserting the gate's
    ``last_error_kind`` is unset on success."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    # Don't monkeypatch open_corpus_for_user -- let the real call
    # succeed (the test sentinel is valid; the index pass will
    # complete normally and return source="fresh" or similar).
    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.last_error_kind != "no_onedrive_sentinel", (
        f"happy path must NOT enter F1 recovery; got "
        f"last_error_kind={state.last_error_kind!r}"
    )
    assert state.source != "blocked_no_onedrive"


@pytest.mark.parametrize(
    "stale_source",
    ("blocked_no_onedrive", "signed_in_no_corpus"),
)
def test_successful_runtime_index_normalizes_stale_blocked_source(
    tmp_path, monkeypatch, stale_source,
):
    """Round 96.1: a healthy runtime index must clear any prior blocked
    ``_STATE.source`` from the same process so the JS panel can classify
    the next payload as runtime_synced."""
    onedrive_root = tmp_path / "onedrive"
    _seed_synced_onedrive_with_sentinel(onedrive_root)
    _wire_onedrive(monkeypatch, onedrive_root)

    class FakeHandle:
        conn = object()

        def commit_to_disk(self):
            return None

    monkeypatch.setattr(
        corpus_bootstrap,
        "_resolve_index_sources",
        lambda: [{"label": "onedrive", "dir": onedrive_root, "filter": "all_supported"}],
    )
    monkeypatch.setattr(
        corpus_bootstrap,
        "open_corpus_for_user",
        lambda **_kwargs: FakeHandle(),
    )
    monkeypatch.setattr(corpus_bootstrap, "configure_connection", lambda _conn: None)
    monkeypatch.setattr(
        corpus_bootstrap,
        "index_folder",
        lambda *_args, **_kwargs: corpus_bootstrap.IndexStats(
            files_seen=1,
            files_parsed=1,
            chunks_added=1,
        ),
    )
    with corpus_bootstrap._BOOT_LOCK:
        corpus_bootstrap._STATE.source = stale_source

    corpus_bootstrap._run_index_pass(rebuild=False)

    state = corpus_bootstrap.get_state()
    assert state.completed is True
    assert state.last_error_kind is None
    assert state.source == "fresh"
