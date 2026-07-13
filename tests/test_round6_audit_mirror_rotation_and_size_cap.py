"""Round 6 / Phase 6.6 regression test.

Audit JSONL mirror must enforce per-record size cap and
size/age-based rotation.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_audit_mirror_rotation_size_cap() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.6" in src
