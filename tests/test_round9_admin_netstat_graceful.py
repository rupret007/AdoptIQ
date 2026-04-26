"""Round 9 / Phase 4.2: admin netstat/lsof shell-out handles missing binaries gracefully."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_admin_netstat_graceful() -> None:
    src = REPO_ROOT.joinpath('enhanced_admin_dashboard_v2.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 4.2' in src, (
        'Round 9 / Phase 4.2 marker missing in enhanced_admin_dashboard_v2.py'
    )
    assert 'FileNotFoundError' in src, (
        'enhanced_admin_dashboard_v2.py: FileNotFoundError guard missing'
    )
