"""Round 63 / Tier A: pin the shared closed-stream defense helpers.

R62 / A1 shipped the closed-stream gate as duplicated copies in
``corpus_bootstrap.py`` and ``corpus_indexer.py`` (the latter could
not import the former because of a cycle).  R63 promotes the
implementation to ``_logging_helpers.py`` and keeps thin wrappers in
both consumers so the R61 + R62 tests keep passing.

These tests pin the shared module directly (independent of the two
wrapper sites).  The wrapper-end of the contract is covered by:

- ``tests/test_round61_corpus_shutdown_logger_quiet.py`` (the
  ``corpus_bootstrap._safe_log_info`` wrapper),
- ``tests/test_round62_corpus_logger_module_wide.py`` (the same
  wrapper, plus the regression-count guard for ``corpus_bootstrap``),
- ``tests/test_round63_corpus_indexer_logger_parity.py`` (the
  ``corpus_indexer`` wrapper + the parallel regression-count guard).
"""

from __future__ import annotations

import io
import logging

import pytest

import _logging_helpers


_LOGGER_NAME = "adoptiq_test_round63_logging_helpers"


@pytest.fixture
def isolated_logger():
    """Fresh, isolated module-style logger per test so the helpers
    can probe / emit without touching the global root.

    Saves and restores any prior handler state defensively even
    though the logger name is test-private.
    """
    target = logging.getLogger(_LOGGER_NAME)
    saved_handlers = list(target.handlers)
    saved_level = target.level
    saved_propagate = target.propagate
    target.handlers.clear()
    target.propagate = False
    target.setLevel(logging.DEBUG)
    yield target
    target.handlers.clear()
    for h in saved_handlers:
        target.addHandler(h)
    target.setLevel(saved_level)
    target.propagate = saved_propagate


def _attach_stream_handler(target: logging.Logger, stream: io.IOBase) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    target.addHandler(handler)
    return handler


# ---------------------------------------------------------------------------
# safe_log_info contract
# ---------------------------------------------------------------------------


def test_safe_log_info_emits_when_stream_open(isolated_logger):
    buf = io.StringIO()
    _attach_stream_handler(isolated_logger, buf)
    _logging_helpers.safe_log_info(isolated_logger, "Round 63 test: %s", "open-stream-emits")
    out = buf.getvalue()
    assert "Round 63 test: open-stream-emits" in out
    assert out.count("Round 63 test:") == 1


def test_safe_log_info_skipped_when_stream_closed_no_stderr_traceback(isolated_logger, capsys):
    """Closed stream MUST silence emission entirely.  Python's logging
    module otherwise would route the failure through
    ``Handler.handleError()`` and dump the traceback to stderr."""
    buf = io.StringIO()
    _attach_stream_handler(isolated_logger, buf)
    buf.close()
    _logging_helpers.safe_log_info(
        isolated_logger, "Round 63 test: %s", "closed-stream-suppressed"
    )
    captured = capsys.readouterr()
    assert "Logging error" not in captured.err
    assert "I/O operation on closed file" not in captured.err
    assert "ValueError" not in captured.err


# ---------------------------------------------------------------------------
# exit_log_streams_open contract
# ---------------------------------------------------------------------------


def test_exit_log_streams_open_returns_true_for_open_stream(isolated_logger):
    buf = io.StringIO()
    _attach_stream_handler(isolated_logger, buf)
    assert _logging_helpers.exit_log_streams_open(isolated_logger) is True


def test_exit_log_streams_open_returns_false_for_closed_stream(isolated_logger):
    buf = io.StringIO()
    _attach_stream_handler(isolated_logger, buf)
    buf.close()
    assert _logging_helpers.exit_log_streams_open(isolated_logger) is False


def test_exit_log_streams_open_walks_propagation_chain(isolated_logger, capsys):
    """If the local logger has clean handlers but a propagation-reachable
    parent has a closed-stream handler, the helper MUST still return
    False (because Python's logging module will route the record up
    the chain and the parent handler's ``handleError`` would dump the
    traceback to stderr)."""
    isolated_logger.handlers.clear()
    isolated_logger.propagate = True
    parent = logging.getLogger()
    saved_root_handlers = list(parent.handlers)
    parent.handlers.clear()
    closed_buf = io.StringIO()
    parent.addHandler(logging.StreamHandler(closed_buf))
    closed_buf.close()
    try:
        assert _logging_helpers.exit_log_streams_open(isolated_logger) is False
        _logging_helpers.safe_log_info(isolated_logger, "Round 63 test: %s", "parent-closed")
        captured = capsys.readouterr()
        assert "Logging error" not in captured.err
        assert "I/O operation on closed file" not in captured.err
    finally:
        parent.handlers.clear()
        for h in saved_root_handlers:
            parent.addHandler(h)


def test_propagate_default_is_true_matching_logging_module():
    """Pin the canonical-default choice: when probing a logger that is
    missing the ``propagate`` attribute entirely (extremely unlikely
    in practice -- ``logging.Logger.__init__`` always sets it -- but
    possible for a custom subclass), the helper MUST treat propagation
    as enabled (Python's actual default in ``Logger.__init__``).

    R62's ``corpus_bootstrap`` copy used ``False`` here, the indexer
    copy used ``True``.  R63 unifies on ``True`` so the chain walk
    matches ``logging.Logger`` semantics.  Pinning this prevents a
    future refactor from quietly flipping the default back."""

    class StubLogger:
        """Minimal duck type with handlers but no ``propagate``.
        ``parent = None`` terminates the walk after one iteration."""

        def __init__(self) -> None:
            self.handlers: list[logging.Handler] = []
            self.parent: logging.Logger | None = None

    stub = StubLogger()
    # The helper must return True (open) and not raise on the missing
    # ``propagate`` attribute.  If the default were False, the chain
    # walk would terminate early but that does not change the answer
    # here because there are no closed handlers to find.  The real
    # behavioral difference is captured by the propagation-chain test
    # above; this test pins the source-shape choice explicitly.
    assert _logging_helpers.exit_log_streams_open(stub) is True  # type: ignore[arg-type]

    src = open(_logging_helpers.__file__, encoding="utf-8").read()
    assert 'getattr(cur, "propagate", True)' in src, (
        "Round 63 / Tier A: _logging_helpers.exit_log_streams_open MUST "
        "use True as the default for the propagate getattr lookup, matching "
        "logging.Logger.__init__'s actual default."
    )
