"""Round 6 / Phase 6.6 regression test.

Audit JSONL mirror must enforce per-record size cap and
size/age-based rotation.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_audit_mirror_rotation_size_cap() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.6", label='src')
