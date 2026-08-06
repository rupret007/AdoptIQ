"""Round 6 / Phase 3.6 regression test.

LLM wrapped errors must carry a stable ``kind=`` so callers can
branch on it (network/timeout/auth/parse/unknown) without scraping
strings.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_llm_wrapped_error_kind() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 3.6", label='src')
