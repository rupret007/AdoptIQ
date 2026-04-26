"""Round 4 / Phase 2.4 regression test.

The ``history.html`` headline tiles ("Last 7 Days", "Active Managers")
must read from server-provided totals (``total_analyses``,
``total_last_7_days``, ``total_managers``) rather than slicing the
current page of analyses (``analyses[:7]|length``), which is not a
portfolio metric.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_history_template_uses_server_provided_totals() -> None:
    html = (REPO_ROOT / "templates" / "history.html").read_text(encoding="utf-8")
    # At minimum, total_analyses or total_last_7_days or total_managers
    # must be referenced.  Round 4 introduces all three.
    refs_total_a = "total_analyses" in html
    refs_total_7d = ("total_last_7_days" in html) or ("reports_last_7_days" in html)
    refs_total_mgr = ("total_managers" in html) or ("active_managers" in html)
    assert refs_total_a and refs_total_7d and refs_total_mgr, (
        "Round 4 Phase 2.4: history.html must reference server-provided "
        "totals (total_analyses / total_last_7_days / total_managers) "
        "instead of slicing the current page."
    )


def test_history_route_passes_totals_to_template() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The history() route must compute and pass these values so they
    # are not undefined in the template.
    assert "total_analyses" in src or "total_last_7_days" in src or "total_managers" in src, (
        "Round 4 Phase 2.4: app_simple.history() must pass at least "
        "one of total_analyses / total_last_7_days / total_managers "
        "to render_template so the headline tiles are not undefined."
    )
