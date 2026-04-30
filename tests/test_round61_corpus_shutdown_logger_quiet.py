"""Round 61 / Phase 2.E regression test: the daily-refresh worker's
exit log MUST NOT raise when the underlying logging stream has been
closed (the symptom from the Round 58 / Round 59 deferral about
pytest-teardown noise).

The fix is at ``corpus_bootstrap._daily_refresh_loop``'s exit log
emission, which is now wrapped in a narrow ``try/except (ValueError,
OSError)`` so the otherwise-cosmetic ``"I/O operation on closed file"``
ValueError raised by the StreamHandler at process shutdown is swallowed.

This file pins:
1. The exit log emission survives a closed stream (no exception bubbles).
2. The wrapper does NOT swallow non-IO exceptions (a coding error in
   the log call itself would still surface).
3. Real-run behavior unchanged: when the stream is open, the log line
   is emitted exactly once.
"""
from __future__ import annotations

import io
import logging
import threading
from unittest.mock import patch


def _build_closed_stream_logger() -> tuple[logging.Logger, io.StringIO]:
    """Construct an isolated logger whose only handler writes to an
    already-closed StringIO.  Reproduces the pytest-teardown failure
    mode without touching the global logging config."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger("test_round61_corpus_shutdown_logger_quiet")
    # Reset so reruns see a clean handler list.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    # Close the stream so the next emit() raises ValueError("I/O
    # operation on closed file"), exactly as pytest teardown does.
    stream.close()
    return logger, stream


def test_exit_log_does_not_raise_on_closed_stream(monkeypatch):
    """Reproduce the pytest-teardown failure mode and confirm the R61
    wrapper swallows it.

    Pre-Round-61 this test would surface a ValueError raised through the
    StreamHandler.emit() chain.  Post-Round-61 the swallow keeps the
    daemon thread quiet."""
    import corpus_bootstrap

    # Build a logger whose stream is already closed and substitute it
    # for the module-level ``logger`` so the exit-log path uses it.
    bad_logger, _stream = _build_closed_stream_logger()
    monkeypatch.setattr(corpus_bootstrap, "logger", bad_logger)

    # Make the loop exit immediately so we hit ONLY the exit-log path.
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_STOP", _PreSetEvent())

    # Should NOT raise ValueError("I/O operation on closed file").
    corpus_bootstrap._daily_refresh_loop()


def test_exit_log_does_not_swallow_unrelated_errors(monkeypatch):
    """The wrapper is narrow: TypeError / RuntimeError / KeyError still
    bubble.  This guards against the wrapper expanding silently in a
    future refactor and hiding real bugs in the exit-log call site
    itself."""
    import corpus_bootstrap

    class _BoomLogger:
        def info(self, *_a, **_k):
            raise RuntimeError("boom: not an IO error")

        def warning(self, *_a, **_k):
            pass

        def debug(self, *_a, **_k):
            pass

    monkeypatch.setattr(corpus_bootstrap, "logger", _BoomLogger())
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_STOP", _PreSetEvent())

    # RuntimeError MUST bubble — proves the except clause is narrow.
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="boom: not an IO error"):
        corpus_bootstrap._daily_refresh_loop()


def test_exit_log_emits_once_when_stream_is_open(monkeypatch):
    """Real-run behavior contract: when the stream is open, the exit
    log line is emitted exactly once.  Guards against accidentally
    suppressing the log under normal operation."""
    import corpus_bootstrap

    captured: list[str] = []

    class _CapturingLogger:
        def info(self, msg, *args, **_k):
            captured.append(msg % args if args else msg)

        def warning(self, *_a, **_k):
            pass

        def debug(self, *_a, **_k):
            pass

    monkeypatch.setattr(corpus_bootstrap, "logger", _CapturingLogger())
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_STOP", _PreSetEvent())

    corpus_bootstrap._daily_refresh_loop()

    exit_msgs = [m for m in captured if "daily refresh worker exiting" in m]
    assert len(exit_msgs) == 1, (
        f"expected exactly one exit log emission, got {len(exit_msgs)}: "
        f"{captured!r}"
    )


class _PreSetEvent:
    """Stand-in for ``threading.Event`` whose ``.wait()`` returns True
    immediately so the loop exits after one tick.  ``.is_set()`` is
    True so the ``while not _DAILY_REFRESH_STOP.is_set():`` guard
    short-circuits and we land directly on the exit log."""

    def __init__(self) -> None:
        self._evt = threading.Event()
        self._evt.set()

    def is_set(self) -> bool:
        return True

    def wait(self, _timeout: float) -> bool:
        return True

    def set(self) -> None:
        self._evt.set()

    def clear(self) -> None:
        pass
