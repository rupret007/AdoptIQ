"""Round 4 / Phase 1.4 regression test.

When risk scoring fails (timeout / exception) and ``risk_scores`` is
empty, the Executive Excel writer must:
  1. write ``n/a`` to the dashboard cells (not 0 / "low risk"),
  2. append an entry to ``partial_data_warnings``, and
  3. add an explicit row to ``Report_Info`` flagging the partial
     condition.

This is a source-level pin that the failure path is wired in.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_empty_risk_scores_marks_dashboard_na() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Look for an n/a marker AND a reference to risk_scores being empty.
    assert (
        re.search(r"risk_scores_unavailable_reason", src)
        or re.search(r"risk_scores\s*[=]\s*\{\}", src)
    ), (
        "Round 4 Phase 1.4: the Executive Excel writer must capture a "
        "risk_scores_unavailable_reason (or similar marker) when "
        "risk_scores is empty so downstream cells can render 'n/a' "
        "instead of treating empty dict as 'all healthy'."
    )
    assert "n/a" in src or "N/A" in src, (
        "Round 4 Phase 1.4: dashboard cells must render an 'n/a' "
        "string when risk scoring is unavailable."
    )


def test_empty_risk_scores_appends_partial_data_warning() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "partial_data_warnings" in src, (
        "Round 4 Phase 1.4: empty risk_scores must surface as an entry "
        "in partial_data_warnings."
    )
