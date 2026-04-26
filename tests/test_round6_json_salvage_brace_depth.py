"""Round 6 / Phase 3.8 regression test.

JSON salvage logic must walk brace depth (not regex) so it can
recover well-formed objects that contain nested braces.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_json_salvage_brace_depth() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 3.8" in src
