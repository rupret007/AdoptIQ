"""Round 63 / Tier A: parity regression-count test for ``corpus_indexer``.

R62 / A1 added the ``_safe_log_info`` helper to BOTH
``corpus_bootstrap.py`` AND ``corpus_indexer.py`` because the latter
could not import the former (circular).  The R62 test file
``tests/test_round62_corpus_logger_module_wide.py`` only pinned the
``bare logger.info`` floor in ``corpus_bootstrap.py``, leaving
``corpus_indexer.py`` exposed: a future PR could quietly add a raw
``logger.info(...)`` call there and silently re-introduce the
closed-stream race.

R63 / Tier A closes that gap by promoting the helper implementation
to ``_logging_helpers`` (so neither module has to carry the body) and
mirroring the regression-count guard onto ``corpus_indexer.py``.

Two contracts pinned:
1. ZERO bare ``logger.info(`` call sites in ``corpus_indexer.py``
   outside the wrapper body.
2. The R62 / A1 floor of 5 ``_safe_log_info(...)`` call sites in
   ``corpus_indexer.py`` is preserved (so a regression that deletes
   the migrations and reverts to bare ``logger.info`` calls is
   caught two ways).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import corpus_indexer


_LOGGER_INFO_RE = re.compile(r"\blogger\.info\(")
_SAFE_LOG_INFO_RE = re.compile(r"_safe_log_info\(")


def _read_corpus_indexer_source() -> List[str]:
    src_path = Path(corpus_indexer.__file__)
    return src_path.read_text(encoding="utf-8").splitlines()


def test_no_bare_logger_info_call_outside_helper_body_in_corpus_indexer():
    """Mirror of
    ``tests/test_round62_corpus_logger_module_wide.py::test_no_bare_logger_info_call_outside_helper_body``
    but applied to ``corpus_indexer.py``.

    The only legitimate ``logger.info(`` call site is inside the
    ``_safe_log_info`` wrapper body itself.  Anything else means a
    future change skipped the helper and re-introduced the closed-
    stream race.  Comment lines are excluded so docstrings + block
    comments mentioning ``logger.info()`` for explanatory purposes
    do not trip this guard.
    """
    lines = _read_corpus_indexer_source()
    bare_calls: list[tuple[int, str]] = []
    in_helper_body = False
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("def _safe_log_info("):
            in_helper_body = True
            continue
        if in_helper_body and line.startswith("def ") and not stripped.startswith(
            "def _safe_log_info("
        ):
            in_helper_body = False
        if stripped.startswith("#"):
            continue
        if not _LOGGER_INFO_RE.search(line):
            continue
        if in_helper_body:
            continue
        bare_calls.append((i, line.strip()))
    assert not bare_calls, (
        "Round 63 / Tier A regression: corpus_indexer.py has bare logger.info(...) "
        "calls outside the _safe_log_info helper body.  Route every logger.info() "
        "site through _safe_log_info() to preserve the closed-stream defense.  "
        f"Offenders: {bare_calls}"
    )


def test_safe_log_info_call_site_count_matches_expected_floor_in_corpus_indexer():
    """Pin the R62 / A1 floor of 5 ``_safe_log_info(`` call sites in
    ``corpus_indexer.py``.  If a future round legitimately adds another
    ``logger.info`` call site routed through the helper, bump this
    floor to match.  If the floor drops, a call site was deleted or
    silently reverted to a bare ``logger.info`` -- cross-check that
    the deletion was intentional."""
    lines = _read_corpus_indexer_source()
    call_count = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("def _safe_log_info("):
            continue
        for _ in _SAFE_LOG_INFO_RE.finditer(line):
            call_count += 1
    assert call_count >= 5, (
        f"Round 63 / Tier A floor: expected >= 5 _safe_log_info(...) call sites in "
        f"corpus_indexer.py, found {call_count}.  If a call site was intentionally "
        "removed, verify the original logger.info path is also gone (not reverted to "
        "a bare logger.info call), then update this floor."
    )
