"""Round 15 / Phase 6 -- reliability adversarial regression tests.

Each test pins a specific Round-15 reliability hardening so a future
refactor can't quietly regress the protection.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import report_export_styling as styling


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 6.1 -- call-signature-mismatch errors must be visible at WARNING.
# ---------------------------------------------------------------------------


def test_phase_6_1_safe_canonical_call_logs_typeerror_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 15 / Phase 6.1 -- a positional call against a keyword-only
    canonical-metrics signature must surface as a WARNING-level log
    line so the next R15-015-class regression is visible to the
    operator instead of being silently swallowed at DEBUG.
    """

    def keyword_only(*, ab_df=None, csone_df=None) -> int:
        return 0

    with caplog.at_level(logging.DEBUG, logger=styling.logger.name):
        result = styling._safe_canonical_call(keyword_only, "df_a", "df_b")

    assert result is None
    matched = [
        r for r in caplog.records if r.levelname == "WARNING" and "Phase 6.1" in r.getMessage()
    ]
    assert matched, (
        f"Round 15 / Phase 6.1 regression: TypeError from a call-signature "
        f"mismatch must surface at WARNING.  Captured records: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    msg = matched[0].getMessage()
    assert "keyword_only" in msg
    assert "str" in msg  # both args reported as ``str`` type names


def test_phase_6_1_safe_canonical_call_keeps_other_errors_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-TypeError exceptions remain at DEBUG (e.g. an empty frame
    that yields ``ValueError`` from ``.unique()``).  They must NOT
    spam WARNING when canonical_metrics returns ``None`` for empty
    inputs is the documented contract.
    """

    def raises_value_error(*args, **kwargs):
        raise ValueError("empty frame")

    with caplog.at_level(logging.DEBUG, logger=styling.logger.name):
        result = styling._safe_canonical_call(raises_value_error)

    assert result is None
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not warning_records, (
        f"ValueError leaked to WARNING: {[r.getMessage() for r in warning_records]}"
    )


def test_phase_6_1_safe_canonical_call_returns_value_on_success():
    """Happy path: a successful call returns the upstream value
    untouched."""

    def ok(*, n: int) -> int:
        return n + 1

    assert styling._safe_canonical_call(ok, n=41) == 42


def test_phase_6_1_marker_in_report_export_styling():
    contents = _read("report_export_styling.py")
    assert "Round 15 / Phase 6.1" in contents


# ---------------------------------------------------------------------------
# Phase 6.2 -- ``analysis_status_lock`` is released across SQLite IO in the
# audit-fallback path of ``progress_view``.
# ---------------------------------------------------------------------------


def test_phase_6_2_progress_view_audit_fallback_releases_lock_around_sqlite():
    """Round 15 / Phase 6.2 -- the audit-fallback branch of
    ``progress_view`` (status-file missing path) must NOT call
    ``_build_status_from_report_history`` while holding
    ``analysis_status_lock``.  The previous shape ran the SQLite
    query under the lock, so any DB latency would block every other
    request thread that wanted to read or write the in-memory
    status dict.

    We pin this with a static-source assertion: the body of
    ``progress_view`` between the ``"Status file not found"`` log
    and the 404 fallthrough must mirror the sibling branch above
    -- i.e. it must contain a ``_need_audit_lookup`` flag pattern
    so the SQLite call lives outside the ``with`` block.
    """

    src = _read("app_simple.py")

    # The progress_view variant of the "status file not found" log
    # message is logged at WARNING and includes the ``Current
    # directory`` context, distinguishing it from the first-run
    # INFO variant elsewhere in the module.
    file_missing_marker = (
        'logger.warning(f"Status file not found at: '
        '{status_file_path}. Current directory:'
    )
    audit_call = "_build_status_from_report_history(analysis_id)"
    phase_marker = "Round 15 / Phase 6.2"
    lock_open = "with analysis_status_lock:"

    assert file_missing_marker in src, (
        "Round 15 / Phase 6.2 regression: progress_view's status-file-"
        "missing log line was renamed.  Re-anchor this test."
    )
    start = src.index(file_missing_marker)
    # Round 29 / L1: the original anchor here was the inline-HTML
    # ``Analysis not found</h1><p>Analysis ID:`` f-string body that
    # ``progress()`` returned at the 404 fallthrough.  Round 29
    # replaced that with ``abort(404)``, so we re-anchor to the
    # ``except Exception`` line that immediately follows the audit-
    # fallback branch -- it has been the structural close of the
    # status-file-missing branch since Round 15 landed and is much
    # less likely to drift than the f-string body was.
    end = src.index("except Exception as e:", start)
    region = src[start:end]

    assert phase_marker in region, (
        "Round 15 / Phase 6.2 marker missing from the file-missing "
        "audit-fallback region of progress_view."
    )
    assert "_need_audit_lookup" in region, (
        "Round 15 / Phase 6.2 regression: the lock-release flag "
        "pattern (``_need_audit_lookup``) was removed from "
        "progress_view's audit-fallback branch."
    )

    # Verify the SQLite call is NOT nested inside the first
    # ``with analysis_status_lock:`` body.  The first lock block in
    # this region opens immediately after the WARNING log and must
    # close (i.e. dedent) before the audit call.
    first_lock = region.index(lock_open)
    audit_idx = region.index(audit_call)
    assert audit_idx > first_lock, (
        "Round 15 / Phase 6.2 regression: SQLite call appears before "
        "the lock block opens; expected the lock to be acquired only "
        "for the in-memory check."
    )

    # Inspect the parent block of the audit call by walking backwards
    # from the call line until we hit a line whose indent is strictly
    # less than the call's indent.  That line is the parent.  The
    # parent must NOT be ``with analysis_status_lock:``; if it is,
    # the SQLite IO is nested inside the lock body again.
    lines = region.splitlines()

    audit_lineno = next(
        i for i, line in enumerate(lines) if audit_call in line
    )
    audit_indent = len(lines[audit_lineno]) - len(
        lines[audit_lineno].lstrip(" ")
    )

    parent_line = None
    for line in reversed(lines[:audit_lineno]):
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent < audit_indent:
            parent_line = stripped
            break

    assert parent_line is not None, (
        "Round 15 / Phase 6.2 regression: could not identify the "
        "parent block of the SQLite call; the call may be at module "
        "scope."
    )
    assert not parent_line.startswith("with analysis_status_lock"), (
        "Round 15 / Phase 6.2 regression: the SQLite call's "
        f"immediate parent is `{parent_line.rstrip(':')}`, meaning "
        "the lock is held across IO again."
    )


def test_phase_6_2_marker_in_app_simple():
    contents = _read("app_simple.py")
    assert "Round 15 / Phase 6.2" in contents
