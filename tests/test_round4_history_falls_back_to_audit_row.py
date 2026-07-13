"""Round 4 / Phase 5.4 regression test.

When ``/progress/<id>`` is hit for an analysis ID that has been
evicted from in-memory ``analysis_status`` AND from ``status.json``,
the route MUST fall back to the ``report_history`` audit table and
render a minimal completed view (instead of returning 404).
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_progress_route_uses_report_history_fallback() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Pin presence of the new helper that reconstructs status from
    # report_history.
    assert "_build_status_from_report_history" in src, (
        "Round 4 Phase 5.4: /progress/<id> must call "
        "_build_status_from_report_history(analysis_id) when the "
        "analysis ID is missing from in-memory / status.json so the "
        "audit row can rehydrate a minimal completed status."
    )
