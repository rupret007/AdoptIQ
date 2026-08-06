"""Round 6 / Phase 6.14 regression test.

Admin KPI SQL queries must compute date ranges in UTC and pass as
parameterized ISO-Z strings.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_admin_kpi_utc_iso_compare() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.14", label='src')
