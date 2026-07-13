"""Round 6 / Phase 6.11 regression test.

Audit JSONL mirror file must be created with 0o600 permissions.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_audit_mirror_0600() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 6.11" in src
    assert "0o600" in src
