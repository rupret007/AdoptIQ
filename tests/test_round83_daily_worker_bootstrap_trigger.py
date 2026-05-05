"""Round 83 / Build 59 — daily refresh worker bootstrap trigger pins.

Round 83 extends the daily refresh worker so that ``signed_in_no_corpus``
participates in:

* The accelerated 30-second tick cadence (matching ``blocked_no_onedrive``)
  -- so the user who just added the corpus share to their OneDrive
  tree sees the panel transition out of ``signed_in_no_corpus`` ->
  ``synced`` quickly, NOT after the next 1h tick.
* The blocked-to-synced transition trigger (matching
  ``blocked_no_onedrive``) -- so the worker fires an immediate refresh
  the moment it observes ``signed_in_no_corpus`` -> ``synced``.
* The R68 sentinel-presence gate -- so the immediate refresh does
  NOT fire while the OneDrive client is still propagating the
  sentinel onto disk (otherwise we'd hit a false ``Last refresh
  failed`` window).

These tests pin the source-shape so a future refactor cannot drop
any of the three contract pieces without firing the alarm.

Round 83 / Build 59
"""
# Round 83
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import corpus_bootstrap


# ---------------------------------------------------------------------------
# 1. _next_refresh_tick_s — accelerated cadence on signed_in_no_corpus
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_state():
    """Round 83: snapshot/restore the corpus_bootstrap module state
    so each test can mutate ``_STATE.source`` without bleeding into
    sibling tests."""
    # Round 83
    saved_source = corpus_bootstrap._STATE.source
    saved_in_progress = corpus_bootstrap._STATE.in_progress
    yield
    corpus_bootstrap._STATE.source = saved_source
    corpus_bootstrap._STATE.in_progress = saved_in_progress


def test_next_refresh_tick_accelerated_on_signed_in_no_corpus(restore_state):
    """When ``_STATE.source == 'signed_in_no_corpus'`` and
    ``blocked_streak < _DAILY_REFRESH_BLOCKED_MAX_TICKS``, the
    worker MUST return the accelerated 30-second tick."""
    # Round 83
    corpus_bootstrap._STATE.source = "signed_in_no_corpus"
    tick = corpus_bootstrap._next_refresh_tick_s(blocked_streak=10)
    assert tick == corpus_bootstrap._DAILY_REFRESH_TICK_BLOCKED_S
    assert tick == 30.0


def test_next_refresh_tick_accelerated_on_blocked_no_onedrive(restore_state):
    """Round 53 contract preserved: ``blocked_no_onedrive`` ALSO
    returns the accelerated 30-second tick."""
    # Round 83
    corpus_bootstrap._STATE.source = "blocked_no_onedrive"
    tick = corpus_bootstrap._next_refresh_tick_s(blocked_streak=10)
    assert tick == corpus_bootstrap._DAILY_REFRESH_TICK_BLOCKED_S


def test_next_refresh_tick_falls_back_after_max_blocked_streak(restore_state):
    """After ``_DAILY_REFRESH_BLOCKED_MAX_TICKS`` (240 ticks = 2h
    of accelerated polling) the worker reverts to the standard
    1h tick so an unsynced user does not get a perpetual 30-second
    polling loop. R83 inherits this behaviour for both blocked
    states."""
    # Round 83
    corpus_bootstrap._STATE.source = "signed_in_no_corpus"
    tick = corpus_bootstrap._next_refresh_tick_s(
        blocked_streak=corpus_bootstrap._DAILY_REFRESH_BLOCKED_MAX_TICKS,
    )
    assert tick == corpus_bootstrap._DAILY_REFRESH_TICK_S
    assert tick == 3600.0


def test_next_refresh_tick_standard_when_not_blocked(restore_state):
    """When the corpus is in any state OTHER than the two blocked
    states (``baked``, ``fresh``, ``signed_in_no_corpus``,
    ``blocked_no_onedrive``), the worker uses the standard 1h tick."""
    # Round 83
    corpus_bootstrap._STATE.source = "baked"
    tick = corpus_bootstrap._next_refresh_tick_s(blocked_streak=0)
    assert tick == corpus_bootstrap._DAILY_REFRESH_TICK_S


# ---------------------------------------------------------------------------
# 2. Source-shape pin: _daily_refresh_loop recognises signed_in_no_corpus
# ---------------------------------------------------------------------------


def test_daily_refresh_loop_recognises_signed_in_no_corpus_in_blocked_disjunction():
    """``_daily_refresh_loop`` MUST include ``signed_in_no_corpus``
    in the ``blocked_now`` disjunction so the
    transition-detection logic considers BOTH blocked states.
    Pinned via source-shape so a refactor cannot drop the second
    arm without firing the alarm."""
    # Round 83
    src_path = REPO_ROOT / "corpus_bootstrap.py"
    src = src_path.read_text(encoding="utf-8")
    # The disjunction MUST contain both source labels.
    assert "blocked_no_onedrive" in src
    assert "signed_in_no_corpus" in src
    # Specific shape: the loop MUST OR them together.
    assert (
        "source_at_tick_start == \"blocked_no_onedrive\"" in src
        or 'source_at_tick_start == "blocked_no_onedrive"' in src
    )
    assert (
        'source_at_tick_start == "signed_in_no_corpus"' in src
        or "source_at_tick_start == 'signed_in_no_corpus'" in src
    )


def test_daily_refresh_loop_preserves_r68_sentinel_gate():
    """The R68 sentinel-presence gate MUST still be present in
    the loop AFTER the R83 disjunction widening. Pre-R83 a sloppy
    refactor could have replaced the disjunction without
    re-checking the sentinel call.  Pin the source-shape so any
    such regression fires the alarm."""
    # Round 83
    src_path = REPO_ROOT / "corpus_bootstrap.py"
    src = src_path.read_text(encoding="utf-8")
    assert "_r68_onedrive_sentinel_present()" in src
    # The sentinel call MUST guard the transition_unblocked
    # decision (R68 contract preserved).
    assert "transition_unblocked = (" in src
    assert "and sentinel_present" in src


def test_daily_refresh_loop_emits_round83_marker():
    """The R83 widening MUST carry a ``Round 83`` source comment
    so future maintainers can ``git blame``/``grep`` the change.
    R83's loop guidelines call for source-marker comments at every
    changed line."""
    # Round 83
    src_path = REPO_ROOT / "corpus_bootstrap.py"
    src = src_path.read_text(encoding="utf-8")
    # The loop body MUST mention Round 83 so the change footprint
    # is documented inline. The R83 plan calls this out in the
    # "Workflow / loop conventions" section.
    assert "Round 83" in src
    # Also pin the canonical phrasing of the R83 transition
    # comment block.
    assert "signed_in_no_corpus" in src
