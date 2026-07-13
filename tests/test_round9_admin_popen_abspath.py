"""Round 9 / Phase 4.1: admin dashboard Popen for app_simple.py uses absolute path."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_admin_popen_abspath() -> None:
    src = REPO_ROOT.joinpath('enhanced_admin_dashboard_v2.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 4.1' in src, (
        'Round 9 / Phase 4.1 marker missing in enhanced_admin_dashboard_v2.py'
    )
    # The new code must resolve the script path from __file__ rather than CWD.
    assert '_app_simple_path' in src, (
        'enhanced_admin_dashboard_v2.py: _app_simple_path helper missing'
    )
    assert "Path(__file__).resolve().parent" in src or "_Path(__file__).resolve().parent" in src
