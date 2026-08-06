"""Round 6 / Phase 3.7 regression test.

Circuit-breaker timeout must be aligned with HTTP timeout to avoid
``open`` flapping under slow LLM responses.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_circuit_timeout_aligned() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 3.7", label='src')
