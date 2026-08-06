"""Round 6 / Phase 6.15 regression test.

Connectivity diagnostics must namespace internal error ``kind`` so
the public API exposes a stable taxonomy.
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_connectivity_diag_namespace() -> None:
    src = (REPO_ROOT / "connectivity_diagnostics.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.15", label='src')
    assert_in_source(src, "_namespace_check_kind", label='src')
