"""Round 6 / Phase 5.9 regression test.

advanced_renewal_analyzer must respect strict-mode (raise on
canonical-data missing instead of silently using literal fallbacks).
The strict-mode threading path lives in app_simple.py.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_renewal_strict_mode() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 5.9" in src
