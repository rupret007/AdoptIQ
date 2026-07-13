"""Round 63 / Tier A: shared closed-stream defense for ``logger.info``.

This module is the single source of truth for the closed-stream gate
that R61 / Phase 2.E first shipped inside ``corpus_bootstrap`` and that
R62 / A1 had to duplicate inside ``corpus_indexer`` (because
``corpus_bootstrap`` already imports from ``corpus_indexer`` and a
direct import in the other direction would create a cycle).

Both call sites are kept as thin wrappers in their original modules so
that pre-existing tests that reference ``corpus_bootstrap._safe_log_info``
/ ``corpus_bootstrap._exit_log_streams_open`` (and the parallel names
in ``corpus_indexer``) continue to work without churn -- the actual
logic lives here.

The ``propagate`` default chosen here is ``True``, matching
``logging.Logger.__init__`` (which always sets ``propagate = True``).
The Round 62 implementation in ``corpus_bootstrap`` defaulted to
``False``; the indexer copy already used ``True``.  R63 unifies on the
more-correct default so a future ``logging.Logger`` subclass that
omits the attribute (extremely unlikely in practice) walks the chain
the same way Python itself would.

Why a try/except wrap is NOT enough on its own: Python's ``logging``
module catches handler-level exceptions internally inside
``Handler.emit()`` / ``Handler.handleError()`` and writes the
traceback to ``sys.stderr`` regardless of any ``try: ... except:``
wrapped around ``logger.info(...)``.  The only reliable way to
silence the cosmetic ``"I/O operation on closed file"`` traceback is
to detect the closed-stream condition BEFORE calling ``logger.info``.

Pinned by:
    tests/test_round63_logging_helpers.py
    tests/test_round63_corpus_indexer_logger_parity.py
    tests/test_round62_corpus_logger_module_wide.py (via wrapper)
    tests/test_round61_corpus_shutdown_logger_quiet.py (via wrapper)
"""

from __future__ import annotations

import logging


def exit_log_streams_open(target_logger: logging.Logger) -> bool:
    """True when every reachable ``StreamHandler`` from ``target_logger``
    has an open underlying stream.  Returns False when ANY reachable
    handler is closed (the signal to skip the log emission).

    Walks the propagation chain (parent loggers) so a closed handler
    on the root logger blocks emission too -- because Python routes
    the record up the chain and the root handler's
    ``Handler.handleError()`` would still write to ``sys.stderr``.

    The walk terminates either at the root or at the first logger
    whose ``propagate`` attribute is False.  The default for the
    ``propagate`` ``getattr`` lookup is ``True`` so this matches
    ``logging.Logger`` semantics (Python's logger always sets
    ``propagate = True`` in ``__init__``; the default here only
    matters if a custom logger subclass omits the attribute).
    """
    seen: set[int] = set()
    cur: logging.Logger | None = target_logger
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        for handler in getattr(cur, "handlers", []):
            stream = getattr(handler, "stream", None)
            if stream is None:
                continue
            try:
                if getattr(stream, "closed", False):
                    return False
            except Exception:  # noqa: BLE001 - defensive: any probe failure is treated as closed
                return False
        if not getattr(cur, "propagate", True):
            break
        cur = getattr(cur, "parent", None)
    return True


def _safe_log_level(
    target_logger: logging.Logger,
    level_name: str,
    msg: str,
    *args: object,
    **kwargs: object,
) -> None:
    """Round 97.2: shared closed-stream gate for logger level methods."""
    if exit_log_streams_open(target_logger):
        try:
            getattr(target_logger, level_name)(msg, *args, **kwargs)
        except (ValueError, OSError):
            pass


def safe_log_info(target_logger: logging.Logger, msg: str, *args: object) -> None:
    """Emit ``target_logger.info(msg, *args)`` only when every reachable
    ``StreamHandler`` has an open underlying stream.

    Real runs (open streams) emit normally.  Pytest teardown runs
    (where the test session has already closed ``capsys``-attached
    streams) silently no-op so the ``"I/O operation on closed file"``
    cosmetic traceback never appears.

    The inner try/except is belt-and-suspenders for the narrow race
    where a stream is open at the gate check and closed by the time
    ``logger.info`` actually emits; both ``ValueError`` (Python's
    "operation on closed file") and ``OSError`` (other I/O failures)
    are swallowed silently because by definition we are mid-shutdown
    and cannot recover.
    """
    _safe_log_level(target_logger, "info", msg, *args)


def safe_log_warning(target_logger: logging.Logger, msg: str, *args: object) -> None:
    """Round 97.2: warning-level sibling for shutdown/teardown races."""
    _safe_log_level(target_logger, "warning", msg, *args)


def safe_log_exception(target_logger: logging.Logger, msg: str, *args: object) -> None:
    """Round 101: exception-level sibling for shutdown/teardown races."""
    _safe_log_level(target_logger, "exception", msg, *args)
