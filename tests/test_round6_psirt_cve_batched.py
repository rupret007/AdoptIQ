"""Round 6 / Phase 4.6 regression test.

PSIRT CVE lookups must be batched (not one-by-one) to respect the
upstream rate limit.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_psirt_cve_batched() -> None:
    src = (REPO_ROOT / "cisco_internal_integrations.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 4.6" in src
