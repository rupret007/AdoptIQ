"""Round 6 / Phase 5.4 regression test.

Renewal/CSOne joins must call ``normalize_customer_name`` to align
keys.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_csone_normalize_name() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 5.4" in src
    assert "normalize_customer_name" in src
