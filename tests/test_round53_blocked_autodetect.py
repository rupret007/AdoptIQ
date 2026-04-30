"""Round 53 / Phase 53.4.2: pin the accelerated 30-second poll loop
that auto-detects when the user finishes signing in to OneDrive.

Background
----------
Without this UX helper, a user who hits the
``blocked_no_onedrive`` state on first launch would have to wait
up to an hour (the daily-refresh tick) for the panel to notice
that they finished syncing OneDrive.  The user explicitly called
out that this would feel like a hindrance ("the point is to make
it easy and simple to use").

Round 53 / Phase 53.4.2 adds:

* ``_DAILY_REFRESH_TICK_BLOCKED_S = 30.0`` -- accelerated tick.
* ``_DAILY_REFRESH_BLOCKED_MAX_TICKS = 240`` -- bound on accelerated
  ticks (240 * 30s = 2h before falling back to hourly so an
  unsynced user does not get a perpetual 30-second polling loop).
* ``_next_refresh_tick_s(blocked_streak)`` -- helper that the loop
  calls to decide how long to sleep.
* Loop logic: while observing the blocked state, increment
  ``blocked_streak``; on a blocked-to-synced transition, kick an
  immediate refresh regardless of the 24h window.

These tests pin the helper logic + the constants.  Pinning the
loop body itself end-to-end is unsafe in unit tests (the loop
sleeps and runs in a thread), so the loop's structural contract
is also covered via source-text inspection -- a regression that
removes the transition-trigger or the bounded-streak logic would
be caught by the source asserts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import corpus_bootstrap


# ---------------------------------------------------------------------------
# Constants pin
# ---------------------------------------------------------------------------


def test_blocked_tick_constant_is_30_seconds():
    """The accelerated tick must be 30 seconds.  Slower than this
    misses the UX goal; faster wastes CPU."""
    assert corpus_bootstrap._DAILY_REFRESH_TICK_BLOCKED_S == 30.0, (
        f"_DAILY_REFRESH_TICK_BLOCKED_S != 30.0; got "
        f"{corpus_bootstrap._DAILY_REFRESH_TICK_BLOCKED_S}.  Pinned by "
        f"the Round 53 UX contract -- changing this needs an "
        f"explicit handoff entry."
    )


def test_blocked_max_ticks_caps_at_2_hours():
    """The accelerated cap (in ticks) must produce a 2-hour fallback
    window.  Different cap == different fallback duration; the user
    visible behavior shifts."""
    cap_ticks = corpus_bootstrap._DAILY_REFRESH_BLOCKED_MAX_TICKS
    cap_seconds = cap_ticks * corpus_bootstrap._DAILY_REFRESH_TICK_BLOCKED_S
    expected = 2 * 3600.0
    assert cap_seconds == expected, (
        f"blocked-tick cap is {cap_seconds}s "
        f"({cap_ticks} ticks * "
        f"{corpus_bootstrap._DAILY_REFRESH_TICK_BLOCKED_S}s); "
        f"expected {expected}s (2 hours).  Pinned by the Round 53 "
        f"UX contract."
    )


def test_normal_tick_is_one_hour():
    """The standard tick is one hour (the pre-Round-53 contract).
    Round 53 must NOT change this -- only the *blocked* tick is new."""
    assert corpus_bootstrap._DAILY_REFRESH_TICK_S == 3600.0


# ---------------------------------------------------------------------------
# _next_refresh_tick_s helper
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch):
    """Reset _STATE between tests so we don't carry blocked_no_onedrive
    over into a sibling test."""
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


def _set_source(value):
    with corpus_bootstrap._BOOT_LOCK:
        corpus_bootstrap._STATE.source = value


def test_next_tick_returns_blocked_interval_when_source_blocked():
    """When ``_STATE.source == 'blocked_no_onedrive'`` and we have
    not hit the cap, the helper returns the 30s tick."""
    _set_source("blocked_no_onedrive")
    assert corpus_bootstrap._next_refresh_tick_s(0) == 30.0
    assert corpus_bootstrap._next_refresh_tick_s(50) == 30.0
    assert corpus_bootstrap._next_refresh_tick_s(
        corpus_bootstrap._DAILY_REFRESH_BLOCKED_MAX_TICKS - 1
    ) == 30.0


def test_next_tick_falls_back_to_hourly_at_cap():
    """At the cap (240 ticks) the helper must revert to hourly.
    Without this an unsynced user would get a perpetual 30-second
    polling loop."""
    _set_source("blocked_no_onedrive")
    assert corpus_bootstrap._next_refresh_tick_s(
        corpus_bootstrap._DAILY_REFRESH_BLOCKED_MAX_TICKS
    ) == 3600.0
    # And anything past the cap also returns hourly.
    assert corpus_bootstrap._next_refresh_tick_s(10_000) == 3600.0


def test_next_tick_returns_hourly_when_not_blocked():
    """When the corpus is in any state OTHER than
    ``blocked_no_onedrive`` the helper returns the standard
    hourly tick regardless of the streak counter (the streak only
    accumulates while blocked)."""
    for state in ("baked", "fresh", "self_healed_baked", None):
        _set_source(state)
        assert corpus_bootstrap._next_refresh_tick_s(0) == 3600.0
        assert corpus_bootstrap._next_refresh_tick_s(50) == 3600.0


def test_next_tick_ignores_streak_when_state_clears():
    """A non-zero streak inherited from a prior blocked period must
    NOT keep the loop on the accelerated tick after the state
    clears -- the helper checks the live state, not the streak."""
    _set_source("baked")
    assert corpus_bootstrap._next_refresh_tick_s(100) == 3600.0


def test_next_tick_accepts_negative_streak_safely():
    """Defense in depth: a malformed streak counter (negative) must
    not crash the helper.  Returns the blocked tick because the cap
    check uses ``<`` so a negative number trivially passes."""
    _set_source("blocked_no_onedrive")
    # A negative streak is < cap, so still returns blocked tick.
    assert corpus_bootstrap._next_refresh_tick_s(-1) == 30.0


# ---------------------------------------------------------------------------
# Loop body source-text contract
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent
_BOOTSTRAP_PATH = _REPO_ROOT / "corpus_bootstrap.py"


def test_loop_uses_next_refresh_tick_s():
    """The daily-refresh loop must use the new helper.  A regression
    that hard-codes the hourly tick would silently revert the UX
    fix even if the helper still exists."""
    src = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    assert "_next_refresh_tick_s" in src, (
        "corpus_bootstrap.py does not reference _next_refresh_tick_s "
        "-- the daily-refresh loop is not using the Round 53 helper."
    )


def test_loop_detects_blocked_to_synced_transition():
    """The loop must trigger an IMMEDIATE refresh when it observes a
    blocked-to-synced transition -- without this the user would
    have to wait the full 24h refresh window."""
    src = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    assert "transition_unblocked" in src, (
        "corpus_bootstrap.py does not include the blocked-to-synced "
        "transition trigger; users would have to wait up to 24h "
        "after signing in to OneDrive before the corpus unlocks."
    )


def test_loop_increments_blocked_streak():
    """The loop must count ticks spent in the blocked state so the
    helper can decide when to revert to hourly."""
    src = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    assert "blocked_streak" in src, (
        "corpus_bootstrap.py does not maintain a blocked_streak "
        "counter; the bounded retry semantics are missing."
    )


def test_loop_resets_blocked_streak_on_state_clear():
    """The streak counter must reset to 0 when the state clears,
    otherwise a later blocked period would inherit the prior count
    and prematurely fall back to hourly."""
    src = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    # The reset shows up as ``blocked_streak = 0`` somewhere in the
    # loop body (multiple branches reset it).
    assert "blocked_streak = 0" in src, (
        "corpus_bootstrap.py does not reset blocked_streak when the "
        "state clears.  A later blocked period would inherit the "
        "prior count and prematurely fall back to hourly."
    )
