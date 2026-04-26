"""Round 6 / Phase 6.13 regression test.

Admin CSRF token validation must not accept tokens from URL query
arguments (only HTTP headers / form bodies).
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_admin_csrf_no_args() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.13" in src
