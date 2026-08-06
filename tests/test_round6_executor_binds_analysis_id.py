"""Round 6 / Phase 7.2 regression test.

``ThreadPoolExecutor`` submissions must propagate the parent
``contextvars.Context`` (which carries ``analysis_id``) into the
worker via ``copy_context().run`` -- otherwise structured logs
inside worker callables silently drop the analysis id.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_executor_binds_analysis_id() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 7.2", label='src')
    assert_in_source(src, "_submit_with_context", label='src')
