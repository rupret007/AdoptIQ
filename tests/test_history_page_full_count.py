"""Round 2 / Phase 1.13 regression test.

The /history page tile must reflect the full ``COUNT(*)`` of the
``report_history`` table, not the LIMIT-50 page length used by the
list itself.  ``get_report_history`` now stamps ``_total_analyses``
on each returned row, and the ``history`` view must read that value
to compute the headline.

Both surfaces are pinned:
  1. ``get_report_history`` returns rows that include ``_total_analyses``.
  2. ``history`` view computes ``total_analyses`` from that field
     rather than ``len(raw)``.
"""
from __future__ import annotations

import inspect
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_history_view_reads_total_analyses_field() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Find the history() view body and confirm it reads
    # _total_analyses rather than computing len(raw).
    view = re.search(
        r"def\s+history\s*\(\s*\)\s*:[^\n]*\n(?:[^\n]*\n){0,40}",
        src,
    )
    assert view is not None, "history() view body must exist in app_simple.py"
    body = view.group(0)
    assert "_total_analyses" in body, (
        "Round 2 Phase 1.13: /history view must read the "
        "_total_analyses field stamped by get_report_history so the "
        "tile shows the full COUNT(*), not the page-length 50."
    )


def test_get_report_history_stamps_total_analyses() -> None:
    """``get_report_history`` (or its enhanced_admin_dashboard_v2
    equivalent) must stamp ``_total_analyses`` on each row so the
    history view can read the COUNT(*) without re-querying.
    """
    try:
        import enhanced_admin_dashboard_v2 as ead
    except Exception as exc:  # pragma: no cover - import failure surfaces clearly
        import pytest
        pytest.skip(f"enhanced_admin_dashboard_v2 not importable: {exc}")
        return
    if not hasattr(ead, "get_report_history"):
        import pytest
        pytest.skip("get_report_history not present on this build")
        return
    src = inspect.getsource(ead.get_report_history)
    assert "_total_analyses" in src, (
        "Round 2 Phase 1.13: get_report_history must stamp "
        "_total_analyses on each returned row so /history can show "
        "the full COUNT(*) instead of the page length."
    )
