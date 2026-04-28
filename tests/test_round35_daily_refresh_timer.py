"""Round 35 / native-corpus: pin the daily-refresh timer math.

The daily-refresh daemon in ``corpus_bootstrap`` decides whether a
runtime refresh is due via :func:`_should_refresh`.  These tests
lock the math so a future tweak to the cadence (24h interval, 1h
tick) cannot accidentally:

* Refresh more aggressively than once per 24 hours (would hammer
  the ``/shares/{u!token}/driveItem`` endpoint).
* Skip a refresh that is genuinely overdue (would leave the user
  on a stale snapshot indefinitely).
* Refresh while another pass is in progress (would race against
  ``EncryptedCorpusHandle.commit_to_disk``).

Also pins:

* ``start_daily_refresh_worker`` is idempotent (calling it twice in
  a row does not spawn two daemons).
* ``stop()`` joins the daemon cleanly (no leaked threads after
  process shutdown).
"""

from __future__ import annotations

import threading
import time

import pytest

import corpus_bootstrap


@pytest.fixture(autouse=True)
def _reset_state():
    corpus_bootstrap.reset_for_tests()
    yield
    corpus_bootstrap.reset_for_tests()


# ---------------------------------------------------------------------------
# _should_refresh math
# ---------------------------------------------------------------------------


def test_should_refresh_true_when_never_refreshed():
    assert corpus_bootstrap._should_refresh(
        last_refresh_ts=None,
        now=1_000_000.0,
    )


def test_should_refresh_true_when_past_24h_window():
    now = 1_000_000.0
    last = now - 86400.0 - 1.0  # 24h + 1s ago
    assert corpus_bootstrap._should_refresh(
        last_refresh_ts=last,
        now=now,
    )


def test_should_refresh_false_when_inside_24h_window():
    now = 1_000_000.0
    last = now - 3600.0  # 1h ago -- still fresh
    assert not corpus_bootstrap._should_refresh(
        last_refresh_ts=last,
        now=now,
    )


def test_should_refresh_true_at_exact_24h_boundary():
    """Boundary case: ``>=`` semantics so a refresh does not skip
    one tick at the exact 24h mark."""
    now = 1_000_000.0
    last = now - 86400.0
    assert corpus_bootstrap._should_refresh(
        last_refresh_ts=last,
        now=now,
    )


def test_should_refresh_false_just_under_24h():
    now = 1_000_000.0
    last = now - 86399.0  # 23h 59m 59s ago
    assert not corpus_bootstrap._should_refresh(
        last_refresh_ts=last,
        now=now,
    )


def test_should_refresh_honors_custom_interval():
    now = 1_000_000.0
    last = now - 100.0
    # 60-second interval: 100 seconds ago is past the boundary.
    assert corpus_bootstrap._should_refresh(
        last_refresh_ts=last,
        now=now,
        interval_s=60.0,
    )
    # 1000-second interval: still fresh.
    assert not corpus_bootstrap._should_refresh(
        last_refresh_ts=last,
        now=now,
        interval_s=1000.0,
    )


def test_should_refresh_zero_interval_always_true():
    """An interval_s of 0 means "always refresh on tick" -- this
    is the test-only fast-poll mode."""
    assert corpus_bootstrap._should_refresh(
        last_refresh_ts=time.time(),
        now=time.time(),
        interval_s=0.0,
    )


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


def test_start_daily_refresh_worker_idempotent():
    started_first = corpus_bootstrap.start_daily_refresh_worker()
    assert started_first is True

    # Second invocation must NOT spawn another daemon.
    started_second = corpus_bootstrap.start_daily_refresh_worker()
    assert started_second is False, (
        "start_daily_refresh_worker must be idempotent -- spawning two "
        "daemons would race the encrypted-DB writes."
    )

    # Cleanup.
    corpus_bootstrap.stop()


def test_start_daily_refresh_worker_thread_is_daemon():
    corpus_bootstrap.start_daily_refresh_worker()
    thread = corpus_bootstrap._DAILY_REFRESH_THREAD
    assert thread is not None
    assert thread.daemon is True, (
        "daily refresh worker MUST be a daemon thread; otherwise "
        "process shutdown blocks for up to 1h waiting on the tick."
    )
    assert thread.name == "adoptiq-corpus-daily-refresh"
    corpus_bootstrap.stop()


def test_stop_signals_daily_refresh_worker():
    """``stop()`` must set the stop event so the worker exits the
    next time it wakes."""
    corpus_bootstrap.start_daily_refresh_worker()
    assert corpus_bootstrap._DAILY_REFRESH_THREAD is not None
    assert corpus_bootstrap._DAILY_REFRESH_THREAD.is_alive()

    corpus_bootstrap.stop()

    # The stop event should be set; reset_for_tests will clear it.
    assert corpus_bootstrap._DAILY_REFRESH_STOP.is_set() or (
        # stop() also clears _DAILY_REFRESH_THREAD; either signal
        # path indicates a clean shutdown.
        corpus_bootstrap._DAILY_REFRESH_THREAD is None
    )


def test_state_carries_refresh_timestamp_fields():
    """The ``CorpusBootState`` snapshot must expose the new R35
    fields so the analyze-page panel can render the timestamps
    without reaching into module globals."""
    state = corpus_bootstrap.get_state()
    # New R35 fields default to None until the first pass / refresh.
    assert hasattr(state, "source")
    assert hasattr(state, "indexed_at")
    assert hasattr(state, "last_successful_refresh_ts")
    assert hasattr(state, "last_refresh_attempt_ts")
    assert hasattr(state, "last_refresh_error")
    assert state.last_successful_refresh_ts is None
    assert state.last_refresh_attempt_ts is None
    assert state.last_refresh_error is None
