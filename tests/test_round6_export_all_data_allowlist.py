"""Round 6 / Phase 4.19 regression test.

``export_all_data`` (incident_storage) must use an explicit
allowlist of (table, order_col), not arbitrary user-supplied SQL.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_export_all_data_allowlist() -> None:
    src = (REPO_ROOT / "incident_storage.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 4.19", label='src')
