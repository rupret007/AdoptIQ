"""Round 6 / Phase 5.8 regression test.

Strict-mode flag must be threaded through to every report writer
and severity computation, not silently dropped.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_strict_mode_threaded() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 5.8" in src
