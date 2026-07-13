"""Round 68 / Build 42 (B5): pin behavior of the daily refresh worker's
new sentinel-presence gate so the blocked-to-synced transition does not
fire ``request_refresh`` until the canonical OneDrive sentinel has
actually landed on disk.

Pre-R68 the worker fired refresh as soon as
``_check_onedrive_sync_status() == "synced"`` (folder + >=1 non-empty
file).  But the sentinel itself can land several ticks after the
folder reports synced -- the worker would then trigger a refresh,
``open_corpus_for_user`` would raise ``CorpusCryptoError("corpus
sentinel not found")``, and the operator would see ``Last refresh
failed`` for ~24h until the natural window-based refresh re-fired.

This suite pins the new ``_r68_onedrive_sentinel_present`` helper +
the daily-worker call site + the structured-log surface so a future
refactor cannot drop the gate without firing the alarm.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import corpus_bootstrap


# ---------------------------------------------------------------------------
# Sentinel helper unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_csone_folder():
    saved = getattr(corpus_bootstrap.Config, "CSONE_ONEDRIVE_FOLDER", None)
    yield
    corpus_bootstrap.Config.CSONE_ONEDRIVE_FOLDER = saved


def _set_folder(path: Path) -> None:
    corpus_bootstrap.Config.CSONE_ONEDRIVE_FOLDER = str(path)


def test_sentinel_helper_false_when_env_unset(restore_csone_folder):
    corpus_bootstrap.Config.CSONE_ONEDRIVE_FOLDER = None
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is False


def test_sentinel_helper_false_when_folder_missing(restore_csone_folder, tmp_path):
    _set_folder(tmp_path / "not-a-folder")
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is False


def test_sentinel_helper_false_when_folder_present_but_no_sentinel(restore_csone_folder, tmp_path):
    (tmp_path / "report.xlsx").write_bytes(b"x" * 1024)
    _set_folder(tmp_path)
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is False


def test_sentinel_helper_false_when_sentinel_is_zero_byte_placeholder(restore_csone_folder, tmp_path):
    """Files-On-Demand stub: filename present, size on disk == 0."""
    sentinel = tmp_path / "adoptiq_corpus_sentinel.json"
    sentinel.write_bytes(b"")
    _set_folder(tmp_path)
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is False


def test_sentinel_helper_true_when_sentinel_is_real_file(restore_csone_folder, tmp_path):
    sentinel = tmp_path / "adoptiq_corpus_sentinel.json"
    sentinel.write_bytes(b"{\"version\": 1}")
    _set_folder(tmp_path)
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is True


def test_sentinel_helper_honors_env_override(restore_csone_folder, tmp_path, monkeypatch):
    sentinel = tmp_path / "custom.json"
    sentinel.write_bytes(b"{\"version\": 1}")
    monkeypatch.setenv("ADOPTIQ_CORPUS_SENTINEL", "custom.json")
    _set_folder(tmp_path)
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is True


def test_sentinel_helper_rejects_traversal_env_override(restore_csone_folder, tmp_path, monkeypatch):
    """A hostile env override that smuggles ``..`` MUST collapse to False
    (the underlying ``resolve_sentinel_path`` raises; the helper
    swallows the error)."""
    monkeypatch.setenv("ADOPTIQ_CORPUS_SENTINEL", "../escape.json")
    (tmp_path.parent / "escape.json").write_bytes(b"x")
    _set_folder(tmp_path)
    assert corpus_bootstrap._r68_onedrive_sentinel_present() is False


# ---------------------------------------------------------------------------
# Daily-worker source-shape pins (no live thread spin-up)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bootstrap_source() -> str:
    return (REPO_ROOT / "corpus_bootstrap.py").read_text(encoding="utf-8")


def test_daily_worker_calls_sentinel_helper(bootstrap_source: str) -> None:
    assert "_r68_onedrive_sentinel_present()" in bootstrap_source, (
        "Round 68 / Build 42 (B5): the daily refresh worker MUST call "
        "_r68_onedrive_sentinel_present() before triggering the "
        "blocked-to-synced transition refresh.  Removing the call "
        "regresses to the pre-R68 fail-fast-then-back-off-24h trap."
    )


def test_transition_unblocked_no_longer_requires_sentinel_present(bootstrap_source: str) -> None:
    """Round 106 / Build 75: the sentinel probe is diagnostic only."""
    snippet = (
        "transition_unblocked = (\n"
        "                blocked_now and od_status == \"synced\" and sentinel_present\n"
        "            )"
    )
    assert snippet not in bootstrap_source, (
        "Round 106 / Build 75: transition_unblocked must not require "
        "sentinel_present because OneDrive is no longer a corpus prerequisite."
    )
    assert "transition_unblocked = blocked_now" in bootstrap_source


def test_synced_folder_without_sentinel_does_not_keep_blocked_streak(bootstrap_source: str) -> None:
    """Round 106 / Build 75: a missing sentinel cannot keep the worker blocked."""
    expected = (
        "if blocked_now and od_status == \"synced\" and not sentinel_present:\n"
        "                blocked_streak += 1"
    )
    assert expected not in bootstrap_source, (
        "Round 106 / Build 75: synced-without-sentinel should fall through to "
        "the local refresh path instead of preserving the pre-Build-75 block."
    )


def test_trigger_log_records_sentinel_present_field(bootstrap_source: str) -> None:
    assert "sentinel_present=%s" in bootstrap_source, (
        "Round 68 / Build 42 (B5): the trigger log line MUST record "
        "the sentinel_present value so SIEM correlation can attribute "
        "a refresh attempt to the sentinel-detected branch vs the "
        "24h-window branch."
    )
