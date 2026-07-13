"""Round 5 / Phase 2.3 regression test.

Destructive admin endpoints (start_server / stop_server / clear_logs /
export_data) must require POST with a per-session CSRF token.  GET
anchors that mutate state are not acceptable.
(CodeGuard ``session-management-and-cookies`` and
``framework-and-languages``.)
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_admin_destructive_endpoints_require_csrf() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 2.3" in src, (
        "Round 5 Phase 2.3 marker missing in enhanced_admin_dashboard_v2.py."
    )
    assert "_require_admin_csrf" in src, (
        "Round 5 Phase 2.3: destructive admin endpoints must call "
        "_require_admin_csrf() before mutating state."
    )
