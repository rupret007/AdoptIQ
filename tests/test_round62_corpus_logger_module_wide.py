"""Round 62 / Phase A1: pin module-wide closed-stream defense for the
corpus_bootstrap logger.

R61 / Phase 2.E shipped ``_exit_log_streams_open()`` and gated the
daemon's start + exit log lines on it.  R62 / A1 introduces the
``_safe_log_info()`` helper that wraps the same gate + try/except
contract, and replaces every other ``logger.info()`` call site in
``corpus_bootstrap.py`` (10 additional sites covering the bake
install, daily-refresh trigger, blocked-state surface, TOCTOU race,
and per-source/aggregate index-pass logs).

These tests pin three contracts:

1. The helper itself behaves the same as the R61 helper across the
   open / closed / no-handlers / propagation-chain cases (smoke).
2. A real-world call site (the index-pass per-source log) emits
   exactly once when streams are open and emits zero ``Logging
   error`` tracebacks to stderr when streams are closed.
3. A regression count: the production source has ZERO bare
   ``logger.info(`` call sites outside the helper definition body
   itself, so a future change that adds a new ``logger.info`` call
   without going through ``_safe_log_info`` is caught immediately.
"""

from __future__ import annotations

import io
import logging
import re
import sys
from pathlib import Path
from typing import List

import pytest

import corpus_bootstrap


# ---------------------------------------------------------------------------
# Helper smoke (parity with R61's _exit_log_streams_open helper)
# ---------------------------------------------------------------------------


def _attach_stream_handler_to_module_logger(stream: io.IOBase) -> logging.Handler:
    """Attach a fresh StreamHandler to corpus_bootstrap.logger and
    isolate the test by clearing pre-existing handlers + propagation.
    """
    target = corpus_bootstrap.logger
    target.handlers.clear()
    target.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    return handler


@pytest.fixture
def isolated_logger():
    """Save and restore the module logger's state per test so we don't
    pollute other test files in the same pytest session."""
    target = corpus_bootstrap.logger
    saved_handlers = list(target.handlers)
    saved_level = target.level
    saved_propagate = target.propagate
    yield target
    target.handlers.clear()
    for h in saved_handlers:
        target.addHandler(h)
    target.setLevel(saved_level)
    target.propagate = saved_propagate


def test_safe_log_info_emits_once_when_stream_is_open(isolated_logger):
    buf = io.StringIO()
    _attach_stream_handler_to_module_logger(buf)
    corpus_bootstrap._safe_log_info("Round 62 test: %s", "open-stream-emits")
    assert "Round 62 test: open-stream-emits" in buf.getvalue()
    assert buf.getvalue().count("Round 62 test:") == 1


def test_safe_log_info_skipped_when_stream_closed(isolated_logger, capsys):
    buf = io.StringIO()
    _attach_stream_handler_to_module_logger(buf)
    buf.close()
    corpus_bootstrap._safe_log_info("Round 62 test: %s", "closed-stream-suppressed")
    captured = capsys.readouterr()
    assert "Logging error" not in captured.err
    assert "I/O operation on closed file" not in captured.err
    assert "ValueError" not in captured.err


def test_safe_log_info_no_handlers_emits_silently(isolated_logger):
    target = corpus_bootstrap.logger
    target.handlers.clear()
    target.propagate = False
    corpus_bootstrap._safe_log_info("Round 62 test: %s", "no-handlers")


def test_safe_log_info_walks_propagation_chain(isolated_logger, capsys):
    """If our handlers are clean but a parent's handler is wired to a
    closed stream, the helper must STILL skip emission (because Python
    routes the record up the propagation chain to the parent handler).
    """
    target = corpus_bootstrap.logger
    target.handlers.clear()
    target.propagate = True
    parent = logging.getLogger()
    saved_root_handlers = list(parent.handlers)
    parent.handlers.clear()
    closed_buf = io.StringIO()
    parent.addHandler(logging.StreamHandler(closed_buf))
    closed_buf.close()
    try:
        corpus_bootstrap._safe_log_info("Round 62 test: %s", "parent-closed")
        captured = capsys.readouterr()
        assert "Logging error" not in captured.err
        assert "I/O operation on closed file" not in captured.err
    finally:
        parent.handlers.clear()
        for h in saved_root_handlers:
            parent.addHandler(h)


# ---------------------------------------------------------------------------
# Real call site coverage: drive the daily-refresh exit log path with a
# closed stream and confirm zero stderr noise.  R61 already pinned the
# specific exit-log call; R62 broadens to the index-pass per-source log
# which is the most common path operators trip on (every refresh emits
# 1-3 of these).
# ---------------------------------------------------------------------------


def test_index_pass_per_source_log_silenced_with_closed_stream(isolated_logger, capsys):
    """The per-source line at corpus_bootstrap.py:1380 used to be a raw
    ``logger.info()`` call; R62 / A1 routed it through ``_safe_log_info``.
    Drive the helper with the same shape arguments that the live call
    site uses and assert no traceback leaks to stderr when the stream
    is closed."""
    buf = io.StringIO()
    _attach_stream_handler_to_module_logger(buf)
    buf.close()
    corpus_bootstrap._safe_log_info(
        "Round 17 / corpus_bootstrap: corpus source=%s dir=%s "
        "files_seen=%d files_parsed=%d files_skipped=%d files_failed=%d "
        "chunks_added=%d",
        "onedrive",
        "/Users/jestory/Library/CloudStorage/OneDrive-Cisco/AI Projects/AdoptIQ_CSOne_Reports",
        253, 0, 253, 0, 0,
    )
    captured = capsys.readouterr()
    assert "Logging error" not in captured.err
    assert "I/O operation on closed file" not in captured.err
    assert "ValueError" not in captured.err


def test_index_pass_per_source_log_emits_with_open_stream(isolated_logger):
    buf = io.StringIO()
    _attach_stream_handler_to_module_logger(buf)
    corpus_bootstrap._safe_log_info(
        "Round 17 / corpus_bootstrap: corpus source=%s dir=%s "
        "files_seen=%d files_parsed=%d files_skipped=%d files_failed=%d "
        "chunks_added=%d",
        "onedrive", "/tmp", 1, 1, 0, 0, 1,
    )
    out = buf.getvalue()
    assert "Round 17 / corpus_bootstrap: corpus source=onedrive" in out
    assert "files_seen=1" in out
    assert "chunks_added=1" in out


# ---------------------------------------------------------------------------
# Regression count: lock down the source-code shape so a future PR that
# reintroduces a raw logger.info call site is caught at test time.
# ---------------------------------------------------------------------------


_LOGGER_INFO_RE = re.compile(r"\blogger\.info\(")


def _read_corpus_bootstrap_source() -> List[str]:
    src_path = Path(corpus_bootstrap.__file__)
    return src_path.read_text(encoding="utf-8").splitlines()


def test_no_bare_logger_info_call_outside_helper_body():
    """The only real ``logger.info(`` call site that may exist in
    ``corpus_bootstrap.py`` is the one inside ``_safe_log_info``'s
    body.  Anything else means a future change skipped the helper and
    re-introduced the closed-stream race.  Comments mentioning
    ``logger.info()`` (the docstring + the multi-line block comment in
    ``_daily_refresh_loop``) are excluded.
    """
    lines = _read_corpus_bootstrap_source()
    bare_calls = []
    in_helper_body = False
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("def _safe_log_info("):
            in_helper_body = True
            continue
        if in_helper_body and line.startswith("def ") and not stripped.startswith("def _safe_log_info("):
            in_helper_body = False
        if stripped.startswith("#"):
            continue
        if not _LOGGER_INFO_RE.search(line):
            continue
        if in_helper_body:
            continue
        bare_calls.append((i, line.strip()))
    assert not bare_calls, (
        "Round 62 / A1 regression: corpus_bootstrap.py has bare logger.info(...) "
        "calls outside the _safe_log_info helper body. Route every logger.info() "
        f"site through _safe_log_info() to preserve the closed-stream defense. "
        f"Offenders: {bare_calls}"
    )


def test_safe_log_info_call_site_count_matches_expected_floor():
    """Pin the expected number of ``_safe_log_info(`` call sites.

    Round 96 retired the baked-corpus install helper, removing its
    install-success log while preserving the no-bare-logger contract.
    If a future round legitimately adds
    another ``logger.info`` call site routed through the helper, bump
    this floor to match.  If the floor drops, a call site was deleted
    or replaced -- cross-check that the deletion was intentional."""
    lines = _read_corpus_bootstrap_source()
    call_re = re.compile(r"_safe_log_info\(")
    call_count = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("def _safe_log_info("):
            continue
        for _ in call_re.finditer(line):
            call_count += 1
    assert call_count >= 9, (
        f"Round 62 / A1 floor adjusted in Round 106: expected >= 9 _safe_log_info(...) call sites, found {call_count}. "
        "If a call site was intentionally removed, verify the original logger.info path is also "
        "gone (not reverted to a bare logger.info call), then update this floor."
    )
