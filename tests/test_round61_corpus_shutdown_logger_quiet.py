"""Round 61 / Phase 2.E regression test: the daily-refresh worker's
exit log MUST NOT produce "I/O operation on closed file" noise on
stderr when the underlying logging stream has been closed (e.g. at
pytest teardown).

The fix is in ``corpus_bootstrap._daily_refresh_loop``: instead of
trying to wrap ``logger.info()`` in try/except (which is INSUFFICIENT
because Python's logging module catches StreamHandler.emit() failures
internally and reports via ``Handler.handleError()`` -> stderr
regardless of any outer try/except), the exit log is now gated on
``_exit_log_streams_open()`` -- a defensive walk of the logger's
handler chain that returns False if ANY reachable StreamHandler is
wired to a closed stream.  When False, the exit log is skipped
entirely, so ``Handler.handleError()`` is never invoked.

This file pins:
1. The exit log emission is skipped when the stream is closed
   (no ValueError raised, no traceback printed to stderr).
2. ``_exit_log_streams_open()`` correctly returns False when a
   reachable handler is closed, True otherwise.
3. Real-run behavior unchanged: when the stream is open, the log
   line is emitted exactly once.
"""
from __future__ import annotations

import io
import logging
import threading


def _build_logger_with_stream_handler(closed: bool) -> tuple[logging.Logger, io.StringIO, logging.StreamHandler]:
    """Construct an isolated logger whose only handler writes to a
    StringIO -- optionally already closed."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger(
        f"test_round61_corpus_shutdown_logger_quiet_{id(stream)}"
    )
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if closed:
        stream.close()
    return logger, stream, handler


def test_exit_log_skipped_when_stream_closed(monkeypatch, capsys):
    """Pre-Round-61 this test would surface a "Logging error" traceback
    on stderr.  Post-Round-61 the gate keeps the daemon thread fully
    silent when the stream is closed."""
    import corpus_bootstrap

    bad_logger, _stream, _handler = _build_logger_with_stream_handler(closed=True)
    monkeypatch.setattr(corpus_bootstrap, "logger", bad_logger)
    monkeypatch.setattr(corpus_bootstrap, "_DAILY_REFRESH_STOP", _PreSetEvent())

    corpus_bootstrap._daily_refresh_loop()

    captured = capsys.readouterr()
    # The whole point: NO "Logging error" / "ValueError" / traceback
    # should reach stderr.
    assert "ValueError" not in captured.err, (
        f"closed-stream exit log leaked a Logging error: {captured.err!r}"
    )
    assert "Logging error" not in captured.err, (
        f"closed-stream exit log leaked a Logging error: {captured.err!r}"
    )


def test_exit_log_emits_once_when_stream_is_open(monkeypatch):
    """Real-run behavior contract: when the stream is open, the exit
    log line is emitted exactly once.  Guards against accidentally
    suppressing the log under normal operation."""
    import corpus_bootstrap

    captured: list[str] = []

    class _CapturingLogger:
        propagate = False
        parent = None
        handlers: list = []  # empty handlers list -> _exit_log_streams_open returns True

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


def test_exit_log_streams_open_returns_true_for_open_stream():
    """Direct contract test for the new helper."""
    import corpus_bootstrap

    open_logger, _stream, _handler = _build_logger_with_stream_handler(closed=False)

    # Save and substitute the module-level logger via monkeypatch-style swap.
    saved = corpus_bootstrap.logger
    try:
        corpus_bootstrap.logger = open_logger
        assert corpus_bootstrap._exit_log_streams_open() is True
    finally:
        corpus_bootstrap.logger = saved


def test_exit_log_streams_open_returns_false_for_closed_stream():
    """Direct contract test for the new helper."""
    import corpus_bootstrap

    bad_logger, _stream, _handler = _build_logger_with_stream_handler(closed=True)

    saved = corpus_bootstrap.logger
    try:
        corpus_bootstrap.logger = bad_logger
        assert corpus_bootstrap._exit_log_streams_open() is False
    finally:
        corpus_bootstrap.logger = saved


def test_exit_log_streams_open_returns_true_when_no_handlers():
    """A logger with no handlers cannot raise -- the gate must allow
    the emission to proceed (logging will silently no-op)."""
    import corpus_bootstrap

    bare = logging.getLogger("test_round61_bare_logger")
    for h in list(bare.handlers):
        bare.removeHandler(h)
    bare.propagate = False

    saved = corpus_bootstrap.logger
    try:
        corpus_bootstrap.logger = bare
        assert corpus_bootstrap._exit_log_streams_open() is True
    finally:
        corpus_bootstrap.logger = saved


def test_exit_log_streams_open_walks_propagation_chain():
    """When the immediate logger has no handlers but a parent does,
    the gate walks up the chain.  Closed parent handler -> False."""
    import corpus_bootstrap

    parent_stream = io.StringIO()
    parent_handler = logging.StreamHandler(parent_stream)
    parent = logging.getLogger("test_round61_propagation_parent")
    for h in list(parent.handlers):
        parent.removeHandler(h)
    parent.addHandler(parent_handler)
    parent.propagate = False
    parent_stream.close()

    child = logging.getLogger("test_round61_propagation_parent.child")
    for h in list(child.handlers):
        child.removeHandler(h)
    child.propagate = True
    child.parent = parent

    saved = corpus_bootstrap.logger
    try:
        corpus_bootstrap.logger = child
        assert corpus_bootstrap._exit_log_streams_open() is False
    finally:
        corpus_bootstrap.logger = saved


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
