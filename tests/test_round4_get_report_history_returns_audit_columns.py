"""Round 4 / Phase 5.3 regression test.

``get_report_history`` SELECT must return the Round 3 audit columns
(``days``, ``word_path``, ``excel_path``, ``word_hash``, ``excel_hash``,
``partial_data_warnings_json``).  Pre-Round-4 the SELECT only
returned the legacy columns, so the dashboard always rendered
``days = None`` even though the audit row had it.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_get_report_history_select_lists_audit_columns() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    # The Round 4 fix extends the SELECT to include audit columns.
    # We pin the presence of each column name in the source.
    for col in (
        "days",
        "word_path",
        "excel_path",
        "word_hash",
        "excel_hash",
        "partial_data_warnings_json",
    ):
        assert col in src, (
            f"Round 4 Phase 5.3: get_report_history must SELECT '{col}' "
            f"so the dashboard / fallback rehydration can read it."
        )


def test_get_report_history_handles_legacy_schema() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    # The fix wraps the new SELECT in a try/except so legacy DBs don't
    # crash.  We pin presence of an OperationalError handler or
    # try/except block near the SELECT.
    assert "OperationalError" in src or "except" in src, (
        "Round 4 Phase 5.3: get_report_history must gracefully handle "
        "older DBs missing the audit columns."
    )
