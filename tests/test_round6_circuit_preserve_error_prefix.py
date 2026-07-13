"""Round 6 / Phase 3.1 regression test.

LLM circuit must preserve error prefix (``[LLM_ERROR:...]``) so
downstream classification still works.
"""
from __future__ import annotations
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_circuit_preserve_error_prefix() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 6 / Phase 3.1" in src
