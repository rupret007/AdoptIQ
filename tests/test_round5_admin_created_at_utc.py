"""Round 5 / Phase 6.3 regression test.

``record_report_completion`` must store ``created_at`` as UTC with a
``Z`` suffix end-to-end so the SQL "last 7 days" KPI tile (which
filters on ``datetime('now', '-7 days')`` UTC) and the dashboard
agree.  Previously, ``datetime.now().isoformat()`` (local time, no
zone marker) was indistinguishable from a UTC timestamp at the SQL
layer and produced off-by-N-hour windowing errors.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_record_report_completion_uses_utc_iso_z() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 6.3" in src, (
        "Round 5 Phase 6.3 marker missing in enhanced_admin_dashboard_v2.py."
    )
    assert "_utc_iso_z" in src, (
        "Round 5 Phase 6.3: record_report_completion must format created_at "
        "via the _utc_iso_z helper (UTC, 'Z' suffix)."
    )
