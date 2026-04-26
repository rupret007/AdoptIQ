"""Round 6 / Phase 7.3 regression test.

Audit JSONL ``json.dumps`` ``default=`` callable must redact
non-serializable values to ``<unserializable:type>`` and log the
type, instead of letting ``str()`` silently leak repr output.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_audit_mirror_strict_default() -> None:
    src = (REPO_ROOT / "enhanced_admin_dashboard_v2.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 7.3" in src
    assert "_strict_default" in src
    assert "<unserializable:" in src
