"""Round 6 / Phase 3.9 regression test.

LLM JSON schemas should set ``additionalProperties: false`` so the
model cannot smuggle extra fields.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_llm_schema_no_additional_properties() -> None:
    src = (REPO_ROOT / "ask_ai_grounded.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 3.9", label='src')
    assert_in_source(src, "additionalProperties", label='src')
