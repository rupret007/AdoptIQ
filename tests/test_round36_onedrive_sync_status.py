"""Round 36 / onedrive-sync-auth: pin
``corpus_bootstrap._check_onedrive_sync_status`` behavior.

The Round 36 native-auth pivot trusts that the OneDrive desktop
client handled SSO/MFA/admin-consent and verifies the result by
``Path.is_dir()`` + counting non-empty files at
``Config.CSONE_ONEDRIVE_FOLDER``.  These tests pin the four-state
output contract that the Intelligence panel
(``static/js/intel_status.js`` / ``classifyCorpusPanel``) and the
daily-refresh worker (``corpus_bootstrap._daily_refresh_loop``)
depend on:

  * ``"synced"``     when the folder exists and has at least one
    real (size > 0) file.
  * ``"not_synced"`` when the folder is missing, unreadable, empty,
    or contains only zero-byte placeholder stubs (the OneDrive
    on-demand "not yet pulled" representation).
  * ``"unknown"``    when ``Config.CSONE_ONEDRIVE_FOLDER`` is not
    configured at all (defensive default; should not happen in a
    shipping build but the helper must not crash).

The helper is on the boot path, so it must NEVER raise -- any
filesystem error MUST collapse to ``("not_synced", 0, path)``.
This is also tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import corpus_bootstrap


@pytest.fixture(autouse=True)
def _reset_state():
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


def _patch_onedrive_folder(monkeypatch, value):
    """Patch ``CSONE_ONEDRIVE_FOLDER`` on the SAME ``Config`` reference
    that ``corpus_bootstrap`` reads from.  ``corpus_bootstrap`` did
    ``from config import Config`` at import time, so any test that
    reloads ``config`` (e.g. ``test_round35_corpus_url_hardcoded.py``)
    leaves ``corpus_bootstrap.Config`` pointing at the *original*
    class.  Patching ``config.Config`` directly would silently miss
    the helper.  We patch both the bootstrap reference and the live
    config-module reference to be robust against reload-pollution."""
    monkeypatch.setattr(
        corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", value,
        raising=False,
    )
    # Also keep the live config-module reference in sync so any code
    # path that re-resolves through ``config.Config`` sees the same
    # value (defense-in-depth).
    import config as _live_config
    monkeypatch.setattr(
        _live_config.Config, "CSONE_ONEDRIVE_FOLDER", value,
        raising=False,
    )


def test_returns_unknown_when_folder_unconfigured(monkeypatch):
    """When ``Config.CSONE_ONEDRIVE_FOLDER`` is falsy the helper must
    return ``("unknown", 0, None)`` so the panel renders the
    "Status unknown" pill rather than misleading the operator with
    "OneDrive sync required"."""
    _patch_onedrive_folder(monkeypatch, "")
    status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    assert status == "unknown"
    assert count == 0
    assert path is None


def test_returns_synced_when_folder_has_real_files(monkeypatch, tmp_path):
    """The happy path: OneDrive client has the folder mirrored and
    has finished pulling at least one file -- the daily-refresh
    worker's gate must pass."""
    od_dir = tmp_path / "AdoptIQ_CSOne_Reports"
    od_dir.mkdir()
    (od_dir / "report1.xlsx").write_bytes(b"binary-content-here")

    _patch_onedrive_folder(monkeypatch, str(od_dir))

    status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    assert status == "synced"
    assert count >= 1
    assert path == str(od_dir)


def test_returns_not_synced_when_folder_missing(monkeypatch, tmp_path):
    """Pointing at a path that does not exist (e.g. user has not
    started the OneDrive desktop client) must surface as
    ``not_synced`` with file_count=0."""
    missing = tmp_path / "no_such_folder"
    _patch_onedrive_folder(monkeypatch, str(missing))
    status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    assert status == "not_synced"
    assert count == 0
    assert path == str(missing)


def test_returns_not_synced_when_folder_empty(monkeypatch, tmp_path):
    """An empty directory (the user created the OneDrive folder but
    nothing has been synced yet) must NOT flip the daily-refresh
    gate to ``synced`` -- otherwise the indexer would walk an empty
    tree and overwrite the baked snapshot with zero rows."""
    od_dir = tmp_path / "AdoptIQ_CSOne_Reports"
    od_dir.mkdir()
    _patch_onedrive_folder(monkeypatch, str(od_dir))
    status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    assert status == "not_synced"
    assert count == 0
    assert path == str(od_dir)


def test_returns_not_synced_when_folder_only_has_zero_byte_stubs(
    monkeypatch, tmp_path
):
    """OneDrive Files-On-Demand represents not-yet-downloaded files
    as zero-byte placeholders.  The presence check must skip those
    and report ``not_synced`` -- otherwise we would attempt to
    index empty placeholders and break the corpus."""
    od_dir = tmp_path / "AdoptIQ_CSOne_Reports"
    od_dir.mkdir()
    # Two zero-byte placeholders, no real content.
    (od_dir / "report1.xlsx").touch()
    (od_dir / "report2.docx").touch()

    _patch_onedrive_folder(monkeypatch, str(od_dir))
    status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    assert status == "not_synced"
    assert count == 0
    assert path == str(od_dir)


def test_returns_not_synced_when_path_is_a_file_not_dir(
    monkeypatch, tmp_path
):
    """Guard against an operator typo that points the config at a
    file path (e.g. a Cisco-Webex backup .zip) -- must report
    ``not_synced`` rather than crashing on ``iterdir``."""
    bogus = tmp_path / "report.zip"
    bogus.write_bytes(b"not a directory")

    _patch_onedrive_folder(monkeypatch, str(bogus))
    status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    assert status == "not_synced"
    assert count == 0
    assert path == str(bogus)


def test_never_raises_on_oserror(monkeypatch, tmp_path):
    """The helper sits on the boot path and MUST NOT raise.  Even
    when ``Path.iterdir`` throws (e.g. permission denied), the
    helper must collapse to ``("not_synced", 0, path)`` so the boot
    sequence keeps moving and the user sees a panel state rather
    than a 500."""
    od_dir = tmp_path / "AdoptIQ_CSOne_Reports"
    od_dir.mkdir()
    (od_dir / "report1.xlsx").write_bytes(b"x")

    _patch_onedrive_folder(monkeypatch, str(od_dir))

    # Make Path.iterdir raise to simulate a permissions failure.
    real_iterdir = Path.iterdir

    def boom(self):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(Path, "iterdir", boom)
    try:
        status, count, path = corpus_bootstrap._check_onedrive_sync_status()
    finally:
        monkeypatch.setattr(Path, "iterdir", real_iterdir)

    assert status == "not_synced"
    assert count == 0
    assert path == str(od_dir)


def test_state_dataclass_has_onedrive_fields():
    """The new R36 fields on ``CorpusBootState`` must default to
    ``None`` so the JSON payload's ``boot.onedrive_status`` /
    ``boot.onedrive_file_count`` are present (set to None) on first
    boot, before the bootstrap thread has populated them."""
    state = corpus_bootstrap.get_state()
    assert hasattr(state, "onedrive_status")
    assert hasattr(state, "onedrive_file_count")
    assert state.onedrive_status is None
    assert state.onedrive_file_count is None
